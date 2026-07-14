import sys
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from ExtractObstacleContextVector import _obstacle_snapshot_hash, _validate_scope_keys
from context_vector_encoder import Obstacle


class ContextVectorProvenanceTests(unittest.TestCase):
    def test_snapshot_hash_is_independent_of_query_order(self):
        first = Obstacle("COLUMN|a|0|0|0|1|1|1", "COLUMN", (0, 0, 0), (1, 1, 1))
        second = Obstacle("BEAM|b|2|2|2|3|3|3", "BEAM", (2, 2, 2), (3, 3, 3))
        self.assertEqual(
            _obstacle_snapshot_hash([first, second]),
            _obstacle_snapshot_hash([second, first]),
        )

    def test_snapshot_hash_changes_with_geometry_identity(self):
        before = Obstacle("COLUMN|a|0|0|0|1|1|1", "COLUMN", (0, 0, 0), (1, 1, 1))
        after = Obstacle("COLUMN|a|0|0|0|2|1|1", "COLUMN", (0, 0, 0), (2, 1, 1))
        self.assertNotEqual(_obstacle_snapshot_hash([before]), _obstacle_snapshot_hash([after]))

    def test_scope_keys_are_all_or_nothing(self):
        self.assertEqual(_validate_scope_keys(" project ", " rev "), ("project", "rev"))
        self.assertEqual(_validate_scope_keys("", ""), ("", ""))
        with self.assertRaises(ValueError):
            _validate_scope_keys("project", "")


if __name__ == "__main__":
    unittest.main()
