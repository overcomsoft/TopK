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
        if (string.IsNullOrWhiteSpace(SettingsFilePath)) SettingsFilePath = ResolveNewSettingsPath();
        var directory = Path.GetDirectoryName(SettingsFilePath);
        if (!string.IsNullOrEmpty(directory)) Directory.CreateDirectory(directory);
        File.WriteAllText(SettingsFilePath, JsonSerializer.Serialize(this, JsonOptions));
    }

    public RerankWeights ToRerankWeights() =>
        new(WeightPosition, WeightPattern, WeightVector, WeightContext);

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
}

/// <summary>화면에 선택적으로 표시하는 BIM AABB.</summary>
public sealed record BimObstacle(string Type, Point3D Minimum, Point3D Maximum);
