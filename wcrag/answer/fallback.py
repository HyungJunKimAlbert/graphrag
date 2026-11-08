"""대체 추천. 질문에 답할 근거가 없을 때 나이·성별 → 구분(Segment) → 관심사 → 성분으로 일반 추천을 만든다.

LLM을 쓰지 않는다. 후보는 기존 엔진의 관심사 생성기(candidates.interest), 금기는 엔진의 판정을 그대로 쓰므로
같은 환자면 엔진의 '관심사' 단계와 같은 성분이 나온다. 이 추천은 개인 맞춤이 아니라 구분별 기본값이다.
"""
from .. import legacy
from . import safety_tool

N = 3  # 엔진 DEFAULT_N과 같다


def recommend(graph, person: dict | None, n: int = N) -> dict | None:
    """{segment, picked: [{ingredient_id, name, paths, cautions}], blocked: [{..., reasons}], incomplete}.
    나이·성별이 없거나 맞는 구분이 없으면 None."""
    if not safety_tool.usable(person):
        return None
    ctx = legacy.build_context(graph, person)
    if not ctx["segment"]:
        return None
    cands = {}
    for cand in legacy.interest_candidates(graph, ctx):
        known = cands.get(cand["ingredient_id"])
        if known:  # 관심사 둘에 같은 성분이 있으면 앞 순위로 한 번만 내고 근거를 합친다
            known["paths"] += [p for p in cand["paths"] if p not in known["paths"]]
        else:
            cands[cand["ingredient_id"]] = {"ingredient_id": cand["ingredient_id"], "name": cand["name"],
                                            "paths": list(cand["paths"])}
    verdicts = legacy.safety_check(graph, list(cands), ctx) if cands else {}
    picked, blocked = [], []
    for iid, cand in cands.items():
        verdict = verdicts[iid]
        if not verdict["allowed"]:
            blocked.append({**cand, "reasons": verdict["avoid"]})
        elif len(picked) < n:
            picked.append({**cand, "cautions": verdict["caution"]})
    return {"segment": ctx["segment"], "picked": picked, "blocked": blocked,
            "incomplete": safety_tool.incomplete(ctx)}
