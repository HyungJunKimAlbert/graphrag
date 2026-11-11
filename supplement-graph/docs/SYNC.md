# 개별인정형 원료 주기 갱신 설계

개별인정형 원료는 식약처 API로 계속 바뀐다. 이 문서는 바뀌는 데이터를 그래프에 반영하면서 사람이 검수한 연결을 잃지 않는 방법을 설명한다.

- 실행할 쿼리: [`../cypher/20_sync_individual.cypher`](../cypher/20_sync_individual.cypher)
- 동작 검증: [`../test/run_tests.py`](../test/run_tests.py)의 "동기화", "누락", "취소", "고시형 전환" 항목

## 1. 문제

원료 데이터의 출처는 두 가지다.

| 출처 | 바뀌는 방식 |
|---|---|
| 영양성분(고시 원문), 고시형(I2710) | 거의 고정. 고시 개정 때만 바뀜 |
| **개별인정형(I-0050, I-0040)** | **새 인정, 문구 수정, 취소, 고시형 전환이 수시로 생김** |

반면 원료에 붙는 연결 중 일부는 사람이 만든다. 기능성 태그 연결, 같은 물질 묶음, 추천·제외 관계가 그렇다. API를 반영할 때 이 연결까지 지우거나 덮어쓰면 검수 작업이 매번 날아간다.

## 2. 해결: 소유자를 나눈다

그래프는 하나만 둔다. 대신 노드 속성과 관계마다 **누가 소유하는지** 정한다. 동기화 작업은 API가 소유한 것만 바꾼다.

| 소유자 | 대상 | 동기화가 할 수 있는 일 |
|---|---|---|
| **API** | `:Ingredient:Individual` 노드의 API 필드<br>`(Company)-[:HOLDS_RECOGNITION]->(Ingredient)` | 생성, 갱신, 누락 표시 |
| **사람** | `HAS_FUNCTION`(기능성 태그), `FORM_OF`(물질 묶음), `RECOMMENDS`, `EXCLUDES`, `INTERACTS_WITH` | **건드리지 않는다.** `needs_review = true` 표시만 가능 |
| **상태 필드** | `status`, `review_state`, `missing_since`, `last_seen_at` | 아래 상태 모델 규칙대로만 바꾼다 |

API 필드 목록은 아래와 같다. 동기화는 `SET i += row.api`로 이 필드만 덮어쓴다. 노드 전체를 교체하는 `SET i = {...}`는 쓰지 않는다.

| 그래프 속성 | I-0050 응답 필드 | 비고 |
|---|---|---|
| `recognition_no` | `HF_FNCLTY_MTRAL_RCOGN_NO` | 정규화 필요 (5장) |
| `name` / `name_raw` | `RAWMTRL_NM` | 원문 끝의 `(…제2013-5호)` 꼬리표를 뗀 이름과 원문 |
| `intake_max` / `intake_min` | `DAY_INTK_HIGHLIMIT` / `DAY_INTK_LOWLIMIT` | `"500 (mg/일)"` 형태라 숫자만 파싱 |
| `intake_unit` | `WT_UNIT` | |
| `intake_raw` | (위 원문) | 파싱 전 문자열 보관 |
| `function_text` / `function_text_en` | `PRIMARY_FNCLTY` | 국문·영문이 한 칸에 섞여 와서 분리 |
| `precaution_text` | `IFTKN_ATNT_MATR_CN` | |
| `source` | — | 항상 `'I-0050'` |

필드 대응은 샘플 응답과 엑셀 시트를 대조해 확인한 것이다. 실제 인증키로 받은 응답에서는 아직 확인하지 않았다.

## 3. 상태 모델

| 필드 | 값 | 누가 바꾸나 |
|---|---|---|
| `status` | `active` → `revoked`(취소) 또는 `converted`(고시형 전환) | `active`는 생성 시. 나머지는 사람이 확인한 뒤 |
| `review_state` | `unreviewed` → `reviewed`, 원문이 바뀌면 `needs_review` | 생성·원문 변경 시 동기화, 검수 완료 시 사람 |
| `last_seen_at` | 마지막으로 API 응답에 나온 시각 | 동기화 (해시가 같아도 갱신) |
| `missing_since` | 전체 수집에서 빠진 시각, 다시 보이면 `null` | 동기화 |
| `source_hash` | API 원본 행의 해시 | 동기화 |

조회 쿼리의 규칙은 두 가지다.

- **원료는 항상 `status = 'active'`만 쓴다.** `revoked`와 `converted`는 노드와 관계를 남겨 두고 조회에서만 뺀다.
- **`needs_review = true`인 기능성 관계는 추천 경로에서 뺀다.** 원문이 바뀌었으니 예전 태그가 여전히 맞는지 사람이 볼 때까지 추천에 쓰지 않는다. 안전 관계(`EXCLUDES`, `INTERACTS_WITH`)는 검수 상태와 상관없이 항상 적용한다.

신규 원료는 따로 막지 않아도 검수 전까지 추천에 나오지 않는다. 증상에서 원료로 가는 경로가 `HAS_FUNCTION`을 거치는데, 신규 원료에는 이 관계가 아직 없다. 이름 검색에는 나온다.

## 4. 실행 흐름

두 가지 작업을 돌린다.

| 작업 | 주기 | 하는 일 |
|---|---|---|
| 증분 수집 | 주 1회 | `CHNG_DT`가 지난 실행 이후인 행만 받아 `upsert_individual` |
| 전체 수집 | 월 1회 | 전체를 받아 `upsert_individual` 후 `mark_missing_individual` |

**누락 감지는 전체 수집 뒤에만 한다.** 증분 수집은 바뀐 행만 돌려주므로, 그 뒤에 누락 감지를 돌리면 바뀌지 않은 원료가 전부 누락으로 찍힌다.

```
1. API 호출 (06~19시 호출 제한이 있어 새벽 배치, 호출 간 지연)
2. 원본 저장: 응답 행마다 raw_json + hash + fetched_at (RDB, 스냅샷 보관)
3. 정규화: 인정번호 → ingredient_id, 섭취량 파싱, 국문·영문 분리
4. 그래프 반영
   - upsert_individual            (I-0050 → 원료 노드)
   - upsert_company_recognition   (I-0040 → 업체 관계)
   - mark_missing_individual      (전체 수집일 때만)
5. 후처리: 바뀐 원료만 임베딩 재계산 (set_ingredient_embedding), 검수 큐 갱신 (review_queue)
6. sync_log 기록: 조회·신규·변경·검수필요·누락 건수. 건수가 급감하면 알림
```

`upsert_individual`이 한 행에 하는 일은 다음과 같다.

| 경우 | 결과 |
|---|---|
| 처음 보는 인정번호 | `:Ingredient:Individual` 생성, `active`, `unreviewed` |
| 이번 수집에 나옴 | `last_seen_at` 갱신, `missing_since` 해제 (해시가 같아도) |
| 해시 동일 | API 필드는 그대로 둔다 |
| 해시 다름 | API 필드만 갱신 |
| 해시가 다르고 `function_text`도 다름 | 위에 더해 원료 `review_state = 'needs_review'`, 기존 `HAS_FUNCTION`에 `needs_review = true` |

사람이 하는 작업은 다음과 같다.

| 상황 | 할 일 | 쿼리 |
|---|---|---|
| 신규 또는 원문 변경 | 기능성 태그 연결을 만들거나 고친다 | `link_has_function` → `mark_reviewed` |
| 같은 물질이 이미 있음 | 물질 묶음에 연결 | `link_form_of` |
| 누락 표시 | 식약처에서 취소·만료인지 확인 | 취소면 `revoke_individual`. 아니면 다음 수집에서 자동 해제 |
| 고시형으로 전환 | 고시형 노드를 넣고 전환 처리 | `upsert_curated_ingredient` → `convert_to_notice` |

## 5. 함정

- **API에서 빠졌다고 바로 지우지 않는다.** 엑셀 작업 때 최근 인정 80건을 크롤링해 비교해 보니 40건이 API 시트에 없었다. 반영이 늦을 뿐인 경우가 있으므로 표시만 하고 사람이 확인한다.
- **인정번호 표기가 제각각이다.** `제2025-63호(2025.11.17.)`, `New제2007-13호`, `2023-40`이 모두 나온다. `제`, `호`, 괄호 안 날짜를 떼서 `IND-2025-63`처럼 만든다. `New` 접두어는 별도 번호 체계일 수 있어 `IND-NEW-2007-13`처럼 구분해 둔다. 같은 번호 체계인지는 아직 확인하지 않았다.
- **I-0040은 같은 인정번호가 업체별로 중복된다.** 원료 노드는 하나만 만들고 업체는 `HOLDS_RECOGNITION` 관계로 붙인다. 1일 섭취량이 "…로서"에서 잘린 행이 많아 섭취량은 I-0050 값을 쓴다.
- **I-0050은 원료명이나 번호로 검색할 수 없다.** 문서상 변경일자(`CHNG_DT`) 조회만 지원한다. 증분 수집이 실제로 되는지는 인증키로 호출해서 확인해야 한다.
- **같은 원료가 인정번호 여러 개로 존재한다.** 예: 이소말토올리고당 제2005-10호, 제2005-11호. 업체별로 따로 인정받기 때문이다. 각각 노드로 두고 `Substance`로 묶는다.
- **이력은 그래프에 쌓지 않는다.** 그래프는 현재 상태만 담는다. 과거 값은 2단계의 원본 스냅샷에 있다. 그래프가 깨지면 원본에서 다시 만든다.
- **시트용 정리 쿼리(`cleanup_sheet_edges`)를 API 원천에 쓰지 않는다.** 증분 수집과 섞이면 멀쩡한 관계가 지워진다.
