from __future__ import annotations
import os
import sys
import json
import math
import argparse
import glob
import webbrowser
from pathlib import Path
from typing import Any
import plotly.graph_objects as go

"""
================================================================================
[실행 명령어 예시 (Execution Commands)]
================================================================================
1. 대화형 모드로 실행 (JSON 목록을 보여주고 선택하여 뷰잉):
   python Tools/ViewSceneData3D.py

2. 특정 프로젝트 JSON 파일의 경로를 명시하여 실행:
   python Tools/ViewSceneData3D.py --json data/output/SceneData/WTNHJ02.json

3. 뷰어 HTML 자동 열기 실행 방지:
   python Tools/ViewSceneData3D.py --json data/output/SceneData/WTNHJ02.json --no-open

4. 특정 출력 HTML 경로 지정하여 뷰어 저장:
   python Tools/ViewSceneData3D.py --json data/output/SceneData/WTNHJ02.json --out-html ./data/output/custom_viewer.html
================================================================================

[전체 프로세스 및 개요 (Overall Process)]
1. 본 스크립트는 `ExportProjectSceneData.py`에 의해 데이터베이스로부터 추출된 
   BIM 공간 씬(Scene) JSON 데이터를 로드하여, Plotly 3D 공간 상에 대화형 입체 기하로 변환하여 렌더링합니다.
   
2. 로드 및 시각화 처리 흐름:
   - JSON 선택: 명령행에서 `--json` 경로를 주지 않은 경우, `data/output/SceneData/` 디렉토리를 탐색하여 
     추출된 파일 목록을 제시하고, 대화형 번호 입력을 받아 파일을 선택적으로 오픈합니다.
     (비대화형 셸이나 시간 내 응답이 없을 시 첫 번째 파일을 자동으로 로드합니다.)
   - 요소별 복원 및 기하 Mesh 생성:
     (1) Grid: 가상 공간 복셀의 전체 도메인을 경계 와이어프레임(Wireframe) 상자로 렌더링.
     (2) Obstacles (장애물): JSON 내의 장애물을 반투명한 회색 메쉬(Mesh3d)로 병합 드로우하여 드로우콜 최적화.
     (3) Equipment (장비): 장비(메인/서브)를 반투명한 로열블루 계열의 메쉬로 병합 렌더링.
     (4) Ducts/Laterals (덕트/레터럴): 카테고리별로 덕트는 반투명 주황색, 레터럴은 보라색 3D 박스로 병합하여 렌더링.
     (5) ExistingPipes (기존설계배관): 3D 공간상의 복잡한 다중 포인트들을 유틸리티별 고유한 색상(Line)으로 
         Plotly의 단일 레이어(None으로 구분)로 합쳐 초고속 렌더링.
     (6) Tasks (시/종 PoC): 자동 설계 요구의 시작점(Start PoC - 빨강 구체)과 끝점(End PoC - 파랑 구체)을 시각화하고, 
         이를 흐리게 이어주는 가상의 설계 링크(Dashed Line) 표시.
     (7) Fittings (자재 피팅): 파이프 사이의 조인트 부속 피팅들을 작은 노란색 구체로 표시.
   - HTML 로컬 파일 빌드 및 브라우저 기동:
     최종 결합된 Plotly 3D Scene 피겨를 `data/output/view_scene_data_3d.html` 로 저장하고 웹 브라우저를 띄워 인터랙션 지원.
"""

# 유틸리티 그룹별 배관 경로 라인 색상 정의
UTILITY_LINE_COLORS = {
    'WATER': 'rgb(0, 191, 255)',      # Deep Sky Blue
    'PCW': 'rgb(30, 144, 255)',        # Dodger Blue
    'EXHAUST': 'rgb(255, 127, 80)',    # Coral Orange
    'EX': 'rgb(255, 69, 0)',           # Orange-Red
    'GAS': 'rgb(255, 215, 0)',         # Gold Yellow
    'CHEMICAL': 'rgb(218, 112, 214)',  # Orchid Magenta
    'DEFAULT': 'rgb(50, 205, 50)'      # Lime Green
}

def generate_sphere_geometry(cx, cy, cz, r, num_lat=5, num_lon=6):
    """
    지정된 구(Sphere) 중심 좌표와 반지름을 기반으로 고속 렌더링용 경량화 구체의 정점 및 삼각면 리스트를 계산합니다.
    - cx, cy, cz: 구체의 중심 좌표
    - r: 구체의 반지름 (mm)
    - num_lat, num_lon: 위도 및 경도 분할 수 (성능을 위해 5, 6 등 저해상도 설정 기본)
    """
    vertices = []
    # 북극점 추가
    vertices.append((cx, cy, cz + r))
    
    # 중간 경도/위도 정점들 수학적 계산
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
    # 북극 캡 조인트 삼각면 생성
    for j in range(num_lon):
        n1 = 1 + j
        n2 = 1 + (j + 1) % num_lon
        faces.append([0, n1, n2])
        
    # 구체 본체 사각형 영역을 삼각형 쌍으로 분할 생성
    for i in range(1, num_lat - 1):
        for j in range(num_lon):
            a = 1 + (i - 1) * num_lon + j
            b = 1 + (i - 1) * num_lon + (j + 1) % num_lon
            c = a + num_lon
            d = b + num_lon
            faces.append([a, c, b])
            faces.append([b, c, d])
            
    # 남극 캡 조인트 삼각면 생성
    last_idx = len(vertices) - 1
    for j in range(num_lon):
        n1 = last_idx - num_lon + j
        n2 = last_idx - num_lon + (j + 1) % num_lon
        faces.append([last_idx, n2, n1])
        
    return vertices, faces

def add_box_mesh_trace(fig, items, color, opacity, trace_name):
    """
    복수의 Bounding Box (AABB) 데이터를 단일 Plotly go.Mesh3d 객체로 인덱스 시프트를 진행하며 고속 병합 렌더링합니다.
    - items: 'MinX', 'MinY', 'MinZ', 'MaxX', 'MaxY', 'MaxZ' 및 'Name' 키를 가진 박스 객체들의 목록
    """
    if not items:
        return
        
    x_all, y_all, z_all = [], [], []
    i_all, j_all, k_all = [], [], []
    text_all = []
    
    # go.Mesh3d로 정육면체를 12개 삼각형 면으로 렌더링하기 위한 기준 인덱스 맵
    box_faces_template = [
        [0, 3, 2], [0, 2, 1], # 바닥면
        [4, 5, 6], [4, 6, 7], # 천장면
        [0, 1, 5], [0, 5, 4], # 옆면 1
        [1, 2, 6], [1, 6, 5], # 옆면 2
        [2, 3, 7], [2, 7, 6], # 옆면 3
        [3, 0, 4], [3, 4, 7]  # 옆면 4
    ]
    
    for idx, item in enumerate(items):
        min_x, min_y, min_z = float(item["MinX"]), float(item["MinY"]), float(item["MinZ"])
        max_x, max_y, max_z = float(item["MaxX"]), float(item["MaxY"]), float(item["MaxZ"])
        name = item.get("Name") or trace_name
        
        # 8개 꼭짓점 정의
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
        
        shift = idx * 8
        for v in vertices:
            x_all.append(v[0])
            y_all.append(v[1])
            z_all.append(v[2])
            text_all.append(name)
            
        for f in box_faces_template:
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

def add_spheres_mesh_trace(fig, spheres_data, color, trace_name, size_div=1.0):
    """
    여러 개의 구체(Sphere)들을 단일 go.Mesh3d 객체로 병합하여 로딩 성능을 최적화합니다.
    - spheres_data: {'X':x, 'Y':y, 'Z':z, 'Radius':r, 'Label':text} 리스트
    """
    if not spheres_data:
        return
        
    x_all, y_all, z_all = [], [], []
    i_all, j_all, k_all = [], [], []
    text_all = []
    vertex_offset = 0
    
    num_lat, num_lon = 5, 6
    
    for sphere in spheres_data:
        cx, cy, cz = float(sphere['X']), float(sphere['Y']), float(sphere['Z'])
        r = float(sphere['Radius']) * size_div
        label = sphere.get('Label') or trace_name
        
        v_sphere, f_sphere = generate_sphere_geometry(cx, cy, cz, r, num_lat, num_lon)
        
        for v in v_sphere:
            x_all.append(v[0])
            y_all.append(v[1])
            z_all.append(v[2])
            text_all.append(f"{label}<br>Coord: ({cx:.1f}, {cy:.1f}, {cz:.1f})")
            
        for f in f_sphere:
            i_all.append(f[0] + vertex_offset)
            j_all.append(f[1] + vertex_offset)
            k_all.append(f[2] + vertex_offset)
            
        vertex_offset += len(v_sphere)
        
    fig.add_trace(go.Mesh3d(
        x=x_all, y=y_all, z=z_all,
        i=i_all, j=j_all, k=k_all,
        color=color,
        opacity=1.0,
        name=trace_name,
        showlegend=True,
        text=text_all,
        hoverinfo='text'
    ))

def add_wireframe_box(fig, min_x, min_y, min_z, max_x, max_y, max_z, color, width, trace_name):
    """
    가상 씬 영역을 식별하기 위한 정육면체 와이어프레임(외곽선) 라인을 추가합니다.
    """
    x = [min_x, max_x, max_x, min_x, min_x, min_x, max_x, max_x, min_x, min_x, min_x, min_x, max_x, max_x, max_x, max_x]
    y = [min_y, min_y, max_y, max_y, min_y, min_y, min_y, max_y, max_y, min_y, max_y, max_y, max_y, min_y, min_y, max_y]
    z = [min_z, min_z, min_z, min_z, min_z, max_z, max_z, max_z, max_z, max_z, max_z, min_z, min_z, min_z, max_z, max_z]
    
    fig.add_trace(go.Scatter3d(
        x=x, y=y, z=z,
        mode='lines',
        line=dict(color=color, width=width),
        name=trace_name,
        hoverinfo='skip'
    ))

def main():
    # 콘솔 출력 유니코드 설정
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding='utf-8')

    parser = argparse.ArgumentParser(description="Visualize project scene JSON data in Plotly 3D viewer.")
    parser.add_argument("--json", default=None, help="Path to exported project scene JSON file")
    parser.add_argument("--no-open", action="store_true", help="Do not open browser automatically")
    parser.add_argument("--out-html", default=None, help="Output HTML file path")
    args = parser.parse_args()

    selected_json = args.json

    # 1. 파일이 CLI 인자로 안 들어왔다면 디렉토리를 탐색해 사용자 입력을 유도
    if not selected_json:
        target_pattern = "data/output/SceneData/*.json"
        json_files = glob.glob(target_pattern)
        
        if not json_files:
            print("[오류] 'data/output/SceneData/' 내에 생성된 JSON 파일이 존재하지 않습니다.")
            print("먼저 'python Tools/ExportProjectSceneData.py'를 수행하세요.")
            # 직접 수동 경로 입력 대기
            user_input = input("불러올 JSON 파일의 경로를 입력하세요: ").strip()
            if not user_input or not os.path.exists(user_input):
                print("[에러] 유효하지 않은 파일 경로입니다. 종료합니다.")
                return 1
            selected_json = user_input
        else:
            print("\n=== 프로젝트 JSON 파일 목록 ===")
            for idx, f in enumerate(json_files):
                print(f" [{idx + 1}] {os.path.basename(f)}")
            print(" [99] 직접 다른 파일 경로 입력...")
            
            # 비대화형 환경 방지 및 입력 획득
            choice = "1"
            if sys.stdin.isatty():
                try:
                    choice = input("\n선택할 파일의 번호를 입력하세요 [기본값: 1]: ").strip()
                except Exception:
                    pass
            else:
                print("비대화형 셸 감지: 1번 파일을 자동 선택합니다.")

            if not choice:
                choice = "1"
                
            if choice == "99":
                user_input = input("불러올 JSON 파일의 경로를 입력하세요: ").strip()
                if not user_input or not os.path.exists(user_input):
                    print("[에러] 유효하지 않은 파일 경로입니다. 종료합니다.")
                    return 1
                selected_json = user_input
            else:
                try:
                    choice_idx = int(choice) - 1
                    if 0 <= choice_idx < len(json_files):
                        selected_json = json_files[choice_idx]
                    else:
                        print("[경고] 범위를 벗어난 번호입니다. 1번 파일을 선택합니다.")
                        selected_json = json_files[0]
                except ValueError:
                    print("[경고] 숫자 형식이 아닙니다. 1번 파일을 선택합니다.")
                    selected_json = json_files[0]

    # 2. 파일 로딩
    print(f"\n[로딩] JSON 파일을 불러오는 중: {selected_json}")
    try:
        with open(selected_json, "r", encoding="utf-8") as f:
            scene_data = json.load(f)
    except Exception as e:
        print(f"[에러] JSON 파일 로드 실패: {e}")
        return 1

    # 데이터 추출 진행
    grid_meta = scene_data.get("Grid", {})
    obstacles_list = scene_data.get("Obstacles", [])
    equipment_list = scene_data.get("Equipment", [])
    ducts_laterals_list = scene_data.get("DuctsLaterals", [])
    spaces_list = scene_data.get("Spaces", [])
    existing_pipes_list = scene_data.get("ExistingPipes", [])
    fittings_list = scene_data.get("Fittings", [])
    tasks_list = scene_data.get("Tasks", [])
    source_file = scene_data.get("SourceFile", "Unknown Project")

    print(f"프로젝트명: {source_file}")
    print(f"장애물 수: {len(obstacles_list)} | 장비 수: {len(equipment_list)} | 덕트/레터럴 수: {len(ducts_laterals_list)} | 공간 수: {len(spaces_list)}")
    print(f"기존 배관수: {len(existing_pipes_list)} | 라우팅 작업수: {len(tasks_list)} | 피팅 수: {len(fittings_list)}")

    # 3. Plotly 3D 공간 구성 시작
    print("3D 기하 매쉬 생성 및 시각화 빌드 중...")
    fig = go.Figure()

    # 1) Grid 경계 상자 와이어프레임 추가
    if grid_meta:
        ox, oy, oz = float(grid_meta["Ox"]), float(grid_meta["Oy"]), float(grid_meta["Oz"])
        cell_mm = float(grid_meta["CellMm"])
        nx, ny, nz = int(grid_meta["Nx"]), int(grid_meta["Ny"]), int(grid_meta["Nz"])
        
        grid_max_x = ox + nx * cell_mm
        grid_max_y = oy + ny * cell_mm
        grid_max_z = oz + nz * cell_mm
        
        # 전체 그리드 도메인 범위 경계선 표시
        add_wireframe_box(fig, ox, oy, oz, grid_max_x, grid_max_y, grid_max_z, 
                          color='rgba(255, 255, 255, 0.25)', width=2.5, trace_name='Grid Bounds')

    # 2) 장애물 시각화 (반투명 회색 박스 - 시인성을 위해 불투명도 및 밝기 상향)
    add_box_mesh_trace(fig, obstacles_list, color='rgba(170, 170, 170, 0.35)', opacity=0.35, trace_name='BIM Obstacle')

    # 3) 장비 시각화 (반투명 스틸 블루)
    add_box_mesh_trace(fig, equipment_list, color='rgba(70, 130, 180, 0.45)', opacity=0.45, trace_name='Equipment')

    # 4) 공간 영역 시각화 (반투명 밝은 초록 와이어 박스로 외곽만 표현)
    for space in spaces_list:
        add_wireframe_box(fig, float(space["MinX"]), float(space["MinY"]), float(space["MinZ"]),
                          float(space["MaxX"]), float(space["MaxY"]), float(space["MaxZ"]),
                          color='rgba(152, 251, 152, 0.3)', width=1.5, trace_name=f'Space: {space.get("Name")}')

    # 5) 덕트 및 레터럴 분할 시각화
    ducts = [dl for dl in ducts_laterals_list if dl.get("Category") == "DUCT"]
    laterals = [dl for dl in ducts_laterals_list if dl.get("Category") == "LATERAL"]
    # 덕트는 주황색, 레터럴은 보라색
    add_box_mesh_trace(fig, ducts, color='rgba(255, 140, 0, 0.45)', opacity=0.45, trace_name='Duct')
    add_box_mesh_trace(fig, laterals, color='rgba(147, 112, 219, 0.45)', opacity=0.45, trace_name='Lateral')

    # 6) 피팅(Fittings) 자재들을 크기를 1/2로 낮추고 회색 구체로 렌더링
    fitting_spheres = []
    for f in fittings_list:
        fitting_spheres.append({
            'X': f['X'], 'Y': f['Y'], 'Z': f['Z'],
            'Radius': f.get('DiameterMm', 50.0) or 50.0,
            'Label': f"Fitting ({f.get('Type')})<br>Size: {f.get('Size')}"
        })
    add_spheres_mesh_trace(fig, fitting_spheres, color='rgb(120, 120, 120)', trace_name='Fitting Component', size_div=0.5)

    # 7) 라우팅 태스크 시점 및 종점(PoC) 렌더링
    poc_start_spheres = []
    poc_end_spheres = []
    for idx, t in enumerate(tasks_list):
        # 시작 PoC (빨강)
        poc_start_spheres.append({
            'X': t['Sx'], 'Y': t['Sy'], 'Z': t['Sz'],
            'Radius': (t.get('DiameterMm', 100.0) or 100.0) / 2.0,
            'Label': f"Start PoC ({t.get('PocName') or '?'})<br>Guid: {t.get('RoutePathGuid')}"
        })
        # 종점 PoC (파랑)
        poc_end_spheres.append({
            'X': t['Gx'], 'Y': t['Gy'], 'Z': t['Gz'],
            'Radius': (t.get('DiameterMm', 100.0) or 100.0) / 2.0,
            'Label': f"End PoC ({t.get('EndName') or '?'})<br>Guid: {t.get('RoutePathGuid')}"
        })
        # 시점과 종점을 잇는 흐릿한 작업 가상 대시 라인 추가
        fig.add_trace(go.Scatter3d(
            x=[t['Sx'], t['Gx']], y=[t['Sy'], t['Gy']], z=[t['Sz'], t['Gz']],
            mode='lines',
            line=dict(color='rgba(255, 255, 255, 0.25)', width=2, dash='dash'),
            name=f"Task Link {idx + 1}",
            showlegend=False,
            hoverinfo='skip'
        ))

    add_spheres_mesh_trace(fig, poc_start_spheres, color='rgb(255, 69, 0)', trace_name='Start PoC (Source)')
    add_spheres_mesh_trace(fig, poc_end_spheres, color='rgb(30, 144, 255)', trace_name='End PoC (Target)')

    # 8) 기존설계 배관 경로(ExistingPipes) 고속 선형 시각화
    # 성능 극대화를 위해 유틸리티 그룹별로 배관들을 분리하여 'None' 구분선으로 다중 배관들을 병합 렌더링
    utility_pipes_map = {}
    for pipe in existing_pipes_list:
        points = pipe.get("Points", [])
        if len(points) < 2:
            continue
        ut_group = (pipe.get("Group") or "DEFAULT").upper()
        if ut_group not in utility_pipes_map:
            utility_pipes_map[ut_group] = []
        utility_pipes_map[ut_group].append(pipe)

    for ut_group, pipes in utility_pipes_map.items():
        x_pipe, y_pipe, z_pipe = [], [], []
        text_pipe = []
        
        color = UTILITY_LINE_COLORS.get(ut_group, UTILITY_LINE_COLORS['DEFAULT'])
        
        for pipe in pipes:
            points = pipe["Points"]
            label = pipe.get("Label") or ut_group
            guid = pipe.get("RoutePathGuid") or ""
            dia = pipe.get("DiameterMm", 0.0)
            
            for pt in points:
                x_pipe.append(pt["X"])
                y_pipe.append(pt["Y"])
                z_pipe.append(pt["Z"])
                text_pipe.append(f"{label}<br>GUID: {guid}<br>Diameter: {dia:.1f} mm")
                
            # Plotly에서 별도 트레이스를 추가하지 않고 라인을 끊어서 표현하기 위한 'None' 주입
            x_pipe.append(None)
            y_pipe.append(None)
            z_pipe.append(None)
            text_pipe.append(None)
            
        fig.add_trace(go.Scatter3d(
            x=x_pipe, y=y_pipe, z=z_pipe,
            mode='lines',
            line=dict(color=color, width=4.0),
            name=f"Pipe: {ut_group}",
            text=text_pipe,
            hoverinfo='text'
        ))

    # 4. 레이아웃 뷰 최적화 (어두운 배경색 적용)
    fig.update_layout(
        title=f"3D Scene Data Interactive Viewer - {source_file}",
        scene=dict(
            xaxis=dict(backgroundcolor="rgb(20, 20, 20)", gridcolor="gray", showbackground=True, zerolinecolor="white"),
            yaxis=dict(backgroundcolor="rgb(20, 20, 20)", gridcolor="gray", showbackground=True, zerolinecolor="white"),
            zaxis=dict(backgroundcolor="rgb(20, 20, 20)", gridcolor="gray", showbackground=True, zerolinecolor="white"),
            aspectmode='data'  # 축 간 1:1:1 실제 비율 고정
        ),
        paper_bgcolor='rgb(15, 15, 15)',
        font_color='white',
        margin=dict(l=0, r=0, b=0, t=50)
    )

    # 5. HTML 파일 저장 및 자동 열기 실행
    if args.out_html:
        html_path = Path(args.out_html).resolve()
    else:
        html_path = Path("data/output/view_scene_data_3d.html").resolve()
        
    html_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"\n[저장] 대화형 3D Scene 파일 작성 중: {html_path}")
    fig.write_html(str(html_path), auto_open=not args.no_open)
    print("3D Scene 뷰어가 웹 브라우저에서 성공적으로 로드되었습니다.")

    return 0

if __name__ == "__main__":
    sys.exit(main())
