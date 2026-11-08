"""개발 DB(dev)를 전용 DB(rag)로 복제한다. 실행: python -m wcrag.index.clone

dev에는 read()만 쓴다. rag의 기존 데이터는 모두 지우므로 개체 임베딩 같은 파생 데이터도 사라진다.
파생 데이터는 out/의 저장 파일에서 다시 쌓을 수 있어 과금이 없다.
"""
from collections import defaultdict

from .. import config
from ..db import Graph, connect

PAGE = 1000
SCHEMA_FILE = config.LEGACY_GRAPHDB / "schema" / "supplement-graph-schema.cypher"

Q_NODES = """MATCH (n) WHERE elementId(n) > $after
RETURN elementId(n), labels(n), properties(n) ORDER BY elementId(n) LIMIT $limit"""
Q_RELS = """MATCH (a)-[r]->(b) WHERE elementId(r) > $after
RETURN elementId(r), elementId(a), elementId(b), type(r), properties(r) ORDER BY elementId(r) LIMIT $limit"""
Q_RULE_VERSION = "MATCH (r:RuleSet) RETURN r.version"


def quoted(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def pages(src: Graph, query: str):
    """elementId 순서로 끝까지 읽는다. SKIP 대신 마지막 ID 뒤부터 읽어 페이지가 겹치지 않는다."""
    after = ""
    while True:
        rows = src.read(query, timeout_s=120, after=after, limit=PAGE)
        if not rows:
            return
        yield rows
        after = rows[-1][0]


def schema_statements() -> list[str]:
    lines = [line for line in SCHEMA_FILE.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("//")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def clone(src: Graph, dst: Graph) -> dict[str, int]:
    dst.run("MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS")
    dst.run("CREATE INDEX clone_src IF NOT EXISTS FOR (n:_Clone) ON (n._src)")

    counts = {"nodes": 0, "relationships": 0}
    for rows in pages(src, Q_NODES):
        by_labels = defaultdict(list)
        for node_id, labels, props in rows:
            by_labels[tuple(sorted(labels))].append({"id": node_id, "props": props})
        for labels, batch in by_labels.items():
            label_text = "".join(":" + quoted(label) for label in labels)
            dst.run(f"UNWIND $rows AS row CREATE (n:_Clone{label_text}) SET n = row.props, n._src = row.id", rows=batch)
        counts["nodes"] += len(rows)

    for rows in pages(src, Q_RELS):
        by_type = defaultdict(list)
        for _, start, end, rel_type, props in rows:
            by_type[rel_type].append({"a": start, "b": end, "props": props})
        for rel_type, batch in by_type.items():
            dst.run("UNWIND $rows AS row MATCH (a:_Clone {_src: row.a}), (b:_Clone {_src: row.b}) "
                    f"CREATE (a)-[r:{quoted(rel_type)}]->(b) SET r = row.props", rows=batch)
        counts["relationships"] += len(rows)

    dst.run("MATCH (n:_Clone) CALL { WITH n REMOVE n:_Clone, n._src } IN TRANSACTIONS OF 5000 ROWS")
    dst.run("DROP INDEX clone_src IF EXISTS")
    for statement in schema_statements():
        dst.run(statement)
    dst.run("CALL db.awaitIndexes(300)")

    version = src.read(Q_RULE_VERSION)
    dst.run("MERGE (m:RagMeta {id: 'current'}) SET m.source_rule_version = $version, m.cloned_at = datetime()",
            version=version[0][0] if version else None)
    return counts


def main():
    src, dst = connect("dev"), connect("rag")
    try:
        counts = clone(src, dst)
    finally:
        src.close()
        dst.close()
    print(f"복제 완료: 노드 {counts['nodes']:,}개, 관계 {counts['relationships']:,}개")
    print("개체 임베딩 같은 파생 데이터는 지워졌습니다. python -m wcrag.index.entities 로 다시 쌓으세요.")


if __name__ == "__main__":
    main()
