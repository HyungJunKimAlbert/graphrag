import pytest

from rxrag import candidates, safety

CTX = {"codes": ["h-1"], "drugs": {"interaction": ["DRUG-x"]}}


class SafetyDB:
    """legacy.safety_check가 보내는 Q_SAFETY에 [iid, conditions, interactions] 행으로 답한다."""

    def __init__(self, verdicts):
        self.verdicts = verdicts

    def run(self, query, ids, **params):
        return [[i, self.verdicts.get(i, {}).get("conditions", []), self.verdicts.get(i, {}).get("interactions", [])]
                for i in ids]


def cand(i, rank):
    return {"ingredient_id": f"I{i}", "name": f"성분{i}", "type": "nutrient", "members": [],
            "facts": [{"fact_id": f"SYM_DIRECT:S>I{i}", "kind": "SYM_DIRECT", "text": "t", "source": "s"}],
            "tier_rank": 0, "rule_rank": rank}


def avoid_drug():
    return {"interactions": [{"drug": "와파린", "note": "출혈 위험", "severity": "avoid", "review": None}]}


def consult_drug():
    return {"interactions": [{"drug": "와파린", "note": "상담 필요", "severity": "consult", "review": None}]}


def test_avoid_goes_to_excluded_with_reasons():
    pool = [cand(0, 0), cand(1, 1)]
    kept, excluded = safety.split(SafetyDB({"I0": avoid_drug()}), pool, CTX)
    assert [c["ingredient_id"] for c in kept] == ["I1"]
    assert excluded == [{"ingredient_id": "I0", "name": "성분0", "reasons": [{"by": "와파린", "text": "출혈 위험"}]}]


def test_caution_kept_with_cautions_by_default():
    kept, excluded = safety.split(SafetyDB({"I0": consult_drug()}), [cand(0, 0)], CTX)
    assert excluded == [] and kept[0]["cautions"] == [{"by": "와파린", "text": "상담 필요"}]


def test_policy_exclude_moves_cautions():
    kept, excluded = safety.split(SafetyDB({"I0": consult_drug()}), [cand(0, 0), cand(1, 1)], CTX, policy="exclude")
    assert [c["ingredient_id"] for c in kept] == ["I1"] and excluded[0]["ingredient_id"] == "I0"


def test_cap_after_split():
    pool = [cand(i, i) for i in range(30)]
    kept, excluded = safety.split(SafetyDB({f"I{i}": avoid_drug() for i in range(15)}), pool, CTX)
    capped = safety.cap(kept, n=20)
    assert len(excluded) == 15 and [c["ingredient_id"] for c in capped] == [f"I{i}" for i in range(15, 30)]


def test_cap_truncates_in_rule_rank_order():
    assert [c["ingredient_id"] for c in safety.cap([cand(i, i) for i in range(25)], n=20)] == [f"I{i}" for i in range(20)]


def test_empty_pool():
    assert safety.split(SafetyDB({}), [], CTX) == ([], [])


@pytest.mark.graph
def test_80m_exclusions(patient_80m_ctx):
    db, ctx = patient_80m_ctx
    kept, excluded = safety.split(db, candidates.pool(db, ctx), ctx)
    names = {e["name"] for e in excluded}
    assert {"마그네슘", "코엔자임Q10"} <= names and not names & {c["name"] for c in kept}
