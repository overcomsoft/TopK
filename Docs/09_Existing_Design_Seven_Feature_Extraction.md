# [설계 개발 문서] 09. 기존 설계경로 7종 특징 데이터 추출

## 업데이트 내용 및 일시

- **최종 업데이트 일시**: 2026-07-22 KST
- **분석 기준 자료**: `C:\Users\overcom\Documents\특정점7개.pdf`
- **참조 문서 양식**: `Docs/01_Vector_Topology_StartEndDirection.md`
- **업데이트 대상 코드**:
  - `Tools/Extract_Design_Pattern.py`
  - `Tools/ExtractObstacleContextVector.py`, `Tools/context_vector_encoder.py`
  - `Tools/PathSegmenter.py`
  - `Tools/ExportGroupPattern.py`
  - `Tools/ExtractStubPatterns.py`
  - `Tools/BuildUtilityPipeGroupVectors.py`, `Tools/utility_pipe_group_encoder.py`
  - `Tools/ExtractBendFeaturePoints.py`, `Tools/geometry_ip_restore.py`
- **업데이트 내용**:
  - 기존 설계경로로부터 생성되는 7종 특징 데이터의 입력, 핵심 알고리즘, 저장 구조 및 자동경로 탐색 활용 방법을 현재 소스코드 기준으로 정리했습니다.
  - 2026-07-22: 각 항목별 처리 함수, 상세 판정 규칙, 데이터 품질 조건, 실패 처리 및 운영 검증 절차를 추가했습니다.
  - 본 문서에서 “7개 특징점”은 PDF의 7개 항목, 즉 **7종 특징 데이터**를 의미합니다. 일곱 번째 항목인 Bend Feature Point는 경로마다 0개 이상 생성되며 정확히 7개의 점을 고정 추출하지 않습니다.
  - 테이블 구조는 대표 컬럼 중심으로 기재했습니다. 실제 운영 스키마에는 인덱스, 생성시각, build provenance 등의 보조 컬럼이 추가될 수 있습니다.

---

## 1. 목적

기존 설계경로에는 단순한 시작점과 종점뿐 아니라 다음과 같은 설계자의 반복 판단이 포함되어 있습니다.

1. 시작/종점 접근 방향과 경로의 전체 형상
2. 시작/종점 주변 장애물 환경
3. 장비 접속부, 공통 trunk, 종단 접속부의 구간 분할
4. 여러 배관이 함께 이동하는 그룹 배열
5. 장비 및 덕트 접속부의 Stub 형상
6. 동일 장비에서 출발하는 UtilityPipeGroup의 대표 형상과 배치
7. 경로 꺾임점의 위치, 전환 유형 및 발생 원인

본 개발의 목적은 위 설계 지식을 DB에 재사용 가능한 벡터, 형상, 패턴, 특징점으로 저장하고 신규 자동경로 탐색 시 다음 단계에 활용하는 것입니다.

```text
기존 설계경로
  -> 7종 특징 추출 및 패턴 집계
  -> 동일 equipment + utility_group + utility 후보 검색
  -> 시작/종점 제약 및 장애물 문맥 재정렬
  -> Stub/Trunk/Bend 가이드 생성
  -> Routing3D 또는 RubberBand 탐색 및 충돌 검증
```

## 2. 입력 데이터

### 2.1 공통 입력 테이블

| 테이블 | 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|---|
| `TB_ROUTE_PATH` | `ROUTE_PATH_GUID` | text | `route-001` | 경로 식별자이며 모든 특징 테이블의 연결 키입니다. |
| `TB_ROUTE_PATH` | `SOURCE_POSX/Y/Z` | double precision | `(1200, 3400, 800)` | 기존 경로의 시작 PoC 좌표(mm)입니다. |
| `TB_ROUTE_PATH` | `TARGET_POSX/Y/Z` | double precision | `(8500, 3400, 2100)` | 기존 경로의 종단 PoC 좌표(mm)입니다. |
| `TB_ROUTE_PATH` | `PROCESS_NAME` | text | `CVD` | 공정 범위 필터에 사용합니다. |
| `TB_ROUTE_PATH` | `EQUIPMENT_NAME` 또는 장비 식별 컬럼 | text | `EQ-CVD-01` | 경로가 속한 장비/프로젝트 범위를 식별합니다. |
| `TB_ROUTE_PATH` | `UTILITY_GROUP` | text | `EXHAUST` | Top-K 후보의 주요 범주 키입니다. |
| `TB_ROUTE_PATH` | `UTILITY` | text | `WET_EXH` | 세부 유틸리티 코드입니다. |
| `TB_ROUTE_PATH` | `SIZE` | text | `100A` | 배관 직경 및 그룹 size signature에 사용합니다. |
| `TB_ROUTE_SEGMENTS` | route/segment 순서 컬럼 | integer/text | `segment_no=3` | 하나의 경로를 구성하는 선분의 순서를 제공합니다. |
| `TB_ROUTE_SEGMENT_DETAIL` | X/Y/Z 좌표 및 detail 순서 | double precision/integer | `(2500, 3400, 1200)` | 선분 상세점을 연결해 3D 중심선 polyline을 복원합니다. |

### 2.2 특징별 추가 입력

| 특징 | 입력 테이블/데이터 | 컬럼 | 타입 | 예시 | 설명 |
|---:|---|---|---|---|---|
| 1 | `TB_ROUTE_FEATURE_OBSTACLE_RELATION` | `ROUTE_PATH_GUID`, 환경 비용 관련 값 | text/double precision | `env_cost=0.18` | 30D 벡터의 장애물 회피 비용 차원에 사용합니다. 이 관계는 같은 파이프라인에서 벡터 저장 전에 계산됩니다. |
| 2 | `TB_BIM_OBSTACLE` | AABB 최소/최대 좌표, 객체 유형/이름 | double precision/text | `min=(0,0,0), max=(500,500,3000)` | 시작/종점 주변 기둥, 보, 벽 등의 공간 문맥을 계산합니다. |
| 3 | `TB_ROUTE_SEGMENT_DETAIL` | point type 또는 연결 가능한 geometry 정보 | text/geometry | `ELBOW`, `TEE` | Stub/Trunk 경계점과 자유점을 판정합니다. |
| 4 | `TB_ROUTE_PATH_SEGMENTATION` | `MIDDLE_TRUNK_GEOM`, `START_STUB_GEOM` | geometry(LineStringZ) | `LINESTRING Z (...)` | 경로 그룹의 공통 trunk와 시작부 정렬을 비교합니다. |
| 5 | `TB_EQUIPMENTS` | 장비 AABB | geometry 또는 좌표 컬럼 | `EQ-CVD-01` AABB | 시작 Stub의 anchor를 결정합니다. |
| 5 | `TB_DUCT`/`TB_DUCT_LATERAL`/`TB_LATERAL_PIPE` | 덕트/배관 anchor AABB | geometry 또는 좌표 컬럼 | `DUCT-17` AABB | 종단 Stub의 anchor와 접속 면을 결정합니다. |
| 6 | `TB_ROUTE_FEATURE_VECTOR` | `FEATURE_VECTOR` | vector(30) | `[0.12,...]` | 그룹 멤버들의 형상 특징 centroid를 만듭니다. |
| 6 | `TB_ROUTE_CONTEXT_VECTOR` | `CONTEXT_VECTOR` | vector(30) | `[0.03,...]` | 그룹 문맥 centroid를 만들며 정밀 재정렬에서 사용합니다. |
| 7 | `TB_ROUTE_GROUP_PATTERN` | `MEMBER_GUIDS`, `PITCH_MM`, `IS_EQUAL_SPACING` | jsonb/double precision/boolean | `250`, `true` | 꺾임 원인이 그룹 정렬 때문인지 판단하는 근거입니다. |
| 7 | 복원된 elbow geometry | 전후 직관 및 elbow 샘플점 | 3D point list | `[(...),(...)]` | `geometry_ip_restore`가 전후 직관의 교차점(IP)을 복원합니다. |

## 3. 핵심 알고리즘

### 3.1 특징 1 - 30D Feature Vector

`Extract_Design_Pattern.py`는 route별 상세점을 순서대로 연결하고 source PoC와 가까운 쪽이 polyline의 시작이 되도록 방향을 보정합니다. 이후 시작/종점 방향, 변위, bounding box, resampling 방향, 전체 길이, 장애물 회피 비용, 방향 문자열 패턴을 30차원으로 구성합니다.

```text
route details 정렬 -> polyline 방향 보정 -> 기하 특징 계산
-> 영역별 가중치 적용 -> L2 정규화 -> vector(30) 저장
```

주요 특징 그룹은 다음과 같습니다.

| 차원 그룹 | 의미 | 자동탐색 효과 |
|---|---|---|
| Start/End topology | 시작 출발 및 종단 접근 단위방향 | 허용 가능한 PoC 접속 방향을 우선합니다. |
| Displacement | 시작-종점 상대 변위 | 유사한 공간 배치의 경로를 찾습니다. |
| Bounding box | 경로 점유 범위 | 우회 규모와 필요한 공간을 비교합니다. |
| Resampled direction | 경로 진행 방향의 분포 | 전체 형상과 굴곡 흐름을 비교합니다. |
| Total length | 정규화된 경로 길이 | 지나치게 긴 후보를 억제합니다. |
| Environment cost | 장애물 근접/회피 비용 | 기존 설계의 회피 난이도를 반영합니다. |
| Arrow pattern | `R/H/D` 기반 방향 통계 | 상승, 수평, 하강 패턴을 비교합니다. |

### 3.2 특징 2 - Context Vector

`ExtractObstacleContextVector.py`는 route 시작점과 종점 주변에 대칭적인 거리 shell을 만들고 `TB_BIM_OBSTACLE` AABB와의 관계를 집계합니다. `context_vector_encoder.py`는 근접도, 상대방향, 수평/수직 관계, 층 통과 수와 같은 환경 통계를 고정 30차원으로 인코딩하고 정규화합니다.

```text
start/end 좌표
  -> 500 mm 및 1000 mm AABB-surface shell 조회
  -> 장애물 유형/방향/거리/정렬 통계
  -> 30D Context Vector
```

Context는 `FEATURE_VECTOR`의 대체물이 아니라 보조 정보입니다. 현재 UtilityPipeGroup 검색에서는 Feature centroid로 ANN 후보를 수집한 뒤 Context centroid로 exact reranking합니다.

### 3.3 특징 3 - Path Segmentation

`PathSegmenter.segment_route()`는 route polyline의 방향 run과 경계 규칙을 이용해 다음 세 구간으로 분할합니다.

1. `START_STUB`: 시작 PoC에서 공통 이동구간에 진입하기 전까지
2. `MIDDLE_TRUNK`: 장거리 공통 이동 및 그룹 정렬의 중심 구간
3. `END_STUB`: 공통 이동구간에서 이탈하여 target PoC로 접근하는 구간

분할 과정에서 짧은 초기 run의 병합, 축방향 스냅, 자유점 위치, 종단 진입 방향을 함께 계산합니다. Bend 추출은 elbow IP 복원으로 점 인덱스가 달라질 수 있으므로 저장된 segmentation을 그대로 재사용하지 않고 복원된 polyline에 `segment_route()`를 다시 적용합니다.

### 3.4 특징 4 - Group/Bundle Pattern

`ExportGroupPattern.py`는 같은 장비 및 유틸리티 범위의 route들을 모으고 `MIDDLE_TRUNK_GEOM`의 유사도와 공간적 근접성을 기준으로 그룹화합니다. 그룹별로 다음 항목을 계산합니다.

- 멤버 수와 평균 유사도
- trunk 대표 높이(`TRUNK_Z`)와 XY spread
- 멤버 간 pitch 평균 및 변동계수
- 등간격 여부와 offset 축
- 직교 bend 개수와 방향 sequence
- 60D 그룹 특징 벡터와 3D 그룹 geometry

### 3.5 특징 5 - Stub Pattern

`ExtractStubPatterns.py`는 시작/종단 PoC와 가장 가까운 장비 또는 덕트 AABB를 anchor로 선택합니다. PoC에 인접한 segmented stub을 source 방향으로 정렬하고 방향 run을 축 방향으로 양자화한 뒤 다음 특징을 생성합니다.

```text
anchor 탐색 -> 최근접 anchor face -> stub 방향 run
-> 짧은 run 병합 -> bend 수/rise/offset/길이
-> 24D FEAT + 3D DIR_UNIT + 대표 stub points
```

동일 `MAIN_EQUIPMENT_NAME + UTILITY_GROUP + UTILITY + SIZE + STUB_KIND + ANCHOR_KIND` 샘플은 template으로 집계됩니다. 신규 경로에서는 template을 신규 PoC와 anchor에 변환하여 시작/종단 Stub 후보를 생성할 수 있습니다.

### 3.6 특징 6 - Utility Pipe Group Vector

`BuildUtilityPipeGroupVectors.py`는 동일 scope에서 `equipment instance + utility_group + utility`가 같은 route를 그룹 멤버로 수집합니다. 멤버 순서를 결정론적으로 정렬하고 다음 대표값을 계산합니다.

- `FEATURE_CENTROID`: 멤버 30D Feature Vector의 성분별 평균 후 L2 정규화
- `CONTEXT_CENTROID`: 유효한 Context Vector의 평균 후 L2 정규화
- `ARRANGEMENT_VECTOR_JSON`: 시작/종점 좌표 분포, 축별 spread, 멤버 간 거리 통계
- 그룹 시작/종점 centroid, 전체 AABB, size signature, vector coverage

그룹 후보는 `utility_group + utility`로 먼저 필터링하며, 장비 instance는 그룹 정체성과 자기 자신 제외에 사용합니다.

### 3.7 특징 7 - Bend Feature Point

`ExtractBendFeaturePoints.py`의 처리 순서는 다음과 같습니다.

```text
route polyline 복원
-> elbow 샘플점을 전후 직관 교차점(IP)으로 복원
-> 복원 polyline 재세그멘테이션
-> 연속 벡터의 cos(angle)로 꺾임 후보 판정
-> 축방향/구간/상대위치 계산
-> 원인 분류
-> 반복 패턴 집계
```

전환 유형은 bend 전후 지배축을 비교하여 `V_TO_H`, `H_TO_V`, `H_TO_H`, `V_TO_V`로 분류합니다. 구간은 `START_STUB`, `MIDDLE_TRUNK`, `END_STUB`이며 상대위치는 각 구간의 누적 길이 비율을 bucket으로 저장합니다.

원인 분류 우선순위는 다음과 같습니다.

1. `ZONE_CONSTRAINT`: Stub/Trunk 구간 경계 또는 구간 구조 때문에 발생
2. `DESTINATION_ENTRY`: target PoC의 진입 방향을 맞추기 위해 발생
3. `OBSTACLE_AVOID`: bend 인근 장애물 AABB 회피와 관련
4. `GROUP_ALIGNMENT`: 그룹 pitch/정렬을 맞추기 위해 발생
5. `UNKNOWN`: 위 근거가 충분하지 않음

집계 키는 장비, 유틸리티, 전환 유형, 구간, 상대위치입니다. 최소 표본 수를 만족한 반복 꺾임만 `TB_ROUTE_BEND_FEATURE_PATTERN`으로 승격합니다.

## 4. 저장 구조

### 4.1 7종 결과 테이블 요약

| 특징 | 저장 테이블 | 대표 컬럼 | 타입 | 예시 | 설명 |
|---:|---|---|---|---|---|
| 1 | `TB_ROUTE_FEATURE_VECTOR` | `ROUTE_PATH_GUID` | text | `route-001` | 원본 경로 키입니다. |
| 1 | `TB_ROUTE_FEATURE_VECTOR` | `FEATURE_VECTOR` | vector(30) | `[0.12,-0.04,...]` | ANN/유사도 검색용 정규화 벡터입니다. |
| 1 | `TB_ROUTE_FEATURE_VECTOR` | `FEATURE_VECTOR_JSON` | jsonb | `[0.12,-0.04,...]` | 검증 및 분석용 동일 벡터입니다. |
| 1 | `TB_ROUTE_FEATURE_VECTOR` | `DIRECTION_PATTERN` | text | `RHHDD` | route 방향 sequence입니다. |
| 1 | `TB_ROUTE_FEATURE_VECTOR` | `TOTAL_LENGTH_MM`, `STEP_COUNT` | double precision/integer | `18340`, `12` | 경로 규모입니다. |
| 2 | `TB_ROUTE_CONTEXT_VECTOR` | `CONTEXT_VECTOR` | vector(30) | `[0.03,0.11,...]` | 시작/종점 장애물 환경 벡터입니다. |
| 2 | `TB_ROUTE_CONTEXT_VECTOR` | scope/build provenance | text/uuid/jsonb | `context-v1` | encoder, build 및 원본 범위를 추적합니다. |
| 3 | `TB_ROUTE_PATH_SEGMENTATION` | `START_STUB_GEOM` | geometry(LineStringZ) | `LINESTRING Z (...)` | 시작 접속부입니다. |
| 3 | `TB_ROUTE_PATH_SEGMENTATION` | `MIDDLE_TRUNK_GEOM` | geometry(LineStringZ) | `LINESTRING Z (...)` | 공통 이동구간입니다. |
| 3 | `TB_ROUTE_PATH_SEGMENTATION` | `END_STUB_GEOM` | geometry(LineStringZ) | `LINESTRING Z (...)` | 종단 접속부입니다. |
| 3 | `TB_ROUTE_PATH_SEGMENTATION` | `START_FREE_POINT`, `END_FREE_POINT` | geometry(PointZ) | `POINT Z (...)` | Stub와 trunk의 연결 후보점입니다. |
| 3 | `TB_ROUTE_PATH_SEGMENTATION` | `END_ENTRY_DIR_X/Y/Z` | double precision | `(0,0,-1)` | target 접근 방향입니다. |
| 4 | `TB_ROUTE_GROUP_PATTERN` | `GROUP_ID`, `MEMBER_GUIDS` | text/jsonb | `grp-01`, `["r1","r2"]` | 그룹과 멤버 경로 목록입니다. |
| 4 | `TB_ROUTE_GROUP_PATTERN` | `PITCH_MM`, `PITCH_CV`, `IS_EQUAL_SPACING` | double precision/boolean | `250`, `0.04`, `true` | 배관 간격 규칙입니다. |
| 4 | `TB_ROUTE_GROUP_PATTERN` | `OFFSET_AXIS`, `TRUNK_Z`, `TRUNK_XY_SPREAD` | text/double precision | `Y`, `2200`, `480` | 그룹 배열과 trunk 위치입니다. |
| 4 | `TB_ROUTE_GROUP_PATTERN` | `FEAT`, `FEAT_JSON` | vector(60)/jsonb | `[0.08,...]` | 그룹 유사도 검색용 벡터입니다. |
| 5 | `TB_ROUTE_STUB_PATTERN` | `PATTERN_ID`, `STUB_KIND`, `ANCHOR_KIND` | text | `sp-01`, `START`, `EQUIPMENT` | 개별 Stub 샘플의 식별 및 종류입니다. |
| 5 | `TB_ROUTE_STUB_PATTERN` | `FACE`, `DIR_SEQ`, `N_BENDS` | text/jsonb/integer | `+X`, `[0,4,0]`, `2` | anchor 면과 Stub 방향 구조입니다. |
| 5 | `TB_ROUTE_STUB_PATTERN` | `RISE_MM`, `OFFSET_MM`, `STUB_LENGTH_MM` | double precision | `500`, `120`, `1800` | Stub 치수 특징입니다. |
| 5 | `TB_ROUTE_STUB_PATTERN` | `FEAT`, `DIR_UNIT`, `STUB_POINTS` | vector(24)/vector(3)/jsonb | `[... ]`, `[1,0,0]` | 검색 및 실제 후보 형상 생성용 데이터입니다. |
| 5 | `TB_ROUTE_STUB_TEMPLATE` | 대표 형상/평균 특징/표본 수 | jsonb/vector/integer | `sample_count=18` | 반복 샘플을 집계한 재사용 template입니다. |
| 6 | `TB_ROUTE_UTILITY_GROUP_VECTOR` | `GROUP_VECTOR_ID`, `MEMBER_COUNT`, `MEMBER_GUIDS` | text/integer/jsonb | `ugv-01`, `4`, `["r1",...]` | UtilityPipeGroup header입니다. |
| 6 | `TB_ROUTE_UTILITY_GROUP_VECTOR` | `FEATURE_CENTROID` | vector(30) | `[0.10,...]` | 1차 ANN 후보 수집용입니다. |
| 6 | `TB_ROUTE_UTILITY_GROUP_VECTOR` | `CONTEXT_CENTROID` | vector(30) | `[0.06,...]` | exact reranking용이며 ANN에는 사용하지 않습니다. |
| 6 | `TB_ROUTE_UTILITY_GROUP_VECTOR` | `ARRANGEMENT_VECTOR_JSON` | jsonb | `{ "axis_stats": ... }` | 그룹 공간배치 특징입니다. |
| 6 | `TB_ROUTE_UTILITY_GROUP_MEMBER` | `MEMBER_ORDER`, `ROUTE_PATH_GUID`, 시작/종점 좌표 | integer/text/double precision | `0`, `route-001` | 그룹 내 결정론적 멤버 순서와 원본 연결입니다. |
| 7 | `TB_ROUTE_BEND_FEATURE_POINT` | `BEND_ID`, `ROUTE_PATH_GUID` | bigint/text | `10021`, `route-001` | 경로별 개별 꺾임점입니다. |
| 7 | `TB_ROUTE_BEND_FEATURE_POINT` | `POINT_3D` | geometry(PointZ) | `POINT Z (2500 3400 1200)` | 복원된 실제/IP 꺾임 좌표입니다. |
| 7 | `TB_ROUTE_BEND_FEATURE_POINT` | `SEGMENT_ZONE`, `REL_POSITION_BUCKET` | text/numeric(3,2) | `MIDDLE_TRUNK`, `0.50` | 경로 구간과 구간 내 상대위치입니다. |
| 7 | `TB_ROUTE_BEND_FEATURE_POINT` | `TRANSITION_TYPE`, `AXIS_BEFORE`, `AXIS_AFTER` | text | `H_TO_V`, `+X`, `+Z` | 꺾임 전후 방향 구조입니다. |
| 7 | `TB_ROUTE_BEND_FEATURE_POINT` | `CAUSE`, `CAUSE_EVIDENCE` | text/jsonb | `OBSTACLE_AVOID`, `{...}` | 추정 원인과 판정 근거입니다. |
| 7 | `TB_ROUTE_BEND_FEATURE_PATTERN` | `SAMPLE_COUNT`, `FREQUENCY_SCORE` | integer/double precision | `24`, `0.72` | 반복 꺾임의 빈도입니다. |
| 7 | `TB_ROUTE_BEND_FEATURE_PATTERN` | `DOMINANT_CAUSE`, `CAUSE_CONFIDENCE` | text/double precision | `GROUP_ALIGNMENT`, `0.83` | 대표 원인과 신뢰도입니다. |

### 4.2 공통 식별 및 provenance 원칙

- 가능한 경우 `PROJECT_SCOPE_KEY + MODEL_REVISION_KEY`로 서로 다른 프로젝트/모델 revision을 분리합니다.
- 개별 route 특징은 `ROUTE_PATH_GUID`로 원본 경로와 연결합니다.
- 그룹 검색의 안정적인 업무 키는 `equipment + utility_group + utility`입니다.
- `BUILD_RUN_ID`, `SOURCE_HASH`, `ENCODER_VERSION`, `ENCODER_CONFIG_HASH`는 재생성 여부와 encoder 호환성을 판정하는 데 사용합니다.
- pgvector 컬럼은 검색용이고 JSON/geometry 컬럼은 분석, 검증, 시각화 및 실제 경로 재구성용입니다.

## 5. 자동경로 탐색에 활용 방법

### 5.1 권장 탐색 순서

| 단계 | 사용 특징 | 처리 내용 | 결과 |
|---:|---|---|---|
| 1 | Utility Pipe Group Vector | `utility_group + utility`로 후보군을 제한하고 `FEATURE_CENTROID` ANN 검색을 수행합니다. | 유사 그룹 Top-K |
| 2 | Context Vector | 신규 시작/종점 주변 Context와 후보 `CONTEXT_CENTROID`를 exact 비교합니다. | 현장 장애물 문맥에 맞는 순위 재정렬 |
| 3 | 30D Feature Vector | 개별 멤버의 시작/종점 방향, 변위, 길이 및 전체 형상을 비교합니다. | 신규 PoC 배치와 유사한 route 선택 |
| 4 | Group/Bundle Pattern | pitch, offset 축, trunk 높이 및 멤버 순서를 신규 그룹에 매핑합니다. | 그룹 초기 배치 및 trunk corridor |
| 5 | Stub Pattern | source/target anchor 면과 방향에 맞는 Stub template을 변환합니다. | 시작/종단 고정 경로와 free point |
| 6 | Path Segmentation | 두 free point 사이를 middle trunk 탐색 문제로 분리합니다. | 탐색 범위 단순화 및 구간별 제약 |
| 7 | Bend Feature Point | 빈도가 높고 신뢰도가 높은 전환 유형/상대위치를 soft waypoint 또는 비용 bias로 적용합니다. | 설계자와 유사한 bend sequence |
| 8 | Routing 엔진 | 후보를 seed/guidance로 사용해 충돌검사와 비용탐색을 수행합니다. | 충돌 없는 최종 자동경로 |

### 5.2 적용 원칙

- 학습 경로의 geometry를 그대로 복사하지 않고 신규 PoC, anchor, 장애물 좌표계에 맞게 변환합니다.
- Stub의 접속 방향과 장비/덕트 clearance는 hard constraint로 취급합니다.
- Group pitch, trunk 높이, Bend 상대위치 및 원인 패턴은 가중 비용 또는 soft waypoint로 사용합니다.
- `OBSTACLE_AVOID` bend는 과거 좌표를 고정점으로 쓰지 않고 신규 장애물 문맥에서 회피 방향을 재검증합니다.
- `GROUP_ALIGNMENT` bend는 단일 배관보다 그룹 전체 pitch 유지 여부를 우선 평가합니다.
- Context Vector는 후보 수집보다 재정렬에 사용하여, 형상은 유사하지만 장애물 환경이 다른 경로가 상위에 고정되는 문제를 줄입니다.
- 최종 후보는 반드시 실제 배관 반경, 여유거리, 장비 AABB 및 장애물 geometry로 충돌 검증합니다.

### 5.3 활용 예시

```text
입력:
  equipment=EQ-CVD-01
  utility_group=EXHAUST
  utility=WET_EXH
  source/target PoC + 신규 장애물 AABB

1) 동일 utility_group + utility의 UtilityPipeGroup Top-K 검색
2) 신규 Context Vector로 후보 reranking
3) 선택 그룹의 size/order/pitch를 신규 PoC에 정렬
4) source/target Stub template으로 고정 접속부 생성
5) Bend pattern의 빈번한 H_TO_V 위치를 soft waypoint로 설정
6) middle trunk를 Routing3D/RubberBand로 탐색
7) 충돌 시 장애물 회피 bend를 신규 환경에서 재계산
8) 최종 route와 선택 template/build provenance 저장
```

### 5.4 검증 항목

- 모든 polyline이 source PoC에서 target PoC 방향으로 정렬되었는지 확인합니다.
- `FEATURE_VECTOR`와 `CONTEXT_VECTOR` 길이가 각각 30인지 확인합니다.
- Segmentation 세 구간이 누락/역전 없이 원본 route를 덮는지 확인합니다.
- Group의 `MEMBER_COUNT`와 `MEMBER_GUIDS` 길이가 일치하는지 확인합니다.
- Stub의 anchor face, 방향 sequence 및 실제 PoC 접속성이 일치하는지 확인합니다.
- Bend IP 복원점의 skew distance가 허용오차 이내인지 확인합니다.
- Bend pattern의 `FREQUENCY_SCORE`, `CAUSE_CONFIDENCE`, 표본 수가 적용 임계값을 만족하는지 확인합니다.
- 자동경로 결과는 벡터 유사도와 별개로 최종 3D 충돌검사를 통과해야 합니다.

---

## 6. 7개 추출 항목별 상세 개발 명세

이 절은 개발자가 각 추출기를 독립적으로 구현, 실행, 검증할 수 있도록 항목별 계약을 상세히 설명합니다.

### 6.1 항목 1 - 30D 특징벡터(Feature Vector)

#### 6.1.1 목적과 실행 코드

| 구분 | 내용 |
|---|---|
| 주 실행 파일 | `Tools/Extract_Design_Pattern.py` |
| 중심 클래스 | `DesignFeatureLearner` |
| 입력 적재 | `load_data()` |
| 장애물 관계 선행 계산 | `load_obstacles_for_routes()`, `save_obstacle_relations()` |
| 벡터 생성/저장 | `save_route_similarity_vectors()` |
| 전체 실행 순서 | `learn_and_save()` |

목적은 기존 경로의 시작/종단 토폴로지와 전체 공간 형상을 같은 30차원 공간에 투영하여 pgvector 유사도 검색이 가능하게 만드는 것입니다.

#### 6.1.2 입력 계약

| 입력 | 필수 조건 | 품질 조건 | 결측 시 처리 |
|---|---|---|---|
| route 메타데이터 | `ROUTE_PATH_GUID` 존재 | GUID가 경로별로 유일해야 함 | 해당 route 제외 또는 적재 실패 |
| source/target PoC | X/Y/Z 모두 숫자 | polyline 양 끝과 논리적으로 연결 | 방향 보정 신뢰도 저하 |
| segment detail | 최소 2개의 서로 다른 3D 점 | segment/detail 순서가 보존되어야 함 | 벡터 생성 제외 |
| 장애물 관계 | route별 0개 이상 | 벡터 생성 전에 계산 완료 | 환경 비용 차원이 0 또는 기본값 |

`load_data()`는 route별 점을 정렬하고 source PoC와 첫 점/마지막 점의 거리를 비교합니다. 마지막 점이 source에 더 가까우면 점 목록을 뒤집습니다.

```text
if distance(source, last) < distance(source, first):
    reverse(points)
```

#### 6.1.3 차원 생성 규칙

| 차원 범주 | 계산 기준 | 정규화 의미 |
|---|---|---|
| 시작 방향 | `normalize(p1 - p0)` | source에서 route 외부로 나가는 방향 |
| 종단 방향 | `normalize(p(n-1) - pn)` | target에서 route 내부를 바라보는 방향 |
| 변위 | target-source 상대좌표 | PoC 간 배치 형태 |
| 공간 범위 | polyline AABB와 축별 span | 우회 폭과 높이 |
| 재표본 방향 | `resample_polyline_points()` 결과의 방향 변화 | 점 개수와 무관한 형상 비교 |
| 길이/step | 누적 3D 길이와 방향 run 수 | 경로 복잡도 |
| 환경 비용 | 저장된 route-obstacle 관계 | 장애물 회피 부담 |
| 방향 패턴 | `compute_direction_pattern()` | 상승/수평/하강의 순차 구조 |

각 그룹의 가중치를 차원 수에 맞게 scale한 뒤 전체 벡터를 L2 정규화합니다. 따라서 검색 시 cosine distance를 사용할 수 있으며, 원시 길이나 좌표 단위가 특정 차원을 과도하게 지배하지 않습니다.

#### 6.1.4 저장 및 Upsert

`ROUTE_PATH_GUID` 충돌 시 기존 행을 갱신합니다. `FEATURE_VECTOR`는 ANN 검색에, `FEATURE_VECTOR_JSON`은 사람이 차원별 값을 검토하거나 encoder 간 결과를 비교하는 데 사용합니다. 기존 DB에 JSON 컬럼이 없으면 `prepare_tables()`가 컬럼을 추가합니다.

#### 6.1.5 자동탐색 적용과 검증

- query route에도 동일한 점 방향, 부호, 가중치, L2 정규화 규칙을 적용해야 합니다.
- 후보 수집 전 `UTILITY_GROUP`, `UTILITY` 등의 범주 필터를 적용합니다.
- 벡터 길이 30, 모든 값의 finite 여부, L2 norm 근사값 1.0을 확인합니다.
- source/target이 바뀐 동일 경로가 상위 후보로 잘못 나타나는지 방향 반전 테스트를 수행합니다.

### 6.2 항목 2 - 장애물 Context Vector

#### 6.2.1 목적과 실행 코드

| 구분 | 내용 |
|---|---|
| 주 실행 파일 | `Tools/ExtractObstacleContextVector.py` |
| 인코더 | `Tools/context_vector_encoder.py` |
| 장애물 인덱스 | `load_obstacle_index()`, `ObstacleIndex` |
| endpoint 인코딩 | `encode_endpoint()` |
| 전체 인코딩 | `encode_context_vector()` |
| 저장 | `save_context_vectors()` |

동일한 모양의 경로라도 주변 기둥, 보, 벽, 장비 배치가 다르면 재사용 가능성이 달라집니다. Context Vector는 이 차이를 별도 30D 벡터로 표현합니다.

#### 6.2.2 상세 처리

1. scope/revision을 확정하고 해당 장애물 snapshot을 읽습니다.
2. 장애물 AABB를 3D uniform grid 공간 인덱스에 적재합니다.
3. 시작점과 종점 각각에서 AABB 표면까지의 거리로 근접 장애물을 조회합니다.
4. 가까운 shell과 먼 shell을 구분하고 장애물 개수, 최근접 거리, 방향성, 정렬도를 집계합니다.
5. 시작-종점 연결선이 지나가는 2D grid cell을 제한된 수로 순회해 경로 corridor의 장애물 통과 문맥을 계산합니다.
6. 30D로 결합한 후 L2 정규화합니다.

점-장애물 거리는 장애물 중심점이 아니라 AABB의 최근접 표면점을 기준으로 합니다. 큰 장애물에서 중심점 거리 때문에 근접도가 왜곡되는 문제를 방지합니다.

#### 6.2.3 저장 계약

| 컬럼 범주 | 의미 | 활용 |
|---|---|---|
| route/scope/revision 키 | 어떤 경로와 모델 snapshot의 문맥인지 식별 | 다른 revision 혼용 방지 |
| `CONTEXT_VECTOR` | 정규화 30D 문맥 | reranking |
| endpoint/tier 통계 JSON | 거리 shell별 원시 집계 | 진단 및 encoder 검증 |
| obstacle snapshot hash | 입력 장애물 집합의 변경 감지 | stale 판단 |
| encoder version/config | 차원 의미의 호환성 | 재생성 판단 |

#### 6.2.4 자동탐색 적용과 검증

- 1차 ANN은 형상 Feature Vector로 수행하고 Context는 후보 집합에 대한 exact score로 반영합니다.
- 신규 PoC 주변에 장애물이 전혀 없을 때는 0벡터/기본 통계 처리 규칙을 기존 경로 생성 규칙과 일치시켜야 합니다.
- 장애물 snapshot hash가 현재 모델과 다르면 해당 Context Vector를 stale로 처리합니다.
- 시작점과 종점이 동일하거나 NaN 좌표인 경우 저장하지 않고 오류로 분류합니다.

### 6.3 항목 3 - Path Segmentation

#### 6.3.1 목적과 실행 코드

| 구분 | 내용 |
|---|---|
| 주 실행 파일 | `Tools/PathSegmenter.py` |
| 핵심 함수 | `segment_route()` |
| 경로 일괄 적재 | `load_route_data_bulk()` |
| point type 판정 | `get_point_type()` |
| DB 적재 | `run_segmentation()` |

경로 전체를 한 번에 학습하지 않고 장비 접속부와 공통 이동부를 분리하여, 서로 다른 재사용 규칙과 탐색 제약을 적용하는 것이 목적입니다.

#### 6.3.2 경계 판정 규칙

- 각 연속점 차이를 `axis_snap()`으로 6개 축방향에 매핑합니다.
- 시작점부터 최초 유효 방향 run과 꺾임/연결 이벤트를 찾아 시작 Stub 경계를 결정합니다.
- 종단에서도 같은 논리를 역방향으로 적용하여 End Stub 경계를 결정합니다.
- 두 경계 사이를 Middle Trunk로 정의합니다.
- 짧거나 중복된 점 때문에 구간 길이가 0이 되지 않도록 최소 점 수와 거리 조건을 적용합니다.
- target 쪽 마지막 유효 선분을 정규화하여 `END_ENTRY_DIR_X/Y/Z`로 저장합니다.

#### 6.3.3 반환 및 저장 데이터

```text
segment_route(points)
  -> start_stub_points
  -> middle_trunk_points
  -> end_stub_points
  -> start_free_point
  -> end_free_point
  -> end_entry_direction
```

세 geometry는 원본 점 순서를 유지해야 하며 인접 구간의 경계점은 연결성을 위해 공유될 수 있습니다. `START_FREE_POINT`와 `END_FREE_POINT`는 신규 경로에서 Stub을 고정한 뒤 middle routing을 시작/종료할 수 있는 좌표입니다.

#### 6.3.4 자동탐색 적용과 검증

- Stub은 template 기반 또는 hard constraint로 먼저 배치하고 free point 사이만 탐색합니다.
- `start + middle + end`를 중복 경계점 하나만 남기고 합쳤을 때 원본 polyline과 동일한지 검사합니다.
- 짧은 경로에서 Start/End Stub이 겹치는 경우 middle이 비어 있을 수 있으므로 별도 상태로 처리합니다.
- elbow IP 복원 후에는 기존 point index가 달라지므로 Bend 추출 단계에서 반드시 재세그멘테이션합니다.

### 6.4 항목 4 - Group/Bundle Pattern

#### 6.4.1 목적과 실행 코드

| 구분 | 내용 |
|---|---|
| 주 실행 파일 | `Tools/ExportGroupPattern.py` |
| 개별 특징 | `extract_pipe_feature()` |
| 경로 유사도 | `compute_similarity()` |
| 평행 중첩 판정 | `check_parallel_overlap()` |
| 간격 규칙 | `compute_offset_regularity()` |
| 그룹 분석 | `analyze_patterns()` |
| 저장 | `save_bundle_patterns()` |

여러 배관이 공통 trunk를 형성하면서 일정 간격을 유지하는 설계 규칙을 그룹 단위로 추출합니다.

#### 6.4.2 그룹 생성 규칙

1. 장비/유틸리티 범위로 route를 분할합니다.
2. 각 route의 middle trunk를 재표본화하고 방향 sequence와 직교 bend를 추출합니다.
3. 경로 간 방향 유사도와 평행 구간의 실제 중첩 길이를 계산합니다.
4. 조건을 만족한 route 쌍을 union-find로 결합합니다.
5. 그룹 내 멤버 offset을 대표축에 투영하여 pitch 평균과 변동계수를 계산합니다.
6. trunk 높이, 공간 spread, elbow 위치 및 60D 특징을 집계합니다.

단순히 두 경로가 평행하다는 사실만으로 그룹화하지 않고, 일정 길이 이상 실제로 나란히 진행하는지와 pitch 규칙성을 함께 확인합니다.

#### 6.4.3 자동탐색 적용과 검증

- `MEMBER_GUIDS`는 중복 없이 `N_MEMBERS`와 개수가 일치해야 합니다.
- `PITCH_CV`가 허용 임계값 이내일 때만 `IS_EQUAL_SPACING=true`로 사용합니다.
- 신규 그룹의 기준 배관을 먼저 생성하고 `OFFSET_AXIS`, `PITCH_MM`, 멤버 순서로 나머지 경로의 corridor를 배치합니다.
- 그룹 geometry를 그대로 이동한 뒤에는 각 멤버별 충돌검사를 다시 수행합니다.

### 6.5 항목 5 - Stub Pattern

#### 6.5.1 목적과 실행 코드

| 구분 | 내용 |
|---|---|
| 주 실행 파일 | `Tools/ExtractStubPatterns.py` |
| route/점 적재 | `fetch_routes()`, `fetch_route_points()` |
| anchor 적재 | `fetch_anchors()` |
| 샘플 생성 | `make_sample()` |
| 방향 run | `dir_runs()`, `merge_short_runs()` |
| 특징 인코딩 | `build_feature()` |
| template 집계 | `build_templates()` |
| 신규 후보 변환 | `instantiate_stub()`, `make_stub_candidates()` |

Stub은 PoC와 장비/덕트 anchor에 직접 접속하므로 자동탐색 전체에서 가장 강한 국부 제약 중 하나입니다.

#### 6.5.2 Anchor 및 특징 계산

- 장비명/utility hint로 anchor 후보를 먼저 좁힙니다.
- PoC가 AABB 내부 또는 표면 1mm 범위에 있으면 해당 anchor를 우선합니다.
- 그렇지 않으면 설정된 최대거리 이내의 최근접 AABB를 fallback으로 선택합니다.
- `nearest_face()`로 PoC와 가장 가까운 AABB 면을 6축 face로 분류합니다.
- `relative_pos()`로 PoC의 AABB 내부 상대좌표를 `[0,1]^3`에 저장합니다.
- Stub 점을 PoC 기준으로 정방향 정렬하고 짧은 방향 run을 병합합니다.
- face one-hot, 방향 sequence, anchor 상대위치, 출발 단위방향을 24D `FEAT`로 구성합니다.

#### 6.5.3 Pattern과 Template 구분

| 구조 | 의미 | 사용 시점 |
|---|---|---|
| `TB_ROUTE_STUB_PATTERN` | 기존 route에서 얻은 개별 START/END Stub 표본 | 분석, provenance, 재집계 |
| `TB_ROUTE_STUB_TEMPLATE` | 동일 업무 키의 대표 Stub과 평균 특징 | 신규 경로 후보 생성 |
| `TB_ROUTE_STUB_APPLICATION_LOG` | 어떤 template을 신규 요청에 적용했는지 기록 | 운영 추적 및 실패 분석 |

#### 6.5.4 자동탐색 적용과 검증

- START template은 source PoC에서 바깥쪽으로, END template은 target PoC에서 route 내부 방향으로 변환합니다.
- anchor face 법선과 첫 방향축이 맞지 않는 후보는 제거합니다.
- 생성된 모든 Stub 점은 anchor clearance와 obstacle collision을 검사합니다.
- template 표본 수가 적거나 방향 분산이 큰 경우 hard constraint가 아니라 낮은 가중치 후보로 사용합니다.

### 6.6 항목 6 - Utility Pipe Group Vector

#### 6.6.1 목적과 실행 코드

| 구분 | 내용 |
|---|---|
| 주 실행 파일 | `Tools/BuildUtilityPipeGroupVectors.py` |
| 인코더 | `Tools/utility_pipe_group_encoder.py` |
| geometry 적재 | `load_geometry_points()` |
| 멤버 적재 | `load_source_members()` |
| 그룹 계산 | `compute_groups()` |
| 배치 특징 | `build_arrangement()` |
| centroid | `normalized_centroid()` |
| 저장/검증 | `save_groups()`, `validate_groups()` |

개별 route 검색을 넘어 같은 장비에서 함께 출발하는 배관 집합 전체를 한 개의 Top-K 검색 단위로 만드는 것이 목적입니다.

#### 6.6.2 그룹 Identity와 멤버 조건

```text
identity = project_scope + model_revision + process
         + equipment_instance + utility_group + utility
```

- 최소 멤버 수를 만족하지 못하면 그룹 벡터를 만들지 않습니다.
- 멤버는 정규화된 size, 시작/종점 좌표 및 GUID 기준의 안정적인 순서로 정렬합니다.
- 유효한 30D Feature Vector가 있는 멤버만 Feature centroid에 포함합니다.
- encoder가 호환되는 Context Vector만 Context centroid에 포함합니다.
- coverage는 전체 멤버 중 유효 벡터가 있는 비율입니다.

#### 6.6.3 Arrangement 데이터

| 값 | 계산 | 자동탐색 의미 |
|---|---|---|
| 시작/종점 centroid | 멤버 좌표 평균 | 그룹 전체의 기준점 |
| 축별 min/max/mean/std | 시작/종점 및 geometry 좌표 | 배치 폭과 높이 |
| 멤버 간 거리 통계 | 결정론적 멤버 순서의 좌표 거리 | pitch/분산 비교 |
| AABB | 모든 멤버 geometry의 범위 | 그룹 corridor 크기 |
| size signature | size별 멤버 수 | 신규 요청과 관경 구성 비교 |

#### 6.6.4 검색 및 저장 상태

- `STATUS=BUILDING`으로 header를 만들고 header/member 저장이 성공하면 `READY`로 전환합니다.
- 원본 또는 encoder config가 바뀐 기존 그룹은 `STALE`로 처리합니다.
- ANN 인덱스는 `FEATURE_CENTROID`에만 적용합니다.
- `CONTEXT_CENTROID`, size, arrangement, coverage는 ANN 결과에 대한 exact reranking에 사용합니다.

### 6.7 항목 7 - Bend Feature Point

#### 6.7.1 목적과 실행 코드

| 구분 | 내용 |
|---|---|
| 주 실행 파일 | `Tools/ExtractBendFeaturePoints.py` |
| route/장애물/그룹 적재 | `fetch_routes()`, `load_obstacles()`, `load_group_pitch_index()` |
| 후보 추출 | `extract_candidates()` |
| 구간/전환 판정 | `classify_zone()`, `classify_transition()` |
| 원인 판정 | `classify_cause()` |
| 반복 패턴 | `aggregate_patterns()` |
| 저장 | `insert_points()`, `insert_patterns()` |
| 운영 명령 | `build()`, `status()`, `validate()` |

기존 설계자가 경로의 어느 구간에서 어떤 방향으로 왜 꺾었는지를 구조화하여, 신규 탐색의 soft waypoint와 비용 편향으로 활용합니다.

#### 6.7.2 IP 복원과 후보 검출

elbow가 arc 또는 여러 샘플점으로 저장된 경우 샘플점 각각을 bend로 세면 동일한 elbow가 여러 번 검출됩니다. `geometry_ip_restore`는 elbow 앞뒤의 직선 run을 연장해 가장 가까운 교차점(IP)을 계산하고 하나의 대표 꺾임점으로 복원합니다.

복원된 각 내부점 `pi`에서 다음 두 벡터를 계산합니다.

```text
v_before = pi - p(i-1)
v_after  = p(i+1) - pi
cos_theta = dot(v_before, v_after) / (|v_before| * |v_after|)
```

거의 직선인 점은 제외하고 방향 변화가 임계값을 넘는 점만 Bend 후보로 만듭니다. 0길이 선분은 각도 계산에서 제외합니다.

#### 6.7.3 세부 분류 규칙

| 분류 | 규칙 | 저장값 예시 |
|---|---|---|
| 방향축 | 절대값이 가장 큰 XYZ 성분과 부호 | `+X`, `-Z` |
| 전환유형 | 전후 축이 수평(X/Y)인지 수직(Z)인지 조합 | `H_TO_V` |
| 구간 | 재세그멘테이션한 경계 index | `MIDDLE_TRUNK` |
| 상대위치 | 해당 구간의 누적거리/전체 구간거리 | `0.50` |
| 순번 | 시작/종단 양쪽에서 센 bend 순서 | `2`, `4` |
| 복원 여부 | 원본점인지 가상 IP인지 | `IS_ELBOW_RESTORED_IP=true` |

원인 분류는 증거가 중복될 때 우선순위를 적용합니다. `GROUP_ALIGNMENT`는 단순 평행 여부가 아니라 해당 bend의 Z가 그룹 `TRUNK_Z` 허용오차 안에 있고 pitch 근거가 있어야 합니다. 시작 Stub을 무조건 `DESTINATION_ENTRY`로 분류하지 않으며 source-facing anchor face와 실제 방향축이 일치해야 합니다.

장애물 판정은 모든 obstacle을 매 bend마다 전수검사하지 않습니다. 2,000mm 3D uniform grid에서 주변 후보 AABB를 얻은 뒤 `segment_aabb_distance()`로 실제 근접도를 계산합니다. 너무 커서 4,096개를 초과하는 cell에 걸친 AABB는 overflow 목록에 유지하여 항상 검사합니다.

#### 6.7.4 Pattern 집계

| 집계값 | 산식/의미 |
|---|---|
| `SAMPLE_COUNT` | 동일 pattern을 가진 고유 route 수 |
| `BEND_INSTANCE_COUNT` | 동일 pattern에 속한 전체 bend 수 |
| `FREQUENCY_SCORE` | `SAMPLE_COUNT / TOTAL_ROUTES_IN_SCOPE` |
| `DOMINANT_CAUSE` | 원인별 count가 가장 큰 값 |
| `CAUSE_CONFIDENCE` | dominant cause count / 전체 instance 수 |
| `POSITION_CONSISTENCY` | 상대위치 또는 실제 좌표 분산 기반 일관성 |
| 대표/평균점 | 멤버 좌표의 representative 및 평균 geometry |

`SOURCE_HASH`는 provenance용이며 증분 skip 조건이 아닙니다. 현재 build는 대상 scope/revision의 Bend Point와 Pattern을 다시 계산해 교체합니다.

#### 6.7.5 자동탐색 적용과 검증

- `FREQUENCY_SCORE`와 `CAUSE_CONFIDENCE`가 모두 임계값을 넘는 pattern만 guidance로 사용합니다.
- `DESTINATION_ENTRY`는 target 방향 hard constraint 보조에, `ZONE_CONSTRAINT`는 Stub/Trunk 경계에 우선 적용합니다.
- `OBSTACLE_AVOID`는 신규 장애물 위치가 달라질 수 있으므로 방향 힌트만 사용하고 좌표를 고정하지 않습니다.
- `GROUP_ALIGNMENT`는 그룹 pitch와 trunk 높이를 동시에 유지할 수 있을 때 적용합니다.
- focused test는 `Tools` 폴더에서 `..\.venv\Scripts\python.exe -m unittest tests.bend_feature_point_tests`로 실행합니다.
- DB 연결이 없는 단위 테스트 통과는 실제 build/status/validate와 UNKNOWN 비율 검증을 대체하지 않습니다.

## 7. 추출 파이프라인 실행 의존성

권장 생성 순서는 다음과 같습니다.

```text
1. 원본 route polyline 및 scope 준비
2. 30D Feature Vector와 route-obstacle relation 생성
3. Context Vector 생성
4. Path Segmentation 생성
5. Group/Bundle Pattern 생성
6. Stub Pattern 및 Template 생성
7. Utility Pipe Group Vector 생성
8. Bend Feature Point 및 Pattern 생성
9. status/validate와 DB 무결성 검사
```

| 선행 결과 | 후행 추출기 | 의존 이유 |
|---|---|---|
| route-obstacle relation | 30D Feature Vector | 환경 비용 차원 계산 |
| Path Segmentation | Group/Stub | trunk와 접속부 분리 |
| Feature/Context Vector | Utility Pipe Group Vector | 그룹 centroid 계산 |
| Group Pattern | Bend Feature Point | `GROUP_ALIGNMENT` 원인 근거 |
| IP 복원 polyline | Bend 재세그멘테이션 | 원본 point index를 재사용할 수 없음 |

## 8. 운영 완료 기준

다음 조건을 모두 만족해야 7종 특징 추출이 완료된 것으로 판단합니다.

- scope/revision별 원본 route 수와 각 특징 테이블 coverage가 보고됩니다.
- 모든 vector의 차원, finite 값 및 encoder version이 유효합니다.
- geometry가 PostGIS에서 유효하고 source-target 방향이 일관됩니다.
- 그룹 header/member 수, JSON 배열 길이, status 제약조건이 일치합니다.
- Stub template은 최소 표본 수와 anchor 방향 검증을 통과합니다.
- Bend Pattern은 고유 route 수와 bend instance 수를 구분하여 집계합니다.
- `status`와 `validate` 결과에 orphan row, 잘못된 scope, 유효하지 않은 enum 값이 없습니다.
- 대표 샘플을 3D로 시각화하여 원본 route, segmentation, Stub, group, bend point가 같은 좌표계에 겹쳐지는지 확인합니다.
- 신규 query 1건 이상에 대해 Top-K 검색부터 최종 collision-free route 생성까지 end-to-end 검증합니다.
