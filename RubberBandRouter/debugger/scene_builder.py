"""
scene_builder.py
----------------
Plotly 3D 씬 구성 모듈.

각 Step(1~4)에 해당하는 RubberBandState 및 DistributionResult를
Plotly Go 트레이스로 변환하여 Figure dict를 생성한다.

색상 코드:
  Step 1 (초기 인장)      🟡 황색 직선
  Step 2 (AI 스냅)        🔵 파란색 꺾임 + 특징점 구체
  Step 3 (충돌 노드)      🔴 붉은 구체 + ⚪ 회색 OBB 박스
  Step 4 (최종 배관)      💗🩵💛 멀티컬러 개별 배관선
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
