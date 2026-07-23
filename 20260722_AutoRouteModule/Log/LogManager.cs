

using System;
using System.Collections.Generic;
using System.IO;
using System.Numerics;
using Newtonsoft.Json;
using AutoRouteModule.Core;

namespace AutoRouteModule.Log
{
    /// <summary>
    /// 로그 수집 활성화 상태
    /// </summary>
    public static class LoggingState
    {
        public static bool EnableLogging = false;

        internal static int NodeFindCount = 0;
        internal static List<SearchNodeRecord> NodeRecords = new List<SearchNodeRecord>(30000);

        public static void Reset()
        {
            NodeFindCount = 0;
            NodeRecords.Clear();
        }
    }

    /// <summary>
    /// 탐색 노드 기록
    /// </summary>
    internal class SearchNodeRecord
    {
        public Vector3 Position;
        public bool IsCollision;
        public float GCost;
        public float HCost;
        public float TotalCost;
    }


    /// <summary>
    /// 경로 탐색 로그를 JSON 파일로 저장하는 관리자 클래스
    /// </summary>
    public class LogManager
    {
        //C:\Users\사용자계정\AppData\Local\DDWorks
        private static readonly string LogDirectoryPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DDWorks","PathFindLog");

        private static readonly JsonSerializerSettings JsonSettings = new JsonSerializerSettings
        {
            Formatting = Formatting.Indented,
            NullValueHandling = NullValueHandling.Include
        };

        private static string? _lastSavedLogPath = null;

        /// <summary>
        /// 마지막으로 저장된 로그 파일 경로
        /// </summary>
        public static string? LastSavedLogPath => _lastSavedLogPath;

        /// <summary>
        /// 경로 탐색 결과를 JSON 파일로 저장 (메인 로그 및 별도 경로 파일)
        /// </summary>
        /// <param name="log">경로 탐색 로그 데이터</param>
        /// <returns>저장된 메인 로그 파일 경로</returns>
        public static string SavePathSearchLog(PathSearchLog log)
        {
            string logDirectory = Path.Combine(LogDirectoryPath, DateTime.Now.ToString("yyyy-MM-dd"));

            try
            {
                if (!Directory.Exists(logDirectory))
                {
                    Directory.CreateDirectory(logDirectory);
                }

                // 파일명 생성: PathLog_HH_mm_ss_ffff.json
                string timestamp = DateTime.Now.ToString("HH_mm_ss_ffff");
                string baseFileName = $"PathLog_{timestamp}";
                string baseFilePath = Path.Combine(logDirectory, baseFileName);

                log.BaseLogPath = baseFilePath;

                // 메인 로그 파일 경로
                string mainLogFile = $"{baseFilePath}.json";

                // 메인 로그 저장
                string json = JsonConvert.SerializeObject(log, JsonSettings);
                File.WriteAllText(mainLogFile, json);

                // 마지막 저장 경로 기록
                _lastSavedLogPath = mainLogFile;

                return mainLogFile;
            }
            catch (Exception)
            {
                // 로그 저장 실패 시 빈 문자열 반환
                return string.Empty;
            }
        }

        /// <summary>
        /// Raw Path를 별도 파일로 저장
        /// </summary>
        private static void SaveRawPath(string baseFilePath, List<Vector3>? rawPath)
        {
            if (rawPath == null || rawPath.Count == 0)
                return;

            string filePath = $"{baseFilePath}_RawPath.json";
            var rawPathLogs = new List<Vector3Log>();

            foreach (var pos in rawPath)
            {
                rawPathLogs.Add(new Vector3Log(pos));
            }

            var pathData = new
            {
                path_type = "raw_path",
                node_count = rawPathLogs.Count,
                nodes = rawPathLogs
            };

            string json = JsonConvert.SerializeObject(pathData, JsonSettings);
            File.WriteAllText(filePath, json);
        }

        /// <summary>
        /// Simplified Path를 별도 파일로 저장
        /// </summary>
        private static void SaveSimplifiedPath(string baseFilePath, List<Vector3>? simplifiedPath)
        {
            if (simplifiedPath == null || simplifiedPath.Count == 0)
                return;

            string filePath = $"{baseFilePath}_SimplifiedPath.json";
            var simplifiedPathLogs = new List<Vector3Log>();

            foreach (var pos in simplifiedPath)
            {
                simplifiedPathLogs.Add(new Vector3Log(pos));
            }

            var pathData = new
            {
                path_type = "simplified_path",
                node_count = simplifiedPathLogs.Count,
                nodes = simplifiedPathLogs
            };

            string json = JsonConvert.SerializeObject(pathData, JsonSettings);
            File.WriteAllText(filePath, json);
        }

        /// <summary>
        /// Search Nodes를 별도 파일로 저장
        /// </summary>
        private static void SaveSearchNodes(string baseFilePath, List<SearchNodeRecord>? nodeRecords)
        {
            if (nodeRecords == null || nodeRecords.Count == 0)
                return;

            string filePath = $"{baseFilePath}_SearchNodes.json";
            var nodeLogs = new List<PathSearchNodeLog>();

            foreach (var record in nodeRecords)
            {
                nodeLogs.Add(new PathSearchNodeLog
                {
                    Position = new Vector3Log(record.Position),
                    IsCollision = record.IsCollision,
                    GCost = record.GCost,
                    HCost = record.HCost,
                    TotalCost = record.TotalCost
                });
            }

            var nodeData = new
            {
                search_nodes = nodeLogs,
                total_count = nodeLogs.Count
            };

            string json = JsonConvert.SerializeObject(nodeData, JsonSettings);
            File.WriteAllText(filePath, json);
        }

        /// <summary>
        /// PathResult로부터 PathSearchLog 생성 및 저장
        /// </summary>
        /// <param name="result">경로 탐색 결과</param>
        /// <param name="startPosition">시작 위치</param>
        /// <param name="goalPosition">목표 위치</param>
        /// <param name="elapsedTimeMs">경과 시간 (밀리초)</param>
        /// <param name="grid">장애물 그리드 (꺾임 이유 분석용, optional)</param>
        /// <param name="voxelSize">복셀 크기 (꺾임 이유 분석용, optional)</param>
        /// <returns>저장된 메인 로그 파일 경로</returns>
        public static string CreateAndSavePathSearchLog(
            PathResult result,
            Vector3 startPosition,
            Vector3 goalPosition,
            double elapsedTimeMs,
            SparseOccupancyGrid? grid = null,
            float voxelSize = 1.0f)
        {
            var log = new PathSearchLog
            {
                Success = result.ResultCode == RESULT_CODES.SUCCESS,
                ResultCode = result.ResultCode.ToString(),
                ElapsedTimeMs = elapsedTimeMs,
                SearchNodeCount = LoggingState.NodeFindCount,
                StartPosition = new Vector3Log(startPosition),
                GoalPosition = new Vector3Log(goalPosition)
            };

            // 메인 로그 저장 (파일 경로 획득)
            string mainLogFile = SavePathSearchLog(log);
            string baseFilePath = log.BaseLogPath ?? string.Empty;

            // Raw Path 별도 저장
            if (result.RawPath != null && result.RawPath.Count > 0)
            {
                SaveRawPath(baseFilePath, result.RawPath);
                log.RawPathFile = Path.GetFileName($"{baseFilePath}_RawPath.json");
            }

            // Simplified Path 별도 저장 및 분석
            if (result.SimplifiedPath != null && result.SimplifiedPath.Count > 0)
            {
                SaveSimplifiedPath(baseFilePath, result.SimplifiedPath);
                log.SimplifiedPathFile = Path.GetFileName($"{baseFilePath}_SimplifiedPath.json");

                if (log.Success)
                {
                    AnalyzePath(log, result.SimplifiedPath, goalPosition, grid, voxelSize);
                }
            }

            // Search Nodes 별도 저장
            if (LoggingState.NodeRecords.Count > 0)
            {
                SaveSearchNodes(baseFilePath, LoggingState.NodeRecords);
                log.SearchNodesFile = Path.GetFileName($"{baseFilePath}_SearchNodes.json");
            }

            // 실패한 경우 가장 가까운 경로 정보 설정
            if (!log.Success && result.RawPath != null && result.RawPath.Count > 0)
            {
                var closestPos = result.RawPath[result.RawPath.Count - 1];
                log.ClosestPath = new ClosestPathLog
                {
                    ClosestPosition = new Vector3Log(closestPos),
                    DistanceToGoal = Vector3.Distance(closestPos, goalPosition)
                };
            }

            // 업데이트된 로그를 메인 파일에 다시 저장
            string updatedJson = JsonConvert.SerializeObject(log, JsonSettings);
            File.WriteAllText(mainLogFile, updatedJson);

            // 마지막 저장 경로 기록
            _lastSavedLogPath = mainLogFile;

            return mainLogFile;
        }

        /// <summary>
        /// 경로 분석: 꺾임 수, 총 길이, 세그먼트별 정보 계산
        /// </summary>
        private static void AnalyzePath(PathSearchLog log, List<Vector3>? path, Vector3 goal, SparseOccupancyGrid? grid, float voxelSize)
        {
            if (path == null || path.Count < 2)
                return;

            float totalLength = 0;
            int turnCount = 0;
            Vector3? previousDirection = null;

            for (int i = 0; i < path.Count - 1; i++)
            {
                Vector3 current = path[i];
                Vector3 next = path[i + 1];
                Vector3 segment = next - current;
                float segmentLength = segment.Length();

                if (segmentLength > 0.001f)
                {
                    Vector3 direction = Vector3.Normalize(segment);
                    totalLength += segmentLength;

                    bool isTurn = false;
                    string? turnReason = null;

                    if (previousDirection.HasValue)
                    {
                        float dot = Vector3.Dot(previousDirection.Value, direction);
                        if (dot < 0.999f)
                        {
                            turnCount++;
                            isTurn = true;
                            turnReason = CalculateTurnReason(current, previousDirection.Value, direction, goal, grid, voxelSize);
                        }
                    }

                    log.Segments.Add(new PathSegmentLog
                    {
                        Length = segmentLength,
                        Direction = new Vector3Log(direction),
                        StartPosition = new Vector3Log(current),
                        EndPosition = new Vector3Log(next),
                        IsTurn = isTurn,
                        TurnReason = turnReason
                    });

                    previousDirection = direction;
                }
            }

            log.TotalPipeLength = totalLength;
            log.TurnCount = turnCount;
            log.FinalArrivalCost = totalLength;
        }

        private static string CalculateTurnReason(Vector3 turnPoint, Vector3 previousDir, Vector3 newDir, Vector3 goal, SparseOccupancyGrid? grid, float voxelSize)
        {
            if (grid == null)
                return "경로 변경";

            // 이전 방향으로 계속 갔을 때 장애물이 있는지 체크
            Vector3 wouldBeNext = turnPoint + previousDir * voxelSize;
            bool hasObstacle = grid.IsBlocked(wouldBeNext);

            if (hasObstacle)
            {
                return "장애물 회피";
            }
            else
            {
                // 장애물이 없다면 목표 방향으로 접근하기 위한 회전
                Vector3 toGoal = Vector3.Normalize(goal - turnPoint);
                float dotWithNew = Vector3.Dot(toGoal, newDir);
                float dotWithPrev = Vector3.Dot(toGoal, previousDir);

                if (dotWithNew > dotWithPrev + 0.1f)
                {
                    return "목표 방향 접근";
                }
                else
                {
                    return "경로 최적화";
                }
            }
        }

        /// <summary>
        /// 노드 탐색 기록 추가 (GridAStar3D에서 호출)
        /// </summary>
        internal static void RecordSearchNode(Vector3 position, bool isCollision, float gCost = 0, float hCost = 0)
        {
            if (!LoggingState.EnableLogging)
                return;

            LoggingState.NodeRecords.Add(new SearchNodeRecord
            {
                Position = position,
                IsCollision = isCollision,
                GCost = gCost,
                HCost = hCost,
                TotalCost = gCost + hCost
            });

            // NodeFindCount는 NodeRecords의 개수와 항상 동일하게 유지
            LoggingState.NodeFindCount = LoggingState.NodeRecords.Count;
        }

        #region 로그 읽기 메서드

        /// <summary>
        /// 메인 로그 파일을 읽어서 PathSearchLog 객체로 반환
        /// </summary>
        /// <param name="logFilePath">로그 파일 경로 (절대 경로 또는 상대 경로)</param>
        /// <returns>PathSearchLog 객체, 실패 시 null</returns>
        public static PathSearchLog? LoadPathSearchLog(string logFilePath)
        {
            try
            {
                if (!File.Exists(logFilePath))
                {
                    return null;
                }

                string json = File.ReadAllText(logFilePath);
                var log = JsonConvert.DeserializeObject<PathSearchLog>(json);

                // BaseLogPath 설정 (파일명에서 확장자 제거)
                if (log != null)
                {
                    log.BaseLogPath = Path.Combine(
                        Path.GetDirectoryName(logFilePath) ?? string.Empty,
                        Path.GetFileNameWithoutExtension(logFilePath)
                    );
                }

                return log;
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>
        /// 마지막으로 저장된 로그 파일을 읽어서 반환
        /// </summary>
        /// <returns>PathSearchLog 객체, 실패 시 null</returns>
        public static PathSearchLog? LoadLastSavedLog()
        {
            if (_lastSavedLogPath == null || 
                string.IsNullOrEmpty(_lastSavedLogPath))
            {
                return null;
            }

            return LoadPathSearchLog(_lastSavedLogPath);
        }

        /// <summary>
        /// Raw Path 파일을 읽어서 Vector3 리스트로 반환
        /// </summary>
        /// <param name="baseLogPath">로그 파일의 기본 경로 (확장자 제외)</param>
        /// <returns>Vector3 리스트, 실패 시 null</returns>
        public static List<Vector3>? LoadRawPath(string baseLogPath)
        {
            try
            {
                string filePath = $"{baseLogPath}_RawPath.json";

                if (!File.Exists(filePath))
                {
                    return null;
                }

                string json = File.ReadAllText(filePath);
                var pathData = JsonConvert.DeserializeObject<RawPathData>(json);

                if (pathData?.nodes == null)
                    return null;

                var result = new List<Vector3>();
                foreach (var node in pathData.nodes)
                {
                    result.Add(new Vector3(node.X, node.Y, node.Z));
                }

                return result;
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>
        /// Simplified Path 파일을 읽어서 Vector3 리스트로 반환
        /// </summary>
        /// <param name="baseLogPath">로그 파일의 기본 경로 (확장자 제외)</param>
        /// <returns>Vector3 리스트, 실패 시 null</returns>
        public static List<Vector3>? LoadSimplifiedPath(string baseLogPath)
        {
            try
            {
                string filePath = $"{baseLogPath}_SimplifiedPath.json";

                if (!File.Exists(filePath))
                {
                    return null;
                }

                string json = File.ReadAllText(filePath);
                var pathData = JsonConvert.DeserializeObject<SimplifiedPathData>(json);

                if (pathData?.nodes == null)
                    return null;

                var result = new List<Vector3>();
                foreach (var node in pathData.nodes)
                {
                    result.Add(new Vector3(node.X, node.Y, node.Z));
                }

                return result;
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>
        /// Search Nodes 파일을 읽어서 PathSearchNodeLog 리스트로 반환
        /// </summary>
        /// <param name="baseLogPath">로그 파일의 기본 경로 (확장자 제외)</param>
        /// <returns>PathSearchNodeLog 리스트, 실패 시 null</returns>
        public static List<PathSearchNodeLog>? LoadSearchNodes(string baseLogPath)
        {
            try
            {
                string filePath = $"{baseLogPath}_SearchNodes.json";

                if (!File.Exists(filePath))
                {
                    return null;
                }

                string json = File.ReadAllText(filePath);
                var nodeData = JsonConvert.DeserializeObject<SearchNodeData>(json);

                return nodeData?.search_nodes;
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>
        /// 메인 로그와 관련된 모든 파일을 읽어서 반환
        /// </summary>
        /// <param name="mainLogFilePath">메인 로그 파일 경로</param>
        /// <returns>CompleteLogData 객체, 실패 시 null</returns>
        public static CompleteLogData? LoadCompleteLog(string mainLogFilePath)
        {
            try
            {
                var mainLog = LoadPathSearchLog(mainLogFilePath);
                if (mainLog == null || string.IsNullOrEmpty(mainLog.BaseLogPath))
                    return null;

                return new CompleteLogData
                {
                    MainLog = mainLog,
                    RawPath = mainLog.BaseLogPath != null ? LoadRawPath(mainLog.BaseLogPath) : null,
                    SimplifiedPath = mainLog.BaseLogPath != null ? LoadSimplifiedPath(mainLog.BaseLogPath) : null,
                    SearchNodes = mainLog.BaseLogPath != null ? LoadSearchNodes(mainLog.BaseLogPath) : null
                };
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>
        /// 마지막으로 저장된 로그의 모든 파일을 읽어서 반환
        /// </summary>
        /// <returns>CompleteLogData 객체, 실패 시 null</returns>
        public static CompleteLogData? LoadLastCompleteLog()
        {
            if (_lastSavedLogPath == null ||
                string.IsNullOrEmpty(_lastSavedLogPath))
            {
                return null;
            }

            return LoadCompleteLog(_lastSavedLogPath);
        }

        #endregion

        #region 내부 데이터 구조 (JSON 역직렬화용)

        private class RawPathData
        {
            public string? path_type { get; set; }
            public int node_count { get; set; }
            public List<Vector3Log>? nodes { get; set; }
        }

        private class SimplifiedPathData
        {
            public string? path_type { get; set; }
            public int node_count { get; set; }
            public List<Vector3Log>? nodes { get; set; }
        }

        private class SearchNodeData
        {
            public int total_count { get; set; }
            public List<PathSearchNodeLog>? search_nodes { get; set; }
        }

        #endregion
    }

    /// <summary>
    /// 전체 로그 데이터 (메인 로그 + 경로 + 노드)
    /// </summary>
    public class CompleteLogData
    {
        public PathSearchLog? MainLog { get; set; }
        public List<Vector3>? RawPath { get; set; }
        public List<Vector3>? SimplifiedPath { get; set; }
        public List<PathSearchNodeLog>? SearchNodes { get; set; }
    }
}
