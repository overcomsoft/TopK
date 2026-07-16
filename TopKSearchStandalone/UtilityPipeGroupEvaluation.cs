using System.Globalization;
using System.Text;

namespace RoutingAI.Standalone;

/// <summary>
/// UtilityPipeGroup 검색의 결정론, 자기검색 제외, Size/Context/Arrangement A/B와 지연시간을
/// 실제 READY 그룹 표본으로 측정한다. 정답 라벨이 없는 운영 DB에서는 Precision을 임의로
/// 만들지 않고 관측 가능한 점수·Coverage·순위변화와 설계자 검토 대상을 보고한다.
/// </summary>
public static class UtilityPipeGroupEvaluation
{
    private sealed record Profile(string Name, UtilityPipeGroupSearchOptions Options);
    private sealed record Observation(string Profile, string QueryId, string QueryEquipment,
        int QueryMembers, int Returned, double ElapsedMs, string Top1Id, string Top1Equipment,
        double Top1Score, double Top1Coverage, double Top1Arrangement, int Top1Matched,
        bool SelfExcluded);

    public static async Task<string> RunMarkdownAsync(DbConfig db, int sampleSize = 20, int k = 5)
    {
        sampleSize = Math.Clamp(sampleSize, 1, 100);
        k = Math.Clamp(k, 1, 20);
        var presets = await UtilityPipeGroupSearch.FetchPresetsAsync(db, limit: 5000).ConfigureAwait(false);
        var eligible = presets
            .GroupBy(item => (item.UtilityGroup, item.Utility))
            .Where(group => group.Count() >= 2)
            .SelectMany(group => group)
            .OrderBy(item => item.GroupVectorId, StringComparer.Ordinal)
            .ToArray();
        if (eligible.Length == 0) throw new InvalidOperationException("비교 가능한 READY 그룹이 없습니다.");
        var sample = SelectEvenly(eligible, sampleSize);

        var profiles = new[]
        {
            new Profile("Baseline-PreferExact", new UtilityPipeGroupSearchOptions { K = k }),
            new Profile("Size-ExactOnly", new UtilityPipeGroupSearchOptions
                { K = k, SizeMatchMode = GroupSizeMatchMode.ExactOnly }),
            new Profile("Size-Ignore", new UtilityPipeGroupSearchOptions
                { K = k, SizeMatchMode = GroupSizeMatchMode.Ignore }),
            new Profile("Context-Off", new UtilityPipeGroupSearchOptions
                { K = k, PairWeights = new RerankWeights(1, 1, 1, 0) }),
            new Profile("Arrangement-Off", new UtilityPipeGroupSearchOptions
                { K = k, MatchedWeight = 1.0, ArrangementWeight = 0.0 }),
        };

        var observations = new List<Observation>();
        var baselineTopIds = new Dictionary<string, string>(StringComparer.Ordinal);
        var deterministic = true;
        foreach (var profile in profiles)
        {
            foreach (var query in sample)
            {
                var (results, meta) = await UtilityPipeGroupSearch.SearchAsync(
                    db, query.GroupVectorId, profile.Options).ConfigureAwait(false);
                var top = results.FirstOrDefault();
                var selfExcluded = results.All(result =>
                    !string.Equals(result.Candidate.GroupVectorId, query.GroupVectorId, StringComparison.Ordinal));
                observations.Add(new Observation(profile.Name, query.GroupVectorId,
                    query.EquipmentInstanceKey, query.MemberCount, results.Count, meta.SearchTimeMs,
                    top?.Candidate.GroupVectorId ?? "", top?.Candidate.EquipmentInstanceKey ?? "",
                    top?.GroupSimilarity ?? 0, top?.Coverage ?? 0, top?.Arrangement ?? 0,
                    top?.Matches.Count ?? 0, selfExcluded));
                if (profile.Name == "Baseline-PreferExact")
                    baselineTopIds[query.GroupVectorId] = top?.Candidate.GroupVectorId ?? "";
            }
        }

        // 동일 입력 반복 결과의 GUID 순서가 완전히 같은지 확인한다.
        foreach (var query in sample)
        {
            var (repeat, _) = await UtilityPipeGroupSearch.SearchAsync(
                db, query.GroupVectorId, profiles[0].Options).ConfigureAwait(false);
            deterministic &= string.Equals(baselineTopIds[query.GroupVectorId],
                repeat.FirstOrDefault()?.Candidate.GroupVectorId ?? "", StringComparison.Ordinal);
        }

        var baseline = observations.Where(item => item.Profile == "Baseline-PreferExact").ToArray();
        var builder = new StringBuilder();
        builder.AppendLine("# UtilityPipeGroup Top-K Phase 5 정량 평가 보고서");
        builder.AppendLine();
        builder.AppendLine($"- 생성시각: {DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss zzz}");
        builder.AppendLine($"- READY 그룹: {presets.Count:N0}개");
        builder.AppendLine($"- 비교 가능한 그룹: {eligible.Length:N0}개");
        builder.AppendLine($"- 결정론적 표본: {sample.Length:N0}개, K={k}");
        builder.AppendLine($"- 동일 입력 Top-1 결정론: {(deterministic ? "100% 통과" : "실패")}");
        builder.AppendLine($"- 자기검색 제외: {(observations.All(item => item.SelfExcluded) ? "100% 통과" : "실패")}");
        builder.AppendLine();
        builder.AppendLine("## 프로파일별 결과");
        builder.AppendLine();
        builder.AppendLine("| 프로파일 | 평균 ms | P95 ms | 평균 반환 | 평균 Top-1 | 평균 Coverage | 평균 Arrangement | Baseline Top-1 유지율 |");
        builder.AppendLine("|---|---:|---:|---:|---:|---:|---:|---:|");
        foreach (var profile in profiles)
        {
            var rows = observations.Where(item => item.Profile == profile.Name).ToArray();
            var elapsed = rows.Select(item => item.ElapsedMs).OrderBy(value => value).ToArray();
            var sameTop = rows.Count(item => baselineTopIds[item.QueryId] == item.Top1Id);
            builder.AppendLine($"| {profile.Name} | {rows.Average(item => item.ElapsedMs):F1} | " +
                               $"{Percentile(elapsed, 0.95):F1} | {rows.Average(item => item.Returned):F2} | " +
                               $"{rows.Average(item => item.Top1Score):F4} | {rows.Average(item => item.Top1Coverage):F4} | " +
                               $"{rows.Average(item => item.Top1Arrangement):F4} | {(double)sameTop / rows.Length:P1} |");
        }

        builder.AppendLine();
        builder.AppendLine("## Baseline 표본 상세");
        builder.AppendLine();
        builder.AppendLine("| Query 장비 | 멤버 | Top-1 장비 | 매칭 | Score | Coverage | Arrangement | ms |");
        builder.AppendLine("|---|---:|---|---:|---:|---:|---:|---:|");
        foreach (var row in baseline)
            builder.AppendLine($"| {Escape(row.QueryEquipment)} | {row.QueryMembers} | {Escape(row.Top1Equipment)} | " +
                               $"{row.Top1Matched} | {row.Top1Score:F4} | {row.Top1Coverage:F4} | " +
                               $"{row.Top1Arrangement:F4} | {row.ElapsedMs:F1} |");

        builder.AppendLine();
        builder.AppendLine("## 판정");
        builder.AppendLine();
        builder.AppendLine($"- Baseline 평균 지연시간: {baseline.Average(item => item.ElapsedMs):F1} ms");
        builder.AppendLine($"- Baseline P95 지연시간: {Percentile(baseline.Select(item => item.ElapsedMs).OrderBy(v => v).ToArray(), 0.95):F1} ms");
        builder.AppendLine($"- 2초 검색 목표: {(baseline.All(item => item.ElapsedMs <= 2000) ? "통과" : "초과 사례 있음")}");
        builder.AppendLine($"- 평균 Top-1 Coverage: {baseline.Average(item => item.Top1Coverage):P1}");
        builder.AppendLine("- Precision/Recall은 정답 레이블이 없어 계산하지 않는다. 표본 상세를 설계자가 검토해 적합 여부를 라벨링해야 한다.");
        builder.AppendLine("- A/B Top-1 유지율이 낮은 프로파일은 해당 항목이 순위에 미치는 영향이 크다는 뜻이며, 품질 우열을 직접 의미하지 않는다.");
        return builder.ToString();
    }

    private static T[] SelectEvenly<T>(IReadOnlyList<T> source, int requested)
    {
        var count = Math.Min(requested, source.Count);
        if (count == source.Count) return source.ToArray();
        return Enumerable.Range(0, count)
            .Select(index => source[(int)Math.Floor((double)index * source.Count / count)])
            .ToArray();
    }

    private static double Percentile(IReadOnlyList<double> sorted, double percentile)
    {
        if (sorted.Count == 0) return 0;
        var index = Math.Clamp((int)Math.Ceiling(percentile * sorted.Count) - 1, 0, sorted.Count - 1);
        return sorted[index];
    }

    private static string Escape(string value) => value.Replace("|", "\\|", StringComparison.Ordinal);
}
