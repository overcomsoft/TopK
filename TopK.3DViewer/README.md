# TopK.3DViewer

현재 TopKGen의 `TopKSearchStandalone` 검색 엔진을 사용하는 독립 WPF 3D Viewer이다.
기존 RoutingAIViewer는 화면/DB 구조 확인에만 사용했으며 이 프로젝트의 소스는 새로 작성했다.

## 주요 기능

- PostgreSQL 연결과 검색조건 목록 자동 로드
- 공정, 장비, Utility Group, Utility, Size, 방향 Pattern 필터
- 시작/종점 XYZ 및 K 입력
- `TB_ROUTE_PATH` 프리셋으로 검색조건 자동 입력
- Baseline 또는 ACTIVE Context Vector Top-K 검색
- Top-K 실제 상세 polyline 동시 3D 표시
- 선택 경로 흰색 강조와 점수/메타 상세 표시
- 검색경로 주변 1,000mm 구조 기둥/보 AABB 표시
- 신형 direct segment schema, 구형 segment-map schema, 시작~종점 직선 fallback 지원

## 설정

`viewer.settings.example.json`을 `viewer.settings.json`으로 복사하고 접속값을 수정한다.

```powershell
Copy-Item TopK.3DViewer\viewer.settings.example.json TopK.3DViewer\viewer.settings.json
```

```json
{
  "host": "localhost",
  "port": 5432,
  "database": "DDW_AI_DB",
  "user": "postgres",
  "password": "비밀번호",
  "defaultK": 5,
  "useObstacleContext": true,
  "obstacleLimit": 2500,
  "weightPosition": 25,
  "weightPattern": 25,
  "weightVector": 25,
  "weightContext": 25,
  "redistributeMissingPatternWeight": true
}
```

비밀번호가 있는 `viewer.settings.json`은 Git에 커밋하지 않는다. 설정파일이 없으면 화면의
기본값으로 자동 생성한다. UI에서 가중치를 수정하고 입력 포커스를 옮기거나 검색을 실행하면
동일한 JSON 파일에 변경값을 자동 저장한다.

## 빌드와 실행

```powershell
dotnet build TopK.3DViewer\TopK.3DViewer.csproj -c Release

dotnet TopK.3DViewer\bin\Release\net8.0-windows\TopK.3DViewer.dll
```

또는 다음 실행파일을 직접 실행한다.

```text
TopK.3DViewer\bin\Release\net8.0-windows\TopK.3DViewer.exe
```

## 화면 사용 순서

1. PostgreSQL 접속값 입력 후 `DB 연결 및 조건 로드`를 누른다.
2. 필요하면 기존 Route 프리셋을 선택한다.
3. 공정, 장비, Utility 조건과 시작/종점 좌표를 입력한다.
4. 결과 수 K와 `장애물 Context Vector 재정렬` 여부를 선택한다.
5. `Top-K 검색 및 3D 로드`를 누른다.
6. 우측 결과 행을 선택해 경로와 점수를 확인한다.
7. 필요하면 상단 `주변 구조 BIM`을 켜 기둥/보와 경로 관계를 확인한다.

Context 검색은 `TB_ROUTE_SOURCE_SCOPE_MANIFEST`의 ACTIVE revision을 자동 사용한다. ACTIVE가
없거나 복수이면 임의 fallback하지 않고 오류를 표시한다.

## 3D 조작

- 회전: 마우스 오른쪽 버튼 드래그
- 이동: 마우스 가운데 버튼 드래그
- 확대/축소: 휠
- 전체 맞춤: 상단 `카메라 맞춤`
- 시작점: 녹색 구, 종점: 주황색 구
- 선택 Top-K: 흰색 굵은 선
- 나머지 Top-K: 순위별 색상 선
- 기둥: 회색 AABB, 보: 붉은 AABB

## 관련 데이터

- 검색: `TB_ROUTE_FEATURE_VECTOR`
- Context: `TB_ROUTE_CONTEXT_VECTOR`, `TB_ROUTE_SOURCE_SCOPE_MANIFEST`
- 경로 메타: `TB_ROUTE_PATH`
- 경로점: `TB_ROUTE_SEGMENTS`, `TB_ROUTE_SEGMENT_DETAIL`, 선택적으로 `TB_ROUTE_PATH_SEGMENT_MAP`
- 구조 BIM: `TB_BIM_OBSTACLE`

검색 후 중앙 3D 화면에는 선택한 Top-K 경로가 흰색 원통형 파이프로 강조된다. 나머지
Top-K는 순위별 색상 선으로 비교 표시되며, 우측 결과표의 행을 바꾸면 선택 경로 주변
1,000mm의 기둥·보 장애물이 자동으로 다시 조회되어 반투명 3D 솔리드로 표시된다.

## 유사도 가중치

좌측 `유사도 가중치`에서 Position, Pattern, Feature, Context 사용 여부를 값으로 조정한다.
`0`은 해당 항목을 제외하고, `0`보다 큰 항목들은 입력 크기와 관계없이 `100/N`으로 균등
배분한다. 예를 들어 `1/0/7/3`을 입력하면 `33.333/0/33.333/33.333`으로 저장·적용된다.
`Similarity`는 별도 가중치가 아니라 네 점수의 최종 가중합 결과이다.

방향 Pattern을 입력하지 않는 검색에서는 `Pattern 미입력 시 해당 가중치 자동 재배분`을
체크한다. 이 경우 Pattern 가중치는 벌점으로 남지 않고 Position, Feature, Context로
재배분된다. 실제 적용 비율은 우측 결과 요약과 선택 상세의 `Weight Profile`에서 확인한다.

## DB 스키마 호환 및 오류 확인

`TB_ROUTE_PATH`의 장비명 컬럼은 DB 버전마다 다를 수 있다. Viewer는 연결 시 컬럼 목록을
확인하고 `EQUIPMENT_NAME`, `SOURCE_OWNER_NAME`, `EQUIPMENT_TAG` 순서로 존재하는 컬럼을
자동 선택한다. 공정명, Utility, Size도 알려진 구·신 스키마 후보 중 실제 컬럼을 선택한다.

`42703: "SOURCE_OWNER_NAME" 이름의 칼럼은 없습니다` 오류가 발생하면 최신 소스로 Release
빌드한 실행파일을 사용한다. 현재 DDW_AI_DB의 `EQUIPMENT_NAME`을 그대로 사용하므로 DB에
호환용 컬럼을 추가할 필요는 없다.

```powershell
dotnet build TopK.3DViewer\TopK.3DViewer.slnx -c Release
TopK.3DViewer\bin\Release\net8.0-windows\TopK.3DViewer.exe
```
