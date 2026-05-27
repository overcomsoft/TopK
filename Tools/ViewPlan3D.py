"""
[실행 명령어]
기본 실행 (전체 데이터 로드 및 Plotly 3D 웹 뷰어 실행):
> python ViewPlan3D.py

특정 층(Level) 데이터만 필터링하여 가볍게 뷰잉:
> python ViewPlan3D.py --level 1F

불러올 레터럴(Lateral Pipe) 객체 개수 제한 (브라우저 메모리 최적화용):
> python ViewPlan3D.py --limit 1000

[전체 흐름도]
1. DB 연결 및 노드 유틸리티 메타데이터 수집
   - psycopg2를 통해 PostgreSQL DB(DDW_AI_DB)에 접속하여 배관 노드별 유틸리티 캐시를 수집합니다.
2. 3D 형상 데이터 로드
   - TB_EQUIPMENTS(장비), TB_DUCT(덕트), TB_LATERAL_PIPE(지표 배관) 테이블에서 각 객체의 3D OBB 정점 8개 및 PoC 리스트를 일괄 로드합니다.
   - 명령줄 인자로 들어온 `--level` 값이 있으면 특정 층 데이터만 필터링합니다.
3. Plotly 3D Mesh 최적화 기하 생성 (병합 렌더링)
   - 3D OBB 정점을 활용해 장비, 덕트, 배관의 3D Box(Cube) 정점과 면(Triangle)들을 구성합니다.
   - 3D 뷰어 속도 저하를 완벽 차단하기 위해 모든 객체를 개별 Trace로 만들지 않고, 카테고리별로 정점 인덱스를 시프트하며 하나의 거대한 go.Mesh3d 레이어로 병합(Concatenation)합니다.
   - 반투명 색상(rgba 적용)을 설정합니다:
     * 장비 (Equipment): 반투명 로열블루 (rgba(70, 130, 180, 0.4))
     * 덕트 (Duct): 반투명 주황색 (rgba(255, 140, 0, 0.4))
     * 지표 배관 (Lateral): 반투명 보라색 (rgba(147, 112, 219, 0.35))
4. PoC Sphere 정밀 시각화
   - PoC를 3D 절대 공간 상에 실제 반지름 크기(Radius in mm)에 정확히 일치하는 3D Sphere 매쉬로 수학적 생성(Lightweight UV Sphere 알고리즘)합니다.
   - 유틸리티별 고유 불투명 색상을 적용하고 하나의 유틸리티 레이어별로 병합해 드로우 콜을 1개로 줄입니다.
5. HTML 로컬 저장 및 웹 브라우저 실행
   - 완성된 3D Scene을 d:/DINNO/DEV/AI-AutoRouting/TopKGen/data/output/view_3d_plan.html 파일로 영구 저장합니다.
   - 브라우저 창을 자동으로 열어 마우스 드래그를 이용한 60 FPS 회전, 줌, 패닝, 팁툴 텍스트 조회를 지원합니다.
"""

import os
import json
import argparse
import math
import psycopg2
import plotly.graph_objects as go

# 유틸리티별 PoC 3D Sphere 적용 불투명 RGB 색상 정의 테이블
UTILITY_COLORS = {
    'PCW_S': 'rgb(0, 0, 255)',       # Blue (파란색)
    'PCW_R': 'rgb(135, 206, 250)',   # Light Blue (연하늘색)
    'EX': 'rgb(255, 69, 0)',         # Orange-Red (주황색)
    'CDA': 'rgb(34, 139, 34)',       # Forest Green (녹색)
    'PV': 'rgb(220, 20, 60)',        # Crimson Red (빨간색)
    'DEFAULT': 'rgb(120, 120, 120)'  # Gray (회색)
}

def parse_size_to_radius(size_str: str) -> float:
    """
    배관/덕트 구경 문자열을 파싱하여 mm 단위 반지름으로 변환합니다.
    """
    if not size_str:
        return 25.0
    size_str = size_str.upper().replace('B', '').strip()
    try:
        if 'X' in size_str:
            parts = size_str.split('X')
            w = float(parts[0].strip())
            h = float(parts[1].strip())
            return (w + h) / 4.0
        elif '*' in size_str:
            parts = size_str.split('*')
            w = float(parts[0].strip())
            h = float(parts[1].strip())
            return (w + h) / 4.0
            
        if ' ' in size_str:
            whole, frac = size_str.split(' ')
            num, den = frac.split('/')
            val = float(whole) + float(num) / float(den)
        elif '/' in size_str:
            num, den = size_str.split('/')
            val = float(num) / float(den)
        else:
            val = float(size_str)
            
        if val < 36.0:
            return val * 25.4 / 2.0
        else:
            return val / 2.0
    except:
        return 25.0

def generate_sphere_geometry(cx, cy, cz, r, num_lat=6, num_lon=8):
    """
    특정 중심 좌표와 반지름을 가지는 구(Sphere)의 정점(Vertices)과 삼각면(Faces)을 수학적으로 계산해 냅니다.
    드로잉 성능 극대화를 위해 위도(num_lat), 경도(num_lon) 세분화를 최소화한 경량화 구를 구축합니다.
    """
    vertices = []
    # 북극점 추가
    vertices.append((cx, cy, cz + r))
    
    for i in range(1, num_lat):
        theta = math.pi * i / num_lat
        sin_theta = math.sin(theta)
        cos_theta = math.cos(theta)
        for j in range(num_lon):
            phi = 2 * math.pi * j / num_lon
            x = cx + r * sin_theta * math.cos(phi)
            y = cy + r * sin_theta * math.sin(phi)
            z = cz + r * cos_theta
            vertices.append((x, y, z))
            
    # 남극점 추가
    vertices.append((cx, cy, cz - r))
    
    faces = []
    # 북극 캡 삼각형 생성
    for j in range(num_lon):
        n1 = 1 + j
        n2 = 1 + (j + 1) % num_lon
        faces.append([0, n1, n2])
        
    # 구체 바디 사각형 영역을 2개 삼각형씩 분할 생성
    for i in range(1, num_lat - 1):
        for j in range(num_lon):
            a = 1 + (i - 1) * num_lon + j
            b = 1 + (i - 1) * num_lon + (j + 1) % num_lon
            c = a + num_lon
            d = b + num_lon
            faces.append([a, c, b])
            faces.append([b, c, d])
            
    # 남극 캡 삼각형 생성
    last_idx = len(vertices) - 1
    for j in range(num_lon):
        n1 = last_idx - num_lon + j
        n2 = last_idx - num_lon + (j + 1) % num_lon
        faces.append([last_idx, n2, n1])
        
    return vertices, faces

def sort_box_vertices(vertices):
    """
    Sorts 8 vertices of a cuboid into a standard predictable order:
    - The first 4 (indices 0, 1, 2, 3) are bottom vertices ordered CCW on the XY plane.
    - The last 4 (indices 4, 5, 6, 7) are corresponding top vertices directly above 0, 1, 2, 3.
    """
    # 1. Sort all 8 vertices by Z to separate bottom 4 and top 4
    sorted_by_z = sorted(vertices, key=lambda p: p[2])
    bottom_4 = sorted_by_z[:4]
    top_4 = sorted_by_z[4:]
    
    # 2. Sort bottom 4 CCW on XY plane
    cx = sum(p[0] for p in bottom_4) / 4.0
    cy = sum(p[1] for p in bottom_4) / 4.0
    def angle(p):
        return math.atan2(p[1] - cy, p[0] - cx)
    bottom_sorted = sorted(bottom_4, key=angle)
    
    # 3. Match each top vertex to the closest bottom vertex in the XY plane
    top_sorted = []
    for b in bottom_sorted:
        best_t = min(top_4, key=lambda t: (t[0]-b[0])**2 + (t[1]-b[1])**2)
        top_sorted.append(best_t)
        
    return bottom_sorted + top_sorted

def fetch_category_data(cur, table_name, utility_map, level_filter, limit=None):
    """
    지정된 테이블로부터 OBB 및 PoC 정보를 데이터베이스로부터 쿼리하여 리스트로 정리합니다.
    """
    query = f'''
        SELECT "INSTANCE_NAME", "UTILITY", "LEVEL",
               "OBB_LEFT_BOTTOM_BACK_X", "OBB_LEFT_BOTTOM_BACK_Y", "OBB_LEFT_BOTTOM_BACK_Z",
               "OBB_RIGHT_BOTTOM_BACK_X", "OBB_RIGHT_BOTTOM_BACK_Y", "OBB_RIGHT_BOTTOM_BACK_Z",
               "OBB_RIGHT_TOP_BACK_X", "OBB_RIGHT_TOP_BACK_Y", "OBB_RIGHT_TOP_BACK_Z",
               "OBB_LEFT_TOP_BACK_X", "OBB_LEFT_TOP_BACK_Y", "OBB_LEFT_TOP_BACK_Z",
               "OBB_LEFT_BOTTOM_FRONT_X", "OBB_LEFT_BOTTOM_FRONT_Y", "OBB_LEFT_BOTTOM_FRONT_Z",
               "OBB_RIGHT_BOTTOM_FRONT_X", "OBB_RIGHT_BOTTOM_FRONT_Y", "OBB_RIGHT_BOTTOM_FRONT_Z",
               "OBB_RIGHT_TOP_FRONT_X", "OBB_RIGHT_TOP_FRONT_Y", "OBB_RIGHT_TOP_FRONT_Z",
               "OBB_LEFT_TOP_FRONT_X", "OBB_LEFT_TOP_FRONT_Y", "OBB_LEFT_TOP_FRONT_Z",
               "POC_ID_LIST", "POC_POSITIONS_LIST", "POC_SIZES_LIST"
        FROM "{table_name}"
        WHERE "OBB_LEFT_BOTTOM_BACK_X" IS NOT NULL
        {level_filter}
    '''
    if limit:
        query += f' LIMIT {limit}'
        
    cur.execute(query)
    results = []
    
    for row in cur.fetchall():
        (name, utility_col, level,
         lbb_x, lbb_y, lbb_z,
         rbb_x, rbb_y, rbb_z,
         rtb_x, rtb_y, rtb_z,
         ltb_x, ltb_y, ltb_z,
         lbf_x, lbf_y, lbf_z,
         rbf_x, rbf_y, rbf_z,
         rtf_x, rtf_y, rtf_z,
         ltf_x, ltf_y, ltf_z,
         poc_ids, poc_pos, poc_sizes) = row
         
        vertices = [
            (lbb_x, lbb_y, lbb_z),
            (rbb_x, rbb_y, rbb_z),
            (rtb_x, rtb_y, rtb_z),
            (ltb_x, ltb_y, ltb_z),
            (lbf_x, lbf_y, lbf_z),
            (rbf_x, rbf_y, rbf_z),
            (rtf_x, rtf_y, rtf_z),
            (ltf_x, ltf_y, ltf_z)
        ]
        vertices = sort_box_vertices(vertices)

        
        pocs = []
        if poc_pos:
            try:
                pos_list = json.loads(poc_pos)
                id_list = json.loads(poc_ids) if poc_ids else []
                size_list = json.loads(poc_sizes) if poc_sizes else []
                
                for i, pos_dict in enumerate(pos_list):
                    pid = pos_dict.get('id', '')
                    if not pid and i < len(id_list):
                        pid = id_list[i]
                        
                    size_str = size_list[i] if i < len(size_list) else ''
                    radius = parse_size_to_radius(size_str)
                    utility = utility_map.get(pid) or utility_col or 'DEFAULT'
                    
                    pocs.append({
                        'x': pos_dict['x'],
                        'y': pos_dict['y'],
                        'z': pos_dict.get('z', 0.0),
                        'radius': radius,
                        'utility': utility
                    })
            except:
                pass
                
        results.append({
            'name': name,
            'vertices': vertices,
            'pocs': pocs,
            'level': level
        })
        
    return results

def add_concatenated_mesh_trace(fig, name_list, vertices_list, faces_template, color, opacity, trace_name):
    """
    수천 개의 개별 OBB 박스들을 인덱스 시프트 방식을 통하여 단 하나의 고속 3D Mesh Trace로 병합합니다.
    """
    if not vertices_list:
        return
        
    x_all, y_all, z_all = [], [], []
    i_all, j_all, k_all = [], [], []
    text_all = []
    
    for idx, (name, vertices) in enumerate(zip(name_list, vertices_list)):
        shift = idx * 8
        for v in vertices:
            x_all.append(v[0])
            y_all.append(v[1])
            z_all.append(v[2])
            text_all.append(name) # 호버 텍스트 매핑
            
        for f in faces_template:
            i_all.append(f[0] + shift)
            j_all.append(f[1] + shift)
            k_all.append(f[2] + shift)
            
    fig.add_trace(go.Mesh3d(
        x=x_all, y=y_all, z=z_all,
        i=i_all, j=j_all, k=k_all,
        color=color,
        opacity=opacity,
        name=trace_name,
        showlegend=True,
        text=text_all,
        hoverinfo='text'
    ))

def add_concatenated_poc_spheres(fig, poc_list, subdivisions=1):
    """
    PoC Sphere 구체들을 생성하고, 동일한 유틸리티에 속하는 구체들을 
    하나의 go.Mesh3d 레이어로 병합하여 3D 로딩 속도를 향상시킵니다.
    """
    if not poc_list:
        return
        
    # 유틸리티별로 PoC 데이터를 분류
    utility_groups = {}
    for poc in poc_list:
        u = poc['utility'].upper()
        if u not in UTILITY_COLORS:
            u = 'DEFAULT'
        if u not in utility_groups:
            utility_groups[u] = []
        utility_groups[u].append(poc)
        
    for u, pocs in utility_groups.items():
        x_all, y_all, z_all = [], [], []
        i_all, j_all, k_all = [], [], []
        text_all = []
        vertex_offset = 0
        
        # 1 subdivisions 구체 기준
        num_lat, num_lon = (5, 6) if u == 'DEFAULT' else (6, 8)
        
        for poc in pocs:
            # 구체 기하 정점과 면 계산
            v_sphere, f_sphere = generate_sphere_geometry(
                poc['x'], poc['y'], poc['z'], poc['radius'],
                num_lat=num_lat, num_lon=num_lon
            )
            
            for v in v_sphere:
                x_all.append(v[0])
                y_all.append(v[1])
                z_all.append(v[2])
                text_all.append(f"PoC [{poc['utility']}]<br>Radius: {poc['radius']:.1f} mm")
                
            for f in f_sphere:
                i_all.append(f[0] + vertex_offset)
                j_all.append(f[1] + vertex_offset)
                k_all.append(f[2] + vertex_offset)
                
            vertex_offset += len(v_sphere)
            
        fig.add_trace(go.Mesh3d(
            x=x_all, y=y_all, z=z_all,
            i=i_all, j=j_all, k=k_all,
            color=UTILITY_COLORS[u],
            opacity=1.0,
            name=f"PoC: {u}",
            showlegend=True,
            text=text_all,
            hoverinfo='text'
        ))

def main():
    parser = argparse.ArgumentParser(description="3D interactive Plotly visualization for Equipments, Ducts, and Laterals.")
    parser.add_argument("--level", default=None, help="Filter by level/floor (e.g. 1F, 2F, 3F)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of laterals to load to prevent lags")
    args = parser.parse_args()
    
    conn_str = "host=localhost port=5432 dbname=DDW_AI_DB user=postgres password=dinno"
    print("Connecting to database...")
    try:
        conn = psycopg2.connect(conn_str)
    except Exception as e:
        print(f"DB Connection failed: {e}")
        return

    cur = conn.cursor()
    
    # 1. 노드 유틸리티 매핑 정보 캐시 구축
    print("Caching utility maps...")
    utility_map = {}
    cur.execute('SELECT "NODE_GUID", "ID", "UTILITY" FROM "TB_ROUTE_NODES"')
    for row in cur.fetchall():
        node_guid, node_id, util = row
        if util:
            if node_guid: utility_map[node_guid] = util
            if node_id: utility_map[node_id] = util
            
    # 층(Level) 필터 SQL 조건 조합
    level_filter = ""
    if args.level:
        print(f"Applying level filter: {args.level}")
        level_filter = f" AND \"LEVEL\" = '{args.level}'"
        
    # 2. 데이터 일괄 수집
    print("Fetching Equipments...")
    eq_query = f'''
        SELECT "INSTANCE_NAME", NULL AS "UTILITY", "LEVEL",
               "OBB_LEFT_BOTTOM_BACK_X", "OBB_LEFT_BOTTOM_BACK_Y", "OBB_LEFT_BOTTOM_BACK_Z",
               "OBB_RIGHT_BOTTOM_BACK_X", "OBB_RIGHT_BOTTOM_BACK_Y", "OBB_RIGHT_BOTTOM_BACK_Z",
               "OBB_RIGHT_TOP_BACK_X", "OBB_RIGHT_TOP_BACK_Y", "OBB_RIGHT_TOP_BACK_Z",
               "OBB_LEFT_TOP_BACK_X", "OBB_LEFT_TOP_BACK_Y", "OBB_LEFT_TOP_BACK_Z",
               "OBB_LEFT_BOTTOM_FRONT_X", "OBB_LEFT_BOTTOM_FRONT_Y", "OBB_LEFT_BOTTOM_FRONT_Z",
               "OBB_RIGHT_BOTTOM_FRONT_X", "OBB_RIGHT_BOTTOM_FRONT_Y", "OBB_RIGHT_BOTTOM_FRONT_Z",
               "OBB_RIGHT_TOP_FRONT_X", "OBB_RIGHT_TOP_FRONT_Y", "OBB_RIGHT_TOP_FRONT_Z",
               "OBB_LEFT_TOP_FRONT_X", "OBB_LEFT_TOP_FRONT_Y", "OBB_LEFT_TOP_FRONT_Z",
               "POC_ID_LIST", "POC_POSITIONS_LIST", "POC_SIZES_LIST"
        FROM "TB_EQUIPMENTS"
        WHERE "OBB_LEFT_BOTTOM_BACK_X" IS NOT NULL
        {level_filter}
    '''
    cur.execute(eq_query)
    eq_rows = []
    for row in cur.fetchall():
        vertices = list(row[3:27])
        vertices = [tuple(vertices[i:i+3]) for i in range(0, 24, 3)]
        vertices = sort_box_vertices(vertices)
        pocs = []
        poc_ids, poc_pos, poc_sizes = row[27:30]
        if poc_pos:
            try:
                pos_list = json.loads(poc_pos)
                id_list = json.loads(poc_ids) if poc_ids else []
                size_list = json.loads(poc_sizes) if poc_sizes else []
                for i, pos_dict in enumerate(pos_list):
                    pid = pos_dict.get('id', '')
                    if not pid and i < len(id_list):
                        pid = id_list[i]
                    size_str = size_list[i] if i < len(size_list) else ''
                    radius = parse_size_to_radius(size_str)
                    pocs.append({
                        'x': pos_dict['x'], 'y': pos_dict['y'], 'z': pos_dict.get('z', 0.0),
                        'radius': radius, 'utility': utility_map.get(pid, 'DEFAULT')
                    })
            except:
                pass
        eq_rows.append({'name': row[0], 'vertices': vertices, 'pocs': pocs})
        
    print("Fetching Ducts...")
    ducts = fetch_category_data(cur, "TB_DUCT", utility_map, level_filter)
    
    print("Fetching Laterals...")
    laterals = fetch_category_data(cur, "TB_LATERAL_PIPE", utility_map, level_filter, limit=args.limit)
    
    conn.close()
    
    print(f"Loaded {len(eq_rows)} equipments, {len(ducts)} ducts, and {len(laterals)} laterals.")
    
    # 3. Plotly 피겨 생성
    print("Building 3D Scene in Plotly...")
    fig = go.Figure()
    
    # Box 면(Face) 템플릿 (12개 삼각면 정의)
    box_faces = [
        # Bottom face (looking from below, CW to face outward)
        [0, 3, 2], [0, 2, 1],
        # Top face (looking from above, CCW to face outward)
        [4, 5, 6], [4, 6, 7],
        # Side faces
        [0, 1, 5], [0, 5, 4], # Side 1 (0-1-5-4)
        [1, 2, 6], [1, 6, 5], # Side 2 (1-2-6-5)
        [2, 3, 7], [2, 7, 6], # Side 3 (2-3-7-6)
        [3, 0, 4], [3, 4, 7]  # Side 4 (3-0-4-7)
    ]
    
    all_pocs = []
    
    # 카테고리별 OBB 박스 병합 렌더링
    
    # 장비 병합 추가
    eq_names = [e['name'] for e in eq_rows]
    eq_vertices = [e['vertices'] for e in eq_rows]
    add_concatenated_mesh_trace(fig, eq_names, eq_vertices, box_faces, 'rgba(70, 130, 180, 0.4)', 0.4, 'Equipment (투명)')
    for e in eq_rows:
        all_pocs.extend(e['pocs'])
        
    # 덕트 병합 추가
    duct_names = [d['name'] for d in ducts]
    duct_vertices = [d['vertices'] for d in ducts]
    add_concatenated_mesh_trace(fig, duct_names, duct_vertices, box_faces, 'rgba(255, 140, 0, 0.4)', 0.4, 'Duct (투명)')
    for d in ducts:
        all_pocs.extend(d['pocs'])
        
    # 지표 배관 병합 추가
    lat_names = [l['name'] for l in laterals]
    lat_vertices = [l['vertices'] for l in laterals]
    add_concatenated_mesh_trace(fig, lat_names, lat_vertices, box_faces, 'rgba(147, 112, 219, 0.35)', 0.35, 'Lateral Pipe (투명)')
    for l in laterals:
        all_pocs.extend(l['pocs'])
        
    # 4. PoC Sphere 병합 렌더링
    print("Building PoC Spheres...")
    add_concatenated_poc_spheres(fig, all_pocs)
    
    # 레이아웃 설정 (배경색 어둡게 지정하여 몰입감 극대화)
    fig.update_layout(
        title="3D BIM Interactive Plan (Equipments, Ducts, Laterals & PoCs)",
        scene=dict(
            xaxis=dict(backgroundcolor="rgb(20, 20, 20)", gridcolor="gray", showbackground=True, zerolinecolor="white"),
            yaxis=dict(backgroundcolor="rgb(20, 20, 20)", gridcolor="gray", showbackground=True, zerolinecolor="white"),
            zaxis=dict(backgroundcolor="rgb(20, 20, 20)", gridcolor="gray", showbackground=True, zerolinecolor="white"),
            aspectmode='data'  # 1:1:1 실제 축척 비율 유지
        ),
        paper_bgcolor='rgb(15, 15, 15)',
        font_color='white',
        margin=dict(l=0, r=0, b=0, t=50)
    )
    
    # 5. HTML 파일 영구 저장 및 자동 로드
    out_dir = r"D:\DINNO\DEV\AI-AutoRouting\TopKGen\data\output"
    os.makedirs(out_dir, exist_ok=True)
    html_path = os.path.join(out_dir, "view_3d_plan.html")
    
    print(f"Saving interactive 3D plan to {html_path}...")
    fig.write_html(html_path, auto_open=True)
    print("3D View loaded in browser successfully.")

if __name__ == '__main__':
    main()
