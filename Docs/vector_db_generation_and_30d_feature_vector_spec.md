# [설계 개발 문서] Top-K 유사 설계 검색을 위한 벡터 DB 생성 모듈 및 30D 특징 벡터(Feature Vector) 상세 규격

본 문서는 자동 라우팅 엔진(TopKGen)에서 유사설계 검색을 수행하기 위해 사용되는 **벡터 데이터베이스 구축 파이프라인**과 경로의 기하학적 형태를 30차원 밀집 벡터로 변환하는 **30D 특징 벡터(Feature Vector)의 인코딩 상세 규격**을 기술합니다.

---

## 1. 벡터 DB 생성 모듈 개발 개요

Top-K 유사설계 검색의 핵심은 기설계된 수만 개의 배관 경로 중에서 현재 라우터가 설계하고자 하는 시작점/끝점 조건 및 위상 패턴과 가장 유사한 경로들을 고속으로 찾는 것입니다. 이를 위해 다음과 같은 모듈식 파이프라인을 구축했습니다.

### ① 파이프라인 구동 주체
- **[VectorDBGen (C# WPF UI)](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/VectorDBGen/MainWindow.xaml.cs)**: 사용자로부터 PostgreSQL 데이터베이스 연결 설정을 입력받아 스키마 초기화(DDL 실행), 스냅샷 뷰 등을 수행하고 비동기 프로세스로 파이썬 빌더를 기동합니다.
- **[learn_design_features.py (Python)](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/Tools/learn_design_features.py)**: 실제 데이터베이스로부터 배관 데이터를 로드하고, 기하 보정 및 30D 특징 벡터 연산을 수행하여 PostgreSQL 내의 벡터 테이블에 벌크 적재(Upsert)합니다.

### ② 데이터베이스 스펙 (`TB_ROUTE_FEATURE_VECTOR`)
특징 벡터를 적재하기 위한 테이블 정의 및 pgvector HNSW 인덱스는 다음과 같이 정의됩니다.

```sql
CREATE TABLE IF NOT EXISTS "TB_ROUTE_FEATURE_VECTOR" (
    "ROUTE_PATH_GUID" text PRIMARY KEY,
    "PROCESS_NAME" text,
    "EQUIPMENT_NAME" text,
    "UTILITY_GROUP" text,
    "UTILITY" text,
    "SIZE" text,
    "DIRECTION_PATTERN" text,
    "TOTAL_LENGTH_MM" double precision,
    "STEP_COUNT" integer,
    "START_POSX" double precision,
    "START_POSY" double precision,
    "START_POSZ" double precision,
    "END_POSX" double precision,
    "END_POSY" double precision,
    "END_POSZ" double precision,
    "FEATURE_VECTOR" vector(30),
    "CREATED_AT" timestamp without time zone DEFAULT now()
);

-- 고속 유사도 검색을 위한 HNSW 코사인 인덱스 생성
CREATE INDEX IF NOT EXISTS "IX_TRFV_HNWS" 
ON "TB_ROUTE_FEATURE_VECTOR" USING hnsw ("FEATURE_VECTOR" vector_cosine_ops);
```

---

## 2. 30D 특징 벡터 (30D Feature Vector) 세부 명세

30차원 특징 벡터는 경로의 기하학적 형태(시점/종점 진입 방향, 공간 변위, Bounding Box 범위, 3구간 분할 주행 형태, 전체 길이 등)를 인코딩하여 코사인 거리 공간 상에서 직관적으로 형태 비교가 가능하게 설계되었습니다.

### ① 30D 차원 분할 구성 (Dimension Mapping)

| Index 범위 | 피처 이름 | 수식 / 계산 방법 | 의미 및 인코딩 목표 |
| :--- | :--- | :--- | :--- |
| **0 ~ 2** | **Start Direction** | $\vec{v}_{start} = \frac{p_1 - p_0}{\|p_1 - p_0\|}$ | 배관 시작 부분(첫 번째 세그먼트)의 3D 단위 방향 벡터 |
| **3 ~ 5** | **End Direction** | $\vec{v}_{end} = \frac{p_{n-1} - p_n}{\|p_{n-1} - p_n\|}$ | 배관 종료 부분(마지막 세그먼트)의 3D 단위 방향 벡터 |
| **6 ~ 8** | **Displacement** | $\vec{d} = \text{Clamp}\left(\frac{p_n - p_0}{\text{DISPLACEMENT\_MAX}}\right)$ | 시작점에서 종료점까지의 X, Y, Z 총 변위 (스케일 정규화) |
| **9 ~ 12** | **Bounding Box** | $\vec{b} = \text{Clamp}\left(\frac{\text{abs}(p_n - p_0)}{\text{BBOX\_MAX}}\right)$ | 전체 경로가 차지하는 Bounding Box 크기 비율 (부호 없음) |
| **12 ~ 14** | **Segment 1** | $\vec{s}_1 = \frac{r_1 - r_0}{\|r_1 - r_0\|}$ | 경로를 3구간으로 리샘플링했을 때, **첫 번째 구간**의 단위 방향 벡터 |
| **15 ~ 17** | **Segment 2** | $\vec{s}_2 = \frac{r_2 - r_1}{\|r_2 - r_1\|}$ | 경로를 3구간으로 리샘플링했을 때, **두 번째(중앙) 구간**의 단위 방향 벡터 |
| **18 ~ 20** | **Segment 3** | $\vec{s}_3 = \frac{r_3 - r_2}{\|r_3 - r_2\|}$ | 경로를 3구간으로 리샘플링했을 때, **세 번째 구간**의 단위 방향 벡터 |
| **21** | **Total Length** | $l = \text{Clamp}\left(\frac{L_{total}}{\text{TOTAL\_LENGTH\_MAX}}\right)$ | 배관 전체 주행 경로의 총 정규화 길이 |
| **22 ~ 24** | **Env Cost** | (0.0으로 패딩) | 장애물 회피 비용 등 환경 특성 요약 피처 영역 |
| **25 ~ 29** | **Arrow Pattern** | (0.0으로 패딩) | 방향성 RLE 부호화 통계 특징 영역 |

> **리샘플링 함수(`resample_polyline_points`)**: 배관 경로 폴리라인 정점들을 선형 보간하여 정확히 동일한 간격의 4개 정점($r_0, r_1, r_2, r_3$)으로 재배치하여 3개 구간 방향 벡터를 구합니다.

### ② 가중치 적용 맵 (WEIGHT_MAP) 및 동적 스케일링
피처 성격에 따라 코사인 유사도 공간 상에서 미치는 영향력(중요도)이 다릅니다. 따라서 각 피처 그룹별로 지정된 가중치(Weight)에 맞게 곱해지는 **Scale Factor**를 계산하여 벡터에 곱해줍니다.

- **피처 가중치 맵**:
  - `start_topology` (0.20), `end_topology` (0.20)
  - `displacement` (0.15), `bounding_box` (0.15)
  - `segment_1 / 2 / 3` (각 0.06)
  - `env_cost` (0.12), `arrow_pattern` (0.15)

- **스케일 팩터 산출 공식**:
  각 구간 차원 수(Dimension, $D_{sub}$)와 지정 가중치($W_{sub}$)에 대해 다음과 같이 스케일 팩터 $S_{sub}$를 도출하여 해당 피처 인덱스 값들에 곱해줍니다.
  $$S_{sub} = \sqrt{\frac{W_{sub} \times 30.0}{D_{sub}}}$$

### ③ L2 정규화 (L2 Normalization)
스케일 팩터가 적용된 30D 벡터 $\vec{V}$에 대해 크기를 1로 맞추는 L2 정규화를 거쳐 최종 DB에 저장합니다.
$$\vec{V}_{final} = \frac{\vec{V}}{\|\vec{V}\|}$$
이로 인해 pgvector에서 지원하는 **Cosine Distance ($\Leftrightarrow$)** 연산자를 사용해 인덱스 기반의 매우 빠른 초고속 ANN(근사 최근접 이웃) 검색을 수행할 수 있게 됩니다.

---

## 3. 쿼리 벡터 생성 (Query Vector Construction)

사용자가 라우팅 에디터 상에서 시작점 $p_{start}$와 종료점 $p_{end}$를 클릭하면, 라우터 내부에서 동일한 가중치와 정규화 기준을 가지는 **30D 쿼리 벡터**를 실시간으로 구축합니다.

### [TopKSearchStandalone.cs - BuildQueryVector30D](file:///d:/DINNO/DEV/AI-AutoRouting/TopKGen/TopKSearchStandalone/TopKSearchStandalone.cs#L973-L1018)

1. 시작/종점 간의 주 방향 벡터 `(dx, dy, dz)`를 구하고 단위 방향으로 정규화하여 `[0:3]` (Start) 및 `[3:6]` (End, 역방향) 인덱스에 채웁니다.
2. `DISPLACEMENT_MAX`와 `BBOX_MAX` 상수를 활용하여 공간 변위(`[6:9]`) 및 Bounding Box 크기 비율(`[9:12]`)을 채웁니다.
3. 쿼리 시점에서는 중간 경로 꺾임 상태를 알 수 없으므로 중간 구간 방향 벡터(`[12:21]`) 및 기타 통계 영역(`[22:30]`)은 `0.0`으로 패딩합니다.
4. 동일한 `ScaleFactors`를 곱한 후 `L2Normalize` 처리를 수행하여 30D 쿼리를 확정합니다.

---

## 4. 하이브리드 재정렬 (Hybrid Reranking) 알고리즘

pgvector 코사인 검색을 통해 DB로부터 $N$개의 1차 후보군을 빠르게 걸러낸 후, 다음과 같이 세분화된 3대 유사도 점수의 가중합(Combined Score)으로 최종 순위를 Reranking합니다:

1. **상대위치 유사도 (Position Score - 50%)**: 쿼리의 시작/끝 변위 벡터와 후보 배관의 시작/끝 변위 벡터 간의 물리적 거리 차이를 정규화하여 계산합니다. (공간적으로 유사한 위치에 놓여 있는지 검증)
2. **패턴 유사도 (Pattern Score - 30%)**: 쿼리 패턴과 후보의 방향 RLE 패턴 문자열(`H-R-H-D` 등) 간의 **Levenshtein 편집거리(Edit Distance)**를 사용하여 형태적 유사성을 비교합니다.
3. **벡터 유사도 (Vector Score - 20%)**: pgvector 코사인 거리를 코사인 유사도로 변환하여 반영합니다 ($1.0 - \text{CosineDistance}$).

최종 재정렬 수식:
$$\text{Score}_{final} = 0.5 \times \text{Score}_{pos} + 0.3 \times \text{Score}_{pattern} + 0.2 \times \text{Score}_{vector}$$

이와 같이 30D 벡터 기반의 1차 ANN 필터링 and 물리적 상대 위치/RLE 문장 편집거리를 복합 결합한 하이브리드 아키텍처를 적용함으로써, 연산 성능과 실무 설계 유사 매칭 정밀도를 동시에 달성하였습니다.
