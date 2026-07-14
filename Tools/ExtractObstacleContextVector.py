#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Route 시작/종점 주변의 BIM 구조 장애물을 30차원 Context Vector로 생성·저장한다.

실행 방법(PowerShell)
---------------------
1. 스키마 생성: python Tools/ExtractObstacleContextVector.py --config Tools/tools.settings.json create-schema
2. DB 저장 없이 통계 확인: python Tools/ExtractObstacleContextVector.py --config Tools/tools.settings.json extract --dry-run
3. 스키마+전체 생성: python Tools/ExtractObstacleContextVector.py --config Tools/tools.settings.json run-all
4. strict 리비전 생성: python Tools/ExtractObstacleContextVector.py --config Tools/tools.settings.json run-all `
   --project-scope-key DB:DDW_AI_DB --model-revision-key snapshot:<sha256>
5. 현황 조회: python Tools/ExtractObstacleContextVector.py --config Tools/tools.settings.json status

전체 흐름도
-----------
  [TB_BIM_OBSTACLE 기둥/보 AABB 1회 로드] --> [3D ObstacleIndex 구성]
                                                   |
  [TB_ROUTE_FEATURE_VECTOR + TB_ROUTE_PATH 좌표] --+
                                                   v
  [각 route의 시작/종점 500/1000mm shell + Tier3 계산]
                                                   |
                                                   v
  [30D L2 정규화 + metadata/provenance/snapshot hash]
                                                   |
                             dry-run --> [통계만 출력]
                             run     --> [TB_ROUTE_CONTEXT_VECTOR upsert]

주요 변수
---------
- ``project_scope_key/model_revision_key``: strict source revision 복합 식별자
- ``scope_resolution_status``: 공통 strict key 사용 여부 또는 global fallback 사유
- ``source_snapshot_hash``: 실제 인코딩에 사용한 장애물 집합의 내용 hash
- ``build_run_id``: 한 번의 전체 생성 작업을 묶는 UUID
- ``ENCODER_VERSION/ENCODER_CONFIG_HASH``: 저장/query 인코더 계약 검증값
"""

# ==============================================================================
# [실행명령어 예시]
#   1) 테이블 스키마 및 HNSW 인덱스 생성:
#      > python Tools/ExtractObstacleContextVector.py --password dinno create-schema
#   2) 컨텍스트 벡터 추출 (DB 미반영, 드라이런으로 로그만 확인):
#      > python Tools/ExtractObstacleContextVector.py --password dinno extract --dry-run
#   3) 스키마 생성 + 추출 + DB 적재 일괄 실행:
#      > python Tools/ExtractObstacleContextVector.py --password dinno run-all
# ==============================================================================

"""
[모듈 개요]
TB_ROUTE_PATH 각 경로의 시작 PoC(SOURCE_POSX/Y/Z)와 종료 PoC(TARGET_POSX/Y/Z)
주변에 배치된 BIM 장애물(기둥/보, TB_BIM_OBSTACLE)의 개수·표면거리·방향을 30차원
벡터로 인코딩하여 TB_ROUTE_CONTEXT_VECTOR에 저장한다.

RoutingAI/src/ContextVectorEncoder(D:\\DINNO\\DEV\\AI-AutoRouting\\RoutingAI)의
설계를 DDW_AI_DB용으로 포팅한 것이며, 상세 배경은
Docs/20260713_Learned Design Data Reuse Strategy.md 를 참조한다.

[핵심 설계 의도]
TB_ROUTE_FEATURE_VECTOR(Extract_Design_Pattern.py가 산출하는 30차원 형상벡터)의
env_cost 구간([22:25])은 경로 전체(꺾임, 우회 여부 등)가 있어야 계산 가능하므로,
아직 경로가 정해지지 않은 "신규 쿼리" 시점에는 0으로 채워 넣을 수밖에 없다
(TopKSearchStandalone.cs의 BuildQueryVector30D 참조).
반면 이 30차원 컨텍스트 벡터는 오직 "좌표 + 주변 장애물"만으로 계산되므로,
과거 경로를 색인할 때와 신규 쿼리 시점 모두 완전히 동일한 함수로 계산할 수 있다.
이 성질 덕분에 TopKSearchStandalone.cs가 쿼리 시점에 컨텍스트 벡터를 즉석에서
계산해 후보들과 코사인 유사도로 비교하는 것이 가능해진다.

[30차원 레이아웃] (context_vector_encoder.encode_context_vector 참조)
  [0:13]  시작 PoC — 두 shell의 기둥·보 배치 + 1000mm 내 free-space 표시
  [13:26] 종료 PoC — 시작과 동일한 레이아웃
  [26:30] Tier3 — 시작~종료 2점 경로 기준 보조 특징(층 전환수, 기둥 격자셀 수,
                  보-평행도, 수평 진행방향)
  전체 30차원은 마지막에 L2 정규화한다.

[중요한 실측 근거 — 반드시 재정렬/페어링 단계 전용으로만 사용할 것]
RoutingAI의 실측(TopK_ContextAware_Plan_v2.md)에 따르면, 이 컨텍스트 벡터를
1차 pgvector ANN 후보추출(FEATURE_VECTOR 검색) 자체에 섞으면 오히려 정확도가
떨어진다(기존 30D의 env_cost 구간과 정보가 중복되는 "천장효과"). 이미 후보군이
좁혀진 뒤의 재정렬 단계에서 별도 가중치 항목으로 사용했을 때만 유의미한 개선이
있었다(그룹 내 재정렬 Top-1 좌표오차 -37.8%, 페어링 정확도 +5.3%p).
TopKSearchStandalone.cs의 하이브리드 재정렬 4번째 항목(ctxScore)이 이 방식을
따른다.

[성능 설계]
TB_BIM_OBSTACLE은 컬럼/보 합계 약 16만 건으로, 경로(827건)마다 매번 DB에
범위질의를 던지면 매우 느리다. 따라서 컬럼/보 장애물 전체를 1회만 메모리에
적재한 뒤 자체 3D 격자 인덱스(ObstacleIndex)를 구성하고, 이후 모든 근접 탐색은
이 인메모리 인덱스로 처리한다(DB 왕복 없음).

[주요 함수]
- ObstacleIndex: 기둥/보 AABB를 셀 크기 1000mm 격자에 버킷팅한 인메모리 공간 인덱스
- encode_endpoint(idx, point): 한 점 주변 두 거리 shell과 free-space를 13차원으로 인코딩
- encode_tier3(idx, start, end, ...): 시작~종료 2점 경로의 보조 특징 4차원 계산
- encode_context_vector(idx, start, end): 시작+종료+Tier3을 이어붙여 L2 정규화한 30차원 반환
- extract_context_vectors(conn, dry_run): 전체 경로에 대해 컨텍스트 벡터를 일괄 계산
- save_context_vectors(conn, rows): TB_ROUTE_CONTEXT_VECTOR에 DELETE 후 재삽입
"""

import argparse
import hashlib
import json
import math
import sys
import uuid
from collections import Counter
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
except ImportError:  # Pure encoder/provenance unit tests do not require a DB driver.
    psycopg2 = None

sys.path.append(str(Path(__file__).resolve().parent))
import tool_config
from context_vector_encoder import (
    CONTEXT_VECTOR_DIM,
    CONTEXT_SCOPE_KIND,
    ENCODER_CONFIG,
    ENCODER_CONFIG_HASH,
    ENCODER_VERSION,
    MID_RADIUS_MM,
    NEAR_RADIUS_MM,
    Obstacle,
    ObstacleIndex,
    encode_context_vector,
)

COLUMN_TYPES = ("COLUMN_ARCHITECTURE", "COLUMN_STRUCTURE")
BEAM_TYPES = ("BEAM_ARCHITECTURE", "BEAM_STRUCTURE")


def open_connection(conninfo: str):
    """PostgreSQL 연결을 열며 driver/접속 오류를 명확한 종료 메시지로 변환한다."""
    """PostgreSQL 서버에 접속하고 커넥션 객체를 반환. 실패 시 SystemExit로 즉시 종료."""
    try:
        if psycopg2 is None:
            raise RuntimeError("psycopg2-binary is required; install Tools/requirements-context-ab.txt")
        return psycopg2.connect(conninfo)
    except Exception as ex:
        raise SystemExit(f"DB connection failed: {ex}")


def create_schema(conn) -> None:
    """Context table, 복합 PK, pgvector/HNSW index migration을 멱등 실행한다."""
    """Tools/sql/create_route_context_vector_table.sql 실행하여 TB_ROUTE_CONTEXT_VECTOR 생성."""
    sql_path = Path(__file__).resolve().parent / "sql" / "create_route_context_vector_table.sql"
    with conn.cursor() as cur:
        if sql_path.exists():
            print(f"Executing DDL from: {sql_path}")
            cur.execute(sql_path.read_text(encoding="utf-8"))
        else:
            raise FileNotFoundError(f"SQL file not found at {sql_path}")
    conn.commit()
    print("Schema TB_ROUTE_CONTEXT_VECTOR ready.")


def print_status(conn) -> None:
    """Feature 대비 Context coverage와 encoder/scope 계약별 건수를 출력한다."""
    """Print the current feature/context coverage and encoder-contract counts."""
    with conn.cursor() as cur:
        cur.execute(
            '''
            SELECT
                (SELECT COUNT(*) FROM "TB_ROUTE_FEATURE_VECTOR") AS feature_count,
                COUNT(*) AS context_count,
                COUNT(DISTINCT "ROUTE_PATH_GUID") AS unique_route_count,
                COUNT(*) FILTER (WHERE "ENCODER_VERSION" = %s) AS version_count,
                COUNT(*) FILTER (WHERE "SCOPE_KIND" = %s) AS scope_count,
                COUNT(DISTINCT "ENCODER_CONFIG_HASH") AS config_hash_count,
                COUNT(*) FILTER (WHERE vector_dims("CONTEXT_VECTOR") = %s) AS dimension_count
            FROM "TB_ROUTE_CONTEXT_VECTOR"
            ''',
            (ENCODER_VERSION, CONTEXT_SCOPE_KIND, CONTEXT_VECTOR_DIM),
        )
        (feature_count, context_count, unique_route_count, version_count,
         scope_count, hash_count, dimension_count) = cur.fetchone()
        cur.execute(
            '''
            SELECT "SCOPE_RESOLUTION_STATUS", "PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY",
                   COUNT(*), COUNT(DISTINCT "SOURCE_SNAPSHOT_HASH"),
                   MIN("SOURCE_OBSTACLE_COUNT"), MAX("SOURCE_OBSTACLE_COUNT"),
                   COUNT(DISTINCT "BUILD_RUN_ID")
            FROM "TB_ROUTE_CONTEXT_VECTOR"
            GROUP BY 1,2,3 ORDER BY 1,2,3
            '''
        )
        provenance_rows = cur.fetchall()

    coverage = unique_route_count / feature_count if feature_count else 0.0
    print(f"Feature vectors : {feature_count}")
    print(f"Context variants: {context_count}")
    print(f"Unique routes   : {unique_route_count}")
    print(f"Route coverage  : {coverage:.1%}")
    print(f"Version match   : {version_count}/{context_count} ({ENCODER_VERSION})")
    print(f"Scope match     : {scope_count}/{context_count} ({CONTEXT_SCOPE_KIND})")
    print(f"Vector dim match: {dimension_count}/{context_count} ({CONTEXT_VECTOR_DIM}D)")
    print(f"Config hashes   : {hash_count}")
    for status, project, revision, count, snapshots, min_obstacles, max_obstacles, runs in provenance_rows:
        print(
            f"Provenance      : status={status}, project={project!r}, revision={revision!r}, "
            f"rows={count}, snapshots={snapshots}, obstacles={min_obstacles}..{max_obstacles}, runs={runs}"
        )


def _validate_scope_keys(project_scope_key: str, model_revision_key: str) -> tuple[str, str]:
    """project와 revision은 둘 다 지정하거나 둘 다 비워야 한다는 계약을 검사한다."""
    project = (project_scope_key or "").strip()
    revision = (model_revision_key or "").strip()
    if bool(project) != bool(revision):
        raise ValueError("--project-scope-key and --model-revision-key must be provided together")
    return project, revision


def _obstacle_snapshot_hash(obstacles: list[Obstacle]) -> str:
    """장애물 ID/종류/AABB를 정렬 직렬화해 순서 독립적인 SHA-256을 만든다."""
    """Return a query-order-independent digest of the exact obstacle geometry used."""
    digest = hashlib.sha256()
    for obstacle in sorted(obstacles, key=lambda item: item.obstacle_id):
        digest.update(obstacle.obstacle_id.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def load_obstacle_index(
    conn, project_scope_key: str = "", model_revision_key: str = ""
) -> tuple[ObstacleIndex, str, int, dict]:
    """지정 scope의 구조 장애물을 한 번 읽어 공간 인덱스와 provenance를 구성한다.

    반환값은 ``(공간 인덱스, 장애물 snapshot hash, 장애물 수, scope 진단정보)``이다.
    strict mode에서는 project/revision이 정확히 일치하는 장애물만 읽는다.
    """
    """모든 BAY의 구조 장애물을 하나의 전역 좌표 공간 인덱스로 구성한다.

    현재 DB에는 project/revision 식별자가 없고 Feature Vector의 일부에는 BAY 계보가 없다.
    BAY 라벨로 미리 제외하지 않고 전역 인덱스를 사용하되, 실제 인코딩은 endpoint 1,000mm
    반경과 start-end 경로 바운딩박스 안의 장애물만 선택한다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='TB_BIM_OBSTACLE'"
        )
        columns = {row[0] for row in cur.fetchall()}
    id_column = next(
        (name for name in ("INSTANCE_ID", "INSTANCE_GUID", "OBSTACLE_GUID", "GUID", "INSTANCE_NAME") if name in columns),
        None,
    )
    project_scope_key, model_revision_key = _validate_scope_keys(
        project_scope_key, model_revision_key
    )
    strict_scope = bool(project_scope_key)
    id_select = f'"{id_column}"' if id_column else "NULL"
    sql = """
        SELECT {id_select}, "DDWORKS_TYPE",
               "AABB_MINX","AABB_MINY","AABB_MINZ","AABB_MAXX","AABB_MAXY","AABB_MAXZ"
        FROM "TB_BIM_OBSTACLE"
        WHERE "DDWORKS_TYPE" = ANY(%s)
    """.format(id_select=id_select)
    types = list(COLUMN_TYPES) + list(BEAM_TYPES)
    params = [types]
    if strict_scope:
        sql += ' AND "PROJECT_SCOPE_KEY" = %s AND "MODEL_REVISION_KEY" = %s'
        params.extend([project_scope_key, model_revision_key])
    obstacles = []
    with conn.cursor() as cur:
        cur.execute(sql, params)
        for raw_id, ddworks_type, minx, miny, minz, maxx, maxy, maxz in cur.fetchall():
            if None in (minx, miny, minz, maxx, maxy, maxz):
                continue
            kind = "COLUMN" if ddworks_type in COLUMN_TYPES else "BEAM"
            raw_key = str(raw_id).strip() if raw_id is not None else "NO_SOURCE_ID"
            obstacle_id = (
                f"{kind}|{raw_key}|{float(minx):.9g}|{float(miny):.9g}|{float(minz):.9g}|"
                f"{float(maxx):.9g}|{float(maxy):.9g}|{float(maxz):.9g}"
            )
            obstacles.append(Obstacle(
                obstacle_id=obstacle_id,
                kind=kind,
                minimum=(float(minx), float(miny), float(minz)),
                maximum=(float(maxx), float(maxy), float(maxz)),
            ))

    snapshot_hash = _obstacle_snapshot_hash(obstacles)
    scope_status = "STRICT_COMMON_KEY" if strict_scope else "GLOBAL_FALLBACK_NO_COMMON_KEY"
    diagnostic = {
        "scope_resolution_status": scope_status,
        "project_scope_key": project_scope_key,
        "model_revision_key": model_revision_key,
        "source_table": "TB_BIM_OBSTACLE",
        "included_types": sorted(types),
        "source_id_column": id_column,
        "reason": (
            "Exact common project/revision keys supplied"
            if strict_scope
            else "Legacy source rows have no reliable common project/revision key"
        ),
    }
    label = "strict scoped" if strict_scope else "global fallback"
    print(f"Loaded {len(obstacles)} column/beam obstacles into {label} spatial index.")
    print(f"Obstacle snapshot SHA-256: {snapshot_hash}")
    return ObstacleIndex(obstacles), snapshot_hash, len(obstacles), diagnostic


def extract_context_vectors(
    conn,
    dry_run: bool = False,
    project_scope_key: str = "",
    model_revision_key: str = "",
    build_run_id: str | None = None,
) -> list[dict]:
    """Feature route별 시작/종점 좌표를 읽어 저장 가능한 Context row를 일괄 계산한다.

    ``build_run_id``는 한 실행에서 생성된 모든 row를 묶고, ``skipped`` 집계는 좌표 오류 등
    개별 route 오류 때문에 전체 작업이 중단되지 않도록 진단에 사용한다.
    """
    """TB_ROUTE_FEATURE_VECTOR의 전체 경로를 대상으로 30D 컨텍스트를 계산한다.

    Feature 테이블 자체의 start/end 좌표를 사용하므로 TB_ROUTE_PATH 계보가 없는 확장·복제
    Feature 경로도 색인할 수 있다.
    """
    project_scope_key, model_revision_key = _validate_scope_keys(
        project_scope_key, model_revision_key
    )
    strict_scope = bool(project_scope_key)
    build_run_id = str(uuid.UUID(build_run_id)) if build_run_id else str(uuid.uuid4())
    sql = """
        SELECT fv."ROUTE_PATH_GUID", fv."START_POSX", fv."START_POSY", fv."START_POSZ",
               fv."END_POSX", fv."END_POSY", fv."END_POSZ", fv."PROCESS_NAME"
        FROM "TB_ROUTE_FEATURE_VECTOR" fv
        WHERE fv."ROUTE_PATH_GUID" IS NOT NULL
          AND fv."START_POSX" IS NOT NULL AND fv."START_POSY" IS NOT NULL AND fv."START_POSZ" IS NOT NULL
          AND fv."END_POSX" IS NOT NULL AND fv."END_POSY" IS NOT NULL AND fv."END_POSZ" IS NOT NULL
    """
    params = []
    if strict_scope:
        sql += ' AND fv."PROJECT_SCOPE_KEY" = %s AND fv."MODEL_REVISION_KEY" = %s'
        params.extend([project_scope_key, model_revision_key])
    with conn.cursor() as cur:
        cur.execute(sql, params)
        routes = cur.fetchall()
    print("Loading obstacle spatial index...")
    obstacle_index, snapshot_hash, obstacle_count, scope_diagnostic = load_obstacle_index(
        conn, project_scope_key, model_revision_key
    )
    print(f"Encoding context vectors for {len(routes)} routes...")

    rows = []
    skipped = Counter()
    for i, (guid, sx, sy, sz, tx, ty, tz, process_name) in enumerate(routes):
        if guid is None or any(value is None for value in (sx, sy, sz, tx, ty, tz)):
            skipped["missing_guid_or_coordinate"] += 1
            continue
        try:
            start = (float(sx), float(sy), float(sz))
            end = (float(tx), float(ty), float(tz))
            vec, meta = encode_context_vector(obstacle_index, start, end)
        except (TypeError, ValueError) as ex:
            skipped[type(ex).__name__] += 1
            if sum(skipped.values()) <= 5:
                print(f"  [warn] skipped route {str(guid).strip()}: {ex}")
            continue
        rows.append({
            "guid": str(guid).strip(),
            "vector": vec,
            "start_meta": meta["start"],
            "end_meta": meta["end"],
            "tier3_meta": meta["tier3"],
            "scope_kind": CONTEXT_SCOPE_KIND,
            "scope_value": "",
            "source_group": str(process_name or "").strip().upper(),
            "project_scope_key": project_scope_key,
            "model_revision_key": model_revision_key,
            "source_snapshot_hash": snapshot_hash,
            "scope_resolution_status": (
                "STRICT_COMMON_KEY" if strict_scope else "GLOBAL_FALLBACK_NO_COMMON_KEY"
            ),
            "source_obstacle_count": obstacle_count,
            "scope_diagnostic": scope_diagnostic,
            "build_run_id": build_run_id,
        })
        if (i + 1) % 200 == 0 or (i + 1) == len(routes):
            print(f"  Encoded {i + 1}/{len(routes)}...")

    print(f"Context vector encoding completed: {len(rows)} routes.")
    if skipped:
        print(f"Skipped invalid routes: {sum(skipped.values())} ({dict(skipped)})")
    if dry_run and rows:
        sample = rows[0]
        print(f"  Sample GUID={sample['guid'][:8]}... vector_head={[round(v, 4) for v in sample['vector'][:6]]}")
        print(f"  Sample start_meta={sample['start_meta']}")
        print(f"  Sample tier3_meta={sample['tier3_meta']}")
        print_dry_run_stats(rows)
    return rows


def _percentile(values: list[float], fraction: float) -> float | None:
    """선형 보간 방식으로 진단 통계의 percentile을 계산한다."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def print_dry_run_stats(rows: list[dict]) -> None:
    """DB를 변경하지 않고 coverage, shell 분포, scope/provenance 분포를 요약 출력한다."""
    """Print shell coverage and distance distribution without changing the DB."""
    print("Dry-run distribution:")
    print(f"  Source process={dict(Counter(row.get('source_group') or '<EMPTY>' for row in rows))}")
    for label, key in (("START", "start_meta"), ("END", "end_meta")):
        metas = [row[key] for row in rows]
        zero_near = sum(
            meta["column_near_count"] == 0 and meta["beam_near_count"] == 0 for meta in metas
        )
        zero_outer = sum(
            meta["column_near_count"] + meta["column_mid_count"]
            + meta["beam_near_count"] + meta["beam_mid_count"] == 0
            for meta in metas
        )
        nearest = []
        for meta in metas:
            distances = [
                value for value in (
                    meta["nearest_column_surface_mm"], meta["nearest_beam_surface_mm"]
                ) if value is not None
            ]
            if distances:
                nearest.append(min(distances))
        p50 = _percentile(nearest, 0.50)
        p95 = _percentile(nearest, 0.95)
        print(
            f"  {label}: zero<=500={zero_near}/{len(metas)} ({zero_near/max(len(metas),1):.1%}), "
            f"zero<=1000={zero_outer}/{len(metas)} ({zero_outer/max(len(metas),1):.1%}), "
            f"nearest_surface_p50={p50:.1f}mm, p95={p95:.1f}mm"
            if p50 is not None and p95 is not None
            else f"  {label}: no nearby obstacle distances"
        )
    norms = [math.sqrt(sum(value * value for value in row["vector"])) for row in rows]
    print(f"  vector_norm: min={min(norms):.9f}, max={max(norms):.9f}")


def save_context_vectors(conn, rows: list[dict]) -> None:
    """복합키(project, revision, route) 기준으로 Context와 provenance를 일괄 upsert한다."""
    """TB_ROUTE_CONTEXT_VECTOR에 기존 레코드를 DELETE 후 execute_batch()로 일괄 재삽입."""
    if not rows:
        print("No context vectors to save.")
        return

    sql = """
        INSERT INTO "TB_ROUTE_CONTEXT_VECTOR"
            ("ROUTE_PATH_GUID", "CONTEXT_VECTOR", "START_META_JSON", "END_META_JSON", "TIER3_META_JSON",
             "SCOPE_KIND", "SCOPE_VALUE", "PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY",
             "SOURCE_SNAPSHOT_HASH", "SCOPE_RESOLUTION_STATUS", "SOURCE_OBSTACLE_COUNT",
             "SCOPE_DIAGNOSTIC_JSON", "BUILD_RUN_ID",
             "ENCODER_VERSION", "ENCODER_CONFIG_JSON", "ENCODER_CONFIG_HASH")
        VALUES (%s, %s::vector, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s,
                %s, %s, %s, %s::jsonb, %s::uuid, %s, %s::jsonb, %s)
        ON CONFLICT ("PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY", "ROUTE_PATH_GUID") DO UPDATE SET
            "CONTEXT_VECTOR" = EXCLUDED."CONTEXT_VECTOR",
            "START_META_JSON" = EXCLUDED."START_META_JSON",
            "END_META_JSON" = EXCLUDED."END_META_JSON",
            "TIER3_META_JSON" = EXCLUDED."TIER3_META_JSON",
            "SCOPE_KIND" = EXCLUDED."SCOPE_KIND",
            "SCOPE_VALUE" = EXCLUDED."SCOPE_VALUE",
            "PROJECT_SCOPE_KEY" = EXCLUDED."PROJECT_SCOPE_KEY",
            "MODEL_REVISION_KEY" = EXCLUDED."MODEL_REVISION_KEY",
            "SOURCE_SNAPSHOT_HASH" = EXCLUDED."SOURCE_SNAPSHOT_HASH",
            "SCOPE_RESOLUTION_STATUS" = EXCLUDED."SCOPE_RESOLUTION_STATUS",
            "SOURCE_OBSTACLE_COUNT" = EXCLUDED."SOURCE_OBSTACLE_COUNT",
            "SCOPE_DIAGNOSTIC_JSON" = EXCLUDED."SCOPE_DIAGNOSTIC_JSON",
            "BUILD_RUN_ID" = EXCLUDED."BUILD_RUN_ID",
            "ENCODER_VERSION" = EXCLUDED."ENCODER_VERSION",
            "ENCODER_CONFIG_JSON" = EXCLUDED."ENCODER_CONFIG_JSON",
            "ENCODER_CONFIG_HASH" = EXCLUDED."ENCODER_CONFIG_HASH",
            "ENCODED_AT" = now()
    """
    values = []
    for r in rows:
        vec_literal = "[" + ",".join(f"{v:.9g}" for v in r["vector"]) + "]"
        values.append((
            r["guid"], vec_literal,
            json.dumps(r["start_meta"]), json.dumps(r["end_meta"]), json.dumps(r["tier3_meta"]),
            r["scope_kind"], r["scope_value"],
            r["project_scope_key"], r["model_revision_key"],
            r["source_snapshot_hash"], r["scope_resolution_status"],
            r["source_obstacle_count"], json.dumps(r["scope_diagnostic"]), r["build_run_id"],
            ENCODER_VERSION,
            json.dumps(ENCODER_CONFIG),
            ENCODER_CONFIG_HASH,
        ))

    with conn.cursor() as cur:
        project_scope_key = rows[0]["project_scope_key"]
        model_revision_key = rows[0]["model_revision_key"]
        cur.execute(
            'DELETE FROM "TB_ROUTE_CONTEXT_VECTOR" '
            'WHERE "PROJECT_SCOPE_KEY" = %s AND "MODEL_REVISION_KEY" = %s',
            (project_scope_key, model_revision_key),
        )
        print(f"Cleared previous records for scope ({project_scope_key!r}, {model_revision_key!r}).")
        psycopg2.extras.execute_batch(cur, sql, values, page_size=200)
    conn.commit()
    print(f"Successfully saved {len(values)} context vectors to database.")


def main() -> int:
    """create-schema/status/extract/run-all 명령을 해석해 생성 파이프라인을 실행한다."""
    """CLI 진입점: create-schema / extract / run-all 서브커맨드를 파싱해 해당 함수로 위임한다."""
    parser = argparse.ArgumentParser(description="Route Start/End Obstacle Context Vector Extractor")
    tool_config.add_common_args(parser)

    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    subparsers.add_parser("create-schema", help="Create TB_ROUTE_CONTEXT_VECTOR schema")
    subparsers.add_parser("status", help="Show context-vector coverage and encoder-contract status")

    extract_parser = subparsers.add_parser("extract", help="Extract obstacle context vectors")
    extract_parser.add_argument("--dry-run", action="store_true", help="Print stats without saving to DB")

    run_all_parser = subparsers.add_parser("run-all", help="Create schema and extract context vectors")
    run_all_parser.add_argument("--dry-run", action="store_true", help="Print stats without saving to DB")

    for command_parser in (extract_parser, run_all_parser):
        command_parser.add_argument("--project-scope-key", default="")
        command_parser.add_argument("--model-revision-key", default="")
        command_parser.add_argument("--build-run-id", default="", help="Optional UUID for reproducible audit grouping")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    runtime = tool_config.resolve_runtime(args)
    tool_config.print_runtime(runtime)
    conn = open_connection(runtime.conninfo)

    try:
        if args.command == "create-schema":
            create_schema(conn)
        elif args.command == "status":
            print_status(conn)
        elif args.command == "extract":
            rows = extract_context_vectors(
                conn, args.dry_run, args.project_scope_key, args.model_revision_key,
                args.build_run_id or None,
            )
            if not args.dry_run:
                save_context_vectors(conn, rows)
        elif args.command == "run-all":
            create_schema(conn)
            rows = extract_context_vectors(
                conn, args.dry_run, args.project_scope_key, args.model_revision_key,
                args.build_run_id or None,
            )
            if not args.dry_run:
                save_context_vectors(conn, rows)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
