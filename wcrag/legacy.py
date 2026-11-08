"""기존 추천 엔진(../graphdb/recommend)을 읽는 유일한 파일.

엔진의 코드와 규칙은 복사하지 않고 그대로 불러 쓴다. 그래야 엔진이 바뀌면 여기 판정도 같이 바뀐다.
엔진은 `db.run(cypher, **params)` 모양의 접속 객체를 받으므로, 읽기 전용으로만 실행하는 어댑터로 감싸서 넘긴다.
"""
import sys

from . import config

# 맨 뒤에 붙인다. 앞에 넣으면 ../graphdb의 같은 이름 폴더가 이 프로젝트의 모듈을 가릴 수 있다
if str(config.LEGACY_GRAPHDB) not in sys.path:
    sys.path.append(str(config.LEGACY_GRAPHDB))

from recommend import candidates as _candidates  # noqa: E402
from recommend import engine as _engine        # noqa: E402
from recommend import normalize as _normalize  # noqa: E402
from recommend import safety as _safety        # noqa: E402
from recommend.llm import FORBIDDEN            # noqa: E402,F401  금지 표현 정규식(공백을 지운 문장에 적용)


class ReadOnly:
    """엔진에 넘기는 접속 객체. 엔진이 보내는 쿼리를 읽기 전용 트랜잭션으로만 실행한다."""

    def __init__(self, graph):
        self.graph = graph

    def run(self, cypher: str, **params) -> list[list]:
        return self.graph.read(cypher, **params)


def build_context(graph, patient: dict) -> dict:
    """환자 입력을 그래프의 키(조건 코드, 약 계열 등)로 바꾼다. 엔진의 normalize.build_context 그대로다."""
    return _normalize.build_context(ReadOnly(graph), patient)


def safety_check(graph, ingredient_ids: list[str], ctx: dict) -> dict:
    """성분별 금기 판정 {id: {allowed, avoid[], caution[]}}. 엔진의 safety.check 그대로다."""
    return _safety.check(ReadOnly(graph), ingredient_ids, ctx)


def interest_candidates(graph, ctx: dict) -> list[dict]:
    """구분(Segment)의 관심사 경로 후보 [{ingredient_id, name, tier, source, paths}]. 엔진의 candidates.interest 그대로다.
    엔진 순서(h.order*100 + r.order) 그대로이며, 같은 성분이 관심사마다 한 줄씩 나올 수 있다. 금기는 거르지 않는다."""
    return _candidates.interest(ReadOnly(graph), ctx)


def incomplete_reasons(ctx: dict) -> list[str]:
    """엔진이 '안전 판단 불완전'으로 보는 입력을 엔진의 문구로 돌려준다.

    주호소 항목은 뺀다. 엔진에서 그 항목은 위험 신호를 못 봤다는 뜻인데, 여기서는 질문 분석 단계가 주호소까지 함께 본다.
    """
    return [template.format(", ".join(ctx["unmatched"][key])) for key, template in _engine.INCOMPLETE
            if key != "complaint" and ctx["unmatched"][key]]
