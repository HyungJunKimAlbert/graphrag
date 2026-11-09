import json

import pytest

from rxrag.llm import FakeLLM, MissingResponse, OpenAILLM, SessionLLM, request_key


def test_request_key_stable_and_sensitive():
    k = request_key("select", "s", "u", {"type": "object"})
    assert k == request_key("select", "s", "u", {"type": "object"}) and len(k) == 16
    assert k != request_key("select", "s", "u2", {"type": "object"})


def test_session_export_writes_request_and_raises(tmp_path):
    c = SessionLLM(tmp_path, mode="export")
    with pytest.raises(MissingResponse):
        c.complete_json("select", "s", "u", {"type": "object"})
    files = list((tmp_path / "requests" / "select").glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8")) == {"task": "select", "system": "s", "user": "u",
                                                               "schema": {"type": "object"}}


def test_session_export_returns_existing_response(tmp_path):
    k = request_key("select", "s", "u", {"type": "object"})
    (tmp_path / "responses" / "select").mkdir(parents=True)
    (tmp_path / "responses" / "select" / f"{k}.json").write_text('{"picks": [1]}')
    assert SessionLLM(tmp_path, mode="export").complete_json("select", "s", "u", {"type": "object"}) == {"picks": [1]}


def test_session_run_reads_response(tmp_path):
    k = request_key("select", "s", "u", {"type": "object"})
    (tmp_path / "responses" / "select").mkdir(parents=True)
    (tmp_path / "responses" / "select" / f"{k}.json").write_text('{"picks": []}')
    assert SessionLLM(tmp_path).complete_json("select", "s", "u", {"type": "object"}) == {"picks": []}


def test_session_run_missing_raises(tmp_path):
    with pytest.raises(MissingResponse) as e:
        SessionLLM(tmp_path).complete_json("select", "s", "u", {})
    assert e.value.key == request_key("select", "s", "u", {})


def test_fake_by_task_and_records_calls():
    f = FakeLLM({"select": {"picks": []}})
    assert f.complete_json("select", "s", "u", {}) == {"picks": []}
    assert f.calls[0]["task"] == "select" and f.calls[0]["user"] == "u"


def test_fake_callable():
    f = FakeLLM(lambda task, system, user, schema: {"n": len(f.calls)})
    assert f.complete_json("x", "s", "u", {}) == {"n": 1}


def test_openai_blocked_in_tests():
    with pytest.raises(Exception):
        OpenAILLM().complete_json("x", "s", "u", {})


def test_openai_records_usage_of_last_call():
    """GPT 호출마다 입력·출력·추론·캐시 토큰을 last_usage에 남긴다(엑셀 「LLM 호출」 시트용)."""
    from types import SimpleNamespace as NS
    usage = NS(input_tokens=1200, output_tokens=300, input_tokens_details=NS(cached_tokens=1024),
               output_tokens_details=NS(reasoning_tokens=200))
    c = OpenAILLM()
    c._client = NS(responses=NS(create=lambda **kw: NS(output_text='{"ok": 1}', usage=usage)))
    assert c.complete_json("select", "s", "u", {}) == {"ok": 1}
    assert c.last_usage == {"input": 1200, "output": 300, "reasoning": 200, "cached": 1024}


def test_meter_times_each_call_and_reads_usage():
    from rxrag.llm import Meter
    f = FakeLLM({"normalize": {"a": 1}, "select": [{"p": 1}, {"p": 2}]})
    m = Meter(f)
    m.complete_json("normalize", "s", "u", {})
    m.complete_json("select", "s", "u", {})
    m.complete_json("select", "s", "u2", {})
    assert [c["task"] for c in m.calls] == ["normalize", "select", "select"]
    assert all(c["latency_ms"] >= 0 and c["input"] is None for c in m.calls)  # FakeLLM은 토큰 정보가 없다
    assert len(f.calls) == 3  # 원래 클라이언트로 그대로 전달
