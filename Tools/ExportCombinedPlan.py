"""
[실행 명령어]
터미널 또는 PowerShell에서 프로젝트 루트 디렉토리(d:\\DINNO\\DEV\\AI-AutoRouting\\TopKGen) 기준 아래 명령어를 실행합니다:
> python Tools/ExportCombinedPlan.py

[전체 프로세스 개요]
1. DB 연결 및 노드 메타데이터 캐싱:
   - psycopg2 라이브러리를 사용하여 PostgreSQL DB(DDW_AI_DB)에 접속합니다.
   - TB_ROUTE_NODES 테이블을 조회하여 각 배관 노드의 고유 ID 및 GUID가 어떤 유틸리티 종류(CDA, PCW_S, EX, PV 등)에 속하는지 매핑 맵(utility_map)을 빌드합니다.
2. 설비/덕트/분기배관 데이터 통합 로딩 및 기하 연산:
   - TB_EQUIPMENTS(장비), TB_DUCT(덕트), TB_LATERAL_PIPE(분기배관) 세 테이블로부터 실시간 데이터를 순차적으로 로드합니다.
   - 각 개체의 24개 OBB 정점 좌표 컬럼(OBB_LEFT_BOTTOM_BACK_X 등)을 파싱하여 3D 정점 구조(obb_3d)를 복원합니다.
   - get_bottom_footprint(): OBB 8개 꼭짓점 중 Z 좌표(고도)가 가장 낮은 4개 점을 바닥면으로 결정하고, XY 투영 중심을 기준으로 CCW(반시계 방향) 정렬하여 선이 꼬이지 않는 닫힌 바닥 다각형을 구성합니다.
   - parse_pocs(): 각 개체에 연결된 PoC(연결 포트) 위치 정보를 파싱합니다. DB 구조에 따라 딕셔너리 형태 혹은 실수 좌표 배열 형태(2D 리스트) 모두 대응할 수 있도록 이중 안전 파싱을 수행하고 유틸리티명을 바인딩합니다.
3. 다차원 도면 및 공간 정보 포맷 일괄 내보내기:
   - export_combined_dxf(): ezdxf 라이브러리를 통해 3차원 DXF 도면을 생성합니다. 장비/덕트/배관의 바닥 3D 폴리라인을 ezdxf.add_polyline3d()로 그리고, PoC는 실제 3차원 위치에 add_circle()로 작도합니다. 항목과 유틸리티에 따라 레이어를 엄격하게 분기하며 특수 기호는 sanitize_layer_name()으로 자동 필터링됩니다.
   - export_combined_shp(): shapefile 라이브러리를 사용하여 GIS 프로그램과 100% 호환되는 3차원 면(POLYGONZ) 파일(combined_elements)과 3차원 점(POINTZ) 파일(combined_pocs)을 출력합니다.
   - export_combined_png(): Matplotlib을 이용하여 전체 설비와 PoC들의 상대적 레이아웃을 1:1 축척에 맞게 컬러풀하게 드로잉하여 이미지로 저장합니다.
"""

import os
import json
import re
import argparse
import psycopg2
import matplotlib
# 백그라운드 무인 실행 및 CLI 환경에서의 원활한 실행을 위해 비대화형 Agg 백엔드 적용
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Circle
import ezdxf
import matplotlib.patches as mpatches
import copy
import matplotlib.path
import shapefile
import math
from tool_config import add_common_args, print_runtime, resolve_runtime

# [핵심 패치] Matplotlib Path deepcopy patch for Python 3.14 compatibility
# Python 3.14 이상 환경에서 matplotlib 내부적으로 super() 객체를 딥카피할 때 
# 발생할 수 있는 RecursionError(무한 재귀 에러)를 방지하기 위해 Path의 딥카피 동작을 재정의합니다.
def _patched_path_deepcopy(self, memo=None):
    if memo is None:
        memo = {}
    new_p = matplotlib.path.Path(
        copy.deepcopy(self.vertices, memo),
        copy.deepcopy(self.codes, memo),
        self._interpolation_steps,
        readonly=False
    )
    memo[id(self)] = new_p
    return new_p

# matplotlib 모듈의 기존 __deepcopy__ 메서드를 우리가 정의한 안전한 패치 함수로 덮어씁니다 (Monkey Patching)
matplotlib.path.Path.__deepcopy__ = _patched_path_deepcopy

# AutoCAD DXF 저장용 유틸리티별 표준 ACI(AutoCAD Color Index) 색상 매핑 사전
CAD_COLORS = {
    'PCW_S': 5,     # Blue (공정 냉각수 공급)
    'PCW_R': 150,   # Light Blue (공정 냉각수 회수)
    'EX': 30,       # Orange (배기)
    'CDA': 3,       # Green (청정 압축 공기)
    'PV': 1,        # Red (공정 진공)
    'DEFAULT': 7    # White/Black (기본값)
}

# Matplotlib 평면 시각화 도면용 유틸리티 HEX 색상 코드 매핑 사전
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
    유틸리티 고유 코드명을 인자로 받아 사전 정의된 CAD 인덱스 색상 또는 HEX 컬러 코드를 반환합니다.
    - utility: 'CDA', 'PCW_S' 등의 유틸리티 명칭 문자열
    - is_cad: True면 정수형 CAD ACI 코드 반환, False면 HEX 색상 문자열 반환
    """
    key = utility.upper() if utility else 'DEFAULT'
    if is_cad:
        return CAD_COLORS.get(key, CAD_COLORS['DEFAULT'])
    return PLT_COLORS.get(key, PLT_COLORS['DEFAULT'])

def sanitize_layer_name(name: str) -> str:
    r"""
    AutoCAD DXF에서 허용되지 않는 레이어 특수문자들(/, \, <, >, ?, *, |, ", :, ;, =, 쉼표, 공백 등)을
    언더바(_) 문자로 치환하여 도면 생성 시의 DXFValueError를 원천 예방하는 함수입니다.
    - name: 원본 레이어 이름 문자열
    """
    return re.sub(r'[\\/<>?*|:";=, ]', '_', name)

def parse_size_to_radius(size_str: str) -> float:
    """
    다양한 배관/덕트 구경 문자열(인치, 분수, mm, 사각 덕트 가로X세로 표기 등)을 파싱하여
    실제 기하 도형을 그릴 때 사용할 mm 단위의 반지름(Radius) 실수값으로 환산합니다.
    
    [주요 파싱 로직]
    1. 사각 덕트 규격 ('X' 또는 '*' 포함, 예: '600x400'):
       - 폭(w)과 높이(h)를 각각 추출하여 수리학적 등가 반경 개념인 (w + h) / 4.0 공식을 통해 반지름을 산출합니다.
    2. 분수식 및 대분수 표기 (공백 및 '/' 포함, 예: '1 1/2'):
       - 정수부와 분수부를 나누어 합산하고 인치 단위를 산출합니다.
    3. 일반 정수/실수 표기 (예: '50A' 또는 '2'):
       - 접미사 A를 필터링하고 숫자로 변환합니다.
    4. 인치 -> mm 단위 변환:
       - 파싱된 직경 값이 36 미만이면 인치(Inch) 단위로 간주하여 25.4를 곱해 mm로 변환한 후 2로 나누어 반지름을 구합니다.
       - 36 이상이면 이미 mm 단위로 간주하고 단순히 2로 나누어 반지름을 구합니다.
    """
    if not size_str:
        return 25.0
    size_str = size_str.upper().replace('B', '').strip()
    try:
        # 사각 덕트 분기 처리
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
            
        # 대분수 형태 파싱 (예: '1 1/2')
        if ' ' in size_str:
            whole, frac = size_str.split(' ')
            num, den = frac.split('/')
            val = float(whole) + float(num) / float(den)
        # 단순 분수 형태 파싱 (예: '3/4')
        elif '/' in size_str:
            num, den = size_str.split('/')
            val = float(num) / float(den)
        # 일반 수치형 표기 (예: '25A' -> '25')
        else:
            size_str = size_str.replace('A', '').strip()
            val = float(size_str)
            
        # 인치 및 mm 분기 기준에 따른 변환
        if val < 36.0:
            return val * 25.4 / 2.0  # 인치를 mm 직경으로 변환 후 나누기 2 -> 반지름
        else:
            return val / 2.0  # mm 직경 나누기 2 -> 반지름
    except:
        # 예외 발생 시 기본 25mm 반지름 반환하여 스크립트 정지 방지
        return 25.0

def sort_box_vertices(vertices):
    """
    육면체 OBB 정점 8개를 고도(Z) 정렬 및 XY 극좌표 각도를 기준으로 정렬하여
    일정한 순서(인덱스 0~3은 바닥면 반시계 방향, 4~7은 그 바로 위 천장면)로 구조화합니다.
    실측 가로, 세로, 높이 크기를 수학적으로 정밀하게 재기 위해 사용됩니다.
    - vertices: 8개의 3D 좌표 점 리스트
    """
    sorted_by_z = sorted(vertices, key=lambda p: p[2])
    bottom_4 = sorted_by_z[:4]
    top_4 = sorted_by_z[4:]
    cx = sum(p[0] for p in bottom_4) / 4.0
    cy = sum(p[1] for p in bottom_4) / 4.0
    def angle(p):
        return math.atan2(p[1] - cy, p[0] - cx)
    bottom_sorted = sorted(bottom_4, key=angle)
    top_sorted = []
    for b in bottom_sorted:
        best_t = min(top_4, key=lambda t: (t[0]-b[0])**2 + (t[1]-b[1])**2)
        top_sorted.append(best_t)
    return bottom_sorted + top_sorted

def get_bottom_footprint(obb_3d):
    """
    3차원 상의 OBB 모퉁이 8개 점 좌표 중 고도가 가장 낮은 4개 정점을 선별해
    XY 평면 중심점에 대해 극좌표 각도 정렬을 거쳐 꼬이지 않는 '바닥면 다각형' 3D 좌표 세트를 추출합니다.
    - obb_3d: 8개 꼭짓점 키(lbb, rbb 등)와 3D 좌표를 쌍으로 가진 딕셔너리
    """
    vertices = list(obb_3d.values())
    # Z 좌표 기준 정렬 후 하단 4개 추출
    bottom_vertices = sorted(vertices, key=lambda p: p[2])[:4]
    
    # 2D 상에서 선분이 꼬이지 않도록 극좌표 각도 기준으로 정렬
    cx = sum(p[0] for p in bottom_vertices) / 4.0
    cy = sum(p[1] for p in bottom_vertices) / 4.0
    def angle(p):
        return math.atan2(p[1] - cy, p[0] - cx)
    return sorted(bottom_vertices, key=angle)

def fetch_utility_map(conn):
    """
    TB_ROUTE_NODES 테이블을 조회하여 각 배관/덕트 노드의 고유 키별 유틸리티 종류를 수집 및 반환합니다.
    - conn: psycopg2 데이터베이스 커넥션 객체
    """
    utility_map = {}
    with conn.cursor() as cur:
        cur.execute('SELECT "NODE_GUID", "ID", "UTILITY" FROM "TB_ROUTE_NODES"')
        for row in cur.fetchall():
            node_guid, node_id, util = row
            if util:
                if node_guid: utility_map[node_guid] = util
                if node_id: utility_map[node_id] = util
    return utility_map

def parse_pocs(poc_pos, poc_ids, poc_sizes, utility_map, utility_col=None):
    """
    개체에 포함된 PoC 목록 정보들을 구조화된 딕셔너리 배열로 파싱 및 변환합니다.
    
    [이중 호환 데이터 파싱]
    - DB 스키마 및 적재 버전에 따라 poc_pos 내부 요소가 사전형(x, y, z 키 내포) 또는 
      배열형([x, y, z] 실수값 목록)인 경우를 모두 감지하여 오류 없이 좌표(x_val, y_val, z_val)를 수집합니다.
    - utility_map을 기반으로 각 PoC의 소속 유틸리티명을 찾아내며, 검색 실패 시 대표 유틸리티명(utility_col)을 기본값으로 사용합니다.
    """
    pocs = []
    if poc_pos:
        try:
            pos_list = json.loads(poc_pos)
            id_list = json.loads(poc_ids) if poc_ids else []
            size_list = json.loads(poc_sizes) if poc_sizes else []
            
            for i, item in enumerate(pos_list):
                pid = ''
                x_val, y_val, z_val = 0.0, 0.0, 0.0
                
                # 사전형 구조 파싱인 경우
                if isinstance(item, dict):
                    pid = item.get('id', '')
                    x_val = item.get('x', 0.0)
                    y_val = item.get('y', 0.0)
                    z_val = item.get('z', 0.0)
                # 배열형 구조 파싱인 경우 (3차원 정밀 좌표 리스트)
                elif isinstance(item, (list, tuple)):
                    x_val = item[0] if len(item) > 0 else 0.0
                    y_val = item[1] if len(item) > 1 else 0.0
                    z_val = item[2] if len(item) > 2 else 0.0
                    
                if not pid and i < len(id_list):
                    pid = id_list[i]
                    
                size_str = size_list[i] if i < len(size_list) else ''
                radius = parse_size_to_radius(size_str)
                utility = utility_map.get(pid)
                if not utility:
                    utility = utility_col if utility_col else 'DEFAULT'
                
                pocs.append({
                    'x': x_val,
                    'y': y_val,
                    'z': z_val,
                    'radius': radius,
                    'utility': utility
                })
        except Exception as e:
            print(f"Warning: JSON parsing error for PoC: {e}")
    return pocs

def fetch_equipments(conn, utility_map):
    """
    TB_EQUIPMENTS 테이블로부터 장비 원본 정보 및 3D OBB 좌표, PoC 연결 정보를 쿼리하여
    정규화 가공이 완료된 장비 데이터 리스트를 구성합니다.
    """
    eqs = []
    with conn.cursor() as cur:
        query = '''
            SELECT "INSTANCE_NAME", "LEVEL", "BAY", "BOP",
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
            (name, level, bay, bop,
             obb_lbb_x, obb_lbb_y, obb_lbb_z,
             obb_rbb_x, obb_rbb_y, obb_rbb_z,
             obb_rtb_x, obb_rtb_y, obb_rtb_z,
             obb_ltb_x, obb_ltb_y, obb_ltb_z,
             obb_lbf_x, obb_lbf_y, obb_lbf_z,
             obb_rbf_x, obb_rbf_y, obb_rbf_z,
             obb_rtf_x, obb_rtf_y, obb_rtf_z,
             obb_ltf_x, obb_ltf_y, obb_ltf_z,
             poc_ids, poc_pos, poc_sizes) = row
             
            # 수집된 OBB 꼭짓점 정보 묶기
            obb_3d = {
                'lbb': (obb_lbb_x, obb_lbb_y, obb_lbb_z),
                'rbb': (obb_rbb_x, obb_rbb_y, obb_rbb_z),
                'rtb': (obb_rtb_x, obb_rtb_y, obb_rtb_z),
                'ltb': (obb_ltb_x, obb_ltb_y, obb_ltb_z),
                'lbf': (obb_lbf_x, obb_lbf_y, obb_lbf_z),
                'rbf': (obb_rbf_x, obb_rbf_y, obb_rbf_z),
                'rtf': (obb_rtf_x, obb_rtf_y, obb_rtf_z),
                'ltf': (obb_ltf_x, obb_ltf_y, obb_ltf_z)
            }
            
            bottom_face = get_bottom_footprint(obb_3d)
            poly = [(p[0], p[1]) for p in bottom_face]
            pocs = parse_pocs(poc_pos, poc_ids, poc_sizes, utility_map)
            
            # 실측 기하 치수(가로/세로/높이) 측정
            sorted_verts = sort_box_vertices(list(obb_3d.values()))
            x_size = math.sqrt((sorted_verts[1][0] - sorted_verts[0][0])**2 + (sorted_verts[1][1] - sorted_verts[0][1])**2 + (sorted_verts[1][2] - sorted_verts[0][2])**2)
            y_size = math.sqrt((sorted_verts[2][0] - sorted_verts[1][0])**2 + (sorted_verts[2][1] - sorted_verts[1][1])**2 + (sorted_verts[2][2] - sorted_verts[1][2])**2)
            z_size = math.sqrt((sorted_verts[4][0] - sorted_verts[0][0])**2 + (sorted_verts[4][1] - sorted_verts[0][1])**2 + (sorted_verts[4][2] - sorted_verts[0][2])**2)

            eqs.append({
                'name': name,
                'category': 'EQUIPMENT',
                'utility': 'DEFAULT',
                'level': level if level else 'N/A',
                'bay': bay if bay else 'N/A',
                'bop': bop if bop is not None else 0.0,
                'poly': poly,
                'obb_3d': obb_3d,
                'bottom_face_3d': bottom_face,
                'pocs': pocs,
                'x_size': x_size,
                'y_size': y_size,
                'z_size': z_size
            })
    return eqs

def fetch_ducts(conn, utility_map):
    """
    TB_DUCT 테이블로부터 덕트 원본 기하 정보와 OBB 및 PoC 위치를 조회하여 가공합니다.
    """
    ducts = []
    with conn.cursor() as cur:
        query = '''
            SELECT "INSTANCE_NAME", "UTILITY", "LATERAL_NUMBER", "UTILITY_GROUP", "LEVEL", "BAY", "BOP",
                   "OBB_LEFT_BOTTOM_BACK_X", "OBB_LEFT_BOTTOM_BACK_Y", "OBB_LEFT_BOTTOM_BACK_Z",
                   "OBB_RIGHT_BOTTOM_BACK_X", "OBB_RIGHT_BOTTOM_BACK_Y", "OBB_RIGHT_BOTTOM_BACK_Z",
                   "OBB_RIGHT_TOP_BACK_X", "OBB_RIGHT_TOP_BACK_Y", "OBB_RIGHT_TOP_BACK_Z",
                   "OBB_LEFT_TOP_BACK_X", "OBB_LEFT_TOP_BACK_Y", "OBB_LEFT_TOP_BACK_Z",
                   "OBB_LEFT_BOTTOM_FRONT_X", "OBB_LEFT_BOTTOM_FRONT_Y", "OBB_LEFT_BOTTOM_FRONT_Z",
                   "OBB_RIGHT_BOTTOM_FRONT_X", "OBB_RIGHT_BOTTOM_FRONT_Y", "OBB_RIGHT_BOTTOM_FRONT_Z",
                   "OBB_RIGHT_TOP_FRONT_X", "OBB_RIGHT_TOP_FRONT_Y", "OBB_RIGHT_TOP_FRONT_Z",
                   "OBB_LEFT_TOP_FRONT_X", "OBB_LEFT_TOP_FRONT_Y", "OBB_LEFT_TOP_FRONT_Z",
                   "POC_ID_LIST", "POC_POSITIONS_LIST", "POC_SIZES_LIST"
            FROM "TB_DUCT"
            WHERE "OBB_LEFT_BOTTOM_BACK_X" IS NOT NULL
        '''
        cur.execute(query)
        for row in cur.fetchall():
            (name, utility_col, lateral_number, utility_group, level, bay, bop,
             obb_lbb_x, obb_lbb_y, obb_lbb_z,
             obb_rbb_x, obb_rbb_y, obb_rbb_z,
             obb_rtb_x, obb_rtb_y, obb_rtb_z,
             obb_ltb_x, obb_ltb_y, obb_ltb_z,
             obb_lbf_x, obb_lbf_y, obb_lbf_z,
             obb_rbf_x, obb_rbf_y, obb_rbf_z,
             obb_rtf_x, obb_rtf_y, obb_rtf_z,
             obb_ltf_x, obb_ltf_y, obb_ltf_z,
             poc_ids, poc_pos, poc_sizes) = row
             
            obb_3d = {
                'lbb': (obb_lbb_x, obb_lbb_y, obb_lbb_z),
                'rbb': (obb_rbb_x, obb_rbb_y, obb_rbb_z),
                'rtb': (obb_rtb_x, obb_rtb_y, obb_rtb_z),
                'ltb': (obb_ltb_x, obb_ltb_y, obb_ltb_z),
                'lbf': (obb_lbf_x, obb_lbf_y, obb_lbf_z),
                'rbf': (obb_rbf_x, obb_rbf_y, obb_rbf_z),
                'rtf': (obb_rtf_x, obb_rtf_y, obb_rtf_z),
                'ltf': (obb_ltf_x, obb_ltf_y, obb_ltf_z)
            }
            
            bottom_face = get_bottom_footprint(obb_3d)
            poly = [(p[0], p[1]) for p in bottom_face]
            pocs = parse_pocs(poc_pos, poc_ids, poc_sizes, utility_map, utility_col)
            
            sorted_verts = sort_box_vertices(list(obb_3d.values()))
            x_size = math.sqrt((sorted_verts[1][0] - sorted_verts[0][0])**2 + (sorted_verts[1][1] - sorted_verts[0][1])**2 + (sorted_verts[1][2] - sorted_verts[0][2])**2)
            y_size = math.sqrt((sorted_verts[2][0] - sorted_verts[1][0])**2 + (sorted_verts[2][1] - sorted_verts[1][1])**2 + (sorted_verts[2][2] - sorted_verts[1][2])**2)
            z_size = math.sqrt((sorted_verts[4][0] - sorted_verts[0][0])**2 + (sorted_verts[4][1] - sorted_verts[0][1])**2 + (sorted_verts[4][2] - sorted_verts[0][2])**2)

            ducts.append({
                'name': name,
                'category': 'DUCT',
                'utility': utility_col if utility_col else 'DEFAULT',
                'lateral_number': lateral_number if lateral_number else 'N/A',
                'utility_group': utility_group if utility_group else 'N/A',
                'level': level if level else 'N/A',
                'bay': bay if bay else 'N/A',
                'bop': bop if bop is not None else 0.0,
                'poly': poly,
                'obb_3d': obb_3d,
                'bottom_face_3d': bottom_face,
                'pocs': pocs,
                'x_size': x_size,
                'y_size': y_size,
                'z_size': z_size
            })
    return ducts

def fetch_laterals(conn, utility_map):
    """
    TB_LATERAL_PIPE 테이블로부터 분기배관 원본 정보와 OBB, PoC 정보들을 가져옵니다.
    """
    laterals = []
    with conn.cursor() as cur:
        query = '''
            SELECT "INSTANCE_NAME", "UTILITY", "LATERAL_NUMBER", "UTILITY_GROUP", "LEVEL", "BAY", "BOP",
                   "OBB_LEFT_BOTTOM_BACK_X", "OBB_LEFT_BOTTOM_BACK_Y", "OBB_LEFT_BOTTOM_BACK_Z",
                   "OBB_RIGHT_BOTTOM_BACK_X", "OBB_RIGHT_BOTTOM_BACK_Y", "OBB_RIGHT_BOTTOM_BACK_Z",
                   "OBB_RIGHT_TOP_BACK_X", "OBB_RIGHT_TOP_BACK_Y", "OBB_RIGHT_TOP_BACK_Z",
                   "OBB_LEFT_TOP_BACK_X", "OBB_LEFT_TOP_BACK_Y", "OBB_LEFT_TOP_BACK_Z",
                   "OBB_LEFT_BOTTOM_FRONT_X", "OBB_LEFT_BOTTOM_FRONT_Y", "OBB_LEFT_BOTTOM_FRONT_Z",
                   "OBB_RIGHT_BOTTOM_FRONT_X", "OBB_RIGHT_BOTTOM_FRONT_Y", "OBB_RIGHT_BOTTOM_FRONT_Z",
                   "OBB_RIGHT_TOP_FRONT_X", "OBB_RIGHT_TOP_FRONT_Y", "OBB_RIGHT_TOP_FRONT_Z",
                   "OBB_LEFT_TOP_FRONT_X", "OBB_LEFT_TOP_FRONT_Y", "OBB_LEFT_TOP_FRONT_Z",
                   "POC_ID_LIST", "POC_POSITIONS_LIST", "POC_SIZES_LIST"
            FROM "TB_LATERAL_PIPE"
            WHERE "OBB_LEFT_BOTTOM_BACK_X" IS NOT NULL
        '''
        cur.execute(query)
        for row in cur.fetchall():
            (name, utility_col, lateral_number, utility_group, level, bay, bop,
             obb_lbb_x, obb_lbb_y, obb_lbb_z,
             obb_rbb_x, obb_rbb_y, obb_rbb_z,
             obb_rtb_x, obb_rtb_y, obb_rtb_z,
             obb_ltb_x, obb_ltb_y, obb_ltb_z,
             obb_lbf_x, obb_lbf_y, obb_lbf_z,
             obb_rbf_x, obb_rbf_y, obb_rbf_z,
             obb_rtf_x, obb_rtf_y, obb_rtf_z,
             obb_ltf_x, obb_ltf_y, obb_ltf_z,
             poc_ids, poc_pos, poc_sizes) = row
             
            obb_3d = {
                'lbb': (obb_lbb_x, obb_lbb_y, obb_lbb_z),
                'rbb': (obb_rbb_x, obb_rbb_y, obb_rbb_z),
                'rtb': (obb_rtb_x, obb_rtb_y, obb_rtb_z),
                'ltb': (obb_ltb_x, obb_ltb_y, obb_ltb_z),
                'lbf': (obb_lbf_x, obb_lbf_y, obb_lbf_z),
                'rbf': (obb_rbf_x, obb_rbf_y, obb_rbf_z),
                'rtf': (obb_rtf_x, obb_rtf_y, obb_rtf_z),
                'ltf': (obb_ltf_x, obb_ltf_y, obb_ltf_z)
            }
            
            bottom_face = get_bottom_footprint(obb_3d)
            poly = [(p[0], p[1]) for p in bottom_face]
            pocs = parse_pocs(poc_pos, poc_ids, poc_sizes, utility_map, utility_col)
            
            sorted_verts = sort_box_vertices(list(obb_3d.values()))
            x_size = math.sqrt((sorted_verts[1][0] - sorted_verts[0][0])**2 + (sorted_verts[1][1] - sorted_verts[0][1])**2 + (sorted_verts[1][2] - sorted_verts[0][2])**2)
            y_size = math.sqrt((sorted_verts[2][0] - sorted_verts[1][0])**2 + (sorted_verts[2][1] - sorted_verts[1][1])**2 + (sorted_verts[2][2] - sorted_verts[1][2])**2)
            z_size = math.sqrt((sorted_verts[4][0] - sorted_verts[0][0])**2 + (sorted_verts[4][1] - sorted_verts[0][1])**2 + (sorted_verts[4][2] - sorted_verts[0][2])**2)

            laterals.append({
                'name': name,
                'category': 'LATERAL',
                'utility': utility_col if utility_col else 'DEFAULT',
                'lateral_number': lateral_number if lateral_number else 'N/A',
                'utility_group': utility_group if utility_group else 'N/A',
                'level': level if level else 'N/A',
                'bay': bay if bay else 'N/A',
                'bop': bop if bop is not None else 0.0,
                'poly': poly,
                'obb_3d': obb_3d,
                'bottom_face_3d': bottom_face,
                'pocs': pocs,
                'x_size': x_size,
                'y_size': y_size,
                'z_size': z_size
            })
    return laterals

def export_combined_dxf(eqs, ducts, laterals, out_path):
    """
    모든 설비 객체들의 3차원 바닥 외곽선과 PoC들을 3D 도면 요소로 변환하여 하나의 DXF 도면 파일로 내보냅니다.
    - Z축 고도를 내포한 closed 3D polyline을 그립니다.
    - 각 항목(EQUIPMENT, DUCT, LATERAL) 및 PoC의 세부 유틸리티 속성에 맞춰 레이어를 격리합니다.
    """
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    
    # 대표 분류 기본 레이어 등록
    doc.layers.add('EQUIPMENT', color=ezdxf.colors.WHITE)
    doc.layers.add('DUCT', color=ezdxf.colors.YELLOW)
    doc.layers.add('LATERAL', color=ezdxf.colors.CYAN)
    
    # 중복 레이어 생성을 차단하기 위한 활성 레이어 캐시
    created_layers = {'EQUIPMENT', 'DUCT', 'LATERAL'}
    
    all_elements = [('EQUIPMENT', eqs), ('DUCT', ducts), ('LATERAL', laterals)]
    for category_name, elements in all_elements:
        for elem in elements:
            # 바닥면의 4개 정점(Z 고도값 포함) 추출
            bf_pts = elem['bottom_face_3d']
            points_3d = [(pt[0], pt[1], pt[2]) for pt in bf_pts]
            
            # ezdxf의 3D Polyline 생성기를 호출하여 3D 고도에 맞는 폐쇄 도형 작성
            poly = msp.add_polyline3d(points_3d, dxfattribs={'layer': category_name})
            poly.close(True)
            
            # PoC 연결포트를 3차원 원(Circle)으로 도면에 삽입
            for poc in elem['pocs']:
                # 카테고리와 유틸리티를 조합한 세부 레이어명 형성 (안전 필터 적용)
                layer_name = sanitize_layer_name(f"{category_name}_POC_{poc['utility']}")
                if layer_name not in created_layers:
                    color_index = get_color(poc['utility'], is_cad=True)
                    doc.layers.add(layer_name, color=color_index)
                    created_layers.add(layer_name)
                
                # Z값을 원 중심에 포함하여 3D 상의 올바른 공간 위치에 묘사
                center_3d = (poc['x'], poc['y'], poc['z'])
                msp.add_circle(center_3d, poc['radius'], dxfattribs={'layer': layer_name})
                
    doc.saveas(out_path)
    print(f"Combined 3D DXF saved to {out_path}")

def export_combined_shp(eqs, ducts, laterals, out_dir):
    """
    GIS 공간정보 호환을 위해 수집된 모든 설비 기하 영역(3D POLYGONZ) 및 
    PoC 포트 위치(3D POINTZ)의 입체 공간정보 파일(.shp, .dbf, .shx) 세트를 내보냅니다.
    """
    os.makedirs(out_dir, exist_ok=True)
    elements_shp_path = os.path.join(out_dir, "combined_elements")
    pocs_shp_path = os.path.join(out_dir, "combined_pocs")
    
    # 1. 3D 입체 다각형(POLYGONZ) 내보내기기
    with shapefile.Writer(elements_shp_path, shapeType=shapefile.POLYGONZ) as w:
        w.field("NAME", "C", "100")
        w.field("CATEGORY", "C", "20")
        w.field("UTILITY", "C", "20")
        w.field("LEVEL", "C", "20")
        w.field("BAY", "C", "20")
        w.field("BOP", "N", decimal=2)
        w.field("X_SIZE", "N", decimal=2)
        w.field("Y_SIZE", "N", decimal=2)
        w.field("Z_SIZE", "N", decimal=2)
        
        all_items = eqs + ducts + laterals
        for item in all_items:
            bf = item['bottom_face_3d']
            # GIS Polygon 규격에 맞춰 마지막 정점은 첫 정점과 일치시켜 루프 강제 마감
            part = [
                [bf[0][0], bf[0][1], bf[0][2]],
                [bf[1][0], bf[1][1], bf[1][2]],
                [bf[2][0], bf[2][1], bf[2][2]],
                [bf[3][0], bf[3][1], bf[3][2]],
                [bf[0][0], bf[0][1], bf[0][2]]
            ]
            w.polyz([part])
            w.record(
                item['name'],
                item['category'],
                item['utility'],
                item['level'],
                item.get('bay', 'N/A'),
                item.get('bop', 0.0),
                item['x_size'],
                item['y_size'],
                item['z_size']
            )
            
    print(f"Combined 3D Elements Shapefile saved to {elements_shp_path}.shp")
    
    # 2. 3D 입체 포인트(POINTZ) 연결점 내보내기
    with shapefile.Writer(pocs_shp_path, shapeType=shapefile.POINTZ) as w:
        w.field("PARENT_NM", "C", "100")
        w.field("CATEGORY", "C", "20")
        w.field("UTILITY", "C", "20")
        w.field("RADIUS", "N", decimal=2)
        w.field("Z_COORD", "N", decimal=2)
        
        for item in all_items:
            for poc in item['pocs']:
                w.pointz(poc['x'], poc['y'], poc['z'])
                w.record(
                    item['name'],
                    item['category'],
                    poc['utility'],
                    poc['radius'],
                    poc['z']
                )
                
    print(f"Combined 3D PoCs Shapefile saved to {pocs_shp_path}.shp")

def export_combined_png(eqs, ducts, laterals, out_path):
    """
    Matplotlib 2D Plotter를 구동하여 장비, 덕트, 분기배관 및 PoC를 
    각기 다른 구분 색상의 오버레이 패치로 변환하여 도면 평면도 이미지(PNG)를 작성합니다.
    """
    fig, ax = plt.subplots(figsize=(20, 20))
    ax.set_aspect('equal') # 도면 축척 1:1 비율 유지
    
    drawn_utilities = set()
    
    # 1. 장비(Equipment) 면적 패치 작도 및 PoC 포팅 (회색 테마)
    for eq in eqs:
        poly = Polygon(eq['poly'], closed=True, facecolor='#E0E0E0', edgecolor='#808080', alpha=0.5, zorder=1)
        ax.add_patch(poly)
        for poc in eq['pocs']:
            color = get_color(poc['utility'], is_cad=False)
            drawn_utilities.add(poc['utility'])
            circ = Circle((poc['x'], poc['y']), poc['radius'], facecolor=color, edgecolor='black', zorder=4)
            ax.add_patch(circ)
            
    # 2. 덕트(Duct) 면적 패치 작도 및 PoC 포팅 (연주황 비스크 테마)
    for duct in ducts:
        poly = Polygon(duct['poly'], closed=True, facecolor='#FFE4C4', edgecolor='#D2B48C', alpha=0.6, zorder=2)
        ax.add_patch(poly)
        for poc in duct['pocs']:
            color = get_color(poc['utility'], is_cad=False)
            drawn_utilities.add(poc['utility'])
            circ = Circle((poc['x'], poc['y']), poc['radius'], facecolor=color, edgecolor='black', zorder=4)
            ax.add_patch(circ)
            
    # 3. 분기배관(Lateral) 면적 패치 작도 및 PoC 포팅 (연청색 스틸블루 테마)
    for lat in laterals:
        poly = Polygon(lat['poly'], closed=True, facecolor='#B0C4DE', edgecolor='#4682B4', alpha=0.6, zorder=3)
        ax.add_patch(poly)
        for poc in lat['pocs']:
            color = get_color(poc['utility'], is_cad=False)
            drawn_utilities.add(poc['utility'])
            circ = Circle((poc['x'], poc['y']), poc['radius'], facecolor=color, edgecolor='black', zorder=4)
            ax.add_patch(circ)
            
    ax.autoscale_view()
    
    # 도면 가독성을 증진할 범례 패치 구성
    legend_patches = [
        mpatches.Patch(color='#E0E0E0', label='Category: Equipment (장비)'),
        mpatches.Patch(color='#FFE4C4', label='Category: Duct (덕트)'),
        mpatches.Patch(color='#B0C4DE', label='Category: Lateral (분기배관)'),
    ]
    for u in sorted(drawn_utilities):
        c = get_color(u, is_cad=False)
        legend_patches.append(mpatches.Patch(color=c, label=f'PoC Utility: {u}'))
        
    ax.legend(handles=legend_patches, loc='upper right')
    
    plt.title('Combined Floor Plan Layout (Equipment, Duct, Lateral & PoCs)')
    plt.xlabel('X (mm)')
    plt.ylabel('Y (mm)')
    plt.grid(True, linestyle='--', alpha=0.5)
    
    plt.savefig(out_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Combined PNG layout saved to {out_path}")

def main():
    """
    통합 반출 파이프라인의 전체 프로세스를 총괄 조율하는 엔트리포인트 함수입니다.
    """
    parser = argparse.ArgumentParser(description="Export combined equipment, duct, and lateral plan drawings.")
    add_common_args(parser)
    args = parser.parse_args()
    runtime = resolve_runtime(args)

    print("Connecting to database...")
    try:
        conn = psycopg2.connect(runtime.conninfo)
    except Exception as e:
        print(f"DB Connection failed: {e}")
        return

    print("Fetching data from DB...")
    # 1단계: 배관 노드 유틸리티 사전 조회 구축
    utility_map = fetch_utility_map(conn)
    
    # 2단계: 각 데이터 테이블(장비, 덕트, 분기배관) 순차 로딩 진행
    print("Loading Equipment data...")
    eqs = fetch_equipments(conn, utility_map)
    print(f"Loaded {len(eqs)} equipments.")
    
    print("Loading Duct data...")
    ducts = fetch_ducts(conn, utility_map)
    print(f"Loaded {len(ducts)} ducts.")
    
    print("Loading Lateral Pipe data...")
    laterals = fetch_laterals(conn, utility_map)
    print(f"Loaded {len(laterals)} laterals.")
    
    # 로딩 완료 후 안전하게 DB 커넥션 종료
    conn.close()
    
    # 출력 경로 검사 및 폴더 생성성
    out_dir = runtime.out_dir
    print_runtime(runtime)
    
    dxf_path = os.path.join(out_dir, "combined_plan.dxf")
    png_path = os.path.join(out_dir, "combined_plan.png")
    
    # 3단계: 통합 DXF, Shapefile, 시각화 PNG 도면 일괄 도출
    print("Generating combined drawings...")
    export_combined_dxf(eqs, ducts, laterals, dxf_path)
    export_combined_shp(eqs, ducts, laterals, out_dir)
    export_combined_png(eqs, ducts, laterals, png_path)
    
    print("\nAll export tasks completed successfully!")

if __name__ == '__main__':
    main()
