# -*- coding: utf-8 -*-
"""
visualize_glb_obb.py
--------------------
설명: GLB 3D 모델과 추출된 Oriented Bounding Box(OBB)를 3차원으로 시각화하고
      대화식으로 조회할 수 있는 HTML 뷰어를 생성하는 도구입니다.
사용법:
    python Tools/visualize_glb_obb.py --input data/lattice.glb
    python Tools/visualize_glb_obb.py --input data/post.glb --output data/output/post_obb.html
"""

import os
import sys
import argparse
import webbrowser
import numpy as np
import plotly.graph_objects as go
import trimesh

# 모듈이 위치한 디렉토리를 path에 추가
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(SCRIPT_DIR)

from glb_obb_extractor import extract_mesh_obbs, extract_global_obb, save_obb_to_glb, OBBData

def get_color_palette(num_colors):
    """
    다양한 객체 구분을 위해 고대비 색상 팔레트를 반환합니다.
    """
    colors = [
        '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
        '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
        '#33a02c', '#fb9a99', '#e31a1c', '#fdbf6f', '#ff7f00',
        '#cab2d6', '#6a3d9a', '#ffff99', '#b15928', '#a6cee3'
    ]
    if num_colors <= len(colors):
        return colors[:num_colors]
    # 색상이 많이 필요할 경우 반복순환
    return [colors[i % len(colors)] for i in range(num_colors)]

def build_visualization(glb_path, obb_list, output_html, show_meshes=True):
    """
    GLB 메시와 OBB 리스트를 로드하여 Plotly 3D 그래프를 생성하고 HTML 파일로 저장합니다.
    """
    print(f"[+] 시각화를 위한 GLB 모델 로드 중: {glb_path}")
    scene = trimesh.load(glb_path)
    
    fig = go.Figure()
    
    # 1. 원본 GLB 메시 렌더링
    if show_meshes:
        if isinstance(scene, trimesh.Scene):
            meshes = scene.dump(concatenate=False)
        elif isinstance(scene, trimesh.Trimesh):
            meshes = [scene]
        else:
            meshes = []
            
        num_meshes = len(meshes)
        print(f"[+] 총 {num_meshes}개의 개별 메시를 발견했습니다.")
        
        # 메시가 너무 많을 경우(예: post.glb 1500+개) 성능 향상을 위해 
        # 개별 트레이스 대신 통합 메시로 결합하여 단일 트레이스로 렌더링합니다.
        if num_meshes > 50:
            print(f"[*] 메시 개수가 많아({num_meshes}개) 통합 렌더링 모드로 전환합니다 (속도 최적화).")
            # 월드 좌표가 반영되도록 scene.to_geometry() 사용
            if isinstance(scene, trimesh.Scene):
                combined_mesh = scene.to_geometry()
            else:
                combined_mesh = scene
                
            v = combined_mesh.vertices
            f = combined_mesh.faces
            if len(v) > 0 and len(f) > 0:
                fig.add_trace(go.Mesh3d(
                    x=v[:, 0], y=v[:, 1], z=v[:, 2],
                    i=f[:, 0], j=f[:, 1], k=f[:, 2],
                    opacity=0.5,
                    color='lightblue',
                    name='Combined GLB Mesh',
                    showlegend=True
                ))
        else:
            # 적은 개수일 때는 개별 메시의 색상을 다채롭게 표시
            palette = get_color_palette(num_meshes)
            for idx, mesh in enumerate(meshes):
                v = mesh.vertices
                f = mesh.faces
                if len(v) == 0 or len(f) == 0:
                    continue
                    
                mesh_name = getattr(mesh, 'metadata', {}).get('name', f"Mesh {idx}")
                if not mesh_name and hasattr(mesh, 'name'):
                    mesh_name = mesh.name
                    
                fig.add_trace(go.Mesh3d(
                    x=v[:, 0], y=v[:, 1], z=v[:, 2],
                    i=f[:, 0], j=f[:, 1], k=f[:, 2],
                    opacity=0.6,
                    color=palette[idx],
                    name=mesh_name,
                    hoverinfo='name',
                    showlegend=True
                ))
                
    # 2. OBB 렌더링 (병합하여 초고속 렌더링 구현)
    # OBB 와이어프레임(외곽선) 연결 인덱스 정의
    obb_edges = [
        (0, 1), (1, 3), (3, 2), (2, 0),  # 바닥면 루프
        (4, 5), (5, 7), (7, 6), (6, 4),  # 천장면 루프
        (0, 4), (1, 5), (3, 7), (2, 6)   # 수직 기둥들
    ]
    
    # OBB 면(Face) 구성을 위한 삼각분할 인덱스 정의 (8개의 꼭짓점 기준 12개 삼각형)
    box_i = [1, 4, 0, 2, 1, 5, 5, 3, 6, 2, 6, 7]
    box_j = [3, 1, 3, 4, 7, 1, 7, 7, 4, 7, 5, 5]
    box_k = [0, 0, 2, 0, 3, 4, 1, 2, 2, 6, 4, 6]
    
    # 모든 OBB 데이터를 하나의 트레이스로 병합하기 위한 좌표 컨테이너
    all_wire_x, all_wire_y, all_wire_z = [], [], []
    all_face_verts = []
    all_face_i, all_face_j, all_face_k = [], [], []
    
    for idx, obb in enumerate(obb_list):
        verts = np.array(obb.vertices)
        
        # A. 와이어프레임 라인 좌표 계산 (중간에 None을 삽입하여 하나의 연속된 선이 아닌 개별 선들로 그리도록 유도)
        for start, end in obb_edges:
            all_wire_x.extend([verts[start][0], verts[end][0], None])
            all_wire_y.extend([verts[start][1], verts[end][1], None])
            all_wire_z.extend([verts[start][2], verts[end][2], None])
            
        # B. 면(Mesh3d) 생성을 위한 꼭짓점 및 인덱스 누적 (8n 오프셋 적용)
        start_v_idx = len(all_face_verts)
        all_face_verts.extend(obb.vertices)
        
        for fi, fj, fk in zip(box_i, box_j, box_k):
            all_face_i.append(fi + start_v_idx)
            all_face_j.append(fj + start_v_idx)
            all_face_k.append(fk + start_v_idx)
            
    # OBB 외곽선(빨간색 실선) 추가
    if all_wire_x:
        fig.add_trace(go.Scatter3d(
            x=all_wire_x, y=all_wire_y, z=all_wire_z,
            mode='lines',
            line=dict(color='rgb(255, 0, 0)', width=3),
            name='OBB Wireframes (Red)',
            hoverinfo='none',
            showlegend=True
        ))
        
    # OBB 내부 반투명 박스면 추가
    if all_face_verts:
        face_verts_arr = np.array(all_face_verts)
        fig.add_trace(go.Mesh3d(
            x=face_verts_arr[:, 0], y=face_verts_arr[:, 1], z=face_verts_arr[:, 2],
            i=all_face_i, j=all_face_j, k=all_face_k,
            opacity=0.15,
            color='rgb(255, 60, 60)',
            name='OBB Volumes (Translucent)',
            hoverinfo='none',
            showlegend=True
        ))
        
    # 3. 레이아웃 템플릿 및 설정 적용
    fig.update_layout(
        title=dict(
            text=f"GLB Mesh & OBB Visualizer ({os.path.basename(glb_path)})",
            x=0.5,
            font=dict(size=20, color='white')
        ),
        template="plotly_dark",
        scene=dict(
            xaxis=dict(title='X (mm)', gridcolor='rgba(255,255,255,0.1)', showbackground=False),
            yaxis=dict(title='Y (mm)', gridcolor='rgba(255,255,255,0.1)', showbackground=False),
            zaxis=dict(title='Z (mm)', gridcolor='rgba(255,255,255,0.1)', showbackground=False),
            aspectmode='data'  # 정비율 왜곡 방지
        ),
        margin=dict(l=10, r=10, b=10, t=60),
        legend=dict(
            yanchor="top",
            y=0.99,
            xanchor="left",
            x=0.01,
            bgcolor="rgba(0,0,0,0.5)"
        )
    )
    
    # 출력 폴더 확인 및 생성
    output_dir = os.path.dirname(os.path.abspath(output_html))
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    fig.write_html(output_html)
    print(f"[+] 시각화 HTML 생성 완료: {output_html}")
    return output_html

def main():
    parser = argparse.ArgumentParser(description='GLB 파일에서 OBB를 추출하고 3D 시각화 HTML을 만드는 프로그램')
    parser.add_argument('--input', type=str, default='data/lattice.glb',
                        help='입력 GLB 파일 경로 (기본값: data/lattice.glb)')
    parser.add_argument('--output', type=str, default='data/output/view_glb_obb.html',
                        help='출력 HTML 파일 경로 (기본값: data/output/view_glb_obb.html)')
    parser.add_argument('--global-only', action='store_true',
                        help='개별 메쉬가 아닌 씬 전체를 감싸는 단일 Global OBB만 추출하여 시각화합니다.')
    parser.add_argument('--no-split', action='store_true',
                        help='메쉬를 개별 위상 구성요소로 분할하지 않고 원본 메쉬 자체의 OBB를 추출합니다.')
    parser.add_argument('--no-mesh', action='store_true',
                        help='시각화 시 원본 GLB 메시는 제외하고 OBB 박스만 그립니다.')
    parser.add_argument('--min-thickness', type=float, default=50.0,
                        help='평면 형태의 OBB가 생성되는 것을 방지하기 위한 최소 상자 두께(mm) (기본값: 50.0)')
                        
    args = parser.parse_args()
    
    if not os.path.exists(args.input):
        print(f"[-] 에러: 입력 파일을 찾을 수 없습니다: {args.input}")
        sys.exit(1)
        
    try:
        # 1. OBB 추출 진행
        if args.global_only:
            print("[*] 전체 모델을 감싸는 단일 Global OBB를 추출합니다...")
            global_obb = extract_global_obb(args.input, min_thickness=args.min_thickness)
            obb_list = [global_obb]
        else:
            print("[*] 개별 메쉬 단위로 Oriented Bounding Box(OBB)를 추출합니다...")
            obb_list = extract_mesh_obbs(args.input, split_components=not args.no_split, min_thickness=args.min_thickness)
            
        # OBB 결과를 별도 GLB 파일로 저장
        try:
            if args.global_only:
                input_dir, input_file = os.path.split(args.input)
                basename, _ = os.path.splitext(input_file)
                output_obb_glb = os.path.join(input_dir, f"{basename}_obb.glb")
                
                # Global OBB 상자 메쉬 생성 후 씬으로 빌드
                global_obb = obb_list[0]
                box_mesh = trimesh.creation.box(extents=global_obb.extents)
                box_mesh.apply_transform(global_obb.transform)
                obb_scene = trimesh.Scene([box_mesh])
                obb_scene.export(output_obb_glb)
                print(f"[+] Global OBB GLB 파일 저장 완료: {output_obb_glb}")
            else:
                output_obb_glb = save_obb_to_glb(args.input, split_components=not args.no_split, min_thickness=args.min_thickness)
                print(f"[+] OBB GLB 파일 저장 완료: {output_obb_glb}")
        except Exception as e:
            print(f"[-] OBB GLB 파일 저장 중 오류 발생: {e}")
            
        # 2. 3D 시각화 빌드 및 브라우저 열기
        html_path = build_visualization(
            glb_path=args.input,
            obb_list=obb_list,
            output_html=args.output,
            show_meshes=not args.no_mesh
        )
        
        # 브라우저 실행
        print("[+] 기본 웹 브라우저를 통해 시각화 결과를 실행합니다...")
        webbrowser.open(f"file:///{os.path.abspath(html_path)}")
        
    except Exception as e:
        print(f"[-] 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
