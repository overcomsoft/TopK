#!/usr/bin/env python
# -*- coding: utf-8 -*-

# ==============================================================================
# [실행 명령어 및 도구 안내]
# 본 스크립트는 TB_ROUTE_GROUP_PATTERN 테이블 및 TB_EQUIPMENTS 테이블을 조회하여,
# 장비 호기별/유틸리티별 그룹배관 추출 결과를 수려한 3D 인터랙티브 웹 대시보드(HTML)로
# 추출하고 브라우저에 즉시 연동하는 도구입니다.
#
# 실행 방법:
#    > python Tools/ViewRouteGroup3D.py --password dinno
#
# 옵션 인자:
#    --password    : DB 비밀번호 (기본값: dinno)
#    --html-out    : 생성할 HTML 웹페이지의 출력 경로 (기본값: data/output/route_group_viewer.html)
# ==============================================================================

import sys
import os
import json
import re
import argparse
import webbrowser
import psycopg2

# 공통 설정 로드
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from tool_config import ToolRuntime
import tool_config


def parse_multilinestring_z(wkt: str) -> list[list[list[float]]]:
    """
    PostGIS의 MULTILINESTRING Z WKT 문자열을 파싱하여 배관별 3D 좌표 목록으로 복원합니다.
    형식: MULTILINESTRING Z ((x1 y1 z1, x2 y2 z2, ...), (x1' y1' z1', ...))
    """
    if not wkt:
        return []
        
    wkt = wkt.strip().upper()
    if "MULTILINESTRING" not in wkt:
        return []
        
    # 외부 괄호 안의 내부 궤적 추출
    start_idx = wkt.find("(")
    end_idx = wkt.rfind(")")
    if start_idx == -1 or end_idx == -1:
        return []
        
    content = wkt[start_idx + 1:end_idx].strip()
    if content.startswith("("):
        content = content[1:]
    if content.endswith(")"):
        content = content[:-1]
        
    lines = []
    # 개별 라인 구분 기호: ),( 또는 ), (
    parts = re.split(r'\)\s*,\s*\(', content)
    for p in parts:
        coords = []
        points_str = p.split(",")
        for pt_str in points_str:
            tokens = pt_str.strip().split()
            if len(tokens) >= 3:
                try:
                    coords.append([float(tokens[0]), float(tokens[1]), float(tokens[2])])
                except ValueError:
                    continue
        if coords:
            lines.append(coords)
            
    return lines


def open_connection(conninfo: str):
    """데이터베이스 커넥션을 엽니다."""
    try:
        return psycopg2.connect(conninfo)
    except Exception as e:
        print(f"[error] Failed to connect to PostgreSQL: {e}")
        sys.exit(1)


def load_route_groups(conn) -> list[dict]:
    """TB_ROUTE_GROUP_PATTERN 테이블로부터 그룹배관 데이터 및 3D 기하 정보를 추출합니다."""
    print("Loading route group patterns from DB...")
    sql = """
    SELECT "GROUP_ID", "EQUIPMENT_TAG", "UTILITY_GROUP", "UTILITY", "N_MEMBERS", 
           "AVG_SIMILARITY", "TRUNK_Z", "TRUNK_XY_SPREAD", "PITCH_MM", "N_ORTHO_BENDS", 
           "MEMBER_GUIDS", "PATTERN_SEQ", "SECTION_BOUNDS", "TRUNK_LEN",
           ST_AsText("GEOM_3D") AS "GEOM_3D_WKT",
           ST_AsText("TRUNK_GEOM_3D") AS "TRUNK_GEOM_3D_WKT"
    FROM "TB_ROUTE_GROUP_PATTERN"
    ORDER BY "EQUIPMENT_TAG", "UTILITY_GROUP", "UTILITY"
    """
    
    with conn.cursor() as cur:
        cur.execute(sql)
        colnames = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        
    results = []
    for r in rows:
        item = dict(zip(colnames, r))
        # WKT 문자열을 파이썬 3차원 배열 리스트로 변환
        wkt = item.get("GEOM_3D_WKT")
        item["lines"] = parse_multilinestring_z(wkt)
        
        trunk_wkt = item.get("TRUNK_GEOM_3D_WKT")
        item["trunk_lines"] = parse_multilinestring_z(trunk_wkt) if trunk_wkt else []
        
        if "GEOM_3D_WKT" in item:
            del item["GEOM_3D_WKT"]
        if "TRUNK_GEOM_3D_WKT" in item:
            del item["TRUNK_GEOM_3D_WKT"]
        results.append(item)
        
    print(f"Loaded {len(results)} route group patterns.")
    return results


def load_equipments(conn) -> list[dict]:
    """TB_EQUIPMENTS 테이블로부터 장비 AABB 정보 및 PoC 레이아웃을 로드합니다."""
    print("Loading equipment layouts from DB...")
    sql = """
    SELECT "INSTANCE_NAME" AS "EQUIPMENT_TAG", 
           "AABB_MINX", "AABB_MINY", "AABB_MINZ",
           "AABB_MAXX", "AABB_MAXY", "AABB_MAXZ",
           "POC_POSITIONS_LIST", "POC_ID_LIST"
    FROM "TB_EQUIPMENTS"
    WHERE "AABB_MINX" IS NOT NULL
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            colnames = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
        results = []
        for r in rows:
            item = dict(zip(colnames, r))
            results.append(item)
        print(f"Loaded {len(results)} equipment layouts.")
        return results
    except Exception as e:
        print(f"[warn] Failed to load TB_EQUIPMENTS: {e}. Skipping equipment background layout overlay.")
        return []


def generate_viewer_html(groups: list[dict], equipments: list[dict], output_path: str) -> None:
    """수려한 다크 테마 대화형 웹 뷰어 HTML 파일을 생성합니다."""
    print(f"Building interactive dashboard HTML...")
    
    # JSON 문자열로 변환하여 JS 영역에 안전하게 삽입
    groups_json = json.dumps(groups, ensure_ascii=False)
    equip_json = json.dumps(equipments, ensure_ascii=False)
    
    html_template = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>TopKGen Group Piping 3D Web Dashboard</title>
    <!-- Tailwind CSS CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- Plotly.js CDN -->
    <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
    <style>
        body {{
            background-color: #0f172a;
            color: #e2e8f0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        }}
        /* 스크롤바 커스텀 */
        ::-webkit-scrollbar {{
            width: 6px;
            height: 6px;
        }}
        ::-webkit-scrollbar-track {{
            background: #1e293b;
        }}
        ::-webkit-scrollbar-thumb {{
            background: #475569;
            border-radius: 3px;
        }}
        ::-webkit-scrollbar-thumb:hover {{
            background: #64748b;
        }}
    </style>
</head>
<body class="h-screen flex flex-col overflow-hidden">

    <!-- 상단 네비게이션 헤더 -->
    <header class="bg-slate-900 border-b border-slate-800 px-6 py-4 flex items-center justify-between shadow-md flex-shrink-0">
        <div class="flex items-center space-x-3">
            <span class="text-2xl font-bold bg-gradient-to-r from-cyan-400 to-indigo-500 bg-clip-text text-transparent">
                TopKGen Group Piping 3D Dashboard
            </span>
            <span class="px-2 py-0.5 text-xs bg-slate-800 border border-slate-700 text-cyan-400 rounded-full font-mono">
                Serverless WebApp v1.0
            </span>
        </div>
        <div class="text-sm text-slate-400">
            Total Groups: <span class="text-cyan-400 font-semibold" id="total-count-badge">0</span> |
            Equipments: <span class="text-indigo-400 font-semibold" id="equip-count-badge">0</span>
        </div>
    </header>

    <!-- 메인 대시보드 레이아웃 -->
    <div class="flex flex-1 overflow-hidden">
        
        <!-- 좌측 사이드바 필터 패널 -->
        <aside class="w-80 bg-slate-900 border-r border-slate-800 flex flex-col flex-shrink-0 shadow-lg">
            
            <!-- 필터 선택 박스 -->
            <div class="p-4 border-b border-slate-800 space-y-4">
                <div>
                    <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Equipment Tag (장비/호기)</label>
                    <select id="eq-select" class="w-full bg-white border border-slate-300 rounded px-3 py-2 text-slate-900 font-medium text-sm focus:outline-none focus:border-cyan-500">
                        <option value="" class="text-slate-900">-- 장비 선택 --</option>
                    </select>
                </div>
                <div>
                    <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Utility Group / Utility (유틸리티)</label>
                    <select id="util-select" class="w-full bg-white border border-slate-300 rounded px-3 py-2 text-slate-900 font-medium text-sm focus:outline-none focus:border-cyan-500" disabled>
                        <option value="" class="text-slate-900">-- 유틸리티 선택 --</option>
                    </select>
                </div>
            </div>

            <!-- 그룹배관 매칭 리스트 -->
            <div class="flex-1 overflow-y-auto p-4 space-y-2" id="group-list-container">
                <div class="text-center py-8 text-slate-500 text-sm">
                    좌측 상단에서 장비와 유틸리티를 선택해주세요.
                </div>
            </div>
            
        </aside>

        <!-- 우측 메인 콘텐츠 영역 (3D 뷰 + 디테일 요약) -->
        <main class="flex-1 flex flex-col bg-slate-950 overflow-hidden relative">
            
            <!-- 3D Plotly 뷰어 영역 -->
            <div id="plotly-div" class="flex-1 w-full h-full relative">
                <!-- 로딩 오버레이 -->
                <div id="loading-overlay" class="absolute inset-0 bg-slate-950/80 flex items-center justify-center z-10 hidden">
                    <div class="flex flex-col items-center space-y-4">
                        <div class="w-12 h-12 border-4 border-cyan-400 border-t-transparent rounded-full animate-spin"></div>
                        <span class="text-cyan-400 font-medium">3D 공간 렌더링 중...</span>
                    </div>
                </div>
                
                <!-- 기본 상태 표시 안내 -->
                <div id="welcome-message" class="absolute inset-0 flex flex-col items-center justify-center text-slate-500 space-y-2">
                    <svg class="w-16 h-16 text-slate-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M14 10l-2 1m0 0l-2-1m2 1v2.5M20 7l-2 1m2-1l-2-1m2 1v2.5M14 4l-2-1-2 1M4 7l2-1M4 7l2 1M4 7v2.5M12 21l-2-1m2 1l2-1m-2 1v-2.5M6 18l-2-1v-2.5M18 18l2-1v-2.5" />
                    </svg>
                    <span class="text-lg font-medium text-slate-400">3D 배관 가시화 캔버스</span>
                    <span class="text-sm text-slate-600">장비를 선택하면 공용 랙 공간과 실제 배관 경로가 드로잉됩니다.</span>
                </div>
            </div>

            <!-- 하단 디테일 정보 패널 -->
            <section class="h-64 bg-slate-900 border-t border-slate-800 p-6 flex-shrink-0 flex flex-col overflow-hidden shadow-2xl">
                <div class="flex items-center justify-between mb-4 border-b border-slate-800 pb-2 flex-shrink-0">
                    <h3 class="text-sm font-bold uppercase tracking-wider text-slate-350 flex items-center space-x-2">
                        <span class="inline-block w-2.5 h-2.5 bg-cyan-400 rounded-full animate-pulse"></span>
                        <span>Selected Pattern Information (상세 속성 정보)</span>
                    </h3>
                    <span class="text-xs text-slate-500 font-mono" id="selected-group-id-display">No Selection</span>
                </div>
                
                <div class="grid grid-cols-4 gap-6 flex-1 overflow-y-auto text-sm" id="detail-cards-container">
                    <div class="col-span-4 text-center py-8 text-slate-600">
                        3D 뷰에서 배관을 클릭하거나 좌측 리스트의 아이템을 선택하시면 상세 정보가 표출됩니다.
                    </div>
                </div>
            </section>
            
        </main>
        
    </div>

    <!-- 푸터 -->
    <footer class="bg-slate-950 border-t border-slate-900 px-6 py-2 flex items-center justify-between text-xs text-slate-500 flex-shrink-0">
        <span>© 2026 DINNO AutoRouting AI Co., Ltd. All rights reserved.</span>
        <span>Developer: Antigravity AI Subagent</span>
    </footer>

    <!-- 클라이언트 사이드 데이터 및 가시화 로직 -->
    <script>
        // 서버에서 주입한 JSON 데이터 바인딩
        const GROUP_DATA = {groups_json};
        const EQUIP_DATA = {equip_json};
        
        // 상태 전역 변수
        let selectedGroup = null;
        
        // 1. 초기 통계 설정 및 드롭다운 초기화
        document.getElementById('total-count-badge').innerText = GROUP_DATA.length;
        document.getElementById('equip-count-badge').innerText = EQUIP_DATA.length;
        
        const eqSelect = document.getElementById('eq-select');
        const utilSelect = document.getElementById('util-select');
        
        // 고유 장비(EQUIPMENT_TAG) 목록 추출하여 바인딩
        const eqTags = [...new Set(GROUP_DATA.map(g => g.EQUIPMENT_TAG))].sort();
        eqTags.forEach(tag => {{
            const opt = document.createElement('option');
            opt.value = tag;
            opt.innerText = tag;
            opt.className = "text-slate-900 bg-white";
            eqSelect.appendChild(opt);
        }});
        
        // 장비 선택 이벤트 핸들러
        eqSelect.addEventListener('change', () => {{
            const selectedEq = eqSelect.value;
            utilSelect.innerHTML = '<option value="" class="text-slate-900 bg-white">-- 유틸리티 선택 --</option>';
            document.getElementById('group-list-container').innerHTML = '<div class="text-center py-8 text-slate-500 text-sm">유틸리티를 선택해주세요.</div>';
            
            if (!selectedEq) {{
                utilSelect.disabled = true;
                return;
            }}
            
            // 해당 장비가 소유한 고유 유틸리티 그룹/유틸리티 이름 필터링
            const filteredGroups = GROUP_DATA.filter(g => g.EQUIPMENT_TAG === selectedEq);
            const utils = [...new Set(filteredGroups.map(g => `${{g.UTILITY_GROUP}} - ${{g.UTILITY}}`))].sort();
            
            utils.forEach(u => {{
                const opt = document.createElement('option');
                opt.value = u;
                opt.innerText = u;
                opt.className = "text-slate-900 bg-white";
                utilSelect.appendChild(opt);
            }});
            
            utilSelect.disabled = false;
        }});
        
        // 유틸리티 선택 이벤트 핸들러
        utilSelect.addEventListener('change', () => {{
            const selectedEq = eqSelect.value;
            const selectedUtilStr = utilSelect.value;
            const listContainer = document.getElementById('group-list-container');
            
            if (!selectedUtilStr) {{
                listContainer.innerHTML = '<div class="text-center py-8 text-slate-500 text-sm">유틸리티를 선택해주세요.</div>';
                return;
            }}
            
            const [grp, ut] = selectedUtilStr.split(' - ').map(s => s.trim());
            
            // 필터링된 그룹배관 목록 도출
            const filtered = GROUP_DATA.filter(g => 
                g.EQUIPMENT_TAG === selectedEq && 
                g.UTILITY_GROUP === grp && 
                g.UTILITY === ut
            );
            
            listContainer.innerHTML = '';
            if (filtered.length === 0) {{
                listContainer.innerHTML = '<div class="text-center py-8 text-slate-500 text-sm">해당 조건에 만족하는 그룹배관 패턴이 없습니다.</div>';
                return;
            }}
            
            filtered.forEach((g, idx) => {{
                const card = document.createElement('div');
                card.className = `p-3 rounded border border-slate-800 bg-slate-850 hover:bg-slate-800 cursor-pointer transition-all duration-150`;
                card.innerHTML = `
                    <div class="flex justify-between items-center mb-1">
                        <span class="text-xs font-mono font-bold text-cyan-400">Bundle_${{g.GROUP_ID.substring(0,8)}}</span>
                        <span class="text-[10px] bg-slate-900 border border-slate-700 px-1.5 py-0.5 rounded text-indigo-300 font-bold uppercase">${{g.PATTERN_SEQ}} Pattern</span>
                    </div>
                    <div class="grid grid-cols-2 gap-1 text-[11px] text-slate-400 mt-2">
                        <div>Members: <span class="text-slate-200 font-semibold">${{g.N_MEMBERS}}ea</span></div>
                        <div>Trunk Z: <span class="text-slate-200 font-semibold">${{g.TRUNK_Z.toLocaleString()}}mm</span></div>
                        <div>Pitch: <span class="text-slate-200 font-semibold">${{g.PITCH_MM.toFixed(1)}}mm</span></div>
                        <div>Bends: <span class="text-slate-200 font-semibold">${{g.N_ORTHO_BENDS}}ea</span></div>
                    </div>
                `;
                
                card.addEventListener('click', () => {{
                    // 이전 선택 하이라이트 해제
                    Array.from(listContainer.children).forEach(child => child.classList.remove('border-cyan-500', 'bg-slate-800'));
                    card.classList.add('border-cyan-500', 'bg-slate-800');
                    render3D(g);
                }});
                listContainer.appendChild(card);
            }});
        }});
        
        // 2. OBB 정점 산출 함수 (JavaScript 단에서 Z축 기준 회전)
        function getObbVertices(cx, cy, cz, sx, sy, sz, rotDeg) {{
            const rad = -rotDeg * Math.PI / 180; // 회전 (Z축 기준)
            const cos = Math.cos(rad);
            const sin = Math.sin(rad);
            
            const localVerts = [
                [-0.5, -0.5, -0.5], [0.5, -0.5, -0.5], [0.5, 0.5, -0.5], [-0.5, 0.5, -0.5],
                [-0.5, -0.5, 0.5],  [0.5, -0.5, 0.5],  [0.5, 0.5, 0.5],  [-0.5, 0.5, 0.5]
            ];
            
            return localVerts.map(v => {{
                const lx = v[0] * sx;
                const ly = v[1] * sy;
                const lz = v[2] * sz;
                
                const rx = lx * cos - ly * sin;
                const ry = lx * sin + ly * cos;
                
                return [rx + cx, ry + cy, lz + cz];
            }});
        }}
        
        // 3. 3D Plotly 렌더링 엔진 호출
        function render3D(group, targetGuid = null) {{
            selectedGroup = group;
            document.getElementById('welcome-message').classList.add('hidden');
            document.getElementById('loading-overlay').classList.remove('hidden');
            
            setTimeout(() => {{
                const traces = [];
                
                // [0] 자동 줌범위 (AABB) 계산을 위해 전체 배관 좌표 수집
                let pipeXs = [], pipeYs = [], pipeZs = [];
                group.lines.forEach(line => {{
                    line.forEach(pt => {{
                        pipeXs.push(pt[0]);
                        pipeYs.push(pt[1]);
                        pipeZs.push(pt[2]);
                    }});
                }});
                
                let minX = Math.min(...pipeXs), maxX = Math.max(...pipeXs);
                let minY = Math.min(...pipeYs), maxY = Math.max(...pipeYs);
                let minZ = Math.min(...pipeZs), maxZ = Math.max(...pipeZs);
                
                // 3D 뷰어 줌 스케일링 범위 설정 (여백 1500mm 추가)
                const margin = 1500;
                const rangeX = [minX - margin, maxX + margin];
                const rangeY = [minY - margin, maxY + margin];
                const rangeZ = [minZ - margin, maxZ + margin];
                
                // [1] 장비 AABB 가시화 (배경 레이어로 투명 회색 3D Cuboid)
                const matchedEquip = EQUIP_DATA.find(e => e.EQUIPMENT_TAG === group.EQUIPMENT_TAG);
                if (matchedEquip && matchedEquip.AABB_MINX !== null && matchedEquip.AABB_MINX !== undefined) {{
                    const minx = matchedEquip.AABB_MINX;
                    const miny = matchedEquip.AABB_MINY;
                    const minz = matchedEquip.AABB_MINZ;
                    const maxx = matchedEquip.AABB_MAXX;
                    const maxy = matchedEquip.AABB_MAXY;
                    const maxz = matchedEquip.AABB_MAXZ;
                    
                    const x = [minx, maxx, maxx, minx, minx, maxx, maxx, minx];
                    const y = [miny, miny, maxy, maxy, miny, miny, maxy, maxy];
                    const z = [minz, minz, minz, minz, maxz, maxz, maxz, maxz];
                    
                    const i = [0, 0, 1, 1, 2, 2, 3, 3, 0, 0, 4, 4];
                    const j = [1, 2, 5, 6, 6, 7, 7, 4, 4, 1, 5, 6];
                    const k = [2, 3, 6, 2, 7, 3, 4, 0, 1, 5, 7, 5];
                    
                    traces.push({{
                        type: 'mesh3d',
                        x: x, y: y, z: z,
                        i: i, j: j, k: k,
                        color: 'rgba(100, 116, 139, 0.22)', // 반투명 회색
                        name: `Equipment: ${{group.EQUIPMENT_TAG}}`,
                        hoverinfo: 'name',
                        showlegend: true
                    }});
                    
                    // 장비 원본 PoC 구체 렌더링
                    if (matchedEquip.POC_POSITIONS_LIST) {{
                        try {{
                            const pocs = JSON.parse(matchedEquip.POC_POSITIONS_LIST);
                            const px = pocs.map(p => p.x);
                            const py = pocs.map(p => p.y);
                            const pz = pocs.map(p => p.z);
                            
                            traces.push({{
                                type: 'scatter3d',
                                mode: 'markers',
                                x: px, y: py, z: pz,
                                marker: {{
                                    size: 5,
                                    color: '#818cf8',
                                    opacity: 0.8
                                }},
                                name: 'Equipment Origin PoC',
                                text: pocs.map((p, idx) => `Origin PoC Port [Idx: ${{idx}}]`),
                                hoverinfo: 'text'
                            }});
                        }} catch (e) {{
                            console.error("Failed to parse equipment PoCs", e);
                        }}
                    }}
                }}
                
                // [2] 실제 그룹배관 멤버 Polyline 드로잉 (선택 하이라이트 기능 추가)
                const colors = ['#22d3ee', '#f43f5e', '#10b981', '#fbbf24', '#a78bfa', '#f97316', '#06b6d4', '#ec4899'];
                
                group.lines.forEach((line, idx) => {{
                    const lx = line.map(pt => pt[0]);
                    const ly = line.map(pt => pt[1]);
                    const lz = line.map(pt => pt[2]);
                    
                    const m_guid = group.MEMBER_GUIDS[idx] || "Unknown GUID";
                    
                    // 특정 배관 선택 시 강조/비강조 스타일 동적 연동
                    let lineWidth = 5.5;
                    let opacityVal = 1.0;
                    if (targetGuid) {{
                        if (targetGuid === m_guid) {{
                            lineWidth = 9.0; // 선택된 배관은 아주 굵게
                            opacityVal = 1.0;
                        }} else {{
                            lineWidth = 3.0; // 비선택 배관들은 얇고 반투명하게 흐림
                            opacityVal = 0.35;
                        }}
                    }}
                    
                    traces.push({{
                        type: 'scatter3d',
                        mode: 'lines+markers',
                        x: lx, y: ly, z: lz,
                        line: {{
                            width: lineWidth,
                            color: colors[idx % colors.length]
                        }},
                        marker: {{
                            size: 3.5,
                            color: colors[idx % colors.length]
                        }},
                        opacity: opacityVal,
                        name: `Pipe_${{idx+1}} (${{m_guid.substring(0,8)}})`,
                        customdata: m_guid,
                        text: `Route GUID: ${{m_guid}}`,
                        hoverinfo: 'name+text'
                    }});
                    
                    // 진입/진출 장비 연결부 PoC 포트 (배관 시점/종단점 마커)
                    if (line.length >= 2) {{
                        const first = line[0];
                        const last = line[line.length - 1];
                        
                        traces.push({{
                            type: 'scatter3d',
                            mode: 'markers',
                            x: [first[0], last[0]],
                            y: [first[1], last[1]],
                            z: [first[2], last[2]],
                            marker: {{
                                size: 8,
                                color: '#fb7185',
                                symbol: 'circle',
                                line: {{
                                    color: '#ffffff',
                                    width: 1
                                }}
                            }},
                            opacity: opacityVal,
                            name: `Piping Connection PoC (Pipe_${{idx+1}})`,
                            text: [`Start Connection PoC`, `End Connection PoC`],
                            hoverinfo: 'name+text',
                            showlegend: false
                        }});
                    }}
                }});
                
                // [3] SECTION_BOUNDS 공간 영역 투명 와이어프레임 박스 + 반투명 3D Mesh 부피 드로잉
                if (group.SECTION_BOUNDS) {{
                    const boxLinesX = [];
                    const boxLinesY = [];
                    const boxLinesZ = [];
                    
                    group.SECTION_BOUNDS.forEach((sec, sIdx) => {{
                        const min = sec.min;
                        const max = sec.max;
                        
                        const v = [
                            [min[0], min[1], min[2]],
                            [max[0], min[1], min[2]],
                            [max[0], max[1], min[2]],
                            [min[0], max[1], min[2]],
                            [min[0], min[1], max[2]],
                            [max[0], min[1], max[2]],
                            [max[0], max[1], max[2]],
                            [min[0], max[1], max[2]]
                        ];
                        
                        const edges = [
                            [0,1], [1,2], [2,3], [3,0], // 바닥
                            [4,5], [5,6], [6,7], [7,4], // 천장
                            [0,4], [1,5], [2,6], [3,7]  // 기둥
                        ];
                        
                        edges.forEach(edge => {{
                            boxLinesX.push(v[edge[0]][0], v[edge[1]][0], null);
                            boxLinesY.push(v[edge[0]][1], v[edge[1]][1], null);
                            boxLinesZ.push(v[edge[0]][2], v[edge[1]][2], null);
                        }});
                        
                        // 투명 Mesh3d 부피면 채우기 추가 (부피 체감 극대화)
                        const vx = [min[0], max[0], max[0], min[0], min[0], max[0], max[0], min[0]];
                        const vy = [min[1], min[1], max[1], max[1], min[1], min[1], max[1], max[1]];
                        const vz = [min[2], min[2], min[2], min[2], max[2], max[2], max[2], max[2]];
                        const i = [0, 0, 1, 1, 2, 2, 3, 3, 0, 0, 4, 4];
                        const j = [1, 2, 5, 6, 6, 7, 7, 4, 4, 1, 5, 6];
                        const k = [2, 3, 6, 2, 7, 3, 4, 0, 1, 5, 7, 5];
                        
                        traces.push({{
                            type: 'mesh3d',
                            x: vx, y: vy, z: vz,
                            i: i, j: j, k: k,
                            color: 'rgba(34, 211, 238, 0.08)', // 아주 옅은 반투명 하늘색
                            name: `Section_${{sIdx}} Box Volume`,
                            hoverinfo: 'name',
                            showlegend: false
                        }});
                    }});
                    
                    traces.push({{
                        type: 'scatter3d',
                        mode: 'lines',
                        x: boxLinesX,
                        y: boxLinesY,
                        z: boxLinesZ,
                        line: {{
                            color: 'rgba(34, 211, 238, 0.55)', // 밝은 투명 하늘색 실선
                            width: 2.5,
                            dash: 'dash'
                        }},
                        name: 'Section BBox Outline',
                        hoverinfo: 'name'
                    }});
                }}
                
                // [5] 그룹배관 대표 중심선(Trunk Centerline) 렌더링
                if (group.trunk_lines && group.trunk_lines.length > 0) {{
                    group.trunk_lines.forEach((tline, idx) => {{
                        const tx = tline.map(pt => pt[0]);
                        const ty = tline.map(pt => pt[1]);
                        const tz = tline.map(pt => pt[2]);
                        
                        traces.push({{
                            type: 'scatter3d',
                            mode: 'lines+markers',
                            x: tx, y: ty, z: tz,
                            line: {{
                                color: '#f59e0b', // 선명한 호박색 (대표 경로선)
                                width: 5.5,
                                dash: 'solid'
                            }},
                            marker: {{
                                size: 4.5,
                                color: '#f59e0b'
                            }},
                            name: `Group Representative Route (대표 경로선)`,
                            hoverinfo: 'name'
                        }});
                    }});
                }}
                
                // [4] 3D 공간 상에 둥둥 뜨는 3D Text Annotation 라벨 추가
                const centerX = (minX + maxX) / 2;
                const centerY = (minY + maxY) / 2;
                const centerZ = maxZ + 600; // 배관 다발 600mm 상위에 표출
                traces.push({{
                    type: 'scatter3d',
                    mode: 'text+markers',
                    x: [centerX],
                    y: [centerY],
                    z: [centerZ],
                    text: [`📍 Bundle_${{group.GROUP_ID.substring(0,8)}} (${{group.PATTERN_SEQ}} Pattern)`],
                    textposition: 'top center',
                    textfont: {{
                        color: '#22d3ee',
                        size: 13,
                        family: 'Arial, sans-serif'
                    }},
                    marker: {{
                        size: 4.5,
                        color: '#22d3ee'
                    }},
                    name: 'Group Location Tag',
                    showlegend: false
                }});
                
                // Plotly Layout 레이아웃 환경설정 (Axis Range 스케일 고정으로 자동 줌인 연동)
                const layout = {{
                    paper_bgcolor: '#090d16',
                    plot_bgcolor: '#090d16',
                    scene: {{
                        xaxis: {{
                            title: 'X (mm)',
                            color: '#94a3b8',
                            gridcolor: '#1e293b',
                            zerolinecolor: '#1e293b',
                            range: rangeX // 자동 줌 적용
                        }},
                        yaxis: {{
                            title: 'Y (mm)',
                            color: '#94a3b8',
                            gridcolor: '#1e293b',
                            zerolinecolor: '#1e293b',
                            range: rangeY // 자동 줌 적용
                        }},
                        zaxis: {{
                            title: 'Z (mm)',
                            color: '#94a3b8',
                            gridcolor: '#1e293b',
                            zerolinecolor: '#1e293b',
                            range: rangeZ // 자동 줌 적용
                        }},
                        aspectmode: 'data', // 1:1:1 실제 스케일 비율 고정
                        camera: {{
                            eye: {{ x: 1.3, y: 1.3, z: 1.1 }}
                        }}
                    }},
                    margin: {{ l: 0, r: 0, b: 0, t: 30 }},
                    legend: {{
                        font: {{ color: '#e2e8f0', size: 11 }},
                        x: 0, y: 1
                    }},
                    title: {{
                        text: `3D View: ${{group.EQUIPMENT_TAG}} - ${{group.UTILITY_GROUP}} (${{group.UTILITY}})`,
                        font: {{ color: '#e2e8f0', size: 14 }},
                        x: 0.05, y: 0.95
                    }}
                }};
                
                const config = {{
                    responsive: true,
                    displaylogo: false,
                    modeBarButtonsToRemove: ['lasso2d', 'select2d']
                }};
                
                Plotly.newPlot('plotly-div', traces, layout, config);
                document.getElementById('loading-overlay').classList.add('hidden');
                
                // 정보 패널 기본값 채우기 (전체 선택된 그룹 정보 채움)
                updateDetailPanel(group, targetGuid);
                
                // Plotly 클릭 핸들러 등록
                const plotDiv = document.getElementById('plotly-div');
                plotDiv.on('plotly_click', (data) => {{
                    if (data.points && data.points.length > 0) {{
                        const pt = data.points[0];
                        if (pt.customdata) {{
                            // 배관 직접 클릭 시 뷰어를 해당 배관 하이라이트 상태로 즉시 갱신
                            render3D(group, pt.customdata);
                        }}
                    }}
                }});
                
            }}, 50);
        }}
        
        // 4. 하단 상세 카드 렌더링 업데이트 함수
        function updateDetailPanel(group, targetGuid = null) {{
            const idDisplay = document.getElementById('selected-group-id-display');
            idDisplay.innerText = group.GROUP_ID;
            
            const container = document.getElementById('detail-cards-container');
            container.innerHTML = '';
            
            // [카드 1] 패턴 기본 통계 정보
            const card1 = document.createElement('div');
            card1.className = 'bg-slate-850 p-4 rounded border border-slate-800 space-y-2';
            card1.innerHTML = `
                <div class="text-xs font-bold text-slate-400 uppercase tracking-wider">Group Base</div>
                <div class="space-y-1">
                    <div class="flex justify-between"><span class="text-slate-500">장비/호기</span><span class="font-semibold text-slate-200">${{group.EQUIPMENT_TAG}}</span></div>
                    <div class="flex justify-between"><span class="text-slate-500">유틸리티</span><span class="font-semibold text-cyan-400">${{group.UTILITY}} (${{group.UTILITY_GROUP}})</span></div>
                    <div class="flex justify-between"><span class="text-slate-500">대표 패턴</span><span class="font-semibold text-indigo-400 uppercase font-mono">${{group.PATTERN_SEQ}}</span></div>
                </div>
            `;
            container.appendChild(card1);
            
            // [카드 2] 기하 및 크기 속성
            const card2 = document.createElement('div');
            card2.className = 'bg-slate-850 p-4 rounded border border-slate-800 space-y-2';
            const tLen = group.TRUNK_LEN ? group.TRUNK_LEN.toLocaleString() : "0";
            card2.innerHTML = `
                <div class="text-xs font-bold text-slate-400 uppercase tracking-wider">Physical Dimension</div>
                <div class="space-y-1">
                    <div class="flex justify-between"><span class="text-slate-500">Trunk Z 고도</span><span class="font-semibold text-slate-200 font-mono">${{group.TRUNK_Z.toLocaleString()}} mm</span></div>
                    <div class="flex justify-between"><span class="text-slate-500">그룹배관 연장</span><span class="font-semibold text-amber-400 font-mono">${{tLen}} mm</span></div>
                    <div class="flex justify-between"><span class="text-slate-500">다발 폭(Spread)</span><span class="font-semibold text-slate-200 font-mono">${{group.TRUNK_XY_SPREAD.toFixed(1)}} mm</span></div>
                    <div class="flex justify-between"><span class="text-slate-500">평균 피치</span><span class="font-semibold text-slate-200 font-mono">${{group.PITCH_MM.toFixed(1)}} mm</span></div>
                </div>
            `;
            container.appendChild(card2);
            
            // [카드 3] 멤버 배관 목록 (클릭 시 3D 하이라이트 연동 지원)
            const card3 = document.createElement('div');
            card3.className = 'bg-slate-850 p-4 rounded border border-slate-800 space-y-2 col-span-2 flex flex-col h-full overflow-hidden';
            
            let guidListHtml = '';
            group.MEMBER_GUIDS.forEach((g, idx) => {{
                const isSelected = (targetGuid && targetGuid === g);
                const highlightClass = isSelected 
                    ? 'bg-cyan-500/25 border-cyan-500 text-cyan-400 font-bold cursor-pointer' 
                    : 'bg-slate-900 border-slate-800 text-slate-400 hover:bg-slate-800 hover:text-slate-200 cursor-pointer';
                guidListHtml += `
                    <div onclick="render3D(selectedGroup, '${{g}}')" class="px-2.5 py-1.5 border rounded text-[11px] font-mono flex justify-between items-center transition-all duration-100 ${{highlightClass}}">
                        <span>[Pipe_${{idx+1}}] ${{g}}</span>
                        ${{isSelected ? '<span class="text-[9px] bg-cyan-500 text-slate-950 font-bold px-1 rounded uppercase">Selected</span>' : ''}}
                    </div>
                `;
            }});
            
            card3.innerHTML = `
                <div class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">
                    Piping Members (${{group.N_MEMBERS}}ea) ${{targetGuid ? '- <span class="text-cyan-400 font-semibold">1개 배관이 3D 뷰에서 선택됨</span>' : ''}}
                </div>
                <div class="space-y-1 overflow-y-auto flex-1 max-h-32 pr-1">
                    ${{guidListHtml}}
                </div>
            `;
            container.appendChild(card3);
        }}
        
    </script>
</body>
</html>
"""

    # HTML 템플릿의 주입 태그 치환
    html_content = html_template.replace("{groups_json}", groups_json).replace("{equip_json}", equip_json)
    
    # 출력 폴더 생성 및 저장
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
        
    print(f"Interactive Web Dashboard successfully saved to: {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive 3D Route Group Pattern Web Viewer builder")
    tool_config.add_common_args(parser)
    parser.add_argument("--html-out", default="data/output/route_group_viewer.html",
                        help="Path to save the generated interactive HTML viewer")
    
    args = parser.parse_args()
    
    runtime = tool_config.resolve_runtime(args)
    tool_config.print_runtime(runtime)
    
    conn = open_connection(runtime.conninfo)
    
    try:
        # 데이터베이스로부터 그룹 패턴 데이터와 장비 데이터를 한 번에 조회
        groups = load_route_groups(conn)
        equipments = load_equipments(conn)
        
        if not groups:
            print("[warn] No group patterns found in TB_ROUTE_GROUP_PATTERN table. Please run analyze first.")
            return 1
            
        # 단일 HTML 대시보드 뷰어 빌드 및 저장
        generate_viewer_html(groups, equipments, args.html_out)
        
        # 브라우저 오픈 안내 및 자동 실행
        abs_html_path = os.path.abspath(args.html_out)
        print(f"\nOpening dashboard in web browser: file://{abs_html_path}")
        webbrowser.open(f"file://{abs_html_path}")
        
    finally:
        conn.close()
        
    return 0


if __name__ == "__main__":
    sys.exit(main())
