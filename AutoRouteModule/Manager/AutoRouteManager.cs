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

        private readonly object _activeSearchesLock = new object();
        private readonly HashSet<GridAStar3D> _activeGridSearches = new HashSet<GridAStar3D>();
        private readonly HashSet<BoundAStar3D_V2> _activeBoundSearches = new HashSet<BoundAStar3D_V2>();

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
            PathResult result = await FindPathCoreAsync(start, goal, startDirection, goalDirection, diameter, null, options);
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
            return await FindPathCoreAsync(start, goal, startDirection, goalDirection, diameter, null, options);
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
            PathResult result = await FindPathCoreAsync(start, goal, startDirection, goalDirection, diameter, waypoints, options);
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
            return await FindPathCoreAsync(start, goal, startDirection, goalDirection, diameter, waypoints, options);
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
            GroupPathFindResult result = await FindParallelCoreAsync(
                pipes, null, groupCenterGoal, startDirection, goalDirection, options);
            onFinish?.Invoke(result);
        }

        public async Task<GroupPathFindResult> FindParallelPipePathsAsync(
          List<GroupPipeSpec> pipes,
          Vector3 groupCenterGoal,
          DirectionType startDirection,
          DirectionType goalDirection,  
          PathFindOptions? options = null)
        {
            return await FindParallelCoreAsync(
                pipes, null, groupCenterGoal, startDirection, goalDirection, options);
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
            GroupPathFindResult result = await FindParallelCoreAsync(
                pipes, waypoints, groupCenterGoal, startDirection, goalDirection, options);
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
            return await FindParallelCoreAsync(
                pipes, waypoints, groupCenterGoal, startDirection, goalDirection, options);
        }

        #endregion


        #region Pathfinding

        private async Task<PathResult> FindPathCoreAsync(
            Vector3 start,
            Vector3 goal,
            DirectionType startDirection,
            DirectionType goalDirection,
            float voxelSize,
            List<Vector3>? waypoints,
            PathFindOptions? options)
        {
            var astar = new GridAStar3D();
            Register(astar);
            try
            {
                return await Task.Run(() => RunPathfinding(
                    astar, start, goal, startDirection, goalDirection, voxelSize, waypoints, options));
            }
            finally
            {
                Unregister(astar);
            }
        }

        private async Task<GroupPathFindResult> FindParallelCoreAsync(
            List<GroupPipeSpec> pipes,
            List<Vector3>? waypoints,
            Vector3 groupCenterGoal,
            DirectionType startDirection,
            DirectionType goalDirection,
            PathFindOptions? options)
        {
            var astar = new BoundAStar3D_V2();
            Register(astar);
            try
            {
                return await Task.Run(() => waypoints == null
                    ? astar.FindPipePaths(pipes, groupCenterGoal, startDirection, goalDirection, options)
                    : astar.FindPipePathsWithWaypoints(
                        pipes, waypoints, groupCenterGoal, startDirection, goalDirection, options));
            }
            finally
            {
                Unregister(astar);
            }
        }

        private PathResult RunPathfinding(
            GridAStar3D astar,
            Vector3 start,
            Vector3 goal,
            DirectionType startDirection,
            DirectionType goalDirection,
            float voxelSize,
            List<Vector3>? waypoints,
            PathFindOptions? options = null)
        {
            if(goalDirection != DirectionType.None)
            {
                if(waypoints == null)                
                    waypoints = new List<Vector3>();
                else
                    waypoints = new List<Vector3>(waypoints);

                Vector3 prevGoalPos = goal - Directions.GetDirection(goalDirection).ToVector3() * voxelSize;

                waypoints.Add(prevGoalPos);
            }


            if(waypoints != null && waypoints.Count > 0)
            {
                return astar.FindPathWithWaypoints(
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

                return astar.FindPath(
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
            GridAStar3D[] gridSearches;
            BoundAStar3D_V2[] boundSearches;
            lock (_activeSearchesLock)
            {
                gridSearches = new GridAStar3D[_activeGridSearches.Count];
                _activeGridSearches.CopyTo(gridSearches);
                boundSearches = new BoundAStar3D_V2[_activeBoundSearches.Count];
                _activeBoundSearches.CopyTo(boundSearches);
            }

            foreach (GridAStar3D search in gridSearches)
                search.CancelPathfinding();
            foreach (BoundAStar3D_V2 search in boundSearches)
                search.CancelPathfinding();
        }

        private void Register(GridAStar3D search)
        {
            lock (_activeSearchesLock)
                _activeGridSearches.Add(search);
        }

        private void Unregister(GridAStar3D search)
        {
            lock (_activeSearchesLock)
                _activeGridSearches.Remove(search);
        }

        private void Register(BoundAStar3D_V2 search)
        {
            lock (_activeSearchesLock)
                _activeBoundSearches.Add(search);
        }

        private void Unregister(BoundAStar3D_V2 search)
        {
            lock (_activeSearchesLock)
                _activeBoundSearches.Remove(search);
        }

        #endregion
    }
}
