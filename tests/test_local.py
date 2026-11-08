"""Task 4. Local 검색(개체 찾기 → 이웃 확장 → 시드 잇기 → 문서 → 예산)과 기준선(문서 벡터만).

rag DB를 읽고, embed는 저장 파일(out/embeddings.jsonl)에 있는 임베딩만 돌려주는 가짜 함수다.
질문 임베딩은 승인 아래 한 번 저장해 두었다. 저장돼 있지 않은 문장을 물으면 테스트가 바로 실패한다(호출하지 않는다).
"""
import pytest

from wcrag import llm
from wcrag.retrieve import local, vector_only

Q_DEPLETION = "메트포르민을 먹으면 부족해지는 영양소는?"
Q_PREGNANT = "임산부가 피해야 하는 원료는?"
Q_PAIR = "메트포르민을 복용하는 임산부에게 권할 수 있는 성분은?"
Q_ISOLATED = "다뇨 증상에 연결된 원료가 있나?"
Q_BROAD = "혈압약을 먹는 사람이 조심해야 할 원료를 모두 알려줘"
Q_MAGNESIUM = "마그네슘의 주의사항은?"
QUESTIONS = [Q_DEPLETION, Q_PREGNANT, Q_PAIR, Q_ISOLATED, Q_BROAD, Q_MAGNESIUM]

METFORMIN, PREGNANT = "Drug:DRUG-metformin", "Condition:pop-pregnant"
FOLATE, COQ10, B12 = "Ingredient:NUT-45668", "Ingredient:NTC-2-9", "Ingredient:NUT-45669"


def embed(texts):
    stored = llm.cached_embeddings(texts)
    missing = [t for t in texts if t not in stored]
    assert not missing, f"저장된 임베딩이 없습니다(호출하지 않음): {missing}"
    return [stored[t] for t in texts]


@pytest.fixture(scope="module")
def contexts(rag):
    return {q: local.retrieve(rag, q, embed) for q in QUESTIONS}


def facts_of(context, key):
    return [f for f in context["facts"] if key in (f["start"], f["end"])]


def test_context_shape(contexts):
    ctx = contexts[Q_DEPLETION]
    assert set(ctx) >= {"seeds", "facts", "docs", "ingredient_ids", "ingredient_names", "max_seed_score"}
    assert ctx["seeds"] and set(ctx["seeds"][0]) == {"key", "kind", "name", "score", "isolated"}
    assert set(ctx["facts"][0]) == {"id", "text", "start", "type", "end", "sources"}
    assert set(ctx["docs"][0]) == {"id", "kind", "text", "ingredient_id"}
    assert all(f["id"].startswith("F-") and len(f["id"]) == 12 for f in ctx["facts"])
    assert len({f["id"] for f in ctx["facts"]}) == len(ctx["facts"])
    assert set(ctx["ingredient_names"]) == set(ctx["ingredient_ids"])
    assert isinstance(ctx["max_seed_score"], float) and ctx["max_seed_score"] == max(s["score"] for s in ctx["seeds"])


def test_drug_question_brings_depletion_facts(contexts):
    ctx = contexts[Q_DEPLETION]
    assert METFORMIN in [s["key"] for s in ctx["seeds"]]
    depleted = {f["end"] for f in ctx["facts"] if f["type"] == "DEPLETES" and f["start"] == METFORMIN}
    assert {FOLATE, COQ10, B12} <= depleted
    assert "NUT-45669" in ctx["ingredient_ids"] and ctx["ingredient_names"]["NUT-45669"] == "비타민 B12"


def test_fact_text_reads_as_a_sentence(contexts):
    ctx = contexts[Q_DEPLETION]
    fact = next(f for f in ctx["facts"] if f["type"] == "DEPLETES" and f["start"] == METFORMIN and f["end"] == B12)
    assert "메트포르민" in fact["text"] and "비타민 B12" in fact["text"] and "고갈" in fact["text"]


def test_hub_relation_capped(contexts):
    ctx = contexts[Q_PREGNANT]
    assert PREGNANT in [s["key"] for s in ctx["seeds"]]
    excludes = [f for f in ctx["facts"] if f["type"] == "EXCLUDES" and f["start"] == PREGNANT]
    assert 1 <= len(excludes) <= 5


def test_pair_path_joins_two_seeds(contexts):
    ctx = contexts[Q_PAIR]
    keys = [s["key"] for s in ctx["seeds"]]
    assert METFORMIN in keys and PREGNANT in keys
    assert any(f["type"] == "DEPLETES" and f["start"] == METFORMIN and f["end"] == FOLATE for f in ctx["facts"])
    assert any(f["type"] == "RECOMMENDS" and f["start"] == PREGNANT and f["end"] == FOLATE for f in ctx["facts"])


def test_isolated_seed_marked(contexts, rag):
    key = "Symptom:" + rag.read("MATCH (s:Symptom {name: '다뇨'}) RETURN s.symptom_id")[0][0]
    ctx = contexts[Q_ISOLATED]
    seed = next(s for s in ctx["seeds"] if s["key"] == key)
    assert seed["isolated"] is True
    assert facts_of(ctx, key) == []


def test_token_budget_respected(contexts):
    for q, ctx in contexts.items():
        texts = [f["text"] for f in ctx["facts"]] + [d["text"] for d in ctx["docs"]]
        assert llm.count_tokens(texts) <= local.TOKEN_BUDGET, q
    assert contexts[Q_BROAD]["facts"]                      # 넓은 질문도 비어 있지는 않다


def test_docs_prefer_function_and_precaution(contexts, rag):
    """성분의 기능·주의 문서는 상한 안에서 먼저 들어간다(마그네슘은 주의 문서가 없어 기능 문서만 해당한다)."""
    ctx = contexts[Q_MAGNESIUM]
    mine = [d for d in ctx["docs"] if d["ingredient_id"] == "NUT-45673"]
    assert 1 <= len(mine) <= local.DOCS_PER_INGREDIENT
    first = {r[0] for r in rag.read("MATCH (d:Doc)-[:ABOUT]->(:Ingredient {ingredient_id: 'NUT-45673'}) "
                                    "WHERE d.kind IN $first RETURN d.doc_id", first=local.FIRST_DOC_KINDS)}
    assert first and first <= {d["id"] for d in mine}


def test_same_question_same_context(rag):
    assert local.retrieve(rag, Q_DEPLETION, embed) == local.retrieve(rag, Q_DEPLETION, embed)


def test_vector_only_has_no_facts(rag):
    ctx = vector_only.retrieve(rag, Q_MAGNESIUM, embed)
    assert ctx["facts"] == [] and ctx["seeds"] == []
    assert 1 <= len(ctx["docs"]) <= 10 and all(d["ingredient_id"] for d in ctx["docs"])
    assert ctx["ingredient_ids"] and set(ctx["ingredient_names"]) == set(ctx["ingredient_ids"])
    assert all(d["ingredient_id"] in ctx["ingredient_ids"] for d in ctx["docs"])
    assert isinstance(ctx["max_seed_score"], float)


# ---------- 긴 질문: 이름 언급을 시드로 ----------

Q_PATIENT = "와파린·메트포르민을 먹는 80세 남성이 다리에 쥐가 난다. 무엇을 권할 수 있나?"
WARFARIN, CRAMP, MAGNESIUM = "Drug:DRUG-warfarin", "Symptom:SYM-48047e9a1c", "Ingredient:NUT-45673"


def test_lucene_query_splits_joined_names():
    assert local.lucene_query("와파린·메트포르민을 먹는 80세 남성") == "와파린 메트포르민을 먹는 80세 남성"
    assert local.lucene_query("비타민 B12(코발라민)?") == "비타민 B12 코발라민"


def test_names_written_in_question_become_seeds(rag):
    ctx = local.retrieve(rag, Q_PATIENT, embed)
    keys = [s["key"] for s in ctx["seeds"]]
    assert METFORMIN in keys and WARFARIN in keys
    assert keys.index(WARFARIN) < 4 and keys.index(METFORMIN) < 4      # 언급된 이름이 앞자리를 차지한다


def test_mentions_from_question_analysis_guide_seeds(rag):
    ctx = local.retrieve(rag, Q_PATIENT, embed, mentions=["와파린", "메트포르민", "근육경련"])
    keys = [s["key"] for s in ctx["seeds"]]
    assert CRAMP in keys and METFORMIN in keys and WARFARIN in keys
    assert any(f["type"] == "RELIEVED_BY" and f["start"] == CRAMP and f["end"] == MAGNESIUM for f in ctx["facts"])
    assert "NUT-45673" in ctx["ingredient_ids"]


def test_literal_match_prefers_longest_name(rag):
    found = local.literal_mentions("비타민 B12가 부족하면?", local.name_index(rag))
    assert "Ingredient:NUT-45669" in found and "Ingredient:NUT-45663" not in found   # B12 안의 B1은 세지 않는다


def test_drug_item_seed_is_lifted_to_its_drug(rag):
    """'와파린'은 약 계열(Drug)과 약 표기(DrugItem) 둘 다의 이름이다. 표기는 계열로 올려 시드 자리를 두 번 쓰지 않는다."""
    ctx = local.retrieve(rag, Q_PATIENT, embed, mentions=["와파린", "메트포르민", "근육경련"])
    assert all(s["kind"] != "DrugItem" for s in ctx["seeds"])
    keys = [s["key"] for s in ctx["seeds"]]
    assert keys.count(WARFARIN) == 1 and keys.count(METFORMIN) == 1
