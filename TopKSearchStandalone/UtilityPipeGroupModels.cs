using System.Text.Json;
using System.Text.Json.Serialization;

namespace RoutingAI.Standalone;

/// <summary>그룹 멤버 Size 대응 정책.</summary>
public enum GroupSizeMatchMode
{
    PreferExact,
    ExactOnly,
    Ignore
}

/// <summary>UtilityPipeGroup Top-K 검색 옵션.</summary>
public sealed record UtilityPipeGroupSearchOptions
{
    public int K { get; init; } = 5;
    public GroupSizeMatchMode SizeMatchMode { get; init; } = GroupSizeMatchMode.PreferExact;
    public RerankWeights PairWeights { get; init; } = new(25, 25, 25, 25);
    public double MatchedWeight { get; init; } = 0.80;
    public double ArrangementWeight { get; init; } = 0.20;
    public int CandidateFetchMultiplier { get; init; } = 20;
    public int CandidateFetchMinimum { get; init; } = 100;
    public int CandidateFetchMaximum { get; init; } = 1000;
    public bool RequireSameProcess { get; init; }
    public string EquipmentFamilyKey { get; init; } = "";
}

/// <summary>Tools/ExtractBendFeaturePoints.py가 TB_ROUTE_BEND_FEATURE_POINT에 적재한 개별 꺾임점 요약.
/// Docs/BendFeaturePoint_Development_Plan.md 7~8절 산출물을 Pair 매칭에 재사용하기 위한 최소 필드셋이다.</summary>
public sealed record BendFeaturePointSummary(
    int OrdinalFromStart,
    string SegmentZone,
    double RelPositionBucket,
    string TransitionType,
    string Cause);

/// <summary>그룹에 속한 한 배관과 Pair 점수 계산에 필요한 원본 Vector.</summary>
public sealed record UtilityPipeGroupMember(
    string RoutePathGuid,
    int MemberOrder,
    string Utility,
    string Size,
    [property: JsonIgnore] (double X, double Y, double Z) StartXyz,
    [property: JsonIgnore] (double X, double Y, double Z) EndXyz,
    string DirectionPattern,
    double TotalLengthMm,
    int StepCount,
    [property: JsonIgnore] double[] FeatureVector,
    [property: JsonIgnore] double[]? ContextVector,
    string FeatureProvenance,
    string ContextProvenance,
    [property: JsonIgnore] IReadOnlyList<BendFeaturePointSummary> BendPoints)
{
    public double StartX => StartXyz.X;
    public double StartY => StartXyz.Y;
    public double StartZ => StartXyz.Z;
    public double EndX => EndXyz.X;
    public double EndY => EndXyz.Y;
    public double EndZ => EndXyz.Z;
    public int BendPointCount => BendPoints.Count;
}

/// <summary>DB 그룹 header와 멤버 전체.</summary>
public sealed record UtilityPipeGroupDescriptor(
    string GroupVectorId,
    string ProjectScopeKey,
    string ModelRevisionKey,
    string ProcessName,
    string EquipmentInstanceKey,
    string EquipmentName,
    string EquipmentFamilyKey,
    string UtilityGroup,
    string Utility,
    int MemberCount,
    IReadOnlyDictionary<string, int> SizeSignature,
    [property: JsonIgnore] double[] FeatureCentroid,
    [property: JsonIgnore] double[]? ContextCentroid,
    [property: JsonIgnore] JsonElement Arrangement,
    double FeatureCoverage,
    double ContextCoverage,
    string SourceHash,
    [property: JsonIgnore] IReadOnlyList<UtilityPipeGroupMember> Members,
    double AnnCosineDistance = 0.0);

/// <summary>한 Query/Candidate 멤버 Pair의 원점수, 정규화 가중치와 최종 기여도.</summary>
public sealed record UtilityPipePairScore(
    double Position,
    double Pattern,
    double Feature,
    double Context,
    double WeightPosition,
    double WeightPattern,
    double WeightFeature,
    double WeightContext,
    double ContributionPosition,
    double ContributionPattern,
    double ContributionFeature,
    double ContributionContext,
    double BaseSimilarity,
    double SizeScore,
    double AdjustedSimilarity,
    bool SizeCompatible,
    bool ContextAvailable);

/// <summary>Hungarian Algorithm이 선택한 멤버 1:1 대응.</summary>
public sealed record UtilityPipeGroupMemberMatch(
    UtilityPipeGroupMember Query,
    UtilityPipeGroupMember Candidate,
    UtilityPipePairScore Score);

/// <summary>Top-K 후보 그룹 한 건과 계산식 진단.</summary>
public sealed record UtilityPipeGroupSearchResult(
    int Rank,
    UtilityPipeGroupDescriptor Candidate,
    double GroupSimilarity,
    double MatchedAverage,
    double Coverage,
    double Arrangement,
    double MatchedWeight,
    double ArrangementWeight,
    double MatchedContribution,
    double ArrangementContribution,
    IReadOnlyList<UtilityPipeGroupMemberMatch> Matches,
    IReadOnlyList<UtilityPipeGroupMember> UnmatchedQueryMembers,
    IReadOnlyList<UtilityPipeGroupMember> UnmatchedCandidateMembers)
{
    public string Formula =>
        $"(({MatchedAverage:F6} × {MatchedWeight:F6}) + " +
        $"({Arrangement:F6} × {ArrangementWeight:F6})) × {Coverage:F6} = {GroupSimilarity:F6}";
}

/// <summary>그룹 검색 실행 진단.</summary>
public sealed record UtilityPipeGroupSearchMeta(
    string QueryGroupId,
    string ProjectScopeKey,
    string ModelRevisionKey,
    int RequestedK,
    int FetchN,
    int AnnCandidateCount,
    int ReturnedCount,
    double SearchTimeMs,
    GroupSizeMatchMode SizeMatchMode,
    string PairWeightProfile,
    double MatchedWeight,
    double ArrangementWeight,
    IReadOnlyDictionary<string, string> FiltersApplied);

/// <summary>Viewer/CLI가 Query 그룹을 선택할 때 사용하는 간단한 프리셋.</summary>
public sealed record UtilityPipeGroupPreset(
    string GroupVectorId,
    string ProcessName,
    string EquipmentInstanceKey,
    string EquipmentName,
    string UtilityGroup,
    string Utility,
    int MemberCount,
    IReadOnlyDictionary<string, int> SizeSignature,
    string Display);
