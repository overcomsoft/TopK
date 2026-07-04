"""TB_ROUTE_CONTEXT_VECTOR 일괄 빌드 CLI.

사용법:
  python BuildContextVectors.py
  python BuildContextVectors.py --start-radius 1000 --end-radius 2000
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
import psycopg2

from ContextVectorEncoder.encoder import encode_context_vector, load_obstacle_index


def _load_route_points(cur, guid: str) -> list[np.ndarray]:
    """경로의 세그먼트 포인트 시퀀스 (tail 제거)."""
    cur.execute(
        '''SELECT sd."TYPE", sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",
                  sd."TO_POSX", sd."TO_POSY", sd."TO_POSZ"
           FROM "TB_ROUTE_SEGMENTS" rs
           JOIN "TB_ROUTE_SEGMENT_DETAIL" sd ON sd."SEGMENT_GUID" = rs."SEGMENT_GUID"
           WHERE rs."ROUTE_PATH_GUID" = %s
           ORDER BY rs."ORDER", sd."ORDER";''', (guid,))
    rows = cur.fetchall()
    if not rows: return []
    # tail 제거
    last_poc = -1
    for i, r in enumerate(rows):
        if (r[0] or "").strip().upper() == "POC":
            last_poc = i
    if last_poc >= 0:
        rows = rows[: last_poc + 1]
    pts: list[np.ndarray] = []
    for r in rows:
        a = np.array([r[1], r[2], r[3]])
        b = np.array([r[4], r[5], r[6]])
        if not pts: pts.append(a)
        if float(np.linalg.norm(b - pts[-1])) > 1.0:
            pts.append(b)
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--dbname", default="AUTOROUTINGV7")
    ap.add_argument("--user", default="postgres")
    ap.add_argument("--password", default="dinno")
    ap.add_argument("--start-radius", type=float, default=1000.0)
    ap.add_argument("--end-radius", type=float, default=2000.0)
    ap.add_argument("--limit", type=int, default=None, help="개발용 N건 제한")
    args = ap.parse_args()

    conn = psycopg2.connect(host=args.host, port=args.port, dbname=args.dbname,
                             user=args.user, password=args.password)

    print(f"[1/4] ObstacleIndex 로드")
    t0 = time.time()
    idx = load_obstacle_index(conn)
    print(f"  {len(idx._aabbs)}건 인덱싱 ({time.time()-t0:.1f}s)")

    print(f"[2/4] 기존 TB_ROUTE_CONTEXT_VECTOR 삭제")
    cur = conn.cursor()
    cur.execute('DELETE FROM "TB_ROUTE_CONTEXT_VECTOR";')
    conn.commit()

    print(f"[3/4] 경로 목록 로드")
    cur.execute('''SELECT TRIM("ROUTE_PATH_GUID"),
                          "SOURCE_POSX","SOURCE_POSY","SOURCE_POSZ",
                          "TARGET_POSX","TARGET_POSY","TARGET_POSZ"
                   FROM "TB_ROUTE_PATH"
                   ORDER BY "ROUTE_PATH_GUID";''')
    routes = cur.fetchall()
    if args.limit: routes = routes[:args.limit]
    print(f"  {len(routes)}건 대상")

    print(f"[4/4] 컨텍스트 벡터 계산 + INSERT (start_radius={args.start_radius} end_radius={args.end_radius})")
    t0 = time.time()
    n_ok = 0; n_fail = 0
    for i, r in enumerate(routes):
        guid = r[0]
        start = np.array([r[1], r[2], r[3]], dtype=float)
        end = np.array([r[4], r[5], r[6]], dtype=float)

        try:
            pts = _load_route_points(cur, guid)
            if len(pts) < 2: pts = [start, end]
            vec, meta = encode_context_vector(idx, start, end, pts,
                                                start_radius=args.start_radius,
                                                end_radius=args.end_radius)
            vec_str = "[" + ",".join(f"{v:.8f}" for v in vec) + "]"

            cur.execute(
                '''INSERT INTO "TB_ROUTE_CONTEXT_VECTOR"
                   ("ROUTE_PATH_GUID","CONTEXT_VECTOR",
                    "START_META_JSON","END_META_JSON","TIER3_META_JSON")
                   VALUES (%s, %s::vector, %s::jsonb, %s::jsonb, %s::jsonb);''',
                (guid, vec_str,
                 json.dumps(meta["start"]),
                 json.dumps(meta["end"]),
                 json.dumps(meta["tier3"])))
            n_ok += 1
        except Exception as ex:
            conn.rollback()
            n_fail += 1
            if n_fail <= 3:
                print(f"  [warn] {guid[:8]} 실패: {ex}")

        if (i + 1) % 1000 == 0:
            conn.commit()
            print(f"  {i+1}/{len(routes)}, ok={n_ok}, fail={n_fail}, elapsed={time.time()-t0:.1f}s")

    conn.commit()
    elapsed = time.time() - t0
    print(f"\n완료 ({elapsed:.1f}s): ok={n_ok}, fail={n_fail}, avg={elapsed/max(n_ok,1)*1000:.1f}ms/경로")

    # 확인 쿼리
    cur.execute('SELECT COUNT(*) FROM "TB_ROUTE_CONTEXT_VECTOR";')
    total = cur.fetchone()[0]
    print(f"TB_ROUTE_CONTEXT_VECTOR 최종: {total}건")

    conn.close()


if __name__ == "__main__":
    main()
