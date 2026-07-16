#!/usr/bin/env python3
"""UtilityPipeGroup Top-K 단계 0 데이터 프로파일러.

실행 예:
  python Tools/ProfileUtilityPipeGroups.py --config Tools/tools.settings.json
  python Tools/ProfileUtilityPipeGroups.py --config Tools/tools.settings.json \
      --markdown-out Docs/UtilityPipeGroup_TopK_Phase0_Data_Profile.md \
      --json-out data/output/utility_pipe_group_profile.json

DB를 변경하지 않는 읽기 전용 도구다. 실제 컬럼을 information_schema에서 먼저 확인한 뒤
장비 + Utility Group + Utility 그룹 수, Size 동질성, Feature/Context/상세경로 연결률을 계산한다.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ModuleNotFoundError:  # 순수 집계 단위 테스트는 DB 드라이버 없이 실행할 수 있다.
    psycopg2 = None
    RealDictCursor = None

import tool_config


@dataclass(frozen=True)
class ActiveScope:
    project: str = ""
    revision: str = ""
    status: str = "UNSCOPED"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UtilityPipeGroup Top-K 단계 0 데이터 프로파일링")
    tool_config.add_common_args(parser)
    parser.add_argument("--scope-mode", choices=("active", "all"), default="active")
    parser.add_argument("--min-members", type=int, default=2)
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--markdown-out", default=None)
    parser.add_argument("--json-out", default=None)
    return parser.parse_args()


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def normalize_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_equipment_key(value: Any) -> str:
    """WTNHJ02와 WTNHJ02_처럼 후행 구분자만 다른 표기를 동일 장비로 정규화한다."""
    text = normalize_text(value).upper()
    text = re.sub(r"[\s_\-]+$", "", text)
    return re.sub(r"\s+", "", text)


def normalize_size(value: Any) -> str:
    text = normalize_text(value).upper().replace(" ", "")
    if not text:
        return "UNKNOWN"
    match = re.fullmatch(r"0*(\d+(?:\.\d+)?)A", text)
    if match:
        number = float(match.group(1))
        return f"{int(number)}A" if number.is_integer() else f"{number:g}A"
    return text


def resolve_table(conn, requested: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT table_name FROM information_schema.tables
               WHERE table_schema=current_schema() AND upper(table_name)=upper(%s)
               ORDER BY CASE WHEN table_name=%s THEN 0 ELSE 1 END LIMIT 1""",
            (requested, requested),
        )
        row = cur.fetchone()
    return row[0] if row else None


def table_columns(conn, table: str | None) -> dict[str, str]:
    if not table:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position""",
            (table,),
        )
        return {row[0].upper(): row[0] for row in cur.fetchall()}


def choose_column(columns: dict[str, str], *candidates: str) -> str | None:
    for candidate in candidates:
        if candidate.upper() in columns:
            return columns[candidate.upper()]
    return None


def text_expr(alias: str, columns: dict[str, str], *candidates: str) -> str:
    available = [columns[c.upper()] for c in candidates if c.upper() in columns]
    if not available:
        return "''"
    parts = [f"NULLIF(BTRIM({alias}.{quote_ident(col)}::text),'')" for col in available]
    return "COALESCE(" + ",".join(parts) + ",'')"


def value_expr(alias: str, columns: dict[str, str], candidate: str) -> str:
    col = columns.get(candidate.upper())
    return f"{alias}.{quote_ident(col)}" if col else "NULL"


def resolve_active_scope(conn) -> ActiveScope:
    table = resolve_table(conn, "TB_ROUTE_SOURCE_SCOPE_MANIFEST")
    columns = table_columns(conn, table)
    required = {"PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY", "STATUS"}
    if not table or not required.issubset(columns):
        return ActiveScope(status="MANIFEST_UNAVAILABLE")

    sql = (
        f"SELECT {quote_ident(columns['PROJECT_SCOPE_KEY'])},"
        f"{quote_ident(columns['MODEL_REVISION_KEY'])} FROM {quote_ident(table)} "
        f"WHERE {quote_ident(columns['STATUS'])}='ACTIVE'"
    )
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    if len(rows) == 1:
        return ActiveScope(normalize_text(rows[0][0]), normalize_text(rows[0][1]), "ACTIVE")
    return ActiveScope(status=f"ACTIVE_COUNT_{len(rows)}")


def scope_where(columns: dict[str, str], alias: str, scope: ActiveScope, scope_mode: str) -> tuple[str, list[Any]]:
    if scope_mode != "active" or scope.status != "ACTIVE":
        return "", []
    project_col = columns.get("PROJECT_SCOPE_KEY")
    revision_col = columns.get("MODEL_REVISION_KEY")
    if not project_col or not revision_col:
        return "", []
    return (
        f" AND {alias}.{quote_ident(project_col)}=%s AND {alias}.{quote_ident(revision_col)}=%s",
        [scope.project, scope.revision],
    )


def fetch_route_rows(conn, scope: ActiveScope, scope_mode: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    table = resolve_table(conn, "TB_ROUTE_PATH")
    columns = table_columns(conn, table)
    guid_col = choose_column(columns, "ROUTE_PATH_GUID")
    if not table or not guid_col:
        raise RuntimeError("TB_ROUTE_PATH.ROUTE_PATH_GUID가 필요합니다.")

    equipment_tag_expr = text_expr("p", columns, "EQUIPMENT_TAG")
    equipment_name_expr = text_expr("p", columns, "EQUIPMENT_NAME", "SOURCE_OWNER_NAME")
    sql = f"""
        SELECT
          {text_expr('p', columns, 'ROUTE_PATH_GUID')} AS route_path_guid,
          {text_expr('p', columns, 'PROCESS_NAME')} AS process_name,
          {equipment_tag_expr} AS equipment_tag,
          {equipment_name_expr} AS equipment_name,
          {text_expr('p', columns, 'UTILITY_GROUP')} AS utility_group,
          {text_expr('p', columns, 'SOURCE_UTILITY', 'UTILITY')} AS utility,
          {text_expr('p', columns, 'SOURCE_SIZE', 'SIZE')} AS size,
          {value_expr('p', columns, 'SOURCE_POSX')} AS sx,
          {value_expr('p', columns, 'SOURCE_POSY')} AS sy,
          {value_expr('p', columns, 'SOURCE_POSZ')} AS sz,
          {value_expr('p', columns, 'TARGET_POSX')} AS ex,
          {value_expr('p', columns, 'TARGET_POSY')} AS ey,
          {value_expr('p', columns, 'TARGET_POSZ')} AS ez
        FROM {quote_ident(table)} p
        WHERE {quote_ident(guid_col)} IS NOT NULL
    """
    where, params = scope_where(columns, "p", scope, scope_mode)
    sql += where
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = [dict(row) for row in cur.fetchall()]

    schema = {
        "table": table,
        "equipment_tag_column": choose_column(columns, "EQUIPMENT_TAG"),
        "equipment_name_column": choose_column(columns, "EQUIPMENT_NAME", "SOURCE_OWNER_NAME"),
        "utility_column": choose_column(columns, "SOURCE_UTILITY", "UTILITY"),
        "size_column": choose_column(columns, "SOURCE_SIZE", "SIZE"),
        "scope_columns_present": bool(columns.get("PROJECT_SCOPE_KEY") and columns.get("MODEL_REVISION_KEY")),
    }
    return rows, schema


def fetch_guid_set(conn, requested_table: str, scope: ActiveScope, scope_mode: str) -> tuple[set[str], dict[str, Any]]:
    table = resolve_table(conn, requested_table)
    columns = table_columns(conn, table)
    guid_col = choose_column(columns, "ROUTE_PATH_GUID")
    if not table or not guid_col:
        return set(), {"table": table, "available": False, "scope_filtered": False}

    sql = f"SELECT DISTINCT {quote_ident(guid_col)}::text FROM {quote_ident(table)} t WHERE {quote_ident(guid_col)} IS NOT NULL"
    where, params = scope_where(columns, "t", scope, scope_mode)
    sql += where
    with conn.cursor() as cur:
        cur.execute(sql, params)
        values = {normalize_text(row[0]) for row in cur.fetchall() if normalize_text(row[0])}
    return values, {
        "table": table,
        "available": True,
        "scope_filtered": bool(where),
        "row_guid_count": len(values),
    }


def fetch_geometry_guid_set(conn) -> tuple[set[str], list[dict[str, Any]]]:
    all_guids: set[str] = set()
    sources: list[dict[str, Any]] = []
    for requested in ("TB_ROUTE_SEGMENTS", "TB_ROUTE_PATH_SEGMENT_MAP", "TB_ROUTE_SEGMENT_DETAIL"):
        table = resolve_table(conn, requested)
        columns = table_columns(conn, table)
        guid_col = choose_column(columns, "ROUTE_PATH_GUID")
        if not table or not guid_col:
            sources.append({"table": table or requested, "route_guid_column": None, "guid_count": 0})
            continue
        sql = f"SELECT DISTINCT {quote_ident(guid_col)}::text FROM {quote_ident(table)} WHERE {quote_ident(guid_col)} IS NOT NULL"
        with conn.cursor() as cur:
            cur.execute(sql)
            values = {normalize_text(row[0]) for row in cur.fetchall() if normalize_text(row[0])}
        all_guids.update(values)
        sources.append({"table": table, "route_guid_column": guid_col, "guid_count": len(values)})
    return all_guids, sources


def safe_ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def percentile(values: list[int], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[low])
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def build_profile(
    route_rows: Iterable[dict[str, Any]],
    feature_guids: set[str],
    context_guids: set[str],
    geometry_guids: set[str],
    min_members: int = 2,
    top_n: int = 20,
) -> dict[str, Any]:
    deduped: dict[str, dict[str, Any]] = {}
    invalid = Counter()
    raw_equipment_by_key: dict[str, set[str]] = defaultdict(set)

    for raw in route_rows:
        guid = normalize_text(raw.get("route_path_guid"))
        equipment_raw = normalize_text(raw.get("equipment_tag")) or normalize_text(raw.get("equipment_name"))
        equipment_key = normalize_equipment_key(equipment_raw)
        utility_group = normalize_text(raw.get("utility_group")).upper()
        utility = normalize_text(raw.get("utility")).upper()
        if not guid:
            invalid["missing_guid"] += 1
            continue
        if not equipment_key:
            invalid["missing_equipment"] += 1
        if not utility_group:
            invalid["missing_utility_group"] += 1
        if not utility:
            invalid["missing_utility"] += 1
        if not equipment_key or not utility_group or not utility:
            continue
        if guid in deduped:
            invalid["duplicate_route_guid"] += 1
            continue

        row = dict(raw)
        row["equipment_raw"] = equipment_raw
        row["equipment_key"] = equipment_key
        row["utility_group"] = utility_group
        row["utility"] = utility
        row["size_normalized"] = normalize_size(raw.get("size"))
        row["coordinates_complete"] = all(raw.get(key) is not None for key in ("sx", "sy", "sz", "ex", "ey", "ez"))
        deduped[guid] = row
        raw_equipment_by_key[equipment_key].add(equipment_raw)

    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in deduped.values():
        key = (
            normalize_text(row.get("process_name")).upper(),
            row["equipment_key"],
            row["utility_group"],
            row["utility"],
        )
        groups[key].append(row)

    eligible = {key: members for key, members in groups.items() if len(members) >= min_members}
    member_counts = [len(members) for members in eligible.values()]
    homogeneous = 0
    full_feature = full_context = full_geometry = 0
    eligible_members = 0
    eligible_feature_members = eligible_context_members = eligible_geometry_members = 0
    eligible_coordinate_members = 0
    group_summaries: list[dict[str, Any]] = []

    candidate_bucket_counts: Counter[tuple[str, str]] = Counter()
    process_candidate_bucket_counts: Counter[tuple[str, str, str]] = Counter()

    for key, members in eligible.items():
        process, equipment, utility_group, utility = key
        candidate_bucket_counts[(utility_group, utility)] += 1
        process_candidate_bucket_counts[(process, utility_group, utility)] += 1
        sizes = Counter(member["size_normalized"] for member in members)
        if len(sizes) == 1:
            homogeneous += 1
        guids = [normalize_text(member["route_path_guid"]) for member in members]
        feature_count = sum(guid in feature_guids for guid in guids)
        context_count = sum(guid in context_guids for guid in guids)
        geometry_count = sum(guid in geometry_guids for guid in guids)
        coordinate_count = sum(bool(member["coordinates_complete"]) for member in members)
        member_count = len(members)
        eligible_members += member_count
        eligible_feature_members += feature_count
        eligible_context_members += context_count
        eligible_geometry_members += geometry_count
        eligible_coordinate_members += coordinate_count
        full_feature += feature_count == member_count
        full_context += context_count == member_count
        full_geometry += geometry_count == member_count
        group_summaries.append({
            "process": process,
            "equipment": equipment,
            "equipment_variants": sorted(raw_equipment_by_key[equipment]),
            "utility_group": utility_group,
            "utility": utility,
            "member_count": member_count,
            "size_signature": dict(sorted(sizes.items())),
            "feature_coverage": safe_ratio(feature_count, member_count),
            "context_coverage": safe_ratio(context_count, member_count),
            "geometry_coverage": safe_ratio(geometry_count, member_count),
            "coordinate_coverage": safe_ratio(coordinate_count, member_count),
        })

    group_summaries.sort(key=lambda item: (-item["member_count"], item["equipment"], item["utility"]))
    variant_keys = {key: sorted(values) for key, values in raw_equipment_by_key.items() if len(values) > 1}
    route_guids = set(deduped)

    return {
        "route_summary": {
            "valid_route_count": len(deduped),
            "invalid_counts": dict(invalid),
            "feature_connected_routes": len(route_guids & feature_guids),
            "context_connected_routes": len(route_guids & context_guids),
            "geometry_connected_routes": len(route_guids & geometry_guids),
            "feature_route_coverage": safe_ratio(len(route_guids & feature_guids), len(route_guids)),
            "context_route_coverage": safe_ratio(len(route_guids & context_guids), len(route_guids)),
            "geometry_route_coverage": safe_ratio(len(route_guids & geometry_guids), len(route_guids)),
        },
        "group_summary": {
            "all_group_count": len(groups),
            "eligible_group_count": len(eligible),
            "single_member_group_count": sum(len(members) == 1 for members in groups.values()),
            "eligible_member_count": eligible_members,
            "member_count_min": min(member_counts) if member_counts else 0,
            "member_count_p50": percentile(member_counts, 0.50),
            "member_count_p90": percentile(member_counts, 0.90),
            "member_count_max": max(member_counts) if member_counts else 0,
            "homogeneous_size_group_count": homogeneous,
            "mixed_size_group_count": len(eligible) - homogeneous,
            "homogeneous_size_group_ratio": safe_ratio(homogeneous, len(eligible)),
            "full_feature_group_count": full_feature,
            "full_context_group_count": full_context,
            "full_geometry_group_count": full_geometry,
            "feature_member_coverage": safe_ratio(eligible_feature_members, eligible_members),
            "context_member_coverage": safe_ratio(eligible_context_members, eligible_members),
            "geometry_member_coverage": safe_ratio(eligible_geometry_members, eligible_members),
            "coordinate_member_coverage": safe_ratio(eligible_coordinate_members, eligible_members),
        },
        "equipment_normalization": {
            "normalized_equipment_count": len(raw_equipment_by_key),
            "keys_with_multiple_raw_variants": len(variant_keys),
            "variant_examples": dict(list(sorted(variant_keys.items()))[:top_n]),
        },
        "candidate_pool_summary": {
            "utility_bucket_count": len(candidate_bucket_counts),
            "buckets_with_at_least_2_groups": sum(count >= 2 for count in candidate_bucket_counts.values()),
            "buckets_with_at_least_5_groups": sum(count >= 5 for count in candidate_bucket_counts.values()),
            "top_utility_buckets": [
                {"utility_group": key[0], "utility": key[1], "group_count": count}
                for key, count in candidate_bucket_counts.most_common(top_n)
            ],
            "top_process_utility_buckets": [
                {"process": key[0], "utility_group": key[1], "utility": key[2], "group_count": count}
                for key, count in process_candidate_bucket_counts.most_common(top_n)
            ],
        },
        "top_groups": group_summaries[:top_n],
    }


def fmt_int(value: Any) -> str:
    return f"{int(value):,}"


def fmt_pct(value: Any) -> str:
    return f"{float(value) * 100.0:.1f}%"


def render_markdown(profile: dict[str, Any]) -> str:
    route = profile["route_summary"]
    group = profile["group_summary"]
    equipment = profile["equipment_normalization"]
    candidate = profile["candidate_pool_summary"]
    scope = profile["scope"]
    lines = [
        "# UtilityPipeGroup Top-K 단계 0 데이터 프로파일링 결과",
        "",
        f"- 생성시각(UTC): {profile['generated_at_utc']}",
        f"- Database: `{profile['database']}`",
        f"- Scope mode: `{profile['scope_mode']}`",
        f"- Scope status: `{scope['status']}`",
        f"- Project: `{scope['project']}`",
        f"- Revision: `{scope['revision']}`",
        f"- 최소 그룹 멤버 수: {profile['min_members']}",
        "",
        "## 1. 결론 요약",
        "",
        f"- 유효 Route {fmt_int(route['valid_route_count'])}건에서 전체 그룹 {fmt_int(group['all_group_count'])}개, 개발 대상 그룹 {fmt_int(group['eligible_group_count'])}개가 확인됐다.",
        f"- 동일 Size 그룹 비율은 {fmt_pct(group['homogeneous_size_group_ratio'])}이며 혼합 Size 그룹은 {fmt_int(group['mixed_size_group_count'])}개다.",
        f"- 개발 대상 멤버의 Feature/Context/상세경로 연결률은 각각 {fmt_pct(group['feature_member_coverage'])} / {fmt_pct(group['context_member_coverage'])} / {fmt_pct(group['geometry_member_coverage'])}다.",
        f"- Utility 후보 버킷 중 그룹이 2개 이상인 버킷은 {fmt_int(candidate['buckets_with_at_least_2_groups'])}개, 5개 이상은 {fmt_int(candidate['buckets_with_at_least_5_groups'])}개다.",
        "- 정확한 장비 키까지 Candidate 필터로 고정하면 한 snapshot에서 Query 그룹 자체만 남으므로, 자기 자신 제외 후 Top-K 후보가 없다.",
        "- 장비는 Query 그룹 식별자로 유지하고 Candidate 후보 수집은 `Utility Group + Utility`를 필수키로 사용해야 한다.",
        "",
        "## 2. Route 연결률",
        "",
        "| 항목 | 건수/비율 |",
        "|---|---:|",
        f"| 유효 Route | {fmt_int(route['valid_route_count'])} |",
        f"| Feature 연결 | {fmt_int(route['feature_connected_routes'])} / {fmt_pct(route['feature_route_coverage'])} |",
        f"| Context 연결 | {fmt_int(route['context_connected_routes'])} / {fmt_pct(route['context_route_coverage'])} |",
        f"| 상세경로 GUID 연결 | {fmt_int(route['geometry_connected_routes'])} / {fmt_pct(route['geometry_route_coverage'])} |",
        "",
        "## 3. 그룹 및 Size 분포",
        "",
        "| 항목 | 값 |",
        "|---|---:|",
        f"| 전체 그룹 | {fmt_int(group['all_group_count'])} |",
        f"| 1개 멤버 그룹 | {fmt_int(group['single_member_group_count'])} |",
        f"| 2개 이상 개발 대상 그룹 | {fmt_int(group['eligible_group_count'])} |",
        f"| 개발 대상 멤버 | {fmt_int(group['eligible_member_count'])} |",
        f"| 멤버 수 min / p50 / p90 / max | {group['member_count_min']} / {group['member_count_p50']:.1f} / {group['member_count_p90']:.1f} / {group['member_count_max']} |",
        f"| 동일 Size 그룹 | {fmt_int(group['homogeneous_size_group_count'])} / {fmt_pct(group['homogeneous_size_group_ratio'])} |",
        f"| 혼합 Size 그룹 | {fmt_int(group['mixed_size_group_count'])} |",
        "",
        "## 4. 개발 대상 그룹 Vector 준비도",
        "",
        "| 항목 | 멤버 coverage | 전체 멤버가 연결된 그룹 |",
        "|---|---:|---:|",
        f"| Feature | {fmt_pct(group['feature_member_coverage'])} | {fmt_int(group['full_feature_group_count'])} |",
        f"| Context | {fmt_pct(group['context_member_coverage'])} | {fmt_int(group['full_context_group_count'])} |",
        f"| 상세경로 | {fmt_pct(group['geometry_member_coverage'])} | {fmt_int(group['full_geometry_group_count'])} |",
        f"| 시작/종점 좌표 | {fmt_pct(group['coordinate_member_coverage'])} | - |",
        "",
        "## 5. 장비명 정규화",
        "",
        f"- 정규화 장비 수: {fmt_int(equipment['normalized_equipment_count'])}",
        f"- 두 가지 이상 원시 표기가 합쳐진 장비 키: {fmt_int(equipment['keys_with_multiple_raw_variants'])}",
        "",
    ]
    if equipment["variant_examples"]:
        lines.extend(["| 정규화 키 | 원시 표기 |", "|---|---|"])
        for key, variants in equipment["variant_examples"].items():
            lines.append(f"| `{key}` | {', '.join(f'`{v}`' for v in variants)} |")
        lines.append("")

    lines.extend(["## 6. Top-K 후보 버킷", "", "| Utility Group | Utility | 그룹 수 |", "|---|---|---:|"])
    for item in candidate["top_utility_buckets"]:
        lines.append(f"| {item['utility_group']} | {item['utility']} | {fmt_int(item['group_count'])} |")
    lines.extend(["", "## 7. 멤버 수 상위 그룹", "", "| 장비 | Utility Group | Utility | 멤버 | Size 분포 | Feature | Context | 상세경로 |", "|---|---|---|---:|---|---:|---:|---:|"])
    for item in profile["top_groups"]:
        size_text = ", ".join(f"{key}:{value}" for key, value in item["size_signature"].items())
        lines.append(
            f"| {item['equipment']} | {item['utility_group']} | {item['utility']} | {item['member_count']} | "
            f"{size_text} | {fmt_pct(item['feature_coverage'])} | {fmt_pct(item['context_coverage'])} | {fmt_pct(item['geometry_coverage'])} |"
        )

    lines.extend([
        "",
        "## 8. Top-K 성립 조건 분석",
        "",
        "그룹 자체를 `(장비 + Utility Group + Utility)`로 정의하면 동일 ACTIVE snapshot에서 이 조합은 한 행만 존재한다. 따라서 Candidate SQL에 정확한 장비 키까지 `WHERE` 조건으로 적용하고 Query 자신을 제외하면 결과가 0건이 된다.",
        "",
        "```text",
        "Query 그룹 식별: 장비 + Utility Group + Utility",
        "Candidate 필수 필터: Utility Group + Utility",
        "Candidate 선택 필터: Process, Equipment Family, Size 정책",
        "금지: 정확한 장비 인스턴스 키를 필수 Candidate 필터로 사용",
        "```",
        "",
        f"현재 ACTIVE 데이터에서는 Utility Group+Utility 버킷 중 {fmt_int(candidate['buckets_with_at_least_2_groups'])}개가 2개 이상 그룹을, {fmt_int(candidate['buckets_with_at_least_5_groups'])}개가 5개 이상 그룹을 보유한다. K=5 검색은 모든 Utility에서 보장되지 않으므로 실제 후보 수를 UI에 표시하고 부족하면 존재하는 그룹만 반환해야 한다.",
        "",
        "## 9. 단계 1 입력 결정",
        "",
        "- 그룹 사용자 키는 `장비 + Utility Group + Utility`를 유지한다.",
        "- 내부 장비 인스턴스 키는 실제 컬럼 `EQUIPMENT_TAG`를 우선하고 후행 `_`, `-`, 공백 제거 정규화를 적용한다.",
        "- Candidate 필수 필터는 `Utility Group + Utility`로 확정하고 장비 인스턴스 키는 자기 제외와 결과 설명에만 사용한다.",
        "- Process 및 향후 Equipment Family는 선택 필터로 두고 후보 부족 시 완화할 수 있게 한다.",
        "- 기본 `minMemberCount=2`를 적용한다.",
        "- 동일 Size 그룹이 100%가 아니므로 기본 Size 정책은 `ExactOnly`가 아니라 `PreferExact`로 확정한다.",
        "- ACTIVE 개발 대상 그룹의 Feature/Context/상세경로 coverage가 모두 100%이므로 1차 구현은 세 데이터가 모두 있는 그룹을 READY로 생성한다.",
        "- 후보 그룹이 K보다 적으면 후보 확장으로 무관한 Utility를 섞지 않고 가용 결과 수만 반환한다.",
        "",
        "## 10. 스키마 진단",
        "",
        "```json",
        json.dumps(profile["schema"], ensure_ascii=False, indent=2),
        "```",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    if args.min_members < 2:
        raise SystemExit("--min-members는 2 이상이어야 합니다.")
    if psycopg2 is None:
        raise SystemExit(
            "psycopg2가 필요합니다. "
            "python -m pip install -r Tools/requirements-utility-pipe-group.txt 를 실행하세요."
        )
    runtime = tool_config.resolve_runtime(args)
    conn = psycopg2.connect(runtime.conninfo)
    conn.set_session(readonly=True, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            database = cur.fetchone()[0]
        scope = resolve_active_scope(conn)
        rows, route_schema = fetch_route_rows(conn, scope, args.scope_mode)
        feature_guids, feature_schema = fetch_guid_set(conn, "TB_ROUTE_FEATURE_VECTOR", scope, args.scope_mode)
        context_guids, context_schema = fetch_guid_set(conn, "TB_ROUTE_CONTEXT_VECTOR", scope, args.scope_mode)
        geometry_guids, geometry_sources = fetch_geometry_guid_set(conn)
        profile = build_profile(rows, feature_guids, context_guids, geometry_guids, args.min_members, args.top_n)
        profile.update({
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "database": database,
            "scope_mode": args.scope_mode,
            "scope": asdict(scope),
            "min_members": args.min_members,
            "schema": {
                "route": route_schema,
                "feature": feature_schema,
                "context": context_schema,
                "geometry_sources": geometry_sources,
            },
        })

        markdown = render_markdown(profile)
        print(markdown)
        if args.markdown_out:
            path = Path(args.markdown_out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")
            print(f"[saved] {path.resolve()}")
        if args.json_out:
            path = Path(args.json_out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[saved] {path.resolve()}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
