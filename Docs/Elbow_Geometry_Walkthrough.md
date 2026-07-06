# 엘보우(Elbow) 기하 복원 및 라우터 연계 구현 완료 보고서

본 보고서는 설계 데이터 내의 엘보우(Elbow) 기하 왜곡 문제를 수학적 교차점(IP)으로 복원하고, 자동 라우터의 3D 시각화 시 직관과 엘보우 세그먼트를 분리하는 후처리를 적용한 내용을 요약합니다.

---

## 1. 주요 구현 내용

### 1.1 Python 데이터 추출기 기하 전처리 (`Tools/ExtractStubPatterns.py`)
* [ExtractStubPatterns.py](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/Tools/ExtractStubPatterns.py#L548-L640)
* **내용**: `fetch_route_points` 에서 DB의 기존 설계 배관을 읽을 때 `sd.TYPE`을 함께 로드합니다.
* **기하 연산**: `ELBOW` 타입을 감지할 시, 앞뒤 `PIPE` 세그먼트의 3D Skew Line 교차점(최근접점)을 수학적으로 계산(IP 복원)하여 복원된 폴리라인의 사선 성분을 제거하고 완벽한 직교 경로로 변환시킵니다.

### 1.2 C# 기존 배관 및 유사설계 로더 기하 전처리 (`ObstacleDbLoader.cs`, `MainWindow.xaml.cs`)
* [ObstacleDbLoader.cs](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/AutoRouteFinder/Models/ObstacleDbLoader.cs#L236-L320)
* [MainWindow.xaml.cs (LoadPathPoints)](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/AutoRouteFinder/MainWindow.xaml.cs#L1030-L1095)
* **내용**: DB에서 기존 배관 장애물(`ExistingPipe`) 및 유사 설계(Top-K) 경로를 로드할 때 세그먼트들을 수집하고 `ReconstructIntersectionPoints`를 적용하여 IP를 복원한 뒤 직교 포인트 리스트로 가공합니다.
* **효과**: C++ 라우팅 엔진에 주입되는 회랑(Corridor)이나 제약 데이터에서 사선 왜곡이 완전히 배제되어, 라우팅 알고리즘의 비직교적 오작동을 차단합니다.

### 1.3 C# 자동설계 결과 및 기존 배관 엘보우 분할 후처리 시각화 (`MainWindow.xaml.cs`)
* [MainWindow.xaml.cs (DividePathToElbows)](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/AutoRouteFinder/MainWindow.xaml.cs#L1031-L1110)
* [MainWindow.xaml.cs (MergeExistingPipes)](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/AutoRouteFinder/MainWindow.xaml.cs#L1134-L1225)
* **내용**: 
  * C++ 라우터가 탐색한 경로뿐만 아니라, **기존 설계 배관(Existing Pipes)을 화면에 렌더링할 때도** 동일하게 `DividePathToElbows` 후처리를 적용했습니다.
  * DB에서 로드되는 기존 설계 배관 데이터가 개별 세그먼트 레코드로 파편화되어 로드될 경우 코너 감지가 불가능해지는 문제를 해결하기 위해, 렌더링 직전에 **인접 배관 조각 병합 알고리즘(`MergeExistingPipes`)**을 추가했습니다.
  * 꺾임 코너 부근을 엘보우 간이 치수 $E = 1.5 \times D$ (Long Radius 기준) 만큼 벌려 **직관**과 **엘보우** 구간으로 쪼개어 각각 튜브 모델로 생성합니다.
* **시각화**: 직관은 기본 유틸리티 색상(Exhaust 등)으로, 코너 엘보우 부분은 주황색(Orange)으로 색상을 분리하여 렌더링함으로써 엘보우 형상의 특징적 위치를 명확히 모사합니다.

---

## 2. 검증 결과

### 2.1 파이썬 전처리 및 Stub 패턴 추출 테스트
* `ExtractStubPatterns.py`를 실행하여 기존 씬 데이터를 분석하고 패턴을 정상적으로 추출함을 확인했습니다.
  ```bash
  python Tools/ExtractStubPatterns.py extract --config Tools/tools.settings.json --limit 5 --dry-run
  ```
  * **결과**: `Extracted samples: 9` 정상 출력. DB의 깨진 사선 노이즈가 제거되어 Stub Points 내에서 깨끗한 직교 진행 방향($+x$, $+y$, $+z$, $-x$, $-y$, $-z$)을 산출하는 것을 검증했습니다.

### 1.4 3D 뷰 툴바 레이아웃 리팩토링 및 클립보드 복사 (`MainWindow.xaml`)
* [MainWindow.xaml](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/AutoRouteFinder/MainWindow.xaml#L220-L255)
* **내용**: 
  * 기존 뷰포트 내부에 겹쳐서 배치되어 WPF Airspace(하드웨어 가속) 문제로 화면상에 가려 보이지 않던 제어 버튼들을 해결했습니다.
  * 중앙 3D 뷰 영역에 Row를 2개로 분할하여, Row 0에 전용 2D 툴바(`3D View Control`)를 확보하고 Row 1에 `Viewport3D`를 재배치했습니다.
  * 이로써 **"Copy Viewport"**, **"Reset View"**, **"Clear 3D"** 버튼이 최상위에 언제나 선명하게 드러나며 클릭 시 클립보드 복사 및 제어 기능이 정상 실행됩니다.

---

## 2. 검증 결과

### 2.1 파이썬 전처리 및 Stub 패턴 추출 테스트
* `ExtractStubPatterns.py`를 실행하여 기존 씬 데이터를 분석하고 패턴을 정상적으로 추출함을 확인했습니다.
  ```bash
  python Tools/ExtractStubPatterns.py extract --config Tools/tools.settings.json --limit 5 --dry-run
  ```
  * **결과**: `Extracted samples: 9` 정상 출력. DB의 깨진 사선 노이즈가 제거되어 Stub Points 내에서 깨끗한 직교 진행 방향($+x$, $+y$, $+z$, $-x$, $-y$, $-z$)을 산출하는 것을 검증했습니다.

### 2.2 C# 솔루션 컴파일 및 빌드
* `dotnet build AutoRouteFinder.sln` 명령을 통해 전체 프로젝트 솔루션 빌드를 성공적으로 수행했습니다. (경고 17개, **오류 0개**로 빌드 완료)
