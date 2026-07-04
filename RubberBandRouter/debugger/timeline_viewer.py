"""
================================================================================
timeline_viewer.py  ─  4단계 시뮬레이션 디버거 UI (Plotly Dash)
================================================================================

【실행 명령어】
  ※ 데모 모드 단독 실행:
      cd RubberBandRouter
      python debugger/timeline_viewer.py
      # → 브라우저에서 http://localhost:8050 접속

  ※ 실환경 실행 (DB 연동):
      python run_routing.py
      # → 프로젝트 및 작업 선택 후 Dash 시각화 서버 자동 기동

================================================================================
【단계별 흐름도 — UI 렌더링 파이프라인】

  ┌─────────────────────────────────────────────────────────────┐
  │  입력: run_routing.py 에서 빌드된 4개의 plotly figure 객체   │
  └───────────────┬─────────────────────────────────────────────┘
                  │ create_dash_app(scenes)
                  ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  Dash UI 레이아웃 로드                                      │
  │  ├─ 헤더 및 요약 정보                                        │
  │  ├─ dcc.Slider (타임라인 슬라이더: 0 ~ 3 단계)               │
  │  ├─ html.Div(id='step-info') (배지 영역)                    │
  │  └─ dcc.Graph(id='scene-graph') (3D 시각화 영역)             │
  └───────────────┬─────────────────────────────────────────────┘
                  │ 콜백 트리거 (슬라이더 드래그/클릭)
                  ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  update_scene(step_idx) 콜백 함수                           │
  │  ├─ step_idx에 해당하는 plotly go.Figure 로딩                │
  │  ├─ Step 정보 배지 스타일링 및 라벨 업데이트                 │
  │  └─ Graph에 figure 주입하여 3D WebGL 캔버스 업데이트          │
  └─────────────────────────────────────────────────────────────┘

================================================================================
【데모 씬 생성 알고리즘 (_build_demo_scenes)】

  DB 연결이 없을 때 로직 검증용 데모 시나리오를 구성한다:
  1) 3개의 가상 OBB 장애물 배치 (TANK-A, COLUMN-B, PUMP-C)
  2) Case B (부분 변동) 꺾임 웨이포인트 3개 가상 정의 (비율 투영 모사)
  3) start(1m, 5m, 3m) → end(28m, 5m, 3m) 라우팅 태스크 생성
  4) run_routing() 엔진 실행 → 3단계 상태(Tension, Snap, Collision Avoidance) 획득
  5) distribute_pipes() → 4개의 병렬 배관으로 트레이 중심선에서 오프셋 분배
  6) build_all_scenes() → Plotly go.Figure 4개 세트로 변환

================================================================================
【주요 함수 / 변수】

  _build_demo_scenes()      가상의 장애물, 웨이포인트, 시작/종단을 생성하여
                            자율 라우팅 후 4단계 plotly 씬 리스트 반환
  create_dash_app()         scenes[] 리스트를 전달받아 Dash Layout 및
                            슬라이더 인터랙션 콜백이 연결된 Dash App 객체 반환
  main()                    진입 함수. use_demo=True 인 경우 데모 씬 사용,
                            False 인 경우 실제 파이프라인에서 실행
  
  KEY VARIABLES:
    scenes[]                list[go.Figure] — go.Figure 객체 4개 리스트
    step_labels             dict — 각 슬라이더 단계별 UI 한글 명칭 매핑
    step_icons              dict — 각 슬라이더 단계별 아이콘 매핑
    _bg, _card, _accent     str — 테마 스타일링용 CSS 색상 상수
================================================================================
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# 경로 설정
_ROUTER_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _ROUTER_ROOT.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_ROUTER_ROOT))

import numpy as np

import config as cfg

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 데모 씬 생성 (DB 없이 독립 실행용)
# ─────────────────────────────────────────────────────────────────────────────

def _build_demo_scenes() -> list[dict]:
    """
    DB 연결 없이 데모 장애물 + 경로를 생성하여 4단계 씬을 반환한다.
    실제 운영 시에는 run_full_pipeline()을 호출한다.
    """
    from core.obstacle_map import OBBObstacle, ObstacleMap
    from core.feature_extractor import FeatureSet, Waypoint
    from core.rubber_band import run_routing, RubberBandState
    from core.pipe_distributor import distribute_pipes
    from debugger.scene_builder import build_all_scenes

    # 데모 장애물 (큰 박스 3개)
    def _make_box_obs(name: str, cx: float, cy: float, cz: float,
                      sx: float, sy: float, sz: float) -> OBBObstacle:
        """축-정렬 직육면체 OBB 장애물 생성."""
        verts = []
        for dx in [-sx/2, sx/2]:
            for dy in [-sy/2, sy/2]:
                for dz in [-sz/2, sz/2]:
                    verts.append([cx+dx, cy+dy, cz+dz])
        verts_arr = np.array(verts)
        center = np.array([cx, cy, cz])
        axes = np.eye(3)
        he = np.array([sx/2, sy/2, sz/2])
        vol = sx * sy * sz
        return OBBObstacle(
            name=name,
            source_table="DEMO",
            project_id="DEMO",
            vertices=verts_arr,
            center=center,
            half_extents=he,
            axes=axes,
            volume=vol,
            is_penetration=False,
            obj_type="EQUIPMENT",
        )

    obstacles = [
        _make_box_obs("TANK-A",    8000,  5000, 3000, 4000, 3000, 5000),
        _make_box_obs("COLUMN-B", 18000,  4000, 6000, 2000, 2000, 10000),
        _make_box_obs("PUMP-C",   14000, 10000, 1000, 3000, 4000, 2000),
    ]

    current_map = ObstacleMap(project_id="CURRENT")
    current_map.obstacles = obstacles

    # 레거시 특징점 (Case B 시뮬레이션)
    waypoints = [
        Waypoint(np.array([6000.0, 5000.0, 5000.0]),  priority=0, source="case_b_rack_z"),
        Waypoint(np.array([13000.0, 5000.0, 5000.0]), priority=1, source="case_b_bypass_start"),
        Waypoint(np.array([20000.0, 5000.0, 5000.0]), priority=2, source="case_b_bypass_end"),
    ]
    feature_set = FeatureSet(case="B", waypoints=waypoints)

    start = np.array([1000.0, 5000.0, 3000.0])
    end   = np.array([28000.0, 5000.0, 3000.0])

    # 라우팅 실행
    result = run_routing(start, end, feature_set, current_map)

    # 배관 분배
    dist_result = distribute_pipes(result.final_segments, pipe_count=4)

    # 씬 빌드
    scenes = build_all_scenes(result.states, dist_result, obstacles)
    return scenes


# ─────────────────────────────────────────────────────────────────────────────
# Dash 앱 구성
# ─────────────────────────────────────────────────────────────────────────────

def create_dash_app(scenes: list[dict]):
    """4단계 타임라인 슬라이더 Dash 앱을 생성하고 반환한다."""
    try:
        import dash
        from dash import dcc, html
        from dash.dependencies import Input, Output
    except ImportError:
        print("[ERROR] dash 패키지가 설치되지 않았습니다. pip install dash 실행 후 재시도하세요.")
        sys.exit(1)

    try:
        import plotly.graph_objects as go
    except ImportError:
        print("[ERROR] plotly 패키지가 설치되지 않았습니다.")
        sys.exit(1)

    step_labels = {
        0: "Step 1: 초기 인장 (S→D 직선)",
        1: "Step 2: AI 특징점 스냅",
        2: "Step 3: 충돌 감지",
        3: "Step 4: 최종 배관 분배",
    }
    step_icons = {0: "🟡", 1: "🔵", 2: "🔴", 3: "💗"}

    app = dash.Dash(
        __name__,
        title="RubberBand Routing Debugger",
    )

    # CSS 스타일 인라인
    _bg = "#0D0D1A"
    _card = "#1A1A2E"
    _accent = "#4B9FFF"
    _text = "#E0E0FF"

    app.layout = html.Div(
        style={"backgroundColor": _bg, "minHeight": "100vh", "fontFamily": "Segoe UI, sans-serif"},
        children=[
            # 헤더
            html.Div(
                style={
                    "background": f"linear-gradient(135deg, #1A1A2E, #16213E)",
                    "padding": "24px 32px",
                    "borderBottom": f"2px solid {_accent}",
                },
                children=[
                    html.H1(
                        "🔧 RubberBand Routing Debugger",
                        style={"color": _text, "margin": 0, "fontSize": "24px", "fontWeight": 700},
                    ),
                    html.P(
                        "AI 맵 위상 매칭 & 고무줄 변형 3D 직교 배관 라우팅 엔진 시각화",
                        style={"color": "#8888BB", "margin": "6px 0 0", "fontSize": "13px"},
                    ),
                ],
            ),

            # 슬라이더 섹션
            html.Div(
                style={"padding": "24px 32px 12px", "backgroundColor": _card},
                children=[
                    html.Label(
                        "⏱ 시뮬레이션 단계",
                        style={"color": _text, "fontWeight": 600, "fontSize": "14px", "marginBottom": "12px", "display": "block"},
                    ),
                    dcc.Slider(
                        id="step-slider",
                        min=0,
                        max=len(scenes) - 1,
                        step=1,
                        value=0,
                        marks={
                            i: {
                                "label": f"{step_icons.get(i, '')} {step_labels.get(i, f'Step {i+1}')}",
                                "style": {"color": _accent, "fontSize": "12px", "whiteSpace": "normal", "maxWidth": "120px"},
                            }
                            for i in range(len(scenes))
                        },
                        updatemode="drag",
                        tooltip={"placement": "bottom", "always_visible": False},
                    ),
                ],
            ),

            # Step 정보 배지
            html.Div(
                id="step-info",
                style={"padding": "12px 32px", "backgroundColor": _bg},
            ),

            # 3D 씬
            html.Div(
                style={"padding": "0 16px 16px"},
                children=[
                    dcc.Graph(
                        id="scene-graph",
                        style={"height": "70vh"},
                        config={"displayModeBar": True, "scrollZoom": True},
                    ),
                ],
            ),

            # 하단 정보
            html.Div(
                style={"padding": "12px 32px", "backgroundColor": _card, "borderTop": "1px solid #333355"},
                children=[
                    html.P(
                        "💡 마우스 드래그: 회전 | 스크롤: 확대/축소 | 더블클릭: 초기화",
                        style={"color": "#666688", "margin": 0, "fontSize": "12px"},
                    ),
                ],
            ),
        ],
    )

    # ─── 콜백 ─────────────────────────────────────────────────────────────────

    @app.callback(
        Output("scene-graph", "figure"),
        Output("step-info", "children"),
        Input("step-slider", "value"),
    )
    def update_scene(step_idx: int):
        if step_idx is None or step_idx >= len(scenes):
            step_idx = 0

        fig_data = scenes[step_idx]

        # Step 정보 배지
        label = step_labels.get(step_idx, f"Step {step_idx + 1}")
        icon  = step_icons.get(step_idx, "")
        badge_colors = {0: "#B8860B", 1: "#1565C0", 2: "#C62828", 3: "#AD1457"}
        badge_color  = badge_colors.get(step_idx, "#333355")

        info = html.Div(
            style={"display": "flex", "alignItems": "center", "gap": "12px"},
            children=[
                html.Span(
                    f"{icon} {label}",
                    style={
                        "backgroundColor": badge_color,
                        "color": _text,
                        "padding": "6px 16px",
                        "borderRadius": "20px",
                        "fontSize": "13px",
                        "fontWeight": 600,
                    },
                ),
                html.Span(
                    f"({step_idx + 1} / {len(scenes)} 단계)",
                    style={"color": "#666688", "fontSize": "12px"},
                ),
            ],
        )

        return fig_data, info

    return app


# ─────────────────────────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────────────────────────

def main(use_demo: bool = True, port: int | None = None) -> None:
    """
    Args:
        use_demo:  True이면 DB 없이 데모 씬 사용
        port:      Dash 서버 포트 (None이면 config.VIZ_PORT)
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    _port = port if port is not None else cfg.VIZ_PORT

    if use_demo:
        logger.info("데모 모드로 씬 생성 중...")
        scenes = _build_demo_scenes()
    else:
        # TODO: DB에서 실제 데이터 로드하여 파이프라인 실행
        raise NotImplementedError("실제 DB 파이프라인은 추후 구현")

    app = create_dash_app(scenes)
    logger.info("Dash 앱 시작: http://localhost:%d", _port)
    app.run(debug=cfg.VIZ_DEBUG, port=_port)


if __name__ == "__main__":
    main(use_demo=True)
