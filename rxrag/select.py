"""⑤ 최종 선택 프롬프트·스키마. LLM은 금지 판정을 통과한 후보 안에서만 고르고, 성분마다 그 후보의 근거 사실 ID를 인용한다."""
from . import config

TASK = "select"
SEX = {"M": "남성", "F": "여성"}

SYSTEM = """당신은 약사가 환자 상담에 참고할 영양성분 {n}개를 우선순위대로 고릅니다.

[선택 규칙]
- 반드시 아래 후보 목록 안에서만 {n}개를 고릅니다. 목록에 없는 성분을 만들지 않습니다.
- 성분마다 fact_ids에 그 성분 행에 적힌 근거 fact_id를 1개 이상 그대로 인용합니다. 다른 성분의 fact_id는 쓰지 않습니다.
- 근거 강도 순서: 확인된 고갈(DRUG_SYM_DEPLETE, DRUG_DEPLETE)·증상 직접 완화(SYM_DIRECT) > 증상 분류 기능(CAT_FUNC) > 검진·질환 규칙(RULE_EXAM, RULE_COND, RULE_BORDER) > 관심사(INTEREST).
- 여러 근거가 겹쳐 환자의 여러 문제를 함께 커버하는 성분을 우대합니다.
- {n}개가 한 가지 문제에 몰리지 않도록 서로 다른 문제를 나눠 맡게 합니다.
- 주의 문구가 있는 성분은 비슷한 대안이 있으면 뒤로 미루거나 대안을 고릅니다.
- 이름이 같은 성분을 두 번 고르지 않고, 묶음 성분과 그 구성 성분을 함께 고르지 않습니다.
- rank는 1부터 {n}까지 한 번씩 씁니다(1이 가장 우선).

[reason 표현 규칙]
- 근거 사실에 있는 내용만 한두 문장으로 씁니다. 건강기능식품 기능성 범위로 "도움을 줄 수 있음"처럼 씁니다.
- 치료·완치·예방을 단정하지 않고, 용량을 쓰지 않으며, 복용 중인 약을 바꾸거나 끊으라고 하지 않습니다."""


def schema(pool, n=config.N):
    ids = [c["ingredient_id"] for c in pool]
    return {
        "type": "object", "additionalProperties": False, "required": ["picks"],
        "properties": {"picks": {"type": "array", "minItems": n, "maxItems": n, "items": {
            "type": "object", "additionalProperties": False, "required": ["ingredient_id", "rank", "fact_ids", "reason"],
            "properties": {"ingredient_id": {"type": "string", "enum": ids},
                           "rank": {"type": "integer", "enum": list(range(1, n + 1))},
                           "fact_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
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
    rows = ["ingredient_id | 이름 | 근거 fact_id: 문장 | 주의"]
    for c in sorted(pool, key=lambda c: c["rule_rank"]):
        facts = " / ".join(f"{f['fact_id']}: {f['text']}" for f in c["facts"])
        cautions = " / ".join(f"{x['by']}: {x['text']}" for x in c.get("cautions", [])) or "-"
        members = f" (묶음: {', '.join(c['members'])})" if c.get("members") else ""
        rows.append(f"{c['ingredient_id']} | {c['name']}{members} | {facts} | {cautions}")
    return "\n".join(rows)


def prompt(ctx, normalized, pool, n=config.N, conditions=()):
    user = f"[환자 요약]\n{patient_summary(ctx, normalized, conditions)}\n\n[후보 목록]\n{candidate_table(pool)}"
    return SYSTEM.format(n=n), user
