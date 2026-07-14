import argparse
import sys
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from RunContextABMatrix import build_command, experiment_id  # noqa: E402


class ContextAbMatrixTests(unittest.TestCase):
    def test_experiment_id_is_stable(self):
        self.assertEqual("ctx-rank1-c050", experiment_id("ctx", "rank1", 0.5))
        self.assertEqual("ctx-union-c200", experiment_id("ctx", "union", 2.0))

    def test_build_command_propagates_policy_and_factor(self):
        args = argparse.Namespace(
            config="cfg.json", target_pairs=3, batch_size=1, cell_mm=100.0, k=3,
            experiment_prefix="ctx", model_revision_key="rev1",
            exclude_reference_experiment="old", execute=True,
            rank_penalty_factors="0,0.5,0.75",
        )
        command = build_command(args, "top2", 1.0)
        self.assertIn("ctx-top2-c100", command)
        self.assertEqual("top2", command[command.index("--corridor-policy") + 1])
        self.assertEqual("1.0", command[command.index("--corridor-cost-factor") + 1])
        self.assertIn("--execute", command)


if __name__ == "__main__":
    unittest.main()
