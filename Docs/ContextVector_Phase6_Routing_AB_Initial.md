# Context Vector 실제 라우팅 A/B 보고서

실험: `context-v3-weight-010`

## Arm별 결과

| Arm | 요청 | 성공률 | 성공 경로 평균 길이(mm) | 평균 bend | 평균 시간(ms) | 평균 확장 노드 | Context coverage | fallback |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BASELINE_TOPK | 0 | N/A | N/A | N/A | N/A | N/A | N/A | 0 |
| CONTEXT_V3 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | 0 |

## 동일 요청 페어 비교

- 페어 수: 0
- 양쪽 성공: 0
- Context만 성공: 0
- Baseline만 성공: 0
- 양쪽 실패: 0
- Context 성공 순증: +0
- 길이 변화(Context-Baseline): N/Amm
- bend 변화(Context-Baseline): N/A
- 처리시간 변화(Context-Baseline): N/Ams
- 확장 노드 변화(Context-Baseline): N/A

판정 준비: **NO**

Collect at least 30 paired requests; production thresholds should be agreed before promotion.
