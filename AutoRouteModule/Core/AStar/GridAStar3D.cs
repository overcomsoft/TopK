using AutoRouteModule.Utils;
using System;
using System.Collections.Generic;
using AutoRouteModule.Log;
using System.Numerics;

namespace AutoRouteModule.Core
{
    public class PathResult
    {
        public RESULT_CODES ResultCode;
        public List<Vector3>? WorldPath;
        public List<Vector3>? RawPath;
        public List<Vector3>? SimplifiedPath;
        public string? LogFilePath;
    }


    public class GridAStar3D
    {
        private struct AStarNode
        {
            public Vector3 Position;

            public float GCost;
            public float HCost;
            public float FCost => GCost + HCost;

            public Vector3? Parent;

            public Int3 Direction;

            // 현재 방향으로 이동한 연속 거리 (직선 이동 제약용)
            public float StraightDistance;
        }



        private float MoveCost = 10;
        private const int DefaultCapacity = 1024;


        private PathFindOptions _options;

        // 중단 플래그 (스레드 안전)
        private volatile bool _isCancelled = false;

        // 마지막 경로 탐색 실패 이유
        private RESULT_CODES _lastFailureReason = RESULT_CODES.FAIL_TO_PATHFIND;


        private readonly MinHeap<Vector3> _openSet = new MinHeap<Vector3>(DefaultCapacity);

        // 재사용 가능한 컬렉션들 - GC Alloc 최소화
        private readonly Dictionary<Vector3, AStarNode> _nodes = new Dictionary<Vector3, AStarNode>(DefaultCapacity);
        private readonly HashSet<Vector3> _closed = new HashSet<Vector3>();
        private readonly List<Vector3> _pathBuffer = new List<Vector3>(DefaultCapacity);
        private readonly List<Vector3> _pathWaypointBuffer = new List<Vector3>(DefaultCapacity);
        private readonly List<Vector3> _tempPathBuffer = new List<Vector3>(DefaultCapacity);
        private readonly List<Vector3> _worldPathBuffer = new List<Vector3>(DefaultCapacity);
        private readonly HashSet<Vector3> _visitedPositions = new HashSet<Vector3>();

        private SparseOccupancyGrid? _grid;

        private Vector3 _goalWorld;

        public PathResult FindPath(
            Vector3 startWorld,
            Vector3 goalWorld,
            DirectionType startDirection,
            //DirectionType goalDirection,
            float voxelSize,
            PathFindOptions? options = null)
        {

            // 중단 플래그 초기화
            _isCancelled = false;

            InitOptions(voxelSize,options);

            // 로깅 초기화
            if (LoggingState.EnableLogging)
            {
                LoggingState.Reset();
            }

            // 시간 측정 시작
            var stopwatch = System.Diagnostics.Stopwatch.StartNew();

            _grid = new SparseOccupancyGrid(startWorld, voxelSize);
            _goalWorld = goalWorld;

            if (startDirection != DirectionType.None)
            {
                Vector3 firstStep = startWorld + startDirection.ToVector3() * voxelSize;
                if (_grid.IsBlocked(firstStep))
                    return new PathResult { ResultCode = RESULT_CODES.FAIL_TO_START_POINT };
            }

            if (IsCheckBlockedGoalPosition(_grid, goalWorld))
                return new PathResult { ResultCode = RESULT_CODES.FAIL_TO_END_POINT };


            List<Vector3>? rawPath = FindPathInternal(_grid, startWorld, goalWorld, startDirection);

            stopwatch.Stop();

            PathResult result;
            if (rawPath == null)
            {
                result = new PathResult { ResultCode = _lastFailureReason };
            }
            else
            {
                result = BuildSuccessResult(rawPath);
            }

            // 로그 저장 (EnableLogging이 켜져있을 때만)
            if (LoggingState.EnableLogging)
            {
                string logFilePath = LogManager.CreateAndSavePathSearchLog(
                    result,
                    startWorld,
                    goalWorld,
                    stopwatch.Elapsed.TotalMilliseconds,
                    _grid,
                    voxelSize);

                result.LogFilePath = logFilePath;
            }

            return result;
        }

        /// <summary>
        /// 웨이포인트를 경유하는 경로를 찾습니다.
        /// 이전에 지나간 경로는 다시 사용할 수 없습니다.
        /// </summary>
        /// <param name="startWorld">시작 위치</param>
        /// <param name="waypoints">경유할 웨이포인트 리스트</param>
        /// <param name="goalWorld">최종 목표 위치</param>
        /// <param name="voxelSize">복셀 크기</param>
        /// <param name="options">경로 탐색 옵션</param>
        /// <returns>전체 경로 결과</returns>
        public PathResult FindPathWithWaypoints(
            Vector3 startWorld,
            List<Vector3> waypoints,
            Vector3 goalWorld,
            DirectionType startDirection,
            //DirectionType endDirection,
            float voxelSize,
            PathFindOptions? options = null)
        {

            // 중단 플래그 초기화
            _isCancelled = false;

            InitOptions(voxelSize,options);

            // 로깅 초기화
            if (LoggingState.EnableLogging)
            {
                LoggingState.Reset();
            }

            // 시간 측정 시작
            var stopwatch = System.Diagnostics.Stopwatch.StartNew();

            _grid = new SparseOccupancyGrid(startWorld, voxelSize);
            _goalWorld = goalWorld;


            // 전체 경로 지점 개수 계산
            int totalPoints = 2 + (waypoints?.Count ?? 0);

            // 재사용 가능한 버퍼 초기화
            _pathWaypointBuffer.Clear();
            if (_pathWaypointBuffer.Capacity < DefaultCapacity)
                _pathWaypointBuffer.Capacity = DefaultCapacity;

            // 이미 지나간 경로를 저장 (다음 구간에서 블록 처리) - 재사용
            _visitedPositions.Clear();

            Vector3 currentStart = startWorld;

            if(startDirection != DirectionType.None)
            {
                // 시작점 검증 (첫 구간만)
                Vector3 fromCheckPos = currentStart + startDirection.ToVector3() * voxelSize;

                if (_grid.IsBlocked(fromCheckPos))
                {
                    return new PathResult { ResultCode = RESULT_CODES.FAIL_TO_START_POINT };
                }
            }



            for (int i = 0; i < totalPoints - 1; i++)
            {
                Vector3 currentGoal;

                if (waypoints != null && i < waypoints.Count)
                {
                    currentGoal = waypoints[i];
                }
                else
                {
                    currentGoal = goalWorld;
                }


                // 도착점 검증               

                if (IsCheckBlockedGoalPosition(_grid, currentGoal))
                {
                    return new PathResult { ResultCode = RESULT_CODES.FAIL_TO_END_POINT };
                }

                // 구간 경로 탐색 (이전에 지나간 위치들을 제외)
                // 첫 구간에만 startDirection 적용, 이후는 None
                DirectionType segmentStartDir = (i == 0) ? startDirection : DirectionType.None;
                //DirectionType segmentEndDir = (i == totalPoints - 2) ? endDirection : DirectionType.None;
                List<Vector3>? segmentPath = FindPathInternal(_grid, currentStart, currentGoal, segmentStartDir, _visitedPositions);

                if (segmentPath == null)
                {
                    return new PathResult { ResultCode = _lastFailureReason };
                }

                // 첫 구간이 아니면 시작점 중복 제거
                int startIdx = (i > 0 && segmentPath.Count > 0) ? 1 : 0;

                // 이번 구간의 경로를 추가하고 방문 처리
                for (int j = startIdx; j < segmentPath.Count; j++)
                {
                    _pathWaypointBuffer.Add(segmentPath[j]);

                    // 마지막 지점은 다음 구간의 시작점이므로 방문 처리 제외
                    if (j < segmentPath.Count - 1)
                    {
                        _visitedPositions.Add(segmentPath[j]);
                    }
                }

                currentStart = segmentPath[segmentPath.Count - 1];
            }

            stopwatch.Stop();

            PathResult result;
            if (_pathWaypointBuffer.Count == 0)
            {
                result = new PathResult { ResultCode = RESULT_CODES.FAIL_TO_PATHFIND };
            }
            else
            {
                result = BuildSuccessResult(_pathWaypointBuffer);
            }

            // 로그 저장 (EnableLogging이 켜져있을 때만)
            if (LoggingState.EnableLogging)
            {
                string logFilePath = LogManager.CreateAndSavePathSearchLog(
                    result,
                    startWorld,
                    goalWorld,
                    stopwatch.Elapsed.TotalMilliseconds,
                    _grid,
                    voxelSize);

                result.LogFilePath = logFilePath;
            }

            return result;
        }

        public void InitOptions(float voxelSize, PathFindOptions? options)
        {
            _options = options ?? PathFindOptions.Default;
        }


        private List<Vector3>? FindPathInternal(
            SparseOccupancyGrid grid,
            Vector3 start,
            Vector3 goal,
            DirectionType startDirection = DirectionType.None)
        {
            return FindPathInternal(grid, start, goal, startDirection, null);
        }

        internal List<Vector3>? FindPathInternal(
            SparseOccupancyGrid grid,
            Vector3 start,
            Vector3 goal,
            DirectionType startDirection,
            HashSet<Vector3>? blockedPositions)
        {

            MoveCost = grid.GridSize;

            // 컬렉션 재사용 - Clear만 호출하여 GC Alloc 최소화
            _nodes.Clear();
            _closed.Clear();
            _openSet.Clear();

            // 시작 방향을 Int3로 변환 (None인 경우 Int3.Zero)
            Int3 startDir = startDirection != DirectionType.None 
                ? Directions.GetDirection(startDirection) 
                : Int3.Zero;

            AStarNode startNode = new AStarNode
            {
                Position = start,
                GCost = 0,
                HCost = Heuristic(start, goal),
                Parent = null,
                Direction = startDir,
                StraightDistance = 0
            };

            _nodes[start] = startNode;
            _openSet.Add(startNode.FCost, start);

            AABB goalAABB = grid.GetVoxelAABB(goal);

            int nodesExplored = 0;

            // 타임아웃 체크를 위한 Stopwatch
            System.Diagnostics.Stopwatch? stopwatch = null;
            if (_options.TimeoutMilliseconds > 0)
            {
                stopwatch = System.Diagnostics.Stopwatch.StartNew();
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

                var current = _openSet.ExtractMin();
                Vector3 currentPos = current.position;

                // 타임아웃 체크 (주기적으로 체크하여 성능 영향 최소화)
                if (stopwatch != null && nodesExplored % 100 == 0 && stopwatch.ElapsedMilliseconds >= _options.TimeoutMilliseconds)
                {
                    _lastFailureReason = RESULT_CODES.TIMEOUT;
                    return ReconstructPath(currentPos);
                }

                // 최대 탐색 노드 수 체크
                if (_options.MaxSearchNodes > 0 && nodesExplored >= _options.MaxSearchNodes)
                {
                    _lastFailureReason = RESULT_CODES.FAIL_TO_PATHFIND;
                    return ReconstructPath(currentPos);
                }              

                // 이미 처리된 노드는 건너뛰기 (중복 방지)
                if (_closed.Contains(currentPos))
                    continue;

                _closed.Add(currentPos);
                nodesExplored++;

                AStarNode currentNode = _nodes[currentPos];

                // 목표 도달 시 조기 종료
                if (goalAABB.IntersectsStrict(grid.GetVoxelAABB(currentPos)))
                {
                    //// 목표 방향이 지정된 경우, 방향이 일치해야 종료
                    //if (goalDirection != DirectionType.None)
                    //{
                    //    Int3 goalDir = Directions.GetDirection(goalDirection);
                    //    if (!currentNode.Direction.Equals(goalDir))
                    //    {
                    //        continue;
                    //    }
                    //}
                    return ReconstructPath(currentPos);
                }
                

                for (int i = 0; i < Directions.Length; ++i)
                {
                    Int3 dir = Directions.Direction[i];

                    // 시작점에서 첫 이동 시 startDirection으로만 이동 가능
                    if (currentPos.Equals(start) && !startDir.Equals(Int3.Zero))
                    {
                        if (!dir.Equals(startDir))
                        {
                            continue;
                        }
                    }

                    if(IsMinStraightDistanceSatisfied(currentNode, dir) == false)
                    {
                        continue;
                    }


                    Vector3 next;

                    next = currentPos + dir.ToVector3() * grid.GridSize;

                    // 이미 처리된 노드는 건너뛰기
                    if (_closed.Contains(next))
                        continue;

                    // 이전에 방문한 위치인지 체크 (시작점과 목표점은 제외)
                    if (blockedPositions != null &&
                        blockedPositions.Contains(next) &&
                        !next.Equals(start) &&
                        !next.Equals(goal))
                        continue;

                    float movedistance = grid.GridSize;

                    float moveCost = CalculateMoveCost(currentNode.Direction, dir, movedistance);
                    float newGCost = currentNode.GCost + moveCost;
                    float hCost = Heuristic(next, goal);

                    // 그리드 블록 체크
                    if (grid.IsBlocked(next))
                    {
                        int retryCount = 3;
                        for (int retry = 0; retry < retryCount; ++retry)
                        {
                            movedistance *= 0.5f;
                            next = currentPos + dir.ToVector3() * movedistance;
                            if (!grid.IsBlocked(next))
                            {
                                // 노드 기록
                                LogManager.RecordSearchNode(next, true, newGCost, hCost);

                                break;
                            }
                        }

                        continue;
                    }

                    // 노드 기록
                    LogManager.RecordSearchNode(next, false, newGCost, hCost);


                    bool isInOpen = _nodes.ContainsKey(next);

                    if (!isInOpen || newGCost < _nodes[next].GCost)
                    {
                        float newStraightDistance;
                        if (currentNode.Direction.Equals(Int3.Zero) || dir.Equals(currentNode.Direction))
                        {
                            // 같은 방향으로 계속 이동
                            newStraightDistance = currentNode.StraightDistance + movedistance;
                        }
                        else
                        {
                            // 방향 전환: 새로운 직선 시작
                            newStraightDistance = grid.GridSize;
                        }

                        AStarNode nextNode = new AStarNode
                        {
                            Position = next,
                            GCost = newGCost,
                            HCost = hCost,
                            Parent = currentPos,
                            Direction = dir,
                            StraightDistance = newStraightDistance
                        };

                        if (isInOpen)
                        {
                            // 기존 노드를 업데이트
                            _openSet.UpdatePriority(next, nextNode.FCost);
                        }
                        else
                        {
                            // 새 노드 추가
                            _openSet.Add(nextNode.FCost, next);
                        }

                        _nodes[next] = nextNode;
                    }
                }
            }

            return null;
        }


        private float CalculateMoveCost(Int3 previousDir, Int3 currentDir, float moveDistance)
        {
            float cost = /*MoveCost * */moveDistance;

            // Turn penalty 적용: 이전 방향과 현재 방향이 다르면 회전 페널티 추가
            if (!previousDir.Equals(Int3.Zero) && !previousDir.Equals(currentDir))
                cost += _options.TurnPenalty;

            // 방향별 페널티 적용: x, y, z 축 이동에 따라 다른 페널티 적용
            if (currentDir.x != 0)            
                cost += currentDir.x > 0 ? _options.PositivePenalty.x : _options.NegativePenalty.x;            

            if (currentDir.y != 0)
                cost += currentDir.y > 0 ? _options.PositivePenalty.y : _options.NegativePenalty.y;

            if (currentDir.z != 0)
                cost += currentDir.z > 0 ? _options.PositivePenalty.z : _options.NegativePenalty.z;

            return cost;
        }

        private float Heuristic(Vector3 a, Vector3 b)
        {
            float X = a.X - b.X;
            float Y = a.Y - b.Y;
            float Z = a.Z - b.Z;

            float dx = Math.Abs(X);
            float dy = Math.Abs(Y);
            float dz = Math.Abs(Z);

            float manhattanDistance = (dx + dy + dz);// * MoveCost;

            // 최소 페널티 추가: 수직 이동과 수평 이동의 최소 페널티 적용
            
            int penalty_x = X == 0 ? 0 : (X > 0 ? _options.PositivePenalty.x : _options.NegativePenalty.x);
            int penalty_y = Y == 0 ? 0 : (Y > 0 ? _options.PositivePenalty.y : _options.NegativePenalty.y);
            int penalty_z = Z == 0 ? 0 : (Z > 0 ? _options.PositivePenalty.z : _options.NegativePenalty.z);

            float minPenalty = (dx * penalty_x) + (dy * penalty_y) + (dz * penalty_z);

            float baseHeuristic = manhattanDistance + minPenalty;

            // 휴리스틱 가중치 적용 (Weighted A*)
            return baseHeuristic * _options.HeuristicWeight;
        }

        private bool IsMinStraightDistanceSatisfied(AStarNode node, Int3 newDirection)
        {
            if (_options.MinStraightDistance <= 0)
                return true;
            if (node.Direction.Equals(Int3.Zero))
                return true; // 시작 노드이므로 직선 거리 제약 없음
            if (node.Direction.Equals(newDirection))
                return true; // 같은 방향으로 이동 중이므로 직선 거리 제약 만족
            // 방향이 바뀌는 경우, 현재까지 직선으로 이동한 거리가 최소 요구사항을 만족하는지 확인
            return node.StraightDistance >= _options.MinStraightDistance;
        }

        private bool IsCheckBlockedGoalPosition(SparseOccupancyGrid grid, Vector3 goalWorld, DirectionType goalDirection = DirectionType.None)
        {
            if(grid == null)
                return false;

            //if(grid.IsBlocked(goalWorld))
            //    return true;

            Vector3 checkPos = goalWorld;

            if (goalDirection != DirectionType.None)
            {
                checkPos += goalDirection.ToVector3() * grid.GridSize * 0.5f;
                return grid.IsBlocked(checkPos);

            }
            else
            {
                // 목표 방향이 지정되지 않은 경우, 주변 6방향을 체크
                for (int i = 0; i < Directions.Length; i++)
                {
                    Int3 dir = Directions.Direction[i];
                    checkPos = goalWorld + dir.ToVector3() * grid.GridSize * 0.5f;
                    if (!grid.IsBlocked(checkPos))
                    {
                        return false; // 하나라도 통과 가능한 위치가 있으면 false
                    }
                }
            }

            return true;
        }

        private List<Vector3> ReconstructPath(Vector3 endPos)
        {
            // 재사용 가능한 임시 버퍼 사용 (역순으로 저장)
            _tempPathBuffer.Clear();

            Vector3? current = endPos;

            while (current.HasValue)
            {
                _tempPathBuffer.Add(current.Value);

                if (!_nodes.ContainsKey(current.Value))
                    break;

                current = _nodes[current.Value].Parent;
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

        private PathResult BuildSuccessResult(List<Vector3> rawPath)
        {
            List<Vector3>? simplifiedPath = PathSimplifier.SimplifyOrthogonalPath(rawPath);
            List<Vector3> worldPath = ConvertToWorldPath(simplifiedPath ?? rawPath);

            return new PathResult
            {
                ResultCode = RESULT_CODES.SUCCESS,
                WorldPath = worldPath,
                SimplifiedPath = simplifiedPath,
                RawPath = new List<Vector3>(rawPath) // 호출자가 수정할 수 있으므로 복사본 제공
            };
        }

        private List<Vector3> ConvertToWorldPath(List<Vector3> worldPath)
        {
            // 재사용 가능한 월드 경로 버퍼 사용
            _worldPathBuffer.Clear();
            if (_worldPathBuffer.Capacity < worldPath.Count)
                _worldPathBuffer.Capacity = worldPath.Count;

            foreach (Vector3 pos in worldPath)
            {
                if (_grid != null)
                {
                    _worldPathBuffer.Add(pos);
                }
            }

            // 목적지 보정 
            GoalCorrection();


            // 결과 복사하여 반환 (버퍼는 재사용되므로)
            return new List<Vector3>(_worldPathBuffer);
        }

        private void GoalCorrection()
        {
            if (_worldPathBuffer.Count == 0)
                return;

            Vector3 _worldPathGoal = _worldPathBuffer[_worldPathBuffer.Count - 1];

            if(_worldPathGoal.Equals(_goalWorld))
                return;

            if (_worldPathBuffer.Count <= 2)
            {
                // TODO : 경로가 2개 이하인 경우 보정이 어려움, 현재는 그냥 반환
                return;
            }


            Vector3 delta = _worldPathGoal - _goalWorld;
            int lastIndex = _worldPathBuffer.Count - 1;
            Vector3 beforePoint = _worldPathBuffer[lastIndex];

            _worldPathBuffer[lastIndex] = _goalWorld;

            for (int i = lastIndex - 1; i > 0; --i)
            {
                Vector3 dir = _worldPathBuffer[i] - beforePoint;
                beforePoint = _worldPathBuffer[i];

                if (Math.Abs(dir.X) > 0)
                {
                    delta.X = 0;
                }
                else if (Math.Abs(dir.Y) > 0)
                {
                    delta.Y = 0;
                }
                else if (Math.Abs(dir.Z) > 0)
                {
                    delta.Z = 0;
                }

                _worldPathBuffer[i] -= delta;


                if (delta.X == 0 && delta.Y == 0 && delta.Z == 0)
                    return;
            }

            if(delta.X != 0 || delta.Y != 0 || delta.Z != 0)
            {
                // TODO: 마지막까지 보정이 안되면 어떻게 할지 고민 필요
            }

        }




        /// <summary>
        /// 현재 진행 중인 경로 탐색을 중단합니다.
        /// </summary>
        public void CancelPathfinding()
        {
            _isCancelled = true;
        }
    }
}
