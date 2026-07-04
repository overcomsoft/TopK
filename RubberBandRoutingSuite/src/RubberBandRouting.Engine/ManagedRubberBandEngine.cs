using System;
using System.Collections.Generic;
using System.Linq;

namespace RubberBandRouting.Engine;

public sealed class ManagedRubberBandEngine
{
    private const double Epsilon = 1e-6;

    public RubberBandResult Route(
        Vec3 start,
        Vec3 end,
        IEnumerable<Aabb> obstacles,
        IEnumerable<Vec3>? featureWaypoints = null,
        RubberBandOptions? options = null)
    {
        options ??= new RubberBandOptions();
        var obstacleList = obstacles?.ToList() ?? new List<Aabb>();
        var features = featureWaypoints?.ToList() ?? new List<Vec3>();

        var result = new RubberBandResult();

        var step1Segments = MakeRubberLineSegments(new[] { start, end });
        result.Steps.Add(MakeStep(1, "Initial straight rubber tension", step1Segments));

        var snappedPoints = BuildSnappedPointList(start, end, features, options.SnapTolerance);
        var step2Segments = MakeOrthogonalSegments(snappedPoints);
        var step2 = MakeStep(2, $"Waypoint skeleton ({snappedPoints.Count - 2} ordered waypoints)", step2Segments);
        foreach (var wp in snappedPoints.Skip(1).Take(Math.Max(0, snappedPoints.Count - 2))) step2.Waypoints.Add(wp);
        result.Steps.Add(step2);

        var step3Segments = RouteOrthogonalAStarViaWaypoints(snappedPoints, obstacleList, options, out var fallbackCount);
        var step3 = MakeStep(3, fallbackCount == 0 ? "Orthogonal A* waypoint routing" : $"Orthogonal A* waypoint routing ({fallbackCount} fallback legs)", step3Segments);
        result.Steps.Add(step3);

        result.FinalSegments.AddRange(step3.Segments);
        foreach (var pipe in DistributePipes(step3.Segments, options)) result.PipePaths.Add(pipe);

        var issues = Validate(step3.Segments, obstacleList, options);
        foreach (var issue in issues) result.ValidationIssues.Add(issue);

        return new RubberBandResultBuilder(result)
        {
            TotalLength = step3.Segments.Sum(s => s.Length),
            VerticalBends = CountVerticalBends(step3.Segments),
            IsValid = issues.Count == 0
        }.Build();
    }

    private static List<Vec3> BuildSnappedPointList(Vec3 start, Vec3 end, List<Vec3> features, double tolerance)
    {
        var route = end - start;
        var len = route.Length;
        if (len < Epsilon || features.Count == 0) return new List<Vec3> { start, end };

        var points = new List<Vec3> { start };
        var maxDetour = Math.Max(len * 2.5, 10000.0);
        foreach (var feature in features)
        {
            if ((feature - start).Length <= tolerance || (feature - end).Length <= tolerance) continue;
            if (DistancePointToSegment(feature, start, end) > maxDetour) continue;
            if (points.Count == 0 || (points[^1] - feature).Length > Math.Max(tolerance, 100.0)) points.Add(feature);
        }

        if ((points[^1] - end).Length > tolerance) points.Add(end);
        else points[^1] = end;
        return points;
    }

    private static double DistancePointToSegment(Vec3 point, Vec3 a, Vec3 b)
    {
        var ab = b - a;
        var len2 = Vec3.Dot(ab, ab);
        if (len2 <= Epsilon) return (point - a).Length;
        var t = Math.Clamp(Vec3.Dot(point - a, ab) / len2, 0.0, 1.0);
        var projection = a + ab * t;
        return (point - projection).Length;
    }

    private static List<RouteSegment> RouteOrthogonalAStarViaWaypoints(IReadOnlyList<Vec3> points, List<Aabb> obstacles, RubberBandOptions options, out int fallbackCount)
    {
        fallbackCount = 0;
        var route = new List<RouteSegment>();
        for (var i = 0; i < points.Count - 1; i++)
        {
            var leg = RouteOrthogonalAStarLeg(points[i], points[i + 1], obstacles, options);
            if (leg.Count == 0)
            {
                fallbackCount++;
                leg = MakeOrthogonalSegments(new[] { points[i], points[i + 1] });
            }
            AppendMerged(route, leg);
        }
        return route;
    }

    private static List<RouteSegment> RouteOrthogonalAStarLeg(Vec3 start, Vec3 end, List<Aabb> obstacles, RubberBandOptions options)
    {
        if ((end - start).Length <= 1e-3) return new List<RouteSegment>();
        var relevant = RelevantObstacles(start, end, obstacles, options).ToList();
        var (xs, ys, zs) = BuildAStarLines(start, end, relevant, options);
        var startNode = new GridNode(IndexOf(xs, start.X), IndexOf(ys, start.Y), IndexOf(zs, start.Z));
        var endNode = new GridNode(IndexOf(xs, end.X), IndexOf(ys, end.Y), IndexOf(zs, end.Z));

        var open = new PriorityQueue<GridNode, double>();
        var cameFrom = new Dictionary<GridNode, GridNode>();
        var cost = new Dictionary<GridNode, double> { [startNode] = 0.0 };
        open.Enqueue(startNode, Heuristic(startNode, endNode, xs, ys, zs));

        var expansions = 0;
        const int maxExpansions = 50000;
        while (open.Count > 0 && expansions++ < maxExpansions)
        {
            var current = open.Dequeue();
            if (current.Equals(endNode)) return NodesToSegments(Reconstruct(cameFrom, current), xs, ys, zs);

            foreach (var next in Neighbors(current, xs.Count, ys.Count, zs.Count))
            {
                var seg = new RouteSegment(NodeToVec(current, xs, ys, zs), NodeToVec(next, xs, ys, zs));
                if (seg.Length <= 1e-3 || IsBlocked(seg, relevant, options)) continue;

                var newCost = cost[current] + seg.Length;
                if (cost.TryGetValue(next, out var oldCost) && newCost >= oldCost) continue;
                cost[next] = newCost;
                cameFrom[next] = current;
                open.Enqueue(next, newCost + Heuristic(next, endNode, xs, ys, zs));
            }
        }

        return new List<RouteSegment>();
    }

    private static IEnumerable<Aabb> RelevantObstacles(Vec3 start, Vec3 end, List<Aabb> obstacles, RubberBandOptions options)
    {
        var margin = Math.Max(options.TrayWidth * 2.0 + options.SafetyMargin, 2000.0);
        var min = new Vec3(Math.Min(start.X, end.X) - margin, Math.Min(start.Y, end.Y) - margin, Math.Min(start.Z, end.Z) - margin);
        var max = new Vec3(Math.Max(start.X, end.X) + margin, Math.Max(start.Y, end.Y) + margin, Math.Max(start.Z, end.Z) + margin);
        var corridor = new Aabb(min, max);
        return obstacles
            .Where(o => !o.IsPenetration)
            .Where(o => Intersects(o, corridor))
            .OrderBy(o => DistancePointToSegment(o.Center, start, end))
            .Take(30);
    }

    private static (List<double> Xs, List<double> Ys, List<double> Zs) BuildAStarLines(Vec3 start, Vec3 end, List<Aabb> obstacles, RubberBandOptions options)
    {
        var clearanceH = options.TrayWidth / 2.0 + options.SafetyMargin + 1.0;
        var clearanceZ = options.TrayHeight / 2.0 + options.SafetyMargin + 1.0;
        var xs = new List<double> { start.X, end.X };
        var ys = new List<double> { start.Y, end.Y };
        var zs = new List<double> { start.Z, end.Z };

        foreach (var obs in obstacles)
        {
            var expanded = obs.Expand(clearanceH, clearanceZ);
            AddCoord(xs, expanded.Min.X - clearanceH);
            AddCoord(xs, expanded.Max.X + clearanceH);
            AddCoord(ys, expanded.Min.Y - clearanceH);
            AddCoord(ys, expanded.Max.Y + clearanceH);
            AddCoord(zs, expanded.Min.Z - clearanceZ);
            AddCoord(zs, expanded.Max.Z + clearanceZ);
        }

        return (NormalizeCoords(xs), NormalizeCoords(ys), NormalizeCoords(zs));
    }

    private static void AddCoord(List<double> values, double value)
    {
        if (double.IsFinite(value)) values.Add(value);
    }

    private static List<double> NormalizeCoords(IEnumerable<double> values) => values
        .OrderBy(v => v)
        .Aggregate(new List<double>(), (acc, value) =>
        {
            if (acc.Count == 0 || Math.Abs(acc[^1] - value) > 1.0) acc.Add(value);
            return acc;
        });

    private static int IndexOf(List<double> values, double value)
    {
        var best = 0;
        var bestDistance = double.MaxValue;
        for (var i = 0; i < values.Count; i++)
        {
            var d = Math.Abs(values[i] - value);
            if (d < bestDistance)
            {
                bestDistance = d;
                best = i;
            }
        }
        return best;
    }

    private static IEnumerable<GridNode> Neighbors(GridNode node, int nx, int ny, int nz)
    {
        if (node.X > 0) yield return node with { X = node.X - 1 };
        if (node.X + 1 < nx) yield return node with { X = node.X + 1 };
        if (node.Y > 0) yield return node with { Y = node.Y - 1 };
        if (node.Y + 1 < ny) yield return node with { Y = node.Y + 1 };
        if (node.Z > 0) yield return node with { Z = node.Z - 1 };
        if (node.Z + 1 < nz) yield return node with { Z = node.Z + 1 };
    }

    private static bool IsBlocked(RouteSegment segment, List<Aabb> obstacles, RubberBandOptions options)
    {
        foreach (var obs in obstacles)
        {
            if (SegmentIntersectsExpandedAabb(segment, obs, options, out _)) return true;
        }
        return false;
    }

    private static double Heuristic(GridNode a, GridNode b, List<double> xs, List<double> ys, List<double> zs) =>
        Math.Abs(xs[a.X] - xs[b.X]) + Math.Abs(ys[a.Y] - ys[b.Y]) + Math.Abs(zs[a.Z] - zs[b.Z]);

    private static Vec3 NodeToVec(GridNode node, List<double> xs, List<double> ys, List<double> zs) => new(xs[node.X], ys[node.Y], zs[node.Z]);

    private static List<GridNode> Reconstruct(Dictionary<GridNode, GridNode> cameFrom, GridNode current)
    {
        var path = new List<GridNode> { current };
        while (cameFrom.TryGetValue(current, out var previous))
        {
            current = previous;
            path.Add(current);
        }
        path.Reverse();
        return path;
    }

    private static List<RouteSegment> NodesToSegments(List<GridNode> nodes, List<double> xs, List<double> ys, List<double> zs)
    {
        var segments = new List<RouteSegment>();
        for (var i = 0; i < nodes.Count - 1; i++)
        {
            var seg = new RouteSegment(NodeToVec(nodes[i], xs, ys, zs), NodeToVec(nodes[i + 1], xs, ys, zs));
            if (seg.Length > 1e-3) AppendMerged(segments, new[] { seg });
        }
        return segments;
    }

    private static void AppendMerged(List<RouteSegment> target, IEnumerable<RouteSegment> source)
    {
        foreach (var seg in source)
        {
            if (seg.Length <= 1e-3) continue;
            if (target.Count == 0)
            {
                target.Add(seg);
                continue;
            }

            var last = target[^1];
            if ((last.End - seg.Start).Length <= 1e-3 && DominantAxis(last.Delta) == DominantAxis(seg.Delta))
            {
                target[^1] = new RouteSegment(last.Start, seg.End);
            }
            else
            {
                target.Add(seg);
            }
        }
    }

    private static bool Intersects(Aabb a, Aabb b) =>
        a.Min.X <= b.Max.X && a.Max.X >= b.Min.X &&
        a.Min.Y <= b.Max.Y && a.Max.Y >= b.Min.Y &&
        a.Min.Z <= b.Max.Z && a.Max.Z >= b.Min.Z;
    public static List<RouteSegment> MakeRubberLineSegments(IEnumerable<Vec3> points)
    {
        var list = points.ToList();
        var segments = new List<RouteSegment>();
        for (var i = 0; i < list.Count - 1; i++)
        {
            if ((list[i + 1] - list[i]).Length <= 1e-3) continue;
            segments.Add(new RouteSegment(list[i], list[i + 1]));
        }
        return segments;
    }
    public static List<RouteSegment> MakeOrthogonalSegments(IEnumerable<Vec3> points)
    {
        var list = points.ToList();
        var segments = new List<RouteSegment>();
        for (var i = 0; i < list.Count - 1; i++)
        {
            var current = list[i];
            var target = list[i + 1];
            foreach (var axis in AxisOrder(current, target))
            {
                var delta = target[axis] - current[axis];
                if (Math.Abs(delta) <= 1e-3) continue;
                var next = current.WithAxis(axis, target[axis]);
                segments.Add(new RouteSegment(current, next));
                current = next;
            }
        }
        return segments;
    }

    private static IEnumerable<int> AxisOrder(Vec3 current, Vec3 target)
    {
        var delta = target - current;
        return new[]
        {
            new { Axis = 0, Magnitude = Math.Abs(delta.X) },
            new { Axis = 1, Magnitude = Math.Abs(delta.Y) },
            new { Axis = 2, Magnitude = Math.Abs(delta.Z) }
        }
        .Where(x => x.Magnitude > 1e-3)
        .OrderByDescending(x => x.Magnitude)
        .Select(x => x.Axis);
    }
    private static RubberBandStep ResolveCollisions(List<RouteSegment> initial, List<Aabb> obstacles, RubberBandOptions options)
    {
        var segments = initial.ToList();
        var collisions = new List<Vec3>();
        var verticalBends = CountVerticalBends(segments);

        for (var iter = 0; iter < options.MaxPushIterations; iter++)
        {
            var hit = FindFirstCollision(segments, obstacles, options);
            if (hit is null) break;

            var (segIndex, obstacle, point) = hit.Value;
            collisions.Add(point);
            var seg = segments[segIndex];
            var remaining = options.MaxVerticalBends - verticalBends;
            var bypassPoints = BuildBypass(seg, obstacle, remaining, options, out var usedBends);
            verticalBends += usedBends;

            var replacement = MakeRubberLineSegments(new[] { seg.Start }.Concat(bypassPoints).Concat(new[] { seg.End }));
            segments.RemoveAt(segIndex);
            segments.InsertRange(segIndex, replacement);
        }

        var step = MakeStep(3, $"Push collision resolution ({collisions.Count} hits)", segments);
        step.CollisionPoints.AddRange(collisions);
        return step;
    }

    private static (int Index, Aabb Obstacle, Vec3 Point)? FindFirstCollision(List<RouteSegment> segments, List<Aabb> obstacles, RubberBandOptions options)
    {
        for (var i = 0; i < segments.Count; i++)
        {
            foreach (var obs in obstacles.Where(o => !o.IsPenetration))
            {
                if (SegmentIntersectsExpandedAabb(segments[i], obs, options, out var point)) return (i, obs, point);
            }
        }
        return null;
    }

    private static bool SegmentIntersectsExpandedAabb(RouteSegment seg, Aabb obs, RubberBandOptions options, out Vec3 point)
    {
        var expanded = obs.Expand(options.TrayWidth / 2.0 + options.SafetyMargin, options.TrayHeight / 2.0 + options.SafetyMargin);
        var delta = seg.Delta;
        var tMin = 0.0;
        var tMax = 1.0;

        for (var axis = 0; axis < 3; axis++)
        {
            var start = seg.Start[axis];
            var direction = delta[axis];
            if (Math.Abs(direction) <= Epsilon)
            {
                if (start < expanded.Min[axis] || start > expanded.Max[axis])
                {
                    point = default;
                    return false;
                }
                continue;
            }

            var inv = 1.0 / direction;
            var t0 = (expanded.Min[axis] - start) * inv;
            var t1 = (expanded.Max[axis] - start) * inv;
            if (t0 > t1) (t0, t1) = (t1, t0);
            tMin = Math.Max(tMin, t0);
            tMax = Math.Min(tMax, t1);
            if (tMin > tMax)
            {
                point = default;
                return false;
            }
        }

        var t = Math.Clamp((tMin + tMax) / 2.0, 0.0, 1.0);
        point = seg.Start + delta * t;
        return true;
    }

    private static List<Vec3> BuildBypass(RouteSegment seg, Aabb obs, int remainingVerticalBends, RubberBandOptions options, out int usedVerticalBends)
    {
        usedVerticalBends = 0;
        var clearance = options.TrayWidth / 2.0 + options.TrayHeight + options.SafetyMargin + 1.0;
        var zOver = obs.Max.Z + clearance;
        var zUnder = obs.Min.Z - clearance;
        var midZ = (seg.Start.Z + seg.End.Z) / 2.0;
        var zTarget = Math.Abs(zOver - midZ) <= Math.Abs(zUnder - midZ) ? zOver : zUnder;
        if (remainingVerticalBends >= 2)
        {
            usedVerticalBends = 2;
            return new List<Vec3>
            {
                new(seg.Start.X, seg.Start.Y, zTarget),
                new(seg.End.X, seg.End.Y, zTarget)
            };
        }

        var axis = DominantAxis(seg.Delta);
        var sideAxis = axis == 0 ? 1 : 0;
        var lateralClearance = options.TrayWidth / 2.0 + options.SafetyMargin + 1.0;
        var lowSide = obs.Min[sideAxis] - lateralClearance;
        var highSide = obs.Max[sideAxis] + lateralClearance;
        var side = Math.Abs(seg.Start[sideAxis] - lowSide) <= Math.Abs(seg.Start[sideAxis] - highSide) ? lowSide : highSide;
        var exit = seg.End;
        var p1 = seg.Start.WithAxis(sideAxis, side);
        var p2 = p1.WithAxis(axis, axis switch { 0 => obs.Max.X + lateralClearance, 1 => obs.Max.Y + lateralClearance, _ => obs.Max.Z + lateralClearance });
        var p3 = exit.WithAxis(sideAxis, side);
        return new List<Vec3> { p1, p2, p3 };
    }

    public static List<List<Vec3>> DistributePipes(List<RouteSegment> segments, RubberBandOptions options)
    {
        var pipes = new List<List<Vec3>>();
        var half = (options.PipeCount - 1) / 2.0;
        for (var pipe = 0; pipe < options.PipeCount; pipe++)
        {
            var offset = (pipe - half) * options.PipePitch;
            var path = new List<Vec3>();
            Vec3? previousNormal = null;
            for (var i = 0; i < segments.Count; i++)
            {
                var normal = ComputeNormal(segments[i], previousNormal);
                previousNormal = normal;
                path.Add(segments[i].Start + normal * offset);
                if (i == segments.Count - 1) path.Add(segments[i].End + normal * offset);
            }
            pipes.Add(path);
        }
        return pipes;
    }

    private static Vec3 ComputeNormal(RouteSegment segment, Vec3? previous)
    {
        var d = segment.Delta;
        if (segment.IsVertical && previous.HasValue) return previous.Value;
        if (Math.Abs(d.X) >= Math.Abs(d.Y)) return new Vec3(0, Math.Sign(d.X == 0 ? 1 : d.X), 0);
        return new Vec3(-Math.Sign(d.Y == 0 ? 1 : d.Y), 0, 0);
    }

    private static List<string> Validate(List<RouteSegment> segments, List<Aabb> obstacles, RubberBandOptions options)
    {
        var issues = new List<string>();

        if (CountVerticalBends(segments) > options.MaxVerticalBends) issues.Add("vertical_bends_exceeded");
        var residual = FindFirstCollision(segments, obstacles, options);
        if (residual != null) issues.Add("residual_collision");
        return issues;
    }

    private static RubberBandStep MakeStep(int index, string description, IEnumerable<RouteSegment> segments)
    {
        var step = new RubberBandStep { StepIndex = index, Description = description };
        step.Segments.AddRange(segments);
        foreach (var p in ToPolyline(step.Segments)) step.Waypoints.Add(p);
        return step;
    }

    private static List<Vec3> ToPolyline(List<RouteSegment> segments)
    {
        if (segments.Count == 0) return new List<Vec3>();
        var points = new List<Vec3> { segments[0].Start };
        points.AddRange(segments.Select(s => s.End));
        return points;
    }

    private static int CountVerticalBends(List<RouteSegment> segments)
    {
        var count = 0;
        bool? prev = null;
        foreach (var seg in segments)
        {
            if (prev.HasValue && prev.Value != seg.IsVertical && seg.IsVertical) count++;
            prev = seg.IsVertical;
        }
        return count;
    }

    private static int DominantAxis(Vec3 v)
    {
        var ax = Math.Abs(v.X);
        var ay = Math.Abs(v.Y);
        var az = Math.Abs(v.Z);
        return ax >= ay && ax >= az ? 0 : ay >= az ? 1 : 2;
    }

    private static bool Within(double value, double min, double max) => value >= min - Epsilon && value <= max + Epsilon;

    private readonly record struct GridNode(int X, int Y, int Z);

    private sealed class RubberBandResultBuilder
    {
        private readonly RubberBandResult _source;
        public RubberBandResultBuilder(RubberBandResult source) => _source = source;
        public double TotalLength { get; init; }
        public int VerticalBends { get; init; }
        public bool IsValid { get; init; }
        public RubberBandResult Build()
        {
            var result = new RubberBandResult { TotalLength = TotalLength, VerticalBends = VerticalBends, IsValid = IsValid };
            result.Steps.AddRange(_source.Steps);
            result.FinalSegments.AddRange(_source.FinalSegments);
            result.PipePaths.AddRange(_source.PipePaths);
            result.ValidationIssues.AddRange(_source.ValidationIssues);
            return result;
        }
    }
}







