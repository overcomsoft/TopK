# [설계 개발 문서] 10-02. 장애물 Context Vector 추출

## 업데이트 내용 및 일시

- **작성 일시**: 2026-07-22 KST
- **대상 코드**: `Tools/ExtractObstacleContextVector.py`, `Tools/context_vector_encoder.py`
- **핵심 함수**: `load_obstacle_index()`, `encode_endpoint()`, `encode_context_vector()`, `extract_context_vectors()`, `save_context_vectors()`
- **작성 내용**: 시작·종점 주변 장애물 환경을 30D Context Vector로 생성하는 방법을 정리했습니다.

---

## 1. 목적

형상이 유사한 기존 경로라도 주변 기둥, 보, 벽, 장비의 위치가 다르면 신규 현장에 적용하기 어렵습니다. Context Vector는 source/target 주변의 장애물 근접도, 방향성, 정렬 및 corridor 통과 특성을 30차원으로 표현하여 Feature Vector 후보를 환경 적합성 기준으로 재정렬합니다.

## 2. 입력 데이터

| 테이블 | 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|---|
| `TB_ROUTE_PATH` | `ROUTE_PATH_GUID` | text | `route-001` | Context 결과 연결 키입니다. |
| `TB_ROUTE_PATH` | `SOURCE_POSX/Y/Z` | double precision | `(1200,3400,800)` | 시작 endpoint입니다. |
| `TB_ROUTE_PATH` | `TARGET_POSX/Y/Z` | double precision | `(8500,3400,2100)` | 종단 endpoint입니다. |
| `TB_BIM_OBSTACLE` | 장애물 식별자 | text | `beam-102` | 장애물 provenance입니다. |
| `TB_BIM_OBSTACLE` | AABB min X/Y/Z | double precision | `(1000,3000,0)` | 장애물 최소 좌표입니다. |
| `TB_BIM_OBSTACLE` | AABB max X/Y/Z | double precision | `(1500,3600,3000)` | 장애물 최대 좌표입니다. |
| `TB_BIM_OBSTACLE` | type/name | text | `BEAM` | 장애물 분류에 사용합니다. |
| scope manifest | `PROJECT_SCOPE_KEY` | text | `fab-a` | 프로젝트 격리 키입니다. |
| scope manifest | `MODEL_REVISION_KEY` | text | `rev-202607` | 장애물 snapshot revision입니다. |

## 3. 핵심 알고리즘

### 3.1 공간 인덱스와 거리

장애물 AABB를 uniform-grid 공간 인덱스에 넣어 endpoint 주변 후보만 조회합니다. 거리는 AABB 중심이 아니라 endpoint와 AABB 표면의 최근접점 사이 거리로 계산합니다.

```text
closest = clamp(endpoint, aabb_min, aabb_max)
distance = ||endpoint - closest||2
```

이 방식은 큰 장애물의 중심이 멀리 있어도 표면이 endpoint에 가까운 경우를 정확히 반영합니다.

### 3.2 Endpoint와 corridor 인코딩

| 단계 | 계산 내용 |
|---|---|
| Endpoint Tier 1 | 가까운 거리 shell의 개수, 최근접 거리, 상대방향 |
| Endpoint Tier 2 | 더 넓은 거리 shell의 장애물 밀도와 정렬 |
| Endpoint 방향 | 최근접점 방향을 정규화하여 축별 장애 정도 표현 |
| Corridor Tier 3 | source-target 선분이 지나는 제한된 grid cell의 장애물 통계 |
| 최종 처리 | 시작/종단/연결부 통계를 30D로 결합하고 L2 정규화 |

거리 shell은 시작과 종점에 같은 규칙으로 대칭 적용합니다. 현재 파이프라인은 500mm와 1000mm 범위의 AABB-surface shell을 기준으로 문맥을 구분합니다.

## 4. 저장 구조

### 4.1 `TB_ROUTE_CONTEXT_VECTOR`

| 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|
| `ROUTE_PATH_GUID` | text | `route-001` | 원본 route입니다. |
| `PROJECT_SCOPE_KEY` | text | `fab-a` | 프로젝트 범위입니다. |
| `MODEL_REVISION_KEY` | text | `rev-202607` | 장애물 모델 revision입니다. |
| `CONTEXT_VECTOR` | vector(30) | `[0.03,0.11,...]` | 정규화 환경 벡터입니다. |
| `CONTEXT_VECTOR_JSON` 또는 통계 JSON | jsonb | `{ "start": ... }` | endpoint/tier별 분석 데이터입니다. |
| obstacle snapshot hash | text | `sha256:...` | 입력 장애물 집합 변경 감지값입니다. |
| encoder version | text | `context-v1` | 차원 정의 버전입니다. |
| encoder config/hash | jsonb/text | `{...}` | 거리 shell 등의 설정과 해시입니다. |
| build run id | uuid | `...` | 생성 실행 식별자입니다. |

실제 컬럼명은 적용된 schema revision을 따르며, 검색 벡터와 provenance를 함께 보존해야 합니다.

## 5. 자동경로 탐색에 활용 방법

- Context Vector는 1차 ANN 후보 수집보다 exact reranking에 사용합니다.
- 신규 source/target과 현재 장애물 snapshot으로 query Context를 생성합니다.
- 기존 후보의 encoder version과 snapshot 호환성을 확인합니다.
- Feature similarity가 높아도 Context distance가 크면 순위를 낮춥니다.
- 시작과 종점의 장애물 방향이 비슷한 후보는 초기 확장 및 종단 접근 방향의 참고값으로 사용합니다.

```text
final_score = w_feature * feature_similarity
            + w_context * context_similarity
            + w_size * size_similarity
            + w_arrangement * arrangement_similarity
```

## 6. 실행 및 검증

- source/target X/Y/Z가 모두 finite인지 확인합니다.
- AABB min이 max보다 크지 않은지 확인합니다.
- 벡터 길이 30, finite 값, norm을 검증합니다.
- 장애물이 없는 endpoint의 기본값 처리가 query와 학습에서 동일해야 합니다.
- obstacle snapshot hash가 바뀌면 기존 Context를 stale로 간주합니다.
- Context 점수가 실제 collision-free를 의미하지 않으므로 최종 충돌검사가 필요합니다.

