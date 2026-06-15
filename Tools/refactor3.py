import re
import os

with open(r'd:\DINNO\DEV\AI-AutoRouting\TopKGen\Tools\learn_design_features.py', 'r', encoding='utf-8') as f:
    content = f.read()

# --- 1. save_obstacle_relations Transaction ---
old_save_obstacle = """    def save_obstacle_relations(self):
        obstacles = self.load_obstacles_for_routes()
        if not obstacles:
            print("   - [Obstacle] No obstacle AABB data found for relation learning.")
            return
        insert_sql = \"\"\"
            INSERT INTO "TB_ROUTE_FEATURE_OBSTACLE_RELATION"
            ("PROJECT_ID", "ROUTE_PATH_GUID", "OBSTACLE_NAME", "OBSTACLE_TYPE", "OBSTACLE_AXIS", "UTILITY_GROUP", "UTILITY",
             "DIAMETER_MM", "NEAREST_DISTANCE_MM", "REQUIRED_CLEARANCE_MM", "CLEARANCE_MARGIN_MM", "BYPASS_SIDE", "BYPASS_AXIS",
             "Z_DELTA_NEAR_OBSTACLE_MM", "BEND_COUNT_BEFORE", "BEND_COUNT_AFTER", "EXTRA_LENGTH_RATIO", "RELATION_SCORE")
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        \"\"\"
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM \\"TB_ROUTE_FEATURE_OBSTACLE_RELATION\\" WHERE \\"PROJECT_ID\\" = %s", (self.project_name,))
            count = 0
            for r in self.routes:
                pts = r.get('points') or []
                if len(pts) < 2:
                    continue
                meta = r['meta']
                diameter = parse_pipe_diameter(meta.get('size')) or 0.0
                required_clearance = diameter * 0.5 + 150.0
                route_length = sum(dist_3d(pts[i - 1], pts[i]) for i in range(1, len(pts)))
                straight = dist_3d(pts[0], pts[-1]) or 1.0
                bends = route_bends(pts)
                for obs in obstacles:
                    best = (float('inf'), 0, 0.0, pts[0])
                    for i in range(1, len(pts)):
                        d, t, near_pt = segment_aabb_distance(pts[i - 1], pts[i], obs)
                        if d < best[0]:
                            best = (d, i, t, near_pt)
                    nearest, seg_index, _, near_pt = best
                    if nearest > max(required_clearance + 1000.0, 1800.0):
                        continue
                    before = sum(1 for b in bends if b < seg_index)
                    after = sum(1 for b in bends if b >= seg_index)
                    bypass_axis = get_dominant_face(pts[seg_index][0] - pts[seg_index - 1][0], pts[seg_index][1] - pts[seg_index - 1][1], pts[seg_index][2] - pts[seg_index - 1][2])
                    z_mid = (obs['minz'] + obs['maxz']) * 0.5
                    relation_score = max(0.0, (required_clearance + 1000.0 - nearest) / (required_clearance + 1000.0))
                    cur.execute(insert_sql, (self.project_name, r['guid'], obs['name'], obs['obstacle_type'], obs['axis'], meta.get('utility_group'), meta.get('utility'), diameter, nearest, required_clearance, nearest - required_clearance, bypass_side_from_obstacle(near_pt, obs), bypass_axis, near_pt[2] - z_mid, before, after, route_length / straight, relation_score))
                    count += 1
        self.conn.commit()
        self.obstacle_relation_count = count
        print(f"   - [Obstacle] Learned obstacle-route relations: {count}")"""
new_save_obstacle = """    def save_obstacle_relations(self):
        obstacles = self.load_obstacles_for_routes()
        if not obstacles:
            print("   - [Obstacle] No obstacle AABB data found for relation learning.")
            return
        insert_sql = \"\"\"
            INSERT INTO "TB_ROUTE_FEATURE_OBSTACLE_RELATION"
            ("PROJECT_ID", "ROUTE_PATH_GUID", "OBSTACLE_NAME", "OBSTACLE_TYPE", "OBSTACLE_AXIS", "UTILITY_GROUP", "UTILITY",
             "DIAMETER_MM", "NEAREST_DISTANCE_MM", "REQUIRED_CLEARANCE_MM", "CLEARANCE_MARGIN_MM", "BYPASS_SIDE", "BYPASS_AXIS",
             "Z_DELTA_NEAR_OBSTACLE_MM", "BEND_COUNT_BEFORE", "BEND_COUNT_AFTER", "EXTRA_LENGTH_RATIO", "RELATION_SCORE")
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        \"\"\"
        try:
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM \\"TB_ROUTE_FEATURE_OBSTACLE_RELATION\\" WHERE \\"PROJECT_ID\\" = %s", (self.project_name,))
                count = 0
                for r in self.routes:
                    pts = r.get('points') or []
                    if len(pts) < 2:
                        continue
                    meta = r['meta']
                    diameter = parse_pipe_diameter(meta.get('size')) or 0.0
                    required_clearance = diameter * 0.5 + 150.0
                    route_length = sum(dist_3d(pts[i - 1], pts[i]) for i in range(1, len(pts)))
                    straight = dist_3d(pts[0], pts[-1]) or 1.0
                    bends = route_bends(pts)
                    for obs in obstacles:
                        best = (float('inf'), 0, 0.0, pts[0])
                        for i in range(1, len(pts)):
                            d, t, near_pt = segment_aabb_distance(pts[i - 1], pts[i], obs)
                            if d < best[0]:
                                best = (d, i, t, near_pt)
                        nearest, seg_index, _, near_pt = best
                        if nearest > max(required_clearance + 1000.0, 1800.0):
                            continue
                        before = sum(1 for b in bends if b < seg_index)
                        after = sum(1 for b in bends if b >= seg_index)
                        bypass_axis = get_dominant_face(pts[seg_index][0] - pts[seg_index - 1][0], pts[seg_index][1] - pts[seg_index - 1][1], pts[seg_index][2] - pts[seg_index - 1][2])
                        z_mid = (obs['minz'] + obs['maxz']) * 0.5
                        relation_score = max(0.0, (required_clearance + 1000.0 - nearest) / (required_clearance + 1000.0))
                        cur.execute(insert_sql, (self.project_name, r['guid'], obs['name'], obs['obstacle_type'], obs['axis'], meta.get('utility_group'), meta.get('utility'), diameter, nearest, required_clearance, nearest - required_clearance, bypass_side_from_obstacle(near_pt, obs), bypass_axis, near_pt[2] - z_mid, before, after, route_length / straight, relation_score))
                        count += 1
            self.conn.commit()
            self.obstacle_relation_count = count
            print(f"   - [Obstacle] Learned obstacle-route relations: {count}")
        except Exception as e:
            self.conn.rollback()
            print(f"   - [Obstacle] Failed to save obstacle relations. Transaction rolled back. Error: {e}")"""
content = content.replace(old_save_obstacle, new_save_obstacle)

# --- 2. group profile key update ---
# In learn_design_features, they group by utility_group instead of (utility_group, utility). Let's fix extract_trunk_spine mapping.
old_trunk_spine = """    def extract_trunk_spine(self, r_list):
        if not r_list:
            return
        
        # 유틸리티 그룹별로 배관 분류
        groups = defaultdict(list)
        for r in r_list:
            grp = r['meta'].get('utility_group', 'UNKNOWN')
            groups[grp].append(r)
            
        print(f"   - [Spine] 3D 공용 척추선(Trunk Centerline) 추출 중 (총 {len(groups)}개 유틸리티 그룹)...")"""
new_trunk_spine = """    def extract_trunk_spine(self, r_list):
        if not r_list:
            return
        
        # 유틸리티 그룹/유틸리티별로 배관 분류
        groups = defaultdict(list)
        for r in r_list:
            grp = r['meta'].get('utility_group', 'UNKNOWN')
            utl = r['meta'].get('utility', 'UNKNOWN')
            groups[(grp, utl)].append(r)
            
        print(f"   - [Spine] 3D 공용 척추선(Trunk Centerline) 추출 중 (총 {len(groups)}개 유틸리티 단위)...")"""
content = content.replace(old_trunk_spine, new_trunk_spine)

old_trunk_spine_loop = """        for grp, routes in groups.items():
            pts = []
            for r in routes:"""
new_trunk_spine_loop = """        for (grp, utl), routes in groups.items():
            pts = []
            for r in routes:"""
content = content.replace(old_trunk_spine_loop, new_trunk_spine_loop)

old_save_profile = "            self.save_group_profile(grp, list(start_face_counter.keys())[0] if start_face_counter else '', list(end_face_counter.keys())[0] if end_face_counter else '', rack_zs, simple_spine)"
new_save_profile = "            self.save_group_profile(grp, utl, list(start_face_counter.keys())[0] if start_face_counter else '', list(end_face_counter.keys())[0] if end_face_counter else '', rack_zs, simple_spine)"
content = content.replace(old_save_profile, new_save_profile)

old_save_group_profile_def = """    def save_group_profile(self, grp, s_face, t_face, rack_zs, spine_pts):
        sql = \"\"\"
            INSERT INTO "TB_ROUTE_FEATURE_GROUP_PROFILE"
            ("PROJECT_ID", "MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "PREFERRED_SOURCE_FACE", "PREFERRED_TARGET_FACE", "PREFERRED_RACK_ZS", "TRUNK_CENTERLINE_JSON", "TRUNK_CENTERLINE_GEOM")
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, ST_GeomFromText(%s, 0))
            ON CONFLICT ("PROJECT_ID", "MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY")
            DO UPDATE SET
                "PREFERRED_SOURCE_FACE" = EXCLUDED."PREFERRED_SOURCE_FACE",
                "PREFERRED_TARGET_FACE" = EXCLUDED."PREFERRED_TARGET_FACE",
                "PREFERRED_RACK_ZS" = EXCLUDED."PREFERRED_RACK_ZS",
                "TRUNK_CENTERLINE_JSON" = EXCLUDED."TRUNK_CENTERLINE_JSON",
                "TRUNK_CENTERLINE_GEOM" = EXCLUDED."TRUNK_CENTERLINE_GEOM",
                "UPDATED_AT" = now();
        \"\"\"
        with self.conn.cursor() as cur:
            cur.execute(sql, (self.project_name, self.project_name, grp, grp, s_face, t_face, rack_zs, json.dumps(spine_pts), points_to_wkt_linestring3d(spine_pts)))
        self.conn.commit()"""
new_save_group_profile_def = """    def save_group_profile(self, grp, utl, s_face, t_face, rack_zs, spine_pts):
        sql = \"\"\"
            INSERT INTO "TB_ROUTE_FEATURE_GROUP_PROFILE"
            ("PROJECT_ID", "MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "PREFERRED_SOURCE_FACE", "PREFERRED_TARGET_FACE", "PREFERRED_RACK_ZS", "TRUNK_CENTERLINE_JSON", "TRUNK_CENTERLINE_GEOM")
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, ST_GeomFromText(%s, 0))
            ON CONFLICT ("PROJECT_ID", "MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY")
            DO UPDATE SET
                "PREFERRED_SOURCE_FACE" = EXCLUDED."PREFERRED_SOURCE_FACE",
                "PREFERRED_TARGET_FACE" = EXCLUDED."PREFERRED_TARGET_FACE",
                "PREFERRED_RACK_ZS" = EXCLUDED."PREFERRED_RACK_ZS",
                "TRUNK_CENTERLINE_JSON" = EXCLUDED."TRUNK_CENTERLINE_JSON",
                "TRUNK_CENTERLINE_GEOM" = EXCLUDED."TRUNK_CENTERLINE_GEOM",
                "UPDATED_AT" = now();
        \"\"\"
        with self.conn.cursor() as cur:
            cur.execute(sql, (self.project_name, self.project_name, grp, utl, s_face, t_face, rack_zs, json.dumps(spine_pts), points_to_wkt_linestring3d(spine_pts)))
        self.conn.commit()"""
content = content.replace(old_save_group_profile_def, new_save_group_profile_def)

# --- 3. CLI settings overriding ---
old_cli = """    settings_path = Path(__file__).resolve().parent / "tools.settings.json"
    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
                if "db" in settings:
                    db_conf = settings["db"]
                    args.host = db_conf.get("host", args.host)
                    args.port = str(db_conf.get("port", args.port))
                    args.db = db_conf.get("database", args.db)
                    args.user = db_conf.get("user", args.user)
                    args.password = db_conf.get("password", args.password)
        except Exception as ex:
            print(f"[알림] 설정 파일 불러오는 도중 오류 발생(CLI 인자 사용): {ex}")"""
new_cli = """    # CLI 인자가 기본값(default)과 다르게 명시적으로 입력되었는지 확인
    explicit_args = {arg.split('=')[0].lstrip('-') for arg in sys.argv if arg.startswith('-')}
    
    settings_path = Path(__file__).resolve().parent / "tools.settings.json"
    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
                if "db" in settings:
                    db_conf = settings["db"]
                    if "host" not in explicit_args: args.host = db_conf.get("host", args.host)
                    if "port" not in explicit_args: args.port = str(db_conf.get("port", args.port))
                    if "db" not in explicit_args: args.db = db_conf.get("database", args.db)
                    if "user" not in explicit_args: args.user = db_conf.get("user", args.user)
                    if "password" not in explicit_args: args.password = db_conf.get("password", args.password)
        except Exception as ex:
            print(f"[알림] 설정 파일 불러오는 도중 오류 발생(CLI 인자 사용): {ex}")"""
content = content.replace(old_cli, new_cli)

with open(r'd:\DINNO\DEV\AI-AutoRouting\TopKGen\Tools\learn_design_features.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("refactored obstacle tx, profile key, and CLI overriding")
