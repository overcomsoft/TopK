from __future__ import annotations

import sys
import unittest
import uuid
from collections import Counter
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from geometry_ip_restore import RestoredVertex, restore_polyline_ip
from ExtractBendFeaturePoints import (
    classify_transition,
    classify_zone,
    compute_rel_position_bucket,
    extract_candidates,
    aggregate_patterns,
    BendCandidate,
    ObstacleSpatialIndex,
    RouteInput,
)
from PathSegmenter import segment_route


class ClassifyTransitionTests(unittest.TestCase):
    def test_vertical_to_horizontal(self):
        self.assertEqual(classify_transition(2, 0), "V_TO_H")

    def test_horizontal_to_vertical(self):
        self.assertEqual(classify_transition(1, 2), "H_TO_V")

    def test_horizontal_to_horizontal(self):
        self.assertEqual(classify_transition(0, 1), "H_TO_H")

    def test_vertical_to_vertical(self):
        self.assertEqual(classify_transition(2, 2), "V_TO_V")


class ClassifyZoneTests(unittest.TestCase):
    def test_boundaries(self):
        # start_idx=2, end_idx=5
        self.assertEqual(classify_zone(0, 2, 5), "START_STUB")
        self.assertEqual(classify_zone(2, 2, 5), "START_STUB")
        self.assertEqual(classify_zone(3, 2, 5), "MIDDLE_TRUNK")
        self.assertEqual(classify_zone(5, 2, 5), "END_STUB")
        self.assertEqual(classify_zone(9, 2, 5), "END_STUB")


class RelPositionBucketTests(unittest.TestCase):
    def test_start_and_end_of_zone(self):
        zone_pts = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (200.0, 0.0, 0.0)]
        # local_idx=0 (zone 시작) -> 0.0
        bucket_start = compute_rel_position_bucket("MIDDLE_TRUNK", 10, 10, 20, [], zone_pts, [])
        self.assertEqual(bucket_start, 0.0)
        # local_idx=2 (zone 끝) -> 1.0
        bucket_end = compute_rel_position_bucket("MIDDLE_TRUNK", 12, 10, 20, [], zone_pts, [])
        self.assertEqual(bucket_end, 1.0)

    def test_bucket_is_rounded_to_tenths(self):
        zone_pts = [(0.0, 0.0, 0.0), (33.0, 0.0, 0.0), (100.0, 0.0, 0.0)]
        bucket = compute_rel_position_bucket("MIDDLE_TRUNK", 11, 10, 20, [], zone_pts, [])
        self.assertAlmostEqual(bucket, 0.3, places=6)


class RestorePolylineIpTests(unittest.TestCase):
    def test_elbow_replaced_with_orthogonal_intersection(self):
        # X축으로 진행하다 90도 엘보로 Y축으로 꺾이는 전형적인 케이스.
        # 개선안 A 예시(Docs/Elbow_Geometry_Analysis_Report.md 60행): X축 진입 후 Y축 진출 시
        # P_IP = (P_out.X, P_in.Y, P_in.Z) 가 되어야 한다.
        raw_segments = [
            {"from": (0.0, 0.0, 0.0), "to": (1000.0, 0.0, 0.0), "type": "PIPE"},
            {"from": (1000.0, 0.0, 0.0), "to": (1000.0, 100.0, 0.0), "type": "ELBOW"},
            {"from": (1000.0, 100.0, 0.0), "to": (1000.0, 1000.0, 0.0), "type": "PIPE"},
        ]
        restored = restore_polyline_ip(raw_segments)
        points = [v.point for v in restored]
        self.assertEqual(len(points), 3)
        ip = points[1]
        self.assertAlmostEqual(ip[0], 1000.0, places=3)
        self.assertAlmostEqual(ip[1], 0.0, places=3)
        self.assertAlmostEqual(ip[2], 0.0, places=3)
        self.assertTrue(restored[1].is_elbow_restored_ip)
        self.assertIsNotNone(restored[1].skew_dist_mm)
        self.assertLess(restored[1].skew_dist_mm, 1e-6)

    def test_non_elbow_segments_pass_through_unchanged(self):
        raw_segments = [
            {"from": (0.0, 0.0, 0.0), "to": (500.0, 0.0, 0.0), "type": "PIPE"},
            {"from": (500.0, 0.0, 0.0), "to": (500.0, 500.0, 0.0), "type": "PIPE"},
        ]
        restored = restore_polyline_ip(raw_segments)
        points = [v.point for v in restored]
        self.assertEqual(points, [(0.0, 0.0, 0.0), (500.0, 0.0, 0.0), (500.0, 500.0, 0.0)])
        self.assertTrue(all(not v.is_elbow_restored_ip for v in restored))

    def test_parallel_directions_are_not_restored(self):
        # 전후 직관 방향이 평행이면 교차점을 정의할 수 없으므로 원본 P_in/P_out을 유지해야 한다.
        raw_segments = [
            {"from": (0.0, 0.0, 0.0), "to": (1000.0, 0.0, 0.0), "type": "PIPE"},
            {"from": (1000.0, 0.0, 0.0), "to": (1010.0, 0.0, 0.0), "type": "ELBOW"},
            {"from": (1010.0, 0.0, 0.0), "to": (2000.0, 0.0, 0.0), "type": "PIPE"},
        ]
        restored = restore_polyline_ip(raw_segments)
        self.assertFalse(any(v.is_elbow_restored_ip for v in restored))


class ExtractCandidatesJitterTests(unittest.TestCase):
    def _route(self) -> RouteInput:
        return RouteInput(
            guid="ROUTE-1", project_scope_key="SCOPE-1", model_revision_key="REV-1",
            equipment_key="EQ01", utility_group="WATER", utility="PCWS",
            size="50A", raw_segments=[],
        )

    def test_bends_shorter_than_jitter_threshold_are_skipped(self):
        # 25mm 짧은 지그재그는 50mm 지터 필터에 걸려 후보에서 제외되어야 한다.
        points = [
            (0.0, 0.0, 1000.0),
            (1000.0, 0.0, 1000.0),
            (1000.0, 25.0, 1000.0),
            (2000.0, 25.0, 1000.0),
        ]
        start_pts, middle_pts, end_pts, _sfp, _efp, _entry = segment_route(points)
        start_idx = len(start_pts) - 1
        end_idx = len(points) - len(end_pts)
        restored = [RestoredVertex(p, False, None) for p in points]
        candidates = extract_candidates(self._route(), points, restored, start_idx, end_idx, start_pts, middle_pts, end_pts)
        self.assertEqual(candidates, [])

    def test_real_bend_above_threshold_is_detected(self):
        points = [
            (0.0, 0.0, 1000.0),
            (1000.0, 0.0, 1000.0),
            (1000.0, 500.0, 1000.0),
            (2000.0, 500.0, 1000.0),
        ]
        start_pts, middle_pts, end_pts, _sfp, _efp, _entry = segment_route(points)
        start_idx = len(start_pts) - 1
        end_idx = len(points) - len(end_pts)
        restored = [RestoredVertex(p, False, None) for p in points]
        candidates = extract_candidates(self._route(), points, restored, start_idx, end_idx, start_pts, middle_pts, end_pts)
        self.assertGreaterEqual(len(candidates), 1)
        self.assertIn(candidates[0].transition_type, {"H_TO_H", "V_TO_H", "H_TO_V"})

    def test_consecutive_horizontal_bends_are_tagged(self):
        points = [
            (0.0, 0.0, 1000.0), (1000.0, 0.0, 1000.0),
            (1000.0, 500.0, 1000.0), (2000.0, 500.0, 1000.0),
            (2000.0, 1000.0, 1000.0),
        ]
        start_pts, middle_pts, end_pts, *_ = segment_route(points)
        restored = [RestoredVertex(p, False, None) for p in points]
        candidates = extract_candidates(
            self._route(), points, restored, len(start_pts) - 1, len(points) - len(end_pts),
            start_pts, middle_pts, end_pts,
        )
        tagged = [c for c in candidates if c.is_horizontal_sequence]
        self.assertGreaterEqual(len(tagged), 2)


class AggregatePatternTests(unittest.TestCase):
    def _candidate(self, route_guid: str, ordinal: int) -> BendCandidate:
        return BendCandidate(
            route_path_guid=route_guid, project_scope_key="SCOPE-1", model_revision_key="REV-1",
            equipment_key="EQ01", utility_group="WATER", utility="PCWS",
            ordinal_from_start=ordinal, ordinal_from_end=1, segment_zone="MIDDLE_TRUNK",
            rel_position_bucket=0.5, transition_type="H_TO_H", axis_before="+x", axis_after="+y",
            axis1=0, axis2=1, point=(float(ordinal), 0.0, 0.0),
            adjacent_before=(0.0, 0.0, 0.0), adjacent_after=(10.0, 0.0, 0.0),
            is_elbow_restored_ip=False, ip_restore_skew_dist_mm=None,
            anchor_rel_position=(0.5, 0.5, 0.5), cause="UNKNOWN",
        )

    def test_sample_count_is_distinct_routes_not_bend_instances(self):
        candidates = [self._candidate("R1", 1), self._candidate("R1", 2), self._candidate("R2", 3)]
        patterns = aggregate_patterns(
            candidates, Counter({("EQ01", "WATER", "PCWS"): 2}), 2, str(uuid.uuid4()),
            "SCOPE-1", "REV-1",
        )
        self.assertEqual(len(patterns), 1)
        self.assertEqual(patterns[0].sample_count, 2)
        self.assertEqual(patterns[0].bend_instance_count, 3)
        self.assertEqual(patterns[0].frequency_score, 1.0)
        self.assertEqual(patterns[0].position_consistency, 1.0)
        self.assertEqual(patterns[0].project_scope_key, "SCOPE-1")
        self.assertEqual(patterns[0].model_revision_key, "REV-1")


class ObstacleSpatialIndexTests(unittest.TestCase):
    @staticmethod
    def _obstacle(name, mn, mx):
        return {
            "name": name,
            "minx": mn[0], "miny": mn[1], "minz": mn[2],
            "maxx": mx[0], "maxy": mx[1], "maxz": mx[2],
        }

    def test_query_returns_nearby_and_excludes_far_obstacles(self):
        obstacles = [
            self._obstacle("near", (900.0, -100.0, -100.0), (1100.0, 100.0, 100.0)),
            self._obstacle("far", (100000.0, 100000.0, 0.0), (101000.0, 101000.0, 1000.0)),
        ]
        index = ObstacleSpatialIndex(obstacles, cell_mm=1000.0)
        found = index.query_segments(
            (0.0, 0.0, 0.0), (1000.0, 0.0, 0.0), (1000.0, 1000.0, 0.0), 600.0
        )
        self.assertEqual({o["name"] for o in found}, {"near"})

    def test_large_obstacle_overflow_is_not_missed(self):
        large = self._obstacle("large", (-100000.0, -100000.0, -100000.0), (100000.0, 100000.0, 100000.0))
        index = ObstacleSpatialIndex([large], cell_mm=1000.0)
        found = index.query_segments((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0), 10.0)
        self.assertEqual(found, [large])


if __name__ == "__main__":
    unittest.main()
