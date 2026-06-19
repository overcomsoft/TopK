# [설계 개발 문서] 피처 영역별 데이터 흐름(Data Flow) 및 핵심 알고리즘 규격서

본 문서는 TopKGen 패키지의 기존 설계 특징점 학습 파이프라인에서 추출하는 **8대 피처 영역**별로 어떤 원천 테이블에서 데이터를 불러오고, 어떤 핵심 알고리즘을 수행하며, 최종적으로 어떤 구조와 포맷으로 저장되는지를 상세하게 정리한 사양서입니다.

---

## 피처 영역별 데이터 흐름 요약표

| 번호 | 피처 영역 | 원천(추출) 테이블 | 핵심 알고리즘 | 적재(저장) 테이블 |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **개별 배관 경로** | `TB_ROUTE_PATH` <br> `TB_ROUTE_SEGMENT` | 기하 스냅 보정 및 정규화 | `TB_ROUTE_FEATURE_PATH` |
| **2** | **접속 앵커 및 초기 Stub** | `TB_ROUTE_PATH` <br> `TB_ROUTE_SEGMENT` | 주행도 분석 및 기하 벡터 추출 | `TB_ROUTE_FEATURE_ANCHOR` |
| **3** | **대표 Stub 템플릿** | `TB_ROUTE_FEATURE_ANCHOR` | 원점 변환 및 기하 정규화 | `TB_ROUTE_FEATURE_STUB_TEMPLATE` |
| **4** | **공용 척추선 (Spine)** | `TB_ROUTE_FEATURE_ANCHOR` | 2D DBSCAN 평면 군집화 및 평균화 | `TB_ROUTE_FEATURE_BUNDLE_TEMPLATE` |
| **5** | **배관-장애물 이격 관계** | `TB_BIM_OBSTACLE` <br> `TB_ROUTE_PATH` | AABB 최단거리 투영 & 레이더 선분 | `TB_ROUTE_FEATURE_OBSTACLE_RELATION` |
| **6** | **다발배관 (Pipe Bundle)** | `TB_ROUTE_PATH` <br> `TB_SPACE_INFO` | 3축 공간 매핑 & 2D 투영 군집화 | `TB_ROUTE_VERTICAL_GROUP_FEATURE` |
| **7** | **주행 복도 그룹 패턴** | `TB_ROUTE_PATH` | 복합 유사도 가중합 & Union-Find | `TB_ROUTE_GROUP_PATTERN` |
| **8** | **유사 설계 30D 벡터** | `TB_ROUTE_PATH` <br> `TB_ROUTE_SEGMENT` | 가중치 맵 기반 스케일링 & L2 정규화 | `TB_ROUTE_FEATURE_VECTOR` |

---

## 1. 개별 배관 경로 (Individual Route Paths)

### ① 추출(불러오는) 데이터 및 테이블
* **대상 테이블**: `TB_ROUTE_PATH` (배관 메타 정보) 및 `TB_ROUTE_SEGMENT` (배관 3D 세그먼트 좌표 정보)
* **추출 필드**:
  - `ROUTE_PATH_GUID`: 배관 경로 고유 식별자
  - `PROCESS_NAME`, `EQUIPMENT_NAME`, `UTILITY_GROUP`, `SOURCE_UTILITY`, `SOURCE_SIZE` (배관 속성 정보)
  - `START_POSX/Y/Z`, `END_POSX/Y/Z` (시작/종점 좌표)
  - `SEGMENT_SEQUENCE`, `START_X/Y/Z`, `END_X/Y/Z` (경로상 모든 단위 세그먼트 좌표)

### ② 핵심 알고리즘 (Core Algorithm)
* **스냅 보정 및 정규화**: 세그먼트 정점들의 미세 오차(노이즈)를 6직교 축(+X, -X, +Y, -Y, +Z, -Z)으로 스냅 보정하여 배관 형상 모델을 정교하게 재조립합니다.
* **RDP(Ramer-Douglas-Peucker) 단순화**: 기하학적으로 일직선 상에 놓인 다중 점들을 하나의 긴 선분으로 병합 압축하여 데이터 용량을 최적화합니다.

### ③ 저장(적재) 데이터 및 테이블
* **대상 테이블**: `TB_ROUTE_FEATURE_PATH`
* **저장 필드**:
  - `ROUTE_PATH_GUID` (text, PK)
  - `PROJECT_ID`, `PROCESS_NAME`, `EQUIPMENT_NAME`, `UTILITY_GROUP`, `UTILITY`, `SIZE`
  - `GEOM_3D` (geometry(LineStringZ, 0)): 정규화 및 단순화가 완료된 배관 3D 정점 실선 데이터

---

## 2. 접속 앵커 및 초기 Stub (Anchors & Initial Stubs)

### ① 추출(불러오는) 데이터 및 테이블
* **대상 테이블**: `TB_ROUTE_FEATURE_PATH` (보정 완료된 개별 배관 데이터)
* **추출 필드**:
  - `ROUTE_PATH_GUID`, `PROJECT_ID`, `EQUIPMENT_NAME`, `UTILITY_GROUP`, `UTILITY`
  - `GEOM_3D` 내의 시작 정점($p_0$), 인접 정점($p_1$), 종료 정점($p_n$), 직전 정점($p_{n-1}$) 좌표

### ② 핵심 알고리즘 (Core Algorithm)
* **앵커 분류**: 배관 시점($p_0$)은 `'EQUIP'`(장비 연결점)으로, 종점($p_n$)은 `'TARGET'`(목적지 연결점)으로 분류합니다.
* **진입 면(Face) 판별**: 시작 세그먼트 벡터($p_1 - p_0$) 및 종료 세그먼트 벡터($p_{n-1} - p_n$)의 직교 방향 벡터 성분을 추출하여 진출입 방향 면(Face - e.g. `+z`, `-x` 등)을 판별합니다.
* **초기 Stub 꺾임 추출**: 배관의 시작/종점 부분에서 수평으로 꺾어 나가기 직전까지의 수직 상승 고도(`RISE_MM`) 및 첫 엘보(Elbow) 정점까지의 다단식 Stub 폴리라인 경로를 분할 추출합니다.

### ③ 저장(적재) 데이터 및 테이블
* **대상 테이블**: `TB_ROUTE_FEATURE_ANCHOR`
* **저장 필드**:
  - `ROUTE_PATH_GUID`, `PROJECT_ID`, `ANCHOR_KIND` (text, 복합 PK/Unique)
  - `ANCHOR_NAME` (장비명), `UTILITY_GROUP`, `UTILITY`, `FACE`, `RISE_MM`, `CONFIDENCE`
  - `ANCHOR_POINT_JSON` (PoC 3D 좌표), `FIRST_ELBOW_POINT_JSON` (첫 코너 3D 좌표), `STUB_POINTS_JSON` (Stub 정점 배열)
  - `GEOM_3D` (geometry(PointZ, 0)): 접속 앵커의 3D 포인트 위치 기하
  - `STUB_GEOM_3D` (geometry(LineStringZ, 0)): 초기 Stub 배관의 3D 실선 기하

---

## 3. 대표 Stub 템플릿 (Stub Templates)

### ① 추출(불러오는) 데이터 및 테이블
* **대상 테이블**: `TB_ROUTE_FEATURE_ANCHOR` (앵커 및 Stub 정점 데이터)
* **추출 필드**: `PROJECT_ID`, `ANCHOR_KIND`, `ANCHOR_NAME` (장비명), `UTILITY_GROUP`, `STUB_POINTS_JSON` (정점 배열)

### ② 핵심 알고리즘 (Core Algorithm)
* **원점 기준 정규화**: 물리 좌표계 상에 존재하는 개별 Stub 좌표들을 로컬 좌표계인 원점 `(0, 0, 0)`으로 평행 이동시키고 회전 정렬하여 상대적 형상 특징만 남깁니다.
* **템플릿 클러스터링**: 동일 장비, 동일 유틸리티에 연결된 모든 정규화 Stub 형상을 비교하여, 소수 형태의 고유 Stub 꺾임 대표 형상(Template)을 도출하고 고유 해시 ID를 부여합니다.

### ③ 저장(적재) 데이터 및 테이블
* **대상 테이블**: `TB_ROUTE_FEATURE_STUB_TEMPLATE`
* **저장 필드**:
  - `TEMPLATE_ID` (text, PK)
  - `PROJECT_ID`, `STUB_KIND` (형상 구조 코드), `ANCHOR_KIND`, `MAIN_EQUIPMENT_NAME`, `UTILITY_GROUP`
  - `POINTS_JSON` (원점 정렬된 3D 정점 좌표 리스트)
  - `GEOM_3D` (geometry(LineStringZ, 0)): 원점에 정렬된 템플릿의 3D 실선 궤적 기하

---

## 4. 공용 척추선 (Spine / Bundle Templates)

### ① 추출(불러오는) 데이터 및 테이블
* **대상 테이블**: `TB_ROUTE_FEATURE_ANCHOR` (앵커 및 Stub 정점 데이터)
* **추출 필드**: `PROJECT_ID`, `UTILITY_GROUP`, `STUB_POINTS_JSON` (초기 꺾임 정점 리스트)

### ② 핵심 알고리즘 (Core Algorithm)
* **DBSCAN 기반 평면 군집화**: 동일 프로젝트, 동일 유틸리티 그룹 내에서 Stub의 첫 엘보(꺾임) 좌표들을 XY 평면에 투영하고, 거리 1m 이내로 모인 포인트 군집(다발)을 식별합니다.
* **평균 Spine 산출**: 군집 내의 좌표들을 평균 내어, 여러 배관이 모여서 주행을 시작하는 대표 가상의 척추선(Spine Line) 중심 궤적을 연산합니다.

### ③ 저장(적재) 데이터 및 테이블
* **대상 테이블**: `TB_ROUTE_FEATURE_BUNDLE_TEMPLATE`
* **저장 필드**:
  - `BUNDLE_ID` (text, PK)
  - `PROJECT_ID`, `UTILITY_GROUP`, `PITCH_MM`, `HEIGHT_LEVEL_MM` (척추선이 지나가는 높이 고도)
  - `SPINE_POINTS_JSON` (척추선 3D 좌표 리스트)
  - `GEOM_3D` (geometry(LineStringZ, 0)): 척추선 3D 실선 기하

---

## 5. 배관-장애물 이격 관계 (Obstacle Relations)

### ① 추출(불러오는) 데이터 및 테이블
* **대상 테이블**: `TB_BIM_OBSTACLE` (BIM 구조/간섭 장애물 테이블) 및 `TB_ROUTE_FEATURE_PATH` (배관 궤적 데이터)
* **추출 필드**:
  - `INSTANCE_NAME`, `OST_TYPE`, `DDWORKS_TYPE` (장애물 메타)
  - `AABB_MINX/Y/Z`, `AABB_MAXX/Y/Z` (장애물 Bounding Box 경계 정점)
  - `GEOM_3D` (배관 정점 선분 데이터)

### ② 핵심 알고리즘 (Core Algorithm)
* **AABB 최단거리 투영 (Closest Point on AABB)**: 배관 3D 선분 상의 정점들과 장애물 Bounding Box(6개 면) 간의 최단 거리를 구합니다.
* **이격 마진 분석**: 배관 지름을 감안하여 실제 여유 이격 거리(`CLEARANCE_MARGIN_MM`)가 설계 표준 필수 요구치(`REQUIRED_CLEARANCE_MM`)를 준수하는지 감지합니다.
* **레이더 선 생성**: 배관 상의 최단 거리점과 장애물 표면 상의 최단 거리점을 연결하는 3D 벡터 선분을 도출합니다.

### ③ 저장(적재) 데이터 및 테이블
* **대상 테이블**: `TB_ROUTE_FEATURE_OBSTACLE_RELATION`
* **저장 필드**:
  - `ROUTE_PATH_GUID`, `OBSTACLE_NAME` (text, 복합 PK/Unique)
  - `PROJECT_ID`, `OBSTACLE_TYPE`, `OBSTACLE_AXIS`, `UTILITY_GROUP`, `UTILITY`, `DIAMETER_MM`, `NEAREST_DISTANCE_MM`, `REQUIRED_CLEARANCE_MM`, `CLEARANCE_MARGIN_MM`
  - `GEOM_3D` (geometry(LineStringZ, 0)): 배관 최단점과 장애물 최단점을 잇는 최단거리 레이더 가시화 선분 기하

---

## 6. 다발배관 (Pipe Bundle)

### ① 추출(불러오는) 데이터 및 테이블
* **대상 테이블**: `TB_ROUTE_PATH` (배관 속성), `TB_ROUTE_SEGMENT` (세그먼트 3D), `TB_SPACE_INFO` (공간 경계 영역 정보)
* **추출 필드**:
  - `ROUTE_PATH_GUID`, `PROJECT_ID`, `EQUIPMENT_NAME`, `UTILITY_GROUP` (배관 속성)
  - `START_X/Y/Z`, `END_X/Y/Z` (단위 세그먼트 좌표)
  - `SPACE_NAME`, `AABB_MINX/Y/Z`, `AABB_MAXX/Y/Z` (공간 AABB)

### ② 핵심 알고리즘 (Core Algorithm)
* **세그먼트 중점 기반 공간 매핑**: 각 세그먼트 중점이 공간 AABB 내에 속하는지 체크하여 공간명을 매핑합니다. (CSF의 경우 A/F 하단 고도부터 CSF 내부 전역을 대상으로 함).
* **주행 축 및 진행 방향 판별**: 세그먼트 진행 벡터에서 가장 절대 변화량이 큰 주행 축(X, Y, Z)과 부호 방향(`+Z`, `-Z`, `+X` 등)을 판별합니다.
* **2D 투영 군집화 (DBSCAN / BFS Fallback)**: 동일 `(장비, 유틸, 공간, 주행축)` 그룹에 속한 세그먼트 중점들을 주행축 제외 2D 단면으로 투영해 DBSCAN(Epsilon 1.0m 이내)으로 군집화합니다.
* **물리 조건 제약 필터링**: 군집된 그룹 중 멤버 경로 개수가 **2개 이상**이고 진행 방향 연장 길이가 **500mm 이상**인 경우만 다발로 채택하여 대표 진행 방향, 평균 Pitch를 도출합니다.

### ③ 저장(적재) 데이터 및 테이블
* **대상 테이블**: `TB_ROUTE_VERTICAL_GROUP_FEATURE` (수평/수직 다발배관 통합 테이블)
* **저장 필드**:
  - `VERTICAL_GROUP_ID` (text), `PROJECT_ID`, `EQUIPMENT_NAME`, `UTILITY`, `SPACE_NAME` (복합 PK/Unique)
  - `DIRECTION` (진행 방향), `BUNDLE_LENGTH` (mm), `AVG_PITCH_MM` (mm), `ROUTE_COUNT` (가닥 수)
  - `AABB_MINX/Y/Z`, `AABB_MAXX/Y/Z` (다발 전체 AABB 범위)
  - `MEMBER_ROUTE_GUIDS_JSON` (소속 GUID 리스트)
  - `GEOM_3D` (geometry(MultiLineStringZ, 0)): 다발에 속한 배관 세그먼트들의 3D 실선 궤적 기하

---

## 7. 주행 복도 그룹 패턴 (Corridor Patterns)

### ① 추출(불러오는) 데이터 및 테이블
* **대상 테이블**: `TB_ROUTE_PATH` (배관 정점 데이터)
* **추출 필드**: `ROUTE_PATH_GUID`, `PROJECT_ID`, `EQUIPMENT_NAME`, `UTILITY_GROUP`, `points` (배관 3D 폴리라인 점들)

### ② 핵심 알고리즘 (Core Algorithm)
* **RLE 압축 방향성 형태 비교**: 각 배관을 $100\text{mm}$ 간격으로 보간한 뒤, 코너 주행 방향을 토큰으로 부호화(Arrow Coding - R/H/D)하고 Levenshtein 문장 편집거리를 사용해 형태 유사도를 구합니다.
* **공동 피치 CV(변동계수) 검증**: 공동 주축 단면에 정점들을 투영하여 배관 간격의 일관성 변동률(`CV <= 0.30`)을 확인합니다.
* **Union-Find 군집화**: 복합 유사도 `sim >= 0.70`이고 꺾임 횟수 `n_bends >= 2`인 경로들을 Union-Find 자료구조를 통해 공동 주행 복도 패턴 그룹으로 묶습니다.

### ③ 저장(적재) 데이터 및 테이블
* **대상 테이블**: `TB_ROUTE_GROUP_PATTERN`
* **저장 필드**:
  - `GROUP_ID` (text, PK)
  - `PROJECT_ID`, `TAG_GROUP_NM` (장비명), `UTILITY` (유틸리티 그룹), `N_MEMBERS`, `AVG_SIMILARITY`, `TRUNK_Z`, `TRUNK_XY_SPREAD` (다발 폭), `PITCH_MM`, `PATTERN_SEQ`, `MEMBER_GUIDS` (JSON), `SECTION_BOUNDS` (구간별 AABB 배열)
  - `GEOM_3D` (geometry(MultiPolygonZ, 0)): 주행 복도(Corridor) 구간 AABB 박스 영역들을 3D 입체 다면체 껍데기로 변환한 기하 데이터

---

## 8. 유사 설계 30D 벡터 (30D Similarity Vectors)

### ① 추출(불러오는) 데이터 및 테이블
* **대상 테이블**: `TB_ROUTE_FEATURE_PATH` (보정 완료된 개별 배관 기하 데이터)
* **추출 필드**: `ROUTE_PATH_GUID`, `PROJECT_ID`, `PROCESS_NAME`, `EQUIPMENT_NAME`, `UTILITY_GROUP`, `UTILITY`, `SIZE`, `GEOM_3D` (3D 선분 리스트)

### ② 핵심 알고리즘 (Core Algorithm)
* **기하 요소 인코딩 (30차원)**: 시작/종점 3D 방향(6차원), 공간 총 변위(3차원), Bounding Box 크기 비율(3차원), 3분할 등간격 리샘플링 방향(9차원), 총 길이 비율(1차원), 환경비용/패턴 영역(8차원)을 벡터 성분에 각각 수식 계산하여 채웁니다.
* **가중치 동적 스케일링**: [30D 특징 벡터 규격서](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/Docs/vector_db_generation_and_30d_feature_vector_spec.md)의 가중치 맵(`WEIGHT_MAP`)을 활용해 각 피처 영역의 중요도 가중합에 비례하는 Scale Factor를 원본 성분에 곱해 줍니다.
* **L2 정규화**: 스케일이 반영된 30D 벡터의 크기를 1로 조절하여 pgvector 코사인 거리 공간에 정렬시킵니다.

### ③ 저장(적재) 데이터 및 테이블
* **대상 테이블**: `TB_ROUTE_FEATURE_VECTOR`
* **저장 필드**:
  - `ROUTE_PATH_GUID` (text, PK)
  - `PROCESS_NAME`, `EQUIPMENT_NAME`, `UTILITY_GROUP`, `UTILITY`, `SIZE`, `DIRECTION_PATTERN`, `TOTAL_LENGTH_MM`, `STEP_COUNT`
  - `START_POSX/Y/Z`, `END_POSX/Y/Z` (시작/종점 3D 좌표)
  - `FEATURE_VECTOR` (vector(30)): pgvector 전용 30차원 밀집 특징 벡터 데이터
