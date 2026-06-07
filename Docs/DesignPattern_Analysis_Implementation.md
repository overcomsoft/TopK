# 그룹배관 패턴 분석 및 저장 기능 구현 문서

## 1. 구현 개요

본 기능은 `DDW_AI_DB`의 기존 설계 배관 데이터로부터 장비명 및 유틸리티별로 평행하게 배치된 다발배관(Bundle) 그룹을 자동으로 식별하고 패턴 데이터베이스를 구축하는 Python 도구이다. 구축된 패턴 DB는 AI 자동 라우팅 시 다발배관 패턴 추천 및 재사용 설계 후보 탐색에 활용된다.

구현 파일:

- `Tools/DesignPatternAnalyzer.py`
- `Tools/sql/create_route_group_pattern_tables.sql`

기존 공통 설정 및 의존성:

- `Tools/tool_config.py`
- `Tools/tools.settings.json` 또는 CLI 인자
- `psycopg2`, `pgvector`

---

## 2. 주요 기능

| 기능 | Subcommand | 설명 |
| --- | --- | --- |
| 스키마 생성 | `create-schema` | 다발배관 패턴 저장 테이블(`TB_ROUTE_GROUP_PATTERN`) 및 인덱스 생성 |
| 그룹 패턴 추출 | `extract` | `TB_SPACE_GROUP_INFO` 및 `TB_ROUTE_PATH/SEGMENT/DETAIL`로부터 다발배관을 군집화 및 검증하여 저장 |
| 일괄 실행 | `run-all` | 스키마 생성 및 그룹 패턴 추출을 한 번에 수행 |

---

## 3. 저장 테이블 (`TB_ROUTE_GROUP_PATTERN`)

추출 및 검증 단계를 거쳐 최종 도출된 평행 다발배관 그룹 정보를 저장한다.

### 3.1 테이블 컬럼 구조

- **`GROUP_ID`** (text, Primary Key): 설계 프로젝트명, 유틸리티, 멤버 GUID들을 조합하여 생성한 고유 해시 ID
- **`TAG_GROUP_NM`** (text): 설계 프로젝트명 (예: `WTNHJ09`, `SLWHJ04`)
- **`UTILITY`** (text): 배관의 유틸리티명 (예: `PCWS`, `LPR`)
- **`N_MEMBERS`** (integer): 해당 다발배관 그룹에 속한 멤버 배관 수
- **`AVG_SIMILARITY`** (double precision): 그룹 내 배관 경로 쌍 간의 평균 복합 유사도 (1.0에 가까울수록 강한 결합)
- **`TRUNK_Z`** (double precision): 주경로(Trunk)가 형성된 공용 랙 고도 (mm)
- **`TRUNK_XY_SPREAD`** (double precision): 주경로 내 배관들 간의 최대 수평 벌어짐 (다발의 전체 horizontal 폭, mm)
- **`PITCH_MM`** (double precision): 인접 배관 간 대표 이격간격 (중앙값, mm)
- **`N_ORTHO_BENDS`** (integer): 그룹 배관들의 대표 수직/수평 꺾임 수 (중앙값)
- **`MEMBER_GUIDS`** (jsonb): 소속 멤버들의 `ROUTE_PATH_GUID` 리스트
- **`PATTERN_SEQ`** (text): 그룹 배관의 대표 V/H/D 패턴 분석 시퀀스 문자열 (예: `"VHV"`)
- **`SECTION_BOUNDS`** (jsonb): 각 세부 구간(수직배관 및 수평배관)의 Boundary Box 목록 리스트
- **`FEAT`** (vector(60)): 60차원 대표 리샘플링 방향 벡터 (pgvector 코사인/L2 유사도 검색용)
- **`FEAT_JSON`** (jsonb): pgvector 미지원 환경을 대비한 60차원 json 실수 배열
- **`CREATED_AT`** (timestamp): 데이터 생성 시각

---

## 4. 추출 및 클러스터링 알고리즘

배관 다발 식별은 크게 **4단계** 파이프라인으로 수행된다.

### 4.1 Phase 1 — 개별 경로 특징 추출 (`PipeFeature`)
각 배관 경로(Polyline)에서 기하학적 형태 정보를 추출한다.
* **`dir_runs`**: 세그먼트들을 6직교 축으로 스냅하고 연속된 동일 방향을 길이를 더하여 병합한 리스트 (`[(축, 길이)]`)
* **`arrow_code`**: 각 세그먼트 벡터를 Z축이 지배적인 `V`(수직), XY축이 지배적인 `H`(수평), 혹은 모호한 `D`(경사) 코드로 판별한 뒤 연속 중복을 제거한 문자열 (예: `VHVHDH`)
* **`n_ortho_bends`**: 인접한 런들 사이에서 축이 변경되는 직교 꺾임 횟수
* **`seg_units`**: 배관 총 길이를 $N=20$ 구간으로 리샘플링하여 계산된 20개의 단위 방향 벡터 (총 60차원 벡터)
* **`extent` & `centroid`**: Bounding Box(dx, dy, dz) 및 중심 좌표
* **`trunk_axis`**: `dir_runs` 중 가장 긴 수평 런이 진행하는 수평 축 (X축=0, Y축=1)

### 4.2 Phase 2 — 복합 유사도 계산 (4대 지표 가중합)
두 배관 경로 $a$, $b$ 간의 복합 유사도 `sim(a,b) ∈ [0, 1]`를 계산한다:
$$\text{sim}(a,b) = 0.3 \cdot \text{Shape} + 0.3 \cdot \text{Direction} + 0.2 \cdot \text{Length} + 0.2 \cdot \text{Scale}$$
* **Shape (형태 일치도, 30%)**: `1 - Levenshtein(arrow_a, arrow_b) / max_len`
* **Direction (방향성 일치도, 30%)**: $N=20$ 구간의 평균 코사인 유사도. 배관 그리기 방향(순방향/역방향) 중 최댓값 선택.
* **Length (길이 일치도, 20%)**: `1 - |L_a - L_b| / max(L_a, L_b)`
* **Scale (물리적 규모 일치도, 20%)**: X, Y, Z Bounding Box 범위 비율의 산술평균

### 4.3 Phase 3 — 그룹화 및 번들 게이트 검증
1. **Pre-filter**: `(TAG_GROUP_NM, UTILITY)`를 파티션 키로 삼아 같은 조건 내에서만 비교를 수행한다.
2. **Union-Find 클러스터링**: 파티션 내 모든 쌍 중 **복합 유사도 $\ge 0.70$**인 쌍을 결합하여 후보 그룹을 도출한다.
3. **번들 게이트 검증**: 다음의 설계 도메인 규칙을 모두 통과한 후보만 최종 번들로 선별한다.
   * **멤버 수**: 그룹 내 배관 수 $\ge 2$
   * **꺾임 수**: 멤버 꺾임 횟수 중앙값(median) $\ge 2$
   * **동일 이격간격**: 다발의 대표 주축(`trunk_axis`)의 수직 평면 좌표값들을 정렬하여 인접 간격(Pitch)들을 구하고, 해당 간격의 변동계수(CV = 표준편차 / 평균) $\le 0.30$을 충족해야 한다.
4. **트렁크(주경로) 정보 탐지**:
   * 멤버들의 수평 런 Z-고도 최빈값을 주경로 고도(`trunk_z`)로 선택한다.
   * perpendicular offset의 최대 벌어짐(`trunk_xy_spread`) 및 pitch 중앙값(`pitch_mm`)을 저장한다.

### 4.4 Phase 4 — 그룹 배관의 다중 구간 및 Boundary Box 추출
1. **대표 멤버 및 패턴 식별**: 그룹 내 멤버들의 `arrow_code` 최빈값을 그룹의 대표 패턴(`PATTERN_SEQ`)으로 결정하고, 해당 패턴을 가지는 가장 긴 멤버를 대표 멤버로 선정한다.
2. **구간 분할 (Segmentation)**: 대표 멤버 배관의 세그먼트들을 수직(`V`), 수평(`H`), 사선(`D`) 타입별로 연속된 런(Run)으로 병합하여 $N$개의 구간(Section)으로 분할한다.
3. **그룹 영역으로 확장**: 각 구간에 대해 dominant axis를 선정하고, 그룹에 속한 모든 배관 멤버들의 좌표 중 해당 구간 범위(±100mm 마진 포함)에 속하는 점들의 Bounding Box를 병합하여 해당 구간의 전체 그룹 Bounding Box를 결정한다.
4. **결과 저장**: 각 구간의 타입(`V`, `H`, `D`) 및 3차원 Bounding Box 정보(`min`, `max`) 목록을 JSONB 배열 형식(`SECTION_BOUNDS`)으로 최종 저장한다.

---

## 5. 실행 명령어 예시

### 5.1 데이터베이스 스키마 생성
```powershell
python Tools/DesignPatternAnalyzer.py --password dinno create-schema
```

### 5.2 드라이런 테스트 (DB 저장 생략)
실제 적재하기 전에 추출 및 매핑이 원활한지 로그로 사전 확인한다.
```powershell
python Tools/DesignPatternAnalyzer.py --password dinno extract --dry-run
```

### 5.3 배관 패턴 추출 및 데이터베이스 적재
데이터를 전량 분석하여 `TB_ROUTE_GROUP_PATTERN`에 덮어쓰기 방식으로 저장한다.
```powershell
python Tools/DesignPatternAnalyzer.py --password dinno extract
```
