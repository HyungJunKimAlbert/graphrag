"""답변 검증. LLM이 쓴 초안을 규칙으로 다시 검사한다. 하나라도 걸리면 답변을 내보내지 않는다.

검사 항목: 문장이 하나라도 있는가, 문장마다 인용이 있는가, 인용한 ID가 문맥에 있었는가, 금지 표현이 없는가,
고립 개체만 인용한 문장이 "효과 없음·안전"처럼 말하지 않는가, 문장에 나온 성분을 권함·주의로 분류했는가,
권함·주의로 분류한 성분이 문장에 실제로 나오는가, 문맥 밖 성분을 이름으로 말하지 않는가,
이 환자에게 금기인 성분을 권하지 않았는가(권함 목록뿐 아니라 문장 표현도 본다).
"""
import re
import unicodedata

from .. import legacy
from ..retrieve.local import literal_mentions

FORBIDDEN = legacy.FORBIDDEN    # 기존 엔진의 금지 표현. 공백을 지운 문장에 적용한다
DOSE = re.compile(r"\d+(\.\d+)?(mg|g|㎎|μg|㎍|iu|정|알|캡슐)", re.I)
# 고립 개체만 인용한 문장: "연결된 근거가 없다"고만 말할 수 있다. 효과·안전을 말하면 근거 없는 주장이다
ISOLATED_OK = re.compile(r"(연결|관계|근거|정보|문서)[^.]{0,12}없")
ISOLATED_NOT = re.compile(r"효과[^.]{0,4}없|안전|문제없|괜찮|도움이되지않|부작용[^.]{0,4}없")
# 금기 성분이 나온 문장: 피하라는 말이 있어야 하고, 권하는 말만 있으면 거부권이 걸린다
CAUTION_WORDS = re.compile(r"피하|피해|금기|주의|권하지|상호작용|삼가|않|제한|부적합")
FAVOR_WORDS = re.compile(r"권[하합장]|검토|도움|추천|고려|보충|섭취할수있")


def compact(text) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", str(text)))


def names_in(text: str, names: dict) -> set[str]:
    """글에 이름이 글자 그대로 나온 성분 ID. 긴 이름부터 찾고 찾은 자리는 가린다."""
    by_name = {}
    for iid, name in names.items():
        if compact(name):
            by_name.setdefault(compact(name), set()).add(iid)
    rest, found = compact(text), set()
    for name in sorted(by_name, key=lambda n: (-len(n), n)):
        if name in rest:
            rest = rest.replace(name, "\0" * len(name))
            found |= by_name[name]
    return found


def quoted_in_intake(dose: str, claim: dict, docs: dict) -> bool:
    """용량 숫자가 이 문장이 인용한 섭취량 문서 원문에 그대로 있는가. '250mg' 안의 '50mg'은 인정하지 않는다."""
    pattern = re.compile(rf"(?<![\d.]){re.escape(dose)}", re.I)
    return any(docs[e]["kind"] == "intake" and pattern.search(compact(docs[e]["text"]))
               for e in claim["evidence_ids"] if e in docs)


def unclassified_ingredients(text: str, names: dict, classified: set) -> list[str]:
    """글에 이름이 나왔는데 권함·주의 어디에도 없는 성분. 긴 이름부터 찾아 '비타민 B12' 속 '비타민 B1'을 따로 세지 않는다."""
    mentioned = names_in(text, names)
    by_name: dict[str, set] = {}
    for iid in mentioned:
        by_name.setdefault(compact(names[iid]), set()).add(iid)
    return [names[next(iter(ids))] for name, ids in sorted(by_name.items()) if not ids & classified]


def unmentioned_ingredients(text: str, names: dict, classified: set) -> list[str]:
    """권함·주의로 분류했는데 어느 문장에도 이름이 없는 성분. 문장 없는 권유는 인용 검증을 피해 간다."""
    mentioned = names_in(text, names)
    return [names.get(iid, iid) for iid in classified if iid not in mentioned]


def as_written(text: str, squashed: str) -> str | None:
    """공백을 뺀 이름(squashed)이 글에 나온 자리를 원문 그대로 돌려준다. '오메가-3지방산함유유지' → '오메가-3 지방산 함유 유지'."""
    positions = [i for i, ch in enumerate(text) if not ch.isspace()]
    flat = "".join(text[i] for i in positions).lower()
    at = flat.find(squashed.lower())
    return None if at < 0 else text[positions[at]:positions[at + len(squashed) - 1] + 1]


def outside_context(text: str, context: dict, name_index: dict | None) -> list[str]:
    """그래프 전체 이름 사전으로 찾았을 때 문맥에 없는 성분이 글에 나오면 그 이름. 문맥 밖 성분은 금기 판정도 받지 않았다.
    같은 이름을 가진 Ingredient가 여럿이면(예: 고시형·개별인정형 '인삼') 그중 하나라도 문맥에 있을 때 문맥 안으로 본다."""
    if not name_index:
        return []
    inside = {f"Ingredient:{iid}" for iid in context.get("ingredient_ids", [])}
    found = {k for k in literal_mentions(text, name_index) if k.startswith("Ingredient:")}
    names = []
    for squashed, keys in sorted(name_index.items(), key=lambda kv: (-len(kv[0]), kv[0])):
        ingredient_keys = {k for k in keys if k.startswith("Ingredient:")}
        if ingredient_keys & found and not ingredient_keys & inside:
            written = as_written(text, squashed)
            if written and written not in names:
                names.append(written)
    return names


def blocked(draft: dict, context: dict, verdicts: dict | None) -> list[str]:
    """금기 거부권. 이 환자에게 금기인 성분이 '권함' 목록에 있거나, 그 성분이 나온 문장이 피하라는 말 없이
    권하는 말만 하면 사유를 돌려준다. '주의' 목록에 있고 문장도 피하라고 하면 막지 않는다."""
    by_ingredient = (verdicts or {}).get("by_ingredient", {})
    names = context.get("ingredient_names", {})
    forbidden = {iid for iid, v in by_ingredient.items() if not v["allowed"]}

    def why(iid):
        return "; ".join(f"{f['by']}: {f['text']}" for f in by_ingredient[iid]["avoid"])

    reasons = []
    for iid in draft["recommended_ingredient_ids"]:
        if iid in forbidden:
            reasons.append(f"금기 성분 권유: '{names.get(iid, iid)}'은(는) 이 환자에게 금기입니다 ({why(iid)})")
    for n, claim in enumerate(draft["claims"], 1):
        flat = compact(claim["text"])
        for iid in sorted(names_in(claim["text"], names) & forbidden):
            if iid in draft["recommended_ingredient_ids"]:
                continue                                  # 위에서 이미 적었다
            if FAVOR_WORDS.search(flat) and not CAUTION_WORDS.search(flat):
                reasons.append(f"금기 성분 권유: {n}번째 문장이 '{names.get(iid, iid)}'을(를) 피하라는 말 없이 권합니다 "
                               f"— 이 환자에게 금기입니다 ({why(iid)})")
    return reasons


def check(draft: dict, context: dict, verdicts: dict | None = None, name_index: dict | None = None) -> tuple[bool, list[str]]:
    docs = {d["id"]: d for d in context["docs"]}
    isolated = {s["key"] for s in context["seeds"] if s["isolated"]}
    # 고립 개체는 "연결된 근거가 없다"는 문장을 위해 자기 키로 인용할 수 있다. 연결된 개체의 키는 근거가 아니다
    known = set(docs) | {f["id"] for f in context["facts"]} | isolated
    reasons = blocked(draft, context, verdicts)
    if not draft["claims"]:
        reasons.append("문장 없음: 답변 문장이 하나도 없습니다.")
    for n, claim in enumerate(draft["claims"], 1):
        flat = compact(claim["text"])
        if not claim["evidence_ids"]:
            reasons.append(f"인용 없는 문장: {n}번째 문장에 근거 ID가 없습니다.")
        unknown = [e for e in claim["evidence_ids"] if e not in known]
        if unknown:
            reasons.append(f"문맥에 없는 근거 ID: {', '.join(unknown)}")
        if claim["evidence_ids"] and all(e in isolated for e in claim["evidence_ids"]):
            if ISOLATED_NOT.search(flat) or not ISOLATED_OK.search(flat):
                reasons.append(f"고립 개체 인용: {n}번째 문장은 '연결된 근거가 없다'고만 말할 수 있습니다.")
        for match in FORBIDDEN.finditer(flat):
            word = match.group(0)
            if DOSE.fullmatch(word) and quoted_in_intake(word, claim, docs):
                continue
            reasons.append(f"금지 표현: '{word}' ({n}번째 문장)")
    classified = set(draft["recommended_ingredient_ids"]) | set(draft["cautioned_ingredient_ids"])
    names = context.get("ingredient_names", {})
    text = " ".join(c["text"] for c in draft["claims"])
    for name in unclassified_ingredients(text, names, classified):
        reasons.append(f"분류되지 않은 성분: '{name}'이(가) 문장에 나왔지만 권함·주의 어디에도 없습니다.")
    for name in unmentioned_ingredients(text, names, classified):
        reasons.append(f"문장 없는 분류: '{name}'을(를) 권함·주의로 분류했지만 어느 문장에도 나오지 않습니다.")
    for name in outside_context(text, context, name_index):
        reasons.append(f"문맥 밖 성분: '{name}'은(는) 검색 문맥에 없는 성분이라 근거도 금기 판정도 없습니다.")
    return not reasons, reasons
