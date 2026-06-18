#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ExtractVerticalGroup.py

이 모듈은 설계 데이터베이스에서 수직으로 주행하는 수직 배관 세그먼트들을 추출하고,
공간 영역(TB_SPACE_INFO - CSF, CR, A/F, FSF 등) 정보를 참조하여
수직다발배관(Vertical Group/Bundle)의 AABB 영역, 배관 수, 배관 간격, 
그리고 공간 경계 부근에서 배관이 수평으로 꺾여 나가는 고도 오프셋 및 방향 특징점(Space Transitions)을 추출하여 적재합니다.
"""

import sys
import os
import math
import json
import hashlib
from pathlib import Path
from collections import defaultdict, Counter

try:
    from sklearn.cluster import DBSCAN
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

# --- DDL 스키마 선언 ---
DDL_SQL = """
CREATE TABLE IF NOT EXISTS "TB_ROUTE_VERTICAL_GROUP_FEATURE" (
    "ID" bigserial PRIMARY KEY,
    "PROJECT_ID" text NOT NULL,
    "EQUIPMENT_NAME" text NOT NULL,
    "UTILITY" text NOT NULL,
    "SPACE_NAME" text NOT NULL,
    "VERTICAL_GROUP_ID" text NOT NULL,
    "DIRECTION" text NOT NULL,
    "BUNDLE_LENGTH" double precision NOT NULL,
    "AVG_PITCH_MM" double precision NOT NULL,
    "AABB_MINX" double precision NOT NULL,
    "AABB_MINY" double precision NOT NULL,
    "AABB_MINZ" double precision NOT NULL,
    "AABB_MAXX" double precision NOT NULL,
    "AABB_MAXY" double precision NOT NULL,
    "AABB_MAXZ" double precision NOT NULL,
    "ROUTE_COUNT" integer NOT NULL,
    "MEMBER_ROUTE_GUIDS_JSON" jsonb NOT NULL,
    "GEOM_3D" geometry(MultiLineStringZ, 0),
    "CREATED_AT" timestamptz DEFAULT now(),
    UNIQUE("PROJECT_ID", "EQUIPMENT_NAME", "UTILITY", "SPACE_NAME", "VERTICAL_GROUP_ID")
);
CREATE INDEX IF NOT EXISTS "IX_TRVGF_PROJECT" ON "TB_ROUTE_VERTICAL_GROUP_FEATURE" ("PROJECT_ID");
CREATE INDEX IF NOT EXISTS "IX_TRVGF_LOOKUP" ON "TB_ROUTE_VERTICAL_GROUP_FEATURE" ("PROJECT_ID", "EQUIPMENT_NAME", "UTILITY", "SPACE_NAME");
"""

# --- 기하학 계산 헬퍼 함수 ---
def dist_3d(p1, p2):
    return math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2 + (p1[2] - p2[2])**2)

def dist_2d(p1, p2):
    return math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)

def get_dominant_face(dx, dy, dz):
    abs_x, abs_y, abs_z = abs(dx), abs(dy), abs(dz)
    if abs_x >= abs_y and abs_x >= abs_z:
        return "+x" if dx >= 0 else "-x"
    elif abs_y >= abs_x and abs_y >= abs_z:
        return "+y" if dy >= 0 else "-y"
    else:
        return "+z" if dz >= 0 else "-z"

def stable_id(*parts: str) -> str:
    h = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return h

def segments_to_wkt_multilinestring3d(segs):
    if not segs:
        return None
    lines_wkt = []
    for s in segs:
        p1, p2 = s['p1'], s['p2']
        lines_wkt.append(f"({p1[0]} {p1[1]} {p1[2]}, {p2[0]} {p2[1]} {p2[2]})")
    return f"MULTILINESTRING Z ({', '.join(lines_wkt)})"

def get_segment_axis_and_direction(p1, p2):
    """
    세그먼트의 시점 p1과 종점 p2를 기반으로 진행하는 주요 기하 축(X, Y, Z)과 방향(+/-)을 판별합니다.
    """
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    dz = p2[2] - p1[2]
    L = dist_3d(p1, p2)
    if L < 1e-6:
        return None, None
        
    abs_x, abs_y, abs_z = abs(dx), abs(dy), abs(dz)
    if abs_z >= abs_x and abs_z >= abs_y:
        axis = 'Z'
        direction = '+Z' if dz >= 0 else '-Z'
    elif abs_x >= abs_y and abs_x >= abs_z:
        axis = 'X'
        direction = '+X' if dx >= 0 else '-X'
    else:
        axis = 'Y'
        direction = '+Y' if dy >= 0 else '-Y'
    return axis, direction

def get_space_name_for_point(pt, spaces):
    """
    3D 점 pt가 TB_SPACE_INFO의 영역 바운딩 박스(마진 100mm 적용) 내부에 포함되는지 확인하여 공간 구역명을 반환합니다.
    """
    x, y, z = pt
    matched_spaces = []
    margin = 100.0
    for sp in spaces:
        if (sp['min_x'] - margin <= x <= sp['max_x'] + margin and
            sp['min_y'] - margin <= y <= sp['max_y'] + margin and
            sp['min_z'] - margin <= z <= sp['max_z'] + margin):
            matched_spaces.append(sp['name'])
    if matched_spaces:
        # 주요 중요 공간명을 우선적으로 반환
        for sp_name in ['CSF', 'CR', 'A/F', 'FSF']:
            if sp_name in matched_spaces:
                return sp_name
        return matched_spaces[0]
    return 'UNKNOWN'

def simple_2d_clustering(points, eps):
    """
    scikit-learn DBSCAN이 없는 환경을 대비한 순수 파이썬 2D 공간 군집화 알고리즘
    """
    clusters = []
    visited = set()
    for i, p in enumerate(points):
        if i in visited:
            continue
        cluster = [i]
        visited.add(i)
        
        # BFS style to find all connected points within eps
        queue = [i]
        while queue:
            curr = queue.pop(0)
            curr_pt = points[curr]
            for j, other in enumerate(points):
                if j not in visited:
                    if dist_2d(curr_pt, other) < eps:
                        visited.add(j)
                        cluster.append(j)
                        queue.append(j)
        if len(cluster) >= 1:
            clusters.append(cluster)
    return clusters

def prepare_tables(conn):
    print("   - [Vertical Group] Preparing vertical group feature tables...")
    with conn.cursor() as cur:
        # 기존 테이블이 존재하고 신규 컬럼(EQUIPMENT_NAME)이 없으면 제약 조건 충돌 방지를 위해 Drop 후 재빌드
        cur.execute("""
            SELECT COUNT(*) 
            FROM information_schema.columns 
            WHERE table_name='TB_ROUTE_VERTICAL_GROUP_FEATURE' AND column_name='EQUIPMENT_NAME';
        """)
        has_new_col = cur.fetchone()[0] > 0
        
        cur.execute("""
            SELECT COUNT(*) 
            FROM information_schema.tables 
            WHERE table_schema='public' AND table_name='TB_ROUTE_VERTICAL_GROUP_FEATURE';
        """)
        table_exists = cur.fetchone()[0] > 0
        
        if table_exists and not has_new_col:
            print("     * Old schema detected. Dropping TB_ROUTE_VERTICAL_GROUP_FEATURE for clean reconstruction...")
            cur.execute('DROP TABLE IF EXISTS "TB_ROUTE_VERTICAL_GROUP_FEATURE" CASCADE;')
            
        cur.execute(DDL_SQL)
    conn.commit()

def extract_and_save_vertical_groups(conn, project_name, routes):
    """
    장비호기별, 유틸리티별, 공간구간별 다발배관(수평/수직) 특징점을 추출하여 저장합니다.
    """
    print("   - [Vertical Group] Starting 3D horizontal/vertical pipe bundle extraction pipeline...")
    
    # 1. 공간 정보 로드 (TB_SPACE_INFO)
    spaces = []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT "SPACE_NAME", "AABB_MINX", "AABB_MINY", "AABB_MINZ", "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"
                FROM "TB_SPACE_INFO"
                WHERE "SPACE_NAME" IN ('CSF', 'CR', 'A/F', 'FSF', 'AREA')
                  AND "AABB_MINZ" IS NOT NULL
            """)
            for row in cur.fetchall():
                spaces.append({
                    'name': row[0].strip(),
                    'min_x': float(row[1] or 0.0),
                    'min_y': float(row[2] or 0.0),
                    'min_z': float(row[3] or 0.0),
                    'max_x': float(row[4] or 0.0),
                    'max_y': float(row[5] or 0.0),
                    'max_z': float(row[6] or 0.0)
                })
        print(f"     * Loaded {len(spaces)} space area zones from TB_SPACE_INFO.")
    except Exception as ex:
        print(f"     * [Warning] Failed to query TB_SPACE_INFO: {ex}")
        conn.rollback()
        spaces = []

    # 2. 각 배관 경로에서 세그먼트 추출 후 공간 및 방향 매핑
    segments_by_group = defaultdict(list)
    for r in routes:
        pts = r['points']
        guid = r['guid']
        meta = r['meta']
        eq_name = meta.get('eq_tag') or project_name
        utility = meta.get('utility_group') or 'UNKNOWN'
        
        for i in range(len(pts) - 1):
            p1, p2 = pts[i], pts[i+1]
            length = dist_3d(p1, p2)
            if length < 100.0:  # 노이즈 필터링
                continue
                
            axis, direction = get_segment_axis_and_direction(p1, p2)
            if not axis:
                continue
                
            mid = ((p1[0]+p2[0])/2.0, (p1[1]+p2[1])/2.0, (p1[2]+p2[2])/2.0)
            space_name = get_space_name_for_point(mid, spaces)
            
            segments_by_group[(eq_name, utility, space_name, axis)].append({
                'guid': guid,
                'p1': p1,
                'p2': p2,
                'mid': mid,
                'direction': direction,
                'length': length
            })

    # 3. 각 그룹(장비, 유틸, 공간, 축)별 2D 군집화 및 다발 조건 추출
    saved_count = 0
    eps_dist = 1000.0  # 평행 배관 다발 군집화 기준 거리 (1.0m)

    with conn.cursor() as cur:
        # 해당 프로젝트의 기존 레코드 정리
        cur.execute('DELETE FROM "TB_ROUTE_VERTICAL_GROUP_FEATURE" WHERE "PROJECT_ID" = %s', (project_name,))
        
        for (eq_name, utility, space_name, axis), segs in segments_by_group.items():
            # 군집화를 위한 2D 좌표 리스트 구성
            proj_points = []
            for s in segs:
                mid = s['mid']
                if axis == 'Z':
                    proj_points.append((mid[0], mid[1]))
                elif axis == 'X':
                    proj_points.append((mid[1], mid[2]))
                else:  # Y축
                    proj_points.append((mid[0], mid[2]))
                    
            # 2D 평면 상에서 군집화 실행
            clusters_indices = []
            if HAS_SKLEARN and len(proj_points) >= 2:
                db = DBSCAN(eps=eps_dist, min_samples=1).fit(proj_points)
                labels = db.labels_
                clusters_map = defaultdict(list)
                for idx, label in enumerate(labels):
                    if label != -1:
                        clusters_map[label].append(idx)
                clusters_indices = list(clusters_map.values())
            else:
                clusters_indices = simple_2d_clustering(proj_points, eps_dist)
                
            for cluster_idx, indices in enumerate(clusters_indices):
                cluster_segs = [segs[idx] for idx in indices]
                if not cluster_segs:
                    continue
                    
                # 다발에 포함된 배관 개수 분석
                member_guids = list(sorted(set(s['guid'] for s in cluster_segs)))
                route_count = len(member_guids)
                if route_count < 2:  # 다발배관 최소 가닥 제약 조건 (2가닥 이상)
                    continue
                    
                # 다발 주행 방향 중심 축 길이 계산 (최소 500mm 이상)
                running_coords = []
                for s in cluster_segs:
                    p1, p2 = s['p1'], s['p2']
                    if axis == 'Z':
                        running_coords.extend([p1[2], p2[2]])
                    elif axis == 'X':
                        running_coords.extend([p1[0], p2[0]])
                    else:
                        running_coords.extend([p1[1], p2[1]])
                min_coord, max_coord = min(running_coords), max(running_coords)
                bundle_length = max_coord - min_coord
                if bundle_length < 500.0:  # 최소 길이 제약 조건 (500mm 이상)
                    continue
                    
                # 다발 내부의 배관 간 평균 간격 (Pitch) 연산
                route_projections = defaultdict(list)
                for s in cluster_segs:
                    guid = s['guid']
                    mid = s['mid']
                    if axis == 'Z':
                        route_projections[guid].append((mid[0], mid[1]))
                    elif axis == 'X':
                        route_projections[guid].append((mid[1], mid[2]))
                    else:
                        route_projections[guid].append((mid[0], mid[2]))
                        
                route_avg_projs = []
                for r_guid, projs in route_projections.items():
                    avg_x = sum(p[0] for p in projs) / len(projs)
                    avg_y = sum(p[1] for p in projs) / len(projs)
                    route_avg_projs.append((avg_x, avg_y))
                    
                pitches = []
                for i in range(len(route_avg_projs)):
                    for j in range(i+1, len(route_avg_projs)):
                        pitches.append(dist_2d(route_avg_projs[i], route_avg_projs[j]))
                avg_pitch = float(sum(pitches) / len(pitches)) if pitches else 0.0
                
                # 대표 진행 방향 식별 (최빈값 방향 사용)
                directions = [s['direction'] for s in cluster_segs]
                dominant_dir = Counter(directions).most_common(1)[0][0] if directions else "UNKNOWN"
                
                # AABB 영역 산출
                xs = [p[0] for s in cluster_segs for p in (s['p1'], s['p2'])]
                ys = [p[1] for s in cluster_segs for p in (s['p1'], s['p2'])]
                zs = [p[2] for s in cluster_segs for p in (s['p1'], s['p2'])]
                aabb_minx, aabb_maxx = min(xs), max(xs)
                aabb_miny, aabb_maxy = min(ys), max(ys)
                aabb_minz, aabb_maxz = min(zs), max(zs)
                
                # WKT MultiLineString 생성
                wkt = segments_to_wkt_multilinestring3d(cluster_segs)
                
                # 고유 다발 ID 생성
                v_group_id = stable_id(project_name, eq_name, utility, space_name, dominant_dir, f"BUNDLE_{cluster_idx}", ",".join(member_guids))
                
                # DB 영속화 적재 (UPSERT)
                cur.execute("""
                    INSERT INTO "TB_ROUTE_VERTICAL_GROUP_FEATURE" (
                        "PROJECT_ID", "EQUIPMENT_NAME", "UTILITY", "SPACE_NAME", "VERTICAL_GROUP_ID",
                        "DIRECTION", "BUNDLE_LENGTH", "AVG_PITCH_MM", 
                        "AABB_MINX", "AABB_MINY", "AABB_MINZ",
                        "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ", 
                        "ROUTE_COUNT", "MEMBER_ROUTE_GUIDS_JSON", "GEOM_3D"
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, ST_GeomFromText(%s, 0))
                    ON CONFLICT ("PROJECT_ID", "EQUIPMENT_NAME", "UTILITY", "SPACE_NAME", "VERTICAL_GROUP_ID")
                    DO UPDATE SET
                        "DIRECTION" = EXCLUDED."DIRECTION",
                        "BUNDLE_LENGTH" = EXCLUDED."BUNDLE_LENGTH",
                        "AVG_PITCH_MM" = EXCLUDED."AVG_PITCH_MM",
                        "AABB_MINX" = EXCLUDED."AABB_MINX",
                        "AABB_MINY" = EXCLUDED."AABB_MINY",
                        "AABB_MINZ" = EXCLUDED."AABB_MINZ",
                        "AABB_MAXX" = EXCLUDED."AABB_MAXX",
                        "AABB_MAXY" = EXCLUDED."AABB_MAXY",
                        "AABB_MAXZ" = EXCLUDED."AABB_MAXZ",
                        "ROUTE_COUNT" = EXCLUDED."ROUTE_COUNT",
                        "MEMBER_ROUTE_GUIDS_JSON" = EXCLUDED."MEMBER_ROUTE_GUIDS_JSON",
                        "GEOM_3D" = EXCLUDED."GEOM_3D",
                        "CREATED_AT" = now()
                """, (
                    project_name, eq_name, utility, space_name, v_group_id,
                    dominant_dir, bundle_length, avg_pitch,
                    aabb_minx, aabb_miny, aabb_minz,
                    aabb_maxx, aabb_maxy, aabb_maxz,
                    route_count, json.dumps(member_guids), wkt
                ))
                saved_count += 1
                
    conn.commit()
    conn.commit()
    print(f"     * Successfully saved {saved_count} 3D pipe bundle features for project '{project_name}'.")

