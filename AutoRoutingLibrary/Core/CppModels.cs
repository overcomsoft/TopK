using System;
using System.Collections.Generic;

namespace AutoRoutingLibrary.Core
{
    /// <summary>A grid cell in integer I/J/K coordinates.</summary>
    public readonly record struct PathCell(int I, int J, int K);

    /// <summary>A 3D point or size expressed in millimeters.</summary>
    public readonly record struct Vec3(double X, double Y, double Z);

    /// <summary>An axis-aligned bounding box expressed in millimeters.</summary>
    public readonly record struct Aabb(Vec3 Min, Vec3 Max);

    /// <summary>Routing grid definition.</summary>
    public readonly record struct RoutingGrid(double CellMm, Vec3 Origin, int Nx, int Ny, int Nz);

    /// <summary>Native routing status codes returned by the C ABI.</summary>
    public enum R3dStatus
    {
        Ok = 0,
        InvalidArgument = 1,
        ParseError = 2,
        RuntimeError = 3,
        RangeError = 4
    }

    /// <summary>Failure reason reported by the native route engine.</summary>
    public enum RouteFail
    {
        None = 0,
        StartBlocked = 1,
        GoalBlocked = 2,
        CorridorMiss = 3,
        ExpansionLimit = 4,
        GoalDirBlocked = 5,
        NoPath = 6
    }

    /// <summary>Optional goal entry direction. Values map to the native 6-neighbor axis order.</summary>
    public enum GoalDirection
    {
        Any = -1,
        PositiveX = 0,
        NegativeX = 1,
        PositiveY = 2,
        NegativeY = 3,
        PositiveZ = 4,
        NegativeZ = 5
    }

    /// <summary>Routing cost and behavior parameters.</summary>
    public sealed class RoutingParameters
    {
        public double CellMm { get; init; } = 50.0;
        public double TurnCostMm { get; init; } = 500.0;
        public double ClearanceCostMm { get; init; } = 10.0;
        public int ClearanceRadiusCells { get; init; } = 2;
        public int ClearanceConnectivity { get; init; } = 6;
        public double CorridorCostMm { get; init; }
        public int CorridorRadiusCells { get; init; } = 1;
        public double HeuristicWeight { get; init; } = 1.0;
        public double NearGoalHeuristicWeight { get; init; }
        public IReadOnlyList<int> RackLevels { get; init; } = Array.Empty<int>();
    }

    /// <summary>Result for one routed task.</summary>
    public sealed class CppRouteResult
    {
        public bool Success { get; init; }
        public double LengthMm { get; init; }
        public double CostMm { get; init; }
        public int Turns { get; init; }
        public long ExpandedNodes { get; init; }
        public double ElapsedMs { get; init; }
        public RouteFail Fail { get; init; }
        public IReadOnlyList<PathCell> Path { get; init; } = Array.Empty<PathCell>();
        public IReadOnlyList<PathCell> Visited { get; init; } = Array.Empty<PathCell>();
    }

    /// <summary>Progress event emitted by RouteMultiProgress.</summary>
    public readonly record struct RouteProgress(
        int Phase,
        int OrderIndex,
        int TaskIndex,
        bool Success,
        double LengthMm,
        int Turns,
        long ExpandedNodes,
        double ElapsedMs,
        int Done,
        int Total,
        double Progress01,
        IReadOnlyList<PathCell> Path);

    /// <summary>Exception thrown when the native routing engine returns a non-zero status.</summary>
    public sealed class Routing3DException : Exception
    {
        public Routing3DException(R3dStatus status, string operation)
            : base($"Native Routing3D operation '{operation}' failed with status {(int)status} ({status}).")
        {
            Status = status;
            Operation = operation;
        }

        public R3dStatus Status { get; }
        public string Operation { get; }
    }
}
