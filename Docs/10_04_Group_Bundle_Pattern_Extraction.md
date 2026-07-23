# [설계 개발 문서] 10-04. Group/Bundle Pattern 추출

## 업데이트 내용 및 일시

- **작성 일시**: 2026-07-22 KST
- **대상 코드**: `Tools/ExportGroupPattern.py`
- **핵심 함수**: `extract_pipe_feature()`, `compute_similarity()`, `check_parallel_overlap()`, `compute_offset_regularity()`, `analyze_patterns()`, `save_bundle_patterns()`
- **작성 내용**: 여러 기존 배관의 공통 trunk, pitch 및 배열 규칙을 그룹 패턴으로 추출하는 방법을 정리했습니다.

---

## 1. 목적

같은 장비와 유틸리티 계통의 배관들이 일정 간격으로 나란히 이동하는 설계 규칙을 학습합니다. 개별 경로의 유사성뿐 아니라 멤버 순서, pitch, offset 축, trunk 높이 및 공통 bend sequence를 신규 다발 배관 배치에 재사용하는 것이 목적입니다.

## 2. 입력 데이터

| 테이블 | 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|---|
| `TB_ROUTE_PATH_SEGMENTATION` | `ROUTE_PATH_GUID` | text | `route-001` | 멤버 route 키입니다. |
| `TB_ROUTE_PATH_SEGMENTATION` | `START_STUB_GEOM` | geometry(LineStringZ) | `LINESTRING Z (...)` | 초기 정렬과 vertical tail 분석에 사용합니다. |
| `TB_ROUTE_PATH_SEGMENTATION` | `MIDDLE_TRUNK_GEOM` | geometry(LineStringZ) | `LINESTRING Z (...)` | 평행/중첩/간격 분석 대상입니다. |
| `TB_ROUTE_PATH` | `EQUIPMENT_TAG` | text | `EQ-CVD-01` | 그룹 범위입니다. |
| `TB_ROUTE_PATH` | `UTILITY_GROUP` | text | `EXHAUST` | 그룹 범주입니다. |
| `TB_ROUTE_PATH` | `UTILITY` | text | `WET_EXH` | 유틸리티 코드입니다. |
| `TB_ROUTE_PATH` | `SIZE` | text | `100A` | 관경 구성 분석값입니다. |

## 3. 핵심 알고리즘

### 3.1 개별 경로 특징

- trunk polyline을 고정 개수로 재표본화합니다.
- 각 선분을 6축 방향으로 스냅합니다.
- 상승/수평/하강 arrow code와 직교 bend 수를 계산합니다.
- elbow 위치와 수평/수직 구간을 추출합니다.

### 3.2 그룹 형성

```text
업무 키로 route partition
-> pairwise 형상 유사도
-> 평행 구간의 실제 중첩 길이와 pitch 검사
-> union-find로 연결요소 구성
-> 멤버 offset/pitch/trunk 통계 집계
```

두 경로가 단순히 같은 방향인 것만으로 그룹화하지 않습니다. 평행 구간이 실제 공간에서 일정 길이 이상 겹치고 최대 pitch 안에 있어야 합니다.

### 3.3 배열 규칙

| 특징 | 계산 | 의미 |
|---|---|---|
| `TRUNK_Z` | trunk 높이 대표값 | rack/공통 이동 높이 |
| `TRUNK_XY_SPREAD` | XY 점유 폭 | 그룹 corridor 폭 |
| `PITCH_MM` | 인접 멤버 offset 차이 대표값 | 배관 간격 |
| `PITCH_CV` | pitch 표준편차/평균 | 간격 일관성 |
| `IS_EQUAL_SPACING` | CV 임계값 판정 | 등간격 여부 |
| `OFFSET_AXIS` | 멤버 배열 지배축 | 그룹 확장 방향 |
| `ORTHO_PATTERN` | 직교 bend sequence | 공통 굴곡 구조 |

## 4. 저장 구조

### 4.1 `TB_ROUTE_GROUP_PATTERN`

| 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|
| `GROUP_ID` | text PK | `grp-01` | 안정적인 그룹 ID입니다. |
| `EQUIPMENT_TAG` | text | `EQ-CVD-01` | 장비입니다. |
| `UTILITY_GROUP` | text | `EXHAUST` | 유틸리티 그룹입니다. |
| `UTILITY` | text | `WET_EXH` | 유틸리티입니다. |
| `N_MEMBERS` | integer | `4` | 그룹 멤버 수입니다. |
| `AVG_SIMILARITY` | double precision | `0.91` | 그룹 내 평균 유사도입니다. |
| `TRUNK_Z` | double precision | `2200` | 대표 trunk 높이입니다. |
| `TRUNK_XY_SPREAD` | double precision | `480` | trunk XY 분산 폭입니다. |
| `PITCH_MM` | double precision | `250` | 대표 pitch입니다. |
| `PITCH_CV` | double precision | `0.04` | pitch 변동계수입니다. |
| `IS_EQUAL_SPACING` | boolean | `true` | 등간격 여부입니다. |
| `OFFSET_AXIS` | text | `Y` | 멤버 배열축입니다. |
| `MEMBER_GUIDS` | jsonb | `["r1","r2"]` | 멤버 route 목록입니다. |
| `PATTERN_SEQ` | text | `R-H-D` | 그룹 방향 sequence입니다. |
| `FEAT` | vector(60) | `[0.08,...]` | 그룹 유사도 벡터입니다. |
| `FEAT_JSON` | jsonb | `[0.08,...]` | 분석용 벡터입니다. |
| `GEOM_3D` | geometry(MultiLineStringZ) | `MULTILINESTRING Z (...)` | 전체 그룹 geometry입니다. |
| `TRUNK_GEOM_3D` | geometry(MultiLineStringZ) | `MULTILINESTRING Z (...)` | 공통 trunk geometry입니다. |

## 5. 자동경로 탐색에 활용 방법

- 신규 그룹과 동일한 `equipment + utility_group + utility`의 패턴을 우선 조회합니다.
- 기준 멤버 route를 생성한 뒤 `OFFSET_AXIS`와 `PITCH_MM`로 나머지 멤버 corridor를 배치합니다.
- `TRUNK_Z`를 선호 높이로 사용하되 신규 장애물과 rack 조건에 따라 조정합니다.
- `ORTHO_PATTERN`과 elbow 통계로 그룹 전체의 bend sequence를 동기화합니다.
- 멤버별 Stub은 각각의 PoC/size 조건에 맞게 별도로 생성합니다.

## 6. 실행 및 검증

- `N_MEMBERS == jsonb_array_length(MEMBER_GUIDS)`인지 확인합니다.
- 멤버 GUID 중복과 자기 중복 그룹이 없어야 합니다.
- `PITCH_MM > 0`이고 `PITCH_CV`가 등간격 판정과 일치해야 합니다.
- `GEOM_3D`와 `TRUNK_GEOM_3D`가 유효한 PostGIS geometry여야 합니다.
- 신규 배치 후 모든 멤버에 대해 개별 collision/clearance 검사를 수행합니다.

