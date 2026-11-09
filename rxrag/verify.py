"""⑥ 검증과 규칙 대체. LLM 응답을 코드로 검사하고, 통과하지 못하면 규칙 점수 순으로 채운다."""
import re

from .legacy import FALLBACK as FALLBACK_REASON
from .legacy import FORBIDDEN


SUFFIXES = ("복합추출물", "추출복합물", "추출분말", "추출물", "농축액", "분말", "추출")
NOISE = re.compile(r"[\s®ⓇⓒTM™]|\d+(?:\.\d+)?%")


def _key(text):
    """공백·상표 기호·함량(20%)을 빼고 추출물/분말 같은 제형 접미어를 뗀다."""
    k = NOISE.sub("", text).lower()
    for suf in SUFFIXES:
        if k.endswith(suf) and len(k) > len(suf):
            return k[:-len(suf)]
    return k


def _aliases(name):
    """원료 이름의 별칭 집합. 괄호 밖 본명, 괄호 안 한글 별칭(쉼표 구분), 한글끼리 하이픈으로 이은 이름의 양쪽."""
    base = re.sub(r"\(.*\)", "", name)  # 괄호 안 제품명·고시번호((주)…, 제2024-30호)는 본명에서 뺀다
    keys = {_key(base)}
    for inner in re.findall(r"\(([^()]*)\)", name):
        if re.search("[가-힣]", inner) and not re.search(r"주\)|제\d", inner):
            keys |= {_key(part) for part in inner.split(",")}
    parts = base.split("-")
    if len(parts) == 2 and all(re.search("[가-힣]{2}", p) for p in parts):
        keys |= {_key(p) for p in parts}
    return {k for k in keys if len(k) >= 2}


def _clash(a, b):
    """같은 원료(별칭이 겹침)이거나 묶음과 그 구성 성분이면 사실상 중복이다."""
    return (bool(_aliases(a["name"]) & _aliases(b["name"])) or a["ingredient_id"] in b.get("members", [])
            or b["ingredient_id"] in a.get("members", []))


def check(picks, pool, excluded_ids, n):
    """오류 문구 목록. 빈 리스트면 통과."""
    items = picks.get("picks") if isinstance(picks, dict) else None
    if not isinstance(items, list):
        return ["picks 목록이 없습니다."]
    errors = []
    if len(items) != n:
        errors.append(f"{n}개를 골라야 하는데 {len(items)}개입니다.")
    if not all(isinstance(p, dict) for p in items):
        return errors + ["picks의 각 항목은 {ingredient_id, rank, fact_ids, reason} 객체여야 합니다."]
    ranks = sorted(p.get("rank") for p in items if isinstance(p.get("rank"), int))
    if ranks != list(range(1, len(items) + 1)) or len(ranks) != len(items):
        errors.append(f"rank는 1부터 {len(items)}까지 한 번씩 써야 합니다.")
    by_id = {c["ingredient_id"]: c for c in pool}
    chosen = []
    for p in items:
        iid = p.get("ingredient_id")
        if not isinstance(iid, str):
            errors.append(f"ingredient_id는 문자열이어야 합니다: {iid!r}")
            continue
        if iid in excluded_ids:
            errors.append(f"{iid}는 금지 판정으로 제외된 성분입니다.")
            continue
        if iid not in by_id:
            errors.append(f"{iid}는 후보 목록에 없는 성분입니다.")
            continue
        c = by_id[iid]
        for other in chosen:
            if other["ingredient_id"] == iid:
                errors.append(f"{c['name']}를 두 번 골랐습니다.")
            elif _clash(c, other):
                errors.append(f"{c['name']}와 {other['name']}는 같은 성분이거나 묶음과 구성 성분입니다.")
        chosen.append(c)
        fact_ids = p.get("fact_ids") or []
        if not isinstance(fact_ids, list):
            errors.append(f"{c['name']}의 fact_ids는 리스트여야 합니다.")
            fact_ids = []
        elif not fact_ids:
            errors.append(f"{c['name']}의 fact_ids가 비었습니다.")
        own = {f["fact_id"] for f in c["facts"]}
        foreign = [f for f in fact_ids if f not in own]
        if foreign:
            errors.append(f"{c['name']}의 근거 목록에 없는 fact_id: {', '.join(map(str, foreign))}")
        reason = p.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{c['name']}의 reason이 비었습니다.")
        elif FORBIDDEN.search(re.sub(r"\s+", "", reason)):
            errors.append(f"{c['name']}의 reason에 건강기능식품에 쓸 수 없는 표현이 있습니다.")
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
