# [설계 개발 문서] 03. BoundingBox / Spatial Range 벡터

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

이 문서는 30D 벡터의 9~11번 차원을 설명합니다. 문서명은 BoundingBox/Spatial Range이지만, **현재 구현 기준으로 vec[9:11]에는 route의 실제 min/max bbox 크기가 아니라 시작점-종단점 축별 절대 변위가 저장됩니다.** 정규화 분모만 전체 route들의 bbox 축별 최대 폭을 사용합니다.

| 차원 | 의미 | 현재 구현 |
|---:|---|---|
| 9 | X축 공간 범위 | `abs(end_x - start_x) / BBOX_MAX_X` |
| 10 | Y축 공간 범위 | `abs(end_y - start_y) / BBOX_MAX_Y` |
| 11 | Z축 공간 범위 | `abs(end_z - start_z) / BBOX_MAX_Z` |

## 2. 입력 데이터

분모는 전체 학습 route에서 실제 polyline bbox 폭의 최댓값으로 계산됩니다.

```python
BBOX_MAX_X = max(abs(max(p[0] for p in r['points']) - min(p[0] for p in r['points'])) for r in self.routes) or 1.0
BBOX_MAX_Y = max(abs(max(p[1] for p in r['points']) - min(p[1] for p in r['points'])) for r in self.routes) or 1.0
BBOX_MAX_Z = max(abs(max(p[2] for p in r['points']) - min(p[2] for p in r['points'])) for r in self.routes) or 1.0
```

분자는 시작점과 종단점의 축별 차이 절대값입니다.

## 3. 핵심 알고리즘

```python
vec[9] = max(-1.0, min(1.0, abs(dx) / BBOX_MAX_X))
vec[10] = max(-1.0, min(1.0, abs(dy) / BBOX_MAX_Y))
vec[11] = max(-1.0, min(1.0, abs(dz) / BBOX_MAX_Z))
```

값은 절대값을 사용하므로 방향 부호는 제거됩니다. 방향성은 6~8번 displacement 차원에서 담당하고, 9~11번은 축별 공간 점유 규모를 표현합니다.

## 4. 가중치와 정규화

`bounding_box` 그룹의 가중치는 0.15이며 3차원입니다.

```text
S = sqrt((0.15 * 30) / 3) ~= 1.2247
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


## 5. 자동경로 탐색 활용

- 유사한 공간 규모를 가진 기존 route를 우선 찾는 데 사용합니다.
- 같은 방향으로 이동하더라도 X/Y/Z 중 어느 축 공간을 크게 소비하는지 비교합니다.
- Routing3D 후보 경로의 예상 envelope이 기존 설계와 크게 다르면 Top-K 점수를 낮추는 보조 특징으로 활용합니다.

## 6. 검증 포인트

- 현재 구현은 실제 polyline bbox 크기가 아니라 `abs(end-start)`를 벡터에 저장합니다.
- 분모는 학습 batch 내 route bbox 최대 폭이므로 batch 구성이 달라지면 정규화 값이 달라질 수 있습니다.
- 향후 실제 polyline bbox 크기를 벡터에 넣고 싶다면 `abs(dx/dy/dz)` 대신 각 route의 `max-min` 값을 사용하도록 코드 변경이 필요합니다.
