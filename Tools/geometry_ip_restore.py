from __future__ import annotations

"""
ELBOW/밴딩 피팅의 입/출구 포트(P_in/P_out)를, 전후 직관 중심선을 연장한 가상 교차점(IP,
Intersection Point)으로 대체하는 공유 유틸리티.

배경 (Docs/BendFeaturePoint_Development_Plan.md 4.5절, 7.0절)
----------------------------------------------------------
기존 설계 데이터에서 엘보우는 입구 포트(FROM_POS)와 출구 포트(TO_POS) 두 점으로만 저장된다.
이 둘을 직선으로 이으면 실제로는 존재하지 않는 사선(대각선) 세그먼트가 생기고, 진짜 꺾임
지점(두 직관 중심선을 연장한 교차점)은 그보다 살짝 벗어난 곳에 있다. 이 모듈은
`Tools/ExtractStubPatterns.py::fetch_route_points()`에 이미 구현되어 있던 skew-line
최근접점(nearest point of two skew lines) 계산 알고리즘을 DB 조회와 분리된 순수 함수로
추출한 것이다. `Tools/ExtractBendFeaturePoints.py`가 이 모듈을 사용하며, 로직 드리프트를
막기 위해 향후 `ExtractStubPatterns.py`도 이 모듈을 import하도록 리팩터링하는 것을 권장한다
(별도 승인 필요 — BendFeaturePoint_Development_Plan.md 17절 6번).
"""

import math
from dataclasses import dataclass

Point3 = tuple[float, float, float]

# 두 직관을 연장했을 때의 최근접점 간 거리(skew distance)가 이 값을 넘으면 직교 꺾임으로
# 보기 어려운 비정형 구간으로 판단하고 IP 대체를 포기한다 (fetch_route_points()와 동일 기준).
DEFAULT_SKEW_DIST_THRESHOLD_MM = 500.0


@dataclass(frozen=True)
class RestoredVertex:
    """IP 복원을 거친 폴리라인의 정점 하나.

    is_elbow_restored_ip가 True이면 point는 실측 좌표가 아니라 전후 직관을 연장해 계산한
    가상 교차점이며, skew_dist_mm은 그 복원 시 두 직선이 완전히 교차하지 않고 벌어져 있던
    거리(작을수록 신뢰도가 높음)다.
    """
    point: Point3
    is_elbow_restored_ip: bool
    skew_dist_mm: float | None


def dist(a: Point3, b: Point3) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _sub(v1: Point3, v2: Point3) -> Point3:
    return (v1[0] - v2[0], v1[1] - v2[1], v1[2] - v2[2])


def _add(v1: Point3, v2: Point3) -> Point3:
    return (v1[0] + v2[0], v1[1] + v2[1], v1[2] + v2[2])


def _mult(v: Point3, s: float) -> Point3:
    return (v[0] * s, v[1] * s, v[2] * s)


def _dot(v1: Point3, v2: Point3) -> float:
    return sum(x * y for x, y in zip(v1, v2))


def _norm(v: Point3) -> Point3:
    length = math.sqrt(_dot(v, v))
    return _mult(v, 1.0 / length) if length > 1e-3 else v


def skew_line_nearest_points(p1: Point3, v1: Point3, p2: Point3, v2: Point3) -> tuple[Point3, Point3] | None:
    """두 직선 L1(t)=p1+t*v1, L2(s)=p2+s*v2의 최근접점 쌍(q1, q2)을 구한다.

    두 방향벡터가 (거의) 평행하면 교차점을 정의할 수 없으므로 None을 반환한다.
    """
    w0 = _sub(p1, p2)
    a_val = _dot(v1, v1)
    b_val = _dot(v1, v2)
    c_val = _dot(v2, v2)
    d_val = _dot(v1, w0)
    e_val = _dot(v2, w0)

    denom = a_val * c_val - b_val * b_val
    if denom <= 1e-6:
        return None

    t = (b_val * e_val - c_val * d_val) / denom
    s = (a_val * e_val - b_val * d_val) / denom
    q1 = _add(p1, _mult(v1, t))
    q2 = _add(p2, _mult(v2, s))
    return q1, q2


def restore_polyline_ip(
    raw_segments: list[dict],
    skew_dist_threshold_mm: float = DEFAULT_SKEW_DIST_THRESHOLD_MM,
) -> list[RestoredVertex]:
    """ORDER 순으로 정렬된 세그먼트 목록(FROM/TO/TYPE)에서 ELBOW 사선을 IP로 복원한 정점열을 만든다.

    raw_segments의 각 원소는 {'from': (x,y,z), 'to': (x,y,z), 'type': str} 형태여야 하며,
    TB_ROUTE_SEGMENTS.ORDER, TB_ROUTE_SEGMENT_DETAIL.ORDER 순서로 미리 정렬되어 있어야 한다.
    `ExtractStubPatterns.py::fetch_route_points()`와 동일한 알고리즘(skew-line 최근접점의
    중점을 IP로 채택, 500mm 이내 오차만 허용)을 사용하며, 그 결과와 100% 동일해야 한다.
    """
    if not raw_segments:
        return []

    verts: list[RestoredVertex] = [RestoredVertex(raw_segments[0]["from"], False, None)]

    i = 0
    n = len(raw_segments)
    while i < n:
        cur = raw_segments[i]
        cur_type = str(cur.get("type") or "").strip().upper()

        if cur_type == "ELBOW" and 0 < i < n - 1:
            prev_seg = raw_segments[i - 1]
            next_seg = raw_segments[i + 1]

            p1 = prev_seg["to"]
            p2 = next_seg["from"]
            v1 = _norm(_sub(prev_seg["to"], prev_seg["from"]))
            v2 = _norm(_sub(next_seg["to"], next_seg["from"]))

            nearest = skew_line_nearest_points(p1, v1, p2, v2)
            if nearest is not None:
                q1, q2 = nearest
                skew_dist = dist(q1, q2)
                if skew_dist < skew_dist_threshold_mm:
                    ip = _mult(_add(q1, q2), 0.5)
                    restored = RestoredVertex(ip, True, skew_dist)
                    if verts:
                        verts[-1] = restored
                    else:
                        verts.append(restored)
                    i += 1
                    continue

        to_pt = cur["to"]
        if not verts or dist(verts[-1].point, to_pt) > 1e-3:
            verts.append(RestoredVertex(to_pt, False, None))
        i += 1

    return verts
