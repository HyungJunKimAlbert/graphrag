from rxrag import select

CTX = {"sex": "M", "age": 80.0, "symptoms": [{"symptom_id": "S1", "name": "새벽 각성"}], "categories": ["혈당"],
       "exams": [{"code": "h-38", "label": "공복혈당 장애"}], "segment": {"code": "M_AGE_65P", "name": "남성 65세 이상"},
       "drugs": {"matched": [{"input": "메트포르민", "item": "메트포르민"}], "full": ["DRUG-metformin"]}}
NORM = {"symptoms": ["새벽 각성"], "categories": ["혈당"], "unmatched": [], "red_flag": False, "red_flag_reason": ""}


def cand(iid, name, facts, cautions=()):
    return {"ingredient_id": iid, "name": name, "type": "nutrient", "members": [], "tier_rank": 0, "rule_rank": 0,
            "facts": [{"fact_id": f, "kind": f.split(":")[0], "text": f"{name} 근거 {i}", "source": None}
                      for i, f in enumerate(facts)], "cautions": list(cautions)}


POOL = [cand("A", "테아닌", ["SYM_DIRECT:S1>A"]),
        cand("B", "비타민 B12", ["DRUG_SYM_DEPLETE:D>S1>B", "DRUG_DEPLETE:D>B"]),
        cand("C", "홍경천 추출물", ["SYM_DIRECT:S1>C"], [{"by": "와파린", "text": "상담 필요"}]),
        cand("D", "엽산", ["DRUG_DEPLETE:D>D"])]


def test_schema_enum_is_pool_ids():
    s = select.schema(POOL, 3)
    assert s["properties"]["picks"]["items"]["properties"]["ingredient_id"]["enum"] == [c["ingredient_id"] for c in POOL]
    assert s["properties"]["picks"]["minItems"] == 3 and s["properties"]["picks"]["maxItems"] == 3


def test_schema_requires_fact_ids():
    item = select.schema(POOL, 3)["properties"]["picks"]["items"]
    assert item["properties"]["fact_ids"]["minItems"] == 1 and set(item["required"]) == {"ingredient_id", "rank",
                                                                                         "fact_ids", "reason"}


def test_prompt_lists_every_fact():
    _, u = select.prompt(CTX, NORM, POOL)
    assert all(f["fact_id"] in u for c in POOL for f in c["facts"])


def test_prompt_shows_cautions():
    _, u = select.prompt(CTX, NORM, POOL)
    assert "상담 필요" in u


def test_prompt_has_no_excluded():
    _, u = select.prompt(CTX, NORM, POOL[:3])
    assert "엽산" not in u


def test_system_has_selection_rules():
    s, _ = select.prompt(CTX, NORM, POOL)
    assert "후보" in s and "fact_id" in s and "3개" in s


def test_summary_no_exact_age():
    text = select.patient_summary(CTX, NORM)
    assert "80대" in text and "80세" not in text and "80.0" not in text
    assert "새벽 각성" in text and "공복혈당 장애" in text and "메트포르민" in text


def test_summary_conditions():
    assert "당뇨" in select.patient_summary(CTX, NORM, conditions=["당뇨"])
