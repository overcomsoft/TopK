-- TB_DUCT_EQUIPMENT_TAKEOFF_PATTERN
-- TB_DUCT의 취출구(TAKEOFF_POC_ID_LIST)가 TB_ROUTE_PATH.TARGET_GUID와 정확히 일치하는
-- 것만 골라 장비(EQUIPMENT_TAG)에 귀속시킨 뒤, 덕트별 면(Face) 분포 시그니처를
-- (EQUIPMENT_TAG, UTILITY)로 집계한 테이블. Tools/AnalyzeDuctPocPattern.py가 적재한다.
--
-- 주의(2026-07-15 실측 근거): TAKEOFF_POC_ID_LIST가 TB_ROUTE_PATH.TARGET_GUID와
-- 정확히 일치하는 경우에만 장비를 확실히 알 수 있다 — 전체 취출구 1,193건 중 105건
-- (8.8%)만 해당. 100mm 거리 허용으로 확장하는 방안도 검토했으나 108건으로 겨우 3건만
-- 늘어, 나머지 취출구는 좌표 오차 문제가 아니라 중간 레터럴/부속 배관 자체가 이 DB에
-- 적재되지 않은 데이터 공백으로 판단해 정확 일치만 채택했다. 즉 이 테이블은
-- "확실히 장비까지 추적된" 취출구만 다루며, TB_DUCT_POC_PATTERN(덕트별 전체 취출구
-- 면 분포)보다 좁은 부분집합이다.

CREATE TABLE IF NOT EXISTS "TB_DUCT_EQUIPMENT_TAKEOFF_PATTERN" (
    "ID"                 bigserial PRIMARY KEY,
    "EQUIPMENT_TAG"      text NOT NULL,
    "UTILITY"            text NOT NULL,
    "PATTERN_SIGNATURE"  text NOT NULL,
    -- 면(Face)별 취출구 개수를 "면:개수" 형태로 면 이름 알파벳 순 정렬해 콤마로 이어붙인
    -- 정규 문자열. 예: "TOP:3", "LEFT:1,TOP:2". 같은 조합이면 항상 같은 문자열이 되므로
    -- GROUP BY 키로 바로 쓸 수 있다.
    "N_DUCTS"            integer NOT NULL,
    "N_TAKEOFFS_TOTAL"   integer NOT NULL,
    "EXAMPLE_DUCT_NAMES" jsonb NOT NULL,
    "ANALYZED_AT"        timestamptz DEFAULT now(),
    UNIQUE ("EQUIPMENT_TAG", "UTILITY", "PATTERN_SIGNATURE")
);

CREATE INDEX IF NOT EXISTS "IX_TDETP_KEY"
ON "TB_DUCT_EQUIPMENT_TAKEOFF_PATTERN" ("EQUIPMENT_TAG", "UTILITY");
