import math
import sys
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from EvaluateContextTopK import (  # noqa: E402
    Candidate,
    Query,
    axis_label,
    build_query_vector30,
    evaluate_deployment_gate,
    score_candidate,
)


def vector_at(index: int) -> tuple[float, ...]:
    values = [0.0] * 30
    values[index] = 1.0
    return tuple(values)


class ContextTopKEvaluatorTests(unittest.TestCase):
    def make_query(self) -> Query:
        return Query(
            guid="query",
            process="P",
            equipment="E",
            utility_group="G",
            utility="U",
            size="20A",
            bay="CMP BAY",
            start=(0.0, 0.0, 0.0),
            end=(1000.0, 0.0, 0.0),
            pattern="H-R-H",
            actual_feature=vector_at(0),
            context=vector_at(12),
        )

    def make_candidate(self, context) -> Candidate:
        return Candidate(
            guid="candidate",
            process="P",
            equipment="E",
            utility_group="G",
            utility="U",
            size="20A",
            start=(0.0, 0.0, 0.0),
            end=(1000.0, 0.0, 0.0),
            pattern="H-R-H",
            feature=build_query_vector30((0.0, 0.0, 0.0), (1000.0, 0.0, 0.0)),
            context=context,
        )

    def test_query_vector_is_normalized(self):
        vector = build_query_vector30((0.0, 0.0, 0.0), (1000.0, 2000.0, 3000.0))
        self.assertEqual(30, len(vector))
        self.assertAlmostEqual(1.0, math.sqrt(sum(value * value for value in vector)), places=12)

    def test_missing_context_falls_back_to_baseline_score(self):
        query = self.make_query()
        query_vector = build_query_vector30(query.start, query.end)
        candidate = self.make_candidate(None)
        baseline = score_candidate(query, candidate, query_vector, None)
        with_context_enabled = score_candidate(query, candidate, query_vector, 0.20)
        self.assertAlmostEqual(baseline.score, with_context_enabled.score, places=12)
        self.assertIsNone(with_context_enabled.context_cosine)

    def test_indexed_context_uses_context_weight(self):
        query = self.make_query()
        query_vector = build_query_vector30(query.start, query.end)
        candidate = self.make_candidate(query.context)
        baseline = score_candidate(query, candidate, query_vector, None)
        with_context = score_candidate(query, candidate, query_vector, 0.20)
        self.assertAlmostEqual((0.8 * baseline.score) + 0.2, with_context.score, places=12)

    def test_axis_label_preserves_sign(self):
        vector = [0.0] * 30
        vector[2] = -0.8
        vector[4] = 0.7
        self.assertEqual("-z", axis_label(vector, 0))
        self.assertEqual("+y", axis_label(vector, 3))

    def test_deployment_gate_passes_healthy_report(self):
        report = {
            "method": {"default_context_weight": 0.10},
            "dataset": {"context_coverage": 1.0},
            "operational": {
                "candidate_context_coverage": 1.0,
                "baseline": {
                    "both_axes_at1": 0.18,
                    "pattern_at1": 0.12,
                    "feature_cosine_at_k": 0.275,
                },
                "context_default": {
                    "queries": 825,
                    "both_axes_at1": 0.21,
                    "pattern_at1": 0.14,
                    "feature_cosine_at_k": 0.249,
                },
            },
            "recommendation": {"context_weight": 0.10},
        }
        gate = evaluate_deployment_gate(report)
        self.assertEqual("PASS", gate["status"])
        self.assertEqual([], gate["failed_checks"])

    def test_deployment_gate_blocks_low_coverage(self):
        report = {
            "method": {"default_context_weight": 0.10},
            "dataset": {"context_coverage": 0.50},
            "operational": {
                "candidate_context_coverage": 0.50,
                "baseline": {
                    "both_axes_at1": 0.18,
                    "pattern_at1": 0.12,
                    "feature_cosine_at_k": 0.275,
                },
                "context_default": {
                    "queries": 825,
                    "both_axes_at1": 0.21,
                    "pattern_at1": 0.14,
                    "feature_cosine_at_k": 0.249,
                },
            },
            "recommendation": {"context_weight": 0.10},
        }
        gate = evaluate_deployment_gate(report)
        self.assertEqual("BLOCK", gate["status"])
        self.assertIn("context_coverage", gate["failed_checks"])
        self.assertIn("candidate_context_coverage", gate["failed_checks"])


if __name__ == "__main__":
    unittest.main()
