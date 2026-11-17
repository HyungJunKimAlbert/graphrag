# Handoff — 2026-11-09 (5차)

> ⚠️ 이 문서는 세션 종료 시점의 해석이다. 사실로 받아들이지 말고 코드·파일과 대조해 검증할 것.
> 이전 핸드오프: `handoff_2026-11-09_4차.md`(근거 엑셀 v05, 기전 층), `handoff_2026-11-09_3차.md`(태스크 정의 U1~U11, DB·포트 규칙). 거기서 뒤집지 않은 결정·제약은 유효하다고 본다.

## Summary

승인된 14태스크 플랜(`docs/superpowers/plans/2026-11-09-patient-graphrag-recommend.md`)을 브랜치 `feature/rxrag`에서 끝까지 실행했다. 새 패키지 `rxrag/`(환자 JSON → 그래프 후보·근거 사실 ID → 규칙 금지 판정 → LLM 3개 선택 → 코드 검증)와 평가 도구 `eval/`이 생겼고 테스트 118개가 통과한다. 평가는 Claude 세션(s1)과 운영 모델 GPT-6-Luna(g3 → 프롬프트 수정 후 g3b)로 돌렸으며, g3b는 30명 중 LLM 선택 29명 전원이 1차 시도에 검증 통과, G1(금지·근거 없음·후보 밖) 위반 0건이다. 결과 엑셀은 `wellcheck/data/`, `~/Downloads/`, `wellcheck/result/`에 `rxrag_recommend_gpt6luna_g3b.xlsx`로 저장했다.

## Key Decisions

- **플랜 그대로 실행, 기전 층(증상→기전→성분, 4차 엑셀)은 범위 밖** — 사용자가 "플랜 그대로 실행"을 지시했고 플랜에 기전 층 태스크가 없다. 그래프 7689 적재도 하지 않았다.
- **같은 작업 트리에 브랜치 `feature/rxrag`, 경로 지정 커밋** — 미추적 `data/`·`.venv`·env 파일을 그대로 써야 해서 worktree 대신 선택. main 작업 트리의 이전 `wcrag` 삭제분(staged `D` 31개)은 커밋하지 않고 남겨 뒀다.
- **must_not(금지 정답)은 후보 풀이 아니라 추천 가능한 전체 성분에 금지 판정해 계산**(`eval/build_must_not.py`) — 풀은 LLM 정규화에 따라 바뀌지만 금지는 환자 조건만으로 정해진다. G1이 더 엄격해진다.
- **G2 블라인드 채점 문구에서 새 시스템 이유 문장·엔진 단계명 제거** — 둘 다 어느 쪽인지 드러냈다. 대신 이유 문장 품질은 G2에서 평가되지 않는다.
- **최종 리뷰 Important 2건 수정** — ① 규칙 대체 시 사실 문장의 "예방" 등 금지 표현을 reason으로 내보내던 것 → 엔진 `FALLBACK` 문구로 대체, ② 서로 겹치는 후보뿐이라 3개 못 채워도 `ok`이던 것 → 겹치지 않게 고를 수 있는 수로 `partial` 판정.
- **선택 프롬프트 수정(g3 실패 원인 대응)** — 후보 표 `[fact_id] (한글 종류) 문장`으로 경계 명시, 스키마 `fact_ids`를 풀의 실제 fact_id enum으로 제한, 금지 단어 나열 + "근거 문장에 있어도 옮겨 쓰지 말 것". 결과: 규칙 대체 4건 → 0건. 대가: s1(Claude)과 프롬프트가 달라져 같은 조건 비교가 아니다.
- **결과 표시 한글화** — ID는 JSON에 추적용으로 남기고, 사실마다 `label`(한글 종류), 검증 오류 문구·`trace.selection`에 성분 이름, 엑셀 「선택 과정」 시트 추가. 사용자 요청("NTC 이런건 사람이 보기 힘든데 한글로").
- **G3(GPT 과금)는 사용자 승인 후 실행** — "gpt-6-Luna 기준으로 뽑아보자"를 승인으로 받았다. 호출: 스모크 2 + g3 30명 + 실패 4명 재실행 + g3b 30명.
- **Claude Docs 가이드 문서 반영은 보류** — 공유 문서이고 기전 층도 없어 사용자 확인 후 한 번에 갱신하기로 했다(사용자 미확인).
- 기각: 손으로 쓴 must_not(마그네슘을 잘못 넣어 G1이 오탐 — 계산기로 대체), 엔진 경로를 `sys.path` 앞에 넣기(아래 Traps).

## Traps to Avoid

- **엔진 경로를 `sys.path` 앞에 넣으면 `graphdb/tests`가 우리 `tests`를 가린다** — `rxrag/legacy.py`는 `append`로 붙인다. `tests/test_graph.py::test_legacy_path_does_not_shadow_our_packages`가 막는다.
- **후보 표에서 `fact_id: 문장` 형식은 GPT가 `SYM_DIRECT:` 접두어를 잘라 읽는다** — fact_id 안에도 콜론이 있어서. 지금은 `[ ]`로 감싸고 스키마 enum으로 막았다. 형식을 되돌리지 말 것.
- **근거 문장 원문(그래프 데이터)에 "예방", "개선" 같은 건기식 금지 표현이 있다** — 관심사·증상 사실 116개 중 39개(리뷰어 측정). 금지 표현 검사는 LLM `reason`과 규칙 대체 reason에만 적용되고 `evidence[].text` 원문은 그대로 나간다.
- **G2는 Claude가 Claude 선택을 채점한 것** — 블라인드여도 같은 계열 선호 가능성. s1 승+무 93.1%는 이 점을 감안해야 한다. g3b(GPT)는 아직 G2 채점을 안 했다.
- **세션 응답 파일 `out/session/`은 gitignore** — 정규화 492·select 29·judge 29 응답이 로컬에만 있다. 지우면 s1 재현 불가. 또 프롬프트를 바꿔서 기존 select 응답 키(프롬프트 해시)는 이제 맞지 않는다.
- **정규화 필수 증상 충족 71.3%는 우리 채점 규칙 기준** — 정답은 엔진 증상 마스터 이름인데 우리 출력은 분류표 키워드+자유 텍스트라 "포함되면 맞힘"으로 판정했다(`eval/normalize_eval.py:_found`). 엔진 채점기와 수치가 다르다.
- **macOS에 `timeout` 명령이 없다** — 셸에서 쓰지 말 것.
- **그래프 데이터 공백**: 빌베리-와파린 상호작용이 그래프에 없어 금지 판정이 안 된다(s1 V-13 블라인드 채점에서 지적). 코드 문제 아님.
- 3차 핸드오프의 함정(7474/7687 접속 금지, `load_graph.py --reset` 금지, 엔진 `db.connect()`의 7474 기본값)은 유효. `rxrag/graph.py`는 포트 7475가 아니면 거부한다.

## Working Agreements

- 모든 답변 한국어. 결론·근거를 표로 짧게. 갈림길은 추천안을 붙여 묻는다.
- 사람이 보는 결과에는 ID(NTC-2-16 등) 대신 한글 이름·설명을 쓴다(이번 세션 사용자 요청).
- 결과 엑셀은 **`wellcheck/result/`**에 저장(2026-11-09 사용자 지시로 이전의 data/·Downloads 두 곳 규칙을 대체). 메모리 디렉터리는 권한상 쓰기가 막혀 이 규칙이 메모리에 저장되지 않았다.
- API 과금(OpenAI)은 사용자 승인 후에만. 개발·평가 LLM 판단은 세션·서브에이전트로(과금 0).
- `../graphdb` 수정 금지(import·읽기만). dev DB(http 7475) 읽기만, 7474/7687 접속 금지, 쓰기는 7689만. `.env`·`.neo4j-*.env` 값 출력 금지(코드가 읽는 것은 허용). 가상환경 `graphdb-rag/.venv`.
- 커밋 메시지 "영어 동사: 한글 요약" + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. 푸시는 사용자가 `! git push`로 직접.
- 새 기능·수정은 TDD(실패 테스트 확인 → 구현 → 전체 통과).
- 사용자는 "지금 건 누가(어떤 모델이) 한 건가"를 확인한다 — 결과마다 어떤 모델이 답했는지 밝힐 것.

## Relevant Files

- `rxrag/recommend.py:26-80` — 진입점 흐름. `named`(trace용 이름 붙이기), `choose`(LLM 선택→검증→1회 재시도→규칙 대체, 시도별 `trace.selection`), partial 판정(겹치지 않는 수 기준).
- `rxrag/candidates.py:9-16,74-160` — 사실 종류 `KINDS`·한글 `KIND_LABELS`, 엔진과 같은 조건의 8개 경로 쿼리, 묶음 접기(`fold_groups`: 데이터 신호 사실만 묶음으로), `rule_rank` 정렬.
- `rxrag/select.py:8-70` — 선택 시스템 프롬프트(금지 단어 목록 포함), 스키마(ingredient_id·fact_ids enum), 후보 표 형식 `[fact_id] (종류) 문장`.
- `rxrag/verify.py:18-85` — 검증 규칙(후보 밖·금지·중복 이름·묶음+구성·근거 소속·금지 표현), `fallback`, `safe_reason`.
- `rxrag/safety.py:10-29` — 금지 판정 분리(엔진 `safety.check` 재사용), 상한은 판정 뒤에 자름.
- `rxrag/graph.py:11-50`, `rxrag/legacy.py` — 읽기 전용 래퍼(쓰기 쿼리 거부·포트 7475 강제), 엔진 어댑터(sys.path append).
- `eval/harness.py` — export/run/g1/engine/g2-export/g2/xlsx. 엑셀 시트 요약·불일치·전체·선택 과정.
- `eval/patients.json`, `eval/build_must_not.py` — 가상 환자 30명과 금지 정답(커밋 후 수정 금지).
- `out/eval/s1*.{jsonl,json,xlsx}`, `normalize-s1.*`, `g3.jsonl`, `g3-fail4.jsonl`, `g3b.{jsonl,xlsx}` — 평가 결과(커밋됨).
- `.superpowers/sdd/2026-11-09-patient-graphrag-recommend/progress.md` — 실행 ledger. `Ruling:` 27개, 미룬 minor 5개(gitignore, 로컬에만).

## Open Work

- 브랜치 `feature/rxrag`(커밋 20개)는 푸시·병합되지 않았다. 병합/PR/유지 결정이 나지 않았다. main 작업 트리의 staged `wcrag` 삭제분과 미추적 `data/`·`docs/`·`handoff*.md`도 미커밋이다(플랜·설계 문서 `docs/superpowers/`도 미커밋).
- g3b(GPT, 수정 프롬프트) 결과는 엔진과 G2 블라인드 채점을 하지 않았다. s1 G2는 승13·무14·패2(93.1%), 확실히 못함 0.
- GPT(g3, 수정 전 프롬프트)와 Claude(s1)의 비교: 1순위 일치 21/29, 집합 일치 6/29, 정규화 결과가 다른 환자 8명. g3b와 s1 비교는 계산하지 않았다.
- 기전 층(증상→기전→성분)은 그래프·코드 어디에도 없다. 후보 경로에 넣으려면 `rxrag/candidates.py`에 새 종류와 쿼리가 필요하고, 7689 재구성은 설계 C5와 충돌한 채 남아 있다.
- 미룬 minor(리뷰): `verify.check`가 dict 아닌 항목·reason 누락에서 예외, 이름 중복 판정이 공백 제거뿐(…분말/영문 병기 차이 못 잡음), 쓰기 거부 정규식이 `CALL db.createLabel` 류 프로시저 쓰기를 못 막음, `contract.validate`가 `symptoms`·`conditions` 문자열·`complaint` 비문자열을 거르지 않음, 분류표 경로가 사용자 Documents 절대경로.
- 결과 엑셀에서 같은 B군 개별 성분 3개(B1·B2·나이아신)를 고르는 환자가 여러 명 있다(검증은 묶음+구성만 막고 형제 구성 성분끼리는 허용). 의도된 동작인지 판단되지 않았다.
- Claude Docs 가이드 문서(https://claude.ai/artifact/2QzTGSk1rDPaXPSDcfLumg)에 구현·평가 결과가 반영되지 않았다.
- 메모리 디렉터리의 `MEMORY.md`가 가리키는 두 파일은 여전히 없다(4차 기준, 이번 세션에서 확인 안 함).

## Prompt for New Chat

```
건기식 GraphRAG(wellcheck/graphdb-rag). 태스크: 질의 없이 BE 환자 JSON만으로 영양성분 3개를 우선순위대로 추천하고 그 과정에 GraphRAG를 쓴다. 모든 답변은 한국어.

현재 상태: 14태스크 플랜을 브랜치 feature/rxrag에서 다 실행했다(커밋 20개, 푸시 안 함, 테스트 118개 통과). 패키지 rxrag/(입력 검증 → 주호소 LLM 정규화 → 그래프 후보+근거 사실 ID → 엔진 금지 판정 → LLM 3개 선택 → 코드 검증·재시도·규칙 대체)와 eval/ 하네스가 있다. 운영 모델 GPT-6-Luna로 수정 프롬프트 재평가(g3b): 29명 전원 1차 검증 통과, G1 위반 0. 결과 엑셀 out/eval/g3b.xlsx(사본 wellcheck/result/rxrag_recommend_gpt6luna_g3b.xlsx). 남은 갈림길: 브랜치 병합/PR, g3b G2 블라인드 채점, 기전 층 추가, 미룬 minor 5개.

관련 파일:
- handoff.md (이번), handoff_2026-11-09_4차.md, handoff_2026-11-09_3차.md
- rxrag/recommend.py:26-80, rxrag/candidates.py:9-16,74-160, rxrag/select.py:8-70, rxrag/verify.py:18-85, rxrag/safety.py, rxrag/graph.py, rxrag/legacy.py
- eval/harness.py, eval/patients.json, eval/build_must_not.py
- out/eval/g3b.jsonl, out/eval/s1-g2.json
- .superpowers/sdd/2026-11-09-patient-graphrag-recommend/progress.md (Ruling 목록)
- docs/superpowers/plans/2026-11-09-patient-graphrag-recommend.md, docs/superpowers/specs/2026-11-09-patient-graphrag-recommend-design.md

제약: OpenAI 과금은 승인 후만, ../graphdb 수정 금지, dev DB(7475) 읽기만·7474/7687 접속 금지·쓰기는 7689만, .env 값 출력 금지, .venv만 사용, 결과 엑셀은 wellcheck/result에 저장, 사람이 보는 결과는 ID 대신 한글, 푸시는 사용자가 직접.

나열된 파일을 Read 도구로 직접 읽어서 이 문서의 주장을 코드와 대조해 검증해.
요약본이라고 주장하거나 기억에서 답하지 말고, 실제 파일을 확인한 뒤 내 지시를 기다려.
```
