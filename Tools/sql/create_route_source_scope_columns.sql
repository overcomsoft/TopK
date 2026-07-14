-- Additive source-scope contract for route/context extraction.
-- Columns intentionally remain nullable: current legacy data has no reliable common project/revision key.
-- Upstream ETL must populate the same values in feature, path, and obstacle rows before strict scoped extraction.

ALTER TABLE "TB_ROUTE_FEATURE_VECTOR"
    ADD COLUMN IF NOT EXISTS "PROJECT_SCOPE_KEY" text,
    ADD COLUMN IF NOT EXISTS "MODEL_REVISION_KEY" text;

ALTER TABLE "TB_ROUTE_PATH"
    ADD COLUMN IF NOT EXISTS "PROJECT_SCOPE_KEY" text,
    ADD COLUMN IF NOT EXISTS "MODEL_REVISION_KEY" text;

ALTER TABLE "TB_BIM_OBSTACLE"
    ADD COLUMN IF NOT EXISTS "PROJECT_SCOPE_KEY" text,
    ADD COLUMN IF NOT EXISTS "MODEL_REVISION_KEY" text;

CREATE INDEX IF NOT EXISTS "IX_TRFV_PROJECT_REVISION"
    ON "TB_ROUTE_FEATURE_VECTOR" ("PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY");
CREATE INDEX IF NOT EXISTS "IX_TRP_PROJECT_REVISION"
    ON "TB_ROUTE_PATH" ("PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY");
CREATE INDEX IF NOT EXISTS "IX_TBO_PROJECT_REVISION_TYPE"
    ON "TB_BIM_OBSTACLE" ("PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY", "DDWORKS_TYPE");

COMMENT ON COLUMN "TB_ROUTE_FEATURE_VECTOR"."PROJECT_SCOPE_KEY" IS
    'Common project/model scope key populated by upstream ETL; NULL means unresolved legacy data';
COMMENT ON COLUMN "TB_ROUTE_FEATURE_VECTOR"."MODEL_REVISION_KEY" IS
    'Common immutable model revision key populated by upstream ETL; NULL means unresolved legacy data';
