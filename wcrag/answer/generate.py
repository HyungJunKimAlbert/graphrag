"""답변 생성. 검색으로 모은 문맥만 보고 LLM이 답을 쓴다.

LLM은 문장마다 어느 근거를 썼는지(evidence_ids) 적어야 하고, 문장에 나온 성분을 '권함'과 '주의' 중 하나로 분류해야 한다.
성분 ID 칸은 문맥에 있는 ID만 고를 수 있는 형식으로 만들어, 문맥에 없는 성분을 끼워 넣지 못하게 한다.
"""
from typing import Literal

from pydantic import BaseModel, create_model

KIND_NAMES = {"Ingredient": "원료", "Symptom": "증상", "Drug": "약", "DrugItem": "약 표기", "Condition": "조건",
              "FunctionTag": "기능성 분류", "SymptomCategory": "증상 분류", "Interest": "관심사", "Segment": "성별·연령 구분",
              "Disease": "질환", "LifeStage": "생애주기", "Mechanism": "기전", "Company": "회사"}
DOC_KINDS = {"function": "기능성", "intake": "섭취량", "precaution": "주의사항", "reason": "추천 이유", "drug": "약 정보",
             "rule": "설명", "food": "함유 식품"}

INSTRUCTIONS = """당신은 약사가 상담에 참고할 답을 쓰는 건강기능식품 질의응답 도우미입니다. 아래 규칙을 모두 지킵니다.

[근거]
- [사실]과 [문서]에 있는 내용만 씁니다. 여기에 없는 효능, 수치, 성분을 만들지 않습니다.
- 문장 하나마다 그 문장의 근거가 된 ID를 evidence_ids에 하나 이상 적습니다. ID는 줄 맨 앞에 적힌 그대로 씁니다.
- 주어진 근거로 질문에 답할 수 없으면 answerable을 false로 하고 claims는 비웁니다.
- 질문이 묻는 대상이 [찾은 개체]에 없으면, 이름이 비슷한 다른 대상으로 대신 답하지 않고 answerable을 false로 합니다.
- "연결된 관계가 없음"으로 표시된 개체는 그래프에 연결된 근거가 없다는 뜻입니다. 그 개체에 대해서는 "연결된 근거가 없다"고만
  쓰고, 효과가 없다거나 안전하다고 쓰지 않습니다. 그 문장의 근거로는 그 개체 줄 맨 앞의 ID(예: Symptom:SYM-...)를 적습니다.
  연결된 관계가 있는 개체의 ID는 근거로 쓸 수 없고, 그 개체의 [사실]과 [문서] ID를 써야 합니다.

[성분 분류]
- 문장에서 이름을 언급한 성분은 [성분 ID]에서 ID를 찾아 반드시 둘 중 하나에 넣습니다.
  recommended_ingredient_ids: 검토하거나 권할 수 있다고 쓴 성분.
  cautioned_ingredient_ids: 피해야 하거나 주의가 필요하다고 쓴 성분, 그리고 권하는 것이 아니라 사실만 전한 성분.
- "[이 환자에게 금기: ...]"가 붙은 성분은 recommended_ingredient_ids에 넣지 않습니다. 피해야 한다고 알리는 것은 됩니다.

[표현]
- 건강기능식품 기능성 표시 범위로 씁니다. "도움을 줄 수 있음"처럼 쓰고, 치료·완치·예방·개선을 단정하지 않습니다.
- "치료", "완치", "예방", "처방", "대체", "특효" 같은 낱말을 쓰지 않습니다.
- 약을 줄이거나 끊으라는 말을 하지 않습니다.
- 용량 숫자(mg, g, μg, IU, 정, 캡슐 등)는 섭취량 문서의 원문에 있는 숫자를 그대로 옮길 때만 쓰고, 그 문서 ID를 evidence_ids에 넣습니다.
- 약사에게 말하듯 짧고 분명하게 씁니다. 문장은 5개 이내입니다."""


class Claim(BaseModel):
    text: str
    evidence_ids: list[str]


def draft_schema(ingredient_ids: list[str]) -> type[BaseModel]:
    """성분 ID 칸의 선택지를 문맥의 성분으로 제한한 출력 형식. 성분이 없으면 그 칸을 아예 두지 않는다."""
    fields = {"answerable": (bool, ...), "claims": (list[Claim], ...)}
    if ingredient_ids:
        choice = Literal[tuple(ingredient_ids)]
        fields.update(recommended_ingredient_ids=(list[choice], ...), cautioned_ingredient_ids=(list[choice], ...))
    return create_model("Draft", **fields)


def kind_name(kind: str) -> str:
    return KIND_NAMES.get(kind, kind)


def render(question: str, context: dict, verdicts: dict | None = None) -> str:
    """LLM에 보낼 글. 줄 맨 앞이 근거 ID다."""
    avoid = {iid: v for iid, v in (verdicts or {}).get("by_ingredient", {}).items() if not v["allowed"]}
    lines = [f"[질문]\n{question}", "", "[찾은 개체]"]
    for seed in context["seeds"]:
        if seed["isolated"]:
            lines.append(f"{seed['key']} | {seed['name']} ({kind_name(seed['kind'])}) — 연결된 관계가 없음")
        else:
            lines.append(f"- {seed['name']} ({kind_name(seed['kind'])})")
    lines += ["", "[사실]"]
    for fact in context["facts"]:
        tag = ""
        for iid, verdict in avoid.items():
            if f"Ingredient:{iid}" in (fact.get("start"), fact.get("end")):
                tag = f" [이 환자에게 금기: {'; '.join(f['text'] for f in verdict['avoid'])}]"
        lines.append(f"{fact['id']} | {fact['text']}{tag}")
    lines += ["", "[문서]"]
    for doc in context["docs"]:
        name = context["ingredient_names"].get(doc.get("ingredient_id"), "")
        head = " · ".join(p for p in (DOC_KINDS.get(doc["kind"], doc["kind"]), name) if p)
        lines.append(f"{doc['id']} | ({head}) {' '.join(doc['text'].split())}")
    lines += ["", "[성분 ID]"]
    for iid in context["ingredient_ids"]:
        tag = f" [이 환자에게 금기: {'; '.join(f['text'] for f in avoid[iid]['avoid'])}]" if iid in avoid else ""
        lines.append(f"{iid} = {context['ingredient_names'].get(iid, iid)}{tag}")
    return "\n".join(lines)


def generate(question: str, context: dict, complete, verdicts: dict | None = None) -> dict:
    schema = draft_schema(context["ingredient_ids"])
    out = complete(INSTRUCTIONS, render(question, context, verdicts), schema, tag="generate").model_dump()
    return {"answerable": out["answerable"], "claims": out["claims"],
            "recommended_ingredient_ids": list(dict.fromkeys(out.get("recommended_ingredient_ids", []))),
            "cautioned_ingredient_ids": list(dict.fromkeys(out.get("cautioned_ingredient_ids", [])))}
