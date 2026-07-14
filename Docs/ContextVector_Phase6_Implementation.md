# Context Vector 6단계 실제 라우팅 A/B 구현 기록

작성일: 2026-07-13

## 1. 목표와 구현 방식

5단계까지의 평가는 기존 경로 축·패턴을 정답 proxy로 사용했다. 6단계에서는 `AutoRouteFinder`가 실제 `Routing3DEngine`을 실행한 결과를 Context 사용 여부와 함께 저장해 성공률, 경로 길이, bend 수, 탐색 노드와 처리시간을 비교할 수 있게 했다.

현재 엔진은 한 번의 batch에서 하나의 corridor 집합만 사용할 수 있다. 동일 실행 안에서 엔진을 두 번 돌리는 완전 동시 shadow 방식은 UI 응답시간과 다중 배관 상호작용을 두 배로 만들기 때문에 적용하지 않았다. 대신 같은 입력을 안정적인 `REQUEST_KEY`로 묶고, UI 체크박스로 baseline/context arm을 각각 실행하면 최신 결과를 자동 페어링하는 지속형 실제 라우팅 A/B 방식을 적용했다.

## 2. UI와 Arm

`AutoRouteFinder` 상단에 `Context v3 A/B arm` 체크박스를 추가했다.

| Top-K | Context 체크 | 저장 ARM | 적용 계약 |
|:---:|:---:|---|---|
| ON | OFF | `BASELINE_TOPK` | `baseline:0.50/0.30/0.20` |
| ON | ON | `CONTEXT_V3` | `context-v3:0.45/0.27/0.18/0.10` |
| OFF | 무관 | `NO_TOPK` | Top-K corridor 미사용 |

동일 프로젝트·작업을 Context OFF와 ON으로 각각 실행하면 장비, utility, 시작·종점, 직경으로 만든 SHA-256 `REQUEST_KEY`가 동일해져 한 쌍으로 분석된다.

## 3. 저장 데이터

관찰 전용 append-only 테이블 `TB_CONTEXT_ROUTING_AB_LOG`에 다음을 저장한다.

- 실험·실행·요청 식별자: `EXPERIMENT_ID`, `RUN_ID`, `REQUEST_KEY`, `ARM`
- 요청: 장비, utility group/utility, 시작·종점, 직경
- 검색: Top-K GUID 배열, 검색시간, Context coverage, fallback 수, 가중치 계약
- 실제 라우팅: 성공 여부, 실패 사유, 길이, bend 수, 확장 노드, 처리시간
- `COLLISION_COUNT`는 현재 Routing3DEngine 진행 결과가 개수를 제공하지 않아 NULL로 저장한다.

로그 저장 실패는 실제 라우팅 결과를 실패로 바꾸지 않으며 디버그 경고만 남긴다.

## 4. Migration과 분석 명령

Migration:

```powershell
python Tools/AnalyzeContextRoutingAB.py --config Tools/tools.settings.json create-schema
```

수집 현황:

```powershell
python Tools/AnalyzeContextRoutingAB.py --config Tools/tools.settings.json status
```

A/B 보고서:

```powershell
python Tools/AnalyzeContextRoutingAB.py `
  --config Tools/tools.settings.json report `
  --output-json data/output/context_routing_ab_report.json `
  --output-md Docs/ContextVector_Routing_AB_Report.md
```

분석기는 동일 요청의 각 arm에서 가장 최근 실행만 선택한다. 성공률과 실패 사유를 arm별 집계하고, 양쪽 성공 요청에서는 `Context - Baseline` 길이·bend·시간·확장 노드 차이를 계산한다.

## 5. 현재 상태

- DB migration 실행 완료
- 6단계 migration 직후 초기 로그: 0건. 이후 7단계 headless 종단 검증으로 2건/1페어가 수집됨
- Python A/B 페어링 테스트 통과
- AutoRouteFinder 빌드 성공
- 실제 판정 최소 표본: 동일 요청 30쌍

실제 로그는 AutoRouteFinder 또는 7단계 headless runner 실행 시 누적된다. migration 직후의 초기 빈 보고서는 `ContextVector_Phase6_Routing_AB_Initial.md`에 기록했다.

## 6. 다음 보완 항목

1. Context OFF/ON batch를 자동으로 순차 실행하는 야간 headless runner
2. 엔진 ABI에서 잔여 충돌 개수를 반환해 `COLLISION_COUNT` 채우기
3. 프로젝트/revision 식별자를 REQUEST_KEY와 로그 컬럼에 추가
4. 30쌍 이상 수집 후 성공률·길이·bend·시간의 운영 승격 기준 승인
