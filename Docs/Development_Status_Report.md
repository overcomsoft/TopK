# TopKGen 개발현황보고서

- 작성일: 2026-06-06
- 대상 경로: `D:\DINNO\DEV\AI-AutoRouting\TopKGen`
- 분석 기준: 저장소 소스, 기존 문서, 빌드 결과, Python 문법 검증, `data/output` 산출물

## 1. 종합 현황

TopKGen은 PostgreSQL 기반 라우팅 DB를 활용해 배관/덕트/장비 데이터를 조회하고, 벡터 검색 및 2D/3D 시각화 산출물을 생성하는 PoC 성격의 도구 묶음으로 개발되어 있다. 현재 구현은 크게 다음 네 축으로 구성된다.

| 구분 | 주요 경로 | 현황 |
| --- | --- | --- |
| Top-K 경로 유사도 검색 엔진 | `TopKSearchStandalone/` | .NET 8 콘솔 실행 파일로 구현 완료, 빌드 성공 |
| VectorDB 생성 UI | `VectorDBGen/` | .NET 9 WPF 앱 구현, 빌드 성공, 단 외부 빌더/DDL 파일 의존성 누락 가능성 있음 |
| 평면/공간 Export 도구 | `Tools/` | Python 기반 DXF/PNG/Shapefile/HTML 생성 모듈 구현, 문법 검증 성공 |
| 스키마/DB 확인 보조 도구 | `SchemaCheckApp/`, 루트 테스트 스크립트 | 간단한 DB 스키마 조회 및 실험 스크립트 존재, 자동화 테스트 체계는 미정비 |

현재 코드는 실제 DB와 로컬 경로에 강하게 결합된 PoC 단계로 보인다. C# 프로젝트는 빌드 가능하고, Python 도구는 문법상 정상이며, `data/output`에는 최신 산출물이 생성되어 있다. 다만 운영화 관점에서는 설정 외부화, 누락 파일 정리, 보안 취약 패키지 업데이트, 자동 테스트 보강이 필요하다.

## 2. 저장소 구성

| 경로 | 설명 |
| --- | --- |
| `TopKSearchStandalone/TopKSearchStandalone.cs` | 단일 파일 기반 Top-K 검색 엔진 및 CLI |
| `VectorDBGen/MainWindow.xaml`, `VectorDBGen/MainWindow.xaml.cs` | VectorDB 생성 WPF UI 및 실행 제어 로직 |
| `VectorDBGen/Models/DbConfig.cs` | DB 연결 설정 모델 |
| `Tools/ExportEquipmentPlan.py` | 장비 OBB/PoC 기반 DXF, PNG, Shapefile, 상세 이미지 생성 |
| `Tools/ExportDuctPlan.py` | 덕트 OBB/PoC 기반 DXF, PNG, Shapefile 생성 |
| `Tools/ExportLateralPlan.py` | 분기 배관 OBB/PoC 기반 DXF, PNG, Shapefile 생성 |
| `Tools/ExportCombinedPlan.py` | 장비/덕트/분기 배관 통합 평면 산출물 생성 |
| `Tools/ViewPlan3D.py`, `Tools/ViewEquipmentOBB.py`, `Tools/VisualizeMANRoute.py` | Plotly HTML 기반 3D 시각화 |
| `SchemaCheckApp/Program.cs` | `TB_ROUTE_NODES` 컬럼 조회용 콘솔 앱 |
| `Docs/` | 기존 분석/개발 문서 및 본 보고서 |
| `data/output/` | DXF, PNG, Shapefile, HTML 등 생성 산출물 |

## 3. 코드 규모

생성물(`bin/`, `obj/`, `__pycache__`)을 제외한 주요 소스 기준 규모는 다음과 같다.

| 파일 | 라인 수 |
| --- | ---: |
| `TopKSearchStandalone/TopKSearchStandalone.cs` | 1,723 |
| `VectorDBGen/MainWindow.xaml.cs` | 1,572 |
| `VectorDBGen/MainWindow.xaml` | 291 |
| `Tools/ExportCombinedPlan.py` | 685 |
| `Tools/ExportEquipmentPlan.py` | 603 |
| `Tools/ExportDuctPlan.py` | 508 |
| `Tools/ViewPlan3D.py` | 506 |
| `Tools/ExportLateralPlan.py` | 494 |
| `Tools/ViewEquipmentOBB.py` | 360 |
| `Tools/VisualizeMANRoute.py` | 146 |

전체적으로 핵심 기능은 소수의 큰 파일에 집중되어 있다. PoC에는 유리하지만 유지보수 단계에서는 공통 유틸리티 분리와 설정 모듈화가 필요하다.

## 4. 모듈별 개발 현황

### 4.1 TopKSearchStandalone

구현 상태: 빌드 가능, CLI/라이브러리 겸용 구조.

주요 기능:

- PostgreSQL/pgvector 기반 `TB_ROUTE_FEATURE_VECTOR` 후보 검색
- 시작/종료 좌표 기반 30D query vector 생성
- 공정, 장비, 유틸리티 그룹, 유틸리티, 사이즈 조건 필터링
- pgvector cosine distance 기반 1차 후보 수집
- 위치 점수, 패턴 점수, 벡터 점수를 조합한 hybrid rerank
- `--json` 결과 출력
- `--list-presets`, `--preset-guid`, `--preset-rank` 기반 `TB_ROUTE_PATH` preset 검색
- `--check-schema` 기반 pgvector/테이블/컬럼/인덱스/row count/벡터 차원 진단

주요 공개/핵심 함수:

- `SearchAsync(...)`
- `CheckSchemaAsync(DbConfig db)`
- `FetchPresetsAsync(...)`
- `FetchPresetByGuidAsync(...)`
- `BuildQueryVector30D(...)`
- `FetchCandidatesAsync(...)`
- `RerankHybrid(...)`

검증 결과:

- `dotnet build TopKSearchStandalone\TopKSearchStandalone.csproj`: 성공
- 경고 0개, 오류 0개

개발 판단:

- 검색 엔진 자체는 독립 실행 가능한 수준까지 구현되어 있다.
- DB 상태에 따라 실제 검색 품질은 `TB_ROUTE_FEATURE_VECTOR` 적재 품질, HNSW 인덱스, 필터 조건의 데이터 정규화에 좌우된다.
- 기본 DB명이 `AUTOROUTINGV7`로 되어 있어 다른 도구의 `DDW_AI_DB` 기본값과 일치하지 않는다. 실제 운영 DB명을 하나로 정리할 필요가 있다.

### 4.2 VectorDBGen

구현 상태: WPF UI 및 실행 제어 로직 구현, 빌드 가능.

주요 기능:

- PostgreSQL 연결 정보 입력 및 접속 테스트
- 선택 빌더별 스키마 초기화, 테이블 재생성, 전체 테이블 생성
- DDL 실행 전후 테이블 row count snapshot
- DDL 실행 결과 Excel report 저장
- Feature, Context, Design Group, Segment Template 빌더 선택 UI
- 1~4단계 일괄 실행
- Python 프로세스 실행, stdout/stderr 로그 스트리밍, 취소 기능

빌더 구성:

| Tag | Target table | 예상 Python script |
| --- | --- | --- |
| `feature` | `TB_ROUTE_FEATURE_VECTOR` | `BuildFeatureVectors.py` |
| `context` | `TB_ROUTE_CONTEXT_VECTOR` | `BuildContextVectors.py` |
| `group` | `TB_ROUTE_DESIGN_GROUP` | `BuildDesignGroups.py` |
| `segment` | `TB_ROUTE_SEGMENT_TEMPLATE` | `BuildSegmentTemplates.py` |

예상 DDL 파일:

- `create_feature_vector_table.sql`
- `create_context_vector_table.sql`
- `create_auto_design_tables.sql`

검증 결과:

- `dotnet build VectorDBGen\VectorDBGen.csproj`: 성공
- 오류 0개
- 경고 2개: `Npgsql` 7.0.6 패키지에 높은 심각도 취약성 경고 `NU1903`

확인된 리스크:

- 현재 저장소 파일 목록에서 `BuildFeatureVectors.py`, `BuildContextVectors.py`, `BuildDesignGroups.py`, `BuildSegmentTemplates.py`가 확인되지 않는다.
- 현재 저장소 파일 목록에서 위 DDL SQL 파일들도 확인되지 않는다.
- 따라서 UI 자체는 빌드되지만, 실제 VectorDB 생성 버튼 실행 시 외부 의존 파일 누락으로 실패할 가능성이 높다.

개발 판단:

- VectorDBGen은 “운영자가 실행하는 제어판” 형태로 상당히 구체화되어 있다.
- 실제 배포 가능 상태로 만들려면 빌더/DDL 파일을 저장소에 포함하거나, 설치 경로/스크립트 경로를 설정 가능한 방식으로 명확히 해야 한다.

### 4.3 Python Export/Visualization 도구

구현 상태: 장비, 덕트, 분기 배관, 통합 평면 및 3D HTML 시각화 도구 구현.

공통 처리:

- PostgreSQL `DDW_AI_DB` 접속
- OBB 3D 꼭짓점에서 바닥 footprint 추출
- PoC 좌표/ID/사이즈 JSON 파싱
- 유틸리티별 색상 매핑
- 사이즈 문자열을 radius로 변환
- DXF, PNG, Shapefile, HTML export
- Matplotlib headless 실행을 위한 `Agg` backend 적용
- Python 3.14 환경에서 발생한 Matplotlib Path deepcopy recursion 문제 대응 패치 포함

모듈별 산출:

| 파일 | 산출물 |
| --- | --- |
| `ExportEquipmentPlan.py` | `equipments_plan.dxf`, `equipments_plan.png`, `equipments.*`, `pocs.*`, 장비 상세 PNG |
| `ExportDuctPlan.py` | `ducts_plan.dxf`, `ducts_plan.png`, `ducts.*`, `duct_pocs.*` |
| `ExportLateralPlan.py` | `laterals_plan.dxf`, `laterals_plan.png`, `laterals.*`, `lateral_pocs.*` |
| `ExportCombinedPlan.py` | `combined_plan.dxf`, `combined_plan.png`, `combined_elements.*`, `combined_pocs.*` |
| `ViewPlan3D.py` | `view_3d_plan.html` |
| `ViewEquipmentOBB.py` | `view_equipment_obb.html` |
| `VisualizeMANRoute.py` | 특정 장비 키워드 기반 route path 3D HTML |

검증 결과:

- `python -m py_compile Tools\...`: 성공
- 실제 DB 실행 검증은 수행하지 않음

개발 판단:

- 평면/3D 산출물 생성 기능은 현재 가장 실증 결과가 잘 남아 있는 영역이다.
- `data/output`에 2026-06-06 기준 통합 DXF/PNG/Shapefile 산출물이 생성되어 있어, DB 연동 및 export 경로가 최근까지 동작한 것으로 판단된다.
- 다만 DB 접속 문자열과 출력 경로가 스크립트 내부에 하드코딩되어 있어 환경 이전성은 낮다.

### 4.4 SchemaCheckApp 및 보조 스크립트

구현 상태: 간단한 DB 조회/실험 스크립트 중심.

구성:

- `SchemaCheckApp/Program.cs`: `DDW_AI_DB`의 `TB_ROUTE_NODES` 컬럼 조회
- `check_db.py`, `list_tables.py`, `list_cols.py`: DB 확인 보조 스크립트
- `test_query*.py`, `test_lateral.py`, `test_route_data.py`: 수동 테스트 또는 데이터 확인용 스크립트
- `SchemaCheckApp/generate_docx.py`, `generate_py_docx.py`: 문서 생성 보조 스크립트

검증 결과:

- `dotnet build SchemaCheckApp\SchemaCheckApp.csproj`: 성공
- 경고 0개, 오류 0개

개발 판단:

- 정식 자동 테스트보다는 개발 중 DB 탐색/검증용 도구에 가깝다.
- 향후에는 smoke test, DB fixture, 설정 기반 통합 테스트로 재구성하는 것이 좋다.

## 5. 생성 산출물 현황

`data/output`에는 다음 유형의 산출물이 확인된다.

| 유형 | 예시 |
| --- | --- |
| 통합 평면 | `combined_plan.dxf`, `combined_plan.png` |
| 장비 평면 | `equipments_plan.dxf`, `equipments_plan.png` |
| 덕트 평면 | `ducts_plan.dxf`, `ducts_plan.png` |
| 분기 배관 평면 | `laterals_plan.dxf`, `laterals_plan.png` |
| Shapefile | `combined_elements.*`, `combined_pocs.*`, `equipments.*`, `pocs.*`, `ducts.*`, `laterals.*` |
| 3D HTML | `view_3d_plan.html`, `view_equipment_obb.html`, `Pump_RoutePath_3D.html` |
| 상세 이미지 | `data/output/equipments_detail_images/*.png` |

특히 2026-06-06 오후에 `combined_plan.*`, `ducts_plan.*`, `laterals_plan.*`, `equipments_plan.*` 산출물이 갱신되어 있어 export 계열 개발은 최근까지 활발히 진행된 상태다.

## 6. 검증 결과 요약

| 항목 | 결과 | 비고 |
| --- | --- | --- |
| TopKSearchStandalone 빌드 | 성공 | 경고 0, 오류 0 |
| VectorDBGen 빌드 | 성공 | `Npgsql 7.0.6` 보안 경고 |
| SchemaCheckApp 빌드 | 성공 | 경고 0, 오류 0 |
| Python Tools 문법 검증 | 성공 | `py_compile` 기준 |
| DB 연동 E2E 테스트 | 미수행 | 실제 로컬 DB 상태/계정 의존 |
| 산출물 존재 확인 | 성공 | `data/output`에 최신 결과물 다수 존재 |

## 7. 주요 이슈 및 리스크

### 7.1 외부 의존 파일 누락 가능성

VectorDBGen이 실행하도록 설계된 Python 빌더와 DDL 파일이 현재 저장소에서 확인되지 않는다.

영향:

- VectorDBGen UI 빌드는 성공하더라도 실제 Feature/Context/Group/Segment 생성 실행은 실패할 수 있다.
- 신규 개발자가 저장소만 받아서는 전체 벡터 생성 파이프라인을 재현하기 어렵다.

권고:

- 빌더 스크립트와 DDL 파일을 저장소에 포함한다.
- 외부 저장소/별도 배포물이라면 README에 경로와 설치 절차를 명시한다.
- VectorDBGen에서 실행 전 누락 파일을 명확히 표시하도록 preflight 메시지를 강화한다.

### 7.2 DB명/설정 불일치

- `TopKSearchStandalone` 기본 DB명: `AUTOROUTINGV7`
- `VectorDBGen`, Python Tools, SchemaCheckApp 기본 DB명: `DDW_AI_DB`

영향:

- 같은 프로젝트 안에서 실행 대상 DB가 혼재되어 재현성이 떨어진다.
- 사용자가 기본값 그대로 실행할 때 서로 다른 DB를 조회할 수 있다.

권고:

- 공통 설정 파일 또는 환경변수 기반으로 DB 설정을 통일한다.
- 최소한 `.env.example` 또는 `appsettings.example.json` 형태의 기준 설정을 둔다.

### 7.3 하드코딩된 계정/비밀번호/절대 경로

여러 Python 스크립트와 SchemaCheckApp에 `localhost`, `postgres`, `dinno`, `D:\DINNO\...` 경로가 직접 포함되어 있다.

영향:

- 다른 PC/서버에서 실행하기 어렵다.
- 비밀번호가 저장소에 남는 보안 문제가 있다.

권고:

- DB 접속 정보는 환경변수, CLI 인자, 설정 파일로 분리한다.
- 출력 경로는 기본값을 두되 `--out-dir`로 변경 가능하게 만든다.

### 7.4 취약 패키지 경고

VectorDBGen 빌드 중 `Npgsql 7.0.6`에 높은 심각도 취약성 경고가 발생했다.

권고:

- `Npgsql`을 8.x 또는 보안 패치가 포함된 버전으로 업데이트한다.
- TopKSearchStandalone은 이미 `Npgsql 8.0.3`을 사용하므로 버전 정합성도 함께 맞춘다.

### 7.5 자동 테스트 체계 부족

현재 `test_*.py` 파일은 존재하지만, 표준 테스트 프레임워크 기반 자동 테스트라기보다 DB 확인/실험 스크립트에 가깝다.

권고:

- 순수 함수 테스트: `parse_size_to_radius`, `get_bottom_footprint`, `parse_pocs`, `BuildQueryVector30D`
- DB smoke test: 연결, 필수 테이블/컬럼 존재, 샘플 조회
- 산출물 smoke test: DXF/PNG/Shapefile/HTML 파일 생성 여부 및 최소 크기 검증

### 7.6 문서 인코딩 문제

기존 `Docs/System_Analysis.md`, `Docs/ExportPlan_Development_Doc.md`는 일부 환경에서 한글이 깨져 보인다.

권고:

- 문서를 UTF-8로 재저장한다.
- README/보고서/코드 주석의 인코딩 기준을 UTF-8로 통일한다.

## 8. 개발 완료도 평가

| 영역 | 완료도 | 평가 |
| --- | ---: | --- |
| Top-K 검색 엔진 | 80% | 핵심 검색/CLI/스키마 진단 구현, 실제 검색 품질 검증 필요 |
| VectorDB 생성 UI | 65% | UI/제어 흐름 구현, 외부 빌더/DDL 누락 여부 해결 필요 |
| 2D 평면 export | 85% | 장비/덕트/분기/통합 산출물 생성 확인, 설정 분리 필요 |
| 3D 시각화 | 75% | HTML 산출물 존재, 인터랙션/성능 검증 필요 |
| DB 스키마 점검 | 55% | 보조 앱/CLI 진단 존재, 통합된 검증 체계 필요 |
| 테스트/배포 준비 | 35% | 빌드 가능하나 자동화, 설정, 보안, 문서 정비 필요 |

종합적으로는 “기능 PoC는 상당 부분 구현되었고, 데모/내부 검증 가능한 단계”로 평가된다. 다만 “새 환경에서 재현 가능한 패키지” 또는 “운영 도구”로 보기에는 설정/의존성/테스트/문서 정리가 아직 필요하다.

## 9. 다음 개발 권장 순서

1. VectorDBGen 외부 의존성 정리
   - 누락된 빌더 Python 파일과 DDL SQL 파일을 저장소에 포함하거나, 별도 경로 설정 기능 추가
   - 실행 전 파일 존재 여부를 UI에서 명확히 표시

2. 설정 외부화
   - DB 접속 정보, 출력 경로, 기본 DB명을 `.env`, JSON 설정, CLI 옵션으로 분리
   - 비밀번호 하드코딩 제거

3. 보안/버전 정리
   - VectorDBGen `Npgsql` 업데이트
   - C# 프로젝트 간 Npgsql 버전 정합성 확보

4. 자동 검증 체계 구축
   - C# 단위 테스트 프로젝트 추가
   - Python `pytest` 기반 순수 함수 테스트 추가
   - DB 연결이 필요한 테스트는 smoke/integration으로 분리

5. 문서 정비
   - 기존 Markdown 문서 UTF-8 재저장
   - 실행 방법, 필요 DB 스키마, 산출물 예시, 문제 해결 가이드 작성

6. 산출물 품질 검증
   - DXF/Shapefile을 실제 CAD/GIS 도구에서 열어 layer/좌표/Z값 검증
   - HTML 파일 크기와 렌더링 성능 점검

## 10. 결론

TopKGen은 라우팅 DB 기반 검색과 도면/공간 산출물 생성을 위한 핵심 기능이 이미 상당히 구현되어 있다. `TopKSearchStandalone`은 독립 검색 엔진으로 빌드 가능하며, Python export 도구들은 최신 산출물을 생성한 흔적이 뚜렷하다. `VectorDBGen`도 운영자용 UI 뼈대와 실행 제어가 충분히 작성되어 있다.

현재 가장 중요한 과제는 “재현성”이다. 저장소만으로 전체 파이프라인을 다시 실행할 수 있도록 누락 의존 파일, DB 설정, 출력 경로, 보안 설정, 테스트 체계를 정리하면 PoC에서 실사용 가능한 내부 도구 단계로 빠르게 올라갈 수 있다.
