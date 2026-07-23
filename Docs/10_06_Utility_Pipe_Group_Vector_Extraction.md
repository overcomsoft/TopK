# [설계 개발 문서] 10-06. Utility Pipe Group Vector 추출

## 업데이트 내용 및 일시

- **작성 일시**: 2026-07-22 KST
- **대상 코드**: `Tools/BuildUtilityPipeGroupVectors.py`, `Tools/utility_pipe_group_encoder.py`
- **핵심 함수**: `load_source_members()`, `compute_groups()`, `normalized_centroid()`, `build_arrangement()`, `save_groups()`, `validate_groups()`
- **작성 내용**: 동일 장비의 복수 배관을 하나의 30D 그룹 검색 단위로 구성하는 방법을 정리했습니다.

---

## 1. 목적

개별 배관 Top-K만으로는 배관 다발의 멤버 수, 관경 구성, 순서, pitch 및 전체 점유공간을 보존하기 어렵습니다. 동일 장비와 유틸리티 그룹에 속하는 route를 UtilityPipeGroup으로 묶고 Feature/Context centroid와 배열 정보를 저장하여 그룹 단위 후보를 검색합니다.

## 2. 입력 데이터

| 테이블 | 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|---|
| `TB_ROUTE_PATH` | route/장비/utility/size | text | `route-001` | 그룹 멤버 메타데이터입니다. |
| `TB_ROUTE_FEATURE_VECTOR` | `FEATURE_VECTOR` | vector(30) | `[0.12,...]` | 형상 centroid 입력입니다. |
| `TB_ROUTE_FEATURE_VECTOR` | 시작/종점/길이/step | double/integer | `18340/12` | 멤버와 배열 정보입니다. |
| `TB_ROUTE_CONTEXT_VECTOR` | `CONTEXT_VECTOR` | vector(30) | `[0.03,...]` | 문맥 centroid 입력입니다. |
| `TB_ROUTE_SEGMENTS/DETAIL` | 3D geometry 점 | double precision | `(x,y,z)` | 그룹 AABB 계산에 사용합니다. |
| scope manifest | scope/revision | text | `fab-a/rev-202607` | source 데이터 격리 키입니다. |

## 3. 핵심 알고리즘

### 3.1 그룹 Identity

```text
PROJECT_SCOPE_KEY
+ MODEL_REVISION_KEY
+ PROCESS_NAME
+ EQUIPMENT_INSTANCE_KEY
+ UTILITY_GROUP
+ UTILITY
```

장비 instance는 그룹 정체성과 자기 후보 제외에 사용하고, 검색 후보 수집의 필수 필터는 `UTILITY_GROUP + UTILITY`입니다. 그룹은 최소 2개 멤버를 요구합니다.

### 3.2 멤버와 centroid

- size, 시작/종점 좌표, GUID를 정규화하여 결정론적 멤버 순서를 만듭니다.
- 유효한 Feature Vector만 평균한 뒤 L2 정규화하여 `FEATURE_CENTROID`를 생성합니다.
- 호환 가능한 Context Vector만 평균한 뒤 정규화하여 `CONTEXT_CENTROID`를 생성합니다.
- 전체 멤버 중 유효 벡터가 있는 비율을 coverage로 저장합니다.

```text
centroid = normalize((v1 + v2 + ... + vk) / k)
```

### 3.3 Arrangement

| 값 | 계산 | 의미 |
|---|---|---|
| start/end centroid | 멤버 endpoint 평균 | 그룹 기준 위치 |
| axis stats | X/Y/Z min, max, mean, std | 배열 폭과 높이 |
| distance stats | 멤버 간 endpoint 거리 통계 | pitch/분산 특성 |
| group AABB | 모든 geometry 점 범위 | 전체 corridor 크기 |
| size signature | size별 개수 | 관경 구성 |

## 4. 저장 구조

### 4.1 Header `TB_ROUTE_UTILITY_GROUP_VECTOR`

| 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|
| `GROUP_VECTOR_ID` | text PK | `ugv-01` | 안정적인 그룹 ID입니다. |
| scope/revision/process | text | `fab-a/rev/CVD` | 생성 범위입니다. |
| `EQUIPMENT_INSTANCE_KEY` | text | `eq-cvd-01` | 정규화 장비 instance입니다. |
| `UTILITY_GROUP/UTILITY` | text | `EXHAUST/WET_EXH` | 후보 필터입니다. |
| `MEMBER_COUNT` | integer | `4` | 멤버 수입니다. |
| `SIZE_SIGNATURE` | jsonb | `{ "100A": 2 }` | 관경 구성입니다. |
| `MEMBER_GUIDS` | jsonb | `["r1","r2"]` | 멤버 목록입니다. |
| `FEATURE_CENTROID` | vector(30) | `[0.10,...]` | ANN 후보 수집용입니다. |
| `CONTEXT_CENTROID` | vector(30) | `[0.06,...]` | exact reranking용입니다. |
| `ARRANGEMENT_VECTOR_JSON` | jsonb | `{ "axis_stats": ... }` | 배열 통계입니다. |
| start/end centroid | double precision | `(x,y,z)` | 그룹 endpoint 중심입니다. |
| AABB min/max | double precision | `(min)-(max)` | 그룹 점유범위입니다. |
| feature/context coverage | double precision | `1.0/0.75` | 유효 벡터 비율입니다. |
| `SOURCE_HASH` | text | `sha256...` | source 변경 판정값입니다. |
| `STATUS` | text | `READY` | `BUILDING/READY/FAILED/STALE`입니다. |

### 4.2 Member `TB_ROUTE_UTILITY_GROUP_MEMBER`

| 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|
| `GROUP_VECTOR_ID` | text | `ugv-01` | Header FK입니다. |
| `ROUTE_PATH_GUID` | text | `route-001` | 원본 route입니다. |
| `MEMBER_ORDER` | integer | `0` | 결정론적 순서입니다. |
| `UTILITY/SIZE` | text | `WET_EXH/100A` | 멤버 속성입니다. |
| `START_X/Y/Z`, `END_X/Y/Z` | double precision | `(x,y,z)` | endpoint입니다. |
| direction/length/step | text/double/integer | `RHH/18340/12` | 개별 형상 요약입니다. |
| vector build ids | text | `build-01` | 원본 벡터 provenance입니다. |

## 5. 자동경로 탐색에 활용 방법

1. `UTILITY_GROUP + UTILITY + STATUS=READY`로 후보를 제한합니다.
2. `FEATURE_CENTROID` HNSW cosine 검색으로 Top-K를 수집합니다.
3. Context, size signature, arrangement 및 coverage를 exact 계산하여 재정렬합니다.
4. 상위 그룹의 멤버 수와 결정론적 순서를 신규 PoC 그룹에 대응시킵니다.
5. Group/Bundle 및 Stub template과 결합해 각 멤버 route를 생성합니다.

## 6. 실행 및 검증

- `MEMBER_COUNT >= 2`이고 `MEMBER_GUIDS` 길이가 같아야 합니다.
- Header의 member count와 Member 테이블 실제 행 수가 같아야 합니다.
- Feature centroid는 필수 30D이고 Context centroid는 nullable입니다.
- coverage는 0~1 범위여야 합니다.
- AABB min/max 순서가 유효해야 합니다.
- 저장 성공 전 `BUILDING`, 성공 후 `READY`, source 변경 시 `STALE` 상태를 지켜야 합니다.

