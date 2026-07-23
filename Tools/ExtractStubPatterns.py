from __future__ import annotations

"""
DDW_AI_DB 기존설계 배관으로부터 Start/End Stub 패턴을 추출, 저장, 집계, 활용하는 CLI 도구.

전체 프로세스
--------------
1. create-schema
   - TB_ROUTE_STUB_PATTERN, TB_ROUTE_STUB_TEMPLATE, TB_ROUTE_STUB_APPLICATION_LOG 테이블을 생성한다.
   - pgvector 확장이 있으면 FEAT vector(24), DIR_UNIT vector(3) 컬럼과 HNSW 인덱스를 사용한다.
   - pgvector가 없으면 JSON fallback 스키마를 생성해 최소 기능을 유지한다.

2. extract
   - TB_ROUTE_PATH, TB_ROUTE_SEGMENTS, TB_ROUTE_SEGMENT_DETAIL을 조인해 route별 3D 폴리라인을 복원한다.
   - source_pos 쪽은 START Stub, target_pos 쪽은 END Stub으로 역방향 정렬해서 동일 알고리즘을 적용한다.
   - 각 stub은 anchor AABB 기준 face, 방향열(dir_seq), rise/offset/length, 24D 특징벡터로 변환된다.
   - 결과는 TB_ROUTE_STUB_PATTERN에 저장된다.

3. build-template
   - 개별 Stub sample을 메인장비, 유틸리티그룹, 유틸리티, 사이즈, face, dir_seq 기준으로 그룹화한다.
   - min-samples 이상 반복된 패턴만 TB_ROUTE_STUB_TEMPLATE에 대표 template으로 저장한다.

4. query-template / make-stub
   - 신규 자동배관설계 요청의 메인장비/유틸리티 조건으로 template을 조회한다.
   - 조회된 Start/End template을 신규 source/target PoC 좌표에 맞춰 월드 좌표 stub 후보로 복원한다.
   - 중간 자동 라우터는 start_stub.free_point와 end_stub.free_point를 연결하면 된다.

주요 실행 명령
--------------
스키마 생성:
    python Tools\\ExtractStubPatterns.py create-schema --config Tools\\tools.settings.json

기존 배관에서 Stub 추출:
    python Tools\\ExtractStubPatterns.py extract --config Tools\\tools.settings.json --limit 100 --dry-run

추출 + 템플릿 집계 일괄 실행:
    python Tools\\ExtractStubPatterns.py run-all --config Tools\\tools.settings.json --min-samples 3 --replace

신규 자동설계용 Stub 후보 생성:
    python Tools\\ExtractStubPatterns.py make-stub --config Tools\\tools.settings.json \\
      --main-equipment WTNHJ02 --utility-group Water --utility PCWS \\
      --source-pos 1000,2000,3000 --target-pos 7000,9000,4500 --max-candidates 5

핵심 알고리즘 요약
------------------
- axis_snap: 임의 3D 벡터를 +x,-x,+y,-y,+z,-z 중 지배축 방향으로 스냅한다.
- Stub 경계 판정은 PathSegmenter.segment_route()를 그대로 재사용한다(자체 재구현 아님).
  route를 source_pos 기준으로 한 번 정규 정렬한 뒤 segment_route()를 1회 호출해 Start
  Stub(CSF 평면 Z=13700mm 인식 포함)과 End Stub(역방향 첫 엘보 스캔)을 동시에 얻고,
  extract_stub_points()가 stub_kind에 맞는 쪽을 골라 PoC가 앞에 오도록 정렬한다.
  ExtractBendFeaturePoints.py도 동일한 segment_route()를 재사용하므로, 이제 이 저장소에서
  "stub 경계가 어디인가"에 대한 정의는 PathSegmenter 하나로 통일되어 있다
  (Docs/FeaturePattern_Pipeline_Overlap_Review.md 3.3절 참고).
- build_feature: face(6D), 1차 방향(6D), 2차 방향(6D), anchor 내부 상대좌표(3D), 진행방향(3D)을 합쳐 24D feature를 만든다.
"""

import argparse
import hashlib
import json
import math
import os
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import psycopg2
import psycopg2.extras

from tool_config import add_common_args, print_runtime, resolve_runtime
from PathSegmenter import segment_route


# 6축 방향 인덱스 규약.
# 인덱스는 PDF 문서의 axis_snap 규칙과 맞춘다.
# 0:+x, 1:-x, 2:+y, 3:-y, 4:+z, 5:-z
AXIS_NAMES = ["+x", "-x", "+y", "-y", "+z", "-z"]
AXIS_VECTORS = [
    (1.0, 0.0, 0.0),
    (-1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, -1.0, 0.0),
    (0.0, 0.0, 1.0),
    (0.0, 0.0, -1.0),
]
# 250mm 미만 방향 run은 설계상 의미 있는 꺾임이 아니라 BIM 지터로 보고 병합한다.
STUB_MIN_DIR_RUN_MM = 250.0
# 첫 엘보 방향으로 포함할 최대 수평/다음축 리드인 길이.
STUB_LEADIN_MM = 800.0
# Stub이 과도하게 긴 중앙 자유공간 run까지 먹지 않도록 하는 안전 상한.
STUB_MAX_MM = 4000.0
# dir_seq에 보관할 최대 bend 수. 현재 알고리즘은 첫 엘보 중심이므로 넉넉한 방어값이다.
STUB_MAX_BENDS = 3
# PoC가 AABB 내부에 없을 때 anchor로 인정할 최대 최근접 거리.
ANCHOR_MAX_MM = 5000.0
# route_stub_pattern.feat 및 route_stub_template.avg_feat와 일치해야 하는 차원.
FEAT_DIM = 24


@dataclass
class Anchor:
    """Stub이 붙는 기준 객체.

    kind:
        EQUIP, DUCT, LATERAL 중 하나. START Stub은 주로 EQUIP, END Stub은 DUCT/LATERAL에 붙는다.
    name:
        장비/덕트/레터럴 이름. 패턴 조회와 로그에서 사람이 식별하기 위한 값이다.
    utility:
        Anchor가 가진 유틸리티 코드. End Stub anchor 매칭 시 보조 필터로 사용한다.
    min_pt/max_pt:
        Anchor AABB의 최소/최대 좌표. face 판정, 상대좌표, rise/offset 계산 기준이다.
    """
    kind: str
    name: str
    utility: str | None
    min_pt: tuple[float, float, float]
    max_pt: tuple[float, float, float]


@dataclass
class RouteRecord:
    """기존 설계 배관 1개를 route 단위로 복원한 메모리 모델.

    TB_ROUTE_PATH의 메타데이터와 TB_ROUTE_SEGMENT_DETAIL에서 복원한 중심선 폴리라인을 함께 담는다.
    extract 단계는 RouteRecord 하나에서 START/END 최대 2개의 StubSample을 만든다.
    """
    guid: str
    process_name: str | None
    equipment_name: str | None
    utility_group: str | None
    utility: str | None
    size: str | None
    source_pos: tuple[float, float, float] | None
    target_pos: tuple[float, float, float] | None
    points: list[tuple[float, float, float]]


@dataclass
class StubSample:
    """DB에 저장되는 개별 Stub 패턴 샘플.

    하나의 ROUTE_PATH_GUID에서 START 또는 END 한쪽 끝을 잘라낸 결과다.
    `feat`는 pgvector 검색/집계용 24D 특징벡터이고, `stub_points`는 실제 월드 좌표 점열이다.
    """
    pattern_id: str
    route_path_guid: str
    stub_kind: str
    anchor_kind: str
    anchor_name: str | None
    main_equipment_name: str | None
    process_name: str | None
    utility_group: str | None
    utility: str | None
    size: str | None
    face: str
    dir_seq: list[str]
    n_bends: int
    rise_mm: float
    offset_mm: float
    diameter_mm: float | None
    stub_length_mm: float
    source_pos: tuple[float, float, float] | None
    target_pos: tuple[float, float, float] | None
    anchor_min: tuple[float, float, float]
    anchor_max: tuple[float, float, float]
    stub_points: list[tuple[float, float, float]]
    feat: list[float]
    dir_unit: list[float]


def main() -> int:
    """CLI 진입점.

    Subcommand별 역할:
    - create-schema: 저장 테이블 생성
    - extract: 기존 설계 배관에서 Stub sample 추출
    - build-template: sample을 그룹화해 재사용 template 생성
    - query-template: 조건에 맞는 template 조회
    - make-stub: 신규 자동배관설계 입력 좌표에 template을 적용해 Stub 후보 생성
    - run-all: schema 생성, extract, build-template 순차 실행
    - validate-existing-route: 특정 기존 route 하나의 Stub 추출 결과 확인
    """
    parser = argparse.ArgumentParser(
        description="Extract, store, query, and apply Start/End stub routing patterns from DDW_AI_DB."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ["create-schema", "extract", "build-template", "query-template", "make-stub", "run-all", "validate-existing-route"]:
        p = sub.add_parser(name)
        add_common_args(p)

    extract = sub.choices["extract"]
    add_filter_args(extract)
    extract.add_argument("--limit", type=int, default=None)
    extract.add_argument("--dry-run", action="store_true")
    extract.add_argument("--export-json", default=None)
    extract.add_argument("--replace", action="store_true", help="Delete existing samples matching current filters before insert")

    tmpl = sub.choices["build-template"]
    add_filter_args(tmpl)
    tmpl.add_argument("--min-samples", type=int, default=3)
    tmpl.add_argument("--replace", action="store_true")

    query = sub.choices["query-template"]
    add_request_args(query)
    query.add_argument("--stub-kind", choices=["START", "END"], default=None)
    query.add_argument("--max-candidates", type=int, default=10)
    query.add_argument("--export-json", default=None)

    make = sub.choices["make-stub"]
    add_request_args(make)
    make.add_argument("--source-pos", required=True)
    make.add_argument("--target-pos", required=True)
    make.add_argument("--source-anchor-min", default=None)
    make.add_argument("--source-anchor-max", default=None)
    make.add_argument("--target-anchor-min", default=None)
    make.add_argument("--target-anchor-max", default=None)
    make.add_argument("--max-candidates", type=int, default=5)
    make.add_argument("--export-json", default=None)
    make.add_argument("--log-application", action="store_true")
    make.add_argument("--request-id", default=None)

    run_all = sub.choices["run-all"]
    add_filter_args(run_all)
    run_all.add_argument("--limit", type=int, default=None)
    run_all.add_argument("--min-samples", type=int, default=3)
    run_all.add_argument("--replace", action="store_true")
    run_all.add_argument("--export-json", default=None)

    valid = sub.choices["validate-existing-route"]
    valid.add_argument("--route-path-guid", required=True)
    valid.add_argument("--export-json", default=None)

    args = parser.parse_args()
    try:
        runtime = resolve_runtime(args)
    except FileNotFoundError as ex:
        raise SystemExit(str(ex)) from ex
    print_runtime(runtime)

    with open_connection(runtime.conninfo) as conn:
        if args.command == "create-schema":
            create_schema(conn)
        elif args.command == "extract":
            samples = extract_samples(conn, args)
            emit_extract_result(conn, args, samples)
        elif args.command == "build-template":
            templates = build_templates(conn, args)
            print(f"Built templates: {len(templates)}")
        elif args.command == "query-template":
            rows = query_templates(conn, args)
            print_json_or_table(rows, args.export_json)
        elif args.command == "make-stub":
            result = make_stub_candidates(conn, args)
            if args.log_application:
                log_application(conn, args, result)
            print_json_or_table(result, args.export_json)
        elif args.command == "run-all":
            create_schema(conn)
            samples = extract_samples(conn, args)
            emit_extract_result(conn, args, samples)
            templates = build_templates(conn, args)
            print(f"Run-all complete. samples={len(samples)}, templates={len(templates)}")
        elif args.command == "validate-existing-route":
            result = validate_existing_route(conn, args)
            print_json_or_table(result, args.export_json)
        else:
            raise ValueError(args.command)
    return 0


def open_connection(conninfo: str):
    """PostgreSQL 연결을 생성한다.

    psycopg2/libpq는 Windows에서 접속 실패 메시지가 CP949 등 비 UTF-8로 돌아올 때
    UnicodeDecodeError를 낼 수 있다. 이 함수는 그 경우 사용자가 실제 원인(설정 파일 누락,
    비밀번호 오류 등)을 바로 알 수 있도록 SystemExit 메시지로 바꾼다.
    """
    try:
        return psycopg2.connect(conninfo)
    except UnicodeDecodeError as ex:
        raise SystemExit(
            "DB connection failed, and libpq returned a non-UTF-8 error message.\n"
            "Most likely causes:\n"
            "  - Tools/tools.settings.json does not exist or has an empty/wrong password.\n"
            "  - The DB name/user/password is incorrect.\n"
            "Fix:\n"
            "  1) Copy Tools/tools.settings.example.json to Tools/tools.settings.json and fill the password, or\n"
            "  2) Pass --host --port --dbname --user --password explicitly.\n"
            f"Raw decode error: {ex}"
        ) from ex
    except psycopg2.OperationalError as ex:
        raise SystemExit(f"DB connection failed: {ex}") from ex


def add_filter_args(parser: argparse.ArgumentParser) -> None:
    """extract/build-template/run-all에서 쓰는 공통 필터 인자를 추가한다."""
    parser.add_argument("--main-equipment", default=None)
    parser.add_argument("--utility-group", default=None)
    parser.add_argument("--utility", default=None)
    parser.add_argument("--size", default=None)


def add_request_args(parser: argparse.ArgumentParser) -> None:
    """신규 자동설계 요청 계열(query-template/make-stub)의 필수 조건 인자를 추가한다."""
    parser.add_argument("--main-equipment", required=True)
    parser.add_argument("--utility-group", required=True)
    parser.add_argument("--utility", default=None)
    parser.add_argument("--size", default=None)


def create_schema(conn) -> None:
    """Stub 패턴 저장용 DB 스키마를 생성한다.

    pgvector가 설치되어 있으면 Tools/sql/create_route_stub_pattern_tables.sql을 실행한다.
    pgvector가 없으면 vector 컬럼 없이 JSON 컬럼만 가진 fallback schema를 생성한다.
    """
    has_vector = pgvector_installed(conn)
    with conn.cursor() as cur:
        if has_vector:
            sql_path = Path(__file__).resolve().parent / "sql" / "create_route_stub_pattern_tables.sql"
            cur.execute(sql_path.read_text(encoding="utf-8"))
        else:
            print("[warn] pgvector extension not found. Creating JSON fallback columns only.")
            cur.execute(fallback_schema_sql())
    conn.commit()
    print(f"Schema ready. pgvector={'yes' if has_vector else 'no'}")


def fallback_schema_sql() -> str:
    """pgvector가 없는 DB에서도 extract/query가 동작하도록 만드는 fallback DDL."""
    return """
CREATE TABLE IF NOT EXISTS "TB_ROUTE_STUB_PATTERN" (
    "PATTERN_ID" text PRIMARY KEY,
    "ROUTE_PATH_GUID" text NOT NULL,
    "STUB_KIND" text NOT NULL,
    "ANCHOR_KIND" text NOT NULL,
    "ANCHOR_NAME" text,
    "MAIN_EQUIPMENT_NAME" text,
    "PROCESS_NAME" text,
    "UTILITY_GROUP" text,
    "UTILITY" text,
    "SIZE" text,
    "FACE" text,
    "DIR_SEQ" text,
    "N_BENDS" integer,
    "RISE_MM" double precision,
    "OFFSET_MM" double precision,
    "DIAMETER_MM" double precision,
    "STUB_LENGTH_MM" double precision,
    "SOURCE_POS" jsonb,
    "TARGET_POS" jsonb,
    "ANCHOR_MIN" jsonb,
    "ANCHOR_MAX" jsonb,
    "STUB_POINTS" jsonb,
    "FEAT_JSON" jsonb,
    "DIR_UNIT_JSON" jsonb,
    "CREATED_AT" timestamp without time zone DEFAULT now()
);
CREATE INDEX IF NOT EXISTS "IX_TRSP_KEY"
ON "TB_ROUTE_STUB_PATTERN" ("MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "SIZE", "STUB_KIND", "ANCHOR_KIND");
CREATE TABLE IF NOT EXISTS "TB_ROUTE_STUB_TEMPLATE" (
    "TEMPLATE_ID" text PRIMARY KEY,
    "STUB_KIND" text NOT NULL,
    "ANCHOR_KIND" text NOT NULL,
    "MAIN_EQUIPMENT_NAME" text,
    "UTILITY_GROUP" text,
    "UTILITY" text,
    "SIZE" text,
    "FACE" text,
    "DIR_SEQ" text,
    "SAMPLE_COUNT" integer NOT NULL,
    "AVG_RISE_MM" double precision,
    "AVG_OFFSET_MM" double precision,
    "AVG_DIAMETER_MM" double precision,
    "AVG_STUB_LENGTH_MM" double precision,
    "REPRESENTATIVE_PATTERN_ID" text,
    "REPRESENTATIVE_ROUTE_PATH_GUID" text,
    "REPRESENTATIVE_STUB_POINTS" jsonb,
    "AVG_FEAT_JSON" jsonb,
    "AVG_DIR_UNIT_JSON" jsonb,
    "CREATED_AT" timestamp without time zone DEFAULT now()
);
CREATE INDEX IF NOT EXISTS "IX_TRST_KEY"
ON "TB_ROUTE_STUB_TEMPLATE" ("MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "SIZE", "STUB_KIND", "ANCHOR_KIND");
CREATE TABLE IF NOT EXISTS "TB_ROUTE_STUB_APPLICATION_LOG" (
    "APPLICATION_ID" text PRIMARY KEY,
    "REQUEST_ID" text,
    "SOURCE_TEMPLATE_ID" text,
    "TARGET_TEMPLATE_ID" text,
    "MAIN_EQUIPMENT_NAME" text,
    "UTILITY_GROUP" text,
    "UTILITY" text,
    "SIZE" text,
    "START_STUB_POINTS" jsonb,
    "END_STUB_POINTS" jsonb,
    "MIDDLE_ROUTE_POINTS" jsonb,
    "FINAL_ROUTE_POINTS" jsonb,
    "SCORE" double precision,
    "STATUS" text,
    "FAIL_REASON" text,
    "CREATED_AT" timestamp without time zone DEFAULT now()
);
"""


def extract_samples(conn, args) -> list[StubSample]:
    """기존 설계 배관에서 START/END Stub sample을 추출한다.

    처리 흐름:
    1. fetch_routes로 route path + segment detail 폴리라인을 복원한다.
    2. fetch_anchors로 START용 메인장비 anchor와 END용 DUCT/LATERAL anchor를 로드한다.
    3. 각 route에 대해 source_pos는 START, target_pos는 END로 make_sample을 호출한다.
    4. dry-run이면 저장하지 않고 요약만 출력한다.
    5. dry-run이 아니면 schema를 보장한 뒤 TB_ROUTE_STUB_PATTERN에 upsert한다.
    """
    routes = fetch_routes(conn, args)
    equip_anchors = fetch_anchors(conn, "EQUIP")
    end_anchors = fetch_anchors(conn, "DUCT") + fetch_anchors(conn, "LATERAL")
    print(f"Routes loaded: {len(routes)}")
    print(f"Equipment anchors: {len(equip_anchors)}, target anchors: {len(end_anchors)}")

    samples: list[StubSample] = []
    skipped = Counter()
    for route in routes:
        if len(route.points) < 2:
            skipped["too_few_points"] += 1
            continue
        if not route.source_pos or not route.target_pos:
            skipped["missing_source_or_target_pos"] += 1
            continue
        # route를 source_pos가 앞에 오도록 한 번만 정규 정렬한 뒤 PathSegmenter.segment_route()를
        # 1회 호출한다 — Start/End Stub 양쪽 다 여기서 얻은 결과를 재사용한다(3.3절 통합).
        canonical_pts = orient_points(route.points, route.source_pos)
        segmentation = segment_route(canonical_pts)
        start_anchor = find_anchor(equip_anchors, route.source_pos, route.equipment_name, None)
        end_anchor = find_anchor(end_anchors, route.target_pos, None, route.utility)
        if not end_anchor:
            end_anchor = find_anchor(equip_anchors, route.target_pos, None, route.utility)
        if not start_anchor:
            skipped["missing_start_anchor"] += 1
        else:
            sample = make_sample(route, "START", start_anchor, segmentation)
            if sample:
                samples.append(sample)
            else:
                skipped["start_stub_failed"] += 1
        if not end_anchor:
            skipped["missing_end_anchor"] += 1
        else:
            sample = make_sample(route, "END", end_anchor, segmentation)
            if sample:
                samples.append(sample)
            else:
                skipped["end_stub_failed"] += 1

    print_summary(samples, skipped)
    if not getattr(args, "dry_run", False):
        create_schema(conn)
        if getattr(args, "replace", False):
            delete_samples(conn, args)
        insert_samples(conn, samples)
        conn.commit()
        print(f"Inserted samples: {len(samples)}")
    return samples


def emit_extract_result(conn, args, samples: list[StubSample]) -> None:
    """extract/run-all 결과를 선택적으로 JSON 파일로 내보낸다."""
    path = getattr(args, "export_json", None)
    if path:
        write_json(path, [sample_to_json(s) for s in samples])
        print(f"Exported samples: {path}")


def fetch_routes(conn, args) -> list[RouteRecord]:
    """DB에서 기존 route 목록과 폴리라인을 읽어 RouteRecord 목록으로 변환한다.

    컬럼명이 일부 프로젝트마다 다를 수 있어 `first_col`로 후보 컬럼을 자동 선택한다.
    예: 장비명은 EQUIPMENT_NAME, EQUIPMENT_TAG, SOURCE_OWNER_NAME 순으로 탐색한다.
    """
    cols = table_columns(conn, "TB_ROUTE_PATH")
    required = {"ROUTE_PATH_GUID"}
    missing = required - cols
    if missing:
        raise RuntimeError(f"TB_ROUTE_PATH missing columns: {sorted(missing)}")

    select_map = {
        "guid": "ROUTE_PATH_GUID",
        "process_name": first_col(cols, "PROCESS_NAME"),
        "equipment_name": first_col(cols, "EQUIPMENT_NAME", "EQUIPMENT_TAG", "SOURCE_OWNER_NAME"),
        "utility_group": first_col(cols, "UTILITY_GROUP"),
        "utility": first_col(cols, "SOURCE_UTILITY", "UTILITY"),
        "size": first_col(cols, "SOURCE_SIZE", "SIZE"),
        "source_posx": first_col(cols, "SOURCE_POSX"),
        "source_posy": first_col(cols, "SOURCE_POSY"),
        "source_posz": first_col(cols, "SOURCE_POSZ"),
        "target_posx": first_col(cols, "TARGET_POSX"),
        "target_posy": first_col(cols, "TARGET_POSY"),
        "target_posz": first_col(cols, "TARGET_POSZ"),
    }
    sql_cols = []
    for alias, col in select_map.items():
        if col:
            sql_cols.append(f'rp."{col}" AS "{alias}"')
        else:
            sql_cols.append(f'NULL AS "{alias}"')

    where = []
    params: list[Any] = []
    if getattr(args, "main_equipment", None) and select_map["equipment_name"]:
        where.append(f'rp."{select_map["equipment_name"]}" ILIKE %s')
        params.append(f"%{args.main_equipment}%")
    if getattr(args, "utility_group", None) and select_map["utility_group"]:
        where.append(f'rp."{select_map["utility_group"]}" = %s')
        params.append(args.utility_group)
    if getattr(args, "utility", None) and select_map["utility"]:
        where.append(f'rp."{select_map["utility"]}" = %s')
        params.append(args.utility)
    if getattr(args, "size", None) and select_map["size"]:
        where.append(f'rp."{select_map["size"]}" = %s')
        params.append(args.size)
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    limit_sql = ""
    if getattr(args, "limit", None):
        limit_sql = "LIMIT %s"
        params.append(args.limit)

    route_sql = f'''
        SELECT {", ".join(sql_cols)}
        FROM "TB_ROUTE_PATH" rp
        {where_sql}
        ORDER BY rp."ROUTE_PATH_GUID"
        {limit_sql}
    '''

    routes: list[RouteRecord] = []
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(route_sql, params)
        for row in cur.fetchall():
            guid = str(row["guid"]).strip()
            points = fetch_route_points(conn, guid)
            routes.append(RouteRecord(
                guid=guid,
                process_name=row.get("process_name"),
                equipment_name=row.get("equipment_name"),
                utility_group=row.get("utility_group"),
                utility=row.get("utility"),
                size=row.get("size"),
                source_pos=triple(row.get("source_posx"), row.get("source_posy"), row.get("source_posz")),
                target_pos=triple(row.get("target_posx"), row.get("target_posy"), row.get("target_posz")),
                points=points,
            ))
    return routes


def fetch_route_points(conn, guid: str) -> list[tuple[float, float, float]]:
    """ROUTE_PATH_GUID 하나의 중심선 폴리라인을 복원한다.

    TB_ROUTE_SEGMENTS.ORDER, TB_ROUTE_SEGMENT_DETAIL.ORDER 순서로 FROM/TO 좌표를 이어붙이고,
    ELBOW 타입 세그먼트를 감지하여 기하학적 꺾임점(IP)을 복원한다.
    """
    sql = '''
        SELECT sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",
               sd."TO_POSX", sd."TO_POSY", sd."TO_POSZ",
               sd."TYPE"
        FROM "TB_ROUTE_SEGMENTS" rs
        JOIN "TB_ROUTE_SEGMENT_DETAIL" sd ON rs."SEGMENT_GUID" = sd."SEGMENT_GUID"
        WHERE rs."ROUTE_PATH_GUID" = %s
        ORDER BY rs."ORDER", sd."ORDER"
    '''
    raw_segs = []
    with conn.cursor() as cur:
        cur.execute(sql, (guid,))
        for row in cur.fetchall():
            a = triple(row[0], row[1], row[2])
            b = triple(row[3], row[4], row[5])
            t = row[6]
            if a and b:
                raw_segs.append({'from': a, 'to': b, 'type': t})

    if not raw_segs:
        return []

    pts: list[tuple[float, float, float]] = []
    pts.append(raw_segs[0]['from'])

    def sub(v1, v2):
        return (v1[0] - v2[0], v1[1] - v2[1], v1[2] - v2[2])

    def add(v1, v2):
        return (v1[0] + v2[0], v1[1] + v2[1], v1[2] + v2[2])

    def mult(v1, s):
        return (v1[0] * s, v1[1] * s, v1[2] * s)

    def dot(v1, v2):
        return sum(x * y for x, y in zip(v1, v2))

    def norm(v):
        l = math.sqrt(dot(v, v))
        return mult(v, 1.0 / l) if l > 1e-3 else v

    i = 0
    n = len(raw_segs)
    while i < n:
        cur = raw_segs[i]

        # ELBOW 타입인 경우 인접한 직선 세그먼트들과의 교차점(IP) 복원
        if cur['type'] == 'ELBOW' and i > 0 and i < n - 1:
            prev_seg = raw_segs[i - 1]
            next_seg = raw_segs[i + 1]

            p1 = prev_seg['to']
            p2 = next_seg['from']

            v1 = norm(sub(prev_seg['to'], prev_seg['from']))
            v2 = norm(sub(next_seg['to'], next_seg['from']))

            w0 = sub(p1, p2)

            a_val = dot(v1, v1)
            b_val = dot(v1, v2)
            c_val = dot(v2, v2)
            d_val = dot(v1, w0)
            e_val = dot(v2, w0)

            denom = a_val * c_val - b_val * b_val
            if denom > 1e-6:
                t = (b_val * e_val - c_val * d_val) / denom
                s = (a_val * e_val - b_val * d_val) / denom

                q1 = add(p1, mult(v1, t))
                q2 = add(p2, mult(v2, s))

                # 3D 교차 근접 지점의 중심을 가상 IP로 산정
                ip = mult(add(q1, q2), 0.5)

                skew_dist = dist(q1, q2)
                if skew_dist < 500.0:
                    if pts:
                        pts[-1] = ip
                    else:
                        pts.append(ip)
                    # ELBOW 세그먼트 건너뛰기
                    i += 1
                    continue

        to_pt = cur['to']
        if not pts or dist(pts[-1], to_pt) > 1e-3:
            pts.append(to_pt)
        i += 1

    return pts


def fetch_anchors(conn, kind: str) -> list[Anchor]:
    """장비/덕트/레터럴 AABB를 Anchor 목록으로 로드한다.

    kind가 EQUIP이면 TB_EQUIPMENTS/TB_BIM_EQUIPMENT를, DUCT이면 TB_DUCT/TB_DUCT_LATERAL을,
    LATERAL이면 TB_LATERAL_PIPE를 탐색한다. 실제 존재하는 테이블과 컬럼만 사용한다.
    """
    if kind == "EQUIP":
        tables = ["TB_EQUIPMENTS", "TB_BIM_EQUIPMENT"]
        name_candidates = ["INSTANCE_NAME", "NAME", "EQUIPMENT_NAME", "TAG"]
    elif kind == "DUCT":
        tables = ["TB_DUCT", "TB_DUCT_LATERAL"]
        name_candidates = ["INSTANCE_NAME", "NAME", "DUCT_NAME", "TAG"]
    else:
        tables = ["TB_LATERAL_PIPE"]
        name_candidates = ["INSTANCE_NAME", "NAME", "LATERAL_NUMBER", "TAG"]

    result: list[Anchor] = []
    for table in tables:
        if not table_exists(conn, table):
            continue
        cols = table_columns(conn, table)
        aabb = [first_col(cols, name) for name in ["AABB_MINX", "AABB_MINY", "AABB_MINZ", "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"]]
        if not all(aabb):
            continue
        name_col = first_col(cols, *name_candidates)
        utility_col = first_col(cols, "UTILITY", "SOURCE_UTILITY")
        select = [
            f'"{name_col}" AS name' if name_col else "NULL AS name",
            f'"{utility_col}" AS utility' if utility_col else "NULL AS utility",
            f'"{aabb[0]}" AS minx', f'"{aabb[1]}" AS miny', f'"{aabb[2]}" AS minz',
            f'"{aabb[3]}" AS maxx', f'"{aabb[4]}" AS maxy', f'"{aabb[5]}" AS maxz',
        ]
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f'SELECT {", ".join(select)} FROM "{table}" WHERE "{aabb[0]}" IS NOT NULL')
            for row in cur.fetchall():
                min_pt = triple(row["minx"], row["miny"], row["minz"])
                max_pt = triple(row["maxx"], row["maxy"], row["maxz"])
                if min_pt and max_pt:
                    result.append(Anchor(kind, row.get("name") or table, row.get("utility"), min_pt, max_pt))
    return result


def make_sample(route: RouteRecord, stub_kind: str, anchor: Anchor, segmentation: tuple) -> StubSample | None:
    """RouteRecord 한쪽 끝에서 StubSample 하나를 만든다.

    segmentation은 extract_samples가 route 하나당 한 번만 계산한
    PathSegmenter.segment_route() 결과(start_pts, middle_pts, end_pts, start_fp, end_fp,
    entry_dir)다. START는 segment_route()의 Start Stub(CSF 평면 인식 포함)을, END는 End
    Stub을 PoC가 앞에 오도록 뒤집어서 그대로 재사용한다 — 이 저장소 안에서 "stub 경계가
    어디인가"를 두 번 다르게 계산하지 않기 위함이다(Docs/FeaturePattern_Pipeline_Overlap_Review.md 3.3절).
    """
    # [1] 시작 좌표(source_pos)와 종단 좌표(target_pos) 획득
    source = route.source_pos
    target = route.target_pos
    if not source or not target:
        return None

    # [2] segment_route() 결과에서 stub_kind에 맞는 쪽을 골라 PoC가 앞에 오도록 정렬
    start_pts, _middle_pts, end_pts, _start_fp, _end_fp, _entry_dir = segmentation
    stub_points = start_pts if stub_kind == "START" else list(reversed(end_pts)) if end_pts else []
    if len(stub_points) < 2:
        return None

    # [3] 잘라낸 stub 구간 내 고유 진행 방향(방향열) 인덱스 축약 생성 (최대 4개)
    dir_ids = []
    for a, b in zip(stub_points, stub_points[1:]):
        direction = axis_snap(vec_sub(b, a))
        if direction not in dir_ids:
            dir_ids.append(direction)
    dir_ids = dir_ids[:4]

    # [4] 앵커(장비/덕트/레터럴/부대설비) 기준 매칭 정보 연산
    poc = source if stub_kind == "START" else target
    face_id, offset = nearest_face(anchor, poc)  # PoC가 앵커의 어느 면(Face)에 가깝고 얼마나 떨어져(Offset) 있는지 연산
    face = AXIS_NAMES[face_id]                   # 면 이름을 문자열(예: +z)로 획득
    dir_seq = [AXIS_NAMES[i] for i in dir_ids]   # 진행방향 시퀀스를 문자열 배열로 획득
    
    # [5] 진행 단위벡터 및 특징벡터 생성
    route_unit = unit(vec_sub(target, source))    # 전체 배관의 주 진행 단위벡터
    # END Stub의 경우 앵커를 향해 들어오는 역방향 단위벡터로 부호 반전
    dir_unit = list(route_unit if stub_kind == "START" else tuple(-v for v in route_unit))
    rel = relative_pos(anchor, poc)              # 앵커 AABB 기준 PoC의 상대적인 3차원 위치비율 [0..1]
    feat = build_feature(face_id, dir_ids, rel, dir_unit) # 패턴 매칭을 위한 24D 특징벡터 생성
    
    # [6] 라이즈(수직높이), 스텁 길이 등 속성 연산
    rise = compute_rise(stub_points, poc, face_id) # 앵커 Face 법선축 기준의 라이즈 높이 계산
    length = polyline_length(stub_points)          # 추출된 Stub의 전체 중심선 길이
    pattern_id = stable_id(route.guid, stub_kind, anchor.kind, anchor.name, ",".join(dir_seq), face) # 고유한 해시 ID 생성
    
    return StubSample(
        pattern_id=pattern_id,
        route_path_guid=route.guid,
        stub_kind=stub_kind,
        anchor_kind=anchor.kind,
        anchor_name=anchor.name,
        main_equipment_name=route.equipment_name,
        process_name=route.process_name,
        utility_group=route.utility_group,
        utility=route.utility,
        size=route.size,
        face=face,
        dir_seq=dir_seq,
        n_bends=max(0, len(dir_seq) - 1),
        rise_mm=rise,
        offset_mm=offset,
        diameter_mm=parse_size_to_diameter(route.size),
        stub_length_mm=length,
        source_pos=source,
        target_pos=target,
        anchor_min=anchor.min_pt,
        anchor_max=anchor.max_pt,
        stub_points=stub_points,
        feat=feat,
        dir_unit=dir_unit,
    )


def orient_points(points: list[tuple[float, float, float]], front: tuple[float, float, float]) -> list[tuple[float, float, float]]:
    """front 좌표에 더 가까운 route 끝점이 points[0]이 되도록 폴리라인 방향을 맞춘다."""
    if not points:
        return points
    return list(reversed(points)) if dist(points[-1], front) < dist(points[0], front) else list(points)


def dir_runs(seg: list[tuple[float, float, float]]) -> list[list[float]]:
    """폴리라인을 6축 방향 run으로 압축한다.

    예: 여러 segment가 연속으로 +z 방향이면 하나의 [+z, 누적길이] run으로 합쳐진다.
    이렇게 하면 정점 개수와 무관하게 실제 방향 변화 지점만 남길 수 있다.
    """
    runs: list[list[float]] = []
    for a, b in zip(seg, seg[1:]):
        length = dist(a, b)
        if length < 1e-6:
            continue
        direction = axis_snap(vec_sub(b, a))
        if runs and int(runs[-1][0]) == direction:
            runs[-1][1] += length
        else:
            runs.append([direction, length])
    return runs


def merge_short_runs(runs: list[list[float]]) -> list[tuple[int, float]]:
    """250mm 미만의 짧은 방향 run을 설계 지터로 보고 인접 run에 흡수한다.

    특히 -z, -y(120mm), -z 같은 패턴을 하나의 긴 -z run으로 복원해
    미세 옵셋이 가짜 엘보로 잡히는 문제를 방지한다.
    """
    runs = [list(r) for r in runs]
    while len(runs) > 1:
        idx = min(range(len(runs)), key=lambda i: runs[i][1])
        if runs[idx][1] >= STUB_MIN_DIR_RUN_MM:
            break
        if idx == 0:
            runs[1][1] += runs[0][1]
            del runs[0]
        elif idx == len(runs) - 1:
            runs[-2][1] += runs[-1][1]
            del runs[-1]
        elif runs[idx - 1][0] == runs[idx + 1][0]:
            runs[idx - 1][1] += runs[idx][1] + runs[idx + 1][1]
            del runs[idx: idx + 2]
        elif runs[idx - 1][1] >= runs[idx + 1][1]:
            runs[idx - 1][1] += runs[idx][1]
            del runs[idx]
        else:
            runs[idx + 1][1] += runs[idx][1]
            del runs[idx]
    return [(int(d), float(length)) for d, length in runs]


def points_until(seg: list[tuple[float, float, float]], length: float) -> list[tuple[float, float, float]]:
    """폴리라인 시작점부터 지정 길이까지 점열을 자른다.

    컷 지점이 segment 중간이면 선형 보간으로 마지막 점을 만든다.
    """
    if not seg:
        return []
    out = [seg[0]]
    remain = length
    for a, b in zip(seg, seg[1:]):
        edge = dist(a, b)
        if edge < 1e-6:
            continue
        if remain >= edge:
            out.append(b)
            remain -= edge
        else:
            t = max(0.0, min(1.0, remain / edge))
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t))
            break
    return out


def build_feature(face_id: int, dir_ids: list[int], rel: tuple[float, float, float], dir_unit: list[float]) -> list[float]:
    """StubSample의 24D 특징벡터를 생성한다.

    구성:
    - 0..5: anchor face one-hot
    - 6..11: 1차 진행 방향 one-hot
    - 12..17: 2차 진행 방향, 즉 첫 엘보 방향 one-hot
    - 18..20: PoC의 anchor AABB 내부 상대좌표
    - 21..23: route 진행 단위벡터. END Stub은 anchor로 접근하는 방향이 되도록 부호 반전
    """
    feat = [0.0] * FEAT_DIM
    feat[face_id] = 1.0
    if dir_ids:
        feat[6 + dir_ids[0]] = 1.0
    if len(dir_ids) > 1:
        feat[12 + dir_ids[1]] = 1.0
    feat[18:21] = list(rel)
    feat[21:24] = list(dir_unit)
    return feat


def build_templates(conn, args) -> list[dict[str, Any]]:
    """TB_ROUTE_STUB_PATTERN sample을 TB_ROUTE_STUB_TEMPLATE으로 집계한다.

    그룹 키는 STUB_KIND, ANCHOR_KIND, MAIN_EQUIPMENT_NAME, UTILITY_GROUP, UTILITY, SIZE,
    FACE, DIR_SEQ이다. 같은 조건에서 min-samples 이상 반복된 패턴만 신규 자동설계에
    재사용할 수 있는 template으로 저장한다.
    """
    create_schema(conn)
    samples = load_samples(conn, args)
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in samples:
        key = (
            row["STUB_KIND"], row["ANCHOR_KIND"], row["MAIN_EQUIPMENT_NAME"],
            row["UTILITY_GROUP"], row["UTILITY"], row["SIZE"], row["FACE"], row["DIR_SEQ"],
        )
        groups[key].append(row)
    templates = []
    for key, rows in groups.items():
        if len(rows) < args.min_samples:
            continue
        rep = min(rows, key=lambda r: abs(float_or_zero(r["STUB_LENGTH_MM"]) - avg(rows, "STUB_LENGTH_MM")))
        avg_feat = mean_vectors([json_value(r["FEAT_JSON"]) for r in rows if r.get("FEAT_JSON")])
        avg_dir = mean_vectors([json_value(r["DIR_UNIT_JSON"]) for r in rows if r.get("DIR_UNIT_JSON")])
        template_id = stable_id("template", *[str(v) for v in key])
        templates.append({
            "TEMPLATE_ID": template_id,
            "STUB_KIND": key[0],
            "ANCHOR_KIND": key[1],
            "MAIN_EQUIPMENT_NAME": key[2],
            "UTILITY_GROUP": key[3],
            "UTILITY": key[4],
            "SIZE": key[5],
            "FACE": key[6],
            "DIR_SEQ": key[7],
            "SAMPLE_COUNT": len(rows),
            "AVG_RISE_MM": avg(rows, "RISE_MM"),
            "AVG_OFFSET_MM": avg(rows, "OFFSET_MM"),
            "AVG_DIAMETER_MM": avg(rows, "DIAMETER_MM"),
            "AVG_STUB_LENGTH_MM": avg(rows, "STUB_LENGTH_MM"),
            "REPRESENTATIVE_PATTERN_ID": rep["PATTERN_ID"],
            "REPRESENTATIVE_ROUTE_PATH_GUID": rep["ROUTE_PATH_GUID"],
            "REPRESENTATIVE_STUB_POINTS": rep["STUB_POINTS"],
            "AVG_FEAT_JSON": avg_feat,
            "AVG_DIR_UNIT_JSON": avg_dir,
        })
    if getattr(args, "replace", False):
        delete_templates(conn, args)
    insert_templates(conn, templates)
    conn.commit()
    return templates


def query_templates(conn, args) -> list[dict[str, Any]]:
    """신규 자동설계 조건에 맞는 Stub template 후보를 조회한다.

    조회 fallback 순서:
    1. 메인장비 + 유틸리티그룹 + 유틸리티 + 사이즈
    2. 메인장비 + 유틸리티그룹 + 유틸리티
    3. 유틸리티그룹 + 유틸리티
    4. 유틸리티그룹

    검색 결과는 sample_count가 큰 template을 우선한다.
    """
    if not table_exists(conn, "TB_ROUTE_STUB_TEMPLATE"):
        raise RuntimeError("TB_ROUTE_STUB_TEMPLATE does not exist. Run create-schema and build-template first.")
    where = []
    params = []
    fallback_levels = [
        ("MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "SIZE"),
        ("MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY"),
        ("UTILITY_GROUP", "UTILITY"),
        ("UTILITY_GROUP",),
    ]
    base = {"MAIN_EQUIPMENT_NAME": args.main_equipment, "UTILITY_GROUP": args.utility_group, "UTILITY": args.utility, "SIZE": args.size}
    for level in fallback_levels:
        where = []
        params = []
        for col in level:
            if base.get(col) is not None:
                where.append(f'"{col}" = %s')
                params.append(base[col])
        if args.stub_kind:
            where.append('"STUB_KIND" = %s')
            params.append(args.stub_kind)
        sql = f'''
            SELECT * FROM "TB_ROUTE_STUB_TEMPLATE"
            {"WHERE " + " AND ".join(where) if where else ""}
            ORDER BY "SAMPLE_COUNT" DESC, "AVG_STUB_LENGTH_MM" ASC NULLS LAST
            LIMIT %s
        '''
        params.append(args.max_candidates)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            if rows:
                return normalize_json_rows(rows)
    return []


def make_stub_candidates(conn, args) -> dict[str, Any]:
    """조회된 Start/End template을 신규 PoC 좌표에 적용해 Stub 후보 조합을 만든다.

    반환되는 후보는 다음 정보를 포함한다.
    - start_stub.points: source_pos에서 시작하는 Start Stub 점열
    - end_stub.points: target_pos에서 시작하는 End Stub 점열
    - middle_route: 중간 자동 라우터가 연결해야 할 [start_free_point, end_free_point]
    - score: template 반복도와 중간거리 penalty를 반영한 단순 점수
    """
    source_pos = parse_xyz(args.source_pos)
    target_pos = parse_xyz(args.target_pos)
    start_anchor = explicit_anchor(args.source_anchor_min, args.source_anchor_max, "EQUIP", args.main_equipment)
    target_anchor = explicit_anchor(args.target_anchor_min, args.target_anchor_max, "DUCT", "TARGET")
    request = {
        "main_equipment_name": args.main_equipment,
        "utility_group": args.utility_group,
        "utility": args.utility,
        "size": args.size,
        "source_pos": source_pos,
        "target_pos": target_pos,
    }
    start_templates = query_templates(conn, SimpleArgs(args, stub_kind="START", max_candidates=args.max_candidates))
    end_templates = query_templates(conn, SimpleArgs(args, stub_kind="END", max_candidates=args.max_candidates))
    start_candidates = [instantiate_stub(t, source_pos, start_anchor, forward=True) for t in start_templates]
    end_candidates = [instantiate_stub(t, target_pos, target_anchor, forward=False) for t in end_templates]
    combos = []
    for s in start_candidates:
        for e in end_candidates:
            middle = [s["free_point"], e["free_point"]]
            score = s["score"] + e["score"] - 0.00001 * dist(s["free_point"], e["free_point"])
            combos.append({"start_stub": s, "end_stub": e, "middle_route": middle, "score": score})
    combos.sort(key=lambda r: r["score"], reverse=True)
    return {"request": request, "candidates": combos[: args.max_candidates]}


class SimpleArgs:
    """query_templates 재사용을 위해 argparse.Namespace처럼 동작하는 가벼운 객체."""
    def __init__(self, base, **overrides):
        self.__dict__.update(vars(base))
        self.__dict__.update(overrides)


def instantiate_stub(template: dict[str, Any], poc: tuple[float, float, float], anchor: Anchor | None, forward: bool) -> dict[str, Any]:
    """Template 한 건을 신규 PoC 기준 월드 좌표 Stub 점열로 복원한다.

    현재 구현은 template의 평균 rise와 dir_seq를 사용해 2~3점짜리 직교 Stub을 만든다.
    anchor AABB가 명시되면 face 판단 보정에 사용하고, 없으면 template face를 그대로 따른다.
    """
    # [1] 앵커(설비박스)를 이용한 진출/진입 면(Face)의 축 ID 결정
    if anchor:
        face_id = AXIS_NAMES.index(template["FACE"]) if template.get("FACE") in AXIS_NAMES else nearest_face(anchor, poc)[0]
    else:
        face_id = AXIS_NAMES.index(template["FACE"]) if template.get("FACE") in AXIS_NAMES else 4 # 기본값: +z
        
    # [2] 템플릿에 저장되어 있던 상대적 진행 방향 시퀀스 로드
    dirs = [AXIS_NAMES.index(x) for x in str(template.get("DIR_SEQ") or "").split(",") if x in AXIS_NAMES]
    if not dirs:
        dirs = [face_id]
        
    # [3] START(forward=True)이면 템플릿 방향 그대로 진행, END(forward=False)이면 역방향으로 180도 축 반전
    if not forward:
        dirs = [opposite_axis(d) for d in dirs]
        
    # [4] 라이즈 높이와 총 리드인 길이 결정
    rise = float_or_zero(template.get("AVG_RISE_MM"))
    length = float_or_zero(template.get("AVG_STUB_LENGTH_MM")) or STUB_LEADIN_MM
    lead = min(STUB_LEADIN_MM, max(0.0, length - rise)) # 라이즈를 제하고 수평 rack으로 도달하는 남은 길이
    
    # [5] 3D 월드 좌표 점열 생성 시작 (기점 PoC부터 차례대로 방향축으로 선분 추가)
    pts = [poc]
    cur = poc
    # 첫 번째 진행: 앵커 면 법선 방향(dirs[0])으로 라이즈 길이만큼 연장
    first = dirs[0]
    cur = add_axis(cur, first, rise if rise > 0 else min(length, STUB_LEADIN_MM))
    pts.append(cur)
    
    # 두 번째 진행: 엘보 절곡 후 다음 진행 방향(dirs[1])으로 남은 길이만큼 연장
    if len(dirs) > 1 and lead > 0:
        cur = add_axis(cur, dirs[1], lead)
        pts.append(cur)
        
    return {
        "template_id": template.get("TEMPLATE_ID"),
        "stub_kind": template.get("STUB_KIND"),
        "anchor_kind": template.get("ANCHOR_KIND"),
        "face": template.get("FACE"),
        "dir_seq": template.get("DIR_SEQ"),
        "points": pts,
        "free_point": pts[-1], # 최종적으로 중간 오토라우터가 바인딩하여 연결할 접속자유점(Free Point)
        "score": float(template.get("SAMPLE_COUNT") or 1),
    }


def validate_existing_route(conn, args) -> dict[str, Any]:
    """기존 route 1건에서 START/END Stub을 다시 추출해 검증용 JSON으로 반환한다."""
    class A:
        main_equipment = None
        utility_group = None
        utility = None
        size = None
        limit = None
    routes = [r for r in fetch_routes(conn, A()) if r.guid == args.route_path_guid]
    if not routes:
        return {"status": "not_found", "route_path_guid": args.route_path_guid}
    route = routes[0]
    equip_anchors = fetch_anchors(conn, "EQUIP")
    end_anchors = fetch_anchors(conn, "DUCT") + fetch_anchors(conn, "LATERAL")
    start_anchor = find_anchor(equip_anchors, route.source_pos, route.equipment_name, None) if route.source_pos else None
    end_anchor = find_anchor(end_anchors, route.target_pos, None, route.utility) if route.target_pos else None
    if route.target_pos and not end_anchor:
        end_anchor = find_anchor(equip_anchors, route.target_pos, None, route.utility)
    segmentation = segment_route(orient_points(route.points, route.source_pos)) if len(route.points) >= 2 and route.source_pos else None
    start = make_sample(route, "START", start_anchor, segmentation) if start_anchor and segmentation else None
    end = make_sample(route, "END", end_anchor, segmentation) if end_anchor and segmentation else None
    return {
        "status": "ok",
        "route_path_guid": route.guid,
        "start_stub": sample_to_json(start) if start else None,
        "end_stub": sample_to_json(end) if end else None,
    }


def insert_samples(conn, samples: list[StubSample]) -> None:
    """StubSample 목록을 TB_ROUTE_STUB_PATTERN에 upsert한다.

    pgvector 컬럼이 있으면 FEAT/DIR_UNIT을 '[...]' 리터럴로 전달해 ::vector 캐스팅한다.
    pgvector fallback schema에서는 JSON 컬럼만 저장한다.
    """
    has_vector = pgvector_installed(conn) and has_column(conn, "TB_ROUTE_STUB_PATTERN", "FEAT")
    cols = [
        "PATTERN_ID", "ROUTE_PATH_GUID", "STUB_KIND", "ANCHOR_KIND", "ANCHOR_NAME",
        "MAIN_EQUIPMENT_NAME", "PROCESS_NAME", "UTILITY_GROUP", "UTILITY", "SIZE",
        "FACE", "DIR_SEQ", "N_BENDS", "RISE_MM", "OFFSET_MM", "DIAMETER_MM", "STUB_LENGTH_MM",
        "SOURCE_POS", "TARGET_POS", "ANCHOR_MIN", "ANCHOR_MAX", "STUB_POINTS",
        "FEAT_JSON", "DIR_UNIT_JSON",
    ]
    if has_vector:
        cols += ["FEAT", "DIR_UNIT"]
    placeholders = []
    for c in cols:
        if c in {"SOURCE_POS", "TARGET_POS", "ANCHOR_MIN", "ANCHOR_MAX", "STUB_POINTS", "FEAT_JSON", "DIR_UNIT_JSON"}:
            placeholders.append("%s::jsonb")
        elif c == "FEAT":
            placeholders.append("%s::vector")
        elif c == "DIR_UNIT":
            placeholders.append("%s::vector")
        else:
            placeholders.append("%s")
    sql = f'''
        INSERT INTO "TB_ROUTE_STUB_PATTERN" ({", ".join(f'"{c}"' for c in cols)})
        VALUES ({", ".join(placeholders)})
        ON CONFLICT ("PATTERN_ID") DO UPDATE SET
        {", ".join(f'"{c}" = EXCLUDED."{c}"' for c in cols if c != "PATTERN_ID")},
        "CREATED_AT" = now()
    '''
    rows = []
    for s in samples:
        row = [
            s.pattern_id, s.route_path_guid, s.stub_kind, s.anchor_kind, s.anchor_name,
            s.main_equipment_name, s.process_name, s.utility_group, s.utility, s.size,
            s.face, ",".join(s.dir_seq), s.n_bends, s.rise_mm, s.offset_mm, s.diameter_mm, s.stub_length_mm,
            json.dumps(s.source_pos), json.dumps(s.target_pos), json.dumps(s.anchor_min), json.dumps(s.anchor_max),
            json.dumps(s.stub_points), json.dumps(s.feat), json.dumps(s.dir_unit),
        ]
        if has_vector:
            row += [vec_literal(s.feat), vec_literal(s.dir_unit)]
        rows.append(row)
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=200)


def insert_templates(conn, templates: list[dict[str, Any]]) -> None:
    """집계된 template 목록을 TB_ROUTE_STUB_TEMPLATE에 upsert한다."""
    if not templates:
        return
    has_vector = pgvector_installed(conn) and has_column(conn, "TB_ROUTE_STUB_TEMPLATE", "AVG_FEAT")
    cols = [
        "TEMPLATE_ID", "STUB_KIND", "ANCHOR_KIND", "MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "SIZE",
        "FACE", "DIR_SEQ", "SAMPLE_COUNT", "AVG_RISE_MM", "AVG_OFFSET_MM", "AVG_DIAMETER_MM", "AVG_STUB_LENGTH_MM",
        "REPRESENTATIVE_PATTERN_ID", "REPRESENTATIVE_ROUTE_PATH_GUID", "REPRESENTATIVE_STUB_POINTS",
        "AVG_FEAT_JSON", "AVG_DIR_UNIT_JSON",
    ]
    if has_vector:
        cols += ["AVG_FEAT", "AVG_DIR_UNIT"]
    placeholders = []
    for c in cols:
        if c in {"REPRESENTATIVE_STUB_POINTS", "AVG_FEAT_JSON", "AVG_DIR_UNIT_JSON"}:
            placeholders.append("%s::jsonb")
        elif c in {"AVG_FEAT", "AVG_DIR_UNIT"}:
            placeholders.append("%s::vector")
        else:
            placeholders.append("%s")
    sql = f'''
        INSERT INTO "TB_ROUTE_STUB_TEMPLATE" ({", ".join(f'"{c}"' for c in cols)})
        VALUES ({", ".join(placeholders)})
        ON CONFLICT ("TEMPLATE_ID") DO UPDATE SET
        {", ".join(f'"{c}" = EXCLUDED."{c}"' for c in cols if c != "TEMPLATE_ID")},
        "CREATED_AT" = now()
    '''
    rows = []
    for t in templates:
        row = [t.get(c) for c in cols if c not in {"REPRESENTATIVE_STUB_POINTS", "AVG_FEAT_JSON", "AVG_DIR_UNIT_JSON", "AVG_FEAT", "AVG_DIR_UNIT"}]
        # Build row explicitly to avoid column-order surprises.
        row = [
            t["TEMPLATE_ID"], t["STUB_KIND"], t["ANCHOR_KIND"], t["MAIN_EQUIPMENT_NAME"], t["UTILITY_GROUP"], t["UTILITY"], t["SIZE"],
            t["FACE"], t["DIR_SEQ"], t["SAMPLE_COUNT"], t["AVG_RISE_MM"], t["AVG_OFFSET_MM"], t["AVG_DIAMETER_MM"], t["AVG_STUB_LENGTH_MM"],
            t["REPRESENTATIVE_PATTERN_ID"], t["REPRESENTATIVE_ROUTE_PATH_GUID"], json.dumps(t["REPRESENTATIVE_STUB_POINTS"]),
            json.dumps(t["AVG_FEAT_JSON"]), json.dumps(t["AVG_DIR_UNIT_JSON"]),
        ]
        if has_vector:
            row += [vec_literal(t["AVG_FEAT_JSON"]), vec_literal(t["AVG_DIR_UNIT_JSON"])]
        rows.append(row)
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=200)


def load_samples(conn, args) -> list[dict[str, Any]]:
    """템플릿 집계 대상 sample을 필터 조건에 맞게 로드한다."""
    where, params = sample_filters(args)
    sql = f'SELECT * FROM "TB_ROUTE_STUB_PATTERN" {"WHERE " + " AND ".join(where) if where else ""}'
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return normalize_json_rows([dict(r) for r in cur.fetchall()])


def delete_samples(conn, args) -> None:
    """--replace 실행 시 현재 필터에 해당하는 기존 sample을 삭제한다."""
    where, params = sample_filters(args)
    sql = f'DELETE FROM "TB_ROUTE_STUB_PATTERN" {"WHERE " + " AND ".join(where) if where else ""}'
    with conn.cursor() as cur:
        cur.execute(sql, params)


def delete_templates(conn, args) -> None:
    """--replace 실행 시 현재 필터에 해당하는 기존 template을 삭제한다."""
    where, params = sample_filters(args)
    sql = f'DELETE FROM "TB_ROUTE_STUB_TEMPLATE" {"WHERE " + " AND ".join(where) if where else ""}'
    with conn.cursor() as cur:
        cur.execute(sql, params)


def sample_filters(args) -> tuple[list[str], list[Any]]:
    """main_equipment/utility_group/utility/size 필터를 SQL WHERE 조각으로 변환한다."""
    pairs = [
        ("MAIN_EQUIPMENT_NAME", getattr(args, "main_equipment", None)),
        ("UTILITY_GROUP", getattr(args, "utility_group", None)),
        ("UTILITY", getattr(args, "utility", None)),
        ("SIZE", getattr(args, "size", None)),
    ]
    where = []
    params = []
    for col, value in pairs:
        if value:
            where.append(f'"{col}" = %s')
            params.append(value)
    return where, params


def log_application(conn, args, result: dict[str, Any]) -> None:
    """make-stub 결과 중 최상위 후보를 TB_ROUTE_STUB_APPLICATION_LOG에 저장한다.

    신규 자동배관설계에서 어떤 Stub template이 선택되었는지 추적하기 위한 감사/디버깅 로그다.
    """
    create_schema(conn)
    best = result.get("candidates", [{}])[0] if result.get("candidates") else {}
    start = best.get("start_stub") or {}
    end = best.get("end_stub") or {}
    app_id = str(uuid.uuid4())
    sql = '''
        INSERT INTO "TB_ROUTE_STUB_APPLICATION_LOG"
        ("APPLICATION_ID", "REQUEST_ID", "SOURCE_TEMPLATE_ID", "TARGET_TEMPLATE_ID",
         "MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "SIZE",
         "START_STUB_POINTS", "END_STUB_POINTS", "MIDDLE_ROUTE_POINTS", "FINAL_ROUTE_POINTS",
         "SCORE", "STATUS", "FAIL_REASON")
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s)
    '''
    final_points = []
    if best:
        final_points = (start.get("points") or []) + (best.get("middle_route") or []) + list(reversed(end.get("points") or []))
    with conn.cursor() as cur:
        cur.execute(sql, (
            app_id, args.request_id, start.get("template_id"), end.get("template_id"),
            args.main_equipment, args.utility_group, args.utility, args.size,
            json.dumps(start.get("points")), json.dumps(end.get("points")), json.dumps(best.get("middle_route")),
            json.dumps(final_points), best.get("score"), "OK" if best else "NO_CANDIDATE", None if best else "No stub template candidates",
        ))
    conn.commit()
    print(f"Application log inserted: {app_id}")


def print_summary(samples: list[StubSample], skipped: Counter) -> None:
    """extract 결과 요약과 스킵 사유를 콘솔에 출력한다."""
    by_kind = Counter(s.stub_kind for s in samples)
    by_group = Counter((s.main_equipment_name, s.utility_group, s.utility, s.stub_kind) for s in samples)
    print(f"Extracted samples: {len(samples)}")
    print(f"  START={by_kind.get('START', 0)}, END={by_kind.get('END', 0)}")
    if skipped:
        print("Skipped:")
        for k, v in skipped.items():
            print(f"  {k}: {v}")
    print("Top pattern groups:")
    for key, cnt in by_group.most_common(10):
        print(f"  {key}: {cnt}")


def sample_to_json(s: StubSample | None) -> dict[str, Any] | None:
    """StubSample dataclass를 JSON 직렬화 가능한 dict로 변환한다."""
    if s is None:
        return None
    return {
        "pattern_id": s.pattern_id,
        "route_path_guid": s.route_path_guid,
        "stub_kind": s.stub_kind,
        "anchor_kind": s.anchor_kind,
        "anchor_name": s.anchor_name,
        "main_equipment_name": s.main_equipment_name,
        "process_name": s.process_name,
        "utility_group": s.utility_group,
        "utility": s.utility,
        "size": s.size,
        "face": s.face,
        "dir_seq": s.dir_seq,
        "n_bends": s.n_bends,
        "rise_mm": s.rise_mm,
        "offset_mm": s.offset_mm,
        "diameter_mm": s.diameter_mm,
        "stub_length_mm": s.stub_length_mm,
        "source_pos": s.source_pos,
        "target_pos": s.target_pos,
        "anchor_min": s.anchor_min,
        "anchor_max": s.anchor_max,
        "stub_points": s.stub_points,
        "feat": s.feat,
        "dir_unit": s.dir_unit,
    }


def table_exists(conn, table: str) -> bool:
    """public schema에 table이 존재하는지 확인한다."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name=%s", (table,))
        return cur.fetchone()[0] > 0


def table_columns(conn, table: str) -> set[str]:
    """테이블의 컬럼명 집합을 반환한다. 프로젝트별 컬럼명 차이를 흡수하는 데 사용한다."""
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (table,))
        return {r[0] for r in cur.fetchall()}


def has_column(conn, table: str, col: str) -> bool:
    """특정 컬럼 존재 여부를 반환한다."""
    return col in table_columns(conn, table)


def pgvector_installed(conn) -> bool:
    """PostgreSQL pgvector 확장 설치 여부를 확인한다."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM pg_extension WHERE extname='vector'")
        return cur.fetchone()[0] > 0


def first_col(cols: set[str], *names: str) -> str | None:
    """후보 컬럼명 중 실제 테이블에 존재하는 첫 컬럼명을 반환한다."""
    for name in names:
        if name in cols:
            return name
    return None


def triple(x, y, z) -> tuple[float, float, float] | None:
    """x/y/z 값이 모두 있으면 float 3튜플로 변환하고, 하나라도 없으면 None을 반환한다."""
    if x is None or y is None or z is None:
        return None
    return (float(x), float(y), float(z))


def axis_snap(d: tuple[float, float, float]) -> int:
    """3D 방향 벡터를 6축 방향 인덱스로 스냅한다.

    절대값이 가장 큰 성분을 지배축으로 보고 해당 축의 부호를 사용한다.
    예: (10, 5, 100)은 +z, 즉 인덱스 4가 된다.
    """
    values = [abs(d[0]), abs(d[1]), abs(d[2])]
    ax = max(range(3), key=lambda i: values[i])
    return ax * 2 + (0 if d[ax] >= 0 else 1)


def opposite_axis(axis_id: int) -> int:
    """+x <-> -x처럼 6축 방향의 반대 방향 인덱스를 반환한다."""
    return axis_id + 1 if axis_id % 2 == 0 else axis_id - 1


def add_axis(p: tuple[float, float, float], axis_id: int, length: float) -> tuple[float, float, float]:
    """점 p에서 axis_id 방향으로 length(mm)만큼 이동한 새 점을 반환한다."""
    v = AXIS_VECTORS[axis_id]
    return (p[0] + v[0] * length, p[1] + v[1] * length, p[2] + v[2] * length)


def dist(a, b) -> float:
    """3D 유클리드 거리."""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def vec_sub(a, b) -> tuple[float, float, float]:
    """3D 벡터 a-b."""
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def unit(v) -> tuple[float, float, float]:
    """3D 벡터를 단위벡터로 정규화한다. 길이가 0이면 0벡터를 반환한다."""
    length = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if length < 1e-9:
        return (0.0, 0.0, 0.0)
    return (v[0] / length, v[1] / length, v[2] / length)


def polyline_length(points) -> float:
    """폴리라인 전체 길이를 계산한다."""
    return sum(dist(a, b) for a, b in zip(points, points[1:]))


def nearest_face(anchor: Anchor, p: tuple[float, float, float]) -> tuple[int, float]:
    """PoC p가 anchor AABB의 어느 면에 가장 가까운지 계산한다.

    반환값은 (face_id, offset_mm)이다. face_id는 AXIS_NAMES 인덱스와 동일하다.
    """
    mn, mx = anchor.min_pt, anchor.max_pt
    distances = [
        abs(mx[0] - p[0]), abs(p[0] - mn[0]),
        abs(mx[1] - p[1]), abs(p[1] - mn[1]),
        abs(mx[2] - p[2]), abs(p[2] - mn[2]),
    ]
    face = min(range(6), key=lambda i: distances[i])
    return face, distances[face]


def relative_pos(anchor: Anchor, p: tuple[float, float, float]) -> tuple[float, float, float]:
    """PoC의 anchor AABB 내부 상대좌표를 [0,1] 범위로 계산한다."""
    out = []
    for i in range(3):
        denom = anchor.max_pt[i] - anchor.min_pt[i]
        out.append(0.5 if abs(denom) < 1e-9 else clamp((p[i] - anchor.min_pt[i]) / denom, 0.0, 1.0))
    return tuple(out)


def compute_rise(points: list[tuple[float, float, float]], poc: tuple[float, float, float], face_id: int) -> float:
    """face 법선축 기준으로 Stub이 PoC에서 최대 얼마나 이동했는지 계산한다."""
    axis = face_id // 2
    return max(abs(p[axis] - poc[axis]) for p in points) if points else 0.0


def find_anchor(anchors: list[Anchor], p: tuple[float, float, float] | None, name_hint: str | None, utility_hint: str | None) -> Anchor | None:
    """PoC에 대응되는 anchor를 찾는다.

    우선 장비명/유틸리티 hint로 후보를 좁히고, PoC가 AABB 내부에 있으면 그 anchor를 우선한다.
    내부 anchor가 없으면 ANCHOR_MAX_MM 이내의 최근접 AABB를 fallback으로 사용한다.
    """
    if not p:
        return None
    candidates = anchors
    
    # [1] 장비명 힌트(name_hint)가 주어진 경우 해당 장비명을 포함하는 앵커만 필터링
    if name_hint:
        filtered = [a for a in candidates if a.name and name_hint.lower() in str(a.name).lower()]
        if filtered:
            candidates = filtered
            
    # [2] 유틸리티 힌트(utility_hint)가 주어진 경우 동일 유틸리티 앵커만 필터링 (Fuzzy 매칭 포함: ACID <-> ACID(HOOD) 등)
    if utility_hint:
        filtered = [
            a for a in candidates 
            if a.utility and (
                utility_hint.lower() in a.utility.lower() or 
                a.utility.lower() in utility_hint.lower()
            )
        ]
        if filtered:
            candidates = filtered
            
    # [3] PoC 좌표가 앵커 바운딩 박스(AABB) 내부에 완전히 들어와 있는지 판정 (우선순위 1)
    inside = [a for a in candidates if point_in_aabb(p, a.min_pt, a.max_pt, margin=1.0)]
    if inside:
        return min(inside, key=lambda a: aabb_distance(p, a.min_pt, a.max_pt))
        
    # [4] 내부에 없는 경우, 3미터(ANCHOR_MAX_MM) 이내에 가장 가깝게 붙어있는 최단거리 앵커 선택 (우선순위 2)
    nearest = min(candidates, key=lambda a: aabb_distance(p, a.min_pt, a.max_pt), default=None)
    if nearest and aabb_distance(p, nearest.min_pt, nearest.max_pt) <= ANCHOR_MAX_MM:
        return nearest
        
    return None


def explicit_anchor(min_text: str | None, max_text: str | None, kind: str, name: str) -> Anchor | None:
    """CLI에서 직접 입력한 anchor min/max 좌표를 Anchor 객체로 변환한다."""
    if not min_text or not max_text:
        return None
    return Anchor(kind=kind, name=name, utility=None, min_pt=parse_xyz(min_text), max_pt=parse_xyz(max_text))


def point_in_aabb(p, mn, mx, margin=0.0) -> bool:
    """점 p가 AABB 내부에 있는지 확인한다."""
    return all(mn[i] - margin <= p[i] <= mx[i] + margin for i in range(3))


def aabb_distance(p, mn, mx) -> float:
    """점 p와 AABB 사이의 최소 거리를 계산한다. 내부면 0이다."""
    sq = 0.0
    for i in range(3):
        if p[i] < mn[i]:
            sq += (mn[i] - p[i]) ** 2
        elif p[i] > mx[i]:
            sq += (p[i] - mx[i]) ** 2
    return math.sqrt(sq)


def parse_xyz(text: str) -> tuple[float, float, float]:
    """'x,y,z' 문자열을 3D 좌표 튜플로 변환한다."""
    parts = [x.strip() for x in text.split(",")]
    if len(parts) != 3:
        raise ValueError(f"Expected x,y,z: {text}")
    return (float(parts[0]), float(parts[1]), float(parts[2]))


def parse_size_to_diameter(size: str | None) -> float | None:
    """배관 사이즈 문자열을 mm 직경으로 변환한다.

    예: 40A -> 40, 1/2B -> 12.7, 2B -> 50.8.
    파싱할 수 없는 값은 None으로 둔다.
    """
    if not size:
        return None
    s = str(size).upper().replace("MM", "").replace("A", "").replace("B", "").strip()
    try:
        if " " in s:
            whole, frac = s.split(" ", 1)
            n, d = frac.split("/")
            return (float(whole) + float(n) / float(d)) * 25.4
        if "/" in s:
            n, d = s.split("/")
            return float(n) / float(d) * 25.4
        value = float("".join(ch for ch in s if ch.isdigit() or ch == "."))
        return value * 25.4 if value < 36 else value
    except Exception:
        return None


def clamp(v: float, lo: float, hi: float) -> float:
    """값 v를 [lo, hi] 범위로 제한한다."""
    return max(lo, min(hi, v))


def stable_id(*parts: str) -> str:
    """여러 문자열을 조합해 재현 가능한 짧은 SHA1 ID를 만든다."""
    h = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return h


def vec_literal(values: Iterable[float]) -> str:
    """pgvector가 받을 수 있는 '[1,2,3]' 형태의 vector 리터럴을 만든다."""
    return "[" + ",".join(f"{float(v):.9g}" for v in values) + "]"


def avg(rows: list[dict[str, Any]], key: str) -> float | None:
    """dict row 목록에서 특정 numeric key의 평균을 계산한다."""
    vals = [float(r[key]) for r in rows if r.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def float_or_zero(v) -> float:
    """None/빈값/파싱 실패를 0.0으로 처리하는 안전 float 변환."""
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0


def mean_vectors(vectors: list[list[float]]) -> list[float]:
    """동일 차원 vector 목록의 성분별 평균을 계산한다."""
    if not vectors:
        return []
    n = len(vectors[0])
    return [sum(v[i] for v in vectors if len(v) == n) / len(vectors) for i in range(n)]


def normalize_json_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """psycopg2가 문자열로 반환한 JSON 컬럼을 Python 값으로 변환한다."""
    for row in rows:
        for key in ["SOURCE_POS", "TARGET_POS", "ANCHOR_MIN", "ANCHOR_MAX", "STUB_POINTS", "FEAT_JSON", "DIR_UNIT_JSON",
                    "REPRESENTATIVE_STUB_POINTS", "AVG_FEAT_JSON", "AVG_DIR_UNIT_JSON"]:
            if key in row and isinstance(row[key], str):
                try:
                    row[key] = json.loads(row[key])
                except Exception:
                    pass
    return rows


def json_value(value: Any) -> Any:
    """문자열 JSON이면 파싱하고, 이미 Python 값이면 그대로 반환한다."""
    if isinstance(value, str):
        return json.loads(value)
    return value


def print_json_or_table(obj: Any, export_json: str | None = None) -> None:
    """결과를 콘솔 JSON으로 출력하고, 필요 시 파일에도 저장한다."""
    if export_json:
        write_json(export_json, obj)
        print(f"Exported JSON: {export_json}")
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def write_json(path: str, obj: Any) -> None:
    """UTF-8 JSON 파일로 저장한다. 상위 폴더가 없으면 생성한다."""
    path_obj = Path(os.path.expandvars(os.path.expanduser(path))).resolve()
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    path_obj.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
