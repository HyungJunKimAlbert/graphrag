"""Local 검색. 질문과 관련된 개체(시드)를 찾고, 그 개체에 이어진 관계와 문서를 모아 문맥을 만든다.

순서
  1. 시드 찾기   : 먼저 질문에 적힌 이름(개체 이름·별칭이 글자 그대로 나온 것)과 질문 분석이 뽑은 언급을 시드로 삼는다.
                   남는 자리는 개체 벡터 상위 10건, 이름 전문검색 상위 10건, 문서 벡터 상위 10건의 ABOUT 대상을
                   순위 역수 합(k=60)으로 합쳐 채운다. 시드는 최대 8개.
  2. 이웃 확장   : 시드마다 ABOUT·MENTIONS를 뺀 1홉 관계를 관계 유형당 5건까지 (이웃과 질문의 코사인 유사도 순).
  3. 시드 잇기   : 시드 쌍 사이에 길이 2 이하 경로가 있으면 그 관계도 더한다.
  4. 문서        : 문맥에 든 성분마다 기능·주의 문서를 먼저, 유사도 순으로 4건까지.
  5. 예산        : 6,000토큰을 넘으면 시드 순위가 낮은 쪽의 문서·사실부터 뺀다.

Context = {seeds, facts, docs, ingredient_ids, ingredient_names, max_seed_score}
  seeds[].score는 질문과 개체 설명의 코사인 유사도다(답 없음 판정 문턱값의 재료). 시드 순서는 순위 역수 합 순이다.
  사실 ID는 "F-" + sha1("시작 키|관계 유형|끝 키")[:10]. 문서 ID는 doc_id 그대로다.
"""
import hashlib
import re

from .. import llm

TOP_K, SEEDS, RRF_K = 10, 8, 60
MENTION_K, MIN_NAME_LEN = 3, 2
NEIGHBORS_PER_TYPE, PATHS_PER_PAIR, DOCS_PER_INGREDIENT = 5, 10, 4
TOKEN_BUDGET = 6000
SKIP_TYPES = ["ABOUT", "MENTIONS"]
FIRST_DOC_KINDS = ["function", "precaution"]
LUCENE_SPECIALS = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/·,.;、！？（）]')   # 가운뎃점을 안 지우면 "와파린·메트포르민"이 한 낱말이 된다
REL_PROPS = "{reason: r.reason, severity: r.severity, note: r.note, text: r.text, evidence: r.evidence, rationale: r.rationale, scope: r.scope}"
NAME = "coalesce({n}.name, {n}.label)"

Q_SEED_VECTOR = """CALL db.index.vector.queryNodes('rag_entity_embedding', $k, $vec) YIELD node, score
RETURN node.rag_key, score ORDER BY score DESC"""
Q_SEED_NAMES = """CALL db.index.fulltext.queryNodes('rag_entity_names', $q, {limit: $k}) YIELD node, score
RETURN node.rag_key, score ORDER BY score DESC"""
Q_SEED_DOCS = """CALL db.index.vector.queryNodes('doc_embedding', $k, $vec) YIELD node, score
MATCH (node)-[:ABOUT]->(i:Ingredient) RETURN i.rag_key, score ORDER BY score DESC"""
Q_MENTION_VECTOR = """CALL db.index.vector.queryNodes('rag_entity_embedding', $k, $vec) YIELD node, score
RETURN node.rag_key, score ORDER BY score DESC"""
Q_NAME_INDEX = "MATCH (n:RagEntity) RETURN n.rag_key, n.rag_names"
Q_LIFT_ITEMS = """UNWIND $keys AS key MATCH (d:DrugItem {rag_key: key})-[:IN_CLASS]->(g:Drug) RETURN key, g.rag_key"""
Q_SEED_INFO = f"""UNWIND $keys AS key MATCH (n:RagEntity {{rag_key: key}})
OPTIONAL MATCH (n)-[r]-() WHERE NOT type(r) IN $skip
RETURN key, {NAME.format(n='n')}, n.rag_kind, vector.similarity.cosine(n.rag_embedding, $vec), count(r)"""
Q_NEIGHBORS = f"""MATCH (s:RagEntity {{rag_key: $key}})-[r]-(n:RagEntity)
WHERE NOT type(r) IN $skip
WITH r, type(r) AS t, vector.similarity.cosine(n.rag_embedding, $vec) AS sim
ORDER BY sim DESC, startNode(r).rag_key, endNode(r).rag_key
WITH t, collect({{start: startNode(r).rag_key, end: endNode(r).rag_key, a: {NAME.format(n='startNode(r)')},
                 b: {NAME.format(n='endNode(r)')}, props: {REL_PROPS}, sim: sim}})[0..$cap] AS top
UNWIND top AS f RETURN t, f.start, f.end, f.a, f.b, f.props, f.sim"""
Q_PAIR_PATHS = f"""UNWIND $pairs AS pair
MATCH (a:RagEntity {{rag_key: pair[0]}}), (b:RagEntity {{rag_key: pair[1]}})
MATCH p = (a)-[*1..2]-(b)
WHERE all(r IN relationships(p) WHERE NOT type(r) IN $skip)
WITH pair, collect(p)[0..$cap] AS paths
UNWIND paths AS p UNWIND relationships(p) AS r
RETURN DISTINCT pair, type(r), startNode(r).rag_key, endNode(r).rag_key, {NAME.format(n='startNode(r)')},
       {NAME.format(n='endNode(r)')}, {REL_PROPS}"""
Q_DOCS = """UNWIND $ids AS iid
MATCH (d:Doc)-[:ABOUT]->(i:Ingredient {ingredient_id: iid}) WHERE d.embedding IS NOT NULL
WITH iid, d, CASE WHEN d.kind IN $first THEN 0 ELSE 1 END AS tier, vector.similarity.cosine(d.embedding, $vec) AS sim
ORDER BY tier, sim DESC, d.doc_id
WITH iid, collect({id: d.doc_id, kind: d.kind, text: d.text, tier: tier, sim: sim})[0..$cap] AS docs
UNWIND docs AS doc RETURN iid, doc.id, doc.kind, doc.text, doc.tier, doc.sim"""

# 관계 유형별 사실 문장 틀. a = 시작 노드 이름, b = 끝 노드 이름, p = 관계 속성
SEVERITY = {"avoid": "피해야 한다", "caution": "섭취에 주의가 필요하다", "consult": "전문가와 상담해야 한다"}
TEMPLATES = {
    "DEPLETES": lambda a, b, p: f"약 '{a}'은(는) 원료 '{b}'을(를) 고갈시킬 수 있다.",
    "EXCLUDES": lambda a, b, p: (f"'{a}' 조건에서는 원료 '{b}'의 제품 성분·제형 확인이 필요하다. 사유: {p.get('reason') or a}"
                                 if p.get("scope") == "product" else
                                 f"'{a}' 조건에서는 원료 '{b}'을(를) {SEVERITY.get(p.get('severity'), SEVERITY['avoid'])}. "
                                 f"사유: {p.get('reason') or a}"),
    "INTERACTS_WITH": lambda a, b, p: (f"원료 '{a}'은(는) 약 '{b}'과(와) 상호작용한다"
                                       f" ({'함께 섭취를 피할 것' if p.get('severity') == 'avoid' else '전문가와 상담할 것'})."
                                       + (f" 주의 문구: {p['note']}" if p.get("note") else "")),
    "HAS_FUNCTION": lambda a, b, p: f"원료 '{a}'의 기능성: {p.get('text') or b} (분류: {b}).",
    "MAY_CAUSE": lambda a, b, p: f"약 '{a}'은(는) 증상 '{b}'을(를) 일으킬 수 있다.",
    "RECOMMENDS": lambda a, b, p: f"'{a}'에는 원료 '{b}'을(를) 권한다." + (f" 이유: {p['reason']}" if p.get("reason") else ""),
    "RELIEVED_BY": lambda a, b, p: f"증상 '{a}'에는 원료 '{b}'이(가) 연결돼 있다.",
    "IN_CLASS": lambda a, b, p: f"약 표기 '{a}'은(는) '{b}' 계열에 속한다.",
    "INCLUDES": lambda a, b, p: f"증상 분류 '{a}'에 증상 '{b}'이(가) 속한다.",
    "RELATED_TO": lambda a, b, p: f"증상 '{a}'과(와) 증상 '{b}'은(는) 관련 증상이다.",
    "HAS_INTEREST": lambda a, b, p: f"'{a}' 구분의 주요 관심사는 '{b}'이다.",
    "HOLDS_RECOGNITION": lambda a, b, p: f"'{a}'이(가) 원료 '{b}'의 개별인정을 보유한다.",
    "ADDRESSED_BY": lambda a, b, p: f"증상 분류 '{a}'은(는) 기능성 분류 '{b}'(으)로 대응한다." + (f" 근거: {p['rationale']}" if p.get("rationale") else ""),
    "EXPLAINED_BY": lambda a, b, p: f"증상 '{a}'의 기전은 '{b}'이다.",
    "SUPPORTED_BY": lambda a, b, p: f"기전 '{a}'은(는) 기능성 분류 '{b}'와 관련된다.",
}
HYPOTHESIS_TAG = " [근거 수준: 가설, 검토 전]"


def fact_id(start: str, rel_type: str, end: str) -> str:
    return "F-" + hashlib.sha1(f"{start}|{rel_type}|{end}".encode("utf-8")).hexdigest()[:10]


def fact_text(rel_type: str, a: str, b: str, props: dict) -> str:
    props = {k: v for k, v in (props or {}).items() if v is not None}
    template = TEMPLATES.get(rel_type, lambda a, b, p: f"'{a}' -{rel_type}-> '{b}'.")
    return template(a, b, props) + (HYPOTHESIS_TAG if props.get("evidence") == "hypothesis" else "")


def make_fact(rel_type, start, end, a, b, props) -> dict:
    # _a, _b(양 끝 이름)는 성분 ID→이름 표를 만들 때만 쓰고 문맥에는 남기지 않는다
    return {"id": fact_id(start, rel_type, end), "text": fact_text(rel_type, a, b, props), "start": start,
            "type": rel_type, "end": end, "sources": [], "_a": a, "_b": b}


def lucene_query(question: str) -> str:
    """전문검색용 질의. 루씬 특수문자를 지우고 낱말을 OR로 잇는다(분석기가 다시 토큰으로 나눈다)."""
    words = LUCENE_SPECIALS.sub(" ", question).split()
    return " ".join(words) or "*"


def fuse(lists: list[list[str]], k: int = RRF_K) -> list[str]:
    """순위 역수 합. 각 목록에서 n등이면 1/(k+n)점을 받고, 합이 큰 키부터 돌려준다."""
    scores: dict[str, float] = {}
    for keys in lists:
        for rank, key in enumerate(dict.fromkeys(keys), start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda key: (-scores[key], key))


_NAME_INDEX: dict[int, dict[str, list[str]]] = {}     # id(graph) → {공백 뺀 이름: [키]}


def compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text)).lower()


def name_index(graph) -> dict[str, list[str]]:
    """개체 이름·별칭(공백 제거, 2자 이상) → 키 목록. 접속마다 한 번만 읽는다."""
    if id(graph) not in _NAME_INDEX:
        index: dict[str, list[str]] = {}
        for key, names in graph.read(Q_NAME_INDEX):
            for name in names or []:
                if len(compact(name)) >= MIN_NAME_LEN:
                    index.setdefault(compact(name), []).append(key)
        _NAME_INDEX[id(graph)] = index
    return _NAME_INDEX[id(graph)]


def literal_mentions(text: str, index: dict[str, list[str]]) -> list[str]:
    """글에 글자 그대로 나온 개체 이름의 키. 긴 이름부터 찾고 찾은 자리는 가려서 '비타민 B12' 속 '비타민 B1'을 세지 않는다."""
    rest, found = compact(text), []
    for name in sorted(index, key=lambda n: (-len(n), n)):
        if name in rest:
            rest = rest.replace(name, "\0" * len(name))
            found += [k for k in index[name] if k not in found]
    return found


def mention_seeds(graph, question: str, embed, mentions: list[str] | None) -> list[str]:
    """질문에 적힌 이름 + 질문 분석이 뽑은 언급. 언급은 이름 전문검색과 개체 벡터로 각각 찾아 합친 1위를 쓴다."""
    index = name_index(graph)
    keys = literal_mentions(question, index)
    phrases = [m for m in dict.fromkeys(m.strip() for m in (mentions or [])) if m]
    if phrases:
        vectors = embed(phrases)
        for phrase, vec in zip(phrases, vectors):
            hits = literal_mentions(phrase, index)
            if not hits:
                by_name = [r[0] for r in graph.read(Q_SEED_NAMES, q=lucene_query(phrase), k=MENTION_K)]
                by_vector = [r[0] for r in graph.read(Q_MENTION_VECTOR, k=MENTION_K, vec=vec)]
                hits = fuse([by_name, by_vector])[:1]
            keys += [k for k in hits if k not in keys]
    return keys


def lift_drug_items(graph, keys: list[str]) -> list[str]:
    """약 표기(DrugItem)는 그 약의 계열(Drug)로 올린다. 표기 노드는 IN_CLASS 관계 하나뿐이라 시드로는 쓸모가 없다."""
    items = [k for k in keys if k.startswith("DrugItem:")]
    lifted = dict(graph.read(Q_LIFT_ITEMS, keys=items)) if items else {}
    return list(dict.fromkeys(lifted.get(k, k) for k in keys))


def find_seeds(graph, question: str, vec: list[float], embed, mentions: list[str] | None = None) -> list[dict]:
    keys = lift_drug_items(graph, mention_seeds(graph, question, embed, mentions))[:SEEDS]
    if len(keys) < SEEDS:
        by_vector = [r[0] for r in graph.read(Q_SEED_VECTOR, k=TOP_K, vec=vec)]
        by_name = [r[0] for r in graph.read(Q_SEED_NAMES, q=lucene_query(question), k=TOP_K)]
        by_doc = [r[0] for r in graph.read(Q_SEED_DOCS, k=TOP_K, vec=vec)]
        fused = lift_drug_items(graph, fuse([by_vector, by_name, by_doc]))
        keys += [k for k in fused if k not in keys][:SEEDS - len(keys)]
    info = {r[0]: r[1:] for r in graph.read(Q_SEED_INFO, keys=keys, vec=vec, skip=SKIP_TYPES)}
    return [{"key": key, "kind": info[key][1], "name": info[key][0], "score": float(info[key][2]),
             "isolated": info[key][3] == 0} for key in keys if key in info]


def neighbor_facts(graph, seeds: list[dict], vec: list[float]) -> list[tuple[int, float, dict]]:
    """(시드 순위, 유사도, 사실). 관계 유형당 상한이 있어 허브(임산부 EXCLUDES 355건)가 문맥을 채우지 못한다."""
    out = []
    for rank, seed in enumerate(seeds):
        if seed["isolated"]:
            continue
        for t, start, end, a, b, props, sim in graph.read(Q_NEIGHBORS, key=seed["key"], vec=vec, skip=SKIP_TYPES,
                                                          cap=NEIGHBORS_PER_TYPE):
            out.append((rank, float(sim), make_fact(t, start, end, a, b, props)))
    return out


def pair_facts(graph, seeds: list[dict]) -> list[tuple[int, float, dict]]:
    """시드 쌍을 잇는 길이 2 이하 경로의 관계. 순위는 두 시드 중 낮은 쪽이다."""
    rank_of = {s["key"]: i for i, s in enumerate(seeds)}
    keys = [s["key"] for s in seeds if not s["isolated"]]
    pairs = [[a, b] for i, a in enumerate(keys) for b in keys[i + 1:]]
    if not pairs:
        return []
    rows = graph.read(Q_PAIR_PATHS, pairs=pairs, skip=SKIP_TYPES, cap=PATHS_PER_PAIR)
    return [(max(rank_of[pair[0]], rank_of[pair[1]]), 0.0, make_fact(t, start, end, a, b, props))
            for pair, t, start, end, a, b, props in rows]


def cap_per_seed(seeds: list[dict], neighbors: list, pairs: list) -> list[tuple[int, float, dict]]:
    """두 목록을 합치고, 시드 하나에 같은 관계 유형이 상한을 넘지 않게 한다. 시드를 잇는 사실이 먼저, 그다음 유사도 순이다.

    시드 잇기(3단계)가 허브 관계로 문맥을 다시 채우는 것을 막는다(임산부 EXCLUDES 355건).
    """
    seed_keys = {s["key"] for s in seeds}
    best: dict[str, tuple[int, int, float, dict]] = {}          # id → (잇기 여부 0/1, 순위, 유사도, 사실)
    for joins, items in ((0, pairs), (1, neighbors)):
        for rank, sim, fact in items:
            candidate = (joins, rank, -sim, fact)
            if fact["id"] not in best or candidate[:3] < best[fact["id"]][:3]:
                best[fact["id"]] = candidate
    used: dict[tuple[str, str], int] = {}
    kept = []
    for joins, rank, neg_sim, fact in sorted(best.values(), key=lambda x: (x[0], x[1], x[2], x[3]["id"])):
        slots = [(k, fact["type"]) for k in (fact["start"], fact["end"]) if k in seed_keys]
        if all(used.get(slot, 0) < NEIGHBORS_PER_TYPE for slot in slots):
            for slot in slots:
                used[slot] = used.get(slot, 0) + 1
            kept.append((rank, -neg_sim, fact))
    return sorted(kept, key=lambda x: (x[0], -x[1], x[2]["id"]))


def ingredient_entries(seeds: list[dict], facts: list[tuple[int, float, dict]]) -> dict[str, tuple[int, str]]:
    """문맥에 든 성분 ID → (가장 좋은 시드 순위, 이름)."""
    entries: dict[str, tuple[int, str]] = {}

    def note(key: str, rank: int, name: str):
        if key.startswith("Ingredient:"):
            iid = key.split(":", 1)[1]
            if iid not in entries or rank < entries[iid][0]:
                entries[iid] = (rank, name)

    for rank, seed in enumerate(seeds):
        note(seed["key"], rank, seed["name"])
    for rank, _, fact in facts:
        for key, name in ((fact["start"], fact["_a"]), (fact["end"], fact["_b"])):
            note(key, rank, name)
    return entries


def fetch_docs(graph, entries: dict[str, tuple[int, str]], vec: list[float]) -> list[tuple[int, int, float, dict]]:
    """(시드 순위, 문서 등급, 유사도, 문서). 기능·주의 문서가 0등급이다."""
    ids = sorted(entries, key=lambda iid: (entries[iid][0], iid))
    if not ids:
        return []
    rows = graph.read(Q_DOCS, ids=ids, vec=vec, first=FIRST_DOC_KINDS, cap=DOCS_PER_INGREDIENT)
    return [(entries[iid][0], tier, float(sim), {"id": doc_id, "kind": kind, "text": text, "ingredient_id": iid})
            for iid, doc_id, kind, text, tier, sim in rows]


def within_budget(facts: list, docs: list) -> tuple[list, list]:
    """토큰 예산을 넘으면 순위가 가장 낮은 쪽부터 문서, 그다음 사실을 뺀다."""
    while facts or docs:
        tokens = llm.count_tokens([f["text"] for _, _, f in facts] + [d["text"] for _, _, _, d in docs])
        if tokens <= TOKEN_BUDGET:
            break
        worst = max([r for r, _, _ in facts] + [r for r, _, _, _ in docs])
        worst_docs = [d for d in docs if d[0] == worst]
        if worst_docs:
            docs.remove(worst_docs[-1])
        else:
            facts.remove([f for f in facts if f[0] == worst][-1])
    return facts, docs


def retrieve(graph, question: str, embed, mentions: list[str] | None = None) -> dict:
    """mentions는 질문 분석(LLM)이 뽑은 개체 언급이다. 없어도 동작한다."""
    vec = embed([question])[0]
    seeds = find_seeds(graph, question, vec, embed, mentions)

    facts = cap_per_seed(seeds, neighbor_facts(graph, seeds, vec), pair_facts(graph, seeds))

    entries = ingredient_entries(seeds, facts)
    docs = sorted(fetch_docs(graph, entries, vec), key=lambda x: (x[0], x[1], -x[2], x[3]["id"]))
    facts, docs = within_budget(facts, docs)

    kept_keys = {s["key"] for s in seeds} | {k for _, _, f in facts for k in (f["start"], f["end"])}
    kept_ids = [iid for iid in sorted(entries, key=lambda i: (entries[i][0], i)) if f"Ingredient:{iid}" in kept_keys]
    return {
        "seeds": seeds,
        "facts": [{k: v for k, v in f.items() if not k.startswith("_")} for _, _, f in facts],
        "docs": [d for _, _, _, d in docs if d["ingredient_id"] in kept_ids],
        "ingredient_ids": kept_ids,
        "ingredient_names": {iid: entries[iid][1] for iid in kept_ids},
        "max_seed_score": max((s["score"] for s in seeds), default=0.0),
    }
