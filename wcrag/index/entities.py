"""개체 설명 문장과 임베딩을 만든다. 실행: python -m wcrag.index.entities [--dry-run]

질문에서 "어느 개체를 묻는가"를 찾으려면 개체마다 검색할 글이 있어야 한다. 그래서 개체 1,220개 각각에
  rag_text  : 개체를 한두 문장으로 설명한 글 (임베딩 대상)
  rag_names : 이름과 별칭 (전문검색 대상)
  rag_key   : "<라벨>:<고유값>" 형태의 키
를 붙이고 :RagEntity 라벨을 단다. 설명 문장은 규칙으로 만들며 LLM을 쓰지 않는다.
"""
import argparse
import csv
import re

from .. import config, llm
from ..db import Graph, connect

ALIAS_FILE = config.LEGACY_GRAPHDB / "data" / "mapping" / "nutrient_alias.csv"
NOT_ENTITY = ["Doc", "RuleSet", "RagMeta"]
INGREDIENT_TYPES = {"nutrient": "영양성분", "notice": "고시형 원료", "individual": "개별인정형 원료",
                    "group": "묶음 원료", "food_component": "식품 성분"}
CONDITION_KINDS = {"exam": "검진", "survey": "문진", "population": "인구집단", "disease": "질환"}
WRITE_BATCH, VECTOR_BATCH = 500, 200

Q_NODES = "MATCH (n:`{label}`) RETURN elementId(n), properties(n)"
Q_TAGS = "MATCH (i:Ingredient)-[:HAS_FUNCTION]->(t:FunctionTag) RETURN i.ingredient_id, collect(DISTINCT t.name)"
Q_CLASSES = "MATCH (d:DrugItem)-[:IN_CLASS]->(g:Drug) RETURN d.item_id, collect(DISTINCT g.name)"
Q_ENTITY_COUNT = "MATCH (n) WHERE none(l IN labels(n) WHERE l IN $skip) RETURN count(n)"
Q_DOCS_MISSING = "MATCH (d:Doc) WHERE d.embedding IS NULL AND d.text IS NOT NULL RETURN d.doc_id, d.text ORDER BY d.doc_id"
Q_WRITE_TEXT = """UNWIND $rows AS row MATCH (n) WHERE elementId(n) = row.eid
SET n:RagEntity, n.rag_key = row.key, n.rag_kind = row.kind, n.rag_text = row.text, n.rag_names = row.names"""
Q_WRITE_VECTOR = """UNWIND $rows AS row MATCH (n) WHERE elementId(n) = row.eid
CALL db.create.setNodeVectorProperty(n, 'rag_embedding', row.vector)"""
Q_WRITE_DOC_VECTOR = """UNWIND $rows AS row MATCH (d:Doc {doc_id: row.id})
CALL db.create.setNodeVectorProperty(d, 'embedding', row.vector)"""
INDEXES = [
    "CREATE INDEX rag_entity_key IF NOT EXISTS FOR (n:RagEntity) ON (n.rag_key)",
    f"""CREATE VECTOR INDEX rag_entity_embedding IF NOT EXISTS FOR (n:RagEntity) ON n.rag_embedding
OPTIONS {{indexConfig: {{`vector.dimensions`: {config.EMBED_DIM}, `vector.similarity_function`: 'cosine'}}}}""",
    """CREATE FULLTEXT INDEX rag_entity_names IF NOT EXISTS FOR (n:RagEntity) ON EACH [n.rag_names]
OPTIONS {indexConfig: {`fulltext.analyzer`: 'cjk'}}""",
]


def flat(text, limit=None) -> str:
    """줄 머리의 번호·기호를 지우고 줄들을 ' / '로 이어 한 줄로 만든다."""
    lines = [re.sub(r"^\s*(\d+\.|-|ㆍ)\s*", "", line).strip() for line in str(text or "").splitlines()]
    one_line = " / ".join(re.sub(r"\s+", " ", line) for line in lines if line)
    return one_line[:limit] if limit else one_line


def unique(values) -> list[str]:
    return list(dict.fromkeys(str(v).strip() for v in values if v and str(v).strip()))


def load_aliases() -> dict[str, list[str]]:
    """성분 ID → 별칭. 매핑표의 영양소 이름이 성분 이름과 다르면 별칭이다(예: 비타민B5 → 판토텐산)."""
    aliases = {}
    with open(ALIAS_FILE, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            for ingredient_id in filter(None, re.split(r"[;,|]\s*", row["ingredient_ids"] or "")):
                aliases.setdefault(ingredient_id.strip(), []).append(row["nutrient_name"].strip())
    return aliases


def describe(label: str, props: dict, neighbors: dict) -> tuple[str, list[str]]:
    """(rag_text, rag_names). 라벨마다 무엇을 넣는지는 플랜 Task 3의 표를 따른다."""
    name = props.get("label") if label == "Condition" else props.get("name")
    names = [name]
    if label == "Ingredient":
        kind = INGREDIENT_TYPES.get(props.get("type"), props.get("type") or "원료")
        if props.get("nutrient_class"):
            kind += f"·{props['nutrient_class']}"
        parts = [f"원료 {name} ({kind})"]
        if props.get("function_text"):
            parts.append(f"기능성: {flat(props['function_text'], 200)}")
        if neighbors.get("tags"):
            parts.append(f"기능 분류: {', '.join(sorted(neighbors['tags']))}")
        names += neighbors.get("aliases", [])
    elif label == "Symptom":
        parts = [f"증상 {name}", f"분류: {props.get('category') or '없음'}"]
        if props.get("aliases"):
            parts.append(f"다른 표현: {', '.join(props['aliases'])}")
        names += props.get("aliases") or []
    elif label == "Drug":
        generic = [g.strip() for g in re.split(r"[,/]", str(props.get("generic_names") or "").strip("() "))]
        parts = [f"약 {name}", f"계열: {props.get('drug_class') or '없음'}"]
        if any(generic):
            parts.append(f"성분·표기: {', '.join(g for g in generic if g)}")
        if props.get("side_effects"):
            parts.append(f"부작용: {flat(props['side_effects'], 150)}")
        names += generic
    elif label == "DrugItem":
        parts = [f"약 표기 {name}"]
        if neighbors.get("classes"):
            parts.append(f"속한 계열: {', '.join(sorted(neighbors['classes']))}")
        names.append(props.get("match"))
    elif label == "Condition":
        parts = [f"{CONDITION_KINDS.get(props.get('kind'), props.get('kind'))} 조건 {name}"]
        if props.get("exam_item"):
            if props.get("exam_value") is not None:
                parts.append(f"검진 항목 {props['exam_item']}, 값 {props['exam_value']}")
            else:
                lo = "" if props.get("exam_lo") is None else f"{props['exam_lo']:g}"
                hi = "" if props.get("exam_hi") is None else f"{props['exam_hi']:g}"
                parts.append(f"검진 항목 {props['exam_item']}, 구간 {lo}~{hi}")
        if props.get("rule_group"):
            parts.append(f"묶음: {props['rule_group']}")
    else:
        head = {"FunctionTag": "기능성 분류", "SymptomCategory": "증상 분류", "Interest": "건강 관심사",
                "Segment": "성별·연령 구분", "Disease": "질환", "LifeStage": "생애주기", "Mechanism": "증상 기전",
                "Company": "회사"}[label]
        detail = props.get("definition") or props.get("group") or props.get("ageLabel")
        parts = [f"{head} {name}"] + ([str(detail)] if detail else [])
    return ". ".join(parts) + ".", unique(names)


def collect(graph: Graph) -> list[dict]:
    """개체 노드를 모두 읽어 설명 문장을 붙인다. 아는 라벨로 설명하지 못한 노드가 있으면 멈춘다."""
    tags, classes, aliases = dict(graph.read(Q_TAGS)), dict(graph.read(Q_CLASSES)), load_aliases()
    entities = []
    for label, (key_prop, _) in config.ENTITY_KEYS.items():
        for eid, props in graph.read(Q_NODES.format(label=label), timeout_s=60):
            node_id = props[key_prop]
            neighbors = {"tags": tags.get(node_id, []), "aliases": aliases.get(node_id, []), "classes": classes.get(node_id, [])}
            text, names = describe(label, props, neighbors)
            entities.append({"eid": eid, "key": f"{label}:{node_id}", "kind": label, "text": text, "names": names})
    total = graph.read(Q_ENTITY_COUNT, skip=NOT_ENTITY + ["XEntity", "Community"])[0][0]
    if total != len(entities):
        raise RuntimeError(f"설명하지 못한 개체가 있습니다: 그래프 {total}개, 설명 {len(entities)}개. ENTITY_KEYS에 라벨을 추가하세요.")
    return entities


def chunks(rows: list, size: int):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def build(graph: Graph, embed=llm.embed, dry_run: bool = False) -> dict:
    """설명 → (저장 파일에 없는 문장만) 임베딩 → DB 기록 → 인덱스. dry_run이면 건수와 토큰만 센다."""
    entities = collect(graph)
    docs = graph.read(Q_DOCS_MISSING)
    texts = unique([e["text"] for e in entities] + [text for _, text in docs])
    vectors = llm.cached_embeddings(texts)
    missing = [t for t in texts if t not in vectors]
    report = {"entities": len(entities), "docs_missing_embedding": len(docs), "texts": len(texts),
              "cached": len(texts) - len(missing), "to_embed": len(missing),
              "tokens": llm.count_tokens(missing) if missing else 0, "written": False}
    if dry_run:
        return report
    if missing:
        vectors.update(zip(missing, embed(missing)))
    for part in chunks([{k: e[k] for k in ("eid", "key", "kind", "text", "names")} for e in entities], WRITE_BATCH):
        graph.run(Q_WRITE_TEXT, rows=part)
    for part in chunks([{"eid": e["eid"], "vector": vectors[e["text"]]} for e in entities], VECTOR_BATCH):
        graph.run(Q_WRITE_VECTOR, rows=part)
    if docs:
        graph.run(Q_WRITE_DOC_VECTOR, rows=[{"id": doc_id, "vector": vectors[text]} for doc_id, text in docs])
    for statement in INDEXES:
        graph.run(statement)
    graph.run("CALL db.awaitIndexes(300)")
    return {**report, "written": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description="개체 설명과 임베딩 색인")
    parser.add_argument("--dry-run", action="store_true", help="호출·기록 없이 건수와 토큰만 출력")
    args = parser.parse_args(argv)
    graph = connect("rag")
    try:
        report = build(graph, dry_run=args.dry_run)
    finally:
        graph.close()
    print(f"개체 {report['entities']:,}개, 임베딩 없는 문서 {report['docs_missing_embedding']}건")
    print(f"임베딩 대상 문장 {report['texts']:,}건 중 저장 파일에 있음 {report['cached']:,}건, "
          f"새로 호출할 문장 {report['to_embed']:,}건 (약 {report['tokens']:,}토큰, 모델 {config.EMBED_MODEL})")
    print("기록 완료." if report["written"] else "dry-run: OpenAI를 호출하지 않았고 DB에 쓰지 않았습니다.")


if __name__ == "__main__":
    main()
