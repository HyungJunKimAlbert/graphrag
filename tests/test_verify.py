from rxrag import verify


def cand(iid, name, rank, members=(), cautions=()):
    return {"ingredient_id": iid, "name": name, "type": "group" if members else "nutrient", "members": list(members),
            "tier_rank": 0, "rule_rank": rank, "cautions": list(cautions),
            "facts": [{"fact_id": f"SYM_DIRECT:S>{iid}", "kind": "SYM_DIRECT", "text": f"{name} 근거", "source": "s"}]}


POOL = [cand("A", "테아닌", 0), cand("B", "비타민 B12", 1), cand("C", "홍경천 추출물", 2),
        cand("G", "비타민 B군", 3, members=["B", "F"]), cand("I1", "인삼", 4), cand("I2", "인 삼", 5),
        cand("F", "엽산", 6)]


def pick(iid, rank, facts=None, reason="수면 건강에 도움을 줄 수 있습니다."):
    return {"ingredient_id": iid, "rank": rank, "fact_ids": [f"SYM_DIRECT:S>{iid}"] if facts is None else facts,
            "reason": reason}


GOOD = {"picks": [pick("A", 1), pick("B", 2), pick("C", 3)]}


def picks(*items):
    return {"picks": list(items)}


def test_ok():
    assert verify.check(GOOD, POOL, set(), 3) == []


def test_wrong_count():
    assert verify.check(picks(pick("A", 1), pick("B", 2)), POOL, set(), 3)


def test_outside_pool():
    assert verify.check(picks(pick("A", 1), pick("B", 2), pick("Z", 3)), POOL, set(), 3)


def test_excluded():
    assert verify.check(GOOD, POOL, {"C"}, 3)


def test_duplicate_id():
    assert verify.check(picks(pick("A", 1), pick("A", 2), pick("C", 3)), POOL, set(), 3)


def test_same_name_two_ids():
    assert verify.check(picks(pick("I1", 1), pick("I2", 2), pick("C", 3)), POOL, set(), 3)


def test_group_and_member():
    assert verify.check(picks(pick("G", 1), pick("B", 2), pick("C", 3)), POOL, set(), 3)


def test_missing_fact():
    assert verify.check(picks(pick("A", 1, facts=[]), pick("B", 2), pick("C", 3)), POOL, set(), 3)


def test_foreign_fact():
    assert verify.check(picks(pick("A", 1, facts=["SYM_DIRECT:S>B"]), pick("B", 2), pick("C", 3)), POOL, set(), 3)


def test_rank_dup():
    assert verify.check(picks(pick("A", 1), pick("B", 1), pick("C", 3)), POOL, set(), 3)


def test_forbidden_reason():
    assert verify.check(picks(pick("A", 1, reason="당뇨를 치료합니다"), pick("B", 2), pick("C", 3)), POOL, set(), 3)


def test_malformed_response():
    assert verify.check({"nope": 1}, POOL, set(), 3)


def test_fallback_respects_names_and_groups():
    pool = [cand("I1", "인삼", 0), cand("I2", "인 삼", 1), cand("B", "비타민 B12", 2), cand("G", "비타민 B군", 3, ["B"]),
            cand("C", "홍경천 추출물", 4)]
    out = verify.fallback(pool, 3)
    assert [p["ingredient_id"] for p in out] == ["I1", "B", "C"]
    assert [p["rank"] for p in out] == [1, 2, 3]
    assert verify.check({"picks": out}, pool, set(), 3) == []


def test_fallback_group_first_blocks_member():
    pool = [cand("G", "비타민 B군", 0, ["B"]), cand("B", "비타민 B12", 1), cand("C", "홍경천 추출물", 2)]
    assert [p["ingredient_id"] for p in verify.fallback(pool, 3)] == ["G", "C"]


def test_materialize_shape_and_order():
    pool = [cand("A", "테아닌", 0, cautions=[{"by": "x", "text": "y"}]), cand("B", "비타민 B12", 1),
            cand("C", "홍경천 추출물", 2)]
    out = verify.materialize({"picks": [pick("C", 3), pick("A", 1), pick("B", 2)]}, pool)
    assert [r["ingredient_id"] for r in out] == ["A", "B", "C"]
    assert list(out[0]) == ["rank", "ingredient_id", "name", "reason", "evidence", "cautions"]
    assert out[0]["evidence"][0]["fact_id"] == "SYM_DIRECT:S>A" and out[0]["cautions"] == [{"by": "x", "text": "y"}]


def test_fallback_reason_never_forbidden():
    """사실 문장 원문에 '예방' 같은 금지 표현이 있으면 reason으로 그대로 내보내지 않는다(근거는 evidence에 남는다)."""
    c = cand("A", "비오틴", 0)
    c["facts"][0]["text"] = "탈모 → 비오틴 (탈모 예방과 모발 건강에 도움)"
    out = verify.fallback([c, cand("B", "비타민 B12", 1), cand("C", "홍경천 추출물", 2)], 3)
    assert verify.check({"picks": out}, [c, cand("B", "비타민 B12", 1), cand("C", "홍경천 추출물", 2)], set(), 3) == []
    assert "예방" not in out[0]["reason"] and out[0]["fact_ids"] == ["SYM_DIRECT:S>A"]
