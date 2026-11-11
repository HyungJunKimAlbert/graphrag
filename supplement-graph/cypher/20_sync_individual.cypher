// 20_sync_individual.cypher
// 개별인정형 원료의 API 주기 갱신 (I-0050 상세, I-0040 인정현황)
// 소유자 규칙과 상태 모델은 docs/SYNC.md 참고
// 각 블록은 "// @query 이름" 으로 시작하고 ; 로 끝난다

// @query upsert_individual
// 주 1회 증분(CHNG_DT) 또는 월 1회 전체 수집 결과를 넣는다
// row.api 에는 API 소유 필드만 담는다: name, recognition_no, intake_min, intake_max, intake_unit,
//   intake_raw, function_text, function_text_en, precaution_text, source
// 동작
//   - 신규: :Individual 라벨, status=active, review_state=unreviewed 로 생성
//   - 이번 수집에 보였으면 last_seen_at 갱신, missing_since 해제 (해시가 같아도)
//   - 해시가 같으면 API 필드는 건드리지 않는다
//   - 기능성 원문이 바뀌면 원료와 기존 기능성 관계에 검수 필요 표시 (관계는 지우지 않는다)
// $rows: [{ingredient_id, source_hash, api: {...}}]
UNWIND $rows AS row
MERGE (i:Ingredient {ingredient_id: row.ingredient_id})
  ON CREATE SET i:Individual, i.status = 'active', i.review_state = 'unreviewed', i.created_at = datetime()
SET i.last_seen_at = datetime(), i.missing_since = null
WITH i, row
WHERE i.source_hash IS NULL OR i.source_hash <> row.source_hash
WITH i, row, (i.function_text IS NOT NULL AND i.function_text <> row.api.function_text) AS fn_changed
SET i += row.api, i.source_hash = row.source_hash, i.synced_at = datetime()
WITH i, fn_changed
WHERE fn_changed
SET i.review_state = 'needs_review'
WITH i
OPTIONAL MATCH (i)-[f:HAS_FUNCTION]->()
SET f.needs_review = true
RETURN count(DISTINCT i) AS flagged;

// @query upsert_company_recognition
// I-0040 은 같은 인정번호가 업체별로 여러 행이다. 원료는 하나, 업체는 관계로 붙인다
// $rows: [{ingredient_id, company_id, company_name, recognized_at}]
UNWIND $rows AS row
MATCH (i:Ingredient:Individual {ingredient_id: row.ingredient_id})
MERGE (c:Company {company_id: row.company_id})
  ON CREATE SET c.name = row.company_name
MERGE (c)-[r:HOLDS_RECOGNITION]->(i)
SET r.recognized_at = row.recognized_at, r.source = 'I-0040', r.synced_at = datetime()
RETURN count(r) AS linked;

// @query mark_missing_individual
// 월 1회 "전체" 수집 직후에만 실행한다. 증분 수집 뒤에 돌리면 안 바뀐 원료가 전부 누락으로 찍힌다
// 삭제하지 않고 표시만 한다. API 반영이 늦는 경우가 있어 사람이 확인한 뒤 revoke 한다
// $run_started_at: 이번 전체 수집 시작 시각 (DB 의 datetime() 기준)
MATCH (i:Ingredient:Individual {status: 'active'})
WHERE i.last_seen_at < $run_started_at AND i.missing_since IS NULL
SET i.missing_since = datetime()
RETURN collect(i.ingredient_id) AS missing;

// @query review_queue
// 사람이 볼 목록: 신규, 원문 변경, 누락
MATCH (i:Ingredient)
WHERE i.review_state IN ['unreviewed', 'needs_review'] OR i.missing_since IS NOT NULL
RETURN i.ingredient_id AS ingredient_id, i.name AS name, i.review_state AS review_state,
       i.missing_since IS NOT NULL AS missing, i.function_text AS function_text
ORDER BY ingredient_id;

// @query mark_reviewed
// 검수 완료. 태그 연결(link_has_function)을 고친 뒤 실행한다
// $ingredient_id
MATCH (i:Ingredient {ingredient_id: $ingredient_id})
SET i.review_state = 'reviewed', i.reviewed_at = datetime()
WITH i
OPTIONAL MATCH (i)-[f:HAS_FUNCTION]->()
SET f.needs_review = false
RETURN i.ingredient_id AS ingredient_id, i.review_state AS review_state;

// @query revoke_individual
// 인정 취소·만료를 사람이 확인한 뒤 실행. 노드와 관계는 남기고 조회에서만 빠진다
// $ingredient_id, $note
MATCH (i:Ingredient:Individual {ingredient_id: $ingredient_id})
SET i.status = 'revoked', i.revoked_at = datetime(), i.revoke_note = $note
RETURN i.ingredient_id AS ingredient_id, i.status AS status;

// @query convert_to_notice
// 개별인정형이 고시형으로 전환됐을 때. 새 고시형 노드를 먼저 넣고(upsert_curated_ingredient) 실행한다
// 이전 노드는 converted 로 바꾸고, 물질 묶음은 새 노드로 이어 준다
// $old_id, $new_id, $converted_at ('YYYY-MM-DD')
MATCH (old:Ingredient:Individual {ingredient_id: $old_id})
MATCH (new:Ingredient:Notice {ingredient_id: $new_id})
MERGE (old)-[r:CONVERTED_TO]->(new)
SET r.converted_at = date($converted_at), old.status = 'converted'
WITH old, new
OPTIONAL MATCH (old)-[:FORM_OF]->(s:Substance)
FOREACH (_ IN CASE WHEN s IS NULL THEN [] ELSE [1] END | MERGE (new)-[:FORM_OF]->(s))
RETURN old.status AS old_status, new.ingredient_id AS new_id;
