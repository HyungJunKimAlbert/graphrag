"""Task 3. 개체 설명과 임베딩. 실제 DB를 보는 테스트는 `python -m wcrag.index.entities` 실행 뒤를 전제로 한다."""
import pytest

from wcrag.index import entities

NOT_ENTITY = ["Doc", "RuleSet", "RagMeta"]


def no_embed(texts):
    raise AssertionError(f"임베딩을 호출했다: {len(texts)}건")


def test_aliases_go_into_names():
    _, names = entities.describe("Symptom", {"name": "근육경련", "category": "신장/전해질", "aliases": ["쥐남", "다리에 쥐"]}, {})
    assert names == ["근육경련", "쥐남", "다리에 쥐"]
    _, names = entities.describe("Drug", {"name": "메트포르민", "drug_class": "내분비", "generic_names": "(Metformin, 당뇨병치료제)"}, {})
    assert names == ["메트포르민", "Metformin", "당뇨병치료제"]
    _, names = entities.describe("DrugItem", {"name": "쿠마딘(와파린)", "match": "쿠마딘"}, {"classes": ["와파린"]})
    assert names == ["쿠마딘(와파린)", "쿠마딘"]
    _, names = entities.describe("Ingredient", {"name": "판토텐산", "type": "nutrient"}, {"aliases": ["비타민B5"], "tags": []})
    assert names == ["판토텐산", "비타민B5"]


def test_ingredient_text_has_name_type_function_and_tags():
    text, _ = entities.describe("Ingredient", {"name": "마그네슘", "type": "nutrient", "nutrient_class": "미네랄",
                                               "function_text": "1. 에너지 이용에 필요\n2. 신경과 근육 기능 유지에 필요"},
                                {"tags": ["신경 및 근육 기능", "대사 및 에너지 생성"], "aliases": []})
    for part in ["마그네슘", "영양성분", "미네랄", "신경과 근육 기능 유지에 필요", "신경 및 근육 기능"]:
        assert part in text
    assert "\n" not in text


def test_condition_text_has_exam_range():
    text, names = entities.describe("Condition", {"label": "체질량지수 25~30 과체중", "kind": "exam", "exam_item": "bmi",
                                                  "exam_lo": 25.0, "exam_hi": 30.0}, {})
    assert "검진" in text and "bmi" in text and "25" in text and "30" in text
    assert names == ["체질량지수 25~30 과체중"]


def test_alias_table_maps_nutrient_names_to_ingredients():
    aliases = entities.load_aliases()
    assert "비타민B5" in aliases["NUT-45666"]


def test_dry_run_calls_no_embed(rag):
    before = rag.read("MATCH (n:RagEntity) RETURN count(n)")
    report = entities.build(rag, embed=no_embed, dry_run=True)
    assert report["entities"] == rag.read("MATCH (n) WHERE NOT n:Doc AND NOT n:RuleSet AND NOT n:RagMeta RETURN count(n)")[0][0]
    assert {"to_embed", "cached", "tokens", "docs_missing_embedding"} <= set(report)
    assert rag.read("MATCH (n:RagEntity) RETURN count(n)") == before     # 아무것도 쓰지 않았다


def test_every_entity_gets_text_names_and_label(rag):
    missing = rag.read("MATCH (n) WHERE none(l IN labels(n) WHERE l IN $skip) AND "
                       "(NOT n:RagEntity OR n.rag_text IS NULL OR n.rag_names IS NULL OR size(n.rag_names) = 0 "
                       "OR n.rag_key IS NULL OR n.rag_kind IS NULL) RETURN labels(n), count(*)", skip=NOT_ENTITY)
    assert missing == []
    assert rag.read("MATCH (n:RagEntity) RETURN count(n)")[0][0] >= 1220
    assert rag.read("MATCH (n:RagEntity) WITH n.rag_key AS k, count(*) AS c WHERE c > 1 RETURN count(k)") == [[0]]


def test_no_doc_or_meta_node_is_an_entity(rag):
    assert rag.read("MATCH (n:RagEntity) WHERE n:Doc OR n:RuleSet OR n:RagMeta RETURN count(n)") == [[0]]


def test_every_entity_has_embedding(rag):
    assert rag.read("MATCH (n:RagEntity) WHERE n.rag_embedding IS NULL RETURN count(n)") == [[0]]
    assert rag.read("MATCH (n:RagEntity) RETURN size(n.rag_embedding) LIMIT 1") == [[1536]]


def test_cached_text_not_reembedded(rag):
    report = entities.build(rag, embed=no_embed)          # 저장 파일에 다 있으면 호출이 0회다
    assert report["to_embed"] == 0


def test_doc_without_embedding_is_filled(rag):
    assert rag.read("MATCH (d:Doc) WHERE d.embedding IS NULL AND d.text IS NOT NULL RETURN count(d)") == [[0]]


def test_indexes_online(rag):
    state = dict(rag.read("SHOW INDEXES YIELD name, state RETURN name, state"))
    assert state.get("rag_entity_embedding") == "ONLINE" and state.get("rag_entity_names") == "ONLINE"


def test_names_index_finds_entity_by_alias(rag):
    rows = rag.read("CALL db.index.fulltext.queryNodes('rag_entity_names', $q) YIELD node RETURN node.rag_key LIMIT 5", q="비타민B5")
    assert ["Ingredient:NUT-45666"] in rows
