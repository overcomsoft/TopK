using Newtonsoft.Json;
using System;
using System.Collections.Generic;
using System.Numerics;
using System.Text;

namespace AutoRouteModule.Log
{

    /// <summary>
    /// 경로 탐색 노드 로그 정보
    /// </summary>
    public class PathSearchNodeLog
    {
        [JsonProperty("g_cost")]
        public float GCost { get; set; }

        [JsonProperty("h_cost")]
        public float HCost { get; set; }

        [JsonProperty("total_cost")]
        public float TotalCost { get; set; }

        [JsonProperty("position")]
        public Vector3Log? Position { get; set; }

        [JsonProperty("is_collision")]
        public bool IsCollision { get; set; }
    }

    /// <summary>
    /// Vector3를 JSON으로 직렬화하기 위한 클래스
    /// </summary>
    public class Vector3Log
    {
        [JsonProperty("x")]
        public float X { get; set; }

        [JsonProperty("y")]
        public float Y { get; set; }

        [JsonProperty("z")]
        public float Z { get; set; }

        public Vector3Log() { }

        public Vector3Log(Vector3 vector)
        {
            X = vector.X;
            Y = vector.Y;
            Z = vector.Z;
        }

        public Vector3Log(float x, float y, float z)
        {
            X = x;
            Y = y;
            Z = z;
        }
    }

    /// <summary>
    /// 경로 세그먼트 정보
    /// </summary>
    public class PathSegmentLog
    {
        [JsonProperty("length")]
        public float Length { get; set; }

        [JsonProperty("direction")]
        public Vector3Log? Direction { get; set; }

        [JsonProperty("start_position")]
        public Vector3Log? StartPosition { get; set; }

        [JsonProperty("end_position")]
        public Vector3Log? EndPosition { get; set; }

        [JsonProperty("is_turn")]
        public bool IsTurn { get; set; }

        [JsonProperty("turn_reason")]
        public string? TurnReason { get; set; }
    }

    /// <summary>
    /// 실패시 가장 가까운 경로 로그
    /// </summary>
    public class ClosestPathLog
    {
        [JsonProperty("closest_position")]
        public Vector3Log? ClosestPosition { get; set; }

        [JsonProperty("distance_to_goal")]
        public float DistanceToGoal { get; set; }
    }

    /// <summary>
    /// GridAStar3D 경로 탐색 로그 데이터
    /// </summary>
    public class PathSearchLog
    {
        [JsonProperty("success")]
        public bool Success { get; set; }

        [JsonProperty("result_code")]
        public string ResultCode { get; set; } = string.Empty;

        [JsonProperty("elapsed_time_ms")]
        public double ElapsedTimeMs { get; set; }

        [JsonProperty("search_node_count")]
        public int SearchNodeCount { get; set; }

        [JsonProperty("turn_count")]
        public int TurnCount { get; set; }

        [JsonProperty("total_pipe_length")]
        public float TotalPipeLength { get; set; }

        [JsonProperty("segment_lengths")]
        public List<PathSegmentLog> Segments { get; set; }

        [JsonProperty("closest_path")]
        public ClosestPathLog? ClosestPath { get; set; }

        [JsonProperty("final_arrival_cost")]
        public float FinalArrivalCost { get; set; }

        [JsonProperty("timestamp")]
        public string Timestamp { get; set; }

        [JsonProperty("start_position")]
        public Vector3Log? StartPosition { get; set; }

        [JsonProperty("goal_position")]
        public Vector3Log? GoalPosition { get; set; }

        // 경로 데이터는 별도 파일로 저장되며, 파일 경로만 기록
        [JsonProperty("raw_path_file")]
        public string? RawPathFile { get; set; }

        [JsonProperty("simplified_path_file")]
        public string? SimplifiedPathFile { get; set; }

        [JsonProperty("search_nodes_file")]
        public string? SearchNodesFile { get; set; }

        // 로그 파일의 기본 경로 (파일명에서 확장자 제거)
        [JsonIgnore]
        public string? BaseLogPath { get; set; }

        public PathSearchLog()
        {
            Segments = new List<PathSegmentLog>();
            Timestamp = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff");
        }
    }
}
