# 환자 정보 기반 GraphRAG 영양성분 추천 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** BE 환자 JSON → 그래프 후보·근거 수집 → 규칙 금지 판정 → LLM이 최종 3개·순위 선택 → 코드 검증 → 결과 JSON.

**Architecture:** 새 패키지 `rxrag/`. 금기 판정과 입력 정규화는 기존 엔진(`../graphdb/recommend`)을 읽기 전용으로 import해 재사용한다(`rxrag/legacy.py` 한 곳). 후보 수집은 엔진과 같은 매칭 조건으로 사실 ID를 돌려주는 쿼리를 새로 쓰고, 엔진 생성기와의 동등성 테스트로 어긋남을 막는다. LLM은 `LLMClient` 인터페이스 뒤에 두고 테스트=Fake, 개발·평가=Session(파일), 운영=OpenAI로 바꿔 끼운다.

**Tech Stack:** Python(`graphdb-rag/.venv`), pytest, openpyxl, python-dotenv, openai(운영 구현만), 엔진의 Neo4j HTTP 클라이언트(`wellcheck/scripts/load_supplement_graph.Neo4j`).

**Spec:** `docs/superpowers/specs/2026-11-09-patient-graphrag-recommend-design.md`

## Global Constraints

- 개발·평가 중 외부 API 과금 0. 테스트는 `tests/conftest.py`가 `openai`·네트워크 호출을 예외로 막는다. G3(GPT 재평가)만 예외이며 실행 전 사용자 승인 필수.
- `../graphdb` 코드·규칙·CSV 수정 금지(import만). 그래프 접속은 dev DB HTTP `7475`만, 읽기 전용. `7474`/`7687`(운영 DB) 접속 금지, `7689` 사용 안 함.
- 접속 정보는 `../graphdb/.neo4j-dev.env`, OpenAI 키는 `../.env`에서 **코드가** 읽는다. 에이전트는 값을 출력·로그하지 않는다.
- 금지(avoid) 성분은 LLM 입력에 넣지 않고, 결과에 절대 나오지 않는다(⑥에서 재검사).
- 추천 성분마다 그 성분의 근거 사실 ID ≥ 1개.
- 주의 등급 처리 `CAUTION_POLICY = "keep"`(기본) | `"exclude"`.
- 추천 수 `N = 3`, LLM 후보 상한 `POOL_CAP = 20`(금지 판정 **뒤**에 자름).
- 증상 분류 기준: `/Users/user/Documents/documents/웰체크협업/건기식/생애주기별 질환 및 증상_통합.xlsx` 「증상별 정규화」 시트 14개(그래프 `SymptomCategory` 14개와 이름 동일).
- 건기식 표현 규칙: 엔진 `recommend.llm.FORBIDDEN` 패턴을 `reason`에 적용.
- 가상 환자만. 평가 뒤 정답(`eval/patients.json`의 `must_not`, 테스트셋) 수정 금지.
- 각 태스크 끝에 `.venv/bin/python -m pytest -q` 전체 통과. 커밋 메시지 "영어 동사: 한글 요약" + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. 푸시는 하지 않는다.
- 그래프가 필요한 테스트는 `@pytest.mark.graph`(dev DB 읽기). DB가 없으면 skip이 아니라 실패로 보고한다(조용한 통과 금지).

## Review Focus

1. **같은 이름 성분 여러 ID**(인삼 3, 홍삼 2): LLM이 이름만 같은 두 ID를 고르면 사실상 중복 → ⑥에서 이름(정규화) 중복도 거부. Task 8 테스트.
2. **묶음 성분과 구성 성분 동시 선택**(엔진 `members`): 둘 다 고르면 중복 → ⑥에서 거부. Task 8 테스트.
3. **금지 판정 전 상한 자르기**: 상한을 먼저 자르면 안전한 하위 후보가 사라져 `partial`이 과다해짐 → 금지 판정 뒤 자르기. Task 6 테스트.
4. **부정 표현 주호소**("근육통 없음", "두통은 없고 피곤"): 증상으로 잡으면 잘못된 추천 → 정규화 프롬프트·파서가 부정은 증상에 넣지 않음. Task 4 테스트(Fake 응답에 부정 구절이 symptoms로 오면 파서가 버리는지는 검증 불가하므로, 프롬프트에 규칙 문구 포함 여부 + 평가셋 forbid로 측정).
5. **가임기 여성 임신 여부 미입력**: 금기 규칙이 달라짐 → `warnings`에 엔진과 같은 문구, `status`는 `needs_review`. Task 9 테스트.

---

## File Structure

```
rxrag/
  __init__.py
  config.py        # 상수: N, POOL_CAP, CAUTION_POLICY, 경로, 허용 포트
  graph.py         # 읽기 전용 그래프 연결(ReadOnlyDB)
  legacy.py        # ../graphdb/recommend 어댑터(build_context, safety.check, GENERATORS, INCOMPLETE, FORBIDDEN)
  contract.py      # 입력 검증, 출력 dict 조립
  taxonomy.py      # 엑셀 14개 분류·키워드 로드, 그래프와 대조
  llm.py           # LLMClient, FakeLLM, SessionLLM, OpenAILLM, MissingResponse
  normalize.py     # ② 주호소 정규화 프롬프트·스키마·파싱·병합
  candidates.py    # ③ 후보 풀 + 근거 사실(Fact)
  safety.py        # ④ 금지 판정 적용·분리·상한
  select.py        # ⑤ 선택 프롬프트·스키마
  verify.py        # ⑥ 검증·규칙 대체
  recommend.py     # 진입점 recommend(patient, llm, db) -> dict
  cli.py           # python -m rxrag.cli recommend|export|run|...
eval/
  patients.json    # 가상 환자 ~30명 + must_not
  harness.py       # export/run/G1/엔진 비교/G2 블라인드/엑셀
  normalize_eval.py
tests/
  conftest.py, test_graph.py, test_contract.py, test_taxonomy.py, test_llm.py,
  test_normalize.py, test_candidates.py, test_safety.py, test_select.py,
  test_verify.py, test_recommend.py, test_eval.py
out/                # gitignore: session/, eval/*.jsonl는 커밋, 캐시는 무시
```

---

### Task 1: 뼈대 — 설정·읽기 전용 그래프·엔진 어댑터

**Files:**
- Create: `rxrag/__init__.py`, `rxrag/config.py`, `rxrag/graph.py`, `rxrag/legacy.py`, `tests/conftest.py`, `tests/test_graph.py`
- Modify: `pytest.ini`(markers `graph`), `.gitignore`(`out/session/`, `out/cache/`)

**Interfaces:**
- Produces:
  - `config.GRAPHDB = Path(".../wellcheck/graphdb")`, `config.WELLCHECK`, `config.DEV_ENV = GRAPHDB/".neo4j-dev.env"`, `config.OPENAI_ENV = WELLCHECK/".env"`, `config.ALLOWED_HTTP_PORT = 7475`, `config.N = 3`, `config.POOL_CAP = 20`, `config.CAUTION_POLICY = "keep"`, `config.TAXONOMY_XLSX`.
  - `graph.ReadOnlyDB(inner)` with `.run(query: str, **params) -> list[list]`; raises `graph.WriteRefused` if query matches `\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|CALL\s+apoc\.(?!meta))\b` (대소문자 무시).
  - `graph.connect() -> ReadOnlyDB`; raises `graph.ConfigError` if `NEO4J_HTTP_URL` 없음 또는 포트 ≠ 7475. 값은 메시지에 넣지 않는다.
  - `legacy.build_context(db, patient, normalized) -> dict`, `legacy.safety_check(db, ids, ctx) -> dict`, `legacy.GENERATORS`, `legacy.INCOMPLETE`, `legacy.FORBIDDEN`, `legacy.engine_recommend(db, patient, normalized) -> dict`(엔진 `recommend(..., normalized=...)`).

- [ ] **Step 1: Write failing tests** (`tests/test_graph.py`)

```python
def test_write_query_refused():
    db = ReadOnlyDB(FakeInner())
    for q in ["CREATE (n)", "match (n) detach delete n", "MATCH (n) SET n.x=1", "MERGE (a:A)"]:
        with pytest.raises(WriteRefused): db.run(q)

def test_read_query_passes():
    assert ReadOnlyDB(FakeInner(rows=[[1]])).run("MATCH (n) RETURN count(n)") == [[1]]

def test_wrong_port_refused(monkeypatch):
    monkeypatch.setattr(graph, "_env", lambda: {"NEO4J_HTTP_URL": "http://localhost:7474", "NEO4J_PASSWORD": "x"})
    with pytest.raises(ConfigError) as e: graph.connect()
    assert "x" not in str(e.value)

def test_missing_url_refused(monkeypatch):
    monkeypatch.setattr(graph, "_env", lambda: {})
    with pytest.raises(ConfigError): graph.connect()

@pytest.mark.graph
def test_dev_graph_reachable():
    assert graph.connect().run("MATCH (c:SymptomCategory) RETURN count(c)")[0][0] == 14

def test_legacy_imports():
    assert len(legacy.GENERATORS) == 8 and callable(legacy.safety_check)
```

`tests/conftest.py`: autouse fixture가 `openai.OpenAI.__init__`과 `socket.create_connection`(graph 마크가 없는 테스트에서)을 예외로 막는다.

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_graph.py -q` → FAIL(import error)
- [ ] **Step 3: Implement** 위 Interfaces. `legacy.py`는 `sys.path`에 `GRAPHDB`와 `WELLCHECK/"scripts"`를 넣고 import. `graph._env()`는 `dotenv_values(DEV_ENV)` + 환경변수 우선.
- [ ] **Step 4: Run** 전체 테스트 → PASS(graph 마크 포함)
- [ ] **Step 5: Commit** `Add: rxrag 뼈대·읽기 전용 그래프 연결·엔진 어댑터`

---

### Task 2: 입력 계약과 출력 조립

**Files:** Create `rxrag/contract.py`, `tests/test_contract.py`

**Interfaces:**
- Produces:
  - `contract.validate(patient: dict) -> tuple[dict | None, list[str]]` — 통과 시 (정리된 patient, []), 실패 시 (None, 오류 문구들). 규칙: `age` 숫자로 변환 가능 & 0 ≤ age ≤ 120, `sex`가 엔진 `SEX_VALUES` 별칭 중 하나, `drugs`는 `[{name}]` 또는 문자열 리스트(문자열이면 `{name}`으로 변환), 나머지 키는 그대로.
  - `contract.result(status, recommendations=[], excluded=[], warnings=[], selector=None, trace={}, message=None) -> dict` — 설계 §5.2 키 순서.
  - `STATUSES = ("ok", "needs_review", "partial", "stopped", "invalid_input")`

- [ ] **Step 1: Failing tests**

```python
def test_missing_age_invalid():   assert validate({"sex": "M"})[0] is None
def test_text_age_invalid():      assert validate({"age": "팔십", "sex": "M"})[0] is None
def test_sex_alias_ok():          assert validate({"age": "80", "sex": "남성"})[0]["age"] == 80
def test_drug_strings_wrapped():  assert validate({"age": 50, "sex": "F", "drugs": ["와파린"]})[0]["drugs"] == [{"name": "와파린"}]
def test_result_shape():
    r = result("partial", message="m")
    assert list(r) == ["status","message","recommendations","excluded","warnings","selector","trace"]
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** 전체 → PASS
- [ ] **Step 5: Commit** `Add: 환자 입력 검증과 결과 조립`

---

### Task 3: 증상 분류표 로드와 그래프 대조

**Files:** Create `rxrag/taxonomy.py`, `tests/test_taxonomy.py`

**Interfaces:**
- Produces: `taxonomy.load(path=config.TAXONOMY_XLSX) -> list[dict]` — `[{"category": str, "definition": str, "keywords": [str]}]` 14개(「증상별 정규화」 시트, 헤더 행 `분류명` 아래부터 빈 행 전까지, 키워드는 쉼표 분리·공백 제거). `taxonomy.graph_mismatch(db, cats) -> dict` — `{"only_xlsx": [...], "only_graph": [...]}`. `taxonomy.prompt_text(cats) -> str` — `- {분류}: {정의} / 키워드: a, b, c` 줄들.

- [ ] **Step 1: Failing tests**

```python
def test_load_14():
    cats = taxonomy.load(); assert len(cats) == 14
    assert next(c for c in cats if c["category"] == "스트레스/수면")["keywords"][:2] == ["불면", "스트레스"]

@pytest.mark.graph
def test_matches_graph():
    assert taxonomy.graph_mismatch(graph.connect(), taxonomy.load()) == {"only_xlsx": [], "only_graph": []}
```

- [ ] **Step 2–4:** FAIL → 구현 → 전체 PASS
- [ ] **Step 5: Commit** `Add: 증상 분류표 로더와 그래프 대조`

---

### Task 4: LLM 클라이언트(Fake·Session·OpenAI)

**Files:** Create `rxrag/llm.py`, `tests/test_llm.py`

**Interfaces:**
- Produces:
  - `class LLMClient(Protocol): def complete_json(self, task: str, system: str, user: str, schema: dict) -> dict`
  - `llm.request_key(task, system, user, schema) -> str` — `sha256(json.dumps([task, system, user, schema], ensure_ascii=False, sort_keys=True))[:16]`.
  - `FakeLLM(responses: dict[str, dict] | Callable)` — task 또는 key로 응답, 호출 기록 `.calls`.
  - `SessionLLM(root=Path("out/session"), mode="run"|"export")` — `export`: `requests/{task}/{key}.json`(task, system, user, schema)을 쓰고 `MissingResponse`를 던진다. `run`: `responses/{task}/{key}.json`을 읽고 없으면 `MissingResponse(key)`.
  - `OpenAILLM(model: str = "gpt-6-luna", effort="low")` — Responses API 구조화 출력(`text.format={"type":"json_schema","strict":True,...}`), 키는 `config.OPENAI_ENV`에서 lazy load. 단위 테스트에서 생성하지 않는다.
  - `class MissingResponse(Exception): key: str`

- [ ] **Step 1: Failing tests**

```python
def test_session_export_writes_request_and_raises(tmp_path):
    c = SessionLLM(tmp_path, mode="export")
    with pytest.raises(MissingResponse): c.complete_json("select", "s", "u", {"type": "object"})
    assert len(list((tmp_path/"requests"/"select").glob("*.json"))) == 1

def test_session_run_reads_response(tmp_path):
    k = request_key("select", "s", "u", {"type": "object"})
    (tmp_path/"responses"/"select").mkdir(parents=True)
    (tmp_path/"responses"/"select"/f"{k}.json").write_text('{"picks": []}')
    assert SessionLLM(tmp_path).complete_json("select", "s", "u", {"type": "object"}) == {"picks": []}

def test_session_run_missing_raises(tmp_path):
    with pytest.raises(MissingResponse): SessionLLM(tmp_path).complete_json("select", "s", "u", {})

def test_openai_blocked_in_tests():
    with pytest.raises(Exception): OpenAILLM().complete_json("x", "s", "u", {})
```

- [ ] **Step 2–4:** FAIL → 구현 → 전체 PASS
- [ ] **Step 5: Commit** `Add: LLM 클라이언트 인터페이스와 세션 응답 파일 구현`

---

### Task 5: ② 주호소 정규화

**Files:** Create `rxrag/normalize.py`, `tests/test_normalize.py`

**Interfaces:**
- Consumes: `taxonomy.load/prompt_text`, `LLMClient`
- Produces:
  - `normalize.SCHEMA` — `{"matches":[{"category": enum(14), "symptoms":[str]}], "unmatched":[{"text": str, "kind": enum("분류표 밖 증상","질환명","약 관련","기타")}], "red_flag": bool, "red_flag_reason": str}` (strict, additionalProperties false).
  - `normalize.prompt(complaint: str, profile: str, cats) -> tuple[str, str]` (system, user). system에 반드시 포함: 분류표, "부정된 증상(없음·아님)은 넣지 않는다", "흉통·호흡곤란·의식저하·마비·혈변/토혈·급격한 체중감소 등 응급 신호는 red_flag", "분류표 키워드에 없는 증상은 unmatched(kind=분류표 밖 증상)".
  - `normalize.run(patient: dict, client: LLMClient, cats) -> dict | None` — complaint 없으면 None. 응답의 category가 enum 밖이면 버리고 unmatched(기타)로. 반환은 엔진 `build_context(normalized=...)` 형식 `{"symptoms": [...], "categories": [...], "unmatched": [...], "red_flag": bool, "red_flag_reason": str}`(엔진 `llm.flatten`과 같은 규칙: symptoms 없는 match는 category로).
  - `normalize.merge(patient: dict, normalized: dict | None) -> dict | None` — `patient["symptoms"]`를 `normalized["symptoms"]`에 합집합. normalized가 None이고 symptoms만 있으면 None(엔진 기본 경로 사용).
  - profile = `"남성, 80대"` 형식(이름 등 식별자 없음).

- [ ] **Step 1: Failing tests**

```python
def test_no_complaint_skips_llm():
    f = FakeLLM({}); assert normalize.run({"age": 80, "sex": "M"}, f, CATS) is None and f.calls == []

def test_flatten_rules():
    f = FakeLLM({"normalize": {"matches": [{"category": "스트레스/수면", "symptoms": ["새벽 각성"]},
                                            {"category": "혈당", "symptoms": []}],
                               "unmatched": [], "red_flag": False, "red_flag_reason": ""}})
    n = normalize.run({"age": 80, "sex": "M", "complaint": "밤에 자주 깸"}, f, CATS)
    assert n["symptoms"] == ["새벽 각성"] and n["categories"] == ["혈당"]

def test_unknown_category_dropped():  # category "수면"(enum 밖) → unmatched kind 기타
def test_merge_union():
    assert normalize.merge({"symptoms": ["근육통"]}, {"symptoms": ["새벽 각성"], "categories": [], "unmatched": [], "red_flag": False, "red_flag_reason": ""})["symptoms"] == ["근육통", "새벽 각성"]
def test_prompt_has_negation_and_redflag_rules():
    s, _ = normalize.prompt("x", "남성, 80대", CATS); assert "없음" in s and "red_flag" in s
```

- [ ] **Step 2–4:** FAIL → 구현 → 전체 PASS
- [ ] **Step 5: Commit** `Add: 주호소 LLM 정규화`

---

### Task 6: ③ 후보 풀과 근거 사실 + ④ 금지 판정

**Files:** Create `rxrag/candidates.py`, `rxrag/safety.py`, `tests/test_candidates.py`, `tests/test_safety.py`

**Interfaces:**
- Consumes: `legacy.build_context` 결과 `ctx`(키: `symptoms[{symptom_id,name}]`, `categories`, `drugs{full,interaction,codes,matched}`, `codes`, `segment`, `sex`, `pregnant`), `legacy.safety_check`
- Produces:
  - `Fact = {"fact_id": str, "kind": str, "text": str, "source": str | None}`; `kind` ∈ `SYM_DIRECT`(Symptom>Ingredient), `DRUG_SYM_DEPLETE`(Drug>Symptom>Ingredient), `CAT_FUNC`(SymptomCategory>FunctionTag>Ingredient), `RULE_EXAM`·`RULE_COND`·`RULE_BORDER`(Condition>Ingredient), `DRUG_DEPLETE`(Drug>Ingredient), `INTEREST`(Segment>Interest>Ingredient). `fact_id = f"{kind}:{'>'.join(node_ids)}"`. `text`는 엔진 경로 문장과 같은 틀(예: `"메트포르민 복용 → 근육통 유발 가능 + 비타민 B12 고갈"`). `source`는 `RELIEVED_BY.source`만, 그 외 None.
  - `Candidate = {"ingredient_id", "name", "type", "members": [ids], "facts": [Fact], "tier_rank": int, "rule_rank": int}` — `tier_rank`는 가장 강한 경로의 엔진 단계 순번(0=1-1 … 7=관심사), `rule_rank`는 `(tier_rank, 단계 내 priority, -len(facts), ingredient_id)` 정렬 순번.
  - `candidates.pool(db, ctx) -> list[Candidate]` — 8개 경로 쿼리를 모두 실행, 같은 성분은 facts 합침, 묶음(type=group, members)에 켜진 구성 성분은 엔진 `fold_groups`와 같은 규칙으로 접음, `i.recommendable`만. 정렬은 `rule_rank`.
  - `safety.split(db, pool, ctx, policy=config.CAUTION_POLICY) -> tuple[list[Candidate], list[dict]]` — `legacy.safety_check`로 판정. avoid → excluded `{"ingredient_id","name","reasons":[{by,text}]}`; policy="exclude"면 caution도 excluded. 남은 후보에 `"cautions": [...]`. 반환 kept는 `rule_rank` 순서 유지.
  - `safety.cap(kept, n=config.POOL_CAP) -> list[Candidate]`.

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.graph
def test_pool_superset_of_engine(patient_80m_ctx):   # 엔진 GENERATORS 전 단계 성분 ID ⊆ pool ID
    db, ctx = patient_80m_ctx
    engine_ids = {c["ingredient_id"] for g in legacy.GENERATORS for c in g(db, ctx)}
    assert engine_ids <= {c["ingredient_id"] for c in candidates.pool(db, ctx)}

@pytest.mark.graph
def test_every_candidate_has_fact(patient_80m_ctx):
    assert all(c["facts"] and all(f["fact_id"].split(":")[0] in KINDS for f in c["facts"]) for c in candidates.pool(*patient_80m_ctx))

@pytest.mark.graph
def test_metformin_b12_depletion_fact(patient_80m_ctx):
    b12 = by_name(candidates.pool(*patient_80m_ctx), "비타민 B12")
    assert any(f["kind"] in ("DRUG_DEPLETE", "DRUG_SYM_DEPLETE") and "메트포르민" in f["text"] for f in b12["facts"])

@pytest.mark.graph
def test_80m_exclusions(patient_80m_ctx):
    kept, excluded = safety.split(*patient_80m_ctx[:1], candidates.pool(*patient_80m_ctx), patient_80m_ctx[1])
    names = {e["name"] for e in excluded}
    assert {"마그네슘", "코엔자임Q10"} <= names and not names & {c["name"] for c in kept}

def test_cap_after_split():   # 가짜 pool 30개 중 앞 15개 avoid → kept 15개가 cap 뒤에도 남음
def test_policy_exclude_moves_cautions():   # FakeDB로 caution 판정 → policy="exclude"면 excluded
```

`patient_80m_ctx` 픽스처: `../graphdb/tests/fixtures/patient_80m.json` + `normalize.merge`(complaint 없이 symptoms만) → `legacy.build_context`.

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** — 쿼리는 엔진 `candidates.py`의 WHERE 조건을 그대로 옮기고 RETURN에 경로 노드 ID(`symptom_id`, `drug_id`, `ingredient_id`, `c.code`, `t.tag_id`/`t.name`, `n.code`, `g.code`)를 추가한다. 엔진 쿼리 문자열은 복사하되 출처 주석(`# from recommend/candidates.py Q_DIRECT`)을 단다.
- [ ] **Step 4: Run** 전체 → PASS
- [ ] **Step 5: Commit** `Add: 근거 사실 ID가 붙은 후보 풀과 금지 판정 분리`

---

### Task 7: ⑤ 최종 선택 프롬프트

**Files:** Create `rxrag/select.py`, `tests/test_select.py`

**Interfaces:**
- Consumes: `Candidate`(+`cautions`), ctx
- Produces:
  - `select.SCHEMA` — `{"picks":[{"ingredient_id": enum(후보 ID들), "rank": int 1..N, "fact_ids":[str] minItems 1, "reason": str}] minItems N maxItems N}`; 호출마다 후보 ID enum으로 만든다(`select.schema(pool, n)`).
  - `select.patient_summary(ctx, normalized) -> str` — 성별·연령대·증상명·분류·검진 이상 라벨·복용약 계열·질환. 이름·나이 숫자 그대로 넣지 않음(연령대만).
  - `select.prompt(ctx, normalized, pool, n=config.N) -> tuple[str, str]` — system: 선택 규칙(후보 안에서만, 성분마다 그 후보의 fact_id 1개 이상, 근거 강도 순서 `확인된 고갈·직접 완화 > 분류 기능 > 검진·질환 규칙 > 관심사`, 여러 문제 커버 우대, 3개가 서로 다른 문제, cautions 있는 성분은 대안 있으면 뒤로, 표현 규칙). user: 환자 요약 + 후보 표(`ingredient_id | 이름 | 근거 fact_id: 문장 | 주의`), `rule_rank` 순.

- [ ] **Step 1: Failing tests**

```python
def test_schema_enum_is_pool_ids():   assert select.schema(POOL, 3)["properties"]["picks"]["items"]["properties"]["ingredient_id"]["enum"] == [c["ingredient_id"] for c in POOL]
def test_prompt_lists_every_fact():   _, u = select.prompt(CTX, NORM, POOL); assert all(f["fact_id"] in u for c in POOL for f in c["facts"])
def test_prompt_has_no_excluded():    # excluded 성분 이름이 user 텍스트에 없음
def test_summary_no_exact_age():      assert "80대" in select.patient_summary(CTX, NORM) and "80세" not in select.patient_summary(CTX, NORM)
```

- [ ] **Step 2–4:** FAIL → 구현 → 전체 PASS
- [ ] **Step 5: Commit** `Add: 근거 기반 최종 선택 프롬프트`

---

### Task 8: ⑥ 검증과 규칙 대체

**Files:** Create `rxrag/verify.py`, `tests/test_verify.py`

**Interfaces:**
- Produces:
  - `verify.check(picks: dict, pool: list[Candidate], excluded_ids: set[str], n: int) -> list[str]` — 오류 문구 목록(빈 리스트=통과). 규칙: 개수 == n, rank가 1..n 각 1번, ingredient_id ∈ pool, ∉ excluded, 같은 ID 중복 금지, **같은 이름(공백 제거) 중복 금지**, **묶음과 그 member 동시 금지**, fact_ids ≥ 1이고 모두 그 후보 facts에 있음, reason이 `legacy.FORBIDDEN`(공백 제거 후)에 걸리지 않음.
  - `verify.fallback(pool, n) -> list[dict]` — `rule_rank` 순으로 이름·묶음 중복 피해 n개, `fact_ids`=그 후보 첫 fact, `reason`=첫 fact의 text.
  - `verify.materialize(picks, pool) -> list[dict]` — 출력 recommendations 형식(§5.2) `{"rank","ingredient_id","name","reason","evidence":[Fact],"cautions":[...]}`, rank 순.

- [ ] **Step 1: Failing tests** — 각 규칙마다 실패 케이스 1개 + 정상 1개:

```python
def test_ok(): assert verify.check(GOOD, POOL, set(), 3) == []
def test_outside_pool(), test_excluded(), test_duplicate_id(), test_same_name_two_ids(),
def test_group_and_member(), test_missing_fact(), test_foreign_fact(), test_rank_dup(),
def test_forbidden_reason():  # reason "당뇨를 치료합니다" → 오류
def test_fallback_respects_names_and_groups()
```

- [ ] **Step 2–4:** FAIL → 구현 → 전체 PASS
- [ ] **Step 5: Commit** `Add: LLM 선택 검증기와 규칙 대체`

---

### Task 9: 진입점 recommend()와 CLI

**Files:** Create `rxrag/recommend.py`, `rxrag/cli.py`, `tests/test_recommend.py`

**Interfaces:**
- Consumes: Task 1–8 전부
- Produces:
  - `recommend.recommend(patient: dict, client: LLMClient, db=None, n=config.N) -> dict` — 흐름:
    1. `contract.validate` 실패 → `invalid_input`
    2. `normalize.run` → `merge` → `legacy.build_context(db, patient, normalized)`; `ctx["red_flag"]` → `stopped`(message "추천을 멈추고 진료를 권유합니다.", reason)
    3. `candidates.pool` → `safety.split` → `safety.cap`
    4. kept < n → LLM 호출 없이 `verify.fallback(kept, len(kept))`, `partial`, message `f"추천 가능한 성분이 {k}개뿐입니다. 억지로 채우지 않았습니다."`
    5. `select.prompt` → `client.complete_json("select", ...)` → `verify.check`; 오류면 오류 문구를 user 끝에 `"\n\n이전 응답의 문제: ..."`로 붙여 1회 재호출; 또 오류면 `verify.fallback`, `selector="fallback_rules"`
    6. warnings = 엔진 `INCOMPLETE` 템플릿을 `ctx["unmatched"]`에 적용 + `pregnancy_unknown` 문구(엔진 문구 그대로); 있으면 `needs_review`, 없으면 `ok`
    7. `trace = {"normalized", "candidate_count": len(pool), "kept_count", "rule_version"}`
  - `MissingResponse`는 잡지 않고 그대로 올린다(평가 오염 방지).
  - `cli`: `python -m rxrag.cli recommend <patient.json> [--llm session|openai]` → JSON 출력.

- [ ] **Step 1: Failing tests** (graph 마크, FakeLLM)

```python
def test_invalid_input(): assert recommend({"sex": "M"}, FakeLLM({}))["status"] == "invalid_input"
def test_red_flag_stops():   # normalize 응답 red_flag True → stopped, select 호출 없음
def test_happy_path_80m():   # FakeLLM select 응답을 pool 상위 3개로 구성 → status in (ok, needs_review), selector llm, 3개 모두 evidence ≥1
def test_bad_then_good_retries_once():   # 첫 응답 excluded 성분 → 두 번째 정상 → selector llm, calls 2
def test_bad_twice_falls_back():   # selector fallback_rules, 금지 성분 없음
def test_partial_skips_llm():   # kept 2개 되도록 CAUTION_POLICY exclude + 약 많은 환자 픽스처 → select 호출 0
def test_pregnancy_unknown_warning():   # 30세 여성, pregnant 없음 → needs_review + 임신 문구
def test_missing_session_response_raises(tmp_path):   # SessionLLM(run) → MissingResponse
```

- [ ] **Step 2–4:** FAIL → 구현 → 전체 PASS
- [ ] **Step 5: Commit** `Add: 환자 정보 기반 추천 진입점과 CLI`

---

### Task 10: 평가 환자셋과 must_not

**Files:** Create `eval/patients.json`, `eval/build_must_not.py`, `tests/test_eval.py`

**Interfaces:**
- Produces: `eval/patients.json` = `{"patients":[{"id":"V-01","group":str,"note":str,"patient":{...},"must_not":[ingredient_id]}]}` 약 30명. 구성:
  - `V-01` 80세 남성 픽스처(complaint 포함)
  - `V-02`~`V-15` archive 브랜치 `git show archive/qa-graphrag:eval/questions.json`의 환자 문항 14건(P-13 제외)을 입력 JSON으로 옮김
  - 금지 유발 6명: 와파린+주호소 피로(코엔자임Q10), 저GFR(<45)+근육경련(마그네슘), 메트포르민 단독(B12 고갈 보충), 스타틴(코엔자임Q10 고갈 vs 와파린 동시 복용), 임신 여성, 칼륨 보존 이뇨제
  - 경계 9명: 주호소만, 약만, 검진만, 정보 거의 없음(나이·성별만), 규칙 없는 질환만, 위험 신호 주호소(흉통), 부정 표현 주호소, 가임기 여성 임신 미입력, 같은 이름 다중 ID 성분이 걸리는 관심사(인삼·홍삼)
  - `must_not` = `build_must_not.py`가 `candidates.pool`+`safety.split`(policy keep)으로 계산한 excluded ID 전부. 한 번 만들고 커밋 후 수정 금지.
- `tests/test_eval.py`: 30명 내외, id 유일, 모든 patient가 `contract.validate` 통과(`V-*` 중 정보 부족 사례 제외 표시 `expect_status`).

- [ ] **Step 1–4:** 테스트 → 파일 작성·`build_must_not.py` 실행 → PASS
- [ ] **Step 5: Commit** `Add: 가상 환자 평가셋과 금지 정답`

---

### Task 11: 평가 하네스(export → 세션 응답 → run, G1·G2·엑셀)

**Files:** Create `eval/harness.py`, extend `tests/test_eval.py`

**Interfaces:**
- Produces (CLI `python -m eval.harness <cmd> --tag <tag>`):
  - `export` — `SessionLLM(mode="export")`로 정규화 요청을 내보낸다. 정규화 응답이 다 있으면 select 요청을 내보낸다(2라운드). 남은 요청 수 출력.
  - `run` — `SessionLLM(mode="run")`로 전 환자 `recommend`; `out/eval/{tag}.jsonl` 저장.
  - `g1` — 환자마다: 추천 ∩ must_not = ∅, 모든 추천 evidence ≥ 1이고 fact_id가 해당 후보 facts에 있음, 스키마 키 일치, `expect_status` 일치. 위반 0이면 exit 0.
  - `engine` — 같은 normalized로 `legacy.engine_recommend` 실행, `out/eval/{tag}-engine.jsonl`.
  - `g2-export` — 환자별 A/B(무작위, seed=환자 id 해시) 쌍 + 근거를 `out/session/requests/judge/*.json`으로. 채점 스키마 `{"winner":"A"|"B"|"tie","clearly_worse": "A"|"B"|null,"scores":{"A":{fit,coverage,spread,caution}, "B":{...}} (1–5), "why": str}`.
  - `g2` — 응답을 풀어 새 시스템 기준 승/무/패, `clearly_worse` 집계. 통과 = (승+무)/전체 ≥ 0.8 이고 새 시스템 clearly_worse 0.
  - `xlsx` — `out/eval/{tag}.xlsx`: 시트 `요약`(G1·G2 지표), `불일치`(엔진과 3개 집합이 다른 환자: 환자 요약, 엔진 3개, 새 3개, 근거, 채점 이유), `전체`.
- 테스트: FakeLLM으로 2명짜리 미니 셋을 export→run→g1 전 과정 실행(`tmp_path`), g2 집계 함수 단위 테스트(승·무·패 경계 0.8).

- [ ] **Step 1–4:** FAIL → 구현 → 전체 PASS
- [ ] **Step 5: Commit** `Add: 추천 평가 하네스(G1 게이트·엔진 블라인드 비교·엑셀)`

---

### Task 12: 정규화 평가(472건)

**Files:** Create `eval/normalize_eval.py`, extend `tests/test_eval.py`

**Interfaces:**
- Consumes: `../graphdb/data/eval/complaint_testset.json`(`cases[]`: `utterance`, `profile`, `required: [[대안...]]`, `forbid`, `red_flag`, `no_match`)
- Produces: `export`(472 요청, `profile`은 케이스 값 그대로), `score`: required 충족률(각 묶음에서 하나 이상 symptoms에 있음), forbid 위반 건수, red_flag 재현율·정밀도, no_match 정확도 → `out/eval/normalize-{tag}.json` + xlsx 시트.
- 테스트: 케이스 3개 고정 응답으로 지표 계산 검증.

- [ ] **Step 1–4:** FAIL → 구현 → 전체 PASS
- [ ] **Step 5: Commit** `Add: 주호소 정규화 평가`

---

### Task 13: 세션 평가 실행(운영 작업, 코드 변경 없음)

- [ ] **Step 1:** `python -m eval.normalize_eval export --tag s1` → 요청 472개. Claude Code 세션이 각 요청 파일의 system/user를 읽고 schema에 맞는 JSON을 `responses/normalize/{key}.json`에 쓴다(배치로 나눠 진행, 서브에이전트 병렬 허용). `score` 실행, 지표 기록.
- [ ] **Step 2:** `python -m eval.harness export --tag s1` → 정규화(환자) 응답 작성 → 다시 `export` → select 요청 응답 작성 → `run` → `g1`. G1 위반이 있으면 원인 수정(정답 수정 금지) 후 재실행.
- [ ] **Step 3:** `engine` → `g2-export` → 세션이 블라인드 채점(새 서브에이전트, A/B가 무엇인지 모름) → `g2` → `xlsx`.
- [ ] **Step 4:** 결과를 가이드 문서 「평가」·「진행 상황」 절과 `handoff.md`에 반영. 결과 커밋 `Add: 세션 평가 결과(s1)`.

---

### Task 14: G3 — 운영 GPT 재평가(승인 게이트)

- [ ] **Step 1:** 요청 수(정규화 ≤30 + 선택 ≤60)와 예상 비용을 사용자에게 보여 주고 승인을 받는다. **승인 없이는 진행하지 않는다.**
- [ ] **Step 2:** `python -m eval.harness run --tag g3 --llm openai` → `g1` → 세션 결과와 3개 일치율(집합 일치·순위 일치) 보고.
- [ ] **Step 3:** 결과 커밋 `Add: GPT 재평가 결과(g3)`.
