using AutoRouteModule;
using AutoRouteModule.API;
using AutoRouteModule.Core;
using System.Numerics;
using System.Threading;

namespace RubberBandRouting.Engine;

/// <summary>
/// AutoRouteModule의 3차원 voxel A* API를 공통 <see cref="IRubberBandEngine"/> 규격으로 변환한다.
///
/// 전체 흐름도:
/// [Viewer Route 요청]
/// → [Route: 동시 실행 잠금 및 예외 처리]
/// → [RouteCore: 특징점과 종단 접근점 분리]
/// → [AABB 확장 및 OBB 변환]
/// → [정적 장애물 초기화]
/// → [일반 또는 특징점 경유 A* 탐색]
/// → [ConvertResult: 공통 결과로 변환]
/// → [AppendEndApproach: 실제 종단 PoC 연결]
/// → [Viewer에 최종 세그먼트 반환]
///
/// AutoRouteModule은 프로세스 전역 장애물 저장소를 사용하므로 장애물 초기화와 탐색을
/// 하나의 임계 구역에서 순차 실행하여 다른 작업의 장애물 데이터가 섞이지 않게 한다.
/// </summary>
public sealed class AutoRouteModuleEngine : IRubberBandEngine
{
    /// <summary>전역 장애물 초기화와 경로 탐색을 직렬화하는 프로세스 공용 잠금.</summary>
    private static readonly SemaphoreSlim SearchGate = new(1, 1);

    /// <summary>
    /// Viewer가 호출하는 공개 진입점. 검색 잠금을 획득하고 내부 예외를 검증 오류로 변환한다.
    /// </summary>
    /// <param name="start">자동경로 시작 PoC 좌표(mm).</param>
    /// <param name="end">자동경로 최종 종단 PoC 좌표(mm).</param>
    /// <param name="obstacles">회피 대상 AABB 장애물 목록.</param>
    /// <param name="featureWaypoints">기존 설계에서 추출한 역할별 특징점 목록.</param>
    /// <param name="options">관경, 안전거리 및 경로 옵션.</param>
    public RubberBandResult Route(
        Vec3 start,
        Vec3 end,
        IEnumerable<Aabb> obstacles,
        IEnumerable<RouteFeature>? featureWaypoints = null,
        RubberBandOptions? options = null)
    {
        options ??= new RubberBandOptions();
        SearchGate.Wait();
        try
        {
            return RouteCore(start, end, obstacles, featureWaypoints, options);
        }
        catch (Exception ex)
        {
            var failed = new RubberBandResult { IsValid = false };
            failed.ValidationIssues.Add($"autoroute_exception:{ex.GetType().Name}:{ex.Message}");
            return failed;
        }
        finally
        {
            SearchGate.Release();
        }
    }

    /// <summary>
    /// 입력 모델을 AutoRouteModule 모델로 변환하고 실제 A* 탐색을 수행한다.
    /// 종단 PoC가 덕트 내부에 있으면 EndApproach까지만 탐색하고 마지막 구간은 별도로 연결한다.
    /// </summary>
    private static RubberBandResult RouteCore(
        Vec3 start,
        Vec3 end,
        IEnumerable<Aabb> obstacles,
        IEnumerable<RouteFeature>? featureWaypoints,
        RubberBandOptions options)
    {
        var features = (featureWaypoints ?? Enumerable.Empty<RouteFeature>())
            .Where(x => x.Required && IsFinite(x.Position))
            .ToList();

        // 실제 종단 PoC는 소유 덕트/장비 AABB의 표면 또는 내부에 있을 수 있다.
        // 실제 PoC까지 A* 검색하면 목표점 충돌로 거부될 수 있으므로 EndApproach를
        // 충돌 회피 검색의 끝점으로 사용하고 짧은 종단 인입부는 나중에 복원한다.
        var endApproach = features
            .Where(x => x.Role == RouteFeatureRole.EndApproach)
            .Select(x => (Vec3?)x.Position)
            .LastOrDefault();
        // searchEnd: A*가 실제로 도달해야 하는 충돌 자유 종점.
        var searchEnd = endApproach is { } approach &&
                        Distance(approach, start) > 1.0 &&
                        Distance(approach, end) > 1.0
            ? approach
            : end;

        // obstacleList: 안전여유까지 확장한 AutoRouteModule 형식의 정적 장애물.
        var obstacleList = obstacles
            .Where(x => !x.IsPenetration)
            .Select(x => ToObb(x.Expand(options.SafetyMargin, options.SafetyMargin)))
            .ToList();

        AutoRouteAPI.InitStaticObstaclesAsync(obstacleList).GetAwaiter().GetResult();
        AutoRouteAPI.ClearDynamicObstacles();

        // requiredWaypoints: 시작·종단과 중복되지 않는 필수 경유점.
        // EndApproach는 searchEnd로 사용하므로 경유점 목록에서는 제외한다.
        var requiredWaypoints = features
            .Where(x => x.Role != RouteFeatureRole.EndApproach)
            .Select(x => x.Position)
            .Where(x => Distance(x, start) > 1.0 && Distance(x, end) > 1.0)
            .Where(x => Distance(x, searchEnd) > 1.0)
            .Distinct()
            .Select(ToVector3)
            .ToList();

        float diameter = checked((float)options.PipeDiameter);
        var findOptions = new PathFindOptions(
            turnPenalty: 40,
            positivePenalty: Int3.Zero,
            negativePenalty: Int3.Zero,
            maxSearchNodes: PathFindOptions.DEFAULT_MAX_SEARCH_NODES,
            heuristicWeight: 1.1f,
            minStraightDistance: 0,
            timeoutMilliseconds: PathFindOptions.DEFAULT_TIMEOUT_MILLISECONDS);

        // 필수 경유점 유무에 따라 단일 구간 API 또는 waypoint API를 선택한다.
        PathResult pathResult = requiredWaypoints.Count == 0
            ? AutoRouteAPI.FindPathAsync(
                    ToVector3(start),
                    ToVector3(searchEnd),
                    DirectionType.None,
                    DirectionType.None,
                    diameter,
                    findOptions)
                .GetAwaiter().GetResult()
            : AutoRouteAPI.FindPathWithWaypointsAsync(
                    ToVector3(start),
                    requiredWaypoints,
                    ToVector3(searchEnd),
                    DirectionType.None,
                    DirectionType.None,
                    diameter,
                    findOptions)
                .GetAwaiter().GetResult();

        var result = ConvertResult(pathResult);
        return result.IsValid && Distance(searchEnd, end) > 1.0
            ? AppendEndApproach(result, searchEnd, end)
            : result;
    }

    /// <summary>
    /// A* 결과 뒤에 EndApproach → 실제 종단 PoC 고정 인입 구간을 추가하고
    /// 중심선, 병렬 배관 경로, 길이 및 세그먼트 사유 코드를 함께 갱신한다.
    /// </summary>
    private static RubberBandResult AppendEndApproach(RubberBandResult head, Vec3 approach, Vec3 end)
    {
        var terminal = new RouteSegment(approach, end);
        var result = new RubberBandResult
        {
            IsValid = head.IsValid,
            TotalLength = head.TotalLength + terminal.Length,
            VerticalBends = head.VerticalBends +
                (head.FinalSegments.Count > 0 &&
                 head.FinalSegments[^1].IsVertical != terminal.IsVertical ? 1 : 0)
        };

        foreach (var step in head.Steps) result.Steps.Add(step);
        result.FinalSegments.AddRange(head.FinalSegments);
        result.FinalSegments.Add(terminal);
        foreach (var pipe in head.PipePaths)
        {
            var extended = new List<Vec3>(pipe);
            if (extended.Count == 0 || Distance(extended[^1], approach) > 1.0)
                extended.Add(approach);
            extended.Add(end);
            result.PipePaths.Add(extended);
        }
        result.ValidationIssues.AddRange(head.ValidationIssues);
        result.CollisionPoints.AddRange(head.CollisionPoints);
        result.FallbackLegs.AddRange(head.FallbackLegs);
        result.VerticalBendPoints.AddRange(head.VerticalBendPoints);
        result.SegmentReasonCodes.AddRange(head.SegmentReasonCodes);
        result.SegmentReasonCodes.Add(SegmentReasons.DirectionChange);
        return result;
    }

    /// <summary>
    /// AutoRouteModule의 결과 코드와 WorldPath를 Viewer 공통 결과로 변환한다.
    /// 실패하면 원본 결과 코드와 로그 경로를 ValidationIssues에 보존한다.
    /// </summary>
    private static RubberBandResult ConvertResult(PathResult source)
    {
        if (source.ResultCode != RESULT_CODES.SUCCESS || source.WorldPath is not { Count: >= 2 })
        {
            var failed = new RubberBandResult { IsValid = false };
            failed.ValidationIssues.Add($"autoroute_result:{source.ResultCode}");
            if (!string.IsNullOrWhiteSpace(source.LogFilePath))
                failed.ValidationIssues.Add($"autoroute_log:{source.LogFilePath}");
            return failed;
        }

        var points = source.WorldPath.Select(ToVec3).ToList();
        var segments = new List<RouteSegment>(points.Count - 1);
        double totalLength = 0;
        for (int i = 0; i < points.Count - 1; i++)
        {
            if (Distance(points[i], points[i + 1]) <= 1e-6)
                continue;
            var segment = new RouteSegment(points[i], points[i + 1]);
            segments.Add(segment);
            totalLength += segment.Length;
        }

        if (segments.Count == 0)
        {
            var failed = new RubberBandResult { IsValid = false };
            failed.ValidationIssues.Add("autoroute_result:empty_path");
            return failed;
        }

        var result = new RubberBandResult
        {
            IsValid = true,
            TotalLength = totalLength
        };
        result.FinalSegments.AddRange(segments);
        result.PipePaths.Add(points);
        for (int i = 0; i < segments.Count; i++)
        {
            result.SegmentReasonCodes.Add(
                i == 0 ? SegmentReasons.RouteStart : SegmentReasons.DirectionChange);
        }
        return result;
    }

    /// <summary>축 정렬 AABB를 단위 회전축을 가진 AutoRouteModule OBB로 변환한다.</summary>
    private static OBB ToObb(Aabb box)
    {
        Vec3 size = box.Max - box.Min;
        if (size.X < 0 || size.Y < 0 || size.Z < 0)
            throw new ArgumentException($"Invalid AABB '{box.Name}': Min must not exceed Max.");

        return new OBB
        {
            Center = ToVector3(box.Center),
            Extents = new Vector3(
                checked((float)(size.X * 0.5)),
                checked((float)(size.Y * 0.5)),
                checked((float)(size.Z * 0.5))),
            Axes = new[] { Vector3.UnitX, Vector3.UnitY, Vector3.UnitZ }
        };
    }

    /// <summary>Viewer의 double 좌표를 AutoRouteModule의 float 좌표로 변환한다.</summary>
    private static Vector3 ToVector3(Vec3 value) => new(
        checked((float)value.X),
        checked((float)value.Y),
        checked((float)value.Z));

    /// <summary>AutoRouteModule 좌표를 Viewer 공통 좌표로 변환한다.</summary>
    private static Vec3 ToVec3(Vector3 value) => new(value.X, value.Y, value.Z);

    /// <summary>NaN 또는 무한대가 포함된 잘못된 특징점을 차단한다.</summary>
    private static bool IsFinite(Vec3 value) =>
        double.IsFinite(value.X) && double.IsFinite(value.Y) && double.IsFinite(value.Z);

    /// <summary>두 3차원 좌표 사이의 유클리드 거리를 mm 단위로 계산한다.</summary>
    private static double Distance(Vec3 a, Vec3 b) => (a - b).Length;
}
