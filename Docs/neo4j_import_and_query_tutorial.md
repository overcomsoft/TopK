# PostgreSQL 기존설계데이터 Neo4j Import 및 설계지식 추론 예제 튜토리얼

## 문서 정보

- **작성 일시**: 2026-06-24 17:31:11 KST
- **대상 시스템**: PostgreSQL/PostGIS/pgvector, Neo4j, TopKGen, Routing3D
- **대상 기준 데이터 예시**: `장비(WTNHJ02) + 유틸리티그룹(Exhaust) + 유틸리티(ACID)`
- **튜토리얼 목적**: PostgreSQL에 저장된 기존 배관 설계 데이터와 특징점 데이터를 Neo4j 지식그래프로 import하고, 동일 장비 + 유틸리티그룹 + 유틸리티 기준으로 설계 규칙, 방향, 유사 route, 장애물 회피 패턴을 질의하는 절차를 설명한다.

---

## 1. 전체 튜토리얼 흐름

```text
1. PostgreSQL 기존설계/특징 테이블 준비
2. Neo4j DB 준비 및 constraint/index 생성
3. PostgreSQL에서 Route, PoC, Segment, Feature, Obstacle 관계 데이터 추출
4. Python ETL로 Neo4j에 노드/관계 import
5. WTNHJ02 + Exhaust + ACID 기준 데이터 검증
6. 설계 방향/장애물 회피/반복 패턴 예제 Cypher 실행
7. Routing3D에 전달할 heuristic JSON 생성
```

핵심 원칙은 다음과 같다.

```text
추론 기준 = 동일 장비 + 동일 유틸리티그룹 + 동일 유틸리티
예시 기준 = WTNHJ02 + Exhaust + ACID
```

---

## 2. 사전 준비

## 2.1 PostgreSQL 준비

PostgreSQL에는 최소한 다음 테이블 또는 동등한 데이터가 있어야 한다.

| 구분 | 테이블 | 용도 |
|---|---|---|
| 기존 route | `TB_ROUTE_PATH` | 장비, 유틸리티, source/target PoC 메타데이터 |
| route segment | `TB_ROUTE_SEGMENTS`, `TB_ROUTE_SEGMENT_DETAIL` | route 중심선 segment 좌표 |
| 30D feature | `TB_ROUTE_FEATURE_VECTOR` | pgvector 및 `FEATURE_VECTOR_JSON` |
| 장애물 관계 | `TB_ROUTE_FEATURE_OBSTACLE_RELATION` | route와 obstacle의 clearance/bypass 관계 |
| 장애물 원본 | `TB_BIM_OBSTACLE` | 장애물 종류, AABB |
| 공간 정보 | `TB_SPACE_INFO` | CSF, CR, A/F, FSF 등 공간 AABB |
| 다발 특징 | `TB_ROUTE_VERTICAL_GROUP_FEATURE` | bundle/vertical group 관계 |

`FEATURE_VECTOR_JSON` 컬럼이 없다면 `Extract_Design_Pattern.py`의 `prepare_tables()` 실행 또는 다음 DDL을 먼저 적용한다.

```sql
ALTER TABLE "TB_ROUTE_FEATURE_VECTOR"
ADD COLUMN IF NOT EXISTS "FEATURE_VECTOR_JSON" jsonb;
```

## 2.2 Neo4j 준비

Neo4j Desktop 또는 Neo4j Server를 실행하고 Bolt 접속 정보를 준비한다.

예시:

```text
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
```

Python ETL에는 다음 패키지가 필요하다.

```powershell
pip install psycopg2-binary neo4j
```

---

## 3. Neo4j Graph Schema 생성

Neo4j Browser 또는 Python ETL 초기화 단계에서 다음 constraint/index를 생성한다.

```cypher
CREATE CONSTRAINT project_id IF NOT EXISTS
FOR (p:Project) REQUIRE p.project_id IS UNIQUE;

CREATE CONSTRAINT equipment_name IF NOT EXISTS
FOR (e:Equipment) REQUIRE e.name IS UNIQUE;

CREATE CONSTRAINT utility_key IF NOT EXISTS
FOR (u:Utility) REQUIRE u.key IS UNIQUE;

CREATE CONSTRAINT route_guid IF NOT EXISTS
FOR (r:Route) REQUIRE r.guid IS UNIQUE;

CREATE CONSTRAINT poc_id IF NOT EXISTS
FOR (p:PoC) REQUIRE p.poc_id IS UNIQUE;

CREATE CONSTRAINT segment_id IF NOT EXISTS
FOR (s:Segment) REQUIRE s.segment_id IS UNIQUE;

CREATE CONSTRAINT obstacle_name IF NOT EXISTS
FOR (o:Obstacle) REQUIRE o.name IS UNIQUE;

CREATE INDEX route_inference_key IF NOT EXISTS
FOR (r:Route) ON (r.equipment_name, r.utility_group, r.utility);

CREATE INDEX route_direction_pattern IF NOT EXISTS
FOR (r:Route) ON (r.direction_pattern);

CREATE INDEX segment_axis IF NOT EXISTS
FOR (s:Segment) ON (s.axis);
```

`Utility.key`는 `utility_group + '|' + utility + '|' + size` 형태로 구성하는 것을 권장한다.

---

## 4. PostgreSQL에서 Import 대상 데이터 추출

## 4.1 기준 데이터 Route 조회 SQL

```sql
SELECT
    rp."ROUTE_PATH_GUID",
    rp."PROCESS_NAME",
    rp."EQUIPMENT_TAG",
    rp."UTILITY_GROUP",
    rp."SOURCE_UTILITY" AS "UTILITY",
    rp."SOURCE_SIZE" AS "SIZE",
    rp."SOURCE_POSX", rp."SOURCE_POSY", rp."SOURCE_POSZ",
    rp."TARGET_POSX", rp."TARGET_POSY", rp."TARGET_POSZ",
    fv."DIRECTION_PATTERN",
    fv."TOTAL_LENGTH_MM",
    fv."STEP_COUNT",
    fv."FEATURE_VECTOR_JSON"
FROM "TB_ROUTE_PATH" rp
LEFT JOIN "TB_ROUTE_FEATURE_VECTOR" fv
       ON rp."ROUTE_PATH_GUID" = fv."ROUTE_PATH_GUID"
WHERE rp."EQUIPMENT_TAG" = 'WTNHJ02'
  AND rp."UTILITY_GROUP" = 'Exhaust'
  AND rp."SOURCE_UTILITY" = 'ACID';
```

## 4.2 Segment 조회 SQL

```sql
SELECT
    rp."ROUTE_PATH_GUID",
    rs."ORDER" AS segment_order,
    sd."ORDER" AS detail_order,
    sd."FROM_POSX", sd."FROM_POSY", sd."FROM_POSZ",
    sd."TO_POSX", sd."TO_POSY", sd."TO_POSZ"
FROM "TB_ROUTE_PATH" rp
JOIN "TB_ROUTE_SEGMENTS" rs
  ON rp."ROUTE_PATH_GUID" = rs."ROUTE_PATH_GUID"
JOIN "TB_ROUTE_SEGMENT_DETAIL" sd
  ON rs."SEGMENT_GUID" = sd."SEGMENT_GUID"
WHERE rp."EQUIPMENT_TAG" = 'WTNHJ02'
  AND rp."UTILITY_GROUP" = 'Exhaust'
  AND rp."SOURCE_UTILITY" = 'ACID'
ORDER BY rp."ROUTE_PATH_GUID", rs."ORDER", sd."ORDER";
```

## 4.3 장애물 관계 조회 SQL

```sql
SELECT
    "ROUTE_PATH_GUID",
    "OBSTACLE_NAME",
    "OBSTACLE_TYPE",
    "OBSTACLE_AXIS",
    "NEAREST_DISTANCE_MM",
    "REQUIRED_CLEARANCE_MM",
    "CLEARANCE_MARGIN_MM",
    "BYPASS_SIDE",
    "BYPASS_AXIS",
    "Z_DELTA_NEAR_OBSTACLE_MM",
    "RELATION_SCORE"
FROM "TB_ROUTE_FEATURE_OBSTACLE_RELATION"
WHERE "PROJECT_ID" = 'WTNHJ02'
  AND "UTILITY_GROUP" = 'Exhaust'
  AND "UTILITY" = 'ACID';
```

---

## 5. Python ETL 예제

아래 코드는 PostgreSQL에서 기준 데이터 route, segment, obstacle relation을 읽고 Neo4j에 import하는 최소 예제이다. 실제 운영에서는 batch size, 로그, 재시도, 설정 파일 분리, 증분 업데이트를 추가한다.

```python
import json
import math
import os
import psycopg2
import psycopg2.extras
from neo4j import GraphDatabase

PG_CONNINFO = os.getenv("PG_CONNINFO", "host=localhost port=5432 dbname=DDW_AI_DB user=postgres password=password")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

EQUIPMENT = "WTNHJ02"
UTILITY_GROUP = "Exhaust"
UTILITY = "ACID"

# 운영 구현에서는 이 예제 함수를 Tools/Export_Design_KG_Neo4j.py로 분리한다.
# load_routes/load_segments/load_obstacle_relations는 위 SQL을 파라미터화해 사용한다.
# import_routes/import_segments/import_obstacle_relations는 MERGE 기반으로 중복 없이 upsert한다.
```

실제 소스 파일에는 위 SQL과 Cypher를 함수로 분리해 다음 구조로 구현하는 것을 권장한다.

```text
Tools/Export_Design_KG_Neo4j.py
  - create_constraints(driver)
  - load_routes(pg_conn, equipment, utility_group, utility)
  - load_segments(pg_conn, equipment, utility_group, utility)
  - load_obstacle_relations(pg_conn, equipment, utility_group, utility)
  - import_routes(session, rows)
  - import_segments(session, rows)
  - import_obstacle_relations(session, rows)
  - validate_import(session, equipment, utility_group, utility)
```

---

## 6. 실행 방법

## 6.1 환경 변수 설정

PowerShell 예시:

```powershell
$env:PG_CONNINFO="host=localhost port=5432 dbname=DDW_AI_DB user=postgres password=your_password"
$env:NEO4J_URI="bolt://localhost:7687"
$env:NEO4J_USER="neo4j"
$env:NEO4J_PASSWORD="your_neo4j_password"
```

## 6.2 ETL 실행

예제 스크립트를 `Tools/Export_Design_KG_Neo4j.py`로 저장한 뒤 실행한다.

```powershell
python D:\DINNO\DEV\AI-AutoRouting\TopKGen\Tools\Export_Design_KG_Neo4j.py --equipment WTNHJ02 --utility-group Exhaust --utility ACID
```

예상 출력:

```text
Imported routes=25, segments=184, obstacle_relations=42
```

---

## 7. Import 결과 검증

## 7.1 기준 데이터 Route 개수 확인

```cypher
MATCH (r:Route {equipment_name:"WTNHJ02", utility_group:"Exhaust", utility:"ACID"})
RETURN count(r) AS route_count;
```

## 7.2 Route-Segment 관계 확인

```cypher
MATCH (r:Route {equipment_name:"WTNHJ02", utility_group:"Exhaust", utility:"ACID"})
MATCH (r)-[:HAS_SEGMENT]->(s:Segment)
RETURN r.guid AS route, count(s) AS segment_count
ORDER BY segment_count DESC
LIMIT 10;
```

## 7.3 장애물 관계 확인

```cypher
MATCH (r:Route {equipment_name:"WTNHJ02", utility_group:"Exhaust", utility:"ACID"})
MATCH (r)-[rel:NEAR_OBSTACLE]->(o:Obstacle)
RETURN r.guid AS route, o.type AS obstacle_type, count(rel) AS relation_count
ORDER BY relation_count DESC
LIMIT 20;
```

---

## 8. 예제 1: 시작 방향 설계 규칙 추론

```cypher
MATCH (r:Route {equipment_name:"WTNHJ02", utility_group:"Exhaust", utility:"ACID"})
MATCH (r)-[:HAS_SEGMENT {order:0}]->(s:Segment)
RETURN s.axis AS start_axis,
       s.direction AS start_direction,
       count(*) AS route_count
ORDER BY route_count DESC;
```

결과 예시:

| start_axis | start_direction | route_count |
|---|---|---:|
| Z | +Z | 18 |
| Y | -Y | 6 |
| X | +X | 3 |

해석: `WTNHJ02 + Exhaust + ACID` 기준 기존 설계는 대체로 `+Z` 상승 후 라우팅을 시작한다. Routing3D에서는 시작 PoC 주변 후보 확장 시 `+Z` 방향 비용을 낮추거나 우선순위를 높일 수 있다.

---

## 9. 예제 2: 방향 패턴 추론

```cypher
MATCH (r:Route {equipment_name:"WTNHJ02", utility_group:"Exhaust", utility:"ACID"})
RETURN r.direction_pattern AS pattern,
       count(*) AS count
ORDER BY count DESC
LIMIT 10;
```

결과 예시:

| pattern | count |
|---|---:|
| R-H | 14 |
| R-H-R | 8 |
| H-R-H | 3 |

---

## 10. 예제 3: 장애물 회피 방향 추론

```cypher
MATCH (r:Route {equipment_name:"WTNHJ02", utility_group:"Exhaust", utility:"ACID"})
MATCH (r)-[rel:NEAR_OBSTACLE]->(o:Obstacle {type:"DUCT"})
RETURN rel.bypass_axis AS bypass_axis,
       rel.bypass_side AS bypass_side,
       count(*) AS count,
       avg(rel.clearance_margin_mm) AS avg_margin,
       avg(rel.z_delta_near_obstacle_mm) AS avg_z_delta
ORDER BY count DESC;
```

결과 예시:

| bypass_axis | bypass_side | count | avg_margin | avg_z_delta |
|---|---|---:|---:|---:|
| Z | +Z | 12 | 145 | 720 |
| Y | -Y | 4 | 110 | 120 |
| X | +X | 2 | 95 | 80 |

해석: `WTNHJ02 + Exhaust + ACID` 기준으로 DUCT 장애물을 만난 경우 `+Z` 회피가 가장 많이 사용되었다. Routing3D에서는 DUCT 근처 탐색 시 `+Z` 상승 후보를 우선 확장한다.

---

## 11. 예제 4: 유사 Route 분석

```cypher
MATCH (q:Route {guid:$query_route_guid})
MATCH (r:Route {equipment_name:q.equipment_name, utility_group:q.utility_group, utility:q.utility})
WHERE r.guid <> q.guid
WITH q, r,
     q.start_dir_x * r.start_dir_x + q.start_dir_y * r.start_dir_y + q.start_dir_z * r.start_dir_z AS start_sim,
     q.end_dir_x * r.end_dir_x + q.end_dir_y * r.end_dir_y + q.end_dir_z * r.end_dir_z AS end_sim
RETURN r.guid AS similar_route,
       start_sim,
       end_sim,
       (start_sim + end_sim) / 2.0 AS direction_score
ORDER BY direction_score DESC
LIMIT 20;
```

주의: 이 예제는 `Route.start_dir_x/y/z`, `Route.end_dir_x/y/z` 속성이 import되어 있어야 한다. 운영 ETL에서는 첫 segment와 마지막 segment 방향을 Route 속성으로 함께 저장하는 것을 권장한다.

---

## 12. 예제 5: Bend Count 및 설계 품질 기준 추론

```cypher
MATCH (r:Route {equipment_name:"WTNHJ02", utility_group:"Exhaust", utility:"ACID"})
RETURN avg(r.bend_count) AS avg_bend,
       percentileCont(r.bend_count, 0.9) AS p90_bend,
       count(*) AS route_count;
```

해석: p90 bend가 5라면, 신규 자동경로가 bend 8개 이상을 생성했을 때 기존 설계 패턴 대비 품질이 낮은 후보로 판단할 수 있다.

---

## 13. Routing3D Heuristic JSON 생성 예

```json
{
  "inference_key": {
    "equipment": "WTNHJ02",
    "utility_group": "Exhaust",
    "utility": "ACID"
  },
  "preferred_start_directions": ["+Z", "-Y"],
  "preferred_bypass": {
    "DUCT": ["+Z"],
    "WALL": ["+X", "-Y"]
  },
  "max_recommended_bend_count": 5,
  "reference_routes": ["R001", "R084", "R132"]
}
```

| Heuristic | 적용 위치 | 설명 |
|---|---|---|
| `preferred_start_directions` | 시작 PoC 확장 | 기존 설계에서 자주 쓰인 시작 방향 우선 |
| `preferred_bypass` | 장애물 근접 탐색 | 장애물 유형별 선호 회피 방향 우선 |
| `max_recommended_bend_count` | 후보 경로 평가 | 기존 설계 대비 과도한 bend 후보 감점 |
| `reference_routes` | 결과 설명/검증 | 어떤 기존 설계를 참고했는지 추적 |

---

## 14. 운영 적용 시 권장 개선

1. ETL 스크립트에서 `equipment`, `utility_group`, `utility`를 command line argument로 받도록 확장한다.
2. `Route.start_dir_x/y/z`, `Route.end_dir_x/y/z`, `Route.axis_ratio_x/y/z`, `Route.bend_count`를 import한다.
3. PostgreSQL `TB_ROUTE_FEATURE_VECTOR.FEATURE_VECTOR_JSON`의 30D 값을 Neo4j에도 저장한다.
4. pgvector Top-K 결과를 Neo4j `SIMILAR_TO` 관계로 캐싱한다.
5. Routing3D 실행 전 Neo4j에서 추론한 heuristic JSON을 생성하는 API를 만든다.
6. ETL 결과를 route count, segment count, obstacle relation count 기준으로 매번 검증한다.

---

## 15. 결론

이 튜토리얼의 핵심은 PostgreSQL 데이터를 Neo4j에 단순 복사하는 것이 아니라, 다음 기준으로 설계 지식을 관계화하는 것이다.

```text
동일 장비 + 동일 유틸리티그룹 + 동일 유틸리티
```

`WTNHJ02 + Exhaust + ACID` 기준으로 Neo4j 지식그래프를 구축하면, 해당 조건의 기존 설계에서 반복되는 시작 방향, 주행 패턴, 장애물 회피 방향, bend 품질 기준을 추론할 수 있다.

이 추론 결과는 Routing3D 자동경로 탐색에서 초기 방향, 장애물 회피 방향, 후보 경로 평가 비용으로 활용할 수 있다.
