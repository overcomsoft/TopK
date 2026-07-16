#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Extract_Design_Pattern.py

기존 배관 설계 데이터(DDW_AI_DB)를 분석하여 Z축 선호 고도(Rack Levels), 
PoC 말단 접속면(Face), 공용 척추선(Trunk Spine), 장애물 회피 경향 등의 통계 특징 프로필과
유사설계 Top-K 검색을 위한 30차원 특징 벡터 데이터베이스(pgvector)를 구성하여 영속화하는 파이썬 분석 스크립트입니다.

====================================================================================================
[ 실행 명령어 샘플 ]
====================================================================================================
1) 로컬 PostgreSQL DB에 기본 접속 정보로 특정 프로젝트(DANHJ01) 특징점 학습 실행:
   python ./tools/learn_design_features.py --project DANHJ01

2) 데이터베이스 내의 전체 프로젝트를 대상으로 일괄 특징점 학습 및 적재 실행:
   python ./tools/Extract_Design_Pattern.py --project all

   python ./tools/Extract_Design_Pattern.py --host 192.168.0.175 --port 55432 --db DDW_AI_DB --user dinno --password dinno --project all


3) 외부 DB 정보(IP, Port, ID, Pwd, DB명)를 명시적으로 전달하여 학습 실행:
   python ./tools/Extract_Design_Pattern.py --host 192.168.0.50 --port 5432 --db DDW_AI_DB --user postgres --password password123 --project DANHJ01

4) 로컬 PostgreSQL DB의 특정 프로젝트(CHILLER 002)를 대상으로 명시적 DB 인자 전달하여 학습 실행:
   python ./Tools/Extract_Design_Pattern.py --project "CHILLER 002" --password "dinno" --user "postgres" --host "localhost"

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
import types

# -----------------------------------------------------------------------------
# Embedded dependency modules
# -----------------------------------------------------------------------------
# Extract_Design_Pattern.py is intentionally self-contained for design-pattern
# extraction. The former external modules ExtractStubPatterns.py and
# ExtractVerticalGroup.py are embedded below and loaded into module-like
# namespaces so the existing pipeline can continue to call
# stub_patterns.extract_samples(...) and vertical_group.extract_and_save_vertical_groups(...)
# without importing separate source files.

def _load_embedded_module(module_name: str, source: str):
    module = types.ModuleType(module_name)
    module.__file__ = __file__
    module.__package__ = None
    exec(compile(source, f"<embedded {module_name}>", "exec"), module.__dict__)
    return module

_EMBEDDED_EXTRACT_STUB_PATTERNS_SOURCE = 'from __future__ import annotations\n\n"""\nDDW_AI_DB 기존설계 배관으로부터 Start/End Stub 패턴을 추출, 저장, 집계, 활용하는 CLI 도구.\n\n전체 프로세스\n--------------\n1. create-schema\n   - TB_ROUTE_STUB_PATTERN, TB_ROUTE_STUB_TEMPLATE, TB_ROUTE_STUB_APPLICATION_LOG 테이블을 생성한다.\n   - pgvector 확장이 있으면 FEAT vector(24), DIR_UNIT vector(3) 컬럼과 HNSW 인덱스를 사용한다.\n   - pgvector가 없으면 JSON fallback 스키마를 생성해 최소 기능을 유지한다.\n\n2. extract\n   - TB_ROUTE_PATH, TB_ROUTE_SEGMENTS, TB_ROUTE_SEGMENT_DETAIL을 조인해 route별 3D 폴리라인을 복원한다.\n   - source_pos 쪽은 START Stub, target_pos 쪽은 END Stub으로 역방향 정렬해서 동일 알고리즘을 적용한다.\n   - 각 stub은 anchor AABB 기준 face, 방향열(dir_seq), rise/offset/length, 24D 특징벡터로 변환된다.\n   - 결과는 TB_ROUTE_STUB_PATTERN에 저장된다.\n\n3. build-template\n   - 개별 Stub sample을 메인장비, 유틸리티그룹, 유틸리티, 사이즈, face, dir_seq 기준으로 그룹화한다.\n   - min-samples 이상 반복된 패턴만 TB_ROUTE_STUB_TEMPLATE에 대표 template으로 저장한다.\n\n4. query-template / make-stub\n   - 신규 자동배관설계 요청의 메인장비/유틸리티 조건으로 template을 조회한다.\n   - 조회된 Start/End template을 신규 source/target PoC 좌표에 맞춰 월드 좌표 stub 후보로 복원한다.\n   - 중간 자동 라우터는 start_stub.free_point와 end_stub.free_point를 연결하면 된다.\n\n주요 실행 명령\n--------------\n스키마 생성:\n    python Tools\\\\ExtractStubPatterns.py create-schema --config Tools\\\\tools.settings.json\n\n기존 배관에서 Stub 추출:\n    python Tools\\\\ExtractStubPatterns.py extract --config Tools\\\\tools.settings.json --limit 100 --dry-run\n\n추출 + 템플릿 집계 일괄 실행:\n    python Tools\\\\ExtractStubPatterns.py run-all --config Tools\\\\tools.settings.json --min-samples 3 --replace\n\n신규 자동설계용 Stub 후보 생성:\n    python Tools\\\\ExtractStubPatterns.py make-stub --config Tools\\\\tools.settings.json \\\\\n      --main-equipment WTNHJ02 --utility-group Water --utility PCWS \\\\\n      --source-pos 1000,2000,3000 --target-pos 7000,9000,4500 --max-candidates 5\n\n핵심 알고리즘 요약\n------------------\n- axis_snap: 임의 3D 벡터를 +x,-x,+y,-y,+z,-z 중 지배축 방향으로 스냅한다.\n- dir_runs: 폴리라인 세그먼트를 방향 run으로 압축한다.\n- merge_short_runs: 250mm 미만 run을 설계 지터로 보고 인접 run에 흡수한다.\n- walk_stub: 첫 run의 축을 수직축으로 보고, 축이 바뀌는 첫 run을 엘보로 포함해 stub을 자른다.\n- build_feature: face(6D), 1차 방향(6D), 2차 방향(6D), anchor 내부 상대좌표(3D), 진행방향(3D)을 합쳐 24D feature를 만든다.\n"""\n\nimport argparse\nimport hashlib\nimport json\nimport math\nimport os\nimport sys\nimport uuid\nfrom collections import Counter, defaultdict\nfrom dataclasses import dataclass\nfrom pathlib import Path\nfrom typing import Any, Iterable\n\nimport psycopg2\nimport psycopg2.extras\n\nfrom tool_config import add_common_args, print_runtime, resolve_runtime\n\n\n# 6축 방향 인덱스 규약.\n# 인덱스는 PDF 문서의 axis_snap 규칙과 맞춘다.\n# 0:+x, 1:-x, 2:+y, 3:-y, 4:+z, 5:-z\nAXIS_NAMES = ["+x", "-x", "+y", "-y", "+z", "-z"]\nAXIS_VECTORS = [\n    (1.0, 0.0, 0.0),\n    (-1.0, 0.0, 0.0),\n    (0.0, 1.0, 0.0),\n    (0.0, -1.0, 0.0),\n    (0.0, 0.0, 1.0),\n    (0.0, 0.0, -1.0),\n]\n# 250mm 미만 방향 run은 설계상 의미 있는 꺾임이 아니라 BIM 지터로 보고 병합한다.\nSTUB_MIN_DIR_RUN_MM = 250.0\n# 첫 엘보 방향으로 포함할 최대 수평/다음축 리드인 길이.\nSTUB_LEADIN_MM = 800.0\n# Stub이 과도하게 긴 중앙 자유공간 run까지 먹지 않도록 하는 안전 상한.\nSTUB_MAX_MM = 4000.0\n# dir_seq에 보관할 최대 bend 수. 현재 알고리즘은 첫 엘보 중심이므로 넉넉한 방어값이다.\nSTUB_MAX_BENDS = 3\n# PoC가 AABB 내부에 없을 때 anchor로 인정할 최대 최근접 거리.\nANCHOR_MAX_MM = 3000.0\n# route_stub_pattern.feat 및 route_stub_template.avg_feat와 일치해야 하는 차원.\nFEAT_DIM = 24\n\n\n@dataclass\nclass Anchor:\n    """Stub이 붙는 기준 객체.\n\n    kind:\n        EQUIP, DUCT, LATERAL 중 하나. START Stub은 주로 EQUIP, END Stub은 DUCT/LATERAL에 붙는다.\n    name:\n        장비/덕트/레터럴 이름. 패턴 조회와 로그에서 사람이 식별하기 위한 값이다.\n    utility:\n        Anchor가 가진 유틸리티 코드. End Stub anchor 매칭 시 보조 필터로 사용한다.\n    min_pt/max_pt:\n        Anchor AABB의 최소/최대 좌표. face 판정, 상대좌표, rise/offset 계산 기준이다.\n    """\n    kind: str\n    name: str\n    utility: str | None\n    min_pt: tuple[float, float, float]\n    max_pt: tuple[float, float, float]\n\n\n@dataclass\nclass RouteRecord:\n    """기존 설계 배관 1개를 route 단위로 복원한 메모리 모델.\n\n    TB_ROUTE_PATH의 메타데이터와 TB_ROUTE_SEGMENT_DETAIL에서 복원한 중심선 폴리라인을 함께 담는다.\n    extract 단계는 RouteRecord 하나에서 START/END 최대 2개의 StubSample을 만든다.\n    """\n    guid: str\n    process_name: str | None\n    equipment_name: str | None\n    utility_group: str | None\n    utility: str | None\n    size: str | None\n    source_pos: tuple[float, float, float] | None\n    target_pos: tuple[float, float, float] | None\n    points: list[tuple[float, float, float]]\n\n\n@dataclass\nclass StubSample:\n    """DB에 저장되는 개별 Stub 패턴 샘플.\n\n    하나의 ROUTE_PATH_GUID에서 START 또는 END 한쪽 끝을 잘라낸 결과다.\n    `feat`는 pgvector 검색/집계용 24D 특징벡터이고, `stub_points`는 실제 월드 좌표 점열이다.\n    """\n    pattern_id: str\n    route_path_guid: str\n    stub_kind: str\n    anchor_kind: str\n    anchor_name: str | None\n    main_equipment_name: str | None\n    process_name: str | None\n    utility_group: str | None\n    utility: str | None\n    size: str | None\n    face: str\n    dir_seq: list[str]\n    n_bends: int\n    rise_mm: float\n    offset_mm: float\n    diameter_mm: float | None\n    stub_length_mm: float\n    source_pos: tuple[float, float, float] | None\n    target_pos: tuple[float, float, float] | None\n    anchor_min: tuple[float, float, float]\n    anchor_max: tuple[float, float, float]\n    stub_points: list[tuple[float, float, float]]\n    feat: list[float]\n    dir_unit: list[float]\n\n\ndef main() -> int:\n    """CLI 진입점.\n\n    Subcommand별 역할:\n    - create-schema: 저장 테이블 생성\n    - extract: 기존 설계 배관에서 Stub sample 추출\n    - build-template: sample을 그룹화해 재사용 template 생성\n    - query-template: 조건에 맞는 template 조회\n    - make-stub: 신규 자동배관설계 입력 좌표에 template을 적용해 Stub 후보 생성\n    - run-all: schema 생성, extract, build-template 순차 실행\n    - validate-existing-route: 특정 기존 route 하나의 Stub 추출 결과 확인\n    """\n    parser = argparse.ArgumentParser(\n        description="Extract, store, query, and apply Start/End stub routing patterns from DDW_AI_DB."\n    )\n    sub = parser.add_subparsers(dest="command", required=True)\n\n    for name in ["create-schema", "extract", "build-template", "query-template", "make-stub", "run-all", "validate-existing-route"]:\n        p = sub.add_parser(name)\n        add_common_args(p)\n\n    extract = sub.choices["extract"]\n    add_filter_args(extract)\n    extract.add_argument("--limit", type=int, default=None)\n    extract.add_argument("--dry-run", action="store_true")\n    extract.add_argument("--export-json", default=None)\n    extract.add_argument("--replace", action="store_true", help="Delete existing samples matching current filters before insert")\n\n    tmpl = sub.choices["build-template"]\n    add_filter_args(tmpl)\n    tmpl.add_argument("--min-samples", type=int, default=3)\n    tmpl.add_argument("--replace", action="store_true")\n\n    query = sub.choices["query-template"]\n    add_request_args(query)\n    query.add_argument("--stub-kind", choices=["START", "END"], default=None)\n    query.add_argument("--max-candidates", type=int, default=10)\n    query.add_argument("--export-json", default=None)\n\n    make = sub.choices["make-stub"]\n    add_request_args(make)\n    make.add_argument("--source-pos", required=True)\n    make.add_argument("--target-pos", required=True)\n    make.add_argument("--source-anchor-min", default=None)\n    make.add_argument("--source-anchor-max", default=None)\n    make.add_argument("--target-anchor-min", default=None)\n    make.add_argument("--target-anchor-max", default=None)\n    make.add_argument("--max-candidates", type=int, default=5)\n    make.add_argument("--export-json", default=None)\n    make.add_argument("--log-application", action="store_true")\n    make.add_argument("--request-id", default=None)\n\n    run_all = sub.choices["run-all"]\n    add_filter_args(run_all)\n    run_all.add_argument("--limit", type=int, default=None)\n    run_all.add_argument("--min-samples", type=int, default=3)\n    run_all.add_argument("--replace", action="store_true")\n    run_all.add_argument("--export-json", default=None)\n\n    valid = sub.choices["validate-existing-route"]\n    valid.add_argument("--route-path-guid", required=True)\n    valid.add_argument("--export-json", default=None)\n\n    args = parser.parse_args()\n    try:\n        runtime = resolve_runtime(args)\n    except FileNotFoundError as ex:\n        raise SystemExit(str(ex)) from ex\n    print_runtime(runtime)\n\n    with open_connection(runtime.conninfo) as conn:\n        if args.command == "create-schema":\n            create_schema(conn)\n        elif args.command == "extract":\n            samples = extract_samples(conn, args)\n            emit_extract_result(conn, args, samples)\n        elif args.command == "build-template":\n            templates = build_templates(conn, args)\n            print(f"Built templates: {len(templates)}")\n        elif args.command == "query-template":\n            rows = query_templates(conn, args)\n            print_json_or_table(rows, args.export_json)\n        elif args.command == "make-stub":\n            result = make_stub_candidates(conn, args)\n            if args.log_application:\n                log_application(conn, args, result)\n            print_json_or_table(result, args.export_json)\n        elif args.command == "run-all":\n            create_schema(conn)\n            samples = extract_samples(conn, args)\n            emit_extract_result(conn, args, samples)\n            templates = build_templates(conn, args)\n            print(f"Run-all complete. samples={len(samples)}, templates={len(templates)}")\n        elif args.command == "validate-existing-route":\n            result = validate_existing_route(conn, args)\n            print_json_or_table(result, args.export_json)\n        else:\n            raise ValueError(args.command)\n    return 0\n\n\ndef open_connection(conninfo: str):\n    """PostgreSQL 연결을 생성한다.\n\n    psycopg2/libpq는 Windows에서 접속 실패 메시지가 CP949 등 비 UTF-8로 돌아올 때\n    UnicodeDecodeError를 낼 수 있다. 이 함수는 그 경우 사용자가 실제 원인(설정 파일 누락,\n    비밀번호 오류 등)을 바로 알 수 있도록 SystemExit 메시지로 바꾼다.\n    """\n    try:\n        return psycopg2.connect(conninfo)\n    except UnicodeDecodeError as ex:\n        raise SystemExit(\n            "DB connection failed, and libpq returned a non-UTF-8 error message.\\n"\n            "Most likely causes:\\n"\n            "  - Tools/tools.settings.json does not exist or has an empty/wrong password.\\n"\n            "  - The DB name/user/password is incorrect.\\n"\n            "Fix:\\n"\n            "  1) Copy Tools/tools.settings.example.json to Tools/tools.settings.json and fill the password, or\\n"\n            "  2) Pass --host --port --dbname --user --password explicitly.\\n"\n            f"Raw decode error: {ex}"\n        ) from ex\n    except psycopg2.OperationalError as ex:\n        raise SystemExit(f"DB connection failed: {ex}") from ex\n\n\ndef add_filter_args(parser: argparse.ArgumentParser) -> None:\n    """extract/build-template/run-all에서 쓰는 공통 필터 인자를 추가한다."""\n    parser.add_argument("--main-equipment", default=None)\n    parser.add_argument("--utility-group", default=None)\n    parser.add_argument("--utility", default=None)\n    parser.add_argument("--size", default=None)\n\n\ndef add_request_args(parser: argparse.ArgumentParser) -> None:\n    """신규 자동설계 요청 계열(query-template/make-stub)의 필수 조건 인자를 추가한다."""\n    parser.add_argument("--main-equipment", required=True)\n    parser.add_argument("--utility-group", required=True)\n    parser.add_argument("--utility", default=None)\n    parser.add_argument("--size", default=None)\n\n\ndef create_schema(conn) -> None:\n    """Stub 패턴 저장용 DB 스키마를 생성한다.\n\n    pgvector가 설치되어 있으면 Tools/sql/create_route_stub_pattern_tables.sql을 실행한다.\n    pgvector가 없으면 vector 컬럼 없이 JSON 컬럼만 가진 fallback schema를 생성한다.\n    """\n    has_vector = pgvector_installed(conn)\n    with conn.cursor() as cur:\n        if has_vector:\n            sql_path = Path(__file__).resolve().parent / "sql" / "create_route_stub_pattern_tables.sql"\n            cur.execute(sql_path.read_text(encoding="utf-8"))\n        else:\n            print("[warn] pgvector extension not found. Creating JSON fallback columns only.")\n            cur.execute(fallback_schema_sql())\n    conn.commit()\n    print(f"Schema ready. pgvector={\'yes\' if has_vector else \'no\'}")\n\n\ndef fallback_schema_sql() -> str:\n    """pgvector가 없는 DB에서도 extract/query가 동작하도록 만드는 fallback DDL."""\n    return """\nCREATE TABLE IF NOT EXISTS "TB_ROUTE_STUB_PATTERN" (\n    "PATTERN_ID" text PRIMARY KEY,\n    "ROUTE_PATH_GUID" text NOT NULL,\n    "STUB_KIND" text NOT NULL,\n    "ANCHOR_KIND" text NOT NULL,\n    "ANCHOR_NAME" text,\n    "MAIN_EQUIPMENT_NAME" text,\n    "PROCESS_NAME" text,\n    "UTILITY_GROUP" text,\n    "UTILITY" text,\n    "SIZE" text,\n    "FACE" text,\n    "DIR_SEQ" text,\n    "N_BENDS" integer,\n    "RISE_MM" double precision,\n    "OFFSET_MM" double precision,\n    "DIAMETER_MM" double precision,\n    "STUB_LENGTH_MM" double precision,\n    "SOURCE_POS" jsonb,\n    "TARGET_POS" jsonb,\n    "ANCHOR_MIN" jsonb,\n    "ANCHOR_MAX" jsonb,\n    "STUB_POINTS" jsonb,\n    "FEAT_JSON" jsonb,\n    "DIR_UNIT_JSON" jsonb,\n    "CREATED_AT" timestamp without time zone DEFAULT now()\n);\nCREATE INDEX IF NOT EXISTS "IX_TRSP_KEY"\nON "TB_ROUTE_STUB_PATTERN" ("MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "SIZE", "STUB_KIND", "ANCHOR_KIND");\nCREATE TABLE IF NOT EXISTS "TB_ROUTE_STUB_TEMPLATE" (\n    "TEMPLATE_ID" text PRIMARY KEY,\n    "STUB_KIND" text NOT NULL,\n    "ANCHOR_KIND" text NOT NULL,\n    "MAIN_EQUIPMENT_NAME" text,\n    "UTILITY_GROUP" text,\n    "UTILITY" text,\n    "SIZE" text,\n    "FACE" text,\n    "DIR_SEQ" text,\n    "SAMPLE_COUNT" integer NOT NULL,\n    "AVG_RISE_MM" double precision,\n    "AVG_OFFSET_MM" double precision,\n    "AVG_DIAMETER_MM" double precision,\n    "AVG_STUB_LENGTH_MM" double precision,\n    "REPRESENTATIVE_PATTERN_ID" text,\n    "REPRESENTATIVE_ROUTE_PATH_GUID" text,\n    "REPRESENTATIVE_STUB_POINTS" jsonb,\n    "AVG_FEAT_JSON" jsonb,\n    "AVG_DIR_UNIT_JSON" jsonb,\n    "CREATED_AT" timestamp without time zone DEFAULT now()\n);\nCREATE INDEX IF NOT EXISTS "IX_TRST_KEY"\nON "TB_ROUTE_STUB_TEMPLATE" ("MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "SIZE", "STUB_KIND", "ANCHOR_KIND");\nCREATE TABLE IF NOT EXISTS "TB_ROUTE_STUB_APPLICATION_LOG" (\n    "APPLICATION_ID" text PRIMARY KEY,\n    "REQUEST_ID" text,\n    "SOURCE_TEMPLATE_ID" text,\n    "TARGET_TEMPLATE_ID" text,\n    "MAIN_EQUIPMENT_NAME" text,\n    "UTILITY_GROUP" text,\n    "UTILITY" text,\n    "SIZE" text,\n    "START_STUB_POINTS" jsonb,\n    "END_STUB_POINTS" jsonb,\n    "MIDDLE_ROUTE_POINTS" jsonb,\n    "FINAL_ROUTE_POINTS" jsonb,\n    "SCORE" double precision,\n    "STATUS" text,\n    "FAIL_REASON" text,\n    "CREATED_AT" timestamp without time zone DEFAULT now()\n);\n"""\n\n\ndef extract_samples(conn, args) -> list[StubSample]:\n    """기존 설계 배관에서 START/END Stub sample을 추출한다.\n\n    처리 흐름:\n    1. fetch_routes로 route path + segment detail 폴리라인을 복원한다.\n    2. fetch_anchors로 START용 메인장비 anchor와 END용 DUCT/LATERAL anchor를 로드한다.\n    3. 각 route에 대해 source_pos는 START, target_pos는 END로 make_sample을 호출한다.\n    4. dry-run이면 저장하지 않고 요약만 출력한다.\n    5. dry-run이 아니면 schema를 보장한 뒤 TB_ROUTE_STUB_PATTERN에 upsert한다.\n    """\n    routes = fetch_routes(conn, args)\n    equip_anchors = fetch_anchors(conn, "EQUIP")\n    end_anchors = fetch_anchors(conn, "DUCT") + fetch_anchors(conn, "LATERAL")\n    print(f"Routes loaded: {len(routes)}")\n    print(f"Equipment anchors: {len(equip_anchors)}, target anchors: {len(end_anchors)}")\n\n    samples: list[StubSample] = []\n    skipped = Counter()\n    for route in routes:\n        if len(route.points) < 2:\n            skipped["too_few_points"] += 1\n            continue\n        if not route.source_pos or not route.target_pos:\n            skipped["missing_source_or_target_pos"] += 1\n            continue\n        start_anchor = find_anchor(equip_anchors, route.source_pos, route.equipment_name, None)\n        end_anchor = find_anchor(end_anchors, route.target_pos, None, route.utility)\n        if not start_anchor:\n            skipped["missing_start_anchor"] += 1\n        else:\n            sample = make_sample(route, "START", start_anchor)\n            if sample:\n                samples.append(sample)\n            else:\n                skipped["start_stub_failed"] += 1\n        if not end_anchor:\n            skipped["missing_end_anchor"] += 1\n        else:\n            sample = make_sample(route, "END", end_anchor)\n            if sample:\n                samples.append(sample)\n            else:\n                skipped["end_stub_failed"] += 1\n\n    print_summary(samples, skipped)\n    if not getattr(args, "dry_run", False):\n        create_schema(conn)\n        if getattr(args, "replace", False):\n            delete_samples(conn, args)\n        insert_samples(conn, samples)\n        conn.commit()\n        print(f"Inserted samples: {len(samples)}")\n    return samples\n\n\ndef emit_extract_result(conn, args, samples: list[StubSample]) -> None:\n    """extract/run-all 결과를 선택적으로 JSON 파일로 내보낸다."""\n    path = getattr(args, "export_json", None)\n    if path:\n        write_json(path, [sample_to_json(s) for s in samples])\n        print(f"Exported samples: {path}")\n\n\ndef fetch_routes(conn, args) -> list[RouteRecord]:\n    """DB에서 기존 route 목록과 폴리라인을 읽어 RouteRecord 목록으로 변환한다.\n\n    컬럼명이 일부 프로젝트마다 다를 수 있어 `first_col`로 후보 컬럼을 자동 선택한다.\n    예: 장비명은 EQUIPMENT_NAME, EQUIPMENT_TAG, SOURCE_OWNER_NAME 순으로 탐색한다.\n    """\n    cols = table_columns(conn, "TB_ROUTE_PATH")\n    required = {"ROUTE_PATH_GUID"}\n    missing = required - cols\n    if missing:\n        raise RuntimeError(f"TB_ROUTE_PATH missing columns: {sorted(missing)}")\n\n    select_map = {\n        "guid": "ROUTE_PATH_GUID",\n        "process_name": first_col(cols, "PROCESS_NAME"),\n        "equipment_name": first_col(cols, "EQUIPMENT_NAME", "EQUIPMENT_TAG", "SOURCE_OWNER_NAME"),\n        "utility_group": first_col(cols, "UTILITY_GROUP"),\n        "utility": first_col(cols, "SOURCE_UTILITY", "UTILITY"),\n        "size": first_col(cols, "SOURCE_SIZE", "SIZE"),\n        "source_posx": first_col(cols, "SOURCE_POSX"),\n        "source_posy": first_col(cols, "SOURCE_POSY"),\n        "source_posz": first_col(cols, "SOURCE_POSZ"),\n        "target_posx": first_col(cols, "TARGET_POSX"),\n        "target_posy": first_col(cols, "TARGET_POSY"),\n        "target_posz": first_col(cols, "TARGET_POSZ"),\n    }\n    sql_cols = []\n    for alias, col in select_map.items():\n        if col:\n            sql_cols.append(f\'rp."{col}" AS "{alias}"\')\n        else:\n            sql_cols.append(f\'NULL AS "{alias}"\')\n\n    where = []\n    params: list[Any] = []\n    if getattr(args, "main_equipment", None) and select_map["equipment_name"]:\n        where.append(f\'rp."{select_map["equipment_name"]}" ILIKE %s\')\n        params.append(f"%{args.main_equipment}%")\n    if getattr(args, "utility_group", None) and select_map["utility_group"]:\n        where.append(f\'rp."{select_map["utility_group"]}" = %s\')\n        params.append(args.utility_group)\n    if getattr(args, "utility", None) and select_map["utility"]:\n        where.append(f\'rp."{select_map["utility"]}" = %s\')\n        params.append(args.utility)\n    if getattr(args, "size", None) and select_map["size"]:\n        where.append(f\'rp."{select_map["size"]}" = %s\')\n        params.append(args.size)\n    where_sql = "WHERE " + " AND ".join(where) if where else ""\n    limit_sql = ""\n    if getattr(args, "limit", None):\n        limit_sql = "LIMIT %s"\n        params.append(args.limit)\n\n    route_sql = f\'\'\'\n        SELECT {", ".join(sql_cols)}\n        FROM "TB_ROUTE_PATH" rp\n        {where_sql}\n        ORDER BY rp."ROUTE_PATH_GUID"\n        {limit_sql}\n    \'\'\'\n\n    routes: list[RouteRecord] = []\n    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:\n        cur.execute(route_sql, params)\n        for row in cur.fetchall():\n            guid = str(row["guid"]).strip()\n            points = fetch_route_points(conn, guid)\n            routes.append(RouteRecord(\n                guid=guid,\n                process_name=row.get("process_name"),\n                equipment_name=row.get("equipment_name"),\n                utility_group=row.get("utility_group"),\n                utility=row.get("utility"),\n                size=row.get("size"),\n                source_pos=triple(row.get("source_posx"), row.get("source_posy"), row.get("source_posz")),\n                target_pos=triple(row.get("target_posx"), row.get("target_posy"), row.get("target_posz")),\n                points=points,\n            ))\n    return routes\n\n\ndef fetch_route_points(conn, guid: str) -> list[tuple[float, float, float]]:\n    """ROUTE_PATH_GUID 하나의 중심선 폴리라인을 복원한다.\n\n    TB_ROUTE_SEGMENTS.ORDER, TB_ROUTE_SEGMENT_DETAIL.ORDER 순서로 FROM/TO 좌표를 이어붙인다.\n    이전 점과 다음 FROM이 살짝 어긋난 경우에도 점열을 유지하되, 완전 중복점은 제거한다.\n    """\n    sql = \'\'\'\n        SELECT sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",\n               sd."TO_POSX", sd."TO_POSY", sd."TO_POSZ"\n        FROM "TB_ROUTE_SEGMENTS" rs\n        JOIN "TB_ROUTE_SEGMENT_DETAIL" sd ON rs."SEGMENT_GUID" = sd."SEGMENT_GUID"\n        WHERE rs."ROUTE_PATH_GUID" = %s\n        ORDER BY rs."ORDER", sd."ORDER"\n    \'\'\'\n    pts: list[tuple[float, float, float]] = []\n    with conn.cursor() as cur:\n        cur.execute(sql, (guid,))\n        for row in cur.fetchall():\n            a = triple(row[0], row[1], row[2])\n            b = triple(row[3], row[4], row[5])\n            if not a or not b:\n                continue\n            if not pts:\n                pts.append(a)\n            elif dist(pts[-1], a) > 1e-6:\n                pts.append(a)\n            if dist(pts[-1], b) > 1e-6:\n                pts.append(b)\n    return pts\n\n\ndef fetch_anchors(conn, kind: str) -> list[Anchor]:\n    """장비/덕트/레터럴 AABB를 Anchor 목록으로 로드한다.\n\n    kind가 EQUIP이면 TB_EQUIPMENTS/TB_BIM_EQUIPMENT를, DUCT이면 TB_DUCT/TB_DUCT_LATERAL을,\n    LATERAL이면 TB_LATERAL_PIPE를 탐색한다. 실제 존재하는 테이블과 컬럼만 사용한다.\n    """\n    if kind == "EQUIP":\n        tables = ["TB_EQUIPMENTS", "TB_BIM_EQUIPMENT"]\n        name_candidates = ["INSTANCE_NAME", "NAME", "EQUIPMENT_NAME", "TAG"]\n    elif kind == "DUCT":\n        tables = ["TB_DUCT", "TB_DUCT_LATERAL"]\n        name_candidates = ["INSTANCE_NAME", "NAME", "DUCT_NAME", "TAG"]\n    else:\n        tables = ["TB_LATERAL_PIPE"]\n        name_candidates = ["INSTANCE_NAME", "NAME", "LATERAL_NUMBER", "TAG"]\n\n    result: list[Anchor] = []\n    for table in tables:\n        if not table_exists(conn, table):\n            continue\n        cols = table_columns(conn, table)\n        aabb = [first_col(cols, name) for name in ["AABB_MINX", "AABB_MINY", "AABB_MINZ", "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"]]\n        if not all(aabb):\n            continue\n        name_col = first_col(cols, *name_candidates)\n        utility_col = first_col(cols, "UTILITY", "SOURCE_UTILITY")\n        select = [\n            f\'"{name_col}" AS name\' if name_col else "NULL AS name",\n            f\'"{utility_col}" AS utility\' if utility_col else "NULL AS utility",\n            f\'"{aabb[0]}" AS minx\', f\'"{aabb[1]}" AS miny\', f\'"{aabb[2]}" AS minz\',\n            f\'"{aabb[3]}" AS maxx\', f\'"{aabb[4]}" AS maxy\', f\'"{aabb[5]}" AS maxz\',\n        ]\n        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:\n            cur.execute(f\'SELECT {", ".join(select)} FROM "{table}" WHERE "{aabb[0]}" IS NOT NULL\')\n            for row in cur.fetchall():\n                min_pt = triple(row["minx"], row["miny"], row["minz"])\n                max_pt = triple(row["maxx"], row["maxy"], row["maxz"])\n                if min_pt and max_pt:\n                    result.append(Anchor(kind, row.get("name") or table, row.get("utility"), min_pt, max_pt))\n    return result\n\n\ndef make_sample(route: RouteRecord, stub_kind: str, anchor: Anchor) -> StubSample | None:\n    """RouteRecord 한쪽 끝에서 StubSample 하나를 만든다.\n\n    START는 source_pos를 front로, END는 target_pos를 front로 폴리라인을 정렬한다.\n    이후 walk_stub으로 잘라낸 점열에 대해 anchor face, 방향열, rise/offset, 24D feature를 계산한다.\n    """\n    source = route.source_pos\n    target = route.target_pos\n    if not source or not target:\n        return None\n    oriented = orient_points(route.points, source if stub_kind == "START" else target)\n    walk = walk_stub(oriented)\n    if not walk:\n        return None\n    stub_points, dir_ids = walk\n    poc = source if stub_kind == "START" else target\n    face_id, offset = nearest_face(anchor, poc)\n    face = AXIS_NAMES[face_id]\n    dir_seq = [AXIS_NAMES[i] for i in dir_ids]\n    route_unit = unit(vec_sub(target, source))\n    dir_unit = list(route_unit if stub_kind == "START" else tuple(-v for v in route_unit))\n    rel = relative_pos(anchor, poc)\n    feat = build_feature(face_id, dir_ids, rel, dir_unit)\n    rise = compute_rise(stub_points, poc, face_id)\n    length = polyline_length(stub_points)\n    pattern_id = stable_id(route.guid, stub_kind, anchor.kind, anchor.name, ",".join(dir_seq), face)\n    return StubSample(\n        pattern_id=pattern_id,\n        route_path_guid=route.guid,\n        stub_kind=stub_kind,\n        anchor_kind=anchor.kind,\n        anchor_name=anchor.name,\n        main_equipment_name=route.equipment_name,\n        process_name=route.process_name,\n        utility_group=route.utility_group,\n        utility=route.utility,\n        size=route.size,\n        face=face,\n        dir_seq=dir_seq,\n        n_bends=max(0, len(dir_seq) - 1),\n        rise_mm=rise,\n        offset_mm=offset,\n        diameter_mm=parse_size_to_diameter(route.size),\n        stub_length_mm=length,\n        source_pos=source,\n        target_pos=target,\n        anchor_min=anchor.min_pt,\n        anchor_max=anchor.max_pt,\n        stub_points=stub_points,\n        feat=feat,\n        dir_unit=dir_unit,\n    )\n\n\ndef orient_points(points: list[tuple[float, float, float]], front: tuple[float, float, float]) -> list[tuple[float, float, float]]:\n    """front 좌표에 더 가까운 route 끝점이 points[0]이 되도록 폴리라인 방향을 맞춘다."""\n    if not points:\n        return points\n    return list(reversed(points)) if dist(points[-1], front) < dist(points[0], front) else list(points)\n\n\ndef walk_stub(seg: list[tuple[float, float, float]]) -> tuple[list[tuple[float, float, float]], list[int]] | None:\n    """폴리라인 앞쪽에서 Stub 구간을 잘라낸다.\n\n    핵심 규칙:\n    - 첫 방향 run의 축을 수직/진출축으로 본다. z축만 수직이라고 가정하지 않는다.\n    - 축이 달라지는 첫 run을 엘보로 보고, 그 run은 최대 STUB_LEADIN_MM만 포함한다.\n    - 엘보가 없으면 STUB_MAX_MM까지 보수적으로 자른다.\n    - 반환값은 잘라낸 stub 점열과 6축 방향 인덱스 목록이다.\n    """\n    runs = merge_short_runs(dir_runs(seg))\n    if not runs:\n        return None\n    first_axis = runs[0][0] // 2\n    total = 0.0\n    dir_ids: list[int] = []\n    bends = 0\n    for direction, length in runs:\n        axis = direction // 2\n        if direction not in dir_ids:\n            dir_ids.append(direction)\n        if axis == first_axis:\n            total += length\n            continue\n        bends += 1\n        total += min(length, STUB_LEADIN_MM)\n        break\n    if bends == 0:\n        total = min(sum(length for _, length in runs), STUB_MAX_MM)\n    total = min(total, STUB_MAX_MM)\n    return points_until(seg, total), dir_ids[: STUB_MAX_BENDS + 1]\n\n\ndef dir_runs(seg: list[tuple[float, float, float]]) -> list[list[float]]:\n    """폴리라인을 6축 방향 run으로 압축한다.\n\n    예: 여러 segment가 연속으로 +z 방향이면 하나의 [+z, 누적길이] run으로 합쳐진다.\n    이렇게 하면 정점 개수와 무관하게 실제 방향 변화 지점만 남길 수 있다.\n    """\n    runs: list[list[float]] = []\n    for a, b in zip(seg, seg[1:]):\n        length = dist(a, b)\n        if length < 1e-6:\n            continue\n        direction = axis_snap(vec_sub(b, a))\n        if runs and int(runs[-1][0]) == direction:\n            runs[-1][1] += length\n        else:\n            runs.append([direction, length])\n    return runs\n\n\ndef merge_short_runs(runs: list[list[float]]) -> list[tuple[int, float]]:\n    """250mm 미만의 짧은 방향 run을 설계 지터로 보고 인접 run에 흡수한다.\n\n    특히 -z, -y(120mm), -z 같은 패턴을 하나의 긴 -z run으로 복원해\n    미세 옵셋이 가짜 엘보로 잡히는 문제를 방지한다.\n    """\n    runs = [list(r) for r in runs]\n    while len(runs) > 1:\n        idx = min(range(len(runs)), key=lambda i: runs[i][1])\n        if runs[idx][1] >= STUB_MIN_DIR_RUN_MM:\n            break\n        if idx == 0:\n            runs[1][1] += runs[0][1]\n            del runs[0]\n        elif idx == len(runs) - 1:\n            runs[-2][1] += runs[-1][1]\n            del runs[-1]\n        elif runs[idx - 1][0] == runs[idx + 1][0]:\n            runs[idx - 1][1] += runs[idx][1] + runs[idx + 1][1]\n            del runs[idx: idx + 2]\n        elif runs[idx - 1][1] >= runs[idx + 1][1]:\n            runs[idx - 1][1] += runs[idx][1]\n            del runs[idx]\n        else:\n            runs[idx + 1][1] += runs[idx][1]\n            del runs[idx]\n    return [(int(d), float(length)) for d, length in runs]\n\n\ndef points_until(seg: list[tuple[float, float, float]], length: float) -> list[tuple[float, float, float]]:\n    """폴리라인 시작점부터 지정 길이까지 점열을 자른다.\n\n    컷 지점이 segment 중간이면 선형 보간으로 마지막 점을 만든다.\n    """\n    if not seg:\n        return []\n    out = [seg[0]]\n    remain = length\n    for a, b in zip(seg, seg[1:]):\n        edge = dist(a, b)\n        if edge < 1e-6:\n            continue\n        if remain >= edge:\n            out.append(b)\n            remain -= edge\n        else:\n            t = max(0.0, min(1.0, remain / edge))\n            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t))\n            break\n    return out\n\n\ndef build_feature(face_id: int, dir_ids: list[int], rel: tuple[float, float, float], dir_unit: list[float]) -> list[float]:\n    """StubSample의 24D 특징벡터를 생성한다.\n\n    구성:\n    - 0..5: anchor face one-hot\n    - 6..11: 1차 진행 방향 one-hot\n    - 12..17: 2차 진행 방향, 즉 첫 엘보 방향 one-hot\n    - 18..20: PoC의 anchor AABB 내부 상대좌표\n    - 21..23: route 진행 단위벡터. END Stub은 anchor로 접근하는 방향이 되도록 부호 반전\n    """\n    feat = [0.0] * FEAT_DIM\n    feat[face_id] = 1.0\n    if dir_ids:\n        feat[6 + dir_ids[0]] = 1.0\n    if len(dir_ids) > 1:\n        feat[12 + dir_ids[1]] = 1.0\n    feat[18:21] = list(rel)\n    feat[21:24] = list(dir_unit)\n    return feat\n\n\ndef build_templates(conn, args) -> list[dict[str, Any]]:\n    """TB_ROUTE_STUB_PATTERN sample을 TB_ROUTE_STUB_TEMPLATE으로 집계한다.\n\n    그룹 키는 STUB_KIND, ANCHOR_KIND, MAIN_EQUIPMENT_NAME, UTILITY_GROUP, UTILITY, SIZE,\n    FACE, DIR_SEQ이다. 같은 조건에서 min-samples 이상 반복된 패턴만 신규 자동설계에\n    재사용할 수 있는 template으로 저장한다.\n    """\n    create_schema(conn)\n    samples = load_samples(conn, args)\n    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)\n    for row in samples:\n        key = (\n            row["STUB_KIND"], row["ANCHOR_KIND"], row["MAIN_EQUIPMENT_NAME"],\n            row["UTILITY_GROUP"], row["UTILITY"], row["SIZE"], row["FACE"], row["DIR_SEQ"],\n        )\n        groups[key].append(row)\n    templates = []\n    for key, rows in groups.items():\n        if len(rows) < args.min_samples:\n            continue\n        rep = min(rows, key=lambda r: abs(float_or_zero(r["STUB_LENGTH_MM"]) - avg(rows, "STUB_LENGTH_MM")))\n        avg_feat = mean_vectors([json_value(r["FEAT_JSON"]) for r in rows if r.get("FEAT_JSON")])\n        avg_dir = mean_vectors([json_value(r["DIR_UNIT_JSON"]) for r in rows if r.get("DIR_UNIT_JSON")])\n        template_id = stable_id("template", *[str(v) for v in key])\n        templates.append({\n            "TEMPLATE_ID": template_id,\n            "STUB_KIND": key[0],\n            "ANCHOR_KIND": key[1],\n            "MAIN_EQUIPMENT_NAME": key[2],\n            "UTILITY_GROUP": key[3],\n            "UTILITY": key[4],\n            "SIZE": key[5],\n            "FACE": key[6],\n            "DIR_SEQ": key[7],\n            "SAMPLE_COUNT": len(rows),\n            "AVG_RISE_MM": avg(rows, "RISE_MM"),\n            "AVG_OFFSET_MM": avg(rows, "OFFSET_MM"),\n            "AVG_DIAMETER_MM": avg(rows, "DIAMETER_MM"),\n            "AVG_STUB_LENGTH_MM": avg(rows, "STUB_LENGTH_MM"),\n            "REPRESENTATIVE_PATTERN_ID": rep["PATTERN_ID"],\n            "REPRESENTATIVE_ROUTE_PATH_GUID": rep["ROUTE_PATH_GUID"],\n            "REPRESENTATIVE_STUB_POINTS": rep["STUB_POINTS"],\n            "AVG_FEAT_JSON": avg_feat,\n            "AVG_DIR_UNIT_JSON": avg_dir,\n        })\n    if getattr(args, "replace", False):\n        delete_templates(conn, args)\n    insert_templates(conn, templates)\n    conn.commit()\n    return templates\n\n\ndef query_templates(conn, args) -> list[dict[str, Any]]:\n    """신규 자동설계 조건에 맞는 Stub template 후보를 조회한다.\n\n    조회 fallback 순서:\n    1. 메인장비 + 유틸리티그룹 + 유틸리티 + 사이즈\n    2. 메인장비 + 유틸리티그룹 + 유틸리티\n    3. 유틸리티그룹 + 유틸리티\n    4. 유틸리티그룹\n\n    검색 결과는 sample_count가 큰 template을 우선한다.\n    """\n    if not table_exists(conn, "TB_ROUTE_STUB_TEMPLATE"):\n        raise RuntimeError("TB_ROUTE_STUB_TEMPLATE does not exist. Run create-schema and build-template first.")\n    where = []\n    params = []\n    fallback_levels = [\n        ("MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "SIZE"),\n        ("MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY"),\n        ("UTILITY_GROUP", "UTILITY"),\n        ("UTILITY_GROUP",),\n    ]\n    base = {"MAIN_EQUIPMENT_NAME": args.main_equipment, "UTILITY_GROUP": args.utility_group, "UTILITY": args.utility, "SIZE": args.size}\n    for level in fallback_levels:\n        where = []\n        params = []\n        for col in level:\n            if base.get(col) is not None:\n                where.append(f\'"{col}" = %s\')\n                params.append(base[col])\n        if args.stub_kind:\n            where.append(\'"STUB_KIND" = %s\')\n            params.append(args.stub_kind)\n        sql = f\'\'\'\n            SELECT * FROM "TB_ROUTE_STUB_TEMPLATE"\n            {"WHERE " + " AND ".join(where) if where else ""}\n            ORDER BY "SAMPLE_COUNT" DESC, "AVG_STUB_LENGTH_MM" ASC NULLS LAST\n            LIMIT %s\n        \'\'\'\n        params.append(args.max_candidates)\n        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:\n            cur.execute(sql, params)\n            rows = [dict(r) for r in cur.fetchall()]\n            if rows:\n                return normalize_json_rows(rows)\n    return []\n\n\ndef make_stub_candidates(conn, args) -> dict[str, Any]:\n    """조회된 Start/End template을 신규 PoC 좌표에 적용해 Stub 후보 조합을 만든다.\n\n    반환되는 후보는 다음 정보를 포함한다.\n    - start_stub.points: source_pos에서 시작하는 Start Stub 점열\n    - end_stub.points: target_pos에서 시작하는 End Stub 점열\n    - middle_route: 중간 자동 라우터가 연결해야 할 [start_free_point, end_free_point]\n    - score: template 반복도와 중간거리 penalty를 반영한 단순 점수\n    """\n    source_pos = parse_xyz(args.source_pos)\n    target_pos = parse_xyz(args.target_pos)\n    start_anchor = explicit_anchor(args.source_anchor_min, args.source_anchor_max, "EQUIP", args.main_equipment)\n    target_anchor = explicit_anchor(args.target_anchor_min, args.target_anchor_max, "DUCT", "TARGET")\n    request = {\n        "main_equipment_name": args.main_equipment,\n        "utility_group": args.utility_group,\n        "utility": args.utility,\n        "size": args.size,\n        "source_pos": source_pos,\n        "target_pos": target_pos,\n    }\n    start_templates = query_templates(conn, SimpleArgs(args, stub_kind="START", max_candidates=args.max_candidates))\n    end_templates = query_templates(conn, SimpleArgs(args, stub_kind="END", max_candidates=args.max_candidates))\n    start_candidates = [instantiate_stub(t, source_pos, start_anchor, forward=True) for t in start_templates]\n    end_candidates = [instantiate_stub(t, target_pos, target_anchor, forward=False) for t in end_templates]\n    combos = []\n    for s in start_candidates:\n        for e in end_candidates:\n            middle = [s["free_point"], e["free_point"]]\n            score = s["score"] + e["score"] - 0.00001 * dist(s["free_point"], e["free_point"])\n            combos.append({"start_stub": s, "end_stub": e, "middle_route": middle, "score": score})\n    combos.sort(key=lambda r: r["score"], reverse=True)\n    return {"request": request, "candidates": combos[: args.max_candidates]}\n\n\nclass SimpleArgs:\n    """query_templates 재사용을 위해 argparse.Namespace처럼 동작하는 가벼운 객체."""\n    def __init__(self, base, **overrides):\n        self.__dict__.update(vars(base))\n        self.__dict__.update(overrides)\n\n\ndef instantiate_stub(template: dict[str, Any], poc: tuple[float, float, float], anchor: Anchor | None, forward: bool) -> dict[str, Any]:\n    """Template 한 건을 신규 PoC 기준 월드 좌표 Stub 점열로 복원한다.\n\n    현재 구현은 template의 평균 rise와 dir_seq를 사용해 2~3점짜리 직교 Stub을 만든다.\n    anchor AABB가 명시되면 face 판단 보정에 사용하고, 없으면 template face를 그대로 따른다.\n    """\n    if anchor:\n        face_id = AXIS_NAMES.index(template["FACE"]) if template.get("FACE") in AXIS_NAMES else nearest_face(anchor, poc)[0]\n    else:\n        face_id = AXIS_NAMES.index(template["FACE"]) if template.get("FACE") in AXIS_NAMES else 4\n    dirs = [AXIS_NAMES.index(x) for x in str(template.get("DIR_SEQ") or "").split(",") if x in AXIS_NAMES]\n    if not dirs:\n        dirs = [face_id]\n    if not forward:\n        dirs = [opposite_axis(d) for d in dirs]\n    rise = float_or_zero(template.get("AVG_RISE_MM"))\n    length = float_or_zero(template.get("AVG_STUB_LENGTH_MM")) or STUB_LEADIN_MM\n    lead = min(STUB_LEADIN_MM, max(0.0, length - rise))\n    pts = [poc]\n    cur = poc\n    first = dirs[0]\n    cur = add_axis(cur, first, rise if rise > 0 else min(length, STUB_LEADIN_MM))\n    pts.append(cur)\n    if len(dirs) > 1 and lead > 0:\n        cur = add_axis(cur, dirs[1], lead)\n        pts.append(cur)\n    return {\n        "template_id": template.get("TEMPLATE_ID"),\n        "stub_kind": template.get("STUB_KIND"),\n        "anchor_kind": template.get("ANCHOR_KIND"),\n        "face": template.get("FACE"),\n        "dir_seq": template.get("DIR_SEQ"),\n        "points": pts,\n        "free_point": pts[-1],\n        "score": float(template.get("SAMPLE_COUNT") or 1),\n    }\n\n\ndef validate_existing_route(conn, args) -> dict[str, Any]:\n    """기존 route 1건에서 START/END Stub을 다시 추출해 검증용 JSON으로 반환한다."""\n    class A:\n        main_equipment = None\n        utility_group = None\n        utility = None\n        size = None\n        limit = None\n    routes = [r for r in fetch_routes(conn, A()) if r.guid == args.route_path_guid]\n    if not routes:\n        return {"status": "not_found", "route_path_guid": args.route_path_guid}\n    route = routes[0]\n    equip_anchors = fetch_anchors(conn, "EQUIP")\n    end_anchors = fetch_anchors(conn, "DUCT") + fetch_anchors(conn, "LATERAL")\n    start_anchor = find_anchor(equip_anchors, route.source_pos, route.equipment_name, None) if route.source_pos else None\n    end_anchor = find_anchor(end_anchors, route.target_pos, None, route.utility) if route.target_pos else None\n    start = make_sample(route, "START", start_anchor) if start_anchor else None\n    end = make_sample(route, "END", end_anchor) if end_anchor else None\n    return {\n        "status": "ok",\n        "route_path_guid": route.guid,\n        "start_stub": sample_to_json(start) if start else None,\n        "end_stub": sample_to_json(end) if end else None,\n    }\n\n\ndef insert_samples(conn, samples: list[StubSample]) -> None:\n    """StubSample 목록을 TB_ROUTE_STUB_PATTERN에 upsert한다.\n\n    pgvector 컬럼이 있으면 FEAT/DIR_UNIT을 \'[...]\' 리터럴로 전달해 ::vector 캐스팅한다.\n    pgvector fallback schema에서는 JSON 컬럼만 저장한다.\n    """\n    has_vector = pgvector_installed(conn) and has_column(conn, "TB_ROUTE_STUB_PATTERN", "FEAT")\n    cols = [\n        "PATTERN_ID", "ROUTE_PATH_GUID", "STUB_KIND", "ANCHOR_KIND", "ANCHOR_NAME",\n        "MAIN_EQUIPMENT_NAME", "PROCESS_NAME", "UTILITY_GROUP", "UTILITY", "SIZE",\n        "FACE", "DIR_SEQ", "N_BENDS", "RISE_MM", "OFFSET_MM", "DIAMETER_MM", "STUB_LENGTH_MM",\n        "SOURCE_POS", "TARGET_POS", "ANCHOR_MIN", "ANCHOR_MAX", "STUB_POINTS",\n        "FEAT_JSON", "DIR_UNIT_JSON",\n    ]\n    if has_vector:\n        cols += ["FEAT", "DIR_UNIT"]\n    placeholders = []\n    for c in cols:\n        if c in {"SOURCE_POS", "TARGET_POS", "ANCHOR_MIN", "ANCHOR_MAX", "STUB_POINTS", "FEAT_JSON", "DIR_UNIT_JSON"}:\n            placeholders.append("%s::jsonb")\n        elif c == "FEAT":\n            placeholders.append("%s::vector")\n        elif c == "DIR_UNIT":\n            placeholders.append("%s::vector")\n        else:\n            placeholders.append("%s")\n    sql = f\'\'\'\n        INSERT INTO "TB_ROUTE_STUB_PATTERN" ({", ".join(f\'"{c}"\' for c in cols)})\n        VALUES ({", ".join(placeholders)})\n        ON CONFLICT ("PATTERN_ID") DO UPDATE SET\n        {", ".join(f\'"{c}" = EXCLUDED."{c}"\' for c in cols if c != "PATTERN_ID")},\n        "CREATED_AT" = now()\n    \'\'\'\n    rows = []\n    for s in samples:\n        row = [\n            s.pattern_id, s.route_path_guid, s.stub_kind, s.anchor_kind, s.anchor_name,\n            s.main_equipment_name, s.process_name, s.utility_group, s.utility, s.size,\n            s.face, ",".join(s.dir_seq), s.n_bends, s.rise_mm, s.offset_mm, s.diameter_mm, s.stub_length_mm,\n            json.dumps(s.source_pos), json.dumps(s.target_pos), json.dumps(s.anchor_min), json.dumps(s.anchor_max),\n            json.dumps(s.stub_points), json.dumps(s.feat), json.dumps(s.dir_unit),\n        ]\n        if has_vector:\n            row += [vec_literal(s.feat), vec_literal(s.dir_unit)]\n        rows.append(row)\n    with conn.cursor() as cur:\n        psycopg2.extras.execute_batch(cur, sql, rows, page_size=200)\n\n\ndef insert_templates(conn, templates: list[dict[str, Any]]) -> None:\n    """집계된 template 목록을 TB_ROUTE_STUB_TEMPLATE에 upsert한다."""\n    if not templates:\n        return\n    has_vector = pgvector_installed(conn) and has_column(conn, "TB_ROUTE_STUB_TEMPLATE", "AVG_FEAT")\n    cols = [\n        "TEMPLATE_ID", "STUB_KIND", "ANCHOR_KIND", "MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "SIZE",\n        "FACE", "DIR_SEQ", "SAMPLE_COUNT", "AVG_RISE_MM", "AVG_OFFSET_MM", "AVG_DIAMETER_MM", "AVG_STUB_LENGTH_MM",\n        "REPRESENTATIVE_PATTERN_ID", "REPRESENTATIVE_ROUTE_PATH_GUID", "REPRESENTATIVE_STUB_POINTS",\n        "AVG_FEAT_JSON", "AVG_DIR_UNIT_JSON",\n    ]\n    if has_vector:\n        cols += ["AVG_FEAT", "AVG_DIR_UNIT"]\n    placeholders = []\n    for c in cols:\n        if c in {"REPRESENTATIVE_STUB_POINTS", "AVG_FEAT_JSON", "AVG_DIR_UNIT_JSON"}:\n            placeholders.append("%s::jsonb")\n        elif c in {"AVG_FEAT", "AVG_DIR_UNIT"}:\n            placeholders.append("%s::vector")\n        else:\n            placeholders.append("%s")\n    sql = f\'\'\'\n        INSERT INTO "TB_ROUTE_STUB_TEMPLATE" ({", ".join(f\'"{c}"\' for c in cols)})\n        VALUES ({", ".join(placeholders)})\n        ON CONFLICT ("TEMPLATE_ID") DO UPDATE SET\n        {", ".join(f\'"{c}" = EXCLUDED."{c}"\' for c in cols if c != "TEMPLATE_ID")},\n        "CREATED_AT" = now()\n    \'\'\'\n    rows = []\n    for t in templates:\n        row = [t.get(c) for c in cols if c not in {"REPRESENTATIVE_STUB_POINTS", "AVG_FEAT_JSON", "AVG_DIR_UNIT_JSON", "AVG_FEAT", "AVG_DIR_UNIT"}]\n        # Build row explicitly to avoid column-order surprises.\n        row = [\n            t["TEMPLATE_ID"], t["STUB_KIND"], t["ANCHOR_KIND"], t["MAIN_EQUIPMENT_NAME"], t["UTILITY_GROUP"], t["UTILITY"], t["SIZE"],\n            t["FACE"], t["DIR_SEQ"], t["SAMPLE_COUNT"], t["AVG_RISE_MM"], t["AVG_OFFSET_MM"], t["AVG_DIAMETER_MM"], t["AVG_STUB_LENGTH_MM"],\n            t["REPRESENTATIVE_PATTERN_ID"], t["REPRESENTATIVE_ROUTE_PATH_GUID"], json.dumps(t["REPRESENTATIVE_STUB_POINTS"]),\n            json.dumps(t["AVG_FEAT_JSON"]), json.dumps(t["AVG_DIR_UNIT_JSON"]),\n        ]\n        if has_vector:\n            row += [vec_literal(t["AVG_FEAT_JSON"]), vec_literal(t["AVG_DIR_UNIT_JSON"])]\n        rows.append(row)\n    with conn.cursor() as cur:\n        psycopg2.extras.execute_batch(cur, sql, rows, page_size=200)\n\n\ndef load_samples(conn, args) -> list[dict[str, Any]]:\n    """템플릿 집계 대상 sample을 필터 조건에 맞게 로드한다."""\n    where, params = sample_filters(args)\n    sql = f\'SELECT * FROM "TB_ROUTE_STUB_PATTERN" {"WHERE " + " AND ".join(where) if where else ""}\'\n    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:\n        cur.execute(sql, params)\n        return normalize_json_rows([dict(r) for r in cur.fetchall()])\n\n\ndef delete_samples(conn, args) -> None:\n    """--replace 실행 시 현재 필터에 해당하는 기존 sample을 삭제한다."""\n    where, params = sample_filters(args)\n    sql = f\'DELETE FROM "TB_ROUTE_STUB_PATTERN" {"WHERE " + " AND ".join(where) if where else ""}\'\n    with conn.cursor() as cur:\n        cur.execute(sql, params)\n\n\ndef delete_templates(conn, args) -> None:\n    """--replace 실행 시 현재 필터에 해당하는 기존 template을 삭제한다."""\n    where, params = sample_filters(args)\n    sql = f\'DELETE FROM "TB_ROUTE_STUB_TEMPLATE" {"WHERE " + " AND ".join(where) if where else ""}\'\n    with conn.cursor() as cur:\n        cur.execute(sql, params)\n\n\ndef sample_filters(args) -> tuple[list[str], list[Any]]:\n    """main_equipment/utility_group/utility/size 필터를 SQL WHERE 조각으로 변환한다."""\n    pairs = [\n        ("MAIN_EQUIPMENT_NAME", getattr(args, "main_equipment", None)),\n        ("UTILITY_GROUP", getattr(args, "utility_group", None)),\n        ("UTILITY", getattr(args, "utility", None)),\n        ("SIZE", getattr(args, "size", None)),\n    ]\n    where = []\n    params = []\n    for col, value in pairs:\n        if value:\n            where.append(f\'"{col}" = %s\')\n            params.append(value)\n    return where, params\n\n\ndef log_application(conn, args, result: dict[str, Any]) -> None:\n    """make-stub 결과 중 최상위 후보를 TB_ROUTE_STUB_APPLICATION_LOG에 저장한다.\n\n    신규 자동배관설계에서 어떤 Stub template이 선택되었는지 추적하기 위한 감사/디버깅 로그다.\n    """\n    create_schema(conn)\n    best = result.get("candidates", [{}])[0] if result.get("candidates") else {}\n    start = best.get("start_stub") or {}\n    end = best.get("end_stub") or {}\n    app_id = str(uuid.uuid4())\n    sql = \'\'\'\n        INSERT INTO "TB_ROUTE_STUB_APPLICATION_LOG"\n        ("APPLICATION_ID", "REQUEST_ID", "SOURCE_TEMPLATE_ID", "TARGET_TEMPLATE_ID",\n         "MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "SIZE",\n         "START_STUB_POINTS", "END_STUB_POINTS", "MIDDLE_ROUTE_POINTS", "FINAL_ROUTE_POINTS",\n         "SCORE", "STATUS", "FAIL_REASON")\n        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s)\n    \'\'\'\n    final_points = []\n    if best:\n        final_points = (start.get("points") or []) + (best.get("middle_route") or []) + list(reversed(end.get("points") or []))\n    with conn.cursor() as cur:\n        cur.execute(sql, (\n            app_id, args.request_id, start.get("template_id"), end.get("template_id"),\n            args.main_equipment, args.utility_group, args.utility, args.size,\n            json.dumps(start.get("points")), json.dumps(end.get("points")), json.dumps(best.get("middle_route")),\n            json.dumps(final_points), best.get("score"), "OK" if best else "NO_CANDIDATE", None if best else "No stub template candidates",\n        ))\n    conn.commit()\n    print(f"Application log inserted: {app_id}")\n\n\ndef print_summary(samples: list[StubSample], skipped: Counter) -> None:\n    """extract 결과 요약과 스킵 사유를 콘솔에 출력한다."""\n    by_kind = Counter(s.stub_kind for s in samples)\n    by_group = Counter((s.main_equipment_name, s.utility_group, s.utility, s.stub_kind) for s in samples)\n    print(f"Extracted samples: {len(samples)}")\n    print(f"  START={by_kind.get(\'START\', 0)}, END={by_kind.get(\'END\', 0)}")\n    if skipped:\n        print("Skipped:")\n        for k, v in skipped.items():\n            print(f"  {k}: {v}")\n    print("Top pattern groups:")\n    for key, cnt in by_group.most_common(10):\n        print(f"  {key}: {cnt}")\n\n\ndef sample_to_json(s: StubSample | None) -> dict[str, Any] | None:\n    """StubSample dataclass를 JSON 직렬화 가능한 dict로 변환한다."""\n    if s is None:\n        return None\n    return {\n        "pattern_id": s.pattern_id,\n        "route_path_guid": s.route_path_guid,\n        "stub_kind": s.stub_kind,\n        "anchor_kind": s.anchor_kind,\n        "anchor_name": s.anchor_name,\n        "main_equipment_name": s.main_equipment_name,\n        "process_name": s.process_name,\n        "utility_group": s.utility_group,\n        "utility": s.utility,\n        "size": s.size,\n        "face": s.face,\n        "dir_seq": s.dir_seq,\n        "n_bends": s.n_bends,\n        "rise_mm": s.rise_mm,\n        "offset_mm": s.offset_mm,\n        "diameter_mm": s.diameter_mm,\n        "stub_length_mm": s.stub_length_mm,\n        "source_pos": s.source_pos,\n        "target_pos": s.target_pos,\n        "anchor_min": s.anchor_min,\n        "anchor_max": s.anchor_max,\n        "stub_points": s.stub_points,\n        "feat": s.feat,\n        "dir_unit": s.dir_unit,\n    }\n\n\ndef table_exists(conn, table: str) -> bool:\n    """public schema에 table이 존재하는지 확인한다."""\n    with conn.cursor() as cur:\n        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=\'public\' AND table_name=%s", (table,))\n        return cur.fetchone()[0] > 0\n\n\ndef table_columns(conn, table: str) -> set[str]:\n    """테이블의 컬럼명 집합을 반환한다. 프로젝트별 컬럼명 차이를 흡수하는 데 사용한다."""\n    with conn.cursor() as cur:\n        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=\'public\' AND table_name=%s", (table,))\n        return {r[0] for r in cur.fetchall()}\n\n\ndef has_column(conn, table: str, col: str) -> bool:\n    """특정 컬럼 존재 여부를 반환한다."""\n    return col in table_columns(conn, table)\n\n\ndef pgvector_installed(conn) -> bool:\n    """PostgreSQL pgvector 확장 설치 여부를 확인한다."""\n    with conn.cursor() as cur:\n        cur.execute("SELECT COUNT(*) FROM pg_extension WHERE extname=\'vector\'")\n        return cur.fetchone()[0] > 0\n\n\ndef first_col(cols: set[str], *names: str) -> str | None:\n    """후보 컬럼명 중 실제 테이블에 존재하는 첫 컬럼명을 반환한다."""\n    for name in names:\n        if name in cols:\n            return name\n    return None\n\n\ndef triple(x, y, z) -> tuple[float, float, float] | None:\n    """x/y/z 값이 모두 있으면 float 3튜플로 변환하고, 하나라도 없으면 None을 반환한다."""\n    if x is None or y is None or z is None:\n        return None\n    return (float(x), float(y), float(z))\n\n\ndef axis_snap(d: tuple[float, float, float]) -> int:\n    """3D 방향 벡터를 6축 방향 인덱스로 스냅한다.\n\n    절대값이 가장 큰 성분을 지배축으로 보고 해당 축의 부호를 사용한다.\n    예: (10, 5, 100)은 +z, 즉 인덱스 4가 된다.\n    """\n    values = [abs(d[0]), abs(d[1]), abs(d[2])]\n    ax = max(range(3), key=lambda i: values[i])\n    return ax * 2 + (0 if d[ax] >= 0 else 1)\n\n\ndef opposite_axis(axis_id: int) -> int:\n    """+x <-> -x처럼 6축 방향의 반대 방향 인덱스를 반환한다."""\n    return axis_id + 1 if axis_id % 2 == 0 else axis_id - 1\n\n\ndef add_axis(p: tuple[float, float, float], axis_id: int, length: float) -> tuple[float, float, float]:\n    """점 p에서 axis_id 방향으로 length(mm)만큼 이동한 새 점을 반환한다."""\n    v = AXIS_VECTORS[axis_id]\n    return (p[0] + v[0] * length, p[1] + v[1] * length, p[2] + v[2] * length)\n\n\ndef dist(a, b) -> float:\n    """3D 유클리드 거리."""\n    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)\n\n\ndef vec_sub(a, b) -> tuple[float, float, float]:\n    """3D 벡터 a-b."""\n    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])\n\n\ndef unit(v) -> tuple[float, float, float]:\n    """3D 벡터를 단위벡터로 정규화한다. 길이가 0이면 0벡터를 반환한다."""\n    length = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)\n    if length < 1e-9:\n        return (0.0, 0.0, 0.0)\n    return (v[0] / length, v[1] / length, v[2] / length)\n\n\ndef polyline_length(points) -> float:\n    """폴리라인 전체 길이를 계산한다."""\n    return sum(dist(a, b) for a, b in zip(points, points[1:]))\n\n\ndef nearest_face(anchor: Anchor, p: tuple[float, float, float]) -> tuple[int, float]:\n    """PoC p가 anchor AABB의 어느 면에 가장 가까운지 계산한다.\n\n    반환값은 (face_id, offset_mm)이다. face_id는 AXIS_NAMES 인덱스와 동일하다.\n    """\n    mn, mx = anchor.min_pt, anchor.max_pt\n    distances = [\n        abs(mx[0] - p[0]), abs(p[0] - mn[0]),\n        abs(mx[1] - p[1]), abs(p[1] - mn[1]),\n        abs(mx[2] - p[2]), abs(p[2] - mn[2]),\n    ]\n    face = min(range(6), key=lambda i: distances[i])\n    return face, distances[face]\n\n\ndef relative_pos(anchor: Anchor, p: tuple[float, float, float]) -> tuple[float, float, float]:\n    """PoC의 anchor AABB 내부 상대좌표를 [0,1] 범위로 계산한다."""\n    out = []\n    for i in range(3):\n        denom = anchor.max_pt[i] - anchor.min_pt[i]\n        out.append(0.5 if abs(denom) < 1e-9 else clamp((p[i] - anchor.min_pt[i]) / denom, 0.0, 1.0))\n    return tuple(out)\n\n\ndef compute_rise(points: list[tuple[float, float, float]], poc: tuple[float, float, float], face_id: int) -> float:\n    """face 법선축 기준으로 Stub이 PoC에서 최대 얼마나 이동했는지 계산한다."""\n    axis = face_id // 2\n    return max(abs(p[axis] - poc[axis]) for p in points) if points else 0.0\n\n\ndef find_anchor(anchors: list[Anchor], p: tuple[float, float, float] | None, name_hint: str | None, utility_hint: str | None) -> Anchor | None:\n    """PoC에 대응되는 anchor를 찾는다.\n\n    우선 장비명/유틸리티 hint로 후보를 좁히고, PoC가 AABB 내부에 있으면 그 anchor를 우선한다.\n    내부 anchor가 없으면 ANCHOR_MAX_MM 이내의 최근접 AABB를 fallback으로 사용한다.\n    """\n    if not p:\n        return None\n    candidates = anchors\n    if name_hint:\n        filtered = [a for a in candidates if a.name and name_hint.lower() in str(a.name).lower()]\n        if filtered:\n            candidates = filtered\n    if utility_hint:\n        filtered = [a for a in candidates if a.utility == utility_hint]\n        if filtered:\n            candidates = filtered\n    inside = [a for a in candidates if point_in_aabb(p, a.min_pt, a.max_pt, margin=1.0)]\n    if inside:\n        return min(inside, key=lambda a: aabb_distance(p, a.min_pt, a.max_pt))\n    nearest = min(candidates, key=lambda a: aabb_distance(p, a.min_pt, a.max_pt), default=None)\n    if nearest and aabb_distance(p, nearest.min_pt, nearest.max_pt) <= ANCHOR_MAX_MM:\n        return nearest\n    return None\n\n\ndef explicit_anchor(min_text: str | None, max_text: str | None, kind: str, name: str) -> Anchor | None:\n    """CLI에서 직접 입력한 anchor min/max 좌표를 Anchor 객체로 변환한다."""\n    if not min_text or not max_text:\n        return None\n    return Anchor(kind=kind, name=name, utility=None, min_pt=parse_xyz(min_text), max_pt=parse_xyz(max_text))\n\n\ndef point_in_aabb(p, mn, mx, margin=0.0) -> bool:\n    """점 p가 AABB 내부에 있는지 확인한다."""\n    return all(mn[i] - margin <= p[i] <= mx[i] + margin for i in range(3))\n\n\ndef aabb_distance(p, mn, mx) -> float:\n    """점 p와 AABB 사이의 최소 거리를 계산한다. 내부면 0이다."""\n    sq = 0.0\n    for i in range(3):\n        if p[i] < mn[i]:\n            sq += (mn[i] - p[i]) ** 2\n        elif p[i] > mx[i]:\n            sq += (p[i] - mx[i]) ** 2\n    return math.sqrt(sq)\n\n\ndef parse_xyz(text: str) -> tuple[float, float, float]:\n    """\'x,y,z\' 문자열을 3D 좌표 튜플로 변환한다."""\n    parts = [x.strip() for x in text.split(",")]\n    if len(parts) != 3:\n        raise ValueError(f"Expected x,y,z: {text}")\n    return (float(parts[0]), float(parts[1]), float(parts[2]))\n\n\ndef parse_size_to_diameter(size: str | None) -> float | None:\n    """배관 사이즈 문자열을 mm 직경으로 변환한다.\n\n    예: 40A -> 40, 1/2B -> 12.7, 2B -> 50.8.\n    파싱할 수 없는 값은 None으로 둔다.\n    """\n    if not size:\n        return None\n    s = str(size).upper().replace("MM", "").replace("A", "").replace("B", "").strip()\n    try:\n        if " " in s:\n            whole, frac = s.split(" ", 1)\n            n, d = frac.split("/")\n            return (float(whole) + float(n) / float(d)) * 25.4\n        if "/" in s:\n            n, d = s.split("/")\n            return float(n) / float(d) * 25.4\n        value = float("".join(ch for ch in s if ch.isdigit() or ch == "."))\n        return value * 25.4 if value < 36 else value\n    except Exception:\n        return None\n\n\ndef clamp(v: float, lo: float, hi: float) -> float:\n    """값 v를 [lo, hi] 범위로 제한한다."""\n    return max(lo, min(hi, v))\n\n\ndef stable_id(*parts: str) -> str:\n    """여러 문자열을 조합해 재현 가능한 짧은 SHA1 ID를 만든다."""\n    h = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]\n    return h\n\n\ndef vec_literal(values: Iterable[float]) -> str:\n    """pgvector가 받을 수 있는 \'[1,2,3]\' 형태의 vector 리터럴을 만든다."""\n    return "[" + ",".join(f"{float(v):.9g}" for v in values) + "]"\n\n\ndef avg(rows: list[dict[str, Any]], key: str) -> float | None:\n    """dict row 목록에서 특정 numeric key의 평균을 계산한다."""\n    vals = [float(r[key]) for r in rows if r.get(key) is not None]\n    return sum(vals) / len(vals) if vals else None\n\n\ndef float_or_zero(v) -> float:\n    """None/빈값/파싱 실패를 0.0으로 처리하는 안전 float 변환."""\n    try:\n        return float(v or 0.0)\n    except Exception:\n        return 0.0\n\n\ndef mean_vectors(vectors: list[list[float]]) -> list[float]:\n    """동일 차원 vector 목록의 성분별 평균을 계산한다."""\n    if not vectors:\n        return []\n    n = len(vectors[0])\n    return [sum(v[i] for v in vectors if len(v) == n) / len(vectors) for i in range(n)]\n\n\ndef normalize_json_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:\n    """psycopg2가 문자열로 반환한 JSON 컬럼을 Python 값으로 변환한다."""\n    for row in rows:\n        for key in ["SOURCE_POS", "TARGET_POS", "ANCHOR_MIN", "ANCHOR_MAX", "STUB_POINTS", "FEAT_JSON", "DIR_UNIT_JSON",\n                    "REPRESENTATIVE_STUB_POINTS", "AVG_FEAT_JSON", "AVG_DIR_UNIT_JSON"]:\n            if key in row and isinstance(row[key], str):\n                try:\n                    row[key] = json.loads(row[key])\n                except Exception:\n                    pass\n    return rows\n\n\ndef json_value(value: Any) -> Any:\n    """문자열 JSON이면 파싱하고, 이미 Python 값이면 그대로 반환한다."""\n    if isinstance(value, str):\n        return json.loads(value)\n    return value\n\n\ndef print_json_or_table(obj: Any, export_json: str | None = None) -> None:\n    """결과를 콘솔 JSON으로 출력하고, 필요 시 파일에도 저장한다."""\n    if export_json:\n        write_json(export_json, obj)\n        print(f"Exported JSON: {export_json}")\n    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))\n\n\ndef write_json(path: str, obj: Any) -> None:\n    """UTF-8 JSON 파일로 저장한다. 상위 폴더가 없으면 생성한다."""\n    path_obj = Path(os.path.expandvars(os.path.expanduser(path))).resolve()\n    path_obj.parent.mkdir(parents=True, exist_ok=True)\n    path_obj.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")\n\n\nif __name__ == "__main__":\n    sys.exit(main())\n'

# Stub Template 기능은 실제 사용 시점에만 로드한다.
# 이유: 통합 파일 안에 보조 모듈 소스가 크게 내장되어 있으므로, CLI --help나
# 스키마 준비처럼 Stub 학습이 필요 없는 경로에서는 compile/exec 비용을 피한다.
stub_patterns = None
HAS_STUB_PATTERNS = True
STUB_PATTERN_IMPORT_ERROR = None

_EMBEDDED_EXTRACT_VERTICAL_GROUP_SOURCE = '#!/usr/bin/env python\n# -*- coding: utf-8 -*-\n"""\nExtractVerticalGroup.py\n\n이 모듈은 설계 데이터베이스에서 수직으로 주행하는 수직 배관 세그먼트들을 추출하고,\n공간 영역(TB_SPACE_INFO - CSF, CR, A/F, FSF 등) 정보를 참조하여\n수직다발배관(Vertical Group/Bundle)의 AABB 영역, 배관 수, 배관 간격, \n그리고 공간 경계 부근에서 배관이 수평으로 꺾여 나가는 고도 오프셋 및 방향 특징점(Space Transitions)을 추출하여 적재합니다.\n"""\n\nimport sys\nimport os\nimport math\nimport json\nimport hashlib\nfrom pathlib import Path\nfrom collections import defaultdict, Counter\n\ntry:\n    from sklearn.cluster import DBSCAN\n    HAS_SKLEARN = True\nexcept ImportError:\n    HAS_SKLEARN = False\n\n# --- DDL 스키마 선언 ---\nDDL_SQL = """\nCREATE TABLE IF NOT EXISTS "TB_ROUTE_VERTICAL_GROUP_FEATURE" (\n    "ID" bigserial PRIMARY KEY,\n    "PROJECT_ID" text NOT NULL,\n    "EQUIPMENT_NAME" text NOT NULL,\n    "UTILITY" text NOT NULL,\n    "SPACE_NAME" text NOT NULL,\n    "VERTICAL_GROUP_ID" text NOT NULL,\n    "DIRECTION" text NOT NULL,\n    "BUNDLE_LENGTH" double precision NOT NULL,\n    "AVG_PITCH_MM" double precision NOT NULL,\n    "AABB_MINX" double precision NOT NULL,\n    "AABB_MINY" double precision NOT NULL,\n    "AABB_MINZ" double precision NOT NULL,\n    "AABB_MAXX" double precision NOT NULL,\n    "AABB_MAXY" double precision NOT NULL,\n    "AABB_MAXZ" double precision NOT NULL,\n    "ROUTE_COUNT" integer NOT NULL,\n    "MEMBER_ROUTE_GUIDS_JSON" jsonb NOT NULL,\n    "GEOM_3D" geometry(MultiLineStringZ, 0),\n    "CREATED_AT" timestamptz DEFAULT now(),\n    UNIQUE("PROJECT_ID", "EQUIPMENT_NAME", "UTILITY", "SPACE_NAME", "VERTICAL_GROUP_ID")\n);\nCREATE INDEX IF NOT EXISTS "IX_TRVGF_PROJECT" ON "TB_ROUTE_VERTICAL_GROUP_FEATURE" ("PROJECT_ID");\nCREATE INDEX IF NOT EXISTS "IX_TRVGF_LOOKUP" ON "TB_ROUTE_VERTICAL_GROUP_FEATURE" ("PROJECT_ID", "EQUIPMENT_NAME", "UTILITY", "SPACE_NAME");\n"""\n\n# --- 기하학 계산 헬퍼 함수 ---\ndef dist_3d(p1, p2):\n    return math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2 + (p1[2] - p2[2])**2)\n\ndef dist_2d(p1, p2):\n    return math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)\n\ndef get_dominant_face(dx, dy, dz):\n    abs_x, abs_y, abs_z = abs(dx), abs(dy), abs(dz)\n    if abs_x >= abs_y and abs_x >= abs_z:\n        return "+x" if dx >= 0 else "-x"\n    elif abs_y >= abs_x and abs_y >= abs_z:\n        return "+y" if dy >= 0 else "-y"\n    else:\n        return "+z" if dz >= 0 else "-z"\n\ndef stable_id(*parts: str) -> str:\n    h = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]\n    return h\n\ndef segments_to_wkt_multilinestring3d(segs):\n    if not segs:\n        return None\n    lines_wkt = []\n    for s in segs:\n        p1, p2 = s[\'p1\'], s[\'p2\']\n        lines_wkt.append(f"({p1[0]} {p1[1]} {p1[2]}, {p2[0]} {p2[1]} {p2[2]})")\n    return f"MULTILINESTRING Z ({\', \'.join(lines_wkt)})"\n\ndef get_segment_axis_and_direction(p1, p2):\n    """\n    세그먼트의 시점 p1과 종점 p2를 기반으로 진행하는 주요 기하 축(X, Y, Z)과 방향(+/-)을 판별합니다.\n    """\n    dx = p2[0] - p1[0]\n    dy = p2[1] - p1[1]\n    dz = p2[2] - p1[2]\n    L = dist_3d(p1, p2)\n    if L < 1e-6:\n        return None, None\n        \n    abs_x, abs_y, abs_z = abs(dx), abs(dy), abs(dz)\n    if abs_z >= abs_x and abs_z >= abs_y:\n        axis = \'Z\'\n        direction = \'+Z\' if dz >= 0 else \'-Z\'\n    elif abs_x >= abs_y and abs_x >= abs_z:\n        axis = \'X\'\n        direction = \'+X\' if dx >= 0 else \'-X\'\n    else:\n        axis = \'Y\'\n        direction = \'+Y\' if dy >= 0 else \'-Y\'\n    return axis, direction\n\ndef get_space_name_for_point(pt, spaces):\n    """\n    3D 점 pt가 TB_SPACE_INFO의 영역 바운딩 박스(마진 100mm 적용) 내부에 포함되는지 확인하여 공간 구역명을 반환합니다.\n    """\n    x, y, z = pt\n    matched_spaces = []\n    margin = 100.0\n    for sp in spaces:\n        if (sp[\'min_x\'] - margin <= x <= sp[\'max_x\'] + margin and\n            sp[\'min_y\'] - margin <= y <= sp[\'max_y\'] + margin and\n            sp[\'min_z\'] - margin <= z <= sp[\'max_z\'] + margin):\n            matched_spaces.append(sp[\'name\'])\n    if matched_spaces:\n        # 주요 중요 공간명을 우선적으로 반환\n        for sp_name in [\'CSF\', \'CR\', \'A/F\', \'FSF\']:\n            if sp_name in matched_spaces:\n                return sp_name\n        return matched_spaces[0]\n    return \'UNKNOWN\'\n\ndef simple_2d_clustering(points, eps):\n    """\n    scikit-learn DBSCAN이 없는 환경을 대비한 순수 파이썬 2D 공간 군집화 알고리즘\n    """\n    clusters = []\n    visited = set()\n    for i, p in enumerate(points):\n        if i in visited:\n            continue\n        cluster = [i]\n        visited.add(i)\n        \n        # BFS style to find all connected points within eps\n        queue = [i]\n        while queue:\n            curr = queue.pop(0)\n            curr_pt = points[curr]\n            for j, other in enumerate(points):\n                if j not in visited:\n                    if dist_2d(curr_pt, other) < eps:\n                        visited.add(j)\n                        cluster.append(j)\n                        queue.append(j)\n        if len(cluster) >= 1:\n            clusters.append(cluster)\n    return clusters\n\ndef prepare_tables(conn):\n    print("   - [Vertical Group] Preparing vertical group feature tables...")\n    with conn.cursor() as cur:\n        # 기존 테이블이 존재하고 신규 컬럼(EQUIPMENT_NAME)이 없으면 제약 조건 충돌 방지를 위해 Drop 후 재빌드\n        cur.execute("""\n            SELECT COUNT(*) \n            FROM information_schema.columns \n            WHERE table_name=\'TB_ROUTE_VERTICAL_GROUP_FEATURE\' AND column_name=\'EQUIPMENT_NAME\';\n        """)\n        has_new_col = cur.fetchone()[0] > 0\n        \n        cur.execute("""\n            SELECT COUNT(*) \n            FROM information_schema.tables \n            WHERE table_schema=\'public\' AND table_name=\'TB_ROUTE_VERTICAL_GROUP_FEATURE\';\n        """)\n        table_exists = cur.fetchone()[0] > 0\n        \n        if table_exists and not has_new_col:\n            print("     * Old schema detected. Dropping TB_ROUTE_VERTICAL_GROUP_FEATURE for clean reconstruction...")\n            cur.execute(\'DROP TABLE IF EXISTS "TB_ROUTE_VERTICAL_GROUP_FEATURE" CASCADE;\')\n            \n        cur.execute(DDL_SQL)\n    conn.commit()\n\ndef extract_and_save_vertical_groups(conn, project_name, routes):\n    """\n    장비호기별, 유틸리티별, 공간구간별 다발배관(수평/수직) 특징점을 추출하여 저장합니다.\n    """\n    print("   - [Vertical Group] Starting 3D horizontal/vertical pipe bundle extraction pipeline...")\n    \n    # 1. 공간 정보 로드 (TB_SPACE_INFO)\n    spaces = []\n    try:\n        with conn.cursor() as cur:\n            cur.execute("""\n                SELECT "SPACE_NAME", "AABB_MINX", "AABB_MINY", "AABB_MINZ", "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"\n                FROM "TB_SPACE_INFO"\n                WHERE "SPACE_NAME" IN (\'CSF\', \'CR\', \'A/F\', \'FSF\', \'AREA\')\n                  AND "AABB_MINZ" IS NOT NULL\n            """)\n            for row in cur.fetchall():\n                spaces.append({\n                    \'name\': row[0].strip(),\n                    \'min_x\': float(row[1] or 0.0),\n                    \'min_y\': float(row[2] or 0.0),\n                    \'min_z\': float(row[3] or 0.0),\n                    \'max_x\': float(row[4] or 0.0),\n                    \'max_y\': float(row[5] or 0.0),\n                    \'max_z\': float(row[6] or 0.0)\n                })\n        print(f"     * Loaded {len(spaces)} space area zones from TB_SPACE_INFO.")\n    except Exception as ex:\n        print(f"     * [Warning] Failed to query TB_SPACE_INFO: {ex}")\n        conn.rollback()\n        spaces = []\n\n    # 2. 각 배관 경로에서 세그먼트 추출 후 공간 및 방향 매핑\n    segments_by_group = defaultdict(list)\n    for r in routes:\n        pts = r[\'points\']\n        guid = r[\'guid\']\n        meta = r[\'meta\']\n        eq_name = meta.get(\'eq_tag\') or project_name\n        utility = meta.get(\'utility_group\') or \'UNKNOWN\'\n        \n        for i in range(len(pts) - 1):\n            p1, p2 = pts[i], pts[i+1]\n            length = dist_3d(p1, p2)\n            if length < 100.0:  # 노이즈 필터링\n                continue\n                \n            axis, direction = get_segment_axis_and_direction(p1, p2)\n            if not axis:\n                continue\n                \n            mid = ((p1[0]+p2[0])/2.0, (p1[1]+p2[1])/2.0, (p1[2]+p2[2])/2.0)\n            space_name = get_space_name_for_point(mid, spaces)\n            \n            segments_by_group[(eq_name, utility, space_name, axis)].append({\n                \'guid\': guid,\n                \'p1\': p1,\n                \'p2\': p2,\n                \'mid\': mid,\n                \'direction\': direction,\n                \'length\': length\n            })\n\n    # 3. 각 그룹(장비, 유틸, 공간, 축)별 2D 군집화 및 다발 조건 추출\n    saved_count = 0\n    eps_dist = 1000.0  # 평행 배관 다발 군집화 기준 거리 (1.0m)\n\n    with conn.cursor() as cur:\n        # 해당 프로젝트의 기존 레코드 정리\n        cur.execute(\'DELETE FROM "TB_ROUTE_VERTICAL_GROUP_FEATURE" WHERE "PROJECT_ID" = %s\', (project_name,))\n        \n        for (eq_name, utility, space_name, axis), segs in segments_by_group.items():\n            # 군집화를 위한 2D 좌표 리스트 구성\n            proj_points = []\n            for s in segs:\n                mid = s[\'mid\']\n                if axis == \'Z\':\n                    proj_points.append((mid[0], mid[1]))\n                elif axis == \'X\':\n                    proj_points.append((mid[1], mid[2]))\n                else:  # Y축\n                    proj_points.append((mid[0], mid[2]))\n                    \n            # 2D 평면 상에서 군집화 실행\n            clusters_indices = []\n            if HAS_SKLEARN and len(proj_points) >= 2:\n                db = DBSCAN(eps=eps_dist, min_samples=1).fit(proj_points)\n                labels = db.labels_\n                clusters_map = defaultdict(list)\n                for idx, label in enumerate(labels):\n                    if label != -1:\n                        clusters_map[label].append(idx)\n                clusters_indices = list(clusters_map.values())\n            else:\n                clusters_indices = simple_2d_clustering(proj_points, eps_dist)\n                \n            for cluster_idx, indices in enumerate(clusters_indices):\n                cluster_segs = [segs[idx] for idx in indices]\n                if not cluster_segs:\n                    continue\n                    \n                # 다발에 포함된 배관 개수 분석\n                member_guids = list(sorted(set(s[\'guid\'] for s in cluster_segs)))\n                route_count = len(member_guids)\n                if route_count < 2:  # 다발배관 최소 가닥 제약 조건 (2가닥 이상)\n                    continue\n                    \n                # 다발 주행 방향 중심 축 길이 계산 (최소 500mm 이상)\n                running_coords = []\n                for s in cluster_segs:\n                    p1, p2 = s[\'p1\'], s[\'p2\']\n                    if axis == \'Z\':\n                        running_coords.extend([p1[2], p2[2]])\n                    elif axis == \'X\':\n                        running_coords.extend([p1[0], p2[0]])\n                    else:\n                        running_coords.extend([p1[1], p2[1]])\n                min_coord, max_coord = min(running_coords), max(running_coords)\n                bundle_length = max_coord - min_coord\n                if bundle_length < 500.0:  # 최소 길이 제약 조건 (500mm 이상)\n                    continue\n                    \n                # 다발 내부의 배관 간 평균 간격 (Pitch) 연산\n                route_projections = defaultdict(list)\n                for s in cluster_segs:\n                    guid = s[\'guid\']\n                    mid = s[\'mid\']\n                    if axis == \'Z\':\n                        route_projections[guid].append((mid[0], mid[1]))\n                    elif axis == \'X\':\n                        route_projections[guid].append((mid[1], mid[2]))\n                    else:\n                        route_projections[guid].append((mid[0], mid[2]))\n                        \n                route_avg_projs = []\n                for r_guid, projs in route_projections.items():\n                    avg_x = sum(p[0] for p in projs) / len(projs)\n                    avg_y = sum(p[1] for p in projs) / len(projs)\n                    route_avg_projs.append((avg_x, avg_y))\n                    \n                pitches = []\n                for i in range(len(route_avg_projs)):\n                    for j in range(i+1, len(route_avg_projs)):\n                        pitches.append(dist_2d(route_avg_projs[i], route_avg_projs[j]))\n                avg_pitch = float(sum(pitches) / len(pitches)) if pitches else 0.0\n                \n                # 대표 진행 방향 식별 (최빈값 방향 사용)\n                directions = [s[\'direction\'] for s in cluster_segs]\n                dominant_dir = Counter(directions).most_common(1)[0][0] if directions else "UNKNOWN"\n                \n                # AABB 영역 산출\n                xs = [p[0] for s in cluster_segs for p in (s[\'p1\'], s[\'p2\'])]\n                ys = [p[1] for s in cluster_segs for p in (s[\'p1\'], s[\'p2\'])]\n                zs = [p[2] for s in cluster_segs for p in (s[\'p1\'], s[\'p2\'])]\n                aabb_minx, aabb_maxx = min(xs), max(xs)\n                aabb_miny, aabb_maxy = min(ys), max(ys)\n                aabb_minz, aabb_maxz = min(zs), max(zs)\n                \n                # WKT MultiLineString 생성\n                wkt = segments_to_wkt_multilinestring3d(cluster_segs)\n                \n                # 고유 다발 ID 생성\n                v_group_id = stable_id(project_name, eq_name, utility, space_name, dominant_dir, f"BUNDLE_{cluster_idx}", ",".join(member_guids))\n                \n                # DB 영속화 적재 (UPSERT)\n                cur.execute("""\n                    INSERT INTO "TB_ROUTE_VERTICAL_GROUP_FEATURE" (\n                        "PROJECT_ID", "EQUIPMENT_NAME", "UTILITY", "SPACE_NAME", "VERTICAL_GROUP_ID",\n                        "DIRECTION", "BUNDLE_LENGTH", "AVG_PITCH_MM", \n                        "AABB_MINX", "AABB_MINY", "AABB_MINZ",\n                        "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ", \n                        "ROUTE_COUNT", "MEMBER_ROUTE_GUIDS_JSON", "GEOM_3D"\n                    )\n                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, ST_GeomFromText(%s, 0))\n                    ON CONFLICT ("PROJECT_ID", "EQUIPMENT_NAME", "UTILITY", "SPACE_NAME", "VERTICAL_GROUP_ID")\n                    DO UPDATE SET\n                        "DIRECTION" = EXCLUDED."DIRECTION",\n                        "BUNDLE_LENGTH" = EXCLUDED."BUNDLE_LENGTH",\n                        "AVG_PITCH_MM" = EXCLUDED."AVG_PITCH_MM",\n                        "AABB_MINX" = EXCLUDED."AABB_MINX",\n                        "AABB_MINY" = EXCLUDED."AABB_MINY",\n                        "AABB_MINZ" = EXCLUDED."AABB_MINZ",\n                        "AABB_MAXX" = EXCLUDED."AABB_MAXX",\n                        "AABB_MAXY" = EXCLUDED."AABB_MAXY",\n                        "AABB_MAXZ" = EXCLUDED."AABB_MAXZ",\n                        "ROUTE_COUNT" = EXCLUDED."ROUTE_COUNT",\n                        "MEMBER_ROUTE_GUIDS_JSON" = EXCLUDED."MEMBER_ROUTE_GUIDS_JSON",\n                        "GEOM_3D" = EXCLUDED."GEOM_3D",\n                        "CREATED_AT" = now()\n                """, (\n                    project_name, eq_name, utility, space_name, v_group_id,\n                    dominant_dir, bundle_length, avg_pitch,\n                    aabb_minx, aabb_miny, aabb_minz,\n                    aabb_maxx, aabb_maxy, aabb_maxz,\n                    route_count, json.dumps(member_guids), wkt\n                ))\n                saved_count += 1\n                \n    conn.commit()\n    conn.commit()\n    print(f"     * Successfully saved {saved_count} 3D pipe bundle features for project \'{project_name}\'.")\n\n'

# Vertical/Bundle 기능도 실제 사용 시점에만 로드한다.
vertical_group = None
HAS_VERTICAL_GROUP = True
VERTICAL_GROUP_IMPORT_ERROR = None


def _get_stub_patterns():
    """내장 Stub Template 모듈을 지연 로딩한다.

    핵심 알고리즘:
    - 내장 문자열(_EMBEDDED_EXTRACT_STUB_PATTERNS_SOURCE)을 module namespace로 compile/exec
    - 성공 시 extract_samples(), build_templates()를 기존 코드와 동일하게 사용
    - 실패 시 HAS_STUB_PATTERNS=False로 기록하고 이후 호출에서 graceful skip
    주요 변수:
    - stub_patterns: 로딩된 module-like 객체
    - STUB_PATTERN_IMPORT_ERROR: 로딩 실패 원인
    """
    global stub_patterns, HAS_STUB_PATTERNS, STUB_PATTERN_IMPORT_ERROR
    if stub_patterns is not None:
        return stub_patterns
    if not HAS_STUB_PATTERNS and STUB_PATTERN_IMPORT_ERROR is not None:
        return None
    try:
        stub_patterns = _load_embedded_module("ExtractStubPatterns_embedded", _EMBEDDED_EXTRACT_STUB_PATTERNS_SOURCE)
        HAS_STUB_PATTERNS = True
        STUB_PATTERN_IMPORT_ERROR = None
        return stub_patterns
    except Exception as ex:
        stub_patterns = None
        HAS_STUB_PATTERNS = False
        STUB_PATTERN_IMPORT_ERROR = ex
        return None


def _get_vertical_group():
    """내장 수직/다발 배관 특징점 모듈을 지연 로딩한다.

    핵심 알고리즘:
    - 내장 문자열(_EMBEDDED_EXTRACT_VERTICAL_GROUP_SOURCE)을 module namespace로 compile/exec
    - 성공 시 prepare_tables(), extract_and_save_vertical_groups()를 호출
    - 실패 시 HAS_VERTICAL_GROUP=False로 기록하고 수직 그룹 추출만 skip
    주요 변수:
    - vertical_group: 로딩된 module-like 객체
    - VERTICAL_GROUP_IMPORT_ERROR: 로딩 실패 원인
    """
    global vertical_group, HAS_VERTICAL_GROUP, VERTICAL_GROUP_IMPORT_ERROR
    if vertical_group is not None:
        return vertical_group
    if not HAS_VERTICAL_GROUP and VERTICAL_GROUP_IMPORT_ERROR is not None:
        return None
    try:
        vertical_group = _load_embedded_module("ExtractVerticalGroup_embedded", _EMBEDDED_EXTRACT_VERTICAL_GROUP_SOURCE)
        HAS_VERTICAL_GROUP = True
        VERTICAL_GROUP_IMPORT_ERROR = None
        return vertical_group
    except Exception as ex:
        vertical_group = None
        HAS_VERTICAL_GROUP = False
        VERTICAL_GROUP_IMPORT_ERROR = ex
        return None

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
# =============================================================================
# 통합 특징점 추출 파이프라인 흐름도
# =============================================================================
# 0. prepare_tables()
#    - PostGIS/pgvector 확장 준비, 특징점 저장 테이블/인덱스 생성, 누락 컬럼 migration.
# 1. load_data()
#    - TB_ROUTE_PATH + TB_ROUTE_SEGMENTS + TB_ROUTE_SEGMENT_DETAIL을 읽어 route별 3D polyline 복원.
#    - SOURCE_POS 기준으로 polyline 방향을 보정해 모든 특징점의 시작/종단 의미를 통일.
# 2. save_individual_paths()
#    - 전체 경로 geometry, 총 길이, bend 수, 대표 rack Z, 관경을 저장.
# 3. save_obstacle_relations()
#    - 장애물 AABB와 경로 segment의 최근접 관계, clearance, 우회 방향을 저장.
# 4. save_route_similarity_vectors()
#    - 방향/형상/env-cost/axis-ratio를 30D pgvector로 변환해 Top-K 유사 설계 검색용으로 저장.
# 5. save_anchor_features()
#    - 출발/종단 PoC의 face, rise, first elbow, stub LineStringZ를 저장.
# 6. learn_stub_templates() / mirror_stub_templates()
#    - 내장 Stub 모듈로 START/END stub sample/template을 만들고 feature 테이블에 복사.
# 7. _get_vertical_group().extract_and_save_vertical_groups()
#    - 내장 수직/다발 모듈로 공간/축별 배관 bundle, pitch, AABB, MultiLineStringZ를 저장.
# 8. detect_rack_levels() + analyze_poc_faces() + extract_trunk_spine()
#    - utility group별 rack Z, PoC 선호 접속면, trunk spine을 추출.
# 9. save_group_profile() + save_bundle_template()
#    - 자동경로 탐색에서 바로 조회할 group profile과 bundle template을 저장.
# =============================================================================

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

    # [공통 준비] 특징점 저장 테이블/인덱스/확장 기능을 준비한다.
    # 주요 변수: DDL_SQL, VECTOR_DDL_SQL, migrations, pgvector_enabled.
    def _table_columns(self, table_name):
        """DB 테이블 컬럼 목록을 안전하게 조회한다.

        개선 목적:
        - 운영 DB마다 PROJECT_ID/EQUIPMENT_TAG 컬럼 존재 여부가 다를 수 있으므로,
          장애물 조회 등에서 schema-aware optional filter를 적용하기 위한 공통 helper.
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=%s;
                """, (table_name,))
                return {row[0] for row in cur.fetchall()}
        except Exception as ex:
            print(f"   - [Notice] Could not inspect columns for {table_name}: {ex}")
            self.conn.rollback()
            return set()

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
        vertical_group_module = _get_vertical_group()
        if vertical_group_module:
            vertical_group_module.prepare_tables(self.conn)
            # [개선] 수직/다발 배관 geometry도 공간 검색 대상이므로 GiST 인덱스를 보강한다.
            with self.conn.cursor() as vg_cur:
                vg_cur.execute('CREATE INDEX IF NOT EXISTS "IX_TRVGF_GEOM" ON "TB_ROUTE_VERTICAL_GROUP_FEATURE" USING gist("GEOM_3D");')
            self.conn.commit()

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
                # [개선] 기존 DB에는 FEATURE_VECTOR_JSON 컬럼이 없을 수 있으므로 자동 마이그레이션한다.
                ("TB_ROUTE_FEATURE_VECTOR", "FEATURE_VECTOR_JSON", 'ALTER TABLE "TB_ROUTE_FEATURE_VECTOR" ADD COLUMN "FEATURE_VECTOR_JSON" jsonb;'),
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

    # [입력 데이터 로딩] 기존 배관 상세 세그먼트를 route별 3D polyline으로 복원한다.
    # 핵심 알고리즘: ORDER 정렬 -> 10mm 이하 단절 보정 -> source_pos 기준 방향 reverse.
    # 주요 변수: raw_details, route_meta, pts, source_pos.
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
                rp."PROJECT_SCOPE_KEY", rp."MODEL_REVISION_KEY",
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
                    ,'project_scope_key': r.get('PROJECT_SCOPE_KEY')
                    ,'model_revision_key': r.get('MODEL_REVISION_KEY')
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

    # [특징점 1: 개별 경로 특징]
    # 핵심 알고리즘: 3D 길이 합산, 벡터 내적 기반 bend 검출, 최장 수평 segment의 Z를 대표 rack Z로 선택.
    # 주요 변수: total_length, bend_count, main_z, diameter, GEOM_3D.
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

    # [특징점 2: PoC Anchor/Stub]
    # 핵심 알고리즘: route_bends()로 첫/마지막 elbow를 찾고, dominant face와 stub LineStringZ를 저장.
    # 주요 변수: first_elbow_idx, last_elbow_idx, start_face, end_face, STUB_GEOM_3D.
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

    # [장애물 입력 로딩] route 전체 bbox를 5m 확장하여 주변 BIM 장애물 AABB를 조회한다.
    # 주요 변수: minx/maxx/miny/maxy/minz/maxz, obstacles, obstacle_type, axis.
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
        params = [minx, maxx, miny, maxy, minz, maxz]

        # [개선] 운영 DB 스키마에 프로젝트/장비 범위 컬럼이 있으면 장애물도 같은 범위로 제한한다.
        # 컬럼이 없는 현장 DB에서는 기존 AABB overlap 조건만 사용하여 호환성을 유지한다.
        obstacle_cols = self._table_columns("TB_BIM_OBSTACLE")
        for scope_col in ("EQUIPMENT_TAG", "PROJECT_ID", "PROJECT_NAME", "MAIN_EQUIPMENT_NAME"):
            if scope_col in obstacle_cols:
                sql += f' AND "{scope_col}" = %s'
                params.append(self.project_name)
                break

        obstacles = []
        try:
            with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
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

    # [특징점 3: 장애물 관계]
    # 핵심 알고리즘: route bbox pruning -> segment-AABB 최단거리 -> clearance margin/우회 방향/relation score 저장.
    # 주요 변수: required_clearance, limit_dist, nearest, bypass_axis, relation_score, GEOM_3D.
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

    # [특징점 7: Stub Template]
    # 목적: 기존 설계의 START/END Stub 형상을 샘플링하고 반복 패턴을 템플릿화한다.
    # 핵심 알고리즘: anchor face 판정 -> 방향 run 압축 -> 24D stub feature 생성 -> template 집계.
    # 주요 함수/변수: _get_stub_patterns(), extract_args, samples, main_names, templates.
    def learn_stub_templates(self):
        stub_module = _get_stub_patterns()
        if not stub_module:
            print(f"   - [Stub] embedded ExtractStubPatterns load failed: {STUB_PATTERN_IMPORT_ERROR}")
            return
        extract_args = SimpleNamespace(main_equipment=self.project_name, utility_group=None, utility=None, size=None, limit=None, dry_run=False, export_json=None, replace=True, min_samples=1)
        try:
            samples = stub_module.extract_samples(self.conn, extract_args)
            main_names = sorted({s.main_equipment_name for s in samples if getattr(s, 'main_equipment_name', None)})
            templates = []
            for main_name in main_names:
                build_args = SimpleNamespace(main_equipment=main_name, utility_group=None, utility=None, size=None, limit=None, dry_run=False, export_json=None, replace=True, min_samples=1)
                templates.extend(stub_module.build_templates(self.conn, build_args))
            self.stub_sample_count = len(samples)
            self.stub_template_count = len(templates)
            self.mirror_stub_templates(main_names)
            print(f"   - [Stub] Learned route-geometry stub samples={len(samples)}, templates={len(templates)}")
        except Exception as ex:
            print(f"   - [Stub] Stub template learning failed: {ex}")
            self.conn.rollback()

    # [특징점 7-후처리: Stub Template Mirror]
    # 핵심 알고리즘: TB_ROUTE_STUB_TEMPLATE의 대표 stub points를 JSON과 LineStringZ로 feature 테이블에 복사.
    # 주요 변수: TEMPLATE_ID, DIR_SEQ_JSON, REPRESENTATIVE_POINTS_JSON, GEOM_3D.
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

    # [특징점 6: Bundle Template]
    # 핵심 알고리즘: trunk spine의 X/Y span으로 주축을 판단하고 group별 중심선과 member route를 저장.
    # 주요 변수: trunk_axis, bundle_id, spine_pts, route_guids, GEOM_3D.
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

    # [특징점 4: 30D 유사도 벡터]
    # 핵심 알고리즘: 시작/종단 방향, displacement, bbox, 3분할 방향, env cost, axis ratio를 가중 후 L2 정규화.
    # 주요 변수: WEIGHT_MAP, scale_factors, obs_relations, vec, FEATURE_VECTOR.
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
                "FEATURE_VECTOR", "FEATURE_VECTOR_JSON", "PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY"
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::vector, %s::jsonb, %s, %s)
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
                "FEATURE_VECTOR" = EXCLUDED."FEATURE_VECTOR",
                "FEATURE_VECTOR_JSON" = EXCLUDED."FEATURE_VECTOR_JSON",
                "PROJECT_SCOPE_KEY" = EXCLUDED."PROJECT_SCOPE_KEY",
                "MODEL_REVISION_KEY" = EXCLUDED."MODEL_REVISION_KEY";
        """
        
        # 30D 벡터 차원 정의
        # 0~2: 시작 방향, 3~5: 종단 방향, 6~8: 시작-끝 변위,
        # 9~11: bbox 크기, 12~20: 3분할 경로 방향,
        # 21~24: 길이/장애물/env cost, 25~29: 축별 주행비율/bend/reserved.
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
                    vec_literal,
                    json.dumps(vec),
                    meta.get('project_scope_key'),
                    meta.get('model_revision_key')
                ))
                count += 1
        self.conn.commit()
        print(f"-> 유사 설계 30D 벡터 {count}개 적재 완료.")

    # [메인 파이프라인] 모든 특징점 추출 함수를 의존 순서에 맞춰 실행한다.
    # 실행 순서 최적화: 장애물 관계를 먼저 저장한 뒤 30D vector의 env cost 차원에서 재사용한다.
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
        
        # [특징점 8: 수직/다발 배관]
        # 목적: 같은 공간/축을 따라 병렬 주행하는 배관 묶음과 수직 라이저 후보를 찾는다.
        # 핵심 알고리즘: 세그먼트 축 판정 -> 공간구역 매핑 -> 2D 군집화 -> pitch/AABB/MultiLineString 저장.
        # 주요 함수/변수: _get_vertical_group(), routes, project_name, TB_ROUTE_VERTICAL_GROUP_FEATURE.
        vertical_group_module = _get_vertical_group()
        if vertical_group_module:
            vertical_group_module.extract_and_save_vertical_groups(self.conn, self.project_name, self.routes)
            t_vert = time.time()
            print(f"     * 수직다발배관 입상 특징점 추출 및 적재 완료 (소요시간: {t_vert - t4:.2f}초)")
        else:
            print(f"     * [Vertical Group] embedded ExtractVerticalGroup load failed or unavailable: {VERTICAL_GROUP_IMPORT_ERROR}")
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
    # [특징점 5: Rack Z 고도]
    # 핵심 알고리즘: 수평 segment 길이를 가중치로 100mm Z histogram을 만들고 peak를 추출한다.
    # 주요 변수: z_weights, bin_z, peaks, max_levels.
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
    # [특징점 6: PoC 선호 접속면]
    # 핵심 알고리즘: 시작/종단 segment 방향을 +x/-x/+y/-y/+z/-z로 투표하고 confidence를 계산한다.
    # 주요 변수: source_votes, target_votes, s_best, t_best.
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
    # [특징점 7: Trunk Spine]
    # 핵심 알고리즘: 200mm resampling -> DBSCAN/fallback clustering -> 진행축 정렬 -> RDP 단순화.
    # 주요 변수: all_pts, spine_candidates, dbs_eps, simplified_spine, refined_spine.
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

    # [특징점 5/6/7 저장: Group Profile]
    # 핵심 알고리즘: utility group별 rack Z, source/target face, trunk centerline을 하나의 profile로 upsert.
    # 주요 변수: grp, rack_zs, spine_json, spine_wkt, TRUNK_CENTERLINE_GEOM.
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


class DesignPatternExtractor(DesignFeatureLearner):
    """Backward-compatible class name used by main().

    The integrated source keeps the original DesignFeatureLearner implementation
    and exposes the DesignPatternExtractor/extract_and_save names expected by
    the CLI entry point.
    """

    def extract_and_save(self):
        return self.learn_and_save()


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
        dummy_extractor = DesignPatternExtractor(conn, "")
        dummy_extractor.prepare_tables()
        
        # 대상 프로젝트 결정
        projects = []
        if args.project.strip().lower() == "all":
            print(">>> [전체 프로젝트 패턴 추출 모드] 데이터베이스에서 고유 프로젝트 목록을 조회합니다...")
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
            extractor = DesignPatternExtractor(conn, proj, report_enabled=(args.report.lower() == "true"))
            
            # 데이터 로드
            data_loaded = extractor.load_data()
            
            # 실제 프로젝트명(process_name)과 장비명(eq_tag) 식별
            p_name = "UNKNOWN"
            e_tag = proj
            if data_loaded and extractor.routes:
                p_name = extractor.routes[0]['meta'].get('process_name', 'UNKNOWN')
                e_tag = extractor.routes[0]['meta'].get('eq_tag', proj)
            
            print(f"\n====================================================================================")
            print(f"=== 기존 설계 디자인 패턴 추출 파이프라인 가동 (프로젝트: {p_name} / 장비: {e_tag}) ===")
            print(f"====================================================================================")
            
            if data_loaded:
                extractor.extract_and_save()
                if extractor.report_groups:
                    report_data_list.append({
                        'project_name': proj,
                        'img_path': extractor.temp_img_path,
                        'groups': extractor.report_groups
                    })
            
            end_proj_time = time.time()
            elapsed_proj = end_proj_time - start_proj_time
            project_times.append((p_name, e_tag, elapsed_proj))
            print(f"\n>>> [소요시간] 프로젝트 '{p_name}' / 장비 '{e_tag}' 추출 완료 (소요시간: {elapsed_proj:.2f}초)")
                    
        # 모든 장비 패턴 추출 완료 후 날씨/시간이 포함된 3D 뷰 종합 PDF 리포트 생성
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
        print(f"=== 전체 패턴 추출 파이프라인 완료 및 요약 (Total Execution Summary) ===")
        print(f"====================================================================================")
        for p_name, e_tag, p_time in project_times:
            print(f"  - 프로젝트 '{p_name}' / 장비 '{e_tag}': {p_time:.2f}초")
        print(f"\n★ [총 소요시간] 전체 작업 완료 총 걸린시간: {elapsed_total:.2f}초")
        print(f"====================================================================================")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
