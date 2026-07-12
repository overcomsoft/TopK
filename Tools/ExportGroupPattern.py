#!/usr/bin/env python
# -*- coding: utf-8 -*-

# ==============================================================================
# [실행명령어 예시]
#   1) 테이블 스키마 및 인덱스 생성:
#      > python Tools/ExportGroupPattern.py --password dinno create-schema
#   2) 그룹배관 패턴 분석 (DB 미반영, 드라이런으로 로그만 확인):
#      > python Tools/ExportGroupPattern.py --password dinno extract --dry-run
#   3) 그룹배관 패턴 분석 + 3D 렌더링 PNG 이미지 내보내기 (DB 미반영):
#      > python Tools/ExportGroupPattern.py --password dinno extract --dry-run --image-out "data/output/images"
#   4) 스키마 생성 + 패턴 분석 + DB 적재 + 3D 이미지 저장 일괄 실행:
#      > python Tools/ExportGroupPattern.py --password dinno run-all --image-out "data/output/images"
# ==============================================================================

"""
[전체적인 코드내의 흐름도]
1. main() 실행 -> argparse로 명령행 인자(create-schema, extract, run-all) 수신 및 DB 런타임 설정 로드
2. open_connection() -> PostgreSQL 데이터베이스 서버 연결 수립
3. create_schema() -> 'Tools/sql/create_route_group_pattern_tables.sql' 스키마 DDL 실행하여
   TB_ROUTE_GROUP_PATTERN 테이블 생성 (pgvector 확장 미설치 시 fallback_schema_sql()로 FEAT 컬럼 없이 생성)
4. analyze_patterns() 호출 (extract/run-all 공통):
   a. load_route_data_bulk() -> TB_ROUTE_PATH ⋈ TB_ROUTE_PATH_SEGMENTATION 조인,
      MIDDLE_TRUNK_GEOM(=PathSegmenter.py가 산출한 CSF구간 본선)과 START_STUB_GEOM을 함께 조회.
      extract_start_stub_vertical_tail()로 START_STUB_GEOM에서 CSF 진입 직전 수직 하강 구간(격자보
      관통 스텁, A/F->CSF)만 꼬리에서 추출해 Middle Trunk 앞에 이어붙여 경로별 3D 폴리라인 복원
      (장비 PoC 인근의 수평 인입 스텁은 제외 — 실무상 대부분 그룹배관으로 진행되는 구간만 포함)
   b. extract_pipe_feature() (각 경로별 1회) -> extract_orthogonal_segments()로 직교 세그먼트 분해,
      dir_runs/get_arrow_code/count_ortho_bends로 굽힘 패턴 및 트렁크축 특징 추출
   c. (EQUIPMENT_TAG, UTILITY_GROUP, SOURCE_UTILITY) 기준으로 파티션 분할 (파티션마다 독립적으로 스캔)
   d. 파티션별 "세그먼트 레벨 평행 스캔"(Segment-level Parallelism Scan) 반복 실행:
      - 미할당 세그먼트 총길이가 가장 긴 경로를 이번 라운드의 기준경로(Base Route)로 선정
      - check_parallel_overlap()으로 기준경로 세그먼트와 평행(같은 진행축) + 피치 1500mm 이내 +
        겹침구간 100mm 이상인 다른 경로들의 세그먼트를 매칭 (seg_members)
      - 매칭 멤버 2개 이상인 연속 세그먼트들을 갭 허용(Gap-Tolerant) 방식으로 하나의 구간(Section)으로
        병합 — 멤버가 짧게(누적 SECTION_GAP_TOLERANCE_MM=300mm 이내) 매칭되지 않아도(계단식 엘보
        전환 구간 등) 구간을 끊지 않고 이어붙임, 총길이 500mm 미만이면 폐기
      - compute_offset_regularity()로 구간 내 인접 배관 간격의 등간격 여부(CV)와
        수평/수직 오프셋축(HORIZONTAL/VERTICAL/MIXED)을 계산
      - 구간에 쓰인 세그먼트는 assigned=True로 표시하여 다음 라운드에서 제외
   e. bundle_parallel_segments_to_wkt() / generate_trunk_centerline_wkt() ->
      다발 멤버 배관선 및 대표 중심선을 WKT(MultiLineString Z)로 직렬화
5. save_bundle_patterns() -> DB의 TB_ROUTE_GROUP_PATTERN 기존 레코드를 전체 삭제(DELETE) 후
   execute_batch()로 일괄 재삽입 (dry-run 시 생략)
6. (옵션) save_bundle_images() -> Plotly + kaleido로 각 다발을 3D 렌더링하여 PNG로 저장 (--image-out 지정 시)

[핵심 알고리즘]
- A/F->CSF 수직 하강 구간 포함(extract_start_stub_vertical_tail, 신규): PathSegmenter.py는 CSF
  진입점까지의 수직 하강 스텁을 Middle Trunk가 아닌 Start Stub(START_STUB_GEOM)에 저장한다. 이
  구간도 실무상 대부분 그룹배관(다발)이므로, Start Stub을 끝에서부터 역방향 스캔(50mm 미만 지터
  무시, PathSegmenter의 END_STUB 스캔과 동일 규약)하여 마지막 유의미 세그먼트가 Z축인 경우에만
  그 꼬리 부분을 Middle Trunk 앞에 이어붙인다. 장비 PoC 인근의 수평 인입 스텁(A/F 내 이동)은
  스캔 대상에서 제외된다.
- 세그먼트 직교 분해 및 축 스냅: 폴리라인의 각 선분 방향벡터를 정규화한 뒤, 어느 축 성분이
  ARROW_TOL(0.9) 이상이면 그 축(X/Y/Z)으로 스냅. 세 축 모두 미달이면 사선(D)으로 분류하여 평행 판정에서 제외.
- 평행/겹침 판정(check_parallel_overlap): 두 세그먼트가 같은 진행축이고, 진행축에 수직인 평면에서의
  거리(피치)가 max_pitch(기본 1500mm) 이하이며, 진행축 방향 투영구간의 겹침 길이가
  min_overlap(기본 100mm) 이상이면 "평행하게 나란히 달린다"고 판정.
- 반복적 배제 스캔: 매 라운드 가장 긴 미할당 경로를 기준경로로 선정 -> 그 세그먼트별로 평행 멤버를
  찾아 연속 구간(Section)으로 병합 -> 유효 구간(길이 500mm 이상)의 세그먼트를 모두 assigned 처리하여
  다음 라운드에서 제외 -> 미할당 경로가 2개 미만이 될 때까지 반복. 이 방식으로 한 파티션 안에 여러
  다발(랙)이 섞여 있어도 독립적으로 분리 추출됨.
- 갭 허용 구간 병합(Gap-Tolerant Section Merging, 신규): 원래는 기준경로의 세그먼트 하나라도 매칭
  실패하면 그 지점에서 구간이 즉시 끊겼으나(엄격한 교집합 방식), 실제 배관은 개별 배관마다 다른
  위치에서 개별적으로 꺾이는 "계단식" 엘보 구간이 흔해 이 방식은 같은 다발을 여러 조각으로
  분절시키는 문제가 있었다. 이제는 멤버가 일시적으로 매칭되지 않아도 누적 미스매칭 길이가
  SECTION_GAP_TOLERANCE_MM(300mm) 이하이면 구간을 끊지 않고 "브릿지"한다. 단, 해당 세그먼트에서
  기준경로 외에 실제로 매칭되는 멤버가 하나도 없는 "순수 공백 세그먼트"는 구간 데이터(segs)에
  포함하지 않는다 — 그렇지 않으면 서로 무관한 두 구간이 그 사이의 짧은 잡음 세그먼트를 매개로
  하나의 거대한 구간으로 잘못 합쳐진다(초기 구현에서 실제로 발생해 PATTERN_SEQ가 비정상적으로
  길어지고 OFFSET_AXIS=UNKNOWN이 되는 회귀를 확인 후 수정함). 최종적으로 멤버가 2개 미만으로 줄어든
  구간은 valid_sections 필터링 단계에서 다시 한번 제외된다(안전장치).
- 등간격(Equal-Spacing) 판정(compute_offset_regularity, 신규): 구간 내 멤버 배관들을 진행축에 수직인
  횡단평면에 투영한 뒤, 두 횡단축 중 퍼짐(spread)이 더 큰 축을 따라 정렬하고 인접 간격의
  변동계수(CV = 표준편차 / 평균)를 계산. CV ≤ PITCH_CV_MAX(0.30)이면 등간격(IS_EQUAL_SPACING=True)으로 판정.
- 수평/수직 오프셋축 분류(compute_offset_regularity, 신규): 진행축이 X이면 횡단축은 (Y,Z), Y이면 (X,Z),
  Z이면 (X,Y). 두 횡단축의 퍼짐을 비교해 Z축 퍼짐이 우세하면 VERTICAL(층층이 쌓임), X/Y축 퍼짐이
  우세하면 HORIZONTAL(옆으로 나열), 두 퍼짐이 비슷하면(작은쪽/큰쪽 비율 ≥0.8) MIXED로 분류.
  (※ 이는 배관이 "진행하는" 축(PATTERN_SEQ)과는 다른 개념으로, 배관들이 서로 "떨어져 배치된" 축을 의미.)
- 형상/방향 유사도(compute_similarity): Levenshtein 거리 기반 형상 시퀀스 비교(30%) + 리샘플링 방향
  코사인 유사도(30%) + 길이 유사도(20%) + 바운딩박스 스케일 유사도(20%)를 가중합산. 현재 extract 자동
  파이프라인에서는 호출되지 않으며(AVG_SIMILARITY는 상수 0.95로 저장), AnalyzeCustomGroup.py의
  수동 검증(사용자가 GUID를 직접 선택해 그룹 유효성을 확인하는 도구)에서만 사용됨 — 알려진 제한사항.

[주요 함수]
- axis_snap(d): 벡터 d를 6방향(0~5: +x,-x,+y,-y,+z,-z) 중 가장 가까운 축 인덱스로 매핑 (PathSegmenter.py와 동일 규약)
- extract_orthogonal_segments(points, tol): 폴리라인을 축정렬 직교 세그먼트 리스트로 분해
- check_parallel_overlap(s1, s2, max_pitch, min_overlap): 두 세그먼트의 평행성/피치/겹침 조건 판정
- compute_offset_regularity(sec, base_route, partition, m_guids): (신규) 구간 내 등간격 CV 및
  수평/수직/혼합 오프셋축 계산
- extract_pipe_feature(guid, points, row_meta): 경로 1개의 직교세그먼트/굽힘횟수/트렁크축 등 특징 추출
- extract_start_stub_vertical_tail(start_pts): (신규) Start Stub에서 CSF 진입 직전 수직 하강 구간만
  역방향 스캔으로 추출 (수평 인입 스텁이면 빈 리스트)
- load_route_data_bulk(conn, eq_tags): DB에서 Middle Trunk(CSF구간) + Start Stub의 수직 꼬리를
  이어붙인 폴리라인을 일괄 조회
- analyze_patterns(conn, dry_run, image_out): 파티션 분할 + 세그먼트 평행 스캔 + 저장을 수행하는 메인 파이프라인
- save_bundle_patterns(conn, bundles): TB_ROUTE_GROUP_PATTERN에 UPSERT(전체 삭제 후 재삽입)
- save_bundle_images(bundles, processed_routes, output_dir): 검출된 다발을 Plotly 3D로 렌더링해 PNG로 저장
- compute_similarity(a, b): 두 경로의 형상/방향/길이/스케일 종합 유사도 계산 (현재 자동 파이프라인 미사용)
- bundle_parallel_segments_to_wkt / generate_trunk_centerline_wkt: 다발 멤버선/대표중심선을 WKT로 직렬화

[주요 변수]
- ARROW_TOL: 세그먼트를 특정 축으로 스냅하기 위한 단위벡터 성분 임계값(0.9)
- PITCH_CV_MAX: 등간격 판정 임계 변동계수(0.30) — 자동 추출(analyze_patterns)과 수동 검증
  (AnalyzeCustomGroup.py) 양쪽에서 공유
- SECTION_GAP_TOLERANCE_MM: (신규) 구간(Section) 병합 시 멤버의 일시적 미스매칭을 허용하는
  최대 누적 갭 길이(300mm) — 계단식 엘보 전환 구간을 브릿지하기 위함
- SIM_THRESHOLD: 형상 유사도 판정 임계값(0.70, 현재는 AnalyzeCustomGroup.py 수동 검증 전용)
- base_route: 현재 라운드의 기준 경로(미할당 세그먼트 총길이가 가장 긴 경로)
- sec / section: 연속 세그먼트를 병합한 그룹배관 후보 구간(다발 하나에 대응)
- m_guids: 구간에 속한 멤버 경로 GUID 목록
- pitch_mm: 기준경로 대비 다른 멤버들 피치(수직 이격거리)의 중앙값
- pitch_cv / is_equal_spacing / offset_axis: (신규) 구간 내 인접 배관 간격의 변동계수,
  등간격 여부(CV≤0.30), 수평/수직/혼합 오프셋축 분류 결과
- avg_sim: 현재 상수 0.95로 고정 저장됨 (compute_similarity가 자동 파이프라인에 연결되어 있지 않음 — 알려진 제한사항)
"""

import sys
import os
import math
import json
import hashlib
import uuid
import argparse
from pathlib import Path
from collections import defaultdict, Counter

# Add parent directory to sys.path to resolve tool_config correctly
sys.path.append(str(Path(__file__).resolve().parent))
import tool_config
import psycopg2
import psycopg2.extras

# 상수 (Constants)
ARROW_TOL = 0.9          # 세그먼트를 특정 축(X/Y/Z)으로 스냅하기 위한 단위벡터 성분 임계값
RESAMPLE_N = 20          # compute_similarity 방향 비교용 폴리라인 균등 리샘플링 개수
PITCH_CV_MAX = 0.30      # 등간격 판정 임계 변동계수(CV) — 이 값 이하면 "등간격"으로 판정
SIM_THRESHOLD = 0.70     # 형상 유사도 판정 임계값 (현재 AnalyzeCustomGroup.py 수동 검증 전용)
SECTION_GAP_TOLERANCE_MM = 300.0  # 구간(Section) 병합 시, 멤버가 일시적으로 매칭되지 않아도
                                   # 허용하는 최대 누적 갭 길이(짧은 엘보 전환 구간을 "브릿지"하기 위함)

# 성능 최적화용 캐시 (동일 문자열 쌍/특징 쌍에 대한 반복 계산 방지)
_lev_cache = {}
_similarity_cache = {}


def open_connection(conninfo: str):
    """PostgreSQL 서버에 접속하고 커넥션 객체를 반환. 실패 시 SystemExit로 즉시 종료."""
    try:
        return psycopg2.connect(conninfo)
    except Exception as ex:
        raise SystemExit(f"DB connection failed: {ex}")


def table_exists(conn, table: str) -> bool:
    """지정한 테이블명이 public 스키마에 존재하는지 여부를 반환."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name=%s",
            (table,),
        )
        return cur.fetchone()[0] > 0


def pgvector_installed(conn) -> bool:
    """pgvector 확장(FEAT 컬럼의 vector(60) 타입 및 HNSW 인덱스에 필요)이 설치되어 있는지 확인."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM pg_extension WHERE extname='vector'")
        return cur.fetchone()[0] > 0


def create_schema(conn) -> None:
    """
    TB_ROUTE_GROUP_PATTERN 테이블 스키마를 생성한다.
    pgvector 확장이 설치되어 있으면 'sql/create_route_group_pattern_tables.sql'의 DDL을 그대로 실행하고,
    없으면 fallback_schema_sql()로 FEAT(vector) 컬럼 및 HNSW 인덱스를 제외한 JSON 전용 스키마를 생성한다.
    """
    has_vector = pgvector_installed(conn)
    with conn.cursor() as cur:
        if has_vector:
            sql_path = Path(__file__).resolve().parent / "sql" / "create_route_group_pattern_tables.sql"
            if sql_path.exists():
                print(f"Executing DDL from: {sql_path}")
                cur.execute(sql_path.read_text(encoding="utf-8"))
            else:
                print(f"[warn] SQL schema file {sql_path} not found. Using raw execution.")
                cur.execute(fallback_schema_sql(with_vector=True))
        else:
            print("[warn] pgvector extension not found. Creating JSON fallback columns only.")
            cur.execute(fallback_schema_sql(with_vector=False))
    conn.commit()
    print(f"Schema configuration ready. pgvector={'yes' if has_vector else 'no'}")


def fallback_schema_sql(with_vector: bool) -> str:
    """
    pgvector 확장이 없는 환경을 위한 대체 DDL을 생성한다.
    with_vector=True이면 create_route_group_pattern_tables.sql과 동일하게 FEAT(vector) 컬럼 및
    HNSW 인덱스를 포함하고, False이면 이를 제외한 JSON(FEAT_JSON) 전용 스키마만 생성한다.
    """
    vector_col = '"FEAT" vector(60),' if with_vector else ""
    vector_idx = 'CREATE INDEX IF NOT EXISTS "IX_TRGP_FEAT_HNSW" ON "TB_ROUTE_GROUP_PATTERN" USING hnsw ("FEAT" vector_l2_ops);' if with_vector else ""

    return f"""
DROP TABLE IF EXISTS "TB_ROUTE_GROUP_PATTERN" CASCADE;
CREATE TABLE IF NOT EXISTS "TB_ROUTE_GROUP_PATTERN" (
    "GROUP_ID" text PRIMARY KEY,
    "EQUIPMENT_TAG" text NOT NULL,
    "UTILITY_GROUP" text NOT NULL,
    "UTILITY" text NOT NULL,
    "N_MEMBERS" integer NOT NULL,
    "AVG_SIMILARITY" double precision NOT NULL,
    "TRUNK_Z" double precision NOT NULL,
    "TRUNK_XY_SPREAD" double precision NOT NULL,
    "PITCH_MM" double precision NOT NULL,
    "PITCH_CV" double precision NOT NULL DEFAULT 0.0,
    "IS_EQUAL_SPACING" boolean NOT NULL DEFAULT true,
    "OFFSET_AXIS" text NOT NULL DEFAULT 'UNKNOWN',
    "N_ORTHO_BENDS" integer NOT NULL,
    "MEMBER_GUIDS" jsonb NOT NULL,
    "PATTERN_SEQ" text,
    "SECTION_BOUNDS" jsonb,
    {vector_col}
    "FEAT_JSON" jsonb,
    "GEOM_3D" geometry(MultiLineStringZ, 0),
    "TRUNK_GEOM_3D" geometry(MultiLineStringZ, 0),
    "TRUNK_LEN" double precision NOT NULL DEFAULT 0.0,
    "CREATED_AT" timestamp without time zone DEFAULT now()
);
CREATE INDEX IF NOT EXISTS "IX_TRGP_KEY"
ON "TB_ROUTE_GROUP_PATTERN" ("EQUIPMENT_TAG", "UTILITY_GROUP", "UTILITY");
{vector_idx}
CREATE INDEX IF NOT EXISTS "IX_TRGP_GEOM"
ON "TB_ROUTE_GROUP_PATTERN" USING gist("GEOM_3D");
CREATE INDEX IF NOT EXISTS "IX_TRGP_TRUNK_GEOM"
ON "TB_ROUTE_GROUP_PATTERN" USING gist("TRUNK_GEOM_3D");
"""


# --- Geometry & Math Helpers ---

def bundle_routes_to_wkt_multilinestringz(member_routes: list[dict]) -> str:
    """
    그룹 내에 속한 각 멤버 배관의 실제 3D Polyline 좌표점 목록을
    PostGIS 공간 연산이 가능한 MULTILINESTRING Z WKT 문자열로 변환합니다.
    """
    if not member_routes:
        return None
        
    lines = []
    for r in member_routes:
        pts = r.get('points', [])
        if len(pts) < 2:
            continue
        pts_str = ", ".join(f"{float(pt[0]):.9g} {float(pt[1]):.9g} {float(pt[2]):.9g}" for pt in pts)
        lines.append(f"({pts_str})")
        
    if not lines:
        return None
        
    return f"MULTILINESTRING Z ({', '.join(lines)})"


def bundle_parallel_segments_to_wkt(sec: dict, base_route: dict, partition: list[dict], m_guids: list[str]) -> str:
    """
    그룹 내 멤버들의 전체 배관 경로 대신, 실제로 평행하게 겹치는 
    그룹배관 구간(Parallel Segments)의 좌표 정보만 추출하여 MULTILINESTRING Z WKT로 반환합니다.
    """
    lines = []
    for m_guid in m_guids:
        member_points = []
        for sm in sec['segs']:
            b_seg = sm['base_seg']
            if m_guid == base_route['guid']:
                member_points.append(b_seg['from'])
                member_points.append(b_seg['to'])
            else:
                other_r = next((r for r in partition if r['guid'] == m_guid), None)
                if not other_r:
                    continue
                for o_seg in other_r['ortho_segs']:
                    pitch, overlap = check_parallel_overlap(b_seg, o_seg)
                    if pitch is not None:
                        member_points.append(o_seg['from'])
                        member_points.append(o_seg['to'])
                        break
                        
        if len(member_points) >= 2:
            for i in range(0, len(member_points), 2):
                if i + 1 < len(member_points):
                    p1 = member_points[i]
                    p2 = member_points[i+1]
                    pts_str = f"{p1[0]:.9g} {p1[1]:.9g} {p1[2]:.9g}, {p2[0]:.9g} {p2[1]:.9g} {p2[2]:.9g}"
                    lines.append(f"({pts_str})")
                    
    if not lines:
        return None
    return f"MULTILINESTRING Z ({', '.join(lines)})"


def generate_trunk_centerline_wkt(section_bounds: list[dict]) -> str:
    """
    각 다발 구간의 바운딩 박스(SECTION_BOUNDS)를 관통하는
    3D 대표 중심선 경로를 계산하여 MULTILINESTRING Z WKT 문자열로 반환합니다.
    """
    if not section_bounds:
        return None
        
    lines = []
    for sec in section_bounds:
        min_pt = sec.get('min')
        max_pt = sec.get('max')
        t = sec.get('type')
        if not min_pt or not max_pt or not t:
            continue
            
        cx = (min_pt[0] + max_pt[0]) / 2.0
        cy = (min_pt[1] + max_pt[1]) / 2.0
        cz = (min_pt[2] + max_pt[2]) / 2.0
        
        if t == 'X':
            start = (min_pt[0], cy, cz)
            end = (max_pt[0], cy, cz)
        elif t == 'Y':
            start = (cx, min_pt[1], cz)
            end = (cx, max_pt[1], cz)
        elif t == 'Z':
            start = (cx, cy, min_pt[2])
            end = (cx, cy, max_pt[2])
        else:
            start = min_pt
            end = max_pt
            
        lines.append(f"({start[0]:.9g} {start[1]:.9g} {start[2]:.9g}, {end[0]:.9g} {end[1]:.9g} {end[2]:.9g})")
        
    if not lines:
        return None
        
    return f"MULTILINESTRING Z ({', '.join(lines)})"


# --- 3D 벡터 기초 연산 헬퍼 ---

def dist(a, b) -> float:
    """두 3D 점 사이의 유클리드 거리."""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def vec_sub(a, b) -> tuple[float, float, float]:
    """벡터 a - b (3D)."""
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def unit(v) -> tuple[float, float, float]:
    """벡터 v를 단위벡터로 정규화. 길이가 0에 가까우면 영벡터 반환."""
    length = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if length < 1e-9:
        return (0.0, 0.0, 0.0)
    return (v[0] / length, v[1] / length, v[2] / length)


def dot_product_3d(u, v) -> float:
    """두 3D 벡터의 내적."""
    return u[0]*v[0] + u[1]*v[1] + u[2]*v[2]


def axis_snap(d: tuple[float, float, float]) -> int:
    """
    벡터 d를 6방향(0:+x, 1:-x, 2:+y, 3:-y, 4:+z, 5:-z) 중 가장 가까운 축 인덱스로 매핑한다.
    (PathSegmenter.py의 axis_snap과 동일한 규약: 절대값이 가장 큰 성분의 축을 지배축으로 선택)
    """
    values = [abs(d[0]), abs(d[1]), abs(d[2])]
    ax = max(range(3), key=lambda i: values[i])
    return ax * 2 + (0 if d[ax] >= 0 else 1)


def extract_start_stub_vertical_tail(start_pts: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """
    Start Stub 폴리라인(장비 PoC ~ CSF 진입점, PathSegmenter.segment_route()의 START_STUB_GEOM)에서
    CSF 진입 직전의 수직 하강 구간(격자보 관통 스텁)만 끝에서부터 역방향으로 잘라낸다.
    PathSegmenter.segment_route()의 END_STUB 역방향 스캔과 동일하게 50mm 미만 지터 세그먼트는
    무시하고, 끝점 기준 첫 유의미(>=50mm) 세그먼트의 진행축이 Z축이 아니면(=장비 인입부가 수평
    스텁으로 끝나는 경우) 빈 리스트를 반환한다 — 이 경우 CSF로 내려가는 수직 구간이 없으므로
    그룹배관 스캔 대상에서 제외된다. Z축이면 방향이 바뀌는 지점까지의 꼬리 부분만 반환한다.
    """
    n = len(start_pts)
    if n < 2:
        return []

    last_axis = -1
    for i in range(n - 2, -1, -1):
        a, b = start_pts[i], start_pts[i + 1]
        if dist(a, b) >= 50.0:
            last_axis = axis_snap(vec_sub(b, a)) // 2
            break

    if last_axis != 2:
        return []

    end_idx = n - 1
    for i in range(n - 2, -1, -1):
        a, b = start_pts[i], start_pts[i + 1]
        L = dist(a, b)
        if L < 50.0:
            end_idx = i
            continue
        curr_axis = axis_snap(vec_sub(b, a)) // 2
        if curr_axis != last_axis:
            end_idx = i + 1
            break
        end_idx = i

    return start_pts[end_idx:]


def dir_runs(points: list[tuple[float, float, float]]) -> list[tuple[int, float]]:
    """
    폴리라인을 axis_snap 기준 동일 축 방향이 이어지는 연속 구간(run)들로 압축한다.
    반환값은 [(축 인덱스 0~5, 그 축으로의 누적 이동거리), ...] 리스트.
    """
    runs = []
    for a, b in zip(points, points[1:]):
        length = dist(a, b)
        if length < 1e-3:
            continue
        direction = axis_snap(vec_sub(b, a))
        if runs and runs[-1][0] == direction:
            runs[-1] = (direction, runs[-1][1] + length)
        else:
            runs.append((direction, length))
    return runs


def get_arrow_code(points: list[tuple[float, float, float]], tol=ARROW_TOL) -> str:
    """
    폴리라인을 V(수직/Z축 이동)·H(수평/XY축 이동)·D(경사) 문자 시퀀스로 압축한 "화살표 코드"를 생성한다.
    dir_runs()의 6방향(X/Y/Z 개별축) 분류와 달리 X/Y를 하나의 H로 합쳐 더 거친 형상 요약을 만들며,
    compute_similarity()의 형상 유사도(Levenshtein 거리) 비교에 사용된다.
    """
    codes = []
    for a, b in zip(points, points[1:]):
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        dz = b[2] - a[2]
        L = math.sqrt(dx**2 + dy**2 + dz**2)
        if L < 1e-3:
            continue
        ux, uy, uz = dx/L, dy/L, dz/L

        # 분류 기준: V(수직/Z축), H(수평/XY평면), D(경사, 어느 쪽으로도 tol 이상 쏠리지 않음)
        if abs(uz) >= tol:
            code = 'V'
        elif max(abs(ux), abs(uy)) >= tol:
            code = 'H'
        else:
            code = 'D'
            
        if not codes or codes[-1] != code:
            codes.append(code)
    return "".join(codes)


def count_ortho_bends(runs: list[tuple[int, float]]) -> int:
    """dir_runs() 결과에서 축 그룹(X/Y ↔ Z, //2로 비교)이 바뀌는 지점의 개수(직교 굽힘 횟수)를 센다."""
    bends = 0
    for i in range(len(runs) - 1):
        if runs[i][0] // 2 != runs[i+1][0] // 2:
            bends += 1
    return bends


def resample_polyline_directions(points: list[tuple[float, float, float]], N=RESAMPLE_N) -> list[float]:
    """
    폴리라인을 호 길이(arc length) 기준으로 N등분한 뒤, 각 구간의 단위 방향벡터를 이어붙인
    길이 N*3짜리 실수 리스트(seg_units)를 반환한다. compute_similarity()의 방향 코사인 유사도
    비교 및 FEAT(60차원, N=20*3) 벡터의 원본 데이터로 쓰인다.
    """
    if len(points) < 2:
        return [0.0] * (N * 3)
        
    dists = [0.0]
    for a, b in zip(points, points[1:]):
        dists.append(dists[-1] + dist(a, b))
        
    total_len = dists[-1]
    if total_len < 1e-3:
        return [0.0] * (N * 3)
        
    resampled_pts = []
    for j in range(N + 1):
        target_d = j * (total_len / N)
        idx = 0
        while idx < len(dists) - 2 and dists[idx+1] < target_d:
            idx += 1
        d1 = dists[idx]
        d2 = dists[idx+1]
        p1 = points[idx]
        p2 = points[idx+1]
        
        t = (target_d - d1) / (d2 - d1) if (d2 - d1) > 1e-6 else 0.0
        x = p1[0] + t * (p2[0] - p1[0])
        y = p1[1] + t * (p2[1] - p1[1])
        z = p1[2] + t * (p2[2] - p1[2])
        resampled_pts.append((x, y, z))
        
    flat_units = []
    for i in range(N):
        p_from = resampled_pts[i]
        p_to = resampled_pts[i+1]
        dx = p_to[0] - p_from[0]
        dy = p_to[1] - p_from[1]
        dz = p_to[2] - p_from[2]
        L = math.sqrt(dx**2 + dy**2 + dz**2)
        if L < 1e-6:
            flat_units.extend([0.0, 0.0, 0.0])
        else:
            flat_units.extend([dx/L, dy/L, dz/L])
            
    return flat_units


def levenshtein_distance(s1, s2):
    """
    두 문자열(주로 get_arrow_code의 V/H/D 코드) 간 편집거리를 계산한다.
    결과는 (s1, s2) 정렬쌍 기준으로 _lev_cache에 캐싱되어 동일 쌍 재계산을 피한다.
    """
    key = (s1, s2) if s1 <= s2 else (s2, s1)
    if key in _lev_cache:
        return _lev_cache[key]
    if len(s1) < len(s2):
        res = levenshtein_distance(s2, s1)
        _lev_cache[key] = res
        return res
    if len(s2) == 0:
        _lev_cache[key] = len(s1)
        return len(s1)
    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    res = previous_row[-1]
    _lev_cache[key] = res
    return res


def compute_similarity(a, b, N=RESAMPLE_N) -> float:
    """
    두 경로 특징(extract_pipe_feature 결과)의 종합 유사도(0~1)를 계산한다.
    형상(30%) + 방향(30%) + 길이(20%) + 스케일(20%) 가중합산.
    ※ 현재 analyze_patterns() 자동 파이프라인에서는 호출되지 않으며(AVG_SIMILARITY는 상수 0.95로 저장),
      AnalyzeCustomGroup.py의 수동 그룹 검증에서만 실제로 사용된다.
    """
    # 1. 형상 유사도(Shape Similarity, 30%) — V/H/D 화살표 코드의 Levenshtein 편집거리 기반
    arrow_a = a['arrow_code']
    arrow_b = b['arrow_code']
    max_arrow_len = max(len(arrow_a), len(arrow_b))
    if max_arrow_len == 0:
        shape_sim = 1.0
    else:
        lev_dist = levenshtein_distance(arrow_a, arrow_b)
        shape_sim = 1.0 - (lev_dist / max_arrow_len)
    shape_sim = max(0.0, min(1.0, shape_sim))

    # 2. 방향 유사도(Direction Similarity, 30%) — 리샘플링된 방향벡터의 코사인 유사도.
    #    두 경로가 서로 반대 방향(장비->덕트 vs 덕트->장비)으로 저장되었을 가능성을 감안해
    #    정방향(cos_forward)과 역방향(cos_backward) 중 더 높은 값을 채택한다.
    u = a['seg_units_3d']
    v = b['seg_units_3d']

    cos_forward = sum(dot_product_3d(u[i], v[i]) for i in range(N)) / N

    v_backward = []
    for i in range(N):
        orig_vec = v[N - 1 - i]
        v_backward.append((-orig_vec[0], -orig_vec[1], -orig_vec[2]))
    cos_backward = sum(dot_product_3d(u[i], v_backward[i]) for i in range(N)) / N

    dir_sim = max(0.0, max(cos_forward, cos_backward))
    dir_sim = min(1.0, dir_sim)

    # 3. 길이 유사도(Length Similarity, 20%) — 총 배관 길이 차이 비율
    len_a = a['total_len']
    len_b = b['total_len']
    max_len = max(len_a, len_b)
    if max_len < 1e-3:
        len_sim = 1.0
    else:
        len_sim = 1.0 - (abs(len_a - len_b) / max_len)
    len_sim = max(0.0, min(1.0, len_sim))
    
    # 4. 스케일 유사도(Scale Similarity, 20%) — X/Y/Z 각 축 바운딩박스 크기 비율의 평균
    ext_a = a['extent']
    ext_b = b['extent']
    scale_sims = []
    for i in range(3):
        ea = ext_a[i]
        eb = ext_b[i]
        if ea < 1.0 and eb < 1.0:
            scale_sims.append(1.0)
        elif ea < 1.0 or eb < 1.0:
            scale_sims.append(0.0)
        else:
            scale_sims.append(min(ea, eb) / max(ea, eb))
    scale_sim = sum(scale_sims) / 3.0
    scale_sim = max(0.0, min(1.0, scale_sim))
    
    return 0.3 * shape_sim + 0.3 * dir_sim + 0.2 * len_sim + 0.2 * scale_sim


# --- Union-Find Helper ---
# 참고: analyze_patterns()의 실제 클러스터링은 아래 "반복적 배제 스캔"(기준경로 선정 + 구간 병합) 방식을
# 사용하며, 이 UnionFind 클래스는 현재 파일 내 어디에서도 인스턴스화되지 않는 미사용(dead) 코드다.

class UnionFind:
    def __init__(self, elements):
        self.parent = {el: el for el in elements}
        self.rank = {el: 0 for el in elements}
        
    def find(self, x):
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]
        
    def union(self, x, y):
        root_x = self.find(x)
        root_y = self.find(y)
        if root_x != root_y:
            if self.rank[root_x] > self.rank[root_y]:
                self.parent[root_y] = root_x
            else:
                self.parent[root_x] = root_y
                if self.rank[root_x] == self.rank[root_y]:
                    self.rank[root_y] += 1


def extract_orthogonal_segments(points, tol=ARROW_TOL):
    """
    폴리라인을 축정렬 직교 세그먼트 리스트로 분해한다. 각 세그먼트는 진행축(X/Y/Z, 경사면 D)에 따라
    'from'/'to' 좌표의 횡단(수직) 성분을 두 끝점의 평균값으로 고정(축 정렬)하여, 미세한 모서리 정렬
    오차가 이후 check_parallel_overlap()의 피치 계산에 섞여 들어가는 것을 방지한다.
    """
    segments = []
    for a, b in zip(points, points[1:]):
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        dz = b[2] - a[2]
        L = math.sqrt(dx**2 + dy**2 + dz**2)
        if L < 1.0:  # 1mm 미만의 미세 세그먼트는 무시
            continue
        ux, uy, uz = dx/L, dy/L, dz/L
        
        if abs(uz) >= tol:
            direction = 'Z'
            mx = (a[0] + b[0]) / 2.0
            my = (a[1] + b[1]) / 2.0
            p_from = (mx, my, a[2])
            p_to = (mx, my, b[2])
        elif abs(ux) >= tol:
            direction = 'X'
            my = (a[1] + b[1]) / 2.0
            mz = (a[2] + b[2]) / 2.0
            p_from = (a[0], my, mz)
            p_to = (b[0], my, mz)
        elif abs(uy) >= tol:
            direction = 'Y'
            mx = (a[0] + b[0]) / 2.0
            mz = (a[2] + b[2]) / 2.0
            p_from = (mx, a[1], mz)
            p_to = (mx, b[1], mz)
        else:
            direction = 'D'
            p_from = a
            p_to = b
            
        segments.append({
            'from': p_from,
            'to': p_to,
            'dir': direction,
            'len': L,
            'vector': (dx, dy, dz),
            'unit': (ux, uy, uz)
        })
    return segments


def check_parallel_overlap(s1, s2, max_pitch=1500.0, min_overlap=100.0):
    """
    두 직교 세그먼트가 "평행하게 나란히 달리는지" 판정한다.
    조건: (1) 같은 진행축(dir)이고 경사(D)가 아니며, (2) 진행축에 수직인 평면에서의 거리(피치)가
    max_pitch 이하, (3) 진행축 방향으로 투영한 두 구간의 겹침 길이가 min_overlap 이상.
    통과 시 (pitch, overlap_len)을 반환하고, 미통과 시 (None, 0.0)을 반환한다.
    """
    if s1['dir'] != s2['dir'] or s1['dir'] == 'D':
        return None, 0.0

    d = s1['dir']
    p1_from, p1_to = s1['from'], s1['to']
    p2_from, p2_to = s2['from'], s2['to']
    
    if d == 'X':
        y1, z1 = p1_from[1], p1_from[2]
        y2, z2 = p2_from[1], p2_from[2]
        pitch = math.sqrt((y1 - y2)**2 + (z1 - z2)**2)
        min1, max1 = min(p1_from[0], p1_to[0]), max(p1_from[0], p1_to[0])
        min2, max2 = min(p2_from[0], p2_to[0]), max(p2_from[0], p2_to[0])
    elif d == 'Y':
        x1, z1 = p1_from[0], p1_from[2]
        x2, z2 = p2_from[0], p2_from[2]
        pitch = math.sqrt((x1 - x2)**2 + (z1 - z2)**2)
        min1, max1 = min(p1_from[1], p1_to[1]), max(p1_from[1], p1_to[1])
        min2, max2 = min(p2_from[1], p2_to[1]), max(p2_from[1], p2_to[1])
    else:  # 'Z'
        x1, y1 = p1_from[0], p1_from[1]
        x2, y2 = p2_from[0], p2_from[1]
        pitch = math.sqrt((x1 - x2)**2 + (y1 - y2)**2)
        min1, max1 = min(p1_from[2], p1_to[2]), max(p1_from[2], p1_to[2])
        min2, max2 = min(p2_from[2], p2_to[2]), max(p2_from[2], p2_to[2])
        
    if pitch > max_pitch:
        return None, 0.0
        
    overlap_min = max(min1, min2)
    overlap_max = min(max1, max2)
    overlap_len = overlap_max - overlap_min
    
    if overlap_len < min_overlap:
        return None, 0.0
        
    return pitch, overlap_len


def compute_offset_regularity(sec, base_route, partition, m_guids):
    """
    번들 섹션 내 멤버 배관들의 진행방향에 수직인(transverse) 평면 상 위치를 모아
    (1) 배관들이 서로 떨어져 배치된(오프셋) 축이 수평(X/Y)인지 수직(Z)인지 분류하고,
    (2) 그 축을 따라 정렬했을 때 인접 배관 간 간격의 변동계수(CV)로 등간격 여부를 판정한다.
    (진행방향 자체의 수평/수직 여부는 기존 PATTERN_SEQ로 이미 알 수 있음 — 이 함수는
     "배관들이 서로 나란히 퍼진 방향"이라는 별도의 축을 다룬다: 예를 들어 남북으로 나란히
     진행하는 3개 배관이 동서(X)로 늘어서 있는지, 상하(Z)로 층층이 쌓여있는지를 구분한다.)

    반환값: (pitch_cv, is_equal_spacing, offset_axis)
      offset_axis: 'HORIZONTAL' | 'VERTICAL' | 'MIXED' | 'UNKNOWN'
    """
    dir_counts = Counter(sm['base_seg']['dir'] for sm in sec['segs'])
    dominant_dir = dir_counts.most_common(1)[0][0]

    # 진행방향(dominant_dir)에 수직인 두 축: X 진행 -> 횡단면(Y,Z), Y 진행 -> (X,Z), Z 진행 -> (X,Y)
    if dominant_dir == 'X':
        axis_idx, axis_names = (1, 2), ('Y', 'Z')
    elif dominant_dir == 'Y':
        axis_idx, axis_names = (0, 2), ('X', 'Z')
    else:  # 'Z' 진행(수직 배관 다발) -> 두 횡단축 모두 수평(X,Y), 즉 서로 층층이 쌓일 수 없음
        axis_idx, axis_names = (0, 1), ('X', 'Y')

    member_positions = defaultdict(list)
    for sm in sec['segs']:
        b_seg = sm['base_seg']
        if b_seg['dir'] != dominant_dir:
            continue
        member_positions[base_route['guid']].append((b_seg['from'][axis_idx[0]], b_seg['from'][axis_idx[1]]))
        for m_guid in m_guids:
            if m_guid == base_route['guid']:
                continue
            other_r = next((r for r in partition if r['guid'] == m_guid), None)
            if other_r is None:
                continue
            for o_seg in other_r['ortho_segs']:
                pitch, overlap = check_parallel_overlap(b_seg, o_seg)
                if pitch is not None:
                    member_positions[m_guid].append((o_seg['from'][axis_idx[0]], o_seg['from'][axis_idx[1]]))
                    break

    # 멤버별 대표 횡단위치 (여러 스텝에 걸친 중앙값으로 잡음 완화)
    rep_pos = {}
    for m_guid in m_guids:
        pts = member_positions.get(m_guid)
        if not pts:
            continue
        rep_pos[m_guid] = (get_median([p[0] for p in pts]), get_median([p[1] for p in pts]))

    if len(rep_pos) < 2:
        return 0.0, True, 'UNKNOWN'

    vals0 = [p[0] for p in rep_pos.values()]
    vals1 = [p[1] for p in rep_pos.values()]
    spread0 = max(vals0) - min(vals0)
    spread1 = max(vals1) - min(vals1)

    bigger, smaller = max(spread0, spread1), min(spread0, spread1)
    if bigger < 1e-6:
        offset_axis = 'UNKNOWN'
        pack_values = vals0
    else:
        dominant_offset_axis_name = axis_names[0] if spread0 >= spread1 else axis_names[1]
        if smaller / bigger >= 0.8:
            # 두 횡단축 퍼짐이 비슷하면(20% 이내) 대각선/혼합 배치로 판정
            offset_axis = 'MIXED'
        elif dominant_offset_axis_name == 'Z':
            offset_axis = 'VERTICAL'
        else:
            offset_axis = 'HORIZONTAL'
        pack_values = vals0 if spread0 >= spread1 else vals1

    pack_values = sorted(pack_values)
    gaps = [pack_values[i + 1] - pack_values[i] for i in range(len(pack_values) - 1)]
    mean_gap = sum(gaps) / len(gaps) if gaps else 0.0
    if mean_gap > 1e-6:
        variance = sum((g - mean_gap) ** 2 for g in gaps) / len(gaps)
        cv = math.sqrt(variance) / mean_gap
    else:
        cv = 0.0

    is_equal_spacing = cv <= PITCH_CV_MAX
    return round(cv, 4), is_equal_spacing, offset_axis


def get_median(values):
    """숫자 리스트의 중앙값. 빈 리스트면 0."""
    if not values:
        return 0
    s_vals = sorted(values)
    n = len(s_vals)
    if n % 2 == 1:
        return s_vals[n // 2]
    else:
        return (s_vals[n // 2 - 1] + s_vals[n // 2]) / 2.0


def get_mode(values):
    """리스트에서 가장 빈도가 높은 값(최빈값). 빈 리스트면 0."""
    if not values:
        return 0
    c = Counter(values)
    return c.most_common(1)[0][0]


def stable_id(*parts: str) -> str:
    """입력 문자열들을 '|'로 이어붙여 SHA1 해시한 뒤 앞 24자를 GROUP_ID로 사용할 안정적 고유 ID로 반환."""
    h = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return h


# --- Core Pipeline Processes ---

def extract_pipe_feature(guid, points, row_meta) -> dict:
    """
    경로 1개(Middle Trunk 폴리라인)로부터 그룹배관 탐지·유사도 비교에 필요한 특징들을 추출한다.
    직교 굽힘 시퀀스(dir_runs), V/H/D 화살표 코드, 방향 리샘플링 벡터, 총 길이, 바운딩박스,
    트렁크축(수평 이동이 가장 긴 축), 직교 세그먼트 목록(ortho_segs, analyze_patterns의 평행 스캔 대상)을
    딕셔너리로 반환한다. 점이 2개 미만이면 빈 딕셔너리를 반환한다.
    """
    if len(points) < 2:
        return {}

    d_runs = dir_runs(points)
    arr_code = get_arrow_code(points)
    n_bends = count_ortho_bends(d_runs)
    seg_units = resample_polyline_directions(points)
    seg_units_3d = [seg_units[i*3:(i+1)*3] for i in range(RESAMPLE_N)]
    
    # 총 길이
    total_len = sum(dist(a, b) for a, b in zip(points, points[1:]))

    # 바운딩박스
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    min_z, max_z = min(zs), max(zs)
    extent = (max_x - min_x, max_y - min_y, max_z - min_z)
    
    centroid = ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0, (min_z + max_z) / 2.0)
    
    # 트렁크축: 가장 긴 수평(X/Y) run의 진행축을 대표 수평 이동축으로 채택
    # d_runs의 각 원소는 (방향 인덱스 0~5, 그 방향으로의 누적길이)
    longest_horizontal_run_dir = -1
    longest_len = -1.0
    for direction, run_len in d_runs:
        if direction in (0, 1, 2, 3):  # X 또는 Y축
            if run_len > longest_len:
                longest_len = run_len
                longest_horizontal_run_dir = direction

    if longest_horizontal_run_dir in (0, 1):
        trunk_axis = 0  # X축이 트렁크축
    elif longest_horizontal_run_dir in (2, 3):
        trunk_axis = 1  # Y축이 트렁크축
    else:
        # 수평 run이 전혀 없으면(수직 배관만 존재) 바운딩박스의 우세 수평축으로 대체
        trunk_axis = 0 if extent[0] >= extent[1] else 1
        
    ortho_segs = extract_orthogonal_segments(points)
        
    return {
        'guid': guid,
        'points': points,
        'eq_tag': row_meta['eq_tag'],
        'utility': row_meta['utility'],
        'utility_group': row_meta['utility_group'],
        'dir_runs': d_runs,
        'arrow_code': arr_code,
        'n_ortho_bends': n_bends,
        'seg_units': seg_units,
        'seg_units_3d': seg_units_3d,
        'total_len': total_len,
        'extent': extent,
        'centroid': centroid,
        'trunk_axis': trunk_axis,
        'ortho_segs': ortho_segs,
    }


def load_route_data_bulk(conn, eq_tags=None) -> list[dict]:
    """
    TB_ROUTE_PATH와 TB_ROUTE_PATH_SEGMENTATION을 조인하여, 각 경로의 Middle Trunk(CSF구간) 폴리라인과
    장비/유틸리티 메타데이터를 일괄 조회한다. eq_tags를 지정하면 해당 EQUIPMENT_TAG 목록으로 필터링한다
    (현재 analyze_patterns()는 항상 None으로 호출하여 전체 경로를 대상으로 함).

    Middle Trunk만으로는 A/F에서 CSF로 내려가는 수직 하강 구간(격자보 관통 스텁)이 누락된다 —
    PathSegmenter.segment_route()는 이 구간을 CSF 진입점까지 포함해 START_STUB_GEOM에 저장하기
    때문이다(Tools/PathSegmenter.py:127-142 주석 참조). 실무상 이 수직 하강 구간도 대부분 그룹배관
    (다발)으로 진행되므로, START_STUB_GEOM도 함께 조회하여 extract_start_stub_vertical_tail()로
    수직 꼬리 부분만 추출해 Middle Trunk 앞에 이어붙인다(장비 PoC 인근의 수평 인입 스텁은 제외).
    """
    print("Fetching route path middle trunk geometries and attributes from DB...")

    where_clause = ""
    params = []
    if eq_tags:
        placeholders = ", ".join(["%s"] * len(eq_tags))
        where_clause = f'WHERE rp."EQUIPMENT_TAG" IN ({placeholders})'
        params = list(eq_tags)

    sql = f"""
        SELECT
            rp."ROUTE_PATH_GUID",
            rp."EQUIPMENT_TAG",
            rp."SOURCE_UTILITY",
            rp."UTILITY_GROUP",
            rp."SOURCE_SIZE",
            ST_AsText(ps."START_STUB_GEOM") AS "START_WKT",
            ST_AsText(ps."MIDDLE_TRUNK_GEOM") AS "TRUNK_WKT"
        FROM "TB_ROUTE_PATH" rp
        JOIN "TB_ROUTE_PATH_SEGMENTATION" ps ON rp."ROUTE_PATH_GUID" = ps."ROUTE_PATH_GUID"
        {where_clause}
        ORDER BY rp."ROUTE_PATH_GUID"
    """

    def parse_wkt_linestring_z(wkt: str) -> list[tuple[float, float, float]]:
        if not wkt or not wkt.upper().startswith("LINESTRING"):
            return []
        cleaned = wkt.replace("LINESTRING", "").replace("Z", "").replace("z", "").strip().strip("()").strip()
        points = []
        for pt_str in cleaned.split(","):
            coords = pt_str.strip().split()
            if len(coords) >= 3:
                points.append((float(coords[0]), float(coords[1]), float(coords[2])))
        return points

    routes = []
    vertical_tail_count = 0
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        print(f"Total middle trunk records fetched: {len(rows)}")
        for r in rows:
            guid = r['ROUTE_PATH_GUID'].strip()
            pts = parse_wkt_linestring_z(r.get('TRUNK_WKT', ''))
            start_pts = parse_wkt_linestring_z(r.get('START_WKT', ''))

            vertical_tail = extract_start_stub_vertical_tail(start_pts)
            if len(vertical_tail) >= 2 and len(pts) >= 1:
                if dist(vertical_tail[-1], pts[0]) < 1.0:
                    pts = vertical_tail[:-1] + pts
                else:
                    pts = vertical_tail + pts
                vertical_tail_count += 1

            if len(pts) >= 2:
                routes.append({
                    'guid': guid,
                    'points': pts,
                    'meta': {
                        'eq_tag': r['EQUIPMENT_TAG'],
                        'utility': r['SOURCE_UTILITY'],
                        'utility_group': r['UTILITY_GROUP'],
                        'size': r['SOURCE_SIZE']
                    }
                })

    print(f"Loaded {len(routes)} valid route polylines "
          f"({vertical_tail_count} include a CSF-entry vertical tail from Start Stub).")
    return routes


def analyze_patterns(conn, dry_run=False, image_out=None) -> list[dict]:
    """
    그룹배관(다발) 탐지 파이프라인 전체를 실행하는 메인 함수.
    (1) 전체 경로 Middle Trunk 로드 -> (2) 경로별 특징 추출 -> (3) 장비+유틸리티 파티션 분할 ->
    (4) 파티션별 "세그먼트 레벨 평행 스캔"으로 다발 구간 탐지 및 등간격/오프셋축 계산 ->
    (5) dry_run이 아니면 DB 저장 -> (6) image_out 지정 시 3D PNG 이미지 저장, 순서로 진행된다.
    반환값은 탐지된 모든 다발(bundle dict) 리스트.
    """
    # 1. DB에서 전체 경로의 Middle Trunk 폴리라인을 일괄 로드
    all_routes = load_route_data_bulk(conn, None)

    # 2. 경로별 특징 추출 (결과를 딕셔너리로 수집하여 GUID 매핑 지원)
    print("\nExtracting features for all paths...")
    processed_routes = {}
    for r in all_routes:
        feat = extract_pipe_feature(r['guid'], r['points'], r['meta'])
        if feat:
            processed_routes[r['guid']] = feat
    print(f"Features extracted for {len(processed_routes)} paths.")
    
    # 3. Partition by (EQUIPMENT_TAG, UTILITY_GROUP, SOURCE_UTILITY)
    partitions = defaultdict(list)
    for feat in processed_routes.values():
        key = (feat['eq_tag'], feat['utility_group'], feat['utility'])
        partitions[key].append(feat)
        
    detected_bundles = []
    
    # 4. 각 파티션(장비+유틸리티그룹+유틸리티)을 세그먼트 레벨 평행 스캔으로 처리
    for key, partition in partitions.items():
        if len(partition) < 2:
            continue
            
        eq_tag, util_gp, util = key
        print(f"\nAnalyzing Partition | Eq: '{eq_tag}' | Group: '{util_gp}' | Util: '{util}' with {len(partition)} paths...")
        
        # 모든 배관의 개별 ortho 세그먼트에 assigned 초기상태 할당
        for r in partition:
            for s in r['ortho_segs']:
                s['assigned'] = False
                
        def get_unassigned_len(route):
            return sum(s['len'] for s in route['ortho_segs'] if not s.get('assigned', False))
            
        while True:
            # 아직 미할당 세그먼트가 남아있는 배관 목록 필터링
            active_routes = [r for r in partition if get_unassigned_len(r) > 0]
            if len(active_routes) < 2:
                break
                
            # 정렬: 미할당 세그먼트 총 길이가 가장 길고, 꺾임이 적은 것 우선 (Base Route 지정)
            active_routes.sort(key=lambda r: (get_unassigned_len(r), -len(r['ortho_segs'])), reverse=True)
            base_route = active_routes[0]
            
            # 기준경로(Base Route)의 미할당 세그먼트만 스캔 대상으로 삼음
            base_segs = [s for s in base_route['ortho_segs'] if not s.get('assigned', False)]
            if not base_segs:
                # 루프 방어선: 세그먼트가 남지 않았다면 제외 마크 후 건너뜀
                for s in base_route['ortho_segs']:
                    s['assigned'] = True
                continue
                
            seg_members = []
            for idx, base_seg in enumerate(base_segs):
                members = {base_route['guid']: (0.0, base_seg['len'])}
                for other in active_routes:
                    if other['guid'] == base_route['guid']:
                        continue
                    best_pitch = None
                    total_overlap = 0.0
                    for o_seg in other['ortho_segs']:
                        if o_seg.get('assigned', False):
                            continue
                        pitch, overlap = check_parallel_overlap(base_seg, o_seg)
                        if pitch is not None:
                            total_overlap += overlap
                            if best_pitch is None or pitch < best_pitch:
                                best_pitch = pitch
                                
                    if best_pitch is not None:
                        members[other['guid']] = (best_pitch, total_overlap)
                seg_members.append({
                    'idx': idx,
                    'base_seg': base_seg,
                    'members': members
                })
                
            # 매칭 멤버가 2개 이상인 연속 세그먼트들을 하나의 구간(Section)으로 병합.
            # 갭 허용(Gap-Tolerant) 방식: 이미 구간에 속한 멤버가 특정 세그먼트에서 일시적으로
            # 매칭되지 않아도(예: 각 배관이 서로 다른 위치에서 개별적으로 꺾이는 "계단식" 엘보
            # 전환 구간) 그 멤버의 누적 미스매칭 길이가 SECTION_GAP_TOLERANCE_MM(300mm) 이하이면
            # 구간에서 이탈시키지 않고 "브릿지"하여 이어지는 세그먼트에서 다시 매칭되면 계속 같은
            # 구간으로 유지한다. 단, 이 세그먼트에서 base_route 외에 실제로 매칭되는 멤버가
            # 하나도 없으면(순수 공백 구간, 예: 굽힘부의 짧은 비축정렬(D) 연결 세그먼트) 그
            # 세그먼트 자체는 구간 데이터(segs)에 포함하지 않는다 — 그렇지 않으면 서로 무관한
            # 두 구간이 사이의 잡음 세그먼트를 매개로 하나의 거대한 구간으로 잘못 합쳐진다.
            # 갭이 허용치를 넘으면 그 멤버만 구간에서 영구 이탈한다(다른 멤버가 2개 이상 남아있으면
            # 구간 자체는 유지). 실제 등간격 여부(IS_EQUAL_SPACING)는 이후 compute_offset_regularity()가
            # 최종 멤버 구성 전체에 대해 별도로 재계산하므로, 브릿지 구간에 일시적 핏치 변동이 있어도
            # 등간격 판정 자체가 왜곡되지는 않는다.
            sections = []
            current_sec = None
            for sm in seg_members:
                valid_members = set(sm['members'].keys())
                seg_len = sm['base_seg']['len']
                non_base_active = valid_members - {base_route['guid']}

                if current_sec is None:
                    if len(valid_members) >= 2:
                        current_sec = {
                            'segs': [sm],
                            'member_guids': valid_members,
                            'gap_len': {g: 0.0 for g in valid_members},
                        }
                    continue

                if not non_base_active:
                    # 순수 공백 세그먼트: segs에는 추가하지 않고 gap_len만 누적한다.
                    # member_guids는 여기서 절대 축소하지 않는다 — 이미 확정된(segs에 실제로
                    # 반영된) 멤버 구성은 이후 공백 구간에서의 유예 만료로 소급 축소되면 안 되며,
                    # (real 분기에서 missing 재판정 시 gap_len이 이미 허용치를 넘어있으면 자연히
                    # survivors에서 제외되므로 여기서 미리 제외할 필요가 없다.)
                    still_alive = False
                    for g in current_sec['member_guids']:
                        if g == base_route['guid']:
                            continue
                        current_sec['gap_len'][g] = current_sec['gap_len'].get(g, 0.0) + seg_len
                        if current_sec['gap_len'][g] <= SECTION_GAP_TOLERANCE_MM:
                            still_alive = True
                    if not still_alive:
                        sections.append(current_sec)
                        current_sec = None
                    continue

                present = current_sec['member_guids'] & valid_members
                missing = current_sec['member_guids'] - valid_members

                survivors = set(present)
                for g in missing:
                    accrued = current_sec['gap_len'].get(g, 0.0) + seg_len
                    if accrued <= SECTION_GAP_TOLERANCE_MM:
                        survivors.add(g)
                        current_sec['gap_len'][g] = accrued
                    # else: 누적 갭이 허용치 초과 -> 해당 멤버는 이 구간에서 영구 이탈

                if len(survivors) >= 2:
                    for g in present:
                        current_sec['gap_len'][g] = 0.0
                    current_sec['segs'].append(sm)
                    current_sec['member_guids'] = survivors
                else:
                    sections.append(current_sec)
                    current_sec = {
                        'segs': [sm],
                        'member_guids': valid_members,
                        'gap_len': {g: 0.0 for g in valid_members},
                    } if len(valid_members) >= 2 else None
            if current_sec:
                sections.append(current_sec)
                
            valid_sections = []
            for sec in sections:
                if len(sec['member_guids']) < 2 or not sec['segs']:
                    continue  # 안전장치: 갭 유예 만료 등으로 멤버가 1개 이하로 줄어든 구간은 제외
                total_len = sum(s['base_seg']['len'] for s in sec['segs'])
                if total_len >= 500.0:
                    valid_sections.append(sec)
                    
            if not valid_sections:
                # 유효 구간이 없으면 루프 탈출을 방기하기 위해 base_segs를 assigned 처리
                for base_seg in base_segs:
                    base_seg['assigned'] = True
                continue
                
            for sec in valid_sections:
                m_guids = sorted(list(sec['member_guids']))
                
                # 구간(Section)의 바운딩박스 산출 — 각 스텝(sm)마다 멤버들의 실제 세그먼트 좌표를 모아 min/max 계산
                section_bounds = []
                for sm in sec['segs']:
                    b_seg = sm['base_seg']
                    t = b_seg['dir']
                    
                    g_pts = []
                    for m_guid in m_guids:
                        if m_guid == base_route['guid']:
                            g_pts.extend([b_seg['from'], b_seg['to']])
                        else:
                            other_r = next(r for r in partition if r['guid'] == m_guid)
                            for o_seg in other_r['ortho_segs']:
                                pitch, overlap = check_parallel_overlap(b_seg, o_seg)
                                if pitch is not None:
                                    g_pts.extend([o_seg['from'], o_seg['to']])
                                    
                    if not g_pts:
                        g_pts = [b_seg['from'], b_seg['to']]
                        
                    xs = [p[0] for p in g_pts]
                    ys = [p[1] for p in g_pts]
                    zs = [p[2] for p in g_pts]
                    
                    section_bounds.append({
                        'type': t,
                        'min': [min(xs), min(ys), min(zs)],
                        'max': [max(xs), max(ys), max(zs)]
                    })
                    
                # PATTERN_SEQ: 구간을 구성하는 기준경로 세그먼트들의 진행축(X/Y/Z) 시퀀스를
                # 연속 중복 제거(dedup)한 대표 패턴 문자열 (예: "XYZ"). 굽힘 횟수(rep_bends)는
                # 이 시퀀스 길이-1로 근사한다.
                pattern_seq = "".join(s['base_seg']['dir'] for s in sec['segs'])
                dedup_pattern = ""
                for char in pattern_seq:
                    if not dedup_pattern or dedup_pattern[-1] != char:
                        dedup_pattern += char

                rep_bends = len(dedup_pattern) - 1 if len(dedup_pattern) > 0 else 0

                # TRUNK_Z: 구간 내 수평(X/Y) 세그먼트들의 Z 고도 중앙값 (공용 랙 고도 대표값)
                z_coords = []
                for sm in sec['segs']:
                    if sm['base_seg']['dir'] in ('X', 'Y'):
                        z_coords.append((sm['base_seg']['from'][2] + sm['base_seg']['to'][2]) / 2.0)
                trunk_z = float(get_median(z_coords)) if z_coords else float(base_route['centroid'][2])

                # PITCH_MM: 기준경로 대비 다른 멤버들의 피치(수직 이격거리) 중앙값
                pitches = []
                for sm in sec['segs']:
                    for m_guid, (pitch, overlap) in sm['members'].items():
                        if m_guid != base_route['guid'] and m_guid in m_guids:
                            pitches.append(pitch)
                pitch_mm = float(get_median(pitches)) if pitches else 0.0

                # TRUNK_XY_SPREAD: 각 스텝에서 기준경로 대비 멤버들의 피치(offset) 최대-최소 폭 중 가장 큰 값
                # (다발 전체의 최대 수평/수직 벌어짐 폭)
                spreads = []
                for sm in sec['segs']:
                    offsets = []
                    for m_guid in m_guids:
                        if m_guid == base_route['guid']:
                            offsets.append(0.0)
                        else:
                            pitch, overlap = sm['members'].get(m_guid, (0.0, 0.0))
                            offsets.append(pitch)
                    spreads.append(max(offsets) - min(offsets))
                trunk_xy_spread = float(max(spreads)) if spreads else 0.0

                # PITCH_CV / IS_EQUAL_SPACING / OFFSET_AXIS: (신규) 등간격 여부 및 수평/수직 오프셋축 분류
                pitch_cv, is_equal_spacing, offset_axis = compute_offset_regularity(sec, base_route, partition, m_guids)

                # AVG_SIMILARITY: 알려진 제한사항 — compute_similarity()가 실제로 연결되어 있지 않아 항상 0.95로 고정 저장됨
                avg_sim = 0.95
                rep_feat = base_route['seg_units']

                # GROUP_ID: (장비, 유틸리티그룹, 유틸리티, 멤버 GUID 목록, 시작 세그먼트 인덱스)를 조합한 안정적 해시
                group_id = stable_id(eq_tag, util_gp, util, ",".join(m_guids), str(sec['segs'][0]['idx']))
                
                geom_wkt = bundle_parallel_segments_to_wkt(sec, base_route, partition, m_guids)
                trunk_wkt = generate_trunk_centerline_wkt(section_bounds)
                trunk_len = float(sum(
                    (sec['max'][0] - sec['min'][0]) if sec['type'] == 'X' else (
                        (sec['max'][1] - sec['min'][1]) if sec['type'] == 'Y' else (sec['max'][2] - sec['min'][2])
                    ) for sec in section_bounds
                ))
                
                bundle = {
                    'GROUP_ID': group_id,
                    'EQUIPMENT_TAG': eq_tag,
                    'UTILITY_GROUP': util_gp,
                    'UTILITY': util,
                    'N_MEMBERS': len(m_guids),
                    'AVG_SIMILARITY': avg_sim,
                    'TRUNK_Z': trunk_z,
                    'TRUNK_XY_SPREAD': trunk_xy_spread,
                    'PITCH_MM': pitch_mm,
                    'PITCH_CV': pitch_cv,
                    'IS_EQUAL_SPACING': is_equal_spacing,
                    'OFFSET_AXIS': offset_axis,
                    'N_ORTHO_BENDS': rep_bends,
                    'MEMBER_GUIDS': m_guids,
                    'PATTERN_SEQ': dedup_pattern,
                    'SECTION_BOUNDS': section_bounds,
                    'FEAT': rep_feat,
                    'GEOM_WKT': geom_wkt,
                    'TRUNK_WKT': trunk_wkt,
                    'TRUNK_LEN': trunk_len
                }
                detected_bundles.append(bundle)
                print(f"  -> Detected Parallel Bundle: ID={group_id[:8]}... Pattern={dedup_pattern}, Members={len(m_guids)}, Z={trunk_z:,.1f}, Pitch={pitch_mm:,.1f} (CV={pitch_cv:.3f}, {'REGULAR' if is_equal_spacing else 'IRREGULAR'}), Offset={offset_axis}, Spread={trunk_xy_spread:,.1f}, Bends={rep_bends}")
                
            # 유효 번들로 사용된 세그먼트들 assigned 처리하여 소거
            for sec in valid_sections:
                for sm in sec['segs']:
                    sm['base_seg']['assigned'] = True
                    b_seg = sm['base_seg']
                    
                    for m_guid in sec['member_guids']:
                        if m_guid == base_route['guid']:
                            continue
                        other_r = next(r for r in partition if r['guid'] == m_guid)
                        for o_seg in other_r['ortho_segs']:
                            if o_seg.get('assigned', False):
                                continue
                            pitch, overlap = check_parallel_overlap(b_seg, o_seg)
                            if pitch is not None:
                                o_seg['assigned'] = True
            
    print(f"\nExtraction completed. Total parallel piping groups detected: {len(detected_bundles)}")
    
    if not dry_run:
        save_bundle_patterns(conn, detected_bundles)
        
    # 이미지 파일 저장 옵션 처리 (3D Plotly 렌더링 캡처)
    if image_out:
        save_bundle_images(detected_bundles, processed_routes, image_out)
        
    return detected_bundles


def save_bundle_patterns(conn, bundles: list[dict]) -> None:
    """
    탐지된 그룹배관 다발 목록을 TB_ROUTE_GROUP_PATTERN에 저장한다.
    pgvector 확장 및 FEAT 컬럼 존재 여부를 확인해 vector 컬럼 포함 여부를 결정하고,
    기존 레코드를 DELETE로 전량 삭제한 뒤 execute_batch()로 일괄 INSERT한다.
    (주의: 매 실행마다 선행 DELETE로 이전 실행 결과와의 충돌은 없지만, GROUP_ID는
    stable_id(eq_tag, util_gp, util, member_guids, 첫세그먼트idx)의 해시값이라
    "동일 배치 내에서" 서로 다른 두 다발이 같은 GROUP_ID를 만들어내면 ON CONFLICT DO UPDATE가
    실제로 발동해 한쪽이 다른 쪽을 덮어써 버린다. 실측 사례: 358개 탐지 시 저장 후 count(*)=356
    (2건 병합/유실). 근본 원인(동일 조합의 다발이 서로 다른 섹션 스캔에서 중복 산출되는지,
    아니면 해시 충돌인지)은 아직 조사되지 않음 — 추후 GROUP_ID에 섹션 경계(SECTION_BOUNDS) 등을
    더 포함시켜 특이성을 높이거나, 저장 전 중복 카운트를 로그로 남기는 개선이 필요하다.)
    """
    if not bundles:
        print("No bundles to save.")
        return
        
    has_vector = pgvector_installed(conn) and table_exists(conn, "TB_ROUTE_GROUP_PATTERN") and tool_config._load_config(None).get("db", {}).get("use_vector", True)
    if has_vector:
        with conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='TB_ROUTE_GROUP_PATTERN' AND column_name='FEAT'")
            has_vector = cur.fetchone() is not None
            
    cols = [
        "GROUP_ID", "EQUIPMENT_TAG", "UTILITY_GROUP", "UTILITY", "N_MEMBERS", "AVG_SIMILARITY",
        "TRUNK_Z", "TRUNK_XY_SPREAD", "PITCH_MM", "PITCH_CV", "IS_EQUAL_SPACING", "OFFSET_AXIS",
        "N_ORTHO_BENDS", "MEMBER_GUIDS",
        "PATTERN_SEQ", "SECTION_BOUNDS", "FEAT_JSON", "GEOM_3D", "TRUNK_GEOM_3D", "TRUNK_LEN"
    ]
    if has_vector:
        cols.append("FEAT")
        
    placeholders = []
    for c in cols:
        if c in ("MEMBER_GUIDS", "SECTION_BOUNDS", "FEAT_JSON"):
            placeholders.append("%s::jsonb")
        elif c == "FEAT":
            placeholders.append("%s::vector")
        elif c in ("GEOM_3D", "TRUNK_GEOM_3D"):
            placeholders.append("ST_GeomFromText(%s, 0)")
        else:
            placeholders.append("%s")
            
    sql = f"""
        INSERT INTO "TB_ROUTE_GROUP_PATTERN" ({", ".join(f'"{c}"' for c in cols)})
        VALUES ({", ".join(placeholders)})
        ON CONFLICT ("GROUP_ID") DO UPDATE SET
        {", ".join(f'"{c}" = EXCLUDED."{c}"' for c in cols if c != "GROUP_ID")},
        "CREATED_AT" = now()
    """
    
    rows = []
    for b in bundles:
        geom_wkt = b.get('GEOM_WKT')
        trunk_wkt = b.get('TRUNK_WKT')
        row = [
            b['GROUP_ID'], b['EQUIPMENT_TAG'], b['UTILITY_GROUP'], b['UTILITY'], b['N_MEMBERS'], b['AVG_SIMILARITY'],
            b['TRUNK_Z'], b['TRUNK_XY_SPREAD'], b['PITCH_MM'], b['PITCH_CV'], b['IS_EQUAL_SPACING'], b['OFFSET_AXIS'],
            b['N_ORTHO_BENDS'], json.dumps(b['MEMBER_GUIDS']),
            b['PATTERN_SEQ'], json.dumps(b['SECTION_BOUNDS']), json.dumps(b['FEAT']), geom_wkt, trunk_wkt,
            b['TRUNK_LEN']
        ]
        if has_vector:
            vec_literal = "[" + ",".join(f"{float(v):.9g}" for v in b['FEAT']) + "]"
            row.append(vec_literal)
        rows.append(row)
        
    with conn.cursor() as cur:
        cur.execute('DELETE FROM "TB_ROUTE_GROUP_PATTERN"')
        print("Cleared previous records in TB_ROUTE_GROUP_PATTERN.")
        
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=200)
    conn.commit()
    print(f"Successfully saved {len(bundles)} group patterns to database (vector extension={has_vector}).")


def save_bundle_images(bundles: list[dict], processed_routes: dict, output_dir: str, max_images: int = 20) -> None:
    """
    추출된 그룹배관 패턴(SECTION_BOUNDS 박스 및 멤버 배관선)을 Plotly 3D 그래프로 구성하고,
    kaleido 엔진을 사용하여 백그라운드에서 PNG 정적 이미지로 저장합니다.
    (기본 최대 저장 개수: 20개)
    """
    if not bundles:
        print("No bundles to render images.")
        return
        
    try:
        import plotly.graph_objects as go
    except ImportError:
        print("[warn] Plotly is not installed. Skipping image export.")
        return
        
    try:
        import kaleido
    except ImportError:
        print("[warn] 'kaleido' package is not installed. Please run 'pip install kaleido' to export static images.")
        print("[warn] Skipping image export.")
        return

    print(f"Saving 3D rendering images for up to {max_images} bundles to: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)
    
    # 너무 많은 이미지 생성은 리소스를 과하게 소모하므로 기본 20개로 제한합니다.
    for idx, b in enumerate(bundles):
        if idx >= max_images:
            print(f"  Reached max_images limit ({max_images}). Skipping the remaining {len(bundles) - max_images} bundles.")
            break
        fig = go.Figure()
        
        # 1. 멤버 배관선(3D Polyline) 드로잉
        for m_guid in b['MEMBER_GUIDS']:
            feat = processed_routes.get(m_guid)
            if not feat:
                continue
            pts = feat['points']
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            zs = [p[2] for p in pts]
            
            fig.add_trace(go.Scatter3d(
                x=xs, y=ys, z=zs,
                mode='lines+markers',
                marker=dict(size=3),
                line=dict(width=4),
                name=f"Pipe_{m_guid[:8]}"
            ))
            
        # 2. SECTION_BOUNDS 박스(AABB) 드로잉
        for s_idx, sec in enumerate(b.get('SECTION_BOUNDS', [])):
            min_pt, max_pt = sec['min'], sec['max']
            box_lines_x = []
            box_lines_y = []
            box_lines_z = []
            
            # 3D 박스 8개 정점 좌표 정의
            v = [
                (min_pt[0], min_pt[1], min_pt[2]),
                (max_pt[0], min_pt[1], min_pt[2]),
                (max_pt[0], max_pt[1], min_pt[2]),
                (min_pt[0], max_pt[1], min_pt[2]),
                (min_pt[0], min_pt[1], max_pt[2]),
                (max_pt[0], min_pt[1], max_pt[2]),
                (max_pt[0], max_pt[1], max_pt[2]),
                (min_pt[0], max_pt[1], max_pt[2])
            ]
            
            # 12개 모서리선 매핑
            edges = [
                (0,1), (1,2), (2,3), (3,0),
                (4,5), (5,6), (6,7), (7,4),
                (0,4), (1,5), (2,6), (3,7)
            ]
            
            for start, end in edges:
                box_lines_x.extend([v[start][0], v[end][0], None])
                box_lines_y.extend([v[start][1], v[end][1], None])
                box_lines_z.extend([v[start][2], v[end][2], None])
                
            fig.add_trace(go.Scatter3d(
                x=box_lines_x, y=box_lines_y, z=box_lines_z,
                mode='lines',
                line=dict(color='rgba(255, 0, 0, 0.6)', width=2),
                name=f"Box_{s_idx}_{sec['type']}"
            ))
            
        # 3. 레이아웃 튜닝 (1:1:1 종횡비 설정)
        fig.update_layout(
            title=f"Parallel Piping Group [ID: {b['GROUP_ID'][:8]}]<br>Eq: {b['EQUIPMENT_TAG']} | Utility: {b['UTILITY']}",
            scene=dict(
                xaxis_title="X (mm)",
                yaxis_title="Y (mm)",
                zaxis_title="Z (mm)",
                aspectmode="data"
            ),
            width=1024,
            height=768,
            showlegend=True
        )
        
        # 4. 이미지 캡처 파일 저장
        img_filename = f"bundle_{b['GROUP_ID'][:8]}.png"
        img_path = os.path.join(output_dir, img_filename)
        try:
            fig.write_image(img_path, engine="kaleido")
            if (idx + 1) % 50 == 0 or (idx + 1) == len(bundles):
                print(f"  Exported {idx + 1}/{len(bundles)} images...")
        except Exception as ex:
            print(f"[error] Failed to save image {img_filename}: {ex}")
            break


def main() -> int:
    """CLI 진입점: create-schema / extract / run-all 서브커맨드를 파싱해 해당 함수로 위임한다."""
    parser = argparse.ArgumentParser(description="Group Piping Pattern Extractor")
    tool_config.add_common_args(parser)
    
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    subparsers.add_parser("create-schema", help="Create pattern table schema")
    
    extract_parser = subparsers.add_parser("extract", help="Extract piping group patterns")
    extract_parser.add_argument("--dry-run", action="store_true", help="Print stats without saving to DB")
    extract_parser.add_argument("--image-out", default=None, help="Directory to save group pattern 3D images (PNG)")
    
    run_all_parser = subparsers.add_parser("run-all", help="Create schema and extract patterns")
    run_all_parser.add_argument("--dry-run", action="store_true", help="Print stats without saving to DB")
    run_all_parser.add_argument("--image-out", default=None, help="Directory to save group pattern 3D images (PNG)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
        
    runtime = tool_config.resolve_runtime(args)
    tool_config.print_runtime(runtime)
    
    conn = open_connection(runtime.conninfo)
    
    # args 객체에 image_out 속성이 정의되어 있는지 확인 (create-schema 커맨드는 속성이 없을 수 있음)
    image_out = getattr(args, "image_out", None)
    
    try:
        if args.command == "create-schema":
            create_schema(conn)
        elif args.command == "extract":
            analyze_patterns(conn, dry_run=args.dry_run, image_out=image_out)
        elif args.command == "run-all":
            create_schema(conn)
            analyze_patterns(conn, dry_run=args.dry_run, image_out=image_out)
    finally:
        conn.close()
        
    return 0


if __name__ == "__main__":
    sys.exit(main())
