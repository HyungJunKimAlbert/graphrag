"""rxrag 설정값. 경로·포트·추천 수·후보 상한·주의 등급 정책."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WELLCHECK = ROOT.parent
GRAPHDB = WELLCHECK / "graphdb"
DEV_ENV = GRAPHDB / ".neo4j-dev.env"
OPENAI_ENV = WELLCHECK / ".env"
ALLOWED_HTTP_PORT = 7475  # dev DB HTTP. 7474(운영 DB)는 접속 금지
RAG_ENV = ROOT / ".neo4j-rag.env"
RAG_HTTP_PORT = 7476  # RAG DB(bolt 7689) HTTP. 증상-기전-성분 층만 둔다. 쓰기는 이 DB에만
MECH_XLSX = Path(os.environ.get("RXRAG_MECH_XLSX") or WELLCHECK / "data" / "symptom_mechanism_evidence_kr.xlsx")
MECH_VERDICTS = ("근거 있음",)  # 후보 경로에 쓰는 증상-기전 판정(U14). 부분 근거는 적재만 한다

N = 3
POOL_CAP = 20  # 금지 판정 뒤에 자른다
CAUTION_POLICY = "keep"  # "keep" | "exclude"
ALWAYS_EXCLUDE_CAUTION = ("pop-pregnant", "pop-lactating")  # 임신·수유 대상 주의는 정책과 무관하게 제외(G2 V-20)

# 협업 폴더의 기준표. 다른 위치면 RXRAG_TAXONOMY_XLSX로 바꾼다
TAXONOMY_XLSX = Path(os.environ.get("RXRAG_TAXONOMY_XLSX")
                     or Path.home() / "Documents/documents/웰체크협업/건기식/생애주기별 질환 및 증상_통합.xlsx")
SESSION_DIR = ROOT / "out" / "session"
