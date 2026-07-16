-- UtilityPipeGroup Top-K schema rollback.
-- 주의: 그룹 파생 데이터가 삭제된다. 사용자의 명시적 rollback 승인 후에만 실행한다.
-- 원본 TB_ROUTE_PATH/FEATURE/CONTEXT/상세경로 데이터는 삭제하지 않는다.

DROP TABLE IF EXISTS "TB_ROUTE_UTILITY_GROUP_MEMBER";
DROP TABLE IF EXISTS "TB_ROUTE_UTILITY_GROUP_VECTOR";
