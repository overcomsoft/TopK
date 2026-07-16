-- TB_DUCT_POC_PATTERN
-- TB_DUCT의 OBB(8정점)로부터 덕트 고유 로컬 3축(길이/높이/폭)을 산출하고,
-- 각 PoC를 TOP/LEFT/RIGHT/BOTTOM/END 면(Face)으로 분류한 뒤 면별 정렬 순서·간격·
-- 유틸리티 시퀀스를 집계한 패턴 테이블. Tools/AnalyzeDuctPocPattern.py 가 적재한다.
--
-- 면 판정 규칙(요약):
--   - 길이축 투영 비율이 END_ZONE_RATIO 이상이면 본선 연결부(END)로 분류(곁가지 PoC와 구분)
--   - 나머지는 높이축/폭축 정규화 투영값 중 절대값이 큰 쪽 방향으로 TOP/BOTTOM/LEFT/RIGHT 결정
-- 상세 로직은 Tools/AnalyzeDuctPocPattern.py의 compute_duct_local_frame()/classify_poc_face() 참조.
--
-- DOMINANT_LAYOUT(2026-07-16 추가): 대표 면(DOMINANT_FACE) 내 취출구들이 길이축 순서로
-- 어떤 배치 형태(일직선/지그재그/분리형/불규칙)를 갖는지. 면별 상세 값은
-- FACE_PATTERN_JSON의 각 면 항목 안 layout/transverse_std_mm/track_count/alternation_rate에
-- 있다. 상세 로직은 Tools/AnalyzeDuctPocPattern.py의 classify_layout_pattern() 참조.
--
-- TAKEOFF_LAYOUT(2026-07-16 추가): 이 덕트의 취출구(TAKEOFF_POC_POSITIONS_LIST) 전체를
-- 하나의 PostGIS MULTIPOINT Z 지오메트리로 저장한 것 — 면/배치형태 분류와 무관하게
-- 실제 3D 좌표를 그대로 담아 GIS 도구·공간 쿼리(ST_DWithin 등)에서 바로 활용 가능.
-- TB_ROUTE_GROUP_PATTERN.GEOM_3D와 동일하게 SRID=0(로컬 엔지니어링 좌표계)을 사용.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS "TB_DUCT_POC_PATTERN" (
    "DUCT_NAME"         text PRIMARY KEY,
    "UTILITY"           text,
    "UTILITY_GROUP"     text,
    "LEVEL"             text,
    "BAY"               text,
    "N_POC_TOTAL"       integer NOT NULL DEFAULT 0,
    "DOMINANT_FACE"     text,
    "DOMINANT_LAYOUT"   text,
    "FACE_PATTERN_JSON" jsonb NOT NULL,
    -- {"TOP": {"count":n, "utility_seq":[...], "spacing_mm":[...], "mean_spacing_mm":v,
    --          "spacing_cv":v, "is_equal_spacing":bool, "layout":"STRAIGHT"|"ZIGZAG"|
    --          "SPLIT_ROWS"|"IRREGULAR"|"SINGLE", "transverse_std_mm":v, "track_count":n,
    --          "alternation_rate":v|null}, "LEFT": {...}, "RIGHT": {...}, ...}
    "TAKEOFF_LAYOUT"    geometry(MultiPointZ, 0),
    "ANALYZED_AT"       timestamptz DEFAULT now()
);

-- 기존에 생성된 테이블에도 신규 컬럼을 추가 (CREATE TABLE IF NOT EXISTS는 이미 있는
-- 테이블에는 아무 영향이 없으므로 별도 ALTER 필요)
ALTER TABLE "TB_DUCT_POC_PATTERN" ADD COLUMN IF NOT EXISTS "DOMINANT_LAYOUT" text;
ALTER TABLE "TB_DUCT_POC_PATTERN" ADD COLUMN IF NOT EXISTS "TAKEOFF_LAYOUT" geometry(MultiPointZ, 0);

CREATE INDEX IF NOT EXISTS "IX_TDPP_KEY"
ON "TB_DUCT_POC_PATTERN" ("UTILITY_GROUP", "UTILITY");

CREATE INDEX IF NOT EXISTS "IX_TDPP_DOMINANT_FACE"
ON "TB_DUCT_POC_PATTERN" ("DOMINANT_FACE");

CREATE INDEX IF NOT EXISTS "IX_TDPP_DOMINANT_LAYOUT"
ON "TB_DUCT_POC_PATTERN" ("DOMINANT_LAYOUT");

CREATE INDEX IF NOT EXISTS "IX_TDPP_TAKEOFF_LAYOUT"
ON "TB_DUCT_POC_PATTERN" USING gist("TAKEOFF_LAYOUT");
