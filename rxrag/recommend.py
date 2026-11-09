"""진입점. 환자 JSON → 검증 → 주호소 정규화 → 후보·근거 → 금지 판정 → LLM 선택 → 코드 검증 → 결과 JSON.

MissingResponse(세션 응답 없음)는 잡지 않는다. 조용히 규칙 대체로 넘어가면 평가가 오염된다.
"""
from functools import lru_cache

from . import candidates, config, contract, graph, legacy, normalize, safety, select, taxonomy, verify

STOP_MESSAGE = "추천을 멈추고 진료를 권유합니다."


@lru_cache(maxsize=1)
def _cats():
    return tuple(taxonomy.load())


def warnings_for(ctx):
    """엔진 INCOMPLETE 문구 + 임신 미입력 문구(엔진 문구 그대로)."""
    out = [template.format(", ".join(ctx["unmatched"][key])) for key, template in legacy.INCOMPLETE
           if ctx["unmatched"].get(key)]
    if ctx.get("pregnancy_unknown"):
        out.append(legacy.PREGNANCY_UNKNOWN)
    return out


def named(picks, pool):
    """trace용: LLM 원응답의 각 선택에 성분 이름을 붙인다. 후보 밖 ID는 '후보 밖 성분'."""
    names = {c["ingredient_id"]: c["name"] for c in pool}
    items = picks.get("picks") if isinstance(picks, dict) else None
    if not isinstance(items, list):
        return [{"raw": picks}]
    return [{**p, "name": names.get(p.get("ingredient_id"), "후보 밖 성분")} if isinstance(p, dict) else {"raw": p}
            for p in items]


def choose(client, ctx, normalized, pool, excluded_ids, n, conditions):
    """LLM 선택 → 검증 → 1회 재시도 → 규칙 대체. (picks, selector, attempts)
    attempts에는 시도마다 LLM 원응답과 검증 오류를 남긴다(왜 거부·통과됐는지 추적용)."""
    system, user = select.prompt(ctx, normalized, pool, n, conditions)
    schema = select.schema(pool, n)
    attempts = []
    for attempt in (1, 2):
        prompt_user = user if attempt == 1 else user + "\n\n이전 응답의 문제: " + " / ".join(attempts[-1]["errors"])
        picks = client.complete_json(select.TASK, system, prompt_user, schema)
        errors = verify.check(picks, pool, excluded_ids, n)
        attempts.append({"attempt": attempt, "picks": named(picks, pool), "errors": errors})
        if not errors:
            return picks, "llm", attempts
    return {"picks": verify.fallback(pool, n)}, "fallback_rules", attempts


def recommend(patient, client, db=None, n=config.N, policy=config.CAUTION_POLICY):
    clean, errors = contract.validate(patient)
    if clean is None:
        return contract.result("invalid_input", message=" ".join(errors))
    db = db or graph.connect()
    cats = list(_cats())
    normalized = normalize.merge(clean, normalize.run(clean, client, cats))
    ctx = legacy.build_context(db, clean, normalized)
    trace = {"normalized": normalized, "rule_version": legacy.rule_version(db)}
    if ctx["red_flag"]:
        return contract.result("stopped", message=STOP_MESSAGE,
                               trace={**trace, "red_flag_reason": ctx["red_flag_reason"]})

    pool = candidates.pool(db, ctx)
    kept, excluded = safety.split(db, pool, ctx, policy)
    capped = safety.cap(kept)
    trace.update(candidate_count=len(pool), kept_count=len(kept))
    warnings = warnings_for(ctx)

    # 후보 수가 아니라 서로 겹치지 않게(이름·묶음) 고를 수 있는 수로 판단한다. 겹치는 후보뿐이면 LLM은 검증을 통과할 수 없다
    distinct = verify.fallback(capped, n)
    if len(distinct) < n:
        return contract.result("partial", verify.materialize({"picks": distinct}, capped), excluded, warnings,
                               "fallback_rules", trace,
                               f"추천 가능한 성분이 {len(distinct)}개뿐입니다. 억지로 채우지 않았습니다.")

    excluded_ids = {e["ingredient_id"] for e in excluded}
    picks, selector, attempts = choose(client, ctx, normalized, capped, excluded_ids, n, clean.get("conditions") or [])
    trace["selection"] = attempts
    status = "needs_review" if warnings else "ok"
    return contract.result(status, verify.materialize(picks, capped), excluded, warnings, selector, trace)
