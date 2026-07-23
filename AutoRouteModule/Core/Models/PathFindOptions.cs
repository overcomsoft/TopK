
using System;

namespace AutoRouteModule.Core
{

    public struct PathFindOptions
    {
        public const int DEFAULT_MAX_SEARCH_NODES = 100000;
        public const int DEFAULT_TIMEOUT_MILLISECONDS = 30000;
        public const int TURN_PENALTY_MAX = 100;
        public const int POSITIVE_PENALTY_MAX = 20;
        public const int NEGATIVE_PENALTY_MAX = 20;


        // 방향이 바뀔 때 추가 비용
        // 배관 엘보우 수를 줄이고 싶으면 크게 준다.(0~100)

        public int TurnPenalty;

        // 양수 방향 이동 비용
        public Int3 PositivePenalty;

        // 음수 방향 이동 비용
        public Int3 NegativePenalty;

        // 최대 탐색 노드 수 (0 = 무제한)
        // 타임아웃 방지용, 10000~50000 추천
        public int MaxSearchNodes;

        // 휴리스틱 가중치 (1.0 = 표준, 1.0~2.0 추천)
        // 값이 클수록 빠르지만 최적 경로를 놓칠 수 있음
        // 1.0 이상이면 Weighted A*가 됨
        public float HeuristicWeight;

        // 방향 전환 후 최소 직선 이동 거리 (0 = 제약 없음)
        // 배관이 꺾인 후 최소한 이 거리만큼은 직진해야 함 (단위 : m)
        // 예: 2로 설정하면 꺾인 후 최소 2m는 같은 방향으로 이동
        public float MinStraightDistance;

        // 타임아웃 시간 (밀리초, 0 = 무제한)
        // 경로 탐색이 이 시간을 초과하면 자동으로 중단됨
        public int TimeoutMilliseconds;

        public PathFindOptions(
            int turnPenalty = -1,
            Int3 positivePenalty = default,
            Int3 negativePenalty = default,
            int maxSearchNodes = -1, float heuristicWeight = 1.0f, float minStraightDistance = 0,
            int timeoutMilliseconds = 0)
        {
            TurnPenalty = MathHelper.Clamp(turnPenalty, 0, TURN_PENALTY_MAX);
            PositivePenalty = Int3.Clamp(positivePenalty, 0, POSITIVE_PENALTY_MAX);
            NegativePenalty = Int3.Clamp(negativePenalty, 0, NEGATIVE_PENALTY_MAX);
            MaxSearchNodes = Math.Max(maxSearchNodes, 0);
            HeuristicWeight = Math.Max(heuristicWeight, 1.0f);
            MinStraightDistance = Math.Max(minStraightDistance, 0);
            TimeoutMilliseconds = Math.Max(timeoutMilliseconds, 0);
        }

        public static PathFindOptions Default => new PathFindOptions
        {
            TurnPenalty = 0,
            PositivePenalty = Int3.Zero,
            NegativePenalty = Int3.Zero,
            MaxSearchNodes = DEFAULT_MAX_SEARCH_NODES,
            HeuristicWeight = 1.0f,
            MinStraightDistance = 0,
            TimeoutMilliseconds = DEFAULT_TIMEOUT_MILLISECONDS,
        };


       

       
    }
}
