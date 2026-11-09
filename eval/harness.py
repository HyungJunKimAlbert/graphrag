"""추천 평가 하네스. python -m eval.harness <cmd> --tag <tag>

export → (세션이 응답 작성) → export → (응답) → run → g1 → engine → g2-export → (세션 블라인드 채점) → g2 → xlsx
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from rxrag import candidates, config, contract, graph, legacy
from rxrag.llm import MissingResponse, SessionLLM
from rxrag.recommend import recommend

ROOT = Path(__file__).resolve().parents[1]
PATIENTS = Path(__file__).resolve().parent / "patients.json"
OUT = ROOT / "out" / "eval"
RESULT_KEYS = ["status", "message", "recommendations", "excluded", "warnings", "selector", "trace"]
JUDGE_TASK = "judge"
PASS_RATIO = 0.8

JUDGE_SYSTEM = """당신은 약사입니다. 같은 환자에게 두 시스템(A, B)이 고른 영양성분 3개를 비교해 어느 쪽이 나은지 채점합니다.
어느 쪽이 어떤 시스템인지는 알려 주지 않습니다. 순서나 문장 길이에 끌리지 말고 내용으로만 판단합니다.

[기준] 각 1~5점
- fit: 근거 적합도. 성분마다 근거가 환자 상황과 맞는가
- coverage: 환자 문제(증상·검진·복용약·질환) 커버리지
- spread: 3개가 서로 다른 문제를 나눠 맡는가(한 문제에 몰리지 않는가)
- caution: 주의 문구가 있는 성분을 대안이 있는데도 앞에 두지 않았는가

[판정]
- winner: 전체적으로 나은 쪽 "A" 또는 "B", 차이가 작으면 "tie"
- clearly_worse: 환자에게 내놓기 어려울 만큼 확실히 못한 쪽이 있으면 "A" 또는 "B", 없으면 null
- why: 판정 이유 한두 문장"""

SCORE = {"type": "object", "additionalProperties": False, "required": ["fit", "coverage", "spread", "caution"],
         "properties": {k: {"type": "integer", "enum": [1, 2, 3, 4, 5]} for k in ["fit", "coverage", "spread", "caution"]}}
JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["winner", "clearly_worse", "scores", "why"],
    "properties": {"winner": {"type": "string", "enum": ["A", "B", "tie"]},
                   "clearly_worse": {"type": ["string", "null"], "enum": ["A", "B", None]},
                   "scores": {"type": "object", "additionalProperties": False, "required": ["A", "B"],
                              "properties": {"A": SCORE, "B": SCORE}},
                   "why": {"type": "string"}},
}


def load_patients(path=PATIENTS):
    return json.loads(Path(path).read_text(encoding="utf-8"))["patients"]


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def pending(root):
    root = Path(root)
    return sorted(p for p in (root / "requests").glob("*/*.json")
                  if not (root / "responses" / p.parent.name / p.name).exists())


def export(patients, root=config.SESSION_DIR, db=None):
    """응답이 있는 단계는 지나가고, 없는 첫 요청을 파일로 쓴다. 남은(응답 없는) 요청 수를 돌려준다."""
    db = db or graph.connect()
    client = SessionLLM(root, mode="export")
    for p in patients:
        try:
            recommend(p["patient"], client, db)
        except MissingResponse:
            pass
    return len(pending(root))


def run(patients, root=config.SESSION_DIR, out_path=None, db=None, client=None):
    db = db or graph.connect()
    client = client or SessionLLM(root, mode="run")
    results = [{"id": p["id"], "result": recommend(p["patient"], client, db)} for p in patients]
    if out_path:
        write_jsonl(out_path, results)
    return results


def facts_of(db, patient, normalized):
    """trace의 정규화 결과로 풀을 다시 만들어 성분별 사실 ID 집합을 얻는다(결과 파일과 독립적인 대조)."""
    clean, _ = contract.validate(patient)
    ctx = legacy.build_context(db, clean, normalized)
    return {c["ingredient_id"]: {f["fact_id"] for f in c["facts"]} for c in candidates.pool(db, ctx)}


def g1(patients, results, db=None, check_facts=True):
    """위반 문구 목록. 빈 리스트면 통과."""
    by_id = {p["id"]: p for p in patients}
    problems = []
    if check_facts:
        db = db or graph.connect()
    for row in results:
        p, r = by_id[row["id"]], row["result"]
        tag = row["id"]
        if list(r) != RESULT_KEYS:
            problems.append(f"{tag}: 결과 키가 스키마와 다릅니다 {list(r)}")
        if p.get("expect_status") and r["status"] != p["expect_status"]:
            problems.append(f"{tag}: status {r['status']} ≠ 기대 {p['expect_status']}")
        recs = r.get("recommendations", [])
        banned = set(p["must_not"]) & {x["ingredient_id"] for x in recs}
        if banned:
            problems.append(f"{tag}: 금지 성분 추천 {sorted(banned)}")
        if not recs:
            continue
        pool_facts = facts_of(db, p["patient"], r["trace"].get("normalized")) if check_facts else None
        for x in recs:
            if not x.get("evidence"):
                problems.append(f"{tag}: {x['ingredient_id']} 근거 없음")
            if pool_facts is None:
                continue
            if x["ingredient_id"] not in pool_facts:
                problems.append(f"{tag}: {x['ingredient_id']} 후보 풀 밖 성분")
                continue
            foreign = [e["fact_id"] for e in x["evidence"] if e["fact_id"] not in pool_facts[x["ingredient_id"]]]
            if foreign:
                problems.append(f"{tag}: {x['ingredient_id']} 후보에 없는 fact_id {foreign}")
    return problems


def engine(patients, results, out_path=None, db=None):
    """같은 정규화 결과를 엔진에 넣는다(선택 단계 차이만 비교)."""
    db = db or graph.connect()
    by_id = {r["id"]: r["result"] for r in results}
    rows = []
    for p in patients:
        r = by_id[p["id"]]
        clean, _ = contract.validate(p["patient"])
        if clean is None or r["status"] in ("stopped", "invalid_input"):
            rows.append({"id": p["id"], "engine": None})
            continue
        rows.append({"id": p["id"], "engine": legacy.engine_recommend(db, clean, r["trace"].get("normalized"))})
    if out_path:
        write_jsonl(out_path, rows)
    return rows


def new_side(pid):
    return "A" if int(hashlib.sha256(pid.encode()).hexdigest(), 16) % 2 == 0 else "B"


def unblind(verdict, new):
    result = "tie" if verdict["winner"] == "tie" else "win" if verdict["winner"] == new else "loss"
    return {"new_side_result": result, "new_clearly_worse": verdict.get("clearly_worse") == new}


def describe_patient(patient):
    sex = {"M": "남성", "F": "여성"}.get(contract.validate(patient)[0]["sex"])
    parts = [f"{sex}, {int(float(patient['age'])) // 10 * 10}대"]
    for key, label in (("complaint", "주호소"), ("symptoms", "증상"), ("conditions", "질환"), ("exam", "검진"),
                       ("drugs", "복용약"), ("pregnant", "임신"), ("lactating", "수유")):
        if patient.get(key) not in (None, [], {}, ""):
            value = patient[key]
            if key == "drugs":
                value = ", ".join(d["name"] if isinstance(d, dict) else d for d in value)
            parts.append(f"{label}: {value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}")
    return "\n".join(f"- {x}" for x in parts)


def side_new(result):
    return [{"name": x["name"], "reason": x["reason"], "evidence": [e["text"] for e in x["evidence"]],
             "cautions": [c["text"] for c in x["cautions"]]} for x in result["recommendations"]]


def side_engine(result):
    return [{"name": x["name"], "reason": x.get("tier", ""), "evidence": x.get("paths", []),
             "cautions": [c["text"] for c in x.get("cautions", [])]} for x in result["recommendations"]]


def render_side(items):
    lines = []
    for i, x in enumerate(items, 1):
        lines.append(f"{i}. {x['name']} — {x['reason']}\n   근거: {' / '.join(x['evidence']) or '-'}"
                     f"\n   주의: {' / '.join(c for c in x['cautions'] if c) or '-'}")
    return "\n".join(lines) or "(추천 없음)"


def judge_requests(patients, results, engine_rows):
    by_res = {r["id"]: r["result"] for r in results}
    by_eng = {r["id"]: r["engine"] for r in engine_rows}
    out = []
    for p in patients:
        new, old = by_res[p["id"]], by_eng[p["id"]]
        if old is None or not new["recommendations"]:
            continue
        sides = {new_side(p["id"]): side_new(new)}
        sides["B" if "A" in sides else "A"] = side_engine(old)
        user = (f"[환자]\n{describe_patient(p['patient'])}\n\n[A]\n{render_side(sides['A'])}\n\n"
                f"[B]\n{render_side(sides['B'])}")
        out.append((p["id"], user))
    return out


def g2_export(patients, results, engine_rows, root=config.SESSION_DIR):
    client = SessionLLM(root, mode="export")
    for _, user in judge_requests(patients, results, engine_rows):
        try:
            client.complete_json(JUDGE_TASK, JUDGE_SYSTEM, user, JUDGE_SCHEMA)
        except MissingResponse:
            pass
    return len([p for p in pending(root) if p.parent.name == JUDGE_TASK])


def g2_verdicts(patients, results, engine_rows, root=config.SESSION_DIR):
    client = SessionLLM(root, mode="run")
    rows = []
    for pid, user in judge_requests(patients, results, engine_rows):
        verdict = client.complete_json(JUDGE_TASK, JUDGE_SYSTEM, user, JUDGE_SCHEMA)
        rows.append({"id": pid, "new_side": new_side(pid), "verdict": verdict, **unblind(verdict, new_side(pid))})
    return rows


def g2_aggregate(rows):
    total = len(rows)
    count = {k: sum(r["new_side_result"] == k for r in rows) for k in ("win", "tie", "loss")}
    worse = sum(bool(r["new_clearly_worse"]) for r in rows)
    ratio = round((count["win"] + count["tie"]) / total, 4) if total else 0.0
    return {**count, "total": total, "ratio": ratio, "new_clearly_worse": worse,
            "pass": total > 0 and ratio >= PASS_RATIO and worse == 0}


def same_set(new, old):
    return {x["ingredient_id"] for x in new["recommendations"]} == {x["ingredient_id"] for x in old["recommendations"]}


def xlsx(path, patients, results, engine_rows, problems, verdicts):
    from openpyxl import Workbook
    by_res = {r["id"]: r["result"] for r in results}
    by_eng = {r["id"]: r["engine"] for r in engine_rows}
    by_ver = {v["id"]: v for v in verdicts}
    agg = g2_aggregate(verdicts)
    wb = Workbook()
    ws = wb.active
    ws.title = "요약"
    for row in [("항목", "값"), ("환자 수", len(patients)), ("G1 위반", len(problems)),
                ("G1 통과", "예" if not problems else "아니오"), ("G2 채점 수", agg["total"]), ("G2 승", agg["win"]),
                ("G2 무", agg["tie"]), ("G2 패", agg["loss"]), ("G2 승+무 비율", agg["ratio"]),
                ("G2 새 시스템 확실히 못함", agg["new_clearly_worse"]), ("G2 통과", "예" if agg["pass"] else "아니오")]:
        ws.append(row)
    for p in problems:
        ws.append(("G1 위반 내용", p))

    def names(r):
        return ", ".join(x["name"] for x in r["recommendations"]) if r else ""

    def evidence(r):
        return "\n".join(f"{x['name']}: " + " / ".join(e["text"] for e in x["evidence"]) for x in r["recommendations"])

    header = ("환자", "그룹", "메모", "환자 요약", "상태", "엔진 3개", "새 3개", "새 근거", "판정", "채점 이유")
    diff, full = wb.create_sheet("불일치"), wb.create_sheet("전체")
    diff.append(header)
    full.append(header)
    for p in patients:
        new, old, v = by_res[p["id"]], by_eng.get(p["id"]), by_ver.get(p["id"])
        row = (p["id"], p["group"], p["note"], describe_patient(p["patient"]), new["status"], names(old), names(new),
               evidence(new), v["new_side_result"] if v else "", v["verdict"]["why"] if v else "")
        full.append(row)
        if old is not None and not same_set(new, old):
            diff.append(row)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="eval.harness")
    parser.add_argument("cmd", choices=["export", "run", "g1", "engine", "g2-export", "g2", "xlsx"])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--patients", type=Path, default=PATIENTS)
    parser.add_argument("--session", type=Path, default=config.SESSION_DIR)
    parser.add_argument("--llm", choices=["session", "openai"], default="session")
    args = parser.parse_args(argv)
    patients = load_patients(args.patients)
    res_path, eng_path = OUT / f"{args.tag}.jsonl", OUT / f"{args.tag}-engine.jsonl"
    g2_path = OUT / f"{args.tag}-g2.json"

    if args.cmd == "export":
        print(f"남은 요청 {export(patients, args.session)}개")
    elif args.cmd == "run":
        client = None
        if args.llm == "openai":
            from rxrag.llm import OpenAILLM
            client = OpenAILLM()
        try:
            run(patients, args.session, res_path, client=client)
        except MissingResponse as e:
            sys.exit(f"세션 응답이 없습니다({e.task}/{e.key}). export부터 다시 하세요.")
        print(f"저장: {res_path}")
    elif args.cmd == "g1":
        problems = g1(patients, read_jsonl(res_path))
        print("\n".join(problems) or "G1 통과: 위반 0건")
        sys.exit(1 if problems else 0)
    elif args.cmd == "engine":
        engine(patients, read_jsonl(res_path), eng_path)
        print(f"저장: {eng_path}")
    elif args.cmd == "g2-export":
        print(f"남은 채점 요청 {g2_export(patients, read_jsonl(res_path), read_jsonl(eng_path), args.session)}개")
    elif args.cmd == "g2":
        verdicts = g2_verdicts(patients, read_jsonl(res_path), read_jsonl(eng_path), args.session)
        agg = g2_aggregate(verdicts)
        g2_path.write_text(json.dumps({"aggregate": agg, "verdicts": verdicts}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
        print(json.dumps(agg, ensure_ascii=False))
        sys.exit(0 if agg["pass"] else 1)
    elif args.cmd == "xlsx":
        results = read_jsonl(res_path)
        verdicts = json.loads(g2_path.read_text(encoding="utf-8"))["verdicts"] if g2_path.exists() else []
        engine_rows = read_jsonl(eng_path) if eng_path.exists() else []
        xlsx(OUT / f"{args.tag}.xlsx", patients, results, engine_rows, g1(patients, results), verdicts)
        print(f"저장: {OUT / f'{args.tag}.xlsx'}")


if __name__ == "__main__":
    main()
