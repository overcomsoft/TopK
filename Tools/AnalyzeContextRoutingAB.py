"""
실제 라우팅 Baseline/Context A-B 로그를 동일 요청·동일 실행 단위로 분석한다.

실행 방법(PowerShell)
---------------------
1. 로그 테이블 생성: python Tools/AnalyzeContextRoutingAB.py --config Tools/tools.settings.json create-schema
2. 현황 조회: python Tools/AnalyzeContextRoutingAB.py --config Tools/tools.settings.json status
3. 최신 paired 결과 분석: python Tools/AnalyzeContextRoutingAB.py --config Tools/tools.settings.json analyze `
   --experiment-id context-v3-weight-010 --output-json data/output/context_ab.json `
   --output-md Docs/context_ab_report.md

전체 흐름도
-----------
  [TB_CONTEXT_ROUTING_AB_LOG]
          -> REQUEST_KEY + RUN_ID가 같은 BASELINE/CONTEXT pair만 선택
          -> 요청별 최신 완전 pair 선택
          -> snapshot/project/revision 혼합 여부 검사
          -> success/길이/bend/collision/탐색노드/시간 차이 집계
          -> JSON + Markdown 보고서

``RUN_ID``가 다른 두 arm을 임의로 묶지 않으며 provenance가 혼합된 pair는 성능 통계에서
제외한다. 이 규칙은 배포 전후 데이터가 하나의 결과로 섞이는 것을 방지한다.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

import tool_config


TABLE = '"TB_CONTEXT_ROUTING_AB_LOG"'
DEFAULT_EXPERIMENT = "context-v3-weight-010"


def create_schema(conn) -> None:
    """A/B 실행 결과와 Context provenance를 저장할 로그 스키마를 생성한다."""
    sql_path = Path(__file__).resolve().parent / "sql" / "create_context_routing_ab_log_table.sql"
    with conn.cursor() as cur:
        cur.execute(sql_path.read_text(encoding="utf-8"))
    conn.commit()
    print(f"Schema ready: {TABLE}")


def load_latest(conn, experiment_id: str, snapshot_hash: str = "") -> list[dict]:
    """experiment별 요청에서 가장 최근의 완전한 동일 RUN_ID pair만 읽는다."""
    import psycopg2.extras

    sql = f'''
        WITH paired_runs AS (
            SELECT "REQUEST_KEY", "RUN_ID", MAX("CREATED_AT") AS max_created
            FROM {TABLE}
            WHERE "EXPERIMENT_ID" = %s
              AND "ARM" IN ('BASELINE_TOPK', 'CONTEXT_V3')
              AND (%s = '' OR "CONTEXT_SNAPSHOT_HASH" = %s)
            GROUP BY "REQUEST_KEY", "RUN_ID"
            HAVING COUNT(DISTINCT "ARM") = 2
        ), ranked_runs AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY "REQUEST_KEY" ORDER BY max_created DESC, "RUN_ID" DESC
            ) AS rn
            FROM paired_runs
        )
        SELECT log.*
        FROM {TABLE} log
        JOIN ranked_runs run
          ON run."REQUEST_KEY" = log."REQUEST_KEY" AND run."RUN_ID" = log."RUN_ID" AND run.rn = 1
        WHERE log."EXPERIMENT_ID" = %s AND log."ARM" IN ('BASELINE_TOPK', 'CONTEXT_V3')
        ORDER BY log."REQUEST_KEY", log."ARM"
    '''
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (experiment_id, snapshot_hash, snapshot_hash, experiment_id))
        return [dict(row) for row in cur.fetchall()]


def _mean(values) -> float | None:
    values = [float(v) for v in values if v is not None]
    return statistics.fmean(values) if values else None


def summarize(rows: list[dict], experiment_id: str) -> dict:
    """arm별 로그를 pair로 묶고 품질·비용 지표와 provenance 위반을 집계한다."""
    snapshot_hashes = sorted({
        str(row.get("CONTEXT_SNAPSHOT_HASH")) for row in rows if row.get("CONTEXT_SNAPSHOT_HASH")
    })
    scope_statuses = sorted({
        str(row.get("CONTEXT_SCOPE_STATUS")) for row in rows if row.get("CONTEXT_SCOPE_STATUS")
    })
    build_run_ids = sorted({
        str(row.get("CONTEXT_BUILD_RUN_ID")) for row in rows if row.get("CONTEXT_BUILD_RUN_ID")
    })
    inconsistent_rows = sum(
        not bool(row.get("CONTEXT_PROVENANCE_CONSISTENT", False)) for row in rows
    )
    missing_provenance_rows = sum(
        not row.get("CONTEXT_SNAPSHOT_HASH") or not row.get("CONTEXT_SCOPE_STATUS") for row in rows
    )
    by_arm: dict[str, list[dict]] = {
        "BASELINE_TOPK": [row for row in rows if row["ARM"] == "BASELINE_TOPK"],
        "CONTEXT_V3": [row for row in rows if row["ARM"] == "CONTEXT_V3"],
    }

    arm_summary = {}
    for arm, arm_rows in by_arm.items():
        successes = [row for row in arm_rows if row["ROUTE_SUCCESS"]]
        arm_summary[arm] = {
            "requests": len(arm_rows),
            "successes": len(successes),
            "success_rate": len(successes) / len(arm_rows) if arm_rows else None,
            "avg_length_mm_success": _mean(row["ROUTE_LENGTH_MM"] for row in successes),
            "avg_bends_success": _mean(row["ROUTE_BEND_COUNT"] for row in successes),
            "avg_elapsed_ms": _mean(row["ROUTE_ELAPSED_MS"] for row in arm_rows),
            "avg_expanded_nodes": _mean(row["EXPANDED_NODES"] for row in arm_rows),
            "avg_collision_count_success": _mean(row.get("COLLISION_COUNT") for row in successes),
            "avg_corridor_cells": _mean(row.get("CORRIDOR_CELL_COUNT") for row in arm_rows),
            "avg_corridor_exclusive_cells": _mean(
                row.get("CORRIDOR_EXCLUSIVE_CELL_COUNT") for row in arm_rows
            ),
            "avg_endpoint_release_count": _mean(row.get("ENDPOINT_RELEASE_COUNT") for row in arm_rows),
            "avg_context_coverage": _mean(row["CONTEXT_COVERAGE"] for row in arm_rows),
            "fallback_total": sum(int(row["CONTEXT_FALLBACK_COUNT"] or 0) for row in arm_rows),
            "fail_reasons": dict(Counter(
                str(row["ROUTE_FAIL_REASON"] or "UNKNOWN")
                for row in arm_rows if not row["ROUTE_SUCCESS"]
            )),
        }

    keyed = {}
    for row in rows:
        keyed.setdefault(str(row["REQUEST_KEY"]).strip(), {})[row["ARM"]] = row
    all_pairs = [pair for pair in keyed.values() if "BASELINE_TOPK" in pair and "CONTEXT_V3" in pair]
    provenance_mismatched_pairs = 0
    pairs = []
    for pair in all_pairs:
        baseline, context = pair["BASELINE_TOPK"], pair["CONTEXT_V3"]
        same_run = not baseline.get("RUN_ID") or baseline.get("RUN_ID") == context.get("RUN_ID")
        same_manifest = all(
            baseline.get(column) == context.get(column) and bool(context.get(column))
            for column in (
                "CONTEXT_SNAPSHOT_HASH", "CONTEXT_SCOPE_STATUS", "CONTEXT_BUILD_RUN_ID",
                "CONTEXT_ENCODER_VERSION", "CONTEXT_ENCODER_CONFIG_HASH",
            )
        )
        consistent = bool(baseline.get("CONTEXT_PROVENANCE_CONSISTENT", False)) and bool(
            context.get("CONTEXT_PROVENANCE_CONSISTENT", False)
        )
        if same_run and same_manifest and consistent:
            pairs.append(pair)
        else:
            provenance_mismatched_pairs += 1
    outcome = Counter()
    successful_pairs = []
    topk_overlap = []
    topk_changed = 0
    changed_topk_pairs = []
    for pair in pairs:
        baseline, context = pair["BASELINE_TOPK"], pair["CONTEXT_V3"]
        baseline_topk = list(baseline.get("TOPK_ROUTE_GUIDS") or [])
        context_topk = list(context.get("TOPK_ROUTE_GUIDS") or [])
        if baseline_topk != context_topk:
            topk_changed += 1
            changed_topk_pairs.append((baseline, context))
        baseline_set, context_set = set(baseline_topk), set(context_topk)
        topk_overlap.append(
            len(baseline_set & context_set) / max(len(baseline_set | context_set), 1)
        )
        b_ok, c_ok = bool(baseline["ROUTE_SUCCESS"]), bool(context["ROUTE_SUCCESS"])
        if b_ok and c_ok:
            outcome["both_success"] += 1
            successful_pairs.append((baseline, context))
        elif c_ok:
            outcome["context_only_success"] += 1
        elif b_ok:
            outcome["baseline_only_success"] += 1
        else:
            outcome["both_failed"] += 1

    def avg_delta(column: str) -> float | None:
        return _mean(
            float(context[column]) - float(baseline[column])
            for baseline, context in successful_pairs
            if baseline[column] is not None and context[column] is not None
        )

    changed_outcome = Counter()
    for baseline, context in changed_topk_pairs:
        b_ok, c_ok = bool(baseline["ROUTE_SUCCESS"]), bool(context["ROUTE_SUCCESS"])
        if c_ok and not b_ok:
            changed_outcome["context_only_success"] += 1
        elif b_ok and not c_ok:
            changed_outcome["baseline_only_success"] += 1
        elif b_ok and c_ok:
            changed_outcome["both_success"] += 1
        else:
            changed_outcome["both_failed"] += 1

    provenance_ready = (
        len(snapshot_hashes) == 1 and inconsistent_rows == 0 and
        missing_provenance_rows == 0 and provenance_mismatched_pairs == 0
    )
    ready = len(pairs) >= 30 and provenance_ready
    context_net = outcome["context_only_success"] - outcome["baseline_only_success"]
    length_delta = avg_delta("ROUTE_LENGTH_MM")
    bend_delta = avg_delta("ROUTE_BEND_COUNT")
    if not provenance_ready:
        decision = "BLOCK_PROVENANCE_MISMATCH"
    elif not ready:
        decision = "COLLECT_MORE"
    elif context_net < 0:
        decision = "BLOCK_CONTEXT_REGRESSION"
    elif context_net > 0:
        decision = "CONTEXT_PROMISING"
    elif topk_changed > 0 and abs(length_delta or 0.0) < 1e-9 and abs(bend_delta or 0.0) < 1e-9:
        decision = "NO_OBSERVED_ROUTE_QUALITY_EFFECT"
    else:
        decision = "INCONCLUSIVE"

    return {
        "experiment_id": experiment_id,
        "provenance": {
            "snapshot_hashes": snapshot_hashes,
            "scope_statuses": scope_statuses,
            "build_run_ids": build_run_ids,
            "inconsistent_rows": inconsistent_rows,
            "missing_rows": missing_provenance_rows,
            "mismatched_pairs": provenance_mismatched_pairs,
            "ready": provenance_ready,
        },
        "model_revision_keys": sorted({
            str(row.get("MODEL_REVISION_KEY")) for row in rows if row.get("MODEL_REVISION_KEY")
        }),
        "corridor_policies": sorted({
            str(row.get("CORRIDOR_POLICY")) for row in rows if row.get("CORRIDOR_POLICY")
        }),
        "corridor_rank_profiles": sorted({
            str(row.get("CORRIDOR_RANK_PROFILE")) for row in rows if row.get("CORRIDOR_RANK_PROFILE")
        }),
        "corridor_cost_factors": sorted({
            float(row.get("CORRIDOR_COST_FACTOR")) for row in rows
            if row.get("CORRIDOR_COST_FACTOR") is not None
        }),
        "latest_rows": len(rows),
        "arms": arm_summary,
        "paired": {
            "requests": len(pairs),
            **{key: outcome.get(key, 0) for key in (
                "both_success", "context_only_success", "baseline_only_success", "both_failed"
            )},
            "context_success_net": context_net,
            "avg_context_minus_baseline_length_mm": length_delta,
            "avg_context_minus_baseline_bends": bend_delta,
            "avg_context_minus_baseline_elapsed_ms": avg_delta("ROUTE_ELAPSED_MS"),
            "avg_context_minus_baseline_expanded_nodes": avg_delta("EXPANDED_NODES"),
            "topk_changed_pairs": topk_changed,
            "topk_changed_rate": topk_changed / len(pairs) if pairs else None,
            "avg_topk_jaccard_overlap": _mean(topk_overlap),
            "changed_topk_outcomes": dict(changed_outcome),
        },
        "ready_for_decision": ready,
        "decision": decision,
        "decision_note": (
            "Do not aggregate rows across missing, mixed, or mismatched context provenance."
            if not provenance_ready else
            "Minimum sample reached; apply approved production thresholds before promotion."
            if ready else
            "Collect at least 30 paired requests before making a routing-quality decision."
        ),
    }


def _fmt(value, digits=2) -> str:
    return "N/A" if value is None else f"{float(value):,.{digits}f}"


def _render_markdown_legacy(report: dict) -> str:
    lines = [
        "# Context Vector 실제 라우팅 A/B 보고서", "",
        f"실험: `{report['experiment_id']}`", "",
        "## Arm별 결과", "",
        "| Arm | 요청 | 성공률 | 성공 경로 평균 길이(mm) | 평균 bend | 평균 시간(ms) | 평균 확장 노드 | Context coverage | fallback |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in ("BASELINE_TOPK", "CONTEXT_V3"):
        row = report["arms"][arm]
        success = "N/A" if row["success_rate"] is None else f"{row['success_rate']:.1%}"
        coverage = "N/A" if row["avg_context_coverage"] is None else f"{row['avg_context_coverage']:.1%}"
        lines.append(
            f"| {arm} | {row['requests']} | {success} | {_fmt(row['avg_length_mm_success'])} | "
            f"{_fmt(row['avg_bends_success'])} | {_fmt(row['avg_elapsed_ms'])} | "
            f"{_fmt(row['avg_expanded_nodes'])} | {coverage} | {row['fallback_total']} |"
        )
    paired = report["paired"]
    changed_rate = "N/A" if paired["topk_changed_rate"] is None else f"{paired['topk_changed_rate']:.1%}"
    lines.extend([
        "", "## 동일 요청 페어 비교", "",
        f"- 페어 수: {paired['requests']}",
        f"- 양쪽 성공: {paired['both_success']}",
        f"- Context만 성공: {paired['context_only_success']}",
        f"- Baseline만 성공: {paired['baseline_only_success']}",
        f"- 양쪽 실패: {paired['both_failed']}",
        f"- Context 성공 순증: {paired['context_success_net']:+d}",
        f"- 길이 변화(Context-Baseline): {_fmt(paired['avg_context_minus_baseline_length_mm'])}mm",
        f"- bend 변화(Context-Baseline): {_fmt(paired['avg_context_minus_baseline_bends'])}",
        f"- 처리시간 변화(Context-Baseline): {_fmt(paired['avg_context_minus_baseline_elapsed_ms'])}ms",
        f"- 확장 노드 변화(Context-Baseline): {_fmt(paired['avg_context_minus_baseline_expanded_nodes'])}",
        f"- Top-K 순위/구성 변경 페어: {paired['topk_changed_pairs']} ({changed_rate})",
        f"- 평균 Top-K Jaccard overlap: {_fmt(paired['avg_topk_jaccard_overlap'], 4)}",
        "", f"판정 준비: **{'YES' if report['ready_for_decision'] else 'NO'}**",
        f"자동 판정: **{report['decision']}**", "",
        report["decision_note"], "",
    ])
    return "\n".join(lines)


def render_markdown(report: dict) -> str:
    """분석 JSON을 운영자가 검토하기 쉬운 한글 Markdown 표로 변환한다."""
    # Provenance is emitted in JSON and controls the decision gate; keep the legacy
    # localized markdown layout backward compatible.
    lines = [
        "# Context Vector 실제 라우팅 A/B 보고서", "",
        f"실험: `{report['experiment_id']}`", "",
        f"- 모델 revision: {', '.join(report.get('model_revision_keys') or ['미지정'])}",
        f"- corridor 정책: {', '.join(report.get('corridor_policies') or ['미기록'])}",
        f"- rank penalty profile: {', '.join(report.get('corridor_rank_profiles') or ['미기록'])}",
        f"- corridor 비용 계수: {', '.join(str(v) for v in report.get('corridor_cost_factors') or ['미기록'])}",
        "", "## Arm별 결과", "",
        "| Arm | 요청 | 성공률 | 성공 경로 평균 길이(mm) | 평균 bend | 평균 시간(ms) | 평균 확장 노드 | 평균 충돌 | 평균 corridor cell | 전용 cell |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in ("BASELINE_TOPK", "CONTEXT_V3"):
        row = report["arms"][arm]
        success = "N/A" if row["success_rate"] is None else f"{row['success_rate']:.1%}"
        lines.append(
            f"| {arm} | {row['requests']} | {success} | {_fmt(row['avg_length_mm_success'])} | "
            f"{_fmt(row['avg_bends_success'])} | {_fmt(row['avg_elapsed_ms'])} | "
            f"{_fmt(row['avg_expanded_nodes'])} | {_fmt(row['avg_collision_count_success'])} | "
            f"{_fmt(row['avg_corridor_cells'])} | {_fmt(row['avg_corridor_exclusive_cells'])} |"
        )
    paired = report["paired"]
    changed_rate = "N/A" if paired["topk_changed_rate"] is None else f"{paired['topk_changed_rate']:.1%}"
    lines.extend([
        "", "## 동일 요청 페어 비교", "",
        f"- 페어 수: {paired['requests']}",
        f"- 양쪽 성공: {paired['both_success']}",
        f"- Context만 성공: {paired['context_only_success']}",
        f"- Baseline만 성공: {paired['baseline_only_success']}",
        f"- 양쪽 실패: {paired['both_failed']}",
        f"- Context 성공 순증: {paired['context_success_net']:+d}",
        f"- 길이 변화(Context-Baseline): {_fmt(paired['avg_context_minus_baseline_length_mm'])}mm",
        f"- bend 변화(Context-Baseline): {_fmt(paired['avg_context_minus_baseline_bends'])}",
        f"- 처리시간 변화(Context-Baseline): {_fmt(paired['avg_context_minus_baseline_elapsed_ms'])}ms",
        f"- 확장 노드 변화(Context-Baseline): {_fmt(paired['avg_context_minus_baseline_expanded_nodes'])}",
        f"- Top-K 순위/구성 변경 페어: {paired['topk_changed_pairs']} ({changed_rate})",
        f"- 평균 Top-K Jaccard overlap: {_fmt(paired['avg_topk_jaccard_overlap'], 4)}",
        "", f"판정 준비: **{'YES' if report['ready_for_decision'] else 'NO'}**",
        f"자동 판정: **{report['decision']}**", "", report["decision_note"], "",
    ])
    return "\n".join(lines)


def main() -> int:
    """schema/status/analyze 명령과 출력 파일 경로를 처리한다."""
    parser = argparse.ArgumentParser(description="Analyze Context Vector actual-routing A/B logs")
    tool_config.add_common_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create-schema")
    sub.add_parser("status")
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT)
    report_parser.add_argument("--snapshot-hash", default="", help="Analyze exactly one context snapshot")
    report_parser.add_argument("--output-json", default="data/output/context_routing_ab_report.json")
    report_parser.add_argument("--output-md", default="Docs/ContextVector_Routing_AB_Report.md")
    args = parser.parse_args()
    runtime = tool_config.resolve_runtime(args)
    tool_config.print_runtime(runtime)
    import psycopg2

    with psycopg2.connect(runtime.conninfo) as conn:
        if args.command == "create-schema":
            create_schema(conn)
        elif args.command == "status":
            with conn.cursor() as cur:
                cur.execute(
                    f'''SELECT COUNT(*), COUNT(DISTINCT "REQUEST_KEY"), COUNT(DISTINCT "RUN_ID"),
                               COUNT(*) FILTER (WHERE "PROJECT_KEY" IS NOT NULL AND TRIM("PROJECT_KEY") <> ''),
                               COUNT(DISTINCT "CONTEXT_SNAPSHOT_HASH") FILTER
                                 (WHERE "CONTEXT_SNAPSHOT_HASH" IS NOT NULL),
                               COUNT(*) FILTER (WHERE NOT "CONTEXT_PROVENANCE_CONSISTENT")
                        FROM {TABLE}'''
                )
                logs, requests, runs, project_scoped, snapshots, inconsistent = cur.fetchone()
            print(
                f"Logs={logs}, requests={requests}, runs={runs}, project_scoped={project_scoped}/{logs}, "
                f"snapshots={snapshots}, inconsistent={inconsistent}"
            )
        else:
            rows = load_latest(conn, args.experiment_id, args.snapshot_hash)
            report = summarize(rows, args.experiment_id)
            json_path, md_path = Path(args.output_json).resolve(), Path(args.output_md).resolve()
            json_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            md_path.write_text(render_markdown(report), encoding="utf-8")
            print(f"JSON: {json_path}")
            print(f"Markdown: {md_path}")
            print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
