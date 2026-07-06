# 엘보우(Elbow) 기하 복원 및 라우팅 엔진 연계 구현 계획서

본 계획서는 기존 설계 배관 데이터의 엘보우(Elbow) 사선 경로 왜곡을 간이 계산 방식으로 직교 기하 교차점(IP)으로 복원하는 전처리를 적용하고, 자동 라우팅 결과물 저장 시 IP 기준 엘보우 자재 분할 저장을 구현하여 기존 설계 스타일과의 호환성을 극대화하기 위한 상세 계획입니다.

---

## 1. 개요 및 설계 방안

### 1.1 엘보우 간이 치수 산정 공식
자재 규격 DB 연동 대신, 다음 공식을 기반으로 엘보우의 Center-to-End 치수 $E$를 계산합니다:
* **Long Radius 엘보우 기준**: $E = 1.5 \times \text{배관의 공칭 직경 또는 외경}(D)$
* 배관 규격 파싱을 통해 `DiameterMm`을 획득하고, 이를 기준으로 $E$를 산정합니다. (예: $100\text{A} \rightarrow D = 100\text{mm} \rightarrow E = 150\text{mm}$)

### 1.2 IP 복원 알고리즘 (데이터 로딩 전처리)
1. DB로부터 `TB_ROUTE_SEGMENT_DETAIL`을 읽을 때 `TYPE = 'ELBOW'`인 구간을 스캔합니다.
2. 이전 배관 방향 벡터 $\vec{v}_1$과 다음 배관 방향 벡터 $\vec{v}_2$의 3D 공간 상의 최근접 교차점(IP)을 수학적으로 계산합니다.
3. 교차 오차(Skew Line Distance)가 $500\text{mm}$ 이하인 타당한 꺾임일 경우, 엘보우 사선 세그먼트 대신 **직교형 교차점 IP**로 포인트 시퀀스를 갱신하여 사선 배관 오인을 제거합니다.

### 1.3 C++ 라우터 결과 저장 처리 (포스트 피팅 분할 저장)
1. C++ 라우터가 탐색을 마친 뒤, 결과물인 직교 격자 경로 점열을 스캔하여 방향이 변경되는 꺾임점(IP)을 검출합니다.
2. 꺾임점(IP) 전후로 엘보우 치수 $E$만큼 이격된 포트 위치($P_{in}, P_{out}$)를 구합니다.
3. 이 위치를 분할하여 DB에 다음과 같이 쪼개어 저장합니다:
   * **[PIPE]**: 진입점 $\rightarrow P_{in}$
   * **[ELBOW]**: $P_{in} \rightarrow P_{out}$
   * **[PIPE]**: $P_{out} \rightarrow$ 다음 코너 또는 종점

---

## 2. 제안된 변경 사항 (Proposed Changes)

### [Component: Data Analysis & Reconstruction (Python)]

#### [MODIFY] [ExtractStubPatterns.py](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/Tools/ExtractStubPatterns.py)
* `fetch_route_points` 함수를 수정하여 DB 쿼리 시 `sd.TYPE`을 추가로 조회합니다.
* 파이썬 버전의 3D Skew Line 교차점 계산 함수 및 IP 복원 알고리즘을 구현하여, 복원된 폴리라인에서 엘보우 사선 배관을 완전히 제거하고 IP 중심의 직교 노드로 전처리합니다.

---

### [Component: Data Loader & Visualization (C#)]

#### [MODIFY] [ObstacleDbLoader.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/AutoRouteFinder/Models/ObstacleDbLoader.cs)
* `LoadRoutesAndTasks` 내 PostgreSQL 쿼리 리더 루프에서 `sd.TYPE` 데이터를 기반으로 세그먼트 데이터를 구조화하여 임시 수집하도록 변경합니다.
* `ReconstructIntersectionPoints` 기하 꺾임 복원 헬퍼 메서드를 추가합니다.
* 기존 스트리밍식 포인터 적재를 **수집 -> IP 복원 및 직교화 전처리 -> ExistingPipe 생성** 순으로 리팩토링합니다.

---

### [Component: Router Results Saving System (C#)]

#### [MODIFY] [MainWindow.xaml.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/AutoRouteFinder/MainWindow.xaml.cs)
* 라우팅이 완료되어 결과를 DB(`TB_ROUTE_SEGMENT_DETAIL`)에 저장하는 메소드를 식별합니다.
* 저장 직전에 결과 경로(직교 점열)에서 꺾임 모서리를 검출하고, 배관 사이즈별 간이 엘보우 치수 $E$를 계산하여 **[직관] - [엘보우] - [직관]** 구조로 세그먼트를 조각내어 저장하는 데이터 분할 로직을 주입합니다.

---

## 3. 검증 계획 (Verification Plan)

### 3.1 Python 패턴 분석 동작 검증
* CLI 명령어를 수행하여 Stub 패턴 추출 프로세스가 정상 동작하는지 확인합니다.
  ```bash
  python Tools/ExtractStubPatterns.py extract --config Tools/tools.settings.json --limit 50 --dry-run
  ```
* 추출된 Stub Points 시퀀스 내에 사선 성분이 배제되고 직교 좌표 스냅이 정상적으로 정합되는지 확인합니다.

### 3.2 C# 데이터 로더 및 라우팅 결과 시각화 검증
* `AutoRouteFinder` 프로그램을 실행하고 씬 데이터를 재로딩합니다.
* 꺾여 들어가는 코너 부분의 배관이 사선 대각선 없이 정확히 직각의 꺾임점(IP)을 기하구조로 복원하여 그려지는지 3D 뷰어로 수동 검증합니다.
* 자동 라우팅 실행 후 저장된 데이터를 검증하여, `TB_ROUTE_SEGMENT_DETAIL`에 생성되는 세그먼트들이 `TYPE='PIPE'`와 `TYPE='ELBOW'`로 알맞게 분할 저장되었는지 DB에서 쿼리 검증합니다.
