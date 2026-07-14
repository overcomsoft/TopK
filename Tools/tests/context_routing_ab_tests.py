import sys
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from AnalyzeContextRoutingAB import summarize  # noqa: E402


def row(request, arm, success, length=1000.0, bends=2, elapsed=10.0, topk=None):
    return {
        "REQUEST_KEY": request,
        "ARM": arm,
        "ROUTE_SUCCESS": success,
        "ROUTE_LENGTH_MM": length,
        "ROUTE_BEND_COUNT": bends,
        "ROUTE_ELAPSED_MS": elapsed,
        "EXPANDED_NODES": 100,
        "CONTEXT_COVERAGE": 1.0 if arm == "CONTEXT_V3" else 0.0,
        "CONTEXT_FALLBACK_COUNT": 0,
        "ROUTE_FAIL_REASON": None if success else "NO_PATH",
        "TOPK_ROUTE_GUIDS": topk or [],
        "RUN_ID": "run-1",
        "CONTEXT_SNAPSHOT_HASH": "snapshot-1",
        "CONTEXT_SCOPE_STATUS": "GLOBAL_FALLBACK_NO_COMMON_KEY",
        "CONTEXT_BUILD_RUN_ID": "build-1",
        "CONTEXT_ENCODER_VERSION": "topkgen-v3",
        "CONTEXT_ENCODER_CONFIG_HASH": "config-1",
        "CONTEXT_PROVENANCE_CONSISTENT": True,
    }


class ContextRoutingAbTests(unittest.TestCase):
    def test_pairs_same_request_and_counts_context_success_gain(self):
        rows = [
            row("a", "BASELINE_TOPK", False),
            row("a", "CONTEXT_V3", True),
            row("b", "BASELINE_TOPK", True, length=1200),
            row("b", "CONTEXT_V3", True, length=1000),
        ]
        report = summarize(rows, "experiment")
        self.assertEqual(2, report["paired"]["requests"])
        self.assertEqual(1, report["paired"]["context_only_success"])
        self.assertEqual(1, report["paired"]["context_success_net"])
        self.assertAlmostEqual(-200.0, report["paired"]["avg_context_minus_baseline_length_mm"])
        self.assertEqual("COLLECT_MORE", report["decision"])

    def test_unpaired_rows_do_not_enter_pair_metrics(self):
        report = summarize([row("a", "CONTEXT_V3", True)], "experiment")
        self.assertEqual(0, report["paired"]["requests"])
        self.assertFalse(report["ready_for_decision"])

    def test_reports_changed_topk_and_overlap(self):
        report = summarize([
            row("a", "BASELINE_TOPK", True, topk=["1", "2", "3"]),
            row("a", "CONTEXT_V3", True, topk=["1", "4", "3"]),
        ], "experiment")
        self.assertEqual(1, report["paired"]["topk_changed_pairs"])
        self.assertAlmostEqual(0.5, report["paired"]["avg_topk_jaccard_overlap"])

    def test_blocks_mixed_snapshot_pair(self):
        baseline = row("a", "BASELINE_TOPK", True)
        context = row("a", "CONTEXT_V3", True)
        context["CONTEXT_SNAPSHOT_HASH"] = "snapshot-2"
        report = summarize([baseline, context], "experiment")
        self.assertEqual(0, report["paired"]["requests"])
        self.assertEqual(1, report["provenance"]["mismatched_pairs"])
        self.assertEqual("BLOCK_PROVENANCE_MISMATCH", report["decision"])


if __name__ == "__main__":
    unittest.main()
