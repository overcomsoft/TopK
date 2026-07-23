# [설계 개발 문서] 10-05. Stub Pattern 추출

## 업데이트 내용 및 일시

- **작성 일시**: 2026-07-22 KST
- **대상 코드**: `Tools/ExtractStubPatterns.py`
- **핵심 함수**: `fetch_routes()`, `fetch_route_points()`, `fetch_anchors()`, `make_sample()`, `build_feature()`, `build_templates()`, `instantiate_stub()`
- **작성 내용**: 장비/덕트 접속부의 Start/End Stub을 개별 패턴과 재사용 template으로 추출하는 방법을 정리했습니다.

---

## 1. 목적

Stub은 PoC, 장비 또는 덕트 면, 초기/종단 방향을 직접 연결하는 국부 경로입니다. 기존 설계의 anchor face, 방향 sequence, rise, offset 및 bend 수를 학습해 신규 route의 시작·종단 고정 구간을 안정적으로 생성하는 것이 목적입니다.

## 2. 입력 데이터

| 테이블 | 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|---|
| `TB_ROUTE_PATH` | route 및 업무 키 | text | `route-001` | 원본 경로와 그룹 조건입니다. |
| `TB_ROUTE_SEGMENTS` | segment 순서 | integer | `1` | 경로 복원에 사용합니다. |
| `TB_ROUTE_SEGMENT_DETAIL` | X/Y/Z | double precision | `(1200,3400,800)` | Stub polyline 원본점입니다. |
| `TB_ROUTE_PATH_SEGMENTATION` | Start/End Stub geometry | geometry(LineStringZ) | `LINESTRING Z (...)` | 추출 대상 접속부입니다. |
| `TB_EQUIPMENTS` | 이름/utility/AABB | text/좌표 | `EQ-CVD-01` | START anchor 후보입니다. |
| `TB_DUCT` | 이름/AABB | text/좌표 | `DUCT-17` | END anchor 후보입니다. |
| `TB_DUCT_LATERAL` | 이름/AABB | text/좌표 | `LAT-03` | END anchor 후보입니다. |
| `TB_LATERAL_PIPE` | 이름/AABB | text/좌표 | `LP-05` | 배관 anchor 후보입니다. |

## 3. 핵심 알고리즘

### 3.1 Anchor 선택

1. 장비명과 utility hint로 후보를 좁힙니다.
2. PoC가 AABB 내부 또는 1mm margin에 있으면 내부 anchor를 우선합니다.
3. 내부 후보가 없으면 허용거리 이내의 최근접 AABB를 선택합니다.
4. PoC에서 가장 가까운 AABB 면을 `+X/-X/+Y/-Y/+Z/-Z`로 분류합니다.
5. PoC의 AABB 내부 상대좌표를 `[0,1]^3`으로 계산합니다.

### 3.2 Stub 특징

| 특징 | 계산 | 의미 |
|---|---|---|
| `FACE` | PoC와 최근접 AABB 면 | 접속 면 법선 |
| `DIR_SEQ` | 연속점의 6축 방향 run | 굴곡 순서 |
| `N_BENDS` | 방향 run 변화 횟수 | Stub 복잡도 |
| `RISE_MM` | face 법선축 최대 이동량 | anchor 탈출 높이/거리 |
| `OFFSET_MM` | PoC와 anchor 면 거리 | 접속 offset |
| `STUB_LENGTH_MM` | polyline 누적길이 | Stub 규모 |
| `FEAT` | face/방향/상대위치 조합 | 24D 검색 벡터 |
| `DIR_UNIT` | 첫 유효 run의 단위방향 | 출발/접근 방향 |

짧은 run은 노이즈성 굴곡을 줄이기 위해 인접 run과 병합합니다. START는 source에서 외부 방향으로, END는 target 기준 해석이 일관되도록 점 순서를 보정합니다.

## 4. 저장 구조

### 4.1 개별 샘플 `TB_ROUTE_STUB_PATTERN`

| 컬럼 | 타입 | 예시 | 설명 |
|---|---|---|---|
| `PATTERN_ID` | text PK | `sp-01` | 안정적인 샘플 ID입니다. |
| `ROUTE_PATH_GUID` | text | `route-001` | 원본 route입니다. |
| `STUB_KIND` | text | `START` | START 또는 END입니다. |
| `ANCHOR_KIND` | text | `EQUIPMENT` | anchor 종류입니다. |
| `ANCHOR_NAME` | text | `EQ-CVD-01` | anchor 이름입니다. |
| `MAIN_EQUIPMENT_NAME` | text | `EQ-CVD-01` | template 그룹 키입니다. |
| `UTILITY_GROUP/UTILITY/SIZE` | text | `EXHAUST/WET_EXH/100A` | 업무 키입니다. |
| `FACE` | text | `+X` | 접속 면입니다. |
| `DIR_SEQ` | jsonb | `[0,4,0]` | 방향축 sequence입니다. |
| `N_BENDS` | integer | `2` | bend 수입니다. |
| `RISE_MM/OFFSET_MM` | double precision | `500/120` | 치수 특징입니다. |
| `STUB_POINTS` | jsonb | `[[x,y,z],...]` | 실제 재구성 점입니다. |
| `FEAT` | vector(24) | `[0.1,...]` | Stub 검색 벡터입니다. |
| `DIR_UNIT` | vector(3) | `[1,0,0]` | 대표 방향입니다. |

### 4.2 집계/적용 테이블

| 테이블 | 저장 내용 | 용도 |
|---|---|---|
| `TB_ROUTE_STUB_TEMPLATE` | 대표 점, 평균 특징, 평균 방향, 표본 수 | 신규 Stub 후보 생성 |
| `TB_ROUTE_STUB_APPLICATION_LOG` | 요청, 선택 template, 생성점, 점수, 실패 사유 | 운영 추적 |

## 5. 자동경로 탐색에 활용 방법

1. 장비, utility group, utility, size, START/END 조건으로 template을 조회합니다.
2. 신규 PoC와 anchor AABB에 대표 Stub을 회전·이동합니다.
3. anchor face와 첫 방향축이 일치하지 않는 후보를 제거합니다.
4. 생성 Stub의 마지막 자유점을 Middle Trunk 탐색 endpoint로 사용합니다.
5. Start Stub, middle route, 역방향 End Stub을 연결합니다.

## 6. 실행 및 검증

- PoC가 선택 anchor와 최대 허용거리 안에 있어야 합니다.
- `DIR_SEQ`와 `N_BENDS`가 실제 `STUB_POINTS`에서 재계산한 값과 일치해야 합니다.
- `FEAT`는 24D, `DIR_UNIT`은 3D여야 합니다.
- template 대표점의 첫 점이 신규 PoC와 정확히 연결되어야 합니다.
- anchor clearance, 배관 반경 및 장애물 충돌을 최종 검사합니다.
- 낮은 표본 수/큰 분산 template은 hard constraint가 아닌 낮은 우선순위 후보로 사용합니다.

