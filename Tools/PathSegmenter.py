#!/usr/bin/env python
# -*- coding: utf-8 -*-

# ==============================================================================
# [실행명령어 예시]
#   1) 테이블 스키마 DDL 생성 및 적용:
#      > python Tools/PathSegmenter.py create-schema --password dinno
#   2) 배관 경로 삼분할(Segmentation) 연산 수행 및 데이터베이스 저장:
#      > python Tools/PathSegmenter.py run-all --password dinno
# ==============================================================================

"""
[전체적인 코드내의 흐름도]
1. main() 실행 -> argparse를 통해 명령행 인자 (create-schema, run-all) 수신 및 DB 런타임 설정 로드
2. open_connection() -> PostgreSQL 데이터베이스 서버 연결 수립
3. create_schema() -> 'Tools/sql/create_path_segmentation_table.sql' 스키마 DDL을 실행하여 테이블 생성
4. run_segmentation() 호출:
   a. load_route_data_bulk() -> TB_ROUTE_PATH 및 관련 세그먼트 상세 정보를 쿼리하여 각 배관 경로의 3D 좌표 폴리라인 복원
   b. segment_route() 호출 (각 배관 경로 루프) -> Start Stub, Middle Trunk, End Stub 분할 연산 수행
   c. points_to_wkt_linestringz() / point_to_wkt_pointz() -> 분할된 3D 점열 및 분기점을 PostGIS 호환 WKT (LINESTRING Z, POINT Z) 포맷으로 변환
   d. DB 기존 데이터를 초기화(DELETE)한 뒤 execute_batch()를 통해 대량 일괄 등록 (INSERT)
5. DB 트랜잭션 Commit 및 커넥션 Close 후 프로세스 종료

[핵심 알고리즘]
- Start Stub (인입부): 첫 유의미한(50mm 이상) 세그먼트의 진행 방향이 수직(Z축)이면 수직 직선 구간이 끝나는 곳까지를 Stub으로 삼음. 수평이면 첫 번째 세그먼트의 끝(index 1)까지 분할.
- End Stub (도출부): 종단점(PoC)에서 역방향으로 탐색하여 최초로 50mm 이상이며 진행 방향이 전환되는 첫 번째 엘보(Elbow) 정점까지 분할.
- Middle Trunk (본선): Start Stub의 끝점(Start Free Point)과 End Stub의 시작점(End Free Point) 사이의 중간 본선 구간.
- 미세 지터 필터링: 50mm 미만의 미세 선분은 방향 분류 판정에서 생략하여 오류 판정을 예외 처리함.

[주요 함수]
- axis_snap(d): 벡터 d를 6방향(0~5: +x, -x, +y, -y, +z, -z) 중 가장 가까운 축 방향 인덱스로 매핑
- get_first_run(points): 시작점부터 동일 축 방향으로 진행하는 첫 번째 직선 구간의 길이, 끝 인덱스, Z축 여부 판정
- segment_route(points): 배관 좌표 목록을 Start Stub, Middle Trunk, End Stub, Start Free Point, End Free Point로 분할하는 코어 로직
- load_route_data_bulk(conn): 데이터베이스로부터 배관 세그먼트 좌표 데이터를 조회하여 3D 폴리라인으로 재구성
- run_segmentation(conn): 전체 배관 경로에 대해 분할 알고리즘을 실행하고 DB 적재 프로세스 제어

[주요 변수]
- first_axis: 시작부의 첫 유의미한 세그먼트 진행 방향 축 인덱스
- start_idx: Start Stub의 분할 종료 기준 정점 인덱스
- last_axis: 종단부 역방향 탐색 시의 최초 진행 방향 축 인덱스
- entry_direction: End Stub이 종단 PoC로 진입하는 축정렬 단위벡터 (예: (0,0,-1)), 미확정 시 None
- end_idx: End Stub의 분할 시작 기준 정점 인덱스
- start_stub_pts / middle_trunk_pts / end_stub_pts: 분할된 각 세그먼트의 3D 좌표 점열 리스트
- start_free_point / end_free_point: 각 Stub과 Trunk 사이의 연결점(접속 자유점) 좌표
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
AXIS_VECTORS = [(1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)]

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

def segment_route(points: list[tuple[float, float, float]]) -> tuple[list, list, list, tuple, tuple, tuple]:
    """
    한 배관 경로를 현업 기준에 맞게 Start Stub, Middle Trunk, End Stub으로 분할합니다.
    (50mm 미만의 미세 지터 세그먼트는 방향 분류에서 제외하여 오작동 방지)

    반환값의 마지막 요소(entry_direction)는 End Stub이 종단 PoC로 진입하는 방향의
    축정렬 단위벡터(예: (0,0,-1))이며, 역방향 스캔에서 찾은 최초 유의미(50mm 이상)
    세그먼트의 실제 진행 방향입니다. 해당 세그먼트를 찾지 못하면 None입니다.
    """
    if len(points) < 2:
        return [], [], [], None, None, None
        
    # --- 1. START_STUB 분할 ---
    # Start Stub: 장비 PoC부터 → A/F구역 수평 이동 → 격자보 관통 수직 하강 → CSF구역 진입 직전까지
    # 핵심: CSF 구역(Z ≤ 13700) 경계를 처음 넘는 점의 "직전 점"이 Start Stub의 끝이 아니라,
    #        실제로 CSF 구역을 향해 내려가는 수직 구간 전체가 Start Stub에 포함되어야 함.
    #        따라서 "CSF 경계를 처음 넘는 점(i)"이 Start Stub의 마지막 점이 되어야 함.
    #        즉 start_idx = i (CSF로 진입하는 점까지 포함, Middle Trunk는 그 다음부터 시작)
    start_idx = 1
    matched_csf = False
    
    # [1-1] 시작점 Z좌표가 13700.0 이상(A/F구역)인 경우 CSF구역 진입 시점 찾기
    if points[0][2] >= 13700.0:
        for i in range(1, len(points)):
            if points[i][2] <= 13700.0:
                # CSF 경계(Z <= 13700)를 넘는 최초의 인덱스 i를 격자보 관통 수직 하강의 종료점으로 설정
                # Start Stub은 points[0..i]까지 포함, Middle Trunk는 그 다음(points[i..])부터 시작
                start_idx = i
                matched_csf = True
                break
            
    # [1-2] CSF 구역 경계를 찾는 것에 실패한 경우, 기하학적 컷팅 조건 적용
    if not matched_csf:
        # 첫 번째 유의미한(50mm 이상) 세그먼트의 진행 방향 축(first_axis) 찾기
        first_axis = -1
        for i in range(len(points) - 1):
            a, b = points[i], points[i+1]
            if dist(a, b) >= 50.0:
                first_axis = axis_snap(vec_sub(b, a)) // 2
                break
                
        if first_axis != -1:
            first_run_len = 0.0
            end_idx = 0
            for i in range(len(points) - 1):
                a, b = points[i], points[i+1]
                L = dist(a, b)
                # 50mm 미만 조각은 필터링
                if L < 50.0:
                    end_idx = i + 1
                    continue
                axis = axis_snap(vec_sub(b, a)) // 2
                # 첫 번째 축 방향과 같은 축으로 진행하는 동안 누적
                if axis == first_axis:
                    first_run_len += L
                    end_idx = i + 1
                else:
                    break
                    
            is_vertical = (first_axis == 2) # Z축 여부 판정
            # 수직관통 스텁인 경우 첫 수직 런(Run)의 끝까지 포함
            if is_vertical:
                start_idx = end_idx
            else:
                # 수평인 경우 첫 번째 세그먼트 끝점까지만 자름
                start_idx = 1
        else:
            start_idx = 1
            
    # 최종 START Stub 점열 및 접속자유점(Start Free Point) 획득
    start_stub_pts = points[:start_idx + 1]
    start_free_point = start_stub_pts[-1]
    
    # --- 2. END_STUB 분할 (종단 덕트/레터럴 역방향 스캔) ---
    # 종단 PoC(points[-1])에서 시작하여 처음으로 방향이 바뀌는 첫 엘보 정점까지 포함
    end_idx = len(points) - 1
    entry_direction = None
    
    # 시작점 인덱스를 초과하는 배관 조각이 남아있을 때만 종단 스캔 시작
    if len(points) - 1 > start_idx:
        # 역방향 기준 첫 번째 유의미한(50mm 이상) 세그먼트 방향 찾기
        last_axis = -1
        last_axis_full = -1
        for i in range(len(points) - 2, start_idx - 1, -1):
            a, b = points[i], points[i+1]
            if dist(a, b) >= 50.0:
                last_axis_full = axis_snap(vec_sub(b, a)) # 6방향 단위벡터 축정렬 인덱스
                last_axis = last_axis_full // 2          # 3차원 축 인덱스 (0:X, 1:Y, 2:Z)
                break

        # 종단 진입 방향 단위벡터 저장
        if last_axis_full != -1:
            entry_direction = AXIS_VECTORS[last_axis_full]

        # 역방향으로 동일 진행축 구간 스캔
        if last_axis != -1:
            for i in range(len(points) - 2, start_idx - 1, -1):
                a, b = points[i], points[i+1]
                L = dist(a, b)
                if L < 50.0:
                    end_idx = i
                    continue
                curr_axis = axis_snap(vec_sub(b, a)) // 2
                # 역방향 진행 중 최초로 진행축 방향이 바뀌는 꺾임점(엘보/밴딩) 검출 시 중단
                if curr_axis != last_axis:
                    end_idx = i + 1 # 방향이 바뀐 첫 엘보/밴딩 정점 인덱스 지정
                    break
                else:
                    end_idx = i
                    
        if end_idx >= len(points):
            end_idx = len(points) - 1
        elif end_idx <= start_idx:
            end_idx = max(start_idx, len(points) - 2)
    else:
        end_idx = start_idx
        
    # 최종 END Stub 점열 및 접속자유점(End Free Point) 획득
    end_stub_pts = points[end_idx:]
    end_free_point = end_stub_pts[0]
    
    # --- 3. MIDDLE_TRUNK 분할 ---
    # Start Stub의 끝점과 End Stub의 시작점 사이 본선 구간 추출
    middle_trunk_pts = points[start_idx : end_idx + 1]
    if len(middle_trunk_pts) < 2:
        middle_trunk_pts = [start_free_point, end_free_point]

    return start_stub_pts, middle_trunk_pts, end_stub_pts, start_free_point, end_free_point, entry_direction

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
            rp."EQUIPMENT_TAG" AS "equip_tag",
            rp."TARGET_OWNER_NAME" AS "target_owner",
            sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",
            sd."TO_POSX",   sd."TO_POSY",   sd."TO_POSZ",
            sd."TYPE" AS "type"
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
            equip_tag = details[0].get('equip_tag') or 'Unknown'
            target_owner = details[0].get('target_owner') or 'Unknown'
            routes.append({
                'guid': guid,
                'points': pts,
                'details': details,
                'equip_tag': equip_tag,
                'target_owner': target_owner
            })
    print(f"Reconstructed {len(routes)} valid route polylines.")
    return routes

def get_point_type(p: tuple[float, float, float] | None, details: list[dict]) -> str:
    """
    지정된 좌표 p가 세그먼트의 어느 피팅 타입(Elbow, Bending 등)에 위치하는지 판정
    """
    if not p:
        return "Unknown"
    best_type = "PIPE"
    min_d = 10.0  # 10mm 이내의 최근접 피팅 매칭 허용 오차
    for d in details:
        fx, fy, fz = d['FROM_POSX'], d['FROM_POSY'], d['FROM_POSZ']
        tx, ty, tz = d['TO_POSX'], d['TO_POSY'], d['TO_POSZ']
        if None in (fx, fy, fz, tx, ty, tz):
            continue
        
        # FROM 또는 TO 지점과 p의 3D 거리 계산
        d_from = math.sqrt((fx - p[0])**2 + (fy - p[1])**2 + (fz - p[2])**2)
        d_to = math.sqrt((tx - p[0])**2 + (ty - p[1])**2 + (tz - p[2])**2)
        curr_d = min(d_from, d_to)
        
        if curr_d < min_d:
            t = (d.get('type') or 'PIPE').strip().upper()
            # 피팅 타입을 우선하여 매칭 (PIPE나 PIPE_SEGMENT가 아닌 것 우선)
            if t not in ('PIPE', 'PIPE_SEGMENT', ''):
                best_type = t
                min_d = curr_d
            elif best_type == 'PIPE':
                best_type = t
                min_d = curr_d
    return best_type

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
            "END_FREE_POINT",
            "END_ENTRY_DIR_X",
            "END_ENTRY_DIR_Y",
            "END_ENTRY_DIR_Z"
        )
        VALUES (
            %s,
            ST_GeomFromText(%s, 0),
            ST_GeomFromText(%s, 0),
            ST_GeomFromText(%s, 0),
            ST_GeomFromText(%s, 0),
            ST_GeomFromText(%s, 0),
            %s, %s, %s
        )
        ON CONFLICT ("ROUTE_PATH_GUID") DO UPDATE SET
            "START_STUB_GEOM" = EXCLUDED."START_STUB_GEOM",
            "MIDDLE_TRUNK_GEOM" = EXCLUDED."MIDDLE_TRUNK_GEOM",
            "END_STUB_GEOM" = EXCLUDED."END_STUB_GEOM",
            "START_FREE_POINT" = EXCLUDED."START_FREE_POINT",
            "END_FREE_POINT" = EXCLUDED."END_FREE_POINT",
            "END_ENTRY_DIR_X" = EXCLUDED."END_ENTRY_DIR_X",
            "END_ENTRY_DIR_Y" = EXCLUDED."END_ENTRY_DIR_Y",
            "END_ENTRY_DIR_Z" = EXCLUDED."END_ENTRY_DIR_Z",
            "CREATED_AT" = now()
    """

    rows = []
    for r in routes:
        guid = r['guid']
        pts = r['points']
        start_pts, middle_pts, end_pts, start_fp, end_fp, entry_dir = segment_route(pts)

        start_wkt = points_to_wkt_linestringz(start_pts)
        middle_wkt = points_to_wkt_linestringz(middle_pts)
        end_wkt = points_to_wkt_linestringz(end_pts)
        start_fp_wkt = point_to_wkt_pointz(start_fp)
        end_fp_wkt = point_to_wkt_pointz(end_fp)
        edx, edy, edz = entry_dir if entry_dir is not None else (None, None, None)

        rows.append((guid, start_wkt, middle_wkt, end_wkt, start_fp_wkt, end_fp_wkt, edx, edy, edz))
        
        # 각 세그먼트별 3D 선분 총 길이 및 전체 배관 길이 계산 (mm)
        start_len = sum(dist(start_pts[i], start_pts[i+1]) for i in range(len(start_pts)-1)) if len(start_pts) >= 2 else 0.0
        middle_len = sum(dist(middle_pts[i], middle_pts[i+1]) for i in range(len(middle_pts)-1)) if len(middle_pts) >= 2 else 0.0
        end_len = sum(dist(end_pts[i], end_pts[i+1]) for i in range(len(end_pts)-1)) if len(end_pts) >= 2 else 0.0
        total_len = sum(dist(pts[i], pts[i+1]) for i in range(len(pts)-1)) if len(pts) >= 2 else 0.0

        # 분할 기준점의 피팅 타입(Elbow, Bending 등) 판정
        s_type = get_point_type(start_fp, r.get('details', []))
        e_type = get_point_type(end_fp, r.get('details', []))

        equip_tag = r['equip_tag']
        target_owner = r['target_owner']

        # 콘솔 창에 배관별 상세 삼분할 결과 출력 (불필요한 좌표 제거 및 전체 길이/세그먼트 길이 중심 표시)
        print(f"[Route {guid[:8]}...] [{equip_tag}] -> [{target_owner}] | Total Length: {total_len:6.1f} mm")
        print(f"  Start Stub: {start_len:6.1f} mm (Type: {s_type}) | "
              f"Trunk: {middle_len:6.1f} mm | "
              f"End Stub: {end_len:6.1f} mm (Type: {e_type})")
        
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
