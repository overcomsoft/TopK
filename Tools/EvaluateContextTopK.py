"""
장애물 Context가 Top-K 재정렬 품질에 주는 효과를 leave-one-out 방식으로 평가한다.

실행 방법(PowerShell)
---------------------
python Tools/EvaluateContextTopK.py --config Tools/tools.settings.json `
  --project-scope-key DB:DDW_AI_DB --model-revision-key snapshot:<sha256> `
  --output-json data/output/context_topk.json --output-md Docs/context_topk.md

전체 알고리즘 흐름
------------------
  [평가 route 1건을 query로 보류]
      -> C#과 같은 endpoint-only 30D query feature 생성
      -> feature cosine으로 1차 후보 pool 선택
      -> 자기 자신 GUID 제거(leave-one-out)
      -> Baseline score와 Feature+Context hybrid score 각각 재정렬
      -> 시작 출구축/종점 접근축/방향 pattern의 Top-K 적중률 비교
      -> coverage/provenance/성능 임계값으로 deployment gate 판정

두 cohort를 함께 보고한다.
- ``operational``: Context가 없는 후보는 baseline score로 fallback하는 실제 운영 조건
- ``indexed_only``: 두 arm 모두 Context가 있는 동일 후보만 사용하여 Context 품질만 분리

주요 변수
---------
- ``VECTOR_DIM``: 기존 설계 Feature Vector 차원(30)
- ``WEIGHT_MAP``: feature 구간별 reranking 가중치
- ``context_weight``: 최종 점수에서 Context cosine이 차지하는 비중
- ``candidate_pool``: 정확한 reranking 전에 ANN/feature로 좁힌 후보 수
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Iterable, Sequence

import tool_config
from context_vector_encoder import CONTEXT_SCOPE_KIND, ENCODER_CONFIG_HASH, ENCODER_VERSION


VECTOR_DIM = 30
REL_DIST_MAX_MM = 50_000.0
BBOX_MAX = (9759.011874999997, 11955.354296875, 11492.00024414066)
DISPLACEMENT_MAX = 11900.982486974623
TOTAL_LENGTH_MAX = 66433.582
WEIGHT_MAP = (
    (0, 3, 0.20),
    (3, 6, 0.20),
    (6, 9, 0.15),
    (9, 12, 0.15),
    (12, 15, 0.06),
    (15, 18, 0.06),
    (18, 21, 0.06),
    (21, 25, 0.12),
    (25, 30, 0.15),
)
BASE_WEIGHTS = (0.50, 0.30, 0.20)
DEFAULT_CONTEXT_WEIGHT = 0.10


@dataclass(frozen=True)
class Candidate:
    guid: str
    process: str
    equipment: str
    utility_group: str
    utility: str
    size: str
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    pattern: str
    feature: tuple[float, ...]
    context: tuple[float, ...] | None


@dataclass(frozen=True)
class Query:
    guid: str
    process: str
    equipment: str
    utility_group: str
    utility: str
    size: str
    bay: str
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    pattern: str
    actual_feature: tuple[float, ...]
    context: tuple[float, ...]


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: Candidate
    feature_cosine: float
    position_score: float
    context_cosine: float | None
    score: float


def _text(value) -> str:
    return str(value or "").strip()


def parse_vector(text: str | None, expected_dim: int = VECTOR_DIM) -> tuple[float, ...] | None:
    """PostgreSQL vector 문자열을 차원 검증된 float tuple로 변환한다."""
    if not text:
        return None
    parts = str(text).strip().strip("[]").split(",")
    if len(parts) != expected_dim:
        return None
    values = tuple(float(item) for item in parts)
    if not all(math.isfinite(value) for value in values):
        return None
    return values


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """두 벡터의 cosine similarity를 계산하며 영벡터는 0으로 처리한다."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na < 1e-12 or nb < 1e-12 else dot / (na * nb)


def _clamp11(value: float) -> float:
    return max(-1.0, min(1.0, value))


def build_query_vector30(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
) -> tuple[float, ...]:
    """시작/종점 좌표만 알려진 신규 요청을 production과 같은 30D query로 변환한다."""
    """Python mirror of TopKSearchStandalone.BuildQueryVector30D."""
    delta = tuple(end[i] - start[i] for i in range(3))
    length = math.sqrt(sum(value * value for value in delta))
    safe_length = length if length > 1e-9 else 1.0
    vector = [0.0] * VECTOR_DIM
    for i in range(3):
        vector[i] = delta[i] / safe_length
        vector[i + 3] = -vector[i]
        vector[i + 6] = _clamp11(delta[i] / DISPLACEMENT_MAX)
        vector[i + 9] = _clamp11(abs(delta[i]) / BBOX_MAX[i])
    vector[21] = _clamp11(length / TOTAL_LENGTH_MAX)

    scales = [1.0] * VECTOR_DIM
    for start_idx, end_idx, weight in WEIGHT_MAP:
        factor = math.sqrt(weight * VECTOR_DIM / (end_idx - start_idx))
        for i in range(start_idx, end_idx):
            scales[i] = factor
    vector = [value * scales[i] for i, value in enumerate(vector)]
    norm = math.sqrt(sum(value * value for value in vector))
    if norm > 1e-12:
        vector = [value / norm for value in vector]
    return tuple(vector)


def position_score(query: Query, candidate: Candidate) -> float:
    query_delta = tuple(query.end[i] - query.start[i] for i in range(3))
    candidate_delta = tuple(candidate.end[i] - candidate.start[i] for i in range(3))
    distance = math.sqrt(sum((query_delta[i] - candidate_delta[i]) ** 2 for i in range(3)))
    return max(0.0, 1.0 - distance / REL_DIST_MAX_MM)


def score_candidate(
    query: Query,
    candidate: Candidate,
    query_vector: Sequence[float],
    context_weight: float | None,
) -> ScoredCandidate:
    """feature/position/context 유사도를 가중 결합해 한 후보의 최종 점수를 계산한다."""
    """Score one candidate, with baseline fallback when context is missing."""
    feature_cosine = cosine(query_vector, candidate.feature)
    pos = position_score(query, candidate)
    # Production preset searches do not infer a query direction pattern, so the
    # pattern term is zero unless an explicit hint is provided.
    baseline = BASE_WEIGHTS[0] * pos + BASE_WEIGHTS[2] * feature_cosine
    context_cosine = None
    score = baseline
    if context_weight is not None and candidate.context is not None:
        context_cosine = max(0.0, cosine(query.context, candidate.context))
        score = (1.0 - context_weight) * baseline + context_weight * context_cosine
    return ScoredCandidate(candidate, feature_cosine, pos, context_cosine, score)


def axis_label(vector: Sequence[float], offset: int) -> str | None:
    values = vector[offset : offset + 3]
    if len(values) < 3:
        return None
    axis = max(range(3), key=lambda i: abs(values[i]))
    if abs(values[axis]) < 1e-9:
        return None
    return ("+" if values[axis] >= 0 else "-") + "xyz"[axis]


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return fmean(items) if items else None


def ranking_metrics(query: Query, ranked: Sequence[ScoredCandidate], k: int) -> dict[str, float | None]:
    """held-out route의 축/방향 proxy label 기준 Top-1/Top-K 지표를 계산한다."""
    top = list(ranked[:k])
    if not top:
        return {}
    query_start_axis = axis_label(query.actual_feature, 0)
    query_end_axis = axis_label(query.actual_feature, 3)

    def matches(item: ScoredCandidate) -> tuple[bool, bool, bool, bool]:
        start_match = axis_label(item.candidate.feature, 0) == query_start_axis
        end_match = axis_label(item.candidate.feature, 3) == query_end_axis
        pattern_match = bool(query.pattern) and item.candidate.pattern == query.pattern
        return start_match, end_match, start_match and end_match, pattern_match

    flags = [matches(item) for item in top]
    return {
        "start_axis_at1": float(flags[0][0]),
        "end_axis_at1": float(flags[0][1]),
        "both_axes_at1": float(flags[0][2]),
        "pattern_at1": float(flags[0][3]),
        "start_axis_at_k": fmean(float(flag[0]) for flag in flags),
        "end_axis_at_k": fmean(float(flag[1]) for flag in flags),
        "both_axes_at_k": fmean(float(flag[2]) for flag in flags),
        "pattern_at_k": fmean(float(flag[3]) for flag in flags),
        "feature_cosine_at_k": fmean(item.feature_cosine for item in top),
        "position_score_at_k": fmean(item.position_score for item in top),
        "context_cosine_at_k": _mean(
            item.context_cosine for item in top if item.context_cosine is not None
        ),
    }


def aggregate_metrics(rows: Sequence[dict[str, float | None]]) -> dict[str, float | int | None]:
    keys = sorted({key for row in rows for key in row})
    result: dict[str, float | int | None] = {"queries": len(rows)}
    for key in keys:
        values = [float(row[key]) for row in rows if row.get(key) is not None]
        result[key] = fmean(values) if values else None
    return result


def load_data(conn) -> tuple[list[Candidate], list[Query], dict[str, int]]:
    import psycopg2.extras

    context_join = f"""
        LEFT JOIN "TB_ROUTE_CONTEXT_VECTOR" cv
          ON TRIM(cv."ROUTE_PATH_GUID") = TRIM(fv."ROUTE_PATH_GUID")
         AND cv."ENCODER_VERSION" = '{ENCODER_VERSION}'
         AND cv."ENCODER_CONFIG_HASH" = '{ENCODER_CONFIG_HASH}'
         AND cv."SCOPE_KIND" = '{CONTEXT_SCOPE_KIND}'
    """
    candidate_sql = f"""
        SELECT TRIM(fv."ROUTE_PATH_GUID") AS guid,
               COALESCE(TRIM(fv."PROCESS_NAME"), '') AS process,
               COALESCE(TRIM(fv."EQUIPMENT_NAME"), '') AS equipment,
               COALESCE(TRIM(fv."UTILITY_GROUP"), '') AS utility_group,
               COALESCE(TRIM(fv."UTILITY"), '') AS utility,
               COALESCE(TRIM(fv."SIZE"), '') AS size,
               COALESCE(fv."START_POSX", 0) AS sx,
               COALESCE(fv."START_POSY", 0) AS sy,
               COALESCE(fv."START_POSZ", 0) AS sz,
               COALESCE(fv."END_POSX", 0) AS ex,
               COALESCE(fv."END_POSY", 0) AS ey,
               COALESCE(fv."END_POSZ", 0) AS ez,
               COALESCE(TRIM(fv."DIRECTION_PATTERN"), '') AS pattern,
               fv."FEATURE_VECTOR"::text AS feature_text,
               cv."CONTEXT_VECTOR"::text AS context_text
        FROM "TB_ROUTE_FEATURE_VECTOR" fv
        {context_join}
        WHERE fv."FEATURE_VECTOR" IS NOT NULL
          AND fv."ROUTE_PATH_GUID" IS NOT NULL
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(candidate_sql)
        raw_candidates = cursor.fetchall()

    candidates: list[Candidate] = []
    invalid_feature = 0
    for row in raw_candidates:
        feature = parse_vector(row["feature_text"])
        if feature is None:
            invalid_feature += 1
            continue
        candidates.append(Candidate(
            guid=_text(row["guid"]),
            process=_text(row["process"]),
            equipment=_text(row["equipment"]),
            utility_group=_text(row["utility_group"]),
            utility=_text(row["utility"]),
            size=_text(row["size"]),
            start=(float(row["sx"]), float(row["sy"]), float(row["sz"])),
            end=(float(row["ex"]), float(row["ey"]), float(row["ez"])),
            pattern=_text(row["pattern"]),
            feature=feature,
            context=parse_vector(row["context_text"]),
        ))

    query_sql = f"""
        SELECT TRIM(rp."ROUTE_PATH_GUID") AS guid,
               COALESCE(TRIM(rp."PROCESS_NAME"), '') AS process,
               COALESCE(TRIM(rp."EQUIPMENT_NAME"), '') AS equipment,
               COALESCE(TRIM(rp."UTILITY_GROUP"), '') AS utility_group,
               COALESCE(TRIM(rp."SOURCE_UTILITY"), '') AS utility,
               COALESCE(TRIM(rp."SOURCE_SIZE"), '') AS size,
               COALESCE(TRIM(rp."BAY"), '') AS bay,
               rp."SOURCE_POSX" AS sx, rp."SOURCE_POSY" AS sy, rp."SOURCE_POSZ" AS sz,
               rp."TARGET_POSX" AS ex, rp."TARGET_POSY" AS ey, rp."TARGET_POSZ" AS ez,
               COALESCE(TRIM(fv."DIRECTION_PATTERN"), '') AS pattern,
               fv."FEATURE_VECTOR"::text AS feature_text,
               cv."CONTEXT_VECTOR"::text AS context_text
        FROM "TB_ROUTE_PATH" rp
        JOIN "TB_ROUTE_FEATURE_VECTOR" fv
          ON TRIM(fv."ROUTE_PATH_GUID") = TRIM(rp."ROUTE_PATH_GUID")
        JOIN "TB_ROUTE_CONTEXT_VECTOR" cv
          ON TRIM(cv."ROUTE_PATH_GUID") = TRIM(rp."ROUTE_PATH_GUID")
         AND cv."ENCODER_VERSION" = '{ENCODER_VERSION}'
         AND cv."ENCODER_CONFIG_HASH" = '{ENCODER_CONFIG_HASH}'
         AND cv."SCOPE_KIND" = '{CONTEXT_SCOPE_KIND}'
        WHERE rp."SOURCE_POSX" IS NOT NULL AND rp."SOURCE_POSY" IS NOT NULL AND rp."SOURCE_POSZ" IS NOT NULL
          AND rp."TARGET_POSX" IS NOT NULL AND rp."TARGET_POSY" IS NOT NULL AND rp."TARGET_POSZ" IS NOT NULL
          AND fv."FEATURE_VECTOR" IS NOT NULL
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(query_sql)
        raw_queries = cursor.fetchall()

    queries: list[Query] = []
    invalid_query = 0
    for row in raw_queries:
        feature = parse_vector(row["feature_text"])
        context = parse_vector(row["context_text"])
        if feature is None or context is None:
            invalid_query += 1
            continue
        queries.append(Query(
            guid=_text(row["guid"]),
            process=_text(row["process"]),
            equipment=_text(row["equipment"]),
            utility_group=_text(row["utility_group"]),
            utility=_text(row["utility"]),
            size=_text(row["size"]),
            bay=_text(row["bay"]),
            start=(float(row["sx"]), float(row["sy"]), float(row["sz"])),
            end=(float(row["ex"]), float(row["ey"]), float(row["ez"])),
            pattern=_text(row["pattern"]),
            actual_feature=feature,
            context=context,
        ))
    return candidates, queries, {
        "invalid_feature_rows": invalid_feature,
        "invalid_query_rows": invalid_query,
    }


def evaluate(
    candidates: Sequence[Candidate],
    queries: Sequence[Query],
    k: int = 5,
    fetch_n: int = 150,
    context_weights: Sequence[float] = (0.05, 0.10, 0.15, 0.20, 0.25),
) -> dict:
    groups: dict[tuple[str, str, str, str], list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        groups[(candidate.process, candidate.equipment, candidate.utility_group, candidate.utility)].append(candidate)

    operational_baseline: list[dict] = []
    operational_context: list[dict] = []
    operational_overlap: list[float] = []
    operational_coverage: list[float] = []
    operational_pools: list[tuple[Query, tuple[float, ...], list[Candidate]]] = []
    fair_pools: list[tuple[Query, tuple[float, ...], list[Candidate]]] = []
    skipped = defaultdict(int)

    for query in queries:
        pool = groups.get((query.process, query.equipment, query.utility_group, query.utility), [])
        if query.size:
            pool = [candidate for candidate in pool if candidate.size == query.size]
        if not pool:
            skipped["no_filter_match"] += 1
            continue

        query_vector = build_query_vector30(query.start, query.end)
        ann = sorted(pool, key=lambda candidate: cosine(query_vector, candidate.feature), reverse=True)
        ann = [candidate for candidate in ann if candidate.guid != query.guid][:fetch_n]
        if not ann:
            skipped["no_leave_one_out_candidate"] += 1
            continue

        baseline = sorted(
            (score_candidate(query, candidate, query_vector, None) for candidate in ann),
            key=lambda item: item.score,
            reverse=True,
        )
        context = sorted(
            (score_candidate(query, candidate, query_vector, DEFAULT_CONTEXT_WEIGHT) for candidate in ann),
            key=lambda item: item.score,
            reverse=True,
        )
        operational_baseline.append(ranking_metrics(query, baseline, k))
        operational_context.append(ranking_metrics(query, context, k))
        baseline_ids = {item.candidate.guid for item in baseline[:k]}
        context_ids = {item.candidate.guid for item in context[:k]}
        operational_overlap.append(len(baseline_ids & context_ids) / max(len(baseline_ids), 1))
        operational_coverage.append(sum(candidate.context is not None for candidate in ann) / len(ann))
        operational_pools.append((query, query_vector, ann))

        indexed = [candidate for candidate in ann if candidate.context is not None]
        if len(indexed) >= k:
            fair_pools.append((query, query_vector, indexed))
        else:
            skipped["indexed_candidates_lt_k"] += 1

    fair_baseline: list[dict] = []
    fair_context: list[dict] = []
    fair_overlap: list[float] = []
    operational_sweep: dict[str, dict] = {}
    sweep: dict[str, dict] = {}
    for weight in context_weights:
        operational_weight_metrics: list[dict] = []
        for query, query_vector, ann in operational_pools:
            ranked = sorted(
                (score_candidate(query, candidate, query_vector, weight) for candidate in ann),
                key=lambda item: item.score,
                reverse=True,
            )
            operational_weight_metrics.append(ranking_metrics(query, ranked, k))
        operational_sweep[f"{weight:.2f}"] = aggregate_metrics(operational_weight_metrics)

        weight_metrics: list[dict] = []
        for query, query_vector, indexed in fair_pools:
            ranked = sorted(
                (score_candidate(query, candidate, query_vector, weight) for candidate in indexed),
                key=lambda item: item.score,
                reverse=True,
            )
            weight_metrics.append(ranking_metrics(query, ranked, k))
        sweep[f"{weight:.2f}"] = aggregate_metrics(weight_metrics)

    for query, query_vector, indexed in fair_pools:
        baseline = sorted(
            (score_candidate(query, candidate, query_vector, None) for candidate in indexed),
            key=lambda item: item.score,
            reverse=True,
        )
        context = sorted(
            (score_candidate(query, candidate, query_vector, DEFAULT_CONTEXT_WEIGHT) for candidate in indexed),
            key=lambda item: item.score,
            reverse=True,
        )
        fair_baseline.append(ranking_metrics(query, baseline, k))
        fair_context.append(ranking_metrics(query, context, k))
        baseline_ids = {item.candidate.guid for item in baseline[:k]}
        context_ids = {item.candidate.guid for item in context[:k]}
        fair_overlap.append(len(baseline_ids & context_ids) / max(len(baseline_ids), 1))

    recommended_weight, recommended_metrics = max(
        operational_sweep.items(),
        key=lambda item: (
            item[1].get("both_axes_at1") or -1.0,
            item[1].get("feature_cosine_at_k") or -1.0,
        ),
    )
    indexed_count = sum(candidate.context is not None for candidate in candidates)
    return {
        "method": {
            "design": "leave-one-out",
            "k": k,
            "fetch_n": fetch_n,
            "default_context_weight": DEFAULT_CONTEXT_WEIGHT,
            "missing_context_policy": "baseline_fallback",
        },
        "dataset": {
            "feature_rows": len(candidates),
            "context_indexed_rows": indexed_count,
            "context_coverage": indexed_count / max(len(candidates), 1),
            "query_rows": len(queries),
        },
        "skipped": dict(skipped),
        "operational": {
            "baseline": aggregate_metrics(operational_baseline),
            "context_default": aggregate_metrics(operational_context),
            "top_k_overlap": _mean(operational_overlap),
            "candidate_context_coverage": _mean(operational_coverage),
            "weight_sweep": operational_sweep,
        },
        "indexed_only": {
            "baseline": aggregate_metrics(fair_baseline),
            "context_default": aggregate_metrics(fair_context),
            "top_k_overlap": _mean(fair_overlap),
            "weight_sweep": sweep,
        },
        "recommendation": {
            "context_weight": float(recommended_weight),
            "selection_metric": "operational.both_axes_at1",
            "metric_value": recommended_metrics.get("both_axes_at1"),
            "decision": "keep_current_weight"
            if abs(float(recommended_weight) - DEFAULT_CONTEXT_WEIGHT) < 1e-12
            else "change_weight",
        },
    }


def _pct(value) -> str:
    return "N/A" if value is None else f"{float(value):.1%}"


def _num(value) -> str:
    return "N/A" if value is None else f"{float(value):.4f}"


def evaluate_deployment_gate(
    report: dict,
    *,
    min_context_coverage: float = 0.99,
    min_candidate_coverage: float = 0.99,
    min_both_axes_gain_pp: float = 1.0,
    min_pattern_gain_pp: float = 1.0,
    max_feature_cosine_drop: float = 0.04,
    min_queries: int = 100,
    require_current_weight: bool = True,
) -> dict:
    """coverage, provenance, 품질 하락 한계를 적용해 배포 가능 여부와 실패 사유를 반환한다."""
    """Evaluate whether a Context Vector rebuild is safe to promote.

    The gate combines data-contract checks (coverage), proxy quality gains, and
    a guardrail against excessive loss of the original feature-vector signal.
    """
    dataset = report["dataset"]
    operational = report["operational"]
    baseline = operational["baseline"]
    context = operational["context_default"]

    def delta_pp(key: str) -> float | None:
        before, after = baseline.get(key), context.get(key)
        return None if before is None or after is None else (float(after) - float(before)) * 100.0

    feature_before = baseline.get("feature_cosine_at_k")
    feature_after = context.get("feature_cosine_at_k")
    feature_drop = (
        None
        if feature_before is None or feature_after is None
        else float(feature_before) - float(feature_after)
    )
    recommended = float(report["recommendation"]["context_weight"])
    current = float(report["method"]["default_context_weight"])

    checks = [
        {
            "name": "context_coverage",
            "value": float(dataset.get("context_coverage", 0.0)),
            "threshold": min_context_coverage,
            "operator": ">=",
        },
        {
            "name": "candidate_context_coverage",
            "value": float(operational.get("candidate_context_coverage") or 0.0),
            "threshold": min_candidate_coverage,
            "operator": ">=",
        },
        {
            "name": "both_axes_top1_gain_pp",
            "value": delta_pp("both_axes_at1"),
            "threshold": min_both_axes_gain_pp,
            "operator": ">=",
        },
        {
            "name": "pattern_top1_gain_pp",
            "value": delta_pp("pattern_at1"),
            "threshold": min_pattern_gain_pp,
            "operator": ">=",
        },
        {
            "name": "feature_cosine_drop",
            "value": feature_drop,
            "threshold": max_feature_cosine_drop,
            "operator": "<=",
        },
        {
            "name": "operational_queries",
            "value": int(context.get("queries", 0)),
            "threshold": min_queries,
            "operator": ">=",
        },
    ]
    if require_current_weight:
        checks.append(
            {
                "name": "recommended_weight_matches_current",
                "value": recommended,
                "threshold": current,
                "operator": "==",
            }
        )

    for check in checks:
        value, threshold, operator = check["value"], check["threshold"], check["operator"]
        if value is None:
            passed = False
        elif operator == ">=":
            passed = value >= threshold
        elif operator == "<=":
            passed = value <= threshold
        else:
            passed = abs(value - threshold) < 1e-12
        check["passed"] = passed

    failed = [check["name"] for check in checks if not check["passed"]]
    return {
        "status": "PASS" if not failed else "BLOCK",
        "checks": checks,
        "failed_checks": failed,
        "note": "Proxy-quality gate; final routing success requires production outcome labels.",
    }


def render_markdown(report: dict) -> str:
    dataset = report["dataset"]
    operational = report["operational"]
    fair = report["indexed_only"]
    lines = [
        "# Context Vector Top-K 3단계 평가 결과",
        "",
        "## 평가 방식",
        "",
        "- 자기 경로를 후보에서 제외한 leave-one-out 평가",
        f"- Top-{report['method']['k']}, ANN 후보 {report['method']['fetch_n']}건",
        "- 운영 코호트: 미색인 후보는 baseline 점수로 fallback",
        "- indexed-only 코호트: 같은 Context 색인 후보 집합에서 baseline/context 비교",
        "",
        "## 데이터 커버리지",
        "",
        f"- Feature Vector: {dataset['feature_rows']:,}건",
        f"- Context Vector: {dataset['context_indexed_rows']:,}건",
        f"- 전체 Context 커버리지: {_pct(dataset['context_coverage'])}",
        f"- 평가 쿼리: {dataset['query_rows']:,}건",
        "",
    ]

    def add_comparison(title: str, section: dict) -> None:
        base = section["baseline"]
        ctx = section["context_default"]
        lines.extend([
            f"## {title}",
            "",
            f"| 지표 | Baseline | Context {report['method']['default_context_weight']:.2f} | 변화 |",
            "|---|---:|---:|---:|",
        ])
        for key, label, percent in (
            ("both_axes_at1", "시작+종점 축 Top-1 일치", True),
            ("both_axes_at_k", "시작+종점 축 Top-K 평균 일치", True),
            ("pattern_at1", "방향 패턴 Top-1 일치", True),
            ("pattern_at_k", "방향 패턴 Top-K 평균 일치", True),
            ("feature_cosine_at_k", "Feature cosine@K", False),
            ("position_score_at_k", "Position score@K", False),
            ("context_cosine_at_k", "Context cosine@K", False),
        ):
            before, after = base.get(key), ctx.get(key)
            delta = None if before is None or after is None else after - before
            formatter = _pct if percent else _num
            delta_text = "N/A" if delta is None else (f"{delta * 100:+.1f}%p" if percent else f"{delta:+.4f}")
            lines.append(f"| {label} | {formatter(before)} | {formatter(after)} | {delta_text} |")
        lines.extend([
            "",
            f"- 평가 쿼리: {ctx.get('queries', 0):,}건",
            f"- Top-K 결과 overlap: {_pct(section.get('top_k_overlap'))}",
            "",
        ])

    add_comparison("운영 후보 전체", operational)
    add_comparison("Context 색인 후보만의 공정 비교", fair)
    def add_sweep(title: str, sweep: dict) -> None:
        lines.extend([
        f"## {title}",
        "",
        "| 가중치 | 쿼리 | 양끝축 Top-1 | 패턴 Top-1 | Context cosine@K | Feature cosine@K |",
        "|---:|---:|---:|---:|---:|---:|",
        ])
        for weight, metrics in sweep.items():
            lines.append(
                f"| {weight} | {metrics.get('queries', 0):,} | {_pct(metrics.get('both_axes_at1'))} | "
                f"{_pct(metrics.get('pattern_at1'))} | {_num(metrics.get('context_cosine_at_k'))} | "
                f"{_num(metrics.get('feature_cosine_at_k'))} |"
            )
        lines.append("")

    add_sweep("Context 가중치 sweep (운영 전체)", operational["weight_sweep"])
    add_sweep("Context 가중치 sweep (indexed-only)", fair["weight_sweep"])
    recommendation = report["recommendation"]
    lines.extend([
        "## 결론",
        "",
        f"- 운영 양끝축 Top-1 일치를 최대화하는 권장 Context 가중치: **{recommendation['context_weight']:.2f}**",
        f"- 선택 지표 값: {_pct(recommendation['metric_value'])}",
        f"- 결정: `{recommendation['decision']}`",
        "- 현재 전체 Context 커버리지가 낮으므로 미색인 후보의 baseline fallback을 유지한다.",
        "",
        "## 해석 주의",
        "",
        "- 현재 Context 커버리지는 전체 Feature 데이터의 일부이므로 운영 결과와 indexed-only 결과를 함께 봐야 한다.",
        "- 축/패턴 일치는 실제 설계 품질의 proxy이며, 최종 자동배관 성공률을 직접 의미하지 않는다.",
        "- Context cosine 개선과 Feature cosine 유지 사이의 균형으로 가중치를 결정한다.",
        "",
    ])
    gate = report.get("deployment_gate")
    if gate:
        lines.extend([
            "## 배포 품질 게이트",
            "",
            f"- 판정: **{gate['status']}**",
            "",
            "| 검사 | 값 | 기준 | 결과 |",
            "|---|---:|---:|:---:|",
        ])
        for check in gate["checks"]:
            value = check["value"]
            value_text = "N/A" if value is None else f"{value:.6g}"
            lines.append(
                f"| `{check['name']}` | {value_text} | {check['operator']} {check['threshold']:.6g} | "
                f"{'PASS' if check['passed'] else 'BLOCK'} |"
            )
        lines.extend(["", f"- 주의: {gate['note']}", ""])
    return "\n".join(lines)


def render_deployment_markdown(report: dict) -> str:
    """Render the current UTF-8 deployment-gate report."""
    dataset = report["dataset"]
    operational = report["operational"]
    baseline = operational["baseline"]
    context = operational["context_default"]
    gate = report["deployment_gate"]

    def delta(key: str, percent: bool = False) -> str:
        before, after = baseline.get(key), context.get(key)
        if before is None or after is None:
            return "N/A"
        value = after - before
        return f"{value * 100:+.2f}%p" if percent else f"{value:+.6f}"

    lines = [
        "# Context Vector 5단계 배포 품질 게이트",
        "",
        "## 평가 방법",
        "",
        f"- 자기 경로를 후보에서 제외한 leave-one-out, Top-{report['method']['k']}",
        f"- ANN 후보 수: {report['method']['fetch_n']}",
        f"- 운영 Context 가중치: {report['method']['default_context_weight']:.2f}",
        "- Context가 없는 후보는 baseline 가중치로 fallback",
        "",
        "## 데이터와 결과",
        "",
        f"- Feature Vector: {dataset['feature_rows']:,}건",
        f"- Context Vector: {dataset['context_indexed_rows']:,}건",
        f"- 전체 coverage: {dataset['context_coverage']:.1%}",
        f"- 운영 평가 쿼리: {context.get('queries', 0):,}건",
        f"- 후보 Context coverage: {operational['candidate_context_coverage']:.1%}",
        "",
        "| 지표 | Baseline | Context 0.10 | 변화 |",
        "|---|---:|---:|---:|",
        f"| 양끝축 Top-1 | {_pct(baseline.get('both_axes_at1'))} | {_pct(context.get('both_axes_at1'))} | {delta('both_axes_at1', True)} |",
        f"| 양끝축 Top-K 평균 | {_pct(baseline.get('both_axes_at_k'))} | {_pct(context.get('both_axes_at_k'))} | {delta('both_axes_at_k', True)} |",
        f"| 패턴 Top-1 | {_pct(baseline.get('pattern_at1'))} | {_pct(context.get('pattern_at1'))} | {delta('pattern_at1', True)} |",
        f"| 패턴 Top-K 평균 | {_pct(baseline.get('pattern_at_k'))} | {_pct(context.get('pattern_at_k'))} | {delta('pattern_at_k', True)} |",
        f"| Feature cosine@K | {_num(baseline.get('feature_cosine_at_k'))} | {_num(context.get('feature_cosine_at_k'))} | {delta('feature_cosine_at_k')} |",
        "",
        "## 가중치 Sweep",
        "",
        "| Context 가중치 | 양끝축 Top-1 | 패턴 Top-1 | Feature cosine@K |",
        "|---:|---:|---:|---:|",
    ]
    for weight, metrics in operational["weight_sweep"].items():
        lines.append(
            f"| {weight} | {_pct(metrics.get('both_axes_at1'))} | "
            f"{_pct(metrics.get('pattern_at1'))} | {_num(metrics.get('feature_cosine_at_k'))} |"
        )

    lines.extend([
        "",
        "## 배포 품질 게이트",
        "",
        f"최종 판정: **{gate['status']}**",
        "",
        "| 검사 | 값 | 기준 | 결과 |",
        "|---|---:|---:|:---:|",
    ])
    for check in gate["checks"]:
        value = check["value"]
        value_text = "N/A" if value is None else f"{value:.6g}"
        lines.append(
            f"| `{check['name']}` | {value_text} | {check['operator']} {check['threshold']:.6g} | "
            f"{'PASS' if check['passed'] else 'BLOCK'} |"
        )

    lines.extend([
        "",
        "## 해석 제한",
        "",
        "현재 게이트는 기존 경로의 축·패턴 일치도를 사용하는 proxy 품질 게이트다. "
        "최종 자동 라우팅 성공률, 충돌 수, 길이 및 bend 수는 실제 실행 결과 레이블을 수집한 뒤 별도 A/B로 검증해야 한다.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    """DB 로드, leave-one-out 평가, gate 판정, JSON/Markdown 출력을 수행한다."""
    parser = argparse.ArgumentParser(description="Evaluate Context Vector Top-K reranking")
    tool_config.add_common_args(parser)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--fetch-n", type=int, default=150)
    parser.add_argument("--limit", type=int, default=0, help="Limit held-out queries; 0 evaluates all")
    parser.add_argument("--context-weights", default="0.05,0.10,0.15,0.20,0.25")
    parser.add_argument("--output-json", default="data/output/context_topk_phase3_evaluation.json")
    parser.add_argument("--output-md", default="Docs/ContextVector_Phase3_TopK_Evaluation.md")
    parser.add_argument("--min-context-coverage", type=float, default=0.99)
    parser.add_argument("--min-candidate-coverage", type=float, default=0.99)
    parser.add_argument("--min-both-axes-gain-pp", type=float, default=1.0)
    parser.add_argument("--min-pattern-gain-pp", type=float, default=1.0)
    parser.add_argument("--max-feature-cosine-drop", type=float, default=0.04)
    parser.add_argument("--min-queries", type=int, default=100)
    parser.add_argument("--allow-weight-change", action="store_true")
    parser.add_argument("--enforce-gate", action="store_true", help="Exit 2 when the deployment gate blocks")
    args = parser.parse_args()
    if args.k < 1 or args.fetch_n < args.k:
        parser.error("Require 1 <= k <= fetch-n")

    weights = tuple(float(item.strip()) for item in args.context_weights.split(",") if item.strip())
    if any(weight < 0.0 or weight > 1.0 for weight in weights):
        parser.error("Context weights must be in [0,1]")

    runtime = tool_config.resolve_runtime(args)
    tool_config.print_runtime(runtime)
    import psycopg2

    with psycopg2.connect(runtime.conninfo) as conn:
        candidates, queries, load_stats = load_data(conn)
    if args.limit > 0:
        queries = queries[: args.limit]
    print(f"Loaded feature candidates={len(candidates)}, held-out queries={len(queries)}")
    report = evaluate(candidates, queries, args.k, args.fetch_n, weights)
    report["load_stats"] = load_stats
    report["deployment_gate"] = evaluate_deployment_gate(
        report,
        min_context_coverage=args.min_context_coverage,
        min_candidate_coverage=args.min_candidate_coverage,
        min_both_axes_gain_pp=args.min_both_axes_gain_pp,
        min_pattern_gain_pp=args.min_pattern_gain_pp,
        max_feature_cosine_drop=args.max_feature_cosine_drop,
        min_queries=args.min_queries,
        require_current_weight=not args.allow_weight_change,
    )

    json_path = Path(args.output_json).resolve()
    md_path = Path(args.output_md).resolve()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_deployment_markdown(report), encoding="utf-8")
    print(f"JSON: {json_path}")
    print(f"Markdown: {md_path}")
    print(f"Deployment gate: {report['deployment_gate']['status']}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if args.enforce_gate and report["deployment_gate"]["status"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
