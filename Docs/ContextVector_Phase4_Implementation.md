# Context Vector 4단계 개발 완료 기록

작성일: 2026-07-13  
대상: `TB_ROUTE_CONTEXT_VECTOR`, Python 인코더, C# Top-K 검색기

## 1. 목표와 결론

3단계의 가장 큰 제약은 Feature Vector 7,879건 중 경로/BAY 계보가 확인되는 827건만 Context Vector가 존재한다는 점이었다. 4단계에서는 모든 Feature Vector의 시작·종점 좌표를 직접 사용하고, 장애물은 문자열 BAY가 아닌 전역 공간 인덱스로 조회해 coverage를 10.5%에서 100%로 확장했다.

최종 인코더 계약은 `topkgen-v3`, `GLOBAL_SPATIAL_ALL_BAYS`, `vector(30)`이다.

## 2. 계보 조사 결과

- `TB_ROUTE_FEATURE_VECTOR`: 7,879개 GUID와 시작·종점 좌표 보유
- `TB_ROUTE_PATH`: 827개 GUID만 보유
- `VW_ROUTE_SEARCH`: 7,879개지만 Feature Vector 기반 view이므로 별도 경로 계보를 제공하지 않음
- `PROCESS_NAME -> BAY`: 일대일이 아님
  - `DIFF`: `METAL BAY`, `N/A`
  - `ETCH`: `CMP BAY`, `N/A`
- 장애물 BAY는 `BAY001`, `BAY002`, `DIFF-2 BAY` 등으로 경로 BAY보다 더 세분화되어 문자열 일치가 안전하지 않음

따라서 누락 행을 process 이름으로 BAY에 강제 매핑하는 방식은 채택하지 않았다.

## 3. 전역 공간 범위의 근거

현재 DB는 하나의 전역 좌표계이며 구조 장애물 164,490건 중 고유 기하는 164,445건이다. 서로 다른 BAY에 동일 AABB가 중복 배치된 사례는 0건이었다. 인코더가 모든 장애물을 매번 계산하는 것이 아니라 endpoint 1,000mm shell과 chord bounding box에 닿는 항목만 공간 인덱스로 조회하므로 계산 범위도 제한된다.

주의: 여러 프로젝트 또는 모델 revision이 같은 좌표에 적재될 수 있는 구조로 확장되면 전역 범위만으로는 충분하지 않다. 그때는 장애물과 Feature Vector 양쪽에 공통 `PROJECT_ID`/`MODEL_REVISION_ID`를 추가하고 범위 키로 사용해야 한다.

## 4. 구현 변경

- `context_vector_encoder.py`
  - version을 `topkgen-v3`로 변경
  - scope policy를 `global_spatial_all_bays`로 변경
- `ExtractObstacleContextVector.py`
  - BAY별 인덱스 대신 단일 전역 공간 인덱스 구성
  - 입력을 `TB_ROUTE_PATH` join에서 전체 `TB_ROUTE_FEATURE_VECTOR`로 변경
  - migration 결과를 확인하는 `status` 하위 명령 추가
- DDL
  - 기본 version/scope를 v3 계약으로 변경
- `TopKSearchStandalone.cs`
  - 후보 조인을 v3 version/hash/scope로 제한
  - 쿼리 시 장애물 조회의 BAY 필터 제거
  - 기본 context 가중치를 0.10으로 조정
  - query context head 진단값 추가
- `EvaluateContextTopK.py`
  - v3 scope 계약 적용
  - 기본 가중치와 보고서 판정 기준을 0.10으로 변경
- `BuildContextVectors.py`
  - `--limit`을 저장 모드에서 차단해 부분 결과가 전체 테이블을 대체하지 못하도록 보호

## 5. DB 재생성 결과

Dry-run에서 7,879/7,879건이 성공했고 모든 벡터 norm은 1.0이었다. 이후 schema migration과 전체 저장을 실행했다.

| 검증 항목 | 결과 |
|---|---:|
| Feature Vector | 7,879 |
| Context Vector | 7,879 |
| Coverage | 100% |
| `topkgen-v3` | 7,879 |
| `GLOBAL_SPATIAL_ALL_BAYS` | 7,879 |
| `vector(30)` | 7,879 |
| 설정 hash 종류 | 1 |

원본 경로·Feature·장애물 테이블은 변경하지 않았고 파생 테이블 `TB_ROUTE_CONTEXT_VECTOR`만 재생성했다.

## 6. 검색 품질 평가

경로가 있는 827건 중 leave-one-out 후보가 존재하는 825건을 평가했다.

| 지표 | Baseline | Context 0.10 | 변화 |
|---|---:|---:|---:|
| 양끝축 Top-1 | 18.303% | 21.212% | +2.91%p |
| 양끝축 Top-K 평균 | 12.917% | 14.469% | +1.55%p |
| 패턴 Top-1 | 12.242% | 14.303% | +2.06%p |
| 패턴 Top-K 평균 | 7.228% | 8.780% | +1.55%p |
| Feature cosine@K | 0.274846 | 0.248708 | -0.026138 |

0.05~0.25 sweep에서 양끝축 Top-1은 0.10이 가장 높았다. 따라서 초기 0.20 대신 0.10을 운영 기본값으로 채택했다. Feature 유사도 저하는 Context가 기하적으로 다른 후보를 더 적극적으로 올리는 trade-off이므로 후속 운영 지표로 계속 관찰한다.

## 7. 실검색 및 parity 검증

실제 C# 검색에서 ANN 후보 150건 모두 Context Vector가 조인되어 coverage 100%를 확인했다. 동일 쿼리의 Python 저장 벡터와 C# 실시간 벡터 앞부분 최대 차이는 약 `1.8E-08`로, DB vector 저장 정밀도 범위 안에서 일치했다.

## 8. 다음 개발 권고

1. 프로젝트/revision 공통 식별자 도입과 복합 scope 계약 설계
2. 검색 로그에 context coverage, fallback 비율, weight별 성공 지표 누적
3. 0.10과 baseline의 실제 라우팅 성공률 A/B 평가
4. Feature cosine 저하가 경로 생성 성공률에 미치는 영향 확인
5. 원본 장애물 변경 시 Context Vector 증분 재생성 전략 추가
