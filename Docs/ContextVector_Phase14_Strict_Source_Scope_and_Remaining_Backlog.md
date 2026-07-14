# Context Vector Phase 14 - Strict Source Scope and Remaining Backlog

## 1. 이번 단계 완료 범위

### 1.1 명시적 전체 모델 scope

현재 DB에는 BAY나 장비 그룹보다 상위인 신뢰 가능한 business project key가 없다. 장애물은 여러 공간 그룹 AABB와 겹칠 수 있으므로 `TAG_GROUP_ID`, BAY, `MODEL_TEMPLATE_ID`를 프로젝트/리비전으로 추정하지 않았다.

대신 현재 DB 전체를 하나의 source model snapshot으로 명시했다.

- Project scope: `DB:DDW_AI_DB`
- Model revision: `snapshot:7cd7f53b47e68623ad5f783a48246968aa1ba9d497e6d6f05cd1172a5840d131`
- Combined source snapshot: `7cd7f53b47e68623ad5f783a48246968aa1ba9d497e6d6f05cd1172a5840d131`

Combined hash는 Feature, Route Path, Context에 사용되는 구조 장애물의 정렬된 내용을 함께 반영한다. Context Vector의 `SOURCE_SNAPSHOT_HASH=de993f...`는 장애물 subset만 나타내므로 두 해시는 목적이 다르다.

### 1.2 source scope 적용 결과

- `TB_ROUTE_FEATURE_VECTOR`: 7,879/7,879 scoped
- `TB_ROUTE_PATH`: 827/827 scoped
- `TB_BIM_OBSTACLE`: 303,725/303,725 scoped
- snapshot hash에 포함된 구조 장애물: 164,490
- `TB_ROUTE_SOURCE_SCOPE_MANIFEST`: manifest 1건 저장

`Tools/ApplyRouteSourceScope.py`는 다음 안전장치를 제공한다.

- `plan`: read-only snapshot 및 대상 행 수 확인
- `apply`: `--confirm-full-database-scope` 없이는 변경 거부
- `REPEATABLE READ`: hash 계산과 scope update가 하나의 일관된 DB snapshot을 사용
- `status`: 세 source coverage 및 manifest 출력
- BAY/장비명 기반 추정 없음

### 1.3 다중 revision Context Vector

`TB_ROUTE_CONTEXT_VECTOR` PK를 다음 복합 키로 변경했다.

```text
(PROJECT_SCOPE_KEY, MODEL_REVISION_KEY, ROUTE_PATH_GUID)
```

따라서 다음 두 variant가 동시에 보존된다.

- Legacy global fallback: 7,879건
- Strict `DB:DDW_AI_DB + snapshot:7cd...`: 7,879건
- 총 variants: 15,758
- 고유 route: 7,879
- route coverage: 100%

### 1.4 writer 연계

- `Extract_Design_Pattern.py`: `TB_ROUTE_PATH`의 project/revision을 읽어 Feature upsert에 전달
- `BuildFeatureVectors.py`: vector rebuild 후 Route Path 기준으로 Feature scope 재동기화
- Route Path와 BIM Obstacle 원천 writer는 이 저장소에 없으므로 full-database scope adapter가 현재 연결 지점이다.

### 1.5 strict routing 검증

실행 옵션:

```powershell
dotnet ContextRoutingABRunner/bin/Release/net8.0-windows/ContextRoutingABRunner.dll `
  --project-id 1 --task-limit 1 --cell-mm 100 --k 3 `
  --require-strict-context-scope `
  --project-scope-key DB:DDW_AI_DB `
  --model-revision-key snapshot:7cd7f53b47e68623ad5f783a48246968aa1ba9d497e6d6f05cd1172a5840d131 `
  --execute --config Tools/tools.settings.json
```

결과:

- Context coverage: 100%
- Scope: `STRICT_COMMON_KEY`
- Manifest: 1
- Baseline/Context 모두 성공
- 길이: 6,800mm / 6,800mm
- bend: 2 / 2
- collision: 0 / 0
- A/B run: `26d9b4cd-937e-40e5-b233-2bbf17e98dec`
- provenance inconsistent: 0

## 2. 운영 명령

계획:

```powershell
python Tools/ApplyRouteSourceScope.py --config Tools/tools.settings.json plan `
  --project-scope-key DB:DDW_AI_DB
```

적용:

```powershell
python Tools/ApplyRouteSourceScope.py --config Tools/tools.settings.json apply `
  --project-scope-key DB:DDW_AI_DB --confirm-full-database-scope
```

Strict Context 생성:

```powershell
python Tools/ExtractObstacleContextVector.py --config Tools/tools.settings.json run-all `
  --project-scope-key DB:DDW_AI_DB `
  --model-revision-key snapshot:7cd7f53b47e68623ad5f783a48246968aa1ba9d497e6d6f05cd1172a5840d131
```

## 3. 남은 개발항목 전체

### P0 - 운영 전 반드시 필요

1. **외부 Route Path importer 연계**
   - `TB_ROUTE_PATH`를 쓰는 원천 프로그램에 `PROJECT_SCOPE_KEY`, `MODEL_REVISION_KEY` 필수 입력 추가
   - import batch ID와 source file checksum 기록
   - NULL scope 신규 행을 운영 환경에서 차단

2. **외부 BIM Obstacle importer 연계**
   - BIM/GLB/Revit import 시작 시 project/revision을 명시
   - 동일 import batch의 모든 obstacle에 같은 scope 전달
   - 장애물 변경 후 이전 revision을 덮어쓰지 않고 새 revision 생성

3. **통합 import orchestration**
   - `source import -> scope manifest -> feature build -> context build -> validation -> ACTIVE` 순서 자동화
   - 중간 실패 시 revision을 ACTIVE로 승격하지 않음
   - 재실행 가능한 idempotent pipeline 제공

4. **Manifest lifecycle**
   - `DRAFT`, `BUILDING`, `READY`, `ACTIVE`, `RETIRED`, `FAILED` 상태 추가
   - READY 이후 source mutation 감지 시 무효화
   - ACTIVE revision을 검색 기본값으로 조회하는 API 추가

5. **운영 strict 기본 전환**
   - 현재 optional인 `--require-strict-context-scope`를 운영 기본값으로 전환
   - global fallback은 진단/비상용 명시 옵션에서만 허용
   - GUI와 서버 API에서도 같은 Gate 적용

6. **실제 A/B 검증 표본 확보**
   - 최소 30 paired requests가 아니라 공정/유틸리티/거리/난이도별 층화 30건 이상 권장
   - ranked/rank1/union 및 cost factor matrix 실행
   - success, length, bend, collision, expanded nodes, 시간 기준 승인 threshold 확정

7. **백업과 rollback 절차**
   - scope 적용 전 DB backup 또는 대상 컬럼 snapshot
   - 잘못된 manifest 비활성화 및 이전 ACTIVE revision 복귀 명령
   - Context revision 삭제/복원 운영 문서

### P1 - 운영 안정화

8. **부분 프로젝트 scope 모델**
   - 실제 업무 프로젝트가 전체 DB보다 작은 단위라면 장애물 다대다 membership 테이블 도입
   - 하나의 장애물이 여러 project scope에 속할 수 있도록 단일 obstacle 컬럼 의존 제거
   - 공간 AABB만으로 자동 소유권을 결정하지 않고 승인된 membership import 사용

9. **증분 rebuild**
   - source checksum 변경 route/obstacle만 재계산
   - 장애물 변경 영향 AABB와 겹치는 route만 invalidation
   - 현재 7,879건 전체 재생성 방식의 운영시간 단축

10. **Context 테이블 retention/partition**
    - revision별 보존기간과 최대 개수 정의
    - project/revision 기준 partition 또는 cleanup job
    - HNSW index 크기와 write amplification 모니터링

11. **검색 성능 검증**
    - scope filter + HNSW 실행계획 확인
    - 데이터 증가 시 partial/partitioned vector index 검토
    - p50/p95/p99 Top-K latency 기준 설정

12. **GUI scope 선택 기능**
    - ACTIVE project/revision 표시 및 선택
    - strict/fallback 상태, snapshot short hash, coverage 표시
    - provenance Gate 실패 이유를 사용자 메시지로 노출

13. **분석 보고서 개선**
    - snapshot/revision별 보고서 자동 분리
    - legacy provenance 없는 70건과 strict 신규 로그를 동일 통계에서 제외
    - manifest, encoder, corridor profile을 보고서 상단에 고정 표시

14. **데이터 품질 모니터링**
    - source scope NULL 행, orphan Feature, orphan Context, mixed revision 경보
    - manifest count와 실제 table count 불일치 감지
    - source hash drift 정기 검사

15. **동시성/격리 테스트**
    - scope hash 계산 중 source import가 발생하는 시나리오
    - Context build 중 revision ACTIVE 변경 시나리오
    - advisory lock 또는 pipeline lock 도입 검토

16. **PostgreSQL 통합 테스트 환경**
    - CI용 소형 fixture DB
    - migration from legacy single PK 테스트
    - global+strict coexistence, mixed provenance 차단, rollback 테스트

### P2 - 확장 및 장기 개선

17. **서버/API scope 계약**
    - 자동배관 요청 DTO에 project scope/revision 필수화
    - 결과에 사용 snapshot/build/encoder 반환
    - 클라이언트가 최신 revision을 묵시적으로 추측하지 않도록 계약 정의

18. **Revision catalog와 비교 도구**
    - 두 revision의 route/obstacle/context 차이 통계
    - 변경된 장애물과 영향을 받은 후보 경로 시각화
    - revision promotion 승인 화면

19. **Context feature 확장 평가**
    - 현재 기둥/보 외 벽, 덕트, 장비, 통과 가능 장애물 포함 여부 실험
    - 기존 30D encoder version과 호환되지 않으므로 새 encoder version으로 분리

20. **보안과 감사**
    - manifest apply/promote/retire 권한 분리
    - 실행 사용자, source artifact, checksum, 승인자 기록
    - 운영 DB scope 대량 변경 audit trail

21. **배포 자동화**
    - migration, scope plan, backup 확인, apply, build, smoke, promote를 배포 pipeline으로 구성
    - 각 단계 결과 artifact와 로그 보관

## 4. 권장 다음 순서

1. 외부 Route Path/BIM importer의 실제 저장소와 담당 writer 확인
2. importer에 project/revision/import batch 계약 추가
3. Manifest lifecycle과 ACTIVE revision API 구현
4. strict 모드로 층화 A/B campaign 실행
5. 승인 threshold 확정 후 strict 운영 기본 전환
