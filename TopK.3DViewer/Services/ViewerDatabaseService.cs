using System.Globalization;
using System.Windows.Media.Media3D;
using Npgsql;
using TopK.ThreeDViewer.Models;

namespace TopK.ThreeDViewer.Services;

/// <summary>
/// Viewer 전용 읽기 서비스. 참조 프로젝트의 데이터 구조만 참고하고 모든 SQL/로딩 코드를 새로 작성했다.
/// 각 public 함수는 독립 connection을 사용하므로 UI의 병렬 경로 로딩에서도 안전하다.
/// </summary>
public sealed class ViewerDatabaseService
{
    private readonly string _connectionString;

    public ViewerDatabaseService(string connectionString) => _connectionString = connectionString;

    public async Task<string> TestConnectionAsync()
    {
        await using var conn = new NpgsqlConnection(_connectionString);
        await conn.OpenAsync();
        await using var cmd = new NpgsqlCommand(
            "SELECT current_database(), current_user, version()", conn);
        await using var reader = await cmd.ExecuteReaderAsync();
        await reader.ReadAsync();
        return $"DB={reader.GetString(0)}, User={reader.GetString(1)}";
    }

    public async Task<FilterCatalog> LoadFilterCatalogAsync()
    {
        await using var conn = new NpgsqlConnection(_connectionString);
        await conn.OpenAsync();

        async Task<IReadOnlyList<string>> DistinctAsync(string column)
        {
            // column은 내부 상수만 전달되므로 SQL identifier injection 대상이 아니다.
            var sql = $"""
                SELECT DISTINCT TRIM("{column}")
                FROM "TB_ROUTE_FEATURE_VECTOR"
                WHERE "{column}" IS NOT NULL AND TRIM("{column}") <> ''
                ORDER BY 1
                """;
            await using var cmd = new NpgsqlCommand(sql, conn);
            await using var reader = await cmd.ExecuteReaderAsync();
            var values = new List<string>();
            while (await reader.ReadAsync()) values.Add(reader.GetString(0));
            return values;
        }

        return new FilterCatalog(
            await DistinctAsync("PROCESS_NAME"),
            await DistinctAsync("EQUIPMENT_NAME"),
            await DistinctAsync("UTILITY_GROUP"),
            await DistinctAsync("UTILITY"),
            await DistinctAsync("SIZE"));
    }

    public async Task<IReadOnlyList<RoutePresetItem>> LoadPresetsAsync(int limit = 200)
    {
        await using var conn = new NpgsqlConnection(_connectionString);
        await conn.OpenAsync();
        var columns = await LoadColumnsAsync(conn, "TB_ROUTE_PATH");

        // DDW_AI_DB revision별 명칭 차이를 흡수한다. 현재 schema는 EQUIPMENT_NAME을 사용하며,
        // 일부 legacy DB의 SOURCE_OWNER_NAME/EQUIPMENT_TAG도 같은 화면 필드로 읽을 수 있다.
        var guidColumn = Require(columns, "TB_ROUTE_PATH", "ROUTE_PATH_GUID");
        var processColumn = Pick(columns, "PROCESS_NAME", "PROCESS");
        var equipmentColumn = Pick(columns, "EQUIPMENT_NAME", "SOURCE_OWNER_NAME", "EQUIPMENT_TAG");
        var utilityGroupColumn = Pick(columns, "UTILITY_GROUP");
        var utilityColumn = Pick(columns, "SOURCE_UTILITY", "UTILITY");
        var sizeColumn = Pick(columns, "SOURCE_SIZE", "SIZE");
        var sxColumn = Require(columns, "TB_ROUTE_PATH", "SOURCE_POSX");
        var syColumn = Require(columns, "TB_ROUTE_PATH", "SOURCE_POSY");
        var szColumn = Require(columns, "TB_ROUTE_PATH", "SOURCE_POSZ");
        var txColumn = Require(columns, "TB_ROUTE_PATH", "TARGET_POSX");
        var tyColumn = Require(columns, "TB_ROUTE_PATH", "TARGET_POSY");
        var tzColumn = Require(columns, "TB_ROUTE_PATH", "TARGET_POSZ");

        var sql = $"""
            SELECT TRIM({Q(guidColumn)}::text), {TextExpression(processColumn)},
                   {TextExpression(equipmentColumn)}, {TextExpression(utilityGroupColumn)},
                   {TextExpression(utilityColumn)}, {TextExpression(sizeColumn)},
                   {Q(sxColumn)},{Q(syColumn)},{Q(szColumn)},
                   {Q(txColumn)},{Q(tyColumn)},{Q(tzColumn)}
            FROM "TB_ROUTE_PATH"
            WHERE {Q(guidColumn)} IS NOT NULL
              AND {Q(sxColumn)} IS NOT NULL AND {Q(txColumn)} IS NOT NULL
            ORDER BY 2,3,5,1
            LIMIT @limit
            """;
        await using var cmd = new NpgsqlCommand(sql, conn);
        cmd.Parameters.AddWithValue("limit", Math.Clamp(limit, 1, 2000));
        await using var reader = await cmd.ExecuteReaderAsync();
        var result = new List<RoutePresetItem>();
        while (await reader.ReadAsync())
        {
            var guid = Text(reader, 0);
            var process = Text(reader, 1);
            var equipment = Text(reader, 2);
            var utilityGroup = Text(reader, 3);
            var utility = Text(reader, 4);
            var size = Text(reader, 5);
            var start = Point(reader, 6);
            var end = Point(reader, 9);
            result.Add(new RoutePresetItem(
                guid,
                $"{process} | {equipment} | {utilityGroup}/{utility} | {size} | {Short(guid)}",
                process, equipment, utilityGroup, utility, size, start, end));
        }
        return result;
    }

    private static async Task<HashSet<string>> LoadColumnsAsync(NpgsqlConnection conn, string table)
    {
        const string sql = """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=@table
            """;
        await using var cmd = new NpgsqlCommand(sql, conn);
        cmd.Parameters.AddWithValue("table", table);
        await using var reader = await cmd.ExecuteReaderAsync();
        var columns = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        while (await reader.ReadAsync()) columns.Add(reader.GetString(0));
        if (columns.Count == 0) throw new InvalidOperationException($"필수 테이블이 없습니다: {table}");
        return columns;
    }

    private static string? Pick(IReadOnlySet<string> columns, params string[] candidates) =>
        candidates.FirstOrDefault(columns.Contains);

    private static string Require(IReadOnlySet<string> columns, string table, params string[] candidates) =>
        Pick(columns, candidates) ?? throw new InvalidOperationException(
            $"{table}에 필수 컬럼이 없습니다. 후보: {string.Join(", ", candidates)}");

    private static string TextExpression(string? column) => column is null
        ? "''::text"
        : $"COALESCE(TRIM({Q(column)}::text),'')";

    private static string Q(string identifier) => $"\"{identifier.Replace("\"", "\"\"")}\"";

    /// <summary>
    /// route GUID의 실제 상세점을 읽는다. 신규 schema(SEGMENTS에 ROUTE_PATH_GUID)를 우선 사용하고,
    /// 구형 map schema를 두 번째로 시도한다. 상세점이 없으면 Route 시작~종점 직선을 반환한다.
    /// </summary>
    public async Task<List<Point3D>> LoadRoutePointsAsync(string routeGuid)
    {
        var direct = await TryLoadPointsAsync(
            """
            SELECT sd."FROM_POSX",sd."FROM_POSY",sd."FROM_POSZ",
                   sd."TO_POSX",sd."TO_POSY",sd."TO_POSZ"
            FROM "TB_ROUTE_SEGMENTS" rs
            JOIN "TB_ROUTE_SEGMENT_DETAIL" sd ON sd."SEGMENT_GUID"=rs."SEGMENT_GUID"
            WHERE TRIM(rs."ROUTE_PATH_GUID")=@guid
            ORDER BY rs."ORDER",sd."ORDER"
            """, routeGuid);
        if (direct.Count >= 2) return RemoveConsecutiveDuplicates(direct);

        var mapped = await TryLoadPointsAsync(
            """
            SELECT sd."FROM_POSX",sd."FROM_POSY",sd."FROM_POSZ",
                   sd."TO_POSX",sd."TO_POSY",sd."TO_POSZ"
            FROM "TB_ROUTE_PATH" p
            JOIN "TB_ROUTE_PATH_SEGMENT_MAP" sm ON TRIM(sm."ROUTE_PATH_ID")=TRIM(p."ROUTE_PATH_ID")
            JOIN "TB_ROUTE_SEGMENT_DETAIL" sd ON sd."SEGMENT_GUID"=sm."SEGMENT_GUID"
            WHERE TRIM(p."ROUTE_PATH_GUID")=@guid
            ORDER BY sm."SEGMENT_ORDER",sd."ORDER"
            """, routeGuid);
        if (mapped.Count >= 2) return RemoveConsecutiveDuplicates(mapped);

        await using var conn = new NpgsqlConnection(_connectionString);
        await conn.OpenAsync();
        await using var cmd = new NpgsqlCommand(
            """
            SELECT "SOURCE_POSX","SOURCE_POSY","SOURCE_POSZ",
                   "TARGET_POSX","TARGET_POSY","TARGET_POSZ"
            FROM "TB_ROUTE_PATH" WHERE TRIM("ROUTE_PATH_GUID")=@guid LIMIT 1
            """, conn);
        cmd.Parameters.AddWithValue("guid", routeGuid.Trim());
        await using var reader = await cmd.ExecuteReaderAsync();
        if (await reader.ReadAsync()) return [Point(reader, 0), Point(reader, 3)];
        return [];
    }

    /// <summary>
    /// Top-K 후보를 한 건씩 조회하면 후보 수만큼 DB 연결과 query가 반복된다. 후보 GUID 전체를
    /// 한 번에 조회하여 실제 상세 polyline을 route별 Dictionary로 반환한다. 신규 direct schema,
    /// 구형 map schema, TB_ROUTE_PATH 시작~종점 순으로 보완한다.
    /// </summary>
    public async Task<IReadOnlyDictionary<string, List<Point3D>>> LoadRoutePointsBatchAsync(
        IEnumerable<string> routeGuids)
    {
        var guids = routeGuids.Select(x => x.Trim()).Where(x => x.Length > 0)
            .Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        var result = new Dictionary<string, List<Point3D>>(StringComparer.OrdinalIgnoreCase);
        if (guids.Length == 0) return result;

        await TryAppendPointRowsAsync(
            """
            SELECT TRIM(rs."ROUTE_PATH_GUID"),
                   sd."FROM_POSX",sd."FROM_POSY",sd."FROM_POSZ",
                   sd."TO_POSX",sd."TO_POSY",sd."TO_POSZ"
            FROM "TB_ROUTE_SEGMENTS" rs
            JOIN "TB_ROUTE_SEGMENT_DETAIL" sd ON sd."SEGMENT_GUID"=rs."SEGMENT_GUID"
            WHERE TRIM(rs."ROUTE_PATH_GUID") = ANY(@guids)
            ORDER BY TRIM(rs."ROUTE_PATH_GUID"),rs."ORDER",sd."ORDER"
            """, guids, result);

        var missing = guids.Where(g => !result.TryGetValue(g, out var points) || points.Count < 2).ToArray();
        if (missing.Length > 0)
        {
            await TryAppendPointRowsAsync(
                """
                SELECT TRIM(p."ROUTE_PATH_GUID"),
                       sd."FROM_POSX",sd."FROM_POSY",sd."FROM_POSZ",
                       sd."TO_POSX",sd."TO_POSY",sd."TO_POSZ"
                FROM "TB_ROUTE_PATH" p
                JOIN "TB_ROUTE_PATH_SEGMENT_MAP" sm
                  ON TRIM(sm."ROUTE_PATH_ID")=TRIM(p."ROUTE_PATH_ID")
                JOIN "TB_ROUTE_SEGMENT_DETAIL" sd ON sd."SEGMENT_GUID"=sm."SEGMENT_GUID"
                WHERE TRIM(p."ROUTE_PATH_GUID") = ANY(@guids)
                ORDER BY TRIM(p."ROUTE_PATH_GUID"),sm."SEGMENT_ORDER",sd."ORDER"
                """, missing, result);
        }

        missing = guids.Where(g => !result.TryGetValue(g, out var points) || points.Count < 2).ToArray();
        if (missing.Length > 0)
        {
            await using var conn = new NpgsqlConnection(_connectionString);
            await conn.OpenAsync();
            await using var cmd = new NpgsqlCommand(
                """
                SELECT TRIM("ROUTE_PATH_GUID"),
                       "SOURCE_POSX","SOURCE_POSY","SOURCE_POSZ",
                       "TARGET_POSX","TARGET_POSY","TARGET_POSZ"
                FROM "TB_ROUTE_PATH"
                WHERE TRIM("ROUTE_PATH_GUID") = ANY(@guids)
                """, conn);
            cmd.Parameters.AddWithValue("guids", missing);
            await using var reader = await cmd.ExecuteReaderAsync();
            while (await reader.ReadAsync())
                result[Text(reader, 0)] = [Point(reader, 1), Point(reader, 4)];
        }

        foreach (var guid in result.Keys.ToList())
            result[guid] = RemoveConsecutiveDuplicates(result[guid]);
        return result;
    }

    private async Task TryAppendPointRowsAsync(string sql, string[] guids,
        Dictionary<string, List<Point3D>> target)
    {
        try
        {
            await using var conn = new NpgsqlConnection(_connectionString);
            await conn.OpenAsync();
            await using var cmd = new NpgsqlCommand(sql, conn);
            cmd.Parameters.AddWithValue("guids", guids);
            await using var reader = await cmd.ExecuteReaderAsync();
            while (await reader.ReadAsync())
            {
                var guid = Text(reader, 0);
                if (!target.TryGetValue(guid, out var points))
                {
                    points = [];
                    target[guid] = points;
                }
                var from = Point(reader, 1);
                var to = Point(reader, 4);
                if (points.Count == 0 || (points[^1] - from).Length > 1e-6) points.Add(from);
                if (points.Count == 0 || (points[^1] - to).Length > 1e-6) points.Add(to);
            }
        }
        catch (PostgresException)
        {
            // schema variant 탐색 query이므로 다음 query가 보완한다.
        }
    }

    public async Task<IReadOnlyList<BimObstacle>> LoadObstaclesAsync(
        Point3D min, Point3D max, double margin, int limit)
    {
        await using var conn = new NpgsqlConnection(_connectionString);
        await conn.OpenAsync();
        const string sql = """
            SELECT COALESCE("DDWORKS_TYPE",''),
                   "AABB_MINX","AABB_MINY","AABB_MINZ",
                   "AABB_MAXX","AABB_MAXY","AABB_MAXZ"
            FROM "TB_BIM_OBSTACLE"
            WHERE "DDWORKS_TYPE" = ANY(@types)
              AND "AABB_MINX" IS NOT NULL AND "AABB_MAXX" IS NOT NULL
              AND "AABB_MINY" IS NOT NULL AND "AABB_MAXY" IS NOT NULL
              AND "AABB_MINZ" IS NOT NULL AND "AABB_MAXZ" IS NOT NULL
              AND "AABB_MAXX" >= @minx AND "AABB_MINX" <= @maxx
              AND "AABB_MAXY" >= @miny AND "AABB_MINY" <= @maxy
              AND "AABB_MAXZ" >= @minz AND "AABB_MINZ" <= @maxz
            ORDER BY
              (("AABB_MINX"+"AABB_MAXX")/2-@centerx) * (("AABB_MINX"+"AABB_MAXX")/2-@centerx) +
              (("AABB_MINY"+"AABB_MAXY")/2-@centery) * (("AABB_MINY"+"AABB_MAXY")/2-@centery) +
              (("AABB_MINZ"+"AABB_MAXZ")/2-@centerz) * (("AABB_MINZ"+"AABB_MAXZ")/2-@centerz)
            LIMIT @limit
            """;
        await using var cmd = new NpgsqlCommand(sql, conn);
        cmd.Parameters.AddWithValue("types", new[]
        {
            "COLUMN_ARCHITECTURE", "COLUMN_STRUCTURE", "BEAM_ARCHITECTURE", "BEAM_STRUCTURE"
        });
        cmd.Parameters.AddWithValue("minx", min.X - margin);
        cmd.Parameters.AddWithValue("miny", min.Y - margin);
        cmd.Parameters.AddWithValue("minz", min.Z - margin);
        cmd.Parameters.AddWithValue("maxx", max.X + margin);
        cmd.Parameters.AddWithValue("maxy", max.Y + margin);
        cmd.Parameters.AddWithValue("maxz", max.Z + margin);
        cmd.Parameters.AddWithValue("centerx", (min.X + max.X) / 2);
        cmd.Parameters.AddWithValue("centery", (min.Y + max.Y) / 2);
        cmd.Parameters.AddWithValue("centerz", (min.Z + max.Z) / 2);
        cmd.Parameters.AddWithValue("limit", Math.Clamp(limit, 1, 20_000));
        await using var reader = await cmd.ExecuteReaderAsync();
        var result = new List<BimObstacle>();
        while (await reader.ReadAsync())
            result.Add(new BimObstacle(Text(reader, 0), Point(reader, 1), Point(reader, 4)));
        return result;
    }

    /// <summary>
    /// Tools/ExtractBendFeaturePoints.py가 TB_ROUTE_BEND_FEATURE_POINT에 적재한 개별 꺾임점을
    /// route GUID 목록으로 일괄 조회한다. 이 테이블은 build를 아직 실행하지 않은 환경에서는
    /// 존재하지 않을 수 있으므로(42P01), 그 경우 빈 목록으로 조용히 fallback한다 — 이 도구는
    /// 어디까지나 보조 시각화이며 없다고 해서 기존 Top-K/그룹 비교 화면이 막혀서는 안 된다.
    /// </summary>
    public async Task<IReadOnlyList<BendFeatureMarker>> LoadBendFeaturePointsAsync(IEnumerable<string> routeGuids)
    {
        var guids = routeGuids.Select(x => x.Trim()).Where(x => x.Length > 0)
            .Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        var result = new List<BendFeatureMarker>();
        if (guids.Length == 0) return result;
        const string sql = """
            SELECT TRIM("ROUTE_PATH_GUID"),"CAUSE","TRANSITION_TYPE","SEGMENT_ZONE",
                   ST_X("POINT_3D"),ST_Y("POINT_3D"),ST_Z("POINT_3D")
            FROM "TB_ROUTE_BEND_FEATURE_POINT"
            WHERE TRIM("ROUTE_PATH_GUID") = ANY(@guids)
            """;
        try
        {
            await using var conn = new NpgsqlConnection(_connectionString);
            await conn.OpenAsync();
            await using var cmd = new NpgsqlCommand(sql, conn);
            cmd.Parameters.AddWithValue("guids", guids);
            await using var reader = await cmd.ExecuteReaderAsync();
            while (await reader.ReadAsync())
            {
                result.Add(new BendFeatureMarker(
                    Text(reader, 0), Text(reader, 1), Text(reader, 2), Text(reader, 3),
                    new Point3D(Number(reader, 4), Number(reader, 5), Number(reader, 6))));
            }
        }
        catch (PostgresException ex) when (ex.SqlState == "42P01")
        {
            // TB_ROUTE_BEND_FEATURE_POINT가 아직 create-schema/build되지 않은 환경.
        }
        return result;
    }

    /// <summary>
    /// Tools/PathSegmenter.py가 TB_ROUTE_PATH_SEGMENTATION에 적재한 Start Stub/Middle Trunk/
    /// End Stub 3구간 지오메트리를 route GUID 목록으로 일괄 조회한다. ST_DumpPoints로 지오메트리를
    /// SQL에서 점열로 풀어내므로 C# 쪽에 WKT 파서가 필요 없다. Bend Feature Point와 동일하게
    /// build 미실행 환경(42P01)에서는 조용히 빈 결과로 fallback한다 — 선택 route 오버레이일 뿐
    /// 이 데이터가 없다고 기존 화면이 막혀서는 안 된다.
    /// </summary>
    public async Task<IReadOnlyList<PathSegmentGeometry>> LoadPathSegmentationBatchAsync(IEnumerable<string> routeGuids)
    {
        var guids = routeGuids.Select(x => x.Trim()).Where(x => x.Length > 0)
            .Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        var result = new List<PathSegmentGeometry>();
        if (guids.Length == 0) return result;
        const string sql = """
            SELECT TRIM("ROUTE_PATH_GUID"),'START' AS kind,(dump).path[1] AS point_idx,
                   ST_X((dump).geom),ST_Y((dump).geom),ST_Z((dump).geom)
              FROM (SELECT "ROUTE_PATH_GUID", ST_DumpPoints("START_STUB_GEOM") AS dump
                      FROM "TB_ROUTE_PATH_SEGMENTATION" WHERE TRIM("ROUTE_PATH_GUID")=ANY(@guids)) t
            UNION ALL
            SELECT TRIM("ROUTE_PATH_GUID"),'MIDDLE',(dump).path[1],
                   ST_X((dump).geom),ST_Y((dump).geom),ST_Z((dump).geom)
              FROM (SELECT "ROUTE_PATH_GUID", ST_DumpPoints("MIDDLE_TRUNK_GEOM") AS dump
                      FROM "TB_ROUTE_PATH_SEGMENTATION" WHERE TRIM("ROUTE_PATH_GUID")=ANY(@guids)) t
            UNION ALL
            SELECT TRIM("ROUTE_PATH_GUID"),'END',(dump).path[1],
                   ST_X((dump).geom),ST_Y((dump).geom),ST_Z((dump).geom)
              FROM (SELECT "ROUTE_PATH_GUID", ST_DumpPoints("END_STUB_GEOM") AS dump
                      FROM "TB_ROUTE_PATH_SEGMENTATION" WHERE TRIM("ROUTE_PATH_GUID")=ANY(@guids)) t
             ORDER BY 1,2,3
            """;
        try
        {
            await using var conn = new NpgsqlConnection(_connectionString);
            await conn.OpenAsync();
            await using var cmd = new NpgsqlCommand(sql, conn);
            cmd.Parameters.AddWithValue("guids", guids);
            await using var reader = await cmd.ExecuteReaderAsync();
            var byGuid = new Dictionary<string, (List<Point3D> Start, List<Point3D> Middle, List<Point3D> End)>(
                StringComparer.OrdinalIgnoreCase);
            while (await reader.ReadAsync())
            {
                var guid = Text(reader, 0);
                var kind = Text(reader, 1);
                if (!byGuid.TryGetValue(guid, out var lists))
                    byGuid[guid] = lists = ([], [], []);
                var point = Point(reader, 3);
                switch (kind)
                {
                    case "START": lists.Start.Add(point); break;
                    case "MIDDLE": lists.Middle.Add(point); break;
                    default: lists.End.Add(point); break;
                }
            }
            foreach (var (guid, lists) in byGuid)
                result.Add(new PathSegmentGeometry(guid, lists.Start, lists.Middle, lists.End));
        }
        catch (PostgresException ex) when (ex.SqlState == "42P01")
        {
            // TB_ROUTE_PATH_SEGMENTATION이 아직 create-schema/build되지 않은 환경.
        }
        return result;
    }

    /// <summary>
    /// TB_ROUTE_PATH의 시작 PoC(메인장비, EQUIPMENT_NAME)/종단 PoC(TARGET_OWNER_NAME) 정보를
    /// route GUID 목록으로 일괄 조회한다. 종단 객체 이름은 TB_EQUIPMENTS/TB_DUCT/TB_LATERAL_PIPE와
    /// 대조해 메인장비/부대장비/덕트/레터럴배관으로 분류한다 — AutoRouteFinder/ObstacleDbLoader.cs와
    /// RubberBandRoutingSuite의 씬 로더가 이미 같은 세 테이블을 항상 존재하는 것으로 전제하고 읽으므로
    /// (TB_BIM_OBSTACLE과 동급의 핵심 테이블), 이 함수도 42P01에 대한 방어적 fallback을 두지 않는다.
    /// </summary>
    public async Task<IReadOnlyDictionary<string, RouteEndpointInfo>> LoadRouteEndpointsBatchAsync(
        IEnumerable<string> routeGuids)
    {
        var guids = routeGuids.Select(x => x.Trim()).Where(x => x.Length > 0)
            .Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        var result = new Dictionary<string, RouteEndpointInfo>(StringComparer.OrdinalIgnoreCase);
        if (guids.Length == 0) return result;

        await using var conn = new NpgsqlConnection(_connectionString);
        await conn.OpenAsync();
        var columns = await LoadColumnsAsync(conn, "TB_ROUTE_PATH");
        var equipmentColumn = Pick(columns, "EQUIPMENT_NAME", "SOURCE_OWNER_NAME", "EQUIPMENT_TAG");

        var sql = $"""
            SELECT TRIM(rp."ROUTE_PATH_GUID"), {TextExpression(equipmentColumn)},
                   COALESCE(TRIM(rp."TARGET_OWNER_NAME"), ''),
                   CASE
                       WHEN eq."INSTANCE_NAME" IS NOT NULL
                            AND UPPER(COALESCE(eq."MAIN_SUB_TYPE", '')) = 'MAINTOOL' THEN 'MAIN_EQUIPMENT'
                       WHEN eq."INSTANCE_NAME" IS NOT NULL THEN 'AUX_EQUIPMENT'
                       WHEN du."INSTANCE_NAME" IS NOT NULL THEN 'DUCT'
                       WHEN lp."INSTANCE_NAME" IS NOT NULL THEN 'LATERAL'
                       ELSE ''
                   END
            FROM "TB_ROUTE_PATH" rp
            LEFT JOIN "TB_EQUIPMENTS"  eq ON TRIM(eq."INSTANCE_NAME") = TRIM(rp."TARGET_OWNER_NAME")
            LEFT JOIN "TB_DUCT"        du ON TRIM(du."INSTANCE_NAME") = TRIM(rp."TARGET_OWNER_NAME")
            LEFT JOIN "TB_LATERAL_PIPE" lp ON TRIM(lp."INSTANCE_NAME") = TRIM(rp."TARGET_OWNER_NAME")
            WHERE TRIM(rp."ROUTE_PATH_GUID") = ANY(@guids)
            """;
        await using var cmd = new NpgsqlCommand(sql, conn);
        cmd.Parameters.AddWithValue("guids", guids);
        await using var reader = await cmd.ExecuteReaderAsync();
        while (await reader.ReadAsync())
        {
            var guid = Text(reader, 0);
            result[guid] = new RouteEndpointInfo(Text(reader, 1), Text(reader, 2), Text(reader, 3));
        }
        return result;
    }

    private async Task<List<Point3D>> TryLoadPointsAsync(string sql, string routeGuid)
    {
        try
        {
            await using var conn = new NpgsqlConnection(_connectionString);
            await conn.OpenAsync();
            await using var cmd = new NpgsqlCommand(sql, conn);
            cmd.Parameters.AddWithValue("guid", routeGuid.Trim());
            await using var reader = await cmd.ExecuteReaderAsync();
            var points = new List<Point3D>();
            while (await reader.ReadAsync())
            {
                if (points.Count == 0) points.Add(Point(reader, 0));
                points.Add(Point(reader, 3));
            }
            return points;
        }
        catch (PostgresException)
        {
            // 이 함수의 query는 schema variant 탐색용이다. 테이블/컬럼/형변환 차이가 있으면
            // 다음 호환 query 또는 시작~종점 직선 fallback으로 진행한다.
            return [];
        }
    }

    private static List<Point3D> RemoveConsecutiveDuplicates(IEnumerable<Point3D> source)
    {
        var result = new List<Point3D>();
        foreach (var point in source)
        {
            if (result.Count == 0 || (result[^1] - point).Length > 1e-6) result.Add(point);
        }
        return result;
    }

    private static Point3D Point(NpgsqlDataReader reader, int offset) => new(
        Number(reader, offset), Number(reader, offset + 1), Number(reader, offset + 2));

    private static double Number(NpgsqlDataReader reader, int ordinal) => reader.IsDBNull(ordinal)
        ? 0
        : Convert.ToDouble(reader.GetValue(ordinal), CultureInfo.InvariantCulture);

    private static string Text(NpgsqlDataReader reader, int ordinal) =>
        reader.IsDBNull(ordinal) ? "" : Convert.ToString(reader.GetValue(ordinal), CultureInfo.InvariantCulture)?.Trim() ?? "";

    private static string Short(string value) => value.Length > 10 ? value[..10] + "…" : value;
}
