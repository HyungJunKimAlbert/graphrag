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


def test_openai_last_usage_is_per_thread():
    """병렬 호출 때 다른 스레드의 토큰이 섞이지 않는다."""
    import threading
    c = OpenAILLM()
    c.last_usage = {"input": 1}
    seen = {}
    t = threading.Thread(target=lambda: seen.setdefault("other", c.last_usage))
    t.start(); t.join()
    assert seen["other"] is None and c.last_usage == {"input": 1}


def test_anthropic_blocked_in_tests():
    from rxrag.llm import AnthropicLLM
    with pytest.raises(Exception):
        AnthropicLLM().complete_json("x", "s", "u", {})


def test_anthropic_sends_cached_system_and_schema_and_records_usage():
    """Haiku: system에 캐시 표시, json_schema 구조화 출력, 토큰은 캐시 읽기·쓰기 포함 입력 합계로 남긴다."""
    from types import SimpleNamespace as NS
    from rxrag.llm import AnthropicLLM
    sent = {}
    usage = NS(input_tokens=40, cache_read_input_tokens=1500, cache_creation_input_tokens=0, output_tokens=120)
    res = NS(stop_reason="end_turn", usage=usage,
             content=[NS(type="thinking", thinking=""), NS(type="text", text='{"ok": 1}')])
    c = AnthropicLLM()
    c._client = NS(messages=NS(create=lambda **kw: sent.update(kw) or res))
    assert c.complete_json("normalize", "SYS", "USER", {"type": "object"}) == {"ok": 1}
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    assert sent["messages"] == [{"role": "user", "content": "USER"}]
    assert sent["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert c.last_usage == {"input": 1540, "output": 120, "reasoning": None, "cached": 1500, "cache_write": 0}


def test_anthropic_refuses_truncated_or_refused_output():
    from types import SimpleNamespace as NS
    from rxrag.llm import AnthropicLLM
    for reason in ("max_tokens", "refusal"):
        c = AnthropicLLM()
        res = NS(stop_reason=reason, usage=None, content=[NS(type="text", text='{"ok"')])
        c._client = NS(messages=NS(create=lambda **kw: res))
        with pytest.raises(RuntimeError, match=reason):
            c.complete_json("normalize", "s", "u", {})


def test_anthropic_strips_unsupported_array_constraints():
    """Claude 구조화 출력은 복잡한 배열 제약을 받지 않는다. 개수는 verify.check가 검사하므로 maxItems·minItems>1을 빼고 보낸다."""
    from types import SimpleNamespace as NS
    from rxrag import select
    from rxrag.llm import AnthropicLLM
    sent = {}
    res = NS(stop_reason="end_turn", usage=None, content=[NS(type="text", text='{"picks": []}')])
    c = AnthropicLLM()
    c._client = NS(messages=NS(create=lambda **kw: sent.update(kw) or res))
    pool = [{"ingredient_id": "A", "facts": [{"fact_id": "F1"}]}]
    original = select.schema(pool, 3)
    c.complete_json("select", "s", "u", original)
    picks = sent["output_config"]["format"]["schema"]["properties"]["picks"]
    assert "maxItems" not in picks and "minItems" not in picks
    assert picks["items"]["properties"]["fact_ids"]["minItems"] == 1  # 0·1은 지원
    assert original["properties"]["picks"]["maxItems"] == 3  # 원본 스키마는 그대로(검증·GPT가 쓴다)


def test_harness_cli_accepts_anthropic(monkeypatch, tmp_path):
    from eval import harness
    import rxrag.llm as llm
    made = {}
    monkeypatch.setattr(llm, "AnthropicLLM", lambda **kw: made.setdefault("client", ("anthropic", kw)))
    monkeypatch.setattr(harness, "run", lambda patients, root, out, client=None: made.update(run=client))
    monkeypatch.setattr(harness, "load_patients", lambda path: [])
    harness.main(["run", "--tag", "t", "--llm", "anthropic"])
    assert made["run"] == ("anthropic", {"effort": "low"})
