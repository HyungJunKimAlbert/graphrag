"""읽기 전용 그래프 연결. dev DB HTTP(7475)만 허용하고 쓰기 쿼리는 보내기 전에 거부한다."""
import os
import re
from functools import lru_cache
from urllib.parse import urlparse

from dotenv import dotenv_values

from . import config
from .legacy import Neo4j

# CALL은 읽기용 메타 프로시저만 허용한다. db.createLabel·dbms.*·인덱스 생성·CALL {서브쿼리} 등은 모두 거부(안전한 쪽)
READ_PROCEDURES = r"(?:db\.(?:labels|relationshipTypes|propertyKeys)|apoc\.meta\.[A-Za-z.]+)\s*\("
WRITE = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|LOAD\s+CSV)\b"
                   rf"|\bCALL\b(?!\s+{READ_PROCEDURES})", re.I)
KEYS = ("NEO4J_HTTP_URL", "NEO4J_USER", "NEO4J_PASSWORD")


class WriteRefused(RuntimeError):
    pass


class ConfigError(RuntimeError):
    pass


class ReadOnlyDB:
    def __init__(self, inner):
        self.inner = inner

    def run(self, query, **params):
        # 속성 이름(n.created_at 등)은 \b 경계 때문에 걸리지 않는다. 문자열 리터럴 안의 키워드는 거부될 수 있다(안전한 쪽)
        if WRITE.search(query):
            raise WriteRefused("쓰기 쿼리는 보낼 수 없습니다(읽기 전용 연결).")
        return self.inner.run(query, **params)


def _env():
    values = dict(dotenv_values(config.DEV_ENV)) if config.DEV_ENV.exists() else {}
    values.update({k: os.environ[k] for k in KEYS if os.environ.get(k)})
    return values


def _rag_env():
    return dict(dotenv_values(config.RAG_ENV)) if config.RAG_ENV.exists() else {}


def connect_rag(write=False):
    """RAG DB(HTTP 7476). 추천은 읽기 전용, 쓰기 연결은 적재 스크립트(rxrag.mechanism)만 쓴다."""
    env = _rag_env()
    url = env.get("NEO4J_HTTP_URL")
    if not url or urlparse(url).port != config.RAG_HTTP_PORT:
        raise ConfigError(f"RAG DB HTTP({config.RAG_HTTP_PORT}) 주소가 아닙니다. .neo4j-rag.env를 확인하세요.")
    if not env.get("NEO4J_PASSWORD"):
        raise ConfigError("RAG DB NEO4J_PASSWORD가 없습니다.")
    inner = Neo4j(url, env.get("NEO4J_USER") or "neo4j", env["NEO4J_PASSWORD"])
    return inner if write else ReadOnlyDB(inner)


def connect():
    env = _env()
    url = env.get("NEO4J_HTTP_URL")
    if not url:
        raise ConfigError("NEO4J_HTTP_URL이 없습니다. graphdb/.neo4j-dev.env를 확인하세요.")
    if urlparse(url).port != config.ALLOWED_HTTP_PORT:
        raise ConfigError(f"허용되지 않은 포트입니다. dev DB HTTP({config.ALLOWED_HTTP_PORT})만 쓸 수 있습니다.")
    if not env.get("NEO4J_PASSWORD"):
        raise ConfigError("NEO4J_PASSWORD가 없습니다.")
    return ReadOnlyDB(Neo4j(url, env.get("NEO4J_USER") or "neo4j", env["NEO4J_PASSWORD"]))


@lru_cache(maxsize=1)
def rag():
    """추천용 RAG DB 읽기 연결(프로세스당 하나)."""
    return connect_rag()
