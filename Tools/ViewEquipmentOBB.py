"""
[실행 명령어]
터미널 또는 PowerShell에서 실행하여 실시간 DB 데이터를 기반으로 장비 OBB 형상과 PoC를 3D로 표출하고 시각 검증합니다.
> python ./Tools/ViewEquipmentOBB.py

[전체 흐름도]
1. DB 연결 및 노드 유틸리티 메타데이터 조회
   - psycopg2를 통해 PostgreSQL DB(DDW_AI_DB)에 접속하여 배관 노드별 유틸리티 캐시를 수집합니다.
2. 장비(Equipment) 원본 데이터 조회
   - TB_EQUIPMENTS 테이블에서 각 장비의 인스턴스명, 3D OBB 정점 8개 및 PoC 정보를 실시간으로 읽어옵니다.
3. 3D 형상 데이터 정규화 및 정밀 정렬 (sort_box_vertices)
   - 데이터베이스 컬럼의 인덱스 꼬임 문제를 수학적으로 자동 해결하는 정렬 알고리즘을 사용합니다.
   - 8개 꼭짓점의 Z 고도를 기반으로 바닥면과 천장면을 구별하고, 바닥면을 CCW로 정렬하여 꼬임 없는 직육면체를 구축합니다.
   - 각 장비의 실측 가로(X), 세로(Y), 높이(Z) 크기를 계산하여 3D 박스 호버 툴팁 정보로 표출합니다.
4. Plotly 3D 병합 Mesh3d 최적화 생성
   - 장비 박스들을 투명도(rgba)가 적용된 고속 병합 Mesh로 렌더링하고, PoC들은 유틸리티별 고유 RGB 색상의 3D Sphere 매쉬로 수학적 생성하여 병합 렌더링합니다.
5. HTML 로컬 저장 및 웹 브라우저 자동 기동
   - data/output/view_equipment_obb.html 파일로 영구 저장하고 브라우저로 60 FPS 회전, 줌인/아웃이 가능한 3D Scene을 자동 실행합니다.
"""

import os
import json
import argparse
import math
import psycopg2
import plotly.graph_objects as go
from tool_config import add_common_args, print_runtime, resolve_runtime

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
    특정 중심 좌표와 반지름을 가지는 경량화 3D Sphere 매쉬 기하를 수학적으로 계산하여 생성합니다.
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
    # 북극 캡
    for j in range(num_lon):
        n1 = 1 + j
        n2 = 1 + (j + 1) % num_lon
        faces.append([0, n1, n2])
        
    # 구체 바디
    for i in range(1, num_lat - 1):
        for j in range(num_lon):
            a = 1 + (i - 1) * num_lon + j
            b = 1 + (i - 1) * num_lon + (j + 1) % num_lon
            c = a + num_lon
            d = b + num_lon
            faces.append([a, c, b])
            faces.append([b, c, d])
            
    # 남극 캡
    last_idx = len(vertices) - 1
    for j in range(num_lon):
        n1 = last_idx - num_lon + j
        n2 = last_idx - num_lon + (j + 1) % num_lon
        faces.append([last_idx, n2, n1])
        
    return vertices, faces

def sort_box_vertices(vertices):
    """
    데이터베이스 컬럼 꼬임 문제를 원천 차단하기 위해 8개 정점을 
    Z 정렬 및 XY 중심 기준 반시계 방향(CCW)으로 규격화 정규 정렬합니다.
    """
    # 1. Z축 고도 기준 정렬하여 하단 4개와 상단 4개로 구분
    sorted_by_z = sorted(vertices, key=lambda p: p[2])
    bottom_4 = sorted_by_z[:4]
    top_4 = sorted_by_z[4:]
    
    # 2. 하단 4개 점을 XY 평면상에서 CCW 정렬 진행
    cx = sum(p[0] for p in bottom_4) / 4.0
    cy = sum(p[1] for p in bottom_4) / 4.0
    def angle(p):
        return math.atan2(p[1] - cy, p[0] - cx)
    bottom_sorted = sorted(bottom_4, key=angle)
    
    # 3. 각 하단 꼭짓점 바로 위의 일직선상에 연장되는 상단 꼭짓점을 1:1로 매칭
    top_sorted = []
    for b in bottom_sorted:
        best_t = min(top_4, key=lambda t: (t[0]-b[0])**2 + (t[1]-b[1])**2)
        top_sorted.append(best_t)
        
    return bottom_sorted + top_sorted

def fetch_equipments(cur, utility_map):
    """
    TB_EQUIPMENTS 테이블로부터 원본 데이터와 OBB 좌표 8개, PoC 정보를 실시간 조회하여 반환합니다.
    """
    query = '''
        SELECT "INSTANCE_NAME",
               "AABB_MINX", "AABB_MINY", "AABB_MINZ",
               "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ",
               "POC_ID_LIST", "POC_POSITIONS_LIST", "POC_SIZES_LIST"
        FROM "TB_EQUIPMENTS"
        WHERE "AABB_MINX" IS NOT NULL
    '''
    cur.execute(query)
    eq_rows = []
    for row in cur.fetchall():
        name = row[0]
        min_x, min_y, min_z, max_x, max_y, max_z = row[1:7]
        vertices = [
            (min_x, min_y, min_z),
            (max_x, min_y, min_z),
            (max_x, max_y, min_z),
            (min_x, max_y, min_z),
            (min_x, min_y, max_z),
            (max_x, min_y, max_z),
            (max_x, max_y, max_z),
            (min_x, max_y, max_z)
        ]
        
        # 3D 찌그러짐을 방지하는 정렬 진행
        sorted_verts = sort_box_vertices(vertices)
        
        # 실측 크기(가로, 세로, 높이) 계산
        x_size = math.sqrt((sorted_verts[1][0] - sorted_verts[0][0])**2 + (sorted_verts[1][1] - sorted_verts[0][1])**2 + (sorted_verts[1][2] - sorted_verts[0][2])**2)
        y_size = math.sqrt((sorted_verts[2][0] - sorted_verts[1][0])**2 + (sorted_verts[2][1] - sorted_verts[1][1])**2 + (sorted_verts[2][2] - sorted_verts[1][2])**2)
        z_size = math.sqrt((sorted_verts[4][0] - sorted_verts[0][0])**2 + (sorted_verts[4][1] - sorted_verts[0][1])**2 + (sorted_verts[4][2] - sorted_verts[0][2])**2)
        
        pocs = []
        poc_ids, poc_pos, poc_sizes = row[7:10]
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
                
        eq_rows.append({
            'name': name,
            'vertices': sorted_verts,
            'pocs': pocs,
            'x_size': x_size,
            'y_size': y_size,
            'z_size': z_size
        })
    return eq_rows

def add_concatenated_mesh_trace(fig, eq_rows, faces_template):
    """
    수백 개의 장비 OBB 박스들을 3D Plotly Mesh Trace로 병합하여 고속 렌더링을 구축합니다.
    """
    if not eq_rows:
        return
        
    x_all, y_all, z_all = [], [], []
    i_all, j_all, k_all = [], [], []
    text_all = []
    
    for idx, eq in enumerate(eq_rows):
        shift = idx * 8
        for v in eq['vertices']:
            x_all.append(v[0])
            y_all.append(v[1])
            z_all.append(v[2])
            text_all.append(
                f"<b>{eq['name']}</b><br>"
                f"가로(X): {eq['x_size']:.1f} mm<br>"
                f"세로(Y): {eq['y_size']:.1f} mm<br>"
                f"높이(Z): {eq['z_size']:.1f} mm"
            )
            
        for f in faces_template:
            i_all.append(f[0] + shift)
            j_all.append(f[1] + shift)
            k_all.append(f[2] + shift)
            
    fig.add_trace(go.Mesh3d(
        x=x_all, y=y_all, z=z_all,
        i=i_all, j=j_all, k=k_all,
        color='rgba(70, 130, 180, 0.45)',  # 수려한 반투명 스틸블루 색상
        opacity=0.45,
        name='Equipment (장비 OBB)',
        showlegend=True,
        text=text_all,
        hoverinfo='text'
    ))

def add_concatenated_poc_spheres(fig, poc_list):
    """
    PoC들을 유틸리티별 고유 불투명 색상의 go.Mesh3d 레이어로 병합하여 시각화 속도를 향상시킵니다.
    """
    if not poc_list:
        return
        
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
        
        for poc in pocs:
            v_sphere, f_sphere = generate_sphere_geometry(
                poc['x'], poc['y'], poc['z'], poc['radius'],
                num_lat=6, num_lon=8
            )
            
            for v in v_sphere:
                x_all.append(v[0])
                y_all.append(v[1])
                z_all.append(v[2])
                text_all.append(f"PoC [{poc['utility']}]<br>반지름: {poc['radius']:.1f} mm")
                
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
    parser = argparse.ArgumentParser(description="3D equipment OBB and PoC verification viewer.")
    parser.add_argument("--no-open", action="store_true", help="Save HTML without opening a browser")
    add_common_args(parser)
    args = parser.parse_args()
    runtime = resolve_runtime(args)
    print("데이터베이스 연결 시도 중...")
    try:
        conn = psycopg2.connect(runtime.conninfo)
    except Exception as e:
        print(f"DB Connection failed: {e}")
        return

    cur = conn.cursor()
    
    # 1. 노드 유틸리티 사전 구축
    print("노드 유틸리티 메타데이터 캐싱 중...")
    utility_map = {}
    cur.execute('SELECT "NODE_GUID", "ID", "UTILITY" FROM "TB_ROUTE_NODES"')
    for row in cur.fetchall():
        node_guid, node_id, util = row
        if util:
            if node_guid: utility_map[node_guid] = util
            if node_id: utility_map[node_id] = util
            
    # 2. 장비 및 PoC 데이터 일괄 로딩
    print("실시간 장비 형상(OBB) 및 연결포트(PoC) 데이터를 DB로부터 읽어오는 중...")
    eq_rows = fetch_equipments(cur, utility_map)
    cur.close()
    conn.close()
    
    print(f"총 {len(eq_rows)}개의 장비 객체를 성공적으로 수집했습니다.")
    
    # 3. 3D 씬 구성
    fig = go.Figure()
    
    # 표준화 정렬된 꼭짓점 기준 12개 삼각면 정의 (Watertight)
    box_faces = [
        [0, 3, 2], [0, 2, 1],  # 바닥면
        [4, 5, 6], [4, 6, 7],  # 천장면
        [0, 1, 5], [0, 5, 4],  # 전면
        [1, 2, 6], [1, 6, 5],  # 우측면
        [2, 3, 7], [2, 7, 6],  # 후면
        [3, 0, 4], [3, 4, 7]   # 좌측면
    ]
    
    all_pocs = []
    for eq in eq_rows:
        all_pocs.extend(eq['pocs'])
        
    print("Plotly 3D 기하 매쉬를 병합 렌더링하는 중...")
    add_concatenated_mesh_trace(fig, eq_rows, box_faces)
    add_concatenated_poc_spheres(fig, all_pocs)
    
    # 레이아웃 스타일 설정 (어두운 배경 테마로 몰입감 극대화)
    fig.update_layout(
        title="Equipment OBB & Connection Ports (PoCs) 3D Verification",
        scene=dict(
            xaxis=dict(backgroundcolor="rgb(20, 20, 20)", gridcolor="gray", showbackground=True, zerolinecolor="white"),
            yaxis=dict(backgroundcolor="rgb(20, 20, 20)", gridcolor="gray", showbackground=True, zerolinecolor="white"),
            zaxis=dict(backgroundcolor="rgb(20, 20, 20)", gridcolor="gray", showbackground=True, zerolinecolor="white"),
            aspectmode='data'
        ),
        paper_bgcolor='rgb(15, 15, 15)',
        font_color='white',
        margin=dict(l=0, r=0, b=0, t=50)
    )
    
    # 4. 파일 영구 저장 및 자동 실행
    out_dir = runtime.out_dir
    print_runtime(runtime)
    html_path = os.path.join(out_dir, "view_equipment_obb.html")
    
    print(f"시각화 결과물을 저장하는 중: {html_path}")
    fig.write_html(html_path, auto_open=not args.no_open)
    print("장비 OBB 및 PoC 검증용 3D 뷰어가 브라우저 상에 정상적으로 호출되었습니다.")

if __name__ == '__main__':
    main()
