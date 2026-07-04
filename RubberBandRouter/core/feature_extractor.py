"""
================================================================================
feature_extractor.py  ─  레거시 꺾임 특징점 추출 및 현재 공간 정규화 모듈
================================================================================

【실행 명령어】
  ※ 독립 테스트:
      cd RubberBandRouter
      python -m pytest tests/test_rubber_band.py::TestStep2PullSnap -v

================================================================================
【단계별 흐름도】

  입력: MatchResult.case(A/B/C), legacy_segments[], current_start, current_end
  │
  ├─ Case C (combined_score < 0.60) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  │    extract_features_case_c()
  │    → FeatureSet(case="C", waypoints=[])   # 빈 웨이포인트
  │    → rubber_band.py 에서 순수 기하학 자율 라우팅
  │
  ├─ Case B (0.60 ≤ score < 0.90) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  │    extract_features_case_b(legacy_segments, legacy_start, legacy_end, current_start, current_end)
  │    │
  │    ├─ 거시적 특징점만 선별:
  │    │    ① is_vertical = True (Z축 꺾임): 파이프 랙 Z-Level 통과점
  │    │    ② volume ≥ 1e10 mm³ 대형 장비 AABB 인접 꺾임점
  │    │
  │    └─ normalize_to_ratio() → ratio_to_current()
  │         선별된 엘보 좌표를 비율 정규화 후 현재 공간에 재투영
  │
  └─ Case A (score ≥ 0.90) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
       extract_features_case_a(legacy_segments, legacy_start, legacy_end, current_start, current_end)
       │
       ├─ 모든 레거시 엘보 좌표 추출 (POC/ELBOW 타입 세그먼트)
       └─ normalize_to_ratio() → ratio_to_current() 전체 적용

================================================================================
【핵심 알고리즘: 비율 정규화 투영 (Proportional Normalization)】

  레거시 공간의 좌표를 현재 공간에 비율로 재투영하여 좌표계 차이를 흡수한다.

  1) 레거시 공간에서 비율 계산 (각 축):
       Ratio_X = (x_old - x_start_old) / (x_end_old - x_start_old + ε)
       Ratio_Y = (y_old - y_start_old) / (y_end_old - y_start_old + ε)
       Ratio_Z = (z_old - z_start_old) / (z_end_old - z_start_old + ε)
       → ratio = [Ratio_X, Ratio_Y, Ratio_Z] ∈ 실수 (범위 제한 없음)

  2) 현재 공간에 재투영:
       x_new = x_start_new + Ratio_X × (x_end_new - x_start_new)
       y_new = y_start_new + Ratio_Y × (y_end_new - y_start_new)
       z_new = z_start_new + Ratio_Z × (z_end_new - z_start_new)
       → new_point = current_start + ratio * (current_end - current_start)

  이 공식으로 레거시 배관이 10m×5m 공간을 지나도,
  현재 8m×7m 공간이면 자동으로 비례 투영된다.

  예시:
    레거시: S=(0,0,0), D=(10000,5000,0), 엘보=(3000,2000,0)
    비율: [0.3, 0.4, 0.0]
    현재: S=(0,0,0), D=(8000,7000,0)
    재투영 엘보: (2400, 2800, 0)

================================================================================
【주요 클래스 / 함수 / 변수】

  Waypoint                            꺾임 웨이포인트 데이터 클래스
    .position   ndarray(3,)  월드 좌표 (mm)
    .priority   int          삽입 우선순위 (낮을수록 먼저 처리)
    .source     str          출처 설명 ("case_a_elbow", "case_b_rack_z" 등)

  FeatureSet                          추출된 특징점 집합
    .case       str          "A" | "B" | "C"
    .waypoints  list[Waypoint]  웨이포인트 목록 (Case C이면 빈 리스트)

  normalize_to_ratio()      레거시 좌표 → 비율 벡터 [Ratio_X, Ratio_Y, Ratio_Z]
  ratio_to_current()        비율 벡터 → 현재 공간 좌표
  extract_features_case_a() Case A: 전체 엘보 추출 + 비율 투영
  extract_features_case_b() Case B: 수직 꺾임 + 대형 장비 인접점만 추출
  extract_features_case_c() Case C: 빈 FeatureSet 반환
  extract_features()        통합 진입점 (case에 따라 내부 분기)

  KEY VARIABLES:
    LARGE_EQUIPMENT_VOLUME_THRESHOLD = 1e10  mm³ (대형 장비 판별 기준 = 10m³)
    ε (epsilon)                      = 1e-9  분모 0 방지용 미소값
    ratio                            ndarray(3,) 비율 벡터 (각 축 0.0~1.0 권장)

================================================================================
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 클래스
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Waypoint:
    """
    특징점(Waypoint) - 고무줄이 스냅(Snapping)될 3D 좌표.

    priority: 낮을수록 높은 우선순위
    source: "case_a_elbow", "case_b_rack_z", "case_b_bypass", 등
    """
    position: np.ndarray    # shape (3,) [x, y, z]
    priority: int = 0
    source: str = ""

    def __repr__(self) -> str:
        p = self.position
        return f"Waypoint({p[0]:.1f}, {p[1]:.1f}, {p[2]:.1f}, src={self.source})"


@dataclass
class LegacyElbow:
    """레거시 설계에서 추출된 엘보(꺾임점) 좌표."""
    position: np.ndarray    # [x, y, z] in legacy coordinate system
    direction_in: np.ndarray | None = None
    direction_out: np.ndarray | None = None
    is_vertical: bool = False
    segment_group: str = ""


@dataclass
class FeatureSet:
    """특징점 추출 결과 세트."""
    case: str                               # "A", "B", "C"
    waypoints: list[Waypoint] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def is_empty(self) -> bool:
        return len(self.waypoints) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 좌표 정규화 / 역정규화
# ─────────────────────────────────────────────────────────────────────────────

def normalize_to_ratio(
    point: np.ndarray,
    start_old: np.ndarray,
    end_old: np.ndarray,
) -> np.ndarray:
    """
    레거시 좌표를 출발-목적지 상대 비율로 정규화한다.

    각 축에서 분모가 0이면 0.5(중간)로 처리.
    """
    delta = end_old - start_old
    ratio = np.where(
        np.abs(delta) > 1e-9,
        (point - start_old) / delta,
        0.5,
    )
    return ratio  # shape (3,)


def ratio_to_current(
    ratio: np.ndarray,
    start_new: np.ndarray,
    end_new: np.ndarray,
) -> np.ndarray:
    """비율 값을 현재 공간 좌표로 역투영한다."""
    return start_new + ratio * (end_new - start_new)


# ─────────────────────────────────────────────────────────────────────────────
# DB에서 레거시 엘보 / 세그먼트 로드
# ─────────────────────────────────────────────────────────────────────────────

def load_legacy_elbows_from_db(
    conninfo: str,
    project_id: str,
) -> list[LegacyElbow]:
    """
    TB_GROUP_SEGMENTS 또는 TB_ROUTE_NODES에서 레거시 프로젝트의
    꺾임점(Elbow/Bend) 좌표를 로드한다.
    """
    import psycopg2
    import psycopg2.extras
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config as cfg

    elbows: list[LegacyElbow] = []

    # TB_GROUP_SEGMENTS에서 세그먼트 시작/끝으로부터 꺾임점 추출
    sql = f"""
        SELECT
            REP_X_START, REP_Y_START, REP_Z_START,
            REP_X_END,   REP_Y_END,   REP_Z_END,
            SEGMENT_DIRECTION,
            SEGMENT_GROUP_ID
        FROM {cfg.TABLE_GROUP_SEGMENTS}
        WHERE PROJECT_ID = %(pid)s
        ORDER BY SEGMENT_ORDER
    """
    try:
        with psycopg2.connect(conninfo) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, {"pid": project_id})
                rows = cur.fetchall()
    except Exception as exc:
        logger.warning("[FeatureExtractor] 레거시 세그먼트 로드 실패: %s", exc)
        return elbows

    if not rows:
        return elbows

    # 연속된 세그먼트의 끝점 = 꺾임점 (방향이 바뀌는 지점)
    prev_dir = None
    for row in rows:
        row = dict(row)
        direction = str(row.get("SEGMENT_DIRECTION") or row.get("segment_direction") or "")
        is_vertical = direction.upper() in ("Z", "VERTICAL", "UP", "DOWN")

        # 세그먼트 시작점도 웨이포인트 후보
        try:
            start = np.array([
                float(row.get("REP_X_START") or row.get("rep_x_start") or 0),
                float(row.get("REP_Y_START") or row.get("rep_y_start") or 0),
                float(row.get("REP_Z_START") or row.get("rep_z_start") or 0),
            ])
            end = np.array([
                float(row.get("REP_X_END") or row.get("rep_x_end") or 0),
                float(row.get("REP_Y_END") or row.get("rep_y_end") or 0),
                float(row.get("REP_Z_END") or row.get("rep_z_end") or 0),
            ])
        except (TypeError, ValueError):
            continue

        # 방향이 바뀌는 지점(엘보) → 세그먼트 시작점 등록
        if prev_dir and prev_dir != direction:
            elbow = LegacyElbow(
                position=start.copy(),
                is_vertical=is_vertical,
                segment_group=str(row.get("SEGMENT_GROUP_ID") or ""),
            )
            elbows.append(elbow)
        prev_dir = direction

        # 마지막 끝점도 등록
        elbow_end = LegacyElbow(
            position=end.copy(),
            is_vertical=is_vertical,
            segment_group=str(row.get("SEGMENT_GROUP_ID") or ""),
        )
        elbows.append(elbow_end)

    # 중복 제거 (동일 좌표)
    unique: list[LegacyElbow] = []
    seen: set[tuple] = set()
    for e in elbows:
        key = tuple(np.round(e.position, 0).astype(int))
        if key not in seen:
            seen.add(key)
            unique.append(e)

    logger.info(
        "[FeatureExtractor] 레거시 엘보 로드 완료: project=%s, 총=%d (중복제거 후=%d)",
        project_id, len(elbows), len(unique),
    )
    return unique


# ─────────────────────────────────────────────────────────────────────────────
# Case A: 전체 엘보 추출 + 정규화
# ─────────────────────────────────────────────────────────────────────────────

def extract_case_a(
    legacy_elbows: list[LegacyElbow],
    start_old: np.ndarray,
    end_old: np.ndarray,
    start_new: np.ndarray,
    end_new: np.ndarray,
) -> FeatureSet:
    """
    Case A: 레거시 엘보 전체를 현재 공간으로 정규화·재투영한다.
    """
    waypoints: list[Waypoint] = []
    for i, elbow in enumerate(legacy_elbows):
        ratio = normalize_to_ratio(elbow.position, start_old, end_old)
        # 비율이 [0,1] 범위를 크게 벗어나면 경로 외부 → 스킵
        if np.any(ratio < -0.1) or np.any(ratio > 1.1):
            continue
        projected = ratio_to_current(ratio, start_new, end_new)
        wp = Waypoint(
            position=projected,
            priority=i,
            source="case_a_elbow",
        )
        waypoints.append(wp)

    logger.info("[FeatureExtractor] Case A: 웨이포인트 %d개 생성", len(waypoints))
    return FeatureSet(case="A", waypoints=waypoints, metadata={"elbow_count": len(legacy_elbows)})


# ─────────────────────────────────────────────────────────────────────────────
# Case B: 거시적 특징점만 추출
# ─────────────────────────────────────────────────────────────────────────────

def extract_case_b(
    legacy_elbows: list[LegacyElbow],
    legacy_obstacles: list,         # list[OBBObstacle]
    start_old: np.ndarray,
    end_old: np.ndarray,
    start_new: np.ndarray,
    end_new: np.ndarray,
    volume_threshold: float = 1e8,  # 대형 장비 최소 부피 (mm³, 기본 10m³)
) -> FeatureSet:
    """
    Case B: 메인 파이프 랙 Z-Level 및 거대 장비 우회 시작/끝점만 추출.
    소형 간섭물 영향이 없는 거시적 특징점만 활용한다.
    """
    waypoints: list[Waypoint] = []

    # 수직(Z) 방향 꺾임 엘보만 → 파이프 랙 고도 특징점
    z_levels: list[float] = []
    for elbow in legacy_elbows:
        if elbow.is_vertical:
            ratio = normalize_to_ratio(elbow.position, start_old, end_old)
            if np.any(ratio < -0.1) or np.any(ratio > 1.1):
                continue
            projected = ratio_to_current(ratio, start_new, end_new)
            wp = Waypoint(position=projected, priority=10, source="case_b_rack_z")
            waypoints.append(wp)
            z_levels.append(elbow.position[2])

    # 대형 장비 우회 시작/끝점 (OBB AABB 기준)
    for obs in legacy_obstacles:
        if obs.volume < volume_threshold:
            continue
        verts = obs.vertices
        bypass_start = np.array([verts[:, 0].min(), verts[:, 1].min(), verts[:, 2].min()])
        bypass_end   = np.array([verts[:, 0].max(), verts[:, 1].max(), verts[:, 2].max()])
        for pos, src in [(bypass_start, "case_b_bypass_start"), (bypass_end, "case_b_bypass_end")]:
            ratio = normalize_to_ratio(pos, start_old, end_old)
            if np.any(ratio < -0.1) or np.any(ratio > 1.1):
                continue
            projected = ratio_to_current(ratio, start_new, end_new)
            waypoints.append(Waypoint(position=projected, priority=20, source=src))

    # 우선순위 정렬
    waypoints.sort(key=lambda w: w.priority)
    logger.info(
        "[FeatureExtractor] Case B: 웨이포인트 %d개 생성 (Z-levels=%d, 대형장비=%d)",
        len(waypoints),
        sum(1 for w in waypoints if "rack_z" in w.source),
        sum(1 for w in waypoints if "bypass" in w.source),
    )
    return FeatureSet(case="B", waypoints=waypoints, metadata={"z_levels": z_levels})


# ─────────────────────────────────────────────────────────────────────────────
# Case C: 면제 (빈 FeatureSet 반환)
# ─────────────────────────────────────────────────────────────────────────────

def extract_case_c() -> FeatureSet:
    """Case C: 레거시 데이터 없이 순수 기하학 자율 라우팅."""
    logger.info("[FeatureExtractor] Case C: 레거시 특징점 없음 → 자율 라우팅 모드")
    return FeatureSet(case="C", waypoints=[], metadata={})


# ─────────────────────────────────────────────────────────────────────────────
# 통합 진입점
# ─────────────────────────────────────────────────────────────────────────────

def extract_features(
    match_result,               # MatchResult (topology_matcher.py)
    legacy_map,                 # ObstacleMap (None이면 Case C 강제)
    legacy_elbows: list,        # list[LegacyElbow]
    start_old: np.ndarray,      # 레거시 출발지
    end_old: np.ndarray,        # 레거시 목적지
    start_new: np.ndarray,      # 현재 출발지 (S)
    end_new: np.ndarray,        # 현재 목적지 (D)
) -> FeatureSet:
    """
    매칭 결과(Case A/B/C)에 따라 적절한 특징점 추출 함수를 호출한다.
    """
    if match_result is None or legacy_map is None:
        return extract_case_c()

    case = match_result.case

    if case == "A":
        return extract_case_a(
            legacy_elbows, start_old, end_old, start_new, end_new
        )
    elif case == "B":
        return extract_case_b(
            legacy_elbows, legacy_map.obstacles,
            start_old, end_old, start_new, end_new
        )
    else:
        return extract_case_c()
