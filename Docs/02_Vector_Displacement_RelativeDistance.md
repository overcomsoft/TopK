# [설계 개발 문서] 02. Displacement 상대 변위 벡터

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

Displacement 벡터는 시작점에서 종단점까지의 전체 상대 이동 방향과 크기를 표현합니다. Start/End Direction이 양 끝의 국소 방향을 표현한다면, Displacement는 route 전체가 공간적으로 어느 방향으로 이동하는지 나타냅니다.

| 차원 | 의미 | 계산 기준 | 가중치 그룹 |
|---:|---|---|---|
| 6 | 시작점 대비 종단점 X 변위 | `dx / DISPLACEMENT_MAX` | `displacement` |
| 7 | 시작점 대비 종단점 Y 변위 | `dy / DISPLACEMENT_MAX` | `displacement` |
| 8 | 시작점 대비 종단점 Z 변위 | `dz / DISPLACEMENT_MAX` | `displacement` |

## 2. 입력 데이터

- `pts[0]`: source PoC 쪽으로 보정된 시작점
- `pts[-1]`: target PoC 쪽 종단점
- `DISPLACEMENT_MAX`: 현재 학습 대상 route들 중 시작점-종단점 직선거리의 최댓값

```python
DISPLACEMENT_MAX = max(dist_3d_local(r['points'][0], r['points'][-1]) for r in self.routes) or 1.0
```

## 3. 핵심 알고리즘

```python
dx = pn[0] - p0[0]
dy = pn[1] - p0[1]
dz = pn[2] - p0[2]

vec[6] = max(-1.0, min(1.0, dx / DISPLACEMENT_MAX))
vec[7] = max(-1.0, min(1.0, dy / DISPLACEMENT_MAX))
vec[8] = max(-1.0, min(1.0, dz / DISPLACEMENT_MAX))
```

수식:

```text
d = pn - p0
vec[6:8] = clamp(d / DISPLACEMENT_MAX, -1, 1)
```
부호를 유지하므로 +X 방향으로 이동하는 route와 -X 방향으로 이동하는 route는 서로 다른 특징으로 저장됩니다.

## 4. 가중치와 정규화

`displacement` 그룹의 가중치는 0.15이며 3차원입니다.

```text
S = sqrt((0.15 * 30) / 3) ~= 1.2247
```

scale factor 적용 후 전체 30D 벡터 L2 정규화가 수행됩니다.

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

- 신규 source-target PoC의 전체 이동 방향이 기존 설계와 유사한지 판단합니다.
- 랙 접근 전 큰 흐름이 동서/남북/상하 중 어느 축을 중심으로 형성되는지 비교합니다.
- Top-K 후보 검색에서 국소 방향은 비슷하지만 전체 이동 방향이 다른 route를 낮은 순위로 보정합니다.

## 6. 검증 포인트

- `DISPLACEMENT_MAX`가 0으로 떨어지지 않도록 fallback `1.0`이 적용됩니다.
- `vec[6:8]`은 signed 값입니다.
- route 방향 보정이 잘못되면 displacement 부호도 뒤집히므로 `load_data()`의 `pts.reverse()` 결과를 함께 확인해야 합니다.
