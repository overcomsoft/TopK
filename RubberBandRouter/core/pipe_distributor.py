"""
================================================================================
pipe_distributor.py  ─  트레이 중심선 → 개별 배관 평행 오프셋 분배 모듈
================================================================================

【실행 명령어】
  ※ 직접 테스트 (Python REPL):
      import sys; sys.path.insert(0, "RubberBandRouter")
      import numpy as np
      from core.rubber_band import RouteSegment
      from core.pipe_distributor import distribute_pipes

      segs = [
          RouteSegment(np.array([0.,0.,0.]),   np.array([5000.,0.,0.])),
          RouteSegment(np.array([5000.,0.,0.]),np.array([5000.,3000.,0.])),
      ]
      result = distribute_pipes(segs, pipe_count=4)
      for pipe in result.pipes:
          print(pipe.pipe_id, pipe.points)

      result.save_json("output.json")   # JSON 저장

================================================================================
【단계별 흐름도】

  입력: RouteSegment[] (트레이 중심선), pipe_count, pipe_pitch
  │
  ├─ 1단계: 세그먼트별 법선 벡터 계산
  │    for each segment:
  │        seg_dir = normalize(end - start)
  │        │
  │        ├─ 수평 세그먼트 (|dz| < threshold):
  │        │    normal = normalize(cross(seg_dir, Z_UP))
  │        │    Z_UP = [0, 0, 1]
  │        │    → XY 평면 내 측면 방향 (배관 가로 배열)
  │        │
  │        └─ 수직 세그먼트 (|dz| >= threshold):
  │             normal = prev_horizontal_normal
  │             → 이전 수평 법선 방향 유지 (배관 연속성 보장)
  │
  ├─ 2단계: 오프셋 계산 (대칭 배열)
  │    n = pipe_count    # 배관 수
  │    for i in 0..n-1:
  │        offset_dist = (i - (n-1)/2.0) × pipe_pitch
  │        # 예: n=4, pitch=100 → [-150, -50, +50, +150] mm
  │        # 예: n=3, pitch=100 → [-100, 0, +100] mm (중앙이 0)
  │
  └─ 3단계: 각 배관 폴리라인 좌표 계산
       for each segment, for each pipe(i):
           offset_vec = offset_dist × normal_vector
           start_i = seg.start + offset_vec
           end_i   = seg.end   + offset_vec
       → 연속된 점 목록으로 폴리라인 구성 (PipePath.points[])

  출력: DistributionResult
        ├─ pipes[]      PipePath[] — 개별 배관 폴리라인
        └─ save_json()  결과를 JSON 파일로 저장

================================================================================
【핵심 알고리즘: 법선 벡터 계산 및 대칭 오프셋 배치】

  # 수평 세그먼트의 법선 계산 (Z×세그먼트방향 의 외적)
  Z_UP     = [0, 0, 1]
  seg_dir  = normalize(seg.end - seg.start)
  normal   = normalize(cross(Z_UP, seg_dir))
  # → seg_dir 에 수직이고 XY 평면 내에 있는 단위벡터

  # 대칭 오프셋 배치
  offsets = [(i - (n-1)/2.0) * pitch for i in range(n)]
  # n=4, pitch=100 예시:
  #   i=0: (0 - 1.5) * 100 = -150mm
  #   i=1: (1 - 1.5) * 100 = -50mm
  #   i=2: (2 - 1.5) * 100 = +50mm
  #   i=3: (3 - 1.5) * 100 = +150mm

  # 배관 j번의 세그먼트 k번 시작점
  point = seg.start + offsets[j] * normal_k

================================================================================
【주요 클래스 / 함수 / 변수】

  PipePath                          개별 배관 1개의 3D 경로
    .pipe_id      str   배관 ID ("PIPE-01", "PIPE-02", ...)
    .pipe_index   int   배관 순번 (0-based, 색상 인덱스 용도)
    .utility      str | None   유틸리티 라벨 (선택)
    .points       list[list[float]]   [[x,y,z], ...] 폴리라인 좌표 (mm)

  DistributionResult                분배 결과 컨테이너
    .pipes        list[PipePath]   개별 배관 목록
    .tray_segments  RouteSegment[]  트레이 중심선 세그먼트 (참조용)
    .save_json()  Path  결과를 JSON 파일로 저장하고 경로 반환

  distribute_pipes()   트레이 세그먼트 + 배관 수 → DistributionResult 반환
                       (주 진입점)

  KEY VARIABLES:
    n_pipes     = pipe_count (기본값: cfg.PIPE_COUNT = 6)
    pitch       = pipe_pitch (기본값: cfg.PIPE_PITCH = 100mm)
    Z_UP        = np.array([0,0,1])   법선 계산 기준 수직 벡터
    offsets[]   대칭 배치 거리 목록 (mm)
    VERT_THRESH = 0.7   |dz/len| 비율이 이 값 이상이면 수직 세그먼트로 판별

================================================================================
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
