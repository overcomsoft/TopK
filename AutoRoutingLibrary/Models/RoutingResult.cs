using System;
using System.Collections.Generic;
using System.Numerics;

namespace AutoRoutingLibrary.Models
{
    /// <summary>
    /// A* 라우팅 탐색이 완료된 후, UI 또는 상위 모듈로 반환되는 결과 데이터를 담는 클래스입니다.
    /// 경로 탐색의 성공 여부, 최종 추출된 경로, 성능 지표 등을 포함합니다.
    /// </summary>
    public class RoutingResult
    {
        // 탐색 성공 여부 (true: 목적지 도달 성공, false: 막힘 또는 시간 초과 등)
        public bool Success { get; set; }
        
        // 꺾이는 지점(Waypoint)들의 3D 좌표 리스트. (성공 시 시작점부터 끝점까지 차례대로 연결된 경로)
        public List<Vector3> Path { get; set; } = new();
        
        // 실패 시 그 원인을 사용자에게 알려주기 위한 상세 에러 메시지
        public string ErrorMessage { get; set; } = string.Empty;
        
        // A* 알고리즘 내부 루프(탐색)가 반복된 총 횟수 (복잡도/연산량 확인 용도)
        public int Iterations { get; set; }
        
        // 경로 탐색을 시작하고 완료될 때까지 걸린 실제 소요 시간
        public TimeSpan ElapsedTime { get; set; }
        
        // (선택적) 단계별 로그나 프로세스 디테일을 문자열 형태로 저장
        public string StepDetails { get; set; } = string.Empty;
    }
}
