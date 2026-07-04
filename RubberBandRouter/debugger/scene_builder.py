"""
================================================================================
scene_builder.py  ─  Plotly 3D 씬 구성 모듈
================================================================================

【실행 명령어】
  ※ 이 모듈은 단독 실행하지 않으며, debugger/timeline_viewer.py 나
     run_routing.py 가 씬을 시각화 객체(Figure dict)로 변환할 때 내부적으로 사용한다.

================================================================================
【단계별 씬 생성 흐름도】

  입력: states[](RubberBandState), dist_result(DistributionResult), obstacles[](OBBObstacle)
  │
  ├─ build_step1_scene()  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 🟡 (Step 1)
  │    ├─ _obb_wireframe_trace() 로 각 장애물 반투명 3D Mesh 생성
  │    └─ _segments_to_scatter3d() 로 황색 인장 직선을 Plotly Go Scatter3d로 드로잉
  │
  ├─ build_step2_scene()  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 🔵 (Step 2)
  │    ├─ _obb_wireframe_trace() 로 장애물 3D Mesh 생성
  │    ├─ _segments_to_scatter3d() 로 파란색 AI 스냅 경로 드로잉
  │    └─ _points_to_scatter3d() 로 특징점 웨이포인트(다이아몬드 마커) 생성
  │
  ├─ build_step3_scene()  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 🔴 (Step 3)
  │    ├─ _obb_wireframe_trace() 로 장애물 3D Mesh 생성 (투명도 조절)
  │    ├─ _segments_to_scatter3d() 로 회피 전 경로 드로잉
  │    └─ _points_to_scatter3d() 로 충돌 지점(붉은색 큰 구체) 생성
  │
  └─ build_step4_scene()  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 💗 (Step 4)
       ├─ _obb_wireframe_trace() 로 장애물 3D Mesh 생성
       ├─ _segments_to_scatter3d() 로 얇은 흰색 트레이 중심선 드로잉
       └─ _pipe_path_trace() 로 오프셋 분배된 N개 개별 배관을 멀티컬러로 드로잉

  출력: list[dict] — Plotly Go Figure 구조를 나타내는 4개의 씬 딕셔너리 리스트

================================================================================
【핵심 알고리즘: OBB 와이어프레임 메쉬 표현 (_obb_wireframe_trace)】

  OBB의 8개 꼭짓점(vertices, 8x3) 정보를 바탕으로, plotly mesh3d 의 삼각형 표면을 생성한다.
  직육면체는 6개의 사각형 면으로 구성되며, 각 면은 2개의 삼각형(총 12개 삼각형)으로 쪼갤 수 있다.
  인덱스 i, j, k는 각 삼각형 꼭짓점을 연결한다:
    i_idx = [0, 0, 1, 1, 2, 2, 4, 4, 0, 0, 3, 3]
    j_idx = [1, 2, 3, 5, 3, 6, 5, 6, 4, 1, 7, 2]
    k_idx = [2, 3, 5, 7, 6, 7, 6, 7, 1, 5, 6, 6]

  이 정점 조합을 이용해 plotly mesh3d의 opacity(투명도)를 주면,
  3D 상에서 속이 비쳐 보이는 입체적인 장애물 박스가 완성된다.

================================================================================
【주요 함수 / 변수】

  _obb_wireframe_trace()    장애물의 8개 월드 꼭짓점을 mesh3d 포맷으로 변환
  _segments_to_scatter3d()  세그먼트 리스트를 이은 3D Line 객체 생성
  _points_to_scatter3d()    특정 포인트(스냅점/충돌점)에 마커 구체 배치
  build_step1~4_scene()     각 단계에 알맞은 Plotly Figure dict 빌드
  _make_figure()            다크 테마 3D 레이아웃(배경 #1A1A2E, 그리드선) 통합 적용
  build_all_scenes()        4단계 씬을 연속 배열로 조립해 일괄 반환
  
  KEY VARIABLES:
    i_idx, j_idx, k_idx     list — mesh3d 3각 폴리곤 인덱스
    traces                  list — go.Figure 데이터 영역에 등록될 plotly 트레이스 리스트
    paper_bgcolor           str — 차트 영역 바깥 배경색 (#1A1A2E)
    aspectmode              str — 'data' 지정으로 3D 공간 비율을 1:1:1로 고정
================================================================================
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from ..core.obstacle_map import OBBObstacle, ObstacleMap
    from ..core.rubber_band import RouteSegment, RubberBandState
    from ..core.pipe_distributor import DistributionResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# OBB 박스 면 트레이스 (와이어프레임)
# ─────────────────────────────────────────────────────────────────────────────

def _obb_wireframe_trace(obs: "OBBObstacle", color: str = "#AAAAAA", opacity: float = 0.3) -> dict:
    """OBB 8 꼭짓점으로 와이어프레임 Mesh3d 트레이스 생성."""
    verts = obs.vertices  # (8, 3)
    x, y, z = verts[:, 0].tolist(), verts[:, 1].tolist(), verts[:, 2].tolist()

    # Mesh3d 삼각형 인덱스 (직육면체 6면 × 2삼각형)
    i_idx = [0, 0, 1, 1, 2, 2, 4, 4, 0, 0, 3, 3]
    j_idx = [1, 2, 3, 5, 3, 6, 5, 6, 4, 1, 7, 2]
    k_idx = [2, 3, 5, 7, 6, 7, 6, 7, 1, 5, 6, 6]

    return {
        "type": "mesh3d",
        "x": x, "y": y, "z": z,
        "i": i_idx, "j": j_idx, "k": k_idx,
        "color": color,
        "opacity": opacity,
        "name": obs.name,
        "showlegend": False,
        "hoverinfo": "name",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 세그먼트 선 트레이스
# ─────────────────────────────────────────────────────────────────────────────

def _segments_to_scatter3d(
    segments: list["RouteSegment"],
    color: str,
    name: str,
    width: int = 4,
) -> dict:
    """RouteSegment 목록 → Scatter3d 트레이스."""
    xs, ys, zs = [], [], []
    for seg in segments:
        xs += [seg.start[0], seg.end[0], None]
        ys += [seg.start[1], seg.end[1], None]
        zs += [seg.start[2], seg.end[2], None]

    return {
        "type": "scatter3d",
        "x": xs, "y": ys, "z": zs,
        "mode": "lines",
        "line": {"color": color, "width": width},
        "name": name,
    }


def _points_to_scatter3d(
    points: list[np.ndarray],
    color: str,
    name: str,
    size: int = 8,
    symbol: str = "circle",
) -> dict:
    """포인트 목록 → Scatter3d 마커 트레이스."""
    if not points:
        return {}
    arr = np.array(points)
    return {
        "type": "scatter3d",
        "x": arr[:, 0].tolist(),
        "y": arr[:, 1].tolist(),
        "z": arr[:, 2].tolist(),
        "mode": "markers",
        "marker": {"color": color, "size": size, "symbol": symbol},
        "name": name,
    }


def _pipe_path_trace(points: list[list[float]], color: str, name: str, width: int = 3) -> dict:
    """배관 경로 포인트 → Scatter3d 선 트레이스."""
    if not points:
        return {}
    arr = np.array(points)
    return {
        "type": "scatter3d",
        "x": arr[:, 0].tolist(),
        "y": arr[:, 1].tolist(),
        "z": arr[:, 2].tolist(),
        "mode": "lines",
        "line": {"color": color, "width": width},
        "name": name,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 단계별 씬 빌더
# ─────────────────────────────────────────────────────────────────────────────

def build_step1_scene(
    state: "RubberBandState",
    obstacles: list["OBBObstacle"] | None = None,
    title: str = "Step 1: 초기 인장 (S→D 직선)",
) -> dict[str, Any]:
    """Step 1 시각화: 황색 직선 + OBB 장애물 박스."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config as cfg

    traces = []

    # 장애물 박스 (회색 반투명)
    if obstacles:
        for obs in obstacles:
            traces.append(_obb_wireframe_trace(obs, cfg.COLOR_OBSTACLE, opacity=0.2))

    # 황색 직선
    traces.append(_segments_to_scatter3d(state.segments, cfg.COLOR_STEP1_TENSION, "초기 인장", width=5))

    return _make_figure(traces, title)


def build_step2_scene(
    state: "RubberBandState",
    obstacles: list["OBBObstacle"] | None = None,
    title: str = "Step 2: AI 특징점 스냅",
) -> dict[str, Any]:
    """Step 2 시각화: 파란색 1차 뼈대 + 특징점 구체."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config as cfg

    traces = []

    if obstacles:
        for obs in obstacles:
            traces.append(_obb_wireframe_trace(obs, cfg.COLOR_OBSTACLE, opacity=0.2))

    traces.append(_segments_to_scatter3d(state.segments, cfg.COLOR_STEP2_SNAP, "AI 스냅 경로", width=4))

    if state.snap_waypoints:
        traces.append(_points_to_scatter3d(
            state.snap_waypoints, cfg.COLOR_STEP2_SNAP, "특징점 웨이포인트", size=10, symbol="diamond",
        ))

    return _make_figure(traces, title)


def build_step3_scene(
    state: "RubberBandState",
    obstacles: list["OBBObstacle"] | None = None,
    title: str = "Step 3: 충돌 감지",
) -> dict[str, Any]:
    """Step 3 시각화: 충돌 노드 붉은 구체 + OBB 박스."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config as cfg

    traces = []

    if obstacles:
        for obs in obstacles:
            traces.append(_obb_wireframe_trace(obs, cfg.COLOR_OBSTACLE, opacity=0.3))

    traces.append(_segments_to_scatter3d(state.segments, cfg.COLOR_STEP2_SNAP, "경로 (회피 전)", width=3))

    if state.collision_points:
        traces.append(_points_to_scatter3d(
            state.collision_points, cfg.COLOR_STEP3_COLLISION, "충돌 노드 🔴", size=14, symbol="circle",
        ))

    return _make_figure(traces, title)


def build_step4_scene(
    final_state: "RubberBandState",
    dist_result: "DistributionResult",
    obstacles: list["OBBObstacle"] | None = None,
    title: str = "Step 4: 최종 배관 분배",
) -> dict[str, Any]:
    """Step 4 시각화: 개별 배관 멀티컬러 + OBB 박스."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config as cfg

    traces = []

    if obstacles:
        for obs in obstacles:
            traces.append(_obb_wireframe_trace(obs, cfg.COLOR_OBSTACLE, opacity=0.2))

    # 트레이 중심선 (얇은 흰색)
    traces.append(_segments_to_scatter3d(final_state.segments, "#FFFFFF", "트레이 중심선", width=1))

    # 개별 배관 (멀티컬러)
    colors = cfg.COLOR_PIPES
    for pipe in dist_result.pipes:
        color = colors[pipe.pipe_index % len(colors)]
        name = f"{pipe.pipe_id} ({pipe.utility})" if pipe.utility else pipe.pipe_id
        tr = _pipe_path_trace(pipe.points, color, name, width=4)
        if tr:
            traces.append(tr)

    return _make_figure(traces, title)


# ─────────────────────────────────────────────────────────────────────────────
# Figure 레이아웃 생성
# ─────────────────────────────────────────────────────────────────────────────

def _make_figure(traces: list[dict], title: str) -> dict[str, Any]:
    """Plotly Figure dict 생성 (다크 테마)."""
    return {
        "data": [t for t in traces if t],
        "layout": {
            "title": {"text": title, "font": {"color": "#FFFFFF", "size": 16}},
            "paper_bgcolor": "#1A1A2E",
            "plot_bgcolor": "#1A1A2E",
            "scene": {
                "bgcolor": "#1A1A2E",
                "xaxis": {"title": "X (mm)", "color": "#CCCCCC", "gridcolor": "#333355"},
                "yaxis": {"title": "Y (mm)", "color": "#CCCCCC", "gridcolor": "#333355"},
                "zaxis": {"title": "Z (mm)", "color": "#CCCCCC", "gridcolor": "#333355"},
                "aspectmode": "data",
            },
            "margin": {"l": 0, "r": 0, "t": 40, "b": 0},
            "legend": {"font": {"color": "#CCCCCC"}, "bgcolor": "#2A2A4E"},
            "font": {"color": "#CCCCCC"},
        },
    }


def build_all_scenes(
    states: list["RubberBandState"],
    dist_result: "DistributionResult",
    obstacles: list["OBBObstacle"] | None = None,
) -> list[dict[str, Any]]:
    """모든 단계 씬을 한 번에 빌드한다. 슬라이더 뷰어에서 인덱스로 접근한다."""
    scenes = []
    if len(states) >= 1:
        scenes.append(build_step1_scene(states[0], obstacles))
    if len(states) >= 2:
        scenes.append(build_step2_scene(states[1], obstacles))
    if len(states) >= 3:
        scenes.append(build_step3_scene(states[2], obstacles))
    # Step 4: 분배 결과
    final_state = states[-1] if states else None
    if final_state and dist_result:
        scenes.append(build_step4_scene(final_state, dist_result, obstacles))
    return scenes
