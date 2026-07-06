# C++ 고무줄 라우팅 엔진 C# P/Invoke 연동 및 사용법 튜토리얼 (초급자용)

본 튜토리얼은 C++로 빌드된 고무줄 라우팅 엔진(`RubberBandRouting.Native.dll`)을 C# 프로그램(WPF 뷰어, 콘솔 앱 등)에서 연동하여 배관 경로 설계 자동화 프로그램을 처음부터 직접 구축할 수 있도록 돕는 단계별 안내서입니다.

C# 초급자도 따라할 수 있도록 **C++ DLL 빌드 확인 → Interop(상호작용) 선언 → 엔진 초기화 → 데이터 입력(장애물, PoC, 특징점) → 라우팅 실행 → 결과 파싱 및 파일 저장**까지 단계별 예제 코드를 제공합니다.

---

## 0. 사전 준비 (Prerequisites)

C#에서 C++ 엔진을 호출하려면 먼저 C++ 코드를 DLL(동적 링크 라이브러리) 파일로 빌드해야 합니다.

1. **C++ DLL 빌드**:
   * Visual Studio의 `Developer Command Prompt` 또는 MSVC 컴파일러가 설정된 터미널을 엽니다.
   * `cpp/RubberBandRouting.Native/` 폴더로 이동하여 `build_msvc.bat`를 실행합니다.
     ```bat
     cd D:\DINNO\DEV\AI-AutoRouting\TopKGen\RubberBandRoutingSuite\cpp\RubberBandRouting.Native
     build_msvc.bat
     ```
   * 성공적으로 완료되면 **`RubberBandRouting.Native.dll`** 파일이 생성됩니다.

2. **DLL 파일 배치**:
   * 생성된 `RubberBandRouting.Native.dll`을 작성하려는 C# 실행 프로그램(`.exe`)이 위치할 폴더(예: `bin/Debug/net8.0/`)에 복사하여 붙여넣거나 시스템 PATH 환경변수에 등록합니다.
   * *WPF 뷰어 프로젝트에서는 빌드 시 자동으로 복사되도록 프로젝트 파일(`csproj`)에 빌드 스크립트가 이미 포함되어 있습니다.*

---

## 1단계: C# 프로젝트 생성 및 P/Invoke 연동 선언

C++ DLL 안의 구조체와 함수들을 C# 프로그램에서 인식할 수 있도록 인터옵(Interop) 클래스를 작성합니다.

### 1.1 데이터 구조체 선언
C++의 구조체 메모리 정렬 방식을 흉내 내기 위해 `[StructLayout(LayoutKind.Sequential)]` 특성(Attribute)을 지정합니다.

```csharp
using System;
using System.Runtime.InteropServices;

namespace RubberBandTutorial
{
    // 3차원 공간 좌표 구조체 (C++의 RbVec3에 대응)
    [StructLayout(LayoutKind.Sequential)]
    public struct RbVec3
    {
        public double X;
        public double Y;
        public double Z;

        public RbVec3(double x, double y, double z)
        {
            X = x;
            Y = y;
            Z = z;
        }
    }

    // 3차원 축정렬 경계상자 장애물 구조체 (C++의 RbAabb에 대응)
    [StructLayout(LayoutKind.Sequential)]
    public struct RbAabb
    {
        public double MinX;
        public double MinY;
        public double MinZ;
        public double MaxX;
        public double MaxY;
        public double MaxZ;
        public int IsPenetration; // 1이면 관통 가능 슬리브 통로(충돌 무시), 0이면 단단한 장애물
    }

    // 라우팅 엔진 설정 구조체 (C++의 RbConfig에 대응)
    [StructLayout(LayoutKind.Sequential)]
    public struct RbConfig
    {
        public int MaxVerticalBends; // 최대 수직 꺾임 허용 횟수 (기본값: 5)
        public double SafetyMargin;  // 장애물과의 안전 거리 (단위: mm)
        public double TrayWidth;     // 배관을 감싸는 가상의 트레이 폭 (단위: mm)
        public double TrayHeight;    // 배관을 감싸는 가상의 트레이 높이 (단위: mm)
        public double PipePitch;     // 다중 배관 배치 시 배관 중심간의 간격 (단위: mm)
        public int PipeCount;        // 배치할 배관의 가닥 수 (기본값: 1)
        public double SnapTolerance; // 기존설계 특징점에 흡착되는 허용 반경 (단위: mm)
        public double PipeDiameter;  // 파이프 외경 크기 (단위: mm, 간섭 검증에 사용)
    }
}
```

### 1.2 DLL 함수 가져오기 (DllImport)
C++ DLL에 들어있는 함수들을 연결합니다. DLL 내부의 함수 이름과 동일하게 C#에 `extern` 메서드로 선언합니다.

```csharp
namespace RubberBandTutorial
{
    internal static class NativeMethods
    {
        // 64비트 DLL 파일명을 명시합니다. (.dll 확장자는 제외 가능)
        private const string LibraryName = "RubberBandRouting.Native";

        // 1. 엔진 인스턴스 생성 및 수명 관리
        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern IntPtr rb_create();

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern void rb_destroy(IntPtr engine);

        // 2. 엔진 설정 및 입력 데이터 주입
        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_initialize(IntPtr engine, RbConfig config);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_set_obstacles(IntPtr engine, RbAabb[] obstacles, int count);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_set_features(IntPtr engine, RbVec3[] features, int count);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_set_feature_flags(IntPtr engine, int[] required, int count);

        // 3. 라우팅 실행
        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_execute(IntPtr engine, RbVec3 start, RbVec3 end);

        // 4. 대표 중심선(Centerline) 결과 추출
        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_get_segment_count(IntPtr engine);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_copy_segments(IntPtr engine, [In, Out] RbVec3[]? outPoints, int maxPoints);

        // 5. 다중 파이프(Pipes) 결과 추출
        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_get_pipe_count(IntPtr engine);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_copy_pipe_path(IntPtr engine, int pipeIndex, [In, Out] RbVec3[]? outPoints, int maxPoints);

        // 6. 결과 분석 및 진단
        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_get_vertical_bends(IntPtr engine);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_get_fallback_count(IntPtr engine);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_is_valid(IntPtr engine);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_get_segment_reason(IntPtr engine, int segmentIndex);
    }
}
```

---

## 2단계: 엔진 생성 및 환경 설정 (Initialization)

C++ 엔진은 네이티브 메모리를 다루므로 생성(`rb_create`) 후 작업이 완료되면 반드시 명시적으로 소멸(`rb_destroy`)시켜서 메모리 누수(Memory Leak)를 방지해야 합니다. C#에서는 `try-finally` 구문을 활용해 안전하게 생명주기를 제어합니다.

```csharp
// 1. 엔진 인스턴스 생성
IntPtr engine = NativeMethods.rb_create();
if (engine == IntPtr.Zero)
{
    Console.WriteLine("엔진 생성에 실패했습니다.");
    return;
}

try
{
    // 2. 파라미터 옵션 설정
    RbConfig config = new RbConfig
    {
        MaxVerticalBends = 5,    // 수직 꺾임 한계 5회
        SafetyMargin = 50.0,     // 장애물과 50mm 안전 마진 유지
        TrayWidth = 600.0,       // 트레이 폭 600mm
        TrayHeight = 100.0,      // 트레이 높이 100mm
        PipePitch = 100.0,       // 배관 중심 간격 100mm
        PipeCount = 3,           // 최종 3가닥 평행배관 분배 생성
        SnapTolerance = 300.0,   // 기존설계 특징점 주위 300mm 이내 흡착 허용
        PipeDiameter = 50.0      // 50mm(50A 등) 관경 적용
    };

    // 3. 엔진 초기화
    int initStatus = NativeMethods.rb_initialize(engine, config);
    if (initStatus != 0)
    {
        Console.WriteLine("엔진 초기화에 실패했습니다.");
        return;
    }
    
    Console.WriteLine("엔진이 정상적으로 생성 및 초기화되었습니다.");
}
finally
{
    // 4. 반드시 수행해야 할 네이티브 메모리 해제
    NativeMethods.rb_destroy(engine);
    Console.WriteLine("엔진 메모리가 해제되었습니다.");
}
```

---

## 3단계: 주변 장애물 데이터 등록 (Register Obstacles)

3D 공간의 대형 설비나 벽면 등을 대표하는 축정렬 상자(AABB)들을 배열로 준비하여 엔진에 등록합니다. `IsPenetration` 옵션을 통해 일반 고체 장애물과 관통 홀(Sleeve)을 분리할 수 있습니다.

```csharp
// 장애물 배열 생성 (mm 단위)
RbAabb[] obstacles = new RbAabb[]
{
    // 1. 일반 고체 장애물 (IsPenetration = 0)
    new RbAabb {
        MinX = 1000.0, MinY = 1000.0, MinZ = 0.0,
        MaxX = 2500.0, MaxY = 3000.0, MaxZ = 1500.0,
        IsPenetration = 0
    },
    // 2. 관통하여 지나갈 수 있는 장비 슬리브 (IsPenetration = 1)
    new RbAabb {
        MinX = 4000.0, MinY = 2000.0, MinZ = 800.0,
        MaxX = 4500.0, MaxY = 2200.0, MaxZ = 1000.0,
        IsPenetration = 1
    }
};

// 엔진에 장애물 배열 전달
int obstacleStatus = NativeMethods.rb_set_obstacles(engine, obstacles, obstacles.Length);
if (obstacleStatus != 0)
{
    Console.WriteLine("장애물 등록 실패!");
}
else
{
    Console.WriteLine($"{obstacles.Length}개의 장애물이 엔진에 등록되었습니다.");
}
```

---

## 4단계: 기존설계 특징점 반영 (Pull Snap Features)

이 고무줄 엔진의 핵심인 **"기존 설계 흐름 반영(Pull Mechanism)"**을 위해, 과거 검증된 시공 경로의 주요 엘보(Elbow)나 고도변경점 등의 특징점 목록을 엔진에 가이드 정보로 넘겨줍니다.

* **일반 특징점**: `required = 0`으로 설정. 경로에서 너무 멀리 우회하게 하거나 오차가 큰 특징점은 엔진 내부 스냅 필터(`maxDetour`, `SnapTolerance`)가 걸러내어 무시합니다.
* **필수 특징점**: `required = 1`로 설정. 엔진의 모든 필터를 우회하여 **반드시** 이 지점을 경유하도록 강제합니다. (예: 장비 연결부 직후의 강제 수직 하강 스텁 등)

```csharp
// 기존설계로부터 추출한 꺾임 제어점들
RbVec3[] features = new RbVec3[]
{
    new RbVec3(500, 500, 1500),   // 1번 제어점
    new RbVec3(3000, 1500, 1500), // 2번 제어점
    new RbVec3(5000, 3000, 900)   // 3번 제어점
};

// 각 특징점의 필수 경유 여부 (1: 필수, 0: 선택)
int[] requiredFlags = new int[]
{
    1, // 1번 특징점은 시작 PoC 직후 고도 하강을 위해 필수 경유 지정
    0, // 2번은 장애물 회피 조건에 따라 우회 가능하도록 선택 지정
    0  // 3번도 선택 지정
};

// 1. 특징점 좌표 배열 주입
NativeMethods.rb_set_features(engine, features, features.Length);

// 2. 특징점 각각의 필수 플래그 주입
NativeMethods.rb_set_feature_flags(engine, requiredFlags, requiredFlags.Length);

Console.WriteLine("기존설계 특징점이 반영되었습니다.");
```

---

## 5단계: 출발지/목적지 설정 및 라우팅 계산 실행

출발지 PoC와 종단 목적지 PoC 좌표를 지정하고 최단 경로 자동 라우팅을 실행합니다.

```csharp
// 출발지와 종단 목적지 설정 (mm 단위)
RbVec3 start = new RbVec3(100.0, 500.0, 2000.0);
RbVec3 end = new RbVec3(6000.0, 4500.0, 900.0);

Console.WriteLine("라우팅 알고리즘을 계산하는 중...");

// 라우팅 엔진 실행 (직선 팽팽함 인장 -> 특징점 스냅 -> A* 회피 -> 단축/병합 -> 다중배관 오프셋)
int executeResult = NativeMethods.rb_execute(engine, start, end);

if (executeResult != 0)
{
    Console.WriteLine("라우팅 계산 중 에러 발생!");
}
else
{
    Console.WriteLine("라우팅 계산이 정상적으로 완료되었습니다.");
}
```

---

## 6단계: 결과 데이터 파싱 및 가공

라우팅 계산 결과인 **대표 중심선 좌표**와 분배된 **다중 파이프라인의 좌표 리스트**를 C# 배열 구조로 읽어옵니다. C++에서 C#으로 동적 배열 크기를 마샬링할 때는 **2-Pass 복사 패턴**을 사용합니다.

* **1-Pass**: 출력 버퍼 매개변수에 `null`을 넣어 호출하여 필요한 배열 크기를 먼저 구합니다.
* **2-Pass**: 알맞은 크기의 C# 배열을 할당하여 실제 좌표 데이터들을 복사받습니다.

```csharp
// ==========================================
// 1. 대표 중심선(Centerline) 경로 파싱
// ==========================================
int segmentCount = NativeMethods.rb_get_segment_count(engine);
int pointCountNeeded = segmentCount + 1; // 세그먼트가 N개이면 점은 N+1개

RbVec3[] centerlinePoints = new RbVec3[pointCountNeeded];
NativeMethods.rb_copy_segments(engine, centerlinePoints, pointCountNeeded);

Console.WriteLine("\n[대표 중심선 경로 결과]");
for (int i = 0; i < centerlinePoints.Length; i++)
{
    // 각 꺾임 지점의 생성 원인(Reason Code) 파악
    string reason = "일반 정점";
    if (i < segmentCount)
    {
        int code = NativeMethods.rb_get_segment_reason(engine, i);
        reason = code switch
        {
            0 => "경로시작(Start)",
            1 => "기존설계스냅(Snap)",
            2 => "장애물우회(Bypass)",
            3 => "방향전환(Turn)",
            4 => "고도변경(Z-Change)",
            5 => "고무줄정렬(Alignment)",
            _ => "기타"
        };
    }
    var pt = centerlinePoints[i];
    Console.WriteLine($"  정점 {i}: ({pt.X:F0}, {pt.Y:F0}, {pt.Z:F0}) [원인: {reason}]");
}

// ==========================================
// 2. 분배된 다중 파이프라인(Pipes) 경로 파싱
// ==========================================
int pipeCount = NativeMethods.rb_get_pipe_count(engine);
List<RbVec3[]> allPipes = new List<RbVec3[]>();

for (int p = 0; p < pipeCount; p++)
{
    // 1-Pass: p번째 파이프 경로의 정점 개수 확인
    int ptsNeeded = NativeMethods.rb_copy_pipe_path(engine, p, null, 0);
    
    // 2-Pass: 정점 개수만큼 배열 할당 후 복사
    RbVec3[] pipePoints = new RbVec3[ptsNeeded];
    NativeMethods.rb_copy_pipe_path(engine, p, pipePoints, ptsNeeded);
    
    allPipes.Add(pipePoints);
    Console.WriteLine($"  분배 파이프 {p} 가닥 정점 수: {pipePoints.Length}개");
}

// ==========================================
// 3. 자가 검증 결과 진단 (Validation)
// ==========================================
int isValid = NativeMethods.rb_is_valid(engine);
int verticalBends = NativeMethods.rb_get_vertical_bends(engine);
int fallbackCount = NativeMethods.rb_get_fallback_count(engine);

Console.WriteLine($"\n[엔진 진단 결과]");
Console.WriteLine($"  - 최종 경로 적합성 여부: {(isValid != 0 ? "합격 (SUCCESS)" : "확인 필요 (WARNING)")}");
Console.WriteLine($"  - 수직 꺾임 횟수: {verticalBends}회");
Console.WriteLine($"  - A* 탐색 실패(우회 fallback) 횟수: {fallbackCount}회");
```

---

## 7단계: 경로 탐색 결과 파일 저장 (Save Results)

설계 결과를 추후 사용자가 활용하거나 3D 뷰어에서 로드할 수 있도록 디스크에 파일로 기록합니다. JSON 형식으로 저장하면 구조화가 용이하여 초급자에게 강력하게 추천되는 방식입니다.

```sharp
using System.IO;
using System.Text.Json;

// 파일에 저장할 데이터 패키지 클래스 정의
public class RoutingResultSaveModel
{
    public DateTime CreatedTime { get; set; }
    public double TotalLengthMm { get; set; }
    public int VerticalBends { get; set; }
    public bool IsValid { get; set; }
    public List<RbVec3> Centerline { get; set; } = new();
    public List<List<RbVec3>> DistributedPipes { get; set; } = new();
}

// ... (계산 완료 후 실행되는 저장 파트) ...

var saveModel = new RoutingResultSaveModel
{
    CreatedTime = DateTime.Now,
    VerticalBends = verticalBends,
    IsValid = (isValid != 0)
};

// 대표 중심선 및 파이프라인 리스트 복사
double totalLength = 0.0;
for (int i = 0; i < centerlinePoints.Length; i++)
{
    saveModel.Centerline.Add(centerlinePoints[i]);
    if (i > 0)
    {
        var dx = centerlinePoints[i].X - centerlinePoints[i - 1].X;
        var dy = centerlinePoints[i].Y - centerlinePoints[i - 1].Y;
        var dz = centerlinePoints[i].Z - centerlinePoints[i - 1].Z;
        totalLength += Math.Sqrt(dx * dx + dy * dy + dz * dz);
    }
}
saveModel.TotalLengthMm = totalLength;

foreach (var pipe in allPipes)
{
    saveModel.DistributedPipes.Add(new List<RbVec3>(pipe));
}

// JSON 형식 문자열로 직렬화
var jsonOptions = new JsonSerializerOptions { WriteIndented = true };
string jsonString = JsonSerializer.Serialize(saveModel, jsonOptions);

// 디바이스 경로에 저장
string targetPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "routing_result.json");
File.WriteAllText(targetPath, jsonString);

Console.WriteLine($"\n라우팅 결과 파일이 성공적으로 저장되었습니다:\n-> {targetPath}");
```

---

## 종합 완전 예제 (Console Application용 단일 소스)

이 코드를 통째로 복사하여 C# .NET Core Console App 프로젝트의 `Program.cs`에 붙여넣고 컴파일하여 한 줄씩 디버깅해 보며 동작을 완벽히 이해할 수 있습니다.

```csharp
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;

namespace RubberBandTutorial
{
    [StructLayout(LayoutKind.Sequential)]
    public struct RbVec3
    {
        public double X, Y, Z;
        public RbVec3(double x, double y, double z) { X = x; Y = y; Z = z; }
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct RbAabb
    {
        public double MinX, MinY, MinZ, MaxX, MaxY, MaxZ;
        public int IsPenetration;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct RbConfig
    {
        public int MaxVerticalBends;
        public double SafetyMargin;
        public double TrayWidth;
        public double TrayHeight;
        public double PipePitch;
        public int PipeCount;
        public double SnapTolerance;
        public double PipeDiameter;
    }

    internal static class NativeMethods
    {
        private const string LibraryName = "RubberBandRouting.Native";

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern IntPtr rb_create();

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern void rb_destroy(IntPtr engine);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_initialize(IntPtr engine, RbConfig config);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_set_obstacles(IntPtr engine, RbAabb[] obstacles, int count);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_set_features(IntPtr engine, RbVec3[] features, int count);

        [DllImport(DllImportSearchPath.AssemblyDirectory)]
        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_set_feature_flags(IntPtr engine, int[] required, int count);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_execute(IntPtr engine, RbVec3 start, RbVec3 end);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_get_segment_count(IntPtr engine);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_copy_segments(IntPtr engine, [In, Out] RbVec3[]? outPoints, int maxPoints);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_get_pipe_count(IntPtr engine);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_copy_pipe_path(IntPtr engine, int pipeIndex, [In, Out] RbVec3[]? outPoints, int maxPoints);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_get_vertical_bends(IntPtr engine);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_get_fallback_count(IntPtr engine);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_is_valid(IntPtr engine);

        [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
        public static extern int rb_get_segment_reason(IntPtr engine, int segmentIndex);
    }

    public class RoutingResultSaveModel
    {
        public DateTime CreatedTime { get; set; }
        public double TotalLengthMm { get; set; }
        public int VerticalBends { get; set; }
        public bool IsValid { get; set; }
        public List<RbVec3> Centerline { get; set; } = new();
        public List<List<RbVec3>> DistributedPipes { get; set; } = new();
    }

    class Program
    {
        static void Main(string[] args)
        {
            Console.WriteLine("=================================================");
            Console.WriteLine("   고무줄 라우팅 엔진 사용 예제 콘솔 프로그램   ");
            Console.WriteLine("=================================================");

            // 1. 엔진 인스턴스 생성
            IntPtr engine = NativeMethods.rb_create();
            if (engine == IntPtr.Zero)
            {
                Console.WriteLine("에러: 라우팅 엔진 생성에 실패했습니다.");
                return;
            }

            try
            {
                // 2. 엔진 규격 및 파라미터 초기 설정
                RbConfig config = new RbConfig
                {
                    MaxVerticalBends = 5,
                    SafetyMargin = 50.0,
                    TrayWidth = 600.0,
                    TrayHeight = 100.0,
                    PipePitch = 100.0,
                    PipeCount = 3,         // 3가닥 다중배관 자동 분배 설정
                    SnapTolerance = 300.0,
                    PipeDiameter = 50.0
                };
                NativeMethods.rb_initialize(engine, config);

                // 3. 주변 물리 장애물 등록 (가상의 단단한 박스 배치)
                RbAabb[] obstacles = new RbAabb[]
                {
                    new RbAabb { MinX = 1000.0, MinY = 1000.0, MinZ = 0.0, MaxX = 2500.0, MaxY = 3000.0, MaxZ = 1500.0, IsPenetration = 0 }
                };
                NativeMethods.rb_set_obstacles(engine, obstacles, obstacles.Length);

                // 4. 기존설계 기반 특징점 및 우선순위 등록
                RbVec3[] features = new RbVec3[]
                {
                    new RbVec3(500, 500, 1500),   // 시작 직후 꺾이는 지점
                    new RbVec3(3000, 1500, 1500)  // 우회 시공된 엘보 지점
                };
                int[] requiredFlags = new int[] { 1, 0 }; // 첫 번째 특징점은 필수(1) 지정
                NativeMethods.rb_set_features(engine, features, features.Length);
                NativeMethods.rb_set_feature_flags(engine, requiredFlags, requiredFlags.Length);

                // 5. 출발 PoC 및 목적 PoC 설정
                RbVec3 start = new RbVec3(100.0, 500.0, 2000.0);
                RbVec3 end = new RbVec3(6000.0, 4500.0, 900.0);

                // 6. 최단경로 라우팅 알고리즘 연산 실행
                Console.WriteLine("라우팅 연산을 실행합니다...");
                int execStatus = NativeMethods.rb_execute(engine, start, end);
                if (execStatus != 0)
                {
                    Console.WriteLine("에러: 라우팅 계산 중 치명적 결함 발생.");
                    return;
                }

                // 7. 계산 결과 추출 (중심선 폴리라인 수집)
                int segmentCount = NativeMethods.rb_get_segment_count(engine);
                RbVec3[] centerlinePoints = new RbVec3[segmentCount + 1];
                NativeMethods.rb_copy_segments(engine, centerlinePoints, centerlinePoints.Length);

                Console.WriteLine("\n[대표 중심선 추출 결과]");
                for (int i = 0; i < centerlinePoints.Length; i++)
                {
                    var pt = centerlinePoints[i];
                    int code = i < segmentCount ? NativeMethods.rb_get_segment_reason(engine, i) : -1;
                    string reason = code switch
                    {
                        0 => "경로시작", 1 => "특징점스냅", 2 => "장애물우회",
                        3 => "방향전환", 4 => "고도변경", 5 => "고무줄정렬", _ => "종단접근"
                    };
                    Console.WriteLine($"  정점 {i}: ({pt.X:F0}, {pt.Y:F0}, {pt.Z:F0}) - {reason}");
                }

                // 8. 계산 결과 추출 (다중 평행배관 수집)
                int pipeCount = NativeMethods.rb_get_pipe_count(engine);
                List<RbVec3[]> pipes = new List<RbVec3[]>();
                Console.WriteLine($"\n[평행 분배 배관 가닥 수]: {pipeCount}개");
                for (int p = 0; p < pipeCount; p++)
                {
                    int ptsCount = NativeMethods.rb_copy_pipe_path(engine, p, null, 0);
                    RbVec3[] pipePoints = new RbVec3[ptsCount];
                    NativeMethods.rb_copy_pipe_path(engine, p, pipePoints, ptsCount);
                    pipes.Add(pipePoints);
                }

                // 9. 엔진의 결과 적격성 진단 데이터 확인
                int isValid = NativeMethods.rb_is_valid(engine);
                int bends = NativeMethods.rb_get_vertical_bends(engine);
                int fallbacks = NativeMethods.rb_get_fallback_count(engine);
                Console.WriteLine("\n[자가 경로 타당성 검사]");
                Console.WriteLine($"  - 적격 판정 여부: {(isValid != 0 ? "합격(PASS)" : "경고(WARNING)")}");
                Console.WriteLine($"  - 수직 꺾임 수: {bends}회");
                Console.WriteLine($"  - A* 에러 대체 수: {fallbacks}회");

                // 10. 직렬화(JSON) 및 파일 디스크 저장
                var saveModel = new RoutingResultSaveModel
                {
                    CreatedTime = DateTime.Now,
                    VerticalBends = bends,
                    IsValid = (isValid != 0),
                    DistributedPipes = new List<List<RbVec3>>()
                };

                double totalLength = 0.0;
                for (int i = 0; i < centerlinePoints.Length; i++)
                {
                    saveModel.Centerline.Add(centerlinePoints[i]);
                    if (i > 0)
                    {
                        var dx = centerlinePoints[i].X - centerlinePoints[i - 1].X;
                        var dy = centerlinePoints[i].Y - centerlinePoints[i - 1].Y;
                        var dz = centerlinePoints[i].Z - centerlinePoints[i - 1].Z;
                        totalLength += Math.Sqrt(dx * dx + dy * dy + dz * dz);
                    }
                }
                saveModel.TotalLengthMm = totalLength;

                foreach (var pipe in pipes)
                {
                    saveModel.DistributedPipes.Add(new List<RbVec3>(pipe));
                }

                string jsonStr = JsonSerializer.Serialize(saveModel, new JsonSerializerOptions { WriteIndented = true });
                string savePath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "routing_result.json");
                File.WriteAllText(savePath, jsonStr);

                Console.WriteLine($"\n결과 파일 저장 완료:\n-> {savePath}");
            }
            catch (Exception ex)
            {
                Console.WriteLine($"예기치 못한 실행 오류: {ex.Message}");
            }
            finally
            {
                // 네이티브 메모리 자원 완전 해제
                NativeMethods.rb_destroy(engine);
                Console.WriteLine("\n엔진 네이티브 리소스가 회수되었습니다. 프로그램을 종료합니다.");
            }
        }
    }
}
```
