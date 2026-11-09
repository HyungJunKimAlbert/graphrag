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
