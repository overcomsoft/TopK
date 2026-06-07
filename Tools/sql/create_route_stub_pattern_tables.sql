-- Stub 패턴 저장/활용 스키마.
--
-- 1) TB_ROUTE_STUB_PATTERN: 기존 설계배관에서 추출한 START/END Stub 샘플 저장.
-- 2) TB_ROUTE_STUB_TEMPLATE: 반복 샘플을 집계한 신규 자동배관설계용 재사용 template 저장.
-- 3) TB_ROUTE_STUB_APPLICATION_LOG: 신규 설계에 어떤 template이 적용됐는지 추적.
--
-- pgvector가 설치되어 있으면 vector(24), vector(3), HNSW 인덱스를 사용한다.
-- pgvector가 없는 환경에서는 ExtractStubPatterns.py의 fallback_schema_sql()이 JSON 전용 스키마를 만든다.
CREATE EXTENSION IF NOT EXISTS vector;

-- 개별 Stub 샘플 저장 테이블.
-- ROUTE_PATH_GUID 하나에서 START/END 최대 2개 행이 생성된다.
-- FEAT = face(6D) + dir1(6D) + dir2(6D) + anchor 상대좌표(3D) + 진행방향(3D).
CREATE TABLE IF NOT EXISTS "TB_ROUTE_STUB_PATTERN" (
    "PATTERN_ID" text PRIMARY KEY,
    "ROUTE_PATH_GUID" text NOT NULL,
    "STUB_KIND" text NOT NULL,
    "ANCHOR_KIND" text NOT NULL,
    "ANCHOR_NAME" text,
    "MAIN_EQUIPMENT_NAME" text,
    "PROCESS_NAME" text,
    "UTILITY_GROUP" text,
    "UTILITY" text,
    "SIZE" text,
    "FACE" text,
    "DIR_SEQ" text,
    "N_BENDS" integer,
    "RISE_MM" double precision,
    "OFFSET_MM" double precision,
    "DIAMETER_MM" double precision,
    "STUB_LENGTH_MM" double precision,
    "SOURCE_POS" jsonb,
    "TARGET_POS" jsonb,
    "ANCHOR_MIN" jsonb,
    "ANCHOR_MAX" jsonb,
    "STUB_POINTS" jsonb,
    "FEAT" vector(24),
    "DIR_UNIT" vector(3),
    "FEAT_JSON" jsonb,
    "DIR_UNIT_JSON" jsonb,
    "CREATED_AT" timestamp without time zone DEFAULT now()
);

-- 조건 검색용 btree 인덱스.
-- 자동설계 활용 시 메인장비/유틸리티/Stub 종류 조건으로 먼저 후보를 좁힌다.
CREATE INDEX IF NOT EXISTS "IX_TRSP_KEY"
ON "TB_ROUTE_STUB_PATTERN" ("MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "SIZE", "STUB_KIND", "ANCHOR_KIND");

-- 형상 유사도 검색용 HNSW 인덱스. L2 거리 기준으로 24D feature 최근접 샘플을 찾는다.
CREATE INDEX IF NOT EXISTS "IX_TRSP_FEAT_HNSW"
ON "TB_ROUTE_STUB_PATTERN" USING hnsw ("FEAT" vector_l2_ops);

-- 진행방향 유사도 검색용 HNSW 인덱스. 방향벡터 cosine distance에 사용한다.
CREATE INDEX IF NOT EXISTS "IX_TRSP_DIR_HNSW"
ON "TB_ROUTE_STUB_PATTERN" USING hnsw ("DIR_UNIT" vector_cosine_ops);

-- Stub 샘플을 그룹화한 재사용 template 테이블.
-- 신규 라우팅 요청은 이 테이블에서 START/END 후보 template을 조회한다.
CREATE TABLE IF NOT EXISTS "TB_ROUTE_STUB_TEMPLATE" (
    "TEMPLATE_ID" text PRIMARY KEY,
    "STUB_KIND" text NOT NULL,
    "ANCHOR_KIND" text NOT NULL,
    "MAIN_EQUIPMENT_NAME" text,
    "UTILITY_GROUP" text,
    "UTILITY" text,
    "SIZE" text,
    "FACE" text,
    "DIR_SEQ" text,
    "SAMPLE_COUNT" integer NOT NULL,
    "AVG_RISE_MM" double precision,
    "AVG_OFFSET_MM" double precision,
    "AVG_DIAMETER_MM" double precision,
    "AVG_STUB_LENGTH_MM" double precision,
    "REPRESENTATIVE_PATTERN_ID" text,
    "REPRESENTATIVE_ROUTE_PATH_GUID" text,
    "REPRESENTATIVE_STUB_POINTS" jsonb,
    "AVG_FEAT" vector(24),
    "AVG_DIR_UNIT" vector(3),
    "AVG_FEAT_JSON" jsonb,
    "AVG_DIR_UNIT_JSON" jsonb,
    "CREATED_AT" timestamp without time zone DEFAULT now()
);

-- template 조건 검색용 인덱스.
CREATE INDEX IF NOT EXISTS "IX_TRST_KEY"
ON "TB_ROUTE_STUB_TEMPLATE" ("MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "SIZE", "STUB_KIND", "ANCHOR_KIND");

-- template feature 최근접 검색용 인덱스.
CREATE INDEX IF NOT EXISTS "IX_TRST_FEAT_HNSW"
ON "TB_ROUTE_STUB_TEMPLATE" USING hnsw ("AVG_FEAT" vector_l2_ops);

-- 신규 자동배관설계에서 Stub template을 실제 적용한 기록.
-- 후보 점수, 선택된 START/END template, 최종 점열을 추적하기 위한 로그 테이블이다.
CREATE TABLE IF NOT EXISTS "TB_ROUTE_STUB_APPLICATION_LOG" (
    "APPLICATION_ID" text PRIMARY KEY,
    "REQUEST_ID" text,
    "SOURCE_TEMPLATE_ID" text,
    "TARGET_TEMPLATE_ID" text,
    "MAIN_EQUIPMENT_NAME" text,
    "UTILITY_GROUP" text,
    "UTILITY" text,
    "SIZE" text,
    "START_STUB_POINTS" jsonb,
    "END_STUB_POINTS" jsonb,
    "MIDDLE_ROUTE_POINTS" jsonb,
    "FINAL_ROUTE_POINTS" jsonb,
    "SCORE" double precision,
    "STATUS" text,
    "FAIL_REASON" text,
    "CREATED_AT" timestamp without time zone DEFAULT now()
);
