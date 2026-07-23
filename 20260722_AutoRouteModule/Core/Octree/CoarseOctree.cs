using AutoRouteModule.Utils;
using System.Collections.Generic;
using System.Numerics;

namespace AutoRouteModule.Core
{
    public class CoarseOctree
    {
        private class Node
        {
            public AABB Bounds;
            public Node[]? Children;
            public List<int> ObstacleIndices = new List<int>();

            public bool IsLeaf => Children == null;
            public BVH? Bvh;
        }

        private readonly Node _root;
        private List<Obstacle> _obstacles;

        private readonly float _minLeafSize;
        private readonly int _maxDepth;
        private readonly int _bvhThreshold;


        public AABB RootAABB => _root.Bounds;

        public CoarseOctree(List<Obstacle> obstacles)
        {
            AABB worldBounds = ComputeWorldBoundsFromObstacles(obstacles);
            _root = new Node { Bounds = worldBounds };
            _obstacles = obstacles;
            _minLeafSize = AutoRouteModuleSetting.octreeMinLeafSize;
            _maxDepth = AutoRouteModuleSetting.octreeMaxDepth;
            _bvhThreshold = AutoRouteModuleSetting.bvhThreshold;

            Build();
        }

        public CoarseOctree(
            AABB worldBounds,
            List<Obstacle> obstacles,
            float minLeafSize,
            int maxDepth,
            int bvhThreshold)
        {
            _root = new Node { Bounds = worldBounds };
            _obstacles = obstacles;
            _minLeafSize = minLeafSize;
            _maxDepth = maxDepth;
            _bvhThreshold = bvhThreshold;

            Build();
        }
        

        public void Build()
        {
            _root.Bounds = ComputeWorldBoundsFromObstacles(_obstacles);
            _root.ObstacleIndices.Clear();
            _root.Children = null;
            _root.Bvh = null;

            for (int i = 0; i < _obstacles.Count; i++)
            {
                Insert(_root, i, 0);
            }

            BuildLeafBvhs(_root);
        }

        public void AddObstacle(Obstacle newObstacle)
        {
            _obstacles.Add(newObstacle);
            Build();
        }

        public void AddObstacles(List<Obstacle> newObstacles)
        {
            _obstacles.AddRange(newObstacles);
            Build();
        }

        public void Query(AABB queryBounds, List<int> result)
        {
            QueryNode(_root, queryBounds, result);
        }

        private void Insert(Node node, int obstacleIndex, int depth)
        {
            AABB obstacleBounds = _obstacles[obstacleIndex].WorldAABB;

            if (!node.Bounds.Intersects(obstacleBounds))
                return;

            Vector3 size = node.Bounds.Size;

            bool canSplit =
                depth < _maxDepth &&
                size.X > _minLeafSize &&
                size.Y > _minLeafSize &&
                size.Z > _minLeafSize;

            if (!canSplit)
            {
                node.ObstacleIndices.Add(obstacleIndex);
                return;
            }

            EnsureChildren(node);

            bool insertedToChild = false;

            for (int i = 0; i < 8; i++)
            {
                if(node.Children == null)
                    continue;
        
                Node child = node.Children[i];

                // 완전히 포함되는 child에만 넣음
                // 여러 child 중복 등록을 피하기 위한 방식
                if (child.Bounds.Contains(obstacleBounds))
                {
                    Insert(child, obstacleIndex, depth + 1);
                    insertedToChild = true;
                    break;
                }
            }

            // child 하나에 완전히 안 들어가면 현재 node에 보관
            // 긴 배관, 큰 설비 중복 등록 방지
            if (!insertedToChild)
            {
                node.ObstacleIndices.Add(obstacleIndex);
            }
        }

        private void EnsureChildren(Node node)
        {
            if (node.Children != null)
                return;

            node.Children = new Node[8];

            Vector3 min = node.Bounds.Min;
            Vector3 max = node.Bounds.Max;
            Vector3 center = node.Bounds.Center;

            int index = 0;

            for (int z = 0; z < 2; z++)
                for (int y = 0; y < 2; y++)
                    for (int x = 0; x < 2; x++)
                    {
                        Vector3 childMin = new Vector3(
                            x == 0 ? min.X : center.X,
                            y == 0 ? min.Y : center.Y,
                            z == 0 ? min.Z : center.Z
                        );

                        Vector3 childMax = new Vector3(
                            x == 0 ? center.X : max.X,
                            y == 0 ? center.Y : max.Y,
                            z == 0 ? center.Z : max.Z
                        );

                        node.Children[index++] = new Node
                        {
                            Bounds = new AABB
                            {
                                Min = childMin,
                                Max = childMax
                            }
                        };
                    }
        }

        private void BuildLeafBvhs(Node node)
        {
            if (node.ObstacleIndices.Count >= _bvhThreshold)
            {
                node.Bvh = new BVH(_obstacles, node.ObstacleIndices);
            }

            if (node.Children == null)
                return;

            for (int i = 0; i < 8; i++)
                BuildLeafBvhs(node.Children[i]);
        }

        private void QueryNode(Node node, AABB queryBounds, List<int> result)
        {
            if (!node.Bounds.Intersects(queryBounds))
                return;

            if (node.Bvh != null)
            {
                node.Bvh.Query(queryBounds, result);
            }
            else
            {
                for (int i = 0; i < node.ObstacleIndices.Count; i++)
                {
                    int obstacleIndex = node.ObstacleIndices[i];

                    if (CollisionUtility.AabbVsObb(queryBounds, _obstacles[obstacleIndex].WorldOBB))
                        result.Add(obstacleIndex);
                }
            }

            if (node.Children == null)
                return;

            for (int i = 0; i < 8; i++)
                QueryNode(node.Children[i], queryBounds, result);
        }

        private static AABB ComputeWorldBoundsFromObstacles(List<Obstacle> obstacles)
        {
            if (obstacles == null || obstacles.Count == 0)
            {
                return AABB.FromCenterSize(Vector3.Zero, new Vector3(1f, 1f, 1f));
            }

            AABB result = obstacles[0].WorldAABB;

            for (int i = 1; i < obstacles.Count; i++)
            {
                result = AABB.Union(result, obstacles[i].WorldAABB);
            }

            return result;
        }
    }
}