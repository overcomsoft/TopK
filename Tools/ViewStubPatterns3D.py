from __future__ import annotations

"""
Start/End Stub 패턴 3D 뷰어 생성 도구.

이 파일은 ExtractStubPatterns.py가 TB_ROUTE_STUB_PATTERN 테이블에 저장한
Start Stub / End Stub 생성 결과를 읽어 HTML 3D 뷰어로 저장한다.

전체 프로세스
1. 사용자가 DB 접속정보와 선택 필터를 명령행 인자로 입력한다.
2. DDW_AI_DB의 TB_ROUTE_STUB_PATTERN 테이블에서 Stub 좌표(STUB_POINTS)를 조회한다.
3. 조회된 Stub 데이터를 브라우저에서 바로 사용할 수 있는 JSON으로 정규화한다.
4. Plotly.js가 포함된 단일 HTML 파일을 생성한다.
5. HTML 화면에서 메인장비목록, 유틸리티그룹목록, 유틸리티목록을 선택하면
   선택 조건에 맞는 Start/End Stub만 3D 공간에 표시된다.

실행 예시
    python Tools\\ViewStubPatterns3D.py --host localhost --port 5432 --dbname DDW_AI_DB --user postgres --password dinno

필터를 걸어 HTML 생성
    python Tools\\ViewStubPatterns3D.py --host localhost --port 5432 --dbname DDW_AI_DB --user postgres --password dinno ^
        --main-equipment WTNHJ02 --utility-group Water --utility PCWS --limit 1000

브라우저 자동 열기 없이 파일만 생성
    python Tools\\ViewStubPatterns3D.py --host localhost --port 5432 --dbname DDW_AI_DB --user postgres --password dinno --no-open
"""

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras
from plotly.offline import get_plotlyjs

from tool_config import add_common_args, print_runtime, resolve_runtime


DEFAULT_HTML_NAME = "view_stub_patterns_3d.html"


def main() -> int:
    """명령행 인자를 해석하고 Stub 패턴 3D 뷰어 HTML을 생성하는 진입점."""
    parser = build_parser()
    args = parser.parse_args()

    try:
        runtime = resolve_runtime(args)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print_runtime(runtime)

    try:
        with open_connection(runtime.conninfo) as conn:
            ensure_stub_pattern_table(conn)
            rows = fetch_stub_pattern_rows(conn, args)
    except UnicodeDecodeError as exc:
        print(
            "DB 접속 문자열 또는 비밀번호 인코딩을 해석하는 중 오류가 발생했습니다.\n"
            "해결 방법: --config 대신 --host/--port/--dbname/--user/--password를 직접 입력하거나, "
            "Tools/tools.settings.json을 UTF-8로 저장했는지 확인하세요.\n"
            f"원본 오류: {exc}",
            file=sys.stderr,
        )
        return 1
    except psycopg2.Error as exc:
        print(f"DB 조회 오류: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    out_path = resolve_output_path(runtime.out_dir, args.out_html)
    html = build_html(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    print(f"Stub rows loaded: {len(rows)}")
    print(f"HTML saved: {out_path}")

    if not args.no_open:
        webbrowser.open(out_path.resolve().as_uri())

    return 0


def build_parser() -> argparse.ArgumentParser:
    """공통 DB 인자와 Stub 3D 뷰어 전용 인자를 정의한다."""
    parser = argparse.ArgumentParser(
        description="TB_ROUTE_STUB_PATTERN의 Start/End Stub 생성 결과를 3D HTML 뷰어로 표시합니다."
    )
    add_common_args(parser)
    parser.add_argument("--main-equipment", default=None, help="초기 조회 대상을 특정 메인장비명으로 제한")
    parser.add_argument("--utility-group", default=None, help="초기 조회 대상을 특정 유틸리티그룹으로 제한")
    parser.add_argument("--utility", default=None, help="초기 조회 대상을 특정 유틸리티로 제한")
    parser.add_argument("--limit", type=int, default=5000, help="DB에서 읽을 최대 Stub row 수")
    parser.add_argument("--out-html", default=None, help="생성할 HTML 파일 경로")
    parser.add_argument("--no-open", action="store_true", help="HTML 생성 후 브라우저를 자동으로 열지 않음")
    return parser


def open_connection(conninfo: str):
    """psycopg2 연결을 열고, 한글/JSON 조회가 안정적으로 동작하도록 UTF-8 client_encoding을 지정한다."""
    conn = psycopg2.connect(conninfo)
    conn.set_client_encoding("UTF8")
    return conn


def ensure_stub_pattern_table(conn) -> None:
    """Stub 패턴 저장 테이블이 없으면 사용자가 선행 명령을 알 수 있도록 명확한 오류를 낸다."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", ('"TB_ROUTE_STUB_PATTERN"',))
        exists = cur.fetchone()[0]
    if not exists:
        raise RuntimeError(
            'TB_ROUTE_STUB_PATTERN 테이블이 없습니다. 먼저 '
            '"python Tools\\ExtractStubPatterns.py create-schema ..."와 '
            '"python Tools\\ExtractStubPatterns.py extract ..."를 실행하세요.'
        )


def fetch_stub_pattern_rows(conn, args: argparse.Namespace) -> list[dict[str, Any]]:
    """DB에서 Start/End Stub 패턴 row를 조회하고 브라우저용 JSON 구조로 정규화한다."""
    where = ['"STUB_POINTS" IS NOT NULL']
    params: list[Any] = []

    if args.main_equipment:
        where.append('"MAIN_EQUIPMENT_NAME" ILIKE %s')
        params.append(args.main_equipment)
    if args.utility_group:
        where.append('"UTILITY_GROUP" = %s')
        params.append(args.utility_group)
    if args.utility:
        where.append('"UTILITY" = %s')
        params.append(args.utility)

    limit = max(1, int(args.limit or 1))
    params.append(limit)

    sql = f"""
        SELECT
            "PATTERN_ID",
            "ROUTE_PATH_GUID",
            "STUB_KIND",
            "ANCHOR_KIND",
            "ANCHOR_NAME",
            "MAIN_EQUIPMENT_NAME",
            "PROCESS_NAME",
            "UTILITY_GROUP",
            "UTILITY",
            "SIZE",
            "FACE",
            "DIR_SEQ",
            "N_BENDS",
            "RISE_MM",
            "OFFSET_MM",
            "STUB_LENGTH_MM",
            "STUB_POINTS",
            "ANCHOR_MIN",
            "ANCHOR_MAX"
        FROM "TB_ROUTE_STUB_PATTERN"
        WHERE {" AND ".join(where)}
        ORDER BY "MAIN_EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY", "STUB_KIND", "PATTERN_ID"
        LIMIT %s
    """

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        raw_rows = cur.fetchall()

    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        points = normalize_points(raw.get("STUB_POINTS"))
        if len(points) < 2:
            continue

        rows.append(
            {
                "pattern_id": raw.get("PATTERN_ID"),
                "route_path_guid": raw.get("ROUTE_PATH_GUID"),
                "stub_kind": raw.get("STUB_KIND") or "",
                "anchor_kind": raw.get("ANCHOR_KIND") or "",
                "anchor_name": raw.get("ANCHOR_NAME") or "",
                "main_equipment_name": raw.get("MAIN_EQUIPMENT_NAME") or "",
                "process_name": raw.get("PROCESS_NAME") or "",
                "utility_group": raw.get("UTILITY_GROUP") or "",
                "utility": raw.get("UTILITY") or "",
                "size": raw.get("SIZE") or "",
                "face": raw.get("FACE") or "",
                "dir_seq": raw.get("DIR_SEQ") or "",
                "n_bends": number_or_none(raw.get("N_BENDS")),
                "rise_mm": number_or_none(raw.get("RISE_MM")),
                "offset_mm": number_or_none(raw.get("OFFSET_MM")),
                "stub_length_mm": number_or_none(raw.get("STUB_LENGTH_MM")),
                "stub_points": points,
                "anchor_min": normalize_point(raw.get("ANCHOR_MIN")),
                "anchor_max": normalize_point(raw.get("ANCHOR_MAX")),
            }
        )
    return rows


def normalize_points(value: Any) -> list[list[float]]:
    """JSON/문자열/튜플 형태로 들어올 수 있는 STUB_POINTS를 [[x,y,z], ...]로 변환한다."""
    parsed = parse_json_value(value)
    if not isinstance(parsed, list):
        return []

    points: list[list[float]] = []
    for item in parsed:
        point = normalize_point(item)
        if point:
            points.append(point)
    return points


def normalize_point(value: Any) -> list[float] | None:
    """단일 좌표를 [x, y, z] float 리스트로 변환한다."""
    parsed = parse_json_value(value)
    if isinstance(parsed, dict):
        parsed = [parsed.get("x"), parsed.get("y"), parsed.get("z")]
    if not isinstance(parsed, (list, tuple)) or len(parsed) < 3:
        return None

    try:
        return [float(parsed[0]), float(parsed[1]), float(parsed[2])]
    except (TypeError, ValueError):
        return None


def parse_json_value(value: Any) -> Any:
    """psycopg2가 jsonb를 이미 파싱한 경우와 문자열로 반환한 경우를 모두 처리한다."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def number_or_none(value: Any) -> float | int | None:
    """HTML JSON에 넣기 전에 Decimal 등 숫자 유사 값을 기본 숫자로 변환한다."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return int(number)
    return number


def resolve_output_path(out_dir: str, out_html: str | None) -> Path:
    """사용자 지정 HTML 경로가 있으면 사용하고, 없으면 data/output 기본 파일명을 사용한다."""
    if out_html:
        return Path(os.path.expandvars(os.path.expanduser(out_html))).resolve()
    return Path(out_dir).resolve() / DEFAULT_HTML_NAME


def build_html(rows: list[dict[str, Any]]) -> str:
    """Stub 데이터와 Plotly.js를 포함한 단일 HTML 문서를 만든다."""
    rows_json = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    plotly_js = get_plotlyjs()

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Start/End Stub 3D Viewer</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #101418;
      --panel: #182026;
      --line: #2d3942;
      --text: #e7edf2;
      --muted: #9dadba;
      --start: #ff5a4e;
      --end: #4aa3ff;
      --poc: #c8c8c8;
      --accent: #7dd3a8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: "Malgun Gothic", "Segoe UI", Arial, sans-serif;
      overflow: hidden;
    }}
    .toolbar {{
      height: 74px;
      display: grid;
      grid-template-columns: minmax(180px, 1fr) minmax(170px, 0.9fr) minmax(150px, 0.8fr) auto;
      gap: 12px;
      align-items: end;
      padding: 10px 14px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
    }}
    label {{
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      min-width: 0;
    }}
    select {{
      width: 100%;
      min-width: 0;
      height: 34px;
      border: 1px solid var(--line);
      background: #0f1418;
      color: var(--text);
      padding: 0 10px;
      border-radius: 6px;
      font-size: 13px;
    }}
    .summary {{
      min-width: 260px;
      display: grid;
      gap: 5px;
      justify-items: end;
      font-size: 13px;
      color: var(--muted);
      white-space: nowrap;
    }}
    .legend {{
      display: flex;
      gap: 14px;
      justify-content: end;
      color: var(--text);
    }}
    .dot {{
      width: 10px;
      height: 10px;
      display: inline-block;
      border-radius: 50%;
      margin-right: 6px;
    }}
    .start {{ background: var(--start); }}
    .end {{ background: var(--end); }}
    .poc {{ background: var(--poc); }}
    #plot {{
      width: 100vw;
      height: calc(100vh - 74px);
    }}
    @media (max-width: 860px) {{
      body {{ overflow: auto; }}
      .toolbar {{
        height: auto;
        grid-template-columns: 1fr;
        align-items: stretch;
      }}
      .summary {{ justify-items: start; min-width: 0; white-space: normal; }}
      .legend {{ justify-content: start; }}
      #plot {{ height: 72vh; }}
    }}
  </style>
</head>
<body>
  <div class="toolbar">
    <label>메인장비목록
      <select id="equipmentSelect"></select>
    </label>
    <label>유틸리티그룹목록
      <select id="groupSelect"></select>
    </label>
    <label>유틸리티목록
      <select id="utilitySelect"></select>
    </label>
    <div class="summary">
      <div id="countText"></div>
      <div class="legend">
        <span><i class="dot start"></i>Start Stub</span>
        <span><i class="dot end"></i>End Stub</span>
        <span><i class="dot poc"></i>Stub End Link</span>
      </div>
    </div>
  </div>
  <div id="plot"></div>

  <script>{plotly_js}</script>
  <script>
    const stubRows = {rows_json};
    const ALL = "__ALL__";
    const startColor = "#ff5a4e";
    const endColor = "#4aa3ff";
    const pocPairColor = "#c8c8c8";

    const equipmentSelect = document.getElementById("equipmentSelect");
    const groupSelect = document.getElementById("groupSelect");
    const utilitySelect = document.getElementById("utilitySelect");
    const countText = document.getElementById("countText");

    function uniqueSorted(values) {{
      return Array.from(new Set(values.filter((value) => value && String(value).trim() !== "")))
        .sort((a, b) => String(a).localeCompare(String(b), "ko"));
    }}

    function fillSelect(select, values, allLabel) {{
      const current = select.value || ALL;
      select.innerHTML = "";
      const allOption = document.createElement("option");
      allOption.value = ALL;
      allOption.textContent = allLabel;
      select.appendChild(allOption);

      values.forEach((value) => {{
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      }});

      select.value = values.includes(current) ? current : ALL;
    }}

    function selectedRows() {{
      return stubRows.filter((row) => {{
        return (equipmentSelect.value === ALL || row.main_equipment_name === equipmentSelect.value)
          && (groupSelect.value === ALL || row.utility_group === groupSelect.value)
          && (utilitySelect.value === ALL || row.utility === utilitySelect.value);
      }});
    }}

    function refreshDependentLists() {{
      const equipmentValue = equipmentSelect.value;
      const groupValue = groupSelect.value;

      const rowsAfterEquipment = stubRows.filter((row) => (
        equipmentValue === ALL || row.main_equipment_name === equipmentValue
      ));
      fillSelect(groupSelect, uniqueSorted(rowsAfterEquipment.map((row) => row.utility_group)), "전체 유틸리티그룹");
      if (uniqueSorted(rowsAfterEquipment.map((row) => row.utility_group)).includes(groupValue)) {{
        groupSelect.value = groupValue;
      }}

      const rowsAfterGroup = rowsAfterEquipment.filter((row) => (
        groupSelect.value === ALL || row.utility_group === groupSelect.value
      ));
      fillSelect(utilitySelect, uniqueSorted(rowsAfterGroup.map((row) => row.utility)), "전체 유틸리티");
    }}

    function rowLabel(row) {{
      const parts = [
        row.stub_kind,
        row.main_equipment_name,
        row.utility_group,
        row.utility,
        row.size,
        row.anchor_name,
        row.face ? "FACE " + row.face : "",
        row.stub_length_mm != null ? row.stub_length_mm + " mm" : ""
      ].filter(Boolean);
      return parts.join(" / ");
    }}

    function buildStubTrace(rows, kind, color) {{
      const x = [];
      const y = [];
      const z = [];
      const text = [];

      rows.filter((row) => row.stub_kind === kind).forEach((row) => {{
        const label = rowLabel(row);
        row.stub_points.forEach((point, index) => {{
          x.push(point[0]);
          y.push(point[1]);
          z.push(point[2]);
          text.push(label + "<br>Point " + (index + 1) + ": (" + point.map((v) => Number(v).toFixed(1)).join(", ") + ")");
        }});
        x.push(null);
        y.push(null);
        z.push(null);
        text.push(null);
      }});

      return {{
        type: "scatter3d",
        mode: "lines+markers",
        name: kind + " Stub",
        x,
        y,
        z,
        text,
        hoverinfo: "text",
        line: {{ color, width: 7 }},
        marker: {{ color, size: 4, symbol: "circle" }}
      }};
    }}

    function buildStubEndLinkTrace(rows) {{
      const x = [];
      const y = [];
      const z = [];
      const text = [];
      const byRoute = new Map();

      rows.forEach((row) => {{
        const key = row.route_path_guid || [
          row.main_equipment_name,
          row.utility_group,
          row.utility,
          row.size
        ].join("|");
        if (!byRoute.has(key)) byRoute.set(key, {{ start: [], end: [] }});
        if (row.stub_kind === "START") byRoute.get(key).start.push(row);
        if (row.stub_kind === "END") byRoute.get(key).end.push(row);
      }});

      byRoute.forEach((group, routeKey) => {{
        group.start.forEach((startRow) => {{
          group.end.forEach((endRow) => {{
            const startEndPoint = startRow.stub_points[startRow.stub_points.length - 1];
            const endEndPoint = endRow.stub_points[endRow.stub_points.length - 1];
            if (!startEndPoint || !endEndPoint) return;
            const label = "Stub End Link"
              + "<br>ROUTE_PATH_GUID: " + routeKey
              + "<br>START: " + rowLabel(startRow)
              + "<br>END: " + rowLabel(endRow);

            [startEndPoint, endEndPoint].forEach((point, index) => {{
              x.push(point[0]);
              y.push(point[1]);
              z.push(point[2]);
              text.push(label + "<br>" + (index === 0 ? "Start Stub 끝좌표" : "End Stub 끝좌표")
                + ": (" + point.map((v) => Number(v).toFixed(1)).join(", ") + ")");
            }});
            x.push(null);
            y.push(null);
            z.push(null);
            text.push(null);
          }});
        }});
      }});

      return {{
        type: "scatter3d",
        mode: "lines+markers",
        name: "Stub End Link",
        x,
        y,
        z,
        text,
        hoverinfo: "text",
        line: {{ color: pocPairColor, width: 3 }},
        marker: {{ color: pocPairColor, size: 3, symbol: "circle-open" }}
      }};
    }}

    function buildAnchorTrace(rows) {{
      const x = [];
      const y = [];
      const z = [];
      const text = [];
      const edges = [[0,1],[1,3],[3,2],[2,0],[4,5],[5,7],[7,6],[6,4],[0,4],[1,5],[2,6],[3,7]];

      rows.forEach((row) => {{
        if (!row.anchor_min || !row.anchor_max) return;
        const mn = row.anchor_min;
        const mx = row.anchor_max;
        const corners = [
          [mn[0], mn[1], mn[2]], [mx[0], mn[1], mn[2]], [mn[0], mx[1], mn[2]], [mx[0], mx[1], mn[2]],
          [mn[0], mn[1], mx[2]], [mx[0], mn[1], mx[2]], [mn[0], mx[1], mx[2]], [mx[0], mx[1], mx[2]]
        ];
        const label = "Anchor / " + row.anchor_name + " / " + row.main_equipment_name;
        edges.forEach(([a, b]) => {{
          [corners[a], corners[b]].forEach((point) => {{
            x.push(point[0]);
            y.push(point[1]);
            z.push(point[2]);
            text.push(label);
          }});
          x.push(null);
          y.push(null);
          z.push(null);
          text.push(null);
        }});
      }});

      return {{
        type: "scatter3d",
        mode: "lines",
        name: "Anchor Box",
        x,
        y,
        z,
        text,
        hoverinfo: "text",
        line: {{ color: "rgba(150, 170, 185, 0.28)", width: 2 }},
        showlegend: true
      }};
    }}

    function render() {{
      const rows = selectedRows();
      const startCount = rows.filter((row) => row.stub_kind === "START").length;
      const endCount = rows.filter((row) => row.stub_kind === "END").length;
      countText.textContent = "표시 Stub: " + rows.length + "개 (Start " + startCount + ", End " + endCount + ")";

      const traces = [
        buildAnchorTrace(rows),
        buildStubEndLinkTrace(rows),
        buildStubTrace(rows, "START", startColor),
        buildStubTrace(rows, "END", endColor)
      ];

      const layout = {{
        margin: {{ l: 0, r: 0, t: 0, b: 0 }},
        paper_bgcolor: "#101418",
        plot_bgcolor: "#101418",
        font: {{ color: "#e7edf2" }},
        showlegend: true,
        legend: {{ x: 0.01, y: 0.99, bgcolor: "rgba(16,20,24,0.75)" }},
        scene: {{
          aspectmode: "data",
          bgcolor: "#101418",
          xaxis: {{ title: "X (mm)", color: "#9dadba", gridcolor: "#29343d", zerolinecolor: "#3a4650" }},
          yaxis: {{ title: "Y (mm)", color: "#9dadba", gridcolor: "#29343d", zerolinecolor: "#3a4650" }},
          zaxis: {{ title: "Z (mm)", color: "#9dadba", gridcolor: "#29343d", zerolinecolor: "#3a4650" }}
        }}
      }};

      Plotly.react("plot", traces, layout, {{ responsive: true, displaylogo: false }});
    }}

    fillSelect(equipmentSelect, uniqueSorted(stubRows.map((row) => row.main_equipment_name)), "전체 메인장비");
    refreshDependentLists();
    render();

    equipmentSelect.addEventListener("change", () => {{
      refreshDependentLists();
      render();
    }});
    groupSelect.addEventListener("change", () => {{
      const rowsAfterGroup = stubRows.filter((row) => (
        (equipmentSelect.value === ALL || row.main_equipment_name === equipmentSelect.value)
        && (groupSelect.value === ALL || row.utility_group === groupSelect.value)
      ));
      fillSelect(utilitySelect, uniqueSorted(rowsAfterGroup.map((row) => row.utility)), "전체 유틸리티");
      render();
    }});
    utilitySelect.addEventListener("change", render);
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
