-- 실행: psql -d DDW_AI_DB -f Tools/sql/create_route_context_vector_table.sql
-- 대체 실행: python Tools/ExtractObstacleContextVector.py --config Tools/tools.settings.json create-schema
-- TB_ROUTE_CONTEXT_VECTOR
-- 경로의 시작/종료 PoC 주변 BIM 장애물(기둥/보) 배치를 30차원으로 인코딩한
-- "공간 컨텍스트 벡터". Tools/ExtractObstacleContextVector.py 가 적재한다.
-- 기존 24D 설계를 DDW_AI_DB용 AABB 표면거리/다중 shell 30D로 개선한 v2
--
-- 이 벡터는 시작/종료 좌표 + 주변 장애물 정보만으로 계산되므로(전체 경로 불필요),
-- TB_ROUTE_FEATURE_VECTOR의 env_cost 구간([22:25])과 달리 "신규 쿼리 시점"에도
-- 동일한 방식으로 계산할 수 있다. TopKSearchStandalone.cs 의 하이브리드 재정렬
-- 4번째 항목(ctxScore)에서 이 성질을 활용한다.
--
-- 주의(실측 근거, RoutingAI/docs/TopK_ContextAware_Plan_v2.md):
--   이 벡터를 1차 pgvector ANN 후보추출(FEATURE_VECTOR 검색) 자체에 섞으면
--   오히려 정확도가 떨어진다(기존 30D의 env_cost와 정보 중복). 반드시 이미
--   후보군이 좁혀진 "재정렬/페어링" 단계의 별도 가중치 항목으로만 사용할 것.

-- =============================================================================
-- 실행 방법
--   psql -d DDW_AI_DB -f Tools/sql/create_route_context_vector_table.sql
-- 또는
--   python Tools/ExtractObstacleContextVector.py --config Tools/tools.settings.json create-schema
--
-- 전체 흐름
--   pgvector 확장 -> Context table 생성/기존 24D migration -> 복합 PK 적용
--   -> scope/provenance 컬럼 보강 -> project/revision 검색 index와 HNSW vector index 생성
--
-- 주요 컬럼
--   CONTEXT_VECTOR          : 시작 13D + 종점 13D + Tier3 4D, L2 정규화된 총 30D
--   PROJECT_SCOPE_KEY       : business/source project 식별자
--   MODEL_REVISION_KEY      : 불변 source snapshot revision
--   SOURCE_SNAPSHOT_HASH    : 실제 인코딩 장애물 집합 hash
--   SCOPE_RESOLUTION_STATUS : STRICT_COMMON_KEY 또는 fallback 진단 상태
--   ENCODER_*               : 저장/query encoder 계약과 재현 정보
--   BUILD_RUN_ID            : 한 번의 일괄 생성 row를 묶는 UUID
-- 복합 PK를 사용하므로 같은 ROUTE_PATH_GUID의 global/strict/과거 revision이 공존할 수 있다.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS "TB_ROUTE_CONTEXT_VECTOR" (
    "ROUTE_PATH_GUID"  text NOT NULL,
    "CONTEXT_VECTOR"   vector(30) NOT NULL,
    "START_META_JSON"  jsonb,   -- 0~500 / 500~1000mm shell count + AABB surface distance
    "END_META_JSON"    jsonb,   -- 위와 동일 구조, END PoC 기준
    "TIER3_META_JSON"  jsonb,   -- {"level_change":n, "column_grid_cells":n, "sampled_grid_cells":n, ...}
    "SCOPE_KIND"       text NOT NULL DEFAULT 'GLOBAL_SPATIAL_ALL_BAYS',
    "SCOPE_VALUE"      text NOT NULL DEFAULT '',
    "PROJECT_SCOPE_KEY" text NOT NULL DEFAULT '',
    "MODEL_REVISION_KEY" text NOT NULL DEFAULT '',
    "SOURCE_SNAPSHOT_HASH" text,
    "SCOPE_RESOLUTION_STATUS" text NOT NULL DEFAULT 'GLOBAL_FALLBACK_NO_COMMON_KEY',
    "SOURCE_OBSTACLE_COUNT" integer,
    "SCOPE_DIAGNOSTIC_JSON" jsonb,
    "BUILD_RUN_ID" uuid,
    "ENCODER_VERSION"  text NOT NULL DEFAULT 'topkgen-v3',
    "ENCODER_CONFIG_JSON" jsonb,
    "ENCODER_CONFIG_HASH" text,
    "ENCODED_AT"       timestamptz DEFAULT now()
    , PRIMARY KEY ("PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY", "ROUTE_PATH_GUID")
);

-- v1(vector(24))은 파생 데이터이므로 v2 스키마로 올릴 때 비우고 차원을 변경한다.
-- 원본 경로/장애물 데이터는 변경하지 않으며 빌더가 바로 전량 재생성한다.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = 'TB_ROUTE_CONTEXT_VECTOR'
          AND a.attname = 'CONTEXT_VECTOR'
          AND format_type(a.atttypid, a.atttypmod) <> 'vector(30)'
    ) THEN
        TRUNCATE TABLE "TB_ROUTE_CONTEXT_VECTOR";
        ALTER TABLE "TB_ROUTE_CONTEXT_VECTOR"
            ALTER COLUMN "CONTEXT_VECTOR" TYPE vector(30);
    END IF;
END $$;

ALTER TABLE "TB_ROUTE_CONTEXT_VECTOR"
    ALTER COLUMN "ENCODER_VERSION" SET DEFAULT 'topkgen-v3';

ALTER TABLE "TB_ROUTE_CONTEXT_VECTOR"
    ADD COLUMN IF NOT EXISTS "SCOPE_KIND" text NOT NULL DEFAULT 'GLOBAL_SPATIAL_ALL_BAYS',
    ADD COLUMN IF NOT EXISTS "SCOPE_VALUE" text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS "PROJECT_SCOPE_KEY" text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS "MODEL_REVISION_KEY" text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS "SOURCE_SNAPSHOT_HASH" text,
    ADD COLUMN IF NOT EXISTS "SCOPE_RESOLUTION_STATUS" text NOT NULL DEFAULT 'GLOBAL_FALLBACK_NO_COMMON_KEY',
    ADD COLUMN IF NOT EXISTS "SOURCE_OBSTACLE_COUNT" integer,
    ADD COLUMN IF NOT EXISTS "SCOPE_DIAGNOSTIC_JSON" jsonb,
    ADD COLUMN IF NOT EXISTS "BUILD_RUN_ID" uuid,
    ADD COLUMN IF NOT EXISTS "ENCODER_CONFIG_JSON" jsonb,
    ADD COLUMN IF NOT EXISTS "ENCODER_CONFIG_HASH" text;

-- v3: global fallback and one or more strict model revisions must coexist.
DO $$
DECLARE pk_name text;
DECLARE pk_columns text[];
BEGIN
    SELECT c.conname,
           array_agg(a.attname ORDER BY u.ordinality)
      INTO pk_name, pk_columns
      FROM pg_constraint c
      CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY u(attnum, ordinality)
      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = u.attnum
     WHERE c.conrelid = '"TB_ROUTE_CONTEXT_VECTOR"'::regclass AND c.contype = 'p'
     GROUP BY c.conname;
    IF pk_name IS NOT NULL AND pk_columns <> ARRAY['PROJECT_SCOPE_KEY','MODEL_REVISION_KEY','ROUTE_PATH_GUID'] THEN
        EXECUTE format('ALTER TABLE "TB_ROUTE_CONTEXT_VECTOR" DROP CONSTRAINT %I', pk_name);
        pk_name := NULL;
    END IF;
    IF pk_name IS NULL THEN
        ALTER TABLE "TB_ROUTE_CONTEXT_VECTOR"
            ADD CONSTRAINT "PK_TB_ROUTE_CONTEXT_VECTOR_SCOPE"
            PRIMARY KEY ("PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY", "ROUTE_PATH_GUID");
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS "IX_TRCV_SCOPE"
    ON "TB_ROUTE_CONTEXT_VECTOR" ("SCOPE_KIND", "SCOPE_VALUE");
CREATE INDEX IF NOT EXISTS "IX_TRCV_PROJECT_REVISION"
    ON "TB_ROUTE_CONTEXT_VECTOR" ("PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY", "ROUTE_PATH_GUID");

-- Remove the legacy HNSW index name before creating the v2 canonical index.
-- Keeping both indexes duplicates storage and makes every vector write update
-- two equivalent ANN indexes.
DROP INDEX IF EXISTS "IDX_CTX_VECTOR_HNSW";

CREATE INDEX IF NOT EXISTS "IX_TRCV_CONTEXT_VECTOR_HNSW"
    ON "TB_ROUTE_CONTEXT_VECTOR"
    USING hnsw ("CONTEXT_VECTOR" vector_cosine_ops);
