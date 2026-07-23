-- TB_DUCT_EQUIPMENT_TAKEOFF_PATTERN
-- TB_DUCT의 취출구(TAKEOFF_POC_ID_LIST)가 TB_ROUTE_PATH.TARGET_GUID와 정확히 일치하는
-- 것만 골라 장비(EQUIPMENT_TAG)에 귀속시킨 뒤, [장비명, 유틸리티, 덕트구분자, 방향]을 기준으로
-- 개별 면(Face) 단위 레코드로 세분화하여 저장한 통계 및 공간 데이터 테이블.
-- Tools/AnalyzeDuctPocPattern.py가 적재한다.

DROP TABLE IF EXISTS "TB_DUCT_EQUIPMENT_TAKEOFF_PATTERN" CASCADE;

CREATE TABLE IF NOT EXISTS "TB_DUCT_EQUIPMENT_TAKEOFF_PATTERN" (
    "ID"                 bigserial PRIMARY KEY,
    "EQUIPMENT_TAG"      text NOT NULL,
    "UTILITY"            text NOT NULL,
    "DUCT_NAME"          text NOT NULL,
    "FACE"               text NOT NULL,
    "LAYOUT"             text NOT NULL,
    "N_TAKEOFFS"         integer NOT NULL,
    "TAKEOFF_WIDTH_MM"   double precision,
    "TAKEOFF_HEIGHT_MM"  double precision,
    "BOUNDBOX_WIDTH_MM"  double precision,
    "BOUNDBOX_HEIGHT_MM" double precision,
    "TAKEOFF_LAYOUT"     geometry(MultiPointZ, 0),
    "ANALYZED_AT"        timestamptz DEFAULT now(),
    UNIQUE ("EQUIPMENT_TAG", "UTILITY", "DUCT_NAME", "FACE")
);

CREATE INDEX IF NOT EXISTS "IX_TDETP_KEY"
ON "TB_DUCT_EQUIPMENT_TAKEOFF_PATTERN" ("EQUIPMENT_TAG", "UTILITY");

CREATE INDEX IF NOT EXISTS "IX_TDETP_TAKEOFF_LAYOUT"
ON "TB_DUCT_EQUIPMENT_TAKEOFF_PATTERN" USING gist("TAKEOFF_LAYOUT");
