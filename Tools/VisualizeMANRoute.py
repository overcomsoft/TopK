"""
[실행 명령어]
> python VisualizeMANRoute.py --eq_name "장비명"
(예: python VisualizeMANRoute.py --eq_name MAN)

[전체 프로그램 흐름]
1. 인자 처리: argparse를 통해 사용자가 조회하고자 하는 장비명(EQUIPMENT_NAME)을 입력받습니다.
2. DB 연결: psycopg2를 이용해 PostgreSQL(DDW_AI_DB)에 접속합니다.
3. 경로 데이터 쿼리: TB_ROUTE_PATH 테이블에서 장비명(EQUIPMENT_NAME)에 사용자가 입력한 키워드가 포함된 경로를 검색하고, TB_ROUTE_SEGMENTS, TB_ROUTE_SEGMENT_DETAIL 테이블과 JOIN하여 해당 경로의 실제 물리적 선분 3D 좌표들을 일괄 가져옵니다.
4. 데이터 최적화 및 그룹화: 검색된 결과들을 라우트 경로 GUID(ROUTE_PATH_GUID) 기준으로 그룹화하고, 연속적인 3D 선분을 그리기 위해 좌표 배열을 구성합니다.
5. Plotly 3D 시각화: Plotly의 Scatter3d 객체를 생성하고 생성된 경로를 3D 공간에 렌더링한 후, HTML 파일로 저장하여 브라우저에 자동 표시합니다.

[핵심 알고리즘]
- 단일 Trace 선분 분리(Line Disconnection) 알고리즘: Plotly에서 수천 개의 개별 3D 파이프 선분 세그먼트를 낱개의 Trace로 추가하면 브라우저 메모리가 초과되고 렌더링 성능이 급격히 저하됩니다. 이를 막기 위해 하나의 배관(ROUTE_PATH_GUID)에 속한 FROM(시작점) -> TO(종료점) 좌표 쌍을 1차원 x, y, z 배열에 누적하되, 각 세그먼트 사이에는 `None` 값을 의도적으로 삽입합니다. 이렇게 하면 단 한 번의 드로우 콜(Draw Call)만으로도 수천 개의 흩어진 선분을 최적화된 상태로 렌더링할 수 있습니다.

[핵심 함수 설명]
- visualize_route_paths(eq_name_keyword): 스크립트의 유일한 메인 프로세스 함수로, DB 접속부터 사용자가 지정한 장비 키워드에 기반한 3D 좌표 추출 SQL 질의 실행, Plotly 3D 가시화 처리, HTML 파일 출력까지 모든 동작을 제어합니다.

[주요 변수 설명]
- eq_name_keyword: 조회하고자 하는 타겟 장비명의 키워드 문자열입니다.
- conn_str: PostgreSQL 데이터베이스 접속을 위한 로컬 커넥션 문자열입니다.
- query: 타겟 설비와 관련된 배관 상세 세그먼트 좌표(FROM/TO)를 3개의 테이블 조인을 통해 가져오는 파라미터화된 SQL 문자열입니다.
- routes_dict: 각 배관 경로(ROUTE_PATH_GUID)를 Key로 가지고, 내부에는 장비명(eq_name), 유틸리티 종류(utility) 및 Plotly 라인 렌더링에 직접 투입되는 1차원 좌표 배열(x, y, z 리스트)을 저장하는 그룹화 딕셔너리 구조체입니다.
- fig: Plotly 프레임워크의 그래프 인스턴스(Figure)로, 3D 레이아웃 정보와 매쉬(Trace) 데이터가 최종적으로 결합되는 캔버스 객체입니다.
"""

import os
import argparse
import psycopg2
import plotly.graph_objects as go
from tool_config import add_common_args, print_runtime, resolve_runtime

def visualize_route_paths(eq_name_keyword, runtime, auto_open=True):
    """
    TB_ROUTE_PATH, TB_ROUTE_SEGMENTS, TB_ROUTE_SEGMENT_DETAIL 테이블을 조인하여
    'MAN' 설비와 관련된 배관 경로를 3D로 시각화합니다.
    """
    # PostgreSQL DB 접속 정보 (기존 ViewPlan3D.py, ExportEquipmentPlan.py 참고)
    print("DB에 연결 중...")
    try:
        conn = psycopg2.connect(runtime.conninfo)
    except Exception as e:
        print(f"DB Connection failed: {e}")
        return

    cur = conn.cursor()
    
    # 지정한 장비명이 포함된 경로 쿼리 (대소문자 무관 검색을 위해 ILIKE 및 파라미터 바인딩 사용)
    query = """
        SELECT 
            rp."ROUTE_PATH_GUID",
            rp."EQUIPMENT_NAME",
            rp."SOURCE_UTILITY",
            rs."ORDER" AS SEG_ORDER,
            sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",
            sd."TO_POSX", sd."TO_POSY", sd."TO_POSZ"
        FROM "TB_ROUTE_PATH" rp
        JOIN "TB_ROUTE_SEGMENTS" rs ON rp."ROUTE_PATH_GUID" = rs."ROUTE_PATH_GUID"
        JOIN "TB_ROUTE_SEGMENT_DETAIL" sd ON rs."SEGMENT_GUID" = sd."SEGMENT_GUID"
        WHERE rp."EQUIPMENT_NAME" ILIKE %s
        ORDER BY rp."ROUTE_PATH_GUID", rs."ORDER", sd."ORDER"
    """
    
    print(f"'{eq_name_keyword}' 관련 데이터를 조회하고 있습니다...")
    cur.execute(query, (f"%{eq_name_keyword}%",))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print(f"'{eq_name_keyword}' 설비로 시작하는 배관 경로(TB_ROUTE_PATH) 데이터를 찾을 수 없습니다.")
        return

    print(f"총 {len(rows)}개의 배관 세그먼트 데이터를 찾았습니다. 3D 렌더링을 준비합니다...")

    # ROUTE_PATH_GUID 별로 3D 선분 좌표 데이터를 그룹화
    routes_dict = {}
    for row in rows:
        rp_guid = row[0]
        eq_name = row[1]
        utility = row[2] if row[2] else "UNKNOWN"
        
        fx, fy, fz = row[4], row[5], row[6]
        tx, ty, tz = row[7], row[8], row[9]
        
        # null 좌표 방지
        if fx is None or tx is None:
            continue
            
        if rp_guid not in routes_dict:
            routes_dict[rp_guid] = {
                'eq_name': eq_name,
                'utility': utility,
                'x': [], 'y': [], 'z': []
            }
        
        # Plotly에서 끊어진 여러 개의 선분(Line)을 한 번의 Trace로 그리려면
        # FROM 좌표 -> TO 좌표 기록 후 None을 삽입하여 선을 끊어줍니다.
        routes_dict[rp_guid]['x'].extend([fx, tx, None])
        routes_dict[rp_guid]['y'].extend([fy, ty, None])
        routes_dict[rp_guid]['z'].extend([fz, tz, None])

    # Plotly 3D Figure 생성
    fig = go.Figure()

    # 추출된 각 배관 경로별로 3D 라인 생성
    for guid, route_data in routes_dict.items():
        name_label = f"{route_data['eq_name']} (Util: {route_data['utility']})"
        fig.add_trace(go.Scatter3d(
            x=route_data['x'],
            y=route_data['y'],
            z=route_data['z'],
            mode='lines',
            line=dict(width=5), # 선 두께 설정
            name=name_label
        ))

    # 3D 뷰 렌더링 레이아웃 설정
    fig.update_layout(
        title=f"'{eq_name_keyword}' 설비 배관 설계 경로 3D 시각화",
        scene=dict(
            xaxis_title='X (mm)',
            yaxis_title='Y (mm)',
            zaxis_title='Z (mm)',
            aspectmode='data' # 실제 x,y,z 스케일 비율 유지
        ),
        margin=dict(l=0, r=0, b=0, t=40),
        legend=dict(title="경로(장비명 - 유틸리티)")
    )

    # 출력 폴더 및 파일 경로 설정
    out_dir = runtime.out_dir
    print_runtime(runtime)
    
    # 윈도우 파일명에 사용할 수 없는 특수문자 안전 처리
    safe_keyword = "".join([c if c.isalnum() else "_" for c in eq_name_keyword])
    out_path = os.path.join(out_dir, f"{safe_keyword}_RoutePath_3D.html")
    
    # HTML 파일로 3D 가시화 결과 저장 및 브라우저에서 자동 열기
    fig.write_html(out_path, auto_open=auto_open)
    print(f"성공적으로 3D 가시화 HTML 파일이 생성되었습니다: {out_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="특정 설비의 3D 배관 설계 경로 가시화 도구")
    parser.add_argument("--eq_name", type=str, default="MAN", help="검색할 설비명(EQUIPMENT_NAME) 키워드를 입력하세요. (기본값: MAN)")
    parser.add_argument("--no-open", action="store_true", help="Save HTML without opening a browser")
    add_common_args(parser)
    args = parser.parse_args()
    runtime = resolve_runtime(args)
    
    visualize_route_paths(args.eq_name, runtime, auto_open=not args.no_open)
