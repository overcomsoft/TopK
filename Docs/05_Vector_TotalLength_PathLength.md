# [설계 개발 문서] 05. Total Length / Path Length 벡터

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

Total Length 특징은 route 전체 길이가 학습 대상 route들 중 어느 정도 규모인지 표현합니다. 현재 구현에서는 21번 차원 하나가 total length 비율을 담당하며, 22~24번은 장애물/우회 관련 env cost로 사용됩니다.

| 차원 | 의미 | 계산 |
|---:|---|---|
| 21 | route 총 길이 정규화 값 | `total_len / TOTAL_LENGTH_MAX` |

## 2. 입력 데이터

- `pts`: route polyline 점열
- `TOTAL_LENGTH_MAX`: 현재 학습 대상 route들의 총 polyline 길이 최댓값

```python
TOTAL_LENGTH_MAX = max(
    sum(dist_3d_local(r['points'][i], r['points'][i+1]) for i in range(len(r['points'])-1))
    for r in self.routes
) or 1.0
```

## 3. 핵심 알고리즘

```python
total_len = 0.0
for i in range(len(pts) - 1):
    total_len += dist_3d(pts[i], pts[i+1])

vec[21] = max(-1.0, min(1.0, total_len / TOTAL_LENGTH_MAX))
```

`total_len`은 모든 segment의 3D 길이 합입니다. 단순 source-target 직선거리가 아니라 실제 배관 중심선의 누적 길이입니다.

## 4. 가중치와 정규화

21~24번은 `env_cost` 그룹으로 묶이며 전체 가중치는 0.12입니다. 그룹 차원 수는 4이므로 scale factor는 다음과 같습니다.

```text
S = sqrt((0.12 * 30) / 4) ~= 0.9487
```

21번은 길이 규모, 22~24번은 장애물/우회 비용이므로 같은 그룹 안에서도 의미가 다릅니다.

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

- 신규 후보 경로가 기존 설계 대비 과도하게 길어지는지 판단하는 기준으로 사용합니다.
- Top-K 유사 설계 검색에서 전체 길이 규모가 비슷한 설계를 우선 찾습니다.
- Routing3D 비용 함수에서 거리 비용이 같은 후보일 때 기존 설계 길이 패턴과 가까운 후보를 우선할 수 있습니다.

## 6. 검증 포인트

- `TOTAL_LENGTH_MAX`는 학습 batch 기준이므로 전체 프로젝트를 대상으로 생성하는 것이 안정적입니다.
- `vec[21]`은 0~1 범위 값입니다.
- 직선거리 대비 우회율은 23번 차원에서 별도로 계산됩니다.
