"""Task 0. 접속 계층: 개발 DB는 읽기만, 읽기 트랜잭션은 쓰기를 거절한다."""
import pytest
from neo4j.exceptions import Neo4jError


def test_dev_run_raises_permission_error(dev):
    with pytest.raises(PermissionError):
        dev.run("RETURN 1")


def test_dev_read_returns_rows(dev):
    assert dev.read("RETURN 1 AS a, 'x' AS b") == [[1, "x"]]


def test_read_rejects_write_cypher(rag):
    with pytest.raises(Neo4jError):
        rag.read("CREATE (n:RagSmoke)")
    assert rag.read("MATCH (n:RagSmoke) RETURN count(n)") == [[0]]


def test_rag_roundtrip(rag):
    rag.run("CREATE (n:RagSmoke {v: $v})", v=1)
    try:
        assert rag.read("MATCH (n:RagSmoke) RETURN n.v") == [[1]]
    finally:
        rag.run("MATCH (n:RagSmoke) DETACH DELETE n")
    assert rag.read("MATCH (n:RagSmoke) RETURN count(n)") == [[0]]
