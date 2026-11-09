"""주호소 정규화 평가(complaint_testset.json 472건). python -m eval.normalize_eval export|score --tag <tag>

정답 이름은 엔진 증상 마스터의 표준 이름이다. 우리 출력은 분류표 키워드(symptoms)와 분류표 밖 증상 텍스트(unmatched)이므로
표준 이름이 symptoms에 있거나, 분류표 밖 증상 텍스트와 같거나(동의어 포함) 그 안에 들어 있으면 맞힌 것으로 본다.
"""
import argparse
import json
import sys
from pathlib import Path

from rxrag import config, normalize, taxonomy
from concurrent.futures import ThreadPoolExecutor

from rxrag import llm
from rxrag.llm import Meter, MissingResponse, SessionLLM

TESTSET = config.GRAPHDB / "data" / "eval" / "complaint_testset.json"
OUT = config.ROOT / "out" / "eval"
NO_PROFILE = "정보 없음"


def load_testset(path=TESTSET):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["cases"], {alias: main for main, aliases in data["equivalents"].items() for alias in aliases}


def prompt_for(case, cats):
    return normalize.prompt(case["utterance"], case["profile"] or NO_PROFILE, cats)


def _found(name, picked, outside, canon):
    target = canon.get(name, name)
    return target in picked or any(target == canon.get(t, t) or target in t or name in t for t in outside)


def score_case(case, flat, canon):
    picked = {canon.get(s, s) for s in flat["symptoms"]}
    outside = [u["text"] for u in flat["unmatched"] if u["kind"] == "분류표 밖 증상"]
    observe = bool(case.get("observe"))
    required = [g for g in case["required"] if g]
    return {
        "id": case["id"], "group": case.get("group", ""), "observe": observe,
        "required_ok": None if observe or not required else all(any(_found(n, picked, outside, canon) for n in g)
                                                                  for g in required),
        "forbid_hit": sorted(n for n in case["forbid"] if _found(n, picked, outside, canon)),
        "red_flag_expected": case["red_flag"], "red_flag_got": bool(flat["red_flag"]),
        "no_match_expected": bool(case["no_match"]),
        "empty": not (flat["symptoms"] or flat["categories"] or outside),
        "symptoms": flat["symptoms"], "categories": flat["categories"], "outside": outside,
    }


def _rate(a, b):
    return round(a / b, 4) if b else None


def metrics(scored):
    live = [s for s in scored if not s["observe"]]
    req = [s for s in live if s["required_ok"] is not None]
    met = sum(s["required_ok"] for s in req)
    tp = sum(s["red_flag_expected"] is True and s["red_flag_got"] for s in live)
    fp = sum(s["red_flag_expected"] is False and s["red_flag_got"] for s in live)
    fn = sum(s["red_flag_expected"] is True and not s["red_flag_got"] for s in live)
    nm = [s for s in live if s["no_match_expected"]]
    return {
        "cases": len(scored), "observed": len(scored) - len(live),
        "required": {"cases": len(req), "met": met, "rate": _rate(met, len(req))},
        "forbid_violations": sum(bool(s["forbid_hit"]) for s in live),
        "red_flag": {"tp": tp, "fp": fp, "fn": fn, "recall": _rate(tp, tp + fn), "precision": _rate(tp, tp + fp)},
        "no_match": {"cases": len(nm), "correct": sum(s["empty"] for s in nm), "accuracy": _rate(sum(s["empty"] for s in nm), len(nm))},
    }


def export(cases, cats, root=config.SESSION_DIR):
    client = SessionLLM(root, mode="export")
    schema = normalize.schema(cats)
    for c in cases:
        try:
            client.complete_json(normalize.TASK, *prompt_for(c, cats), schema)
        except MissingResponse:
            pass
    root = Path(root)
    return sum(1 for p in (root / "requests" / normalize.TASK).glob("*.json")
               if not (root / "responses" / normalize.TASK / p.name).exists())


def score(cases, cats, canon, root=config.SESSION_DIR, client=None, workers=1):
    """(채점, 지표, 원응답, 호출별 시간·토큰). client가 없으면 세션 응답 파일을 읽는다. 운영 GPT는 workers로 병렬 호출."""
    meter = Meter(client or SessionLLM(root, mode="run"))
    schema = normalize.schema(cats)
    ask = lambda c: meter.complete_json(normalize.TASK, *prompt_for(c, cats), schema)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        responses = list(pool.map(ask, cases))  # map은 입력 순서를 지킨다
    scored = [score_case(c, normalize.flatten(r, cats), canon) for c, r in zip(cases, responses)]
    raw = [{"id": c["id"], "response": r} for c, r in zip(cases, responses)]
    return scored, metrics(scored), raw, meter.calls


def usage_summary(calls):
    """호출 수, 시간 평균·p95(초), 토큰 합계. 세션 응답은 토큰이 None이다."""
    lat = sorted(c["latency_ms"] for c in calls)
    tok = lambda k: sum(c[k] for c in calls) if calls and all(c.get(k) is not None for c in calls) else None
    return {"calls": len(calls), "latency_avg_s": round(sum(lat) / len(lat) / 1000, 2) if lat else None,
            "latency_p95_s": round(lat[int(0.95 * (len(lat) - 1))] / 1000, 2) if lat else None,
            "input_tokens": tok("input"), "cached_tokens": tok("cached"), "output_tokens": tok("output"),
            "reasoning_tokens": tok("reasoning"), "cache_write_tokens": tok("cache_write")}


def write_xlsx(path, cases, scored, m):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "정규화 요약"
    ws.append(("항목", "값"))
    for key, value in m.items():
        ws.append((key, json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value))
    detail = wb.create_sheet("정규화 전체")
    detail.append(("id", "그룹", "주호소", "필수 충족", "금지 위반", "위험 신호 정답", "위험 신호 응답", "빈값 정답", "증상",
                   "분류", "분류표 밖"))
    for c, s in zip(cases, scored):
        detail.append((s["id"], s["group"], c["utterance"], str(s["required_ok"]), ", ".join(s["forbid_hit"]),
                       str(s["red_flag_expected"]), str(s["red_flag_got"]), str(s["no_match_expected"]),
                       ", ".join(s["symptoms"]), ", ".join(s["categories"]), ", ".join(s["outside"])))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="eval.normalize_eval")
    parser.add_argument("cmd", choices=["export", "score"])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--session", type=Path, default=config.SESSION_DIR)
    parser.add_argument("--testset", type=Path, default=TESTSET)
    parser.add_argument("--llm", choices=["session", "openai", "anthropic"], default="session")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--effort", choices=["low", "medium", "high"], default="low")  # GPT·Claude effort
    args = parser.parse_args(argv)
    cases, canon = load_testset(args.testset)
    cats = taxonomy.load()
    if args.cmd == "export":
        print(f"남은 정규화 요청 {export(cases, cats, args.session)}개")
        return
    try:
        client = {"openai": lambda: llm.OpenAILLM(effort=args.effort),  # 과금: GPT
                  "anthropic": lambda: llm.AnthropicLLM(effort=args.effort),  # 과금: Claude Haiku
                  }.get(args.llm, lambda: None)()
        scored, m, raw, calls = score(cases, cats, canon, args.session, client, args.workers if client else 1)
    except MissingResponse as e:
        sys.exit(f"세션 응답이 없습니다({e.task}/{e.key}). export 후 응답을 작성하세요.")
    out = OUT / f"normalize-{args.tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"metrics": m, "usage": usage_summary(calls), "llm": args.llm,
                              "effort": args.effort if client else None, "cases": scored},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / f"normalize-{args.tag}-raw.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in raw),
                                                        encoding="utf-8")
    write_xlsx(OUT / f"normalize-{args.tag}.xlsx", cases, scored, m)
    print(json.dumps(m, ensure_ascii=False))


if __name__ == "__main__":
    main()
