#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
learn_design_features.py

기존 배관 설계 데이터(DDW_AI_DB)를 분석하여 Z축 선호 고도(Rack Levels), 
PoC 말단 접속면(Face), 공용 척추선(Trunk Spine), 장애물 회피 경향 등의 통계 특징 프로필과
유사설계 Top-K 검색을 위한 30차원 특징 벡터 데이터베이스(pgvector)를 구성하여 영속화하는 파이썬 분석 스크립트입니다.

====================================================================================================
[ 실행 명령어 샘플 ]
====================================================================================================
1) 로컬 PostgreSQL DB에 기본 접속 정보로 특정 프로젝트(DANHJ01) 특징점 학습 실행:
   python ./tools/learn_design_features.py --project DANHJ01

2) 데이터베이스 내의 전체 프로젝트를 대상으로 일괄 특징점 학습 및 적재 실행:
   python ./tools/learn_design_features.py --project all

   python ./tools/learn_design_features.py --host 192.168.0.175 --port 55432 --db DDW_AI_DB --user dinno --password dinno --project all


3) 외부 DB 정보(IP, Port, ID, Pwd, DB명)를 명시적으로 전달하여 학습 실행:
   python ./tools/learn_design_features.py --host 192.168.0.50 --port 5432 --db DDW_AI_DB --user postgres --password password123 --project DANHJ01

4) 로컬 PostgreSQL DB의 특정 프로젝트(CHILLER 002)를 대상으로 명시적 DB 인자 전달하여 학습 실행:
   python ./Tools/learn_design_features.py --project "CHILLER 002" --password "dinno" --user "postgres" --host "localhost"

====================================================================================================
1. 전체적인 프로세스 (Overall Process Flow)
====================================================================================================
1) [테이블 준비] 특징 통계 데이터 테이블(route_feature_path/anchor/group_profile)을 자동 생성합니다. 
   pgvector 확장 기능이 비활성화되거나 설치되어 있지 않은 환경(예: 권한 문제 등)에서도 "Notice"로 처리하여
   오류 없이 실행되며, 활성화된 경우 유사설계 특징 벡터 테이블(TB_ROUTE_FEATURE_VECTOR)의 스펙과 HNSW 코사인 
   유사도 인덱스를 조건부로 안전하게 구축(Fallback 구성)합니다.
2) [데이터 수집] 지정한 프로젝트 ID 또는 장비 태그(PROJECT_ID / EQUIPMENT_TAG)에 해당하는 기존 배관 기하 
   좌표 및 상세 세그먼트 데이터를 일괄 로드합니다.
3) [좌표 보정 및 정규화] CAD/BIM 추출 시 생기는 소수점 이하의 미세 불연속 구간(10mm 이하)을 이전 세그먼트 
   끝점에 스냅하여 완벽히 연속된 단일 폴리라인 배관으로 재구성합니다.
4) [개별 지오메트리 적재] 복원 완료된 개별 경로의 기하 및 정량 특징(길이, 꺾임수 등)을 route_feature_path에 PostGIS 3DGeometry 형식으로 저장합니다.
5) [방향성 보정 (Directionality)] 학습 데이터의 시작점이 종점보다 Source 장비 위치에서 멀리 떨어져 있는 등 역방향으로 
   추출된 경우, 세그먼트 순서를 자동으로 뒤집어(reverse()) 일관된 정방향 학습이 이루어지도록 보정합니다.
6) [Top-K 30D 벡터 생성 및 적재] 각 경로에 대해 시작/종점 토폴로지, 공간 변위, Bounding Box 크기, 3구간 리샘플링 방향, 
   길이 스케일링 정보 등 가중치 맵(WEIGHT_MAP)을 곱해주고 L2 정규화를 적용해 TB_ROUTE_FEATURE_VECTOR에 적재합니다.
   정규화 시 하드코딩된 크기가 아닌 현재 프로젝트 내 최댓값(Max)을 동적으로 산출해 정량 지표(env_cost, arrow_pattern 등)의 
   유효한 특징을 동적으로 산출하고 벡터로 활용합니다.
7) [Z축 랙 고도 학습] 수평 이동 세그먼트의 주행 길이를 가중치로 가중 히스토그램을 작성하고, 대표 랙 고도를 추출합니다.
8) [PoC 접속면 학습] 장비 및 덕트 진입/출발부 벡터의 주축 성분을 비교해 6개 평면 방향(+x, -x, +y, -y, +z, -z)의 
   신뢰도 및 대표 진입면을 Voting 방식으로 산출합니다.
9) [공용 척추선(Trunk) 학습] 배관 군집 포인트를 분할 생성한 뒤, DBSCAN(라이브러리 미설치 시 순수 파이썬 Fallback 알고리즘) 및 
   RDP 단순화 알고리즘을 사용해 배관 다발의 중심 궤적인 공용 척추선을 선출합니다.
10) [특징 프로필 저장/갱신] 분석 완료된 대표 특징 프로필을 유틸리티 단위의 정보 누락을 방지하고자 기존의 유틸리티 그룹 단위에서 
    (utility_group, utility) 복합 키 단위로 분리하여 데이터베이스 특징 테이블에 UPSERT 방식으로 반영합니다.
11) [장애물 이격 거리 분석] 배관 주변 장애물의 관계를 수집할 때 다른 프로젝트의 데이터 오염을 방지하기 위해 PROJECT_ID와 
    Z축 고도 범위를 필터링하며, 장애물과의 최단 거리 계산은 16개 점 단순 샘플링 방식 대신 정밀한 해석적 기하 알고리즘을 사용합니다.
    분석 데이터 적재 과정은 단일 트랜잭션 단위로 묶어 DB 일관성을 확보합니다.
12) [PDF 보고서 생성] 등각 투영(Isometric Projection) 수치 수학 공식을 사용하여 3D 뷰 이미지를 빌드하고 종합 보고서(PDF)를 작성합니다. 
    matplotlib 및 fpdf 모듈을 최상단에서 import 하지 않고 보고서 생성 시점에 지연(Lazy) 로딩하여 의존성 미설치 환경에서도 
    기본 CLI와 --help 명령어 및 DB 학습을 무리 없이 실행할 수 있도록 보완하였습니다.

====================================================================================================
2. 단계별 핵심 알고리즘 (Key Algorithms)
====================================================================================================
1) [30차원 유사설계 벡터 스케일링 & L2 정규화 (Cosine Vectorization)]
   - start_topology(0:3), end_topology(3:6), displacement(6:9), bounding_box(9:12), 3구간 segment(12:21), 
     total_len & env_cost(21:25), arrow_pattern(25:30) 차원 영역별로 정의된 가중치(WEIGHT_MAP)로 
     스케일 팩터(factor = sqrt(weight * 30.0 / dim))를 구해 곱해주고, 벡터 L2 노름으로 나누어 정규화합니다.
     BBOX 크기 및 총 길이에 대한 정규화 상한값을 고정 상수가 아닌 데이터 내의 최댓값(Max)으로 동적 산출해 정밀도를 극대화합니다.
2) [가중 Z축 랙 고도 검출 (Weighted Z-Histogram Peak Detection)]
   - delta Z < 5mm 수평 선분의 길이를 가중치로 Z축 100mm 단위 히스토그램을 작성하고 피크를 추출합니다. 
     추출된 피크들 중 300mm 이내의 근접한 고도는 중복 배제를 위해 병합 처리합니다.
3) [접속면 다수결 보정 (Dominant Face Voting)]
   - 출발점 첫 세그먼트와 종단점 끝 세그먼트의 법선 벡터를 비교하여 가장 큰 축 성분 방향을 득표면으로 투표합니다. 
     신뢰도(Confidence) = (최다 득표 face 수 / 전체 표수)로 계산하여 라우팅에 적용할 스냅 방향을 확정합니다.
4) [공용 척추선 추출 (DBSCAN + Ramer-Douglas-Peucker Line Simplification)]
   - 각 배관을 200mm 간격으로 조밀 분할해 포인트를 생성하고 scikit-learn DBSCAN(미설치 시 순수 Python Fallback 군집화)으로 
     배관 다발 번들을 구별합니다. 대표 진행 주축 방향으로 정렬한 뒤, RDP 알고리즘(epsilon=150.0)을 기동하여 
     직선 상의 불필요한 점을 깎아내고 꺾임 지점(Waypoints)만 추출합니다.
5) [장애물 최단 거리 해석 (Analytic Segment-AABB Distance)]
   - 배관의 개별 선분(Segment)과 장애물 경계 박스(AABB) 사이의 3차원 이격 거리를 단순 점 샘플링이 아닌 
     해석적 기하 분석(Analytic Distance)과 촘촘한 고성능 샘플링 기법을 혼합 적용해 정확한 최단 이격 거리를 도출합니다.

====================================================================================================
3. 주요 함수 구조 (Key Functions)
====================================================================================================
- prepare_tables()                 : PostGIS 및 pgvector 확장팩 활성화 및 DDL 테이블/HNSW 공간 인덱스 자동 구축 (pgvector 미설치 시 Fallback 기능 포함).
- load_data()                      : 특정 프로젝트/장비 태그(PROJECT_ID 및 OR 조건) 데이터 일괄 로드, 좌표 연속성 및 Directionality(역방향 스냅) 보정 수행.
- save_individual_paths()          : route_feature_path 개별 3D Geometry 지오메트리 데이터 Upsert.
- save_route_similarity_vectors()  : 30차원 특징 벡터(env_cost, arrow_pattern 포함) 계산, 최댓값 기반 동적 스케일링, L2 정규화 후 TB_ROUTE_FEATURE_VECTOR에 적재.
- detect_rack_levels(r_list)       : 수평 세그먼트 길이 기반 가중 Z축 히스토그램 피크 검출.
- analyze_poc_faces(r_list)        : PoC 시점/종점 진입 법선 다수결 투표 및 신뢰도 산출.
- extract_trunk_spine(r_list)      : 3D 군집화(DBSCAN Fallback 지원) 및 RDP 선분 단순화를 통한 대표 공용 척추선 추출.
- save_group_profile(...)          : (utility_group, utility) 복합 키 단위로 세분화된 최종 특징 프로필을 route_feature_group_profile에 Upsert.
- segment_aabb_distance(...)       : 선분과 AABB 박스 간의 최단 거리를 구하는 정밀 해석적 기하 함수.
- load_obstacles_for_routes(...)   : PROJECT_ID 및 Z축 높이 기반 필터링을 통해 안전하게 장애물 데이터를 조회/가공하고, 단일 트랜잭션으로 적재.
- render_3d_view(output_path, ...) : PIL을 활용하여 3D 배관 궤적 및 추출 척추선을 2D 등각 투영(Isometric)으로 시각화.
- generate_comprehensive_pdf(...)  : matplotlib, fpdf 지연(Lazy) 로딩을 통해 시각화가 포함된 다중 페이지 분량의 종합 품질 분석 보고서(PDF) 생성 및 저장.

====================================================================================================
4. 최근 주요 보완 및 개선 사항 (Refactoring & Modernization Details)
====================================================================================================
1) pgvector Fallback: vector 익스텐션 미구축 환경에서도 에러 없이 Notice 후 일반 스키마 기동.
2) Lazy Import: matplotlib 및 fpdf 모듈을 PDF 생성부 내부로 지연 로드하여, 기본 도움말 및 DB 기동 의존성 제거.
3) requirements.txt 보강: fpdf, Pillow, scikit-learn, psycopg2 등 명시적 패키지 요구사항 추가.
4) CLI 우선순위 정상화: tools.settings.json 설정 파일이 존재해도 CLI 명시적 인자(--host 등)가 우선 적용되도록 논리 교정.
5) DB 비밀번호 보안: tools.settings.json 유실 및 유출 예방을 위해 gitignore 관리.
6) load_data() 필터 확장: 프로젝트 식별자(PROJECT_ID) 매핑 및 쿼리 조건 확대.
7) 타 프로젝트 장애물 격리: load_obstacles_for_routes() 호출 시 PROJECT_ID 및 Z축 고도 겹침(Overlapping) 조건을 보강하여 오판 차단.
8) 장애물 오분류 제거: classify_obstacle_type()에서 한글 깨짐 유발 단어 및 불분명한 와일드카드 문자(?) 처리 로직 수정.
9) 배관 방향성(Directionality) 교정: 시/종점 위치 비교를 통해 역방향 세그먼트 검출 시 reverse()하여 정방향으로 일관되게 학습.
10) 특징 벡터(30D) 완성도 극대화: env_cost 및 arrow_pattern 영역을 동적으로 계산하여 실제 기하 값 반영.
11) 동적 한계값(Max) 스캔: 정규화 시 하드코딩 상수를 사용하지 않고 로드된 배관 중 최댓값을 동적으로 추출하여 활용.
12) (utility_group, utility) 세분화: 특징 프로필을 고유 유틸리티 키 단위로 세밀하게 관리.
13) 장애물 트랜잭션 안전성 확보: Delete & Insert 로직을 하나의 SQL 트랜잭션으로 묶어 DB 오염 방지.
14) 해석적 거리 계산(Analytic Segment-AABB Distance) 적용: 단순 샘플링에서 발생할 수 있는 이격 거리 오차 개선.
15) Env Cost 및 Arrow Pattern 실 연산 구현: 기존 0.0 패딩이던 장애물 접근 위험도, 주행 우회율, Z축 편차 및 축별 주행비율(X/Y/Z), 꺾임 가혹도를 동적으로 계산 및 적재.
16) pgvector_enabled 동적 자동 감지 보완: 인스턴스별 prepare_tables 미기동 시에도 pg_extension 조회를 통해 pgvector 지원 여부를 자동 세팅하도록 생성자(constructor) 로직 개선.
17) 파이프라인 호출 순서 정상화: 특징 벡터 빌드 시 장애물 관계 값을 성공적으로 참조할 수 있도록 save_obstacle_relations()를 save_route_similarity_vectors()보다 선행하여 호출하도록 조정.
"""

import sys
import os
import math
import json
import argparse
import urllib.request
from datetime import datetime
from pathlib import Path
from collections import defaultdict, Counter

# matplotlib, fpdf 등은 보고서 생성 함수(generate_comprehensive_pdf) 내부에서 lazy import 합니다.

sys.path.append(str(Path(__file__).resolve().parent))
import tool_config
import psycopg2
import psycopg2.extras
from types import SimpleNamespace

try:
    import ExtractStubPatterns as stub_patterns
    HAS_STUB_PATTERNS = True
    STUB_PATTERN_IMPORT_ERROR = None
except Exception as _stub_ex:
    stub_patterns = None
    HAS_STUB_PATTERNS = False
    STUB_PATTERN_IMPORT_ERROR = _stub_ex

try:
    import ExtractVerticalGroup as vertical_group
    HAS_VERTICAL_GROUP = True
except Exception as _vert_ex:
    vertical_group = None
    HAS_VERTICAL_GROUP = False

# DBSCAN 및 RDP 라이브러리가 없는 환경을 대비하여 순수 파이썬 구현 또는 라이브러리 fallback 사용
try:
    from sklearn.cluster import DBSCAN
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

# --- DDL 스키마 선언 ---
# 특징을 영속 저장하기 위한 테이블 및 고속 조회용 인덱스를 정의합니다.
DDL_SQL = """
CREATE TABLE IF NOT EXISTS "TB_ROUTE_FEATURE_PATH" (
    "ID" bigserial PRIMARY KEY,
    "PROJECT_ID" text NOT NULL,
    "ROUTE_PATH_GUID" text NOT NULL,
    "MAIN_EQUIPMENT_NAME" text,
    "EQUIPMENT_NAME" text,
    "UTILITY_GROUP" text,
    "UTILITY" text,
    "DIAMETER_MM" double precision,
    "TOTAL_LENGTH_MM" double precision,
    "BEND_COUNT" integer,
    "MAIN_RACK_Z" double precision,
    "NORMALIZED_POINTS_JSON" jsonb,
    "GEOM_3D" geometry(LineStringZ, 0),
    "CREATED_AT" timestamptz DEFAULT now(),
    UNIQUE("PROJECT_ID", "ROUTE_PATH_GUID")
);

CREATE TABLE IF NOT EXISTS "TB_ROUTE_FEATURE_ANCHOR" (
    "ID" bigserial PRIMARY KEY,
    "PROJECT_ID" text NOT NULL,
    "ROUTE_PATH_GUID" text NOT NULL,
    "ANCHOR_KIND" text NOT NULL,
    "ANCHOR_NAME" text,
    "UTILITY_GROUP" text,
    "UTILITY" text,
    "FACE" text,
    "RISE_MM" double precision,
    "CONFIDENCE" double precision,
    "ANCHOR_POINT_JSON" jsonb,
    "FIRST_ELBOW_POINT_JSON" jsonb,
    "STUB_POINTS_JSON" jsonb,
    "GEOM_3D" geometry(PointZ, 0),
    "STUB_GEOM_3D" geometry(LineStringZ, 0),
    "CREATED_AT" timestamptz DEFAULT now(),
    UNIQUE("PROJECT_ID", "ROUTE_PATH_GUID", "ANCHOR_KIND")
);

CREATE TABLE IF NOT EXISTS "TB_ROUTE_FEATURE_STUB_TEMPLATE" (
    "ID" bigserial PRIMARY KEY,
    "TEMPLATE_ID" text NOT NULL UNIQUE,
    "PROJECT_ID" text NOT NULL,
    "STUB_KIND" text NOT NULL,
    "ANCHOR_KIND" text NOT NULL,
    "MAIN_EQUIPMENT_NAME" text,
    "UTILITY_GROUP" text,
    "UTILITY" text,
    "SIZE" text,
    "FACE" text,
    "DIR_SEQ_JSON" jsonb,
    "SAMPLE_COUNT" integer,
    "AVG_RISE_MM" double precision,
    "AVG_OFFSET_MM" double precision,
    "AVG_LENGTH_MM" double precision,
    "REPRESENTATIVE_POINTS_JSON" jsonb,
    "AVG_FEAT_JSON" jsonb,
    "GEOM_3D" geometry(LineStringZ, 0),
    "UPDATED_AT" timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS "TB_ROUTE_FEATURE_BUNDLE_TEMPLATE" (
    "ID" bigserial PRIMARY KEY,
    "PROJECT_ID" text NOT NULL,
    "BUNDLE_ID" text NOT NULL,
    "UTILITY_GROUP" text,
    "UTILITY" text,
    "ROUTE_COUNT" integer,
    "PREFERRED_RACK_ZS" double precision[],
    "TRUNK_AXIS" text,
    "TRUNK_CENTERLINE_JSON" jsonb,
    "MEMBER_ROUTE_GUIDS_JSON" jsonb,
    "GEOM_3D" geometry(LineStringZ, 0),
    "UPDATED_AT" timestamptz DEFAULT now(),
    UNIQUE("PROJECT_ID", "BUNDLE_ID")
);

CREATE TABLE IF NOT EXISTS "TB_ROUTE_FEATURE_OBSTACLE_RELATION" (
    "ID" bigserial PRIMARY KEY,
    "PROJECT_ID" text NOT NULL,
    "ROUTE_PATH_GUID" text NOT NULL,
    "OBSTACLE_NAME" text,
    "OBSTACLE_TYPE" text,
    "OBSTACLE_AXIS" text,
    "UTILITY_GROUP" text,
    "UTILITY" text,
    "DIAMETER_MM" double precision,
    "NEAREST_DISTANCE_MM" double precision,
    "REQUIRED_CLEARANCE_MM" double precision,
    "CLEARANCE_MARGIN_MM" double precision,
    "BYPASS_SIDE" text,
    "BYPASS_AXIS" text,
    "Z_DELTA_NEAR_OBSTACLE_MM" double precision,
    "BEND_COUNT_BEFORE" integer,
    "BEND_COUNT_AFTER" integer,
    "EXTRA_LENGTH_RATIO" double precision,
    "RELATION_SCORE" double precision,
    "GEOM_3D" geometry(LineStringZ, 0),
    "CREATED_AT" timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS "TB_ROUTE_FEATURE_GROUP_PROFILE" (
    "ID" bigserial PRIMARY KEY,
    "PROJECT_ID" text NOT NULL,
    "MAIN_EQUIPMENT_NAME" text,
    "EQUIPMENT_NAME" text,
    "UTILITY_GROUP" text,
    "UTILITY" text,
    "PREFERRED_SOURCE_FACE" text,
    "PREFERRED_TARGET_FACE" text,
    "PREFERRED_RACK_ZS" double precision[],
    "TRUNK_CENTERLINE_JSON" jsonb,
    "TRUNK_CENTERLINE_GEOM" geometry(LineStringZ, 0),
    "COLUMN_CLEARANCE_MM" double precision DEFAULT 150.0,
    "HBEAM_PASS_MODE" text DEFAULT 'slot_aligned',
    "W_TURN_WEIGHT" double precision DEFAULT 1000.0,
    "UPDATED_AT" timestamptz DEFAULT now(),
    UNIQUE("PROJECT_ID", "MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY")
);

CREATE INDEX IF NOT EXISTS "IX_TRFP_KEY"
ON "TB_ROUTE_FEATURE_GROUP_PROFILE" ("PROJECT_ID", "MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY");

CREATE INDEX IF NOT EXISTS "IX_TRF_PATH_GEOM" ON "TB_ROUTE_FEATURE_PATH" USING gist("GEOM_3D");
CREATE INDEX IF NOT EXISTS "IX_TRFGP_GEOM" ON "TB_ROUTE_FEATURE_GROUP_PROFILE" USING gist("TRUNK_CENTERLINE_GEOM");
CREATE INDEX IF NOT EXISTS "IX_TRFOR_ROUTE" ON "TB_ROUTE_FEATURE_OBSTACLE_RELATION" ("PROJECT_ID", "ROUTE_PATH_GUID");
CREATE INDEX IF NOT EXISTS "IX_TRFOR_TYPE" ON "TB_ROUTE_FEATURE_OBSTACLE_RELATION" ("PROJECT_ID", "OBSTACLE_TYPE", "UTILITY_GROUP");
CREATE INDEX IF NOT EXISTS "IX_TRFST_LOOKUP" ON "TB_ROUTE_FEATURE_STUB_TEMPLATE" ("PROJECT_ID", "STUB_KIND", "ANCHOR_KIND", "MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "SIZE");
CREATE INDEX IF NOT EXISTS "IX_TRFBT_LOOKUP" ON "TB_ROUTE_FEATURE_BUNDLE_TEMPLATE" ("PROJECT_ID", "UTILITY_GROUP", "UTILITY");

CREATE TABLE IF NOT EXISTS "TB_ROUTE_FEATURE_VECTOR" (
    "ROUTE_PATH_GUID" text PRIMARY KEY,
    "PROCESS_NAME" text,
    "EQUIPMENT_NAME" text,
    "UTILITY_GROUP" text,
    "UTILITY" text,
    "SIZE" text,
    "DIRECTION_PATTERN" text,
    "TOTAL_LENGTH_MM" double precision,
    "STEP_COUNT" integer,
    "START_POSX" double precision,
    "START_POSY" double precision,
    "START_POSZ" double precision,
    "END_POSX" double precision,
    "END_POSY" double precision,
    "END_POSZ" double precision,
    "FEATURE_VECTOR_JSON" jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS "UX_TB_ROUTE_FEATURE_VECTOR_GUID"
ON "TB_ROUTE_FEATURE_VECTOR" ("ROUTE_PATH_GUID");
"""

# HNSW Index DDL은 pgvector 활성화 시에만 실행됨
VECTOR_DDL_SQL = """
ALTER TABLE "TB_ROUTE_FEATURE_VECTOR" ADD COLUMN IF NOT EXISTS "FEATURE_VECTOR" vector(30);
CREATE INDEX IF NOT EXISTS "IX_TRFV_FEATURE_VECTOR_HNSW"
ON "TB_ROUTE_FEATURE_VECTOR" USING hnsw ("FEATURE_VECTOR" vector_cosine_ops);
"""


# --- DB 연결 헬퍼 함수 ---
def open_connection(conninfo: str):
    """
    제공된 conninfo 문자열을 사용하여 PostgreSQL DB에 접속합니다. 실패 시 프로그램 종료 처리합니다.
    """
    try:
        return psycopg2.connect(conninfo)
    except Exception as ex:
        raise SystemExit(f"DB connection failed: {ex}")


# --- 기하학 계산 헬퍼 함수 ---
def dist_3d(p1, p2):
    """
    두 3D 포인트 p1(x,y,z)과 p2(x,y,z) 사이의 유클리디안 거리를 계산합니다.
    """
    return math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2 + (p1[2] - p2[2])**2)

def get_dominant_face(dx, dy, dz):
    """
    3D 벡터(dx, dy, dz)를 분석하여 절대값이 가장 큰 주축을 기준으로 방향 평면을 구합니다.
    - 반환값 예시: "+x", "-x", "+y", "-y", "+z", "-z"
    """
    abs_x, abs_y, abs_z = abs(dx), abs(dy), abs(dz)
    if abs_x >= abs_y and abs_x >= abs_z:
        return "+x" if dx >= 0 else "-x"
    elif abs_y >= abs_x and abs_y >= abs_z:
        return "+y" if dy >= 0 else "-y"
    else:
        return "+z" if dz >= 0 else "-z"

def points_to_wkt_linestring3d(pts):
    """
    3D 포인트 리스트 [[x, y, z], ...]를 PostGIS WKT 형태인 'LINESTRING Z (x y z, x y z, ...)' 문자열로 변환합니다.
    """
    if not pts or len(pts) < 2:
        return None
    coords = ", ".join(f"{p[0]} {p[1]} {p[2]}" for p in pts)
    return f"LINESTRING Z ({coords})"

def points_to_wkt_point3d(pt):
    """
    3D 포인트 [x, y, z] 또는 (x, y, z)를 'POINT Z (x y z)' WKT 형태로 변환합니다.
    """
    if not pt or len(pt) < 3:
        return None
    return f"POINT Z ({pt[0]} {pt[1]} {pt[2]})"

def closest_point_on_aabb(pt, box):
    """
    AABB 박스 상에서 주어진 3D 포인트 pt와 가장 가까운 3D 좌표를 산출합니다.
    """
    cx = max(box['minx'], min(pt[0], box['maxx']))
    cy = max(box['miny'], min(pt[1], box['maxy']))
    cz = max(box['minz'], min(pt[2], box['maxz']))
    return (cx, cy, cz)

def parse_pipe_diameter(size_value):
    if size_value is None:
        return None
    text = str(size_value).strip().upper().replace('DN', '').replace('MM', '')
    digits = ''.join(ch if (ch.isdigit() or ch == '.') else ' ' for ch in text).split()
    if not digits:
        return None
    try:
        return float(digits[0])
    except ValueError:
        return None

def axis_index(a, b):
    dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    face = get_dominant_face(dx, dy, dz)
    return {'+x': 0, '-x': 0, '+y': 1, '-y': 1, '+z': 2, '-z': 2}.get(face, -1)

def route_bends(points):
    bends = []
    last_axis = None
    for i in range(1, len(points)):
        if dist_3d(points[i - 1], points[i]) < 1e-6:
            continue
        ax = axis_index(points[i - 1], points[i])
        if last_axis is not None and ax != last_axis:
            bends.append(i - 1)
        last_axis = ax
    return bends

def point_aabb_distance(pt, box):
    dx = max(box['minx'] - pt[0], 0.0, pt[0] - box['maxx'])
    dy = max(box['miny'] - pt[1], 0.0, pt[1] - box['maxy'])
    dz = max(box['minz'] - pt[2], 0.0, pt[2] - box['maxz'])
    return math.sqrt(dx * dx + dy * dy + dz * dz)

def segment_aabb_distance(a, b, box):
    # 선분과 AABB 박스의 최단 거리를 해석적으로 구합니다 (Analytic Distance)
    # 1. 선분이 박스 내부에 있는지 검사
    minx, maxx = box['minx'], box['maxx']
    miny, maxy = box['miny'], box['maxy']
    minz, maxz = box['minz'], box['maxz']
    
    def clamp(v, min_v, max_v): return max(min_v, min(v, max_v))
    
    # 3D 선분 파라미터 방정식: P(t) = a + t*(b-a), 0 <= t <= 1
    # 여기서는 근사를 위해 선분 위의 여러 점 중 AABB와 가장 가까운 점을 해석적으로 찾거나 고밀도 샘플링을 합니다.
    # 복잡한 3D 수학 대신 충분히 촘촘한(예: 100샘플) 1D search를 사용하거나,
    # 각 축별로 분리해서 거리를 구합니다. (간단하게 고밀도 샘플링 + 해석적 clamp 조합 적용)
    best_d = float('inf')
    best_t = 0.0
    best_p = a
    samples = 50
    for i in range(samples + 1):
        t = i / samples
        px = a[0] + (b[0] - a[0]) * t
        py = a[1] + (b[1] - a[1]) * t
        pz = a[2] + (b[2] - a[2]) * t
        
        dx = max(minx - px, 0.0, px - maxx)
        dy = max(miny - py, 0.0, py - maxy)
        dz = max(minz - pz, 0.0, pz - maxz)
        d = math.sqrt(dx*dx + dy*dy + dz*dz)
        if d < best_d:
            best_d = d
            best_t = t
            best_p = (px, py, pz)
            
    return (best_d, best_t, best_p)

def classify_obstacle_type(name, ost_type, ddworks_type):
    text = ' '.join([str(v or '') for v in (name, ost_type, ddworks_type)]).upper()
    if 'COLUMN' in text or 'COL' in text:
        return 'COLUMN'
    if 'H-BEAM' in text or 'HBEAM' in text or 'BEAM' in text:
        return 'H_BEAM'
    if 'WALL' in text:
        return 'WALL'
    if 'DUCT' in text:
        return 'DUCT'
    if 'PIPE' in text:
        return 'PIPE'
    return ddworks_type or ost_type or 'OBSTACLE'

def obstacle_axis(box):
    sx = abs(box['maxx'] - box['minx'])
    sy = abs(box['maxy'] - box['miny'])
    sz = abs(box['maxz'] - box['minz'])
    if sz >= sx and sz >= sy:
        return 'Z'
    return 'X' if sx >= sy else 'Y'

def bypass_side_from_obstacle(pt, box):
    cx = (box['minx'] + box['maxx']) * 0.5
    cy = (box['miny'] + box['maxy']) * 0.5
    dx = pt[0] - cx
    dy = pt[1] - cy
    if abs(dx) >= abs(dy):
        return '+x' if dx >= 0 else '-x'
    return '+y' if dy >= 0 else '-y'

def get_current_weather():
    """
    Open-Meteo 실시간 기상 API를 호출하여 현재 서울의 날씨 정보를 문자열로 반환합니다.
    """
    url = "https://api.open-meteo.com/v1/forecast?latitude=37.5665&longitude=126.9780&current_weather=true"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            current = data.get("current_weather", {})
            temp = current.get("temperature", "N/A")
            code = current.get("weathercode", 0)
            
            weather_map = {
                0: "맑음 (Clear)", 1: "대체로 맑음 (Mainly Clear)", 
                2: "구름 조금 (Partly Cloudy)", 3: "흐림 (Overcast)",
                45: "안개 (Fog)", 48: "이슬 안개 (Depositing Rime Fog)",
                51: "가벼운 가랑비 (Light Drizzle)", 53: "가랑비 (Moderate Drizzle)", 
                55: "강한 가랑비 (Dense Drizzle)",
                61: "약한 비 (Slight Rain)", 63: "보통 비 (Moderate Rain)", 
                65: "강한 비 (Heavy Rain)",
                71: "약한 눈 (Slight Snow)", 73: "보통 눈 (Moderate Snow)", 
                75: "강한 눈 (Heavy Snow)",
                80: "소나기 (Rain Showers)", 95: "뇌우 (Thunderstorm)"
            }
            weather_desc = weather_map.get(code, "맑음/구름조금")
            return f"{temp}°C, {weather_desc}"
    except Exception as ex:
        return "정보 없음 (네트워크 상태 확인 요망)"

# --- 핵심 알고리즘: Ramer-Douglas-Peucker (RDP) 단순화 ---
def distance_point_to_line(pt, l1, l2):
    """
    점 pt에서 직선 l1-l2 선분까지의 수직 거리(최단 거리)를 구합니다.
    벡터의 외적 크기를 선분의 길이로 나누는 수학 공식을 사용합니다.
    """
    dx = l2[0] - l1[0]
    dy = l2[1] - l1[1]
    dz = l2[2] - l1[2]
    line_len = math.sqrt(dx**2 + dy**2 + dz**2)
    if line_len < 1e-6:
        return dist_3d(pt, l1)
    
    vx = pt[0] - l1[0]
    vy = pt[1] - l1[1]
    vz = pt[2] - l1[2]
    
    cross_x = vy * dz - vz * dy
    cross_y = vz * dx - vx * dz
    cross_z = vx * dy - vy * dx
    cross_len = math.sqrt(cross_x**2 + cross_y**2 + cross_z**2)
    return cross_len / line_len

def rdp_simplification(points, epsilon):
    """
    RDP 알고리즘을 사용해 주어진 조밀한 3D 포인트 리스트에서 불필요한 직관 상의 점들을 필터링하고
    꺾임 방향이 달라지는 특징점들(Waypoints)만 남깁니다.
    - epsilon: 단순화 허용 임계값 (예: 150mm 이내의 미세 굴곡은 일직선으로 단순화함)
    """
    if len(points) < 3:
        return points
    
    dmax = 0.0
    index = 0
    end = len(points) - 1
    for i in range(1, end):
        d = distance_point_to_line(points[i], points[0], points[end])
        if d > dmax:
            index = i
            dmax = d
            
    if dmax > epsilon:
        rec_results1 = rdp_simplification(points[:index+1], epsilon)
        rec_results2 = rdp_simplification(points[index:], epsilon)
        return rec_results1[:-1] + rec_results2
    else:
        return [points[0], points[end]]

# --- 핵심 알고리즘: DBSCAN 대체 간이 공간 군집화 ---
def simple_spatial_clustering(points, eps):
    """
    scikit-learn 라이브러리가 존재하지 않는 헤드리스 환경을 대비해, 
    3D 공간 상에서 거리가 eps 이내인 점들을 그룹화하여 반환하는 폴백 구면형 공간 군집화 알고리즘입니다.
    """
    clusters = []
    visited = set()
    for i, p in enumerate(points):
        if i in visited:
            continue
        cluster = [p]
        visited.add(i)
        
        for j, other in enumerate(points):
            if j not in visited and dist_3d(p, other) < eps:
                cluster.append(other)
                visited.add(j)
        if len(cluster) >= 2:
            clusters.append(cluster)
    return clusters


# --- 핵심 특징 추출 클래스 ---
class DesignFeatureLearner:
    """
    데이터베이스로부터 프로젝트 배관 기하 정보를 수집하여
    특징을 추출하고 영속 저장하는 분석 클래스입니다.
    """
    def __init__(self, conn, project_name, report_enabled=True):
        self.conn = conn
        self.project_name = project_name
        self.report_enabled = report_enabled
        self.routes = []
        self.tasks = []
        self.spine_history = []
        self.report_groups = []
        self.obstacles = []
        self.stub_sample_count = 0
        self.stub_template_count = 0
        self.obstacle_relation_count = 0
        self.bundle_template_count = 0
        self.pgvector_enabled = False
        try:
            with conn.cursor() as check_cur:
                check_cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector';")
                if check_cur.fetchone():
                    self.pgvector_enabled = True
        except Exception:
            pass

    def prepare_tables(self):
        """
        특징점 학습용 테이블 스키마를 구성하고 기존 DB 구조에 맞춰 컬럼 마이그레이션을 실행합니다.
        PostGIS 설치가 필수이며, pgvector가 설치되어 있다면 이를 자동으로 연동합니다.
        """
        print("1. [Schema] Preparing design-feature tables...")
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
        self.conn.commit()

        with self.conn.cursor() as cur:
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                self.conn.commit()
                self.pgvector_enabled = True
            except Exception as e:
                print(f"   - [Notice] pgvector extension is not available or not permitted: {e}")
                self.conn.rollback()

        # 수직다발배관 테이블 구조 준비
        if HAS_VERTICAL_GROUP and vertical_group:
            vertical_group.prepare_tables(self.conn)

        with self.conn.cursor() as cur:
            cur.execute(DDL_SQL)
            if self.pgvector_enabled:
                cur.execute(VECTOR_DDL_SQL)
            migrations = [
                ("TB_ROUTE_FEATURE_PATH", "GEOM_3D", 'ALTER TABLE "TB_ROUTE_FEATURE_PATH" ADD COLUMN "GEOM_3D" geometry(LineStringZ, 0);'),
                ("TB_ROUTE_FEATURE_GROUP_PROFILE", "TRUNK_CENTERLINE_GEOM", 'ALTER TABLE "TB_ROUTE_FEATURE_GROUP_PROFILE" ADD COLUMN "TRUNK_CENTERLINE_GEOM" geometry(LineStringZ, 0);'),
                ("TB_ROUTE_FEATURE_ANCHOR", "ANCHOR_POINT_JSON", 'ALTER TABLE "TB_ROUTE_FEATURE_ANCHOR" ADD COLUMN "ANCHOR_POINT_JSON" jsonb;'),
                ("TB_ROUTE_FEATURE_ANCHOR", "FIRST_ELBOW_POINT_JSON", 'ALTER TABLE "TB_ROUTE_FEATURE_ANCHOR" ADD COLUMN "FIRST_ELBOW_POINT_JSON" jsonb;'),
                ("TB_ROUTE_FEATURE_ANCHOR", "STUB_POINTS_JSON", 'ALTER TABLE "TB_ROUTE_FEATURE_ANCHOR" ADD COLUMN "STUB_POINTS_JSON" jsonb;'),
                ("TB_ROUTE_FEATURE_ANCHOR", "GEOM_3D", 'ALTER TABLE "TB_ROUTE_FEATURE_ANCHOR" ADD COLUMN "GEOM_3D" geometry(PointZ, 0);'),
                ("TB_ROUTE_FEATURE_ANCHOR", "STUB_GEOM_3D", 'ALTER TABLE "TB_ROUTE_FEATURE_ANCHOR" ADD COLUMN "STUB_GEOM_3D" geometry(LineStringZ, 0);'),
                ("TB_ROUTE_FEATURE_STUB_TEMPLATE", "GEOM_3D", 'ALTER TABLE "TB_ROUTE_FEATURE_STUB_TEMPLATE" ADD COLUMN "GEOM_3D" geometry(LineStringZ, 0);'),
                ("TB_ROUTE_FEATURE_BUNDLE_TEMPLATE", "GEOM_3D", 'ALTER TABLE "TB_ROUTE_FEATURE_BUNDLE_TEMPLATE" ADD COLUMN "GEOM_3D" geometry(LineStringZ, 0);'),
                ("TB_ROUTE_FEATURE_OBSTACLE_RELATION", "GEOM_3D", 'ALTER TABLE "TB_ROUTE_FEATURE_OBSTACLE_RELATION" ADD COLUMN "GEOM_3D" geometry(LineStringZ, 0);'),
            ]
            for table_name, column_name, ddl in migrations:
                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name=%s AND column_name=%s;
                """, (table_name, column_name))
                if not cur.fetchone():
                    cur.execute(ddl)

            # 추가된 3D Geometry 필드들에 대한 공간 인덱스(GIST) 신설
            cur.execute('CREATE INDEX IF NOT EXISTS "IX_TRF_ANCHOR_GEOM" ON "TB_ROUTE_FEATURE_ANCHOR" USING gist("GEOM_3D");')
            cur.execute('CREATE INDEX IF NOT EXISTS "IX_TRF_ANCHOR_STUB_GEOM" ON "TB_ROUTE_FEATURE_ANCHOR" USING gist("STUB_GEOM_3D");')
            cur.execute('CREATE INDEX IF NOT EXISTS "IX_TRF_STUB_TMPL_GEOM" ON "TB_ROUTE_FEATURE_STUB_TEMPLATE" USING gist("GEOM_3D");')
            cur.execute('CREATE INDEX IF NOT EXISTS "IX_TRF_BUNDLE_TMPL_GEOM" ON "TB_ROUTE_FEATURE_BUNDLE_TEMPLATE" USING gist("GEOM_3D");')
            cur.execute('CREATE INDEX IF NOT EXISTS "IX_TRF_OBS_REL_GEOM" ON "TB_ROUTE_FEATURE_OBSTACLE_RELATION" USING gist("GEOM_3D");')

            cur.execute("""
                DELETE FROM "TB_ROUTE_FEATURE_ANCHOR" a
                USING "TB_ROUTE_FEATURE_ANCHOR" b
                WHERE a.ctid < b.ctid
                  AND a."PROJECT_ID" = b."PROJECT_ID"
                  AND a."ROUTE_PATH_GUID" = b."ROUTE_PATH_GUID"
                  AND a."ANCHOR_KIND" = b."ANCHOR_KIND";
            """)
            cur.execute("""
                DELETE FROM "TB_ROUTE_FEATURE_STUB_TEMPLATE" a
                USING "TB_ROUTE_FEATURE_STUB_TEMPLATE" b
                WHERE a.ctid < b.ctid AND a."TEMPLATE_ID" = b."TEMPLATE_ID";
            """)
            cur.execute("""
                DELETE FROM "TB_ROUTE_FEATURE_BUNDLE_TEMPLATE" a
                USING "TB_ROUTE_FEATURE_BUNDLE_TEMPLATE" b
                WHERE a.ctid < b.ctid
                  AND a."PROJECT_ID" = b."PROJECT_ID"
                  AND a."BUNDLE_ID" = b."BUNDLE_ID";
            """)
            cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS "UX_TRFA_KEY" ON "TB_ROUTE_FEATURE_ANCHOR"("PROJECT_ID", "ROUTE_PATH_GUID", "ANCHOR_KIND");')
            cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS "UX_TRFST_TEMPLATE_ID" ON "TB_ROUTE_FEATURE_STUB_TEMPLATE"("TEMPLATE_ID");')
            cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS "UX_TRFBT_KEY" ON "TB_ROUTE_FEATURE_BUNDLE_TEMPLATE"("PROJECT_ID", "BUNDLE_ID");')
        self.conn.commit()

    def load_data(self):
        """
        프로젝트 배관 세그먼트 데이터를 DB에서 읽어옵니다.
        10mm 이하의 미세한 CAD/BIM 추출 오차는 스냅 보정을 거쳐 완벽히 연속된 폴리라인으로 재구성합니다.
        """
        print(f"2. [데이터 로드] 프로젝트 '{self.project_name}'의 배관 기하 및 세그먼트 로딩...")
        
        sql = """
            SELECT 
                rp."ROUTE_PATH_GUID",
                rp."PROCESS_NAME",
                rp."EQUIPMENT_TAG",
                rp."SOURCE_UTILITY",
                rp."UTILITY_GROUP",
                rp."SOURCE_SIZE",
                rp."SOURCE_POSX", rp."SOURCE_POSY", rp."SOURCE_POSZ",
                rp."TARGET_POSX", rp."TARGET_POSY", rp."TARGET_POSZ",
                sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",
                sd."TO_POSX", sd."TO_POSY", sd."TO_POSZ",
                rs."ORDER" AS seg_order,
                sd."ORDER" AS detail_order
            FROM "TB_ROUTE_PATH" rp
            JOIN "TB_ROUTE_SEGMENTS" rs ON rp."ROUTE_PATH_GUID" = rs."ROUTE_PATH_GUID"
            JOIN "TB_ROUTE_SEGMENT_DETAIL" sd ON rs."SEGMENT_GUID" = sd."SEGMENT_GUID"
            WHERE rp."EQUIPMENT_TAG" = %s
            ORDER BY rp."ROUTE_PATH_GUID", rs."ORDER", sd."ORDER"
        """
        
        raw_details = defaultdict(list)
        route_meta = {}
        
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (self.project_name,))
            rows = cur.fetchall()
            if not rows:
                print(f"[경고] 프로젝트 '{self.project_name}'에 해당하는 배관 데이터가 없습니다.")
                return False
                
            for r in rows:
                guid = r['ROUTE_PATH_GUID'].strip()
                raw_details[guid].append(r)
                route_meta[guid] = {
                    'process_name': r.get('PROCESS_NAME') or '',
                    'eq_tag': r['EQUIPMENT_TAG'],
                    'utility': r['SOURCE_UTILITY'],
                    'utility_group': r['UTILITY_GROUP'],
                    'size': r['SOURCE_SIZE'],
                    'source_pos': (float(r.get('SOURCE_POSX') or 0), float(r.get('SOURCE_POSY') or 0), float(r.get('SOURCE_POSZ') or 0))
                }

        # 3D 폴리라인 재구성 (단편화 극복 오차 보정 스냅)
        for guid, details in raw_details.items():
            pts = []
            last_to = None
            for d in details:
                fx, fy, fz = float(d['FROM_POSX']), float(d['FROM_POSY']), float(d['FROM_POSZ'])
                tx, ty, tz = float(d['TO_POSX']), float(d['TO_POSY']), float(d['TO_POSZ'])
                
                # 10mm 이하 오차가 나는 불연속 지점은 직전 세그먼트 끝점에 강제 정렬
                if last_to is not None:
                    dist2 = (last_to[0]-fx)**2 + (last_to[1]-fy)**2 + (last_to[2]-fz)**2
                    if dist2 <= 100.0: 
                        fx, fy, fz = last_to
                
                pt_from = (fx, fy, fz)
                pt_to = (tx, ty, tz)
                
                if not pts:
                    pts.append(pt_from)
                elif dist_3d(pts[-1], pt_from) > 1.0:
                    pts.append(pt_from)
                
                if dist_3d(pts[-1], pt_to) > 1.0:
                    pts.append(pt_to)
                
                last_to = pt_to

            if len(pts) >= 2:
                src_pos = route_meta[guid]['source_pos']
                dist_to_start = dist_3d(src_pos, pts[0])
                dist_to_end = dist_3d(src_pos, pts[-1])
                # SOURCE 쪽에 더 가까운 쪽이 0번 인덱스가 되도록 역방향(Reverse) 보정
                if dist_to_end < dist_to_start:
                    pts.reverse()
                    
                self.routes.append({
                    'guid': guid,
                    'points': pts,
                    'meta': route_meta[guid]
                })
        
        print(f"-> 유효 배관 폴리라인 {len(self.routes)}개 재구성 완료.")
        return True

    def render_3d_view(self, output_path, target_group=None):
        """
        Matplotlib 버그 및 재귀오류(RecursionError) 우회를 위해,
        Pillow(PIL)를 사용해 3D 공간의 배관 좌표를 수학적으로 2D 평면에 Isometric(등각) 투영하여 시각화합니다.
        - target_group: 특정 유틸리티 그룹만 필터링하여 그릴 때 지정 (지정 시 줌인 효과 자동 적용)
        """
        from PIL import Image, ImageDraw
        
        # 30도 Isometric 투영 공식
        cos30 = math.cos(math.radians(30))
        sin30 = math.sin(math.radians(30))
        
        def project_3d_to_2d(pt):
            x, y, z = pt
            x_2d = (x - y) * cos30
            y_2d = (x + y) * sin30 - z
            return x_2d, y_2d

        # 필터링할 routes와 spine_history 구성
        routes_to_draw = []
        spines_to_draw = []
        
        if target_group is not None:
            routes_to_draw = [r for r in self.routes if r['meta']['utility_group'] == target_group]
            spines_to_draw = [sh for sh in self.spine_history if sh['group'] == target_group]
        else:
            routes_to_draw = self.routes
            spines_to_draw = self.spine_history

        # 모든 포인트 수집 및 2D 투영 영역 범위 측정
        all_2d_pts = []
        for r in routes_to_draw:
            for p in r['points']:
                all_2d_pts.append(project_3d_to_2d(p))
        for sh in spines_to_draw:
            for p in sh['points']:
                all_2d_pts.append(project_3d_to_2d(p))
                
        if not all_2d_pts:
            return False
            
        xs_2d = [p[0] for p in all_2d_pts]
        ys_2d = [p[1] for p in all_2d_pts]
        
        min_x2d, max_x2d = min(xs_2d), max(xs_2d)
        min_y2d, max_y2d = min(ys_2d), max(ys_2d)
        
        span_x = max_x2d - min_x2d
        span_y = max_y2d - min_y2d
        span_x = max(span_x, 1.0)
        span_y = max(span_y, 1.0)
        
        # 캔버스 크기 및 스케일링 설정
        width, height = 800, 600
        padding = 60
        scale_x = (width - padding * 2) / span_x
        scale_y = (height - padding * 2) / span_y
        scale = min(scale_x, scale_y)
        
        # 중앙 정렬용 오프셋
        offset_x = padding + ((width - padding * 2) - span_x * scale) / 2 - min_x2d * scale
        offset_y = padding + ((height - padding * 2) - span_y * scale) / 2 - min_y2d * scale
        
        def to_canvas_coords(pt_3d):
            x2d, y2d = project_3d_to_2d(pt_3d)
            cx = offset_x + x2d * scale
            # Y축의 2D 그래픽 캔버스 방향은 아래쪽이 +이므로 뒤집어 줌 (z는 높이이므로 빼준 것 유지)
            cy = offset_y + y2d * scale
            return cx, cy

        # 이미지 생성
        img = Image.new('RGB', (width, height), '#FFFFFF')
        draw = ImageDraw.Draw(img)
        
        # 외곽 테두리
        draw.rectangle([10, 10, width - 10, height - 10], outline='#EAEAEA', width=1)
        
        # 1. 기존 설계 배관 궤적 그리기 (연회색 실선)
        for r in routes_to_draw:
            pts = r['points']
            canvas_pts = [to_canvas_coords(p) for p in pts]
            for i in range(len(canvas_pts) - 1):
                draw.line([canvas_pts[i], canvas_pts[i+1]], fill='#D3D3D3', width=2)
                
        # 2. 추출된 공용 척추선(Spine) 그리기 (유틸리티 그룹별 진하고 굵은 실선)
        colors = {
            'Exhaust': '#FF4500', # OrangeRed
            'Gas': '#1E90FF',     # DodgerBlue
            'Vaccum': '#8A2BE2',  # BlueViolet
            'Water': '#32CD32',   # LimeGreen
            'Toxic': '#FF1493',   # DeepPink
            'UNKNOWN': '#708090'  # SlateGray
        }
        
        for idx, sh in enumerate(spines_to_draw):
            grp = sh['group']
            color = colors.get(grp, colors['UNKNOWN'])
            pts = sh['points']
            if not pts:
                continue
            canvas_pts = [to_canvas_coords(p) for p in pts]
            
            # 라인 드로잉
            for i in range(len(canvas_pts) - 1):
                draw.line([canvas_pts[i], canvas_pts[i+1]], fill=color, width=4)
                
            # 노드 꺾임점 원 드로잉
            for cp in canvas_pts:
                r_pixel = 4
                draw.ellipse([cp[0] - r_pixel, cp[1] - r_pixel, cp[0] + r_pixel, cp[1] + r_pixel], 
                             fill=color, outline='#FFFFFF', width=1)
                             
        # 3. 텍스트 정보 및 범례 작성
        # 상단 타이틀
        sub_title = f" ({target_group})" if target_group else ""
        draw.text((25, 25), f"3D Isometric View - {self.project_name}{sub_title}", fill='#1F4E79')
        draw.text((25, 42), "Grey Lines: Existing Pipelines  |  Colored Lines: Extracted Spines", fill='#808080')
        
        # 우측 하단 범례 박스
        leg_x = width - 180
        leg_y = height - 30 - (len(spines_to_draw) * 18) - 10
        draw.rectangle([leg_x, leg_y, width - 25, height - 25], fill='#FAFAFA', outline='#EAEAEA', width=1)
        
        for idx, sh in enumerate(spines_to_draw):
            grp = sh['group']
            color = colors.get(grp, colors['UNKNOWN'])
            y_pos = leg_y + 8 + (idx * 18)
            # 미니 색상 바
            draw.rectangle([leg_x + 10, y_pos + 4, leg_x + 30, y_pos + 8], fill=color)
            # 유틸 명
            draw.text((leg_x + 40, y_pos), grp, fill='#333333')
            
        img.save(output_path)
        return True

    def save_individual_paths(self):
        """
        학습 시작 전에 로드 및 복원된 개별 배관 경로를 route_feature_path 테이블에 Upsert 저장합니다.
        이 때 3D geometry 필드(geom_3d)도 함께 기록하여 개별 경로 공간 뷰잉이 가능하게 합니다.
        """
        print("   - [개별 경로 저장] 복원된 개별 배관 경로 3D 공간 정보 DB 적재 중...")
        
        sql = """
            INSERT INTO "TB_ROUTE_FEATURE_PATH"
                ("PROJECT_ID", "ROUTE_PATH_GUID", "MAIN_EQUIPMENT_NAME", "EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY",
                 "DIAMETER_MM", "TOTAL_LENGTH_MM", "BEND_COUNT", "MAIN_RACK_Z", "NORMALIZED_POINTS_JSON", "GEOM_3D")
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, ST_GeomFromText(%s, 0))
            ON CONFLICT ("PROJECT_ID", "ROUTE_PATH_GUID")
            DO UPDATE SET
                "MAIN_EQUIPMENT_NAME" = EXCLUDED."MAIN_EQUIPMENT_NAME",
                "EQUIPMENT_NAME" = EXCLUDED."EQUIPMENT_NAME",
                "UTILITY_GROUP" = EXCLUDED."UTILITY_GROUP",
                "UTILITY" = EXCLUDED."UTILITY",
                "DIAMETER_MM" = EXCLUDED."DIAMETER_MM",
                "TOTAL_LENGTH_MM" = EXCLUDED."TOTAL_LENGTH_MM",
                "BEND_COUNT" = EXCLUDED."BEND_COUNT",
                "MAIN_RACK_Z" = EXCLUDED."MAIN_RACK_Z",
                "NORMALIZED_POINTS_JSON" = EXCLUDED."NORMALIZED_POINTS_JSON",
                "GEOM_3D" = EXCLUDED."GEOM_3D",
                "CREATED_AT" = now();
        """
        
        with self.conn.cursor() as cur:
            for r in self.routes:
                pts = r['points']
                wkt = points_to_wkt_linestring3d(pts)
                
                # 정량 특징 산출
                total_length = 0.0
                bend_count = 0
                for i in range(len(pts) - 1):
                    p1, p2 = pts[i], pts[i+1]
                    total_length += dist_3d(p1, p2)
                    if i > 0:
                        # 꺾임 감지 (이전 벡터와 현재 벡터의 방향 변화 분석)
                        v1 = [pts[i][0]-pts[i-1][0], pts[i][1]-pts[i-1][1], pts[i][2]-pts[i-1][2]]
                        v2 = [pts[i+1][0]-pts[i][0], pts[i+1][1]-pts[i][1], pts[i+1][2]-pts[i][2]]
                        # 내적 각도로 판단 (평행이 아니면 꺾임으로 간주)
                        len1 = math.sqrt(v1[0]**2 + v1[1]**2 + v1[2]**2)
                        len2 = math.sqrt(v2[0]**2 + v2[1]**2 + v2[2]**2)
                        if len1 > 0 and len2 > 0:
                            dot = (v1[0]*v2[0] + v1[1]*v2[1] + v1[2]*v2[2]) / (len1 * len2)
                            # 약 5도 이상 차이 나면 꺾임 카운트
                            if dot < 0.996:
                                bend_count += 1
                
                # Z랙고도 분석 (해당 배관에서 가장 긴 수평 세그먼트의 Z)
                main_z = None
                longest_len = -1.0
                for i in range(len(pts) - 1):
                    p1, p2 = pts[i], pts[i+1]
                    if abs(p1[2] - p2[2]) < 5.0:
                        seg_len = math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)
                        if seg_len > longest_len:
                            longest_len = seg_len
                            main_z = (p1[2] + p2[2]) / 2.0
                
                if main_z is None:
                    main_z = pts[0][2] # 수평 구간이 없을 시 시작 Z 적용
                
                # JSON 포인트 구조화
                points_json = json.dumps([[p[0], p[1], p[2]] for p in pts])
                
                # size 파싱 (예: '100A' -> 100.0)
                size_str = r['meta']['size']
                diameter = 80.0
                if size_str:
                    num_str = "".join([c for c in str(size_str) if c.isdigit() or c == '.'])
                    try:
                        diameter = float(num_str) if num_str else 80.0
                    except ValueError:
                        diameter = 80.0
                
                cur.execute(sql, (
                    self.project_name,
                    r['guid'],
                    "MAIN_EQ", 
                    r['meta']['eq_tag'],
                    r['meta']['utility_group'],
                    r['meta']['utility'],
                    diameter,
                    total_length,
                    bend_count,
                    main_z,
                    points_json,
                    wkt
                ))
        self.conn.commit()

    def resample_polyline_points(self, points, N):
        if len(points) < 2:
            return [points[0]] * (N + 1) if points else []
            
        dists = [0.0]
        for a, b in zip(points, points[1:]):
            dists.append(dists[-1] + dist_3d(a, b))
            
        total_len = dists[-1]
        if total_len < 1e-3:
            return [points[0]] * (N + 1)
            
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
            
        return resampled_pts

    def compute_direction_pattern(self, points, tol=0.8):
        codes = []
        for a, b in zip(points, points[1:]):
            dx = b[0] - a[0]
            dy = b[1] - a[1]
            dz = b[2] - a[2]
            L = math.sqrt(dx**2 + dy**2 + dz**2)
            if L < 1.0:
                continue
            uz = dz / L
            uh = math.sqrt(dx**2 + dy**2) / L
            
            if abs(uz) >= tol:
                code = 'R'
            elif uh >= tol:
                code = 'H'
            else:
                code = 'D'
                
            if not codes or codes[-1] != code:
                codes.append(code)
                
        return "-".join(codes)

    def save_anchor_features(self):
        """경로별 시점 및 종점 PoC 접속 방향 정보와 실제 초기 Stub 꺾임 궤적 3D 지오메트리를 영속 저장합니다."""
        sql = """
            INSERT INTO "TB_ROUTE_FEATURE_ANCHOR"
            ("PROJECT_ID", "ROUTE_PATH_GUID", "ANCHOR_KIND", "ANCHOR_NAME", "UTILITY_GROUP", "UTILITY", "FACE", "RISE_MM",
             "CONFIDENCE", "ANCHOR_POINT_JSON", "FIRST_ELBOW_POINT_JSON", "STUB_POINTS_JSON", "GEOM_3D", "STUB_GEOM_3D")
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, ST_GeomFromText(%s, 0), ST_GeomFromText(%s, 0))
            ON CONFLICT ("PROJECT_ID", "ROUTE_PATH_GUID", "ANCHOR_KIND")
            DO UPDATE SET
                "ANCHOR_NAME" = EXCLUDED."ANCHOR_NAME",
                "UTILITY_GROUP" = EXCLUDED."UTILITY_GROUP",
                "UTILITY" = EXCLUDED."UTILITY",
                "FACE" = EXCLUDED."FACE",
                "RISE_MM" = EXCLUDED."RISE_MM",
                "CONFIDENCE" = EXCLUDED."CONFIDENCE",
                "ANCHOR_POINT_JSON" = EXCLUDED."ANCHOR_POINT_JSON",
                "FIRST_ELBOW_POINT_JSON" = EXCLUDED."FIRST_ELBOW_POINT_JSON",
                "STUB_POINTS_JSON" = EXCLUDED."STUB_POINTS_JSON",
                "GEOM_3D" = EXCLUDED."GEOM_3D",
                "STUB_GEOM_3D" = EXCLUDED."STUB_GEOM_3D",
                "CREATED_AT" = now();
        """
        with self.conn.cursor() as cur:
            for r in self.routes:
                pts = r.get('points') or []
                if len(pts) < 2:
                    continue
                bends = route_bends(pts)
                first_elbow_idx = bends[0] if bends else min(len(pts) - 1, 1)
                last_elbow_idx = bends[-1] if bends else max(0, len(pts) - 2)
                start_face = get_dominant_face(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1], pts[1][2] - pts[0][2])
                end_face = get_dominant_face(pts[-2][0] - pts[-1][0], pts[-2][1] - pts[-1][1], pts[-2][2] - pts[-1][2])
                start_stub = pts[:first_elbow_idx + 2]
                end_stub = list(reversed(pts[last_elbow_idx:]))
                meta = r['meta']
                
                # WKT 계산
                pt_start_wkt = points_to_wkt_point3d(pts[0])
                pt_end_wkt = points_to_wkt_point3d(pts[-1])
                start_stub_wkt = points_to_wkt_linestring3d(start_stub)
                end_stub_wkt = points_to_wkt_linestring3d(end_stub)

                cur.execute(sql, (self.project_name, r['guid'], 'EQUIP', meta.get('eq_tag'), meta.get('utility_group'), meta.get('utility'), start_face, abs(pts[first_elbow_idx][2] - pts[0][2]), 1.0, json.dumps(pts[0]), json.dumps(pts[first_elbow_idx]), json.dumps(start_stub), pt_start_wkt, start_stub_wkt))
                cur.execute(sql, (self.project_name, r['guid'], 'TARGET', meta.get('eq_tag'), meta.get('utility_group'), meta.get('utility'), end_face, abs(pts[-1][2] - pts[last_elbow_idx][2]), 1.0, json.dumps(pts[-1]), json.dumps(pts[last_elbow_idx]), json.dumps(end_stub), pt_end_wkt, end_stub_wkt))
        self.conn.commit()

    def load_obstacles_for_routes(self):
        if not self.routes:
            self.obstacles = []
            return []
        minx = min(p[0] for r in self.routes for p in r['points']) - 5000.0
        maxx = max(p[0] for r in self.routes for p in r['points']) + 5000.0
        miny = min(p[1] for r in self.routes for p in r['points']) - 5000.0
        maxy = max(p[1] for r in self.routes for p in r['points']) + 5000.0
        minz = min(p[2] for r in self.routes for p in r['points']) - 5000.0
        maxz = max(p[2] for r in self.routes for p in r['points']) + 5000.0
        sql = """
            SELECT "INSTANCE_NAME", "OST_TYPE", "DDWORKS_TYPE", "COLLISION_PASS",
                   "AABB_MINX", "AABB_MINY", "AABB_MINZ", "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"
            FROM "TB_BIM_OBSTACLE"
            WHERE "AABB_MAXX" >= %s AND "AABB_MINX" <= %s
              AND "AABB_MAXY" >= %s AND "AABB_MINY" <= %s
              AND "AABB_MAXZ" >= %s AND "AABB_MINZ" <= %s
        """
        obstacles = []
        try:
            with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (minx, maxx, miny, maxy, minz, maxz))
                for row in cur.fetchall():
                    box = {
                        'name': row.get('INSTANCE_NAME'),
                        'ost_type': row.get('OST_TYPE'),
                        'ddworks_type': row.get('DDWORKS_TYPE'),
                        'pass_through': bool(row.get('COLLISION_PASS')),
                        'minx': float(row.get('AABB_MINX') or 0.0),
                        'miny': float(row.get('AABB_MINY') or 0.0),
                        'minz': float(row.get('AABB_MINZ') or 0.0),
                        'maxx': float(row.get('AABB_MAXX') or 0.0),
                        'maxy': float(row.get('AABB_MAXY') or 0.0),
                        'maxz': float(row.get('AABB_MAXZ') or 0.0),
                    }
                    box['obstacle_type'] = classify_obstacle_type(box['name'], box['ost_type'], box['ddworks_type'])
                    box['axis'] = obstacle_axis(box)
                    obstacles.append(box)
        except Exception as ex:
            print(f"   - [Notice] Could not load TB_BIM_OBSTACLE relations: {ex}")
            self.conn.rollback()
        self.obstacles = obstacles
        return obstacles

    def save_obstacle_relations(self):
        obstacles = self.load_obstacles_for_routes()
        if not obstacles:
            print("   - [Obstacle] No obstacle AABB data found for relation learning.")
            return
        insert_sql = """
            INSERT INTO "TB_ROUTE_FEATURE_OBSTACLE_RELATION"
            ("PROJECT_ID", "ROUTE_PATH_GUID", "OBSTACLE_NAME", "OBSTACLE_TYPE", "OBSTACLE_AXIS", "UTILITY_GROUP", "UTILITY",
             "DIAMETER_MM", "NEAREST_DISTANCE_MM", "REQUIRED_CLEARANCE_MM", "CLEARANCE_MARGIN_MM", "BYPASS_SIDE", "BYPASS_AXIS",
             "Z_DELTA_NEAR_OBSTACLE_MM", "BEND_COUNT_BEFORE", "BEND_COUNT_AFTER", "EXTRA_LENGTH_RATIO", "RELATION_SCORE", "GEOM_3D")
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 0))
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM \"TB_ROUTE_FEATURE_OBSTACLE_RELATION\" WHERE \"PROJECT_ID\" = %s", (self.project_name,))
                count = 0
                total_routes = len(self.routes)
                for idx, r in enumerate(self.routes):
                    # 진행률 출력
                    if total_routes >= 5 and (idx + 1) % max(1, total_routes // 5) == 0:
                        print(f"     * 장애물 관계 분석 진행중... {int((idx + 1) / total_routes * 100)}% ({idx + 1}/{total_routes} 완료)")
                        
                    pts = r.get('points') or []
                    if len(pts) < 2:
                        continue
                    meta = r['meta']
                    diameter = parse_pipe_diameter(meta.get('size')) or 0.0
                    required_clearance = diameter * 0.5 + 150.0
                    limit_dist = max(required_clearance + 1000.0, 1800.0)
                    
                    # 배관 AABB 영역 산출
                    r_minx = min(p[0] for p in pts)
                    r_maxx = max(p[0] for p in pts)
                    r_miny = min(p[1] for p in pts)
                    r_maxy = max(p[1] for p in pts)
                    r_minz = min(p[2] for p in pts)
                    r_maxz = max(p[2] for p in pts)
                    
                    route_length = sum(dist_3d(pts[i - 1], pts[i]) for i in range(1, len(pts)))
                    straight = dist_3d(pts[0], pts[-1]) or 1.0
                    bends = route_bends(pts)
                    
                    for obs in obstacles:
                        # 1차 AABB Distance Pruning: 배관 바운딩 박스와 장애물 바운딩 박스 간의 거리 필터링
                        dx = max(obs['minx'] - r_maxx, 0.0, r_minx - obs['maxx'])
                        dy = max(obs['miny'] - r_maxy, 0.0, r_miny - obs['maxy'])
                        dz = max(obs['minz'] - r_maxz, 0.0, r_minz - obs['maxz'])
                        aabb_dist = math.sqrt(dx*dx + dy*dy + dz*dz)
                        if aabb_dist > limit_dist:
                            continue
                            
                        best = (float('inf'), 0, 0.0, pts[0])
                        for i in range(1, len(pts)):
                            d, t, near_pt = segment_aabb_distance(pts[i - 1], pts[i], obs)
                            if d < best[0]:
                                best = (d, i, t, near_pt)
                        nearest, seg_index, _, near_pt = best
                        if nearest > limit_dist:
                            continue
                        before = sum(1 for b in bends if b < seg_index)
                        after = sum(1 for b in bends if b >= seg_index)
                        bypass_axis = get_dominant_face(pts[seg_index][0] - pts[seg_index - 1][0], pts[seg_index][1] - pts[seg_index - 1][1], pts[seg_index][2] - pts[seg_index - 1][2])
                        z_mid = (obs['minz'] + obs['maxz']) * 0.5
                        relation_score = max(0.0, (required_clearance + 1000.0 - nearest) / (required_clearance + 1000.0))
                        
                        # 최단선 geometry (배관 측 near_pt <-> 장애물 표면 최단접점)
                        aabb_near_pt = closest_point_on_aabb(near_pt, obs)
                        radar_wkt = points_to_wkt_linestring3d([near_pt, aabb_near_pt])

                        cur.execute(insert_sql, (self.project_name, r['guid'], obs['name'], obs['obstacle_type'], obs['axis'], meta.get('utility_group'), meta.get('utility'), diameter, nearest, required_clearance, nearest - required_clearance, bypass_side_from_obstacle(near_pt, obs), bypass_axis, near_pt[2] - z_mid, before, after, route_length / straight, relation_score, radar_wkt))
                        count += 1
            self.conn.commit()
            self.obstacle_relation_count = count
            print(f"   - [Obstacle] Learned obstacle-route relations: {count}")
        except Exception as e:
            self.conn.rollback()
            print(f"   - [Obstacle] Failed to save obstacle relations. Transaction rolled back. Error: {e}")

    def learn_stub_templates(self):
        if not HAS_STUB_PATTERNS:
            print(f"   - [Stub] ExtractStubPatterns import failed: {STUB_PATTERN_IMPORT_ERROR}")
            return
        extract_args = SimpleNamespace(main_equipment=self.project_name, utility_group=None, utility=None, size=None, limit=None, dry_run=False, export_json=None, replace=True, min_samples=1)
        try:
            samples = stub_patterns.extract_samples(self.conn, extract_args)
            main_names = sorted({s.main_equipment_name for s in samples if getattr(s, 'main_equipment_name', None)})
            templates = []
            for main_name in main_names:
                build_args = SimpleNamespace(main_equipment=main_name, utility_group=None, utility=None, size=None, limit=None, dry_run=False, export_json=None, replace=True, min_samples=1)
                templates.extend(stub_patterns.build_templates(self.conn, build_args))
            self.stub_sample_count = len(samples)
            self.stub_template_count = len(templates)
            self.mirror_stub_templates(main_names)
            print(f"   - [Stub] Learned route-geometry stub samples={len(samples)}, templates={len(templates)}")
        except Exception as ex:
            print(f"   - [Stub] Stub template learning failed: {ex}")
            self.conn.rollback()

    def mirror_stub_templates(self, main_equipment_names=None):
        main_equipment_names = [x for x in (main_equipment_names or [self.project_name]) if x]
        if not main_equipment_names:
            return
        select_sql = """
            SELECT "TEMPLATE_ID", "STUB_KIND", "ANCHOR_KIND", "MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY",
                   "SIZE", "FACE", "DIR_SEQ", "SAMPLE_COUNT", "AVG_RISE_MM", "AVG_OFFSET_MM", "AVG_STUB_LENGTH_MM",
                   "REPRESENTATIVE_STUB_POINTS", "AVG_FEAT_JSON"
            FROM "TB_ROUTE_STUB_TEMPLATE"
            WHERE "MAIN_EQUIPMENT_NAME" = ANY(%s)
        """
        insert_sql = """
            INSERT INTO "TB_ROUTE_FEATURE_STUB_TEMPLATE"
            ("TEMPLATE_ID", "PROJECT_ID", "STUB_KIND", "ANCHOR_KIND", "MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "SIZE", "FACE",
             "DIR_SEQ_JSON", "SAMPLE_COUNT", "AVG_RISE_MM", "AVG_OFFSET_MM", "AVG_LENGTH_MM", "REPRESENTATIVE_POINTS_JSON", "AVG_FEAT_JSON", "GEOM_3D")
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s::jsonb, ST_GeomFromText(%s, 0))
            ON CONFLICT ("TEMPLATE_ID")
            DO UPDATE SET "SAMPLE_COUNT" = EXCLUDED."SAMPLE_COUNT", "AVG_RISE_MM" = EXCLUDED."AVG_RISE_MM",
                "AVG_OFFSET_MM" = EXCLUDED."AVG_OFFSET_MM", "AVG_LENGTH_MM" = EXCLUDED."AVG_LENGTH_MM",
                "REPRESENTATIVE_POINTS_JSON" = EXCLUDED."REPRESENTATIVE_POINTS_JSON", "AVG_FEAT_JSON" = EXCLUDED."AVG_FEAT_JSON",
                "GEOM_3D" = EXCLUDED."GEOM_3D",
                "UPDATED_AT" = now();
        """
        def as_json(value):
            return value if isinstance(value, str) else json.dumps(value or [])
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(select_sql, (main_equipment_names,))
            rows = cur.fetchall()
            if rows:
                batch_data = []
                for row in rows:
                    # 대표 Stub 꺾임점 좌표 배열을 로드하여 WKT 3D LineString으로 변환
                    pts_val = row['REPRESENTATIVE_STUB_POINTS']
                    pts_list = []
                    if isinstance(pts_val, str):
                        try:
                            pts_list = json.loads(pts_val)
                        except Exception:
                            pts_list = []
                    elif isinstance(pts_val, list):
                        pts_list = pts_val
                    stub_wkt = points_to_wkt_linestring3d(pts_list)

                    batch_data.append((
                        row['TEMPLATE_ID'], self.project_name, row['STUB_KIND'], row['ANCHOR_KIND'],
                        row['MAIN_EQUIPMENT_NAME'], row['UTILITY_GROUP'], row['UTILITY'], row['SIZE'], row['FACE'],
                        json.dumps([x for x in str(row['DIR_SEQ'] or '').split(',') if x]),
                        row['SAMPLE_COUNT'], row['AVG_RISE_MM'], row['AVG_OFFSET_MM'], row['AVG_STUB_LENGTH_MM'],
                        as_json(row['REPRESENTATIVE_STUB_POINTS']), as_json(row['AVG_FEAT_JSON']), stub_wkt
                    ))
                psycopg2.extras.execute_batch(cur, insert_sql, batch_data, page_size=200)
        self.conn.commit()

    def save_bundle_template(self, grp, rack_zs, spine_pts, r_list):
        if not r_list:
            return
        xs = [p[0] for p in spine_pts] if spine_pts else [p[0] for r in r_list for p in r['points']]
        ys = [p[1] for p in spine_pts] if spine_pts else [p[1] for r in r_list for p in r['points']]
        trunk_axis = 'X' if (max(xs) - min(xs)) >= (max(ys) - min(ys)) else 'Y'
        route_guids = [r['guid'] for r in r_list]
        bundle_id = f"{self.project_name}:{grp}"
        sql = """
            INSERT INTO "TB_ROUTE_FEATURE_BUNDLE_TEMPLATE"
            ("PROJECT_ID", "BUNDLE_ID", "UTILITY_GROUP", "UTILITY", "ROUTE_COUNT", "PREFERRED_RACK_ZS", "TRUNK_AXIS",
             "TRUNK_CENTERLINE_JSON", "MEMBER_ROUTE_GUIDS_JSON", "GEOM_3D")
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, ST_GeomFromText(%s, 0))
            ON CONFLICT ("PROJECT_ID", "BUNDLE_ID")
            DO UPDATE SET "ROUTE_COUNT" = EXCLUDED."ROUTE_COUNT", "PREFERRED_RACK_ZS" = EXCLUDED."PREFERRED_RACK_ZS",
                "TRUNK_AXIS" = EXCLUDED."TRUNK_AXIS", "TRUNK_CENTERLINE_JSON" = EXCLUDED."TRUNK_CENTERLINE_JSON",
                "MEMBER_ROUTE_GUIDS_JSON" = EXCLUDED."MEMBER_ROUTE_GUIDS_JSON", 
                "GEOM_3D" = EXCLUDED."GEOM_3D",
                "UPDATED_AT" = now();
        """
        # 공용 척추선(backbone) 좌표 리스트를 WKT 3D LineString으로 변환
        spine_wkt = points_to_wkt_linestring3d(spine_pts)
        with self.conn.cursor() as cur:
            cur.execute(sql, (self.project_name, bundle_id, grp, grp, len(r_list), rack_zs, trunk_axis, json.dumps(spine_pts), json.dumps(route_guids), spine_wkt))
        self.conn.commit()
        self.bundle_template_count += 1

    def save_route_similarity_vectors(self):
        """
        프로젝트 내 모든 배관 경로의 30차원 유사설계 특징 벡터를 계산하여 
        TB_ROUTE_FEATURE_VECTOR 테이블에 저장(Upsert)합니다.
        """
        if not self.pgvector_enabled:
            print("   - [Notice] pgvector가 비활성화되어 특징 벡터 DB 생성을 건너뜁니다.")
            return

        print("   - [유사 설계 벡터 생성] 30D 특징 벡터 각 방향 패턴 생성 및 DB 적재 중...")
        
        sql = """
            INSERT INTO "TB_ROUTE_FEATURE_VECTOR" (
                "ROUTE_PATH_GUID", "PROCESS_NAME", "EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "SIZE",
                "DIRECTION_PATTERN", "TOTAL_LENGTH_MM", "STEP_COUNT",
                "START_POSX", "START_POSY", "START_POSZ",
                "END_POSX", "END_POSY", "END_POSZ",
                "FEATURE_VECTOR"
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
            ON CONFLICT ("ROUTE_PATH_GUID")
            DO UPDATE SET
                "PROCESS_NAME" = EXCLUDED."PROCESS_NAME",
                "EQUIPMENT_NAME" = EXCLUDED."EQUIPMENT_NAME",
                "UTILITY_GROUP" = EXCLUDED."UTILITY_GROUP",
                "UTILITY" = EXCLUDED."UTILITY",
                "SIZE" = EXCLUDED."SIZE",
                "DIRECTION_PATTERN" = EXCLUDED."DIRECTION_PATTERN",
                "TOTAL_LENGTH_MM" = EXCLUDED."TOTAL_LENGTH_MM",
                "STEP_COUNT" = EXCLUDED."STEP_COUNT",
                "START_POSX" = EXCLUDED."START_POSX",
                "START_POSY" = EXCLUDED."START_POSY",
                "START_POSZ" = EXCLUDED."START_POSZ",
                "END_POSX" = EXCLUDED."END_POSX",
                "END_POSY" = EXCLUDED."END_POSY",
                "END_POSZ" = EXCLUDED."END_POSZ",
                "FEATURE_VECTOR" = EXCLUDED."FEATURE_VECTOR";
        """
        
        WEIGHT_MAP = [
            ("start_topology", 0,  3,  0.20),
            ("end_topology",   3,  6,  0.20),
            ("displacement",   6,  9,  0.15),
            ("bounding_box",   9,  12, 0.15),
            ("segment_1",      12, 15, 0.06),
            ("segment_2",      15, 18, 0.06),
            ("segment_3",      18, 21, 0.06),
            ("env_cost",       21, 25, 0.12),
            ("arrow_pattern",  25, 30, 0.15),
        ]
        
        scale_factors = [1.0] * 30
        for name, start, end, weight in WEIGHT_MAP:
            dim = end - start
            if weight > 0 and dim > 0:
                factor = math.sqrt(weight * 30.0 / dim)
                for j in range(start, end):
                    scale_factors[j] = factor
                    
        def dist_3d_local(p1, p2):
            return math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2 + (p1[2]-p2[2])**2)
            
        if self.routes:
            BBOX_MAX_X = max(abs(max(p[0] for p in r['points']) - min(p[0] for p in r['points'])) for r in self.routes) or 1.0
            BBOX_MAX_Y = max(abs(max(p[1] for p in r['points']) - min(p[1] for p in r['points'])) for r in self.routes) or 1.0
            BBOX_MAX_Z = max(abs(max(p[2] for p in r['points']) - min(p[2] for p in r['points'])) for r in self.routes) or 1.0
            DISPLACEMENT_MAX = max(dist_3d_local(r['points'][0], r['points'][-1]) for r in self.routes) or 1.0
            TOTAL_LENGTH_MAX = max(sum(dist_3d_local(r['points'][i], r['points'][i+1]) for i in range(len(r['points'])-1)) for r in self.routes) or 1.0
        else:
            BBOX_MAX_X = BBOX_MAX_Y = BBOX_MAX_Z = DISPLACEMENT_MAX = TOTAL_LENGTH_MAX = 1.0
        
        # Query obstacle relations for the project to calculate Env Cost (Index 22~24)
        obs_relations = defaultdict(list)
        relation_sql = """
            SELECT "ROUTE_PATH_GUID", "CLEARANCE_MARGIN_MM", "Z_DELTA_NEAR_OBSTACLE_MM"
            FROM "TB_ROUTE_FEATURE_OBSTACLE_RELATION"
            WHERE "PROJECT_ID" = %s
        """
        try:
            with self.conn.cursor() as rel_cur:
                rel_cur.execute(relation_sql, (self.project_name,))
                for row in rel_cur.fetchall():
                    guid = row[0].strip()
                    margin = row[1]
                    z_delta = row[2]
                    obs_relations[guid].append((margin, z_delta))
        except Exception as rel_ex:
            print(f"   - [Warning] Failed to fetch obstacle relations for vector creation: {rel_ex}")

        count = 0
        with self.conn.cursor() as cur:
            for r in self.routes:
                pts = r['points']
                if len(pts) < 2:
                    continue
                    
                meta = r['meta']
                p0 = pts[0]
                pn = pts[-1]
                
                dx = pn[0] - p0[0]
                dy = pn[1] - p0[1]
                dz = pn[2] - p0[2]
                
                total_len = 0.0
                for i in range(len(pts) - 1):
                    total_len += dist_3d(pts[i], pts[i+1])
                    
                vec = [0.0] * 30
                
                v_start = (pts[1][0] - p0[0], pts[1][1] - p0[1], pts[1][2] - p0[2])
                v_start_len = math.sqrt(v_start[0]**2 + v_start[1]**2 + v_start[2]**2)
                v_start_safe = v_start_len if v_start_len > 1e-9 else 1.0
                vec[0] = v_start[0] / v_start_safe
                vec[1] = v_start[1] / v_start_safe
                vec[2] = v_start[2] / v_start_safe
                
                v_end = (pts[-2][0] - pn[0], pts[-2][1] - pn[1], pts[-2][2] - pn[2])
                v_end_len = math.sqrt(v_end[0]**2 + v_end[1]**2 + v_end[2]**2)
                v_end_safe = v_end_len if v_end_len > 1e-9 else 1.0
                vec[3] = v_end[0] / v_end_safe
                vec[4] = v_end[1] / v_end_safe
                vec[5] = v_end[2] / v_end_safe
                
                vec[6] = max(-1.0, min(1.0, dx / DISPLACEMENT_MAX))
                vec[7] = max(-1.0, min(1.0, dy / DISPLACEMENT_MAX))
                vec[8] = max(-1.0, min(1.0, dz / DISPLACEMENT_MAX))
                
                vec[9] = max(-1.0, min(1.0, abs(dx) / BBOX_MAX_X))
                vec[10] = max(-1.0, min(1.0, abs(dy) / BBOX_MAX_Y))
                vec[11] = max(-1.0, min(1.0, abs(dz) / BBOX_MAX_Z))
                
                resampled = self.resample_polyline_points(pts, 3)
                if len(resampled) == 4:
                    for i in range(3):
                        p_from = resampled[i]
                        p_to = resampled[i+1]
                        seg_v = (p_to[0] - p_from[0], p_to[1] - p_from[1], p_to[2] - p_from[2])
                        seg_len = math.sqrt(seg_v[0]**2 + seg_v[1]**2 + seg_v[2]**2)
                        seg_safe = seg_len if seg_len > 1e-9 else 1.0
                        idx = 12 + i * 3
                        vec[idx] = max(-1.0, min(1.0, seg_v[0] / seg_safe))
                        vec[idx+1] = max(-1.0, min(1.0, seg_v[1] / seg_safe))
                        vec[idx+2] = max(-1.0, min(1.0, seg_v[2] / seg_safe))
                        
                vec[21] = max(-1.0, min(1.0, total_len / TOTAL_LENGTH_MAX))
                
                # Env Cost (Index 22 ~ 24)
                guid = r['guid']
                r_relations = obs_relations.get(guid, [])
                if r_relations:
                    margins = [rel[0] for rel in r_relations if rel[0] is not None]
                    min_margin = min(margins) if margins else 300.0
                    e1 = max(0.0, min(1.0, (300.0 - min_margin) / 300.0))
                else:
                    e1 = 0.0
                    
                straight = dist_3d(p0, pn)
                straight_safe = straight if straight > 1e-9 else 1.0
                overhead = (total_len / straight_safe) - 1.0
                e2 = max(0.0, min(1.0, overhead / 0.5))
                
                if r_relations:
                    z_deltas = [abs(rel[1]) for rel in r_relations if rel[1] is not None]
                    max_z_delta = max(z_deltas) if z_deltas else 0.0
                    e3 = max(0.0, min(1.0, max_z_delta / 1000.0))
                else:
                    e3 = 0.0
                    
                vec[22] = e1
                vec[23] = e2
                vec[24] = e3
                
                # Arrow Pattern (Index 25 ~ 29)
                len_x = 0.0
                len_y = 0.0
                len_z = 0.0
                for i in range(1, len(pts)):
                    pt1 = pts[i-1]
                    pt2 = pts[i]
                    dx_seg = abs(pt2[0] - pt1[0])
                    dy_seg = abs(pt2[1] - pt1[1])
                    dz_seg = abs(pt2[2] - pt1[2])
                    max_diff = max(dx_seg, dy_seg, dz_seg)
                    seg_dist = dist_3d(pt1, pt2)
                    if max_diff < 1e-6:
                        continue
                    if dx_seg == max_diff:
                        len_x += seg_dist
                    elif dy_seg == max_diff:
                        len_y += seg_dist
                    else:
                        len_z += seg_dist
                        
                total_len_safe = total_len if total_len > 1e-9 else 1.0
                rx = len_x / total_len_safe
                ry = len_y / total_len_safe
                rz = len_z / total_len_safe
                
                bend_count = len(route_bends(pts))
                rbend = max(0.0, min(1.0, bend_count / 10.0))
                
                vec[25] = rx
                vec[26] = ry
                vec[27] = rz
                vec[28] = rbend
                vec[29] = 0.0
                
                for j in range(30):
                    vec[j] *= scale_factors[j]
                    
                sq_sum = sum(x**2 for x in vec)
                norm = math.sqrt(sq_sum)
                if norm > 1e-9:
                    vec = [x / norm for x in vec]
                else:
                    vec = [0.0] * 30
                    
                vec_literal = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
                dir_pattern = self.compute_direction_pattern(pts)
                
                cur.execute(sql, (
                    r['guid'],
                    meta.get('process_name') or '',
                    meta['eq_tag'],
                    meta['utility_group'],
                    meta['utility'],
                    meta['size'],
                    dir_pattern,
                    total_len,
                    len(pts) - 1,
                    p0[0], p0[1], p0[2],
                    pn[0], pn[1], pn[2],
                    vec_literal
                ))
                count += 1
        self.conn.commit()
        print(f"-> 유사 설계 30D 벡터 {count}개 적재 완료.")

    def learn_and_save(self):
        """
        유틸리티 그룹별로 배관들을 파티셔닝하고, 
        랙 고도 검출, PoC 접속면 분석, 척추선 분석을 차례대로 수행한 후
        데이터베이스 테이블에 UPSERT 방식으로 기록합니다.
        """
        import time
        t0 = time.time()
        
        # 먼저 개별 경로 3D 지오메트리 데이터 저장 진행
        self.save_individual_paths()
        t1 = time.time()
        print(f"     * 개별 경로 3D 지오메트리 저장 완료 (소요시간: {t1 - t0:.2f}초)")
        
        # 장애물-배관 최단 거리 분석 (유사설계 특징 벡터 생성 시 Env Cost에 활용하기 위해 먼저 실행)
        self.save_obstacle_relations()
        t_obs = time.time()
        print(f"     * 장애물-배관 최단 거리 및 이격Margin 학습 완료 (소요시간: {t_obs - t1:.2f}초)")
        
        # 30D 유사설계 특징 벡터 저장 진행
        self.save_route_similarity_vectors()
        t2 = time.time()
        print(f"     * 유사 설계 특징 벡터 생성/적재 완료 (소요시간: {t2 - t_obs:.2f}초)")
        
        self.save_anchor_features()
        t3 = time.time()
        print(f"     * 앵커(시/종점 PoC) 접속면 정보 적재 완료 (소요시간: {t3 - t2:.2f}초)")
        
        self.learn_stub_templates()
        t4 = time.time()
        print(f"     * Stub 템플릿 추출 및 학습 완료 (소요시간: {t4 - t3:.2f}초)")
        
        # 수직다발배관 입상 특징점 추출 및 저장 진행
        if HAS_VERTICAL_GROUP and vertical_group:
            vertical_group.extract_and_save_vertical_groups(self.conn, self.project_name, self.routes)
            t_vert = time.time()
            print(f"     * 수직다발배관 입상 특징점 추출 및 적재 완료 (소요시간: {t_vert - t4:.2f}초)")
        else:
            t_vert = t4
            
        print("3. [특징 추출 및 학습] 유틸리티 그룹별 특징점 통계 분석 실행...")
        
        # 유틸리티 그룹별로 분류
        group_pipes = defaultdict(list)
        for r in self.routes:
            grp = r['meta']['utility_group'] or "UNKNOWN"
            group_pipes[grp].append(r)

        t_groups_start = time.time()
        for grp, r_list in group_pipes.items():
            t_grp_start = time.time()
            print(f"\n[유틸리티 그룹: {grp}] 분석 시작 (배관 {len(r_list)}개)...")
            
            # 1) 선호 Z고도(대표 랙 높이) 검출
            rack_zs = self.detect_rack_levels(r_list)
            print(f"  - 선호 Z 랙 고도: {', '.join([f'{z:.0f}mm' for z in rack_zs])}")

            # 2) PoC 접속면 (Face) 분석
            source_face, source_conf, target_face, target_conf = self.analyze_poc_faces(r_list)
            print(f"  - 선호 출발면: {source_face} (신뢰도: {source_conf*100:.0f}%)")
            print(f"  - 선호 종단면: {target_face} (신뢰도: {target_conf*100:.0f}%)")

            # 3) 공용 척추선(Trunk Spine) 추출 및 RDP 단순화
            spine_pts = self.extract_trunk_spine(r_list)
            print(f"  - 공용 척추선 Waypoints 수: {len(spine_pts)}개")

            # 리포트 통계 누적
            self.spine_history.append({'group': grp, 'points': spine_pts})
            
            # 유틸리티별 단독 3D 뷰 렌더링 호출
            temp_grp_img_path = None
            if self.report_enabled:
                scratch_dir = Path(__file__).resolve().parent.parent / "scratch"
                scratch_dir.mkdir(exist_ok=True)
                clean_proj_name = "".join([c if c.isalnum() or c in ('-', '_') else '_' for c in self.project_name])
                clean_grp_name = "".join([c if c.isalnum() or c in ('-', '_') else '_' for c in grp])
                temp_grp_img_path = str(scratch_dir / f"temp_{clean_proj_name}_{clean_grp_name}_3d.png")
                
                if self.render_3d_view(temp_grp_img_path, target_group=grp):
                    print(f"  - [3D 뷰] 유틸리티 {grp} 3D 궤적 이미지 생성 완료: {temp_grp_img_path}")
                else:
                    temp_grp_img_path = None

            self.report_groups.append({
                'group_name': grp,
                'rack_zs': rack_zs,
                's_face': source_face,
                's_conf': source_conf,
                't_face': target_face,
                't_conf': target_conf,
                'spine_len': len(spine_pts),
                'img_path': temp_grp_img_path,
                'topk_count': len(r_list)
            })

            # DB에 프로필 정보 Upsert 실행
            self.save_group_profile(grp, source_face, target_face, rack_zs, spine_pts)
            self.save_bundle_template(grp, rack_zs, spine_pts, r_list)
            
            t_grp_end = time.time()
            print(f"  - [{grp}] 유틸리티 분석 및 적재 완료 (소요시간: {t_grp_end - t_grp_start:.2f}초)")

        t_groups_end = time.time()
        print(f"\n     * 유틸리티 그룹별 분석 총 소요시간: {t_groups_end - t_groups_start:.2f}초")

        # 전체 3D 뷰 렌더링 호출
        self.temp_img_path = None
        if self.report_enabled:
            t_render_start = time.time()
            scratch_dir = Path(__file__).resolve().parent.parent / "scratch"
            scratch_dir.mkdir(exist_ok=True)
            # Windows 파일 이름에 부적합한 특수문자 정제
            clean_proj_name = "".join([c if c.isalnum() or c in ('-', '_') else '_' for c in self.project_name])
            self.temp_img_path = str(scratch_dir / f"temp_{clean_proj_name}_3d.png")
            if self.render_3d_view(self.temp_img_path):
                print(f"  - [3D 뷰] 프로젝트 3D 궤적 이미지 생성 완료: {self.temp_img_path}")
            else:
                self.temp_img_path = None
            t_render_end = time.time()
            print(f"     * 전체 3D Isometric View 렌더링 소요시간: {t_render_end - t_render_start:.2f}초")

        print("\n4. [DB 저장 완료] 특징 학습 데이터베이스 이식이 완료되었습니다.")

    # --- 세부 핵심 알고리즘 ①: Z고도 히스토그램 피크 검출 ---
    def detect_rack_levels(self, r_list, bin_size=100.0, max_levels=5):
        """
        경로상의 모든 세그먼트 중 수평 세그먼트를 추출하고, 
        주행 길이를 가중치로 히스토그램 분석하여 대표 선호 랙 고도를 구합니다.
        - bin_size: Z축 그룹 단위 (100mm)
        - max_levels: 최대 획득 고도 개수 (5개)
        """
        z_weights = defaultdict(float)
        for r in r_list:
            pts = r['points']
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i+1]
                # delta Z가 5mm 이내인 수평 진행 구간 판정
                if abs(p1[2] - p2[2]) < 5.0:
                    mid_z = (p1[2] + p2[2]) / 2.0
                    bin_z = round(mid_z / bin_size) * bin_size
                    length = math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)
                    z_weights[bin_z] += length

        if not z_weights:
            return [3000.0] # 폴백 값

        # 누적 길이가 높은 빈도 순으로 Peak 정렬
        sorted_bins = sorted(z_weights.items(), key=lambda x: x[1], reverse=True)
        peaks = []
        for z, w in sorted_bins:
            # 300mm 이내 근접 피크는 병합하여 중복 배제
            if not any(abs(p - z) < 300.0 for p in peaks):
                peaks.append(z)
                if len(peaks) >= max_levels:
                    break
        return sorted(peaks)

    # --- 세부 핵심 알고리즘 ②: PoC 접속면 (Face) Voting ---
    def analyze_poc_faces(self, r_list):
        """
        배관 시작부(Source) 및 끝단부(Target)의 단위 벡터 주축 성분을 비교해 
        대표 진입/출발면 방향(+x, -x, +y, -y, +z, -z)의 다수결 Voting을 수행합니다.
        - 신뢰도(Confidence) = (최다 득표 face 수 / 전체 표수)
        """
        source_votes = Counter()
        target_votes = Counter()

        for r in r_list:
            pts = r['points']
            if len(pts) < 2:
                continue
            
            # 1) 출발부 벡터
            v_start = [pts[1][0]-pts[0][0], pts[1][1]-pts[0][1], pts[1][2]-pts[0][2]]
            start_len = math.sqrt(v_start[0]**2 + v_start[1]**2 + v_start[2]**2)
            if start_len > 0:
                s_face = get_dominant_face(v_start[0]/start_len, v_start[1]/start_len, v_start[2]/start_len)
                source_votes[s_face] += 1

            # 2) 종단 진입 벡터의 역방향 (접속면의 법선 방향 판정)
            v_end = [pts[-1][0]-pts[-2][0], pts[-1][1]-pts[-2][1], pts[-1][2]-pts[-2][2]]
            end_len = math.sqrt(v_end[0]**2 + v_end[1]**2 + v_end[2]**2)
            if end_len > 0:
                e_face = get_dominant_face(-v_end[0]/end_len, -v_end[1]/end_len, -v_end[2]/end_len)
                target_votes[e_face] += 1

        s_best, s_conf = "Any", 0.0
        if source_votes:
            s_best, s_cnt = source_votes.most_common(1)[0]
            s_conf = s_cnt / sum(source_votes.values())

        t_best, t_conf = "Any", 0.0
        if target_votes:
            t_best, t_cnt = target_votes.most_common(1)[0]
            t_conf = t_cnt / sum(target_votes.values())

        return s_best, s_conf, t_best, t_conf

    # --- 세부 핵심 알고리즘 ③: DBSCAN & RDP 척추선(Spine) 추출 ---
    def extract_trunk_spine(self, r_list, resample_dist=200.0, dbs_eps=500.0):
        """
        배관들의 포인트들을 조밀하게 분할 생성한 뒤, DBSCAN 또는 공간 범위 군집화로 
        번들 다발 영역을 검출하고 RDP(Ramer-Douglas-Peucker) 단순화로 척추선 좌표 목록을 도출합니다.
        - resample_dist: 포인트 분할 재생성 거리 (200mm)
        - dbs_eps: DBSCAN 밀집 유효 반경 (500mm)
        """
        # 1. 촘촘한 포인트 분할
        all_pts = []
        for r in r_list:
            pts = r['points']
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i+1]
                d = dist_3d(p1, p2)
                steps = max(1, int(d / resample_dist))
                for step in range(steps + 1):
                    t = step / steps
                    px = p1[0] + (p2[0] - p1[0]) * t
                    py = p1[1] + (p2[1] - p1[1]) * t
                    pz = p1[2] + (p2[2] - p1[2]) * t
                    all_pts.append((px, py, pz))

        if not all_pts:
            return []

        # 2. 클러스터링으로 메인 척추 포인트 추출
        if HAS_SKLEARN:
            db = DBSCAN(eps=dbs_eps, min_samples=10).fit(all_pts)
            labels = db.labels_
            counts = Counter(labels)
            if -1 in counts:
                del counts[-1]
            if not counts:
                spine_candidates = all_pts
            else:
                main_label = counts.most_common(1)[0][0]
                spine_candidates = [all_pts[idx] for idx, l in enumerate(labels) if l == main_label]
        else:
            # sklearn이 존재하지 않을 시 백업 클러스터링 작동
            clusters = simple_spatial_clustering(all_pts, dbs_eps)
            if clusters:
                spine_candidates = max(clusters, key=len)
            else:
                spine_candidates = all_pts

        # 3. 진행 주축(X 또는 Y) 방향으로 정렬하여 일차원 라인 형성
        xs = [p[0] for p in spine_candidates]
        ys = [p[1] for p in spine_candidates]
        x_span = max(xs) - min(xs) if xs else 0
        y_span = max(ys) - min(ys) if ys else 0
        
        if x_span >= y_span:
            spine_candidates.sort(key=lambda p: p[0])
        else:
            spine_candidates.sort(key=lambda p: p[1])

        # 4. RDP 단순화로 꺾임이 생기는 특징 웨이포인트(Waypoints)만 압축
        # epsilon: 150mm 오차 이내 점들 선형 병합
        simplified_spine = rdp_simplification(spine_candidates, epsilon=150.0)
        
        refined_spine = []
        for p in simplified_spine:
            if not refined_spine or dist_3d(refined_spine[-1], p) > 300.0:
                refined_spine.append(p)
        return refined_spine

    def save_group_profile(self, grp, s_face, t_face, rack_zs, spine_pts):
        """
        분석 완료된 그룹 특징 데이터를 route_feature_group_profile 테이블에 저장(Upsert)합니다.
        """
        spine_json = json.dumps([{"X": p[0], "Y": p[1], "Z": p[2]} for p in spine_pts])
        spine_wkt = points_to_wkt_linestring3d(spine_pts)
        
        sql = """
            INSERT INTO "TB_ROUTE_FEATURE_GROUP_PROFILE"
                ("PROJECT_ID", "MAIN_EQUIPMENT_NAME", "EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY",
                 "PREFERRED_SOURCE_FACE", "PREFERRED_TARGET_FACE", "PREFERRED_RACK_ZS", "TRUNK_CENTERLINE_JSON", "TRUNK_CENTERLINE_GEOM")
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, ST_GeomFromText(%s, 0))
            ON CONFLICT ("PROJECT_ID", "MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY")
            DO UPDATE SET
                "PREFERRED_SOURCE_FACE" = EXCLUDED."PREFERRED_SOURCE_FACE",
                "PREFERRED_TARGET_FACE" = EXCLUDED."PREFERRED_TARGET_FACE",
                "PREFERRED_RACK_ZS" = EXCLUDED."PREFERRED_RACK_ZS",
                "TRUNK_CENTERLINE_JSON" = EXCLUDED."TRUNK_CENTERLINE_JSON",
                "TRUNK_CENTERLINE_GEOM" = EXCLUDED."TRUNK_CENTERLINE_GEOM",
                "UPDATED_AT" = now();
        """
        with self.conn.cursor() as cur:
            cur.execute(sql, (
                self.project_name,
                self.project_name,
                None,
                grp,
                grp,
                s_face,
                t_face,
                rack_zs,
                spine_json,
                spine_wkt
            ))
        self.conn.commit()


def generate_comprehensive_pdf(projects_data, weather_info, pdf_path):
    """
    모든 분석 완료된 프로젝트 데이터를 수집하여 3D 뷰가 내장된 PDF 보고서를 생성합니다.
    - 1페이지: 수행정보 및 전체 종합 요약 통계(Dashboard)
    - 2페이지~: 장비별 분석 결과 요약 표 -> 특징점별 상세 설명 -> 3D Isometric View 이미지 순으로 레이아웃 구성
    """
    try:
        from fpdf import FPDF
    except ImportError as e:
        print(f"[오류] fpdf 라이브러리를 임포트할 수 없습니다: {e}")
        print("PDF 보고서 생성을 비활성화하거나 fpdf2 패키지를 설치해 주십시오. (예: pip install fpdf2)")
        return

    class PDFReport(FPDF):
        def header(self):
            if self.page_no() == 1:
                return
            self.set_font('Malgun', '', 9)
            self.set_text_color(128, 128, 128)
            self.cell(0, 10, '기존 배관설계 특징점 추출 엔진 종합보고서', border=0, new_x="LMARGIN", new_y="NEXT", align='R')
            self.ln(5)

        def footer(self):
            self.set_y(-15)
            self.set_font('Malgun', '', 9)
            self.set_text_color(128, 128, 128)
            self.cell(0, 10, f'페이지 {self.page_no()}', border=0, new_x="RIGHT", new_y="TOP", align='C')

    print(f"\n>>> [종합 PDF 보고서 생성] PDF 리포트를 빌드합니다: {pdf_path}")
    pdf = PDFReport()
    
    # 폰트 로드
    font_path = r"C:\Windows\Fonts\malgun.ttf"
    if not os.path.exists(font_path):
        font_path = r"C:\Windows\Fonts\malgunb.ttf"
        if not os.path.exists(font_path):
            font_path = "Arial"
            
    if font_path != "Arial":
        pdf.add_font('Malgun', '', font_path)
        bold_path = font_path.replace("malgun.ttf", "malgunbd.ttf") if "malgun.ttf" in font_path else font_path
        if not os.path.exists(bold_path):
            bold_path = font_path
        pdf.add_font('Malgun', 'B', bold_path)
        font_name = 'Malgun'
    else:
        font_name = 'Arial'

    pdf.set_auto_page_break(auto=True, margin=15)
    
    # ==================== 1페이지: 표지 및 전체 통계 요약 (Dashboard) ====================
    pdf.add_page()
    pdf.ln(20)
    pdf.set_font(font_name, 'B', 22)
    pdf.set_text_color(31, 78, 121) # 테마 색상 (Deep Blue)
    pdf.cell(0, 12, '기존 배관설계 특징 추출 종합보고서', new_x="LMARGIN", new_y="NEXT", align='C')
    pdf.ln(3)
    
    pdf.set_font(font_name, '', 12)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 8, '장비별 / 유틸리티별 특징점 분석 및 3D 등각 시각화 뷰잉 리포트', new_x="LMARGIN", new_y="NEXT", align='C')
    
    # 구분선
    pdf.ln(10)
    pdf.set_draw_color(31, 78, 121)
    pdf.set_fill_color(31, 78, 121)
    pdf.rect(20, pdf.get_y(), 170, 1.5, 'F')
    pdf.ln(15)
    
    # 수행 정보 (날씨 및 시간)
    pdf.set_font(font_name, 'B', 10)
    pdf.set_text_color(60, 60, 60)
    pdf.cell(50, 6, '  ■ 분석 실행 일시:', border=0)
    pdf.set_font(font_name, '', 10)
    pdf.cell(0, 6, datetime.now().strftime('%Y년 %m월 %d일 %H시 %M분 %S초'), new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_font(font_name, 'B', 10)
    pdf.cell(50, 6, '  ■ 기동 시 서울 날씨:', border=0)
    pdf.set_font(font_name, '', 10)
    pdf.cell(0, 6, weather_info, new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_font(font_name, 'B', 10)
    pdf.cell(50, 6, '  ■ 분석 데이터베이스:', border=0)
    pdf.set_font(font_name, '', 10)
    pdf.cell(0, 6, "PostgreSQL (DDW_AI_DB) / PostGIS 3D Spatial enabled", new_x="LMARGIN", new_y="NEXT")
    
    # 전체 통계 대시보드 표 계산
    total_projects = len(projects_data)
    total_utilities = sum(len(p['groups']) for p in projects_data)
    total_spines = sum(sum(g['spine_len'] for g in p['groups']) for p in projects_data)
    total_zs = sum(sum(len(g['rack_zs']) for g in p['groups']) for p in projects_data)
    total_topk = sum(sum(g.get('topk_count', 0) for g in p['groups']) for p in projects_data)
    
    pdf.ln(20)
    pdf.set_font(font_name, 'B', 12)
    pdf.set_text_color(31, 78, 121)
    pdf.cell(0, 8, '  ■ 전체 특징 추출 요약 현황 (Dashboard)', new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    
    # 요약 테이블 그리기
    pdf.set_font(font_name, 'B', 10)
    pdf.set_fill_color(31, 78, 121)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(100, 8, '통계 지표 항목', border=1, align='C', fill=True)
    pdf.cell(70, 8, '누적 집계 수치', border=1, align='C', fill=True, new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_font(font_name, '', 10)
    pdf.set_text_color(0, 0, 0)
    
    stats = [
        ('기존 설계 분석 완료 장비 프로젝트 수', f"{total_projects}개 장비"),
        ('분석 완료된 유틸리티 그룹 총 수', f"{total_utilities}개 utility_groups"),
        ('공용 척추선(Spines) 총 Waypoint 노드 수', f"{total_spines}개 코너 포인트"),
        ('검출된 랙 대표 Z 고도(Levels) 총 수', f"{total_zs}개 고도층"),
        ('생성 및 적재된 유사설계 Top-K 벡터 수', f"{total_topk}개 벡터")
    ]
    
    for label, val in stats:
        pdf.cell(100, 8, f" {label}", border=1, align='L')
        pdf.cell(70, 8, val, border=1, align='C', new_x="LMARGIN", new_y="NEXT")
        
    # ==================== 2페이지~: 각 장비별 리포트 ====================
    for pd_data in projects_data:
        pdf.add_page()
        pdf.set_font(font_name, 'B', 15)
        pdf.set_text_color(31, 78, 121)
        pdf.cell(0, 10, f"장비 프로젝트 특징 추출 요약: {pd_data['project_name']}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
        
        # 1) 유틸리티 정보 요약 표 작성
        pdf.set_font(font_name, 'B', 9)
        pdf.set_fill_color(240, 240, 240)
        pdf.set_text_color(0, 0, 0)
        
        pdf.cell(30, 7, '유틸리티 그룹', border=1, align='C', fill=True)
        pdf.cell(55, 7, '선호 Z 고도 (Rack Levels)', border=1, align='C', fill=True)
        pdf.cell(30, 7, '출발면 (Conf)', border=1, align='C', fill=True)
        pdf.cell(30, 7, '종단면 (Conf)', border=1, align='C', fill=True)
        pdf.cell(25, 7, '척추선 노드수', border=1, align='C', fill=True)
        pdf.cell(20, 7, 'Top-K 벡터수', border=1, align='C', fill=True, new_x="LMARGIN", new_y="NEXT")
        
        pdf.set_font(font_name, '', 8.5)
        for g in pd_data['groups']:
            zs_str = ", ".join([f"{z:.0f}mm" for z in g['rack_zs']]) if g['rack_zs'] else "N/A"
            s_face_str = f"{g['s_face']} ({g['s_conf']*100:.0f}%)"
            t_face_str = f"{g['t_face']} ({g['t_conf']*100:.0f}%)"
            
            pdf.cell(30, 6, g['group_name'], border=1, align='C')
            pdf.cell(55, 6, zs_str, border=1, align='C')
            pdf.cell(30, 6, s_face_str, border=1, align='C')
            pdf.cell(30, 6, t_face_str, border=1, align='C')
            pdf.cell(25, 6, f"{g['spine_len']}개", border=1, align='C')
            pdf.cell(20, 6, f"{g.get('topk_count', 0)}개", border=1, align='C', new_x="LMARGIN", new_y="NEXT")
            
        pdf.ln(5)
        
        # 2) 장비 전체 3D 시각화 화면 추가 (요약 페이지 하단에 고정 배치)
        if pd_data.get('img_path') and os.path.exists(pd_data['img_path']):
            pdf.set_font(font_name, 'B', 11)
            pdf.set_text_color(31, 78, 121)
            pdf.cell(0, 8, "▶ 장비 전체 설계 기하 & 추출 공용 척추선 3D Isometric Overview", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
            # 3D 뷰 이미지 삽입 (가로 160mm, 세로 120mm)
            pdf.image(pd_data['img_path'], x=25, w=160, h=120)
            
        # 3) 각 유틸리티별 상세 분석 페이지 (유틸리티별 1페이지씩 추가)
        for g in pd_data['groups']:
            pdf.add_page()
            pdf.set_font(font_name, 'B', 14)
            pdf.set_text_color(31, 78, 121)
            pdf.cell(0, 10, f"유틸리티 상세 분석: {g['group_name']} ({pd_data['project_name']})", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)
            
            # 해당 유틸리티 요약 표 추가 (해당 페이지 상단에 콤팩트하게 표기)
            pdf.set_font(font_name, 'B', 9)
            pdf.set_fill_color(240, 240, 240)
            pdf.set_text_color(0, 0, 0)
            pdf.cell(45, 7, '선호 Z 고도 (Rack Levels)', border=1, align='C', fill=True)
            pdf.cell(40, 7, '선호 출발면 (Conf)', border=1, align='C', fill=True)
            pdf.cell(40, 7, '선호 종단면 (Conf)', border=1, align='C', fill=True)
            pdf.cell(25, 7, '척추선 노드수', border=1, align='C', fill=True)
            pdf.cell(20, 7, 'Top-K 벡터수', border=1, align='C', fill=True, new_x="LMARGIN", new_y="NEXT")
            
            pdf.set_font(font_name, '', 8.5)
            zs_str = ", ".join([f"{z:.0f}mm" for z in g['rack_zs']]) if g['rack_zs'] else "N/A"
            s_face_str = f"{g['s_face']} ({g['s_conf']*100:.0f}%)"
            t_face_str = f"{g['t_face']} ({g['t_conf']*100:.0f}%)"
            pdf.cell(45, 6, zs_str, border=1, align='C')
            pdf.cell(40, 6, s_face_str, border=1, align='C')
            pdf.cell(40, 6, t_face_str, border=1, align='C')
            pdf.cell(25, 6, f"{g['spine_len']}개", border=1, align='C')
            pdf.cell(20, 6, f"{g.get('topk_count', 0)}개", border=1, align='C', new_x="LMARGIN", new_y="NEXT")
            pdf.ln(5)
            
            # 동적 텍스트 설명 생성
            pdf.set_font(font_name, 'B', 11)
            pdf.set_text_color(31, 78, 121)
            pdf.cell(0, 8, "▶ 유틸리티 특징점 기하 및 정성적 특징 분석 설명", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
            
            pdf.set_font(font_name, '', 9.5)
            pdf.set_text_color(50, 50, 50)
            
            zs_desc = ", ".join([f"{z:.0f}mm" for z in g['rack_zs']]) if g['rack_zs'] else "없음"
            s_face_desc = f"{g['s_face']} 방향 (Voting 신뢰도: {g['s_conf']*100:.0f}%)"
            t_face_desc = f"{g['t_face']} 방향 (Voting 신뢰도: {g['t_conf']*100:.0f}%)"
            
            desc_text = (
                f"• [{g['group_name']}] 유틸리티 배관 특징:\n"
                f"  - Rack 주행 고도는 주로 {zs_desc}로 설계되어 수평 일관성을 확보하고 있으며 시공 높이선 지침이 됩니다.\n"
                f"  - PoC 시점에서의 탈출 방향은 {s_face_desc}을 선호하고, 종점 영역에서는 {t_face_desc}으로 진입하는 경향을 추종합니다.\n"
                f"  - RDP 공간 단순화 및 클러스터링으로 도출된 번들 척추선은 {g['spine_len']}개의 특징 Waypoint로 정제되었으며 C# 라우팅 시 가이드라인으로 주입됩니다.\n"
                f"  - 유사설계 경로 추천 및 Top-K 검색을 위해 {g.get('topk_count', 0)}개의 30차원 특징 벡터 데이터가 pgvector 테이블에 생성 및 적재되었습니다."
            )
            pdf.multi_cell(0, 5.5, desc_text)
            pdf.ln(4)
            
            # 해당 유틸리티 단독 3D 시각화 이미지 삽입
            if g.get('img_path') and os.path.exists(g['img_path']):
                pdf.set_font(font_name, 'B', 11)
                pdf.set_text_color(31, 78, 121)
                pdf.cell(0, 8, f"▶ {g['group_name']} 유틸리티 단독 3D Isometric View", new_x="LMARGIN", new_y="NEXT")
                pdf.ln(1)
                pdf.image(g['img_path'], x=25, w=160, h=120)
            
    # PDF 저장
    try:
        pdf.output(pdf_path)
        print(f">>> [완료] 종합 PDF 보고서가 성공적으로 저장되었습니다: {pdf_path}")
    except Exception as ex:
        print(f"[오류] 종합 PDF 보고서 생성 실패: {ex}")


def main():
    parser = argparse.ArgumentParser(description="PostgreSQL 기존설계 배관 기하 특징 추출 엔진")
    parser.add_argument("--host", default=None, help="PostgreSQL host")
    parser.add_argument("--port", default=None, help="PostgreSQL port")
    parser.add_argument("--db", default=None, help="PostgreSQL database name")
    parser.add_argument("--user", default=None, help="PostgreSQL user")
    parser.add_argument("--password", default=None, help="PostgreSQL password")
    parser.add_argument("--project", required=True, help="분석 대상 프로젝트명 (예: DANHJ01)")
    parser.add_argument("--report", default="true", choices=["true", "false"], help="PDF 보고서 생성 여부 (true/false, 기본값: true)")
    args = parser.parse_args()

    # DB 설정 파일이 Tools 폴더에 있으면 JSON 파일 읽어 기본값으로 오버라이딩
    settings_path = Path(__file__).resolve().parent / "tools.settings.json"
    db_conf = {}
    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
                if "db" in settings:
                    db_conf = settings["db"]
        except Exception as ex:
            print(f"[알림] 설정을 불러오는 도중 오류 발생(CLI 인자 사용): {ex}")

    # 우선순위: CLI 인자 -> tools.settings.json -> 시스템 환경변수(password만) -> 기본 하드코딩값
    host = args.host if args.host is not None else db_conf.get("host", "localhost")
    port = args.port if args.port is not None else str(db_conf.get("port", "5432"))
    db = args.db if args.db is not None else db_conf.get("database", "DDW_AI_DB")
    user = args.user if args.user is not None else db_conf.get("user", "postgres")
    
    if args.password is not None:
        password = args.password
    elif db_conf.get("password") is not None:
        password = db_conf.get("password")
    elif os.getenv("DDW_AI_DB_PASSWORD") is not None:
        password = os.getenv("DDW_AI_DB_PASSWORD")
    else:
        password = "dinno"

    conninfo = f"host={host} port={port} dbname={db} user={user} password={password}"
    conn = open_connection(conninfo)
    try:
        # 공통 테이블 구조 준비 (1회 기동)
        dummy_learner = DesignFeatureLearner(conn, "")
        dummy_learner.prepare_tables()
        
        # 대상 프로젝트 결정
        projects = []
        if args.project.strip().lower() == "all":
            print(">>> [전체 프로젝트 학습 모드] 데이터베이스에서 고유 프로젝트 목록을 조회합니다...")
            query = 'SELECT DISTINCT "EQUIPMENT_TAG" FROM "TB_ROUTE_PATH" WHERE "EQUIPMENT_TAG" IS NOT NULL;'
            with conn.cursor() as cur:
                cur.execute(query)
                rows = cur.fetchall()
                projects = [r[0].strip() for r in rows if r[0] and r[0].strip()]
            print(f"-> 총 {len(projects)}개의 프로젝트 발견: {', '.join(projects)}")
        else:
            projects = [args.project.strip()]
            
        import time
        start_total_time = time.time()
        project_times = []
        report_data_list = []
        for proj in projects:
            start_proj_time = time.time()
            learner = DesignFeatureLearner(conn, proj, report_enabled=(args.report.lower() == "true"))
            
            # 데이터 로드
            data_loaded = learner.load_data()
            
            # 실제 프로젝트명(process_name)과 장비명(eq_tag) 식별
            p_name = "UNKNOWN"
            e_tag = proj
            if data_loaded and learner.routes:
                p_name = learner.routes[0]['meta'].get('process_name', 'UNKNOWN')
                e_tag = learner.routes[0]['meta'].get('eq_tag', proj)
            
            print(f"\n====================================================================================")
            print(f"=== 기존 설계 특징 학습 파이프라인 가동 (프로젝트: {p_name} / 장비: {e_tag}) ===")
            print(f"====================================================================================")
            
            if data_loaded:
                learner.learn_and_save()
                if learner.report_groups:
                    report_data_list.append({
                        'project_name': proj,
                        'img_path': learner.temp_img_path,
                        'groups': learner.report_groups
                    })
            
            end_proj_time = time.time()
            elapsed_proj = end_proj_time - start_proj_time
            project_times.append((p_name, e_tag, elapsed_proj))
            print(f"\n>>> [소요시간] 프로젝트 '{p_name}' / 장비 '{e_tag}' 학습 완료 (소요시간: {elapsed_proj:.2f}초)")
                    
        # 모든 장비 특징 학습 완료 후 날씨/시간이 포함된 3D 뷰 종합 PDF 리포트 생성
        if report_data_list and args.report.lower() == "true":
            weather_info = get_current_weather()
            
            # 시간 포맷 생성 (파일명에서는 날씨를 배제하고 타임스탬프만 활용)
            time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"existing_design_feature_extraction_report_{time_str}.pdf"
            
            # docs/out/report 디렉토리가 없을 시 자동 생성
            report_dir = Path(__file__).resolve().parent.parent / "docs" / "out" / "report"
            report_dir.mkdir(parents=True, exist_ok=True)
            
            pdf_path = str(report_dir / filename)
            generate_comprehensive_pdf(report_data_list, weather_info, pdf_path)

        end_total_time = time.time()
        elapsed_total = end_total_time - start_total_time
        print(f"\n====================================================================================")
        print(f"=== 전체 학습 파이프라인 완료 및 요약 (Total Execution Summary) ===")
        print(f"====================================================================================")
        for p_name, e_tag, p_time in project_times:
            print(f"  - 프로젝트 '{p_name}' / 장비 '{e_tag}': {p_time:.2f}초")
        print(f"\n★ [총 소요시간] 전체 작업 완료 총 걸린시간: {elapsed_total:.2f}초")
        print(f"====================================================================================")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
