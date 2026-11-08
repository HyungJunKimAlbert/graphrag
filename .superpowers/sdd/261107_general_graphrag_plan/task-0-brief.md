### Task 0: 환경과 호환성 확인

**Files:**
- Create: `requirements.txt`, `docker-compose.yml`, `.neo4j-rag.env`, `wcrag/config.py`, `wcrag/db.py`, `tests/test_db.py`

**Interfaces:**
- Produces: `wcrag.db.connect(target: Literal["rag", "dev"] = "rag") -> Graph`
- Produces: `Graph.run(cypher: str, **params) -> list[list]`. 쓰기 가능. `target="dev"`이면 `PermissionError`를 낸다.
- Produces: `Graph.read(cypher: str, timeout_s: float = 10, **params) -> list[list]`. 읽기 트랜잭션으로 실행한다.
- `dev` 접속은 `bolt://localhost:7688`과 `../graphdb/.neo4j-dev.env`의 사용자·비밀번호를 쓴다.

- [ ] **Step 1:** `python3 -m venv .venv` 후 `neo4j-graphrag[openai]==1.23.0`, `leidenalg`, `igraph`, `openpyxl`, `pytest`, `python-dotenv`, `tiktoken`을 설치한다.
- [ ] **Step 2:** `docker-compose.yml`로 `supplement-neo4j-rag`를 띄운다. 이미지는 `neo4j:5.26`, 포트는 `7476:7474`와 `7689:7687`, 볼륨은 이름 있는 볼륨 2개다.
- [ ] **Step 3: 실패 테스트 작성.**
  - `test_dev_run_raises_permission_error`
  - `test_read_rejects_write_cypher`: `Graph.read("CREATE (n:RagSmoke)")`가 드라이버 오류를 낸다
  - `test_rag_roundtrip`: `:RagSmoke` 노드를 만들고 지운다
- [ ] **Step 4:** 구현하고 통과시킨다. `test_read_rejects_write_cypher`가 통과하지 않으면 Task 8의 읽기 전용 보장이 성립하지 않으므로 사용자에게 알리고 멈춘다.
- [ ] **Step 5:** 설치된 버전에서 `HybridCypherRetriever`, `Text2CypherRetriever`, `LLMInterface`, `Embedder`의 import 경로와 시그니처를 확인해 이 문서 4.5절 아래에 적는다.
- [ ] **Step 6 (승인 후, 호출 1~2건):** neo4j-graphrag의 OpenAI LLM 클래스로 `gpt-6-luna`가 호출되는지 확인한다. 안 되면 Task 3의 `llm.complete()`를 감싼 `LLMInterface` 구현을 쓰기로 하고 문서에 적는다.

