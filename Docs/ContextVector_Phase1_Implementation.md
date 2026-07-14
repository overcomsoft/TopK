# Context Vector 1단계 개발 결과

작성일: 2026-07-13  
인코더 버전: `topkgen-v2`

## 적용 내용

- 시작점과 종점에 동일한 거리 shell 적용
  - Near: `0~500mm`
  - Mid: `500~1,000mm`
- 장애물 중심점 거리를 AABB 표면 최근접거리로 변경
- 긴 기둥·보가 여러 격자 셀에 걸칠 때 모든 점유 셀에 등록
- 시작·종점 각각 13D, 공통 Tier3 4D인 30D 벡터 적용
- 장애물 없음은 거리 1이 아니라 해당 특징 전체를 0으로 표현
- 시작·종점 주변에 동시에 들어오는 동일 보를 ID 기준으로 중복 제거
- 경로 격자 셀을 순서가 보장되는 traversal로 계산
- 200셀 초과 시 경로 순서 기반 균등 downsampling
- Python 색인기와 C# 신규 쿼리 인코더의 AABB 거리·shell·Tier3 공식을 통일
- `ExtractObstacleContextVector.py`와 `BuildContextVectors.py`가 공용 인코더 사용
- VectorDBGen의 잘못된 테이블명과 DDL 파일명 수정
- GUID와 SOURCE/TARGET XYZ 전체 NULL 및 finite 검증
- 벡터 차원과 L2 norm 검증 테스트 추가

## 30D 레이아웃

### 시작 13D: `[0:13]`

| 상대 인덱스 | 내용 |
|---:|---|
| 0 | 500mm 이내 기둥 수 / 8 |
| 1 | 500~1,000mm 기둥 수 / 8 |
| 2~4 | 최근접 기둥 AABB 표면 방향 XYZ |
| 5 | 최근접 기둥 AABB 표면거리 / 1,000 |
| 6 | 500mm 이내 보 수 / 5 |
| 7 | 500~1,000mm 보 수 / 5 |
| 8~10 | 최근접 보 AABB 표면 방향 XYZ |
| 11 | 최근접 보 AABB 표면거리 / 1,000 |
| 12 | 1,000mm 내 기둥·보가 모두 없는 free-space 표시 |

종점 `[13:26]`도 같은 레이아웃을 사용한다.

### 공통 Tier3: `[26:30]`

| 인덱스 | 내용 |
|---:|---|
| 26 | 시작·종점 Z level 변화 |
| 27 | chord 통과 격자의 기둥 점유 점수 |
| 28 | 주변 보 장축과 chord의 평행도 |
| 29 | chord 수평방향의 X축 cosine |

## 주요 파일

- `Tools/context_vector_encoder.py`: 공용 순수 Python 인코더
- `Tools/ExtractObstacleContextVector.py`: CLI 생성·추출·저장 진입점
- `Tools/BuildContextVectors.py`: VectorDBGen 호환 빌더 진입점
- `Tools/sql/create_route_context_vector_table.sql`: vector(30) v2 스키마와 v1 migration
- `TopKSearchStandalone/TopKSearchStandalone.cs`: 신규 요청 시점 30D 계산 및 재정렬
- `VectorDBGen/MainWindow.xaml(.cs)`: 30D 빌드 UI와 실행 경로
- `Tools/tests/context_vector_encoder_tests.py`: 기하 및 벡터 단위 테스트

## 실행

CLI:

```powershell
python Tools/ExtractObstacleContextVector.py --config Tools/tools.settings.json run-all
```

VectorDBGen:

```powershell
dotnet run --project VectorDBGen/VectorDBGen.csproj
```

Context Vector 빌더를 선택하면 고정된 500/1,000mm shell과 `topkgen-v2`를 사용한다.

## 스키마 변경 주의

`create_route_context_vector_table.sql`은 기존 컬럼이 `vector(24)`이면 파생 데이터인
`TB_ROUTE_CONTEXT_VECTOR`를 비운 뒤 `vector(30)`로 변경한다. 원본 경로와 BIM 장애물
테이블은 변경하지 않는다. v2 적용 후에는 전체 Context Vector를 다시 생성해야 한다.

`TopKSearchStandalone`은 `ENCODER_VERSION='topkgen-v2'`인 후보만 context 점수에 사용한다.

## 검증 결과

- Python unit test: 5개 통과
- Python syntax compile: 통과
- `TopKSearchStandalone.csproj` Release build: 경고 0, 오류 0
- `VectorDBGen.csproj` Release build: 경고 0, 오류 0

DB 실데이터 재생성은 이번 코드 개발 과정에서 실행하지 않았다. 실행 전 DB 백업과
프로젝트/모델 범위 컬럼 확정이 필요하다.

## 다음 단계

- 프로젝트·모델 revision별 장애물 격리
- 실제 DB 분포를 이용한 500/1,000mm shell의 zero-rate 및 count 분포 확인
- Python/C# 고정 fixture 기반 수치 parity 자동화
- 실제 검색 Recall@K, MRR 및 첫 이탈방향 일치율 평가
