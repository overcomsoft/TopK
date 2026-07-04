"""
================================================================================
collision.py  ─  OBB 충돌 검사 및 3대 회피 전략 모듈
================================================================================

【실행 명령어】
  ※ 독립 테스트:
      cd RubberBandRouter
      python -m pytest tests/test_collision.py -v

================================================================================
【단계별 흐름도】

  ┌──────────────────────────────────────────────────────────────┐
  │  입력: 세그먼트(start, end) + 장애물 목록(OBBObstacle[])    │
  └────────────────────┬─────────────────────────────────────────┘
                       │ find_collisions()
                       ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  segment_vs_obb_sat() — 각 세그먼트 × 각 장애물 충돌 검사   │
  │                                                              │
  │  알고리즘 (2단계):                                           │
  │  1단계 — AABB 빠른 사전 필터 (margin 포함)                  │
  │    seg_min = [start,end].min - margin                        │
  │    seg_max = [start,end].max + margin                        │
  │    → 겹침 없으면 즉시 "충돌 없음" 반환 (O(1))              │
  │                                                              │
  │  2단계 — OBB 로컬 공간 샘플 검사                            │
  │    세그먼트를 500mm 간격으로 N개 점 샘플링                   │
  │    for each pt in samples:                                   │
  │        local = obs.axes @ (pt - obs.center)  # 로컬 좌표    │
  │        if all(|local| <= half_extents + margin):             │
  │            → "충돌" 반환 + 침투깊이(penetration_depth)      │
  └──────────────┬───────────────────────────────────────────────┘
                 │ CollisionResult.is_colliding = True
                 ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  resolve_collision() — 3대 회피 전략 순서 적용               │
  │                                                              │
  │  1순위: strategy_1_sleeve_tunnel()                           │
  │    조건: obs.is_penetration = True (슬리브 관통 허용)        │
  │    처리: 세그먼트를 슬리브 중심축 방향으로 강제 정렬         │
  │    웨이포인트: [진입점, 통과점, 이탈점]                      │
  │                                                              │
  │  2순위: strategy_2_vertical_bypass()                         │
  │    조건: remaining_vertical_bends >= 2                       │
  │         AND 수직이동거리 < 수평이동거리                       │
  │    처리: 장애물 위(over) 또는 아래(under) 계단형 우회        │
  │    웨이포인트: [상승점, 오버패스 수평선, 하강점]             │
  │    비용: 수직 꺾임 2회 소비                                  │
  │                                                              │
  │  3순위: strategy_3_lateral_bypass()                          │
  │    조건: 항상 실행 가능 (폴백)                               │
  │    처리: OBB 측면 + margin 에 바짝 붙은 4포인트 90도 우회   │
  │    웨이포인트: [좌/우 진입, 측면 통과, 좌/우 이탈]          │
  └──────────────────────────────────────────────────────────────┘
                 │
                 ▼
  AvoidanceResult(strategy, waypoints[], success)
  → rubber_band.py 의 step3_push_resolve() 에서 새 세그먼트 생성

================================================================================
【핵심 알고리즘: OBB 로컬 공간 충돌 검사】

  # 세그먼트 위의 샘플 점이 OBB (+ margin) 내부에 있는지 판별
  margin   = tray_half_width + safety_margin   # 배관 반폭 + 안전거리
  expanded = obs.half_extents + margin          # 확장된 반-크기

  for t in linspace(0, 1, N):                  # N = seg_len / 500 + 1
      pt    = start + t * (end - start)
      local = obs.axes @ (pt - obs.center)     # (3,) 로컬 좌표
      if all(|local[i]| <= expanded[i]):       # 3축 모두 범위 내
          → 충돌 감지

【3순위 회피: 최소 우회 거리 계산】

  # 장애물 측면까지 법선 방향 투영
  seg_dir  = normalize(end - start)
  normal   = normalize(cross(seg_dir, [0,0,1]))  # 수평 법선
  proj_to_normal = dot(obs.center - start, normal)
  bypass_side = sign(proj_to_normal)             # 좌(-1) 또는 우(+1)

  # 회피 오프셋 = OBB 측면 + margin (법선 방향)
  offset = (max_half_normal + margin) * bypass_side * normal
  p1 = start + proj_along_seg * seg_dir + offset  # 진입 회피점
  p2 = end   - proj_along_seg * seg_dir + offset  # 이탈 회피점

================================================================================
【주요 클래스 / 함수 / 변수】

  CollisionResult                         충돌 검사 결과 데이터 클래스
    .is_colliding     bool  충돌 여부
    .obstacle         OBBObstacle | None  충돌한 장애물
    .collision_point  ndarray(3,)  충돌 근사 좌표 (세그먼트 중점)
    .penetration_depth float  침투 깊이 (mm)

  AvoidanceStrategy   Enum  회피 전략 종류
    SLEEVE_TUNNEL     1순위 슬리브 터널링
    VERTICAL_BYPASS   2순위 수직 오버/언더패스
    LATERAL_BYPASS    3순위 측면 직교 우회

  AvoidanceResult                         회피 전략 적용 결과
    .strategy         AvoidanceStrategy
    .waypoints[]      list[ndarray(3,)]  삽입할 웨이포인트 좌표 목록
    .vertical_bends_used  int  소비된 수직 꺾임 횟수 (1순위=0, 2순위=2, 3순위=0)
    .success          bool

  segment_vs_obb_sat()    세그먼트 vs OBB 충돌 검사 (AABB 필터 + 샘플링)
  find_collisions()       전체 세그먼트 × 장애물 목록 충돌 탐색
  resolve_collision()     충돌 → 3대 전략 순서 적용
  strategy_1_sleeve_tunnel()   1순위 슬리브 관통
  strategy_2_vertical_bypass() 2순위 수직 우회
  strategy_3_lateral_bypass()  3순위 측면 우회

  KEY VARIABLES:
    margin            = tray_half_width + safety_margin (충돌 여유 mm)
    expanded_he       = obs.half_extents + margin       (확장 반-크기)
    n_samples         = max(3, seg_len // 500 + 1)      (샘플 개수)
    bypass_offset     = OBB 측면 반-크기 + margin       (우회 거리)

================================================================================
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .obstacle_map import OBBObstacle

logger = logging.getLogger(__name__)




# ─────────────────────────────────────────────────────────────────────────────
# 충돌 결과 데이터 클래스
# ─────────────────────────────────────────────────────────────────────────────

class AvoidanceStrategy(Enum):
    SLEEVE_TUNNEL   = auto()   # 1순위: 슬리브 관통
    VERTICAL_BYPASS = auto()   # 2순위: 수직 오버/언더패스
    LATERAL_BYPASS  = auto()   # 3순위: 외곽 우회


@dataclass
class CollisionResult:
    """세그먼트-OBB 충돌 검사 결과."""
    is_colliding: bool
    obstacle: "OBBObstacle | None" = None
    collision_point: np.ndarray | None = None   # 충돌 지점 (세그먼트 파라미터 t)
    penetration_depth: float = 0.0


@dataclass
class AvoidanceResult:
    """회피 전략 적용 결과."""
    strategy: AvoidanceStrategy
    waypoints: list[np.ndarray]   # 회피 경로 추가 웨이포인트 (직교)
    success: bool
    vertical_bends_used: int = 0  # 이번 회피에서 사용한 수직 꺾임 수


# ─────────────────────────────────────────────────────────────────────────────
# SAT OBB 충돌 검사
# ─────────────────────────────────────────────────────────────────────────────

def _project_interval(points: np.ndarray, axis: np.ndarray) -> tuple[float, float]:
    """점들을 축에 투영하여 [min, max] 구간을 반환."""
    proj = points @ axis
    return float(proj.min()), float(proj.max())


def _obb_corners(obs: "OBBObstacle") -> np.ndarray:
    """OBB의 8개 꼭짓점을 반환한다 (이미 저장된 vertices 활용)."""
    return obs.vertices  # shape (8, 3)


def segment_vs_obb_sat(
    seg_start: np.ndarray,
    seg_end: np.ndarray,
    obs: "OBBObstacle",
    tray_half_width: float = 300.0,
    safety_margin: float = 50.0,
) -> CollisionResult:
    """
    세그먼트(배관 트레이 중심축) vs OBB 간 충돌 검사.

    알고리즘:
      1) AABB 빠른 사전 필터 (margin 포함)
      2) 세그먼트 위 최근접점(closest point)을 구한 뒤
         OBB 로컬 공간에서 half_extents + margin 범위 내에 있는지 점검

    Returns:
        CollisionResult
    """
    margin = tray_half_width + safety_margin

    obs_corners = _obb_corners(obs)  # (8, 3)
    obs_min = obs_corners.min(axis=0)
    obs_max = obs_corners.max(axis=0)

    # 세그먼트 AABB (margin 포함)
    seg_pts = np.vstack([seg_start, seg_end])
    seg_min = seg_pts.min(axis=0) - margin
    seg_max = seg_pts.max(axis=0) + margin

    # 1차 AABB 분리 테스트
    if np.any(seg_max < obs_min) or np.any(seg_min > obs_max):
        return CollisionResult(is_colliding=False)

    # 2차: 세그먼트 위 모든 샘플점을 OBB 로컬 공간에서 검사
    # (세그먼트를 N개 점으로 샘플링 → OBB 로컬 좌표 변환 → 범위 초과 여부)
    seg_len = np.linalg.norm(seg_end - seg_start)
    n_samples = max(3, int(seg_len / 500) + 1)   # 500mm 간격으로 샘플
    ts = np.linspace(0.0, 1.0, n_samples)

    expanded_he = obs.half_extents + margin   # margin 확장된 반-크기

    for t in ts:
        pt = seg_start + t * (seg_end - seg_start)
        # OBB 로컬 공간으로 변환
        local = obs.axes @ (pt - obs.center)   # (3,)
        if np.all(np.abs(local) <= expanded_he):
            mid_point = (seg_start + seg_end) / 2.0
            pen = float(np.min(expanded_he - np.abs(local)))
            return CollisionResult(
                is_colliding=True,
                obstacle=obs,
                collision_point=mid_point,
                penetration_depth=max(0.0, pen),
            )

    return CollisionResult(is_colliding=False)


def find_collisions(
    segments: list[tuple[np.ndarray, np.ndarray]],
    obstacles: list["OBBObstacle"],
    tray_half_width: float = 300.0,
    safety_margin: float = 50.0,
) -> list[tuple[int, CollisionResult]]:
    """
    다수 세그먼트와 다수 장애물 간 일괄 충돌 검사.

    Returns:
        [(segment_index, CollisionResult), ...] - 충돌이 발생한 쌍 목록
    """
    collisions: list[tuple[int, CollisionResult]] = []
    for i, (s, e) in enumerate(segments):
        for obs in obstacles:
            if obs.is_penetration:
                continue  # 슬리브는 충돌 검사 제외
            result = segment_vs_obb_sat(s, e, obs, tray_half_width, safety_margin)
            if result.is_colliding:
                collisions.append((i, result))
                break  # 한 세그먼트당 첫 충돌만 기록 (순차 처리)
    return collisions


# ─────────────────────────────────────────────────────────────────────────────
# 3대 회피 전략
# ─────────────────────────────────────────────────────────────────────────────

def _align_orthogonal(point: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """
    주어진 점을 reference 점과 직교 정렬한다.
    가장 큰 차이 성분만 유지하고 나머지는 reference에 맞춘다.
    (직교 라우팅 강제)
    """
    delta = point - reference
    abs_delta = np.abs(delta)
    dominant_axis = int(np.argmax(abs_delta))
    aligned = reference.copy()
    aligned[dominant_axis] = point[dominant_axis]
    return aligned


def strategy_1_sleeve_tunnel(
    obs: "OBBObstacle",
    seg_start: np.ndarray,
    seg_end: np.ndarray,
) -> AvoidanceResult | None:
    """
    1순위: 슬리브 터널링.
    관통 슬리브가 있으면 OBB 중심축으로 경로를 강제 정렬하여 통과.
    """
    if not obs.is_penetration:
        return None

    # 슬리브 중심축 방향으로 경로 정렬
    center = obs.center
    # 세그먼트 방향에 따라 통과 진입/진출 포인트 계산
    seg_dir = seg_end - seg_start
    seg_len = np.linalg.norm(seg_dir)
    if seg_len < 1e-9:
        return None
    seg_unit = seg_dir / seg_len

    # 슬리브 중심을 통과하도록 진입/진출 조정
    t_in  = np.dot(center - seg_start, seg_unit)
    t_out = t_in  # 슬리브 중심 단일 통과점

    # 통과 웨이포인트: 슬리브 중심점에 세그먼트 방향 투영
    through_pt = seg_start + t_in * seg_unit
    # 직교 스냅 (슬리브 중심 X,Y 고정, Z는 세그먼트 유지)
    through_pt[0] = center[0]
    through_pt[1] = center[1]

    logger.debug("[Collision] 슬리브 터널링: center=%s", center)
    return AvoidanceResult(
        strategy=AvoidanceStrategy.SLEEVE_TUNNEL,
        waypoints=[through_pt],
        success=True,
        vertical_bends_used=0,
    )


def strategy_2_vertical_bypass(
    obs: "OBBObstacle",
    seg_start: np.ndarray,
    seg_end: np.ndarray,
    remaining_vertical_bends: int,
    tray_height: float = 100.0,
    safety_margin: float = 50.0,
    prefer_over: bool = True,
) -> AvoidanceResult | None:
    """
    2순위: 수직 오버/언더패스.
    MAX_VERTICAL_BENDS 잔여 횟수 내에서 장애물 상공 또는 하부를 계단형으로 우회.
    수직 우회에는 꺾임 2회가 필요 (올라가기+내려오기).
    """
    if remaining_vertical_bends < 2:
        return None

    obs_corners = _obb_corners(obs)
    obs_z_max = float(obs_corners[:, 2].max())
    obs_z_min = float(obs_corners[:, 2].min())

    # 오버패스 Z 높이
    z_over  = obs_z_max + tray_height + safety_margin * 2
    # 언더패스 Z 높이
    z_under = obs_z_min - tray_height - safety_margin * 2

    # 수직 우회 거리 vs 수평 우회 거리 비교 (prefer_over 기본)
    seg_mid = (seg_start + seg_end) / 2.0
    z_target = z_over if prefer_over else z_under
    vertical_dist = abs(z_target - seg_mid[2]) * 2  # 올라가고 내려오기

    # 수평 우회 거리 (OBB AABB 기준 최단 외곽)
    obs_x_max = float(obs_corners[:, 0].max())
    obs_x_min = float(obs_corners[:, 0].min())
    lateral_dist = min(
        abs(seg_start[0] - obs_x_max) + abs(seg_end[0] - obs_x_max),
        abs(seg_start[0] - obs_x_min) + abs(seg_end[0] - obs_x_min),
    )

    # 수직 우회가 더 짧을 때만 적용
    if vertical_dist >= lateral_dist:
        return None

    # 계단형 웨이포인트 생성 (직교)
    # 진입점 → 상승/하강 → 장애물 통과 → 복귀
    obs_x_center = (obs_x_max + obs_x_min) / 2.0

    wp1 = np.array([seg_start[0], seg_start[1], z_target])  # 상승
    wp2 = np.array([seg_end[0],   seg_end[1],   z_target])  # 수평 이동
    # wp3 = seg_end (내려오기, 자연스럽게 연결)

    logger.debug(
        "[Collision] 수직 %s패스: z=%.1f, 수직거리=%.1f, 수평거리=%.1f",
        "오버" if prefer_over else "언더", z_target, vertical_dist, lateral_dist,
    )
    return AvoidanceResult(
        strategy=AvoidanceStrategy.VERTICAL_BYPASS,
        waypoints=[wp1, wp2],
        success=True,
        vertical_bends_used=2,
    )


def strategy_3_lateral_bypass(
    obs: "OBBObstacle",
    seg_start: np.ndarray,
    seg_end: np.ndarray,
    tray_half_width: float = 300.0,
    safety_margin: float = 50.0,
) -> AvoidanceResult:
    """
    3순위: OBB 최소 마진 외곽 우회 (90도 직교).
    OBB 꼭짓점 + 트레이폭 + 안전마진에 바짝 밀착시켜 최단 직교 우회선 형성.
    """
    obs_corners = _obb_corners(obs)
    margin = tray_half_width + safety_margin

    # 세그먼트 방향 파악
    seg_dir = seg_end - seg_start
    # 주 이동 축 결정 (가장 큰 성분)
    dominant_axis = int(np.argmax(np.abs(seg_dir)))
    side_axis = 1 if dominant_axis == 0 else 0  # XY 평면 우회

    # OBB 두 측면 중 세그먼트에 더 가까운 측 선택
    obs_side_min = float(obs_corners[:, side_axis].min()) - margin
    obs_side_max = float(obs_corners[:, side_axis].max()) + margin

    # 현재 세그먼트의 side_axis 값
    seg_side = seg_start[side_axis]

    # 더 가까운 측으로 우회
    if abs(seg_side - obs_side_min) <= abs(seg_side - obs_side_max):
        bypass_side = obs_side_min
    else:
        bypass_side = obs_side_max

    # OBB 앞뒤 (dominant_axis 기준)
    obs_dom_min = float(obs_corners[:, dominant_axis].min()) - margin
    obs_dom_max = float(obs_corners[:, dominant_axis].max()) + margin

    # 4개 직교 웨이포인트: 진입 → 측면 이동 → 전진 → 복귀
    wp1 = seg_start.copy(); wp1[side_axis] = bypass_side
    wp2 = wp1.copy();       wp2[dominant_axis] = obs_dom_max
    wp3 = seg_end.copy();   wp3[side_axis] = bypass_side
    wp4 = seg_end.copy()

    waypoints = [wp1, wp2, wp3, wp4]

    logger.debug(
        "[Collision] 외곽 우회: 측면축=%d, bypass_side=%.1f",
        side_axis, bypass_side,
    )
    return AvoidanceResult(
        strategy=AvoidanceStrategy.LATERAL_BYPASS,
        waypoints=waypoints,
        success=True,
        vertical_bends_used=0,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 통합 회피 전략 실행
# ─────────────────────────────────────────────────────────────────────────────

def resolve_collision(
    collision: CollisionResult,
    seg_start: np.ndarray,
    seg_end: np.ndarray,
    remaining_vertical_bends: int,
    tray_half_width: float = 300.0,
    tray_height: float = 100.0,
    safety_margin: float = 50.0,
) -> AvoidanceResult:
    """
    충돌 결과에 대해 3대 회피 전략을 순차적으로 시도한다.

    우선순위:
      1. 슬리브 터널링
      2. 수직 오버패스 (잔여 bend 여유 있을 때)
      3. 수직 언더패스 (오버패스 거리 열위 시)
      4. 외곽 우회 (최후 수단)
    """
    obs = collision.obstacle
    if obs is None:
        return AvoidanceResult(
            strategy=AvoidanceStrategy.LATERAL_BYPASS,
            waypoints=[],
            success=False,
        )

    # 1순위: 슬리브 터널링
    result = strategy_1_sleeve_tunnel(obs, seg_start, seg_end)
    if result is not None:
        return result

    # 2순위: 수직 오버패스
    result = strategy_2_vertical_bypass(
        obs, seg_start, seg_end, remaining_vertical_bends,
        tray_height, safety_margin, prefer_over=True,
    )
    if result is not None:
        return result

    # 2순위 변형: 수직 언더패스
    result = strategy_2_vertical_bypass(
        obs, seg_start, seg_end, remaining_vertical_bends,
        tray_height, safety_margin, prefer_over=False,
    )
    if result is not None:
        return result

    # 3순위: 외곽 우회
    return strategy_3_lateral_bypass(obs, seg_start, seg_end, tray_half_width, safety_margin)
