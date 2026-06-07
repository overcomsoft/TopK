import psycopg2
import math
from collections import defaultdict

def analyze():
    conn_str = "host=localhost port=5432 dbname=DDW_AI_DB user=postgres password=dinno"
    try:
        conn = psycopg2.connect(conn_str)
    except Exception as e:
        print(f"DB Connection failed: {e}")
        return

    cur = conn.cursor()
    
    # 1. CLEAN 장비의 Exhaust 관련 경로 기본 정보
    query = """
        SELECT 
            rp."ROUTE_PATH_GUID",
            rp."EQUIPMENT_NAME",
            rp."SOURCE_UTILITY",
            sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",
            sd."TO_POSX", sd."TO_POSY", sd."TO_POSZ"
        FROM "TB_ROUTE_PATH" rp
        JOIN "TB_ROUTE_SEGMENTS" rs ON rp."ROUTE_PATH_GUID" = rs."ROUTE_PATH_GUID"
        JOIN "TB_ROUTE_SEGMENT_DETAIL" sd ON rs."SEGMENT_GUID" = sd."SEGMENT_GUID"
        WHERE rp."EQUIPMENT_NAME" ILIKE '%CLEAN%' 
          AND rp."SOURCE_UTILITY" ILIKE '%EX%'
    """
    
    cur.execute(query)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("CLEAN 장비의 Exhaust 유틸리티 그룹 배관 경로가 없습니다.")
        return

    routes = defaultdict(lambda: {'eq_name': '', 'utility': '', 'length': 0.0, 'segments': 0})
    
    for row in rows:
        guid = row[0]
        eq_name = row[1]
        util = row[2]
        
        fx, fy, fz = row[3], row[4], row[5]
        tx, ty, tz = row[6], row[7], row[8]
        
        if fx is None or tx is None:
            continue
            
        dist = math.sqrt((tx-fx)**2 + (ty-fy)**2 + (tz-fz)**2)
        
        routes[guid]['eq_name'] = eq_name
        routes[guid]['utility'] = util
        routes[guid]['length'] += dist
        routes[guid]['segments'] += 1

    total_paths = len(routes)
    total_length = sum(r['length'] for r in routes.values())
    avg_length = total_length / total_paths if total_paths > 0 else 0
    max_length_path = max(routes.items(), key=lambda x: x[1]['length']) if total_paths > 0 else None
    
    # 통계 출력
    print("=" * 50)
    print("📊 CLEAN 장비 - Exhaust 유틸리티 그룹 배관경로 통계")
    print("=" * 50)
    print(f"총 분석된 배관(Route Path) 개수: {total_paths} 개")
    print(f"전체 배관 길이의 합계: {total_length/1000.0:,.2f} m ({total_length:,.0f} mm)")
    print(f"배관 1개당 평균 길이: {avg_length/1000.0:,.2f} m")
    
    if max_length_path:
        print(f"가장 긴 배관 경로 (GUID: {max_length_path[0][:8]}...): {max_length_path[1]['length']/1000.0:,.2f} m (장비: {max_length_path[1]['eq_name']}, 유틸: {max_length_path[1]['utility']})")
    
    print("-" * 50)
    
    # 장비별/유틸리티별 세분화
    by_eq = defaultdict(lambda: {'count': 0, 'length': 0.0})
    by_util = defaultdict(lambda: {'count': 0, 'length': 0.0})
    
    for r in routes.values():
        by_eq[r['eq_name']]['count'] += 1
        by_eq[r['eq_name']]['length'] += r['length']
        
        by_util[r['utility']]['count'] += 1
        by_util[r['utility']]['length'] += r['length']
        
    print("[유틸리티 종류별 통계]")
    for u, stats in by_util.items():
        print(f" - {u}: {stats['count']} 라인, 총 {stats['length']/1000.0:,.2f} m")
        
    print("\n[주요 CLEAN 장비별 통계 (Top 5 길이)]")
    sorted_eq = sorted(by_eq.items(), key=lambda x: x[1]['length'], reverse=True)[:5]
    for eq, stats in sorted_eq:
        print(f" - {eq}: {stats['count']} 라인, 총 {stats['length']/1000.0:,.2f} m")
    print("=" * 50)

if __name__ == '__main__':
    analyze()
