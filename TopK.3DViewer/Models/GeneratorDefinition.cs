using RoutingAI.Standalone;

namespace TopK.ThreeDViewer.Models;

/// <summary>Top-K 특징점/패턴 생성기 하나의 정의. Docs/BendFeaturePoint_Development_Plan.md,
/// Docs/UtilityPipeGroup_TopK_Development_Plan.md 등에서 설계한 Tools/*.py CLI를 그대로 감싼다.</summary>
public sealed record GeneratorDefinition(
    string Key,
    string DisplayName,
    string ScriptFile,
    string TargetTable,
    bool HasSeparateCreateSchema,
    Func<DbConfig, string, string, string> BuildArgs,
    Func<DbConfig, string>? BuildCreateSchemaArgs = null,
    string? DependencyNote = null,
    bool RequiresActiveScope = false,
    // Docs/FeaturePattern_Pipeline_Overlap_Review.md 2.4절: 이 테이블을 Tools/ 밖에서 읽는
    // 코드가 없다(make-stub도 CLI 밖에서 호출된 적 없음) — "전체 순서대로 실행"에서는 건너뛰고
    // 필요할 때만 수동으로 생성하도록 격하한다.
    bool IsOptional = false);

public static class GeneratorCatalog
{
    private static string Q(string value) =>
        "\"" + (value ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";

    /// <summary>tool_config.add_common_args 관례(--host/--port/--dbname/--user/--password)를 따르는 스크립트용.</summary>
    public static string ToolConfigConnectionArgs(DbConfig db) =>
        $"--host {Q(db.Host)} --port {db.Port} --dbname {Q(db.Database)} --user {Q(db.User)} --password {Q(db.Password)}";

    /// <summary>Extract_Design_Pattern.py 전용(--db, tool_config 미사용).</summary>
    private static string LegacyConnectionArgs(DbConfig db) =>
        $"--host {Q(db.Host)} --port {db.Port} --db {Q(db.Database)} --user {Q(db.User)} --password {Q(db.Password)}";

    /// <summary>7개 생성기를 의존 순서 그대로 담는다 — 다이얼로그 표시 순서, "전체 순서대로 실행" 순서와 동일하다.</summary>
    public static IReadOnlyList<GeneratorDefinition> All { get; } = new[]
    {
        new GeneratorDefinition(
            Key: "feature_vector",
            DisplayName: "30D 특징벡터 (Feature Vector)",
            ScriptFile: "Extract_Design_Pattern.py",
            TargetTable: "TB_ROUTE_FEATURE_VECTOR",
            // Extract_Design_Pattern.py는 서브커맨드 자체가 없고(flat parser), 매 실행마다
            // CREATE TABLE IF NOT EXISTS를 스스로 수행한 뒤 데이터를 입력한다 — 별도로 분리
            // 호출할 create-schema 커맨드가 없으므로 이 항목만 예외적으로 false.
            HasSeparateCreateSchema: false,
            BuildArgs: (db, _, _) => $"{LegacyConnectionArgs(db)} --project all --report false"),

        new GeneratorDefinition(
            Key: "context_vector",
            DisplayName: "Context Vector",
            ScriptFile: "ExtractObstacleContextVector.py",
            TargetTable: "TB_ROUTE_CONTEXT_VECTOR",
            HasSeparateCreateSchema: true,
            // ExtractObstacleContextVector.py는 tool_config.add_common_args()를 최상위 parser에
            // 호출한 뒤 add_subparsers()를 등록한다(스크립트 내부 구조). argparse 서브파서는 상위
            // parser의 옵션을 상속하지 않으므로, --host 등 연결 인자는 서브커맨드(run-all/
            // create-schema) *앞에* 와야 한다 — 뒤에 두면 "unrecognized arguments"로 실패한다.
            BuildArgs: (db, project, revision) =>
                $"{ToolConfigConnectionArgs(db)} run-all --project-scope-key {Q(project)} --model-revision-key {Q(revision)}",
            BuildCreateSchemaArgs: db => $"{ToolConfigConnectionArgs(db)} create-schema",
            DependencyNote: "30D 특징벡터가 먼저 생성되어 있어야 함"),

        new GeneratorDefinition(
            Key: "path_segmentation",
            DisplayName: "Path Segmentation",
            ScriptFile: "PathSegmenter.py",
            TargetTable: "TB_ROUTE_PATH_SEGMENTATION",
            HasSeparateCreateSchema: true,
            // PathSegmenter.py는 add_subparsers() 등록 후 서브파서마다 개별적으로
            // add_common_args()를 호출하므로, 연결 인자는 서브커맨드 *뒤에* 와야 한다.
            BuildArgs: (db, _, _) => $"run-all {ToolConfigConnectionArgs(db)}",
            BuildCreateSchemaArgs: db => $"create-schema {ToolConfigConnectionArgs(db)}"),

        new GeneratorDefinition(
            Key: "group_pattern",
            DisplayName: "Group/Bundle Pattern (다발배관)",
            ScriptFile: "ExportGroupPattern.py",
            TargetTable: "TB_ROUTE_GROUP_PATTERN",
            HasSeparateCreateSchema: true,
            // ExportGroupPattern.py도 ExtractObstacleContextVector.py와 동일하게 연결 인자가
            // 최상위 parser 소속이라 서브커맨드(run-all/create-schema) *앞에* 와야 한다.
            BuildArgs: (db, _, _) => $"{ToolConfigConnectionArgs(db)} run-all",
            BuildCreateSchemaArgs: db => $"{ToolConfigConnectionArgs(db)} create-schema",
            DependencyNote: "Path Segmentation이 먼저 생성되어 있어야 함"),

        new GeneratorDefinition(
            Key: "stub_pattern",
            DisplayName: "Stub Pattern",
            ScriptFile: "ExtractStubPatterns.py",
            TargetTable: "TB_ROUTE_STUB_PATTERN",
            HasSeparateCreateSchema: true,
            // ExtractStubPatterns.py는 PathSegmenter.py와 동일하게 서브파서별로
            // add_common_args()를 호출하므로 연결 인자는 서브커맨드 뒤에 온다.
            BuildArgs: (db, _, _) => $"run-all {ToolConfigConnectionArgs(db)} --min-samples 3",
            BuildCreateSchemaArgs: db => $"create-schema {ToolConfigConnectionArgs(db)}",
            // TopKSearchStandalone/StubPatternSearch.cs + TopK.3DViewer "Stub 패턴" 검색 모드가
            // 이 테이블의 FEAT/DIR_UNIT을 실제로 조회한다(make-stub CLI 자체는 여전히 미연결).
            DependencyNote: "Stub 패턴 유사도 검색(TopK.3DViewer \"Stub 패턴\" 모드)이 이 테이블을 사용함"),

        new GeneratorDefinition(
            Key: "utility_pipe_group_vector",
            DisplayName: "Utility Pipe Group Vector",
            ScriptFile: "BuildUtilityPipeGroupVectors.py",
            TargetTable: "TB_ROUTE_UTILITY_GROUP_VECTOR",
            HasSeparateCreateSchema: true,
            // BuildUtilityPipeGroupVectors.py는 add_subparsers가 아니라 위치 인자(action)를
            // 쓰고 연결 인자를 최상위 parser 한 곳에서만 등록하므로 순서에 영향받지 않는다.
            BuildArgs: (db, project, revision) =>
                $"build {ToolConfigConnectionArgs(db)} --scope-mode explicit " +
                $"--project-scope-key {Q(project)} --model-revision-key {Q(revision)} --min-members 2",
            BuildCreateSchemaArgs: db => $"create-schema {ToolConfigConnectionArgs(db)}",
            DependencyNote: "30D 특징벡터 + Context Vector가 먼저 생성되어 있어야 함",
            RequiresActiveScope: true),

        new GeneratorDefinition(
            Key: "bend_feature_point",
            DisplayName: "Bend Feature Point",
            ScriptFile: "ExtractBendFeaturePoints.py",
            TargetTable: "TB_ROUTE_BEND_FEATURE_POINT",
            HasSeparateCreateSchema: true,
            // ExtractBendFeaturePoints.py도 서브파서별 add_common_args() 방식이라
            // 연결 인자는 서브커맨드 뒤에 온다.
            BuildArgs: (db, project, revision) =>
                $"build {ToolConfigConnectionArgs(db)} --scope-mode explicit " +
                $"--project-scope-key {Q(project)} --model-revision-key {Q(revision)} --min-samples 3",
            BuildCreateSchemaArgs: db => $"create-schema {ToolConfigConnectionArgs(db)}",
            DependencyNote: "Group/Bundle Pattern이 있으면 GROUP_ALIGNMENT 원인 판정에 활용(없어도 동작)",
            RequiresActiveScope: true),
    };
}
