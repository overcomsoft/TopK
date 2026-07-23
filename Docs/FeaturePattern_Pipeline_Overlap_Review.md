# 기존설계 특징점/패턴 추출 파이프라인 7종 — 기능 중복·유사성 검토

## 0. 검토 배경 및 방법

Top-K 검색·자동배관설계가 참조하는 기존설계 특징점/패턴 DB는 `Tools/*.py` 7개 생성기가 각각
독립적으로 만든다(`[특징점생성]` 다이얼로그, `TopK.3DViewer/Models/GeneratorDefinition.cs` 참고).
7개가 별도 스크립트·별도 개발 시점(가장 오래된 것은 30D 특징벡터, 가장 최근은 Bend Feature
Point)으로 만들어지다 보니, "이미 다른 파이프라인이 계산한 것을 또 계산하고 있는 것은 아닌지"를
코드 기준으로 점검했다.

검토 범위:
- 각 생성기(`Tools/*.py`)의 실제 추출 로직과 저장 컬럼
- 각 테이블을 실제로 읽는 소비처(`TopKSearchStandalone/`, `TopK.3DViewer/`,
  `RubberBandRoutingSuite/`, `GroupPatternViewer/` 등, `Tools/`·`Docs/` 제외) — grep과 코드 리딩으로 확인
- 소비처가 없는 "생성만 되고 안 쓰이는" 테이블 여부

결론부터 요약하면: **완전히 동일한 계산을 두 번 하는 진짜 중복은 발견되지 않았다.** 다만 (1) 같은
목적("이 구간이 stub인지 trunk인지")을 서로 다른 임계값으로 3곳에서 독립 구현한 사례,
(2) 설계 문서상 연동이 계획되었지만 실제 코드에는 구현되지 않아 이름만 비슷하고 개념이 갈라져
버린 사례, (3) 만들기만 하고 어디서도 읽지 않는 테이블이 하나 있다. 아래에서 항목별로 정리한다.

---

## 1. 7개 파이프라인 한눈에 보기

| # | 이름 | 스크립트 | 테이블 | 단위(granularity) | 핵심 신호 |
|---|---|---|---|---|---|
| 1 | 30D 특징벡터 | `Extract_Design_Pattern.py` | `TB_ROUTE_FEATURE_VECTOR` | **route 1개당 1행** | 시작/종점 방향, 변위, 바운딩박스, 3등분 형상, 길이, 장애물 회피비용(env_cost), 축 이동비율+굴곡수(arrow_pattern) — 30차원 |
| 2 | Context Vector | `ExtractObstacleContextVector.py` | `TB_ROUTE_CONTEXT_VECTOR` | route 1개당 1행 (시작/종점 2블록) | 시작·종점 주변 기둥/보(컬럼/빔) 근접도·방향·거리, 층 통과수, 보 평행도 — 30차원 |
| 3 | Path Segmentation | `PathSegmenter.py` | `TB_ROUTE_PATH_SEGMENTATION` | route 1개당 1행 | Start Stub/Middle Trunk/End Stub 3구간 지오메트리, End Stub 진입방향(`END_ENTRY_DIR_*`) |
| 4 | Group/Bundle Pattern(다발배관) | `ExportGroupPattern.py` | `TB_ROUTE_GROUP_PATTERN` | **평행 구간(Section) 1개당 1행** — route가 아님 | 국소적으로 나란히 달리는 배관 묶음의 피치/등간격 여부/축/굴곡패턴 |
| 5 | Stub Pattern | `ExtractStubPatterns.py` | `TB_ROUTE_STUB_PATTERN`(+`TEMPLATE`) | route 1개당 2행(시작/종점) | 앵커(장비/덕트/lateral) 기준 stub 형상, 방향, rise/offset — 신규 stub 생성(`make-stub`)까지 지원 |
| 6 | Utility Pipe Group Vector | `BuildUtilityPipeGroupVectors.py` | `TB_ROUTE_UTILITY_GROUP_VECTOR` | **속성 동일 그룹 1개당 1행** — (장비+유틸리티) 전체 route 집합 | 그룹의 30D 특징벡터/Context 벡터 평균(centroid), 배치통계(`ARRANGEMENT_VECTOR_JSON`) |
| 7 | Bend Feature Point | `ExtractBendFeaturePoints.py` | `TB_ROUTE_BEND_FEATURE_POINT`(+`PATTERN`) | **꺾임점 1개당 1행** — route당 여러 행 | 개별 꺾임의 위치·전이유형·원인(CAUSE: 장애물회피/목적지진입/그룹정렬 등) |

같은 "그룹"이라는 단어를 쓰는 4번과 6번, 같은 "stub/구간"을 다루는 3번·5번·7번이 검토의 핵심
포인트다.

---

## 2. 실제 소비 구조 — Top-K 검색은 "하나"가 아니라 "넷"

> **2026-07-22 갱신**: 이 문서 작성 당시 검색 코드가 전혀 없던 Group/Bundle Pattern·Stub
> Pattern에 `TopKSearchStandalone/GroupPatternSearch.cs`·`StubPatternSearch.cs`를 신규
> 추가하고, `TopK.3DViewer`에 "다발배관 패턴"/"Stub 패턴" 검색 모드로 연결했다. Path
> Segmentation도 `TopK.3DViewer`에 "구간분할 표시" 오버레이 체크박스로 연결했다(검색 대상이
> 아니라 선택된 route에 겹쳐 그리는 참고용 표시 — 3.2/4.2절의 설계 결정대로). 아래 2.3~2.5는
> 이 갱신을 반영했고, 2.4의 "생성만 되고 소비처가 없는 테이블" 목록에서 Stub Pattern은 빠졌다.

`TopKSearchStandalone/`에는 서로 독립된 네 개의 검색 파이프라인이 있고, 각각 필요한 테이블을
**의도적으로 조합**해서 쓴다. 7개 테이블을 각각 따로 검색하는 구조가 아니다.

### 2.1 route 단위 Top-K 검색 (`TopKSearchStandalone.cs`)
- `TB_ROUTE_FEATURE_VECTOR` — 1차 후보 추출(pgvector ANN, `ORDER BY ... <=> @vec`).
- `TB_ROUTE_CONTEXT_VECTOR` — 후보가 뽑힌 **뒤에** LEFT JOIN해서 재정렬(rerank)에만 사용, 가중치
  0.10. ANN 1차 검색에는 절대 섞지 않는다(아래 3.1 참고).
- 최종 스코어 = `posScore(0.45) + patScore(0.27) + vecScore(0.18) + ctxScore(0.10)`.

### 2.2 Utility Pipe Group 단위 Top-K 검색 (`UtilityPipeGroupSearch.cs`, `UtilityPipeGroupMatcher.cs`)
- `TB_ROUTE_UTILITY_GROUP_VECTOR` — 1차 후보 추출(centroid ANN).
- `TB_ROUTE_FEATURE_VECTOR`, `TB_ROUTE_CONTEXT_VECTOR` — 그룹 멤버 route별로 조인해서 재사용.
- `TB_ROUTE_BEND_FEATURE_POINT` — 있으면 멤버 간 헝가리안 매칭에 원인(CAUSE) 정보를 추가 반영,
  없으면(테이블 미생성 시 SQLSTATE 42P01) 조용히 건너뜀 — **선택적 강화 신호**로 설계됨.

`TopK.3DViewer`의 라디오버튼("개별 배관" / "Utility 배관 그룹")이 바로 이 두 파이프라인을
전환하는 UI다.

### 2.3 다발배관 패턴 / Stub 패턴 Top-K 검색 (`GroupPatternSearch.cs`, `StubPatternSearch.cs`)
- `TB_ROUTE_GROUP_PATTERN."FEAT"`(vector(60), HNSW L2) — 기존 항목 하나를 쿼리로 골라
  같은 `EQUIPMENT_TAG`/`UTILITY_GROUP`(옵션 `UTILITY`) 안에서 형상이 비슷한 평행구간을 찾는다.
  UtilityPipeGroupSearch와 달리 멤버 단위 Hungarian 매칭은 없다 — 한 행이 이미 완결된 형상
  요약이라 route끼리 짝지을 필요가 없다.
- `TB_ROUTE_STUB_PATTERN."FEAT"`(vector(24))/`"DIR_UNIT"`(vector(3)) — 같은
  `STUB_KIND`/`ANCHOR_KIND` 안에서 FEAT ANN으로 후보를 넓게 뽑은 뒤 `DIR_UNIT` 코사인유사도를
  얹어 재정렬한다(route Top-K가 1차 ANN과 재정렬을 분리하는 것과 같은 아이디어).
- `TopK.3DViewer`의 "다발배관 패턴"/"Stub 패턴" 검색 모드가 이 두 클래스를 직접 호출한다.

### 2.4 그 외 — 검색이 아니라 선택 route 오버레이/표시 전용
- `TB_ROUTE_PATH_SEGMENTATION`은 벡터가 없는 순수 지오메트리라 Top-K 검색 대상이 될 수 없다
  (3.2/4.2절 참고). `TopK.3DViewer`의 "구간분할 표시" 체크박스가
  `ViewerDatabaseService.LoadPathSegmentationBatchAsync`로 현재 선택된 route의 Start
  Stub/Middle Trunk/End Stub을 3색으로 겹쳐 그린다 — 검색이 아니라 참고용 오버레이다.
- `RubberBandRoutingSuite`의 뷰어(`RubberBandRouting.Viewer`)도 별도로 같은 테이블을 시각화
  목적으로 조회한다(변경 없음). C++/C# 실제 라우팅 솔버에는 여전히 전달되지 않는다.
- `TB_ROUTE_BEND_FEATURE_POINT` — `TopK.3DViewer`가 꺾임점을 원인별 색상 구체로 렌더링하는 데도
  쓴다(2.2의 검색 강화와는 별개의 순수 표시 용도 사용).

### 2.5 생성만 되고 소비처가 없는 테이블
- **`TB_ROUTE_BEND_FEATURE_PATTERN`**(꺾임점을 구조적 키로 묶은 집계 테이블) — `Tools/`·`Docs/`
  밖에서는 어디서도 참조되지 않는다. 원 데이터인 `TB_ROUTE_BEND_FEATURE_POINT`만 소비되고,
  이 위에서 계산된 집계 패턴 자체는 아직 활용처가 없다.

**자동배관설계 생성 로직**(기존 route를 찾는 검색이 아니라 새 route 지오메트리를 만드는 로직)에
실제로 연결된 파이프라인은 여전히 7개 중 하나도 없다 — 지금까지 추가된 것은 모두 검색 또는
표시(오버레이) 용도다. `ExtractStubPatterns.py`의 `make-stub` 명령(신규 stub 좌표 생성)도
스크립트 자신의 CLI에서만 호출되고, 외부에서 이를 실행하는 코드는 여전히 없다.

---

## 3. 발견된 중복·유사점 상세

### 3.1 [해소됨] 30D 특징벡터의 `env_cost`(dims 22-24) ↔ Context Vector — 동일 성격 신호, 의도적으로 분리 사용

- 30D 특징벡터의 22~24번 차원(`env_cost`)은 장애물 회피 관련 값(여유거리 부족분, 우회비율,
  장애물 근처 Z 편차 — 원천은 `TB_ROUTE_FEATURE_OBSTACLE_RELATION`)이고, Context Vector는
  시작/종점 주변 기둥·보 근접도를 계산한다. **둘 다 "장애물 인접도"를 다룬다는 점에서 신호가
  겹친다.**
- 이미 개발 과정에서 실제로 겹치는 걸 확인하고 대응한 흔적이 코드에 남아 있다:
  `ExtractObstacleContextVector.py`와 `Docs/20260713_Learned Design Data Reuse Strategy.md`,
  `TopKSearchStandalone.cs:184-196`이 명시적으로 "1차 ANN 검색에 두 신호를 같이 넣으면 정보
  중복으로 정확도가 떨어지는 천장효과(ceiling effect)가 있다"고 기록하고 있고, 그래서
  Context Vector는 **재정렬 단계에서만, 낮은 가중치(0.10)로만** 쓰도록 아키텍처를 분리했다.
- **판단: 중복이지만 이미 인지되고 해결된 중복.** 추가 조치 불필요. 다만 향후 두 파이프라인 중
  하나를 수정할 때 이 결합(재정렬 전용 제약)을 깨뜨리지 않도록 주의가 필요하다.

### 3.2 [경계 필요] 30D 특징벡터의 3등분 형상(dims 12-20) ↔ Path Segmentation — 같은 목적, 다른 정의가 공존

- 30D 특징벡터는 route 폴리라인을 **등호(等弧) 길이 기준 3등분**해서 각 구간의 방향을 인코딩한다
  (`resample_polyline_points(pts, 3)`) — 순수 기하학적 분할이며 CSF 평면이나 설비 인터페이스
  개념이 없다.
- Path Segmentation은 **CSF 평면(Z=13,700mm) 통과 여부 + 축 진행 스캔**으로 Start
  Stub/Middle Trunk/End Stub을 나눈다 — 엔지니어링 의미가 있는 경계다.
- 두 파이프라인은 서로를 참조하지 않고 완전히 독립적으로 "3구간 분할"을 각자 계산한다. 목적은
  같지만("route를 시작/중간/끝으로 나눠서 형상 비교") 정의가 달라 **같은 route에 대해 서로 다른
  경계 위치를 낼 수 있다.**
- **판단: 즉시 위험한 버그는 아니지만(용도가 다르다 — 하나는 30D 벡터의 조악한 요약, 하나는
  RubberBandRouting 뷰어가 실제로 쓰는 정밀 지오메트리) 두 "3분할"이 존재한다는 사실 자체가
  향후 유지보수자를 혼란시킬 수 있다.** 30D 벡터의 3분할을 Path Segmentation 결과로 대체하는
  안은 30D 벡터가 route 존재 여부와 무관하게(성능상) 항상 계산 가능해야 한다는 제약과 충돌할
  수 있어 재설계 범위이므로, 이번 검토에서는 "구조적으로 다른 정의가 공존함을 문서화"만 권고한다.

### 3.3 [조치 완료] Stub 경계/진입방향 계산이 3곳에서 독립 구현되어 있었음

같은 질문("이 route의 어디까지가 stub이고 어디부터 trunk인가")에 대해 서로 다른 답을 낼 수 있는
**3개의 독립 구현**이 존재했다:

| 구현 | 기준(모듈 docstring 상 설계) | CSF 평면 인식 |
|---|---|---|
| `PathSegmenter.segment_route()` | Z=13,700mm 평면 통과 + 축 진행 스캔(50mm jitter) | 있음 |
| `ExtractStubPatterns.walk_stub()`(리팩터링 전) | 첫 수직 구간 + 800mm 리드인(최대 4000mm), 250mm jitter | 없음 |
| `ExtractBendFeaturePoints.py` | ELBOW IP 좌표 복원 후 `PathSegmenter.segment_route()` **재호출** | 있음(간접) |

**[후속 확인]** 실제 코드를 라인 단위로 대조한 결과, `walk_stub()`의 실행 경로는 모듈
docstring이 말하는 "250mm jitter, 800mm 리드인, 4000mm 상한" 규칙을 구현하고 있지 않았다.
그 규칙에 해당하는 상수(`STUB_MIN_DIR_RUN_MM`, `STUB_MAX_MM`)와 함수(`dir_runs`,
`merge_short_runs`)는 정의만 되어 있고 추출 경로에서 **한 번도 호출되지 않는 죽은 코드**였다.
`walk_stub()`이 실제로 실행하던 알고리즘은 `PathSegmenter.segment_route()`의 비-CSF 폴백
분기와 50mm jitter 임계값까지 거의 그대로 복제한 코드였고, 유일한 실질적 차이는 Start Stub의
CSF 평면(Z=13,700mm) 인식 여부뿐이었다 — 임계값이 다른 게 아니라, **같은 알고리즘이 두 파일에
따로 유지보수되고 있었다는 게 진짜 문제**였다.

- `ExtractBendFeaturePoints.py`는 자체 구현 없이 `PathSegmenter`의 함수를 그대로 재사용하고
  있었으므로, 사실상 "PathSegmenter류(CSF 인식)"와 "ExtractStubPatterns류(CSF 미인식이지만
  그 외엔 동일 로직 복제)" 2개의 정의가 갈라져 있었다.
- `PathSegmenter.py`의 `END_ENTRY_DIR_X/Y/Z`와 `ExtractStubPatterns.py`의 `DIR_UNIT`도 같은
  목적(종점 진입 방향 벡터)을 각자 계산하고 있었고, 실제 운영 중인 `RubberBandRouting.Engine`
  (`PostgresRoutingDataLoader.cs:348-355`)은 `TB_ROUTE_PATH_SEGMENTATION.END_ENTRY_DIR_*`만
  읽는다 — `TB_ROUTE_STUB_PATTERN`의 동급 값은 프로덕션에 쓰이지 않는다(2.4 참고).

**조치:** `Tools/ExtractStubPatterns.py`를 리팩터링해 `walk_stub()`(자체 재구현)을 제거하고,
`PathSegmenter.segment_route()`를 직접 import해서 재사용하도록 바꿨다.

- `extract_samples()`가 route 하나당 `orient_points(route.points, route.source_pos)`로 한 번만
  정규 정렬한 뒤 `segment_route()`를 **1회** 호출하고, 그 결과를 START/END 두 `make_sample()`
  호출이 나눠 쓴다(기존에는 stub_kind별로 별도 정렬 + 별도 컷팅 로직을 두 번 실행했다).
- `make_sample()`은 STUB_KIND="START"면 `segment_route()`의 `start_stub_pts`(CSF 인식 포함)를,
  "END"면 `end_stub_pts`를 PoC가 앞에 오도록 뒤집어서 그대로 사용한다.
- `validate_existing_route()`(진단용 커맨드)도 같은 방식으로 갱신했다.
- 합성 폴리라인으로 `stub_start[0]==source`, `stub_end[0]==target`(PoC-first 정렬 불변식 유지)을
  검증했고, `python -m py_compile`/모듈 import로 구문·순환참조 문제가 없음을 확인했다(실 DB
  기반 회귀 테스트는 미실시 — 아래 확인 필요 사항 참고).
- `dir_runs`/`merge_short_runs`(추출 경로에서 원래도 안 쓰이던 죽은 함수)와
  `STUB_LEADIN_MM`(→`make-stub`의 신규 stub 인스턴스화 전용, 추출과 무관)은 이번 조치
  범위 밖이라 그대로 두었다.
- `Docs/StubPattern_Extraction_Implementation.md` 4절을 새 알고리즘에 맞게 갱신했다.

**확인 필요:** 이 변경으로 Start Stub이 이제 CSF 평면을 인식하게 되므로, A/F구역(Z≥13700mm)에서
시작하는 route의 `TB_ROUTE_STUB_PATTERN` 샘플 결과가 이전 실행 결과와 달라질 수 있다. 실 DB로
`extract --dry-run`을 재실행해 샘플 수·`FACE`/`DIR_SEQ` 분포가 납득할 만한 수준으로 바뀌는지
확인한 뒤, 필요하면 `--replace`로 기존 데이터를 재생성하는 것을 권장한다(이 테이블은 2.4절대로
현재 프로덕션 소비처가 없으므로 재생성 자체의 하위 호환 위험은 없다).

### 3.4 [이름만 유사, 개념은 분리] Group/Bundle Pattern ↔ Utility Pipe Group Vector

가장 헷갈리기 쉬운 쌍이지만, 코드 레벨에서는 실제로 완전히 독립적이다.

| | Group/Bundle Pattern(`TB_ROUTE_GROUP_PATTERN`) | Utility Pipe Group Vector(`TB_ROUTE_UTILITY_GROUP_VECTOR`) |
|---|---|---|
| 그룹 정의 | **기하학적**: 국소적으로 나란히(pitch≤1500mm, 겹침≥100mm) 달리는 구간 | **속성 기반**: (장비+유틸리티그룹+유틸리티)가 같은 route 전체 집합 |
| 단위 | 평행 Section(부분 구간) — 한 route가 여러 Section에 걸칠 수 있음 | Route 전체 — 한 route는 정확히 하나의 그룹에 속함 |
| 목적 | 다발배관 시각화(랙/뱅크 구조 확인) | Utility Pipe Group 단위 Top-K 검색의 검색 대상 |
| 대표 소비처 | `RubberBandRouting.Viewer`(그룹배관 다이얼로그), `GroupPatternViewer` | `TopKSearchStandalone`(검색), `TopK.3DViewer`("Utility 배관 그룹" 모드) |

- `Docs/UtilityPipeGroup_TopK_Development_Plan.md`는 애초에 두 개념이 다르다는 것을 인지하고,
  Group/Bundle Pattern의 피치/평행성 통계를 Utility Pipe Group Vector의
  `ARRANGEMENT_VECTOR_JSON`에 **선택적으로 재사용**할 계획이라고 명시했다. 그러나 실제
  `BuildUtilityPipeGroupVectors.py` 코드에는 `TB_ROUTE_GROUP_PATTERN`을 읽는 코드가 전혀 없다
  — **계획된 연동이 구현되지 않은 채로 두 파이프라인이 완전히 갈라져 있다.**
- 유일하게 실제로 연결된 지점은 Group/Bundle Pattern → Bend Feature Point 방향
  (`ExtractBendFeaturePoints.py`가 `TB_ROUTE_GROUP_PATTERN`의 `PITCH_MM`/`TRUNK_Z`를 읽어
  `CAUSE=GROUP_ALIGNMENT` 판정에 쓴다)이며, 이건 의도된 재사용이라 중복이 아니다.
- **[후속 확인 완료]** `ARRANGEMENT_VECTOR_JSON`의 실제 계산부(`utility_pipe_group_encoder.py`
  `build_arrangement()`)를 라인 단위로 대조한 결과, **Group/Bundle Pattern의 피치/CV 로직과는
  다른 통계를 계산하고 있어 재계산 중복이 아님을 확인했다.** `PITCH_MM`/`PITCH_CV`는
  `check_parallel_overlap()`으로 축-스냅된 세그먼트 간의 수직 오프셋(피치)을 기준경로 대비
  중앙값·변동계수로 낸 값(국소적으로 나란히 달리는 구간의 "간격 규칙성")인 반면,
  `build_arrangement()`의 `start_pairwise_distance_mm`/`end_pairwise_distance_mm`은 그룹
  멤버 전체 시작점(또는 종점) 좌표 사이의 페어와이즈 유클리드 거리 평균/표준편차, `aabb`는
  전체 지오메트리 포인트의 바운딩박스다 — 둘 다 "그룹이 얼마나 퍼져 있는가"를 다루지만 하나는
  평행 세그먼트의 국소 이격거리, 다른 하나는 전체 연결점 집합의 전역 산포도로 서로 다른 질문에
  답한다. 계획서상 예정했던 "선택적 재사용" 연동은 실제로 구현되지 않았고, 구현했더라도
  단순 재사용이 아니라 별도 통계이므로 **판단: 중복 아님. 추가 조치 불필요.**

### 3.5 [의도적 공존] Bend Feature Point ↔ 30D 특징벡터의 굴곡 관련 차원

- 30D 특징벡터의 `[12:20]`(3등분 방향 — 대략적 굴곡 패턴)과 `[25:29]`(arrow_pattern: 축별
  이동비율 + 정규화된 굴곡수)는 "route가 얼마나/어떻게 꺾이는가"를 **거칠게 요약**한다.
- Bend Feature Point는 개별 꺾임점 단위로 위치·전이유형·원인까지 **정밀하게** 기록한다.
- `UtilityPipeGroupMatcher.cs`는 Bend Feature Point가 있으면 이를 우선 사용하고, 없으면 기존
  30D 벡터의 굵은(coarse) 코사인 유사도로 50:50 폴백하도록 **명시적으로 설계**되어 있다. 즉
  이건 실수로 생긴 중복이 아니라 "정밀 데이터가 없을 때를 위한 조악한 대체재"로 의도된 이중
  표현이다.
- **판단: 중복 아님.** 다만 두 표현을 유지보수하는 비용은 있으므로, Bend Feature Point가
  안정화되어 커버리지가 충분해지면 30D 벡터 쪽 굴곡 관련 차원의 가중치를 낮추거나 폴백 전용으로
  명확히 격하하는 정책 결정을 고려할 수 있다(지금 당장 조치할 사안은 아님).

### 3.6 [약한 의심, 미확인] Stub Pattern의 `N_BENDS`/`DIR_SEQ`/`FACE` ↔ Bend Feature Point

- Stub Pattern도 stub 구간 내의 굴곡수(`N_BENDS`)와 방향 시퀀스(`DIR_SEQ`)를 자체적으로 갖고
  있어, 개념적으로 Bend Feature Point의 stub 구간(`SEGMENT_ZONE=START_STUB/END_STUB`) 꺾임점과
  겹칠 가능성이 있다.
- 이번 조사에서는 두 스크립트 간 실제 코드 참조 관계까지는 확인하지 못했다(Stub Pattern이
  Bend Feature Point보다 먼저 개발되어 서로 참조하지 않을 가능성이 높음). Stub Pattern 자체가
  현재 프로덕션에 연결되어 있지 않으므로(2.4) 우선순위는 낮다.
- **판단: 지금 당장 조치 불필요, 우선순위 낮은 후속 확인 항목.**

---

## 4. 종합 판단 및 권고

### 4.1 진짜 "중복 계산" 여부
7개 파이프라인 각각은 **서로 다른 granularity**(route / stub 2개 / 꺾임점 N개 / 평행구간 /
속성그룹)에서 동작하도록 설계되어 있어, 완전히 동일한 계산의 단순 재발명은 없다. 가장 우려했던
Group/Bundle Pattern ↔ Utility Pipe Group Vector 쌍도 코드 레벨에서는 독립적으로 확인됐다.

### 4.2 실제로 조치가 필요한 순서

이번 검토에서 제기된 4개 항목 중 3개(1·2·3번)를 실제로 조치했다. 남은 것은 4번(문서화만 필요)
뿐이다.

1. **[조치 완료]** (3.3) Stub 경계 계산이 `PathSegmenter.py`와 `ExtractStubPatterns.py` 두
   곳에 따로 유지보수되던 것을 통합 — `ExtractStubPatterns.py`가 이제
   `PathSegmenter.segment_route()`를 직접 재사용한다. 실 DB 재검증(샘플 재추출)은 아직 대기 중.
2. **[조치 완료]** (3.4) `ARRANGEMENT_VECTOR_JSON`(`utility_pipe_group_encoder.build_arrangement`)
   계산부를 Group/Bundle Pattern의 피치/CV 로직과 라인 단위로 대조 — 재계산 중복이 아님을
   확인했다(전체 연결점 집합의 페어와이즈 거리·AABB 통계 vs 평행 세그먼트의 국소 이격거리
   통계로, 서로 다른 질문에 답하는 통계다). 코드 변경 불필요.
3. **[조치 완료 → 2026-07-22 원복]** `TB_ROUTE_STUB_PATTERN`이 당시 생성만 되고 어디서도
   읽히지 않아 `[특징점생성]` 다이얼로그의 "전체 순서대로 실행"에서 제외(`IsOptional: true`)
   했었으나, 이후 `TopKSearchStandalone/StubPatternSearch.cs` + `TopK.3DViewer` "Stub 패턴"
   검색 모드가 이 테이블을 실제로 조회하게 되면서 전제가 깨졌다. `GeneratorDefinition.cs`의
   `IsOptional`/표시 이름/`DependencyNote`를 원래대로 되돌려 다시 필수 생성 목록에 포함시켰다.
   `TB_ROUTE_BEND_FEATURE_PATTERN`(집계 테이블)은 여전히 소비처가 없다(2.5절).
4. **(3.2, 낮음, 문서화만)** 30D 벡터의 등호길이 3분할과 Path Segmentation의 CSF기준 3분할이
   서로 다른 정의로 공존한다는 점을 팀 내에 공유해, "3구간"이라는 말이 나올 때 어느 파이프라인
   기준인지 항상 명시하도록 한다.

### 4.3 잘 설계된 부분(변경 불필요)
- Feature Vector `env_cost` ↔ Context Vector: 중복을 미리 인지하고 아키텍처(ANN vs rerank
  단계 분리)로 해결한 사례 — 모범 사례로 유지.
- Bend Feature Point ↔ Feature Vector 굴곡 차원: 정밀/조악 대체재 관계로 의도된 공존.
- route Top-K와 Utility Pipe Group Top-K가 필요한 테이블을 조합해서 쓰는 방식 — 7개를 따로
  검색하지 않고 상호보완적으로 결합한 구조는 설계 의도대로 잘 동작하고 있다.
