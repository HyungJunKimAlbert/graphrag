"""기준선 B0. 문서 벡터 검색만 하는 일반 RAG다. 그래프 관계는 쓰지 않으므로 facts는 비어 있다.

Local 검색이 이보다 나은지 재는 데 쓴다. 문서가 다루는 성분(ABOUT)만 ingredient_ids에 넣어 성분 분류 검사가 돌게 한다.
"""
TOP_K = 10
Q_DOCS = """CALL db.index.vector.queryNodes('doc_embedding', $k, $vec) YIELD node, score
MATCH (node)-[:ABOUT]->(i:Ingredient)
WITH node, score, collect(i)[0] AS i
RETURN node.doc_id, node.kind, node.text, i.ingredient_id, i.name, score ORDER BY score DESC, node.doc_id"""


def retrieve(graph, question: str, embed, mentions: list[str] | None = None) -> dict:
    """mentions는 받기만 한다. 기준선은 질문 분석의 언급을 쓰지 않지만, ask()는 모든 검색기를 같은 모양으로 부른다."""
    vec = embed([question])[0]
    rows = graph.read(Q_DOCS, k=TOP_K, vec=vec)
    docs = [{"id": doc_id, "kind": kind, "text": text, "ingredient_id": iid} for doc_id, kind, text, iid, _, _ in rows]
    names = {iid: name for _, _, _, iid, name, _ in rows}
    return {"seeds": [], "facts": [], "docs": docs, "ingredient_ids": list(names), "ingredient_names": names,
            "max_seed_score": max((float(r[5]) for r in rows), default=0.0)}
