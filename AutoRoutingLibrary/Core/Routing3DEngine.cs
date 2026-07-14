using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;

namespace AutoRoutingLibrary.Core
{
    /// <summary>Managed .NET wrapper around the native Routing3D C API.</summary>
    public sealed class Routing3DEngine : IDisposable
    {
        private readonly R3dEngineHandle _handle = R3dEngineHandle.Create();
        private bool _disposed;

        public static string NativeVersion => Native.VersionString();

        /// <summary>Routes a complete scene text document and returns the routed scene text.</summary>
        public static string RouteSceneText(string sceneText, string mode = "multi", string priority = "longest")
        {
            if (sceneText == null) throw new ArgumentNullException(nameof(sceneText));
            Check(Native.r3d_route_scene_text(Native.Utf8(sceneText), Native.Utf8(mode), Native.Utf8(priority), out var routed),
                "route_scene_text");
            return Native.TakeString(routed);
        }

        public void LoadSceneText(string sceneText)
        {
            if (sceneText == null) throw new ArgumentNullException(nameof(sceneText));
            Check(Native.r3d_load_scene_text(Handle, Native.Utf8(sceneText)), "load_scene_text");
        }

        public void SetGrid(RoutingGrid grid)
        {
            var g = new Native.R3dGrid
            {
                cell_mm = grid.CellMm,
                ox = grid.Origin.X,
                oy = grid.Origin.Y,
                oz = grid.Origin.Z,
                nx = grid.Nx,
                ny = grid.Ny,
                nz = grid.Nz
            };
            Check(Native.r3d_set_grid(Handle, in g), "set_grid");
        }

        public void SetGrid(double cellMm, double ox, double oy, double oz, int nx, int ny, int nz)
            => SetGrid(new RoutingGrid(cellMm, new Vec3(ox, oy, oz), nx, ny, nz));

        public void SetParameters(RoutingParameters parameters)
        {
            if (parameters == null) throw new ArgumentNullException(nameof(parameters));
            var rack = new int[8];
            var rackCount = Math.Min(parameters.RackLevels.Count, rack.Length);
            for (var i = 0; i < rackCount; i++) rack[i] = parameters.RackLevels[i];

            var p = new Native.R3dParams
            {
                cell_mm = parameters.CellMm,
                w_turn = parameters.TurnCostMm,
                w_clear = parameters.ClearanceCostMm,
                w_corridor = parameters.CorridorCostMm,
                w_heur = parameters.HeuristicWeight,
                w_heur_near = parameters.NearGoalHeuristicWeight,
                clearance_radius = parameters.ClearanceRadiusCells,
                clearance_connectivity = parameters.ClearanceConnectivity,
                corridor_radius = parameters.CorridorRadiusCells,
                rack_level_count = rackCount,
                rack_levels = rack
            };
            Check(Native.r3d_set_params(Handle, in p), "set_params");
        }

        public void SetParameters(double cellMm, double turnCostMm, double clearanceCostMm,
            int clearanceRadiusCells = 2, int clearanceConnectivity = 6)
            => SetParameters(new RoutingParameters
            {
                CellMm = cellMm,
                TurnCostMm = turnCostMm,
                ClearanceCostMm = clearanceCostMm,
                ClearanceRadiusCells = clearanceRadiusCells,
                ClearanceConnectivity = clearanceConnectivity
            });

        public void AddObstacle(Aabb box)
            => Check(Native.r3d_add_obstacle(Handle, box.Min.X, box.Min.Y, box.Min.Z, box.Max.X, box.Max.Y, box.Max.Z),
                "add_obstacle");

        public void AddObstacle(double minx, double miny, double minz, double maxx, double maxy, double maxz)
            => AddObstacle(new Aabb(new Vec3(minx, miny, minz), new Vec3(maxx, maxy, maxz)));

        public void AddPassthrough(Aabb box)
            => Check(Native.r3d_add_passthrough(Handle, box.Min.X, box.Min.Y, box.Min.Z, box.Max.X, box.Max.Y, box.Max.Z),
                "add_passthrough");

        public void AddPassthrough(double minx, double miny, double minz, double maxx, double maxy, double maxz)
            => AddPassthrough(new Aabb(new Vec3(minx, miny, minz), new Vec3(maxx, maxy, maxz)));

        public int AddTask(Vec3 startMm, Vec3 goalMm, string? utility = null, string? utilityGroup = null)
        {
            var index = Native.r3d_add_task(Handle, startMm.X, startMm.Y, startMm.Z, goalMm.X, goalMm.Y, goalMm.Z,
                Native.Utf8OrNull(utility), Native.Utf8OrNull(utilityGroup));
            if (index < 0) throw new InvalidOperationException("Native Routing3D operation 'add_task' failed.");
            return index;
        }

        public int AddTask(double sx, double sy, double sz, double gx, double gy, double gz,
            string? utility = null, string? utilityGroup = null)
            => AddTask(new Vec3(sx, sy, sz), new Vec3(gx, gy, gz), utility, utilityGroup);

        public void SetTaskEndpoints(int task, Vec3 startMm, Vec3 goalMm)
            => Check(Native.r3d_set_task_endpoints(Handle, task, startMm.X, startMm.Y, startMm.Z, goalMm.X, goalMm.Y, goalMm.Z),
                "set_task_endpoints");

        public void SetTaskDiameter(int task, double diameterMm)
            => Check(Native.r3d_set_task_diameter(Handle, task, diameterMm), "set_task_diameter");

        public void SetTaskGoalDirection(int task, GoalDirection direction)
            => Check(Native.r3d_set_task_goal_dir(Handle, task, (int)direction), "set_task_goal_dir");

        public void SetCollectVisited(bool enabled)
            => Check(Native.r3d_set_collect_visited(Handle, enabled ? 1 : 0), "set_collect_visited");

        public void SetPipeRadius(int radiusCells)
            => Check(Native.r3d_set_pipe_radius(Handle, radiusCells), "set_pipe_radius");

        public void SetPerTaskRadius(bool enabled)
            => Check(Native.r3d_set_per_task_radius(Handle, enabled ? 1 : 0), "set_per_task_radius");

        public void SetCbsDepth(int depth)
            => Check(Native.r3d_set_cbs_depth(Handle, depth), "set_cbs_depth");

        public void SetMinStraight(double multiplier)
            => Check(Native.r3d_set_min_straight(Handle, multiplier), "set_min_straight");

        public void SetPipeGap(double gapMm)
            => Check(Native.r3d_set_pipe_gap(Handle, gapMm), "set_pipe_gap");

        public void SetCorridorCells(IEnumerable<PathCell>? cells)
        {
            if (cells == null)
            {
                Check(Native.r3d_set_corridor_cells(Handle, null, 0), "set_corridor_cells");
                return;
            }

            var flattened = cells.SelectMany(c => new[] { c.I, c.J, c.K }).ToArray();
            Check(Native.r3d_set_corridor_cells(Handle, flattened, flattened.Length / 3), "set_corridor_cells");
        }

        public void SetRankedCorridorCells(
            IEnumerable<RankedPathCell>? cells, IReadOnlyList<double> rankPenaltyFactors)
        {
            if (rankPenaltyFactors == null || rankPenaltyFactors.Count == 0)
                throw new ArgumentException("At least one rank penalty factor is required.", nameof(rankPenaltyFactors));
            var factors = rankPenaltyFactors.ToArray();
            if (factors.Any(value => value < 0.0 || value > 1.0 || double.IsNaN(value) || double.IsInfinity(value)))
                throw new ArgumentOutOfRangeException(nameof(rankPenaltyFactors), "Factors must be finite values in [0,1].");
            var ranked = cells?.ToArray() ?? Array.Empty<RankedPathCell>();
            if (ranked.Any(value => value.Rank < 1 || value.Rank > factors.Length))
                throw new ArgumentOutOfRangeException(nameof(cells), "Every rank must map to a supplied factor.");
            var flattened = ranked.SelectMany(value => new[] { value.Cell.I, value.Cell.J, value.Cell.K }).ToArray();
            var ranks = ranked.Select(value => value.Rank).ToArray();
            Check(Native.r3d_set_ranked_corridor_cells(
                Handle, flattened, ranks, ranked.Length, factors, factors.Length), "set_ranked_corridor_cells");
        }

        public void RouteMulti(string priority = "longest")
            => Check(Native.r3d_route_multi(Handle, Native.Utf8(priority)), "route_multi");

        public void RouteRipup(string priority = "longest", int maxRounds = 10, int maxRipup = 4)
            => Check(Native.r3d_route_ripup(Handle, Native.Utf8(priority), maxRounds, maxRipup), "route_ripup");

        public CppRouteResult RouteTask(int task)
        {
            Check(Native.r3d_route_task(Handle, task, out _), "route_task");
            return GetResult(task);
        }

        public void RouteCorridor(int factor = 16, int radius = 2)
            => Check(Native.r3d_route_corridor(Handle, factor, radius), "route_corridor");

        public void RouteCorridorMulti(int factor = 16, int radius = 2, string priority = "longest", int pipeRadius = 0)
            => Check(Native.r3d_route_corridor_multi(Handle, factor, radius, Native.Utf8(priority), pipeRadius),
                "route_corridor_multi");

        public void RouteMultiProgress(string priority, Action<RouteProgress> onProgress, Func<bool>? shouldCancel = null)
        {
            if (onProgress == null) throw new ArgumentNullException(nameof(onProgress));
            Native.R3dProgressFn callback = (_, phase, orderIndex, taskIndex, success, lengthMm, turns,
                expandedNodes, elapsedMs, done, total, progress01, pathPtr, pathLen) =>
            {
                onProgress(new RouteProgress(phase, orderIndex, taskIndex, success != 0, lengthMm, turns,
                    expandedNodes, elapsedMs, done, total, progress01, CopyPathFromCallback(pathPtr, pathLen)));
                return (shouldCancel != null && shouldCancel()) ? 1 : 0;
            };

            try
            {
                Check(Native.r3d_route_multi_progress(Handle, Native.Utf8(priority), callback, IntPtr.Zero),
                    "route_multi_progress");
            }
            finally
            {
                GC.KeepAlive(callback);
            }
        }

        public CppRouteResult GetResult(int task)
        {
            Check(Native.r3d_get_result(Handle, task, out var result), "get_result");
            return ToRouteResult(task, result);
        }

        public IReadOnlyList<PathCell> CopyBlocked() => CopyCells(Native.r3d_copy_blocked);
        public IReadOnlyList<PathCell> CopyPassthrough() => CopyCells(Native.r3d_copy_passthrough);

        public string DumpSceneText()
        {
            Check(Native.r3d_dump_scene_text(Handle, out var text), "dump_scene_text");
            return Native.TakeString(text);
        }

        private CppRouteResult ToRouteResult(int task, Native.R3dResult result)
        {
            return new CppRouteResult
            {
                Success = result.success != 0,
                LengthMm = result.length_mm,
                CostMm = result.cost_mm,
                Turns = result.turns,
                ExpandedNodes = result.expanded_nodes,
                ElapsedMs = result.elapsed_ms,
                Fail = (RouteFail)result.fail_reason,
                Path = CopyTaskCells(task, result.path_len, Native.r3d_copy_path),
                Visited = CopyTaskCells(task, result.visited_len, Native.r3d_copy_visited)
            };
        }

        private IReadOnlyList<PathCell> CopyTaskCells(int task, int count, Func<IntPtr, int, int[], int, int> copy)
        {
            if (count <= 0) return Array.Empty<PathCell>();
            var buffer = new int[count * 3];
            var copied = copy(Handle, task, buffer, count);
            return ToCells(buffer, copied);
        }

        private IReadOnlyList<PathCell> CopyCells(Func<IntPtr, int[]?, int, int> copy)
        {
            var total = copy(Handle, null, 0);
            if (total <= 0) return Array.Empty<PathCell>();
            var buffer = new int[total * 3];
            var copied = copy(Handle, buffer, total);
            return ToCells(buffer, copied);
        }

        private static IReadOnlyList<PathCell> CopyPathFromCallback(IntPtr pathPtr, int pathLen)
        {
            if (pathPtr == IntPtr.Zero || pathLen <= 0) return Array.Empty<PathCell>();
            var buffer = new int[pathLen * 3];
            Marshal.Copy(pathPtr, buffer, 0, buffer.Length);
            return ToCells(buffer, pathLen);
        }

        private static PathCell[] ToCells(int[] buffer, int count)
        {
            if (count <= 0) return Array.Empty<PathCell>();
            var cells = new PathCell[count];
            for (var i = 0; i < count; i++)
                cells[i] = new PathCell(buffer[3 * i], buffer[3 * i + 1], buffer[3 * i + 2]);
            return cells;
        }

        private IntPtr Handle
        {
            get
            {
                if (_disposed) throw new ObjectDisposedException(nameof(Routing3DEngine));
                return _handle.DangerousGetHandle();
            }
        }

        private static void Check(int status, string operation)
        {
            if (status != 0) throw new Routing3DException((R3dStatus)status, operation);
        }

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;
            _handle.Dispose();
        }
    }
}
