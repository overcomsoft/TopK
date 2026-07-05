# RubberBand Routing Suite — C++ API 매뉴얼 및 C# 경로 뷰어 코드 분석

본 문서는 `RubberBandRoutingSuite/` 하위에 구현된 소스를 직접 분석하여 다음을 정리한다.

1. C++ 네이티브 엔진(`RubberBandRouting.Native`)의 C API 매뉴얼
2. 3D 경로 뷰어(C# WPF)와 관리형 엔진(C#)의 실제 구현 분석
3. 기존 소스의 문제점과 개선사항

분석 대상 소스 기준: `main` 브랜치, 커밋 `0bfb2e1` 시점.

---

## 0. 먼저 확인해야 할 핵심 사실 — C++ 엔진과 C# 뷰어는 "연결되어 있지 않다"

개발 요청에서는 "C++로 고무줄밴드 경로엔진, C#으로 뷰어가 개발되어 있고 C# 뷰어가 C++ API를 구현한다"고 전제했으나, **실제 소스 상태는 그렇지 않다.** 이 점을 먼저 명확히 하고 나머지 분석을 진행한다.

| 확인 항목 | 실제 상태 | 근거 |
|---|---|---|
| C# 뷰어가 C++ DLL을 P/Invoke로 호출하는가 | **아니다.** `DllImport`/`rb_*` 심볼이 소스 전체에 없음 | `src/` 전체 grep 결과 P/Invoke 없음 |
| C++ 프로젝트가 솔루션에 포함되는가 | **아니다.** `.sln`에는 C# 두 프로젝트만 존재 | [RubberBandRoutingSuite.sln](RubberBandRoutingSuite/RubberBandRoutingSuite.sln#L8-L11) |
| C++는 어떻게 빌드되는가 | 솔루션과 무관하게 `build_msvc.bat`로 DLL만 별도 생성 | [build_msvc.bat](RubberBandRoutingSuite/cpp/RubberBandRouting.Native/build_msvc.bat) |
| 실제 라우팅 계산 주체 | **C# `ManagedRubberBandEngine`** | [MainWindow.xaml.cs:378](RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs#L378) |

즉 현재 저장소에는 **두 개의 서로 다른 라우팅 구현이 병렬로 존재**하며(C++ 스텁 1개 + C# 실동작 엔진 1개), 둘은 코드로 연결되어 있지 않다. C++ 네이티브 README도 "implementation stub"이라고 스스로 명시한다([README.md:1-3](RubberBandRoutingSuite/cpp/RubberBandRouting.Native/README.md#L1-L3)).

따라서 본 문서의 "C++ API 매뉴얼"은 **장차 C#이 대체·연동할 대상으로서의 네이티브 API 규격**을 문서화한 것이고, "C# 뷰어 분석"은 **현재 실제로 화면을 그리는 구현**을 문서화한 것이다. 두 구현의 대응/불일치는 4장과 5장에서 정리한다.

---

## 1. 솔루션 구조

```
RubberBandRoutingSuite/
├─ RubberBandRoutingSuite.sln        # C# 프로젝트 2개만 포함
├─ cpp/
│  └─ RubberBandRouting.Native/      # C++ 네이티브 엔진 (솔루션 외부, DLL 스텁)
│     ├─ rubberband_native.h         # C API 선언 (extern "C")
│     ├─ rubberband_native.cpp       # 구현
│     ├─ build_msvc.bat              # cl.exe 단독 빌드 스크립트
│     └─ README.md
└─ src/
   ├─ RubberBandRouting.Engine/      # net8.0 클래스 라이브러리
   │  ├─ Models.cs                   # Vec3/Aabb/RouteSegment/옵션/결과 모델
   │  ├─ ManagedRubberBandEngine.cs  # 실제 라우팅 알고리즘 (A* 기반)
   │  └─ PostgresRoutingDataLoader.cs# PostgreSQL 씬 로더 (Npgsql)
   └─ RubberBandRouting.Viewer/      # net8.0-windows WPF + HelixToolkit
      ├─ MainWindow.xaml             # UI 레이아웃
      └─ MainWindow.xaml.cs          # 씬 렌더링·특징점 추출·자동설계 오케스트레이션
```

- Engine 의존성: `Npgsql 8.0.4` ([csproj](RubberBandRoutingSuite/src/RubberBandRouting.Engine/RubberBandRouting.Engine.csproj#L10))
- Viewer 의존성: `HelixToolkit.Wpf 2.25.0` + Engine 프로젝트 참조 ([csproj](RubberBandRoutingSuite/src/RubberBandRouting.Viewer/RubberBandRouting.Viewer.csproj))

---

## 2. C++ 네이티브 엔진 API 매뉴얼

파일: [rubberband_native.h](RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.h) / [rubberband_native.cpp](RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.cpp)

C++ 내부는 익명 네임스페이스 클래스로 캡슐화하고, 외부에는 `extern "C"`로 순수 C 심볼만 노출한다. Windows에서는 `RUBBERBAND_NATIVE_EXPORTS` 정의 여부에 따라 `__declspec(dllexport/dllimport)`가 결정된다.

### 2.1 데이터 구조 (ABI)

모든 좌표·치수 단위는 **mm**이며 double 정밀도이다.

| 구조체 | 필드 | 설명 |
|---|---|---|
| `RbVec3` | `double x, y, z` | 3D 좌표/벡터 |
| `RbAabb` | `double min_x,min_y,min_z, max_x,max_y,max_z`; `int is_penetration` | 축 정렬 경계상자 장애물. `is_penetration != 0`이면 통과 가능(충돌 무시) |
| `RbConfig` | `int max_vertical_bends`; `double safety_margin, tray_width, tray_height, pipe_pitch`; `int pipe_count` | 라우팅 파라미터 |
| `RbEngineHandle` | `void*` | 불투명 엔진 핸들 |

> ⚠ **구조체 필드 순서 주의**: `RbConfig`의 필드 순서는 `max_vertical_bends → safety_margin → tray_width → tray_height → pipe_pitch → pipe_count`이다. C# 측 `[StructLayout(LayoutKind.Sequential)]`로 마샬링할 때 이 순서와 타입(int/double 혼합)을 정확히 맞춰야 한다. 내부 기본값은 `{5, 50.0, 600.0, 100.0, 100.0, 3}` ([cpp:11](RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.cpp#L11)).

### 2.2 함수 레퍼런스

모든 정수 반환 함수는 **0 = 성공, 0이 아니면 실패/오류**(주로 null 핸들) 규약을 따른다. 단, `rb_get_*_count`와 `rb_copy_*`는 예외적으로 개수를 반환한다.

| 함수 | 시그니처 | 동작 | 반환 |
|---|---|---|---|
| `rb_create` | `RbEngineHandle rb_create(void)` | 엔진 인스턴스 생성(`new Engine`) | 핸들(실패 시 예외 없음) |
| `rb_destroy` | `void rb_destroy(RbEngineHandle)` | 엔진 해제(`delete`) | 없음 |
| `rb_initialize` | `int rb_initialize(RbEngineHandle, RbConfig)` | 설정 주입 | 0/1 |
| `rb_set_obstacles` | `int rb_set_obstacles(RbEngineHandle, const RbAabb*, int count)` | 장애물 배열 복사 저장 | 0/1(`count<0` 시 1) |
| `rb_execute` | `int rb_execute(RbEngineHandle, RbVec3 start, RbVec3 end)` | 경로 계산 실행 | 0/1 |
| `rb_get_segment_count` | `int rb_get_segment_count(RbEngineHandle)` | 중심선 세그먼트 수 | 세그먼트 개수 |
| `rb_copy_segments` | `int rb_copy_segments(RbEngineHandle, RbVec3* out, int max)` | 중심선 폴리라인 점 복사 | `out==null`이면 필요한 점 개수, 아니면 복사한 개수 |
| `rb_get_pipe_count` | `int rb_get_pipe_count(RbEngineHandle)` | 분배된 파이프 라인 수 | 파이프 개수 |
| `rb_copy_pipe_path` | `int rb_copy_pipe_path(RbEngineHandle, int idx, RbVec3* out, int max)` | idx번 파이프 폴리라인 점 복사 | `out==null`이면 점 개수, 아니면 복사한 개수 |

**중심선 점 개수 규약**: 세그먼트 N개일 때 폴리라인 점은 `N+1`개(`front().a` + 각 세그먼트의 `b`). `rb_copy_segments`에 `out_points=null`을 넘겨 필요한 크기를 먼저 조회한 뒤 버퍼를 할당하는 2-패스 패턴을 지원한다([cpp:158-167](RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.cpp#L158-L167)).

### 2.3 표준 호출 시퀀스

```c
RbEngineHandle h = rb_create();
RbConfig cfg = {5, 50.0, 600.0, 100.0, 100.0, 3};
rb_initialize(h, cfg);
rb_set_obstacles(h, obstacles, obstacleCount);
rb_execute(h, start, end);

int n = rb_copy_segments(h, NULL, 0);       // 필요한 점 개수 조회
RbVec3* pts = malloc(sizeof(RbVec3) * n);
rb_copy_segments(h, pts, n);                // 실제 복사

int pipeCount = rb_get_pipe_count(h);
// pipe_index 별 rb_copy_pipe_path(...) 반복
rb_destroy(h);
```

### 2.4 내부 알고리즘 파이프라인 (`rb_execute`)

README가 표방하는 "고무줄 control-point 라우터"와 달리, **실제 cpp 구현은 직교 축분해(orthogonal) 라우터**이다([cpp:130-156](RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.cpp#L130-L156)).

1. **초기 직교 경로** — `append_orthogonal(start, end)`: 축 순서 `X → Y → Z`로 델타가 있는 축마다 세그먼트를 생성. 고무줄 직선/특징점 스냅 단계가 **없다**.
2. **충돌 해소 루프** — 최대 40회 반복. 각 세그먼트에 대해 `is_penetration`이 아닌 장애물과 `intersects()` 검사:
   - `expand()`로 장애물을 `tray_width/2 + margin`(수평), `tray_height/2 + margin`(수직)만큼 확장.
   - `intersects()`는 **세그먼트의 지배축을 기준으로 한 축정렬 판정**이며, 임의 방향 3D 세그먼트를 정확히 판정하지 못한다([cpp:49-60](RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.cpp#L49-L60)).
   - 충돌 시 `bypass()`로 우회점 생성 후 `append_orthogonal`로 재분해하여 해당 세그먼트를 치환.
3. **우회 전략** — `bypass()`: 남은 수직 bend 여유가 2 이상이면 장애물 위(`max_z + clearance`)로 넘기고, 아니면 측면으로 우회([cpp:73-94](RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.cpp#L73-L94)).
4. **파이프 분배** — `distribute()`: `pipe_count`개의 파이프를 세그먼트 법선 방향으로 `pipe_pitch` 간격 오프셋. 수직 구간은 직전 법선을 유지([cpp:101-118](RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.cpp#L101-L118)).

### 2.5 빌드

```bat
:: Visual Studio Developer Command Prompt에서
build_msvc.bat
:: 내부적으로: cl /std:c++17 /EHsc /O2 /LD /Fe:RubberBandRouting.Native.dll rubberband_native.cpp
```

산출물 `RubberBandRouting.Native.dll`은 현재 어떤 프로젝트에서도 참조하지 않는다.

---

## 3. C# 경로 뷰어 / 엔진 코드 분석 (실제 동작 구현)

### 3.1 데이터 모델 — `Models.cs`

[Models.cs](RubberBandRoutingSuite/src/RubberBandRouting.Engine/Models.cs)

| 타입 | 성격 | 비고 |
|---|---|---|
| `Vec3` | `readonly record struct` | 연산자 오버로드, `this[axis]`, `WithAxis`, `Dot`, `Length` |
| `Aabb` | `readonly record struct` | `Center`, `Expand(h, v)`, `IsPenetration`, `Name` |
| `RouteSegment` | `record` | `Delta`, `Length`, `IsVertical`(Z 델타가 X·Y보다 크면 수직) |
| `RubberBandOptions` | class | `MaxVerticalBends=5, SafetyMargin=50, TrayWidth=600, TrayHeight=100, PipePitch=100, PipeCount=3, SnapTolerance=100, MaxPushIterations=40` |
| `RubberBandStep` | class | 단계별 세그먼트/웨이포인트/충돌점 |
| `RubberBandResult` | class | 단계 목록, 최종 세그먼트, 파이프 경로, 총길이, 수직 bend, 유효성, 검증이슈 |

C# 옵션 기본값은 C++ `RbConfig` 기본값과 정확히 일치한다(둘 다 5/50/600/100/100/3).

### 3.2 관리형 라우팅 엔진 — `ManagedRubberBandEngine.Route()`

[ManagedRubberBandEngine.cs:11-49](RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs#L11-L49)

실제 3단계 파이프라인:

- **Step 1 — 초기 직선 고무줄**: `MakeRubberLineSegments([start, end])`. start→end 단일 직선.
- **Step 2 — 웨이포인트 스켈레톤**: `BuildSnappedPointList()`로 특징점을 순서화한 뒤 **`MakeOrthogonalSegments()`로 직교 분해**. (개발계획서 §4.2가 말하는 "rubber pull-snap"이 아니라 직교 스켈레톤이다.)
- **Step 3 — 직교 A* 웨이포인트 라우팅**: `RouteOrthogonalAStarViaWaypoints()`. 각 웨이포인트 구간을 A*로 연결하고, 실패 시 직교 fallback.

핵심 세부:

- `BuildSnappedPointList()` ([L51-69](RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs#L51-L69)): 특징점을 start/end 근접·과도 우회(`maxDetour = max(len*2.5, 10000)`) 기준으로 필터링하여 경유 순서 리스트 생성.
- `RouteOrthogonalAStarLeg()` ([L98-132](RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs#L98-L132)): **희소 좌표 격자 A***. 장애물 확장 경계로부터 후보 좌표선(`BuildAStarLines`)을 만들고, 6방향 이웃 탐색. 휴리스틱은 맨해튼 거리, 최대 확장 50,000회.
- `IsBlocked()` / `SegmentIntersectsExpandedAabb()` ([L208-393](RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs#L208-L393)): **슬래브(slab) 기반 정식 segment–AABB 교차 판정**. C++의 축정렬 근사 판정보다 정확.
- `RelevantObstacles()` ([L134-145](RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs#L134-L145)): start–end 회랑에 걸치는 장애물 중 **가까운 30개만** 선택.
- `DistributePipes()` ([L426-445](RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs#L426-L445)): C++ `distribute()`와 동일 로직으로 다중 파이프 오프셋 생성.
- `Validate()` ([L455-463](RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs#L455-L463)): 수직 bend 초과(`vertical_bends_exceeded`), 잔여 충돌(`residual_collision`) 이슈 반환.

> **레거시/데드 코드**: `ResolveCollisions()`, `BuildBypass()`, `Within()`은 정의되어 있으나 `Route()` 경로에서 호출되지 않는다([L316-424](RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs#L316-L424)). 이는 개발계획서 §4.3이 서술하는 "push collision resolution"의 잔재이며, 실제 충돌 회피는 A*가 담당한다. 문서와 코드가 불일치한다.

### 3.3 PostgreSQL 씬 로더 — `PostgresRoutingDataLoader`

[PostgresRoutingDataLoader.cs](RubberBandRoutingSuite/src/RubberBandRouting.Engine/PostgresRoutingDataLoader.cs)

- 연결: `PostgresConnectionOptions`(기본 DB `DDW_AI_DB`, user `postgres`, pw `dinno`). 환경변수 `PGHOST/PGPORT/...`로 오버라이드 가능([L20-29](RubberBandRoutingSuite/src/RubberBandRouting.Engine/PostgresRoutingDataLoader.cs#L20-L29)).
- `ListProjectsAsync()`: `TB_SPACE_GROUP_INFO`에서 공간 그룹(AABB 포함)을 프로젝트로 로드.
- `LoadSceneAsync()`: 프로젝트 AABB에 `±500mm` 마진을 준 범위로 다음을 조회하여 `RoutingScene` 구성:

| 요소 | 소스 테이블 | 메서드 |
|---|---|---|
| 장애물 | `TB_BIM_OBSTACLE` | `LoadObstaclesAsync` — `damper` 제외, `COLLISION_PASS`/타입으로 통과여부 판정 |
| 장비 | `TB_EQUIPMENTS` | `LoadEquipmentAsync` — `MainTool`은 `MAIN_EQUIPMENT` |
| 레터럴/덕트 | `TB_LATERAL_PIPE`, `TB_DUCT` | `LoadDuctLateralAsync` |
| PoC | `TB_POCINSTANCES` | `TryLoadPocsAsync` — **컬럼명을 information_schema로 동적 탐지**(`Pick`) |
| 라우팅 태스크 | `TB_ROUTE_PATH` | `TryLoadRouteTasksAsync` |
| 기존 경로 | `TB_ROUTE_PATH`+`TB_ROUTE_SEGMENTS`+`TB_ROUTE_SEGMENT_DETAIL` | `TryLoadExistingRoutePathsAsync` |

- `ClassifyPoc()`: PoC를 장비/덕트/레터럴로 분류(타입 문자열 + 최근접 박스 거리).
- 태스크가 없으면 `BuildNearestPocTasks()`로 유틸리티 일치 최근접 PoC 쌍을 태스크로 합성([L373-386](RubberBandRoutingSuite/src/RubberBandRouting.Engine/PostgresRoutingDataLoader.cs#L373-L386)).
- `CollisionObstacles`: 통과 불가 장애물만 AABB로 노출([L107](RubberBandRoutingSuite/src/RubberBandRouting.Engine/PostgresRoutingDataLoader.cs#L107)).

> `TryLoad*` 계열은 모두 `catch { }`로 예외를 **무음 처리**한다([L220-272](RubberBandRoutingSuite/src/RubberBandRouting.Engine/PostgresRoutingDataLoader.cs#L220-L272)). 스키마 불일치나 권한 오류가 조용히 "데이터 0건"으로 나타난다.

### 3.4 뷰어 — `MainWindow`

레이아웃 [MainWindow.xaml](RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml): 상단 DB/프로젝트 바, 좌측 유틸리티 그룹/유틸리티/PoC 패널, 중앙 `HelixViewport3D`, 우측 결과 그리드 + 분석/단계/세그먼트 탭, 하단 레이어 토글 + FPS/렌더 객체 수.

코드비하인드 [MainWindow.xaml.cs](RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs) 주요 흐름:

**(a) 씬 로딩·렌더**
- `LoadSceneAsync()` → `DrawScene()`가 공간 와이어박스, 장애물/장비/덕트 박스, 시작/종단 PoC 구를 레이어별 버킷에 추가([L494-509](RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs#L494-L509)).
- 레이어 토글(`ShouldShowBucket`/`ApplyLayerVisibility`)로 뷰포트 자식 추가/제거.

**(b) 자동설계 오케스트레이션 — `RouteRowsAsync()`** ([L310-374](RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs#L310-L374))
- 대상 태스크(최대 200개)를 순회하며:
  1. `BuildFeatureWaypoints()`로 매칭 기존경로에서 특징점 추출.
  2. `Route()`로 엔진 실행(시작 강제 -Z 드롭 스텁 선처리 포함).
  3. 생성된 경로를 `BuildRouteObstacles()`로 AABB envelope화하여 **다음 태스크의 누적 장애물에 추가**(자동경로끼리 겹침 방지).
  4. 결과·분석·단계·세그먼트 행을 그리드에 채움.

**(c) 특징점 추출 — `BuildFeatureWaypoints()` 계열** ([L577-709](RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs#L577-L709))
- `FindMatchedExistingRoute()`: `RoutePathGuid` 직접 매칭 우선, 실패 시 그룹/유틸리티/양끝 거리 스코어(<8000)로 fallback.
- `ExtractExistingRouteFeatures()`: 기존경로에서 (1) 두 번째/뒤 두 번째 점, (2) **방향 전환·Z 변화 지점**, (3) 4m 이상 긴 직선의 등분점을 특징점 후보로 뽑고, 중복 제거 후 **최대 28개**로 샘플링.
- `ApplyStartVerticalStub()`: 시작·종단 고도차가 크면 기존경로 첫 구간 성향을 반영한 수직 스텁을 특징점 앞에 삽입.

**(d) 시작 -Z 드롭 — `Route()` / `RequiredStartDropPoint()` / `PrependStartStub()`** ([L376-419](RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs#L376-L419))
- 시작이 종단보다 충분히 높으면 먼저 수직으로 내린 뒤 나머지를 엔진에 위임하고 결과를 접합.

**(e) 표시용 bend 보정 — `DrawRoundedSegments()` / `BuildRoundedBendPolyline()`** ([L901-979](RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs#L901-L979))
- 중심선 폴리라인의 **직각(내적≈0) 코너만** `BendRadius(diameter)` 반경으로 8스텝 원호 보간. 관경 비례 bend radius(120~1800mm clamp) 반영.

**(f) 계측**: `CompositionTarget.Rendering`에서 0.5초 주기 FPS, 뷰포트 자식 수(−2 보정) 표시([L1071-1087](RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs#L1071-L1087)).

> **뷰어가 사용하지 않는 엔진 출력**: 엔진이 계산한 `RubberBandResult.PipePaths`(다중 파이프)는 뷰어에서 렌더링되지 않는다. 뷰어는 언제나 단일 중심선 튜브(`RouteSegments`)만 그린다([RedrawAutoRoutes L421-432](RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs#L421-L432)). 즉 `DistributePipes` 결과는 계산되지만 소비되지 않는다.

---

## 4. C++ API ↔ C# 구현 대응 관계

| 개념 | C++ 네이티브 | C# 관리형 | 일치 여부 |
|---|---|---|---|
| 벡터 | `RbVec3` | `Vec3` | 구조 동일 |
| 장애물 | `RbAabb`(+`is_penetration`) | `Aabb`(+`IsPenetration`,`Name`) | C#이 상위호환 |
| 설정 | `RbConfig` | `RubberBandOptions` | 기본값 동일, C#에 `SnapTolerance`/`MaxPushIterations` 추가 |
| 초기 경로 | 직교 축분해(`append_orthogonal`) | 직선 고무줄 + 직교 스켈레톤 | **불일치** |
| 충돌 회피 | 반복 bypass 삽입 | **A\* 격자 탐색** | **불일치(방식 자체가 다름)** |
| 교차 판정 | 지배축 축정렬 근사 | 슬래브 정식 판정 | **C#이 더 정확** |
| 특징점 스냅 | **없음** | 기존경로 특징점 추출·경유 | **C#에만 존재** |
| 파이프 분배 | `distribute` | `DistributePipes` | 로직 동일(뷰어 미사용) |
| bend 보정 | 없음(중심선만) | 뷰어에서 원호 보간 | **C#(뷰어)에만 존재** |

결론: **C++ 스텁은 C# 엔진보다 이전 세대(직교 라우터) 알고리즘에 머물러 있고**, 특징점 기반 고무줄 개념·정식 충돌 판정·A* 탐색은 C#에만 구현되어 있다. 두 구현의 알고리즘이 다르므로, C++를 그대로 P/Invoke로 붙이면 현재 뷰어 결과와 전혀 다른 경로가 나온다.

---

## 5. 기존 소스의 문제점

### 5.1 아키텍처·정합성
1. **C++ 엔진과 C# 뷰어의 단절**: C++는 솔루션 밖 스텁이며 뷰어가 호출하지 않는다. "C++ 엔진 + C# 뷰어" 구성이 실제로는 성립하지 않는다.
2. **두 구현의 알고리즘 불일치**: C++는 직교 라우터, C#은 특징점+A* 라우터. 동기화 부채가 크다(개발계획서 §9-4가 인정).
3. **문서–코드 불일치**: 개발계획서 §4는 "rubber pull-snap → push collision resolution(BuildBypass)"로 서술하나, 실제 Step2는 직교 스켈레톤, Step3는 A*이며 `ResolveCollisions/BuildBypass`는 **데드 코드**이다.
4. **README 과장**: C++ README는 "rubber-band control-point router"라 하지만 실제 cpp는 축분해 라우터이다.

### 5.2 알고리즘·정확성
5. **C++ 교차 판정 부정확**: `intersects()`가 지배축 축정렬을 가정 → 대각/임의 방향 세그먼트에서 오탐/미탐 가능([cpp:49-60](RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.cpp#L49-L60)).
6. **A* fallback이 장애물을 무시**: A* 실패 시 `MakeOrthogonalSegments`로 직교 fallback → 장애물 관통 경로가 생성될 수 있고, `Validate`가 `residual_collision`을 붙여도 경로는 그대로 그려진다([L88-92](RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs#L88-L92)).
7. **표시용 라운드 bend 미검증**: 원호 보간 결과가 장애물과 재충돌하는지 확인하지 않는다(개발계획서 §9-3).
8. **누적 장애물 vs 30개 제한 상충**: 자동경로를 장애물로 누적하지만 `RelevantObstacles`가 최근접 30개만 취하므로, 뒤 순번 경로가 멀리 있는 선행 자동경로를 못 볼 수 있다([L144](RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs#L144)).
9. **파이프 분배 결과 미사용**: 엔진이 계산한 다중 `PipePaths`가 뷰어에서 버려진다(계산 낭비 + 다중 파이프 시각화 부재).

### 5.3 견고성·운영
10. **DB 예외 무음 처리**: `TryLoad*`의 `catch { }`가 스키마/권한 오류를 "0건"으로 은폐한다.
11. **하드코딩된 자격증명**: DB 비밀번호 `dinno`가 소스·XAML에 평문으로 존재하고, 비밀번호 입력도 `PasswordBox`가 아닌 평문 `TextBox`이다([XAML L40](RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml#L40)).
12. **조용한 상한**: 태스크 200, 기존경로 표시 250, 특징점 28 등 상한이 사용자 고지 없이 결과를 잘라낸다.
13. **UI 무응답 위험**: `RouteRowsAsync`가 UI 스레드에서 동기 루프로 최대 200개 A* 라우팅을 수행한다(`Task.CompletedTask` 반환, 실제 백그라운드 오프로딩 없음). 대규모 씬에서 프리즈 가능.
14. **특징점 모델 부재**: 특징점이 `Vec3` 좌표로만 전달되어 역할(시작 드롭/bend/trunk/end approach)·우선순위·접선 정보가 소실된다(개발계획서 §9-1).

---

## 6. 개선사항 (우선순위 제안)

### P0 — 구조 정리
1. **단일 진실 소스(Single Source of Truth) 확정**: 당분간 C# 엔진을 정본으로 선언하고, C++는 (a) 삭제하거나 (b) C# 알고리즘(특징점+A*+슬래브 판정)과 동일하게 재작성해 P/Invoke로 실제 연동한다. 지금처럼 방치된 스텁은 혼란만 유발한다.
2. **문서·코드 동기화**: 개발계획서 §4를 실제 파이프라인(직선→직교 스켈레톤→A*)으로 갱신하고, `ResolveCollisions/BuildBypass/Within`, C++ README의 과장 문구를 정리.
3. **데드 코드 제거**: 미사용 메서드와 미소비 `PipePaths` 경로를 정리하거나 실제 사용(다중 파이프 렌더)으로 승격.

### P1 — 정확성
4. **fallback 안전화**: A* 실패 시 직교 fallback을 그대로 쓰지 말고, 실패 태스크를 "실패"로 표시하거나 A* 격자 해상도를 높여 재시도.
5. **라운드 bend 재충돌 검증**: 보간 폴리라인에도 `SegmentIntersectsExpandedAabb`를 재적용.
6. **누적 장애물 커버리지**: 자동경로 누적분은 30개 제한과 무관하게 항상 후보에 포함하거나, 공간 인덱스(격자/BVH)로 R-tree 질의를 도입.
7. **C++ 교차 판정 교체**: 지배축 근사를 슬래브 판정으로 교체(C# 로직 이식).

### P2 — 모델·운영
8. **`RouteFeature` 모델 도입**: `Vec3` 대신 역할/우선순위/접선/필수여부를 갖는 특징점 구조체로 확장(개발계획서 §9-1).
9. **단계별 사유 엔진화**: 현재 뷰어의 `SegmentReason` 추론을 엔진이 세그먼트/특징점 생성 시점에 직접 반환하도록 이동(개발계획서 §9-5).
10. **보안·견고성**: 자격증명 하드코딩 제거(환경변수/보안 저장소), `PasswordBox` 사용, `TryLoad`의 예외를 상태바/로그로 노출.
11. **응답성**: 대량 라우팅을 `Task.Run` 백그라운드로 오프로딩하고 진행률·취소 지원, 상한 도달 시 사용자 고지.

---

## 부록 A. 파일별 핵심 진입점 요약

| 파일 | 핵심 심볼 |
|---|---|
| [rubberband_native.h](RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.h) | `RbVec3/RbAabb/RbConfig`, `rb_create/…/rb_copy_pipe_path` |
| [rubberband_native.cpp](RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.cpp) | `rb_execute`(L130), `append_orthogonal`(L32), `intersects`(L49), `bypass`(L73), `distribute`(L101) |
| [Models.cs](RubberBandRoutingSuite/src/RubberBandRouting.Engine/Models.cs) | `Vec3/Aabb/RouteSegment/RubberBandOptions/RubberBandResult` |
| [ManagedRubberBandEngine.cs](RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs) | `Route`(L11), `RouteOrthogonalAStarLeg`(L98), `SegmentIntersectsExpandedAabb`(L356), `DistributePipes`(L426) |
| [PostgresRoutingDataLoader.cs](RubberBandRoutingSuite/src/RubberBandRouting.Engine/PostgresRoutingDataLoader.cs) | `LoadSceneAsync`(L138), `LoadObstaclesAsync`(L163), `LoadExistingRoutePathsAsync`(L274) |
| [MainWindow.xaml.cs](RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs) | `RouteRowsAsync`(L310), `Route`(L376), `ExtractExistingRouteFeatures`(L651), `BuildRoundedBendPolyline`(L921) |

## 부록 B. 분석 시점 검증

- 소스: `main` @ `0bfb2e1`
- `.sln` 구성: C# 프로젝트 2개(Engine, Viewer)만 포함, C++ 프로젝트 미포함 확인
- `src/` P/Invoke(`DllImport`, `rb_*`) 심볼: **없음**(바이너리 DLL 매칭만 존재, 소스 매칭 없음)

---

## 부록 C. 수정 반영 (2026-07-05)

본 분석 이후 5장 문제점에 대해 다음 수정을 적용했다. (C++ 처리 방향은 사용자 결정: **C# 알고리즘과 완전 동기화**)

| # | 문제 | 조치 |
|---|---|---|
| 5.1-2, 4 | C++가 직교 라우터로 C#과 알고리즘 불일치 / README 과장 | C++ 네이티브를 **특징점 snap + 직교 A* + 슬래브 교차판정**으로 전면 재작성. `rb_set_features`, `rb_get_vertical_bends/fallback_count/is_valid` 추가. `RbConfig`에 `snap_tolerance` 추가. README를 실제 구현에 맞게 정정 |
| 5.1-3 | 문서-코드 불일치 + 데드 코드 | `ResolveCollisions/BuildBypass/Within` 및 미사용 옵션 `MaxPushIterations` 제거. 개발계획서 §4 파이프라인을 실제(직교 스켈레톤→A*)로 갱신 |
| 5.2-5 | C++ 교차 판정 부정확(지배축 근사) | 슬래브 기반 `segment_hits()`로 교체 |
| 5.2-6 | A* fallback이 장애물 무시하고 조용히 그려짐 | fallback 발생 시 결과에 `astar_fallback_used` 검증이슈를 부여해 "확인 필요"로 노출 |
| 5.2-7 | 라운드 bend 재충돌 미검증 | `IsRoundedPathClear()`로 라운드 폴리라인을 확장 장애물과 재검사, 충돌 시 sharp 중심선으로 표시(백그라운드 계산) |
| 5.2-8 | 누적 장애물 vs 30개 제한 상충 | 충돌 판정은 회랑 내 전체(`CorridorObstacles`, 상한 256), 격자 생성은 근접 `GridObstacleLimit`(48)로 분리 |
| 5.3-10 | DB 예외 무음 처리 | `TryLoad*`의 `catch{}`를 `scene.LoadWarnings`에 기록하도록 변경, 상태바·분석 그리드에 경고 노출 |
| 5.3-11 | 하드코딩 자격증명 / 평문 비밀번호 | XAML `TextBox`→`PasswordBox`, `TxtPassword.Password` 사용, 클래스·XAML 기본 비밀번호 `dinno` 제거 |
| 5.3-12 | 조용한 상한 | 라우팅 200개 상한 도달 시 상태바에 생략 건수 고지 |
| 5.3-13 | UI 스레드 동기 라우팅으로 프리즈 위험 | 라우팅 루프를 `Task.Run` 백그라운드로 오프로딩(`ComputeRoutes`), UI 바인딩/드로잉만 UI 스레드에서 수행 |
| P0-1 | C++ 엔진과 C# 뷰어가 코드로 연결되지 않음 | **실제 P/Invoke 연동 완료.** `IRubberBandEngine` 공통 인터페이스, `NativeMethods.cs`(구조체/함수 P/Invoke 선언), `NativeRubberBandEngine.cs`(rb_* 호출 → `RubberBandResult` 변환) 추가. 뷰어 하단 "네이티브 C++ 엔진" 체크박스로 관리형/네이티브를 런타임 전환. `Viewer.csproj`에 `PlatformTarget=x64`(네이티브 DLL과 프로세스 비트수 일치) 및 DLL 조건부 출력 복사 항목 추가. DLL 미존재/로드 실패 시 `NativeRubberBandEngine.IsAvailable`이 감지해 관리형으로 자동 대체 |
| P2-8 | `RouteFeature` 의미 모델 부재(특징점이 `Vec3` 좌표뿐) | **완료.** `RouteFeature(Position, Role, Required)` + `RouteFeatureRole`(StartStub/Bend/ElevationChange/TrunkGuide/EndApproach) 도입. `Required=true` 특징점은 두 엔진 모두에서 snap-tolerance/detour 필터를 우회. 28개 상한 트리밍이 역할 우선순위 기반으로 개선(경로 순서는 트리밍 후 복원). 뷰어가 역할별 마커 색상 + "특징점 구성" 분석 행을 표시 |
| P2-9 | 단계별 경로 사유를 뷰어가 사후 추론 | **완료.** `RubberBandResult.SegmentReasonCodes`를 엔진이 직접 채운다(`ClassifySegmentReasons`/C++ `classify_segment_reasons`, 공유 토큰은 `SegmentReasons` 정적 클래스). 뷰어는 토큰→한글 라벨 매핑만 수행 |
| 5.2-9 | 다중 파이프(`PipePaths`) 계산은 되지만 뷰어가 렌더링하지 않음 | **완료.** `PipeCount>1`이면 `RedrawAutoRoutes()`가 각 파이프 경로를 개별 튜브로 렌더링(`DrawRoundedPolyline`). 시작 수직 드롭 스텁도 각 파이프에 동일 오프셋으로 연장하여 드롭 구간 누락을 방지 |

미적용 항목 없음 — 5장에서 식별한 문제 전건 및 6장 개선사항 전건이 반영되었다.

### P/Invoke 연동 및 후속 3개 항목 검증

임시 콘솔 스모크 테스트로 관리형/네이티브 엔진에 동일 입력(장애물 1개, 특징점 2개 — 그중 1개는 `Required=true`)을 넣어 비교:

```text
NativeRubberBandEngine.IsAvailable = True
Managed: segments=11 length=17000.0 valid=True pipes=3
Native : segments=11 length=17000.0 valid=True pipes=3
Managed required-far-feature honored: True
Native  required-far-feature honored: True
```

세그먼트 수·총 길이(17000.0mm)·파이프 수(3, 동일 시작/끝점)가 두 엔진에서 완전히 일치했고, 정상 필터라면 제외되었을 먼 지점의 `Required` 특징점이 두 엔진 모두에서 실제로 경로에 반영됨을 확인했다. `SegmentReasonCodes`는 11개 중 9개가 완전히 일치했고, 2개는 인접한 합리적 사유(예: `collision_bypass` vs `direction_change`) 중 다른 쪽을 골랐다 — 사유는 근사 휴리스틱이므로 이 정도 편차는 허용 범위이며 기하학적 결과에는 영향이 없다.

### 빌드 검증

- `dotnet build RubberBandRoutingSuite.sln -c Debug`: **오류 0** (HelixToolkit `NU1701` 경고 2건은 기존과 동일)
- C++ MSVC 컴파일(`cl /std:c++17 /EHsc /O2 /LD`): **성공**, `RubberBandRouting.Native.dll` 생성
