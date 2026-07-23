//using AutoRouteModule.Core;
//using System.Numerics;

//namespace AutoRouteModule.Examples
//{
//    /// <summary>
//    /// MinStraightDistance 옵션 사용 예제
//    /// </summary>
//    public class MinStraightDistanceExample
//    {
//        public void Example1_NoConstraint()
//        {
//            // 제약 없음 - 자유롭게 방향 전환 가능
//            var pathfinder = new GridAStar3D();
//            var options = new PathFindOptions(
//                minStraightDistance: 0  // 제약 없음
//            );

//            var result = pathfinder.FindPath(
//                new Vector3(0, 0, 0),
//                new Vector3(10, 0, 10),
//                DirectionType.None,
//                1.0f,
//                options
//            );

//            // 결과: 많은 지그재그 패턴이 나올 수 있음
//        }

//        public void Example2_MinimumTwoGrids()
//        {
//            // 꺾인 후 최소 2칸은 직진해야 함
//            var pathfinder = new GridAStar3D();
//            var options = new PathFindOptions(
//                minStraightDistance: 2  // 최소 2칸 직진
//            );

//            var result = pathfinder.FindPath(
//                new Vector3(0, 0, 0),
//                new Vector3(10, 0, 10),
//                DirectionType.None,
//                1.0f,
//                options
//            );

//            // 결과: 
//            // - 오른쪽으로 꺾였다면 최소 2칸은 오른쪽으로 이동
//            // - 그 후에야 다른 방향으로 전환 가능
//            // - 더 부드럽고 예측 가능한 경로
//        }

//        public void Example3_PipeRouting()
//        {
//            // 실제 배관 라우팅 예제
//            var pathfinder = new GridAStar3D();
//            var options = new PathFindOptions(
//                turnPenalty: 30,         // 엘보우 비용
//                minStraightDistance: 3,  // 최소 3칸 직진 (실제 배관 제약)
//                verticalPenalty: 5,      // 수직 이동 약간 비싸게
//                heuristicWeight: 1.2f    // 약간 빠른 탐색
//            );

//            var result = pathfinder.FindPath(
//                new Vector3(0, 0, 0),
//                new Vector3(20, 10, 15),
//                DirectionType.None,
//                0.5f,  // 0.5m 복셀
//                options
//            );

//            // 결과:
//            // - 엘보우 최소화
//            // - 각 직선 구간이 최소 3칸 (1.5m) 이상
//            // - 실제 배관 설치에 적합한 경로
//        }

//        public void Example4_CombinedConstraints()
//        {
//            // 여러 제약 조합
//            var pathfinder = new GridAStar3D();
//            var options = new PathFindOptions(
//                turnPenalty: 50,         // 방향 전환을 매우 비싸게
//                minStraightDistance: 5,  // 한번 꺾으면 최소 5칸은 직진
//                maxSearchNodes: 30000    // 탐색 제한
//            );

//            var result = pathfinder.FindPath(
//                new Vector3(0, 0, 0),
//                new Vector3(30, 20, 25),
//                DirectionType.None,
//                1.0f,
//                options
//            );

//            // 결과:
//            // - 매우 긴 직선 구간
//            // - 최소한의 방향 전환
//            // - 단순하고 예측 가능한 경로
//        }
//    }
//}
