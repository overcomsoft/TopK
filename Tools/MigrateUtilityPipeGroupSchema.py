#!/usr/bin/env python3
"""UtilityPipeGroup Top-K schema apply/verify CLI.

실행:
  python Tools/MigrateUtilityPipeGroupSchema.py apply --config Tools/tools.settings.json
  python Tools/MigrateUtilityPipeGroupSchema.py verify --config Tools/tools.settings.json

apply는 additive create SQL만 실행한다. rollback SQL은 안전상 이 도구에서 자동 실행하지 않는다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    import psycopg2
except ModuleNotFoundError:
    psycopg2 = None

import tool_config


ROOT = Path(__file__).resolve().parents[1]
CREATE_SQL = ROOT / "Tools" / "sql" / "create_route_utility_group_vector_tables.sql"

EXPECTED_COLUMNS = {
    "TB_ROUTE_UTILITY_GROUP_VECTOR": {
        "GROUP_VECTOR_ID": "text",
        "PROJECT_SCOPE_KEY": "text",
        "MODEL_REVISION_KEY": "text",
        "EQUIPMENT_INSTANCE_KEY": "text",
        "UTILITY_GROUP": "text",
        "UTILITY": "text",
        "MEMBER_COUNT": "integer",
        "FEATURE_CENTROID": "vector(30)",
        "CONTEXT_CENTROID": "vector(30)",
        "STATUS": "text",
    },
    "TB_ROUTE_UTILITY_GROUP_MEMBER": {
        "GROUP_VECTOR_ID": "text",
        "ROUTE_PATH_GUID": "text",
        "MEMBER_ORDER": "integer",
        "UTILITY": "text",
        "SIZE": "text",
    },
}

EXPECTED_INDEXES = {
    "IX_TRUGV_CANDIDATE_FILTER",
    "IX_TRUGV_PROCESS_CANDIDATE",
    "IX_TRUGV_EQUIPMENT_FAMILY",
    "IX_TRUGV_SOURCE_HASH",
    "IX_TRUGV_FEATURE_CENTROID_HNSW",
    "IX_TRUGV_SIZE_SIGNATURE_GIN",
    "IX_TRUGM_ROUTE_GUID",
    "IX_TRUGM_GROUP_SIZE",
}

EXPECTED_CONSTRAINTS = {
    "TB_ROUTE_UTILITY_GROUP_VECTOR": {
        "FK_TRUGV_SOURCE_SCOPE",
        "UX_TRUGV_SCOPE_EQUIPMENT_UTILITY",
        "CK_TRUGV_STATUS",
        "CK_TRUGV_MEMBER_COUNT",
        "CK_TRUGV_MEMBER_GUID_COUNT",
        "CK_TRUGV_FEATURE_COVERAGE",
        "CK_TRUGV_CONTEXT_COVERAGE",
        "CK_TRUGV_AABB_ORDER",
    },
    "TB_ROUTE_UTILITY_GROUP_MEMBER": {
        "PK_TRUGM",
        "FK_TRUGM_GROUP",
        "UX_TRUGM_ORDER",
        "CK_TRUGM_MEMBER_ORDER",
        "CK_TRUGM_LENGTH",
        "CK_TRUGM_STEP_COUNT",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UtilityPipeGroup schema migration")
    parser.add_argument("action", choices=("apply", "verify"))
    tool_config.add_common_args(parser)
    return parser.parse_args()


def apply_schema(conn) -> None:
    sql_text = CREATE_SQL.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql_text)
    conn.commit()


def inspect_schema(conn) -> dict:
    report: dict = {
        "tables": {},
        "indexes": [],
        "constraints": {},
        "row_counts": {},
        "valid": True,
        "errors": [],
    }
    with conn.cursor() as cur:
        for table, expected in EXPECTED_COLUMNS.items():
            cur.execute(
                """SELECT a.attname, format_type(a.atttypid,a.atttypmod)
                   FROM pg_attribute a
                   JOIN pg_class c ON c.oid=a.attrelid
                   JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname=current_schema() AND c.relname=%s
                     AND a.attnum>0 AND NOT a.attisdropped
                   ORDER BY a.attnum""",
                (table,),
            )
            actual = {name.upper(): type_name for name, type_name in cur.fetchall()}
            report["tables"][table] = actual
            for column, expected_type in expected.items():
                if column not in actual:
                    report["errors"].append(f"{table}.{column} missing")
                elif actual[column] != expected_type:
                    report["errors"].append(
                        f"{table}.{column} type={actual[column]} expected={expected_type}"
                    )

        cur.execute(
            """SELECT indexname FROM pg_indexes
               WHERE schemaname=current_schema()
                 AND tablename IN ('TB_ROUTE_UTILITY_GROUP_VECTOR','TB_ROUTE_UTILITY_GROUP_MEMBER')"""
        )
        report["indexes"] = sorted(row[0] for row in cur.fetchall())
        for index_name in sorted(EXPECTED_INDEXES - set(report["indexes"])):
            report["errors"].append(f"index missing: {index_name}")

        for table in EXPECTED_COLUMNS:
            cur.execute(
                """SELECT conname, contype FROM pg_constraint
                   WHERE conrelid=%s::regclass ORDER BY conname""",
                (f'"{table}"',),
            )
            constraints = [
                {"name": name, "type": constraint_type} for name, constraint_type in cur.fetchall()
            ]
            report["constraints"][table] = constraints
            actual_constraints = {item["name"] for item in constraints}
            for constraint_name in sorted(EXPECTED_CONSTRAINTS[table] - actual_constraints):
                report["errors"].append(f"constraint missing: {table}.{constraint_name}")

            cur.execute(f'SELECT count(*) FROM "{table}"')
            report["row_counts"][table] = cur.fetchone()[0]
    report["valid"] = not report["errors"]
    return report


def main() -> int:
    args = parse_args()
    if psycopg2 is None:
        raise SystemExit(
            "psycopg2가 필요합니다. "
            "python -m pip install -r Tools/requirements-utility-pipe-group.txt 를 실행하세요."
        )
    runtime = tool_config.resolve_runtime(args)
    conn = psycopg2.connect(runtime.conninfo)
    try:
        if args.action == "apply":
            apply_schema(conn)
            print(f"[applied] {CREATE_SQL}")
        report = inspect_schema(conn)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["valid"] else 2
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
