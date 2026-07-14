"""
Route/Context 원본 리비전 운영 라이프사이클 도구.

실행 방법(PowerShell)
---------------------
1. 현재 ACTIVE 조회
   python Tools/RouteContextLifecycle.py --config Tools/tools.settings.json active
2. 원본 스냅샷 계산 + scope 적용 + Context 생성 + 검증
   python Tools/RouteContextLifecycle.py --config Tools/tools.settings.json build `
     --project-scope-key DB:DDW_AI_DB --import-batch-id IMPORT-001
3. READY 리비전 운영 승격
   python Tools/RouteContextLifecycle.py --config Tools/tools.settings.json promote `
     --project-scope-key DB:DDW_AI_DB --model-revision-key snapshot:<sha256>
4. 무결성/원본 변조 감시
   python Tools/RouteContextLifecycle.py --config Tools/tools.settings.json monitor --verify-source-hash
5. 이전 ACTIVE로 복구
   python Tools/RouteContextLifecycle.py --config Tools/tools.settings.json rollback `
     --project-scope-key DB:DDW_AI_DB --model-revision-key <previous-revision>
6. 보존정책 확인(기본 dry-run, 실제 삭제 시 --execute 추가)
   python Tools/RouteContextLifecycle.py --config Tools/tools.settings.json cleanup `
     --project-scope-key DB:DDW_AI_DB --keep 2

전체 흐름도
-----------
  [source Route/Feature/BIM]
            |
            v
  [REPEATABLE READ snapshot hash] -- 동일 hash/유효 revision --> [재생성 생략]
            |
            v
  [Manifest BUILDING + source scope 적용]
            |
            v
  [Context Vector 생성] -> [건수/coverage/encoder/snapshot 검증]
            |                         |
            | 실패                    | 성공
            v                         v
         [FAILED]                  [READY]
                                       |
                              promote/rollback
                                       v
             [기존 ACTIVE -> RETIRED] + [신규 ACTIVE]

설계 원칙
---------
- 프로젝트별 ACTIVE는 하나만 허용한다.
- build 전체를 advisory lock으로 직렬화하고 동일 DB snapshot에서 해시와 데이터를 읽는다.
- 검증을 통과하지 않은 revision은 ACTIVE로 승격하지 않는다.
- 모든 상태 전이는 audit 테이블에 남긴다.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

import tool_config
import ApplyRouteSourceScope as source_scope
import ExtractObstacleContextVector as context_builder


def audit(conn, project, revision, action, old=None, new=None, detail=None):
    """상태 변경 이력을 저장한다.

    project/revision은 감사 대상의 복합 식별자이고, old/new는 전이 전후 상태이다.
    detail은 검증 결과나 작업 사유처럼 정형 컬럼에 담기 어려운 정보를 JSON으로 보존한다.
    """
    with conn.cursor() as cur:
        cur.execute(
            '''INSERT INTO "TB_ROUTE_SOURCE_SCOPE_AUDIT"
               ("AUDIT_ID","PROJECT_SCOPE_KEY","MODEL_REVISION_KEY","ACTION",
                "FROM_STATUS","TO_STATUS","DETAIL_JSON")
               VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)''',
            (str(uuid.uuid4()), project, revision, action, old, new, json.dumps(detail or {})),
        )


def ensure_schema(conn):
    """source scope와 Context Vector의 멱등성 migration을 모두 적용한다."""
    source_scope.create_schema(conn)
    context_builder.create_schema(conn)


def validate_revision(conn, project: str, revision: str) -> dict:
    """Manifest에 선언된 리비전과 실제 원본/Context 데이터의 일치 여부를 검사한다.

    주요 변수:
    - manifest: 빌드 시 고정한 Feature/Route/Obstacle 건수와 source hash
    - context_count: 해당 scope/revision에 저장된 Context variant 수
    - unique_routes: 중복을 제거한 Context route 수
    - wrong_scope/wrong_encoder: strict provenance 또는 encoder 계약 위반 건수
    """
    with conn.cursor() as cur:
        cur.execute(
            '''SELECT "FEATURE_ROW_COUNT","ROUTE_ROW_COUNT","OBSTACLE_ROW_COUNT",
                      "SOURCE_SNAPSHOT_HASH","STATUS"
               FROM "TB_ROUTE_SOURCE_SCOPE_MANIFEST"
               WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s''',
            (project, revision),
        )
        manifest = cur.fetchone()
        if not manifest:
            raise ValueError("manifest not found")
        cur.execute(
            '''SELECT COUNT(*), COUNT(DISTINCT "ROUTE_PATH_GUID"),
                      COUNT(DISTINCT "SOURCE_SNAPSHOT_HASH"),
                      COUNT(*) FILTER (WHERE "SCOPE_RESOLUTION_STATUS" <> 'STRICT_COMMON_KEY'),
                      COUNT(*) FILTER (WHERE "ENCODER_VERSION" <> %s OR "ENCODER_CONFIG_HASH" <> %s)
               FROM "TB_ROUTE_CONTEXT_VECTOR"
               WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s''',
            (context_builder.ENCODER_VERSION, context_builder.ENCODER_CONFIG_HASH, project, revision),
        )
        context_count, unique_routes, context_snapshots, wrong_scope, wrong_encoder = cur.fetchone()
        cur.execute(
            '''SELECT COUNT(*) FROM "TB_ROUTE_FEATURE_VECTOR"
               WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s''',
            (project, revision),
        )
        current_features = cur.fetchone()[0]
        cur.execute(
            '''SELECT COUNT(*) FROM "TB_ROUTE_PATH"
               WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s''',
            (project, revision),
        )
        current_routes = cur.fetchone()[0]
        cur.execute(
            '''SELECT COUNT(*) FROM "TB_BIM_OBSTACLE"
               WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s
                 AND "DDWORKS_TYPE" = ANY(%s)''',
            (project, revision, list(source_scope.STRUCTURAL_TYPES)),
        )
        current_obstacles = cur.fetchone()[0]
    expected_features, expected_routes, expected_obstacles, source_hash, status = manifest
    checks = {
        "feature_manifest_match": current_features == expected_features,
        "route_manifest_match": current_routes == expected_routes,
        "obstacle_manifest_match": current_obstacles == expected_obstacles,
        "context_coverage_match": unique_routes == expected_features,
        "context_no_duplicates": context_count == unique_routes,
        "single_context_snapshot": context_snapshots == 1,
        "strict_scope_only": wrong_scope == 0,
        "encoder_contract": wrong_encoder == 0,
    }
    return {
        "project_scope_key": project, "model_revision_key": revision,
        "source_snapshot_hash": source_hash, "status": status,
        "expected": {"feature": expected_features, "route": expected_routes, "obstacle": expected_obstacles},
        "actual": {"feature": current_features, "route": current_routes, "obstacle": current_obstacles,
                   "context": context_count, "unique_context_routes": unique_routes},
        "checks": checks, "valid": all(checks.values()),
    }


def build(conn, project: str, requested_revision: str = "", metadata: dict | None = None) -> dict:
    """원본 snapshot부터 READY Context revision까지 원자적인 빌드 절차를 수행한다.

    requested_revision이 비어 있으면 ``snapshot:<source hash>``를 사용한다.
    metadata에는 importer batch, 원본 artifact/checksum, 부모 revision, 메모가 전달된다.
    기존 READY/ACTIVE가 같은 해시이며 재검증도 통과하면 비싼 전체 인코딩을 생략한다.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(hashtext(%s))", ("route-context:" + project,))
    conn.commit()
    try:
        conn.set_session(isolation_level="REPEATABLE READ")
        snapshot, counts = source_scope.compute_snapshot(conn)
        revision = requested_revision.strip() or f"snapshot:{snapshot}"
        with conn.cursor() as cur:
            cur.execute(
                '''SELECT "STATUS","SOURCE_SNAPSHOT_HASH"
                   FROM "TB_ROUTE_SOURCE_SCOPE_MANIFEST"
                   WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s''',
                (project, revision),
            )
            existing = cur.fetchone()
        if existing and existing[0] in ("READY", "ACTIVE") and existing[1] == snapshot:
            report = validate_revision(conn, project, revision)
            if report["valid"]:
                report["skipped_unchanged"] = True
                audit(conn, project, revision, "BUILD_SKIPPED_UNCHANGED", existing[0], existing[0], report)
                conn.commit()
                return report
        source_scope.apply_scope(conn, project, revision, snapshot, counts)
        metadata = metadata or {}
        with conn.cursor() as cur:
            cur.execute(
                '''UPDATE "TB_ROUTE_SOURCE_SCOPE_MANIFEST"
                   SET "IMPORT_BATCH_ID"=%s,"SOURCE_ARTIFACT"=%s,"SOURCE_ARTIFACT_HASH"=%s,
                       "PARENT_REVISION_KEY"=%s,"NOTE"=%s
                   WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s''',
                (metadata.get("import_batch_id"), metadata.get("source_artifact"),
                 metadata.get("source_artifact_hash"), metadata.get("parent_revision_key"),
                 metadata.get("note"), project, revision),
            )
        conn.commit()
        rows = context_builder.extract_context_vectors(
            conn, project_scope_key=project, model_revision_key=revision
        )
        context_builder.save_context_vectors(conn, rows)
        report = validate_revision(conn, project, revision)
        report["status"] = "READY" if report["valid"] else "FAILED"
        with conn.cursor() as cur:
            cur.execute(
                '''UPDATE "TB_ROUTE_SOURCE_SCOPE_MANIFEST"
                   SET "STATUS"=%s, "VALIDATION_JSON"=%s::jsonb,
                       "READY_AT"=CASE WHEN %s THEN now() ELSE NULL END
                   WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s''',
                ("READY" if report["valid"] else "FAILED", json.dumps(report), report["valid"], project, revision),
            )
        audit(conn, project, revision, "BUILD", "BUILDING",
              "READY" if report["valid"] else "FAILED", report)
        conn.commit()
        return report
    finally:
        conn.set_session(isolation_level="READ COMMITTED")
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", ("route-context:" + project,))
        conn.commit()


def promote(conn, project: str, revision: str, note: str = "", action: str = "PROMOTE") -> dict:
    """검증된 revision을 ACTIVE로 승격하고 기존 ACTIVE를 RETIRED로 전환한다."""
    report = validate_revision(conn, project, revision)
    if not report["valid"]:
        raise ValueError("revision validation failed")
    with conn.cursor() as cur:
        cur.execute(
            '''SELECT "MODEL_REVISION_KEY" FROM "TB_ROUTE_SOURCE_SCOPE_MANIFEST"
               WHERE "PROJECT_SCOPE_KEY"=%s AND "STATUS"='ACTIVE' FOR UPDATE''', (project,)
        )
        old_row = cur.fetchone()
        old_revision = old_row[0] if old_row else None
        if old_revision and old_revision != revision:
            cur.execute(
                '''UPDATE "TB_ROUTE_SOURCE_SCOPE_MANIFEST"
                   SET "STATUS"='RETIRED',"RETIRED_AT"=now()
                   WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s''',
                (project, old_revision),
            )
            audit(conn, project, old_revision, "RETIRE_ON_PROMOTE", "ACTIVE", "RETIRED")
        cur.execute(
            '''UPDATE "TB_ROUTE_SOURCE_SCOPE_MANIFEST"
               SET "STATUS"='ACTIVE',"PROMOTED_AT"=now(),"RETIRED_AT"=NULL,"NOTE"=%s
                   ,"VALIDATION_JSON"=jsonb_set(COALESCE("VALIDATION_JSON",'{}'::jsonb),'{status}','"ACTIVE"'::jsonb)
               WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s
                 AND "STATUS" IN ('READY','ACTIVE','RETIRED')''',
            (note or None, project, revision),
        )
        if cur.rowcount != 1:
            raise ValueError("revision is not promotable")
    audit(conn, project, revision, action, report["status"], "ACTIVE",
          {"previous_active": old_revision, "note": note})
    conn.commit()
    return {"project_scope_key": project, "active_revision": revision, "previous_active": old_revision}


def retire(conn, project: str, revision: str, force: bool = False) -> dict:
    """revision을 RETIRED로 전환한다. ACTIVE 직접 retire는 force 없이는 거부한다."""
    with conn.cursor() as cur:
        cur.execute(
            '''SELECT "STATUS" FROM "TB_ROUTE_SOURCE_SCOPE_MANIFEST"
               WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s FOR UPDATE''',
            (project, revision),
        )
        row = cur.fetchone()
        if not row: raise ValueError("manifest not found")
        old = row[0]
        if old == "ACTIVE" and not force:
            raise ValueError("refusing to retire ACTIVE revision without --force")
        cur.execute(
            '''UPDATE "TB_ROUTE_SOURCE_SCOPE_MANIFEST"
               SET "STATUS"='RETIRED',"RETIRED_AT"=now()
               WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s''',
            (project, revision),
        )
    audit(conn, project, revision, "RETIRE", old, "RETIRED", {"force": force})
    conn.commit()
    return {"project_scope_key": project, "retired_revision": revision, "previous_status": old}


def active(conn, project: str = "") -> list[dict]:
    """전체 프로젝트 또는 지정 프로젝트의 현재 ACTIVE catalog를 반환한다."""
    sql = '''SELECT "PROJECT_SCOPE_KEY","MODEL_REVISION_KEY","SOURCE_SNAPSHOT_HASH",
                    "STATUS","PROMOTED_AT","VALIDATION_JSON"
             FROM "TB_ROUTE_SOURCE_SCOPE_MANIFEST" WHERE "STATUS"='ACTIVE' '''
    params = []
    if project:
        sql += ' AND "PROJECT_SCOPE_KEY"=%s'
        params.append(project)
    sql += ' ORDER BY "PROJECT_SCOPE_KEY"'
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(zip(("project_scope_key","model_revision_key","source_snapshot_hash",
                          "status","promoted_at","validation"), row)) for row in cur.fetchall()]


def monitor(conn, verify_source_hash: bool = False) -> dict:
    """NULL scope, ACTIVE 개수, orphan Context 및 선택적인 source hash drift를 검사한다.

    verify_source_hash는 대용량 원본 전체를 다시 해시하므로 정기 심층점검에서만 켠다.
    """
    issues = []
    with conn.cursor() as cur:
        for table in source_scope.SOURCE_TABLES:
            cur.execute(
                f'''SELECT COUNT(*) FROM "{table}" WHERE "PROJECT_SCOPE_KEY" IS NULL
                     OR "MODEL_REVISION_KEY" IS NULL'''
            )
            count = cur.fetchone()[0]
            if count: issues.append({"kind": "NULL_SCOPE", "table": table, "count": count})
        cur.execute(
            '''SELECT "PROJECT_SCOPE_KEY",COUNT(*) FILTER (WHERE "STATUS"='ACTIVE')
               FROM "TB_ROUTE_SOURCE_SCOPE_MANIFEST"
               GROUP BY 1 HAVING COUNT(*) FILTER (WHERE "STATUS"='ACTIVE')<>1'''
        )
        for project, count in cur.fetchall():
            issues.append({"kind": "ACTIVE_COUNT", "project": project, "count": count})
        cur.execute(
            '''SELECT COUNT(*) FROM "TB_ROUTE_CONTEXT_VECTOR" cv
               LEFT JOIN "TB_ROUTE_FEATURE_VECTOR" fv
                 ON fv."ROUTE_PATH_GUID"=cv."ROUTE_PATH_GUID"
                AND fv."PROJECT_SCOPE_KEY"=cv."PROJECT_SCOPE_KEY"
                AND fv."MODEL_REVISION_KEY"=cv."MODEL_REVISION_KEY"
               WHERE cv."SCOPE_RESOLUTION_STATUS"='STRICT_COMMON_KEY'
                 AND fv."ROUTE_PATH_GUID" IS NULL'''
        )
        orphan_context = cur.fetchone()[0]
        if orphan_context: issues.append({"kind": "ORPHAN_CONTEXT", "count": orphan_context})
    current_source_hash = None
    if verify_source_hash:
        current_source_hash, _ = source_scope.compute_snapshot(conn)
        for manifest in active(conn):
            if manifest["source_snapshot_hash"] != current_source_hash:
                issues.append({
                    "kind": "SOURCE_HASH_DRIFT",
                    "project": manifest["project_scope_key"],
                    "active_revision": manifest["model_revision_key"],
                    "expected": manifest["source_snapshot_hash"],
                    "actual": current_source_hash,
                })
    return {"healthy": not issues, "issues": issues, "active": active(conn),
            "current_source_hash": current_source_hash}


def diff(conn, project: str, left: str, right: str) -> dict:
    """두 revision의 source hash, 원본 건수 및 상태를 비교한다."""
    with conn.cursor() as cur:
        cur.execute(
            '''SELECT "MODEL_REVISION_KEY","SOURCE_SNAPSHOT_HASH","FEATURE_ROW_COUNT",
                      "ROUTE_ROW_COUNT","OBSTACLE_ROW_COUNT","STATUS"
               FROM "TB_ROUTE_SOURCE_SCOPE_MANIFEST"
               WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=ANY(%s)''',
            (project, [left, right]),
        )
        manifests = {row[0]: row[1:] for row in cur.fetchall()}
    if left not in manifests or right not in manifests:
        raise ValueError("both revisions must exist")
    keys = ("snapshot_hash","feature_count","route_count","obstacle_count","status")
    return {"project_scope_key": project, "left": dict(zip(keys, manifests[left])),
            "right": dict(zip(keys, manifests[right])),
            "same_source_snapshot": manifests[left][0] == manifests[right][0]}


def cleanup(conn, project: str, keep: int, execute: bool) -> dict:
    """오래된 RETIRED/FAILED revision을 보존정책에 따라 정리한다.

    keep은 최근 보존 개수이다. 원본 테이블이 참조하는 revision은 protected_revisions로
    분류해 삭제하지 않으며, execute=False인 기본 모드는 삭제 계획만 반환한다.
    """
    with conn.cursor() as cur:
        cur.execute(
            '''SELECT "MODEL_REVISION_KEY" FROM "TB_ROUTE_SOURCE_SCOPE_MANIFEST"
               WHERE "PROJECT_SCOPE_KEY"=%s AND "STATUS" IN ('RETIRED','FAILED')
               ORDER BY COALESCE("PROMOTED_AT","APPLIED_AT") DESC OFFSET %s''',
            (project, keep),
        )
        candidates = [row[0] for row in cur.fetchall()]
        revisions = []
        protected = []
        for revision in candidates:
            references = {}
            for table in source_scope.SOURCE_TABLES:
                cur.execute(
                    f'''SELECT COUNT(*) FROM "{table}"
                        WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s''',
                    (project, revision),
                )
                count = cur.fetchone()[0]
                if count:
                    references[table] = count
            if references:
                protected.append({"model_revision_key": revision, "source_references": references})
            else:
                revisions.append(revision)
        if execute:
            for revision in revisions:
                cur.execute(
                    '''DELETE FROM "TB_ROUTE_CONTEXT_VECTOR"
                       WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s''',
                    (project, revision),
                )
                cur.execute(
                    '''DELETE FROM "TB_ROUTE_SOURCE_SCOPE_MANIFEST"
                       WHERE "PROJECT_SCOPE_KEY"=%s AND "MODEL_REVISION_KEY"=%s''',
                    (project, revision),
                )
                audit(conn, project, revision, "CLEANUP", None, None)
    if execute: conn.commit()
    return {"execute": execute, "delete_revisions": revisions, "protected_revisions": protected}


def main() -> int:
    """공통 DB 설정과 sub-command 인자를 해석하여 해당 운영 함수를 실행한다."""
    parser = argparse.ArgumentParser(description="Route Context revision lifecycle")
    tool_config.add_common_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("schema")
    monitor_p = sub.add_parser("monitor")
    monitor_p.add_argument("--verify-source-hash", action="store_true")
    active_p = sub.add_parser("active"); active_p.add_argument("--project-scope-key", default="")
    for name in ("build", "validate", "promote", "rollback", "retire"):
        p = sub.add_parser(name); p.add_argument("--project-scope-key", required=True)
        p.add_argument("--model-revision-key", default="" if name == "build" else None,
                       required=name != "build")
        if name in ("promote", "rollback"): p.add_argument("--note", default="")
        if name == "retire": p.add_argument("--force", action="store_true")
        if name == "build":
            p.add_argument("--import-batch-id", default="")
            p.add_argument("--source-artifact", default="")
            p.add_argument("--source-artifact-hash", default="")
            p.add_argument("--parent-revision-key", default="")
            p.add_argument("--note", default="")
    diff_p = sub.add_parser("diff"); diff_p.add_argument("--project-scope-key", required=True)
    diff_p.add_argument("--left", required=True); diff_p.add_argument("--right", required=True)
    clean = sub.add_parser("cleanup"); clean.add_argument("--project-scope-key", required=True)
    clean.add_argument("--keep", type=int, default=2); clean.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    runtime = tool_config.resolve_runtime(args); tool_config.print_runtime(runtime)
    with source_scope.open_connection(runtime.conninfo) as conn:
        ensure_schema(conn)
        if args.command == "schema": result = {"schema": "ready"}
        elif args.command == "monitor": result = monitor(conn, args.verify_source_hash)
        elif args.command == "active": result = active(conn, args.project_scope_key)
        elif args.command == "build": result = build(
            conn, args.project_scope_key, args.model_revision_key,
            {"import_batch_id": args.import_batch_id or None, "source_artifact": args.source_artifact or None,
             "source_artifact_hash": args.source_artifact_hash or None,
             "parent_revision_key": args.parent_revision_key or None, "note": args.note or None})
        elif args.command == "validate": result = validate_revision(conn, args.project_scope_key, args.model_revision_key)
        elif args.command == "promote": result = promote(conn, args.project_scope_key, args.model_revision_key, args.note)
        elif args.command == "rollback": result = promote(
            conn, args.project_scope_key, args.model_revision_key, args.note, action="ROLLBACK")
        elif args.command == "retire": result = retire(
            conn, args.project_scope_key, args.model_revision_key, args.force)
        elif args.command == "diff": result = diff(conn, args.project_scope_key, args.left, args.right)
        elif args.command == "cleanup": result = cleanup(conn, args.project_scope_key, args.keep, args.execute)
        else: raise ValueError(args.command)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
