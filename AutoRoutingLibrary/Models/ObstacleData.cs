using System;
using System.Numerics;

namespace AutoRoutingLibrary.Models
{
    /// <summary>
    /// 라우팅 알고리즘이 회피해야 할 3D 장애물(설비, 구조물, 기존 배관 등)을 정의하는 데이터 모델
    /// </summary>
    public class ObstacleData
    {
        // 장애물의 고유 식별자 (GUID 등)
        public string Id { get; set; } = string.Empty;
        
        // 장애물의 유형 카테고리 (예: "Grating", "Floor", "Beam", "Equipment", "Pipe")
        public string Type { get; set; } = string.Empty; 
        
        // 장애물이 차지하는 3차원 물리적 영역 (Bounding Box)
        public BoundingBox3D BoundingBox { get; set; }

        /// <summary>
        /// 배관이 해당 장애물 내부를 뚫고 지나가도(통과해도) 되는지 판별하는 함수입니다.
        /// 시작점(장비 내부 PoC)이나 덕트 내부 등 충돌로 판정하면 안 되는 대상을 걸러냅니다.
        /// </summary>
        /// <returns>무시(통과) 가능한 장애물이면 true 반환</returns>
        public bool IsPassThrough()
        {
            if (string.IsNullOrEmpty(Type)) return false;
            
            var t = Type.ToLower(); // 대소문자 구분 없이 검사하기 위해 소문자 변환
            
            // 그레이팅, 바닥판, 빔(보), 장비(Equipment), 덕트 등은 
            // 현재 PoC 라우팅 테스트 목적 상 내부 관통을 허용(충돌 예외)하도록 처리합니다.
            return t.Contains("grating") || 
                   t.Contains("floor") || 
                   t.Contains("격자보") || 
                   t.Contains("beam") ||
                   t.Contains("equipment") ||
                   t.Contains("장비") ||
                   t.Contains("duct") ||
                   t.Contains("덕트");
        }
    }
}
