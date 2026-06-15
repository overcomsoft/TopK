using System;
using System.Collections.Generic;
using System.Linq;
using System.Numerics;

namespace AutoRoutingLibrary.Core
{
    public class ExtractedFeatures
    {
        public List<double> RackLevelsZ { get; set; } = new();
        public string PreferredSourceFace { get; set; } = "Any";
        public string PreferredTargetFace { get; set; } = "Any";
        public double SourceFaceConfidence { get; set; }
        public double TargetFaceConfidence { get; set; }
    }

    public static class FeatureExtractor
    {
        /// <summary>
        /// 다수 배관들의 폴리라인 세그먼트 데이터로부터 대표 수평 고도(Rack Z) 및 접속면 통계를 일괄 분석합니다.
        /// </summary>
        public static ExtractedFeatures ExtractGlobalFeatures(List<List<Vector3>> normalizedPipes)
        {
            var features = new ExtractedFeatures();
            if (normalizedPipes == null || normalizedPipes.Count == 0) return features;

            // 1. Z고도 수평 주행 누적 히스토그램 피크 검출
            features.RackLevelsZ = DetectRackLevels(normalizedPipes);

            // 2. 접속면(Face) Voting 분석
            AnalyzeEndpointFaces(normalizedPipes, features);

            return features;
        }

        /// <summary>
        /// 수평 구간의 주행 길이를 기반으로 랙 고도(Z레벨) 후보군을 검출합니다.
        /// </summary>
        private static List<double> DetectRackLevels(List<List<Vector3>> pipes, double binSizeMm = 100.0, int maxLevels = 8)
        {
            var zWeights = new Dictionary<double, double>(); // zBin(mm) -> 누적 수평 주행 거리

            foreach (var points in pipes)
            {
                for (int i = 0; i < points.Count - 1; i++)
                {
                    var p1 = points[i];
                    var p2 = points[i + 1];

                    // Z축 변동이 거의 없는 수평 세그먼트 판별
                    if (Math.Abs(p1.Z - p2.Z) < 5.0)
                    {
                        double midZ = (p1.Z + p2.Z) / 2.0;
                        // binSize 단위로 클램핑하여 그룹핑
                        double binZ = Math.Round(midZ / binSizeMm) * binSizeMm;
                        double length = Vector2.Distance(new Vector2(p1.X, p1.Y), new Vector2(p2.X, p2.Y));

                        zWeights[binZ] = zWeights.GetValueOrDefault(binZ) + length;
                    }
                }
            }

            if (zWeights.Count == 0) return new List<double>();

            // 가중치(길이)가 높은 순서대로 로컬 피크 정렬 후 병합 처리
            var sortedBins = zWeights.OrderByDescending(kv => kv.Value).ToList();
            var peakLevels = new List<double>();

            foreach (var bin in sortedBins)
            {
                // 이미 선점된 대표 피크와 300mm(3셀 범위) 이내로 근접해 있으면 병합
                bool isMerged = false;
                foreach (var peak in peakLevels)
                {
                    if (Math.Abs(peak - bin.Key) < 300.0)
                    {
                        isMerged = true;
                        break;
                    }
                }

                if (!isMerged)
                {
                    peakLevels.Add(bin.Key);
                    if (peakLevels.Count >= maxLevels) break;
                }
            }

            return peakLevels.OrderBy(z => z).ToList();
        }

        /// <summary>
        /// 시작 및 종단 세그먼트의 dominant axis 벡터를 비교하여 접속면 방향 최다 득표 face를 감출합니다.
        /// </summary>
        private static void AnalyzeEndpointFaces(List<List<Vector3>> pipes, ExtractedFeatures outFeatures)
        {
            var sourceFaceVotes = new Dictionary<string, int>();
            var targetFaceVotes = new Dictionary<string, int>();

            foreach (var points in pipes)
            {
                if (points.Count < 2) continue;

                // 1. 출발면 분석 (첫 번째 세그먼트 진행 방향)
                var startDir = Vector3.Normalize(points[1] - points[0]);
                string startFace = GetDominantFace(startDir);
                sourceFaceVotes[startFace] = sourceFaceVotes.GetValueOrDefault(startFace) + 1;

                // 2. 종단면 분석 (마지막 세그먼트가 덕트로 들어가는 방향의 역방향)
                int lastIdx = points.Count - 1;
                var endDir = Vector3.Normalize(points[lastIdx] - points[lastIdx - 1]);
                // 진입 방향의 역벡터(면의 법선 방향)로 판정
                string endFace = GetDominantFace(-endDir);
                targetFaceVotes[endFace] = targetFaceVotes.GetValueOrDefault(endFace) + 1;
            }

            int totalSource = sourceFaceVotes.Values.Sum();
            if (totalSource > 0)
            {
                var bestSource = sourceFaceVotes.OrderByDescending(kv => kv.Value).First();
                outFeatures.PreferredSourceFace = bestSource.Key;
                outFeatures.SourceFaceConfidence = (double)bestSource.Value / totalSource;
            }

            int totalTarget = targetFaceVotes.Values.Sum();
            if (totalTarget > 0)
            {
                var bestTarget = targetFaceVotes.OrderByDescending(kv => kv.Value).First();
                outFeatures.PreferredTargetFace = bestTarget.Key;
                outFeatures.TargetFaceConfidence = (double)bestTarget.Value / totalTarget;
            }
        }

        /// <summary>
        /// 3D 단위 벡터로부터 가장 내적값이 큰 주축 법선 방향 면을 텍스트 기호로 맵핑합니다.
        /// </summary>
        public static string GetDominantFace(Vector3 direction)
        {
            double absX = Math.Abs(direction.X);
            double absY = Math.Abs(direction.Y);
            double absZ = Math.Abs(direction.Z);

            if (absX >= absY && absX >= absZ)
            {
                return direction.X >= 0 ? "+x" : "-x";
            }
            else if (absY >= absX && absY >= absZ)
            {
                return direction.Y >= 0 ? "+y" : "-y";
            }
            else
            {
                return direction.Z >= 0 ? "+z" : "-z";
            }
        }
    }
}
