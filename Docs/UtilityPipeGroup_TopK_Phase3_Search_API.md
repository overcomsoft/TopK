# UtilityPipeGroup Top-K 단계 3 검색 API 구현 결과

- 완료일: 2026-07-16
- 대상 프로젝트: `TopKSearchStandalone`
- 대상 DB: `DDW_AI_DB`
- 결과: 완료

## 1. 구현 결과

기존 개별 배관 `TopKSearchStandalone.SearchAsync()`는 변경 없이 유지하고, 복수 배관 그룹 전용 API를 별도 추가했다.

| 기능 | 결과 |
|---|---|
| Group ID Query 검색 | 구현 완료 |
| Equipment + Utility Group + Utility Query 검색 | 구현 완료 |
| ACTIVE scope 자동 결정 | 구현 완료 |
| Utility Group + Utility 필수 후보 필터 | 구현 완료 |
| Query 자기 그룹 제외 | 구현 완료 |
| Feature centroid HNSW 후보 조회 | 구현 완료 |
| PreferExact / ExactOnly / Ignore Size 정책 | 구현 완료 |
| Position/Pattern/Feature/Context Pair 원점수 | 구현 완료 |
| 활성 Pair 가중치 자동 정규화 | 구현 완료 |
| Hungarian 최대합 1:1 멤버 대응 | 구현 완료 |
| Coverage와 미대응 멤버 진단 | 구현 완료 |
| Arrangement 유사도 | 구현 완료 |
| 최종 점수 계산식과 기여도 | 구현 완료 |
| 그룹 프리셋 API/CLI | 구현 완료 |
| JSON 및 사람이 읽는 CLI 출력 | 구현 완료 |

## 2. 공개 API

### 2.1 Group ID로 검색

```csharp
var db = new DbConfig("localhost", 5432, "DDW_AI_DB", "postgres", "dinno");
var options = new UtilityPipeGroupSearchOptions
{
    K = 5,
    SizeMatchMode = GroupSizeMatchMode.PreferExact,
    PairWeights = new RerankWeights(25, 25, 25, 25),
    MatchedWeight = 0.80,
    ArrangementWeight = 0.20
};

var (results, meta) = await UtilityPipeGroupSearch.SearchAsync(
    db, queryGroupId, options);
```

### 2.2 Equipment 조건으로 Query 그룹 자동 선택

```csharp
var (results, meta) = await UtilityPipeGroupSearch.SearchByIdentityAsync(
    db,
    processName: "CVD",
    equipmentInstanceKey: "TNMHJ04",
    utilityGroup: "VACCUM",
    utility: "FORELINE",
    options: options);
```

scope를 생략하면 `TB_ROUTE_SOURCE_SCOPE_MANIFEST`의 ACTIVE 한 건을 사용한다. ACTIVE가 없거나 여러 개이면 임의 revision을 선택하지 않고 실패한다.

### 2.3 Query 그룹 프리셋

```csharp
var presets = await UtilityPipeGroupSearch.FetchPresetsAsync(
    db,
    utilityGroup: "VACCUM",
    utility: "FORELINE",
    limit: 100);
```

Viewer에서는 이 API로 Process, Equipment, Utility Group, Utility, 멤버 수와 Size 분포를 표시할 수 있다.

## 3. CLI 사용법

### 3.1 그룹 프리셋 목록

```powershell
dotnet run --project TopKSearchStandalone\TopKSearchStandalone.csproj -c Release -- `
  --list-group-presets `
  --utility-group VACCUM `
  --utility FORELINE `
  --dbname DDW_AI_DB --user postgres --password dinno
```

### 3.2 Group ID 검색

```powershell
dotnet run --project TopKSearchStandalone\TopKSearchStandalone.csproj -c Release -- `
  --group-query-id <GROUP_VECTOR_ID> `
  --group-size-mode PreferExact `
  --k 5 `
  --dbname DDW_AI_DB --user postgres --password dinno
```

### 3.3 Equipment 조건 검색

```powershell
dotnet run --project TopKSearchStandalone\TopKSearchStandalone.csproj -c Release -- `
  --group-search `
  --process CVD `
  --equipment TNMHJ04 `
  --utility-group VACCUM `
  --utility FORELINE `
  --group-size-mode ExactOnly `
  --k 5 `
  --dbname DDW_AI_DB --user postgres --password dinno
```

`--json`을 추가하면 Viewer나 다른 프로그램이 읽을 수 있는 상세 JSON을 반환한다. 내부 검색용 30D 배열은 JSON에서 제외하지만, C# API 객체에는 그대로 유지된다.

## 4. 검색 알고리즘

### 4.1 Query 및 후보 범위

```text
Query identity
= Scope + Revision + Process + Equipment Instance + Utility Group + Utility

Candidate mandatory filter
= 동일 Scope/Revision + READY + Utility Group + Utility

Candidate exclusion
= Query GROUP_VECTOR_ID 제외
```

`RequireSameProcess`와 `EquipmentFamilyKey`는 선택 필터다. 정확한 Equipment Instance를 후보 필터로 사용하지 않으므로 다른 장비의 과거 유사 그룹을 검색할 수 있다.

### 4.2 1차 ANN 후보

```sql
ORDER BY "FEATURE_CENTROID" <=> @queryVector::vector
LIMIT clamp(max(K × 20, 100), 1, 1000)
```

Context centroid는 ANN에 혼합하지 않고 멤버 Pair 정밀 재정렬에만 사용한다.

### 4.3 Size 정책

| 정책 | 동작 |
|---|---|
| `PreferExact` | 동일 Size 1.0, 인접 단계 0.8, 두 단계 0.5, 그 이상 0 |
| `ExactOnly` | Size가 다르면 해당 Pair 대응 금지 |
| `Ignore` | Size에 관계없이 1.0 |

Size 문자열은 공백 제거와 대문자 변환 후 표준 nominal size 순서표에서 단계 차이를 계산한다.

### 4.4 멤버 Pair 원점수

```text
Position = max(0, 1 - |Query 변위 - Candidate 변위| / 50000mm)

Pattern
= 0.5 × RLE Pattern Levenshtein 유사도
 + 0.5 × Feature[12..20] Bend cosine

Feature = max(0, Feature 30D cosine)
Context = max(0, Context 30D cosine)
```

가중치가 0보다 큰 항목만 활성화하며, Pattern 또는 Context 원본이 없는 Pair에서는 해당 가중치를 나머지 활성 항목으로 자동 재배분한다.

```text
PairBase
= Position × WPosition
 + Pattern × WPattern
 + Feature × WFeature
 + Context × WContext

PairAdjusted = PairBase × SizeScore
```

결과의 `UtilityPipePairScore`에는 각 원점수, 정규화 가중치, 항목별 기여도, PairBase, SizeScore와 PairAdjusted가 모두 포함된다.

### 4.5 Hungarian 대응

Query와 Candidate 멤버의 모든 PairAdjusted 행렬을 만든 뒤 최대합 Hungarian Algorithm을 적용한다. Query와 Candidate 수의 합만큼 dummy 행/열을 추가해 다음 상황을 명시적으로 지원한다.

- 그룹별 멤버 수가 다름
- ExactOnly Size 불일치
- 유사도가 0인 Pair
- Query 또는 Candidate 미대응 멤버

최대 멤버 수 실측 38개에서는 최대 76×76 행렬이며 계산량은 운영 범위에 충분하다.

### 4.6 Arrangement와 최종 점수

Arrangement는 절대 좌표를 비교하지 않고 다음 translation-invariant 통계를 비교한다.

- 시작점/종점 축별 표준편차
- 변위 평균과 표준편차
- 시작점/종점 pairwise 거리 통계
- 길이와 Step 통계
- AABB 크기

```text
MatchedAverage
= Hungarian이 선택한 PairAdjusted 평균

Coverage
= 2 × MatchedCount / (QueryMemberCount + CandidateMemberCount)

MatchedContribution     = MatchedAverage × 0.80
ArrangementContribution = Arrangement × 0.20

GroupSimilarity
= Coverage × (MatchedContribution + ArrangementContribution)
```

반환 결과의 `Formula`는 실제 값으로 다음과 같이 표시된다.

```text
((0.640552 × 0.800000) + (0.591459 × 0.200000)) × 0.571429
= 0.360419
```

## 5. ACTIVE DB smoke test

Query:

```text
Process       : CVD
Equipment     : TNMHJ04
Utility Group : VACCUM
Utility       : FORELINE
Query members : 4 × 50A
Size policy   : PreferExact
```

실행 결과:

| 항목 | 값 |
|---|---:|
| Query 제외 후 ANN 후보 | 7 |
| 요청/반환 K | 3 / 3 |
| 검색시간 | 약 272ms |
| Rank 1 Candidate | SLWHJ01 |
| Rank 1 멤버 대응 | 4 / Query 4 / Candidate 10 |
| Rank 1 Coverage | 0.571429 |
| Rank 1 MatchedAverage | 0.640552 |
| Rank 1 Arrangement | 0.591459 |
| Rank 1 GroupSimilarity | 0.360419 |

Group ID 직접 검색과 Equipment 조건 자동 선택이 동일 Query ID를 사용함을 확인했다. `ExactOnly` smoke test와 JSON 좌표·계산 기여도 직렬화도 통과했다.

## 6. 테스트 및 빌드

```powershell
dotnet build TopKSearchStandalone\TopKSearchStandalone.csproj -c Release --no-restore

dotnet run --project TopKSearchStandalone\TopKSearchStandalone.csproj `
  -c Release --no-build -- --group-self-test
```

결과:

- Release 빌드: 경고 0, 오류 0
- `TopK.3DViewer` 연동 Release 빌드: 오류 0, 기존 `HelixToolkit.Wpf 2.25.0`의 `NU1701` 호환성 경고 1건
- Matcher golden test: 6건 통과
- Hungarian 교차 최적해 검증
- ExactOnly/PreferExact Size 정책 검증
- Context 누락 가중치 재배분 검증
- Coverage와 최종 점수식 golden test
- 멤버 입력 순서 불변성 검증
- 절대 평행이동에 대한 Arrangement 불변성 검증

## 7. 산출물

- `TopKSearchStandalone/UtilityPipeGroupModels.cs`
- `TopKSearchStandalone/UtilityPipeGroupMatcher.cs`
- `TopKSearchStandalone/UtilityPipeGroupSearch.cs`
- `TopKSearchStandalone/UtilityPipeGroupMatcherSelfTests.cs`
- `TopKSearchStandalone/TopKSearchStandalone.cs` 그룹 CLI 확장

## 8. 단계 4 Viewer 입력

단계 4에서는 다음 기능을 연결한다.

1. 개별 배관/Utility 배관 그룹 검색 모드 전환
2. `FetchPresetsAsync()`를 이용한 그룹 프리셋과 멤버 목록
3. `SearchAsync()` 또는 `SearchByIdentityAsync()` 호출
4. 그룹 결과 DataGrid에 Similarity, Matched, Coverage, Arrangement, Size 분포 표시
5. 선택 결과의 Pair별 원점수×가중치=기여도와 최종 계산식 표시
6. Query와 Candidate의 전체 실제 상세경로 일괄 로드
7. 대응 Pair 동일 색상, 미대응 Query/Candidate와 Size 불일치 강조
8. 원좌표 보기와 나란히 보기

단계 4 완료 기준은 Query 프리셋 선택 시 그룹 전체 배관이 표시되고, Top-K 검색 시 K개 후보 그룹의 전체 멤버를 선택·비교할 수 있는 것이다.
