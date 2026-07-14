# Context Vector 3단계 개발 완료 기록

> 이 문서는 v2/BAY 기반의 당시 평가 기록이다. coverage 문제는 4단계 `topkgen-v3` 전역 공간 인덱스로 해결되었고, 운영 기본 가중치는 0.20에서 0.10으로 변경되었다. 현재 상태는 `ContextVector_Phase4_Implementation.md`를 참조한다.

작성일: 2026-07-13  
대상: `TopKSearchStandalone`, `Tools/EvaluateContextTopK.py`

## 1. 개발 목적

저장된 30D 장애물 Context Vector가 실제 Top-K 재정렬에서 유효한지 자기참조를 제외한
leave-one-out 방식으로 검증하고, 부분 색인 상태에서도 검색 결과가 왜곡되지 않도록 검색기를
보완했다.

## 2. 검색기 보완

전체 `TB_ROUTE_FEATURE_VECTOR`는 7,879건이지만 호환되는 Context Vector는 827건이다.
기존 구현은 Context가 없는 후보의 `ctxScore`를 0으로 두면서 기존 점수 가중치도 0.8배로
축소했다. 이 방식은 실제 환경 불일치와 단순 미색인을 구분하지 못해 coverage bias를 만든다.

수정 정책:

```text
candidate context 있음  = 0.40*position + 0.24*pattern + 0.16*feature + 0.20*context
candidate context 없음  = 0.50*position + 0.30*pattern + 0.20*feature (baseline fallback)
```

`SearchMeta`에는 다음 진단값을 추가했다.

- `ContextCandidates`
- `ContextCoverage`

CLI human/JSON 출력에서도 후보 Context 커버리지를 확인할 수 있다.

## 3. 평가 도구

`Tools/EvaluateContextTopK.py`를 추가했다.

평가 절차:

1. C#과 동일한 endpoint-only 30D query feature 생성
2. 동일 공정·장비·유틸리티그룹·유틸리티·사이즈 후보군 구성
3. feature cosine 기준 ANN 후보 150건 추출
4. query GUID를 제거하여 자기검색 배제
5. baseline과 Context 재정렬 Top-5 비교
6. Context 가중치 0.05, 0.10, 0.15, 0.20, 0.25 sweep

평가 지표:

- 실제 저장 feature에서 추출한 시작 이탈축 일치
- 종점 접근축 일치
- 시작·종점 양끝축 동시 일치
- 방향 패턴 일치
- Feature cosine 유지율
- Context cosine
- Top-K 결과 overlap

## 4. 실제 DB 평가 결과

운영 전체 후보 825개 query에서 Context 가중치 0.20 적용 결과:

| 지표 | Baseline | Context | 변화 |
|---|---:|---:|---:|
| 양끝축 Top-1 일치 | 18.3% | 26.5% | +8.2%p |
| 양끝축 Top-K 평균 일치 | 12.9% | 20.3% | +7.4%p |
| 방향 패턴 Top-1 일치 | 12.2% | 22.3% | +10.1%p |
| 방향 패턴 Top-K 평균 일치 | 7.2% | 15.5% | +8.3%p |
| Feature cosine@K | 0.2748 | 0.2458 | -0.0290 |
| Context cosine@K | - | 0.8470 | - |

Context 색인 후보가 5건 이상인 동일 후보군 144개 query의 공정 비교:

| 지표 | Baseline | Context | 변화 |
|---|---:|---:|---:|
| 양끝축 Top-1 일치 | 58.3% | 77.1% | +18.8%p |
| 방향 패턴 Top-1 일치 | 60.4% | 75.7% | +15.3%p |

운영 양끝축 Top-1 일치가 가중치 0.20에서 가장 높아 기존 가중치 0.20을 유지한다.

## 5. 실제 C# 검색 검증

Context가 저장된 CLEAN BAY 프리셋으로 실제 검색을 실행했다.

- 검색 시간: 약 733ms
- ANN 후보: 150건
- 해당 후보군의 Context 후보: 0건
- fallback 결과: 오류 없이 baseline 점수로 검색 완료

이는 검색기 보완이 정상 작동한다는 증거이면서 현재 Context 커버리지의 한계도 보여준다.

## 6. 남은 핵심 문제

전체 Feature 대비 Context 커버리지는 10.5%이고, 실제 ANN 후보군 평균 커버리지는 7.8%다.
`TB_ROUTE_PATH`가 있는 827개 경로만 BAY를 신뢰성 있게 알 수 있기 때문이다.

`PROCESS_NAME -> BAY` 변환은 완전한 일대일 관계가 아니다.

- `DIFF`: `METAL BAY`와 `N/A`가 함께 존재
- `ETCH`: `CMP BAY`와 `N/A`가 함께 존재

따라서 Feature-only 7,052건에 PROCESS_NAME만으로 BAY를 추정해 Context를 생성하는 것은
교차 공간 오염 위험이 있어 적용하지 않았다. 다음 단계에서는 Feature 생성 시 원본 BAY와
source route lineage를 `TB_ROUTE_FEATURE_VECTOR`에 함께 저장하거나 별도 매핑 테이블로
보존해야 한다.

## 7. 산출물

- `Tools/EvaluateContextTopK.py`: 재현 가능한 전체 평가 도구
- `Tools/tests/context_topk_evaluator_tests.py`: query vector, fallback, 가중치, 축 판정 테스트
- `Docs/ContextVector_Phase3_TopK_Evaluation.md`: 자동 생성 평가 보고서
- `data/output/context_topk_phase3_evaluation.json`: 전체 수치 원자료
