-- UtilityPipeGroup Top-K additive schema migration
--
-- 실행:
--   python Tools/MigrateUtilityPipeGroupSchema.py apply --config Tools/tools.settings.json
-- 검증:
--   python Tools/MigrateUtilityPipeGroupSchema.py verify --config Tools/tools.settings.json
-- 직접 실행:
--   psql -d DDW_AI_DB -f Tools/sql/create_route_utility_group_vector_tables.sql
--
-- 이 migration은 신규 테이블/인덱스/제약조건만 생성한다.
-- 기존 Route, Feature, Context, 상세경로 테이블과 데이터는 변경하지 않는다.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS "TB_ROUTE_UTILITY_GROUP_VECTOR" (
    "GROUP_VECTOR_ID" text PRIMARY KEY,
    "PROJECT_SCOPE_KEY" text NOT NULL,
    "MODEL_REVISION_KEY" text NOT NULL,
    "PROCESS_NAME" text NOT NULL DEFAULT '',
    "EQUIPMENT_INSTANCE_KEY" text NOT NULL,
    "EQUIPMENT_NAME" text NOT NULL DEFAULT '',
    "EQUIPMENT_FAMILY_KEY" text NOT NULL DEFAULT '',
    "UTILITY_GROUP" text NOT NULL,
    "UTILITY" text NOT NULL,
    "MEMBER_COUNT" integer NOT NULL,
    "SIZE_SIGNATURE" jsonb NOT NULL DEFAULT '{}'::jsonb,
    "MEMBER_GUIDS" jsonb NOT NULL,
    "FEATURE_CENTROID" vector(30) NOT NULL,
    "CONTEXT_CENTROID" vector(30),
    "ARRANGEMENT_VECTOR_JSON" jsonb NOT NULL DEFAULT '{}'::jsonb,
    "START_CENTROID_X" double precision NOT NULL,
    "START_CENTROID_Y" double precision NOT NULL,
    "START_CENTROID_Z" double precision NOT NULL,
    "END_CENTROID_X" double precision NOT NULL,
    "END_CENTROID_Y" double precision NOT NULL,
    "END_CENTROID_Z" double precision NOT NULL,
    "AABB_MINX" double precision NOT NULL,
    "AABB_MINY" double precision NOT NULL,
    "AABB_MINZ" double precision NOT NULL,
    "AABB_MAXX" double precision NOT NULL,
    "AABB_MAXY" double precision NOT NULL,
    "AABB_MAXZ" double precision NOT NULL,
    "FEATURE_COVERAGE" double precision NOT NULL DEFAULT 0.0,
    "CONTEXT_COVERAGE" double precision NOT NULL DEFAULT 0.0,
    "SOURCE_HASH" text NOT NULL,
    "BUILD_RUN_ID" uuid NOT NULL,
    "ENCODER_VERSION" text NOT NULL DEFAULT 'utility-pipe-group-v1',
    "ENCODER_CONFIG_JSON" jsonb NOT NULL DEFAULT '{}'::jsonb,
    "ENCODER_CONFIG_HASH" text NOT NULL,
    "STATUS" text NOT NULL DEFAULT 'BUILDING',
    "CREATED_AT" timestamptz NOT NULL DEFAULT now(),
    "UPDATED_AT" timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT "FK_TRUGV_SOURCE_SCOPE"
        FOREIGN KEY ("PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY")
        REFERENCES "TB_ROUTE_SOURCE_SCOPE_MANIFEST" ("PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY")
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT "UX_TRUGV_SCOPE_EQUIPMENT_UTILITY"
        UNIQUE ("PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY", "PROCESS_NAME",
                "EQUIPMENT_INSTANCE_KEY", "UTILITY_GROUP", "UTILITY"),
    CONSTRAINT "CK_TRUGV_STATUS"
        CHECK ("STATUS" IN ('BUILDING','READY','FAILED','STALE')),
    CONSTRAINT "CK_TRUGV_MEMBER_COUNT"
        CHECK ("MEMBER_COUNT" >= 2),
    CONSTRAINT "CK_TRUGV_MEMBER_GUIDS_ARRAY"
        CHECK (jsonb_typeof("MEMBER_GUIDS") = 'array'),
    CONSTRAINT "CK_TRUGV_MEMBER_GUID_COUNT"
        CHECK (jsonb_array_length("MEMBER_GUIDS") = "MEMBER_COUNT"),
    CONSTRAINT "CK_TRUGV_SIZE_SIGNATURE_OBJECT"
        CHECK (jsonb_typeof("SIZE_SIGNATURE") = 'object'),
    CONSTRAINT "CK_TRUGV_ARRANGEMENT_OBJECT"
        CHECK (jsonb_typeof("ARRANGEMENT_VECTOR_JSON") = 'object'),
    CONSTRAINT "CK_TRUGV_ENCODER_CONFIG_OBJECT"
        CHECK (jsonb_typeof("ENCODER_CONFIG_JSON") = 'object'),
    CONSTRAINT "CK_TRUGV_FEATURE_COVERAGE"
        CHECK ("FEATURE_COVERAGE" >= 0.0 AND "FEATURE_COVERAGE" <= 1.0),
    CONSTRAINT "CK_TRUGV_CONTEXT_COVERAGE"
        CHECK ("CONTEXT_COVERAGE" >= 0.0 AND "CONTEXT_COVERAGE" <= 1.0),
    CONSTRAINT "CK_TRUGV_AABB_ORDER"
        CHECK ("AABB_MINX" <= "AABB_MAXX"
           AND "AABB_MINY" <= "AABB_MAXY"
           AND "AABB_MINZ" <= "AABB_MAXZ")
);

CREATE TABLE IF NOT EXISTS "TB_ROUTE_UTILITY_GROUP_MEMBER" (
    "GROUP_VECTOR_ID" text NOT NULL,
    "ROUTE_PATH_GUID" text NOT NULL,
    "MEMBER_ORDER" integer NOT NULL,
    "UTILITY" text NOT NULL,
    "SIZE" text NOT NULL DEFAULT '',
    "START_X" double precision NOT NULL,
    "START_Y" double precision NOT NULL,
    "START_Z" double precision NOT NULL,
    "END_X" double precision NOT NULL,
    "END_Y" double precision NOT NULL,
    "END_Z" double precision NOT NULL,
    "DIRECTION_PATTERN" text NOT NULL DEFAULT '',
    "TOTAL_LENGTH_MM" double precision NOT NULL DEFAULT 0.0,
    "STEP_COUNT" integer NOT NULL DEFAULT 0,
    "FEATURE_VECTOR_BUILD_RUN_ID" text NOT NULL DEFAULT '',
    "CONTEXT_VECTOR_BUILD_RUN_ID" text,
    "CREATED_AT" timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT "PK_TRUGM"
        PRIMARY KEY ("GROUP_VECTOR_ID", "ROUTE_PATH_GUID"),
    CONSTRAINT "FK_TRUGM_GROUP"
        FOREIGN KEY ("GROUP_VECTOR_ID")
        REFERENCES "TB_ROUTE_UTILITY_GROUP_VECTOR" ("GROUP_VECTOR_ID")
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT "UX_TRUGM_ORDER"
        UNIQUE ("GROUP_VECTOR_ID", "MEMBER_ORDER"),
    CONSTRAINT "CK_TRUGM_MEMBER_ORDER"
        CHECK ("MEMBER_ORDER" >= 0),
    CONSTRAINT "CK_TRUGM_LENGTH"
        CHECK ("TOTAL_LENGTH_MM" >= 0.0),
    CONSTRAINT "CK_TRUGM_STEP_COUNT"
        CHECK ("STEP_COUNT" >= 0)
);

-- 후보 수집 필수 필터: Utility Group + Utility. 장비 인스턴스는 Query 식별/자기 제외에 사용한다.
CREATE INDEX IF NOT EXISTS "IX_TRUGV_CANDIDATE_FILTER"
    ON "TB_ROUTE_UTILITY_GROUP_VECTOR"
       ("UTILITY_GROUP", "UTILITY", "STATUS", "PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY");

CREATE INDEX IF NOT EXISTS "IX_TRUGV_PROCESS_CANDIDATE"
    ON "TB_ROUTE_UTILITY_GROUP_VECTOR"
       ("PROCESS_NAME", "UTILITY_GROUP", "UTILITY", "STATUS");

CREATE INDEX IF NOT EXISTS "IX_TRUGV_EQUIPMENT_FAMILY"
    ON "TB_ROUTE_UTILITY_GROUP_VECTOR"
       ("EQUIPMENT_FAMILY_KEY", "UTILITY_GROUP", "UTILITY", "STATUS");

CREATE INDEX IF NOT EXISTS "IX_TRUGV_SOURCE_HASH"
    ON "TB_ROUTE_UTILITY_GROUP_VECTOR"
       ("PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY", "SOURCE_HASH");

-- Context는 ANN 후보 수집에 섞지 않고 정밀 재정렬에서만 사용한다.
CREATE INDEX IF NOT EXISTS "IX_TRUGV_FEATURE_CENTROID_HNSW"
    ON "TB_ROUTE_UTILITY_GROUP_VECTOR"
    USING hnsw ("FEATURE_CENTROID" vector_cosine_ops);

CREATE INDEX IF NOT EXISTS "IX_TRUGV_SIZE_SIGNATURE_GIN"
    ON "TB_ROUTE_UTILITY_GROUP_VECTOR" USING gin ("SIZE_SIGNATURE");

CREATE INDEX IF NOT EXISTS "IX_TRUGM_ROUTE_GUID"
    ON "TB_ROUTE_UTILITY_GROUP_MEMBER" ("ROUTE_PATH_GUID");

CREATE INDEX IF NOT EXISTS "IX_TRUGM_GROUP_SIZE"
    ON "TB_ROUTE_UTILITY_GROUP_MEMBER" ("GROUP_VECTOR_ID", "SIZE", "MEMBER_ORDER");

COMMENT ON TABLE "TB_ROUTE_UTILITY_GROUP_VECTOR" IS
    'UtilityPipeGroup Top-K group header/vector. Query identity is equipment+utility group+utility; ANN candidates are collected by utility group+utility.';
COMMENT ON COLUMN "TB_ROUTE_UTILITY_GROUP_VECTOR"."FEATURE_CENTROID" IS
    'L2-normalized mean of member TB_ROUTE_FEATURE_VECTOR 30D vectors. Used only for first-stage ANN candidate retrieval.';
COMMENT ON COLUMN "TB_ROUTE_UTILITY_GROUP_VECTOR"."CONTEXT_CENTROID" IS
    'L2-normalized mean of compatible member Context vectors. Used only for exact reranking, never ANN retrieval.';
COMMENT ON COLUMN "TB_ROUTE_UTILITY_GROUP_VECTOR"."EQUIPMENT_INSTANCE_KEY" IS
    'Normalized EQUIPMENT_TAG used for group identity and self exclusion, not a mandatory candidate filter.';
COMMENT ON TABLE "TB_ROUTE_UTILITY_GROUP_MEMBER" IS
    'Deterministically ordered route members of one UtilityPipeGroup. Original individual vectors remain in their source tables.';
