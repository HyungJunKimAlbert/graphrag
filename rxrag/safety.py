"""④ 금지 판정. 판정 자체는 엔진 safety.check(금기의 단일 원천)이고, 여기서는 풀을 kept/excluded로 나누고 상한을 자른다."""
from . import config
from .legacy import safety_check


def _brief(findings):
    return [{"by": f["by"], "text": f["text"]} for f in findings]


def split(db, pool, ctx, policy=config.CAUTION_POLICY):
    """avoid → excluded. policy="exclude"면 caution도, 임신·수유 대상 caution은 항상 excluded. kept에는 cautions를 붙이고 rule_rank 순서를 유지한다."""
    assert policy in ("keep", "exclude"), policy
    if not pool:
        return [], []
    verdicts = safety_check(db, [c["ingredient_id"] for c in pool], ctx)
    kept, excluded = [], []
    for c in pool:
        v = verdicts[c["ingredient_id"]]
        if not v["allowed"]:
            excluded.append({"ingredient_id": c["ingredient_id"], "name": c["name"], "reasons": _brief(v["avoid"])})
        elif policy == "exclude" and v["caution"]:
            excluded.append({"ingredient_id": c["ingredient_id"], "name": c["name"], "reasons": _brief(v["caution"])})
        elif strict := [f for f in v["caution"] if f["by"] in config.ALWAYS_EXCLUDE_CAUTION]:
            excluded.append({"ingredient_id": c["ingredient_id"], "name": c["name"], "reasons": _brief(strict)})
        else:
            kept.append({**c, "cautions": _brief(v["caution"])})
    return kept, excluded


def cap(kept, n=config.POOL_CAP):
    return kept[:n]
