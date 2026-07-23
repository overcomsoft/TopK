using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using Npgsql;

namespace RoutingAI.Standalone;

// 실행 예시(코드 참고용 — 이 클래스는 CLI가 아니라 TopK.3DViewer가 직접 호출하는 라이브러리다):
//   var presets = await StubPatternSearch.FetchPresetsAsync(db, stubKind: "START");
//   var (results, meta) = await StubPatternSearch.SearchAsync(db, presets[0].PatternId);

/// <summary>
/// TB_ROUTE_STUB_PATTERN의 24D FEAT(면/방향/앵커상대좌표/진행방향, HNSW L2)와 3D DIR_UNIT
/// (진행방향, HNSW cosine)으로 Stub 패턴 Top-K 유사도 검색을 수행한다.
/// Docs/FeaturePattern_Pipeline_Overlap_Review.md 2.4절 기준으로 이 테이블도 지금까지 어떤
/// 검색 코드에서도 조회된 적이 없었다.
///
/// 쿼리는 FEAT의 pgvector ANN으로 넓게 후보를 뽑은 뒤, 애플리케이션 코드에서 DIR_UNIT
/// 코사인유사도를 얹어 재정렬한다 — route Top-K가 1차 ANN(FEATURE_VECTOR)과 재정렬(패턴/컨텍스트)을
/// 분리하는 것과 같은 아이디어다(TopKSearchStandalone.cs 참고).
/// </summary>
public static class StubPatternSearch
{
    public static async Task<StubPatternDescriptor?> LoadAsync(DbConfig db, string patternId)
    {
        if (string.IsNullOrWhiteSpace(patternId))
            throw new ArgumentException("patternId가 필요합니다.", nameof(patternId));
        await using var connection = new NpgsqlConnection(db.ToConnectionString());
        await connection.OpenAsync().ConfigureAwait(false);
        return await LoadHeaderAsync(connection, patternId.Trim()).ConfigureAwait(false);
    }

    public static async Task<IReadOnlyList<StubPatternPreset>> FetchPresetsAsync(
        DbConfig db,
        string stubKind = "",
        string mainEquipmentName = "",
        string utilityGroup = "",
        string utility = "",
        int limit = 500)
    {
        await using var connection = new NpgsqlConnection(db.ToConnectionString());
        await connection.OpenAsync().ConfigureAwait(false);
        var where = new List<string>();
        await using var command = new NpgsqlCommand { Connection = connection };
        AddOptionalFilter(command, where, "STUB_KIND", "stubKind", stubKind.Trim());
        AddOptionalFilter(command, where, "MAIN_EQUIPMENT_NAME", "mainEquipmentName", mainEquipmentName.Trim());
        AddOptionalFilter(command, where, "UTILITY_GROUP", "utilityGroup", utilityGroup.Trim());
        AddOptionalFilter(command, where, "UTILITY", "utility", utility.Trim());
        command.Parameters.AddWithValue("limit", Math.Clamp(limit, 1, 5000));
        command.CommandText = $"""
            SELECT "PATTERN_ID","STUB_KIND","ANCHOR_KIND","MAIN_EQUIPMENT_NAME","UTILITY_GROUP","UTILITY",
                   "SIZE","FACE","DIR_SEQ"
              FROM "TB_ROUTE_STUB_PATTERN"
              {(where.Count > 0 ? "WHERE " + string.Join(" AND ", where) : "")}
             ORDER BY "MAIN_EQUIPMENT_NAME","UTILITY_GROUP","UTILITY","STUB_KIND"
             LIMIT @limit
            """;
        var result = new List<StubPatternPreset>();
        await using var reader = await command.ExecuteReaderAsync().ConfigureAwait(false);
        while (await reader.ReadAsync().ConfigureAwait(false))
        {
            var display = $"{Text(reader, 1)} | {Text(reader, 3)} | {Text(reader, 4)}/{Text(reader, 5)} | " +
                          $"{Text(reader, 6)} | Face {Text(reader, 7)} | {Text(reader, 8)}";
            result.Add(new(Text(reader, 0), Text(reader, 1), Text(reader, 2), Text(reader, 3), Text(reader, 4),
                Text(reader, 5), Text(reader, 6), Text(reader, 7), Text(reader, 8), display));
        }
        return result;
    }

    /// <summary>
    /// queryPatternId 행과 같은 STUB_KIND/ANCHOR_KIND 안에서 FEAT ANN으로 후보를 넓게 뽑고,
    /// DIR_UNIT 코사인유사도를 가중 결합해 재정렬한다.
    /// </summary>
    public static async Task<(IReadOnlyList<StubPatternSearchResult> Results, StubPatternSearchMeta Meta)> SearchAsync(
        DbConfig db,
        string queryPatternId,
        StubPatternSearchOptions? options = null)
    {
        options ??= new StubPatternSearchOptions();
        if (options.K < 1) throw new ArgumentOutOfRangeException(nameof(options), "K는 1 이상이어야 합니다.");
        await using var connection = new NpgsqlConnection(db.ToConnectionString());
        await connection.OpenAsync().ConfigureAwait(false);
        var stopwatch = Stopwatch.StartNew();

        var queryHeader = await LoadHeaderAsync(connection, queryPatternId.Trim()).ConfigureAwait(false)
            ?? throw new InvalidOperationException($"Stub Pattern이 없습니다: {queryPatternId}");
        var fetchN = Math.Clamp(
            Math.Max(options.K * options.CandidateFetchMultiplier, options.CandidateFetchMinimum),
            1, options.CandidateFetchMaximum);

        var sql = HeaderSelectSql.Replace(
            "0.0::double precision AS ann_distance",
            "\"FEAT\" <-> @queryVector::vector AS ann_distance",
            StringComparison.Ordinal) + """
             WHERE "STUB_KIND"=@stubKind AND "ANCHOR_KIND"=@anchorKind AND "PATTERN_ID"<>@queryId
             ORDER BY "FEAT" <-> @queryVector::vector, "PATTERN_ID"
             LIMIT @fetchN
            """;
        await using var command = new NpgsqlCommand(sql, connection);
        command.Parameters.AddWithValue("stubKind", queryHeader.StubKind);
        command.Parameters.AddWithValue("anchorKind", queryHeader.AnchorKind);
        command.Parameters.AddWithValue("queryId", queryHeader.PatternId);
        command.Parameters.AddWithValue("queryVector", ToVectorLiteral(queryHeader.Feat));
        command.Parameters.AddWithValue("fetchN", fetchN);
        var candidates = new List<StubPatternDescriptor>();
        await using (var reader = await command.ExecuteReaderAsync().ConfigureAwait(false))
        {
            while (await reader.ReadAsync().ConfigureAwait(false)) candidates.Add(ReadHeader(reader));
        }

        var scored = candidates
            .Select(candidate =>
            {
                var featureSimilarity = 1.0 / (1.0 + candidate.AnnDistance);
                var directionSimilarity = CosineSimilarity(queryHeader.DirUnit, candidate.DirUnit);
                var similarity = options.FeatureWeight * featureSimilarity + options.DirectionWeight * directionSimilarity;
                return new StubPatternSearchResult(0, candidate, featureSimilarity, directionSimilarity, similarity);
            })
            .OrderByDescending(result => result.Similarity)
            .ThenBy(result => result.Candidate.PatternId, StringComparer.Ordinal)
            .Take(options.K)
            .Select((result, index) => result with { Rank = index + 1 })
            .ToArray();
        stopwatch.Stop();

        var meta = new StubPatternSearchMeta(
            queryHeader.PatternId, options.K, fetchN, candidates.Count, scored.Length,
            stopwatch.Elapsed.TotalMilliseconds);
        return (scored, meta);
    }

    private const string HeaderSelectSql = """
        SELECT "PATTERN_ID","ROUTE_PATH_GUID","STUB_KIND","ANCHOR_KIND","ANCHOR_NAME",
               "MAIN_EQUIPMENT_NAME","PROCESS_NAME","UTILITY_GROUP","UTILITY","SIZE",
               "FACE","DIR_SEQ","N_BENDS","RISE_MM","OFFSET_MM","DIAMETER_MM","STUB_LENGTH_MM",
               "ANCHOR_MIN"::text,"ANCHOR_MAX"::text,"STUB_POINTS"::text,
               "FEAT"::text,"DIR_UNIT"::text,0.0::double precision AS ann_distance
          FROM "TB_ROUTE_STUB_PATTERN"
        """;

    private static async Task<StubPatternDescriptor?> LoadHeaderAsync(NpgsqlConnection connection, string patternId)
    {
        var sql = HeaderSelectSql + " WHERE \"PATTERN_ID\"=@patternId";
        await using var command = new NpgsqlCommand(sql, connection);
        command.Parameters.AddWithValue("patternId", patternId);
        await using var reader = await command.ExecuteReaderAsync().ConfigureAwait(false);
        return await reader.ReadAsync().ConfigureAwait(false) ? ReadHeader(reader) : null;
    }

    private static StubPatternDescriptor ReadHeader(NpgsqlDataReader reader) => new(
        Text(reader, 0), Text(reader, 1), Text(reader, 2), Text(reader, 3), Text(reader, 4),
        Text(reader, 5), Text(reader, 6), Text(reader, 7), Text(reader, 8), Text(reader, 9),
        Text(reader, 10), Text(reader, 11), reader.IsDBNull(12) ? 0 : reader.GetInt32(12),
        Number(reader, 13), Number(reader, 14), Number(reader, 15), Number(reader, 16),
        ParseJsonPoint(Text(reader, 17)), ParseJsonPoint(Text(reader, 18)),
        ParseJsonPointArray(Text(reader, 19)),
        ParseVector(Text(reader, 20), 24), ParseVector(Text(reader, 21), 3),
        Number(reader, 22));

    private static double CosineSimilarity(IReadOnlyList<double> a, IReadOnlyList<double> b)
    {
        if (a.Count != b.Count || a.Count == 0) return 0.0;
        double dot = 0, normA = 0, normB = 0;
        for (var i = 0; i < a.Count; i++)
        {
            dot += a[i] * b[i];
            normA += a[i] * a[i];
            normB += b[i] * b[i];
        }
        if (normA <= 1e-12 || normB <= 1e-12) return 0.0;
        return Math.Clamp(dot / (Math.Sqrt(normA) * Math.Sqrt(normB)), -1.0, 1.0);
    }

    private static void AddOptionalFilter(NpgsqlCommand command, List<string> where,
        string column, string parameter, string value)
    {
        if (string.IsNullOrWhiteSpace(value)) return;
        where.Add($"\"{column}\"=@{parameter}");
        command.Parameters.AddWithValue(parameter, value);
    }

    private static (double X, double Y, double Z) ParseJsonPoint(string json)
    {
        if (string.IsNullOrWhiteSpace(json)) return (0, 0, 0);
        var values = JsonSerializer.Deserialize<double[]>(json) ?? [];
        return values.Length >= 3 ? (values[0], values[1], values[2]) : (0, 0, 0);
    }

    private static IReadOnlyList<(double X, double Y, double Z)> ParseJsonPointArray(string json)
    {
        if (string.IsNullOrWhiteSpace(json)) return [];
        var values = JsonSerializer.Deserialize<double[][]>(json) ?? [];
        return values.Where(point => point.Length >= 3)
            .Select(point => (point[0], point[1], point[2])).ToArray();
    }

    private static double[] ParseVector(string text, int dimension)
    {
        var values = text.Trim().TrimStart('[').TrimEnd(']').Split(',', StringSplitOptions.RemoveEmptyEntries)
            .Select(value => double.Parse(value, CultureInfo.InvariantCulture)).ToArray();
        if (values.Length != dimension || values.Any(value => !double.IsFinite(value)))
            throw new InvalidOperationException($"Vector 차원이 올바르지 않습니다. expected={dimension}, actual={values.Length}");
        return values;
    }

    private static string ToVectorLiteral(IReadOnlyList<double> vector)
    {
        var builder = new StringBuilder("[");
        for (var index = 0; index < vector.Count; index++)
        {
            if (index > 0) builder.Append(',');
            builder.Append(vector[index].ToString("G17", CultureInfo.InvariantCulture));
        }
        return builder.Append(']').ToString();
    }

    private static double Number(NpgsqlDataReader reader, int ordinal) =>
        reader.IsDBNull(ordinal) ? 0.0 : Convert.ToDouble(reader.GetValue(ordinal), CultureInfo.InvariantCulture);
    private static string Text(NpgsqlDataReader reader, int ordinal) =>
        reader.IsDBNull(ordinal) ? "" : Convert.ToString(reader.GetValue(ordinal), CultureInfo.InvariantCulture)?.Trim() ?? "";
}

/// <summary>Stub Pattern Top-K 유사도 검색 옵션.</summary>
public sealed record StubPatternSearchOptions
{
    public int K { get; init; } = 10;
    public int CandidateFetchMultiplier { get; init; } = 10;
    public int CandidateFetchMinimum { get; init; } = 50;
    public int CandidateFetchMaximum { get; init; } = 500;
    public double FeatureWeight { get; init; } = 0.7;
    public double DirectionWeight { get; init; } = 0.3;
}

/// <summary>TB_ROUTE_STUB_PATTERN 한 행.</summary>
public sealed record StubPatternDescriptor(
    string PatternId,
    string RoutePathGuid,
    string StubKind,
    string AnchorKind,
    string AnchorName,
    string MainEquipmentName,
    string ProcessName,
    string UtilityGroup,
    string Utility,
    string Size,
    string Face,
    string DirSeq,
    int NBends,
    double RiseMm,
    double OffsetMm,
    double DiameterMm,
    double StubLengthMm,
    (double X, double Y, double Z) AnchorMin,
    (double X, double Y, double Z) AnchorMax,
    IReadOnlyList<(double X, double Y, double Z)> StubPoints,
    [property: System.Text.Json.Serialization.JsonIgnore] double[] Feat,
    [property: System.Text.Json.Serialization.JsonIgnore] double[] DirUnit,
    double AnnDistance = 0.0);

/// <summary>Top-K 후보 Stub 패턴 한 건.</summary>
public sealed record StubPatternSearchResult(
    int Rank, StubPatternDescriptor Candidate, double FeatureSimilarity, double DirectionSimilarity, double Similarity);

/// <summary>Stub 패턴 검색 실행 진단.</summary>
public sealed record StubPatternSearchMeta(
    string QueryPatternId, int RequestedK, int FetchN, int AnnCandidateCount, int ReturnedCount, double SearchTimeMs);

/// <summary>Viewer가 Query로 쓸 Stub 패턴을 고를 때 사용하는 프리셋.</summary>
public sealed record StubPatternPreset(
    string PatternId, string StubKind, string AnchorKind, string MainEquipmentName,
    string UtilityGroup, string Utility, string Size, string Face, string DirSeq, string Display);
