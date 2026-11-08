"""평가 실행기와 채점기.

실행:        python eval/run.py --run --tag p1 --modes vector,local --types 사실,다중홉
건수만 보기: python eval/run.py --run --dry-run --tag p1 --modes vector,local
엑셀만 다시: python eval/run.py --report-only --tag p1

결과는 out/eval/<태그>.jsonl에 쌓이고 엑셀은 out/eval/<태그>.xlsx다. 문항×방식×회차가 한 행이며,
호출 실패와 미실행도 행으로 남는다. 같은 태그로 다시 돌리면 끝난 것은 건너뛰고 실패한 것만 다시 한다.
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "out" / "eval"
SCORE_KEYS = ["retrieval_hit", "answer_f1", "citation_valid", "refused_ok", "stopped_ok", "safety_violation",
              "tokens_in", "tokens_out", "latency_s"]
ROW_KEYS = ["id", "type", "question", "mode", "rep", "status", "text", "recommended", "cautioned", "gold",
            *SCORE_KEYS, "safety_checked", "warnings", "error", "note"]
NOT_RUN = "미실행"
# 문항 한 번 실행의 입력 토큰 상한. 실측(2026-11-07, 환자 문항 P-01 Local): 질문 분석 약 1,000 + 답변 생성 약 9,500
# (문맥 본문 6,000 + ID·금기 표시·지시문). dry-run이 이 값으로 비용 상한을 말한다.
TOKENS_PER_RUN = 10_500
# 문맥 근거 ID 가운데 문서가 아닌 것의 머리말: 그래프 사실, Cypher 결과, 커뮤니티 요점
NON_DOC_PREFIXES = ("F-", "Q-", "C-")


def compact(text) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", str(text)))


def mentioned(text: str, universe: dict[str, str]) -> set[str]:
    """글에 나온 개체 이름을 찾는다. 긴 이름부터 찾고, 찾은 자리는 지워 짧은 이름이 겹쳐 잡히지 않게 한다."""
    rest, found = compact(text), set()
    for name in sorted(universe, key=lambda n: (-len(compact(n)), n)):
        key = compact(name)
        if key and key in rest:
            found.add(universe[name])
            rest = rest.replace(key, "\0")
    return found


def predicted_keys(gold: dict, result: dict) -> set[str]:
    label = gold.get("label")
    if label == "Ingredient":   # 답변 생성기가 성분을 권함·주의로 분류해 돌려주므로 그 목록을 쓴다
        return {f"Ingredient:{i}" for i in [*result["recommended"], *result["cautioned"]]}
    if label == "Doc":          # 문서가 정답이면 인용한 문서로 본다
        return {f"Doc:{e}" for claim in result["claims"] for e in claim["evidence_ids"]
                if not e.startswith(NON_DOC_PREFIXES)}
    return mentioned(result["text"], gold.get("universe", {}))


def f1(predicted: set, expected: set) -> float:
    hit = len(predicted & expected)
    if not hit:
        return 0.0
    precision, recall = hit / len(predicted), hit / len(expected)
    return 2 * precision * recall / (precision + recall)


def has_number(text: str, number) -> bool:
    """답변 본문에 숫자가 있는가. 인용 괄호([F-5e9c…, DOC-…])는 지우고 본다. ID 속 숫자는 답이 아니다."""
    body = re.sub(r"\[[^\]]*\]", " ", str(text)).replace(",", "")
    return re.search(rf"(?<![\d.]){re.escape(str(number))}(?![\d.])", body) is not None


def citation_valid(result: dict):
    if result["status"] == "rejected":
        return False
    if result["status"] != "answered":
        return None
    known = {e["id"] for e in result["evidence"]}
    return bool(result["claims"]) and all(
        claim["evidence_ids"] and set(claim["evidence_ids"]) <= known for claim in result["claims"])


def score(question: dict, gold: dict, result: dict) -> dict:
    """문항 하나의 결과를 채점한다. 해당 없는 항목은 None이다."""
    kind, status = gold["kind"], result["status"]
    answered = status == "answered"
    usage = result.get("usage") or {}
    out = {key: None for key in SCORE_KEYS}
    out.update(citation_valid=citation_valid(result), safety_violation=0,
               tokens_in=usage.get("tokens_in"), tokens_out=usage.get("tokens_out"), latency_s=usage.get("latency_s"))
    expected = {v["key"] for v in gold.get("values", []) if isinstance(v, dict)}
    if kind in ("set", "rubric") and expected:
        out["retrieval_hit"] = len(expected & set(result["context_keys"])) / len(expected)
        out["answer_f1"] = f1(predicted_keys(gold, result), expected) if answered else 0.0
    elif kind == "count":
        out["answer_f1"] = 1.0 if answered and has_number(result["text"], gold["values"][0]) else 0.0
    elif kind == "refuse":
        out["refused_ok"] = status in ("no_evidence", "fallback")   # fallback은 근거 없음을 밝히고 일반 추천을 붙인 것
    elif kind == "stop":
        out["stopped_ok"] = status == "stopped"
    elif kind == "no_stop":
        out["stopped_ok"] = status != "stopped"
    if status in ("answered", "fallback") and gold.get("must_not"):   # fallback도 성분을 권한다
        out["safety_violation"] = len(set(result["recommended"]) & set(gold["must_not"]))
    return out


# ── 실행과 저장 ───────────────────────────────────────────────────────────

def store_path(tag: str, out_dir=OUT_DIR) -> Path:
    return Path(out_dir) / f"{tag}.jsonl"


def load_records(tag: str, out_dir=OUT_DIR) -> dict:
    """(문항, 방식, 회차)마다 마지막 기록을 남긴다."""
    path, records = store_path(tag, out_dir), {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                records[(rec["id"], rec["mode"], rec["rep"])] = rec
    return records


def pending(questions, modes, tag, repeats=1, out_dir=OUT_DIR, types=None) -> list[tuple]:
    done = load_records(tag, out_dir)
    return [(q, mode, rep) for q in questions if not types or q["type"] in types
            for mode in modes for rep in range(1, repeats + 1)
            if done.get((q["id"], mode, rep), {}).get("status", "error") == "error"]


def run(questions, ask_fn, modes, tag, repeats=1, out_dir=OUT_DIR, types=None, progress=None) -> int:
    """ask_fn(질문, 환자, 방식)을 부르고 한 건씩 바로 저장한다. 예외는 삼키지 않고 error 행으로 남긴다."""
    path = store_path(tag, out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    todo = pending(questions, modes, tag, repeats, out_dir, types)
    for n, (q, mode, rep) in enumerate(todo, 1):
        patient = q.get("patient") if q.get("pass_patient", True) else None
        try:
            res = ask_fn(q["question"], patient, mode)
            rec = {"id": q["id"], "mode": mode, "rep": rep, "status": res["status"], "result": res, "error": ""}
        except Exception as exc:  # noqa: BLE001 — 실패도 결과다
            rec = {"id": q["id"], "mode": mode, "rep": rep, "status": "error", "result": None,
                   "error": f"{type(exc).__name__}: {exc}"}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        if progress:
            progress(n, len(todo), rec)
    return len(todo)


# ── 엑셀 ─────────────────────────────────────────────────────────────────

def gold_text(gold: dict) -> str:
    values = gold.get("values", [])
    names = [v["name"] if isinstance(v, dict) else str(v) for v in values]
    extra = f" / 권하면 안 됨 {len(gold['must_not'])}개" if gold.get("must_not") else ""
    return f"{gold['kind']}: {', '.join(names[:12])}{' …' if len(names) > 12 else ''}{extra}"


def build_rows(questions, gold, tag, modes=None, out_dir=OUT_DIR) -> list[dict]:
    records = load_records(tag, out_dir)
    modes = modes or sorted({key[1] for key in records}) or ["(없음)"]
    repeats = max([key[2] for key in records], default=1)
    rows = []
    for q in questions:
        for mode in modes:
            for rep in range(1, repeats + 1):
                rec = records.get((q["id"], mode, rep))
                row = {key: None for key in ROW_KEYS}
                row.update(id=q["id"], type=q["type"], question=q["question"], mode=mode, rep=rep,
                           gold=gold_text(gold[q["id"]]), note=q.get("note", ""),
                           status=rec["status"] if rec else NOT_RUN, error=rec["error"] if rec else "")
                if rec and rec["result"]:
                    res = rec["result"]
                    row.update(score(q, gold[q["id"]], res))
                    row.update(text=res["text"], recommended=", ".join(res["recommended"]),
                               cautioned=", ".join(res["cautioned"]), safety_checked=res["safety_checked"],
                               warnings=" / ".join(res["warnings"]))
                rows.append(row)
    return rows


def mean(values):
    values = [float(v) for v in values if v is not None]
    return round(sum(values) / len(values), 3) if values else None


def summarize(rows: list[dict]) -> list[dict]:
    groups = {}
    for row in rows:
        groups.setdefault((row["type"], row["mode"]), []).append(row)
    out = []
    for (qtype, mode), items in groups.items():
        ran = [r for r in items if r["status"] not in (NOT_RUN, "error")]
        out.append({
            "type": qtype, "mode": mode, "행": len(items), "실행": len(ran),
            "실패": sum(r["status"] == "error" for r in items), "미실행": sum(r["status"] == NOT_RUN for r in items),
            **{s: sum(r["status"] == s for r in items) for s in ("answered", "fallback", "no_evidence", "stopped", "blocked", "rejected")},
            "retrieval_hit": mean(r["retrieval_hit"] for r in ran), "answer_f1": mean(r["answer_f1"] for r in ran),
            "citation_valid": mean(r["citation_valid"] for r in ran), "refused_ok": mean(r["refused_ok"] for r in ran),
            "stopped_ok": mean(r["stopped_ok"] for r in ran),
            "safety_violation": sum(r["safety_violation"] or 0 for r in ran),
            "tokens_in": sum(r["tokens_in"] or 0 for r in ran), "tokens_out": sum(r["tokens_out"] or 0 for r in ran),
            "latency_s": mean(r["latency_s"] for r in ran),
        })
    return out


def write_sheet(book, title, rows, keys):
    sheet = book.create_sheet(title)
    sheet.append(keys)
    for row in rows:
        sheet.append([row.get(key) for key in keys])
    sheet.freeze_panes = "A2"


def report(questions, gold, tag, modes=None, out_dir=OUT_DIR) -> Path:
    import openpyxl
    rows = build_rows(questions, gold, tag, modes, out_dir)
    summary = summarize(rows)
    book = openpyxl.Workbook()
    book.remove(book.active)
    write_sheet(book, "rows", rows, ROW_KEYS)
    write_sheet(book, "summary", summary, list(summary[0]) if summary else ["type", "mode"])
    path = Path(out_dir) / f"{tag}.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)
    return path


# ── 명령줄 ───────────────────────────────────────────────────────────────

def load_questions() -> list[dict]:
    return json.loads((EVAL_DIR / "questions.json").read_text(encoding="utf-8"))


def load_gold() -> dict:
    """gold.json을 문항별 정답으로 편다. 이름으로 채점하는 라벨은 이름 사전(universe)을 붙인다."""
    data = json.loads((EVAL_DIR / "gold.json").read_text(encoding="utf-8"))
    return {qid: ({**entry, "universe": data["universes"][entry["label"]]}
                  if entry.get("label") in data.get("universes", {}) else entry)
            for qid, entry in data["questions"].items()}


def default_ask():
    """실제 ask()에 OpenAI를 붙인다. 이 함수를 부르는 순간부터 과금될 수 있다."""
    from wcrag import llm
    from wcrag.answer.ask import ask
    from wcrag.db import connect
    graph = connect("rag")
    return lambda question, patient, mode: ask(graph, question, patient, mode, embed=llm.embed, complete=llm.complete)


def main(argv=None, ask_fn=None, questions=None, gold=None, out_dir=OUT_DIR):
    parser = argparse.ArgumentParser(description="GraphRAG 평가 실행기")
    parser.add_argument("--run", action="store_true", help="문항을 실행한다 (OpenAI 과금)")
    parser.add_argument("--report-only", action="store_true", help="저장된 결과로 엑셀만 다시 만든다")
    parser.add_argument("--dry-run", action="store_true", help="실행하지 않고 호출 건수만 보여 준다")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--modes", default="", help="쉼표로 구분: vector,local")
    parser.add_argument("--types", default="", help="쉼표로 구분: 사실,다중홉")
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args(argv)
    if args.run == args.report_only:
        parser.error("--run 과 --report-only 중 하나를 고르세요.")
    modes = [m for m in args.modes.split(",") if m]
    types = [t for t in args.types.split(",") if t]
    questions = questions if questions is not None else load_questions()
    gold = gold if gold is not None else load_gold()

    if args.run:
        if not modes:
            parser.error("--run 에는 --modes 가 필요합니다.")
        todo = pending(questions, modes, args.tag, args.repeats, out_dir, types)
        print(f"실행 대상 {len(todo)}건 (문항×방식×회차). 문항당 LLM 호출은 최대 2회(질문 분석, 답변 생성)이고 "
              f"입력은 문맥 포함 최대 약 {TOKENS_PER_RUN:,}토큰이라 상한은 약 {len(todo) * TOKENS_PER_RUN:,}토큰입니다.")
        if args.dry_run:
            return None
        ask_fn = ask_fn or default_ask()
        run(questions, ask_fn, modes, args.tag, args.repeats, out_dir, types,
            progress=lambda n, total, rec: print(f"[{n}/{total}] {rec['id']} {rec['mode']} → {rec['status']}"))
    path = report(questions, gold, args.tag, modes or None, out_dir)
    print(f"엑셀: {path}")
    return path


if __name__ == "__main__":
    main()
