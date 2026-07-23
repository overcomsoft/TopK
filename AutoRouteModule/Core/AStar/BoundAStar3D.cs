//using AutoRouteModule.Debug;
//using System;
//using System.Collections.Generic;
//using System.Numerics;

//namespace AutoRouteModule.Core
//{
//    public class BoundPathResult : PathResult
//    {
//        public List<Vector3>? CenterPath;
//        public List<Int3>? Directions;
//        public bool GoalReached;
//        public Vector3 EndPosition;
//    }

//    public struct ParallelPipeSpec
//    {
//        public Vector3 Start;
//        public float Diameter;

//        public ParallelPipeSpec(Vector3 start, float diameter)
//        {
//            Start = start;
//            Diameter = diameter;
//        }
//    }

//    public class ParallelPipePathResult : BoundPathResult
//    {
//        public List<List<Vector3>>? PipeWorldPaths;
//        public AABB StartBounds;
//    }

//    public class BoundAStar3D
//    {
//        private readonly struct SearchState : IEquatable<SearchState>
//        {
//            public readonly AABB Aabb;
//            public readonly Int3 Direction;

//            public SearchState(AABB aabb, Int3 direction)
//            {
//                Aabb = aabb;
//                Direction = direction;
//            }

//            // AABB와 진행 방향이 모두 같으면 같은 탐색 상태로 취급합니다.
//            public bool Equals(SearchState other)
//            {
//                return Aabb.Equals(other.Aabb) && Direction.Equals(other.Direction);
//            }

//            public override bool Equals(object obj)
//            {
//                return obj is SearchState other && Equals(other);
//            }

//            public override int GetHashCode()
//            {
//                unchecked
//                {
//                    int hash = 17;
//                    hash = hash * 31 + Aabb.GetHashCode();
//                    hash = hash * 31 + Direction.GetHashCode();
//                    return hash;
//                }
//            }
//        }

//        private struct SearchNode
//        {
//            public int GCost;
//            public int HCost;
//            public int FCost => GCost + HCost;
//            public SearchState Parent;
//            public float StraightDistance;
//        }

//        private readonly struct BoundTransition
//        {
//            public readonly AABB Bounds;
//            public readonly float CostDistance;
//            public readonly float StraightDistance;

//            public BoundTransition(AABB bounds, float costDistance, float straightDistance)
//            {
//                Bounds = bounds;
//                CostDistance = costDistance;
//                StraightDistance = straightDistance;
//            }
//        }

//        private static readonly Int3[] Directions =
//        {
//            new Int3( 1, 0, 0),
//            new Int3(-1, 0, 0),
//            new Int3( 0, 1, 0),
//            new Int3( 0,-1, 0),
//            new Int3( 0, 0, 1),
//            new Int3( 0, 0,-1),
//        };

//        private const int DefaultCapacity = 1024;
//        private const float Epsilon = 0.0001f;

//        private readonly StateHeap _openSet = new StateHeap(DefaultCapacity);
//        private readonly Dictionary<SearchState, SearchNode> _nodes = new Dictionary<SearchState, SearchNode>(DefaultCapacity);
//        private readonly HashSet<SearchState> _closed = new HashSet<SearchState>();

//        private readonly List<int> _queryBuffer = new List<int>(DefaultCapacity);
//        private SearchContext _context;

//        private struct SearchContext
//        {
//            public Vector3 StartCenter;
//            public Vector3 GoalCenter;
//            public Vector3 BaseSize;
//            public int BaseLongAxis;
//            public float LongLength;
//            public float Clearance;
//            public float MinStraightDistance;
//            public int TurnPenalty;
//            public int VerticalPenalty;
//            public int HorizontalPenalty;
//            public int MaxSearchNodes;
//            public float HeuristicWeight;
//            public Int3 StartDirection;
//        }

//        private struct SegmentPath
//        {
//            public List<Vector3> Centers;
//            public List<Int3> Directions;
//            public List<SearchState> States;
//        }

//        /// <summary>
//        /// 시작 AABB에서 목표 중심까지 그룹 배관 전체를 감싸는 박스를 이동시키며 경로를 탐색합니다.
//        /// </summary>
//        public BoundPathResult FindPath(
//            AABB startBounds,
//            Vector3 goalCenter,
//            Int3 startDirection,
//            PathFindOptions? options = null,
//            float clearance = 0f)
//        {
//            _context = BuildContext(startBounds, goalCenter, startDirection, options, clearance);

//            AABB startAabb = CreateBound(_context.StartCenter, _context.StartDirection);
//            if (IsAreaBlocked(startAabb))
//                return new BoundPathResult { ResultCode = RESULT_CODES.FAIL_TO_START_POINT, EndPosition = _context.StartCenter };

//            SearchResult searchResult = FindPathInternal(startAabb);
//            if (!searchResult.Found || !searchResult.EndState.HasValue)
//                return new BoundPathResult { ResultCode = RESULT_CODES.FAIL_TO_PATHFIND, EndPosition = _context.StartCenter };

//            return BuildSuccessResult(searchResult);
//        }

//        /// <summary>
//        /// 여러 배관을 하나의 시작 AABB로 묶어 경로를 찾고, 각 배관별 월드 경로로 변환합니다.
//        /// </summary>
//        public ParallelPipePathResult FindPipePaths(
//            List<ParallelPipeSpec> pipes,
//            Vector3 groupCenterGoal,
//            Int3 startDirection,
//            PathFindOptions? options = null,
//            float clearance = 0f)
//        {
//            if (pipes == null || pipes.Count == 0)
//                return new ParallelPipePathResult { ResultCode = RESULT_CODES.FAIL_TO_START_POINT };

//            if (!TryBuildStartBounds(pipes, out AABB startBounds))
//                return new ParallelPipePathResult { ResultCode = RESULT_CODES.FAIL_TO_START_POINT };

//            BoundPathResult boundResult = FindPath(startBounds, groupCenterGoal, startDirection, options, clearance);
//            ParallelPipePathResult result = CopyBoundResult(boundResult, startBounds);

//            if (boundResult.ResultCode != RESULT_CODES.SUCCESS || boundResult.WorldPath == null)
//                return result;

//            result.PipeWorldPaths = BuildPipeWorldPaths(boundResult.WorldPath, pipes, startBounds.Center);
//            return result;
//        }

//        /// <summary>
//        /// waypoint들을 순서대로 경유하도록 그룹 AABB 경로를 구간별로 탐색합니다.
//        /// </summary>
//        public BoundPathResult FindPathWithWaypoints(
//            AABB startBounds,
//            List<Vector3> waypoints,
//            Vector3 goalCenter,
//            Int3 startDirection,
//            PathFindOptions? options = null,
//            float clearance = 0f)
//        {
//            _context = BuildContext(startBounds, goalCenter, startDirection, options, clearance);
//            SearchContext baseContext = _context;

//            List<Vector3> centerPath = new List<Vector3>(DefaultCapacity);
//            List<Int3> directions = new List<Int3>(DefaultCapacity);
//            HashSet<AABB> visitedBounds = new HashSet<AABB>();

//            Vector3 currentCenter = baseContext.StartCenter;
//            Int3 currentDirection = baseContext.StartDirection;
//            int totalPoints = 2 + (waypoints?.Count ?? 0);

//            for (int i = 0; i < totalPoints - 1; i++)
//            {
//                Vector3 currentGoal = waypoints != null && i < waypoints.Count
//                    ? waypoints[i]
//                    : goalCenter;

//                _context = BuildSegmentContext(baseContext, currentCenter, currentGoal, currentDirection);

//                AABB startAabb = CreateBound(_context.StartCenter, _context.StartDirection);
//                if (i == 0 && IsAreaBlocked(startAabb))
//                    return new BoundPathResult { ResultCode = RESULT_CODES.FAIL_TO_START_POINT, EndPosition = currentCenter };

//                SearchResult segmentResult = FindPathInternal(startAabb, visitedBounds);
//                if (!segmentResult.Found || !segmentResult.EndState.HasValue)
//                    return new BoundPathResult { ResultCode = RESULT_CODES.FAIL_TO_PATHFIND, EndPosition = currentCenter };

//                SegmentPath segmentPath = BuildSegmentPath(segmentResult);
//                if (segmentPath.Centers.Count == 0 || segmentPath.States.Count == 0)
//                    return new BoundPathResult { ResultCode = RESULT_CODES.FAIL_TO_PATHFIND, EndPosition = currentCenter };

//                AppendSegmentPath(centerPath, directions, segmentPath);
//                AddVisitedBounds(visitedBounds, segmentPath.States);

//                SearchState lastState = segmentPath.States[segmentPath.States.Count - 1];
//                currentCenter = lastState.Aabb.Center;
//                currentDirection = lastState.Direction;
//            }

//            if (centerPath.Count == 0)
//                return new BoundPathResult { ResultCode = RESULT_CODES.FAIL_TO_PATHFIND, EndPosition = baseContext.StartCenter };

//            List<Vector3> routePath = BuildOrthogonalRoute(centerPath, directions);
//            return new BoundPathResult
//            {
//                ResultCode = RESULT_CODES.SUCCESS,
//                WorldPath = routePath,
//                CenterPath = centerPath,
//                Directions = directions,
//                GoalReached = true,
//                EndPosition = centerPath[centerPath.Count - 1]
//            };
//        }

//        /// <summary>
//        /// 여러 배관을 하나의 시작 AABB로 묶고 waypoint들을 경유하는 개별 배관 경로를 계산합니다.
//        /// </summary>
//        public ParallelPipePathResult FindPipePathsWithWaypoints(
//            List<ParallelPipeSpec> pipes,
//            List<Vector3> groupCenterWaypoints,
//            Vector3 groupCenterGoal,
//            Int3 startDirection,
//            PathFindOptions? options = null,
//            float clearance = 0f)
//        {
//            if (pipes == null || pipes.Count == 0)
//                return new ParallelPipePathResult { ResultCode = RESULT_CODES.FAIL_TO_START_POINT };

//            if (!TryBuildStartBounds(pipes, out AABB startBounds))
//                return new ParallelPipePathResult { ResultCode = RESULT_CODES.FAIL_TO_START_POINT };

//            BoundPathResult boundResult = FindPathWithWaypoints(
//                startBounds,
//                groupCenterWaypoints,
//                groupCenterGoal,
//                startDirection,
//                options,
//                clearance);

//            ParallelPipePathResult result = CopyBoundResult(boundResult, startBounds);

//            if (boundResult.ResultCode != RESULT_CODES.SUCCESS || boundResult.WorldPath == null)
//                return result;

//            result.PipeWorldPaths = BuildPipeWorldPaths(boundResult.WorldPath, pipes, startBounds.Center);
//            return result;
//        }

//        // 한 번의 탐색에 필요한 옵션, 시작/목표 정보, AABB 회전 기준을 구성합니다.
//        private static SearchContext BuildContext(
//            AABB startBounds,
//            Vector3 goalCenter,
//            Int3 startDirection,
//            PathFindOptions? options,
//            float clearance)
//        {
//            SearchContext context = new SearchContext
//            {
//                StartCenter = startBounds.Center,
//                GoalCenter = goalCenter,
//                BaseSize = startBounds.Size,
//                Clearance = Math.Max(0f, clearance),
//                TurnPenalty = ClampOption(options?.TurnPenalty, AutoRouteModuleDefine.TURN_PENALTY_DEFAULT, AutoRouteModuleDefine.TURN_PENALTY_MAX),
//                VerticalPenalty = ClampOption(options?.VerticalPenalty, AutoRouteModuleDefine.VERTICAL_PENALTY_DEFAULT, AutoRouteModuleDefine.VERTICAL_PENALTY_MAX),
//                HorizontalPenalty = ClampOption(options?.HorizontalPenalty, AutoRouteModuleDefine.HORIZONTAL_PENALTY_DEFAULT, AutoRouteModuleDefine.HORIZONTAL_PENALTY_MAX),
//                MaxSearchNodes = options?.MaxSearchNodes >= 0 ? options.Value.MaxSearchNodes : AutoRouteModuleDefine.MAX_SEARCH_NODES_DEFAULT,
//                HeuristicWeight = options?.HeuristicWeight > 0 ? Math.Max(1.0f, Math.Min(options.Value.HeuristicWeight, 3.0f)) : 1.0f,
//                MinStraightDistance = options?.MinStraightDistance >= 0 ? options.Value.MinStraightDistance : 0f
//            };

//            context.BaseLongAxis = GetDominantAxis(context.BaseSize);
//            context.LongLength = Math.Max(GetAxis(context.BaseSize, context.BaseLongAxis), Epsilon);
//            context.StartDirection = NormalizeAxisDirection(startDirection);

//            if (context.StartDirection.Equals(Int3.Zero))
//                context.StartDirection = AxisToDirection(context.BaseLongAxis, 1);

//            return context;
//        }

//        // 원본 AABB/옵션 설정은 유지하고, 현재 구간의 시작/목표/방향만 바꿉니다.
//        private static SearchContext BuildSegmentContext(
//            SearchContext baseContext,
//            Vector3 startCenter,
//            Vector3 goalCenter,
//            Int3 startDirection)
//        {
//            SearchContext context = baseContext;
//            context.StartCenter = startCenter;
//            context.GoalCenter = goalCenter;
//            context.StartDirection = NormalizeAxisDirection(startDirection);

//            if (context.StartDirection.Equals(Int3.Zero))
//                context.StartDirection = AxisToDirection(context.BaseLongAxis, 1);

//            return context;
//        }

//        // 옵션 값이 유효하면 최대값으로 제한하고, 유효하지 않으면 기본값을 사용합니다.
//        private static int ClampOption(int? value, int defaultValue, int maxValue)
//        {
//            return value >= 0 ? Math.Min(value.Value, maxValue) : defaultValue;
//        }

//        // A* 본 탐색을 수행하며, 각 상태는 이동 중인 AABB와 진행 방향을 함께 가집니다.
//        private SearchResult FindPathInternal(AABB startAabb)
//        {
//            return FindPathInternal(startAabb, null);
//        }

//        // A* 본 탐색을 수행하며, 각 상태는 이동 중인 AABB와 진행 방향을 함께 가집니다.
//        private SearchResult FindPathInternal(AABB startAabb, HashSet<AABB>? blockedBounds)
//        {
//            _nodes.Clear();
//            _closed.Clear();
//            _openSet.Clear();

//            SearchState invalid = CreateInvalidState();
//            SearchState startState = new SearchState(startAabb, _context.StartDirection);
//            SearchNode startNode = new SearchNode
//            {
//                Parent = invalid,
//                GCost = 0,
//                HCost = Heuristic(startState.Aabb.Center),
//                StraightDistance = GetStepDistance(_context.StartDirection)
//            };

//            _nodes[startState] = startNode;
//            _openSet.Add(startNode.FCost, startState);

//            int explored = 0;

//            while (_openSet.Count > 0)
//            {
//                if (_context.MaxSearchNodes > 0 && explored >= _context.MaxSearchNodes)
//                    return SearchResult.NotFound;

//                var current = _openSet.ExtractMin();
//                SearchState currentState = current.state;

//                if (_closed.Contains(currentState))
//                    continue;

//                _closed.Add(currentState);
//                explored++;

//                SearchNode currentNode = _nodes[currentState];

//                if (TryGetTerminal(currentState, currentNode, out SearchResult terminal))
//                    return terminal;

//                for (int i = 0; i < Directions.Length; i++)
//                {
//                    Int3 nextDirection = Directions[i];

//                    if (nextDirection.Equals(-currentState.Direction))
//                        continue;

//                    bool isTurn = !nextDirection.Equals(currentState.Direction);
//                    if (isTurn && currentNode.StraightDistance + Epsilon < _context.MinStraightDistance)
//                        continue;

//                    BoundTransition transition = BuildTransition(currentState.Aabb, currentState.Direction, nextDirection);
//                    AABB nextBound = transition.Bounds;
//                    SearchState nextState = new SearchState(nextBound, nextDirection);

//                    if (_closed.Contains(nextState))
//                        continue;

//                    if (blockedBounds != null && blockedBounds.Contains(nextState.Aabb))
//                        continue;

//                    Vector3 nextCenter = nextBound.Center;
//                    if (IsMoveBlocked(currentState.Aabb, nextBound))
//                        continue;

//                    int newGCost = currentNode.GCost + MoveCost(currentState.Direction, nextDirection, transition.CostDistance);
//                    bool isOpen = _nodes.TryGetValue(nextState, out SearchNode existingNode);

//                    if (!isOpen || newGCost < existingNode.GCost)
//                    {
//                        SearchNode nextNode = new SearchNode
//                        {
//                            Parent = currentState,
//                            GCost = newGCost,
//                            HCost = Heuristic(nextCenter),
//                            StraightDistance = isTurn ? transition.StraightDistance : currentNode.StraightDistance + transition.StraightDistance
//                        };

//                        if (isOpen)
//                            _openSet.UpdatePriority(nextState, nextNode.FCost);
//                        else
//                            _openSet.Add(nextNode.FCost, nextState);

//                        _nodes[nextState] = nextNode;
//                    }
//                }
//            }

//            return SearchResult.NotFound;
//        }

//        // 현재 상태에서 목표에 도달했거나, 한 스텝 이내로 목표까지 연결 가능한지 확인합니다.
//        private bool TryGetTerminal(SearchState state, SearchNode node, out SearchResult result)
//        {
//            Vector3 currentCenter = state.Aabb.Center;
//            Vector3 delta = _context.GoalCenter - currentCenter;

//            if (delta.LengthSquared() <= Epsilon * Epsilon)
//            {
//                result = SearchResult.Goal(state);
//                return true;
//            }

//            if (!TryBuildCompletionTail(currentCenter, state, node, out result))
//            {
//                result = SearchResult.NotFound;
//                return false;
//            }

//            return true;
//        }

//        // 목표까지 남은 축 이동을 tail 경로로 붙일 수 있는지 검사합니다.
//        private bool TryBuildCompletionTail(
//            Vector3 currentCenter,
//            SearchState state,
//            SearchNode node,
//            out SearchResult result)
//        {
//            List<CompletionSegment> segments = BuildCompletionSegments(currentCenter, state.Direction);
//            if (segments.Count == 0)
//            {
//                result = SearchResult.Goal(state);
//                return true;
//            }

//            List<Vector3> tail = new List<Vector3>(segments.Count);
//            List<Int3> tailDirections = new List<Int3>(segments.Count);
//            AABB from = state.Aabb;
//            Int3 fromDirection = state.Direction;
//            float straightDistance = node.StraightDistance;

//            for (int i = 0; i < segments.Count; i++)
//            {
//                CompletionSegment segment = segments[i];

//                if (segment.Distance + Epsilon < _context.MinStraightDistance)
//                {
//                    result = SearchResult.StopBeforeGoal(state, tail, tailDirections);
//                    return true;
//                }

//                bool isTurn = !segment.Direction.Equals(fromDirection);
//                if (isTurn && straightDistance + Epsilon < _context.MinStraightDistance)
//                {
//                    result = SearchResult.NotFound;
//                    return false;
//                }

//                BoundTransition fullTransition = BuildTransition(from, fromDirection, segment.Direction);
//                if (segment.Distance > fullTransition.StraightDistance + Epsilon)
//                {
//                    result = SearchResult.NotFound;
//                    return false;
//                }

//                BoundTransition transition = BuildTransition(from, fromDirection, segment.Direction, segment.Distance);
//                AABB to = transition.Bounds;
//                if (IsMoveBlocked(from, to))
//                {
//                    result = SearchResult.NotFound;
//                    return false;
//                }

//                tail.Add(to.Center);
//                tailDirections.Add(segment.Direction);
//                straightDistance = isTurn ? segment.Distance : straightDistance + segment.Distance;
//                fromDirection = segment.Direction;
//                from = to;
//            }

//            result = SearchResult.Goal(state, tail, tailDirections);
//            return true;
//        }

//        // 현재 중심에서 목표 중심까지 필요한 축별 이동을 현재 방향 우선 순서로 나눕니다.
//        private List<CompletionSegment> BuildCompletionSegments(Vector3 currentCenter, Int3 currentDirection)
//        {
//            List<CompletionSegment> segments = new List<CompletionSegment>(3);
//            Vector3 delta = _context.GoalCenter - currentCenter;
//            int currentAxis = DirectionToAxis(currentDirection);

//            AddCompletionSegmentForAxis(segments, delta, currentAxis);

//            for (int axis = 0; axis < 3; axis++)
//            {
//                if (axis == currentAxis)
//                    continue;

//                AddCompletionSegmentForAxis(segments, delta, axis);
//            }

//            return segments;
//        }

//        // 특정 축의 목표 잔여 이동량을 completion segment로 추가합니다.
//        private static void AddCompletionSegmentForAxis(List<CompletionSegment> segments, Vector3 delta, int axis)
//        {
//            float value = GetAxis(delta, axis);
//            float distance = Math.Abs(value);
//            if (distance <= Epsilon)
//                return;

//            segments.Add(new CompletionSegment
//            {
//                Direction = AxisToDirection(axis, value > 0f ? 1 : -1),
//                Distance = distance
//            });
//        }

//        // 이전 AABB와 다음 AABB가 지나가는 Union 영역에 장애물이 있는지 확인합니다.
//        private bool IsMoveBlocked(AABB from, AABB to)
//        {
//            AABB query = AABB.Union(from, to);
//            return IsAreaBlocked(query);
//        }

//        // 정적/동적 장애물 공간에서 해당 AABB 영역의 충돌 여부를 확인합니다.
//        private bool IsAreaBlocked(AABB queryBounds)
//        {
//            ObstacleManager obstacleManager = ObstacleManager.Instance;

//            if (IsOctreeAreaBlocked(obstacleManager.StaticOctree, queryBounds))
//                return true;

//            return IsOctreeAreaBlocked(obstacleManager.DynamicOctree, queryBounds);
//        }

//        // 단일 octree에서 query AABB와 겹치는 장애물이 있는지 검사합니다.
//        private bool IsOctreeAreaBlocked(CoarseOctree? octree, AABB queryBounds)
//        {
//            if (octree == null)
//                return false;

//            _queryBuffer.Clear();
//            octree.Query(queryBounds, _queryBuffer);

//            if (DebugInfo.DebugMode)
//                DebugInfo.AABBCheckRecord.Add(new DebugAABBRecordInfo { aabb = queryBounds, isOccupied = _queryBuffer.Count > 0 });

//            return _queryBuffer.Count > 0;
//        }

//        // 중심 좌표와 진행 방향을 기준으로 clearance가 반영된 AABB를 만듭니다.
//        private AABB CreateBound(Vector3 center, Int3 direction)
//        {
//            return AABB.FromCenterSize(center, GetClearedSizeForDirection(direction));
//        }

//        // 한 스텝 이동할 다음 AABB와 비용/직진거리 정보를 계산합니다.
//        private BoundTransition BuildTransition(AABB from, Int3 currentDirection, Int3 nextDirection)
//        {
//            Vector3 nextSize = GetClearedSizeForDirection(nextDirection);
//            return BuildTransition(from, currentDirection, nextDirection, GetForwardDistance(from, nextSize, nextDirection));
//        }

//        // 지정된 전진 거리만큼 AABB를 진행시키며, 회전이면 이전 방향 offset도 함께 적용합니다.
//        private BoundTransition BuildTransition(AABB from, Int3 currentDirection, Int3 nextDirection, float forwardDistance)
//        {
//            Vector3 nextSize = GetClearedSizeForDirection(nextDirection);
//            Vector3 offset = DirectionToVector(nextDirection) * forwardDistance;

//            // 회전 시에는 새 축으로만 밀지 않고 이전 진행축 offset도 더해 코너를 따라 이동시킵니다.
//            if (!nextDirection.Equals(currentDirection))
//            {
//                int currentAxis = DirectionToAxis(currentDirection);
//                float currentOffset = GetAxis(from.Size, currentAxis) * 0.5f - GetAxis(nextSize, currentAxis) * 0.5f;
//                offset += DirectionToVector(currentDirection) * currentOffset;
//            }

//            AABB nextBounds = AABB.FromCenterSize(from.Center + offset, nextSize);
//            return new BoundTransition(nextBounds, GetManhattanLength(offset), Math.Abs(forwardDistance));
//        }

//        // 현재 AABB와 다음 방향 AABB가 맞닿기 위해 필요한 새 진행축 이동 거리입니다.
//        private float GetForwardDistance(AABB from, Vector3 nextSize, Int3 nextDirection)
//        {
//            int axis = DirectionToAxis(nextDirection);
//            float fromHalf = Math.Max(GetAxis(from.Size, axis) * 0.5f, Epsilon * 0.5f);
//            float nextHalf = Math.Max(GetAxis(nextSize, axis) * 0.5f, Epsilon * 0.5f);
//            return fromHalf + nextHalf;
//        }

//        // 방향에 맞게 회전된 AABB 크기에 clearance를 더합니다.
//        private Vector3 GetClearedSizeForDirection(Int3 direction)
//        {
//            Vector3 size = GetSizeForDirection(direction);
//            if (_context.Clearance > 0f)
//                size += new Vector3(_context.Clearance * 2f, _context.Clearance * 2f, _context.Clearance * 2f);

//            return size;
//        }

//        // 시작 AABB의 긴 축을 현재 진행 방향 축으로 옮긴 크기를 계산합니다.
//        private Vector3 GetSizeForDirection(Int3 direction)
//        {
//            int targetAxis = DirectionToAxis(direction);
//            Vector3 size = _context.BaseSize;

//            if (targetAxis != _context.BaseLongAxis)
//            {
//                float targetSize = GetAxis(size, targetAxis);
//                SetAxis(ref size, targetAxis, _context.LongLength);
//                SetAxis(ref size, _context.BaseLongAxis, targetSize);
//            }

//            return size;
//        }

//        // 해당 방향으로 한 칸 직진할 때 사용하는 기준 길이를 계산합니다.
//        private float GetStepDistance(Int3 direction)
//        {
//            Int3 normalized = NormalizeAxisDirection(direction);
//            if (normalized.Equals(Int3.Zero))
//                normalized = AxisToDirection(_context.BaseLongAxis, 1);

//            int axis = DirectionToAxis(normalized);
//            return Math.Max(GetAxis(GetSizeForDirection(normalized), axis), Epsilon);
//        }

//        // 탐색 결과 상태와 completion tail을 최종 PathResult 형태로 변환합니다.
//        private BoundPathResult BuildSuccessResult(SearchResult searchResult)
//        {
//            if (!searchResult.EndState.HasValue)
//                return new BoundPathResult { ResultCode = RESULT_CODES.FAIL_TO_PATHFIND, EndPosition = _context.StartCenter };

//            SegmentPath segmentPath = BuildSegmentPath(searchResult);
//            List<Vector3> centerPath = segmentPath.Centers;
//            List<Int3> directions = segmentPath.Directions;

//            if (centerPath.Count == 0)
//                return new BoundPathResult { ResultCode = RESULT_CODES.FAIL_TO_PATHFIND, EndPosition = _context.StartCenter };

//            List<Vector3> routePath = BuildOrthogonalRoute(centerPath, directions);

//            return new BoundPathResult
//            {
//                ResultCode = RESULT_CODES.SUCCESS,
//                WorldPath = routePath,
//                CenterPath = centerPath,
//                Directions = directions,
//                GoalReached = searchResult.GoalReached,
//                EndPosition = centerPath[centerPath.Count - 1]
//            };
//        }

//        // SearchResult의 상태 경로와 tail을 center/direction/state 목록으로 펼칩니다.
//        private SegmentPath BuildSegmentPath(SearchResult searchResult)
//        {
//            if (!searchResult.EndState.HasValue)
//            {
//                return new SegmentPath
//                {
//                    Centers = new List<Vector3>(),
//                    Directions = new List<Int3>(),
//                    States = new List<SearchState>()
//                };
//            }

//            List<SearchState> reconstructedStates = ReconstructStates(searchResult.EndState.Value);
//            SegmentPath path = new SegmentPath
//            {
//                Centers = new List<Vector3>(reconstructedStates.Count + (searchResult.ExactTail?.Count ?? 0)),
//                Directions = new List<Int3>(reconstructedStates.Count + (searchResult.ExactTailDirections?.Count ?? 0)),
//                States = new List<SearchState>(reconstructedStates.Count + (searchResult.ExactTail?.Count ?? 0))
//            };

//            for (int i = 0; i < reconstructedStates.Count; i++)
//                AddSegmentState(path, reconstructedStates[i]);

//            if (searchResult.ExactTail != null)
//            {
//                for (int i = 0; i < searchResult.ExactTail.Count; i++)
//                {
//                    if (searchResult.ExactTailDirections == null || i >= searchResult.ExactTailDirections.Count)
//                        break;

//                    SearchState tailState = new SearchState(
//                        CreateBound(searchResult.ExactTail[i], searchResult.ExactTailDirections[i]),
//                        searchResult.ExactTailDirections[i]);

//                    AddSegmentState(path, tailState);
//                }
//            }

//            return path;
//        }

//        // 중복 중심점을 제외하고 segment 경로에 상태를 추가합니다.
//        private static void AddSegmentState(SegmentPath path, SearchState state)
//        {
//            if (path.Centers.Count > 0 && IsSamePoint(path.Centers[path.Centers.Count - 1], state.Aabb.Center))
//                return;

//            path.Centers.Add(state.Aabb.Center);
//            path.Directions.Add(state.Direction);
//            path.States.Add(state);
//        }

//        // waypoint 구간 경로를 전체 경로에 붙이며 구간 시작 중복점을 제거합니다.
//        private static void AppendSegmentPath(List<Vector3> centers, List<Int3> directions, SegmentPath segmentPath)
//        {
//            int startIndex = centers.Count > 0 && segmentPath.Centers.Count > 0 && IsSamePoint(centers[centers.Count - 1], segmentPath.Centers[0])
//                ? 1
//                : 0;

//            for (int i = startIndex; i < segmentPath.Centers.Count; i++)
//            {
//                centers.Add(segmentPath.Centers[i]);
//                directions.Add(segmentPath.Directions[i]);
//            }
//        }

//        // 이전 구간에서 지나간 상태를 다음 구간에서 재사용하지 않도록 기록합니다.
//        private static void AddVisitedBounds(HashSet<AABB> visitedBounds, List<SearchState> states)
//        {
//            for (int i = 0; i < states.Count - 1; i++)
//                visitedBounds.Add(states[i].Aabb);
//        }

//        // 여러 배관의 시작점과 직경을 모두 포함하는 시작 AABB를 만듭니다.
//        private static bool TryBuildStartBounds(List<ParallelPipeSpec> pipes, out AABB startBounds)
//        {
//            startBounds = new AABB();
//            bool initialized = false;

//            for (int i = 0; i < pipes.Count; i++)
//            {
//                if (pipes[i].Diameter <= 0f)
//                    return false;

//                Vector3 size = new Vector3(pipes[i].Diameter, pipes[i].Diameter, pipes[i].Diameter);
//                AABB pipeBounds = AABB.FromCenterSize(pipes[i].Start, size);
//                startBounds = initialized ? AABB.Union(startBounds, pipeBounds) : pipeBounds;
//                initialized = true;
//            }

//            return initialized;
//        }

//        // BoundPathResult의 공통 결과 필드를 ParallelPipePathResult로 복사합니다.
//        private static ParallelPipePathResult CopyBoundResult(BoundPathResult source, AABB startBounds)
//        {
//            return new ParallelPipePathResult
//            {
//                ResultCode = source.ResultCode,
//                WorldPath = source.WorldPath != null ? new List<Vector3>(source.WorldPath) : null,
//                RawPath = source.RawPath != null ? new List<Int3>(source.RawPath) : null,
//                SimplifiedPath = source.SimplifiedPath != null ? new List<Int3>(source.SimplifiedPath) : null,
//                CenterPath = source.CenterPath != null ? new List<Vector3>(source.CenterPath) : null,
//                Directions = source.Directions != null ? new List<Int3>(source.Directions) : null,
//                GoalReached = source.GoalReached,
//                EndPosition = source.EndPosition,
//                StartBounds = startBounds
//            };
//        }

//        // 그룹 AABB 중심 경로를 각 배관 시작 offset 기준의 개별 배관 경로로 변환합니다.
//        private static List<List<Vector3>> BuildPipeWorldPaths(
//            List<Vector3> boundWorldPath,
//            List<ParallelPipeSpec> pipes,
//            Vector3 startBoundsCenter)
//        {
//            List<List<Vector3>> result = new List<List<Vector3>>(pipes.Count);

//            for (int i = 0; i < pipes.Count; i++)
//            {
//                Vector3 offset = pipes[i].Start - startBoundsCenter;
//                List<Vector3> pipePath = new List<Vector3>(boundWorldPath.Count);

//                for (int j = 0; j < boundWorldPath.Count; j++)
//                    pipePath.Add(boundWorldPath[j] + offset);

//                if (pipePath.Count > 0)
//                    pipePath[0] = pipes[i].Start;

//                result.Add(pipePath);
//            }

//            return result;
//        }

//        // 종료 상태에서 parent를 따라 시작 상태까지 거슬러 올라가 상태 경로를 복원합니다.
//        private List<SearchState> ReconstructStates(SearchState endState)
//        {
//            List<SearchState> states = new List<SearchState>(256);
//            SearchState current = endState;
//            SearchState invalid = CreateInvalidState();

//            while (!current.Equals(invalid))
//            {
//                states.Add(current);

//                if (!_nodes.ContainsKey(current))
//                    break;

//                current = _nodes[current].Parent;
//            }

//            states.Reverse();
//            return states;
//        }

//        // parent 추적 종료를 표시하기 위한 sentinel 상태를 만듭니다.
//        private static SearchState CreateInvalidState()
//        {
//            Vector3 invalidPoint = new Vector3(float.MinValue, float.MinValue, float.MinValue);
//            return new SearchState(
//                new AABB
//                {
//                    Min = invalidPoint,
//                    Max = invalidPoint
//                },
//                Int3.Zero);
//        }

//        // 회전 전이로 인해 대각선처럼 기록된 중심 경로를 직교 경로로 후처리합니다.
//        private List<Vector3> BuildOrthogonalRoute(List<Vector3> centerPath, List<Int3> directions)
//        {
//            List<Vector3> route = new List<Vector3>();
//            if (centerPath.Count == 0)
//                return route;

//            route.Add(centerPath[0]);

//            if (centerPath.Count == 1)
//                return route;

//            for (int i = 1; i < centerPath.Count; i++)
//            {
//                Int3 previousDirection = GetRouteDirection(directions, i - 1, centerPath[i] - centerPath[i - 1]);
//                Int3 segmentDirection = GetRouteDirection(directions, i, centerPath[i] - centerPath[i - 1]);

//                // 회전 전이는 중심 좌표의 두 축이 동시에 바뀔 수 있어 교차점을 추가합니다.
//                if (!previousDirection.Equals(segmentDirection))
//                {
//                    Vector3 turnPoint = GetTurnPoint(centerPath[i - 1], previousDirection, centerPath[i]);
//                    AddTurnPointIfNeeded(route, centerPath[i - 1], turnPoint, centerPath[i]);
//                }

//                AddPointIfDifferent(route, centerPath[i]);
//            }

//            return route;
//        }

//        // 교차점이 이전/다음 점과 겹치지 않을 때만 route에 추가합니다.
//        private static void AddTurnPointIfNeeded(List<Vector3> route, Vector3 previousPoint, Vector3 turnPoint, Vector3 nextPoint)
//        {
//            if (IsSamePoint(turnPoint, previousPoint) || IsSamePoint(turnPoint, nextPoint))
//                return;

//            AddPointIfDifferent(route, turnPoint);
//        }

//        // directions에 기록된 방향을 우선 사용하고, 없으면 좌표 delta로 방향을 추정합니다.
//        private static Int3 GetRouteDirection(List<Int3> directions, int index, Vector3 fallbackDelta)
//        {
//            if (index >= 0 && index < directions.Count)
//                return NormalizeAxisDirection(directions[index]);

//            return NormalizeAxisDirection(WorldDeltaToDirection(fallbackDelta));
//        }

//        // 이동 거리와 방향 전환/수직/수평 패널티를 합산해 A* G 비용을 계산합니다.
//        private int MoveCost(Int3 previousDirection, Int3 nextDirection, float distance)
//        {
//            int cost = WorldCost(distance);

//            if (!previousDirection.Equals(nextDirection))
//                cost += _context.TurnPenalty;

//            cost += nextDirection.y != 0 ? _context.VerticalPenalty : _context.HorizontalPenalty;
//            return cost;
//        }

//        // 현재 중심에서 목표 중심까지의 Manhattan 거리 기반 휴리스틱 비용입니다.
//        private int Heuristic(Vector3 position)
//        {
//            Vector3 center = position;
//            Vector3 delta = _context.GoalCenter - center;
//            float manhattan = Math.Abs(delta.X) + Math.Abs(delta.Y) + Math.Abs(delta.Z);
//            return (int)(WorldCost(manhattan) * _context.HeuristicWeight);
//        }

//        // 이전 방향 축을 기준으로 직교 경로의 회전 교차점을 계산합니다.
//        private static Vector3 GetTurnPoint(Vector3 previousPoint, Int3 previousDirection, Vector3 targetPoint)
//        {
//            Vector3 result = previousPoint;
//            int previousAxis = DirectionToAxis(previousDirection);
//            SetAxis(ref result, previousAxis, GetAxis(targetPoint, previousAxis));
//            return result;
//        }

//        // 월드 좌표 delta에서 가장 큰 축을 기준으로 단위 방향을 추정합니다.
//        private static Int3 WorldDeltaToDirection(Vector3 delta)
//        {
//            float ax = Math.Abs(delta.X);
//            float ay = Math.Abs(delta.Y);
//            float az = Math.Abs(delta.Z);

//            if (ax >= ay && ax >= az && ax > Epsilon)
//                return new Int3(delta.X > 0f ? 1 : -1, 0, 0);

//            if (ay >= ax && ay >= az && ay > Epsilon)
//                return new Int3(0, delta.Y > 0f ? 1 : -1, 0);

//            if (az > Epsilon)
//                return new Int3(0, 0, delta.Z > 0f ? 1 : -1);

//            return Int3.Zero;
//        }

//        // 임의의 Int3 방향을 6방향 단위 축 방향으로 정규화합니다.
//        private static Int3 NormalizeAxisDirection(Int3 direction)
//        {
//            if (direction.x != 0)
//                return new Int3(direction.x > 0 ? 1 : -1, 0, 0);

//            if (direction.y != 0)
//                return new Int3(0, direction.y > 0 ? 1 : -1, 0);

//            if (direction.z != 0)
//                return new Int3(0, 0, direction.z > 0 ? 1 : -1);

//            return Int3.Zero;
//        }

//        // Int3 방향을 Vector3 방향으로 변환합니다.
//        private static Vector3 DirectionToVector(Int3 direction)
//        {
//            return new Vector3(direction.x, direction.y, direction.z);
//        }

//        // 방향 벡터가 속한 축 index를 반환합니다.
//        private static int DirectionToAxis(Int3 direction)
//        {
//            if (direction.x != 0)
//                return 0;

//            if (direction.y != 0)
//                return 1;

//            return 2;
//        }

//        // 축 index와 부호로 단위 축 방향을 만듭니다.
//        private static Int3 AxisToDirection(int axis, int sign)
//        {
//            int s = sign >= 0 ? 1 : -1;
//            if (axis == 0)
//                return new Int3(s, 0, 0);

//            if (axis == 1)
//                return new Int3(0, s, 0);

//            return new Int3(0, 0, s);
//        }

//        // Vector3에서 절대값이 가장 큰 축 index를 반환합니다.
//        private static int GetDominantAxis(Vector3 value)
//        {
//            float ax = Math.Abs(value.X);
//            float ay = Math.Abs(value.Y);
//            float az = Math.Abs(value.Z);

//            if (ax >= ay && ax >= az)
//                return 0;

//            if (ay >= ax && ay >= az)
//                return 1;

//            return 2;
//        }

//        // 축 index에 해당하는 Vector3 성분을 읽습니다.
//        private static float GetAxis(Vector3 value, int axis)
//        {
//            if (axis == 0)
//                return value.X;

//            if (axis == 1)
//                return value.Y;

//            return value.Z;
//        }

//        // 축 index에 해당하는 Vector3 성분을 씁니다.
//        private static void SetAxis(ref Vector3 value, int axis, float axisValue)
//        {
//            if (axis == 0)
//                value.X = axisValue;
//            else if (axis == 1)
//                value.Y = axisValue;
//            else
//                value.Z = axisValue;
//        }

//        // 월드 거리 단위를 정수 비용 단위로 변환합니다.
//        private static int WorldCost(float distance)
//        {
//            return (int)Math.Round(distance * 1000f);
//        }

//        // Vector3의 축별 절대값 합을 계산합니다.
//        private static float GetManhattanLength(Vector3 value)
//        {
//            return Math.Abs(value.X) + Math.Abs(value.Y) + Math.Abs(value.Z);
//        }

//        // 마지막 점과 충분히 다를 때만 점을 추가합니다.
//        private static void AddPointIfDifferent(List<Vector3> points, Vector3 point)
//        {
//            if (points.Count == 0 || !IsSamePoint(points[points.Count - 1], point))
//                points.Add(point);
//        }

//        // 부동소수점 오차를 고려해 두 점이 같은지 판단합니다.
//        private static bool IsSamePoint(Vector3 a, Vector3 b)
//        {
//            return Vector3.DistanceSquared(a, b) <= Epsilon * Epsilon;
//        }

//        private struct SearchResult
//        {
//            public bool Found;
//            public bool GoalReached;
//            public SearchState? EndState;
//            public List<Vector3>? ExactTail;
//            public List<Int3>? ExactTailDirections;

//            // 경로를 찾지 못한 결과입니다.
//            public static SearchResult NotFound => new SearchResult { Found = false };

//            // 현재 상태에서 목표에 도달한 결과를 만듭니다.
//            public static SearchResult Goal(SearchState state)
//            {
//                return new SearchResult
//                {
//                    Found = true,
//                    GoalReached = true,
//                    EndState = state
//                };
//            }

//            // 목표까지 정확히 이어지는 tail 경로를 포함한 성공 결과를 만듭니다.
//            public static SearchResult Goal(SearchState state, List<Vector3> exactTail, List<Int3> exactTailDirections)
//            {
//                return new SearchResult
//                {
//                    Found = true,
//                    GoalReached = true,
//                    EndState = state,
//                    ExactTail = exactTail,
//                    ExactTailDirections = exactTailDirections
//                };
//            }

//            // MinStraightDistance 조건 때문에 목표 직전에 멈춘 결과를 만듭니다.
//            public static SearchResult StopBeforeGoal(SearchState state, List<Vector3> exactTail, List<Int3> exactTailDirections)
//            {
//                return new SearchResult
//                {
//                    Found = true,
//                    GoalReached = false,
//                    EndState = state,
//                    ExactTail = exactTail,
//                    ExactTailDirections = exactTailDirections
//                };
//            }
//        }

//        // 목표까지 한 축으로 이어 붙일 수 있는 후보 이동 구간입니다.
//        private struct CompletionSegment
//        {
//            public Int3 Direction;
//            public float Distance;
//        }

//        // SearchState의 F cost 우선순위를 관리하는 최소 힙입니다.
//        private class StateHeap
//        {
//            private struct HeapNode
//            {
//                public int FCost;
//                public SearchState State;

//                public HeapNode(int fCost, SearchState state)
//                {
//                    FCost = fCost;
//                    State = state;
//                }
//            }

//            private readonly List<HeapNode> _heap;
//            private readonly Dictionary<SearchState, int> _stateToIndex;

//            public int Count => _heap.Count;

//            // 예상 용량으로 힙 저장소와 state-index 조회 테이블을 초기화합니다.
//            public StateHeap(int capacity)
//            {
//                _heap = new List<HeapNode>(capacity);
//                _stateToIndex = new Dictionary<SearchState, int>(capacity);
//            }

//            // 힙과 조회 테이블을 비웁니다.
//            public void Clear()
//            {
//                _heap.Clear();
//                _stateToIndex.Clear();
//            }

//            // 새 상태를 F cost 기준으로 힙에 추가합니다.
//            public void Add(int fCost, SearchState state)
//            {
//                HeapNode node = new HeapNode(fCost, state);
//                _heap.Add(node);
//                int index = _heap.Count - 1;
//                _stateToIndex[state] = index;
//                HeapifyUp(index);
//            }

//            // 가장 낮은 F cost 상태를 꺼냅니다.
//            public (int fCost, SearchState state) ExtractMin()
//            {
//                HeapNode min = _heap[0];
//                _stateToIndex.Remove(min.State);

//                int lastIndex = _heap.Count - 1;
//                if (lastIndex > 0)
//                {
//                    _heap[0] = _heap[lastIndex];
//                    _stateToIndex[_heap[0].State] = 0;
//                }

//                _heap.RemoveAt(lastIndex);

//                if (_heap.Count > 0)
//                    HeapifyDown(0);

//                return (min.FCost, min.State);
//            }

//            // 이미 열린 상태의 우선순위를 갱신합니다.
//            public void UpdatePriority(SearchState state, int newFCost)
//            {
//                if (!_stateToIndex.TryGetValue(state, out int index))
//                    return;

//                int oldFCost = _heap[index].FCost;
//                _heap[index] = new HeapNode(newFCost, state);

//                if (newFCost < oldFCost)
//                    HeapifyUp(index);
//                else if (newFCost > oldFCost)
//                    HeapifyDown(index);
//            }

//            // 자식보다 작은 F cost가 될 때까지 노드를 위로 올립니다.
//            private void HeapifyUp(int index)
//            {
//                while (index > 0)
//                {
//                    int parent = (index - 1) / 2;
//                    if (_heap[parent].FCost <= _heap[index].FCost)
//                        break;

//                    Swap(index, parent);
//                    index = parent;
//                }
//            }

//            // 부모보다 큰 F cost가 될 때까지 노드를 아래로 내립니다.
//            private void HeapifyDown(int index)
//            {
//                while (true)
//                {
//                    int left = index * 2 + 1;
//                    int right = index * 2 + 2;
//                    int smallest = index;

//                    if (left < _heap.Count && _heap[left].FCost < _heap[smallest].FCost)
//                        smallest = left;

//                    if (right < _heap.Count && _heap[right].FCost < _heap[smallest].FCost)
//                        smallest = right;

//                    if (smallest == index)
//                        break;

//                    Swap(index, smallest);
//                    index = smallest;
//                }
//            }

//            // 힙 노드 두 개의 위치와 index 테이블을 함께 교환합니다.
//            private void Swap(int a, int b)
//            {
//                HeapNode temp = _heap[a];
//                _heap[a] = _heap[b];
//                _heap[b] = temp;

//                _stateToIndex[_heap[a].State] = a;
//                _stateToIndex[_heap[b].State] = b;
//            }
//        }
//    }
//}
