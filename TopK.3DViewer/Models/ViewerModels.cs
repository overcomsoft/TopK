using System.Text.Json;
using System.Text.Json.Serialization;
using System.IO;
using System.Windows.Media.Media3D;
using RoutingAI.Standalone;

namespace TopK.ThreeDViewer.Models;

/// <summary>화면과 서비스가 공유하는 DB 및 검색 기본 설정.</summary>
public sealed class ViewerSettings
{
    public string Host { get; set; } = "localhost";
    public int Port { get; set; } = 5432;
    public string Database { get; set; } = "DDW_AI_DB";
    public string User { get; set; } = "dinno";
    public string Password { get; set; } = "dinno";
    public int DefaultK { get; set; } = 5;
    public bool UseObstacleContext { get; set; } = true;
    public int ObstacleLimit { get; set; } = 2500;
    public double WeightPosition { get; set; } = 25;
    public double WeightPattern { get; set; } = 25;
    public double WeightVector { get; set; } = 25;
    public double WeightContext { get; set; } = 25;
    public bool RedistributeMissingPatternWeight { get; set; } = true;
    public string SearchUnit { get; set; } = "Individual";
    public string GroupSizeMatchMode { get; set; } = nameof(RoutingAI.Standalone.GroupSizeMatchMode.PreferExact);
    public double GroupMatchedWeight { get; set; } = 80;
    public double GroupArrangementWeight { get; set; } = 20;
    public string GroupComparisonView { get; set; } = "Original";
    public bool ShowUnmatchedGroupMembers { get; set; } = true;
    public string PythonExe { get; set; } = "python";

    /// <summary>실제로 읽었거나 새로 생성할 설정 파일. JSON에는 기록하지 않는다.</summary>
    [JsonIgnore]
    public string SettingsFilePath { get; private set; } = "";

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true
    };

    public static ViewerSettings Load()
    {
        var candidates = new[]
        {
            Path.Combine(AppContext.BaseDirectory, "viewer.settings.json"),
            Path.Combine(Environment.CurrentDirectory, "TopK.3DViewer", "viewer.settings.json"),
            Path.Combine(Environment.CurrentDirectory, "viewer.settings.json")
        };
        var path = candidates.FirstOrDefault(File.Exists) ?? ResolveNewSettingsPath();
        var settings = File.Exists(path)
            ? JsonSerializer.Deserialize<ViewerSettings>(File.ReadAllText(path), JsonOptions) ?? new ViewerSettings()
            : new ViewerSettings();
        settings.SettingsFilePath = path;
        settings.EqualizeEnabledWeights();
        settings.NormalizeGroupWeights();
        return settings;
    }

    /// <summary>
    /// 0보다 큰 가중치는 모두 활성 항목으로 간주하고 100/N(%)로 균등 배분한다.
    /// 0은 명시적인 비활성 값이며, 네 항목이 모두 0이면 검색 의미가 없으므로 거부한다.
    /// </summary>
    public void EqualizeEnabledWeights()
    {
        var values = new[] { WeightPosition, WeightPattern, WeightVector, WeightContext };
        if (values.Any(value => !double.IsFinite(value) || value < 0))
            throw new InvalidDataException("유사도 가중치는 0 이상의 유한수여야 합니다.");

        var activeCount = values.Count(value => value > 0);
        if (activeCount == 0)
            throw new InvalidDataException("Position, Pattern, Feature, Context 중 하나 이상의 가중치는 0보다 커야 합니다.");

        var equalWeight = 100.0 / activeCount;
        WeightPosition = WeightPosition > 0 ? equalWeight : 0;
        WeightPattern = WeightPattern > 0 ? equalWeight : 0;
        WeightVector = WeightVector > 0 ? equalWeight : 0;
        WeightContext = WeightContext > 0 ? equalWeight : 0;
    }

    /// <summary>현재 UI 설정을 viewer.settings.json에 저장한다.</summary>
    public void Save()
    {
        EqualizeEnabledWeights();
        NormalizeGroupWeights();
        if (string.IsNullOrWhiteSpace(SettingsFilePath)) SettingsFilePath = ResolveNewSettingsPath();
        var directory = Path.GetDirectoryName(SettingsFilePath);
        if (!string.IsNullOrEmpty(directory)) Directory.CreateDirectory(directory);
        File.WriteAllText(SettingsFilePath, JsonSerializer.Serialize(this, JsonOptions));
    }

    public RerankWeights ToRerankWeights() =>
        new(WeightPosition, WeightPattern, WeightVector, WeightContext);

    /// <summary>그룹의 Pair 평균과 배치 유사도 가중치를 합계 100%로 정규화한다.</summary>
    public void NormalizeGroupWeights()
    {
        if (!double.IsFinite(GroupMatchedWeight) || !double.IsFinite(GroupArrangementWeight) ||
            GroupMatchedWeight < 0 || GroupArrangementWeight < 0 ||
            GroupMatchedWeight + GroupArrangementWeight <= 0)
            throw new InvalidDataException("그룹 Match/Arrangement 가중치는 0 이상이며 합계가 0보다 커야 합니다.");
        var sum = GroupMatchedWeight + GroupArrangementWeight;
        GroupMatchedWeight = GroupMatchedWeight / sum * 100.0;
        GroupArrangementWeight = GroupArrangementWeight / sum * 100.0;

        if (!Enum.TryParse<RoutingAI.Standalone.GroupSizeMatchMode>(GroupSizeMatchMode, true, out _))
            GroupSizeMatchMode = nameof(RoutingAI.Standalone.GroupSizeMatchMode.PreferExact);
        if (!GroupComparisonView.Equals("Original", StringComparison.OrdinalIgnoreCase) &&
            !GroupComparisonView.Equals("SideBySide", StringComparison.OrdinalIgnoreCase))
            GroupComparisonView = "Original";
        SearchUnit = SearchUnit.Equals("Group", StringComparison.OrdinalIgnoreCase) ? "Group" : "Individual";
    }

    private static string ResolveNewSettingsPath()
    {
        var projectDirectory = Path.Combine(Environment.CurrentDirectory, "TopK.3DViewer");
        return Directory.Exists(projectDirectory)
            ? Path.Combine(projectDirectory, "viewer.settings.json")
            : Path.Combine(AppContext.BaseDirectory, "viewer.settings.json");
    }

    public DbConfig ToDbConfig() => new(Host, Port, Database, User, Password);
    public string ToConnectionString() => ToDbConfig().ToConnectionString();
}

/// <summary>검색 필터 ComboBox를 채우는 distinct 값 모음.</summary>
public sealed record FilterCatalog(
    IReadOnlyList<string> Processes,
    IReadOnlyList<string> Equipments,
    IReadOnlyList<string> UtilityGroups,
    IReadOnlyList<string> Utilities,
    IReadOnlyList<string> Sizes);

/// <summary>TB_ROUTE_PATH 한 행을 검색조건으로 재사용하기 위한 프리셋.</summary>
public sealed record RoutePresetItem(
    string RoutePathGuid,
    string Display,
    string Process,
    string Equipment,
    string UtilityGroup,
    string Utility,
    string Size,
    Point3D Start,
    Point3D End);

/// <summary>한 Top-K 결과와 DB에서 읽은 실제 3D polyline을 결합한 화면 항목.</summary>
public sealed class TopKRouteItem
{
    public required SearchResult Search { get; init; }
    public List<Point3D> Points { get; init; } = [];
    public string GeometrySource { get; init; } = "DB 상세경로";
    public bool IsExactGeometry { get; init; } = true;
    public string GeometryLabel => IsExactGeometry ? "실제" : "재구성";
    public int Rank => Search.Rank;
    public string Guid => Search.RoutePathGuid;
    public double Score => Search.SimilarityScore;
    public double ContextScore => Search.ScoreContext;
    public string Equipment => Search.EquipmentName;
    public string Utility => Search.Utility;
    public string Size => Search.Size;
    public double Length => Search.TotalLengthMm;
    public string Pattern => Search.DirectionPattern;
    public string ShortGuid => Guid.Length > 12 ? Guid[..12] + "…" : Guid;

    /// <summary>종단 PoC 대상 객체 이름(TB_ROUTE_PATH.TARGET_OWNER_NAME). ViewerDatabaseService.
    /// LoadRouteEndpointsBatchAsync로 검색 직후 일괄 조회해 채운다.</summary>
    public string TargetOwnerName { get; init; } = "";
    /// <summary>종단 객체 분류: MAIN_EQUIPMENT/AUX_EQUIPMENT/DUCT/LATERAL 중 하나, 미확인이면 "".</summary>
    public string TargetKind { get; init; } = "";
    public string TargetDisplay => TargetOwnerName.Length == 0 ? "" : $"{TargetOwnerName} ({RouteEndpointInfo.KindLabel(TargetKind)})";
}

/// <summary>
/// UtilityPipeGroup 검색 결과와 후보 그룹 각 배관의 실제 DB polyline을 결합한 화면 항목.
/// PointsByRouteGuid는 원좌표를 보존하며 Side-by-Side 이동은 렌더링 직전에만 적용한다.
/// </summary>
public sealed class TopKGroupItem
{
    public required UtilityPipeGroupSearchResult Search { get; init; }
    public Dictionary<string, List<Point3D>> PointsByRouteGuid { get; init; } =
        new(StringComparer.OrdinalIgnoreCase);
    public HashSet<string> ReconstructedRouteGuids { get; init; } =
        new(StringComparer.OrdinalIgnoreCase);
    public int Rank => Search.Rank;
    public double Score => Search.GroupSimilarity;
    public double Matched => Search.MatchedAverage;
    public double Coverage => Search.Coverage;
    public double Arrangement => Search.Arrangement;
    public string Equipment => Search.Candidate.EquipmentInstanceKey;
    public string Utility => Search.Candidate.Utility;
    public int PipeCount => Search.Candidate.MemberCount;
    public string MatchedLabel => $"{Search.Matches.Count}/{Search.UnmatchedQueryMembers.Count + Search.Matches.Count}/" +
                                  $"{Search.UnmatchedCandidateMembers.Count + Search.Matches.Count}";
    public string Sizes => string.Join(", ", Search.Candidate.SizeSignature
        .OrderBy(item => item.Key, StringComparer.OrdinalIgnoreCase)
        .Select(item => $"{item.Key}:{item.Value}"));
    public string GroupId => Search.Candidate.GroupVectorId;
    public int ReconstructedCount => ReconstructedRouteGuids.Count;
    public int ExactGeometryCount => Math.Max(0, PointsByRouteGuid.Count - ReconstructedCount);
    public string GeometryLabel => ReconstructedCount == 0 ? "실제" : $"재구성 {ReconstructedCount}";

    /// <summary>그룹 멤버 route guid → 종단 PoC 대상 객체 요약. ViewerDatabaseService.
    /// LoadRouteEndpointsBatchAsync로 후보 그룹의 전체 멤버를 일괄 조회해 채운다.</summary>
    public IReadOnlyDictionary<string, RouteEndpointInfo> EndpointsByRouteGuid { get; init; } =
        new Dictionary<string, RouteEndpointInfo>(StringComparer.OrdinalIgnoreCase);

    /// <summary>멤버들의 종단 객체가 모두 같으면 "이름 (종류)", 섞여 있으면 종류별 건수 요약.</summary>
    public string TargetSummary
    {
        get
        {
            var entries = Search.Candidate.Members
                .Select(m => EndpointsByRouteGuid.TryGetValue(m.RoutePathGuid, out var info) ? info : null)
                .Where(info => info is not null && info.TargetOwnerName.Length > 0)
                .Select(info => info!)
                .ToList();
            if (entries.Count == 0) return "";
            var distinctNames = entries.Select(e => e.TargetOwnerName)
                .Distinct(StringComparer.OrdinalIgnoreCase).ToList();
            if (distinctNames.Count == 1) return entries[0].TargetDisplay;
            return string.Join(" · ", entries
                .GroupBy(e => e.TargetKind)
                .Select(g => $"{RouteEndpointInfo.KindLabel(g.Key)} {g.Count()}"));
        }
    }
}

/// <summary>그룹 프리셋의 멤버 표시에 사용하는 읽기 전용 행.</summary>
public sealed record UtilityPipeGroupMemberRow(
    int Order,
    string RoutePathGuid,
    string Size,
    string Pattern,
    double LengthMm)
{
    public string ShortGuid => RoutePathGuid.Length > 12 ? RoutePathGuid[..12] + "…" : RoutePathGuid;
}

/// <summary>화면에 선택적으로 표시하는 BIM AABB.</summary>
public sealed record BimObstacle(string Type, Point3D Minimum, Point3D Maximum);

/// <summary>TB_ROUTE_PATH 한 건의 시작 PoC(메인장비)/종단 PoC(대상 객체) 요약. 종단 객체 이름을
/// TB_EQUIPMENTS/TB_DUCT/TB_LATERAL_PIPE와 대조해 분류한다(AutoRouteFinder/ObstacleDbLoader.cs의
/// MAIN_SUB_TYPE="MainTool" 판정과 동일 규칙).</summary>
public sealed record RouteEndpointInfo(string SourceEquipmentName, string TargetOwnerName, string TargetKind)
{
    public static string KindLabel(string kind) => kind switch
    {
        "MAIN_EQUIPMENT" => "메인장비",
        "AUX_EQUIPMENT" => "부대장비",
        "DUCT" => "덕트",
        "LATERAL" => "레터럴배관",
        _ => "미확인"
    };

    public string TargetKindLabel => KindLabel(TargetKind);
    public string TargetDisplay => TargetOwnerName.Length == 0 ? "" : $"{TargetOwnerName} ({TargetKindLabel})";
}

/// <summary>TB_ROUTE_BEND_FEATURE_POINT 한 행을 3D 마커로 표시하기 위한 최소 필드셋.</summary>
public sealed record BendFeatureMarker(
    string RouteGuid, string Cause, string TransitionType, string SegmentZone, Point3D Point);

/// <summary>TB_ROUTE_PATH_SEGMENTATION 한 행 — 선택된 route에 오버레이로 겹쳐 그리는
/// Start Stub/Middle Trunk/End Stub 3구간 폴리라인.</summary>
public sealed record PathSegmentGeometry(
    string RouteGuid,
    IReadOnlyList<Point3D> StartStub,
    IReadOnlyList<Point3D> MiddleTrunk,
    IReadOnlyList<Point3D> EndStub);

/// <summary>Group/Bundle Pattern 검색 결과 한 건을 화면에 표시하기 위한 래퍼.</summary>
public sealed class GroupPatternResultItem
{
    public required GroupPatternSearchResult Search { get; init; }
    public int Rank => Search.Rank;
    public double Score => Search.Similarity;
    public string GroupId => Search.Candidate.GroupId;
    public string Equipment => Search.Candidate.EquipmentTag;
    public string UtilityGroup => Search.Candidate.UtilityGroup;
    public string Utility => Search.Candidate.Utility;
    public int NMembers => Search.Candidate.NMembers;
    public double PitchMm => Search.Candidate.PitchMm;
    public string SpacingLabel => Search.Candidate.IsEqualSpacing ? "등간격" : "비등간격";
    public string OffsetAxis => Search.Candidate.OffsetAxis;
}

/// <summary>Stub Pattern 검색 결과 한 건을 화면에 표시하기 위한 래퍼.</summary>
public sealed class StubPatternResultItem
{
    public required StubPatternSearchResult Search { get; init; }
    public int Rank => Search.Rank;
    public double Score => Search.Similarity;
    public double FeatureScore => Search.FeatureSimilarity;
    public double DirectionScore => Search.DirectionSimilarity;
    public string PatternId => Search.Candidate.PatternId;
    public string MainEquipmentName => Search.Candidate.MainEquipmentName;
    public string UtilityGroup => Search.Candidate.UtilityGroup;
    public string Utility => Search.Candidate.Utility;
    public string Size => Search.Candidate.Size;
    public string Face => Search.Candidate.Face;
    public string DirSeq => Search.Candidate.DirSeq;
    public List<Point3D> StubPoints =>
        Search.Candidate.StubPoints.Select(p => new Point3D(p.X, p.Y, p.Z)).ToList();
    public Point3D AnchorMin => new(Search.Candidate.AnchorMin.X, Search.Candidate.AnchorMin.Y, Search.Candidate.AnchorMin.Z);
    public Point3D AnchorMax => new(Search.Candidate.AnchorMax.X, Search.Candidate.AnchorMax.Y, Search.Candidate.AnchorMax.Z);
}
