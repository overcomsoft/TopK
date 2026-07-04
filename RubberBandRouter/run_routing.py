"""
run_routing.py
--------------
RubberBandRouter PoC 통합 실행 스크립트.

실행 흐름:
  1. PostgreSQL DB에서 프로젝트 선택
  2. 장애물 + 장비PoC + 덕트/레터럴PoC + 기존 라우팅 작업 로드
  3. 라우팅 작업 목록 표시 → 사용자가 1건 선택 (또는 --task 인수)
  4. RubberBand 라우팅 엔진 실행 (Pull & Push)
  5. 배관 분배 후 결과 JSON 저장
  6. Plotly Dash 4단계 시각화 디버거 실행

사용법:
    python run_routing.py                          # 대화형 선택
    python run_routing.py --project 2 --task 0    # 비대화형 (자동화)
    python run_routing.py --no-dash                # 시각화 없이 JSON만 저장
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import numpy as np

import config as cfg
from core.data_loader import (
    load_scene,
    scene_to_obstacle_map,
    select_project,
    RoutingTask,
    RoutingScene,
)
from core.feature_extractor import FeatureSet
from core.rubber_band import run_routing
from core.pipe_distributor import distribute_pipes

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 라우팅 작업 선택
# ─────────────────────────────────────────────────────────────────────────────

def select_task(scene: RoutingScene, task_idx: int | None = None) -> RoutingTask:
    """라우팅 작업 목록을 표시하고 사용자가 선택한 1건을 반환."""
    tasks = scene.tasks
    if not tasks:
        raise RuntimeError(
            "DB에 라우팅 작업(TB_ROUTE_PATH)이 없습니다. "
            "장비 PoC와 덕트/레터럴 PoC가 연결된 데이터가 필요합니다."
        )

    if task_idx is not None:
        if 0 <= task_idx < len(tasks):
            return tasks[task_idx]
        raise ValueError(f"task_idx={task_idx} 범위 초과 (0~{len(tasks)-1})")

    print(f"\n=== 라우팅 작업 목록 (총 {len(tasks)}건) ===")
    for i, t in enumerate(tasks[:50]):   # 최대 50건 표시
        sx, sy, sz = t.start_x, t.start_y, t.start_z
        ex, ey, ez = t.end_x, t.end_y, t.end_z
        print(
            f"  [{i:3d}] {t.source_name or '?'} → {t.target_name or '?'} "
            f"| util={t.utility or '-'} | dia={t.diameter_mm:.0f}mm "
            f"| S=({sx:.0f},{sy:.0f},{sz:.0f}) E=({ex:.0f},{ey:.0f},{ez:.0f})"
        )
    if len(tasks) > 50:
        print(f"  ... (이하 {len(tasks)-50}건 생략)")

    print()
    while True:
        try:
            choice = int(input(f"작업 번호 입력 (0~{min(len(tasks)-1, 49)}): ").strip())
            if 0 <= choice < len(tasks):
                logger.info("[Runner] 선택 작업: %s", tasks[choice])
                return tasks[choice]
        except (ValueError, EOFError):
            pass
        print(f"  → 올바른 번호를 입력하세요 (0~{min(len(tasks)-1, 49)}).")


# ─────────────────────────────────────────────────────────────────────────────
# 메인 파이프라인
# ─────────────────────────────────────────────────────────────────────────────

def run(
    project_id: int | None = None,
    task_idx: int | None = None,
    launch_dash: bool = True,
    pipe_count: int | None = None,
) -> None:
    # ── Step 1: DB → 프로젝트 선택 ───────────────────────────────────────────
    conninfo = cfg.get_conninfo()
    proj = select_project(conninfo, project_id)

    # ── Step 2: 씬 로드 ──────────────────────────────────────────────────────
    print(f"\n[1/5] 씬 데이터 로드 중: {proj.display} ...")
    scene = load_scene(conninfo, proj)
    print(f"  → {scene.summary()}")

    # ── Step 3: 라우팅 작업 선택 ─────────────────────────────────────────────
    task = select_task(scene, task_idx)
    print(f"\n[2/5] 선택된 작업: {task}")

    # ── Step 4: 장애물 맵 변환 ───────────────────────────────────────────────
    print("\n[3/5] 장애물 맵 생성 중...")
    current_map = scene_to_obstacle_map(scene)

    # ── Step 5: RubberBand 라우팅 ────────────────────────────────────────────
    print("\n[4/5] 라우팅 엔진 실행 중...")
    # Case C (레거시 데이터 없음) → 순수 기하학 라우팅
    feature_set = FeatureSet(case="C")

    start = task.start
    end   = task.end

    routing_result = run_routing(
        start=start,
        end=end,
        feature_set=feature_set,
        current_map=current_map,
        max_vertical_bends=cfg.MAX_VERTICAL_BENDS,
        tray_half_width=cfg.TRAY_WIDTH / 2.0,
        tray_height=cfg.TRAY_HEIGHT,
        safety_margin=cfg.SAFETY_MARGIN,
    )
    print(f"  → {routing_result.summary()}")

    # ── Step 6: 배관 분배 & 저장 ─────────────────────────────────────────────
    print("\n[5/5] 배관 분배 및 결과 저장 중...")
    n_pipes = pipe_count or cfg.PIPE_COUNT
    dist_result = distribute_pipes(
        routing_result.final_segments,
        pipe_count=n_pipes,
    )

    out_path = cfg.RESULTS_DIR / f"routing_{proj.group_id}_{task.route_path_guid[:8]}.json"
    dist_result.save_json(out_path)
    print(f"  → 결과 저장: {out_path}")

    # ── 시각화 디버거 ─────────────────────────────────────────────────────────
    if launch_dash:
        print(f"\n  ▶ 브라우저에서 http://localhost:{cfg.VIZ_PORT} 접속하세요.")
        from debugger.scene_builder import build_all_scenes
        from debugger.timeline_viewer import create_dash_app

        # 장애물 목록 (시각화용, PassThrough 제외)
        all_obs = [
            obs for obs in current_map.obstacles
        ]

        scenes = build_all_scenes(routing_result.states, dist_result, all_obs)
        app = create_dash_app(scenes)
        app.run(debug=cfg.VIZ_DEBUG, port=cfg.VIZ_PORT)
    else:
        print("\n시각화 생략 (--no-dash)")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="RubberBandRouter PoC - DB 연동 라우팅 실행")
    parser.add_argument("--project", type=int, default=None, help="프로젝트 번호 (생략 시 대화형 선택)")
    parser.add_argument("--task",    type=int, default=None, help="라우팅 작업 인덱스 (생략 시 대화형 선택)")
    parser.add_argument("--pipes",   type=int, default=None, help="배관 수 (기본: config.PIPE_COUNT)")
    parser.add_argument("--no-dash", action="store_true",    help="Dash 시각화 디버거 실행 안 함")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    run(
        project_id=args.project,
        task_idx=args.task,
        launch_dash=not args.no_dash,
        pipe_count=args.pipes,
    )


if __name__ == "__main__":
    main()
