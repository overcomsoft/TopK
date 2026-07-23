using AutoRouteModule;
using AutoRouteModule.API;
using AutoRouteModule.Core;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Numerics;
using System.Threading.Tasks;

static void Assert(bool condition, string message)
{
    if (!condition)
        throw new InvalidOperationException(message);
}

static async Task<PathResult> FindStraightAsync(float y)
{
    return await AutoRouteAPI.FindPathAsync(
        new Vector3(0, y, 0),
        new Vector3(4, y, 0),
        DirectionType.Right,
        DirectionType.Right,
        1f);
}

Assert(
    PathFindOptions.Default.MaxSearchNodes == PathFindOptions.DEFAULT_MAX_SEARCH_NODES,
    "Default node limit is not applied.");
Assert(
    PathFindOptions.Default.TimeoutMilliseconds == PathFindOptions.DEFAULT_TIMEOUT_MILLISECONDS,
    "Default timeout is not applied.");

bool rejectedInvalidDiameter = false;
try
{
    await AutoRouteAPI.FindPathAsync(
        Vector3.Zero,
        Vector3.One,
        DirectionType.Right,
        DirectionType.Right,
        0f);
}
catch (ArgumentOutOfRangeException)
{
    rejectedInvalidDiameter = true;
}
Assert(rejectedInvalidDiameter, "Zero diameter must be rejected.");

AutoRouteAPI.ClearObstacles();
PathResult[] concurrent = await Task.WhenAll(
    FindStraightAsync(0f),
    FindStraightAsync(10f));
Assert(concurrent.All(r => r.ResultCode == RESULT_CODES.SUCCESS), "Concurrent straight searches failed.");
Assert(concurrent.All(r => r.WorldPath is { Count: >= 2 }), "Concurrent searches returned empty paths.");

var blockingObstacle = new OBB
{
    Center = new Vector3(1, 0, 0),
    Extents = new Vector3(0.45f),
    Axes = new[] { Vector3.UnitX, Vector3.UnitY, Vector3.UnitZ }
};
await AutoRouteAPI.InitStaticObstaclesAsync(new List<OBB> { blockingObstacle });
PathResult blockedStart = await AutoRouteAPI.FindPathAsync(
    Vector3.Zero,
    new Vector3(4, 0, 0),
    DirectionType.Right,
    DirectionType.Right,
    1f);
Assert(
    blockedStart.ResultCode == RESULT_CODES.FAIL_TO_START_POINT,
    $"Expected FAIL_TO_START_POINT, got {blockedStart.ResultCode}.");

AutoRouteAPI.ClearObstacles();
Console.WriteLine("AutoRouteModule smoke tests passed.");
