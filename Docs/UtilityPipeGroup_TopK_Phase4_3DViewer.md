# Utility 배관 그룹 Top-K Phase 4 — TopK.3DViewer 구현 결과

## 1. 실행 방법

```powershell
dotnet build .\TopK.3DViewer\TopK.3DViewer.csproj -c Release
& .\TopK.3DViewer\bin\Release\net8.0-windows\TopK.3DViewer.exe
```

DB 연결 전 `TopK.3DViewer/viewer.settings.json`을 작성하면 접속 정보와 그룹 검색 기본값을
미리 지정할 수 있다. 파일이 없으면 프로그램이 최초 실행 시 기본 설정 파일을 생성한다.

## 2. 구현 목적

기존 Viewer의 개별 Route Top-K를 유지하면서, `장비 + Utility Group + Utility`에 속한
여러 배관을 하나의 Query 그룹으로 사용해 유사한 기존 배관 그룹을 검색하고 Query/Candidate
멤버 대응을 3D로 비교한다.

## 3. 전체 흐름

1. `DB 연결 및 조건 로드`가 개별 Route 프리셋과 READY 그룹 프리셋을 함께 조회한다.
2. `Utility 배관 그룹` 검색 단위를 선택한다.
3. 그룹 프리셋을 선택하면 그룹 header와 전체 멤버를 읽는다.
4. 각 멤버의 `TB_ROUTE_SEGMENT_DETAIL` 실제 polyline을 일괄 조회한다.
5. 기존 Query 배관과 주변 COLUMN/BEAM 장애물을 3D 화면에 표시한다.
6. 그룹 Top-K 검색 시 Feature centroid ANN으로 후보군을 수집한다.
7. 후보마다 Hungarian Algorithm으로 Query/Candidate 멤버를 1:1 대응한다.
8. Pair 평균, 배치 유사도, Coverage로 그룹 최종 유사도를 계산한다.
9. 우측 결과 표에 Top-K 그룹을 표시하고 선택 결과의 Pair 계산식을 상세 표시한다.
10. 3D 화면에서 같은 Pair는 같은 색, 미매칭 Query/Candidate는 각각 빨강/주황으로 표시한다.

## 4. 화면 기능

### 4.1 검색 단위

- `개별 배관`: 기존 시작/종점 기반 Route Top-K 검색을 그대로 사용한다.
- `Utility 배관 그룹`: 선택한 그룹의 전체 배관을 Query로 사용한다.

모드에 따라 프리셋, 좌표 입력, 그룹 옵션, 우측 결과 DataGrid가 자동 전환된다.

### 4.2 그룹 프리셋

프리셋 표시 기준은 다음과 같다.

```text
Process | Equipment | Utility Group/Utility | Pipe Count | Size Signature
```

프리셋 아래 멤버 표에는 순서, Size, Pattern, Route GUID를 표시한다. 프리셋 선택 즉시
Query 그룹의 기존 상세 경로와 주변 장애물을 표시하므로 검색 전에도 원본 배관 배치를 확인할 수 있다.

### 4.3 Size 매칭 정책

| 정책 | 동작 |
|---|---|
| `PreferExact` | 같은 Size Pair를 우선하고 필요한 경우 다른 Size를 허용한다. |
| `ExactOnly` | 같은 Size끼리만 Pair를 허용한다. |
| `Ignore` | Size를 Pair 적합도 계산에서 제외한다. |

### 4.4 그룹 가중치

- `Pair 평균 %`: Hungarian으로 선택된 멤버 Pair 점수 평균의 비중
- `배치 %`: 그룹 내 배관 상대 배치 유사도의 비중

두 값은 합계 100%로 자동 정규화되고 JSON에 저장된다. 기본값은 80/20이다.

### 4.5 3D 비교 모드

- `Original`: Query와 Candidate를 DB 실제 좌표에 표시한다.
- `SideBySide`: Candidate 그룹을 Query 오른쪽으로 평행 이동하고 Y/Z 중심을 맞춰 형상을 비교한다.

SideBySide 이동은 렌더링 좌표에만 적용한다. 장애물 조회와 검색 계산은 항상 DB 원좌표를 사용한다.

### 4.6 색상 의미

| 표시 | 의미 |
|---|---|
| Query/Candidate 같은 색 | Hungarian Algorithm이 대응시킨 하나의 배관 Pair |
| 빨강 | 대응 후보가 없는 Query 배관 |
| 주황 | 대응 Query가 없는 Candidate 배관 |
| 노란색 외곽선 | Size가 일치하지 않는 허용 Pair |
| 가는 Rank 색상 | `Top-K 전체 표시`가 켜진 Original 모드의 비선택 후보 그룹 |
| 초록/주황 구 | Query 배관의 시작점/종점 |

## 5. 점수 계산

멤버 Pair 기본 유사도는 활성화된 네 항목의 가중합이다.

```text
Pair = Position × Wpos
     + Pattern  × Wpattern
     + Feature  × Wfeature
     + Context  × Wcontext

Adjusted Pair = Pair × SizeScore
```

그룹 최종 유사도는 다음과 같다.

```text
GroupSimilarity = (MatchedAverage × MatchedWeight
                 + Arrangement   × ArrangementWeight)
                 × Coverage
```

우측 상세창에는 각 Pair마다 원점수, 가중치, 최종 기여도와 Size 보정 결과를 표시한다.

## 6. JSON 설정

```json
{
  "searchUnit": "Group",
  "groupSizeMatchMode": "PreferExact",
  "groupMatchedWeight": 80,
  "groupArrangementWeight": 20,
  "groupComparisonView": "Original",
  "showUnmatchedGroupMembers": true
}
```

화면에서 값을 변경하면 처음 읽은 `viewer.settings.json`에 다시 저장된다.

## 7. 주요 코드

| 파일 | 역할 |
|---|---|
| `TopKSearchStandalone/UtilityPipeGroupSearch.cs` | 그룹 프리셋, Query 그룹, ANN 후보, Top-K 검색 |
| `TopKSearchStandalone/UtilityPipeGroupMatcher.cs` | Hungarian Pair, Size 정책, Arrangement/Coverage, 그룹 점수 |
| `TopK.3DViewer/MainWindow.xaml` | 개별/그룹 UI, 그룹 옵션, 결과 DataGrid |
| `TopK.3DViewer/MainWindow.xaml.cs` | 그룹 프리셋·검색·상세·3D 렌더링 흐름 |
| `TopK.3DViewer/Models/ViewerModels.cs` | JSON 설정과 그룹 화면 모델 |

## 8. 검증 결과

### 8.1 자동 테스트

- Python 그룹 Vector/Profile/Schema 테스트: 15/15 통과
- C# Hungarian/Size/Coverage/Arrangement 테스트: 6/6 통과
- `git diff --check`: 오류 없음
- Viewer Release 빌드: 오류 0건
- WPF 숨김 기동 4초 smoke test: 프로세스 생존, `Responding=True`
- 실제 WPF 창 생성 및 개별/그룹 검색 단위 UI 표시 확인
- 그룹 결과에 매칭 수, Size 분포, 실제/재구성 geometry 상태 표시

### 8.2 실제 DB smoke test

```text
Query: CVD / TNMHJ04 / VACCUM / FORELINE
Query member count: 4
ANN candidates: 7
Returned Top-K: 5
Elapsed: 243.2 ms
Rank 1: SLWHJ01
Score: 0.360419
Matched: 4 / Query 4 / Candidate 10
Coverage: 0.5714
Arrangement: 0.5915
```

Rank 1 계산식:

```text
((0.640552 × 0.800000) + (0.591459 × 0.200000)) × 0.571429
= 0.360419
```

## 9. 참고 사항

- `HelixToolkit.Wpf 2.25.0` 복원 시 `NU1701` 경고가 발생하지만 현재 Release 빌드와
  WPF 기동에는 실패가 없다. 향후 HelixToolkit의 최신 WPF 호환 패키지 전환을 별도 검토한다.
- 상세 polyline이 없는 legacy 멤버는 시작/종점 메타데이터로 직교 fallback을 생성하고,
  검색 점수는 원본 Vector 데이터로 계산한다.
