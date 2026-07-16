from __future__ import annotations

import sys
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from ProfileUtilityPipeGroups import build_profile, normalize_equipment_key, normalize_size


class UtilityPipeGroupProfileTests(unittest.TestCase):
    def test_equipment_normalization_merges_trailing_delimiters(self):
        self.assertEqual("WTNHJ02", normalize_equipment_key(" WTNHJ02_ "))
        self.assertEqual("WTNHJ02", normalize_equipment_key("wtnhj02-"))

    def test_size_normalization(self):
        self.assertEqual("50A", normalize_size("050a"))
        self.assertEqual("65A", normalize_size(" 65 A "))
        self.assertEqual("UNKNOWN", normalize_size(None))

    def test_group_profile_counts_size_and_vector_coverage(self):
        rows = [
            self.row("r1", "WTNHJ02_", "50A"),
            self.row("r2", "WTNHJ02", "50A"),
            self.row("r3", "WTNHJ03", "50A"),
            self.row("r4", "WTNHJ03", "65A"),
            self.row("r5", "WTNHJ04", "50A"),
        ]
        profile = build_profile(
            rows,
            feature_guids={"r1", "r2", "r3"},
            context_guids={"r1", "r2"},
            geometry_guids={"r1", "r2", "r3", "r4"},
            min_members=2,
        )
        group = profile["group_summary"]
        self.assertEqual(3, group["all_group_count"])
        self.assertEqual(2, group["eligible_group_count"])
        self.assertEqual(1, group["single_member_group_count"])
        self.assertEqual(1, group["homogeneous_size_group_count"])
        self.assertEqual(1, group["mixed_size_group_count"])
        self.assertAlmostEqual(3 / 4, group["feature_member_coverage"])
        self.assertAlmostEqual(2 / 4, group["context_member_coverage"])
        self.assertEqual(1, profile["equipment_normalization"]["keys_with_multiple_raw_variants"])

    @staticmethod
    def row(guid: str, equipment: str, size: str) -> dict:
        return {
            "route_path_guid": guid,
            "process_name": "CLEAN",
            "equipment_tag": equipment,
            "equipment_name": "",
            "utility_group": "Exhaust",
            "utility": "ACID",
            "size": size,
            "sx": 0.0,
            "sy": 0.0,
            "sz": 0.0,
            "ex": 1.0,
            "ey": 1.0,
            "ez": 1.0,
        }


if __name__ == "__main__":
    unittest.main()
