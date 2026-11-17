"""사용 중인 그래프 두 개(규칙 그래프 dev DB · 기전 그래프 RAG DB)의 전체 노드·엣지를 엑셀 한 파일로 내보낸다.

python -m scripts.export_graph_xlsx  (두 DB 읽기 전용, 과금 없음)
- 「요약」: DB별 노드 라벨·엣지 종류와 개수
- 「노드_<DB>_<라벨>」: 라벨마다 한 시트, 속성 전체(Doc.embedding 벡터는 제외)
- 「엣지_<DB>_<종류>」: 종류마다 한 시트, 시작·끝 노드의 ID·이름 + 엣지 속성 전체
"""
import json
import sys
from pathlib import Path

import openpyxl
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from rxrag import graph

OUT = Path(__file__).resolve().parents[2] / "result" / "rxrag_graphdb_전체항목.xlsx"
SKIP_PROPS = {"embedding"}  # 1,699개 문서의 임베딩 벡터. 사람이 읽을 값이 아니고 엑셀 셀 한도를 넘는다
ID_KEYS = ("ingredient_id", "symptom_id", "mechanism_id", "tag_id", "drug_id", "item_id", "company_id", "doc_id", "code", "id")
LABELS_KO = {"Ingredient": "성분", "Symptom": "증상", "SymptomCategory": "증상 분류", "FunctionTag": "기능성 태그",
             "Mechanism": "원인 기전", "Drug": "약 계열", "DrugItem": "복용약 제품", "Condition": "건강 조건",
             "Segment": "성별·연령군", "Interest": "관심사", "Doc": "근거 문서", "Company": "인정 업체",
             "Disease": "질환", "LifeStage": "생애주기", "RuleSet": "규칙 버전"}
MAX_CELL = 32000  # 엑셀 셀 글자 한도(32,767) 안쪽


def cell(value):
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and len(value) > MAX_CELL:
        return value[:MAX_CELL] + "…(잘림)"
    return value


def node_key(n):
    return next((n[k] for k in ID_KEYS if n.get(k) not in (None, "")), None)


def node_name(n):
    return n.get("name") or n.get("label")


def write_sheet(wb, title, header, rows):
    ws = wb.create_sheet(title[:31])
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([cell(v) for v in r])
    ws.freeze_panes = "A2"
    for i, h in enumerate(header, 1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows[:200] if r[i - 1] is not None])
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 8), 60)
    return ws


def export(dbs, out=OUT):
    wb = openpyxl.Workbook()
    summary = wb.active
    summary.title = "요약"
    summary.append(["DB", "구분", "이름", "한국어", "개수", "시트"])
    for c in summary[1]:
        c.font = Font(bold=True)
    for tag, db in dbs:
        labels = [l for l, in db.run("MATCH (n) UNWIND labels(n) AS l RETURN DISTINCT l ORDER BY l")]
        for label in labels:
            nodes = [n for n, in db.run(f"MATCH (n:`{label}`) RETURN n")]
            keys = sorted({k for n in nodes for k in n} - SKIP_PROPS)
            first = [k for k in ID_KEYS if k in keys] + [k for k in ("name", "label") if k in keys]
            header = first + [k for k in keys if k not in first]
            rows = sorted(([n.get(k) for k in header] for n in nodes), key=lambda r: str(r[0]))
            ws = write_sheet(wb, f"노드_{tag}_{label}", header, rows)
            summary.append([tag, "노드", label, LABELS_KO.get(label, ""), len(rows), ws.title])
        types = [t for t, in db.run("MATCH ()-[r]->() RETURN DISTINCT type(r) ORDER BY type(r)")]
        for t in types:
            edges = db.run(f"MATCH (a)-[r:`{t}`]->(b) RETURN labels(a)[0], a, properties(r), labels(b)[0], b")
            keys = sorted({k for _, _, p, _, _ in edges for k in p} - SKIP_PROPS)
            header = ["시작 라벨", "시작 ID", "시작 이름", "끝 라벨", "끝 ID", "끝 이름"] + keys
            rows = sorted(([la, node_key(a), node_name(a), lb, node_key(b), node_name(b)] + [p.get(k) for k in keys]
                           for la, a, p, lb, b in edges), key=lambda r: (str(r[1]), str(r[4])))
            ws = write_sheet(wb, f"엣지_{tag}_{t}", header, rows)
            pair = sorted({f"{r[0]} → {r[3]}" for r in rows})
            summary.append([tag, "엣지", t, ", ".join(pair), len(rows), ws.title])
    summary.append([])
    summary.append(["dev = 규칙 그래프(기존 엔진, 읽기 전용) · RAG = 기전 그래프(증상-기전-성분 층). Doc.embedding 벡터는 뺐다."])
    for col, w in zip("ABCDEF", (6, 6, 20, 40, 8, 32)):
        summary.column_dimensions[col].width = w
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


if __name__ == "__main__":
    path = export([("dev", graph.connect()), ("RAG", graph.connect_rag())], Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
    print(f"저장: {path}")
