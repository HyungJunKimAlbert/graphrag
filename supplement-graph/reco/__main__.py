"""샘플 사용자 전원에 추천 파이프라인을 돌린다.

    python3 -m reco                       # ../data/sample_patients*.json → out/recommendations.{json,md}
    python3 -m reco --patients <파일>...
    python3 -m reco --only kim normal_01

LLM(OpenAI)을 호출하므로 과금된다. 같은 요청은 out/llm_cache 를 다시 쓴다.
"""
import argparse
import json
import pathlib

from .graph import Graph
from .llm import LLM
from .pipeline import recommend

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT = [ROOT.parent / "data" / "sample_patients.json", ROOT.parent / "data" / "sample_patients_normal.json"]
OUT = ROOT / "out"


def markdown(results):
    lines = ["# 추천 결과", ""]
    for r in results:
        lines += [f"## {r['patient_id']} · {r['age']}세 {'남' if r['sex'] == 'M' else '여'}", "",
                  f"- 주호소: {r['chief_complaint']}",
                  "- 정규화: " + (", ".join(s["name"] for s in r["step1"]["symptoms"]) or "없음")
                  + (f" (대응 못 한 표현: {', '.join(r['step1']['unmatched'])})" if r["step1"]["unmatched"] else ""),
                  f"- 진료 판정: {r['step1']['care_level']} · {r['step1']['care_reason']}"]
        if r.get("message"):
            lines.append(f"- **{r['outcome']}**: {r['message']}")
        if "picks" not in r:
            lines.append("")
            continue
        if r["step1"].get("held_symptoms"):
            lines.append(f"- 진료가 먼저라 추천 근거에서 뺀 증상: {', '.join(r['step1']['held_symptoms'])}")
        s2, s3, s4 = r["step2"], r["step3"], r["step4"]
        lines += [f"- 생애주기: {s2['life_stage']} (겹치는 증상: {', '.join(s2['lifecycle_symptoms_matched']) or '없음'})",
                  f"- 후보: 증상 {s2['symptom_candidates']}개, 복용약 {len(s2['drug_candidates'])}개"
                  f"{' (' + ', '.join(s2['drug_candidates']) + ')' if s2['drug_candidates'] else ''}, "
                  f"관심사 {s2['interest_candidates']}개 "
                  f"→ 4점 {s3['score4']} / 3점 {s3['score3']} / 2점 {s3['score2']} / 1점 {s3['score1']} / 0점 {s3['score0']}",
                  f"- 검증: 조건 {', '.join(s4['condition_codes']) or '없음'} · 약 {', '.join(s4['drug_classes']) or '없음'} "
                  f"→ 통과 {s4['kept']}개, 제외 {s4['excluded']}개", "",
                  "| 순위 | 점수 | 성분 | 유형 | 근거 | 주의 |", "|---|---|---|---|---|---|"]
        picked = {p["ingredient_id"] for p in r["picks"]}
        for c in r["top10"]:
            basis = "; ".join(c["paths"][:2] + [f"{d['drug']} 복용 → {d['effect']}" for d in c["drug_basis"] if d["grade"] == "A"]) \
                or "관심사: " + ", ".join(c["interests"])
            mark = "**" if c["ingredient_id"] in picked else ""
            lines.append(f"| {c['rank']} | {c['score']}점 {'+'.join(c['routes'])} | {mark}{c['name']}{mark} | {c['kind']} | {basis} | "
                         f"{'; '.join(c['cautions'])[:80]} |")
        lines += ["", "**함께 권할 건기식 (참고용 제안)**", ""]
        for p in r["picks"]:
            k = p["card"]
            lines.append(f"- **{k['title']}{' ' + k['dose'] if k['dose'] else ''}** · {k['headline']} · [{k['tag']}]"
                         + "".join(f" · {d}" for d in k["drug"])
                         + "".join(f" · 주의: {x}" for x in k["cautions"]))
        lines += ["", f"**선정 3개 상세** — {r['summary']}", ""]
        for p in r["picks"]:
            lines.append(f"- **{p['name']}** ({p['score']}점 {'+'.join(p['routes'])}, 섭취량: {p['intake'] or '원문 없음'}): {p['reason']}")
            lines.append(f"  - 출처: {'; '.join(p['references'])}")
            for b in p["drug_basis"]:
                lines.append(f"  - 복용약 근거({b['grade']}): {b['drug']} — \"{b['quote']}\" ({b['url']})")
            d = p["detail"]
            for label, key in (("일일섭취량", "daily_intake"), ("원재료명", "raw_material"),
                               ("섭취 시 주의사항", "precautions"), ("주된 기능성", "main_function")):
                lines.append(f"  - {label}: {' / '.join((d[key] or '원문 없음').split(chr(10)))}")
            for t in p["take_together"]:
                lines.append(f"  - 함께 섭취: {t['name']} — {t['effect']} ({t['source']})")
            for a in p["conflicts_with_picks"]:
                lines.append(f"  - 주의: {a['a_name']} ↔ {a['b_name']} — {a['effect']}")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--patients", nargs="*", default=[str(p) for p in DEFAULT])
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    patients = [p for f in args.patients for p in json.loads(pathlib.Path(f).read_text(encoding="utf-8"))]
    if args.only:
        patients = [p for p in patients if p["patient_id"] in args.only]

    graph, llm = Graph(), LLM()
    symptom_list = graph.run("all_symptoms")
    results = []
    for p in patients:
        r = recommend(p, graph, llm, symptom_list)
        results.append(r)
        picks = ", ".join(x["name"] for x in r.get("picks", [])) or r.get("message", "")
        print(f"{p['patient_id']:<10} {r['outcome']:<10} {picks}")
    OUT.mkdir(exist_ok=True)
    (OUT / "recommendations.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "recommendations.md").write_text(markdown(results), encoding="utf-8")
    print(f"\nLLM 호출 {llm.calls}회 (캐시 재사용 {llm.cached}회) → {OUT / 'recommendations.md'}")


if __name__ == "__main__":
    main()
