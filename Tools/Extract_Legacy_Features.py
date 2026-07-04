#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Extract_Legacy_Features.py
---------------------------
기존 설계 데이터의 경로(시작점, 종료점) 영역에 걸쳐 있거나 포함되는 
장애물들을 공간적으로 분석하여, 서브 존(Sub-zone) 단위 위상 정보와 
국소 점유 밀도 가중치를 추출하고 legacy_feature_obstacles 테이블에 저장하는 데이터 파이프라인.

사용법:
    python Extract_Legacy_Features.py
    또는
    python Extract_Legacy_Features.py --project WTNHJ02
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "RubberBandRouter"))

import numpy as np
import psycopg2
import psycopg2.extras
import config as cfg

logger = logging.getLogger("ExtractLegacyFeatures")

# ─────────────────────────────────────────────────────────────────────────────
# 1단계 & 2단계: PostGIS 활성화 및 DDL 스키마 준비
# ─────────────────────────────────────────────────────────────────────────────

def prepare_tables(conn) -> None:
    """PostGIS 활성화 및 신규 특징점 테이블 구조 정의 (DDL)"""
    with conn.cursor() as cur:
        # PostGIS 공간 연산 확장 모듈 활성화
        logger.info("[DDL] PostGIS 확장 모듈 설치 및 활성화 검사...")
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis_topology;")
        
        # 신규 특징점 저장 테이블 생성 (문자열 타입의 글자수 제한 에러를 막기 위해 TEXT 로 변경)
        logger.info("[DDL] legacy_feature_obstacles 테이블 생성...")
        cur.execute("""
            DROP TABLE IF EXISTS legacy_feature_obstacles CASCADE;
            CREATE TABLE legacy_feature_obstacles (
                feature_id SERIAL PRIMARY KEY,
                legacy_project_id TEXT NOT NULL,          -- 과거 프로젝트 고유 ID
                obstacle_id TEXT NOT NULL,                 -- 원본 장애물 ID
                category TEXT,                            -- STRUCTURE, EQUIPMENT 등
                
                -- OBB 기하 정보 (중심점, 반폭, 로컬 3축 벡터를 나누어 보관)
                center_x DOUBLE PRECISION,
                center_y DOUBLE PRECISION,
                center_z DOUBLE PRECISION,
                extent_x DOUBLE PRECISION,
                extent_y DOUBLE PRECISION,
                extent_z DOUBLE PRECISION,
                axis_u_x JSONB,                           -- {"x": x, "y": y, "z": z}
                axis_u_y JSONB,
                axis_u_z JSONB,
                
                -- PostGIS 3D 가속 연산용 경계상자
                geom_poly_3d GEOMETRY(PolyhedralSurfaceZ, 0), 
                
                -- 세부 영역 분석을 위한 메타데이터
                sub_zone_id INT,                          -- 분할된 서브 존 인덱스
                voxel_density_weight DOUBLE PRECISION,    -- 해당 블록의 장애물 점유 밀도 가중치 (0.0 ~ 1.0)
                is_penetration BOOLEAN DEFAULT FALSE,     -- 관통 슬리브 여부
                
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 3D 공간 검색용 GiST 인덱스 및 복합 인덱스
        logger.info("[DDL] GiST 인덱스 및 복합 인덱스 생성...")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_feature_geom_3d ON legacy_feature_obstacles USING gist (geom_poly_3d);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_project_subzone ON legacy_feature_obstacles (legacy_project_id, sub_zone_id);")
        
    conn.commit()
    logger.info("[DDL] 스키마 준비 완료.")


# ─────────────────────────────────────────────────────────────────────────────
# 3단계: 기존 설계 경로 범위 내 장애물 필터링 및 특징점 추출
# ─────────────────────────────────────────────────────────────────────────────

def calculate_sub_zone_id(x: float, y: float, z: float) -> int:
    """
    3D 공간 좌표 (mm 단위)를 3m x 3m x 3m (3000mm) 단위의 서브존 인덱스로 매핑.
    공간 크기가 30,000mm 이므로 각 축당 0~9개의 구역이 존재 (총 1000개 서브존).
    """
    ix = int(np.clip(x // 3000, 0, 9))
    iy = int(np.clip(y // 3000, 0, 9))
    iz = int(np.clip(z // 3000, 0, 9))
    return ix + iy * 10 + iz * 100


def get_sub_zone_origin(sub_zone_id: int) -> np.ndarray:
    """서브존의 원점 월드 좌표(mm) 반환"""
    iz = sub_zone_id // 100
    remainder = sub_zone_id % 100
    iy = remainder // 10
    ix = remainder % 10
    return np.array([ix * 3000.0, iy * 3000.0, iz * 3000.0])


def create_3d_box_geometry(min_x, min_y, min_z, max_x, max_y, max_z) -> str:
    """PostGIS PolyhedralSurfaceZ geometry WKT 형식 문자열 생성"""
    # 6개의 면에 대한 3D 다각형 정보 조합
    wkt = (
        f"POLYHEDRALSURFACEZ("
        f"(( {min_x} {min_y} {min_z}, {max_x} {min_y} {min_z}, {max_x} {max_y} {min_z}, {min_x} {max_y} {min_z}, {min_x} {min_y} {min_z} )), "  # Bottom
        f"(( {min_x} {min_y} {max_z}, {min_x} {max_y} {max_z}, {max_x} {max_y} {max_z}, {max_x} {min_y} {max_z}, {min_x} {min_y} {max_z} )), "  # Top
        f"(( {min_x} {min_y} {min_z}, {min_x} {min_y} {max_z}, {max_x} {min_y} {max_z}, {max_x} {min_y} {min_z}, {min_x} {min_y} {min_z} )), "  # Front
        f"(( {max_x} {min_y} {min_z}, {max_x} {min_y} {max_z}, {max_x} {max_y} {max_z}, {max_x} {max_y} {min_z}, {max_x} {min_y} {min_z} )), "  # Right
        f"(( {max_x} {max_y} {min_z}, {max_x} {max_y} {max_z}, {min_x} {max_y} {max_z}, {min_x} {max_y} {min_z}, {max_x} {max_y} {min_z} )), "  # Back
        f"(( {min_x} {max_y} {min_z}, {min_x} {max_y} {max_z}, {min_x} {min_y} {max_z}, {min_x} {min_y} {min_z}, {min_x} {max_y} {min_z} ))"   # Left
        f")"
    )
    return wkt


def run_extraction_pipeline(conn, project_id: str | None = None) -> list[tuple]:
    """기존 설계 경로 주변의 장애물을 공간적으로 분석 및 특징점 데이터로 가공"""
    # 1) 전체 경로 데이터 쿼리
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        logger.info("[Pipeline] 기존 설계 경로 목록 조회 중...")
        sql = """
            SELECT "ROUTE_PATH_GUID", "SOURCE_POSX", "SOURCE_POSY", "SOURCE_POSZ",
                   "TARGET_POSX", "TARGET_POSY", "TARGET_POSZ", "UTILITY_GROUP"
            FROM "TB_ROUTE_PATH"
            WHERE "SOURCE_POSX" IS NOT NULL AND "TARGET_POSX" IS NOT NULL
        """
        params = []
        if project_id:
            sql += ' AND ("EQUIPMENT_TAG" ILIKE %s OR "EQUIPMENT_NAME" ILIKE %s OR "ROUTE_PATH_GUID" LIKE %s)'
            params.append(f"%{project_id}%")
            params.append(f"%{project_id}%")
            params.append(f"%{project_id}%")
        cur.execute(sql, params)
        routes = cur.fetchall()

    logger.info("[Pipeline] 총 %d개의 경로 로드 완료", len(routes))
    
    extracted_features = []
    
    # 2) 각 경로의 ROI 영역을 확장하여 교차되는 장애물 탐색
    for r in routes:
        guid = r["ROUTE_PATH_GUID"]
        sx, sy, sz = float(r["SOURCE_POSX"]), float(r["SOURCE_POSY"]), float(r["SOURCE_POSZ"])
        tx, ty, tz = float(r["TARGET_POSX"]), float(r["TARGET_POSY"]), float(r["TARGET_POSZ"])
        
        # 시작-종단 최소/최대 영역 설정 (마진 1,000mm 적용)
        margin = 1000.0
        min_x, max_x = min(sx, tx) - margin, max(sx, tx) + margin
        min_y, max_y = min(sy, ty) - margin, max(sy, ty) + margin
        min_z, max_z = min(sz, tz) - margin, max(sz, tz) + margin
        
        # PostGIS 3D 공간 쿼리를 통해 관심 영역(ROI)에 걸쳐 있는 장애물 조회
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            query = """
                SELECT "INSTANCE_NAME", "OST_TYPE", "DDWORKS_TYPE", "COLLISION_PASS",
                       "AABB_MINX", "AABB_MINY", "AABB_MINZ",
                       "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"
                FROM "TB_BIM_OBSTACLE"
                WHERE "AABB_MINX" <= %s AND "AABB_MAXX" >= %s
                  AND "AABB_MINY" <= %s AND "AABB_MAXY" >= %s
                  AND "AABB_MINZ" <= %s AND "AABB_MAXZ" >= %s
            """
            cur.execute(query, (max_x, min_x, max_y, min_y, max_z, min_z))
            obstacles = cur.fetchall()

        for obs in obstacles:
            # AABB 정보 획득
            omnx, omny, omnz = float(obs["AABB_MINX"]), float(obs["AABB_MINY"]), float(obs["AABB_MINZ"])
            omxx, omxy, omxz = float(obs["AABB_MAXX"]), float(obs["AABB_MAXY"]), float(obs["AABB_MAXZ"])
            
            # OBB 연산 기하 추정 (축 정렬로 우선 산정)
            cx = (omnx + omxx) / 2.0
            cy = (omny + omxy) / 2.0
            cz = (omnz + omxz) / 2.0
            hx = (omxx - omnx) / 2.0
            hy = (omxy - omny) / 2.0
            hz = (omxz - omnz) / 2.0
            
            sub_zone_id = calculate_sub_zone_id(cx, cy, cz)
            
            # 국소 밀도 (Voxel Density Weight) 연산
            # 서브 블록(3m)^3 전체 부피 중 이 장애물 OBB의 부피 점유율 산정
            zone_volume = 3000.0 ** 3 # 2.7 * 10^10 mm^3
            obs_volume = (hx * 2.0) * (hy * 2.0) * (hz * 2.0)
            voxel_density_weight = min(1.0, obs_volume / zone_volume)
            
            # 슬리브 관통 여부 판별 (Grating, Floor, Ceiling 등)
            ost = str(obs["OST_TYPE"] or "").upper()
            is_penetration = ost in ("OST_FLOORS", "OST_CEILINGS")
            if obs.get("COLLISION_PASS"):
                is_penetration = bool(obs["COLLISION_PASS"])

            geom_wkt = create_3d_box_geometry(omnx, omny, omnz, omxx, omxy, omxz)
            
            # 특징점 리스트 추가
            extracted_features.append((
                guid,  # legacy_project_id로 사용
                obs["INSTANCE_NAME"] or f"OBS_{sub_zone_id}",
                obs["OST_TYPE"] or "STRUCTURE",
                cx, cy, cz,
                hx, hy, hz,
                json.dumps({"x": 1.0, "y": 0.0, "z": 0.0}), # axis_u_x
                json.dumps({"x": 0.0, "y": 1.0, "z": 0.0}), # axis_u_y
                json.dumps({"x": 0.0, "y": 0.0, "z": 1.0}), # axis_u_z
                geom_wkt, # geom_poly_3d용 WKT
                sub_zone_id,
                voxel_density_weight,
                is_penetration
            ))
            
    return extracted_features


# ─────────────────────────────────────────────────────────────────────────────
# 4단계: Python을 활용한 PostgreSQL 데이터 저장 자동화
# ─────────────────────────────────────────────────────────────────────────────

def save_legacy_features_to_postgres(conn, features: list[tuple]) -> None:
    """추출 가공된 특징점 데이터를 legacy_feature_obstacles 테이블에 벌크 적재"""
    if not features:
        logger.warning("[Database] 삽입할 특징점이 없습니다.")
        return

    cursor = conn.cursor()
    
    # 다중 로우 대량 주입 쿼리 (geom_poly_3d 는 WKT에서 geometry 객체로 자동 변환)
    insert_query = """
        INSERT INTO legacy_feature_obstacles (
            legacy_project_id, obstacle_id, category,
            center_x, center_y, center_z,
            extent_x, extent_y, extent_z,
            axis_u_x, axis_u_y, axis_u_z,
            geom_poly_3d,
            sub_zone_id, voxel_density_weight, is_penetration
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s::jsonb, %s::jsonb, %s::jsonb,
            ST_GeomFromText(%s, 0),
            %s, %s, %s
        )
    """

    try:
        logger.info("[Database] %d개의 특징점 적재 시도 중...", len(features))
        psycopg2.extras.execute_batch(cursor, insert_query, features)
        conn.commit()
        logger.info("[Database] 성공: 특징점이 데이터베이스에 성공적으로 누적되었습니다.")
    except Exception as e:
        conn.rollback()
        logger.error("[Database] 특징점 저장 오류 발생: %s", e)
        raise e
    finally:
        cursor.close()


# ─────────────────────────────────────────────────────────────────────────────
# CLI 실행 모듈
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Legacy Feature Extraction Data Pipeline")
    parser.add_argument("--project", type=str, default=None, help="과거 프로젝트 ID 필터 패턴")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    conninfo = cfg.get_conninfo()
    logger.info("[Init] DB 접속 연결 시작: %s", conninfo)
    
    try:
        with psycopg2.connect(conninfo) as conn:
            # 1단계 & 2단계: 테이블 준비
            prepare_tables(conn)
            
            # 3단계: 특징점 추출 및 ROI 쿼리 가공
            features = run_extraction_pipeline(conn, args.project)
            
            # 4단계: DB 적재
            save_legacy_features_to_postgres(conn, features)
            
    except Exception as ex:
        logger.error("[Init] 오류 발생: %s", ex)
        sys.exit(1)


if __name__ == "__main__":
    main()
