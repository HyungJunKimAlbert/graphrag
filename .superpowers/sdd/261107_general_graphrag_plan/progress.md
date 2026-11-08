# SDD ledger — plan: /Users/user/Documents/projects/wellcheck/graphdb-rag/261107_general_graphrag_plan.md

Spec: 플랜 문서 1~5절(별도 설계 문서 없음).
Setup: Ruling: git 저장소가 아니라 task-start/task-done 스크립트가 동작하지 않는다 — 플랜 Global Constraints대로 커밋 없이 진행하고 원장을 손으로 쓴다 — 틀리면: 커밋 범위 기반 리뷰 패키지를 못 만든다(파일 목록으로 대체).
Setup: Ruling: "먼저 정할 것" 1~7은 사용자가 "플랜 구현" 지시로 추천안을 수용한 것으로 본다 — 틀리면: 용도·DB 배치·외부 전송 범위를 다시 정해야 한다.
Setup: Ruling: 실행 범위는 Phase 0~1(Task 0~6). Task 7은 사용자 실행, Phase 2 이후는 게이트 G1 뒤 — 플랜 5절 — 틀리면: 뒤 단계 구현이 한 번 더 지시를 기다린다.
Pre-flight: Task1 clone ↔ Task3 entities — clone이 지우는 파생 데이터를 entities.build가 캐시 파일로 복원해야 함(일치). Task4 Context ↔ Task5 generate/guard ↔ Task2 score(context_keys) — Context에 개체 키가 있어야 retrieval_hit 계산 가능(일치: seeds.key, facts.start/end, docs.id). Task5 FORBIDDEN 임시 상수 ↔ Task6 legacy.FORBIDDEN — 이름 동일(일치). Task6 legacy 어댑터 ↔ recommend 쿼리 — recommend는 HTTP 클라이언트의 db.run(cypher, **params)->list[list]를 쓴다. Graph.read가 같은 모양을 돌려준다(일치).
Task 0: Ruling: Local 검색은 neo4j-graphrag HybridCypherRetriever 대신 Cypher 직접 구현 — 벡터1+전문1 인덱스와 naive/linear 결합만 지원해 Task 4의 3목록 RRF(k=60)와 안 맞음(플랜 4.5가 허용) — 틀리면: 라이브러리 검색기를 다시 붙이는 작업.
Task 0: Step 1~5 완료. tests: .venv/bin/python -m pytest tests -q → 4 passed. Step 6(gpt-6-luna 호환 호출)은 승인 대기.
Task 1: complete (커밋 없음, tests: .venv/bin/python -m pytest tests -q → 11 passed). 복제 실행: 노드 2,920 · 관계 5,085 · 약 8초. cli status 구현.
Task 2: Ruling: 플랜 예시 다중 홉 "메트포르민 고갈 ∩ 임산부 금기"는 DB에서 0건이라 문항에서 빼고 "임산부 권장"(엽산)으로 바꿈 — Step 1의 "실제로 있는 관계만" 규칙 — 틀리면: 문항 1개 교체.
Task 2: Ruling: 집합 정답 채점은 성분=recommended∪cautioned ID, 문서=인용한 문서 ID, 그 외 라벨=답변 문장 속 이름 찾기(긴 이름 우선) — 플랜은 "답변 집합 F1"만 정함 — 틀리면: score()의 predicted_keys 수정.
Task 2: Ruling: 환자 문항 must_not은 엔진 금기 판정으로 뽑는다. Task 6 전이라 gold.json에 pending으로 두고 Task 6 뒤 build_gold 재실행 — 평가 전이므로 "결과 본 뒤 정답 수정 금지"와 충돌 없음 — 틀리면: 금기 위반 수가 0으로만 나옴.
Task 2: Ruling: eval/run.py에 --dry-run 추가(호출 건수·토큰 상한 출력) — Global Constraints의 dry-run 선행 규칙 — 틀리면: 옵션 하나 삭제.
Task 2: complete (커밋 없음, tests: .venv/bin/python -m pytest tests -q → 28 passed). 105문항, gold.json 규칙 1.1.0 (set 49, count 6, rubric 25, refuse 10, stop 10, no_stop 5).
Setup: Ruling: 승인이 필요한 단계(Task 0 Step 6, Task 3 Step 3, 그 임베딩이 있어야 도는 Task 4)를 뒤로 미루고 Task 3 코드 → Task 5 → Task 6을 먼저 한다 — 승인을 기다리는 동안 멈추지 않기 위해 — 틀리면: Task 4 구현 때 Task 5 연결부를 다시 확인해야 함.
Task 3: Ruling: 개체 노드에 rag_key("<라벨>:<고유값>") 속성과 rag_entity_key 인덱스를 추가 — 검색 결과에서 키를 바로 돌려주기 위해(플랜은 키 형식만 정함) — 틀리면: 속성 하나 삭제.
Task 3: Step 1~2 완료(코드). tests: 40 passed, 6 failed — 실패 6건은 모두 실제 임베딩 실행 뒤를 전제로 한 DB 상태 테스트. dry-run 실측: 고유 문장 1,186건, 약 72,730토큰. Step 3(임베딩 실행)은 승인 대기.
Task 5: Ruling: Context에 ingredient_names({성분 ID: 이름})를 추가 — 답변 문장 속 성분 이름을 찾아 "분류 안 된 성분"을 가리고, LLM에 ID=이름 표를 주려면 필요(플랜 Context에는 없는 키) — 틀리면: Task 4 검색기가 이 키를 안 채우면 guard가 이름 검사를 못 함(Task 4에서 반드시 채움).
Task 5: Ruling: ask()에 retrievers 인자(검색기 주입)를 추가 — 테스트가 DB·임베딩 없이 돌게 하려고. 기본값은 wcrag.retrieve.local/vector_only — 틀리면: 인자 하나 삭제.
Task 5: Ruling: 사실도 문서도 없는 문맥(고립 시드만 있는 경우 포함)은 LLM을 부르지 않고 no_evidence로 끝낸다 — 근거가 0건이면 생성할 것이 없고, 고립 노드를 "효과 없음"으로 답하는 실패(Review Focus)를 코드로 막는다 — 틀리면: 분기 하나 삭제.
Task 5: Ruling: cli에 render()와 ask_fn 주입을 둠 — 출력 형식을 OpenAI 없이 테스트하려고 — 틀리면: 없음.
Task 5: complete (커밋 없음, tests: .venv/bin/python -m pytest tests/test_answer.py -q → 22 passed). config.MIN_SCORE = None(측정 전에는 쓰지 않음), ENABLED_MODES = ["local"].
Task 6: Ruling: test_verdicts_equal_engine은 엔진의 HTTP 클라이언트(recommend.db.connect) 대신 엔진 함수(normalize.build_context, safety.check)를 dev Bolt 읽기 세션에 직접 돌려 비교 — recommend.db.connect는 환경변수 NEO4J_HTTP_URL을 먼저 보므로 운영 DB(7474)에 붙을 위험이 있다 — 틀리면: HTTP 경로와 Bolt 경로의 결과 차이를 못 잡음(같은 Cypher, 같은 DB라 가능성 낮음).
Task 6: Ruling: legacy에 incomplete_reasons(ctx)를 추가해 엔진의 INCOMPLETE 문구를 그대로 씀. "complaint" 항목은 빼고, 대신 환자 주호소 문장을 질문 분석(위험 신호 판단)에 함께 넣음 — 엔진에서 그 항목은 "위험 신호를 못 봤다"는 뜻인데 여기서는 analyze가 본다 — 틀리면: 환자 파일에 주호소가 있을 때마다 '판정 불완전'이 붙는 쪽으로 되돌림.
Task 6: Ruling: 임신·수유 여부 미입력(pregnancy_unknown)을 incomplete에 넣음 — 엔진은 경고만 붙이고 불완전으로 치지 않지만, 여기서는 "판정이 닿지 못한 입력"을 한 목록으로 보여 줌 — 틀리면: 가임기 여성 환자 답변에 불완전 문구가 엔진보다 자주 붙음.
Task 6: Ruling: 권한 성분에 '주의' 판정이 있으면 그 문구를 warnings에 붙임(엔진의 D3: caution은 포함하고 문구를 붙인다) — 플랜은 avoid 처리만 적음 — 틀리면: 경고 줄이 늘어남.
Task 6: Ruling: 넘겨받은 patient가 있으면 질문에서 뽑은 PatientHint보다 우선 — 플랜에 우선순위가 없음 — 틀리면: 질문에만 적힌 약이 판정에서 빠짐(환자 파일에 없을 때).
Task 6: Ruling: sys.path에 ../graphdb를 맨 뒤에 붙임(append) — 앞에 넣으면 같은 이름 폴더(tests, eval)가 이 프로젝트 모듈을 가릴 수 있음 — 틀리면: 없음.
Task 6: complete (커밋 없음, tests: .venv/bin/python -m pytest tests -q → 78 passed, 6 failed — 실패 6건은 Task 3 Step 3(임베딩 실행, 승인 대기) 전제 테스트). eval/build_gold.py 재실행: 환자 문항 14건 must_not frozen(P-01 67건, P-02 176건, P-03 1건, P-04 177건 …).
중단(2026-11-07, 사용자 지시 "여기까지만, 남은 건 내일"): 여기서 멈춤. 다음에 이어서 할 것 —
  1) OpenAI 승인 받기: Task 0 Step 6(gpt-6-luna 호환 확인 1~2건), Task 3 Step 3(개체 임베딩 고유 문장 1,186건·약 72,730토큰), Task 4 테스트용 질문 임베딩과 문서 예시용 가상 질문 실행 몇 건.
  2) 승인 뒤: python -m wcrag.index.entities → tests/test_entities.py 나머지 6건 확인 → Task 4(tests/test_local.py → wcrag/retrieve/local.py, vector_only.py; Context에 ingredient_names 채울 것).
  3) 문서(https://claude.ai/artifact/AZkz2iqkCc85sH7hAFnx2i, doc 4d75277b-d5b5-42c8-9a99-4c5ffb987aa7, 본문 노드 b3d241dc-68fe, rev 4): "한눈에 보기", "GraphRAG란 무엇인가", "기존 추천 엔진과 무엇이 다른가"만 채움. 남은 pending 블록 m4aqg8bfwsd.34(전체 흐름·그림) .35(색인) .36(질의) .37(실제 예시) .38(안전 장치) .39(평가 방법) .40(구축 단계) .41(결과 확인) .42(구현 현황) .43(용어 사전).
  4) 마무리: 새 컨텍스트 리뷰 1회 → Critical/Important 수정 → Rulings·Deferred minors 보고. Task 7(평가 실행)은 사용자 실행.
Task 0: Step 6 완료(2026-11-08, 호출 2건). wcrag.llm.complete와 neo4j_graphrag OpenAILLM 모두 gpt-6-luna 호출 성공. Task 0: complete.
Task 3: Step 3 완료(2026-11-08, 승인 후). 임베딩 1,186문장 호출·저장(out/embeddings.jsonl), RagEntity 1,220/1,220 임베딩, 인덱스 3개 ONLINE, 임베딩 없는 Doc 0건.
Task 3: Ruling: tests/test_clone.py::test_doc_embeddings_copied를 "복제본 ⊇ 개발 DB"(doc_id 부분집합 + 표본 벡터 동일)로 바꿈 — Task 3이 빠진 Doc 임베딩 1건을 채우면 건수 일치 가정이 깨짐(Task 3 테스트 test_doc_without_embedding_is_filled와 충돌) — 틀리면: 복제본에 임베딩이 더 있는 상태를 못 잡음(의도된 상태).
Task 3: complete (커밋 없음, tests: .venv/bin/python -m pytest tests -q → 84 passed).
Task 4: Ruling: seeds[].score와 max_seed_score는 순위 역수 합이 아니라 질문과 개체 설명의 코사인 유사도 — 순위 역수 합은 순위만 반영해 어떤 질문이든 비슷한 값이 나와 MIN_SCORE(답 없음 문턱값)의 재료가 될 수 없다. 시드 순서는 여전히 순위 역수 합 순 — 틀리면: Task 7의 분포표가 유사도 기준으로 나옴(의도).
Task 4: Ruling: 관계 유형당 상한(5건)은 이웃 확장과 시드 잇기를 합친 뒤에 시드별로 다시 적용 — 시드 잇기가 허브 관계(임산부–수유부 EXCLUDES)로 문맥을 다시 채워 test_hub_relation_capped가 8건으로 실패 — 틀리면: 시드를 잇는 사실이 상한에 밀릴 수 있음(잇는 사실을 먼저 넣어 완화).
Task 4: Ruling: 시드 찾기 앞에 "질문에 글자 그대로 나온 개체 이름"과 "질문 분석(LLM)이 뽑은 언급(Route.mentions)"을 우선 시드로 둠. retrieve(graph, question, embed, mentions=None) — 플랜의 순위 역수 합만으로는 긴 환자 질문(P-01 예시)에서 와파린·메트포르민·근육경련이 시드가 되지 못해 no_evidence가 나옴(실측) — 틀리면: 언급 추출이 틀리면 엉뚱한 시드가 앞자리를 차지함(평가에서 확인).
Task 4: Ruling: 전문검색 질의에서 가운뎃점·쉼표·마침표·물음표를 공백으로 바꿈 — "와파린·메트포르민"이 한 낱말로 분석돼 약 이름이 검색되지 않음(실측) — 틀리면: 없음.
Task 4: Ruling: 고립 시드는 자기 키(예: Symptom:SYM-...)로 인용할 수 있고, 연결된 시드의 키는 근거가 아님 — "연결된 근거가 없다"는 문장이 인용할 ID가 없어 실제 실행에서 rejected가 됨(실측) — 틀리면: 고립 개체 키를 다른 주장에 인용해도 guard가 못 잡음(지시문으로만 제한).
Task 4: Ruling: 사실 문장에 evidence='hypothesis'인 관계는 "[근거 수준: 가설, 검토 전]" 꼬리표를 붙임 — 검토 전 규칙을 확정 사실처럼 쓰지 않게 — 틀리면: 꼬리표 제거.
Task 4: Ruling: Result에 ingredient_names(권함·주의 성분의 이름)를 추가하고 CLI에 [권함]/[주의] 줄을 보여 줌 — 약사가 ID 대신 이름을 봐야 함 — 틀리면: 키 하나 삭제.
Task 4: complete (커밋 없음, tests: .venv/bin/python -m pytest tests -q → 102 passed). 테스트용 질문 임베딩 7건+언급 3건 저장. 실제 ask 4건 실행(answered 2, answered-고립 1, stopped 1), 결과는 examples/ 없음·스크래치패드 ask_runs2.txt.
Task 4: Ruling: 약 표기(DrugItem) 시드는 IN_CLASS로 이어진 약 계열(Drug)로 올림 — 같은 이름의 표기·계열이 시드 자리를 두세 개 차지했고 표기 노드의 사실은 IN_CLASS뿐(실측) — 틀리면: 표기 고유 정보(없음)를 잃음.
Task 7 준비: Ruling: eval dry-run 상한 문구를 문항당 6,500 → TOKENS_PER_RUN=10,500으로 — 실측(P-01 Local: 분석 ≈1,000 + 생성 ≈9,500)이 6,500을 넘어 사용자가 비용을 과소평가할 수 있었음(test_dry_run_estimate_covers_measured_input RED→GREEN) — 틀리면 비용 상한을 과대 또는 과소 안내.
Task 4 추가 Ruling: 문서 예시용으로 P-01(Q2)을 DrugItem 올리기 이후 한 번 재실행(answered, 입력 9,048·출력 488토큰) — 저장된 생성 응답이 바뀐 문맥과 맞지 않아 문서에 섞인 실행값을 쓰지 않기 위함 — 비용은 LLM 1회.
문서: 모든 pending 블록 채움(rev 17). 그림 2개(전체 흐름 92587273-9448, Local 검색 a0cee7cb-63ba). 문서 예시용 ask 재실행 2건(P-01, 메트포르민 고갈) — 시드 정리 이후 문맥 기준. OpenAI 호출 누계: 호환 2, 임베딩 1,197문장, analyze 8, generate 8.
Final review: 새 컨텍스트 리뷰어(fable, higher) 1회 dispatch — 패키지 review-package.md, review-focus.md, 원장 Ruling 전달.
Final: 리뷰 결과 Critical 1, Important 6, Minor 13. 효과 기준 재등급: 모두 그대로(약사가 받는 결과에 직접 닿음).
Final: fixed vector 모드 TypeError(vector_only.retrieve에 mentions 인자 없음) — test_vector_retriever_accepts_mentions, test_ask_vector_mode_runs_with_default_retriever RED→GREEN, suite 114/114
Final: fixed count 채점이 인용 ID 속 숫자를 셈 — test_count_gold_ignores_digits_in_citation_ids RED→GREEN(has_number가 [..] 괄호를 지움), suite 114/114
Final: fixed 문장 없는 권유·문장에 안 나온 분류가 answered — test_recommendation_without_any_claim_is_rejected, test_recommended_ingredient_must_be_named_in_a_claim RED→GREEN(guard: claims 비면 거절, unmentioned_ingredients), suite 114/114
Final: fixed 고립 키만 인용한 문장이 "효과 없음/안전"이라 써도 통과 — test_isolated_citation_cannot_claim_no_effect_or_safety RED→GREEN(ISOLATED_OK/ISOLATED_NOT), suite 114/114
Final: fixed 문맥 밖 성분을 이름으로 권해도 통과 — test_ingredient_outside_context_named_in_text_is_rejected, test_ask_rejects_ingredient_outside_context_using_graph_names RED→GREEN(guard.check(name_index=), ask.graph_names), suite 114/114
Final: fixed 금기 성분을 글로 권하고 cautioned로 분류하면 통과 — test_forbidden_ingredient_written_favorably_is_blocked_even_if_cautioned RED→GREEN(blocked: 금기 성분이 나온 문장에 피하라는 말 없이 권하는 말만 있으면 blocked), suite 114/114
Final: fixed 넘겨받은 환자가 있으면 질문 속 약·질환이 판정에서 빠짐 — test_given_patient_identity_wins_but_question_drugs_are_added, test_merge_patient_keeps_identity_and_unions_drugs_and_conditions RED→GREEN(safety_tool.merge_patient: 신원은 파일, 약·질환은 합집합; patient={}도 단서 사용), suite 114/114
Final: Ruling: 문맥 밖 성분 검사는 그래프 이름·별칭 사전에 글자 그대로 있는 이름만 잡는다 — 자유 표기("오메가3")는 못 잡음, 사전을 넓히는 것은 Phase 3(추출 층) 몫 — 틀리면: 별칭 없는 표기로 쓴 문맥 밖 권유가 통과.
Final: Ruling: eval score()의 safety_violation은 recommended ID 기준 그대로 둠 — 문장 수준 거부권이 생성 단계에서 blocked로 바꾸므로 answered 결과에는 위반이 남지 않고, 시도는 blocked 건수로 보인다 — 틀리면: 채점기가 blocked 건수를 위반으로 집계하지 않아 G1 표에서 따로 읽어야 함.
Final: Ruling: 리뷰어 Declined 항목 — MIN_SCORE 미적용·"DB에 없는 대상" 코드 강제 없음(Task 7 뒤), Graph.read 행 수 제한 없음(Task 8), complaint가 analyze로 전송됨(플랜 결정 4, 평가는 가상 환자), TOKEN_BUDGET은 본문만 셈(TOKENS_PER_RUN에 반영), 이웃 선택이 코사인 기준(평가로 판단), 캐시 적중도 사용량 합산(보고 방식), clone이 rag DB 전부 지움(플랜 4.2), universe 이름 충돌 덮어씀(중복 0건) — 모두 그대로 둠, 플랜이 정한 범위 — 틀리면: 각각 Phase 2~3 태스크에서 다시 봄.
Final: Ruling: 작업공간 .superpowers/sdd/261107_general_graphrag_plan/ 은 지우지 않음 — git이 없어 원장이 유일한 결정 기록이고 사용자가 세션을 나눠 이어감 — 틀리면: 스크래치 폴더 하나가 남음.
Final: minor (deferred): 문서만 있는 성분 3개가 isolated=True로 표시되고 인용 문구가 "문서가 없음"이라 거짓(local.py isolated 정의 vs ISOLATED_EVIDENCE 문구)
Final: minor (deferred): 결정성 — Q_PAIR_PATHS collect(p)[0..cap]에 ORDER BY 없음, fulltext·doc 동점 tiebreak 없음(실측 3회 동일)
Final: minor (deferred): guard.compact가 소문자화를 안 해 "코엔자임q10" 표기는 분류 검사가 못 찾음
Final: minor (deferred): entities.unique()가 strip한 문장으로 키를 잡고 vectors[text]는 원문으로 찾음 — 앞뒤 공백 Doc이 생기면 KeyError(현재 0건)
Final: minor (deferred): 평가 실행 시 gold.rule_version과 RagMeta.source_rule_version을 비교하지 않음(재복제 뒤 낡은 정답으로 채점 가능; 테스트 test_gold_frozen_with_rule_version이 잡음)
Final: minor (deferred): dry-run 문구가 임베딩 호출(질문 1+언급 n)을 말하지 않음; 글자 그대로 찾히는 구절도 먼저 임베딩함(캐시)
Final: minor (deferred): eval default_ask()가 드라이버를 닫지 않음
Final: minor (deferred): citation_valid는 answered면 항상 True(guard가 이미 거름) — "rejected 아님"과 같은 값
Final: minor (deferred): 빈 문맥 메시지가 질문과 무관한 고립 시드까지 나열함
Final: minor (deferred): caution finding text가 None이면 "None"이 찍힘; 엑셀 recommended/cautioned 열이 이름 아닌 ID
Final: minor (deferred): 섭취량 "250㎎" vs 답변 "250 mg"은 거절(NFC가 ㎎→mg를 안 바꿈; 안전한 방향)
Final: minor (deferred): Lucene AND/OR/NOT 낱말 미처리; Doc 라벨 predicted_keys에 고립 시드 키가 Doc:Symptom:…로 섞임

Task 7 Step 1 (2026-11-07, 사용자 승인 "전체 160건 실행" 뒤 내가 실행): `eval/run.py --run --tag p1 --modes vector,local --types 사실,다중홉,답없음,환자상황,위험,위험대조` → 160건, error 0, 12분, 입력 689,108·출력 47,974 토큰(상한 1,680,000의 41%). 결과 out/eval/p1.jsonl·p1.xlsx.
Task 7 Step 2: max_seed_score 분포(캐시 재계산, 외부 호출 0) — 사실 min 0.649/med 0.748/max 0.84, 다중홉 0.672/0.752/0.832, 답없음 0.654/0.718/0.82, 환자상황 0.656/0.716/0.758. 완전히 겹침 → 플랜 규정대로 MIN_SCORE 미적용, answerable 플래그만 사용. config.py 주석에 기록.
Task 7: Ruling: 평가 뒤 guard.outside_context 오탐 수정 — 같은 이름의 Ingredient가 여럿(인삼 3개, 홍삼 2개)일 때 하나가 문맥에 있어도 "문맥 밖"으로 거절해 정답(M-10 인삼)을 숨김. 정답·채점기는 안 고치고 검증기 버그만 고침(test_same_name_with_several_ids_is_inside_if_any_id_is_in_context RED→GREEN, suite 115/115). 재채점은 캐시만으로 p1-guardfix 태그에 별도 저장(캐시 미스 0, error 0 — 160건 문맥이 결정적으로 재현됨). 바뀐 행: M-10 local rejected→answered, P-14 vector rejected→answered(홍삼 권함, 메트포르민 '주의' 경고 동반, must_not 겹침 없음) — 틀리면: 같은 이름 다른 ID의 문맥 밖 권유가 통과(이름이 같으면 금기 판정도 같은 규칙이라 위험 낮음).
Task 7 Step 3: G1 — 금기 위반 0/0, 위험 중단 누락 0/10, 사실 Local 0.919/0.734 vs vector 0.192/0.05, 다중홉 Local 0.752/0.606 vs 0.398/0.1 → 통과. 사용자와 정할 것: 답없음 local refused_ok 0.5(answered 4건은 전부 고립 개체 인용으로 "연결된 근거 없음"이라 답함 — 채점 정의 문제), 위험대조 RC-03 "어제 쓰러짐" 오중단 1/5, 환자상황 local no_evidence 2(P-03 스타틴, P-15 아스피린).

2026-11-09 핸드오프: handoff.md 작성. 플랜 밖 요청 「결과 없으면 관심사·연령·성별 기준 대체 추천」은 설계 합의 단계(코드 없음, 상세는 handoff.md). 샘플 결과 ~/Downloads/관심사_추천_샘플_261108.xlsx.

2026-11-09 대체 추천(플랜 밖, 사용자 요청 「결과 없으면 관심사/연령/성별 기준 추천」): 사용자 결정 — 발동은 no_evidence 두 지점만(rejected·blocked 제외), 답없음 채점에서 fallback은 거절로 셈.
Fallback: 구현 wcrag/answer/fallback.py(recommend), ask.py no_evidence() 내부 함수, legacy.interest_candidates, safety_tool.incomplete(분리), eval/run.py(refused_ok·safety_violation·summarize), cli STATUS_LABELS. tests/test_fallback.py 10건 + test_eval.py 4건 RED→GREEN, suite 129/129. 80세 남성 픽스처 → '남성 65세 이상' 비타민 C·단백질·비타민 B1, EPA·DHA 금기로 뺌(샘플 엑셀과 일치). 외부 호출 0.
Fallback: Ruling: 관심사 쿼리를 복사하지 않고 엔진 candidates.interest를 legacy로 그대로 호출 — legacy.py의 "엔진 코드는 복사하지 않는다" 원칙, 엔진이 바뀌면 같이 바뀜(test_equals_engine_on_dev) — 틀리면: 엔진 함수 시그니처가 바뀌면 여기서 깨짐(테스트가 잡음).
Fallback: Ruling: 같은 성분이 관심사 둘에 있으면 앞 순위로 한 번만, 근거(paths)를 합침 — 엔진 select의 seen 처리와 같음. 묶음(group) 구성 성분 중복(overlaps)은 관심사 후보에 members가 없어 다루지 않음 — 틀리면: 묶음과 구성 성분이 함께 나올 수 있음(현재 관심사 경로에 group 없음은 미확인).
Fallback: Ruling: 후보가 전부 금기거나 나이·성별·구분이 없으면 기존 no_evidence 그대로(경고 없음) — 권할 것이 없으면 fallback 상태를 낼 이유가 없음 — 틀리면: 전부 금기인 환자에게 "왜 일반 추천도 없는지"가 안 보임.
Fallback: Ruling: 근거 ID는 fact_id가 아니라 "Ingredient:<id>", 문장은 "'구분' 구분의 관심사 경로: 관심사 → 성분 (이유)" — 엔진 후보에 관심사 코드가 없어 fact_id를 만들 수 없음. claims는 비움(LLM 문장 없음) — 틀리면: 평가 context_keys·인용 체계와 따로 놂(fallback은 채점에서 답변으로 안 셈).
Fallback: Ruling: 문구는 "근거 없음"을 먼저, "질문에 대한 답이나 개인 맞춤 추천이 아니라 같은 성별·연령대의 일반 추천"을 명시, "많이 찾는" 대신 "주요 관심사"(그래프 HAS_INTEREST 문장 틀과 같은 말) — 구분 기본값이라 개인화처럼 말하면 안 됨(핸드오프 함정) — 틀리면: 문구 수정만.
Fallback: Ruling: person(merge_patient) 계산을 검색 앞으로 옮김 — 첫 no_evidence 지점에서도 나이·성별이 필요. 순수 함수라 기존 동작 변화 없음 — 틀리면: 없음.
Fallback: 남은 일 — 평가 재채점(p1 저장 응답만으로 fallback 반영, 외부 호출 0 예상)은 아직 안 함. 가이드 문서에 대체 추천 설명 없음.
