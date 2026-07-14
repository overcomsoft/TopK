# Context Vector Phase 15 - 운영 라이프사이클 통합 개발 결과

## 1. 완료 범위

이번 단계에서는 Context Vector를 단순 생성 데이터가 아니라 원본 모델 리비전에 종속된 운영 데이터로 관리하도록 통합했다.

- 원본 범위 Manifest 상태: `DRAFT -> BUILDING -> READY -> ACTIVE -> RETIRED/FAILED`
- 프로젝트별 ACTIVE 리비전은 최대 1개로 제한
- Route/Feature/BIM/Context의 project/revision 일치 검증
- ACTIVE 승격, 이전 리비전 rollback, retire, retention cleanup
- 모든 상태 변경 audit trail 기록
- 원본 스냅샷 해시와 ACTIVE 해시의 drift 감시
- 동일한 원본 스냅샷의 재빌드는 검증 후 자동 생략
- 빌드 중 PostgreSQL advisory lock과 `REPEATABLE READ` 적용
- strict scope를 검색과 A/B 라우팅의 기본 동작으로 전환
- global context fallback은 `--allow-global-fallback`을 지정한 경우에만 허용
- Route/Obstacle 다대다 project membership 확장 테이블 제공
- importer가 전달할 source artifact/import batch JSON 계약 제공

## 2. 주요 파일

- `Tools/RouteContextLifecycle.py`: build/validate/promote/rollback/retire/monitor/diff/cleanup 통합 CLI
- `Tools/ApplyRouteSourceScope.py`: 원본 DB 스냅샷 계산과 명시적 scope 적용
- `Tools/ExtractObstacleContextVector.py`: strict project/revision Context Vector 생성
- `Tools/sql/create_route_source_scope_manifest.sql`: manifest, audit, membership 스키마
- `Tools/contracts/route_source_scope_contract.schema.json`: 외부 importer 전달 계약
- `TopKSearchStandalone/TopKSearchStandalone.cs`: ACTIVE scope 자동 해석 및 strict 기본 검색
- `ContextRoutingABRunner/Program.cs`: ACTIVE scope 자동 해석 및 명시적 fallback

## 3. 운영 명령

### 3.1 신규 리비전 빌드

```powershell
python Tools/RouteContextLifecycle.py --config Tools/tools.settings.json build `
  --project-scope-key DB:DDW_AI_DB `
  --import-batch-id IMPORT-20260714-001 `
  --source-artifact model.glb `
  --source-artifact-hash <sha256> `
  --note "scheduled context rebuild"
```

리비전을 생략하면 전체 source snapshot hash를 사용해 `snapshot:<sha256>` 키를 만든다. 동일 스냅샷이 이미 READY 또는 ACTIVE이고 검증이 유효하면 `skipped_unchanged=true`를 반환한다.

### 3.2 검증과 승격

```powershell
python Tools/RouteContextLifecycle.py --config Tools/tools.settings.json validate `
  --project-scope-key DB:DDW_AI_DB --model-revision-key <revision>

python Tools/RouteContextLifecycle.py --config Tools/tools.settings.json promote `
  --project-scope-key DB:DDW_AI_DB --model-revision-key <revision>
```

검증 항목은 원본 테이블 건수, Context coverage, 중복, snapshot 단일성, strict scope, encoder version/config hash이다. 검증을 통과하지 않은 리비전은 ACTIVE로 승격할 수 없다.

### 3.3 rollback과 retire

```powershell
python Tools/RouteContextLifecycle.py --config Tools/tools.settings.json rollback `
  --project-scope-key DB:DDW_AI_DB --model-revision-key <previous-revision> `
  --note "rollback reason"

python Tools/RouteContextLifecycle.py --config Tools/tools.settings.json retire `
  --project-scope-key DB:DDW_AI_DB --model-revision-key <revision>
```

ACTIVE 리비전의 직접 retire는 기본적으로 거부한다. 비상 상황에서만 `--force`를 사용한다.

### 3.4 모니터링과 보존정책

```powershell
python Tools/RouteContextLifecycle.py --config Tools/tools.settings.json monitor --verify-source-hash

python Tools/RouteContextLifecycle.py --config Tools/tools.settings.json cleanup `
  --project-scope-key DB:DDW_AI_DB --keep 2
```

`cleanup`은 기본 dry-run이다. 실제 삭제는 `--execute`를 추가해야 한다. Route/Feature/BIM 원본 행이 아직 해당 리비전을 참조하면 삭제 대상에서 제외하고 `protected_revisions`로 보고한다.

## 4. 실제 DB 검증 결과

- Project: `DB:DDW_AI_DB`
- ACTIVE revision: `snapshot:7cd7f53b47e68623ad5f783a48246968aa1ba9d497e6d6f05cd1172a5840d131`
- Source snapshot hash: `7cd7f53b47e68623ad5f783a48246968aa1ba9d497e6d6f05cd1172a5840d131`
- Feature: 7,879 / 7,879
- Route: 827 / 827
- Structural obstacle: 164,490 / 164,490
- Context: 7,879, unique route 7,879, coverage 100%
- Strict scope/encoder/snapshot/duplicate 검사: 전부 통과
- Source hash drift: 없음
- 동일 스냅샷 build: 재생성 없이 정상 생략
- Retention dry-run: 삭제 대상 없음

## 5. 기존 21개 개발항목 대응

| 번호 | 항목 | 이번 구현 |
|---:|---|---|
| 1-2 | Route/BIM importer 연계 | DB 컬럼, manifest, import batch/artifact 계약과 NULL 감시 제공. 외부 importer는 이 저장소에 소스가 없어 계약 적용 지점까지 구현 |
| 3-5 | 통합 orchestration, lifecycle, strict 기본 | 완료 |
| 6 | A/B 표본/행렬 | campaign/matrix runner와 provenance gate 완료. 운영 표본 30건 이상 실행은 데이터 운영 작업 |
| 7 | backup/rollback | revision rollback/retire/복구 명령 완료. 물리 DB backup은 배포 인프라 책임 |
| 8 | 부분 project scope | Route/Obstacle membership 스키마 완료 |
| 9 | 증분 rebuild | 동일 snapshot 무변경 빌드 생략 완료. 변경 AABB 기반 부분 재계산은 별도 성능 최적화 과제 |
| 10 | retention/partition | 안전한 retention CLI 완료. 물리 partition은 실데이터 증가율을 측정한 뒤 결정 |
| 11 | 검색 성능 검증 | 기존 평가기/행렬 runner 유지, scope filter 통합 완료 |
| 12 | GUI scope | GUI 검색도 ACTIVE strict scope를 자동 사용. 수동 revision picker는 운영자 도구 확장 항목 |
| 13-15 | 보고서/모니터링/동시성 | snapshot provenance 분리, drift/orphan/count 감시, advisory lock 완료 |
| 16 | PostgreSQL 통합 테스트 | 실제 DDW_AI_DB migration/build/promote/monitor/skip/cleanup E2E 완료 |
| 17 | 서버/API 계약 | JSON schema와 검색 결과 provenance 계약 완료 |
| 18 | revision catalog/diff | active/diff/validate 명령 완료 |
| 19 | Context feature 확장 | encoder version/config hash 격리 기반 제공. 신규 feature 정의는 별도 모델 성능 실험 필요 |
| 20 | 보안/감사 | 상태 변경 audit, artifact/checksum 기록 완료. DB role 배정은 운영 계정 확정 필요 |
| 21 | 배포 자동화 | 단일 lifecycle CLI로 schema/build/validate/promote/smoke 호출 가능. CI 스케줄 연결은 배포 환경 작업 |

## 6. 검증

- Python: `24/24` unit tests 통과
- C#: `ContextRoutingABRunner` Release build 성공, 오류 0
- 기존 경고: `HelixToolkit.Wpf 2.25.0`의 `NU1701` 호환성 경고 4건
- `git diff --check`: 이번 기능과 무관한 기존 수정 파일의 trailing whitespace 때문에 전체 저장소 검사는 실패하며 해당 사용자 변경은 보존함

## 7. 배포 전 외부 확인 항목

저장소 내부 개발은 완료했지만 다음 항목은 외부 시스템 또는 운영 의사결정이 있어야 실행할 수 있다.

1. 실제 Route Path/BIM importer 저장소에서 `route_source_scope_contract.schema.json` 필드 연결
2. 운영 PostgreSQL 계정 이름 확정 후 apply/promote/retire 권한 분리
3. CI/CD 제품에서 lifecycle CLI 호출과 물리 DB backup 단계 연결
4. 운영 승인용 30건 이상 A/B campaign 실행 및 임계값 확정
5. 데이터 증가율 측정 후 physical partition과 변경 AABB 증분 계산의 투자 여부 결정
