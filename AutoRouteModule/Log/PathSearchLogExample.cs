using System;
using System.Numerics;
using AutoRouteModule.Core;
using AutoRouteModule.Log;

namespace AutoRouteModule.Examples
{
    /// <summary>
    /// GridAStar3D 경로 탐색 로그를 JSON으로 저장하는 예제
    /// </summary>
    public class PathSearchLogExample
    {
        /// <summary>
        /// 기본 경로 탐색 로그 저장 예제
        /// </summary>
        public void BasicPathSearchLogExample()
        {
            // 1. GridAStar3D 인스턴스 생성
            var pathfinder = new GridAStar3D();

            // 2. 로깅 활성화 (로그를 수집하려면 반드시 활성화 필요)
            LoggingState.EnableLogging = true;

            // 3. 경로 탐색 실행
            Vector3 start = new Vector3(0, 0, 0);
            Vector3 goal = new Vector3(10, 10, 10);
            float voxelSize = 1.0f;

            PathResult result = pathfinder.FindPath(
                start,
                goal,
                DirectionType.Right,
                voxelSize,
                new PathFindOptions
                {
                    TurnPenalty = 10,
                    MaxSearchNodes = 10000,
                    HeuristicWeight = 1.0f
                });

            // 4. 로그 파일 경로가 result.LogFilePath에 자동 저장됨
            if (!string.IsNullOrEmpty(result.LogFilePath))
            {
                Console.WriteLine($"경로 탐색 로그가 저장되었습니다: {result.LogFilePath}");
                Console.WriteLine($"결과 코드: {result.ResultCode}");
            }

            // 로깅 비활성화
            LoggingState.EnableLogging = false;
        }

        /// <summary>
        /// 특정 디렉토리에 로그 저장하는 예제
        /// </summary>
        public void CustomDirectoryLogExample()
        {
            var pathfinder = new GridAStar3D();
            LoggingState.EnableLogging = true;

            Vector3 start = new Vector3(0, 0, 0);
            Vector3 goal = new Vector3(15, 20, 5);
            float voxelSize = 0.5f;

            PathResult result = pathfinder.FindPath(
                start,
                goal,
                DirectionType.Up,
                voxelSize,
                PathFindOptions.Default);

            if (!string.IsNullOrEmpty(result.LogFilePath))
            {
                Console.WriteLine($"로그 저장 완료: {result.LogFilePath}");
                Console.WriteLine("추가 파일:");
                Console.WriteLine("  - PathLog_YYYYMMDD_HHMMSS_RawPath.json");
                Console.WriteLine("  - PathLog_YYYYMMDD_HHMMSS_SimplifiedPath.json");
                Console.WriteLine("  - PathLog_YYYYMMDD_HHMMSS_SearchNodes.json");
            }

            LoggingState.EnableLogging = false;
        }

        /// <summary>
        /// 웨이포인트를 포함한 경로 탐색 로그 예제
        /// </summary>
        public void WaypointPathSearchLogExample()
        {
            var pathfinder = new GridAStar3D();
            LoggingState.EnableLogging = true;

            Vector3 start = new Vector3(0, 0, 0);
            Vector3 goal = new Vector3(30, 30, 30);
            var waypoints = new System.Collections.Generic.List<Vector3>
            {
                new Vector3(10, 10, 0),
                new Vector3(20, 10, 10),
                new Vector3(20, 20, 20)
            };

            PathResult result = pathfinder.FindPathWithWaypoints(
                start,
                waypoints,
                goal,
                DirectionType.Right,
                1.0f,
                new PathFindOptions
                {
                    TurnPenalty = 15,
                    MaxSearchNodes = 50000,
                    MinStraightDistance = 2.0f
                });

            if (!string.IsNullOrEmpty(result.LogFilePath))
            {
                Console.WriteLine($"웨이포인트 경로 로그 저장: {result.LogFilePath}");
            }

            LoggingState.EnableLogging = false;
        }

        /// <summary>
        /// 실패한 경로 탐색 로그 예제
        /// </summary>
        public void FailedPathSearchLogExample()
        {
            var pathfinder = new GridAStar3D();
            LoggingState.EnableLogging = true;

            Vector3 start = new Vector3(0, 0, 0);
            Vector3 goal = new Vector3(100, 100, 100);

            // 매우 제한적인 옵션으로 실패를 유도
            PathResult result = pathfinder.FindPath(
                start,
                goal,
                DirectionType.Right,
                1.0f,
                new PathFindOptions
                {
                    MaxSearchNodes = 100, // 매우 적은 노드 수로 제한
                    TimeoutMilliseconds = 10 // 매우 짧은 타임아웃
                });

            if (!string.IsNullOrEmpty(result.LogFilePath))
            {
                Console.WriteLine($"실패한 경로 탐색 로그 저장: {result.LogFilePath}");
                Console.WriteLine($"실패 이유: {result.ResultCode}");
            }

            LoggingState.EnableLogging = false;
        }

        /// <summary>
        /// 로그 없이 경로만 탐색하는 예제
        /// </summary>
        public void PathSearchWithoutLoggingExample()
        {
            var pathfinder = new GridAStar3D();

            // 로깅을 활성화하지 않으면 로그 파일이 생성되지 않음
            LoggingState.EnableLogging = false;

            Vector3 start = new Vector3(0, 0, 0);
            Vector3 goal = new Vector3(10, 10, 10);

            PathResult result = pathfinder.FindPath(
                start,
                goal,
                DirectionType.Right,
                1.0f,
                PathFindOptions.Default);

            // result.LogFilePath는 null 또는 빈 문자열
            Console.WriteLine($"경로 탐색 결과: {result.ResultCode}");
            Console.WriteLine($"로그 파일: {(string.IsNullOrEmpty(result.LogFilePath) ? "없음" : result.LogFilePath)}");
        }

        /// <summary>
        /// 로그 파일 읽기 예제
        /// </summary>
        public void LoadLogExample()
        {
            // 1. 특정 로그 파일 읽기
            string logFilePath = "Logs/PathLog_20240315_143025.json";
            var log = LogManager.LoadPathSearchLog(logFilePath);

            if (log != null)
            {
                Console.WriteLine($"로그 로드 성공: {logFilePath}");
                Console.WriteLine($"성공 여부: {log.Success}");
                Console.WriteLine($"경과 시간: {log.ElapsedTimeMs}ms");
                Console.WriteLine($"탐색 노드 수: {log.SearchNodeCount}");
                Console.WriteLine($"총 배관 길이: {log.TotalPipeLength}");
            }
        }

        /// <summary>
        /// 마지막 저장 로그 읽기 예제
        /// </summary>
        public void LoadLastSavedLogExample()
        {
            var pathfinder = new GridAStar3D();

            // 로그를 생성
            LoggingState.EnableLogging = true;
            PathResult result = pathfinder.FindPath(
                new Vector3(0, 0, 0),
                new Vector3(10, 10, 10),
                DirectionType.Right,
                1.0f,
                PathFindOptions.Default);
            LoggingState.EnableLogging = false;

            // 마지막 저장된 로그 읽기
            var lastLog = LogManager.LoadLastSavedLog();

            if (lastLog != null)
            {
                Console.WriteLine("마지막 저장 로그 로드 성공");
                Console.WriteLine($"마지막 저장 경로: {LogManager.LastSavedLogPath}");
                Console.WriteLine($"결과 코드: {lastLog.ResultCode}");
            }
        }

        /// <summary>
        /// 경로 파일들을 개별적으로 읽기 예제
        /// </summary>
        public void LoadIndividualPathFilesExample()
        {
            // 로그 생성
            LoggingState.EnableLogging = true;
            var pathfinder = new GridAStar3D();
            PathResult result = pathfinder.FindPath(
                new Vector3(0, 0, 0),
                new Vector3(10, 10, 10),
                DirectionType.Right,
                1.0f,
                PathFindOptions.Default);
            LoggingState.EnableLogging = false;

            if (result == null || 
                result.LogFilePath == null ||
                string.IsNullOrEmpty(result.LogFilePath))
                return;

            // 메인 로그 읽기
            var mainLog = LogManager.LoadPathSearchLog(result.LogFilePath);
            if (mainLog == null || 
                mainLog.BaseLogPath == null ||
                string.IsNullOrEmpty(mainLog.BaseLogPath))
                return;

            // Raw Path 읽기
            var rawPath = LogManager.LoadRawPath(mainLog.BaseLogPath);
            if (rawPath != null)
            {
                Console.WriteLine($"Raw Path 노드 수: {rawPath.Count}");
            }

            // Simplified Path 읽기
            var simplifiedPath = LogManager.LoadSimplifiedPath(mainLog.BaseLogPath);
            if (simplifiedPath != null)
            {
                Console.WriteLine($"Simplified Path 노드 수: {simplifiedPath.Count}");
            }

            // Search Nodes 읽기
            var searchNodes = LogManager.LoadSearchNodes(mainLog.BaseLogPath);
            if (searchNodes != null)
            {
                Console.WriteLine($"Search Nodes 수: {searchNodes.Count}");
                Console.WriteLine($"첫 번째 노드 G Cost: {searchNodes[0].GCost}");
            }
        }

        /// <summary>
        /// 전체 로그 한번에 읽기 예제
        /// </summary>
        public void LoadCompleteLogExample()
        {
            // 로그 생성
            LoggingState.EnableLogging = true;
            var pathfinder = new GridAStar3D();
            PathResult result = pathfinder.FindPath(
                new Vector3(0, 0, 0),
                new Vector3(10, 10, 10),
                DirectionType.Right,
                1.0f,
                PathFindOptions.Default);
            LoggingState.EnableLogging = false;

            if (result == null || 
                result.LogFilePath == null ||
                string.IsNullOrEmpty(result.LogFilePath))
                return;

            // 전체 로그 한번에 읽기
            var completeLog = LogManager.LoadCompleteLog(result.LogFilePath);

            if (completeLog != null)
            {
                Console.WriteLine("=== 전체 로그 로드 성공 ===");

                if (completeLog.MainLog != null)
                {
                    Console.WriteLine($"성공: {completeLog.MainLog.Success}");
                    Console.WriteLine($"경과 시간: {completeLog.MainLog.ElapsedTimeMs}ms");
                }

                if (completeLog.RawPath != null)
                {
                    Console.WriteLine($"Raw Path 노드 수: {completeLog.RawPath.Count}");
                }

                if (completeLog.SimplifiedPath != null)
                {
                    Console.WriteLine($"Simplified Path 노드 수: {completeLog.SimplifiedPath.Count}");
                }

                if (completeLog.SearchNodes != null)
                {
                    Console.WriteLine($"탐색 노드 수: {completeLog.SearchNodes.Count}");
                }
            }
        }

        /// <summary>
        /// 마지막 저장된 전체 로그 읽기 예제
        /// </summary>
        public void LoadLastCompleteLogExample()
        {
            // 로그 생성
            LoggingState.EnableLogging = true;
            var pathfinder = new GridAStar3D();
            pathfinder.FindPath(
                new Vector3(0, 0, 0),
                new Vector3(10, 10, 10),
                DirectionType.Right,
                1.0f,
                PathFindOptions.Default);
            LoggingState.EnableLogging = false;

            // 마지막 저장된 전체 로그 읽기
            var completeLog = LogManager.LoadLastCompleteLog();

            if (completeLog != null)
            {
                Console.WriteLine("마지막 저장 전체 로그 로드 성공");
                Console.WriteLine($"메인 로그 성공 여부: {completeLog.MainLog?.Success}");
                Console.WriteLine($"Raw Path: {completeLog.RawPath?.Count ?? 0} 노드");
                Console.WriteLine($"Simplified Path: {completeLog.SimplifiedPath?.Count ?? 0} 노드");
                Console.WriteLine($"Search Nodes: {completeLog.SearchNodes?.Count ?? 0} 개");
            }
        }
    }
}
