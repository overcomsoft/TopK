"""
================================================================================
config.py  ─  RubberBandRouter PoC  전역 설정 및 파라미터 중앙화 모듈
================================================================================

【실행 명령어】
  ※ 이 파일은 직접 실행하지 않으며, 모든 모듈에서 import 하여 사용한다.
  ※ DB 연결 정보 설정 우선순위:
      1순위) 환경변수      : TOPKGEN_CONN_STR  (전체 conninfo 문자열)
      2순위) settings.json : tools.settings.json 의 "conn_str" 또는 "db" 객체
      3순위) 기본값        : localhost:5432 / DDW_AI_DB / postgres

  # 환경변수로 DB 연결 설정 예시 (PowerShell)
  $env:TOPKGEN_DB_HOST     = "192.168.0.46"
  $env:TOPKGEN_DB_PORT     = "5432"
  $env:TOPKGEN_DB_NAME     = "DDW_AI_DB"
  $env:TOPKGEN_DB_USER     = "postgres"
  $env:TOPKGEN_DB_PASSWORD = "dinno"

  # settings.json 으로 설정 예시 (TopKGen/tools.settings.json)
  {
      "db": {
          "host": "192.168.0.46",
          "port": 5432,
          "dbname": "DDW_AI_DB",
          "user": "postgres",
          "password": "dinno"
      }
  }

================================================================================
【전체 파이프라인 흐름도】

  ┌─────────────────────────────────────────────────────────────┐
  │  PostgreSQL DB (DDW_AI_DB)                                  │
  │  TB_SPACE_GROUP_INFO / TB_BIM_OBSTACLE / TB_EQUIPMENTS      │
  │  TB_POCINSTANCES / TB_ROUTE_PATH                            │
  └───────────────┬─────────────────────────────────────────────┘
                  │ data_loader.py
                  ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  RoutingScene                                               │
  │  ├─ obstacles[]    장애물 AABB 목록                         │
  │  ├─ equipment[]    장비 박스 목록                           │
  │  ├─ equipment_pocs[] 장비 PoC 좌표 (출발점 후보)           │
  │  ├─ duct_pocs[]    덕트/레터럴 PoC 좌표 (목적지 후보)      │
  │  └─ tasks[]        라우팅 작업 (Source→Target 쌍)          │
  └───────────────┬─────────────────────────────────────────────┘
                  │ obstacle_map.py
                  ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  ObstacleMap                                                │
  │  ├─ obstacles[]      OBBObstacle 목록                       │
  │  └─ density_tensor   3D 이진 행렬 (30×30×30, 1m 해상도)    │
  └─────────────────────────────────────────────────────────────┘
                  │
       ┌──────────┴──────────┐
       │ topology_matcher.py │  (레거시 맵 있을 때)
       └──────────┬──────────┘
                  │ 코사인 유사도 + OBB 정밀 매칭
                  ▼ Case A / B / C 분류
  ┌─────────────────────────────────────────────────────────────┐
  │  feature_extractor.py                                       │
  │  FeatureSet : 꺾임 웨이포인트 목록 (레거시 정규화 좌표)    │
  └───────────────┬─────────────────────────────────────────────┘
                  │ rubber_band.py
                  ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  Step 1 : 초기 인장 (S→D 직선 직교 분해)        🟡         │
  │  Step 2 : Pull — 특징점 스냅 (AI 웨이포인트 흡착) 🔵        │
  │  Step 3 : Push — SAT 충돌 검사 + 3대 회피 전략   🔴        │
  └───────────────┬─────────────────────────────────────────────┘
                  │ pipe_distributor.py
                  ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  DistributionResult                                         │
  │  ├─ tray_centerline   트레이 중심선 좌표 목록               │
  │  └─ pipes[]           개별 배관 3D 좌표 세트                │
  └───────────────┬─────────────────────────────────────────────┘
                  │ debugger/timeline_viewer.py
                  ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  Plotly Dash 4단계 타임라인 슬라이더 디버거                 │
  │  → http://localhost:8050                                    │
  └─────────────────────────────────────────────────────────────┘

================================================================================
【이 파일이 제공하는 것】
  - 경로 상수       : PROJECT_ROOT, ROUTER_ROOT, DATA_DIR, LEGACY_MAP_DIR, RESULTS_DIR
  - DB 연결         : get_conninfo() → psycopg2 conninfo 문자열
  - 공간 파라미터   : SPACE_MAX, GRID_SIZE, GRID_DIM
  - 라우팅 파라미터 : MAX_VERTICAL_BENDS, SAFETY_MARGIN, SNAP_TOLERANCE
  - 위상 매칭 임계값: TOPOLOGY_CASE_A_THRESHOLD, TOPOLOGY_CASE_B_THRESHOLD, TOP_K_LEGACY_CANDIDATES
  - 트레이 설정     : TRAY_WIDTH, TRAY_HEIGHT, PIPE_PITCH, PIPE_COUNT
  - DB 테이블명     : TABLE_EQUIPMENT, TABLE_DUCT, TABLE_GROUP_SEGMENTS 등
  - 시각화 설정     : VIZ_PORT, VIZ_DEBUG, 단계별 색상 상수
================================================================================
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# 【경로 상수】
#   - PROJECT_ROOT  : TopKGen/ 최상위 디렉토리 (이 파일의 2단계 상위)
#   - ROUTER_ROOT   : RubberBandRouter/ 디렉토리 (이 파일이 위치한 곳)
#   - DATA_DIR      : 데이터 캐시 및 결과 저장 디렉토리
#   - LEGACY_MAP_DIR: 과거 장애물 맵 pickle 캐시 디렉토리
#   - RESULTS_DIR   : 라우팅 결과 JSON 저장 디렉토리
# ─────────────────────────────────────────────────────────────────────────────
PROJECT_ROOT   = Path(__file__).resolve().parents[1]   # TopKGen/
ROUTER_ROOT    = Path(__file__).resolve().parent        # RubberBandRouter/
DATA_DIR       = ROUTER_ROOT / "data"
LEGACY_MAP_DIR = DATA_DIR / "legacy_maps"              # 과거 맵 pickle 캐시
RESULTS_DIR    = DATA_DIR / "results"                  # 라우팅 결과 JSON

# 디렉토리 자동 생성
DATA_DIR.mkdir(parents=True, exist_ok=True)
LEGACY_MAP_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# 【DB 연결 설정】
#
# 설정 우선순위:
#   1) 환경변수 TOPKGEN_CONN_STR  (전체 conninfo 문자열, 최우선)
#   2) tools.settings.json 의 "conn_str" 또는 "connStr"
#   3) 개별 환경변수 (TOPKGEN_DB_HOST, TOPKGEN_DB_PORT, TOPKGEN_DB_NAME,
#                    TOPKGEN_DB_USER, TOPKGEN_DB_PASSWORD)
#   4) tools.settings.json 의 "db" 객체 내 host/port/dbname/user/password
#   5) 기본값: localhost:5432/DDW_AI_DB/postgres
#
# 검색 파일 위치:
#   - TopKGen/tools.settings.json
#   - TopKGen/Tools/tools.settings.json
#   - RubberBandRouter/settings.json
# ─────────────────────────────────────────────────────────────────────────────

def _load_settings() -> dict[str, Any]:
    """
    tools.settings.json 을 로드한다. 여러 후보 경로를 순서대로 탐색한다.

    반환값:
        dict: JSON 설정 딕셔너리 (없으면 빈 dict)
    """
    candidates = [
        PROJECT_ROOT / "tools.settings.json",
        PROJECT_ROOT / "Tools" / "tools.settings.json",
        ROUTER_ROOT / "settings.json",
    ]
    for p in candidates:
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)
    return {}


def _build_conninfo(**kw: Any) -> str:
    """
    키워드 인수들을 psycopg2 conninfo 문자열로 조합한다.

    예) host='localhost' port='5432' dbname='DDW_AI_DB' user='postgres' password=''

    인수:
        **kw: host, port, dbname, user, password 등
    반환값:
        str: "key='value' key='value' ..." 형식의 conninfo 문자열
    """
    parts = []
    for k, v in kw.items():
        # 백슬래시·따옴표 이스케이프 처리
        text = str(v).replace("\\", "\\\\").replace("'", "\\'")
        parts.append(f"{k}='{text}'")
    return " ".join(parts)


def _first(*values: Any) -> Any:
    """
    인수 중 None·빈 문자열이 아닌 첫 번째 값을 반환한다.
    설정 우선순위 체인(환경변수 → 파일 → 기본값)을 구현하는 헬퍼.
    """
    for v in values:
        if v is not None and v != "":
            return v
    return None


def get_conninfo() -> str:
    """
    psycopg2 연결에 사용할 conninfo 문자열을 반환한다.

    【우선순위 체인】
      TOPKGEN_CONN_STR 환경변수
       → tools.settings.json conn_str / connStr
       → 개별 환경변수(TOPKGEN_DB_HOST 등) + settings.json db 객체
       → 기본값 (localhost:5432/DDW_AI_DB/postgres)

    반환값:
        str: psycopg2.connect() 에 전달할 conninfo 문자열
    """
    cfg = _load_settings()
    db = cfg.get("db", {})

    # 1) 전체 conninfo 문자열이 있으면 바로 반환
    raw = (
        os.getenv("TOPKGEN_CONN_STR")
        or cfg.get("conn_str")
        or cfg.get("connStr")
    )
    if raw:
        return raw

    # 2) 개별 항목 조합
    host     = _first(os.getenv("TOPKGEN_DB_HOST"), db.get("host"), "localhost")
    port     = _first(os.getenv("TOPKGEN_DB_PORT"), db.get("port"), 5432)
    dbname   = _first(os.getenv("TOPKGEN_DB_NAME"), db.get("dbname"), db.get("database"), "DDW_AI_DB")
    user     = _first(os.getenv("TOPKGEN_DB_USER"), db.get("user"), "postgres")
    password = _first(os.getenv("TOPKGEN_DB_PASSWORD"), db.get("password"), "")
    return _build_conninfo(host=host, port=port, dbname=dbname, user=user, password=password)


# ─────────────────────────────────────────────────────────────────────────────
# 【공간 스케일 & 그리드 파라미터】
#
# SPACE_MAX  : 단일 축 최대 좌표 (mm). 플랜트/건축 공간 최대 범위 = 30,000mm (30m)
# GRID_SIZE  : 밀도 텐서 복셀 크기 (mm). 1,000mm = 1m 해상도로 공간 분할
# GRID_DIM   : 텐서 한 변의 셀 수 = SPACE_MAX / GRID_SIZE = 30
#
# 예시: 10m × 10m × 10m 장애물 → 10×10×10 셀이 1로 채워짐
#        코사인 유사도 계산 시 30×30×30 = 27,000개 셀 비교
# ─────────────────────────────────────────────────────────────────────────────
SPACE_MAX: int = 30_000                        # 단일 축 최대 좌표 (mm)
GRID_SIZE: int = 1_000                         # 밀도 텐서 복셀 크기 (mm = 1m)
GRID_DIM:  int = SPACE_MAX // GRID_SIZE        # 텐서 차원 = 30


# ─────────────────────────────────────────────────────────────────────────────
# 【라우팅 알고리즘 핵심 파라미터】
#
# MAX_VERTICAL_BENDS : 수직(Z축) 꺾임 최대 허용 횟수
#   - 배관 설계 품질 제약 조건. 5회 초과 시 공정 낭비 및 시공 불편 발생
#   - rubber_band.py 의 step3_push_resolve() 에서 카운터로 관리
#   - 2순위 회피전략(수직 오버/언더패스)은 잔여 횟수 2회 이상 필요
#
# SAFETY_MARGIN : OBB 장애물 우회 시 트레이 외면과 장애물 사이 최소 간격 (mm)
#   - 물리적 시공 여유값. 너무 작으면 실제 배관이 장애물에 인접
#   - collision.py 의 모든 회피 전략에서 margin = TRAY_WIDTH/2 + SAFETY_MARGIN 로 사용
#
# SNAP_TOLERANCE : 특징점 스냅 허용 거리 (mm)
#   - 웨이포인트가 세그먼트에서 이 거리 이내일 때만 스냅 적용
# ─────────────────────────────────────────────────────────────────────────────
MAX_VERTICAL_BENDS: int   = 5       # 수직(Z) 꺾임 최대 허용 횟수
SAFETY_MARGIN:      float = 50.0    # OBB 우회 시 최소 안전 마진 (mm)
SNAP_TOLERANCE:     float = 100.0   # 특징점 스냅 허용 거리 (mm)


# ─────────────────────────────────────────────────────────────────────────────
# 【위상 매칭(Topology Matching) 임계값】
#
# 현재 맵 vs 레거시 맵의 코사인 유사도 + OBB 가중 결합 점수(combined_score)로
# 레거시 데이터 활용 전략(Case)을 결정한다.
#
# Case A (≥ 0.90) — 완전 일치형
#   → 레거시 엘보 좌표 전체를 현재 공간에 정규화 투영하여 주입
#   → 가장 정확한 가이드, 실제 배관 설계자 동선 그대로 재현
#
# Case B (0.60 ~ 0.90) — 부분 변동형
#   → 파이프 랙 고도(Z-Level)와 대형 장비 우회 시작/끝점만 추출
#   → 소형 간섭물 변동은 무시, 거시적 동선만 참조
#
# Case C (< 0.60) — 판이한 환경
#   → 레거시 데이터 사용 안 함, 순수 기하학 기반 자율 라우팅
#
# TOP_K_LEGACY_CANDIDATES : 코사인 유사도 1차 필터 후 OBB 정밀 매칭에 넘길 후보 수
# ─────────────────────────────────────────────────────────────────────────────
TOPOLOGY_CASE_A_THRESHOLD: float = 0.90   # Case A 하한 (완전 일치)
TOPOLOGY_CASE_B_THRESHOLD: float = 0.60   # Case B 하한 (부분 변동)
TOP_K_LEGACY_CANDIDATES:   int   = 5      # 1차 필터 후 OBB 정밀 매칭 후보 수


# ─────────────────────────────────────────────────────────────────────────────
# 【배관 트레이 파라미터】
#
# 복수의 배관을 하나의 직사각형 단면 '트레이'로 묶어 라우팅하고,
# 최종 단계에서 개별 배관으로 평행 분배한다.
#
# TRAY_WIDTH  : 트레이 전체 폭 (mm). 충돌 검사에서 margin = TRAY_WIDTH/2 + SAFETY_MARGIN
# TRAY_HEIGHT : 트레이 높이 (mm). 수직 오버/언더패스 시 z_target 계산에 사용
# PIPE_PITCH  : 개별 배관 간격 (mm). 오프셋 = (index - (n-1)/2) × PIPE_PITCH
# PIPE_COUNT  : 동시 라우팅 배관 수. 분배 결과 배열 크기
#
# 예시: PIPE_COUNT=4, PITCH=100 → 오프셋 = [-150, -50, +50, +150] mm
# ─────────────────────────────────────────────────────────────────────────────
TRAY_WIDTH:  float = 600.0   # 트레이 폭 (mm)
TRAY_HEIGHT: float = 100.0   # 트레이 높이 (mm)
PIPE_PITCH:  float = 100.0   # 개별 배관 간격 (mm)
PIPE_COUNT:  int   = 6       # 동시 라우팅 배관 수


# ─────────────────────────────────────────────────────────────────────────────
# 【DB 테이블 및 필드 이름】 (TopKGen/obstacle_map.py 용 — 레거시 OBB 기반)
#
# 주의: data_loader.py 는 DDW_AI_DB 신 스키마(TB_BIM_OBSTACLE, TB_EQUIPMENTS 등)를 사용.
#       아래는 obstacle_map.py 가 구 스키마(TB_EQUIPMENT, TB_DUCT)를 참조할 때의 상수.
# ─────────────────────────────────────────────────────────────────────────────
TABLE_EQUIPMENT      = "TB_EQUIPMENT"           # 장비 OBB (구 스키마)
TABLE_PIPE_RACK      = "TB_PIPE_RACK"            # 파이프 랙 (구 스키마)
TABLE_DUCT           = "TB_DUCT"                 # 덕트 (구 스키마)

# OBB 8 꼭짓점 필드 접두사 (각 꼭짓점마다 _X, _Y, _Z 컬럼 존재 → 총 24개 필드)
OBB_VERTEX_PREFIXES = [
    "OBB_LEFT_BOTTOM_BACK",   "OBB_RIGHT_BOTTOM_BACK",
    "OBB_LEFT_TOP_BACK",      "OBB_RIGHT_TOP_BACK",
    "OBB_LEFT_BOTTOM_FRONT",  "OBB_RIGHT_BOTTOM_FRONT",
    "OBB_LEFT_TOP_FRONT",     "OBB_RIGHT_TOP_FRONT",
]

# 슬리브(관통 허용) 판별 필드
# IS_PENETRATION = 'true' 이면 SAT 충돌 검사에서 제외하고 경로 통과 허용
PENETRATION_FIELD    = "IS_PENETRATION"

# 레거시 설계 배관 세그먼트 테이블 (엘보 꺾임 패턴 추출 용)
TABLE_GROUP_SEGMENTS = "TB_GROUP_SEGMENTS"
TABLE_ROUTE_NODES    = "TB_ROUTE_NODES"

# Pass-through(경로 통과 허용) 객체 유형 집합
# → obstacle_map.py 의 OBB 등록 및 충돌 검사에서 제외
PASSTHROUGH_TYPES: set[str] = {"GRATING", "FLOOR", "GRID_BEAM", "HANDRAIL"}


# ─────────────────────────────────────────────────────────────────────────────
# 【시각화 설정】
#
# VIZ_PORT  : Plotly Dash 웹 서버 포트 (기본 8050)
#             실행 후 브라우저에서 http://localhost:8050 접속
# VIZ_DEBUG : Dash 디버그 모드 (True = 코드 변경 시 자동 새로고침)
#
# 단계별 색상 (Plotly CSS hex 코드):
#   COLOR_STEP1_TENSION  🟡 황색   — Step 1: 초기 인장 직선
#   COLOR_STEP2_SNAP     🔵 파란색  — Step 2: AI 특징점 스냅 경로
#   COLOR_STEP3_COLLISION🔴 붉은색  — Step 3: 충돌 노드 마커
#   COLOR_OBSTACLE       ⚪ 회색    — OBB 장애물 반투명 박스
#   COLOR_PIPES[]        멀티컬러   — Step 4: 개별 배관 분배선
# ─────────────────────────────────────────────────────────────────────────────
VIZ_PORT:  int  = 8050    # Plotly Dash 서버 포트
VIZ_DEBUG: bool = True    # Dash 디버그 모드

# 단계별 색상 (Plotly CSS 색상명 또는 hex)
COLOR_STEP1_TENSION   = "#FFD700"   # 🟡 황색   — 초기 인장 직선
COLOR_STEP2_SNAP      = "#4B9FFF"   # 🔵 파란색  — AI 특징점 스냅
COLOR_STEP3_COLLISION = "#FF4444"   # 🔴 붉은색  — 충돌 노드
COLOR_OBSTACLE        = "#AAAAAA"   # ⚪ 회색    — OBB 장애물
COLOR_PIPES = [
    "#FF69B4",   # 💗 분홍  — PIPE-01
    "#48D1CC",   # 🩵 민트  — PIPE-02
    "#ADFF2F",   # 💛 연두  — PIPE-03
    "#FFA500",   # 주황     — PIPE-04
    "#DA70D6",   # 보라     — PIPE-05
    "#00CED1",   # 청록     — PIPE-06
]
