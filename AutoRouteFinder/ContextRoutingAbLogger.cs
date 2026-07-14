using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Npgsql;

namespace AutoRouteFinder;

public sealed record ContextSearchTrace(
    IReadOnlyList<string> TopKRouteGuids,
    double SearchTimeMs,
    double ContextCoverage,
    int ContextFallbackCount,
    string RerankWeightProfile,
    string? ContextSnapshotHash = null,
    string? ContextScopeStatus = null,
    string? ContextBuildRunId = null,
    string? ContextProjectScopeKey = null,
    string? ContextModelRevisionKey = null,
    string? ContextEncoderVersion = null,
    string? ContextEncoderConfigHash = null,
    bool ContextProvenanceConsistent = true,
    string? ContextProvenanceIssue = null);

public sealed record ContextRoutingAbRecord(
    Guid LogId, Guid RunId, string ExperimentId, string RequestKey, string Arm, bool IsShadow,
    string? ProjectKey, string? ModelRevisionKey,
    string? RoutePathGuid, string? EquipmentName, string? UtilityGroup, string? Utility,
    double SourceX, double SourceY, double SourceZ, double TargetX, double TargetY, double TargetZ,
    double DiameterMm, ContextSearchTrace? Search,
    string CorridorPolicy, string? CorridorRankProfile, double CorridorCostFactor, int CorridorCellCount,
    int CorridorExclusiveCellCount, int EndpointReleaseCount,
    bool RouteSuccess, string? RouteFailReason, double RouteLengthMm, int RouteBendCount,
    int? CollisionCount, long ExpandedNodes, double RouteElapsedMs);

public static class ContextRoutingAbLogger
{
    public const string ExperimentId = "context-v3-weight-010";

    public static string BuildRequestKey(
        Models.TaskInfo task, string? projectKey = null, string? modelRevisionKey = null)
    {
        static string F(double value) => value.ToString("R", System.Globalization.CultureInfo.InvariantCulture);
        string canonical = string.Join("|", new[]
        {
            (projectKey ?? "").Trim().ToUpperInvariant(),
            (modelRevisionKey ?? "").Trim().ToUpperInvariant(),
            (task.EquipmentTag ?? "").Trim().ToUpperInvariant(),
            (task.Group ?? "").Trim().ToUpperInvariant(),
            (task.Utility ?? "").Trim().ToUpperInvariant(),
            F(task.Sx), F(task.Sy), F(task.Sz), F(task.Gx), F(task.Gy), F(task.Gz), F(task.DiameterMm),
        });
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(canonical))).ToLowerInvariant();
    }

    public static async Task SaveBatchAsync(string connectionString, IReadOnlyCollection<ContextRoutingAbRecord> rows)
    {
        if (rows.Count == 0) return;
        await using var conn = new NpgsqlConnection(connectionString);
        await conn.OpenAsync().ConfigureAwait(false);
        await using var tx = await conn.BeginTransactionAsync().ConfigureAwait(false);
        const string sql = """
            INSERT INTO "TB_CONTEXT_ROUTING_AB_LOG"
            ("LOG_ID", "RUN_ID", "EXPERIMENT_ID", "REQUEST_KEY", "ARM", "IS_SHADOW",
             "PROJECT_KEY", "MODEL_REVISION_KEY",
             "ROUTE_PATH_GUID", "EQUIPMENT_NAME", "UTILITY_GROUP", "UTILITY",
             "SOURCE_X", "SOURCE_Y", "SOURCE_Z", "TARGET_X", "TARGET_Y", "TARGET_Z", "DIAMETER_MM",
             "TOPK_ROUTE_GUIDS", "TOPK_SEARCH_MS", "CONTEXT_COVERAGE", "CONTEXT_FALLBACK_COUNT", "RERANK_WEIGHT_PROFILE",
             "CONTEXT_SNAPSHOT_HASH", "CONTEXT_SCOPE_STATUS", "CONTEXT_BUILD_RUN_ID",
             "CONTEXT_PROJECT_SCOPE_KEY", "CONTEXT_MODEL_REVISION_KEY",
             "CONTEXT_ENCODER_VERSION", "CONTEXT_ENCODER_CONFIG_HASH",
             "CONTEXT_PROVENANCE_CONSISTENT", "CONTEXT_PROVENANCE_ISSUE",
             "CORRIDOR_POLICY", "CORRIDOR_RANK_PROFILE", "CORRIDOR_COST_FACTOR", "CORRIDOR_CELL_COUNT",
             "CORRIDOR_EXCLUSIVE_CELL_COUNT", "ENDPOINT_RELEASE_COUNT",
             "ROUTE_SUCCESS", "ROUTE_FAIL_REASON", "ROUTE_LENGTH_MM", "ROUTE_BEND_COUNT", "COLLISION_COUNT",
             "EXPANDED_NODES", "ROUTE_ELAPSED_MS")
            VALUES
            (@log_id, @run_id, @experiment_id, @request_key, @arm, @is_shadow,
             @project_key, @model_revision_key,
             @route_path_guid, @equipment_name, @utility_group, @utility,
             @sx, @sy, @sz, @tx, @ty, @tz, @diameter,
             @topk::jsonb, @search_ms, @coverage, @fallback, @profile,
             @context_snapshot_hash, @context_scope_status, @context_build_run_id,
             @context_project_scope_key, @context_model_revision_key,
             @context_encoder_version, @context_encoder_config_hash,
             @context_provenance_consistent, @context_provenance_issue,
             @corridor_policy, @corridor_rank_profile, @corridor_cost_factor, @corridor_cell_count,
             @corridor_exclusive_cell_count, @endpoint_release_count,
             @success, @fail_reason, @length, @bends, @collisions, @expanded, @elapsed)
            """;
        foreach (var row in rows)
        {
            await using var cmd = new NpgsqlCommand(sql, conn, tx);
            cmd.Parameters.AddWithValue("log_id", row.LogId); cmd.Parameters.AddWithValue("run_id", row.RunId);
            cmd.Parameters.AddWithValue("experiment_id", row.ExperimentId); cmd.Parameters.AddWithValue("request_key", row.RequestKey);
            cmd.Parameters.AddWithValue("arm", row.Arm); cmd.Parameters.AddWithValue("is_shadow", row.IsShadow);
            cmd.Parameters.AddWithValue("project_key", (object?)row.ProjectKey ?? DBNull.Value);
            cmd.Parameters.AddWithValue("model_revision_key", (object?)row.ModelRevisionKey ?? DBNull.Value);
            cmd.Parameters.AddWithValue("route_path_guid", (object?)row.RoutePathGuid ?? DBNull.Value);
            cmd.Parameters.AddWithValue("equipment_name", (object?)row.EquipmentName ?? DBNull.Value);
            cmd.Parameters.AddWithValue("utility_group", (object?)row.UtilityGroup ?? DBNull.Value);
            cmd.Parameters.AddWithValue("utility", (object?)row.Utility ?? DBNull.Value);
            cmd.Parameters.AddWithValue("sx", row.SourceX); cmd.Parameters.AddWithValue("sy", row.SourceY); cmd.Parameters.AddWithValue("sz", row.SourceZ);
            cmd.Parameters.AddWithValue("tx", row.TargetX); cmd.Parameters.AddWithValue("ty", row.TargetY); cmd.Parameters.AddWithValue("tz", row.TargetZ);
            cmd.Parameters.AddWithValue("diameter", row.DiameterMm);
            cmd.Parameters.AddWithValue("topk", JsonSerializer.Serialize(row.Search?.TopKRouteGuids ?? Array.Empty<string>()));
            cmd.Parameters.AddWithValue("search_ms", (object?)row.Search?.SearchTimeMs ?? DBNull.Value);
            cmd.Parameters.AddWithValue("coverage", (object?)row.Search?.ContextCoverage ?? DBNull.Value);
            cmd.Parameters.AddWithValue("fallback", (object?)row.Search?.ContextFallbackCount ?? DBNull.Value);
            cmd.Parameters.AddWithValue("profile", (object?)row.Search?.RerankWeightProfile ?? DBNull.Value);
            cmd.Parameters.AddWithValue("context_snapshot_hash", (object?)row.Search?.ContextSnapshotHash ?? DBNull.Value);
            cmd.Parameters.AddWithValue("context_scope_status", (object?)row.Search?.ContextScopeStatus ?? DBNull.Value);
            cmd.Parameters.AddWithValue("context_build_run_id", (object?)row.Search?.ContextBuildRunId ?? DBNull.Value);
            cmd.Parameters.AddWithValue("context_project_scope_key", (object?)row.Search?.ContextProjectScopeKey ?? DBNull.Value);
            cmd.Parameters.AddWithValue("context_model_revision_key", (object?)row.Search?.ContextModelRevisionKey ?? DBNull.Value);
            cmd.Parameters.AddWithValue("context_encoder_version", (object?)row.Search?.ContextEncoderVersion ?? DBNull.Value);
            cmd.Parameters.AddWithValue("context_encoder_config_hash", (object?)row.Search?.ContextEncoderConfigHash ?? DBNull.Value);
            cmd.Parameters.AddWithValue("context_provenance_consistent", row.Search?.ContextProvenanceConsistent ?? true);
            cmd.Parameters.AddWithValue("context_provenance_issue", (object?)row.Search?.ContextProvenanceIssue ?? DBNull.Value);
            cmd.Parameters.AddWithValue("corridor_policy", row.CorridorPolicy);
            cmd.Parameters.AddWithValue("corridor_rank_profile", (object?)row.CorridorRankProfile ?? DBNull.Value);
            cmd.Parameters.AddWithValue("corridor_cost_factor", row.CorridorCostFactor);
            cmd.Parameters.AddWithValue("corridor_cell_count", row.CorridorCellCount);
            cmd.Parameters.AddWithValue("corridor_exclusive_cell_count", row.CorridorExclusiveCellCount);
            cmd.Parameters.AddWithValue("endpoint_release_count", row.EndpointReleaseCount);
            cmd.Parameters.AddWithValue("success", row.RouteSuccess);
            cmd.Parameters.AddWithValue("fail_reason", (object?)row.RouteFailReason ?? DBNull.Value);
            cmd.Parameters.AddWithValue("length", row.RouteLengthMm); cmd.Parameters.AddWithValue("bends", row.RouteBendCount);
            cmd.Parameters.AddWithValue("collisions", (object?)row.CollisionCount ?? DBNull.Value);
            cmd.Parameters.AddWithValue("expanded", row.ExpandedNodes); cmd.Parameters.AddWithValue("elapsed", row.RouteElapsedMs);
            await cmd.ExecuteNonQueryAsync().ConfigureAwait(false);
        }
        await tx.CommitAsync().ConfigureAwait(false);
    }
}
