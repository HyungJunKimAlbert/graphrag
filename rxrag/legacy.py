"""기존 규칙 엔진(../graphdb/recommend) 읽기 전용 어댑터. 엔진을 import하는 곳은 이 파일 하나다."""
import sys

from .config import GRAPHDB, WELLCHECK

for _p in (str(WELLCHECK / "scripts"), str(GRAPHDB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from load_supplement_graph import Neo4j  # noqa: E402
from recommend import engine as _engine  # noqa: E402
from recommend import normalize as _normalize  # noqa: E402
from recommend import safety as _safety  # noqa: E402
from recommend.candidates import GENERATORS, fold_groups  # noqa: E402
from recommend.engine import INCOMPLETE  # noqa: E402
from recommend.llm import FORBIDDEN  # noqa: E402

SEX_VALUES = _normalize.SEX_VALUES
SEX_KEYS = _normalize.SEX_KEYS
PREGNANCY_UNKNOWN = "임신·수유 여부가 입력되지 않았습니다. 해당하면 차단 규칙이 달라집니다."  # 엔진 engine.recommend 문구


def build_context(db, patient, normalized=None):
    return _normalize.build_context(db, patient, normalized)


def safety_check(db, ids, ctx):
    return _safety.check(db, ids, ctx)


def engine_recommend(db, patient, normalized=None, n=3):
    return _engine.recommend(db, patient, n=n, normalized=normalized)


def rule_version(db):
    return _engine.rule_version(db)


__all__ = ["Neo4j", "GENERATORS", "INCOMPLETE", "FORBIDDEN", "SEX_VALUES", "SEX_KEYS", "PREGNANCY_UNKNOWN",
           "build_context", "safety_check", "engine_recommend", "rule_version", "fold_groups"]
