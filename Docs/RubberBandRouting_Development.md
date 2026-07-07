# 3차원 고무줄 배관 라우팅 엔진 개발 보고서

최종 업데이트: 2026-07-06

본 문서는 `RubberBandRoutingSuite`의 C# 고무줄 라우팅 엔진과 WPF 3D Viewer 개발 현황을 기록한다. 현재 구현은 기존 Python PoC와 별도로 신규 작성된 C# 엔진/뷰어 기반이며, PostgreSQL의 기존 배관 경로와 PoC 데이터를 읽어 자동 배관 중심선을 생성하고 HelixToolkit 3D 뷰어에 표시한다.

> 2026-07-05 업데이트: 기존의 "시작점-종단점 직교 축 분해" 방식은 고무줄 밴딩 원리와 맞지 않아 제거하였다. 현재 엔진의 1차 경로는 시작PoC와 종단PoC를 잇는 직선 고무줄이며, 기존설계 특징점은 이 고무줄을 당기는 control point로 사용한다.

---

## 1. 개발 목적

1. PostgreSQL 설계 DB에서 장비 PoC, 덕트/레터럴 PoC, 장애물, 기존 배관 경로를 자동 조회한다.
2. 기존 배관 설계의 특징점을 활용하여 신규 자동경로가 기존 설계와 유사한 흐름을 갖도록 한다.
3. 자동설계된 경로는 다음 경로 탐색 시 장애물로 누적하여 배관끼리 겹치지 않도록 한다.
4. WPF HelixToolkit 3D Viewer에서 장애물, 공간 CubeBox, 기존경로, 자동경로, PoC, 특징점, FPS/렌더 객체 수를 확인한다.
5. 자동설계 결과 리스트, 분석결과, 단계별 경로, 세그먼트 상세를 데이터그리드로 제공한다.

---

## 2. 현재 구성

| 영역              | 주요 파일                                                                                               | 역할                                                                                                                                                            |
| ----------------- | ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| C# Engine         | `RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs`                      | 고무줄 control point 기반 경로 생성, 충돌 회피, 검증                                                                                                            |
| Engine Models     | `RubberBandRoutingSuite/src/RubberBandRouting.Engine/Models.cs`                                       | `Vec3`, `Aabb`, `RouteSegment`, `RubberBandResult` 등 데이터 구조                                                                                       |
| PostgreSQL Loader | `RubberBandRoutingSuite/src/RubberBandRouting.Engine/PostgresRoutingDataLoader.cs`                    | 프로젝트, 장애물, PoC, 기존경로, 경로 태스크 조회                                                                                                               |
| WPF Viewer        | `RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml(.cs)`                            | DB 연결(저장/자동 로딩 포함), 프로젝트 로딩, 3D 렌더링, 3D 클릭 피킹, 자동경로 실행/결과 표시                                                                   |
| 비교 다이얼로그   | `RubberBandRoutingSuite/src/RubberBandRouting.Viewer/CompareRoutesWindow.xaml(.cs)`                   | 기존경로 vs 자동경로를 좌우 2개 3D 뷰 + 단계별 세그먼트 그리드로 비교                                                                                           |
| 뷰어 설정 저장    | `RubberBandRoutingSuite/src/RubberBandRouting.Viewer/ViewerSettings.cs`                               | DB 연결정보(호스트/포트/계정/DB명/마지막 프로젝트)를`%AppData%`에 저장, 비밀번호는 Windows DPAPI로 암호화                                                     |
| C++ Native Engine | `RubberBandRoutingSuite/cpp/RubberBandRouting.Native`                                                 | C# 엔진과 동일한 특징점+A*+슬래브+dogleg 병합 알고리즘의 네이티브 구현, C API(`rb_*`) 노출                                                                    |
| P/Invoke 브리지   | `RubberBandRoutingSuite/src/RubberBandRouting.Engine/NativeInterop.cs`, `NativeRubberBandEngine.cs` | 네이티브 DLL 구조체/함수 선언(`NativeMethods`)과 `IRubberBandEngine` 구현체(`NativeRubberBandEngine`) — 뷰어가 관리형/네이티브 엔진을 런타임에 전환 가능 |

---

## 3. 고무줄 밴딩 알고리즘 원리

고무줄 밴딩 모델은 "처음부터 X/Y/Z 순서로 꺾는 알고리즘"이 아니다. 원리는 다음과 같다.

1. 시작PoC와 종단PoC 사이에 하나의 직선 고무줄을 건다.
2. 기존설계에서 추출한 특징점 중 현재 라우팅 조건에 맞는 점을 선택한다.
3. 선택된 특징점이 고무줄을 당기며, 경로의 중간 control point가 된다.
4. 장애물과 충돌하는 구간이 있으면 해당 위치에 우회 control point를 추가한다.
5. 최종 표시 단계에서 배관 관경과 bend radius를 반영하여 둥근 bend 형상으로 보정한다.

따라서 축 이동 순서(`X -> Y -> Z`, `Z -> X -> Y` 등)는 고무줄 알고리즘의 본질이 아니다. 축 순서 기반 생성은 단순 직교 라우터의 성격이며, 기존설계 특징점 기반 고무줄 밴딩과는 구분해야 한다.

---

## 4. 현재 구현된 라우팅 단계

### 4.1 Step 1 - Initial straight rubber tension

- 입력: 시작점 `S`, 종단점 `D`
- 처리: `S -> D`를 하나의 직선 rubber segment로 생성
- 구현: `ManagedRubberBandEngine.MakeRubberLineSegments(new[] { start, end })`
- 목적: 장애물이나 특징점을 적용하기 전의 가장 자연스러운 장력 기준선 생성

기존 구현의 문제였던 `MakeOrthogonalSegments(new[] { start, end })` 방식은 제거하였다. 이 변경으로 시작점이 종단점보다 높은 경우에도 엔진이 임의로 좌우 축 이동을 먼저 선택하지 않는다.

### 4.2 Step 2 - Pull snap by existing-design control points (waypoint skeleton)

- 입력: 기존 설계에서 추출된 특징점 목록
- 처리: `BuildSnappedPointList()`로 `S -> feature1 -> ... -> D` 순서의 경유점 리스트를 만들고, `MakeOrthogonalSegments()`로 직교 스켈레톤을 구성
- 구현: `BuildSnappedPointList()` + `MakeOrthogonalSegments()`
- 표시: 뷰어 하단 `특징점` 토글로 표시/숨김 가능

특징점은 단순한 경유 좌표가 아니라, 기존 배관의 흐름을 재현하기 위해 고무줄을 당기는 control point로 취급한다.

### 4.3 Step 3 - Orthogonal A* obstacle avoidance

- 입력: Step 2의 경유점 리스트와 장애물 AABB 목록
- 처리: 각 경유점 구간(leg)을 확장 장애물 경계에서 만든 희소 좌표 격자 위에서 직교 A*로 연결하여 충돌을 회피
- 충돌 판정: `SegmentIntersectsExpandedAabb()`의 슬래브(slab) 기반 segment-AABB 교차 판정(임의 방향 세그먼트 지원)
- 구현: `RouteOrthogonalAStarViaWaypoints()` → `RouteOrthogonalAStarLeg()`
- Fallback: A*가 경로를 찾지 못한 leg는 직교 fallback으로 대체되며, 이 경우 결과에 `astar_fallback_used` 검증이슈가 부여되어 "확인 필요"로 표시된다

> 초기 문서에 있던 `ResolveCollisions()`/`BuildBypass()` 기반 "push collision resolution"은 실제 코드에서 A* 방식으로 대체되었고 해당 데드 메서드는 제거되었다.

장애물 사용은 두 갈래로 분리된다. 충돌 판정(`IsBlocked`)에는 회랑에 걸치는 모든 장애물(`CorridorObstacles`, 상한 256)을 사용해 어떤 장애물도 조용히 관통되지 않게 하고, A* 격자선 생성(`BuildAStarLines`)에는 근접 상위 `GridObstacleLimit`(48)개만 사용해 격자 크기를 제한한다.

### 4.4 Step 4 - Final display bend correction

- 엔진 결과는 중심선 control polyline이다.
- 뷰어 표시 단계에서 배관 관경을 읽고 bend radius를 계산하여 꺾임부를 둥글게 보정한다.
- 목적은 기존설계 배관처럼 관경에 따른 bending radius가 반영된 시각 결과를 제공하는 것이다.

---

## 5. 기존설계 특징점 활용 방식

### 5.1 특징점(기존경로 폴리라인)을 저장하는 실제 테이블

기존설계 배관의 "지나가는 점들"은 별도의 특징점 전용 테이블이 있는 게 아니라, 배관 라우팅 결과를 세그먼트 단위로 저장하는 3개 테이블을 조인해서 폴리라인으로 복원한다(`PostgresRoutingDataLoader.LoadExistingRoutePathsAsync`, [PostgresRoutingDataLoader.cs:275](RubberBandRoutingSuite/src/RubberBandRouting.Engine/PostgresRoutingDataLoader.cs#L275)).

| 테이블                           | 역할                                                                                           | 이 코드에서 실제로 읽는 컬럼                                                                                                                                                         |
| -------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `TB_ROUTE_PATH` (rp)           | 배관 한 줄(시작 PoC ↔ 종단 PoC) 단위 메타데이터.`ROUTE_PATH_GUID`가 PK 역할                 | `ROUTE_PATH_GUID`, `UTILITY_GROUP`, `SOURCE_UTILITY`, `SOURCE_SIZE`, `EQUIPMENT_NAME`(시작측 이름), `TARGET_OWNER_NAME`(종단측 이름), `SOURCE_POSX/Y`(스코프 필터링용) |
| `TB_ROUTE_SEGMENTS` (s)        | 한`ROUTE_PATH_GUID` 안에서 여러 구간(세그먼트)의 순서. `SEGMENT_GUID`로 상세 테이블과 연결 | `SEGMENT_GUID`, `ROUTE_PATH_GUID`, `ORDER`(세그먼트 순서)                                                                                                                      |
| `TB_ROUTE_SEGMENT_DETAIL` (sd) | 세그먼트 하나의 실제 시작/끝 좌표(꺾이는 지점 포함, 가장 미세한 단위)                          | `SEGMENT_GUID`, `ORDER`(세그먼트 내 순서), `FROM_POSX/Y/Z`, `TO_POSX/Y/Z`                                                                                                    |

실제 조회 SQL(요지):

```sql
SELECT s."ROUTE_PATH_GUID", rp."UTILITY_GROUP", rp."SOURCE_UTILITY",
       rp."SOURCE_SIZE", rp."EQUIPMENT_NAME", rp."TARGET_OWNER_NAME",
       sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",
       sd."TO_POSX",   sd."TO_POSY",   sd."TO_POSZ"
FROM "TB_ROUTE_SEGMENT_DETAIL" sd
JOIN "TB_ROUTE_SEGMENTS" s ON s."SEGMENT_GUID" = sd."SEGMENT_GUID"
JOIN "TB_ROUTE_PATH" rp    ON rp."ROUTE_PATH_GUID" = s."ROUTE_PATH_GUID"
WHERE rp."SOURCE_POSX" BETWEEN @minx AND @maxx
  AND rp."SOURCE_POSY" BETWEEN @miny AND @maxy
ORDER BY s."ROUTE_PATH_GUID", s."ORDER", sd."ORDER"
```

`ROUTE_PATH_GUID`, `ORDER`, `ORDER` 순으로 정렬해서 한 줄씩 읽으며, GUID가 바뀔 때마다 지금까지 모은 점들을 하나의 `ExistingRoutePath`(`RoutePathGuid, Utility, Group, SourceName, TargetName, DiameterMm, Points`)로 확정(flush)한다. 각 행의 `FROM_POS*`/`TO_POS*`를 순서대로 폴리라인에 추가하되, 직전 점과 1mm 이내로 겹치면 중복 추가하지 않는다(`AddPoint`). 즉 `ExistingRoutePath.Points`는 **그 배관이 실제로 지나간 모든 꺾임점을 원래 시공 순서 그대로 담은 폴리라인**이다.

### 5.2 자동설계 태스크와 기존경로 매칭

자동설계가 라우팅할 `RouteTask`(시작 PoC ↔ 종단 PoC) 하나마다, 어떤 `ExistingRoutePath`가 "이 배관의 기존설계"인지 먼저 찾는다(`MainWindow.FindMatchedExistingRoute`, [MainWindow.xaml.cs:761](RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs#L761)).

1. **GUID 직접매칭**: `RouteTask.RoutePathGuid`(이 값 자체가 `TB_ROUTE_PATH.ROUTE_PATH_GUID`에서 온 것 — `LoadRouteTasksAsync`가 `TB_ROUTE_PATH`를 그대로 읽어 태스크를 만들기 때문)와 동일한 `RoutePathGuid`를 가진 `ExistingRoutePath`가 있으면 그걸 그대로 사용한다.
2. **조건 fallback**: 직접매칭이 없으면 같은 그룹(`Group`)·유틸리티(`Utility`)인 기존경로들만 추려서, `ExistingRouteMatchScore`로 점수를 매겨 가장 낮은(=가장 가까운) 것을 고른다. 점수는 "태스크 시작·끝 ↔ 기존경로 양 끝점" 거리 합(정방향/역방향 중 짧은 쪽)이 기본값이고, `SourceName`/`TargetName`이 문자열로 일치하면 각각 500을 깎아준다(이름이 같으면 좌표가 조금 떨어져 있어도 우선). 점수가 8000을 넘으면 매칭 실패로 처리한다.

결과 그리드의 "기존경로 GUID"/"기존경로 매칭" 컬럼과 §12의 비교 다이얼로그가 표시하는 `GUID 직접매칭`/`조건 fallback`/`매칭 없음`이 바로 이 두 갈래를 그대로 보여주는 것이다.

### 5.3 폴리라인 → RouteFeature(제어점) 변환

매칭된 `ExistingRoutePath.Points`(원본 시공 순서 폴리라인)를 자동설계 엔진이 바로 쓸 수는 없다 — 좌표 나열만으로는 "이 점이 왜 중요한지"를 모른다. 그래서 `BuildFeatureWaypoints`([MainWindow.xaml.cs:747](RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs#L747))가 다음 순서로 `List<RouteFeature>`(위치 + 역할 + 필수여부)를 만든다.

1. **방향 정렬** (`OrientedExistingRoutePoints`): 기존경로가 태스크와 반대 방향으로 저장돼 있을 수 있으므로, "태스크 시작↔경로 첫점 + 태스크 끝↔경로 끝점" 거리 합과 그 반대 조합을 비교해 더 짧은 쪽이 되도록 필요시 `Points`를 뒤집는다.
2. **특징점 분류** (`ExtractExistingRouteFeatures`): 정렬된 폴리라인을 순회하며 역할(`RouteFeatureRole`)을 붙인다.
   - 경로의 두 번째 점 → `StartStub`(시작 직후 방향), 뒤에서 두 번째 점 → `EndApproach`(종단 접근 방향).
   - 중간 점들은 앞뒤 구간의 지배축(`DominantAxis`)이 바뀌면 `Bend`, Z값이 유의미하게 바뀌면(`Bend`보다 우선) `ElevationChange`로 분류.
   - 4000mm를 넘는 긴 직선 구간은 최대 3등분점을 `TrunkGuide`로 추가해, 그 구간에서도 기존 배관과 비슷한 경로를 따라가도록 유도.
   - 태스크 시작/끝점에서 250mm(또는 `SnapTolerance`) 이내인 후보는 제외하고, 서로 너무 가까운 후보는 하나로 합친다.
   - 후보가 28개(`maxFeatures`)를 넘으면 역할 우선순위(`FeatureRoleWeight`: StartStub/EndApproach=3, Bend/ElevationChange=2, TrunkGuide=1)로 상위 28개만 남긴 뒤, 원래 경로상 순서로 복원한다(순서가 흐트러지면 라우팅이 왔다갔다 할 수 있어서).
3. **시작 수직 스텁 강제** (`ApplyStartVerticalStub`): 시작·끝 PoC의 Z차이가 `TrayHeight` 이상인 경우, 기존설계가 실제로 시작하자마자 수직으로 빠졌는지(`DominantAxis(legacyDelta) == 2`) 확인해서 그 고도(또는 없으면 종단 Z)로 `(task.Start.X, task.Start.Y, targetZ)` 지점을 만들고, 이 점을 **`Required = true`인 `StartStub` 특징점으로 맨 앞에 삽입**한다. `Required` 특징점은 일반 특징점과 달리 엔진의 허용오차/우회 필터링을 무조건 통과한다(§8-8, §8-9 참고).

### 5.4 자동설계 엔진이 특징점을 실제로 소비하는 방식

`BuildFeatureWaypoints`가 만든 `List<RouteFeature>`는 `MainWindow.Route()` → `engine.Route(start, end, obstacles, featureWaypoints, options)`를 거쳐 관리형/네이티브 엔진에 그대로 전달되고, 엔진 내부의 `BuildSnappedPointList`([ManagedRubberBandEngine.cs:63](RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs#L63) / C++ `build_snapped_points`)가 이를 A* 이전의 "제어점 뼈대"로 압축한다.

- 시작/끝점 기준 `SnapTolerance` 이내로 붙어 있는 특징점은 건너뛴다(이미 시작/끝과 사실상 같은 위치이므로) — 단, `Required=true`면 이 필터를 건너뛰지 않고 무조건 포함한다.
- 직선 경로에서 `max(len*2.5, 10000mm)`보다 먼 곳에 있는 특징점(=터무니없는 우회를 유발하는 특징점)은 제외한다 — 이 필터도 `Required`면 적용되지 않는다.
- 남은 특징점들을 순서대로 이어 붙여 `[start, 특징점1, 특징점2, ..., end]` 형태의 좌표열을 만들고, 이후 Step2(직교 스켈레톤) → Step3(A*) → Step4(LOS 직선 단축)이 이 좌표열을 지나가도록(waypoint로 삼아) 라우팅한다.

즉, "기존설계 특징점을 활용한다"는 것은 구체적으로 **기존 배관 폴리라인에서 뽑은 대표 꺾임/고도변경/시작방향/종단방향 점들을 자동설계 A*가 반드시(또는 되도록) 거쳐가야 할 waypoint로 주입하는 것**이며, 그 원본 데이터는 `TB_ROUTE_PATH`/`TB_ROUTE_SEGMENTS`/`TB_ROUTE_SEGMENT_DETAIL` 3테이블 조인 결과다.

현재 특징점으로 활용하는 대표 요소는 다음과 같다.

| 특징점 유형   | 의미                                          | 자동경로 반영                                                              |
| ------------- | --------------------------------------------- | -------------------------------------------------------------------------- |
| Start stub    | 장비 PoC에서 배관이 처음 빠져나가는 방향/드롭 | 시작 직후 control point 후보 (Z차이가 크면`Required` 스텁으로 강제 삽입) |
| Existing bend | 기존 배관의 방향 전환점                       | 주요 꺾임 후보                                                             |
| Z change      | 고도 변경 구간                                | 수직 이동 후보 (Bend보다 우선 분류)                                        |
| Trunk guide   | 긴 공용 직선 배관 흐름(4000mm 초과 구간)      | 기존 배관과 유사한 주행 방향 유도                                          |
| End approach  | 덕트/레터럴 PoC 접근 방향                     | 종단부 접속 방향 보정                                                      |

`RouteFeature`(위치/역할/`Required` 여부) 모델은 §9-1에서 이미 도입되었고, 엔진(관리형/네이티브) 양쪽이 이 모델을 공통으로 소비한다.

---

## 6. 자동경로 누적 장애물 처리

자동설계는 태스크를 순차 실행한다. 한 경로가 생성되면 해당 경로의 배관 envelope을 AABB 장애물로 변환하여 다음 태스크의 장애물 목록에 추가한다.

이 방식으로 나중에 생성되는 경로는 앞서 생성된 자동경로를 피해야 한다. 즉, 자동설계 경로도 기존 장애물과 동일하게 collision 대상이 된다.

---

## 7. 뷰어 기능 현황

- DB 연결 정보 입력 및 프로젝트 로딩 — 연결정보(호스트/포트/계정/DB명/마지막 프로젝트)는 `%AppData%`에 저장되어 다음 실행 시 자동으로 채워지고 자동 로딩됨(비밀번호는 DPAPI 암호화)
- 유틸리티 그룹/유틸리티별 경로 태스크 필터링
- 기존 배관 표시 및 유틸리티 그룹별 색상 표시
- 선택 유틸리티만 기존배관/자동경로 대상 필터링
- 공간 CubeBox 선형 표시
- 장애물(30% 투명도), 장비, 덕트, 레터럴, 기존배관, 자동경로, PoC, 특징점(반투명 큐브) 표시 토글
- 자동설계 결과 데이터그리드 표시 — 태스크별/전체 라우팅 소요시간(ms) 표시 포함
- 결과 선택 시 3D View에서 선택 경로 강조 표시
- 3D 뷰에서 배관(기존/자동)·특징점을 직접 클릭하면 속성 표시 + 해당 데이터그리드 행 자동 선택(피킹)
- 자동경로 삭제(선택 행만 삭제 / 전체 삭제) 버튼
- "기존경로와 비교" 다이얼로그 — 좌우 2개 3D 뷰 + 단계별 세그먼트 그리드로 기존경로/자동경로 비교, 세그먼트 클릭 시 하이라이트, 특징점도 양쪽 뷰에 표시
- 분석결과, 단계별 경로, 세그먼트 상세 데이터그리드 표시
- 관리형(C#)/네이티브(C++) 엔진 전환 체크박스(네이티브 DLL 존재 시 기본 사용)
- FPS 및 렌더 객체 수 표시
- 프로젝트 로딩 후 ZoomFit 처리

---

## 8. 검증 상태

최근 빌드 검증:

```powershell
dotnet build .\RubberBandRoutingSuite\RubberBandRoutingSuite.sln -c Debug --nologo -v:minimal /p:UseAppHost=false /p:OutDir=C:\tmp\RubberBandRoutingSuite-build\
```

결과:

- 빌드 성공
- 오류 0개
- HelixToolkit.Wpf `NU1701` 호환성 경고 2개 존재

---

## 9. 남은 개선 항목

1. `RouteFeature` 데이터 구조 추가 (완료)

   - `Vec3` 좌표 대신 `RouteFeature(Position, Role, Required)`를 도입. `RouteFeatureRole`은 `StartStub/Bend/ElevationChange/TrunkGuide/EndApproach/Unknown`.
   - `Required=true`인 특징점(예: `ApplyStartVerticalStub`가 삽입하는 시작 수직 스텁)은 `BuildSnappedPointList`/`build_snapped_points`의 snap-tolerance·detour 필터를 우회해 항상 경로에 반영된다. 관리형·네이티브 엔진 모두 동일하게 적용.
   - `MainWindow.ExtractExistingRouteFeatures()`가 각 특징점에 역할을 부여하고, 28개 상한 트리밍 시 역할 우선순위(시작스텁/종단접근=3, 꺾임/고도변경=2, 트렁크=1)로 먼저 걸러낸 뒤 경로 순서를 복원해 중요한 점이 트리밍에서 먼저 탈락하지 않게 한다.
   - 뷰어는 역할별로 특징점 마커 색상을 구분해 표시하고(꺾임=Magenta, 고도변경=Cyan, 트렁크=MediumPurple, 시작스텁=OrangeRed, 종단접근=LimeGreen), 분석 그리드에 "특징점 구성" 요약(예: `꺾임 3, 고도변경 1, 트렁크 2`)을 표시한다.
   - `Vec3`는 여전히 `RouteFeature`로 암묵 변환되므로(`implicit operator`) 위치만 필요한 호출부는 변경이 필요 없다.
2. 특징점 선택/스코어링 고도화 (일부 완료)

   - 위 1번의 역할 우선순위 트리밍으로 "무엇을 남길지"는 개선되었다. 유틸리티/관경/고도층 유사도 기반 스코어링은 아직 남은 과제.
3. 최종 배관 형상 검증 (완료)

   - `IsRoundedPathClear()`로 표시 단계의 둥근 bend가 확장 장애물과 재충돌하는지 검사하고, 충돌 시 sharp 중심선으로 대체 표시한다.
4. C++ 엔진 동기화 (완료, P/Invoke 연동 포함)

   - C++ Native 엔진을 C# 엔진과 동일한 특징점+A*+슬래브 판정 알고리즘으로 재작성(`rb_set_features`, `rb_set_feature_flags`, `rb_get_segment_reason` 추가).
   - `RubberBandRouting.Engine`에 `IRubberBandEngine` 공통 인터페이스, `NativeMethods`(P/Invoke 선언), `NativeRubberBandEngine`(rb_* 호출 래퍼)을 추가해 뷰어가 관리형/네이티브 엔진을 런타임에 전환 가능. 뷰어 하단 "네이티브 C++ 엔진" 체크박스로 토글하며, DLL 부재 시 관리형으로 자동 대체.
   - `Viewer.csproj`는 `PlatformTarget=x64`(네이티브 DLL과 프로세스 비트수 일치)와 `cpp/RubberBandRouting.Native/RubberBandRouting.Native.dll`(존재 시) 출력 복사 항목을 포함.
   - 검증: 스모크 테스트에서 관리형/네이티브 엔진이 동일 시나리오(장애물 1개 + 특징점 2개, 그중 1개는 `Required=true`)에서 동일 세그먼트 수(11)·동일 총 길이(17000mm)·동일 다중 파이프(3개, 동일 시작/끝점)를 반환함을 확인. `Required` 특징점이 두 엔진 모두에서 필터를 우회해 실제 반영됨도 확인.
5. 단계별 사유 데이터 구조화 (완료)

   - `RubberBandResult.SegmentReasonCodes`를 추가해 엔진이 각 세그먼트의 생성 사유(`route_start/start_drop_stub/feature_snap/collision_bypass/direction_change/elevation_change/rubber_alignment`)를 직접 반환한다. 관리형은 `ClassifySegmentReasons()`, 네이티브는 `classify_segment_reasons()`(C++)로 동일 로직을 포팅했으며, `SegmentReasons` 정적 클래스(Models.cs)가 토큰 어휘를 공유한다.
   - 뷰어는 더 이상 사유를 추론하지 않고 `result.SegmentReasonCodes`를 한글 라벨로만 매핑해 표시한다(`SegmentReasonLabel`).
   - 참고: 사유 분류는 근사 휴리스틱(특징점 근접/장애물 격자 경계 근접/축 변화/고도 변화)이라 두 엔진이 동일 경로에서도 드물게 인접한 두 사유(예: `collision_bypass` vs `direction_change`) 중 다른 쪽을 고를 수 있다 — 기하학적 결과(세그먼트/길이/파이프)는 항상 동일하다.
6. 다중 파이프 렌더링 (완료)

   - 엔진이 계산한 `RubberBandResult.PipePaths`가 이전에는 계산만 되고 버려졌으나, 이제 `MainWindow.RedrawAutoRoutes()`가 `PipeCount > 1`이면 각 파이프 경로를 개별 튜브로 렌더링한다(`DrawRoundedPolyline`).
   - `PrependStartStub()`이 시작 수직 드롭 스텁을 중심선뿐 아니라 각 파이프 경로에도 동일한 측방 오프셋으로 연장해, 다중 파이프 표시에서도 드롭 구간이 누락되지 않게 한다.
   - 라운드 bend 재충돌 검사(`RoundSafe`)는 여전히 중심선 기준 근사치이며, 모든 파이프에 동일하게 적용한다(개별 파이프별 재검사는 비용 대비 효과가 낮아 보류).
7. `PipeCount` 하드코딩 및 파이프 오프셋 코너 버그 수정 (완료, 2026-07-05)

   - 다중 파이프 렌더링 도입 직후 "자동경로가 1개가 아니라 여러 개로 지저분하게 나온다"는 문제가 보고됨. 원인은 두 가지였다.
   - **원인 1**: `MainWindow.ReadOptions()`와 `RubberBandOptions.PipeCount` 기본값이 무조건 `3`으로 하드코딩되어, 실제로 몇 가닥인지와 무관하게 모든 태스크가 항상 3개 평행선으로 증폭 렌더링됨. 근거 데이터가 없는 한 기본값을 `1`로 변경(`Models.cs`, `MainWindow.xaml.cs`, C++ `RbConfig` 기본값 동일 반영).
   - **원인 2**: `DistributePipes`(관리형)/`distribute()`(네이티브)가 코너(꺾이는 지점)에서 세그먼트 양 끝을 서로 다른 법선(직전 세그먼트의 법선 vs 다음 세그먼트의 법선)으로 오프셋해, 파이프 라인이 중심선과 평행하지 않고 대각선으로 비틀리며 서로 교차하는 것처럼 보였다. 각 세그먼트를 자기 자신의 법선 하나로만 양 끝을 오프셋하도록 수정(`ComputeSegmentNormals`/`compute_segment_normals`을 먼저 계산해 세그먼트당 하나의 연속적인 법선 유지). 꺾이는 지점에서는 두 세그먼트의 오프셋이 달라 생기는 짧은 연결 조인트(jog)가 자연스럽게 생기는데, 이는 실제 평행 배관 elbow의 정상적인 형태다.
   - 검증: 관리형·네이티브 각각에서 "모든 센터라인 세그먼트에 대해 각 파이프가 정확히 고정된 오프셋 크기를 유지하는 평행 엣지를 갖는다"는 불변식을 스모크 테스트로 확인(PASS). 기본 옵션 실행 시 `PipeCount=1`로 파이프 1개(기존 중심선과 동일)만 생성됨도 확인.
8. 시작 수직 스텁 과다 하강 / 특징점 사선 미지원 / A* 전면 실패로 인한 충돌 회피 무력화 수정 (완료, 2026-07-05)

   - 실제 씬(WTNHJ02/BAY004, Exhaust/ACID 20개 태스크 일괄 라우팅) 스크린샷 리뷰에서 세 가지 문제가 보고됨.
   - **원인 1 — 시작점 수직하강 과다**: `MainWindow.Route()`가 항상 `RequiredStartDropPoint()`를 먼저 호출해 시작 PoC에서 **최종 목적지 고도(`task.End.Z`)까지 통째로** 수직 하강시켰다. 별도로 `ApplyStartVerticalStub()`가 기존설계 특징점 기반의 더 합리적인 중간 트레이 고도(`second.Z`)를 계산해 두었지만, `RequiredStartDropPoint()`가 그 값을 무시하고 항상 이겼다. **수정**: `RequiredStartDropPoint(task, options, featureWaypoints)`가 `task.Start`와 동일 X/Y를 갖는 `Required` 특징점(주로 `ApplyStartVerticalStub`가 삽입한 `StartStub`)이 있으면 그 Z를 우선 사용하고, 없을 때만 `task.End.Z`로 전량 하강한다.
   - **원인 2 — 특징점 사이 사선 연결 부재**: Step2(`MakeOrthogonalSegments`)와 Step3(6방향 A*)는 구조적으로 대각선 이동이 불가능해, "고무줄"이라는 이름과 달리 순수 Manhattan 라우터였다. **수정**: Step3 A* 결과에 **Step4 LOS(line-of-sight) 직선 단축** 후처리(`ApplyLineOfSightShortcuts`/C++ `apply_line_of_sight_shortcuts`)를 추가했다. 폴리라인을 앞에서부터 훑으며, 장애물에 막히지 않는 한 가장 먼 뒤쪽 정점까지 직선(대각선 포함)으로 단축하는 greedy string-pulling 방식이다. 단, `Required` 특징점(예: 시작 드롭 스텁)은 시야가 뚫려 있어도 절대 건너뛰지 않도록 각 확장 단계마다 다음 필수 정점을 상한으로 고정했다(초기 구현에서 이 가드가 없어 장애물이 없는 구간에서 필수 정점이 건너뛰어지는 회귀가 있었고, 스모크 테스트로 발견·수정함).
   - **원인 3 — 다른 자동배관과의 충돌(원인: A* 전체 실패 → 무방비 폴백)**: 해당 스크린샷의 자동설계 결과 20건 전부가 `상태=확인, 실패사유=astar_fallback_used`였다. A*가 실패하면 `MakeOrthogonalSegments`로 무장애물검사 직결 폴백하므로, 장애물 누적 메커니즘 자체는 정상이어도 사실상 충돌 회피가 전혀 작동하지 않았다. **완화 조치**: 관리형 `maxExpansions`/네이티브 `kMaxExpansions`를 50,000 → 200,000으로 상향. 단, 장비 PoC가 현재 클리어런스(TrayWidth/2+SafetyMargin)보다 촘촘히 몰려 있어 애초에 직교 경로상 여유 공간이 없는 경우라면 예산을 올려도 근본적으로 해결되지 않을 수 있으며, 이 경우는 별도 진단(혼잡도/클리어런스 조정)이 필요하다.
   - 검증: 스모크 테스트로 (a) 장애물 없는 구간이 관리형·네이티브 모두 시작-끝 단일 대각선으로 정확히 축약됨, (b) 장애물이 있는 구간은 두 엔진 모두 우회하며 잔여 충돌 없음(`IsValid=true`), (c) `Required` 특징점은 장애물이 없어 시야가 뚫려 있어도 두 엔진 모두 반드시 경유함을 확인.
9. 파이프 밴딩 R값(곡률 반경)을 외경 배수 변수로 전환 (완료, 2026-07-05)

   - 뷰어 표시 단계의 둥근 bend 반경이 `Math.Max(diameter * 1.5, diameter + 80)`를 `[120, 1800]`으로 클램프하는 임의 휴리스틱이었다. 실제 배관 가공 관례(외경 D 기준 최소 3D 이상 확보)와 무관했고, 상한 클램프(1800mm)가 큰 배관·큰 배수 요구를 조용히 잘라낼 수 있었다.
   - **수정**: `RubberBandOptions.BendRadiusFactor`(기본값 `3.0`, 즉 3D) 추가. `MainWindow.BendRadius(diameter, bendRadiusFactor)`가 `diameter * bendRadiusFactor`만 계산하도록 단순화하고, 임의 상한 클램프를 제거했다 — 대신 `BuildRoundedBendPolyline`이 이미 갖고 있던 "가용 직선 구간 길이의 45%" 상한과 "20mm 미만이면 sharp corner로 폴백"이 실사용 가능한 반경으로 자연스럽게 제한한다.
   - `MainWindow.ReadOptions()`에서 `BendRadiusFactor = 3.0`으로 명시적으로 설정하며, `IsRoundedPathClear()`(라운드 재충돌 검사)와 `DrawRoundedPolyline()`(렌더링) 양쪽 모두 이 값을 사용해 R값이 일관되게 적용된다.
   - 현재는 다른 `RubberBandOptions` 필드(SafetyMargin, TrayWidth 등)와 동일하게 코드 레벨 변수로 노출되어 있다(UI 텍스트박스는 아직 없음 — 다른 엔진 파라미터들도 아직 UI에 노출돼 있지 않아 일관성을 맞춤). UI에서 직접 조정 가능하게 하려면 별도 텍스트박스와 `ReadOptions()` 파싱 로직 추가가 필요하다.
10. 결과 그리드 UX 개선: 상태값 의미 확인, 라우팅 소요시간 표시, 선택 경로 삭제, 네이티브 엔진 기본 사용, 특징점/장비 표시 방식 변경 (완료, 2026-07-05)

    - **상태=확인 관련 질의에 대한 설명**: `상태` 컬럼은 `성공`/`확인` 두 값만 쓰며(코드상 "실패"라는 라벨은 없음), `확인`은 엔진이 경로 자체는 생성했지만 사후 검증(`Validate`/`rb_is_valid`)에서 문제 신호를 검출했다는 뜻이다. 신호는 세 가지뿐이다 — `astar_fallback_used`(A*가 실패해 무장애물 폴백 사용), `vertical_bends_exceeded`(수직 꺾임 횟수 초과), `residual_collision`(최종 경로 세그먼트가 안전마진(`TrayWidth/2+SafetyMargin`)을 확장한 장애물 AABB와 실제로 교차). 즉 `확인`은 오탐이 아니라 **실제 기하 검사 결과**이며, `성공`인 행은 이미 `실패사유` 칸이 항상 비어 있다(기존 코드 그대로 유지, 별도 수정 불필요 — `ComputeRoutes`에서 `result.IsValid ? "성공" : "확인"`, `failure = result.IsValid ? "" : ...`).
    - **소요시간 표시**: `ComputeRoutes`의 태스크별 루프에 `Stopwatch`를 추가해 `ResultRow.ElapsedMs`(개별 라우팅 소요시간, ms)를 계산해 결과 그리드에 `라우팅시간(ms)` 컬럼으로 노출했다. `RouteRowsAsync`에도 전체 배치 `Stopwatch`를 추가해 완료 시 상태 표시줄에 `Auto routing completed: N/M tasks in TTT ms.`로 전체 소요시간을 표시한다.
    - **선택 경로 삭제**: 결과 그리드 헤더 옆에 `선택 경로 삭제` 버튼(`BtnDeleteSelectedRoute_Click`)을 추가해 그리드에서 선택한 행 하나만 `_resultRows`에서 제거하고 재렌더링한다. 기존 좌측 패널의 `♜ 자동설계 경로 삭제`(`BtnClearRoutes_Click`, 전체 삭제)는 그대로 유지된다.
    - **네이티브 C++ 엔진 기본 사용**: `MainWindow()` 생성자에서 `ChkUseNativeEngine.IsChecked = NativeRubberBandEngine.IsAvailable`로 초기화해, DLL이 배치되어 있으면 기본적으로 네이티브 엔진을 쓰도록 바꿨다(기존에는 기본 미체크 상태라 매번 수동으로 켜야 했음). DLL이 없으면 자동으로 관리형(C#) 엔진으로 남는다.
    - **특징점 표시를 구체 → 반투명 큐브로 변경**: `DrawFeaturePoints`가 `AddSphere` 대신 `AddBox`로 각 특징점 위치에 정육면체(한 변 240mm/선택 시 320mm)를 그리며, 알파값 128(≈50%)의 반투명으로 렌더링한다(`FeatureRoleColor`로 역할별 색상은 그대로 유지).
    - **메인 장비 투명도 30%로 조정**: `DrawScene`의 장비 박스 렌더링 알파값을 90(≈35%) → 77(≈30%)로 낮췄다.
11. DB 연결정보 저장/자동로딩 및 상단 툴바 정리 (완료, 2026-07-05)

    - **연결정보 영속화**: 새 `ViewerSettings` 클래스(`%AppData%\RubberBandRoutingViewer\connection.json`)를 추가해 Host/Port/Username/Database/마지막 선택 프로젝트를 저장한다. 비밀번호는 평문 저장하지 않고 Windows DPAPI(`ProtectedData.Protect`, `DataProtectionScope.CurrentUser`)로 암호화해 `EncryptedPassword`에 base64로 저장한다(같은 Windows 계정에서만 복호화 가능). 패키지 참조 `System.Security.Cryptography.ProtectedData` 8.0.0 추가.
    - `MainWindow()` 생성자에서 `ApplySavedConnectionSettings()`로 저장된 값을 입력 필드에 미리 채우고, `LoadProjectsAsync()`가 프로젝트 목록 조회에 성공한 시점(연결이 실제로 유효했다는 뜻)에 `SaveConnectionSettings()`를 호출해 갱신한다. 창을 닫을 때(`Closing`)도 한 번 더 저장한다.
    - 마지막으로 선택했던 프로젝트는 `DisplayName` 문자열로 기억해 두었다가, 다음 실행 시 프로젝트 목록에서 동일한 `DisplayName`을 찾아 재선택한다(없으면 기존처럼 0번째 항목). 기존에도 앱 최초 실행 시 자동으로 `LoadSceneAsync()`가 호출되는 로직(`_loadedInitialScene`)이 있었으므로, 이제는 마지막 접속정보 + 마지막 프로젝트로 완전히 자동 로딩된다.
    - **상단 툴바 정리**: 상단 바를 고정폭 `Grid`(컬럼 12개, 오른쪽으로 갈수록 좁아지는 `프로젝트` 콤보박스가 `*`로 늘어나던 구조)에서 `HorizontalAlignment="Left"`인 `StackPanel`로 교체해, DB 필드 → 프로젝트 콤보(고정폭 260) → `로드`/`기본설계` 버튼까지 왼쪽부터 내용 크기만큼만 차지하도록 정리했다.
    - 상단 바에 있던 `다단 락`(그룹 전체 라우팅)과 `전체보기` 버튼을 제거했다. `다단 락`은 좌측 패널의 `▶ 이 그룹 전체 라우팅`(`BtnRouteSelectedGroup`)과 클릭 핸들러(`BtnRouteGroup_Click`)가 완전히 동일한 중복 버튼이었고, `전체보기`(`BtnRouteAll_Click`/`RouteAllAsync`)는 그룹 필터가 선택된 상태에서는 사실상 같은 동작이라 혼란만 주었다 — 두 핸들러 중 `RouteAllAsync`와 `BtnRouteAll_Click`은 사용처가 없어져 코드에서도 제거했다. `SetBusy()`의 `BtnRouteGroup`/`BtnRouteAll` 참조도 함께 정리했다.
12. 결과 그리드 삭제 버튼 명칭 통일 + 기존경로/자동경로 비교 다이얼로그 신설 (완료, 2026-07-05)

    - **버튼 명칭 변경**: 결과 그리드 헤더 옆 `선택 경로 삭제`(`BtnDeleteSelectedRoute`, 선택한 한 행만 삭제) 버튼의 표시 텍스트를 `자동경로삭제`로 변경했다. 동작(선택 행만 제거)은 그대로이며, 좌측 패널의 `♜ 자동설계 경로 삭제`(`BtnClearRoutes`, 전체 삭제)와는 별개 버튼이다.
    - **`⇄ 기존경로와 비교` 버튼 추가**: 좌측 ② 유틸리티 패널 하단에 신설(`BtnCompareRoutes_Click`). 현재 결과 그리드(`_resultRows`)가 비어 있으면 상태표시줄에 안내만 하고 종료한다.
    - **비교 다이얼로그(`CompareRoutesWindow`, 비모달)**: 클릭 시 `MainWindow.BuildCompareEntries()`가 각 자동경로 결과 행에 대해 `FindMatchedExistingRoute(task)`로 이미 매칭해 둔 기존설계 경로(`ResultRow.MatchedExistingRoute`, `ComputeRoutes`에서 함께 계산해 저장하도록 `ResultRow`에 `Task`/`MatchedExistingRoute` 필드를 추가함)를 `RouteCompareEntry` 목록으로 변환해 새 창에 전달한다.
      - 좌측: 경로 목록 그리드(#, 유틸, 시작/종단 PoC, 매칭 상태 — `GUID 직접매칭`/`조건 fallback`/`매칭 없음`). 행을 선택하면 우측이 갱신된다.
      - 우측 상단: 좌우 2분할 3D 뷰(`ViewportExisting`/`ViewportAuto`, 각각 독립 `HelixViewport3D`), **각 뷰 오른쪽에 세그먼트 정보 그리드**(`GridExistingSegments`/`GridAutoSegments`: #, 방향(+X/-X/+Y/-Y/+Z/-Z/사선), 길이)를 추가했다. 두 경로 모두 세그먼트 단위로 색상을 순환시켜(`SegmentColors` 8색 팔레트) 그려 어느 구간이 어떻게 갈리는지 구분되게 했고, 꺾이는 지점마다 흰색 구체로 정점을 표시했다.
      - **세그먼트 그리드 → 3D 뷰 하이라이트 연동**: `GridExistingSegments`/`GridAutoSegments`에서 행을 클릭하면 해당 그리드가 속한 3D 뷰에서만(다른 쪽 뷰는 그대로) 그 세그먼트를 노란색 굵은 튜브(직경 80→170)로, 양 끝 정점을 빨간 구체(반경 60→130)로 다시 그려 강조한다(`DrawPolyline(..., highlightIndex, fitCamera:false)` — 하이라이트 시에는 카메라를 다시 맞추지 않아 사용자가 보던 화면 위치/줌을 유지).
      - 우측 하단: 비교 분석 표 — 매칭 상태, 세그먼트 수(기존/자동/차이), 총 길이(mm, 기존/자동/차이), 길이 변화율(%), 수직 Bend 수(기존/자동/차이), 시작·종단 위치 오차(mm, 기존 폴리라인 끝점과 자동경로 첫/끝 세그먼트 끝점 간 거리)를 계산해 보여준다. 매칭되는 기존경로가 없으면 그 사실만 안내하고 자동경로만 그린다.
      - 새 파일: `CompareRoutesWindow.xaml`/`.xaml.cs` (내부적으로 `RouteCompareEntry`/`AnalysisCompareRow`/`SegmentInfoRow` record 포함).
13. 비교 다이얼로그 3D 뷰 초기 전체보기 미작동 수정 + 수동 전체보기 버튼 추가 (완료, 2026-07-05)

    - 12번 항목에서 `ZoomExtents(200)`를 `Dispatcher.BeginInvoke(..., DispatcherPriority.ContextIdle)`로 지연시켰던 수정은 실제로는 더 나빠졌다 — 실사용 스크린샷에서 기존경로/자동경로 두 3D 뷰 모두 빈 화면(중앙에 점 하나만 찍힌 상태)으로 나타났다. 원인은 지연된 콜백이 실행되는 시점에도 여전히 뷰포트가 신뢰할 만한 크기로 레이아웃되지 않았거나, 두 뷰포트의 지연 호출이 겹치며 카메라 피팅이 어긋난 것으로 보인다.
    - **수정**: `DrawPolyline`의 카메라 핏은 다시 동기 호출(`viewport.ZoomExtents(200)`)로 되돌렸다. 대신 다이얼로그 생성자에서 `Window.ContentRendered` 이벤트(창이 실제로 화면에 그려진 뒤 정확히 한 번 보장되는 시점)에 `FitBothOnce()`를 걸어, 최초 진입 시 레이아웃이 아직 준비되지 않은 상태에서 호출됐을 가능성이 있는 첫 핏을 다시 한번 확실하게 맞춰준다(`_initialFitDone` 플래그로 최초 1회만 실행).
    - **수동 `⤢ 전체보기` 버튼 추가**: 기존경로/자동경로 각 3D 뷰 헤더(`DockPanel`로 변경)에 개별 버튼(`BtnFitExisting_Click`/`BtnFitAuto_Click`)을 추가해, 세그먼트 하이라이트나 사용자 드래그로 뷰가 어긋났을 때 언제든 그 뷰만 다시 전체보기로 맞출 수 있게 했다.
14. 특징점 큐브 크기 축소 + 비교 다이얼로그 세그먼트 그리드를 "단계별 경로" 수준으로 확장 (완료, 2026-07-05)

    - **특징점 큐브 축소**: 메인 3D 뷰의 `DrawFeaturePoints`가 그리는 특징점 정육면체 한 변 반크기(`halfSize`)를 기존 120mm/선택 시 160mm에서 60mm/선택 시 80mm로 절반으로 줄였다(항목 10에서 도입한 반투명 큐브 표시 방식은 그대로 유지).
    - **비교 다이얼로그 세그먼트 그리드 확장**: 기존에는 `#/방향/길이` 3개 컬럼뿐이라 메인 창의 "단계별 경로" 탭 대비 정보가 부족했다. `SegmentInfoRow`를 메인 창의 `StepDetailRow`와 동일한 형태(`Index, SegmentType(시작/꺾임), Start, End, Direction, LengthMm, Reason`)로 확장하고, 그리드 컬럼도 `#/구간/방향/길이/꺾임·사유`로 맞췄다(폭이 좁아 좌표 컬럼은 그리드에 노출하지 않고 3D 하이라이트로 대체).
      - **자동경로**: `MainWindow.BuildCompareEntries()`가 `ResultRow.StepRows`(엔진이 실제로 계산한 `SegmentReasonCodes` 기반 사유 텍스트, 메인 창 "단계별 경로" 탭과 완전히 동일한 데이터)를 그대로 `SegmentInfoRow`로 매핑한다.
      - **기존경로**: 엔진이 사유를 계산해주지 않으므로 `BuildExistingStepRows()`가 as-built 폴리라인 특성에 맞는 최소한의 라벨을 붙인다 — 첫 세그먼트는 "기존경로 시작점", 마지막은 "기존경로 종단점", 그 사이는 (모든 정점이 실제 배관이 꺾인 지점이므로) "실시공 꺾임점"으로 표시하고, 구간/방향/길이는 메인 창과 동일한 `SegmentDirection`/`FormatVec` 로직을 재사용해 계산한다.
      - `RouteCompareEntry`에 `ExistingSteps`/`AutoSteps` 필드가 추가되어 그리드에 그대로 바인딩되고, 세그먼트 클릭 시 3D 하이라이트로 연결되는 인덱스(`SegmentInfoRow.Index`)는 이전과 동일하게 동작한다.
15. 비교 다이얼로그 "전체보기"가 배관을 화면 구석의 점으로 축소시키던 문제 수정 (완료, 2026-07-05)

    - **원인**: `HelixViewport3D.ZoomExtents()`(인자 없는/margin만 있는 버전)는 뷰포트의 **모든** 비주얼을 기준으로 바운딩 박스를 계산한다. 그런데 각 3D 뷰에는 XAML에서 고정 배치한 `GridLinesVisual3D`(가로/세로 30000mm 기준격자)가 항상 떠 있고, 실제 배관 경로는 보통 이보다 훨씬 작다. 그 결과 카메라가 3만mm 격자 전체가 들어오도록 줌아웃되어, 실제 배관은 화면 한쪽 구석의 작은 점으로 표시되었다.
    - **수정**: `FitViewportToPoints(viewport, points)`를 새로 추가해, 뷰포트의 전체 비주얼이 아니라 **전달받은 배관 좌표점들만의 바운딩 박스**로 카메라를 맞춘다(중심/반경 계산 후 `PerspectiveCamera.Position/LookDirection/NearPlaneDistance/FarPlaneDistance`를 직접 설정 — `MainWindow.FitProjectToViewport()`와 동일한 수식). `FitBothOnce()`, `BtnFitExisting_Click`/`BtnFitAuto_Click`, `DrawPolyline`의 자동 핏 분기 모두 이 메서드로 교체했다. 이제 격자 크기와 무관하게 배관 경로 자체의 바운딩 박스로 정확히 확대된다.
16. 메인 뷰어에 남아 있던 영어 UI 문구를 한글로 통일 (완료, 2026-07-05)

    - 대부분의 UI는 이미 한글이었지만, 상태표시줄(`TxtStatus.Text`)과 씬 분석 그리드(`SetSceneAnalysis`) 일부 문구가 영어로 남아 있었다. 다음을 한글로 교체했다.
      - `LoadProjectsAsync`/`LoadSceneAsync`/`ShowExistingRoutesAsync`/`RefreshExistingRoutesForCurrentGroup`/`RouteRowsAsync`/`RunBusyAsync`의 진행/완료/오류 상태 메시지("Loading projects...", "Scene loaded: ...", "Auto routing completed: ...", "Error" 등) 전부.
      - `RouteGroupAsync`/`RouteUtilityAsync`가 `RouteRowsAsync`에 넘기던 `scope` 문자열(`"Group {group}"`, `"Utility {utility}"` → `"그룹 {group}"`, `"유틸리티 {utility}"`).
      - `SetSceneAnalysis`의 항목명(`Project`, `Obstacles`, `Equipment`, `Duct/Lateral`, `Endpoint PoC`, `Route tasks`, `Existing paths` → 프로젝트/장애물/장비/덕트·레터럴/종단 PoC/라우팅 태스크/기존경로).
      - [MainWindow.xaml](RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml)의 상태표시줄 초기값 `"Ready"` → `"준비됨"`, 버튼 `"Reset Camera"` → `"카메라 초기화"`.
    - DB 연결 필드 기본값(`localhost`, `postgres`, `DDW_AI_DB`)이나 `FPS`처럼 원래 영문/약어가 표준인 항목은 그대로 두었다.
17. 자동경로 bend(꺾임) 위치가 기존설계처럼 매끄럽지 않고 지그재그로 보이는 문제 진단·수정 (완료, 2026-07-05)

    - 실사용 스크린샷(기존설계 vs 자동설계 클로즈업 비교)에서, 기존설계는 배관마다 부드러운 elbow 1개로 꺾이는 반면 자동설계는 코너 2개가 붙어 눌린 지그재그처럼 보였다.
    - **1차 진단(코너 병합)**: `BuildRoundedBendPolyline`의 라운딩 반경이 `Math.Min(requestedRadius, Math.Min(inLen, outLen) * 0.45)`로 인접 직선 구간 길이에 비례해 줄어드는데, 중심선 자체에 짧은 곁가지 코너 쌍이 남아있으면 두 코너 모두 반경이 눌려 지그재그로 보인다는 가설로, "직전/직후 연장선의 교점으로 코너 2개를 코너 1개로 병합"하는 `MergeShortDoglegs`(관리형 `ManagedRubberBandEngine.cs`)/`merge_short_doglegs`(네이티브 `rubberband_native.cpp`)를 Step 5로 추가했다(장애물 재검사 통과 시에만 병합, `Required` 특징점은 병합 대상에서 제외).
    - **스모크 테스트로 발견한 한계**: 직접 두 시나리오로 검증한 결과 (a) "내려가다 → 짧게 옆으로 → 다시 내려가다"처럼 **들어오는/나가는 방향이 서로 평행한 오프셋**은 병합되지 않았고(3→3 세그먼트 그대로 — 평행선은 연장해도 절대 교차하지 않으므로 수학적으로 병합 불가), (b) "대각선 → 짧은 격자 잔재 → 다른 대각선"처럼 **평행하지 않아 실제로 교차하는 코너 자르기**는 정상적으로 병합됐다(3→2 세그먼트). 스크린샷의 지그재그는 (a) 유형(평행 오프셋)에 더 가까워, `MergeShortDoglegs`만으로는 실제 증상이 해결되지 않을 가능성이 높다고 판단해 사용자에게 재확인했다. (a) 유형은 실제 배관에서도 "엘보 2개 + 짧은 직관"으로 시공하는 정상적인 오프셋 형태라, "코너 1개로 합친다"는 접근 자체가 이 케이스엔 기하학적으로 맞지 않았다.
    - **근본 원인 수정(승인됨)**: 평행 오프셋이 애초에 왜 생기는지 재검토한 결과, `ComputeRoutes`가 앞서 라우팅된 자동경로를 전부 무조건 다음 태스크의 장애물로 누적시키고 있었다(§6) — 같은 트레이/번들(같은 그룹+유틸리티)에 속해 원래 나란히 붙어가야 할 형제(sibling) 배관들까지 서로를 장애물로 취급해, 뒤에 라우팅되는 배관이 앞선 형제 배관의 두꺼운 AABB 포락선을 피하려고 불필요하게 옆으로 피했다가 다시 돌아오는 오프셋을 만들어낸 것으로 보인다. **수정**: `ComputeRoutes`에서 경로별 장애물을 `RouteObstacleEntry(Aabb Box, string Group, string Utility)`로 태그해 별도 보관(`routeObstacles`)하고, 태스크 라우팅 시 자기 자신과 같은 그룹+유틸리티인 형제 배관의 장애물만 제외한 목록(`obstaclesForTask`)을 사용하도록 했다(다른 유틸리티의 자동경로·기존 씬 장애물은 그대로 회피). `IsRoundedPathClear`(라운드 재충돌 검사)도 동일하게 `obstaclesForTask` 기준으로 판정하도록 맞췄다.
    - `MergeShortDoglegs`/`merge_short_doglegs`(코너 자르기 병합)는 별도로 유효하고 안전한 개선이라 그대로 유지했다 — 형제 장애물 수정과는 독립적으로, A* 격자 스냅이 남긴 진짜 불필요한 코너를 계속 정리해준다.
    - **2차 회귀: 수평 구간에서까지 대각선 이동이 나타남**: 형제장애물 수정 직후 재확인한 실사용 스크린샷에서, 여러 배관이 각자의 특징점(웨이포인트)을 무시하고 하나의 넓은 대각선으로 뭉쳐 보이는 새로운 문제가 나타났다. 원인은 Step4 LOS 직선단축이 "장애물만 없으면 대각선 포함 직선으로 최대한 당긴다"는 로직을 **수평/수직 구분 없이 경로 전체에 무차별 적용**하고 있었기 때문 — 원래는 (사용자 확인 결과) 수평 이동은 항상 직교(X 또는 Y 단일 축)만 허용되고, 특징점은 반드시 웨이포인트로 거쳐야 하며, 다축 대각선은 "장비 PoC를 떠나는 시작 구간"에서만 허용되어야 했다. 형제장애물 제외로 수평 구간을 막던 장애물이 사라지면서, 이 무차별 대각선 로직이 훨씬 더 자주 성립하게 되어 문제가 도드라졌다.
    - **수정**: `ApplyLineOfSightShortcuts`/`apply_line_of_sight_shortcuts`의 각 확장 후보에 `IsShortcutDirectionAllowed`/`is_shortcut_direction_allowed` 방향 검사를 추가했다 — (1) X·Y가 동시에 바뀌면서 Z가 지배적이지 않은 "수평 평면 대각선"은 어떤 구간에서도 항상 금지, (2) 다축 대각선(예: Z와 X/Y가 섞인 경사 하강)은 폴리라인의 **첫 leg(i==0, 즉 시작점을 떠나는 구간)에서만** 허용하고 그 이후 모든 leg는 단일 축 이동만 허용. `MergeShortDoglegs`/`merge_short_doglegs`가 병합 시 새로 만드는 두 구간에도 동일한 검사를 적용해, 코너 병합으로 대각선이 재도입되는 것도 막았다.
    - 스모크 테스트로 (a) 수평 구간 + 오프셋 특징점 시나리오에서 관리형·네이티브 모두 대각선 없이 단일 축으로만 꺾이며 특징점을 정확히 통과함을 확인했고, (b) 시작 leg의 경사 하강은 여전히(관리형은 완전한 사선으로, 네이티브는 축별 계단형으로 — 이 차이는 A* 격자 처리 방식의 기존 엔진 간 특성으로 보이며 이번 수정과는 무관) 유효하게 처리됨을 확인했다.
18. 3D 뷰에서 기존설계/자동설계 배관 클릭 시 속성 표시 + 데이터그리드 선택 연동 (완료, 2026-07-05)

    - **배관 → 소유 데이터 매핑**: `_visualOwners`(`Dictionary<Visual3D, object>`) 딕셔너리를 추가해, 배관을 그리는 `TubeVisual3D`가 어떤 `ResultRow`(자동설계) 또는 `ExistingRoutePath`(기존설계)에 속하는지 역참조할 수 있게 했다. `DrawPath`/`DrawRoundedPolyline`/`DrawRoundedSegments`에 선택적 `owner` 매개변수를 추가해, `RedrawAutoRoutes`는 각 배관에 해당 `ResultRow`를, `DrawExistingRoutePaths`는 각 배관에 해당 `ExistingRoutePath`를 태깅한다. `ClearVisuals`가 비주얼을 지울 때 `_visualOwners`에서도 함께 제거해 재생성 시 오래된 매핑이 남지 않게 했다.
    - **클릭(피킹) 처리**: `Viewport`에 `PreviewMouseLeftButtonDown`/`Up` 핸들러를 달아, 버튼을 누른 위치와 뗀 위치가 4px 이내로 거의 같을 때만 "클릭"으로 인정한다(그 이상 움직이면 HelixToolkit의 카메라 회전/이동 드래그로 간주해 무시 — 카메라 조작과 충돌하지 않도록 함). 클릭으로 인정되면 `VisualTreeHelper.HitTest(Viewport.Viewport, position)`로 3D 피킹해 `RayMeshGeometry3DHitTestResult.VisualHit`을 얻고, `_visualOwners`에서 소유자를 조회한다.
    - **자동설계 배관 클릭**: 소유자가 `ResultRow`면 `GridResults.SelectedItem`으로 설정 — 기존 `GridResults_SelectionChanged`가 그대로 발동해 분석결과/단계별 경로/세그먼트 상세 탭이 갱신되고 3D에서도 노란색으로 강조된다(별도 코드 추가 없이 기존 인프라 재사용).
    - **기존설계 배관 클릭**: 소유자가 `ExistingRoutePath`면 `SelectExistingRoutePath()`가 (1) `_visibleTaskRows`에서 `RoutePathGuid`가 일치하는(또는 `FindMatchedExistingRoute` 결과가 동일한) 태스크를 찾아 `GridTasks`(③ 개별 PoC)에서 선택하고, (2) `GridAnalysis`(분석결과)에 이 기존경로 고유의 속성(GUID, 그룹, 유틸리티, 시작/종단 PoC, 관경, 점 개수, 총 길이)을 직접 채워 보여준다 — 기존설계 배관은 별도의 결과 그리드가 없으므로 분석결과 패널을 재사용했다.
19. 특징점 클릭 시 속성 표시 + 비교 다이얼로그 2개 3D 뷰에 특징점 표시 (완료, 2026-07-05)

    - **특징점 클릭 → 속성 표시**: `DrawFeaturePoints`가 각 특징점 큐브에 `FeaturePointInfo(RouteFeature Feature, ResultRow Route)` — 어떤 특징점이며 어느 경로에 속하는지 — 를 `_visualOwners`에 태깅하도록 `AddBox`에도 `owner` 매개변수를 추가했다(§18의 `DrawPath` 패턴과 동일). `PickVisualAt`에 `FeaturePointInfo` 케이스를 추가해, 특징점을 클릭하면 (1) 먼저 소속 경로를 `GridResults`에서 선택해 기존 하이라이트/탭 갱신을 그대로 재사용하고, (2) 이어서 `GridAnalysis`를 이 특징점만의 속성(역할 — `FeatureRoleLabel`로 한글 라벨, 필수 여부, 위치, 소속 경로의 그룹·유틸리티·시작/종단 PoC)으로 다시 채운다.
    - **비교 다이얼로그에 특징점 표시**: `RouteCompareEntry`에 `FeatureWaypoints` 필드를 추가해 `MainWindow.BuildCompareEntries()`가 `ResultRow.FeatureWaypoints`(자동설계가 기존경로에서 뽑아 쓴 그 특징점들, 실제 3D 좌표는 기존 폴리라인 위의 점)를 그대로 전달한다. `CompareRoutesWindow.DrawFeatureMarkers()`가 이 특징점들을 역할별 색상(메인 뷰어의 `FeatureRoleColor`와 동일 팔레트)의 반투명 큐브로 그리며, `RedrawExisting()`/`RedrawAuto()` 양쪽 모두에서 호출해 **기존경로 뷰·자동경로 뷰 모두에** 동일한 특징점 위치를 표시한다 — 자동설계가 기존 폴리라인의 어느 지점을 근거로 그 waypoint를 잡았는지 두 뷰에서 나란히 확인할 수 있다.

---

## 10. 2026-07-06 추가 개선 내용 (카메라 동기화, 파라미터 UI, 스코어링 고도화, 다중 파이프 정밀 검사)

### 10.1 비교 다이얼로그 카메라 동기화 (Sync Viewport) 기능 개발

- **개요**: 비교 다이얼로그에서 양쪽(기존설계 vs 자동설계) 뷰포트의 카메라 이동, 회전, 줌 상태를 실시간으로 동일하게 조작할 수 있도록 개선하여 UX 비교성을 대폭 높였습니다.
- **구현**:
  - [CompareRoutesWindow.xaml](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/CompareRoutesWindow.xaml) 좌측 목록 패널 상단에 `카메라 뷰포트 동기화` 체크박스 (`ChkSyncCamera`)를 장착했습니다 (기본 활성화).
  - 양쪽 `HelixViewport3D`(`ViewportExisting`, `ViewportAuto`)의 `CameraChanged` 이벤트를 바인딩했습니다.
  - [CompareRoutesWindow.xaml.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/CompareRoutesWindow.xaml.cs)에서 `_isSyncingCameras` 플래그 필드를 선언하여 양방향 동기화 호출 시 무한 재귀 루프에 빠지는 현상을 원천 방지하였습니다.
  - `CopyCamera` 헬퍼 메서드를 통해 카메라 위치, 줌, 조준 벡터, Up 벡터 등을 상대편 카메라로 수동 대입하여 정밀하고 안전하게 동기화합니다.

### 10.2 엔진 설정 파라미터 UI 제어 및 실시간 재라우팅 기능 개발

- **개요**: 하드코딩되었던 엔진 파라미터(안전 마진, 트레이 규격 등)를 뷰어 UI에서 바로 변경하고, 변경 시 현재 표시 중인 배관 경로들이 실시간으로 자동 재계산되어 3D 뷰에 반영되도록 개편했습니다.
- **구현**:
  - [MainWindow.xaml](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml)의 좌측 패널 레이아웃 적층 구조(`RowDefinitions`)를 `Auto` 기반으로 재조정하고 하드코딩 마진들을 걷어내어 유연성을 높였습니다.
  - `④ 라우팅 설정` Border 섹션을 신설하고 안전 마진, 트레이 폭/높이, 파이프 피치, 가닥 수, 곡률 반경 배수, 최대 수직 꺾임 수, 허용 오차 등 8개 텍스트박스 입력 창을 2열 그리드로 정렬 배치했습니다.
  - [MainWindow.xaml.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs)에서 최근 라우팅을 수행했던 태스크들과 영역 스코프를 유지하기 위해 `_lastRoutedTaskRows` 및 `_lastRoutedScope` 필드를 추가했습니다.
  - 각 텍스트박스의 `TextChanged`, `KeyDown` (Enter 키), `LostFocus` 이벤트를 감지하여 자동 재라우팅(`ReRouteCurrentAsync`)을 실행합니다.
  - **디바운싱(Debounce) 타이머**: 타이핑 도중 연산 누적으로 발생하는 렉을 방지하고자 `DispatcherTimer`(400ms 대기)를 이용해 텍스트 입력이 멈춘 후 400ms 뒤에 연산을 트리거하며, `Enter` 키 입력이나 포커스 이동 시에는 즉각 연산을 수행하도록 처리했습니다.
  - **파싱 오류 가드**: 입력 문자 오류로 인한 앱 크래시를 방지하기 위해 `ReadDouble`/`ReadInt` 메서드에 정합성 검사 및 안전한 기본값 폴백 처리를 적용했습니다.
  - **엔진 스위칭 동기화**: 하단의 `네이티브 C++ 엔진` 체크박스를 토글할 때도 즉시 바뀐 엔진으로 현재 배관 경로를 실시간 재계산하도록 연결했습니다.

### 10.3 특징점 매칭 및 스코어링 알고리즘 고도화

- **개요**: 자동설계 태스크가 DB에서 어떤 기존설계 경로를 참조할지 선정하는 매칭 스코어링 공식에 기하학적 유사도 인자들을 반영하여 정합성을 극대화하였습니다.
- **구현**:
  - [MainWindow.xaml.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs)의 `FindMatchedExistingRoute`를 **2단계 매칭(Strict-to-Loose Fallback) 프로세스**로 고도화했습니다.
    1. **1단계 (엄격 매칭)**: 동일 그룹 및 유틸리티명 내에서만 기존경로 매칭을 수립합니다.
    2. **2단계 (유연 매칭)**: 1단계 실패 시 그룹/유틸리티명이 일치하지 않더라도 스코어링 공식 하에 전체 경로 중 가장 가까운 최적 경로를 정합합니다. (명칭 오타나 표기법 오차 구제)
  - `ExistingRouteMatchScore` 함수에 다음 요소들을 반영하는 종합 스코어 수식을 장착했습니다.
    - **기초 거리**: 양 끝점 간의 유클리드 거리 차이 합 (정방향/역방향 중 최소값).
    - **고도(Z) 차이 페널티**: 시작/종단 Z 고도차를 추가 점수화 (`zGap * 0.5`).
    - **장비명 일치 보너스**: SourceName/TargetName 일치 시 각각 `-1000` 점의 큰 보너스 제공.
    - **관경(Diameter) 유사도**: 두 경로의 관경이 일치하면 `-300` 점 보너스, 불일치하면 오차 1mm당 `+5.0` 점의 페널티 가산.
    - **유틸리티 불일치 페널티**: 2단계 유연 매칭 시 그룹 다름은 `+3000` 점, 유틸리티 다름은 `+2000` 점 페널티를 부과해 동일 성격의 배관이 우선 매칭되도록 가중치를 정비했습니다.

### 10.4 다중 파이프(Multi-Pipe) 개별 튜브의 정밀 충돌 검증 로직 구현

- **개요**: 기존에 대표 중심선 1개 기준으로 수행하던 간섭 검사를 다중 분배된 각 개별 파이프(튜브) 라인이 독립 외경과 안전 마진을 만족하면서 실제로 충돌을 완벽히 우회하는지 개별 정밀 검증하도록 전면 개선했습니다.
- **구현**:
  - C# `RubberBandOptions` ([Models.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Engine/Models.cs)) 및 C++ P/Invoke `RbConfig` ([NativeInterop.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Engine/NativeInterop.cs), [rubberband_native.h](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.h)) 구조체에 실제 파이프 관경 정보 전달을 위해 `PipeDiameter`/`pipe_diameter` 필드를 새로 장착하였습니다.
  - **Managed 엔진(C#)**: `Validate` 호출 시 분산 생성된 개별 파이프 폴리라인들(`result.PipePaths`)을 전달받아, 각 파이프의 외경 반경(`options.PipeDiameter / 2.0`) 및 `options.SafetyMargin`으로 확장된 장애물 AABB 범위 침범 여부(`FindFirstPipeCollision`, `SegmentIntersectsPipeAabb`)를 정밀하게 전수 검사하도록 수정했습니다.
  - **Native 엔진(C++)**: [rubberband_native.cpp](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/cpp/RubberBandRouting.Native/rubberband_native.cpp)에서 기존의 대표 중심선 기준 간섭 판단(`has_residual_collision`)을 제거하고, `distribute()`에 의해 분산 생성된 `e->pipes` 내의 모든 개별 튜브 세그먼트들을 순회하며 각각 `segment_hits_pipe` 헬퍼로 장애물 AABB 충돌을 정밀 검증하도록 업데이트했습니다.
  - **WPF 뷰어**: [MainWindow.xaml.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs)의 `IsRoundedPathClear` 함수를 전면 개편하여, 꺾임부 라운딩 처리 안전성 판단(`roundSafe` = rounded vs sharp elbow) 시 `result.PipePaths`의 각 개별 배관 경로들에 `BuildRoundedBendPolyline`을 적용한 뒤 각각의 둥근 엘보 세그먼트가 관경 기준 확장 영역 `(diameter / 2.0) + options.SafetyMargin` 장애물과 간섭을 일으키는지 최종 정밀 검증합니다.

### 10.5 DB 접속 정보 영속화 강화 및 기동 시 자동 렌더링 개선

- **개요**: 프로젝트 로드 시 입력한 DB 접속 정보와 패스워드가 항상 즉시 저장되도록 세이브 시점을 확장하고, 기동 시 WPF 콤보박스의 바인딩 지연 등으로 인해 3D 렌더링이 되지 않고 멈추던 레이아웃 동기화 문제를 로컬 참조 기법으로 완벽하게 해결하였습니다.
- **구현**:
  - [MainWindow.xaml.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs)의 `LoadProjectsAsync`에서 WPF ComboBox `SelectedItem` 업데이트 지연 문제를 극복하기 위해 `selectedProject` 로컬 변수 참조 방식을 도입했습니다. 이를 통해 DB 조회 성공 시 WPF의 갱신 주기 지연과 관계없이 씬 자동 로딩 및 3D 렌더링이 즉각 트리거되도록 수정했습니다.
  - `LoadSceneAsync` 메서드가 성공적으로 완료된 시점에도 `SaveConnectionSettings()`를 수행하도록 하여, 사용자가 `기본설계` 버튼을 눌러 프로젝트 씬을 성공적으로 렌더링한 즉시 DB 연결 정보와 패스워드가 갱신 저장되도록 영속성 시점을 전면 보강했습니다.

### 10.6 유틸리티 및 태스크 선택 시 기존설계 특징점 자동 표시 및 정보 조회

- **개요**: 좌측 사이드바에서 유틸리티 그룹이나 유틸리티를 변경하여 태스크가 필터링되거나, 사용자가 개별 PoC 태스크를 클릭할 때 해당 태스크에 매칭된 기존설계 경로로부터 특징점(RouteFeature)들을 자동으로 추출하여 3D 화면에 마커 큐브로 그리고 특징점 정보를 즉시 출력하도록 개선했습니다.
- **구현**:
  - **특징점 자동 3D 표시**: [MainWindow.xaml.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs)의 `HighlightTaskEndpoints` 내부에서 `FindMatchedExistingRoute`로 기존설계 경로를 찾은 뒤, `BuildFeatureWaypoints`로 추출한 특징점들을 하이라이트 비주얼 버킷 `_selectedEndpointVisuals`에 반투명 색상 박스 마커로 그려줍니다.
  - **정보 및 속성 요약**: 태스크를 선택한 즉시 우측 하단 `분석결과` 창에 태스크의 기본 정보와 매칭 경로 ID, 특징점 총 개수, 특징점 상세 구성 정보(시작스텁, 꺾임, 고도변경 등의 카운트합)가 자동 갱신 표기됩니다.
  - **3D 마커 클릭 피킹 연동**: `TaskFeatureInfo` 레코드를 새로 정의하여 3D 특징점 마커들의 소유자로 연동하였으며, 사용자가 화면상의 특징점 큐브를 직접 클릭하면 `ShowTaskFeatureProperties` 함수가 실행되어 정확한 3D 위치 좌표와 속성이 분석창에 즉각 단독 출력됩니다.

### 10.7 세그먼트 단위별 구간 라우팅 세부 디버그 로그 파일 출력 기능 개발

- **개요**: 자동설계 엔진이 배관 경로를 시작점부터 중간 특징점들을 거쳐 종단점까지 찾아 나가는 상세 탐색 단계를 파일로그로 기록하여 역추적할 수 있게 하였습니다.
- **구현**:
  - **세그먼트별 Leg 트레이싱**: [ManagedRubberBandEngine.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs)의 A* 탐색 메서드들에 `StringBuilder` 로그 빌더를 추가하여 각 특징점 사이 구간(Leg)마다 출발/목적 좌표, 격자 수(X, Y, Z밀도), 주변의 장애물 충돌 수, A* 탐색 성공 시 팽창 수(Expansions) 또는 실패 시 직교 폴백 연결 발생 여부, 최종 생성된 선분들을 디테일하게 추적 기록하도록 고도화하였습니다.
  - **로그 영속화 및 리셋 연동**: 탐색이 끝난 직후 `d:\DINNO\DEV\AI-AutoRouting\TopKGen\Docs\RubberBandRouting_DebugTrace.log`에 로그 문자열을 작성(`AppendAllText`)하도록 처리했습니다. 뷰어의 `RouteRowsAsync` 시작 시 이전 로그를 리셋(Truncate)하여 매 세션 깔끔한 신규 로그만 보존합니다.
  - **UI 상태 표시 안내**: 관리형(C#) 엔진을 사용하여 설계를 구동할 경우 상태바 문구 끝부분에 `(디버그 로그가 Docs/RubberBandRouting_DebugTrace.log에 기록됨)` 알림 텍스트를 출력하여 접근성을 보장하였습니다.

### 10.8 디버그 로그 제어 CheckBox 및 1-클릭 디버그리플레이 버튼 추가

- **개요**: 로깅 기능을 직접 끄고 켤 수 있게 옵션을 분리하고, 결과 그리드 상에서 클릭 한 번으로 특정 경로만 디버그 시뮬레이션한 뒤 자동으로 해당 디버그 로그 텍스트 파일을 팝업 시켜주는 단일 리플레이 기능을 구현하였습니다.
- **구현**:
  - **WPF UI 컴포넌트 추가**: [MainWindow.xaml](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml)의 결과 테이블 상단 헤더 영역에 `디버그로그 생성` CheckBox(`ChkEnableDebugLog`, 기본 활성화)와 `디버그리플레이` Button(`BtnReplayDebug`, 단독 짙은 파랑색 테마)을 배치하였습니다.
  - **로깅 활성 제어 연동**: `RubberBandOptions`에 `EnableDebugLog` 제속 속성을 신설하고, WPF `ReadOptions()`에서 체크박스 값을 바인딩하여 엔진이 원하지 않을 때는 불필요한 IO 기록을 수행하지 않도록 개선했습니다.
  - **자동 메모장 연동 리플레이**: `BtnReplayDebug_Click` 핸들러를 구축하여, 사용자가 리스트의 특정 경로를 선택하고 클릭 시 자동으로 **관리형(C#) 엔진**으로 일시 타겟팅을 전환하고 단일 경로를 재탐색하여 3D 씬을 갱신합니다. 계산이 끝난 즉시 `Process.Start("notepad.exe", logPath)`를 호출하여 **상세 디버그 로그 텍스트를 메모장 팝업창으로 바로 출력**해 주어 극대화된 사용성 편의를 제공하였습니다.

### 10.9 충돌(간섭) 발생 지점 3D 반투명 빨간 구체 시각화 및 좌표 목록 제공

- **개요**: 자동설계 경로가 장애물과 충돌(간섭)을 유발하는 정확한 위치 정보를 3D 공간 마킹 및 분석 정보 리스팅 방식으로 제공하여 디버깅 직관성을 강화하였습니다.
- **구현**:
  - **충돌점 기하 연산**: [ManagedRubberBandEngine.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs)의 충돌 검증 로직을 확장하여 `AABB` 영역과 배관 세그먼트 선분의 간섭이 일어난 정확한 투영 최단 점(`ClosestPointOnSegment`)을 추출하여 `result.CollisionPoints` 컬렉션에 누적 반환하도록 고도화했습니다.
  - **엔진 단 교차 매핑**: 네이티브 C++ 엔진 구동 시에도 충돌점 좌표를 추출할 수 있도록 [MainWindow.xaml.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs)의 `ComputeRoutes`에서 C#의 `PopulateCollisionPoints` 검증 유틸을 교차 구동해 충돌 리스트를 완성해 줍니다.
  - **3D 씬 내 빨간 구체 렌더링**: 결과 테이블에서 검증 실패(`residual_collision`) 상태인 경로를 선택하면, 충돌점 좌표에 **굵은 반투명 빨간색 구체(Sphere, 반경: 지름의 1.5배 이상)**들을 마커로 추가하여 3D 공간에 그려줍니다. 선택을 전환하면 마커들은 자동으로 소거됩니다.
  - **분석결과 좌표 리스팅**: `BuildAnalysisRows`를 업데이트하여, 우측 하단의 분석 결과 리스트에 `충돌 개수: N개` 정보와 개별 충돌점들의 3D X/Y/Z 좌표를 테이블 행으로 포맷팅하여 바인딩 출력합니다.

### 10.10 A* 탐색 실패 및 수직 Bend 한도 초과 오류 위치 3D 하이라이트 및 좌표 제공

- **개요**: 장애물 간섭 외에 주요 설계 실패 원인인 **A* 길찾기 실패 구간(Orange)**과 **수직 꺾임 한도 초과 엘보 지점(Magenta)** 또한 3D 공간 표시와 데이터 텍스트 정보로 구별하여 정밀 분석할 수 있도록 구현했습니다.
- **구현**:
  - **오류 정보 수집 확장**: A* 탐색 실패로 강제 직교 연결이 발생한 경유 Leg 구간은 `result.FallbackLegs`에, 수직 상하 단차 전환이 일어난 엘보 꼭짓점들은 `result.VerticalBendPoints`에 엔진 단에서 수집되도록 설계했습니다. 네이티브 엔진의 출력물도 WPF `ComputeRoutes`에서 `FindVerticalBends` C# 모듈이 엘보 좌표를 동일하게 채우도록 하였습니다.
  - **A* 실패 구간 시각화**: A* 탐색 실패 구간을 3D 공간 상에 **두꺼운 반투명 주황색 오버레이 튜브(Color: Argb(180, 255, 128, 0))** 및 양 끝점의 구체 마커로 강조하여, 어느 경유 특징점 사이가 차단되었는지 즉각 특정합니다.
  - **수직 꺾임 초과 시각화**: 수직 꺾임 한도가 초과된 경우, 높이가 변화하는 모서리 엘보 위치에 **반투명 보라색(자주색) 구체(Color: Argb(160, 255, 0, 255))** 마커를 장착해 과도하게 높은 수직 기동이 발생한 지점을 가시화합니다.
  - **상세 분석 리스팅**: `BuildAnalysisRows`에 바인딩하여 `A* 실패 구간 수` 및 개별 실패구간 범위 좌표 정보와 `수직 Bend 위치 수` 및 각 수직 꺾임 엘보 좌표들을 분석결과 테이블 그리드에 깔끔하게 노출해 줍니다.

### 10.11 [설계오류리스트] 탭 추가 및 클릭 시 3D 카메라 줌인/하이라이트 피킹 인터랙션 구현

- **개요**: 검증 오류 목록을 통합 조회할 수 있는 독립 탭을 추가하고, 행 클릭 시 3D 씬 카메라를 해당 오류의 중앙 좌표로 포커싱 이동하며 밝은 노란색으로 점멸 조준하는 인터랙션을 장착했습니다.
- **구현**:
  - **WPF UI 탭 신설**: [MainWindow.xaml](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml)의 우측 하단 탭컨트롤 내에 `설계오류리스트` TabItem과 DataGrid(`GridErrorList`)를 추가했습니다.
  - **데이터 추출 바인딩**: [MainWindow.xaml.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs)에 `ErrorDetailRow` 레코드를 추가하고, 선택된 배관 결과의 `CollisionPoints`, `FallbackLegs`, `VerticalBendPoints` 정보를 파싱하여 "장애물 충돌", "A* 탐색 실패", "수직 꺾임 초과" 행 데이터를 실시간 빌드해 바인딩합니다.
  - **클릭 피킹 카메라 줌 & 밝은 노란색 3D 하이라이트**: 그리드에서 임의의 오류 행을 선택 시 `GridErrorList_SelectionChanged` 이벤트가 트리거되어, 선택된 간섭 꼭짓점 또는 실패 구간 세그먼트를 3D 뷰포트 내에 **밝은 노란색(Yellow) 굵은 실선과 구체**로 하이라이트 렌더링하고, **카메라 시점(`pc.Position` 및 `pc.LookDirection`)을 해당 기하 구조체의 중심 좌표로 자동 부드럽게 줌인 포커싱**하여 사용성을 극대화했습니다.

### 10.12 경로결과 그리드에 꺽임수량 열 추가 및 수직 꺽임 최대 수량 99개 기본값 상향

- **개요**: 결과 경로 목록에서 각 배관의 수직 방향 꺾임 횟수를 빠르게 모니터링할 수 있도록 제공하고, 엔진 단에서 수직 꺾임 발생 시 과도한 차단 에러를 예방하기 위해 한도 기본값을 `99`개로 변경하였습니다.
- **구현**:
  - **꺽임수량 컬럼 추가**: [MainWindow.xaml](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml)의 우측 상단 결과 DataGrid(`GridResults`) 컬럼 구성에 `길이` 뒤에 `꺽임수량` 컬럼(`<DataGridTextColumn Header="꺽임수량" Binding="{Binding VerticalBends}" Width="70"/>`)을 신설 연동하였습니다.
  - **최대 꺽임 한도 상향 (99개)**: [Models.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Engine/Models.cs)의 `MaxVerticalBends` 기본 프로퍼티를 `5`에서 `99`로 수정하였습니다. 또한 사이드바의 설정 입력창(`TxtMaxVerticalBends`의 default `Text="99"`) 및 `MainWindow.xaml.cs`의 `ReadOptions()`의 대체용 기본 fallback 값 또한 `99`로 통일 상향 갱신하였습니다.

### 10.13 카메라 배관 초정밀 근접 시 렌더링 누출 해결 (Near/Far clipping plane 조정)

- **개요**: 사용자가 3D 뷰포트 상에서 특정 배관의 상세 관통 부위나 엘보 단면을 밀착하여(초근접) 살펴볼 때, 카메라의 전면/후면 자름 평면(Near/Far Clipping Planes) 설정 한계로 인해 배관 기하 구조가 구멍 뚫린 것처럼 잘려나가던(Clipping) 현상을 해결하였습니다.
- **구현**:
  - **안전 근접 거리 설정**: [MainWindow.xaml.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs) 및 [CompareRoutesWindow.xaml.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/RubberBandRoutingSuite/src/RubberBandRouting.Viewer/CompareRoutesWindow.xaml.cs)에서 카메라 자동 포커싱(Zoom-to-Fit) 연산 직후 주입되던 동적 NearPlane 값을 상수 스펙인 `NearPlaneDistance = 10.0` (10mm 이내만 잘림)으로 통일 수정하였습니다.
  - **최대 가시 렌더 범위 확장**: FarPlane 값을 충분한 가시 깊이를 보장하는 `FarPlaneDistance = 10000000.0` (10km)으로 확장 지정하여, 렌더 타겟에 밀착하거나 뒤로 멀리 물러났을 때 어떠한 상황에서도 렌더링이 유실되지 않도록 가시 성능의 안정성을 완벽히 확보하였습니다.




