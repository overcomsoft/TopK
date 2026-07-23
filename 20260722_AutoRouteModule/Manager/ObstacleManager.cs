
using AutoRouteModule.Core;
using AutoRouteModule.Log;
using AutoRouteModule.Utils;
using System;
using System.Collections.Generic;
using System.Threading.Tasks;

namespace AutoRouteModule
{
    public class ObstacleManager
    {
        private static ObstacleManager? _instance;
        public static ObstacleManager Instance => _instance ??= new ObstacleManager();

        private List<Obstacle> _staticObstacles = new List<Obstacle>();
        private List<Obstacle> _dynamicObstacles = new List<Obstacle>();
        private CoarseOctree? _staticOctree;
        private CoarseOctree? _dynamicOctree;

        public IReadOnlyList<Obstacle> StaticObstacles => _staticObstacles;
        public IReadOnlyList<Obstacle> DynamicObstacles => _dynamicObstacles;
        public CoarseOctree? StaticOctree => _staticOctree;
        public CoarseOctree? DynamicOctree => _dynamicOctree;

        private readonly List<int> _queryBuffer = new List<int>();



        public async void InitStaticObstaclesAsync(List<OBB> obstacleObbs, Action onFinish)
        {
            await Task.Run(() =>
            {
                UpdateObstacles(obstacleObbs, ref _staticObstacles, ref _staticOctree);               
            });

            onFinish?.Invoke();
        }

        public async Task InitStaticObstaclesAsync(List<OBB> obstacleObbs)
        {
            await Task.Run(() =>
            {
                UpdateObstacles(obstacleObbs, ref _staticObstacles, ref _staticOctree);
            });
        }

        public async void AddDynamicObstacleAsync(OBB obstacle, Action onFinish)
        {
            await Task.Run(() =>
            {
                Obstacle newObstacle = CreateObstacle(obstacle);
                _dynamicObstacles.Add(newObstacle);

                if (_dynamicOctree == null)
                    _dynamicOctree = new CoarseOctree(_dynamicObstacles);
                else
                    _dynamicOctree.AddObstacle(newObstacle);

                RebuildDynamicOctree();                
            });

            onFinish?.Invoke();

        }

        public async void AddDynamicObstaclesAsync(List<OBB> obstacles, Action onFinish)
        {
            await AddDynamicObstaclesAsync(obstacles);

            onFinish?.Invoke();
        }

        public async Task AddDynamicObstaclesAsync(List<OBB> obstacles)
        {
            await Task.Run(() =>
            {
                List<Obstacle> newObstacles = new List<Obstacle>();
                foreach (var obb in obstacles)
                {
                    Obstacle newObstacle = CreateObstacle(obb);

                    _dynamicObstacles.Add(newObstacle);
                    newObstacles.Add(newObstacle);
                }

                if (_dynamicOctree == null)
                    _dynamicOctree = new CoarseOctree(_dynamicObstacles);
                else
                    _dynamicOctree.AddObstacles(newObstacles);

                RebuildDynamicOctree();
            });
        }

        public bool CheckCollision(AABB aabb)
        {
            _queryBuffer.Clear();
            if (_staticOctree != null)
                _staticOctree.Query(aabb, _queryBuffer);
            if (_dynamicOctree != null)
                _dynamicOctree.Query(aabb, _queryBuffer);

            return _queryBuffer.Count > 0;
        }

        private void RebuildDynamicOctree()
        {
            if (_dynamicObstacles.Count > 0)
            {
                if(_dynamicOctree == null)
                    _dynamicOctree = new CoarseOctree(_dynamicObstacles);
                else
                    _dynamicOctree.Build();
            }               
            else
            {
                ClearDynamicObstacles();
            }
        }



        private void UpdateObstacles(List<OBB> obstacleOBBs, ref List<Obstacle> obstacles, ref CoarseOctree? octree)
        {
            obstacles.Clear();

            for (int i = 0; i < obstacleOBBs.Count; i++)
            {
                obstacles.Add(CreateObstacle(obstacleOBBs[i]));
            }

            if (octree == null)
                octree = new CoarseOctree(obstacles);
            else
                octree.Build();
        }


        private static Obstacle CreateObstacle(OBB obb)
        {
            AABB aabb = ObbUtility.CalculateAABBFromOBB(obb);
            return new Obstacle
            {
                WorldOBB = obb,
                WorldAABB = aabb
            };
        }


        public void ClearDynamicObstacles()
        {
            _dynamicObstacles.Clear();
            _dynamicOctree = null;
        }

        public void ClearObstacles()
        {
            _staticObstacles.Clear();
            _dynamicObstacles.Clear();
            _staticOctree = null;
            _dynamicOctree = null;
        }


    }
}