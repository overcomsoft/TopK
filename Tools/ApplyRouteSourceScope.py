"""
라우팅 원본 테이블에 명시적인 project/revision scope를 부여하는 도구.

실행 방법(PowerShell)
---------------------
1. 변경 없이 대상 건수와 deterministic snapshot 확인
   python Tools/ApplyRouteSourceScope.py --config Tools/tools.settings.json plan `
     --project-scope-key DB:DDW_AI_DB
2. 전체 DB를 하나의 source scope로 명시 적용
   python Tools/ApplyRouteSourceScope.py --config Tools/tools.settings.json apply `
     --project-scope-key DB:DDW_AI_DB --confirm-full-database-scope
3. 현재 scope/manifest 상태 조회
   python Tools/ApplyRouteSourceScope.py --config Tools/tools.settings.json status

전체 흐름도
-----------
  [Feature 정렬 직렬화] --+
  [Route 정렬 직렬화]   +--> [SHA-256 source snapshot] --> [plan 출력]
  [구조 장애물 직렬화] --+                               |
                                                          | apply + 명시적 확인
                                                          v
  [세 원본 테이블 project/revision 갱신] --> [Manifest BUILDING upsert]

중요 안전장치
-------------
- BAY, equipment, MODEL_TEMPLATE_ID로 business project를 추정하지 않는다.
- 전체 DB scope 적용은 ``--confirm-full-database-scope`` 없이는 거부한다.
- 각 쿼리 결과를 고정 정렬해 DB 반환 순서와 무관한 동일 hash를 만든다.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import tool_config


SOURCE_TABLES = (
    "TB_ROUTE_FEATURE_VECTOR",
    "TB_ROUTE_PATH",
    "TB_BIM_OBSTACLE",
)
# Context 인코더가 실제 사용하는 기둥/보 유형. 전체 BIM 건수와 구분해 hash/manifest에 사용한다.
STRUCTURAL_TYPES = (
    "COLUMN_ARCHITECTURE", "COLUMN_STRUCTURE", "BEAM_ARCHITECTURE", "BEAM_STRUCTURE",
)


def open_connection(conninfo: str):
    """psycopg2 연결을 열고 연결 실패를 사용자가 이해할 수 있는 메시지로 변환한다."""
    try:
        import psycopg2
        return psycopg2.connect(conninfo)
    except Exception as ex:
        raise SystemExit(f"DB connection failed: {ex}") from ex


def create_schema(conn) -> None:
    """source scope 컬럼과 manifest/audit/membership migration을 멱등 적용한다."""
    sql_dir = Path(__file__).resolve().parent / "sql"
    with conn.cursor() as cur:
        cur.execute((sql_dir / "create_route_source_scope_columns.sql").read_text(encoding="utf-8"))
        cur.execute((sql_dir / "create_route_source_scope_manifest.sql").read_text(encoding="utf-8"))
    conn.commit()


def _hash_query(conn, digest, label: str, sql: str, params=()) -> int:
    """server-side cursor로 정렬된 대량 행을 순회하며 digest에 안정적으로 누적한다.

    label은 테이블 경계를 hash 입력에 포함해 서로 다른 원본 조합의 우연한 충돌을 막고,
    count는 Manifest에 기록할 실제 source 행 수로 반환한다.
    """
    count = 0
    with conn.cursor(name=f"scope_hash_{label.lower()}") as cur:
        cur.itersize = 2000
        cur.execute(sql, params)
        for (payload,) in cur:
            digest.update(label.encode("utf-8"))
            digest.update(b"\x1f")
            digest.update(str(payload).encode("utf-8"))
            digest.update(b"\n")
            count += 1
    return count


def compute_snapshot(conn) -> tuple[str, dict[str, int]]:
    """Feature, Route, 구조 장애물의 내용 기반 SHA-256과 테이블별 건수를 계산한다."""
    digest = hashlib.sha256()
    counts = {}
    counts["feature"] = _hash_query(
        conn, digest, "FEATURE",
        '''SELECT (to_jsonb(t) - 'PROJECT_SCOPE_KEY' - 'MODEL_REVISION_KEY')::text
           FROM "TB_ROUTE_FEATURE_VECTOR" t ORDER BY TRIM("ROUTE_PATH_GUID")''',
    )
    counts["route"] = _hash_query(
        conn, digest, "ROUTE",
        '''SELECT (to_jsonb(t) - 'PROJECT_SCOPE_KEY' - 'MODEL_REVISION_KEY')::text
           FROM "TB_ROUTE_PATH" t ORDER BY TRIM("ROUTE_PATH_GUID")''',
    )
    counts["obstacle"] = _hash_query(
        conn, digest, "OBSTACLE",
        '''SELECT (to_jsonb(t) - 'PROJECT_SCOPE_KEY' - 'MODEL_REVISION_KEY')::text
           FROM "TB_BIM_OBSTACLE" t
           WHERE "DDWORKS_TYPE" = ANY(%s)
           ORDER BY COALESCE("INSTANCE_ID", ''), "DDWORKS_TYPE",
                    "AABB_MINX", "AABB_MINY", "AABB_MINZ",
                    "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"''',
        (list(STRUCTURAL_TYPES),),
    )
    return digest.hexdigest(), counts


def apply_scope(conn, project: str, revision: str, snapshot: str, counts: dict[str, int]) -> None:
    """세 원본 테이블에 같은 scope를 적용하고 Manifest를 BUILDING 상태로 upsert한다."""
    with conn.cursor() as cur:
        for table in SOURCE_TABLES:
            cur.execute(
                f'''UPDATE "{table}" SET "PROJECT_SCOPE_KEY"=%s, "MODEL_REVISION_KEY"=%s''',
                (project, revision),
            )
            print(f"Assigned {table}: {cur.rowcount}")
        cur.execute(
            '''INSERT INTO "TB_ROUTE_SOURCE_SCOPE_MANIFEST"
               ("PROJECT_SCOPE_KEY","MODEL_REVISION_KEY","SOURCE_SNAPSHOT_HASH",
                "FEATURE_ROW_COUNT","ROUTE_ROW_COUNT","OBSTACLE_ROW_COUNT","SOURCE_DATABASE","STATUS")
               VALUES (%s,%s,%s,%s,%s,%s,current_database(),'BUILDING')
               ON CONFLICT ("PROJECT_SCOPE_KEY","MODEL_REVISION_KEY") DO UPDATE SET
                 "SOURCE_SNAPSHOT_HASH"=EXCLUDED."SOURCE_SNAPSHOT_HASH",
                 "FEATURE_ROW_COUNT"=EXCLUDED."FEATURE_ROW_COUNT",
                 "ROUTE_ROW_COUNT"=EXCLUDED."ROUTE_ROW_COUNT",
                 "OBSTACLE_ROW_COUNT"=EXCLUDED."OBSTACLE_ROW_COUNT",
                 "SOURCE_DATABASE"=EXCLUDED."SOURCE_DATABASE", "STATUS"='BUILDING',
                 "APPLIED_AT"=now(), "READY_AT"=NULL''',
            (project, revision, snapshot, counts["feature"], counts["route"], counts["obstacle"]),
        )
    conn.commit()


def print_status(conn) -> None:
    """원본 테이블별 scope coverage와 최근 Manifest 목록을 출력한다."""
    with conn.cursor() as cur:
        for table in SOURCE_TABLES:
            cur.execute(
                f'''SELECT COUNT(*), COUNT(*) FILTER
                     (WHERE "PROJECT_SCOPE_KEY" IS NOT NULL AND "MODEL_REVISION_KEY" IS NOT NULL)
                     FROM "{table}"'''
            )
            total, scoped = cur.fetchone()
            print(f"{table}: scoped={scoped}/{total}")
        cur.execute(
            '''SELECT "PROJECT_SCOPE_KEY","MODEL_REVISION_KEY","SOURCE_SNAPSHOT_HASH",
                      "FEATURE_ROW_COUNT","ROUTE_ROW_COUNT","OBSTACLE_ROW_COUNT","APPLIED_AT"
               FROM "TB_ROUTE_SOURCE_SCOPE_MANIFEST" ORDER BY "APPLIED_AT" DESC LIMIT 10'''
        )
        for row in cur.fetchall():
            print(
                f"Manifest: project={row[0]}, revision={row[1]}, snapshot={row[2]}, "
                f"feature={row[3]}, route={row[4]}, obstacle={row[5]}, applied={row[6]}"
            )


def main() -> int:
    """plan/apply/status 명령을 처리하며 apply는 REPEATABLE READ에서 실행한다."""
    parser = argparse.ArgumentParser(description="Apply explicit full-database routing source scope")
    tool_config.add_common_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create-schema")
    sub.add_parser("status")
    for name in ("plan", "apply"):
        command = sub.add_parser(name)
        command.add_argument("--project-scope-key", required=True)
        command.add_argument("--model-revision-key", default="")
        if name == "apply":
            command.add_argument("--confirm-full-database-scope", action="store_true")
    args = parser.parse_args()
    runtime = tool_config.resolve_runtime(args)
    tool_config.print_runtime(runtime)
    with open_connection(runtime.conninfo) as conn:
        create_schema(conn)
        if args.command == "create-schema":
            print("Source scope schema ready.")
            return 0
        if args.command == "status":
            print_status(conn)
            return 0
        conn.set_session(isolation_level="REPEATABLE READ")
        project = args.project_scope_key.strip()
        if not project:
            raise SystemExit("--project-scope-key must not be empty")
        snapshot, counts = compute_snapshot(conn)
        revision = args.model_revision_key.strip() or f"snapshot:{snapshot}"
        print(f"Project scope : {project}")
        print(f"Model revision: {revision}")
        print(f"Source SHA-256: {snapshot}")
        print(f"Rows           : {counts}")
        if args.command == "plan":
            print("PLAN ONLY: no source rows changed.")
            return 0
        if not args.confirm_full_database_scope:
            raise SystemExit("apply requires --confirm-full-database-scope")
        apply_scope(conn, project, revision, snapshot, counts)
        print("Full-database source scope applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
