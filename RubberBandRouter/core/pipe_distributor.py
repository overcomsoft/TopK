"""
pipe_distributor.py
-------------------
트레이 중심선 경로 → 개별 배관 평행 오프셋 분배 모듈.

트레이 중심선(RouteSegment 목록)에 대해 법선 방향으로
Index * Pitch 오프셋을 적용하여 각 배관의 3D 좌표 세트를 도출한다.

법선 방향 결정:
  - 수평 세그먼트: Z축 기준 법선 (XY 평면 내 측면)
  - 수직 세그먼트: 이전 수평 세그먼트의 법선 방향 유지
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .rubber_band import RouteSegment

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 클래스
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PipePath:
    """단일 배관의 3D 경로 좌표 세트."""
    pipe_index: int
    pipe_id: str
    utility: str
    offset: float                          # 중심선에서의 오프셋 거리 (mm)
    points: list[list[float]] = field(default_factory=list)  # [[x,y,z], ...]

    def to_dict(self) -> dict:
        return {
            "pipe_index": self.pipe_index,
            "pipe_id": self.pipe_id,
            "utility": self.utility,
            "offset_mm": self.offset,
            "points": self.points,
        }


@dataclass
class DistributionResult:
    """배관 분배 최종 결과."""
    tray_centerline: list[list[float]]    # 트레이 중심선 좌표 목록
    pipes: list[PipePath]                 # 개별 배관 경로 목록
    tray_width: float
    pipe_pitch: float
    pipe_count: int

    def to_dict(self) -> dict:
        return {
            "tray_centerline": self.tray_centerline,
            "tray_width_mm": self.tray_width,
            "pipe_pitch_mm": self.pipe_pitch,
            "pipe_count": self.pipe_count,
            "pipes": [p.to_dict() for p in self.pipes],
        }

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info("[PipeDistributor] JSON 저장: %s", path)


# ─────────────────────────────────────────────────────────────────────────────
# 법선 방향 계산
# ─────────────────────────────────────────────────────────────────────────────

def _compute_normal(seg_dir: np.ndarray, prev_normal: np.ndarray | None) -> np.ndarray:
    """
    세그먼트 방향에 대한 법선 벡터를 계산한다.

    - 수평 세그먼트 (Z 성분 작음): XY 평면 내 수직 방향
    - 수직 세그먼트: 이전 법선 유지
    """
    norm_dir = np.linalg.norm(seg_dir)
    if norm_dir < 1e-9:
        return prev_normal if prev_normal is not None else np.array([0.0, 1.0, 0.0])

    unit = seg_dir / norm_dir
    is_vertical = abs(unit[2]) > 0.7

    if is_vertical and prev_normal is not None:
        return prev_normal

    # 수평 세그먼트: Z축과의 외적으로 법선 계산
    z_axis = np.array([0.0, 0.0, 1.0])
    normal = np.cross(unit, z_axis)
    n_len = np.linalg.norm(normal)
    if n_len < 1e-9:
        # 세그먼트가 Z축과 평행한 경우 (수직) → Y축 사용
        normal = np.array([0.0, 1.0, 0.0])
    else:
        normal = normal / n_len

    return normal


# ─────────────────────────────────────────────────────────────────────────────
# 중심선 추출
# ─────────────────────────────────────────────────────────────────────────────

def segments_to_centerline(
    segments: list["RouteSegment"],
) -> list[np.ndarray]:
    """
    RouteSegment 목록에서 중복 없는 중심선 포인트 목록을 추출한다.
    """
    if not segments:
        return []
    points = [segments[0].start.copy()]
    for seg in segments:
        points.append(seg.end.copy())
    return points


# ─────────────────────────────────────────────────────────────────────────────
# 개별 배관 오프셋 분배
# ─────────────────────────────────────────────────────────────────────────────

def distribute_pipes(
    segments: list["RouteSegment"],
    pipe_count: int | None = None,
    pipe_pitch: float | None = None,
    tray_width: float | None = None,
    pipe_ids: list[str] | None = None,
    utilities: list[str] | None = None,
) -> DistributionResult:
    """
    트레이 중심선 세그먼트에서 개별 배관 경로를 법선 오프셋으로 분배한다.

    Args:
        segments:    트레이 중심선 RouteSegment 목록 (rubber_band.py 출력)
        pipe_count:  배관 수 (None이면 config.py 기본값)
        pipe_pitch:  배관 간격 mm (None이면 config.py 기본값)
        tray_width:  트레이 폭 mm (None이면 config.py 기본값)
        pipe_ids:    각 배관 ID (없으면 자동 생성)
        utilities:   각 배관 유틸리티 종류 (없으면 빈 문자열)

    Returns:
        DistributionResult
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config as cfg

    n     = pipe_count  if pipe_count  is not None else cfg.PIPE_COUNT
    pitch = pipe_pitch  if pipe_pitch  is not None else cfg.PIPE_PITCH
    width = tray_width  if tray_width  is not None else cfg.TRAY_WIDTH

    if pipe_ids is None:
        pipe_ids = [f"PIPE-{i+1:02d}" for i in range(n)]
    if utilities is None:
        utilities = ["" for _ in range(n)]

    # 배관별 오프셋: 중심선을 0으로 대칭 배치
    # 예: n=4 → offsets = [-1.5, -0.5, +0.5, +1.5] * pitch
    half = (n - 1) / 2.0
    offsets = [(i - half) * pitch for i in range(n)]

    # 중심선 포인트
    centerline_pts = segments_to_centerline(segments)
    centerline_list = [p.tolist() for p in centerline_pts]

    pipes: list[PipePath] = []

    prev_normal: np.ndarray | None = None

    for pipe_idx, (pid, util, offset) in enumerate(
        zip(pipe_ids[:n], utilities[:n], offsets)
    ):
        pipe_points: list[list[float]] = []

        for i, seg in enumerate(segments):
            seg_dir = seg.end - seg.start
            normal = _compute_normal(seg_dir, prev_normal)
            prev_normal = normal

            # 세그먼트 시작점에 오프셋 적용
            pt_start = seg.start + normal * offset
            pipe_points.append(pt_start.tolist())

            # 마지막 세그먼트면 끝점도 추가
            if i == len(segments) - 1:
                pt_end = seg.end + normal * offset
                pipe_points.append(pt_end.tolist())

        pipe_path = PipePath(
            pipe_index=pipe_idx,
            pipe_id=pid,
            utility=util,
            offset=offset,
            points=pipe_points,
        )
        pipes.append(pipe_path)

    logger.info(
        "[PipeDistributor] 분배 완료: 배관=%d, 피치=%.0fmm, 트레이폭=%.0fmm",
        n, pitch, width,
    )

    return DistributionResult(
        tray_centerline=centerline_list,
        pipes=pipes,
        tray_width=width,
        pipe_pitch=pitch,
        pipe_count=n,
    )
