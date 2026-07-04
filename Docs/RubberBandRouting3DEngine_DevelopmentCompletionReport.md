# RubberBand 3D Routing Engine 개발 완료 보고서

## 1. 보고서 개요

본 보고서는 `RubberBandRouter` Python 고무줄 경로 3D 엔진이 `Docs/RubberBandRouting3DEngine.md`의 개발 계획에 따라 어느 수준까지 구현되었는지 소스코드 기준으로 분석한 결과이다. 분석 대상은 라우팅 코어, 장애물 맵, 위상 매칭, 특징점 추출, 충돌 회피, 배관 분배, DB 로더, Plotly Dash 디버거, 단위 테스트 전체이다.

분석 기준일: 2026-07-04  
대상 경로: `D:\DINNO\DEV\AI-AutoRouting\TopKGen\RubberBandRouter`

## 2. 종합 결론

`RubberBandRouter`는 개발 계획서에서 요구한 Python PoC 범위의 핵심 구조를 대부분 구현했다. 특히 다음 항목은 실제 코드와 테스트로 확인된다.

- 30,000mm 공간 스케일과 1,000mm 복셀 기반 밀도 텐서
- 현재 장애물 맵과 레거시 맵 간 코사인 유사도 기반 위상 매칭
- OBB 중심/부피 기반 2차 매칭 및 Case A/B/C 분류
- 레거시 엘보/특징점의 비율 정규화 및 현재 공간 재투영
- Pull 단계의 특징점 스냅
- Push 단계의 장애물 충돌 감지 및 3대 회피 전략 적용
- 트레이 중심선 기반 개별 배관 평행 분배
- 4단계 Plotly Dash 시각화 디버거
- PostgreSQL 기반 프로젝트/장애물/PoC/라우팅 작업 로더
- 단위 테스트 57건 전체 통과

다만 현재 구현은 “생산급 정밀 라우팅 엔진”이라기보다 “기능 검증용 PoC”에 가깝다. 특히 충돌 검사는 함수명에 SAT가 포함되어 있으나 실제로는 완전한 분리축 이론 기반 선분-OBB 검사가 아니라 AABB 사전 필터와 샘플 포인트 기반 OBB 내부 판정 방식이다. 또한 개발 계획서의 레거시 맵 전체 매칭 파이프라인은 모듈로는 구현되어 있으나, 현재 `run_routing.py` 통합 실행 경로에서는 신규 `legacy_feature_obstacles` 특징점 테이블을 우선 사용하고 있어 기존 `topology_matcher.py`와 `feature_extractor.extract_features()` 전체 흐름이 직접 연결되어 있지는 않다.

## 3. 구현 모듈 구성

### 3.1 전역 설정

파일: `RubberBandRouter/config.py`

`config.py`는 프로젝트 루트, 데이터/결과 디렉토리, DB 연결 정보, 공간 파라미터, 라우팅 파라미터, 위상 매칭 임계값, 트레이 설정, 시각화 설정을 중앙화한다.

주요 설정값은 다음과 같다.

- `SPACE_MAX = 30000`
- `GRID_SIZE = 1000`
- `GRID_DIM = 30`
- `MAX_VERTICAL_BENDS = 5`
- `SAFETY_MARGIN = 50.0`
- `SNAP_TOLERANCE = 100.0`
- `TOPOLOGY_CASE_A_THRESHOLD = 0.90`
- `TOPOLOGY_CASE_B_THRESHOLD = 0.60`
- `TOP_K_LEGACY_CANDIDATES = 5`
- `TRAY_WIDTH = 600.0`
- `TRAY_HEIGHT = 100.0`
- `PIPE_PITCH = 100.0`
- `PIPE_COUNT = 6`

개발 계획서의 기본 전제조건인 30,000mm 공간, 1m 복셀, 수직 꺾임 5회 제한, 트레이 기반 그룹 라우팅 설정이 반영되어 있다.

### 3.2 장애물 맵 및 밀도 텐서

파일: `RubberBandRouter/core/obstacle_map.py`

`OBBObstacle`와 `ObstacleMap`이 구현되어 있다. `OBBObstacle.from_24_vertices()`는 DB의 8개 꼭짓점 좌표로부터 중심점, 로컬 축, 반 크기, 부피를 계산한다. 로컬 축 추정은 SVD 기반이다.

`ObstacleMap.build_density_tensor()`는 장애물 꼭짓점의 AABB 범위를 1,000mm 격자로 변환하고 해당 복셀을 1로 채운다. 결과 텐서는 `(30, 30, 30)` 형태의 `int8` 3차원 바이너리 배열이다.

구현 완료 항목:

- OBB 장애물 데이터 구조
- 24좌표 기반 OBB 복원
- 30m 공간 기준 1m 복셀 밀도 텐서 생성
- pickle 기반 장애물 맵 캐시 저장/로드
- DB 기반 OBB 장애물 로딩 진입점

주의할 점:

- 밀도 텐서는 정밀 OBB 복셀화가 아니라 AABB 근사 복셀화다.
- `obstacle_map.py`의 DB 로더는 구 스키마 상수(`TB_EQUIPMENT` 등)를 참조하고, `data_loader.py`는 신 스키마(`TB_BIM_OBSTACLE`, `TB_EQUIPMENTS` 등)를 참조한다. 두 경로가 공존한다.

### 3.3 맵 위상 매칭

파일: `RubberBandRouter/core/topology_matcher.py`

개발 계획서의 “격자 기반 밀도 텐서 비교”와 “OBB 정밀 매칭”이 별도 모듈로 구현되어 있다.

주요 함수:

- `compute_cosine_similarity()`: 두 밀도 텐서를 평탄화하여 코사인 유사도를 계산한다. 텐서 크기가 다르면 제로 패딩으로 맞춘다.
- `filter_top_k_candidates()`: 레거시 텐서 후보 중 상위 K개를 선정한다.
- `_obb_match_score()`: 대형 장애물의 부피 유사도와 중심점 거리 유사도를 결합한다.
- `match_legacy_map()`: 코사인 점수 60%, OBB 점수 40% 가중합으로 최종 점수를 계산하고 Case A/B/C를 분류한다.

구현 완료 항목:

- 코사인 유사도 기반 1차 필터
- Top-K 후보 선정
- OBB 중심/부피 기반 2차 점수화
- Case A/B/C 분류

개발 계획서와 차이:

- 문서의 OBB 정밀 매칭은 “OBB 부피 및 중심점 거리 2차 비교”로 표현되어 있고, 구현은 이를 단순화한 점수 함수로 처리한다.
- 주석에는 거리 감쇠 `exp(-dist/10000)`이 설명되어 있으나 실제 구현은 `1.0 - dist / 30000.0` 선형 정규화 방식이다. 문서/주석/코드 간 불일치가 있다.

### 3.4 레거시 특징점 추출

파일: `RubberBandRouter/core/feature_extractor.py`

개발 계획서의 Case A/B/C 특징점 추출 정책이 구현되어 있다.

주요 클래스:

- `LegacyElbow`
- `Waypoint`
- `FeatureSet`

주요 함수:

- `normalize_to_ratio()`: 레거시 좌표를 출발-목적지 상대 비율로 정규화한다.
- `ratio_to_current()`: 비율 좌표를 현재 공간 좌표로 재투영한다.
- `extract_case_a()`: 레거시 엘보 전체를 웨이포인트로 변환한다.
- `extract_case_b()`: 수직 엘보와 대형 장비 우회점만 추출한다.
- `extract_case_c()`: 빈 `FeatureSet`을 반환한다.
- `extract_features_from_legacy_obstacles()`: 신규 `legacy_feature_obstacles` 기반 특징점을 웨이포인트로 변환한다.

구현 완료 항목:

- Case A 전체 엘보 재투영
- Case B 거시 특징점 선별
- Case C 레거시 특징점 면제
- 좌표 비율 정규화
- 레거시 DB 세그먼트에서 엘보 후보 로드
- 신규 특징점 테이블 연계용 보조 함수

주의할 점:

- `extract_case_b()`의 기본 `volume_threshold`는 `1e8`로 되어 있고, 상단 설명에는 `1e10` 또는 10m³ 기준이 언급된다. 단위/수치 설명이 일치하지 않는다.
- `extract_features_from_legacy_obstacles()`는 입력 특징점이 이미 현재 좌표계에 있다고 가정하는 형태라, 기존 Case A/B의 레거시 정규화 흐름과 성격이 다르다.

### 3.5 고무줄 라우팅 코어

파일: `RubberBandRouter/core/rubber_band.py`

라우팅 엔진의 중심 모듈이다. 개발 계획서의 Pull & Push 구조가 3단계 상태로 구현되어 있다.

주요 클래스:

- `RouteSegment`
- `RubberBandState`
- `RoutingResult`

주요 함수:

- `make_orthogonal_segments()`
- `count_vertical_bends()`
- `step1_initial_tension()`
- `step2_pull_snap()`
- `step3_push_resolve()`
- `run_routing()`

구현 흐름:

1. Step 1: 출발점과 목적지를 X/Y/Z 순서의 직교 세그먼트로 분해한다.
2. Step 2: `FeatureSet`의 웨이포인트를 경로 진행 방향 투영값 기준으로 정렬한 뒤 스냅 경로를 구성한다.
3. Step 3: 세그먼트별 충돌을 반복 감지하고 회피 웨이포인트를 삽입한다.

구현 완료 항목:

- 직교 세그먼트 생성
- Case C에서 특징점 없이 기본 경로 유지
- Case A/B에서 웨이포인트 기반 Pull 스냅
- 최대 20회 반복 Push 보정
- 수직 꺾임 카운트 관리
- 단계별 상태 보존

주의할 점:

- 개발 계획서는 시각화 4단계를 제시하지만 `rubber_band.py` 자체는 Step 1~3만 상태로 생성한다. Step 4는 `pipe_distributor.py`와 `scene_builder.py`에서 분리 구현된다.
- `step2_pull_snap()`의 `snap_tolerance` 인자는 전달되지만 실제 스냅 거리 필터로 사용되지 않는다.
- `RoutingResult.summary()`는 수직 꺾임 최대값을 하드코딩된 `{5}`로 표시한다. 설정값이 변경되면 요약 문자열과 실제 제한값이 어긋난다.

### 3.6 충돌 감지 및 3대 회피 전략

파일: `RubberBandRouter/core/collision.py`

개발 계획서의 “거대 장애물 조우 시 3대 회피 전략”이 구현되어 있다.

주요 클래스:

- `CollisionResult`
- `AvoidanceResult`
- `AvoidanceStrategy`

주요 함수:

- `segment_vs_obb_sat()`
- `find_collisions()`
- `resolve_collision()`
- `strategy_1_sleeve_tunnel()`
- `strategy_2_vertical_bypass()`
- `strategy_3_lateral_bypass()`

구현 완료 항목:

- AABB 빠른 사전 필터
- OBB 로컬 좌표계 기반 샘플 포인트 내부 판정
- 슬리브 터널링 전략
- 수직 오버/언더패스 전략
- 외곽 측면 우회 전략
- 회피 전략 우선순위 적용

개발 계획서와 차이:

- 함수명과 문서에는 SAT가 언급되지만 실제 구현은 선분을 500mm 간격으로 샘플링하여 OBB 내부 여부를 검사한다. 완전한 SAT 기반 선분/캡슐/트레이-OBB 교차 판정은 아니다.
- `find_collisions()`는 `obs.is_penetration` 장애물을 아예 충돌 검사에서 제외한다. 이로 인해 `strategy_1_sleeve_tunnel()`이 `resolve_collision()`에서 1순위로 존재하더라도 일반 흐름에서는 슬리브 장애물 충돌 결과가 넘어오지 않는다. 현재 구조에서는 슬리브 “충돌 후 터널링”보다 “충돌 대상 제외”에 가깝다.
- 수직 회피 전략은 오버패스와 언더패스를 모두 지원하지만, 수평 우회 거리 계산이 X축 AABB 기준으로 단순화되어 있어 Y축 주행 또는 회전 OBB 상황에서는 비용 비교 정확도가 떨어질 수 있다.
- 외곽 우회 전략은 dominant axis와 side axis를 XY 평면 기준으로 단순 선택한다. 수직 세그먼트 충돌이나 복잡한 3D 우회에서는 충분하지 않다.

### 3.7 트레이 중심선 기반 개별 배관 분배

파일: `RubberBandRouter/core/pipe_distributor.py`

개발 계획서의 4단계 “개별 배관 상대 좌표 분배”가 구현되어 있다.

주요 클래스:

- `PipePath`
- `DistributionResult`

주요 함수:

- `segments_to_centerline()`
- `distribute_pipes()`

구현 완료 항목:

- 트레이 중심선 좌표 추출
- 배관 수와 피치 기반 대칭 오프셋 계산
- 수평 세그먼트 법선 벡터 계산
- 수직 세그먼트에서 이전 수평 법선 유지
- JSON 저장

문제점:

- 파일 상단에 `json`, `logging`, `dataclass`, `Path`, `TYPE_CHECKING`, `numpy` import 블록이 중복되어 있다.
- `prev_normal`이 배관별 루프 바깥에 선언되어 있어 이전 배관의 마지막 세그먼트 법선이 다음 배관 계산에 영향을 줄 수 있다. 배관별 독립성을 위해 각 pipe 루프 안에서 초기화하는 것이 더 안전하다.
- 세그먼트 연결부에서 각 세그먼트의 시작점만 누적하고 마지막 세그먼트 끝점만 추가하므로, 코너 전후 오프셋 연결이 설계적으로 매끄러운지 별도 검증이 필요하다.

### 3.8 PostgreSQL 데이터 로더

파일: `RubberBandRouter/core/data_loader.py`

`DDW_AI_DB`의 신 스키마를 대상으로 프로젝트, 장애물, 장비, PoC, 라우팅 태스크를 로드하는 통합 로더가 구현되어 있다.

주요 클래스:

- `ProjectInfo`
- `ObstacleAABB`
- `EquipmentInfo`
- `PocPoint`
- `RoutingTask`
- `RoutingScene`

주요 기능:

- `TB_SPACE_GROUP_INFO` 프로젝트 목록 로드
- `TB_BIM_OBSTACLE` 장애물 로드
- `TB_EQUIPMENTS` 장비 로드
- `TB_POCINSTANCES` PoC 로드
- `TB_ROUTE_PATH` 라우팅 작업 로드
- PassThrough 객체 판별
- AABB 장애물을 `ObstacleMap`의 OBB 구조로 변환
- 신규 `legacy_feature_obstacles` 특징점 로드

구현 완료 항목:

- 실 DB 연동용 데이터 수집 파이프라인
- C# 로더 패턴 대응 설명 및 Python 구현
- DB 스키마 차이를 고려한 동적 컬럼 탐색
- route 기반 덕트/레터럴 PoC 보충 로더

주의할 점:

- DB 연결이 필요한 부분은 단위 테스트에서 실제 접속 검증까지 수행하지 않는다.
- `scene_to_obstacle_map()`은 AABB를 축 정렬 OBB로 변환한다. 회전 OBB가 필요한 원본 데이터에서는 정밀도가 제한된다.

### 3.9 통합 실행 스크립트

파일: `RubberBandRouter/run_routing.py`

DB에서 프로젝트와 태스크를 선택하고, 씬 로드, 장애물 맵 생성, 라우팅 실행, 배관 분배, 결과 저장, Dash 실행까지 연결하는 CLI 진입점이다.

구현 흐름:

1. DB 연결 정보 로드
2. 프로젝트 선택
3. 씬 데이터 로드
4. 라우팅 작업 선택
5. 장애물 맵 생성
6. 서브존 산정
7. `legacy_feature_obstacles` 특징점 로드
8. 특징점이 있으면 Case B, 없으면 Case C로 라우팅
9. 배관 분배
10. JSON 저장
11. Dash 시각화 실행

중요한 차이점:

- 현재 통합 실행 경로는 `topology_matcher.match_legacy_map()`을 직접 호출하지 않는다.
- 개발 계획서의 “현재 맵 vs 레거시 맵 전체 위상 매칭 후 Case A/B/C 결정”은 모듈로는 구현되어 있지만, 운영 CLI에서는 `legacy_feature_obstacles`의 존재 여부로 Case B/C를 구성하는 흐름이 중심이다.
- 따라서 문서상의 전체 AI 맵 위상 매칭 파이프라인은 “구현 모듈 존재” 상태이며, “통합 실행 경로에 완전 연결” 상태는 아니다.

### 3.10 Plotly Dash 시각화 디버거

파일:

- `RubberBandRouter/debugger/scene_builder.py`
- `RubberBandRouter/debugger/timeline_viewer.py`

개발 계획서의 4단계 타임라인 시각화 디버거가 구현되어 있다.

구현 완료 항목:

- Step 1 초기 인장 경로 표시
- Step 2 AI 특징점 스냅 경로 및 웨이포인트 표시
- Step 3 충돌 지점 표시
- Step 4 최종 배관 분배 결과 표시
- OBB 장애물 Mesh3d 표시
- Dash 슬라이더 기반 단계 전환
- DB 없이 실행 가능한 데모 모드

주의할 점:

- Step 3 제목과 trace 이름이 “회피 전”으로 되어 있으나 실제로는 `step3_push_resolve()` 후의 최종 세그먼트를 표시한다. 충돌 전/후 경로를 함께 비교하려면 별도 상태 저장이 필요하다.
- `timeline_viewer.py`의 실제 DB 파이프라인 모드는 `NotImplementedError`로 남아 있고, 운영 DB 연동은 `run_routing.py`에서 수행된다.

## 4. 개발 계획 대비 완료 현황

| 개발 계획 항목 | 구현 상태 | 근거 모듈 | 평가 |
|---|---:|---|---|
| 30,000mm 공간 및 1m 격자 | 완료 | `config.py`, `obstacle_map.py` | 계획과 일치 |
| 최대 수직 꺾임 5회 제한 | 완료 | `config.py`, `rubber_band.py`, `collision.py` | 기본 제한 구현 |
| 트레이 기반 그룹 라우팅 | 완료 | `pipe_distributor.py` | 중심선 후처리 방식으로 구현 |
| 현재/레거시 맵 코사인 유사도 | 완료 | `topology_matcher.py` | 모듈 구현 및 테스트 통과 |
| OBB 2차 매칭 | 부분 완료 | `topology_matcher.py` | 단순 점수화 구현, 주석과 수식 차이 |
| Case A/B/C 분류 | 완료 | `topology_matcher.py`, `feature_extractor.py` | 모듈 수준 구현 |
| Case A 전체 엘보 주입 | 완료 | `feature_extractor.py` | 비율 정규화 구현 |
| Case B 거시 특징점 추출 | 완료 | `feature_extractor.py` | 수직 엘보/대형 장비 기준 |
| Case C 레거시 우회 | 완료 | `feature_extractor.py`, `rubber_band.py` | 빈 FeatureSet 처리 |
| Pull 스냅 | 완료 | `rubber_band.py` | 웨이포인트 정렬 후 직교 분해 |
| Push 충돌 회피 | 부분 완료 | `rubber_band.py`, `collision.py` | 반복 회피 구현, 정밀 SAT는 미구현 |
| 슬리브 터널링 | 부분 완료 | `collision.py` | 전략 함수는 있으나 일반 충돌 흐름과 연결 약함 |
| 수직 오버/언더패스 | 완료 | `collision.py` | 잔여 수직 꺾임 기준 적용 |
| 외곽 최소 마진 우회 | 완료 | `collision.py` | 단순 XY 외곽 우회 구현 |
| 개별 배관 오프셋 분배 | 완료 | `pipe_distributor.py` | JSON 저장 포함 |
| 4단계 시각화 디버거 | 완료 | `scene_builder.py`, `timeline_viewer.py` | 데모 및 실행 연동 구현 |
| PostgreSQL 실데이터 로딩 | 부분 완료 | `data_loader.py`, `obstacle_map.py` | 로더 구현, 실제 DB 통합 테스트는 별도 필요 |
| Python PoC 검증 | 완료 | `tests/` | 57개 테스트 통과 |
| C++ Production Porting | 미구현 | 없음 | Phase 2 범위로 남음 |

## 5. 테스트 및 검증 결과

다음 명령으로 전체 테스트를 실행했다.

```powershell
python -m pytest RubberBandRouter\tests -v
```

결과:

```text
57 passed in 0.64s
```

테스트 범위:

- 충돌 검사 및 회피 전략
- 데이터 로더의 AABB/PassThrough/헬퍼 함수
- 씬을 장애물 맵으로 변환하는 로직
- 직교 세그먼트 생성
- 수직 꺾임 계산
- Pull 스냅 및 라우팅 상태 생성
- 코사인 유사도
- Top-K 후보 선정
- Case 분류

테스트는 모두 통과했으나, 실제 PostgreSQL 접속, 대용량 레거시 맵 캐시, Dash 브라우저 렌더링, 장거리 30,000mm 경로에서의 성능 검증은 단위 테스트 범위에 포함되어 있지 않다.

## 6. 주요 문제점

### 6.1 SAT 충돌 검사 명칭과 실제 구현 불일치

`segment_vs_obb_sat()`는 SAT라는 이름을 갖지만 실제 구현은 다음 방식이다.

1. 세그먼트 AABB와 장애물 AABB의 빠른 겹침 검사
2. 세그먼트 위를 500mm 간격으로 샘플링
3. 샘플 포인트가 OBB 확장 영역 안에 들어가는지 검사

따라서 좁은 장애물, 짧은 교차 구간, 트레이 단면이 실제로 접촉하는 경우를 놓칠 수 있다. 생산 품질의 충돌 검사를 위해서는 선분 swept volume 또는 캡슐/박스-OBB 기반의 정밀 교차 판정이 필요하다.

### 6.2 슬리브 터널링 전략이 일반 흐름에서 약하게 연결됨

`find_collisions()`는 `obs.is_penetration`이면 충돌 검사를 건너뛴다. 반면 `resolve_collision()`의 1순위 전략은 `strategy_1_sleeve_tunnel()`이다. 이 구조에서는 슬리브가 충돌로 들어오지 않기 때문에 1순위 전략이 일반 Push 루프에서 자연스럽게 작동하기 어렵다.

개선 방향은 다음 중 하나다.

- 관통 슬리브를 별도 후보로 전달하여 충돌 장애물과 함께 평가한다.
- 슬리브는 충돌 제외가 아니라 “허용 통로”로 등록하고 경로를 슬리브 중심축으로 유도한다.
- 일반 장애물과 슬리브를 분리한 공간 그래프를 구성한다.

### 6.3 통합 실행 경로와 개발 계획의 레거시 매칭 흐름 차이

문서의 파이프라인은 현재 맵과 레거시 맵을 매칭하여 Case A/B/C를 분류한 뒤 특징점을 추출하는 구조다. 그러나 `run_routing.py`에서는 `legacy_feature_obstacles` 테이블에서 현재 경로 서브존 특징점을 직접 조회하고, 특징점이 있으면 Case B, 없으면 Case C로 실행한다.

즉 다음 모듈들은 구현되어 있으나 운영 CLI에 완전 통합되어 있지 않다.

- `topology_matcher.match_legacy_map()`
- `feature_extractor.extract_features()`
- 레거시 맵 Top-K 후보 선정 후 최종 맵 확정 흐름

### 6.4 문서/주석/코드 수치 불일치

확인된 불일치:

- OBB 매칭 설명은 `exp(-dist/10000)` 감쇠를 언급하지만 실제 코드는 `1 - dist / 30000` 선형 점수를 사용한다.
- Case B 대형 장비 기준은 문서/주석에서 `1e10` 또는 10m³로 설명되지만 실제 기본값은 `1e8`이다.
- `RoutingResult.summary()`는 최대 수직 꺾임을 항상 5로 표시한다.

### 6.5 `snap_tolerance` 미사용

`step2_pull_snap()`는 `snap_tolerance` 인자를 받지만 실제로 웨이포인트가 경로에서 허용 거리 이내인지 검사하지 않는다. 현재는 FeatureSet에 포함된 모든 웨이포인트를 스냅 대상으로 사용한다.

이 경우 경로와 무관한 웨이포인트가 들어오면 경로가 과도하게 꺾일 수 있다.

### 6.6 배관 분배 법선 상태 공유 가능성

`pipe_distributor.py`에서 `prev_normal`이 배관별 루프 바깥에 있어, 한 배관의 마지막 법선이 다음 배관의 첫 수직 세그먼트 처리에 영향을 줄 수 있다. 배관별 독립성을 위해 `prev_normal`은 각 pipe loop 안에서 초기화하는 것이 바람직하다.

### 6.7 DB 스키마 경로 이원화

`obstacle_map.py`는 구 스키마 상수(`TB_EQUIPMENT`)를 사용하고, `data_loader.py`는 신 스키마(`TB_BIM_OBSTACLE`, `TB_EQUIPMENTS`)를 사용한다. 장기 유지보수 관점에서는 DB 접근 계층을 하나로 정리하는 것이 좋다.

### 6.8 대규모 성능 검증 부족

현재 테스트는 작고 통제된 데이터 중심이다. 실제 30m 공간, 다수 장애물, 다수 라우팅 태스크, DB 캐시, Dash 렌더링까지 포함한 성능 지표는 없다.

필요한 지표:

- 장애물 수별 밀도 텐서 생성 시간
- 레거시 맵 수별 Top-K 매칭 시간
- 세그먼트 수/장애물 수별 충돌 검사 시간
- Push 반복 횟수 평균/최대
- Dash 렌더링 가능 장애물 수
- 결과 경로의 총 길이/꺾임 수/충돌 잔여 여부

## 7. 개선 제안

### 7.1 진짜 SAT 또는 Swept Volume 충돌 검사 도입

현재 샘플링 방식은 PoC로는 단순하고 빠르지만 누락 가능성이 있다. 다음 순서로 개선을 권장한다.

1. 선분과 OBB의 정확한 교차 판정 추가
2. 트레이 폭/높이를 반영한 oriented box sweep 또는 capsule/box 확장 판정 추가
3. 샘플링 방식은 디버그/폴백 용도로 유지
4. 충돌 지점은 세그먼트 중점이 아니라 실제 최근접점 또는 최초 진입점으로 계산

### 7.2 위상 매칭 파이프라인을 `run_routing.py`에 통합

현재 구현된 `topology_matcher.py`와 `feature_extractor.py`를 통합 실행 경로에 연결해야 개발 계획서와 완전히 일치한다.

권장 흐름:

1. 현재 `ObstacleMap` 생성
2. 레거시 맵 캐시 또는 DB에서 후보 맵 로드
3. `match_legacy_map()` 실행
4. 매칭된 레거시 프로젝트의 엘보 로드
5. `extract_features()` 실행
6. 추가로 `legacy_feature_obstacles` 특징점을 병합
7. 우선순위/중복 제거 후 `run_routing()` 호출

### 7.3 특징점 필터링 강화

`snap_tolerance`를 실제로 적용해야 한다.

권장 조건:

- 웨이포인트의 S-D 진행 방향 투영값이 0~1 범위 또는 허용 마진 안에 있는지 검사
- 직선/직교 후보 경로에서의 최소 거리 기준으로 필터링
- 우선순위가 낮은 웨이포인트는 최대 개수 제한 적용
- 중복 또는 가까운 웨이포인트 클러스터링

### 7.4 회피 전략 비용 함수 도입

현재 회피는 전략 우선순위 중심이다. 실제 설계 품질을 높이려면 비용 함수를 도입하는 것이 좋다.

비용 요소:

- 추가 경로 길이
- 수직 꺾임 사용량
- 총 엘보 수
- 장애물과의 최소 여유 거리
- 레거시 특징점 이탈 정도
- 트레이/배관 간격 유지 여부

### 7.5 잔여 충돌 검증 단계 추가

Push 루프 종료 후 최종 경로에 대해 별도의 `validate_route()`를 수행해야 한다.

검증 항목:

- 모든 세그먼트가 단일 축 직교인지
- 연결점이 끊기지 않았는지
- 수직 꺾임 수가 제한 이하인지
- 모든 장애물과 충돌이 해소되었는지
- 트레이 및 개별 배관 오프셋 후에도 충돌이 없는지

### 7.6 배관 분배 후 개별 배관 충돌 재검사

현재 충돌 검사는 트레이 중심선과 확장 margin 기준으로 수행한다. 하지만 최종 배관 오프셋 후 개별 배관이 실제로 장애물과 충돌하지 않는지 검증하는 단계가 필요하다.

### 7.7 DB 로더 통합 및 스키마 어댑터화

구 스키마와 신 스키마를 별도 코드 경로로 유지하기보다, 다음 구조가 더 안전하다.

- `db/adapters/legacy_schema.py`
- `db/adapters/ddw_ai_schema.py`
- 공통 출력 모델: `ObstacleMap`, `RoutingScene`, `LegacyRoutePattern`
- 실행 설정에서 사용할 어댑터 선택

### 7.8 테스트 보강

추가 테스트가 필요한 항목:

- `run_routing.py`의 통합 CLI 흐름
- `pipe_distributor.py`의 수직 세그먼트 연속성 및 법선 초기화
- 실제 SAT 대체 후 경계 충돌 케이스
- 슬리브 터널링이 일반 라우팅 흐름에서 실제로 선택되는지
- Case A/B/C 통합 파이프라인
- Dash scene 생성 결과의 trace 개수 및 필수 속성
- 결과 JSON 스키마 안정성

## 8. 우선순위별 조치 목록

### P1: 정확도/기능 완성

- `run_routing.py`에 `topology_matcher` 기반 레거시 맵 매칭 경로 통합
- `snap_tolerance` 실제 적용
- 슬리브 터널링 흐름 재설계
- 최종 경로 잔여 충돌 검증 추가
- `segment_vs_obb_sat()`를 정확한 충돌 검사로 교체 또는 명칭 수정

### P2: 품질/안정성

- `pipe_distributor.py`의 중복 import 제거
- `prev_normal` 배관별 초기화
- `RoutingResult.summary()`에서 최대 수직 꺾임 하드코딩 제거
- 코드 주석의 거리 감쇠/볼륨 기준과 실제 구현 일치
- DB 스키마 접근 계층 정리

### P3: 운영/성능

- 대용량 레거시 맵 캐시 벤치마크
- 충돌 검사 공간 인덱스 도입
- 장애물 수가 많은 Dash 시각화의 LOD 또는 필터링
- 결과 JSON에 검증 리포트 포함
- C++ 포팅 대상 API 경계 정리

## 9. 최종 평가

`RubberBandRouter` Python 엔진은 개발 계획서의 핵심 아이디어를 PoC 수준에서 성공적으로 구현했다. 구조적으로는 다음 흐름이 잘 분리되어 있다.

- DB/씬 로딩
- 장애물 맵 변환
- 위상 매칭
- 특징점 추출
- 고무줄 Pull/Push 라우팅
- 개별 배관 분배
- 시각화 디버깅

테스트 57건이 모두 통과하여 기본 알고리즘 단위의 안정성도 확인되었다. 따라서 본 프로젝트는 “Python PoC 개발 완료”로 판단할 수 있다.

단, 생산 적용 또는 C++ 포팅 전에는 정밀 충돌 검사, 통합 레거시 매칭 연결, 슬리브 터널링 흐름, 최종 검증 단계, DB 스키마 정리, 대규모 성능 검증을 반드시 보강해야 한다. 특히 현재의 충돌 검사는 명칭과 달리 완전한 SAT가 아니므로, 실제 플랜트/건축 배관 자동 라우팅 품질을 보장하려면 이 부분을 가장 먼저 개선하는 것이 좋다.
