"""
================================================================================
topology_matcher.py  ─  맵 위상(Topology) 유사도 매칭 및 Case 분류 모듈
================================================================================

【실행 명령어】
  ※ 독립 테스트:
      cd RubberBandRouter
      python -m pytest tests/test_topology.py -v

================================================================================
【단계별 흐름도】

  입력: current_map(ObstacleMap), legacy_maps{pid: ObstacleMap}
  │
  ├─ 1단계: 코사인 유사도 1차 필터 (전체 레거시 맵 대상)
  │    compute_cosine_similarity(current_tensor, legacy_tensor)
  │    │
  │    ├─ 텐서 크기 다를 경우 제로 패딩으로 동일 크기 맞춤
  │    ├─ flat = tensor.flatten()                    # 1D 벡터화
  │    ├─ 두 텐서 모두 0이면 → similarity = 1.0 (빈 공간 = 완전 일치)
  │    └─ cosine_sim = dot(A, B) / (norm(A) * norm(B))
  │
  │    filter_top_k_candidates(current_tensor, legacy_tensors, top_k=5)
  │    → 상위 TOP_K_LEGACY_CANDIDATES 개 (pid, cosine_sim) 목록
  │
  ├─ 2단계: OBB 정밀 매칭 (Top-K 후보 대상)
  │    _obb_match_score(current_map, legacy_map)
  │    │
  │    ├─ 부피 기준 대형 장비만 추출 (top 5개)
  │    │    big_obbs = sorted(obs, key=volume, desc)[:5]
  │    ├─ 각 현재 대형 장비 ↔ 레거시 대형 장비 중심점 거리 계산
  │    │    dist = norm(center_current - center_legacy)
  │    │    부피 유사도 = min(vol_a, vol_b) / max(vol_a, vol_b)
  │    └─ score = mean(부피유사도 × exp(-dist/10000))   # 10m 감쇠
  │
  ├─ 3단계: 결합 점수 계산
  │    combined_score = 0.6 × cosine_sim + 0.4 × obb_score
  │    (코사인 유사도 60% 가중 + OBB 정밀 매칭 40% 가중)
  │
  └─ 4단계: Case 분류
       ┌─────────────────────────────────────────────┐
       │ combined_score ≥ 0.90 → Case A (완전 일치)  │
       │ combined_score ≥ 0.60 → Case B (부분 변동)  │
       │ combined_score < 0.60 → Case C (판이한 환경) │
       └─────────────────────────────────────────────┘

================================================================================
【핵심 알고리즘: 코사인 유사도】

  # 두 밀도 텐서(각 30×30×30 = 27,000 차원 이진 벡터)의 유사도
  A_flat = tensor_a.flatten().astype(float)    # (27000,)
  B_flat = tensor_b.flatten().astype(float)    # (27000,)

  dot_ab  = dot(A_flat, B_flat)                # 겹치는 셀 수
  norm_a  = sqrt(sum(A_flat²)) = sqrt(장애물_a 셀 수)
  norm_b  = sqrt(sum(B_flat²)) = sqrt(장애물_b 셀 수)

  cosine_similarity = dot_ab / (norm_a × norm_b)
  # = 0.0 (완전 다름) ~ 1.0 (완전 일치)

【OBB 정밀 매칭 가중치 함수】
  # 중심점 거리 d(mm), 부피 유사도 v ∈ [0,1]
  score_ij = v × exp(-d / 10000)
  # 10m(10000mm) 거리에서 가중치 ≈ 0.37 (1/e)
  # 5m 거리에서   ≈ 0.61
  # 1m 거리에서   ≈ 0.90

================================================================================
【주요 클래스 / 함수 / 변수】

  MatchResult                               매칭 결과 데이터 클래스
    .legacy_project_id  str   레거시 프로젝트 ID
    .cosine_similarity  float 1단계 코사인 유사도 (0~1)
    .obb_match_score    float 2단계 OBB 정밀 매칭 점수 (0~1)
    .combined_score     float 최종 결합 점수 (0.6×cos + 0.4×obb)
    .case               str   "A" | "B" | "C"

  compute_cosine_similarity()   두 밀도 텐서의 코사인 유사도 계산
  filter_top_k_candidates()     상위 K개 레거시 맵 후보 선정
  _obb_match_score()            대형 장비 OBB 중심점/부피 정밀 매칭
  match_legacy_map()            전체 2단계 매칭 파이프라인 실행
  classify_case()               combined_score → Case A/B/C 분류

  KEY VARIABLES:
    TOP_K_LEGACY_CANDIDATES  = cfg.TOP_K_LEGACY_CANDIDATES (=5)
    CASE_A_THRESHOLD         = cfg.TOPOLOGY_CASE_A_THRESHOLD (=0.90)
    CASE_B_THRESHOLD         = cfg.TOPOLOGY_CASE_B_THRESHOLD (=0.60)
    COSINE_WEIGHT            = 0.6    코사인 유사도 가중치
    OBB_WEIGHT               = 0.4    OBB 정밀 매칭 가중치
    DISTANCE_DECAY           = 10000  거리 감쇠 상수 (mm)

================================================================================
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy.spatial.distance import cosine

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 결과 데이터 클래스
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MatchResult:
    """위상 매칭 결과."""
    legacy_project_id: str
    cosine_similarity: float         # 1차 텐서 코사인 유사도
    obb_match_score: float           # 2차 OBB 정밀 매칭 점수 (0~1)
    combined_score: float            # 최종 통합 점수
    case: str                        # "A", "B", "C"

    def __str__(self) -> str:
        return (
            f"MatchResult(legacy={self.legacy_project_id}, "
            f"cosine={self.cosine_similarity:.3f}, "
            f"obb={self.obb_match_score:.3f}, "
            f"combined={self.combined_score:.3f}, "
            f"case={self.case})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 1단계: 코사인 유사도 기반 1차 필터
# ─────────────────────────────────────────────────────────────────────────────

def compute_cosine_similarity(tensor_a: np.ndarray, tensor_b: np.ndarray) -> float:
    """
    두 3D 밀도 텐서 간 코사인 유사도를 계산한다.

    텐서 형상이 다를 경우 작은 쪽에 맞춰 제로 패딩 후 비교.
    반환값: 0.0(완전 다름) ~ 1.0(완전 일치)
    """
    if tensor_a.shape != tensor_b.shape:
        target_shape = tuple(max(a, b) for a, b in zip(tensor_a.shape, tensor_b.shape))
        def pad_tensor(t: np.ndarray, shape: tuple) -> np.ndarray:
            padded = np.zeros(shape, dtype=t.dtype)
            slices = tuple(slice(0, s) for s in t.shape)
            padded[slices] = t
            return padded
        tensor_a = pad_tensor(tensor_a, target_shape)
        tensor_b = pad_tensor(tensor_b, target_shape)

    flat_a = tensor_a.flatten().astype(float)
    flat_b = tensor_b.flatten().astype(float)

    # 둘 다 비어있는 경우 처리
    if flat_a.sum() == 0 and flat_b.sum() == 0:
        return 1.0
    if flat_a.sum() == 0 or flat_b.sum() == 0:
        return 0.0

    similarity = 1.0 - float(cosine(flat_a, flat_b))
    return max(0.0, min(1.0, similarity))


def filter_top_k_candidates(
    current_map_tensor: np.ndarray,
    legacy_tensors: dict[str, np.ndarray],
    top_k: int = 5,
) -> list[tuple[str, float]]:
    """
    현재 맵 텐서와 레거시 맵 텐서들 간 코사인 유사도를 산출하여
    상위 K개의 (project_id, similarity) 후보 리스트를 반환한다.
    """
    scores: list[tuple[str, float]] = []
    for pid, tensor in legacy_tensors.items():
        sim = compute_cosine_similarity(current_map_tensor, tensor)
        scores.append((pid, sim))
        logger.debug("[TopologyMatcher] 1차 필터 - %s: cosine=%.4f", pid, sim)

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_k]


# ─────────────────────────────────────────────────────────────────────────────
# 2단계: OBB 정밀 매칭
# ─────────────────────────────────────────────────────────────────────────────

def _obb_match_score(
    current_obstacles: list,        # list[OBBObstacle]
    legacy_obstacles: list,         # list[OBBObstacle]
    volume_threshold: float = 1e7,  # 대형 장비 최소 부피 (mm³, 기본 1m³)
) -> float:
    """
    현재 맵과 레거시 맵의 대형 장비 OBB를 비교하여 정밀 매칭 점수를 산출한다.

    비교 기준:
      - 부피 유사도: |V_curr - V_leg| / max(V_curr, V_leg)
      - 중심점 거리: 유클리디안 거리를 공간 크기로 정규화
    두 항목 평균을 매칭 점수로 반환 (1.0 = 완전 일치).
    """
    # 대형 장비만 필터링
    curr_large = [o for o in current_obstacles if o.volume >= volume_threshold]
    leg_large  = [o for o in legacy_obstacles  if o.volume >= volume_threshold]

    if not curr_large or not leg_large:
        return 0.5  # 대형 장비 없으면 중간값

    # 가장 유사한 쌍 매칭 (그리디 근사)
    matched_scores: list[float] = []
    for c_obs in curr_large:
        best = -1.0
        for l_obs in leg_large:
            # 부피 유사도
            v_sim = 1.0 - abs(c_obs.volume - l_obs.volume) / max(c_obs.volume, l_obs.volume, 1.0)
            # 중심점 거리 (30,000mm 공간 정규화)
            dist = np.linalg.norm(c_obs.center - l_obs.center)
            d_sim = max(0.0, 1.0 - dist / 30_000.0)
            score = 0.5 * v_sim + 0.5 * d_sim
            if score > best:
                best = score
        matched_scores.append(best)

    return float(np.mean(matched_scores)) if matched_scores else 0.5


# ─────────────────────────────────────────────────────────────────────────────
# 3단계: Case 분류 및 최종 매칭
# ─────────────────────────────────────────────────────────────────────────────

def _classify_case(combined_score: float) -> str:
    """유사도 점수로 Case A/B/C를 분류한다."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config as cfg

    if combined_score >= cfg.TOPOLOGY_CASE_A_THRESHOLD:
        return "A"
    elif combined_score >= cfg.TOPOLOGY_CASE_B_THRESHOLD:
        return "B"
    else:
        return "C"


def match_legacy_map(
    current_map,           # ObstacleMap
    legacy_maps: dict,     # dict[str, ObstacleMap]
    top_k: int = 5,
    cosine_weight: float = 0.6,
    obb_weight: float = 0.4,
) -> MatchResult | None:
    """
    현재 맵과 레거시 맵들을 비교하여 최적 매칭 결과를 반환한다.

    Args:
        current_map:   현재 ObstacleMap (density_tensor 빌드 완료 상태)
        legacy_maps:   {project_id: ObstacleMap} 딕셔너리
        top_k:         1차 코사인 필터링 후 유지할 후보 수
        cosine_weight: 코사인 유사도 가중치
        obb_weight:    OBB 정밀 매칭 가중치

    Returns:
        최고 점수 MatchResult, 레거시 맵이 없으면 None
    """
    if not legacy_maps:
        logger.warning("[TopologyMatcher] 레거시 맵 없음 → Case C")
        return None

    if current_map.density_tensor is None:
        current_map.build_density_tensor()

    # 레거시 텐서 딕셔너리 준비
    legacy_tensors: dict[str, np.ndarray] = {}
    for pid, lmap in legacy_maps.items():
        if lmap.density_tensor is None:
            lmap.build_density_tensor()
        legacy_tensors[pid] = lmap.density_tensor

    # 1단계: 코사인 유사도 상위 K 후보
    candidates = filter_top_k_candidates(current_map.density_tensor, legacy_tensors, top_k)
    logger.info("[TopologyMatcher] 1차 필터 상위 %d 후보: %s", len(candidates), candidates)

    # 2단계: OBB 정밀 매칭
    results: list[MatchResult] = []
    for pid, cos_sim in candidates:
        lmap = legacy_maps[pid]
        obb_score = _obb_match_score(current_map.obstacles, lmap.obstacles)
        combined = cosine_weight * cos_sim + obb_weight * obb_score
        case = _classify_case(combined)
        mr = MatchResult(
            legacy_project_id=pid,
            cosine_similarity=cos_sim,
            obb_match_score=obb_score,
            combined_score=combined,
            case=case,
        )
        results.append(mr)
        logger.info("[TopologyMatcher] 2차 OBB 매칭 - %s", mr)

    if not results:
        return None

    best = max(results, key=lambda r: r.combined_score)
    logger.info("[TopologyMatcher] 최종 매칭: %s", best)
    return best
