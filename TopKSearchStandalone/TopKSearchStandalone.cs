// Context Vector 기능 실행 방법(PowerShell)
// ----------------------------------------
// 1) 빌드: dotnet build TopKSearchStandalone/TopKSearchStandalone.csproj -c Release
// 2) 도움말: dotnet run --project TopKSearchStandalone/TopKSearchStandalone.csproj -- --help
// 3) Context 검색: 일반 검색 인자에 --use-obstacle-context 추가
// 4) API 호출: SearchAsync(..., useObstacleContext: true)
//
// 전체 흐름: 입력 -> endpoint 30D query -> pgvector Feature 후보 -> 기존 hybrid 재정렬
//            -> ACTIVE strict scope -> query/candidate Context cosine -> 최종 Top-K
// scope 생략 시 ACTIVE를 자동 선택하며, global fallback은 명시적으로 허용한 경우만 사용한다.
//
// ═════════════════════════════════════════════════════════════════════════════
//  TopKSearchStandalone.cs
// ─────────────────────────────────────────────────────────────────────────────
//  ● 단일 파일 Top-K 경로 유사도 검색기 (PostgreSQL + pgvector)
//  ● 다른 개발자가 본 파일 하나 + Npgsql NuGet 만으로 Top-K 검색 기능을
//    구현할 수 있도록 자체 완결형(self-contained)으로 작성됨.
//
//  [공개 API]
//    SearchAsync(...)              → (List<SearchResult>, SearchMeta) — Top-K 검색 (본 파일 핵심)
//    FetchPresetsAsync(...)        → List<RoutePreset>                — TB_ROUTE_PATH 프리셋 나열
//    FetchPresetByGuidAsync(...)   → RoutePreset?                     — 단일 프리셋 조회
//    CheckSchemaAsync(db)          → SchemaCheckReport                — 스키마 무결성 점검(read-only)
//
// ─────────────────────────────────────────────────────────────────────────────
//  [입력] SearchAsync 파라미터 (섹션 2.2, 라인 ~390)
// ─────────────────────────────────────────────────────────────────────────────
//    ┌──────────────────┬─────────────────────────────────┬────────────────────┐
//    │ 파라미터          │ 타입                            │ 필수/기본 / 비고   │
//    ├──────────────────┼─────────────────────────────────┼────────────────────┤
//    │ db               │ DbConfig (record)               │ 필수 — Host/Port/  │
//    │                  │                                 │ Database/User/Pass │
//    │                  │                                 │ (기본 localhost:   │
//    │                  │                                 │ 5432/AUTOROUTINGV7)│
//    │ processName      │ string                          │ 필수 — ""이면      │
//    │                  │                                 │   해당 WHERE 생략  │
//    │                  │                                 │   예) "CMP"        │
//    │ equipmentName    │ string                          │ 필수 — ""이면 생략 │
//    │                  │                                 │   예) "kscta01"    │
//    │ utilityGroup     │ string                          │ 필수 — ""이면 생략 │
//    │                  │                                 │   예) "UPW","Gas", │
//    │                  │                                 │   "Chemical",      │
//    │                  │                                 │   "Vacuum"         │
//    │ utility          │ string                          │ 필수 — ""이면 생략 │
//    │                  │                                 │   예) "UPW_S",     │
//    │                  │                                 │   "PN2", "Ar"      │
//    │ startXyz         │ (double X, double Y, double Z)  │ 필수 — mm 단위     │
//    │                  │   ValueTuple                    │   예) (12000,8500, │
//    │                  │                                 │          3200)     │
//    │ endXyz           │ (double X, double Y, double Z)  │ 필수 — mm 단위     │
//    │ k                │ int                             │ 기본 5 — k<1 이면  │
//    │                  │                                 │ ArgumentOutOf-     │
//    │                  │                                 │ RangeException     │
//    │ size             │ string (선택)                   │ 기본 "" — 주면     │
//    │                  │                                 │ WHERE SIZE=@s 추가.│
//    │                  │                                 │ 과도 필터 시 0건   │
//    │                  │                                 │ 주의 (예: "20A")   │
//    │ queryPattern     │ string (선택)                   │ 기본 "" — "H-R-H"  │
//    │                  │                                 │ 형태 주면 struct   │
//    │                  │                                 │ 점수 활성          │
//    └──────────────────┴─────────────────────────────────┴────────────────────┘
//
//    ※ 프리셋 입력 대안: FetchPresetsAsync / FetchPresetByGuidAsync 로 얻은
//       RoutePreset 의 ProcessName/EquipmentName/UtilityGroup/Utility/Size/
//       StartXyz/EndXyz 를 그대로 SearchAsync 에 전달. (섹션 2.2.1)
//
// ─────────────────────────────────────────────────────────────────────────────
//  [출력] SearchAsync 반환 = (List<SearchResult> Results, SearchMeta Meta)
// ─────────────────────────────────────────────────────────────────────────────
//    SearchResult — Top-K 단일 항목 (Combined 내림차순, Rank=1..k)
//    ┌────────────────────┬──────────────────────────────┬───────────────────┐
//    │ 필드                │ 타입                         │ 의미              │
//    ├────────────────────┼──────────────────────────────┼───────────────────┤
//    │ Rank                │ int                          │ 1..K (최종 순위)  │
//    │ RoutePathGuid       │ string                       │ 원본 경로 GUID    │
//    │                     │                              │ (TB_ROUTE_PATH    │
//    │                     │                              │  조인 키)         │
//    │ ProcessName         │ string                       │ 공정명            │
//    │ EquipmentName       │ string                       │ 장비명            │
//    │ UtilityGroup        │ string                       │ 유틸리티 그룹     │
//    │ Utility             │ string                       │ 유틸리티          │
//    │ Size                │ string                       │ 배관 구경         │
//    │                     │                              │ (예: "20A")       │
//    │ DirectionPattern    │ string                       │ 방향 코드 시퀀스  │
//    │                     │                              │ (예: "H-R-H-D")   │
//    │ TotalLengthMm       │ double                       │ 총 경로 길이(mm)  │
//    │ StepCount           │ int                          │ 세그먼트 수       │
//    │ StartXyz            │ (double X,Y,Z)               │ 후보 경로 시작    │
//    │                     │                              │ 좌표 (mm)         │
//    │ EndXyz              │ (double X,Y,Z)               │ 후보 경로 종료    │
//    │                     │                              │ 좌표 (mm)         │
//    │ CosineDistance      │ double                       │ pgvector <=> 원값 │
//    │                     │                              │ (0=동일, 2=정반대)│
//    │ ScorePosition       │ double (0~1)                 │ 상대위치 점수     │
//    │                     │                              │ (가중치 0.50)     │
//    │ ScorePattern        │ double (0~1)                 │ 패턴 점수         │
//    │                     │                              │ (가중치 0.30)     │
//    │ ScoreVector         │ double (0~1)                 │ 30D 코사인 점수   │
//    │                     │                              │ (가중치 0.10)     │
//    │ SimilarityScore     │ double (0~1)                 │ Combined =        │
//    │                     │                              │   0.50·Pos +      │
//    │                     │                              │   0.30·Pat +      │
//    │                     │                              │   0.18·Vec        │
//    └────────────────────┴──────────────────────────────┴───────────────────┘
//
//    SearchMeta — 실행 진단/로깅 정보
//    ┌────────────────────┬──────────────────────────────┬───────────────────┐
//    │ 필드                │ 타입                         │ 의미              │
//    ├────────────────────┼──────────────────────────────┼───────────────────┤
//    │ SearchTimeMs        │ double                       │ 전체 소요 시간    │
//    │                     │                              │ (ms, stopwatch)   │
//    │ TotalCandidates     │ int                          │ pgvector 반환     │
//    │                     │                              │ 실제 후보 수      │
//    │ FetchN              │ int                          │ LIMIT 값 =        │
//    │                     │                              │ max(k×30, 150)    │
//    │ FiltersApplied      │ Dictionary<string,string>    │ 적용된 WHERE      │
//    │                     │                              │ (process_name /   │
//    │                     │                              │  equipment_name / │
//    │                     │                              │  utility_group /  │
//    │                     │                              │  utility / size)  │
//    │ QueryVectorHead     │ double[6]                    │ 쿼리 벡터 앞 6개  │
//    │                     │                              │ (타 구현체 1:1    │
//    │                     │                              │  대조 검증용)     │
//    └────────────────────┴──────────────────────────────┴───────────────────┘
//
//    CLI --json 출력 형태 (Program.Main 이 위 record 를 snake_case 직렬화):
//    {
//      "meta":    { "search_time_ms":28.3, "total_candidates":57, "fetch_n":150,
//                   "filters_applied":{...}, "query_vector_head":[...] },
//      "results": [ { "rank":1, "route_path_guid":"b3c9...", "similarity_score":0.89,
//                     "score_position":0.95, "score_pattern":0.00, "score_vector":0.89,
//                     "cosine_distance":0.11, ... }, ... ]
//    }
//
//  [예외]
//    ArgumentOutOfRangeException : k < 1
//    Npgsql.NpgsqlException      : DB 접속/쿼리 실패 (pgvector 미설치, 테이블 누락 등)
//                                  → 사전에 CheckSchemaAsync 로 게이트 권장
//
//  [전체 처리 흐름]
//
//    ┌────────────────────────────────────────────────────────────────────┐
//    │ 사용자 입력 (공정/장비/유틸/start/end/k)                            │
//    └────────────────────────┬───────────────────────────────────────────┘
//                             │
//                             ▼
//    ┌────────────────────────────────────────────────────────────────────┐
//    │ [1] BuildQueryVector30D()                                           │
//    │     start/end 좌표만으로 30D 쿼리 벡터 생성                         │
//    │                                                                     │
//    │     [0:3]  Start 토폴로지   = (dx,dy,dz)/|d|                        │
//    │     [3:6]  End   토폴로지   = -(dx,dy,dz)/|d|                       │
//    │     [6:9]  공간 변위        = (dx,dy,dz)/displacement_max           │
//    │     [9:12] 바운딩박스       = (|dx|,|dy|,|dz|)/bbox_max             │
//    │     [12:21] 3구간 꺾임       = 0 (경로 미지)                        │
//    │     [21]    총 길이         = |d|/total_length_max                  │
//    │     [22:25] 꺾임수/장애물… = 0                                     │
//    │     [25:30] Arrow 통계      = 0                                     │
//    │     * 각 그룹에 WEIGHT_MAP 기반 스케일 팩터 적용 후 L2 정규화       │
//    └────────────────────────┬───────────────────────────────────────────┘
//                             │
//                             ▼
//    ┌────────────────────────────────────────────────────────────────────┐
//    │ [2] FetchCandidatesAsync()                                          │
//    │     PostgreSQL + pgvector Phase 1                                   │
//    │                                                                     │
//    │     WHERE PROCESS_NAME=@pn AND EQUIPMENT_NAME=@eq                   │
//    │           AND UTILITY_GROUP=@ug AND UTILITY=@ut                     │
//    │     ORDER BY "FEATURE_VECTOR" <=> @vec::vector                      │
//    │     LIMIT N = max(k × 30, 150)                                      │
//    │                                                                     │
//    │     pgvector <=> = 코사인 거리 (0=동일)                             │
//    └────────────────────────┬───────────────────────────────────────────┘
//                             │
//                             ▼
//    ┌────────────────────────────────────────────────────────────────────┐
//    │ [3] RerankHybrid()                                                  │
//    │     Phase 2 — 3~4가지 유사도 가중합                                  │
//    │                                                                     │
//    │     posScore  = max(0, 1 − ‖Δq−Δc‖ / 50000)                         │
//    │     patScore  = 0.5·structScore + 0.5·bendScore                     │
//    │                  (struct = Levenshtein(RLE 축약), bend = [12:21] cos)│
//    │     vecScore  = 1 − cosineDistance                                   │
//    │     ctxScore  = (선택, useObstacleContext=true 시) 장애물 컨텍스트   │
//    │                  30D 코사인 유사도 (§2.8 BuildContextVector30Async) │
//    │                                                                     │
//    │  useObstacleContext=false(기본): combined = 0.50·pos+0.30·pat+0.20·vec│
//    │  useObstacleContext=true       : combined = 0.45·pos+0.27·pat        │
//    │                                              +0.18·vec+0.10·ctx      │
//    │     → 내림차순 정렬 → 상위 K 선정                                   │
//    └────────────────────────────────────────────────────────────────────┘
//
//    ※ ctxScore는 재정렬 단계에서만 사용된다. [2]의 1차 pgvector ANN 후보추출에는
//       절대 섞지 않는다 — 실측상 후보추출 단계에 섞으면 오히려 정확도가 떨어짐
//       (기존 FEATURE_VECTOR의 env_cost 구간과 정보 중복, "천장효과").
//       상세 근거: Docs/20260713_Learned Design Data Reuse Strategy.md
//
//  [사용 예시]
//
//   (A) CLI:
//       dotnet run --project TopKSearchStandalone.csproj -- \
//           --host localhost --port 5432 --dbname AUTOROUTINGV7 \
//           --user postgres --password dinno \
//           --process CMP --equipment kscta01 \
//           --utility-group UPW --utility UPW_S \
//           --start 12000,8500,3200 --end 14500,10200,3200 \
//           --k 5
//
//   (B) 라이브러리:
//       var db = new DbConfig("localhost", 5432, "AUTOROUTINGV7", "postgres", "dinno");
//       var (results, meta) = await TopKSearchStandalone.SearchAsync(
//           db,
//           processName:   "CMP",
//           equipmentName: "kscta01",
//           utilityGroup:  "UPW",
//           utility:       "UPW_S",
//           startXyz:      (12000, 8500, 3200),
//           endXyz:        (14500, 10200, 3200),
//           k: 5);
//       foreach (var r in results)
//           Console.WriteLine($"#{r.Rank}  score={r.SimilarityScore:F3}  {r.EquipmentName}/{r.Utility}");
//
//   (C) TB_ROUTE_PATH 기본 프리셋 사용 (실존 경로 1개를 골라 Top-K 입력으로 재사용):
//
//       CLI 프리셋 나열:
//         dotnet run -- --list-presets --process CMP --equipment kscta01
//
//       CLI 프리셋으로 검색:
//         dotnet run -- --preset-guid <ROUTE_PATH_GUID> --k 5
//
//       라이브러리:
//         var presets = await TopKSearchStandalone.FetchPresetsAsync(
//             db, processName: "CMP", equipmentName: "kscta01", limit: 20);
//         var preset  = await TopKSearchStandalone.FetchPresetByGuidAsync(db, presets[0].RoutePathGuid);
//         var (results, meta) = await TopKSearchStandalone.SearchAsync(
//             db, preset!.ProcessName, preset.EquipmentName,
//             preset.UtilityGroup, preset.Utility,
//             preset.StartXyz, preset.EndXyz, k: 5, size: preset.Size);
//
//  [의존성]
//    .NET 8+  /  Npgsql 8+  /  PostgreSQL 14+ + pgvector extension
//
//  [필수 DB 스키마]
//    TB_ROUTE_FEATURE_VECTOR(
//        ROUTE_PATH_GUID, PROCESS_NAME, EQUIPMENT_NAME, UTILITY_GROUP, UTILITY,
//        SIZE, START_POSX/Y/Z, END_POSX/Y/Z, DIRECTION_PATTERN,
//        TOTAL_LENGTH_MM, STEP_COUNT,
//        FEATURE_VECTOR vector(30)          -- pgvector 컬럼 + HNSW 인덱스
//    )
//    TB_ROUTE_PATH(                         -- 기본 프리셋 출처
//        ROUTE_PATH_GUID, PROCESS_NAME, EQUIPMENT_NAME (=EQUIPMENT_NAME),
//        UTILITY_GROUP, SOURCE_UTILITY (=UTILITY), SOURCE_SIZE,
//        SOURCE_POSX/Y/Z, TARGET_POSX/Y/Z,
//        TARGET_OWNER_NAME, TOTAL_LENGTH, BEND_COUNT
//    )
// ═════════════════════════════════════════════════════════════════════════════

// -----------------------------------------------------------------------------
// Context Vector 운영 실행 요약(한글)
// 실행: dotnet build TopKSearchStandalone/TopKSearchStandalone.csproj -c Release
// CLI 도움말: dotnet run --project TopKSearchStandalone/TopKSearchStandalone.csproj -- --help
// 라이브러리: SearchAsync(..., useObstacleContext: true)
//
// 흐름: 입력 -> endpoint 30D query -> pgvector Feature 후보 -> 위치/pattern/feature 재정렬
//       -> ACTIVE strict scope -> query/candidate Context cosine -> hybrid Top-K
// 계약: scope 인자를 생략하면 ACTIVE를 자동 선택하고, ACTIVE 0/복수건은 실패한다.
//       legacy fallback은 allowGlobalContextFallback=true인 진단 호출에서만 허용한다.
//       SearchMeta는 coverage/scope/snapshot/encoder provenance를 반환한다.
// -----------------------------------------------------------------------------
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Npgsql;

namespace RoutingAI.Standalone;

// ═════════════════════════════════════════════════════════════════════════════
// 섹션 1 — 공개 데이터 구조 (DbConfig / SearchResult / SearchMeta)
// ═════════════════════════════════════════════════════════════════════════════

/// <summary>PostgreSQL 접속 정보. 본 스크립트의 유일한 외부 설정.</summary>
public sealed record DbConfig(
    string Host     = "localhost",
    int    Port     = 5432,
    string Database = "AUTOROUTINGV7",
    string User     = "postgres",
    string Password = "dinno")
{
    /// <summary>Npgsql ConnectionString으로 변환.</summary>
    public string ToConnectionString() =>
        $"Host={Host};Port={Port};Database={Database};Username={User};Password={Password};Encoding=UTF8";
}

/// <summary>
/// 하이브리드 재정렬 가중치. 합계가 1 또는 100일 필요는 없으며 재정렬 시 활성 항목만
/// 자동 정규화한다. 모든 값은 0 이상의 유한수여야 한다.
/// </summary>
public sealed record RerankWeights(
    double Position = 0.45,
    double Pattern = 0.27,
    double Vector = 0.18,
    double Context = 0.10);

/// <summary>Top-K 결과 단일 건.</summary>
/// <param name="Rank">1..K 최종 순위 (Combined 내림차순).</param>
/// <param name="RoutePathGuid">원본 경로 GUID (TB_ROUTE_PATH 와 조인용).</param>
/// <param name="ProcessName">공정명.</param>
/// <param name="EquipmentName">장비명.</param>
/// <param name="UtilityGroup">유틸리티 그룹.</param>
/// <param name="Utility">유틸리티.</param>
/// <param name="Size">배관 구경 문자열(예: "20A").</param>
/// <param name="DirectionPattern">"H-R-H-D" 형태 방향 코드 시퀀스.</param>
/// <param name="TotalLengthMm">총 경로 길이(mm).</param>
/// <param name="StepCount">세그먼트 수.</param>
/// <param name="StartXyz">후보 경로의 시작 좌표(mm).</param>
/// <param name="EndXyz">후보 경로의 종료 좌표(mm).</param>
/// <param name="CosineDistance">pgvector &lt;=&gt; 연산자 원값(0=동일, 2=정반대).</param>
/// <param name="ScorePosition">상대위치 유사도 (0~1, 가중치 0.50 또는 컨텍스트 사용시 0.45).</param>
/// <param name="ScorePattern">패턴 유사도 (0~1, 가중치 0.30 또는 컨텍스트 사용시 0.27).</param>
/// <param name="ScoreVector">30D 코사인 유사도 (0~1, 가중치 0.20 또는 컨텍스트 사용시 0.18).</param>
/// <param name="SimilarityScore">최종 Combined 점수(0~1).</param>
/// <param name="ScoreContext">장애물 컨텍스트 유사도 (0~1, 가중치 0.10). useObstacleContext=false면 항상 0.</param>
public sealed record SearchResult(
    int    Rank,
    string RoutePathGuid,
    string ProcessName,
    string EquipmentName,
    string UtilityGroup,
    string Utility,
    string Size,
    string DirectionPattern,
    double TotalLengthMm,
    int    StepCount,
    (double X, double Y, double Z) StartXyz,
    (double X, double Y, double Z) EndXyz,
    double CosineDistance,
    double ScorePosition,
    double ScorePattern,
    double ScoreVector,
    double SimilarityScore,
    double ScoreContext = 0.0);

/// <summary>검색 실행 메타 (진단/로깅용).</summary>
public sealed class SearchMeta
{
    public double SearchTimeMs    { get; set; }
    public int    TotalCandidates { get; set; }
    public int    FetchN          { get; set; }
    /// <summary>재정렬 후보 중 호환되는 TB_ROUTE_CONTEXT_VECTOR가 존재하는 건수.</summary>
    public int    ContextCandidates { get; set; }
    /// <summary>재정렬 후보의 Context Vector 커버리지(0~1).</summary>
    public double ContextCoverage => TotalCandidates == 0
        ? 0.0
        : (double)ContextCandidates / TotalCandidates;
    /// <summary>Context 사용 요청은 있었지만 호환 벡터가 없어 baseline 점수를 사용한 후보 수.</summary>
    public int ContextFallbackCandidates => UsedObstacleContext
        ? Math.Max(0, TotalCandidates - ContextCandidates)
        : 0;
    /// <summary>검색에 적용한 재정렬 가중치 계약.</summary>
    public string RerankWeightProfile { get; set; } = "baseline:0.50/0.30/0.20";
    public Dictionary<string, string> FiltersApplied { get; set; } = new();
    /// <summary>쿼리 벡터 앞 6개 원소(다른 구현체와 대조 검증용).</summary>
    public double[] QueryVectorHead { get; set; } = Array.Empty<double>();
    /// <summary>쿼리 Context Vector 앞 6개 원소(Python 색인 벡터와 parity 진단용).</summary>
    public double[] QueryContextVectorHead { get; set; } = Array.Empty<double>();
    /// <summary>이번 검색이 장애물 컨텍스트 벡터(ctxScore)를 재정렬에 사용했는지 여부.</summary>
    public bool UsedObstacleContext { get; set; }
    public string ContextSnapshotHash { get; set; } = "";
    public string ContextScopeStatus { get; set; } = "";
    public string ContextBuildRunId { get; set; } = "";
    public string ContextProjectScopeKey { get; set; } = "";
    public string ContextModelRevisionKey { get; set; } = "";
    public string ContextEncoderVersion { get; set; } = "";
    public string ContextEncoderConfigHash { get; set; } = "";
    public bool ContextProvenanceConsistent { get; set; } = true;
    public string ContextProvenanceIssue { get; set; } = "";
    public int ContextProvenanceTupleCount { get; set; }
}

/// <summary>
/// 스키마 무결성 점검 리포트. <see cref="TopKSearchStandalone.CheckSchemaAsync"/> 가
/// 반환하며 CLI 또는 외부 호출자가 실패/경고 항목을 그대로 활용할 수 있다.
/// </summary>
public sealed class SchemaCheckReport
{
    /// <summary>pgvector 확장 설치 여부.</summary>
    public bool   PgvectorInstalled { get; set; }
    public string PgvectorVersion   { get; set; } = "";

    /// <summary>두 필수 테이블 존재 여부.</summary>
    public bool FvTableExists { get; set; }
    public bool RpTableExists { get; set; }

    /// <summary>코드가 기대하는 컬럼 개수 (요약 출력용).</summary>
    public int ExpectedFvColumnCount { get; set; }
    public int ExpectedRpColumnCount { get; set; }

    /// <summary>FetchCandidates / FetchPresets 가 SELECT 하는 컬럼 중 누락된 항목.</summary>
    public List<string> FvMissingColumns      { get; set; } = new();
    public List<string> RpMissingColumns      { get; set; } = new();
    public List<string> FvColumnTypeWarnings  { get; set; } = new();
    public List<string> RpColumnTypeWarnings  { get; set; } = new();

    /// <summary>두 테이블의 모든 인덱스 (Tablename, Indexname, Definition, IsHnsw).</summary>
    public List<(string Table, string Name, string Definition, bool IsHnsw)>
        Indexes { get; set; } = new();
    public bool HasHnswIndex { get; set; }

    public long FvRowCount { get; set; }
    public long RpRowCount { get; set; }

    /// <summary>FV 핵심 컬럼별 NULL/빈 카운트.</summary>
    public Dictionary<string, long> FvNullCounts { get; set; } = new();
    /// <summary>RP 핵심 컬럼별 NULL/빈 카운트.</summary>
    public Dictionary<string, long> RpNullCounts { get; set; } = new();

    /// <summary>FEATURE_VECTOR 의 차원 분포 (정상값=30).</summary>
    public Dictionary<int, long> VectorDimDistribution { get; set; } = new();

    /// <summary>End-to-end smoke 테스트 메시지.</summary>
    public List<string> EndToEndOk  { get; set; } = new();
    public List<string> EndToEndErr { get; set; } = new();

    /// <summary>차단성 이슈 (FAIL).</summary>
    public List<string> Failures { get; set; } = new();
    /// <summary>권고 이슈 (WARN).</summary>
    public List<string> Warnings { get; set; } = new();

    public bool IsHealthy => Failures.Count == 0;
}


/// <summary>
/// TB_ROUTE_PATH 한 행에서 추출한 "검색 기본 프리셋".
/// 사용자가 공정/장비/유틸리티/좌표를 일일이 타이핑하지 않고, 실존하는
/// 경로 하나를 골라 동일한 파라미터로 Top-K 검색을 실행할 수 있게 한다.
/// </summary>
/// <param name="RoutePathGuid">TB_ROUTE_PATH.ROUTE_PATH_GUID (프리셋 식별자).</param>
/// <param name="ProcessName">공정명 (PROCESS_NAME).</param>
/// <param name="EquipmentName">장비명 (EQUIPMENT_NAME → Feature Vector 측 EQUIPMENT_NAME 과 동일 개념).</param>
/// <param name="UtilityGroup">유틸리티 그룹 (UTILITY_GROUP).</param>
/// <param name="Utility">유틸리티 (SOURCE_UTILITY → Feature Vector 측 UTILITY).</param>
/// <param name="Size">배관 구경 (SOURCE_SIZE).</param>
/// <param name="StartXyz">시작 좌표 (SOURCE_POSX/Y/Z).</param>
/// <param name="EndXyz">종료 좌표 (TARGET_POSX/Y/Z).</param>
/// <param name="TargetOwnerName">End PoC 소속(참고용, TARGET_OWNER_NAME).</param>
/// <param name="TotalLengthMm">원본 경로 총 길이(mm, TOTAL_LENGTH).</param>
/// <param name="BendCount">원본 경로 꺾임 수(BEND_COUNT).</param>
public sealed record RoutePreset(
    string RoutePathGuid,
    string ProcessName,
    string EquipmentName,
    string UtilityGroup,
    string Utility,
    string Size,
    (double X, double Y, double Z) StartXyz,
    (double X, double Y, double Z) EndXyz,
    string TargetOwnerName,
    double TotalLengthMm,
    int    BendCount,
    string Bay = "");


// ═════════════════════════════════════════════════════════════════════════════
// 섹션 2 — 핵심 알고리즘 (TopKSearchStandalone static 클래스)
// ═════════════════════════════════════════════════════════════════════════════

/// <summary>
/// Top-K 유사 경로 검색 엔진. 단일 공개 API <see cref="SearchAsync"/> 를 통해
/// 벡터 인코딩 + pgvector 검색 + 하이브리드 재정렬을 일괄 수행한다.
/// </summary>
public static class TopKSearchStandalone
{
    // ─────────────────────────────────────────────────────────────────────────
    // 2.1 상수 — DB 벡터 생성 시 사용된 값과 반드시 일치해야 함
    // ─────────────────────────────────────────────────────────────────────────

    /// <summary>벡터 차원 수. TB_ROUTE_FEATURE_VECTOR.FEATURE_VECTOR vector(30).</summary>
    public const int VECTOR_DIM = 30;

    /// <summary>장애물 컨텍스트 벡터 차원 수. 시작 13D + 종료 13D + Tier3 4D.</summary>
    public const int CONTEXT_VECTOR_DIM = 30;
    public const int CONTEXT_ENDPOINT_DIM = 13;

    /// <summary>시작/종료 공통 근접 shell 반경(mm).</summary>
    public const double CTX_NEAR_RADIUS_MM = 500.0;
    /// <summary>시작/종료 공통 외곽 반경(mm). 두 번째 shell은 500~1000mm.</summary>
    public const double CTX_MID_RADIUS_MM = 1000.0;
    public const string CONTEXT_ENCODER_VERSION = "topkgen-v3";
    public const string CONTEXT_ENCODER_CONFIG_HASH = "bd5ff8de064cec2a2603dc514b18e9baa2e3d077b081a2d5477f91149e5a430b";

    /// <summary>30D 벡터의 차원 그룹별 (start, end, weight).
    /// 가중치는 원 설계 문서 기준이며 벡터 스케일링에만 사용(합=1 아님).</summary>
    private static readonly (string Name, int Start, int End, double Weight)[] WEIGHT_MAP =
    {
        ("start_topology", 0,  3,  0.20),
        ("end_topology",   3,  6,  0.20),
        ("displacement",   6,  9,  0.15),
        ("bounding_box",   9,  12, 0.15),
        ("segment_1",      12, 15, 0.06),
        ("segment_2",      15, 18, 0.06),
        ("segment_3",      18, 21, 0.06),
        ("env_cost",       21, 25, 0.12),
        ("arrow_pattern",  25, 30, 0.15),
    };

    // ─ 정규화 상수 ─ DB 빌드 시점(BuildFeatureVectors.py)과 동일해야 함 ────
    //  data/FeatureVectors/db_norm_params.json 에서 추출한 실측값.
    //  이 값이 DB와 불일치하면 쿼리 벡터가 다른 스케일 공간에 놓여
    //  검색 품질이 급격히 저하된다. (변경 시 재측정 필수)
    private const double BBOX_MAX_X          = 9759.011874999997;
    private const double BBOX_MAX_Y          = 11955.354296875;
    private const double BBOX_MAX_Z          = 11492.00024414066;
    private const double DISPLACEMENT_MAX    = 11900.982486974623;
    private const double TOTAL_LENGTH_MAX    = 66433.582;

    // ─ 재정렬 가중치 (Phase 2) ─ C# TopKSearchService 와 동일 ────────────────
    // useObstacleContext=false(기본값)일 때 사용 — 기존 호출자와 100% 동일한 결과 보장.
    /// <summary>상대위치 유사도 가중치 (최우선 순위).</summary>
    public const double RERANK_W_POSITION = 0.50;
    /// <summary>패턴 유사도 가중치.</summary>
    public const double RERANK_W_PATTERN  = 0.30;
    /// <summary>30D 벡터 유사도 가중치.</summary>
    public const double RERANK_W_VECTOR   = 0.20;
    /// <summary>패턴 유사도 내 구조(Levenshtein) 비중.</summary>
    public const double RERANK_W_STRUCT   = 0.50;
    /// <summary>패턴 유사도 내 꺾임(코사인) 비중.</summary>
    public const double RERANK_W_BEND     = 0.50;

    // ─ 재정렬 가중치 (컨텍스트 포함, useObstacleContext=true일 때만 사용) ──────
    // 기존 3항목(0.50/0.30/0.20)을 0.9배로 비례 축소하고 ctxScore에 0.10을 배정
    // (TopKGen v3 7,879건 전량 색인 leave-one-out 평가에서 context weight=0.10이
    //  운영 양끝축 Top-1 일치를 최대화한 결과를 따름 — Phase4 평가 문서 참조).
    // 1차 pgvector ANN 후보추출에는 절대 섞지 않고, 재정렬 단계에서만 사용한다
    // (섞으면 오히려 정확도가 떨어짐이 실측됨 — TB_ROUTE_CONTEXT_VECTOR 스키마 주석 참조).
    public const double RERANK_W_POSITION_CTX = 0.45;
    public const double RERANK_W_PATTERN_CTX  = 0.27;
    public const double RERANK_W_VECTOR_CTX   = 0.18;
    /// <summary>장애물 컨텍스트 유사도 가중치 (useObstacleContext=true일 때만 적용).</summary>
    public const double RERANK_W_CONTEXT      = 0.10;

    /// <summary>상대거리 정규화 한계(mm). relDist 가 이 이상이면 posScore=0.</summary>
    public const double REL_DIST_MAX_MM = 50000.0;

    /// <summary>Top-K 재정렬용 후보 수량 배수. fetchN = max(k×30, 150).</summary>
    public const int FETCH_MULTIPLIER = 30;
    public const int FETCH_MIN        = 150;

    /// <summary>각 차원 그룹에 sqrt(weight × 30 / dim_count) 스케일 적용.
    /// 타입 초기화 시 1회만 계산됨.</summary>
    private static readonly double[] ScaleFactors = BuildScaleFactors();

    private static double[] BuildScaleFactors()
    {
        var s = new double[VECTOR_DIM];
        for (int i = 0; i < VECTOR_DIM; i++) s[i] = 1.0;
        foreach (var (_, start, end, weight) in WEIGHT_MAP)
        {
            int dim = end - start;
            if (weight > 0 && dim > 0)
            {
                double factor = Math.Sqrt(weight * VECTOR_DIM / dim);
                for (int i = start; i < end; i++) s[i] = factor;
            }
        }
        return s;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 2.2 공개 진입점 — SearchAsync (End-to-End)
    // ─────────────────────────────────────────────────────────────────────────

    /// <summary>
    /// 공정/장비/유틸리티/start-end 좌표로 TB_ROUTE_FEATURE_VECTOR 에서 Top-K
    /// 유사 경로를 반환한다. 본 메서드 내부에서 DB 연결을 열고 닫는다.
    /// </summary>
    /// <param name="db">PostgreSQL 접속 정보.</param>
    /// <param name="processName">공정명 (빈 문자열이면 해당 WHERE 생략).</param>
    /// <param name="equipmentName">장비명.</param>
    /// <param name="utilityGroup">유틸리티 그룹.</param>
    /// <param name="utility">유틸리티.</param>
    /// <param name="startXyz">(x,y,z) 시작 좌표 mm.</param>
    /// <param name="endXyz">(x,y,z) 종료 좌표 mm.</param>
    /// <param name="k">반환할 Top-K 수량 (&gt;=1).</param>
    /// <param name="size">배관 구경 필터 (선택, 기본 공백).</param>
    /// <param name="queryPattern">방향 패턴 힌트 (선택, 예:"H-R-H"). 제공 시 struct 점수 활성.</param>
    /// <param name="useObstacleContext">true면 시작/종료 PoC 주변 장애물(기둥/보) 배치를 30D
    /// 컨텍스트 벡터로 즉석 계산해 재정렬 4번째 항목(ctxScore, 가중치 0.10)에 반영한다.
    /// TB_ROUTE_CONTEXT_VECTOR(Tools/ExtractObstacleContextVector.py 산출)가 없는 후보는
    /// 미색인 상태로 보고 기존 3항목 baseline 점수로 fallback한다. 기본값 false — 기존 호출자의 결과에 영향 없음.</param>
    /// <param name="bay">하위 호환용 진단 필드. v3 전역 공간 인코더에서는 장애물 필터에 사용하지 않는다.</param>
    /// <exception cref="ArgumentOutOfRangeException">k&lt;1 또는 좌표가 유효하지 않을 때.</exception>
    /// <exception cref="Npgsql.NpgsqlException">DB 접속/쿼리 실패.</exception>
    public static async Task<(List<SearchResult> Results, SearchMeta Meta)> SearchAsync(
        DbConfig db,
        string processName,
        string equipmentName,
        string utilityGroup,
        string utility,
        (double X, double Y, double Z) startXyz,
        (double X, double Y, double Z) endXyz,
        int k = 5,
        string size = "",
        string queryPattern = "",
        bool useObstacleContext = false,
        string bay = "",
        string projectScopeKey = "",
        string modelRevisionKey = "",
        bool allowGlobalContextFallback = false,
        RerankWeights? rerankWeights = null,
        bool redistributeMissingPatternWeight = false)
    {
        // 주요 입력 변수
        // - useObstacleContext: false=기존 feature 검색, true=Context hybrid 재정렬
        // - contextWeight: Context cosine의 최종점수 비중(나머지 점수는 비례 축소)
        // - projectScopeKey/modelRevisionKey: 고정할 source revision, 둘 다 비면 ACTIVE 자동 조회
        // - allowGlobalContextFallback: legacy 무범위 Context를 명시적으로 허용하는 비상 스위치
        if (k < 1) throw new ArgumentOutOfRangeException(nameof(k), "k must be >= 1");
        projectScopeKey = (projectScopeKey ?? "").Trim();
        modelRevisionKey = (modelRevisionKey ?? "").Trim();
        if (string.IsNullOrEmpty(projectScopeKey) != string.IsNullOrEmpty(modelRevisionKey))
            throw new ArgumentException("projectScopeKey and modelRevisionKey must be provided together");
        var sw = Stopwatch.StartNew();

        // [1] 쿼리 벡터 생성
        double[] queryVec = BuildQueryVector30D(startXyz, endXyz);

        // [2] DB 후보 수집
        int fetchN = Math.Max(k * FETCH_MULTIPLIER, FETCH_MIN);
        await using var conn = new NpgsqlConnection(db.ToConnectionString());
        await conn.OpenAsync().ConfigureAwait(false);
        if (useObstacleContext && string.IsNullOrEmpty(projectScopeKey) && !allowGlobalContextFallback)
        {
            (projectScopeKey, modelRevisionKey) = await ResolveActiveContextScopeAsync(conn).ConfigureAwait(false);
        }

        // [2.5] (선택) 쿼리 시점 장애물 컨텍스트 벡터 — 경로 없이 start/end + 주변 장애물만으로 계산 가능.
        //       1차 ANN 후보추출(FetchCandidatesAsync)에는 절대 섞지 않고 재정렬 단계에서만 사용한다.
        double[]? queryCtxVec = useObstacleContext
            ? await BuildContextVector30Async(
                conn, startXyz, endXyz, bay, projectScopeKey, modelRevisionKey).ConfigureAwait(false)
            : null;

        var candidates = await FetchCandidatesAsync(
            conn, queryVec,
            processName, equipmentName, utilityGroup, utility, size,
            fetchN, includeContext: useObstacleContext,
            projectScopeKey: projectScopeKey, modelRevisionKey: modelRevisionKey).ConfigureAwait(false);

        // [3] 하이브리드 재정렬
        ValidateRerankWeights(rerankWeights);
        var results = RerankHybrid(candidates, startXyz, endXyz, queryPattern, k, queryCtxVec,
            rerankWeights, redistributeMissingPatternWeight);

        sw.Stop();
        var meta = new SearchMeta
        {
            SearchTimeMs    = sw.Elapsed.TotalMilliseconds,
            TotalCandidates = candidates.Count,
            FetchN          = fetchN,
            ContextCandidates = useObstacleContext
                ? candidates.Count(c => c.ContextVector != null)
                : 0,
            FiltersApplied  = new Dictionary<string, string>
            {
                ["process_name"]   = processName   ?? "",
                ["equipment_name"] = equipmentName ?? "",
                ["utility_group"]  = utilityGroup  ?? "",
                ["utility"]        = utility       ?? "",
                ["size"]           = size          ?? "",
                ["bay"]            = bay           ?? "",
                ["project_scope_key"] = projectScopeKey,
                ["model_revision_key"] = modelRevisionKey,
            },
            QueryVectorHead     = queryVec.Take(6).ToArray(),
            QueryContextVectorHead = queryCtxVec?.Take(6).ToArray() ?? Array.Empty<double>(),
            UsedObstacleContext = useObstacleContext,
            RerankWeightProfile = DescribeRerankWeights(
                rerankWeights, useObstacleContext, queryPattern, redistributeMissingPatternWeight),
        };
        PopulateContextProvenance(meta, candidates, useObstacleContext);
        return (results, meta);
    }

    private static async Task<(string Project, string Revision)> ResolveActiveContextScopeAsync(
        NpgsqlConnection conn)
    {
        // ACTIVE가 정확히 한 건이어야 한다. 임의의 최신 revision 선택은 재현성을 깨므로 금지한다.
        await using var cmd = new NpgsqlCommand(
            """
            SELECT "PROJECT_SCOPE_KEY", "MODEL_REVISION_KEY"
            FROM "TB_ROUTE_SOURCE_SCOPE_MANIFEST"
            WHERE "STATUS"='ACTIVE'
            ORDER BY "PROMOTED_AT" DESC NULLS LAST
            LIMIT 2
            """, conn);
        var rows = new List<(string Project, string Revision)>();
        await using var reader = await cmd.ExecuteReaderAsync().ConfigureAwait(false);
        while (await reader.ReadAsync().ConfigureAwait(false))
            rows.Add((reader.GetString(0), reader.GetString(1)));
        if (rows.Count == 0)
            throw new InvalidOperationException(
                "No ACTIVE context revision. Promote one or set allowGlobalContextFallback=true explicitly.");
        if (rows.Count > 1)
            throw new InvalidOperationException(
                "Multiple ACTIVE context projects exist; provide projectScopeKey/modelRevisionKey explicitly.");
        return rows[0];
    }

    private static void PopulateContextProvenance(
        SearchMeta meta, IReadOnlyCollection<Candidate> candidates, bool useObstacleContext)
    {
        if (!useObstacleContext) return;
        var withContext = candidates.Where(candidate => candidate.ContextVector != null).ToList();
        var tuples = withContext
            .Select(candidate => string.Join("\u001f", new[]
            {
                candidate.ContextSnapshotHash, candidate.ContextScopeStatus, candidate.ContextBuildRunId,
                candidate.ContextProjectScopeKey, candidate.ContextModelRevisionKey,
                candidate.ContextEncoderVersion, candidate.ContextEncoderConfigHash,
            }))
            .Distinct(StringComparer.Ordinal)
            .ToList();
        meta.ContextProvenanceTupleCount = tuples.Count;
        if (withContext.Count == 0)
        {
            meta.ContextProvenanceConsistent = true;
            return;
        }
        var first = withContext[0];
        meta.ContextSnapshotHash = first.ContextSnapshotHash;
        meta.ContextScopeStatus = first.ContextScopeStatus;
        meta.ContextBuildRunId = first.ContextBuildRunId;
        meta.ContextProjectScopeKey = first.ContextProjectScopeKey;
        meta.ContextModelRevisionKey = first.ContextModelRevisionKey;
        meta.ContextEncoderVersion = first.ContextEncoderVersion;
        meta.ContextEncoderConfigHash = first.ContextEncoderConfigHash;
        var missing = new List<string>();
        if (string.IsNullOrWhiteSpace(meta.ContextSnapshotHash)) missing.Add("snapshot_hash");
        if (string.IsNullOrWhiteSpace(meta.ContextScopeStatus)) missing.Add("scope_status");
        if (string.IsNullOrWhiteSpace(meta.ContextBuildRunId)) missing.Add("build_run_id");
        if (string.IsNullOrWhiteSpace(meta.ContextEncoderVersion)) missing.Add("encoder_version");
        if (string.IsNullOrWhiteSpace(meta.ContextEncoderConfigHash)) missing.Add("encoder_config_hash");
        meta.ContextProvenanceConsistent = tuples.Count == 1 && missing.Count == 0;
        if (tuples.Count > 1)
            meta.ContextProvenanceIssue = $"mixed_context_provenance:{tuples.Count}";
        else if (missing.Count > 0)
            meta.ContextProvenanceIssue = "missing_context_provenance:" + string.Join(",", missing);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 2.2.0 공개 진입점 — 스키마 무결성 점검
    // ─────────────────────────────────────────────────────────────────────────

    /// <summary>FetchCandidatesAsync 가 SELECT 하는 TB_ROUTE_FEATURE_VECTOR 컬럼 + 허용 타입.</summary>
    private static readonly Dictionary<string, string[]> EXPECTED_FV_COLUMNS = new()
    {
        ["ROUTE_PATH_GUID"]   = new[] { "text", "character", "character varying" },
        ["PROCESS_NAME"]      = new[] { "text", "character", "character varying" },
        ["EQUIPMENT_NAME"]    = new[] { "text", "character", "character varying" },
        ["UTILITY_GROUP"]     = new[] { "text", "character", "character varying" },
        ["UTILITY"]           = new[] { "text", "character", "character varying" },
        ["SIZE"]              = new[] { "text", "character", "character varying" },
        ["DIRECTION_PATTERN"] = new[] { "text", "character", "character varying" },
        ["TOTAL_LENGTH_MM"]   = new[] { "double precision", "numeric", "real" },
        ["STEP_COUNT"]        = new[] { "integer", "bigint", "smallint", "double precision" },
        ["START_POSX"]        = new[] { "double precision", "numeric", "real" },
        ["START_POSY"]        = new[] { "double precision", "numeric", "real" },
        ["START_POSZ"]        = new[] { "double precision", "numeric", "real" },
        ["END_POSX"]          = new[] { "double precision", "numeric", "real" },
        ["END_POSY"]          = new[] { "double precision", "numeric", "real" },
        ["END_POSZ"]          = new[] { "double precision", "numeric", "real" },
        ["FEATURE_VECTOR"]    = new[] { "USER-DEFINED" },   // pgvector vector(30)
    };

    /// <summary>FetchPresetsCoreAsync 가 SELECT 하는 TB_ROUTE_PATH 컬럼 + 허용 타입.</summary>
    private static readonly Dictionary<string, string[]> EXPECTED_RP_COLUMNS = new()
    {
        ["ROUTE_PATH_GUID"]   = new[] { "text", "character", "character varying" },
        ["PROCESS_NAME"]      = new[] { "text", "character", "character varying" },
        ["EQUIPMENT_NAME"] = new[] { "text", "character", "character varying" },
        ["UTILITY_GROUP"]     = new[] { "text", "character", "character varying" },
        ["SOURCE_UTILITY"]    = new[] { "text", "character", "character varying" },
        ["SOURCE_SIZE"]       = new[] { "text", "character", "character varying" },
        ["SOURCE_POSX"]       = new[] { "double precision", "numeric", "real" },
        ["SOURCE_POSY"]       = new[] { "double precision", "numeric", "real" },
        ["SOURCE_POSZ"]       = new[] { "double precision", "numeric", "real" },
        ["TARGET_POSX"]       = new[] { "double precision", "numeric", "real" },
        ["TARGET_POSY"]       = new[] { "double precision", "numeric", "real" },
        ["TARGET_POSZ"]       = new[] { "double precision", "numeric", "real" },
        ["TARGET_OWNER_NAME"] = new[] { "text", "character", "character varying" },
        ["TOTAL_LENGTH"]   = new[] { "double precision", "numeric", "real" },
        // BEND_COUNT 가 double precision 인 환경이 실측됨 → 정수 타입과 모두 허용 (캐스팅 처리됨)
        ["BEND_COUNT"]     = new[] { "double precision", "numeric", "integer", "bigint", "smallint" },
    };

    /// <summary>
    /// 본 모듈이 의존하는 PostgreSQL 스키마(pgvector / TB_ROUTE_FEATURE_VECTOR / TB_ROUTE_PATH)를
    /// 일괄 점검하여 <see cref="SchemaCheckReport"/> 로 반환한다. CLI <c>--check-schema</c> 의 백엔드.
    /// 모든 점검은 read-only.
    /// </summary>
    public static async Task<SchemaCheckReport> CheckSchemaAsync(DbConfig db)
    {
        var rep = new SchemaCheckReport
        {
            ExpectedFvColumnCount = EXPECTED_FV_COLUMNS.Count,
            ExpectedRpColumnCount = EXPECTED_RP_COLUMNS.Count,
        };
        await using var conn = new NpgsqlConnection(db.ToConnectionString());
        await conn.OpenAsync().ConfigureAwait(false);

        // 1) pgvector 확장
        await using (var cmd = new NpgsqlCommand(
            "SELECT extname, extversion FROM pg_extension WHERE extname='vector';", conn))
        await using (var rd = await cmd.ExecuteReaderAsync())
        {
            if (await rd.ReadAsync())
            {
                rep.PgvectorInstalled = true;
                rep.PgvectorVersion   = rd.GetString(1);
            }
            else
            {
                rep.Failures.Add("pgvector 확장 미설치 — CREATE EXTENSION vector; 필요");
            }
        }

        // 2) 테이블 존재
        rep.FvTableExists = await TableExistsAsync(conn, "TB_ROUTE_FEATURE_VECTOR");
        rep.RpTableExists = await TableExistsAsync(conn, "TB_ROUTE_PATH");
        if (!rep.FvTableExists) rep.Failures.Add("테이블 누락: TB_ROUTE_FEATURE_VECTOR");
        if (!rep.RpTableExists) rep.Failures.Add("테이블 누락: TB_ROUTE_PATH");

        // 3) 컬럼/타입
        if (rep.FvTableExists)
        {
            var (missing, warns) = await CheckColumnsAsync(conn, "TB_ROUTE_FEATURE_VECTOR", EXPECTED_FV_COLUMNS);
            rep.FvMissingColumns     = missing;
            rep.FvColumnTypeWarnings = warns;
            foreach (var m in missing) rep.Failures.Add($"TB_ROUTE_FEATURE_VECTOR 누락 컬럼: {m}");
            foreach (var w in warns)   rep.Warnings.Add($"TB_ROUTE_FEATURE_VECTOR 타입 경고: {w}");
        }
        if (rep.RpTableExists)
        {
            var (missing, warns) = await CheckColumnsAsync(conn, "TB_ROUTE_PATH", EXPECTED_RP_COLUMNS);
            rep.RpMissingColumns     = missing;
            rep.RpColumnTypeWarnings = warns;
            foreach (var m in missing) rep.Failures.Add($"TB_ROUTE_PATH 누락 컬럼: {m}");
            foreach (var w in warns)   rep.Warnings.Add($"TB_ROUTE_PATH 타입 경고: {w}");
        }

        // 4) 인덱스 (HNSW 포함)
        await using (var cmd = new NpgsqlCommand(@"
            SELECT tablename, indexname, indexdef FROM pg_indexes
            WHERE schemaname='public'
              AND tablename IN ('TB_ROUTE_FEATURE_VECTOR','TB_ROUTE_PATH')
            ORDER BY tablename, indexname;", conn))
        await using (var rd = await cmd.ExecuteReaderAsync())
        {
            while (await rd.ReadAsync())
            {
                var defn = rd.GetString(2);
                bool isHnsw = defn.IndexOf("hnsw", StringComparison.OrdinalIgnoreCase) >= 0;
                rep.Indexes.Add((rd.GetString(0), rd.GetString(1), defn, isHnsw));
                if (isHnsw) rep.HasHnswIndex = true;
            }
        }
        if (rep.FvTableExists && !rep.HasHnswIndex)
            rep.Warnings.Add("TB_ROUTE_FEATURE_VECTOR.FEATURE_VECTOR HNSW 인덱스 없음 — Top-K ANN 성능 저하");

        // 5) 행 수
        if (rep.FvTableExists) rep.FvRowCount = await ScalarLongAsync(conn, "SELECT COUNT(*) FROM \"TB_ROUTE_FEATURE_VECTOR\";");
        if (rep.RpTableExists) rep.RpRowCount = await ScalarLongAsync(conn, "SELECT COUNT(*) FROM \"TB_ROUTE_PATH\";");
        if (rep.FvTableExists && rep.FvRowCount == 0) rep.Warnings.Add("TB_ROUTE_FEATURE_VECTOR 비어있음");
        if (rep.RpTableExists && rep.RpRowCount == 0) rep.Warnings.Add("TB_ROUTE_PATH 비어있음");

        // 6) FV NULL/빈값 (검색 핵심 컬럼)
        if (rep.FvTableExists)
        {
            await using var cmd = new NpgsqlCommand(@"
                SELECT
                  COUNT(*) FILTER (WHERE ""FEATURE_VECTOR""  IS NULL),
                  COUNT(*) FILTER (WHERE ""ROUTE_PATH_GUID"" IS NULL OR TRIM(""ROUTE_PATH_GUID"")=''),
                  COUNT(*) FILTER (WHERE ""PROCESS_NAME""    IS NULL OR TRIM(""PROCESS_NAME"")=''),
                  COUNT(*) FILTER (WHERE ""EQUIPMENT_NAME""  IS NULL OR TRIM(""EQUIPMENT_NAME"")=''),
                  COUNT(*) FILTER (WHERE ""UTILITY_GROUP""   IS NULL OR TRIM(""UTILITY_GROUP"")=''),
                  COUNT(*) FILTER (WHERE ""UTILITY""         IS NULL OR TRIM(""UTILITY"")='')
                FROM ""TB_ROUTE_FEATURE_VECTOR"";", conn);
            await using var rd = await cmd.ExecuteReaderAsync();
            if (await rd.ReadAsync())
            {
                rep.FvNullCounts["FEATURE_VECTOR_NULL"]   = rd.GetInt64(0);
                rep.FvNullCounts["ROUTE_PATH_GUID_EMPTY"] = rd.GetInt64(1);
                rep.FvNullCounts["PROCESS_NAME_EMPTY"]    = rd.GetInt64(2);
                rep.FvNullCounts["EQUIPMENT_NAME_EMPTY"]  = rd.GetInt64(3);
                rep.FvNullCounts["UTILITY_GROUP_EMPTY"]   = rd.GetInt64(4);
                rep.FvNullCounts["UTILITY_EMPTY"]         = rd.GetInt64(5);
            }
            if (rep.FvNullCounts.GetValueOrDefault("FEATURE_VECTOR_NULL") > 0)
                rep.Failures.Add($"FV.FEATURE_VECTOR IS NULL = {rep.FvNullCounts["FEATURE_VECTOR_NULL"]:N0} 건");
            if (rep.FvNullCounts.GetValueOrDefault("ROUTE_PATH_GUID_EMPTY") > 0)
                rep.Failures.Add($"FV.ROUTE_PATH_GUID empty = {rep.FvNullCounts["ROUTE_PATH_GUID_EMPTY"]:N0} 건");
            foreach (var key in new[] { "PROCESS_NAME_EMPTY", "EQUIPMENT_NAME_EMPTY", "UTILITY_GROUP_EMPTY", "UTILITY_EMPTY" })
            {
                long n = rep.FvNullCounts.GetValueOrDefault(key);
                if (n > 0) rep.Warnings.Add($"FV.{key.Replace("_EMPTY", "")} empty = {n:N0} 건");
            }
        }

        // 7) RP 좌표·꺾임 NULL
        if (rep.RpTableExists)
        {
            await using var cmd = new NpgsqlCommand(@"
                SELECT
                  COUNT(*) FILTER (WHERE ""SOURCE_POSX""       IS NULL),
                  COUNT(*) FILTER (WHERE ""TARGET_POSX""       IS NULL),
                  COUNT(*) FILTER (WHERE ""BEND_COUNT""     IS NULL),
                  COUNT(*) FILTER (WHERE ""EQUIPMENT_NAME"" IS NULL OR TRIM(""EQUIPMENT_NAME"")='')
                FROM ""TB_ROUTE_PATH"";", conn);
            await using var rd = await cmd.ExecuteReaderAsync();
            if (await rd.ReadAsync())
            {
                rep.RpNullCounts["SOURCE_POSX_NULL"]        = rd.GetInt64(0);
                rep.RpNullCounts["TARGET_POSX_NULL"]        = rd.GetInt64(1);
                rep.RpNullCounts["BEND_COUNT_NULL"]      = rd.GetInt64(2);
                rep.RpNullCounts["EQUIPMENT_NAME_EMPTY"] = rd.GetInt64(3);
            }
            foreach (var kv in rep.RpNullCounts)
                if (kv.Value > 0) rep.Warnings.Add($"RP.{kv.Key} = {kv.Value:N0} 건");
        }

        // 8) 벡터 차원 분포
        if (rep.FvTableExists && rep.FvRowCount > 0)
        {
            await using var cmd = new NpgsqlCommand(@"
                SELECT
                  array_length(string_to_array(trim(both '[]' FROM ""FEATURE_VECTOR""::text), ','), 1) AS dim,
                  COUNT(*)
                FROM ""TB_ROUTE_FEATURE_VECTOR""
                WHERE ""FEATURE_VECTOR"" IS NOT NULL
                GROUP BY 1 ORDER BY 2 DESC;", conn);
            await using var rd = await cmd.ExecuteReaderAsync();
            while (await rd.ReadAsync())
            {
                int dim = rd.IsDBNull(0) ? 0 : rd.GetInt32(0);
                long n  = rd.GetInt64(1);
                rep.VectorDimDistribution[dim] = n;
                if (dim != VECTOR_DIM)
                    rep.Failures.Add($"FV 벡터 차원={dim} (예상 {VECTOR_DIM}) — {n:N0} 건");
            }
        }

        // 9) End-to-end smoke (Fetch SELECT 모방 + <=> 연산자)
        if (rep.FvTableExists && rep.RpTableExists && rep.PgvectorInstalled)
        {
            try
            {
                await using var cmd = new NpgsqlCommand(@"
                    SELECT TRIM(""ROUTE_PATH_GUID""),
                           COALESCE(""SOURCE_POSX"",0), COALESCE(""TARGET_POSX"",0)
                    FROM ""TB_ROUTE_PATH"" LIMIT 1;", conn);
                await using var rd = await cmd.ExecuteReaderAsync();
                if (await rd.ReadAsync())
                    rep.EndToEndOk.Add($"TB_ROUTE_PATH 프리셋 SELECT 정상 (sample guid={Truncate8(rd.GetString(0))})");
                else
                    rep.Warnings.Add("TB_ROUTE_PATH 비어있어 smoke SELECT 결과 없음");
            }
            catch (Exception ex) { rep.EndToEndErr.Add($"TB_ROUTE_PATH smoke: {ex.Message}"); }

            try
            {
                await using var cmd = new NpgsqlCommand(@"
                    SELECT TRIM(""ROUTE_PATH_GUID""), ""FEATURE_VECTOR""::text
                    FROM ""TB_ROUTE_FEATURE_VECTOR"" LIMIT 1;", conn);
                await using var rd = await cmd.ExecuteReaderAsync();
                if (await rd.ReadAsync())
                {
                    string vt = rd.IsDBNull(1) ? "" : rd.GetString(1);
                    int dim = string.IsNullOrEmpty(vt) ? 0 : vt.Count(c => c == ',') + 1;
                    rep.EndToEndOk.Add($"TB_ROUTE_FEATURE_VECTOR 후보 SELECT 정상 (vec_text dim≈{dim})");
                }
            }
            catch (Exception ex) { rep.EndToEndErr.Add($"TB_ROUTE_FEATURE_VECTOR smoke: {ex.Message}"); }

            try
            {
                await using var cmd = new NpgsqlCommand(@"
                    SELECT MIN(""FEATURE_VECTOR"" <=> ""FEATURE_VECTOR"")
                    FROM ""TB_ROUTE_FEATURE_VECTOR"" LIMIT 1;", conn);
                var d = await cmd.ExecuteScalarAsync();
                rep.EndToEndOk.Add($"pgvector <=> 연산자 동작 (자기-자신 거리={d ?? "(null)"})");
            }
            catch (Exception ex)
            {
                rep.Failures.Add($"pgvector <=> 연산자 실패: {ex.Message}");
                rep.EndToEndErr.Add($"<=> smoke: {ex.Message}");
            }
        }

        return rep;
    }

    private static async Task<bool> TableExistsAsync(NpgsqlConnection conn, string tableName)
    {
        await using var cmd = new NpgsqlCommand(
            "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=@t LIMIT 1;",
            conn);
        cmd.Parameters.AddWithValue("t", tableName);
        var v = await cmd.ExecuteScalarAsync();
        return v != null && v != DBNull.Value;
    }

    private static async Task<long> ScalarLongAsync(NpgsqlConnection conn, string sql)
    {
        await using var cmd = new NpgsqlCommand(sql, conn);
        var v = await cmd.ExecuteScalarAsync();
        return v is long L ? L : Convert.ToInt64(v ?? 0);
    }

    private static async Task<(List<string> Missing, List<string> Warnings)>
        CheckColumnsAsync(NpgsqlConnection conn, string tableName, Dictionary<string, string[]> expected)
    {
        var actual = new Dictionary<string, (string DataType, string Udt)>();
        await using (var cmd = new NpgsqlCommand(@"
            SELECT column_name, data_type, udt_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=@t;", conn))
        {
            cmd.Parameters.AddWithValue("t", tableName);
            await using var rd = await cmd.ExecuteReaderAsync();
            while (await rd.ReadAsync())
                actual[rd.GetString(0)] = (rd.GetString(1), rd.GetString(2));
        }
        var missing = new List<string>();
        var warns   = new List<string>();
        foreach (var (col, allowed) in expected)
        {
            if (!actual.TryGetValue(col, out var info)) { missing.Add(col); continue; }
            bool dtOk  = allowed.Contains(info.DataType);
            bool udtOk = allowed.Contains("USER-DEFINED")
                         && info.Udt.Equals("vector", StringComparison.OrdinalIgnoreCase);
            if (!(dtOk || udtOk))
                warns.Add($"{col}: data_type='{info.DataType}' udt='{info.Udt}' (예상={string.Join("|", allowed)})");
        }
        return (missing, warns);
    }

    private static string Truncate8(string s) => s.Length > 8 ? s[..8] + "…" : s;

    // ─────────────────────────────────────────────────────────────────────────
    // 2.2.1 공개 진입점 — 프리셋 로드 (TB_ROUTE_PATH 참조)
    // ─────────────────────────────────────────────────────────────────────────

    /// <summary>
    /// TB_ROUTE_PATH 에서 "검색 기본 프리셋" 후보 목록을 가져온다. 전달된 필드 중
    /// 비어있지 않은 값만 WHERE 조건(AND)으로 적용되며, 나머지는 전체 대상.
    /// 결과는 공정/장비/유틸리티 오름차순 정렬.
    /// </summary>
    /// <param name="db">PostgreSQL 접속 정보.</param>
    /// <param name="processName">공정명 필터(빈 문자열이면 생략).</param>
    /// <param name="equipmentName">장비명 필터 → EQUIPMENT_NAME 과 매칭.</param>
    /// <param name="utilityGroup">유틸리티 그룹 필터.</param>
    /// <param name="utility">유틸리티 필터 → SOURCE_UTILITY 와 매칭.</param>
    /// <param name="size">배관 구경 필터 → SOURCE_SIZE 와 매칭.</param>
    /// <param name="limit">최대 반환 건수(기본 50).</param>
    public static async Task<List<RoutePreset>> FetchPresetsAsync(
        DbConfig db,
        string processName   = "",
        string equipmentName = "",
        string utilityGroup  = "",
        string utility       = "",
        string size          = "",
        int    limit         = 50)
    {
        if (limit < 1) throw new ArgumentOutOfRangeException(nameof(limit), "limit must be >= 1");
        await using var conn = new NpgsqlConnection(db.ToConnectionString());
        await conn.OpenAsync().ConfigureAwait(false);
        return await FetchPresetsCoreAsync(
            conn, processName, equipmentName, utilityGroup, utility, size, limit, singleGuid: null)
            .ConfigureAwait(false);
    }

    /// <summary>
    /// TB_ROUTE_PATH 에서 특정 GUID 한 건을 프리셋으로 반환한다. 존재하지 않으면 null.
    /// CLI 의 --preset-guid 처리에 사용.
    /// </summary>
    public static async Task<RoutePreset?> FetchPresetByGuidAsync(DbConfig db, string routePathGuid)
    {
        if (string.IsNullOrWhiteSpace(routePathGuid)) return null;
        await using var conn = new NpgsqlConnection(db.ToConnectionString());
        await conn.OpenAsync().ConfigureAwait(false);
        var list = await FetchPresetsCoreAsync(
            conn, "", "", "", "", "", limit: 1, singleGuid: routePathGuid.Trim())
            .ConfigureAwait(false);
        return list.Count > 0 ? list[0] : null;
    }

    /// <summary>TB_ROUTE_PATH SELECT 공용 구현. singleGuid 가 주어지면 다른 필터 무시하고 GUID 1건만 조회.</summary>
    private static async Task<List<RoutePreset>> FetchPresetsCoreAsync(
        NpgsqlConnection conn,
        string processName, string equipmentName,
        string utilityGroup, string utility, string size,
        int limit, string? singleGuid)
    {
        var whereParts  = new List<string>();
        var paramValues = new List<(string Name, string Val)>();

        if (!string.IsNullOrEmpty(singleGuid))
        {
            whereParts.Add("TRIM(\"ROUTE_PATH_GUID\") = @guid");
            paramValues.Add(("@guid", singleGuid));
        }
        else
        {
            AddFilter("PROCESS_NAME",      "@pn", processName,   whereParts, paramValues);
            AddFilter("EQUIPMENT_NAME", "@eq", equipmentName, whereParts, paramValues);
            AddFilter("UTILITY_GROUP",     "@ug", utilityGroup,  whereParts, paramValues);
            AddFilter("SOURCE_UTILITY",    "@ut", utility,       whereParts, paramValues);
            AddFilter("SOURCE_SIZE",       "@sz", size,          whereParts, paramValues);
        }

        string whereSql = whereParts.Count > 0
            ? "WHERE " + string.Join(" AND ", whereParts)
            : "";

        string sql = $@"
            SELECT
                TRIM(""ROUTE_PATH_GUID""),
                COALESCE(TRIM(""PROCESS_NAME""),      ''),
                COALESCE(TRIM(""EQUIPMENT_NAME""), ''),
                COALESCE(TRIM(""UTILITY_GROUP""),     ''),
                COALESCE(TRIM(""SOURCE_UTILITY""),    ''),
                COALESCE(TRIM(""SOURCE_SIZE""),       ''),
                COALESCE(""SOURCE_POSX"", 0), COALESCE(""SOURCE_POSY"", 0), COALESCE(""SOURCE_POSZ"", 0),
                COALESCE(""TARGET_POSX"", 0), COALESCE(""TARGET_POSY"", 0), COALESCE(""TARGET_POSZ"", 0),
                COALESCE(TRIM(""TARGET_OWNER_NAME""), ''),
                COALESCE(""TOTAL_LENGTH"", 0),
                COALESCE(""BEND_COUNT"", 0),
                COALESCE(TRIM(""BAY""), '')
            FROM ""TB_ROUTE_PATH""
            {whereSql}
            ORDER BY ""PROCESS_NAME"", ""EQUIPMENT_NAME"", ""SOURCE_UTILITY"", ""ROUTE_PATH_GUID""
            LIMIT @lim;";

        await using var cmd = new NpgsqlCommand(sql, conn);
        cmd.Parameters.AddWithValue("lim", limit);
        foreach (var (name, val) in paramValues)
            cmd.Parameters.AddWithValue(name.TrimStart('@'), val);

        var list = new List<RoutePreset>();
        await using var reader = await cmd.ExecuteReaderAsync().ConfigureAwait(false);
        while (await reader.ReadAsync().ConfigureAwait(false))
        {
            list.Add(new RoutePreset(
                RoutePathGuid:   reader.GetString(0),
                ProcessName:     reader.GetString(1),
                EquipmentName:   reader.GetString(2),
                UtilityGroup:    reader.GetString(3),
                Utility:         reader.GetString(4),
                Size:            reader.GetString(5),
                StartXyz:        (reader.GetDouble(6),  reader.GetDouble(7),  reader.GetDouble(8)),
                EndXyz:          (reader.GetDouble(9),  reader.GetDouble(10), reader.GetDouble(11)),
                TargetOwnerName: reader.GetString(12),
                TotalLengthMm:   reader.GetDouble(13),
                BendCount:       (int)Math.Round(reader.GetDouble(14)),
                Bay:             reader.GetString(15)));
        }
        return list;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 2.3 Phase 1 — 쿼리 벡터 생성
    // ─────────────────────────────────────────────────────────────────────────

    /// <summary>start/end 좌표로 30D 쿼리 벡터를 만든다. (스케일 + L2 정규화까지)
    /// DB에 저장된 원본 경로 벡터와 동일한 스케일 공간을 보장한다.</summary>
    public static double[] BuildQueryVector30D(
        (double X, double Y, double Z) startXyz,
        (double X, double Y, double Z) endXyz)
    {
        double dx = endXyz.X - startXyz.X;
        double dy = endXyz.Y - startXyz.Y;
        double dz = endXyz.Z - startXyz.Z;
        double length = Math.Sqrt(dx * dx + dy * dy + dz * dz);
        double lenSafe = length > 1e-9 ? length : 1.0;

        var vec = new double[VECTOR_DIM];

        // [0:3] Start 토폴로지 — 시작→종점 단위 방향
        vec[0] = dx / lenSafe;
        vec[1] = dy / lenSafe;
        vec[2] = dz / lenSafe;

        // [3:6] End 토폴로지 — 종점→시작 역방향
        vec[3] = -vec[0];
        vec[4] = -vec[1];
        vec[5] = -vec[2];

        // [6:9] 공간 변위 — displacement_max 정규화 (방향+크기)
        vec[6] = Clamp11(dx / DISPLACEMENT_MAX);
        vec[7] = Clamp11(dy / DISPLACEMENT_MAX);
        vec[8] = Clamp11(dz / DISPLACEMENT_MAX);

        // [9:12] 바운딩 박스 — 축별 크기 정규화 (부호 없음)
        vec[9]  = Clamp11(Math.Abs(dx) / BBOX_MAX_X);
        vec[10] = Clamp11(Math.Abs(dy) / BBOX_MAX_Y);
        vec[11] = Clamp11(Math.Abs(dz) / BBOX_MAX_Z);

        // [12:21] 3구간 꺾임 — 실경로 모름 → 0 유지
        // [22:24] 꺾임수/장애물/서포트 점수 — 0 유지
        // [25:29] Arrow 통계 — 0 유지

        // [21] 총 길이
        vec[21] = Clamp11(length / TOTAL_LENGTH_MAX);

        // 스케일 팩터 적용 (DB 벡터와 같은 코사인 공간 맞추기)
        for (int i = 0; i < VECTOR_DIM; i++) vec[i] *= ScaleFactors[i];

        // L2 정규화 (pgvector <=> 는 정규화된 벡터 가정)
        L2NormalizeInPlace(vec);
        return vec;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 2.4 Phase 1 — DB 후보 조회 (pgvector ANN)
    // ─────────────────────────────────────────────────────────────────────────

    /// <summary>내부 후보 레코드 (재정렬 입력).</summary>
    private sealed class Candidate
    {
        public string RoutePathGuid    { get; set; } = "";
        public string ProcessName      { get; set; } = "";
        public string EquipmentName    { get; set; } = "";
        public string UtilityGroup     { get; set; } = "";
        public string Utility          { get; set; } = "";
        public string Size             { get; set; } = "";
        public string DirectionPattern { get; set; } = "";
        public double TotalLengthMm    { get; set; }
        public int    StepCount        { get; set; }
        public (double X, double Y, double Z) StartXyz { get; set; }
        public (double X, double Y, double Z) EndXyz   { get; set; }
        public double   CosineDistance { get; set; }
        public double[] FeatureVector  { get; set; } = new double[VECTOR_DIM];
        /// <summary>TB_ROUTE_CONTEXT_VECTOR.CONTEXT_VECTOR (없으면 null). includeContext=true일 때만 채워짐.</summary>
        public double[]? ContextVector { get; set; }
        public string ContextSnapshotHash { get; set; } = "";
        public string ContextScopeStatus { get; set; } = "";
        public string ContextBuildRunId { get; set; } = "";
        public string ContextProjectScopeKey { get; set; } = "";
        public string ContextModelRevisionKey { get; set; } = "";
        public string ContextEncoderVersion { get; set; } = "";
        public string ContextEncoderConfigHash { get; set; } = "";
    }

    /// <summary>pgvector 코사인 거리 기준 상위 N개를 DB에서 가져온다.
    /// WHERE 필터는 비어있지 않은 항목만 AND 연결.</summary>
    /// <param name="includeContext">true면 TB_ROUTE_CONTEXT_VECTOR를 LEFT JOIN하여
    /// 각 후보의 30D 컨텍스트 벡터도 함께 가져온다(없는 후보는 null).</param>
    private static async Task<List<Candidate>> FetchCandidatesAsync(
        NpgsqlConnection conn,
        double[] queryVec,
        string processName, string equipmentName,
        string utilityGroup, string utility, string size,
        int fetchN, bool includeContext = false,
        string projectScopeKey = "", string modelRevisionKey = "")
    {
        // 1) WHERE 절 동적 구성 (바인딩 파라미터 이름 순서 기록)
        var whereParts  = new List<string>();
        var paramValues = new List<(string Name, string Val)>();
        AddFilter("PROCESS_NAME",   "@pn", processName,   whereParts, paramValues);
        AddFilter("EQUIPMENT_NAME", "@eq", equipmentName, whereParts, paramValues);
        AddFilter("UTILITY_GROUP",  "@ug", utilityGroup,  whereParts, paramValues);
        AddFilter("UTILITY",        "@ut", utility,       whereParts, paramValues);
        AddFilter("SIZE",           "@sz", size,          whereParts, paramValues);
        bool strictScope = !string.IsNullOrEmpty(projectScopeKey);
        if (strictScope)
        {
            whereParts.Add("fv.\"PROJECT_SCOPE_KEY\" = @cps");
            whereParts.Add("fv.\"MODEL_REVISION_KEY\" = @cmr");
            paramValues.Add(("@cps", projectScopeKey));
            paramValues.Add(("@cmr", modelRevisionKey));
        }
        string whereSql = whereParts.Count > 0
            ? "WHERE " + string.Join(" AND ", whereParts)
            : "";

        // 2) pgvector 리터럴 "[v0,v1,...]" 생성
        string vecLit = ToPgVectorLiteral(queryVec);

        // 2.5) 컨텍스트 벡터 LEFT JOIN (includeContext=true일 때만) — 없는 후보는 NULL로 자연 처리됨.
        //      절대 ORDER BY/1차 ANN 필터에는 관여하지 않음(재정렬 전용, 스키마 주석 참조).
        string ctxJoin   = includeContext ? $@"LEFT JOIN ""TB_ROUTE_CONTEXT_VECTOR"" cv
            ON TRIM(cv.""ROUTE_PATH_GUID"") = TRIM(fv.""ROUTE_PATH_GUID"")
           AND cv.""ENCODER_VERSION"" = '{CONTEXT_ENCODER_VERSION}'
           AND cv.""ENCODER_CONFIG_HASH"" = '{CONTEXT_ENCODER_CONFIG_HASH}'
           AND cv.""SCOPE_KIND"" = 'GLOBAL_SPATIAL_ALL_BAYS'
           AND cv.""PROJECT_SCOPE_KEY"" = @ctx_project
           AND cv.""MODEL_REVISION_KEY"" = @ctx_revision
           AND cv.""SCOPE_RESOLUTION_STATUS"" = @ctx_status" : "";
        string ctxSelect = includeContext ? @", cv.""CONTEXT_VECTOR""::text AS ctx_text,
                COALESCE(cv.""SOURCE_SNAPSHOT_HASH"", ''),
                COALESCE(cv.""SCOPE_RESOLUTION_STATUS"", ''),
                COALESCE(cv.""BUILD_RUN_ID""::text, ''),
                COALESCE(cv.""PROJECT_SCOPE_KEY"", ''),
                COALESCE(cv.""MODEL_REVISION_KEY"", ''),
                COALESCE(cv.""ENCODER_VERSION"", ''),
                COALESCE(cv.""ENCODER_CONFIG_HASH"", '')" : "";

        // 3) SQL — <=> 는 pgvector 코사인 거리 연산자
        //    FEATURE_VECTOR::text 는 후보의 [12:21] 꺾임 구간 비교에 필요
        string sql = $@"
            SELECT
                TRIM(fv.""ROUTE_PATH_GUID""),
                COALESCE(TRIM(fv.""PROCESS_NAME""),    ''),
                COALESCE(TRIM(fv.""EQUIPMENT_NAME""),  ''),
                COALESCE(TRIM(fv.""UTILITY_GROUP""),   ''),
                COALESCE(TRIM(fv.""UTILITY""),         ''),
                COALESCE(TRIM(fv.""SIZE""),            ''),
                COALESCE(TRIM(fv.""DIRECTION_PATTERN""), ''),
                COALESCE(fv.""TOTAL_LENGTH_MM"", 0),
                COALESCE(fv.""STEP_COUNT"", 0),
                COALESCE(fv.""START_POSX"", 0), COALESCE(fv.""START_POSY"", 0), COALESCE(fv.""START_POSZ"", 0),
                COALESCE(fv.""END_POSX"",   0), COALESCE(fv.""END_POSY"",   0), COALESCE(fv.""END_POSZ"",   0),
                (fv.""FEATURE_VECTOR"" <=> @vec::vector) AS cosine_distance,
                fv.""FEATURE_VECTOR""::text AS vec_text
                {ctxSelect}
            FROM ""TB_ROUTE_FEATURE_VECTOR"" fv
            {ctxJoin}
            {whereSql}
            ORDER BY fv.""FEATURE_VECTOR"" <=> @vec::vector
            LIMIT @n;";

        await using var cmd = new NpgsqlCommand(sql, conn);
        cmd.Parameters.AddWithValue("vec", vecLit);
        cmd.Parameters.AddWithValue("n",   fetchN);
        if (includeContext)
        {
            cmd.Parameters.AddWithValue("ctx_project", projectScopeKey);
            cmd.Parameters.AddWithValue("ctx_revision", modelRevisionKey);
            cmd.Parameters.AddWithValue(
                "ctx_status", strictScope ? "STRICT_COMMON_KEY" : "GLOBAL_FALLBACK_NO_COMMON_KEY");
        }
        foreach (var (name, val) in paramValues)
            cmd.Parameters.AddWithValue(name.TrimStart('@'), val);

        var list = new List<Candidate>(fetchN);
        await using var reader = await cmd.ExecuteReaderAsync().ConfigureAwait(false);
        while (await reader.ReadAsync().ConfigureAwait(false))
        {
            var c = new Candidate
            {
                RoutePathGuid    = reader.GetString(0),
                ProcessName      = reader.GetString(1),
                EquipmentName    = reader.GetString(2),
                UtilityGroup     = reader.GetString(3),
                Utility          = reader.GetString(4),
                Size             = reader.GetString(5),
                DirectionPattern = reader.GetString(6),
                TotalLengthMm    = reader.GetDouble(7),
                StepCount        = reader.GetInt32(8),
                StartXyz         = (reader.GetDouble(9),  reader.GetDouble(10), reader.GetDouble(11)),
                EndXyz           = (reader.GetDouble(12), reader.GetDouble(13), reader.GetDouble(14)),
                CosineDistance   = reader.GetDouble(15),
            };
            string vecText = reader.IsDBNull(16) ? "" : reader.GetString(16);
            c.FeatureVector = ParsePgVectorLiteral(vecText);
            if (includeContext && reader.FieldCount > 17 && !reader.IsDBNull(17))
            {
                c.ContextVector = ParsePgVectorLiteral(reader.GetString(17), CONTEXT_VECTOR_DIM);
                c.ContextSnapshotHash = reader.GetString(18);
                c.ContextScopeStatus = reader.GetString(19);
                c.ContextBuildRunId = reader.GetString(20);
                c.ContextProjectScopeKey = reader.GetString(21);
                c.ContextModelRevisionKey = reader.GetString(22);
                c.ContextEncoderVersion = reader.GetString(23);
                c.ContextEncoderConfigHash = reader.GetString(24);
            }
            list.Add(c);
        }
        return list;
    }

    private static void AddFilter(
        string colName, string paramName, string value,
        List<string> whereParts, List<(string, string)> paramValues)
    {
        if (!string.IsNullOrEmpty(value))
        {
            whereParts.Add($"\"{colName}\" = {paramName}");
            paramValues.Add((paramName, value));
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 2.5 Phase 2 — 하이브리드 재정렬
    // ─────────────────────────────────────────────────────────────────────────

    /// <summary>3~4가지 유사도(위치/패턴/벡터/[컨텍스트])의 가중합으로 후보를 재정렬, 상위 k개 반환.
    /// queryContextVector가 null이면(기본) 기존 3항목 가중치(0.50/0.30/0.20)로 RoutingAIViewer의
    /// TopKSearchService 와 완전히 동일한 결과를 낸다. non-null이고 후보 Context도 있으면
    /// 4항목 가중치(0.45/0.27/0.18/0.10)로 ctxScore를 추가 반영하며,
    /// 후보 Context가 없으면 기존 baseline 점수로 fallback한다.</summary>
    private static List<SearchResult> RerankHybrid(
        List<Candidate> candidates,
        (double X, double Y, double Z) startXyz,
        (double X, double Y, double Z) endXyz,
        string queryPattern,
        int k,
        double[]? queryContextVector = null,
        RerankWeights? requestedWeights = null,
        bool redistributeMissingPatternWeight = false)
    {
        bool useContext = queryContextVector != null;
        bool usePattern = !string.IsNullOrWhiteSpace(queryPattern) || !redistributeMissingPatternWeight;
        // 쿼리 변위벡터 Δq (start→end)
        double qdx = endXyz.X - startXyz.X;
        double qdy = endXyz.Y - startXyz.Y;
        double qdz = endXyz.Z - startXyz.Z;

        // 좌표만 입력받은 경우 쿼리 꺾임 벡터(9D)는 계산 불가 → null
        double[]? queryBendVec = null;

        var scored = new List<(double Combined, Candidate Cand, double Pos, double Pat, double Vec, double Ctx)>(candidates.Count);

        foreach (var c in candidates)
        {
            // (1) 상대위치 유사도 ──────────────────────────────────────────
            double cdx = c.EndXyz.X - c.StartXyz.X;
            double cdy = c.EndXyz.Y - c.StartXyz.Y;
            double cdz = c.EndXyz.Z - c.StartXyz.Z;
            double rdx = qdx - cdx, rdy = qdy - cdy, rdz = qdz - cdz;
            double relDist = Math.Sqrt(rdx * rdx + rdy * rdy + rdz * rdz);
            double posScore = Math.Max(0.0, 1.0 - relDist / REL_DIST_MAX_MM);

            // (2) 패턴 유사도 ───────────────────────────────────────────────
            double structScore = PatternSimilarity(queryPattern, c.DirectionPattern);
            double bendScore   = 0.0;
            if (queryBendVec != null)
            {
                // 후보의 꺾임 9D = FeatureVector[12..21]
                double[] candBend = new double[9];
                Array.Copy(c.FeatureVector, 12, candBend, 0, 9);
                bendScore = Math.Max(0.0, CosineSimilarity(queryBendVec, candBend));
            }
            double patternScore = structScore * RERANK_W_STRUCT + bendScore * RERANK_W_BEND;

            // (3) 벡터 유사도 ───────────────────────────────────────────────
            // pgvector <=> (cosine distance) = 1 - cosine similarity 관계
            double vecScore = 1.0 - c.CosineDistance;

            // (4) (선택) 장애물 컨텍스트 유사도 ─────────────────────────────
            // 후보에 TB_ROUTE_CONTEXT_VECTOR 레코드가 없으면(미색인) ctxScore=0으로 처리한다.
            bool hasCandidateContext = useContext && c.ContextVector != null;
            double ctxScore = 0.0;
            if (hasCandidateContext)
                ctxScore = Math.Max(0.0, CosineSimilarity(queryContextVector!, c.ContextVector!));

            // 후보 Context가 아직 색인되지 않은 경우에는 "환경 불일치"로 간주하지 않고
            // 기존 3항목 baseline 가중치로 fallback한다. 부분 색인 상태에서 ctxScore=0을
            // 강제하면 미색인 여부가 순위를 결정하는 coverage bias가 발생한다.
            var defaults = hasCandidateContext
                ? new RerankWeights(RERANK_W_POSITION_CTX, RERANK_W_PATTERN_CTX,
                    RERANK_W_VECTOR_CTX, RERANK_W_CONTEXT)
                : new RerankWeights(RERANK_W_POSITION, RERANK_W_PATTERN, RERANK_W_VECTOR, 0.0);
            var weights = requestedWeights ?? defaults;
            double wPos = weights.Position;
            double wPat = usePattern ? weights.Pattern : 0.0;
            double wVec = weights.Vector;
            double wCtx = hasCandidateContext ? weights.Context : 0.0;
            double activeWeight = wPos + wPat + wVec + wCtx;
            if (activeWeight <= 0.0)
                throw new InvalidOperationException("활성 유사도 가중치 합계가 0입니다.");
            wPos /= activeWeight;
            wPat /= activeWeight;
            wVec /= activeWeight;
            wCtx /= activeWeight;

            // 최종 가중합
            double combined = posScore     * wPos
                            + patternScore * wPat
                            + vecScore     * wVec
                            + ctxScore     * wCtx;

            scored.Add((combined, c, posScore, patternScore, vecScore, ctxScore));
        }

        // 내림차순 정렬 후 상위 k
        scored.Sort((a, b) => b.Combined.CompareTo(a.Combined));

        var results = new List<SearchResult>(Math.Min(k, scored.Count));
        int rank = 1;
        foreach (var (combined, c, pos, pat, vec, ctx) in scored.Take(k))
        {
            results.Add(new SearchResult(
                Rank:             rank++,
                RoutePathGuid:    c.RoutePathGuid,
                ProcessName:      c.ProcessName,
                EquipmentName:    c.EquipmentName,
                UtilityGroup:     c.UtilityGroup,
                Utility:          c.Utility,
                Size:             c.Size,
                DirectionPattern: c.DirectionPattern,
                TotalLengthMm:    c.TotalLengthMm,
                StepCount:        c.StepCount,
                StartXyz:         c.StartXyz,
                EndXyz:           c.EndXyz,
                CosineDistance:   c.CosineDistance,
                ScorePosition:    pos,
                ScorePattern:     pat,
                ScoreVector:      vec,
                SimilarityScore:  combined,
                ScoreContext:     ctx));
        }
        return results;
    }

    private static void ValidateRerankWeights(RerankWeights? weights)
    {
        if (weights is null) return;
        var values = new[] { weights.Position, weights.Pattern, weights.Vector, weights.Context };
        if (values.Any(value => !double.IsFinite(value) || value < 0.0))
            throw new ArgumentOutOfRangeException(nameof(weights), "유사도 가중치는 0 이상의 유한수여야 합니다.");
        if (values.Sum() <= 0.0)
            throw new ArgumentOutOfRangeException(nameof(weights), "유사도 가중치 합계는 0보다 커야 합니다.");
    }

    private static string DescribeRerankWeights(RerankWeights? requested, bool useContext,
        string queryPattern, bool redistributeMissingPatternWeight)
    {
        if (requested is null && !redistributeMissingPatternWeight)
            return useContext ? "context-v3:0.45/0.27/0.18/0.10" : "baseline:0.50/0.30/0.20";
        var weights = requested ?? (useContext
            ? new RerankWeights(RERANK_W_POSITION_CTX, RERANK_W_PATTERN_CTX,
                RERANK_W_VECTOR_CTX, RERANK_W_CONTEXT)
            : new RerankWeights(RERANK_W_POSITION, RERANK_W_PATTERN, RERANK_W_VECTOR, 0.0));
        var patternActive = !string.IsNullOrWhiteSpace(queryPattern) || !redistributeMissingPatternWeight;
        var p = weights.Position;
        var t = patternActive ? weights.Pattern : 0.0;
        var v = weights.Vector;
        var c = useContext ? weights.Context : 0.0;
        var sum = p + t + v + c;
        if (sum <= 0.0) return "invalid:0/0/0/0";
        return $"custom:{p / sum:F3}/{t / sum:F3}/{v / sum:F3}/{c / sum:F3}" +
               (patternActive ? "" : ":pattern-auto-off");
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 2.6 패턴 유사도 (Levenshtein on RLE-compressed tokens)
    // ─────────────────────────────────────────────────────────────────────────

    /// <summary>"H-R-H-D" 형태 Direction Pattern 유사도. 두 패턴을 RLE 압축 후
    /// 토큰 시퀀스 편집거리로 비교하고 [0,1] 로 정규화한다.</summary>
    private static double PatternSimilarity(string a, string b)
    {
        if (string.IsNullOrEmpty(a) || string.IsNullOrEmpty(b)) return 0.0;
        var tokA = CompressPattern(a.Split('-', StringSplitOptions.RemoveEmptyEntries));
        var tokB = CompressPattern(b.Split('-', StringSplitOptions.RemoveEmptyEntries));
        int dist = LevenshteinDistance(tokA, tokB);
        int maxLen = Math.Max(tokA.Length, tokB.Length);
        if (maxLen == 0) return 1.0;
        return Math.Max(0.0, 1.0 - (double)dist / maxLen);
    }

    /// <summary>연속 중복 토큰 축약: [R,H,H,H,R,H,R] → [R,H,R,H,R].</summary>
    private static string[] CompressPattern(string[] tokens)
    {
        if (tokens.Length == 0) return tokens;
        var result = new List<string>(tokens.Length) { tokens[0] };
        for (int i = 1; i < tokens.Length; i++)
            if (tokens[i] != result[^1]) result.Add(tokens[i]);
        return result.ToArray();
    }

    /// <summary>Levenshtein 편집거리(토큰 배열판). 시간/공간 O(m·n).</summary>
    private static int LevenshteinDistance(string[] a, string[] b)
    {
        int m = a.Length, n = b.Length;
        if (m == 0) return n;
        if (n == 0) return m;
        var dp = new int[m + 1, n + 1];
        for (int i = 0; i <= m; i++) dp[i, 0] = i;
        for (int j = 0; j <= n; j++) dp[0, j] = j;
        for (int i = 1; i <= m; i++)
        {
            for (int j = 1; j <= n; j++)
            {
                int cost = a[i - 1] == b[j - 1] ? 0 : 1;
                dp[i, j] = Math.Min(Math.Min(
                    dp[i - 1, j]     + 1,          // 삭제
                    dp[i, j - 1]     + 1),         // 삽입
                    dp[i - 1, j - 1] + cost);      // 치환
            }
        }
        return dp[m, n];
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 2.7 수치 유틸 (Cosine, Clamp, Normalize, pgvector literal IO)
    // ─────────────────────────────────────────────────────────────────────────

    /// <summary>값을 [-1, 1] 로 클램프. (함수명 'Clamp11' 은 ±1 범위 표기.)</summary>
    private static double Clamp11(double v) => Math.Max(-1.0, Math.Min(1.0, v));

    /// <summary>L2 정규화 (in-place). 영벡터이면 변경 없음.</summary>
    private static void L2NormalizeInPlace(double[] v)
    {
        double sum = 0;
        for (int i = 0; i < v.Length; i++) sum += v[i] * v[i];
        double n = Math.Sqrt(sum);
        if (n < 1e-12) return;
        for (int i = 0; i < v.Length; i++) v[i] /= n;
    }

    /// <summary>두 동일 차원 벡터의 코사인 유사도 (-1 ~ 1). 영벡터는 0 반환.</summary>
    private static double CosineSimilarity(double[] a, double[] b)
    {
        if (a.Length != b.Length) return 0.0;
        double dot = 0, na = 0, nb = 0;
        for (int i = 0; i < a.Length; i++)
        {
            dot += a[i] * b[i];
            na  += a[i] * a[i];
            nb  += b[i] * b[i];
        }
        double denom = Math.Sqrt(na) * Math.Sqrt(nb);
        return denom < 1e-12 ? 0.0 : dot / denom;
    }

    /// <summary>30D 벡터를 pgvector 리터럴 "[v0,v1,...]" 문자열로 변환.
    /// Invariant culture 로 포맷하여 로캘 콤마 소수점 이슈를 회피한다.</summary>
    private static string ToPgVectorLiteral(double[] v)
    {
        var sb = new StringBuilder(v.Length * 10);
        sb.Append('[');
        for (int i = 0; i < v.Length; i++)
        {
            if (i > 0) sb.Append(',');
            sb.Append(v[i].ToString("G", CultureInfo.InvariantCulture));
        }
        sb.Append(']');
        return sb.ToString();
    }

    /// <summary>pgvector 텍스트 "[v0,v1,...]" → double[dim]. 차원 불일치 시 0 패딩.
    /// dim 생략 시 VECTOR_DIM(30) — CONTEXT_VECTOR_DIM(24) 파싱 시 명시적으로 전달.</summary>
    private static double[] ParsePgVectorLiteral(string text, int dim = VECTOR_DIM)
    {
        var result = new double[dim];
        if (string.IsNullOrEmpty(text)) return result;
        var s = text.Trim();
        if (s.StartsWith('[')) s = s[1..];
        if (s.EndsWith(']'))   s = s[..^1];
        var parts = s.Split(',');
        int count = Math.Min(parts.Length, dim);
        for (int i = 0; i < count; i++)
        {
            double.TryParse(parts[i], NumberStyles.Float, CultureInfo.InvariantCulture, out double val);
            result[i] = val;
        }
        return result;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 2.8 (선택) 장애물 컨텍스트 벡터 — 30D, 쿼리 시점 계산 가능
    // ─────────────────────────────────────────────────────────────────────────
    //  Tools/ExtractObstacleContextVector.py 와 완전히 동일한 레이아웃/공식으로
    //  계산해야 코사인 비교가 의미를 가진다(양쪽 구현이 어긋나면 조용히 틀린
    //  유사도를 낼 뿐 예외가 나지 않으므로 특히 주의).
    // ─────────────────────────────────────────────────────────────────────────

    private static readonly string[] ColumnDdworksTypes = { "COLUMN_ARCHITECTURE", "COLUMN_STRUCTURE" };
    private static readonly string[] BeamDdworksTypes    = { "BEAM_ARCHITECTURE", "BEAM_STRUCTURE" };

    private sealed class NearbyObstacle
    {
        public string Id = "";
        public (double X, double Y, double Z) Minimum;
        public (double X, double Y, double Z) Maximum;
        public (double X, double Y, double Z) Center;
        public (double X, double Y, double Z) Closest;
        public (double X, double Y, double Z) Extent;
        public double Distance;
    }

    /// <summary>start/end 좌표 + 전역 공간의 주변 장애물(TB_BIM_OBSTACLE)만으로 30D 컨텍스트 벡터를 계산한다.
    /// BAY 문자열로 선필터하지 않고 실제 1,000mm 반경/경로 바운딩박스로 공간 필터링한다.</summary>
    public static async Task<double[]> BuildContextVector30Async(
        NpgsqlConnection conn,
        (double X, double Y, double Z) startXyz,
        (double X, double Y, double Z) endXyz,
        string bay,
        string projectScopeKey = "",
        string modelRevisionKey = "")
    {
        var startColumns = await FetchNearbyObstaclesAsync(conn, startXyz, CTX_MID_RADIUS_MM, ColumnDdworksTypes, projectScopeKey, modelRevisionKey).ConfigureAwait(false);
        var startBeams    = await FetchNearbyObstaclesAsync(conn, startXyz, CTX_MID_RADIUS_MM, BeamDdworksTypes, projectScopeKey, modelRevisionKey).ConfigureAwait(false);
        var endColumns    = await FetchNearbyObstaclesAsync(conn, endXyz,   CTX_MID_RADIUS_MM, ColumnDdworksTypes, projectScopeKey, modelRevisionKey).ConfigureAwait(false);
        var endBeams      = await FetchNearbyObstaclesAsync(conn, endXyz,   CTX_MID_RADIUS_MM, BeamDdworksTypes, projectScopeKey, modelRevisionKey).ConfigureAwait(false);

        double[] startContext = EncodeEndpoint(startXyz, startColumns, startBeams);
        double[] endContext   = EncodeEndpoint(endXyz,   endColumns,   endBeams);

        // Tier3: 경로 전체 컬럼 격자셀 탐색은 start~end 바운딩박스 하나로 일괄 조회(성능).
        double minX = Math.Min(startXyz.X, endXyz.X) - 600.0, maxX = Math.Max(startXyz.X, endXyz.X) + 600.0;
        double minY = Math.Min(startXyz.Y, endXyz.Y) - 600.0, maxY = Math.Max(startXyz.Y, endXyz.Y) + 600.0;
        double avgZ = (startXyz.Z + endXyz.Z) / 2.0;
        var pathColumns = await FetchObstaclesInBoxAsync(conn, minX, minY, maxX, maxY, ColumnDdworksTypes, projectScopeKey, modelRevisionKey).ConfigureAwait(false);
        double[] tier3 = EncodeTier3(startXyz, endXyz, pathColumns, startBeams.Concat(endBeams).ToList());

        var full = new double[CONTEXT_VECTOR_DIM];
        Array.Copy(startContext, 0, full, 0, CONTEXT_ENDPOINT_DIM);
        Array.Copy(endContext, 0, full, CONTEXT_ENDPOINT_DIM, CONTEXT_ENDPOINT_DIM);
        Array.Copy(tier3, 0, full, CONTEXT_ENDPOINT_DIM * 2, 4);
        L2NormalizeInPlace(full);
        return full;
    }

    /// <summary>point 주변 radius(mm) 이내, 지정 DDWORKS_TYPE 목록에 속한 장애물을 AABB 범위조건으로 조회.</summary>
    private static async Task<List<NearbyObstacle>> FetchNearbyObstaclesAsync(
        NpgsqlConnection conn, (double X, double Y, double Z) point, double radius, string[] ddworksTypes,
        string projectScopeKey = "", string modelRevisionKey = "")
    {
        bool strictScope = !string.IsNullOrEmpty(projectScopeKey);
        string sql = @"
            SELECT COALESCE(""INSTANCE_ID"", ''),
                   ""AABB_MINX"",""AABB_MINY"",""AABB_MINZ"",""AABB_MAXX"",""AABB_MAXY"",""AABB_MAXZ""
            FROM ""TB_BIM_OBSTACLE""
            WHERE ""DDWORKS_TYPE"" = ANY(@types)
              AND ""AABB_MINX"" <= @maxx AND ""AABB_MAXX"" >= @minx
              AND ""AABB_MINY"" <= @maxy AND ""AABB_MAXY"" >= @miny
              AND ""AABB_MINZ"" <= @maxz AND ""AABB_MAXZ"" >= @minz" +
              (strictScope ? @" AND ""PROJECT_SCOPE_KEY"" = @cps AND ""MODEL_REVISION_KEY"" = @cmr" : "");
        await using var cmd = new NpgsqlCommand(sql, conn);
        cmd.Parameters.AddWithValue("types", ddworksTypes);
        cmd.Parameters.AddWithValue("minx", point.X - radius); cmd.Parameters.AddWithValue("maxx", point.X + radius);
        cmd.Parameters.AddWithValue("miny", point.Y - radius); cmd.Parameters.AddWithValue("maxy", point.Y + radius);
        cmd.Parameters.AddWithValue("minz", point.Z - radius); cmd.Parameters.AddWithValue("maxz", point.Z + radius);
        if (strictScope)
        {
            cmd.Parameters.AddWithValue("cps", projectScopeKey);
            cmd.Parameters.AddWithValue("cmr", modelRevisionKey);
        }

        var result = new List<NearbyObstacle>();
        await using var reader = await cmd.ExecuteReaderAsync().ConfigureAwait(false);
        while (await reader.ReadAsync().ConfigureAwait(false))
        {
            string rawId = reader.GetString(0);
            double minx = reader.GetDouble(1), miny = reader.GetDouble(2), minz = reader.GetDouble(3);
            double maxx = reader.GetDouble(4), maxy = reader.GetDouble(5), maxz = reader.GetDouble(6);
            var minimum = (minx, miny, minz);
            var maximum = (maxx, maxy, maxz);
            var center = ((minx + maxx) / 2.0, (miny + maxy) / 2.0, (minz + maxz) / 2.0);
            var closest = ClosestPointOnAabb(point, minimum, maximum);
            double d = Distance3(point, closest);
            if (d <= radius)
                result.Add(new NearbyObstacle
                {
                    Id = FormattableString.Invariant($"{rawId.Trim()}|{minx:R}|{miny:R}|{minz:R}|{maxx:R}|{maxy:R}|{maxz:R}"),
                    Minimum = minimum, Maximum = maximum, Center = center, Closest = closest,
                    Extent = (maxx - minx, maxy - miny, maxz - minz), Distance = d
                });
        }
        return result;
    }

    /// <summary>XY 바운딩박스 내 지정 타입 장애물 전체 조회 (Tier3 경로 격자셀 탐색용, Z 조건 없음).</summary>
    private static async Task<List<NearbyObstacle>> FetchObstaclesInBoxAsync(
        NpgsqlConnection conn, double minX, double minY, double maxX, double maxY, string[] ddworksTypes,
        string projectScopeKey = "", string modelRevisionKey = "")
    {
        bool strictScope = !string.IsNullOrEmpty(projectScopeKey);
        string sql = @"
            SELECT COALESCE(""INSTANCE_ID"", ''),
                   ""AABB_MINX"",""AABB_MINY"",""AABB_MINZ"",""AABB_MAXX"",""AABB_MAXY"",""AABB_MAXZ""
            FROM ""TB_BIM_OBSTACLE""
            WHERE ""DDWORKS_TYPE"" = ANY(@types)
              AND ""AABB_MINX"" <= @maxx AND ""AABB_MAXX"" >= @minx
              AND ""AABB_MINY"" <= @maxy AND ""AABB_MAXY"" >= @miny" +
              (strictScope ? @" AND ""PROJECT_SCOPE_KEY"" = @cps AND ""MODEL_REVISION_KEY"" = @cmr" : "");
        await using var cmd = new NpgsqlCommand(sql, conn);
        cmd.Parameters.AddWithValue("types", ddworksTypes);
        cmd.Parameters.AddWithValue("minx", minX); cmd.Parameters.AddWithValue("maxx", maxX);
        cmd.Parameters.AddWithValue("miny", minY); cmd.Parameters.AddWithValue("maxy", maxY);
        if (strictScope)
        {
            cmd.Parameters.AddWithValue("cps", projectScopeKey);
            cmd.Parameters.AddWithValue("cmr", modelRevisionKey);
        }

        var result = new List<NearbyObstacle>();
        await using var reader = await cmd.ExecuteReaderAsync().ConfigureAwait(false);
        while (await reader.ReadAsync().ConfigureAwait(false))
        {
            string rawId = reader.GetString(0);
            double minx = reader.GetDouble(1), miny = reader.GetDouble(2), minz = reader.GetDouble(3);
            double maxx = reader.GetDouble(4), maxy = reader.GetDouble(5), maxz = reader.GetDouble(6);
            var minimum = (minx, miny, minz);
            var maximum = (maxx, maxy, maxz);
            var center = ((minx + maxx) / 2.0, (miny + maxy) / 2.0, (minz + maxz) / 2.0);
            result.Add(new NearbyObstacle
            {
                Id = FormattableString.Invariant($"{rawId.Trim()}|{minx:R}|{miny:R}|{minz:R}|{maxx:R}|{maxy:R}|{maxz:R}"),
                Minimum = minimum, Maximum = maximum, Center = center, Closest = center,
                Extent = (maxx - minx, maxy - miny, maxz - minz), Distance = 0.0
            });
        }
        return result;
    }

    /// <summary>한 endpoint를 13D로 인코딩한다. 기둥/보 각각
    /// near count, 500~1000mm count, 최근접 AABB 표면방향 XYZ, 표면거리/1000 순서이며
    /// 마지막 차원은 1000mm 안에 기둥/보가 모두 없는 free-space 표시다.</summary>
    private static double[] EncodeEndpoint(
        (double X, double Y, double Z) point, List<NearbyObstacle> columns, List<NearbyObstacle> beams)
    {
        var vector = new double[CONTEXT_ENDPOINT_DIM];

        void Fill(int offset, List<NearbyObstacle> obstacles, double countScale)
        {
            int nearCount = obstacles.Count(o => o.Distance <= CTX_NEAR_RADIUS_MM + 1e-9);
            int midCount = obstacles.Count - nearCount;
            vector[offset] = Clamp01(nearCount / countScale);
            vector[offset + 1] = Clamp01(midCount / countScale);
            if (obstacles.Count == 0) return;

            var nearest = obstacles.OrderBy(o => o.Distance)
                .ThenBy(o => o.Minimum.X).ThenBy(o => o.Minimum.Y).ThenBy(o => o.Minimum.Z)
                .ThenBy(o => o.Maximum.X).ThenBy(o => o.Maximum.Y).ThenBy(o => o.Maximum.Z)
                .ThenBy(o => o.Id, StringComparer.Ordinal).First();
            if (nearest.Distance > 1e-9)
            {
                vector[offset + 2] = (nearest.Closest.X - point.X) / nearest.Distance;
                vector[offset + 3] = (nearest.Closest.Y - point.Y) / nearest.Distance;
                vector[offset + 4] = (nearest.Closest.Z - point.Z) / nearest.Distance;
            }
            vector[offset + 5] = Clamp01(nearest.Distance / CTX_MID_RADIUS_MM);
        }

        Fill(0, columns, 8.0);
        Fill(6, beams, 5.0);
        vector[12] = columns.Count == 0 && beams.Count == 0 ? 1.0 : 0.0;
        return vector;
    }

    /// <summary>시작~종료 2점 경로 기준 보조 특징 4D (Tools/ExtractObstacleContextVector.py::encode_tier3 와 동일 공식).
    /// [0] Z층 전환수/3, [1] 경로상 1m 격자 중 기둥이 있는 셀 수/15, [2] 진행방향-보 장축 평행도, [3] 수평 진행방향 코사인.</summary>
    private static double[] EncodeTier3(
        (double X, double Y, double Z) start, (double X, double Y, double Z) end,
        List<NearbyObstacle> pathColumns, List<NearbyObstacle> nearbyBeams)
    {
        const double gridCellMm = 1000.0;
        const int maxCells = 200;

        var zLevels = new HashSet<double>(new[] { Math.Round(start.Z / 500.0) * 500.0, Math.Round(end.Z / 500.0) * 500.0 });
        double layerTransitions = Clamp01((zLevels.Count - 1) / 3.0);

        double dx = end.X - start.X, dy = end.Y - start.Y;
        double horizLen = Math.Sqrt(dx * dx + dy * dy);
        var cells = LineGridCells(start, end, gridCellMm);
        IReadOnlyList<(int X, int Y)> limitedCells = cells;
        if (cells.Count > maxCells)
        {
            var sampled = new List<(int X, int Y)>(maxCells);
            int last = cells.Count - 1;
            for (int i = 0; i < maxCells; i++)
                sampled.Add(cells[(int)Math.Round(i * last / (double)(maxCells - 1))]);
            limitedCells = sampled;
        }

        int colCellCount = 0;
        foreach (var (cx, cy) in limitedCells)
        {
            double centerX = (cx + 0.5) * gridCellMm, centerY = (cy + 0.5) * gridCellMm;
            var cellCenter = (centerX, centerY, (start.Z + end.Z) / 2.0);
            bool hasColumn = pathColumns.Any(o => PointAabbDistance(cellCenter, o.Minimum, o.Maximum) <= gridCellMm * 0.6 + 1e-9);
            if (hasColumn) colCellCount++;
        }
        double columnGridScore = Clamp01(colCellCount / 15.0);

        double beamParallelism = 0.0;
        if (horizLen > 1e-6 && nearbyBeams.Count > 0)
        {
            double pux = dx / horizLen, puy = dy / horizLen;
            var scores = new List<double>();
            foreach (var b in nearbyBeams.GroupBy(o => o.Id, StringComparer.Ordinal).Select(g => g.First()))
            {
                var ext = new[] { b.Extent.X, b.Extent.Y, b.Extent.Z };
                int axis = 0;
                for (int i = 1; i < 3; i++) if (ext[i] > ext[axis]) axis = i;
                double bux, buy;
                if (axis == 0) { bux = 1.0; buy = 0.0; }
                else if (axis == 1) { bux = 0.0; buy = 1.0; }
                else continue; // 장축이 수직(Z)인 보는 수평 평행도 비교에서 제외
                scores.Add(Math.Abs(pux * bux + puy * buy));
            }
            if (scores.Count > 0) beamParallelism = scores.Average();
        }

        double bearingCos = horizLen > 1e-6 ? dx / horizLen : 0.0;

        return new[] { layerTransitions, columnGridScore, beamParallelism, bearingCos };
    }

    private static List<(int X, int Y)> LineGridCells(
        (double X, double Y, double Z) start, (double X, double Y, double Z) end, double cellSize)
    {
        double x0 = start.X / cellSize, y0 = start.Y / cellSize;
        double x1 = end.X / cellSize, y1 = end.Y / cellSize;
        int cx = (int)Math.Floor(x0), cy = (int)Math.Floor(y0);
        int endX = (int)Math.Floor(x1), endY = (int)Math.Floor(y1);
        var cells = new List<(int X, int Y)> { (cx, cy) };
        double dx = x1 - x0, dy = y1 - y0;
        int stepX = dx > 0 ? 1 : dx < 0 ? -1 : 0;
        int stepY = dy > 0 ? 1 : dy < 0 ? -1 : 0;
        double tDeltaX = dx == 0 ? double.PositiveInfinity : Math.Abs(1.0 / dx);
        double tDeltaY = dy == 0 ? double.PositiveInfinity : Math.Abs(1.0 / dy);
        double nextX = stepX > 0 ? cx + 1 : cx;
        double nextY = stepY > 0 ? cy + 1 : cy;
        double tMaxX = dx == 0 ? double.PositiveInfinity : (nextX - x0) / dx;
        double tMaxY = dy == 0 ? double.PositiveInfinity : (nextY - y0) / dy;

        while (cx != endX || cy != endY)
        {
            if (tMaxX < tMaxY) { cx += stepX; tMaxX += tDeltaX; }
            else if (tMaxY < tMaxX) { cy += stepY; tMaxY += tDeltaY; }
            else { cx += stepX; cy += stepY; tMaxX += tDeltaX; tMaxY += tDeltaY; }
            cells.Add((cx, cy));
        }
        return cells;
    }

    private static (double X, double Y, double Z) ClosestPointOnAabb(
        (double X, double Y, double Z) point,
        (double X, double Y, double Z) minimum,
        (double X, double Y, double Z) maximum)
        => (Math.Max(minimum.X, Math.Min(point.X, maximum.X)),
            Math.Max(minimum.Y, Math.Min(point.Y, maximum.Y)),
            Math.Max(minimum.Z, Math.Min(point.Z, maximum.Z)));

    private static double PointAabbDistance(
        (double X, double Y, double Z) point,
        (double X, double Y, double Z) minimum,
        (double X, double Y, double Z) maximum)
        => Distance3(point, ClosestPointOnAabb(point, minimum, maximum));

    private static double Clamp01(double v) => Math.Max(0.0, Math.Min(1.0, v));

    private static double Distance3((double X, double Y, double Z) a, (double X, double Y, double Z) b)
    {
        double dx = a.X - b.X, dy = a.Y - b.Y, dz = a.Z - b.Z;
        return Math.Sqrt(dx * dx + dy * dy + dz * dz);
    }
}


// ═════════════════════════════════════════════════════════════════════════════
// 섹션 3 — CLI 진입점 (Program.Main)
// ═════════════════════════════════════════════════════════════════════════════
//
//  본 섹션을 제거해도 TopKSearchStandalone 클래스는 라이브러리로 동작한다.
//  `dotnet run -- --help` 형태로 콘솔 실행 시에만 사용됨.
// ═════════════════════════════════════════════════════════════════════════════

internal static class Program
{
    private static async Task<int> Main(string[] args)
    {
        if (args.Length == 0 || args.Contains("-h") || args.Contains("--help"))
        {
            PrintHelp();
            return 0;
        }

        CliOptions opt;
        try { opt = ParseArgs(args); }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[인자 오류] {ex.Message}\n");
            PrintHelp();
            return 2;
        }

        try
        {
            var db = new DbConfig(opt.Host, opt.Port, opt.Dbname, opt.User, opt.Password);

            // UtilityPipeGroup Matcher golden test는 DB 없이 실행한다.
            if (opt.GroupSelfTest)
                return UtilityPipeGroupMatcherSelfTests.RunAll();

            if (opt.GroupEvaluate)
            {
                var report = await UtilityPipeGroupEvaluation.RunMarkdownAsync(db, opt.GroupEvaluateSample, opt.K);
                var output = Path.GetFullPath(opt.GroupEvaluateOut);
                Directory.CreateDirectory(Path.GetDirectoryName(output)!);
                await File.WriteAllTextAsync(output, report, Encoding.UTF8);
                Console.WriteLine($"UtilityPipeGroup 평가 보고서: {output}");
                return 0;
            }

            // (Z) --check-schema 모드: pgvector + 두 테이블의 스키마 무결성 진단
            if (opt.CheckSchema)
            {
                var rep = await TopKSearchStandalone.CheckSchemaAsync(db);
                PrintSchemaCheck(rep);
                return rep.IsHealthy ? 0 : 5;
            }

            if (opt.ListGroupPresets)
            {
                var presets = await UtilityPipeGroupSearch.FetchPresetsAsync(
                    db, opt.Process, opt.Equipment, opt.UtilityGroup, opt.Utility, opt.PresetLimit);
                if (opt.Json)
                    Console.WriteLine(System.Text.Json.JsonSerializer.Serialize(presets,
                        new System.Text.Json.JsonSerializerOptions
                        {
                            WriteIndented = true,
                            PropertyNamingPolicy = System.Text.Json.JsonNamingPolicy.SnakeCaseLower,
                            Converters = { new System.Text.Json.Serialization.JsonStringEnumConverter() },
                        }));
                else
                    foreach (var preset in presets)
                        Console.WriteLine($"{preset.GroupVectorId} | {preset.Display}");
                return 0;
            }

            // 신규 UtilityPipeGroup Top-K. 기존 개별 Route 검색 분기와 완전히 분리한다.
            if (!string.IsNullOrWhiteSpace(opt.GroupQueryId) || opt.GroupSearchByIdentity)
            {
                var groupOptions = new UtilityPipeGroupSearchOptions
                {
                    K = opt.K,
                    SizeMatchMode = opt.GroupSizeMode,
                    RequireSameProcess = opt.GroupRequireSameProcess,
                    EquipmentFamilyKey = opt.GroupEquipmentFamily,
                };
                var (groupResults, groupMeta) = opt.GroupSearchByIdentity
                    ? await UtilityPipeGroupSearch.SearchByIdentityAsync(
                        db, opt.Process, opt.Equipment, opt.UtilityGroup, opt.Utility, groupOptions)
                    : await UtilityPipeGroupSearch.SearchAsync(db, opt.GroupQueryId, groupOptions);
                if (opt.Json)
                {
                    Console.WriteLine(System.Text.Json.JsonSerializer.Serialize(
                        new { meta = groupMeta, results = groupResults },
                        new System.Text.Json.JsonSerializerOptions
                        {
                            WriteIndented = true,
                            PropertyNamingPolicy = System.Text.Json.JsonNamingPolicy.SnakeCaseLower,
                            Converters = { new System.Text.Json.Serialization.JsonStringEnumConverter() },
                        }));
                }
                else
                {
                    PrintGroupResults(groupResults, groupMeta);
                }
                return 0;
            }

            // (A) --list-presets 모드: TB_ROUTE_PATH 에서 프리셋 후보를 나열하고 종료
            if (opt.ListPresets)
            {
                var presets = await TopKSearchStandalone.FetchPresetsAsync(
                    db,
                    processName:   opt.Process,
                    equipmentName: opt.Equipment,
                    utilityGroup:  opt.UtilityGroup,
                    utility:       opt.Utility,
                    size:          opt.Size,
                    limit:         opt.PresetLimit);

                if (opt.Json) PrintPresetsJson(presets, opt);
                else          PrintPresetsHuman(presets, opt);
                return 0;
            }

            // (B-1) --preset-rank N 모드: 현재 필터로 프리셋 후보를 조회 → N번째(1-base) 행을 골라 검색.
            //       GUID 를 미리 알 필요가 없는 "한줄 실행" 워크플로우.
            //       (개별 --process/--equipment/... 는 필터로 동작하면서, 동시에 빈 값은 프리셋이 채움)
            if (opt.PresetRank > 0)
            {
                int needed = Math.Max(opt.PresetRank, 1);
                var presets = await TopKSearchStandalone.FetchPresetsAsync(
                    db,
                    processName:   opt.Process,
                    equipmentName: opt.Equipment,
                    utilityGroup:  opt.UtilityGroup,
                    utility:       opt.Utility,
                    size:          opt.Size,
                    limit:         needed);
                if (presets.Count < opt.PresetRank)
                {
                    Console.Error.WriteLine(
                        $"[프리셋 없음] --preset-rank {opt.PresetRank} 요청했지만 필터 조건과 일치하는 행이 {presets.Count}건뿐입니다. " +
                        "필터를 완화하거나 --preset-rank 값을 줄이세요.");
                    return 4;
                }
                var picked = presets[opt.PresetRank - 1];
                ApplyPresetToOptions(opt, picked);
                Console.WriteLine($"[프리셋#{opt.PresetRank} 자동선택] {picked.ProcessName}/{picked.EquipmentName}/" +
                                  $"{picked.UtilityGroup}/{picked.Utility}  size={picked.Size}  " +
                                  $"length={picked.TotalLengthMm:F0}mm  guid={picked.RoutePathGuid}");
            }

            // (B-2) --preset-guid 모드: 지정 GUID 로 프리셋 1건을 불러와 검색 입력으로 사용
            //     CLI 에서 개별 --process / --start 등을 함께 주면 override 됨.
            if (!string.IsNullOrWhiteSpace(opt.PresetGuid))
            {
                var preset = await TopKSearchStandalone.FetchPresetByGuidAsync(db, opt.PresetGuid);
                if (preset is null)
                {
                    Console.Error.WriteLine($"[프리셋 없음] ROUTE_PATH_GUID='{opt.PresetGuid}' 에 해당하는 TB_ROUTE_PATH 행이 없습니다.");
                    return 4;
                }
                ApplyPresetToOptions(opt, preset);
                Console.WriteLine($"[프리셋 적용] {preset.ProcessName}/{preset.EquipmentName}/{preset.UtilityGroup}/{preset.Utility}  " +
                                  $"size={preset.Size}  length={preset.TotalLengthMm:F0}mm  guid={preset.RoutePathGuid}");
            }

            // (C) 일반 검색 모드 — 필수 필드 보강 검증 (프리셋 적용 후에도 비어있으면 오류)
            EnsureSearchFieldsPresent(opt);

            var (results, meta) = await TopKSearchStandalone.SearchAsync(
                db,
                processName:   opt.Process,
                equipmentName: opt.Equipment,
                utilityGroup:  opt.UtilityGroup,
                utility:       opt.Utility,
                startXyz:      opt.Start,
                endXyz:        opt.End,
                k:             opt.K,
                size:          opt.Size,
                queryPattern:  opt.QueryPattern,
                useObstacleContext: opt.UseObstacleContext,
                bay: opt.Bay);

            if (opt.Json) PrintJson(results, meta);
            else          PrintHuman(results, meta);
            return 0;
        }
        catch (NpgsqlException ex)
        {
            Console.Error.WriteLine($"[DB 오류] {ex.Message}");
            return 3;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[ERROR] {ex.Message}");
            return 1;
        }
    }

    /// <summary>프리셋 → CLI 옵션 덮어쓰기. 단 사용자가 명시적으로 값을 준 필드는 존중(override).</summary>
    private static void ApplyPresetToOptions(CliOptions opt, RoutePreset p)
    {
        if (string.IsNullOrEmpty(opt.Process))      opt.Process      = p.ProcessName;
        if (string.IsNullOrEmpty(opt.Equipment))    opt.Equipment    = p.EquipmentName;
        if (string.IsNullOrEmpty(opt.UtilityGroup)) opt.UtilityGroup = p.UtilityGroup;
        if (string.IsNullOrEmpty(opt.Utility))      opt.Utility      = p.Utility;
        if (string.IsNullOrEmpty(opt.Size))         opt.Size         = p.Size;
        if (string.IsNullOrEmpty(opt.Bay))          opt.Bay          = p.Bay;
        if (!opt.StartProvided) { opt.Start = p.StartXyz; opt.StartProvided = true; }
        if (!opt.EndProvided)   { opt.End   = p.EndXyz;   opt.EndProvided   = true; }
    }

    /// <summary>일반 검색 모드 필수 필드 검증. 프리셋 적용 후 호출.</summary>
    private static void EnsureSearchFieldsPresent(CliOptions opt)
    {
        if (string.IsNullOrEmpty(opt.Process))      throw new ArgumentException("--process 또는 --preset-guid 가 필요합니다");
        if (string.IsNullOrEmpty(opt.Equipment))    throw new ArgumentException("--equipment 또는 --preset-guid 가 필요합니다");
        if (string.IsNullOrEmpty(opt.UtilityGroup)) throw new ArgumentException("--utility-group 또는 --preset-guid 가 필요합니다");
        if (string.IsNullOrEmpty(opt.Utility))      throw new ArgumentException("--utility 또는 --preset-guid 가 필요합니다");
        if (!opt.StartProvided)                     throw new ArgumentException("--start 또는 --preset-guid 가 필요합니다");
        if (!opt.EndProvided)                       throw new ArgumentException("--end 또는 --preset-guid 가 필요합니다");
    }

    private sealed class CliOptions
    {
        public string Host = "localhost";
        public int    Port = 5432;
        public string Dbname = "AUTOROUTINGV7";
        public string User = "postgres";
        public string Password = "dinno";

        public string Process      = "";
        public string Equipment    = "";
        public string UtilityGroup = "";
        public string Utility      = "";
        public (double X, double Y, double Z) Start = (0, 0, 0);
        public (double X, double Y, double Z) End   = (0, 0, 0);
        public int K = 5;
        public string Size         = "";
        public string QueryPattern = "";
        public string Bay          = "";
        public bool Json = false;
        /// <summary>--use-obstacle-context : 시작/종료 PoC 주변 장애물(기둥/보) 컨텍스트 벡터(30D)를
        /// 재정렬 4번째 항목(ctxScore)에 반영. TB_ROUTE_CONTEXT_VECTOR(ExtractObstacleContextVector.py) 필요.</summary>
        public bool UseObstacleContext = false;

        // 프리셋 관련 옵션
        public bool   ListPresets   = false;
        public string PresetGuid    = "";
        public int    PresetLimit   = 50;
        /// <summary>--preset-rank N : 현재 필터(--process/--equipment/...)로 프리셋을 조회한 뒤
        /// N번째(1-base) 행을 골라 그 값으로 곧바로 검색한다. 0=비활성. GUID 미리 알 필요 없는 한줄 실행 모드.</summary>
        public int    PresetRank    = 0;
        public bool   StartProvided = false;
        public bool   EndProvided   = false;

        // 스키마 진단 모드
        public bool   CheckSchema   = false;

        // UtilityPipeGroup 검색/테스트
        public string GroupQueryId = "";
        public GroupSizeMatchMode GroupSizeMode = GroupSizeMatchMode.PreferExact;
        public bool GroupRequireSameProcess = false;
        public string GroupEquipmentFamily = "";
        public bool GroupSelfTest = false;
        public bool ListGroupPresets = false;
        public bool GroupSearchByIdentity = false;
        public bool GroupEvaluate = false;
        public int GroupEvaluateSample = 20;
        public string GroupEvaluateOut = "Docs/UtilityPipeGroup_TopK_Phase5_Evaluation.md";
    }

    private static CliOptions ParseArgs(string[] args)
    {
        var o = new CliOptions();
        for (int i = 0; i < args.Length; i++)
        {
            string a = args[i];
            string? Next() => i + 1 < args.Length ? args[++i] : null;
            switch (a)
            {
                case "--host":           o.Host         = Next() ?? throw new ArgumentException("--host requires value"); break;
                case "--port":           o.Port         = int.Parse(Next() ?? throw new ArgumentException("--port requires value")); break;
                case "--dbname":         o.Dbname       = Next() ?? throw new ArgumentException("--dbname requires value"); break;
                case "--user":           o.User         = Next() ?? throw new ArgumentException("--user requires value"); break;
                case "--password":       o.Password     = Next() ?? throw new ArgumentException("--password requires value"); break;
                case "--process":        o.Process      = Next() ?? throw new ArgumentException("--process requires value"); break;
                case "--equipment":      o.Equipment    = Next() ?? throw new ArgumentException("--equipment requires value"); break;
                case "--utility-group":  o.UtilityGroup = Next() ?? throw new ArgumentException("--utility-group requires value"); break;
                case "--utility":        o.Utility      = Next() ?? throw new ArgumentException("--utility requires value"); break;
                case "--start":          o.Start        = ParseXyz(Next() ?? throw new ArgumentException("--start requires value")); o.StartProvided = true; break;
                case "--end":            o.End          = ParseXyz(Next() ?? throw new ArgumentException("--end requires value"));   o.EndProvided   = true; break;
                case "--k":              o.K            = int.Parse(Next() ?? throw new ArgumentException("--k requires value")); break;
                case "--size":           o.Size         = Next() ?? ""; break;
                case "--query-pattern":  o.QueryPattern = Next() ?? ""; break;
                case "--bay":            o.Bay          = Next() ?? throw new ArgumentException("--bay requires value"); break;
                case "--json":           o.Json         = true; break;
                case "--list-presets":   o.ListPresets  = true; break;
                case "--preset-guid":    o.PresetGuid   = Next() ?? throw new ArgumentException("--preset-guid requires value"); break;
                case "--preset-limit":   o.PresetLimit  = int.Parse(Next() ?? throw new ArgumentException("--preset-limit requires value")); break;
                case "--preset-rank":    o.PresetRank   = int.Parse(Next() ?? throw new ArgumentException("--preset-rank requires value")); break;
                case "--check-schema":   o.CheckSchema  = true; break;
                case "--use-obstacle-context": o.UseObstacleContext = true; break;
                case "--group-query-id": o.GroupQueryId = Next() ?? throw new ArgumentException("--group-query-id requires value"); break;
                case "--group-size-mode": o.GroupSizeMode = Enum.Parse<GroupSizeMatchMode>(
                    Next() ?? throw new ArgumentException("--group-size-mode requires value"), true); break;
                case "--group-require-same-process": o.GroupRequireSameProcess = true; break;
                case "--group-equipment-family": o.GroupEquipmentFamily = Next() ?? throw new ArgumentException("--group-equipment-family requires value"); break;
                case "--group-self-test": o.GroupSelfTest = true; break;
                case "--list-group-presets": o.ListGroupPresets = true; break;
                case "--group-search": o.GroupSearchByIdentity = true; break;
                case "--group-evaluate": o.GroupEvaluate = true; break;
                case "--group-evaluate-sample": o.GroupEvaluateSample = int.Parse(
                    Next() ?? throw new ArgumentException("--group-evaluate-sample requires value")); break;
                case "--group-evaluate-out": o.GroupEvaluateOut = Next() ??
                    throw new ArgumentException("--group-evaluate-out requires value"); break;
                default: throw new ArgumentException($"Unknown option: {a}");
            }
        }

        // 프리셋 모드는 별도 경로로 분기 — 필수 검증을 우회.
        //   --list-presets : 필터는 모두 선택(optional)
        //   --preset-guid  : 검색 필드는 Main 단계에서 프리셋으로 채움 → 그 후 EnsureSearchFieldsPresent 실행
        //   일반 모드      : Main 단계의 EnsureSearchFieldsPresent 에서 누락 시 에러
        return o;
    }

    private static (double X, double Y, double Z) ParseXyz(string s)
    {
        var p = s.Split(',', StringSplitOptions.TrimEntries);
        if (p.Length != 3) throw new ArgumentException($"좌표 포맷은 'x,y,z' 이어야 합니다: {s}");
        return (
            double.Parse(p[0], CultureInfo.InvariantCulture),
            double.Parse(p[1], CultureInfo.InvariantCulture),
            double.Parse(p[2], CultureInfo.InvariantCulture));
    }

    private static void PrintHelp()
    {
        Console.WriteLine(@"
TopKSearchStandalone — TB_ROUTE_FEATURE_VECTOR 기반 Top-K 경로 검색 (단일 파일)

[1] 일반 검색:
  dotnet run -- --process <공정> --equipment <장비> \
      --utility-group <그룹> --utility <유틸리티> \
      --start x,y,z --end x,y,z [--k 5] [--size 20A] \
      [--host localhost] [--port 5432] [--dbname AUTOROUTINGV7] \
      [--user postgres] [--password dinno] [--query-pattern H-R-H] \
      [--use-obstacle-context] [--bay ""CMP BAY""] [--json]

  예:
    dotnet run -- --process CMP --equipment kscta01 \
        --utility-group UPW --utility UPW_S \
        --start 12000,8500,3200 --end 14500,10200,3200 --k 5

  --use-obstacle-context : 시작/종료 PoC 주변 장애물(기둥/보) 배치를 30D
    컨텍스트 벡터로 즉석 계산해 재정렬 4번째 항목(ctxScore, 가중치 0.10)에
    반영한다. TB_ROUTE_CONTEXT_VECTOR(Tools/ExtractObstacleContextVector.py 로
    사전 색인 필요) 레코드가 없는 후보는 ctxScore=0으로 처리된다.
    v3는 모든 BAY 라벨을 포함한 전역 좌표 인덱스에서 실제 근접 장애물만 조회한다.
    --bay는 하위 호환 및 진단 출력용이며 필수 입력이 아니다.

[2] TB_ROUTE_PATH 기본 프리셋 사용 (공정/장비/유틸리티/좌표를 한 행에서 일괄 로드):

  (2-a) 프리셋 후보 나열:
    dotnet run -- --list-presets [--process CMP] [--equipment kscta01] \
        [--utility-group UPW] [--utility UPW_S] [--size 20A] \
        [--preset-limit 50] [--json]

  (2-b) 특정 프리셋(ROUTE_PATH_GUID) 으로 검색 실행:
    dotnet run -- --preset-guid <ROUTE_PATH_GUID> [--k 5] [--json]

  (2-c) **한 줄 실행** — 필터에 맞는 N번째 프리셋을 자동 선택해 그대로 검색:
    dotnet run -- --preset-rank 1 [--process CMP] [--equipment kscta01] \
        [--utility-group UPW] [--utility UPW_S] [--size 20A] [--k 5] [--json]

    예) 필터 없이 첫 프리셋 그대로 Top-5:
       dotnet run -- --preset-rank 1 --k 5
    예) CMP 공정 첫 프리셋:
       dotnet run -- --preset-rank 1 --process CMP --k 5
    예) 같은 필터의 2번째 프리셋:
       dotnet run -- --preset-rank 2 --process CMP --k 5

  개별 --process/--equipment/--start 등을 함께 주면 프리셋 값을 override.

[3] 스키마 무결성 진단 (DB 의존 테이블/컬럼/인덱스/벡터차원/E2E):

  dotnet run -- --check-schema \
      [--host localhost] [--port 5432] [--dbname AUTOROUTINGV7] \
      [--user postgres] [--password dinno]

  종료 코드: 0=정상, 5=차단성 이슈 발견.

[4] UtilityPipeGroup Top-K:

  Query 그룹 목록:
    dotnet run -- --list-group-presets [--process CLEAN] [--equipment WTNHJ02] \
      [--utility-group EXHAUST] [--utility ACID] [--preset-limit 100]

  dotnet run -- --group-query-id <GROUP_VECTOR_ID> --k 5 \
      [--group-size-mode PreferExact|ExactOnly|Ignore] \
      [--group-require-same-process] [--group-equipment-family <family>] \
      [--host localhost] [--port 5432] [--dbname DDW_AI_DB] \
      [--user postgres] [--password dinno] [--json]

  Equipment + Utility Group + Utility로 Query 그룹 자동 선택:
    dotnet run -- --group-search --process CVD --equipment TNMHJ04 \
      --utility-group VACCUM --utility FORELINE --k 5 \
      --dbname DDW_AI_DB --user postgres --password dinno

  Matcher golden test(DB 불필요):
    dotnet run -- --group-self-test

  실제 READY 그룹 정량 평가(Size/Context/Arrangement A/B 포함):
    dotnet run -- --group-evaluate --group-evaluate-sample 20 --k 5 \
      --dbname DDW_AI_DB --group-evaluate-out Docs/UtilityPipeGroup_TopK_Phase5_Evaluation.md
");
    }

    private static void PrintGroupResults(
        IReadOnlyList<UtilityPipeGroupSearchResult> results,
        UtilityPipeGroupSearchMeta meta)
    {
        Console.WriteLine($"UtilityPipeGroup Top-K: query={meta.QueryGroupId}");
        Console.WriteLine($"scope={meta.ProjectScopeKey} / {meta.ModelRevisionKey}");
        Console.WriteLine($"candidates={meta.AnnCandidateCount}, returned={meta.ReturnedCount}, " +
                          $"elapsed={meta.SearchTimeMs:F1}ms, size={meta.SizeMatchMode}");
        foreach (var result in results)
        {
            Console.WriteLine($"#{result.Rank} score={result.GroupSimilarity:F6} " +
                              $"matched={result.Matches.Count}/{result.UnmatchedQueryMembers.Count + result.Matches.Count}/" +
                              $"{result.UnmatchedCandidateMembers.Count + result.Matches.Count} " +
                              $"coverage={result.Coverage:F4} arrangement={result.Arrangement:F4} " +
                              $"equipment={result.Candidate.EquipmentInstanceKey}");
            Console.WriteLine($"   {result.Formula}");
        }
    }

    /// <summary>SchemaCheckReport 를 사람이 읽기 좋은 표로 출력. CLI --check-schema 전용.</summary>
    private static void PrintSchemaCheck(SchemaCheckReport rep)
    {
        string OK   = "[OK ]";
        string WARN = "[WARN]";
        string FAIL = "[FAIL]";

        void Section(string t)
        {
            Console.WriteLine();
            Console.WriteLine(new string('=', 72));
            Console.WriteLine(" " + t);
            Console.WriteLine(new string('=', 72));
        }

        Section("1. pgvector 확장");
        Console.WriteLine(rep.PgvectorInstalled
            ? $"  {OK} vector v{rep.PgvectorVersion}"
            : $"  {FAIL} pgvector 미설치");

        Section("2. 필수 테이블 존재");
        Console.WriteLine($"  {(rep.FvTableExists ? OK : FAIL)} TB_ROUTE_FEATURE_VECTOR");
        Console.WriteLine($"  {(rep.RpTableExists ? OK : FAIL)} TB_ROUTE_PATH");

        Section("3. TB_ROUTE_FEATURE_VECTOR 컬럼·타입 점검");
        if (rep.FvMissingColumns.Count == 0)
            Console.WriteLine($"  {OK} 필수 컬럼 모두 존재 ({rep.ExpectedFvColumnCount}개)");
        else
            Console.WriteLine($"  {FAIL} 누락 컬럼: {string.Join(", ", rep.FvMissingColumns)}");
        foreach (var w in rep.FvColumnTypeWarnings) Console.WriteLine($"  {WARN} {w}");

        Section("3. TB_ROUTE_PATH 컬럼·타입 점검");
        if (rep.RpMissingColumns.Count == 0)
            Console.WriteLine($"  {OK} 필수 컬럼 모두 존재 ({rep.ExpectedRpColumnCount}개)");
        else
            Console.WriteLine($"  {FAIL} 누락 컬럼: {string.Join(", ", rep.RpMissingColumns)}");
        foreach (var w in rep.RpColumnTypeWarnings) Console.WriteLine($"  {WARN} {w}");
        Console.WriteLine("  ! 참고: BEND_COUNT 가 double precision 인 환경 → C# 코드는 GetDouble 후 (int)Math.Round 캐스팅 (known)");

        Section("4. 인덱스 (HNSW · 보조)");
        foreach (var (tbl, name, defn, isHnsw) in rep.Indexes)
        {
            string flag = isHnsw ? "HNSW"
                        : defn.IndexOf("UNIQUE", StringComparison.OrdinalIgnoreCase) >= 0 ? "UNIQUE" : "    ";
            Console.WriteLine($"  [{flag}] {tbl}.{name}");
            if (isHnsw) Console.WriteLine($"          {defn}");
        }
        if (!rep.HasHnswIndex)
            Console.WriteLine($"  {WARN} HNSW 인덱스 없음 — Top-K ANN 성능 저하");

        Section("5. 데이터 행 수");
        Console.WriteLine($"  {(rep.FvRowCount > 0 ? OK : WARN)} TB_ROUTE_FEATURE_VECTOR: {rep.FvRowCount:N0} 건");
        Console.WriteLine($"  {(rep.RpRowCount > 0 ? OK : WARN)} TB_ROUTE_PATH: {rep.RpRowCount:N0} 건");

        Section("6. TB_ROUTE_FEATURE_VECTOR NULL/빈값 점검");
        foreach (var kv in rep.FvNullCounts)
        {
            string tag = kv.Value == 0 ? OK
                       : (kv.Key.Contains("FEATURE_VECTOR") || kv.Key.Contains("ROUTE_PATH_GUID")) ? FAIL : WARN;
            Console.WriteLine($"  {tag} {kv.Key}: {kv.Value:N0}");
        }

        Section("7. TB_ROUTE_PATH 좌표·꺾임 NULL 점검");
        foreach (var kv in rep.RpNullCounts)
            Console.WriteLine($"  {(kv.Value == 0 ? OK : WARN)} {kv.Key}: {kv.Value:N0}");

        Section($"8. FEATURE_VECTOR 벡터 차원 분포 (가정: {TopKSearchStandalone.VECTOR_DIM}D)");
        foreach (var kv in rep.VectorDimDistribution)
            Console.WriteLine($"  {(kv.Key == TopKSearchStandalone.VECTOR_DIM ? OK : FAIL)} dim={kv.Key}: {kv.Value:N0} 건");

        Section("9. End-to-End Smoke");
        foreach (var s in rep.EndToEndOk)  Console.WriteLine($"  {OK} {s}");
        foreach (var s in rep.EndToEndErr) Console.WriteLine($"  {FAIL} {s}");

        Section("종합");
        if (rep.IsHealthy && rep.Warnings.Count == 0)
        {
            Console.WriteLine($"  {OK} 모든 점검 통과 — TopKSearchStandalone 실행에 스키마상 문제 없음.");
        }
        else
        {
            if (rep.Failures.Count > 0)
            {
                Console.WriteLine($"  {FAIL} 차단성 이슈 {rep.Failures.Count}건:");
                foreach (var m in rep.Failures) Console.WriteLine($"    · {m}");
            }
            if (rep.Warnings.Count > 0)
            {
                Console.WriteLine($"  {WARN} 권고 이슈 {rep.Warnings.Count}건:");
                foreach (var m in rep.Warnings) Console.WriteLine($"    · {m}");
            }
        }
    }

    /// <summary>프리셋 목록을 사람이 읽기 좋은 표로 출력.</summary>
    private static void PrintPresetsHuman(List<RoutePreset> presets, CliOptions opt)
    {
        Console.WriteLine();
        Console.WriteLine($"[프리셋 후보: TB_ROUTE_PATH] {presets.Count}건 (limit={opt.PresetLimit})");
        var filters = new List<string>();
        if (!string.IsNullOrEmpty(opt.Process))      filters.Add($"process={opt.Process}");
        if (!string.IsNullOrEmpty(opt.Equipment))    filters.Add($"equipment={opt.Equipment}");
        if (!string.IsNullOrEmpty(opt.UtilityGroup)) filters.Add($"utility_group={opt.UtilityGroup}");
        if (!string.IsNullOrEmpty(opt.Utility))      filters.Add($"utility={opt.Utility}");
        if (!string.IsNullOrEmpty(opt.Size))         filters.Add($"size={opt.Size}");
        Console.WriteLine($"필터: {(filters.Count > 0 ? string.Join(", ", filters) : "(없음)")}");

        if (presets.Count == 0)
        {
            Console.WriteLine("\n(조건에 맞는 TB_ROUTE_PATH 행이 없습니다)");
            return;
        }

        Console.WriteLine();
        Console.WriteLine(
            $"{"Idx",3}  {"GUID",-12}  {"Process",-10}  {"Equipment",-16}  {"UG",-8}  {"Utility",-10}  " +
            $"{"Size",-6}  {"Bay",-12}  {"Start(x,y,z)",-28}  {"End(x,y,z)",-28}  {"Len(mm)",8}");
        Console.WriteLine(new string('-', 150));

        int idx = 1;
        foreach (var p in presets)
        {
            string guid = p.RoutePathGuid.Length > 10 ? p.RoutePathGuid[..10] + "…" : p.RoutePathGuid;
            string proc = Truncate(p.ProcessName,   10);
            string equ  = Truncate(p.EquipmentName, 16);
            string ug   = Truncate(p.UtilityGroup,   8);
            string util = Truncate(p.Utility,       10);
            string sz   = Truncate(p.Size,           6);
            string s    = $"({p.StartXyz.X:F0},{p.StartXyz.Y:F0},{p.StartXyz.Z:F0})";
            string e    = $"({p.EndXyz.X:F0},{p.EndXyz.Y:F0},{p.EndXyz.Z:F0})";
            Console.WriteLine(
                $"{idx++,3}  {guid,-12}  {proc,-10}  {equ,-16}  {ug,-8}  {util,-10}  " +
                $"{sz,-6}  {Truncate(p.Bay, 12),-12}  {s,-28}  {e,-28}  {p.TotalLengthMm,8:F0}");
        }

        Console.WriteLine();
        Console.WriteLine("사용 예: dotnet run -- --preset-guid <위 GUID 전체> --k 5");
    }

    /// <summary>프리셋 목록 JSON 출력.</summary>
    private static void PrintPresetsJson(List<RoutePreset> presets, CliOptions opt)
    {
        var sb = new StringBuilder();
        sb.Append("{\n  \"count\": ").Append(presets.Count).Append(",\n");
        sb.Append("  \"limit\": ").Append(opt.PresetLimit).Append(",\n");
        sb.Append("  \"filters\": {");
        var filters = new List<string>
        {
            $"\"process_name\":\"{JsonEscape(opt.Process)}\"",
            $"\"equipment_name\":\"{JsonEscape(opt.Equipment)}\"",
            $"\"utility_group\":\"{JsonEscape(opt.UtilityGroup)}\"",
            $"\"utility\":\"{JsonEscape(opt.Utility)}\"",
            $"\"size\":\"{JsonEscape(opt.Size)}\"",
        };
        sb.Append(string.Join(", ", filters));
        sb.Append("},\n  \"presets\": [\n");
        for (int i = 0; i < presets.Count; i++)
        {
            var p = presets[i];
            sb.Append("    {");
            sb.Append($"\"route_path_guid\":\"{JsonEscape(p.RoutePathGuid)}\",");
            sb.Append($"\"process_name\":\"{JsonEscape(p.ProcessName)}\",");
            sb.Append($"\"equipment_name\":\"{JsonEscape(p.EquipmentName)}\",");
            sb.Append($"\"utility_group\":\"{JsonEscape(p.UtilityGroup)}\",");
            sb.Append($"\"utility\":\"{JsonEscape(p.Utility)}\",");
            sb.Append($"\"size\":\"{JsonEscape(p.Size)}\",");
            sb.Append($"\"bay\":\"{JsonEscape(p.Bay)}\",");
            sb.Append($"\"start_xyz\":[{p.StartXyz.X.ToString("F2", CultureInfo.InvariantCulture)},{p.StartXyz.Y.ToString("F2", CultureInfo.InvariantCulture)},{p.StartXyz.Z.ToString("F2", CultureInfo.InvariantCulture)}],");
            sb.Append($"\"end_xyz\":[{p.EndXyz.X.ToString("F2", CultureInfo.InvariantCulture)},{p.EndXyz.Y.ToString("F2", CultureInfo.InvariantCulture)},{p.EndXyz.Z.ToString("F2", CultureInfo.InvariantCulture)}],");
            sb.Append($"\"target_owner_name\":\"{JsonEscape(p.TargetOwnerName)}\",");
            sb.Append($"\"total_length_mm\":{p.TotalLengthMm.ToString("F2", CultureInfo.InvariantCulture)},");
            sb.Append($"\"bend_count\":{p.BendCount}");
            sb.Append('}');
            if (i < presets.Count - 1) sb.Append(',');
            sb.Append('\n');
        }
        sb.Append("  ]\n}");
        Console.WriteLine(sb.ToString());
    }

    private static void PrintHuman(List<SearchResult> results, SearchMeta meta)
    {
        Console.WriteLine();
        Console.WriteLine($"검색 시간: {meta.SearchTimeMs:F1} ms  | " +
                          $"후보 수집: {meta.TotalCandidates}건 (fetch_n={meta.FetchN})");
        Console.WriteLine($"필터: {string.Join(", ", meta.FiltersApplied.Select(kv => $"{kv.Key}={kv.Value}"))}" +
                          (meta.UsedObstacleContext
                              ? $"  | 장애물 컨텍스트(ctxScore) 반영됨, 후보 커버리지={meta.ContextCandidates}/{meta.TotalCandidates} ({meta.ContextCoverage:P1}), fallback={meta.ContextFallbackCandidates}"
                              : ""));
        Console.WriteLine($"재정렬 계약: {meta.RerankWeightProfile}");
        if (results.Count == 0)
        {
            Console.WriteLine("\n(결과 없음 — 필터 조건과 일치하는 경로가 DB에 없습니다)");
            return;
        }

        Console.WriteLine();
        if (meta.UsedObstacleContext)
        {
            Console.WriteLine($"{"Rank",4}  {"Score",6}  {"Pos",5}  {"Pat",5}  {"Vec",5}  {"Ctx",5}  " +
                              $"{"Equip",-18}  {"Utility",-10}  {"Size",-6}  {"Length(mm)",10}  " +
                              $"{"Pattern",-18}  GUID");
            Console.WriteLine(new string('-', 128));
        }
        else
        {
            Console.WriteLine($"{"Rank",4}  {"Score",6}  {"Pos",5}  {"Pat",5}  {"Vec",5}  " +
                              $"{"Equip",-18}  {"Utility",-10}  {"Size",-6}  {"Length(mm)",10}  " +
                              $"{"Pattern",-18}  GUID");
            Console.WriteLine(new string('-', 120));
        }
        foreach (var r in results)
        {
            string pat  = Truncate(r.DirectionPattern, 18);
            string equ  = Truncate(r.EquipmentName, 18);
            string util = Truncate(r.Utility, 10);
            string sz   = Truncate(r.Size, 6);
            string guid = r.RoutePathGuid.Length > 8 ? r.RoutePathGuid[..8] + "…" : r.RoutePathGuid;
            string ctxCol = meta.UsedObstacleContext ? $"{r.ScoreContext,5:F2}  " : "";
            Console.WriteLine(
                $"{r.Rank,4}  {r.SimilarityScore,6:F3}  {r.ScorePosition,5:F2}  " +
                $"{r.ScorePattern,5:F2}  {r.ScoreVector,5:F2}  {ctxCol}" +
                $"{equ,-18}  {util,-10}  {sz,-6}  {r.TotalLengthMm,10:F0}  " +
                $"{pat,-18}  {guid}");
        }
    }

    private static void PrintJson(List<SearchResult> results, SearchMeta meta)
    {
        var sb = new StringBuilder();
        sb.Append("{\n  \"meta\": {\n");
        sb.Append($"    \"search_time_ms\": {meta.SearchTimeMs.ToString("F2", CultureInfo.InvariantCulture)},\n");
        sb.Append($"    \"total_candidates\": {meta.TotalCandidates},\n");
        sb.Append($"    \"fetch_n\": {meta.FetchN},\n");
        sb.Append("    \"filters_applied\": {");
        sb.Append(string.Join(", ", meta.FiltersApplied.Select(kv => $"\"{kv.Key}\": \"{JsonEscape(kv.Value)}\"")));
        sb.Append("},\n");
        sb.Append("    \"query_vector_head\": [");
        sb.Append(string.Join(", ", meta.QueryVectorHead.Select(v => v.ToString("G", CultureInfo.InvariantCulture))));
        sb.Append("],\n");
        sb.Append("    \"query_context_vector_head\": [");
        sb.Append(string.Join(", ", meta.QueryContextVectorHead.Select(v => v.ToString("G", CultureInfo.InvariantCulture))));
        sb.Append("],\n");
        sb.Append($"    \"used_obstacle_context\": {(meta.UsedObstacleContext ? "true" : "false")},\n");
        sb.Append($"    \"context_candidates\": {meta.ContextCandidates},\n");
        sb.Append($"    \"context_fallback_candidates\": {meta.ContextFallbackCandidates},\n");
        sb.Append($"    \"context_coverage\": {meta.ContextCoverage.ToString("F6", CultureInfo.InvariantCulture)},\n");
        sb.Append($"    \"rerank_weight_profile\": \"{JsonEscape(meta.RerankWeightProfile)}\"\n");
        sb.Append("  },\n  \"results\": [\n");
        for (int i = 0; i < results.Count; i++)
        {
            var r = results[i];
            sb.Append("    {");
            sb.Append($"\"rank\":{r.Rank},");
            sb.Append($"\"route_path_guid\":\"{JsonEscape(r.RoutePathGuid)}\",");
            sb.Append($"\"process_name\":\"{JsonEscape(r.ProcessName)}\",");
            sb.Append($"\"equipment_name\":\"{JsonEscape(r.EquipmentName)}\",");
            sb.Append($"\"utility_group\":\"{JsonEscape(r.UtilityGroup)}\",");
            sb.Append($"\"utility\":\"{JsonEscape(r.Utility)}\",");
            sb.Append($"\"size\":\"{JsonEscape(r.Size)}\",");
            sb.Append($"\"direction_pattern\":\"{JsonEscape(r.DirectionPattern)}\",");
            sb.Append($"\"total_length_mm\":{r.TotalLengthMm.ToString("F2", CultureInfo.InvariantCulture)},");
            sb.Append($"\"step_count\":{r.StepCount},");
            sb.Append($"\"start_xyz\":[{r.StartXyz.X.ToString("F2", CultureInfo.InvariantCulture)},{r.StartXyz.Y.ToString("F2", CultureInfo.InvariantCulture)},{r.StartXyz.Z.ToString("F2", CultureInfo.InvariantCulture)}],");
            sb.Append($"\"end_xyz\":[{r.EndXyz.X.ToString("F2", CultureInfo.InvariantCulture)},{r.EndXyz.Y.ToString("F2", CultureInfo.InvariantCulture)},{r.EndXyz.Z.ToString("F2", CultureInfo.InvariantCulture)}],");
            sb.Append($"\"cosine_distance\":{r.CosineDistance.ToString("G", CultureInfo.InvariantCulture)},");
            sb.Append($"\"score_position\":{r.ScorePosition.ToString("F4", CultureInfo.InvariantCulture)},");
            sb.Append($"\"score_pattern\":{r.ScorePattern.ToString("F4", CultureInfo.InvariantCulture)},");
            sb.Append($"\"score_vector\":{r.ScoreVector.ToString("F4", CultureInfo.InvariantCulture)},");
            sb.Append($"\"score_context\":{r.ScoreContext.ToString("F4", CultureInfo.InvariantCulture)},");
            sb.Append($"\"similarity_score\":{r.SimilarityScore.ToString("F4", CultureInfo.InvariantCulture)}");
            sb.Append('}');
            if (i < results.Count - 1) sb.Append(',');
            sb.Append('\n');
        }
        sb.Append("  ]\n}");
        Console.WriteLine(sb.ToString());
    }

    private static string Truncate(string s, int max)
        => s.Length > max ? s[..max] : s;

    private static string JsonEscape(string s)
        => s.Replace("\\", "\\\\").Replace("\"", "\\\"");
}
