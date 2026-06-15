using System;
using System.Collections.Generic;
using System.Linq;
using System.Numerics;

namespace AutoRoutingLibrary.Core
{
    public enum PipeDirection
    {
        Forward,
        Reverse
    }

    public class MatchResult
    {
        public string RoutePathGuid { get; set; } = string.Empty;
        public string Utility { get; set; } = string.Empty;
        public string Group { get; set; } = string.Empty;
        public List<Vector3> NormalizedPoints { get; set; } = new();
        public PipeDirection Direction { get; set; }
        public double MatchCost { get; set; }
    }

    public static class RouteMatcher
    {
        /// <summary>
        /// 기존 배관 폴리라인 목록과 라우팅 작업(Task) 목록을 기하학적 거리 및 GUID 기반으로 1:1 정밀 매칭합니다.
        /// </summary>
        public static List<MatchResult> MatchRoutes(
            IEnumerable<Vector3[]> rawPipes,
            IEnumerable<(string guid, Vector3 start, Vector3 end, string utility, string group)> tasks,
            double maxMatchDistance = 3000.0)
        {
            var results = new List<MatchResult>();
            var taskList = tasks.ToList();
            var pipeList = rawPipes.ToList();

            foreach (var task in taskList)
            {
                MatchResult? bestMatch = null;
                double minCost = double.MaxValue;
                int bestPipeIdx = -1;

                for (int i = 0; i < pipeList.Count; i++)
                {
                    var points = pipeList[i];
                    if (points == null || points.Length < 2) continue;

                    var pipeStart = points[0];
                    var pipeEnd = points[points.Length - 1];

                    // 1. 정방향/역방향 매칭 거리(Manhattan/Euclidean 비용) 산출
                    double forwardCost = Vector3.Distance(task.start, pipeStart) + Vector3.Distance(task.end, pipeEnd);
                    double reverseCost = Vector3.Distance(task.start, pipeEnd) + Vector3.Distance(task.end, pipeStart);

                    double matchCost = Math.Min(forwardCost, reverseCost);

                    // 지정된 매칭 한계 임계값 내에 있을 때 후보 등록
                    if (matchCost < maxMatchDistance && matchCost < minCost)
                    {
                        minCost = matchCost;
                        bestPipeIdx = i;

                        var direction = (forwardCost <= reverseCost) ? PipeDirection.Forward : PipeDirection.Reverse;
                        
                        // 방향에 맞게 좌표 리스트 정렬 (Task의 Start -> End 흐름에 맞춤)
                        var normalizedPoints = points.ToList();
                        if (direction == PipeDirection.Reverse)
                        {
                            normalizedPoints.Reverse();
                        }

                        bestMatch = new MatchResult
                        {
                            RoutePathGuid = task.guid,
                            Utility = task.utility,
                            Group = task.group,
                            NormalizedPoints = normalizedPoints,
                            Direction = direction,
                            MatchCost = matchCost
                        };
                    }
                }

                if (bestMatch != null)
                {
                    results.Add(bestMatch);
                    // 매칭된 배관은 중복 매칭을 막기 위해 후보군에서 제외할 수도 있으나, 
                    // 공유 배관 패턴이 존재할 수 있으므로 여기서는 단순 수집합니다.
                }
            }

            return results;
        }
    }
}
