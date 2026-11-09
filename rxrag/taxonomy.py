"""증상 분류 기준표(「증상별 정규화」 시트 14개) 로드와 그래프 SymptomCategory 대조."""
import openpyxl

from . import config

SHEET = "증상별 정규화"
Q_CATEGORIES = "MATCH (c:SymptomCategory) RETURN c.name"


def load(path=config.TAXONOMY_XLSX):
    """헤더 행(`분류명`) 아래부터 빈 행 전까지. 키워드는 쉼표로 나누고 빈 값은 버린다."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = list(wb[SHEET].iter_rows(values_only=True))
    finally:
        wb.close()
    header = next(i for i, r in enumerate(rows) if "분류명" in r)
    col = rows[header].index("분류명")
    cats = []
    for r in rows[header + 1:]:
        name = r[col] if col < len(r) else None
        if name is None or not str(name).strip():
            break
        keywords = [k.strip() for k in str(r[col + 2] or "").split(",") if k.strip()]
        cats.append({"category": str(name).strip(), "definition": str(r[col + 1] or "").strip(), "keywords": keywords})
    return cats


def graph_mismatch(db, cats):
    xlsx = {c["category"] for c in cats}
    graph = {r[0] for r in db.run(Q_CATEGORIES)}
    return {"only_xlsx": sorted(xlsx - graph), "only_graph": sorted(graph - xlsx)}


def prompt_text(cats):
    return "\n".join(f"- {c['category']}: {c['definition']} / 키워드: {', '.join(c['keywords'])}" for c in cats)
