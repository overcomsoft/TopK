# [설계 개발 문서] 10-03. Path Segmentation 추출

## 업데이트 내용 및 일시

- **작성 일시**: 2026-07-22 KST
- **대상 코드**: `Tools/PathSegmenter.py`
- **핵심 함수**: `load_route_data_bulk()`, `axis_snap()`, `get_first_run()`, `segment_route()`, `get_point_type()`, `run_segmentation()`
- **작성 내용**: 기존 경로를 Start Stub, Middle Trunk, End Stub으로 분할하는 규칙과 저장 구조를 정리했습니다.

---

## 1. 목적

장비 접속부와 장거리 공통 이동부는 설계 제약과 재사용 방식이 다릅니다. 경로를 세 구간으로 분할하여 Stub은 anchor 기반 template으로, Middle Trunk는 장애물 탐색과 그룹 정렬 대상으로 각각 처리하는 것이 목적입니다.

## 2. 입력 데이터

| 테이블 | 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|---|
| `TB_ROUTE_PATH` | `ROUTE_PATH_GUID` | text | `route-001` | 분할 결과 키입니다. |
| `TB_ROUTE_PATH` | source/target 좌표 | double precision | `(1200,3400,800)` | 경로 방향 보정 기준입니다. |
| `TB_ROUTE_SEGMENTS` | segment order | integer | `3` | 선분 순서입니다. |
| `TB_ROUTE_SEGMENT_DETAIL` | detail order | integer | `5` | 선분 내부 점 순서입니다. |
| `TB_ROUTE_SEGMENT_DETAIL` | X/Y/Z | double precision | `(2500,3400,1200)` | polyline 점입니다. |
| `TB_ROUTE_SEGMENT_DETAIL` | point/element type | text | `ELBOW` | 경계 판정 보조값입니다. |

중복된 연속점은 방향 계산 전에 제거하거나 무시해야 합니다. 최소 2개의 유효점이 없는 route는 분할할 수 없습니다.

## 3. 핵심 알고리즘

### 3.1 축방향 run

연속점 차이 벡터에서 절대값이 가장 큰 축과 부호를 선택해 `+X/-X/+Y/-Y/+Z/-Z`로 스냅합니다. 같은 축방향이 연속되는 선분은 하나의 run으로 묶습니다.

```text
axis = argmax(|dx|, |dy|, |dz|)
direction = axis + sign
```

### 3.2 세 구간 분할

| 구간 | 정의 | 주요 산출물 |
|---|---|---|
| Start Stub | source PoC에서 첫 자유점까지 | 장비 접속 형상 |
| Middle Trunk | 두 자유점 사이 | 공통 장거리 이동구간 |
| End Stub | 마지막 자유점에서 target PoC까지 | 종단 anchor 접속 형상 |

`segment_route()`는 최초 유효 run, 방향 변화, point type 및 최소 길이를 고려해 경계를 정합니다. 종단은 경로를 역방향으로 해석하여 같은 원칙을 적용합니다. 마지막 유효 선분으로 target 진입 방향을 계산합니다.

### 3.3 함수 반환 계약

```text
segment_route(points) ->
  start_stub_points,
  middle_trunk_points,
  end_stub_points,
  start_free_point,
  end_free_point,
  end_entry_direction
```

세 구간은 source에서 target 방향을 유지해야 합니다. 인접 구간은 연결성을 위해 경계점을 공유할 수 있습니다.

## 4. 저장 구조

### 4.1 `TB_ROUTE_PATH_SEGMENTATION`

| 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|
| `ROUTE_PATH_GUID` | text PK | `route-001` | 원본 경로입니다. |
| `START_STUB_GEOM` | geometry(LineStringZ) | `LINESTRING Z (...)` | 시작 Stub입니다. |
| `MIDDLE_TRUNK_GEOM` | geometry(LineStringZ) | `LINESTRING Z (...)` | 중앙 trunk입니다. |
| `END_STUB_GEOM` | geometry(LineStringZ) | `LINESTRING Z (...)` | 종단 Stub입니다. |
| `START_FREE_POINT` | geometry(PointZ) | `POINT Z (...)` | Start Stub 이후 탐색 시작점입니다. |
| `END_FREE_POINT` | geometry(PointZ) | `POINT Z (...)` | End Stub 이전 탐색 종점입니다. |
| `END_ENTRY_DIR_X/Y/Z` | double precision | `(0,0,-1)` | target 접근 방향입니다. |
| `CREATED_AT` | timestamp | `2026-07-22` | 생성 시각입니다. |

geometry 컬럼에는 GIST 인덱스를 생성하여 공간 조회를 지원합니다.

## 5. 자동경로 탐색에 활용 방법

1. source/target anchor에 맞는 Stub template을 먼저 배치합니다.
2. `START_FREE_POINT`와 `END_FREE_POINT`를 middle route의 검색 endpoint로 사용합니다.
3. Middle Trunk에 그룹 pitch, rack 높이, obstacle corridor 제약을 적용합니다.
4. 세 결과를 경계점 중복 없이 결합합니다.
5. target 마지막 선분이 `END_ENTRY_DIR` 제약을 만족하는지 검사합니다.

## 6. 실행 및 검증

- 세 구간을 다시 합쳤을 때 원본 polyline의 순서와 형상이 복원되어야 합니다.
- Start/End free point가 각 Stub과 Middle 양쪽에 연결되어야 합니다.
- 짧은 경로에서 Start/End Stub이 겹치면 Middle이 비어 있는 상태를 명시적으로 처리합니다.
- 0길이 선분과 중복점이 axis 판정을 왜곡하지 않아야 합니다.
- elbow IP 복원으로 점 인덱스가 변경된 Bend 추출에서는 저장된 segmentation을 재사용하지 않고 복원점으로 다시 `segment_route()`를 실행합니다.

