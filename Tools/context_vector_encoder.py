"""
인덱싱과 검색 시점에서 공통으로 사용하는 30차원 장애물 Context Vector 인코더.

직접 실행하는 CLI 파일은 아니며 다음 도구에서 import한다.
- 전체 생성: ``python Tools/ExtractObstacleContextVector.py --config Tools/tools.settings.json run-all``
- 단위검증: ``python -m unittest discover -s Tools/tests -p "*_tests.py" -v``

전체 알고리즘 흐름
------------------
  [시작/종점 좌표] + [장애물 AABB 공간 인덱스]
             |
             +--> 각 끝점에서 AABB 표면거리 계산
             |       +-- near shell: 0~500 mm
             |       +-- mid shell : 500~1000 mm
             |       +-- 기둥/보별 개수와 최근접 방향 -> 끝점당 13차원
             |
             +--> 시작~종점 2D grid traversal
                     +-- 높이 변화, 기둥 점유, 보 평행성 -> Tier3 4차원
             |
             v
       [13 + 13 + 4 = 30차원] --> [L2 정규화]

500mm는 PoC 바로 주변의 출구/진입 제약을, 1000mm는 바깥 구조물과 여유 공간을
표현한다. 장애물 중심이 아니라 AABB 표면까지의 최단거리를 사용해 긴 장애물도 누락하지 않는다.
"""
from __future__ import annotations

import math
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence


# 저장 벡터와 query 벡터가 같은 레이아웃인지 검증하는 계약 버전.
ENCODER_VERSION = "topkgen-v3"
CONTEXT_SCOPE_KIND = "GLOBAL_SPATIAL_ALL_BAYS"
# 끝점 shell 경계(mm), 공간 인덱스 cell 크기(mm), 경로 표본 상한.
NEAR_RADIUS_MM = 500.0
MID_RADIUS_MM = 1000.0
GRID_CELL_MM = 1000.0
MAX_PATH_GRID_CELLS = 200
# 벡터 레이아웃: 시작 13 + 종점 13 + 경로 특성 4 = 총 30차원.
ENDPOINT_DIM = 13
TIER3_DIM = 4
CONTEXT_VECTOR_DIM = ENDPOINT_DIM * 2 + TIER3_DIM
ENCODER_CONFIG = {
    "version": ENCODER_VERSION,
    "dimension": CONTEXT_VECTOR_DIM,
    "near_radius_mm": NEAR_RADIUS_MM,
    "mid_radius_mm": MID_RADIUS_MM,
    "distance_metric": "point_to_aabb_surface",
    "endpoint_layout": "column6,beam6,empty_within_1000",
    "grid_cell_mm": GRID_CELL_MM,
    "max_path_grid_cells": MAX_PATH_GRID_CELLS,
    "scope_policy": CONTEXT_SCOPE_KIND.lower(),
}
ENCODER_CONFIG_HASH = hashlib.sha256(
    json.dumps(ENCODER_CONFIG, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


Point3 = tuple[float, float, float]


def clamp01(value: float) -> float:
    """정규화 feature를 0~1 구간으로 제한한다."""
    return max(0.0, min(1.0, value))


def _finite_point(point: Sequence[float]) -> Point3:
    """입력 좌표가 3차원 유한 실수인지 검증하고 tuple로 변환한다."""
    if len(point) != 3:
        raise ValueError(f"Expected a 3D point, got {len(point)} values")
    result = (float(point[0]), float(point[1]), float(point[2]))
    if not all(math.isfinite(v) for v in result):
        raise ValueError(f"Point contains a non-finite value: {point}")
    return result


@dataclass(frozen=True)
class Obstacle:
    """장애물 식별자, 종류와 AABB 최소/최대 좌표의 불변 표현."""
    obstacle_id: str
    kind: str
    minimum: Point3
    maximum: Point3

    def __post_init__(self) -> None:
        mn = _finite_point(self.minimum)
        mx = _finite_point(self.maximum)
        if any(mn[i] > mx[i] for i in range(3)):
            raise ValueError(f"Invalid AABB for obstacle {self.obstacle_id}: {mn}..{mx}")
        object.__setattr__(self, "minimum", mn)
        object.__setattr__(self, "maximum", mx)
        object.__setattr__(self, "kind", self.kind.upper())

    @property
    def center(self) -> Point3:
        return tuple((self.minimum[i] + self.maximum[i]) / 2.0 for i in range(3))  # type: ignore[return-value]

    @property
    def extent(self) -> Point3:
        return tuple(self.maximum[i] - self.minimum[i] for i in range(3))  # type: ignore[return-value]


@dataclass(frozen=True)
class NearbyObstacle:
    """query 점에서 장애물까지의 표면거리와 최근접점을 함께 보관한다."""
    distance: float
    closest: Point3
    obstacle: Obstacle


def closest_point_on_aabb(point: Sequence[float], obstacle: Obstacle) -> Point3:
    """각 축을 AABB 범위로 clamp하여 가장 가까운 AABB 점을 구한다."""
    p = _finite_point(point)
    return tuple(
        max(obstacle.minimum[i], min(p[i], obstacle.maximum[i])) for i in range(3)
    )  # type: ignore[return-value]


def point_aabb_distance(point: Sequence[float], obstacle: Obstacle) -> tuple[float, Point3]:
    """점-AABB 유클리드 최단거리와 최근접점을 반환한다. AABB 내부는 거리 0이다."""
    p = _finite_point(point)
    closest = closest_point_on_aabb(p, obstacle)
    distance = math.sqrt(sum((p[i] - closest[i]) ** 2 for i in range(3)))
    return distance, closest


class ObstacleIndex:
    """AABB가 겹치는 모든 3D cell에 등록하는 균일격자 공간 인덱스.

    중심 cell만 쓰지 않으므로 긴 장애물 표면이 반경에 들어오는 경우도 찾을 수 있다.
    ``grid``는 cell 좌표별 장애물 목록, ``obstacle_count``는 원본 장애물 수이다.
    """

    def __init__(self, obstacles: Iterable[Obstacle], cell_size: float = GRID_CELL_MM):
        if not math.isfinite(cell_size) or cell_size <= 0:
            raise ValueError("cell_size must be a positive finite value")
        self.cell_size = float(cell_size)
        self.grid: dict[tuple[int, int, int], list[Obstacle]] = defaultdict(list)
        self.obstacle_count = 0
        for obstacle in obstacles:
            self.obstacle_count += 1
            min_cell = self._cell(obstacle.minimum)
            max_cell = self._cell(obstacle.maximum)
            for x in range(min_cell[0], max_cell[0] + 1):
                for y in range(min_cell[1], max_cell[1] + 1):
                    for z in range(min_cell[2], max_cell[2] + 1):
                        self.grid[(x, y, z)].append(obstacle)

    def _cell(self, point: Sequence[float]) -> tuple[int, int, int]:
        p = _finite_point(point)
        return tuple(int(math.floor(v / self.cell_size)) for v in p)  # type: ignore[return-value]

    def query_radius(
        self, point: Sequence[float], radius: float, kind: str | None = None
    ) -> list[NearbyObstacle]:
        p = _finite_point(point)
        if not math.isfinite(radius) or radius < 0:
            raise ValueError("radius must be a non-negative finite value")
        lower = self._cell((p[0] - radius, p[1] - radius, p[2] - radius))
        upper = self._cell((p[0] + radius, p[1] + radius, p[2] + radius))
        expected_kind = kind.upper() if kind else None
        candidates: dict[str, Obstacle] = {}
        for x in range(lower[0], upper[0] + 1):
            for y in range(lower[1], upper[1] + 1):
                for z in range(lower[2], upper[2] + 1):
                    for obstacle in self.grid.get((x, y, z), ()):
                        if expected_kind is None or obstacle.kind == expected_kind:
                            candidates[obstacle.obstacle_id] = obstacle

        found: list[NearbyObstacle] = []
        for obstacle in candidates.values():
            distance, closest = point_aabb_distance(p, obstacle)
            if distance <= radius + 1e-9:
                found.append(NearbyObstacle(distance, closest, obstacle))
        found.sort(key=lambda item: (
            item.distance,
            *item.obstacle.minimum,
            *item.obstacle.maximum,
            item.obstacle.obstacle_id,
        ))
        return found


class MergedObstacleIndex:
    """여러 scope 인덱스를 obstacle ID 기준 중복 제거해 조회하는 읽기 전용 view."""

    def __init__(self, *indexes: ObstacleIndex):
        self.indexes = tuple(index for index in indexes if index is not None)
        self.obstacle_count = sum(index.obstacle_count for index in self.indexes)

    def query_radius(
        self, point: Sequence[float], radius: float, kind: str | None = None
    ) -> list[NearbyObstacle]:
        merged: dict[str, NearbyObstacle] = {}
        for index in self.indexes:
            for item in index.query_radius(point, radius, kind):
                previous = merged.get(item.obstacle.obstacle_id)
                if previous is None or item.distance < previous.distance:
                    merged[item.obstacle.obstacle_id] = item
        result = list(merged.values())
        result.sort(key=lambda item: (
            item.distance,
            *item.obstacle.minimum,
            *item.obstacle.maximum,
            item.obstacle.obstacle_id,
        ))
        return result


def _direction(point: Point3, nearby: NearbyObstacle) -> Point3:
    """끝점에서 가장 가까운 AABB 점으로 향하는 단위 방향벡터를 계산한다."""
    delta = tuple(nearby.closest[i] - point[i] for i in range(3))
    length = math.sqrt(sum(v * v for v in delta))
    if length <= 1e-9:
        return (0.0, 0.0, 0.0)
    return tuple(v / length for v in delta)  # type: ignore[return-value]


def encode_endpoint(idx, point: Sequence[float]) -> tuple[list[float], dict, list[Obstacle]]:
    """한 PoC 주변을 near/mid shell로 집계해 13차원 feature를 만든다.

    기둥/보 각각 6차원은 near 개수, mid 개수, 최근접 방향 XYZ, 정규화 거리이며,
    마지막 1차원은 1000mm 안에 기둥과 보가 모두 없는 free-space 표시이다.
    반환값은 ``(벡터, 진단 metadata, 근접 보 목록)``이다.
    """
    p = _finite_point(point)
    columns = idx.query_radius(p, MID_RADIUS_MM, "COLUMN")
    beams = idx.query_radius(p, MID_RADIUS_MM, "BEAM")
    vector = [0.0] * ENDPOINT_DIM

    def fill(offset: int, items: list[NearbyObstacle], count_scale: float) -> None:
        near_count = sum(item.distance <= NEAR_RADIUS_MM + 1e-9 for item in items)
        mid_count = len(items) - near_count
        vector[offset] = clamp01(near_count / count_scale)
        vector[offset + 1] = clamp01(mid_count / count_scale)
        if items:
            nearest = items[0]
            direction = _direction(p, nearest)
            vector[offset + 2 : offset + 5] = direction
            vector[offset + 5] = clamp01(nearest.distance / MID_RADIUS_MM)

    fill(0, columns, 8.0)
    fill(6, beams, 5.0)
    vector[12] = 1.0 if not columns and not beams else 0.0
    meta = {
        "near_radius_mm": NEAR_RADIUS_MM,
        "mid_radius_mm": MID_RADIUS_MM,
        "column_near_count": sum(item.distance <= NEAR_RADIUS_MM + 1e-9 for item in columns),
        "column_mid_count": sum(item.distance > NEAR_RADIUS_MM + 1e-9 for item in columns),
        "nearest_column_surface_mm": round(columns[0].distance, 3) if columns else None,
        "beam_near_count": sum(item.distance <= NEAR_RADIUS_MM + 1e-9 for item in beams),
        "beam_mid_count": sum(item.distance > NEAR_RADIUS_MM + 1e-9 for item in beams),
        "nearest_beam_surface_mm": round(beams[0].distance, 3) if beams else None,
        "empty_within_1000": not columns and not beams,
    }
    return vector, meta, [item.obstacle for item in beams]


def line_grid_cells(start: Sequence[float], end: Sequence[float]) -> list[tuple[int, int]]:
    """2D voxel traversal로 시작~종점 선분이 통과하는 XY cell을 순서대로 반환한다."""
    a, b = _finite_point(start), _finite_point(end)
    x0 = a[0] / GRID_CELL_MM
    y0 = a[1] / GRID_CELL_MM
    x1 = b[0] / GRID_CELL_MM
    y1 = b[1] / GRID_CELL_MM
    cx, cy = math.floor(x0), math.floor(y0)
    end_x, end_y = math.floor(x1), math.floor(y1)
    cells = [(int(cx), int(cy))]
    dx, dy = x1 - x0, y1 - y0
    step_x = 1 if dx > 0 else -1 if dx < 0 else 0
    step_y = 1 if dy > 0 else -1 if dy < 0 else 0
    t_delta_x = abs(1.0 / dx) if dx else math.inf
    t_delta_y = abs(1.0 / dy) if dy else math.inf
    next_x = cx + 1 if step_x > 0 else cx
    next_y = cy + 1 if step_y > 0 else cy
    t_max_x = (next_x - x0) / dx if dx else math.inf
    t_max_y = (next_y - y0) / dy if dy else math.inf

    while (cx, cy) != (end_x, end_y):
        if t_max_x < t_max_y:
            cx += step_x
            t_max_x += t_delta_x
        elif t_max_y < t_max_x:
            cy += step_y
            t_max_y += t_delta_y
        else:
            cx += step_x
            cy += step_y
            t_max_x += t_delta_x
            t_max_y += t_delta_y
        cells.append((int(cx), int(cy)))
    return cells


def _limit_cells(cells: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """긴 경로의 계산량을 제한하면서 처음/끝을 포함해 cell을 균등 표본화한다."""
    if len(cells) <= MAX_PATH_GRID_CELLS:
        return cells
    last = len(cells) - 1
    indexes = [round(i * last / (MAX_PATH_GRID_CELLS - 1)) for i in range(MAX_PATH_GRID_CELLS)]
    return [cells[index] for index in indexes]


def encode_tier3(
    idx,
    start: Sequence[float],
    end: Sequence[float],
    beams_near_start: Iterable[Obstacle],
    beams_near_end: Iterable[Obstacle],
) -> tuple[list[float], dict]:
    """경로 구간의 높이변화, 기둥 점유, 보 평행성, 진행방향을 4차원으로 만든다.

    ``column_cell_count``는 chord 표본 cell 중 기둥 근접 cell 수이고,
    ``beam_parallelism``은 경로 진행방향과 근접 보 장축의 평행 정도이다.
    """
    a, b = _finite_point(start), _finite_point(end)
    z_levels = {round(a[2] / 500.0) * 500.0, round(b[2] / 500.0) * 500.0}
    level_change_raw = len(z_levels) - 1
    level_change = clamp01(level_change_raw / 3.0)

    dx, dy = b[0] - a[0], b[1] - a[1]
    horizontal_length = math.hypot(dx, dy)
    cells = _limit_cells(line_grid_cells(a, b))
    average_z = (a[2] + b[2]) / 2.0
    column_cell_count = 0
    for cx, cy in cells:
        cell_center = ((cx + 0.5) * GRID_CELL_MM, (cy + 0.5) * GRID_CELL_MM, average_z)
        if idx.query_radius(cell_center, GRID_CELL_MM * 0.6, "COLUMN"):
            column_cell_count += 1
    column_grid_score = clamp01(column_cell_count / 15.0)

    unique_beams = {
        obstacle.obstacle_id: obstacle
        for obstacle in (*tuple(beams_near_start), *tuple(beams_near_end))
    }
    parallel_scores: list[float] = []
    if horizontal_length > 1e-9:
        ux, uy = dx / horizontal_length, dy / horizontal_length
        for obstacle in unique_beams.values():
            extent = obstacle.extent
            axis = max(range(3), key=lambda i: extent[i])
            if axis == 0:
                parallel_scores.append(abs(ux))
            elif axis == 1:
                parallel_scores.append(abs(uy))
    beam_parallelism = sum(parallel_scores) / len(parallel_scores) if parallel_scores else 0.0
    bearing_cos = dx / horizontal_length if horizontal_length > 1e-9 else 0.0
    vector = [level_change, column_grid_score, beam_parallelism, bearing_cos]
    meta = {
        "level_change": level_change_raw,
        "column_grid_cells": column_cell_count,
        "sampled_grid_cells": len(cells),
        "beam_parallelism": round(beam_parallelism, 6),
        "bearing_cos": round(bearing_cos, 6),
    }
    return vector, meta


def l2_normalize(vector: Sequence[float]) -> list[float]:
    """cosine 비교를 위해 L2 norm을 1로 정규화하고 NaN/무한대를 차단한다."""
    values = [float(value) for value in vector]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Context vector contains a non-finite value")
    norm = math.sqrt(sum(value * value for value in values))
    return values if norm <= 1e-12 else [value / norm for value in values]


def encode_context_vector(
    idx, start: Sequence[float], end: Sequence[float]
) -> tuple[list[float], dict]:
    """시작 13D, 종점 13D, Tier3 4D를 결합·정규화해 최종 30D를 반환한다."""
    start_vector, start_meta, start_beams = encode_endpoint(idx, start)
    end_vector, end_meta, end_beams = encode_endpoint(idx, end)
    tier3_vector, tier3_meta = encode_tier3(idx, start, end, start_beams, end_beams)
    vector = l2_normalize(start_vector + end_vector + tier3_vector)
    if len(vector) != CONTEXT_VECTOR_DIM:
        raise AssertionError(f"Expected {CONTEXT_VECTOR_DIM} dimensions, got {len(vector)}")
    return vector, {"start": start_meta, "end": end_meta, "tier3": tier3_meta}
