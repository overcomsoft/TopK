using AutoRouteModule.Core;
using System;
using System.Collections.Generic;
using System.Numerics;
using System.Threading.Tasks;

namespace AutoRouteModule.API
{
    /// <summary>
    /// 자동 경로 탐색 모듈의 메인 API 클래스
    /// 3D 공간에서의 경로 탐색, 장애물 관리, 트리 설정 기능을 제공합니다.
    /// </summary>
    public static class AutoRouteAPI
    {
        #region Pathfinding

        /// <summary>
        /// 시작 지점에서 목표 지점까지의 경로를 비동기로 탐색합니다.
        /// </summary>
        /// <param name="start">시작 위치 (Vector3)</param>
        /// <param name="goal">목표 위치 (Vector3)</param>
        /// <param name="startDirection">시작 방향 (DirectionType)</param>
        /// <param name="diameter">에이전트의 직경 (충돌 검사에 사용됨)</param>
        /// <param name="onFinish">경로 탐색 완료 시 호출되는 콜백 함수</param>
        public static void FindPath(
            Vector3 start,
            Vector3 goal,
            DirectionType startDirection,
            float diameter,
            DirectionType goalDirection,
            Action<PathResult> onFinish)
        {
            AutoRouteManager.Instance.FindPathAsync(start, goal, startDirection, goalDirection, diameter, null, onFinish);
        }

        /// <summary>
        /// 사용자 정의 옵션을 사용하여 시작 지점에서 목표 지점까지의 경로를 비동기로 탐색합니다.
        /// </summary>
        /// <param name="start">시작 위치 (Vector3)</param>
        /// <param name="goal">목표 위치 (Vector3)</param>
        /// <param name="startDirection">시작 방향 (DirectionType)</param>
        /// <param name="diameter">에이전트의 직경 (충돌 검사에 사용됨)</param>
        /// <param name="options">A* 알고리즘의 사용자 정의 옵션 (휴리스틱 가중치, 최대 반복 등)</param>
        /// <param name="onFinish">경로 탐색 완료 시 호출되는 콜백 함수</param>
        public static void FindPath(
            Vector3 start,
            Vector3 goal,
            DirectionType startDirection,
            DirectionType goalDirection,
            float diameter,
            PathFindOptions options,
            Action<PathResult> onFinish)
        {
            AutoRouteManager.Instance.FindPathAsync(start, goal, startDirection, goalDirection, diameter, options, onFinish);
        }

        /// <summary>
        /// 시작 지점에서 목표 지점까지의 경로를 비동기로 탐색하고, 결과를 Task로 반환합니다.
        /// </summary>
        /// <param name="start">시작 위치 (Vector3)</param>
        /// <param name="goal">목표 위치 (Vector3)</param>
        /// <param name="startDirection">시작 방향 (DirectionType)</param>
        /// <param name="goalDirection">목표 방향 (DirectionType)</param>
        /// <param name="diameter">에이전트의 직경 (충돌 검사에 사용됨)</param>
        /// <param name="options">A* 알고리즘의 사용자 정의 옵션</param>
        /// <returns>경로 탐색 결과를 포함한 Task</returns>
        public static async Task<PathResult> FindPathAsync(
            Vector3 start,
            Vector3 goal,
            DirectionType startDirection,
            DirectionType goalDirection,
            float diameter,
            PathFindOptions? options = null)
        {
            return await AutoRouteManager.Instance.FindPathAsync(start, goal, startDirection, goalDirection, diameter, options);
        }

        /// <summary>
        /// 여러 웨이포인트를 경유하는 경로를 비동기로 탐색합니다.
        /// 이미 지나간 경로는 다시 사용할 수 없습니다.
        /// </summary>
        /// <param name="start">시작 위치 (Vector3)</param>
        /// <param name="waypoints">경유할 웨이포인트 리스트</param>
        /// <param name="goal">최종 목표 위치 (Vector3)</param>
        /// <param name="startDirection">시작 방향 (DirectionType)</param>
        /// <param name="diameter">에이전트의 직경 (충돌 검사에 사용됨)</param>
        /// <param name="onFinish">경로 탐색 완료 시 호출되는 콜백 함수</param>
        public static void FindPathWithWaypoints(
            Vector3 start,
            List<Vector3> waypoints,
            Vector3 goal,
            DirectionType startDirection,
            DirectionType goalDirection,
            float diameter,
            Action<PathResult> onFinish)
        {
            AutoRouteManager.Instance.FindPathAsync(start, goal, startDirection, goalDirection, diameter, waypoints, null, onFinish);
        }

        /// <summary>
        /// 여러 웨이포인트를 경유하는 경로를 사용자 정의 옵션과 함께 비동기로 탐색합니다.
        /// 이미 지나간 경로는 다시 사용할 수 없습니다.
        /// </summary>
        /// <param name="start">시작 위치 (Vector3)</param>
        /// <param name="waypoints">경유할 웨이포인트 리스트</param>
        /// <param name="goal">최종 목표 위치 (Vector3)</param>
        /// <param name="startDirection">시작 방향 (DirectionType)</param>
        /// <param name="diameter">에이전트의 직경 (충돌 검사에 사용됨)</param>
        /// <param name="options">A* 알고리즘의 사용자 정의 옵션</param>
        /// <param name="onFinish">경로 탐색 완료 시 호출되는 콜백 함수</param>
        public static void FindPathWithWaypoints(
            Vector3 start,
            List<Vector3> waypoints,
            Vector3 goal,
            DirectionType startDirection,
            DirectionType goalDirection,
            float diameter,
            PathFindOptions options,
            Action<PathResult> onFinish)
        {
            AutoRouteManager.Instance.FindPathAsync(start, goal, startDirection, goalDirection, diameter, waypoints, options, onFinish);
        }

        public static async Task<PathResult> FindPathWithWaypointsAsync(
            Vector3 start,
            List<Vector3> waypoints,
            Vector3 goal,
            DirectionType startDirection,
            DirectionType goalDirection,
            float diameter,
            PathFindOptions? options = null)
        {
            return await AutoRouteManager.Instance.FindPathAsync(start, goal, startDirection, goalDirection, diameter, waypoints, options);
        }

        /// <summary>
        /// 여러 배관의 시작 좌표와 직경으로 전체를 감싸는 AABB를 만들고 Parallel 기반 경로를 비동기로 탐색합니다.
        /// </summary>
        /// <param name="pipes">배관 시작 좌표와 직경 목록. 첫 번째 배관이 목표 기준 배관입니다.</param>
        /// <param name="groupCenterGoal">전체 AABB 중심의 최종 목표 좌표</param>
        /// <param name="startDirection">초기 진행 방향</param>
        /// <param name="goalDirection">최종 목표 방향</param>
        /// <param name="onFinish">경로 탐색 완료 시 호출되는 콜백 함수</param>
        public static void FindParallelPipePaths(
            List<GroupPipeSpec> pipes,
            Vector3 groupCenterGoal,
            DirectionType startDirection,
            DirectionType goalDirection,
            Action<GroupPathFindResult> onFinish)
        {
            AutoRouteManager.Instance.FindParallelPipePathsAsync(pipes, groupCenterGoal, startDirection, goalDirection, onFinish: onFinish);
        }

        /// <summary>
        /// 여러 배관의 시작 좌표와 직경으로 전체를 감싸는 AABB를 만들고 Parallel 기반 경로를 사용자 정의 옵션과 함께 비동기로 탐색합니다.
        /// </summary>
        /// <param name="pipes">배관 시작 좌표와 직경 목록. 첫 번째 배관이 목표 기준 배관입니다.</param>
        /// <param name="groupCenterGoal">전체 AABB 중심의 최종 목표 좌표</param>
        /// <param name="startDirection">초기 진행 방향</param>
        /// <param name="goalDirection">최종 목표 방향</param>
        /// <param name="options">A* 알고리즘의 사용자 정의 옵션</param>
        /// <param name="onFinish"></param>
        public static void FindParallelPipePaths(
            List<GroupPipeSpec> pipes,
            Vector3 groupCenterGoal,
            DirectionType startDirection,
            DirectionType goalDirection,
            PathFindOptions options,
            //float clearance,
            Action<GroupPathFindResult> onFinish)
        {
            AutoRouteManager.Instance.FindParallelPipePathsAsync(pipes, groupCenterGoal, startDirection, goalDirection, options, /*clearance,*/ onFinish);
        }

        /// <summary>
        /// 사용자 정의 옵션을 사용하여 여러 배관의 Parallel 기반 경로를 비동기로 탐색하고, 결과를 Task로 반환합니다.
        /// </summary>
        /// <param name="pipes">배관 시작 좌표와 직경 목록. 첫 번째 배관이 목표 기준 배관입니다.</param>
        /// <param name="groupCenterGoal">전체 AABB 중심의 최종 목표 좌표</param>
        /// <param name="startDirection">초기 진행 방향</param>
        /// <param name="goalDirection">최종 목표 방향</param>
        /// <param name="options">A* 알고리즘의 사용자 정의 옵션</param>
        /// <returns>경로 탐색 결과를 포함하는 Task</returns>
        public static async Task<GroupPathFindResult> FindParallelPipePaths(
            List<GroupPipeSpec> pipes,
            Vector3 groupCenterGoal,
            DirectionType startDirection,
            DirectionType goalDirection,
            PathFindOptions options
            //float clearance
            )
        {
            return await AutoRouteManager.Instance.FindParallelPipePathsAsync(pipes, groupCenterGoal, startDirection, goalDirection, options);
        }

        /// <summary>
        /// 여러 배관을 하나의 AABB로 묶고, 지정한 웨이포인트들을 순서대로 경유하는 경로를 비동기로 탐색합니다.
        /// 웨이포인트와 목표 좌표는 전체 AABB 중심 기준입니다.
        /// 이미 지나간 경로는 다시 사용할 수 없습니다.
        /// </summary>
        /// <param name="pipes">배관 시작 좌표와 직경 목록. 첫 번째 배관이 목표 기준 배관입니다.</param>
        /// <param name="waypoints">전체 AABB 중심이 경유할 웨이포인트 목록</param>
        /// <param name="groupCenterGoal">전체 AABB 중심의 최종 목표 좌표</param>
        /// <param name="startDirection">초기 진행 방향</param>
        /// <param name="goalDirection">최종 목표 방향</param>
        /// <param name="onFinish">경로 탐색 완료 시 호출되는 콜백 함수</param>
        public static void FindParallelPipePathsWithWaypoints(
            List<GroupPipeSpec> pipes,
            List<Vector3> waypoints,
            Vector3 groupCenterGoal,
            DirectionType startDirection,
            DirectionType goalDirection,
            Action<GroupPathFindResult> onFinish)
        {
            AutoRouteManager.Instance.FindParallelPipePathsWithWaypointsAsync(pipes, waypoints, groupCenterGoal, startDirection, goalDirection, onFinish: onFinish);
        }

        /// <summary>
        /// 여러 배관을 하나의 AABB로 묶고, 지정한 웨이포인트들을 순서대로 경유하는 경로를 사용자 정의 옵션과 함께 비동기로 탐색합니다.
        /// 웨이포인트와 목표 좌표는 전체 AABB 중심 기준입니다.
        /// 이미 지나간 경로는 다시 사용할 수 없습니다.
        /// </summary>
        /// <param name="pipes">배관 시작 좌표와 직경 목록. 첫 번째 배관이 목표 기준 배관입니다.</param>
        /// <param name="waypoints">전체 AABB 중심이 경유할 웨이포인트 목록</param>
        /// <param name="groupCenterGoal">전체 AABB 중심의 최종 목표 좌표</param>
        /// <param name="startDirection">초기 진행 방향</param>
        /// <param name="goalDirection">최종 목표 방향</param>
        /// <param name="options">A* 알고리즘의 사용자 정의 옵션</param>
        /// <param name="onFinish">경로 탐색 완료 시 호출되는 콜백 함수</param>
        public static void FindParallelPipePathsWithWaypoints(
            List<GroupPipeSpec> pipes,
            List<Vector3> waypoints,
            Vector3 groupCenterGoal,
            DirectionType startDirection,
            DirectionType goalDirection,
            PathFindOptions options,
            Action<GroupPathFindResult> onFinish)
        {
            AutoRouteManager.Instance.FindParallelPipePathsWithWaypointsAsync(pipes, waypoints, groupCenterGoal, startDirection, goalDirection, options, onFinish);
        }

        /// <summary>
        /// 사용자 정의 옵션을 사용하여 여러 배관의 웨이포인트 경유 경로를 비동기로 탐색하고, 결과를 Task로 반환합니다.
        /// 이미 지나간 경로는 다시 사용할 수 없습니다.
        /// </summary>
        /// <param name="pipes">배관 시작 좌표와 직경 목록. 첫 번째 배관이 목표 기준 배관입니다.</param>
        /// <param name="waypoints">전체 AABB 중심이 경유할 웨이포인트 목록</param>
        /// <param name="groupCenterGoal">전체 AABB 중심의 최종 목표 좌표</param>
        /// <param name="startDirection">초기 진행 방향</param>
        /// <param name="goalDirection">최종 목표 방향</param>
        /// <param name="options">A* 알고리즘의 사용자 정의 옵션</param>
        /// <returns>경로 탐색 결과를 포함하는 Task</returns>
        public static async Task<GroupPathFindResult> FindParallelPipePathsWithWaypoints(
            List<GroupPipeSpec> pipes,
            List<Vector3> waypoints,
            Vector3 groupCenterGoal,
            DirectionType startDirection,
            DirectionType goalDirection,
            PathFindOptions? options = null)
        {
            return await AutoRouteManager.Instance.FindParallelPipePathsWithWaypointsAsync(pipes, waypoints, groupCenterGoal, startDirection, goalDirection, options);
        }
        //        onFinish: onFinish);
        //}

        ///// <summary>
        ///// 사용자 정의 옵션과 여유 간격을 사용하여 여러 배관의 waypoint 기반 AABB 경로를 탐색합니다.
        ///// 웨이포인트와 목표 좌표는 전체 AABB 중심 기준입니다.
        ///// </summary>
        ///// <param name="pipes">배관 시작 좌표와 직경 목록</param>
        ///// <param name="groupCenterWaypoints">전체 AABB 중심이 경유할 웨이포인트 목록</param>
        ///// <param name="groupCenterGoal">전체 AABB 중심의 최종 목표 좌표</param>
        ///// <param name="startDirection">초기 진행 방향</param>
        ///// <param name="options">A* 알고리즘의 사용자 정의 옵션</param>
        ///// <param name="clearance">전체 AABB 충돌 검사에 추가할 여유 간격</param>
        ///// <param name="onFinish">경로 탐색 완료 시 호출되는 콜백 함수</param>
        //public static void FindParallelPipePathsWithWaypoints(
        //    List<GroupPipeSpec> pipes,
        //    List<Vector3> groupCenterWaypoints,
        //    Vector3 groupCenterGoal,
        //    Int3 startDirection,
        //    PathFindOptions options,
        //    float clearance,
        //    Action<ParallelPipePathResult> onFinish)
        //{
        //    AutoRouteManager.Instance.FindParallelPipePathsWithWaypointsAsync(
        //        pipes,
        //        groupCenterWaypoints,
        //        groupCenterGoal,
        //        startDirection,
        //        options,
        //        clearance,
        //        onFinish);
        //}

        #endregion

        #region Pathfinding Control

        /// <summary>
        /// 현재 진행 중인 경로 탐색을 중단합니다.
        /// 비동기로 실행 중인 경로 탐색 작업이 있을 경우, 해당 작업은 CANCELLED 상태로 종료됩니다.
        /// </summary>
        public static void CancelPathfinding()
        {
            AutoRouteManager.Instance.CancelPathfinding();
        }

        #endregion

        #region Obstacle Management

        /// <summary>
        /// 정적 장애물 목록을 초기화합니다.
        /// 이 메서드는 변하지 않는 장애물을 설정할 때 사용됩니다.
        /// </summary>
        /// <param name="obstacleObbs">장애물의 OBB(Oriented Bounding Box) 리스트</param>
        /// <param name="onFinish">초기화 완료 시 호출되는 콜백 함수</param>
        public static void InitStaticObstacles(List<OBB> obstacleObbs, Action onFinish)
        {
            ObstacleManager.Instance.InitStaticObstaclesAsync(obstacleObbs, onFinish);
        }

        /// <summary>
        /// 정적 장애물 목록을 비동기로 초기화합니다. 완료 시 Task를 반환합니다.
        /// </summary>
        /// <param name="obstacleObbs">장애물의 OBB(Oriented Bounding Box) 리스트</param>
        /// <returns>초기화 완료를 나타내는 Task</returns>
        public static async Task InitStaticObstaclesAsync(List<OBB> obstacleObbs)
        {
            await ObstacleManager.Instance.InitStaticObstaclesAsync(obstacleObbs);
        }

        /// <summary>
        /// 단일 동적 장애물을 추가합니다.
        /// </summary>
        /// <param name="obstacle">추가할 장애물의 OBB(Oriented Bounding Box)</param>
        /// <param name="onFinish">추가 완료 시 호출되는 콜백 함수</param>
        public static void AddDynamicObstacle(OBB obstacle, Action onFinish)
        {
            ObstacleManager.Instance.AddDynamicObstacleAsync(obstacle, onFinish);
        }

        /// <summary>
        /// 여러 동적 장애물을 일괄 추가합니다.
        /// </summary>
        /// <param name="obstacles">추가할 장애물들의 OBB(Oriented Bounding Box) 리스트</param>
        /// <param name="onFinish">추가 완료 시 호출되는 콜백 함수</param>
        public static void AddDynamicObstacles(List<OBB> obstacles, Action onFinish)
        {
            ObstacleManager.Instance.AddDynamicObstaclesAsync(obstacles, onFinish);
        }

        /// <summary>
        /// 여러 동적 장애물을 비동기로 추가합니다. 완료 시 Task를 반환합니다.
        /// </summary>
        /// <param name="obstacles"></param>
        /// <returns></returns>
        public static async Task AddDynamicObstaclesAsync(List<OBB> obstacles)
        {
            await ObstacleManager.Instance.AddDynamicObstaclesAsync(obstacles);
        }

        /// <summary>
        /// 모든 동적 장애물을 제거합니다. 정적 장애물은 유지됩니다.
        /// </summary>
        public static void ClearDynamicObstacles()
        {
            ObstacleManager.Instance.ClearDynamicObstacles();
        }

        /// <summary>
        /// 모든 장애물을 제거합니다. 정적 및 동적 장애물 모두 초기화됩니다.
        /// </summary>
        public static void ClearObstacles()
        {
            ObstacleManager.Instance.ClearObstacles();
        }

        #endregion

        #region Tree Setting

        /// <summary>
        /// Octree 및 BVH(Bounding Volume Hierarchy) 트리 설정을 일괄 변경합니다.
        /// 이 설정들은 장애물 관리와 충돌 검사의 성능에 영향을 줍니다.
        /// </summary>
        /// <param name="octreeMinLeafSize">Octree의 최소 리프 노드 크기 (작을수록 세밀하지만 메모리 사용량 증가)</param>
        /// <param name="octreeMaxDepth">Octree의 최대 깊이 (클수록 세밀하지만 성능 저하 가능)</param>
        /// <param name="bvhThreshold">BVH 사용 임계값 (장애물 개수가 이 값 이상일 때 BVH 사용)</param>
        public static void SetTreeSetting(float octreeMinLeafSize, int octreeMaxDepth, int bvhThreshold)
        {
            AutoRouteModuleSetting.octreeMinLeafSize = octreeMinLeafSize;
            AutoRouteModuleSetting.octreeMaxDepth = octreeMaxDepth;
            AutoRouteModuleSetting.bvhThreshold = bvhThreshold;
        }

        /// <summary>
        /// Octree의 최소 리프 노드 크기를 설정합니다.
        /// </summary>
        /// <param name="size">최소 리프 노드 크기 (작을수록 세밀한 공간 분할)</param>
        public static void SetOctreeMinLeafSize(float size)
        {
            AutoRouteModuleSetting.octreeMinLeafSize = size;
        }

        /// <summary>
        /// Octree의 최대 깊이를 설정합니다.
        /// </summary>
        /// <param name="depth">최대 깊이 (높을수록 더 세밀하게 공간을 분할)</param>
        public static void SetOctreeMaxDepth(int depth)
        {
            AutoRouteModuleSetting.octreeMaxDepth = depth;
        }

        /// <summary>
        /// BVH 사용 임계값을 설정합니다.
        /// 장애물 개수가 이 값 이상일 때 BVH를 사용하여 충돌 검사를 최적화합니다.
        /// </summary>
        /// <param name="threshold">BVH 사용 임계값 (장애물 개수)</param>
        public static void SetBvhThreshold(int threshold)
        {
            AutoRouteModuleSetting.bvhThreshold = threshold;
        }

        #endregion
    }
}
