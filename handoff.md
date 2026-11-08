# Handoff — 2026-11-09

> ⚠️ 이 문서는 세션 종료 시점의 해석이다. 사실로 받아들이지 말고 코드와 대조해 검증할 것.
> 프로젝트는 git 저장소가 아니다. 변경 이력은 `.superpowers/sdd/261107_general_graphrag_plan/progress.md`(원장)가 유일한 기록이다.

## Summary

플랜 `261107_general_graphrag_plan.md`의 1단계(Task 0~7)가 끝났다. Task 7 평가 160건(문항 80 × vector·local)을 실행해 게이트 G1(금기 위반 0, 위험 중단 누락 0, 사실·다중홉에서 Local ≥ vector)을 통과했고, `MIN_SCORE`는 분포가 겹쳐 쓰지 않기로 했으며, 평가 중 발견한 검증기 오탐(같은 이름의 Ingredient ID가 여럿일 때 "문맥 밖"으로 거절)을 TDD로 고쳤다(115 passed). 그 뒤 사용자 요청 「결과 없으면 관심사/연령/성별 기준으로 추천」의 설계를 검토해 방향을 합의했고, 샘플 환자별 관심사 추천 결과를 `~/Downloads/관심사_추천_샘플_261108.xlsx`로 만들었다. **대체 추천 코드는 아직 한 줄도 없다.**

## Key Decisions

- `MIN_SCORE = None` 유지 — p1 캐시 재계산 결과 답없음(0.654/0.718/0.82)과 사실(0.649/0.748/0.84)의 max_seed_score 분포가 완전히 겹침. 플랜 Task 7 Step 2 규정대로 `answerable` 플래그만 쓴다. 근거는 `wcrag/config.py` 주석.
- `guard.outside_context`를 이름 단위 판정으로 수정 — '인삼'은 Ingredient ID가 3개(고시형 1 + 개별인정형 2), '홍삼'은 2개. 그중 하나가 문맥에 있으면 문맥 안으로 본다. 정답·채점기는 손대지 않았다(원장 Ruling). 틀리면 같은 이름·다른 ID의 문맥 밖 권유가 통과하지만, 이름이 같으면 금기 규칙도 같아 위험이 낮다고 봤다.
- 수정 뒤 재채점은 저장된 LLM 응답만으로(`api=` 자리에 예외를 던지는 함수 주입, 외부 호출 0) 별도 태그 `p1-guardfix`에 저장 — 원본 `p1`은 그대로 두어 "평가 뒤 정답 수정 금지" 원칙과 재현성을 지켰다. 바뀐 행은 M-10 local, P-14 vector 두 건뿐.
- 대체 추천은 **LLM 없이 규칙으로** — 기존 엔진 `../graphdb/recommend/candidates.py`의 `interest` 생성기와 같은 쿼리(Segment→HAS_INTEREST→Interest→RECOMMENDS→Ingredient, `recommendable`만, `h.order*100+r.order` 정렬)와 같은 금기 필터를 써서 상위 3개. 이유: 결과가 이미 규칙으로 정해지는 일이라 LLM을 거치면 비용·검증 부담만 늘고, 기존 엔진과 결과가 같아야 설명이 쉽다. 재채점도 캐시만으로 돌 수 있다.
- 새 상태값 `fallback`(질문 직접 근거 없음 + 관심사 일반 추천) — `answered`와 섞으면 "질문에 답했다"로 채점되므로 분리. 답없음 유형 채점에서는 거절로 센다는 제안.
- 발동 지점은 `ask.py`의 `no_evidence` 두 곳(검색 문맥 비었을 때, LLM `answerable=False`)만 — `rejected`·`blocked`는 "답이 있었는데 검증에 걸린 것"이라 제외. **이건 내 판단이며 사용자가 아직 확정하지 않았다.**
- 나이·성별은 **필수 입력**(사용자 확인, 2026-11-08) — 대체 추천에 "나이·성별 없을 때" 안내 분기는 넣지 않기로 했다. 단, `ask()`는 지금도 `patient=None`으로 호출될 수 있고(테스트·평가 문항 다수) `legacy.build_context`는 `patient["sex"]`, `float(patient["age"])`를 그대로 읽어 KeyError가 나므로 코드에서는 여전히 방어해야 한다.
- 기각한 대안: 대체 추천 문장을 LLM이 쓰는 방식(위 이유), Text2Cypher로 관심사 추천(2단계 Task 8~9 범위이며 아직 없음), LifeStage 노드 활용(관계가 0건이라 쓸 수 없음).

## Traps to Avoid

- `eval/gold.json`은 `{rule_version, built_at, universes, questions}` dict다. `questions`를 꺼내지 않고 ID로 인덱싱하면 KeyError. 반면 `eval/questions.json`은 list(105건)다.
- 평가 결과 레코드(`out/eval/*.jsonl`)에는 `max_seed_score`가 없다. 분포를 보려면 캐시만으로 `analyze → retrieve`를 재실행해야 한다(스크립트 `seed_scores.py` 참고, 아래 Relevant Files).
- 캐시 전용 재실행은 `llm.embed(texts, api=raise_fn)`·`llm.complete(..., api=raise_fn)`로 한다. `llm.USAGE_LOG`의 `cached` 플래그로 외부 호출 0을 확인할 것. 캐시 키가 프롬프트 전체이므로 analyze/generate 프롬프트를 바꾸면 160건 전부 다시 과금된다.
- 같은 이름의 Ingredient가 여러 ID를 가진다(인삼 3, 홍삼 2). 이름→ID 매핑을 1:1로 가정하는 코드는 오탐을 낸다.
- `LifeStage` 7개는 관계가 0건, `Segment`는 10개(남·여 × 29세↓/30~39/40~49/50~64/65↑, 0~150세 전 구간 커버).
- 관심사 추천은 **개인화가 아니라 구분 기본값**이다. 65세 이상 남성 샘플 5명이 모두 비타민 C·단백질·B1로 수렴했다. 답변 문구에서 이를 개인 맞춤처럼 말하면 안 된다.
- 금기 필터가 실제로 결과를 바꾼다: EPA·DHA는 당뇨치료제·항응고제·혈압강하제 규칙에 차단(P-01·P-11·P-12·P-14·P-15), 비타민 K는 와파린 규칙에 차단(P-07, 원문 consult를 차단으로 상향). 대체 추천에서 금기 판정을 빼면 위반이 바로 생긴다.
- `eval/run.py`의 `score()`는 `status == "no_evidence"`만 거절로 세고(`refuse` 종류), `summarize()`는 상태 목록이 하드코딩돼 있다. 새 상태 `fallback`을 넣으면 두 곳과 `answered` 판정(`citation_valid`, `safety_violation` 계산)에 모두 손대야 한다. 그러지 않으면 fallback 행이 어떤 집계에도 안 잡힌다.
- Claude Docs 갱신: 글자 범위 타깃은 `{"kind":"chars","range":[start,end]}`(start/end 키 아님), 블록 통째 교체·삭제는 `ifHash`+`ifRev` 필요, `read`는 `container`와 payload kind(`view`/`search`) 필수. 문서 현재 rev 23(세션 종료 시점; 바뀌었을 수 있음).
- 메모리 디렉터리(`~/.claude/projects/.../memory/`) 쓰기가 권한 설정으로 거부됐다. 메모리에 저장된 것은 없다.
- `python3 scripts/load_graph.py --reset` 금지. `supplement-neo4j-dev`(7688)는 읽기만, `supplement-neo4j`(7687)는 접속 금지, 쓰기는 `supplement-neo4j-rag`(7689)만.

## Working Agreements

- 모든 답변은 한국어.
- 구현은 TDD: 실패 테스트 작성 → 실패 확인 → 구현 → `.venv/bin/python -m pytest tests -q` 전체 통과. 가상환경은 `graphdb-rag/.venv`만.
- 새 동작(기능)은 설계를 먼저 글로 보여 주고 사용자 승인 뒤 코드를 쓴다(brainstorming 게이트). 사용자는 "어떻게 동작하는지", "LLM이 Cypher를 짜는지" 같은 구조 설명을 원했다 — 변경을 제안할 때 동작 흐름을 함께 설명한다.
- OpenAI 호출이 있는 실행은 `--dry-run`으로 건수·토큰을 먼저 보이고 범위를 말한 뒤 승인받는다. 160건 평가는 AskUserQuestion으로 승인받고 실행했다.
- `../graphdb` 코드·규칙·CSV·테스트는 수정하지 않는다. 읽는 곳은 `wcrag/legacy.py` 한 파일.
- `.env`, `.neo4j-dev.env`, `.neo4j-rag.env` 값은 읽지도 출력하지도 않는다.
- 평가 결과를 본 뒤 정답(`eval/gold.json`)을 고치지 않는다. RC-03 "남편이 어제 쓰러짐"이 두 방식 모두 stopped인데 정답은 no_stop이어도 그대로 뒀다.
- 개발·평가에는 가상 문항만 쓴다. 실제 환자 문장을 넣지 않는다.
- 결과 정리를 엑셀로 원할 때 `~/Downloads/`에 저장한다(openpyxl, `.venv`에 있음).
- 플랜 실행 중 내린 판단은 원장에 `Ruling: 무엇 — 왜 — 틀리면 비용` 형식으로 남기고, 최종 보고에 모은다.
- 작업공간 `.superpowers/sdd/261107_general_graphrag_plan/`은 삭제하지 않기로 했다(git이 없어 원장이 유일한 기록).

## Relevant Files

- `wcrag/answer/ask.py:92-96`, `:113-115` — `no_evidence`가 나는 두 지점. 대체 추천이 들어갈 자리. `:98-110`은 `person` 생성과 금기 판정 흐름(대체 추천도 같은 `person`·`verdicts`를 써야 한다).
- `wcrag/answer/safety_tool.py:26-47` — `merge_patient`(환자 정보 + 질문 단서 합치기), `verdicts`(`legacy.build_context` 결과에 `segment`가 들어 있다).
- `wcrag/legacy.py:30-37` — 기존 엔진 `build_context`·`safety_check` 어댑터(읽기 전용).
- `../graphdb/recommend/candidates.py:29-33`, `:71-74`, `:142-143` — `Q_INTEREST`, `interest` 생성기, GENERATORS 순서(관심사가 마지막). 대체 추천이 따라야 할 원본. **수정 금지.**
- `../graphdb/recommend/normalize.py:42`, `:206-238` — `Q_SEGMENT`(sex·minAge·maxAge), `ctx["segment"]` 생성. `patient["sex"]`·`patient["age"]` 필수.
- `../graphdb/recommend/engine.py:37-59` — `select()`: 금기 판정 → 차단/채택, N개 채우기, 묶음(group) 중복 처리. 대체 추천의 채택 규칙 참고.
- `wcrag/retrieve/local.py:69-99` — 관계 문장 틀(`TEMPLATES`, `HAS_INTEREST`·`RECOMMENDS` 포함)과 `fact_id`. 대체 추천 근거 ID를 같은 체계로 만들 때 쓴다.
- `wcrag/answer/guard.py:72-86` — 이번에 고친 `outside_context`(이름 단위 판정).
- `wcrag/config.py:33-36` — `ENABLED_MODES`, `MIN_SCORE`와 미적용 근거 주석.
- `eval/run.py:84-106`, `:190-205` — `score()`·`summarize()`. `fallback` 상태를 넣을 때 고쳐야 할 곳.
- `eval/questions.json` — 가상 문항 105건(list). `patient`가 있는 14건(P-01~P-15, P-13 제외)이 대체 추천 샘플이다.
- `tests/test_answer.py` — `test_same_name_with_several_ids_is_inside_if_any_id_is_in_context`가 이번 수정의 테스트. 대체 추천 테스트도 여기에 가짜 그래프·가짜 LLM으로 붙이는 게 자연스럽다.
- `.superpowers/sdd/261107_general_graphrag_plan/progress.md` — 원장. 끝부분 "Task 7 Step 1~3"과 Ruling, Deferred minors 10건.
- `261107_general_graphrag_plan.md` — 플랜. 2단계(Task 8~9 Text2Cypher, 게이트 G2)는 미착수.
- `out/eval/p1-guardfix.xlsx` — 현재 기준 평가 결과(sheets rows/summary). `p1.xlsx`는 수정 전 원본.
- `~/Downloads/관심사_추천_샘플_261108.xlsx` — 샘플 24명(평가 환자 14 + 구분별 기본 10)의 관심사 추천·차단 결과, 전체 경로 91건, Segment 10개.
- `/private/tmp/claude-501/-Users-user-Documents-projects-wellcheck-graphdb-rag/af6a2786-330e-4fc0-ac46-0e90652ac140/scratchpad/interest_samples.py` — 위 엑셀을 만든 스크립트(세션 임시 폴더라 사라졌을 수 있음; `fallback()` 함수가 제안한 설계의 프로토타입이다). 같은 폴더 `seed_scores.py`는 MIN_SCORE 분포 재계산 스크립트.
- 가이드 문서(Claude Docs): https://claude.ai/artifact/AZkz2iqkCc85sH7hAFnx2i — 「1단계 측정 결과 (2026-11-07)」 절까지 반영됨. 대체 추천 내용은 없음.

## Open Work

- 「결과 없으면 관심사 기준 대체 추천」은 설계 단계이며 코드가 없다. 합의된 것: LLM 없이 규칙, Segment→Interest→Ingredient, 금기 필터, 상위 3개, 새 상태 `fallback`, 나이·성별 필수. 미확정: `rejected`까지 대체 추천으로 채울지(내 제안은 제외). 사용자는 "이대로 TDD로 들어가도 될까요?"에 답하지 않은 채 핸드오프를 요청했다.
- 대체 추천을 넣으면 `eval/run.py`의 채점·집계가 `fallback` 상태를 모르는 상태다. 답없음 유형 `refused_ok`의 정의(no_evidence만 / fallback 포함 / 고립 개체 인용 answered 포함)는 사용자 결정 사항으로 남아 있다.
- Task 7 뒤 사용자가 정할 것 3가지가 미정이다: ① 답없음 local의 answered 4건(전부 고립 개체 인용 "연결된 근거 없음")을 거절로 셀지(세면 refused_ok 0.5→0.9), ② RC-03 "어제 쓰러짐" 오중단 — 위험 신호 기준을 넓게 둘지, ③ 사실 0.734 / 다중홉 0.606을 다음 단계 기준값으로 쓸지.
- 환자상황 no_evidence 2건(P-03 스타틴 근육통, P-15 아스피린·고혈압 관절통)은 문맥이 있었는데 LLM이 답하지 못한 상태다. 원인은 보지 않았다. 대체 추천이 들어가면 이 둘은 fallback으로 바뀔 것으로 예상된다(샘플 엑셀에 결과 있음).
- 2단계(Task 8~9 Text2Cypher, 게이트 G2)는 미착수이며 별도 지시가 필요하다.
- 가이드 문서에 대체 추천·Text2Cypher 여부 설명이 없다(사용자가 이번에 물어본 내용).
- 원장의 Deferred minors 10건은 그대로 남아 있다(예: `guard.compact` 소문자화 안 함, eval `default_ask()`가 드라이버를 안 닫음, 엑셀 recommended 열이 이름 아닌 ID).

## Prompt for New Chat

```
건기식 GraphRAG 질의응답(wellcheck/graphdb-rag). 플랜 261107_general_graphrag_plan.md의 1단계(Task 0~7)는 끝났고 게이트 G1을 통과했다(평가 결과 out/eval/p1-guardfix.xlsx, 원장 .superpowers/sdd/261107_general_graphrag_plan/progress.md). 모든 답변은 한국어.

현재 상태: 「검색 결과가 없으면 나이·성별(필수 입력) → Segment → 관심사 → 성분으로 대체 추천」을 설계만 합의하고 코드는 없다. 합의안은 handoff.md Key Decisions에 있다(LLM 없이 규칙, 기존 엔진 recommend/candidates.py의 interest 생성기와 같은 쿼리·금기 필터, 상위 3개, 새 status "fallback", 발동은 ask.py의 no_evidence 두 지점). rejected까지 채울지는 미확정. 샘플 결과는 ~/Downloads/관심사_추천_샘플_261108.xlsx.

관련 파일:
- handoff.md
- wcrag/answer/ask.py:92-115
- wcrag/answer/safety_tool.py:26-47
- wcrag/legacy.py:30-37
- ../graphdb/recommend/candidates.py:29-33, 71-74, 142-143 (읽기만, 수정 금지)
- ../graphdb/recommend/normalize.py:42, 206-238 (읽기만)
- ../graphdb/recommend/engine.py:37-59 (읽기만)
- wcrag/retrieve/local.py:69-99
- eval/run.py:84-106, 190-205
- tests/test_answer.py
- .superpowers/sdd/261107_general_graphrag_plan/progress.md (끝부분 Task 7)

제약: ../graphdb 수정 금지, supplement-neo4j-dev(7688)는 읽기만·supplement-neo4j(7687)는 접속 금지·쓰기는 supplement-neo4j-rag(7689)만, .env 류 값 출력 금지, 가상환경은 graphdb-rag/.venv만, OpenAI 호출은 --dry-run 뒤 승인받고 실행, 구현은 실패 테스트 먼저(TDD), 평가 뒤 정답(eval/gold.json) 수정 금지.

나열된 파일을 Read 도구로 직접 읽어서 이 문서의 주장을 코드와 대조해 검증해.
요약본이라고 주장하거나 기억에서 답하지 말고, 실제 파일을 확인한 뒤 내 지시를 기다려.
```
