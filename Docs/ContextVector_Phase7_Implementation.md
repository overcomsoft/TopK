# Context Vector 7단계 Headless A/B Runner 구현 기록

작성일: 2026-07-13

## 1. 목표

6단계에서는 사용자가 AutoRouteFinder 체크박스를 바꿔 동일 작업을 두 번 실행해야 했다. 7단계에서는 UI 없이 동일 장면과 작업을 `BASELINE_TOPK`, `CONTEXT_V3` 순서로 자동 실행하고 한 `RUN_ID`로 실제 라우팅 A/B 로그를 저장하는 `ContextRoutingABRunner`를 추가했다.

## 2. 비교 조건

두 arm은 다음 조건을 공유한다.

- 같은 프로젝트, 작업 목록, 시작·종점과 직경
- 같은 장애물·장비와 grid
- 같은 Routing3DEngine 파라미터
- 같은 Top-K 개수와 corridor 비용
- 서로 다른 항목은 Top-K 재정렬에서 Context 사용 여부뿐

UI Feature Profile의 rack/spine/face는 runner에서 제외했다. Context 효과를 다른 학습 특징과 섞지 않고 분리하기 위한 조건이다.

## 3. 안전장치

- 기본 동작은 read-only dry-run이며 Top-K 검색만 수행한다.
- 실제 엔진 실행은 `--execute`를 명시해야 한다.
- `--no-save`로 실제 라우팅은 수행하되 DB 로그 저장을 막을 수 있다.
- 기본 작업 수는 1개다.
- grid가 기본 2억 5천만 cell을 넘으면 실행을 차단한다.
- 프로젝트 `GROUP_ID`를 `PROJECT_KEY`와 요청 해시에 포함해 프로젝트 간 오페어링을 막는다.

## 4. 실행 방법

프로젝트 목록:

```powershell
dotnet ContextRoutingABRunner/bin/Debug/net8.0-windows/ContextRoutingABRunner.dll `
  --config Tools/tools.settings.json --list-projects
```

Dry-run:

```powershell
dotnet ContextRoutingABRunner/bin/Debug/net8.0-windows/ContextRoutingABRunner.dll `
  --config Tools/tools.settings.json `
  --project-id 1 --task-limit 1 --cell-mm 100
```

실제 두 arm 실행 및 로그 저장:

```powershell
dotnet ContextRoutingABRunner/bin/Debug/net8.0-windows/ContextRoutingABRunner.dll `
  --config Tools/tools.settings.json `
  --project-id 1 --task-limit 1 --cell-mm 100 --execute
```

## 5. 최초 실측 결과

대상: `WTNHJ02 / BAY004 / CLEAN`, 첫 작업 1개, cell 100mm

| 지표 | Baseline | Context v3 | Context-Baseline |
|---|---:|---:|---:|
| 성공 | 성공 | 성공 | 동일 |
| 경로 길이 | 6,600mm | 6,600mm | 0mm |
| bend | 4 | 4 | 0 |
| 확장 노드 | 1,762 | 1,762 | 0 |
| 처리시간 | 65.9ms | 52.4ms | -13.6ms |
| Context coverage | 0% | 100% | +100%p |
| fallback | 0 | 0 | 0 |

DB에는 2개 로그, 1개 요청 페어, 1개 run이 저장됐다. 이 결과는 runner와 로깅 경로의 종단 검증이며 표본 1쌍이므로 가중치 품질 결론으로 사용하지 않는다.

## 6. 다음 단계

1. 여러 프로젝트에서 최소 30쌍을 균형 있게 수집 — 8단계에서 완료
2. 작업 선택을 첫 N개가 아닌 utility/거리/난이도 층화 표본으로 변경
3. arm 실행 순서를 교차하거나 반복해 warm-cache 시간 편향 제거
4. timeout·취소·중간 checkpoint 추가
5. 네이티브 엔진 충돌 개수 반환 및 로그 채우기
