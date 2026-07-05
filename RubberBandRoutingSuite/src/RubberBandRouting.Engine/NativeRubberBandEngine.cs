using System;
using System.Collections.Generic;
using System.Linq;

namespace RubberBandRouting.Engine;

/// <summary>
/// Routes through the native C++ engine (RubberBandRouting.Native.dll) via P/Invoke.
/// Mirrors <see cref="ManagedRubberBandEngine"/>'s public contract so the viewer can
/// switch engines without touching call sites.
/// </summary>
public sealed class NativeRubberBandEngine : IRubberBandEngine
{
    private static bool? _available;

    /// <summary>
    /// True if RubberBandRouting.Native.dll can be loaded and called. Cached after first probe.
    /// Build it with cpp/RubberBandRouting.Native/build_msvc.bat and place the DLL next to the
    /// executable (or on PATH) before enabling the native engine.
    /// </summary>
    public static bool IsAvailable
    {
        get
        {
            if (_available.HasValue) return _available.Value;
            try
            {
                var handle = NativeMethods.rb_create();
                if (handle == IntPtr.Zero) { _available = false; return false; }
                NativeMethods.rb_destroy(handle);
                _available = true;
            }
            catch (Exception ex) when (ex is DllNotFoundException or BadImageFormatException or EntryPointNotFoundException)
            {
                _available = false;
            }
            return _available.Value;
        }
    }

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

        var handle = NativeMethods.rb_create();
        if (handle == IntPtr.Zero) throw new InvalidOperationException("rb_create failed to allocate a native engine instance.");

        try
        {
            NativeMethods.rb_initialize(handle, ToRbConfig(options));

            var rbObstacles = obstacleList.Select(ToRbAabb).ToArray();
            NativeMethods.rb_set_obstacles(handle, rbObstacles, rbObstacles.Length);

            var rbFeatures = features.Select(f => ToRbVec3(f.Position)).ToArray();
            NativeMethods.rb_set_features(handle, rbFeatures, rbFeatures.Length);
            var required = features.Select(f => f.Required ? 1 : 0).ToArray();
            NativeMethods.rb_set_feature_flags(handle, required, required.Length);

            NativeMethods.rb_execute(handle, ToRbVec3(start), ToRbVec3(end));

            var segments = CopySegments(handle);
            var pipes = CopyPipes(handle);
            var verticalBends = NativeMethods.rb_get_vertical_bends(handle);
            var fallbackCount = NativeMethods.rb_get_fallback_count(handle);
            var isValid = NativeMethods.rb_is_valid(handle) != 0;

            var result = new RubberBandResult
            {
                TotalLength = segments.Sum(s => s.Length),
                VerticalBends = verticalBends,
                IsValid = isValid
            };

            var step = new RubberBandStep { StepIndex = 1, Description = "Native rubber-band routing (C++ rb_execute)" };
            step.Segments.AddRange(segments);
            foreach (var p in ToPolyline(segments)) step.Waypoints.Add(p);
            result.Steps.Add(step);

            result.FinalSegments.AddRange(segments);
            result.PipePaths.AddRange(pipes);
            for (var i = 0; i < segments.Count; i++)
                result.SegmentReasonCodes.Add(SegmentReasons.FromNativeCode(NativeMethods.rb_get_segment_reason(handle, i)));
            if (fallbackCount > 0) result.ValidationIssues.Add("astar_fallback_used");
            if (verticalBends > options.MaxVerticalBends) result.ValidationIssues.Add("vertical_bends_exceeded");
            if (!isValid && result.ValidationIssues.Count == 0) result.ValidationIssues.Add("residual_collision");

            return result;
        }
        finally
        {
            NativeMethods.rb_destroy(handle);
        }
    }

    private static List<RouteSegment> CopySegments(IntPtr handle)
    {
        var needed = NativeMethods.rb_copy_segments(handle, null, 0);
        if (needed <= 0) return new List<RouteSegment>();

        var buffer = new NativeMethods.RbVec3[needed];
        var copied = NativeMethods.rb_copy_segments(handle, buffer, needed);

        var segments = new List<RouteSegment>();
        for (var i = 0; i < copied - 1; i++) segments.Add(new RouteSegment(FromRbVec3(buffer[i]), FromRbVec3(buffer[i + 1])));
        return segments;
    }

    private static List<List<Vec3>> CopyPipes(IntPtr handle)
    {
        var pipeCount = NativeMethods.rb_get_pipe_count(handle);
        var pipes = new List<List<Vec3>>();
        for (var p = 0; p < pipeCount; p++)
        {
            var needed = NativeMethods.rb_copy_pipe_path(handle, p, null, 0);
            if (needed <= 0) { pipes.Add(new List<Vec3>()); continue; }

            var buffer = new NativeMethods.RbVec3[needed];
            var copied = NativeMethods.rb_copy_pipe_path(handle, p, buffer, needed);
            pipes.Add(buffer.Take(copied).Select(FromRbVec3).ToList());
        }
        return pipes;
    }

    private static List<Vec3> ToPolyline(List<RouteSegment> segments)
    {
        if (segments.Count == 0) return new List<Vec3>();
        var points = new List<Vec3> { segments[0].Start };
        points.AddRange(segments.Select(s => s.End));
        return points;
    }

    private static NativeMethods.RbConfig ToRbConfig(RubberBandOptions options) => new()
    {
        MaxVerticalBends = options.MaxVerticalBends,
        SafetyMargin = options.SafetyMargin,
        TrayWidth = options.TrayWidth,
        TrayHeight = options.TrayHeight,
        PipePitch = options.PipePitch,
        PipeCount = options.PipeCount,
        SnapTolerance = options.SnapTolerance
    };

    private static NativeMethods.RbVec3 ToRbVec3(Vec3 v) => new(v.X, v.Y, v.Z);
    private static Vec3 FromRbVec3(NativeMethods.RbVec3 v) => new(v.X, v.Y, v.Z);

    private static NativeMethods.RbAabb ToRbAabb(Aabb a) => new()
    {
        MinX = a.Min.X, MinY = a.Min.Y, MinZ = a.Min.Z,
        MaxX = a.Max.X, MaxY = a.Max.Y, MaxZ = a.Max.Z,
        IsPenetration = a.IsPenetration ? 1 : 0
    };
}
