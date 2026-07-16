#!/usr/bin/env python3
r"""UtilityPipeGroup 30D 집계 Vector 생성·검증 CLI.

실행 방법:
  .venv\Scripts\python.exe Tools\BuildUtilityPipeGroupVectors.py create-schema --config Tools\tools.settings.json
  .venv\Scripts\python.exe Tools\BuildUtilityPipeGroupVectors.py build --config Tools\tools.settings.json --scope-mode active
  .venv\Scripts\python.exe Tools\BuildUtilityPipeGroupVectors.py validate --config Tools\tools.settings.json --scope-mode active
  .venv\Scripts\python.exe Tools\BuildUtilityPipeGroupVectors.py status --config Tools\tools.settings.json --scope-mode active

전체 흐름:
  ACTIVE scope 확정 -> Route/Feature/Context/상세점 일괄 조회 -> 그룹화
  -> stable ID와 Source Hash -> 30D centroid/배치 통계 -> 원자적 upsert
  -> 변경 없는 READY 그룹 skip -> 사라진 그룹 STALE -> DB/원본 drift 검증
"""
from __future__ import annotations

import argparse
import json
import math
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor, execute_batch
except ModuleNotFoundError:
    psycopg2 = None
    RealDictCursor = None
    execute_batch = None

import tool_config
from utility_pipe_group_encoder import (
    ENCODER_VERSION,
    VECTOR_DIM,
    build_arrangement,
    canonical_json,
    centroid,
    deterministic_members,
    encoder_config,
    make_group_vector_id,
    normalize_equipment_key,
    normalize_size,
    normalize_text,
    normalized_centroid,
    parse_vector,
    sha256_json,
    source_hash_payload,
    vector_literal,
)


ROOT = Path(__file__).resolve().parents[1]
CREATE_SQL = ROOT / "Tools" / "sql" / "create_route_utility_group_vector_tables.sql"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UtilityPipeGroup Vector Builder")
    parser.add_argument("action", choices=("create-schema", "build", "validate", "status"))
    tool_config.add_common_args(parser)
    parser.add_argument("--scope-mode", choices=("active", "explicit"), default="active")
    parser.add_argument("--project-scope-key", default="")
    parser.add_argument("--model-revision-key", default="")
    parser.add_argument("--min-members", type=int, default=2)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report-out", default=None)
    return parser.parse_args()


def resolve_scope(conn, args: argparse.Namespace) -> tuple[str, str]:
    if args.scope_mode == "explicit":
        project = normalize_text(args.project_scope_key)
        revision = normalize_text(args.model_revision_key)
        if not project or not revision:
            raise ValueError("explicit scope에는 --project-scope-key와 --model-revision-key가 모두 필요합니다.")
        return project, revision
    with conn.cursor() as cur:
        cur.execute(
            '''SELECT "PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY"
                 FROM "TB_ROUTE_SOURCE_SCOPE_MANIFEST" WHERE "STATUS"='ACTIVE'
                 ORDER BY "PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY"'''
        )
        rows = cur.fetchall()
    if len(rows) != 1:
        raise ValueError(f"ACTIVE scope는 정확히 1개여야 합니다. 현재 {len(rows)}개입니다.")
    return normalize_text(rows[0][0]), normalize_text(rows[0][1])


def load_geometry_points(conn, project: str, revision: str) -> dict[str, list[list[float]]]:
    """ACTIVE Route에 연결된 segment 양 끝점을 순서대로 읽어 그룹 AABB 입력으로 사용한다."""
    sql = '''
        SELECT BTRIM(s."ROUTE_PATH_GUID") AS route_path_guid, s."ORDER" AS segment_order,
               s."START_SEGMENT_DETAIL_POSX" AS sx, s."START_SEGMENT_DETAIL_POSY" AS sy,
               s."START_SEGMENT_DETAIL_POSZ" AS sz, s."END_SEGMENT_DETAIL_POSX" AS ex,
               s."END_SEGMENT_DETAIL_POSY" AS ey, s."END_SEGMENT_DETAIL_POSZ" AS ez
          FROM "TB_ROUTE_SEGMENTS" s
          JOIN "TB_ROUTE_PATH" p ON BTRIM(p."ROUTE_PATH_GUID")=BTRIM(s."ROUTE_PATH_GUID")
         WHERE p."PROJECT_SCOPE_KEY"=%s AND p."MODEL_REVISION_KEY"=%s
         ORDER BY BTRIM(s."ROUTE_PATH_GUID"), s."ORDER", s."SEGMENT_GUID"
    '''
    points: dict[str, list[list[float]]] = defaultdict(list)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (project, revision))
        for row in cur.fetchall():
            guid = normalize_text(row["route_path_guid"])
            for keys in (("sx", "sy", "sz"), ("ex", "ey", "ez")):
                values = [row[key] for key in keys]
                if all(value is not None for value in values):
                    point = [float(value) for value in values]
                    if not points[guid] or points[guid][-1] != point:
                        points[guid].append(point)
    return dict(points)


def load_source_members(conn, project: str, revision: str) -> list[dict[str, Any]]:
    """scope가 일치하는 원본 Route와 개별 Feature/Context provenance를 한 번에 로드한다."""
    sql = '''
        SELECT
          BTRIM(p."ROUTE_PATH_GUID") AS route_path_guid,
          COALESCE(BTRIM(p."PROCESS_NAME"),'') AS process_name,
          COALESCE(NULLIF(BTRIM(p."EQUIPMENT_TAG"),''), BTRIM(p."EQUIPMENT_NAME"),'') AS equipment_tag,
          COALESCE(BTRIM(p."EQUIPMENT_NAME"),'') AS equipment_name,
          COALESCE(BTRIM(p."UTILITY_GROUP"),'') AS utility_group,
          COALESCE(NULLIF(BTRIM(p."SOURCE_UTILITY"),''), BTRIM(fv."UTILITY"),'') AS utility,
          COALESCE(NULLIF(BTRIM(p."SOURCE_SIZE"),''), BTRIM(fv."SIZE"),'') AS size,
          fv."START_POSX" AS sx, fv."START_POSY" AS sy, fv."START_POSZ" AS sz,
          fv."END_POSX" AS ex, fv."END_POSY" AS ey, fv."END_POSZ" AS ez,
          COALESCE(BTRIM(fv."DIRECTION_PATTERN"),'') AS direction_pattern,
          COALESCE(fv."TOTAL_LENGTH_MM", p."TOTAL_LENGTH", 0.0) AS total_length_mm,
          COALESCE(fv."STEP_COUNT", 0) AS step_count,
          fv."FEATURE_VECTOR"::text AS feature_vector_text,
          COALESCE(BTRIM(fv."ENCODER_VERSION"),'') AS feature_encoder_version,
          fv."ENCODED_AT"::text AS feature_encoded_at,
          cv."CONTEXT_VECTOR"::text AS context_vector_text,
          COALESCE(BTRIM(cv."ENCODER_VERSION"),'') AS context_encoder_version,
          COALESCE(BTRIM(cv."ENCODER_CONFIG_HASH"),'') AS context_config_hash,
          cv."BUILD_RUN_ID"::text AS context_build_run_id
        FROM "TB_ROUTE_PATH" p
        JOIN "TB_ROUTE_FEATURE_VECTOR" fv
          ON BTRIM(fv."ROUTE_PATH_GUID")=BTRIM(p."ROUTE_PATH_GUID")
         AND fv."PROJECT_SCOPE_KEY"=p."PROJECT_SCOPE_KEY"
         AND fv."MODEL_REVISION_KEY"=p."MODEL_REVISION_KEY"
        LEFT JOIN "TB_ROUTE_CONTEXT_VECTOR" cv
          ON BTRIM(cv."ROUTE_PATH_GUID")=BTRIM(p."ROUTE_PATH_GUID")
         AND cv."PROJECT_SCOPE_KEY"=p."PROJECT_SCOPE_KEY"
         AND cv."MODEL_REVISION_KEY"=p."MODEL_REVISION_KEY"
        WHERE p."PROJECT_SCOPE_KEY"=%s AND p."MODEL_REVISION_KEY"=%s
        ORDER BY BTRIM(p."ROUTE_PATH_GUID")
    '''
    geometry = load_geometry_points(conn, project, revision)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (project, revision))
        for raw in cur.fetchall():
            row = dict(raw)
            guid = normalize_text(row["route_path_guid"])
            if guid in seen:
                raise ValueError(f"scope 내 Route GUID가 중복 조회되었습니다: {guid}")
            seen.add(guid)
            feature = parse_vector(row.pop("feature_vector_text"))
            context = parse_vector(row.pop("context_vector_text"))
            row.update({
                "route_path_guid": guid,
                "process_name": normalize_text(row["process_name"]).upper(),
                "equipment_raw": normalize_text(row["equipment_tag"]),
                "equipment_key": normalize_equipment_key(row["equipment_tag"]),
                "equipment_name": normalize_text(row["equipment_name"]),
                "equipment_family_key": normalize_text(row["equipment_name"]).upper(),
                "utility_group": normalize_text(row["utility_group"]).upper(),
                "utility": normalize_text(row["utility"]).upper(),
                "size": normalize_size(row["size"]),
                "start_xyz": [float(row[key]) for key in ("sx", "sy", "sz")] if all(row[key] is not None for key in ("sx", "sy", "sz")) else None,
                "end_xyz": [float(row[key]) for key in ("ex", "ey", "ez")] if all(row[key] is not None for key in ("ex", "ey", "ez")) else None,
                "feature_vector": feature,
                "context_vector": context,
                "feature_provenance": "@".join(filter(None, (normalize_text(row["feature_encoder_version"]), normalize_text(row["feature_encoded_at"])))),
                "context_provenance": normalize_text(row["context_build_run_id"]),
                "context_signature": (
                    normalize_text(row["context_encoder_version"]),
                    normalize_text(row["context_config_hash"]),
                ) if context is not None else None,
                "geometry_points": geometry.get(guid, []),
            })
            rows.append(row)
    return rows


def compute_groups(
    source_members: list[dict[str, Any]],
    project: str,
    revision: str,
    min_members: int,
    build_run_id: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    diagnostics = Counter()
    for member in source_members:
        required = (member["equipment_key"], member["utility_group"], member["utility"])
        if not all(required):
            diagnostics["invalid_group_key_members"] += 1
            continue
        grouped[(member["process_name"], *required)].append(member)

    base_config = encoder_config(min_members)
    results: list[dict[str, Any]] = []
    for key, raw_members in sorted(grouped.items()):
        if len(raw_members) < min_members:
            diagnostics["below_min_member_groups"] += 1
            continue
        members = deterministic_members(raw_members)
        if any(member["feature_vector"] is None for member in members):
            diagnostics["incomplete_feature_groups"] += 1
            continue
        if any(member["start_xyz"] is None or member["end_xyz"] is None for member in members):
            diagnostics["incomplete_coordinate_groups"] += 1
            continue

        signatures = Counter(member["context_signature"] for member in members if member["context_signature"] is not None)
        if len(signatures) > 1:
            diagnostics["mixed_context_contract_groups"] += 1
        selected_signature = sorted(signatures, key=lambda item: (-signatures[item], item))[0] if signatures else None
        compatible_contexts = []
        for member in members:
            if member["context_signature"] == selected_signature:
                compatible_contexts.append(member["context_vector"])
            else:
                member["context_vector"] = None
                member["context_provenance"] = ""

        process, equipment, utility_group, utility = key
        identity = {
            "project_scope_key": project,
            "model_revision_key": revision,
            "process_name": process,
            "equipment_instance_key": equipment,
            "utility_group": utility_group,
            "utility": utility,
        }
        context_contract = None
        if selected_signature is not None:
            context_contract = {
                "encoder_version": selected_signature[0],
                "encoder_config_hash": selected_signature[1],
            }
        group_config = {**base_config, "context_contract": context_contract}
        config_hash = sha256_json(group_config)
        group_id = make_group_vector_id(identity)
        feature_centroid = normalized_centroid([member["feature_vector"] for member in members])
        context_centroid = normalized_centroid(compatible_contexts) if compatible_contexts else None
        arrangement = build_arrangement(members)
        starts = [member["start_xyz"] for member in members]
        ends = [member["end_xyz"] for member in members]
        source_hash = sha256_json(source_hash_payload(identity, members, config_hash))
        equipment_names = Counter(member["equipment_name"] for member in members if member["equipment_name"])
        equipment_name = sorted(equipment_names, key=lambda item: (-equipment_names[item], item))[0] if equipment_names else ""

        for order, member in enumerate(members):
            member["member_order"] = order
        results.append({
            **identity,
            "group_vector_id": group_id,
            "equipment_name": equipment_name,
            "equipment_family_key": equipment_name.upper(),
            "member_count": len(members),
            "size_signature": arrangement["size_signature"],
            "member_guids": [member["route_path_guid"] for member in members],
            "feature_centroid": feature_centroid,
            "context_centroid": context_centroid,
            "arrangement": arrangement,
            "start_centroid": centroid(starts),
            "end_centroid": centroid(ends),
            "aabb_min": arrangement["aabb"]["min"],
            "aabb_max": arrangement["aabb"]["max"],
            "feature_coverage": 1.0,
            "context_coverage": len(compatible_contexts) / len(members),
            "context_signature": selected_signature,
            "source_hash": source_hash,
            "build_run_id": build_run_id,
            "encoder_version": ENCODER_VERSION,
            "encoder_config": group_config,
            "encoder_config_hash": config_hash,
            "status": "READY",
            "members": members,
        })
        diagnostics["compatible_context_members"] += len(compatible_contexts)
        if len(compatible_contexts) == len(members):
            diagnostics["full_context_groups"] += 1
        diagnostics["geometry_fallback_members"] += sum(not member["geometry_points"] for member in members)
    diagnostics["source_member_count"] = len(source_members)
    diagnostics["source_group_count"] = len(grouped)
    diagnostics["ready_group_count"] = len(results)
    diagnostics["ready_member_count"] = sum(group["member_count"] for group in results)
    return results, dict(diagnostics)


HEADER_UPSERT_SQL = '''
    INSERT INTO "TB_ROUTE_UTILITY_GROUP_VECTOR" (
      "GROUP_VECTOR_ID","PROJECT_SCOPE_KEY","MODEL_REVISION_KEY","PROCESS_NAME",
      "EQUIPMENT_INSTANCE_KEY","EQUIPMENT_NAME","EQUIPMENT_FAMILY_KEY","UTILITY_GROUP","UTILITY",
      "MEMBER_COUNT","SIZE_SIGNATURE","MEMBER_GUIDS","FEATURE_CENTROID","CONTEXT_CENTROID",
      "ARRANGEMENT_VECTOR_JSON","START_CENTROID_X","START_CENTROID_Y","START_CENTROID_Z",
      "END_CENTROID_X","END_CENTROID_Y","END_CENTROID_Z","AABB_MINX","AABB_MINY","AABB_MINZ",
      "AABB_MAXX","AABB_MAXY","AABB_MAXZ","FEATURE_COVERAGE","CONTEXT_COVERAGE","SOURCE_HASH",
      "BUILD_RUN_ID","ENCODER_VERSION","ENCODER_CONFIG_JSON","ENCODER_CONFIG_HASH","STATUS","UPDATED_AT")
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::vector,%s::vector,%s::jsonb,
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::uuid,%s,%s::jsonb,%s,'BUILDING',now())
    ON CONFLICT ("GROUP_VECTOR_ID") DO UPDATE SET
      "PROCESS_NAME"=EXCLUDED."PROCESS_NAME","EQUIPMENT_NAME"=EXCLUDED."EQUIPMENT_NAME",
      "EQUIPMENT_FAMILY_KEY"=EXCLUDED."EQUIPMENT_FAMILY_KEY","MEMBER_COUNT"=EXCLUDED."MEMBER_COUNT",
      "SIZE_SIGNATURE"=EXCLUDED."SIZE_SIGNATURE","MEMBER_GUIDS"=EXCLUDED."MEMBER_GUIDS",
      "FEATURE_CENTROID"=EXCLUDED."FEATURE_CENTROID","CONTEXT_CENTROID"=EXCLUDED."CONTEXT_CENTROID",
      "ARRANGEMENT_VECTOR_JSON"=EXCLUDED."ARRANGEMENT_VECTOR_JSON",
      "START_CENTROID_X"=EXCLUDED."START_CENTROID_X","START_CENTROID_Y"=EXCLUDED."START_CENTROID_Y",
      "START_CENTROID_Z"=EXCLUDED."START_CENTROID_Z","END_CENTROID_X"=EXCLUDED."END_CENTROID_X",
      "END_CENTROID_Y"=EXCLUDED."END_CENTROID_Y","END_CENTROID_Z"=EXCLUDED."END_CENTROID_Z",
      "AABB_MINX"=EXCLUDED."AABB_MINX","AABB_MINY"=EXCLUDED."AABB_MINY","AABB_MINZ"=EXCLUDED."AABB_MINZ",
      "AABB_MAXX"=EXCLUDED."AABB_MAXX","AABB_MAXY"=EXCLUDED."AABB_MAXY","AABB_MAXZ"=EXCLUDED."AABB_MAXZ",
      "FEATURE_COVERAGE"=EXCLUDED."FEATURE_COVERAGE","CONTEXT_COVERAGE"=EXCLUDED."CONTEXT_COVERAGE",
      "SOURCE_HASH"=EXCLUDED."SOURCE_HASH","BUILD_RUN_ID"=EXCLUDED."BUILD_RUN_ID",
      "ENCODER_VERSION"=EXCLUDED."ENCODER_VERSION","ENCODER_CONFIG_JSON"=EXCLUDED."ENCODER_CONFIG_JSON",
      "ENCODER_CONFIG_HASH"=EXCLUDED."ENCODER_CONFIG_HASH","STATUS"='BUILDING',"UPDATED_AT"=now()
'''


MEMBER_INSERT_SQL = '''
    INSERT INTO "TB_ROUTE_UTILITY_GROUP_MEMBER" (
      "GROUP_VECTOR_ID","ROUTE_PATH_GUID","MEMBER_ORDER","UTILITY","SIZE",
      "START_X","START_Y","START_Z","END_X","END_Y","END_Z","DIRECTION_PATTERN",
      "TOTAL_LENGTH_MM","STEP_COUNT","FEATURE_VECTOR_BUILD_RUN_ID","CONTEXT_VECTOR_BUILD_RUN_ID")
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
'''


def header_values(group: dict[str, Any]) -> tuple[Any, ...]:
    return (
        group["group_vector_id"], group["project_scope_key"], group["model_revision_key"], group["process_name"],
        group["equipment_instance_key"], group["equipment_name"], group["equipment_family_key"],
        group["utility_group"], group["utility"], group["member_count"], canonical_json(group["size_signature"]),
        canonical_json(group["member_guids"]), vector_literal(group["feature_centroid"]),
        vector_literal(group["context_centroid"]) if group["context_centroid"] is not None else None,
        canonical_json(group["arrangement"]), *group["start_centroid"], *group["end_centroid"],
        *group["aabb_min"], *group["aabb_max"], group["feature_coverage"], group["context_coverage"],
        group["source_hash"], group["build_run_id"], group["encoder_version"],
        canonical_json(group["encoder_config"]), group["encoder_config_hash"],
    )


def member_values(group: dict[str, Any], member: dict[str, Any]) -> tuple[Any, ...]:
    return (
        group["group_vector_id"], member["route_path_guid"], member["member_order"], group["utility"],
        member["size"], *member["start_xyz"], *member["end_xyz"], normalize_text(member["direction_pattern"]),
        float(member["total_length_mm"] or 0.0), int(member["step_count"] or 0),
        normalize_text(member["feature_provenance"]), normalize_text(member["context_provenance"]) or None,
    )


def save_groups(conn, groups: list[dict[str, Any]], project: str, revision: str, force: bool) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            '''SELECT "GROUP_VECTOR_ID","SOURCE_HASH","STATUS" FROM "TB_ROUTE_UTILITY_GROUP_VECTOR"
               WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s''',
            (project, revision),
        )
        existing = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

    changed = [
        group for group in groups
        if force or existing.get(group["group_vector_id"]) != (group["source_hash"], "READY")
    ]
    current_ids = [group["group_vector_id"] for group in groups]
    try:
        with conn.cursor() as cur:
            if changed:
                execute_batch(cur, HEADER_UPSERT_SQL, [header_values(group) for group in changed], page_size=100)
                changed_ids = [group["group_vector_id"] for group in changed]
                cur.execute(
                    'DELETE FROM "TB_ROUTE_UTILITY_GROUP_MEMBER" WHERE "GROUP_VECTOR_ID"=ANY(%s)',
                    (changed_ids,),
                )
                member_rows = [member_values(group, member) for group in changed for member in group["members"]]
                execute_batch(cur, MEMBER_INSERT_SQL, member_rows, page_size=500)
                cur.execute(
                    '''UPDATE "TB_ROUTE_UTILITY_GROUP_VECTOR" SET "STATUS"='READY',"UPDATED_AT"=now()
                       WHERE "GROUP_VECTOR_ID"=ANY(%s)''',
                    (changed_ids,),
                )
            if current_ids:
                cur.execute(
                    '''UPDATE "TB_ROUTE_UTILITY_GROUP_VECTOR" SET "STATUS"='STALE',"UPDATED_AT"=now()
                       WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s
                         AND NOT ("GROUP_VECTOR_ID"=ANY(%s)) AND "STATUS"<>'STALE' ''',
                    (project, revision, current_ids),
                )
            else:
                cur.execute(
                    '''UPDATE "TB_ROUTE_UTILITY_GROUP_VECTOR" SET "STATUS"='STALE',"UPDATED_AT"=now()
                       WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s AND "STATUS"<>'STALE' ''',
                    (project, revision),
                )
            stale_changed = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "computed_groups": len(groups),
        "rebuilt_groups": len(changed),
        "skipped_unchanged_groups": len(groups) - len(changed),
        "stale_status_changes": max(stale_changed, 0),
        "saved_members": sum(group["member_count"] for group in changed),
    }


def status_report(conn, project: str, revision: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            '''SELECT "STATUS",COUNT(*),COALESCE(SUM("MEMBER_COUNT"),0),
                      MIN("UPDATED_AT"),MAX("UPDATED_AT")
                 FROM "TB_ROUTE_UTILITY_GROUP_VECTOR"
                WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s GROUP BY "STATUS" ORDER BY "STATUS"''',
            (project, revision),
        )
        status = [
            {"status": row[0], "group_count": row[1], "declared_member_count": row[2],
             "updated_at_min": row[3].isoformat() if row[3] else None,
             "updated_at_max": row[4].isoformat() if row[4] else None}
            for row in cur.fetchall()
        ]
        cur.execute(
            '''SELECT COUNT(*),COUNT(DISTINCT m."ROUTE_PATH_GUID")
                 FROM "TB_ROUTE_UTILITY_GROUP_MEMBER" m
                 JOIN "TB_ROUTE_UTILITY_GROUP_VECTOR" g ON g."GROUP_VECTOR_ID"=m."GROUP_VECTOR_ID"
                WHERE g."PROJECT_SCOPE_KEY"=%s AND g."MODEL_REVISION_KEY"=%s AND g."STATUS"='READY' ''',
            (project, revision),
        )
        member_rows, distinct_routes = cur.fetchone()
    return {"project": project, "revision": revision, "status": status,
            "ready_member_rows": member_rows, "ready_distinct_routes": distinct_routes}


def validate_groups(conn, project: str, revision: str, min_members: int) -> dict[str, Any]:
    """DB 구조 무결성과 현재 원본으로 재계산한 Source Hash drift를 함께 검사한다."""
    errors: list[str] = []
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            '''SELECT g."GROUP_VECTOR_ID",g."MEMBER_COUNT",g."MEMBER_GUIDS",g."FEATURE_CENTROID"::text,
                      g."CONTEXT_CENTROID"::text,g."FEATURE_COVERAGE",g."CONTEXT_COVERAGE",g."SOURCE_HASH",
                      COUNT(m.*) AS actual_members,COUNT(DISTINCT m."ROUTE_PATH_GUID") AS distinct_members
                 FROM "TB_ROUTE_UTILITY_GROUP_VECTOR" g
                 LEFT JOIN "TB_ROUTE_UTILITY_GROUP_MEMBER" m ON m."GROUP_VECTOR_ID"=g."GROUP_VECTOR_ID"
                WHERE g."PROJECT_SCOPE_KEY"=%s AND g."MODEL_REVISION_KEY"=%s AND g."STATUS"='READY'
                GROUP BY g."GROUP_VECTOR_ID"''',
            (project, revision),
        )
        stored = [dict(row) for row in cur.fetchall()]
    stored_hashes = {row["GROUP_VECTOR_ID"]: row["SOURCE_HASH"] for row in stored}
    for row in stored:
        group_id = row["GROUP_VECTOR_ID"]
        if row["MEMBER_COUNT"] != row["actual_members"] or row["actual_members"] != row["distinct_members"]:
            errors.append(f"{group_id}: member count mismatch")
        if len(row["MEMBER_GUIDS"]) != row["MEMBER_COUNT"]:
            errors.append(f"{group_id}: MEMBER_GUIDS length mismatch")
        for label in ("FEATURE_CENTROID", "CONTEXT_CENTROID"):
            vector = parse_vector(row[label])
            if label == "FEATURE_CENTROID" and vector is None:
                errors.append(f"{group_id}: invalid Feature centroid")
            if vector is not None:
                norm = math.sqrt(sum(value * value for value in vector))
                if abs(norm - 1.0) > 1e-5:
                    errors.append(f"{group_id}: {label} norm={norm}")

    source = load_source_members(conn, project, revision)
    expected, diagnostics = compute_groups(source, project, revision, min_members, str(uuid.UUID(int=0)))
    expected_hashes = {group["group_vector_id"]: group["source_hash"] for group in expected}
    for group_id in sorted(set(expected_hashes) | set(stored_hashes)):
        if expected_hashes.get(group_id) != stored_hashes.get(group_id):
            errors.append(f"{group_id}: source hash drift or missing READY group")
    return {
        "valid": not errors,
        "errors": errors,
        "ready_group_count": len(stored),
        "ready_member_count": sum(row["actual_members"] for row in stored),
        "expected_group_count": len(expected),
        "diagnostics": diagnostics,
    }


def write_report(report: dict[str, Any], path_value: str | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(text)
    if path_value:
        path = Path(path_value)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"[saved] {path.resolve()}")


def main() -> int:
    args = parse_args()
    if args.min_members < 2:
        raise SystemExit("--min-members는 2 이상이어야 합니다.")
    if psycopg2 is None:
        raise SystemExit("psycopg2가 필요합니다. requirements-utility-pipe-group.txt를 설치하세요.")
    runtime = tool_config.resolve_runtime(args)
    conn = psycopg2.connect(runtime.conninfo)
    try:
        if args.action == "create-schema":
            with conn.cursor() as cur:
                cur.execute(CREATE_SQL.read_text(encoding="utf-8"))
            conn.commit()
            write_report({"schema_applied": True, "sql": str(CREATE_SQL)}, args.report_out)
            return 0

        project, revision = resolve_scope(conn, args)
        if args.action == "status":
            write_report(status_report(conn, project, revision), args.report_out)
            return 0
        if args.action == "validate":
            report = validate_groups(conn, project, revision, args.min_members)
            write_report(report, args.report_out)
            return 0 if report["valid"] else 2

        build_run_id = str(uuid.uuid4())
        source = load_source_members(conn, project, revision)
        groups, diagnostics = compute_groups(source, project, revision, args.min_members, build_run_id)
        report: dict[str, Any] = {
            "project": project,
            "revision": revision,
            "build_run_id": build_run_id,
            "dry_run": args.dry_run,
            "diagnostics": diagnostics,
        }
        if args.dry_run:
            report["save"] = {"computed_groups": len(groups), "would_save_members": sum(g["member_count"] for g in groups)}
        else:
            report["save"] = save_groups(conn, groups, project, revision, args.force)
            report["validation"] = validate_groups(conn, project, revision, args.min_members)
        write_report(report, args.report_out)
        return 0 if args.dry_run or report["validation"]["valid"] else 2
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
