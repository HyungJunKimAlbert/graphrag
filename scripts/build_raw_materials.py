"""성분 ID → 원재료명 JSON(rxrag/data/raw_materials.json). 그래프에 원재료 속성이 없어 원료 DB 엑셀에서 붙인다.

python -m scripts.build_raw_materials  (dev DB 읽기 + 엑셀 읽기, 과금 없음)
- 영양성분: 「2-1. 영양성분」 제조기준(원료), 성분명으로 연결
- 고시형: 「2-2. 고시형」 원재료, 원료명으로 연결
- 개별인정형: 「2-3. 개별인정형(API)」 원재료 명(식약처 API RAWMTRL_NM), 인정번호로 연결. 크롤링 시트에는 원재료 칸이 없다
"""
import json
import re
from pathlib import Path

import openpyxl

from rxrag import graph

SOURCE = Path("/Users/user/Documents/documents/웰체크협업/건기식/건기식원료_DB_261030_API정리.xlsx")
OUT = Path(__file__).resolve().parents[1] / "rxrag" / "data" / "raw_materials.json"
Q = "MATCH (i:Ingredient) WHERE i.recommendable RETURN i.ingredient_id, i.type, i.name, i.recognition_no"


def rec_key(value):
    """'제2025-64호' · '2025-64' · 'New제2007-13호' → '2025-64' · 'New2007-13'."""
    return re.sub(r"[제호\s]", "", str(value or ""))


def clean(text):
    text = re.sub(r"^\s*원재료\s*:\s*", "", str(text or "").strip())
    text = re.sub(r"[ \t]+", " ", text).strip()
    return None if not text or "정보 없음" in text else text


def sheet(wb, name, header_row):
    rows = wb[name].iter_rows(values_only=True)
    for _ in range(header_row):
        next(rows)
    return [r for r in rows if any(r)]


def build(db, wb):
    nut = {str(r[1]).strip(): r[2] for r in sheet(wb, "2-1. 영양성분", 2) if r[1]}
    ntc = {str(r[1]).strip(): r[2] for r in sheet(wb, "2-2. 고시형", 1) if r[1]}
    ind = {rec_key(r[0]): r[4] or r[5] for r in sheet(wb, "2-3. 개별인정형(API)", 1) if r[0]}
    out = {}
    for iid, kind, name, rec in db.run(Q):
        raw = {"nutrient": nut.get(name), "notice": ntc.get(name), "individual": ind.get(rec_key(rec))}.get(kind)
        if clean(raw):
            out[iid] = clean(raw)
    return out


if __name__ == "__main__":
    data = build(graph.connect(), openpyxl.load_workbook(SOURCE, read_only=True))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(dict(sorted(data.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(data)}개 저장: {OUT}")
