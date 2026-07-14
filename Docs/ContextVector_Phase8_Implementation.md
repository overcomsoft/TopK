# Context Vector 8단계 층화 A/B 캠페인 완료 기록

작성일: 2026-07-13

## 1. 목표

Headless runner로 여러 프로젝트·utility·거리 난이도를 포함한 최소 30개 실제 라우팅 페어를 자동 수집하고, Context가 Top-K뿐 아니라 최종 Routing3DEngine 결과를 개선하는지 확인했다.

## 2. 캠페인 구현

`Tools/RunContextABCampaign.py`를 추가했다.

- 완료된 baseline/context 페어는 자동 제외하고 목표 총 페어 수까지 추가 선택
- 프로젝트를 1차로 round-robin 배분
- 프로젝트 내부에서 utility group, utility, 거리대별 round-robin 선택
- 거리대: Short `<5,000mm`, Medium `<15,000mm`, Long `>=15,000mm`
- 프로젝트별 최대 4개 작업 batch
- batch마다 baseline-first/context-first 순서를 교차
- 각 arm 종료 즉시 DB checkpoint 저장
- 기본 dry-run, `--execute`에서만 실제 엔진과 DB 쓰기 실행

프로젝트 AABB가 겹치는 데이터 특성을 확인해 `PROCESS_NAME -> project process` 일치를 우선 사용하고 공간 범위를 보조 조건으로 적용했다.

## 3. 실행 명령

계획만 검증:

```powershell
python Tools/RunContextABCampaign.py `
  --config Tools/tools.settings.json `
  --target-pairs 30 --batch-size 4 --cell-mm 100
```

실제 캠페인:

```powershell
python Tools/RunContextABCampaign.py `
  --config Tools/tools.settings.json `
  --target-pairs 30 --batch-size 4 --cell-mm 100 --execute
```

## 4. 표본 구성

- 기존 종단 검증 페어: 1개
- 신규 캠페인 페어: 29개
- 최종: 30개 페어, 로그 60건, run 9개
- 프로젝트: 8개 전체, 프로젝트별 3~4개
- 거리대: Short 16개, Medium 14개(최초 1개 포함), Long 0개
- 모든 Context arm coverage: 100%
- fallback: 0건

현재 TB_ROUTE_PATH 기반 후보에서는 15m 이상 Long 표본이 선택되지 않았다. Long 난이도는 향후 별도 기준 또는 다른 데이터셋으로 보충해야 한다.

## 5. 실제 라우팅 결과

| 지표 | Baseline | Context v3 | 변화 |
|---|---:|---:|---:|
| 요청 | 30 | 30 | - |
| 성공 | 19 | 19 | 0 |
| 성공률 | 63.33% | 63.33% | 0%p |
| 성공 경로 평균 길이 | 13,094.7mm | 13,094.7mm | 0mm |
| 성공 경로 평균 bend | 5.158 | 5.158 | 0 |
| 평균 처리시간(전체) | 451.2ms | 442.0ms | -9.3ms |
| 실패 사유 | StartBlocked 7, NoPath 4 | 동일 | 동일 |

페어 비교:

- 양쪽 성공: 19
- 양쪽 실패: 11
- Context만 성공: 0
- Baseline만 성공: 0
- 성공한 페어의 Context-Baseline 처리시간 평균: -21.8ms
- 성공한 페어의 확장 노드 평균 변화: -281

처리시간은 arm 순서를 교차했지만 30개 표본만으로 성능 우위로 확정하지 않는다.

## 6. 핵심 원인 분석

- Top-K 순위 또는 구성이 변경된 페어: 16/30, 53.3%
- 평균 Top-K Jaccard overlap: 0.7633
- Top-K가 변경된 16페어도 결과는 양쪽 성공 9, 양쪽 실패 7로 동일
- 성공 경로의 길이와 bend 변화: 모두 0

따라서 Context Vector 재정렬 자체는 작동한다. 그러나 현재 `CorridorCostMm = cellMm * 0.5`의 soft guidance와 다중 후보 corridor 합집합 적용에서는 변경된 후보가 최종 경로 품질에 영향을 주지 못했다.

자동 판정은 `NO_OBSERVED_ROUTE_QUALITY_EFFECT`다. 이는 Context Vector가 잘못됐다는 뜻이 아니라, 검색 결과를 라우팅 엔진에 전달하는 현재 소비 방식이 Context 차이를 희석한다는 뜻이다.

## 7. 다음 개발 권고

1. Top-K 전체 corridor 합집합 대신 rank별 가중 corridor 적용
2. Context와 baseline의 서로 다른 corridor cell 비율 기록
3. CorridorCost 0.5×/1×/2× cell의 별도 실험
4. StartBlocked 7건은 유효 시작 PoC 보정 후 A/B 표본에서 분리
5. NoPath 4건은 expansion/cell 설정을 동일하게 완화한 난이도 코호트로 재평가
