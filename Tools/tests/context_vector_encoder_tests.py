import math
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context_vector_encoder import (  # noqa: E402
    CONTEXT_VECTOR_DIM,
    Obstacle,
    ObstacleIndex,
    MergedObstacleIndex,
    encode_context_vector,
    line_grid_cells,
    point_aabb_distance,
)


class ContextVectorEncoderTests(unittest.TestCase):
    def test_long_obstacle_is_found_by_surface_not_center(self):
        beam = Obstacle("beam-1", "BEAM", (400.0, -100.0, -100.0), (8400.0, 100.0, 100.0))
        index = ObstacleIndex([beam])
        found = index.query_radius((0.0, 0.0, 0.0), 500.0, "BEAM")
        self.assertEqual(1, len(found))
        self.assertAlmostEqual(400.0, found[0].distance)

    def test_point_inside_aabb_has_zero_distance(self):
        column = Obstacle("column-1", "COLUMN", (-100.0, -100.0, -100.0), (100.0, 100.0, 100.0))
        distance, closest = point_aabb_distance((0.0, 0.0, 0.0), column)
        self.assertEqual(0.0, distance)
        self.assertEqual((0.0, 0.0, 0.0), closest)

    def test_near_and_mid_shells_are_counted_separately(self):
        obstacles = [
            Obstacle("c-near", "COLUMN", (400.0, 0.0, 0.0), (450.0, 50.0, 50.0)),
            Obstacle("c-mid", "COLUMN", (700.0, 0.0, 0.0), (750.0, 50.0, 50.0)),
        ]
        vector, meta = encode_context_vector(ObstacleIndex(obstacles), (0, 0, 0), (0, 2000, 0))
        self.assertEqual(CONTEXT_VECTOR_DIM, len(vector))
        self.assertEqual(1, meta["start"]["column_near_count"])
        self.assertEqual(1, meta["start"]["column_mid_count"])
        self.assertAlmostEqual(1.0, math.sqrt(sum(value * value for value in vector)))

    def test_grid_traversal_is_ordered_and_deterministic(self):
        cells = line_grid_cells((-100.0, -100.0, 0.0), (2100.0, 2100.0, 0.0))
        self.assertEqual([(-1, -1), (0, 0), (1, 1), (2, 2)], cells)

    def test_invalid_non_finite_coordinate_is_rejected(self):
        with self.assertRaises(ValueError):
            encode_context_vector(ObstacleIndex([]), (math.nan, 0, 0), (1, 2, 3))

    def test_empty_environment_uses_free_space_dimensions_not_zero_vector(self):
        vector, meta = encode_context_vector(ObstacleIndex([]), (0, 0, 0), (0, 0, 0))
        self.assertAlmostEqual(1.0, vector[12] * math.sqrt(2.0))
        self.assertAlmostEqual(1.0, vector[25] * math.sqrt(2.0))
        self.assertAlmostEqual(1.0, math.sqrt(sum(value * value for value in vector)))
        self.assertTrue(meta["start"]["empty_within_1000"])

    def test_merged_scope_includes_common_and_matching_bay_without_duplicates(self):
        common = Obstacle("common", "COLUMN", (100, 0, 0), (200, 100, 100))
        scoped = Obstacle("scoped", "COLUMN", (300, 0, 0), (400, 100, 100))
        index = MergedObstacleIndex(ObstacleIndex([common]), ObstacleIndex([common, scoped]))
        found = index.query_radius((0, 0, 0), 500, "COLUMN")
        self.assertEqual(["common", "scoped"], [item.obstacle.obstacle_id for item in found])


if __name__ == "__main__":
    unittest.main()
