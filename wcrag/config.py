"""경로, 모델명, 기본값 상수. 값을 바꿀 곳은 이 파일 하나다."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WELLCHECK = ROOT.parent
LEGACY_GRAPHDB = WELLCHECK / "graphdb"          # 기존 추천 엔진. 읽기만 한다
OUT = ROOT / "out"

# 접속 정보 파일. 값은 출력하지 않는다
RAG_ENV = ROOT / ".neo4j-rag.env"
DEV_ENV = LEGACY_GRAPHDB / ".neo4j-dev.env"
OPENAI_ENV = WELLCHECK / ".env"
RAG_BOLT_URL = "bolt://localhost:7689"          # supplement-neo4j-rag. 쓰기 가능한 유일한 DB
DEV_BOLT_URL = "bolt://localhost:7688"          # supplement-neo4j-dev. 읽기 전용

# 모델
CHAT_MODEL = "gpt-6-luna"
EFFORT = "low"
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536

READ_TIMEOUT_S = 10

# 개체 키는 "<라벨>:<고유값>"이다. 라벨마다 (고유값 속성, 이름 속성)을 적는다
ENTITY_KEYS = {
    "Ingredient": ("ingredient_id", "name"), "Symptom": ("symptom_id", "name"), "Drug": ("drug_id", "name"),
    "DrugItem": ("item_id", "name"), "Condition": ("code", "label"), "FunctionTag": ("tag_id", "name"),
    "SymptomCategory": ("code", "name"), "Interest": ("code", "name"), "Segment": ("code", "name"),
    "Disease": ("code", "name"), "LifeStage": ("code", "name"), "Mechanism": ("mechanism_id", "name"),
    "Company": ("company_id", "name"),
}

# 질의 기본값
ENABLED_MODES = ["local"]       # Phase 2에서 "cypher", Phase 4에서 "global"을 더한다
MIN_SCORE = None                # 답 없음 판정 문턱값. 2026-11-07 p1 측정 결과 답없음(중앙값 0.718, 최대 0.82)과
                                # 사실(중앙값 0.748, 최소 0.649) 분포가 겹쳐 가를 수 없음 → 쓰지 않고 answerable 플래그만 쓴다
