// 11_load_context.cypher
// 추천 로직이 쓰는 맥락 데이터: 생애주기·질환, 세그먼트·관심사, 시너지·길항
// 원천은 '생애주기별 질환 및 증상_통합.xlsx' 와 load/refs/public_refs_kr.json (국내 공공 출처 조사 결과)
// 각 블록은 "// @query 이름" 으로 시작하고 ; 로 끝난다

// @query upsert_life_stage
// $rows: [{name, props: {age_band, age_min, age_max, source}}]
UNWIND $rows AS row
MERGE (l:LifeStage {name: row.name})
SET l += row.props, l.synced_at = datetime()
RETURN count(l) AS upserted;

// @query upsert_symptom_category
// 증상 분류와 그 분류에 속한 증상. 증상은 이름으로 찾는다
// $rows: [{name, definition, symptoms: [증상 이름], source}]
UNWIND $rows AS row
MERGE (c:SymptomCategory {name: row.name})
SET c.definition = row.definition, c.source = row.source, c.synced_at = datetime()
WITH c, row
UNWIND row.symptoms AS symptom_name
MATCH (s:Symptom {name: symptom_name})
MERGE (c)-[r:INCLUDES]->(s)
SET r.source = row.source, r.synced_at = datetime()
RETURN count(r) AS linked;

// @query link_common_disease
// 생애주기 → 다빈도 질환. sex 는 'A'(공통) | 'M' | 'F'. ref_* 는 심평원 통계로 확인한 근거
// $rows: [{stage, disease, sex, order, main_symptoms, categories: [분류명], source, props: {ref_org, ref_doc, ref_url, ref_stat}}]
UNWIND $rows AS row
MATCH (l:LifeStage {name: row.stage})
MERGE (d:Disease {name: row.disease})
MERGE (l)-[r:COMMON_DISEASE {sex: row.sex}]->(d)
SET r += coalesce(row.props, {}), r.order = row.order, r.main_symptoms = row.main_symptoms,
    r.source = row.source, r.synced_at = datetime()
WITH d, row
UNWIND row.categories AS category
MATCH (c:SymptomCategory {name: category})
MERGE (d)-[k:CLASSIFIED_AS]->(c)
SET k.source = row.source, k.synced_at = datetime()
RETURN count(DISTINCT d) AS linked;

// @query link_disease_symptom
// 질환 → 증상 (공공 출처로 확인한 것). 표에 없는 질환은 만들지 않는다
// $rows: [{disease, symptom, source, props: {doc, url, quote}}]
UNWIND $rows AS row
MATCH (d:Disease {name: row.disease})
MATCH (s:Symptom {name: row.symptom})
MERGE (d)-[r:HAS_SYMPTOM]->(s)
SET r += coalesce(row.props, {}), r.source = row.source, r.evidence = 'confirmed', r.synced_at = datetime()
RETURN count(r) AS linked;

// @query upsert_segment_interest
// 세그먼트(성별 × 연령대) → 관심사
// $rows: [{code, segment: {name, sex, age_band, age_min, age_max}, interest, group, order, source}]
UNWIND $rows AS row
MERGE (g:Segment {code: row.code})
SET g += row.segment, g.synced_at = datetime()
MERGE (n:Interest {name: row.interest})
SET n.group = row.group, n.source = row.source, n.synced_at = datetime()
MERGE (g)-[r:HAS_INTEREST]->(n)
SET r.order = row.order, r.source = row.source, r.synced_at = datetime()
RETURN count(r) AS linked;

// @query link_interest_ingredient
// 관심사 → 추천 원료. raw_name 은 표에 적힌 이름, match 는 원료 대응 방식('이름 일치' | '유사')
// $rows: [{interest, ingredient_id, order, raw_name, match, source}]
UNWIND $rows AS row
MATCH (n:Interest {name: row.interest})
MATCH (i:Ingredient {ingredient_id: row.ingredient_id})
MERGE (n)-[r:RECOMMENDS]->(i)
SET r.order = row.order, r.raw_name = row.raw_name, r.match = row.match,
    r.source = row.source, r.evidence = 'hypothesis', r.synced_at = datetime()
RETURN count(r) AS linked;

// @query upsert_synergy
// $rows: [{key, props: {name, effect, verdict, source, doc, url, quote}}]
UNWIND $rows AS row
MERGE (s:Synergy {key: row.key})
SET s += row.props, s.synced_at = datetime()
RETURN count(s) AS upserted;

// @query link_in_synergy
// $rows: [{ingredient_id, key, source, props: {doc, url, quote, page}}]
UNWIND $rows AS row
MATCH (i:Ingredient {ingredient_id: row.ingredient_id})
MATCH (s:Synergy {key: row.key})
MERGE (i)-[r:IN_SYNERGY]->(s)
SET r += coalesce(row.props, {}), r.source = row.source, r.evidence = 'confirmed', r.synced_at = datetime()
RETURN count(r) AS linked;

// @query link_antagonizes
// 함께 먹으면 흡수·작용을 방해하는 원료 쌍
// $rows: [{a, b, effect, source, props: {doc, url, quote}}]
UNWIND $rows AS row
MATCH (a:Ingredient {ingredient_id: row.a})
MATCH (b:Ingredient {ingredient_id: row.b})
MERGE (a)-[r:ANTAGONIZES]->(b)
SET r += coalesce(row.props, {}), r.effect = row.effect, r.source = row.source,
    r.evidence = 'confirmed', r.synced_at = datetime()
RETURN count(r) AS linked;
