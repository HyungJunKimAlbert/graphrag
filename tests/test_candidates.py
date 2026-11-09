import pytest

from rxrag import candidates, legacy
from rxrag.candidates import KINDS


def by_name(pool, name):
    return next(c for c in pool if c["name"] == name)


@pytest.mark.graph
def test_pool_superset_of_engine(patient_80m_ctx):
    db, ctx = patient_80m_ctx
    engine_ids = {c["ingredient_id"] for g in legacy.GENERATORS for c in g(db, ctx)}
    assert engine_ids <= {c["ingredient_id"] for c in candidates.pool(db, ctx)}


@pytest.mark.graph
def test_every_candidate_has_fact(patient_80m_ctx):
    pool = candidates.pool(*patient_80m_ctx)
    assert pool and all(c["facts"] and all(f["fact_id"].split(":")[0] in KINDS for f in c["facts"]) for c in pool)


@pytest.mark.graph
def test_fact_ids_unique_within_candidate(patient_80m_ctx):
    for c in candidates.pool(*patient_80m_ctx):
        ids = [f["fact_id"] for f in c["facts"]]
        assert len(ids) == len(set(ids))


@pytest.mark.graph
def test_metformin_b12_depletion_fact(patient_80m_ctx):
    b12 = by_name(candidates.pool(*patient_80m_ctx), "비타민 B12")
    assert any(f["kind"] in ("DRUG_DEPLETE", "DRUG_SYM_DEPLETE") and "메트포르민" in f["text"] for f in b12["facts"])


@pytest.mark.graph
def test_group_has_members_and_rule_rank_order(patient_80m_ctx):
    pool = candidates.pool(*patient_80m_ctx)
    assert by_name(pool, "비타민 B군")["members"]
    assert [c["rule_rank"] for c in pool] == list(range(len(pool)))
    assert pool[0]["tier_rank"] == 0


@pytest.mark.graph
def test_relieved_by_fact_has_source(patient_80m_ctx):
    facts = [f for c in candidates.pool(*patient_80m_ctx) for f in c["facts"] if f["kind"] == "SYM_DIRECT"]
    assert facts and all(f["source"] for f in facts)


def test_rank_key_orders_tier_then_priority_then_fact_count():
    pool = candidates.rank([
        {"ingredient_id": "B", "facts": [1], "tier_rank": 1, "priority": 0},
        {"ingredient_id": "A", "facts": [1], "tier_rank": 0, "priority": 5},
        {"ingredient_id": "C", "facts": [1, 2], "tier_rank": 0, "priority": 5},
        {"ingredient_id": "D", "facts": [1], "tier_rank": 0, "priority": 1},
    ])
    assert [c["ingredient_id"] for c in pool] == ["D", "C", "A", "B"]
    assert [c["rule_rank"] for c in pool] == [0, 1, 2, 3]


def test_fact_has_korean_kind_label():
    row = ["I", "이름", "nutrient", ["D", "I"], "메트포르민 복용 → 이름 고갈", None, 0]
    f = candidates.fact("DRUG_DEPLETE", row)
    assert f["label"] == "복용약 고갈" and set(candidates.KIND_LABELS) == set(KINDS)
