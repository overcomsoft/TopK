# [설계 개발 문서] 07. Arrow Pattern / Direction Statistics 벡터

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

Arrow Pattern / Direction Statistics는 route 전체가 X/Y/Z 중 어느 축을 중심으로 주행했는지와 bend가 얼마나 많았는지를 요약합니다. 이는 배관의 전반적인 주행 스타일을 표현하는 통계 특징입니다.

| 차원 | 의미 | 값 범위 |
|---:|---|---|
| 25 | X축 지배 segment 길이 비율 | 0~1 |
| 26 | Y축 지배 segment 길이 비율 | 0~1 |
| 27 | Z축 지배 segment 길이 비율 | 0~1 |
| 28 | bend count 정규화 값 | 0~1 |
| 29 | reserved | 현재 0.0 |

## 2. 입력 데이터

- `pts`: source 기준으로 방향 보정된 route polyline
- `total_len`: route 전체 3D 길이
- `route_bends(pts)`: 지배 축이 바뀌는 지점 목록

## 3. 축별 주행 비율 계산

각 segment에서 `abs(dx)`, `abs(dy)`, `abs(dz)` 중 가장 큰 축을 지배 축으로 선택하고, 해당 segment 길이를 축별 누적 길이에 더합니다.

```python
for i in range(1, len(pts)):
    dx_seg = abs(pt2[0] - pt1[0])
    dy_seg = abs(pt2[1] - pt1[1])
    dz_seg = abs(pt2[2] - pt1[2])
    max_diff = max(dx_seg, dy_seg, dz_seg)
    seg_dist = dist_3d(pt1, pt2)

    if dx_seg == max_diff:
        len_x += seg_dist
    elif dy_seg == max_diff:
        len_y += seg_dist
    else:
        len_z += seg_dist
```

최종 비율:

```python
rx = len_x / total_len_safe
ry = len_y / total_len_safe
rz = len_z / total_len_safe

vec[25] = rx
vec[26] = ry
vec[27] = rz
```

## 4. Bend count 계산

`route_bends()`는 각 segment의 지배 축을 `X`, `Y`, `Z` 중 하나로 보고, 이전 segment와 축이 달라지는 지점을 bend로 기록합니다.

```python
bend_count = len(route_bends(pts))
rbend = max(0.0, min(1.0, bend_count / 10.0))
vec[28] = rbend
vec[29] = 0.0
```

bend 10개 이상은 1.0으로 saturate됩니다.

## 5. 방향 패턴 문자열

DB에는 30D vector 외에 `DIRECTION_PATTERN` 문자열도 저장됩니다. `compute_direction_pattern()`은 segment 단위로 다음 코드를 만듭니다.

| 코드 | 의미 | 기준 |
|---|---|---|
| `R` | 수직 상승/하강 성격이 강함 | `abs(uz) >= 0.8` |
| `H` | 수평 주행 성격이 강함 | horizontal ratio `>= 0.8` |
| `D` | 대각/복합 방향 | 그 외 |

연속 중복 코드는 하나로 압축되어 `H-R-H` 같은 패턴 문자열로 저장됩니다.

## 6. 가중치와 정규화

`arrow_pattern` 그룹은 25~29번 5차원이며 가중치 0.15를 갖습니다.

```text
S = sqrt((0.15 * 30) / 5) ~= 0.9487
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

- X/Y/Z 주행 비율로 기존 설계가 수평 위주인지, 수직 이동이 많은지 판단합니다.
- bend가 많은 기존 설계는 장애물/공간 제약이 많은 패턴으로 볼 수 있습니다.
- 신규 Routing3D 후보가 과도하게 많은 bend를 만들면 Top-K 설계 통계와 비교하여 비용을 높일 수 있습니다.

## 8. 검증 포인트

- `vec[25] + vec[26] + vec[27]`은 정규화 전 기준으로 대체로 1.0에 가까워야 합니다.
- tie 상황에서는 코드상 X, Y, Z 순서로 우선권이 있습니다.
- 29번 차원은 예약값이므로 현재는 항상 0.0입니다.
