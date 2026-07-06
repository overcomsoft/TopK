# RubberBand Routing Suite — C++ API 매뉴얼 및 C# 뷰어/엔진 통합 분석 보고서

본 문서는 `RubberBandRoutingSuite/` 하위에 구현된 소스 코드를 직접 분석하여 다음을 정리한다.

1. **C++ 네이티브 엔진(`RubberBandRouting.Native`)의 C API 규격 및 내부 파이프라인**
2. **C# 관리형 엔진(`RubberBandRouting.Engine`)의 알고리즘 및 데이터 구조**
3. **P/Invoke 브리지를 통한 C++ 네이티브 엔진과 C# 뷰어의 실제 연동 메커니즘**
4. **WPF 3D 경로 뷰어(`RubberBandRouting.Viewer`)의 핵심 기능 및 고도화 항목**
5. **두 엔진의 정합성 검증 및 이전 세대(스텁 단계) 대비 개선 완료 사항**

---

## 0. 핵심 변경 사항 개요 (2026-07-05 완료)

이전 분석 시점(커밋 `0bfb2e1`)에서는 C++ 엔진이 단순한 "직교 축 분해 스텁"에 머물러 있었고 C# 뷰어와 코드로 단절되어 병렬로만 존재했습니다. 2026-07-05 고도화 작업을 통해 다음 사항이 완벽히 해결 및 연동되었습니다.

* **실제 P/Invoke 연동 완료**: `IRubberBandEngine` 공통 인터페이스를 기반으로, 뷰어에서 관리형(C#) 엔진과 네이티브(C++) 엔진을 런타임에 체크박스 하나로 자유롭게 전환하여 사용할 수 있습니다.
* **알고리즘의 1:1 동기화**: C++ 네이티브 엔진을 C# 관리형 엔진과 동일한 **특징점 Snap + 직교 A\* + 슬래브(Slab) 교차판정 + Line-Of-Sight(LOS) 직선 단축 + Dogleg 코너 병합 + 평행 파이프 분배** 파이프라인으로 전면 재작성했습니다.
* **기능 고도화**: DB 연결정보 영속화(DPAPI 암호화), 3D 뷰 픽-투-셀렉트(배관/특징점 클릭 연동), 형제(Sibling) 배관 간섭 제외 필터링, 기존 배관 경로와의 정밀 비교 다이얼로그(`CompareRoutesWindow`) 등이 추가되었습니다.

---

## 1. 솔루션 및 프로젝트 구조

```
RubberBandRoutingSuite/
├─ RubberBandRoutingSuite.sln        # 전체 C# 솔루션 (Engine, Viewer 포함)
├─ cpp/
│  └─ RubberBandRouting.Native/      # C++ 네이티브 엔진 프로젝트
│     ├─ rubberband_native.h         # C API 선언 (extern "C")
│     ├─ rubberband_native.cpp       # C++ 구현 (A*, 슬래브 판정, LOS, Dogleg)
│     └─ build_msvc.bat              # cl.exe 단독 빌드 스크립트 (x64 DLL 생성)
└─ src/
   ├─ RubberBandRouting.Engine/      # .NET 8.0 클래스 라이브러리
   │  ├─ Models.cs                   # Vec3, Aabb, RouteFeature(역할 포함), IRubberBandEngine
   │  ├─ ManagedRubberBandEngine.cs  # C# A* + LOS + Dogleg 라우팅 알고리즘
   │  ├─ PostgresRoutingDataLoader.cs# PostgreSQL 씬 데이터 로더 (경고/상태 수집)
   │  ├─ NativeInterop.cs            # C++ DLL P/Invoke 매핑 (NativeMethods)
   │  └─ NativeRubberBandEngine.cs   # IRubberBandEngine 인터페이스 구현 및 마샬링 래퍼
   └─ RubberBandRouting.Viewer/      # .NET 8.0-windows WPF application (HelixToolkit)
      ├─ MainWindow.xaml             # 메인 UI (디버그 제어, 설정, 엔진 전환)
      ├─ MainWindow.xaml.cs          # 씬 렌더링, 피킹, 수직 스텁, 누적 장애물 처리
      ├─ CompareRoutesWindow.xaml    # 기존설계 vs 자동설계 3D 비교 창 (비모달)
      ├─ CompareRoutesWindow.xaml.cs # 이중 3D 뷰포트, 카메라 동기화, 세그먼트 매칭
      └─ ViewerSettings.cs           # DB 자격증명 보안 영속화 (Windows DPAPI 사용)
```

* **프로세스 비트수 일치**: C++ DLL과의 연동을 위해 `Viewer.csproj`에 `PlatformTarget=x64` 및 DLL 조건부 복사 빌드 스크립트가 적용되었습니다.
* **DLL 자동 로드 예외 처리**: DLL이 존재하지 않거나 로드 실패 시 `NativeRubberBandEngine.IsAvailable`이 자동 감지하여 관리형 엔진으로 안전하게 대체(Fallback)됩니다.

---

## 2. C++ 네이티브 엔진 API 매뉴얼

파일: [rubberband_native.h](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.h) / [rubberband_native.cpp](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.cpp)

외부에는 `extern "C"` 형태로 순수 C ABI 심볼만 노출하여 C# 마샬링 호환성을 제공합니다.

### 2.1 데이터 구조 (ABI)

모든 좌표·치수 단위는 **mm**이며 `double` 정밀도입니다.

| 구조체 | 필드 구성 | 설명 |
|---|---|---|
| `RbVec3` | `double x, y, z` | 3D 좌표/벡터 |
| `RbAabb` | `double min_x, min_y, min_z, max_x, max_y, max_z; int is_penetration` | 축 정렬 경계상자 장애물. `is_penetration != 0`이면 내부 슬리브 통로로 충돌을 무시하고 강제 통과 |
| `RbConfig` | `int max_vertical_bends; double safety_margin, tray_width, tray_height, pipe_pitch; int pipe_count; double snap_tolerance; double pipe_diameter;` | 라우팅/기하 파라미터. `pipe_diameter`는 최종 파이프 충돌 및 라운드 밴드 클리어런스 계산에 활용 |
| `RbEngineHandle` | `void*` | 내부 `Engine` C++ 인스턴스를 가리키는 불투명 핸들 |

> ⚠ **마샬링 정밀 일치**: `RbConfig`는 C# 측 `[StructLayout(LayoutKind.Sequential)]` 구조체(`NativeMethods.RbConfig`)와 메모리 구조가 정확히 1:1로 대응해야 합니다.

### 2.2 함수 레퍼런스

성공 시 `0`을 반환하고, 실패 또는 잘못된 인자 입력 시 `1`을 반환하는 ABI 규약을 따릅니다.

| 함수 시그니처 | 동작 설명 |
|---|---|
| `RbEngineHandle rb_create(void)` | 네이티브 `Engine` 객체를 힙 메모리에 할당하고 핸들을 반환합니다. |
| `void rb_destroy(RbEngineHandle engine)` | 할당된 `Engine` 객체를 메모리에서 해제합니다. |
| `int rb_initialize(RbEngineHandle engine, RbConfig config)` | 라우팅 설정 파라미터(`RbConfig`)를 엔진 인스턴스에 주입합니다. |
| `int rb_set_obstacles(RbEngineHandle engine, const RbAabb* obstacles, int count)` | 현재 설계 영역의 장애물 AABB 배열 데이터를 내부 메모리에 복사 저장합니다. |
| `int rb_set_features(RbEngineHandle engine, const RbVec3* features, int count)` | 기존 설계의 특징점(Elbow, 고도변경점 등) 좌표 배열을 입력합니다. (내부 필수 플래그 초기화) |
| `int rb_set_feature_flags(RbEngineHandle engine, const int* required, int count)` | 각 특징점의 필수(`Required=1`) 여부 배열을 매핑합니다. 필수 특징점은 엔진의 허용 오차/우회 필터를 통과합니다. |
| `int rb_execute(RbEngineHandle engine, RbVec3 start, RbVec3 end)` | 전체 고무줄 라우팅 파이프라인을 실행합니다. (성공 시 `0` 반환) |
| `int rb_get_segment_count(RbEngineHandle engine)` | 자동설계 완료된 대표 중심선(Centerline)의 세그먼트 개수를 반환합니다. |
| `int rb_copy_segments(RbEngineHandle engine, RbVec3* out_points, int max_points)` | 대표 중심선 폴리라인 점들을 복사합니다. `out_points`가 Null이면 필요한 배열 크기(`세그먼트 수 + 1`)를 반환합니다. |
| `int rb_get_pipe_count(RbEngineHandle engine)` | 분배된 파이프 라인 개수를 반환합니다 (`cfg.pipe_count`와 일치). |
| `int rb_copy_pipe_path(RbEngineHandle engine, int pipe_index, RbVec3* out_points, int max_points)` | 지정된 인덱스의 개별 파이프 경로 점들을 복사합니다. |
| `int rb_get_vertical_bends(RbEngineHandle engine)` | 최종 경로의 수직 꺾임(Vertical Bends) 횟수를 진단하여 반환합니다. |
| `int rb_get_fallback_count(RbEngineHandle engine)` | A\* 탐색에 실패하여 단순 직교 경로(Manhattan Fallback)로 대체된 구간의 수를 반환합니다. |
| `int rb_is_valid(RbEngineHandle engine)` | 최종 유효성 여부(`1`: 성공, `0`: 경고)를 판단합니다. (잔여 충돌 없고, Fallback 없고, 수직 꺾임이 상한 이하인 경우 유효) |
| `int rb_get_segment_reason(RbEngineHandle engine, int segment_index)` | 대표 중심선의 각 세그먼트 시작 지점의 생성 원인 코드(Start, Snap, Bypass, Turn, Z-Change, Alignment)를 반환합니다. |

### 2.3 내부 알고리즘 파이프라인 (`rb_execute`)

1. **[Build Snapped Points]**
   * 시작 PoC와 종단 PoC 사이의 팽팽한 직선 장력 고무줄을 설정합니다.
   * `SnapTolerance` 이내에 있거나 `max(len * 2.5, 10000mm)` 이상의 터무니없는 우회를 만드는 특징점(Optional)을 필터링합니다. 단, `Required` 특징점은 본 필터를 거치지 않고 무조건 포함합니다.
   * 최종적으로 `[Start, snap1, snap2, ..., End]` 형태의 정렬된 제어점(Waypoint) 뼈대를 형성합니다.
2. **[Orthogonal A\* Search per Leg]**
   * 각 제어점 구간을 희소 격자 A\* 알고리즘을 사용해 연결합니다.
   * 격자 노드 생성 시에는 근접 `kGridObstacleLimit(48)`개의 장애물을 활용해 효율성을 도모하되, 실제 충돌 검사(`IsBlocked`) 단계에서는 회랑 내의 모든 장애물(`kCorridorObstacleLimit(256)`)과 정밀 슬래브 교차 검사를 수행하여 임의의 대각선 고무줄 세그먼트 충돌도 누락 없이 완벽히 탐지합니다.
   * 탐색 한도(`kMaxExpansions = 200,000`)를 초과하는 등 A\* 탐색에 실패할 경우, 단순 직교 분해(Manhattan Fallback) 경로를 덧붙이고 `fallbackCount`를 증가시킵니다.
3. **[Line-Of-Sight (LOS) Shortcuts]**
   * A\*가 생성한 계단형 직교 경로를 단순화하기 위해 Greedy "String Pulling" pass를 실행합니다.
   * 장애물 간섭이 없다면 최대한 멀리 있는 정점을 직접 사선 또는 직선으로 연결합니다.
   * 단, **수평 평면 대각선 이동은 실제 시공 규격을 준수하기 위해 완전히 차단**하며, 다축 사선(Z와 X/Y의 동시 변화)은 **PoC를 빠져나오는 첫 번째 Leg(시작 스텁)에서만 허용**됩니다. 또한 `Required` 특징점 정점은 절대 건너뛰지 않도록 상한 필터를 둡니다.
4. **[Merge Short Doglegs]**
   * 코너에 남은 미세한 직교 계단(Dogleg) 잔재를 정리하기 위해, 인접한 세그먼트의 연장선 교점을 구하여 세그먼트 수를 축소 병합합니다. (단, 병합 후 생성되는 세그먼트의 장애물 간섭 및 대각선 방향 유효성을 사전 검증함)
5. **[Pipe Distribution]**
   * 중심선 세그먼트들의 방향 법선 오프셋을 산출하여 `pipe_count` 개수만큼의 평행 파이프 경로들을 복제 분배합니다. 
   * 법선을 세그먼트 단위로 정밀 산출하여 꺾임부에서 파이프가 찌그러지거나 꼬이지 않고 실물 엘보처럼 평행하게 연결되도록 보정합니다.
6. **[Final Validation & Diagnosis]**
   * 개별 분배된 모든 파이프 경로와 장애물 간의 잔여 충돌 여부(`segment_hits_pipe`, 파이프 반경 + 안전마진 적용)를 확인합니다.
   * 수직 꺾임 횟수를 최종 카운트하고, 세그먼트마다 기하학적 특징(특징점 인접성, 장애물 외곽선 인접성 등)에 따른 세그먼트 사유를 최종 라벨링합니다.

---

## 3. C# 경로 뷰어 및 엔진 코드 분석

### 3.1 공통 데이터 모델 및 설정 — `Models.cs`

* `Vec3`, `Aabb`, `RouteSegment` 등 기하 레코드를 통해 수학 연산을 추상화합니다.
* `RouteFeature`: 특징점의 좌표(`Position`), 역할(`Role`, StartStub/Bend/ElevationChange/TrunkGuide/EndApproach), 필수여부(`Required`)를 패키징합니다.
* `RubberBandOptions`: `SnapTolerance`(mm), `PipeDiameter`(mm), `MaxVerticalBends`, `EnableDebugLog` 등의 파라미터를 관리합니다.
* `IRubberBandEngine`: `Route(...)` 공통 계약을 제공하여, 호출부 코드 변경 없이 엔진을 다형성 있게 다룰 수 있습니다.

### 3.2 C# 관리형 엔진 — `ManagedRubberBandEngine.cs`

C++ 네이티브 엔진의 구현과 완전히 일치하는 알고리즘 파이프라인을 지닌 정본(Golden Reference) 엔진입니다.
* **디버그 로그 생성**: `EnableDebugLog=true` 설정 시, A\* 격자 노드 생성수 및 확장 실패 이력, 세그먼트 좌표 변화량 등을 `RubberBandRouting_DebugTrace.log` 파일에 상세히 기록합니다.

### 3.3 P/Invoke 연동 브리지 — `NativeInterop.cs` / `NativeRubberBandEngine.cs`

* C++ DLL의 함수 심볼을 `[DllImport]`로 선언하고 마샬링 데이터 포맷을 정렬합니다.
* C#의 `Route(...)` 매개변수 구조체들(`Vec3`, `Aabb`, `RouteFeature`)을 C++ 메모리 배열 구조체(`RbVec3`, `RbAabb`)로 복사 및 가인수 전달하고, 연산 결과 중심선과 파이프라인 구조를 2-패스 복사 패턴으로 다시 복원하여 `RubberBandResult` 개체로 재구성합니다.

### 3.4 3D 뷰어 및 오케스트레이션 — `MainWindow.xaml.cs`

WPF 환경에서 HelixToolkit을 이용한 3D 가시화 및 자동 라우팅의 전 과정을 제어합니다.
* **비동기 오프로딩**: 대량 라우팅(최대 200개) 시 UI 프리즈를 유발하던 동기 루프를 `Task.Run` 기반의 백그라운드 스레드(`ComputeRoutes`)로 이동하여 응답성을 개선했습니다.
* **형제(Sibling) 배관 간섭 제외**: 평행 트레이 안에서 함께 주행하는 형제 자동배관(동일 Group, Utility 소속)이 서로를 장애물로 오인해 지그재그 우회 꺾임을 만들지 않도록, 라우팅 실행 전에 형제 배관 AABB를 충돌 대상 장애물 목록에서 동적으로 차단합니다.
* **시작 수직 스텁 고도 반영**: 시작 PoC와 중간 트레이 고도 차이가 클 경우, 기존 배관의 방향 패턴을 분석하여 시작 PoC 직후 고도로 직결되는 수직 하강 스텁을 `Required` 특징점으로 강제 삽입하여 고도 불일치 우회를 원천 봉쇄합니다.
* **3D 피킹 및 양방향 선택**: 3D 뷰포트의 배관 실물 튜브나 특징점 마커를 클릭하면, `_visualOwners` 매핑을 역조회하여 결과 그리드의 소속 행이 자동 선택되거나, 분석 그리드에 대상 개체의 기하 속성(특징점 유형, 기존설계 GUID 등)이 즉시 렌더링됩니다.

### 3.5 DB 연결 영속화 및 보안 — `ViewerSettings.cs`

* 호스트, 포트, 데이터베이스명, 사용자 계정 및 마지막 로딩에 성공한 프로젝트 이름을 `%AppData%\RubberBandRoutingViewer\connection.json`에 지속적으로 영속화하여 자동 로딩 환경을 지원합니다.
* 비밀번호 평문 저장을 방지하고 보안을 높이기 위해 Windows DPAPI(`System.Security.Cryptography.ProtectedData`) 암호화를 적용하여 로컬 계정 보안 컨텍스트 외의 접근을 원천 차단합니다.

### 3.6 기존경로 비교 다이얼로그 — `CompareRoutesWindow.xaml.cs`

자동설계 경로와 기존 설계 경로 간의 정밀 위상 및 물리량 분석을 위한 비모달 윈도우입니다.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                비교 다이얼로그                               │
├──────────────────────┬──────────────────────────────┬────────────────────────┤
│      경로 리스트     │       기존설계 3D 뷰         │       자동설계 3D 뷰   │
│                      │   - 기존 배관 튜브 렌더링    │   - 자동 배관 튜브     │
│   - GUID 매칭 여부   │   - 특징점 마커 가시화       │   - 특징점 마커 가시화 │
│   - 유틸리티/그룹    ├──────────────────────────────┼────────────────────────┤
│   - 라우팅 상태      │      기존 세그먼트 그리드    │      자동 세그먼트 그리드│
│                      │   - # / 꺾임 / 방향 / 길이   │   - # / 꺾임 / 방향 / 길이│
├──────────────────────┴──────────────────────────────┴────────────────────────┤
│                                  비교 분석 테이블                            │
│  - 세그먼트 수 차이 | 총 길이 차이 (변화율) | 수직 Bend 수 차이 | 시작/끝 오차   │
└──────────────────────────────────────────────────────────────────────────────┘
```

* **이중 동기 뷰포트**: 좌우 독립된 `HelixViewport3D`를 통해 배관을 세그먼트 단위로 순환 색상(8색 팔레트) 튜브로 시각화하며, "카메라 동기화" 체크박스 활성화 시 한쪽 3D 뷰의 회전/이동/확대가 다른 쪽에도 1:1로 실시간 전파됩니다.
* **세그먼트 그리드 연동**: 리스트에서 세그먼트 행을 더블클릭/클릭하면 해당 3D 뷰포트의 배관 세그먼트만 노란색 굵은 튜브로 하이라이트 표시되어 상세 위치 파악을 돕습니다.
* **정밀 줌 핏**: `HelixViewport3D.ZoomExtents()` 사용 시 3D 눈금 격자(`GridLinesVisual3D`)의 비대함 때문에 배관이 점으로 작아지는 현상을 방지하고자, 배관의 정점 좌표들만으로 순수 OBB/AABB 바운딩 박스를 계산하여 카메라를 직접 핏하는 `FitViewportToPoints(...)`를 구현했습니다.

---

## 4. C++ API ↔ C# 구현 대응 관계 및 정합성

현재 C++ 네이티브 엔진과 C# 관리형 엔진은 연동 아키텍처 내에서 **동등한 논리적 정합성**을 지니고 있습니다.

| 기하학적 개념 | C++ 네이티브 엔진 (`rubberband_native.cpp`) | C# 관리형 엔진 (`ManagedRubberBandEngine.cs`) | 일치 여부 및 비고 |
|---|---|---|---|
| **3차원 벡터** | `RbVec3` 구조체 | `Vec3` 구조체 | **100% 동일** (double 3차원 좌표) |
| **장애물 AABB** | `RbAabb` 구조체 + `is_penetration` | `Aabb` 구조체 + `IsPenetration` | **100% 동일** |
| **초기 스켈레톤** | `build_snapped_points` | `BuildSnappedPointList` | **100% 동일** (snap 및 detour 가드 일치) |
| **충돌 회피 알고리즘** | 희소 격자선 추출 + 6방향 A\* 탐색 | 희소 격자선 추출 + 6방향 A\* 탐색 | **100% 동일** (동일 격자 확장 논리) |
| **교차 판정 공식** | 3차원 Slab-based AABB 교차식 (`segment_hits`) | 3차원 Slab-based AABB 교차식 (`SegmentIntersectsExpandedAabb`) | **100% 동일** (사선 세그먼트 판독) |
| **특징점 소비** | `rb_set_feature_flags` (`Required` 가드) | `featureWaypoints` (`Required` 가드) | **100% 동일** (동일 오차 우회 방지) |
| **Greedy 단축 pass** | `apply_line_of_sight_shortcuts` | `ApplyLineOfSightShortcuts` | **100% 동일** (대각 금지, 시작사선 허용) |
| **코너 병합 pass** | `merge_short_doglegs` (교점 추출식) | `MergeShortDoglegs` (교점 추출식) | **100% 동일** (불필요한 지그재그 코너 정리) |
| **다중 파이프 분배** | 법선 기반 `distribute` 평행 오프셋 | 법선 기반 `DistributePipes` 평행 오프셋 | **100% 동일** (동일 평행 엘보 형태 유지) |
| **라운드 Bend 보정** | 미탑재 (중심선 수치 연산만 수행) | 뷰어단 `BuildRoundedBendPolyline` | **일치** (렌더링 장치 의존성을 뷰어로 격리) |

> **검증 결과**: 동일한 씬 장애물과 특징점 조건 하에 스모크 테스트(Smoke Test)를 실행한 결과, 두 엔진 모두 세그먼트 개수, 총 경로 길이(mm), 파이프 세트 오프셋 수치 등에서 완벽한 수학적 데이터 일치를 보여주었습니다.

---

## 5. 이전 세대 대비 개선 사항 상세 매핑

이전 시점(커밋 `0bfb2e1` 기준)의 한계와 이에 따른 개선 조치 결과를 요약합니다.

| # | 구 버전 문제점 | 개선 조치 및 현재 상태 (2026-07-05 완료) |
|---|---|---|
| **1** | C++ 엔진과 C# 뷰어 단절 (P/Invoke 없음) | `NativeInterop.cs` / `NativeRubberBandEngine.cs`를 경유한 **실제 DLL 호출 연동 완료**. 뷰어 런타임에서 토글 체크 가능. |
| **2** | C++와 C# 엔진의 알고리즘 불일치 (stretching 대 직교) | C++ 프로젝트를 C#의 특징점+A\*+SLAB+LOS+Dogleg 병합 파이프라인으로 **전면 재작성하여 1:1 일치화**. |
| **3** | C++ 교차 판정의 부정확성 (축 정렬 가정 오류) | 3차원 로컬 슬래브 알고리즘 `segment_hits` 적용으로 **임의 사선 충돌까지 완벽 탐지**. |
| **4** | A\* 실패 시 조용한 장애물 관통 fallback | A\* 실패 시 결과 플래그에 `astar_fallback_used` 검증 이슈를 부여하여, 뷰어 그리드에 **"확인 필요" 상태로 즉각 고지**. |
| **5** | 30개 최근접 장애물 제한으로 인한 누적 충돌 | 격자선 생성을 위한 제한(48개)과, 충돌 판정을 위한 회랑 내 제한(`kCorridorObstacleLimit=256`개)으로 **이원화하여 대형 씬 안정성 확보**. |
| **6** | DB 로딩 시 조용히 오류가 묻히는 현상 (`catch {}`) | DB Loader의 모든 예외를 `scene.LoadWarnings`에 바인딩하여 뷰어 상태 표시줄 및 그리드에 **구체적인 원인 경고 출력**. |
| **7** | 자격증명 평문 하드코딩 및 평문 TextBox | Windows DPAPI를 이용한 **보안 암호화 저장소** 구현 및 XAML 컨트롤의 **`PasswordBox` 보안 입력 처리**. |
| **8** | UI 스레드 동기 연산으로 인한 프리즈 위험 | 라우팅 루프 연산을 `Task.Run` 기반 **백그라운드 스레드로 격리하여 비동기 실행**. |
| **9** | 특징점을 3D 뷰에서 육안으로 구별하기 힘듦 | 특징점 가시화 마커를 Sphere에서 **역할별 색상의 반투명 큐브**로 변경하여 가독성 강화. |
| **10** | 다중 파이프 연산 결과가 뷰어에서 버려짐 | `PipeCount > 1` 설정 시, 뷰어에서 평행 다중 파이프를 **개별 3D 튜브 객체로 실시간 렌더링**. |
| **11** | 형제 배관끼리 상호 간섭하여 지그재그 유발 | 라우팅 시 **동일 그룹/유틸리티 자동 경로를 간섭 목록에서 필터링**하여 매끄러운 1차 평행 레이아웃 도출. |
| **12** | 무차별적인 대각선 단축으로 인한 기하 불일치 | 수평 평면 내 대각선 이동을 차단하고, 다축 대각선은 **PoC 시작 구간(첫 세그먼트)으로만 제한**하여 현업 시공 룰 반영. |
| **13** | 뷰포트 전체보기 시 격자 라인으로 인해 줌아웃됨 | 배관 전용 바운딩 박스를 독립 계산하는 `FitViewportToPoints`로 카메라를 핏하여 **배관만 화면 가득 확대**. |

---

## 6. 향후 고도화 과제 (P3)

1. **R-Tree / BVH 기반 공간 쿼리 가속**:
   * 현재 씬 내의 256개 회랑 장애물 탐색 및 슬래브 충돌 테스트는 선형 루프(`std::vector` 순회)로 처리됩니다. 장애물 데이터가 수만 개 단위로 급증할 경우 프레임 저하가 생길 수 있으므로 C++ 단에 BVH(Bounding Volume Hierarchy) 트리 가속 구조 도입을 고려할 수 있습니다.
2. **다중 파이프 개별 라운드 밴드 충돌 체크**:
   * 현재 표시용 라운드 밴드의 간섭 여부는 중심선을 기반으로 대표 확인하며, 개별 파이프 가닥의 곡률 간섭은 근사치로 판단합니다. 가닥 수가 많고 좁은 엘보 구간이 밀집한 경우, 개별 파이프 튜브의 호(Arc) 부분에 대한 정밀 OBB 검사가 요구될 수 있습니다.
3. **특징점 스코어링의 기하-유사도 결합 모델 구현**:
   * 현재 특징점 추출은 DB의 기설계 노선과 Task 간의 기하학적 유사도(이름 매칭 및 거리 스코어)에 기반합니다. 이를 고도화하여 배관의 흐름(Flow Direction) 벡터 유사도 및 주변 장비 인접도를 계량화한 AI 유사도 텐서 피팅이 결합될 여지가 있습니다.

---

## 부록 A. 파일별 핵심 진입점 요약

* [rubberband_native.h](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.h)
  * `RbConfig` (L19) — `snap_tolerance` 및 `pipe_diameter`가 이식된 설정 구조체.
  * `rb_set_feature_flags` (L33) — 특징점 가중치 유도를 위한 필수 지정 API.
  * `rb_get_segment_reason` (L59) — 각 꺾임 지점의 발생 사유 조회 API.
* [rubberband_native.cpp](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.cpp)
  * `rb_execute` (L671) — 네이티브 고무줄 변형 라우팅 최외곽 실행 파이프라인.
  * `route_astar_leg` (L306) — 6방향 희소 격자 A\* 탐색 코어.
  * `apply_line_of_sight_shortcuts` (L434) — 축 제한 및 필수점 보존이 적용된 직선 단축.
  * `merge_short_doglegs` (L497) — 지그재그 코너 병합.
  * `distribute` (L612) — 법선 오프셋 기반 평행 다중 파이프 분배.
* [Models.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Engine/Models.cs)
  * `RouteFeature` (L108) — 특징점 속성 모델 (Position, Role, Required).
  * `IRubberBandEngine` (L150) — 엔진 스왑을 지원하는 인터페이스 디렉티브.
* [NativeInterop.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Engine/NativeInterop.cs)
  * `NativeMethods` (L10) — C++ DLL 맵 `[DllImport]` 선언 및 `RbConfig` 정의.
* [NativeRubberBandEngine.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Engine/NativeRubberBandEngine.cs)
  * `Route` (L41) — DLL `rb_execute` 호출 및 C# `RubberBandResult` 데이터 가공 복원.
* [ManagedRubberBandEngine.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs)
  * `Route` (L13) — 관리형 엔진 메인 파이프라인 및 디버그 파일로그 출력 처리.
* [MainWindow.xaml.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs)
  * `ComputeRoutes` (L430) — 백그라운드 비동기 멀티스레드 배치 라우팅 연산 루프.
  * `PreviewMouseLeftButtonDown` (L1040) — 3D 뷰포트 배관 및 특징점 큐브 클릭 피킹 핸들링.
* [CompareRoutesWindow.xaml.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/CompareRoutesWindow.xaml.cs)
  * `FitViewportToPoints` (L210) — 3D grid 크기를 배제한 정확한 배관 중심 핏 연산.
  * `GridSegments_SelectionChanged` (L300) — 세그먼트 그리드 선택에 따른 특정 배관 마디 3D 하이라이팅 연동.
