"""TB_ROUTE_SEGMENT_TEMPLATE 일괄 빌드 CLI.

사용법:
  python BuildSegmentTemplates.py
  python BuildSegmentTemplates.py --role A_EQUIP_STUB
"""
from __future__ import annotations

import argparse
import time

import psycopg2

from AutoRouteDesigner.template_builder import build_all_templates, TemplateBuildConfig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--dbname", default="AUTOROUTINGV7")
    ap.add_argument("--user", default="postgres")
    ap.add_argument("--password", default="dinno")
    ap.add_argument("--role", choices=["A_EQUIP_STUB","B_BRIDGE","C_DUCT_ENTRY"], default=None)
    args = ap.parse_args()

    print(f"[1/2] DB 연결: {args.host}:{args.port}/{args.dbname}")
    conn = psycopg2.connect(host=args.host, port=args.port, dbname=args.dbname,
                            user=args.user, password=args.password)

    cfg = TemplateBuildConfig(role_filter=[args.role] if args.role else None)
    print(f"[2/2] build_all_templates(role_filter={cfg.role_filter})")
    t0 = time.time()
    summary = build_all_templates(conn, cfg)
    elapsed = time.time() - t0
    print(f"완료 ({elapsed:.1f}s): {summary}")

    cur = conn.cursor()
    cur.execute('''SELECT "SEGMENT_ROLE", COUNT(*) FROM "TB_ROUTE_SEGMENT_TEMPLATE" GROUP BY 1 ORDER BY 1;''')
    print("\nRole별 템플릿 수:")
    for r in cur.fetchall():
        print(f"  {r[0]:15s} {r[1]}")

    cur.execute('''SELECT "DUCT_OR_TARGET_TYPE", COUNT(*) FROM "TB_ROUTE_SEGMENT_TEMPLATE"
                   WHERE "SEGMENT_ROLE"='C_DUCT_ENTRY' GROUP BY 1 ORDER BY 2 DESC LIMIT 10;''')
    print("\nC_DUCT_ENTRY 타입별 상위 10종:")
    for r in cur.fetchall():
        print(f"  {(r[0] or '')[:20]:20s} {r[1]}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
