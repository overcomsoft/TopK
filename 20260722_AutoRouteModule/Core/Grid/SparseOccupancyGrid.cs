using System.Collections.Generic;
using System.Numerics;

namespace AutoRouteModule.Core
{
    public class SparseOccupancyGrid
    {
        private readonly Dictionary<Vector3, bool> _cache = new Dictionary<Vector3, bool>();
        private readonly Vector3 _startPoint;
        private readonly Vector3 _cellSize;
        private readonly float _gridSize;

        public float GridSize => _gridSize;
        public float Clearance => _gridSize * 1.1f;

        public SparseOccupancyGrid(Vector3 startPoint, float gridSize)
        {
            _startPoint = startPoint;
            _gridSize = gridSize;

            //_cellSize = new Vector3(_gridSize);
            _cellSize = new Vector3(Clearance);
        }


        public AABB GetVoxelAABB(Vector3 worldPos)
        {
            return AABB.FromCenterSize(worldPos, _cellSize);
        }


        public bool IsBlocked(Vector3 worldPos)
        {
            if (_cache.TryGetValue(worldPos, out bool cached))
            {
                return cached;
            }

            bool blocked = CheckCollision(worldPos);
            _cache[worldPos] = blocked;
            return blocked;
        }

        private bool CheckCollision(Vector3 worldPos)
        {
            AABB voxelAABB = GetVoxelAABB(worldPos);

            return ObstacleManager.Instance.CheckCollision(voxelAABB);
        }

        public void ClearCache()
        {
            _cache.Clear();
        }

        public int GetCacheSize()
        {
            return _cache.Count;
        }
    }
}


