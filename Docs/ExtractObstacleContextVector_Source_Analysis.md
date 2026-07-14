# `ExtractObstacleContextVector.py` 소스 상세 분석 및 개선 제안

> 상태 갱신(2026-07-13): 본 문서는 기존 v1 소스의 문제 분석 기록이다. AABB 표면거리,
> 500/1,000mm 다중 shell, BAY 범위 격리, Python/C# 공식 통일 및 30D free-space
> 보정은 `topkgen-v2`에 반영됐다. 구현 결과는 `ContextVector_Phase1_Implementation.md`와
> `ContextVector_Phase2_Scope_and_Validation.md`를 참조한다.

작성일: 2026-07-13  
분석 대상: `Tools/ExtractObstacleContextVector.py`  
연관 대상: `Tools/sql/create_route_context_vector_table.sql`, `TopKSearchStandalone/TopKSearchStandalone.cs`, `Tools/BuildContextVectors.py`, `VectorDBGen/MainWindow.xaml.cs`

## 1. 결론 요약

이 소스의 기본 방향은 타당하다. 기존 설계 경로의 시작점과 종단점 주변 장애물 환경을 별도 벡터로 저장하고, 신규 라우팅 요청에서도 같은 방식으로 컨텍스트 벡터를 계산하여 기존 설계 후보를 재정렬하려는 구조다. 특히 기존 30차원 형상 벡터에서 신규 요청 시 계산할 수 없는 `env_cost`를 억지로 채우지 않고, **경로 생성 전에 계산 가능한 환경 특징을 별도 벡터로 분리했다는 점**은 설계상 장점이다.

그러나 현재 구현은 운영 적용 전에 보완이 필요하다.

가장 중요한 문제는 다음과 같다.

1. 장애물까지의 거리가 AABB 표면 거리가 아니라 **AABB 중심점 거리**다. 크고 긴 기둥·보를 누락하거나 실제 이격 거리를 왜곡할 수 있다.
2. `Tier3`는 실제 기존 경로를 읽지 않는다. 시작점과 종단점을 잇는 직선만 사용하므로 문서의 “경로 전체 특징” 설명과 실제 계산이 다르다.
3. 장애물 로드 시 프로젝트·모델·스냅샷 범위가 없다. 한 테이블에 여러 프로젝트가 섞이면 다른 현장의 장애물이 벡터를 오염시킬 수 있다.
4. Python 색인기와 C# 신규 쿼리 인코더의 Tier3 기둥 판정 방식이 서로 다르다. 동일한 입력에도 두 벡터가 달라질 수 있다.
5. VectorDBGen 연계 경로는 현재 잘못된 테이블명·DDL명·누락 모듈을 참조한다. UI에서 Context Vector 빌드를 실행하기 어려운 상태다.
6. 전역 L2 정규화 전에 특징별 통계 스케일링과 의미 있는 가중치가 없다. 방향 단위벡터와 “장애물 없음=1” 값이 다른 특징을 과도하게 지배할 수 있다.

따라서 현재 버전은 개념 검증용으로는 사용할 수 있지만, 검색 품질과 재현성을 보장하는 운영용 인코더로 보기에는 이르다. 아래의 P0 항목을 먼저 수정하고, 기존 벡터를 `ENCODER_VERSION=topkgen-v2`로 전량 재생성하는 것을 권장한다.

---

## 2. 소스의 목적과 전체 처리 흐름

### 2.1 입력 데이터

| 입력 | 사용 컬럼 | 용도 |
|---|---|---|
| `TB_ROUTE_PATH` | `ROUTE_PATH_GUID`, `SOURCE_POSX/Y/Z`, `TARGET_POSX/Y/Z` | 기존 경로의 시작·종단 좌표 |
| `TB_ROUTE_FEATURE_VECTOR` | `ROUTE_PATH_GUID` | 컨텍스트 벡터 생성 대상 경로를 기존 30D 벡터 보유 경로로 제한 |
| `TB_BIM_OBSTACLE` | `DDWORKS_TYPE`, `AABB_MIN*`, `AABB_MAX*` | 기둥·보의 종류, 중심점, 크기 계산 |

실제 배관 폴리라인을 가진 `TB_ROUTE_SEGMENTS`와 `TB_ROUTE_SEGMENT_DETAIL`은 이 소스에서 사용하지 않는다.

### 2.2 처리 순서

1. `tool_config`에서 PostgreSQL 접속정보를 해석한다.
2. `TB_BIM_OBSTACLE`의 기둥·보를 모두 메모리에 읽는다.
3. 각 장애물 AABB의 중심점이 속한 1,000mm 3D 격자 셀에 장애물을 등록한다.
4. `TB_ROUTE_PATH`와 `TB_ROUTE_FEATURE_VECTOR`가 매칭되는 경로를 읽는다.
5. 시작점 주변 1,000mm 범위에서 10D Tier1을 계산한다.
6. 종단점 주변 2,000mm 범위에서 10D Tier2를 계산한다.
7. 시작점과 종단점의 두 점으로 4D Tier3를 계산한다.
8. 24개 값을 이어 붙인 뒤 L2 정규화한다.
9. `TB_ROUTE_CONTEXT_VECTOR`를 전체 삭제하고 계산 결과를 다시 삽입한다.

### 2.3 저장 결과

| 컬럼 | 내용 |
|---|---|
| `ROUTE_PATH_GUID` | 기존 경로 식별자 |
| `CONTEXT_VECTOR` | L2 정규화된 `vector(24)` |
| `START_META_JSON` | 시작점 주변 기둥·보 개수와 최근접 거리 |
| `END_META_JSON` | 종단점 주변 기둥·보 개수와 최근접 거리 |
| `TIER3_META_JSON` | 층 전환, 기둥 셀 수, 보 평행도, 진행 방향 |
| `ENCODER_VERSION` | 현재 고정 문자열 `topkgen-v1` |

HNSW cosine 인덱스도 생성하지만, 현재 `TopKSearchStandalone`은 DB에서 이 벡터로 ANN 검색하지 않고 기존 후보를 읽은 뒤 C# 메모리에서 cosine 점수를 계산한다. 현재 사용 방식만 보면 이 HNSW 인덱스는 검색에 사용되지 않는다.

---

## 3. 24차원 벡터 상세 분석

## 3.1 Tier1: 시작 PoC 주변 10D

탐색 반경은 1,000mm다.

| 인덱스 | 계산값 | 범위/의미 |
|---:|---|---|
| 0 | `min(기둥 수 / 8, 1)` | 기둥 밀도 |
| 1~3 | 시작점에서 최근접 기둥 중심으로 향하는 단위벡터 XYZ | 기둥 방향 |
| 4 | `min(중심거리 / 3000, 1)` | 기둥 거리, 없으면 1 |
| 5 | `min(보 수 / 5, 1)` | 보 밀도 |
| 6~8 | 시작점에서 최근접 보 중심으로 향하는 단위벡터 XYZ | 보 방향 |
| 9 | `min(중심거리 / 2000, 1)` | 보 거리, 없으면 1 |

## 3.2 Tier2: 종단 PoC 주변 10D

레이아웃과 공식은 Tier1과 같고 탐색 반경만 2,000mm다. 시작과 종단의 역할이 다른 설비 연결 구조라면 비대칭 반경이 의미가 있을 수 있지만, 현재 코드에는 1,000/2,000mm의 데이터 근거와 설정 가능성이 없다.

## 3.3 Tier3: 시작~종단 보조 특징 4D

| 인덱스 | 계산값 | 실제 의미 |
|---:|---|---|
| 20 | 시작·종단 Z를 500mm로 반올림한 값이 다르면 `1/3`, 같으면 `0` | 실제 층 전환 수가 아니라 시작·종단 Z 레벨 차이 유무 |
| 21 | 시작~종단 XY 직선이 통과한다고 추정한 격자 중 기둥 발견 셀 수 `/15` | 실제 배관 경로가 아닌 chord 주변 기둥 분포 |
| 22 | 시작·종단 주변 보 AABB 장축과 시작→종단 수평방향의 `abs(cos)` 평균 | 보와 전역 경로 방향의 평행도 |
| 23 | 시작→종단 수평방향의 X 성분 `dx / hypot(dx,dy)` | 전역 좌표계 기준 방향 |

### 중요한 의미 불일치

주석과 문서는 Tier3를 “경로 전체” 또는 “층 전환수”라고 설명하지만, 실제로는 경로의 점열을 전혀 읽지 않는다. 따라서 다음 두 경로는 시작·종단이 같으면 Tier3가 같다.

- 장애물을 피해 여러 번 꺾인 실제 기존 경로
- 시작점과 종단점을 바로 잇는 직선 경로

이는 신규 쿼리 시점에도 동일하게 계산할 수 있다는 장점과 맞닿아 있다. 즉 **경로가 없어도 계산 가능한 검색 컨텍스트**와 **기존 실제 경로가 장애물을 어떻게 회피했는지 나타내는 학습 특징**은 동시에 만족할 수 없다. 두 목적은 별도 벡터로 분리해야 한다.

---

## 4. 잘 설계된 부분

### 4.1 신규 요청에서도 계산 가능한 별도 컨텍스트

기존 경로 자체가 필요한 형상·회피 비용과 달리 시작·종단 좌표와 BIM 장애물만으로 계산하려는 접근은 올바르다. 신규 라우팅 요청과 기존 경로 후보를 같은 함수 공간에서 비교할 수 있다.

### 4.2 1차 후보검색과 재정렬의 분리

현재 검색 코드는 24D 컨텍스트를 30D ANN 후보검색에 합치지 않고, 후보가 좁혀진 뒤 별도 `ctxScore`로 사용한다. 기존 형상 벡터와 중복되는 환경 정보 때문에 1차 검색을 왜곡할 가능성을 줄이는 구조다.

### 4.3 장애물 전체를 한 번만 로드하는 방식

경로마다 DB 범위 쿼리를 반복하지 않고 인메모리 공간 인덱스를 사용하는 것은 일괄 생성 성능에 유리하다. 데이터가 메모리에 들어갈 수 있는 규모라면 단순하고 효과적이다.

### 4.4 저장 원자성

기존 데이터 삭제와 새 데이터 삽입이 같은 DB 트랜잭션에서 수행된다. 중간 오류가 호출부까지 전파되어 rollback되면 독자가 부분 생성 결과를 보는 문제를 피할 수 있다.

### 4.5 메타데이터 동시 저장

벡터만 저장하지 않고 원시 개수와 최근접 거리를 JSON으로 남긴 것은 검색 결과를 설명하고 품질 이상을 조사하는 데 도움이 된다.

---

## 5. 수정·보완이 필요한 문제

## 5.1 P0 — 운영 적용 전 필수 수정

### P0-1. AABB 중심점 거리 사용으로 장애물 누락 및 거리 왜곡

관련 코드: `ObstacleIndex.__init__`, `query_radius`, `load_obstacle_index`, `encode_tier`

현재 장애물은 AABB 중심점 하나로 축약된다. 탐색도 “PoC에서 장애물 중심까지의 거리 ≤ 반경”으로 판정한다.

예를 들어 길이 8m인 보의 끝단이 PoC에서 300mm 떨어져 있어도 보 중심이 4m 이상 떨어져 있으면 검색에서 제외된다. 반대로 중심은 가까워도 실제 표면이 어느 방향에 있는지 알 수 없다. 실제 라우팅에 중요한 것은 중심점이 아니라 배관 중심선과 장애물 표면 사이의 거리 및 여유공간이다.

개선안:

1. 장애물 객체에 `min`, `max`, `center`, `extent`, 안정적인 obstacle ID를 모두 저장한다.
2. 점과 AABB의 최근접점을 아래와 같이 계산한다.
   - `closest.x = clamp(point.x, min.x, max.x)`
   - Y/Z도 동일
3. `distance(point, closest)`를 실제 거리로 사용한다.
4. 방향도 `point → center`가 아니라 우선 `point → closest surface point`로 계산한다.
5. 공간 인덱스에는 중심 셀 하나가 아니라 AABB가 겹치는 모든 셀에 등록하거나 R-tree/STRtree를 사용한다.
6. 여러 셀에 중복 등록될 수 있으므로 obstacle ID로 조회 결과를 deduplicate한다.

### P0-2. 프로젝트·모델 범위가 없는 장애물 전량 혼합

관련 코드: `load_obstacle_index`

쿼리는 `DDWORKS_TYPE`만 필터링한다. `TB_BIM_OBSTACLE`에 여러 프로젝트, 장비 스냅샷, 모델 revision이 같이 저장되면 현재 경로와 무관한 장애물까지 포함된다.

이 저장소의 다른 추출 코드인 `Extract_Design_Pattern.py`는 `PROJECT_ID`, `EQUIPMENT_TAG`, `PROJECT_NAME`, `MAIN_EQUIPMENT_NAME` 같은 가용 범위 컬럼을 검사하고 프로젝트 격리를 고려한다. Context Vector도 최소한 경로와 장애물을 같은 프로젝트/모델 revision으로 결합해야 한다.

개선안:

- 대상 DB의 실제 스키마를 확인하여 `PROJECT_ID` 또는 동등한 모델 식별자를 명시적으로 받는다.
- CLI에 `--project-id`, 필요하면 `--model-revision`을 추가한다.
- `TB_ROUTE_CONTEXT_VECTOR`의 키도 `(PROJECT_ID, ROUTE_PATH_GUID, ENCODER_VERSION)` 또는 별도 surrogate key로 변경한다.
- 프로젝트 범위를 결정할 수 없을 때는 경고 후 전체 실행하지 말고 실패시키는 편이 안전하다.

### P0-3. Python과 C# Tier3 계산 불일치

관련 코드:

- Python: `ExtractObstacleContextVector.py::encode_tier3`
- C#: `TopKSearchStandalone.cs::BuildContextVector24Async`, `EncodeTier3`

Python은 각 격자 중심에서 3D 구면 반경 600mm를 조회하며 장애물 중심 Z와 평균 경로 Z까지 비교한다. C#은 시작~종단 XY bounding box의 기둥을 Z 조건 없이 가져온 뒤, 격자 중심과 기둥 중심의 X/Y 차이가 각각 600mm 이하인지 사각형 조건으로 검사한다.

차이점:

- Python: 3D Euclidean sphere
- C#: 2D axis-aligned square
- Python: 공간 인덱스에 들어간 전체 장애물 중심 후보
- C#: 시작~종단 XY bounding box와 겹치는 AABB 후보
- Python은 Z 중심 거리에 민감하지만 C#은 Z를 무시

현재 코드 주석의 “완전히 동일한 공식”은 사실이 아니다. 과거 경로 벡터와 신규 쿼리 벡터가 조용히 달라져 cosine 유사도가 왜곡될 수 있다.

개선안:

- 공식을 하나의 명세와 golden test vector로 고정한다.
- 가능하면 C#과 Python이 동일한 공용 인코더를 사용한다. 예: C ABI/.NET 라이브러리 한 곳에서 계산하거나, Python 생성 시에도 C#과 동일한 SQL/기하 규칙을 사용한다.
- 최소한 20개 이상의 고정 장애물 장면에 대해 양쪽 24D 각 성분의 허용오차를 `1e-6` 이하로 검증한다.

### P0-4. 실제 경로 주변 특징이라는 요구와 구현 불일치

관련 코드: `extract_context_vectors`, `encode_tier3`

현재 소스는 `TB_ROUTE_SEGMENTS`, `TB_ROUTE_SEGMENT_DETAIL`을 읽지 않는다. 따라서 기존 설계의 시작 경로와 종단 경로 주변 장애물이 아니라, **원시 SOURCE/TARGET PoC 한 점 주변**과 **두 PoC를 잇는 직선**을 계산한다.

사용자 목적에 맞는 권장 분리:

1. `EndpointEnvironmentVector`
   - 신규 쿼리 전에 계산 가능
   - 시작/종단 PoC 주변 장애물 배치만 표현
   - 검색 후보 재정렬에 사용
2. `EndpointRouteResponseVector` 또는 `StubObstacleInteractionVector`
   - 기존 경로의 시작부터 첫 N mm, 종단에서 역방향 첫 N mm의 실제 폴리라인 사용
   - 장애물과의 이격, 첫 회피방향, 통과면, 상하 우회, 복도 폭 등을 표현
   - 검색된 기존 패턴을 실제 stub/waypoint로 재사용할 때 사용

이렇게 해야 “신규 요청에도 계산 가능”과 “기존 경로의 실제 회피 방식 학습”을 혼동하지 않는다.

### P0-5. VectorDBGen 연계가 현재 깨져 있음

확인된 문제:

| 위치 | 현재 참조 | 실제 상태 |
|---|---|---|
| `Tools/BuildContextVectors.py` | `ContextVectorEncoder.encoder` import | 저장소에 해당 모듈/디렉터리 없음 |
| `VectorDBGen/MainWindow.xaml.cs` | 소스 테이블 `TB_BIM_OBSTACLES` | 실제 소스와 다른 복수형 이름. 추출기는 `TB_BIM_OBSTACLE` 사용 |
| `VectorDBGen/MainWindow.xaml.cs` | `create_context_vector_table.sql` | 해당 파일 없음 |
| 실제 DDL | `create_route_context_vector_table.sql` | VectorDBGen이 참조하지 않음 |
| VectorDBGen 빌더 | `BuildContextVectors.py` | 분석 대상 `ExtractObstacleContextVector.py`와 별도 실행 경로 |

즉 CLI에서 `ExtractObstacleContextVector.py run-all`을 직접 실행하는 경로와 VectorDBGen UI 경로가 통합되어 있지 않다.

개선안:

- Context Vector 생성 진입점을 하나로 통합한다.
- 권장: `ExtractObstacleContextVector.py`의 인코더 로직을 `Tools/context_vector_encoder.py`로 분리한다.
- `ExtractObstacleContextVector.py`와 `BuildContextVectors.py`가 같은 모듈을 import하게 한다.
- DDL 이름을 하나로 통일하고 VectorDBGen 리소스 포함 여부를 csproj에서 검증한다.
- `TB_BIM_OBSTACLE` 단수형으로 통일한다.
- UI 실행 전 import smoke test와 DDL 존재 검사를 자동화한다.

### P0-6. 입력 NULL 검증 불완전

SQL은 `SOURCE_POSX`와 `TARGET_POSX`만 NULL이 아닌지 검사한다. Y/Z 중 하나가 NULL이면 `float(None)`에서 전체 추출이 중단된다. GUID가 NULL이면 `guid.strip()`에서도 중단된다.

개선안:

- GUID와 6개 좌표를 모두 `IS NOT NULL`로 검사한다.
- `isfinite()`로 NaN/Infinity도 거부한다.
- 잘못된 행은 GUID와 사유를 별도 오류 목록에 남긴다.
- 전체 작업을 중단할지 건별 skip할지 정책을 CLI 옵션으로 명확히 한다.

## 5.2 P1 — 검색 품질과 재현성 개선

### P1-1. Tier3의 층 전환 수는 실제 전환 수가 아님

시작 Z와 종단 Z 두 값만 set에 넣으므로 결과는 0 또는 1뿐이다. 정규화 결과도 0 또는 0.3333만 가능하다. 기존 경로가 중간에 위로 우회했다가 원래 높이로 돌아오는 경우 전환이 2번 이상이어도 0이 된다.

권장:

- 검색 전 컨텍스트에는 `abs(endZ-startZ)` 또는 normalized vertical displacement처럼 정직한 이름의 특징을 사용한다.
- 실제 경로 특징에는 실제 폴리라인의 Z run 변화 수, 최대 Z 편차, 누적 수직 길이를 사용한다.

### P1-2. 격자 셀 생성 방식이 경로 통과 셀을 정확히 열거하지 않음

현재는 수평길이를 1,000mm로 나눈 횟수만큼 직선을 샘플링한다. 짧은 대각선이 여러 셀 경계를 지나거나 셀 경계에 가까이 진행하면 통과 셀을 빠뜨릴 수 있다.

권장:

- 2D voxel traversal(Amanatides-Woo) 또는 supercover line 알고리즘으로 직선이 실제 통과하는 모든 셀을 열거한다.
- 실제 경로를 사용할 경우 각 세그먼트별로 같은 traversal을 적용한다.

### P1-3. 200개 셀 제한이 비결정적이고 Python/C# 간 결과가 달라질 수 있음

Python은 set을 list로 바꾼 뒤 앞 200개를 취하고, C#은 `HashSet.Take(200)`을 사용한다. 두 언어의 집합 순회 순서는 동일하다는 보장이 없다. 긴 경로에서 서로 다른 구간을 검사하게 된다.

권장:

- 경로 진행 순서대로 셀을 보존한다.
- 제한이 필요하면 균등 간격으로 deterministic downsampling한다.
- 더 좋은 방법은 절대 개수 대신 `기둥 포함 셀 비율`을 계산하여 경로 길이 편향을 제거하는 것이다.

### P1-4. 보 중복 집계

같은 보가 시작 1,000mm와 종단 2,000mm 범위에 모두 걸리면 `all_beams`에 두 번 들어가 평행도 평균에 이중 반영된다. 장애물 ID를 로드하지 않아 deduplicate할 수도 없다.

권장:

- `INSTANCE_GUID` 또는 안정적인 장애물 키를 함께 로드한다.
- Tier3 집계 전에 obstacle ID로 중복 제거한다.

### P1-5. 회전된 보의 방향 추정이 부정확

AABB의 가장 긴 X/Y/Z extent를 보의 축으로 가정한다. 회전된 보의 AABB는 실제 중심축 방향을 잃어버리며, 45도 보라면 X/Y extent가 비슷해 임의 축으로 판정될 수 있다.

권장:

- OBB 회전축, transformation matrix, centerline 또는 quaternion이 있으면 그것을 사용한다.
- 없으면 이 특징은 신뢰도가 낮음을 명시하고, X/Y extent 차이가 일정 비율보다 작을 때 `orientation_unknown`으로 처리한다.

### P1-6. 전역 좌표 방향 특징의 일반화 한계

최근접 장애물 방향과 `bearing_cos`는 월드 X/Y/Z 축 기준이다. 동일한 배관 설계가 다른 위치에서 90도 회전되어 배치되면 구조는 같아도 벡터가 크게 달라진다. 또한 `bearing_cos` 하나만으로는 +Y와 -Y를 구분하지 못한다.

권장:

- 시작→종단 수평방향을 local X, 그 수직방향을 local Y, 월드 Z를 local Z로 정의하여 장애물 방향을 local frame으로 변환한다.
- 장비 face/PoC 방향을 알 수 있다면 그것을 우선 local frame 기준으로 사용한다.
- 전역 방향이 실제 설계 규칙에 필요하다면 `cos(theta), sin(theta)` 두 차원으로 저장한다.

### P1-7. 카운트 포화와 고정 거리 상수의 근거 부족

기둥 8개, 보 5개부터 모두 같은 값 1로 포화된다. 시작 반경은 1,000mm인데 기둥 거리는 3,000mm로 나누므로 시작점 기둥 거리값은 최대 약 0.333이다. 종단 반경 2,000mm에서도 최대 약 0.667이다.

권장:

- 실측 분포의 P95/P99를 기준으로 스케일 상수를 결정한다.
- 원시 통계를 버전별 JSON으로 보관한다.
- 카운트는 `log1p(count)/log1p(p99)` 같은 완만한 포화 함수를 검토한다.
- 시작·종단에 같은 거리 의미를 원한다면 각 탐색 반경으로 나누거나 명시적인 공용 최대거리로 통일한다.

### P1-8. “장애물 없음” 표현이 cosine 점수에 강하게 기여

장애물이 없으면 거리 차원에 1.0을 넣는다. 따라서 장애물이 전혀 없는 두 위치는 거리 차원의 큰 양수값 때문에 높은 유사도를 얻는다. 이 자체는 의도일 수 있지만, 장애물이 있는 경우 방향 단위벡터는 norm 1을 추가하므로 전역 L2 정규화 후 다른 모든 차원의 크기도 바뀐다.

권장:

- `presence`와 `distance`를 분리한다.
- 없을 때 `presence=0, distance=0, direction=(0,0,0)`으로 정의한다.
- 있을 때 `presence=1`, distance는 가까울수록 큰 clearance-risk 또는 별도 normalized distance로 표현한다.
- 특징 그룹별 표준화와 가중치 후 마지막에 L2 정규화한다.

### P1-9. 최근접 장애물 하나만으로 방향을 표현

같은 최근접 장애물을 가진 두 환경은 반대편이 완전히 막혀 있어도 비슷하게 보일 수 있다. 실제 라우팅 가능 공간은 한 개 장애물보다 주위 점유 분포가 중요하다.

권장 특징 예:

- local frame 기준 8방향 또는 3D octant별 최근접 표면거리
- 좌/우/상/하/전/후 clearance
- 반경별 장애물 개수 histogram(0~500, 500~1000, 1000~2000mm)
- 장애물 종류별 점유율
- 가장 넓은 탈출 방향과 예상 자유 통로 폭

### P1-10. 시작·종단 반경과 특징 정의가 코드 상수에 고정

배관 구경, 장비 크기, 유틸리티, required clearance에 관계없이 같은 반경을 쓴다.

권장:

- 기본 반경 + `pipe_radius + required_clearance`를 반영한다.
- CLI 설정을 허용하되 `ENCODER_VERSION` 또는 `ENCODER_CONFIG_HASH`에 설정값을 포함한다.
- 후보와 쿼리가 반드시 동일 설정으로 계산됐는지 검증한다.

## 5.3 P2 — 운영성과 유지보수 개선

### P2-1. 사용되지 않는 함수

`table_exists`와 `pgvector_installed`는 정의만 되고 호출되지 않는다. 스키마 사전검증에 실제 사용하거나 제거해야 한다.

### P2-2. 스키마 migration 부재

`CREATE TABLE IF NOT EXISTS`는 기존 테이블 구조가 오래됐거나 vector 차원이 다른 경우 수정하지 않는다. `ENCODER_VERSION`이 있어도 한 GUID당 한 행만 허용되어 v1/v2 비교 저장이 불가능하다.

권장:

- `(PROJECT_ID, ROUTE_PATH_GUID, ENCODER_VERSION)`를 유일키로 사용한다.
- active encoder version을 별도 설정 또는 view로 선택한다.
- DDL migration을 명시적으로 관리한다.

### P2-3. 전체 메모리 적재와 전체 rows 보관

현재 약 827건 규모에서는 문제가 작지만 프로젝트 수가 늘면 장애물 전체와 결과 전체를 동시에 메모리에 보관한다.

권장:

- 프로젝트별 obstacle index를 구성한다.
- 서버-side cursor로 경로를 batch 처리한다.
- 200~1,000건 단위로 임시 테이블에 적재한 뒤 원자적으로 교체한다.

### P2-4. 재생성 이력과 품질 통계 부족

성공 건수만 출력하며 누락률, NULL 행, 프로젝트별 장애물 수, 벡터 norm, 각 차원 min/max/mean, zero vector 수를 검증하지 않는다.

권장:

- build run ID, source snapshot ID, config hash, encoder git revision을 저장한다.
- 차원별 통계와 이상치 보고서를 생성한다.
- 저장 전 vector 길이 24, 모든 값 finite, norm≈1을 assert한다.

### P2-5. HNSW 인덱스의 현재 활용성 불명확

현재 재정렬은 Context Vector 후보를 GUID join으로 읽고 C#에서 cosine을 계산하므로 HNSW가 사용되지 않는다. 향후 context ANN을 별도로 할 계획이 없다면 생성 비용과 유지 비용을 재검토해야 한다.

### P2-6. CLI 예시의 평문 비밀번호

소스 상단에 `--password dinno`가 예시로 들어 있다. 실제 운영 비밀번호가 아니더라도 명령 history와 문서에 비밀번호를 남기는 습관을 유도한다.

권장:

- `TOPKGEN_DB_PASSWORD` 환경변수 또는 권한 제한된 `tools.settings.json` 사용을 기본 예시로 한다.
- runtime log에는 접속 문자열이나 비밀번호를 출력하지 않는다.

---

## 6. 권장 v2 설계

## 6.1 벡터를 목적별로 분리

### A. 검색용 Endpoint Environment Vector

신규 경로가 아직 없을 때도 계산할 수 있어야 한다.

권장 입력:

- 시작·종단 좌표
- 시작·종단 PoC 방향 또는 장비 face
- 배관 구경과 required clearance
- 동일 프로젝트/모델 revision의 장애물 AABB/OBB

권장 특징:

- 시작/종단 각각 local 6방향 clearance
- 반경별·종류별 obstacle count histogram
- 가장 넓은 탈출 방향
- 장애물 표면까지의 최근접 거리
- local occupancy signature
- 시작→종단 수평/수직 displacement

이 벡터는 기존 후보 재정렬 전용으로 사용한다.

### B. 실제 기존 설계 학습용 Endpoint Route Response Vector

기존 경로가 장애물 환경에 어떻게 반응했는지를 저장한다.

권장 입력:

- `TB_ROUTE_SEGMENTS`와 `TB_ROUTE_SEGMENT_DETAIL`로 복원한 실제 폴리라인
- 시작에서 첫 2~5m 구간
- 종단에서 역방향 첫 2~5m 구간
- 주변 장애물 표면거리와 장비 anchor

권장 특징:

- 첫 이탈 방향과 첫/두 번째 elbow 방향
- 상승/하강 우회 여부와 높이
- 장애물 통과면(좌/우/상/하)
- 실제 최소 clearance와 평균 clearance
- 시작·종단 stub 길이
- 장애물 경계에 대한 상대 좌표

이 벡터는 선택된 유사 경로에서 stub/waypoint 패턴을 가져오는 단계에 사용한다.

## 6.2 좌표계

권장 local frame:

1. PoC 방향 또는 장비 외향 normal을 local +X로 사용한다.
2. 월드 +Z를 local +Z로 유지한다.
3. local +Y는 `Z × X`로 계산한다.
4. PoC 방향이 없으면 시작→종단 수평방향을 fallback으로 사용한다.
5. 수직 경로처럼 수평방향이 0에 가까우면 장비 face 또는 고정 fallback 규칙을 사용한다.

이렇게 하면 평행 이동과 대부분의 회전에 강한 특징을 만들 수 있다.

## 6.3 데이터 구조 예시

```text
TB_ROUTE_CONTEXT_VECTOR
  PROJECT_ID
  MODEL_REVISION
  ROUTE_PATH_GUID
  VECTOR_KIND              # ENDPOINT_ENV / ENDPOINT_ROUTE_RESPONSE
  CONTEXT_VECTOR
  ENCODER_VERSION
  ENCODER_CONFIG_JSON
  ENCODER_CONFIG_HASH
  SOURCE_SNAPSHOT_ID
  META_JSON
  ENCODED_AT

UNIQUE(PROJECT_ID, MODEL_REVISION, ROUTE_PATH_GUID, VECTOR_KIND, ENCODER_VERSION)
```

---

## 7. 단계별 수정 우선순위

### 1단계: 정확성 및 실행 경로 복구

1. VectorDBGen의 `TB_BIM_OBSTACLES`를 실제 테이블명으로 수정한다.
2. DDL 파일명을 통일한다.
3. 누락된 `ContextVectorEncoder` 의존을 제거하고 인코더 공용 모듈을 만든다.
4. Python과 C# golden parity test를 작성한다.
5. 좌표 6개와 GUID의 NULL/finite 검사를 추가한다.
6. 프로젝트/모델 범위를 필수화한다.

### 2단계: 기하 정확성 개선

1. 중심거리 대신 point-to-AABB surface distance를 사용한다.
2. AABB가 겹치는 모든 grid cell에 등록하거나 검증된 공간 인덱스로 교체한다.
3. deterministic voxel traversal을 적용한다.
4. 장애물 ID를 로드하여 중복 제거한다.
5. 회전된 보는 OBB/중심축으로 방향을 계산한다.

### 3단계: 특징 재설계

1. endpoint 검색 벡터와 actual-route response 벡터를 분리한다.
2. local coordinate frame을 적용한다.
3. presence/distance/direction을 분리한다.
4. 방향별 clearance와 occupancy histogram을 추가한다.
5. 실측 분포로 스케일 상수와 가중치를 결정한다.

### 4단계: 검색 효과 검증

기존의 단일 자기검색 성공 사례만으로는 일반화 성능을 판단하기 어렵다. 최소한 다음 지표로 v1/v2와 context 미사용 baseline을 비교해야 한다.

- 동일 `equipment + utility_group + utility` 후보군에서 Recall@K
- 정답 기존 경로의 Mean Reciprocal Rank
- 시작/종단 좌표 오차
- 첫 이탈방향 및 첫 elbow 일치율
- 장애물 통과면 일치율
- 실제 라우팅 적용 후 충돌률과 추가 우회길이
- 프로젝트를 분리한 hold-out 검증

동일 경로의 좌표로 자기 자신을 검색하면 거의 같은 벡터가 나오는 것은 구현 sanity check일 뿐, 신규 위치의 설계 일반화 성능 검증은 아니다.

---

## 8. 필수 테스트 목록

### 단위 테스트

- PoC가 AABB 내부에 있을 때 표면거리 0
- 매우 긴 보의 끝이 반경 안이고 중심은 반경 밖인 경우
- 음수 좌표와 grid 경계 좌표
- 장애물이 전혀 없는 경우
- 같은 장애물이 시작/종단 반경에 동시에 포함되는 경우
- 45도 회전 보
- 수직 시작→종단 경로
- 200개가 넘는 경로 셀
- Y/Z NULL, NaN, Infinity 입력
- 중복 GUID와 공백 포함 GUID

### Python/C# parity 테스트

- 동일 장애물 목록과 좌표로 24차원 전체 비교
- 장애물 없음/기둥만/보만/혼합 장면
- AABB 경계가 정확히 반경에 닿는 장면
- 긴 경로와 대각선 경로
- 허용오차 `abs(py[i]-cs[i]) <= 1e-6`

### 통합 테스트

- 프로젝트 A 경로에 프로젝트 B 장애물이 섞이지 않는지 확인
- 재생성 실패 시 기존 테이블이 유지되는지 확인
- encoder version이 다른 벡터를 잘못 비교하지 않는지 확인
- 실제 TopK rerank에서 context on/off 순위 비교
- VectorDBGen UI와 CLI가 동일 행 수와 동일 벡터를 생성하는지 확인

---

## 9. 최종 판단

`ExtractObstacleContextVector.py`는 “아직 경로가 없는 신규 요청에서도 계산 가능한 환경 유사도”를 별도 특징으로 만든다는 핵심 발상은 좋다. 다만 현재 24D는 실제 장애물 형상보다 중심점 분포에 가깝고, Tier3는 실제 기존 경로를 반영하지 않으며, Python/C# 및 UI 실행 경로가 완전히 일치하지 않는다.

따라서 다음 원칙으로 개편하는 것이 가장 안전하다.

> 검색 전에는 시작·종단의 **장애물 공간 자체**를 비교하고, 후보 선택 후에는 기존 경로의 시작·종단 실제 폴리라인에서 추출한 **장애물 대응 방식**을 별도로 재사용한다.

이 두 정보를 한 벡터에 섞지 않고 목적별로 분리하면 검색 가능성, 학습 가치, 설명 가능성, 운영 재현성을 모두 확보할 수 있다.
