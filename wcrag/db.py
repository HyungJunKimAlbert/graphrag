"""Neo4j Bolt 접속.

쓰기는 rag DB에만 한다. dev DB에 run()을 부르면 PermissionError가 난다.
read()는 읽기 전용 트랜잭션이라, 쿼리 안에 쓰기가 섞여 있어도 서버가 거절한다.
"""
from typing import Literal

from dotenv import dotenv_values
from neo4j import READ_ACCESS, GraphDatabase, Query

from . import config

Target = Literal["rag", "dev"]


class Graph:
    def __init__(self, driver, target: Target):
        self.driver = driver
        self.target = target

    def run(self, cypher: str, **params) -> list[list]:
        """쓰기 가능한 실행. 자동 커밋이라 스키마 변경과 CALL { } IN TRANSACTIONS도 된다."""
        if self.target != "rag":
            raise PermissionError(f"{self.target} DB에는 쓰지 않습니다. 쓰기는 rag DB에만 합니다.")
        with self.driver.session() as session:
            return [list(record.values()) for record in session.run(cypher, params)]

    def read(self, cypher: str, timeout_s: float = config.READ_TIMEOUT_S, **params) -> list[list]:
        """읽기 전용 실행. 시간 제한을 넘으면 서버가 트랜잭션을 끊는다."""
        with self.driver.session(default_access_mode=READ_ACCESS) as session:
            return [list(record.values()) for record in session.run(Query(cypher, timeout=timeout_s), params)]

    def close(self):
        self.driver.close()


def connect(target: Target = "rag") -> Graph:
    if target == "rag":
        env, url = dotenv_values(config.RAG_ENV), config.RAG_BOLT_URL
    elif target == "dev":
        env, url = dotenv_values(config.DEV_ENV), config.DEV_BOLT_URL
    else:
        raise ValueError(f"알 수 없는 대상: {target!r}")
    password = env.get("NEO4J_PASSWORD")
    if not password:
        raise RuntimeError(f"{target} DB 접속 정보 파일에 NEO4J_PASSWORD가 없습니다.")
    # 없는 라벨·속성 경고 같은 서버 알림은 로그를 덮으므로 끈다. 오류는 그대로 예외로 올라온다
    driver = GraphDatabase.driver(url, auth=(env.get("NEO4J_USER", "neo4j"), password),
                                  notifications_min_severity="OFF")
    return Graph(driver, target)
