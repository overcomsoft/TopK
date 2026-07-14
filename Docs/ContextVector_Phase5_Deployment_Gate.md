# Context Vector 5단계 배포 품질 게이트

## 평가 방법

- 자기 경로를 후보에서 제외한 leave-one-out, Top-5
- ANN 후보 수: 150
- 운영 Context 가중치: 0.10
- Context가 없는 후보는 baseline 가중치로 fallback

## 데이터와 결과

- Feature Vector: 7,879건
- Context Vector: 7,879건
- 전체 coverage: 100.0%
- 운영 평가 쿼리: 825건
- 후보 Context coverage: 100.0%

| 지표 | Baseline | Context 0.10 | 변화 |
|---|---:|---:|---:|
| 양끝축 Top-1 | 18.3% | 21.2% | +2.91%p |
| 양끝축 Top-K 평균 | 12.9% | 14.5% | +1.55%p |
| 패턴 Top-1 | 12.2% | 14.3% | +2.06%p |
| 패턴 Top-K 평균 | 7.2% | 8.8% | +1.55%p |
| Feature cosine@K | 0.2748 | 0.2487 | -0.026138 |

## 가중치 Sweep

| Context 가중치 | 양끝축 Top-1 | 패턴 Top-1 | Feature cosine@K |
|---:|---:|---:|---:|
| 0.05 | 18.3% | 12.0% | 0.2671 |
| 0.10 | 21.2% | 14.3% | 0.2487 |
| 0.15 | 20.4% | 13.6% | 0.2406 |
| 0.20 | 19.0% | 12.4% | 0.2320 |
| 0.25 | 19.2% | 12.4% | 0.2268 |

## 배포 품질 게이트

최종 판정: **PASS**

| 검사 | 값 | 기준 | 결과 |
|---|---:|---:|:---:|
| `context_coverage` | 1 | >= 0.99 | PASS |
| `candidate_context_coverage` | 1 | >= 0.99 | PASS |
| `both_axes_top1_gain_pp` | 2.90909 | >= 1 | PASS |
| `pattern_top1_gain_pp` | 2.06061 | >= 1 | PASS |
| `feature_cosine_drop` | 0.0261376 | <= 0.04 | PASS |
| `operational_queries` | 825 | >= 100 | PASS |
| `recommended_weight_matches_current` | 0.1 | == 0.1 | PASS |

## 해석 제한

현재 게이트는 기존 경로의 축·패턴 일치도를 사용하는 proxy 품질 게이트다. 최종 자동 라우팅 성공률, 충돌 수, 길이 및 bend 수는 실제 실행 결과 레이블을 수집한 뒤 별도 A/B로 검증해야 한다.
