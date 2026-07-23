
using AutoRouteModule.Core;
using System.Collections.Generic;
using System.Numerics;

namespace AutoRouteModule.Log
{
    /// <summary>
    /// 레거시 디버그 정보 클래스 (deprecated - LogManager 사용 권장)
    /// </summary>
    //[System.Obsolete("DebugInfo는 더 이상 사용되지 않습니다. LoggingState와 LogManager를 사용하세요.")]
    //public static class DebugInfo
    //{
    //    public static bool DebugMode = false;

    //    public static int NodeFindCount = 0;
    //    public static List<DebugRecordInfo> FindRecord = new List<DebugRecordInfo>(30000);

    //    public static List<DebugAABBRecordInfo> AABBCheckRecord = new List<DebugAABBRecordInfo>(30000);
    //}

    //public class DebugRecordInfo
    //{
    //    public Vector3 pos;
    //    public bool isOccupied;
    //}

    //public class DebugAABBRecordInfo
    //{
    //    public AABB? aabb;
    //    public bool isOccupied;
    //}
}
