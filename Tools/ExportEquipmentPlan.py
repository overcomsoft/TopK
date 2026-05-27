"""
[실행 명령어]
기본 실행:
> python ExportEquipmentPlan.py

특정 파이프라인의 일환으로 또는 단독으로 터미널/PowerShell에서 실행하여, 
PostgreSQL DB에 저장된 장비의 3D 꼭짓점 정보 및 배관 연결점(PoC) 정보를 바탕으로 
AutoCAD용 DXF 파일, Matplotlib 시각화 PNG 이미지, GIS 공간 분석용 Shapefile(.shp)을 
지정된 출력 폴더(data/output)에 한꺼번에 생성 및 저장합니다.

[전체 흐름도]
1. DB 연결 및 노드 유틸리티 메타데이터 로드
   - psycopg2를 이용해 PostgreSQL 서버(DDW_AI_DB)에 접속합니다.
   - TB_ROUTE_NODES 테이블을 조회하여 각 배관 노드(ID 및 GUID)가 어떤 유틸리티 종류(CDA, PCW, PV 등)에 속하는지 매핑 맵(utility_map)을 빌드합니다.
2. 장비(Equipment) 원본 데이터 조회
   - TB_EQUIPMENTS 테이블로부터 장비 인스턴스명, 3D OBB(Oriented Bounding Box)의 8개 꼭짓점 X,Y,Z 좌표, 그리고 PoC 정보 목록(ID 배열, 좌표 배열, 구경 크기 배열)을 조회합니다.
3. 데이터 가공 및 기하 연산
   - parse_size_to_radius(): 규격화되지 않은 배관 크기 문자열(예: '25B', '1 1/2', '3/4' 등)을 파싱하여 실제 mm 단위의 반지름(Radius) 실수값으로 정확히 변환합니다.
   - get_bottom_footprint(): 장비의 3D OBB 정점 8개 중 Z값이 가장 낮은 4개 정점을 찾아 물리적 '바닥면'으로 결정하고, XY 평면 중심을 기준으로 시계/반시계 정렬하여 닫힌 외곽 다각형(2D Footprint)을 만듭니다.
4. 다중 포맷 파일 저장 및 시각화 (출력 경로: TopKGen/data/output)
   - export_dxf(): ezdxf 라이브러리로 새 R2010 도면을 생성하고, 장비 외곽선 및 PoC 원(3D 고도 반영)을 레이어별로 자동 분할(유틸리티별 고유 색상 매핑)하여 저장합니다.
   - export_png(): Matplotlib을 이용해 장비 면적 다각형과 PoC 원을 2D상에 축척에 맞춰 플로팅하고 유틸리티 범례와 함께 고해상도 PNG 이미지로 출력합니다.
   - export_shp(): shapefile 라이브러리를 사용해 GIS 호환이 가능한 3D 입체 장비 다각형(POLYGONZ)과 PoC 점(POINTZ) 공간정보 파일(.shp, .dbf, .shx)을 각각 내보냅니다.
"""

import os
import json
import re
import argparse
import psycopg2
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Circle
import ezdxf
import matplotlib.patches as mpatches
import shapefile
import math

def parse_size_to_radius(size_str: str) -> float:
    """
    배관 구경 규격 문자열(인치, 분수 또는 B 접미사 형식)을 분석하여 
    실제 배관의 mm 단위 반지름(Radius) 실수값으로 환산하는 함수입니다.
    
    [인자 (Arguments)]
    - size_str (str): 배관 크기를 나타내는 원본 규격 문자열 (예: '2B', '1 1/2', '3/4', '50B' 등)
    
    [반환값 (Returns)]
    - float: mm 단위로 계산된 배관의 물리적 반지름 값 (파싱 에러 시 기본값 25.0 mm를 반환)
    
    [주요 변수 및 동작 개요]
    - whole, frac: 대분수(예: '1 1/2') 파싱 시 정수부와 분수부를 임시 분할 저장하는 변수
    - num, den: 분수(예: '3/4') 파싱 시 분자와 분모를 분할 저장하는 변수
    - val: 파싱 완료된 인치(Inch) 단위의 배관 지름 실수값
    - val * 25.4 / 2.0: 1인치 = 25.4mm를 적용하여 mm 지름으로 변환한 뒤, 2로 나누어 반지름을 산출합니다.
    """
    if not size_str:
        return 25.0
    # 영문 B 규격 접미사를 제거하고 대문자 공백 제거 정규화 진행
    size_str = size_str.upper().replace('B', '').strip()
    try:
        if ' ' in size_str:
            # 대분수 형태인 경우 (예: '1 1/2') 정수와 분수로 분할
            whole, frac = size_str.split(' ')
            num, den = frac.split('/')
            val = float(whole) + float(num) / float(den)
        elif '/' in size_str:
            # 단순 분수 형태인 경우 (예: '3/4') 분자, 분모 분할
            num, den = size_str.split('/')
            val = float(num) / float(den)
        else:
            # 단순 실수나 정수인 경우 (예: '50' 또는 '2')
            val = float(size_str)
        # 인치 -> mm 지름 -> mm 반지름으로 단위 환산 적용
        return val * 25.4 / 2.0  # Diameter to Radius in mm
    except:
        # 비정상 데이터 유입 시 기본 배관 반지름(25mm)으로 대체 안전 장치
        return 25.0

# AutoCAD DXF용 유틸리티 종류별 표준 ACI(AutoCAD Color Index) 색상 매핑 상수 테이블
# - PCW_S (공정 냉각수 공급): 5 (Blue)
# - PCW_R (공정 냉각수 회수): 150 (Light Blue)
# - EX (배기): 30 (Orange)
# - CDA (청정 압축 공기): 3 (Green)
# - PV (공정 진공): 1 (Red)
# - DEFAULT (기타 기본값): 7 (White/Black)
CAD_COLORS = {
    'PCW_S': 5,     # Blue
    'PCW_R': 150,   # Light Blue
    'EX': 30,       # Orange
    'CDA': 3,       # Green
    'PV': 1,        # Red
    'DEFAULT': 7    # White/Black
}

# Matplotlib 평면도 시각화용 유틸리티 종류별 HEX 색상 코드 매핑 상수 테이블
PLT_COLORS = {
    'PCW_S': '#0000FF',
    'PCW_R': '#ADD8E6',
    'EX': '#FFA500',
    'CDA': '#008000',
    'PV': '#FF0000',
    'DEFAULT': '#808080'
}

def get_color(utility: str, is_cad=False):
    """
    유틸리티 유형 문자열에 해당하는 정의된 색상 인덱스(CAD) 또는 색상 코드(Matplotlib)를 반환합니다.
    
    [인자 (Arguments)]
    - utility (str): 유틸리티 고유 코드명 (예: 'CDA', 'PCW_S', 'EX' 등)
    - is_cad (bool): True인 경우 AutoCAD ACI 정수값 반환, False인 경우 시각화용 HEX 코드 문자열 반환
    
    [반환값 (Returns)]
    - int 또는 str: 매핑 테이블에서 매치된 유틸리티별 고유 색상값 (미등록 코드 시 DEFAULT로 대체 처리)
    """
    key = utility.upper() if utility else 'DEFAULT'
    if is_cad:
        return CAD_COLORS.get(key, CAD_COLORS['DEFAULT'])
    return PLT_COLORS.get(key, PLT_COLORS['DEFAULT'])

def get_bottom_footprint(obb_3d):
    """
    장비의 3D 공간 상 8개 정점 좌표(OBB)를 분석하여 물리적인 바닥면(Bottom Face)을 검출하고, 
    XY 평면상에서 닫힌 정밀 다각형을 그리기 쉽도록 중심점 대비 방향성 각도 기준 정렬을 수행합니다.
    
    [인자 (Arguments)]
    - obb_3d (dict): 장비의 OBB 3D 8개 모퉁이 점 좌표 정보 딕셔너리
      * lbb/rbb/rtb/ltb: Bottom에 해당하는 정점들 (Local 기준)
      * lbf/rbf/rtf/ltf: Top에 해당하는 정점들 (Local 기준)
      
    [반환값 (Returns)]
    - list of tuple (float, float, float): 정렬된 순서의 3D 바닥면 꼭짓점 좌표 리스트 (4개 점)
    
    [주요 변수 및 기하 동작]
    - faces: 육면체의 6개 면을 구성하는 정점 묶음 리스트
    - best_face: 각 면의 정점들의 Z값 평균을 내어, 가장 바닥에 평평하게 닿아있는 면(최저 평균 Z값 면)을 바닥면으로 낙점
    - cx, cy: 바닥면 4개 정점의 XY 투영 중심점 좌표 (각도 정렬을 위한 기준 원점)
    - angle(p): math.atan2 함수를 활용해 중심점(cx, cy) 대비 꼭짓점의 극좌표 각도를 반환하는 내부 헬퍼 함수
    """
    faces = [
        [obb_3d['lbb'], obb_3d['rbb'], obb_3d['rbf'], obb_3d['lbf']], # 바닥면 영역 (Local Bottom)
        [obb_3d['ltb'], obb_3d['rtb'], obb_3d['rtf'], obb_3d['ltf']], # 천장면 영역 (Local Top)
        [obb_3d['lbb'], obb_3d['rbb'], obb_3d['rtb'], obb_3d['ltb']], # 배면 영역 (Local Back)
        [obb_3d['lbf'], obb_3d['rbf'], obb_3d['rtf'], obb_3d['ltf']], # 정면 영역 (Local Front)
        [obb_3d['lbb'], obb_3d['ltb'], obb_3d['ltf'], obb_3d['lbf']], # 좌측면 영역 (Local Left)
        [obb_3d['rbb'], obb_3d['rtb'], obb_3d['rtf'], obb_3d['rbf']]  # 우측면 영역 (Local Right)
    ]
    # Z 좌표가 가장 낮은 정점들의 조합을 지닌 물리적 바닥 다각형 선택
    best_face = min(faces, key=lambda f: sum(p[2] for p in f) / 4.0)
    
    # 2D(XY 평면) 상에서 선분이 꼬이지 않도록 4개 점을 중심 기준으로 극좌표 각도 정렬 진행
    cx = sum(p[0] for p in best_face) / 4.0
    cy = sum(p[1] for p in best_face) / 4.0
    def angle(p):
        return math.atan2(p[1] - cy, p[0] - cx)
    return sorted(best_face, key=angle)

def fetch_data(conn):
    """
    PostgreSQL 데이터베이스에 질의하여 배관 노드들의 유틸리티 속성 사전 및 
    장비와 해당 장비에 장착된 PoC 정보를 일괄 조회하여 구조화된 리스트로 변환합니다.
    
    [인자 (Arguments)]
    - conn: psycopg2를 이용해 정상적으로 오픈된 PostgreSQL 데이터베이스 커넥션 객체
    
    [반환값 (Returns)]
    - list of dict: 각 장비의 상세 기하 정보와 가공 처리된 PoC 데이터 구조체가 포함된 리스트
      * 'name': 장비 고유명
      * 'poly': XY 평면에 투영되어 정렬된 2D 바닥면 폴리곤 꼭짓점 좌표 리스트
      * 'obb_3d': 장비 OBB 꼭짓점 8개
      * 'bottom_face_3d': 3D 상에 존재하는 바닥면 4개 정점
      * 'pocs': 장비에 연결된 PoC(접속 포인트) 배열 (각 PoC는 x, y, z, radius, utility 속성 소유)
      
    [주요 변수 및 흐름]
    - utility_map (dict): TB_ROUTE_NODES 테이블을 통해 노드의 고유 키(ID, GUID)별 유틸리티 종류('CDA', 'EX' 등)를 캐싱한 매핑 사전
    - eqs (list): 최종적으로 구성되어 반환되는 전체 장비 기하 정보 목록
    - id_list, pos_list, size_list: TB_EQUIPMENTS에 들어있는 JSON 문자열 배열 형태의 PoC ID 목록, 좌표 목록, 규격 목록
    - parse_size_to_radius(): PoC의 규격 배열 정보로부터 기하학적인 원 크기(반지름)를 실 mm 단위로 계산해 pocs 리스트에 적재
    """
    # 1. TB_ROUTE_NODES 테이블로부터 노드들의 유틸리티 종류 메타데이터 캐싱
    utility_map = {}
    with conn.cursor() as cur:
        cur.execute('SELECT "NODE_GUID", "ID", "UTILITY" FROM "TB_ROUTE_NODES"')
        for row in cur.fetchall():
            node_guid, node_id, util = row
            if util:
                if node_guid: utility_map[node_guid] = util
                if node_id: utility_map[node_id] = util
    
    # 2. TB_EQUIPMENTS 테이블로부터 장비와 OBB 8점 좌표, PoC 목록 정보 일괄 조회
    eqs = []
    with conn.cursor() as cur:
        query = '''
            SELECT "INSTANCE_NAME",
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
        '''
        cur.execute(query)
        for row in cur.fetchall():
            (name,
             lbb_x, lbb_y, lbb_z,
             rbb_x, rbb_y, rbb_z,
             rtb_x, rtb_y, rtb_z,
             ltb_x, ltb_y, ltb_z,
             lbf_x, lbf_y, lbf_z,
             rbf_x, rbf_y, rbf_z,
             rtf_x, rtf_y, rtf_z,
             ltf_x, ltf_y, ltf_z,
             poc_ids, poc_pos, poc_sizes) = row
             
            # 3D 상에서 육면체 OBB를 구성하기 위한 8개의 로컬 좌표 꼭짓점 세팅
            obb_3d = {
                'lbb': (lbb_x, lbb_y, lbb_z),
                'rbb': (rbb_x, rbb_y, rbb_z),
                'rtb': (rtb_x, rtb_y, rtb_z),
                'ltb': (ltb_x, ltb_y, ltb_z),
                'lbf': (lbf_x, lbf_y, lbf_z),
                'rbf': (rbf_x, rbf_y, rbf_z),
                'rtf': (rtf_x, rtf_y, rtf_z),
                'ltf': (ltf_x, ltf_y, ltf_z)
            }
            
            # 장비의 3D 바닥면 다각형 정점 계산 및 2D XY 평면 투영(폴리곤 정보 추출)
            bottom_face = get_bottom_footprint(obb_3d)
            poly = [(p[0], p[1]) for p in bottom_face]
            
            # PoC 리스트 파싱 및 유틸리티 속성 결합
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
                        utility = utility_map.get(pid, 'DEFAULT')
                        
                        pocs.append({
                            'x': pos_dict['x'],
                            'y': pos_dict['y'],
                            'z': pos_dict.get('z', 0.0),
                            'radius': radius,
                            'utility': utility
                        })
                except Exception as e:
                    print(f"Warning: JSON parsing error for equipment {name}: {e}")
                    
            eqs.append({
                'name': name,
                'poly': poly,
                'obb_3d': obb_3d,
                'bottom_face_3d': bottom_face,
                'pocs': pocs
            })
            
    return eqs

def export_dxf(eqs, out_path):
    """
    장비 바닥 외곽선과 PoC들을 도면 요소로 변환하여 AutoCAD용 DXF CAD 파일로 내보냅니다.
    
    [인자 (Arguments)]
    - eqs (list of dict): fetch_data()에서 정밀 파싱 완료되어 넘어온 장비 데이터 목록
    - out_path (str): 출력되어 저장될 최종 DXF 파일의 절대 경로
    
    [주요 기하 드로잉 동작]
    - ezdxf.new('R2010'): AutoCAD 호환을 위해 R2010 DXF 규격의 새 문서를 생성합니다.
    - msp.add_lwpolyline(): 정렬 완료된 2D 바닥 외곽선을 'EQUIPMENT' 레이어에 흰색 닫힌(close=True) 선으로 그립니다.
    - msp.add_circle(): PoC 연결점의 X, Y 및 Z 높이 고도를 그대로 유지한 상태에서 
      해당 PoC의 실제 반지름을 바탕으로 유틸리티별 고유 레이어(POC_PCW_S, POC_CDA 등)에 3D 원으로 기록합니다.
    """
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    
    # 1. 장비 레이어 및 유틸리티 종류별 CAD 전용 레이어 생성
    doc.layers.add('EQUIPMENT', color=ezdxf.colors.WHITE)
    for u in CAD_COLORS.keys():
        doc.layers.add(f'POC_{u}', color=CAD_COLORS[u])

    for eq in eqs:
        # 장비 바닥면 영역 2D 다각형 폴리라인 추가
        msp.add_lwpolyline(eq['poly'], close=True, dxfattribs={'layer': 'EQUIPMENT'})
        
        # PoC 원형 요소 생성 및 삽입
        for poc in eq['pocs']:
            layer_name = f"POC_{poc['utility']}" if poc['utility'] in CAD_COLORS else "POC_DEFAULT"
            if poc['utility'] not in CAD_COLORS:
                if layer_name not in doc.layers:
                    doc.layers.add(layer_name, color=CAD_COLORS['DEFAULT'])
            
            # PoC는 XY 평면에 평행한 상태로 설정된 3D 절대 고도(Z)에 원으로 추가됩니다.
            z_coord = poc.get('z', 0.0)
            center_3d = (poc['x'], poc['y'], z_coord)
            
            circle = msp.add_circle(
                center_3d, 
                poc['radius'],
                dxfattribs={'layer': layer_name}
            )
            # 기본 압출 벡터(0,0,1)를 통하여 XY 평면에 나란한 상태로 3D Z축 번역 유지
            
    doc.saveas(out_path)
    print(f"DXF saved to {out_path}")

def export_png(eqs, out_path):
    """
    Matplotlib 플로팅 라이브러리를 이용하여 전체 장비 영역과 PoC(연결구)의 레이아웃을 
    유틸리티 범례와 함께 2D 평면 도면 이미지(PNG)로 가시화하여 저장합니다.
    
    [인자 (Arguments)]
    - eqs (list of dict): 전체 장비 및 PoC의 기하 정보 목록
    - out_path (str): 저장할 시각화 PNG 이미지 파일의 절대 경로
    
    [주요 변수 및 흐름]
    - fig, ax: 15인치 X 15인치 크기의 고해상도(dpi=300) 도면 판넬을 구성
    - Polygon: 회색 채움(#E0E0E0) 및 검은 외곽선을 가지는 장비 2D 풋프린트 다각형 패치 객체
    - Circle: PoC 위치를 지름 비율에 맞추되 시각적 인지가 가능하도록 최소 크기(50mm)를 보정한 유틸리티 색상의 원형 패치 객체
    - drawn_utilities: 범례에 표시하기 위해 실제로 화면상에 묘사된 유틸리티들의 이름을 누적하는 집합(Set)
    - legend_patches: 범례 박스를 위해 생성된 Matplotlib Patch 리스트
    """
    fig, ax = plt.subplots(figsize=(15, 15))
    ax.set_aspect('equal') # X축과 Y축의 물리적 축척 비율을 1:1로 강제 고정
    
    drawn_utilities = set()
    
    for eq in eqs:
        # 장비 바닥 다각형 Matplotlib 패치 인스턴스 생성 및 배치
        poly = Polygon(eq['poly'], closed=True, facecolor='#E0E0E0', edgecolor='black', alpha=0.5, zorder=1)
        ax.add_patch(poly)
        
        # PoC 원 패치 인스턴스 생성 및 배치
        for poc in eq['pocs']:
            color = get_color(poc['utility'], is_cad=False)
            drawn_utilities.add(poc['utility'])
            
            # PoC 구경 크기가 너무 미세할 경우 시각적으로 누락되는 문제를 방지하기 위해 최소 50mm 반지름 지정
            circ = Circle((poc['x'], poc['y']), max(poc['radius'], 50), facecolor=color, edgecolor='black', zorder=2)
            ax.add_patch(circ)
            
    # 전체 좌표 범위를 바탕으로 도면 영역의 축 범위를 자동 설정
    ax.autoscale_view()
    
    # 3. 우측 상단 유틸리티 종류 범례(Legend) 표시 작업
    legend_patches = []
    for u in sorted(drawn_utilities):
        c = get_color(u, is_cad=False)
        legend_patches.append(mpatches.Patch(color=c, label=f'PoC: {u}'))
    if legend_patches:
        ax.legend(handles=legend_patches, loc='upper right')
        
    plt.title('Equipment Floor Plan & PoCs')
    plt.xlabel('X (mm)')
    plt.ylabel('Y (mm)')
    plt.grid(True, linestyle='--', alpha=0.6)
    
    plt.savefig(out_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"PNG saved to {out_path}")

def export_shp(eqs, out_dir):
    """
    GIS 공간정보 시스템 및 외부 3D 프로그램과의 원활한 벡터 연동을 위하여 
    pyshp 라이브러리를 사용해 장비 영역(POLYGONZ) 및 PoC(POINTZ) Shapefile을 빌드합니다.
    
    [인자 (Arguments)]
    - eqs (list of dict): 기하 데이터 정보 목록
    - out_dir (str): GIS 쉐이프 파일 세트가 내보내질 최상위 디렉토리
    
    [공간 정보 쓰기 로직 흐름]
    1. 장비 Shapefile 쓰기 (POLYGONZ - Z 좌표를 가지는 3D 다각형 타입):
       - 'NAME' 문자열 속성 필드를 정의합니다.
       - 정렬된 3D 바닥 정점 4개를 바탕으로 첫 정점과 끝 정점을 닫아서 루프(part)를 만들고 기록합니다.
       - 장비명(INSTANCE_NAME)을 Attribute Record에 연동합니다.
    2. PoC Shapefile 쓰기 (POINTZ - Z 좌표를 가지는 3D 점 타입):
       - 'EQ_NAME'(장비명), 'UTILITY'(유틸리티 종류), 'RADIUS'(PoC 반지름) 3개의 속성 필드를 구성합니다.
       - PoC의 X, Y, Z 절대 좌표를 점(PointZ) 요소로 추가하고, 각 PoC의 속성을 레코드 테이블에 대입합니다.
    """
    eq_shp_path = os.path.join(out_dir, "equipments")
    poc_shp_path = os.path.join(out_dir, "pocs")
    
    # 1. 장비 3D 바닥 다각형 정보 내보내기 (POLYGONZ)
    with shapefile.Writer(eq_shp_path, shapeType=shapefile.POLYGONZ) as w:
        w.field("NAME", "C", "50")
        for eq in eqs:
            bf = eq['bottom_face_3d']
            # GIS Polygon 규격에 맞춰 처음 정점 좌표를 끝에 다시 추가하여 명확히 루프 폐쇄
            part = [
                [bf[0][0], bf[0][1], bf[0][2]],
                [bf[1][0], bf[1][1], bf[1][2]],
                [bf[2][0], bf[2][1], bf[2][2]],
                [bf[3][0], bf[3][1], bf[3][2]],
                [bf[0][0], bf[0][1], bf[0][2]] # close
            ]
            w.polyz([part])
            w.record(eq['name'])
            
    print(f"Equipment Shapefile saved to {eq_shp_path}.shp")
    
    # 2. PoC 3D 위치 포인트 정보 내보내기 (POINTZ)
    with shapefile.Writer(poc_shp_path, shapeType=shapefile.POINTZ) as w:
        w.field("EQ_NAME", "C", "50")
        w.field("UTILITY", "C", "50")
        w.field("RADIUS", "N", decimal=2)
        
        for eq in eqs:
            for poc in eq['pocs']:
                w.pointz(poc['x'], poc['y'], poc.get('z', 0.0))
                w.record(eq['name'], poc['utility'], poc['radius'])
                
    print(f"PoC Shapefile saved to {poc_shp_path}.shp")

def main():
    """
    데이터 로드, 데이터 가공 및 파일 포맷별 출력 파이프라인 프로세스 전체를 
    총괄하여 구동하는 어플리케이션 메인 프로그램 함수입니다.
    
    [주요 흐름]
    - 로컬 PostgreSQL 호스트 설정 및 데이터베이스(DDW_AI_DB) 커넥션 취득 시도
    - fetch_data() 실행: 원본 장비 기하와 PoC 관계 맵 일괄 가공 적재
    - 출력 저장 대상 경로(TopKGen/data/output) 폴더 자동 생성 검증
    - export_dxf(), export_png(), export_shp() 순차 실행 및 저장 완료
    """
    conn_str = "host=localhost port=5432 dbname=DDW_AI_DB user=postgres password=dinno"
    print("Connecting to database...")
    try:
        conn = psycopg2.connect(conn_str)
    except Exception as e:
        print(f"DB Connection failed: {e}")
        return

    print("Fetching data...")
    eqs = fetch_data(conn)
    conn.close()
    
    print(f"Loaded {len(eqs)} equipments.")
    
    # 최종 결과물 내보내기 폴더 경로 정의 및 누락 방지 자동 생성
    out_dir = r"D:\DINNO\DEV\AI-AutoRouting\TopKGen\data\output"
    os.makedirs(out_dir, exist_ok=True)
    
    dxf_path = os.path.join(out_dir, "equipments_plan.dxf")
    png_path = os.path.join(out_dir, "equipments_plan.png")
    
    # 3대 주요 포맷(DXF, PNG, GIS Shapefile) 일괄 출력 구동
    export_dxf(eqs, dxf_path)
    export_png(eqs, png_path)
    export_shp(eqs, out_dir)

if __name__ == '__main__':
    main()
