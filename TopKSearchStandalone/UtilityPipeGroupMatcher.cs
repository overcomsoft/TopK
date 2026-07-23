namespace RoutingAI.Standalone;

/// <summary>
/// UtilityPipeGroup 멤버 Pair 점수, Size 호환, Hungarian 1:1 대응과 그룹 최종 점수를 계산한다.
/// DB와 무관한 순수 알고리즘이므로 단위 테스트와 Viewer에서 동일하게 재사용한다.
/// </summary>
public static class UtilityPipeGroupMatcher
{
    private const double RelativeDistanceMaxMm = 50_000.0;

    /// <summary>bendFeature 데이터가 있을 때 Pattern 내부 3분할 비중. 합계 1.0.</summary>
    private static class BendFeatureShare
    {
        public const double Structural = 0.34;
        public const double CoarseBend = 0.33;
        public const double BendFeature = 0.33;
    }

    private static readonly double[] StandardNominalSizes =
    [6, 8, 10, 15, 20, 25, 32, 40, 50, 65, 80, 100, 125, 150, 200, 250, 300, 350, 400, 450, 500, 600, 700, 800, 900, 1000];

    public static UtilityPipePairScore ScorePair(
        UtilityPipeGroupMember query,
        UtilityPipeGroupMember candidate,
        GroupSizeMatchMode sizeMode,
        RerankWeights requestedWeights)
    {
        ValidateWeights(requestedWeights);
        var qDelta = Delta(query);
        var cDelta = Delta(candidate);
        var relativeDistance = Distance(qDelta, cDelta);
        var position = Clamp01(1.0 - relativeDistance / RelativeDistanceMaxMm);

        var patternAvailable = !string.IsNullOrWhiteSpace(query.DirectionPattern)
            && !string.IsNullOrWhiteSpace(candidate.DirectionPattern);
        var structural = patternAvailable
            ? PatternSimilarity(query.DirectionPattern, candidate.DirectionPattern)
            : 0.0;
        var bend = query.FeatureVector.Length >= 21 && candidate.FeatureVector.Length >= 21
            ? Clamp01(Cosine(query.FeatureVector.AsSpan(12, 9), candidate.FeatureVector.AsSpan(12, 9)))
            : 0.0;
        // TB_ROUTE_BEND_FEATURE_POINT(원인 분류가 붙은 개별 꺾임점)이 두 멤버 모두에 존재하면,
        // Feature[12:20] coarse cosine보다 정밀한 cause-aware 시퀀스 매칭을 Pattern에 섞는다.
        // 없는 멤버(과거 build 미실행 등)는 기존 structural/bend 50:50 배합으로 그대로 fallback한다.
        var bendFeatureAvailable = query.BendPoints.Count > 0 && candidate.BendPoints.Count > 0;
        var bendFeature = bendFeatureAvailable
            ? BendFeatureMatch(query.BendPoints, candidate.BendPoints)
            : 0.0;
        var pattern = !patternAvailable
            ? 0.0
            : bendFeatureAvailable
                ? BendFeatureShare.Structural * structural + BendFeatureShare.CoarseBend * bend + BendFeatureShare.BendFeature * bendFeature
                : 0.5 * structural + 0.5 * bend;
        var feature = Clamp01(Cosine(query.FeatureVector, candidate.FeatureVector));
        var contextAvailable = query.ContextVector is not null && candidate.ContextVector is not null;
        var context = contextAvailable
            ? Clamp01(Cosine(query.ContextVector!, candidate.ContextVector!))
            : 0.0;

        var wPosition = requestedWeights.Position;
        var wPattern = patternAvailable ? requestedWeights.Pattern : 0.0;
        var wFeature = requestedWeights.Vector;
        var wContext = contextAvailable ? requestedWeights.Context : 0.0;
        var active = wPosition + wPattern + wFeature + wContext;
        if (active <= 0.0)
            throw new InvalidOperationException("현재 Pair에서 활성화할 수 있는 유사도 가중치가 없습니다.");
        wPosition /= active;
        wPattern /= active;
        wFeature /= active;
        wContext /= active;

        var contributionPosition = position * wPosition;
        var contributionPattern = pattern * wPattern;
        var contributionFeature = feature * wFeature;
        var contributionContext = context * wContext;
        var baseSimilarity = contributionPosition + contributionPattern
            + contributionFeature + contributionContext;
        var (sizeCompatible, sizeScore) = SizeCompatibility(query.Size, candidate.Size, sizeMode);
        var adjusted = sizeCompatible ? baseSimilarity * sizeScore : 0.0;
        return new UtilityPipePairScore(
            position, pattern, feature, context,
            wPosition, wPattern, wFeature, wContext,
            contributionPosition, contributionPattern, contributionFeature, contributionContext,
            baseSimilarity, sizeScore, adjusted, sizeCompatible, contextAvailable);
    }

    public static UtilityPipeGroupSearchResult ScoreGroup(
        UtilityPipeGroupDescriptor query,
        UtilityPipeGroupDescriptor candidate,
        UtilityPipeGroupSearchOptions options,
        int rank = 0)
    {
        ValidateOptions(options);
        var queryMembers = query.Members.OrderBy(member => member.RoutePathGuid, StringComparer.Ordinal).ToArray();
        var candidateMembers = candidate.Members.OrderBy(member => member.RoutePathGuid, StringComparer.Ordinal).ToArray();
        var pairScores = new UtilityPipePairScore[queryMembers.Length, candidateMembers.Length];
        var augmentedSize = queryMembers.Length + candidateMembers.Length;
        var weights = new double[augmentedSize, augmentedSize];

        for (var q = 0; q < queryMembers.Length; q++)
        for (var c = 0; c < candidateMembers.Length; c++)
        {
            var score = ScorePair(queryMembers[q], candidateMembers[c], options.SizeMatchMode, options.PairWeights);
            pairScores[q, c] = score;
            weights[q, c] = score.SizeCompatible ? score.AdjustedSimilarity : -1_000_000.0;
        }

        var assignment = MaximumWeightAssignment(weights);
        var matches = new List<UtilityPipeGroupMemberMatch>();
        var matchedQuery = new HashSet<string>(StringComparer.Ordinal);
        var matchedCandidate = new HashSet<string>(StringComparer.Ordinal);
        for (var q = 0; q < queryMembers.Length; q++)
        {
            var c = assignment[q];
            if (c < 0 || c >= candidateMembers.Length) continue;
            var score = pairScores[q, c];
            if (!score.SizeCompatible || score.AdjustedSimilarity <= 0.0) continue;
            matches.Add(new(queryMembers[q], candidateMembers[c], score));
            matchedQuery.Add(queryMembers[q].RoutePathGuid);
            matchedCandidate.Add(candidateMembers[c].RoutePathGuid);
        }
        matches.Sort((left, right) => string.CompareOrdinal(left.Query.RoutePathGuid, right.Query.RoutePathGuid));

        var matchedAverage = matches.Count == 0 ? 0.0 : matches.Average(match => match.Score.AdjustedSimilarity);
        var coverage = queryMembers.Length + candidateMembers.Length == 0
            ? 0.0
            : 2.0 * matches.Count / (queryMembers.Length + candidateMembers.Length);
        var arrangement = ArrangementSimilarity(query.Arrangement, candidate.Arrangement);
        var groupWeightSum = options.MatchedWeight + options.ArrangementWeight;
        var matchedWeight = options.MatchedWeight / groupWeightSum;
        var arrangementWeight = options.ArrangementWeight / groupWeightSum;
        var matchedContribution = matchedAverage * matchedWeight;
        var arrangementContribution = arrangement * arrangementWeight;
        var similarity = coverage * (matchedContribution + arrangementContribution);

        return new UtilityPipeGroupSearchResult(
            rank, candidate, similarity, matchedAverage, coverage, arrangement,
            matchedWeight, arrangementWeight, matchedContribution, arrangementContribution,
            matches,
            queryMembers.Where(member => !matchedQuery.Contains(member.RoutePathGuid)).ToArray(),
            candidateMembers.Where(member => !matchedCandidate.Contains(member.RoutePathGuid)).ToArray());
    }

    public static double ArrangementSimilarity(System.Text.Json.JsonElement query, System.Text.Json.JsonElement candidate)
    {
        var metrics = new (string[] Path, bool Signed)[]
        {
            (["start","std","0"],false), (["start","std","1"],false), (["start","std","2"],false),
            (["end","std","0"],false), (["end","std","1"],false), (["end","std","2"],false),
            (["displacement","mean","0"],true), (["displacement","mean","1"],true), (["displacement","mean","2"],true),
            (["displacement","std","0"],false), (["displacement","std","1"],false), (["displacement","std","2"],false),
            (["start_pairwise_distance_mm","mean"],false), (["start_pairwise_distance_mm","std"],false),
            (["start_pairwise_distance_mm","min"],false), (["start_pairwise_distance_mm","max"],false),
            (["end_pairwise_distance_mm","mean"],false), (["end_pairwise_distance_mm","std"],false),
            (["end_pairwise_distance_mm","min"],false), (["end_pairwise_distance_mm","max"],false),
            (["length_mm","mean"],false), (["length_mm","std"],false),
            (["step_count","mean"],false), (["step_count","std"],false),
            (["aabb","size","0"],false), (["aabb","size","1"],false), (["aabb","size","2"],false),
        };
        var scores = new List<double>(metrics.Length);
        foreach (var (path, signed) in metrics)
        {
            if (!TryNumber(query, path, out var left) || !TryNumber(candidate, path, out var right))
                continue;
            scores.Add(signed
                ? Clamp01(1.0 - Math.Abs(left - right) / RelativeDistanceMaxMm)
                : Clamp01(1.0 - Math.Abs(left - right) / Math.Max(1.0, Math.Max(Math.Abs(left), Math.Abs(right)))));
        }
        return scores.Count == 0 ? 0.0 : scores.Average();
    }

    public static (bool Compatible, double Score) SizeCompatibility(
        string querySize, string candidateSize, GroupSizeMatchMode mode)
    {
        if (mode == GroupSizeMatchMode.Ignore) return (true, 1.0);
        var query = NormalizeSize(querySize);
        var candidate = NormalizeSize(candidateSize);
        if (string.Equals(query, candidate, StringComparison.OrdinalIgnoreCase)) return (true, 1.0);
        if (mode == GroupSizeMatchMode.ExactOnly) return (false, 0.0);
        if (!TryNominalSize(query, out var queryValue) || !TryNominalSize(candidate, out var candidateValue))
            return (true, 0.0);
        var queryIndex = NearestSizeIndex(queryValue);
        var candidateIndex = NearestSizeIndex(candidateValue);
        return Math.Abs(queryIndex - candidateIndex) switch
        {
            1 => (true, 0.80),
            2 => (true, 0.50),
            _ => (true, 0.0),
        };
    }

    internal static int[] MaximumWeightAssignment(double[,] weights)
    {
        var rows = weights.GetLength(0);
        var columns = weights.GetLength(1);
        if (rows != columns) throw new ArgumentException("Hungarian 입력은 정사각 행렬이어야 합니다.");
        var n = rows;
        if (n == 0) return [];
        var maximum = double.NegativeInfinity;
        foreach (var value in weights) maximum = Math.Max(maximum, value);
        var u = new double[n + 1];
        var v = new double[n + 1];
        var p = new int[n + 1];
        var way = new int[n + 1];
        const double epsilon = 1e-12;

        for (var i = 1; i <= n; i++)
        {
            p[0] = i;
            var minv = Enumerable.Repeat(double.PositiveInfinity, n + 1).ToArray();
            var used = new bool[n + 1];
            var j0 = 0;
            do
            {
                used[j0] = true;
                var i0 = p[j0];
                var delta = double.PositiveInfinity;
                var j1 = 0;
                for (var j = 1; j <= n; j++)
                {
                    if (used[j]) continue;
                    var cost = maximum - weights[i0 - 1, j - 1];
                    var current = cost - u[i0] - v[j];
                    if (current < minv[j] - epsilon)
                    {
                        minv[j] = current;
                        way[j] = j0;
                    }
                    if (minv[j] < delta - epsilon || (Math.Abs(minv[j] - delta) <= epsilon && j < j1))
                    {
                        delta = minv[j];
                        j1 = j;
                    }
                }
                for (var j = 0; j <= n; j++)
                {
                    if (used[j]) { u[p[j]] += delta; v[j] -= delta; }
                    else minv[j] -= delta;
                }
                j0 = j1;
            } while (p[j0] != 0);
            do
            {
                var j1 = way[j0];
                p[j0] = p[j1];
                j0 = j1;
            } while (j0 != 0);
        }
        var assignment = Enumerable.Repeat(-1, n).ToArray();
        for (var j = 1; j <= n; j++)
            if (p[j] > 0) assignment[p[j] - 1] = j - 1;
        return assignment;
    }

    private static void ValidateOptions(UtilityPipeGroupSearchOptions options)
    {
        ValidateWeights(options.PairWeights);
        if (options.K < 1) throw new ArgumentOutOfRangeException(nameof(options.K));
        if (!double.IsFinite(options.MatchedWeight) || !double.IsFinite(options.ArrangementWeight)
            || options.MatchedWeight < 0 || options.ArrangementWeight < 0
            || options.MatchedWeight + options.ArrangementWeight <= 0)
            throw new ArgumentOutOfRangeException(nameof(options), "그룹 최종 가중치는 0 이상의 유한수이며 합계가 0보다 커야 합니다.");
    }

    private static void ValidateWeights(RerankWeights weights)
    {
        var values = new[] { weights.Position, weights.Pattern, weights.Vector, weights.Context };
        if (values.Any(value => !double.IsFinite(value) || value < 0) || values.Sum() <= 0)
            throw new ArgumentOutOfRangeException(nameof(weights), "Pair 가중치는 0 이상의 유한수이며 합계가 0보다 커야 합니다.");
    }

    private static (double X, double Y, double Z) Delta(UtilityPipeGroupMember member) =>
        (member.EndXyz.X - member.StartXyz.X, member.EndXyz.Y - member.StartXyz.Y, member.EndXyz.Z - member.StartXyz.Z);

    private static double Distance((double X, double Y, double Z) left, (double X, double Y, double Z) right)
    {
        var dx = left.X - right.X;
        var dy = left.Y - right.Y;
        var dz = left.Z - right.Z;
        return Math.Sqrt(dx * dx + dy * dy + dz * dz);
    }

    private static double PatternSimilarity(string left, string right)
    {
        var a = CompressPattern(left);
        var b = CompressPattern(right);
        if (a.Length == 0 || b.Length == 0) return 0.0;
        var previous = Enumerable.Range(0, b.Length + 1).ToArray();
        for (var i = 1; i <= a.Length; i++)
        {
            var current = new int[b.Length + 1];
            current[0] = i;
            for (var j = 1; j <= b.Length; j++)
                current[j] = Math.Min(Math.Min(current[j - 1] + 1, previous[j] + 1),
                    previous[j - 1] + (a[i - 1] == b[j - 1] ? 0 : 1));
            previous = current;
        }
        return Clamp01(1.0 - (double)previous[b.Length] / Math.Max(a.Length, b.Length));
    }

    /// <summary>두 멤버의 개별 꺾임점 시퀀스(ordinal 순)를 가중 편집거리로 비교한다.
    /// 위치(zone)·꺾임유형(transition)·원인(cause)이 모두 같으면 대체비용 0, zone+transition만
    /// 같으면 0.4, transition만 같으면 0.7, 전혀 다르면 1.0(=삽입/삭제와 동일)을 적용해
    /// "형상은 비슷한데 원인이 다른" 경우를 완전 불일치보다는 가깝게, 완전 일치보다는 멀게 평가한다.</summary>
    internal static double BendFeatureMatch(
        IReadOnlyList<BendFeaturePointSummary> left, IReadOnlyList<BendFeaturePointSummary> right)
    {
        if (left.Count == 0 || right.Count == 0) return 0.0;
        var previous = new double[right.Count + 1];
        for (var j = 0; j <= right.Count; j++) previous[j] = j;
        for (var i = 1; i <= left.Count; i++)
        {
            var current = new double[right.Count + 1];
            current[0] = i;
            for (var j = 1; j <= right.Count; j++)
            {
                var substitution = previous[j - 1] + BendPointSubstitutionCost(left[i - 1], right[j - 1]);
                var deletion = previous[j] + 1.0;
                var insertion = current[j - 1] + 1.0;
                current[j] = Math.Min(substitution, Math.Min(deletion, insertion));
            }
            previous = current;
        }
        var distance = previous[right.Count];
        return Clamp01(1.0 - distance / Math.Max(left.Count, right.Count));
    }

    private static double BendPointSubstitutionCost(BendFeaturePointSummary left, BendFeaturePointSummary right)
    {
        var zoneMatch = string.Equals(left.SegmentZone, right.SegmentZone, StringComparison.OrdinalIgnoreCase);
        var transitionMatch = string.Equals(left.TransitionType, right.TransitionType, StringComparison.OrdinalIgnoreCase);
        var causeMatch = string.Equals(left.Cause, right.Cause, StringComparison.OrdinalIgnoreCase);
        if (zoneMatch && transitionMatch && causeMatch) return 0.0;
        if (zoneMatch && transitionMatch) return 0.4;
        if (transitionMatch) return 0.7;
        return 1.0;
    }

    private static string[] CompressPattern(string pattern)
    {
        var tokens = pattern.Split('-', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        var result = new List<string>();
        foreach (var token in tokens)
            if (result.Count == 0 || !string.Equals(result[^1], token, StringComparison.OrdinalIgnoreCase))
                result.Add(token.ToUpperInvariant());
        return result.ToArray();
    }

    private static double Cosine(ReadOnlySpan<double> left, ReadOnlySpan<double> right)
    {
        if (left.Length != right.Length) return 0.0;
        double dot = 0, normLeft = 0, normRight = 0;
        for (var index = 0; index < left.Length; index++)
        {
            dot += left[index] * right[index];
            normLeft += left[index] * left[index];
            normRight += right[index] * right[index];
        }
        var denominator = Math.Sqrt(normLeft * normRight);
        return denominator <= 1e-15 ? 0.0 : dot / denominator;
    }

    private static bool TryNumber(System.Text.Json.JsonElement root, IReadOnlyList<string> path, out double value)
    {
        var current = root;
        foreach (var part in path)
        {
            if (current.ValueKind == System.Text.Json.JsonValueKind.Array && int.TryParse(part, out var index))
            {
                if (index < 0 || index >= current.GetArrayLength()) { value = 0; return false; }
                current = current[index];
            }
            else if (current.ValueKind == System.Text.Json.JsonValueKind.Object && current.TryGetProperty(part, out var child))
                current = child;
            else { value = 0; return false; }
        }
        return current.TryGetDouble(out value) && double.IsFinite(value);
    }

    private static string NormalizeSize(string value) => value.Trim().Replace(" ", "").ToUpperInvariant();
    private static bool TryNominalSize(string value, out double size) =>
        double.TryParse(value.TrimEnd('A'), System.Globalization.NumberStyles.Float,
            System.Globalization.CultureInfo.InvariantCulture, out size);
    private static int NearestSizeIndex(double value) =>
        Enumerable.Range(0, StandardNominalSizes.Length)
            .OrderBy(index => Math.Abs(StandardNominalSizes[index] - value)).ThenBy(index => index).First();
    private static double Clamp01(double value) => Math.Max(0.0, Math.Min(1.0, value));
}
