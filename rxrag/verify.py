"""⑥ 검증과 규칙 대체. LLM 응답을 코드로 검사하고, 통과하지 못하면 규칙 점수 순으로 채운다."""
import re

from .legacy import FALLBACK as FALLBACK_REASON
from .legacy import FORBIDDEN


def _name(c):
    return re.sub(r"\s+", "", c["name"])


def _clash(a, b):
    """같은 이름(공백 제거)이거나 묶음과 그 구성 성분이면 사실상 중복이다."""
    return (_name(a) == _name(b) or a["ingredient_id"] in b.get("members", [])
            or b["ingredient_id"] in a.get("members", []))


def check(picks, pool, excluded_ids, n):
    """오류 문구 목록. 빈 리스트면 통과."""
    items = picks.get("picks") if isinstance(picks, dict) else None
    if not isinstance(items, list):
        return ["picks 목록이 없습니다."]
    errors = []
    if len(items) != n:
        errors.append(f"{n}개를 골라야 하는데 {len(items)}개입니다.")
    ranks = sorted(p.get("rank") for p in items if isinstance(p.get("rank"), int))
    if ranks != list(range(1, len(items) + 1)) or len(ranks) != len(items):
        errors.append(f"rank는 1부터 {len(items)}까지 한 번씩 써야 합니다.")
    by_id = {c["ingredient_id"]: c for c in pool}
    chosen = []
    for p in items:
        iid = p.get("ingredient_id")
        if iid in excluded_ids:
            errors.append(f"{iid}는 금지 판정으로 제외된 성분입니다.")
            continue
        if iid not in by_id:
            errors.append(f"{iid}는 후보 목록에 없는 성분입니다.")
            continue
        c = by_id[iid]
        for other in chosen:
            if other["ingredient_id"] == iid:
                errors.append(f"{iid}를 두 번 골랐습니다.")
            elif _clash(c, other):
                errors.append(f"{iid}({c['name']})와 {other['ingredient_id']}({other['name']})는 같은 성분이거나 묶음과 구성 성분입니다.")
        chosen.append(c)
        fact_ids = p.get("fact_ids") or []
        if not fact_ids:
            errors.append(f"{iid}의 fact_ids가 비었습니다.")
        own = {f["fact_id"] for f in c["facts"]}
        foreign = [f for f in fact_ids if f not in own]
        if foreign:
            errors.append(f"{iid}의 근거 목록에 없는 fact_id: {', '.join(map(str, foreign))}")
        if FORBIDDEN.search(re.sub(r"\s+", "", str(p.get("reason", "")))):
            errors.append(f"{iid}의 reason에 건강기능식품에 쓸 수 없는 표현이 있습니다.")
    return errors


def fallback(pool, n):
    """rule_rank 순으로 이름·묶음 중복을 피해 n개. 근거는 그 후보의 첫 사실."""
    picked = []
    for c in sorted(pool, key=lambda c: c["rule_rank"]):
        if len(picked) >= n:
            break
        if any(_clash(c, p) for p in picked):
            continue
        picked.append(c)
    return [{"ingredient_id": c["ingredient_id"], "rank": i + 1, "fact_ids": [c["facts"][0]["fact_id"]],
             "reason": safe_reason(c["facts"][0]["text"])} for i, c in enumerate(picked)]


def safe_reason(text):
    """사실 문장 원문에 건기식 금지 표현(예방 등)이 있으면 엔진의 대체 문구로 바꾼다. 원문은 evidence에 남는다."""
    return FALLBACK_REASON if FORBIDDEN.search(re.sub(r"\s+", "", text)) else text


def materialize(picks, pool):
    by_id = {c["ingredient_id"]: c for c in pool}
    out = []
    for p in sorted(picks["picks"], key=lambda p: p["rank"]):
        c = by_id[p["ingredient_id"]]
        facts = {f["fact_id"]: f for f in c["facts"]}
        out.append({"rank": p["rank"], "ingredient_id": c["ingredient_id"], "name": c["name"], "reason": p["reason"],
                    "evidence": [facts[f] for f in p["fact_ids"]], "cautions": c.get("cautions", [])})
    return out
