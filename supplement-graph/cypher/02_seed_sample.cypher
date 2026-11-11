// 02_seed_sample.cypher
// 테스트용 최소 데이터. 엑셀(건기식원료_DB_261030_API정리.xlsx) 내용으로 만들었고 ID 는 임시 규칙을 따른다
// 녹차추출물(NTC-green-tea)은 이름만 넣은 자리표시자다

// ---------- 원료: 영양성분 (2-1 시트, 고시 제2025-11호) ----------
MERGE (i:Ingredient {ingredient_id: 'NUT-45663'})
SET i:Nutrient, i.name = '비타민 B1', i.intake_min = 0.36, i.intake_max = 100, i.intake_unit = 'mg',
    i.function_text = '탄수화물과 에너지 대사에 필요', i.source = '고시 제2025-11호',
    i.status = 'active', i.review_state = 'reviewed';

MERGE (i:Ingredient {ingredient_id: 'NUT-45669'})
SET i:Nutrient, i.name = '비타민 B12', i.intake_min = 0.72, i.intake_max = 2000, i.intake_unit = 'μg',
    i.function_text = '정상적인 엽산 대사에 필요', i.source = '고시 제2025-11호',
    i.status = 'active', i.review_state = 'reviewed';

MERGE (i:Ingredient {ingredient_id: 'NUT-45662'})
SET i:Nutrient, i.name = '비타민 K', i.intake_min = 21, i.intake_max = 1000, i.intake_unit = 'μg',
    i.function_text = '정상적인 혈액응고에 필요', i.precaution_text = '항응고제 등 복용 시 전문가와 상담할 것',
    i.source = '고시 제2025-11호', i.status = 'active', i.review_state = 'reviewed';

// ---------- 원료: 고시형 (자리표시자) ----------
MERGE (i:Ingredient {ingredient_id: 'NTC-green-tea'})
SET i:Notice, i.name = '녹차추출물', i.source = 'I2710', i.status = 'active', i.review_state = 'reviewed';

// ---------- 원료: 개별인정형 (2-3 개별인정형(API) 시트) ----------
MERGE (i:Ingredient {ingredient_id: 'IND-2005-10'})
SET i:Individual, i.name = '이소말토올리고당', i.recognition_no = '2005-10',
    i.intake_min = 8, i.intake_max = 12, i.intake_unit = 'g',
    i.function_text = '장내 유익균의 증식과 유해균의 억제에 도움을 줄 수 있음',
    i.source = 'I-0050', i.source_hash = 'seed', i.status = 'active', i.review_state = 'reviewed',
    i.last_seen_at = datetime();

MERGE (i:Ingredient {ingredient_id: 'IND-2005-11'})
SET i:Individual, i.name = '이소말토올리고당', i.recognition_no = '2005-11',
    i.intake_min = 8, i.intake_max = 12, i.intake_unit = 'g',
    i.function_text = '장내 유익균의 증식과 유해균의 억제에 도움을 줄 수 있음',
    i.source = 'I-0050', i.source_hash = 'seed', i.status = 'active', i.review_state = 'reviewed',
    i.last_seen_at = datetime();

MERGE (i:Ingredient {ingredient_id: 'IND-2004-6'})
SET i:Individual, i.name = '참당귀뿌리추출물', i.recognition_no = '2004-6',
    i.intake_min = 800, i.intake_max = 800, i.intake_unit = 'mg',
    i.function_text = '노인의 인지능력 저하의 개선에 도움을 줄 수 있음',
    i.precaution_text = '혈액응고방지제 또는 혈당강하제를 복용하시는 분은 의사의 상담 하에 사용',
    i.source = 'I-0050', i.source_hash = 'seed', i.status = 'active', i.review_state = 'reviewed',
    i.last_seen_at = datetime();

MERGE (i:Ingredient {ingredient_id: 'IND-2004-7'})
SET i:Individual, i.name = '씨제이 히비스커스 등 복합추출물', i.recognition_no = '2004-7',
    i.intake_min = 2079, i.intake_max = 2079, i.intake_unit = 'mg',
    i.function_text = '체지방 감소에 도움을 줄 수 있음',
    i.precaution_text = '어린이, 임산부 및 수유부는 섭취를 피할 것',
    i.source = 'I-0050', i.source_hash = 'seed', i.status = 'active', i.review_state = 'reviewed',
    i.last_seen_at = datetime();

// ---------- 물질 묶음 ----------
MERGE (s:Substance {substance_id: 'SUB-imo'})
SET s.name = '이소말토올리고당', s.aliases = ['IMO', '이소말토 올리고당'];

// ---------- 증상 · 기전 · 기능성 태그 ----------
MERGE (s:Symptom {symptom_id: 'SYM-fatigue-chronic'})
SET s.name = '만성피로', s.category = '피로/활력', s.aliases = ['계속 피곤함', '기운 없음', '무기력'];

MERGE (s:Symptom {symptom_id: 'SYM-constipation'})
SET s.name = '변비', s.category = '소화/장', s.aliases = ['배변 불편', '화장실을 잘 못 감'];

MERGE (m:Mechanism {mechanism_id: 'MEC-energy-metabolism'})
SET m.name = '에너지 대사 저하', m.evidence_level = 'hypothesis';

MERGE (m:Mechanism {mechanism_id: 'MEC-gut-flora'})
SET m.name = '장내 환경 불균형', m.evidence_level = 'hypothesis';

MERGE (t:FunctionTag {tag_id: 'TAG-energy'})
SET t.name = '대사 및 에너지 생성';

MERGE (t:FunctionTag {tag_id: 'TAG-gut'})
SET t.name = '장 건강 및 배변활동';

// ---------- 약물: 성분과 계열 (드럭머거_21, 원료 주의사항 문구) ----------
MERGE (d:Drug {drug_id: 'DRUG-metformin'})     SET d.name = '메트포르민', d.level = 'ingredient', d.atc = 'A10BA02';
MERGE (d:Drug {drug_id: 'DRUG-warfarin'})      SET d.name = '와파린',     d.level = 'ingredient', d.atc = 'B01AA03';
MERGE (d:Drug {drug_id: 'CLS-antidiabetic'})   SET d.name = '혈당강하제', d.level = 'class';
MERGE (d:Drug {drug_id: 'CLS-anticoagulant'})  SET d.name = '항응고제',   d.level = 'class';

// ---------- 조건: 검진 코드, 대상군 ----------
MERGE (c:Condition {code: 'h-3'})           SET c.kind = 'exam',       c.label = 'BMI 25~30 과체중';
MERGE (c:Condition {code: 'POP-pregnant'})  SET c.kind = 'population', c.label = '임산부·수유부';

// ---------- 관계 ----------
MATCH (s:Symptom {symptom_id: 'SYM-fatigue-chronic'}), (m:Mechanism {mechanism_id: 'MEC-energy-metabolism'}),
      (t:FunctionTag {tag_id: 'TAG-energy'})
MERGE (s)-[r1:EXPLAINED_BY]->(m) SET r1.source = '①증상기준', r1.evidence = 'hypothesis'
MERGE (m)-[r2:SUPPORTED_BY]->(t) SET r2.source = '①증상기준', r2.evidence = 'hypothesis';

MATCH (s:Symptom {symptom_id: 'SYM-constipation'}), (m:Mechanism {mechanism_id: 'MEC-gut-flora'}),
      (t:FunctionTag {tag_id: 'TAG-gut'})
MERGE (s)-[r1:EXPLAINED_BY]->(m) SET r1.source = '①증상기준', r1.evidence = 'hypothesis'
MERGE (m)-[r2:SUPPORTED_BY]->(t) SET r2.source = '①증상기준', r2.evidence = 'hypothesis';

MATCH (t:FunctionTag {tag_id: 'TAG-energy'}), (i:Ingredient)
WHERE i.ingredient_id IN ['NUT-45663', 'NUT-45669']
MERGE (i)-[r:HAS_FUNCTION]->(t)
SET r.text = i.function_text, r.source = '고시 제2025-11호', r.evidence = 'confirmed';

MATCH (t:FunctionTag {tag_id: 'TAG-gut'}), (i:Ingredient)
WHERE i.ingredient_id IN ['IND-2005-10', 'IND-2005-11']
MERGE (i)-[r:HAS_FUNCTION]->(t)
SET r.text = i.function_text, r.source = 'review', r.evidence = 'confirmed';

MATCH (s:Substance {substance_id: 'SUB-imo'}), (i:Ingredient)
WHERE i.ingredient_id IN ['IND-2005-10', 'IND-2005-11']
MERGE (i)-[r:FORM_OF]->(s) SET r.source = 'review';

MATCH (d:Drug {drug_id: 'DRUG-metformin'}), (c:Drug {drug_id: 'CLS-antidiabetic'})
MERGE (d)-[r:IN_CLASS]->(c) SET r.source = 'ATC';

MATCH (d:Drug {drug_id: 'DRUG-warfarin'}), (c:Drug {drug_id: 'CLS-anticoagulant'})
MERGE (d)-[r:IN_CLASS]->(c) SET r.source = 'ATC';

MATCH (d:Drug {drug_id: 'DRUG-metformin'}), (i:Ingredient {ingredient_id: 'NUT-45669'}),
      (s:Symptom {symptom_id: 'SYM-fatigue-chronic'})
MERGE (d)-[r1:DEPLETES]->(i) SET r1.source = '드럭머거_21', r1.evidence = 'confirmed'
MERGE (d)-[r2:MAY_CAUSE]->(s) SET r2.source = '드럭머거_21', r2.evidence = 'confirmed';

MATCH (i:Ingredient {ingredient_id: 'NUT-45662'}), (c:Drug {drug_id: 'CLS-anticoagulant'})
MERGE (i)-[r:INTERACTS_WITH]->(c)
SET r.severity = 'consult', r.note = '항응고제 등 복용 시 전문가와 상담', r.source = '고시 제2025-11호';

MATCH (i:Ingredient {ingredient_id: 'IND-2004-6'}), (c:Drug)
WHERE c.drug_id IN ['CLS-anticoagulant', 'CLS-antidiabetic']
MERGE (i)-[r:INTERACTS_WITH]->(c)
SET r.severity = 'consult', r.note = '의사의 상담 하에 사용', r.source = 'I-0050 주의사항';

MATCH (c:Condition {code: 'h-3'}), (i:Ingredient {ingredient_id: 'NTC-green-tea'})
MERGE (c)-[r:RECOMMENDS]->(i) SET r.priority = 1, r.source = '참고_베어헬스몰(검진결과>추천)';

MATCH (c:Condition {code: 'POP-pregnant'}), (i:Ingredient {ingredient_id: 'IND-2004-7'})
MERGE (c)-[r:EXCLUDES]->(i) SET r.reason = '임산부 및 수유부는 섭취를 피할 것', r.source = 'I-0050 주의사항';
