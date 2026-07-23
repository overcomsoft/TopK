using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Npgsql;

namespace RoutingAI.Standalone;

// 실행 방법(PowerShell)
// 1) Query 그룹 확인:
//    dotnet run --project TopKSearchStandalone/TopKSearchStandalone.csproj -- \
//      --list-group-presets --utility-group VACCUM --utility FORELINE \
//      --dbname DDW_AI_DB --user postgres --password dinno
// 2) Group ID 검색:
//    dotnet run --project TopKSearchStandalone/TopKSearchStandalone.csproj -- \
//      --group-query-id <GROUP_VECTOR_ID> --group-size-mode PreferExact --k 5 \
//      --dbname DDW_AI_DB --user postgres --password dinno
// 3) 조건 검색:
//    dotnet run --project TopKSearchStandalone/TopKSearchStandalone.csproj -- \
//      --group-search --process CVD --equipment TNMHJ04 \
//      --utility-group VACCUM --utility FORELINE --k 5 \
//      --dbname DDW_AI_DB --user postgres --password dinno
// 4) DB 없는 알고리즘 검사: dotnet run --project TopKSearchStandalone/TopKSearchStandalone.csproj -- --group-self-test

/// <summary>
/// TB_ROUTE_UTILITY_GROUP_VECTOR의 Feature centroid로 ANN 후보를 수집하고,
/// 멤버 Pair Hungarian 대응과 Arrangement/Coverage로 UtilityPipeGroup Top-K를 반환한다.
/// </summary>
public static class UtilityPipeGroupSearch
{
    /// <summary>
    /// Viewer/외부 호출자가 READY 그룹 한 건과 전체 배관 멤버를 조회한다.
    /// 검색과 동일한 header/member 로더를 사용하므로 화면에 표시되는 Query 데이터와
    /// 실제 Top-K 계산에 투입되는 Query 데이터가 항상 일치한다.
    /// </summary>
    public static async Task<UtilityPipeGroupDescriptor?> LoadGroupAsync(
        DbConfig db,
        string groupVectorId)
    {
        if (string.IsNullOrWhiteSpace(groupVectorId))
            throw new ArgumentException("groupVectorId가 필요합니다.", nameof(groupVectorId));

        await using var connection = new NpgsqlConnection(db.ToConnectionString());
        await connection.OpenAsync().ConfigureAwait(false);
        var header = await LoadHeaderAsync(connection, groupVectorId.Trim()).ConfigureAwait(false);
        if (header is null) return null;
        var groups = await AttachMembersAsync(connection, [header]).ConfigureAwait(false);
        return groups[header.GroupVectorId];
    }

    public static async Task<(IReadOnlyList<UtilityPipeGroupSearchResult> Results,
        UtilityPipeGroupSearchMeta Meta)> SearchAsync(
        DbConfig db,
        string queryGroupId,
        UtilityPipeGroupSearchOptions? options = null)
    {
        options ??= new UtilityPipeGroupSearchOptions();
        await using var connection = new NpgsqlConnection(db.ToConnectionString());
        await connection.OpenAsync().ConfigureAwait(false);
        return await SearchCoreAsync(connection, queryGroupId.Trim(), options).ConfigureAwait(false);
    }

    public static async Task<(IReadOnlyList<UtilityPipeGroupSearchResult> Results,
        UtilityPipeGroupSearchMeta Meta)> SearchByIdentityAsync(
        DbConfig db,
        string processName,
        string equipmentInstanceKey,
        string utilityGroup,
        string utility,
        UtilityPipeGroupSearchOptions? options = null,
        string projectScopeKey = "",
        string modelRevisionKey = "")
    {
        options ??= new UtilityPipeGroupSearchOptions();
        await using var connection = new NpgsqlConnection(db.ToConnectionString());
        await connection.OpenAsync().ConfigureAwait(false);
        var scope = string.IsNullOrWhiteSpace(projectScopeKey) && string.IsNullOrWhiteSpace(modelRevisionKey)
            ? await ResolveActiveScopeAsync(connection).ConfigureAwait(false)
            : (projectScopeKey.Trim(), modelRevisionKey.Trim());
        if (string.IsNullOrWhiteSpace(scope.Item1) || string.IsNullOrWhiteSpace(scope.Item2))
            throw new ArgumentException("projectScopeKey와 modelRevisionKey는 함께 입력해야 합니다.");

        const string sql = """
            SELECT "GROUP_VECTOR_ID"
            FROM "TB_ROUTE_UTILITY_GROUP_VECTOR"
            WHERE "PROJECT_SCOPE_KEY"=@project AND "MODEL_REVISION_KEY"=@revision
              AND "STATUS"='READY' AND "PROCESS_NAME"=@process
              AND "EQUIPMENT_INSTANCE_KEY"=@equipment
              AND "UTILITY_GROUP"=@utilityGroup AND "UTILITY"=@utility
            """;
        await using var command = new NpgsqlCommand(sql, connection);
        command.Parameters.AddWithValue("project", scope.Item1);
        command.Parameters.AddWithValue("revision", scope.Item2);
        command.Parameters.AddWithValue("process", processName.Trim().ToUpperInvariant());
        command.Parameters.AddWithValue("equipment", NormalizeEquipmentKey(equipmentInstanceKey));
        command.Parameters.AddWithValue("utilityGroup", utilityGroup.Trim().ToUpperInvariant());
        command.Parameters.AddWithValue("utility", utility.Trim().ToUpperInvariant());
        var value = await command.ExecuteScalarAsync().ConfigureAwait(false);
        if (value is null)
            throw new InvalidOperationException("입력한 Equipment + Utility Group + Utility에 해당하는 READY 그룹이 없습니다.");
        return await SearchCoreAsync(connection, Convert.ToString(value, CultureInfo.InvariantCulture)!, options)
            .ConfigureAwait(false);
    }

    public static async Task<IReadOnlyList<UtilityPipeGroupPreset>> FetchPresetsAsync(
        DbConfig db,
        string processName = "",
        string equipmentInstanceKey = "",
        string utilityGroup = "",
        string utility = "",
        int limit = 500)
    {
        await using var connection = new NpgsqlConnection(db.ToConnectionString());
        await connection.OpenAsync().ConfigureAwait(false);
        var (project, revision) = await ResolveActiveScopeAsync(connection).ConfigureAwait(false);
        var where = new List<string>
        {
            "\"PROJECT_SCOPE_KEY\"=@project", "\"MODEL_REVISION_KEY\"=@revision", "\"STATUS\"='READY'"
        };
        await using var command = new NpgsqlCommand { Connection = connection };
        command.Parameters.AddWithValue("project", project);
        command.Parameters.AddWithValue("revision", revision);
        AddOptionalFilter(command, where, "PROCESS_NAME", "process", processName.Trim().ToUpperInvariant());
        AddOptionalFilter(command, where, "EQUIPMENT_INSTANCE_KEY", "equipment", NormalizeEquipmentKey(equipmentInstanceKey));
        AddOptionalFilter(command, where, "UTILITY_GROUP", "utilityGroup", utilityGroup.Trim().ToUpperInvariant());
        AddOptionalFilter(command, where, "UTILITY", "utility", utility.Trim().ToUpperInvariant());
        command.Parameters.AddWithValue("limit", Math.Clamp(limit, 1, 5000));
        command.CommandText = $"""
            SELECT "GROUP_VECTOR_ID","PROCESS_NAME","EQUIPMENT_INSTANCE_KEY","EQUIPMENT_NAME",
                   "UTILITY_GROUP","UTILITY","MEMBER_COUNT","SIZE_SIGNATURE"::text
              FROM "TB_ROUTE_UTILITY_GROUP_VECTOR"
             WHERE {string.Join(" AND ", where)}
             ORDER BY "PROCESS_NAME","EQUIPMENT_INSTANCE_KEY","UTILITY_GROUP","UTILITY"
             LIMIT @limit
            """;
        var result = new List<UtilityPipeGroupPreset>();
        await using var reader = await command.ExecuteReaderAsync().ConfigureAwait(false);
        while (await reader.ReadAsync().ConfigureAwait(false))
        {
            var sizes = ParseSizeSignature(reader.GetString(7));
            var display = $"{Text(reader, 1)} | {Text(reader, 2)} | {Text(reader, 4)}/{Text(reader, 5)} | " +
                          $"{reader.GetInt32(6)} pipes | {string.Join(",", sizes.Select(item => $"{item.Key}:{item.Value}"))}";
            result.Add(new(Text(reader, 0), Text(reader, 1), Text(reader, 2), Text(reader, 3),
                Text(reader, 4), Text(reader, 5), reader.GetInt32(6), sizes, display));
        }
        return result;
    }

    private static async Task<(IReadOnlyList<UtilityPipeGroupSearchResult> Results,
        UtilityPipeGroupSearchMeta Meta)> SearchCoreAsync(
        NpgsqlConnection connection,
        string queryGroupId,
        UtilityPipeGroupSearchOptions options)
    {
        if (string.IsNullOrWhiteSpace(queryGroupId)) throw new ArgumentException("queryGroupId가 필요합니다.");
        ValidateSearchOptions(options);
        var stopwatch = Stopwatch.StartNew();
        var queryHeader = await LoadHeaderAsync(connection, queryGroupId).ConfigureAwait(false)
            ?? throw new InvalidOperationException($"READY Query 그룹이 없습니다: {queryGroupId}");
        var fetchN = Math.Clamp(
            Math.Max(options.K * options.CandidateFetchMultiplier, options.CandidateFetchMinimum),
            1, options.CandidateFetchMaximum);
        var candidateHeaders = await LoadCandidateHeadersAsync(connection, queryHeader, options, fetchN)
            .ConfigureAwait(false);
        var allHeaders = new[] { queryHeader }.Concat(candidateHeaders).ToArray();
        var withMembers = await AttachMembersAsync(connection, allHeaders).ConfigureAwait(false);
        var query = withMembers[queryHeader.GroupVectorId];

        var scored = candidateHeaders
            .Select(header => UtilityPipeGroupMatcher.ScoreGroup(query, withMembers[header.GroupVectorId], options))
            .OrderByDescending(result => result.GroupSimilarity)
            .ThenByDescending(result => result.MatchedAverage)
            .ThenByDescending(result => result.Arrangement)
            .ThenBy(result => result.Candidate.AnnCosineDistance)
            .ThenBy(result => result.Candidate.GroupVectorId, StringComparer.Ordinal)
            .Take(options.K)
            .Select((result, index) => result with { Rank = index + 1 })
            .ToArray();
        stopwatch.Stop();

        var pairWeights = options.PairWeights;
        var pairSum = pairWeights.Position + pairWeights.Pattern + pairWeights.Vector + pairWeights.Context;
        var meta = new UtilityPipeGroupSearchMeta(
            query.GroupVectorId, query.ProjectScopeKey, query.ModelRevisionKey, options.K, fetchN,
            candidateHeaders.Count, scored.Length, stopwatch.Elapsed.TotalMilliseconds, options.SizeMatchMode,
            $"Pos/Pat/Feat/Ctx={pairWeights.Position / pairSum:P1}/{pairWeights.Pattern / pairSum:P1}/" +
            $"{pairWeights.Vector / pairSum:P1}/{pairWeights.Context / pairSum:P1}",
            options.MatchedWeight, options.ArrangementWeight,
            new Dictionary<string, string>
            {
                ["utility_group"] = query.UtilityGroup,
                ["utility"] = query.Utility,
                ["self_excluded"] = query.GroupVectorId,
                ["same_process"] = options.RequireSameProcess ? query.ProcessName : "",
                ["equipment_family"] = options.EquipmentFamilyKey.Trim(),
                ["project_scope_key"] = query.ProjectScopeKey,
                ["model_revision_key"] = query.ModelRevisionKey,
            });
        return (scored, meta);
    }

    private static async Task<UtilityPipeGroupDescriptor?> LoadHeaderAsync(
        NpgsqlConnection connection, string groupId)
    {
        var sql = HeaderSelectSql + " WHERE g.\"GROUP_VECTOR_ID\"=@groupId AND g.\"STATUS\"='READY'";
        await using var command = new NpgsqlCommand(sql, connection);
        command.Parameters.AddWithValue("groupId", groupId);
        await using var reader = await command.ExecuteReaderAsync().ConfigureAwait(false);
        return await reader.ReadAsync().ConfigureAwait(false) ? ReadHeader(reader) : null;
    }

    private static async Task<List<UtilityPipeGroupDescriptor>> LoadCandidateHeadersAsync(
        NpgsqlConnection connection,
        UtilityPipeGroupDescriptor query,
        UtilityPipeGroupSearchOptions options,
        int fetchN)
    {
        var where = new List<string>
        {
            "g.\"STATUS\"='READY'", "g.\"PROJECT_SCOPE_KEY\"=@project", "g.\"MODEL_REVISION_KEY\"=@revision",
            "g.\"UTILITY_GROUP\"=@utilityGroup", "g.\"UTILITY\"=@utility", "g.\"GROUP_VECTOR_ID\"<>@queryId"
        };
        if (options.RequireSameProcess) where.Add("g.\"PROCESS_NAME\"=@process");
        if (!string.IsNullOrWhiteSpace(options.EquipmentFamilyKey)) where.Add("g.\"EQUIPMENT_FAMILY_KEY\"=@family");
        var sql = HeaderSelectSql.Replace(
            "0.0::double precision AS ann_distance",
            "g.\"FEATURE_CENTROID\" <=> @queryVector::vector AS ann_distance",
            StringComparison.Ordinal) + $"""
             WHERE {string.Join(" AND ", where)}
             ORDER BY g."FEATURE_CENTROID" <=> @queryVector::vector, g."GROUP_VECTOR_ID"
             LIMIT @fetchN
            """;
        await using var command = new NpgsqlCommand(sql, connection);
        command.Parameters.AddWithValue("project", query.ProjectScopeKey);
        command.Parameters.AddWithValue("revision", query.ModelRevisionKey);
        command.Parameters.AddWithValue("utilityGroup", query.UtilityGroup);
        command.Parameters.AddWithValue("utility", query.Utility);
        command.Parameters.AddWithValue("queryId", query.GroupVectorId);
        command.Parameters.AddWithValue("queryVector", ToVectorLiteral(query.FeatureCentroid));
        command.Parameters.AddWithValue("fetchN", fetchN);
        if (options.RequireSameProcess) command.Parameters.AddWithValue("process", query.ProcessName);
        if (!string.IsNullOrWhiteSpace(options.EquipmentFamilyKey))
            command.Parameters.AddWithValue("family", options.EquipmentFamilyKey.Trim().ToUpperInvariant());
        var result = new List<UtilityPipeGroupDescriptor>();
        await using var reader = await command.ExecuteReaderAsync().ConfigureAwait(false);
        while (await reader.ReadAsync().ConfigureAwait(false)) result.Add(ReadHeader(reader));
        return result;
    }

    private static async Task<Dictionary<string, UtilityPipeGroupDescriptor>> AttachMembersAsync(
        NpgsqlConnection connection,
        IReadOnlyList<UtilityPipeGroupDescriptor> headers)
    {
        var ids = headers.Select(header => header.GroupVectorId).Distinct(StringComparer.Ordinal).ToArray();
        const string sql = """
            SELECT m."GROUP_VECTOR_ID",BTRIM(m."ROUTE_PATH_GUID"),m."MEMBER_ORDER",m."UTILITY",m."SIZE",
                   m."START_X",m."START_Y",m."START_Z",m."END_X",m."END_Y",m."END_Z",
                   m."DIRECTION_PATTERN",m."TOTAL_LENGTH_MM",m."STEP_COUNT",
                   fv."FEATURE_VECTOR"::text,cv."CONTEXT_VECTOR"::text,
                   m."FEATURE_VECTOR_BUILD_RUN_ID",m."CONTEXT_VECTOR_BUILD_RUN_ID"
              FROM "TB_ROUTE_UTILITY_GROUP_MEMBER" m
              JOIN "TB_ROUTE_UTILITY_GROUP_VECTOR" g ON g."GROUP_VECTOR_ID"=m."GROUP_VECTOR_ID"
              JOIN "TB_ROUTE_FEATURE_VECTOR" fv
                ON BTRIM(fv."ROUTE_PATH_GUID")=BTRIM(m."ROUTE_PATH_GUID")
               AND fv."PROJECT_SCOPE_KEY"=g."PROJECT_SCOPE_KEY" AND fv."MODEL_REVISION_KEY"=g."MODEL_REVISION_KEY"
              LEFT JOIN "TB_ROUTE_CONTEXT_VECTOR" cv
                ON BTRIM(cv."ROUTE_PATH_GUID")=BTRIM(m."ROUTE_PATH_GUID")
               AND cv."PROJECT_SCOPE_KEY"=g."PROJECT_SCOPE_KEY" AND cv."MODEL_REVISION_KEY"=g."MODEL_REVISION_KEY"
             WHERE m."GROUP_VECTOR_ID"=ANY(@ids)
             ORDER BY m."GROUP_VECTOR_ID",m."MEMBER_ORDER",BTRIM(m."ROUTE_PATH_GUID")
            """;
        var rows = new List<(string GroupId, string RouteGuid, int MemberOrder, string Utility, string Size,
            (double, double, double) Start, (double, double, double) End, string DirectionPattern,
            double TotalLength, int StepCount, double[] FeatureVector, double[]? ContextVector,
            string FeatureProvenance, string ContextProvenance)>();
        await using (var command = new NpgsqlCommand(sql, connection))
        {
            command.Parameters.AddWithValue("ids", ids);
            await using var reader = await command.ExecuteReaderAsync().ConfigureAwait(false);
            while (await reader.ReadAsync().ConfigureAwait(false))
            {
                rows.Add((
                    Text(reader, 0), Text(reader, 1), reader.GetInt32(2), Text(reader, 3), Text(reader, 4),
                    Point(reader, 5), Point(reader, 8), Text(reader, 11), Number(reader, 12), reader.GetInt32(13),
                    ParseVector(Text(reader, 14), 30), reader.IsDBNull(15) ? null : ParseVector(Text(reader, 15), 30),
                    Text(reader, 16), Text(reader, 17)));
            }
        }

        var bendByGuid = await FetchBendPointsAsync(
            connection, headers[0], rows.Select(row => row.RouteGuid).Distinct(StringComparer.Ordinal).ToArray())
            .ConfigureAwait(false);

        var members = headers.ToDictionary(header => header.GroupVectorId,
            _ => new List<UtilityPipeGroupMember>(), StringComparer.Ordinal);
        foreach (var row in rows)
        {
            members[row.GroupId].Add(new(
                row.RouteGuid, row.MemberOrder, row.Utility, row.Size,
                row.Start, row.End, row.DirectionPattern, row.TotalLength, row.StepCount,
                row.FeatureVector, row.ContextVector, row.FeatureProvenance, row.ContextProvenance,
                bendByGuid.TryGetValue(row.RouteGuid, out var bendPoints)
                    ? bendPoints
                    : (IReadOnlyList<BendFeaturePointSummary>)Array.Empty<BendFeaturePointSummary>()));
        }
        var result = new Dictionary<string, UtilityPipeGroupDescriptor>(StringComparer.Ordinal);
        foreach (var header in headers)
        {
            if (members[header.GroupVectorId].Count != header.MemberCount)
                throw new InvalidOperationException(
                    $"그룹 {header.GroupVectorId}의 선언 멤버 {header.MemberCount}개와 Vector 연결 {members[header.GroupVectorId].Count}개가 다릅니다.");
            result[header.GroupVectorId] = header with { Members = members[header.GroupVectorId] };
        }
        return result;
    }

    /// <summary>Tools/ExtractBendFeaturePoints.py가 적재한 개별 꺾임점을 배관 GUID별로 조회한다.
    /// 테이블이 아직 build되지 않은 환경(TB_ROUTE_BEND_FEATURE_POINT 미생성)에서도 그룹 검색
    /// 자체는 fallback(구조 패턴 50:50)으로 동작해야 하므로, 조회 실패는 조용히 빈 결과로 처리한다.</summary>
    private static async Task<Dictionary<string, List<BendFeaturePointSummary>>> FetchBendPointsAsync(
        NpgsqlConnection connection, UtilityPipeGroupDescriptor scopeSource, IReadOnlyList<string> routeGuids)
    {
        var result = new Dictionary<string, List<BendFeaturePointSummary>>(StringComparer.Ordinal);
        if (routeGuids.Count == 0) return result;
        const string sql = """
            SELECT BTRIM("ROUTE_PATH_GUID"),"ORDINAL_FROM_START","SEGMENT_ZONE","REL_POSITION_BUCKET","TRANSITION_TYPE","CAUSE"
              FROM "TB_ROUTE_BEND_FEATURE_POINT"
             WHERE "PROJECT_SCOPE_KEY"=@project AND "MODEL_REVISION_KEY"=@revision
               AND BTRIM("ROUTE_PATH_GUID")=ANY(@guids)
             ORDER BY BTRIM("ROUTE_PATH_GUID"),"ORDINAL_FROM_START"
            """;
        try
        {
            await using var command = new NpgsqlCommand(sql, connection);
            command.Parameters.AddWithValue("project", scopeSource.ProjectScopeKey);
            command.Parameters.AddWithValue("revision", scopeSource.ModelRevisionKey);
            command.Parameters.AddWithValue("guids", routeGuids.ToArray());
            await using var reader = await command.ExecuteReaderAsync().ConfigureAwait(false);
            while (await reader.ReadAsync().ConfigureAwait(false))
            {
                var guid = Text(reader, 0);
                if (!result.TryGetValue(guid, out var list)) result[guid] = list = new List<BendFeaturePointSummary>();
                list.Add(new(reader.GetInt32(1), Text(reader, 2), Number(reader, 3), Text(reader, 4), Text(reader, 5)));
            }
        }
        catch (PostgresException ex) when (ex.SqlState == "42P01")
        {
            // TB_ROUTE_BEND_FEATURE_POINT가 아직 create-schema/build되지 않은 환경.
        }
        return result;
    }

    private const string HeaderSelectSql = """
        SELECT g."GROUP_VECTOR_ID",g."PROJECT_SCOPE_KEY",g."MODEL_REVISION_KEY",g."PROCESS_NAME",
               g."EQUIPMENT_INSTANCE_KEY",g."EQUIPMENT_NAME",g."EQUIPMENT_FAMILY_KEY",
               g."UTILITY_GROUP",g."UTILITY",g."MEMBER_COUNT",g."SIZE_SIGNATURE"::text,
               g."FEATURE_CENTROID"::text,g."CONTEXT_CENTROID"::text,
               g."ARRANGEMENT_VECTOR_JSON"::text,g."FEATURE_COVERAGE",g."CONTEXT_COVERAGE",
               g."SOURCE_HASH",0.0::double precision AS ann_distance
          FROM "TB_ROUTE_UTILITY_GROUP_VECTOR" g
        """;

    private static UtilityPipeGroupDescriptor ReadHeader(NpgsqlDataReader reader) => new(
        Text(reader, 0), Text(reader, 1), Text(reader, 2), Text(reader, 3), Text(reader, 4), Text(reader, 5),
        Text(reader, 6), Text(reader, 7), Text(reader, 8), reader.GetInt32(9), ParseSizeSignature(Text(reader, 10)),
        ParseVector(Text(reader, 11), 30), reader.IsDBNull(12) ? null : ParseVector(Text(reader, 12), 30),
        JsonDocument.Parse(Text(reader, 13)).RootElement.Clone(), Number(reader, 14), Number(reader, 15),
        Text(reader, 16), [], Number(reader, 17));

    private static async Task<(string Project, string Revision)> ResolveActiveScopeAsync(NpgsqlConnection connection)
    {
        const string sql = """
            SELECT "PROJECT_SCOPE_KEY","MODEL_REVISION_KEY"
              FROM "TB_ROUTE_SOURCE_SCOPE_MANIFEST" WHERE "STATUS"='ACTIVE'
             ORDER BY "PROJECT_SCOPE_KEY","MODEL_REVISION_KEY" LIMIT 2
            """;
        await using var command = new NpgsqlCommand(sql, connection);
        var rows = new List<(string, string)>();
        await using var reader = await command.ExecuteReaderAsync().ConfigureAwait(false);
        while (await reader.ReadAsync().ConfigureAwait(false)) rows.Add((Text(reader, 0), Text(reader, 1)));
        return rows.Count switch
        {
            1 => rows[0],
            0 => throw new InvalidOperationException("ACTIVE source scope가 없습니다."),
            _ => throw new InvalidOperationException("ACTIVE source scope가 여러 개입니다. 명시적인 scope가 필요합니다."),
        };
    }

    private static void AddOptionalFilter(NpgsqlCommand command, List<string> where,
        string column, string parameter, string value)
    {
        if (string.IsNullOrWhiteSpace(value)) return;
        where.Add($"\"{column}\"=@{parameter}");
        command.Parameters.AddWithValue(parameter, value);
    }

    private static void ValidateSearchOptions(UtilityPipeGroupSearchOptions options)
    {
        if (options.K < 1) throw new ArgumentOutOfRangeException(nameof(options.K));
        if (options.CandidateFetchMultiplier < 1 || options.CandidateFetchMinimum < 1
            || options.CandidateFetchMaximum < options.CandidateFetchMinimum)
            throw new ArgumentOutOfRangeException(nameof(options), "후보 fetch 설정이 올바르지 않습니다.");
        var pair = new[]
        {
            options.PairWeights.Position, options.PairWeights.Pattern,
            options.PairWeights.Vector, options.PairWeights.Context,
        };
        if (pair.Any(value => !double.IsFinite(value) || value < 0) || pair.Sum() <= 0)
            throw new ArgumentOutOfRangeException(nameof(options), "Pair 가중치는 0 이상의 유한수이며 합계가 0보다 커야 합니다.");
        if (!double.IsFinite(options.MatchedWeight) || !double.IsFinite(options.ArrangementWeight)
            || options.MatchedWeight < 0 || options.ArrangementWeight < 0
            || options.MatchedWeight + options.ArrangementWeight <= 0)
            throw new ArgumentOutOfRangeException(nameof(options), "그룹 최종 가중치가 올바르지 않습니다.");
    }

    private static string NormalizeEquipmentKey(string value)
    {
        var upper = value.Trim().ToUpperInvariant();
        upper = Regex.Replace(upper, @"[\s_\-]+$", "");
        return Regex.Replace(upper, @"\s+", "");
    }

    private static IReadOnlyDictionary<string, int> ParseSizeSignature(string text) =>
        JsonSerializer.Deserialize<Dictionary<string, int>>(text)
        ?? new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);

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

    private static (double X, double Y, double Z) Point(NpgsqlDataReader reader, int offset) =>
        (Number(reader, offset), Number(reader, offset + 1), Number(reader, offset + 2));
    private static double Number(NpgsqlDataReader reader, int ordinal) =>
        reader.IsDBNull(ordinal) ? 0.0 : Convert.ToDouble(reader.GetValue(ordinal), CultureInfo.InvariantCulture);
    private static string Text(NpgsqlDataReader reader, int ordinal) =>
        reader.IsDBNull(ordinal) ? "" : Convert.ToString(reader.GetValue(ordinal), CultureInfo.InvariantCulture)?.Trim() ?? "";
}
