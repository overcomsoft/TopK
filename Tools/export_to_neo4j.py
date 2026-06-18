#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
export_to_neo4j.py

PostgreSQL에 적재된 다발배관(수평/수직), 공간(Space), 연결 패턴(Group Pattern), 
장비 접속점(Anchor) 등의 설계 특징점 데이터를 Neo4j 지식그래프로 이관하는 자동화 파이프라인 스크립트입니다.
"""

import sys
import os
import json
import argparse
from pathlib import Path
import psycopg2
from neo4j import GraphDatabase

def load_db_settings():
    settings_path = Path(__file__).parent / "tools.settings.json"
    host, port, db, user, password = "localhost", "5432", "DDW_AI_DB", "postgres", "dinno"
    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
                if "db" in settings:
                    db_conf = settings["db"]
                    host = db_conf.get("host", host)
                    port = str(db_conf.get("port", port))
                    db = db_conf.get("database", db)
                    user = db_conf.get("user", user)
                    password = db_conf.get("password", password)
        except Exception as ex:
            print(f"[Warning] Failed to load settings from tools.settings.json: {ex}")
    return f"host={host} port={port} dbname={db} user={user} password={password}"

def main():
    parser = argparse.ArgumentParser(description="PostgreSQL 설계 특징점 데이터를 Neo4j 지식그래프로 이관")
    parser.add_argument("--project", type=str, default="CHILLER 002", help="이관 대상 프로젝트 ID")
    parser.add_argument("--uri", type=str, default="bolt://localhost:7687", help="Neo4j Connection URI")
    parser.add_argument("--user", type=str, default="neo4j", help="Neo4j Username")
    parser.add_argument("--password", type=str, default="dinno", help="Neo4j Password")
    args = parser.parse_args()

    # 1. PostgreSQL 연결
    conn_str = load_db_settings()
    print(f"Connecting to PostgreSQL: {conn_str.split(' password=')[0]}")
    try:
        pg_conn = psycopg2.connect(conn_str)
    except Exception as e:
        print(f"[Error] Failed to connect to PostgreSQL: {e}")
        sys.exit(1)

    # 2. Neo4j 연결 테스트 및 핸들러 준비
    print(f"Connecting to Neo4j: {args.uri} (User: {args.user})")
    
    # 비밀번호 후보군 시도 (기본 입력값 -> neo4j -> password)
    passwords_to_try = [args.password, "neo4j", "password", "admin"]
    driver = None
    connected_password = None
    
    for pwd in passwords_to_try:
        try:
            temp_driver = GraphDatabase.driver(args.uri, auth=(args.user, pwd))
            with temp_driver.session() as session:
                session.run("RETURN 1").single()
            driver = temp_driver
            connected_password = pwd
            break
        except Exception as e:
            if "refused" in str(e).lower() or "active" in str(e).lower():
                print(f"[Error] Neo4j Connection Refused! DBMS가 실행 중인지, 포트 7687이 열려 있는지 확인하세요.")
                print(f"상세 에러: {e}")
                pg_conn.close()
                sys.exit(1)
            print(f"  * Password '{pwd}' failed.")

    if not driver:
        print(f"[Error] Neo4j 인증에 실패했습니다. 비밀번호를 다시 확인하세요.")
        pg_conn.close()
        sys.exit(1)

    print(f"Neo4j Connected successfully using password: '{connected_password}'")

    try:
        # 3. PostgreSQL 데이터 로드
        print("\n[1] PostgreSQL에서 원본 설계 데이터 로드 중...")
        with pg_conn.cursor() as cur:
            # (A) 공간 정보 로드
            cur.execute("""
                SELECT "SPACE_NAME", "AABB_MINX", "AABB_MINY", "AABB_MINZ", "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"
                FROM "TB_SPACE_INFO"
                WHERE "AABB_MINZ" IS NOT NULL;
            """)
            spaces = []
            for r in cur.fetchall():
                spaces.append({
                    "name": r[0].strip(),
                    "min_x": float(r[1] or 0), "min_y": float(r[2] or 0), "min_z": float(r[3] or 0),
                    "max_x": float(r[4] or 0), "max_y": float(r[5] or 0), "max_z": float(r[6] or 0)
                })
            print(f"  * 공간 데이터 로드 완료: {len(spaces)}개 구역")

            # (B) 다발배관 정보 로드 (수평/수직)
            cur.execute("""
                SELECT "VERTICAL_GROUP_ID", "EQUIPMENT_NAME", "UTILITY", "SPACE_NAME", 
                       "DIRECTION", "BUNDLE_LENGTH", "AVG_PITCH_MM", "ROUTE_COUNT", 
                       "MEMBER_ROUTE_GUIDS_JSON", "AABB_MINX", "AABB_MINY", "AABB_MINZ", 
                       "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"
                FROM "TB_ROUTE_VERTICAL_GROUP_FEATURE"
                WHERE "PROJECT_ID" = %s;
            """, (args.project,))
            bundles = []
            for r in cur.fetchall():
                guids = r[8] if isinstance(r[8], list) else json.loads(r[8] or "[]")
                bundles.append({
                    "id": r[0], "eq_name": r[1], "utility": r[2], "space_name": r[3],
                    "direction": r[4], "length": float(r[5] or 0), "pitch": float(r[6] or 0),
                    "route_count": int(r[7] or 0), "guids": guids,
                    "min_x": float(r[9] or 0), "min_y": float(r[10] or 0), "min_z": float(r[11] or 0),
                    "max_x": float(r[12] or 0), "max_y": float(r[13] or 0), "max_z": float(r[14] or 0)
                })
            print(f"  * 다발배관 데이터 로드 완료: {len(bundles)}개")

            # (C) 장비 접속 앵커 정보 로드
            cur.execute("""
                SELECT "ROUTE_PATH_GUID", "EQUIPMENT_NAME", "ANCHOR_PORT_NAME", "ANCHOR_PORT_DIRECTION"
                FROM "TB_ROUTE_FEATURE_ANCHOR"
                WHERE "PROJECT_ID" = %s AND "EQUIPMENT_NAME" IS NOT NULL;
            """, (args.project,))
            anchors = []
            for r in cur.fetchall():
                anchors.append({
                    "route_guid": r[0], "eq_name": r[1], "port_name": r[2], "direction": r[3]
                })
            print(f"  * 장비 접속 앵커 데이터 로드 완료: {len(anchors)}개")

            # (D) 그룹 주행 패턴 (Corridor Pattern) 정보 로드
            cur.execute("""
                SELECT "GROUP_ID", "EQUIPMENT_TAG", "UTILITY_GROUP", "MEMBER_COUNT", "MEMBER_GUIDS_JSON", "TRUNK_AXIS"
                FROM "TB_ROUTE_GROUP_PATTERN"
                WHERE "PROJECT_ID" = %s;
            """, (args.project,))
            group_patterns = []
            for r in cur.fetchall():
                guids = r[4] if isinstance(r[4], list) else json.loads(r[4] or "[]")
                group_patterns.append({
                    "group_id": str(r[0]), "eq_tag": r[1], "utility": r[2], "count": int(r[3] or 0),
                    "guids": guids, "axis": r[5]
                })
            print(f"  * 그룹 주행 패턴 데이터 로드 완료: {len(group_patterns)}개")

        # 4. Neo4j 지식그래프 적재
        print("\n[2] Neo4j 지식그래프 적재 시작...")
        with driver.session() as session:
            # 기존 노드/관계 초기화
            print("  * 기존 지식그래프 초기화 중...")
            session.run("MATCH (n) DETACH DELETE n;")

            # 프로젝트 노드 생성
            print("  * Project 노드 생성 중...")
            session.run("CREATE (p:Project {id: $proj});", proj=args.project)

            # 공간 정보 적재
            print("  * Space 노드 적재 중...")
            session.run("""
                UNWIND $spaces AS sp
                CREATE (s:Space {
                    name: sp.name,
                    min_x: sp.min_x, min_y: sp.min_y, min_z: sp.min_z,
                    max_x: sp.max_x, max_y: sp.max_y, max_z: sp.max_z
                });
            """, spaces=spaces)

            # 장비 노드 1차 생성 (다발 및 앵커에 언급된 장비)
            eq_names = set([b["eq_name"] for b in bundles] + [a["eq_name"] for a in anchors] + [gp["eq_tag"] for gp in group_patterns])
            print(f"  * Equipment 노드 적재 중 ({len(eq_names)}개)...")
            session.run("""
                UNWIND $eqs AS eq_name
                CREATE (e:Equipment {name: eq_name});
            """, eqs=list(eq_names))

            # 다발배관(BundleGroup) 및 배관 경로(Route) 적재
            print("  * BundleGroup 및 Route 노드와 관계망 생성 중...")
            for b in bundles:
                session.run("""
                    MATCH (p:Project {id: $project_id})
                    CREATE (bg:BundleGroup {
                        id: $id,
                        direction: $direction,
                        length: $length,
                        avg_pitch: $pitch,
                        route_count: $route_count,
                        min_x: $min_x, min_y: $min_y, min_z: $min_z,
                        max_x: $max_x, max_y: $max_y, max_z: $max_z
                    })
                    CREATE (bg)-[:BELONGS_TO]->(p)
                    
                    WITH bg
                    MATCH (e:Equipment {name: $eq_name})
                    CREATE (bg)-[:CONNECTED_TO]->(e)
                    
                    WITH bg
                    MATCH (s:Space {name: $space_name})
                    CREATE (bg)-[:PASSES_THROUGH]->(s)
                    
                    WITH bg
                    UNWIND $guids AS r_guid
                    MERGE (r:Route {guid: r_guid})
                    ON CREATE SET r.utility = $utility
                    MERGE (r)-[:MEMBER_OF]->(bg)
                """, **b)

            # 장비 접속 앵커 관계망 생성
            print("  * Route - Equipment 간 앵커 포트 연결 관계(CONNECTED_TO) 설정 중...")
            for a in anchors:
                session.run("""
                    MATCH (r:Route {guid: $route_guid})
                    MATCH (e:Equipment {name: $eq_name})
                    MERGE (r)-[c:CONNECTED_TO]->(e)
                    SET c.port = $port_name, c.direction = $direction
                """, **a)

            # 그룹 주행 패턴 (Corridor Pattern) 적재
            print("  * Corridor Pattern(주행 복도) 그룹 노드 및 멤버 관계 생성 중...")
            for gp in group_patterns:
                session.run("""
                    MATCH (p:Project {id: $project_id})
                    CREATE (gp:CorridorPattern {
                        id: $group_id,
                        utility: $utility,
                        axis: $axis,
                        route_count: $count
                    })
                    CREATE (gp)-[:BELONGS_TO]->(p)
                    
                    WITH gp
                    MATCH (e:Equipment {name: $eq_tag})
                    CREATE (gp)-[:CONNECTED_TO]->(e)
                    
                    WITH gp
                    UNWIND $guids AS r_guid
                    MERGE (r:Route {guid: r_guid})
                    MERGE (r)-[:MEMBER_OF]->(gp)
                """, project_id=args.project, **gp)

        print("\n=======================================================")
        print("🎉 Neo4j 지식그래프 이관 및 적재가 성공적으로 완료되었습니다!")
        print("=======================================================")

    finally:
        pg_conn.close()
        if driver:
            driver.close()

if __name__ == "__main__":
    main()
