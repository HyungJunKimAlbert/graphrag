# 일반적인 GraphRAG 적용 플랜 — 웰체크 건기식 그래프

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 약사가 자연어로 물으면 LLM이 그래프를 검색해 근거와 함께 답하는 질의응답 경로를 만든다. 기존 규칙 추천 엔진은 바꾸지 않는다.

**Architecture:** 개발 DB를 읽기 전용으로 복제한 전용 Neo4j에 색인(개체 임베딩 → LLM 추출 층 → 커뮤니티 요약)을 쌓는다. 질의는 질문 분석 → 검색(Local·Cypher·Global) → LLM 답변 → 검증 순서로 돈다. 답은 LLM이 정하고, 환자 정보가 있을 때 금기 판정만 기존 규칙이 거부권을 가진다.

**Tech Stack:** Python 3.11, Neo4j 5.26 Community(Bolt), neo4j-graphrag 1.23.0, OpenAI `gpt-6-luna` + `text-embedding-3-small`, leidenalg + igraph, pytest, openpyxl.

**Spec:** 이 문서의 1~5절. 별도 설계 문서는 없다.

> **상태:** 초안(2026-11-07). 승인 전이고 구현은 시작하지 않았다. 2절의 수치는 `supplement-neo4j-dev`를 읽기 전용으로 조회해 얻었다. 이전 대화의 "약사나 환자가 무엇을 할 수 있게 되길 바라는가"에 답을 받지 못해, 용도는 아래 결정 1의 추천안으로 가정했다.

## 먼저 정할 것

아래 7개는 플랜 작성자의 추천안이고 사용자가 확정한 것이 아니다. 1~4번이 정해져야 Phase 0을 시작할 수 있다.

| # | 결정 | 추천안 | 이유 | 다르게 정하면 |
|---|---|---|---|---|
| 1 | 누가 무엇을 묻는가 | 약사가 상담 중 묻는 질의응답 | 평가 문항과 답변 말투가 여기서 정해진다 | 환자용이면 표현 규칙과 위험 신호 처리를 더 엄격하게 다시 짠다 |
| 2 | 기존 추천 엔진과의 관계 | 병행. 엔진은 그대로 두고 GraphRAG는 질의응답 경로로 추가 | 엔진은 같은 입력에 같은 결과를 내고 테스트 161건으로 고정돼 있다. LLM이 답을 정하는 경로는 실행마다 달라질 수 있다 | 대체하면 추천 결과의 재현성이 사라진다 |
| 3 | 색인을 어느 DB에 쌓는가 | 개발 DB를 복제한 전용 컨테이너 | `load_graph.py` L501이 재적재 때 Doc 외 노드를 전부 지워 색인이 사라진다. LLM이 만든 Cypher도 규칙 DB에 닿지 않는다 | 개발 DB에 직접 쌓으면 `load_graph.py`를 고쳐야 한다 |
| 4 | 외부 전송 | 색인은 공개 문서만 전송. 질의는 가상 문항으로만 개발·평가 | 질문 문장이 임베딩과 LLM 호출로 OpenAI에 나간다. 실제 환자 문장의 전송 범위·동의는 기존 플랜의 결정 게이트가 아직 열려 있다 | 로컬 임베딩으로 가면 1536차원 인덱스와 기존 Doc 임베딩을 다시 만든다 |
| 5 | 색인할 문서를 더 넣을 것인가 | 지금 있는 Doc으로 시작하고 Phase 3 전에 다시 판단 | 현재 텍스트는 59,261토큰뿐이다. LLM 추출과 커뮤니티 요약은 문서가 많을수록 효과가 난다 | 고시 전문·상담 매뉴얼·논문 초록이 있으면 Phase 3·4의 가치가 커진다 |
| 6 | 모델 | `gpt-6-luna`(effort low), `text-embedding-3-small` 유지 | 기존 코드와 Doc 임베딩 1,698건이 이 조합이다. 임베딩 모델이 다르면 기존 임베딩과 비교할 수 없다 | 모델을 바꾸면 Doc 임베딩부터 다시 만든다 |
| 7 | 위험 신호 판단 | LLM 판단으로 시작. 기존 플랜의 `RedFlag` 규칙이 그래프에 생기면 규칙을 먼저 적용 | 지금 그래프에는 위험 신호 규칙이 없다 | 규칙이 먼저 있어야 한다면 기존 플랜 Task 6이 선행 조건이 된다 |

---

## 1. 무엇이 달라지나

이전 대화에서 "일반적인 GraphRAG"와 우리 시스템을 가른 기준 6개를 그대로 쓴다. 이 플랜은 기존 추천 경로를 고치지 않고, 6개 기준을 만족하는 경로를 옆에 새로 만든다.

| 기준 | 일반적인 GraphRAG | 지금 우리 | 이 플랜에서 바뀌는 시점 |
|---|---|---|---|
| 목적 | 자유 질문에 답변 | 환자 데이터로 성분 3개 추천 | Phase 1 |
| 입력 | 자연어 질문 | 구조화 데이터 | Phase 1 |
| 검색 | 질문과 관련된 노드·이웃·요약 | 켜진 코드로 고정 Cypher | 노드·이웃은 Phase 1, 요약은 Phase 4 |
| 답을 정하는 주체 | LLM | Python 규칙 | Phase 1 (금기만 규칙이 거부권) |
| LLM이 없으면 | 답변 불가 | 그대로 동작 | Phase 1 |
| 그래프 생성 | LLM이 문서에서 추출 | CSV·엑셀 규칙 | Phase 3 (규칙 그래프 위에 추출 층을 얹는다) |

Phase 1만 끝나도 Neo4j가 말하는 GraphRAG(벡터로 찾고 그래프로 확장)가 된다. Phase 4까지 끝나면 Microsoft GraphRAG의 구성 요소(LLM 추출, 커뮤니티 요약, Local·Global 검색)가 모두 들어간다.

## 2. 현재 DB 실측 (2026-11-07)

| 항목 | 값 | 플랜에 주는 영향 |
|---|---|---|
| Neo4j | 5.26.32 Community. APOC·GDS 없음 | 커뮤니티 탐지는 DB 밖 Python에서 한다. Community 에디션은 인스턴스당 사용자 DB가 1개다 |
| 개체 노드 | 1,220개. Ingredient 552, Symptom 201, Condition 155, DrugItem 91, Company 67, Disease 44, Drug 29, FunctionTag 24, Interest 22, SymptomCategory 14, Segment 10, LifeStage 7, Mechanism 4 | 개체 임베딩 대상 |
| 관계 | ABOUT 제외 3,258개. EXCLUDES 1,462, HAS_FUNCTION 673, INTERACTS_WITH 255, MAY_CAUSE 223, RECOMMENDS 204 순 | 문서에 없는 정보가 관계에 있다. 그래프 확장이 의미가 있는 이유다 |
| 문서 | Doc 1,699건, 88,677자, 59,261토큰. 평균 24~98자, rule 15건만 평균 341자 | 텍스트가 작다 (2.1절) |
| 임베딩 | Doc 1,698건(1536차원). Ingredient·Symptom은 인덱스만 있고 0건 | 질문에서 개체를 찾을 수단이 지금은 없다 |
| 허브 | 임산부 355, 수유부 352, 영유아·어린이 300건의 EXCLUDES. 인구집단 4개가 EXCLUDES 1,015건을 차지 | 이웃 확장과 커뮤니티 탐지에서 따로 다룬다 |
| 고립 노드 | 183개. Symptom 84, Condition 45, Disease 44, LifeStage 7, 문서만 연결된 Ingredient 3 | 이 노드에 대한 질문은 "연결된 근거 없음"이 맞는 답이다 |
| 커뮤니티 시험 | Louvain, 시드 3개. 전체 관계: 14~15개, 최대 302~330개, modularity 0.51~0.53. 인구집단 EXCLUDES 제외: 17~19개, 최대 126~143개, 0.65~0.66 | 인구집단 EXCLUDES를 빼고 탐지한다 |
| 추출 대상 | intake 제외 1,161건, 고유 텍스트 830건, 50,959토큰. "이상사례 발생 시 섭취를 중단하고…" 한 문장이 179회 반복 | 고유 텍스트당 한 번만 추출한다 |
| 패키지 | neo4j-graphrag 1.23.0(2026-10-28)은 numpy≥2.0, neo4j 드라이버≥6.3.1을 요구. 현재 환경은 numpy 1.26.4, 드라이버 미설치 | 전용 가상환경을 쓴다 |
| 컨테이너 | `supplement-neo4j-dev`와 `supplement-neo4j`가 각각 약 1GiB 사용. Docker 메모리 7.65GiB | 세 번째 컨테이너를 띄울 여유가 있다 |

### 2.1 이 데이터에 일반적인 GraphRAG가 맞는가

- **맞는 부분:** 그래프가 이미 있고, 금기·고갈·상호작용 같은 정보가 문서가 아니라 관계에 있다. "메트포르민 복용자가 부족해지기 쉬운 영양소 중 임산부가 피해야 하는 것"처럼 관계를 두 번 건너는 질문은 문서 벡터 검색만으로 답하기 어렵다.
- **약한 부분:** 텍스트가 59,261토큰으로 작고 문서가 짧다. LLM 추출과 커뮤니티 요약은 큰 비정형 문서 묶음에서 효과가 나는 기법이라, 이 데이터에서는 효과가 작을 수 있다. 그래서 Phase 3·4는 앞 단계 측정 뒤에 시작하고, 효과가 없으면 끈다.
- **위험:** LLM이 답을 정하면 금기 성분을 권할 수 있다. 환자 정보가 있을 때는 기존 엔진과 같은 금기 쿼리로 답변을 다시 검사한다(4.4절).

## 3. 일반적인 GraphRAG 구성 요소와 우리 현황

| 구성 요소 | Microsoft GraphRAG | Neo4j GraphRAG | 지금 | 이 플랜 |
|---|---|---|---|---|
| 텍스트 단위 | TextUnit | Chunk | Doc 1,699건 | 그대로 쓴다 |
| 개체 임베딩 | 개체 설명 임베딩 | 벡터 인덱스 | 0건 | Task 3 |
| 질문에서 개체 찾기 | entity mapping | Vector·Hybrid retriever | 없음 | Task 4 |
| 이웃 확장 검색 | Local search | VectorCypher·HybridCypher retriever | 없음 | Task 4 |
| 질의 생성 | 없음 | Text2Cypher retriever | 없음 | Task 8 |
| LLM 개체·관계 추출 | Extract graph | SimpleKGPipeline | 없음 (규칙 구축) | Task 10·11 |
| 커뮤니티 탐지 | 계층형 Leiden | GDS 플러그인 | 없음 | Task 12 |
| 커뮤니티 요약 | Community reports | 1.23.0 포함 여부 미확인 | 없음 | Task 13 |
| 전역 검색 | Global search (map-reduce) | 1.23.0 포함 여부 미확인 | 없음 | Task 14 |
| 답변 생성 | LLM | GraphRAG 클래스 | 설명 문장만 | Task 5 |

## 4. 설계

### 4.1 전체 흐름

```text
[색인 — 오프라인]
supplement-neo4j-dev ──(읽기 전용 복제)──▶ supplement-neo4j-rag
   Phase 1  개체 1,220개 ──설명 문장 + 임베딩──▶ :RagEntity
   Phase 3  Doc 1,161건 ──LLM 추출──▶ 검수 엑셀 ──▶ :XEntity, X_RELATED
   Phase 4  관계 그래프 ──Leiden──▶ :Community ──LLM 요약──▶ 요약 + 임베딩

[질의 — 온라인]
질문 ─▶ 질문 분석(LLM) ─┬─ 위험 신호 ─▶ 중단하고 진료 권유
                        └─ 검색 방식 선택
                             ├─ Local  : 질문 → 개체 찾기 → 이웃 관계·문서
                             ├─ Cypher : LLM이 읽기 전용 Cypher 작성 → 실행
                             └─ Global : 커뮤니티 요약에서 요점 모으기
      ─▶ 문맥 ─▶ (환자 정보가 있으면) 금기 판정을 문맥에 표시
      ─▶ 답변 생성(LLM) ─▶ 검증(인용·금지 표현·금기) ─▶ 응답
```

### 4.2 DB 배치와 추가되는 그래프 구조

전용 컨테이너 `supplement-neo4j-rag`(HTTP 7476, Bolt 7689)에 개발 DB를 복제한다. 개발 DB에는 읽기 쿼리만 보낸다. 복제하면 파생 데이터가 지워지지만, 임베딩과 LLM 응답을 파일로 저장해 두므로 다시 쌓는 데 과금이 없다.

| 추가 | 형태 | 태스크 |
|---|---|---|
| 복제 기록 | `(:RagMeta {id:'current', source_rule_version, cloned_at})` | 1 |
| 개체 공통 라벨 | 기존 개체 노드에 `:RagEntity` 라벨과 `rag_kind`, `rag_text`, `rag_names`, `rag_embedding` 속성 | 3 |
| 개체 인덱스 | 벡터 `rag_entity_embedding`(1536, cosine), 전문 `rag_entity_names`(cjk, `rag_names`) | 3 |
| 추출 개체 | `(:XEntity:RagEntity {xid, name, type, description, review_status:'unreviewed'})` | 11 |
| 추출 관계 | `(a)-[:X_RELATED {rel, description, source_doc_ids, evidence:'llm_extracted', review_status:'unreviewed', conflicts_with_rule}]->(b)` | 11 |
| 문서 언급 | `(:Doc)-[:MENTIONS]->(:XEntity)` | 11 |
| 커뮤니티 | `(:Community {cid, level, size, parent_cid, title, summary, findings, rating, embedding})`, `(:RagEntity)-[:IN_COMMUNITY]->(:Community)` | 12·13 |
| 커뮤니티 인덱스 | 벡터 `community_embedding`(1536, cosine) | 13 |

기존 규칙 관계(`EXCLUDES`, `INTERACTS_WITH`, `RECOMMENDS` 등)와 같은 이름의 관계는 만들지 않는다. 금기 쿼리는 `EXCLUDES`·`INTERACTS_WITH`만 읽으므로, 추출 관계가 금기 판정을 바꿀 수 없다.

### 4.3 질문 유형과 검색 방식

| 유형 | 예 | 맞는 검색 | 평가 문항 수 |
|---|---|---|---|
| 사실 | 마그네슘의 주의사항은? | Local | 20 |
| 다중 홉 | 메트포르민 복용자가 부족해지기 쉬운 영양소 중 임산부가 피해야 하는 것은? | Local, Cypher | 20 |
| 집계·목록 | 수면 건강 기능성 원료는 몇 개이고 개별인정형은 무엇인가? | Cypher | 15 |
| 전체·주제 | 혈압약 복용자에게 조심할 성분을 묶어서 정리해 줘 | Global | 10 |
| 환자 상황 | 와파린·메트포르민을 먹는 80세 남성이 다리에 쥐가 난다. 무엇을 권할 수 있나? | Local + 금기 검사 | 15 |
| 답 없음 | DB에 없는 성분이나 고립 노드에 대한 질문 | "근거 없음"으로 답 | 10 |
| 위험 | 가슴 쥐어짜는 통증, 식은땀 | 검색 없이 중단 | 10 |
| 위험 대조 | 예전에 가슴 통증이 있었으나 지금은 없음 | 중단하지 않음 | 5 |

### 4.4 안전 장치

| 장치 | 동작 | 태스크 |
|---|---|---|
| 위험 신호 | 질문 분석에서 위험 신호면 검색·생성 없이 `stopped`를 돌려준다 | 5 |
| 인용 검증 | 답변의 모든 문장은 문맥에 있던 근거 ID를 하나 이상 인용해야 한다. 없는 ID를 인용하면 답변을 내지 않는다 | 5 |
| 금지 표현 | 기존 `recommend.llm.FORBIDDEN`을 그대로 쓴다. 용량 숫자는 인용한 `intake` 문서 원문에 그대로 있을 때만 허용한다 | 5 |
| 금기 거부권 | 환자 정보가 있으면 기존 `build_context` + `safety.check`로 문맥 속 성분을 판정한다. 금기 성분을 권하는 답변은 `blocked`로 막고 사유를 보여준다 | 6 |
| 판정 불완전 표시 | 모르는 약·질환이 있으면 기존 엔진처럼 "확인되지 않았다"는 경고를 붙인다 | 6 |
| 환자 정보 없음 | 성분을 권하는 답변에 "환자 정보가 없어 금기를 확인하지 않았다"는 고정 문구를 붙인다 | 5 |
| 추출 관계 분리 | `X_RELATED`는 금기 판정에 쓰지 않고, 문맥에 `[미검토·LLM 추출]` 꼬리표를 붙인다. 규칙과 모순되면 문맥에서 뺀다 | 11 |
| 읽기 전용 Cypher | 정적 검사, 읽기 트랜잭션, 시간 제한, 행 수 제한을 모두 건다 | 8 |

### 4.5 라이브러리 선택

| 후보 | 판단 | 이유 |
|---|---|---|
| neo4j-graphrag 1.23.0 | **검색 부품에 사용** | Neo4j 공식 패키지이고 Hybrid·Text2Cypher retriever가 있다. 클래스 시그니처와 `gpt-6-luna` 호출 가능 여부는 Task 0에서 확인한다 |
| Microsoft `graphrag` 3.3.0 | 방법만 차용 | 내가 아는 버전은 자체 파일과 벡터 저장소에 색인하고 Neo4j를 저장소로 쓰지 않는다. 3.3.0에서 바뀌었는지는 확인하지 않았다. 커뮤니티 요약과 Global 검색의 절차만 가져온다 |
| 직접 구현 | 추출, 커뮤니티, 요약, Global 검색 | 추출 결과를 DB에 쓰기 전에 엑셀로 검수해야 하고, 기존 Doc 노드를 그대로 써야 한다. 문서가 짧아 구조화 출력 호출 하나로 충분하다 |
| GDS 플러그인 | 쓰지 않음 | 컨테이너를 다시 만들어야 한다. 노드 1,000개 규모는 Python에서 몇 초면 된다 |

Task 0에서 neo4j-graphrag가 맞지 않으면 같은 인터페이스를 Cypher로 직접 구현한다. Local 검색은 Cypher 두세 개 수준이라 대체할 수 있다.

#### Task 0 확인 결과 (2026-11-07, neo4j-graphrag 1.23.0 설치본)

| 클래스 | import 경로 | 시그니처 요점 |
|---|---|---|
| `HybridCypherRetriever` | `neo4j_graphrag.retrievers` | `(driver, vector_index_name, fulltext_index_name, retrieval_query, embedder=None, result_formatter=None, neo4j_database=None)`. `search(query_text, query_vector=None, top_k=5, effective_search_ratio=1, query_params=None, ranker='naive', alpha=None)` |
| `Text2CypherRetriever` | `neo4j_graphrag.retrievers` | `(driver, llm: LLMInterface, neo4j_schema=None, examples=None, result_formatter=None, custom_prompt=None, neo4j_database=None)`. `get_search_results(query_text, prompt_params=None)` |
| `LLMInterface` | `neo4j_graphrag.llm` | `(model_name, model_params=None, rate_limit_handler=None)`. `invoke(input, message_history=None, system_instruction=None) -> LLMResponse` |
| `OpenAILLM` | `neo4j_graphrag.llm` | `invoke(..., response_format=None)`로 구조화 출력을 받는다 |
| `Embedder` | `neo4j_graphrag.embeddings` | `embed_query(text) -> list[float]` |

- 설치된 버전: neo4j 드라이버 6.4.1, numpy 2.4.6, openai 3.46.0, pydantic 2.14.3, leidenalg 0.12.0, igraph 1.0.0.
- **Local 검색은 Cypher로 직접 구현한다.** `HybridCypherRetriever`는 벡터 인덱스 1개와 전문 인덱스 1개만 받고, 순위 결합이 `naive`·`linear` 둘뿐이다. Task 4는 세 목록(개체 벡터, 이름 전문검색, Doc 벡터의 `ABOUT` 대상)을 순위 역수 합(k=60)으로 합쳐야 해서 맞지 않는다.
- `Text2CypherRetriever`는 Task 8에서 쿼리 생성 부품 후보로 남긴다.
- Step 6(2026-11-08, 승인 후 호출 2건): `wcrag.llm.complete()`(Responses API `responses.parse`, effort low)와 neo4j-graphrag `OpenAILLM(model_name='gpt-6-luna', model_params={'reasoning_effort': 'low'})` 둘 다 `gpt-6-luna`를 정상 호출했다. Task 8에서 `Text2CypherRetriever`에 `OpenAILLM`을 그대로 쓸 수 있다.

### 4.6 평가

- **문항:** 4.3절의 105문항. 사실·다중 홉·집계 유형의 정답은 Cypher로 만들어 고정한다. 위험·위험 대조 문항은 `../graphdb/data/eval/complaint_testset.json`의 `위험` 30건과 `위험대조` 15건에서 고른다.
- **기준선:** B0는 Doc 벡터 검색만 쓰는 일반 RAG다. 각 단계는 자기 유형에서 앞 단계보다 나아야 유지한다.
- **측정값:** 검색 적중(정답 개체가 문맥에 있는가), 답변 집합 F1, 인용 유효 여부, 답 없음 판정, 위험 중단 판정, 금기 위반 건수, 토큰, 지연.
- **출력:** 문항×방식×회차가 한 행인 엑셀. 호출 실패와 미실행도 행으로 남긴다.
- **한계:** 문항과 정답은 Claude 초안이고 약사 검토 전이다. 실서비스 정확도로 읽으면 안 된다.

## 5. 단계와 게이트

| Phase | 내용 | 외부 호출 | 시작 조건 |
|---|---|---|---|
| 0 | 환경, 복제, 평가 문항 (Task 0~2) | 호환 확인 1~2건뿐 | 이 플랜 승인, 결정 1~4 |
| 1 | Local 검색, 답변, 금기 거부권 (Task 3~7) | 임베딩(공개 데이터), 답변(가상 문항) | Phase 0 완료 |
| 2 | Text2Cypher, 검색 방식 선택 (Task 8~9) | 답변(가상 문항) | 게이트 G1 |
| 3 | LLM 추출 층 (Task 10~11) | 추출(공개 문서) | 게이트 G2, 결정 5 |
| 4 | 커뮤니티 요약, Global 검색 (Task 12~14) | 요약(공개 데이터), 답변 | 게이트 G3 |
| 5 | 통합 평가, 결과 문서 (Task 15) | 답변(가상 문항) | 게이트 G4 |

| 게이트 | 통과 조건 | 통과하지 못하면 |
|---|---|---|
| G1 | 금기 위반 0건, 위험 문항 중단 누락 0건, 사실·다중 홉에서 Local이 B0 이상 | Local 검색을 고친다. 뒤 단계로 가지 않는다 |
| G2 | 집계·목록에서 Cypher가 Local보다 높음, 쓰기 쿼리 실행 0건 | Text2Cypher를 기본 경로에서 끄고 Phase 3 진행 여부를 사용자가 정한다 |
| G3 | 추출 표본 100건의 정밀도를 사용자가 수용, 추출 층을 넣은 뒤 Local 점수가 떨어지지 않음 | 추출 층을 검색에서 뺀다 |
| G4 | 전체·주제에서 Global이 Local보다 높음 | 커뮤니티 요약을 기본 경로에서 끈다 |

금기 위반과 위험 중단 누락 외의 숫자 기준은 첫 측정 뒤 사용자와 정한다.

## Global Constraints

- 작업 폴더는 `/Users/user/Documents/projects/wellcheck/graphdb-rag`다. git 저장소가 아니므로 커밋 단계가 없다.
- `../graphdb`의 코드·규칙·CSV·테스트를 수정하지 않는다. 읽기만 하고, 읽는 곳은 `wcrag/legacy.py` 한 파일이다.
- `supplement-neo4j-dev`(7475/7688)에는 읽기 쿼리만 보낸다. `supplement-neo4j`(7474/7687)에는 접속하지 않는다. 쓰기는 `supplement-neo4j-rag`(7476/7689)에만 한다.
- `.env`, `.neo4j-dev.env`, `.neo4j-rag.env`의 값을 출력하지 않는다.
- 전용 가상환경 `graphdb-rag/.venv`만 쓴다. 전역 환경에 neo4j-graphrag를 설치하면 numpy가 2.x로 올라가 기존 환경이 바뀐다.
- 모델은 `gpt-6-luna`(effort `low`), 임베딩은 `text-embedding-3-small`(1536차원)이다.
- 테스트는 외부 호출 없이 돈다. LLM과 임베딩은 가짜 함수를 주입한다.
- OpenAI 호출이 있는 실행은 `--dry-run`으로 건수와 토큰을 먼저 출력하고, 범위를 말하고 승인을 받은 뒤 한다. 평가 실행은 사용자가 직접 한다.
- LLM·임베딩 응답은 파일에 저장한다. 저장 키에 모델·지시문·입력이 들어간다. 같은 입력을 다시 돌리면 과금이 없다.
- 개발과 평가에는 가상 문항만 쓴다. 실제 환자 문장을 넣지 않는다.
- 평가 결과를 본 뒤 정답을 고치지 않는다. 틀려 보이는 정답은 `note`에 표시만 한다.
- 답 없음 판정 문턱값 `MIN_SCORE`는 측정으로 정한다. 측정 전에 임의 값을 넣지 않는다.
- `XEntity`와 `X_RELATED`는 금기 판정에 쓰지 않는다.
- 각 태스크는 실패 테스트 작성 → 실패 확인 → 구현 → `.venv/bin/python -m pytest tests -q` 전체 통과로 끝낸다.

## Review Focus

- **허브 개체가 들어간 질문:** 임산부는 EXCLUDES가 355건이다. 이웃 확장이 문맥을 이 관계로 채우면 안 되고, 관계 유형마다 상한이 걸려야 한다. (Task 4)
- **DB에 없는 대상에 대한 질문:** 가장 가까운 다른 성분으로 답하면 안 되고 `no_evidence`가 나와야 한다. (Task 5, 7)
- **고립 노드에 대한 질문:** 증상 84개는 연결이 없다. "관련 성분이 없다"를 "효과가 없다"나 "안전하다"로 답하면 안 되고, 연결된 근거가 없다고 답해야 한다. (Task 4, 5)
- **금기 성분을 권하는 답변:** 와파린 복용자에게 오메가3를 권하면 `blocked`가 돼야 한다. "피해야 한다"는 언급은 통과해야 한다. (Task 6)
- **LLM이 만든 Cypher:** 쓰기, 프로시저 호출, 무제한 조회, 임베딩 벡터 반환이 모두 막혀야 한다. 정적 검사를 빠져나간 쓰기도 읽기 트랜잭션에서 거절돼야 한다. (Task 8)

## 파일 구조

```text
graphdb-rag/
├── 261107_general_graphrag_plan.md
├── requirements.txt
├── docker-compose.yml             # supplement-neo4j-rag
├── .neo4j-rag.env                 # 접속 정보. 출력 금지
├── wcrag/
│   ├── config.py                  # 경로, 모델명, 기본값 상수
│   ├── db.py                      # Bolt 접속. Graph.run, Graph.read
│   ├── llm.py                     # complete(), embed(), 파일 저장
│   ├── legacy.py                  # ../graphdb/recommend를 읽는 유일한 파일
│   ├── index/clone.py             # dev → rag 복제
│   ├── index/entities.py          # 개체 설명, 임베딩, 인덱스
│   ├── index/extract.py           # 문서 → 개체·관계 (파일로 저장)
│   ├── index/resolve.py           # 추출 결과 해소와 적재
│   ├── index/communities.py       # Leiden
│   ├── index/reports.py           # 커뮤니티 요약
│   ├── retrieve/vector_only.py    # 기준선 B0
│   ├── retrieve/local.py          # 개체 찾기 + 이웃 확장
│   ├── retrieve/cypher.py         # Text2Cypher + 읽기 전용 실행
│   ├── retrieve/global_search.py  # 커뮤니티 요약 map-reduce
│   ├── retrieve/merge.py          # 여러 검색 결과 합치기
│   ├── answer/analyze.py          # 위험 신호, 환자 단서, 검색 방식
│   ├── answer/generate.py         # 답변 생성
│   ├── answer/guard.py            # 인용, 금지 표현, 금기 검증
│   ├── answer/ask.py              # 분석 → 검색 → 생성 → 검증을 잇는 진입점
│   ├── answer/safety_tool.py      # 금기 판정
│   └── cli.py                     # status, ask
├── eval/
│   ├── questions.json
│   ├── build_gold.py
│   ├── gold.json
│   └── run.py
├── tests/
└── out/                           # 저장한 응답, 추출 결과, 평가 엑셀
```

패키지 이름은 `wcrag`다. `graphrag`(Microsoft)나 `neo4j_graphrag`와 겹치지 않게 했다.

---

# Phase 0: 준비

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

### Task 1: 개발 DB 복제

**Files:**
- Create: `wcrag/index/clone.py`, `tests/test_clone.py`

**Interfaces:**
- Consumes: `connect("dev")`, `connect("rag")`
- Produces: `clone(src: Graph, dst: Graph) -> dict[str, int]`. 실행은 `python -m wcrag.index.clone`.
- `dst`의 기존 데이터를 모두 지우고 `src`의 노드·관계·속성(임베딩 포함)을 복사한다. 이어서 `../graphdb/schema/supplement-graph-schema.cypher`의 제약과 인덱스를 적용하고 `RagMeta`를 쓴다.
- `src`에는 `Graph.read`만 쓴다.

- [ ] **Step 1: 실패 테스트 작성.**
  - `test_counts_match_by_label_and_type`: 라벨별 노드 수와 유형별 관계 수가 `src`와 같다
  - `test_doc_embeddings_copied`: 임베딩이 있는 Doc 수가 `src`와 같다
  - `test_source_receives_only_reads`: 기록용 래퍼로 `src.run` 호출이 0회임을 확인한다
  - `test_rule_version_recorded`: `RagMeta.source_rule_version`이 `src`의 `RuleSet.version`과 같다
  - `test_schema_indexes_online`
- [ ] **Step 2:** 실패를 확인하고 구현한다. 1,000행 단위 `UNWIND`로 쓴다.
- [ ] **Step 3:** `wcrag/cli.py`에 `status`를 만든다. `RagMeta`의 규칙 버전과 개발 DB의 현재 버전을 나란히 출력하고, 다르면 다시 복제하라고 알린다.

### Task 2: 평가 문항, 정답, 채점기

**Files:**
- Create: `eval/questions.json`, `eval/build_gold.py`, `eval/gold.json`, `eval/run.py`, `tests/test_eval.py`

**Interfaces:**
- 문항: `{id, type, question, patient?, gold: {kind, cypher?, values?, must_not?}, note}`. `gold.kind`는 `set`, `count`, `refuse`, `stop`, `no_stop`, `rubric` 중 하나다.
- `build_gold.py`는 `gold.cypher`를 rag DB에서 실행해 `gold.json`에 값과 규칙 버전을 고정한다.
- Produces: `score(question: dict, gold: dict, result: dict) -> dict`. 키는 `retrieval_hit`, `answer_f1`, `citation_valid`, `refused_ok`, `stopped_ok`, `safety_violation`, `tokens_in`, `tokens_out`, `latency_s`다.
- Consumes: Task 5의 `ask()`가 돌려주는 `Result`. 그 전에는 가짜 응답기로 테스트한다.
- 실행: `python eval/run.py --run --tag <태그> --modes vector,local --types 사실,다중홉`, 엑셀만 다시 만들 때는 `--report-only --tag <태그>`.
- 출력: `out/eval/<태그>.xlsx`. 문항×방식×회차가 한 행이다.

- [ ] **Step 1:** 4.3절의 유형별 수대로 105문항을 쓴다. 다중 홉 문항은 두 개체를 잇는 관계가 실제로 있는지 Cypher로 확인한 것만 넣는다. 답 없음 문항은 DB에 없거나 고립된 대상임을 Cypher로 확인한 것만 넣는다.
- [ ] **Step 2: 실패 테스트 작성.**
  - `test_set_gold_scored_by_f1`
  - `test_refuse_gold_requires_no_evidence_status`
  - `test_stop_gold_requires_stopped_status`, `test_no_stop_gold_fails_on_stopped`
  - `test_safety_violation_counts_recommended_avoid`
  - `test_failed_call_is_a_row`
  - `test_report_only_makes_no_calls`
  - `test_gold_frozen_with_rule_version`
- [ ] **Step 3:** 구현하고 통과시킨다.

---

# Phase 1: Local 검색과 답변

### Task 3: 개체 설명과 임베딩

**Files:**
- Create: `wcrag/llm.py`, `wcrag/index/entities.py`, `tests/test_entities.py`

**Interfaces:**
- Produces: `llm.embed(texts: list[str]) -> list[list[float]]`. `out/embeddings.jsonl`에 `sha256(모델|문장)` 키로 저장한다.
- Produces: `llm.complete(instructions: str, payload: str, schema: type[BaseModel], tag: str) -> BaseModel`. OpenAI Responses API의 구조화 출력을 쓰고 `out/llm_cache/<tag>.jsonl`에 저장한다.
- Produces: `entities.describe(label: str, props: dict, neighbors: dict) -> tuple[str, list[str]]`. `(rag_text, rag_names)`를 돌려준다. LLM을 쓰지 않는다.
- Produces: `entities.build(graph: Graph, embed=llm.embed, dry_run: bool = False) -> dict`.
- 개체 키는 `<라벨>:<고유값>`이다. 고유값은 Ingredient `ingredient_id`, Symptom `symptom_id`, Drug `drug_id`, Condition `code`, DrugItem `item_id`, FunctionTag `tag_id`, SymptomCategory·Interest·Segment·Disease·LifeStage `code`, Mechanism `mechanism_id`, Company `company_id`다.

| 라벨 | `rag_text`에 넣는 것 | `rag_names`에 넣는 것 |
|---|---|---|
| Ingredient | 이름, 유형, `function_text` 앞부분, 연결된 기능 태그 이름 | 이름, `../graphdb/data/mapping/nutrient_alias.csv`의 별칭 |
| Symptom | 이름, 분류, 별칭 | 이름, `aliases` |
| Drug | 이름, 계열, 성분명, 부작용 요약 | 이름, `generic_names` |
| DrugItem | 이름, 속한 계열 이름 | 이름, `match` |
| Condition | 라벨, 종류, 검진 항목과 구간 | 라벨 |
| 그 외 | 이름과 정의 | 이름 |

- [ ] **Step 1: 실패 테스트 작성.**
  - `test_every_entity_gets_text_names_and_label`: Doc·RuleSet·RagMeta를 뺀 모든 노드
  - `test_aliases_go_into_names`: Symptom `aliases`, Drug `generic_names`, DrugItem `match`
  - `test_dry_run_calls_no_embed`
  - `test_cached_text_not_reembedded`: 복제 후 다시 돌려도 `embed` 호출 0회
  - `test_doc_without_embedding_is_filled`: 임베딩 없는 Doc 1건도 채운다
  - `test_indexes_online`: `rag_entity_embedding`, `rag_entity_names`
- [ ] **Step 2:** 실패를 확인하고 구현한다.
- [ ] **Step 3 (승인 후):** `--dry-run`으로 건수와 토큰을 확인하고 임베딩을 실행한다.

### Task 4: Local 검색

**Files:**
- Create: `wcrag/retrieve/local.py`, `wcrag/retrieve/vector_only.py`, `tests/test_local.py`

**Interfaces:**
- Produces: `local.retrieve(graph: Graph, question: str, embed) -> Context`
- Produces: `vector_only.retrieve(graph: Graph, question: str, embed) -> Context`. Doc 벡터 상위 10건만 넣고 `facts`는 비운다.
- `Context = {seeds: [{key, kind, name, score, isolated}], facts: [{id, text, start, type, end, sources}], docs: [{id, kind, text, ingredient_id}], ingredient_ids: [str], max_seed_score: float}`
- 사실 ID는 `"F-" + sha1(f"{시작 키}|{관계 유형}|{끝 키}")[:10]`이다. 문서의 근거 ID는 `doc_id`다.
- `start`·`type`·`end`는 그래프 관계에서 온 사실에만 채운다. `sources`는 다른 근거에서 만든 사실(Task 14)의 원래 근거 ID 목록이고, 그 외에는 빈 목록이다.

검색 순서는 아래와 같다.

1. **시드 찾기.** 개체 벡터 상위 10건, 이름 전문검색 상위 10건, Doc 벡터 상위 10건의 `ABOUT` 대상을 구한다. 세 목록을 순위 역수 합(k=60)으로 합쳐 상위 8개를 시드로 삼는다.
2. **이웃 확장.** 시드마다 `ABOUT`·`MENTIONS`를 뺀 1홉 관계를 관계 유형당 5건까지 가져온다. 순서는 이웃의 `rag_embedding`과 질문의 코사인 유사도다.
3. **시드 잇기.** 시드 쌍 사이에 길이 2 이하 경로가 있으면 그 관계를 더한다.
4. **문서.** 문맥에 든 성분마다 기능·주의 문서를 먼저 넣고 유사도 순으로 4건까지 채운다.
5. **예산.** 6,000토큰을 넘으면 시드 순위가 낮은 쪽의 사실과 문서부터 뺀다.

- [ ] **Step 1: 실패 테스트 작성.** rag DB를 쓰고 `embed`는 저장된 임베딩을 돌려주는 가짜 함수다.
  - `test_drug_question_brings_depletion_facts`: "메트포르민을 먹으면 부족해지는 영양소"의 `facts`에 엽산, 코엔자임Q10, 비타민 B12 고갈이 모두 있다
  - `test_hub_relation_capped`: 임산부가 시드일 때 `EXCLUDES` 사실이 5건 이하다
  - `test_pair_path_joins_two_seeds`: 메트포르민과 임산부를 함께 물으면 둘을 잇는 성분의 사실이 있다
  - `test_isolated_seed_marked`: 고립 증상이 시드이면 `isolated`가 참이고 그 시드의 사실이 없다
  - `test_token_budget_respected`
  - `test_same_question_same_context`
  - `test_vector_only_has_no_facts`
- [ ] **Step 2:** 실패를 확인하고 구현한다. 관계 유형별 사실 문장 틀은 `local.py`의 표 하나에 둔다.

### Task 5: 질문 분석, 답변 생성, 검증

**Files:**
- Create: `wcrag/answer/analyze.py`, `wcrag/answer/generate.py`, `wcrag/answer/guard.py`, `wcrag/answer/ask.py`, `tests/test_answer.py`
- Modify: `wcrag/cli.py`

**Interfaces:**
- Produces: `analyze(question: str, complete) -> Route`. `Route = {modes: list[str], red_flag: bool, red_flag_reason: str, patient: PatientHint | None}`, `PatientHint = {age, sex, conditions, drugs, pregnant, lactating}`. 허용 방식은 상수 `ENABLED_MODES = ["local"]`로 제한한다.
- Produces: `generate(question: str, context: Context, complete, verdicts: dict | None = None) -> Draft`. `Draft = {answerable: bool, claims: [{text, evidence_ids}], recommended_ingredient_ids: [str], cautioned_ingredient_ids: [str]}`. 성분 ID 필드는 문맥의 `ingredient_ids`만 허용하는 타입으로 만든다.
- Produces: `guard.check(draft: Draft, context: Context, verdicts: dict | None = None) -> tuple[bool, list[str]]`
- Produces: `ask.ask(graph, question: str, patient: dict | None = None, mode: str = "auto", *, embed, complete) -> Result`. `Result = {status, text, claims, recommended, cautioned, evidence, context_keys, safety_checked, safety_incomplete, warnings, mode, usage}`. `status`는 `answered`, `no_evidence`, `stopped`, `blocked`, `rejected` 중 하나다.
- `mode`는 `auto`, `vector`, `local` 중 하나다. `vector`는 `vector_only.retrieve`를 쓰는 기준선 B0이고 `auto`는 `Route.modes`를 따른다. Phase 2와 4에서 `cypher`, `global`이 더해진다.
- `context_keys`는 문맥에 든 개체 키와 문서 ID 목록이다. 평가의 `retrieval_hit`이 이 값을 쓴다. `usage`는 `{tokens_in, tokens_out, latency_s}`다.
- 실행: `python -m wcrag.cli ask "<질문>" [--patient <파일>] [--mode local]`

| 조건 | 결과 |
|---|---|
| `red_flag`가 참 | `stopped`. 검색과 생성을 호출하지 않는다 |
| `answerable`이 거짓이거나 `max_seed_score < MIN_SCORE` | `no_evidence` |
| 인용 없는 문장, 문맥에 없는 근거 ID, 금지 표현, 분류되지 않은 성분 이름 | `rejected`. 답변 문장은 숨기고 사유만 보여준다 |
| 성분을 권했는데 환자 정보가 없음 | `answered`에 고정 문구를 붙이고 `safety_checked`는 거짓 |

`MIN_SCORE`는 Task 7에서 정한다. 그 전에는 `answerable`만 쓴다.

- [ ] **Step 1: 실패 테스트 작성.** `complete`와 `embed`는 가짜 함수다.
  - `test_red_flag_stops_before_retrieval`: `embed` 호출 0회
  - `test_claim_without_evidence_rejected`
  - `test_unknown_evidence_id_rejected`
  - `test_unanswerable_returns_no_evidence`
  - `test_forbidden_expression_rejected`
  - `test_intake_number_allowed_only_when_quoted`: 인용한 `intake` 문서 원문에 있는 숫자는 통과하고, 없는 숫자는 거절된다
  - `test_ingredient_named_in_text_must_be_classified`: 문장에 나온 성분이 권함·주의 목록 어디에도 없으면 거절된다
  - `test_recommendation_without_patient_adds_notice`
  - `test_isolated_seed_answer_says_no_linked_evidence`
- [ ] **Step 2:** 실패를 확인하고 구현한다. 금지 표현은 Task 6의 `legacy.FORBIDDEN`을 쓰므로, 이 태스크에서는 같은 이름의 임시 상수로 시작하고 Task 6에서 바꾼다.

### Task 6: 금기 거부권

**Files:**
- Create: `wcrag/legacy.py`, `wcrag/answer/safety_tool.py`, `tests/test_safety_tool.py`
- Modify: `wcrag/answer/generate.py`, `wcrag/answer/guard.py`

**Interfaces:**
- Produces: `legacy.FORBIDDEN`, `legacy.build_context(graph: Graph, patient: dict) -> dict`, `legacy.safety_check(graph: Graph, ingredient_ids: list[str], ctx: dict) -> dict`. `../graphdb/recommend`의 `llm.FORBIDDEN`, `normalize.build_context`, `safety.check`를 그대로 부른다. `graph`는 `run`이 `Graph.read`로 가는 어댑터로 감싼다.
- Produces: `safety_tool.verdicts(graph: Graph, patient: dict, ingredient_ids: list[str]) -> {by_ingredient: {id: {allowed, avoid, caution}}, incomplete: list[str]}`
- 환자 형식은 `../graphdb/tests/fixtures/patient_80m.json`과 같다.
- 흐름: 검색 → 문맥의 `ingredient_ids` 판정 → 금기 성분의 사실에 `[이 환자에게 금기: 사유]`를 붙임 → 생성 → `guard`가 `recommended_ingredient_ids`에 금기 성분이 있으면 `blocked`.
- 질문에서 뽑은 `PatientHint`에 나이나 성별이 없으면 판정하지 않고 `safety_checked`를 거짓으로 두며 경고를 붙인다.

- [ ] **Step 1: 실패 테스트 작성.** rag DB와 80세 남성 픽스처를 쓴다.
  - `test_verdicts_equal_engine`: 같은 환자와 성분에 대해 개발 DB에서 돌린 `recommend.safety.check` 결과와 같다
  - `test_warfarin_patient_omega3_recommendation_blocked`
  - `test_mention_as_caution_is_allowed`: 오메가3가 주의 목록에만 있으면 `answered`
  - `test_unknown_drug_sets_incomplete`
  - `test_no_age_or_sex_means_unchecked`
- [ ] **Step 2:** 실패를 확인하고 구현한다.

### Task 7: Phase 1 측정과 게이트 G1

- [ ] **Step 1 (사용자 실행):** `python eval/run.py --run --tag p1 --modes vector,local --types 사실,다중홉,답없음,환자상황,위험,위험대조`
- [ ] **Step 2:** 답 없음 문항과 사실 문항의 `max_seed_score` 분포를 표로 만들어 `MIN_SCORE`를 정하고 `wcrag/config.py`에 넣는다. 두 분포가 겹쳐 가를 수 없으면 `answerable` 플래그만 쓰기로 하고 그 사실을 기록한다.
- [ ] **Step 3:** 유형×방식 표와 실패 문항 목록을 사용자에게 보고하고 G1을 판단한다.

---

# Phase 2: Text2Cypher와 검색 방식 선택

### Task 8: Text2Cypher

**Files:**
- Create: `wcrag/retrieve/cypher.py`, `tests/test_cypher.py`

**Interfaces:**
- Produces: `cypher.is_safe(query: str) -> tuple[bool, str]`
- Produces: `cypher.retrieve(graph: Graph, question: str, complete) -> Context`. 결과 행을 문장으로 바꿔 `facts`에 넣는다. ID는 `Q-<행 번호>`이고, 실행한 쿼리는 `Context["cypher"]`에 남긴다.
- `is_safe`는 `CREATE`, `MERGE`, `SET`, `DELETE`, `REMOVE`, `DROP`, `LOAD CSV`, `CALL`, `FOREACH`와 세미콜론으로 이은 여러 문장을 거절한다.
- 실행은 `Graph.read`(10초 제한)로 한다. `LIMIT`이 없으면 `LIMIT 50`을 붙이고, 반환값에서 `embedding`과 `rag_embedding`을 뺀다.
- 지시문에는 라벨·관계·주요 속성, 관계별 의미 한 줄, 예시 질문·쿼리 10쌍을 넣는다. 예시는 평가 문항과 겹치지 않게 한다. Phase 3 뒤에는 `X_RELATED`에 "LLM 추출, 미검토"를 적는다.
- 문법 오류가 나면 오류 문장을 넣어 한 번만 다시 만든다. 그래도 실패하면 빈 `Context`를 돌려준다.

- [ ] **Step 1: 실패 테스트 작성.** `complete`는 고정 쿼리를 돌려주는 가짜 함수다.
  - `test_write_keywords_rejected`: 금지어마다 한 건씩
  - `test_multiple_statements_rejected`
  - `test_limit_added_when_missing`
  - `test_embeddings_stripped_from_rows`
  - `test_syntax_error_retried_once_then_empty`
  - `test_write_blocked_by_read_transaction`: `is_safe`를 건너뛰어도 쓰기가 실행되지 않는다
  - `test_count_question_returns_number`: 수면 건강 태그 원료 수가 7이다
- [ ] **Step 2:** 실패를 확인하고 구현한다. Task 0에서 `Text2CypherRetriever`가 맞다고 확인됐으면 쿼리 생성에 쓰고, 아니면 `complete`로 직접 만든다. 검사와 실행은 어느 쪽이든 이 파일의 코드를 거친다.

### Task 9: 검색 방식 선택과 게이트 G2

**Files:**
- Create: `wcrag/retrieve/merge.py`
- Modify: `wcrag/answer/analyze.py`, `wcrag/answer/ask.py`, `tests/test_answer.py`

**Interfaces:**
- `ENABLED_MODES`에 `"cypher"`를 더한다.
- Produces: `merge.merge(contexts: list[Context]) -> Context`. 같은 ID의 사실과 문서를 한 번만 남긴다.
- `ask(..., mode="auto")`는 `Route.modes`의 방식을 모두 돌려 합친다. `mode`를 지정하면 그 방식만 쓴다.

- [ ] **Step 1: 실패 테스트 작성.** `test_count_question_uses_cypher`, `test_merge_dedupes_by_id`, `test_mode_override_skips_router`
- [ ] **Step 2:** 구현하고 통과시킨다.
- [ ] **Step 3 (사용자 실행):** `python eval/run.py --run --tag p2 --modes local,cypher,auto --types 사실,다중홉,집계`. 결과로 G2를 판단한다.

---

# Phase 3: LLM 추출 층

### Task 10: 문서에서 개체와 관계 추출

**Files:**
- Create: `wcrag/index/extract.py`, `tests/test_extract.py`
- 산출물: `out/extraction.jsonl`, `out/extraction_review.xlsx`

**Interfaces:**
- Produces: `extract.run(graph: Graph, complete, dry_run: bool = False) -> dict`. DB에 쓰지 않는다.
- 출력 형식: `{text_hash, doc_ids, entities: [{name, type, description}], relations: [{source, target, rel, description}]}`
- 개체 `type`은 `세부기능`, `주의대상`, `부작용`, `식품`, `건강개념`, `성분`, `약물`, `질환`, `증상` 중 하나다.
- 관계 `rel`은 `도움`, `주의`, `피함`, `상담필요`, `유발`, `함유`, `관련` 중 하나다.
- 대상은 `kind`가 `intake`가 아닌 Doc이다. 같은 문장은 한 번만 추출한다.
- 문서의 주어가 생략돼 있으므로 지시문에서 주어를 `{이 성분}`으로 부르게 한다. 적재할 때 그 문장을 가진 모든 Doc의 `ABOUT` 성분으로 펼친다.
- 모든 성분에 공통으로 붙는 문구("이상사례 발생 시 섭취를 중단하고 전문가와 상담할 것" 등)에서는 관계를 만들지 않는다. 대상 문구 목록은 30회 이상 반복되는 줄로 정하고 `out/boilerplate.txt`에 남긴다.
- 검수 엑셀은 관계당 한 행이다. 열은 문서 ID, 원문, 추출 관계, 기존 그래프와의 관계(`같음`, `없음`, `모순`)다.

- [ ] **Step 1: 실패 테스트 작성.** `complete`는 가짜 함수다.
  - `test_unique_text_extracted_once`
  - `test_intake_docs_skipped`
  - `test_boilerplate_line_yields_no_relation`
  - `test_subject_placeholder_fans_out_to_about_ingredients`
  - `test_dry_run_reports_counts_only`
  - `test_failed_batch_recorded_not_dropped`
  - `test_review_sheet_has_one_row_per_relation`
- [ ] **Step 2:** 실패를 확인하고 구현한다.
- [ ] **Step 3 (승인 후):** `--dry-run`으로 호출 수와 토큰을 확인하고, 먼저 50건만 추출해 결과를 사용자에게 보인 뒤 전체를 실행한다.
- [ ] **Step 4:** 무작위 100건의 관계를 사람이 읽어 맞음·틀림을 표시하고 정밀도를 기록한다.

### Task 11: 개체 해소와 적재, 게이트 G3

**Files:**
- Create: `wcrag/index/resolve.py`, `tests/test_resolve.py`
- Modify: `wcrag/retrieve/local.py`
- 산출물: `out/resolve_review.xlsx`

**Interfaces:**
- Consumes: `out/extraction.jsonl`
- Produces: `resolve.load(graph: Graph, embed, path: Path) -> dict`
- 이름을 NFC로 맞추고 공백을 지운 뒤 기존 개체의 `rag_names`와 정확히 같으면 그 노드를 쓴다. 같지 않으면 `XEntity`를 만든다. 임베딩이 가까운 후보는 자동으로 합치지 않고 `resolve_review.xlsx`에 적는다.
- 추출 관계가 `도움`인데 같은 두 노드 사이에 `EXCLUDES`나 `INTERACTS_WITH`가 있으면 `conflicts_with_rule`을 참으로 둔다.
- `local.retrieve`는 `X_RELATED`를 이웃 확장에 넣되 `conflicts_with_rule`이 참인 것은 빼고, 사실 문장 끝에 `[미검토·LLM 추출]`을 붙인다.

- [ ] **Step 1: 실패 테스트 작성.**
  - `test_exact_name_resolves_to_existing_node`
  - `test_unresolved_becomes_xentity_with_embedding`
  - `test_conflict_with_rule_flagged_and_excluded_from_context`
  - `test_safety_verdict_unchanged_after_load`: 적재 전후로 80세 남성 픽스처의 금기 판정이 같다
  - `test_load_is_idempotent`
  - `test_fact_text_tagged_unreviewed`
- [ ] **Step 2:** 실패를 확인하고 구현한다.
- [ ] **Step 3 (사용자 실행):** `python eval/run.py --run --tag p3 --modes local,auto --types 사실,다중홉,환자상황`. `p1`·`p2`와 비교해 G3을 판단한다.

---

# Phase 4: 커뮤니티 요약과 Global 검색

### Task 12: 커뮤니티 탐지

**Files:**
- Create: `wcrag/index/communities.py`, `tests/test_communities.py`

**Interfaces:**
- Produces: `communities.build(graph: Graph, seed: int = 42) -> dict`
- 투영에는 `RagEntity` 노드와 그 사이 관계를 쓴다. `ABOUT`, `MENTIONS`, 그리고 `kind = 'population'`인 Condition의 `EXCLUDES`는 뺀다.
- `leidenalg`로 level 0을 나누고, 크기가 30을 넘는 커뮤니티를 다시 나눠 level 1을 만든다. 30 이하인 커뮤니티는 level 1에서도 그대로 한 커뮤니티다.
- 관계가 없는 노드는 커뮤니티에 넣지 않고 수를 결과에 남긴다.
- 사람이 만든 묶음도 같은 형식으로 만든다. FunctionTag 24개, SymptomCategory 14개, Drug 29개 각각과 직접 연결된 노드를 `level = 'curated'` 커뮤니티로 둔다.

- [ ] **Step 1: 실패 테스트 작성.**
  - `test_same_seed_same_partition`
  - `test_connected_entity_in_exactly_one_level0`
  - `test_level1_nested_in_level0`
  - `test_isolated_nodes_reported_not_assigned`
  - `test_population_excludes_not_in_projection`
  - `test_curated_communities_match_tag_counts`: 24 + 14 + 29개
- [ ] **Step 2:** 실패를 확인하고 구현한다. 커뮤니티 수와 크기 분포를 `out/communities_report.md`에 남긴다.

### Task 13: 커뮤니티 요약

**Files:**
- Create: `wcrag/index/reports.py`, `tests/test_reports.py`

**Interfaces:**
- Produces: `reports.build(graph: Graph, complete, embed, level: str, dry_run: bool = False) -> dict`
- 요약 형식: `{title, summary, findings: [{text, evidence_ids}], rating}`. `rating`은 1~10이다.
- 입력은 구성원의 `rag_text`, 내부 관계의 사실 문장, 대표 문서다. 8,000토큰을 넘으면 연결 수가 많은 개체와 그 관계부터 넣는다.
- level 1을 먼저 만들고, level 0은 하위 커뮤니티의 요약을 입력으로 쓴다.
- `findings`의 근거 ID가 입력에 없던 것이면 그 항목을 빼고 기록한다.
- 요약 문장을 임베딩해 `Community.embedding`에 넣고 `community_embedding` 인덱스를 만든다.

- [ ] **Step 1: 실패 테스트 작성.** `test_finding_with_unknown_evidence_dropped`, `test_parent_uses_child_summaries`, `test_budget_keeps_high_degree_members`, `test_dry_run_reports_counts_only`, `test_cached_report_not_regenerated`
- [ ] **Step 2:** 실패를 확인하고 구현한다.
- [ ] **Step 3 (승인 후):** `--dry-run`으로 건수와 토큰을 확인하고 실행한다.

### Task 14: Global 검색과 게이트 G4

**Files:**
- Create: `wcrag/retrieve/global_search.py`, `tests/test_global.py`
- Modify: `wcrag/answer/analyze.py`

**Interfaces:**
- Produces: `global_search.retrieve(graph: Graph, question: str, embed, complete, level: str = "1") -> Context`
- 질문 임베딩으로 커뮤니티 요약 상위 5개를 고른다. 요약마다 질문과 관련된 요점과 점수(0~100)를 뽑고, 0점은 버린다. 남은 요점을 `facts`에 넣는다. ID는 `C-<cid>-<번호>`이고 원래 근거 ID를 함께 둔다. 합치는 일은 `generate`가 한다.
- `ENABLED_MODES`에 `"global"`을 더한다.

- [ ] **Step 1: 실패 테스트 작성.** `test_zero_score_points_dropped`, `test_fact_ids_trace_to_community`, `test_map_calls_bounded_by_top_k`, `test_theme_question_uses_global`
- [ ] **Step 2:** 실패를 확인하고 구현한다.
- [ ] **Step 3 (사용자 실행):** `python eval/run.py --run --tag p4 --modes local,global,auto --types 전체·주제`. `level`을 `1`과 `curated`로 각각 돌려 비교하고 G4를 판단한다.

---

# Phase 5: 통합 평가

### Task 15: 전체 평가와 결과 문서

**Files:**
- Create: `RESULT.md`, `handoff.md`

- [ ] **Step 1 (사용자 실행):** `python eval/run.py --run --tag final --modes vector,auto`를 전체 유형으로 돌린다. 유형별 비교가 필요한 방식은 앞 단계 태그의 결과를 쓴다.
- [ ] **Step 2:** `RESULT.md`에 유형×방식 점수표, 실패 유형, 금기 위반과 위험 중단 누락 건수, 토큰과 지연, 1절의 6개 기준표 갱신본, 꺼 둔 구성 요소와 이유, 남은 한계를 적는다.
- [ ] **Step 3:** `handoff.md`를 쓴다.

---

## 호출 규모 추정

`gpt-6-luna` 단가는 확인되지 않아 금액은 적지 않는다. 아래 값은 추정이고, 실제 값은 각 단계의 `--dry-run`이 출력한다.

| 작업 | 호출 수 | 입력 규모 | 근거 |
|---|---|---|---|
| 개체 임베딩 | 개체 1,220개 + Doc 1건 | Doc 전체 59,261토큰과 비슷하거나 작음 | 개체 설명이 문서보다 짧다 |
| 문서 추출 | 고유 텍스트 830건. 10건씩 묶으면 83회 | 본문 50,959토큰 + 호출마다 지시문 | 실측 |
| 커뮤니티 요약 | Leiden 커뮤니티 수 + 사람이 만든 묶음 67개 | 요약당 최대 8,000토큰 | Leiden 커뮤니티 수는 Task 12 뒤에 정해진다 |
| 평가 전체 1회 | 105문항 × 방식 수 | 답변 호출당 문맥 최대 6,000토큰. 5개 방식이면 최대 315만 토큰 | 상한. 단계별로 유형을 나눠 돌린다 |
| Global 질의 1건 | 요점 추출 5회 + 답변 1회 | 요약 5개 + 문맥 | Task 14 |

## 이번 범위 밖

- 기존 추천 엔진, 규칙 CSV, `load_graph.py`, 개발 DB의 변경.
- `261106_graph_centric_recommend_rework.md`의 태스크. 그 플랜이 구현돼 `normalize`의 입출력이 바뀌면 `wcrag/legacy.py`만 고친다.
- 운영 DB 적재, API 서버, 화면.
- 실제 환자 문장 처리. 결정 4가 정해진 뒤 별도로 다룬다.
- 추출 관계를 약사 검토를 거쳐 규칙으로 올리는 절차.
- 제품 추천과 용량 처방.

## 구현자가 정해도 되는 것

개체 설명 문장의 표현, 관계 유형별 사실 문장의 표현, LLM 지시문의 문구, 복제 방식의 세부, 엑셀 서식, Text2Cypher 예시 10쌍의 내용.

## 플랜 작성자의 판단 (사용자 미확정)

1. "먼저 정할 것"의 7개 추천안 전부.
2. 진행 순서를 Microsoft 방식(추출 → 커뮤니티 → 검색)이 아니라 Local → Cypher → 추출 → 커뮤니티로 잡았다. 지금 그래프만으로 효과를 볼 수 있는 것을 먼저 하고, 효과가 불확실한 것을 뒤에 두었다.
3. LLM이 답을 정하되 금기는 규칙이 거부권을 가진다. 엄밀히는 "답을 정하는 주체가 LLM"이라는 기준에 예외를 둔 것이다.
4. 용량 숫자는 인용한 `intake` 문서 원문에 있을 때만 허용한다. 기존 금지 표현을 그대로 쓰면 "하루 섭취량은?"에 답할 수 없다.
5. 커뮤니티 탐지에서 인구집단 EXCLUDES를 뺀다.
6. 사람이 만든 묶음(기능 태그 등)의 요약을 Leiden 커뮤니티와 함께 만들어 비교한다.
7. 평가 문항 수와 유형별 배분(105문항).
8. 문맥 6,000토큰, 시드 8개, 관계 유형당 5건 같은 기본값. 평가 뒤에 바꿀 수 있다.

## 참고

- 패키지 버전은 2026-11-07에 PyPI에서 조회했다: [neo4j-graphrag](https://pypi.org/project/neo4j-graphrag/) 1.23.0, graphrag 3.3.0, leidenalg 0.12.0, igraph 1.0.0, neo4j 6.4.1.
- [Neo4j GraphAcademy — GraphRAG Retrievers](https://graphacademy.neo4j.com/courses/workshop-graphrag-introduction/3-querying/2-retrievers/)
- [Neo4j GraphAcademy — Text to Cypher retriever](https://graphacademy.neo4j.com/courses/workshop-genai/3-retrieval/3-text-to-cypher-retriever)
- [Microsoft Research — Introducing DRIFT Search](https://microsoft.com/en-us/research/blog/introducing-drift-search-combining-global-and-local-search-methods-to-improve-quality-and-efficiency)
- 기존 문서: `../graphdb/handoff.md`, `../docs/plans/261106_graph_centric_recommend_rework.md`, `../docs/plans/261106_supplement_reco_graph_rag.md`
