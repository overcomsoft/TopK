# Context Vector Phase 12 - Source Scope and Snapshot Provenance

## 1. 개발 목적

컨텍스트 벡터가 어떤 프로젝트/모델 리비전과 어떤 장애물 스냅샷을 기준으로 생성되었는지 추적할 수 있도록 소스 범위 계약과 provenance를 추가했다.

현재 DDW_AI_DB의 기존 데이터에는 경로 Feature, Route Path, BIM Obstacle을 정확히 연결하는 공통 프로젝트/리비전 키가 없다. 따라서 BAY, `MODEL_TEMPLATE_ID`, 장비명 등을 프로젝트 또는 리비전으로 임의 변환하지 않는다. 기존 데이터는 명시적인 전역 fallback으로 처리하고, 향후 upstream ETL이 세 소스에 동일한 공통 키를 공급할 때만 strict scope를 사용한다.

## 2. DB 변경

`Tools/sql/create_route_source_scope_columns.sql`은 다음 테이블에 nullable 컬럼을 추가한다.

- `TB_ROUTE_FEATURE_VECTOR.PROJECT_SCOPE_KEY`, `MODEL_REVISION_KEY`
- `TB_ROUTE_PATH.PROJECT_SCOPE_KEY`, `MODEL_REVISION_KEY`
- `TB_BIM_OBSTACLE.PROJECT_SCOPE_KEY`, `MODEL_REVISION_KEY`

`TB_ROUTE_CONTEXT_VECTOR`에는 다음 provenance 컬럼을 추가했다.

- `PROJECT_SCOPE_KEY`, `MODEL_REVISION_KEY`
- `SOURCE_SNAPSHOT_HASH`: 사용한 장애물 identity/type/AABB의 정렬된 SHA-256
- `SCOPE_RESOLUTION_STATUS`
  - `STRICT_COMMON_KEY`: 두 공통 키로 Feature와 Obstacle을 모두 제한
  - `GLOBAL_FALLBACK_NO_COMMON_KEY`: 공통 키가 없는 legacy 전역 공간 인덱스
- `SOURCE_OBSTACLE_COUNT`
- `SCOPE_DIAGNOSTIC_JSON`
- `BUILD_RUN_ID`: 한 번의 추출 실행을 식별하는 UUID

마이그레이션 실행:

```powershell
dotnet ContextRoutingABRunner/bin/Release/net8.0-windows/ContextRoutingABRunner.dll `
  --create-scope-schema --config Tools/tools.settings.json
```

모든 DDL은 `ADD COLUMN IF NOT EXISTS`와 `CREATE INDEX IF NOT EXISTS`를 사용한 additive migration이다.

## 3. 추출 계약

전역 legacy 추출:

```powershell
python Tools/ExtractObstacleContextVector.py --config Tools/tools.settings.json run-all
```

엄격 범위 추출:

```powershell
python Tools/ExtractObstacleContextVector.py --config Tools/tools.settings.json extract `
  --project-scope-key PROJECT_A `
  --model-revision-key REV_20260713
```

두 키는 반드시 함께 지정해야 한다. 하나만 지정하면 실행을 중단한다. strict 추출은 Feature와 Obstacle 양쪽에 같은 두 키를 적용한다.

스냅샷 해시는 DB 조회 순서와 무관하도록 장애물 키를 정렬한 후 계산한다. 장애물 종류, source identity, AABB가 바뀌면 해시도 바뀐다.

현재 `TB_ROUTE_CONTEXT_VECTOR`의 PK는 `ROUTE_PATH_GUID` 단일 컬럼이다. 따라서 동일 경로의 여러 리비전 벡터를 동시에 보존하는 이력 테이블이 아니라 현재 유효 결과 테이블이다. 여러 리비전 동시 보관이 필요해지면 PK를 `(PROJECT_SCOPE_KEY, MODEL_REVISION_KEY, ROUTE_PATH_GUID)`로 전환하는 별도 migration이 필요하다.

## 4. Top-K 검색 계약

`TopKSearchStandalone.SearchAsync`에 optional `projectScopeKey`, `modelRevisionKey`를 추가했다.

- 두 키가 없으면 Feature 후보는 기존 방식으로 조회하고, `GLOBAL_FALLBACK_NO_COMMON_KEY` 컨텍스트만 조인한다.
- 두 키가 있으면 Feature, query-side obstacle, saved context vector를 모두 같은 두 키로 제한하고 `STRICT_COMMON_KEY`만 조인한다.
- 두 키 중 하나만 있으면 `ArgumentException`을 발생시킨다.
- 기존 호출자는 optional 인자 때문에 호환성이 유지된다.

## 5. 2026-07-13 실제 DB 검증

- Feature Vector: 7,879
- Context Vector: 7,879 (coverage 100.0%)
- Encoder/version/dimension 일치: 7,879/7,879
- 범위 상태: `GLOBAL_FALLBACK_NO_COMMON_KEY`
- 프로젝트/리비전 값: 빈 값/빈 값
- 사용 장애물: 164,490
- 장애물 snapshot SHA-256: `de993fda82117d266c58a004b712943db354fda0d550a02b0c0d56b663b5febe`
- snapshot 종류: 1
- build run 종류: 1

소스 키 현황:

- `TB_ROUTE_FEATURE_VECTOR`: 두 키 모두 populated 0건
- `TB_ROUTE_PATH`: 두 키 모두 populated 0건
- `TB_BIM_OBSTACLE`: 두 키 모두 populated 0건

따라서 현재 strict scope 데이터는 만들지 않았고, 기존 결과를 감사 가능한 global fallback으로 재생성했다.

## 6. 검증 결과

- Python unit tests: 23/23 성공
- C# Release build: 오류 0
- 기존 NU1701 및 nullable 경고는 본 단계와 무관한 기존 경고
- 실제 DB migration 성공
- 실제 DB 전체 재추출 성공

## 7. 다음 개발 항목

1. Upstream ETL에서 세 소스 테이블에 동일한 immutable 프로젝트/리비전 키 공급
2. 특정 프로젝트/리비전으로 strict extraction smoke test
3. 리비전별 벡터 동시 보존이 필요하면 context table 복합 PK migration
4. A/B 로그에 context snapshot hash와 scope status를 복사하여 실험 재현성 강화
5. 운영 gate에서 strict scope 요청 시 fallback 결과가 섞이면 실패 처리
