"""③ 후보 풀과 근거 사실. 엔진 생성기 8단계와 같은 매칭 조건으로 모든 경로를 모으고, 경로 하나를 사실 하나로 돌려준다.

엔진 쿼리는 근거를 문자열로 합쳐 노드 ID를 잃는다. 여기 쿼리는 WHERE 조건을 그대로 두고 경로의 노드 ID를 RETURN한다.
엔진과의 어긋남은 tests/test_candidates.py의 동등성 테스트(엔진 성분 ID ⊆ 풀 성분 ID)가 막는다.
"""
from . import config, graph

UNRANKED = 999  # recommend/candidates.py UNRANKED

# kind → 엔진 단계 순번(tier_rank)
KINDS = {"SYM_DIRECT": 0, "DRUG_SYM_DEPLETE": 1, "SYM_MECH": 1.5, "CAT_FUNC": 2, "RULE_EXAM": 3, "RULE_COND": 4, "DRUG_DEPLETE": 5,
         "RULE_BORDER": 6, "INTEREST": 7}
KIND_LABELS = {"SYM_DIRECT": "증상 직접 근거", "DRUG_SYM_DEPLETE": "복용약 고갈+증상", "SYM_MECH": "증상 기전 근거", "CAT_FUNC": "증상 분류 기능성",
               "RULE_EXAM": "검진 규칙", "RULE_COND": "질환 규칙", "DRUG_DEPLETE": "복용약 고갈",
               "RULE_BORDER": "경계 수치", "INTEREST": "성별·연령 관심사"}
DATA_KINDS ={"RULE_EXAM", "RULE_COND", "RULE_BORDER", "DRUG_DEPLETE"}  # 엔진 data_signals 출신. 묶음 접기 대상
SECONDARY_KINDS = {"INTEREST"}  # 2순위: 성별·연령 관심사. 1순위(증상·검진·질환·복용약)가 모자랄 때만 채운다


def tier(c):
    """후보 계층. 1순위 근거가 하나라도 있으면 1, 관심사 근거만 있으면 2."""
    return 2 if all(f["kind"] in SECONDARY_KINDS for f in c["facts"]) else 1

# from recommend/candidates.py Q_EXPAND
Q_EXPAND = """MATCH (s:Symptom) WHERE s.symptom_id IN $ids
OPTIONAL MATCH (s)-[:RELATED_TO]->(r:Symptom)
RETURN collect(DISTINCT s.symptom_id) + collect(DISTINCT r.symptom_id)"""

# from recommend/candidates.py Q_DIRECT
Q_DIRECT = """MATCH (s:Symptom)-[r:RELIEVED_BY]->(i:Ingredient)
WHERE s.symptom_id IN $ids AND i.recommendable
RETURN i.ingredient_id, i.name, i.type, [s.symptom_id, i.ingredient_id], s.name + ' → ' + r.evidence, r.source,
       r.priority"""

# from recommend/candidates.py Q_DEPLETION
Q_DEPLETION = """MATCH (d:Drug)-[:MAY_CAUSE]->(s:Symptom), (d)-[x:DEPLETES]->(i:Ingredient)
WHERE d.drug_id IN $drugs AND s.symptom_id IN $ids AND i.recommendable
RETURN i.ingredient_id, i.name, i.type, [d.drug_id, s.symptom_id, i.ingredient_id],
       d.name + ' 복용 → ' + s.name + ' 유발 가능 + ' + i.name + ' 고갈', null,
       CASE x.evidence WHEN 'confirmed' THEN 0 ELSE 100 END + x.order"""

# from recommend/candidates.py Q_FUNCTION
Q_FUNCTION = """MATCH (s:Symptom)<-[n:INCLUDES]-(c:SymptomCategory)-[a:ADDRESSED_BY]->(t:FunctionTag)<-[f:HAS_FUNCTION]-(i:Ingredient)
WHERE ((s.symptom_id IN $ids AND n.primary) OR c.name IN $categories) AND i.recommendable
  AND (f.target IS NULL OR f.target = $sex OR (f.target = 'pregnancy' AND $pregnant))
RETURN DISTINCT i.ingredient_id, i.name, i.type, [c.code, t.tag_id, i.ingredient_id], c.name + ' → ' + t.name, null,
       a.priority"""

# from recommend/candidates.py Q_RULES (+ 단계 구분용 c.kind, r.level)
Q_RULES = """MATCH (c:Condition)-[r:RECOMMENDS]->(i:Ingredient)
WHERE c.code IN $codes AND i.recommendable AND all(x IN coalesce(r.requires, []) WHERE x IN $codes)
RETURN i.ingredient_id, i.name, i.type, [c.code, i.ingredient_id], c.label + ' → ' + i.name, null,
       coalesce(r.priority, $unranked), c.kind, r.level"""

# from recommend/candidates.py Q_DEPLETION_ANY
Q_DEPLETION_ANY = """MATCH (d:Drug)-[x:DEPLETES]->(i:Ingredient)
WHERE d.drug_id IN $drugs AND i.recommendable
RETURN i.ingredient_id, i.name, i.type, [d.drug_id, i.ingredient_id], d.name + ' 복용 → ' + i.name + ' 고갈', null,
       CASE x.evidence WHEN 'confirmed' THEN 0 ELSE 100 END + x.order"""

# from recommend/candidates.py Q_INTEREST
Q_INTEREST = """MATCH (g:Segment {code: $segment})-[h:HAS_INTEREST]->(n:Interest)-[r:RECOMMENDS]->(i:Ingredient)
WHERE i.recommendable
RETURN i.ingredient_id, i.name, i.type, [g.code, n.code, i.ingredient_id],
       n.name + ' → ' + i.name + CASE WHEN r.reason = '' THEN '' ELSE ' (' + r.reason + ')' END, null,
       h.order * 100 + r.order"""

# RAG DB(7689)의 증상 → 기전 → 성분 층. 엔진에는 없는 우리 경로다(근거: symptom_mechanism_evidence_kr.xlsx)
Q_MECH = """MATCH (s:Symptom)-[e:HAS_MECHANISM]->(m:Mechanism)-[x:ADDRESSED_BY]->(i:Ingredient)
WHERE s.symptom_id IN $ids AND e.verdict IN $verdicts
RETURN s.symptom_id, s.name, m.mechanism_id, m.name, e.source_org, e.verdict, i.ingredient_id, x.claim, x.source, e.url"""
# 추천 가능 여부·이름·유형은 dev 그래프가 원천이다. 기능성이 모두 다른 성별(·임신) 대상이면 뺀다(Q_FUNCTION의 target 조건)
Q_RECOMMENDABLE = """MATCH (i:Ingredient) WHERE i.ingredient_id IN $ids AND i.recommendable
OPTIONAL MATCH (i)-[f:HAS_FUNCTION]->()
WITH i, collect(f) AS fs
WHERE size(fs) = 0 OR any(f IN fs WHERE f.target IS NULL OR f.target = $sex OR (f.target = 'pregnancy' AND $pregnant))
RETURN i.ingredient_id, i.name, i.type"""

# from recommend/candidates.py Q_GROUPS
Q_GROUPS = "MATCH (i:Ingredient {type: 'group'}) RETURN i.ingredient_id, i.members"


def expanded_symptoms(db, ctx):
    ids = [s["symptom_id"] for s in ctx["symptoms"]]
    return [i for i in db.run(Q_EXPAND, ids=ids)[0][0] if i] if ids else []


def mechanism_paths(db, rag, symptom_ids, sex, pregnant):
    """환자 증상(정규화 직후, RELATED_TO 확장 전)의 기전 경로. row 형식은 다른 경로와 같다. 국내 대표 근거를 앞에 둔다."""
    if rag is None or not symptom_ids:
        return []
    hits = rag.run(Q_MECH, ids=symptom_ids, verdicts=list(config.MECH_VERDICTS))
    ids = sorted({h[6] for h in hits})
    ok = {r[0]: (r[1], r[2]) for r in db.run(Q_RECOMMENDABLE, ids=ids, sex=sex, pregnant=pregnant)} if hits else {}
    out = []
    for sid, sname, mid, mname, org, _verdict, iid, claim, src, url in hits:
        if iid not in ok:
            continue
        name, typ = ok[iid]
        text = f"{sname} → {mname}({org}) → {name}: {claim}"
        out.append(("SYM_MECH", [iid, name, typ, [sid, mid, iid], text, url or src, 0]))
    return out


def rule_kind(row):
    """엔진 data_signals와 같은 구분: level '경계' → 경계, kind exam → 검진, 그 외 → 질환."""
    return "RULE_BORDER" if row[8] == "경계" else "RULE_EXAM" if row[7] == "exam" else "RULE_COND"


def paths(db, ctx, rag=None):
    """(kind, row) 목록. row = [iid, name, type, node_ids, text, source, priority, ...]."""
    ids = expanded_symptoms(db, ctx)
    direct_ids = [s["symptom_id"] for s in ctx["symptoms"]]
    drugs = ctx["drugs"]["full"]
    out = []
    if ids:
        out += [("SYM_DIRECT", r) for r in db.run(Q_DIRECT, ids=ids)]
        if drugs:
            out += [("DRUG_SYM_DEPLETE", r) for r in db.run(Q_DEPLETION, ids=ids, drugs=drugs)]
    out += mechanism_paths(db, rag, direct_ids, ctx["sex"], ctx["pregnant"])
    if direct_ids or ctx["categories"]:
        out += [("CAT_FUNC", r) for r in db.run(Q_FUNCTION, ids=direct_ids, categories=ctx["categories"],
                                                  sex=ctx["sex"], pregnant=ctx["pregnant"])]
    if ctx["codes"]:
        out += [(rule_kind(r), r) for r in db.run(Q_RULES, codes=ctx["codes"], unranked=UNRANKED)]
    if drugs:
        out += [("DRUG_DEPLETE", r) for r in db.run(Q_DEPLETION_ANY, drugs=drugs)]
    if ctx["segment"]:
        out += [("INTEREST", r) for r in db.run(Q_INTEREST, segment=ctx["segment"]["code"])]
    return out


def fact(kind, row):
    return {"fact_id": f"{kind}:{'>'.join(row[3])}", "kind": kind, "label": KIND_LABELS[kind], "text": row[4],
            "source": row[5]}


def collect(rows):
    """같은 성분의 사실을 합친다. 사실마다 그 단계의 우선순위를 기억해 둔다(정렬용)."""
    found = {}
    for kind, row in rows:
        c = found.setdefault(row[0], {"ingredient_id": row[0], "name": row[1], "type": row[2], "members": [],
                                      "facts": [], "_prio": {}})
        f = fact(kind, row)
        if f["fact_id"] not in c["_prio"]:
            c["facts"].append(f)
            c["_prio"][f["fact_id"]] = UNRANKED if row[6] is None else row[6]
    return found


def fold_groups(db, found):
    """엔진 fold_groups와 같은 규칙: 묶음이 데이터 신호(규칙·고갈)로 켜졌으면 구성 성분의 데이터 신호 사실을 묶음으로 옮긴다.
    증상 경로 사실은 구성 성분에 남는다(엔진도 증상 단계의 구성 성분을 따로 낸다). 사실이 다 옮겨진 구성 성분은 빠진다."""
    for gid, members in db.run(Q_GROUPS):
        if gid not in found:
            continue
        group = found[gid]
        group["members"] = list(members)
        if not any(f["kind"] in DATA_KINDS for f in group["facts"]):
            continue
        for member in (m for m in members if m in found):
            m = found[member]
            moving = [f for f in m["facts"] if f["kind"] in DATA_KINDS]
            for f in moving:
                if f["fact_id"] not in group["_prio"]:
                    group["facts"].append(f)
                    group["_prio"][f["fact_id"]] = m["_prio"][f["fact_id"]]
            m["facts"] = [f for f in m["facts"] if f["kind"] not in DATA_KINDS]
            if not m["facts"]:
                del found[member]
    return found


def rank(cands):
    """rule_rank = (tier_rank, 단계 내 priority, -사실 수, ingredient_id) 정렬 순번."""
    ordered = sorted(cands, key=lambda c: (c["tier_rank"], c["priority"], -len(c["facts"]), c["ingredient_id"]))
    for i, c in enumerate(ordered):
        c["rule_rank"] = i
    return ordered


def pool(db, ctx, rag=None):
    """rag 없음(None)이면 graph.rag()의 기본 연결을 쓴다. 기전 경로를 끄려면 rag=False."""
    rag = graph.rag() if rag is None else (rag or None)
    found = fold_groups(db, collect(paths(db, ctx, rag)))
    cands = []
    for c in found.values():
        c["facts"].sort(key=lambda f: (KINDS[f["kind"]], c["_prio"][f["fact_id"]], f["fact_id"]))
        best = c["facts"][0]
        c["tier_rank"] = KINDS[best["kind"]]
        c["priority"] = c["_prio"][best["fact_id"]]
        del c["_prio"]
        cands.append(c)
    return rank(cands)
