
namespace AutoRouteModule
{
    public enum RESULT_CODES
    {
        FAIL = 0,
        SUCCESS = 1, // 성공
        FAIL_TO_INITIALIZE = 2, // 초기화 실패
        FAIL_TO_START_POINT = 3, // 시작점 설정 실패
        FAIL_TO_END_POINT = 4, // 종료점 설정 실패
        FAIL_TO_PATHFIND = 5,  // 경로 탐색 실패
        TIMEOUT = 6, // 타임아웃
        CANCELLED = 7, // 사용자 중단

    }
}
