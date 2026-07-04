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
    public int PipeCount { get; init; } = 3;
    public double SnapTolerance { get; init; } = 100.0;
    public int MaxPushIterations { get; init; } = 40;
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
}
