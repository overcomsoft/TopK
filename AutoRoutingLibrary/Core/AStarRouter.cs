using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Numerics;
using AutoRoutingLibrary.Models;

namespace AutoRoutingLibrary.Core
{
    /// <summary>
    /// 배관 자동 설계 알고리즘 (A* 라우팅 알고리즘) 클래스
    /// 공간을 3차원 그리드로 분할하고, 시작점에서 종료점까지의 최적 경로를 탐색합니다.
    /// 장애물 회피, 방향 전환 최소화, 그리고 지정된 다발(패턴) 경로 추종 기능이 핵심입니다.
    /// </summary>
    public class AStarRouter
    {
        // 3D 장애물과의 충돌 여부를 빠르게 판별하기 위한 공간 인덱스 클래스
        private readonly ObstacleSpatialIndex _spatialIndex;
        // 그룹 배관의 패턴 정보를 제공하여 특정 경로로 유도하는 가이드라인 필드
        private readonly GuidanceField _guidanceField;
        // 탐색을 수행할 3D 그리드의 기본 해상도 (크기가 클수록 빠르나 정밀도가 떨어짐)
        private readonly float _gridSize;
        
        /// <summary>
        /// AStarRouter 생성자
        /// </summary>
        /// <param name="spatialIndex">장애물 공간 인덱스 인스턴스</param>
        /// <param name="guidanceField">다발 배관 유도선 정보 인스턴스</param>
        /// <param name="gridSize">탐색 그리드의 크기 (기본값: 50f)</param>
        public AStarRouter(ObstacleSpatialIndex spatialIndex, GuidanceField guidanceField, float gridSize = 50f)
        {
            _spatialIndex = spatialIndex;
            _guidanceField = guidanceField;
            _gridSize = gridSize;
        }

        /// <summary>
        /// A* 알고리즘을 수행하여 시작점(startPoc)에서 종료점(endPoc)까지의 최적 배관 경로를 탐색합니다.
        /// </summary>
        /// <param name="startPoc">시작 좌표 (Point of Connection)</param>
        /// <param name="endPoc">종료 좌표 (Point of Connection)</param>
        /// <param name="pipeDiameter">배관의 직경 (장애물 회피 검사에 사용)</param>
        /// <param name="progress">UI 등에 처리 상태를 전달하기 위한 IProgress 객체</param>
        /// <returns>탐색 결과(RoutingResult), 경로 정보 및 성공/실패 여부를 포함</returns>
        public RoutingResult FindPath(Vector3 startPoc, Vector3 endPoc, float pipeDiameter, IProgress<string> progress = null)
        {
            var result = new RoutingResult();
            var sw = Stopwatch.StartNew();

            try
            {
                progress?.Report("라우팅 초기화 중...");
                
                // 배관이 지나갈 때 장애물과 부딪히지 않기 위해 필요한 안전 거리 (반지름 + 여유공간)
                float safeMargin = pipeDiameter / 2.0f + 10f; 
                
                // 시작점과 종료점이 이미 장애물 내부(덕트 제외 등)에 묻혀있다면 탐색 불가 처리
                if (_spatialIndex.IsOccupied(startPoc, safeMargin))
                {
                    result.ErrorMessage = "시작 PoC 위치가 장애물 내부에 존재합니다.";
                    return result;
                }
                if (_spatialIndex.IsOccupied(endPoc, safeMargin))
                {
                    result.ErrorMessage = "종료 PoC 위치가 장애물 내부에 존재합니다.";
                    return result;
                }

                // 1. Grid 스냅: 입력받은 임의의 좌표를 지정된 gridSize 단위의 좌표계로 강제 정렬합니다.
                Vector3 startGrid = SnapToGrid(startPoc);
                Vector3 endGrid = SnapToGrid(endPoc);

                // A* 탐색을 위한 오픈 리스트 (탐색 예정 노드, 우선순위 큐)
                var openSet = new PriorityQueue<Node3D, float>();
                // 이미 방문 및 확정된 노드 리스트
                var closedSet = new HashSet<Vector3>();
                // 경로 추적을 위한 부모 노드 기록 딕셔너리
                var cameFrom = new Dictionary<Vector3, Node3D>();

                // 시작 노드 생성 (현재 비용 0, 휴리스틱 비용 계산)
                var startNode = new Node3D(startGrid, 0, GetHeuristic(startGrid, endGrid), null);
                openSet.Enqueue(startNode, startNode.F);
                cameFrom[startGrid] = startNode;

                Node3D endNode = null;

                // 6방향 탐색: 직교하는 3차원 축 방향(상/하/좌/우/전/후)으로만 배관 이동
                Vector3[] directions = new Vector3[]
                {
                    new Vector3(_gridSize, 0, 0), new Vector3(-_gridSize, 0, 0),
                    new Vector3(0, _gridSize, 0), new Vector3(0, -_gridSize, 0),
                    new Vector3(0, 0, _gridSize), new Vector3(0, 0, -_gridSize)
                };

                int maxIterations = 200000; // 무한 루프 방지를 위한 최대 탐색 제한 횟수
                int iterations = 0;
                int reportInterval = 5000;  // UI 갱신을 위한 루프 간격

                progress?.Report("A* 경로 탐색 시작...");

                // 2. 핵심 알고리즘 루프
                while (openSet.Count > 0 && iterations < maxIterations)
                {
                    iterations++;
                    if (iterations % reportInterval == 0)
                    {
                        progress?.Report($"탐색 중... ({iterations} 반복, 현재 큐: {openSet.Count} 노드)");
                    }

                    // 가장 비용(F)이 낮은 최적 유망 노드 꺼내기
                    var current = openSet.Dequeue();

                    // 이미 방문 완료된 노드이면 중복 처리 방지를 위해 건너뜀
                    if (closedSet.Contains(current.Position)) continue;

                    // 종료점 인근 도달 시 루프 종료 (그리드 크기 절반 이내)
                    if (Vector3.Distance(current.Position, endGrid) < _gridSize * 0.5f)
                    {
                        endNode = current;
                        break;
                    }

                    // 탐색 완료 처리
                    closedSet.Add(current.Position);

                    float stepMultiplier = 1.0f;

                    // 인접한 6개 노드 탐색
                    foreach (var dir in directions)
                    {
                        Vector3 neighborPos = current.Position + (dir * stepMultiplier);

                        // 이미 방문한 노드이거나 장애물(다른 배관 포함)과 충돌하는 경우 패스
                        if (closedSet.Contains(neighborPos)) continue;
                        if (_spatialIndex.IsOccupied(neighborPos, safeMargin)) continue;

                        // 진행 비용(G 비용) 누적
                        float stepCost = dir.Length() * stepMultiplier;
                        
                        // 패턴 그룹 다발 세그먼트에 가까울수록 stepCost를 획기적으로 낮춤 (유도 효과)
                        float guidanceBonus = _guidanceField.GetBonusMultiplier(neighborPos);
                        stepCost *= guidanceBonus;

                        float gCost = current.G + stepCost;
                        
                        // 방향 전환 비용 부여 (Elbow 부속품이 많아지는 것을 방지하여 배관을 가급적 직진하게 만듦)
                        if (current.Parent != null)
                        {
                            Vector3 currentDir = Vector3.Normalize(current.Position - current.Parent.Position);
                            Vector3 nextDir = Vector3.Normalize(dir);
                            if (Vector3.Dot(currentDir, nextDir) < 0.99f) // 방향이 달라지면 페널티
                            {
                                gCost += _gridSize * 5.0f; 
                            }
                        }

                        // Weighted A* 적용 (도착점까지의 예상 비용을 부풀려 탐색 속도를 대폭 향상, Greedy 특성)
                        float hCost = GetHeuristic(neighborPos, endGrid) * 1.2f;
                        
                        // 아직 방문하지 않은 노드이거나, 우회하는 길이 기존 길보다 비용이 더 저렴할 경우 업데이트
                        if (!cameFrom.TryGetValue(neighborPos, out var existingNode) || gCost < existingNode.G)
                        {
                            var neighborNode = new Node3D(neighborPos, gCost, hCost, current);
                            cameFrom[neighborPos] = neighborNode;
                            openSet.Enqueue(neighborNode, neighborNode.F); // 큐에 삽입
                        }
                    }
                }

                result.Iterations = iterations;

                // 3. 탐색 실패 예외 처리
                if (endNode == null)
                {
                    if (iterations >= maxIterations)
                    {
                        result.ErrorMessage = $"최대 반복 횟수({maxIterations})를 초과하여 탐색을 중단했습니다. 공간이 막혀있거나 너무 복잡합니다.";
                    }
                    else
                    {
                        result.ErrorMessage = "목표 지점에 도달할 수 있는 가능한 경로가 없습니다 (완전 막힘).";
                    }
                    return result;
                }

                progress?.Report("경로 최적화 중...");

                // 4. 경로 역추적 (도착지점에서부터 부모 노드를 타고 출발점까지 이동)
                var path = new List<Vector3>();
                var curr = endNode;
                while (curr != null)
                {
                    path.Add(curr.Position);
                    curr = curr.Parent;
                }
                path.Reverse();

                // 5. 원본 출발/종료점 보정 (스냅된 위치 대신 원래의 정밀한 좌표 연결)
                if (startPoc != path.First()) path.Insert(0, startPoc);
                if (endPoc != path.Last()) path.Add(endPoc);

                // 6. 중간의 불필요한 직진 노드 제거 (꺾이는 노드만 반환하여 3D 렌더링 최적화)
                result.Path = SimplifyPath(path);
                result.Success = true;
                progress?.Report("라우팅 완료.");
            }
            catch (Exception ex)
            {
                result.ErrorMessage = $"예기치 않은 오류 발생: {ex.Message}";
            }
            finally
            {
                sw.Stop();
                result.ElapsedTime = sw.Elapsed;
            }

            return result;
        }

        /// <summary>
        /// 남은 예상 거리를 계산하는 휴리스틱 함수
        /// 배관의 특성상 대각선 이동이 불가능하고 X, Y, Z 직교로만 움직이므로 맨해튼 거리 사용
        /// </summary>
        private float GetHeuristic(Vector3 a, Vector3 b)
        {
            return Math.Abs(a.X - b.X) + Math.Abs(a.Y - b.Y) + Math.Abs(a.Z - b.Z);
        }

        /// <summary>
        /// 공간상의 임의의 좌표를 A* 알고리즘이 처리할 수 있도록 3D 그리드(Grid)에 맞게 정렬(Snap)합니다.
        /// </summary>
        private Vector3 SnapToGrid(Vector3 pos)
        {
            return new Vector3(
                (float)Math.Round(pos.X / _gridSize) * _gridSize,
                (float)Math.Round(pos.Y / _gridSize) * _gridSize,
                (float)Math.Round(pos.Z / _gridSize) * _gridSize
            );
        }

        /// <summary>
        /// 탐색된 수많은 그리드 노드 중 직진 구간의 노드들을 생략하고, 방향이 꺾이는 지점만 남겨 최적화합니다.
        /// </summary>
        private List<Vector3> SimplifyPath(List<Vector3> path)
        {
            if (path.Count <= 2) return path;

            var simplified = new List<Vector3> { path[0] };
            for (int i = 1; i < path.Count - 1; i++)
            {
                Vector3 dir1 = Vector3.Normalize(path[i] - path[i - 1]);
                Vector3 dir2 = Vector3.Normalize(path[i + 1] - path[i]);

                // 벡터 내적(Dot Product)을 통해 방향 변경 감지 (일직선이면 내적값이 1에 가까움)
                if (Vector3.Dot(dir1, dir2) < 0.99f) 
                {
                    simplified.Add(path[i]);
                }
            }
            simplified.Add(path.Last());
            return simplified;
        }

        /// <summary>
        /// A* 탐색의 단일 공간 상태를 표현하는 노드 클래스
        /// </summary>
        private class Node3D
        {
            public Vector3 Position { get; }    // 3차원 좌표
            public float G { get; }             // 시작점부터 현재 노드까지의 실제 이동 비용
            public float H { get; }             // 현재 노드부터 도착점까지의 예상 휴리스틱 비용
            public float F => G + H;            // 최종 우선순위 결정 비용 (낮을수록 우선 탐색)
            public Node3D Parent { get; }       // 역추적을 위한 부모 노드 정보

            public Node3D(Vector3 pos, float g, float h, Node3D parent)
            {
                Position = pos;
                G = g;
                H = h;
                Parent = parent;
            }
        }
    }
}
