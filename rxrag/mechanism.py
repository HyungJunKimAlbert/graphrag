"""증상 → 기전 → 성분 층을 RAG DB(7689)에 적재한다. 원천은 symptom_mechanism_evidence_kr.xlsx.

증상·성분 ID는 dev 그래프(7475, 읽기 전용) 것을 쓴다. 후보 수집이 두 DB를 ingredient_id·symptom_id로 잇기 때문이다.
실행: .venv/bin/python -m rxrag.mechanism  (RAG DB의 Symptom·Mechanism·Ingredient를 지우고 다시 올린다)
"""
import json
import re
import sys
from collections import Counter

import openpyxl

from . import config, graph, legacy

STRENGTH = {"근거 있음": 0, "부분 근거": 1}
Q_DEV_ING = "MATCH (i:Ingredient) RETURN i.ingredient_id, i.name"


def _key(name):
    return re.sub(r"\s+", "", str(name))


def _bare(name):
    return re.sub(r"\s+|\(.*?\)", "", str(name))


def ingredient_index(rows):
    """dev 성분 [(id, 이름)] → {ids, 이름 키 → id}."""
    by_name = {}
    for iid, name in rows:
        by_name.setdefault(_key(name), iid)
        by_name.setdefault(_bare(name), iid)
    return {"ids": {iid for iid, _ in rows}, "by_name": by_name}


def resolve_ingredient(name, index):
    """개별인정형은 인정번호(제YYYY-N호 → IND-YYYY-N), 아니면 이름(공백·괄호 무시)."""
    m = re.search(r"제\s*(\d{4})-(\d+)\s*호", name)
    if m and f"IND-{m.group(1)}-{int(m.group(2))}" in index["ids"]:
        return f"IND-{m.group(1)}-{int(m.group(2))}"
    return index["by_name"].get(_key(name)) or index["by_name"].get(_bare(name))


def resolve_symptom(dev, name):
    """엔진 resolve_symptoms(표준명·별칭, 엄격 일치)로 dev 증상 ID와 표준명. 주호소 정규화 결과가 가는 길과 같다."""
    found, _ = legacy.resolve_symptoms(dev, [name])
    return (found[0]["symptom_id"], found[0]["name"]) if len(found) == 1 else (None, None)


def _rows(wb, sheet):
    rows = list(wb[sheet].iter_rows(values_only=True))
    head = list(rows[0])
    return [dict(zip(head, r)) for r in rows[1:] if any(v is not None for v in r)]


def build(dev, path=config.MECH_XLSX):
    """엑셀 → 적재용 노드·관계와 보고서. 증상이 그래프에 없으면 그 연결은 빼고 보고한다."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        links, mechs, mech_ing = _rows(wb, "증상-기전 판정"), _rows(wb, "기전 목록"), _rows(wb, "기전-성분")
    finally:
        wb.close()
    ing_rows = dev.run(Q_DEV_ING)
    ing_idx, ing_names = ingredient_index(ing_rows), dict(ing_rows)

    edges, unmatched_sym = {}, Counter()
    for r in links:
        sid, sname = resolve_symptom(dev, r["증상 키워드"])
        if sid is None:
            unmatched_sym[r["증상 키워드"]] += 1
            continue
        e = {"symptom_id": sid, "symptom": sname, "keyword": r["증상 키워드"], "mechanism_id": r["기전 ID"], "verdict": r["최종 판정"],
             "category": r["분류명"], "evidence_origin": r["대표 근거"], "treatment": r["처리 구분"],
             "source_org": r["대표 출처 기관"], "doc": r["대표 문서"], "url": r["대표 URL"],
             "quote": r["대표 인용(한글: 국내는 원문, 해외는 번역)"]}
        k = (sid, e["mechanism_id"])  # 같은 증상이 두 대분류에 있으면 더 강한 판정을 남긴다
        if k not in edges or STRENGTH.get(e["verdict"], 9) < STRENGTH.get(edges[k]["verdict"], 9):
            edges[k] = e

    addressed, unmatched_ing = {}, Counter()
    for r in mech_ing:
        iid = resolve_ingredient(r["성분"], ing_idx)
        if iid is None:
            unmatched_ing[r["성분"]] += 1
            continue
        addressed.setdefault((r["기전 ID"], iid), {"mechanism_id": r["기전 ID"], "ingredient_id": iid,
                                                  "claim": r["식약처 기능성 문구(원문)"], "source": r["출처"],
                                                  "match": r["매칭 경로(자동, 검토 필요)"], "raw_name": r["성분"]})

    symptoms = {e["symptom_id"]: e["symptom"] for e in edges.values()}
    used_ing = {a["ingredient_id"] for a in addressed.values()}
    return {
        "symptoms": [{"symptom_id": k, "name": v} for k, v in sorted(symptoms.items())],
        "mechanisms": [{"mechanism_id": m["기전 ID"], "name": m["기전"], "group": m["기전 분류"],
                        "treatment": m["처리 구분"], "origin": m["원천"]} for m in mechs],
        "ingredients": [{"ingredient_id": i, "name": ing_names[i]} for i in sorted(used_ing)],
        "edges": sorted(edges.values(), key=lambda e: (e["symptom_id"], e["mechanism_id"])),
        "addressed": sorted(addressed.values(), key=lambda a: (a["mechanism_id"], a["ingredient_id"])),
        "report": {"links_in": len(links), "edges": len(edges), "verdicts": dict(Counter(e["verdict"] for e in edges.values())),
                   "unmatched_symptoms": dict(unmatched_sym), "mech_ing_in": len(mech_ing),
                   "addressed": len(addressed), "unmatched_ingredients": dict(unmatched_ing)},
    }


LOAD = [
    "MATCH (n) WHERE n:Symptom OR n:Mechanism OR n:Ingredient DETACH DELETE n",
    "CREATE CONSTRAINT mech_sym IF NOT EXISTS FOR (n:Symptom) REQUIRE n.symptom_id IS UNIQUE",
    "CREATE CONSTRAINT mech_mech IF NOT EXISTS FOR (n:Mechanism) REQUIRE n.mechanism_id IS UNIQUE",
    "CREATE CONSTRAINT mech_ing IF NOT EXISTS FOR (n:Ingredient) REQUIRE n.ingredient_id IS UNIQUE",
]
NODES = [
    ("symptoms", "UNWIND $rows AS r MERGE (n:Symptom {symptom_id: r.symptom_id}) SET n.name = r.name"),
    ("mechanisms", "UNWIND $rows AS r MERGE (n:Mechanism {mechanism_id: r.mechanism_id}) SET n += r"),
    ("ingredients", "UNWIND $rows AS r MERGE (n:Ingredient {ingredient_id: r.ingredient_id}) SET n.name = r.name"),
    ("edges", """UNWIND $rows AS r MATCH (s:Symptom {symptom_id: r.symptom_id}), (m:Mechanism {mechanism_id: r.mechanism_id})
MERGE (s)-[e:HAS_MECHANISM]->(m)
SET e += r {.keyword, .verdict, .category, .evidence_origin, .treatment, .source_org, .doc, .url, .quote}"""),
    ("addressed", """UNWIND $rows AS r MATCH (m:Mechanism {mechanism_id: r.mechanism_id}), (i:Ingredient {ingredient_id: r.ingredient_id})
MERGE (m)-[x:ADDRESSED_BY]->(i) SET x += r"""),
]


def load(rag, data):
    for q in LOAD:
        rag.run(q)
    for key, q in NODES:
        rows = data[key]
        for i in range(0, len(rows), 500):
            rag.run(q, rows=rows[i:i + 500])
    return rag.run("MATCH (n) RETURN labels(n)[0], count(*) ORDER BY labels(n)[0]")


def main():
    data = build(graph.connect())
    print(json.dumps(data["report"], ensure_ascii=False, indent=1))
    print(load(graph.connect_rag(write=True), data))


if __name__ == "__main__":
    sys.exit(main())
