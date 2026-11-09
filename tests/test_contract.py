from rxrag.contract import STATUSES, result, validate


def test_missing_age_invalid():
    assert validate({"sex": "M"})[0] is None


def test_text_age_invalid():
    patient, errors = validate({"age": "팔십", "sex": "M"})
    assert patient is None and errors


def test_age_out_of_range_invalid():
    assert validate({"age": 130, "sex": "M"})[0] is None and validate({"age": -1, "sex": "F"})[0] is None


def test_missing_sex_invalid():
    assert validate({"age": 50})[0] is None


def test_unknown_sex_invalid():
    assert validate({"age": 50, "sex": "X"})[0] is None


def test_sex_alias_ok():
    assert validate({"age": "80", "sex": "남성"})[0]["age"] == 80


def test_drug_strings_wrapped():
    assert validate({"age": 50, "sex": "F", "drugs": ["와파린"]})[0]["drugs"] == [{"name": "와파린"}]


def test_other_keys_kept():
    assert validate({"age": 50, "sex": "F", "complaint": "피곤"})[0]["complaint"] == "피곤"


def test_result_shape():
    r = result("partial", message="m")
    assert list(r) == ["status", "message", "recommendations", "excluded", "warnings", "selector", "trace"]


def test_statuses():
    assert STATUSES == ("ok", "needs_review", "partial", "stopped", "invalid_input")
