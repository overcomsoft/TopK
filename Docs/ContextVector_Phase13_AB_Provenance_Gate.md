# Context Vector Phase 13 - A/B Provenance Gate

## 1. 목적

A/B 라우팅 결과가 동일한 Context Vector 장애물 snapshot, build run, encoder 계약과 scope에서 생성되었음을 로그로 증명하고, 서로 다른 provenance가 섞인 비교를 차단한다.

## 2. 데이터 흐름

1. `TopKSearchStandalone`이 `TB_ROUTE_CONTEXT_VECTOR` 후보와 함께 provenance 컬럼을 조회한다.
2. 한 검색의 context 후보들이 동일한 snapshot/build/scope/encoder tuple인지 검사한다.
3. `SearchMeta`와 `ContextSearchTrace`가 검사 결과를 전달한다.
4. Runner는 Context Arm의 provenance를 같은 요청의 Baseline Arm에도 복사한다.
5. 실행 Gate 통과 후 두 Arm 결과를 `TB_CONTEXT_ROUTING_AB_LOG`에 저장한다.
6. 분석기는 동일 `REQUEST_KEY + RUN_ID`의 두 Arm만 pairing하고 provenance 불일치 pair를 통계에서 제외한다.

## 3. 로그 컬럼

- `CONTEXT_SNAPSHOT_HASH`
- `CONTEXT_SCOPE_STATUS`
- `CONTEXT_BUILD_RUN_ID`
- `CONTEXT_PROJECT_SCOPE_KEY`
- `CONTEXT_MODEL_REVISION_KEY`
- `CONTEXT_ENCODER_VERSION`
- `CONTEXT_ENCODER_CONFIG_HASH`
- `CONTEXT_PROVENANCE_CONSISTENT`
- `CONTEXT_PROVENANCE_ISSUE`

기존 로그는 보존되며 컬럼은 `ADD COLUMN IF NOT EXISTS`로 추가된다.

## 4. 실행 Gate

기본 호환 모드:

```powershell
dotnet ContextRoutingABRunner/bin/Release/net8.0-windows/ContextRoutingABRunner.dll `
  --project-id 1 --task-limit 1 --cell-mm 100 --k 3 `
  --min-context-coverage 1 --config Tools/tools.settings.json --no-save
```

- `--min-context-coverage 0.0..1.0`: 요청별 최소 coverage
- context가 존재하면 provenance 누락 또는 tuple 혼합을 차단
- 현재 legacy 데이터의 `GLOBAL_FALLBACK_NO_COMMON_KEY`는 허용

Strict 모드:

```powershell
dotnet ContextRoutingABRunner/bin/Release/net8.0-windows/ContextRoutingABRunner.dll `
  --project-id 1 --task-limit 1 --cell-mm 100 --k 3 `
  --require-strict-context-scope --model-revision-key REV_20260714 `
  --config Tools/tools.settings.json --no-save
```

Strict 모드는 다음을 모두 요구한다.

- context coverage 100%
- `SCOPE_RESOLUTION_STATUS = STRICT_COMMON_KEY`
- context의 project scope가 선택 프로젝트 `GroupId`와 일치
- context의 model revision이 요청 revision과 일치
- 모든 요청이 하나의 provenance manifest 사용

## 5. 분석 계약

분석기는 서로 다른 실행의 Baseline/Context를 조합하지 않는다. 한 `REQUEST_KEY`에 대해 두 Arm이 모두 존재하는 최신 `RUN_ID`만 선택한다.

특정 snapshot 분석:

```powershell
python Tools/AnalyzeContextRoutingAB.py --config Tools/tools.settings.json report `
  --experiment-id context-v3-weight-010 `
  --snapshot-hash <SHA256>
```

누락, 혼합 또는 불일치 provenance가 발견되면 `BLOCK_PROVENANCE_MISMATCH`이며 해당 pair는 품질 통계에서 제외된다.

## 6. 2026-07-14 검증 결과

- DB additive migration: 성공
- C# Release build: 오류 0
- Python unit tests: 24/24 성공
- global fallback dry-run: coverage 100%, manifest 1, Gate 통과
- strict `TEST_REV` dry-run: source data 없음, coverage 0%, Gate 차단
- 실제 저장 run: `8e9e2e26-3567-4b2e-bae5-3c81bcff8e0b`
- Baseline/Context: 모두 성공, 길이 6,800mm, bend 2, collision 0
- 두 Arm snapshot: `de993fda82117d266c58a004b712943db354fda0d550a02b0c0d56b663b5febe`
- 두 Arm context build: `38215df7-c379-4065-ba85-55be77f9c61c`
- 두 Arm scope: `GLOBAL_FALLBACK_NO_COMMON_KEY`
- 저장 provenance inconsistent: 0
- snapshot-filtered 분석: pair 1, mismatched pair 0, provenance ready true

## 7. 다음 단계

Upstream ETL에서 `TB_ROUTE_FEATURE_VECTOR`, `TB_ROUTE_PATH`, `TB_BIM_OBSTACLE`에 동일한 immutable `PROJECT_SCOPE_KEY`와 `MODEL_REVISION_KEY`를 공급하고 실제 strict scope vector를 생성한다.
