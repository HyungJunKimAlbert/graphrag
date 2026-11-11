"""1단계 주호소 정규화 평가. 약사가 적는 짧은 구 형태의 주호소로 기대 증상이 나오는지 본다.

    python3 test/eval_normalize.py        # LLM(OpenAI)을 호출하므로 과금된다. 같은 요청은 캐시를 다시 쓴다
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from reco.graph import Graph  # noqa: E402
from reco.llm import LLM  # noqa: E402
from reco.pipeline import normalize, patient_view  # noqa: E402


def main():
    cases = json.loads((ROOT / "test" / "normalize_cases.json").read_text(encoding="utf-8"))
    symptoms = Graph().run("all_symptoms")
    names = {s["symptom_id"]: s["name"] for s in symptoms}
    llm, failed = LLM(), 0
    for c in cases:
        view = {"age": c.get("age", 70), "sex": c.get("sex", "F"), "chief_complaint": c["text"],
                "managed_conditions": [], "comorbidities": [], "abnormal_checkup": [], "medications": []}
        n = normalize(llm, view, symptoms)
        got = {s["symptom_id"] for s in n["symptoms"]}
        ok_ids = set(c.get("must", [])) | set(c.get("allow", [])) | {x for g in c.get("any", []) for x in g}
        problems = [f"빠짐 {names[i]}" for i in c.get("must", []) if i not in got]
        problems += [f"빠짐 {'/'.join(names[i] for i in g)} 중 하나" for g in c.get("any", []) if not got & set(g)]
        problems += [f"잘못 고름 {names[i]}" for i in sorted(got - ok_ids)] if "must" in c or "any" in c else []
        problems += [f"미대응에 없음 '{u}'" for u in c.get("unmatched", []) if not any(u in x for x in n["unmatched"])]
        problems += [f"맥락에 없음 '{u}'" for u in c.get("context", []) if not any(u in x for x in n["context"])]
        if c.get("care") and n["care_level"] != c["care"]:
            problems.append(f"판정 {n['care_level']} (기대 {c['care']})")
        if "care" not in c and n["care_level"] != "none":
            problems.append(f"판정 {n['care_level']} (기대 none)")
        failed += bool(problems)
        print(f"{'실패' if problems else '통과'}  {c['text']}\n      → {', '.join(names[i] for i in sorted(got)) or '-'}"
              f" | 미대응 {n['unmatched']} | 맥락 {n['context']} | {n['care_level']}"
              + (f"\n      ! {'; '.join(problems)}" if problems else ""))
    # 샘플 사용자: 질환·복용약까지 본 진료 판정이 유지되는지
    visit, either = {"choi", "jung", "yoon", "kang", "oh"}, {"seo"}   # seo: 시력 저하 + 검진 시력 0.4, 어느 쪽도 틀리지 않다
    for f in ("sample_patients.json", "sample_patients_normal.json"):
        for pt in json.loads((ROOT.parent / "data" / f).read_text(encoding="utf-8")):
            n = normalize(llm, patient_view(pt), symptoms)
            want = "visit" if pt["patient_id"] in visit else "none"
            bad = n["care_level"] != want and pt["patient_id"] not in either
            failed += bad
            cases.append(pt)
            print(f"{'실패' if bad else '통과'}  [샘플 {pt['patient_id']}] 판정 {n['care_level']} (기대 {want}) · 사유 증상 {n['care_symptom_ids']} · {n['care_reason']}")
    print(f"\n결과: {len(cases) - failed}/{len(cases)} 통과 (LLM 호출 {llm.calls}회, 캐시 {llm.cached}회)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
