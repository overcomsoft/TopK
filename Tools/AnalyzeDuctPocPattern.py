"""
[실행 명령어]
기본 실행 (Tools 디렉토리 기준 또는 프로젝트 루트에서):
> python Tools/AnalyzeDuctPocPattern.py create-schema
> python Tools/AnalyzeDuctPocPattern.py analyze --dry-run
> python Tools/AnalyzeDuctPocPattern.py run-all
공통 DB 접속 옵션(--host, --port, --dbname, --user, --password, --conn-str, --config)은
서브커맨드 이름보다 "앞"에 위치해야 합니다. 예:
> python Tools/AnalyzeDuctPocPattern.py --password dinno run-all

[목적]
PostgreSQL DB(DDW_AI_DB)에 저장된 덕트(TB_DUCT) 1건 1건마다, 그 덕트 몸체에서 실제로
갈라져 나가는 곁가지 분기점(TAKEOFF_POC_POSITIONS_LIST/TAKEOFF_POC_SIZES_LIST 기반,
이하 "takeoff")들이 덕트의 어느 면(상단 TOP / 좌측 LEFT / 우측 RIGHT / 하단 BOTTOM /
본선 양 끝단 END)에, 어떤 간격·순서·유틸리티 시퀀스로 분포하는지 분석하여
TB_DUCT_POC_PATTERN 테이블에 적재합니다.

[분석 대상이 POC_POSITIONS_LIST가 아니라 TAKEOFF_POC_POSITIONS_LIST인 이유]
TB_DUCT의 POC_ID_LIST/POC_POSITIONS_LIST/POC_SIZES_LIST(코드상 duct['pocs'])는
덕트가 앞뒤로 인접 덕트와 체인처럼 이어지는 "본선 연결 관절"이라, 실제 데이터에서
행마다 정확히 2개, 항상 길이축 양 끝단(END)에만 위치한다 — 즉 면 분포를 볼 대상이
아니다. 실제로 상단/좌측/우측 등으로 갈라지는 곁가지 분기는 TAKEOFF_POC_ID_LIST/
TAKEOFF_POC_POSITIONS_LIST/TAKEOFF_POC_SIZES_LIST(코드상 duct['takeoffs'])에 담겨
있으므로, 이 파일의 모든 면(Face) 분포 패턴 분석은 duct['takeoffs']를 대상으로 한다.

ExportDuctPlan.py는 DXF/PNG/Shapefile 시각화 전용이라 이런 "패턴" 통계를 생성하지 않으므로,
동일한 fetch_data()를 그대로 재사용하되 이 파일에서 패턴 분석만 별도로 수행합니다.

[전체 흐름도]
1. fetch_data() (ExportDuctPlan.py 재사용) — TB_DUCT의 OBB 8정점과 takeoff(및 본선 POC)
   좌표/구경/유틸리티 목록 로드
2. compute_duct_local_frame() — 덕트별로 로컬 3축(길이/높이/폭) 산출
3. classify_duct_takeoffs()/classify_poc_face() — 각 takeoff를 3축에 투영해 소속 면
   (TOP/BOTTOM/LEFT/RIGHT/END) 판정
4. analyze_duct_pattern() — 면별로 takeoff를 길이축 순 정렬 후 간격 시퀀스·등간격 여부(CV)·
   유틸리티 시퀀스 계산. classify_layout_pattern()으로 배치 형태(일직선/지그재그/분리형/
   불규칙)도 함께 판정.
5. save_patterns() — 덕트당 1행으로 TB_DUCT_POC_PATTERN에 UPSERT(DUCT_NAME 기준)
6. export_face_pattern_png() — 전체 덕트 풋프린트 + takeoff를 면(Face) 색상 하나의 PNG에
   합쳐서 시각화 저장 (analyze/run-all 실행 시 dry-run 여부와 무관하게 항상 out_dir에 저장됨)
7. export_duct_face_pattern_pngs() — 덕트 1개당 PNG 1장씩, PER_DUCT_PNG_DIR
   (기본값 data/out/duct-face-img/)에 저장. 전체 플랜트를 한 장에 몰아 그리면 개별
   취출구가 너무 작아 색이 안 보이는 export_face_pattern_png()의 한계를 보완.
8. resolve_takeoff_equipment_map()/build_equipment_takeoff_patterns() — 취출구를
   TB_ROUTE_PATH.TARGET_GUID 정확 일치로 장비(EQUIPMENT_TAG)에 귀속시킨 뒤,
   (EQUIPMENT_TAG, UTILITY, 면 분포+배치형태 시그니처)별로 몇 개의 덕트가 그 패턴을
   갖는지 집계해 TB_DUCT_EQUIPMENT_TAKEOFF_PATTERN에 저장. 커버리지가 낮다는 한계가
   있음(2026-07-15 실측: 전체 취출구 1,193건 중 105건, 8.8%만 장비까지 확인됨 — 나머지는
   중간 레터럴/부속 배관이 DB에 없는 데이터 공백).

[핵심 알고리즘: 왜 1차원 정렬이 아니라 "면(Face) 분류"가 먼저 필요한가]
덕트는 길이 방향으로 길고 단면(폭×높이)이 얇은 육면체(OBB)입니다. takeoff는 상단/좌측/
우측 등 서로 다른 면에 붙을 수 있으므로, 그냥 하나의 축으로만 정렬하면 서로 다른 면에
붙은 takeoff들이 뒤섞여 간격·순서 통계가 무의미해집니다. 따라서:
1. compute_duct_local_frame(): OBB 8정점에서 덕트 고유 로컬 3축(길이/높이/폭)과
   half-extent(중심에서 면까지의 절반 길이)를 계산합니다. 높이축은 세계 Z축과 내적이
   가장 큰 축으로, 길이축은 나머지 두 축 중 half-extent가 더 큰 쪽으로 자동 판별하여
   평면상 회전된 덕트에도 안전하게 동작합니다(컬럼명이 암시하는 축 배정을 그대로 믿지 않음).
2. classify_poc_face(): takeoff 위치를 로컬 3축에 투영해 TOP/BOTTOM/LEFT/RIGHT 중 하나로 분류.
   단, 길이축 투영이 끝단(half_len)에 매우 근접(END_ZONE_RATIO 이상)하면 END로 분리합니다
   (드물지만 덕트 끝단 가까이에 곁가지가 붙는 경우를 본선 연결부와 구분하기 위함).
3. analyze_duct_pattern(): 면별로 takeoff를 길이축 위치 순으로 정렬해 간격 시퀀스, 등간격
   여부(간격의 변동계수 CV 기준), 유틸리티 시퀀스를 계산합니다.

[핵심 알고리즘 ②: "일직선 vs 지그재그" 배치 형태 판정 (2026-07-16 신규)]
간격 시퀀스(길이축 1차원)만으로는 같은 면에 취출구가 3개 있을 때 "일직선으로 나란히"
붙었는지 "좌우로 번갈아(지그재그)" 붙었는지 구분할 수 없습니다. 이를 구분하려면 그 면의
"횡방향"(TOP/BOTTOM면은 폭축, LEFT/RIGHT면은 높이축)으로 각 취출구가 얼마나, 어떤
순서로 퍼져있는지 봐야 합니다.
1. _transverse_axis(): 면(Face)에 따라 폭축(axis_w) 또는 높이축(axis_h) 중 어느 쪽이
   "그 면 위에서의 좌우 방향"인지 결정.
2. classify_duct_takeoffs(): 기존 proj_len(길이축 위치)에 더해 proj_transverse(횡방향
   위치)도 함께 계산.
3. classify_layout_pattern(): 횡방향 오프셋의 표준편차가 작으면 곧바로 STRAIGHT(일직선).
   그 외에는 _cluster_1d()로 몇 개의 물리적 열(track)로 나뉘는지 추정해, 정확히 2열이면
   길이축 순서대로 좌우 부호가 바뀌는 비율(alternation_rate)을 계산 — 높으면 ZIGZAG
   (지그재그), 낮으면 SPLIT_ROWS(분리형 이중열), 애매하면 IRREGULAR. 3열 이상이면
   바로 IRREGULAR.

[주요 변수]
- END_ZONE_RATIO: 길이축 정규화 투영값(-1~1)의 절대값이 이 값 이상이면 END로 분류
- EQUAL_SPACING_CV_THRESHOLD: 간격 리스트의 변동계수(표준편차/평균)가 이 값 이하이면
  "등간격 배치"로 판정하는 기준값
- STRAIGHT_STD_THRESHOLD_MM / CLUSTER_GAP_THRESHOLD_MM / ZIGZAG_ALTERNATION_MIN /
  SPLIT_ALTERNATION_MAX: classify_layout_pattern()의 배치 형태 판정 임계값 (실측 데이터로
  보정한 값 — 정의부 주석 참조)
"""

import argparse
import math
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

import tool_config
# ExportDuctPlan을 임포트하는 시점에 matplotlib.use('Agg') 백엔드 설정 및
# Path.__deepcopy__ 몽키패치가 함께 적용되므로, 이 파일에서 matplotlib을 별도로
# 초기화할 필요 없이 바로 pyplot을 사용할 수 있다.
from ExportDuctPlan import fetch_data
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Circle
import matplotlib.patches as mpatches

# 길이축 정규화 투영 비율이 이 값(95%) 이상이면 곁가지 PoC가 아니라
# 덕트 본선 자체의 시작/끝단 연결부(END)로 분류한다.
END_ZONE_RATIO = 0.95

# 면(Face) 내 인접 PoC 간격들의 변동계수(CV = 표준편차 / 평균)가 이 값 이하이면
# "등간격 배치"로 판정한다. 값이 작을수록 등간격 판정이 엄격해진다.
EQUAL_SPACING_CV_THRESHOLD = 0.15

# --- 배치 형태(일직선/지그재그/분리형/불규칙) 판정 임계값 ---
# (2026-07-16, 실측 데이터로 보정: takeoff 3개 이상인 면(Face) 그룹 146건의 횡방향
#  오프셋 표준편차 분포를 보면 중앙값 5.6mm, 상위 25%부터 100mm 이상으로 뚜렷이
#  갈렸다 — classify_layout_pattern() 참조)
STRAIGHT_STD_THRESHOLD_MM = 30.0   # 횡방향 오프셋 표준편차가 이 값 이하이면 무조건 "일직선"
CLUSTER_GAP_THRESHOLD_MM = 80.0    # 횡방향 오프셋을 몇 개의 물리적 열(track)로 나눌지 판단하는 간격 기준
ZIGZAG_ALTERNATION_MIN = 0.7       # 2열일 때, 인접 취출구 간 좌우 부호가 바뀌는 비율이 이 이상이면 "지그재그"
SPLIT_ALTERNATION_MAX = 0.3        # 2열일 때, 위 비율이 이 이하이면 "분리형"(앞뒤로 몰려 배치)

# PNG 시각화에서 면(Face)별 PoC 색상 매핑. ExportDuctPlan.PLT_COLORS(유틸리티 기준)와
# 달리 이 테이블은 "어느 면에 붙어있는가"를 색으로 구분하기 위한 것이다.
FACE_COLORS = {
    'TOP': '#FF0000',      # 빨강 — 상단 곁가지
    'BOTTOM': '#8B4513',   # 갈색 — 하단 곁가지 (실무상 드문 케이스)
    'LEFT': '#0000FF',     # 파랑 — 좌측 곁가지
    'RIGHT': '#00A000',    # 초록 — 우측 곁가지
    'END': '#808080',      # 회색 — 본선 자체의 시작/끝단 연결부
}

# 덕트 1개당 PNG 1장씩 저장할 기본 디렉토리. export_face_pattern_png()가 전체 플랜트를
# 한 이미지에 몰아 그려 개별 취출구가 픽셀 단위로 작아지는 문제(전체 플랜트 스케일에서는
# 반경 50~150mm 취출구가 1~3픽셀에 불과해 면 색상이 검은 테두리에 묻힘)를 해결하기 위해,
# 덕트별로 그 덕트의 바운딩 박스에 맞춰 확대한 개별 이미지를 별도 경로에 저장한다.
PER_DUCT_PNG_DIR = Path(__file__).resolve().parents[1] / "data" / "out" / "duct-face-img"


def _mean(points):
    """3차원 좌표 리스트의 산술 평균(중심점)을 (x, y, z) 튜플로 반환합니다."""
    n = len(points)
    return (
        sum(p[0] for p in points) / n,
        sum(p[1] for p in points) / n,
        sum(p[2] for p in points) / n,
    )


def _sub(a, b):
    """3차원 벡터 뺄셈 a - b."""
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    """3차원 벡터 내적(dot product) a·b."""
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    """
    벡터를 단위벡터화합니다.

    [인자 (Arguments)]
    - a (tuple): 정규화할 3차원 벡터 (x, y, z)

    [반환값 (Returns)]
    - tuple: (단위벡터 (x, y, z), 원래 벡터의 길이(float))
      벡터 길이가 0에 가까우면(퇴화한 OBB 등) 영벡터 (0,0,0)과 길이 0.0을 반환하여
      이후 나눗셈(half-extent 계산 등)에서 ZeroDivisionError가 나지 않도록 방어합니다.
    """
    length = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)
    if length < 1e-9:
        return (0.0, 0.0, 0.0), 0.0
    return (a[0] / length, a[1] / length, a[2] / length), length


def compute_duct_local_frame(obb_3d: dict) -> dict:
    """
    덕트 OBB의 8개 정점으로부터 덕트 고유의 로컬 3축(길이축/높이축/폭축)과
    각 축의 half-extent(중심~면까지 절반 길이)를 계산합니다.

    [인자 (Arguments)]
    - obb_3d (dict): get_bottom_footprint()가 아닌 원본 OBB 8정점 딕셔너리
      (키: 'lbb','rbb','rtb','ltb','lbf','rbf','rtf','ltf', 값: (x,y,z) 튜플)

    [반환값 (Returns)]
    - dict: {
        'centroid': 8정점 전체의 중심점 (x,y,z),
        'axis_len': 길이축 단위벡터, 'half_len': 길이축 half-extent,
        'axis_h':   높이축 단위벡터, 'half_h':   높이축 half-extent,
        'axis_w':   폭축 단위벡터,   'half_w':   폭축 half-extent,
      }

    [주요 변수 및 판정 로직]
    - left/right, bottom/top, back/front: OBB 정점 컬럼명(LEFT/RIGHT, BOTTOM/TOP,
      BACK/FRONT)이 암시하는 3개 대응쌍 각 4점씩을 그룹핑
    - axis_a/b/c, len_a/b/c: 각 그룹쌍의 평균 위치차로 얻은 3개의 "후보축"과 그 원래 길이
      (아직 어느 축이 길이/높이/폭인지 확정되지 않은 상태)
    - height_key, axis_h, half_h: 세계 Z축(0,0,1)과의 내적 절대값이 가장 큰 후보축을
      "높이축"으로 채택. 덕트는 대개 수평 설치되므로 이 방식이 평면상 회전된 덕트에도
      안전하게 동작합니다. 이후 axis_h[2]가 음수이면 부호를 뒤집어 항상 "+가 위쪽"이
      되도록 보정합니다(그래야 classify_poc_face에서 TOP/BOTTOM 부호 판정이 일관됨).
    - remaining, axis_len/half_len, axis_w/half_w: 높이축을 제외한 나머지 두 후보축 중
      half-extent(치수)가 더 큰 쪽을 "길이축"(덕트는 기다란 형태라는 전제), 남은 쪽을
      "폭축"으로 확정. 컬럼명이 암시하는 축 배정(BACK/FRONT=길이축)을 그대로 믿지 않고
      실측 치수로 재검증하는 방식이라 임의 회전/치수 조합에도 안전합니다.
    """
    v = obb_3d
    left = [v['lbb'], v['ltb'], v['lbf'], v['ltf']]
    right = [v['rbb'], v['rtb'], v['rbf'], v['rtf']]
    bottom = [v['lbb'], v['rbb'], v['lbf'], v['rbf']]
    top = [v['ltb'], v['rtb'], v['ltf'], v['rtf']]
    back = [v['lbb'], v['rbb'], v['rtb'], v['ltb']]
    front = [v['lbf'], v['rbf'], v['rtf'], v['ltf']]

    # 3개의 "후보축" 계산: 각 대응쌍(예: left↔right)의 4점 평균 위치 차이를 단위벡터화.
    # len_a/b/c는 단위벡터화 전 벡터의 길이 = 해당 방향의 전체 치수(half-extent의 2배).
    axis_a, len_a = _norm(_sub(_mean(right), _mean(left)))
    axis_b, len_b = _norm(_sub(_mean(top), _mean(bottom)))
    axis_c, len_c = _norm(_sub(_mean(front), _mean(back)))

    candidates = [
        ('a', axis_a, len_a / 2.0),
        ('b', axis_b, len_b / 2.0),
        ('c', axis_c, len_c / 2.0),
    ]

    # 높이축 판정: 세계 Z축과 가장 나란한(내적 절대값이 가장 큰) 후보축을 채택.
    height_key, axis_h, half_h = max(candidates, key=lambda t: abs(t[1][2]))
    if axis_h[2] < 0:
        # "+방향 = 위쪽"이 되도록 부호 통일 (TOP/BOTTOM 판정 일관성을 위함)
        axis_h = (-axis_h[0], -axis_h[1], -axis_h[2])

    # 길이축/폭축 판정: 높이축을 제외한 나머지 두 축 중 치수가 더 큰 쪽이 길이축.
    remaining = [t for t in candidates if t[0] != height_key]
    remaining.sort(key=lambda t: t[2], reverse=True)
    _, axis_len, half_len = remaining[0]
    _, axis_w, half_w = remaining[1]

    centroid = _mean(list(v.values()))

    return {
        'centroid': centroid,
        'axis_len': axis_len, 'half_len': half_len,
        'axis_h': axis_h, 'half_h': half_h,
        'axis_w': axis_w, 'half_w': half_w,
    }


def classify_poc_face(poc_xyz, frame: dict):
    """
    PoC 위치를 덕트 로컬 3축에 투영하여 소속 면과 길이축 위치를 판정합니다.

    [인자 (Arguments)]
    - poc_xyz (tuple): PoC의 3D 절대좌표 (x, y, z)
    - frame (dict): compute_duct_local_frame()이 반환한 로컬 좌표계 정보

    [반환값 (Returns)]
    - tuple: (face(str), proj_len(float))
      * face: 'TOP' | 'BOTTOM' | 'LEFT' | 'RIGHT' | 'END' 중 하나
      * proj_len: 길이축 위 투영 위치(부호 있는 실수, mm). 같은 면에 속한 PoC들을
        길이축 순으로 정렬하고 간격을 계산하는 데 사용됩니다.

    [주요 변수 및 판정 로직]
    - rel: PoC 위치에서 덕트 중심(centroid)을 뺀 상대좌표
    - proj_len/proj_h/proj_w: rel을 각각 길이축/높이축/폭축에 투영(내적)한 부호 있는 거리
    - norm_len/norm_h/norm_w: 위 투영값을 해당 축의 half-extent로 나눠 -1~1 범위로 정규화
      (즉, ±1에 가까울수록 그 축 방향의 "면"에 가깝다는 뜻)
    - 판정 순서:
      1) |norm_len| >= END_ZONE_RATIO 이면, 곁가지가 아니라 덕트 본선 자체의 시작/끝단
         연결부이므로 END로 우선 분류 (본선 연결과 곁가지 분기를 통계적으로 분리하기 위함)
      2) 그 외에는 |norm_h|와 |norm_w| 중 더 큰 쪽의 축·부호로 TOP/BOTTOM 또는 LEFT/RIGHT 결정
    """
    rel = _sub(poc_xyz, frame['centroid'])
    proj_len = _dot(rel, frame['axis_len'])
    proj_h = _dot(rel, frame['axis_h'])
    proj_w = _dot(rel, frame['axis_w'])

    # half-extent가 0에 가까운 퇴화 축(폭/높이가 사실상 0인 기형 OBB)은 0으로 취급해
    # ZeroDivisionError 없이 안전하게 넘어간다.
    norm_len = proj_len / frame['half_len'] if frame['half_len'] > 1e-6 else 0.0
    norm_h = proj_h / frame['half_h'] if frame['half_h'] > 1e-6 else 0.0
    norm_w = proj_w / frame['half_w'] if frame['half_w'] > 1e-6 else 0.0

    if abs(norm_len) >= END_ZONE_RATIO:
        face = 'END'
    elif abs(norm_h) >= abs(norm_w):
        face = 'TOP' if norm_h >= 0 else 'BOTTOM'
    else:
        face = 'RIGHT' if norm_w >= 0 else 'LEFT'

    return face, proj_len


def _transverse_axis(face: str, frame: dict):
    """
    면(Face)의 "배치 형태"(일직선/지그재그 등)를 볼 때 기준이 되는 횡방향 축을 반환합니다.
    TOP/BOTTOM면은 그 면 위에서 폭 방향(axis_w)으로 좌우가 갈리고, LEFT/RIGHT면은 높이
    방향(axis_h)으로 상하가 갈립니다. END는 본선 끝단이라 배치 패턴의 의미가 제한적이지만
    일관성을 위해 폭축을 기본값으로 사용합니다.
    """
    if face in ('LEFT', 'RIGHT'):
        return frame['axis_h']
    return frame['axis_w']


def _cluster_1d(values: list, gap_threshold: float) -> list:
    """
    정렬된 1차원 값들을 gap_threshold보다 큰 간격이 나오는 지점에서 끊어 클러스터(물리적
    열/track)로 묶습니다. classify_layout_pattern()이 취출구가 실제로 몇 개의 열에 나뉘어
    배치됐는지 추정하는 데 사용합니다.

    [반환값 (Returns)]
    - list of list: 클러스터별 값 목록 (입력이 비어있으면 빈 리스트)
    """
    if not values:
        return []
    ordered = sorted(values)
    clusters = [[ordered[0]]]
    for v in ordered[1:]:
        if v - clusters[-1][-1] <= gap_threshold:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return clusters


def classify_layout_pattern(transverse_offsets: list) -> dict:
    """
    한 면(Face) 내 취출구들의 횡방향 오프셋 시퀀스(길이축 위치 순으로 정렬된 상태)로부터
    배치 형태를 판정합니다.

    [인자 (Arguments)]
    - transverse_offsets (list of float): 길이축 위치 순으로 정렬된 취출구들의 횡방향
      오프셋(TOP/BOTTOM면은 폭축 axis_w, LEFT/RIGHT면은 높이축 axis_h 투영값) 리스트.
      _transverse_axis()로 어느 축을 쓸지 결정한 뒤 이 함수에 넘겨줍니다.

    [반환값 (Returns)]
    - dict: {'layout': str, 'transverse_std': float, 'track_count': int,
             'alternation_rate': float | None}
      layout은 다음 중 하나:
      * 'NONE': 취출구 0개
      * 'SINGLE': 취출구 1개 (배치 형태를 논할 수 없음)
      * 'STRAIGHT'(일직선): 횡방향으로 거의 안 퍼짐
      * 'ZIGZAG'(지그재그): 정확히 2개 열로 나뉘고, 인접 취출구끼리 번갈아 배치됨
      * 'SPLIT_ROWS'(분리형 이중열): 정확히 2개 열로 나뉘지만 앞뒤로 몰려서 배치됨
        (예: 앞쪽 절반은 왼쪽 열, 뒤쪽 절반은 오른쪽 열)
      * 'IRREGULAR'(불규칙): 3개 이상 열이거나, 2열이지만 지그재그/분리형 어느 쪽에도
        뚜렷하게 속하지 않음

    [주요 변수 및 판정 로직]
    1. 표준편차(std)가 STRAIGHT_STD_THRESHOLD_MM(30mm) 이하면 곧바로 STRAIGHT — 횡방향
       퍼짐 자체가 작으므로 순서와 무관하게 사실상 일직선.
    2. 그 외에는 _cluster_1d()로 CLUSTER_GAP_THRESHOLD_MM(80mm) 간격 기준 열 개수(track_count)를
       추정. 1개면 STRAIGHT로 재확인, 3개 이상이면 바로 IRREGULAR.
    3. 정확히 2개 열인 경우에만 alternation_rate(인접 취출구 사이에서 전체 평균 대비 부호가
       바뀌는 비율)를 계산해 ZIGZAG(>=0.7)/SPLIT_ROWS(<=0.3)/IRREGULAR(그 사이)를 가른다.

    [알려진 한계]
    아웃라이어(노이즈성 이상치) 1~2개가 섞이면 실제로는 대부분 STRAIGHT에 가까운 배치도
    track_count가 3 이상으로 잡혀 IRREGULAR로 분류될 수 있습니다. 아웃라이어에 강건한
    클러스터링(예: DBSCAN)은 이번 구현 범위에서 제외했습니다 — 실측 샘플을 봤을 때
    간단한 임계값 방식으로도 STRAIGHT/ZIGZAG 주요 케이스가 뚜렷하게 갈렸기 때문입니다.
    """
    n = len(transverse_offsets)
    if n == 0:
        return {'layout': 'NONE', 'transverse_std': 0.0, 'track_count': 0, 'alternation_rate': None}
    if n == 1:
        return {'layout': 'SINGLE', 'transverse_std': 0.0, 'track_count': 1, 'alternation_rate': None}

    mean_t = sum(transverse_offsets) / n
    variance = sum((v - mean_t) ** 2 for v in transverse_offsets) / n
    std = math.sqrt(variance)

    if std <= STRAIGHT_STD_THRESHOLD_MM:
        return {'layout': 'STRAIGHT', 'transverse_std': round(std, 1), 'track_count': 1, 'alternation_rate': None}

    track_count = len(_cluster_1d(transverse_offsets, CLUSTER_GAP_THRESHOLD_MM))

    if track_count == 1:
        return {'layout': 'STRAIGHT', 'transverse_std': round(std, 1), 'track_count': 1, 'alternation_rate': None}
    if track_count != 2:
        return {'layout': 'IRREGULAR', 'transverse_std': round(std, 1), 'track_count': track_count, 'alternation_rate': None}

    # 정확히 2개 열: 원래(길이축) 순서대로 전체 평균 대비 부호를 매겨 번갈아 배치되는지 확인
    signs = [1 if v >= mean_t else -1 for v in transverse_offsets]
    flips = sum(1 for i in range(n - 1) if signs[i] != signs[i + 1])
    alternation_rate = flips / (n - 1)

    if alternation_rate >= ZIGZAG_ALTERNATION_MIN:
        layout = 'ZIGZAG'
    elif alternation_rate <= SPLIT_ALTERNATION_MAX:
        layout = 'SPLIT_ROWS'
    else:
        layout = 'IRREGULAR'

    return {
        'layout': layout,
        'transverse_std': round(std, 1),
        'track_count': track_count,
        'alternation_rate': round(alternation_rate, 2),
    }


def build_multipoint_z_wkt(points: list) -> str | None:
    """
    (x,y,z) 좌표 리스트를 PostGIS MULTIPOINT Z WKT(Well-Known Text) 문자열로 변환합니다.
    TB_DUCT_POC_PATTERN.TAKEOFF_LAYOUT(geometry(MultiPointZ, 0)) 컬럼에 저장하기 위해 사용.

    [인자 (Arguments)]
    - points (list of tuple): (x, y, z) 좌표 튜플 리스트

    [반환값 (Returns)]
    - str | None: 'MULTIPOINT Z (x1 y1 z1, x2 y2 z2, ...)' 형태의 WKT 문자열.
      points가 비어있으면(취출구가 없는 덕트) 저장할 지오메트리가 없다는 뜻이므로 None을
      반환합니다 — save_patterns()에서 ST_GeomFromText(NULL, 0)을 통해 NULL로 저장됩니다.
    """
    if not points:
        return None
    coords = ", ".join(f"{x} {y} {z}" for x, y, z in points)
    return f"MULTIPOINT Z ({coords})"


def classify_duct_takeoffs(duct: dict) -> list:
    """
    한 덕트에 속한 "곁가지 분기점(takeoffs)" 전체를 면(Face) 판정하여, 원본 정보
    (x,y,z,radius,utility)에 'face'와 'proj_len'을 덧붙인 평탄한 리스트로 반환합니다.

    [분석 대상이 duct['pocs']가 아니라 duct['takeoffs']인 이유]
    TB_DUCT 1행은 덕트 1개를 의미하며, 'pocs'(POC_POSITIONS_LIST 기반)는 그 덕트가
    앞/뒤로 인접 덕트와 체인처럼 이어지는 "본선 연결 관절"이라 실사용 데이터에서
    항상 정확히 2개, 항상 길이축 양 끝단(END)에만 위치한다 — 즉 면(Face) 분포
    패턴을 볼 대상이 아니다. 실제로 상단/좌측/우측 등으로 갈라지는 곁가지는
    TAKEOFF_POC_POSITIONS_LIST/TAKEOFF_POC_SIZES_LIST 기반의 'takeoffs'에 담겨
    있으므로, 면 분포 패턴 분석은 반드시 이쪽을 대상으로 해야 한다.

    analyze_duct_pattern()의 집계와 export_face_pattern_png()의 시각화가 이 함수를
    공용으로 사용하여, 로컬 좌표계 계산과 면 판정 로직이 두 곳에 중복 구현되지
    않도록 합니다.

    [인자 (Arguments)]
    - duct (dict): fetch_data()가 반환하는 덕트 1건 (obb_3d, takeoffs 포함)

    [반환값 (Returns)]
    - list of dict: 각 원소는 원본 takeoff 딕셔너리 + {'face': str, 'proj_len': float,
      'proj_transverse': float}. proj_transverse는 _transverse_axis()가 정한 횡방향
      축(면에 따라 폭축 또는 높이축) 투영값으로, classify_layout_pattern()이 일직선/
      지그재그 등 배치 형태를 판정하는 데 사용합니다.
    """
    frame = compute_duct_local_frame(duct['obb_3d'])
    classified = []
    for takeoff in duct['takeoffs']:
        pos = (takeoff['x'], takeoff['y'], takeoff['z'])
        face, proj_len = classify_poc_face(pos, frame)
        rel = _sub(pos, frame['centroid'])
        proj_transverse = _dot(rel, _transverse_axis(face, frame))
        classified.append({**takeoff, 'face': face, 'proj_len': proj_len, 'proj_transverse': proj_transverse})
    return classified


def analyze_duct_pattern(duct: dict) -> dict:
    """
    한 덕트의 곁가지 분기점(takeoffs)들을 면별로 분류·정렬하여 간격 시퀀스, 등간격 여부,
    유틸리티 시퀀스를 산출합니다. (본선 양 끝단 연결점 'pocs'는 분석 대상이 아님 —
    classify_duct_takeoffs() docstring 참조)

    [인자 (Arguments)]
    - duct (dict): ExportDuctPlan.fetch_data()가 반환하는 덕트 1건의 딕셔너리
      (obb_3d, takeoffs, name, utility, utility_group, level, bay 포함)

    [반환값 (Returns)]
    - dict: TB_DUCT_POC_PATTERN 1행에 대응하는 구조체.
      {
        'duct_name', 'utility', 'utility_group', 'level', 'bay': 덕트 메타 정보,
        'n_poc_total': 전체 takeoff(곁가지 분기점) 개수,
        'dominant_face': takeoff 개수가 가장 많은 대표 면,
        'dominant_layout': dominant_face의 배치 형태(STRAIGHT/ZIGZAG/SPLIT_ROWS/
                            IRREGULAR/SINGLE/None),
        'faces': {face명: {count, utility_seq, spacing_mm, mean_spacing_mm,
                            spacing_cv, is_equal_spacing, layout, transverse_std_mm,
                            track_count, alternation_rate}, ...},
        'takeoff_layout_wkt': 이 덕트의 취출구 전체(면 분류와 무관하게) 3D 좌표를
                              MULTIPOINT Z WKT 문자열로 담은 것. 취출구가 없으면 None.
      }

    [주요 변수 및 동작 개요]
    - classified: classify_duct_takeoffs()가 반환한, 면 라벨이 붙은 takeoff 평탄 리스트
      (proj_len=길이축 위치, proj_transverse=횡방향 위치 포함)
    - faces (dict[str, list]): 면(face)별로 takeoff 딕셔너리(face/proj_len/proj_transverse
      포함)를 모아두는 중간 집계 딕셔너리
    - items: 한 면에 속한 takeoff들을 길이축 위치(proj_len) 오름차순으로 정렬한 리스트
    - positions: 정렬된 takeoff들의 길이축 위치 값만 뽑은 리스트
    - spacings: 인접 takeoff 간 간격(positions[i+1]-positions[i]) 리스트 (1개 이하면 빈 리스트)
    - mean_sp, variance, cv: 간격 리스트의 평균/분산/변동계수(CV=표준편차/평균).
      CV가 작을수록(EQUAL_SPACING_CV_THRESHOLD 이하) 실제 시공에서 "등간격으로 배치"됐다고
      판단할 수 있습니다.
    - layout_info: classify_layout_pattern()으로 얻은 이 면의 배치 형태(일직선/지그재그/
      분리형/불규칙) 판정 결과. spacing_cv(길이축 간격 규칙성)와는 독립적인 축(횡방향
      퍼짐/교대 패턴)을 보는 지표라, "등간격이면서 지그재그" 같은 조합도 표현 가능합니다.
    - dominant_face: 여러 면 중 takeoff 개수가 가장 많은 면 (한 덕트를 대표하는 면 라벨로 사용)
    """
    classified = classify_duct_takeoffs(duct)

    # 1단계: takeoff를 면별로 그룹핑
    faces: dict[str, list] = {}
    for p in classified:
        faces.setdefault(p['face'], []).append(p)

    # 2단계: 면별로 길이축 순 정렬 후 간격/등간격/유틸리티 시퀀스 계산
    face_pattern = {}
    for face, items in faces.items():
        items.sort(key=lambda p: p['proj_len'])
        positions = [p['proj_len'] for p in items]
        spacings = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]

        mean_sp = sum(spacings) / len(spacings) if spacings else 0.0
        if spacings and mean_sp > 1e-6:
            variance = sum((s - mean_sp) ** 2 for s in spacings) / len(spacings)
            cv = math.sqrt(variance) / mean_sp
        else:
            # PoC가 0~1개라 간격이 없는 경우: 비교 대상이 없으므로 "등간격"으로 간주(True)
            cv = 0.0

        layout_info = classify_layout_pattern([p['proj_transverse'] for p in items])

        face_pattern[face] = {
            'count': len(items),
            'utility_seq': [p['utility'] for p in items],
            'spacing_mm': [round(s, 1) for s in spacings],
            'mean_spacing_mm': round(mean_sp, 1),
            'spacing_cv': round(cv, 3),
            'is_equal_spacing': bool(cv <= EQUAL_SPACING_CV_THRESHOLD) if spacings else True,
            'layout': layout_info['layout'],
            'transverse_std_mm': layout_info['transverse_std'],
            'track_count': layout_info['track_count'],
            'alternation_rate': layout_info['alternation_rate'],
        }

    dominant_face = max(face_pattern.items(), key=lambda kv: kv[1]['count'])[0] if face_pattern else None
    dominant_layout = face_pattern[dominant_face]['layout'] if dominant_face else None

    return {
        'duct_name': duct['name'],
        'utility': duct['utility'],
        'utility_group': duct['utility_group'],
        'level': duct['level'],
        'bay': duct['bay'],
        'n_poc_total': sum(v['count'] for v in face_pattern.values()),
        'dominant_face': dominant_face,
        'dominant_layout': dominant_layout,
        'faces': face_pattern,
        'takeoff_layout_wkt': build_multipoint_z_wkt([(p['x'], p['y'], p['z']) for p in classified]),
    }


def analyze_all(ducts: list) -> list:
    """
    전체 덕트 목록을 순회하며 analyze_duct_pattern()을 적용합니다.

    [인자 (Arguments)]
    - ducts (list of dict): fetch_data()가 반환한 전체 덕트 목록

    [반환값 (Returns)]
    - list of dict: 덕트별 패턴 분석 결과 목록. takeoff가 아예 없는 덕트도 제외하지 않고
      포함시키되, analyze_duct_pattern()이 자연스럽게 dominant_face=None, faces={},
      n_poc_total=0인 "빈 패턴"을 반환하도록 둡니다. (이렇게 해야 이 덕트가 save_patterns()에서
      함께 UPSERT되어, 이전 실행에서 남은 낡은 패턴 데이터가 DB에 방치되지 않습니다 —
      과거에 이 필드가 duct['pocs'] 기반이었을 때는 모든 덕트가 END로 채워졌는데,
      취출구가 실제로 없는 덕트를 여기서 건너뛰면 그 낡은 END 값이 그대로 남아 오해를
      일으키는 문제가 있었습니다.)

    [주요 동작]
    - half-extent가 0인 퇴화 OBB 등 예외적인 기하 데이터로 인해 분석이 실패하면,
      전체 배치를 중단시키지 않고 해당 덕트만 건너뛰며 경고 로그를 남깁니다.
    """
    results = []
    for duct in ducts:
        try:
            results.append(analyze_duct_pattern(duct))
        except Exception as ex:
            print(f"[warn] Failed to analyze duct '{duct['name']}': {ex}")
    return results


def print_summary(patterns: list) -> None:
    """
    dry-run 검증용 요약 통계를 콘솔에 출력합니다.

    [인자 (Arguments)]
    - patterns (list of dict): analyze_all()의 결과 목록

    [주요 변수]
    - face_counts (dict): 면(face)별로 "그 면을 1개 이상 가진 덕트 수"를 집계
    - equal_spacing_counts (dict): 면별로 [등간격 판정된 덕트 수, PoC 2개 이상인 덕트 수]를
      집계 (PoC가 1개뿐이면 간격 자체가 없어 등간격 비율 계산에서 제외)
    - layout_counts (dict): 배치 형태(STRAIGHT/ZIGZAG/SPLIT_ROWS/IRREGULAR/SINGLE)별로
      "그 형태를 가진 면(Face)-덕트 조합 수"를 집계 (classify_layout_pattern() 결과)
    """
    if not patterns:
        print("No duct patterns to summarize.")
        return

    face_counts = {}
    equal_spacing_counts = {}
    layout_counts = {}
    for p in patterns:
        for face, stats in p['faces'].items():
            face_counts[face] = face_counts.get(face, 0) + 1
            if stats['count'] >= 2:
                equal_spacing_counts.setdefault(face, [0, 0])
                equal_spacing_counts[face][1] += 1
                if stats['is_equal_spacing']:
                    equal_spacing_counts[face][0] += 1
            layout_counts[stats['layout']] = layout_counts.get(stats['layout'], 0) + 1

    print(f"\n=== Duct PoC Pattern Summary ({len(patterns)} ducts) ===")
    for face in sorted(face_counts.keys()):
        n_ducts = face_counts[face]
        eq_n, eq_total = equal_spacing_counts.get(face, [0, 0])
        eq_str = f", equal-spacing {eq_n}/{eq_total} (>=2 PoC)" if eq_total else ""
        print(f"  {face:8s}: appears in {n_ducts} ducts{eq_str}")

    print("\n--- Layout distribution across all face-duct groups (STRAIGHT/ZIGZAG/SPLIT_ROWS/IRREGULAR/SINGLE) ---")
    for layout in sorted(layout_counts.keys()):
        print(f"  {layout:10s}: {layout_counts[layout]}")

    print("\n--- Sample (first 5) ---")
    for p in patterns[:5]:
        face_summary = ", ".join(f"{f}={s['count']}({s['layout']})" for f, s in p['faces'].items())
        print(f"  {p['duct_name']}: total={p['n_poc_total']}, dominant={p['dominant_face']}/{p['dominant_layout']}, [{face_summary}]")


def save_patterns(conn, patterns: list) -> None:
    """
    분석 결과를 TB_DUCT_POC_PATTERN 테이블에 UPSERT합니다.

    [인자 (Arguments)]
    - conn: psycopg2로 오픈된 PostgreSQL 커넥션 객체
    - patterns (list of dict): analyze_all()의 결과 목록

    [주요 동작]
    - DUCT_NAME(=INSTANCE_NAME 또는 폴백된 INSTANCE_ID)을 기본키로 하여, 이미 존재하는
      덕트는 ON CONFLICT DO UPDATE로 최신 분석 결과로 덮어쓰고, 없으면 새로 삽입합니다.
    - FACE_PATTERN_JSON은 면별 통계 딕셔너리를 psycopg2.extras.Json으로 감싸 jsonb 컬럼에
      그대로 저장합니다.
    - TAKEOFF_LAYOUT은 takeoff_layout_wkt(MULTIPOINT Z WKT 문자열, 취출구가 없으면 None)를
      ST_GeomFromText(%s, 0)로 감싸 geometry(MultiPointZ, 0) 컬럼에 저장합니다.
      ST_GeomFromText(NULL, 0)은 NULL을 반환하므로 취출구 없는 덕트도 별도 분기 없이
      동일한 SQL로 처리됩니다.
    """
    if not patterns:
        print("No patterns to save.")
        return

    sql = '''
        INSERT INTO "TB_DUCT_POC_PATTERN"
            ("DUCT_NAME", "UTILITY", "UTILITY_GROUP", "LEVEL", "BAY",
             "N_POC_TOTAL", "DOMINANT_FACE", "DOMINANT_LAYOUT", "FACE_PATTERN_JSON",
             "TAKEOFF_LAYOUT", "ANALYZED_AT")
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 0), now())
        ON CONFLICT ("DUCT_NAME") DO UPDATE SET
            "UTILITY" = EXCLUDED."UTILITY",
            "UTILITY_GROUP" = EXCLUDED."UTILITY_GROUP",
            "LEVEL" = EXCLUDED."LEVEL",
            "BAY" = EXCLUDED."BAY",
            "N_POC_TOTAL" = EXCLUDED."N_POC_TOTAL",
            "DOMINANT_FACE" = EXCLUDED."DOMINANT_FACE",
            "DOMINANT_LAYOUT" = EXCLUDED."DOMINANT_LAYOUT",
            "FACE_PATTERN_JSON" = EXCLUDED."FACE_PATTERN_JSON",
            "TAKEOFF_LAYOUT" = EXCLUDED."TAKEOFF_LAYOUT",
            "ANALYZED_AT" = now()
    '''
    with conn.cursor() as cur:
        for p in patterns:
            cur.execute(sql, (
                p['duct_name'], p['utility'], p['utility_group'], p['level'], p['bay'],
                p['n_poc_total'], p['dominant_face'], p['dominant_layout'], Json(p['faces']),
                p['takeoff_layout_wkt'],
            ))
    conn.commit()
    print(f"Saved {len(patterns)} duct patterns to TB_DUCT_POC_PATTERN.")


def create_schema(conn) -> None:
    """
    Tools/sql/create_duct_poc_pattern_table.sql과
    Tools/sql/create_duct_equipment_takeoff_pattern_table.sql의 DDL을 실행하여
    TB_DUCT_POC_PATTERN, TB_DUCT_EQUIPMENT_TAKEOFF_PATTERN 테이블(및 관련 인덱스)을
    생성합니다. 둘 다 CREATE TABLE IF NOT EXISTS 기반이라 이미 테이블이 있으면 아무
    영향 없이 통과합니다(멱등).
    """
    sql_dir = Path(__file__).resolve().parent / "sql"
    for sql_name, label in (
        ("create_duct_poc_pattern_table.sql", "TB_DUCT_POC_PATTERN"),
        ("create_duct_equipment_takeoff_pattern_table.sql", "TB_DUCT_EQUIPMENT_TAKEOFF_PATTERN"),
    ):
        sql_path = sql_dir / sql_name
        with conn.cursor() as cur:
            print(f"Executing DDL from: {sql_path}")
            cur.execute(sql_path.read_text(encoding="utf-8"))
        conn.commit()
        print(f"Schema ready: {label}")


def resolve_takeoff_equipment_map(conn) -> dict:
    """
    TB_ROUTE_PATH를 조회해 {취출구 PoC ID: EQUIPMENT_TAG} 매핑을 만듭니다.

    [배경 — 왜 정확 일치(TARGET_GUID)만 쓰는가, 2026-07-15 실측]
    덕트의 취출구가 어느 장비로 이어지는지는 TAKEOFF_POC_ID_LIST의 각 원소가
    TB_ROUTE_PATH.TARGET_GUID와 정확히 일치하는 경우에만 확인 가능합니다. 취출구 좌표와
    TB_ROUTE_PATH.TARGET_POSX/Y/Z 사이의 거리를 100mm까지 허용해 확장하는 방안도
    검토했으나, 정확 일치 105건 대비 겨우 3건(108건)만 늘었고 그마저 2개 이상의
    후보가 겹치는 모호한 경우가 4건 있었습니다. 나머지 대다수(1,193건 중 약 91%)는
    좌표가 살짝 어긋난 게 아니라 중간에 있어야 할 레터럴/부속 배관 자체가 이 DB에
    적재되지 않은 데이터 공백이라, 거리 허용으로는 해결되지 않습니다. 따라서 오탐(false
    positive) 위험이 있는 거리 기반 매칭은 채택하지 않고 정확 일치만 사용합니다.

    [반환값 (Returns)]
    - dict[str, str]: {TARGET_GUID(=취출구 PoC ID): EQUIPMENT_TAG}
    """
    mapping = {}
    with conn.cursor() as cur:
        cur.execute(
            'SELECT "TARGET_GUID", "EQUIPMENT_TAG" FROM "TB_ROUTE_PATH" '
            'WHERE "TARGET_GUID" IS NOT NULL AND "EQUIPMENT_TAG" IS NOT NULL'
        )
        for target_guid, equipment_tag in cur.fetchall():
            mapping[target_guid] = equipment_tag
    return mapping


def build_equipment_takeoff_patterns(ducts: list, equipment_map: dict) -> list:
    """
    장비(EQUIPMENT_TAG)까지 확인된 취출구만 모아, (EQUIPMENT_TAG, UTILITY)별로 어떤
    면(Face) 분포 패턴이 몇 개의 덕트에서 나타나는지 집계합니다.

    [인자 (Arguments)]
    - ducts (list of dict): fetch_data()의 원본 덕트 목록
    - equipment_map (dict): resolve_takeoff_equipment_map()의 결과

    [반환값 (Returns)]
    - list of dict: {'equipment_tag','utility','signature','n_ducts','n_takeoffs_total',
      'example_duct_names'} — (EQUIPMENT_TAG, UTILITY, PATTERN_SIGNATURE) 조합별 1행.
      n_ducts 내림차순으로 정렬되어 가장 흔한 패턴이 먼저 나옵니다.

    [주요 변수 및 동작 개요]
    - duct_equipment_face_points: 1단계 집계. 덕트 하나가 장비 하나에 여러 취출구를
      낼 수 있으므로, (덕트명, EQUIPMENT_TAG) 조합 단위로 장비가 확인된 취출구만 모아
      면(face)별로 (proj_len, proj_transverse) 좌표쌍을 쌓습니다. (덕트가 이론상 여러
      장비로 취출구를 나눠 낼 가능성을 배제하지 않기 위해 덕트 전체가 아니라 이
      서브그룹 단위로 시그니처를 만듭니다.)
    - signature: 면별로 "면:개수:배치형태"를 면 이름 알파벳 순으로 정렬 후 콤마로
      이어붙인 정규 문자열(예: "LEFT:1:SINGLE,TOP:2:ZIGZAG") — 개수만 같고 배치 형태가
      다르면(예: 일직선 TOP:3 vs 지그재그 TOP:3) 서로 다른 패턴으로 구분됩니다.
      classify_layout_pattern()으로 면별 배치 형태(일직선/지그재그/분리형/불규칙)를
      판정해 시그니처에 포함시킵니다.
    - pattern_rows: 2단계 집계. (EQUIPMENT_TAG, UTILITY, signature)별로 덕트 수·취출구
      총합·예시 덕트명(최대 5개)을 누적합니다.
    """
    # 1단계: 덕트별 · 장비별로 확인된 취출구만 모아 면별 (길이축, 횡방향) 좌표 수집
    duct_equipment_face_points = {}
    for duct in ducts:
        if not any(equipment_map.get(t.get('id')) for t in duct['takeoffs']):
            continue
        for takeoff in classify_duct_takeoffs(duct):
            equipment_tag = equipment_map.get(takeoff.get('id'))
            if not equipment_tag:
                continue

            key = (duct['name'], equipment_tag)
            entry = duct_equipment_face_points.setdefault(
                key, {'utility': duct['utility'], 'faces': {}, 'n_takeoffs': 0}
            )
            entry['faces'].setdefault(takeoff['face'], []).append(
                (takeoff['proj_len'], takeoff['proj_transverse'])
            )
            entry['n_takeoffs'] += 1

    # 2단계: (EQUIPMENT_TAG, UTILITY, signature)로 재집계
    pattern_rows = {}
    for (duct_name, equipment_tag), entry in duct_equipment_face_points.items():
        sig_parts = []
        for face, points in sorted(entry['faces'].items()):
            points_sorted = sorted(points, key=lambda pt: pt[0])  # 길이축 위치 순 정렬
            layout = classify_layout_pattern([pt[1] for pt in points_sorted])['layout']
            sig_parts.append(f"{face}:{len(points)}:{layout}")
        signature = ",".join(sig_parts)

        key = (equipment_tag, entry['utility'], signature)
        row = pattern_rows.setdefault(key, {
            'equipment_tag': equipment_tag,
            'utility': entry['utility'],
            'signature': signature,
            'n_ducts': 0,
            'n_takeoffs_total': 0,
            'example_duct_names': [],
        })
        row['n_ducts'] += 1
        row['n_takeoffs_total'] += entry['n_takeoffs']
        if len(row['example_duct_names']) < 5:
            row['example_duct_names'].append(duct_name)

    return sorted(pattern_rows.values(), key=lambda r: (-r['n_ducts'], r['equipment_tag'], r['utility']))


def print_equipment_pattern_summary(patterns: list) -> None:
    """(EQUIPMENT_TAG, UTILITY, PATTERN_SIGNATURE)별 집계 결과를 콘솔에 표로 출력합니다."""
    if not patterns:
        print("No equipment-linked takeoff patterns found (TARGET_GUID exact match yielded nothing).")
        return

    n_ducts_total = sum(p['n_ducts'] for p in patterns)
    print(f"\n=== Duct-Equipment Takeoff Pattern Summary ({len(patterns)} distinct patterns, {n_ducts_total} ducts) ===")
    print(f"  {'EQUIPMENT_TAG':22s} {'UTILITY':14s} {'SIGNATURE (face:count:layout)':34s} {'N_DUCTS':8s} N_TAKEOFFS")
    for p in patterns:
        print(f"  {p['equipment_tag']:22s} {p['utility']:14s} {p['signature']:34s} {p['n_ducts']:<8d} {p['n_takeoffs_total']}")


def save_equipment_patterns(conn, patterns: list) -> None:
    """
    (EQUIPMENT_TAG, UTILITY, PATTERN_SIGNATURE) 집계 결과를
    TB_DUCT_EQUIPMENT_TAKEOFF_PATTERN에 UPSERT합니다. 매 실행마다 그 시점의 DB 상태를
    그대로 반영해야 하므로, 저장 전에 기존 행을 전부 비우고(TRUNCATE) 새로 채웁니다
    (UNIQUE 키 upsert만으로는 이번 실행에서 사라진 조합 — 예: 이전엔 있었지만 지금은
    관측되지 않는 시그니처 — 이 남아있게 되는 문제를 피하기 위함).
    """
    if not patterns:
        print("No equipment-linked patterns to save.")
        return

    with conn.cursor() as cur:
        cur.execute('TRUNCATE TABLE "TB_DUCT_EQUIPMENT_TAKEOFF_PATTERN"')
        sql = '''
            INSERT INTO "TB_DUCT_EQUIPMENT_TAKEOFF_PATTERN"
                ("EQUIPMENT_TAG", "UTILITY", "PATTERN_SIGNATURE", "N_DUCTS",
                 "N_TAKEOFFS_TOTAL", "EXAMPLE_DUCT_NAMES", "ANALYZED_AT")
            VALUES (%s, %s, %s, %s, %s, %s, now())
        '''
        for p in patterns:
            cur.execute(sql, (
                p['equipment_tag'], p['utility'], p['signature'], p['n_ducts'],
                p['n_takeoffs_total'], Json(p['example_duct_names']),
            ))
    conn.commit()
    print(f"Saved {len(patterns)} equipment takeoff patterns to TB_DUCT_EQUIPMENT_TAKEOFF_PATTERN.")


def export_face_pattern_png(ducts: list, out_path: str) -> None:
    """
    덕트 풋프린트와 takeoff(곁가지 분기점)를 면(Face) 판정 결과에 따라 색상 구분하여
    PNG로 시각화합니다. ExportDuctPlan.export_png()와 동일한 도면 규격(15x15인치,
    300dpi, XY 1:1 축척)을 쓰되, 점 색상 기준이 "유틸리티 종류"가 아니라 "어느 면에
    붙어있는가(TOP/BOTTOM/LEFT/RIGHT/END)"라는 점이 다릅니다. TOP/LEFT/RIGHT처럼
    END가 아닌 색이 많이 보이면 실제로 곁가지 분기가 있는 덕트라는 뜻이므로, 육안으로
    곁가지 존재 여부를 빠르게 스캔하는 용도로 사용합니다. (본선 양 끝단 'pocs'는
    표시 대상이 아님 — classify_duct_takeoffs() 참조)

    [인자 (Arguments)]
    - ducts (list of dict): fetch_data()의 원본 덕트 목록 (poly, obb_3d, takeoffs 필요)
    - out_path (str): 저장할 PNG 파일 경로

    [주요 변수]
    - drawn_faces (set): 범례에 표시하기 위해 실제로 화면에 그려진 면 라벨을 누적하는 집합
    - classify_duct_takeoffs(duct): 덕트별로 takeoff에 face 라벨을 붙인 리스트를 얻어
      색상 결정에 사용 (analyze_duct_pattern()과 동일한 판정 로직을 재사용하므로
      통계와 그림이 항상 일치)
    """
    fig, ax = plt.subplots(figsize=(15, 15))
    ax.set_aspect('equal')  # X축과 Y축의 물리적 축척 비율을 1:1로 강제 고정

    drawn_faces = set()

    for duct in ducts:
        if not duct['takeoffs']:
            continue

        # 덕트 바닥 다각형 패치 (유틸리티 시각화와 동일한 비스크 황갈색, 구분을 위해 옅게)
        poly = Polygon(duct['poly'], closed=True, facecolor='#FFE4C4', edgecolor='black', alpha=0.4, zorder=1)
        ax.add_patch(poly)

        # takeoff를 면(Face) 판정 색상으로 그리기
        for p in classify_duct_takeoffs(duct):
            color = FACE_COLORS.get(p['face'], '#000000')
            drawn_faces.add(p['face'])
            circ = Circle((p['x'], p['y']), p['radius'], facecolor=color, edgecolor='black', zorder=2)
            ax.add_patch(circ)

    ax.autoscale_view()

    # 실제로 그려진 면 라벨만 범례에 표시
    legend_patches = [
        mpatches.Patch(color=FACE_COLORS.get(f, '#000000'), label=f'Face: {f}')
        for f in sorted(drawn_faces)
    ]
    if legend_patches:
        ax.legend(handles=legend_patches, loc='upper right')

    plt.title('Duct PoC Face Pattern (TOP / BOTTOM / LEFT / RIGHT / END)')
    plt.xlabel('X (mm)')
    plt.ylabel('Y (mm)')
    plt.grid(True, linestyle='--', alpha=0.6)

    plt.savefig(out_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Face pattern PNG saved to {out_path}")


def export_duct_face_pattern_pngs(ducts: list, out_dir) -> None:
    """
    덕트 1개당 PNG 이미지 1장씩을 생성해 out_dir에 저장합니다. export_face_pattern_png()는
    전체 플랜트를 하나의 이미지에 몰아 그려 개별 취출구(반경 50~150mm)가 전체 스케일
    (예: X 0~440,000mm) 대비 1~3픽셀에 불과해 면 색상이 검은 테두리에 묻히는 한계가
    있는데, 이 함수는 덕트마다 그 덕트 하나의 바운딩 박스에 맞춰 확대한 개별 이미지를
    만들어 그 문제를 해결합니다.

    [인자 (Arguments)]
    - ducts (list of dict): fetch_data()의 원본 덕트 목록 (poly, obb_3d, takeoffs, name,
      utility 필요)
    - out_dir (str | Path): 저장할 디렉토리. 없으면 자동 생성됩니다.

    [주요 변수 및 동작 개요]
    - 덕트마다 독립된 Figure를 새로 만들어 그 덕트의 풋프린트(poly)와 takeoff만 그리므로,
      ax.autoscale_view() + ax.margins()가 자동으로 그 덕트 하나에 맞는 확대 축척을 잡습니다.
    - drawn_faces: 해당 덕트에서 실제로 나타난 면 라벨만 범례에 표시하기 위한 집합
    - safe_name: 파일명 충돌/오류 방지를 위해 duct['name'](보통 GUID)에서 경로 구분자만 치환
    - takeoff가 0개인 덕트도 건너뛰지 않고 풋프린트만 있는 빈 이미지를 저장합니다
      (덕트 1개당 이미지 1장이라는 1:1 대응을 깨지 않기 위함 — 몇 개가 비어있는지도
      파일 개수로 바로 파악할 수 있게 됩니다).
    """
    os.makedirs(out_dir, exist_ok=True)

    for duct in ducts:
        classified = classify_duct_takeoffs(duct)

        fig, ax = plt.subplots(figsize=(6, 6))
        ax.set_aspect('equal')

        poly = Polygon(duct['poly'], closed=True, facecolor='#FFE4C4', edgecolor='black', alpha=0.5, zorder=1)
        ax.add_patch(poly)

        drawn_faces = set()
        for p in classified:
            color = FACE_COLORS.get(p['face'], '#000000')
            drawn_faces.add(p['face'])
            # 원 크기는 실제 취출구 반경(p['radius'])을 그대로 사용한다. 다만 matplotlib
            # 패치의 테두리 두께는 데이터 좌표가 아니라 포인트(pt) 단위라서, 반경이 작으면
            # (주로 25~75mm) 검은 테두리가 채움색보다 시각적으로 두드러져 보일 수 있으므로
            # 테두리만 얇게(0.6pt) 낮춰 실제 크기를 왜곡하지 않으면서 색상 판독성을 높인다.
            circ = Circle((p['x'], p['y']), p['radius'], facecolor=color, edgecolor='black', linewidth=0.6, zorder=2)
            ax.add_patch(circ)

        ax.autoscale_view()
        ax.margins(0.15)  # 도형이 화면 가장자리에 딱 붙어 잘려 보이지 않도록 여백 확보

        legend_patches = [
            mpatches.Patch(color=FACE_COLORS.get(f, '#000000'), label=f'Face: {f}')
            for f in sorted(drawn_faces)
        ]
        if legend_patches:
            ax.legend(handles=legend_patches, loc='upper right', fontsize=8)

        ax.set_title(f"{duct['name']}\n({duct['utility']}, takeoffs={len(classified)})", fontsize=10)
        ax.set_xlabel('X (mm)')
        ax.set_ylabel('Y (mm)')
        ax.grid(True, linestyle='--', alpha=0.5)

        safe_name = duct['name'].replace('/', '_').replace('\\', '_')
        out_path = os.path.join(str(out_dir), f"{safe_name}.png")
        plt.savefig(out_path, dpi=150, bbox_inches='tight')
        plt.close(fig)

    print(f"Saved {len(ducts)} per-duct face pattern PNGs to {out_dir}")


def main() -> int:
    """
    CLI 진입점: create-schema / analyze / run-all 서브커맨드를 파싱해 해당 처리로 위임합니다.

    [서브커맨드]
    - create-schema: TB_DUCT_POC_PATTERN 스키마만 생성
    - analyze [--dry-run]: 덕트 데이터를 조회·분석하여 요약을 출력. --dry-run이 없으면
      결과를 DB에 저장(스키마가 없으면 INSERT 시 오류가 날 수 있으므로 최초 1회는
      create-schema 또는 run-all을 먼저 실행해야 함)
    - run-all [--dry-run]: 스키마 생성 + 분석을 한 번에 수행. --dry-run이면 분석/요약까지만
      하고 DB 저장은 건너뜀

    [주요 흐름]
    1. tool_config로 DB 접속 정보(runtime.conninfo) 및 출력 디렉토리 설정 해석
    2. psycopg2로 DB 연결 (실패 시 SystemExit로 즉시 종료)
    3. fetch_data() 재사용 → analyze_all() → print_summary() → (dry-run이 아니면) save_patterns()
    """
    parser = argparse.ArgumentParser(description="Duct PoC Distribution Pattern Analyzer")
    tool_config.add_common_args(parser)

    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    subparsers.add_parser("create-schema", help="Create TB_DUCT_POC_PATTERN schema")

    analyze_parser = subparsers.add_parser("analyze", help="Analyze duct PoC face/spacing patterns")
    analyze_parser.add_argument("--dry-run", action="store_true", help="Print summary without saving to DB")

    run_all_parser = subparsers.add_parser("run-all", help="Create schema and analyze patterns")
    run_all_parser.add_argument("--dry-run", action="store_true", help="Print summary without saving to DB")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    runtime = tool_config.resolve_runtime(args)
    tool_config.print_runtime(runtime)

    try:
        conn = psycopg2.connect(runtime.conninfo)
    except Exception as ex:
        raise SystemExit(f"DB connection failed: {ex}")

    try:
        if args.command == "create-schema":
            create_schema(conn)
        elif args.command in ("analyze", "run-all"):
            if args.command == "run-all":
                create_schema(conn)
            print("Fetching duct data...")
            ducts = fetch_data(conn)
            print(f"Loaded {len(ducts)} ducts.")
            patterns = analyze_all(ducts)
            print_summary(patterns)

            # PNG 시각화는 로컬 파일 저장일 뿐 DB에 영향을 주지 않으므로,
            # dry-run 여부와 무관하게 항상 생성한다 (ExportDuctPlan.py와 동일한 관례).
            png_path = os.path.join(runtime.out_dir, "duct_poc_face_pattern.png")
            export_face_pattern_png(ducts, png_path)
            export_duct_face_pattern_pngs(ducts, PER_DUCT_PNG_DIR)

            if not args.dry_run:
                save_patterns(conn, patterns)
            else:
                print("\n[dry-run] Skipped DB save.")

            # 취출구 -> 장비(EQUIPMENT_TAG) 귀속 및 (장비, 유틸리티)별 패턴 집계.
            # TB_ROUTE_PATH.TARGET_GUID 정확 일치만 사용하므로 커버리지가 낮을 수 있음
            # (resolve_takeoff_equipment_map() 참조).
            print("\nResolving takeoff -> equipment linkage (TB_ROUTE_PATH.TARGET_GUID exact match)...")
            equipment_map = resolve_takeoff_equipment_map(conn)
            equipment_patterns = build_equipment_takeoff_patterns(ducts, equipment_map)
            print_equipment_pattern_summary(equipment_patterns)
            if not args.dry_run:
                save_equipment_patterns(conn, equipment_patterns)
            else:
                print("\n[dry-run] Skipped DB save (equipment takeoff patterns).")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
