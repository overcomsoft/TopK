"""
Context corridor 정책과 비용계수 조합을 동일 campaign 조건으로 반복 실행한다.

실행 방법(PowerShell)
---------------------
1. 9개 조합 명령 확인: python Tools/RunContextABMatrix.py --config Tools/tools.settings.json
2. 실제 실행: 위 명령에 ``--execute --target-pairs 30`` 추가

전체 흐름도
-----------
  policies(ranked, rank1, union) x factors(0.5, 1.0, 2.0)
      -> 조합별 고유 EXPERIMENT_ID와 출력 경로 생성
      -> RunContextABCampaign.py 호출
      -> 조합별 독립 DB 로그/JSON/Markdown 보고서

``POLICIES``는 여러 Top-K 경로를 corridor로 합치는 방식이고, ``FACTORS``는 corridor cell의
탐색비용 배율이다. 같은 표본 조건을 유지해 정책 자체의 영향을 비교한다.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


POLICIES = ("ranked", "rank1", "union")
FACTORS = (0.5, 1.0, 2.0)


def experiment_id(prefix: str, policy: str, factor: float) -> str:
    """정책/비용계수를 파일명과 DB key에 안전한 결정적 experiment ID로 만든다."""
    factor_code = f"{factor:.2f}".replace(".", "")
    return f"{prefix}-{policy}-c{factor_code}"


def build_command(args, policy: str, factor: float) -> list[str]:
    """한 matrix cell을 실행할 campaign subprocess 인자 목록을 구성한다."""
    exp = experiment_id(args.experiment_prefix, policy, factor)
    command = [
        sys.executable, "Tools/RunContextABCampaign.py",
        "--config", args.config,
        "--target-pairs", str(args.target_pairs),
        "--batch-size", str(args.batch_size),
        "--cell-mm", str(args.cell_mm),
        "--k", str(args.k),
        "--experiment-id", exp,
        "--corridor-policy", policy,
        "--corridor-cost-factor", str(factor),
        "--rank-penalty-factors", args.rank_penalty_factors,
        "--plan-json", f"data/output/{exp}_plan.json",
        "--report-json", f"data/output/{exp}_report.json",
        "--report-md", f"Docs/{exp}_report.md",
    ]
    if args.model_revision_key:
        command.extend(["--model-revision-key", args.model_revision_key])
    if args.exclude_reference_experiment:
        command.extend(["--exclude-reference-experiment", args.exclude_reference_experiment])
    if args.execute:
        command.append("--execute")
    return command


def main() -> int:
    """정책×비용계수 Cartesian product를 순회하며 plan 또는 실제 campaign을 실행한다."""
    parser = argparse.ArgumentParser(description="Run rank-cut x corridor-cost A/B matrix")
    parser.add_argument("--config", default="Tools/tools.settings.json")
    parser.add_argument("--experiment-prefix", default="context-v3-corridor")
    parser.add_argument("--target-pairs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--cell-mm", type=float, default=100.0)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--model-revision-key", default="")
    parser.add_argument("--rank-penalty-factors", default="0,0.5,0.75")
    parser.add_argument("--exclude-reference-experiment", default="")
    parser.add_argument("--policies", default=",".join(POLICIES))
    parser.add_argument("--factors", default=",".join(str(v) for v in FACTORS))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()

    policies = [value.strip() for value in args.policies.split(",") if value.strip()]
    factors = [float(value) for value in args.factors.split(",") if value.strip()]
    invalid = sorted(set(policies) - set(POLICIES))
    if invalid or any(value < 0 for value in factors):
        parser.error(f"invalid policies={invalid} or negative factor")

    root = Path(__file__).resolve().parents[1]
    failures = []
    for policy in policies:
        for factor in factors:
            command = build_command(args, policy, factor)
            print(f"Matrix: {experiment_id(args.experiment_prefix, policy, factor)}")
            result = subprocess.run(command, cwd=root, check=False)
            if result.returncode:
                failures.append((policy, factor, result.returncode))
                if not args.continue_on_error:
                    break
        if failures and not args.continue_on_error:
            break
    if failures:
        print(f"FAILED: {failures}", file=sys.stderr)
        return 1
    print(f"Matrix complete: {len(policies) * len(factors)} experiments; execute={args.execute}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
