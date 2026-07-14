# Context Vector Phase 9~13 구현 결과

## 1. 구현 범위

Phase 8에서 Top-K 순위가 바뀌어도 최종 경로가 바뀌지 않은 원인을 기준으로 다음 항목을 구현했다.

1. Phase 9: corridor rank-cut 및 비용 계수 실험
2. Phase 10: `StartBlocked`/`NoPath` 실패 코호트 정리
3. Phase 11: 성공 경로의 정적 장애물 충돌 감사
4. Phase 12: 프로젝트 및 모델 revision 로그 scope
5. Phase 13: migration/status 및 실험 매트릭스 자동화

## 2. Phase 9 - rank-cut corridor

기존 native Routing3D corridor 입력은 셀 집합 하나와 `w_corridor` 하나만 받았다. 이번 단계에서 기존 API를 유지하면서 `r3d_set_ranked_corridor_cells` C ABI를 추가했다.

따라서 현재 ABI에서 의미 있게 검증 가능한 정책을 다음과 같이 구현했다.

| 정책 | 사용 후보 |
|---|---|
| `ranked` | Rank 1~K를 모두 사용하고 cell별 rank penalty 적용 |
| `rank1` | Rank 1 경로만 corridor로 사용 |
| `top2` | Rank 1~2 합집합 |
| `union` | 전체 Top-K 합집합(기존 동작) |

각 정책은 `--corridor-cost-factor 0.5/1/2`와 조합할 수 있다. baseline/context corridor의 전용 cell 수도 계산하여 `CORRIDOR_EXCLUSIVE_CELL_COUNT`에 기록한다. 셀 중복 시 가장 높은 rank를 보존하는 내부 맵을 사용한다.

`ranked` 기본 profile은 `0,0.5,0.75`다. 값은 corridor 외부 페널티의 잔여 비율이며 Rank1은 0%, Rank2는 50%, Rank3는 75%를 부담한다. corridor 밖은 100%다. 중복 cell은 가장 낮은 잔여 비율, 즉 가장 강한 rank를 사용한다. 기존 `r3d_set_corridor_cells`는 변경하지 않아 이전 호출자와 호환된다.

## 3. Phase 10 - 실패 코호트

### StartBlocked

선택 task의 시작/종점이 해당 owner equipment AABB 내부에 있고 owner 이름이 일치하면 그 equipment를 정적 장애물에서 제외한다. 이는 유효한 장비 PoC가 장비 내부에 있는 경우를 허용하기 위한 보정이다. 제외 개수는 `ENDPOINT_RELEASE_COUNT`에 기록하며 `--keep-owner-equipment`로 기존 동작을 재현할 수 있다.

### NoPath / 기존 실패 표본

campaign에 다음 옵션을 추가했다.

- `--exclude-reference-experiment`: 실패 코호트의 기준 실험
- `--exclude-fail-reasons StartBlocked,NoPath`: 제외할 실패 사유

따라서 정상 품질 표본과 난이도/입력오류 코호트를 섞지 않고 별도 실험으로 운영할 수 있다.

## 4. Phase 11 - 충돌 감사

라우팅 성공 경로의 각 cell 중심을 실제 scene의 non-pass-through 장애물 및 유지된 equipment AABB와 대조한다. 충돌 cell 수를 `COLLISION_COUNT`에 저장한다. 실패 경로는 경로가 없으므로 `NULL`이다.

이 값은 managed post-route audit이다. native `R3dResult`에는 collision count가 없으므로, ABI 원천 메트릭이 필요하면 후속으로 `R3dResult` ABI 버전 관리 또는 별도 `r3d_get_result_metrics` API가 필요하다.

## 5. Phase 12 - 프로젝트/revision scope

- `PROJECT_KEY`에 더해 `MODEL_REVISION_KEY`를 로그에 저장한다.
- request key 해시에 revision을 포함하여 서로 다른 revision의 동일 좌표 요청이 페어링되지 않게 했다.
- runner는 `--model-revision-key`를 받는다.

현재 원천 장애물/경로 테이블 사이에 공통 revision 식별자가 없으므로 revision은 실행자가 명시하는 snapshot key다. 원천 DB에 공통 revision 컬럼이 생기기 전까지 자동 추론하지 않는다.

## 6. Phase 13 - 운영 자동화

### DB migration/status

Python DB 드라이버 없이 runner에서 실행할 수 있다.

```powershell
dotnet ContextRoutingABRunner/bin/Debug/net8.0-windows/ContextRoutingABRunner.dll `
  --config Tools/tools.settings.json --create-schema --status
```

### 3 x 3 실험 매트릭스

`Tools/RunContextABMatrix.py`가 `ranked/rank1/union x 0.5/1/2`의 9개 실험 ID와 결과 경로를 분리한다. 기본은 계획만 생성하고 `--execute`를 지정할 때만 실제 라우팅을 수행한다.

campaign 실행에는 `Tools/requirements-context-ab.txt`의 PostgreSQL Python 드라이버가 필요하다.

## 7. 검증 결과

- Python 단위 테스트: 20개 통과
- C# runner 빌드: 오류 0
- migration: 기존 60개 로그 보존, 신규 컬럼 추가 완료
- 실제 1-pair smoke: baseline/context 모두 성공, 길이 6,800mm, bend 2
- smoke metadata: `rank1`, 비용 `0.5x`, corridor 1,865 cell, owner equipment 1개 release
- Routing3D native `attract`, `capi` 테스트 통과
- ranked ABI 4-pair smoke: 양쪽 4/4 성공, 충돌 0
- ranked corridor 차이: baseline 전용 766 cell, context 전용 1,407 cell
- 실제 bend 변화: task 2는 baseline 1/context 2, task 4는 baseline 12/context 9

기존 HelixToolkit 호환 및 nullable 경고는 남아 있으나 이번 변경에서 새 컴파일 오류는 발생하지 않았다.
