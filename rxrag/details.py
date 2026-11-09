"""추천 성분 세부사항. LLM이 3개를 고른 뒤 코드가 붙인다(LLM 입력·토큰에 영향 없음, 문구를 LLM이 바꾸지 않음).

일일섭취량·섭취 시 주의사항·주된 기능성은 그래프 성분 노드, 원재료명은 rxrag/data/raw_materials.json(scripts/build_raw_materials.py).
cautions(이 환자의 약·질환 때문에 생긴 주의)와 달리 precautions는 제품 표시용 일반 주의사항이다.
"""
import json
from functools import lru_cache
from pathlib import Path

RAW_FILE = Path(__file__).resolve().parent / "data" / "raw_materials.json"
Q = """MATCH (i:Ingredient) WHERE i.ingredient_id IN $ids
RETURN i.ingredient_id, i.intake_raw, i.precaution_text, i.function_text"""


@lru_cache(maxsize=1)
def load_raw():
    return json.loads(RAW_FILE.read_text(encoding="utf-8"))


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def attach(db, recommendations, raw=None):
    """추천 목록 각 항목에 details를 붙인 새 목록을 돌려준다. 그래프 조회는 한 번."""
    if not recommendations:
        return []
    raw = load_raw() if raw is None else raw
    ids = [r["ingredient_id"] for r in recommendations]
    rows = {iid: (intake, precaution, function) for iid, intake, precaution, function in db.run(Q, ids=ids)}
    out = []
    for r in recommendations:
        intake, precaution, function = rows.get(r["ingredient_id"], (None, None, None))
        out.append({**r, "details": {"daily_intake": _text(intake), "raw_materials": _text(raw.get(r["ingredient_id"])),
                                     "precautions": _text(precaution), "function": _text(function)}})
    return out
