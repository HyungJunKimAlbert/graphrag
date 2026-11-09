"""실제 샘플 형식(상담 화면 구조) → rxrag 입력. 엔진 screen_input.from_screen을 쓰고 두 가지를 보탠다.

- 주호소: 엔진 변환은 주호소를 버린다. chief_complaint.text를 complaint로 넣는다
- 질환 이름: 엔진은 질환 문자열을 쉼표로 쪼갠다. 괄호 안 쉼표를 'ㆍ'(한글 아래아, 엔진 구분자 아님)로 바꿔 '심혈관질환(협심증, 심근경색)'이 잘리지 않게 한다
누락 섹션(missing)은 엔진이 경고로 낸다. 관리질환(managed_conditions)과 동반질환(comorbidities)은 엔진처럼 conditions 하나로 합친다(금기 판정은 구분하지 않음).
"""
import re

from .legacy import from_screen

SCREEN_KEYS = ("managed_conditions", "comorbidities", "chief_complaint", "checkup", "medications")


def is_screen(patient):
    return isinstance(patient, dict) and any(k in patient for k in SCREEN_KEYS)


def _protect(name):
    return re.sub(r"\(([^)]*)\)", lambda m: "(" + re.sub(r"\s*,\s*", "ㆍ", m.group(1)) + ")", str(name))


def adapt(record):
    out = from_screen(record)
    out["conditions"] = [_protect(c) for c in out["conditions"]]
    text = ((record.get("chief_complaint") or {}).get("text") or "").strip()
    if text:
        out["complaint"] = text
    return out
