#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
경로 삼분할(Start Stub, Middle Trunk, End Stub)을 실행하고 
결과를 TB_ROUTE_PATH_SEGMENTATION 테이블에 저장하는 모듈.
"""

import sys
import os
import math
import json
from pathlib import Path
from collections import defaultdict

# Add parent directory to sys.path to resolve tool_config correctly
sys.path.append(str(Path(__file__).resolve().parent))
import tool_config
import psycopg2
import psycopg2.extras

# 6축 방향 인덱스 규약: 0:+x, 1:-x, 2:+y, 3:-y, 4:+z, 5:-z
AXIS_NAMES = ["+x", "-x", "+y", "-y", "+z", "-z"]

def dist(a, b) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)

def vec_sub(a, b) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

def axis_snap(d: tuple[float, float, float]) -> int:
    values = [abs(d[0]), abs(d[1]), abs(d[2])]
    ax = max(range(3), key=lambda i: values[i])
    return ax * 2 + (0 if d[ax] >= 0 else 1)

def points_to_wkt_linestringz(points: list[tuple[float, float, float]]) -> str:
    if not points or len(points) < 2:
        return None
    pts_str = ", ".join(f"{float(pt[0]):.9g} {float(pt[1]):.9g} {float(pt[2]):.9g}" for pt in points)
    return f"LINESTRING Z ({pts_str})"

def point_to_wkt_pointz(pt: tuple[float, float, float]) -> str:
    if not pt:
        return None
    return f"POINT Z ({float(pt[0]):.9g} {float(pt[1]):.9g} {float(pt[2]):.9g})"

def get_first_run(points: list[tuple[float, float, float]]) -> tuple[float, int, bool]:
    """
    시작점부터 동일한 축 방향으로 진행하는 첫 번째 런(Run)의 길이와 끝 정점 인덱스, Z축(수직) 여부를 반환합니다.
    """
    if len(points) < 2:
        return 0.0, 0, False
    
    first_axis = axis_snap(vec_sub(points[1], points[0])) // 2
    total_len = 0.0
    end_idx = 0
    
    for i in range(len(points) - 1):
        a = points[i]
        b = points[i+1]
        dx, dy, dz = b[0]-a[0], b[1]-a[1], b[2]-a[2]
        L = math.sqrt(dx**2 + dy**2 + dz**2)
        if L < 1e-3:
            continue
        axis = axis_snap((dx, dy, dz)) // 2
        
        if axis == first_axis:
            total_len += L
            end_idx = i + 1
        else:
            break
            
    is_vertical = (first_axis == 2) # Z축은 2
    return total_len, end_idx, is_vertical

def segment_route(points: list[tuple[float, float, float]]) -> tuple[list, list, list, tuple, tuple]:
    """
    한 배관 경로를 현업 기준에 맞게 Start Stub, Middle Trunk, End Stub으로 분할합니다.
    (50mm 미만의 미세 지터 세그먼트는 방향 분류에서 제외하여 오작동 방지)
    """
    if len(points) < 2:
        return [], [], [], None, None
        
    # --- 1. START_STUB 분할 ---
    # 첫 번째 유의미한(50mm 이상) 세그먼트의 진행 방향 찾기
    first_axis = -1
    for i in range(len(points) - 1):
        a, b = points[i], points[i+1]
        if dist(a, b) >= 50.0:
            first_axis = axis_snap(vec_sub(b, a)) // 2
            break
            
    start_idx = 1
    if first_axis != -1:
        first_run_len = 0.0
        end_idx = 0
        for i in range(len(points) - 1):
            a, b = points[i], points[i+1]
            L = dist(a, b)
            if L < 50.0:
                end_idx = i + 1
                continue
            axis = axis_snap(vec_sub(b, a)) // 2
            if axis == first_axis:
                first_run_len += L
                end_idx = i + 1
            else:
                break
                
        is_vertical = (first_axis == 2) # Z축
        # 수직인 경우 격자보 관통 스텁으로 판단하여 그 런의 끝까지 포함
        if is_vertical:
            start_idx = end_idx
        else:
            # 수직이 아니면 첫 세그먼트 끝(points[1])까지만 자름
            start_idx = 1
    else:
        start_idx = 1
        
    start_stub_pts = points[:start_idx + 1]
    start_free_point = start_stub_pts[-1]
    
    # --- 2. END_STUB 분할 (종단 덕트/레터럴 역방향 스캔) ---
    # 종단 PoC(points[-1])에서 시작하여 처음으로 방향이 바뀌는 첫 엘보 정점까지 포함
    end_idx = len(points) - 1
    if len(points) - 1 > start_idx:
        # 역방향 기준 첫 번째 유의미한(50mm 이상) 세그먼트 방향 찾기
        last_axis = -1
        for i in range(len(points) - 2, start_idx - 1, -1):
            a, b = points[i], points[i+1]
            if dist(a, b) >= 50.0:
                last_axis = axis_snap(vec_sub(b, a)) // 2
                break
                
        if last_axis != -1:
            for i in range(len(points) - 2, start_idx - 1, -1):
                a, b = points[i], points[i+1]
                L = dist(a, b)
                if L < 50.0:
                    end_idx = i
                    continue
                curr_axis = axis_snap(vec_sub(b, a)) // 2
                if curr_axis != last_axis:
                    # 방향이 바뀐 첫 엘보 정점은 points[i+1]
                    end_idx = i + 1
                    break
                else:
                    end_idx = i
                    
        if end_idx >= len(points):
            end_idx = len(points) - 1
        elif end_idx <= start_idx:
            end_idx = max(start_idx, len(points) - 2)
    else:
        end_idx = start_idx
        
    end_stub_pts = points[end_idx:]
    end_free_point = end_stub_pts[0]
    
    # --- 3. MIDDLE_TRUNK 분할 ---
    middle_trunk_pts = points[start_idx : end_idx + 1]
    if len(middle_trunk_pts) < 2:
        middle_trunk_pts = [start_free_point, end_free_point]
        
    return start_stub_pts, middle_trunk_pts, end_stub_pts, start_free_point, end_free_point

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

def create_schema(conn) -> None:
    sql_path = Path(__file__).resolve().parent / "sql" / "create_path_segmentation_table.sql"
    with conn.cursor() as cur:
        if sql_path.exists():
            print(f"Executing DDL from: {sql_path}")
            cur.execute(sql_path.read_text(encoding="utf-8"))
        else:
            raise FileNotFoundError(f"SQL file not found at {sql_path}")
    conn.commit()
    print("Schema TB_ROUTE_PATH_SEGMENTATION ready.")

def load_route_data_bulk(conn) -> list[dict]:
    """
    TB_ROUTE_PATH와 하위 세그먼트를 쿼리하여 원본 경로 폴리라인 복원
    """
    print("Fetching route path geometries and attributes from DB in bulk...")
    sql = """
        SELECT 
            rp."ROUTE_PATH_GUID",
            sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",
            sd."TO_POSX",   sd."TO_POSY",   sd."TO_POSZ"
        FROM "TB_ROUTE_PATH" rp
        JOIN "TB_ROUTE_SEGMENTS" rs ON rp."ROUTE_PATH_GUID" = rs."ROUTE_PATH_GUID"
        JOIN "TB_ROUTE_SEGMENT_DETAIL" sd ON rs."SEGMENT_GUID" = sd."SEGMENT_GUID"
        ORDER BY rp."ROUTE_PATH_GUID", rs."ORDER", sd."ORDER"
    """
    raw_details = defaultdict(list)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        for r in rows:
            guid = r['ROUTE_PATH_GUID'].strip()
            raw_details[guid].append(r)
            
    routes = []
    for guid, details in raw_details.items():
        pts = []
        for d in details:
            fx, fy, fz = d['FROM_POSX'], d['FROM_POSY'], d['FROM_POSZ']
            tx, ty, tz = d['TO_POSX'], d['TO_POSY'], d['TO_POSZ']
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
                'points': pts
            })
    print(f"Reconstructed {len(routes)} valid route polylines.")
    return routes

def run_segmentation(conn) -> None:
    """
    모든 배관에 대해 삼분할 실행 후 TB_ROUTE_PATH_SEGMENTATION에 적재
    """
    routes = load_route_data_bulk(conn)
    if not routes:
        print("No routes found in database.")
        return
        
    insert_sql = """
        INSERT INTO "TB_ROUTE_PATH_SEGMENTATION" (
            "ROUTE_PATH_GUID", 
            "START_STUB_GEOM", 
            "MIDDLE_TRUNK_GEOM", 
            "END_STUB_GEOM", 
            "START_FREE_POINT", 
            "END_FREE_POINT"
        )
        VALUES (
            %s, 
            ST_GeomFromText(%s, 0), 
            ST_GeomFromText(%s, 0), 
            ST_GeomFromText(%s, 0), 
            ST_GeomFromText(%s, 0), 
            ST_GeomFromText(%s, 0)
        )
        ON CONFLICT ("ROUTE_PATH_GUID") DO UPDATE SET
            "START_STUB_GEOM" = EXCLUDED."START_STUB_GEOM",
            "MIDDLE_TRUNK_GEOM" = EXCLUDED."MIDDLE_TRUNK_GEOM",
            "END_STUB_GEOM" = EXCLUDED."END_STUB_GEOM",
            "START_FREE_POINT" = EXCLUDED."START_FREE_POINT",
            "END_FREE_POINT" = EXCLUDED."END_FREE_POINT",
            "CREATED_AT" = now()
    """
    
    rows = []
    for r in routes:
        guid = r['guid']
        start_pts, middle_pts, end_pts, start_fp, end_fp = segment_route(r['points'])
        
        start_wkt = points_to_wkt_linestringz(start_pts)
        middle_wkt = points_to_wkt_linestringz(middle_pts)
        end_wkt = points_to_wkt_linestringz(end_pts)
        start_fp_wkt = point_to_wkt_pointz(start_fp)
        end_fp_wkt = point_to_wkt_pointz(end_fp)
        
        rows.append((guid, start_wkt, middle_wkt, end_wkt, start_fp_wkt, end_fp_wkt))
        
    with conn.cursor() as cur:
        cur.execute('DELETE FROM "TB_ROUTE_PATH_SEGMENTATION"')
        print("Cleared previous segmentation records.")
        psycopg2.extras.execute_batch(cur, insert_sql, rows, page_size=200)
        
    conn.commit()
    print(f"Successfully segmented and saved {len(routes)} routes.")

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Segment route paths into Start Stub, Middle Trunk, and End Stub.")
    sub = parser.add_subparsers(dest="command", required=True)
    
    for name in ["create-schema", "run-all"]:
        p = sub.add_parser(name)
        tool_config.add_common_args(p)
        
    args = parser.parse_args()
    try:
        runtime = tool_config.resolve_runtime(args)
    except FileNotFoundError as ex:
        raise SystemExit(str(ex)) from ex
        
    with open_connection(runtime.conninfo) as conn:
        if args.command == "create-schema":
            create_schema(conn)
        elif args.command == "run-all":
            create_schema(conn)
            run_segmentation(conn)
            
    return 0

if __name__ == "__main__":
    sys.exit(main())
