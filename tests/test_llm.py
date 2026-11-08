"""Task 3. LLM·임베딩 호출 계층. 실제 OpenAI 대신 가짜 호출 함수를 넣는다."""
import pytest
from pydantic import BaseModel

from wcrag import llm


class Answer(BaseModel):
    word: str


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "EMBED_CACHE", tmp_path / "embeddings.jsonl")
    monkeypatch.setattr(llm, "LLM_CACHE_DIR", tmp_path / "llm_cache")
    monkeypatch.setattr(llm, "_embeddings", None)
    return tmp_path


def test_embed_saves_and_reuses(store):
    calls = []

    def fake_api(texts):
        calls.append(list(texts))
        return [[float(len(t)), 0.5] for t in texts]

    first = llm.embed(["가", "나나"], api=fake_api)
    second = llm.embed(["나나", "다다다"], api=fake_api)
    assert first == [[1.0, 0.5], [2.0, 0.5]] and second == [[2.0, 0.5], [3.0, 0.5]]
    assert calls == [["가", "나나"], ["다다다"]]          # 저장된 문장은 다시 보내지 않는다
    assert set(llm.cached_embeddings(["가", "없는 문장"])) == {"가"}


def test_embed_cache_survives_restart(store, monkeypatch):
    llm.embed(["가"], api=lambda texts: [[1.0] for _ in texts])
    monkeypatch.setattr(llm, "_embeddings", None)          # 프로세스를 새로 띄운 것과 같다
    assert llm.embed(["가"], api=lambda texts: pytest.fail("다시 호출했다")) == [[1.0]]


def test_embed_key_includes_model(store, monkeypatch):
    llm.embed(["가"], api=lambda texts: [[1.0] for _ in texts])
    monkeypatch.setattr(llm.config, "EMBED_MODEL", "other-model")
    assert llm.cached_embeddings(["가"]) == {}


def test_complete_saves_and_reuses(store):
    calls = []

    def fake_api(instructions, payload, schema):
        calls.append(payload)
        return {"word": payload.upper()}, {"tokens_in": 10, "tokens_out": 2}

    first = llm.complete("지시", "abc", Answer, tag="t", api=fake_api)
    again = llm.complete("지시", "abc", Answer, tag="t", api=fake_api)
    other = llm.complete("다른 지시", "abc", Answer, tag="t", api=fake_api)
    assert (first.word, again.word, other.word) == ("ABC", "ABC", "ABC")
    assert calls == ["abc", "abc"]                        # 지시문이 바뀌면 다시 호출한다
    assert (store / "llm_cache" / "t.jsonl").exists()


def test_complete_records_usage_and_cache_flag(store):
    api = lambda instructions, payload, schema: ({"word": "x"}, {"tokens_in": 10, "tokens_out": 2})  # noqa: E731
    start = len(llm.USAGE_LOG)
    llm.complete("지시", "p", Answer, tag="t", api=api)
    llm.complete("지시", "p", Answer, tag="t", api=api)
    fresh, cached = llm.USAGE_LOG[start:]
    assert (fresh["tokens_in"], fresh["tokens_out"], fresh["cached"]) == (10, 2, False)
    assert (cached["tokens_in"], cached["cached"]) == (10, True)


def test_count_tokens_is_positive():
    assert llm.count_tokens(["마그네슘은 미네랄입니다.", "x"]) > 2
