"""
================================================================================
run_routing.py  ─  RubberBandRouter PoC 통합 실행 스크립트
================================================================================

【실행 명령어】
  ※ 대화형 선택 모드 (일반 실행):
      cd RubberBandRouter
      python run_routing.py

  ※ 자동 실행 모드 (CLI 인수 지정):
      python run_routing.py --project 2 --task 0 --pipes 4

  ※ 시각화 웹서버 없이 결과 JSON만 추출할 때:
      python run_routing.py --project 2 --task 0 --no-dash

================================================================================
【전체 통합 실행 흐름도】

  1. config.py 에서 DB Connection Info 획득 (get_conninfo)
  2. select_project()
     └─ list_projects() 로 프로젝트 목록을 불러와 CLI에 출력하고 사용자 입력 접수
  3. load_scene()
     └─ PostgreSQL DB 로부터 해당 프로젝트의 장애물, 장비, PoC 및 태스크 정보 일괄 로드
  4. select_task()
     └─ 로드된 라우팅 태스크 목록 중 수행할 작업 번호 선택
  5. scene_to_obstacle_map()
     └─ 수집된 AABB 장애물들을 core OBBObstacle 포맷으로 변환하고 밀도 텐서 빌드
  6. run_routing()
     └─ Step 1(Tension), Step 2(Snap), Step 3(Collision Avoidance) 라우팅 엔진 구동
  7. distribute_pipes()
     └─ 도출된 트레이 중심선 경로를 평행 평면상에 지정된 배관 개수만큼 오프셋 배치
  8. dist_result.save_json()
     └─ 최종 배치 결과를 json 파일로 data/results 폴더 아래 저장
  9. build_all_scenes() & create_dash_app()
     └─ launch_dash가 True 인 경우 단계별 Plotly Scene을 로드하여 Dash 웹서버(8050) 시작

================================================================================
【주요 함수 / 인수 및 변수】

  select_task()             작업 리스트 중 실행할 태스크 인덱스를 사용자로부터 입력받음
  run()                     전체 모듈(DB 로드 -> 엔진 실행 -> 결과 저장 -> 시각화)을
                            하나의 순차적인 흐름으로 결합하여 실행
  main()                    CLI argument parser 구성 및 logging 초기화
  
  KEY ARGUMENTS:
    --project               Project Info Sequence Number (1-based index)
    --task                  Route Path Task Index (0-based index)
    --pipes                 생성할 평행 배관의 수
    --no-dash               True 지정 시 3D Plotly Dash 기동을 스킵
================================================================================
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
