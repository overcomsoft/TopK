# -*- coding: utf-8 -*-
"""
ViewObstacleGLB.py
------------------
설명: TB_BIM_OBSTACLE 테이블의 GLB 3D 형상 및 메타데이터 정보를 브라우저 상에서
      대화식으로 조회하고 시각화할 수 있는 프리미엄 3D 뷰어 도구입니다.
실행 명령어: python Tools/ViewObstacleGLB.py
"""

import os
import sys
import json
import socket
import webbrowser
import threading
import urllib.parse
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingTCPServer
import psycopg2
from psycopg2.extras import RealDictCursor

# 1. 데이터베이스 설정 로드 및 구성
SCRIPT_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = SCRIPT_DIR / "tools.settings.json"

db_config = {
    "host": "localhost",
    "port": 5432,
    "database": "DDW_AI_DB",
    "user": "postgres",
    "password": "dinno"
}

if SETTINGS_PATH.exists():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            settings = json.load(f)
            if "db" in settings:
                for k, v in settings["db"].items():
                    if k == "database" or k == "dbname":
                        db_config["database"] = v
                    else:
                        db_config[k] = v
        print(f"[+] 설정 파일에서 DB 정보를 로드했습니다: {db_config['host']}:{db_config['port']}/{db_config['database']}")
    except Exception as ex:
        print(f"[-] 설정 파일 로드 실패 (기본 설정을 사용합니다): {ex}")
else:
    print(f"[*] 설정 파일({SETTINGS_PATH})을 찾을 수 없어 기본 설정을 사용합니다.")

def get_db_connection():
    return psycopg2.connect(
        host=db_config["host"],
        port=db_config["port"],
        dbname=db_config["database"],
        user=db_config["user"],
        password=db_config["password"]
    )

# 2. 다단계 GLB 바이너리 조회 폴백 로직
def fetch_glb_binary(instance_id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # 1단계: TB_BIM_OBSTACLE에 GEOMETRY_DATA가 있는지 시도
            try:
                cur.execute('SELECT "GEOMETRY_DATA" FROM "TB_BIM_OBSTACLE" WHERE "INSTANCE_ID" = %s LIMIT 1;', (instance_id,))
                row = cur.fetchone()
                if row and row[0] is not None:
                    print(f"[+] 1단계 성공 (TB_BIM_OBSTACLE.GEOMETRY_DATA): {len(row[0])} bytes")
                    return bytes(row[0])
            except psycopg2.Error as e:
                conn.rollback()
                # print(f"[*] 1단계 건너뜀 (컬럼 없음 혹은 오류): {e}")

            # 2단계: TB_BIM_LATTICE에 GEOMETRY_DATA가 있는지 시도
            try:
                cur.execute('SELECT "GEOMETRY_DATA" FROM "TB_BIM_LATTICE" WHERE "INSTANCE_ID" = %s LIMIT 1;', (instance_id,))
                row = cur.fetchone()
                if row and row[0] is not None:
                    print(f"[+] 2단계 성공 (TB_BIM_LATTICE.GEOMETRY_DATA): {len(row[0])} bytes")
                    return bytes(row[0])
            except psycopg2.Error as e:
                conn.rollback()
                # print(f"[*] 2단계 건너뜀 (LATTICE 조회 실패): {e}")

            # 3단계: MODEL_TEMPLATE_ID 조회 후 TB_MODELTEMPLATES.BLOBs 조회
            model_template_id = None
            for table in ["TB_BIM_OBSTACLE", "TB_BIM_LATTICE"]:
                try:
                    cur.execute(f'SELECT "MODEL_TEMPLATE_ID" FROM "{table}" WHERE "INSTANCE_ID" = %s LIMIT 1;', (instance_id,))
                    row = cur.fetchone()
                    if row and row[0]:
                        model_template_id = row[0]
                        break
                except psycopg2.Error:
                    conn.rollback()

            if model_template_id:
                try:
                    cur.execute('SELECT "BLOBs", "RAWMODEL_ID" FROM "TB_MODELTEMPLATES" WHERE "MODEL_TEMPLATE_ID" = %s LIMIT 1;', (model_template_id,))
                    row = cur.fetchone()
                    if row:
                        blobs_data, rawmodel_id = row
                        if blobs_data is not None:
                            print(f"[+] 3단계 성공 (TB_MODELTEMPLATES.BLOBs): {len(blobs_data)} bytes")
                            return bytes(blobs_data)
                        
                        # 4단계: RAWMODEL_ID 기반 TB_RAWMODELS.RAWMODEL_DATA 조회
                        if rawmodel_id:
                            cur.execute('SELECT "RAWMODEL_DATA" FROM "TB_RAWMODELS" WHERE "RAWMODEL_ID" = %s LIMIT 1;', (rawmodel_id,))
                            row2 = cur.fetchone()
                            if row2 and row2[0] is not None:
                                print(f"[+] 4단계 성공 (TB_RAWMODELS.RAWMODEL_DATA): {len(row2[0])} bytes")
                                return bytes(row2[0])
                except psycopg2.Error as e:
                    conn.rollback()
                    # print(f"[*] 3~4단계 건너뜀 (TEMPLATES/RAWMODELS 조회 실패): {e}")
                    
    except Exception as ex:
        print(f"[-] GLB 바이너리 추출 에러: {ex}")
    finally:
        if conn:
            conn.close()
    return None

# 3. HTTP 웹 서버 핸들러 정의
class ThreadingHTTPServer(ThreadingTCPServer):
    allow_reuse_address = True

class ObstacleViewerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # 웹 로그 간소화
        pass

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        # 3.1. API - 장애물 리스트 조회
        if path == "/api/obstacles":
            self.handle_api_obstacles(query)
            
        # 3.2. API - GLB 파일 스트리밍
        elif path == "/api/glb":
            self.handle_api_glb(query)
            
        # 3.3. 루트 - 웹 페이지 서빙
        elif path == "/" or path == "/index.html":
            self.handle_serve_html()
            
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found")

    def handle_api_obstacles(self, query):
        search_query = query.get("search", [""])[0].strip()
        level_filter = query.get("level", [""])[0].strip()
        type_filter = query.get("type", [""])[0].strip()

        conn = None
        try:
            conn = get_db_connection()
            # JSON 딕셔너리로 받아오기 위해 RealDictCursor 사용
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. 검색/필터 필터링용 distinct values
                levels = []
                types = []
                
                try:
                    cur.execute('SELECT DISTINCT "LEVEL" FROM "TB_BIM_OBSTACLE" WHERE "LEVEL" IS NOT NULL AND "LEVEL" <> \'\' ORDER BY "LEVEL";')
                    levels = [r["LEVEL"] for r in cur.fetchall()]
                except psycopg2.Error:
                    conn.rollback()

                try:
                    cur.execute('SELECT DISTINCT "OBS_TYPE" FROM "TB_BIM_OBSTACLE" WHERE "OBS_TYPE" IS NOT NULL AND "OBS_TYPE" <> \'\' ORDER BY "OBS_TYPE";')
                    types = [r["OBS_TYPE"] for r in cur.fetchall()]
                except psycopg2.Error:
                    conn.rollback()
                
                # levels, types 가 비어 있으면 LATTICE에서 가져오도록 폴백
                if not levels or not types:
                    try:
                        cur.execute('SELECT DISTINCT "LEVEL" FROM "TB_BIM_LATTICE" WHERE "LEVEL" IS NOT NULL AND "LEVEL" <> \'\' ORDER BY "LEVEL";')
                        levels = [r["LEVEL"] for r in cur.fetchall()]
                    except psycopg2.Error:
                        conn.rollback()
                    try:
                        cur.execute('SELECT DISTINCT "TYPE" FROM "TB_BIM_LATTICE" WHERE "TYPE" IS NOT NULL AND "TYPE" <> \'\' ORDER BY "TYPE";')
                        types = [r["TYPE"] for r in cur.fetchall()]
                    except psycopg2.Error:
                        conn.rollback()

                # 2. 본 쿼리 빌딩 (기본적으로 TB_BIM_OBSTACLE을 쓰되 테이블이 없거나 데이터가 비어있으면 TB_BIM_LATTICE 사용)
                target_table = "TB_BIM_OBSTACLE"
                try:
                    cur.execute('SELECT COUNT(*) FROM "TB_BIM_OBSTACLE"')
                    cnt = cur.fetchone()
                    if not cnt or cnt["count"] == 0:
                        target_table = "TB_BIM_LATTICE"
                except psycopg2.Error:
                    conn.rollback()
                    target_table = "TB_BIM_LATTICE"

                where_clauses = []
                params = []

                if search_query:
                    where_clauses.append('("INSTANCE_NAME" ILIKE %s OR "DDWORKS_TYPE" ILIKE %s OR "INSTANCE_ID" ILIKE %s)')
                    pattern = f"%{search_query}%"
                    params.extend([pattern, pattern, pattern])

                if level_filter:
                    where_clauses.append('"LEVEL" = %s')
                    params.append(level_filter)

                if type_filter:
                    if target_table == "TB_BIM_OBSTACLE":
                        where_clauses.append('"OBS_TYPE" = %s')
                    else:
                        where_clauses.append('"TYPE" = %s')
                    params.append(type_filter)

                where_str = ""
                if where_clauses:
                    where_str = "WHERE " + " AND ".join(where_clauses)

                type_col = '"OBS_TYPE"' if target_table == "TB_BIM_OBSTACLE" else '"TYPE"'
                query_str = f"""
                    SELECT 
                        "INSTANCE_ID", "INSTANCE_NAME", "DDWORKS_TYPE", {type_col} AS "OBS_TYPE", 
                        "LEVEL", "BAY", "BOP", "POS_X", "POS_Y", "POS_Z", 
                        "AABB_MINX", "AABB_MINY", "AABB_MINZ", "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ"
                    FROM "{target_table}"
                    {where_str}
                    ORDER BY "INSTANCE_NAME" ASC, "INSTANCE_ID" ASC
                    LIMIT 300
                """
                
                cur.execute(query_str, params)
                rows = cur.fetchall()

                # JSON 반환
                response_data = {
                    "table_used": target_table,
                    "obstacles": rows,
                    "levels": levels,
                    "types": types
                }
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(response_data, ensure_ascii=False).encode('utf-8'))

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
        finally:
            if conn:
                conn.close()

    def handle_api_glb(self, query):
        instance_id = query.get("id", [""])[0].strip()
        if not instance_id:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing 'id' parameter.")
            return

        glb_data = fetch_glb_binary(instance_id)
        if glb_data:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(glb_data)))
            # CORS 헤더 추가
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(glb_data)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"GLB binary not found or failed to retrieve.")

    def handle_serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML_CONTENT.encode('utf-8'))


# 4. 내장 프리미엄 Web UI (HTML / CSS / Three.js)
HTML_CONTENT = """<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BIM Obstacle 3D Viewer</title>
    <!-- Google Fonts Inter & Outfit -->
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Outfit:wght@400;600;800&display=swap" rel="stylesheet">
    <!-- FontAwesome 아이콘 -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    
    <style>
        :root {
            --bg-color: #0f172a;
            --sidebar-bg: rgba(30, 41, 59, 0.7);
            --card-bg: rgba(15, 23, 42, 0.6);
            --accent-blue: #00f0ff;
            --accent-pink: #ff007f;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border-glow: rgba(0, 240, 255, 0.25);
            --border-color: rgba(255, 255, 255, 0.08);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Inter', 'Segoe UI', Roboto, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            overflow: hidden;
            height: 100vh;
        }

        #app {
            display: flex;
            width: 100vw;
            height: 100vh;
            position: relative;
        }

        /* 1. 사이드바 스타일 */
        #sidebar {
            width: 380px;
            background: var(--sidebar-bg);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border-right: 1px solid var(--border-color);
            display: flex;
            flex-direction: column;
            padding: 24px;
            z-index: 10;
            box-shadow: 10px 0 30px rgba(0, 0, 0, 0.5);
            transition: all 0.3s ease;
        }

        .logo-area {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 24px;
        }

        .logo-area i {
            font-size: 28px;
            color: var(--accent-blue);
            text-shadow: 0 0 15px var(--accent-blue);
        }

        .logo-area h1 {
            font-family: 'Outfit', sans-serif;
            font-size: 20px;
            font-weight: 800;
            background: linear-gradient(135deg, #00f0ff 0%, #ff007f 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: 0.5px;
        }

        /* 검색 및 필터 */
        .search-container {
            position: relative;
            margin-bottom: 16px;
        }

        .search-container input {
            width: 100%;
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid var(--border-color);
            padding: 12px 40px 12px 16px;
            border-radius: 8px;
            color: var(--text-main);
            font-size: 14px;
            outline: none;
            transition: all 0.25s ease;
        }

        .search-container input:focus {
            border-color: var(--accent-blue);
            box-shadow: 0 0 10px rgba(0, 240, 255, 0.2);
        }

        .search-container i {
            position: absolute;
            right: 14px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-muted);
            cursor: pointer;
            transition: color 0.2s;
        }

        .search-container i:hover {
            color: var(--accent-blue);
        }

        .filter-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin-bottom: 20px;
        }

        .filter-group {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .filter-group label {
            font-size: 11px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .filter-group select {
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid var(--border-color);
            padding: 8px 12px;
            border-radius: 6px;
            color: var(--text-main);
            font-size: 13px;
            outline: none;
            cursor: pointer;
            transition: all 0.2s;
        }

        .filter-group select:focus {
            border-color: var(--accent-blue);
        }

        .list-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 12px;
            color: var(--text-muted);
            margin-bottom: 12px;
            padding-bottom: 8px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }

        /* 장애물 리스트 */
        .obstacle-list {
            flex: 1;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 10px;
            padding-right: 4px;
        }

        /* 스크롤바 디자인 */
        .obstacle-list::-webkit-scrollbar {
            width: 5px;
        }
        .obstacle-list::-webkit-scrollbar-track {
            background: transparent;
        }
        .obstacle-list::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.1);
            border-radius: 10px;
        }
        .obstacle-list::-webkit-scrollbar-thumb:hover {
            background: rgba(0, 240, 255, 0.3);
        }

        .obstacle-item {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 14px;
            cursor: pointer;
            transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            overflow: hidden;
        }

        .obstacle-item::before {
            content: '';
            position: absolute;
            left: 0;
            top: 0;
            height: 100%;
            width: 3px;
            background: transparent;
            transition: background 0.2s;
        }

        .obstacle-item:hover {
            border-color: rgba(0, 240, 255, 0.4);
            transform: translateY(-2px);
            background: rgba(30, 41, 59, 0.4);
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
        }

        .obstacle-item.active {
            border-color: var(--accent-blue);
            background: rgba(0, 240, 255, 0.06);
            box-shadow: 0 0 15px rgba(0, 240, 255, 0.15);
        }

        .obstacle-item.active::before {
            background: var(--accent-blue);
        }

        .item-name {
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 6px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            color: var(--text-main);
        }

        .item-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            font-size: 11px;
        }

        .badge {
            padding: 3px 8px;
            border-radius: 4px;
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-muted);
            border: 1px solid rgba(255, 255, 255, 0.03);
            max-width: 140px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .badge.type-badge {
            background: rgba(0, 240, 255, 0.1);
            color: var(--accent-blue);
            border-color: rgba(0, 240, 255, 0.1);
        }

        .badge.level-badge {
            background: rgba(255, 0, 127, 0.1);
            color: var(--accent-pink);
            border-color: rgba(255, 0, 127, 0.1);
        }

        /* 2. 3D 뷰어 컨테이너 */
        #viewer-container {
            flex: 1;
            position: relative;
            background-color: #0b0f19;
        }

        #three-canvas {
            width: 100%;
            height: 100%;
            display: block;
        }

        /* 상단 플로팅 정보 카드 */
        .info-overlay {
            position: absolute;
            top: 24px;
            left: 24px;
            right: 24px;
            pointer-events: none;
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 20px;
            z-index: 5;
        }

        .detail-card {
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(0, 240, 255, 0.2);
            border-radius: 12px;
            padding: 20px;
            width: 380px;
            pointer-events: auto;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);
            display: none;
            animation: fadeIn 0.3s ease;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(-10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .detail-card h2 {
            font-family: 'Outfit', sans-serif;
            font-size: 16px;
            margin-bottom: 12px;
            color: var(--accent-blue);
            border-bottom: 1px solid rgba(0, 240, 255, 0.2);
            padding-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .detail-grid {
            display: grid;
            grid-template-columns: 100px 1fr;
            row-gap: 8px;
            font-size: 12px;
        }

        .detail-label {
            color: var(--text-muted);
            font-weight: 500;
        }

        .detail-value {
            color: var(--text-main);
            word-break: break-all;
        }

        .detail-value.coord {
            font-family: monospace;
            background: rgba(255, 255, 255, 0.03);
            padding: 2px 6px;
            border-radius: 4px;
        }

        /* 경고 배너 */
        #warning-banner {
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid rgba(239, 68, 68, 0.4);
            color: #ef4444;
            backdrop-filter: blur(12px);
            padding: 12px 20px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 10px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
            animation: fadeIn 0.3s ease;
            max-width: 450px;
            display: none;
            pointer-events: auto;
        }

        /* 하단 툴바 */
        .toolbar {
            position: absolute;
            bottom: 24px;
            right: 24px;
            display: flex;
            gap: 10px;
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(8px);
            padding: 8px;
            border-radius: 30px;
            border: 1px solid var(--border-color);
            box-shadow: 0 10px 25px rgba(0,0,0,0.3);
            z-index: 5;
        }

        .tool-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            width: 38px;
            height: 38px;
            border-radius: 50%;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            transition: all 0.2s;
        }

        .tool-btn:hover {
            color: var(--accent-blue);
            background: rgba(255, 255, 255, 0.05);
            transform: scale(1.05);
        }

        .tool-btn.active {
            color: var(--bg-color);
            background: var(--accent-blue);
            box-shadow: 0 0 10px rgba(0, 240, 255, 0.4);
        }

        /* 로딩 화면 */
        #loading-overlay {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(15, 23, 42, 0.8);
            backdrop-filter: blur(4px);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            z-index: 8;
            transition: opacity 0.3s;
            pointer-events: auto;
        }

        .spinner {
            width: 50px;
            height: 50px;
            border: 3px solid rgba(0, 240, 255, 0.1);
            border-top: 3px solid var(--accent-blue);
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-bottom: 16px;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        .loading-text {
            font-size: 14px;
            font-weight: 500;
            color: var(--text-main);
            letter-spacing: 0.5px;
        }

        /* 노 데이터 화면 */
        .no-data {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 200px;
            color: var(--text-muted);
            font-size: 14px;
            gap: 12px;
        }

        .no-data i {
            font-size: 24px;
        }
    </style>
</head>
<body>
    <div id="app">
        <!-- 1. 사이드바 영역 -->
        <div id="sidebar">
            <div class="logo-area">
                <i class="fa-solid fa-cube"></i>
                <h1>Obstacle 3D Visualizer</h1>
            </div>

            <!-- 검색바 -->
            <div class="search-container">
                <input type="text" id="search-input" placeholder="장애물명, 타입, ID 입력...">
                <i class="fa-solid fa-magnifying-glass" id="search-btn"></i>
            </div>

            <!-- 필터그룹 -->
            <div class="filter-row">
                <div class="filter-group">
                    <label for="level-select">Level</label>
                    <select id="level-select">
                        <option value="">전체</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label for="type-select">Type</label>
                    <select id="type-select">
                        <option value="">전체</option>
                    </select>
                </div>
            </div>

            <div class="list-header">
                <span>장애물 목록</span>
                <span id="list-count">조회 중...</span>
            </div>

            <!-- 리스트 스크롤 영역 -->
            <div class="obstacle-list" id="obstacle-list">
                <!-- 동적 렌더링 -->
            </div>
        </div>

        <!-- 2. 3D 뷰어 영역 -->
        <div id="viewer-container">
            <!-- 3D 캔버스 -->
            <canvas id="three-canvas"></canvas>

            <!-- 상태 오버레이 정보 -->
            <div class="info-overlay">
                <!-- 세부 정보 카드 -->
                <div class="detail-card" id="detail-card">
                    <h2 id="detail-title">장애물 정보 카드</h2>
                    <div class="detail-grid">
                        <div class="detail-label">INSTANCE ID</div>
                        <div class="detail-value coord" id="card-id">-</div>

                        <div class="detail-label">BIM 타입</div>
                        <div class="detail-value" id="card-type">-</div>

                        <div class="detail-label">레벨 / 구역</div>
                        <div class="detail-value" id="card-level-bay">-</div>

                        <div class="detail-label">BOP (높이)</div>
                        <div class="detail-value" id="card-bop">-</div>

                        <div class="detail-label">중심 위치</div>
                        <div class="detail-value coord" id="card-pos">-</div>

                        <div class="detail-label">AABB 영역</div>
                        <div class="detail-value coord" id="card-aabb">-</div>
                        
                        <div class="detail-label">박스 크기</div>
                        <div class="detail-value" id="card-size">-</div>
                    </div>
                </div>

                <!-- 경고 배너 (폴백 가시화 시 노출) -->
                <div id="warning-banner">
                    <i class="fa-solid fa-triangle-exclamation"></i>
                    <span id="warning-text">GLB 데이터 누락: AABB 바운딩 박스로 시각화되었습니다.</span>
                </div>
            </div>

            <!-- 툴바 조작버튼 -->
            <div class="toolbar">
                <button class="tool-btn active" id="btn-grid" title="그리드 토글">
                    <i class="fa-solid fa-border-all"></i>
                </button>
                <button class="tool-btn active" id="btn-axes" title="축선 토글">
                    <i class="fa-solid fa-arrows-up-down-left-right"></i>
                </button>
                <button class="tool-btn" id="btn-wireframe" title="와이어프레임 강제 토글">
                    <i class="fa-solid fa-square-minus"></i>
                </button>
                <button class="tool-btn" id="btn-reset" title="카메라 리셋">
                    <i class="fa-solid fa-arrows-rotate"></i>
                </button>
            </div>

            <!-- 로딩 마스크 -->
            <div id="loading-overlay" style="display: none;">
                <div class="spinner"></div>
                <div class="loading-text" id="loading-msg">데이터 베이스 연결 중...</div>
            </div>
        </div>
    </div>

    <!-- Three.js 관련 라이브러리 (CDN) -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/GLTFLoader.js"></script>

    <script>
        // 글로벌 상태 관리
        let scene, camera, renderer, controls;
        let gridHelper, axesHelper;
        let currentModel = null;
        let forceWireframe = false;
        let activeObstacle = null;

        // API 연동용 캐시
        let obstacleData = [];

        // DOM 요소 획득
        const elSearchInput = document.getElementById('search-input');
        const elSearchBtn = document.getElementById('search-btn');
        const elLevelSelect = document.getElementById('level-select');
        const elTypeSelect = document.getElementById('type-select');
        const elObstacleList = document.getElementById('obstacle-list');
        const elListCount = document.getElementById('list-count');
        const elDetailCard = document.getElementById('detail-card');
        const elWarningBanner = document.getElementById('warning-banner');
        const elLoadingOverlay = document.getElementById('loading-overlay');
        const elLoadingMsg = document.getElementById('loading-msg');

        // 3D 환경 셋업
        function init3D() {
            const container = document.getElementById('viewer-container');
            const width = container.clientWidth;
            const height = container.clientHeight;

            // 씬
            scene = new THREE.Scene();
            scene.background = new THREE.Color(0x0b0f19);

            // 카메라
            camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000000);
            camera.position.set(100, 100, 100);

            // 렌더러
            renderer = new THREE.WebGLRenderer({ canvas: document.getElementById('three-canvas'), antialias: true });
            renderer.setSize(width, height);
            renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
            renderer.shadowMap.enabled = true;

            // 컨트롤
            controls = new THREE.OrbitControls(camera, renderer.domElement);
            controls.enableDamping = true;
            controls.dampingFactor = 0.05;
            controls.maxPolarAngle = Math.PI / 2 + 0.1; // 바닥 뚫기 방지

            // 조명 설정
            const ambientLight = new THREE.AmbientLight(0xffffff, 0.4);
            scene.add(ambientLight);

            const dirLight1 = new THREE.DirectionalLight(0x00f0ff, 0.8);
            dirLight1.position.set(1, 1, 1).normalize();
            scene.add(dirLight1);

            const dirLight2 = new THREE.DirectionalLight(0xff007f, 0.6);
            dirLight2.position.set(-1, 1, -1).normalize();
            scene.add(dirLight2);

            const mainLight = new THREE.DirectionalLight(0xffffff, 0.5);
            mainLight.position.set(0, 10000, 0);
            scene.add(mainLight);

            // 보조 도구 (그리드, 축)
            gridHelper = new THREE.GridHelper(5000, 100, 0x00f0ff, 0x1e293b);
            gridHelper.position.y = -1; // 겹침 방지
            scene.add(gridHelper);

            axesHelper = new THREE.AxesHelper(1000);
            scene.add(axesHelper);

            // 리사이즈 이벤트
            window.addEventListener('resize', onWindowResize);

            animate();
        }

        function animate() {
            requestAnimationFrame(animate);
            controls.update();
            renderer.render(scene, camera);
        }

        function onWindowResize() {
            const container = document.getElementById('viewer-container');
            camera.aspect = container.clientWidth / container.clientHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(container.clientWidth, container.clientHeight);
        }

        // 로딩 UI 제어
        function showLoading(msg) {
            elLoadingMsg.textContent = msg;
            elLoadingOverlay.style.display = 'flex';
        }

        function hideLoading() {
            elLoadingOverlay.style.display = 'none';
        }

        // 장애물 목록 백엔드에서 fetch
        async function loadObstacles() {
            showLoading("데이터베이스에서 장애물 목록을 가져오는 중...");
            try {
                const search = encodeURIComponent(elSearchInput.value);
                const level = encodeURIComponent(elLevelSelect.value);
                const type = encodeURIComponent(elTypeSelect.value);
                
                const url = `/api/obstacles?search=${search}&level=${level}&type=${type}`;
                const res = await fetch(url);
                const data = await res.json();
                
                obstacleData = data.obstacles || [];
                
                // 레벨/타입 필터 채우기 (최초 1회만)
                if (elLevelSelect.options.length <= 1) {
                    data.levels.forEach(lvl => {
                        const opt = document.createElement('option');
                        opt.value = lvl;
                        opt.textContent = lvl;
                        elLevelSelect.appendChild(opt);
                    });
                }
                if (elTypeSelect.options.length <= 1) {
                    data.types.forEach(typ => {
                        const opt = document.createElement('option');
                        opt.value = typ;
                        opt.textContent = typ;
                        elTypeSelect.appendChild(opt);
                    });
                }

                renderList();
            } catch (err) {
                console.error("Failed to load obstacles", err);
                elObstacleList.innerHTML = `<div class="no-data"><i class="fa-solid fa-triangle-exclamation"></i><span>데이터를 불러오지 못했습니다. DB 연결을 확인하세요.</span></div>`;
                elListCount.textContent = "Error";
            } finally {
                hideLoading();
            }
        }

        // 리스트 동적 렌더링
        function renderList() {
            elObstacleList.innerHTML = "";
            elListCount.textContent = `${obstacleData.length}개 표시`;

            if (obstacleData.length === 0) {
                elObstacleList.innerHTML = `<div class="no-data"><i class="fa-solid fa-folder-open"></i><span>검색 결과가 없습니다.</span></div>`;
                return;
            }

            obstacleData.forEach(obs => {
                const item = document.createElement('div');
                item.className = 'obstacle-item';
                if (activeObstacle && activeObstacle.INSTANCE_ID === obs.INSTANCE_ID) {
                    item.className += ' active';
                }

                const name = obs.INSTANCE_NAME || obs.DDWORKS_TYPE || `Unnamed Obstacle (${obs.INSTANCE_ID.substring(0,8)})`;
                const type = obs.OBS_TYPE || obs.DDWORKS_TYPE || "Obstacle";
                const lvl = obs.LEVEL || "No Level";

                item.innerHTML = `
                    <div class="item-name" title="${name}">${name}</div>
                    <div class="item-meta">
                        <span class="badge type-badge" title="${type}">${type}</span>
                        <span class="badge level-badge">${lvl}</span>
                    </div>
                `;

                item.addEventListener('click', () => {
                    // 이전 active 제거
                    document.querySelectorAll('.obstacle-item').forEach(el => el.classList.remove('active'));
                    item.classList.add('active');
                    selectObstacle(obs);
                });

                elObstacleList.appendChild(item);
            });
        }

        // 장애물 선택 처리
        function selectObstacle(obs) {
            activeObstacle = obs;
            
            // 상세 정보 카드 노출
            elDetailCard.style.display = 'block';
            document.getElementById('card-id').textContent = obs.INSTANCE_ID;
            document.getElementById('card-type').textContent = `${obs.OBS_TYPE || "N/A"} (${obs.DDWORKS_TYPE || "N/A"})`;
            document.getElementById('card-level-bay').textContent = `${obs.LEVEL || "N/A"} / ${obs.BAY || "N/A"}`;
            document.getElementById('card-bop').textContent = obs.BOP !== null ? `${obs.BOP.toFixed(1)} mm` : "N/A";
            document.getElementById('card-pos').textContent = `X: ${obs.POS_X.toFixed(1)}, Y: ${obs.POS_Y.toFixed(1)}, Z: ${obs.POS_Z.toFixed(1)}`;
            
            const minStr = `Min(${obs.AABB_MINX.toFixed(1)}, ${obs.AABB_MINY.toFixed(1)}, ${obs.AABB_MINZ.toFixed(1)})`;
            const maxStr = `Max(${obs.AABB_MAXX.toFixed(1)}, ${obs.AABB_MAXY.toFixed(1)}, ${obs.AABB_MAXZ.toFixed(1)})`;
            document.getElementById('card-aabb').innerHTML = `${minStr}<br>${maxStr}`;
            
            const dx = Math.abs(obs.AABB_MAXX - obs.AABB_MINX);
            const dy = Math.abs(obs.AABB_MAXY - obs.AABB_MINY);
            const dz = Math.abs(obs.AABB_MAXZ - obs.AABB_MINZ);
            document.getElementById('card-size').textContent = `${dx.toFixed(0)} x ${dy.toFixed(0)} x ${dz.toFixed(0)} mm`;

            // 경고 배너 및 이전 3D 모델 클리어
            elWarningBanner.style.display = 'none';
            if (currentModel) {
                scene.remove(currentModel);
                currentModel = null;
            }

            // 3D 모델 불러오기
            load3DModel(obs);
        }

        // 3D 모델 로딩 (GLB fetch -> 실패 시 AABB 폴백)
        function load3DModel(obs) {
            showLoading("GLB 3D 형상 데이터를 가져오는 중...");
            
            const loader = new THREE.GLTFLoader();
            const glbUrl = `/api/glb?id=${obs.INSTANCE_ID}`;
            
            loader.load(
                glbUrl,
                // 1) 로드 성공
                function (gltf) {
                    currentModel = gltf.scene;
                    
                    // 강제 와이어프레임 모드 처리
                    applyWireframe(currentModel, forceWireframe);
                    
                    scene.add(currentModel);
                    adjustCamera(currentModel);
                    hideLoading();
                },
                // 2) 프로그레스
                undefined,
                // 3) 에러 발생 (GLB 없음 또는 비표준 파일 헤더)
                function (error) {
                    console.warn("GLTFLoader failed to parse model binary. Falling back to wireframe box.", error);
                    drawAABBFallback(obs);
                    elWarningBanner.style.display = 'flex';
                    hideLoading();
                }
            );
        }

        // 와이어프레임 모드 재귀 적용
        function applyWireframe(object, enabled) {
            object.traverse((child) => {
                if (child.isMesh) {
                    child.material.wireframe = enabled;
                    if (enabled) {
                        child.material.transparent = true;
                        child.material.opacity = 0.5;
                    } else {
                        child.material.transparent = false;
                        child.material.opacity = 1.0;
                    }
                }
            });
        }

        // AABB를 사용한 반투명 박스 가시화 (바이너리 부재/비표준 시 폴백)
        function drawAABBFallback(obs) {
            const dx = Math.abs(obs.AABB_MAXX - obs.AABB_MINX);
            const dy = Math.abs(obs.AABB_MAXY - obs.AABB_MINY);
            const dz = Math.abs(obs.AABB_MAXZ - obs.AABB_MINZ);

            // 만약 크기가 0이하인 경우 기본값 100mm
            const sx = dx > 0 ? dx : 100;
            const sy = dy > 0 ? dy : 100;
            const sz = dz > 0 ? dz : 100;

            const geometry = new THREE.BoxGeometry(sx, sy, sz);
            
            // 아름다운 반투명 네온 오렌지 재질
            const material = new THREE.MeshPhongMaterial({
                color: 0xff4400,
                transparent: true,
                opacity: 0.35,
                shininess: 120,
                side: THREE.DoubleSide
            });

            const mesh = new THREE.Mesh(geometry, material);
            mesh.castShadow = true;
            mesh.receiveShadow = true;

            // 중심 좌표 계산
            const cx = (obs.AABB_MINX + obs.AABB_MAXX) / 2;
            const cy = (obs.AABB_MINY + obs.AABB_MAXY) / 2;
            const cz = (obs.AABB_MINZ + obs.AABB_MAXZ) / 2;
            mesh.position.set(cx, cy, cz);

            // 외곽선 추가
            const edges = new THREE.EdgesGeometry(geometry);
            const line = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ 
                color: 0xffaa00, 
                linewidth: 2 
            }));
            mesh.add(line);

            currentModel = mesh;
            scene.add(currentModel);
            adjustCamera(currentModel);
        }

        // 카메라 오토 포커스 맞춤
        function adjustCamera(object) {
            const box = new THREE.Box3().setFromObject(object);
            const center = new THREE.Vector3();
            box.getCenter(center);
            const size = new THREE.Vector3();
            box.getSize(size);

            const maxDim = Math.max(size.x, size.y, size.z);
            const fov = camera.fov * (Math.PI / 180);
            let cameraZ = Math.abs(maxDim / 2 / Math.tan(fov / 2));
            cameraZ *= 1.8; // 약간의 줌 여백

            // 만약 차원이 너무 작거나 없는 경우 (기본 줌)
            if (maxDim < 1.0) {
                cameraZ = 200;
            }

            camera.position.set(center.x + cameraZ * 0.4, center.y + cameraZ * 0.4, center.z + cameraZ * 0.8);
            camera.lookAt(center);

            controls.target.copy(center);
            controls.update();
        }

        // 컨트롤 툴바 버튼 이벤트
        document.getElementById('btn-grid').addEventListener('click', function() {
            this.classList.toggle('active');
            gridHelper.visible = this.classList.contains('active');
        });

        document.getElementById('btn-axes').addEventListener('click', function() {
            this.classList.toggle('active');
            axesHelper.visible = this.classList.contains('active');
        });

        document.getElementById('btn-wireframe').addEventListener('click', function() {
            this.classList.toggle('active');
            forceWireframe = this.classList.contains('active');
            if (currentModel) {
                applyWireframe(currentModel, forceWireframe);
            }
        });

        document.getElementById('btn-reset').addEventListener('click', function() {
            if (currentModel) {
                adjustCamera(currentModel);
            } else {
                camera.position.set(100, 100, 100);
                controls.target.set(0, 0, 0);
                controls.update();
            }
        });

        // 검색 및 필터 이벤트 바인딩
        elSearchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') loadObstacles();
        });
        elSearchBtn.addEventListener('click', loadObstacles);
        elLevelSelect.addEventListener('change', loadObstacles);
        elTypeSelect.addEventListener('change', loadObstacles);

        // 최초 로드
        window.addEventListener('DOMContentLoaded', () => {
            init3D();
            loadObstacles();
        });
    </script>
</body>
</html>
"""


# 5. 로컬 포트 스캔 및 실행
def find_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port

def run_server(port):
    server_address = ('', port)
    httpd = ThreadingHTTPServer(server_address, ObstacleViewerHandler)
    print(f"\n[+] 3D Obstacle Viewer 서버가 실행되었습니다! (포트: {port})")
    print(f"    브라우저 접속 주소: http://localhost:{port}")
    print("[*] 종료하려면 터미널에서 Ctrl+C를 입력하십시오.")
    
    # 백그라운드에서 브라우저 열기
    threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] 서버를 종료하는 중입니다...")
        httpd.shutdown()
        print("[+] 서버가 정상 종료되었습니다.")

def main():
    print("="*60)
    print("           BIM 장애물 3D 가시화 뷰어 (Python/Three.js)")
    print("="*60)
    port = find_free_port()
    # 8000번 포트가 비어있으면 8000번 우선 사용
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('localhost', 8000))
        s.close()
        port = 8000
    except socket.error:
        pass
        
    run_server(port)

if __name__ == '__main__':
    main()
