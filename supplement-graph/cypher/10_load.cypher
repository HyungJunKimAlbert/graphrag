// 10_load.cypher
// 시트·고시형 API(사람 또는 전체 교체형 원천)를 그래프에 넣는 템플릿
// 서버가 $rows 를 배치로 넘겨 실행한다. 각 블록은 "// @query 이름" 으로 시작하고 ; 로 끝난다
// 개별인정형(API 주기 갱신)은 20_sync_individual.cypher 를 쓴다

// @query upsert_curated_ingredient
// 영양성분(2-1 시트)·고시형(I2710) 원료. row.kind = 'Nutrient' | 'Notice'
// $rows: [{ingredient_id, kind, source_hash, props: {name, intake_min, intake_max, intake_unit,
//          intake_raw, function_text, precaution_text, source}}]
UNWIND $rows AS row
MERGE (i:Ingredient {ingredient_id: row.ingredient_id})
  ON CREATE SET i.status = 'active', i.review_state = 'unreviewed', i.created_at = datetime()
FOREACH (_ IN CASE WHEN row.kind = 'Nutrient' THEN [1] ELSE [] END | SET i:Nutrient)
FOREACH (_ IN CASE WHEN row.kind = 'Notice'   THEN [1] ELSE [] END | SET i:Notice)
WITH i, row
WHERE i.source_hash IS NULL OR i.source_hash <> row.source_hash
SET i += row.props, i.source_hash = row.source_hash, i.synced_at = datetime()
RETURN count(i) AS updated;

// @query upsert_symptom
// $rows: [{symptom_id, props: {name, category, aliases}}]
UNWIND $rows AS row
MERGE (s:Symptom {symptom_id: row.symptom_id})
SET s += row.props, s.synced_at = datetime()
RETURN count(s) AS upserted;

// @query upsert_function_tag
// $rows: [{tag_id, props: {name, phrases, source}}]
UNWIND $rows AS row
MERGE (t:FunctionTag {tag_id: row.tag_id})
SET t += row.props, t.synced_at = datetime()
RETURN count(t) AS upserted;

// @query upsert_mechanism
// $rows: [{mechanism_id, props: {name, category, handling, source}}]
UNWIND $rows AS row
MERGE (m:Mechanism {mechanism_id: row.mechanism_id})
SET m += row.props, m.synced_at = datetime()
RETURN count(m) AS upserted;

// @query upsert_condition
// $rows: [{code, props: {kind, label, rule_group}}]
UNWIND $rows AS row
MERGE (c:Condition {code: row.code})
SET c += row.props, c.synced_at = datetime()
RETURN count(c) AS upserted;

// @query upsert_drug
// 성분이면 class_id 로 계열에 연결한다. 계열 노드를 먼저 넣어야 한다
// $rows: [{drug_id, class_id, source, props: {name, level, atc}}]
UNWIND $rows AS row
MERGE (d:Drug {drug_id: row.drug_id})
SET d += row.props, d.synced_at = datetime()
WITH d, row
WHERE row.class_id IS NOT NULL
MATCH (c:Drug {drug_id: row.class_id})
MERGE (d)-[r:IN_CLASS]->(c)
SET r.source = row.source, r.synced_at = datetime()
RETURN count(r) AS linked;

// @query set_ingredient_embedding
// $rows: [{ingredient_id, embedding: [float, ...]}]  차원은 01_schema 의 벡터 인덱스와 같아야 한다
UNWIND $rows AS row
MATCH (i:Ingredient {ingredient_id: row.ingredient_id})
SET i.embedding = row.embedding
RETURN count(i) AS updated;

// @query link_symptom_chain
// 증상 → 기전 → 기능성 (①증상기준 시트, 사람이 관리)
// $rows: [{symptom_id, mechanism_id, tag_id, source, evidence}]
UNWIND $rows AS row
MATCH (s:Symptom {symptom_id: row.symptom_id})
MATCH (m:Mechanism {mechanism_id: row.mechanism_id})
MATCH (t:FunctionTag {tag_id: row.tag_id})
MERGE (s)-[r1:EXPLAINED_BY]->(m)
SET r1.source = row.source, r1.evidence = row.evidence, r1.synced_at = datetime()
MERGE (m)-[r2:SUPPORTED_BY]->(t)
SET r2.source = row.source, r2.evidence = row.evidence, r2.synced_at = datetime()
RETURN count(*) AS linked;

// @query link_symptom_mechanism
// 증상 → 기전. 관계마다 근거(출처 기관·문서·URL·원문)를 따로 남길 때 쓴다
// $rows: [{symptom_id, mechanism_id, source, evidence, props: {verdict, source_orgs, docs, urls, quote}}]
UNWIND $rows AS row
MATCH (s:Symptom {symptom_id: row.symptom_id})
MATCH (m:Mechanism {mechanism_id: row.mechanism_id})
MERGE (s)-[r:EXPLAINED_BY]->(m)
SET r += coalesce(row.props, {}), r.source = row.source, r.evidence = row.evidence, r.synced_at = datetime()
RETURN count(r) AS linked;

// @query link_mechanism_tag
// 기전 → 기능성 태그
// $rows: [{mechanism_id, tag_id, source, evidence, props: {verdict, source_orgs, docs, urls, quote}}]
UNWIND $rows AS row
MATCH (m:Mechanism {mechanism_id: row.mechanism_id})
MATCH (t:FunctionTag {tag_id: row.tag_id})
MERGE (m)-[r:SUPPORTED_BY]->(t)
SET r += coalesce(row.props, {}), r.source = row.source, r.evidence = row.evidence, r.synced_at = datetime()
RETURN count(r) AS linked;

// @query link_mechanism_replenished
// 결핍 기전 → 그 영양소 원료. 기능성 태그를 거치지 않는 직접 경로다 (예: 비타민D 결핍 → 비타민 D)
// $rows: [{mechanism_id, ingredient_id, source, evidence, props: {verdict, source_orgs, docs, urls, quote}}]
UNWIND $rows AS row
MATCH (m:Mechanism {mechanism_id: row.mechanism_id})
MATCH (i:Ingredient {ingredient_id: row.ingredient_id})
MERGE (m)-[r:REPLENISHED_BY]->(i)
SET r += coalesce(row.props, {}), r.source = row.source, r.evidence = row.evidence, r.synced_at = datetime()
RETURN count(r) AS linked;

// @query link_has_function
// 원료 → 기능성 태그. 개별인정형은 사람이 검수한 뒤에만 넣는다(SYNC.md)
// $rows: [{ingredient_id, tag_id, text, source, evidence, props: {verdict, docs} (선택)}]
UNWIND $rows AS row
MATCH (i:Ingredient {ingredient_id: row.ingredient_id})
MATCH (t:FunctionTag {tag_id: row.tag_id})
MERGE (i)-[r:HAS_FUNCTION]->(t)
SET r += coalesce(row.props, {}), r.text = row.text, r.source = row.source, r.evidence = row.evidence,
    r.needs_review = false, r.synced_at = datetime()
RETURN count(r) AS linked;

// @query link_form_of
// 원료 → 물질 묶음
// $rows: [{ingredient_id, substance_id, substance_name, aliases}]
UNWIND $rows AS row
MATCH (i:Ingredient {ingredient_id: row.ingredient_id})
MERGE (s:Substance {substance_id: row.substance_id})
  ON CREATE SET s.name = row.substance_name, s.aliases = row.aliases
MERGE (i)-[r:FORM_OF]->(s)
SET r.source = 'review', r.synced_at = datetime()
RETURN count(r) AS linked;

// @query link_drug_depletes
// 약물(성분 또는 계열) → 고갈 영양소. 근거는 식약처 허가사항 문장이다 (load/refs/drug_nutrient_kr.json)
// $rows: [{drug_id, ingredient_id, source, props: {grade, effect, quote, product, item_seq, url, note, checked_at} (선택)}]
UNWIND $rows AS row
MATCH (d:Drug {drug_id: row.drug_id})
MATCH (i:Ingredient {ingredient_id: row.ingredient_id})
MERGE (d)-[r:DEPLETES]->(i)
SET r += coalesce(row.props, {}), r.source = row.source, r.evidence = 'confirmed', r.synced_at = datetime()
RETURN count(r) AS linked;

// @query link_interacts_with
// 원료 → 약물 계열. 원료 주의사항 문구("항응고제", "혈당강하제" 등)를 파싱해 사람이 확인한 뒤 넣는다
// $rows: [{ingredient_id, drug_id, severity, note, source, props: {evidence, derived} (선택)}]
UNWIND $rows AS row
MATCH (i:Ingredient {ingredient_id: row.ingredient_id})
MATCH (d:Drug {drug_id: row.drug_id})
MERGE (i)-[r:INTERACTS_WITH]->(d)
SET r += coalesce(row.props, {}), r.severity = row.severity, r.note = row.note, r.source = row.source, r.synced_at = datetime()
RETURN count(r) AS linked;

// @query link_condition
// 조건 → 원료. row.rel = 'RECOMMENDS' | 'EXCLUDES' (관계 타입은 파라미터로 못 받아 FOREACH 로 분기)
// $rows: [{code, ingredient_id, rel, priority, reason, source, props: {severity, evidence, derived} (선택)}]
UNWIND $rows AS row
MATCH (c:Condition {code: row.code})
MATCH (i:Ingredient {ingredient_id: row.ingredient_id})
FOREACH (_ IN CASE WHEN row.rel = 'RECOMMENDS' THEN [1] ELSE [] END |
  MERGE (c)-[r:RECOMMENDS]->(i)
  SET r.priority = row.priority, r.source = row.source, r.synced_at = datetime())
FOREACH (_ IN CASE WHEN row.rel = 'EXCLUDES' THEN [1] ELSE [] END |
  MERGE (c)-[r:EXCLUDES]->(i)
  SET r += coalesce(row.props, {}), r.reason = row.reason, r.source = row.source, r.synced_at = datetime())
RETURN count(*) AS processed;

// @query link_product_contains
// 제품 → 원료 (I0030, C003). 제품·업체 노드도 함께 만든다
// $rows: [{report_no, product_name, company_id, company_name, ingredient_id, amount, unit, source}]
UNWIND $rows AS row
MATCH (i:Ingredient {ingredient_id: row.ingredient_id})
MERGE (p:Product {report_no: row.report_no}) SET p.name = row.product_name
MERGE (c:Company {company_id: row.company_id}) SET c.name = row.company_name
MERGE (c)-[:MAKES]->(p)
MERGE (p)-[r:CONTAINS]->(i)
SET r.amount = row.amount, r.unit = row.unit, r.source = row.source, r.synced_at = datetime()
RETURN count(r) AS linked;

// @query cleanup_sheet_edges
// 시트를 전체 다시 임포트한 뒤, 이번 실행 전에 갱신된 관계는 시트에서 지워진 것으로 보고 삭제한다
// 전체 교체형 원천(시트)에만 쓴다. API 원천에는 쓰지 않는다
// $source: 시트명, $run_started_at: datetime
MATCH ()-[r]->()
WHERE r.source = $source AND r.synced_at < $run_started_at
DELETE r
RETURN count(*) AS deleted;
