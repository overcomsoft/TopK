-- 배관 꺾임특징점(Bend Feature Point) 스키마 rollback.
-- 주의: 파생 데이터가 삭제된다. 사용자의 명시적 rollback 승인 후에만 실행한다.
-- 원본 TB_ROUTE_PATH/TB_ROUTE_SEGMENTS/TB_ROUTE_SEGMENT_DETAIL 데이터는 삭제하지 않는다.

DROP TABLE IF EXISTS "TB_ROUTE_BEND_FEATURE_PATTERN";
DROP TABLE IF EXISTS "TB_ROUTE_BEND_FEATURE_POINT";
