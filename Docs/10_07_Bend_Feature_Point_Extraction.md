# [설계 개발 문서] 10-07. Bend Feature Point 추출

## 업데이트 내용 및 일시

- **작성 일시**: 2026-07-22 KST
- **대상 코드**: `Tools/ExtractBendFeaturePoints.py`, `Tools/geometry_ip_restore.py`, `Tools/PathSegmenter.py`
- **스키마**: `Tools/sql/create_bend_feature_tables.sql`
- **핵심 함수**: `fetch_routes()`, `extract_candidates()`, `classify_transition()`, `classify_zone()`, `classify_cause()`, `aggregate_patterns()`, `insert_points()`, `insert_patterns()`
- **작성 내용**: 개별 꺾임점의 IP 복원, 구간/전환/원인 분류 및 반복 패턴 집계 방법을 정리했습니다.

---

## 1. 목적

기존 설계자가 경로의 어느 위치에서 어떤 방향으로 왜 꺾었는지를 데이터화합니다. 개별 bend 인스턴스와 반복 패턴을 분리 저장하여 신규 경로에서 신뢰도 높은 bend sequence를 soft waypoint 또는 비용 bias로 활용합니다.

이 항목은 경로마다 정확히 7개 점을 만드는 기능이 아닙니다. 유효한 방향 변화 수에 따라 0개 이상의 Bend Feature Point가 생성됩니다.

## 2. 입력 데이터

| 테이블/소스 | 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|---|
| `TB_ROUTE_PATH` | route/장비/utility/scope | text | `route-001` | 경로와 pattern 그룹 키입니다. |
| `TB_ROUTE_SEGMENTS` | segment 순서 | integer | `3` | polyline 복원 순서입니다. |
| `TB_ROUTE_SEGMENT_DETAIL` | point 좌표/type | double/text | `(x,y,z)/ELBOW` | bend와 elbow 복원 원본입니다. |
| `TB_BIM_OBSTACLE` | AABB/type/name | 좌표/text | `BEAM` | `OBSTACLE_AVOID` 근거입니다. |
| `TB_ROUTE_GROUP_PATTERN` | `MEMBER_GUIDS` | jsonb | `["r1","r2"]` | 그룹 멤버 역인덱스입니다. |
| `TB_ROUTE_GROUP_PATTERN` | `TRUNK_Z` | double precision | `2200` | 그룹 정렬 높이입니다. |
| `TB_ROUTE_GROUP_PATTERN` | `PITCH_MM` | double precision | `250` | 그룹 pitch 근거입니다. |
| `TB_ROUTE_GROUP_PATTERN` | `IS_EQUAL_SPACING` | boolean | `true` | 그룹 규칙 신뢰값입니다. |

## 3. 핵심 알고리즘

### 3.1 전체 처리 흐름

```text
route polyline 복원
-> elbow arc를 직관 교차점(IP)으로 복원
-> 복원 polyline 재세그멘테이션
-> 내부점 각도 변화로 bend 후보 검출
-> 방향축/전환유형/구간/상대위치 계산
-> 원인 분류
-> 반복 pattern 집계
-> Point/Pattern 전체 교체 적재
```

### 3.2 Elbow IP 복원

elbow가 여러 arc 샘플점으로 저장되면 하나의 elbow가 여러 bend로 오검출될 수 있습니다. 전후 직선 run을 연장하여 가장 가까운 교차점을 대표 IP로 복원합니다. 두 직선이 정확히 교차하지 않으면 skew distance를 저장하여 복원 품질을 추적합니다.

### 3.3 Bend 후보

각 내부점에서 전후 벡터의 cosine을 계산합니다.

```text
v1 = pi - p(i-1)
v2 = p(i+1) - pi
cos(theta) = dot(v1,v2) / (|v1||v2|)
```

거의 직선인 점과 0길이 선분은 제외합니다. 남은 점은 지배축과 부호로 `AXIS_BEFORE/AFTER`를 만들고 수평(X/Y)/수직(Z) 조합으로 전환유형을 분류합니다.

| 전환유형 | 의미 |
|---|---|
| `V_TO_H` | 수직에서 수평으로 전환 |
| `H_TO_V` | 수평에서 수직으로 전환 |
| `H_TO_H` | XY 평면 내 방향 전환 |
| `V_TO_V` | 수직축 부호/구조 전환 |

### 3.4 구간과 원인

IP 복원 후 점 index가 달라지므로 `PathSegmenter.segment_route()`를 다시 실행합니다. bend는 `START_STUB`, `MIDDLE_TRUNK`, `END_STUB`으로 분류하고 구간 내 누적길이 비율을 상대위치 bucket으로 저장합니다.

원인 우선순위는 다음과 같습니다.

1. `ZONE_CONSTRAINT`
2. `DESTINATION_ENTRY`
3. `OBSTACLE_AVOID`
4. `GROUP_ALIGNMENT`
5. `UNKNOWN`

`GROUP_ALIGNMENT`는 단순 평행만으로 판정하지 않습니다. bend Z가 `TRUNK_Z`의 허용오차(현재 ±50mm)에 있고 pitch/등간격 근거가 있어야 합니다. 장애물은 2,000mm 3D grid로 후보를 줄인 뒤 segment-AABB 실제 거리를 계산합니다. 4,096개를 초과하는 cell을 점유하는 큰 AABB는 overflow 목록에서 항상 검사합니다.

## 4. 저장 구조

### 4.1 `TB_ROUTE_BEND_FEATURE_POINT`

| 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|
| `BEND_ID` | bigserial PK | `10021` | 개별 bend ID입니다. |
| scope/revision | text | `fab-a/rev` | 모델 범위입니다. |
| `ROUTE_PATH_GUID` | text | `route-001` | 원본 경로입니다. |
| equipment/utility keys | text | `EQ/EXHAUST/WET_EXH` | pattern 그룹 키입니다. |
| `ORDINAL_FROM_START/END` | integer | `2/4` | 양 끝 기준 bend 순번입니다. |
| `SEGMENT_ZONE` | text | `MIDDLE_TRUNK` | 세 구간 중 위치입니다. |
| `REL_POSITION_BUCKET` | numeric(3,2) | `0.50` | 구간 내 상대위치입니다. |
| `TRANSITION_TYPE` | text | `H_TO_V` | 전환 유형입니다. |
| `AXIS_BEFORE/AFTER` | text | `+X/+Z` | 전후 지배축입니다. |
| `CAUSE` | text | `OBSTACLE_AVOID` | 추정 원인입니다. |
| `CAUSE_EVIDENCE` | jsonb | `{ "distance": 80 }` | 판정 근거입니다. |
| `IS_ELBOW_RESTORED_IP` | boolean | `true` | IP 복원 여부입니다. |
| `IP_RESTORE_SKEW_DIST_MM` | double precision | `3.2` | 복원 직선 간 최소거리입니다. |
| `ANCHOR_REL_POSITION` | jsonb | `[0.2,0.5,1.0]` | anchor 상대좌표입니다. |
| `IS_HORIZONTAL_SEQUENCE` | boolean | `true` | 수평 sequence 여부입니다. |
| `POINT_3D` | geometry(PointZ) | `POINT Z (...)` | bend 좌표입니다. |

### 4.2 `TB_ROUTE_BEND_FEATURE_PATTERN`

| 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|
| `PATTERN_ID` | text PK | `bfp-01` | 구조적 pattern ID입니다. |
| transition/zone/position | text/numeric | `H_TO_V/MIDDLE/0.5` | pattern key입니다. |
| `SAMPLE_COUNT` | integer | `24` | 고유 route 수입니다. |
| `BEND_INSTANCE_COUNT` | integer | `31` | 실제 bend 인스턴스 수입니다. |
| `TOTAL_ROUTES_IN_SCOPE` | integer | `40` | 분모 route 수입니다. |
| `FREQUENCY_SCORE` | double precision | `0.60` | sample/전체 route 비율입니다. |
| `DOMINANT_CAUSE` | text | `GROUP_ALIGNMENT` | 최빈 원인입니다. |
| `CAUSE_CONFIDENCE` | double precision | `0.83` | 최빈 원인 비율입니다. |
| `CAUSE_BREAKDOWN` | jsonb | `{ "GROUP_ALIGNMENT": 26 }` | 원인별 개수입니다. |
| representative/average point | geometry(PointZ) | `POINT Z (...)` | 대표/평균 위치입니다. |
| position std/consistency | double precision | `45/0.91` | 위치 반복성입니다. |
| `MEMBER_BEND_IDS` | jsonb | `[10021,...]` | 개별 bend 목록입니다. |
| `SOURCE_HASH` | text | `sha256...` | provenance입니다. 증분 skip 키가 아닙니다. |

`SAMPLE_COUNT`는 고유 route 수이고 `BEND_INSTANCE_COUNT`는 실제 bend 수이므로 서로 구분해야 합니다.

## 5. 자동경로 탐색에 활용 방법

- 빈도와 원인 신뢰도가 모두 임계값을 넘는 pattern만 guidance로 사용합니다.
- `DESTINATION_ENTRY`는 target 접근방향에, `ZONE_CONSTRAINT`는 Stub/Trunk 경계에 우선 적용합니다.
- `OBSTACLE_AVOID`는 신규 장애물 배치가 다르므로 좌표 고정점이 아닌 방향 힌트로 사용합니다.
- `GROUP_ALIGNMENT`는 신규 그룹의 pitch와 trunk 높이를 유지할 수 있을 때 적용합니다.
- bend 위치는 hard waypoint보다 비용 bias/soft waypoint로 사용하고 충돌 시 이동을 허용합니다.

## 6. 실행 및 검증

- build 시작 시 `create_bend_feature_tables.sql`을 적용하여 schema migration을 보장합니다.
- 대상 scope/revision의 Point와 Pattern은 build마다 다시 계산하여 교체합니다.
- 제한 실행은 진단용 `--dry-run`과 함께 사용하며 일부 결과로 전체 테이블을 교체하지 않습니다.
- 단위 테스트: `Tools`에서 `..\.venv\Scripts\python.exe -m unittest tests.bend_feature_point_tests`
- `status()`로 point/pattern 및 cause 분포를 확인하고 `validate()`로 orphan과 무효 참조를 검사합니다.
- DB 단위의 UNKNOWN 비율, 원인 정확도 및 실제 build/status/validate는 연결 가능한 운영 DB에서 별도로 검증해야 합니다.

