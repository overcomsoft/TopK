using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Npgsql;

namespace RubberBandRouting.Engine;

public sealed class PostgresConnectionOptions
{
    public string Host { get; set; } = "localhost";
    public int Port { get; set; } = 5432;
    public string Database { get; set; } = "DDW_AI_DB";
    public string Username { get; set; } = "postgres";
    public string Password { get; set; } = string.Empty;
    public int TimeoutSeconds { get; set; } = 10;

    public static PostgresConnectionOptions FromEnvironment()
    {
        var options = new PostgresConnectionOptions();
        options.Host = Environment.GetEnvironmentVariable("PGHOST") ?? options.Host;
        if (int.TryParse(Environment.GetEnvironmentVariable("PGPORT"), NumberStyles.Integer, CultureInfo.InvariantCulture, out var port)) options.Port = port;
        options.Database = Environment.GetEnvironmentVariable("PGDATABASE") ?? options.Database;
        options.Username = Environment.GetEnvironmentVariable("PGUSER") ?? options.Username;
        options.Password = Environment.GetEnvironmentVariable("PGPASSWORD") ?? options.Password;
        return options;
    }

    public string ConnectionString => $"Host={Host};Port={Port};Database={Database};Username={Username};Password={Password};Timeout={TimeoutSeconds};Encoding=UTF8";
}

public sealed record RoutingProject(
    int Index,
    string GroupId,
    string GroupName,
    string? Bay,
    string? Process,
    Aabb Bounds)
{
    public string DisplayName => $"{GroupName} / {Bay ?? "?"} / {Process ?? "?"}";
    public override string ToString() => DisplayName;
}

public enum PocKind
{
    Unknown,
    Equipment,
    Duct,
    Lateral
}

public sealed record SceneObject(
    string Name,
    string Category,
    string? Utility,
    Aabb Bounds,
    bool IsPassThrough = false);

public sealed record PocPoint(
    PocKind Kind,
    string Name,
    string OwnerName,
    string? Utility,
    string? Group,
    Vec3 Position,
    double SizeMm = 0,
    bool IsRouteStart = false,
    bool IsRouteEnd = false,
    string? RoutePathGuid = null);

public sealed record RouteTask(
    string Name,
    string? Utility,
    string? Group,
    Vec3 Start,
    Vec3 End,
    string? SourceName,
    string? TargetName,
    string? RoutePathGuid,
    double DiameterMm)
{
    public string UtilityLabel => $"[{(string.IsNullOrWhiteSpace(Group) ? "?" : Group)}] {(string.IsNullOrWhiteSpace(Utility) ? "?" : Utility)}";
}

public sealed record ExistingRoutePath(
    string RoutePathGuid,
    string? Utility,
    string? Group,
    string? SourceName,
    string? TargetName,
    double DiameterMm,
    List<Vec3> Points);

public sealed class RoutingScene
{
    public RoutingProject Project { get; init; } = new(0, string.Empty, string.Empty, null, null, new Aabb(new Vec3(), new Vec3()));
    public List<SceneObject> Obstacles { get; } = new();
    public List<SceneObject> Equipment { get; } = new();
    public List<SceneObject> DuctLaterals { get; } = new();
    public List<PocPoint> EquipmentPocs { get; } = new();
    public List<PocPoint> DuctLateralPocs { get; } = new();
    public List<RouteTask> Tasks { get; } = new();
    public List<ExistingRoutePath> ExistingRoutePaths { get; } = new();
    public List<string> LoadWarnings { get; } = new();

    public IReadOnlyList<Aabb> CollisionObstacles => Obstacles.Where(o => !o.IsPassThrough).Select(o => o.Bounds).ToList();
}

public sealed class PostgresRoutingDataLoader
{
    private const double ScopeMarginMm = 500.0;

    public async Task<List<RoutingProject>> ListProjectsAsync(PostgresConnectionOptions options, CancellationToken cancellationToken = default)
    {
        var list = new List<RoutingProject>();
        await using var conn = new NpgsqlConnection(options.ConnectionString);
        await conn.OpenAsync(cancellationToken);
        await using var cmd = new NpgsqlCommand(
            @"SELECT ""TAG_GROUP_ID"",""TAG_GROUP_NM"",""BAY_GROUP_NM"",""PROCESS_GROUP_NM"",
                     ""AABB_MINX"",""AABB_MINY"",""AABB_MINZ"",""AABB_MAXX"",""AABB_MAXY"",""AABB_MAXZ""
              FROM ""TB_SPACE_GROUP_INFO""
              ORDER BY ""PROCESS_GROUP_NM"",""TAG_GROUP_NM""", conn);
        await using var r = await cmd.ExecuteReaderAsync(cancellationToken);
        var index = 1;
        while (await r.ReadAsync(cancellationToken))
        {
            var bounds = new Aabb(
                new Vec3(Dbl(r, 4), Dbl(r, 5), Dbl(r, 6)),
                new Vec3(Dbl(r, 7), Dbl(r, 8), Dbl(r, 9)),
                false,
                Str(r, 1));
            list.Add(new RoutingProject(index++, Str(r, 0), Str(r, 1), NullStr(r, 2), NullStr(r, 3), bounds));
        }
        return list;
    }

    public async Task<RoutingScene> LoadSceneAsync(PostgresConnectionOptions options, RoutingProject project, CancellationToken cancellationToken = default)
    {
        await using var conn = new NpgsqlConnection(options.ConnectionString);
        await conn.OpenAsync(cancellationToken);

        var scene = new RoutingScene { Project = project };
        var minx = project.Bounds.Min.X - ScopeMarginMm;
        var miny = project.Bounds.Min.Y - ScopeMarginMm;
        var minz = project.Bounds.Min.Z - ScopeMarginMm;
        var maxx = project.Bounds.Max.X + ScopeMarginMm;
        var maxy = project.Bounds.Max.Y + ScopeMarginMm;
        var maxz = project.Bounds.Max.Z + ScopeMarginMm;

        await LoadObstaclesAsync(conn, scene, minx, miny, minz, maxx, maxy, maxz, cancellationToken);
        await LoadEquipmentAsync(conn, scene, minx, miny, maxx, maxy, cancellationToken);
        await LoadDuctLateralAsync(conn, scene, "TB_LATERAL_PIPE", "LATERAL", minx, miny, maxx, maxy, cancellationToken);
        await LoadDuctLateralAsync(conn, scene, "TB_DUCT", "DUCT", minx, miny, maxx, maxy, cancellationToken);
        await TryLoadPocsAsync(conn, scene, minx, miny, maxx, maxy, cancellationToken);
        await TryLoadRouteTasksAsync(conn, scene, minx, miny, maxx, maxy, cancellationToken);
        await TryLoadExistingRoutePathsAsync(conn, scene, minx, miny, maxx, maxy, cancellationToken);
        AddEndpointPocs(scene);
        if (scene.Tasks.Count == 0) BuildNearestPocTasks(scene);
        return scene;
    }

    private static async Task LoadObstaclesAsync(NpgsqlConnection conn, RoutingScene scene, double minx, double miny, double minz, double maxx, double maxy, double maxz, CancellationToken ct)
    {
        const string sql = @"SELECT ""AABB_MINX"",""AABB_MINY"",""AABB_MINZ"",""AABB_MAXX"",""AABB_MAXY"",""AABB_MAXZ"",
                                   ""INSTANCE_NAME"",""OST_TYPE"",""DDWORKS_TYPE"",""COLLISION_PASS""
                            FROM ""TB_BIM_OBSTACLE""
                            WHERE ""AABB_MINX""<=@maxx AND ""AABB_MAXX"">=@minx AND ""AABB_MINY""<=@maxy AND ""AABB_MAXY"">=@miny";
        await using var cmd = new NpgsqlCommand(sql, conn);
        AddXy(cmd, minx, miny, maxx, maxy);
        await using var r = await cmd.ExecuteReaderAsync(ct);
        while (await r.ReadAsync(ct))
        {
            var name = Str(r, 6);
            if (name.Contains("damper", StringComparison.OrdinalIgnoreCase)) continue;
            var box = ClipBox(ReadBox(r, 0, name), minx, miny, minz, maxx, maxy, maxz);
            if (box == null) continue;
            var ost = Str(r, 7);
            var ddworks = Str(r, 8);
            var pass = !r.IsDBNull(9) ? Convert.ToInt64(r.GetValue(9), CultureInfo.InvariantCulture) != 0 : IsPassThroughByType(ost, ddworks);
            scene.Obstacles.Add(new SceneObject(name, "OBSTACLE", null, box.Value, pass));
        }
    }

    private static async Task LoadEquipmentAsync(NpgsqlConnection conn, RoutingScene scene, double minx, double miny, double maxx, double maxy, CancellationToken ct)
    {
        const string sql = @"SELECT ""INSTANCE_NAME"",""MAIN_SUB_TYPE"",
                                   ""AABB_MINX"",""AABB_MINY"",""AABB_MINZ"",""AABB_MAXX"",""AABB_MAXY"",""AABB_MAXZ""
                            FROM ""TB_EQUIPMENTS""
                            WHERE ""AABB_MINX""<=@maxx AND ""AABB_MAXX"">=@minx AND ""AABB_MINY""<=@maxy AND ""AABB_MAXY"">=@miny";
        await using var cmd = new NpgsqlCommand(sql, conn);
        AddXy(cmd, minx, miny, maxx, maxy);
        await using var r = await cmd.ExecuteReaderAsync(ct);
        while (await r.ReadAsync(ct))
        {
            var box = ReadBox(r, 2, Str(r, 0));
            if (!IsValid(box)) continue;
            var category = string.Equals(NullStr(r, 1), "MainTool", StringComparison.OrdinalIgnoreCase) ? "MAIN_EQUIPMENT" : "EQUIPMENT";
            scene.Equipment.Add(new SceneObject(Str(r, 0), category, null, box));
        }
    }

    private static async Task LoadDuctLateralAsync(NpgsqlConnection conn, RoutingScene scene, string table, string category, double minx, double miny, double maxx, double maxy, CancellationToken ct)
    {
        var sql = $@"SELECT ""INSTANCE_NAME"",""UTILITY"",
                           ""AABB_MINX"",""AABB_MINY"",""AABB_MINZ"",""AABB_MAXX"",""AABB_MAXY"",""AABB_MAXZ""
                    FROM ""{table}""
                    WHERE ""AABB_MINX""<=@maxx AND ""AABB_MAXX"">=@minx AND ""AABB_MINY""<=@maxy AND ""AABB_MAXY"">=@miny";
        await using var cmd = new NpgsqlCommand(sql, conn);
        AddXy(cmd, minx, miny, maxx, maxy);
        await using var r = await cmd.ExecuteReaderAsync(ct);
        while (await r.ReadAsync(ct))
        {
            var box = ReadBox(r, 2, Str(r, 0));
            if (!IsValid(box)) continue;
            scene.DuctLaterals.Add(new SceneObject(Str(r, 0), category, NullStr(r, 1), box));
        }
    }

    private static async Task TryLoadPocsAsync(NpgsqlConnection conn, RoutingScene scene, double minx, double miny, double maxx, double maxy, CancellationToken ct)
    {
        try { await LoadPocsAsync(conn, scene, minx, miny, maxx, maxy, ct); }
        catch (Exception ex) { scene.LoadWarnings.Add($"PoC 로드 실패: {ex.Message}"); }
    }

    private static async Task LoadPocsAsync(NpgsqlConnection conn, RoutingScene scene, double minx, double miny, double maxx, double maxy, CancellationToken ct)
    {
        var cols = await ColumnSetAsync(conn, "TB_POCINSTANCES", ct);
        if (cols.Count == 0) return;

        var cx = Pick(cols, "POSX", "POS_X", "POSITION_X", "POINT_X", "X", "POC_POSX", "FROM_POSX");
        var cy = Pick(cols, "POSY", "POS_Y", "POSITION_Y", "POINT_Y", "Y", "POC_POSY", "FROM_POSY");
        var cz = Pick(cols, "POSZ", "POS_Z", "POSITION_Z", "POINT_Z", "Z", "POC_POSZ", "FROM_POSZ");
        if (cx == null || cy == null || cz == null) return;

        var cName = Pick(cols, "POC_NAME", "NAME", "INSTANCE_NAME", "TAG_NAME");
        var cOwner = Pick(cols, "OWNER_INSTANCE_NAME", "OWNER_NAME", "EQUIPMENT_NAME", "TARGET_OWNER_NAME");
        var cOwnerType = Pick(cols, "OWNER_INSTANCE_TYPE", "OWNER_TYPE", "CATEGORY", "TYPE");
        var cUtility = Pick(cols, "UTILITY", "SOURCE_UTILITY");
        var cSize = Pick(cols, "SIZE", "POC_SIZE", "SOURCE_SIZE", "DIAMETER", "DIAMETER_MM", "PIPE_SIZE");
        string select(string? c) => c == null ? "NULL" : Q(c);

        var sql = $@"SELECT {Q(cx)}, {Q(cy)}, {Q(cz)}, {select(cName)}, {select(cOwner)}, {select(cOwnerType)}, {select(cUtility)}, {select(cSize)}
                     FROM ""TB_POCINSTANCES""
                     WHERE {Q(cx)} BETWEEN @minx AND @maxx AND {Q(cy)} BETWEEN @miny AND @maxy";
        await using var cmd = new NpgsqlCommand(sql, conn);
        AddXy(cmd, minx, miny, maxx, maxy);
        await using var r = await cmd.ExecuteReaderAsync(ct);
        while (await r.ReadAsync(ct))
        {
            var pos = new Vec3(DblAny(r, 0), DblAny(r, 1), DblAny(r, 2));
            var owner = Str(r, 4);
            var kind = ClassifyPoc(scene, Str(r, 5), owner, pos, out var matchedOwner, out var matchedUtility);
            if (string.IsNullOrWhiteSpace(owner)) owner = matchedOwner;
            var utility = NullStr(r, 6) ?? matchedUtility;
            var sizeMm = ParsePipeSizeMm(NullStr(r, 7));
            AddPoc(scene, new PocPoint(kind, string.IsNullOrWhiteSpace(Str(r, 3)) ? owner : Str(r, 3), owner, utility, null, pos, sizeMm));
        }
    }

    private static async Task TryLoadRouteTasksAsync(NpgsqlConnection conn, RoutingScene scene, double minx, double miny, double maxx, double maxy, CancellationToken ct)
    {
        try { await LoadRouteTasksAsync(conn, scene, minx, miny, maxx, maxy, ct); }
        catch (Exception ex) { scene.LoadWarnings.Add($"라우팅 태스크 로드 실패: {ex.Message}"); }
    }


    private static async Task TryLoadExistingRoutePathsAsync(NpgsqlConnection conn, RoutingScene scene, double minx, double miny, double maxx, double maxy, CancellationToken ct)
    {
        try { await LoadExistingRoutePathsAsync(conn, scene, minx, miny, maxx, maxy, ct); }
        catch (Exception ex) { scene.LoadWarnings.Add($"기존경로 로드 실패: {ex.Message}"); }
    }

    private static async Task LoadExistingRoutePathsAsync(NpgsqlConnection conn, RoutingScene scene, double minx, double miny, double maxx, double maxy, CancellationToken ct)
    {
        const string sql = @"SELECT s.""ROUTE_PATH_GUID"", rp.""UTILITY_GROUP"", rp.""SOURCE_UTILITY"",
                                   rp.""SOURCE_SIZE"", rp.""EQUIPMENT_NAME"", rp.""TARGET_OWNER_NAME"",
                                   sd.""FROM_POSX"", sd.""FROM_POSY"", sd.""FROM_POSZ"",
                                   sd.""TO_POSX"",   sd.""TO_POSY"",   sd.""TO_POSZ""
                            FROM ""TB_ROUTE_SEGMENT_DETAIL"" sd
                            JOIN ""TB_ROUTE_SEGMENTS"" s ON s.""SEGMENT_GUID"" = sd.""SEGMENT_GUID""
                            JOIN ""TB_ROUTE_PATH"" rp    ON rp.""ROUTE_PATH_GUID"" = s.""ROUTE_PATH_GUID""
                            WHERE rp.""SOURCE_POSX"" BETWEEN @minx AND @maxx
                              AND rp.""SOURCE_POSY"" BETWEEN @miny AND @maxy
                            ORDER BY s.""ROUTE_PATH_GUID"", s.""ORDER"", sd.""ORDER""";
        await using var cmd = new NpgsqlCommand(sql, conn);
        AddXy(cmd, minx, miny, maxx, maxy);
        await using var r = await cmd.ExecuteReaderAsync(ct);

        string? currentGuid = null;
        string? group = null;
        string? utility = null;
        string? sourceName = null;
        string? targetName = null;
                double diameterMm = 0;
        var points = new List<Vec3>();

        void Flush()
        {
            if (string.IsNullOrWhiteSpace(currentGuid) || points.Count < 2) return;
            scene.ExistingRoutePaths.Add(new ExistingRoutePath(currentGuid, utility, group, sourceName, targetName, diameterMm, new List<Vec3>(points)));
        }

        void AddPoint(Vec3 point)
        {
            if (points.Count == 0 || (points[^1] - point).Length > 1.0) points.Add(point);
        }

        while (await r.ReadAsync(ct))
        {
            var guid = Str(r, 0);
            if (!string.Equals(currentGuid, guid, StringComparison.Ordinal))
            {
                Flush();
                currentGuid = guid;
                group = NullStr(r, 1);
                utility = NullStr(r, 2);
                diameterMm = ParsePipeSizeMm(NullStr(r, 3));
                sourceName = NullStr(r, 4);
                targetName = NullStr(r, 5);
                points.Clear();
            }

            if (!(r.IsDBNull(6) || r.IsDBNull(7) || r.IsDBNull(8))) AddPoint(new Vec3(Dbl(r, 6), Dbl(r, 7), Dbl(r, 8)));
            if (!(r.IsDBNull(9) || r.IsDBNull(10) || r.IsDBNull(11))) AddPoint(new Vec3(Dbl(r, 9), Dbl(r, 10), Dbl(r, 11)));
        }
        Flush();
    }
    private static async Task LoadRouteTasksAsync(NpgsqlConnection conn, RoutingScene scene, double minx, double miny, double maxx, double maxy, CancellationToken ct)
    {
        const string sql = @"SELECT ""ROUTE_PATH_GUID"",""UTILITY_GROUP"",""SOURCE_UTILITY"",""SOURCE_SIZE"",
                                   ""EQUIPMENT_NAME"",""TARGET_OWNER_NAME"",
                                   ""SOURCE_POSX"",""SOURCE_POSY"",""SOURCE_POSZ"",
                                   ""TARGET_POSX"",""TARGET_POSY"",""TARGET_POSZ""
                            FROM ""TB_ROUTE_PATH""
                            WHERE ""SOURCE_POSX"" BETWEEN @minx AND @maxx AND ""SOURCE_POSY"" BETWEEN @miny AND @maxy
                            ORDER BY ""UTILITY_GROUP"",""SOURCE_UTILITY"",""EQUIPMENT_NAME""";
        await using var cmd = new NpgsqlCommand(sql, conn);
        AddXy(cmd, minx, miny, maxx, maxy);
        await using var r = await cmd.ExecuteReaderAsync(ct);
        while (await r.ReadAsync(ct))
        {
            if (r.IsDBNull(6) || r.IsDBNull(7) || r.IsDBNull(8) || r.IsDBNull(9) || r.IsDBNull(10) || r.IsDBNull(11)) continue;
            var start = new Vec3(Dbl(r, 6), Dbl(r, 7), Dbl(r, 8));
            var end = new Vec3(Dbl(r, 9), Dbl(r, 10), Dbl(r, 11));
            if ((end - start).Length < 1) continue;
            var source = NullStr(r, 4);
            var target = NullStr(r, 5);
            scene.Tasks.Add(new RouteTask(
                $"{source ?? "Equipment"} -> {target ?? "Duct/Lateral"}",
                NullStr(r, 2),
                NullStr(r, 1),
                start,
                end,
                source,
                target,
                NullStr(r, 0),
                ParsePipeSizeMm(NullStr(r, 3))));
        }
    }

    private static void AddEndpointPocs(RoutingScene scene)
    {
        foreach (var task in scene.Tasks)
        {
            AddPoc(scene, new PocPoint(PocKind.Equipment, task.SourceName ?? "Start PoC", task.SourceName ?? "Equipment", task.Utility, task.Group, task.Start, task.DiameterMm, true, false, task.RoutePathGuid));
            var nearest = Nearest(scene.DuctLaterals, task.End, null);
            var kind = string.Equals(nearest?.Category, "LATERAL", StringComparison.OrdinalIgnoreCase) ? PocKind.Lateral : PocKind.Duct;
            AddPoc(scene, new PocPoint(kind, task.TargetName ?? "End PoC", task.TargetName ?? "Duct/Lateral", task.Utility, task.Group, task.End, task.DiameterMm, false, true, task.RoutePathGuid));
        }
    }

    private static void BuildNearestPocTasks(RoutingScene scene)
    {
        foreach (var start in scene.EquipmentPocs)
        {
            var candidates = scene.DuctLateralPocs
                .Where(p => string.IsNullOrWhiteSpace(start.Utility) || string.IsNullOrWhiteSpace(p.Utility) || string.Equals(start.Utility, p.Utility, StringComparison.OrdinalIgnoreCase))
                .OrderBy(p => (p.Position - start.Position).Length)
                .Take(1);
            foreach (var end in candidates)
            {
                scene.Tasks.Add(new RouteTask($"{start.OwnerName} -> {end.OwnerName}", start.Utility ?? end.Utility, start.Group ?? end.Group, start.Position, end.Position, start.Name, end.Name, null, 0));
            }
        }
    }

    private static PocKind ClassifyPoc(RoutingScene scene, string ownerType, string ownerName, Vec3 pos, out string matchedOwner, out string? matchedUtility)
    {
        matchedOwner = ownerName;
        matchedUtility = null;
        var key = (ownerType + " " + ownerName).ToUpperInvariant();
        if (key.Contains("LATERAL")) return MatchDuctLateral(scene, pos, true, out matchedOwner, out matchedUtility);
        if (key.Contains("DUCT")) return MatchDuctLateral(scene, pos, false, out matchedOwner, out matchedUtility);
        if (key.Contains("EQUIP") || key.Contains("MODEL") || key.Contains("TOOL"))
        {
            var eq = Nearest(scene.Equipment, pos, null);
            if (eq != null) matchedOwner = eq.Name;
            return PocKind.Equipment;
        }

        var nearestEq = Nearest(scene.Equipment, pos, null);
        var nearestDl = Nearest(scene.DuctLaterals, pos, null);
        var de = nearestEq == null ? double.MaxValue : BoxDistance2(pos, nearestEq.Bounds);
        var dd = nearestDl == null ? double.MaxValue : BoxDistance2(pos, nearestDl.Bounds);
        if (de <= dd)
        {
            if (nearestEq != null) matchedOwner = nearestEq.Name;
            return PocKind.Equipment;
        }
        if (nearestDl != null)
        {
            matchedOwner = nearestDl.Name;
            matchedUtility = nearestDl.Utility;
            return string.Equals(nearestDl.Category, "LATERAL", StringComparison.OrdinalIgnoreCase) ? PocKind.Lateral : PocKind.Duct;
        }
        return PocKind.Unknown;
    }

    private static PocKind MatchDuctLateral(RoutingScene scene, Vec3 pos, bool lateralOnly, out string owner, out string? utility)
    {
        var nearest = Nearest(scene.DuctLaterals, pos, lateralOnly ? "LATERAL" : "DUCT");
        owner = nearest?.Name ?? string.Empty;
        utility = nearest?.Utility;
        if (nearest == null) return lateralOnly ? PocKind.Lateral : PocKind.Duct;
        return string.Equals(nearest.Category, "LATERAL", StringComparison.OrdinalIgnoreCase) ? PocKind.Lateral : PocKind.Duct;
    }

    private static SceneObject? Nearest(IEnumerable<SceneObject> objects, Vec3 pos, string? category)
    {
        SceneObject? best = null;
        var bestDistance = double.MaxValue;
        foreach (var item in objects)
        {
            if (category != null && !string.Equals(item.Category, category, StringComparison.OrdinalIgnoreCase)) continue;
            var d = BoxDistance2(pos, item.Bounds);
            if (d < bestDistance)
            {
                bestDistance = d;
                best = item;
            }
        }
        return best;
    }

    private static void AddPoc(RoutingScene scene, PocPoint poc)
    {
        var list = poc.Kind == PocKind.Equipment ? scene.EquipmentPocs : scene.DuctLateralPocs;
        var existingIndex = list.FindIndex(x => IsSamePocEndpoint(x, poc));
        if (existingIndex < 0)
        {
            list.Add(poc);
            return;
        }

        var existing = list[existingIndex];
        list[existingIndex] = existing with
        {
            Name = !string.IsNullOrWhiteSpace(existing.Name) ? existing.Name : poc.Name,
            OwnerName = !string.IsNullOrWhiteSpace(existing.OwnerName) ? existing.OwnerName : poc.OwnerName,
            Utility = existing.Utility ?? poc.Utility,
            Group = existing.Group ?? poc.Group,
            SizeMm = Math.Max(existing.SizeMm, poc.SizeMm),
            IsRouteStart = existing.IsRouteStart || poc.IsRouteStart,
            IsRouteEnd = existing.IsRouteEnd || poc.IsRouteEnd,
            RoutePathGuid = existing.RoutePathGuid ?? poc.RoutePathGuid
        };
    }

    private static bool IsSamePocEndpoint(PocPoint existing, PocPoint incoming)
    {
        if ((existing.Position - incoming.Position).Length >= 1) return false;
        if (string.Equals(existing.Name, incoming.Name, StringComparison.OrdinalIgnoreCase)) return true;
        if (!string.IsNullOrWhiteSpace(existing.OwnerName) && string.Equals(existing.OwnerName, incoming.OwnerName, StringComparison.OrdinalIgnoreCase)) return true;
        return incoming.IsRouteStart || incoming.IsRouteEnd || existing.IsRouteStart || existing.IsRouteEnd;
    }

    private static async Task<HashSet<string>> ColumnSetAsync(NpgsqlConnection conn, string table, CancellationToken ct)
    {
        var set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        await using var cmd = new NpgsqlCommand(@"SELECT column_name FROM information_schema.columns WHERE table_name = @table", conn);
        cmd.Parameters.AddWithValue("@table", table);
        await using var r = await cmd.ExecuteReaderAsync(ct);
        while (await r.ReadAsync(ct)) set.Add(r.GetString(0));
        return set;
    }

    private static string? Pick(HashSet<string> cols, params string[] names) => names.FirstOrDefault(cols.Contains);
    private static string Q(string identifier) => "\"" + identifier.Replace("\"", "\"\"") + "\"";

    private static Aabb ReadBox(NpgsqlDataReader r, int offset, string name) => new(
        new Vec3(Dbl(r, offset), Dbl(r, offset + 1), Dbl(r, offset + 2)),
        new Vec3(Dbl(r, offset + 3), Dbl(r, offset + 4), Dbl(r, offset + 5)),
        false,
        name);

    private static Aabb? ClipBox(Aabb box, double minx, double miny, double minz, double maxx, double maxy, double maxz)
    {
        var clipped = new Aabb(
            new Vec3(Math.Max(box.Min.X, minx), Math.Max(box.Min.Y, miny), Math.Max(box.Min.Z, minz)),
            new Vec3(Math.Min(box.Max.X, maxx), Math.Min(box.Max.Y, maxy), Math.Min(box.Max.Z, maxz)),
            box.IsPenetration,
            box.Name);
        return IsValid(clipped) ? clipped : null;
    }

    private static bool IsValid(Aabb box) => box.Max.X > box.Min.X && box.Max.Y > box.Min.Y && box.Max.Z > box.Min.Z;

    private static bool IsPassThroughByType(string ost, string ddworks) =>
        string.Equals(ost, "OST_Floors", StringComparison.OrdinalIgnoreCase) ||
        string.Equals(ost, "OST_Ceilings", StringComparison.OrdinalIgnoreCase) ||
        (string.Equals(ost, "OST_StructuralFraming", StringComparison.OrdinalIgnoreCase) && string.Equals(ddworks, "BEAM_STRUCTURE", StringComparison.OrdinalIgnoreCase));

    private static double BoxDistance2(Vec3 p, Aabb box)
    {
        var dx = p.X < box.Min.X ? box.Min.X - p.X : p.X > box.Max.X ? p.X - box.Max.X : 0;
        var dy = p.Y < box.Min.Y ? box.Min.Y - p.Y : p.Y > box.Max.Y ? p.Y - box.Max.Y : 0;
        var dz = p.Z < box.Min.Z ? box.Min.Z - p.Z : p.Z > box.Max.Z ? p.Z - box.Max.Z : 0;
        return dx * dx + dy * dy + dz * dz;
    }

    private static void AddXy(NpgsqlCommand cmd, double minx, double miny, double maxx, double maxy)
    {
        cmd.Parameters.AddWithValue("@minx", minx);
        cmd.Parameters.AddWithValue("@miny", miny);
        cmd.Parameters.AddWithValue("@maxx", maxx);
        cmd.Parameters.AddWithValue("@maxy", maxy);
    }

    private static string Str(NpgsqlDataReader r, int i) => r.IsDBNull(i) ? string.Empty : Convert.ToString(r.GetValue(i), CultureInfo.InvariantCulture) ?? string.Empty;
    private static string? NullStr(NpgsqlDataReader r, int i)
    {
        var s = Str(r, i);
        return string.IsNullOrWhiteSpace(s) ? null : s;
    }

    private static double Dbl(NpgsqlDataReader r, int i) => r.IsDBNull(i) ? 0.0 : Convert.ToDouble(r.GetValue(i), CultureInfo.InvariantCulture);

    private static double DblAny(NpgsqlDataReader r, int i)
    {
        if (r.IsDBNull(i)) return 0.0;
        return double.TryParse(Convert.ToString(r.GetValue(i), CultureInfo.InvariantCulture), NumberStyles.Float, CultureInfo.InvariantCulture, out var parsed) ? parsed : 0.0;
    }

    private static double ParsePipeSizeMm(string? size)
    {
        if (string.IsNullOrWhiteSpace(size)) return 0;
        var digits = new string(size.Where(c => char.IsDigit(c) || c == '.').ToArray());
        return double.TryParse(digits, NumberStyles.Float, CultureInfo.InvariantCulture, out var value) ? value : 0;
    }
}









