"""Task 1. 개발 DB 복제. 실제 DB 상태를 보는 테스트는 `python -m wcrag.index.clone` 실행 뒤를 전제로 한다."""
from wcrag import cli
from wcrag.index.clone import clone

Q_LABELS = "MATCH (n) UNWIND labels(n) AS l RETURN l, count(*)"
Q_TYPES = "MATCH ()-[r]->() RETURN type(r), count(*)"


class Recorder:
    """호출을 기록하는 래퍼. inner가 없으면 아무것도 저장하지 않는 빈 대상이다."""

    def __init__(self, inner=None):
        self.inner, self.reads, self.runs = inner, [], []

    def read(self, cypher, timeout_s=10, **params):
        self.reads.append(cypher)
        return self.inner.read(cypher, timeout_s, **params) if self.inner else []

    def run(self, cypher, **params):
        self.runs.append(cypher)
        return []


def test_counts_match_by_label_and_type(dev, rag):
    src_labels, dst_labels = dict(dev.read(Q_LABELS)), dict(rag.read(Q_LABELS))
    assert src_labels["Ingredient"] > 0
    assert {label: dst_labels.get(label) for label in src_labels} == src_labels
    src_types, dst_types = dict(dev.read(Q_TYPES)), dict(rag.read(Q_TYPES))
    assert {t: dst_types.get(t) for t in src_types} == src_types


def test_doc_embeddings_copied(dev, rag):
    """개발 DB에 임베딩이 있는 문서는 복제본에도 같은 임베딩이 있다. 복제본은 Task 3이 빠진 문서를 채우므로 더 많을 수 있다."""
    ids = "MATCH (d:Doc) WHERE d.embedding IS NOT NULL RETURN d.doc_id"
    src_ids, dst_ids = {r[0] for r in dev.read(ids)}, {r[0] for r in rag.read(ids)}
    assert src_ids and src_ids <= dst_ids
    sample = "MATCH (d:Doc) WHERE d.embedding IS NOT NULL WITH d ORDER BY d.doc_id LIMIT 1 RETURN d.doc_id, d.embedding"
    assert rag.read(sample) == dev.read(sample)


def test_source_receives_only_reads(dev):
    src, sink = Recorder(dev), Recorder()
    result = clone(src, sink)
    assert src.runs == []
    assert src.reads and sink.runs
    assert result["nodes"] == dev.read("MATCH (n) RETURN count(n)")[0][0]
    assert result["relationships"] == dev.read("MATCH ()-[r]->() RETURN count(r)")[0][0]


def test_rule_version_recorded(dev, rag):
    version = dev.read("MATCH (r:RuleSet) RETURN r.version")
    assert version and version[0][0]
    assert rag.read("MATCH (m:RagMeta {id: 'current'}) RETURN m.source_rule_version") == version


def test_no_clone_scaffolding_left(rag):
    assert rag.read("MATCH (n) WHERE n._src IS NOT NULL OR n:_Clone RETURN count(n)") == [[0]]


def test_schema_indexes_online(rag):
    state = dict(rag.read("SHOW INDEXES YIELD name, state RETURN name, state"))
    for name in ["ingredient_id", "condition_code", "doc_id", "doc_text", "doc_embedding", "symptom_text"]:
        assert state.get(name) == "ONLINE", name


def test_status_shows_both_rule_versions(dev, capsys):
    version = dev.read("MATCH (r:RuleSet) RETURN r.version")[0][0]
    cli.main(["status"])
    out = capsys.readouterr().out
    assert out.count(version) >= 2 and "다시 복제" not in out
