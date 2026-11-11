// 30_queries.cypher
// 서비스가 쓰는 조회. 질의 흐름 순서대로 정리했다 (docs/DESIGN.md 5장)
// 규칙: 원료는 항상 status = 'active' 만 쓴다. 검수 필요(needs_review) 기능성 관계는 추천 경로에서 뺀다
// 각 블록은 "// @query 이름" 으로 시작하고 ; 로 끝난다
// 벡터·전문검색은 5.x 호환 프로시저로 썼다. 2026.01+ 에서는 SEARCH 절이 권장된다(주석 예시)

// @query symptom_fulltext
// 사용자 문장 → 증상 후보. $q: '피곤'
CALL db.index.fulltext.queryNodes('symptom_text', $q) YIELD node, score
RETURN node.symptom_id AS symptom_id, node.name AS name, node.category AS category, score
ORDER BY score DESC
LIMIT 5;

// @query symptom_vector
// 표현이 달라도 뜻이 같은 증상. $embedding: 문장 임베딩. 점수가 임계값보다 낮으면 되묻기로 분기
// 2026.01+ :  MATCH (s:Symptom) SEARCH s IN (VECTOR INDEX symptom_embedding FOR $embedding LIMIT 5) SCORE AS score
CALL db.index.vector.queryNodes('symptom_embedding', 5, $embedding) YIELD node, score
RETURN node.symptom_id AS symptom_id, node.name AS name, score
ORDER BY score DESC;

// @query name_lookup
// 원료·물질 이름 검색. "IMO" 처럼 물질 별칭으로 찾으면 그 물질의 원료(고시형·개별인정형)를 모두 돌려준다
// $q
CALL db.index.fulltext.queryNodes('name_search', $q) YIELD node, score
OPTIONAL MATCH (node)<-[:FORM_OF]-(via:Ingredient)
WITH node, score, collect(via) AS vias
WITH score, CASE WHEN node:Ingredient THEN [node] ELSE [] END + vias AS ings
UNWIND ings AS i
WITH i, max(score) AS score
WHERE i.status = 'active'
RETURN i.ingredient_id AS ingredient_id, i.name AS name,
       [l IN labels(i) WHERE l <> 'Ingredient'][0] AS kind, score
ORDER BY score DESC, ingredient_id
LIMIT 20;

// @query symptom_to_ingredient
// 증상 → 기전 → 기능성 ← 원료. 설명 경로(reasons)를 함께 돌려준다
// 결핍 기전은 기능성 태그 없이 영양소 원료로 바로 간다 (REPLENISHED_BY, via = 'deficiency')
// $symptom_ids
MATCH (s:Symptom)-[e:EXPLAINED_BY]->(m:Mechanism)
WHERE s.symptom_id IN $symptom_ids
CALL {
  WITH m
  MATCH (m)-[:SUPPORTED_BY]->(t:FunctionTag)<-[f:HAS_FUNCTION]-(i:Ingredient)
  WHERE i.status = 'active' AND coalesce(f.needs_review, false) = false
  RETURN i, t.name AS function, 'function' AS via
  UNION
  WITH m
  MATCH (m)-[:REPLENISHED_BY]->(i:Ingredient)
  WHERE i.status = 'active'
  RETURN i, null AS function, 'deficiency' AS via
}
WITH i, collect(DISTINCT {symptom: s.name, mechanism: m.name, function: function, via: via, evidence: e.evidence}) AS reasons
RETURN i.ingredient_id AS ingredient_id, i.name AS name,
       [l IN labels(i) WHERE l <> 'Ingredient'][0] AS kind,
       size(reasons) AS hits, reasons
ORDER BY hits DESC, ingredient_id
LIMIT 20;

// @query condition_to_ingredient
// 룰 엔진이 평가해 낸 활성 조건 코드(h-code, s-code) → 추천 원료
// $condition_codes
MATCH (c:Condition)-[r:RECOMMENDS]->(i:Ingredient {status: 'active'})
WHERE c.code IN $condition_codes
RETURN i.ingredient_id AS ingredient_id, i.name AS name,
       collect(c.code) AS matched_codes, min(r.priority) AS priority
ORDER BY priority ASC, ingredient_id;

// @query drug_depletes
// 복용 약물 → 고갈 영양소. 성분에서 계열까지 펼쳐서 찾는다
// $drug_ids: 처방 매핑 결과 (성분 단위 Drug ID)
MATCH (d:Drug) WHERE d.drug_id IN $drug_ids
MATCH (d)-[:IN_CLASS*0..2]->(x:Drug)
MATCH (x)-[r:DEPLETES]->(i:Ingredient {status: 'active'})
RETURN i.ingredient_id AS ingredient_id, i.name AS name,
       collect(DISTINCT d.name) AS depleted_by, collect(DISTINCT r.source) AS sources
ORDER BY ingredient_id;

// @query safety_filter
// 후보 원료마다 막히는 이유를 모은다. allowed = false 면 추천에서 빼거나 "전문가 상담" 문구를 강제한다
// 사용자 약은 성분 → 계열로 펼친다 (주의사항은 대개 "항응고제" 같은 계열로 쓰여 있다)
// $ingredient_ids, $condition_codes, $drug_ids
OPTIONAL MATCH (d:Drug) WHERE d.drug_id IN $drug_ids
OPTIONAL MATCH (d)-[:IN_CLASS*0..2]->(x:Drug)
WITH collect(DISTINCT x.drug_id) AS user_drugs
UNWIND $ingredient_ids AS iid
MATCH (i:Ingredient {ingredient_id: iid})
OPTIONAL MATCH (c:Condition)-[ex:EXCLUDES]->(i)
  WHERE c.code IN $condition_codes
WITH i, user_drugs,
     collect(DISTINCT CASE WHEN c IS NOT NULL
       THEN {kind: 'condition', code: c.code, severity: ex.severity, reason: ex.reason} END) AS by_condition
OPTIONAL MATCH (i)-[y:INTERACTS_WITH]->(dx:Drug)
  WHERE dx.drug_id IN user_drugs
WITH i, by_condition,
     collect(DISTINCT CASE WHEN dx IS NOT NULL
       THEN {kind: 'drug', drug: dx.name, severity: y.severity, note: y.note} END) AS by_drug
RETURN i.ingredient_id AS ingredient_id, i.name AS name,
       by_condition + by_drug AS blocks,
       i.status = 'active' AND size(by_condition) + size(by_drug) = 0 AS allowed
ORDER BY ingredient_id;

// @query products_for_ingredient
// 원료가 든 제품. 1일 섭취 상한을 넘는 제품은 뺀다 (단위가 같을 때만 비교, 다르면 unit_mismatch 표시)
// $ingredient_id
MATCH (p:Product)-[r:CONTAINS]->(i:Ingredient {ingredient_id: $ingredient_id, status: 'active'})
WHERE i.intake_max IS NULL OR r.unit <> i.intake_unit OR r.amount <= i.intake_max
OPTIONAL MATCH (c:Company)-[:MAKES]->(p)
RETURN p.report_no AS report_no, p.name AS product, c.name AS company,
       r.amount AS amount, r.unit AS unit, (r.unit <> i.intake_unit) AS unit_mismatch
ORDER BY report_no;

// @query evidence_pack
// LLM 에 넘길 근거 묶음. 섭취량 같은 수치는 LLM 이 이 값을 그대로 인용해야 한다
// $ingredient_ids: 안전 필터를 통과한 원료
UNWIND $ingredient_ids AS iid
MATCH (i:Ingredient {ingredient_id: iid, status: 'active'})
OPTIONAL MATCH (i)-[f:HAS_FUNCTION]->(t:FunctionTag)
  WHERE coalesce(f.needs_review, false) = false
RETURN i.ingredient_id AS ingredient_id,
       i.name AS name,
       [l IN labels(i) WHERE l <> 'Ingredient'][0] AS kind,
       i.recognition_no AS recognition_no,
       i.intake_min AS intake_min, i.intake_max AS intake_max, i.intake_unit AS intake_unit,
       i.intake_raw AS intake_raw,
       i.precaution_text AS precaution,
       i.function_text AS function_text,
       i.raw_materials AS raw_materials, i.manufacturing_std AS manufacturing_std,
       i.marker_compound AS marker_compound, i.name_raw AS name_raw,
       [x IN collect({tag: t.name, text: f.text}) WHERE x.tag IS NOT NULL] AS functions,
       i.source AS source
ORDER BY ingredient_id;

// ---------------------------------------------------------------
// 추천 파이프라인용 (reco/). 2-1 증상 후보, 2-2 관심사 후보, 4-2 시너지·길항
// ---------------------------------------------------------------

// @query lifestage_symptoms
// 나이·성별 → 생애주기 → 다빈도 질환 → 증상. 질환에 확인된 증상(HAS_SYMPTOM)이 없을 때만 증상 분류로 펼친다
// $age, $sex ('M' | 'F')
MATCH (l:LifeStage) WHERE l.age_min <= $age AND $age <= l.age_max
MATCH (l)-[c:COMMON_DISEASE]->(d:Disease) WHERE c.sex IN ['A', $sex]
CALL {
  WITH d
  MATCH (d)-[h:HAS_SYMPTOM]->(s:Symptom)
  RETURN s, 'disease' AS basis, h.source AS ref
  UNION
  WITH d
  WITH d WHERE NOT (d)-[:HAS_SYMPTOM]->()
  MATCH (d)-[:CLASSIFIED_AS]->(:SymptomCategory)-[:INCLUDES]->(s:Symptom)
  RETURN s, 'category' AS basis, null AS ref
}
RETURN s.symptom_id AS symptom_id, s.name AS name, l.name AS life_stage,
       collect(DISTINCT d.name) AS diseases, collect(DISTINCT basis) AS basis,
       [x IN collect(DISTINCT ref) WHERE x IS NOT NULL] AS refs
ORDER BY symptom_id;

// @query symptom_candidates
// 2-1 증상 → 기전 → 기능성 ← 원료 (또는 결핍 기전 → 영양소). 경로마다 구간별 출처를 돌려준다
// $symptom_ids: 사용자 증상, $lifecycle_ids: 그 나이·성별의 생애주기 증상
// $skip_mechanisms, $skip_tags: 성별에 맞지 않는 기전·기능성 (서버가 정한다)
MATCH (s:Symptom)-[e:EXPLAINED_BY]->(m:Mechanism)
WHERE s.symptom_id IN $symptom_ids AND NOT m.mechanism_id IN $skip_mechanisms
CALL {
  WITH m
  MATCH (m)-[sb:SUPPORTED_BY]->(t:FunctionTag)<-[f:HAS_FUNCTION]-(i:Ingredient)
  WHERE i.status = 'active' AND coalesce(f.needs_review, false) = false AND NOT t.tag_id IN $skip_tags
  RETURN i, t.name AS function, 'function' AS via, [sb.source, f.source] AS hop_sources,
         sb.evidence = 'confirmed' AND f.evidence = 'confirmed' AS hop_confirmed, sb.urls AS hop_urls
  UNION
  WITH m
  MATCH (m)-[rp:REPLENISHED_BY]->(i:Ingredient)
  WHERE i.status = 'active'
  RETURN i, null AS function, 'deficiency' AS via, [rp.source] AS hop_sources,
         rp.evidence = 'confirmed' AS hop_confirmed, rp.urls AS hop_urls
}
WITH i, collect({symptom_id: s.symptom_id, symptom: s.name, mechanism: m.name, function: function, via: via,
                 lifecycle: s.symptom_id IN $lifecycle_ids,
                 confirmed: e.evidence = 'confirmed' AND hop_confirmed,
                 sources: [e.source] + hop_sources,
                 urls: coalesce(e.urls, []) + coalesce(hop_urls, [])}) AS paths
RETURN i.ingredient_id AS ingredient_id, i.name AS name,
       [l IN labels(i) WHERE l <> 'Ingredient'][0] AS kind, paths
ORDER BY ingredient_id;

// @query interest_candidates
// 2-2 나이·성별 → 세그먼트 → 관심사 → 원료
// $age, $sex ('M' | 'F')
MATCH (g:Segment {sex: $sex}) WHERE g.age_min <= $age AND $age <= g.age_max
MATCH (g)-[h:HAS_INTEREST]->(n:Interest)-[r:RECOMMENDS]->(i:Ingredient {status: 'active'})
WITH g, i, h, n, r ORDER BY h.order, r.order
RETURN i.ingredient_id AS ingredient_id, i.name AS name,
       [l IN labels(i) WHERE l <> 'Ingredient'][0] AS kind, g.name AS segment,
       collect({interest: n.name, group: n.group, interest_order: h.order, order: r.order,
                raw_name: r.raw_name, match: r.match, source: r.source}) AS interests,
       i.function_text AS function_text, i.source AS ingredient_source
ORDER BY ingredient_id;

// @query drug_matchers
// 사용자 약 이름·분류에서 약 성분·계열을 알아내는 정규식. 서버가 사용자 데이터에 걸고, 그래프에는 사용자 약을 넣지 않는다
MATCH (d:Drug) WHERE d.match IS NOT NULL
RETURN d.drug_id AS drug_id, d.name AS name, d.match AS match
ORDER BY drug_id;

// @query drug_candidates
// 2-3 복용약 → 고갈 영양소. 성분에서 계열까지 펼쳐서 찾는다. grade A 만 후보가 되고 B 는 참고 표시다(서버가 나눈다)
// $drug_ids: 서버가 사용자 약에서 알아낸 Drug ID
MATCH (d:Drug) WHERE d.drug_id IN $drug_ids
MATCH (d)-[:IN_CLASS*0..2]->(x:Drug)-[r:DEPLETES]->(i:Ingredient {status: 'active'})
WITH i, collect(DISTINCT {drug_id: x.drug_id, drug: x.name, grade: r.grade, effect: r.effect, quote: r.quote,
                          product: r.product, url: r.url, note: r.note, source: r.source}) AS depletions
RETURN i.ingredient_id AS ingredient_id, i.name AS name,
       [l IN labels(i) WHERE l <> 'Ingredient'][0] AS kind, i.function_text AS function_text, depletions
ORDER BY ingredient_id;

// @query synergy_partners
// 4-2 후보 원료와 함께 먹으면 좋은 원료. 근거는 Synergy 노드의 공공 출처
// $ingredient_ids
MATCH (i:Ingredient)-[:IN_SYNERGY]->(s:Synergy)<-[:IN_SYNERGY]-(p:Ingredient {status: 'active'})
WHERE i.ingredient_id IN $ingredient_ids AND p <> i
RETURN i.ingredient_id AS ingredient_id, p.ingredient_id AS partner_id, p.name AS partner,
       s.name AS synergy, s.effect AS effect, s.verdict AS verdict, s.source AS source, s.url AS url
ORDER BY ingredient_id, partner_id;

// @query antagonist_pairs
// 4-2 후보 안에서 서로 흡수·작용을 방해하는 쌍
// $ingredient_ids
MATCH (a:Ingredient)-[r:ANTAGONIZES]->(b:Ingredient)
WHERE a.ingredient_id IN $ingredient_ids AND b.ingredient_id IN $ingredient_ids
RETURN a.ingredient_id AS a_id, a.name AS a_name, b.ingredient_id AS b_id, b.name AS b_name,
       r.effect AS effect, r.source AS source, r.url AS url
ORDER BY a_id, b_id;

// @query all_symptoms
// 1단계 LLM 정규화에 넘기는 증상 목록
MATCH (s:Symptom)
RETURN s.symptom_id AS symptom_id, s.name AS name
ORDER BY symptom_id;
