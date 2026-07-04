"""
rubber_band.py
--------------
고무줄 변형 라우팅 엔진 - Pull(특징점 스냅) & Push(충돌 회피) 통합.

수행 단계:
  Step 1 - 초기 인장: S→D 직선 (시각화 디버거 Step 1)
  Step 2 - Pull: 특징점 스냅으로 1차 배관 뼈대 형성 (시각화 Step 2)
  Step 3 - Push: 현재 장애물 SAT 충돌 검사 및 3대 회피 (시각화 Step 3~4)
  최종 - 직교 세그먼트 경로 확정 반환

모든 세그먼트는 90도 직교 형태를 유지한다.
수직(Z) 꺾임은 MAX_VERTICAL_BENDS 횟수 내로 제한한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .obstacle_map import OBBObstacle, ObstacleMap
    from .feature_extractor import FeatureSet, Waypoint
    from .collision import CollisionResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 클래스
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RouteSegment:
    """직교 배관 경로 세그먼트."""
    start: np.ndarray    # [x, y, z]
    end: np.ndarray      # [x, y, z]

    @property
    def direction(self) -> np.ndarray:
        d = self.end - self.start
        n = np.linalg.norm(d)
        return d / n if n > 1e-9 else d

    @property
    def length(self) -> float:
        return float(np.linalg.norm(self.end - self.start))

    @property
    def is_vertical(self) -> bool:
        d = self.end - self.start
        return abs(d[2]) > max(abs(d[0]), abs(d[1]))

    def __repr__(self) -> str:
        s, e = self.start, self.end
        return (
            f"Seg([{s[0]:.0f},{s[1]:.0f},{s[2]:.0f}]"
            f"→[{e[0]:.0f},{e[1]:.0f},{e[2]:.0f}]"
            f", len={self.length:.0f})"
        )


@dataclass
class RubberBandState:
    """
    고무줄 변형 상태 스냅샷 - 4단계 시각화 디버거용.

    각 단계별 상태를 보존하여 슬라이더로 재생할 수 있게 한다.
    """
    step: int                                          # 1~4
    segments: list[RouteSegment] = field(default_factory=list)
    collision_points: list[np.ndarray] = field(default_factory=list)
    snap_waypoints: list[np.ndarray] = field(default_factory=list)
    vertical_bends_used: int = 0
    description: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# 직교 세그먼트 생성 유틸리티
# ─────────────────────────────────────────────────────────────────────────────

def make_orthogonal_segments(
    points: list[np.ndarray],
) -> list[RouteSegment]:
    """
    웨이포인트 목록을 직교 세그먼트 목록으로 변환한다.

    연속된 두 점 사이를 최대 2개 직교 세그먼트(L자형)로 분해한다.
    X/Y 방향 우선, 마지막에 Z 방향 처리.
    """
    segments: list[RouteSegment] = []
    if len(points) < 2:
        return segments

    for i in range(len(points) - 1):
        p1 = points[i].copy()
        p2 = points[i + 1].copy()
        delta = p2 - p1

        # 이미 단일 축 이동이면 바로 세그먼트 추가
        nonzero = np.where(np.abs(delta) > 1e-3)[0]
        if len(nonzero) <= 1:
            if len(nonzero) == 1:
                segments.append(RouteSegment(p1.copy(), p2.copy()))
            continue

        # 2축 이상 차이: 직교 분해 (X→Y→Z 순)
        mid1 = p1.copy()
        mid2 = p1.copy()

        if abs(delta[0]) > 1e-3:
            mid1[0] = p2[0]
            segments.append(RouteSegment(p1.copy(), mid1.copy()))
            if abs(delta[1]) > 1e-3:
                mid2 = mid1.copy()
                mid2[1] = p2[1]
                segments.append(RouteSegment(mid1.copy(), mid2.copy()))
                if abs(delta[2]) > 1e-3:
                    segments.append(RouteSegment(mid2.copy(), p2.copy()))
            elif abs(delta[2]) > 1e-3:
                segments.append(RouteSegment(mid1.copy(), p2.copy()))
        elif abs(delta[1]) > 1e-3:
            mid1[1] = p2[1]
            segments.append(RouteSegment(p1.copy(), mid1.copy()))
            if abs(delta[2]) > 1e-3:
                segments.append(RouteSegment(mid1.copy(), p2.copy()))
        else:
            segments.append(RouteSegment(p1.copy(), p2.copy()))

    return segments


def count_vertical_bends(segments: list[RouteSegment]) -> int:
    """세그먼트 목록에서 수직(Z) 꺾임 수를 계산한다."""
    count = 0
    prev_vertical = None
    for seg in segments:
        curr_vertical = seg.is_vertical
        if prev_vertical is not None and prev_vertical != curr_vertical:
            if curr_vertical:  # 수직으로 진입하는 꺾임
                count += 1
        prev_vertical = curr_vertical
    return count


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: 초기 인장 (S→D 직선)
# ─────────────────────────────────────────────────────────────────────────────

def step1_initial_tension(
    start: np.ndarray,
    end: np.ndarray,
) -> RubberBandState:
    """Step 1: 출발지~목적지 초기 직선 인장 상태 생성."""
    # 직교 분해된 직선 (L자형)
    segments = make_orthogonal_segments([start, end])
    state = RubberBandState(
        step=1,
        segments=segments,
        description="초기 인장: S→D 직선 (장애물 무시)",
    )
    logger.info("[RubberBand] Step 1 완료 - 세그먼트 %d개", len(segments))
    return state


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Pull - 특징점 스냅
# ─────────────────────────────────────────────────────────────────────────────

def step2_pull_snap(
    start: np.ndarray,
    end: np.ndarray,
    feature_set: "FeatureSet",
    snap_tolerance: float = 100.0,
) -> RubberBandState:
    """
    Step 2: 추출된 특징점으로 고무줄을 스냅(Snapping)시켜
    베테랑 설계자의 배관 동선 1차 뼈대를 형성한다.

    Case C(웨이포인트 없음)이면 직선 상태 그대로 반환.
    """
    if feature_set.is_empty():
        # Case C: 특징점 없음 → Step 1 상태 유지
        segments = make_orthogonal_segments([start, end])
        return RubberBandState(
            step=2,
            segments=segments,
            description="Case C: 레거시 특징점 없음 → 직선 유지",
        )

    # 특징점 우선순위 정렬
    waypoints_sorted = sorted(feature_set.waypoints, key=lambda w: w.priority)

    # 웨이포인트를 S→D 경로 방향 순으로 정렬 (진행 방향 투영값 기준)
    route_dir = end - start
    route_len = np.linalg.norm(route_dir)
    if route_len > 1e-9:
        route_unit = route_dir / route_len
        waypoints_sorted.sort(
            key=lambda w: float(np.dot(w.position - start, route_unit))
        )

    # 전체 경로 포인트: S → 웨이포인트들 → D
    all_points = [start] + [w.position for w in waypoints_sorted] + [end]

    # 직교 세그먼트 생성
    segments = make_orthogonal_segments(all_points)
    snap_positions = [w.position for w in waypoints_sorted]

    logger.info(
        "[RubberBand] Step 2 완료 - case=%s, 웨이포인트=%d, 세그먼트=%d",
        feature_set.case, len(waypoints_sorted), len(segments),
    )
    return RubberBandState(
        step=2,
        segments=segments,
        snap_waypoints=snap_positions,
        description=f"AI 스냅: Case {feature_set.case}, 웨이포인트 {len(waypoints_sorted)}개",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Push - 충돌 감지 및 회피
# ─────────────────────────────────────────────────────────────────────────────

def step3_push_resolve(
    state_step2: RubberBandState,
    current_obstacles: list["OBBObstacle"],
    max_vertical_bends: int = 5,
    tray_half_width: float = 300.0,
    tray_height: float = 100.0,
    safety_margin: float = 50.0,
) -> RubberBandState:
    """
    Step 3: 현재 장애물 맵과의 SAT 충돌 검사 후 3대 회피 전략 적용.
    충돌 세그먼트를 회피 웨이포인트로 교체하며 경로를 미세 조정한다.
    """
    from .collision import find_collisions, resolve_collision

    segments = list(state_step2.segments)
    collision_points: list[np.ndarray] = []
    vertical_bends_used = count_vertical_bends(segments)
    max_iterations = 20  # 무한루프 방지

    for iteration in range(max_iterations):
        seg_pairs = [(s.start, s.end) for s in segments]
        collisions = find_collisions(
            seg_pairs, current_obstacles, tray_half_width, safety_margin
        )

        if not collisions:
            break

        modified = False
        for seg_idx, col_result in collisions:
            if seg_idx >= len(segments):
                continue

            seg = segments[seg_idx]
            collision_points.append(col_result.collision_point)

            remaining_v = max_vertical_bends - vertical_bends_used
            avoidance = resolve_collision(
                col_result, seg.start, seg.end,
                remaining_v, tray_half_width, tray_height, safety_margin,
            )

            if not avoidance.success:
                logger.warning("[RubberBand] 회피 실패: seg_idx=%d", seg_idx)
                continue

            vertical_bends_used += avoidance.vertical_bends_used

            # 기존 세그먼트를 회피 경로로 교체
            new_points = [seg.start] + avoidance.waypoints + [seg.end]
            new_segs = make_orthogonal_segments(new_points)
            segments = segments[:seg_idx] + new_segs + segments[seg_idx + 1:]
            modified = True
            break  # 한 번에 하나씩 처리 후 재검사

        if not modified:
            break  # 더 이상 수정 불가

    logger.info(
        "[RubberBand] Step 3 완료 - 충돌점=%d, 수직꺾임=%d/%d, 최종세그먼트=%d",
        len(collision_points), vertical_bends_used, max_vertical_bends, len(segments),
    )
    return RubberBandState(
        step=3,
        segments=segments,
        collision_points=collision_points,
        snap_waypoints=state_step2.snap_waypoints,
        vertical_bends_used=vertical_bends_used,
        description=f"충돌 해소: {len(collision_points)}개 충돌점, 수직꺾임={vertical_bends_used}회",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 통합 라우팅 실행
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RoutingResult:
    """라우팅 최종 결과."""
    start: np.ndarray
    end: np.ndarray
    states: list[RubberBandState]   # Step 1~3 상태 목록 (디버거용)
    final_segments: list[RouteSegment]
    vertical_bends: int
    total_length: float

    @property
    def final_state(self) -> RubberBandState:
        return self.states[-1]

    def summary(self) -> str:
        return (
            f"RoutingResult: {len(self.final_segments)}개 세그먼트, "
            f"총길이={self.total_length:.0f}mm, "
            f"수직꺾임={self.vertical_bends}/{5}회"
        )


def run_routing(
    start: np.ndarray,
    end: np.ndarray,
    feature_set: "FeatureSet",
    current_map: "ObstacleMap",
    max_vertical_bends: int | None = None,
    tray_half_width: float | None = None,
    tray_height: float | None = None,
    safety_margin: float | None = None,
    snap_tolerance: float | None = None,
) -> RoutingResult:
    """
    전체 라우팅 파이프라인 실행.

    Args:
        start:          출발지 좌표 [x,y,z]
        end:            목적지 좌표 [x,y,z]
        feature_set:    특징점 추출 결과 (FeatureSet)
        current_map:    현재 장애물 맵 (ObstacleMap)
        나머지 파라미터: None이면 config.py 기본값 사용

    Returns:
        RoutingResult
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config as cfg

    max_v  = max_vertical_bends if max_vertical_bends is not None else cfg.MAX_VERTICAL_BENDS
    t_hw   = tray_half_width    if tray_half_width    is not None else cfg.TRAY_WIDTH / 2.0
    t_h    = tray_height        if tray_height        is not None else cfg.TRAY_HEIGHT
    s_mg   = safety_margin      if safety_margin      is not None else cfg.SAFETY_MARGIN
    snap_t = snap_tolerance     if snap_tolerance     is not None else cfg.SNAP_TOLERANCE

    states: list[RubberBandState] = []

    # Step 1: 초기 인장
    s1 = step1_initial_tension(start, end)
    states.append(s1)

    # Step 2: Pull - 특징점 스냅
    s2 = step2_pull_snap(start, end, feature_set, snap_t)
    states.append(s2)

    # Step 3: Push - 충돌 회피
    s3 = step3_push_resolve(s2, current_map.obstacles, max_v, t_hw, t_h, s_mg)
    states.append(s3)

    final_segments = s3.segments
    total_length = sum(seg.length for seg in final_segments)

    result = RoutingResult(
        start=start,
        end=end,
        states=states,
        final_segments=final_segments,
        vertical_bends=s3.vertical_bends_used,
        total_length=total_length,
    )
    logger.info("[RubberBand] 라우팅 완료 - %s", result.summary())
    return result
