# UtilityPipeGroup Top-K 단계 1 구현 결과

- 완료일: 2026-07-16
- 대상 DB: `DDW_AI_DB`
- 구현 범위: Schema, Vector 계약, migration 실행/검증
- 결과: 완료

## 1. 구현 결과

기존 개별 Route 및 Vector 테이블은 수정하지 않고 다음 두 테이블을 추가했다.

| 테이블 | 역할 | 단계 1 적용 후 행 수 |
|---|---|---:|
| `TB_ROUTE_UTILITY_GROUP_VECTOR` | 그룹 식별자, 집계 Vector, 배치 상태와 provenance 저장 | 0 |
| `TB_ROUTE_UTILITY_GROUP_MEMBER` | 그룹을 구성하는 개별 Route와 원본 특성 연결 | 0 |

신규 테이블이 비어 있는 것은 정상이다. 실제 106개 개발 대상 그룹과 747개 멤버의 적재는 단계 2 Vector Builder가 담당한다.

## 2. 확정된 데이터 계약

그룹 Query의 논리 식별 기준은 다음과 같다.

```text
Project Scope + Model Revision + Process
+ Equipment Instance + Utility Group + Utility
```

검색 후보의 필수 필터는 `Utility Group + Utility`다. 동일 Equipment를 필수 후보 필터로 사용하면 자기 그룹 제외 후 후보가 사라지므로, Equipment는 Query 그룹 식별과 자기 제외에 사용한다. `Process`, `Equipment Family`, `Size`는 검색 단계의 선택 필터 또는 호환성 점수로 사용한다.

| 필드 | 형식 | 용도 |
|---|---|---|
| `FEATURE_CENTROID` | `vector(30)`, NOT NULL | 1차 ANN 후보 수집 |
| `CONTEXT_CENTROID` | `vector(30)`, NULL 허용 | 후보 정밀 재정렬 전용 |
| `SIZE_SIGNATURE` | `jsonb` | 그룹 내 Size별 개수와 호환성 계산 |
| `MEMBER_GUIDS` | `jsonb array` | 원본 Route 구성 검증 |
| `ARRANGEMENT_VECTOR_JSON` | `jsonb object` | 멤버 간 상대 배치 특징 |
| `SOURCE_HASH` | `text` | 원본 변경 감지와 증분 재생성 |
| `STATUS` | `BUILDING/READY/FAILED/STALE` | 배치 공개 상태 |

Feature만 HNSW `vector_cosine_ops` 인덱스를 사용한다. Context는 장애물 Vector의 가용성과 품질을 확인하면서 정확 계산으로 재정렬하며 ANN 후보 수집에는 사용하지 않는다.

## 3. 무결성 및 운영 안전장치

- Source Scope Manifest에 대한 복합 외래키를 적용했다.
- 동일 scope/revision/process/equipment/utility group/utility 중복을 금지했다.
- 그룹 멤버 수는 2개 이상이어야 한다.
- `MEMBER_GUIDS` 배열 길이는 `MEMBER_COUNT`와 같아야 한다.
- Feature/Context coverage는 0~1 범위로 제한했다.
- AABB 최소값이 최대값보다 커지는 입력을 차단했다.
- 멤버 Route와 멤버 순서의 그룹 내 중복을 차단했다.
- 그룹 삭제 시 멤버만 연쇄 삭제하며 기존 원본 Route/Vector에는 영향을 주지 않는다.
- rollback 파일은 신규 두 테이블만 삭제하도록 제한했으며 자동 실행 기능은 제공하지 않는다.

## 4. 인덱스

명시적으로 생성한 보조 인덱스는 8개다.

| 인덱스 | 목적 |
|---|---|
| `IX_TRUGV_CANDIDATE_FILTER` | Utility Group + Utility 후보 범위 축소 |
| `IX_TRUGV_PROCESS_CANDIDATE` | Process 선택 필터 |
| `IX_TRUGV_EQUIPMENT_FAMILY` | Equipment Family 선택 필터 |
| `IX_TRUGV_SOURCE_HASH` | 증분 변경 판정 |
| `IX_TRUGV_FEATURE_CENTROID_HNSW` | Feature centroid ANN 검색 |
| `IX_TRUGV_SIZE_SIGNATURE_GIN` | Size JSON 조건 검색 |
| `IX_TRUGM_ROUTE_GUID` | 원본 Route에서 그룹 역조회 |
| `IX_TRUGM_GROUP_SIZE` | 그룹 멤버와 Size 조회 |

DB 검증에서 PK/UNIQUE 자동 인덱스를 포함해 총 12개가 확인됐다.

## 5. 실행 및 검증 결과

실행 명령:

```powershell
.\.venv\Scripts\python.exe Tools\MigrateUtilityPipeGroupSchema.py apply --config Tools\tools.settings.json
.\.venv\Scripts\python.exe Tools\MigrateUtilityPipeGroupSchema.py verify --config Tools\tools.settings.json
```

검증 결과:

| 항목 | 결과 |
|---|---|
| 최초 additive migration | 성공 |
| 동일 migration 재실행 | 성공 |
| 필수 컬럼과 타입 | 정상 |
| Feature/Context 차원 | 각각 30D |
| 필수 인덱스 | 정상 |
| 필수 PK/FK/UNIQUE/CHECK | 정상 |
| 검증 오류 | 0건 |
| 자동 테스트 | 9건 통과 |
| Python 문법 검사 | 통과 |

최초 실행 직후 검증기에서 PostgreSQL `pg_attribute`의 컬럼명을 `column_name`으로 잘못 조회한 오류가 발견됐다. 실제 시스템 컬럼인 `attname`으로 수정하고 회귀 테스트를 추가했다. Migration 트랜잭션은 검증 전에 정상 커밋되었고, 수정 후 별도 검증과 재실행 검증이 모두 통과했다.

## 6. 산출물

- `Tools/sql/create_route_utility_group_vector_tables.sql`: additive 생성 SQL
- `Tools/sql/drop_route_utility_group_vector_tables.sql`: 명시적 rollback SQL
- `Tools/MigrateUtilityPipeGroupSchema.py`: apply/verify CLI
- `Tools/contracts/utility_pipe_group_vector_contract.schema.json`: Python/C# 공용 JSON 계약
- `Tools/tests/utility_pipe_group_schema_tests.py`: migration/계약 회귀 테스트

## 7. 단계 2 입력 및 개발 범위

단계 2에서는 ACTIVE scope의 106개 그룹과 747개 멤버를 실제로 생성한다.

1. Route, Feature, Context, 상세 좌표를 한 번에 로드한다.
2. Equipment key를 정규화하고 `Equipment + Utility Group + Utility`로 묶는다.
3. 1개 멤버 그룹은 제외하고, 멤버를 결정적으로 정렬한다.
4. Feature/Context 30D centroid를 계산하고 L2 정규화한다.
5. Size signature, start/end centroid, AABB, arrangement 특징을 계산한다.
6. stable group ID, source hash, encoder config hash를 만든다.
7. `BUILDING` 상태로 원자적 upsert 후 검증이 성공한 그룹만 `READY`로 전환한다.
8. 원본이 바뀐 그룹만 다시 만들고 사라진 그룹은 `STALE` 처리한다.
9. build/validate/status CLI와 단위·DB 통합 테스트를 제공한다.

단계 2 완료 기준은 프로파일 대상 106개 그룹이 모두 READY이고, 747개 멤버가 중복 없이 연결되며, 재실행 시 불필요한 재생성이 발생하지 않는 것이다.
