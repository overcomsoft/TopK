
using AutoRouteModule.Log;
using AutoRouteModule.Utils;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Numerics;
using static System.Net.Mime.MediaTypeNames;

namespace AutoRouteModule.Core
{
    public class GroupPathFindResult
    {
        public RESULT_CODES ResultCode;
        public List<List<Vector3>>? WorldPath;
        public List<Vector3>? SimpleCenterPath;
    }

    public struct GroupPipeSpec
    {
        public Vector3 Start;
        public float Diameter;

        public GroupPipeSpec(Vector3 start, float diameter)
        {
            Start = start;
            Diameter = diameter;
        }
    }

    public class BoundAStar3D_V2
    {          

        private struct SearchNode
        {
            public float GCost;
            public float HCost;
            public float FCost => GCost + HCost;
            public BoundBox? Parent;
            public BoundBox CurrentState;
            public float StraightDistance;
            public BoundBox? SegmentStartState;
        }


        private Vector3 _goalCenter;
        private DirectionType _goalDirection;
        private PathFindOptions _options;

        private float _shortestLength;
        private float _longestLength;

        private const int DefaultCapacity = 1024;
        private const float Epsilon = 0.0001f;


        // 중단 플래그 (스레드 안전)
        private volatile bool _isCancelled = false;

        // 마지막 경로 탐색 실패 이유
        private RESULT_CODES _lastFailureReason = RESULT_CODES.FAIL_TO_PATHFIND;

        private readonly MinHeap<BoundBox> _openSet = new MinHeap<BoundBox>(DefaultCapacity);
        private readonly Dictionary<BoundBox, SearchNode> _nodes = new Dictionary<BoundBox, SearchNode>(DefaultCapacity);
        private readonly HashSet<BoundBox> _closed = new HashSet<BoundBox>();

        private readonly List<BoundBox> _tempPathBuffer = new List<BoundBox>(DefaultCapacity);
        private readonly List<BoundBox> _pathBuffer = new List<BoundBox>(DefaultCapacity);
        private readonly List<BoundBox> _waypointPathBuffer = new List<BoundBox>(DefaultCapacity);
        private readonly List<Vector3> _worldPathBuffer = new List<Vector3>(DefaultCapacity);


        private List<Vector3> _pipeOffsets = new List<Vector3>();
        private readonly List<AABB> _previousPathAABBs = new List<AABB>();

        private AABB _goalBox;

        public void CancelPathfinding()
        {
            _isCancelled = true;
        }


        public GroupPathFindResult FindPipePaths(
            List<GroupPipeSpec> pipes,
            Vector3 groupCenterGoal,
            DirectionType startDirection,
            DirectionType goalDirection,
            PathFindOptions? options = null)
        {
            if (pipes == null || pipes.Count == 0)
                return new GroupPathFindResult { ResultCode = RESULT_CODES.FAIL_TO_START_POINT };

            if (!TryBuildStartBounds(pipes, out AABB startBounds))
                return new GroupPathFindResult { ResultCode = RESULT_CODES.FAIL_TO_START_POINT };


            // Create Start
            DirectionType startUpDirection = GetUpDirection(startBounds, startDirection);
            BoundBox startState = new BoundBox(startBounds, startDirection, startUpDirection);

            _goalDirection = goalDirection;

            // Init Pipe Offsets
            InitPipeOffsets(pipes, startBounds);

            return FindPath(startState, groupCenterGoal, options);
        }

        /// <summary>
        /// 웨이포인트를 경유하여 여러 파이프의 경로를 찾습니다.
        /// 이전에 지나간 경로의 세그먼트와 충돌을 체크합니다.
        /// </summary>
        public GroupPathFindResult FindPipePathsWithWaypoints(
            List<GroupPipeSpec> pipes,
            List<Vector3> waypoints,
            Vector3 groupCenterGoal,
            DirectionType startDirection,
            DirectionType goalDirection,
            PathFindOptions? options = null)
        {
            if (pipes == null || pipes.Count == 0)
                return new GroupPathFindResult { ResultCode = RESULT_CODES.FAIL_TO_START_POINT };

            if (!TryBuildStartBounds(pipes, out AABB startBounds))
                return new GroupPathFindResult { ResultCode = RESULT_CODES.FAIL_TO_START_POINT };

            DirectionType startUpDirection = GetUpDirection(startBounds, startDirection);
            BoundBox startState = new BoundBox(startBounds, startDirection, startUpDirection);

            _goalDirection = goalDirection;

            // Init Pipe Offsets
            InitPipeOffsets(pipes, startBounds);

            GroupPathFindResult result = FindPathWithWaypoints(startState, waypoints, groupCenterGoal, options);
            _previousPathAABBs.Clear();


            return result;
        }

        /// <summary>
        /// 웨이포인트를 경유하는 경로를 찾습니다.
        /// 이전 구간의 경로 세그먼트와 충돌을 체크하여 재사용을 방지합니다.
        /// </summary>
        private GroupPathFindResult FindPathWithWaypoints(
            BoundBox startState,
            List<Vector3> waypoints,
            Vector3 goalCenter,
            PathFindOptions? options = null)
        {
            _isCancelled = false;

            _longestLength = GetLongestSize(startState.Bound.Size);
            _shortestLength = GetShortestSize(startState.Bound.Size);

            _options = options ?? PathFindOptions.Default;

            // 전체 경로 지점 개수 계산
            int totalPoints = 2 + (waypoints?.Count ?? 0);

            // 재사용 가능한 버퍼 초기화
            _waypointPathBuffer.Clear();
            if (_waypointPathBuffer.Capacity < DefaultCapacity)
                _waypointPathBuffer.Capacity = DefaultCapacity;

            // 이전 구간의 경로 AABB 저장
            _previousPathAABBs.Clear();

            BoundBox currentStart = startState;

            for (int i = 0; i < totalPoints - 1; i++)
            {
                Vector3 currentGoal;
                DirectionType currentGoalDirection;

                if (waypoints != null && i < waypoints.Count)
                {
                    currentGoal = waypoints[i];
                    // 중간 웨이포인트에서는 방향 제약 없음
                    currentGoalDirection = DirectionType.None;
                }
                else
                {
                    currentGoal = goalCenter;
                    currentGoalDirection = _goalDirection;
                }

                // 임시로 목표 설정
                Vector3 prevGoalCenter = _goalCenter;
                DirectionType prevGoalDirection = _goalDirection;

                _goalCenter = currentGoal;
                _goalDirection = currentGoalDirection;

                _goalBox = AABB.FromCenterSize(currentGoal, Vector3.One * _longestLength);


                // 구간 경로 탐색 (이전 구간의 세그먼트와 충돌 체크)
                List<BoundBox>? segmentPath = FindPathInternal(currentStart);

                // 목표 복원
                _goalCenter = prevGoalCenter;
                _goalDirection = prevGoalDirection;

                if (segmentPath == null)
                {
                    return new GroupPathFindResult { ResultCode = _lastFailureReason };
                }

                // 첫 구간이 아니면 시작점 중복 제거
                int startIdx = (i > 0 && segmentPath.Count > 0) ? 1 : 0;
                List<BoundBox>? simplifiedPath = PathSimplifier.SimplifyBoundBoxPath(segmentPath);

                if(simplifiedPath == null || simplifiedPath.Count == 0)
                {
                    return new GroupPathFindResult { ResultCode = RESULT_CODES.FAIL_TO_PATHFIND };
                }

                // 이번 구간의 경로를 추가하고 세그먼트 저장
                for (int j = startIdx; j < simplifiedPath.Count; j++)
                {
                    _waypointPathBuffer.Add(simplifiedPath[j]);
                }

                // 이번 구간의 세그먼트 AABB를 미리 계산하여 저장 (다음 구간에서 충돌 체크용)
                ExtractAndStoreSegmentAABBs(simplifiedPath);

                if (simplifiedPath.Count > 0)
                {
                    currentStart = simplifiedPath[simplifiedPath.Count - 1];
                }
            }

            if (_waypointPathBuffer.Count == 0)
            {
                return new GroupPathFindResult { ResultCode = RESULT_CODES.FAIL_TO_PATHFIND };
            }

            return BuildSuccessResult(_waypointPathBuffer, useSimplifiedPath: false);
        }

        /// <summary>
        /// 경로에서 세그먼트의 Union AABB를 미리 계산하여 _previousPathAABBs에 저장합니다.
        /// </summary>
        private void ExtractAndStoreSegmentAABBs(List<BoundBox> segmentPath)
        {
            if (segmentPath == null || segmentPath.Count == 0)
                return;

            if (segmentPath.Count == 1)
            {
                // 단일 노드인 경우, 해당 노드의 AABB만 저장
                _previousPathAABBs.Add(segmentPath[0].Bound);
                return;
            }

            // 경로의 각 노드에 대해 세그먼트의 Union AABB를 계산
            for (int i = 1; i < segmentPath.Count; ++i)
            {
                BoundBox prevBox = segmentPath[i - 1];
                BoundBox currentBox = segmentPath[i];

                _previousPathAABBs.Add(AABB.Union(prevBox.Bound, currentBox.Bound));
            }
        }

        /// <summary>
        /// 시작 AABB에서 목표 중심까지 그룹 배관 전체를 감싸는 박스를 이동시키며 경로를 탐색합니다.
        /// </summary>
        private GroupPathFindResult FindPath(
            BoundBox startState,
            Vector3 goalCenter,
            PathFindOptions? options = null)
        {
            _isCancelled = false;

            _goalCenter = goalCenter;

            _longestLength = GetLongestSize(startState.Bound.Size);
            _shortestLength = GetShortestSize(startState.Bound.Size);

            _goalBox = AABB.FromCenterSize(goalCenter, Vector3.One * _shortestLength);

            _options = options ?? PathFindOptions.Default;

            //if (IsAreaBlocked(startState.Bound))
            //    return new GroupPathFindResult { ResultCode = RESULT_CODES.FAIL_TO_START_POINT };


            List<BoundBox>? rawPath = FindPathInternal(startState);
            if (rawPath == null)
                return new GroupPathFindResult { ResultCode = _lastFailureReason };

            return BuildSuccessResult(rawPath);
        }


        // A* 본 탐색을 수행하며, 각 상태는 이동 중인 AABB와 진행 방향을 함께 가집니다.
        private List<BoundBox>? FindPathInternal(BoundBox startState)
        {
            _nodes.Clear();
            _closed.Clear();
            _openSet.Clear();


            SearchNode startNode = new SearchNode
            {
                Parent = null,
                GCost = 0,
                HCost = Heuristic(startState.Bound.Center),
                StraightDistance = 0,
                CurrentState = startState,
                SegmentStartState = startState

            };

            _nodes[startState] = startNode;
            _openSet.Add(startNode.FCost, startState);

            int explored = 0;

            // 타임아웃 체크를 위한 Stopwatch
            Stopwatch? stopwatch = null;
            if (_options.TimeoutMilliseconds > 0)
            {
                stopwatch = Stopwatch.StartNew();
            }

            // 기본 실패 이유 설정
            _lastFailureReason = RESULT_CODES.FAIL_TO_PATHFIND;

            while (_openSet.Count > 0)
            {
                // 중단 플래그 체크
                if (_isCancelled)
                {
                    _lastFailureReason = RESULT_CODES.CANCELLED;
                    return null;
                }

                // 타임아웃 체크 (주기적으로 체크하여 성능 영향 최소화)
                if (stopwatch != null && explored % 100 == 0 && stopwatch.ElapsedMilliseconds >= _options.TimeoutMilliseconds)
                {
                    _lastFailureReason = RESULT_CODES.TIMEOUT;
                    return null;
                }

                if (_options.MaxSearchNodes > 0 && explored >= _options.MaxSearchNodes)
                {
                    _lastFailureReason = RESULT_CODES.FAIL_TO_PATHFIND;
                    return null;
                }

                var current = _openSet.ExtractMin();
                BoundBox currentState = current.position;

                if (_closed.Contains(currentState))
                    continue;

                _closed.Add(currentState);
                explored++;

                SearchNode currentNode = _nodes[currentState];

                if (currentState.Bound.Intersects(_goalBox))
                {
                    if(currentState.Forward == _goalDirection || _goalDirection == DirectionType.None)
                        return ReconstructPath(currentState);
                }                    

                for (int i = 0; i < Directions.Length; i++)
                {
                    DirectionType nextDirection = (DirectionType)i;

                    // 시작점에서 첫 이동 시 startDirection으로만 이동 가능
                    if (currentState.Equals(startState) && !nextDirection.Equals(startState.Forward))
                    {
                        continue;                        
                    }


                    if (nextDirection == Directions.GetOppositeDirection(currentState.Forward))
                        continue;

                    bool isTurn = nextDirection != currentState.Forward;
                    if (isTurn && currentNode.StraightDistance + Epsilon < _options.MinStraightDistance)
                        continue;

                    BoundBox nextState = BoundBox.CreateNextBox(currentState, nextDirection);

                    if (_closed.Contains(nextState))
                        continue;

                    float movedistance = _shortestLength;
                    if (IsMoveBlocked(currentState.Bound, nextState.Bound) && currentState.Equals(startState) == false)
                    {
                        if(currentState.Forward.Equals(nextState.Forward))
                        {
                            int retryCount = 3;
                            for (int retry = 0; retry < retryCount; ++retry)
                            {
                                movedistance *= 0.5f;
                                nextState = currentState.MoveForwardDistance(movedistance);
                                if (!IsMoveBlocked(currentState.Bound, nextState.Bound))
                                {
                                    break;
                                }
                            }
                        }

                        continue;
                    }



                    float newGCost = currentNode.GCost + MoveCost(currentState, nextState);
                    bool isOpen = _nodes.TryGetValue(nextState, out SearchNode existingNode);

                    if (!isOpen || newGCost < existingNode.GCost)
                    {
                        Vector3 nextCenter = nextState.Bound.Center;                        

                        if (IsPathBlocked(currentNode, nextState.Bound))
                            continue;

                        // 이전 구간의 경로 세그먼트와 충돌 체크 (웨이포인트 경로 탐색 시)
                        if (IsPreviousSegmentsBlocked(nextState.Bound))
                            continue;

                        SearchNode nextNode = new SearchNode
                        {
                            Parent = currentState,
                            GCost = newGCost,
                            HCost = Heuristic(nextCenter),
                            StraightDistance = isTurn ? 0 : currentNode.StraightDistance + movedistance,
                            CurrentState = nextState,
                            SegmentStartState = isTurn ? nextState : currentNode.SegmentStartState
                        };

                        if (isOpen)
                            _openSet.UpdatePriority(nextState, nextNode.FCost);
                        else
                            _openSet.Add(nextNode.FCost, nextState);

                        _nodes[nextState] = nextNode;
                    }
                }
            }

            return null;
        }

        private void InitPipeOffsets(List<GroupPipeSpec> pipes, AABB startBounds)
        {
            _pipeOffsets.Clear();
            foreach (var pipe in pipes)
            {
                Vector3 offset = pipe.Start - startBounds.Center;
                _pipeOffsets.Add(offset);
            }
        }


        // 이전 AABB와 다음 AABB가 지나가는 Union 영역에 장애물이 있는지 확인합니다.
        private bool IsMoveBlocked(AABB from, AABB to)
        {

            //if (DebugInfo.DebugMode)
            //    DebugInfo.AABBCheckRecord.Add(new DebugAABBRecordInfo { aabb = to, isOccupied = false});

            AABB query = AABB.Union(from, to);
            return IsAreaBlocked(query);
        }
        private bool IsAreaBlocked(AABB queryBounds)
        {
            return ObstacleManager.Instance.CheckCollision(queryBounds);
        }

        // 기존 경로와 충돌하는지 확인합니다.
        private bool IsPathBlocked(SearchNode currentNode, AABB bound)
        {
            if (currentNode.SegmentStartState == null)
                return false;


            // 현재 노드와 세그먼트 시작 노드 사이의 Union AABB를 계산하여 충돌 여부를 확인합니다.
            // 세그먼트 시작 노드의 부모가 null이 될 때까지 반복합니다.

            SearchNode searchNode = currentNode;

            while (searchNode.SegmentStartState.HasValue)
            {
                if (searchNode.SegmentStartState.Value.Equals(searchNode.CurrentState))
                {
                    if (searchNode.CurrentState.Bound.IntersectsStrict(bound))
                        return true;
                }
                else
                {
                    AABB unionAABB = AABB.Union(searchNode.CurrentState.Bound, searchNode.SegmentStartState.Value.Bound);
                    if (unionAABB.IntersectsStrict(bound))
                        return true;
                }

                if (searchNode.Parent == null)
                    break;

                if (_nodes.TryGetValue(searchNode.Parent.Value, out SearchNode parentNode))
                {
                    searchNode = parentNode;
                }
                else
                {
                    break;
                }

            }

            return false;
        }

        /// <summary>
        /// 이전 구간의 경로와 충돌하는지 확인합니다.
        /// 미리 계산된 Union AABB 리스트와 충돌 체크합니다.
        /// </summary>
        private bool IsPreviousSegmentsBlocked(AABB bound)
        {
            if (_previousPathAABBs.Count == 0)
                return false;

            foreach (var previousAABB in _previousPathAABBs)
            {
                if (previousAABB.IntersectsStrict(bound))
                    return true;
            }

            return false;
        }

        // 이동 거리와 방향 전환/수직/수평 패널티를 합산해 A* G 비용을 계산합니다.
        private float MoveCost(in BoundBox currentState, in BoundBox nextState)
        {
            float cost = Vector3.Distance(currentState.Bound.Center, nextState.Bound.Center);

            //if (cost > _shortestLength)
            //    cost = _longestLength * 2f;

            if (currentState.Forward != nextState.Forward)
            {
                cost = _longestLength * 2f;
                cost += _options.TurnPenalty;
            }
                

            //cost += nextState.Bound.Center.Y != currentState.Bound.Center.Y ? _options.VerticalPenalty : _options.HorizontalPenalty;

            Int3 currentDir = Directions.GetDirection(nextState.Forward);

            // 방향별 페널티 적용: x, y, z 축 이동에 따라 다른 페널티 적용
            if (currentDir.x != 0)
                cost += currentDir.x > 0 ? _options.PositivePenalty.x : _options.NegativePenalty.x;

            if (currentDir.y != 0)
                cost += currentDir.y > 0 ? _options.PositivePenalty.y : _options.NegativePenalty.y;

            if (currentDir.z != 0)
                cost += currentDir.z > 0 ? _options.PositivePenalty.z : _options.NegativePenalty.z;


            return cost;
        }

        // 현재 중심에서 목표 중심까지의 Manhattan 거리 기반 휴리스틱 비용입니다.
        private float Heuristic(Vector3 position)
        {
            Vector3 center = position;
            Vector3 delta = _goalCenter - center;
            float manhattan = Math.Abs(delta.X) + Math.Abs(delta.Y) + Math.Abs(delta.Z);

            return manhattan * _options.HeuristicWeight;
        }

       


        // 여러 배관의 시작점과 직경을 모두 포함하는 시작 AABB를 만듭니다.
        private static bool TryBuildStartBounds(List<GroupPipeSpec> pipes, out AABB startBounds)
        {
            startBounds = new AABB();
            bool initialized = false;

            for (int i = 0; i < pipes.Count; i++)
            {
                if (pipes[i].Diameter <= 0f)
                    return false;

                Vector3 size = new Vector3(pipes[i].Diameter, pipes[i].Diameter, pipes[i].Diameter);
                AABB pipeBounds = AABB.FromCenterSize(pipes[i].Start, size);
                startBounds = initialized ? AABB.Union(startBounds, pipeBounds) : pipeBounds;
                initialized = true;
            }

            return initialized;
        }

        private List<BoundBox> ReconstructPath(BoundBox endState)
        {
            // 재사용 가능한 임시 버퍼 사용 (역순으로 저장)
            _tempPathBuffer.Clear();

            SearchNode current = _nodes[endState];
            _tempPathBuffer.Add(endState);

            while (current.Parent != null)
            {
                BoundBox parentState = current.Parent.Value;
                _tempPathBuffer.Add(parentState);
                
                if (!_nodes.ContainsKey(parentState))
                    break;

                current = _nodes[parentState];
            }

            // _pathBuffer를 재사용하여 역순으로 직접 추가 (Reverse 호출 없이)
            _pathBuffer.Clear();
            if (_pathBuffer.Capacity < _tempPathBuffer.Count)
                _pathBuffer.Capacity = _tempPathBuffer.Count;

            for (int i = _tempPathBuffer.Count - 1; i >= 0; i--)
            {
                _pathBuffer.Add(_tempPathBuffer[i]);
            }

            return _pathBuffer;
        }


        private GroupPathFindResult BuildSuccessResult(List<BoundBox> rawPath, bool useSimplifiedPath = true)
        {
            List<BoundBox>? simplifiedPath = useSimplifiedPath ? PathSimplifier.SimplifyBoundBoxPath(rawPath) : null;

            List<Vector3>? simpleCenterPath = new List<Vector3>();
            foreach (var box in simplifiedPath ?? rawPath)
            {
                simpleCenterPath.Add(box.Bound.Center);
            }

            List<List<Vector3>> worldPaths = new List<List<Vector3>>(_pipeOffsets.Count);
            foreach(var offset in _pipeOffsets)
            {
                worldPaths.Add(ConvertToWorldPath(simplifiedPath ?? rawPath, offset));
            }            

            return new GroupPathFindResult
            {
                ResultCode = RESULT_CODES.SUCCESS,
                WorldPath = worldPaths,
                SimpleCenterPath = simpleCenterPath
            };
        }


        private List<Vector3> ConvertToWorldPath(List<BoundBox> boundPath, Vector3 startOffset)
        {
            // 재사용 가능한 월드 경로 버퍼 사용
            _worldPathBuffer.Clear();
            if (_worldPathBuffer.Capacity < boundPath.Count)
                _worldPathBuffer.Capacity = boundPath.Count;

            BoundBox firstBox = boundPath[0];

            Vector3 startPosition = firstBox.Bound.Center + startOffset;
            Vector3 offset = startOffset;
            _worldPathBuffer.Add(startPosition);

            for (int i = 1; i < boundPath.Count; ++i)
            {
                BoundBox previousBox = boundPath[i - 1];
                BoundBox currentBox = boundPath[i];

                // 시작 박스의 forward 와 up 방향을 고려하여 회전된 현재 로컬의 Offset 을 계산
                if(previousBox.Forward != currentBox.Forward)
                    offset = RotateOffset(offset, previousBox.Forward, previousBox.Up, currentBox.Forward, currentBox.Up);

                Vector3 worldPosition = boundPath[i].Bound.Center + offset;

                // 좌/우 이동
                if(previousBox.Forward != currentBox.Forward && previousBox.Up == currentBox.Up)
                {
                    // 교차 지점 계산: Forward 방향이 바뀌는 순간, 이전 박스의 Forward 방향과 현재 박스의 Forward 방향이 교차하는 지점을 계산
                    Vector3 beforePosition = _worldPathBuffer[_worldPathBuffer.Count - 1];
                    Vector3 crossPosition = worldPosition * Vector3.Abs(previousBox.Forward.ToVector3())
                                           + beforePosition * Vector3.Abs(currentBox.Forward.ToVector3())
                                           + beforePosition * Vector3.Abs(previousBox.Up.ToVector3());

                    if(crossPosition.Equals(beforePosition) == false)
                    {
                        if(i == 1)
                            _worldPathBuffer.Add(crossPosition);
                        else
                            _worldPathBuffer[_worldPathBuffer.Count - 1] = crossPosition;
                    }
                        
                }


                _worldPathBuffer.Add(worldPosition);
            }

            // 결과 복사하여 반환 (버퍼는 재사용되므로)
            return new List<Vector3>(_worldPathBuffer);
        }


        private static DirectionType GetUpDirection(AABB aabb, DirectionType forwardDirection)
        {
            int axisIndex = GetDominantAxis(aabb.Size);

            DirectionType dir = DirectionType.None;

            switch (forwardDirection)
            {
                case DirectionType.Up:
                case DirectionType.Down:
                    {
                        if (axisIndex == 0)
                            dir = DirectionType.Forward;
                        else if (axisIndex == 2)
                            dir = DirectionType.Right;
                    }
                    break;

                case DirectionType.Left:
                case DirectionType.Right:
                    {
                        if (axisIndex == 1)
                            dir = DirectionType.Forward;
                        else if (axisIndex == 2)
                            dir = DirectionType.Up;
                    }
                    break;

                case DirectionType.Forward:
                case DirectionType.Backward:
                    {
                        if (axisIndex == 0)
                            dir = DirectionType.Up;
                        else if (axisIndex == 1)
                            dir = DirectionType.Right;
                    }
                    break;
            }

            return dir;
        }

        // Vector3에서 절대값이 가장 큰 축 index를 반환합니다.
        private static int GetDominantAxis(Vector3 value)
        {
            float ax = Math.Abs(value.X);
            float ay = Math.Abs(value.Y);
            float az = Math.Abs(value.Z);

            if (ax >= ay && ax >= az)
                return 0;

            if (ay >= ax && ay >= az)
                return 1;

            return 2;
        }

        private static Vector3 RotateOffset(Vector3 offset,
            DirectionType baseForward, DirectionType baseUp,
            DirectionType targetForward, DirectionType targetUp)
        {
            // Forward 방향이 같으면 회전이 필요 없음
            if(baseForward == targetForward)
                return offset;

            // 기본 좌표계의 회전 행렬 생성
            // CreateLookAt은 카메라 변환이므로 inverse가 필요함
            Matrix4x4 baseLookAt = Matrix4x4.CreateLookAt(Vector3.Zero, baseForward.ToVector3(), baseUp.ToVector3());
            Quaternion baseRotation = Quaternion.CreateFromRotationMatrix(baseLookAt);

            // 목표 좌표계의 회전 행렬 생성
            Matrix4x4 targetLookAt = Matrix4x4.CreateLookAt(Vector3.Zero, targetForward.ToVector3(), targetUp.ToVector3());
            Quaternion targetRotation = Quaternion.CreateFromRotationMatrix(targetLookAt);

            // 기본 좌표계에서 목표 좌표계로의 회전 변환
            Quaternion rotation = Quaternion.Inverse(baseRotation) * targetRotation;

            // offset 벡터를 회전 변환
            Vector3 rotatedOffset = Vector3.Transform(offset, rotation);
            if(baseUp == targetUp) 
                return -rotatedOffset;
        
            return rotatedOffset;
        }

        private static float GetLongestSize(Vector3 size)
        {
            return Math.Max(size.X, Math.Max(size.Y, size.Z));
        }

        private static float GetShortestSize(Vector3 size)
        {
            return Math.Min(size.X, Math.Min(size.Y, size.Z));
        }

    }
}
