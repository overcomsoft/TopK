"""
data_loader.py
--------------
PostgreSQL(DDW_AI_DB) → RubberBandRouter 데이터 로더.

C# ObstacleDbLoader.cs / SceneData.cs 의 DB 쿼리 패턴을 Python으로 재구현.

제공 기능:
  1. list_projects()       → TB_SPACE_GROUP_INFO 프로젝트 목록
  2. load_obstacles()      → TB_BIM_OBSTACLE (장애물 AABB, PassThrough 처리)
  3. load_equipment_pocs() → TB_EQUIPMENTS + TB_POCINSTANCES (장비 + 장비 PoC)
  4. load_duct_pocs()      → TB_LATERAL_PIPE / TB_DUCT + TB_POCINSTANCES (덕트/레터럴 종단 PoC)
  5. load_routing_tasks()  → TB_ROUTE_PATH (기존 설계 Source→Target 좌표, 작업 목록)
  6. load_scene()          → 위 모두를 한 번에 RoutingScene 으로 반환
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 공간 스코프 여유값 (C# ScopeMarginMm = 500.0)
SCOPE_MARGIN_MM: float = 500.0

# Pass-through 판별용 OST 타입 (C# ObstacleBox.IsPassThrough)
_PASSTHROUGH_OST = {"OST_FLOORS", "OST_CEILINGS"}
_PASSTHROUGH_DDWORKS_BEAM = "BEAM_STRUCTURE"
_PASSTHROUGH_OST_FRAMING  = "OST_STRUCTURALFRAMING"


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 클래스
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProjectInfo:
    """TB_SPACE_GROUP_INFO 1행 — 프로젝트(=툴 그룹) 메타."""
    project_id: int              # 1-based 순번 (C# ProjectId)
    group_id: str                # TAG_GROUP_ID
    group_name: str              # TAG_GROUP_NM
    bay: str | None              # BAY_GROUP_NM
    process: str | None          # PROCESS_GROUP_NM
    min_x: float = 0.0
    min_y: float = 0.0
    min_z: float = 0.0
    max_x: float = 0.0
    max_y: float = 0.0
    max_z: float = 0.0

    @property
    def display(self) -> str:
        return f"{self.group_name} / {self.bay or '?'} / {self.process or '?'}"

    def __str__(self) -> str:
        return self.display


@dataclass
class ObstacleAABB:
    """장애물 AABB (C# ObstacleBox 대응)."""
    name: str
    ost_type: str
    ddworks_type: str
    collision_pass: bool | None   # None=미지정, True=통과, False=충돌
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    @property
    def is_pass_through(self) -> bool:
        """C# ObstacleBox.IsPassThrough 동일 로직."""
        if self.collision_pass is not None:
            return self.collision_pass
        ost = (self.ost_type or "").strip().upper()
        if ost in _PASSTHROUGH_OST:
            return True
        if ost == _PASSTHROUGH_OST_FRAMING and \
                (self.ddworks_type or "").strip().upper() == _PASSTHROUGH_DDWORKS_BEAM:
            return True
        return False

    @property
    def center(self) -> np.ndarray:
        return np.array([
            (self.min_x + self.max_x) / 2.0,
            (self.min_y + self.max_y) / 2.0,
            (self.min_z + self.max_z) / 2.0,
        ])

    @property
    def half_extents(self) -> np.ndarray:
        return np.array([
            (self.max_x - self.min_x) / 2.0,
            (self.max_y - self.min_y) / 2.0,
            (self.max_z - self.min_z) / 2.0,
        ])

    @property
    def volume(self) -> float:
        return float(
            max(0, self.max_x - self.min_x) *
            max(0, self.max_y - self.min_y) *
            max(0, self.max_z - self.min_z)
        )


@dataclass
class EquipmentInfo:
    """장비 박스 + 메타 (C# EquipmentBox 확장)."""
    name: str
    is_main: bool
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float


@dataclass
class PocPoint:
    """PoC(Point of Connection) 좌표 + 메타 (C# PocMarker 대응)."""
    name: str              # POC_NAME / INSTANCE_NAME
    owner_name: str        # 소유 장비/덕트/레터럴 이름
    owner_type: str        # OWNER_INSTANCE_TYPE (Equipment / Duct / Lateral / Unknown)
    utility: str | None    # UTILITY
    x: float
    y: float
    z: float

    @property
    def position(self) -> np.ndarray:
        return np.array([self.x, self.y, self.z])

    def __str__(self) -> str:
        return f"PoC({self.name}@{self.owner_name} [{self.owner_type}] xyz=[{self.x:.0f},{self.y:.0f},{self.z:.0f}])"


@dataclass
class RoutingTask:
    """라우팅 작업 1개 — 출발(장비 PoC) → 목적(덕트/레터럴 PoC) (C# TaskInfo 대응)."""
    route_path_guid: str
    utility: str | None
    utility_group: str | None
    diameter_mm: float
    # 출발 좌표 (장비 PoC = SOURCE_POS)
    start_x: float
    start_y: float
    start_z: float
    # 목적 좌표 (덕트/레터럴 PoC = TARGET_POS)
    end_x: float
    end_y: float
    end_z: float
    source_name: str | None    # EQUIPMENT_NAME
    target_name: str | None    # TARGET_OWNER_NAME

    @property
    def start(self) -> np.ndarray:
        return np.array([self.start_x, self.start_y, self.start_z])

    @property
    def end(self) -> np.ndarray:
        return np.array([self.end_x, self.end_y, self.end_z])

    def __str__(self) -> str:
        return (
            f"Task({self.source_name}→{self.target_name}, "
            f"util={self.utility}, dia={self.diameter_mm:.0f}mm)"
        )


@dataclass
class RoutingScene:
    """로딩된 씬 전체 데이터 (C# SceneData 대응)."""
    project: ProjectInfo
    obstacles: list[ObstacleAABB] = field(default_factory=list)
    equipment: list[EquipmentInfo] = field(default_factory=list)
    equipment_pocs: list[PocPoint] = field(default_factory=list)
    duct_lateral_pocs: list[PocPoint] = field(default_factory=list)
    tasks: list[RoutingTask] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"RoutingScene[{self.project.display}]: "
            f"장애물={len(self.obstacles)}, "
            f"장비={len(self.equipment)}, "
            f"장비PoC={len(self.equipment_pocs)}, "
            f"덕트PoC={len(self.duct_lateral_pocs)}, "
            f"라우팅작업={len(self.tasks)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def _dbl(row: dict, *keys: str, default: float = 0.0) -> float:
    """딕셔너리 행에서 첫 번째로 존재하는 키의 float 값을 반환."""
    for k in keys:
        v = row.get(k) or row.get(k.lower())
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return default


def _str(row: dict, *keys: str, default: str = "") -> str:
    for k in keys:
        v = row.get(k) or row.get(k.lower())
        if v is not None and str(v).strip():
            return str(v).strip()
    return default


def _parse_pipe_size_mm(size_str: str | None) -> float:
    """
    관경 문자열(예: '40A', '2inch', '50') → 외경 mm 근사.
    C# ParsePipeSizeMm 로직 재현.
    """
    if not size_str:
        return 0.0
    s = size_str.strip().upper()
    import re
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return 0.0
    val = float(m.group(1))
    # 'A'(호칭경mm) → 외경 근사 (배관 규격표 근사)
    if "A" in s:
        # 호칭경 → 외경 근사: 근사값 사용
        nominal_to_od = {
            15: 21.7, 20: 27.2, 25: 34.0, 32: 42.7, 40: 48.6, 50: 60.5,
            65: 76.3, 80: 89.1, 100: 114.3, 125: 139.8, 150: 165.2,
            200: 216.3, 250: 267.4, 300: 318.5,
        }
        return nominal_to_od.get(int(val), val * 1.3)
    # 'INCH' → mm 변환
    if "INCH" in s or "\"" in s:
        return val * 25.4
    return val


def _classify_owner_type(owner_type: str, owner_name: str) -> str:
    """
    OWNER_INSTANCE_TYPE 문자열로 PoC 소유 분류.
    C# ClassifyPoc 로직 재현.
    """
    key = (owner_type + " " + owner_name).upper()
    if "LATERAL" in key:
        return "Lateral"
    if "DUCT" in key:
        return "Duct"
    if any(k in key for k in ("EQUIP", "MODEL", "TOOL", "MAIN")):
        return "Equipment"
    return "Unknown"


def _scope_filter_sql(alias: str = "") -> str:
    """XY 공간 교차 필터 SQL 조각 (C# IsectXY 패턴)."""
    pfx = f"{alias}." if alias else ""
    return (
        f' {pfx}"AABB_MINX" <= %(maxx)s AND {pfx}"AABB_MAXX" >= %(minx)s'
        f' AND {pfx}"AABB_MINY" <= %(maxy)s AND {pfx}"AABB_MAXY" >= %(miny)s '
    )


def _make_scope_params(proj: ProjectInfo) -> dict:
    return {
        "minx": proj.min_x - SCOPE_MARGIN_MM,
        "maxx": proj.max_x + SCOPE_MARGIN_MM,
        "miny": proj.min_y - SCOPE_MARGIN_MM,
        "maxy": proj.max_y + SCOPE_MARGIN_MM,
        "minz": proj.min_z - SCOPE_MARGIN_MM,
        "maxz": proj.max_z + SCOPE_MARGIN_MM,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. 프로젝트 목록
# ─────────────────────────────────────────────────────────────────────────────

def list_projects(conninfo: str) -> list[ProjectInfo]:
    """
    TB_SPACE_GROUP_INFO 에서 프로젝트(툴 그룹) 목록을 로드한다.
    C# ObstacleDbLoader.ListProjects() 대응.
    """
    sql = """
        SELECT "TAG_GROUP_ID", "TAG_GROUP_NM", "BAY_GROUP_NM", "PROCESS_GROUP_NM",
               "AABB_MINX", "AABB_MINY", "AABB_MINZ",
               "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"
          FROM "TB_SPACE_GROUP_INFO"
         ORDER BY "PROCESS_GROUP_NM", "TAG_GROUP_NM"
    """
    projects: list[ProjectInfo] = []
    with psycopg2.connect(conninfo) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            for seq, row in enumerate(cur.fetchall(), start=1):
                row = dict(row)
                projects.append(ProjectInfo(
                    project_id=seq,
                    group_id=_str(row, "TAG_GROUP_ID"),
                    group_name=_str(row, "TAG_GROUP_NM"),
                    bay=row.get("BAY_GROUP_NM"),
                    process=row.get("PROCESS_GROUP_NM"),
                    min_x=_dbl(row, "AABB_MINX"),
                    min_y=_dbl(row, "AABB_MINY"),
                    min_z=_dbl(row, "AABB_MINZ"),
                    max_x=_dbl(row, "AABB_MAXX"),
                    max_y=_dbl(row, "AABB_MAXY"),
                    max_z=_dbl(row, "AABB_MAXZ"),
                ))
    logger.info("[DataLoader] 프로젝트 목록 %d건 로드", len(projects))
    return projects


def select_project(conninfo: str, project_id: int | None = None) -> ProjectInfo:
    """
    프로젝트 목록을 출력하고 사용자가 선택(또는 project_id 직접 지정)한
    ProjectInfo 를 반환한다.
    """
    projects = list_projects(conninfo)
    if not projects:
        raise RuntimeError("DB에 프로젝트(TB_SPACE_GROUP_INFO)가 없습니다.")

    if project_id is not None:
        for p in projects:
            if p.project_id == project_id:
                return p
        raise ValueError(f"project_id={project_id} 가 없습니다 (총 {len(projects)}개).")

    # 대화형 선택
    print("\n=== 프로젝트 목록 ===")
    for p in projects:
        print(f"  [{p.project_id:3d}] {p.display}")
    print()
    while True:
        try:
            choice = int(input(f"프로젝트 번호 입력 (1~{len(projects)}): ").strip())
            match = next((p for p in projects if p.project_id == choice), None)
            if match:
                logger.info("[DataLoader] 선택: %s", match)
                return match
        except (ValueError, EOFError):
            pass
        print(f"  → 올바른 번호를 입력하세요 (1~{len(projects)}).")


# ─────────────────────────────────────────────────────────────────────────────
# 2. 장애물 로드
# ─────────────────────────────────────────────────────────────────────────────

def load_obstacles(conninfo: str, proj: ProjectInfo) -> list[ObstacleAABB]:
    """
    TB_BIM_OBSTACLE 에서 그룹 AABB 교차 장애물을 로드한다.
    - damper 객체 제외 (경로 막힘 방지)
    - 그룹 박스로 클리핑
    - PassThrough 판별 포함
    C# ObstacleDbLoader.LoadScene() 블록 1 대응.
    """
    sp = _make_scope_params(proj)
    sql = f"""
        SELECT "AABB_MINX","AABB_MINY","AABB_MINZ",
               "AABB_MAXX","AABB_MAXY","AABB_MAXZ",
               "INSTANCE_NAME","OST_TYPE","DDWORKS_TYPE","COLLISION_PASS"
          FROM "TB_BIM_OBSTACLE"
         WHERE {_scope_filter_sql()}
    """
    obstacles: list[ObstacleAABB] = []
    minx, maxx = sp["minx"], sp["maxx"]
    miny, maxy = sp["miny"], sp["maxy"]
    minz, maxz = sp["minz"], sp["maxz"]

    with psycopg2.connect(conninfo) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, sp)
            for row in cur.fetchall():
                row = dict(row)
                name = _str(row, "INSTANCE_NAME")
                # damper 제외 (C# 동일)
                if "damper" in name.lower():
                    continue

                mnx = _dbl(row, "AABB_MINX"); mny = _dbl(row, "AABB_MINY"); mnz = _dbl(row, "AABB_MINZ")
                mxx = _dbl(row, "AABB_MAXX"); mxy = _dbl(row, "AABB_MAXY"); mxz = _dbl(row, "AABB_MAXZ")
                if mxx <= mnx or mxy <= mny or mxz <= mnz:
                    continue

                # 그룹 박스로 클리핑
                mnx = max(mnx, minx); mny = max(mny, miny); mnz = max(mnz, minz)
                mxx = min(mxx, maxx); mxy = min(mxy, maxy); mxz = min(mxz, maxz)
                if mxx <= mnx or mxy <= mny or mxz <= mnz:
                    continue

                cp_raw = row.get("COLLISION_PASS") or row.get("collision_pass")
                collision_pass: bool | None = None
                if cp_raw is not None:
                    try:
                        collision_pass = bool(int(cp_raw))
                    except (TypeError, ValueError):
                        pass

                obs = ObstacleAABB(
                    name=name,
                    ost_type=_str(row, "OST_TYPE"),
                    ddworks_type=_str(row, "DDWORKS_TYPE"),
                    collision_pass=collision_pass,
                    min_x=mnx, min_y=mny, min_z=mnz,
                    max_x=mxx, max_y=mxy, max_z=mxz,
                )
                obstacles.append(obs)

    real = [o for o in obstacles if not o.is_pass_through]
    logger.info(
        "[DataLoader] 장애물: 총=%d, 통과객체=%d, 실장애물=%d",
        len(obstacles), len(obstacles) - len(real), len(real),
    )
    return obstacles


# ─────────────────────────────────────────────────────────────────────────────
# 3. 장비 + 장비 PoC 로드
# ─────────────────────────────────────────────────────────────────────────────

def load_equipment(conninfo: str, proj: ProjectInfo) -> list[EquipmentInfo]:
    """
    TB_EQUIPMENTS 에서 장비 박스(AABB) 목록을 로드한다.
    C# ObstacleDbLoader.LoadScene() 블록 2 대응.
    """
    sp = _make_scope_params(proj)
    sql = f"""
        SELECT "INSTANCE_NAME","MAIN_SUB_TYPE",
               "AABB_MINX","AABB_MINY","AABB_MINZ",
               "AABB_MAXX","AABB_MAXY","AABB_MAXZ"
          FROM "TB_EQUIPMENTS"
         WHERE {_scope_filter_sql()}
    """
    equipment: list[EquipmentInfo] = []
    with psycopg2.connect(conninfo) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, sp)
            for row in cur.fetchall():
                row = dict(row)
                mnx = _dbl(row, "AABB_MINX"); mny = _dbl(row, "AABB_MINY"); mnz = _dbl(row, "AABB_MINZ")
                mxx = _dbl(row, "AABB_MAXX"); mxy = _dbl(row, "AABB_MAXY"); mxz = _dbl(row, "AABB_MAXZ")
                if mxx <= mnx or mxy <= mny or mxz <= mnz:
                    continue
                sub_type = _str(row, "MAIN_SUB_TYPE")
                equipment.append(EquipmentInfo(
                    name=_str(row, "INSTANCE_NAME"),
                    is_main=sub_type.lower() == "maintool",
                    min_x=mnx, min_y=mny, min_z=mnz,
                    max_x=mxx, max_y=mxy, max_z=mxz,
                ))
    logger.info("[DataLoader] 장비 %d건 로드", len(equipment))
    return equipment


def load_pocs(
    conninfo: str,
    proj: ProjectInfo,
    equipment: list[EquipmentInfo],
) -> tuple[list[PocPoint], list[PocPoint]]:
    """
    TB_POCINSTANCES 에서 PoC 목록을 로드하여
    (장비 PoC 목록, 덕트/레터럴 PoC 목록) 튜플로 반환한다.

    - 컬럼명이 DB 스키마마다 다를 수 있어 동적으로 탐색 (C# Pick() 패턴)
    - 소유 타입(OWNER_INSTANCE_TYPE)으로 Equipment / Duct / Lateral 분류
    C# LoadProjectPocs() 대응.
    """
    sp = _make_scope_params(proj)

    # ── 컬럼 동적 탐색 (C# ColumnSet + Pick 패턴) ──────────────────────────
    with psycopg2.connect(conninfo) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                 WHERE table_name = 'TB_POCINSTANCES'
            """)
            cols = {r[0].upper() for r in cur.fetchall()}

    def pick(*candidates: str) -> str | None:
        for c in candidates:
            if c.upper() in cols:
                return c
        return None

    cx = pick("POSX", "POS_X", "POSITION_X", "POINT_X", "X", "POC_POSX", "FROM_POSX")
    cy = pick("POSY", "POS_Y", "POSITION_Y", "POINT_Y", "Y", "POC_POSY", "FROM_POSY")
    cz = pick("POSZ", "POS_Z", "POSITION_Z", "POINT_Z", "Z", "POC_POSZ", "FROM_POSZ")
    if not cx or not cy or not cz:
        logger.warning("[DataLoader] TB_POCINSTANCES 좌표 컬럼 탐색 실패 → PoC 건너뜀")
        return [], []

    c_name   = pick("POC_NAME", "NAME", "INSTANCE_NAME", "TAG_NAME") or cx
    c_owner  = pick("OWNER_INSTANCE_NAME", "OWNER_NAME", "EQUIPMENT_NAME", "TARGET_OWNER_NAME") or cx
    c_owner_type = pick("OWNER_INSTANCE_TYPE", "OWNER_TYPE", "CATEGORY", "TYPE") or cx
    c_util   = pick("UTILITY", "SOURCE_UTILITY")

    def qcol(c: str | None, alias: str) -> str:
        return f'"{c}" AS {alias}' if c else f"NULL AS {alias}"

    sql = f"""
        SELECT "{cx}" AS px, "{cy}" AS py, "{cz}" AS pz,
               {qcol(c_name,       'poc_name')},
               {qcol(c_owner,      'owner_name')},
               {qcol(c_owner_type, 'owner_type')},
               {qcol(c_util,       'utility')}
          FROM "TB_POCINSTANCES"
         WHERE "{cx}" BETWEEN %(minx)s AND %(maxx)s
           AND "{cy}" BETWEEN %(miny)s AND %(maxy)s
    """
    equip_pocs: list[PocPoint] = []
    duct_pocs: list[PocPoint] = []

    # 장비 AABB 조회용 인덱스
    def nearest_equipment_name(x: float, y: float, z: float) -> str:
        best, bd = "", float("inf")
        for e in equipment:
            dx = max(e.min_x - x, 0, x - e.max_x)
            dy = max(e.min_y - y, 0, y - e.max_y)
            dz = max(e.min_z - z, 0, z - e.max_z)
            d2 = dx*dx + dy*dy + dz*dz
            if d2 < bd:
                bd = d2; best = e.name
        return best

    with psycopg2.connect(conninfo) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, sp)
            for row in cur.fetchall():
                row = dict(row)
                x = float(row.get("px") or 0)
                y = float(row.get("py") or 0)
                z = float(row.get("pz") or 0)
                if x < sp["minx"] or x > sp["maxx"] or y < sp["miny"] or y > sp["maxy"]:
                    continue

                poc_name   = str(row.get("poc_name") or "")
                owner_name = str(row.get("owner_name") or "")
                owner_type = str(row.get("owner_type") or "")
                utility    = row.get("utility")

                kind = _classify_owner_type(owner_type, owner_name)

                # 소유자 이름이 없으면 가장 가까운 장비로 폴백
                if not owner_name and kind == "Equipment":
                    owner_name = nearest_equipment_name(x, y, z)

                poc = PocPoint(
                    name=poc_name if poc_name else owner_name,
                    owner_name=owner_name,
                    owner_type=kind,
                    utility=str(utility) if utility else None,
                    x=x, y=y, z=z,
                )

                if kind == "Equipment":
                    equip_pocs.append(poc)
                else:
                    duct_pocs.append(poc)

    logger.info(
        "[DataLoader] PoC 로드: 장비PoC=%d, 덕트/레터럴PoC=%d",
        len(equip_pocs), len(duct_pocs),
    )
    return equip_pocs, duct_pocs


# ─────────────────────────────────────────────────────────────────────────────
# 4. 덕트/레터럴 PoC (종단점) - 보조 로더
# ─────────────────────────────────────────────────────────────────────────────

def load_duct_lateral_pocs_from_route(
    conninfo: str,
    proj: ProjectInfo,
) -> list[PocPoint]:
    """
    TB_ROUTE_PATH 의 TARGET_POS(덕트/레터럴 종단 좌표)를 PoC 목록으로 반환.
    TB_POCINSTANCES 에 종단 PoC 가 없는 경우의 보조 수단.
    C# TaskInfo 의 Gx/Gy/Gz(= TARGET_POSX/Y/Z) 활용.
    """
    sp = _make_scope_params(proj)
    sql = """
        SELECT "TARGET_POSX","TARGET_POSY","TARGET_POSZ",
               "TARGET_OWNER_NAME","UTILITY_GROUP","SOURCE_UTILITY"
          FROM "TB_ROUTE_PATH"
         WHERE "SOURCE_POSX" BETWEEN %(minx)s AND %(maxx)s
           AND "SOURCE_POSY" BETWEEN %(miny)s AND %(maxy)s
           AND "TARGET_POSX" IS NOT NULL
    """
    pocs: list[PocPoint] = []
    seen: set[tuple] = set()
    try:
        with psycopg2.connect(conninfo) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, sp)
                for row in cur.fetchall():
                    row = dict(row)
                    tx = row.get("TARGET_POSX") or row.get("target_posx")
                    ty = row.get("TARGET_POSY") or row.get("target_posy")
                    tz = row.get("TARGET_POSZ") or row.get("target_posz")
                    if tx is None or ty is None or tz is None:
                        continue
                    x, y, z = float(tx), float(ty), float(tz)
                    owner = str(row.get("TARGET_OWNER_NAME") or row.get("target_owner_name") or "")
                    util  = str(row.get("SOURCE_UTILITY") or row.get("source_utility") or "")
                    key = (round(x, 0), round(y, 0), round(z, 0))
                    if key in seen:
                        continue
                    seen.add(key)
                    pocs.append(PocPoint(
                        name=owner,
                        owner_name=owner,
                        owner_type="Duct",
                        utility=util or None,
                        x=x, y=y, z=z,
                    ))
    except Exception as exc:
        logger.warning("[DataLoader] TARGET_POS 로드 실패: %s", exc)
    logger.info("[DataLoader] 덕트 종단PoC(route 기반) %d건", len(pocs))
    return pocs


# ─────────────────────────────────────────────────────────────────────────────
# 5. 라우팅 작업(Task) 로드
# ─────────────────────────────────────────────────────────────────────────────

def load_routing_tasks(conninfo: str, proj: ProjectInfo) -> list[RoutingTask]:
    """
    TB_ROUTE_PATH 에서 SOURCE_POS→TARGET_POS 작업 목록을 로드한다.
    SOURCE_POS 가 그룹 AABB 안에 있는 경로만 포함.
    C# LoadRoutesAndTasks() / TaskInfo 대응.
    """
    sp = _make_scope_params(proj)
    sql = """
        SELECT "ROUTE_PATH_GUID","UTILITY_GROUP","SOURCE_UTILITY","SOURCE_SIZE",
               "EQUIPMENT_NAME","TARGET_OWNER_NAME",
               "SOURCE_POSX","SOURCE_POSY","SOURCE_POSZ",
               "TARGET_POSX","TARGET_POSY","TARGET_POSZ"
          FROM "TB_ROUTE_PATH"
         WHERE "SOURCE_POSX" BETWEEN %(minx)s AND %(maxx)s
           AND "SOURCE_POSY" BETWEEN %(miny)s AND %(maxy)s
           AND "TARGET_POSX" IS NOT NULL
           AND "TARGET_POSY" IS NOT NULL
         ORDER BY "UTILITY_GROUP","SOURCE_UTILITY","ROUTE_PATH_GUID"
    """
    tasks: list[RoutingTask] = []
    try:
        with psycopg2.connect(conninfo) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, sp)
                for row in cur.fetchall():
                    row = dict(row)
                    sx = row.get("SOURCE_POSX") or row.get("source_posx")
                    sy = row.get("SOURCE_POSY") or row.get("source_posy")
                    sz = row.get("SOURCE_POSZ") or row.get("source_posz")
                    tx = row.get("TARGET_POSX") or row.get("target_posx")
                    ty = row.get("TARGET_POSY") or row.get("target_posy")
                    tz = row.get("TARGET_POSZ") or row.get("target_posz")
                    if None in (sx, sy, sz, tx, ty, tz):
                        continue
                    tasks.append(RoutingTask(
                        route_path_guid=str(row.get("ROUTE_PATH_GUID") or row.get("route_path_guid") or ""),
                        utility=str(row.get("SOURCE_UTILITY") or row.get("source_utility") or "") or None,
                        utility_group=str(row.get("UTILITY_GROUP") or row.get("utility_group") or "") or None,
                        diameter_mm=_parse_pipe_size_mm(
                            str(row.get("SOURCE_SIZE") or row.get("source_size") or "")
                        ),
                        start_x=float(sx), start_y=float(sy), start_z=float(sz),
                        end_x=float(tx), end_y=float(ty), end_z=float(tz),
                        source_name=str(row.get("EQUIPMENT_NAME") or row.get("equipment_name") or "") or None,
                        target_name=str(row.get("TARGET_OWNER_NAME") or row.get("target_owner_name") or "") or None,
                    ))
    except Exception as exc:
        logger.warning("[DataLoader] 라우팅 작업 로드 실패: %s", exc)
    logger.info("[DataLoader] 라우팅 작업 %d건 로드", len(tasks))
    return tasks


# ─────────────────────────────────────────────────────────────────────────────
# 6. 통합 씬 로더
# ─────────────────────────────────────────────────────────────────────────────

def load_scene(
    conninfo: str,
    proj: ProjectInfo,
) -> RoutingScene:
    """
    선택된 프로젝트의 장애물 + 장비PoC + 덕트/레터럴PoC + 라우팅작업을
    한 번에 로드하여 RoutingScene 으로 반환한다.
    C# ObstacleDbLoader.LoadScene() 전체 대응.
    """
    scene = RoutingScene(project=proj)

    logger.info("[DataLoader] 씬 로드 시작: %s", proj.display)

    # 1) 장애물
    scene.obstacles = load_obstacles(conninfo, proj)

    # 2) 장비
    scene.equipment = load_equipment(conninfo, proj)

    # 3) PoC (장비 + 덕트/레터럴)
    equip_pocs, duct_pocs = load_pocs(conninfo, proj, scene.equipment)
    scene.equipment_pocs     = equip_pocs
    scene.duct_lateral_pocs  = duct_pocs

    # 4) 덕트 종단 PoC 보충 (TB_POCINSTANCES 에 없는 경우)
    if not duct_pocs:
        scene.duct_lateral_pocs = load_duct_lateral_pocs_from_route(conninfo, proj)

    # 5) 라우팅 작업 (기존 설계 Source→Target)
    scene.tasks = load_routing_tasks(conninfo, proj)

    logger.info("[DataLoader] 씬 로드 완료 - %s", scene.summary())
    return scene


# ─────────────────────────────────────────────────────────────────────────────
# RubberBandRouter 호환 변환
# ─────────────────────────────────────────────────────────────────────────────

def scene_to_obstacle_map(scene: RoutingScene):
    """
    RoutingScene 의 장애물 목록을 RubberBandRouter core.obstacle_map.ObstacleMap 으로 변환.
    PassThrough 객체는 자동 제외.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    from core.obstacle_map import OBBObstacle, ObstacleMap

    obs_map = ObstacleMap(project_id=scene.project.group_id)

    for aabb in scene.obstacles:
        if aabb.is_pass_through:
            continue  # 통과 객체 제외
        if aabb.volume < 1.0:
            continue  # 퇴화 박스 제외

        # AABB → OBB (축-정렬, axes=단위행렬)
        cx = (aabb.min_x + aabb.max_x) / 2.0
        cy = (aabb.min_y + aabb.max_y) / 2.0
        cz = (aabb.min_z + aabb.max_z) / 2.0
        hx = (aabb.max_x - aabb.min_x) / 2.0
        hy = (aabb.max_y - aabb.min_y) / 2.0
        hz = (aabb.max_z - aabb.min_z) / 2.0

        # 8 꼭짓점 생성
        verts = np.array([
            [cx+dx, cy+dy, cz+dz]
            for dx in [-hx, hx]
            for dy in [-hy, hy]
            for dz in [-hz, hz]
        ])
        obs = OBBObstacle(
            name=aabb.name,
            source_table="TB_BIM_OBSTACLE",
            project_id=scene.project.group_id,
            vertices=verts,
            center=np.array([cx, cy, cz]),
            half_extents=np.array([hx, hy, hz]),
            axes=np.eye(3),
            volume=aabb.volume,
            is_penetration=False,
            obj_type=aabb.ost_type,
        )
        obs_map.obstacles.append(obs)

    obs_map.build_density_tensor()
    logger.info(
        "[DataLoader] ObstacleMap 변환 완료: 장애물=%d, 텐서=%s",
        len(obs_map.obstacles), obs_map.density_tensor.shape,
    )
    return obs_map


# ─────────────────────────────────────────────────────────────────────────────
# CLI 진입점 (독립 실행 테스트)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import config as cfg

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    conninfo = cfg.get_conninfo()
    proj = select_project(conninfo)
    scene = load_scene(conninfo, proj)

    print("\n=== 씬 요약 ===")
    print(scene.summary())

    if scene.tasks:
        print(f"\n=== 라우팅 작업 샘플 (최대 5건) ===")
        for t in scene.tasks[:5]:
            print(f"  {t}")

    if scene.equipment_pocs:
        print(f"\n=== 장비 PoC 샘플 (최대 5건) ===")
        for p in scene.equipment_pocs[:5]:
            print(f"  {p}")

    if scene.duct_lateral_pocs:
        print(f"\n=== 덕트/레터럴 PoC 샘플 (최대 5건) ===")
        for p in scene.duct_lateral_pocs[:5]:
            print(f"  {p}")
