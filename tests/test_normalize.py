from rxrag import normalize, taxonomy
from rxrag.llm import FakeLLM

CATS = taxonomy.load()


def reply(matches, unmatched=(), red_flag=False, reason=""):
    return {"matches": matches, "unmatched": list(unmatched), "red_flag": red_flag, "red_flag_reason": reason}


def test_no_complaint_skips_llm():
    f = FakeLLM({})
    assert normalize.run({"age": 80, "sex": "M"}, f, CATS) is None and f.calls == []


def test_blank_complaint_skips_llm():
    f = FakeLLM({})
    assert normalize.run({"age": 80, "sex": "M", "complaint": "  "}, f, CATS) is None and f.calls == []


def test_flatten_rules():
    f = FakeLLM({"normalize": reply([{"category": "스트레스/수면", "symptoms": ["새벽 각성"]},
                                     {"category": "혈당", "symptoms": []}])})
    n = normalize.run({"age": 80, "sex": "M", "complaint": "밤에 자주 깸"}, f, CATS)
    assert n["symptoms"] == ["새벽 각성"] and n["categories"] == ["혈당"]
    assert n["red_flag"] is False and n["unmatched"] == []


def test_unknown_category_dropped():
    f = FakeLLM({"normalize": reply([{"category": "수면", "symptoms": ["새벽 각성"]}])})
    n = normalize.run({"age": 80, "sex": "M", "complaint": "밤에 자주 깸"}, f, CATS)
    assert n["symptoms"] == [] and n["categories"] == []
    assert n["unmatched"] == [{"text": "수면: 새벽 각성", "kind": "기타"}]


def test_red_flag_passed_through():
    f = FakeLLM({"normalize": reply([], red_flag=True, reason="흉통")})
    n = normalize.run({"age": 60, "sex": "F", "complaint": "가슴이 조이듯 아파요"}, f, CATS)
    assert n["red_flag"] is True and n["red_flag_reason"] == "흉통"


def test_user_prompt_has_profile_not_exact_age():
    f = FakeLLM({"normalize": reply([])})
    normalize.run({"age": 83, "sex": "M", "complaint": "피곤"}, f, CATS)
    assert "남성, 80대" in f.calls[0]["user"] and "83" not in f.calls[0]["user"]


def test_schema_category_enum_is_taxonomy():
    enum = normalize.schema(CATS)["properties"]["matches"]["items"]["properties"]["category"]["enum"]
    assert enum == [c["category"] for c in CATS]


def test_merge_union():
    merged = normalize.merge({"symptoms": ["근육통"]}, {"symptoms": ["새벽 각성"], "categories": [], "unmatched": [],
                                                    "red_flag": False, "red_flag_reason": ""})
    assert merged["symptoms"] == ["근육통", "새벽 각성"]


def test_merge_without_complaint_uses_engine_default():
    assert normalize.merge({"symptoms": ["근육통"]}, None) is None


def test_prompt_has_negation_and_redflag_rules():
    s, _ = normalize.prompt("x", "남성, 80대", CATS)
    assert "없음" in s and "red_flag" in s and "분류표 밖 증상" in s and "스트레스/수면" in s


def test_red_flag_rule_covers_emergency_groups():
    """GPT가 짧은 목록에 있는 것만 잡았다(sf1-gpt 위험 신호 7건 누락). 응급 유형을 넓게 적고, 목록 밖이라도 같은 수준이면 잡게 한다."""
    from rxrag import normalize
    for term in ["식은땀", "가슴", "극심한 두통", "혈뇨", "콜라색", "복통", "알아보지", "자살", "시력", "출혈"]:
        assert term in normalize.SYSTEM, term
    assert "목록에 없더라도" in normalize.SYSTEM
