from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from BuildUtilityPipeGroupVectors import compute_groups
from utility_pipe_group_encoder import (
    VECTOR_DIM,
    build_arrangement,
    deterministic_members,
    encoder_config,
    make_group_vector_id,
    normalized_centroid,
    sha256_json,
    source_hash_payload,
)


def vector(axis: int) -> list[float]:
    result = [0.0] * VECTOR_DIM
    result[axis] = 1.0
    return result


def member(guid: str, offset: float, context_signature=("ctx-v1", "config-a")) -> dict:
    return {
        "route_path_guid": guid,
        "process_name": "CLEAN",
        "equipment_raw": "EQ_01_",
        "equipment_key": "EQ_01",
        "equipment_name": "EQ-FAMILY",
        "equipment_family_key": "EQ-FAMILY",
        "utility_group": "EXHAUST",
        "utility": "ACID",
        "size": "50A",
        "start_xyz": [offset, 0.0, 0.0],
        "end_xyz": [offset, 100.0, 50.0],
        "direction_pattern": "H-R-D",
        "total_length_mm": 150.0,
        "step_count": 3,
        "feature_vector": vector(int(offset) % 2),
        "context_vector": vector(2),
        "feature_provenance": "feature-v1@time",
        "context_provenance": "run-1",
        "context_signature": context_signature,
        "geometry_points": [[offset, 0.0, 0.0], [offset, 120.0, 50.0]],
    }


class UtilityPipeGroupEncoderTests(unittest.TestCase):
    def test_centroid_is_l2_normalized(self):
        result = normalized_centroid([vector(0), vector(1)])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(1.0, math.sqrt(sum(value * value for value in result)), places=12)
        self.assertAlmostEqual(math.sqrt(0.5), result[0], places=12)
        self.assertAlmostEqual(math.sqrt(0.5), result[1], places=12)

    def test_member_order_and_group_id_are_deterministic(self):
        rows = [member("route-b", 1.0), member("route-a", 0.0)]
        self.assertEqual(["route-a", "route-b"], [r["route_path_guid"] for r in deterministic_members(rows)])
        identity = {"project": "P", "revision": "R", "equipment": "E"}
        self.assertEqual(make_group_vector_id(identity), make_group_vector_id(dict(reversed(list(identity.items())))))

    def test_arrangement_is_independent_of_input_order(self):
        first = member("route-a", 0.0)
        second = member("route-b", 10.0)
        self.assertEqual(build_arrangement([first, second]), build_arrangement([second, first]))

    def test_source_hash_changes_when_vector_or_config_changes(self):
        identity = {"project": "P", "revision": "R", "equipment": "E"}
        rows = [member("route-a", 0.0), member("route-b", 1.0)]
        config_hash = sha256_json(encoder_config(2))
        original = sha256_json(source_hash_payload(identity, rows, config_hash))
        rows[0]["feature_vector"] = vector(5)
        changed_vector = sha256_json(source_hash_payload(identity, rows, config_hash))
        changed_config = sha256_json(source_hash_payload(identity, rows, sha256_json(encoder_config(3))))
        self.assertNotEqual(original, changed_vector)
        self.assertNotEqual(changed_vector, changed_config)

    def test_compute_groups_selects_one_context_contract(self):
        rows = [
            member("route-a", 0.0, ("ctx-v1", "config-a")),
            member("route-b", 1.0, ("ctx-v1", "config-a")),
            member("route-c", 2.0, ("ctx-v2", "config-b")),
        ]
        groups, diagnostics = compute_groups(rows, "DB:P", "snapshot:R", 2, "00000000-0000-0000-0000-000000000001")
        self.assertEqual(1, len(groups))
        self.assertEqual(3, groups[0]["member_count"])
        self.assertAlmostEqual(2 / 3, groups[0]["context_coverage"])
        self.assertEqual(("ctx-v1", "config-a"), groups[0]["context_signature"])
        self.assertEqual(
            {"encoder_version": "ctx-v1", "encoder_config_hash": "config-a"},
            groups[0]["encoder_config"]["context_contract"],
        )
        self.assertEqual(1, diagnostics["ready_group_count"])

    def test_compute_group_id_and_source_hash_ignore_input_order(self):
        rows = [member("route-c", 2.0), member("route-a", 0.0), member("route-b", 1.0)]
        first, _ = compute_groups(rows, "DB:P", "snapshot:R", 2, "00000000-0000-0000-0000-000000000001")
        second, _ = compute_groups(list(reversed(rows)), "DB:P", "snapshot:R", 2, "00000000-0000-0000-0000-000000000002")
        self.assertEqual(first[0]["group_vector_id"], second[0]["group_vector_id"])
        self.assertEqual(first[0]["source_hash"], second[0]["source_hash"])
        self.assertEqual(first[0]["member_guids"], second[0]["member_guids"])


if __name__ == "__main__":
    unittest.main()
