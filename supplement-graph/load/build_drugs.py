#!/usr/bin/env python3
"""복용약 → 영양소 근거를 그래프에 올린다. build_graph.py 다음에 실행한다.

    python3 load/build_drugs.py

올리는 것 (load/refs/drug_nutrient_kr.json, 식약처 의약품안전나라 허가사항에서 확인한 문장)
  - 약 성분·계열 노드와 사용자 약 이름에 거는 정규식(match)
  - 약 → 영양소 DEPLETES (grade A = 추천 후보, B = 참고 표시)
  - 영양소 → 약 INTERACTS_WITH (허가사항이 병용을 조심하라고 한 것, severity = caution)

다시 실행하면 이 스크립트가 올린 관계만 지우고 새로 올린다. 주의사항 문구에서 뽑은 기존 관계는 건드리지 않는다.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_graph import ROOT, load_blocks, run_block, shell  # noqa: E402

REFS = ROOT / "load" / "refs" / "drug_nutrient_kr.json"
PROP_KEYS = ("grade", "effect", "quote", "product", "item_seq", "url", "note")


def main():
    q = load_blocks()
    refs = json.loads(REFS.read_text(encoding="utf-8"))
    checked = refs["checked_at"]
    orgs = sorted({e["org"] for e in refs["depletes"] + refs["cautions"]})

    shell("MATCH ()-[r:DEPLETES]->() DELETE r")
    shell("MATCH ()-[r:INTERACTS_WITH]->() WHERE r.source IN $orgs DELETE r", {"orgs": orgs})

    print("약 성분 · 계열")
    drugs = sorted(refs["drugs"], key=lambda d: d["level"] != "class")      # 계열을 먼저 넣어야 성분이 이어진다
    run_block(q, "upsert_drug", [
        {"drug_id": d["drug_id"], "class_id": d.get("class_id"), "source": "약효 분류",
         "props": {"name": d["name"], "level": d["level"], "match": d["match"]}} for d in drugs])

    print("복용약 → 영양소")
    run_block(q, "link_drug_depletes", [
        {"drug_id": e["drug_id"], "ingredient_id": e["ingredient_id"], "source": e["org"],
         "props": {**{k: e[k] for k in PROP_KEYS if e.get(k)}, "checked_at": checked}} for e in refs["depletes"]])
    run_block(q, "link_interacts_with", [
        {"ingredient_id": e["ingredient_id"], "drug_id": e["drug_id"], "severity": e["severity"], "note": e["note"],
         "source": e["org"], "props": {**{k: e[k] for k in ("quote", "product", "item_seq", "url")},
                                       "evidence": "confirmed", "checked_at": checked}} for e in refs["cautions"]])

    print("\n결과")
    n_dep = int(shell("MATCH ()-[r:DEPLETES]->() RETURN count(r)")[0])
    n_cau = int(shell("MATCH ()-[r:INTERACTS_WITH]->() WHERE r.source IN $orgs RETURN count(r)", {"orgs": orgs})[0])
    for r in shell("MATCH (d:Drug)-[r:DEPLETES]->(i:Ingredient) RETURN r.grade + ' ' + d.name + ' → ' + i.name ORDER BY r.grade, d.name, i.name"):
        print("  ", r)
    ok = n_dep == len(refs["depletes"]) and n_cau == len(refs["cautions"])
    print(f"\nDEPLETES {n_dep}/{len(refs['depletes'])} · 허가사항 주의 {n_cau}/{len(refs['cautions'])} → {'일치' if ok else '불일치'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
