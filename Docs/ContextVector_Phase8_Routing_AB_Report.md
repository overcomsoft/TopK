# Context Vector 실제 라우팅 A/B 보고서

실험: `context-v3-weight-010`

## Arm별 결과

| Arm | 요청 | 성공률 | 성공 경로 평균 길이(mm) | 평균 bend | 평균 시간(ms) | 평균 확장 노드 | Context coverage | fallback |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BASELINE_TOPK | 30 | 63.3% | 13,094.74 | 5.16 | 451.23 | 550,455.13 | 0.0% | 0 |
| CONTEXT_V3 | 30 | 63.3% | 13,094.74 | 5.16 | 441.96 | 550,277.17 | 100.0% | 0 |

## 동일 요청 페어 비교

- 페어 수: 30
- 양쪽 성공: 19
- Context만 성공: 0
- Baseline만 성공: 0
- 양쪽 실패: 11
- Context 성공 순증: +0
- 길이 변화(Context-Baseline): 0.00mm
- bend 변화(Context-Baseline): 0.00
- 처리시간 변화(Context-Baseline): -21.76ms
- 확장 노드 변화(Context-Baseline): -281.00
- Top-K 순위/구성 변경 페어: 16 (53.3%)
- 평균 Top-K Jaccard overlap: 0.7633

판정 준비: **YES**
자동 판정: **NO_OBSERVED_ROUTE_QUALITY_EFFECT**

Minimum sample reached; apply approved production thresholds before promotion.
