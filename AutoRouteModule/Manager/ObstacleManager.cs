
using AutoRouteModule.Core;
using AutoRouteModule.Log;
using AutoRouteModule.Utils;
using System;
using System.Collections.Generic;
using System.Threading;
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
        private readonly ReaderWriterLockSlim _obstacleLock = new ReaderWriterLockSlim();
        private int _revision;

        public IReadOnlyList<Obstacle> StaticObstacles => _staticObstacles;
        public IReadOnlyList<Obstacle> DynamicObstacles => _dynamicObstacles;
        public CoarseOctree? StaticOctree => _staticOctree;
        public CoarseOctree? DynamicOctree => _dynamicOctree;

        public int Revision => Volatile.Read(ref _revision);



        public async void InitStaticObstaclesAsync(List<OBB> obstacleObbs, Action onFinish)
        {
            await Task.Run(() =>
            {
                _obstacleLock.EnterWriteLock();
                try
                {
                    UpdateObstacles(obstacleObbs, ref _staticObstacles, ref _staticOctree);
                    IncrementRevision();
                }
                finally
                {
                    _obstacleLock.ExitWriteLock();
                }
            });

            onFinish?.Invoke();
        }

        public async Task InitStaticObstaclesAsync(List<OBB> obstacleObbs)
        {
            await Task.Run(() =>
            {
                _obstacleLock.EnterWriteLock();
                try
                {
                    UpdateObstacles(obstacleObbs, ref _staticObstacles, ref _staticOctree);
                    IncrementRevision();
                }
                finally
                {
                    _obstacleLock.ExitWriteLock();
                }
            });
        }

        public async void AddDynamicObstacleAsync(OBB obstacle, Action onFinish)
        {
            await Task.Run(() =>
            {
                _obstacleLock.EnterWriteLock();
                try
                {
                    Obstacle newObstacle = CreateObstacle(obstacle);
                    _dynamicObstacles.Add(newObstacle);
                    RebuildDynamicOctree();
                    IncrementRevision();
                }
                finally
                {
                    _obstacleLock.ExitWriteLock();
                }
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
                _obstacleLock.EnterWriteLock();
                try
                {
                    foreach (var obb in obstacles)
                        _dynamicObstacles.Add(CreateObstacle(obb));
                    RebuildDynamicOctree();
                    IncrementRevision();
                }
                finally
                {
                    _obstacleLock.ExitWriteLock();
                }
            });
        }

        public bool CheckCollision(AABB aabb)
        {
            var queryBuffer = new List<int>();
            _obstacleLock.EnterReadLock();
            try
            {
                if (_staticOctree != null)
                    _staticOctree.Query(aabb, queryBuffer);
                if (_dynamicOctree != null)
                    _dynamicOctree.Query(aabb, queryBuffer);
                return queryBuffer.Count > 0;
            }
            finally
            {
                _obstacleLock.ExitReadLock();
            }
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
                _dynamicObstacles.Clear();
                _dynamicOctree = null;
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
            _obstacleLock.EnterWriteLock();
            try
            {
                _dynamicObstacles.Clear();
                _dynamicOctree = null;
                IncrementRevision();
            }
            finally
            {
                _obstacleLock.ExitWriteLock();
            }
        }

        public void ClearObstacles()
        {
            _obstacleLock.EnterWriteLock();
            try
            {
                _staticObstacles.Clear();
                _dynamicObstacles.Clear();
                _staticOctree = null;
                _dynamicOctree = null;
                IncrementRevision();
            }
            finally
            {
                _obstacleLock.ExitWriteLock();
            }
        }

        private void IncrementRevision()
        {
            Interlocked.Increment(ref _revision);
        }


    }
}
