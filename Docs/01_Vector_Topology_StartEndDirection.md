# [설계 개발 문서] 01. Start/End Direction 토폴로지 벡터

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

Start/End Direction은 기존 배관이 시작 PoC에서 어떤 방향으로 출발하고, 종단 PoC에 어떤 방향으로 접근하는지를 나타내는 토폴로지 특징입니다. 자동경로 탐색에서는 초기 확장 방향과 종단 접근 방향이 설계 품질에 큰 영향을 주므로, 30D 벡터에서 가장 높은 가중치 그룹 중 하나로 사용합니다.

| 차원 | 의미 | 값 범위 | 가중치 그룹 |
|---:|---|---|---|
| 0 | 시작 방향 X 성분 | -1~1 | `start_topology` |
| 1 | 시작 방향 Y 성분 | -1~1 | `start_topology` |
| 2 | 시작 방향 Z 성분 | -1~1 | `start_topology` |
| 3 | 종단 접근 방향 X 성분 | -1~1 | `end_topology` |
| 4 | 종단 접근 방향 Y 성분 | -1~1 | `end_topology` |
| 5 | 종단 접근 방향 Z 성분 | -1~1 | `end_topology` |

## 2. 입력 데이터

- `TB_ROUTE_PATH`: `SOURCE_POSX/Y/Z`, `TARGET_POSX/Y/Z`, route 메타데이터
- `TB_ROUTE_SEGMENTS`, `TB_ROUTE_SEGMENT_DETAIL`: route 중심선 polyline 복원용 segment 좌표

`load_data()`는 route별 segment detail을 `ROUTE_PATH_GUID`, segment order, detail order 순서로 정렬하고, source PoC와 polyline 양 끝점의 거리를 비교하여 방향을 보정합니다.

```python
if dist_to_end < dist_to_start:
    pts.reverse()
```

이 보정 이후 `pts[0]`은 source PoC 쪽, `pts[-1]`은 target PoC 쪽으로 해석됩니다.

## 3. 핵심 알고리즘

### 3.1 Start Direction

```python
v_start = (pts[1][0] - p0[0], pts[1][1] - p0[1], pts[1][2] - p0[2])
vec[0] = v_start[0] / v_start_safe
vec[1] = v_start[1] / v_start_safe
vec[2] = v_start[2] / v_start_safe
```

수식:

```text
v_start = normalize(p1 - p0)
```

### 3.2 End Direction

```python
v_end = (pts[-2][0] - pn[0], pts[-2][1] - pn[1], pts[-2][2] - pn[2])
vec[3] = v_end[0] / v_end_safe
vec[4] = v_end[1] / v_end_safe
vec[5] = v_end[2] / v_end_safe
```

수식:

```text
v_end = normalize(p(n-1) - pn)
```

End Direction은 target PoC에서 route 내부를 바라보는 방향입니다. 신규 query vector 생성 시에도 같은 부호 규칙을 사용해야 합니다.

## 4. 가중치와 정규화

`WEIGHT_MAP`에서 `start_topology`와 `end_topology`는 각각 0.20 가중치를 갖습니다. 각 그룹은 3차원이므로 scale factor는 다음과 같습니다.

```text
S = sqrt((0.20 * 30) / 3) = sqrt(2) ~= 1.4142
```

영역별 scale factor 적용 후 전체 30D 벡터를 L2 정규화하여 `FEATURE_VECTOR`와 `FEATURE_VECTOR_JSON`에 저장합니다.

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

- 시작 PoC에서 후보 확장 방향을 정할 때 Top-K 기존 설계의 0~2번 차원을 우선 참고합니다.
- 종단 PoC 접근부에서는 3~5번 차원을 이용해 target으로 접근하는 방향 후보를 제한하거나 가중합니다.
- Routing3D 탐색 중 같은 비용의 후보가 있으면 Start/End 방향이 Top-K 방향과 유사한 후보를 우선 확장합니다.

## 6. 검증 포인트

- `dist(SOURCE_POS, pts[0]) <= dist(SOURCE_POS, pts[-1])`인지 확인합니다.
- `FEATURE_VECTOR_JSON` 길이가 30인지 확인합니다.
- 방향이 반대인 route가 Top-K 결과에서 낮은 순위로 밀리는지 확인합니다.
