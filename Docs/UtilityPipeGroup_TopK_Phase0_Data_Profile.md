# UtilityPipeGroup Top-K 단계 0 데이터 프로파일링 결과

- 생성시각(UTC): 2026-07-16T06:07:05.914609+00:00
- Database: `DDW_AI_DB`
- Scope mode: `active`
- Scope status: `ACTIVE`
- Project: `DB:DDW_AI_DB`
- Revision: `snapshot:7cd7f53b47e68623ad5f783a48246968aa1ba9d497e6d6f05cd1172a5840d131`
- 최소 그룹 멤버 수: 2

## 1. 결론 요약

- 유효 Route 827건에서 전체 그룹 186개, 개발 대상 그룹 106개가 확인됐다.
- 동일 Size 그룹 비율은 69.8%이며 혼합 Size 그룹은 32개다.
- 개발 대상 멤버의 Feature/Context/상세경로 연결률은 각각 100.0% / 100.0% / 100.0%다.
- Utility 후보 버킷 중 그룹이 2개 이상인 버킷은 22개, 5개 이상은 9개다.
- 정확한 장비 키까지 Candidate 필터로 고정하면 한 snapshot에서 Query 그룹 자체만 남으므로, 자기 자신 제외 후 Top-K 후보가 없다.
- 장비는 Query 그룹 식별자로 유지하고 Candidate 후보 수집은 `Utility Group + Utility`를 필수키로 사용해야 한다.

## 2. Route 연결률

| 항목 | 건수/비율 |
|---|---:|
| 유효 Route | 827 |
| Feature 연결 | 827 / 100.0% |
| Context 연결 | 827 / 100.0% |
| 상세경로 GUID 연결 | 827 / 100.0% |

## 3. 그룹 및 Size 분포

| 항목 | 값 |
|---|---:|
| 전체 그룹 | 186 |
| 1개 멤버 그룹 | 80 |
| 2개 이상 개발 대상 그룹 | 106 |
| 개발 대상 멤버 | 747 |
| 멤버 수 min / p50 / p90 / max | 2 / 4.0 / 17.0 / 38 |
| 동일 Size 그룹 | 74 / 69.8% |
| 혼합 Size 그룹 | 32 |

## 4. 개발 대상 그룹 Vector 준비도

| 항목 | 멤버 coverage | 전체 멤버가 연결된 그룹 |
|---|---:|---:|
| Feature | 100.0% | 106 |
| Context | 100.0% | 106 |
| 상세경로 | 100.0% | 106 |
| 시작/종점 좌표 | 100.0% | - |

## 5. 장비명 정규화

- 정규화 장비 수: 35
- 두 가지 이상 원시 표기가 합쳐진 장비 키: 0

## 6. Top-K 후보 버킷

| Utility Group | Utility | 그룹 수 |
|---|---|---:|
| VACCUM | FORELINE | 8 |
| GAS | GN2 | 7 |
| WATER | PCWS | 7 |
| EXHAUST | ACID | 6 |
| GAS | PN2 | 6 |
| WATER | PCWR | 6 |
| GAS | PA | 5 |
| WATER | LPS | 5 |
| WATER | LPR | 5 |
| EXHAUST | ALKA | 3 |
| VACCUM | PV | 3 |
| UPW | UPW_S | 2 |
| EXHAUST | ORG | 2 |
| WASTE WATER | OWW | 2 |
| EXHAUST | CABINET(EX) | 2 |
| GAS | O2 | 2 |
| TOXIC | CH2F2 | 2 |
| TOXIC | C4F6 | 2 |
| TOXIC | NF3 | 2 |
| TOXIC | SIH4 | 2 |

## 7. 멤버 수 상위 그룹

| 장비 | Utility Group | Utility | 멤버 | Size 분포 | Feature | Context | 상세경로 |
|---|---|---|---:|---|---:|---:|---:|
| WTNHJ02 | WASTE LIQUID | NFW | 38 | 15A:30, 20A:1, 50A:7 | 100.0% | 100.0% | 100.0% |
| ELOHJ02 | WATER | PCWR | 36 | 1B:21, 20A:15 | 100.0% | 100.0% | 100.0% |
| ELOHJ02 | WATER | PCWS | 36 | 1B:12, 20A:24 | 100.0% | 100.0% | 100.0% |
| KSCTA01 | UPW | UPW_S | 32 | 25A:32 | 100.0% | 100.0% | 100.0% |
| COMP_PUMP_KAS_SDE30M20-LT_9?? | GAS | GN2 | 30 | 1/4B:30 | 100.0% | 100.0% | 100.0% |
| COMP_PUMP_KAS_SDE30M20-LT_9?? | WATER | LPR | 30 | 3/8B:30 | 100.0% | 100.0% | 100.0% |
| COMP_PUMP_KAS_SDE30M20-LT_9?? | WATER | LPS | 30 | 3/8B:30 | 100.0% | 100.0% | 100.0% |
| KSCTA01 | WASTE WATER | AKWW | 20 | 20A:15, 50A:5 | 100.0% | 100.0% | 100.0% |
| WTNHJ02 | GAS | PA | 18 | 1/2B:6, 1B:12 | 100.0% | 100.0% | 100.0% |
| WTNHJ02 | UPW | UPW_S | 18 | 20A:2, 25A:6, 40A:10 | 100.0% | 100.0% | 100.0% |
| PSTWA03 | EXHAUST | ORG | 17 | 100A:7, 125A:8, 150A:2 | 100.0% | 100.0% | 100.0% |
| WTNHJ02 | EXHAUST | ACID | 17 | 100A:1, 150A:10, 50A:6 | 100.0% | 100.0% | 100.0% |
| SLWHJ01 | VACCUM | PV_VENT | 16 | 1/4B:16 | 100.0% | 100.0% | 100.0% |
| SLWHJ01 | GAS | AR | 15 | 1/4B:15 | 100.0% | 100.0% | 100.0% |
| KSCTA01 | EXHAUST | ALKA | 14 | 100A:6, 50A:3, 65A:5 | 100.0% | 100.0% | 100.0% |
| WTNHJ02 | UPW | HOT DI_S | 12 | 25A:2, 40A:10 | 100.0% | 100.0% | 100.0% |
| WTNHJ02 | GAS | PN2 | 11 | 1/2B:3, 3/4B:8 | 100.0% | 100.0% | 100.0% |
| COMP_PUMP_KAS_SDE30M20-LT_9?? | WATER | PCWS | 10 | 100A:10 | 100.0% | 100.0% | 100.0% |
| DANHJ01 | GAS | PN2 | 10 | 1/2B:5, 1/8B:5 | 100.0% | 100.0% | 100.0% |
| SCRUBBER_001 | EXHAUST | PFC | 10 | 100A:6, 50A:4 | 100.0% | 100.0% | 100.0% |

## 8. Top-K 성립 조건 분석

그룹 자체를 `(장비 + Utility Group + Utility)`로 정의하면 동일 ACTIVE snapshot에서 이 조합은 한 행만 존재한다. 따라서 Candidate SQL에 정확한 장비 키까지 `WHERE` 조건으로 적용하고 Query 자신을 제외하면 결과가 0건이 된다.

```text
Query 그룹 식별: 장비 + Utility Group + Utility
Candidate 필수 필터: Utility Group + Utility
Candidate 선택 필터: Process, Equipment Family, Size 정책
금지: 정확한 장비 인스턴스 키를 필수 Candidate 필터로 사용
```

현재 ACTIVE 데이터에서는 Utility Group+Utility 버킷 중 22개가 2개 이상 그룹을, 9개가 5개 이상 그룹을 보유한다. K=5 검색은 모든 Utility에서 보장되지 않으므로 실제 후보 수를 UI에 표시하고 부족하면 존재하는 그룹만 반환해야 한다.

## 9. 단계 1 입력 결정

- 그룹 사용자 키는 `장비 + Utility Group + Utility`를 유지한다.
- 내부 장비 인스턴스 키는 실제 컬럼 `EQUIPMENT_TAG`를 우선하고 후행 `_`, `-`, 공백 제거 정규화를 적용한다.
- Candidate 필수 필터는 `Utility Group + Utility`로 확정하고 장비 인스턴스 키는 자기 제외와 결과 설명에만 사용한다.
- Process 및 향후 Equipment Family는 선택 필터로 두고 후보 부족 시 완화할 수 있게 한다.
- 기본 `minMemberCount=2`를 적용한다.
- 동일 Size 그룹이 100%가 아니므로 기본 Size 정책은 `ExactOnly`가 아니라 `PreferExact`로 확정한다.
- ACTIVE 개발 대상 그룹의 Feature/Context/상세경로 coverage가 모두 100%이므로 1차 구현은 세 데이터가 모두 있는 그룹을 READY로 생성한다.
- 후보 그룹이 K보다 적으면 후보 확장으로 무관한 Utility를 섞지 않고 가용 결과 수만 반환한다.

## 10. 스키마 진단

```json
{
  "route": {
    "table": "TB_ROUTE_PATH",
    "equipment_tag_column": "EQUIPMENT_TAG",
    "equipment_name_column": "EQUIPMENT_NAME",
    "utility_column": "SOURCE_UTILITY",
    "size_column": "SOURCE_SIZE",
    "scope_columns_present": true
  },
  "feature": {
    "table": "TB_ROUTE_FEATURE_VECTOR",
    "available": true,
    "scope_filtered": true,
    "row_guid_count": 7879
  },
  "context": {
    "table": "TB_ROUTE_CONTEXT_VECTOR",
    "available": true,
    "scope_filtered": true,
    "row_guid_count": 7879
  },
  "geometry_sources": [
    {
      "table": "TB_ROUTE_SEGMENTS",
      "route_guid_column": "ROUTE_PATH_GUID",
      "guid_count": 827
    },
    {
      "table": "TB_ROUTE_PATH_SEGMENT_MAP",
      "route_guid_column": null,
      "guid_count": 0
    },
    {
      "table": "TB_ROUTE_SEGMENT_DETAIL",
      "route_guid_column": null,
      "guid_count": 0
    }
  ]
}
```
