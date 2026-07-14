"""
공정/Utility/거리/높이변화 strata를 균형 있게 뽑아 실제 라우팅 A-B campaign을 실행한다.

실행 방법(PowerShell)
---------------------
1. 표본계획만 생성: python Tools/RunContextABCampaign.py --config Tools/tools.settings.json `
   --target-pairs 30 --batch-size 5 --plan-json data/output/context_campaign_plan.json
2. 실제 routing/log 저장: 위 명령에 ``--execute`` 추가
3. 특정 ACTIVE revision 고정: ``--model-revision-key snapshot:<sha256>`` 추가

전체 흐름도
-----------
  [공간 project + route 후보 로드] -> [이미 완료/제외 route 제거]
        -> [process, utility, 거리, 높이 strata bucket]
        -> [round-robin으로 목표 pair 선택]
        -> [batch별 ContextRoutingABRunner 실행]
        -> [DB 최신 paired 로그 재조회]
        -> [JSON/Markdown 누적 보고서]

주요 변수: ``target_pairs``는 목표 paired 요청 수, ``batch_size``는 checkpoint 간격,
``seed``는 재실행해도 같은 후보 순서를 만드는 값, ``completed``는 중복실행 방지 집합이다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path

import tool_config
from AnalyzeContextRoutingAB import DEFAULT_EXPERIMENT, load_latest, render_markdown, summarize


def load_projects(conn) -> list[dict]:
    """공간 그룹을 읽고 C# runner가 사용하는 1-based project_id를 부여한다."""
    with conn.cursor() as cur:
        cur.execute(
            '''SELECT "TAG_GROUP_ID", "TAG_GROUP_NM", "BAY_GROUP_NM", "PROCESS_GROUP_NM",
                      "AABB_MINX", "AABB_MINY", "AABB_MAXX", "AABB_MAXY"
               FROM "TB_SPACE_GROUP_INFO"
               ORDER BY "PROCESS_GROUP_NM", "TAG_GROUP_NM"'''
        )
        rows = cur.fetchall()
    return [
        {
            "project_id": index,
            "project_key": str(row[0] or ""),
            "name": str(row[1] or ""),
            "bay": str(row[2] or ""),
            "process": str(row[3] or ""),
            "minx": float(row[4]), "miny": float(row[5]),
            "maxx": float(row[6]), "maxy": float(row[7]),
        }
        for index, row in enumerate(rows, 1)
    ]


def load_candidates(conn, projects: list[dict], completed: set[tuple[str, str]], seed: str,
                    excluded_route_guids: set[str] | None = None) -> list[dict]:
    """route를 공간 project와 연결하고 strata/결정적 seed를 가진 campaign 후보로 만든다."""
    with conn.cursor() as cur:
        cur.execute(
            '''SELECT "ROUTE_PATH_GUID", "UTILITY_GROUP", "SOURCE_UTILITY", "EQUIPMENT_NAME", "PROCESS_NAME",
                      "SOURCE_POSX", "SOURCE_POSY", "SOURCE_POSZ",
                      "TARGET_POSX", "TARGET_POSY", "TARGET_POSZ"
               FROM "TB_ROUTE_PATH"
               WHERE "SOURCE_POSX" IS NOT NULL AND "SOURCE_POSY" IS NOT NULL AND "SOURCE_POSZ" IS NOT NULL
                 AND "TARGET_POSX" IS NOT NULL AND "TARGET_POSY" IS NOT NULL AND "TARGET_POSZ" IS NOT NULL'''
        )
        rows = cur.fetchall()

    candidates = []
    excluded_route_guids = excluded_route_guids or set()
    assigned = set()
    for row in rows:
        guid = str(row[0])
        if guid in assigned or guid in excluded_route_guids:
            continue
        process = str(row[4] or "").strip().upper()
        sx, sy, sz, tx, ty, tz = map(float, row[5:11])
        spatial = [p for p in projects if
                   p["minx"] - 500.0 <= sx <= p["maxx"] + 500.0 and
                   p["miny"] - 500.0 <= sy <= p["maxy"] + 500.0]
        project = next((p for p in spatial if p["process"].strip().upper() == process), None)
        if project is None:
            project = next((p for p in projects if p["process"].strip().upper() == process), None)
        if project is None and len(spatial) == 1:
            project = spatial[0]
        if project is None or (project["project_key"], guid) in completed:
            continue
        assigned.add(guid)
        distance = math.dist((sx, sy, sz), (tx, ty, tz))
        band = "SHORT" if distance < 5_000 else "MEDIUM" if distance < 15_000 else "LONG"
        candidates.append({
            "project_id": project["project_id"],
            "project_key": project["project_key"],
            "project_name": project["name"],
            "process": project["process"],
            "route_guid": guid,
            "utility_group": str(row[1] or ""),
            "utility": str(row[2] or ""),
            "equipment": str(row[3] or ""),
            "distance_mm": distance,
            "distance_band": band,
            "order_key": hashlib.sha256(f"{seed}|{guid}".encode()).hexdigest(),
        })
    return candidates


def completed_pairs(conn, experiment_id: str) -> set[tuple[str, str]]:
    """이미 두 arm이 모두 저장된 request/project pair를 반환해 재실행을 막는다."""
    with conn.cursor() as cur:
        cur.execute(
            '''SELECT COALESCE("PROJECT_KEY", ''), COALESCE("ROUTE_PATH_GUID", '')
               FROM "TB_CONTEXT_ROUTING_AB_LOG"
               WHERE "EXPERIMENT_ID" = %s AND "ARM" IN ('BASELINE_TOPK', 'CONTEXT_V3')
               GROUP BY "PROJECT_KEY", "ROUTE_PATH_GUID"
               HAVING COUNT(DISTINCT "ARM") = 2''',
            (experiment_id,),
        )
        return {(str(row[0]), str(row[1])) for row in cur.fetchall() if row[1]}


def failure_cohort(conn, experiment_id: str, fail_reasons: set[str]) -> set[str]:
    """참조 experiment에서 특정 실패 사유를 가진 route GUID 집합을 구한다."""
    if not experiment_id or not fail_reasons:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            '''SELECT DISTINCT COALESCE("ROUTE_PATH_GUID", '')
               FROM "TB_CONTEXT_ROUTING_AB_LOG"
               WHERE "EXPERIMENT_ID" = %s AND "ROUTE_SUCCESS" = false
                 AND "ROUTE_FAIL_REASON" = ANY(%s)''',
            (experiment_id, sorted(fail_reasons)),
        )
        return {str(row[0]) for row in cur.fetchall() if row[0]}


def stratified_select(candidates: list[dict], count: int) -> list[dict]:
    """stratum별 deque를 round-robin 순회하여 한 종류에 치우치지 않게 표본을 선택한다."""
    project_rows: dict[int, list[dict]] = defaultdict(list)
    for row in candidates:
        project_rows[row["project_id"]].append(row)

    project_queues: dict[int, deque] = {}
    for project_id, rows in project_rows.items():
        strata: dict[tuple, deque] = defaultdict(deque)
        for row in sorted(rows, key=lambda item: item["order_key"]):
            key = (row["utility_group"].upper(), row["utility"].upper(), row["distance_band"])
            strata[key].append(row)
        ordered = []
        keys = sorted(strata)
        while keys:
            next_keys = []
            for key in keys:
                bucket = strata[key]
                if bucket:
                    ordered.append(bucket.popleft())
                if bucket:
                    next_keys.append(key)
            keys = next_keys
        project_queues[project_id] = deque(ordered)

    selected = []
    project_ids = sorted(project_queues)
    while len(selected) < count and project_ids:
        next_projects = []
        for project_id in project_ids:
            if len(selected) >= count:
                break
            queue = project_queues[project_id]
            if queue:
                selected.append(queue.popleft())
            if queue:
                next_projects.append(project_id)
        project_ids = next_projects
    return selected


def write_plan(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Plan: {path}")


def execute_batches(args, selected: list[dict]) -> list[dict]:
    """선택 route를 batch로 나눠 C# runner를 호출하고 batch별 결과를 기록한다."""
    by_project: dict[int, list[dict]] = defaultdict(list)
    for row in selected:
        by_project[row["project_id"]].append(row)
    batches = []
    sequence = 0
    for project_id in sorted(by_project):
        rows = by_project[project_id]
        for offset in range(0, len(rows), args.batch_size):
            batch = rows[offset:offset + args.batch_size]
            context_first = sequence % 2 == 1
            sequence += 1
            command = [
                "dotnet", str(Path(args.runner_dll)),
                "--config", args.config,
                "--project-id", str(project_id),
                "--route-guids", ",".join(row["route_guid"] for row in batch),
                "--cell-mm", str(args.cell_mm),
                "--k", str(args.k),
                "--max-grid-cells", str(args.max_grid_cells),
                "--experiment-id", args.experiment_id,
                "--corridor-policy", args.corridor_policy,
                "--corridor-cost-factor", str(args.corridor_cost_factor),
                "--rank-penalty-factors", args.rank_penalty_factors,
                "--execute",
            ]
            if args.model_revision_key:
                command.extend(["--model-revision-key", args.model_revision_key])
            if args.keep_owner_equipment:
                command.append("--keep-owner-equipment")
            if context_first:
                command.append("--context-first")
            print(
                f"Campaign batch project={project_id}, tasks={len(batch)}, "
                f"order={'context-first' if context_first else 'baseline-first'}"
            )
            completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], check=False)
            batches.append({
                "project_id": project_id,
                "route_guids": [row["route_guid"] for row in batch],
                "context_first": context_first,
                "exit_code": completed.returncode,
            })
            if completed.returncode != 0 and not args.continue_on_error:
                raise RuntimeError(f"Campaign batch failed for project {project_id}: exit={completed.returncode}")
    return batches


def main() -> int:
    """campaign 계획, 선택, 선택적 실행, 최종 분석 보고서 생성을 조정한다."""
    parser = argparse.ArgumentParser(description="Run a stratified Context Vector routing A/B campaign")
    tool_config.add_common_args(parser)
    parser.add_argument("--target-pairs", type=int, default=30, help="Target total completed pairs")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--cell-mm", type=float, default=100.0)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--max-grid-cells", type=int, default=250_000_000)
    parser.add_argument("--seed", default="context-ab-v1")
    parser.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--corridor-policy", choices=("ranked", "rank1", "top2", "union"), default="ranked")
    parser.add_argument("--corridor-cost-factor", type=float, default=0.5)
    parser.add_argument("--rank-penalty-factors", default="0,0.5,0.75")
    parser.add_argument("--model-revision-key", default="")
    parser.add_argument("--keep-owner-equipment", action="store_true")
    parser.add_argument("--exclude-reference-experiment", default="")
    parser.add_argument("--exclude-fail-reasons", default="StartBlocked,NoPath")
    parser.add_argument("--runner-dll", default="ContextRoutingABRunner/bin/Debug/net8.0-windows/ContextRoutingABRunner.dll")
    parser.add_argument("--plan-json", default="data/output/context_ab_campaign_plan.json")
    parser.add_argument("--report-json", default="data/output/context_routing_ab_phase8_report.json")
    parser.add_argument("--report-md", default="Docs/ContextVector_Phase8_Routing_AB_Report.md")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()
    if (args.target_pairs < 1 or args.batch_size < 1 or args.cell_mm <= 0 or
            args.k < 1 or args.corridor_cost_factor < 0):
        parser.error("target-pairs, batch-size, cell-mm, and k must be positive")

    runtime = tool_config.resolve_runtime(args)
    tool_config.print_runtime(runtime)
    import psycopg2

    with psycopg2.connect(runtime.conninfo) as conn:
        projects = load_projects(conn)
        completed = completed_pairs(conn, args.experiment_id)
        excluded_reasons = {value.strip() for value in args.exclude_fail_reasons.split(",") if value.strip()}
        excluded_guids = failure_cohort(conn, args.exclude_reference_experiment, excluded_reasons)
        needed = max(0, args.target_pairs - len(completed))
        candidates = load_candidates(conn, projects, completed, args.seed, excluded_guids)
        selected = stratified_select(candidates, needed)
        plan = {
            "experiment_id": args.experiment_id,
            "corridor_policy": args.corridor_policy,
            "corridor_cost_factor": args.corridor_cost_factor,
            "rank_penalty_factors": args.rank_penalty_factors,
            "model_revision_key": args.model_revision_key or None,
            "owner_equipment_release": not args.keep_owner_equipment,
            "excluded_reference_experiment": args.exclude_reference_experiment or None,
            "excluded_fail_reasons": sorted(excluded_reasons),
            "excluded_route_count": len(excluded_guids),
            "target_total_pairs": args.target_pairs,
            "completed_before": len(completed),
            "needed": needed,
            "available_candidates": len(candidates),
            "selected_count": len(selected),
            "selection": selected,
            "execute": args.execute,
        }
        write_plan(Path(args.plan_json).resolve(), plan)
        by_band = defaultdict(int)
        for row in selected:
            by_band[row["distance_band"]] += 1
        print(f"Completed={len(completed)}, needed={needed}, selected={len(selected)}, bands={dict(by_band)}")
        if len(selected) < needed:
            raise RuntimeError(f"Insufficient candidates: needed={needed}, selected={len(selected)}")

    if not args.execute:
        print("DRY-RUN PASS. Add --execute to run the planned campaign.")
        return 0

    batches = execute_batches(args, selected)
    with psycopg2.connect(runtime.conninfo) as conn:
        report = summarize(load_latest(conn, args.experiment_id), args.experiment_id)
    report["campaign"] = {"batches": batches, "plan_json": str(Path(args.plan_json).resolve())}
    report_json, report_md = Path(args.report_json).resolve(), Path(args.report_md).resolve()
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report_md.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report JSON: {report_json}")
    print(f"Report Markdown: {report_md}")
    print(f"Completed pairs after campaign: {report['paired']['requests']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
