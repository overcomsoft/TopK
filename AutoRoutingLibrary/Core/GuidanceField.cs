using System;
using System.Collections.Generic;
using System.Numerics;
using AutoRoutingLibrary.Models;

namespace AutoRoutingLibrary.Core
{
    /// <summary>
    /// 패턴 기반 유도장(Guidance Field) 클래스
    /// 자동 라우팅 탐색 시, 배관이 지나가야 할 바람직한 경로(다발 세그먼트)를 정의하여 
    /// 해당 경로 근처로 탐색을 강력하게 유도(보너스 부여)하는 역할을 담당합니다.
    /// </summary>
    public class GuidanceField
    {
        // 유도선(패턴 다발 세그먼트)들의 목록을 저장
        private readonly List<SegmentData> _referenceSegments = new();
        // 유도 보너스를 받을 수 있는 세그먼트로부터의 최대 반경 거리
        private readonly float _guidanceRadius;
        // 반경 내에 들어왔을 때 A* 탐색 비용(G-Cost)에 곱해질 보너스 가중치 (1.0 미만일수록 비용 감소로 우선 탐색됨)
        private readonly float _bonusCost;

        /// <summary>
        /// GuidanceField 생성자
        /// </summary>
        /// <param name="guidanceRadius">세그먼트 유도 효과가 미치는 공간 반경 거리</param>
        /// <param name="bonusCost">반경 내에 있을 때 부여할 비용 감소 보너스 (예: 0.01이면 100배 빠르게 그 경로를 선호함)</param>
        public GuidanceField(float guidanceRadius = 1000f, float bonusCost = 0.5f)
        {
            _guidanceRadius = guidanceRadius;
            _bonusCost = bonusCost;
        }

        /// <summary>
        /// 참조할 가이드라인 세그먼트(다발 배관)를 유도장에 추가합니다.
        /// </summary>
        /// <param name="start">세그먼트의 시작 3D 좌표</param>
        /// <param name="end">세그먼트의 종료 3D 좌표</param>
        /// <param name="direction">세그먼트의 뻗어나가는 방향 (+X, -Y 등)</param>
        public void AddReferenceSegment(Vector3 start, Vector3 end, string direction)
        {
            _referenceSegments.Add(new SegmentData { Start = start, End = end, Direction = direction });
        }

        /// <summary>
        /// 주어진 현재 좌표(position)가 등록된 유도선 반경 내에 있는지 검사하여 보너스 수치를 반환합니다.
        /// </summary>
        /// <param name="position">현재 A* 알고리즘이 탐색 중인 공간 좌표</param>
        /// <returns>해당 위치에서의 탐색 비용 증감 배수 (기본 1.0f, 유도장 내부면 지정된 bonusCost)</returns>
        public float GetBonusMultiplier(Vector3 position)
        {
            foreach (var seg in _referenceSegments)
            {
                if (IsNearSegment(position, seg))
                {
                    // 거리가 세그먼트 반경(_guidanceRadius) 내부일 경우 지정된 보너스를 부여하여
                    // 배관이 이 영역을 최우선적으로 지나가도록 만듭니다.
                    return _bonusCost; 
                }
            }
            return 1.0f; // 범위 밖이면 일반 비용(보너스 없음) 적용
        }

        /// <summary>
        /// 3D 공간상의 점과 선분 사이의 최단 거리를 계산하여, 반경 안에 들어오는지 판별하는 핵심 기하 알고리즘
        /// </summary>
        private bool IsNearSegment(Vector3 pos, SegmentData seg)
        {
            // 선분의 방향 벡터
            Vector3 segDir = seg.End - seg.Start;
            float len = segDir.Length(); // 선분의 총 길이
            
            // 점 형태의 선분 예외 처리
            if (len == 0) return Vector3.Distance(pos, seg.Start) <= _guidanceRadius;

            segDir /= len; // 방향 벡터 정규화(Normalize)

            // 점 P(pos)와 선분의 시작점(Start)을 잇는 벡터
            Vector3 v = pos - seg.Start;
            // 해당 벡터를 선분 방향으로 투영(Dot Product)한 거리 t
            float t = Vector3.Dot(v, segDir);

            // 투영 지점이 선분 시작점 바깥일 때 (시작점과의 거리 검사)
            if (t < 0) return Vector3.Distance(pos, seg.Start) <= _guidanceRadius;
            // 투영 지점이 선분 종료점 바깥일 때 (종료점과의 거리 검사)
            if (t > len) return Vector3.Distance(pos, seg.End) <= _guidanceRadius;

            // 투영 지점이 선분 내부일 때: 투영 지점의 3D 좌표를 구해 직교 최단 거리 계산
            Vector3 projection = seg.Start + t * segDir;
            return Vector3.Distance(pos, projection) <= _guidanceRadius;
        }

        /// <summary>
        /// 유도장의 기준이 되는 선분 단위의 데이터 구조체
        /// </summary>
        private class SegmentData
        {
            public Vector3 Start { get; set; } // 시작 좌표
            public Vector3 End { get; set; }   // 끝 좌표
            public string Direction { get; set; } = string.Empty; // 방향 메타데이터
        }
    }
}
