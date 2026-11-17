"""입력 계약과 출력 조립. 엔진 build_context는 age·sex를 그대로 읽으므로 그 앞에서 막는다."""
from .legacy import SEX_KEYS

STATUSES = ("ok", "needs_review", "partial", "stopped", "invalid_input")


def _age(value):
    try:
        age = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not 0 <= age <= 120:
        return None
    return int(age) if age.is_integer() else age


def _drugs(drugs):
    out = []
    for d in drugs or []:
        if isinstance(d, str):
            out.append({"name": d})
        elif isinstance(d, dict) and d.get("name"):
            out.append(d)
        else:
            return None
    return out


def _texts(value):
    """문자열 하나는 [문자열]로 감싼다(엔진이 원소마다 쉼표로 나눈다). 문자열 리스트가 아니면 None."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    return None


def validate(patient):
    """(정리된 patient, []) 또는 (None, 오류 문구들)."""
    if not isinstance(patient, dict):
        return None, ["입력이 JSON 객체가 아닙니다."]
    errors = []
    age = _age(patient.get("age")) if patient.get("age") is not None else None
    if age is None:
        errors.append("age가 없거나 0~120 사이의 숫자가 아닙니다.")
    sex = SEX_KEYS.get(str(patient.get("sex", "")).strip().lower().replace(" ", ""))
    if sex is None:
        errors.append("sex가 없거나 알 수 없는 값입니다(M/F, 남성/여성 등).")
    drugs = _drugs(patient.get("drugs"))
    if drugs is None:
        errors.append("drugs는 약 이름 문자열 또는 {name} 객체의 리스트여야 합니다.")
    lists = {}
    for key in ("conditions", "symptoms"):
        if key in patient:
            lists[key] = _texts(patient[key])
            if lists[key] is None:
                errors.append(f"{key}는 문자열 또는 문자열 리스트여야 합니다.")
    if patient.get("complaint") is not None and not isinstance(patient["complaint"], str):
        errors.append("complaint는 문자열이어야 합니다.")
    if errors:
        return None, errors
    return {**patient, **lists, "age": age, "sex": sex, "drugs": drugs}, []


def result(status, recommendations=(), excluded=(), warnings=(), selector=None, trace=None, message=None):
    assert status in STATUSES, status
    return {"status": status, "message": message, "recommendations": list(recommendations), "excluded": list(excluded),
            "warnings": list(warnings), "selector": selector, "trace": dict(trace or {})}
