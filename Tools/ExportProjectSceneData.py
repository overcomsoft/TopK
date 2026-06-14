from __future__ import annotations
import os
import sys
import json
import math
import argparse
from pathlib import Path
import psycopg2
import psycopg2.extras

"""
================================================================================
[실행 명령어 예시 (Execution Commands)]
================================================================================
1. 전체 프로젝트 내보내기 (기본값 설정):
   python Tools/ExportProjectSceneData.py

2. 특정 프로젝트(그룹명)만 지정하여 내보내기:
   python Tools/ExportProjectSceneData.py --project WTNHJ02

3. 데이터베이스 연결 정보 직접 지정 및 특정 출력 디렉토리와 그리드 복셀 크기 지정:
   python Tools/ExportProjectSceneData.py --host localhost --port 5432 --db DDW_AI_DB --user postgres --password dinno --outdir ./data/custom_output --cell-mm 50.0

4. config 파일(tools.settings.json) 위치를 명시하여 실행:
   python Tools/ExportProjectSceneData.py --config Tools/tools.settings.json
================================================================================

[전체 프로세스 및 개요 (Overall Process)]
1. 본 스크립트는 PostgreSQL 데이터베이스("DDW_AI_DB")로부터 배관 설계에 필요한 
   BIM 공간 데이터, 장애물, 덕트, 레터럴, 장비 정보 및 기존 배관/태스크 데이터를 
   공간 경계 기반으로 추출하여, C# 'GroupPatternViewer' 모델 스키마('SceneData')에 부합하는 
   JSON 파일로 출력합니다. (1개 프로젝트당 1개의 JSON 파일 생성)
   
2. 상세 처리 흐름:
   - 데이터베이스 연결: 'tools.settings.json' 파일 또는 명령행(CLI) 인자를 받아 DB 연결.
   - 프로젝트 메타정보 로드: 'TB_SPACE_GROUP_INFO' 테이블에서 프로젝트 목록 및 각 영역의 AABB(바운딩 박스)를 조회.
   - 경계 상자 여유값(Scope Margin) 확장: 각 프로젝트별 기본 AABB 사방으로 500.0mm를 더해 쿼리 범위 설정.
   - 구성 요소 쿼리 및 데이터 구조화:
     (1) 'TB_BIM_OBSTACLE' 테이블로부터 해당 공간 범위 내의 장애물 정보 조회 및 필터링.
     (2) 'TB_EQUIPMENTS' 테이블로부터 공간 범위 내의 장비 배치(메인/서브 구분) 조회.
     (3) 'TB_LATERAL_PIPE' 및 'TB_DUCT' 테이블로부터 덕트/레터럴 배치 정보 조회.
     (4) 'TB_SPACE_INFO' 테이블로부터 공간 영역(층/구역 등)의 정보를 툴 경계 상자에 맞춰 트리밍하여 조회.
     (5) 'TB_ROUTE_PATH', 'TB_ROUTE_SEGMENTS', 'TB_ROUTE_SEGMENT_DETAIL' 테이블을 조인하여 기존 설계된 배관 경로 및 시점/종점(PoC) 등의 작업 정보(Tasks) 복원.
     (6) 동일한 공간 범위 내 부속 자재 피팅(Fittings) 목록 산출.
   - 격자 정보(GridMeta) 생성: 내보낼 배관의 시점/종점 및 사방 범위를 기준으로 그리드의 원점(Origin) 및 그리드 셀의 개수(Nx, Ny, Nz) 연산.
   - JSON 파일 출력: 최종 구성된 SceneData 객체를 프로젝트 명칭('{그룹명}.json')으로 저장.

[핵심 알고리즘 (Core Algorithm)]
- 인치 단위를 mm 단위로의 파싱 알고리즘: B단위 인치 문자열(분수 포함)을 mm 단위 배관 외경 직경으로 완벽 변환.
- AABB 공간 교차 필터링: 프로젝트 바운딩 박스를 기준으로 데이터베이스로부터 교차하는 객체만 선택.
- 배관 불연속점 분리: 동일한 GUID를 가진 배관 세그먼트 중 이전 세그먼트의 끝점과 다음 세그먼트의 시작점 사이 거리가 10mm(거리 제곱 100)를 초과할 경우, 이를 분리된 배관 인스턴스로 감지.
- 배관 경계면 트리밍(Trim to Boundary): 수집된 원시 폴리라인 경로에서 실제 설계 영역의 시작(source_pos)과 끝(target_pos)에 가장 가까운 구간만 남기고 잘라냄.
- 복셀 그리드 계산: 전체 객체 및 배관 경로의 z좌표 최저/최고 높이에 500mm 마진을 부여하고, 지정된 cell_mm 크기로 나누어 그리드 행렬 수(Nx, Ny, Nz)와 가상 원점(Ox, Oy, Oz)을 도출.
"""

def parse_pipe_size_mm(size_str: str | None) -> float:
    """
    배관 규격 문자열(예: '40A', '2B', '1-1/2B', '1/2B' 등)을 읽어 mm 단위의 수치로 변환합니다.
    - size_str: 파싱할 배관 사이즈 규격 문자열
    - 반환값: mm 단위로 변환된 배관 사이즈 수치 (실패 시 0.0)
    """
    if not size_str or not size_str.strip():
        return 0.0
    # 'X' 또는 'x' 문자가 포함된 규격 분할 (예: 40A X 10t 등의 형태 고려)
    tok = size_str.strip().split('X')[0].split('x')[0].strip()
    if len(tok) < 2:
        return 0.0
    unit = tok[-1].upper()  # 단위 문자 ('A' 또는 'B')
    num = tok[:-1].strip()  # 수치 문자열 (예: '40', '1-1/2')
    if unit == 'A':
        # A타입 배관은 해당 숫자 수치를 그대로 mm로 간주
        try:
            return float(num)
        except ValueError:
            return 0.0
    elif unit == 'B':
        # B타입 배관은 인치(Inch) 단위 규격이므로 mm로 환산 (1인치 = 25.4mm)
        inch = parse_inch(num)
        return inch * 25.4 if inch > 0 else 0.0
    return 0.0

def parse_inch(s: str) -> float:
    """
    인치 형식 분수 및 정수 문자열을 실수(float) 값으로 파싱합니다. (예: '1-1/2', '1 1/2' -> 1.5, '1/2' -> 0.5)
    - s: 인치 규격 숫자 문자열
    - 반환값: 변환된 인치 실수 값
    """
    s = s.strip().replace('-', ' ') # 하이픈(-) 문자를 공백으로 치환
    if '/' in s:
        parts = s.split(' ')
        whole = 0.0
        frac = s
        if len(parts) == 2:
            # 대분수 형태인 경우 (예: '1 1/2') 정수부와 분수부 분리
            try:
                whole = float(parts[0])
            except ValueError:
                pass
            frac = parts[1]
        fp = frac.split('/')
        if len(fp) == 2:
            # 분수부 계산 (분자 / 분모)
            try:
                a = float(fp[0])
                b = float(fp[1])
                if b != 0:
                    return whole + a / b
            except ValueError:
                pass
            return whole
    else:
        # 분수가 아닌 일반 정수/실수 형태인 경우
        try:
            return float(s)
        except ValueError:
            return 0.0
    return 0.0

def dist2(a, b):
    """
    두 3D 좌표 점 사이의 거리 제곱을 계산합니다. (제곱근 연산을 생략하여 속도 최적화)
    - a, b: {'X': x, 'Y': y, 'Z': z} 형태의 좌표 딕셔너리
    - 반환값: 거리의 제곱 (double)
    """
    dx = a["X"] - b["X"]
    dy = a["Y"] - b["Y"]
    dz = a["Z"] - b["Z"]
    return dx * dx + dy * dy + dz * dz

def trim_to_boundary(path, start_pos, end_pos):
    """
    수집된 배관 정점(Point) 리스트에서 실제 작업 시작 시점(start_pos)과 끝 종점(end_pos) 
    내부 경계에 포함되는 세그먼트만 남기고 잘라냅니다.
    - path: [{'X': x, 'Y': y, 'Z': z}, ...] 형태의 폴리라인 좌표 리스트
    - start_pos: 배관의 시작 위치 좌표 (source_pos)
    - end_pos: 배관의 끝 위치 좌표 (target_pos)
    - 반환값: 잘려진 후의 좌표 정점 리스트
    """
    if len(path) < 2:
        return path
    si = 0                 # 시작점이 위치할 인덱스 후보
    ei = len(path) - 1     # 끝점이 위치할 인덱스 후보
    sb = float('inf')      # 시작점과의 최소 거리의 제곱 캐시
    eb = float('inf')      # 끝점과의 최소 거리의 제곱 캐시
    
    # 각 정점들을 순회하면서 시작/끝 PoC 좌표에 가장 가까운 정점 인덱스를 찾음
    for i, p in enumerate(path):
        ds = dist2(p, start_pos)
        de = dist2(p, end_pos)
        if ds < sb:
            sb = ds
            si = i
        if de < eb:
            eb = de
            ei = i
    # 인덱스 방향이 뒤집힌 경우를 고려해 보정
    if si > ei:
        si, ei = ei, si
    return path[si : ei + 1]

def get_is_pass_through(pass_through_override, ost_type, ddworks_type):
    """
    특정 장애물의 관통 가능 여부(IsPassThrough)를 판별합니다.
    - pass_through_override: DB(COLLISION_PASS)에 정의된 관통 재정의값 (bool 또는 None)
    - ost_type: 장애물의 OST_TYPE 분류 문자열
    - ddworks_type: 장애물의 DDWORKS_TYPE 분류 문자열
    - 반환값: 관통 가능하면 True, 불가능하면 False
    """
    if pass_through_override is not None:
        return pass_through_override
    
    ost = (ost_type or "").strip().lower()
    ddworks = (ddworks_type or "").strip().lower()
    
    # 바닥(Floor)이나 천장(Ceiling)은 기본 관통 허용
    if ost in ["ost_floors", "ost_ceilings"]:
        return True
    # 빔 구조물(Beam Structure)인 경우 관통 허용
    if ost == "ost_structuralframing" and ddworks == "beam_structure":
        return True
    return False

def load_settings(config_path_opt=None):
    """
    데이터베이스 접속 설정이 보관된 tools.settings.json 파일 경로를 탐색하여 로드합니다.
    - config_path_opt: 명령행 인자로 직접 지정한 설정 파일 경로
    - 반환값: 파싱된 설정 정보 딕셔너리 (실패 시 None)
    """
    paths_to_try = []
    if config_path_opt:
        paths_to_try.append(Path(config_path_opt))
    paths_to_try.extend([
        Path("Tools/tools.settings.json"),
        Path("tools.settings.json"),
        Path(__file__).parent / "tools.settings.json",
        Path(__file__).parent.parent / "Tools" / "tools.settings.json"
    ])
    
    for p in paths_to_try:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "db" in data:
                        return data
            except Exception as e:
                print(f"[Warn] Failed to load config from {p}: {e}", file=sys.stderr)
    return None

def main():
    """
    메인 오케스트레이션 실행 제어부. 데이터베이스 접속, 공간 필터링 처리, JSON 출력 진행.
    """
    # 표준 출력/에러 스트림 인코딩을 UTF-8로 지정하여 한글 깨짐 방지
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding='utf-8')

    # 명령행 인자 파서 초기화
    parser = argparse.ArgumentParser(description="Export project scene data to JSON format for GroupPatternViewer.")
    parser.add_argument("--config", default=None, help="Path to tools.settings.json")
    parser.add_argument("--host", default=None, help="Database host")
    parser.add_argument("--port", default=None, help="Database port")
    parser.add_argument("--db", default=None, help="Database name")
    parser.add_argument("--dbname", default=None, help="Database name (alias)")
    parser.add_argument("--user", default=None, help="Database user")
    parser.add_argument("--password", default=None, help="Database password")
    parser.add_argument("--outdir", default=None, help="Output directory path")
    parser.add_argument("--cell-mm", type=float, default=25.0, help="Grid cell size in mm")
    parser.add_argument("--project", default=None, help="Export a specific project GroupName (exports all if not specified)")

    args = parser.parse_args()

    # 1. 설정 파일(json) 우선 로드
    settings = load_settings(args.config)
    
    # 2. DB 파라미터 확인 (CLI 인자가 설정 파일값보다 높은 우선순위를 가짐)
    db_settings = settings.get("db", {}) if settings else {}
    host = args.host or db_settings.get("host") or "localhost"
    port = args.port or db_settings.get("port") or 5432
    db = args.db or args.dbname or db_settings.get("database") or "DDW_AI_DB"
    user = args.user or db_settings.get("user") or "postgres"
    password = args.password or db_settings.get("password") or "dinno"
    
    # 3. 출력 경로 확인
    settings_out_dir = settings.get("outDir") if settings else None
    if args.outdir:
        out_dir = Path(args.outdir)
    elif settings_out_dir:
        out_dir = Path(settings_out_dir) / "SceneData"
    else:
        out_dir = Path("data/output/SceneData")
        
    out_dir.mkdir(parents=True, exist_ok=True)

    # psycopg2 DB 커넥션 생성
    conn_str = f"host={host} port={port} dbname={db} user={user} password={password}"
    print(f"Connecting to database (Host: {host}, Port: {port}, DB: {db}, User: {user})...")
    
    try:
        conn = psycopg2.connect(conn_str)
        conn.set_client_encoding("UTF8")
    except Exception as e:
        print(f"Failed to connect to database: {e}", file=sys.stderr)
        return 1

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # 1. 프로젝트 그룹(TB_SPACE_GROUP_INFO) 목록 정보 전체 로드
        cur.execute('''
            SELECT "TAG_GROUP_ID", "TAG_GROUP_NM", "BAY_GROUP_NM", "PROCESS_GROUP_NM",
                   "AABB_MINX", "AABB_MINY", "AABB_MINZ", "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"
            FROM "TB_SPACE_GROUP_INFO"
            ORDER BY "PROCESS_GROUP_NM", "TAG_GROUP_NM"
        ''')
        projects = cur.fetchall()
        
        if not projects:
            print("No projects found in TB_SPACE_GROUP_INFO.")
            return 0
            
        print(f"Found {len(projects)} projects.")
        
        processed_count = 0
        
        # 각 프로젝트를 순회하며 데이터 쿼리 후 개별 JSON 생성
        for proj in projects:
            group_id = proj["TAG_GROUP_ID"]
            group_name = proj["TAG_GROUP_NM"] # 그룹 이름 (예: WTNHJ02)
            
            # 특정 단일 프로젝트 파라미터가 유입된 경우 해당 그룹만 필터 처리
            if args.project and group_name != args.project:
                continue
                
            bay = proj["BAY_GROUP_NM"]
            process = proj["PROCESS_GROUP_NM"]
            
            # 툴 기본 공간 경계 영역 좌표
            proj_min_x = float(proj["AABB_MINX"])
            proj_min_y = float(proj["AABB_MINY"])
            proj_min_z = float(proj["AABB_MINZ"])
            proj_max_x = float(proj["AABB_MAXX"])
            proj_max_y = float(proj["AABB_MAXY"])
            proj_max_z = float(proj["AABB_MAXZ"])
            
            print(f"\nProcessing project: {group_name} (Bay: {bay}, Process: {process})")
            print(f"  Project Bounds: ({proj_min_x:.1f}, {proj_min_y:.1f}, {proj_min_z:.1f}) ~ ({proj_max_x:.1f}, {proj_max_y:.1f}, {proj_max_z:.1f})")
            
            # 사방 공간 교차 쿼리 영역 산출 (여유 마진인 500mm를 감안하여 사방으로 바운딩 박스를 확장)
            scope_margin = 500.0
            minx = proj_min_x - scope_margin
            maxx = proj_max_x + scope_margin
            miny = proj_min_y - scope_margin
            maxy = proj_max_y + scope_margin
            minz = proj_min_z - scope_margin
            maxz = proj_max_z + scope_margin
            
            # ==================================================================
            # 1) 장애물 데이터 쿼리 (TB_BIM_OBSTACLE)
            # ==================================================================
            print("  Querying obstacles...")
            cur.execute('''
                SELECT "AABB_MINX", "AABB_MINY", "AABB_MINZ", "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ",
                       "INSTANCE_NAME", "OST_TYPE", "DDWORKS_TYPE", "COLLISION_PASS"
                FROM "TB_BIM_OBSTACLE"
                WHERE "AABB_MINX" <= %s AND "AABB_MAXX" >= %s AND "AABB_MINY" <= %s AND "AABB_MAXY" >= %s
            ''', (maxx, minx, maxy, miny))
            
            obstacles = []
            for row in cur.fetchall():
                mnx, mny, mnz = float(row[0]), float(row[1]), float(row[2])
                mxx, mxy, mxz = float(row[3]), float(row[4]), float(row[5])
                if mxx <= mnx or mxy <= mny or mxz <= mnz:
                    continue
                name = row[6] or ""
                # 'damper'가 포함된 인스턴스 명칭은 제외 처리 (C# ObstacleDbLoader 로직 반영)
                if "damper" in name.lower():
                    continue
                # 좌표를 확장된 쿼리 영역 범위 내부로 강제 클램핑(Clamp)
                mnx = max(mnx, minx)
                mny = max(mny, miny)
                mnz = max(mnz, minz)
                mxx = min(mxx, maxx)
                mxy = min(mxy, maxy)
                mxz = min(mxz, maxz)
                if mxx <= mnx or mxy <= mny or mxz <= mnz:
                    continue
                    
                pass_through_override = bool(row[9]) if row[9] is not None else None
                ost_type = row[7] or ""
                ddworks_type = row[8] or ""
                
                obstacles.append({
                    "Name": name,
                    "DdworksType": ddworks_type,
                    "OstType": ost_type,
                    "MinX": mnx, "MinY": mny, "MinZ": mnz,
                    "MaxX": mxx, "MaxY": mxy, "MaxZ": mxz,
                    "PassThroughOverride": pass_through_override,
                    "IsPassThrough": get_is_pass_through(pass_through_override, ost_type, ddworks_type)
                })
                
            print(f"    Loaded {len(obstacles)} obstacles.")

            # ==================================================================
            # 2) 장비 데이터 쿼리 (TB_EQUIPMENTS)
            # ==================================================================
            print("  Querying equipment...")
            cur.execute('''
                SELECT "INSTANCE_NAME", "MAIN_SUB_TYPE",
                       "AABB_MINX", "AABB_MINY", "AABB_MINZ", "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"
                FROM "TB_EQUIPMENTS"
                WHERE "AABB_MINX" <= %s AND "AABB_MAXX" >= %s AND "AABB_MINY" <= %s AND "AABB_MAXY" >= %s
            ''', (maxx, minx, maxy, miny))
            
            equipment = []
            for row in cur.fetchall():
                mnx, mny, mnz = float(row[2]), float(row[3]), float(row[4])
                mxx, mxy, mxz = float(row[5]), float(row[6]), float(row[7])
                if mxx <= mnx or mxy <= mny or mxz <= mnz:
                    continue
                name = row[0] or ""
                # 메인 툴 여부 판별 (MainTool 문자열 체크)
                is_main = bool(row[1] and row[1].strip().lower() == "maintool")
                equipment.append({
                    "Name": name,
                    "IsMain": is_main,
                    "MinX": mnx, "MinY": mny, "MinZ": mnz,
                    "MaxX": mxx, "MaxY": mxy, "MaxZ": mxz
                })
                
            print(f"    Loaded {len(equipment)} equipment.")

            # ==================================================================
            # 3) 덕트 및 레터럴 데이터 쿼리 (TB_LATERAL_PIPE, TB_DUCT)
            # ==================================================================
            print("  Querying ducts and laterals...")
            ducts_laterals = []
            
            # TB_LATERAL_PIPE (카테고리: LATERAL)
            cur.execute('''
                SELECT "INSTANCE_NAME", "UTILITY",
                       "AABB_MINX", "AABB_MINY", "AABB_MINZ", "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"
                FROM "TB_LATERAL_PIPE"
                WHERE "AABB_MINX" <= %s AND "AABB_MAXX" >= %s AND "AABB_MINY" <= %s AND "AABB_MAXY" >= %s
            ''', (maxx, minx, maxy, miny))
            for row in cur.fetchall():
                mnx, mny, mnz = float(row[2]), float(row[3]), float(row[4])
                mxx, mxy, mxz = float(row[5]), float(row[6]), float(row[7])
                if mxx <= mnx or mxy <= mny or mxz <= mnz:
                    continue
                ducts_laterals.append({
                    "Name": row[0] or "",
                    "Category": "LATERAL",
                    "Utility": row[1],
                    "MinX": mnx, "MinY": mny, "MinZ": mnz,
                    "MaxX": mxx, "MaxY": mxy, "MaxZ": mxz,
                    "IsLateral": True
                })
                
            # TB_DUCT (카테고리: DUCT)
            cur.execute('''
                SELECT "INSTANCE_NAME", "UTILITY",
                       "AABB_MINX", "AABB_MINY", "AABB_MINZ", "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"
                FROM "TB_DUCT"
                WHERE "AABB_MINX" <= %s AND "AABB_MAXX" >= %s AND "AABB_MINY" <= %s AND "AABB_MAXY" >= %s
            ''', (maxx, minx, maxy, miny))
            for row in cur.fetchall():
                mnx, mny, mnz = float(row[2]), float(row[3]), float(row[4])
                mxx, mxy, mxz = float(row[5]), float(row[6]), float(row[7])
                if mxx <= mnx or mxy <= mny or mxz <= mnz:
                    continue
                ducts_laterals.append({
                    "Name": row[0] or "",
                    "Category": "DUCT",
                    "Utility": row[1],
                    "MinX": mnx, "MinY": mny, "MinZ": mnz,
                    "MaxX": mxx, "MaxY": mxy, "MaxZ": mxz,
                    "IsLateral": False
                })
                
            print(f"    Loaded {len(ducts_laterals)} ducts and laterals.")

            # ==================================================================
            # 4) 공간 데이터 쿼리 (TB_SPACE_INFO)
            # ==================================================================
            print("  Querying spaces...")
            cur.execute('''
                SELECT "SPACE_NAME", "AABB_MINX", "AABB_MINY", "AABB_MINZ", "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"
                FROM "TB_SPACE_INFO"
                WHERE "AABB_MINX" <= %s AND "AABB_MAXX" >= %s AND "AABB_MINY" <= %s AND "AABB_MAXY" >= %s
                ORDER BY "AABB_MINZ"
            ''', (maxx, minx, maxy, miny))
            
            spaces = []
            for row in cur.fetchall():
                mnx, mny, mnz = float(row[1]), float(row[2]), float(row[3])
                mxx, mxy, mxz = float(row[4]), float(row[5]), float(row[6])
                
                # 원본 툴 경계 상자(AABB_MIN/MAX) 내부로 제약(Constraint)
                smnx = max(mnx, proj_min_x)
                smny = max(mny, proj_min_y)
                smnz = max(mnz, proj_min_z)
                smxx = min(mxx, proj_max_x)
                smxy = min(mxy, proj_max_y)
                smxz = min(mxz, proj_max_z)
                
                if smxx <= smnx or smxy <= smny or smxz <= smnz:
                    continue
                    
                spaces.append({
                    "Name": row[0] or "",
                    "MinX": smnx, "MinY": smny, "MinZ": smnz,
                    "MaxX": smxx, "MaxY": smxy, "MaxZ": smxz
                })
                
            print(f"    Loaded {len(spaces)} spaces.")

            # ==================================================================
            # 5) 라우팅 태스크 및 기존 배관 데이터 쿼리 및 재건
            #    (TB_ROUTE_PATH + TB_ROUTE_SEGMENTS + TB_ROUTE_SEGMENT_DETAIL)
            # ==================================================================
            print("  Querying existing routes and tasks...")
            cur.execute('''
                SELECT s."ROUTE_PATH_GUID", rp."UTILITY_GROUP", rp."SOURCE_UTILITY", rp."SOURCE_SIZE",
                       rp."EQUIPMENT_NAME", rp."TARGET_OWNER_NAME",
                       sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",
                       sd."TO_POSX",   sd."TO_POSY",   sd."TO_POSZ",
                       rp."SOURCE_POSX", rp."SOURCE_POSY", rp."SOURCE_POSZ",
                       rp."TARGET_POSX", rp."TARGET_POSY", rp."TARGET_POSZ",
                       sd."TYPE", rp."EQUIPMENT_TAG"
                  FROM "TB_ROUTE_SEGMENT_DETAIL" sd
                  JOIN "TB_ROUTE_SEGMENTS" s ON s."SEGMENT_GUID" = sd."SEGMENT_GUID"
                  JOIN "TB_ROUTE_PATH" rp    ON rp."ROUTE_PATH_GUID" = s."ROUTE_PATH_GUID"
                 WHERE rp."SOURCE_POSX" BETWEEN %s AND %s
                   AND rp."SOURCE_POSY" BETWEEN %s AND %s
                 ORDER BY s."ROUTE_PATH_GUID", s."ORDER", sd."ORDER"
            ''', (minx, maxx, miny, maxy))
            
            existing_pipes = []
            tasks = []
            
            cur_guid = None     # 현재 처리 중인 배관 GUID 캐시
            cur_pipe = None     # 현재 빌드 중인 기존 설계 배관 데이터 딕셔너리
            cur_start = None    # 현재 배관의 원천 PoC 좌표 (Source Pos)
            cur_end = None      # 현재 배관의 종단 PoC 좌표 (Target Pos)
            last_to = None      # 직전 세그먼트의 끝 좌표 (불연속점 분리 검사용)
            
            def flush_pipe():
                """수집 완료된 배관 인스턴스를 기존 배관 리스트에 정점 트리밍 후 추가합니다."""
                nonlocal cur_pipe
                if cur_pipe is None:
                    return
                # 배관 시점/종점에 맞추어 중간 경로 트리밍 수행
                if cur_start is not None and cur_end is not None:
                    cur_pipe["Points"] = trim_to_boundary(cur_pipe["Points"], cur_start, cur_end)
                if len(cur_pipe["Points"]) >= 2:
                    existing_pipes.append(cur_pipe)

            rows = cur.fetchall()
            for row in rows:
                g = row[0]
                row_from = None if (row[6] is None or row[7] is None or row[8] is None) else {"X": float(row[6]), "Y": float(row[7]), "Z": float(row[8])}
                row_to = None if (row[9] is None or row[10] is None or row[11] is None) else {"X": float(row[9]), "Y": float(row[10]), "Z": float(row[11])}
                
                is_new_guid = (cur_guid != g)
                is_disconnected = is_new_guid
                
                # 배관의 끊어짐(Disconnect) 분석: 
                # 직전 세그먼트 끝(last_to)과 이번 세그먼트 시작(row_from) 거리 차이가 10mm(100 mm^2) 초과 시 분리된 배관으로 판정
                if not is_disconnected and last_to is not None and row_from is not None:
                    if dist2(last_to, row_from) > 100.0:
                        is_disconnected = True
                        
                if is_disconnected:
                    flush_pipe()
                    cur_guid = g
                    util = row[2]
                    grp = row[1]
                    cur_pipe = {
                        "Points": [],
                        "RoutePathGuid": g,
                        "Utility": util,
                        "Group": grp,
                        "DiameterMm": parse_pipe_size_mm(row[3]),
                        "SourcePos": None,
                        "TargetPos": None,
                        "Label": f"[{grp or '?'}] {util or '?'}"
                    }
                    
                    cur_start = None if (row[12] is None or row[13] is None or row[14] is None) else {"X": float(row[12]), "Y": float(row[13]), "Z": float(row[14])}
                    cur_end = None if (row[15] is None or row[16] is None or row[17] is None) else {"X": float(row[15]), "Y": float(row[16]), "Z": float(row[17])}
                    cur_pipe["SourcePos"] = cur_start
                    cur_pipe["TargetPos"] = cur_end
                    
                    # 새로운 GUID 배관 경로가 시작되었을 때만 라우팅 태스크(TaskInfo)로 추가
                    if is_new_guid:
                        last_to = None
                        if cur_start is not None and cur_end is not None:
                            current_task = {
                                "RoutePathGuid": g,
                                "Sx": cur_start["X"], "Sy": cur_start["Y"], "Sz": cur_start["Z"],
                                "Gx": cur_end["X"], "Gy": cur_end["Y"], "Gz": cur_end["Z"],
                                "Utility": util,
                                "Group": grp,
                                "EquipmentTag": row[19],
                                "PocName": row[4],
                                "EndName": row[5],
                                "DiameterMm": parse_pipe_size_mm(row[3]),
                                "UtilityLabel": f"[{grp or '?'}] {util or '?'}"
                            }
                            tasks.append(current_task)
                            
                def add_pt(p):
                    """중복 정점을 걸러내고 배관 점 목록(Points)에 좌표 추가"""
                    if len(cur_pipe["Points"]) == 0 or dist2(cur_pipe["Points"][-1], p) > 1.0:
                        cur_pipe["Points"].append(p)
                        
                if row_from is not None:
                    add_pt(row_from)
                if row_to is not None:
                    add_pt(row_to)
                    last_to = row_to

            flush_pipe()
            print(f"    Loaded {len(tasks)} tasks, {len(existing_pipes)} existing pipe lines.")

            # ==================================================================
            # 6) 피팅 자재 부속 정보 쿼리 (TB_ROUTE_SEGMENT_DETAIL에서 TYPE 지정)
            # ==================================================================
            print("  Querying pipe fittings...")
            cur.execute('''
                SELECT sd."TYPE", sd."SIZE", rp."SOURCE_UTILITY",
                       sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",
                       sd."TO_POSX",   sd."TO_POSY",   sd."TO_POSZ"
                  FROM "TB_ROUTE_SEGMENT_DETAIL" sd
                  JOIN "TB_ROUTE_SEGMENTS" s ON s."SEGMENT_GUID" = sd."SEGMENT_GUID"
                  JOIN "TB_ROUTE_PATH" rp    ON rp."ROUTE_PATH_GUID" = s."ROUTE_PATH_GUID"
                 WHERE rp."SOURCE_POSX" BETWEEN %s AND %s
                   AND rp."SOURCE_POSY" BETWEEN %s AND %s
                   AND sd."TYPE" IS NOT NULL
                   AND sd."TYPE" NOT IN ('PIPE','POC','BENDING')
            ''', (minx, maxx, miny, maxy))
            
            fittings = []
            for row in cur.fetchall():
                t_type = row[0] # 피팅 형태 (ELBOW, TEE 등)
                size = row[1]   # 자재 사이즈 규격
                util = row[2]   # 유틸리티 코드
                # 부속 자재의 위치 좌표는 시작/끝의 정가운데(중점)로 산정
                cx = (float(row[3]) + float(row[6])) * 0.5
                cy = (float(row[4]) + float(row[7])) * 0.5
                cz = (float(row[5]) + float(row[8])) * 0.5
                if cx < minx or cx > maxx or cy < miny or cy > maxy or cz < minz or cz > maxz:
                    continue
                fittings.append({
                    "Type": t_type,
                    "Size": size,
                    "X": cx, "Y": cy, "Z": cz,
                    "Utility": util,
                    "DiameterMm": parse_pipe_size_mm(size)
                })
                
            print(f"    Loaded {len(fittings)} pipe fittings.")

            # ==================================================================
            # 7) 그리드 계산 (GridMeta 생성)
            #    전체 객체 및 라우팅 태스크의 높이 범위를 포함하는 전체 그리드 복셀 설정
            # ==================================================================
            cell_mm = args.cell_mm
            gzlo = minz
            gzhi = maxz
            for t in tasks:
                gzlo = min(gzlo, min(t["Sz"], t["Gz"]) - scope_margin)
                gzhi = max(gzhi, max(t["Sz"], t["Gz"]) + scope_margin)
                
            nx = max(1, int(math.ceil((maxx - minx) / cell_mm)))
            ny = max(1, int(math.ceil((maxy - miny) / cell_mm)))
            nz = max(1, int(math.ceil((gzhi - gzlo) / cell_mm)))
            
            grid = {
                "CellMm": cell_mm,
                "Ox": minx, "Oy": miny, "Oz": gzlo,
                "Nx": nx, "Ny": ny, "Nz": nz
            }

            # 8. 프로젝트의 모든 씬 요소를 묶어 C# SceneData 스키마 구조로 구축
            scene_data = {
                "Grid": grid,
                "Obstacles": obstacles,
                "Tasks": tasks,
                "Spaces": spaces,
                "Equipment": equipment,
                "DuctsLaterals": ducts_laterals,
                "ExistingPipes": existing_pipes,
                "Fittings": fittings,
                "SourceFile": group_name,
                "RawText": ""
            }

            # 9. 최종 프로젝트 JSON 결과 파일 저장
            out_file = out_dir / f"{group_name}.json"
            print(f"  Writing scene data to {out_file}...")
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(scene_data, f, ensure_ascii=False, indent=2)
            print(f"  Project {group_name} successfully exported.")
            processed_count += 1
            
        print(f"\nAll done! Successfully exported {processed_count} projects to {out_dir}")

    except Exception as e:
        print(f"Error occurred during execution: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    finally:
        conn.close()
        
    return 0

if __name__ == "__main__":
    sys.exit(main())
