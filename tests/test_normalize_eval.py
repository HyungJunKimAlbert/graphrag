import json

from eval import normalize_eval as ne
from rxrag import taxonomy

CANON = {"피로감": "만성피로", "흉통": "가슴통증"}


def case(cid, **kw):
    return {"id": cid, "group": "샘플", "utterance": "u", "profile": "", "required": [], "forbid": [], "route": "",
            "red_flag": None, "no_match": False, **kw}


def flat(symptoms=(), outside=(), red=False, categories=()):
    return {"symptoms": list(symptoms), "categories": list(categories),
            "unmatched": [{"text": t, "kind": "분류표 밖 증상"} for t in outside], "red_flag": red, "red_flag_reason": ""}


def test_required_met_with_alternatives_and_outside_text():
    c = case("a", required=[["근육통"], ["새벽 각성", "불면"]])
    assert ne.score_case(c, flat(["불면"], outside=["다리 근육통"]), CANON)["required_ok"] is True
    assert ne.score_case(c, flat(["불면"]), CANON)["required_ok"] is False


def test_canonical_names():
    assert ne.score_case(case("b", required=[["만성피로"]]), flat(["피로감"]), CANON)["required_ok"] is True


def test_forbid_violation():
    assert ne.score_case(case("c", forbid=["두통"]), flat(outside=["두통"]), CANON)["forbid_hit"] == ["두통"]


def test_metrics_three_cases():
    cases = [case("1", required=[["가슴통증"]], red_flag=True), case("2", red_flag=False, forbid=["두통"]),
             case("3", no_match=True)]
    answers = {"1": flat(outside=["흉통"], red=True), "2": flat(outside=["두통"], red=True), "3": flat()}
    m = ne.metrics([ne.score_case(c, answers[c["id"]], CANON) for c in cases])
    assert m["required"] == {"cases": 1, "met": 1, "rate": 1.0}
    assert m["forbid_violations"] == 1
    assert m["red_flag"] == {"tp": 1, "fp": 1, "fn": 0, "recall": 1.0, "precision": 0.5}
    assert m["no_match"] == {"cases": 1, "correct": 1, "accuracy": 1.0}


def test_no_match_wrong_when_symptom_found():
    m = ne.metrics([ne.score_case(case("x", no_match=True), flat(["불면"]), CANON)])
    assert m["no_match"]["correct"] == 0


def test_observe_cases_skipped():
    s = ne.score_case({**case("o", required=[["근육통"]]), "observe": True}, flat(), CANON)
    assert ne.metrics([s])["required"]["cases"] == 0


def test_export_writes_one_request_per_unique_prompt(tmp_path):
    cats = taxonomy.load()
    cases = [case("1", utterance="밤에 자주 깸", profile="여성, 70대"), case("2", utterance="밤에 자주 깸", profile="여성, 70대"),
             case("3", utterance="속쓰림", profile="")]
    assert ne.export(cases, cats, tmp_path) == 2
    body = json.loads(next((tmp_path / "requests" / "normalize").glob("*.json")).read_text(encoding="utf-8"))
    assert body["task"] == "normalize" and "주호소:" in body["user"]


def test_testset_option_reads_other_file(tmp_path, monkeypatch):
    """--testset으로 다른 테스트셋(샘플 형식 셋 등)을 읽는다. 기본값은 엔진 테스트셋."""
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"equivalents": {"만성피로": ["피로감"]}, "cases": [case("x")]}, ensure_ascii=False))
    seen = {}
    monkeypatch.setattr(ne, "export", lambda cases, cats, root: seen.setdefault("ids", [c["id"] for c in cases]) and 0)
    ne.main(["export", "--tag", "t", "--testset", str(path), "--session", str(tmp_path)])
    assert seen["ids"] == ["x"]


def test_score_with_client_parallel_keeps_order_and_records_usage():
    """운영 GPT처럼 클라이언트를 직접 주면 병렬로 부르고, 케이스 순서·원응답·호출별 시간을 남긴다."""
    from rxrag.llm import FakeLLM
    cats = taxonomy.load()

    def answer(task, system, user, schema):
        sym = "속쓰림" if "속쓰림" in user else "불면"
        cat = "소화/장" if sym == "속쓰림" else "스트레스/수면"
        return {"matches": [{"category": cat, "symptoms": [sym]}], "unmatched": [], "red_flag": False, "red_flag_reason": ""}
    cases = [case(str(i), utterance=("속쓰림" if i % 2 else "잠이 안 옴"), required=[["속쓰림" if i % 2 else "불면"]])
             for i in range(8)]
    scored, m, raw, calls = ne.score(cases, cats, {}, client=FakeLLM(answer), workers=4)
    assert [s["id"] for s in scored] == [str(i) for i in range(8)] and m["required"]["met"] == 8
    assert [r["id"] for r in raw] == [str(i) for i in range(8)] and raw[1]["response"]["matches"][0]["symptoms"] == ["속쓰림"]
    assert len(calls) == 8 and all(c["latency_ms"] >= 0 for c in calls)


def test_score_cli_openai_option(tmp_path, monkeypatch):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"equivalents": {}, "cases": [case("x", utterance="속쓰림", required=[["속쓰림"]])]},
                               ensure_ascii=False))
    from rxrag import llm
    from rxrag.llm import FakeLLM
    made = {}
    monkeypatch.setattr(llm, "OpenAILLM", lambda **kw: made.update(kw) or FakeLLM(lambda *a: {"matches": [{"category": "소화/장", "symptoms": ["속쓰림"]}],
                                                                     "unmatched": [], "red_flag": False, "red_flag_reason": ""}))
    monkeypatch.setattr(ne, "OUT", tmp_path)
    ne.main(["score", "--tag", "t", "--testset", str(path), "--llm", "openai", "--effort", "medium"])
    assert made == {"effort": "medium"}
    out = json.loads((tmp_path / "normalize-t.json").read_text(encoding="utf-8"))
    assert out["metrics"]["required"]["met"] == 1 and out["usage"]["calls"] == 1 and out["effort"] == "medium"
    assert (tmp_path / "normalize-t-raw.jsonl").exists()


def test_score_cli_anthropic_option(tmp_path, monkeypatch):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"equivalents": {}, "cases": [case("x", utterance="속쓰림", required=[["속쓰림"]])]},
                               ensure_ascii=False))
    from rxrag import llm
    from rxrag.llm import FakeLLM
    made = {}
    monkeypatch.setattr(llm, "AnthropicLLM", lambda **kw: made.update(kw) or FakeLLM(lambda *a: {"matches": [{"category": "소화/장", "symptoms": ["속쓰림"]}],
                                                                        "unmatched": [], "red_flag": False, "red_flag_reason": ""}))
    monkeypatch.setattr(ne, "OUT", tmp_path)
    ne.main(["score", "--tag", "t", "--testset", str(path), "--llm", "anthropic"])
    assert made == {"effort": "low"}
    out = json.loads((tmp_path / "normalize-t.json").read_text(encoding="utf-8"))
    assert out["llm"] == "anthropic" and out["effort"] == "low" and out["usage"]["calls"] == 1
