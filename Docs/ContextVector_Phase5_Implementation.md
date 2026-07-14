# Context Vector 5단계 개발 완료 기록

작성일: 2026-07-13

## 1. 목표

Context Vector를 재생성하거나 가중치를 변경한 뒤 사람이 지표를 일일이 비교하지 않아도 운영 반영 가능 여부를 자동 판정하도록 배포 품질 게이트를 추가했다. 실제 검색에서는 Context 누락에 따른 fallback 수와 적용된 가중치 계약을 명시적으로 노출했다.

## 2. 배포 품질 게이트

`Tools/EvaluateContextTopK.py`가 다음 조건을 검사한다.

| 검사 | 기본 기준 |
|---|---:|
| 전체 Context coverage | 99% 이상 |
| ANN 후보 Context coverage | 99% 이상 |
| 양끝축 Top-1 개선 | 1.0%p 이상 |
| 패턴 Top-1 개선 | 1.0%p 이상 |
| Feature cosine 감소 | 0.04 이하 |
| 운영 평가 쿼리 | 100건 이상 |
| 권장 가중치 | 현재 0.10과 일치 |

`--enforce-gate`를 지정하면 하나라도 실패할 때 종료 코드 2를 반환하므로 배치, CI 또는 VectorDB 재생성 파이프라인의 배포 차단 조건으로 사용할 수 있다. 임계값은 각각 CLI 옵션으로 조정할 수 있다.

## 3. 검색 진단 확장

`SearchMeta`와 JSON 출력에 다음을 추가했다.

- `context_fallback_candidates`: 호환 Context Vector가 없어 baseline으로 계산된 후보 수
- `rerank_weight_profile`: 실제 적용된 가중치 계약
- 기존 `context_candidates`, `context_coverage`와 함께 출력

Context 검색을 사용하지 않은 경우 계약은 `baseline:0.50/0.30/0.20`, 사용할 경우 `context-v3:0.45/0.27/0.18/0.10`이다.

## 4. 전체 평가 결과

7,879개 Feature/Context와 827개 기존 경로를 사용한 leave-one-out 평가에서 운영 가능한 쿼리는 825개였다.

| 검사 | 측정값 | 판정 |
|---|---:|:---:|
| 전체 coverage | 100% | PASS |
| 후보 coverage | 100% | PASS |
| 양끝축 Top-1 개선 | +2.91%p | PASS |
| 패턴 Top-1 개선 | +2.06%p | PASS |
| Feature cosine 감소 | 0.02614 | PASS |
| 운영 쿼리 | 825 | PASS |
| 권장 가중치 | 0.10 | PASS |

최종 배포 게이트 결과는 **PASS**다.

## 5. 실검색 검증

기존 경로 GUID `0194be4f-c660-4231-8bec-f71576df93ee`를 사용한 C# 검색 결과:

- 후보: 150건
- Context 후보: 150건
- Context coverage: 100%
- fallback: 0건
- 가중치 계약: `context-v3:0.45/0.27/0.18/0.10`
- 검색 시간: 약 515ms

## 6. 제한과 다음 단계

현재 평가는 기존 경로의 양끝축과 방향 패턴을 정답 proxy로 사용한다. 다음 단계에서는 실제 라우팅 실행 결과에서 성공 여부, 충돌 수, 경로 길이, bend 수, 처리 시간을 저장하고 baseline/context를 동일 요청에 적용하는 shadow A/B 로그를 구축해야 한다.
