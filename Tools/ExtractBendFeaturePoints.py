from __future__ import annotations

"""
장비명+유틸리티 배관 경로에서 꺾임특징점(Bend Feature Point)을 추출, 원인 분류, 빈도 집계하는 CLI 도구.

설계 문서: Docs/BendFeaturePoint_Development_Plan.md (7~9절, 8절 DB 설계)

전체 프로세스
--------------
0. ELBOW IP 복원 (`geometry_ip_restore.restore_polyline_ip`)
   - TB_ROUTE_SEGMENT_DETAIL 원본에서 ELBOW 세그먼트의 사선(P_in/P_out)을, 전후 직관을
     연장한 가상 교차점(IP)으로 대체한다. `ExtractStubPatterns.py::fetch_route_points()`와
     동일한 skew-line 최근접점 알고리즘을 공유 유틸로 분리해 사용한다 (4.5절, 7.0절).
0-1. Start/Trunk/End 재세그멘테이션 (`PathSegmenter.segment_route`)
   - IP 복원으로 정점 인덱스가 바뀌므로, 저장된 TB_ROUTE_PATH_SEGMENTATION을 재사용하지 않고
     PathSegmenter.segment_route()를 IP 복원 폴리라인에 직접 재적용한다 (7.0-1절).
1. 후보 꺾임점 추출: 50mm 지터 필터 + axis_snap 방향전환 판정으로 V→H/H→V/H→H/V→V를 분류한다.
2. 좌표 정규화: 설계 간 좌표 비교를 위해 순번(ordinal)과 구간 내 상대위치(0.1 단위 버킷)를 부여한다.
3. 원인(CAUSE) 분류: ZONE_CONSTRAINT → DESTINATION_ENTRY → OBSTACLE_AVOID → GROUP_ALIGNMENT
   → UNKNOWN 순으로 우선순위를 두고 판정한다 (7.3절).
4. 구조적 키(장비/유틸리티/전환유형/구간/상대위치)로 그룹핑해 min-samples 이상 반복된 조합만
   TB_ROUTE_BEND_FEATURE_PATTERN으로 승격한다 (7.4~7.5절).

모든 build/status/validate 명령은 BuildUtilityPipeGroupVectors.py와 동일하게 scope를 확정한다
(기본값 --scope-mode active: TB_ROUTE_SOURCE_SCOPE_MANIFEST의 ACTIVE 1건, 여러 개/0개면 실패).
서로 다른 scope/revision의 경로가 하나의 빈도 집계에 섞이지 않도록 build마다 해당 scope의
TB_ROUTE_BEND_FEATURE_POINT/PATTERN 행만 재계산/재적재한다 (v1.2, PROJECT_SCOPE_KEY/MODEL_REVISION_KEY 추가).

주요 실행 명령
--------------
스키마 생성:
    python Tools\\ExtractBendFeaturePoints.py create-schema --config Tools\\tools.settings.json

전체 추출 + 빈도 집계 (ACTIVE scope):
    python Tools\\ExtractBendFeaturePoints.py build --config Tools\\tools.settings.json --min-samples 3

특정 scope 지정:
    python Tools\\ExtractBendFeaturePoints.py build --config Tools\\tools.settings.json --min-samples 3 ^
        --scope-mode explicit --project-scope-key <PROJECT_SCOPE_KEY> --model-revision-key <MODEL_REVISION_KEY>

디버그용 소량 실행(DB 미기록):
    python Tools\\ExtractBendFeaturePoints.py build --config Tools\\tools.settings.json --limit 50 --dry-run

현재 적재 상태 확인:
    python Tools\\ExtractBendFeaturePoints.py status --config Tools\\tools.settings.json

무결성 검증:
    python Tools\\ExtractBendFeaturePoints.py validate --config Tools\\tools.settings.json

알려진 단순화(v1 범위)
----------------------
- TB_ROUTE_BEND_FEATURE_POINT/PATTERN은 매 build마다 전량 DELETE 후 재적재한다
  (BuildUtilityPipeGroupVectors.py류의 SOURCE_HASH 기반 증분 스킵은 이번 버전에서는 적용하지 않음).
- Start Stub DESTINATION_ENTRY는 SOURCE 장비 Anchor AABB face 축과 첫 꺾임 진입축을 대조한다.
- 그룹배관(다발) 특징점으로의 확장(Docs 18절)은 이번 버전에 포함하지 않는다.
"""

import argparse
import hashlib
import json
import math
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parent))

import psycopg2
import psycopg2.extras

import tool_config
from geometry_ip_restore import RestoredVertex, restore_polyline_ip
from PathSegmenter import AXIS_NAMES, axis_snap, dist, segment_route
from Extract_Design_Pattern import (
    bypass_side_from_obstacle,
    classify_obstacle_type,
    parse_pipe_diameter,
    point_aabb_distance,
    segment_aabb_distance,
)
from ExtractStubPatterns import Anchor, fetch_anchors, find_anchor, nearest_face, relative_pos


JITTER_THRESHOLD_MM = 50.0
STRAIGHT_COS_THRESHOLD = 0.999
CSF_BOUNDARY_Z = 13700.0
ZONE_CONSTRAINT_TOLERANCE_MM = 50.0
OBSTACLE_BBOX_PAD_MM = 5000.0
OBSTACLE_BASE_CLEARANCE_MM = 150.0
OBSTACLE_MIN_LIMIT_MM = 600.0
DEFAULT_MIN_SAMPLES = 3
OBSTACLE_GRID_CELL_MM = 2000.0
OBSTACLE_GRID_MAX_CELLS_PER_ITEM = 4096


@dataclass
class RouteInput:
    guid: str
    project_scope_key: str
    model_revision_key: str
    equipment_key: str
    utility_group: str
    utility: str
    size: str | None
    raw_segments: list[dict]
    source_pos: tuple[float, float, float] | None = None
    target_pos: tuple[float, float, float] | None = None
    source_anchor: Anchor | None = None


@dataclass
class BendCandidate:
    route_path_guid: str
    project_scope_key: str
    model_revision_key: str
    equipment_key: str
    utility_group: str
    utility: str
    ordinal_from_start: int
    ordinal_from_end: int
    segment_zone: str
    rel_position_bucket: float
    transition_type: str
    axis_before: str
    axis_after: str
    axis1: int
    axis2: int
    point: tuple[float, float, float]
    adjacent_before: tuple[float, float, float]
    adjacent_after: tuple[float, float, float]
    is_elbow_restored_ip: bool
    ip_restore_skew_dist_mm: float | None
    anchor_rel_position: tuple[float, float, float] | None = None
    is_horizontal_sequence: bool = False
    is_first_in_start_zone: bool = False
    cause: str = "UNKNOWN"
    cause_evidence: dict[str, Any] = field(default_factory=dict)
    bend_id: int | None = None


@dataclass
class BendPattern:
    pattern_id: str
    project_scope_key: str
    model_revision_key: str
    equipment_key: str
    utility_group: str
    utility: str
    transition_type: str
    segment_zone: str
    rel_position_bucket: float
    sample_count: int
    bend_instance_count: int
    total_routes_in_scope: int
    frequency_score: float
    dominant_cause: str
    cause_confidence: float
    cause_breakdown: dict[str, int]
    position_consistency: float | None
    representative_point: tuple[float, float, float] | None
    avg_position: tuple[float, float, float] | None
    position_std_mm: float | None
    avg_anchor_rel_position: tuple[float, float, float] | None
    anchor_rel_std: float | None
    member_candidates: list[BendCandidate]
    source_hash: str
    build_run_id: str


class ObstacleSpatialIndex:
    """장애물 AABB를 균일 3D grid에 배치해 선분 주변 후보만 빠르게 찾는다."""

    def __init__(self, obstacles: list[dict], cell_mm: float = OBSTACLE_GRID_CELL_MM):
        self.obstacles = obstacles
        self.cell_mm = cell_mm
        self.cells: dict[tuple[int, int, int], list[int]] = defaultdict(list)
        self.overflow: list[int] = []
        self.query_count = 0
        self.total_candidates = 0
        self.max_candidates = 0
        for index, obs in enumerate(obstacles):
            lo = self._cell(obs["minx"], obs["miny"], obs["minz"])
            hi = self._cell(obs["maxx"], obs["maxy"], obs["maxz"])
            cell_count = (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1)
            if cell_count > OBSTACLE_GRID_MAX_CELLS_PER_ITEM:
                self.overflow.append(index)
                continue
            for ix in range(lo[0], hi[0] + 1):
                for iy in range(lo[1], hi[1] + 1):
                    for iz in range(lo[2], hi[2] + 1):
                        self.cells[(ix, iy, iz)].append(index)

    def _cell(self, x: float, y: float, z: float) -> tuple[int, int, int]:
        return (math.floor(x / self.cell_mm), math.floor(y / self.cell_mm), math.floor(z / self.cell_mm))

    def query_segments(
        self,
        a: tuple[float, float, float],
        bend: tuple[float, float, float],
        b: tuple[float, float, float],
        margin_mm: float,
    ) -> list[dict]:
        lo = self._cell(
            min(a[0], bend[0], b[0]) - margin_mm,
            min(a[1], bend[1], b[1]) - margin_mm,
            min(a[2], bend[2], b[2]) - margin_mm,
        )
        hi = self._cell(
            max(a[0], bend[0], b[0]) + margin_mm,
            max(a[1], bend[1], b[1]) + margin_mm,
            max(a[2], bend[2], b[2]) + margin_mm,
        )
        indices = set(self.overflow)
        for ix in range(lo[0], hi[0] + 1):
            for iy in range(lo[1], hi[1] + 1):
                for iz in range(lo[2], hi[2] + 1):
                    indices.update(self.cells.get((ix, iy, iz), ()))
        count = len(indices)
        self.query_count += 1
        self.total_candidates += count
        self.max_candidates = max(self.max_candidates, count)
        return [self.obstacles[i] for i in indices]

    @property
    def average_candidates(self) -> float:
        return self.total_candidates / self.query_count if self.query_count else 0.0


# ----------------------------------------------------------------------------
# DB 스키마 소개 유틸 (프로젝트마다 컬럼명이 다를 수 있어 ExtractStubPatterns.py와 동일 패턴 사용)
# ----------------------------------------------------------------------------

def table_columns(conn, table: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
            (table,),
        )
        return {r[0] for r in cur.fetchall()}


def first_col(cols: set[str], *names: str) -> str | None:
    for name in names:
        if name in cols:
            return name
    return None


def _normalize(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _triple(x: Any, y: Any, z: Any) -> tuple[float, float, float] | None:
    if x is None or y is None or z is None:
        return None
    return (float(x), float(y), float(z))


def _report_progress(idx: int, total: int, label: str, every_pct: int = 20) -> None:
    """total이 충분히 클 때만 every_pct% 단위로 진행률을 출력한다 (Extract_Design_Pattern.py와 동일 관례)."""
    if total < 5:
        return
    step = max(1, total // (100 // every_pct))
    if (idx + 1) % step == 0 or idx + 1 == total:
        pct = int((idx + 1) / total * 100)
        print(f"  ... {label} {pct}% ({idx + 1}/{total})")


# ----------------------------------------------------------------------------
# 0단계: 원본 조회
# ----------------------------------------------------------------------------

def resolve_scope(conn, args: argparse.Namespace) -> tuple[str, str]:
    """ACTIVE 또는 explicit PROJECT_SCOPE_KEY/MODEL_REVISION_KEY를 확정한다.

    BuildUtilityPipeGroupVectors.py::resolve_scope()와 동일한 관례를 따른다. 이 도구가
    scope를 무시하고 TB_ROUTE_PATH 전체를 대상으로 build하면, 서로 다른 scope/revision의
    경로가 하나의 빈도 집계에 섞이고 UtilityPipeGroup Top-K가 join할 scope 축이 없어진다.
    """
    if getattr(args, "scope_mode", "active") == "explicit":
        project = _normalize(args.project_scope_key)
        revision = _normalize(args.model_revision_key)
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
    return _normalize(rows[0][0]), _normalize(rows[0][1])


def fetch_routes(conn, project_scope_key: str, model_revision_key: str, limit: int | None = None) -> list[RouteInput]:
    cols = table_columns(conn, "TB_ROUTE_PATH")
    equip_col = first_col(cols, "EQUIPMENT_TAG", "EQUIPMENT_NAME", "SOURCE_OWNER_NAME")
    utility_group_col = first_col(cols, "UTILITY_GROUP")
    utility_col = first_col(cols, "SOURCE_UTILITY", "UTILITY")
    size_col = first_col(cols, "SOURCE_SIZE", "SIZE")
    source_cols = [first_col(cols, f"SOURCE_POS{axis}") for axis in "XYZ"]
    target_cols = [first_col(cols, f"TARGET_POS{axis}") for axis in "XYZ"]

    select_parts = ['rp."ROUTE_PATH_GUID" AS guid']
    select_parts.append(f'rp."{equip_col}" AS equipment_key' if equip_col else "NULL AS equipment_key")
    select_parts.append(f'rp."{utility_group_col}" AS utility_group' if utility_group_col else "NULL AS utility_group")
    select_parts.append(f'rp."{utility_col}" AS utility' if utility_col else "NULL AS utility")
    select_parts.append(f'rp."{size_col}" AS size' if size_col else "NULL AS size")
    for prefix, selected in (("source", source_cols), ("target", target_cols)):
        for axis, col in zip("xyz", selected):
            select_parts.append(f'rp."{col}" AS {prefix}_{axis}' if col else f"NULL AS {prefix}_{axis}")

    limit_sql = "LIMIT %s" if limit else ""
    params: list[Any] = [project_scope_key, model_revision_key] + ([limit] if limit else [])
    meta_sql = f'''
        SELECT {", ".join(select_parts)}
        FROM "TB_ROUTE_PATH" rp
        WHERE rp."PROJECT_SCOPE_KEY" = %s AND rp."MODEL_REVISION_KEY" = %s
        ORDER BY rp."ROUTE_PATH_GUID"
        {limit_sql}
    '''
    print(f"[fetch] Querying TB_ROUTE_PATH for candidate routes (scope={project_scope_key}, revision={model_revision_key})...")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(meta_sql, params)
        meta_rows = {str(r["guid"]).strip(): r for r in cur.fetchall()}
    print(f"[fetch] Found {len(meta_rows)} candidate routes.")

    if not meta_rows:
        return []

    guid_list = list(meta_rows.keys())
    seg_sql = '''
        SELECT rp."ROUTE_PATH_GUID" AS guid,
               sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",
               sd."TO_POSX", sd."TO_POSY", sd."TO_POSZ", sd."TYPE"
        FROM "TB_ROUTE_PATH" rp
        JOIN "TB_ROUTE_SEGMENTS" rs ON rp."ROUTE_PATH_GUID" = rs."ROUTE_PATH_GUID"
        JOIN "TB_ROUTE_SEGMENT_DETAIL" sd ON rs."SEGMENT_GUID" = sd."SEGMENT_GUID"
        WHERE rp."ROUTE_PATH_GUID" = ANY(%s)
        ORDER BY rp."ROUTE_PATH_GUID", rs."ORDER", sd."ORDER"
    '''
    print("[fetch] Querying TB_ROUTE_SEGMENTS / TB_ROUTE_SEGMENT_DETAIL for polylines...")
    segments_by_guid: dict[str, list[dict]] = defaultdict(list)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(seg_sql, (guid_list,))
        for row in cur.fetchall():
            guid = str(row["guid"]).strip()
            fx, fy, fz = row["FROM_POSX"], row["FROM_POSY"], row["FROM_POSZ"]
            tx, ty, tz = row["TO_POSX"], row["TO_POSY"], row["TO_POSZ"]
            if None in (fx, fy, fz, tx, ty, tz):
                continue
            segments_by_guid[guid].append({
                "from": (float(fx), float(fy), float(fz)),
                "to": (float(tx), float(ty), float(tz)),
                "type": row["TYPE"],
            })

    routes: list[RouteInput] = []
    for guid, meta in meta_rows.items():
        raw_segments = segments_by_guid.get(guid)
        if not raw_segments:
            continue
        routes.append(RouteInput(
            guid=guid,
            project_scope_key=project_scope_key,
            model_revision_key=model_revision_key,
            equipment_key=_normalize(meta.get("equipment_key")) or "UNKNOWN",
            utility_group=_normalize(meta.get("utility_group")) or "UNKNOWN",
            utility=_normalize(meta.get("utility")) or "UNKNOWN",
            size=meta.get("size"),
            raw_segments=raw_segments,
            source_pos=_triple(meta.get("source_x"), meta.get("source_y"), meta.get("source_z")),
            target_pos=_triple(meta.get("target_x"), meta.get("target_y"), meta.get("target_z")),
        ))
    print(f"[fetch] Reconstructed {len(routes)} routes with valid segment data (of {len(meta_rows)} candidates).")
    return routes


def load_obstacles(conn, points_by_guid: dict[str, list[tuple[float, float, float]]]) -> list[dict]:
    all_points = [p for pts in points_by_guid.values() for p in pts]
    if not all_points:
        return []
    minx = min(p[0] for p in all_points) - OBSTACLE_BBOX_PAD_MM
    maxx = max(p[0] for p in all_points) + OBSTACLE_BBOX_PAD_MM
    miny = min(p[1] for p in all_points) - OBSTACLE_BBOX_PAD_MM
    maxy = max(p[1] for p in all_points) + OBSTACLE_BBOX_PAD_MM
    minz = min(p[2] for p in all_points) - OBSTACLE_BBOX_PAD_MM
    maxz = max(p[2] for p in all_points) + OBSTACLE_BBOX_PAD_MM

    sql = '''
        SELECT "INSTANCE_NAME", "OST_TYPE", "DDWORKS_TYPE",
               "AABB_MINX", "AABB_MINY", "AABB_MINZ", "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"
        FROM "TB_BIM_OBSTACLE"
        WHERE "AABB_MAXX" >= %s AND "AABB_MINX" <= %s
          AND "AABB_MAXY" >= %s AND "AABB_MINY" <= %s
          AND "AABB_MAXZ" >= %s AND "AABB_MINZ" <= %s
    '''
    print(f"[obstacles] Querying TB_BIM_OBSTACLE within route bbox +-{OBSTACLE_BBOX_PAD_MM:.0f}mm...")
    obstacles: list[dict] = []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (minx, maxx, miny, maxy, minz, maxz))
            for row in cur.fetchall():
                box = {
                    "name": row.get("INSTANCE_NAME"),
                    "minx": float(row.get("AABB_MINX") or 0.0),
                    "miny": float(row.get("AABB_MINY") or 0.0),
                    "minz": float(row.get("AABB_MINZ") or 0.0),
                    "maxx": float(row.get("AABB_MAXX") or 0.0),
                    "maxy": float(row.get("AABB_MAXY") or 0.0),
                    "maxz": float(row.get("AABB_MAXZ") or 0.0),
                }
                box["obstacle_type"] = classify_obstacle_type(box["name"], row.get("OST_TYPE"), row.get("DDWORKS_TYPE"))
                obstacles.append(box)
    except Exception as ex:
        print(f"[warn] Could not load TB_BIM_OBSTACLE: {ex}")
        conn.rollback()
    print(f"[obstacles] Loaded {len(obstacles)} obstacle AABBs.")
    return obstacles


def load_group_pitch_index(conn) -> dict[str, dict]:
    """TB_ROUTE_GROUP_PATTERN.MEMBER_GUIDS -> {group_id, pitch_mm, is_equal_spacing} 역인덱스.

    GROUP_ALIGNMENT 원인 판정에 사용한다 (7.3.4절). 테이블이 없는 환경에서도 동작하도록
    조회 실패는 무시하고 빈 인덱스를 반환한다.
    """
    print("[group] Querying TB_ROUTE_GROUP_PATTERN for GROUP_ALIGNMENT evidence...")
    idx: dict[str, dict] = {}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('SELECT "GROUP_ID", "MEMBER_GUIDS", "TRUNK_Z", "PITCH_MM", "IS_EQUAL_SPACING" FROM "TB_ROUTE_GROUP_PATTERN"')
            for row in cur.fetchall():
                member_guids = row.get("MEMBER_GUIDS") or []
                for guid in member_guids:
                    idx[str(guid).strip()] = {
                        "group_id": row.get("GROUP_ID"),
                        "pitch_mm": row.get("PITCH_MM"),
                        "trunk_z": row.get("TRUNK_Z"),
                        "is_equal_spacing": bool(row.get("IS_EQUAL_SPACING")),
                    }
    except Exception as ex:
        print(f"[warn] Could not load TB_ROUTE_GROUP_PATTERN: {ex}")
        conn.rollback()
    print(f"[group] Indexed {len(idx)} member routes across group patterns.")
    return idx


# ----------------------------------------------------------------------------
# 1~2단계: 후보 꺾임점 추출 + 정규화
# ----------------------------------------------------------------------------

def _sub(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cos_angle(v1: tuple[float, float, float], v2: tuple[float, float, float]) -> float:
    n1 = math.sqrt(sum(c * c for c in v1))
    n2 = math.sqrt(sum(c * c for c in v2))
    if n1 < 1e-9 or n2 < 1e-9:
        return 1.0
    return sum(a * b for a, b in zip(v1, v2)) / (n1 * n2)


def classify_transition(axis1: int, axis2: int) -> str:
    if axis1 == 2 and axis2 != 2:
        return "V_TO_H"
    if axis1 != 2 and axis2 == 2:
        return "H_TO_V"
    if axis1 != 2 and axis2 != 2:
        return "H_TO_H"
    return "V_TO_V"


def classify_zone(i: int, start_idx: int, end_idx: int) -> str:
    if i <= start_idx:
        return "START_STUB"
    if i >= end_idx:
        return "END_STUB"
    return "MIDDLE_TRUNK"


def _progress_ratio(zone_pts: list[tuple[float, float, float]], local_idx: int) -> float:
    if len(zone_pts) < 2 or local_idx <= 0:
        return 0.0
    local_idx = min(local_idx, len(zone_pts) - 1)
    total = sum(dist(zone_pts[k], zone_pts[k + 1]) for k in range(len(zone_pts) - 1))
    if total <= 1e-6:
        return 0.0
    partial = sum(dist(zone_pts[k], zone_pts[k + 1]) for k in range(local_idx))
    return partial / total


def compute_rel_position_bucket(
    zone: str, i: int, start_idx: int, end_idx: int,
    start_pts: list, middle_pts: list, end_pts: list,
) -> float:
    if zone == "START_STUB":
        zone_pts, local_idx = start_pts, i
    elif zone == "END_STUB":
        zone_pts, local_idx = end_pts, i - end_idx
    else:
        zone_pts, local_idx = middle_pts, i - start_idx
    ratio = _progress_ratio(zone_pts, local_idx)
    bucket = round(ratio * 10) / 10.0
    return max(0.0, min(1.0, bucket))


def extract_candidates(
    route: RouteInput,
    points: list[tuple[float, float, float]],
    restored: list[RestoredVertex],
    start_idx: int,
    end_idx: int,
    start_pts: list,
    middle_pts: list,
    end_pts: list,
) -> list[BendCandidate]:
    candidates: list[BendCandidate] = []
    n = len(points)
    for i in range(1, n - 1):
        seg_before = dist(points[i - 1], points[i])
        seg_after = dist(points[i], points[i + 1])
        if seg_before < JITTER_THRESHOLD_MM or seg_after < JITTER_THRESHOLD_MM:
            continue
        v1 = _sub(points[i], points[i - 1])
        v2 = _sub(points[i + 1], points[i])
        if _cos_angle(v1, v2) >= STRAIGHT_COS_THRESHOLD:
            continue

        axis1_full = axis_snap(v1)
        axis2_full = axis_snap(v2)
        axis1 = axis1_full // 2
        axis2 = axis2_full // 2
        transition = classify_transition(axis1, axis2)
        zone = classify_zone(i, start_idx, end_idx)
        rel_bucket = compute_rel_position_bucket(zone, i, start_idx, end_idx, start_pts, middle_pts, end_pts)
        vertex = restored[i]

        candidates.append(BendCandidate(
            route_path_guid=route.guid,
            project_scope_key=route.project_scope_key,
            model_revision_key=route.model_revision_key,
            equipment_key=route.equipment_key,
            utility_group=route.utility_group,
            utility=route.utility,
            ordinal_from_start=i,
            ordinal_from_end=n - 1 - i,
            segment_zone=zone,
            rel_position_bucket=rel_bucket,
            transition_type=transition,
            axis_before=AXIS_NAMES[axis1_full],
            axis_after=AXIS_NAMES[axis2_full],
            axis1=axis1,
            axis2=axis2,
            point=vertex.point,
            adjacent_before=points[i - 1],
            adjacent_after=points[i + 1],
            is_elbow_restored_ip=vertex.is_elbow_restored_ip,
            ip_restore_skew_dist_mm=vertex.skew_dist_mm,
            anchor_rel_position=relative_pos(route.source_anchor, vertex.point) if route.source_anchor else None,
        ))

    start_zone = [c for c in candidates if c.segment_zone == "START_STUB"]
    if start_zone:
        first = min(start_zone, key=lambda c: c.ordinal_from_start)
        first.is_first_in_start_zone = True

    # 수평 꺾임이 후보 순서상 연속 2회 이상이면 계단형/복합 수평 꺾임 구간으로 태깅한다.
    for left, right in zip(candidates, candidates[1:]):
        if left.transition_type == "H_TO_H" and right.transition_type == "H_TO_H":
            left.is_horizontal_sequence = True
            right.is_horizontal_sequence = True

    return candidates


# ----------------------------------------------------------------------------
# 3단계: 원인(CAUSE) 분류 (7.3절 우선순위: ZONE_CONSTRAINT -> DESTINATION_ENTRY ->
#         OBSTACLE_AVOID -> GROUP_ALIGNMENT -> UNKNOWN)
# ----------------------------------------------------------------------------

def classify_cause(
    candidate: BendCandidate,
    route: RouteInput,
    entry_dir: tuple[float, float, float] | None,
    obstacles: list[dict],
    group_pitch_index: dict[str, dict],
    obstacle_index: ObstacleSpatialIndex | None = None,
) -> tuple[str, dict[str, Any]]:
    point = candidate.point

    if abs(point[2] - CSF_BOUNDARY_Z) < ZONE_CONSTRAINT_TOLERANCE_MM:
        return "ZONE_CONSTRAINT", {"boundary": "CSF_Z_13700", "z_mm": round(point[2], 1)}

    if candidate.segment_zone == "END_STUB" and entry_dir is not None:
        entry_axis_full = axis_snap(entry_dir)
        if entry_axis_full // 2 == candidate.axis2:
            return "DESTINATION_ENTRY", {
                "entry_direction": AXIS_NAMES[entry_axis_full],
                "matched": "axis_after",
            }

    if candidate.segment_zone == "START_STUB" and candidate.is_first_in_start_zone and route.source_anchor and route.source_pos:
        face_id, face_offset = nearest_face(route.source_anchor, route.source_pos)
        first_axis_full = axis_snap(_sub(candidate.point, route.source_pos))
        if first_axis_full // 2 == face_id // 2:
            return "DESTINATION_ENTRY", {
                "reason": "start_stub_anchor_face_match",
                "anchor_name": route.source_anchor.name,
                "anchor_face": AXIS_NAMES[face_id],
                "first_axis": AXIS_NAMES[first_axis_full],
                "face_offset_mm": round(face_offset, 1),
            }

    diameter = parse_pipe_diameter(route.size) or 0.0
    required_clearance = diameter * 0.5 + OBSTACLE_BASE_CLEARANCE_MM
    limit_dist = max(required_clearance * 1.5, OBSTACLE_MIN_LIMIT_MM)
    nearby_obstacles = obstacle_index.query_segments(
        candidate.adjacent_before, point, candidate.adjacent_after, limit_dist
    ) if obstacle_index else obstacles
    nearest: tuple[float, dict] | None = None
    for obs in nearby_obstacles:
        before_dist, _, _ = segment_aabb_distance(candidate.adjacent_before, point, obs)
        after_dist, _, _ = segment_aabb_distance(point, candidate.adjacent_after, obs)
        d = min(before_dist, after_dist)
        if d <= limit_dist and (nearest is None or d < nearest[0]):
            nearest = (d, obs)
    if nearest is not None:
        d, obs = nearest
        return "OBSTACLE_AVOID", {
            "obstacle_name": obs.get("name"),
            "obstacle_type": obs.get("obstacle_type"),
            "nearest_dist_mm": round(d, 1),
            "bypass_side": bypass_side_from_obstacle(point, obs),
            "required_clearance_mm": round(required_clearance, 1),
        }

    grp = group_pitch_index.get(route.guid)
    trunk_z = grp.get("trunk_z") if grp else None
    if grp and grp.get("is_equal_spacing") and trunk_z is not None and abs(point[2] - float(trunk_z)) <= ZONE_CONSTRAINT_TOLERANCE_MM:
        return "GROUP_ALIGNMENT", {
            "group_id": grp.get("group_id"),
            "pitch_mm": grp.get("pitch_mm"),
            "trunk_z": trunk_z,
        }

    evidence: dict[str, Any] = {}
    if nearby_obstacles:
        d, obs = min(((point_aabb_distance(point, o), o) for o in nearby_obstacles), key=lambda t: t[0])
        evidence["nearest_obstacle"] = {"name": obs.get("name"), "dist_mm": round(d, 1)}
    return "UNKNOWN", evidence


# ----------------------------------------------------------------------------
# 4~5단계: 구조적 키 빈도 집계 + 신뢰도 스코어링
# ----------------------------------------------------------------------------

def aggregate_patterns(
    candidates: list[BendCandidate],
    route_scope_totals: Counter,
    min_samples: int,
    build_run_id: str,
    project_scope_key: str = "",
    model_revision_key: str = "",
) -> list[BendPattern]:
    grouped: dict[tuple, list[BendCandidate]] = defaultdict(list)
    for c in candidates:
        key = (c.equipment_key, c.utility_group, c.utility, c.transition_type, c.segment_zone, c.rel_position_bucket)
        grouped[key].append(c)
    print(f"  ... grouped {len(candidates)} candidates into {len(grouped)} structural keys "
          f"(promoting groups with >= {min_samples} distinct routes)")

    patterns: list[BendPattern] = []
    for key, items in grouped.items():
        distinct_routes = {c.route_path_guid for c in items}
        if len(distinct_routes) < min_samples:
            continue

        equipment_key, utility_group, utility, transition_type, segment_zone, rel_bucket = key
        total_routes = route_scope_totals.get((equipment_key, utility_group, utility), len(distinct_routes))
        frequency_score = min(1.0, len(distinct_routes) / total_routes) if total_routes else 0.0

        per_route: dict[str, BendCandidate] = {}
        for item in items:
            per_route.setdefault(item.route_path_guid, item)
        cause_counts = Counter(c.cause for c in per_route.values())
        dominant_cause, dominant_count = cause_counts.most_common(1)[0]
        cause_confidence = dominant_count / len(per_route)

        pts = [c.point for c in items]
        centroid = tuple(sum(p[k] for p in pts) / len(pts) for k in range(3))
        distances = [dist(p, centroid) for p in pts]
        position_std = math.sqrt(sum(d * d for d in distances) / len(distances)) if distances else 0.0
        representative = min(items, key=lambda c: dist(c.point, centroid)).point

        rels = [c.anchor_rel_position for c in per_route.values() if c.anchor_rel_position is not None]
        avg_rel = None
        rel_std = None
        position_consistency = None
        if rels:
            avg_rel = tuple(sum(p[k] for p in rels) / len(rels) for k in range(3))
            rel_distances = [dist(p, avg_rel) for p in rels]
            rel_std = math.sqrt(sum(d * d for d in rel_distances) / len(rel_distances))
            position_consistency = max(0.0, 1.0 - rel_std / math.sqrt(3.0))

        pattern_id = "bfp_" + hashlib.sha1(
            "|".join([
                project_scope_key, model_revision_key, equipment_key, utility_group, utility,
                transition_type, segment_zone, f"{rel_bucket:.2f}",
            ]).encode("utf-8")
        ).hexdigest()[:24]
        source_hash = hashlib.sha256(
            json.dumps(sorted(distinct_routes), ensure_ascii=False).encode("utf-8")
        ).hexdigest()

        patterns.append(BendPattern(
            pattern_id=pattern_id,
            project_scope_key=project_scope_key,
            model_revision_key=model_revision_key,
            equipment_key=equipment_key,
            utility_group=utility_group,
            utility=utility,
            transition_type=transition_type,
            segment_zone=segment_zone,
            rel_position_bucket=rel_bucket,
            sample_count=len(distinct_routes),
            bend_instance_count=len(items),
            total_routes_in_scope=total_routes,
            frequency_score=frequency_score,
            dominant_cause=dominant_cause,
            cause_confidence=cause_confidence,
            cause_breakdown=dict(cause_counts),
            position_consistency=position_consistency,
            representative_point=representative,
            avg_position=centroid,
            position_std_mm=position_std,
            avg_anchor_rel_position=avg_rel,
            anchor_rel_std=rel_std,
            member_candidates=items,
            source_hash=source_hash,
            build_run_id=build_run_id,
        ))
    print(f"  ... {len(patterns)} of {len(grouped)} structural keys promoted to patterns")
    return patterns


# ----------------------------------------------------------------------------
# DB 저장
# ----------------------------------------------------------------------------

def create_schema(conn) -> None:
    sql_path = Path(__file__).resolve().parent / "sql" / "create_bend_feature_tables.sql"
    print(f"[create-schema] Executing DDL from {sql_path} ...")
    with conn.cursor() as cur:
        cur.execute(sql_path.read_text(encoding="utf-8"))
    conn.commit()
    print("[create-schema] Schema TB_ROUTE_BEND_FEATURE_POINT / TB_ROUTE_BEND_FEATURE_PATTERN ready.")


def _point_wkt(pt: tuple[float, float, float]) -> str:
    return f"POINT Z ({pt[0]:.9g} {pt[1]:.9g} {pt[2]:.9g})"


def insert_points(conn, candidates: list[BendCandidate], build_run_id: str) -> list[int]:
    if not candidates:
        return []
    print(f"[insert] Building {len(candidates)} INSERT rows for TB_ROUTE_BEND_FEATURE_POINT...")
    rows = [
        (
            c.project_scope_key, c.model_revision_key,
            c.route_path_guid, c.equipment_key, c.utility_group, c.utility,
            c.ordinal_from_start, c.ordinal_from_end, c.segment_zone, c.rel_position_bucket,
            c.transition_type, c.axis_before, c.axis_after, c.cause,
            json.dumps(c.cause_evidence, ensure_ascii=False),
            c.is_elbow_restored_ip, c.ip_restore_skew_dist_mm,
            json.dumps(c.anchor_rel_position) if c.anchor_rel_position else None,
            c.is_horizontal_sequence,
            _point_wkt(c.point), build_run_id,
        )
        for c in candidates
    ]
    sql = '''
        INSERT INTO "TB_ROUTE_BEND_FEATURE_POINT" (
            "PROJECT_SCOPE_KEY","MODEL_REVISION_KEY",
            "ROUTE_PATH_GUID","EQUIPMENT_KEY","UTILITY_GROUP","UTILITY",
            "ORDINAL_FROM_START","ORDINAL_FROM_END","SEGMENT_ZONE","REL_POSITION_BUCKET",
            "TRANSITION_TYPE","AXIS_BEFORE","AXIS_AFTER","CAUSE","CAUSE_EVIDENCE",
            "IS_ELBOW_RESTORED_IP","IP_RESTORE_SKEW_DIST_MM","ANCHOR_REL_POSITION",
            "IS_HORIZONTAL_SEQUENCE","POINT_3D","BUILD_RUN_ID"
        ) VALUES %s
        RETURNING "BEND_ID"
    '''
    template = '(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,ST_GeomFromText(%s,0),%s)'
    with conn.cursor() as cur:
        results = psycopg2.extras.execute_values(cur, sql, rows, template=template, page_size=500, fetch=True)
    print(f"[insert] {len(results)} rows inserted into TB_ROUTE_BEND_FEATURE_POINT.")
    return [r[0] for r in results]


def insert_patterns(conn, patterns: list[BendPattern]) -> None:
    if not patterns:
        return
    print(f"[insert] Building {len(patterns)} INSERT rows for TB_ROUTE_BEND_FEATURE_PATTERN...")
    rows = []
    for p in patterns:
        member_ids = [c.bend_id for c in p.member_candidates if c.bend_id is not None]
        rows.append((
            p.pattern_id, p.project_scope_key, p.model_revision_key,
            p.equipment_key, p.utility_group, p.utility, p.transition_type, p.segment_zone,
            p.rel_position_bucket, p.sample_count, p.bend_instance_count, p.total_routes_in_scope, p.frequency_score,
            p.dominant_cause, p.cause_confidence, json.dumps(p.cause_breakdown, ensure_ascii=False),
            p.position_consistency,
            _point_wkt(p.representative_point) if p.representative_point else None,
            _point_wkt(p.avg_position) if p.avg_position else None,
            p.position_std_mm,
            json.dumps(p.avg_anchor_rel_position) if p.avg_anchor_rel_position else None,
            p.anchor_rel_std, json.dumps(member_ids), p.source_hash, p.build_run_id,
        ))
    sql = '''
        INSERT INTO "TB_ROUTE_BEND_FEATURE_PATTERN" (
            "PATTERN_ID","PROJECT_SCOPE_KEY","MODEL_REVISION_KEY",
            "EQUIPMENT_KEY","UTILITY_GROUP","UTILITY","TRANSITION_TYPE","SEGMENT_ZONE",
            "REL_POSITION_BUCKET","SAMPLE_COUNT","BEND_INSTANCE_COUNT","TOTAL_ROUTES_IN_SCOPE","FREQUENCY_SCORE",
            "DOMINANT_CAUSE","CAUSE_CONFIDENCE","CAUSE_BREAKDOWN","POSITION_CONSISTENCY",
            "REPRESENTATIVE_POINT","AVG_POSITION","POSITION_STD_MM","AVG_ANCHOR_REL_POSITION",
            "ANCHOR_REL_STD","MEMBER_BEND_IDS",
            "SOURCE_HASH","BUILD_RUN_ID"
        ) VALUES %s
    '''
    template = (
        '(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,'
        'ST_GeomFromText(%s,0),ST_GeomFromText(%s,0),%s,%s::jsonb,%s,%s::jsonb,%s,%s)'
    )
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, template=template, page_size=200)
    print(f"[insert] {len(rows)} rows inserted into TB_ROUTE_BEND_FEATURE_PATTERN.")


# ----------------------------------------------------------------------------
# build 파이프라인
# ----------------------------------------------------------------------------

def build(conn, args: argparse.Namespace) -> dict[str, Any]:
    build_run_id = str(uuid.uuid4())
    project_scope_key, model_revision_key = resolve_scope(conn, args)
    print(f"=== Bend Feature Point build started (build_run_id={build_run_id}, "
          f"scope={project_scope_key}, revision={model_revision_key}) ===")

    # 쓰기 빌드는 DDL을 먼저 적용해 기존 v1 테이블에도 v1.1 추가 컬럼을 자동 마이그레이션한다.
    # 이 단계가 없으면 create-schema를 다시 실행하지 않은 DB에서 INSERT 시 UndefinedColumn이 난다.
    if not args.dry_run:
        print("[schema] Applying create/upgrade DDL before build...")
        create_schema(conn)

    print("[1/5] Fetching routes...")
    routes = fetch_routes(conn, project_scope_key, model_revision_key, args.limit)
    if not routes:
        print("[1/5] No routes found. Nothing to do.")
        return {"build_run_id": build_run_id, "routes_processed": 0, "candidates_found": 0, "patterns_promoted": 0}

    print(f"[2/5] Restoring ELBOW IP for {len(routes)} routes...")
    points_by_guid: dict[str, list[tuple[float, float, float]]] = {}
    restored_by_guid: dict[str, list[RestoredVertex]] = {}
    for idx, route in enumerate(routes):
        restored = restore_polyline_ip(route.raw_segments)
        points = [v.point for v in restored]
        if len(points) < 3:
            continue
        points_by_guid[route.guid] = points
        restored_by_guid[route.guid] = restored
        _report_progress(idx, len(routes), "IP restore")
    print(f"[2/5] {len(points_by_guid)} routes have a usable polyline (>= 3 points).")

    print("[2/5] Matching source equipment anchors...")
    equipment_anchors = fetch_anchors(conn, "EQUIP")
    for route in routes:
        route.source_anchor = find_anchor(
            equipment_anchors, route.source_pos, route.equipment_key, route.utility
        )

    obstacles = load_obstacles(conn, points_by_guid)
    print(f"[obstacles] Building {OBSTACLE_GRID_CELL_MM:.0f}mm spatial grid...")
    obstacle_index = ObstacleSpatialIndex(obstacles)
    print(f"[obstacles] Spatial grid ready: {len(obstacle_index.cells)} cells, "
          f"{len(obstacle_index.overflow)} oversized obstacles in overflow list.")
    group_pitch_index = load_group_pitch_index(conn)

    route_scope_totals: Counter = Counter()
    for route in routes:
        route_scope_totals[(route.equipment_key, route.utility_group, route.utility)] += 1

    print(f"[3/5] Segmenting + extracting bend candidates for {len(points_by_guid)} routes...")
    all_candidates: list[BendCandidate] = []
    for idx, route in enumerate(routes):
        points = points_by_guid.get(route.guid)
        if not points:
            continue
        restored = restored_by_guid[route.guid]
        start_pts, middle_pts, end_pts, _start_fp, _end_fp, entry_dir = segment_route(points)
        if not start_pts or not end_pts:
            continue
        start_idx = len(start_pts) - 1
        end_idx = len(points) - len(end_pts)

        candidates = extract_candidates(route, points, restored, start_idx, end_idx, start_pts, middle_pts, end_pts)
        for c in candidates:
            c.cause, c.cause_evidence = classify_cause(
                c, route, entry_dir, obstacles, group_pitch_index, obstacle_index
            )
        all_candidates.extend(candidates)
        _report_progress(idx, len(routes), "candidate extraction")
    print(f"[3/5] Found {len(all_candidates)} bend candidates.")
    print(f"[3/5] Obstacle spatial queries: {obstacle_index.query_count}, "
          f"avg candidates={obstacle_index.average_candidates:.1f}, "
          f"max candidates={obstacle_index.max_candidates} "
          f"(from {len(obstacles)} total obstacles).")

    report: dict[str, Any] = {
        "build_run_id": build_run_id,
        "project_scope_key": project_scope_key,
        "model_revision_key": model_revision_key,
        "routes_fetched": len(routes),
        "routes_processed": len(points_by_guid),
        "candidates_found": len(all_candidates),
        "cause_breakdown": dict(Counter(c.cause for c in all_candidates)),
        "obstacles_in_scope": len(obstacles),
        "dry_run": bool(args.dry_run),
    }
    print(f"[4/5] Cause breakdown: {report['cause_breakdown']}")

    print(f"[5/5] Aggregating patterns (min-samples={args.min_samples})...")
    if args.dry_run:
        patterns = aggregate_patterns(
            all_candidates, route_scope_totals, args.min_samples, build_run_id,
            project_scope_key, model_revision_key,
        )
        report["patterns_promoted"] = len(patterns)
        print(f"[5/5] {len(patterns)} patterns would be promoted (dry-run, nothing written to DB).")
        print("=== Bend Feature Point build finished (dry-run) ===")
        return report

    print(f"[5/5] Deleting previous rows for scope={project_scope_key}, revision={model_revision_key}...")
    with conn.cursor() as cur:
        cur.execute(
            'DELETE FROM "TB_ROUTE_BEND_FEATURE_PATTERN" WHERE "PROJECT_SCOPE_KEY" = %s AND "MODEL_REVISION_KEY" = %s',
            (project_scope_key, model_revision_key),
        )
        cur.execute(
            'DELETE FROM "TB_ROUTE_BEND_FEATURE_POINT" WHERE "PROJECT_SCOPE_KEY" = %s AND "MODEL_REVISION_KEY" = %s',
            (project_scope_key, model_revision_key),
        )

    print(f"[5/5] Inserting {len(all_candidates)} bend points...")
    bend_ids = insert_points(conn, all_candidates, build_run_id)
    for c, bid in zip(all_candidates, bend_ids):
        c.bend_id = bid

    patterns = aggregate_patterns(
        all_candidates, route_scope_totals, args.min_samples, build_run_id,
        project_scope_key, model_revision_key,
    )
    print(f"[5/5] Inserting {len(patterns)} promoted patterns...")
    insert_patterns(conn, patterns)
    conn.commit()
    print("[5/5] Committed.")

    report["points_saved"] = len(bend_ids)
    report["patterns_promoted"] = len(patterns)
    unknown = report["cause_breakdown"].get("UNKNOWN", 0)
    report["unknown_ratio"] = round(unknown / len(all_candidates), 4) if all_candidates else 0.0
    print("=== Bend Feature Point build finished ===")
    return report


def status(conn, project_scope_key: str, model_revision_key: str) -> dict[str, Any]:
    print(f"[status] Counting TB_ROUTE_BEND_FEATURE_POINT rows (scope={project_scope_key}, revision={model_revision_key})...")
    scope_params = (project_scope_key, model_revision_key)
    with conn.cursor() as cur:
        cur.execute(
            'SELECT COUNT(*) FROM "TB_ROUTE_BEND_FEATURE_POINT" WHERE "PROJECT_SCOPE_KEY" = %s AND "MODEL_REVISION_KEY" = %s',
            scope_params,
        )
        point_count = cur.fetchone()[0]
        print(f"[status] point_count={point_count}. Grouping by CAUSE...")
        cur.execute(
            'SELECT "CAUSE", COUNT(*) FROM "TB_ROUTE_BEND_FEATURE_POINT" '
            'WHERE "PROJECT_SCOPE_KEY" = %s AND "MODEL_REVISION_KEY" = %s GROUP BY "CAUSE" ORDER BY "CAUSE"',
            scope_params,
        )
        cause_breakdown = {row[0]: row[1] for row in cur.fetchall()}
        print("[status] Counting TB_ROUTE_BEND_FEATURE_PATTERN rows...")
        cur.execute(
            'SELECT COUNT(*) FROM "TB_ROUTE_BEND_FEATURE_PATTERN" WHERE "PROJECT_SCOPE_KEY" = %s AND "MODEL_REVISION_KEY" = %s',
            scope_params,
        )
        pattern_count = cur.fetchone()[0]
        print(f"[status] pattern_count={pattern_count}. Checking CREATED_AT range...")
        cur.execute(
            'SELECT MIN("CREATED_AT"), MAX("CREATED_AT") FROM "TB_ROUTE_BEND_FEATURE_POINT" '
            'WHERE "PROJECT_SCOPE_KEY" = %s AND "MODEL_REVISION_KEY" = %s',
            scope_params,
        )
        min_created, max_created = cur.fetchone()
    unknown_ratio = (cause_breakdown.get("UNKNOWN", 0) / point_count) if point_count else 0.0
    print("[status] Done.")
    return {
        "project_scope_key": project_scope_key,
        "model_revision_key": model_revision_key,
        "point_count": point_count,
        "pattern_count": pattern_count,
        "cause_breakdown": cause_breakdown,
        "unknown_ratio": round(unknown_ratio, 4),
        "created_at_min": min_created.isoformat() if min_created else None,
        "created_at_max": max_created.isoformat() if max_created else None,
    }


def validate(conn, project_scope_key: str, model_revision_key: str) -> dict[str, Any]:
    errors: list[str] = []
    scope_params = (project_scope_key, model_revision_key)
    print(f"[validate] Checking MEMBER_BEND_IDS length vs BEND_INSTANCE_COUNT (scope={project_scope_key}, revision={model_revision_key})...")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            'SELECT "PATTERN_ID","SAMPLE_COUNT","BEND_INSTANCE_COUNT","TOTAL_ROUTES_IN_SCOPE","MEMBER_BEND_IDS" '
            'FROM "TB_ROUTE_BEND_FEATURE_PATTERN" WHERE "PROJECT_SCOPE_KEY" = %s AND "MODEL_REVISION_KEY" = %s',
            scope_params,
        )
        for row in cur.fetchall():
            member_ids = row["MEMBER_BEND_IDS"] or []
            if len(member_ids) != row["BEND_INSTANCE_COUNT"]:
                errors.append(f'{row["PATTERN_ID"]}: MEMBER_BEND_IDS length {len(member_ids)} != BEND_INSTANCE_COUNT {row["BEND_INSTANCE_COUNT"]}')
            if row["SAMPLE_COUNT"] > row["TOTAL_ROUTES_IN_SCOPE"]:
                errors.append(f'{row["PATTERN_ID"]}: SAMPLE_COUNT exceeds TOTAL_ROUTES_IN_SCOPE')

        print("[validate] Checking MEMBER_BEND_IDS for dangling references...")
        cur.execute('''
            SELECT p."PATTERN_ID"
            FROM "TB_ROUTE_BEND_FEATURE_PATTERN" p
            WHERE p."PROJECT_SCOPE_KEY" = %s AND p."MODEL_REVISION_KEY" = %s
              AND EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(p."MEMBER_BEND_IDS") bid
                WHERE NOT EXISTS (
                    SELECT 1 FROM "TB_ROUTE_BEND_FEATURE_POINT" bp WHERE bp."BEND_ID" = bid::bigint
                )
            )
        ''', scope_params)
        for row in cur.fetchall():
            errors.append(f'{row["PATTERN_ID"]}: dangling MEMBER_BEND_IDS reference')

        print("[validate] Checking FREQUENCY_SCORE range...")
        cur.execute(
            'SELECT COUNT(*) FROM "TB_ROUTE_BEND_FEATURE_PATTERN" '
            'WHERE "PROJECT_SCOPE_KEY" = %s AND "MODEL_REVISION_KEY" = %s AND ("FREQUENCY_SCORE" < 0 OR "FREQUENCY_SCORE" > 1)',
            scope_params,
        )
        bad_freq = cur.fetchone()[0]
        if bad_freq:
            errors.append(f'{bad_freq} pattern rows with FREQUENCY_SCORE out of [0,1]')

    print(f"[validate] Done. {len(errors)} error(s) found." if errors else "[validate] Done. No errors found.")
    return {"valid": not errors, "errors": errors}


def _write_report(report: dict[str, Any], path_value: str | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(text)
    if path_value:
        path = Path(path_value)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"[saved] {path.resolve()}")


def main() -> int:
    # 표준출력이 파이프/파일로 리다이렉트된 경우 Python은 완전 버퍼링 모드로 전환되어,
    # 프로세스가 끝날 때까지 print() 출력이 화면에 전혀 보이지 않을 수 있다.
    # 처리 단계별 메시지를 실시간으로 보여주기 위해 줄 단위 버퍼링으로 강제 전환한다.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="Extract, classify, and aggregate pipe routing bend feature points.")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ["create-schema", "build", "status", "validate"]:
        p = sub.add_parser(name)
        tool_config.add_common_args(p)

    for name in ["build", "status", "validate"]:
        p = sub.choices[name]
        p.add_argument("--scope-mode", choices=("active", "explicit"), default="active",
                        help="active: TB_ROUTE_SOURCE_SCOPE_MANIFEST의 ACTIVE 1건을 사용 (기본값). "
                             "explicit: --project-scope-key/--model-revision-key를 직접 지정.")
        p.add_argument("--project-scope-key", default="")
        p.add_argument("--model-revision-key", default="")

    build_p = sub.choices["build"]
    build_p.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    build_p.add_argument("--limit", type=int, default=None, help="Debug: limit number of routes processed")
    build_p.add_argument("--dry-run", action="store_true")
    build_p.add_argument("--report-out", default=None)

    for name in ["status", "validate"]:
        sub.choices[name].add_argument("--report-out", default=None)

    args = parser.parse_args()
    if args.command == "build" and args.limit and not args.dry_run:
        parser.error("--limit is diagnostic-only and must be used together with --dry-run; a limited build cannot replace full tables.")
    try:
        runtime = tool_config.resolve_runtime(args)
    except FileNotFoundError as ex:
        raise SystemExit(str(ex)) from ex

    with psycopg2.connect(runtime.conninfo) as conn:
        if args.command == "create-schema":
            create_schema(conn)
            return 0
        if args.command == "build":
            report = build(conn, args)
            _write_report(report, args.report_out)
            return 0
        if args.command == "status":
            project_scope_key, model_revision_key = resolve_scope(conn, args)
            report = status(conn, project_scope_key, model_revision_key)
            _write_report(report, args.report_out)
            return 0
        if args.command == "validate":
            project_scope_key, model_revision_key = resolve_scope(conn, args)
            report = validate(conn, project_scope_key, model_revision_key)
            _write_report(report, args.report_out)
            return 0 if report["valid"] else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
