import pytest

from rxrag import graph, legacy
from rxrag.graph import ConfigError, ReadOnlyDB, WriteRefused


class FakeInner:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.queries = []

    def run(self, query, **params):
        self.queries.append(query)
        return self.rows


def test_write_query_refused():
    db = ReadOnlyDB(FakeInner())
    for q in ["CREATE (n)", "match (n) detach delete n", "MATCH (n) SET n.x=1", "MERGE (a:A)",
              "MATCH (n) REMOVE n.x", "DROP INDEX x", "LOAD CSV FROM 'f' AS r RETURN r", "CALL apoc.create.node([], {})"]:
        with pytest.raises(WriteRefused):
            db.run(q)


def test_refused_query_never_reaches_db():
    inner = FakeInner()
    with pytest.raises(WriteRefused):
        ReadOnlyDB(inner).run("CREATE (n)")
    assert inner.queries == []


def test_read_query_passes():
    assert ReadOnlyDB(FakeInner(rows=[[1]])).run("MATCH (n) RETURN count(n)") == [[1]]


def test_property_names_with_keywords_pass():
    db = ReadOnlyDB(FakeInner(rows=[[1]]))
    assert db.run("MATCH (n) WHERE n.created_at IS NOT NULL RETURN n.offset, n.dataset") == [[1]]


def test_wrong_port_refused(monkeypatch):
    monkeypatch.setattr(graph, "_env", lambda: {"NEO4J_HTTP_URL": "http://localhost:7474", "NEO4J_PASSWORD": "x"})
    with pytest.raises(ConfigError) as e:
        graph.connect()
    assert "x" not in str(e.value) and "7474" not in str(e.value)


def test_missing_url_refused(monkeypatch):
    monkeypatch.setattr(graph, "_env", lambda: {})
    with pytest.raises(ConfigError):
        graph.connect()


@pytest.mark.graph
def test_dev_graph_reachable():
    assert graph.connect().run("MATCH (c:SymptomCategory) RETURN count(c)")[0][0] == 14


def test_legacy_imports():
    assert len(legacy.GENERATORS) == 8 and callable(legacy.safety_check)
    assert callable(legacy.build_context) and callable(legacy.engine_recommend)
    assert legacy.INCOMPLETE and legacy.FORBIDDEN.search("당뇨를치료합니다")


def test_legacy_path_does_not_shadow_our_packages():
    """엔진 경로가 우리 루트보다 앞에 있으면 tests·eval 같은 같은 이름 패키지가 엔진 쪽으로 가려진다."""
    import sys
    from pathlib import Path
    from rxrag.config import GRAPHDB, ROOT
    paths = [str(Path(p or ".").resolve()) for p in sys.path]
    assert paths.index(str(ROOT)) < paths.index(str(GRAPHDB))
