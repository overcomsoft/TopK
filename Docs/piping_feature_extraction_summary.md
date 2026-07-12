# 배관 특징점 추출 파이프라인 컴포넌트 및 기능 요약

이 문서는 기존 설계 데이터(DDW_AI_DB)를 분석하여 자동 경로 탐색 알고리즘에 활용할 Z축 Rack 레벨 고도, PoC 접속면 방향, 공용 척추선(Spine), 장애물 이격 거리, 수직 다발배관 및 주행 복도(Corridor) 영역 등의 설계 특징점을 추출하는 4가지 핵심 컴포넌트의 기능과 데이터베이스 영속 스펙을 설명합니다.

---

## 1. 컴포넌트별 기능 및 DB 스펙 요약

| 코드 파일명 | 주요 추출 특징점 및 기능 | 영속 저장 대상 테이블 | 적재되는 3D 공간 데이터 (`GEOM_3D` / `STUB_GEOM_3D`) |
| :--- | :--- | :--- | :--- |
| **`learn_design_features.py`** | **기존 설계 특징점 학습 통합 파이프라인**<br>• 개별 배관 복원 및 정규화 (스냅 보정)<br>• 시점/종점 PoC 접속 방향 Voting 분산 분석<br>• 선호 Z축 고도층 (Rack Levels) 통계 추출<br>• scikit-learn DBSCAN 기반 공용 척추선(Spine) 도출<br>• 배관 세그먼트-장애물 간 정밀 최단 거리 분석<br>• Top-K 유사 설계 검색용 30차원 코사인 특징 벡터 빌드 | • `TB_ROUTE_FEATURE_PATH` (개별 경로)<br>• `TB_ROUTE_FEATURE_ANCHOR` (접속 앵커)<br>• `TB_ROUTE_FEATURE_BUNDLE_TEMPLATE` (번들)<br>• `TB_ROUTE_FEATURE_OBSTACLE_RELATION` (장애물)<br>• `TB_ROUTE_FEATURE_VECTOR` (30D 벡터)<br>• `TB_ROUTE_FEATURE_GROUP_PROFILE` (그룹 프로필) | • `LineStringZ` (배관 궤적 및 척추선)<br>• `PointZ` (접속 PoC 좌표)<br>• `LineStringZ` (앵커 진입 Stub)<br>• `LineStringZ` (배관-장애물 최단 레이더선) |
| **`ExtractStubPatterns.py`** | **장비/덕트 주변 초기 진입/탈출 Stub 형상 학습**<br>• 시점/종점 PoC 기준 꺾임(Elbow) 전까지의 대표 형상 군집화<br>• 장비명, 유틸리티, 파이프 구경별 표준 Stub 템플릿(Rise, Offset, 평균 길이) 선출 및 특징 모델 생성 | • `TB_ROUTE_STUB_TEMPLATE`<br>• `TB_ROUTE_FEATURE_STUB_TEMPLATE` (미러링) | • `LineStringZ` (대표 Stub 꺾임 궤적) |
| **`ExtractVerticalGroup.py`** | **수직 다발 배관 (입상/입하 그룹) 분석**<br>• `TB_SPACE_INFO` 영역 정보를 참조하여 격자보 하단 등 CSF 상단 높이 기반의 수직 다발배관 그룹 탐지<br>• 동일 영역 수직 이동 배관 수, 배치 간격, AABB 영역 산출<br>• 다발배관이 수평으로 꺾여 나가는 전이 정보 추정 | • `TB_ROUTE_VERTICAL_GROUP_FEATURE` | • `MultiLineStringZ` (수직 다발 라인 형상) |
| **`ExportGroupPattern.py`** (구 `DesignPatternAnalyzer.py`) | **그룹배관(다발) 패턴 세그먼트 스캔 및 등간격/수평·수직 분류**<br>• 장비+유틸리티그룹 파티션 내에서 세그먼트 단위 평행 스캔으로 다발배관 구간 탐지<br>• 각 구간의 Box AABB 경계 구역(`SECTION_BOUNDS`) 산출<br>• 대표 진행축 시퀀스(`PATTERN_SEQ`, 예: `"XYZ"`) 및 인접 배관 간격의 등간격(CV) 여부, 수평/수직 오프셋축 분류 | • `TB_ROUTE_GROUP_PATTERN` | • `MultiLineStringZ` (다발 멤버 배관선 및 대표 중심선) |

---

## 2. 각 컴포넌트별 핵심 알고리즘 상세

### ① 배관 형상 복원 및 정규화 (`learn_design_features.py` -> `load_data()`)
- CAD 또는 BIM 등에서 기하 추출 시 발생하는 소수점 이하의 미세 불연속 구간(10mm 이하)을 이전 세그먼트 끝점에 강제로 스냅시켜, 단일의 연속된 3D 폴리라인(`LineStringZ`)으로 복원합니다.
- 장비 원점(Source Position)과의 유클리드 거리를 산출하여, 역방향으로 추출된 배관 데이터는 자동으로 정방향이 되도록 세그먼트 인덱스를 뒤집어(`reverse()`) 정렬의 일관성을 확보합니다.

### ② Z축 선호 고도 검출 (Z-Rack Levels) (`learn_design_features.py` -> `detect_rack_levels()`)
- $\Delta Z < 5\text{mm}$ 인 수평 세그먼트를 추출하고 주행 거리를 가중치로 부여한 Z축 가중 히스토그램을 생성합니다.
- 히스토그램의 Peak를 검출하되, 300mm 이내의 너무 근접한 고도층은 병합 처리하여 유의미한 주요 Rack 고도를 도출합니다.

### ③ 대표 접속면 선출 (Dominant Face Voting) (`learn_design_features.py` -> `analyze_poc_faces()`)
- 장비와 파이프라인의 접합부(PoC) 세그먼트의 방향 벡터 주축 성분을 비교 분석합니다.
- 6대 평면 축($+x, -x, +y, -y, +z, -z$) 중 다수결 투표를 통해 신뢰도(Confidence)와 함께 선호 출발/종단면을 선출합니다.

### ④ 공용 척추선 추출 (Trunk Spine) (`learn_design_features.py` -> `extract_trunk_spine()`)
- 배관 경로들을 200mm 간격으로 조밀 분할해 점들을 샘플링한 후, DBSCAN 밀도 기반 군집화 알고리즘을 수행하여 공간상의 번들을 구분합니다.
- Ramer-Douglas-Peucker(RDP) 단순화 알고리즘(임계값 $\epsilon=150\text{mm}$)을 구동해 직관 상의 불필요한 점을 깎아내고 주요 꺾임 지점(Waypoints)만 추출해 대표 번들 경로를 구축합니다.

### ⑤ 장애물 최단 거리 분석 (Analytic AABB-Segment Distance) (`learn_design_features.py` -> `save_obstacle_relations()`)
- 배관의 세그먼트 선분과 주위 장애물 AABB 경계상자 간의 최단 거리를 해석 기하 알고리즘을 사용해 구합니다.
- 가장 가깝게 만나는 배관 상의 점(`near_pt`)과 장애물 상의 최단 점(`aabb_near_pt`)을 계산하여 이 둘을 잇는 이격 레이더 선분(`LineStringZ`)을 DB에 저장합니다.

### ⑥ 수직 다발배관 추출 (`ExtractVerticalGroup.py`)
- 건물 슬래브 또는 격자보 하단 높이 등의 공간 영역 스키마(`TB_SPACE_INFO`, `TB_SPACE_GROUP_INFO`)를 연계하여, 해당 영역을 수직으로 지나가는 다발 관로를 탐색합니다.
- 이를 통해 수직 이동 통로 상의 배관 개수, 배열 간격, 바운딩 박스 기하정보를 도출합니다.

### ⑦ 그룹배관 대표 중심선 산출 (`ExportGroupPattern.py` -> `generate_trunk_centerline_wkt()`)
- 탐지된 그룹배관 다발 영역의 3D 공간 정보를 표현하기 위해, 각 구간 Bounding Box의 Min/Max 좌표를 기반으로 진행축을 관통하는 대표 중심선을 계산하여 PostGIS가 해석할 수 있는 `MULTILINESTRING Z` WKT 포맷으로 변환해 `TRUNK_GEOM_3D` 컬럼에 적재합니다.
  (※ 이전 버전 문서는 `MULTIPOLYGON Z` 다면체 생성 함수로 기술되어 있었으나, 실제 코드는 다면체가 아닌 중심선(`LineStringZ`)을 생성합니다 — 문서 오기 정정.)
