# UtilityPipeGroup Top-K 단계 2 Vector Builder 구현 결과

- 완료일: 2026-07-16
- 대상 DB: `DDW_AI_DB`
- 대상 scope: `DB:DDW_AI_DB`
- 대상 revision: `snapshot:7cd7f53b47e68623ad5f783a48246968aa1ba9d497e6d6f05cd1172a5840d131`
- 결과: 완료

## 1. 최종 적재 결과

| 항목 | 결과 |
|---|---:|
| ACTIVE 원본 Route | 827 |
| 전체 그룹 | 186 |
| 멤버 1개 제외 그룹 | 80 |
| READY UtilityPipeGroup | 106 |
| READY 멤버 행 | 747 |
| 서로 다른 멤버 Route | 747 |
| Feature coverage | 747/747, 100% |
| 호환 Context coverage | 747/747, 100% |
| Context가 완전한 그룹 | 106/106 |
| 상세 segment 좌표 fallback | 0건 |
| Source Hash drift | 0건 |
| STALE 그룹 | 0건 |

두 번째 동일 build 실행 결과는 재생성 0개, 변경 없는 그룹 skip 106개, 멤버 write 0개였다. 따라서 Source Hash 기반 증분 생성이 실제 DB에서 동작함을 확인했다.

## 2. 실행 명령

### 2.1 Schema 확인/생성

```powershell
.\.venv\Scripts\python.exe Tools\BuildUtilityPipeGroupVectors.py create-schema `
  --config Tools\tools.settings.json
```

### 2.2 DB 변경 없는 사전 계산

```powershell
.\.venv\Scripts\python.exe Tools\BuildUtilityPipeGroupVectors.py build `
  --config Tools\tools.settings.json `
  --scope-mode active `
  --min-members 2 `
  --dry-run
```

### 2.3 실제 전체/증분 생성

```powershell
.\.venv\Scripts\python.exe Tools\BuildUtilityPipeGroupVectors.py build `
  --config Tools\tools.settings.json `
  --scope-mode active `
  --min-members 2 `
  --report-out data\output\utility_pipe_group_build_phase2.json
```

입력과 설정의 Source Hash가 같은 READY 그룹은 자동으로 건너뛴다. 모든 그룹을 강제로 다시 생성해야 할 때만 `--force`를 추가한다.

### 2.4 원본 drift 및 무결성 검증

```powershell
.\.venv\Scripts\python.exe Tools\BuildUtilityPipeGroupVectors.py validate `
  --config Tools\tools.settings.json `
  --scope-mode active `
  --min-members 2
```

### 2.5 현재 상태 조회

```powershell
.\.venv\Scripts\python.exe Tools\BuildUtilityPipeGroupVectors.py status `
  --config Tools\tools.settings.json `
  --scope-mode active
```

특정 revision을 지정하려면 `--scope-mode explicit --project-scope-key ... --model-revision-key ...`를 사용한다.

## 3. 전체 처리 흐름

```text
ACTIVE Scope 1개 확정
        ↓
TB_ROUTE_PATH + TB_ROUTE_FEATURE_VECTOR + TB_ROUTE_CONTEXT_VECTOR 조회
        ↓
TB_ROUTE_SEGMENTS에서 실제 상세 좌표/AABB 입력 조회
        ↓
Process + Equipment Instance + Utility Group + Utility 그룹화
        ↓
멤버 2개 미만 제외, GUID 오름차순 결정론적 정렬
        ↓
Context encoder version/config hash 호환 집합 선택
        ↓
Feature/Context 평균 → L2 정규화된 30D centroid
        ↓
Size/시작·종점/변위/간격/길이/Step/AABB 배치 통계
        ↓
Stable Group ID + Encoder Config Hash + Source Hash
        ↓
변경 그룹 BUILDING upsert → 멤버 교체 → READY 원자적 전환
        ↓
사라진 기존 그룹 STALE 처리 → 원본 재계산 validate
```

## 4. 주요 알고리즘

### 4.1 Stable Group ID

다음 identity를 key 정렬된 JSON으로 직렬화하고 SHA-256을 적용한다.

```text
Project Scope + Model Revision + Process
+ Equipment Instance + Utility Group + Utility
```

결과 형식은 `upg_<64자리 SHA-256>`이다. Build Run이나 현재 시각을 포함하지 않으므로 같은 그룹은 항상 같은 ID를 갖는다.

### 4.2 Feature/Context centroid

```text
rawCentroid[d] = 각 멤버 Vector[d]의 산술평균
centroid        = rawCentroid / L2Norm(rawCentroid)
```

Feature는 모든 멤버가 30D여야 READY가 된다. Context는 같은 scope/revision 및 같은 encoder version/config hash만 집계한다. 실제 데이터에서는 747개가 모두 동일 호환 계약으로 연결됐다. 선택한 Context 계약은 그룹의 `ENCODER_CONFIG_JSON.context_contract`에도 저장한다.

### 4.3 Arrangement 통계

`ARRANGEMENT_VECTOR_JSON`에는 이름이 명시된 다음 통계를 저장한다.

- 시작점/종점 X·Y·Z 평균과 표준편차
- 각 Route 시작→종점 변위의 평균과 표준편차
- 시작점 및 종점 pairwise 거리의 평균·표준편차·최소·최대
- Route 길이와 Step 수의 평균·표준편차
- Size별 멤버 수
- 상세 segment 좌표 기반 그룹 AABB 최소/최대/크기

멤버 배열 순서가 달라도 결과가 같도록 대칭 통계만 사용한다.

### 4.4 Source Hash

Source Hash에는 다음만 포함하며 실행 시각과 Build Run ID는 제외한다.

- 그룹 identity
- Group encoder config hash와 선택 Context 계약
- GUID 순으로 정렬한 멤버 목록
- Size, 좌표, Pattern, 길이, Step
- 원본 Feature/Context Vector와 provenance
- 상세 segment 좌표

따라서 검색 결과에 영향을 주는 입력이 바뀔 때만 그룹이 다시 생성된다.

### 4.5 원자적 저장

변경 그룹은 먼저 `BUILDING`으로 upsert하고 기존 멤버를 교체한다. 모든 멤버 insert가 성공한 뒤 같은 트랜잭션 안에서 `READY`로 전환한다. 중간 오류가 발생하면 전체 트랜잭션을 rollback하므로 부분 그룹이 검색에 노출되지 않는다.

## 5. 검증 항목

`validate`는 저장 테이블만 세는 데 그치지 않고 현재 원본을 다시 읽어 그룹을 재계산한다.

- 선언 멤버 수 = 실제 멤버 행 수 = 서로 다른 Route 수
- `MEMBER_GUIDS` 길이 = 멤버 수
- Feature/Context centroid 30D 파싱 및 L2 norm
- 현재 원본에서 계산한 Stable ID 집합 = READY ID 집합
- 현재 원본에서 계산한 Source Hash = 저장 Source Hash
- 누락 READY 그룹과 원본 drift 없음

단위 테스트는 입력 순서 불변성, stable ID, Source Hash 민감도, centroid 정규화, Context 계약 선택, Arrangement 순서 불변성을 검증한다. 단계 0/1 테스트를 합쳐 최종 15건이 통과했다.

## 6. 산출물

- `Tools/BuildUtilityPipeGroupVectors.py`: create-schema/build/validate/status CLI와 DB 저장
- `Tools/utility_pipe_group_encoder.py`: 순수 계산 encoder
- `Tools/tests/utility_pipe_group_encoder_tests.py`: 알고리즘 단위 테스트
- `data/output/utility_pipe_group_build_phase2.json`: 실제 build/validation 결과

## 7. 단계 3 검색 API 입력

단계 3은 다음 조건으로 바로 개발할 수 있다.

1. Query 그룹은 `GROUP_VECTOR_ID` 또는 ACTIVE scope의 `Equipment + Utility Group + Utility`로 결정한다.
2. 자기 그룹을 제외하고 `Utility Group + Utility + READY`를 필수 후보 조건으로 사용한다.
3. `FEATURE_CENTROID <=> query` HNSW로 1차 후보를 수집한다.
4. Process, Equipment Family, Size는 선택 필터/호환 점수로 적용한다.
5. 그룹 멤버 원본 Feature/Context를 읽어 Pair 점수 행렬을 만든다.
6. Hungarian matching으로 멤버를 1:1 최적 대응한다.
7. Coverage, MatchedAverage, Arrangement를 결합해 최종 GroupSimilarity와 계산식 진단을 반환한다.

단계 3 완료 기준은 자기 그룹 제외, 입력 순서 불변, Size 정책, Hungarian 최적 대응, 최종 점수식 golden test 및 ACTIVE DB smoke test 통과다.
