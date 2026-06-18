#!/usr/bin/env python
# -*- coding: utf-8 -*-

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
    "TAG_GROUP_NM" text NOT NULL,
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
    "GEOM_3D" geometry(MultiPolygonZ, 0),
    "CREATED_AT" timestamp without time zone DEFAULT now()
);
CREATE INDEX IF NOT EXISTS "IX_TRGP_KEY"
ON "TB_ROUTE_GROUP_PATTERN" ("TAG_GROUP_NM", "UTILITY");
{vector_idx}
CREATE INDEX IF NOT EXISTS "IX_TRGP_GEOM"
ON "TB_ROUTE_GROUP_PATTERN" USING gist("GEOM_3D");
"""


# --- Geometry & Math Helpers ---

def section_bounds_to_wkt_multipolygonz(bounds):
    """
    SECTION_BOUNDS 내의 각 구간 AABB [min, max] 좌표를 6개의 3D 사각 패치 폴리곤 면으로 구성하고,
    이를 PostGIS가 해독할 수 있는 단일 MULTIPOLYGON Z (...) WKT 문자열로 변환합니다.
    """
    if not bounds:
        return None
        
    polygons = []
    for b in bounds:
        if 'min' not in b or 'max' not in b:
            continue
        minx, miny, minz = b['min']
        maxx, maxy, maxz = b['max']
        
        # 6개 면 정의
        faces = [
            # Bottom (Z = minz)
            [(minx, miny, minz), (maxx, miny, minz), (maxx, maxy, minz), (minx, maxy, minz), (minx, miny, minz)],
            # Top (Z = maxz)
            [(minx, miny, maxz), (minx, maxy, maxz), (maxx, maxy, maxz), (maxx, miny, maxz), (minx, miny, maxz)],
            # Front (Y = miny)
            [(minx, miny, minz), (minx, miny, maxz), (maxx, miny, maxz), (maxx, miny, minz), (minx, miny, minz)],
            # Back (Y = maxy)
            [(minx, maxy, minz), (maxx, maxy, minz), (maxx, maxy, maxz), (minx, maxy, maxz), (minx, maxy, minz)],
            # Left (X = minx)
            [(minx, miny, minz), (minx, maxy, minz), (minx, maxy, maxz), (minx, miny, maxz), (minx, miny, minz)],
            # Right (X = maxx)
            [(maxx, miny, minz), (maxx, miny, maxz), (maxx, maxy, maxz), (maxx, maxy, minz), (maxx, miny, minz)]
        ]
        
        for f in faces:
            coords_str = ", ".join(f"{pt[0]} {pt[1]} {pt[2]}" for pt in f)
            polygons.append(f"(({coords_str}))")
            
    if not polygons:
        return None
        
    return f"MULTIPOLYGON Z ({', '.join(polygons)})"


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
        
    return {
        'guid': guid,
        'points': points,
        'eq_tag': row_meta['eq_tag'],
        'utility': row_meta['utility'],
        'dir_runs': d_runs,
        'arrow_code': arr_code,
        'n_ortho_bends': n_bends,
        'seg_units': seg_units,
        'seg_units_3d': seg_units_3d,
        'total_len': total_len,
        'extent': extent,
        'centroid': centroid,
        'trunk_axis': trunk_axis,
    }


def load_route_data_bulk(conn, eq_tags=None) -> list[dict]:
    """
    Loads all routing paths, segments and details in a single query
    to optimize database round-trip times.
    """
    print("Fetching route path geometries and attributes from DB in bulk...")
    
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
            sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",
            sd."TO_POSX", sd."TO_POSY", sd."TO_POSZ",
            rs."ORDER" AS seg_order,
            sd."ORDER" AS detail_order
        FROM "TB_ROUTE_PATH" rp
        JOIN "TB_ROUTE_SEGMENTS" rs ON rp."ROUTE_PATH_GUID" = rs."ROUTE_PATH_GUID"
        JOIN "TB_ROUTE_SEGMENT_DETAIL" sd ON rs."SEGMENT_GUID" = sd."SEGMENT_GUID"
        {where_clause}
        ORDER BY rp."ROUTE_PATH_GUID", rs."ORDER", sd."ORDER"
    """
    
    raw_details = defaultdict(list)
    route_meta = {}
    
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        print(f"Total segment detail records fetched: {len(rows)}")
        for r in rows:
            guid = r['ROUTE_PATH_GUID'].strip()
            raw_details[guid].append(r)
            route_meta[guid] = {
                'eq_tag': r['EQUIPMENT_TAG'],
                'utility': r['SOURCE_UTILITY'],
                'utility_group': r['UTILITY_GROUP'],
                'size': r['SOURCE_SIZE']
            }
            
    # Reconstruct polylines in memory
    routes = []
    for guid, details in raw_details.items():
        pts = []
        for d in details:
            fx, fy, fz = d['FROM_POSX'], d['FROM_POSY'], d['FROM_POSZ']
            tx, ty, tz = d['TO_POSX'], d['TO_POSY'], d['TO_POSZ']
            
            # Check for Null values
            if None in (fx, fy, fz, tx, ty, tz):
                continue
                
            pt_from = (float(fx), float(fy), float(fz))
            pt_to = (float(tx), float(ty), float(tz))
            
            if not pts:
                pts.append(pt_from)
            elif dist(pts[-1], pt_from) > 1e-3:
                pts.append(pt_from)
                
            if dist(pts[-1], pt_to) > 1e-3:
                pts.append(pt_to)
                
        if len(pts) >= 2:
            routes.append({
                'guid': guid,
                'points': pts,
                'meta': route_meta[guid]
            })
            
    print(f"Reconstructed {len(routes)} valid route polylines.")
    return routes


def analyze_patterns(conn, dry_run=False) -> list[dict]:
    # 1. Fetch space groups
    print("Loading space groups from TB_SPACE_GROUP_INFO...")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute('SELECT "TAG_GROUP_ID", "TAG_GROUP_NM", "EQUIPMENT_TAG_LIST" FROM "TB_SPACE_GROUP_INFO"')
        space_groups = cur.fetchall()
    print(f"Loaded {len(space_groups)} space groups.")
    
    # 2. Map equipment tags to group names
    eq_to_groups = defaultdict(list)
    all_eq_tags = set()
    for sg in space_groups:
        tag_nm = sg['TAG_GROUP_NM']
        eq_tags_str = sg['EQUIPMENT_TAG_LIST']
        try:
            eq_tags = json.loads(eq_tags_str) if eq_tags_str else []
        except:
            eq_tags = [eq_tags_str] if eq_tags_str else []
        
        for eq_tag in eq_tags:
            eq_to_groups[eq_tag].append(tag_nm)
            all_eq_tags.add(eq_tag)
            
    # 3. Load all routes that belong to these equipment tags
    all_routes = load_route_data_bulk(conn, all_eq_tags)
    
    # Group routes by space group name
    group_routes = defaultdict(list)
    for r in all_routes:
        eq_tag = r['meta']['eq_tag']
        for g_nm in eq_to_groups.get(eq_tag, []):
            group_routes[g_nm].append(r)
            
    # Process features for each route (Phase 1)
    print("\nPhase 1: Extracting features for all paths...")
    processed_routes = {}
    for r in all_routes:
        feat = extract_pipe_feature(r['guid'], r['points'], r['meta'])
        if feat:
            processed_routes[r['guid']] = feat
    print(f"Features extracted for {len(processed_routes)} paths.")
    
    detected_bundles = []
    
    # Process each space group
    for g_nm, routes in group_routes.items():
        # Partition routes by utility within the group
        util_partitions = defaultdict(list)
        for r in routes:
            guid = r['guid']
            if guid in processed_routes:
                feat = processed_routes[guid]
                util_partitions[feat['utility']].append(feat)
                
        for util, partition in util_partitions.items():
            if len(partition) < 2:
                continue
                
            n_paths = len(partition)
            print(f"\nAnalyzing Space Group '{g_nm}' | Utility '{util}' with {n_paths} paths...")
            
            # Phase 2: Compute pairwise similarities (using global _similarity_cache)
            pairs_sim = {}
            for i in range(n_paths):
                for j in range(i+1, n_paths):
                    p_a = partition[i]
                    p_b = partition[j]
                    
                    key = (p_a['guid'], p_b['guid']) if p_a['guid'] <= p_b['guid'] else (p_b['guid'], p_a['guid'])
                    if key in _similarity_cache:
                        sim = _similarity_cache[key]
                    else:
                        sim = compute_similarity(p_a, p_b)
                        _similarity_cache[key] = sim
                    
                    pairs_sim[(p_a['guid'], p_b['guid'])] = sim
                    
            # Phase 3: Union-Find clustering
            uf = UnionFind([p['guid'] for p in partition])
            for (g1, g2), sim in pairs_sim.items():
                if sim >= SIM_THRESHOLD:
                    uf.union(g1, g2)
                    
            # Group by root
            clusters = defaultdict(list)
            for p in partition:
                root = uf.find(p['guid'])
                clusters[root].append(p)
                
            # Evaluate Bundle Gates for each cluster
            cluster_id_counter = 0
            for root, members in clusters.items():
                if len(members) < 2:
                    continue
                    
                # Gate 1: member count >= 2 (already satisfied)
                
                # Gate 2: Representative bends (median of member bends) >= 2
                bends_list = [m['n_ortho_bends'] for m in members]
                rep_bends = int(round(get_median(bends_list)))
                if rep_bends < 2:
                    continue
                    
                # Gate 3: Uniform pitch
                # Determine representative trunk axis of the group (mode of member trunk axes)
                axes_list = [m['trunk_axis'] for m in members]
                rep_trunk_axis = get_mode(axes_list)
                
                # Project member centroids to perpendicular horizontal plane
                # If rep_trunk_axis is X(0), perp horizontal coordinate is Y(1)
                # If rep_trunk_axis is Y(1), perp horizontal coordinate is X(0)
                offsets = [m['centroid'][1] if rep_trunk_axis == 0 else m['centroid'][0] for m in members]
                offsets.sort()
                
                pitches = [offsets[i+1] - offsets[i] for i in range(len(offsets) - 1)]
                mean_pitch = sum(pitches) / len(pitches)
                
                # Pitch CV check
                if mean_pitch > 0:
                    variance = sum((p - mean_pitch)**2 for p in pitches) / len(pitches)
                    std_pitch = math.sqrt(variance)
                    cv = std_pitch / mean_pitch
                else:
                    cv = 0.0
                    
                if len(pitches) > 1 and cv > PITCH_CV_MAX:
                    # Coefficient of variation is too high (not evenly spaced)
                    continue
                    
                # Passed all gates! Detect trunk section attributes
                # Trunk Z (rack elevation): Mode of horizontal runs Z coordinates
                hz_z_coords = []
                for m in members:
                    pts = m['points']
                    for a, b in zip(pts, pts[1:]):
                        direction = axis_snap(vec_sub(b, a))
                        if direction in (0, 1, 2, 3): # Horizontal segments
                            z_coord = round((a[2] + b[2]) / 2.0)
                            hz_z_coords.append(z_coord)
                            
                if hz_z_coords:
                    trunk_z = float(get_mode(hz_z_coords))
                else:
                    # Fallback to median centroid Z
                    trunk_z = float(get_median([m['centroid'][2] for m in members]))
                    
                trunk_xy_spread = float(max(offsets) - min(offsets))
                pitch_mm = float(get_median(pitches)) if pitches else 0.0
                
                # Calculate average similarity of all pairs in the cluster
                member_guids = sorted([m['guid'] for m in members])
                sims = []
                for i in range(len(member_guids)):
                    for j in range(i+1, len(member_guids)):
                        pair = (member_guids[i], member_guids[j])
                        if pair in pairs_sim:
                            sims.append(pairs_sim[pair])
                        elif (pair[1], pair[0]) in pairs_sim:
                            sims.append(pairs_sim[(pair[1], pair[0])])
                avg_sim = sum(sims) / len(sims) if sims else 1.0
                
                # Average feature vectors (mean of resampled unit vectors)
                avg_feat = [0.0] * (RESAMPLE_N * 3)
                for m in members:
                    for k in range(RESAMPLE_N * 3):
                        avg_feat[k] += m['seg_units'][k]
                for k in range(RESAMPLE_N * 3):
                    avg_feat[k] /= len(members)
                    
                # Segment group routing and extract bounding boxes for V, H, D sections
                section_bounds, rep_pattern = compute_group_section_bounds(members)
                
                # Build unique group pattern ID
                group_id = stable_id(g_nm, util, ",".join(member_guids))
                
                bundle = {
                    'GROUP_ID': group_id,
                    'TAG_GROUP_NM': g_nm,
                    'UTILITY': util,
                    'N_MEMBERS': len(members),
                    'AVG_SIMILARITY': avg_sim,
                    'TRUNK_Z': trunk_z,
                    'TRUNK_XY_SPREAD': trunk_xy_spread,
                    'PITCH_MM': pitch_mm,
                    'N_ORTHO_BENDS': rep_bends,
                    'MEMBER_GUIDS': member_guids,
                    'PATTERN_SEQ': rep_pattern,
                    'SECTION_BOUNDS': section_bounds,
                    'FEAT': avg_feat
                }
                detected_bundles.append(bundle)
                cluster_id_counter += 1
                
                print(f"  -> Detected Bundle Pattern: ID={group_id[:8]}... Pattern={rep_pattern}, Members={len(members)}, Z={trunk_z:,.1f}, Pitch={pitch_mm:,.1f}, CV={cv:.3f}, Spread={trunk_xy_spread:,.1f}, Bends={rep_bends}")
                
    print(f"\nExtraction completed. Total piping bundle groups detected: {len(detected_bundles)}")
    
    if not dry_run:
        save_bundle_patterns(conn, detected_bundles)
        
    return detected_bundles


def compute_group_section_bounds(members: list[dict], tol=ARROW_TOL) -> tuple[list[dict], str]:
    if not members:
        return [], ""
        
    # 1. Find the representative arrow_code
    arrow_codes = [m['arrow_code'] for m in members]
    if not arrow_codes:
        return [], ""
    rep_pattern = Counter(arrow_codes).most_common(1)[0][0]
    
    # 2. Find matching members and select the longest one as rep member
    matching_members = [m for m in members if m['arrow_code'] == rep_pattern]
    if not matching_members:
        # Fallback to the longest member in the group
        rep_member = max(members, key=lambda m: m['total_len'])
        rep_pattern = rep_member['arrow_code']
    else:
        rep_member = max(matching_members, key=lambda m: m['total_len'])
        
    # 3. Segment the representative member's points into contiguous runs
    pts = rep_member['points']
    segments_classes = []
    for a, b in zip(pts, pts[1:]):
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        dz = b[2] - a[2]
        L = math.sqrt(dx**2 + dy**2 + dz**2)
        if L < 1e-3:
            continue
        ux, uy, uz = dx/L, dy/L, dz/L
        
        if abs(uz) >= tol:
            code = 'V'
        elif max(abs(ux), abs(uy)) >= tol:
            code = 'H'
        else:
            code = 'D'
        segments_classes.append((code, a, b))
        
    # Group contiguous segments of the same code
    sections = []
    for code, a, b in segments_classes:
        if sections and sections[-1]['type'] == code:
            sections[-1]['points'].append(b)
        else:
            sections.append({
                'type': code,
                'points': [a, b]
            })
            
    # 4. Compute group bounding boxes for each section
    section_bounds = []
    epsilon = 100.0  # mm
    
    for sec in sections:
        t = sec['type']
        sec_pts = sec['points']
        xs = [p[0] for p in sec_pts]
        ys = [p[1] for p in sec_pts]
        zs = [p[2] for p in sec_pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        min_z, max_z = min(zs), max(zs)
        
        # Determine dominant run axis
        if t == 'V':
            run_axis = 'Z'
            axis_range = (min_z, max_z)
        elif t == 'H':
            if (max_x - min_x) >= (max_y - min_y):
                run_axis = 'X'
                axis_range = (min_x, max_x)
            else:
                run_axis = 'Y'
                axis_range = (min_y, max_y)
        else:
            # Diagonal
            dx, dy, dz = max_x - min_x, max_y - min_y, max_z - min_z
            if dx >= dy and dx >= dz:
                run_axis = 'X'
                axis_range = (min_x, max_x)
            elif dy >= dx and dy >= dz:
                run_axis = 'Y'
                axis_range = (min_y, max_y)
            else:
                run_axis = 'Z'
                axis_range = (min_z, max_z)
                
        # Filter points of all member pipes in the group along this run axis
        group_pts = []
        for m in members:
            for p in m['points']:
                val = p[2] if run_axis == 'Z' else (p[0] if run_axis == 'X' else p[1])
                if axis_range[0] - epsilon <= val <= axis_range[1] + epsilon:
                    group_pts.append(p)
                    
        if not group_pts:
            # Fallback to rep member's section points
            group_pts = sec_pts
            
        g_xs = [p[0] for p in group_pts]
        g_ys = [p[1] for p in group_pts]
        g_zs = [p[2] for p in group_pts]
        
        section_bounds.append({
            'type': t,
            'min': [min(g_xs), min(g_ys), min(g_zs)],
            'max': [max(g_xs), max(g_ys), max(g_zs)]
        })
        
    return section_bounds, rep_pattern


def save_bundle_patterns(conn, bundles: list[dict]) -> None:
    if not bundles:
        print("No bundles to save.")
        return
        
    has_vector = pgvector_installed(conn) and table_exists(conn, "TB_ROUTE_GROUP_PATTERN") and tool_config._load_config(None).get("db", {}).get("use_vector", True)
    # 테이블이 폴백(fallback) 스키마로 생성되었을 경우를 대비하여 FEAT 컬럼 존재 여부를 확인합니다.
    if has_vector:
        with conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='TB_ROUTE_GROUP_PATTERN' AND column_name='FEAT'")
            has_vector = cur.fetchone() is not None
            
    cols = [
        "GROUP_ID", "TAG_GROUP_NM", "UTILITY", "N_MEMBERS", "AVG_SIMILARITY",
        "TRUNK_Z", "TRUNK_XY_SPREAD", "PITCH_MM", "N_ORTHO_BENDS", "MEMBER_GUIDS",
        "PATTERN_SEQ", "SECTION_BOUNDS", "FEAT_JSON", "GEOM_3D"
    ]
    if has_vector:
        cols.append("FEAT")
        
    placeholders = []
    for c in cols:
        if c in ("MEMBER_GUIDS", "SECTION_BOUNDS", "FEAT_JSON"):
            placeholders.append("%s::jsonb")
        elif c == "FEAT":
            placeholders.append("%s::vector")
        elif c == "GEOM_3D":
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
        geom_wkt = section_bounds_to_wkt_multipolygonz(b.get('SECTION_BOUNDS'))
        row = [
            b['GROUP_ID'], b['TAG_GROUP_NM'], b['UTILITY'], b['N_MEMBERS'], b['AVG_SIMILARITY'],
            b['TRUNK_Z'], b['TRUNK_XY_SPREAD'], b['PITCH_MM'], b['N_ORTHO_BENDS'], json.dumps(b['MEMBER_GUIDS']),
            b['PATTERN_SEQ'], json.dumps(b['SECTION_BOUNDS']), json.dumps(b['FEAT']), geom_wkt
        ]
        if has_vector:
            # pgvector 특징 벡터 데이터를 PostgreSQL Array 포맷 문자열로 변환합니다. (예: '[1.0, 2.0, ...]')
            vec_literal = "[" + ",".join(f"{float(v):.9g}" for v in b['FEAT']) + "]"
            row.append(vec_literal)
        rows.append(row)
        
    with conn.cursor() as cur:
        # 최신 분석된 설계 패턴을 새로 저장하기 위해 이전 레코드를 삭제합니다.
        cur.execute('DELETE FROM "TB_ROUTE_GROUP_PATTERN"')
        print("Cleared previous records in TB_ROUTE_GROUP_PATTERN.")
        
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=200)
    conn.commit()
    print(f"Successfully saved {len(bundles)} group patterns to database (vector extension={has_vector}).")


def main() -> int:
    parser = argparse.ArgumentParser(description="Piping Design Pattern Analyzer")
    tool_config.add_common_args(parser)
    
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    subparsers.add_parser("create-schema", help="Create pattern table schema")
    
    extract_parser = subparsers.add_parser("extract", help="Extract piping group patterns")
    extract_parser.add_argument("--dry-run", action="store_true", help="Print stats without saving to DB")
    
    run_all_parser = subparsers.add_parser("run-all", help="Create schema and extract patterns")
    run_all_parser.add_argument("--dry-run", action="store_true", help="Print stats without saving to DB")
    
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
        elif args.command == "extract":
            analyze_patterns(conn, dry_run=args.dry_run)
        elif args.command == "run-all":
            create_schema(conn)
            analyze_patterns(conn, dry_run=args.dry_run)
    finally:
        conn.close()
        
    return 0


if __name__ == "__main__":
    sys.exit(main())
