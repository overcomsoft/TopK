import re
import math
import os

with open(r'd:\DINNO\DEV\AI-AutoRouting\TopKGen\Tools\learn_design_features.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update save_route_similarity_vectors (fallback + dynamic max)
match = re.search(r'    def save_route_similarity_vectors\(self\):.*?BBOX_MAX_X = 9759\.011874999997.*?for r in self\.routes:', content, re.DOTALL)
if match:
    old_body = match.group(0)
    new_body = '''    def save_route_similarity_vectors(self):
        """
        프로젝트 내 모든 배관 경로의 30차원 유사설계 특징 벡터를 계산하여 
        TB_ROUTE_FEATURE_VECTOR 테이블에 저장(Upsert)합니다.
        """
        if not self.pgvector_enabled:
            print("   - [Notice] pgvector가 비활성화되어 특징 벡터 DB 생성을 건너뜁니다.")
            return

        print("   - [유사 설계 벡터 생성] 30D 특징 벡터 각 방향 패턴 생성 및 DB 적재 중...")
        
        sql = """
            INSERT INTO "TB_ROUTE_FEATURE_VECTOR" (
                "ROUTE_PATH_GUID", "PROCESS_NAME", "EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "SIZE",
                "DIRECTION_PATTERN", "TOTAL_LENGTH_MM", "STEP_COUNT",
                "START_POSX", "START_POSY", "START_POSZ",
                "END_POSX", "END_POSY", "END_POSZ",
                "FEATURE_VECTOR"
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
            ON CONFLICT ("ROUTE_PATH_GUID")
            DO UPDATE SET
                "PROCESS_NAME" = EXCLUDED."PROCESS_NAME",
                "EQUIPMENT_NAME" = EXCLUDED."EQUIPMENT_NAME",
                "UTILITY_GROUP" = EXCLUDED."UTILITY_GROUP",
                "UTILITY" = EXCLUDED."UTILITY",
                "SIZE" = EXCLUDED."SIZE",
                "DIRECTION_PATTERN" = EXCLUDED."DIRECTION_PATTERN",
                "TOTAL_LENGTH_MM" = EXCLUDED."TOTAL_LENGTH_MM",
                "STEP_COUNT" = EXCLUDED."STEP_COUNT",
                "START_POSX" = EXCLUDED."START_POSX",
                "START_POSY" = EXCLUDED."START_POSY",
                "START_POSZ" = EXCLUDED."START_POSZ",
                "END_POSX" = EXCLUDED."END_POSX",
                "END_POSY" = EXCLUDED."END_POSY",
                "END_POSZ" = EXCLUDED."END_POSZ",
                "FEATURE_VECTOR" = EXCLUDED."FEATURE_VECTOR";
        """
        
        WEIGHT_MAP = [
            ("start_topology", 0,  3,  0.20),
            ("end_topology",   3,  6,  0.20),
            ("displacement",   6,  9,  0.15),
            ("bounding_box",   9,  12, 0.15),
            ("segment_1",      12, 15, 0.06),
            ("segment_2",      15, 18, 0.06),
            ("segment_3",      18, 21, 0.06),
            ("env_cost",       21, 25, 0.12),
            ("arrow_pattern",  25, 30, 0.15),
        ]
        
        scale_factors = [1.0] * 30
        for name, start, end, weight in WEIGHT_MAP:
            dim = end - start
            if weight > 0 and dim > 0:
                factor = math.sqrt(weight * 30.0 / dim)
                for j in range(start, end):
                    scale_factors[j] = factor
                    
        def dist_3d_local(p1, p2):
            return math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2 + (p1[2]-p2[2])**2)
            
        if self.routes:
            BBOX_MAX_X = max(abs(max(p[0] for p in r['points']) - min(p[0] for p in r['points'])) for r in self.routes) or 1.0
            BBOX_MAX_Y = max(abs(max(p[1] for p in r['points']) - min(p[1] for p in r['points'])) for r in self.routes) or 1.0
            BBOX_MAX_Z = max(abs(max(p[2] for p in r['points']) - min(p[2] for p in r['points'])) for r in self.routes) or 1.0
            DISPLACEMENT_MAX = max(dist_3d_local(r['points'][0], r['points'][-1]) for r in self.routes) or 1.0
            TOTAL_LENGTH_MAX = max(sum(dist_3d_local(r['points'][i], r['points'][i+1]) for i in range(len(r['points'])-1)) for r in self.routes) or 1.0
        else:
            BBOX_MAX_X = BBOX_MAX_Y = BBOX_MAX_Z = DISPLACEMENT_MAX = TOTAL_LENGTH_MAX = 1.0
        
        count = 0
        with self.conn.cursor() as cur:
            for r in self.routes:'''
    content = content.replace(old_body, new_body)

with open(r'd:\DINNO\DEV\AI-AutoRouting\TopKGen\Tools\learn_design_features.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("refactored save_route_similarity_vectors max and fallback")
