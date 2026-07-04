"""
================================================================================
obstacle_map.py  ─  장애물 맵 로더 및 밀도 텐서 생성 모듈
================================================================================

【실행 명령어】
  ※ 이 모듈은 직접 실행하지 않으며 run_routing.py 또는 timeline_viewer.py 에서 호출한다.
  ※ 독립 테스트:
      cd RubberBandRouter
      python -m pytest tests/test_data_loader.py::TestSceneToObstacleMap -v

  ※ 캐시 강제 재생성:
      from core.obstacle_map import get_or_build_obstacle_map
      import config as cfg
      obs_map = get_or_build_obstacle_map(
          conninfo=cfg.get_conninfo(),
          project_id="PROJ-001",
          cache_dir=cfg.LEGACY_MAP_DIR,
          force_rebuild=True,   # ← 캐시 무시하고 DB에서 재로드
      )

================================================================================
【단계별 흐름도】

  ┌─────────────────────────────────────────────────────────────┐
  │  호출: get_or_build_obstacle_map(conninfo, pid, cache_dir)  │
  └───────────────┬─────────────────────────────────────────────┘
                  │
          ┌───────▼────────┐
          │ 캐시 파일       │  obstacle_map_{pid}.pkl
          │ 존재하는가?     │
          └───┬─────────┬──┘
           예 │         │ 아니오 / force_rebuild=True
              ▼         ▼
     load_obstacle_    load_obstacles_from_db()
     map_cache()        │
          │             │ DB: TB_EQUIPMENT 등 조회
          │             │ OBB 24정점 → from_24_vertices()
          │             │   ├─ SVD로 로컬 축 추정
          │             │   ├─ 반-크기(half_extents) 계산
          │             │   └─ 부피(volume) 계산
          │             │
          │             ▼
          │         passthrough 객체 필터링
          │         (GRATING, FLOOR, is_penetration=True 제외)
          │             │
          │             ▼
          │         build_density_tensor()
          │             │
          │         ┌───▼────────────────────────────────────┐
          │         │ 알고리즘: AABB 근사 복셀화              │
          │         │                                         │
          │         │  for each OBBObstacle:                  │
          │         │    mn = floor(verts.min / grid_size)    │
          │         │    mx = ceil (verts.max / grid_size)    │
          │         │    tensor[mn:mx, mn:mx, mn:mx] = 1      │
          │         │                                         │
          │         │  결과: (30, 30, 30) numpy int8 텐서     │
          │         │  0 = 빈 공간, 1 = 장애물 점유 셀        │
          │         └───────────────────────────────────────┬─┘
          │                                                 │
          ▼                                                 ▼
     ObstacleMap                                    save_obstacle_map()
     (캐시에서)                                      → .pkl 저장
          │                                                 │
          └──────────────────┬──────────────────────────────┘
                             ▼
                       ObstacleMap 반환
                       (topology_matcher.py 에서 cosine similarity 계산)

================================================================================
【핵심 알고리즘: OBB → SVD 로컬 축 추정】

  DB에서 8개 꼭짓점 좌표가 주어졌을 때 OBB 의 로컬 좌표계를 복원한다:

  1) 중심 계산:   center = mean(verts, axis=0)         # (3,)
  2) SVD 분해:   _, _, vh = svd(verts - center)
                  axes = vh                             # (3,3), 각 행이 로컬 축 단위벡터
  3) 반-크기:    proj = (verts - center) @ axes.T      # (8,3): 각 꼭짓점의 로컬 좌표
                  half_extents = (proj.max - proj.min) / 2   # (3,)
  4) 부피:       volume = prod(half_extents * 2)

  이렇게 구한 axes, half_extents 는 collision.py 의 SAT 충돌 검사에서
  OBB 로컬 공간 변환에 바로 사용된다.

================================================================================
【주요 클래스 / 함수】

  OBBObstacle
    .name           장애물 이름 (DB INSTANCE_NAME)
    .vertices       (8,3) ndarray — 8개 꼭짓점 월드 좌표 (mm)
    .center         (3,)  ndarray — OBB 중심 (mm)
    .half_extents   (3,)  ndarray — 로컬 x/y/z 반-크기 (mm)
    .axes           (3,3) ndarray — 로컬 축 단위벡터 (행 = 축)
    .volume         float — OBB 부피 (mm³)
    .is_penetration bool  — True: 슬리브 통과 허용
    .from_24_vertices()   클래스메서드, SVD 기반 OBB 복원

  ObstacleMap
    .obstacles[]    OBBObstacle 목록
    .density_tensor (G,G,G) int8 — G = SPACE_MAX//GRID_SIZE = 30
    .grid_dim       텐서 차원 = space_max // grid_size
    .build_density_tensor() AABB 근사 복셀화 → 텐서 생성

  get_or_build_obstacle_map()   캐시 우선 로드 통합 진입점
  load_obstacles_from_db()      DB 직접 조회 로더
  save_obstacle_map()           pickle 직렬화 저장
  load_obstacle_map_cache()     pickle 역직렬화 로드

================================================================================
"""
from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 클래스
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OBBObstacle:
    """OBB(Oriented Bounding Box) 장애물 정보."""
    name: str
    source_table: str
    project_id: str
    vertices: np.ndarray          # shape (8, 3) - 8 꼭짓점 × XYZ
    center: np.ndarray            # shape (3,) - OBB 중심
    half_extents: np.ndarray      # shape (3,) - 로컬 반-크기
    axes: np.ndarray              # shape (3, 3) - OBB 로컬 축 (행 = 축)
    volume: float                 # OBB 부피 (mm³)
    is_penetration: bool = False  # 슬리브/관통 허용 여부
    obj_type: str = ""            # 객체 유형 (GRATING, FLOOR 등 pass-through 식별)

    @classmethod
    def from_24_vertices(
        cls,
        name: str,
        source_table: str,
        project_id: str,
        vertices_24: list[list[float]],
        is_penetration: bool = False,
        obj_type: str = "",
    ) -> "OBBObstacle":
        """
        24개 정점(8꼭짓점 × 3좌표) 리스트로부터 OBBObstacle 생성.

        vertices_24: [[x0,y0,z0], [x1,y1,z1], ..., [x7,y7,z7]]
        (DB 필드 OBB_LEFT_BOTTOM_BACK_X/Y/Z ~ OBB_RIGHT_TOP_FRONT_X/Y/Z 순서)
        """
        verts = np.array(vertices_24, dtype=float)  # (8, 3)

        center = verts.mean(axis=0)

        # 중심에서 꼭짓점 벡터로 로컬 축 추정 (SVD)
        _, _, vh = np.linalg.svd(verts - center)
        axes = vh  # (3, 3)

        # 각 축 방향 투영 반-범위
        proj = (verts - center) @ axes.T  # (8, 3)
        half_extents = proj.max(axis=0) - proj.min(axis=0)
        half_extents = half_extents / 2.0
        volume = float(np.prod(half_extents * 2))

        return cls(
            name=name,
            source_table=source_table,
            project_id=project_id,
            vertices=verts,
            center=center,
            half_extents=half_extents,
            axes=axes,
            volume=volume,
            is_penetration=is_penetration,
            obj_type=obj_type,
        )


@dataclass
class ObstacleMap:
    """현재(또는 레거시) 프로젝트의 장애물 맵."""
    project_id: str
    obstacles: list[OBBObstacle] = field(default_factory=list)
    density_tensor: np.ndarray | None = None   # shape (G, G, G) binary int8
    grid_size: int = 1_000                     # 복셀 크기 (mm)
    space_max: int = 30_000                    # 공간 최대 크기 (mm)

    @property
    def grid_dim(self) -> int:
        return self.space_max // self.grid_size

    def build_density_tensor(self) -> np.ndarray:
        """
        장애물 리스트에서 3D 바이너리 밀도 텐서를 생성한다.

        각 OBB의 AABB(축-정렬 바운딩 박스)를 먼저 계산하고,
        해당 복셀 셀을 1로 채운다.
        (정밀한 OBB 복셀화는 위상 매칭용으로 AABB 근사로 충분)
        """
        G = self.grid_dim
        tensor = np.zeros((G, G, G), dtype=np.int8)

        for obs in self.obstacles:
            verts = obs.vertices
            mn = np.floor(verts.min(axis=0) / self.grid_size).astype(int)
            mx = np.ceil(verts.max(axis=0) / self.grid_size).astype(int)

            # 경계 클리핑
            mn = np.clip(mn, 0, G - 1)
            mx = np.clip(mx, 0, G - 1)

            tensor[mn[0]:mx[0]+1, mn[1]:mx[1]+1, mn[2]:mx[2]+1] = 1

        self.density_tensor = tensor
        logger.info(
            "[ObstacleMap] project=%s, obstacles=%d, tensor_shape=%s, occupied=%d",
            self.project_id, len(self.obstacles), tensor.shape, int(tensor.sum()),
        )
        return tensor


# ─────────────────────────────────────────────────────────────────────────────
# DB 로더
# ─────────────────────────────────────────────────────────────────────────────

# OBB 24 정점 필드 순서 (DB 스키마 기준)
_OBB_KEYS = [
    "OBB_LEFT_BOTTOM_BACK",
    "OBB_RIGHT_BOTTOM_BACK",
    "OBB_LEFT_TOP_BACK",
    "OBB_RIGHT_TOP_BACK",
    "OBB_LEFT_BOTTOM_FRONT",
    "OBB_RIGHT_BOTTOM_FRONT",
    "OBB_LEFT_TOP_FRONT",
    "OBB_RIGHT_TOP_FRONT",
]


def _parse_obb_vertices(row: dict[str, Any]) -> list[list[float]] | None:
    """DB 행 딕셔너리에서 OBB 8 꼭짓점 리스트를 파싱한다."""
    vertices = []
    for key in _OBB_KEYS:
        x = row.get(f"{key}_X") or row.get(f"{key.lower()}_x")
        y = row.get(f"{key}_Y") or row.get(f"{key.lower()}_y")
        z = row.get(f"{key}_Z") or row.get(f"{key.lower()}_z")
        if x is None or y is None or z is None:
            return None
        try:
            vertices.append([float(x), float(y), float(z)])
        except (TypeError, ValueError):
            return None
    return vertices


def _is_passthrough(obj_type: str, passthrough_types: set[str]) -> bool:
    return obj_type.upper().strip() in passthrough_types


def load_obstacles_from_db(
    conninfo: str,
    project_id: str,
    passthrough_types: set[str] | None = None,
) -> ObstacleMap:
    """
    DB에서 장애물 OBB 데이터를 로드하여 ObstacleMap을 반환한다.

    project_id: 현재 프로젝트 ID (레거시 맵 로드 시 다른 ID 전달)
    """
    import sys
    # config를 상위 패키지에서 import
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config as cfg

    if passthrough_types is None:
        passthrough_types = cfg.PASSTHROUGH_TYPES

    import psycopg2
    import psycopg2.extras

    obstacle_map = ObstacleMap(project_id=project_id)

    query_equipment = f"""
        SELECT
            COALESCE(INSTANCE_NAME, 'UNNAMED') AS name,
            '{cfg.TABLE_EQUIPMENT}' AS source_table,
            COALESCE(PROJECT_ID::text, '') AS project_id,
            COALESCE({cfg.PENETRATION_FIELD}::text, 'false') AS is_penetration,
            COALESCE(EQUIP_TYPE, '') AS obj_type,
            {', '.join(
                f'{key}_X, {key}_Y, {key}_Z'
                for key in _OBB_KEYS
            )}
        FROM {cfg.TABLE_EQUIPMENT}
        WHERE PROJECT_ID = %(pid)s
    """

    queries = [
        (query_equipment, cfg.TABLE_EQUIPMENT),
    ]

    with psycopg2.connect(conninfo) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for sql, tbl in queries:
                try:
                    cur.execute(sql, {"pid": project_id})
                    rows = cur.fetchall()
                except Exception as exc:
                    logger.warning("[ObstacleMap] 테이블 %s 조회 실패: %s", tbl, exc)
                    continue

                for row in rows:
                    row = dict(row)
                    obj_type = str(row.get("obj_type", ""))
                    if _is_passthrough(obj_type, passthrough_types):
                        logger.debug("[ObstacleMap] pass-through 제외: %s", row.get("name"))
                        continue

                    vertices = _parse_obb_vertices(row)
                    if vertices is None:
                        logger.debug("[ObstacleMap] OBB 파싱 실패 (필드 없음): %s", row.get("name"))
                        continue

                    is_pen = str(row.get("is_penetration", "false")).lower() in ("true", "1", "yes")
                    obs = OBBObstacle.from_24_vertices(
                        name=str(row.get("name", "")),
                        source_table=str(row.get("source_table", tbl)),
                        project_id=project_id,
                        vertices_24=vertices,
                        is_penetration=is_pen,
                        obj_type=obj_type,
                    )
                    obstacle_map.obstacles.append(obs)

    logger.info(
        "[ObstacleMap] DB 로드 완료 - project=%s, 총 장애물=%d",
        project_id, len(obstacle_map.obstacles),
    )
    return obstacle_map


# ─────────────────────────────────────────────────────────────────────────────
# 캐시 저장 / 로드
# ─────────────────────────────────────────────────────────────────────────────

def save_obstacle_map(obstacle_map: ObstacleMap, cache_dir: Path) -> Path:
    """ObstacleMap을 pickle 캐시로 저장한다."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"obstacle_map_{obstacle_map.project_id}.pkl"
    with path.open("wb") as f:
        pickle.dump(obstacle_map, f, protocol=pickle.HIGHEST_PROTOCOL)
    logger.info("[ObstacleMap] 캐시 저장: %s", path)
    return path


def load_obstacle_map_cache(project_id: str, cache_dir: Path) -> ObstacleMap | None:
    """pickle 캐시에서 ObstacleMap을 로드한다. 없으면 None 반환."""
    path = cache_dir / f"obstacle_map_{project_id}.pkl"
    if not path.exists():
        return None
    with path.open("rb") as f:
        obs_map: ObstacleMap = pickle.load(f)
    logger.info("[ObstacleMap] 캐시 로드: %s (%d 장애물)", path, len(obs_map.obstacles))
    return obs_map


def get_or_build_obstacle_map(
    conninfo: str,
    project_id: str,
    cache_dir: Path,
    force_rebuild: bool = False,
    passthrough_types: set[str] | None = None,
) -> ObstacleMap:
    """
    캐시가 있으면 로드, 없으면 DB에서 로드 후 텐서 생성 및 캐시 저장.
    force_rebuild=True 이면 항상 DB에서 새로 로드.
    """
    if not force_rebuild:
        cached = load_obstacle_map_cache(project_id, cache_dir)
        if cached is not None:
            if cached.density_tensor is None:
                cached.build_density_tensor()
            return cached

    obs_map = load_obstacles_from_db(conninfo, project_id, passthrough_types)
    obs_map.build_density_tensor()
    save_obstacle_map(obs_map, cache_dir)
    return obs_map
