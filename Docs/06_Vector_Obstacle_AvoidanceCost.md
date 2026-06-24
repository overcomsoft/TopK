# [설계 개발 문서] 06. Obstacle Avoidance / Env Cost 벡터

## 업데이트 내용 및 일시

- **업데이트 일시**: 2026-06-24 16:07:56 KST
- **업데이트 대상 코드**: `D:\DINNO\DEV\AI-AutoRouting\TopKGen\Tools\Extract_Design_Pattern.py`
- **공통 업데이트 내용**:
  - 현재 `save_route_similarity_vectors()` 구현 기준으로 30D 특징 벡터 차원 정의를 재정리했습니다.
  - `FEATURE_VECTOR` pgvector 저장과 `FEATURE_VECTOR_JSON` jsonb 저장 구조를 문서에 반영했습니다.
  - 기존 DB에 `FEATURE_VECTOR_JSON` 컬럼이 없을 때 `prepare_tables()`에서 자동 마이그레이션하는 내용을 추가했습니다.
  - 장애물 관계 특징은 `save_obstacle_relations()`가 먼저 계산되고, 이후 30D 벡터의 env cost 차원에서 재사용된다는 실행 순서를 명확히 했습니다.

---


## 1. 목적

Obstacle Avoidance / Env Cost 벡터는 기존 route가 장애물과 얼마나 가깝게 지나갔는지, 직선 대비 얼마나 우회했는지, 장애물 주변에서 Z 방향 회피가 얼마나 발생했는지를 표현합니다. 이 특징은 Routing3D 자동경로 탐색에서 장애물 회피 전략을 학습된 설계 패턴으로 반영하기 위한 값입니다.

| 차원 | 의미 | 계산 |
|---:|---|---|
| 22 | 최소 clearance margin 기반 근접 위험도 | `(300 - min_margin) / 300` |
| 23 | 직선거리 대비 우회 길이 비율 | `((total_len / straight) - 1) / 0.5` |
| 24 | 장애물 주변 최대 Z 변화량 | `max_z_delta / 1000` |

21~24번은 `env_cost` 그룹이며, 21번 total length와 함께 가중치 0.12를 공유합니다.

## 2. 선행 입력 테이블

`save_route_similarity_vectors()`가 실행되기 전에 `learn_and_save()`는 `save_obstacle_relations()`를 먼저 호출합니다.

```python
self.save_obstacle_relations()
self.save_route_similarity_vectors()
```

장애물 관계는 `TB_ROUTE_FEATURE_OBSTACLE_RELATION`에 저장됩니다.

| 컬럼 | 용도 |
|---|---|
| `ROUTE_PATH_GUID` | route별 env cost 조인 키 |
| `CLEARANCE_MARGIN_MM` | 요구 clearance 대비 실제 최소 거리 margin |
| `Z_DELTA_NEAR_OBSTACLE_MM` | 장애물 근처에서의 Z 변화량 |
| `BYPASS_SIDE`, `BYPASS_AXIS` | 우회 방향 분석 정보 |
| `RELATION_SCORE` | 장애물 관계 점수 |
| `GEOM_3D` | 관련 route segment geometry |

## 3. 장애물 관계 로딩

```python
SELECT "ROUTE_PATH_GUID", "CLEARANCE_MARGIN_MM", "Z_DELTA_NEAR_OBSTACLE_MM"
FROM "TB_ROUTE_FEATURE_OBSTACLE_RELATION"
WHERE "PROJECT_ID" = %s
```

로드된 결과는 `obs_relations[ROUTE_PATH_GUID]`에 route별 list로 저장됩니다.

## 4. 핵심 알고리즘

### 4.1 Clearance 위험도 e1

```python
margins = [rel[0] for rel in r_relations if rel[0] is not None]
min_margin = min(margins) if margins else 300.0
e1 = max(0.0, min(1.0, (300.0 - min_margin) / 300.0))
vec[22] = e1
```

- margin이 300mm 이상이면 위험도 0에 가깝습니다.
- margin이 0mm 또는 음수이면 위험도 1에 가까워집니다.

### 4.2 우회 길이 비율 e2

```python
straight = dist_3d(p0, pn)
overhead = (total_len / straight_safe) - 1.0
e2 = max(0.0, min(1.0, overhead / 0.5))
vec[23] = e2
```

직선거리 대비 50% 이상 길어진 경우 1.0으로 saturate됩니다.

### 4.3 Z 회피량 e3

```python
z_deltas = [abs(rel[1]) for rel in r_relations if rel[1] is not None]
max_z_delta = max(z_deltas) if z_deltas else 0.0
e3 = max(0.0, min(1.0, max_z_delta / 1000.0))
vec[24] = e3
```

장애물 주변에서 1000mm 이상 상승/하강하면 1.0으로 saturate됩니다.

## 5. 장애물 로딩 개선 사항

현재 `load_obstacles_for_routes()`는 route 전체 bbox에 5000mm margin을 준 AABB overlap으로 장애물을 가져옵니다. 또한 `TB_BIM_OBSTACLE`에 다음 컬럼 중 하나가 있으면 같은 프로젝트/장비 범위로 필터링합니다.

- `EQUIPMENT_TAG`
- `PROJECT_ID`
- `PROJECT_NAME`
- `MAIN_EQUIPMENT_NAME`

컬럼이 없는 DB에서는 기존 AABB overlap 조건만 사용하여 호환성을 유지합니다.

## 6. 가중치와 정규화

`env_cost` 그룹의 가중치는 0.12이고, 21~24 네 차원에 적용됩니다.

```text
S = sqrt((0.12 * 30) / 4) ~= 0.9487
```

## 공통 저장 구조

30D 벡터는 `TB_ROUTE_FEATURE_VECTOR`에 저장됩니다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `ROUTE_PATH_GUID` | text | 기존 설계 route 식별자 |
| `PROCESS_NAME` | text | 공정명 |
| `EQUIPMENT_NAME` | text | 장비명 또는 프로젝트 범위 식별자 |
| `UTILITY_GROUP` | text | 유틸리티 그룹 |
| `UTILITY` | text | 유틸리티 코드 |
| `SIZE` | text | 배관 사이즈 |
| `DIRECTION_PATTERN` | text | `R`, `H`, `D` 기반 방향 패턴 문자열 |
| `TOTAL_LENGTH_MM` | double precision | route polyline 총 길이 |
| `STEP_COUNT` | integer | route segment step 수 |
| `START_POSX/Y/Z` | double precision | 시작점 좌표 |
| `END_POSX/Y/Z` | double precision | 종단점 좌표 |
| `FEATURE_VECTOR` | vector(30) | pgvector Top-K 유사 설계 검색용 벡터 |
| `FEATURE_VECTOR_JSON` | jsonb | 동일 벡터의 검증/분석용 JSON 배열 |

기존 DB에 `FEATURE_VECTOR_JSON`이 없으면 `prepare_tables()`에서 다음 DDL을 자동 수행합니다.

```sql
ALTER TABLE "TB_ROUTE_FEATURE_VECTOR"
ADD COLUMN "FEATURE_VECTOR_JSON" jsonb;
```


## 7. 자동경로 탐색 활용

- 장애물과 너무 가까운 기존 route는 위험 패턴으로 볼 수 있고, 실제 설계자가 선택한 우회 방향은 회피 후보로 볼 수 있습니다.
- 신규 route 후보의 clearance, 우회율, Z 회피량을 query vector에 반영하면 장애물 회피 성향이 유사한 기존 설계를 Top-K로 찾을 수 있습니다.
- Routing3D에서는 장애물 근접 후보의 비용을 높이고, Top-K 설계의 우회 축/방향을 heuristic으로 활용할 수 있습니다.

## 8. 검증 포인트

- `TB_ROUTE_FEATURE_OBSTACLE_RELATION`이 먼저 생성되어야 22/24번 값이 0이 아닌 의미 있는 값으로 채워집니다.
- 장애물 데이터가 없으면 `e1=0`, `e3=0`입니다.
- 23번 우회율은 장애물 데이터가 없어도 total length와 직선거리로 계산됩니다.
