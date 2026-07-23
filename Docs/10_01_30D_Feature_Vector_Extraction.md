# [설계 개발 문서] 10-01. 30D 특징벡터 추출

## 업데이트 내용 및 일시

- **작성 일시**: 2026-07-22 KST
- **대상 코드**: `Tools/Extract_Design_Pattern.py`
- **핵심 클래스/함수**: `DesignFeatureLearner`, `load_data()`, `save_obstacle_relations()`, `save_route_similarity_vectors()`, `learn_and_save()`
- **작성 내용**: 기존 설계경로를 30차원 형상 벡터로 변환하는 입력, 차원 구성, 가중치, 저장 및 Top-K 활용 방법을 정리했습니다.

---

## 1. 목적

기존 배관의 시작·종단 방향, 상대 변위, 점유 공간, 진행 방향, 길이 및 장애물 회피 특성을 동일한 30차원 공간에 표현합니다. 신규 자동경로와 기존 설계경로 간 cosine 유사도를 계산하여 설계자가 과거에 선택한 형상과 유사한 후보를 검색하는 것이 목적입니다.

## 2. 입력 데이터

| 테이블 | 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|---|
| `TB_ROUTE_PATH` | `ROUTE_PATH_GUID` | text | `route-001` | 경로 고유 식별자입니다. |
| `TB_ROUTE_PATH` | `SOURCE_POSX/Y/Z` | double precision | `(1200,3400,800)` | 시작 PoC입니다. |
| `TB_ROUTE_PATH` | `TARGET_POSX/Y/Z` | double precision | `(8500,3400,2100)` | 종단 PoC입니다. |
| `TB_ROUTE_PATH` | `PROCESS_NAME` | text | `CVD` | 공정 범위입니다. |
| `TB_ROUTE_PATH` | `EQUIPMENT_NAME` | text | `EQ-CVD-01` | 장비 식별 값입니다. |
| `TB_ROUTE_PATH` | `UTILITY_GROUP` | text | `EXHAUST` | 후보 필터 그룹입니다. |
| `TB_ROUTE_PATH` | `UTILITY` | text | `WET_EXH` | 유틸리티 코드입니다. |
| `TB_ROUTE_PATH` | `SIZE` | text | `100A` | 배관 크기입니다. |
| `TB_ROUTE_SEGMENTS` | segment 순서 | integer | `3` | 경로 선분 순서를 제공합니다. |
| `TB_ROUTE_SEGMENT_DETAIL` | X/Y/Z, detail 순서 | double precision/integer | `(2500,3400,1200)` | 3D polyline 복원점입니다. |
| `TB_ROUTE_FEATURE_OBSTACLE_RELATION` | route 장애물 관계/비용 | text/double precision | `env_cost=0.18` | 환경 비용 차원에 사용합니다. |

입력 경로는 최소 2개의 서로 다른 점이 필요합니다. source PoC와 polyline 양 끝의 거리를 비교하여 마지막 점이 source에 더 가까우면 점 순서를 뒤집습니다.

## 3. 핵심 알고리즘

### 3.1 처리 흐름

```text
route/detail 적재
-> 점 순서 및 source-target 방향 보정
-> 장애물 관계 계산
-> 형상 특징 30D 구성
-> 그룹별 가중치 scale
-> 전체 L2 정규화
-> pgvector/jsonb Upsert
```

### 3.2 주요 특징 그룹

| 그룹 | 계산 | 의미 |
|---|---|---|
| Start topology | `normalize(p1-p0)` | source에서 출발하는 방향 |
| End topology | `normalize(p(n-1)-pn)` | target에서 route 내부를 바라보는 방향 |
| Displacement | target-source 상대좌표 | PoC 공간 배치 |
| Bounding box | 축별 min/max와 span | 경로 점유 공간 |
| Resampled direction | 등거리 재표본점의 방향 성분 | 점 밀도와 무관한 전체 형상 |
| Path length | 누적 3D 길이 | 우회 규모 |
| Environment cost | 장애물 관계 집계 | 회피 난이도 |
| Arrow pattern | 상승/수평/하강 방향 sequence | 진행 형태 |

시작과 종단 방향의 부호 규칙은 query vector에서도 동일해야 합니다. End direction은 마지막 선분의 진행방향이 아니라 target에서 route 내부를 향하는 방향입니다.

가중치는 특징 그룹별 중요도를 반영한 scale factor로 적용하고 마지막에 전체 벡터를 L2 정규화합니다.

```text
scaled_i = raw_i * sqrt((group_weight * 30) / group_dimension)
feature = scaled / ||scaled||2
```

## 4. 저장 구조

### 4.1 `TB_ROUTE_FEATURE_VECTOR`

| 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|
| `ROUTE_PATH_GUID` | text | `route-001` | 원본 경로 연결 키입니다. |
| `PROCESS_NAME` | text | `CVD` | 공정명입니다. |
| `EQUIPMENT_NAME` | text | `EQ-CVD-01` | 장비명입니다. |
| `UTILITY_GROUP` | text | `EXHAUST` | 유틸리티 그룹입니다. |
| `UTILITY` | text | `WET_EXH` | 유틸리티 코드입니다. |
| `SIZE` | text | `100A` | 관경입니다. |
| `DIRECTION_PATTERN` | text | `RHHDD` | 방향 패턴입니다. |
| `TOTAL_LENGTH_MM` | double precision | `18340` | 경로 총길이(mm)입니다. |
| `STEP_COUNT` | integer | `12` | 방향 run/step 수입니다. |
| `START_POSX/Y/Z` | double precision | `(1200,3400,800)` | 보정된 시작 좌표입니다. |
| `END_POSX/Y/Z` | double precision | `(8500,3400,2100)` | 보정된 종단 좌표입니다. |
| `FEATURE_VECTOR` | vector(30) | `[0.12,-0.04,...]` | ANN/cosine 검색용입니다. |
| `FEATURE_VECTOR_JSON` | jsonb | `[0.12,-0.04,...]` | 검증과 분석용 동일 배열입니다. |
| `PROJECT_SCOPE_KEY` | text | `fab-a` | 프로젝트 범위입니다. |
| `MODEL_REVISION_KEY` | text | `rev-202607` | 모델 revision입니다. |

`ROUTE_PATH_GUID` 기준 Upsert를 수행합니다. 기존 DB에 `FEATURE_VECTOR_JSON`이 없으면 `prepare_tables()`가 추가합니다.

## 5. 자동경로 탐색에 활용 방법

1. 신규 source/target과 후보 seed 경로를 같은 encoder로 30D 변환합니다.
2. `UTILITY_GROUP + UTILITY`로 후보군을 제한합니다.
3. `FEATURE_VECTOR` cosine distance로 Top-K를 수집합니다.
4. Context, size, 장비 family 및 그룹 배열 점수로 재정렬합니다.
5. 상위 경로의 방향 sequence, trunk 및 bend 구조를 신규 좌표계에 매핑합니다.
6. 최종 후보는 실제 장애물 geometry로 다시 탐색·충돌검사합니다.

## 6. 실행 및 검증

- 벡터 길이는 정확히 30이어야 합니다.
- 모든 값은 finite이고 전체 norm은 1.0에 가까워야 합니다.
- `FEATURE_VECTOR`와 `FEATURE_VECTOR_JSON`의 성분이 일치해야 합니다.
- source와 target을 바꾼 경로가 같은 방향 벡터로 저장되지 않는지 확인합니다.
- 장애물 관계 저장이 벡터 저장보다 먼저 실행되는지 확인합니다.
- 벡터 유사도는 충돌 가능 여부를 보장하지 않으므로 최종 geometry 검증을 생략하지 않습니다.

