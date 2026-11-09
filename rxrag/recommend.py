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


def choose(client, ctx, normalized, pool, excluded_ids, n, conditions):
    """LLM 선택 → 검증 → 1회 재시도 → 규칙 대체. (picks, selector)"""
    system, user = select.prompt(ctx, normalized, pool, n, conditions)
    schema = select.schema(pool, n)
    picks = client.complete_json(select.TASK, system, user, schema)
    errors = verify.check(picks, pool, excluded_ids, n)
    if errors:
        retry_user = user + "\n\n이전 응답의 문제: " + " / ".join(errors)
        picks = client.complete_json(select.TASK, system, retry_user, schema)
        errors = verify.check(picks, pool, excluded_ids, n)
    if errors:
        return {"picks": verify.fallback(pool, n)}, "fallback_rules"
    return picks, "llm"


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

    if len(capped) < n:
        picks = {"picks": verify.fallback(capped, len(capped))}
        return contract.result("partial", verify.materialize(picks, capped), excluded, warnings, "fallback_rules", trace,
                               f"추천 가능한 성분이 {len(capped)}개뿐입니다. 억지로 채우지 않았습니다.")

    excluded_ids = {e["ingredient_id"] for e in excluded}
    picks, selector = choose(client, ctx, normalized, capped, excluded_ids, n, clean.get("conditions") or [])
    status = "needs_review" if warnings else "ok"
    return contract.result(status, verify.materialize(picks, capped), excluded, warnings, selector, trace)
