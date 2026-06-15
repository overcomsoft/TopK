import sys
import json
import math
import argparse
from collections import defaultdict
import psycopg2
import psycopg2.extras

def dist_3d(a, b):
    return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)

def get_direction_enum(a, b):
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    dz = b[2] - a[2]
    mx = max(abs(dx), abs(dy), abs(dz))
    if mx == 0: return "None"
    if mx == abs(dx): return "+X" if dx > 0 else "-X"
    if mx == abs(dy): return "+Y" if dy > 0 else "-Y"
    if mx == abs(dz): return "+Z" if dz > 0 else "-Z"
    return "None"

def get_axis_name(direction):
    if "X" in direction: return "X"
    if "Y" in direction: return "Y"
    if "Z" in direction: return "Z"
    return "None"

def segment_overlap_length(s1, s2, axis):
    if axis == "X":
        min1, max1 = min(s1['start'][0], s1['end'][0]), max(s1['start'][0], s1['end'][0])
        min2, max2 = min(s2['start'][0], s2['end'][0]), max(s2['start'][0], s2['end'][0])
    elif axis == "Y":
        min1, max1 = min(s1['start'][1], s1['end'][1]), max(s1['start'][1], s1['end'][1])
        min2, max2 = min(s2['start'][1], s2['end'][1]), max(s2['start'][1], s2['end'][1])
    else:
        min1, max1 = min(s1['start'][2], s1['end'][2]), max(s1['start'][2], s1['end'][2])
        min2, max2 = min(s2['start'][2], s2['end'][2]), max(s2['start'][2], s2['end'][2])
    
    overlap = max(0.0, min(max1, max2) - max(min1, min2))
    return overlap

def segment_2d_distance(s1, s2, axis):
    # s1 start and end should have same 2D coords
    if axis == "X":
        return math.sqrt((s1['start'][1] - s2['start'][1])**2 + (s1['start'][2] - s2['start'][2])**2)
    elif axis == "Y":
        return math.sqrt((s1['start'][0] - s2['start'][0])**2 + (s1['start'][2] - s2['start'][2])**2)
    else:
        return math.sqrt((s1['start'][0] - s2['start'][0])**2 + (s1['start'][1] - s2['start'][1])**2)

class UnionFind:
    def __init__(self, elements):
        self.parent = {el: el for el in elements}
        self.rank = {el: 0 for el in elements}
        
    def find(self, x):
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]
        
    def union(self, x, y):
        rx = self.find(x)
        ry = self.find(y)
        if rx != ry:
            if self.rank[rx] > self.rank[ry]:
                self.parent[ry] = rx
            else:
                self.parent[rx] = ry
                if self.rank[rx] == self.rank[ry]:
                    self.rank[ry] += 1

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding='utf-8')

    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', required=True)
    parser.add_argument('--db', required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--password', required=True)
    args = parser.parse_args()

    try:
        conn = psycopg2.connect(
            host=args.host,
            port=args.port,
            dbname=args.db,
            user=args.user,
            password=args.password
        )
        conn.autocommit = True
    except Exception as e:
        print(json.dumps({"success": False, "error": f"DB 연결 실패: {str(e)}"}))
        return

    try:
        # 1. Fetch DB
        sql = """
            SELECT 
                rp."ROUTE_PATH_GUID",
                rp."EQUIPMENT_TAG",
                rp."UTILITY_GROUP",
                sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",
                sd."TO_POSX", sd."TO_POSY", sd."TO_POSZ"
            FROM "TB_ROUTE_PATH" rp
            JOIN "TB_ROUTE_SEGMENTS" rs ON rp."ROUTE_PATH_GUID" = rs."ROUTE_PATH_GUID"
            JOIN "TB_ROUTE_SEGMENT_DETAIL" sd ON rs."SEGMENT_GUID" = sd."SEGMENT_GUID"
        """
        segments = []
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            for i, r in enumerate(rows):
                a = (r['FROM_POSX'], r['FROM_POSY'], r['FROM_POSZ'])
                b = (r['TO_POSX'], r['TO_POSY'], r['TO_POSZ'])
                length = dist_3d(a, b)
                if length >= 500.0:
                    d = get_direction_enum(a, b)
                    if d != "None":
                        segments.append({
                            'id': i,
                            'guid': r['ROUTE_PATH_GUID'].strip(),
                            'eq_tag': r['EQUIPMENT_TAG'] if r['EQUIPMENT_TAG'] else "UNKNOWN",
                            'utility_group': r['UTILITY_GROUP'] if r['UTILITY_GROUP'] else "UNKNOWN",
                            'start': a,
                            'end': b,
                            'length': length,
                            'direction': d,
                            'axis': get_axis_name(d)
                        })

        # 2. Group by eq_tag, utility_group, direction
        groups = defaultdict(list)
        for s in segments:
            key = (s['eq_tag'], s['utility_group'], s['direction'])
            groups[key].append(s)

        clusters = []
        cluster_id = 1
        
        # 3. Cluster within groups
        MAX_DIST = 1000.0 # 1 meter max pitch
        MIN_OVERLAP = 500.0

        for key, segs in groups.items():
            if len(segs) < 2:
                continue
            
            uf = UnionFind([s['id'] for s in segs])
            axis = segs[0]['axis']

            for i in range(len(segs)):
                for j in range(i+1, len(segs)):
                    s1 = segs[i]
                    s2 = segs[j]
                    
                    if s1['guid'] == s2['guid']:
                        continue # Same pipe path segments? Skip grouping with itself
                    
                    d2d = segment_2d_distance(s1, s2, axis)
                    if d2d <= MAX_DIST:
                        overlap = segment_overlap_length(s1, s2, axis)
                        if overlap >= MIN_OVERLAP:
                            uf.union(s1['id'], s2['id'])
            
            # Extract clusters
            clustered_ids = defaultdict(list)
            for s in segs:
                root = uf.find(s['id'])
                clustered_ids[root].append(s)
            
            for root, grouped_segs in clustered_ids.items():
                if len(grouped_segs) >= 2: # Only keep clusters of 2 or more
                    direction = key[2]
                    axis = get_axis_name(direction)
                    
                    rep_x = sum((s['start'][0] + s['end'][0]) / 2 for s in grouped_segs) / len(grouped_segs)
                    rep_y = sum((s['start'][1] + s['end'][1]) / 2 for s in grouped_segs) / len(grouped_segs)
                    rep_z = sum((s['start'][2] + s['end'][2]) / 2 for s in grouped_segs) / len(grouped_segs)
                    
                    if axis == "X":
                        min_val = min(min(s['start'][0], s['end'][0]) for s in grouped_segs)
                        max_val = max(max(s['start'][0], s['end'][0]) for s in grouped_segs)
                        rep_length = max_val - min_val
                    elif axis == "Y":
                        min_val = min(min(s['start'][1], s['end'][1]) for s in grouped_segs)
                        max_val = max(max(s['start'][1], s['end'][1]) for s in grouped_segs)
                        rep_length = max_val - min_val
                    else: # Z
                        min_val = min(min(s['start'][2], s['end'][2]) for s in grouped_segs)
                        max_val = max(max(s['start'][2], s['end'][2]) for s in grouped_segs)
                        rep_length = max_val - min_val

                    clusters.append({
                        'group_id': cluster_id,
                        'eq_tag': key[0],
                        'utility_group': key[1],
                        'direction': direction,
                        'rep_x': rep_x,
                        'rep_y': rep_y,
                        'rep_z': rep_z,
                        'rep_length': rep_length,
                        'segments': grouped_segs
                    })
                    cluster_id += 1

        # 4. Save to Database
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "TB_GROUP_SEGMENTS" (
                    "ID" SERIAL PRIMARY KEY,
                    "GROUP_ID" INTEGER,
                    "EQUIPMENT_TAG" VARCHAR(255),
                    "UTILITY_GROUP" VARCHAR(255),
                    "DIRECTION" VARCHAR(10),
                    "ROUTE_PATH_GUID" VARCHAR(255),
                    "START_X" FLOAT, "START_Y" FLOAT, "START_Z" FLOAT,
                    "END_X" FLOAT, "END_Y" FLOAT, "END_Z" FLOAT,
                    "LENGTH" FLOAT,
                    "REP_X" FLOAT, "REP_Y" FLOAT, "REP_Z" FLOAT, "REP_LENGTH" FLOAT
                )
            """)
            
            # 컬럼이 존재하지 않을 경우 추가 (마이그레이션 방어)
            cur.execute("""
                DO $$
                BEGIN
                    BEGIN
                        ALTER TABLE "TB_GROUP_SEGMENTS" ADD COLUMN "REP_X" FLOAT;
                    EXCEPTION
                        WHEN duplicate_column THEN NULL;
                    END;
                    BEGIN
                        ALTER TABLE "TB_GROUP_SEGMENTS" ADD COLUMN "REP_Y" FLOAT;
                    EXCEPTION
                        WHEN duplicate_column THEN NULL;
                    END;
                    BEGIN
                        ALTER TABLE "TB_GROUP_SEGMENTS" ADD COLUMN "REP_Z" FLOAT;
                    EXCEPTION
                        WHEN duplicate_column THEN NULL;
                    END;
                    BEGIN
                        ALTER TABLE "TB_GROUP_SEGMENTS" ADD COLUMN "REP_LENGTH" FLOAT;
                    EXCEPTION
                        WHEN duplicate_column THEN NULL;
                    END;
                END $$;
            """)

            cur.execute('TRUNCATE TABLE "TB_GROUP_SEGMENTS"')
            
            insert_sql = """
                INSERT INTO "TB_GROUP_SEGMENTS" 
                ("GROUP_ID", "EQUIPMENT_TAG", "UTILITY_GROUP", "DIRECTION", "ROUTE_PATH_GUID", 
                 "START_X", "START_Y", "START_Z", "END_X", "END_Y", "END_Z", "LENGTH",
                 "REP_X", "REP_Y", "REP_Z", "REP_LENGTH")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            for c in clusters:
                for s in c['segments']:
                    cur.execute(insert_sql, (
                        c['group_id'], c['eq_tag'], c['utility_group'], c['direction'], s['guid'],
                        s['start'][0], s['start'][1], s['start'][2],
                        s['end'][0], s['end'][1], s['end'][2],
                        s['length'],
                        c['rep_x'], c['rep_y'], c['rep_z'], c['rep_length']
                    ))

        print(json.dumps({
            "success": True, 
            "total_clusters": len(clusters),
            "total_segments": sum(len(c['segments']) for c in clusters)
        }))

    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))
    finally:
        if 'conn' in locals() and conn:
            conn.close()

if __name__ == "__main__":
    main()
