using AutoRouteModule.Utils;
using System.Collections.Generic;
using System.Numerics;

namespace AutoRouteModule.Core
{
    internal class BVHNode
    {
        public AABB Bounds;

        public int Left = -1;
        public int Right = -1;

        public int Start;
        public int Count;

        public bool IsLeaf => Left < 0 && Right < 0;
    }

    internal class BVH
    {
        private readonly List<BVHNode> _nodes = new List<BVHNode>();
        private readonly List<int> _indices = new List<int>();
        private readonly List<Obstacle> _obstacles;

        private const int LeafSize = 8;

        public BVH(List<Obstacle> obstacles, List<int> obstacleIndices)
        {
            _obstacles = obstacles;
            _indices.AddRange(obstacleIndices);

            if (_indices.Count > 0)
                BuildNode(0, _indices.Count);
        }

        public void Query(AABB queryBounds, List<int> result)
        {
            if (_nodes.Count == 0)
                return;

            QueryNode(0, queryBounds, result);
        }

        private int BuildNode(int start, int count)
        {
            AABB bounds = _obstacles[_indices[start]].WorldAABB;

            for (int i = start + 1; i < start + count; i++)
            {
                bounds = AABB.Union(bounds, _obstacles[_indices[i]].WorldAABB);
            }

            int nodeIndex = _nodes.Count;

            BVHNode node = new BVHNode
            {
                Bounds = bounds,
                Start = start,
                Count = count
            };

            _nodes.Add(node);

            if (count <= LeafSize)
            {
                return nodeIndex;
            }

            Vector3 size = bounds.Size;

            int axis;
            if (size.X >= size.Y && size.X >= size.Z)
                axis = 0;
            else if (size.Y >= size.X && size.Y >= size.Z)
                axis = 1;
            else
                axis = 2;

            _indices.Sort(start, count, Comparer<int>.Create((a, b) =>
            {
                float ca = GetAxis(_obstacles[a].WorldAABB.Center, axis);
                float cb = GetAxis(_obstacles[b].WorldAABB.Center, axis);
                return ca.CompareTo(cb);
            }));

            int half = count / 2;

            int left = BuildNode(start, half);
            int right = BuildNode(start + half, count - half);

            _nodes[nodeIndex].Left = left;
            _nodes[nodeIndex].Right = right;

            return nodeIndex;
        }

        private void QueryNode(int nodeIndex, AABB queryBounds, List<int> result)
        {
            BVHNode node = _nodes[nodeIndex];

            if (!node.Bounds.Intersects(queryBounds))
                return;

            if (node.IsLeaf)
            {
                for (int i = node.Start; i < node.Start + node.Count; i++)
                {
                    int obstacleIndex = _indices[i];

                    if (CollisionUtility.AabbVsObb(queryBounds, _obstacles[obstacleIndex].WorldOBB))
                        result.Add(obstacleIndex);
                }

                return;
            }

            QueryNode(node.Left, queryBounds, result);
            QueryNode(node.Right, queryBounds, result);
        }

        private static float GetAxis(Vector3 v, int axis)
        {
            return axis switch
            {
                0 => v.X,
                1 => v.Y,
                _ => v.Z
            };
        }
    }
}
