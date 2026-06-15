import re

with open(r'd:\DINNO\DEV\AI-AutoRouting\TopKGen\Tools\learn_design_features.py', 'r', encoding='utf-8') as f:
    content = f.read()

# --- 1. classify_obstacle_type ---
content = content.replace("    if 'COLUMN' in text or 'COL' in text or '??' in text:\n        return 'COLUMN'",
                          "    if 'COLUMN' in text or 'COL' in text:\n        return 'COLUMN'")
content = content.replace("    if 'H-BEAM' in text or 'HBEAM' in text or 'BEAM' in text or '?' in text:\n        return 'H_BEAM'",
                          "    if 'H-BEAM' in text or 'HBEAM' in text or 'BEAM' in text:\n        return 'H_BEAM'")
content = content.replace("    if 'WALL' in text or '?' in text:\n        return 'WALL'",
                          "    if 'WALL' in text:\n        return 'WALL'")

# --- 2. segment_aabb_distance ---
# We will just rewrite segment_aabb_distance completely using an analytic approach
old_segment_aabb = """def segment_aabb_distance(a, b, box, samples=16):
    best = (float('inf'), 0.0, a)
    for i in range(samples + 1):
        t = i / samples
        p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)
        d = point_aabb_distance(p, box)
        if d < best[0]:
            best = (d, t, p)
    return best"""

new_segment_aabb = """def segment_aabb_distance(a, b, box):
    # 선분과 AABB 박스의 최단 거리를 해석적으로 구합니다 (Analytic Distance)
    # 1. 선분이 박스 내부에 있는지 검사
    minx, maxx = box['minx'], box['maxx']
    miny, maxy = box['miny'], box['maxy']
    minz, maxz = box['minz'], box['maxz']
    
    def clamp(v, min_v, max_v): return max(min_v, min(v, max_v))
    
    # 3D 선분 파라미터 방정식: P(t) = a + t*(b-a), 0 <= t <= 1
    # 여기서는 근사를 위해 선분 위의 여러 점 중 AABB와 가장 가까운 점을 해석적으로 찾거나 고밀도 샘플링을 합니다.
    # 복잡한 3D 수학 대신 충분히 촘촘한(예: 100샘플) 1D search를 사용하거나,
    # 각 축별로 분리해서 거리를 구합니다. (간단하게 고밀도 샘플링 + 해석적 clamp 조합 적용)
    best_d = float('inf')
    best_t = 0.0
    best_p = a
    samples = 50
    for i in range(samples + 1):
        t = i / samples
        px = a[0] + (b[0] - a[0]) * t
        py = a[1] + (b[1] - a[1]) * t
        pz = a[2] + (b[2] - a[2]) * t
        
        dx = max(minx - px, 0.0, px - maxx)
        dy = max(miny - py, 0.0, py - maxy)
        dz = max(minz - pz, 0.0, pz - maxz)
        d = math.sqrt(dx*dx + dy*dy + dz*dz)
        if d < best_d:
            best_d = d
            best_t = t
            best_p = (px, py, pz)
            
    return (best_d, best_t, best_p)"""
content = content.replace(old_segment_aabb, new_segment_aabb)

# --- 3. load_data (SQL + Reverse pts) ---
old_load_data_sql = """        sql = \"\"\"
            SELECT 
                rp."ROUTE_PATH_GUID",
                rp."PROCESS_NAME",
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
            WHERE rp."EQUIPMENT_TAG" = %s
            ORDER BY rp."ROUTE_PATH_GUID", rs."ORDER", sd."ORDER"
        \"\"\""""

new_load_data_sql = """        sql = \"\"\"
            SELECT 
                rp."ROUTE_PATH_GUID",
                rp."PROCESS_NAME",
                rp."EQUIPMENT_TAG",
                rp."SOURCE_UTILITY",
                rp."UTILITY_GROUP",
                rp."SOURCE_SIZE",
                rp."SOURCE_POSX", rp."SOURCE_POSY", rp."SOURCE_POSZ",
                rp."TARGET_POSX", rp."TARGET_POSY", rp."TARGET_POSZ",
                sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",
                sd."TO_POSX", sd."TO_POSY", sd."TO_POSZ",
                rs."ORDER" AS seg_order,
                sd."ORDER" AS detail_order
            FROM "TB_ROUTE_PATH" rp
            JOIN "TB_ROUTE_SEGMENTS" rs ON rp."ROUTE_PATH_GUID" = rs."ROUTE_PATH_GUID"
            JOIN "TB_ROUTE_SEGMENT_DETAIL" sd ON rs."SEGMENT_GUID" = sd."SEGMENT_GUID"
            WHERE rp."PROJECT_ID" = %s OR rp."EQUIPMENT_TAG" = %s
            ORDER BY rp."ROUTE_PATH_GUID", rs."ORDER", sd."ORDER"
        \"\"\""""
content = content.replace(old_load_data_sql, new_load_data_sql)

content = content.replace("cur.execute(sql, (self.project_name,))", "cur.execute(sql, (self.project_name, self.project_name))")

old_route_meta = """                route_meta[guid] = {
                    'process_name': r.get('PROCESS_NAME') or '',
                    'eq_tag': r['EQUIPMENT_TAG'],
                    'utility': r['SOURCE_UTILITY'],
                    'utility_group': r['UTILITY_GROUP'],
                    'size': r['SOURCE_SIZE']
                }"""
new_route_meta = """                route_meta[guid] = {
                    'process_name': r.get('PROCESS_NAME') or '',
                    'eq_tag': r['EQUIPMENT_TAG'],
                    'utility': r['SOURCE_UTILITY'],
                    'utility_group': r['UTILITY_GROUP'],
                    'size': r['SOURCE_SIZE'],
                    'source_pos': (float(r.get('SOURCE_POSX') or 0), float(r.get('SOURCE_POSY') or 0), float(r.get('SOURCE_POSZ') or 0))
                }"""
content = content.replace(old_route_meta, new_route_meta)

old_pts_append = """            if len(pts) >= 2:
                self.routes.append({
                    'guid': guid,
                    'points': pts,
                    'meta': route_meta[guid]
                })"""
new_pts_append = """            if len(pts) >= 2:
                src_pos = route_meta[guid]['source_pos']
                dist_to_start = dist_3d(src_pos, pts[0])
                dist_to_end = dist_3d(src_pos, pts[-1])
                # SOURCE 쪽에 더 가까운 쪽이 0번 인덱스가 되도록 역방향(Reverse) 보정
                if dist_to_end < dist_to_start:
                    pts.reverse()
                    
                self.routes.append({
                    'guid': guid,
                    'points': pts,
                    'meta': route_meta[guid]
                })"""
content = content.replace(old_pts_append, new_pts_append)

# --- 4. load_obstacles_for_routes (PROJECT_ID + Z overlap) ---
old_obstacle_sql = """        sql = \"\"\"
            SELECT "INSTANCE_NAME", "OST_TYPE", "DDWORKS_TYPE", "COLLISION_PASS",
                   "AABB_MINX", "AABB_MINY", "AABB_MINZ", "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"
            FROM "TB_BIM_OBSTACLE"
            WHERE "AABB_MAXX" >= %s AND "AABB_MINX" <= %s
              AND "AABB_MAXY" >= %s AND "AABB_MINY" <= %s
        \"\"\""""
new_obstacle_sql = """        minz = min(p[2] for r in self.routes for p in r['points']) - 5000.0
        maxz = max(p[2] for r in self.routes for p in r['points']) + 5000.0
        sql = \"\"\"
            SELECT "INSTANCE_NAME", "OST_TYPE", "DDWORKS_TYPE", "COLLISION_PASS",
                   "AABB_MINX", "AABB_MINY", "AABB_MINZ", "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"
            FROM "TB_BIM_OBSTACLE"
            WHERE "PROJECT_ID" = %s
              AND "AABB_MAXX" >= %s AND "AABB_MINX" <= %s
              AND "AABB_MAXY" >= %s AND "AABB_MINY" <= %s
              AND "AABB_MAXZ" >= %s AND "AABB_MINZ" <= %s
        \"\"\""""
content = content.replace(old_obstacle_sql, new_obstacle_sql)
content = content.replace("cur.execute(sql, (minx, maxx, miny, maxy))", "cur.execute(sql, (self.project_name, minx, maxx, miny, maxy, minz, maxz))")

with open(r'd:\DINNO\DEV\AI-AutoRouting\TopKGen\Tools\learn_design_features.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("refactored obstacle logic and directionality")
