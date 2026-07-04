"""
config.py
---------
RubberBandRouter PoC - 전역 설정 및 파라미터 중앙화 모듈

모든 알고리즘 파라미터, DB 연결 정보, 트레이 설정을 이곳에서 관리한다.
환경변수 또는 tools.settings.json 으로 오버라이드 가능.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────
# 경로
# ─────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]          # TopKGen/
ROUTER_ROOT = Path(__file__).resolve().parent               # RubberBandRouter/
DATA_DIR = ROUTER_ROOT / "data"
LEGACY_MAP_DIR = DATA_DIR / "legacy_maps"
RESULTS_DIR = DATA_DIR / "results"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LEGACY_MAP_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# DB 연결 설정 (기존 Tools/tool_config.py 패턴 답습)
# ─────────────────────────────────────────────
def _load_settings() -> dict[str, Any]:
    """tools.settings.json 로드 (프로젝트 루트 또는 Tools/ 디렉토리)."""
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
    parts = []
    for k, v in kw.items():
        text = str(v).replace("\\", "\\\\").replace("'", "\\'")
        parts.append(f"{k}='{text}'")
    return " ".join(parts)


def _first(*values: Any) -> Any:
    for v in values:
        if v is not None and v != "":
            return v
    return None


def get_conninfo() -> str:
    """psycopg2 conninfo 문자열 반환."""
    cfg = _load_settings()
    db = cfg.get("db", {})
    raw = (
        os.getenv("TOPKGEN_CONN_STR")
        or cfg.get("conn_str")
        or cfg.get("connStr")
    )
    if raw:
        return raw
    host     = _first(os.getenv("TOPKGEN_DB_HOST"), db.get("host"), "localhost")
    port     = _first(os.getenv("TOPKGEN_DB_PORT"), db.get("port"), 5432)
    dbname   = _first(os.getenv("TOPKGEN_DB_NAME"), db.get("dbname"), db.get("database"), "DDW_AI_DB")
    user     = _first(os.getenv("TOPKGEN_DB_USER"), db.get("user"), "postgres")
    password = _first(os.getenv("TOPKGEN_DB_PASSWORD"), db.get("password"), "")
    return _build_conninfo(host=host, port=port, dbname=dbname, user=user, password=password)


# ─────────────────────────────────────────────
# 공간 스케일 & 그리드
# ─────────────────────────────────────────────
SPACE_MAX: int = 30_000          # 단일 축 최대 좌표 (mm)
GRID_SIZE: int = 1_000           # 밀도 텐서 복셀 크기 (mm = 1m)
GRID_DIM: int = SPACE_MAX // GRID_SIZE  # 텐서 차원 (30)

# ─────────────────────────────────────────────
# 라우팅 알고리즘 파라미터
# ─────────────────────────────────────────────
MAX_VERTICAL_BENDS: int = 5      # 수직(Z) 꺾임 최대 허용 횟수
SAFETY_MARGIN: float = 50.0      # OBB 우회 시 최소 안전 마진 (mm)
SNAP_TOLERANCE: float = 100.0    # 특징점 스냅 허용 거리 (mm)

# ─────────────────────────────────────────────
# 위상 매칭 임계값
# ─────────────────────────────────────────────
TOPOLOGY_CASE_A_THRESHOLD: float = 0.90   # Case A (완전 일치)
TOPOLOGY_CASE_B_THRESHOLD: float = 0.60   # Case B (부분 변동) / 미만은 Case C
TOP_K_LEGACY_CANDIDATES: int = 5          # 1차 필터링 후 보관할 레거시 맵 후보 수

# ─────────────────────────────────────────────
# 배관 트레이 파라미터 (DB에서 읽어오지 않을 경우 기본값)
# ─────────────────────────────────────────────
TRAY_WIDTH: float = 600.0        # 트레이 폭 (mm)
TRAY_HEIGHT: float = 100.0       # 트레이 높이 (mm)
PIPE_PITCH: float = 100.0        # 개별 배관 간격 (mm)
PIPE_COUNT: int = 6              # 동시 라우팅 배관 수

# ─────────────────────────────────────────────
# DB 테이블 및 필드 이름 (프로젝트 DB 스키마 기반)
# ─────────────────────────────────────────────
# 장애물 데이터 소스 테이블
TABLE_EQUIPMENT = "TB_EQUIPMENT"
TABLE_PIPE_RACK  = "TB_PIPE_RACK"
TABLE_DUCT       = "TB_DUCT"

# OBB 24 정점 필드 접두사 (예: OBB_LEFT_BOTTOM_BACK_X, ...)
OBB_VERTEX_PREFIXES = [
    "OBB_LEFT_BOTTOM_BACK",   "OBB_RIGHT_BOTTOM_BACK",
    "OBB_LEFT_TOP_BACK",      "OBB_RIGHT_TOP_BACK",
    "OBB_LEFT_BOTTOM_FRONT",  "OBB_RIGHT_BOTTOM_FRONT",
    "OBB_LEFT_TOP_FRONT",     "OBB_RIGHT_TOP_FRONT",
]

# 슬리브(관통) 필드
PENETRATION_FIELD = "IS_PENETRATION"   # 'true'/'false' 또는 boolean

# 레거시 설계 세그먼트 테이블 (과거 꺾임 패턴)
TABLE_GROUP_SEGMENTS = "TB_GROUP_SEGMENTS"
TABLE_ROUTE_NODES    = "TB_ROUTE_NODES"

# Pass-through 예외 객체 유형 (충돌 등록 제외)
PASSTHROUGH_TYPES: set[str] = {"GRATING", "FLOOR", "GRID_BEAM", "HANDRAIL"}

# ─────────────────────────────────────────────
# 시각화 설정
# ─────────────────────────────────────────────
VIZ_PORT: int = 8050             # Plotly Dash 서버 포트
VIZ_DEBUG: bool = True           # Dash 디버그 모드

# 단계별 색상 (Plotly CSS 색상명 또는 hex)
COLOR_STEP1_TENSION  = "#FFD700"   # 🟡 황색 - 초기 인장 직선
COLOR_STEP2_SNAP     = "#4B9FFF"   # 🔵 파란색 - AI 특징점 스냅
COLOR_STEP3_COLLISION= "#FF4444"   # 🔴 붉은색 - 충돌 노드
COLOR_OBSTACLE       = "#AAAAAA"   # ⚪ 회색 - OBB 장애물
COLOR_PIPES = [
    "#FF69B4",   # 💗 분홍
    "#48D1CC",   # 🩵 민트
    "#ADFF2F",   # 💛 연두
    "#FFA500",   # 주황
    "#DA70D6",   # 보라
    "#00CED1",   # 청록
]
