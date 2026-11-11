// 01_schema.cypher
// 제약조건과 인덱스. IF NOT EXISTS 라서 여러 번 실행해도 안전하다
// 대상: Neo4j 5.13 이상 (벡터 인덱스 최소 버전). 테스트는 5.26 Community 에서 수행

// ---------------------------------------------------------------
// 유니크 키 (유니크 제약은 인덱스를 함께 만든다)
// ---------------------------------------------------------------
CREATE CONSTRAINT symptom_id      IF NOT EXISTS FOR (n:Symptom)     REQUIRE n.symptom_id    IS UNIQUE;
CREATE CONSTRAINT mechanism_id    IF NOT EXISTS FOR (n:Mechanism)   REQUIRE n.mechanism_id  IS UNIQUE;
CREATE CONSTRAINT function_tag_id IF NOT EXISTS FOR (n:FunctionTag) REQUIRE n.tag_id        IS UNIQUE;
CREATE CONSTRAINT ingredient_id   IF NOT EXISTS FOR (n:Ingredient)  REQUIRE n.ingredient_id IS UNIQUE;
CREATE CONSTRAINT substance_id    IF NOT EXISTS FOR (n:Substance)   REQUIRE n.substance_id  IS UNIQUE;
CREATE CONSTRAINT drug_id         IF NOT EXISTS FOR (n:Drug)        REQUIRE n.drug_id       IS UNIQUE;
CREATE CONSTRAINT condition_code  IF NOT EXISTS FOR (n:Condition)   REQUIRE n.code          IS UNIQUE;
CREATE CONSTRAINT product_no      IF NOT EXISTS FOR (n:Product)     REQUIRE n.report_no     IS UNIQUE;
CREATE CONSTRAINT company_id      IF NOT EXISTS FOR (n:Company)     REQUIRE n.company_id    IS UNIQUE;
CREATE CONSTRAINT life_stage_name IF NOT EXISTS FOR (n:LifeStage)   REQUIRE n.name          IS UNIQUE;
CREATE CONSTRAINT disease_name    IF NOT EXISTS FOR (n:Disease)     REQUIRE n.name          IS UNIQUE;
CREATE CONSTRAINT symptom_cat     IF NOT EXISTS FOR (n:SymptomCategory) REQUIRE n.name      IS UNIQUE;
CREATE CONSTRAINT segment_code    IF NOT EXISTS FOR (n:Segment)     REQUIRE n.code          IS UNIQUE;
CREATE CONSTRAINT interest_name   IF NOT EXISTS FOR (n:Interest)    REQUIRE n.name          IS UNIQUE;
CREATE CONSTRAINT synergy_key     IF NOT EXISTS FOR (n:Synergy)     REQUIRE n.key           IS UNIQUE;

// ---------------------------------------------------------------
// 필터용 인덱스
// ---------------------------------------------------------------
CREATE INDEX ingredient_status   IF NOT EXISTS FOR (n:Ingredient) ON (n.status);
CREATE INDEX ingredient_review   IF NOT EXISTS FOR (n:Ingredient) ON (n.review_state);
CREATE INDEX ingredient_recog_no IF NOT EXISTS FOR (n:Ingredient) ON (n.recognition_no);
CREATE INDEX symptom_category    IF NOT EXISTS FOR (n:Symptom)    ON (n.category);
CREATE INDEX condition_kind      IF NOT EXISTS FOR (n:Condition)  ON (n.kind);
CREATE INDEX drug_atc            IF NOT EXISTS FOR (n:Drug)       ON (n.atc);

// ---------------------------------------------------------------
// 전문검색 인덱스 (STRING, LIST<STRING> 모두 색인)
// 한글은 기본 분석기로 부분 일치가 안 되어 cjk 분석기(2글자 단위)를 쓴다
// ---------------------------------------------------------------
CREATE FULLTEXT INDEX symptom_text IF NOT EXISTS
FOR (n:Symptom) ON EACH [n.name, n.aliases]
OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } };

CREATE FULLTEXT INDEX name_search IF NOT EXISTS
FOR (n:Substance|Ingredient|Product) ON EACH [n.name, n.aliases]
OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } };

// ---------------------------------------------------------------
// 벡터 인덱스. dimensions 는 실제 임베딩 모델 차원으로 바꾼다
// 공통 라벨(Ingredient) 하나에 걸어야 5.x 에서도 원료 유형 전체를 한 번에 검색할 수 있다
// (여러 라벨에 걸친 벡터 인덱스는 2026.01 부터 지원)
// ---------------------------------------------------------------
CREATE VECTOR INDEX symptom_embedding IF NOT EXISTS
FOR (n:Symptom) ON n.embedding
OPTIONS { indexConfig: { `vector.dimensions`: 1536, `vector.similarity_function`: 'cosine' } };

CREATE VECTOR INDEX ingredient_embedding IF NOT EXISTS
FOR (n:Ingredient) ON n.embedding
OPTIONS { indexConfig: { `vector.dimensions`: 1536, `vector.similarity_function`: 'cosine' } };
