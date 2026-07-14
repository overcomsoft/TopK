// Context Vector 실제 라우팅 A/B headless runner
//
// 실행 방법(PowerShell)
// ---------------------
// 1) 프로젝트 목록:
//    dotnet ContextRoutingABRunner/bin/Release/net8.0-windows/ContextRoutingABRunner.dll --list-projects --config Tools/tools.settings.json
// 2) ACTIVE strict scope 자동 선택 + dry-run gate:
//    dotnet ContextRoutingABRunner/bin/Release/net8.0-windows/ContextRoutingABRunner.dll --project-id 1 --task-limit 1 --cell-mm 100 --k 3 --config Tools/tools.settings.json
// 3) 두 arm 실제 라우팅 및 DB 로그 저장: 위 명령에 --execute 추가
// 4) 특정 revision 고정: --project-scope-key DB:DDW_AI_DB --model-revision-key snapshot:<sha256>
// 5) 진단용 legacy Context 허용: --allow-global-fallback (운영 기본값에서는 사용 금지)
// 6) Release 빌드: dotnet build ContextRoutingABRunner/ContextRoutingABRunner.csproj -c Release
//
// 전체 흐름도
// ----------
// [CLI/DB 설정] -> [ACTIVE project/revision 자동 해석] -> [공간/Task/장애물 로드]
//       -> BASELINE_TOPK 검색 -------+-------> [Baseline corridor 구성] -> [Routing arm 실행]
//       -> CONTEXT_V3 검색 ----------+-------> [Context corridor 구성]  -> [Routing arm 실행]
//                       |                                |
//                       +-> coverage/scope/encoder gate  +-> 길이/bend/collision/nodes/time
//                                                            |
//                                                            v
//                                     [동일 RUN_ID의 paired DB 로그 + checkpoint]
//
// 핵심 원칙: strict Context가 기본이며, 두 arm은 Context reranking/corridor 이외의 scene,
// grid, task, engine 조건을 동일하게 유지한다. 그래야 결과 차이를 Context 효과로 해석할 수 있다.

using System.Text.Json;
using AutoRouteFinder;
using AutoRouteFinder.Models;
using AutoRoutingLibrary.Core;
using Npgsql;
using RoutingAI.Standalone;

namespace ContextRoutingABRunner;

internal static class Program
{
    private sealed class Options
    {
        // 입력/실행 제어. Execute=false이면 검색과 gate까지만 확인하고 routing/DB write를 하지 않는다.
        public string Config = "Tools/tools.settings.json";
        public bool ListProjects;
        public bool CreateSchema;
        public bool Status;
        public bool InspectScopeSchema;
        public bool CreateScopeSchema;
        public bool Execute;
        public bool Save = true;
        // ProjectId는 TB_SPACE_GROUP_INFO 정렬 결과의 1-based 번호, TaskLimit은 최대 평가 요청 수이다.
        public int ProjectId;
        public int TaskLimit = 1;
        public double CellMm = 50.0;
        public int K = 3;
        public long MaxGridCells = 250_000_000;
        public string RouteGuids = "";
        public bool ContextFirst;
        public string ExperimentId = ContextRoutingAbLogger.ExperimentId;
        // CorridorPolicy: ranked/rank1/union, RankPenaltyFactors: 후보 rank별 추가 비용.
        public string CorridorPolicy = "ranked";
        public string RankPenaltyFactors = "0,0.5,0.75";
        public double CorridorCostFactor = 0.5;
        // 두 값이 비어 있으면 ACTIVE를 자동 선택한다. 한쪽만 지정하는 것은 허용하지 않는다.
        public string ModelRevisionKey = "";
        public string ProjectScopeKey = "";
        public bool ReleaseOwnerEquipment = true;
        // 운영 기본은 strict=true. --allow-global-fallback에서만 false로 바뀐다.
        public bool RequireStrictContextScope = true;
        public double MinContextCoverage;
    }

    private sealed record ArmInputs(
        // Traces: task별 Top-K/provenance, Corridors: engine 비용 조정 cell,
        // BestRanks: 한 cell이 여러 후보에 포함될 때 가장 좋은 rank.
        Dictionary<int, ContextSearchTrace> Traces,
        List<PathCell> Corridors,
        Dictionary<PathCell, int> BestRanks);

    private static async Task<int> Main(string[] args)
    {
        // 전체 orchestration 진입점. 조회성 명령을 먼저 처리한 후 A/B 실행 경로로 진입한다.
        try
        {
            var options = Parse(args);
            var db = LoadDbConfig(options.Config);
            if (options.CreateSchema)
            {
                await CreateSchemaAsync(db.ConnectionString);
                Console.WriteLine("Schema ready: TB_CONTEXT_ROUTING_AB_LOG");
                if (!options.Status) return 0;
            }
            if (options.CreateScopeSchema)
            {
                await ExecuteSqlFileAsync(db.ConnectionString, "Tools/sql/create_route_source_scope_columns.sql");
                await ExecuteSqlFileAsync(db.ConnectionString, "Tools/sql/create_route_context_vector_table.sql");
                Console.WriteLine("Scope schema ready: source tables + TB_ROUTE_CONTEXT_VECTOR");
                return 0;
            }
            if (options.Status)
            {
                await PrintStatusAsync(db.ConnectionString);
                return 0;
            }
            if (options.InspectScopeSchema)
            {
                await InspectScopeSchemaAsync(db.ConnectionString);
                return 0;
            }
            if (options.RequireStrictContextScope &&
                (string.IsNullOrWhiteSpace(options.ProjectScopeKey) || string.IsNullOrWhiteSpace(options.ModelRevisionKey)))
            {
                var activeScope = await ResolveActiveScopeAsync(db.ConnectionString, options.ProjectScopeKey);
                options.ProjectScopeKey = activeScope.ProjectScopeKey;
                options.ModelRevisionKey = activeScope.ModelRevisionKey;
                Console.WriteLine(
                    $"Resolved ACTIVE context scope: project={options.ProjectScopeKey}, revision={options.ModelRevisionKey}");
            }
            var projects = ObstacleDbLoader.ListProjects(db);
            if (options.ListProjects)
            {
                foreach (var project in projects)
                    Console.WriteLine($"{project.ProjectId,3}  {project.Display}  id={project.GroupId}");
                return 0;
            }
            if (options.ProjectId <= 0)
                throw new ArgumentException("--project-id is required unless --list-projects is used.");
            var selected = projects.FirstOrDefault(p => p.ProjectId == options.ProjectId)
                ?? throw new ArgumentException($"Project id not found: {options.ProjectId}");

            Console.WriteLine($"Loading project: {selected.Display}");
            var scene = ObstacleDbLoader.LoadScene(db, selected, options.CellMm);
            long gridCells = checked((long)scene.Grid.Nx * scene.Grid.Ny * scene.Grid.Nz);
            Console.WriteLine(
                $"Scene tasks={scene.Tasks.Count}, obstacles={scene.Obstacles.Count}, equipment={scene.Equipment.Count}, " +
                $"grid={scene.Grid.Nx}x{scene.Grid.Ny}x{scene.Grid.Nz} ({gridCells:N0} cells)");
            if (gridCells > options.MaxGridCells)
                throw new InvalidOperationException(
                    $"Grid safety limit exceeded: {gridCells:N0} > {options.MaxGridCells:N0}. " +
                    "Increase --cell-mm or explicitly raise --max-grid-cells.");

            var requestedGuids = options.RouteGuids.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
                .ToHashSet(StringComparer.OrdinalIgnoreCase);
            var tasks = requestedGuids.Count > 0
                ? scene.Tasks.Where(task => task.RoutePathGuid != null && requestedGuids.Contains(task.RoutePathGuid)).ToList()
                : scene.Tasks.Take(options.TaskLimit).ToList();
            if (tasks.Count == 0) throw new InvalidOperationException("No routing tasks found in the selected project.");
            if (requestedGuids.Count > 0 && tasks.Count != requestedGuids.Count)
            {
                var found = tasks.Select(task => task.RoutePathGuid!).ToHashSet(StringComparer.OrdinalIgnoreCase);
                var missing = requestedGuids.Where(guid => !found.Contains(guid));
                throw new InvalidOperationException($"Requested route GUIDs not found in scene: {string.Join(",", missing)}");
            }
            Console.WriteLine($"Selected tasks: {tasks.Count}");

            Guid runId = Guid.NewGuid();
            Console.WriteLine(
                $"Experiment={options.ExperimentId}, corridor={options.CorridorPolicy}, " +
                $"cost={options.CorridorCostFactor:R}x cell, " +
                $"ranks={EmptyAs(options.RankPenaltyFactors, "(none)")}, " +
                $"source_scope={EmptyAs(options.ProjectScopeKey, "(global-fallback)")}, " +
                $"revision={EmptyAs(options.ModelRevisionKey, "(unspecified)")}");
            var baselineInputs = await BuildArmInputsAsync(
                db, scene.Grid, selected, tasks, false, options.K, options.CorridorPolicy,
                options.RequireStrictContextScope, options.ProjectScopeKey, options.ModelRevisionKey);
            var contextInputs = await BuildArmInputsAsync(
                db, scene.Grid, selected, tasks, true, options.K, options.CorridorPolicy,
                options.RequireStrictContextScope, options.ProjectScopeKey, options.ModelRevisionKey);
            ValidateContextGate(contextInputs, options);
            baselineInputs = CopyContextProvenance(baselineInputs, contextInputs);
            var baselineOnly = baselineInputs.Corridors.Except(contextInputs.Corridors).Count();
            var contextOnly = contextInputs.Corridors.Except(baselineInputs.Corridors).Count();
            Console.WriteLine(
                $"Corridor difference: baseline_only={baselineOnly:N0}, context_only={contextOnly:N0}, " +
                $"shared={baselineInputs.Corridors.Intersect(contextInputs.Corridors).Count():N0}");
            if (!options.Execute)
            {
                Console.WriteLine("DRY-RUN: routing and DB log writes are disabled.");
                Console.WriteLine("DRY-RUN PASS. Add --execute to run both routing arms.");
                return 0;
            }

            List<ContextRoutingAbRecord> baseline;
            List<ContextRoutingAbRecord> context;
            if (options.ContextFirst)
            {
                context = RunArm(db, scene, selected, tasks, true, runId, contextInputs,
                    contextOnly, options);
                await SaveCheckpointAsync(db, context, options.Save, runId);
                baseline = RunArm(db, scene, selected, tasks, false, runId, baselineInputs,
                    baselineOnly, options);
                await SaveCheckpointAsync(db, baseline, options.Save, runId);
            }
            else
            {
                baseline = RunArm(db, scene, selected, tasks, false, runId, baselineInputs,
                    baselineOnly, options);
                await SaveCheckpointAsync(db, baseline, options.Save, runId);
                context = RunArm(db, scene, selected, tasks, true, runId, contextInputs,
                    contextOnly, options);
                await SaveCheckpointAsync(db, context, options.Save, runId);
            }
            PrintPairSummary(baseline, context);
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"ERROR: {ex.Message}");
            return 1;
        }
    }

    private static async Task SaveCheckpointAsync(
        AutoRouteFinder.Models.DbConfig db, IReadOnlyCollection<ContextRoutingAbRecord> rows, bool save, Guid runId)
    {
        if (!save)
        {
            Console.WriteLine($"--no-save: arm checkpoint skipped, rows={rows.Count}");
            return;
        }
        await ContextRoutingAbLogger.SaveBatchAsync(db.ConnectionString, rows);
        Console.WriteLine($"Saved arm checkpoint: rows={rows.Count}, run={runId}");
    }

    private static async Task CreateSchemaAsync(string connectionString)
    {
        string sql = await File.ReadAllTextAsync("Tools/sql/create_context_routing_ab_log_table.sql");
        await using var conn = new NpgsqlConnection(connectionString);
        await conn.OpenAsync();
        await using var cmd = new NpgsqlCommand(sql, conn) { CommandTimeout = 120 };
        await cmd.ExecuteNonQueryAsync();
    }

    private static async Task ExecuteSqlFileAsync(string connectionString, string path)
    {
        string sql = await File.ReadAllTextAsync(path);
        await using var conn = new NpgsqlConnection(connectionString);
        await conn.OpenAsync();
        await using var cmd = new NpgsqlCommand(sql, conn) { CommandTimeout = 300 };
        await cmd.ExecuteNonQueryAsync();
    }

    private sealed record ActiveScope(string ProjectScopeKey, string ModelRevisionKey);

    private static async Task<ActiveScope> ResolveActiveScopeAsync(
        string connectionString, string requestedProjectScopeKey)
    {
        // 프로젝트별 ACTIVE가 정확히 한 건인지 확인한다. 0건/복수건은 암묵 fallback 없이 실패한다.
        await using var conn = new NpgsqlConnection(connectionString);
        await conn.OpenAsync();
        string sql =
            """
            SELECT "PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY"
            FROM "TB_ROUTE_SOURCE_SCOPE_MANIFEST"
            WHERE "STATUS"='ACTIVE'
            """ + (string.IsNullOrWhiteSpace(requestedProjectScopeKey)
                ? " ORDER BY \"PROMOTED_AT\" DESC NULLS LAST"
                : " AND \"PROJECT_SCOPE_KEY\"=@project ORDER BY \"PROMOTED_AT\" DESC NULLS LAST");
        await using var cmd = new NpgsqlCommand(sql, conn);
        if (!string.IsNullOrWhiteSpace(requestedProjectScopeKey))
            cmd.Parameters.AddWithValue("project", requestedProjectScopeKey.Trim());
        var scopes = new List<ActiveScope>();
        await using var reader = await cmd.ExecuteReaderAsync();
        while (await reader.ReadAsync())
            scopes.Add(new ActiveScope(reader.GetString(0), reader.GetString(1)));
        if (scopes.Count == 0)
            throw new InvalidOperationException(
                "No ACTIVE context revision. Build/promote one or use --allow-global-fallback explicitly.");
        if (scopes.Count > 1 && string.IsNullOrWhiteSpace(requestedProjectScopeKey))
            throw new InvalidOperationException(
                "Multiple ACTIVE project scopes exist; specify --project-scope-key.");
        return scopes[0];
    }

    private static async Task PrintStatusAsync(string connectionString)
    {
        await using var conn = new NpgsqlConnection(connectionString);
        await conn.OpenAsync();
        await using var cmd = new NpgsqlCommand(
            """
            SELECT COUNT(*), COUNT(DISTINCT "REQUEST_KEY"), COUNT(DISTINCT "RUN_ID"),
                   COUNT(*) FILTER (WHERE "PROJECT_KEY" IS NOT NULL AND TRIM("PROJECT_KEY") <> ''),
                   COUNT(*) FILTER (WHERE "MODEL_REVISION_KEY" IS NOT NULL AND TRIM("MODEL_REVISION_KEY") <> ''),
                   COUNT(*) FILTER (WHERE "CONTEXT_SNAPSHOT_HASH" IS NOT NULL AND TRIM("CONTEXT_SNAPSHOT_HASH") <> ''),
                   COUNT(DISTINCT "CONTEXT_SNAPSHOT_HASH") FILTER
                     (WHERE "CONTEXT_SNAPSHOT_HASH" IS NOT NULL AND TRIM("CONTEXT_SNAPSHOT_HASH") <> ''),
                   COUNT(*) FILTER (WHERE NOT "CONTEXT_PROVENANCE_CONSISTENT")
            FROM "TB_CONTEXT_ROUTING_AB_LOG"
            """, conn);
        await using var reader = await cmd.ExecuteReaderAsync();
        await reader.ReadAsync();
        Console.WriteLine(
            $"Logs={reader.GetInt64(0)}, requests={reader.GetInt64(1)}, runs={reader.GetInt64(2)}, " +
            $"project_scoped={reader.GetInt64(3)}/{reader.GetInt64(0)}, " +
            $"revision_scoped={reader.GetInt64(4)}/{reader.GetInt64(0)}, " +
            $"provenance={reader.GetInt64(5)}/{reader.GetInt64(0)}, " +
            $"snapshots={reader.GetInt64(6)}, inconsistent={reader.GetInt64(7)}");
        await reader.DisposeAsync();

        await using var latest = new NpgsqlCommand(
            """
            SELECT "EXPERIMENT_ID", "ARM", "MODEL_REVISION_KEY", "CORRIDOR_POLICY", "CORRIDOR_RANK_PROFILE",
                   "CORRIDOR_COST_FACTOR", "CORRIDOR_CELL_COUNT", "CORRIDOR_EXCLUSIVE_CELL_COUNT",
                   "ENDPOINT_RELEASE_COUNT", "COLLISION_COUNT", "ROUTE_SUCCESS"
                   , "CONTEXT_SNAPSHOT_HASH", "CONTEXT_SCOPE_STATUS", "CONTEXT_BUILD_RUN_ID",
                     "CONTEXT_PROVENANCE_CONSISTENT", "CONTEXT_PROVENANCE_ISSUE"
            FROM "TB_CONTEXT_ROUTING_AB_LOG"
            ORDER BY "CREATED_AT" DESC, "LOG_ID" DESC
            LIMIT 2
            """, conn);
        await using var latestReader = await latest.ExecuteReaderAsync();
        while (await latestReader.ReadAsync())
        {
            Console.WriteLine(
                $"Latest: experiment={latestReader.GetString(0)}, arm={latestReader.GetString(1)}, " +
                $"revision={(latestReader.IsDBNull(2) ? "(none)" : latestReader.GetString(2))}, " +
                $"corridor={latestReader.GetString(3)}@{latestReader.GetDouble(5):R}x, " +
                $"rank_profile={(latestReader.IsDBNull(4) ? "(none)" : latestReader.GetString(4))}, " +
                $"cells={latestReader.GetInt32(6)}, exclusive={latestReader.GetInt32(7)}, " +
                $"released={latestReader.GetInt32(8)}, collisions={(latestReader.IsDBNull(9) ? "N/A" : latestReader.GetInt32(9))}, " +
                $"success={latestReader.GetBoolean(10)}, " +
                $"snapshot={(latestReader.IsDBNull(11) ? "(none)" : ShortHash(latestReader.GetString(11)))}, " +
                $"scope={(latestReader.IsDBNull(12) ? "(none)" : latestReader.GetString(12))}, " +
                $"build={(latestReader.IsDBNull(13) ? "(none)" : latestReader.GetString(13))}, " +
                $"consistent={latestReader.GetBoolean(14)}, " +
                $"issue={(latestReader.IsDBNull(15) ? "(none)" : latestReader.GetString(15))}");
        }
    }

    private static async Task InspectScopeSchemaAsync(string connectionString)
    {
        static string QuoteIdentifier(string value) => $"\"{value.Replace("\"", "\"\"")}\"";
        string[] tables =
        {
            "TB_ROUTE_PATH", "TB_ROUTE_FEATURE_VECTOR", "TB_ROUTE_CONTEXT_VECTOR",
            "TB_BIM_OBSTACLE", "TB_ROUTE_SEGMENTS", "TB_ROUTE_SEGMENT_DETAIL", "TB_SPACE_GROUP_INFO"
        };
        string[] markers = { "PROJECT", "MODEL", "REVISION", "VERSION", "TEMPLATE", "BAY", "PROCESS", "GROUP", "EQUIPMENT", "SOURCE_FILE" };
        await using var conn = new NpgsqlConnection(connectionString);
        await conn.OpenAsync();
        foreach (string table in tables)
        {
            var columns = new List<(string Name, string Type)>();
            await using (var cmd = new NpgsqlCommand(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=@table
                ORDER BY ordinal_position
                """, conn))
            {
                cmd.Parameters.AddWithValue("table", table);
                await using var reader = await cmd.ExecuteReaderAsync();
                while (await reader.ReadAsync()) columns.Add((reader.GetString(0), reader.GetString(1)));
            }
            Console.WriteLine($"TABLE {table}: columns={columns.Count}");
            foreach (var column in columns.Where(column => markers.Any(marker =>
                         column.Name.Contains(marker, StringComparison.OrdinalIgnoreCase))))
            {
                string quotedTable = QuoteIdentifier(table);
                string quotedColumn = QuoteIdentifier(column.Name);
                string sql = $"""
                    SELECT COUNT(*) FILTER (WHERE {quotedColumn} IS NOT NULL),
                           COUNT(DISTINCT {quotedColumn}),
                           string_agg(sample_value, ', ' ORDER BY sample_value)
                    FROM (
                        SELECT {quotedColumn}, {quotedColumn}::text AS sample_value
                        FROM {quotedTable}
                        WHERE {quotedColumn} IS NOT NULL
                        GROUP BY {quotedColumn}
                        ORDER BY {quotedColumn}::text
                        LIMIT 5
                    ) sample
                    """;
                try
                {
                    await using var valueCmd = new NpgsqlCommand(sql, conn);
                    await using var valueReader = await valueCmd.ExecuteReaderAsync();
                    await valueReader.ReadAsync();
                    Console.WriteLine(
                        $"  {column.Name} ({column.Type}): sampled_nonnull={valueReader.GetInt64(0)}, " +
                        $"sampled_distinct={valueReader.GetInt64(1)}, " +
                        $"values={(valueReader.IsDBNull(2) ? "(none)" : valueReader.GetString(2))}");
                }
                catch (Exception ex)
                {
                    Console.WriteLine($"  {column.Name} ({column.Type}): inspect failed: {ex.Message}");
                }
            }
        }

        string[] diagnostics =
        {
            """
            SELECT 'feature_to_space_group' AS name,
                   COUNT(*)::text AS total,
                   COUNT(*) FILTER (WHERE sg."TAG_GROUP_ID" IS NOT NULL)::text AS matched,
                   COUNT(*) FILTER (WHERE sg."TAG_GROUP_ID" IS NOT NULL
                                      AND UPPER(TRIM(COALESCE(fv."PROCESS_NAME",''))) =
                                          UPPER(TRIM(COALESCE(sg."PROCESS_GROUP_NM",''))))::text AS process_matched
            FROM "TB_ROUTE_FEATURE_VECTOR" fv
            LEFT JOIN "TB_SPACE_GROUP_INFO" sg
              ON UPPER(TRIM(COALESCE(fv."EQUIPMENT_NAME",''))) = UPPER(TRIM(COALESCE(sg."TAG_GROUP_NM",'')))
            """,
            """
            WITH scope_bays AS (
              SELECT DISTINCT UPPER(TRIM(sg."BAY_GROUP_NM")) AS bay
              FROM "TB_SPACE_GROUP_INFO" sg WHERE sg."BAY_GROUP_NM" IS NOT NULL
              UNION
              SELECT DISTINCT UPPER(TRIM(value))
              FROM "TB_SPACE_GROUP_INFO" sg
              CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(sg."EQUIPMENT_BAY_LIST", '[]')::jsonb) value
              WHERE value IS NOT NULL AND TRIM(value) <> ''
            )
            SELECT 'obstacle_bay_coverage' AS name,
                   COUNT(*) FILTER (WHERE o."DDWORKS_TYPE" IN
                     ('COLUMN_ARCHITECTURE','COLUMN_STRUCTURE','BEAM_ARCHITECTURE','BEAM_STRUCTURE'))::text AS total,
                   COUNT(*) FILTER (WHERE o."DDWORKS_TYPE" IN
                     ('COLUMN_ARCHITECTURE','COLUMN_STRUCTURE','BEAM_ARCHITECTURE','BEAM_STRUCTURE')
                     AND UPPER(TRIM(COALESCE(o."BAY",''))) IN (SELECT bay FROM scope_bays))::text AS matched,
                   COUNT(*) FILTER (WHERE o."DDWORKS_TYPE" IN
                     ('COLUMN_ARCHITECTURE','COLUMN_STRUCTURE','BEAM_ARCHITECTURE','BEAM_STRUCTURE')
                     AND (o."BAY" IS NULL OR TRIM(o."BAY")=''))::text AS process_matched
            FROM "TB_BIM_OBSTACLE" o
            """,
            """
            WITH matches AS (
              SELECT fv."ROUTE_PATH_GUID", COUNT(DISTINCT sg."TAG_GROUP_ID") AS match_count
              FROM "TB_ROUTE_FEATURE_VECTOR" fv
              LEFT JOIN "TB_SPACE_GROUP_INFO" sg
                ON UPPER(TRIM(COALESCE(fv."PROCESS_NAME",''))) = UPPER(TRIM(COALESCE(sg."PROCESS_GROUP_NM",'')))
               AND fv."START_POSX" BETWEEN sg."AABB_MINX" - 500 AND sg."AABB_MAXX" + 500
               AND fv."START_POSY" BETWEEN sg."AABB_MINY" - 500 AND sg."AABB_MAXY" + 500
               AND EXISTS (
                 SELECT 1 FROM jsonb_array_elements_text(COALESCE(sg."EQUIPMENT_TAG_LIST", '[]')::jsonb) tag
                 WHERE UPPER(TRIM(tag)) = UPPER(TRIM(COALESCE(fv."EQUIPMENT_NAME",'')))
               )
              GROUP BY fv."ROUTE_PATH_GUID"
            )
            SELECT 'feature_spatial_tag_scope' AS name,
                   COUNT(*)::text AS total,
                   COUNT(*) FILTER (WHERE match_count = 1)::text AS matched,
                   COUNT(*) FILTER (WHERE match_count > 1)::text AS process_matched
            FROM matches
            """,
            """
            SELECT 'feature_route_process_bay' AS name,
                   COUNT(*)::text AS total,
                   COUNT(*) FILTER (WHERE rp."ROUTE_PATH_GUID" IS NOT NULL
                                      AND rp."BAY" IS NOT NULL AND TRIM(rp."BAY") <> '')::text AS matched,
                   COUNT(DISTINCT UPPER(TRIM(COALESCE(rp."PROCESS_NAME",''))) || '|' ||
                                         UPPER(TRIM(COALESCE(rp."BAY",''))))::text AS process_matched
            FROM "TB_ROUTE_FEATURE_VECTOR" fv
            LEFT JOIN "TB_ROUTE_PATH" rp ON rp."ROUTE_PATH_GUID" = fv."ROUTE_PATH_GUID"
            """,
        };
        foreach (string sql in diagnostics)
        {
            await using var cmd = new NpgsqlCommand(sql, conn);
            await using var reader = await cmd.ExecuteReaderAsync();
            await reader.ReadAsync();
            Console.WriteLine(
                $"DIAGNOSTIC {reader.GetString(0)}: total={reader.GetString(1)}, " +
                $"matched={reader.GetString(2)}, third={reader.GetString(3)}");
        }
    }

    private static List<ContextRoutingAbRecord> RunArm(
        AutoRouteFinder.Models.DbConfig db, SceneData scene, ProjectInfo project,
        IReadOnlyList<TaskInfo> tasks, bool useContext, Guid runId, ArmInputs inputs,
        int exclusiveCorridorCells, Options options)
    {
        // 동일 Task 순서로 한 arm을 실행하고 성공/실패와 품질·탐색비용 지표를 record로 만든다.
        string arm = useContext ? "CONTEXT_V3" : "BASELINE_TOPK";
        var (engine, releasedEquipment) = BuildEngine(
            scene, tasks, inputs, options.CorridorPolicy, ParseRankFactors(options.RankPenaltyFactors),
            options.CorridorCostFactor, options.ReleaseOwnerEquipment);
        int endpointReleaseCount = releasedEquipment.Count;
        using (engine)
        {
        Console.WriteLine(
            $"[{arm}] Routing tasks={tasks.Count}, corridor_cells={inputs.Corridors.Count:N0}, " +
            $"exclusive={exclusiveCorridorCells:N0}, released_equipment={endpointReleaseCount}");
        engine.RouteMulti("longest");

        var rows = new List<ContextRoutingAbRecord>(tasks.Count);
        for (int i = 0; i < tasks.Count; i++)
        {
            var task = tasks[i];
            var result = engine.GetResult(i);
            inputs.Traces.TryGetValue(i, out var trace);
            int? collisionCount = result.Success
                ? CountStaticCollisionCells(result.Path, scene, releasedEquipment)
                : null;
            rows.Add(new ContextRoutingAbRecord(
                Guid.NewGuid(), runId, options.ExperimentId,
                ContextRoutingAbLogger.BuildRequestKey(task, project.GroupId, options.ModelRevisionKey),
                arm, true, project.GroupId, EmptyAsNull(options.ModelRevisionKey),
                task.RoutePathGuid, task.EquipmentTag, task.Group, task.Utility,
                task.Sx, task.Sy, task.Sz, task.Gx, task.Gy, task.Gz, task.DiameterMm,
                trace, options.CorridorPolicy,
                options.CorridorPolicy == "ranked" ? options.RankPenaltyFactors : null,
                options.CorridorCostFactor, inputs.Corridors.Count,
                exclusiveCorridorCells, endpointReleaseCount,
                result.Success, result.Success ? null : result.Fail.ToString(),
                result.LengthMm, result.Turns, collisionCount, result.ExpandedNodes, result.ElapsedMs));
            Console.WriteLine(
                $"[{arm}] #{i + 1} success={result.Success}, length={result.LengthMm:F0}, " +
                $"bends={result.Turns}, elapsed={result.ElapsedMs:F1}ms, fail={result.Fail}");
        }
        return rows;
        }
    }

    private static (Routing3DEngine Engine, HashSet<EquipmentBox> ReleasedEquipment) BuildEngine(
        SceneData scene, IReadOnlyList<TaskInfo> tasks, ArmInputs inputs, string corridorPolicy,
        IReadOnlyList<double> rankPenaltyFactors,
        double corridorCostFactor, bool releaseOwnerEquipment)
    {
        // 두 arm에 동일한 voxel grid와 장애물을 구성한다. endpoint owner 장비만 정책에 따라 해제한다.
        var g = scene.Grid;
        var engine = new Routing3DEngine();
        engine.SetGrid(g.CellMm, g.Ox, g.Oy, g.Oz, g.Nx, g.Ny, g.Nz);
        foreach (var obstacle in scene.Obstacles)
        {
            if (obstacle.IsPassThrough)
                engine.AddPassthrough(obstacle.MinX, obstacle.MinY, obstacle.MinZ, obstacle.MaxX, obstacle.MaxY, obstacle.MaxZ);
            else
                engine.AddObstacle(obstacle.MinX, obstacle.MinY, obstacle.MinZ, obstacle.MaxX, obstacle.MaxY, obstacle.MaxZ);
        }
        var released = releaseOwnerEquipment
            ? scene.Equipment.Where(equipment => IsOwnerEquipmentAtEndpoint(equipment, tasks)).ToHashSet()
            : new HashSet<EquipmentBox>();
        foreach (var equipment in scene.Equipment.Where(equipment => !released.Contains(equipment)))
            engine.AddObstacle(equipment.MinX, equipment.MinY, equipment.MinZ, equipment.MaxX, equipment.MaxY, equipment.MaxZ);
        for (int i = 0; i < tasks.Count; i++)
        {
            var task = tasks[i];
            int index = engine.AddTask(task.Sx, task.Sy, task.Sz, task.Gx, task.Gy, task.Gz, task.Utility, task.Group);
            if (index != i) throw new InvalidOperationException($"Unexpected engine task index {index}; expected {i}.");
            if (task.DiameterMm > 0) engine.SetTaskDiameter(index, task.DiameterMm);
        }
        engine.SetParameters(new RoutingParameters
        {
            CellMm = g.CellMm,
            TurnCostMm = 500.0,
            ClearanceCostMm = 10.0,
            CorridorCostMm = inputs.Corridors.Count > 0 ? g.CellMm * corridorCostFactor : 0.0,
            HeuristicWeight = 2.0,
            NearGoalHeuristicWeight = 1.0,
            ClearanceRadiusCells = 2,
            ClearanceConnectivity = 6,
            CorridorRadiusCells = 2,
            RackLevels = new List<int>(),
        });
        if (inputs.Corridors.Count > 0)
        {
            if (corridorPolicy == "ranked")
                engine.SetRankedCorridorCells(
                    inputs.BestRanks.Select(pair => new RankedPathCell(pair.Key, pair.Value)),
                    rankPenaltyFactors);
            else
                engine.SetCorridorCells(inputs.Corridors);
        }
        return (engine, released);
    }

    private static async Task<ArmInputs> BuildArmInputsAsync(
        AutoRouteFinder.Models.DbConfig db, GridMeta grid, ProjectInfo project,
        IReadOnlyList<TaskInfo> tasks, bool useContext, int k, string corridorPolicy,
        bool strictScope, string projectScopeKey, string modelRevisionKey)
    {
        // 각 Task의 Top-K를 검색하고 후보 기존경로를 voxelize하여 corridor cell/rank를 만든다.
        var traces = new Dictionary<int, ContextSearchTrace>();
        var bestRanks = new Dictionary<PathCell, int>();
        var searchDb = new RoutingAI.Standalone.DbConfig(db.Host, db.Port, db.Database, db.User, db.Password);
        for (int i = 0; i < tasks.Count; i++)
        {
            var task = tasks[i];
            var (results, meta) = await TopKSearchStandalone.SearchAsync(
                searchDb, project.Process ?? "", task.EquipmentTag ?? "", task.Group ?? "", task.Utility ?? "",
                (task.Sx, task.Sy, task.Sz), (task.Gx, task.Gy, task.Gz), k,
                useObstacleContext: useContext, bay: project.Bay ?? "",
                projectScopeKey: strictScope ? projectScopeKey : "",
                modelRevisionKey: strictScope ? modelRevisionKey : "",
                allowGlobalContextFallback: !strictScope);
            traces[i] = new ContextSearchTrace(
                results.Select(r => r.RoutePathGuid).ToArray(), meta.SearchTimeMs, meta.ContextCoverage,
                meta.ContextFallbackCandidates, meta.RerankWeightProfile,
                EmptyAsNull(meta.ContextSnapshotHash), EmptyAsNull(meta.ContextScopeStatus),
                EmptyAsNull(meta.ContextBuildRunId), EmptyAsNull(meta.ContextProjectScopeKey),
                EmptyAsNull(meta.ContextModelRevisionKey), EmptyAsNull(meta.ContextEncoderVersion),
                EmptyAsNull(meta.ContextEncoderConfigHash), meta.ContextProvenanceConsistent,
                EmptyAsNull(meta.ContextProvenanceIssue));
            int rankLimit = corridorPolicy switch
                { "rank1" => 1, "top2" => 2, "union" => k, "ranked" => k, _ => 0 };
            for (int rank = 0; rank < Math.Min(results.Count, rankLimit); rank++)
                foreach (var cell in VoxelizePath(LoadPathPoints(db.ConnectionString, results[rank].RoutePathGuid), grid, 2))
                    if (!bestRanks.TryGetValue(cell, out var oldRank) || rank + 1 < oldRank)
                        bestRanks[cell] = rank + 1;
            Console.WriteLine(
                $"[{(useContext ? "CONTEXT_V3" : "BASELINE_TOPK")}] search #{i + 1}: " +
                $"topk={results.Count}, coverage={meta.ContextCoverage:P1}, fallback={meta.ContextFallbackCandidates}, " +
                $"scope={EmptyAs(meta.ContextScopeStatus, "(none)")}, snapshot={ShortHash(meta.ContextSnapshotHash)}");
        }
        return new ArmInputs(traces, bestRanks.Keys.ToList(), bestRanks);
    }

    private static void ValidateContextGate(ArmInputs contextInputs, Options options)
    {
        // 최소 coverage, strict scope, manifest/encoder/provenance 일관성을 routing 전에 차단한다.
        var traces = contextInputs.Traces.Values.ToList();
        foreach (var trace in traces)
        {
            if (trace.ContextCoverage + 1e-12 < options.MinContextCoverage)
                throw new InvalidOperationException(
                    $"Context coverage gate failed: {trace.ContextCoverage:P1} < {options.MinContextCoverage:P1}.");
            if (trace.ContextCoverage > 0 && !trace.ContextProvenanceConsistent)
                throw new InvalidOperationException(
                    $"Context provenance gate failed: {trace.ContextProvenanceIssue ?? "unknown inconsistency"}.");
        }
        var populated = traces.Where(trace => trace.ContextCoverage > 0).ToList();
        var manifests = populated.Select(trace => string.Join("|", new[]
        {
            trace.ContextSnapshotHash ?? "", trace.ContextScopeStatus ?? "", trace.ContextBuildRunId ?? "",
            trace.ContextProjectScopeKey ?? "", trace.ContextModelRevisionKey ?? "",
            trace.ContextEncoderVersion ?? "", trace.ContextEncoderConfigHash ?? "",
        })).Distinct(StringComparer.Ordinal).ToList();
        if (manifests.Count > 1)
            throw new InvalidOperationException($"Context provenance gate failed: {manifests.Count} manifests in one run.");
        if (options.RequireStrictContextScope)
        {
            if (string.IsNullOrWhiteSpace(options.ModelRevisionKey))
                throw new InvalidOperationException(
                    "--require-strict-context-scope requires --model-revision-key.");
            if (populated.Count != traces.Count || traces.Any(trace => trace.ContextCoverage < 1.0 - 1e-12))
                throw new InvalidOperationException("Strict context scope requires 100% context coverage.");
            foreach (var trace in traces)
            {
                if (!string.Equals(trace.ContextScopeStatus, "STRICT_COMMON_KEY", StringComparison.Ordinal) ||
                    !string.Equals(trace.ContextProjectScopeKey, options.ProjectScopeKey, StringComparison.Ordinal) ||
                    !string.Equals(trace.ContextModelRevisionKey, options.ModelRevisionKey, StringComparison.Ordinal))
                    throw new InvalidOperationException(
                        "Strict context scope gate failed: saved vector scope does not match the requested project/revision.");
            }
        }
        Console.WriteLine(
            $"Context gate PASS: manifests={manifests.Count}, min_coverage=" +
            $"{(traces.Count == 0 ? 0 : traces.Min(trace => trace.ContextCoverage)):P1}, " +
            $"strict={options.RequireStrictContextScope}");
    }

    private static ArmInputs CopyContextProvenance(ArmInputs baseline, ArmInputs context)
    {
        // A/B 로그 비교 시 baseline에도 동일 Context build provenance를 복사하되 검색점수는 변경하지 않는다.
        foreach (var (index, source) in context.Traces)
        {
            if (!baseline.Traces.TryGetValue(index, out var target)) continue;
            baseline.Traces[index] = target with
            {
                ContextSnapshotHash = source.ContextSnapshotHash,
                ContextScopeStatus = source.ContextScopeStatus,
                ContextBuildRunId = source.ContextBuildRunId,
                ContextProjectScopeKey = source.ContextProjectScopeKey,
                ContextModelRevisionKey = source.ContextModelRevisionKey,
                ContextEncoderVersion = source.ContextEncoderVersion,
                ContextEncoderConfigHash = source.ContextEncoderConfigHash,
                ContextProvenanceConsistent = source.ContextProvenanceConsistent,
                ContextProvenanceIssue = source.ContextProvenanceIssue,
            };
        }
        return baseline;
    }

    private static string ShortHash(string value) =>
        string.IsNullOrWhiteSpace(value) ? "(none)" : value[..Math.Min(12, value.Length)];

    private static List<Pt3> LoadPathPoints(string connectionString, string routeGuid)
    {
        var points = new List<Pt3>();
        using var conn = new NpgsqlConnection(connectionString);
        conn.Open();
        using var cmd = new NpgsqlCommand(
            """
            SELECT sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",
                   sd."TO_POSX", sd."TO_POSY", sd."TO_POSZ"
            FROM "TB_ROUTE_SEGMENT_DETAIL" sd
            JOIN "TB_ROUTE_SEGMENTS" s ON s."SEGMENT_GUID" = sd."SEGMENT_GUID"
            WHERE s."ROUTE_PATH_GUID" = @guid
            ORDER BY s."ORDER", sd."ORDER"
            """, conn);
        cmd.Parameters.AddWithValue("guid", routeGuid);
        using var reader = cmd.ExecuteReader();
        while (reader.Read())
        {
            if (reader.IsDBNull(0) || reader.IsDBNull(1) || reader.IsDBNull(2) ||
                reader.IsDBNull(3) || reader.IsDBNull(4) || reader.IsDBNull(5)) continue;
            var from = new Pt3(reader.GetDouble(0), reader.GetDouble(1), reader.GetDouble(2));
            var to = new Pt3(reader.GetDouble(3), reader.GetDouble(4), reader.GetDouble(5));
            AddDistinct(points, from); AddDistinct(points, to);
        }
        return points;
    }

    private static void AddDistinct(List<Pt3> points, Pt3 point)
    {
        if (points.Count == 0) { points.Add(point); return; }
        var last = points[^1];
        double dx = last.X - point.X, dy = last.Y - point.Y, dz = last.Z - point.Z;
        if (dx * dx + dy * dy + dz * dz > 1.0) points.Add(point);
    }

    private static bool IsOwnerEquipmentAtEndpoint(EquipmentBox equipment, IReadOnlyList<TaskInfo> tasks)
    {
        static bool SameOwner(string? owner, string equipmentName) =>
            !string.IsNullOrWhiteSpace(owner) &&
            string.Equals(owner.Trim(), equipmentName.Trim(), StringComparison.OrdinalIgnoreCase);
        static bool Contains(EquipmentBox box, double x, double y, double z) =>
            x >= box.MinX && x <= box.MaxX && y >= box.MinY && y <= box.MaxY &&
            z >= box.MinZ && z <= box.MaxZ;

        return tasks.Any(task =>
            (Contains(equipment, task.Sx, task.Sy, task.Sz) &&
             (SameOwner(task.PocName, equipment.Name) || SameOwner(task.EquipmentTag, equipment.Name))) ||
            (Contains(equipment, task.Gx, task.Gy, task.Gz) && SameOwner(task.EndName, equipment.Name)));
    }

    private static int CountStaticCollisionCells(
        IReadOnlyList<PathCell> path, SceneData scene, IReadOnlySet<EquipmentBox> releasedEquipment)
    {
        var grid = scene.Grid;
        static bool Inside(double x, double y, double z,
            double minX, double minY, double minZ, double maxX, double maxY, double maxZ) =>
            x >= minX && x <= maxX && y >= minY && y <= maxY && z >= minZ && z <= maxZ;

        int count = 0;
        foreach (var cell in path.Distinct())
        {
            double x = grid.Ox + (cell.I + 0.5) * grid.CellMm;
            double y = grid.Oy + (cell.J + 0.5) * grid.CellMm;
            double z = grid.Oz + (cell.K + 0.5) * grid.CellMm;
            bool blocked = scene.Obstacles.Any(o => !o.IsPassThrough &&
                Inside(x, y, z, o.MinX, o.MinY, o.MinZ, o.MaxX, o.MaxY, o.MaxZ));
            if (!blocked)
                blocked = scene.Equipment.Any(e => !releasedEquipment.Contains(e) &&
                    Inside(x, y, z, e.MinX, e.MinY, e.MinZ, e.MaxX, e.MaxY, e.MaxZ));
            if (blocked) count++;
        }
        return count;
    }

    private static string EmptyAs(string value, string fallback) =>
        string.IsNullOrWhiteSpace(value) ? fallback : value.Trim();

    private static string? EmptyAsNull(string value) =>
        string.IsNullOrWhiteSpace(value) ? null : value.Trim();

    private static double[] ParseRankFactors(string value) => value
        .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
        .Select(item => double.Parse(item, System.Globalization.CultureInfo.InvariantCulture))
        .ToArray();

    private static IEnumerable<PathCell> VoxelizePath(IReadOnlyList<Pt3> points, GridMeta grid, int dilate)
    {
        // 기존 설계 polyline을 grid cell로 변환하고 dilate 반경만큼 넓혀 재사용 corridor를 만든다.
        if (grid.CellMm <= 0 || points.Count < 2) yield break;
        var cells = new HashSet<PathCell>();
        for (int p = 1; p < points.Count; p++)
        {
            var a = points[p - 1]; var b = points[p];
            double dx = b.X - a.X, dy = b.Y - a.Y, dz = b.Z - a.Z;
            double length = Math.Sqrt(dx * dx + dy * dy + dz * dz);
            int steps = Math.Max(1, (int)(length / (grid.CellMm * 0.5)));
            for (int s = 0; s <= steps; s++)
            {
                double t = (double)s / steps;
                int ci = (int)Math.Floor((a.X + dx * t - grid.Ox) / grid.CellMm);
                int cj = (int)Math.Floor((a.Y + dy * t - grid.Oy) / grid.CellMm);
                int ck = (int)Math.Floor((a.Z + dz * t - grid.Oz) / grid.CellMm);
                for (int di = -dilate; di <= dilate; di++)
                    for (int dj = -dilate; dj <= dilate; dj++)
                        for (int dk = -dilate; dk <= dilate; dk++)
                        {
                            int i = ci + di, j = cj + dj, k = ck + dk;
                            if (i >= 0 && j >= 0 && k >= 0 && i < grid.Nx && j < grid.Ny && k < grid.Nz)
                                cells.Add(new PathCell(i, j, k));
                        }
            }
        }
        foreach (var cell in cells) yield return cell;
    }

    private static void PrintPairSummary(
        IReadOnlyList<ContextRoutingAbRecord> baseline, IReadOnlyList<ContextRoutingAbRecord> context)
    {
        int bOk = baseline.Count(row => row.RouteSuccess), cOk = context.Count(row => row.RouteSuccess);
        Console.WriteLine($"PAIR SUMMARY: baseline={bOk}/{baseline.Count}, context={cOk}/{context.Count}, delta={cOk - bOk:+#;-#;0}");
    }

    private static AutoRouteFinder.Models.DbConfig LoadDbConfig(string path)
    {
        using var doc = JsonDocument.Parse(File.ReadAllText(path));
        var db = doc.RootElement.GetProperty("db");
        return new AutoRouteFinder.Models.DbConfig
        {
            Host = db.TryGetProperty("host", out var host) ? host.GetString() ?? "localhost" : "localhost",
            Port = db.TryGetProperty("port", out var port) ? port.GetInt32() : 5432,
            Database = db.TryGetProperty("database", out var database) ? database.GetString() ?? "DDW_AI_DB" : "DDW_AI_DB",
            User = db.TryGetProperty("user", out var user) ? user.GetString() ?? "postgres" : "postgres",
            Password = db.TryGetProperty("password", out var password) ? password.GetString() ?? "" : "",
        };
    }

    private static Options Parse(string[] args)
    {
        // CLI 문자열을 강타입 Options로 변환하고 상호의존 인자를 마지막에 검증한다.
        var options = new Options();
        for (int i = 0; i < args.Length; i++)
        {
            string Next() => ++i < args.Length ? args[i] : throw new ArgumentException($"{args[i - 1]} requires a value");
            switch (args[i])
            {
                case "--config": options.Config = Next(); break;
                case "--list-projects": options.ListProjects = true; break;
                case "--create-schema": options.CreateSchema = true; break;
                case "--status": options.Status = true; break;
                case "--inspect-scope-schema": options.InspectScopeSchema = true; break;
                case "--create-scope-schema": options.CreateScopeSchema = true; break;
                case "--project-id": options.ProjectId = int.Parse(Next()); break;
                case "--task-limit": options.TaskLimit = int.Parse(Next()); break;
                case "--cell-mm": options.CellMm = double.Parse(Next(), System.Globalization.CultureInfo.InvariantCulture); break;
                case "--k": options.K = int.Parse(Next()); break;
                case "--max-grid-cells": options.MaxGridCells = long.Parse(Next()); break;
                case "--route-guids": options.RouteGuids = Next(); break;
                case "--context-first": options.ContextFirst = true; break;
                case "--experiment-id": options.ExperimentId = Next(); break;
                case "--corridor-policy": options.CorridorPolicy = Next().Trim().ToLowerInvariant(); break;
                case "--corridor-cost-factor": options.CorridorCostFactor = double.Parse(
                    Next(), System.Globalization.CultureInfo.InvariantCulture); break;
                case "--rank-penalty-factors": options.RankPenaltyFactors = Next(); break;
                case "--model-revision-key": options.ModelRevisionKey = Next(); break;
                case "--project-scope-key": options.ProjectScopeKey = Next(); break;
                case "--require-strict-context-scope": options.RequireStrictContextScope = true; break;
                case "--allow-global-fallback": options.RequireStrictContextScope = false; break;
                case "--min-context-coverage": options.MinContextCoverage = double.Parse(
                    Next(), System.Globalization.CultureInfo.InvariantCulture); break;
                case "--keep-owner-equipment": options.ReleaseOwnerEquipment = false; break;
                case "--execute": options.Execute = true; break;
                case "--no-save": options.Save = false; break;
                case "--help": PrintHelp(); Environment.Exit(0); break;
                default: throw new ArgumentException($"Unknown option: {args[i]}");
            }
        }
        if (options.TaskLimit < 1 || options.K < 1 || options.CellMm <= 0 || options.MaxGridCells < 1 ||
            options.CorridorCostFactor < 0 || options.MinContextCoverage < 0 || options.MinContextCoverage > 1 ||
            string.IsNullOrWhiteSpace(options.ExperimentId))
            throw new ArgumentException(
                "Require task-limit>=1, k>=1, cell-mm>0, max-grid-cells>=1, corridor-cost-factor>=0, " +
                "min-context-coverage in [0,1], and experiment-id.");
        if (options.CorridorPolicy is not ("ranked" or "rank1" or "top2" or "union"))
            throw new ArgumentException("--corridor-policy must be ranked, rank1, top2, or union.");
        if (options.RequireStrictContextScope &&
            string.IsNullOrWhiteSpace(options.ProjectScopeKey) != string.IsNullOrWhiteSpace(options.ModelRevisionKey))
            throw new ArgumentException(
                "Specify both --project-scope-key and --model-revision-key, or neither to use ACTIVE scope.");
        var rankFactors = ParseRankFactors(options.RankPenaltyFactors);
        if (rankFactors.Length < options.K || rankFactors.Any(value => value < 0 || value > 1) ||
            rankFactors.Zip(rankFactors.Skip(1)).Any(pair => pair.First > pair.Second))
            throw new ArgumentException(
                "--rank-penalty-factors requires at least k ascending values in [0,1].");
        return options;
    }

    private static void PrintHelp() => Console.WriteLine("""
        ContextRoutingABRunner
          --create-schema [--status] [--config Tools/tools.settings.json]
          --status [--config Tools/tools.settings.json]
          --inspect-scope-schema [--config Tools/tools.settings.json]
          --create-scope-schema [--config Tools/tools.settings.json]
          --list-projects [--config Tools/tools.settings.json]
          --project-id N [--task-limit 1] [--cell-mm 50] [--k 3]
                         [--route-guids guid1,guid2] [--context-first]
                         [--experiment-id ID] [--corridor-policy ranked|rank1|top2|union]
                         [--corridor-cost-factor 0.5]
                         [--project-scope-key KEY] [--model-revision-key KEY]
                         [--min-context-coverage 0.0] [--require-strict-context-scope]
                         [--allow-global-fallback]
                         [--rank-penalty-factors 0,0.5,0.75]
                         [--keep-owner-equipment]
                         [--max-grid-cells 250000000] [--execute] [--no-save]

        Default is read-only dry-run using the ACTIVE strict scope.
        --allow-global-fallback explicitly enables legacy global context.
        --execute runs BASELINE_TOPK and CONTEXT_V3 sequentially.
        """);
}
