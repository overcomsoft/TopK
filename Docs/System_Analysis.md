# TopKGen 프로젝트 개발 분석 문서

## 1. 개요
본 문서는 `TopKSearchStandalone` 및 `VectorDBGen` 프로젝트의 내부 구조, 프로세스 흐름, 주요 함수 및 데이터베이스 사용 현황을 분석한 개발 문서입니다. 두 프로젝트는 PostgreSQL과 `pgvector`를 활용하여 3D 배관 경로 검색 및 벡터 데이터베이스 생성 자동화를 목표로 합니다.

## 2. 프로젝트 별 프로세스 흐름

### 2.1 TopKSearchStandalone
이 프로젝트는 C# 기반의 단일 파일로 작성된 자체 완결형(self-contained) Top-K 경로 유사도 검색 엔진입니다.
- **프로세스 흐름**:
  1. **사용자 입력 수신**: 공정명, 장비명, 유틸리티 정보, 시작/종료 좌표(mm) 및 검색할 유사 경로의 개수(k)를 파라미터로 입력받습니다.
  2. **쿼리 벡터 생성 (Phase 1)**: 입력된 시작 좌표(startXyz)와 종료 좌표(endXyz)를 기반으로 `BuildQueryVector30D` 함수를 호출하여 30차원(30D) 쿼리 벡터를 생성합니다. 이때 Start 토폴로지, End 토폴로지, 공간 변위 등을 스케일링 인자(WEIGHT_MAP)를 적용하여 정규화합니다.
  3. **DB 후보 검색 (Phase 2)**: PostgreSQL DB에 연결하여 `FetchCandidatesAsync`를 통해 공정, 장비명 등의 조건(WHERE 절)을 만족하고 코사인 거리 기준(`<=>`) 가장 유사한 상위 N개의 후보를 추출합니다. (N = max(k * 30, 150))
  4. **하이브리드 재정렬 (Phase 3)**: 추출된 N개의 후보를 대상으로 `RerankHybrid` 함수가 작동하여 상대위치(50%), 패턴 유사도(30%), 30D 벡터 유사도(20%)의 가중합으로 최종 점수(Combined Score)를 산출하고 가장 높은 상위 K개를 반환합니다.

### 2.2 VectorDBGen
WPF(Windows Presentation Foundation) 기반의 데스크톱 애플리케이션으로, DB 연결 검증, DDL 테이블 자동 생성, 파이썬 기반 벡터 생성 파이프라인 엔진을 비동기적으로 실행하고 상태를 모니터링합니다.
- **프로세스 흐름**:
  1. **DB 연결 및 스키마 점검**: 사용자가 입력한 DB 호스트, 포트, 계정 정보로 접속을 시도하고 필수 테이블과 `pgvector` 확장 존재 여부를 검사합니다.
  2. **스키마 초기화/재생성**: 선택된 대상 벡터(Feature, Context 등)에 맞는 DDL 스크립트를 Npgsql을 통해 실행하여, `CREATE TABLE IF NOT EXISTS` 혹은 `DROP CASCADE` 방식으로 테이블 환경을 준비합니다.
  3. **파이프라인 서브 프로세스 실행**: `Feature Vector`, `Context Vector`, `Design Group`, `Segment Template` 순서로 구성된 4단계 일괄 빌드 옵션을 선택할 수 있으며, 이 때 C# 어플리케이션은 파이썬 스크립트를 독립 프로세스로 호출하고 stdout을 로깅합니다.
  4. **리포트 엑셀 출력**: 테이블의 이전/이후 Row 변경량(Snapshot) 및 구문별 실행 결과를 수집해 ClosedXML을 통해 `.xlsx` 파일 형태의 상세 보고서를 생성합니다.

## 3. 주요 함수 및 변수 분석

### 3.1 TopKSearchStandalone 주요 함수
- **`SearchAsync(...)`**
  - **기능**: Top-K 검색 파이프라인의 메인 진입 함수. 커넥션을 관리하고 벡터 빌드부터 DB 검색, 최종 리랭킹까지 모두 호출합니다.
  - **변수**: 
    - `db (DbConfig)`: 호스트, 사용자 정보가 담긴 DB 설정 Record.
    - `processName, equipmentName, utilityGroup, utility, size (string)`: SQL 쿼리 시 WHERE 조건에 필터링 될 변수.
    - `startXyz, endXyz (ValueTuple)`: X, Y, Z double형의 시작, 종료 공간 좌표 (단위: mm).
    - `k (int)`: 출력으로 요청할 결과의 갯수.
    - `queryVec (double[])`: 좌표로 변환된 30차원 배열.

- **`BuildQueryVector30D(startXyz, endXyz)`**
  - **기능**: 거리와 방향을 계산하여 정규화 상수에 맞게 변환된 30D 벡터를 추출합니다.
  - **변수**:
    - `dx, dy, dz (double)`: 시작-종료점 사이의 X, Y, Z 편차.
    - `dist (double)`: 시작-종료점 사이의 총 유클리디안 거리.

- **`FetchCandidatesAsync(...)`**
  - **기능**: PostgreSQL에 질의하여 `pgvector` 코사인 거리 검색 쿼리를 생성하고 수행합니다.
  - **변수**:
    - `sql (StringBuilder)`: 동적으로 생성되는 PostgreSQL Select문.
    - `fetchN (int)`: 1차적으로 DB에서 넉넉하게 뽑아올 후보군 갯수.

- **`RerankHybrid(...)`**
  - **기능**: DB 1차 후보군을 위치 점수, 형태 점수, 벡터 점수로 재정렬합니다.
  - **변수**:
    - `posScore, patScore, vecScore (double)`: 각 평가 항목별 가중 점수 변수 (0.0 ~ 1.0).
    - `combined (double)`: 가중치를 적용한 최종 유사도 종합 점수.

### 3.2 VectorDBGen 주요 함수
- **`BtnConnect_Click(...)`**
  - **기능**: UI의 접속 버튼 핸들러. NpgsqlConnection을 통해 DB 접속을 검증하고, 원본 소스와 벡터 테이블들의 무결성을 확인합니다.
  - **변수**:
    - `host, portStr, dbname, user, password (string)`: Text 필드로부터 가져온 접속 값.
    - `candidate (DbConfig)`: DB 접속 시도용 설정 인스턴스.
    - `missingTarget, missingSource (List<string>)`: 검사 후 발견된 누락 테이블 리스트 모음.

- **`RunDdlWithReportAsync(...)`**
  - **기능**: 단일/다중 DDL 파일을 읽어 DB에 실행하며, 스냅샷 전/후를 비교해 리포트를 작성합니다.
  - **변수**:
    - `report (DdlReport)`: 현재 액션의 실행 결과, 걸린 시간 및 테이블 Row Count 변화를 기록하는 모델.
    - `totalOk, totalFail (int)`: 정상 처리 및 실패한 쿼리 구문의 누적 횟수.

- **`BtnRunAll_Click(...)`**
  - **기능**: 4가지 Python 백엔드 데이터 생성 엔진(Feature, Context, Group, Segment)을 연속적으로 호출합니다.
  - **변수**:
    - `tag (string)`: 현재 진행 중인 빌드 스텝의 식별자.
    - `scriptName (string)`: Python 인터프리터로 구동할 맵핑된 .py 스크립트명.
    - `exitCode (int)`: 파이썬 서브 프로세스의 반환(종료) 코드.

## 4. 데이터베이스 및 테이블 분석

### 4.1 연결 데이터베이스
- **Database**: `AUTOROUTINGV7` (PostgreSQL 기반)
- **Extension**: `pgvector` 확장을 통해 vector 타입과 인덱싱(HNSW 등) 기능 필수 요구.

### 4.2 주요 대상(Target) 벡터 테이블
VectorDBGen에서 생성 및 관리되며 TopKSearchStandalone이 조회에 활용하는 인공지능 탐색용 특징 테이블.

1. **`TB_ROUTE_FEATURE_VECTOR`**
   - **목적**: 3D 라우팅 검색 성능의 핵심으로, 30차원의 벡터 임베딩이 저장되는 메인 테이블.
   - **주요 필드**:
     - `ROUTE_PATH_GUID (text)`: 원본 배관 경로 고유 ID (Primary Key).
     - `PROCESS_NAME, EQUIPMENT_NAME, UTILITY_GROUP, UTILITY (text)`: 메타 정보 필드로 Top-K 검색의 Filter 역활.
     - `SIZE (text)`: 배관 구경 메타정보.
     - `START_POSX, START_POSY, START_POSZ (double precision)`: 출발 좌표.
     - `END_POSX, END_POSY, END_POSZ (double precision)`: 목적 좌표.
     - `FEATURE_VECTOR (vector(30))` : pgvector 타입. 30차원 경로 특성 임베딩.
     - `DIRECTION_PATTERN (text)`: 경로의 방향 패턴을 나타내는 문자열.
     - `TOTAL_LENGTH_MM (double precision)`: 경로 전체 길이(mm).
     - `STEP_COUNT (integer)`: 꺾임 및 세그먼트 스텝 횟수.

2. **`TB_ROUTE_CONTEXT_VECTOR`**
   - **목적**: 경로 주변 BIM 환경 및 장애물 상황 정보를 포함하는 24차원의 맥락 임베딩 보관 테이블.

3. **`TB_ROUTE_DESIGN_GROUP` & `TB_ROUTE_SEGMENT_TEMPLATE`**
   - **목적**: AI 오토 라우팅을 위한 디자인 그룹핑 정보와 세그먼트 단위별 설계 파라미터 템플릿 메타 테이블.

### 4.3 소스(Source) 데이터 테이블
Python 빌드 파이프라인이 임베딩 벡터 생성 과정에서 데이터를 수급받기 위한 Read-Only 목적의 원본 테이블. (외부 프로그램/수집기로 적재)

1. **`TB_ROUTE_PATH`**
   - **목적**: 원본 경로 데이터셋으로 TopKSearchStandalone에서도 프리셋 조회 기능 시 참조됨.
   - **주요 필드**:
     - `ROUTE_PATH_GUID (text)`
     - `SOURCE_OWNER_NAME, SOURCE_UTILITY, SOURCE_SIZE (text)`
     - `SOURCE_POSX, SOURCE_POSY, SOURCE_POSZ (double precision)`
     - `TARGET_POSX, TARGET_POSY, TARGET_POSZ (double precision)`
     - `PR_TOTAL_LENGTH, PR_BEND_COUNT`

2. **`TB_ROUTE_SEGMENTS`, `TB_ROUTE_SEGMENT_DETAIL`, `TB_BIM_OBSTACLES`**
   - **목적**: 원본 경로의 각 세그먼트 형상 궤적 데이터 및 BIM 기반 건축물/장비 물리적 장애물 모델 테이블로, `Context Vector` 스크립트 실행을 위해 참조됩니다.
