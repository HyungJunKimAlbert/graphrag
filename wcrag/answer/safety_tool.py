"""금기 판정 도구. 기존 추천 엔진의 판정을 그대로 가져온다.

GraphRAG가 무엇을 찾고 LLM이 무엇을 쓰든, "이 환자에게 이 성분이 금기인가"는 여기서 규칙으로만 정한다.
XEntity·X_RELATED(LLM이 추출한 층)는 이 판정에 쓰지 않는다.
"""
from .. import legacy

PREGNANCY_UNKNOWN = "임신·수유 여부가 입력되지 않았습니다. 해당하면 차단 규칙이 달라집니다."
Q_ALL_INGREDIENTS = "MATCH (i:Ingredient) RETURN i.ingredient_id ORDER BY i.ingredient_id"


def usable(patient: dict | None) -> bool:
    """판정에 꼭 필요한 나이와 성별이 있는가. 없으면 성별·연령 구간 규칙을 적용할 수 없다."""
    return bool(patient) and patient.get("age") is not None and bool(patient.get("sex"))


def from_hint(hint: dict | None) -> dict | None:
    """질문에서 뽑은 환자 단서를 엔진의 환자 입력 형식으로 바꾼다."""
    if not hint:
        return None
    return {"age": hint["age"], "sex": hint["sex"], "conditions": hint["conditions"],
            "drugs": [{"name": name} for name in hint["drugs"]], "pregnant": hint["pregnant"],
            "lactating": hint["lactating"]}


def merge_patient(patient: dict | None, hint: dict | None) -> dict | None:
    """넘겨받은 환자 정보와 질문에서 뽑은 단서를 합친다.
    나이·성별·임신·수유 같은 신원은 환자 정보가 이기고, 약·질환은 합집합이다 — 질문에만 적힌 약이 빠지면 금기를 놓친다."""
    hinted = from_hint(hint)
    if not patient:
        return hinted
    if not hinted:
        return patient
    merged = {**hinted, **patient}
    seen = {d["name"] for d in patient.get("drugs", [])}
    merged["drugs"] = list(patient.get("drugs", [])) + [d for d in hinted["drugs"] if d["name"] not in seen]
    merged["conditions"] = list(dict.fromkeys(list(patient.get("conditions", [])) + list(hinted["conditions"])))
    return merged


def verdicts(graph, patient: dict, ingredient_ids: list[str]) -> dict:
    """{by_ingredient: {id: {allowed, avoid, caution}}, incomplete: [판정이 닿지 못한 입력]}"""
    ctx = legacy.build_context(graph, patient)
    by_ingredient = legacy.safety_check(graph, ingredient_ids, ctx) if ingredient_ids else {}
    return {"by_ingredient": by_ingredient, "incomplete": incomplete(ctx)}


def incomplete(ctx: dict) -> list[str]:
    """판정이 닿지 못한 입력. 엔진의 불완전 문구에 임신·수유 미입력을 더한다."""
    reasons = legacy.incomplete_reasons(ctx)
    if ctx["pregnancy_unknown"]:
        reasons.append(PREGNANCY_UNKNOWN)
    return reasons


def avoid_ids(graph, patient: dict) -> list[str]:
    """이 환자에게 금기인 성분 ID 전부. 평가 정답의 '권하면 안 되는 성분'을 만들 때 쓴다."""
    ids = [row[0] for row in graph.read(Q_ALL_INGREDIENTS)]
    by_ingredient = verdicts(graph, patient, ids)["by_ingredient"]
    return sorted(iid for iid, verdict in by_ingredient.items() if not verdict["allowed"])
