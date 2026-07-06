using System;
using System.Collections.Generic;
using System.Linq;

namespace RubberBandRouting.Engine;

public readonly record struct Vec3(double X, double Y, double Z)
{
    public static Vec3 operator +(Vec3 a, Vec3 b) => new(a.X + b.X, a.Y + b.Y, a.Z + b.Z);
    public static Vec3 operator -(Vec3 a, Vec3 b) => new(a.X - b.X, a.Y - b.Y, a.Z - b.Z);
    public static Vec3 operator *(Vec3 a, double s) => new(a.X * s, a.Y * s, a.Z * s);
    public double this[int axis] => axis switch { 0 => X, 1 => Y, _ => Z };
    public Vec3 WithAxis(int axis, double value) => axis switch
    {
        0 => new Vec3(value, Y, Z),
        1 => new Vec3(X, value, Z),
        _ => new Vec3(X, Y, value)
    };
    public double Length => Math.Sqrt(X * X + Y * Y + Z * Z);
    public static double Dot(Vec3 a, Vec3 b) => a.X * b.X + a.Y * b.Y + a.Z * b.Z;
}

public readonly record struct Aabb(Vec3 Min, Vec3 Max, bool IsPenetration = false, string Name = "")
{
    public Vec3 Center => new((Min.X + Max.X) / 2.0, (Min.Y + Max.Y) / 2.0, (Min.Z + Max.Z) / 2.0);
    public Aabb Expand(double horizontal, double vertical) => new(
        new Vec3(Min.X - horizontal, Min.Y - horizontal, Min.Z - vertical),
        new Vec3(Max.X + horizontal, Max.Y + horizontal, Max.Z + vertical),
        IsPenetration,
        Name);
}

public sealed record RouteSegment(Vec3 Start, Vec3 End)
{
    public Vec3 Delta => End - Start;
    public double Length => Delta.Length;
    public bool IsVertical => Math.Abs(Delta.Z) > Math.Max(Math.Abs(Delta.X), Math.Abs(Delta.Y));
}

public sealed class RubberBandOptions
{
    public int MaxVerticalBends { get; init; } = 5;
    public double SafetyMargin { get; init; } = 50.0;
    public double TrayWidth { get; init; } = 600.0;
    public double TrayHeight { get; init; } = 100.0;
    public double PipePitch { get; init; } = 100.0;
    // Default to a single pipe: no per-task data source currently indicates how many physical
    // pipes a given start-end connection actually bundles, so forcing a multiplier here would
    // just draw N near-duplicate lines for every task. Callers with real bundling data should
    // set this explicitly.
    public int PipeCount { get; init; } = 1;
    public double SnapTolerance { get; init; } = 100.0;
    public double PipeDiameter { get; set; } = 50.0;
    public bool EnableDebugLog { get; set; } = true;

    /// <summary>
    /// Pipe bend radius as a multiple of pipe outer diameter (R = BendRadiusFactor × D), i.e. the
    /// radius of the centerline arc a pipe can be bent to without kinking/deforming. Standard
    /// pipe fabrication practice commonly requires at least 3D for a smooth bend, so that's the
    /// default; raise it for materials/processes that need a gentler (e.g. 5D) bend.
    /// </summary>
    public double BendRadiusFactor { get; init; } = 3.0;
}

public sealed class RubberBandStep
{
    public int StepIndex { get; init; }
    public string Description { get; init; } = string.Empty;
    public List<RouteSegment> Segments { get; } = new();
    public List<Vec3> Waypoints { get; } = new();
    public List<Vec3> CollisionPoints { get; } = new();
}

public sealed class RubberBandResult
{
    public List<RubberBandStep> Steps { get; } = new();
    public List<RouteSegment> FinalSegments { get; } = new();
    public List<List<Vec3>> PipePaths { get; } = new();
    public double TotalLength { get; init; }
    public int VerticalBends { get; init; }
    public bool IsValid { get; init; }
    public List<string> ValidationIssues { get; } = new();
    public List<Vec3> CollisionPoints { get; } = new();
    public List<RouteSegment> FallbackLegs { get; } = new();
    public List<Vec3> VerticalBendPoints { get; } = new();

    /// <summary>
    /// One entry per <see cref="FinalSegments"/> element, naming why that segment's leading
    /// corner exists (see <see cref="SegmentReasons"/> tokens). Populated by the engine that
    /// produced the route (managed or native) so the viewer no longer has to re-infer it.
    /// </summary>
    public List<string> SegmentReasonCodes { get; } = new();
}

/// <summary>
/// Shared vocabulary for <see cref="RubberBandResult.SegmentReasonCodes"/>, used by both the
/// managed and native engines so a segment's reason is authoritative and identical regardless
/// of which engine produced the route.
/// </summary>
public static class SegmentReasons
{
    public const string RouteStart = "route_start";
    public const string StartDropStub = "start_drop_stub";
    public const string FeatureSnap = "feature_snap";
    public const string CollisionBypass = "collision_bypass";
    public const string DirectionChange = "direction_change";
    public const string ElevationChange = "elevation_change";
    public const string RubberAlignment = "rubber_alignment";

    /// <summary>Native engine reason codes (rb_get_segment_reason) in this exact order.</summary>
    public static readonly string[] NativeCodeOrder =
    {
        RouteStart, FeatureSnap, CollisionBypass, DirectionChange, ElevationChange, RubberAlignment
    };

    public static string FromNativeCode(int code) =>
        code >= 0 && code < NativeCodeOrder.Length ? NativeCodeOrder[code] : RubberAlignment;
}

/// <summary>
/// Role an existing-design control point plays when pulling the rubber line, per
/// RubberBandRouting_Development.md §5 (특징점 활용 방식).
/// </summary>
public enum RouteFeatureRole
{
    Unknown,
    StartStub,
    Bend,
    ElevationChange,
    TrunkGuide,
    EndApproach
}

/// <summary>
/// An existing-design control point the rubber line is pulled through, carrying enough meaning
/// (role/priority/required) that the engine and viewer no longer have to guess it from a bare
/// coordinate. Implicitly convertible to/from <see cref="Vec3"/> for call sites that only need
/// the position.
/// </summary>
public readonly record struct RouteFeature(Vec3 Position, RouteFeatureRole Role = RouteFeatureRole.Unknown, bool Required = false)
{
    public static implicit operator Vec3(RouteFeature f) => f.Position;
    public static implicit operator RouteFeature(Vec3 v) => new(v);
}

/// <summary>
/// Common contract implemented by both the managed (C#) and native (C++ via P/Invoke)
/// rubber-band routing engines, so the viewer can swap implementations freely.
/// </summary>
public interface IRubberBandEngine
{
    RubberBandResult Route(
        Vec3 start,
        Vec3 end,
        IEnumerable<Aabb> obstacles,
        IEnumerable<RouteFeature>? featureWaypoints = null,
        RubberBandOptions? options = null);
}
