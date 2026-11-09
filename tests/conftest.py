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
    if request.node.get_closest_marker("graph") is None:
        monkeypatch.setattr(socket, "create_connection", refuse)
