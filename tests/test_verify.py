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


def test_errors_name_ingredients_in_korean():
    errors = verify.check(picks(pick("A", 1, facts=["SYM_DIRECT:S>B"]), pick("B", 2), pick("C", 3)), POOL, set(), 3)
    assert errors and "테아닌" in errors[0]


# --- 리뷰에서 미룬 minor: 형식이 깨진 응답은 예외 대신 오류 문구로 ---
def test_non_dict_pick_is_error_not_exception():
    errors = verify.check({"picks": ["A", pick("B", 2), pick("C", 3)]}, POOL, set(), 3)
    assert errors and any("객체" in e for e in errors)


def test_missing_reason_is_error():
    p = pick("A", 1)
    del p["reason"]
    assert any("reason" in e for e in verify.check(picks(p, pick("B", 2), pick("C", 3)), POOL, set(), 3))


def test_blank_reason_is_error():
    assert verify.check(picks(pick("A", 1, reason="  "), pick("B", 2), pick("C", 3)), POOL, set(), 3)


def test_fact_ids_string_is_error():
    p = pick("A", 1, facts="SYM_DIRECT:S>A")
    assert any("fact_ids" in e for e in verify.check(picks(p, pick("B", 2), pick("C", 3)), POOL, set(), 3))


def test_unhashable_ingredient_id_is_error():
    p = pick("A", 1)
    p["ingredient_id"] = ["A"]
    assert verify.check(picks(p, pick("B", 2), pick("C", 3)), POOL, {"C"}, 3)


# --- 이름 중복: 괄호 속 제품명·추출물/분말 접미어·별칭이 달라도 같은 원료면 중복 ---
def _two(a, b):
    pool = [cand("X", a, 0), cand("Y", b, 1), cand("C", "셀레늄", 2)]
    return verify.check(picks(pick("X", 1), pick("Y", 2), pick("C", 3)), pool, set(), 3)


def test_same_raw_material_different_product_tag():
    assert _two("아쉬아간다 추출물", "아쉬아간다추출물(Sensoril®)((주)노바렉스, 제2024-30호)")


def test_suffix_variants_clash():
    assert _two("루테인지아잔틴복합추출물20%", "루테인지아잔틴추출복합물")


def test_alias_in_parentheses_or_hyphen_clash():
    assert _two("사탕수수왁스알코올(폴리코사놀)", "폴리코사놀-사탕수수왁스알코올")


def test_different_ingredients_do_not_clash():
    for a, b in [("인삼", "인삼가수분해 농축액"), ("홍삼", "홍삼추출효소처리분말"), ("비타민 B1", "비타민 B12"),
                 ("크릴오일(FJH-KO)", "크릴오일등복합물(FlexPro MDⓇ)"), ("녹차추출물 (카테킨)", "테아닌")]:
        assert _two(a, b) == [], (a, b)


# 계층 채우기: 1순위(증상·검진·질환·복용약)에서 고를 수 있는 만큼 먼저, 모자랄 때만 2순위(관심사)
def interest(iid, name, rank):
    c = cand(iid, name, rank)
    c["facts"] = [{"fact_id": f"INTEREST:SEG>{iid}", "kind": "INTEREST", "text": f"{name} 관심사", "source": "s"}]
    return c


def ipick(iid, rank):
    return pick(iid, rank, facts=[f"INTEREST:SEG>{iid}"])


TIERED = [cand("A", "테아닌", 0), cand("B", "비타민 B12", 1), cand("C", "홍경천 추출물", 2),
          interest("X", "칼슘", 3), interest("Y", "비타민 D", 4)]


def test_interest_rejected_when_primary_enough():
    errors = verify.check(picks(pick("A", 1), pick("B", 2), ipick("X", 3)), TIERED, set(), 3)
    assert any("1순위" in e for e in errors)


def test_primary_only_passes():
    assert verify.check(picks(pick("A", 1), pick("B", 2), pick("C", 3)), TIERED, set(), 3) == []


def test_interest_fills_when_primary_short():
    pool = TIERED[:2] + TIERED[3:]
    assert verify.check(picks(pick("A", 1), pick("B", 2), ipick("X", 3)), pool, set(), 3) == []


def test_must_take_all_primary_when_short():
    pool = TIERED[:2] + TIERED[3:]
    errors = verify.check(picks(pick("A", 1), ipick("X", 2), ipick("Y", 3)), pool, set(), 3)
    assert any("1순위" in e for e in errors)


def test_primary_counted_without_duplicates():
    pool = [cand("I1", "인삼", 0), cand("I2", "인 삼", 1), cand("A", "테아닌", 2), interest("X", "칼슘", 3)]
    assert verify.check(picks(pick("I1", 1), pick("A", 2), ipick("X", 3)), pool, set(), 3) == []


def test_interest_only_pool_passes():
    pool = [interest("X", "칼슘", 0), interest("Y", "비타민 D", 1), interest("Z", "마그네슘", 2)]
    assert verify.check(picks(ipick("X", 1), ipick("Y", 2), ipick("Z", 3)), pool, set(), 3) == []
