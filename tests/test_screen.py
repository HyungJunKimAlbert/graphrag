"""실제 샘플 형식(상담 화면 구조: 관리질환·동반질환·주호소 객체·검진 항목 리스트) → rxrag 입력."""
import pytest

from rxrag import screen

RECORD = {"patient_id": "kang", "age": 71, "sex": "M", "managed_conditions": ["고혈압"],
          "comorbidities": ["이상지질혈증(콜레스테롤)", "심혈관질환(협심증, 심근경색)"],
          "chief_complaint": {"text": "다리 근육통, 밤에 자주 깸", "date": "2026-09-28"},
          "checkup": {"date": "2024-11-20", "items": [{"category": "당뇨병", "item": "공복혈당", "value": 132}]},
          "prescription": {"date": "2026-10-08"},
          "medications": [{"name": "와파린", "class": "항응고제"}]}


def test_is_screen():
    assert screen.is_screen(RECORD) and not screen.is_screen({"age": 80, "sex": "M", "conditions": []})


def test_adapt_merges_conditions_and_keeps_commas_inside_parentheses():
    p = screen.adapt(RECORD)
    # 엔진은 질환 문자열을 쉼표로 쪼갠다. 괄호 안 쉼표를 'ㆍ'로 바꿔 '심혈관질환(협심증'처럼 잘리지 않게 한다
    assert p["conditions"] == ["고혈압", "이상지질혈증(콜레스테롤)", "심혈관질환(협심증ㆍ심근경색)"]
    assert p["complaint"] == "다리 근육통, 밤에 자주 깸"
    assert p["exam"] == {"공복혈당": 132} and p["drugs"] == [{"name": "와파린", "drug_class": "항응고제"}]
    assert p["age"] == 71 and p["sex"] == "M" and p["missing"] == []


def test_adapt_empty_complaint_and_missing_sections():
    p = screen.adapt({**RECORD, "chief_complaint": {"text": None, "date": None}, "prescription": None,
                      "managed_conditions": None, "comorbidities": None, "medications": None})
    assert "complaint" not in p and p["conditions"] == [] and p["missing"] == ["문진", "처방"]


@pytest.mark.graph
def test_recommend_accepts_screen_record_and_warns_missing():
    from rxrag.recommend import recommend
    from tests.test_recommend import smart
    r = recommend({**RECORD, "prescription": None, "medications": None}, smart())
    assert r["status"] == "needs_review"
    assert sum("처방" in w for w in r["warnings"]) == 1  # 누락 경고는 엔진이 한 번만 낸다
    assert any("심혈관질환(협심증ㆍ심근경색)" in w for w in r["warnings"])  # 잘리지 않은 이름 그대로
