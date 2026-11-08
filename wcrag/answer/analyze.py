"""질문 분석. 검색하기 전에 LLM에게 세 가지를 묻는다.

1. 위험 신호인가 — 맞으면 검색도 답변도 하지 않고 멈춘다.
2. 질문에 환자 단서(나이, 성별, 약, 질환)가 있는가.
3. 질문이 어떤 개체(원료, 약, 증상, 질환, 조건)를 언급하는가 — 검색의 출발점이 된다.
4. 어느 검색 방식이 맞는가.
"""
from typing import Literal, Optional

from pydantic import BaseModel

from .. import config

INSTRUCTIONS = """당신은 약국 상담용 질의응답 시스템의 첫 단계입니다. 약사의 질문을 읽고 아래를 판단합니다. 답변은 쓰지 않습니다.

[위험 신호]
지금 바로 진료가 필요한 상태가 질문이나 [환자 주호소]에 적혀 있으면 red_flag를 true로 하고 red_flag_reason에 한 문장으로 이유를 씁니다.
- 예: 가슴 통증·압박감과 식은땀, 한쪽 팔다리 마비나 힘 빠짐, 갑자기 말이 어눌함, 얼굴 한쪽 처짐, 숨이 막힘,
  토혈·혈변·검은 변·혈뇨, 멈추지 않는 출혈, 실신, 갑작스러운 시력 상실, 인생에서 가장 심한 두통, 고열과 목 경직,
  약 복용 뒤 입술·혀 부종과 호흡곤란, 죽고 싶다는 생각, 근육통과 콜라색 소변, 매우 높은 혈당과 계속되는 구토.
- 여러 증상 중 하나라도 위 범주면 true입니다.
- 다음은 위험 신호가 아닙니다: 과거에 있었고 지금은 없는 증상, 검사에서 정상으로 확인된 증상, 환자 본인이 아닌 사람의 일,
  "피곤해 죽겠다"처럼 과장된 표현, 오래된 가벼운 증상.
- 위험 신호가 아니면 red_flag는 false, red_flag_reason은 빈 문자열입니다.

[환자 단서]
질문에 특정 환자의 정보가 있으면 patient에 적습니다. 적힌 것만 옮기고 추측하지 않습니다.
- age: 나이(숫자). 없으면 null.  sex: 남성 "M", 여성 "F". 없으면 null.
- conditions: 질환명.  drugs: 복용약 이름이나 계열.
- pregnant, lactating: 임신·수유가 적혀 있으면 true, 아니라고 적혀 있으면 false, 언급이 없으면 null.
- 환자 정보가 전혀 없는 일반 질문이면 patient는 null입니다.

[언급한 개체]
질문이 가리키는 원료·약·증상·질환·조건의 이름을 mentions에 적습니다. 검색의 출발점으로 쓰므로 데이터베이스에서 찾기 좋은
표준 이름으로 씁니다. 말로 풀어 쓴 증상은 표준 증상명으로 바꿉니다(예: "다리에 쥐가 난다" → "근육경련", "속이 더부룩하다" → "소화불량").
약은 성분명이나 계열명으로(예: "혈압약" → "혈압강하제"), 상태는 조건 이름으로(예: "임신 중" → "임산부") 씁니다.
질문에 나온 것만 적고, 답이 될 만한 원료를 추측해서 적지 않습니다. 없으면 빈 목록입니다.

[검색 방식] modes에 하나 이상 고릅니다.
- local: 특정 원료·약·증상·질환에 대한 사실이나, 둘 이상을 이어서 묻는 질문.
- cypher: 개수, 전체 목록, 가장 많은 것처럼 세거나 모두 나열해야 하는 질문.
- global: 여러 대상을 묶어 전체 그림을 요약해 달라는 질문."""


class PatientHint(BaseModel):
    age: Optional[int]
    sex: Optional[Literal["M", "F"]]
    conditions: list[str]
    drugs: list[str]
    pregnant: Optional[bool]
    lactating: Optional[bool]


class Route(BaseModel):
    red_flag: bool
    red_flag_reason: str
    mentions: list[str]
    modes: list[Literal["local", "cypher", "global"]]
    patient: Optional[PatientHint]


def analyze(question: str, complete, complaint: str | None = None) -> dict:
    """Route를 dict로 돌려준다. 아직 켜지 않은 검색 방식은 걸러 내고, 남는 게 없으면 기본 방식을 쓴다.

    환자 정보에 주호소 문장이 있으면 질문 뒤에 붙여, 위험 신호를 질문과 주호소 양쪽에서 본다.
    """
    payload = f"{question}\n\n[환자 주호소]\n{complaint}" if complaint else question
    route = complete(INSTRUCTIONS, payload, Route, tag="analyze").model_dump()
    modes = [m for m in dict.fromkeys(route["modes"]) if m in config.ENABLED_MODES]
    route["modes"] = modes or config.ENABLED_MODES[:1]
    return route
