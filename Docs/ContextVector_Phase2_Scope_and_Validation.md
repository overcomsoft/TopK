# Context Vector 2단계: BAY 범위 격리 및 실데이터 검증

작성일: 2026-07-13  
인코더: `topkgen-v2`, `vector(30)`

## 1. 실제 DB 스키마 확인 결과

`TB_ROUTE_PATH`와 `TB_BIM_OBSTACLE`에 공통으로 존재하는 공간 범위 컬럼은 `BAY`뿐이다.

- `TB_ROUTE_PATH`: `BAY`, `EQUIPMENT_TAG` 등 보유
- `TB_BIM_OBSTACLE`: `BAY`, `MODEL_TEMPLATE_ID` 등 보유
- 공통 `PROJECT_ID`, `PROJECT_NAME`, `MODEL_REVISION` 없음

장애물의 `MODEL_TEMPLATE_ID`를 경로 범위로 사용하는 방법도 검토했지만 적합하지 않았다.
경로 827건 모두 세그먼트 상세에서 MODEL_TEMPLATE_ID가 확인되나, 한 경로가 1~14개의
서로 다른 MODEL_TEMPLATE_ID에 걸쳐 있다. 이는 단일 프로젝트/모델 revision 키로 사용할 수 없다.

따라서 현재 DB에서는 다음 정책을 적용한다.

```text
route context obstacles = route.BAY와 같은 장애물 + BAY가 비어 있는 공통 장애물
scope kind              = BAY_PLUS_UNASSIGNED
scope value             = normalized route.BAY
```

## 2. 실제 데이터 범위

- 대상 경로: 827건
- 구조 장애물(기둥·보): 164,490건
- BAY 미지정 공통 장애물: 37,400건
- 장애물 BAY scope: 9개
- 경로 BAY: CLEAN/METAL/PHOTO/CMP/IMP/N/A, 총 6개

경로 분포:

| BAY | 경로 수 |
|---|---:|
| METAL BAY | 288 |
| CMP BAY | 259 |
| CLEAN BAY | 159 |
| PHOTO BAY | 66 |
| IMP BAY | 29 |
| N/A | 26 |

## 3. 500/1,000mm shell 검증

DB를 변경하지 않는 dry-run으로 827건 전체를 계산했다.

| Endpoint | 500mm 내 장애물 없음 | 1,000mm 내 장애물 없음 | 최근접 표면거리 P50 | P95 |
|---|---:|---:|---:|---:|
| START | 172건, 20.8% | 132건, 16.0% | 149.0mm | 549.6mm |
| END | 464건, 56.1% | 200건, 24.2% | 355.0mm | 919.3mm |

판단:

- 500mm shell은 즉시 회피가 필요한 국부 장애물을 구분한다.
- 1,000mm shell은 특히 END에서 무장애 비율을 56.1%에서 24.2%로 낮춰 추가 변별력을 제공한다.
- 두 shell을 함께 유지하는 것이 타당하다.

## 4. 0벡터 보정

초기 28D dry-run에서 일부 완전 무장애·수평방향 없음 경로가 0벡터가 됐다. cosine
비교가 정의되지 않으므로 시작·종점 각각에 `empty_within_1000` 1차원을 추가했다.

최종 레이아웃:

```text
START 13D + END 13D + Tier3 4D = 30D
```

보정 후 전체 827건 결과:

```text
vector norm min = 1.000000000
vector norm max = 1.000000000
```

## 5. 저장 추적정보

`TB_ROUTE_CONTEXT_VECTOR`에 다음 컬럼을 추가했다.

- `SCOPE_KIND`
- `SCOPE_VALUE`
- `ENCODER_CONFIG_JSON`
- `ENCODER_CONFIG_HASH`

현재 config hash:

```text
e77100d40c74e3054427096b0feeb1369d5cfb610b51046ff80b925521ab6df4
```

C# 검색기는 버전, config hash, scope kind가 모두 일치하는 후보 벡터만 사용한다.

## 6. Python/C# 수치 동등성

실제 경로 `0194be4f-c660-4231-8bec-f71576df93ee`, `CLEAN BAY`를 동일 입력으로 사용했다.

```text
dimension    = 30
max abs diff = 2.7755575615628914E-17
```

허용오차 `1e-9`보다 충분히 작아 Python 일괄 색인기와 C# 신규 쿼리 인코더가
수치적으로 동일함을 확인했다.

## 7. DDL migration 검증

현재 DB의 기존 context 테이블을 대상으로 DDL을 트랜잭션 안에서 실행한 뒤 rollback했다.

- `CONTEXT_VECTOR`: `vector(30)` 변환 성공
- scope/config 컬럼 추가 성공
- 트랜잭션 rollback 성공
- 실제 DB 데이터 변경 없음

## 8. 검색 API 변경

`useObstacleContext=true`일 때 `bay`가 필수다.

```csharp
SearchAsync(..., useObstacleContext: true, bay: "CMP BAY")
```

CLI는 다음과 같이 사용한다.

```powershell
dotnet run --project TopKSearchStandalone -- `
  --preset-guid <GUID> --use-obstacle-context --bay "CMP BAY"
```

프리셋을 사용하면 `TB_ROUTE_PATH.BAY`가 자동으로 검색 옵션에 채워진다.

## 9. 실제 DB 적용 결과

2026-07-13 실제 `DDW_AI_DB`에 migration과 전체 적재를 실행했다.

- `TB_ROUTE_CONTEXT_VECTOR`: `vector(30)` 적용 완료
- 저장 건수: 827건
- `ENCODER_VERSION='topkgen-v2'`: 827건
- `SCOPE_KIND='BAY_PLUS_UNASSIGNED'`: 827건
- 설정 해시 종류: 1개
- HNSW 중복 레거시 인덱스 제거 완료

원본 `TB_ROUTE_PATH`, `TB_ROUTE_FEATURE_VECTOR`, `TB_BIM_OBSTACLE` 데이터는 변경하지 않았다.
