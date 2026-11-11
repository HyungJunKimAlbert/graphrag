"""Neo4j 조회. cypher/ 의 '// @query' 블록만 실행한다 (LLM 이 Cypher 를 쓰지 않는다는 규칙).

표준 라이브러리만 쓰려고 HTTP 트랜잭션 API 를 호출한다.
"""
import base64
import json
import pathlib
import re
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
URL = "http://localhost:7476/db/neo4j/tx/commit"


def _blocks():
    blocks = {}
    for path in sorted((ROOT / "cypher").glob("*.cypher")):
        name, buf = None, []
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*//\s*@query\s+(\w+)", line)
            if m:
                name, buf = m.group(1), []
                continue
            if name is None or line.strip().startswith("//"):
                continue
            buf.append(line)
            if line.rstrip().endswith(";"):
                blocks[name] = "\n".join(buf).rstrip().rstrip(";")
                name = None
    return blocks


def _password():
    for line in (ROOT / ".neo4j.env").read_text().splitlines():
        if line.startswith("NEO4J_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(".neo4j.env 에 NEO4J_PASSWORD 가 없다")


class Graph:
    def __init__(self, url=URL):
        self.url = url
        self.blocks = _blocks()
        token = base64.b64encode(f"neo4j:{_password()}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    def run(self, block, **params):
        """블록 이름과 파라미터 → 행(dict) 목록."""
        body = json.dumps({"statements": [{"statement": self.blocks[block], "parameters": params}]}).encode()
        req = urllib.request.Request(self.url, data=body, headers=self.headers)
        with urllib.request.urlopen(req, timeout=60) as res:
            out = json.load(res)
        if out["errors"]:
            raise RuntimeError(f"{block}: {out['errors'][0]['message']}")
        result = out["results"][0]
        return [dict(zip(result["columns"], row["row"])) for row in result["data"]]
