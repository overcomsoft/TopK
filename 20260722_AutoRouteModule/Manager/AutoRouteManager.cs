using AutoRouteModule.Core;
using System;
using System.Collections.Generic;
using System.Numerics;
using System.Threading.Tasks;

namespace AutoRouteModule
{
    internal class AutoRouteManager
    {
        private static AutoRouteManager? _instance;
        public static AutoRouteManager Instance => _instance ??= new AutoRouteManager();

        private readonly GridAStar3D _astar = new GridAStar3D();
        private readonly BoundAStar3D_V2 _boundAstar = new BoundAStar3D_V2();

        #region Public API

        public async void FindPathAsync(
            Vector3 start,
            Vector3 goal,
            DirectionType startDirection,
            DirectionType goalDirection,
            float diameter,
            PathFindOptions? options = null,
             Action<PathResult>? onFinish = null
             )
        {
            PathResult result = await Task.Run(() => RunPathfinding(new Vector3(start.X, start.Y, start.Z), new Vector3(goal.X, goal.Y, goal.Z), startDirection, goalDirection, diameter, null, options));

            GC.Collect();
            onFinish?.Invoke(result);
        }

        public async Task<PathResult> FindPathAsync(
            Vector3 start,
            Vector3 goal,
            DirectionType startDirection,
            DirectionType goalDirection,    
            float diameter,
            PathFindOptions? options = null)
        {
            PathResult result = await Task.Run(() => RunPathfinding(new Vector3(start.X, start.Y, start.Z), new Vector3(goal.X, goal.Y, goal.Z), startDirection, goalDirection, diameter, null, options));

            GC.Collect();
            return result;
        }

        public async void FindPathAsync(
            Vector3 start,
            Vector3 goal,
            DirectionType startDirection,
            DirectionType goalDirection,
            float diameter,
            List<Vector3> waypoints,
            PathFindOptions? options = null,
            Action<PathResult>? onFinish = null)
        {
            PathResult result = await Task.Run(() => RunPathfinding(new Vector3(start.X, start.Y, start.Z), new Vector3(goal.X, goal.Y, goal.Z), startDirection, goalDirection, diameter, waypoints, options));
            GC.Collect();
            onFinish?.Invoke(result);
        }

        public async Task<PathResult> FindPathAsync(
            Vector3 start,
            Vector3 goal,
            DirectionType startDirection,
            DirectionType goalDirection,
            float diameter,
            List<Vector3> waypoints,
            PathFindOptions? options = null)
        {
            PathResult result = await Task.Run(() => RunPathfinding(new Vector3(start.X, start.Y, start.Z), new Vector3(goal.X, goal.Y, goal.Z), startDirection, goalDirection, diameter, waypoints, options));
            GC.Collect();
            return result;
        }

        public async void FindParallelPipePathsAsync(
            List<GroupPipeSpec> pipes,
            Vector3 groupCenterGoal,
            DirectionType startDirection,
            DirectionType goalDirection,
            PathFindOptions? options = null,
            //float clearance = 0f,
            Action<GroupPathFindResult>? onFinish = null)
        {
            GroupPathFindResult result = await Task.Run(() =>
                _boundAstar.FindPipePaths(pipes, groupCenterGoal, startDirection, goalDirection, options));

            onFinish?.Invoke(result);
        }

        public async Task<GroupPathFindResult> FindParallelPipePathsAsync(
          List<GroupPipeSpec> pipes,
          Vector3 groupCenterGoal,
          DirectionType startDirection,
          DirectionType goalDirection,  
          PathFindOptions? options = null)
        {
                return await Task.Run(() => _boundAstar.FindPipePaths(pipes, groupCenterGoal, startDirection, goalDirection, options));
        }

        public async void FindParallelPipePathsWithWaypointsAsync(
            List<GroupPipeSpec> pipes,
            List<Vector3> waypoints,
            Vector3 groupCenterGoal,
            DirectionType startDirection,
            DirectionType goalDirection,
            PathFindOptions? options = null,
            Action<GroupPathFindResult>? onFinish = null)
        {
            GroupPathFindResult result = await Task.Run(() =>
                _boundAstar.FindPipePathsWithWaypoints(
                    pipes,
                    waypoints,
                    groupCenterGoal,
                    startDirection,
                    goalDirection,
                    options));

            onFinish?.Invoke(result);
        }

        public async Task<GroupPathFindResult> FindParallelPipePathsWithWaypointsAsync(
            List<GroupPipeSpec> pipes,
            List<Vector3> waypoints,
            Vector3 groupCenterGoal,
            DirectionType startDirection,
            DirectionType goalDirection,
            PathFindOptions? options = null)
        {
            return await Task.Run(() =>
                _boundAstar.FindPipePathsWithWaypoints(
                    pipes,
                    waypoints,
                    groupCenterGoal,
                    startDirection,
                    goalDirection,
                    options));
        }

        #endregion


        #region Pathfinding

        private PathResult RunPathfinding(Vector3 start, Vector3 goal, DirectionType startDirection, DirectionType goalDirection, float voxelSize, List<Vector3>? waypoints, PathFindOptions? options = null)
        {
            if(goalDirection != DirectionType.None)
            {
                if(waypoints == null)                
                    waypoints = new List<Vector3>();

                Vector3 prevGoalPos = goal - Directions.GetDirection(goalDirection).ToVector3() * voxelSize;

                waypoints.Add(prevGoalPos);
            }


            if(waypoints != null && waypoints.Count > 0)
            {
                return _astar.FindPathWithWaypoints(
                    start,
                    waypoints,
                    goal,
                    startDirection,
                    //goalDirection,
                    voxelSize,
                    options);
            }
            else
            {

                return _astar.FindPath(
                        start,
                        goal,
                        startDirection,
                        //goalDirection,
                        voxelSize,
                        options);
            }
        }



        #endregion


        #region Cancellation

        /// <summary>
        /// 현재 진행 중인 경로 탐색을 중단합니다.
        /// </summary>
        public void CancelPathfinding()
        {
            _astar.CancelPathfinding();
        }

        #endregion
    }
}
