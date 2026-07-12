# TopKGen 개발현황보고서 (최신 갱신본)

- **작성일**: 2026-06-19
- **대상 경로**: `D:\DINNO\DEV\AI-AutoRouting\TopKGen`
- **분석 기준**: 저장소 소스 코드, PostGIS DB 적재 결과, Neo4j 지식그래프 적재 검증, `data/output` 및 `Docs/` 산출물

---

## 1. 종합 현황

TopKGen은 PostgreSQL 기반 라우팅 DB를 활용해 배관/덕트/장비 데이터를 조회하고, 벡터 검색 및 2D/3D 시각화 산출물을 생성하는 PoC 성격의 도구 묶음으로 출발하였습니다. 최근 개발을 통해 핵심 특징점의 **3차원 공간 기하(PostGIS Geometry) 데이터베이스 영속화**, **수평/수직 다발배관(Pipe Bundle) 탐지 파이프라인 고도화**, 그리고 **Neo4j 지식그래프(Knowledge Graph) 이관 파이프라인**을 연동하는 대규모 개편이 완료되었습니다.

### 주요 핵심 연동 모듈 현황

| 구분 | 주요 경로 | 개발 상태 |
| --- | --- | --- |
| **Top-K 경로 유사도 검색 엔진** | `TopKSearchStandalone/` | .NET 8 콘솔 실행 파일로 구현 완료, 빌드 성공 |
| **VectorDB 생성 UI** | `VectorDBGen/` | .NET 9 WPF 앱 구현, 빌드 성공 (파이썬 프로세스 제어 가능) |
| **3D 공간 기하 특징 적재** | `Tools/learn_design_features.py` | 앵커 PoC(PointZ), 초기 Stub 꺾임 궤적(LineStringZ), 척추선, 장애물 최단 거리 레이더선(LineStringZ), 주행 복도(MultiPolygonZ) 등 5대 특징점의 PostGIS geometry 저장 기능 추가 완료 |
| **다발배관(수평/수직) 탐지 모듈** | `Tools/ExtractVerticalGroup.py` | 3축(X, Y, Z) 주행 방향 분류, DBSCAN 및 BFS fallback 2D 투영 군집화, 동일 Pitch 간격, 최소 500mm 이상 길이, 2가닥 이상 배관 조건 충족 및 CSF/AF 공간 매핑 구현 완료 |
| **Neo4j 지식그래프 이관 모듈** | `Tools/export_to_neo4j.py` | PostgreSQL 데이터를 Neo4j 그래프 DB로 벌크 이관, 프로젝트-장비-배관-다발-공간 간 위상 관계망(CONNECTED_TO, MEMBER_OF) 직조 및 Cypher 쿼리 템플릿 검증 완료 |
| **평면/공간 Export 도구** | `Tools/` | Python 기반 DXF/PNG/Shapefile/HTML 생성 모듈 구현, 문법 검증 및 출력 확인 |

---

## 2. 저장소 구성 및 추가 모듈

| 경로 | 설명 |
| --- | --- |
| `TopKSearchStandalone/TopKSearchStandalone.cs` | 단일 파일 기반 Top-K 검색 엔진 및 CLI |
| `VectorDBGen/MainWindow.xaml`, `VectorDBGen/MainWindow.xaml.cs` | VectorDB 생성 WPF UI 및 실행 제어 로직 |
| `Tools/learn_design_features.py` | 배관 3D 정점 기반 특징점 학습 및 PostGIS Geometry 영속화 메인 파이프라인 |
| `Tools/ExtractVerticalGroup.py` | 장비호기별, 유틸리티별, 공간구역별 수평/수직 다발배관 탐지 알고리즘 |
| `Tools/export_to_neo4j.py` | PostgreSQL 데이터를 Neo4j 지식그래프(`DDW_AI` 인스턴스)로 벌크 적재하는 파이프라인 |
| `Tools/ExportGroupPattern.py` (구 `DesignPatternAnalyzer.py`) | 그룹배관(다발) 패턴 추출 및 `TB_ROUTE_GROUP_PATTERN` 적재 모듈 |
| `Tools/ExportCombinedPlan.py` | 장비/덕트/분기 배관 통합 평면 산출물 생성 |
| `Docs/` | 개발 명세서 및 현황 문서 보관 폴더 |
| `data/output/` | DXF, PNG, Shapefile, HTML 등 생성 산출물 |

---

## 3. 핵심 신규 기능 상세

### 3.1 3차원 공간 기하 특징점 PostGIS 영속 적재
라우팅 가이더(C#) 및 GIS 3D 뷰어 프로그램과의 데이터 상호연동을 위해, 분석된 모든 설계 특징점에 PostGIS 3D 공간 기하 데이터(`GEOM_3D`, `STUB_GEOM_3D`) 필드를 설계해 반영했습니다:
* **앵커 및 Stub 궤적 (`TB_ROUTE_FEATURE_ANCHOR`)**: 장비 접속 앵커 PoC(PointZ) 및 초기 Stub 꺾임 라인(LineStringZ) 저장
* **Stub 템플릿 (`TB_ROUTE_FEATURE_STUB_TEMPLATE`)**: 대표 Stub 꺾임 형상(LineStringZ)을 원점(0,0,0) 기준으로 정규화하여 저장
* **다발배관 중심 Spine (`TB_ROUTE_FEATURE_BUNDLE_TEMPLATE`)**: 척추선 궤적(LineStringZ) 저장
* **장애물 최단 거리 레이더선 (`TB_ROUTE_FEATURE_OBSTACLE_RELATION`)**: 배관-장애물 간 최단 거리를 연결하는 선분(LineStringZ)을 저장하여 3D 간섭 여부의 가시화 지원
* **주행 복도 영역 (`TB_ROUTE_GROUP_PATTERN`)**: 복도(Corridor) 구간 AABB 영역의 6면 다각형 패치들을 묶어 3D 다면체(`MultiPolygonZ`)로 빌드 및 저장

### 3.2 수평 및 수직 다발배관(Pipe Bundle) 탐지 파이프라인 구축 (`ExtractVerticalGroup.py`)
Z축 주행에 국한되었던 기존 탐지 로직을 X, Y, Z축의 3차원 공간으로 대폭 확장했습니다:
* **세그먼트-공간 매핑**: `TB_SPACE_INFO` 공간 바운딩 박스를 기준으로 중점(Midpoint) 충돌 판단을 거쳐 CSF, A/F, CR 등 해당 공간을 확정합니다. 특히 CSF 공간은 A/F 하단 고도부터 CSF 내부 전역을 탐색 영역으로 보정했습니다.
* **유틸리티 그룹 결합**: 공급/환수 배관(예: Coolant_S, Coolant_R)이 하나의 쌍으로 묶여 주행하도록 `utility_group` 기준으로 군집화를 묶어 기동합니다.
* **투영 평면 군집화**: X/Y/Z 주행 축별로 2D 평면상에 세그먼트 중점을 투영하여 DBSCAN(또는 BFS fallback) 알고리즘으로 평면 거리 1m 이내의 세그먼트들을 군집화합니다.
* **물리 제약 필터링**: 다발 내에 포함된 배관 경로가 **2개 이상**이고 진행 방향 총 연장 길이가 **500mm 이상**인 경우만 유효한 다발배관으로 인정하여 저장합니다.

### 3.3 Neo4j 지식그래프(Knowledge Graph) 연동 파이프라인 (`export_to_neo4j.py`)
PostgreSQL DB에 저장된 관계형 테이블 데이터들을 토폴로지 중심의 지식그래프로 매핑하여 Neo4j 데이터베이스에 적재하는 벌크 이관 모듈을 구축했습니다.
* **노드(Nodes)**: `Project`, `Space`, `Equipment`, `Route`, `BundleGroup`, `CorridorPattern` 노드 생성
* **관계(Relationships)**: `CONNECTED_TO`(포트 연결), `MEMBER_OF`(다발 소속), `PASSES_THROUGH`(공간 통과), `BELONGS_TO` 관계망 연결
* **Cypher 패턴 쿼리**: 특정 장비에 연결된 배관 다발 통계, 이기종 유틸리티 간의 평행 공유 주행 패턴 조회, Louvain 알고리즘 기반 커뮤니티 감지(Clustering) 등의 쿼리 템플릿 검증 완료

---

## 4. 실증 및 통합 테스트 결과 (WTNHJ02 - CLEAN 메인장비 샘플)

실제 반도체 Fab 배관 데이터인 **`WTNHJ02`** (CLEAN 메인장비) 프로젝트 데이터를 가동하여 전체 파이프라인의 종단 간 검증을 완료했습니다.

### ① 특징점 추출 실행 결과
```powershell
python ./Tools/learn_design_features.py --project "WTNHJ02" --report false
```
* **수행 시간**: 약 40.72초
* **로드 배관 수**: 159개 노선
* **추출 완료 특징**:
  - 앵커 데이터: 318개 PoC 및 진입 방향 추출
  - 3D Pipe Bundle: **총 67개**의 수평/수직 다발배관 탐지 및 저장
  - 장애물 간섭 관계: 30,190건의 레이더선(LineStringZ) 적재 완료
  - 주행 복도 패턴: 8개 그룹 패턴 적재

### ② Neo4j 지식그래프 이관 및 적재 결과
```powershell
python ./Tools/export_to_neo4j.py --project "WTNHJ02" --password "dinno3040"
```
* **Neo4j 적재 정보**:
  - Space 노드: 5개 구역
  - Equipment 노드: `WTNHJ02` 메인장비 노드 생성
  - BundleGroup 노드: 67개 (수평/수직 통합) 및 3D AABB 좌표 매핑
  - Connected_To 관계: 318개 앵커 포트와 배관 간의 연결 엣지 및 포트/방향 속성 매핑 완료
  - Member_Of 관계: 각 다발에 속한 배관 경로(Route)의 그래프 연결 완료

---

## 5. 최종 완료도 및 평가

| 영역 | 이전 완료도 (06-06) | **현재 완료도 (06-19)** | 평가 및 갱신 내역 |
| --- | ---: | ---: | --- |
| **Top-K 검색 엔진** | 80% | **80%** | CLI 및 스키마 진단 유지 |
| **VectorDB 생성 UI** | 65% | **85%** | 수평/수직 다발 탐색 및 Neo4j 이관 연동 체계 완료 |
| **3D 공간 특징점 영속화** | - | **95%** | PostGIS geometry 필드로 5대 특징점 및 다면체 적재 완료 |
| **다발배관 탐지 모듈** | - | **100%** | X/Y/Z 다발 탐지, DBSCAN/BFS, 최소 500mm/2가닥 조건 완료 |
| **Neo4j 지식그래프 연동** | - | **100%** | 이관 스크립트 작성 완료 및 `WTNHJ02` 데이터 적재 실증 완료 |
| **2D 평면 export** | 85% | **85%** | combined_plan 및 세부 도면 유지 |
| **3D 시각화** | 75% | **80%** | Plotly html 3D 및 PostGIS 기하 가시화 준비 |

---

## 6. 다음 개발 권장 단계

1. **Neo4j 지식그래프 기반 C# WPF UI 분석 연동**
   - C# WPF 설계 뷰어에 Neo4j Bolt Client를 연동하여 사용자가 3D 화면 상에서 배관 클릭 시 해당 배관의 위상 그래프를 사이드바 창으로 띄우는 기능 개발.
2. **지식그래프 Louvain 알고리즘 고도화**
   - GDS 라이브러리를 활용해 물리 기하 연산 없이 토폴로지만으로 다발(Bundle Cluster)을 탐지하는 분류 품질을 높여 C# 라우팅 가이더의 가중치로 환류하는 루프 형성.
3. **보안/패키지 버전 갱신**
   - VectorDBGen 빌드의 Npgsql 보안 취약 버전 교체 진행.
