import sys
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from RunContextABCampaign import stratified_select  # noqa: E402


class ContextAbCampaignTests(unittest.TestCase):
    def test_stratified_selection_round_robins_strata(self):
        candidates = []
        for index in range(3):
            candidates.append({
                "project_id": 1, "utility_group": "G", "utility": "U1",
                "distance_band": "SHORT", "order_key": f"a{index}", "id": f"a{index}",
            })
            candidates.append({
                "project_id": 2, "utility_group": "G", "utility": "U2",
                "distance_band": "LONG", "order_key": f"b{index}", "id": f"b{index}",
            })
        selected = stratified_select(candidates, 2)
        self.assertEqual({1, 2}, {row["project_id"] for row in selected})

    def test_stratified_selection_stops_at_available_count(self):
        candidate = {
            "project_id": 1, "utility_group": "G", "utility": "U",
            "distance_band": "MEDIUM", "order_key": "a",
        }
        self.assertEqual(1, len(stratified_select([candidate], 5)))


if __name__ == "__main__":
    unittest.main()
