"""
VectorDBGen에서 호출할 수 있는 Context Vector 전체 재생성 호환 진입점.

실행 방법
---------
- 전체 저장: ``python Tools/BuildContextVectors.py --config Tools/tools.settings.json``
- 일부 계산 확인: ``python Tools/BuildContextVectors.py --config Tools/tools.settings.json --dry-run --limit 10``

흐름: 설정/DB 연결 -> schema 확인 -> 공용 extractor/encoder 호출 -> 선택적 limit -> DB upsert.
실제 계산은 중복 구현하지 않고 ``ExtractObstacleContextVector``와
``context_vector_encoder``를 사용하므로 CLI와 GUI의 벡터 결과가 동일하다.
``--limit`` 저장은 전체 테이블을 불완전하게 만들 수 있어 dry-run에서만 허용한다.
"""
from __future__ import annotations

import argparse
import sys

from ExtractObstacleContextVector import (
    create_schema,
    extract_context_vectors,
    open_connection,
    save_context_vectors,
)
from context_vector_encoder import ENCODER_VERSION, MID_RADIUS_MM, NEAR_RADIUS_MM
import tool_config


def main() -> int:
    """호환 CLI 인자를 검증하고 공용 Context 생성 함수를 순서대로 호출한다."""
    parser = argparse.ArgumentParser(description="Build TB_ROUTE_CONTEXT_VECTOR")
    tool_config.add_common_args(parser)
    parser.add_argument("--limit", type=int, default=None, help="Development-only route limit")
    parser.add_argument("--dry-run", action="store_true", help="Calculate without changing the DB")
    args = parser.parse_args()

    if args.limit is not None:
        if args.limit <= 0:
            parser.error("--limit must be greater than zero")
        if not args.dry_run:
            parser.error(
                "--limit is only allowed with --dry-run because a saved build "
                "replaces the complete context-vector table"
            )

    runtime = tool_config.resolve_runtime(args)
    tool_config.print_runtime(runtime)
    print(
        f"Context encoder={ENCODER_VERSION}, shells=0..{NEAR_RADIUS_MM:g}mm/"
        f"{NEAR_RADIUS_MM:g}..{MID_RADIUS_MM:g}mm"
    )
    conn = open_connection(runtime.conninfo)
    try:
        if not args.dry_run:
            create_schema(conn)
        rows = extract_context_vectors(conn, dry_run=args.dry_run)
        if args.limit is not None:
            rows = rows[: args.limit]
        if not args.dry_run:
            save_context_vectors(conn, rows)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
