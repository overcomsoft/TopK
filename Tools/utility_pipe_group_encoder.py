#!/usr/bin/env python3
"""UtilityPipeGroup의 결정론적 집계 Vector와 배치 통계를 계산한다.

이 모듈은 DB에 의존하지 않는다. BuildUtilityPipeGroupVectors.py가 조회한 멤버를
정렬하고, 30D centroid·배치 통계·stable ID·source hash를 생성할 때 사용한다.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from typing import Any, Iterable, Sequence


VECTOR_DIM = 30
ENCODER_VERSION = "utility-pipe-group-v1"
ARRANGEMENT_VERSION = "utility-pipe-group-arrangement-v1"
DEFAULT_ENCODER_CONFIG = {
    "vector_dimension": VECTOR_DIM,
    "member_order": "route_path_guid_ascending",
    "centroid": "arithmetic_mean_then_l2_normalize",
    "context_compatibility": "same_scope_revision_encoder_version_config_hash",
    "arrangement_version": ARRANGEMENT_VERSION,
    "aabb_source": "route_segment_endpoints_with_route_endpoint_fallback",
}


def normalize_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_equipment_key(value: Any) -> str:
    text = normalize_text(value).upper()
    text = re.sub(r"[\s_\-]+$", "", text)
    return re.sub(r"\s+", "", text)


def normalize_size(value: Any) -> str:
    text = normalize_text(value).upper().replace(" ", "")
    if not text:
        return "UNKNOWN"
    match = re.fullmatch(r"0*(\d+(?:\.\d+)?)A", text)
    if not match:
        return text
    number = float(match.group(1))
    return f"{int(number)}A" if number.is_integer() else f"{number:g}A"


def parse_vector(value: Any, dimension: int = VECTOR_DIM) -> list[float] | None:
    """pgvector 문자열, JSON 문자열 또는 숫자 배열을 유한 실수 배열로 변환한다."""
    if value is None:
        return None
    parsed = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        parsed = json.loads(text)
    if not isinstance(parsed, (list, tuple)) or len(parsed) != dimension:
        return None
    result = [float(item) for item in parsed]
    return result if all(math.isfinite(item) for item in result) else None


def l2_normalize(values: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(float(value) ** 2 for value in values))
    if norm <= 1e-15:
        return [0.0 for _ in values]
    return [float(value) / norm for value in values]


def normalized_centroid(vectors: Iterable[Sequence[float]], dimension: int = VECTOR_DIM) -> list[float] | None:
    rows = [list(vector) for vector in vectors]
    if not rows:
        return None
    if any(len(row) != dimension for row in rows):
        raise ValueError(f"모든 Vector는 {dimension}차원이어야 합니다.")
    mean = [sum(row[index] for row in rows) / len(rows) for index in range(dimension)]
    return l2_normalize(mean)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def encoder_config(min_members: int) -> dict[str, Any]:
    return {**DEFAULT_ENCODER_CONFIG, "min_members": int(min_members)}


def make_group_vector_id(identity: dict[str, str]) -> str:
    return "upg_" + sha256_json(identity)


def deterministic_members(members: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(members, key=lambda item: normalize_text(item.get("route_path_guid")))


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    average = _mean(values)
    return math.sqrt(sum((value - average) ** 2 for value in values) / len(values))


def _axis_stats(points: Sequence[Sequence[float]]) -> dict[str, list[float]]:
    return {
        "mean": [_mean([point[axis] for point in points]) for axis in range(3)],
        "std": [_std([point[axis] for point in points]) for axis in range(3)],
    }


def _distance_stats(points: Sequence[Sequence[float]]) -> dict[str, float]:
    distances = [
        math.dist(points[left], points[right])
        for left in range(len(points))
        for right in range(left + 1, len(points))
    ]
    if not distances:
        return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0}
    return {
        "mean": _mean(distances),
        "std": _std(distances),
        "min": min(distances),
        "max": max(distances),
    }


def build_arrangement(members: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """멤버 순서와 무관한 시작/종점·변위·간격·길이·AABB 통계를 만든다."""
    starts = [[float(value) for value in member["start_xyz"]] for member in members]
    ends = [[float(value) for value in member["end_xyz"]] for member in members]
    displacements = [
        [end[axis] - start[axis] for axis in range(3)]
        for start, end in zip(starts, ends)
    ]
    lengths = [float(member.get("total_length_mm") or 0.0) for member in members]
    steps = [float(member.get("step_count") or 0) for member in members]

    all_points: list[list[float]] = []
    for member, start, end in zip(members, starts, ends):
        points = member.get("geometry_points") or []
        all_points.extend([[float(value) for value in point] for point in points])
        if not points:
            all_points.extend([start, end])
    mins = [min(point[axis] for point in all_points) for axis in range(3)]
    maxs = [max(point[axis] for point in all_points) for axis in range(3)]

    sizes = Counter(normalize_size(member.get("size")) for member in members)
    return {
        "version": ARRANGEMENT_VERSION,
        "member_count": len(members),
        "size_signature": dict(sorted(sizes.items())),
        "start": _axis_stats(starts),
        "end": _axis_stats(ends),
        "displacement": _axis_stats(displacements),
        "start_pairwise_distance_mm": _distance_stats(starts),
        "end_pairwise_distance_mm": _distance_stats(ends),
        "length_mm": {"mean": _mean(lengths), "std": _std(lengths)},
        "step_count": {"mean": _mean(steps), "std": _std(steps)},
        "aabb": {"min": mins, "max": maxs, "size": [maxs[i] - mins[i] for i in range(3)]},
    }


def centroid(points: Sequence[Sequence[float]]) -> list[float]:
    return [_mean([float(point[axis]) for point in points]) for axis in range(3)]


def vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(value):.12g}" for value in vector) + "]"


def source_hash_payload(
    identity: dict[str, str],
    members: Sequence[dict[str, Any]],
    config_hash: str,
) -> dict[str, Any]:
    """시각/실행 ID를 제외하고 결과에 영향을 주는 입력만 Source Hash에 포함한다."""
    return {
        "identity": identity,
        "encoder_config_hash": config_hash,
        "members": [
            {
                "route_path_guid": normalize_text(member.get("route_path_guid")),
                "size": normalize_size(member.get("size")),
                "start_xyz": member.get("start_xyz"),
                "end_xyz": member.get("end_xyz"),
                "direction_pattern": normalize_text(member.get("direction_pattern")),
                "total_length_mm": float(member.get("total_length_mm") or 0.0),
                "step_count": int(member.get("step_count") or 0),
                "feature_vector": member.get("feature_vector"),
                "feature_provenance": normalize_text(member.get("feature_provenance")),
                "context_vector": member.get("context_vector"),
                "context_provenance": normalize_text(member.get("context_provenance")),
                "geometry_points": member.get("geometry_points") or [],
            }
            for member in deterministic_members(members)
        ],
    }
