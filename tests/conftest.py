"""테스트 중 외부 호출 차단. graph 마크가 없는 테스트는 네트워크를 쓰지 못하고, OpenAI 클라이언트는 어디서도 만들 수 없다."""
import socket

import pytest


class ExternalCallBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def block_external(request, monkeypatch):
    def refuse(*args, **kwargs):
        raise ExternalCallBlocked("테스트 중 외부 호출은 막혀 있습니다.")

    try:
        import openai
        monkeypatch.setattr(openai.OpenAI, "__init__", refuse)
    except ImportError:
        pass
    try:
        import anthropic
        monkeypatch.setattr(anthropic.Anthropic, "__init__", refuse)
    except ImportError:
        pass
    if request.node.get_closest_marker("graph") is None:
        monkeypatch.setattr(socket, "create_connection", refuse)


FIXTURE_80M = "/Users/user/Documents/projects/wellcheck/graphdb/tests/fixtures/patient_80m.json"


@pytest.fixture
def patient_80m():
    import json
    with open(FIXTURE_80M, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def patient_80m_ctx(patient_80m):
    """주호소 없이 symptoms만 쓴다(merge → None → 엔진 기본 경로). graph 마크 테스트에서만 쓴다."""
    from rxrag import graph, legacy, normalize
    db = graph.connect()
    return db, legacy.build_context(db, patient_80m, normalize.merge(patient_80m, None))
