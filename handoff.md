# Handoff — 2026-11-09 (6차)

> ⚠️ 이 문서는 세션 종료 시점의 해석이다. 사실로 받아들이지 말고 코드·파일과 대조해 검증할 것.
> 이전 핸드오프: `handoff_2026-11-09_5차.md`(추천 시간·토큰, 세부사항, 샘플 입력), `_4차.md`(근거 엑셀·기전 층), `_3차.md`(태스크 정의 U1~U11, DB·포트 규칙). 거기서 뒤집지 않은 결정·제약은 유효하다고 본다.

## Summary
주호소 정규화를 GPT-6-Luna(low·medium)로 300건 샘플 형식 테스트셋에 돌려 Claude(세션 응답)와 비교했고, 위험 신호(red flag) 규칙을 분류별 응급 목록으로 고쳐 GPT low의 위험 신호 재현율을 76.7% → 100%로 올렸다. Claude Haiku API 클라이언트(`AnthropicLLM`)와 `--llm anthropic` 옵션을 TDD로 추가했다(테스트 141개 통과). 이 변경들은 **아직 커밋되지 않았다.**

## Key Decisions
- 위험 신호 규칙을 심장·뇌·의식/정신·호흡·출혈·그 밖의 분류별 응급 목록으로 교체 — 옛 짧은 목록에서 GPT low가 위험 30건 중 7건을 놓쳤다(안전 문제). 수정 후 샘플 300건 기준 TP 30/FN 0/FP 0(`sf2-gpt`), 홀드아웃 45건 기준 TP 30/FN 0/FP 1(`rf-gpt-after`).
- GPT effort는 low 유지 — medium은 필수 충족률 +0.7%p, 위험 신호 +1건 정도의 이득에 비해 지연이 약 19% 늘고 추론 토큰이 2배이며, 분류표 밖 증상을 관절 통증으로 억지로 넣는 경향을 보였다.
- 동일 조건 비교는 "1건당 API 1회 호출, `--workers 8` 병렬" — 이전 Claude 결과(`sf1`)는 서브에이전트 3개가 100건씩 일괄 답한 것이라 지연 시간을 GPT와 비교할 수 없고, 옛 프롬프트 기준이다. 그래서 Haiku API로 다시 돌리기로 했다.
- `AnthropicLLM` 기본값은 `claude-haiku-5-5`, effort `low` — 사용자가 Haiku를 지정했고, GPT low와 맞추려고 effort를 명시했다(Haiku 5.5 기본값은 medium).
- Claude 프롬프트 캐싱은 system 블록에 `cache_control: ephemeral`을 명시 — OpenAI는 자동 접두 캐싱(입력의 약 98%가 캐시)이지만 Anthropic은 명시해야 한다. Haiku 5.5 최소 캐시 길이는 512토큰이고 분류표가 든 system 프롬프트는 그보다 길다.
- Claude 토큰 기록에서 `input`은 캐시 읽기·쓰기를 포함한 입력 합계, `cached`는 캐시 읽기, `cache_write`는 따로 기록 — OpenAI의 `input`(캐시 포함 합계)과 같은 의미로 맞춰 비교하기 위해서다.
- `stop_reason`이 `max_tokens`/`refusal`이면 RuntimeError — 잘린 JSON을 조용히 파싱하거나 대체하지 않는다는 프로젝트 원칙.
- 키는 `wellcheck/.env`(= `config.OPENAI_ENV`)의 `ANTHROPIC_API_KEY`에서 읽는다 — 별도 env 파일을 만들지 않았다.

## Traps to Avoid
- 위험 신호 결과를 일반화된 성능으로 보면 안 됨 — 홀드아웃의 주제가 새 응급 목록과 겹치고, 샘플의 위험 신호 주제도 옛 위험 그룹에서 나왔다. 목록 밖 응급 상황으로 만든 새 홀드아웃이 아직 없다.
- 엔진에는 키워드 기반 위험 신호 규칙이 없다 — `build_context`는 LLM의 `red_flag`만 쓴다. 즉 LLM이 놓치면 걸러지지 않는다.
- `sf1`(Claude)과 GPT 결과의 지연 시간 비교 — 실행 방식이 달라 무의미하다(Key Decisions 참고).
- pytest 요약 줄이 안 보임 — `pytest.ini` addopts의 `-q`와 겹친다. `.venv/bin/python -m pytest -o addopts="" -q -p no:cacheprovider`로 실행.
- Pyright가 `rxrag` import 미해결, `output_config`/`reasoning` dict 타입 불일치를 보고함 — 런타임과 무관한 오탐이다. SDK의 `OutputConfigParam`은 `{effort, format:{type:"json_schema", schema}}`로 확인했다.
- `.env` 값 출력 금지 — 코드가 읽는 것은 괜찮지만 cat/echo로 보지 말 것.

## Working Agreements
- 모든 답변은 한국어. 사람이 보는 출력은 ID가 아니라 한국어 이름.
- 과금 API 실행은 사용자 승인 하에, 이번 Haiku 실행은 **사용자가 직접 돌린다**고 했다.
- 새 기능·수정은 TDD. 결과마다 어떤 모델로 만들었는지 밝힌다.
- 결과 엑셀은 `wellcheck/result/`에 저장.
- `../graphdb` 수정 금지(import/read만). dev DB(7475) 읽기 전용, 7474/7687 접속 금지, 쓰기는 7689만.
- `graphdb-rag/.venv`만 사용.
- 커밋은 사용자가 요청할 때만. 형식 "English verb: 한글 요약" + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. 푸시는 safe-push 스킬로.

## Relevant Files
- `rxrag/llm.py:L72-L160` — `OpenAILLM`, 새 `AnthropicLLM`(L105~), `anthropic_usage_of`(L142~). 캐시·구조화 출력·토큰 기록 방식.
- `rxrag/llm.py:L163-L180` — `Meter`. 호출마다 지연과 토큰(`cache_write` 포함)을 남긴다.
- `rxrag/normalize.py:L8-L26` — 정규화 SYSTEM 프롬프트와 새 응급 신호 목록.
- `eval/normalize_eval.py:L87-L160` — `score`(병렬), `usage_summary`(`cache_write_tokens` 추가), CLI `--llm session|openai|anthropic`, `--effort`, `--workers`.
- `tests/test_llm.py` 끝부분 — Anthropic 테스트 3개(차단, 캐시·스키마·토큰, 잘림·거절).
- `tests/conftest.py:L12-L26` — 테스트 중 OpenAI·Anthropic 클라이언트 생성 차단.
- `eval/complaint_sample.json` — 300건 샘플 형식 테스트셋(정상 270, 위험 30).
- `eval/complaint_redflag_holdout.json` — 위험 신호 홀드아웃 45건(위험 30, 위험대조 15). 미커밋.
- `out/eval/normalize-{sf1,sf1-gpt,sf1-gpt-med,sf2-gpt,rf-gpt-before,rf-gpt-after}.json` — 비교 결과. `sf1`만 커밋됨.
- `/private/tmp/claude-501/-Users-user-Documents-projects-wellcheck-graphdb-rag/4ebcec4b-0c7f-4c38-877a-9818784f9134/scratchpad/sf_compare.py` — 모델 비교 엑셀 생성 스크립트(TAGS에 열 추가 방식). 세션 임시 폴더라 사라졌을 수 있다.
- `/Users/user/Documents/projects/wellcheck/result/rxrag_normalize_sample_format_claude_vs_gpt.xlsx` — 현재 모델 비교 엑셀(4개 모델 열 + 홀드아웃 시트).
- 가이드 문서: https://claude.ai/artifact/2QzTGSk1rDPaXPSDcfLumg — 진행 상황에 Haiku·코드 안전망 항목이 미체크로 있다.

## Open Work
- Haiku API 실행은 아직 안 됐다. `.env`에 `ANTHROPIC_API_KEY`가 들어가면 사용자가 직접 실행한다:
  - `.venv/bin/python -m eval.normalize_eval score --tag sf2-haiku --llm anthropic --testset eval/complaint_sample.json`
  - `.venv/bin/python -m eval.normalize_eval score --tag rf-haiku --llm anthropic --testset eval/complaint_redflag_holdout.json`
- 실제 API로 `AnthropicLLM`을 호출해 본 적은 없다(목 객체 테스트만). 첫 실행에서 스키마 제약(예: `enum`, `additionalProperties`)이나 effort 파라미터가 거부될 가능성이 남아 있다. 캐시 적중은 결과 json의 `usage.cached_tokens`/`cache_write_tokens`로 확인 가능하다.
- Haiku 결과가 나오면 모델 비교 표·엑셀·가이드 문서에 Haiku 열을 추가하는 작업이 남아 있다.
- 미커밋 변경: 정규화 프롬프트 수정, `--llm/--workers/--effort` 옵션, thread-local usage, `AnthropicLLM`, `requirements.txt`의 `anthropic==2.6.0`, GPT 결과 파일들, 홀드아웃 세트, 핸드오프 파일 정리(`handoff.md` → `handoff_2026-11-09_5차.md` 이름 변경 후 새 `handoff.md`).
- 제안만 되고 확정되지 않은 것: 위험 신호 코드 키워드 안전망, 목록 밖 응급 상황 신규 홀드아웃, 수정 프롬프트로 Claude 재응답, PR/병합 결정.

## Prompt for New Chat
```
건강기능식품 GraphRAG(rxrag, /Users/user/Documents/projects/wellcheck/graphdb-rag, 브랜치 feature/rxrag).
환자 JSON → 주호소 정규화(LLM) → 그래프 후보 → 금지 검사 → LLM 선택 → 성분 3개 추천.

현재 상태: 주호소 정규화를 GPT-6-Luna로 300건 평가했고 위험 신호 프롬프트를 고쳤다(미커밋).
Claude Haiku API 클라이언트(AnthropicLLM, --llm anthropic)를 추가했지만 실제 API로는 아직 안 돌렸다.
사용자가 .env에 ANTHROPIC_API_KEY를 넣고 직접 sf2-haiku / rf-haiku를 실행할 예정이다.
결과가 나오면 모델 비교 엑셀·가이드 문서에 Haiku 열을 추가하는 단계다.

관련 파일:
- handoff.md (이 문서), handoff_2026-11-09_5차.md
- rxrag/llm.py:L72-L180
- rxrag/normalize.py:L8-L26
- eval/normalize_eval.py:L87-L160
- tests/test_llm.py, tests/conftest.py
- eval/complaint_sample.json, eval/complaint_redflag_holdout.json
- out/eval/normalize-*.json

나열된 파일을 Read 도구로 직접 읽어서 이 문서의 주장을 코드와 대조해 검증해.
요약본이라고 주장하거나 기억에서 답하지 말고, 실제 파일을 확인한 뒤 내 지시를 기다려.
```
