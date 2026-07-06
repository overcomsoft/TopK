using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;

namespace RubberBandRouting.Engine;

public sealed class ManagedRubberBandEngine : IRubberBandEngine
{
    private const double Epsilon = 1e-6;

    public RubberBandResult Route(
        Vec3 start,
        Vec3 end,
        IEnumerable<Aabb> obstacles,
        IEnumerable<RouteFeature>? featureWaypoints = null,
        RubberBandOptions? options = null)
    {
        options ??= new RubberBandOptions();
        var obstacleList = obstacles?.ToList() ?? new List<Aabb>();
        var features = featureWaypoints?.ToList() ?? new List<RouteFeature>();

        var sb = new StringBuilder();
        sb.AppendLine($"==================================================================================");
        sb.AppendLine($"[Task Trace] Start: ({start.X:F1}, {start.Y:F1}, {start.Z:F1}) ➔ End: ({end.X:F1}, {end.Y:F1}, {end.Z:F1})");
        sb.AppendLine($" - SafetyMargin: {options.SafetyMargin}mm, TrayWidth: {options.TrayWidth}mm, PipeDiameter: {options.PipeDiameter}mm");
        sb.AppendLine($" - Total Obstacles in Scene: {obstacleList.Count}");
        sb.AppendLine($" - Matched Features Count: {features.Count}");

        var result = new RubberBandResult();

        var step1Segments = MakeRubberLineSegments(new[] { start, end });
        result.Steps.Add(MakeStep(1, "Initial straight rubber tension", step1Segments));

        var snappedPoints = BuildSnappedPointList(start, end, features, options.SnapTolerance);
        sb.AppendLine($" - Waypoint skeleton snapped points ({snappedPoints.Count} total):");
        for (int i = 0; i < snappedPoints.Count; i++)
        {
            sb.AppendLine($"   [{i}] ({snappedPoints[i].X:F1}, {snappedPoints[i].Y:F1}, {snappedPoints[i].Z:F1})");
        }

        var step2Segments = MakeOrthogonalSegments(snappedPoints);
        var step2 = MakeStep(2, $"Waypoint skeleton ({snappedPoints.Count - 2} ordered waypoints)", step2Segments);
        foreach (var wp in snappedPoints.Skip(1).Take(Math.Max(0, snappedPoints.Count - 2))) step2.Waypoints.Add(wp);
        result.Steps.Add(step2);

        var step3Segments = RouteOrthogonalAStarViaWaypoints(snappedPoints, obstacleList, options, out var fallbackCount, sb, result.FallbackLegs);
        
        var requiredPoints = features.Where(f => f.Required).Select(f => f.Position).ToList();
        var straightened = ApplyLineOfSightShortcuts(step3Segments, obstacleList, options, requiredPoints);
        var merged = MergeShortDoglegs(straightened, obstacleList, options, requiredPoints);
        var step3 = MakeStep(3, fallbackCount == 0 ? "Orthogonal A* + line-of-sight straightening + dogleg merge" : $"Orthogonal A* + line-of-sight straightening + dogleg merge ({fallbackCount} fallback legs)", merged);
        result.Steps.Add(step3);

        result.FinalSegments.AddRange(step3.Segments);
        foreach (var pipe in DistributePipes(step3.Segments, options)) result.PipePaths.Add(pipe);

        var issues = Validate(step3.Segments, result.PipePaths, obstacleList, options, result.CollisionPoints, result.VerticalBendPoints);
        if (fallbackCount > 0) issues.Add("astar_fallback_used");
        foreach (var issue in issues) result.ValidationIssues.Add(issue);

        var reasons = ClassifySegmentReasons(step3.Segments, features, obstacleList, options);

        var finalResult = new RubberBandResultBuilder(result)
        {
            TotalLength = step3.Segments.Sum(s => s.Length),
            VerticalBends = CountVerticalBends(step3.Segments),
            IsValid = issues.Count == 0,
            SegmentReasonCodes = reasons
        }.Build();

        sb.AppendLine($"\r\n - Final Result Status: {(finalResult.IsValid ? "SUCCESS (Valid)" : "CHECK (Has Validation Issues)")}");
        sb.AppendLine($"   Validation Issues: {string.Join(", ", finalResult.ValidationIssues)}");
        sb.AppendLine($"   Final Path Segment Count: {finalResult.FinalSegments.Count}");
        sb.AppendLine($"   Total Length: {finalResult.TotalLength:N0}mm");
        sb.AppendLine($"   Vertical Bends: {finalResult.VerticalBends}");
        sb.AppendLine($"==================================================================================\r\n");

        if (options.EnableDebugLog)
        {
            var logPath = @"d:\DINNO\DEV\AI-AutoRouting\TopKGen\Docs\RubberBandRouting_DebugTrace.log";
            try
            {
                File.AppendAllText(logPath, sb.ToString());
            }
            catch { }
        }

        return finalResult;
    }

    private static List<Vec3> BuildSnappedPointList(Vec3 start, Vec3 end, List<RouteFeature> features, double tolerance)
    {
        var route = end - start;
        var len = route.Length;
        if (len < Epsilon || features.Count == 0) return new List<Vec3> { start, end };

        var points = new List<Vec3> { start };
        var maxDetour = Math.Max(len * 2.5, 10000.0);
        foreach (var feature in features)
        {
            var pos = feature.Position;
            if (!feature.Required)
            {
                if ((pos - start).Length <= tolerance || (pos - end).Length <= tolerance) continue;
                if (DistancePointToSegment(pos, start, end) > maxDetour) continue;
            }
            if (points.Count == 0 || (points[^1] - pos).Length > Math.Max(tolerance, 100.0)) points.Add(pos);
        }

        if ((points[^1] - end).Length > tolerance) points.Add(end);
        else points[^1] = end;
        return points;
    }

    /// <summary>
    /// Names why each final segment's leading corner exists, using the same signals the viewer
    /// used to infer post-hoc (feature proximity, obstacle-grid boundary proximity, axis/Z
    /// changes) but computed once by the engine that actually produced the route.
    /// </summary>
    private static List<string> ClassifySegmentReasons(List<RouteSegment> segments, List<RouteFeature> features, List<Aabb> obstacles, RubberBandOptions options)
    {
        var reasons = new List<string>(segments.Count);
        var clearanceH = options.TrayWidth / 2.0 + options.SafetyMargin + 1.0;
        var clearanceZ = options.TrayHeight / 2.0 + options.SafetyMargin + 1.0;
        var featureTolerance = Math.Max(options.SnapTolerance, 300.0);

        for (var i = 0; i < segments.Count; i++)
        {
            if (i == 0) { reasons.Add(SegmentReasons.RouteStart); continue; }

            var joint = segments[i].Start;
            if (features.Any(f => (f.Position - joint).Length <= featureTolerance))
            {
                reasons.Add(SegmentReasons.FeatureSnap);
                continue;
            }

            if (IsNearObstacleGridBoundary(joint, obstacles, clearanceH, clearanceZ))
            {
                reasons.Add(SegmentReasons.CollisionBypass);
                continue;
            }

            var previous = segments[i - 1];
            if (DominantAxis(previous.Delta) != DominantAxis(segments[i].Delta))
            {
                reasons.Add(SegmentReasons.DirectionChange);
                continue;
            }

            if (Math.Abs(previous.End.Z - segments[i].Start.Z) > 10 || Math.Abs(segments[i].Delta.Z) > 10)
            {
                reasons.Add(SegmentReasons.ElevationChange);
                continue;
            }

            reasons.Add(SegmentReasons.RubberAlignment);
        }
        return reasons;
    }

    /// <summary>
    /// True if <paramref name="point"/> sits on one of the A* bypass grid lines that
    /// <see cref="BuildAStarLines"/> derives from an obstacle (i.e. it's a detour corner, not a
    /// coincidental corner elsewhere in space). Mirrors BuildAStarLines' own margin math
    /// (expand by clearance, then offset the grid line by clearance again).
    /// </summary>
    private static bool IsNearObstacleGridBoundary(Vec3 point, List<Aabb> obstacles, double clearanceH, double clearanceZ)
    {
        const double eps = 5.0;
        var totalH = clearanceH * 2.0;
        var totalZ = clearanceZ * 2.0;
        foreach (var obs in obstacles)
        {
            if (obs.IsPenetration) continue;
            var e = obs.Expand(totalH, totalZ);
            var onX = Math.Abs(point.X - e.Min.X) <= eps || Math.Abs(point.X - e.Max.X) <= eps;
            var onY = Math.Abs(point.Y - e.Min.Y) <= eps || Math.Abs(point.Y - e.Max.Y) <= eps;
            var onZ = Math.Abs(point.Z - e.Min.Z) <= eps || Math.Abs(point.Z - e.Max.Z) <= eps;
            var withinX = point.X >= e.Min.X - eps && point.X <= e.Max.X + eps;
            var withinY = point.Y >= e.Min.Y - eps && point.Y <= e.Max.Y + eps;
            var withinZ = point.Z >= e.Min.Z - eps && point.Z <= e.Max.Z + eps;
            if ((onX && withinY && withinZ) || (onY && withinX && withinZ) || (onZ && withinX && withinY)) return true;
        }
        return false;
    }

    private static double DistancePointToSegment(Vec3 point, Vec3 a, Vec3 b)
    {
        return (point - ClosestPointOnSegment(point, a, b)).Length;
    }

    public static Vec3 ClosestPointOnSegment(Vec3 point, Vec3 a, Vec3 b)
    {
        var ab = b - a;
        var len2 = Vec3.Dot(ab, ab);
        if (len2 <= Epsilon) return a;
        var t = Math.Clamp(Vec3.Dot(point - a, ab) / len2, 0.0, 1.0);
        return a + ab * t;
    }

    private static List<RouteSegment> RouteOrthogonalAStarViaWaypoints(IReadOnlyList<Vec3> points, List<Aabb> obstacles, RubberBandOptions options, out int fallbackCount, StringBuilder sb, List<RouteSegment> fallbackLegs)
    {
        fallbackCount = 0;
        var route = new List<RouteSegment>();
        for (var i = 0; i < points.Count - 1; i++)
        {
            var ptStart = points[i];
            var ptEnd = points[i + 1];
            sb.AppendLine($"\r\n [Leg {i}] ({ptStart.X:F1}, {ptStart.Y:F1}, {ptStart.Z:F1}) ➔ ({ptEnd.X:F1}, {ptEnd.Y:F1}, {ptEnd.Z:F1})");

            var leg = RouteOrthogonalAStarLeg(ptStart, ptEnd, obstacles, options, sb);
            if (leg.Count == 0)
            {
                fallbackCount++;
                leg = MakeOrthogonalSegments(new[] { ptStart, ptEnd });
                sb.AppendLine($"   ➔ Leg {i} A* FAILED. Fell back to Orthogonal straight routing.");
                fallbackLegs.Add(new RouteSegment(ptStart, ptEnd));
            }
            else
            {
                sb.AppendLine($"   ➔ Leg {i} SUCCESS. Created {leg.Count} orthogonal segments (total length: {leg.Sum(s => s.Length):N0}mm)");
                for (int j = 0; j < leg.Count; j++)
                {
                    sb.AppendLine($"     Segment {j}: ({leg[j].Start.X:F1}, {leg[j].Start.Y:F1}, {leg[j].Start.Z:F1}) ➔ ({leg[j].End.X:F1}, {leg[j].End.Y:F1}, {leg[j].End.Z:F1}) (len: {leg[j].Length:N0}mm)");
                }
            }
            AppendMerged(route, leg);
        }
        return route;
    }

    private static List<RouteSegment> RouteOrthogonalAStarLeg(Vec3 start, Vec3 end, List<Aabb> obstacles, RubberBandOptions options, StringBuilder sb)
    {
        if ((end - start).Length <= 1e-3) return new List<RouteSegment>();
        // Split obstacle usage: every corridor-intersecting obstacle is used for collision
        // testing (so nothing is silently passed through), while only the nearest few build
        // the A* coordinate grid (keeps grid size bounded).
        var collision = CorridorObstacles(start, end, obstacles, options);
        var grid = collision
            .OrderBy(o => DistancePointToSegment(o.Center, start, end))
            .Take(GridObstacleLimit)
            .ToList();
        var (xs, ys, zs) = BuildAStarLines(start, end, grid, options);
        
        sb.AppendLine($"   * Grid lines: X={xs.Count}, Y={ys.Count}, Z={zs.Count} (Total potential grid nodes: {xs.Count * ys.Count * zs.Count})");
        sb.AppendLine($"     Corridor obstacles used for collision test: {collision.Count}");

        var startNode = new GridNode(IndexOf(xs, start.X), IndexOf(ys, start.Y), IndexOf(zs, start.Z));
        var endNode = new GridNode(IndexOf(xs, end.X), IndexOf(ys, end.Y), IndexOf(zs, end.Z));

        var open = new PriorityQueue<GridNode, double>();
        var cameFrom = new Dictionary<GridNode, GridNode>();
        var cost = new Dictionary<GridNode, double> { [startNode] = 0.0 };
        open.Enqueue(startNode, Heuristic(startNode, endNode, xs, ys, zs));

        var expansions = 0;
        // Raised from 50,000: in a dense equipment cluster with many nearby obstacles/accumulated
        // auto-routes, the grid can need far more expansions to find a genuinely narrow gap.
        // Mitigates (does not guarantee-fix) widespread astar_fallback_used when many tasks are
        // routed back-to-back in a tight area — if it's still hit, the obstacles may genuinely
        // leave no orthogonal gap at the current TrayWidth/SafetyMargin clearance.
        const int maxExpansions = 200000;
        while (open.Count > 0 && expansions < maxExpansions)
        {
            expansions++;
            var current = open.Dequeue();
            if (current.Equals(endNode))
            {
                var path = NodesToSegments(Reconstruct(cameFrom, current), xs, ys, zs);
                sb.AppendLine($"   * A* search SUCCESS after {expansions} expansions.");
                return path;
            }

            foreach (var next in Neighbors(current, xs.Count, ys.Count, zs.Count))
            {
                var seg = new RouteSegment(NodeToVec(current, xs, ys, zs), NodeToVec(next, xs, ys, zs));
                if (seg.Length <= 1e-3 || IsBlocked(seg, collision, options)) continue;

                var newCost = cost[current] + seg.Length;
                if (cost.TryGetValue(next, out var oldCost) && newCost >= oldCost) continue;
                cost[next] = newCost;
                cameFrom[next] = current;
                open.Enqueue(next, newCost + Heuristic(next, endNode, xs, ys, zs));
            }
        }

        if (expansions >= maxExpansions)
        {
            sb.AppendLine($"   * A* search TIMEOUT (max expansions limit {maxExpansions} reached).");
        }
        else
        {
            sb.AppendLine($"   * A* search FAILED after {expansions} expansions (destination unreachable on grid).");
        }

        return new List<RouteSegment>();
    }

    private const int GridObstacleLimit = 48;
    private const int CorridorObstacleLimit = 256;

    private static List<Aabb> CorridorObstacles(Vec3 start, Vec3 end, List<Aabb> obstacles, RubberBandOptions options)
    {
        var margin = Math.Max(options.TrayWidth * 2.0 + options.SafetyMargin, 2000.0);
        var min = new Vec3(Math.Min(start.X, end.X) - margin, Math.Min(start.Y, end.Y) - margin, Math.Min(start.Z, end.Z) - margin);
        var max = new Vec3(Math.Max(start.X, end.X) + margin, Math.Max(start.Y, end.Y) + margin, Math.Max(start.Z, end.Z) + margin);
        var corridor = new Aabb(min, max);
        return obstacles
            .Where(o => !o.IsPenetration)
            .Where(o => Intersects(o, corridor))
            .OrderBy(o => DistancePointToSegment(o.Center, start, end))
            .Take(CorridorObstacleLimit)
            .ToList();
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

    /// <summary>
    /// Greedy "string pulling" pass over the routed polyline: from each vertex, extend to the
    /// farthest later vertex still reachable by a straight line that stays clear of every
    /// obstacle, then continue from there. Turns an orthogonal A* staircase into a taut rubber
    /// line wherever nothing is in the way, while leaving genuinely obstructed stretches as their
    /// original (safe) orthogonal detour.
    ///
    /// Diagonal travel is intentionally restricted: horizontal-plane movement must always stay
    /// axis-aligned (a real pipe run only turns 90 degrees in plan view), and a true multi-axis
    /// diagonal is only ever allowed on the very first leg — the drop leaving the start PoC/
    /// equipment — never afterwards. Every other leg may only straighten a staircase into a
    /// single straight run along one axis.
    /// </summary>
    private static List<RouteSegment> ApplyLineOfSightShortcuts(List<RouteSegment> segments, List<Aabb> obstacles, RubberBandOptions options, List<Vec3> requiredPoints)
    {
        var points = ToPolyline(segments);
        if (points.Count < 3) return segments;

        bool IsRequired(Vec3 p) => requiredPoints.Any(r => (r - p).Length < 1.0);

        var simplified = new List<Vec3> { points[0] };
        var i = 0;
        while (i < points.Count - 1)
        {
            // Never shortcut past a required waypoint (e.g. a forced start-drop stub) — find the
            // next one, if any, and cap the greedy extension there so it always stays its own vertex.
            var hardLimit = points.Count - 1;
            for (var k = i + 1; k < points.Count - 1; k++)
            {
                if (IsRequired(points[k])) { hardLimit = k; break; }
            }

            var isStartLeg = i == 0;
            var farthest = i + 1;
            for (var j = i + 2; j <= hardLimit; j++)
            {
                var candidate = points[j] - simplified[^1];
                if (!IsShortcutDirectionAllowed(candidate, isStartLeg)) break;
                if (IsSegmentClear(simplified[^1], points[j], obstacles, options)) farthest = j;
                else break;
            }
            simplified.Add(points[farthest]);
            i = farthest;
        }
        return MakeRubberLineSegments(simplified);
    }

    /// <summary>
    /// A pipe run only ever turns 90 degrees in plan view, so a shortcut spanning both X and Y
    /// with Z not dominant (a "horizontal-plane diagonal") is never allowed, on any leg. A true
    /// multi-axis diagonal (e.g. a sloped drop mixing Z with X/Y) is only allowed on the very
    /// first leg — leaving the start PoC/equipment — matching how far a real installer can angle
    /// a pipe right as it leaves its connection point; every later leg must stay single-axis.
    /// </summary>
    private static bool IsShortcutDirectionAllowed(Vec3 delta, bool isStartLeg)
    {
        const double axisEpsilon = 5.0;
        var ax = Math.Abs(delta.X) > axisEpsilon;
        var ay = Math.Abs(delta.Y) > axisEpsilon;
        var az = Math.Abs(delta.Z) > axisEpsilon;

        var horizontalDiagonal = ax && ay && Math.Abs(delta.Z) <= Math.Max(Math.Abs(delta.X), Math.Abs(delta.Y));
        if (horizontalDiagonal) return false;

        if (isStartLeg) return true;

        var activeAxes = (ax ? 1 : 0) + (ay ? 1 : 0) + (az ? 1 : 0);
        return activeAxes <= 1;
    }

    private static bool IsSegmentClear(Vec3 a, Vec3 b, List<Aabb> obstacles, RubberBandOptions options)
    {
        var seg = new RouteSegment(a, b);
        foreach (var obs in obstacles)
        {
            if (!obs.IsPenetration && SegmentIntersectsExpandedAabb(seg, obs, options, out _)) return false;
        }
        return true;
    }

    /// <summary>
    /// Collapses a short "dogleg" — two consecutive corners joined by a very short connecting
    /// segment — into a single corner, by intersecting the extended incoming and outgoing
    /// directions. Only applied when the merged corner's two new legs stay clear of every
    /// obstacle and neither original corner is a required waypoint.
    /// </summary>
    private static List<RouteSegment> MergeShortDoglegs(List<RouteSegment> segments, List<Aabb> obstacles, RubberBandOptions options, List<Vec3> requiredPoints)
    {
        var points = ToPolyline(segments);
        if (points.Count < 4) return segments;

        bool IsRequired(Vec3 p) => requiredPoints.Any(r => (r - p).Length < 1.0);
        var mergeThreshold = Math.Max(options.TrayWidth * 0.75, 300.0);

        var result = new List<Vec3> { points[0] };
        var idx = 1;
        while (idx <= points.Count - 2)
        {
            if (idx + 1 <= points.Count - 2 && !IsRequired(points[idx]) && !IsRequired(points[idx + 1]))
            {
                var jog = (points[idx + 1] - points[idx]).Length;
                if (jog > 1 && jog < mergeThreshold)
                {
                    var prev = result[^1];
                    var next = points[idx + 2];
                    var inDir = points[idx] - prev;
                    var outDir = next - points[idx + 1];
                    // Same direction restriction as the LOS shortcut: a merged corner may only
                    // introduce a multi-axis diagonal when it's anchored at the true route start
                    // (the drop leaving the start PoC/equipment); never a horizontal-plane one.
                    var isStartLeg = (prev - points[0]).Length < 1.0;
                    if (inDir.Length > 1 && outDir.Length > 1
                        && TryIntersectLines(prev, inDir, points[idx + 1], outDir, out var merged)
                        && IsShortcutDirectionAllowed(merged - prev, isStartLeg)
                        && IsShortcutDirectionAllowed(next - merged, isStartLeg)
                        && IsSegmentClear(prev, merged, obstacles, options)
                        && IsSegmentClear(merged, next, obstacles, options))
                    {
                        result.Add(merged);
                        idx += 2;
                        continue;
                    }
                }
            }
            result.Add(points[idx]);
            idx++;
        }
        result.Add(points[^1]);
        return MakeRubberLineSegments(result);
    }

    /// <summary>
    /// Closest-point-between-two-lines intersection: line 1 through p1 with direction d1, line 2
    /// through p2 with direction d2. Returns false for parallel/degenerate lines, or when the two
    /// lines' closest points are too far apart to represent a genuine (near-coplanar) corner.
    /// </summary>
    private static bool TryIntersectLines(Vec3 p1, Vec3 d1, Vec3 p2, Vec3 d2, out Vec3 intersection)
    {
        intersection = default;
        var a = Vec3.Dot(d1, d1);
        var b = Vec3.Dot(d1, d2);
        var c = Vec3.Dot(d2, d2);
        var w = p1 - p2;
        var d = Vec3.Dot(d1, w);
        var e = Vec3.Dot(d2, w);
        var denom = a * c - b * b;
        if (Math.Abs(denom) < 1e-6) return false;

        var t = (b * e - c * d) / denom;
        var s = (a * e - b * d) / denom;
        var pointOnLine1 = p1 + d1 * t;
        var pointOnLine2 = p2 + d2 * s;
        if ((pointOnLine1 - pointOnLine2).Length > Math.Max(50.0, (p1 - p2).Length * 0.1)) return false;

        intersection = (pointOnLine1 + pointOnLine2) * 0.5;
        return true;
    }

    public static List<List<Vec3>> DistributePipes(List<RouteSegment> segments, RubberBandOptions options)
    {
        var pipes = new List<List<Vec3>>();
        var half = (options.PipeCount - 1) / 2.0;
        var normals = ComputeSegmentNormals(segments);
        for (var pipe = 0; pipe < options.PipeCount; pipe++)
        {
            var offset = (pipe - half) * options.PipePitch;
            var path = new List<Vec3>();
            for (var i = 0; i < segments.Count; i++)
            {
                // Offset BOTH ends of segment i by segment i's own normal, so this pipe's edge
                // stays parallel to the centerline segment. At a turn, the previous segment's
                // offset end and this segment's offset start differ (different normals) — that
                // gap is the natural connecting jog a parallel pipe bend actually has; it used
                // to be silently skipped (sharing one corner point with the wrong normal),
                // which drew a diagonal, non-parallel edge instead of a clean bend.
                var normal = normals[i];
                var start = segments[i].Start + normal * offset;
                var end = segments[i].End + normal * offset;
                if (path.Count == 0 || (path[^1] - start).Length > 1e-6) path.Add(start);
                path.Add(end);
            }
            pipes.Add(path);
        }
        return pipes;
    }

    private static List<Vec3> ComputeSegmentNormals(List<RouteSegment> segments)
    {
        var normals = new List<Vec3>(segments.Count);
        Vec3? previous = null;
        foreach (var seg in segments)
        {
            var normal = ComputeNormal(seg, previous);
            normals.Add(normal);
            previous = normal;
        }
        return normals;
    }

    private static Vec3 ComputeNormal(RouteSegment segment, Vec3? previous)
    {
        var d = segment.Delta;
        if (segment.IsVertical && previous.HasValue) return previous.Value;
        if (Math.Abs(d.X) >= Math.Abs(d.Y)) return new Vec3(0, Math.Sign(d.X == 0 ? 1 : d.X), 0);
        return new Vec3(-Math.Sign(d.Y == 0 ? 1 : d.Y), 0, 0);
    }

    private static List<string> Validate(List<RouteSegment> segments, List<List<Vec3>> pipePaths, List<Aabb> obstacles, RubberBandOptions options, List<Vec3> collisionPoints, List<Vec3> verticalBends)
    {
        var issues = new List<string>();

        if (FindVerticalBends(segments, verticalBends) > options.MaxVerticalBends) issues.Add("vertical_bends_exceeded");
        
        FindAllPipeCollisions(pipePaths, obstacles, options, collisionPoints);
        if (collisionPoints.Count > 0) issues.Add("residual_collision");
        return issues;
    }

    private static void FindAllPipeCollisions(List<List<Vec3>> pipePaths, List<Aabb> obstacles, RubberBandOptions options, List<Vec3> collisionPoints)
    {
        var radius = options.PipeDiameter / 2.0;
        foreach (var pipe in pipePaths)
        {
            for (var i = 0; i < pipe.Count - 1; i++)
            {
                var seg = new RouteSegment(pipe[i], pipe[i + 1]);
                foreach (var obs in obstacles.Where(o => !o.IsPenetration))
                {
                    if (SegmentIntersectsPipeAabb(seg, obs, radius, options.SafetyMargin))
                    {
                        var cp = ClosestPointOnSegment(obs.Center, seg.Start, seg.End);
                        if (!collisionPoints.Any(p => (p - cp).Length <= 100.0))
                        {
                            collisionPoints.Add(cp);
                        }
                    }
                }
            }
        }
    }

    public static bool SegmentIntersectsPipeAabb(RouteSegment seg, Aabb obs, double radius, double safetyMargin)
    {
        var expanded = obs.Expand(radius + safetyMargin, radius + safetyMargin);
        var delta = seg.Delta;
        var tMin = 0.0;
        var tMax = 1.0;

        for (var axis = 0; axis < 3; axis++)
        {
            var start = seg.Start[axis];
            var direction = delta[axis];
            if (Math.Abs(direction) <= Epsilon)
            {
                if (start < expanded.Min[axis] || start > expanded.Max[axis]) return false;
                continue;
            }

            var inv = 1.0 / direction;
            var t0 = (expanded.Min[axis] - start) * inv;
            var t1 = (expanded.Max[axis] - start) * inv;
            if (t0 > t1) (t0, t1) = (t1, t0);
            tMin = Math.Max(tMin, t0);
            tMax = Math.Min(tMax, t1);
            if (tMin > tMax) return false;
        }
        return true;
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
        return FindVerticalBends(segments, new List<Vec3>());
    }

    public static int FindVerticalBends(List<RouteSegment> segments, List<Vec3> bendPoints)
    {
        var count = 0;
        for (int i = 0; i < segments.Count - 1; i++)
        {
            var s1 = segments[i];
            var s2 = segments[i + 1];
            if (s1.IsVertical != s2.IsVertical)
            {
                count++;
                if (bendPoints != null)
                {
                    bendPoints.Add(s1.End);
                }
            }
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

    private readonly record struct GridNode(int X, int Y, int Z);

    private sealed class RubberBandResultBuilder
    {
        private readonly RubberBandResult _source;
        public RubberBandResultBuilder(RubberBandResult source) => _source = source;
        public double TotalLength { get; init; }
        public int VerticalBends { get; init; }
        public bool IsValid { get; init; }
        public List<string> SegmentReasonCodes { get; init; } = new();
        public RubberBandResult Build()
        {
            var result = new RubberBandResult { TotalLength = TotalLength, VerticalBends = VerticalBends, IsValid = IsValid };
            result.Steps.AddRange(_source.Steps);
            result.FinalSegments.AddRange(_source.FinalSegments);
            result.PipePaths.AddRange(_source.PipePaths);
            result.ValidationIssues.AddRange(_source.ValidationIssues);
            result.SegmentReasonCodes.AddRange(SegmentReasonCodes);
            return result;
        }
    }
}







