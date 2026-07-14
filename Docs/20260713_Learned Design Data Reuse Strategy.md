# 기존 설계 데이터 학습 및 신규 배관설계 활용 전략

DDW AI AutoRouting System | v1.0 | 2026-07-13

## 1. 목적 및 배경

`TopKGen`은 지금까지 기존 설계 데이터(`DDW_AI_DB`)로부터 다음 3종의 핵심 패턴을 추출·저장해왔다.

1. **Start Stub / End Stub 패턴** — 장비 PoC 및 덕트/레터럴 PoC 인근의 정형화된 인입·진입 형상 (`Tools/ExtractStubPatterns.py` → `TB_ROUTE_STUB_TEMPLATE`)
2. **Middle Trunk 그룹배관(다발) 패턴** — CSF구역에서 평행하게 나란히 달리는 배관 다발 (`Tools/ExportGroupPattern.py` → `TB_ROUTE_GROUP_PATTERN`)
3. **경로 형상 유사도 특징벡터** — 경로 전체의 30차원 형상 벡터 (`Tools/Extract_Design_Pattern.py` → `TB_ROUTE_FEATURE_VECTOR`)

이 문서는 "이 데이터를 어떻게 학습시키고, 신규 배관설계 시 어떻게 활용할 것인가"라는 질문에 답하기 위해 작성되었다. 결론을 먼저 요약하면:

> **TopKGen 자체의 라우팅 엔진(`RubberBandRoutingSuite`)에는 이 학습 데이터를 신규 설계에 연결하는 통합 지점이 아직 없다** (§2.6 참조). 그러나 자매 저장소 `RoutingAI`(`D:\DINNO\DEV\AI-AutoRouting\RoutingAI`)에 **동일한 데이터 자산을 기반으로 "탐색→페어링→생성→충돌회피"를 수행하는 베타 단계의 작동 파이프라인이 이미 구현되어 있고, 실측 성능 지표까지 확보되어 있다** (§3 참조). 따라서 이 문서의 핵심 제안은 처음부터 새 학습 방법을 설계하는 것이 아니라, **RoutingAI의 검증된 접근 방식을 TopKGen의 데이터/엔진에 맞춰 이식·연동하는 것**이다 (§5, §6).

---

## 2. 현재 보유 중인 학습 가능 데이터 자산 (TopKGen)

### 2.1 Start Stub / End Stub 템플릿 (`TB_ROUTE_STUB_TEMPLATE` 계열)

| 테이블 | 역할 |
| --- | --- |
| `TB_ROUTE_STUB_PATTERN` | 개별 경로 1건당 1행 — Start/End 각각의 원본 스텁 샘플 |
| `TB_ROUTE_STUB_TEMPLATE` | 동일 조건(장비/유틸리티/구경/면/방향)으로 묶은 대표 템플릿 (평균값 + 대표 경로) |
| `TB_ROUTE_STUB_APPLICATION_LOG` | `make-stub` CLI로 신규 요청에 템플릿을 적용한 이력 로그 |

**템플릿 키(그룹핑 기준)**: `(STUB_KIND[START/END], ANCHOR_KIND[EQUIP/DUCT/LATERAL], MAIN_EQUIPMENT_NAME, UTILITY_GROUP, UTILITY, SIZE, FACE, DIR_SEQ)`, 최소 샘플 수(`--min-samples`, 기본 3) 이상인 조합만 템플릿으로 확정된다.

**24차원 스텁 특징벡터** (`build_feature`, `Tools/ExtractStubPatterns.py:903-921`):

| 차원 | 의미 |
| --- | --- |
| `[0:6]` | 앵커 접속면(6방향) 원-핫 |
| `[6:12]` | 1차 진행방향 원-핫 |
| `[12:18]` | 2차 진행방향(첫 엘보) 원-핫 |
| `[18:21]` | 앵커 AABB 내 PoC 상대위치 |
| `[21:24]` | 진행 단위벡터 (End Stub은 앵커로 "들어가는" 방향이 되도록 부호 반전) |

**중요**: `Tools/ExtractStubPatterns.py`는 이미 `make-stub` CLI 서브커맨드로 **"신규 요청 → 템플릿 조회(4단계 폴백) → 좌표 인스턴스화 → Start/End 후보 스코어링"까지의 전체 적용 로직을 자체 내장**하고 있다 (`make_stub_candidates()`, `instantiate_stub()` — `Tools/ExtractStubPatterns.py:1023-1100`). 다만 **이 출력은 어떤 C# 라우팅 엔진에도 연결되어 있지 않다** — 저장소 전체에서 `TB_ROUTE_STUB_TEMPLATE`을 참조하는 `.cs` 파일은 0건이다.

### 2.2 Middle Trunk 그룹배관 패턴 (`TB_ROUTE_GROUP_PATTERN`)

`Tools/ExportGroupPattern.py`가 산출(2026-07-12~13 세션에서 A/F→CSF 수직꼬리 포함 및 갭허용 병합으로 개선됨, `Docs/20260712_Group Piping Pattern Extraction.md` 참조). 핵심 컬럼: `EQUIPMENT_TAG`, `UTILITY_GROUP`, `UTILITY`, `N_MEMBERS`, `PITCH_MM`, `PITCH_CV`, `IS_EQUAL_SPACING`, `OFFSET_AXIS`(HORIZONTAL/VERTICAL/MIXED), `PATTERN_SEQ`(예: `"XYZ"`), `MEMBER_GUIDS`, `GEOM_3D`/`TRUNK_GEOM_3D`, `FEAT`(60차원 벡터, pgvector).

신규 설계 관점에서 이 데이터가 알려주는 것: *"이 장비 + 이 유틸리티그룹의 배관은 CSF구간에서 보통 N개가 피치 P(mm)로 수평/수직으로 묶여서 진행한다"* — 즉 **신규 배관을 완전히 새 경로로 뽑기보다, 기존 다발에 합류(join)시켜야 하는지 판단하는 근거**가 된다.

### 2.3 30차원 형상 유사도 벡터와 Top-K 검색 (`TB_ROUTE_FEATURE_VECTOR`, `TopKSearchStandalone`)

`Tools/Extract_Design_Pattern.py`(과거 `learn_design_features.py`, 현재는 삭제되고 이 파일로 대체됨)가 경로마다 30차원 벡터를 산출해 pgvector `vector(30)` 컬럼에 HNSW 코사인 인덱스로 저장한다.

| 차원 | 그룹 | 가중치 | 의미 |
| --- | --- | --- | --- |
| `[0:3]` | start_topology | 0.20 | 시작 세그먼트 진행 단위벡터 |
| `[3:6]` | end_topology | 0.20 | 종단 세그먼트 진입 단위벡터 |
| `[6:9]` | displacement | 0.15 | 시작→종단 변위(dx,dy,dz), 프로젝트 내 최대값으로 정규화 |
| `[9:12]` | bounding_box | 0.15 | 바운딩박스 크기(축별) |
| `[12:21]` | segment 1/2/3 | 0.06×3 | 경로를 3등분 리샘플링한 구간별 진행방향 |
| `[21]` | total_len | (env_cost 그룹에 포함) | 총 길이 |
| `[22:25]` | env_cost | 0.12 | 장애물 이격 여유, 우회 비율, 장애물 근접 시 Z변화량 |
| `[25:30]` | arrow_pattern | 0.15 | X/Y/Z축별 이동거리 비율 + 굽힘횟수 |

전체 벡터는 그룹별 `sqrt(weight × 30 / 그룹내차원수)`로 스케일링 후 L2 정규화된다.

`TopKSearchStandalone/TopKSearchStandalone.cs`가 이 벡터로 실제 Top-K 유사경로 검색을 수행한다:

1. **후보 조회**: 시작/종단 좌표만으로 부분 벡터(구조 관련 차원은 0으로 채움)를 만들어 `ORDER BY FEATURE_VECTOR <=> @vec::vector LIMIT max(k×30, 150)`으로 pgvector ANN 조회
2. **하이브리드 재정렬(rerank)**: `Combined = 0.50×posScore(종단변위 유사도) + 0.30×patScore(방향패턴 Levenshtein + 코사인) + 0.20×vecScore(코사인 유사도)`

**현재 소비처**: `TopKSearchStandalone`의 결과를 실제로 활용하는 코드는 `RubberBandRoutingSuite`가 아니라 **별도의 구식 WPF 앱 `AutoRouteFinder`**이며, 그마저도 (a) 단순 3D 시각화, (b) 별도의 그리드 기반 엔진(`AutoRoutingLibrary.Routing3DEngine`)에 코리도(corridor) 셀 가중치를 살짝 얹어주는 소프트 바이어스 정도에 그친다.

### 2.4 기타 특징 테이블

`Tools/Extract_Design_Pattern.py`가 함께 산출하는 보조 테이블들 — 아직 신규설계 소비처가 없는 원재료 데이터:

| 테이블 | 내용 |
| --- | --- |
| `TB_ROUTE_FEATURE_ANCHOR` | 시작/종단 PoC 접속면·확신도·앵커점·첫 엘보점 |
| `TB_ROUTE_FEATURE_BUNDLE_TEMPLATE` | 다발 단위 템플릿(선호 랙고도, 트렁크축, 대표 중심선) |
| `TB_ROUTE_FEATURE_OBSTACLE_RELATION` | 배관-장애물 최단거리/이격여유/우회방향 |
| `TB_ROUTE_FEATURE_GROUP_PROFILE` | 유틸리티그룹 단위 집계 프로필(선호 접속면, 선호 랙고도, 트렁크 중심선) |

### 2.5 Neo4j 지식그래프

`Tools/export_to_neo4j.py`가 `Project`/`Space`/`Equipment`/`Route`/`BundleGroup`/`CorridorPattern` 노드와 `CONNECTED_TO`/`MEMBER_OF`/`PASSES_THROUGH`/`BELONGS_TO` 관계로 그래프를 구성한다. `Docs/knowledge_graph_neo4j_design_patterns_spec.md`에 실제 스키마와 일치하는 Cypher 쿼리(장비별 다발-공간 통계, 이종 유틸리티 병행 주행 탐지, Louvain 커뮤니티 탐지)가 검증되어 있다. 단, 이 그래프와 §2.3의 30차원 pgvector 검색은 **서로 연결되지 않은 독립적인 두 유사도 메커니즘**이다.

### 2.6 현재 활용 현황(Gap) — 결정적으로 확인된 사실

`RubberBandRoutingSuite/src/RubberBandRouting.Engine/`의 라우팅 알고리즘(`ManagedRubberBandEngine.cs`/`NativeRubberBandEngine.cs`)은 순수 기하학적 직교 A* + 되돌아보기 직선화(rubber-band) 알고리즘이며, **DB의 학습된 테이블을 전혀 조회하지 않는다.** 인터페이스는 다음과 같다:

```csharp
RubberBandResult Route(Vec3 start, Vec3 end, IEnumerable<Aabb> obstacles,
    IEnumerable<RouteFeature>? featureWaypoints = null, RubberBandOptions? options = null)
```

`featureWaypoints`(`RouteFeature{Position, Role, Required}`)가 "고무줄을 이 점들로 통과시켜라"라는 범용 훅으로 이미 존재하지만, 유일한 실사용처인 `MainWindow.xaml.cs`의 `FindMatchedExistingRoute()`/`ExtractExistingRouteFeatures()`는 **학습 테이블을 전혀 쓰지 않고, 원본 `TB_ROUTE_PATH`에서 자체 점수식(종단거리+Z갭×0.5−이름일치보너스−구경일치보너스)으로 가장 비슷한 경로 1건만 찾아 그 경로의 굴곡점을 그대로 통과점으로 넣는** 임시방편 휴리스틱이다. `TB_ROUTE_GROUP_PATTERN`은 `GroupPatternViewerWindow`에서 읽지만 **시각화 전용**이며 `engine.Route(...)` 호출에는 전혀 반영되지 않는다.

**결론**: §2.1~2.5의 모든 학습 데이터는 풍부하게 축적되어 있으나, TopKGen 자체 라우팅 엔진과의 통합은 설계되지 않은 상태다. 이것이 바로 이 문서가 다뤄야 할 공백이다.

### 2.7 장애물 컨텍스트 벡터 (신규 구현, 2026-07-13)

§2.3의 30D 벡터는 장비명/유틸리티/시작·종료좌표만을 검색조건으로 사용하며, `env_cost` 구간(`[22:25]`)은 경로 전체가 있어야 계산 가능해 **신규 쿼리 시점에는 항상 0으로 채워진다** — 즉 시작/종료 좌표 AABB 주변의 실제 장애물(기둥/보) 배치 유사도가 검색에 전혀 반영되지 않는다는 문제가 있었다. 이를 보완하기 위해 RoutingAI의 `ContextVectorEncoder` 설계를 DDW_AI_DB용으로 포팅하여 다음을 구현했다.

- **`Tools/ExtractObstacleContextVector.py`** → `TB_ROUTE_CONTEXT_VECTOR`: 시작·종점 모두 0~500mm/500~1,000mm shell의 기둥·보 개수, AABB 표면거리·방향, free-space와 Tier3 보조 특징을 30차원으로 인코딩한다. 장애물은 `route.BAY`와 같은 항목 및 BAY 미지정 공통 항목으로 격리한다. **좌표 + BAY + 주변 장애물만으로 계산되므로(실제 경로 불필요) 색인 시점과 신규 쿼리 시점에 동일하게 계산 가능**하다는 것이 핵심 — env_cost 구간과의 결정적 차이.
- **`TopKSearchStandalone.cs`의 `--use-obstacle-context` 옵션(`useObstacleContext` 파라미터, 기본 false)**: 활성화 시 쿼리 시점에 `BuildContextVector24Async()`로 컨텍스트 벡터를 즉석 계산해, 하이브리드 재정렬의 4번째 항목(`ctxScore`, 가중치 0.20)으로 반영한다. **1차 pgvector ANN 후보추출에는 절대 섞지 않는다** — RoutingAI의 실측(`TopK_ContextAware_Plan_v2.md`)상 후보추출 단계에 섞으면 기존 30D의 env_cost와 정보가 중복되어 오히려 정확도가 떨어지는 "천장효과"가 확인되었기 때문. 재정렬 전용으로 사용했을 때만(그룹 내 재정렬 Top-1 좌표오차 -37.8%, 페어링 정확도 +5.3%p) 유의미한 개선이 실측되었다.
- **검증**: 827개 경로에 대해 컨텍스트 벡터를 색인한 뒤, 한 경로의 실제 좌표로 자기 자신을 쿼리했을 때 `ctxScore=0.9973`(≈1.0)로 최상위 매칭되었고, 나머지 후보들은 0.76~0.98 사이의 자연스러운 유사도 분포를 보여 Python 색인기와 C# 쿼리 시점 인코더가 수치적으로 일치함을 확인했다.

**부수 발견(데이터 정합성)**: 검증 과정에서 `TB_ROUTE_FEATURE_VECTOR.EQUIPMENT_NAME`이 같은 물리 장비에 대해 `"WTNHJ02"`(정제형)와 `"WTNHJ02_"`(끝 `_` 포함, `TB_ROUTE_PATH` 쪽 형태)가 **혼재**되어 있음을 확인했다 — §2.6에서 다룬 `EQUIPMENT_NAME` vs `EQUIPMENT_TAG` 불일치 문제와 유사한 패턴이 `TB_ROUTE_FEATURE_VECTOR` 내부에도 존재한다는 뜻이다. `TopKSearchStandalone`의 `EQUIPMENT_NAME` 완전일치 필터가 이 두 표기를 다른 장비로 취급해 일부 후보를 놓칠 수 있다 — §8에 알려진 제한사항으로 추가.

---

## 3. 이미 검증된 선행 사례: RoutingAI의 AutoRouteDesigner

자매 저장소 `D:\DINNO\DEV\AI-AutoRouting\RoutingAI\src\AutoRouteDesigner\`에 **"기존 설계를 학습해 신규 경로를 생성"하는 베타(β) 단계의 종단간(end-to-end) 파이프라인이 이미 구현되어 실측 성능까지 문서화**되어 있다(`RoutingAI/docs/RoutingAI_Overview_v1.md`, `RoutingAI/docs/AutoRouteDesigner_Plan_v1.md`). WPF UI에 "자동설계(β)" 버튼으로 연결되어 있다.

> DB는 `AUTOROUTINGV7`으로 TopKGen의 `DDW_AI_DB`와 별개다. 스키마 이관 또는 동기화가 선행되어야 TopKGen 데이터에 바로 적용 가능하다(§6 로드맵 참조).

### 3.1 아키텍처 개요 — Stage A→D

```text
Stage A. Reference Search (reference_search.py)
  └ 신규 요청(장비/유틸리티/시작PoC/종단후보)의 30D 쿼리벡터 생성
    → TB_ROUTE_DESIGN_GROUP에서 pgvector 코사인 유사도로 후보 그룹 검색
      (필터 폴백: 장비+유틸리티그룹+유틸리티 → 유틸리티그룹+유틸리티 → 유틸리티그룹 → 전체)
Stage B. Pairing (그룹 내에서 가장 적합한 참조 경로 1건 선정, 68.29% Top-1 정확도)
Stage C. Route Generation (route_templating.py::generate_route())
  └ 참조 경로의 점 시퀀스를 Rodrigues 회전 유사변환으로 신규 시작/종단에 맞춰 변형
Stage D. Collision Check & Local Reroute (obstacle_check.py)
  └ 장애물 AABB와 충돌 시 문제 구간만 국소 A*로 우회
```

### 3.2 유사도 검색 방법

`TB_ROUTE_DESIGN_GROUP`은 `(PROCESS_NAME, EQUIPMENT_NAME, UTILITY_GROUP, UTILITY)`로 묶은 그룹마다 **멤버 경로들의 30차원 특징벡터를 L2정규화 평균**한 "대표 벡터"를 미리 계산해둔 테이블이다(TopKGen의 `TB_ROUTE_FEATURE_VECTOR`와 동일한 30D 레이아웃). 검색은 이 대표 벡터에 대해 pgvector `<=>` 코사인 거리로 이루어진다 — TopKGen의 `TopKSearchStandalone.cs`가 개별 경로 단위로 하는 것과 원리는 같고, **그룹(대표) 단위로 미리 집계**해둔다는 점이 다르다.

### 3.3 경로 생성 알고리즘 — Rodrigues 회전 유사변환

핵심 통찰: *"참조 경로의 형상(굴곡 패턴)은 그대로 유지한 채, 시작점→종단점을 잇는 전체 방향/길이만 새 요청에 맞게 통째로 회전·스케일·평행이동한다."*

```
scale = |new_end - new_start| / |ref_end - ref_start|   (0.5~2.0로 클램프)
R     = Rodrigues 공식으로 구한 회전행렬 (ref 방향벡터 → new 방향벡터)
p_new = R @ (p_ref - ref_start) × scale + new_start
```

스케일이 클램프되어 끝점이 정확히 `new_end`에 도달하지 못하면 마지막 점만 스냅 보정한다. 변환 후 장애물 충돌을 검사해, "구조물(보/기둥) 관통"은 정보성으로만 기록하고 "실충돌(하드 콜리전)"만 국소 A*로 우회 처리한다.

### 3.4 실측 성능 지표 및 한계 (`RoutingAI_Overview_v1.md` §5.5, §7.3-7.5)

| 지표 | 값 | 조건 |
| --- | --- | --- |
| Stage B Top-1 페어링 정확도 | **68.29%** (기준선 41.57% 대비 개선) | 27개 그룹 / 1,744쌍 |
| Stage C 자기참조(self-reference) 생성 성공률 | **100%** (80/80) | |
| 자기참조 길이 편차 | **+11.91%** | |
| 자기참조 실충돌률 | **0%** | 구조물 관통(545건)은 별도 집계, 비차단 처리 |
| Phase 5 홀드아웃(leave-one-out) Top-1 | **9.33%** | 75샘플, 홀드아웃 경로를 참조군에서 제외 — "보수적/어려운" 지표로 명시됨 |
| Phase 5 홀드아웃 길이 편차 | **81.6%** | |
| Phase 5 무충돌률 | 92% | |

**해석**: 자기 자신을 참조로 쓸 수 있는 상황(=학습 데이터에 매우 가까운 신규 요청)에서는 사실상 완벽하게 작동하지만(100%/0% 충돌), 진짜 미지의 신규 설계에 대한 일반화 성능(Phase 5)은 아직 약하다 — 문서 스스로 "Phase 6 이후 개선 필요" 항목으로 명시하고 있다. **결론: 이 접근은 "완전 자동 생성"보다 "숙련자를 위한 초안(1차 안) 제시 도구"로 쓰기에 적합한 성숙도**다.

---

## 4. 학습 방법론 선택지 비교

| 방법 | 원리 | 필요 데이터 | 장점 | 단점 | 채택 상태 |
| --- | --- | --- | --- | --- | --- |
| **① 검색기반 사례추론 (Retrieval / Case-Based Reasoning)** | 신규 요청과 가장 유사한 과거 경로를 검색해 기하변환으로 재사용 | 30D 벡터 + pgvector | 설명 가능, 소량 데이터로도 동작, RoutingAI에서 검증됨 | 학습 데이터 밖 완전 신규 배치엔 취약(Phase 5) | **RoutingAI에 구현·검증됨** |
| **② 통계적 템플릿 평균화** | 동일 조건 그룹의 평균 Rise/Offset/피치 등을 그대로 신규 경로 스텁/피치값으로 사용 | `TB_ROUTE_STUB_TEMPLATE`, `TB_ROUTE_GROUP_PATTERN` | 구현 단순, 해석 쉬움 | 경로 전체가 아닌 국소(스텁/피치) 정보만 제공 | **TopKGen에 데이터/조회 로직까지 구현됨, 엔진 미연동** |
| **③ 그래프 기반 추천 (Neo4j)** | 위상(토폴로지) 유사성/커뮤니티 구조로 "이 장비 주변엔 보통 이런 다발이 있다"를 추천 | Neo4j KG | 관계형 질의(공간/장비 간 관계)에 강함, 커뮤니티 탐지로 숨은 패턴 발견 가능 | 좌표/형상 정보 없음 → 단독으로 경로 생성 불가, ①②의 보조 역할 | **그래프 구축 및 Cypher 쿼리 검증됨, 라우팅 미연동** |
| **④ 딥러닝 기반 생성모델 (시퀀스/그래프 신경망)** | 좌표 시퀀스를 학습해 새 경로를 직접 생성 (예: Transformer, GNN) | 수천~수만 건의 정제된 경로 + 레이블 | 국소 형상까지 학습 가능, 잠재적으로 최고 성능 | 현재 데이터량(수백~1천여 개 그룹패턴, 827개 경로)으로는 과적합 위험, 설명 불가능성, 개발/검증 비용 큼 | **시기상조 — ①②③으로 충분한 개선 여지가 남아있음** |

**권장**: ① (RoutingAI 검증 완료) + ② (TopKGen 자체 보유) 를 결합한 **하이브리드 검색-템플릿 조합**을 1차 목표로 하고, ③은 "같은 장비 주변 다른 유틸리티가 어떻게 묶여 다니는지" 등 검색 필터링 보조로 활용한다. ④는 ①~③ 파이프라인이 프로덕션에 자리잡고 데이터가 수천 건 이상 축적된 후 재검토한다.

---

## 5. TopKGen 데이터를 신규 설계에 활용하는 구체적 파이프라인 설계

### 5.1 시나리오

신규 배관 1건 설계 요청: `(장비, 유틸리티그룹, 유틸리티, 구경, 시작 PoC 좌표, 종단 PoC 좌표/후보)`이 주어졌을 때, 이를 세 구간(Start Stub / Middle Trunk / End Stub, `PathSegmenter.py`와 동일한 삼분할 개념)에 대해 각각 다른 전략으로 채워 넣는다.

```text
신규 요청 (장비, 유틸리티그룹, 유틸리티, 구경, 시작PoC, 종단PoC)
 │
 ├─ Step 1. Start Stub 후보 생성 ── §5.2
 ├─ Step 2. Middle Trunk 그룹배관 정합 ── §5.3
 ├─ Step 3. End Stub 후보 생성 ── §5.4
 │
 ▼
 RouteFeature(featureWaypoints) 리스트로 통합
 │
 ▼
 RubberBandRouting.Engine.Route(start, end, obstacles, featureWaypoints) ── §5.5
 │
 ▼
 충돌 검증 및 등급 판정 ── §5.6
 │
 ▼
 "AI 추천 초안" 으로 사용자에게 제시 (자동 확정 아님, RoutingAI Phase5 홀드아웃 성능 고려)
```

### 5.2 Step 1 — Start Stub 적용

`Tools/ExtractStubPatterns.py`의 `query_templates()`(4단계 폴백: 장비+유틸리티그룹+유틸리티+구경 → 장비+유틸리티그룹+유틸리티 → 유틸리티그룹+유틸리티 → 유틸리티그룹)로 `TB_ROUTE_STUB_TEMPLATE`을 조회하고, `instantiate_stub()`으로 신규 시작 PoC 좌표에 그 템플릿의 `AVG_RISE_MM`/`AVG_OFFSET_MM`/`FACE`/`DIR_SEQ`를 적용해 2~3점짜리 구체 스텁 폴리라인과 `free_point`(중앙 라우터가 이어받을 지점)를 얻는다. 이 점들을 `RouteFeature(Role=StartStub, Required=true)`로 변환한다.

### 5.3 Step 2 — Middle Trunk 그룹배관 정합

1. `(EQUIPMENT_TAG, UTILITY_GROUP, UTILITY)`로 `TB_ROUTE_GROUP_PATTERN`을 조회해, 신규 요청의 시작/종단 위치와 공간적으로 겹치는 기존 다발(`SECTION_BOUNDS`가 신규 경로의 대략적 이동 범위와 교차)이 있는지 확인한다.
2. 겹치는 다발이 있고 `IS_EQUAL_SPACING=true`이면: 신규 배관을 그 다발의 `PITCH_MM`만큼 옆(또는 위/아래, `OFFSET_AXIS` 방향)으로 띄운 평행 경로로 유도 — 다발의 `TRUNK_GEOM_3D`를 참조선으로 삼아 `RouteFeature(Role=TrunkGuide)` 통과점들을 등간격으로 생성한다.
3. 겹치는 다발이 없으면: §3.2~3.3의 RoutingAI 접근(유사 그룹 검색 → 참조 경로 유사변환)으로 Middle Trunk 형상을 생성한다.

### 5.4 Step 3 — End Stub 적용

Start Stub과 동일한 방식이나 `STUB_KIND=END`, `ANCHOR_KIND=DUCT/LATERAL`로 조회. §2절에서 다룬 `END_ENTRY_DIR_X/Y/Z`(`Tools/PathSegmenter.py`, 이번 세션에 추가된 진입방향 컬럼)를 함께 활용해, 마지막 구간이 덕트/레터럴 접속면에 수직으로 진입하도록 방향을 고정한다.

### 5.5 Step 4 — RubberBandRouting 엔진 연동

Step1~3에서 만든 `RouteFeature` 리스트를 `IRubberBandEngine.Route(start, end, obstacles, featureWaypoints)`의 `featureWaypoints` 인자로 전달한다. 이것이 §2.6에서 확인한 **이미 존재하는, 그러나 지금은 학습 데이터를 전혀 쓰지 않는 통합 지점**이다 — 새 엔진을 만들 필요 없이 이 훅에 데이터를 채우기만 하면 된다. 현재 `MainWindow.xaml.cs`의 `FindMatchedExistingRoute()`/`ExtractExistingRouteFeatures()`가 이 훅을 채우는 유일한 코드이므로, **이 두 메서드를 위 Step1~3 로직으로 교체(또는 다중 소스 중 하나로 추가)하는 것이 최소 침습적인 구현 지점**이다.

### 5.6 Step 5 — 충돌 검증 및 등급 판정

RoutingAI의 `obstacle_check.py`처럼, 생성된 경로를 `TB_BIM_OBSTACLE`과 대조해 "구조물 관통(정보성)"과 "실충돌(재시도 필요)"을 구분한다. `RubberBandRouting.Engine`은 이미 `obstacles: IEnumerable<Aabb>`를 받아 회피 라우팅을 시도하므로, 상당 부분은 엔진이 자체적으로 처리한다 — 다만 §3.4의 실측치를 감안해 **생성 결과에는 반드시 "참조 신뢰도"(유사도 점수, 매칭된 그룹의 샘플 수)를 함께 표기**하고, 설계자가 최종 확정하는 반자동(半自動) 워크플로로 운용할 것을 권장한다.

---

## 6. 구현 로드맵 (단계별)

| 단계 | 내용 | 선행조건 | 산출물 |
| --- | --- | --- | --- |
| **0. 데이터 정합성 점검** | `TB_ROUTE_STUB_TEMPLATE`/`TB_ROUTE_GROUP_PATTERN`/`TB_ROUTE_FEATURE_VECTOR`가 최신 상태인지, 프로젝트 커버리지가 충분한지 확인 | 없음 | 커버리지 리포트 |
| **1. Start/End Stub 연동 (PoC)** | §5.2/5.4를 C#으로 이식해 `RouteFeature` 생성기로 구현, 소규모 장비군에 한해 테스트 | 0단계 | `StubTemplateFeatureProvider.cs` (가칭) |
| **2. 그룹배관 정합 연동** | §5.3을 구현해 기존 다발과의 정합 여부 판정 로직 추가 | 1단계 | `GroupPatternFeatureProvider.cs` (가칭) |
| **3. RoutingAI 검색-생성 로직 이식 또는 원격 호출** | `AUTOROUTINGV7` → `DDW_AI_DB` 스키마 정합 후, `reference_search`/`route_templating`을 Python 서비스로 노출하거나 C#으로 이식 | 0단계, DB 통합 결정 | 서비스 API 또는 이식 코드 |
| **4. `RubberBandRoutingSuite` 통합** | 1~3단계 산출물을 `featureWaypoints`로 합쳐 실제 `engine.Route()` 호출에 연결, "AI 추천 초안" UI 버튼 추가 | 1~3단계 | 통합 빌드 |
| **5. 정량 평가** | RoutingAI의 `EvaluateRouteQuality.py`와 동일한 방법론(자기참조/홀드아웃)으로 TopKGen 데이터 기준 성능 측정 | 4단계 | 평가 리포트 |
| **6. 그래프 기반 필터 고도화 (선택)** | Neo4j 커뮤니티 탐지 결과를 검색 단계의 사전 필터로 추가 | 3단계 | Cypher 통합 |

---

## 7. 데이터 품질/커버리지 전제조건

- `TB_ROUTE_STUB_TEMPLATE`은 `--min-samples`(기본 3) 미만인 조합에서는 템플릿이 아예 생성되지 않는다 — 희귀 장비/유틸리티 조합은 폴백 4단계(§2.1)까지 밀려도 매칭이 안 될 수 있음을 UI에서 명시해야 한다.
- `TB_ROUTE_GROUP_PATTERN`은 2026-07-13 기준 263건(§ `Docs/20260712_Group Piping Pattern Extraction.md` §5)으로, 장비/유틸리티 조합 수 대비 커버리지가 아직 제한적이다.
- `TB_ROUTE_FEATURE_VECTOR`의 30차원 정규화 상수(프로젝트 내 최대 변위/바운딩박스 등)가 **프로젝트별로 다르게 계산**되므로, 여러 프로젝트를 넘나드는 유사도 비교 시 벡터 스케일이 프로젝트마다 다를 수 있다는 점에 유의해야 한다(§2.3).

---

## 8. 알려진 위험 및 한계

| 항목 | 내용 |
| --- | --- |
| 홀드아웃 일반화 성능 미흡 | RoutingAI Phase 5 실측: Top-1 9.33%, 길이편차 81.6% — 완전히 새로운 배치에 대한 자동 생성 정확도는 아직 낮음. "자동 확정"이 아닌 "초안 제시"로 포지셔닝 필요 |
| 구조물 과다 충돌 판정 | BIM 보/기둥 AABB가 실제 형상보다 큰 입체로 단순화되어 있어 "구조물 관통"이 과다 집계됨 (545건, RoutingAI 사례) — 이 문제는 TopKGen의 `TB_BIM_OBSTACLE`에도 동일하게 존재할 가능성이 높음 |
| 두 개의 독립적인 30D 벡터 구현체 | `Tools/Extract_Design_Pattern.py`와 `RoutingAI/src/TopKRoutingSearch.py`가 동일한 `TB_ROUTE_FEATURE_VECTOR` 테이블에 동일 레이아웃이지만 서로 다른 코드로 값을 채워 넣음 — 두 파이프라인을 모두 쓸 경우 비트 단위로 동일하지 않은 벡터가 섞일 수 있어 사전 조정(reconciliation) 필요 |
| DB 인스턴스 불일치 | RoutingAI는 `AUTOROUTINGV7`, TopKGen은 `DDW_AI_DB` — 3단계(§6) 착수 전 스키마/데이터 동기화 방안 결정 필요 |
| Neo4j-벡터 검색 미연동 | §2.5의 그래프와 §2.3의 pgvector 검색이 서로 다른 후보군을 낼 수 있음 — 하나를 주, 하나를 보조로 명확히 역할 분담 필요 |
| `EQUIPMENT_NAME` 표기 혼재 (신규 발견, §2.7) | `TB_ROUTE_FEATURE_VECTOR.EQUIPMENT_NAME`에 `"WTNHJ02"`/`"WTNHJ02_"` 두 표기가 혼재 — `TopKSearchStandalone`의 완전일치 필터가 일부 후보를 놓칠 수 있음. `EQUIPMENT_TAG` 기준 정규화 필요(§2.6의 EQUIPMENT_NAME/EQUIPMENT_TAG 문제와 근본 원인 동일) |
| `TB_ROUTE_FEATURE_VECTOR`의 낮은 `TB_ROUTE_PATH` 커버리지 | 전체 7,879건 중 현재 `TB_ROUTE_PATH`(827건)와 매칭되는 것은 827건뿐 — 나머지 7,052건은 과거/타 프로젝트 스냅샷으로 추정됨. `TB_ROUTE_CONTEXT_VECTOR`(§2.7)도 동일하게 827건만 색인되어 있어, 나머지 후보는 `ctxScore=0`으로 처리됨(정상 동작이나 검색 결과 해석 시 유의) |

---

## 9. 참고 파일 목록

| 구분 | 경로 |
| --- | --- |
| Start/End Stub 추출·적용 | `Tools/ExtractStubPatterns.py` |
| 그룹배관(다발) 추출 | `Tools/ExportGroupPattern.py`, `Docs/20260712_Group Piping Pattern Extraction.md` |
| 30D 특징벡터/보조 테이블 | `Tools/Extract_Design_Pattern.py` |
| Top-K 유사도 검색(단건) | `TopKSearchStandalone/TopKSearchStandalone.cs` |
| 장애물 컨텍스트 벡터 (신규, §2.7) | `Tools/ExtractObstacleContextVector.py`, `Tools/sql/create_route_context_vector_table.sql` |
| Neo4j 지식그래프 이관/쿼리 | `Tools/export_to_neo4j.py`, `Docs/knowledge_graph_neo4j_design_patterns_spec.md` |
| 삼분할(Start/Middle/End) 세그멘테이션 | `Tools/PathSegmenter.py`, `Docs/20260712_Path Segmentation.md` |
| TopKGen 라우팅 엔진 | `RubberBandRoutingSuite/src/RubberBandRouting.Engine/`, `RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml.cs` |
| **RoutingAI 검증된 생성 파이프라인** | `RoutingAI/src/AutoRouteDesigner/{reference_search,route_templating,group_builder,template_builder}.py` |
| RoutingAI 설계/성능 문서 | `RoutingAI/docs/AutoRouteDesigner_Plan_v1.md`, `RoutingAI/docs/RoutingAI_Overview_v1.md` |
