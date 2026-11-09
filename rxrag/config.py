"""rxrag 설정값. 경로·포트·추천 수·후보 상한·주의 등급 정책."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WELLCHECK = ROOT.parent
GRAPHDB = WELLCHECK / "graphdb"
DEV_ENV = GRAPHDB / ".neo4j-dev.env"
OPENAI_ENV = WELLCHECK / ".env"
ALLOWED_HTTP_PORT = 7475  # dev DB HTTP. 7474(운영 DB)는 접속 금지

N = 3
POOL_CAP = 20  # 금지 판정 뒤에 자른다
CAUTION_POLICY = "keep"  # "keep" | "exclude"

TAXONOMY_XLSX = Path("/Users/user/Documents/documents/웰체크협업/건기식/생애주기별 질환 및 증상_통합.xlsx")
SESSION_DIR = ROOT / "out" / "session"
