#!/usr/bin/env python
# -*- coding: utf-8 -*-

# ==============================================================================
# [실행 명령어 및 도구 안내]
# 본 스크립트는 기존 설계 데이터로부터 평행하게 설치된 다발배관(Bundle) 그룹패턴을 
# 세그먼트 레벨 스캔 알고리즘을 통해 탐지하고 데이터베이스에 적재하는 도구입니다.
# 추가로 탐지된 다발배관의 3D 공간 기하 정보를 PNG 이미지 파일로 저장할 수 있습니다.
#
# 1. 데이터베이스 스키마 및 인덱스 생성:
#    > python Tools/DesignPatternAnalyzer.py --password dinno create-schema
#
# 2. 다발배관 패턴 분석 및 3D 렌더링 이미지 내보내기 (DB 적재 없음):
#    > python Tools/DesignPatternAnalyzer.py --password dinno extract --dry-run --image-out "data/output/images"
#
# 3. 스키마 생성 및 패턴 분석 + DB 적재 + 3D 이미지 내보내기 일괄 실행:
#    > python Tools/DesignPatternAnalyzer.py --password dinno run-all --image-out "data/output/images"
# ==============================================================================

import sys
import os
import math
import json
import hashlib
import uuid
import argparse
from pathlib import Path
from collections import defaultdict, Counter

# Add parent directory to sys.path to resolve tool_config correctly
sys.path.append(str(Path(__file__).resolve().parent))
import tool_config
import psycopg2
import psycopg2.extras

# Constants
ARROW_TOL = 0.9
RESAMPLE_N = 20
PITCH_CV_MAX = 0.30
SIM_THRESHOLD = 0.70

# Caches for performance optimization
_lev_cache = {}
_similarity_cache = {}


def open_connection(conninfo: str):
    try:
        return psycopg2.connect(conninfo)
    except Exception as ex:
        raise SystemExit(f"DB connection failed: {ex}")


def table_exists(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name=%s",
            (table,),
        )
        return cur.fetchone()[0] > 0


def pgvector_installed(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM pg_extension WHERE extname='vector'")
        return cur.fetchone()[0] > 0


def create_schema(conn) -> None:
    has_vector = pgvector_installed(conn)
    with conn.cursor() as cur:
        if has_vector:
            sql_path = Path(__file__).resolve().parent / "sql" / "create_route_group_pattern_tables.sql"
            if sql_path.exists():
                print(f"Executing DDL from: {sql_path}")
                cur.execute(sql_path.read_text(encoding="utf-8"))
            else:
                print(f"[warn] SQL schema file {sql_path} not found. Using raw execution.")
                cur.execute(fallback_schema_sql(with_vector=True))
        else:
            print("[warn] pgvector extension not found. Creating JSON fallback columns only.")
            cur.execute(fallback_schema_sql(with_vector=False))
    conn.commit()
    print(f"Schema configuration ready. pgvector={'yes' if has_vector else 'no'}")


def fallback_schema_sql(with_vector: bool) -> str:
    vector_col = '"FEAT" vector(60),' if with_vector else ""
    vector_idx = 'CREATE INDEX IF NOT EXISTS "IX_TRGP_FEAT_HNSW" ON "TB_ROUTE_GROUP_PATTERN" USING hnsw ("FEAT" vector_l2_ops);' if with_vector else ""
    
    return f"""
DROP TABLE IF EXISTS "TB_ROUTE_GROUP_PATTERN" CASCADE;
CREATE TABLE IF NOT EXISTS "TB_ROUTE_GROUP_PATTERN" (
    "GROUP_ID" text PRIMARY KEY,
    "EQUIPMENT_TAG" text NOT NULL,
    "UTILITY_GROUP" text NOT NULL,
    "UTILITY" text NOT NULL,
    "N_MEMBERS" integer NOT NULL,
    "AVG_SIMILARITY" double precision NOT NULL,
    "TRUNK_Z" double precision NOT NULL,
    "TRUNK_XY_SPREAD" double precision NOT NULL,
    "PITCH_MM" double precision NOT NULL,
    "N_ORTHO_BENDS" integer NOT NULL,
    "MEMBER_GUIDS" jsonb NOT NULL,
    "PATTERN_SEQ" text,
    "SECTION_BOUNDS" jsonb,
    {vector_col}
    "FEAT_JSON" jsonb,
    "GEOM_3D" geometry(MultiLineStringZ, 0),
    "TRUNK_GEOM_3D" geometry(MultiLineStringZ, 0),
    "TRUNK_LEN" double precision NOT NULL DEFAULT 0.0,
    "CREATED_AT" timestamp without time zone DEFAULT now()
);
CREATE INDEX IF NOT EXISTS "IX_TRGP_KEY"
ON "TB_ROUTE_GROUP_PATTERN" ("EQUIPMENT_TAG", "UTILITY_GROUP", "UTILITY");
{vector_idx}
CREATE INDEX IF NOT EXISTS "IX_TRGP_GEOM"
ON "TB_ROUTE_GROUP_PATTERN" USING gist("GEOM_3D");
CREATE INDEX IF NOT EXISTS "IX_TRGP_TRUNK_GEOM"
ON "TB_ROUTE_GROUP_PATTERN" USING gist("TRUNK_GEOM_3D");
"""


# --- Geometry & Math Helpers ---

def bundle_routes_to_wkt_multilinestringz(member_routes: list[dict]) -> str:
    """
    그룹 내에 속한 각 멤버 배관의 실제 3D Polyline 좌표점 목록을
    PostGIS 공간 연산이 가능한 MULTILINESTRING Z WKT 문자열로 변환합니다.
    """
    if not member_routes:
        return None
        
    lines = []
    for r in member_routes:
        pts = r.get('points', [])
        if len(pts) < 2:
            continue
        pts_str = ", ".join(f"{float(pt[0]):.9g} {float(pt[1]):.9g} {float(pt[2]):.9g}" for pt in pts)
        lines.append(f"({pts_str})")
        
    if not lines:
        return None
        
    return f"MULTILINESTRING Z ({', '.join(lines)})"


def bundle_parallel_segments_to_wkt(sec: dict, base_route: dict, partition: list[dict], m_guids: list[str]) -> str:
    """
    그룹 내 멤버들의 전체 배관 경로 대신, 실제로 평행하게 겹치는 
    그룹배관 구간(Parallel Segments)의 좌표 정보만 추출하여 MULTILINESTRING Z WKT로 반환합니다.
    """
    lines = []
    for m_guid in m_guids:
        member_points = []
        for sm in sec['segs']:
            b_seg = sm['base_seg']
            if m_guid == base_route['guid']:
                member_points.append(b_seg['from'])
                member_points.append(b_seg['to'])
            else:
                other_r = next((r for r in partition if r['guid'] == m_guid), None)
                if not other_r:
                    continue
                for o_seg in other_r['ortho_segs']:
                    pitch, overlap = check_parallel_overlap(b_seg, o_seg)
                    if pitch is not None:
                        member_points.append(o_seg['from'])
                        member_points.append(o_seg['to'])
                        break
                        
        if len(member_points) >= 2:
            for i in range(0, len(member_points), 2):
                if i + 1 < len(member_points):
                    p1 = member_points[i]
                    p2 = member_points[i+1]
                    pts_str = f"{p1[0]:.9g} {p1[1]:.9g} {p1[2]:.9g}, {p2[0]:.9g} {p2[1]:.9g} {p2[2]:.9g}"
                    lines.append(f"({pts_str})")
                    
    if not lines:
        return None
    return f"MULTILINESTRING Z ({', '.join(lines)})"


def generate_trunk_centerline_wkt(section_bounds: list[dict]) -> str:
    """
    각 다발 구간의 바운딩 박스(SECTION_BOUNDS)를 관통하는
    3D 대표 중심선 경로를 계산하여 MULTILINESTRING Z WKT 문자열로 반환합니다.
    """
    if not section_bounds:
        return None
        
    lines = []
    for sec in section_bounds:
        min_pt = sec.get('min')
        max_pt = sec.get('max')
        t = sec.get('type')
        if not min_pt or not max_pt or not t:
            continue
            
        cx = (min_pt[0] + max_pt[0]) / 2.0
        cy = (min_pt[1] + max_pt[1]) / 2.0
        cz = (min_pt[2] + max_pt[2]) / 2.0
        
        if t == 'X':
            start = (min_pt[0], cy, cz)
            end = (max_pt[0], cy, cz)
        elif t == 'Y':
            start = (cx, min_pt[1], cz)
            end = (cx, max_pt[1], cz)
        elif t == 'Z':
            start = (cx, cy, min_pt[2])
            end = (cx, cy, max_pt[2])
        else:
            start = min_pt
            end = max_pt
            
        lines.append(f"({start[0]:.9g} {start[1]:.9g} {start[2]:.9g}, {end[0]:.9g} {end[1]:.9g} {end[2]:.9g})")
        
    if not lines:
        return None
        
    return f"MULTILINESTRING Z ({', '.join(lines)})"


def dist(a, b) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def vec_sub(a, b) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def unit(v) -> tuple[float, float, float]:
    length = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if length < 1e-9:
        return (0.0, 0.0, 0.0)
    return (v[0] / length, v[1] / length, v[2] / length)


def dot_product_3d(u, v) -> float:
    return u[0]*v[0] + u[1]*v[1] + u[2]*v[2]


def axis_snap(d: tuple[float, float, float]) -> int:
    values = [abs(d[0]), abs(d[1]), abs(d[2])]
    ax = max(range(3), key=lambda i: values[i])
    return ax * 2 + (0 if d[ax] >= 0 else 1)


def dir_runs(points: list[tuple[float, float, float]]) -> list[tuple[int, float]]:
    runs = []
    for a, b in zip(points, points[1:]):
        length = dist(a, b)
        if length < 1e-3:
            continue
        direction = axis_snap(vec_sub(b, a))
        if runs and runs[-1][0] == direction:
            runs[-1] = (direction, runs[-1][1] + length)
        else:
            runs.append((direction, length))
    return runs


def get_arrow_code(points: list[tuple[float, float, float]], tol=ARROW_TOL) -> str:
    codes = []
    for a, b in zip(points, points[1:]):
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        dz = b[2] - a[2]
        L = math.sqrt(dx**2 + dy**2 + dz**2)
        if L < 1e-3:
            continue
        ux, uy, uz = dx/L, dy/L, dz/L
        
        # Classification: V (Vertical Z), H (Horizontal XY), D (Inclined)
        if abs(uz) >= tol:
            code = 'V'
        elif max(abs(ux), abs(uy)) >= tol:
            code = 'H'
        else:
            code = 'D'
            
        if not codes or codes[-1] != code:
            codes.append(code)
    return "".join(codes)


def count_ortho_bends(runs: list[tuple[int, float]]) -> int:
    bends = 0
    for i in range(len(runs) - 1):
        if runs[i][0] // 2 != runs[i+1][0] // 2:
            bends += 1
    return bends


def resample_polyline_directions(points: list[tuple[float, float, float]], N=RESAMPLE_N) -> list[float]:
    if len(points) < 2:
        return [0.0] * (N * 3)
        
    dists = [0.0]
    for a, b in zip(points, points[1:]):
        dists.append(dists[-1] + dist(a, b))
        
    total_len = dists[-1]
    if total_len < 1e-3:
        return [0.0] * (N * 3)
        
    resampled_pts = []
    for j in range(N + 1):
        target_d = j * (total_len / N)
        idx = 0
        while idx < len(dists) - 2 and dists[idx+1] < target_d:
            idx += 1
        d1 = dists[idx]
        d2 = dists[idx+1]
        p1 = points[idx]
        p2 = points[idx+1]
        
        t = (target_d - d1) / (d2 - d1) if (d2 - d1) > 1e-6 else 0.0
        x = p1[0] + t * (p2[0] - p1[0])
        y = p1[1] + t * (p2[1] - p1[1])
        z = p1[2] + t * (p2[2] - p1[2])
        resampled_pts.append((x, y, z))
        
    flat_units = []
    for i in range(N):
        p_from = resampled_pts[i]
        p_to = resampled_pts[i+1]
        dx = p_to[0] - p_from[0]
        dy = p_to[1] - p_from[1]
        dz = p_to[2] - p_from[2]
        L = math.sqrt(dx**2 + dy**2 + dz**2)
        if L < 1e-6:
            flat_units.extend([0.0, 0.0, 0.0])
        else:
            flat_units.extend([dx/L, dy/L, dz/L])
            
    return flat_units


def levenshtein_distance(s1, s2):
    key = (s1, s2) if s1 <= s2 else (s2, s1)
    if key in _lev_cache:
        return _lev_cache[key]
    if len(s1) < len(s2):
        res = levenshtein_distance(s2, s1)
        _lev_cache[key] = res
        return res
    if len(s2) == 0:
        _lev_cache[key] = len(s1)
        return len(s1)
    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    res = previous_row[-1]
    _lev_cache[key] = res
    return res


def compute_similarity(a, b, N=RESAMPLE_N) -> float:
    # 1. Shape Similarity (30%)
    arrow_a = a['arrow_code']
    arrow_b = b['arrow_code']
    max_arrow_len = max(len(arrow_a), len(arrow_b))
    if max_arrow_len == 0:
        shape_sim = 1.0
    else:
        lev_dist = levenshtein_distance(arrow_a, arrow_b)
        shape_sim = 1.0 - (lev_dist / max_arrow_len)
    shape_sim = max(0.0, min(1.0, shape_sim))
    
    # 2. Direction Similarity (30%)
    u = a['seg_units_3d']
    v = b['seg_units_3d']
    
    cos_forward = sum(dot_product_3d(u[i], v[i]) for i in range(N)) / N
    
    v_backward = []
    for i in range(N):
        orig_vec = v[N - 1 - i]
        v_backward.append((-orig_vec[0], -orig_vec[1], -orig_vec[2]))
    cos_backward = sum(dot_product_3d(u[i], v_backward[i]) for i in range(N)) / N
    
    dir_sim = max(0.0, max(cos_forward, cos_backward))
    dir_sim = min(1.0, dir_sim)
    
    # 3. Length Similarity (20%)
    len_a = a['total_len']
    len_b = b['total_len']
    max_len = max(len_a, len_b)
    if max_len < 1e-3:
        len_sim = 1.0
    else:
        len_sim = 1.0 - (abs(len_a - len_b) / max_len)
    len_sim = max(0.0, min(1.0, len_sim))
    
    # 4. Scale Similarity (20%)
    ext_a = a['extent']
    ext_b = b['extent']
    scale_sims = []
    for i in range(3):
        ea = ext_a[i]
        eb = ext_b[i]
        if ea < 1.0 and eb < 1.0:
            scale_sims.append(1.0)
        elif ea < 1.0 or eb < 1.0:
            scale_sims.append(0.0)
        else:
            scale_sims.append(min(ea, eb) / max(ea, eb))
    scale_sim = sum(scale_sims) / 3.0
    scale_sim = max(0.0, min(1.0, scale_sim))
    
    return 0.3 * shape_sim + 0.3 * dir_sim + 0.2 * len_sim + 0.2 * scale_sim


# --- Union-Find Helper ---

class UnionFind:
    def __init__(self, elements):
        self.parent = {el: el for el in elements}
        self.rank = {el: 0 for el in elements}
        
    def find(self, x):
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]
        
    def union(self, x, y):
        root_x = self.find(x)
        root_y = self.find(y)
        if root_x != root_y:
            if self.rank[root_x] > self.rank[root_y]:
                self.parent[root_y] = root_x
            else:
                self.parent[root_x] = root_y
                if self.rank[root_x] == self.rank[root_y]:
                    self.rank[root_y] += 1


def extract_orthogonal_segments(points, tol=ARROW_TOL):
    segments = []
    for a, b in zip(points, points[1:]):
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        dz = b[2] - a[2]
        L = math.sqrt(dx**2 + dy**2 + dz**2)
        if L < 1.0:  # Ignore sub-millimeter segments
            continue
        ux, uy, uz = dx/L, dy/L, dz/L
        
        if abs(uz) >= tol:
            direction = 'Z'
            mx = (a[0] + b[0]) / 2.0
            my = (a[1] + b[1]) / 2.0
            p_from = (mx, my, a[2])
            p_to = (mx, my, b[2])
        elif abs(ux) >= tol:
            direction = 'X'
            my = (a[1] + b[1]) / 2.0
            mz = (a[2] + b[2]) / 2.0
            p_from = (a[0], my, mz)
            p_to = (b[0], my, mz)
        elif abs(uy) >= tol:
            direction = 'Y'
            mx = (a[0] + b[0]) / 2.0
            mz = (a[2] + b[2]) / 2.0
            p_from = (mx, a[1], mz)
            p_to = (mx, b[1], mz)
        else:
            direction = 'D'
            p_from = a
            p_to = b
            
        segments.append({
            'from': p_from,
            'to': p_to,
            'dir': direction,
            'len': L,
            'vector': (dx, dy, dz),
            'unit': (ux, uy, uz)
        })
    return segments


def check_parallel_overlap(s1, s2, max_pitch=1500.0, min_overlap=100.0):
    if s1['dir'] != s2['dir'] or s1['dir'] == 'D':
        return None, 0.0
        
    d = s1['dir']
    p1_from, p1_to = s1['from'], s1['to']
    p2_from, p2_to = s2['from'], s2['to']
    
    if d == 'X':
        y1, z1 = p1_from[1], p1_from[2]
        y2, z2 = p2_from[1], p2_from[2]
        pitch = math.sqrt((y1 - y2)**2 + (z1 - z2)**2)
        min1, max1 = min(p1_from[0], p1_to[0]), max(p1_from[0], p1_to[0])
        min2, max2 = min(p2_from[0], p2_to[0]), max(p2_from[0], p2_to[0])
    elif d == 'Y':
        x1, z1 = p1_from[0], p1_from[2]
        x2, z2 = p2_from[0], p2_from[2]
        pitch = math.sqrt((x1 - x2)**2 + (z1 - z2)**2)
        min1, max1 = min(p1_from[1], p1_to[1]), max(p1_from[1], p1_to[1])
        min2, max2 = min(p2_from[1], p2_to[1]), max(p2_from[1], p2_to[1])
    else:  # 'Z'
        x1, y1 = p1_from[0], p1_from[1]
        x2, y2 = p2_from[0], p2_from[1]
        pitch = math.sqrt((x1 - x2)**2 + (y1 - y2)**2)
        min1, max1 = min(p1_from[2], p1_to[2]), max(p1_from[2], p1_to[2])
        min2, max2 = min(p2_from[2], p2_to[2]), max(p2_from[2], p2_to[2])
        
    if pitch > max_pitch:
        return None, 0.0
        
    overlap_min = max(min1, min2)
    overlap_max = min(max1, max2)
    overlap_len = overlap_max - overlap_min
    
    if overlap_len < min_overlap:
        return None, 0.0
        
    return pitch, overlap_len


def get_median(values):
    if not values:
        return 0
    s_vals = sorted(values)
    n = len(s_vals)
    if n % 2 == 1:
        return s_vals[n // 2]
    else:
        return (s_vals[n // 2 - 1] + s_vals[n // 2]) / 2.0


def get_mode(values):
    if not values:
        return 0
    c = Counter(values)
    return c.most_common(1)[0][0]


def stable_id(*parts: str) -> str:
    h = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return h


# --- Core Pipeline Processes ---

def extract_pipe_feature(guid, points, row_meta) -> dict:
    if len(points) < 2:
        return {}
        
    d_runs = dir_runs(points)
    arr_code = get_arrow_code(points)
    n_bends = count_ortho_bends(d_runs)
    seg_units = resample_polyline_directions(points)
    seg_units_3d = [seg_units[i*3:(i+1)*3] for i in range(RESAMPLE_N)]
    
    # Total length
    total_len = sum(dist(a, b) for a, b in zip(points, points[1:]))
    
    # Bounding box
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    min_z, max_z = min(zs), max(zs)
    extent = (max_x - min_x, max_y - min_y, max_z - min_z)
    
    centroid = ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0, (min_z + max_z) / 2.0)
    
    # Trunk axis: horizontal run direction of the longest horizontal run
    # Runs has elements like (direction, length) where direction is 0..5
    longest_horizontal_run_dir = -1
    longest_len = -1.0
    for direction, run_len in d_runs:
        if direction in (0, 1, 2, 3): # X or Y axes
            if run_len > longest_len:
                longest_len = run_len
                longest_horizontal_run_dir = direction
                
    if longest_horizontal_run_dir in (0, 1):
        trunk_axis = 0 # X axis is trunk axis
    elif longest_horizontal_run_dir in (2, 3):
        trunk_axis = 1 # Y axis is trunk axis
    else:
        # Fallback to dominant bbox horizontal extent
        trunk_axis = 0 if extent[0] >= extent[1] else 1
        
    ortho_segs = extract_orthogonal_segments(points)
        
    return {
        'guid': guid,
        'points': points,
        'eq_tag': row_meta['eq_tag'],
        'utility': row_meta['utility'],
        'utility_group': row_meta['utility_group'],
        'dir_runs': d_runs,
        'arrow_code': arr_code,
        'n_ortho_bends': n_bends,
        'seg_units': seg_units,
        'seg_units_3d': seg_units_3d,
        'total_len': total_len,
        'extent': extent,
        'centroid': centroid,
        'trunk_axis': trunk_axis,
        'ortho_segs': ortho_segs,
    }


def load_route_data_bulk(conn, eq_tags=None) -> list[dict]:
    """
    Loads all routing paths and their pre-segmented Middle Trunk geometries from DB.
    """
    print("Fetching route path middle trunk geometries and attributes from DB...")
    
    where_clause = ""
    params = []
    if eq_tags:
        placeholders = ", ".join(["%s"] * len(eq_tags))
        where_clause = f'WHERE rp."EQUIPMENT_TAG" IN ({placeholders})'
        params = list(eq_tags)
        
    sql = f"""
        SELECT 
            rp."ROUTE_PATH_GUID",
            rp."EQUIPMENT_TAG",
            rp."SOURCE_UTILITY",
            rp."UTILITY_GROUP",
            rp."SOURCE_SIZE",
            ST_AsText(ps."MIDDLE_TRUNK_GEOM") AS "TRUNK_WKT"
        FROM "TB_ROUTE_PATH" rp
        JOIN "TB_ROUTE_PATH_SEGMENTATION" ps ON rp."ROUTE_PATH_GUID" = ps."ROUTE_PATH_GUID"
        {where_clause}
        ORDER BY rp."ROUTE_PATH_GUID"
    """
    
    def parse_wkt_linestring_z(wkt: str) -> list[tuple[float, float, float]]:
        if not wkt or not wkt.upper().startswith("LINESTRING"):
            return []
        cleaned = wkt.replace("LINESTRING", "").replace("Z", "").replace("z", "").strip().strip("()").strip()
        points = []
        for pt_str in cleaned.split(","):
            coords = pt_str.strip().split()
            if len(coords) >= 3:
                points.append((float(coords[0]), float(coords[1]), float(coords[2])))
        return points

    routes = []
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        print(f"Total middle trunk records fetched: {len(rows)}")
        for r in rows:
            guid = r['ROUTE_PATH_GUID'].strip()
            pts = parse_wkt_linestring_z(r.get('TRUNK_WKT', ''))
            
            if len(pts) >= 2:
                routes.append({
                    'guid': guid,
                    'points': pts,
                    'meta': {
                        'eq_tag': r['EQUIPMENT_TAG'],
                        'utility': r['SOURCE_UTILITY'],
                        'utility_group': r['UTILITY_GROUP'],
                        'size': r['SOURCE_SIZE']
                    }
                })
            
    print(f"Loaded {len(routes)} valid route middle trunk polylines.")
    return routes


def analyze_patterns(conn, dry_run=False, image_out=None) -> list[dict]:
    # 1. Load all routes from DB in bulk
    all_routes = load_route_data_bulk(conn, None)
    
    # 2. Extract features for all paths (결과를 딕셔너리로 수집하여 GUID 매핑 지원)
    print("\nExtracting features for all paths...")
    processed_routes = {}
    for r in all_routes:
        feat = extract_pipe_feature(r['guid'], r['points'], r['meta'])
        if feat:
            processed_routes[r['guid']] = feat
    print(f"Features extracted for {len(processed_routes)} paths.")
    
    # 3. Partition by (EQUIPMENT_TAG, UTILITY_GROUP, SOURCE_UTILITY)
    partitions = defaultdict(list)
    for feat in processed_routes.values():
        key = (feat['eq_tag'], feat['utility_group'], feat['utility'])
        partitions[key].append(feat)
        
    detected_bundles = []
    
    # 4. Process each partition using Segment-level Parallelism Scan
    for key, partition in partitions.items():
        if len(partition) < 2:
            continue
            
        eq_tag, util_gp, util = key
        print(f"\nAnalyzing Partition | Eq: '{eq_tag}' | Group: '{util_gp}' | Util: '{util}' with {len(partition)} paths...")
        
        # 모든 배관의 개별 ortho 세그먼트에 assigned 초기상태 할당
        for r in partition:
            for s in r['ortho_segs']:
                s['assigned'] = False
                
        def get_unassigned_len(route):
            return sum(s['len'] for s in route['ortho_segs'] if not s.get('assigned', False))
            
        while True:
            # 아직 미할당 세그먼트가 남아있는 배관 목록 필터링
            active_routes = [r for r in partition if get_unassigned_len(r) > 0]
            if len(active_routes) < 2:
                break
                
            # 정렬: 미할당 세그먼트 총 길이가 가장 길고, 꺾임이 적은 것 우선 (Base Route 지정)
            active_routes.sort(key=lambda r: (get_unassigned_len(r), -len(r['ortho_segs'])), reverse=True)
            base_route = active_routes[0]
            
            # Base Route의 미할당 세그먼트만 스캔 대상
            base_segs = [s for s in base_route['ortho_segs'] if not s.get('assigned', False)]
            if not base_segs:
                # 루프 방어선: 세그먼트가 남지 않았다면 제외 마크 후 건너뜀
                for s in base_route['ortho_segs']:
                    s['assigned'] = True
                continue
                
            seg_members = []
            for idx, base_seg in enumerate(base_segs):
                members = {base_route['guid']: (0.0, base_seg['len'])}
                for other in active_routes:
                    if other['guid'] == base_route['guid']:
                        continue
                    best_pitch = None
                    total_overlap = 0.0
                    for o_seg in other['ortho_segs']:
                        if o_seg.get('assigned', False):
                            continue
                        pitch, overlap = check_parallel_overlap(base_seg, o_seg)
                        if pitch is not None:
                            total_overlap += overlap
                            if best_pitch is None or pitch < best_pitch:
                                best_pitch = pitch
                                
                    if best_pitch is not None:
                        members[other['guid']] = (best_pitch, total_overlap)
                seg_members.append({
                    'idx': idx,
                    'base_seg': base_seg,
                    'members': members
                })
                
            # Merge contiguous segments having members count >= 2 (엄격한 교집합 적용)
            sections = []
            current_sec = None
            for sm in seg_members:
                valid_members = set(sm['members'].keys())
                if len(valid_members) < 2:
                    if current_sec:
                        sections.append(current_sec)
                        current_sec = None
                    continue
                    
                if current_sec is None:
                    current_sec = {
                        'segs': [sm],
                        'member_guids': valid_members
                    }
                else:
                    common = current_sec['member_guids'].intersection(valid_members)
                    if len(common) >= 2:
                        current_sec['segs'].append(sm)
                        current_sec['member_guids'] = common # 교집합 기반 갱신
                    else:
                        sections.append(current_sec)
                        current_sec = {
                            'segs': [sm],
                            'member_guids': valid_members
                        }
            if current_sec:
                sections.append(current_sec)
                
            valid_sections = []
            for sec in sections:
                total_len = sum(s['base_seg']['len'] for s in sec['segs'])
                if total_len >= 500.0:
                    valid_sections.append(sec)
                    
            if not valid_sections:
                # 유효 구간이 없으면 루프 탈출을 방기하기 위해 base_segs를 assigned 처리
                for base_seg in base_segs:
                    base_seg['assigned'] = True
                continue
                
            for sec in valid_sections:
                m_guids = sorted(list(sec['member_guids']))
                
                # Compute section bounding boxes
                section_bounds = []
                for sm in sec['segs']:
                    b_seg = sm['base_seg']
                    t = b_seg['dir']
                    
                    g_pts = []
                    for m_guid in m_guids:
                        if m_guid == base_route['guid']:
                            g_pts.extend([b_seg['from'], b_seg['to']])
                        else:
                            other_r = next(r for r in partition if r['guid'] == m_guid)
                            for o_seg in other_r['ortho_segs']:
                                pitch, overlap = check_parallel_overlap(b_seg, o_seg)
                                if pitch is not None:
                                    g_pts.extend([o_seg['from'], o_seg['to']])
                                    
                    if not g_pts:
                        g_pts = [b_seg['from'], b_seg['to']]
                        
                    xs = [p[0] for p in g_pts]
                    ys = [p[1] for p in g_pts]
                    zs = [p[2] for p in g_pts]
                    
                    section_bounds.append({
                        'type': t,
                        'min': [min(xs), min(ys), min(zs)],
                        'max': [max(xs), max(ys), max(zs)]
                    })
                    
                pattern_seq = "".join(s['base_seg']['dir'] for s in sec['segs'])
                dedup_pattern = ""
                for char in pattern_seq:
                    if not dedup_pattern or dedup_pattern[-1] != char:
                        dedup_pattern += char
                        
                rep_bends = len(dedup_pattern) - 1 if len(dedup_pattern) > 0 else 0
                
                z_coords = []
                for sm in sec['segs']:
                    if sm['base_seg']['dir'] in ('X', 'Y'):
                        z_coords.append((sm['base_seg']['from'][2] + sm['base_seg']['to'][2]) / 2.0)
                trunk_z = float(get_median(z_coords)) if z_coords else float(base_route['centroid'][2])
                
                pitches = []
                for sm in sec['segs']:
                    for m_guid, (pitch, overlap) in sm['members'].items():
                        if m_guid != base_route['guid'] and m_guid in m_guids:
                            pitches.append(pitch)
                pitch_mm = float(get_median(pitches)) if pitches else 0.0
                
                spreads = []
                for sm in sec['segs']:
                    offsets = []
                    for m_guid in m_guids:
                        if m_guid == base_route['guid']:
                            offsets.append(0.0)
                        else:
                            pitch, overlap = sm['members'].get(m_guid, (0.0, 0.0))
                            offsets.append(pitch)
                    spreads.append(max(offsets) - min(offsets))
                trunk_xy_spread = float(max(spreads)) if spreads else 0.0
                
                avg_sim = 0.95
                rep_feat = base_route['seg_units']
                
                group_id = stable_id(eq_tag, util_gp, util, ",".join(m_guids), str(sec['segs'][0]['idx']))
                
                geom_wkt = bundle_parallel_segments_to_wkt(sec, base_route, partition, m_guids)
                trunk_wkt = generate_trunk_centerline_wkt(section_bounds)
                trunk_len = float(sum(
                    (sec['max'][0] - sec['min'][0]) if sec['type'] == 'X' else (
                        (sec['max'][1] - sec['min'][1]) if sec['type'] == 'Y' else (sec['max'][2] - sec['min'][2])
                    ) for sec in section_bounds
                ))
                
                bundle = {
                    'GROUP_ID': group_id,
                    'EQUIPMENT_TAG': eq_tag,
                    'UTILITY_GROUP': util_gp,
                    'UTILITY': util,
                    'N_MEMBERS': len(m_guids),
                    'AVG_SIMILARITY': avg_sim,
                    'TRUNK_Z': trunk_z,
                    'TRUNK_XY_SPREAD': trunk_xy_spread,
                    'PITCH_MM': pitch_mm,
                    'N_ORTHO_BENDS': rep_bends,
                    'MEMBER_GUIDS': m_guids,
                    'PATTERN_SEQ': dedup_pattern,
                    'SECTION_BOUNDS': section_bounds,
                    'FEAT': rep_feat,
                    'GEOM_WKT': geom_wkt,
                    'TRUNK_WKT': trunk_wkt,
                    'TRUNK_LEN': trunk_len
                }
                detected_bundles.append(bundle)
                print(f"  -> Detected Parallel Bundle: ID={group_id[:8]}... Pattern={dedup_pattern}, Members={len(m_guids)}, Z={trunk_z:,.1f}, Pitch={pitch_mm:,.1f}, Spread={trunk_xy_spread:,.1f}, Bends={rep_bends}")
                
            # 유효 번들로 사용된 세그먼트들 assigned 처리하여 소거
            for sec in valid_sections:
                for sm in sec['segs']:
                    sm['base_seg']['assigned'] = True
                    b_seg = sm['base_seg']
                    
                    for m_guid in sec['member_guids']:
                        if m_guid == base_route['guid']:
                            continue
                        other_r = next(r for r in partition if r['guid'] == m_guid)
                        for o_seg in other_r['ortho_segs']:
                            if o_seg.get('assigned', False):
                                continue
                            pitch, overlap = check_parallel_overlap(b_seg, o_seg)
                            if pitch is not None:
                                o_seg['assigned'] = True
            
    print(f"\nExtraction completed. Total parallel piping groups detected: {len(detected_bundles)}")
    
    if not dry_run:
        save_bundle_patterns(conn, detected_bundles)
        
    # 이미지 파일 저장 옵션 처리 (3D Plotly 렌더링 캡처)
    if image_out:
        save_bundle_images(detected_bundles, processed_routes, image_out)
        
    return detected_bundles


def save_bundle_patterns(conn, bundles: list[dict]) -> None:
    if not bundles:
        print("No bundles to save.")
        return
        
    has_vector = pgvector_installed(conn) and table_exists(conn, "TB_ROUTE_GROUP_PATTERN") and tool_config._load_config(None).get("db", {}).get("use_vector", True)
    if has_vector:
        with conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='TB_ROUTE_GROUP_PATTERN' AND column_name='FEAT'")
            has_vector = cur.fetchone() is not None
            
    cols = [
        "GROUP_ID", "EQUIPMENT_TAG", "UTILITY_GROUP", "UTILITY", "N_MEMBERS", "AVG_SIMILARITY",
        "TRUNK_Z", "TRUNK_XY_SPREAD", "PITCH_MM", "N_ORTHO_BENDS", "MEMBER_GUIDS",
        "PATTERN_SEQ", "SECTION_BOUNDS", "FEAT_JSON", "GEOM_3D", "TRUNK_GEOM_3D", "TRUNK_LEN"
    ]
    if has_vector:
        cols.append("FEAT")
        
    placeholders = []
    for c in cols:
        if c in ("MEMBER_GUIDS", "SECTION_BOUNDS", "FEAT_JSON"):
            placeholders.append("%s::jsonb")
        elif c == "FEAT":
            placeholders.append("%s::vector")
        elif c in ("GEOM_3D", "TRUNK_GEOM_3D"):
            placeholders.append("ST_GeomFromText(%s, 0)")
        else:
            placeholders.append("%s")
            
    sql = f"""
        INSERT INTO "TB_ROUTE_GROUP_PATTERN" ({", ".join(f'"{c}"' for c in cols)})
        VALUES ({", ".join(placeholders)})
        ON CONFLICT ("GROUP_ID") DO UPDATE SET
        {", ".join(f'"{c}" = EXCLUDED."{c}"' for c in cols if c != "GROUP_ID")},
        "CREATED_AT" = now()
    """
    
    rows = []
    for b in bundles:
        geom_wkt = b.get('GEOM_WKT')
        trunk_wkt = b.get('TRUNK_WKT')
        row = [
            b['GROUP_ID'], b['EQUIPMENT_TAG'], b['UTILITY_GROUP'], b['UTILITY'], b['N_MEMBERS'], b['AVG_SIMILARITY'],
            b['TRUNK_Z'], b['TRUNK_XY_SPREAD'], b['PITCH_MM'], b['N_ORTHO_BENDS'], json.dumps(b['MEMBER_GUIDS']),
            b['PATTERN_SEQ'], json.dumps(b['SECTION_BOUNDS']), json.dumps(b['FEAT']), geom_wkt, trunk_wkt,
            b['TRUNK_LEN']
        ]
        if has_vector:
            vec_literal = "[" + ",".join(f"{float(v):.9g}" for v in b['FEAT']) + "]"
            row.append(vec_literal)
        rows.append(row)
        
    with conn.cursor() as cur:
        cur.execute('DELETE FROM "TB_ROUTE_GROUP_PATTERN"')
        print("Cleared previous records in TB_ROUTE_GROUP_PATTERN.")
        
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=200)
    conn.commit()
    print(f"Successfully saved {len(bundles)} group patterns to database (vector extension={has_vector}).")


def save_bundle_images(bundles: list[dict], processed_routes: dict, output_dir: str, max_images: int = 20) -> None:
    """
    추출된 그룹배관 패턴(SECTION_BOUNDS 박스 및 멤버 배관선)을 Plotly 3D 그래프로 구성하고,
    kaleido 엔진을 사용하여 백그라운드에서 PNG 정적 이미지로 저장합니다.
    (기본 최대 저장 개수: 20개)
    """
    if not bundles:
        print("No bundles to render images.")
        return
        
    try:
        import plotly.graph_objects as go
    except ImportError:
        print("[warn] Plotly is not installed. Skipping image export.")
        return
        
    try:
        import kaleido
    except ImportError:
        print("[warn] 'kaleido' package is not installed. Please run 'pip install kaleido' to export static images.")
        print("[warn] Skipping image export.")
        return

    print(f"Saving 3D rendering images for up to {max_images} bundles to: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)
    
    # 너무 많은 이미지 생성은 리소스를 과하게 소모하므로 기본 20개로 제한합니다.
    for idx, b in enumerate(bundles):
        if idx >= max_images:
            print(f"  Reached max_images limit ({max_images}). Skipping the remaining {len(bundles) - max_images} bundles.")
            break
        fig = go.Figure()
        
        # 1. 멤버 배관선(3D Polyline) 드로잉
        for m_guid in b['MEMBER_GUIDS']:
            feat = processed_routes.get(m_guid)
            if not feat:
                continue
            pts = feat['points']
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            zs = [p[2] for p in pts]
            
            fig.add_trace(go.Scatter3d(
                x=xs, y=ys, z=zs,
                mode='lines+markers',
                marker=dict(size=3),
                line=dict(width=4),
                name=f"Pipe_{m_guid[:8]}"
            ))
            
        # 2. SECTION_BOUNDS 박스(AABB) 드로잉
        for s_idx, sec in enumerate(b.get('SECTION_BOUNDS', [])):
            min_pt, max_pt = sec['min'], sec['max']
            box_lines_x = []
            box_lines_y = []
            box_lines_z = []
            
            # 3D 박스 8개 정점 좌표 정의
            v = [
                (min_pt[0], min_pt[1], min_pt[2]),
                (max_pt[0], min_pt[1], min_pt[2]),
                (max_pt[0], max_pt[1], min_pt[2]),
                (min_pt[0], max_pt[1], min_pt[2]),
                (min_pt[0], min_pt[1], max_pt[2]),
                (max_pt[0], min_pt[1], max_pt[2]),
                (max_pt[0], max_pt[1], max_pt[2]),
                (min_pt[0], max_pt[1], max_pt[2])
            ]
            
            # 12개 모서리선 매핑
            edges = [
                (0,1), (1,2), (2,3), (3,0),
                (4,5), (5,6), (6,7), (7,4),
                (0,4), (1,5), (2,6), (3,7)
            ]
            
            for start, end in edges:
                box_lines_x.extend([v[start][0], v[end][0], None])
                box_lines_y.extend([v[start][1], v[end][1], None])
                box_lines_z.extend([v[start][2], v[end][2], None])
                
            fig.add_trace(go.Scatter3d(
                x=box_lines_x, y=box_lines_y, z=box_lines_z,
                mode='lines',
                line=dict(color='rgba(255, 0, 0, 0.6)', width=2),
                name=f"Box_{s_idx}_{sec['type']}"
            ))
            
        # 3. 레이아웃 튜닝 (1:1:1 종횡비 설정)
        fig.update_layout(
            title=f"Parallel Piping Group [ID: {b['GROUP_ID'][:8]}]<br>Eq: {b['EQUIPMENT_TAG']} | Utility: {b['UTILITY']}",
            scene=dict(
                xaxis_title="X (mm)",
                yaxis_title="Y (mm)",
                zaxis_title="Z (mm)",
                aspectmode="data"
            ),
            width=1024,
            height=768,
            showlegend=True
        )
        
        # 4. 이미지 캡처 파일 저장
        img_filename = f"bundle_{b['GROUP_ID'][:8]}.png"
        img_path = os.path.join(output_dir, img_filename)
        try:
            fig.write_image(img_path, engine="kaleido")
            if (idx + 1) % 50 == 0 or (idx + 1) == len(bundles):
                print(f"  Exported {idx + 1}/{len(bundles)} images...")
        except Exception as ex:
            print(f"[error] Failed to save image {img_filename}: {ex}")
            break


def main() -> int:
    parser = argparse.ArgumentParser(description="Piping Design Pattern Analyzer")
    tool_config.add_common_args(parser)
    
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    subparsers.add_parser("create-schema", help="Create pattern table schema")
    
    extract_parser = subparsers.add_parser("extract", help="Extract piping group patterns")
    extract_parser.add_argument("--dry-run", action="store_true", help="Print stats without saving to DB")
    extract_parser.add_argument("--image-out", default=None, help="Directory to save group pattern 3D images (PNG)")
    
    run_all_parser = subparsers.add_parser("run-all", help="Create schema and extract patterns")
    run_all_parser.add_argument("--dry-run", action="store_true", help="Print stats without saving to DB")
    run_all_parser.add_argument("--image-out", default=None, help="Directory to save group pattern 3D images (PNG)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
        
    runtime = tool_config.resolve_runtime(args)
    tool_config.print_runtime(runtime)
    
    conn = open_connection(runtime.conninfo)
    
    # args 객체에 image_out 속성이 정의되어 있는지 확인 (create-schema 커맨드는 속성이 없을 수 있음)
    image_out = getattr(args, "image_out", None)
    
    try:
        if args.command == "create-schema":
            create_schema(conn)
        elif args.command == "extract":
            analyze_patterns(conn, dry_run=args.dry_run, image_out=image_out)
        elif args.command == "run-all":
            create_schema(conn)
            analyze_patterns(conn, dry_run=args.dry_run, image_out=image_out)
    finally:
        conn.close()
        
    return 0


if __name__ == "__main__":
    sys.exit(main())
