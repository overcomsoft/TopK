import sys
import json
import math
import argparse
from collections import Counter, defaultdict
import psycopg2
import psycopg2.extras

# 기존 ExportGroupPattern 모듈에서 핵심 함수 및 상수 임포트
from ExportGroupPattern import (
    extract_pipe_feature,
    compute_similarity,
    get_median,
    get_mode,
    SIM_THRESHOLD,
    PITCH_CV_MAX,
    MAX_PITCH_DISTANCE,
    RESAMPLE_N
)

def dist(a, b) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)

def load_specific_routes(conn, guids: list[str]) -> list[dict]:
    if not guids:
        return []
        
    placeholders = ", ".join(["%s"] * len(guids))
    where_clause = f'WHERE rp."ROUTE_PATH_GUID" IN ({placeholders})'
    
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
        cur.execute(sql, guids)
        rows = cur.fetchall()
        for r in rows:
            guid = r['ROUTE_PATH_GUID'].strip()
            raw_details[guid].append(r)
            route_meta[guid] = {
                'eq_tag': r['EQUIPMENT_TAG'],
                'utility': r['SOURCE_UTILITY'],
                'utility_group': r['UTILITY_GROUP'],
                'size': r['SOURCE_SIZE']
            }
            
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
                'points': pts,
                'meta': route_meta[guid]
            })
            
    return routes

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding='utf-8')
        
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', required=True)
    parser.add_argument('--dbname', required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--password', required=True)
    parser.add_argument('--guids', required=True, help="JSON array of GUIDs")
    
    args = parser.parse_args()
    
    try:
        guids = json.loads(args.guids)
    except Exception as e:
        print(json.dumps({"success": False, "error": f"Invalid guids JSON: {e}"}))
        return
        
    conn_str = f"host={args.host} port={args.port} dbname={args.dbname} user={args.user} password={args.password}"
    
    try:
        conn = psycopg2.connect(conn_str)
    except Exception as e:
        print(json.dumps({"success": False, "error": f"DB connection failed: {e}"}))
        return
        
    try:
        routes = load_specific_routes(conn, guids)
        if len(routes) < 2:
            print(json.dumps({"success": False, "error": "2개 이상의 유효한 배관 경로를 찾을 수 없습니다."}))
            return
            
        features = []
        for r in routes:
            f = extract_pipe_feature(r['guid'], r['points'], r['meta'])
            if f:
                features.append(f)
                
        if len(features) < 2:
            print(json.dumps({"success": False, "error": "선택한 배관에서 유효한 특징점(Feature)을 추출할 수 없습니다."}))
            return
            
        result = {
            "success": True,
            "n_members": len(features),
            "guids": [f['guid'] for f in features],
            "utility_match": {"pass": True, "value": None},
            "similarity": {"pass": True, "avg": 0.0, "threshold": SIM_THRESHOLD},
            "bends": {"pass": True, "median": 0, "min": 2},
            "pitch_cv": {"pass": True, "cv": 0.0, "max": PITCH_CV_MAX},
            "final_verdict": False,
            "messages": []
        }
        
        # 1. Utility match
        utils = set(f['utility'] for f in features)
        if len(utils) > 1:
            result['utility_match']['pass'] = False
            result['utility_match']['value'] = ", ".join(str(u) for u in utils)
            result['messages'].append(f"유틸리티(Utility) 속성이 일치하지 않습니다: {', '.join(str(u) for u in utils)}")
        else:
            result['utility_match']['value'] = list(utils)[0]
            
        # 2. Similarity
        sims = []
        # Python script _similarity_cache is empty, compute fresh
        for i in range(len(features)):
            for j in range(i+1, len(features)):
                sims.append(compute_similarity(features[i], features[j]))
                
        avg_sim = sum(sims) / len(sims) if sims else 0.0
        result['similarity']['avg'] = round(avg_sim, 3)
        if avg_sim < SIM_THRESHOLD:
            result['similarity']['pass'] = False
            result['messages'].append(f"배관 간 평균 형상/방향 유사도가 낮습니다 (점수: {avg_sim:.2f}, 기준: {SIM_THRESHOLD})")
            
        # 3. Bends
        bends_list = [m['n_ortho_bends'] for m in features]
        rep_bends = int(round(get_median(bends_list)))
        result['bends']['median'] = rep_bends
        if rep_bends < 2:
            result['bends']['pass'] = False
            result['messages'].append(f"충분한 꺾임(Bends) 패턴이 없습니다 (중앙값: {rep_bends}회, 기준: 최소 2회)")
            
        # 4. Pitch CV
        axes_list = [m['trunk_axis'] for m in features]
        rep_trunk_axis = get_mode(axes_list)
        
        offsets = [m['centroid'][1] if rep_trunk_axis == 0 else m['centroid'][0] for m in features]
        offsets.sort()
        
        pitches = [offsets[i+1] - offsets[i] for i in range(len(offsets) - 1)]
        mean_pitch = sum(pitches) / len(pitches) if pitches else 0.0
        
        if mean_pitch > 0:
            variance = sum((p - mean_pitch)**2 for p in pitches) / len(pitches)
            std_pitch = math.sqrt(variance)
            cv = std_pitch / mean_pitch
        else:
            cv = 0.0
            
        result['pitch_cv']['cv'] = round(cv, 3)
        if len(pitches) > 1 and cv > PITCH_CV_MAX:
            result['pitch_cv']['pass'] = False
            result['messages'].append(f"배관 간 간격이 불규칙합니다 (변동계수: {cv:.3f}, 최대 허용치: {PITCH_CV_MAX})")
            
        # Final verdict
        if (result['utility_match']['pass'] and 
            result['similarity']['pass'] and 
            result['bends']['pass'] and 
            result['pitch_cv']['pass']):
            result['final_verdict'] = True
            result['messages'].insert(0, "모든 그룹핑 조건을 통과하였습니다! 패턴 그룹 생성이 가능합니다.")
        else:
            result['final_verdict'] = False
            
        print(json.dumps(result, ensure_ascii=False))
        
    except Exception as e:
        print(json.dumps({"success": False, "error": f"Analysis failed: {str(e)}"}))
        
    finally:
        conn.close()

if __name__ == "__main__":
    main()
