using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using Npgsql;

namespace RoutingAI.Standalone;

// 실행 예시(코드 참고용 — 이 클래스는 CLI가 아니라 TopK.3DViewer가 직접 호출하는 라이브러리다):
//   var presets = await GroupPatternSearch.FetchPresetsAsync(db, equipmentTag: "TNMHJ04");
//   var (results, meta) = await GroupPatternSearch.SearchAsync(db, presets[0].GroupId);

/// <summary>
/// TB_ROUTE_GROUP_PATTERN의 60D FEAT(트렁크 샘플 형상 벡터, HNSW L2)로 다발배관(Group/Bundle
/// Pattern) Top-K 유사도 검색을 수행한다. Docs/FeaturePattern_Pipeline_Overlap_Review.md 2.4절
/// 기준으로 이 테이블은 지금까지 어떤 검색 코드에서도 조회된 적이 없었다 — pgvector 인덱스는
/// 이미 있었지만(create_route_group_pattern_tables.sql) 검색 로직이 없었던 상태를 채운다.
///
/// UtilityPipeGroupSearch.cs와 달리 멤버 단위 Hungarian 매칭은 하지 않는다 — Group Pattern은
/// 한 행이 이미 "국소 평행구간 하나"를 나타내는 완결된 형상 요약이라, route 여러 개를 서로
/// 짝짓는 절차가 필요 없다.
/// </summary>
public static class GroupPatternSearch
{
    /// <summary>Viewer가 프리셋 선택 시 Query로 쓸 그룹 패턴 1건과 트렁크 지오메트리를 조회한다.</summary>
    public static async Task<GroupPatternDescriptor?> LoadAsync(DbConfig db, string groupId)
    {
        if (string.IsNullOrWhiteSpace(groupId))
            throw new ArgumentException("groupId가 필요합니다.", nameof(groupId));
        await using var connection = new NpgsqlConnection(db.ToConnectionString());
        await connection.OpenAsync().ConfigureAwait(false);
        var header = await LoadHeaderAsync(connection, groupId.Trim()).ConfigureAwait(false);
        if (header is null) return null;
        var withGeometry = await AttachTrunkLinesAsync(connection, [header]).ConfigureAwait(false);
        return withGeometry[header.GroupId];
    }

    public static async Task<IReadOnlyList<GroupPatternPreset>> FetchPresetsAsync(
        DbConfig db,
        string equipmentTag = "",
        string utilityGroup = "",
        string utility = "",
        int limit = 500)
    {
        await using var connection = new NpgsqlConnection(db.ToConnectionString());
        await connection.OpenAsync().ConfigureAwait(false);
        var where = new List<string>();
        await using var command = new NpgsqlCommand { Connection = connection };
        AddOptionalFilter(command, where, "EQUIPMENT_TAG", "equipmentTag", equipmentTag.Trim());
        AddOptionalFilter(command, where, "UTILITY_GROUP", "utilityGroup", utilityGroup.Trim());
        AddOptionalFilter(command, where, "UTILITY", "utility", utility.Trim());
        command.Parameters.AddWithValue("limit", Math.Clamp(limit, 1, 5000));
        command.CommandText = $"""
            SELECT "GROUP_ID","EQUIPMENT_TAG","UTILITY_GROUP","UTILITY","N_MEMBERS",
                   "PITCH_MM","IS_EQUAL_SPACING","OFFSET_AXIS"
              FROM "TB_ROUTE_GROUP_PATTERN"
              {(where.Count > 0 ? "WHERE " + string.Join(" AND ", where) : "")}
             ORDER BY "EQUIPMENT_TAG","UTILITY_GROUP","UTILITY","N_MEMBERS" DESC
             LIMIT @limit
            """;
        var result = new List<GroupPatternPreset>();
        await using var reader = await command.ExecuteReaderAsync().ConfigureAwait(false);
        while (await reader.ReadAsync().ConfigureAwait(false))
        {
            var nMembers = reader.GetInt32(4);
            var pitch = Number(reader, 5);
            var equal = reader.GetBoolean(6);
            var axis = Text(reader, 7);
            var display = $"{Text(reader, 1)} | {Text(reader, 2)}/{Text(reader, 3)} | {nMembers}본 | " +
                          $"Pitch {pitch:F0}mm {(equal ? "등간격" : "비등간격")} ({axis})";
            result.Add(new(Text(reader, 0), Text(reader, 1), Text(reader, 2), Text(reader, 3),
                nMembers, pitch, equal, axis, display));
        }
        return result;
    }

    /// <summary>
    /// queryGroupId 행의 FEAT를 쿼리 벡터로 삼아, 같은 EQUIPMENT_TAG/UTILITY_GROUP(옵션: UTILITY)
    /// 안에서 형상이 비슷한 다른 평행구간을 찾는다. N_MEMBERS 차이가 클수록 소폭 감점한다.
    /// </summary>
    public static async Task<(IReadOnlyList<GroupPatternSearchResult> Results, GroupPatternSearchMeta Meta)> SearchAsync(
        DbConfig db,
        string queryGroupId,
        GroupPatternSearchOptions? options = null)
    {
        options ??= new GroupPatternSearchOptions();
        if (options.K < 1) throw new ArgumentOutOfRangeException(nameof(options), "K는 1 이상이어야 합니다.");
        await using var connection = new NpgsqlConnection(db.ToConnectionString());
        await connection.OpenAsync().ConfigureAwait(false);
        var stopwatch = Stopwatch.StartNew();

        var queryHeader = await LoadHeaderAsync(connection, queryGroupId.Trim()).ConfigureAwait(false)
            ?? throw new InvalidOperationException($"Group Pattern이 없습니다: {queryGroupId}");
        var fetchN = Math.Clamp(
            Math.Max(options.K * options.CandidateFetchMultiplier, options.CandidateFetchMinimum),
            1, options.CandidateFetchMaximum);
        var candidateHeaders = await LoadCandidateHeadersAsync(connection, queryHeader, options, fetchN)
            .ConfigureAwait(false);

        var scored = candidateHeaders
            .Select(candidate => new GroupPatternSearchResult(
                0, candidate,
                1.0 / (1.0 + candidate.AnnDistance) * MemberCountFactor(queryHeader.NMembers, candidate.NMembers)))
            .OrderByDescending(result => result.Similarity)
            .ThenBy(result => result.Candidate.GroupId, StringComparer.Ordinal)
            .Take(options.K)
            .Select((result, index) => result with { Rank = index + 1 })
            .ToArray();

        // 3D 렌더링에 필요한 트렁크 지오메트리는 최종 Top-K만 조회한다(넓은 ANN 후보 전체를
        // 파싱할 필요가 없음).
        var withGeometry = await AttachTrunkLinesAsync(
            connection, scored.Select(result => result.Candidate).ToArray()).ConfigureAwait(false);
        scored = scored.Select(result => result with { Candidate = withGeometry[result.Candidate.GroupId] }).ToArray();
        stopwatch.Stop();

        var meta = new GroupPatternSearchMeta(
            queryHeader.GroupId, options.K, fetchN, candidateHeaders.Count, scored.Length,
            stopwatch.Elapsed.TotalMilliseconds);
        return (scored, meta);
    }

    private const string HeaderSelectSql = """
        SELECT "GROUP_ID","EQUIPMENT_TAG","UTILITY_GROUP","UTILITY","N_MEMBERS","TRUNK_Z","TRUNK_XY_SPREAD",
               "PITCH_MM","PITCH_CV","IS_EQUAL_SPACING","OFFSET_AXIS","N_ORTHO_BENDS","ORTHO_PATTERN",
               "MEMBER_GUIDS"::text,"FEAT"::text,0.0::double precision AS ann_distance
          FROM "TB_ROUTE_GROUP_PATTERN"
        """;

    private static async Task<GroupPatternDescriptor?> LoadHeaderAsync(NpgsqlConnection connection, string groupId)
    {
        var sql = HeaderSelectSql + " WHERE \"GROUP_ID\"=@groupId";
        await using var command = new NpgsqlCommand(sql, connection);
        command.Parameters.AddWithValue("groupId", groupId);
        await using var reader = await command.ExecuteReaderAsync().ConfigureAwait(false);
        return await reader.ReadAsync().ConfigureAwait(false) ? ReadHeader(reader) : null;
    }

    private static async Task<List<GroupPatternDescriptor>> LoadCandidateHeadersAsync(
        NpgsqlConnection connection,
        GroupPatternDescriptor query,
        GroupPatternSearchOptions options,
        int fetchN)
    {
        var where = new List<string>
        {
            "\"EQUIPMENT_TAG\"=@equipmentTag", "\"UTILITY_GROUP\"=@utilityGroup", "\"GROUP_ID\"<>@queryId"
        };
        if (options.RequireSameUtility) where.Add("\"UTILITY\"=@utility");
        var sql = HeaderSelectSql.Replace(
            "0.0::double precision AS ann_distance",
            "\"FEAT\" <-> @queryVector::vector AS ann_distance",
            StringComparison.Ordinal) + $"""
             WHERE {string.Join(" AND ", where)}
             ORDER BY "FEAT" <-> @queryVector::vector, "GROUP_ID"
             LIMIT @fetchN
            """;
        await using var command = new NpgsqlCommand(sql, connection);
        command.Parameters.AddWithValue("equipmentTag", query.EquipmentTag);
        command.Parameters.AddWithValue("utilityGroup", query.UtilityGroup);
        command.Parameters.AddWithValue("queryId", query.GroupId);
        command.Parameters.AddWithValue("queryVector", ToVectorLiteral(query.Feat));
        command.Parameters.AddWithValue("fetchN", fetchN);
        if (options.RequireSameUtility) command.Parameters.AddWithValue("utility", query.Utility);
        var result = new List<GroupPatternDescriptor>();
        await using var reader = await command.ExecuteReaderAsync().ConfigureAwait(false);
        while (await reader.ReadAsync().ConfigureAwait(false)) result.Add(ReadHeader(reader));
        return result;
    }

    /// <summary>TRUNK_GEOM_3D(MultiLineStringZ)를 ST_DumpPoints로 풀어 멤버별 폴리라인으로 복원한다.
    /// C# 쪽에 WKT 파서를 새로 만들지 않기 위해 지오메트리 분해를 SQL에서 끝낸다.</summary>
    private static async Task<Dictionary<string, GroupPatternDescriptor>> AttachTrunkLinesAsync(
        NpgsqlConnection connection, IReadOnlyList<GroupPatternDescriptor> headers)
    {
        var result = new Dictionary<string, GroupPatternDescriptor>(StringComparer.Ordinal);
        if (headers.Count == 0) return result;
        var ids = headers.Select(header => header.GroupId).Distinct(StringComparer.Ordinal).ToArray();
        const string sql = """
            SELECT "GROUP_ID",(dump).path[1] AS line_idx,(dump).path[2] AS point_idx,
                   ST_X((dump).geom),ST_Y((dump).geom),ST_Z((dump).geom)
              FROM (SELECT "GROUP_ID", ST_DumpPoints("TRUNK_GEOM_3D") AS dump
                      FROM "TB_ROUTE_GROUP_PATTERN" WHERE "GROUP_ID"=ANY(@ids)) t
             ORDER BY "GROUP_ID",line_idx,point_idx
            """;
        var lines = new Dictionary<string, Dictionary<int, List<(double X, double Y, double Z)>>>(StringComparer.Ordinal);
        await using (var command = new NpgsqlCommand(sql, connection))
        {
            command.Parameters.AddWithValue("ids", ids);
            await using var reader = await command.ExecuteReaderAsync().ConfigureAwait(false);
            while (await reader.ReadAsync().ConfigureAwait(false))
            {
                var groupId = Text(reader, 0);
                var lineIdx = reader.GetInt32(1);
                if (!lines.TryGetValue(groupId, out var byLine))
                    lines[groupId] = byLine = new Dictionary<int, List<(double, double, double)>>();
                if (!byLine.TryGetValue(lineIdx, out var points))
                    byLine[lineIdx] = points = [];
                points.Add((Number(reader, 3), Number(reader, 4), Number(reader, 5)));
            }
        }
        foreach (var header in headers)
        {
            var trunkLines = lines.TryGetValue(header.GroupId, out var byLine)
                ? byLine.OrderBy(kv => kv.Key)
                    .Select(kv => new GroupPatternMemberLine(kv.Key, kv.Value))
                    .ToArray()
                : Array.Empty<GroupPatternMemberLine>();
            result[header.GroupId] = header with { TrunkLines = trunkLines };
        }
        return result;
    }

    private static GroupPatternDescriptor ReadHeader(NpgsqlDataReader reader) => new(
        Text(reader, 0), Text(reader, 1), Text(reader, 2), Text(reader, 3), reader.GetInt32(4),
        Number(reader, 5), Number(reader, 6), Number(reader, 7), Number(reader, 8), reader.GetBoolean(9),
        Text(reader, 10), reader.GetInt32(11), Text(reader, 12),
        ParseStringArray(Text(reader, 13)), ParseVector(Text(reader, 14), 60), [],
        Number(reader, 15));

    /// <summary>N_MEMBERS 차이가 클수록 유사도를 소폭 낮춘다(형상은 비슷해도 규모가 다른 다발은
    /// 재사용 참고 가치가 떨어짐).</summary>
    private static double MemberCountFactor(int queryCount, int candidateCount) =>
        1.0 / (1.0 + 0.15 * Math.Abs(queryCount - candidateCount));

    private static void AddOptionalFilter(NpgsqlCommand command, List<string> where,
        string column, string parameter, string value)
    {
        if (string.IsNullOrWhiteSpace(value)) return;
        where.Add($"\"{column}\"=@{parameter}");
        command.Parameters.AddWithValue(parameter, value);
    }

    private static IReadOnlyList<string> ParseStringArray(string json) =>
        string.IsNullOrWhiteSpace(json) ? [] : JsonSerializer.Deserialize<string[]>(json) ?? [];

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

/// <summary>다발배관(Group/Bundle Pattern) Top-K 유사도 검색 옵션.</summary>
public sealed record GroupPatternSearchOptions
{
    public int K { get; init; } = 10;
    public int CandidateFetchMultiplier { get; init; } = 10;
    public int CandidateFetchMinimum { get; init; } = 50;
    public int CandidateFetchMaximum { get; init; } = 500;
    /// <summary>false면 같은 EQUIPMENT_TAG/UTILITY_GROUP 안에서 UTILITY가 달라도 후보로 본다.</summary>
    public bool RequireSameUtility { get; init; } = true;
}

/// <summary>TRUNK_GEOM_3D를 구성하는 개별 멤버 폴리라인 한 줄.</summary>
public sealed record GroupPatternMemberLine(int LineIndex, IReadOnlyList<(double X, double Y, double Z)> Points);

/// <summary>TB_ROUTE_GROUP_PATTERN 한 행(평행구간 하나).</summary>
public sealed record GroupPatternDescriptor(
    string GroupId,
    string EquipmentTag,
    string UtilityGroup,
    string Utility,
    int NMembers,
    double TrunkZ,
    double TrunkXySpread,
    double PitchMm,
    double PitchCv,
    bool IsEqualSpacing,
    string OffsetAxis,
    int NOrthoBends,
    string OrthoPattern,
    IReadOnlyList<string> MemberGuids,
    [property: System.Text.Json.Serialization.JsonIgnore] double[] Feat,
    IReadOnlyList<GroupPatternMemberLine> TrunkLines,
    double AnnDistance = 0.0);

/// <summary>Top-K 후보 그룹 패턴 한 건.</summary>
public sealed record GroupPatternSearchResult(int Rank, GroupPatternDescriptor Candidate, double Similarity);

/// <summary>그룹 패턴 검색 실행 진단.</summary>
public sealed record GroupPatternSearchMeta(
    string QueryGroupId, int RequestedK, int FetchN, int AnnCandidateCount, int ReturnedCount, double SearchTimeMs);

/// <summary>Viewer가 Query로 쓸 그룹 패턴을 고를 때 사용하는 프리셋.</summary>
public sealed record GroupPatternPreset(
    string GroupId, string EquipmentTag, string UtilityGroup, string Utility,
    int NMembers, double PitchMm, bool IsEqualSpacing, string OffsetAxis, string Display);
