# Top-K 특징점 7종 — TopKSearchStandalone 함수 사용법 & TopK.3DViewer 연동 정리

이 문서는 `[특징점생성]`으로 만든 7개 특징점/패턴 DB를 실제로 **검색·조회**할 때 쓰는
`TopKSearchStandalone` 쪽 함수와, `TopK.3DViewer`가 그 함수를 어디서 어떻게 호출해 화면에
붙이는지를 정리한다. 각 테이블의 추출 로직·소스코드·중복 여부는
`Docs/FeaturePattern_Pipeline_Overlap_Review.md`를 참고하고, 이 문서는 **사용법(API
레퍼런스)**에 집중한다.

## 0. 공통 사항

- 모든 검색 함수는 `RoutingAI.Standalone` 네임스페이스(`TopKSearchStandalone` 프로젝트)의
  `static class`에 있고, 전부 `async Task` 시그니처다.
- `TopK.3DViewer.csproj`는 `TopKSearchStandalone.csproj`를 `<ProjectReference>`로 직접
  참조한다(`TopK.3DViewer.csproj:20`) — subprocess 호출이 아니라 C# 메서드를 그대로 호출한다.
  (7개 생성기 Python 스크립트를 실행하는 `[특징점생성]` 다이얼로그만 별도로 subprocess를 쓴다.)
- 접속 정보는 어디서나 동일한 `DbConfig` 레코드(`TopKSearchStandalone.cs:286`)를 쓴다:
  ```csharp
  var db = new DbConfig(Host: "localhost", Port: 5432, Database: "DDW_AI_DB",
                         User: "dinno", Password: "dinno");
  // TopK.3DViewer에서는 _settings.ToDbConfig()로 UI 입력값을 그대로 변환해서 넘긴다.
  ```
- 각 함수는 내부에서 `NpgsqlConnection`을 직접 열고 닫으므로 호출자가 커넥션을 관리할 필요가
  없다. 예외는 그대로 던져지므로(`InvalidOperationException`, `Npgsql.PostgresException` 등)
  `TopK.3DViewer`는 공통 헬퍼 `RunBusyAsync(status, action)`으로 감싸 실패 시 메시지박스를
  띄운다(`MainWindow.xaml.cs`).
- 결과가 없거나 테이블이 아직 `create-schema`/`build`되지 않은 경우(`PostgresException`
  `SqlState=="42P01"`), **오버레이용 조회 함수**(Bend Feature Point, Path Segmentation)는
  조용히 빈 목록을 반환한다. 반면 **검색의 1차 대상 함수**(아래 4~7번)는 예외를 던진다 — 검색
  쿼리가 존재하지 않는 테이블을 대상으로 "성공"할 수는 없기 때문이다.

---

## 1~2. 30D 특징벡터 + Context Vector — route 단위 Top-K 검색

두 테이블은 항상 하나의 함수로 같이 쓰인다. Context Vector는 독립적으로 검색할 수 없고,
30D 특징벡터 ANN 후보가 뽑힌 **뒤** 재정렬에만 관여한다(`Docs/FeaturePattern_Pipeline_Overlap_Review.md`
3.1절).

### `TopKSearchStandalone.SearchAsync` (`TopKSearchStandalone.cs:598`)

```csharp
public static async Task<(List<SearchResult> Results, SearchMeta Meta)> SearchAsync(
    DbConfig db,
    string processName,
    string equipmentName,
    string utilityGroup,
    string utility,
    (double X, double Y, double Z) startXyz,
    (double X, double Y, double Z) endXyz,
    int k = 5,
    string size = "",
    string queryPattern = "",
    bool useObstacleContext = false,
    string bay = "",
    string projectScopeKey = "",
    string modelRevisionKey = "",
    bool allowGlobalContextFallback = false,
    RerankWeights? rerankWeights = null,
    bool redistributeMissingPatternWeight = false)
```

| 파라미터 | 설명 |
|---|---|
| `processName`/`equipmentName`/`utilityGroup`/`utility` | 빈 문자열이면 해당 WHERE 절 생략(필터 안 함) |
| `startXyz`/`endXyz` | 검색 쿼리의 시작/종점 좌표(mm) — 30D 벡터의 dims[0:12]를 이 좌표로 즉석 계산 |
| `k` | 반환할 Top-K 수 (≥1) |
| `size` | 배관 구경 필터(선택) |
| `queryPattern` | `"H-R-H"` 같은 방향 패턴 힌트(선택, 주면 구조 유사도 항목이 활성화됨) |
| `useObstacleContext` | **true**면 `TB_ROUTE_CONTEXT_VECTOR`를 재정렬 4번째 항목(`ctxScore`, 가중치 0.10)에 반영. 매칭되는 Context Vector가 없는 후보는 baseline 3항목으로 자동 fallback |
| `projectScopeKey`/`modelRevisionKey` | 둘 다 비우면 `TB_ROUTE_SOURCE_SCOPE_MANIFEST`의 ACTIVE를 자동 조회 |
| `rerankWeights` | `RerankWeights(Position, Pattern, Vector, Context)` — 생략 시 baseline(0.50/0.30/0.20) 또는 컨텍스트 포함(0.45/0.27/0.18/0.10) |

**반환값**: `SearchResult`(`TopKSearchStandalone.cs:327`) 목록 — `Rank`, `RoutePathGuid`,
`SimilarityScore`, `ScorePosition`/`ScorePattern`/`ScoreVector`/`ScoreContext`,
`DirectionPattern`, `StartXyz`/`EndXyz` 등. `SearchMeta`(`:348`)는 `SearchTimeMs`,
`ContextCoverage`, `UsedObstacleContext` 등 진단 정보.

```csharp
var (results, meta) = await TopKSearchStandalone.SearchAsync(
    db: db, processName: "CVD", equipmentName: "TNMHJ04",
    utilityGroup: "VACCUM", utility: "FORELINE",
    startXyz: (1000, 2000, 3000), endXyz: (7000, 9000, 4500),
    k: 5, useObstacleContext: true);
foreach (var r in results)
    Console.WriteLine($"#{r.Rank} {r.RoutePathGuid} score={r.SimilarityScore:F4}");
```

### `TopKSearchStandalone.FetchPresetsAsync` (`:1070`)

기존 `TB_ROUTE_PATH` 한 건을 검색조건 프리셋으로 가져온다(사용자가 좌표를 직접 타이핑하지
않도록).

```csharp
public static async Task<List<RoutePreset>> FetchPresetsAsync(
    DbConfig db, string processName = "", string equipmentName = "",
    string utilityGroup = "", string utility = "", string size = "", int limit = 50)
```

### TopK.3DViewer 연동

- **모드**: `RdoIndividualMode`(라디오, `MainWindow.xaml:73`) — 기본 선택 모드.
- **프리셋**: `CmbPreset` → `Preset_SelectionChanged`(`MainWindow.xaml.cs`)가
  `_database.LoadPresetsAsync()`(`ViewerDatabaseService.cs`, 내부적으로 `TB_ROUTE_PATH`를
  직접 조회 — `TopKSearchStandalone.FetchPresetsAsync`와 별개의 UI 전용 구현)로 채운
  콤보박스에서 좌표/필터를 자동 채운다.
- **검색 실행**: `BtnSearch` → `Search_Click`이 위 `SearchAsync`를 호출 →
  `_database.LoadRoutePointsBatchAsync(guids)`로 실제 3D 폴리라인을 일괄 로드 →
  `ObservableCollection<TopKRouteItem> _routes`에 담아 `GridResults` DataGrid에 바인딩.
- **렌더링**: `GridResults` 선택 변경 → `Results_SelectionChanged` → `RenderRoutes()`가
  선택된 route는 굵은 파이프(`AddPipePath`, `RouteLayer`), 나머지 후보는 얇은 선으로 그린다.
- **시작/종단 PoC 표시**: `Search_Click`이 `_database.LoadRouteEndpointsBatchAsync(guids)`
  (`ViewerDatabaseService.cs`)를 함께 호출해 각 `TopKRouteItem`의 `TargetOwnerName`/
  `TargetKind`를 채운다. `GridResults`의 "시작 장비" 열은 기존 `Equipment`(=
  `TB_ROUTE_PATH.EQUIPMENT_NAME`, 시작 PoC의 메인장비)를, 신규 "종단 객체" 열은
  `TargetDisplay`(예: `"DUCT_AA_01 (덕트)"`)를 바인딩한다. 종단 객체 분류는
  `TARGET_OWNER_NAME`을 `TB_EQUIPMENTS`(`MAIN_SUB_TYPE="MainTool"`이면 메인장비, 아니면
  부대장비)/`TB_DUCT`(덕트)/`TB_LATERAL_PIPE`(레터럴배관)와 대조해 결정한다
  (`AutoRouteFinder/Models/ObstacleDbLoader.cs`의 분류 규칙과 동일). `TxtDetails`
  (`UpdateSelectedRouteDetails`)에도 "종단 PoC 객체" 줄로 함께 표시된다.

---

## 3. Path Segmentation — 검색 대상 아님, 선택 route 오버레이

`TB_ROUTE_PATH_SEGMENTATION`은 벡터가 없는 순수 지오메트리라 Top-K 검색이 성립하지 않는다.
대신 **현재 화면에 표시 중인 route**에 Start/Middle/End Stub 구간을 겹쳐 그리는 용도로만
쓴다.

### `ViewerDatabaseService.LoadPathSegmentationBatchAsync`

```csharp
public async Task<IReadOnlyList<PathSegmentGeometry>> LoadPathSegmentationBatchAsync(
    IEnumerable<string> routeGuids)
```

`PathSegmentGeometry(RouteGuid, StartStub, MiddleTrunk, EndStub)` — 각 필드는 이미
`Point3D` 리스트로 파싱되어 있다(SQL에서 `ST_DumpPoints`로 지오메트리를 풀어 오므로 호출자가
WKT를 직접 파싱할 필요가 없다). 테이블이 없으면(`42P01`) 빈 목록.

```csharp
var db = new ViewerDatabaseService(connectionString);
var segments = await db.LoadPathSegmentationBatchAsync(["<ROUTE_PATH_GUID>"]);
foreach (var seg in segments)
    Console.WriteLine($"{seg.RouteGuid}: start={seg.StartStub.Count}pt, " +
                       $"trunk={seg.MiddleTrunk.Count}pt, end={seg.EndStub.Count}pt");
```

### TopK.3DViewer 연동

- **체크박스**: `ChkShowPathSegments`("구간분할 표시", `MainWindow.xaml`) — 기본 꺼짐.
- **토글 시**: `PathSegmentToggle_Changed` → `LoadAndRenderPathSegmentsAsync()`
  (`MainWindow.xaml.cs`)가 `GetActiveRouteGuids()`(현재 선택된 route 또는 그룹 멤버 GUID
  목록 — 개별/그룹 모드 공용 헬퍼)로 대상을 정하고, 3색으로 `PathSegmentLayer`에 그린다:
  **노랑=Start Stub, 흰색=Middle Trunk, 청록=End Stub**.
- 검색 결과 선택이 바뀔 때마다(`Results_SelectionChanged`, `GroupResults_SelectionChanged`,
  `Search_Click`, `Preset_SelectionChanged`, `GroupPreset_SelectionChanged`) 체크박스가
  켜져 있으면 자동 재조회한다.
- **다발배관 패턴/Stub 패턴 모드에서는 아무것도 그리지 않는다** — `GetActiveRouteGuids()`가
  그 두 모드에서는 빈 목록을 돌려주기 때문(그 모드는 "route 선택"이라는 개념 자체가 없음).

---

## 4. Group/Bundle Pattern(다발배관) — `GroupPatternSearch`

### 프리셋 조회 — `FetchPresetsAsync`

```csharp
public static async Task<IReadOnlyList<GroupPatternPreset>> FetchPresetsAsync(
    DbConfig db, string equipmentTag = "", string utilityGroup = "",
    string utility = "", int limit = 500)
```

### 단건 조회(쿼리로 쓸 패턴 로드) — `LoadAsync`

```csharp
public static async Task<GroupPatternDescriptor?> LoadAsync(DbConfig db, string groupId)
```

트렁크 지오메트리(`TrunkLines`)까지 채워서 반환한다 — 3D 렌더링 바로 가능.

### 유사도 검색 — `SearchAsync`

```csharp
public static async Task<(IReadOnlyList<GroupPatternSearchResult> Results,
    GroupPatternSearchMeta Meta)> SearchAsync(
    DbConfig db, string queryGroupId, GroupPatternSearchOptions? options = null)
```

`GroupPatternSearchOptions`: `K`(기본 10), `CandidateFetchMultiplier/Minimum/Maximum`,
`RequireSameUtility`(기본 true — 같은 `UTILITY`까지 일치해야 후보로 봄).

내부 동작: `queryGroupId` 행의 `FEAT`(vector(60), L2)를 쿼리 벡터로 삼아 같은
`EQUIPMENT_TAG`/`UTILITY_GROUP`(옵션 `UTILITY`) 안에서 `ORDER BY "FEAT" <-> @vec`로 후보를
뽑고, `N_MEMBERS` 차이가 클수록 `1/(1+0.15×|Δmembers|)`로 소폭 감점해 `Similarity`를
계산한다. UtilityPipeGroupSearch와 달리 **멤버 단위 Hungarian 매칭이 없다** — 한 행이 이미
완결된 형상 요약이라 route끼리 짝지을 필요가 없기 때문.

```csharp
var presets = await GroupPatternSearch.FetchPresetsAsync(db, equipmentTag: "TNMHJ04");
var query = await GroupPatternSearch.LoadAsync(db, presets[0].GroupId);
var (results, meta) = await GroupPatternSearch.SearchAsync(
    db, query!.GroupId, new GroupPatternSearchOptions { K = 5 });
foreach (var r in results)
    Console.WriteLine($"#{r.Rank} {r.Candidate.GroupId} sim={r.Similarity:F4} " +
                       $"pitch={r.Candidate.PitchMm:F0}mm {r.Candidate.OffsetAxis}");
```

### TopK.3DViewer 연동

- **모드**: `RdoGroupPatternMode`("다발배관 패턴").
- **프리셋**: `CmbGroupPatternPreset` → `GroupPatternPreset_SelectionChanged`가
  `GroupPatternSearch.LoadAsync`로 쿼리 패턴을 로드하고 `PresetRouteLayer`에 트렁크 라인을
  파란색으로 미리 표시.
- **검색**: `BtnSearch` → `Search_Click` → `SearchGroupPatternsAsync()` →
  `GroupPatternSearch.SearchAsync` 호출 → `ObservableCollection<GroupPatternResultItem>
  _groupPatternResults` → `GridGroupPatternResults`.
- **렌더링**: `GridGroupPatternResults` 선택 변경 → `GroupPatternResults_SelectionChanged`가
  쿼리(파란색, `PresetRouteLayer`)와 선택 후보(초록색, `RouteLayer`)의 `TrunkLines`를 각각
  `AddPipePath`로 겹쳐 그린다.

---

## 5. Stub Pattern — `StubPatternSearch`

### 프리셋 조회 — `FetchPresetsAsync`

```csharp
public static async Task<IReadOnlyList<StubPatternPreset>> FetchPresetsAsync(
    DbConfig db, string stubKind = "", string mainEquipmentName = "",
    string utilityGroup = "", string utility = "", int limit = 500)
```

`stubKind`는 `"START"`/`"END"`.

### 단건 조회 — `LoadAsync`

```csharp
public static async Task<StubPatternDescriptor?> LoadAsync(DbConfig db, string patternId)
```

### 유사도 검색 — `SearchAsync`

```csharp
public static async Task<(IReadOnlyList<StubPatternSearchResult> Results,
    StubPatternSearchMeta Meta)> SearchAsync(
    DbConfig db, string queryPatternId, StubPatternSearchOptions? options = null)
```

`StubPatternSearchOptions`: `K`(기본 10), `CandidateFetchMultiplier/Minimum/Maximum`,
`FeatureWeight`(기본 0.7), `DirectionWeight`(기본 0.3).

내부 동작: 같은 `STUB_KIND`/`ANCHOR_KIND` 안에서 `FEAT`(vector(24), L2) ANN으로 후보를 넓게
뽑은 뒤, `DIR_UNIT`(vector(3)) 코사인유사도를 애플리케이션 코드에서 계산해 가중 결합
(`Similarity = FeatureWeight×featureSim + DirectionWeight×directionSim`)한다 — route
Top-K가 1차 ANN과 재정렬을 분리하는 것과 같은 설계다.

```csharp
var presets = await StubPatternSearch.FetchPresetsAsync(db, stubKind: "START");
var query = await StubPatternSearch.LoadAsync(db, presets[0].PatternId);
var (results, meta) = await StubPatternSearch.SearchAsync(db, query!.PatternId);
foreach (var r in results)
    Console.WriteLine($"#{r.Rank} {r.Candidate.PatternId} sim={r.Similarity:F4} " +
                       $"(feat={r.FeatureSimilarity:F3}, dir={r.DirectionSimilarity:F3}) " +
                       $"face={r.Candidate.Face} dirSeq={r.Candidate.DirSeq}");
```

### TopK.3DViewer 연동

- **모드**: `RdoStubPatternMode`("Stub 패턴").
- **프리셋**: `CmbStubPatternPreset` → `StubPatternPreset_SelectionChanged`가
  `StubPatternSearch.LoadAsync`로 로드 → `RenderStubPatternQuery()`가 stub 점열(파란색
  `AddPipePath`)과 anchor AABB(하늘색 와이어프레임, `AddWireBox`)를 `PresetRouteLayer`에
  그린다.
- **검색**: `Search_Click` → `SearchStubPatternsAsync()` →
  `ObservableCollection<StubPatternResultItem> _stubPatternResults` →
  `GridStubPatternResults`.
- **렌더링**: `StubPatternResults_SelectionChanged`가 선택 후보의 stub 점열(초록색)과
  anchor AABB(연두 와이어프레임)를 `RouteLayer`에 겹쳐 그린다.

---

## 6. Utility Pipe Group Vector — `UtilityPipeGroupSearch`

### 그룹 1건 로드 — `LoadGroupAsync` (`UtilityPipeGroupSearch.cs:37`)

```csharp
public static async Task<UtilityPipeGroupDescriptor?> LoadGroupAsync(
    DbConfig db, string groupVectorId)
```

멤버(각 route의 30D/Context 벡터, 꺾임점 요약 포함)까지 전부 채워서 반환.

### 프리셋 조회 — `FetchPresetsAsync` (`:106`)

```csharp
public static async Task<IReadOnlyList<UtilityPipeGroupPreset>> FetchPresetsAsync(
    DbConfig db, string processName = "", string equipmentInstanceKey = "",
    string utilityGroup = "", string utility = "", int limit = 500)
```

### 유사도 검색 — `SearchAsync` / `SearchByIdentityAsync` (`:52`, `:64`)

```csharp
public static async Task<(IReadOnlyList<UtilityPipeGroupSearchResult> Results,
    UtilityPipeGroupSearchMeta Meta)> SearchAsync(
    DbConfig db, string queryGroupId, UtilityPipeGroupSearchOptions? options = null)

public static async Task<(IReadOnlyList<UtilityPipeGroupSearchResult> Results,
    UtilityPipeGroupSearchMeta Meta)> SearchByIdentityAsync(
    DbConfig db, string processName, string equipmentInstanceKey,
    string utilityGroup, string utility, UtilityPipeGroupSearchOptions? options = null,
    string projectScopeKey = "", string modelRevisionKey = "")
```

`UtilityPipeGroupSearchOptions`: `K`, `SizeMatchMode`(`PreferExact`/`ExactOnly`/`Ignore`),
`PairWeights`(`RerankWeights` — 멤버 Pair 점수의 Position/Pattern/Feature/Context 가중치),
`MatchedWeight`(기본 0.80)/`ArrangementWeight`(기본 0.20), `RequireSameProcess`,
`EquipmentFamilyKey`.

내부 동작(2.2절): `FEATURE_CENTROID` ANN으로 후보 그룹을 뽑고, 각 그룹의 멤버를 Query 멤버와
**Hungarian 알고리즘**(`UtilityPipeGroupMatcher.ScoreGroup`)으로 1:1 매칭 — 매칭 시
`TB_ROUTE_BEND_FEATURE_POINT`가 있으면 원인(CAUSE) 일치도를 Pair 점수에 추가 반영(있으면
강화, 없으면 자동 생략).

```csharp
var presets = await UtilityPipeGroupSearch.FetchPresetsAsync(db, utilityGroup: "VACCUM");
var query = await UtilityPipeGroupSearch.LoadGroupAsync(db, presets[0].GroupVectorId);
var (results, meta) = await UtilityPipeGroupSearch.SearchAsync(
    db, query!.GroupVectorId, new UtilityPipeGroupSearchOptions { K = 5 });
foreach (var r in results)
    Console.WriteLine($"#{r.Rank} {r.Candidate.GroupVectorId} sim={r.GroupSimilarity:F4} " +
                       $"matched={r.Matches.Count}/{r.Candidate.MemberCount}");
```

### TopK.3DViewer 연동

- **모드**: `RdoGroupMode`("Utility 배관 그룹").
- **프리셋**: `CmbGroupPreset` → `GroupPreset_SelectionChanged` → `LoadGroupAsync` → 멤버를
  `_database.LoadRoutePointsBatchAsync`로 실제 3D 폴리라인 로드(없으면
  `BuildMemberFallbackPolyline`로 직선 재구성) → `GridGroupMembers`에 멤버 목록 표시.
- **검색**: `Search_Click` → `SearchGroupsAsync()` → `UtilityPipeGroupSearch.SearchAsync` →
  `ObservableCollection<TopKGroupItem> _groupRoutes` → `GridGroupResults`.
- **렌더링**: `GroupResults_SelectionChanged` → `RenderGroupComparison()` — 매칭된 Query/
  Candidate 멤버 쌍을 같은 색으로, 크기 불일치는 노랑 오버레이, 미매칭 멤버는 빨강(Query)/
  주황(Candidate)으로 표시(`ChkShowUnmatchedGroupMembers`).
- **시작/종단 PoC 표시**: `SearchGroupsAsync()`가 후보 그룹 전체 멤버의 route guid를 모아
  `_database.LoadRouteEndpointsBatchAsync(guids)` 한 번으로 일괄 조회하고, 그 결과를
  `TopKGroupItem.EndpointsByRouteGuid`에 담는다. `GridGroupResults`의 "시작 장비" 열은
  `Candidate.EquipmentInstanceKey`(그룹 전체가 공유하는 시작 PoC 메인장비)를, 신규
  "종단 객체" 열은 `TargetSummary`를 바인딩한다 — 그룹 멤버들의 종단 객체 이름이 모두
  같으면 `"이름 (종류)"`, 섞여 있으면(예: 일부는 덕트, 일부는 레터럴배관) `"덕트 2 · 레터럴배관 1"`
  처럼 종류별 건수로 요약한다. 종단 객체 분류 규칙은 §1~2와 동일(`TB_EQUIPMENTS`/
  `TB_DUCT`/`TB_LATERAL_PIPE` 대조). `TxtDetails`(`UpdateSelectedGroupDetails`)에도
  "종단 PoC 객체" 줄로 표시된다.

---

## 7. Bend Feature Point — 검색 자체 대상 아님(간접 강화) + 선택 route 오버레이

`TB_ROUTE_BEND_FEATURE_POINT`는 **독립적으로 검색되지 않는다.** 두 가지 방식으로만 쓰인다.

### (a) Utility Pipe Group 검색의 보조 강화 신호 (자동, 별도 호출 불필요)

`UtilityPipeGroupSearch` 내부 비공개 함수 `FetchBendPointsAsync`
(`UtilityPipeGroupSearch.cs:321`)가 그룹 멤버 route들의 꺾임점을 자동으로 조회해
`UtilityPipeGroupMatcher.ScorePair`의 원인(CAUSE) 일치도 보너스에 반영한다. 테이블이 없으면
(`42P01`) 조용히 건너뛰고 구조 패턴 50:50 폴백으로 동작 — **6번 `SearchAsync`를 호출하면
자동으로 적용되며, 별도 API 호출이 필요 없다.**

### (b) 선택 route 오버레이 — `ViewerDatabaseService.LoadBendFeaturePointsAsync`

```csharp
public async Task<IReadOnlyList<BendFeatureMarker>> LoadBendFeaturePointsAsync(
    IEnumerable<string> routeGuids)
```

`BendFeatureMarker(RouteGuid, Cause, TransitionType, SegmentZone, Point)` 목록 반환. 테이블
없으면(`42P01`) 빈 목록.

```csharp
var markers = await db.LoadBendFeaturePointsAsync(["<ROUTE_PATH_GUID>"]);
foreach (var m in markers)
    Console.WriteLine($"{m.Cause} @ {m.Point} ({m.SegmentZone}/{m.TransitionType})");
```

### TopK.3DViewer 연동

- **체크박스**: `ChkShowBendFeatures`("꺾임원인 표시") — 기본 켜짐.
- **토글/선택 변경 시**: `BendFeatureToggle_Changed`/각 `*_SelectionChanged` →
  `LoadAndRenderBendFeaturesAsync()`가 `GetActiveRouteGuids()` 대상을 조회해
  `BendFeatureLayer`에 `SphereVisual3D`로 그린다. 색상은 `BendCauseColor`:
  주황=`OBSTACLE_AVOID`, 초록=`DESTINATION_ENTRY`, 파랑=`ZONE_CONSTRAINT`,
  보라=`GROUP_ALIGNMENT`, 회색=미분류.
- Path Segmentation 오버레이(3절)와 동일한 `GetActiveRouteGuids()` 패턴을 공유하므로, 다발배관
  패턴/Stub 패턴 모드에서는 마찬가지로 아무것도 그려지지 않는다.

---

## 8. 빠른 참조 표

| # | 테이블 | 검색 함수(TopKSearchStandalone) | 프리셋/로드 함수 | TopK.3DViewer 모드 |
|---|---|---|---|---|
| 1 | `TB_ROUTE_FEATURE_VECTOR` | `TopKSearchStandalone.SearchAsync` | `FetchPresetsAsync` | 개별 배관 |
| 2 | `TB_ROUTE_CONTEXT_VECTOR` | (1번의 `useObstacleContext=true`) | — | 개별 배관 |
| 3 | `TB_ROUTE_PATH_SEGMENTATION` | 없음(검색 불가) | `ViewerDatabaseService.LoadPathSegmentationBatchAsync` | 전 모드 공통 오버레이(`ChkShowPathSegments`) |
| 4 | `TB_ROUTE_GROUP_PATTERN` | `GroupPatternSearch.SearchAsync` | `FetchPresetsAsync`/`LoadAsync` | 다발배관 패턴 |
| 5 | `TB_ROUTE_STUB_PATTERN` | `StubPatternSearch.SearchAsync` | `FetchPresetsAsync`/`LoadAsync` | Stub 패턴 |
| 6 | `TB_ROUTE_UTILITY_GROUP_VECTOR` | `UtilityPipeGroupSearch.SearchAsync`/`SearchByIdentityAsync` | `FetchPresetsAsync`/`LoadGroupAsync` | Utility 배관 그룹 |
| 7 | `TB_ROUTE_BEND_FEATURE_POINT` | 없음(6번 내부 자동 강화) | `ViewerDatabaseService.LoadBendFeaturePointsAsync` | 전 모드 공통 오버레이(`ChkShowBendFeatures`) |

7개 특징점 테이블은 아니지만, 검색 결과 자체에 항상 곁들여지는 보조 조회 함수 하나가 더 있다.

| 함수 | 조회 대상 | 용도 | 적용 모드 |
|---|---|---|---|
| `ViewerDatabaseService.LoadRouteEndpointsBatchAsync` | `TB_ROUTE_PATH`(+`TB_EQUIPMENTS`/`TB_DUCT`/`TB_LATERAL_PIPE` 대조) | 시작 PoC 메인장비명 + 종단 PoC 대상 객체명·분류(메인장비/부대장비/덕트/레터럴배관) | 개별 배관, Utility 배관 그룹 |
