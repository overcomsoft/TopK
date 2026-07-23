using System.Text.Json;

namespace RoutingAI.Standalone;

/// <summary>외부 테스트 패키지 없이 실행 가능한 그룹 Matcher golden tests.</summary>
public static class UtilityPipeGroupMatcherSelfTests
{
    public static int RunAll(TextWriter? output = null)
    {
        output ??= Console.Out;
        var tests = new (string Name, Action Test)[]
        {
            ("hungarian_cross_optimum", HungarianCrossOptimum),
            ("exact_size_rejects_mismatch", ExactSizeRejectsMismatch),
            ("missing_context_redistributes_weight", MissingContextRedistributesWeight),
            ("coverage_and_formula_golden", CoverageAndFormulaGolden),
            ("member_order_invariance", MemberOrderInvariance),
            ("translated_arrangement_is_equal", TranslatedArrangementIsEqual),
            ("bend_feature_match_identical_sequence_is_one", BendFeatureMatchIdenticalSequenceIsOne),
            ("bend_feature_match_penalizes_cause_mismatch_less_than_full_mismatch", BendFeatureMatchPenalizesCauseMismatch),
            ("bend_feature_available_changes_pattern_score", BendFeatureAvailableChangesPatternScore),
        };
        foreach (var (name, test) in tests)
        {
            test();
            output.WriteLine($"[PASS] {name}");
        }
        output.WriteLine($"UtilityPipeGroup matcher self-tests: {tests.Length} passed");
        return 0;
    }

    private static void HungarianCrossOptimum()
    {
        var weights = new[,] { { 0.80, 0.90 }, { 0.95, 0.10 } };
        var assignment = UtilityPipeGroupMatcher.MaximumWeightAssignment(weights);
        Equal(1, assignment[0], "row0");
        Equal(0, assignment[1], "row1");
    }

    private static void ExactSizeRejectsMismatch()
    {
        var (compatible, score) = UtilityPipeGroupMatcher.SizeCompatibility("50A", "65A", GroupSizeMatchMode.ExactOnly);
        True(!compatible, "ExactOnly compatible");
        Near(0.0, score, "ExactOnly score");
        var adjacent = UtilityPipeGroupMatcher.SizeCompatibility("50A", "65A", GroupSizeMatchMode.PreferExact);
        True(adjacent.Compatible, "PreferExact compatible");
        Near(0.80, adjacent.Score, "PreferExact adjacent score");
    }

    private static void MissingContextRedistributesWeight()
    {
        var query = Member("q", "50A", 0);
        var candidate = Member("c", "50A", 0, withContext: false);
        var score = UtilityPipeGroupMatcher.ScorePair(query, candidate, GroupSizeMatchMode.PreferExact,
            new RerankWeights(0, 0, 50, 50));
        Near(1.0, score.WeightFeature, "Feature redistributed weight");
        Near(0.0, score.WeightContext, "Context disabled weight");
        Near(1.0, score.AdjustedSimilarity, "redistributed similarity");
    }

    private static void CoverageAndFormulaGolden()
    {
        var arrangement = ArrangementJson(offset: 0);
        var query = Group("query", arrangement, [Member("q1", "50A", 0), Member("q2", "50A", 10)]);
        var candidate = Group("candidate", arrangement, [Member("c1", "50A", 0)]);
        var result = UtilityPipeGroupMatcher.ScoreGroup(query, candidate,
            new UtilityPipeGroupSearchOptions
            {
                PairWeights = new RerankWeights(0, 0, 1, 0),
                MatchedWeight = 0.8,
                ArrangementWeight = 0.2,
            });
        Equal(1, result.Matches.Count, "match count");
        Near(2.0 / 3.0, result.Coverage, "coverage");
        Near(1.0, result.MatchedAverage, "matched average");
        Near(1.0, result.Arrangement, "arrangement");
        Near(2.0 / 3.0, result.GroupSimilarity, "group similarity");
    }

    private static void MemberOrderInvariance()
    {
        var arrangement = ArrangementJson(0);
        var query = Group("query", arrangement, [Member("q2", "50A", 10), Member("q1", "50A", 0)]);
        var candidateA = Group("candidate", arrangement, [Member("c2", "50A", 10), Member("c1", "50A", 0)]);
        var candidateB = candidateA with { Members = candidateA.Members.Reverse().ToArray() };
        var options = new UtilityPipeGroupSearchOptions { PairWeights = new RerankWeights(0, 0, 1, 0) };
        var left = UtilityPipeGroupMatcher.ScoreGroup(query, candidateA, options);
        var right = UtilityPipeGroupMatcher.ScoreGroup(query, candidateB, options);
        Near(left.GroupSimilarity, right.GroupSimilarity, "order invariant similarity");
        Equal(string.Join("|", left.Matches.Select(match => match.Query.RoutePathGuid + ">" + match.Candidate.RoutePathGuid)),
            string.Join("|", right.Matches.Select(match => match.Query.RoutePathGuid + ">" + match.Candidate.RoutePathGuid)),
            "order invariant matching");
    }

    private static void TranslatedArrangementIsEqual() =>
        Near(1.0, UtilityPipeGroupMatcher.ArrangementSimilarity(ArrangementJson(0), ArrangementJson(100_000)),
            "translation independent arrangement");

    private static void BendFeatureMatchIdenticalSequenceIsOne()
    {
        var sequence = new[]
        {
            new BendFeaturePointSummary(1, "START_STUB", 0.5, "H_TO_V", "DESTINATION_ENTRY"),
            new BendFeaturePointSummary(2, "MIDDLE_TRUNK", 0.3, "H_TO_H", "OBSTACLE_AVOID"),
        };
        Near(1.0, UtilityPipeGroupMatcher.BendFeatureMatch(sequence, sequence), "identical bend sequence");
    }

    private static void BendFeatureMatchPenalizesCauseMismatch()
    {
        var left = new[] { new BendFeaturePointSummary(1, "MIDDLE_TRUNK", 0.5, "H_TO_H", "OBSTACLE_AVOID") };
        var sameZoneDifferentCause = new[] { new BendFeaturePointSummary(1, "MIDDLE_TRUNK", 0.5, "H_TO_H", "GROUP_ALIGNMENT") };
        var fullyDifferent = new[] { new BendFeaturePointSummary(1, "END_STUB", 0.5, "V_TO_H", "DESTINATION_ENTRY") };
        var causeMismatchScore = UtilityPipeGroupMatcher.BendFeatureMatch(left, sameZoneDifferentCause);
        var fullMismatchScore = UtilityPipeGroupMatcher.BendFeatureMatch(left, fullyDifferent);
        Near(0.6, causeMismatchScore, "zone+transition match, cause mismatch");
        Near(0.0, fullMismatchScore, "fully mismatched single-point sequence");
        True(causeMismatchScore > fullMismatchScore, "cause-only mismatch should score above total mismatch");
    }

    private static void BendFeatureAvailableChangesPatternScore()
    {
        var bends = new[] { new BendFeaturePointSummary(1, "MIDDLE_TRUNK", 0.5, "H_TO_H", "OBSTACLE_AVOID") };
        var query = Member("q", "50A", 0, bendPoints: bends);
        var candidateWithBends = Member("c1", "50A", 0, bendPoints: bends);
        var candidateWithoutBends = Member("c2", "50A", 0);
        var weights = new RerankWeights(0, 1, 0, 0);
        var withBendScore = UtilityPipeGroupMatcher.ScorePair(query, candidateWithBends, GroupSizeMatchMode.Ignore, weights);
        var withoutBendScore = UtilityPipeGroupMatcher.ScorePair(query, candidateWithoutBends, GroupSizeMatchMode.Ignore, weights);
        // Member()의 FeatureVector는 axis 0만 1인 단위벡터라 Feature[12:21] coarse bend slice는
        // 항상 영벡터(coarse bend cosine=0)다. 이 경우 cause-aware bendFeature(=1.0, 완전 일치)가
        // 섞이면 0.34*1.0 + 0.33*0.0 + 0.33*1.0 = 0.67로, coarse-only fallback(0.5*1.0+0.5*0.0=0.5)보다
        // 높아져야 한다 — 즉 개별 꺾임점 데이터가 있을 때 정보가 더 정확하게 반영됨을 검증한다.
        Near(0.67, withBendScore.Pattern, "bend-aware pattern blends structural+coarse+bendFeature");
        Near(0.5, withoutBendScore.Pattern, "fallback pattern stays structural+coarse 50:50");
        True(withBendScore.Pattern > withoutBendScore.Pattern, "bend-aware pattern should score above coarse-only fallback here");
    }

    private static UtilityPipeGroupMember Member(
        string guid, string size, double x, bool withContext = true,
        IReadOnlyList<BendFeaturePointSummary>? bendPoints = null) => new(
        guid, 0, "ACID", size, (x, 0, 0), (x, 100, 50), "H-R-D", 150, 3,
        Vector(0), withContext ? Vector(2) : null, "feature", withContext ? "context" : "",
        bendPoints ?? Array.Empty<BendFeaturePointSummary>());

    private static UtilityPipeGroupDescriptor Group(string id, JsonElement arrangement,
        IReadOnlyList<UtilityPipeGroupMember> members) => new(
        id, "DB:P", "snapshot:R", "CLEAN", id, "FAMILY", "FAMILY", "EXHAUST", "ACID",
        members.Count, new Dictionary<string, int> { ["50A"] = members.Count }, Vector(0), Vector(2),
        arrangement, 1, 1, "hash", members);

    private static double[] Vector(int axis)
    {
        var result = new double[30];
        result[axis] = 1;
        return result;
    }

    private static JsonElement ArrangementJson(double offset)
    {
        var json = $$"""
        {
          "start":{"mean":[{{offset}},0,0],"std":[10,20,30]},
          "end":{"mean":[{{offset}},100,50],"std":[10,20,30]},
          "displacement":{"mean":[0,100,50],"std":[1,2,3]},
          "start_pairwise_distance_mm":{"mean":100,"std":5,"min":90,"max":110},
          "end_pairwise_distance_mm":{"mean":100,"std":5,"min":90,"max":110},
          "length_mm":{"mean":150,"std":10},"step_count":{"mean":3,"std":1},
          "aabb":{"size":[100,200,50]}
        }
        """;
        return JsonDocument.Parse(json).RootElement.Clone();
    }

    private static void Near(double expected, double actual, string label)
    {
        if (Math.Abs(expected - actual) > 1e-9)
            throw new InvalidOperationException($"{label}: expected={expected}, actual={actual}");
    }
    private static void True(bool condition, string label)
    {
        if (!condition) throw new InvalidOperationException(label);
    }
    private static void Equal<T>(T expected, T actual, string label)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
            throw new InvalidOperationException($"{label}: expected={expected}, actual={actual}");
    }
}
