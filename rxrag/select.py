"""⑤ 최종 선택 프롬프트·스키마. LLM은 금지 판정을 통과한 후보 안에서만 고르고, 성분마다 그 후보의 근거 사실 ID를 인용한다."""
from . import config
from .candidates import KIND_LABELS, tier

TASK = "select"
SEX = {"M": "남성", "F": "여성"}

SYSTEM = """당신은 약사가 환자 상담에 참고할 영양성분 {n}개를 우선순위대로 고릅니다.

[선택 규칙]
- 반드시 아래 후보 목록 안에서만 {n}개를 고릅니다. 목록에 없는 성분을 만들지 않습니다.
- 후보는 1순위(증상·검진·질환·복용약 근거)와 2순위(성별·연령 관심사만, 이름 옆에 "2순위"로 표시)로 나뉩니다. 1순위에서 서로 겹치지 않게 고를 수 있는 만큼 먼저 고르고, 1순위가 {n}개에 모자랄 때만 2순위로 나머지를 채웁니다.
- 성분마다 fact_ids에 그 성분 행에 적힌 근거 fact_id를 1개 이상 인용합니다. fact_id는 대괄호 [ ] 안의 문자열 전체이며, 콜론 앞의 종류(SYM_DIRECT 등)까지 포함해 그대로 씁니다. 다른 성분의 fact_id는 쓰지 않습니다.
- 근거 강도 순서: 확인된 고갈(DRUG_SYM_DEPLETE, DRUG_DEPLETE)·증상 직접 완화(SYM_DIRECT) > 증상 기전 근거(SYM_MECH: 공식 출처의 증상-기전 + 식약처 기능성) > 증상 분류 기능(CAT_FUNC) > 검진·질환 규칙(RULE_EXAM, RULE_COND, RULE_BORDER) > 관심사(INTEREST).
- 여러 근거가 겹쳐 환자의 여러 문제를 함께 커버하는 성분을 우대합니다.
- {n}개가 한 가지 문제에 몰리지 않도록 서로 다른 문제를 나눠 맡게 합니다.
- 주의 문구가 있는 성분은 비슷한 대안이 있으면 뒤로 미루거나 대안을 고릅니다.
- 이름이 같은 성분을 두 번 고르지 않고, 묶음 성분과 그 구성 성분을 함께 고르지 않습니다.
- rank는 1부터 {n}까지 한 번씩 씁니다(1이 가장 우선).

[reason 표현 규칙]
- 근거 사실에 있는 내용만 한두 문장으로 씁니다. 건강기능식품 기능성 범위로 "도움을 줄 수 있음"처럼 씁니다.
- 치료·완치·예방을 단정하지 않고, 용량을 쓰지 않으며, 복용 중인 약을 바꾸거나 끊으라고 하지 않습니다.
- 다음 단어는 reason에 쓰지 않습니다: 치료, 완치, 특효, 낫습니다, 낫게, 예방, 처방, 대체, 안전합니다, 개선됩니다, 숫자+단위(mg, g, IU, 정, 캡슐 등).
- 근거 문장에 이 단어(특히 "예방")가 들어 있어도 reason에 옮겨 쓰지 말고 "도움을 줄 수 있음"처럼 바꿔 씁니다."""


def schema(pool, n=config.N):
    ids = [c["ingredient_id"] for c in pool]
    fact_ids = [f["fact_id"] for c in pool for f in c["facts"]]  # 형식이 틀린 ID를 낼 수 없게 한다. 소속 성분 대조는 verify가 한다
    return {
        "type": "object", "additionalProperties": False, "required": ["picks"],
        "properties": {"picks": {"type": "array", "minItems": n, "maxItems": n, "items": {
            "type": "object", "additionalProperties": False, "required": ["ingredient_id", "rank", "fact_ids", "reason"],
            "properties": {"ingredient_id": {"type": "string", "enum": ids},
                           "rank": {"type": "integer", "enum": list(range(1, n + 1))},
                           "fact_ids": {"type": "array", "minItems": 1, "items": {"type": "string", "enum": fact_ids}},
                           "reason": {"type": "string"}}}}},
    }


def patient_summary(ctx, normalized, conditions=()):
    """식별자 없이 성별·연령대·증상·분류·검진 이상·복용약·질환. 나이는 연령대로만."""
    lines = [f"- 성별·연령대: {SEX.get(ctx['sex'], ctx['sex'])}, {int(ctx['age']) // 10 * 10}대"]
    symptoms = [s["name"] for s in ctx.get("symptoms", [])]
    categories = sorted(set(ctx.get("categories", [])) | set((normalized or {}).get("categories", [])))
    drugs = sorted({d["item"] for d in ctx.get("drugs", {}).get("matched", [])})
    for label, values in (("증상", symptoms), ("증상 분류(키워드 없음)", categories),
                          ("검진 이상", [e["label"] for e in ctx.get("exams", [])]), ("복용약", drugs),
                          ("질환", list(conditions))):
        lines.append(f"- {label}: {', '.join(values) if values else '없음'}")
    return "\n".join(lines)


def candidate_table(pool):
    rows = ["ingredient_id | 이름 | 근거 [fact_id] (종류) 문장 | 주의"]
    for c in sorted(pool, key=lambda c: c["rule_rank"]):
        facts = " / ".join(f"[{f['fact_id']}] ({KIND_LABELS[f['kind']]}) {f['text']}" for f in c["facts"])
        cautions = " / ".join(f"{x['by']}: {x['text']}" for x in c.get("cautions", [])) or "-"
        members = f" (묶음: {', '.join(c['members'])})" if c.get("members") else ""
        second = " (2순위: 관심사만)" if tier(c) == 2 else ""
        rows.append(f"{c['ingredient_id']} | {c['name']}{members}{second} | {facts} | {cautions}")
    return "\n".join(rows)


def prompt(ctx, normalized, pool, n=config.N, conditions=()):
    user = f"[환자 요약]\n{patient_summary(ctx, normalized, conditions)}\n\n[후보 목록]\n{candidate_table(pool)}"
    return SYSTEM.format(n=n), user
