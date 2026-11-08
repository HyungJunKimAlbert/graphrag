"""공통 픽스처. 테스트는 OpenAI를 호출하지 않는다. LLM과 임베딩은 가짜 함수를 주입한다."""
import pytest

from wcrag.db import connect


@pytest.fixture(scope="session")
def rag():
    graph = connect("rag")
    yield graph
    graph.close()


@pytest.fixture(scope="session")
def dev():
    graph = connect("dev")
    yield graph
    graph.close()
