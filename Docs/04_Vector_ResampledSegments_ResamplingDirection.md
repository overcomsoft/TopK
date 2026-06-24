# [설계 개발 문서] 04. Resampled Segments 방향 벡터

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

Resampled Segments 벡터는 route 전체를 길이 기준으로 3등분한 뒤 각 구간의 대표 진행 방향을 3D 단위 벡터로 저장합니다. 시작/종단의 국소 방향만으로는 알 수 없는 중간 경로의 흐름을 표현합니다.

| 차원 | 의미 |
|---:|---|
| 12~14 | 1구간 대표 방향 X/Y/Z |
| 15~17 | 2구간 대표 방향 X/Y/Z |
| 18~20 | 3구간 대표 방향 X/Y/Z |

## 2. 입력 데이터

입력은 source 기준으로 방향 보정된 route polyline `pts`입니다. `resample_polyline_points(pts, 3)`은 전체 길이를 3등분하여 4개의 점을 생성합니다.

```python
resampled = self.resample_polyline_points(pts, 3)
```

## 3. 리샘플링 알고리즘

`resample_polyline_points()`는 누적 길이 배열을 만든 뒤, 목표 거리 `0`, `L/3`, `2L/3`, `L` 위치를 polyline 위에서 선형 보간합니다.

```python
target_d = j * (total_len / N)
t = (target_d - d1) / (d2 - d1)
x = p1[0] + t * (p2[0] - p1[0])
y = p1[1] + t * (p2[1] - p1[1])
z = p1[2] + t * (p2[2] - p1[2])
```

경로 길이가 매우 짧으면 동일 점을 반복 반환하여 계산 실패를 방지합니다.

## 4. 방향 벡터 계산

```python
if len(resampled) == 4:
    for i in range(3):
        p_from = resampled[i]
        p_to = resampled[i+1]
        seg_v = (p_to[0] - p_from[0], p_to[1] - p_from[1], p_to[2] - p_from[2])
        seg_len = math.sqrt(seg_v[0]**2 + seg_v[1]**2 + seg_v[2]**2)
        idx = 12 + i * 3
        vec[idx] = clamp(seg_v[0] / seg_safe, -1.0, 1.0)
        vec[idx+1] = clamp(seg_v[1] / seg_safe, -1.0, 1.0)
        vec[idx+2] = clamp(seg_v[2] / seg_safe, -1.0, 1.0)
```

각 구간은 단위 방향 벡터로 저장되므로 구간 길이 자체보다는 흐름 방향을 강조합니다.

## 5. 가중치와 정규화

각 segment 그룹의 가중치는 0.06이며 각 그룹은 3차원입니다.

```text
S = sqrt((0.06 * 30) / 3) ~= 0.7746
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


## 6. 자동경로 탐색 활용

- Top-K 기존 route의 중간부가 수평 우회형인지, 상승 후 이동형인지, 하강 후 접근형인지 판단합니다.
- 후보 route가 시작/종단 방향은 유사하지만 중간 흐름이 전혀 다르면 유사도를 낮춥니다.
- Routing3D에서 중간 waypoint 또는 rack 접근 방향을 정할 때 12~20번 차원의 흐름을 참고할 수 있습니다.

## 7. 검증 포인트

- 리샘플링 결과는 항상 4개 점이어야 합니다.
- 각 3D 방향 벡터의 길이는 정규화 전 기준으로 1에 가까워야 합니다.
- 너무 짧은 route는 12~20번 차원이 0에 가까울 수 있습니다.
