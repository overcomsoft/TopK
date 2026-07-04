"""TB_ROUTE_DESIGN_GROUP 일괄 빌드 CLI.

사용법:
  python BuildDesignGroups.py
  python BuildDesignGroups.py --min-members 3
"""
from __future__ import annotations

import argparse
import time

import psycopg2

from AutoRouteDesigner.group_builder import build_design_groups, GroupBuildConfig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--dbname", default="AUTOROUTINGV7")
    ap.add_argument("--user", default="postgres")
    ap.add_argument("--password", default="dinno")
    ap.add_argument("--min-members", type=int, default=3)
    args = ap.parse_args()

    print(f"[1/2] DB 연결: {args.host}:{args.port}/{args.dbname}")
    conn = psycopg2.connect(host=args.host, port=args.port, dbname=args.dbname,
                            user=args.user, password=args.password)

    print(f"[2/2] build_design_groups(min_members={args.min_members})")
    t0 = time.time()
    n = build_design_groups(conn, GroupBuildConfig(min_member_count=args.min_members))
    print(f"완료: {n}개 그룹 생성, {time.time()-t0:.2f}초")

    # 요약
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*), SUM("MEMBER_COUNT") FROM "TB_ROUTE_DESIGN_GROUP";')
    gc, mc = cur.fetchone()
    print(f"TB_ROUTE_DESIGN_GROUP: {gc}개 그룹, 멤버 경로 합계 {mc}건")

    cur.execute('''SELECT "PROCESS_NAME","EQUIPMENT_NAME","UTILITY","MEMBER_COUNT"
                   FROM "TB_ROUTE_DESIGN_GROUP" ORDER BY "MEMBER_COUNT" DESC LIMIT 10;''')
    print("\n상위 10개 그룹:")
    for r in cur.fetchall():
        print(f"  {(r[0] or '')[:8]:8s} / {(r[1] or '')[:30]:30s} / {(r[2] or '')[:15]:15s} : {r[3]}건")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
