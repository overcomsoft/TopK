# Neo4j 지식그래프 기반 배관 설계 특징 분석 예시 설명서

## 문서 정보

- **수정 일시**: 2026-06-24 17:21:24 KST
- **대상 시스템**: TopKGen / PostgreSQL / Neo4j / Routing3D
- **문서 목적**: 지식그래프(Neo4j)에서 수행할 수 있는 Route, Segment, PoC, Equipment, Utility, Obstacle 관계 구축과 설계 패턴 분석 기능을 예시 중심으로 설명한다.
- **중요 기준**: 모든 설계 규칙, 방향, 장애물 회피, 반복 패턴 추론은 **동일 장비 + 유틸리티그룹 + 유틸리티** 조합을 기준으로 수행한다.
- **대표 예시 기준 데이터**: `장비(WTNHJ02) + 유틸리티그룹(Exhaust) + 유틸리티(ACID)`

---

## 1. 핵심 개념

PostgreSQL은 좌표, 거리, geometry 계산에 강하고, Neo4j는 관계를 따라가며 설계 맥락을 찾는 데 강하다.

본 문서에서 모든 추론은 다음 3중 키를 기준으로 수행한다.

```text
Inference Key = Equipment + UtilityGroup + Utility
예: WTNHJ02 + Exhaust + ACID
```

즉, 단순히 같은 장비만 보거나 같은 유틸리티그룹만 보는 것이 아니라, **동일 장비에서 동일 유틸리티그룹의 동일 유틸리티 배관이 과거에 어떻게 설계되었는가**를 기준으로 설계 규칙을 추론한다.

예를 들어 다음 질문이 핵심 추론 질문이다.

> WTNHJ02 장비의 Exhaust 그룹 중 ACID 유틸리티 배관은 기존 설계에서 어떤 방향으로 출발하고, 어떤 장애물을 만나면 어떤 방향으로 우회했는가?

Neo4j에서는 이 질문을 다음 관계 탐색으로 풀 수 있다.

```text
Equipment(WTNHJ02)
  -> Route
  -> UtilityGroup(Exhaust)
  -> Utility(ACID)
  -> Segment / PoC / Obstacle / FeatureVector
```

---

## 2. Route, Segment, PoC, Equipment, Utility, Obstacle 관계 구축

Neo4j에서는 테이블의 row를 그대로 복사하는 것이 아니라, 설계 의미 단위로 노드와 관계를 만든다. 이때 Route는 반드시 장비, 유틸리티그룹, 유틸리티와 함께 조회될 수 있어야 한다.

### 2.1 예시 그래프 모델

```text
(:Equipment {name:"WTNHJ02"})
    <-[:CONNECTED_TO {role:"START"}]-
(:Route {guid:"R001", equipment_name:"WTNHJ02", utility_group:"Exhaust", utility:"ACID"})
    -[:USES_UTILITY]->
(:Utility {group:"Exhaust", code:"ACID", size:"80A"})

(:Route {guid:"R001"})
    -[:STARTS_AT]->
(:PoC {kind:"START", x:1000, y:2000, z:3000})

(:Route {guid:"R001"})
    -[:HAS_SEGMENT {order:1}]->
(:Segment {axis:"Z", direction:"+Z", length_mm:1200})

(:Segment)
    -[:NEAR_OBSTACLE {clearance_margin_mm:80, bypass_axis:"Z", bypass_side:"+Z"}]->
(:Obstacle {type:"DUCT", name:"DUCT_A01"})
```

### 2.2 추론 기준 관계

| 기준 | Neo4j 표현 | 설명 |
|---|---|---|
| 장비 | `(:Equipment {name:"WTNHJ02"})` 또는 `Route.equipment_name` | 같은 장비의 과거 설계만 추론 대상으로 사용 |
| 유틸리티그룹 | `(:Utility {group:"Exhaust"})` 또는 `Route.utility_group` | 같은 계통/그룹의 배관만 비교 |
| 유틸리티 | `(:Utility {code:"ACID"})` 또는 `Route.utility` | 같은 유틸리티 배관만 설계 규칙으로 사용 |

이 3개 조건을 모두 만족하는 Route만 설계규칙, 방향, 장애물 회피 추론의 기준 데이터가 된다.

### 2.3 Cypher 예시: 기준 데이터 Route 조회

```cypher
MATCH (eq:Equipment {name:$equipment})<-[:CONNECTED_TO]-(r:Route)
MATCH (r)-[:USES_UTILITY]->(u:Utility {group:$utility_group, code:$utility})
MATCH (r)-[:STARTS_AT]->(sp:PoC)
MATCH (r)-[:ENDS_AT]->(ep:PoC)
RETURN
  r.guid AS route,
  eq.name AS equipment,
  u.group AS utility_group,
  u.code AS utility,
  u.size AS size,
  sp.x AS start_x, sp.y AS start_y, sp.z AS start_z,
  ep.x AS end_x, ep.y AS end_y, ep.z AS end_z;
```

파라미터 예:

```json
{
  "equipment": "WTNHJ02",
  "utility_group": "Exhaust",
  "utility": "ACID"
}
```

---

## 3. 설계 패턴 질의

설계 패턴 질의는 기준 데이터, 즉 `WTNHJ02 + Exhaust + ACID`에 해당하는 기존 Route만 대상으로 반복 패턴을 찾는다.

대표 질문은 다음과 같다.

- WTNHJ02 장비의 Exhaust/ACID 배관은 보통 어느 방향으로 출발하는가?
- WTNHJ02 장비의 Exhaust/ACID 배관은 종단 PoC에 어느 방향에서 접근하는가?
- Exhaust/ACID 배관은 특정 공간에서 Z축 상승 후 X/Y축으로 이동하는 패턴이 많은가?
- WTNHJ02의 Exhaust/ACID 배관은 보통 몇 번 꺾이는가?

### 3.1 기준 데이터의 시작 방향 통계

```cypher
MATCH (eq:Equipment {name:$equipment})<-[:CONNECTED_TO {role:"START"}]-(r:Route)
MATCH (r)-[:USES_UTILITY]->(u:Utility {group:$utility_group, code:$utility})
RETURN
  r.start_axis AS start_axis,
  r.start_direction AS start_direction,
  count(*) AS route_count
ORDER BY route_count DESC;
```

결과 예시:

| start_axis | start_direction | route_count |
|---|---|---:|
| Z | +Z | 18 |
| Y | -Y | 6 |
| X | +X | 3 |

해석: WTNHJ02의 Exhaust/ACID 배관은 기존 설계에서 대부분 `+Z` 방향으로 먼저 상승한 뒤 라우팅을 시작했다. 신규 자동경로 탐색에서도 시작 후보 방향으로 `+Z`를 우선 적용할 수 있다.

### 3.2 기준 데이터의 방향 패턴 문자열 질의

```cypher
MATCH (eq:Equipment {name:$equipment})<-[:CONNECTED_TO]-(r:Route)
MATCH (r)-[:USES_UTILITY]->(u:Utility {group:$utility_group, code:$utility})
RETURN
  r.direction_pattern AS pattern,
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

해석: WTNHJ02의 Exhaust/ACID 배관은 먼저 수직으로 상승한 뒤 수평 랙 또는 덕트 주변 회피 경로로 이동하는 패턴이 가장 많다.

---

## 4. 유사 route 관계 분석

유사 route 분석도 동일한 3중 키를 기준으로 제한해야 한다. 즉, query route와 비교 대상 route가 모두 같은 장비, 같은 유틸리티그룹, 같은 유틸리티에 속해야 한다.

### 4.1 유사 route 관계 예시

```text
(:Route {guid:"R001", equipment_name:"WTNHJ02", utility_group:"Exhaust", utility:"ACID"})
    -[:SIMILAR_TO {
        score:0.91,
        reason:"same equipment, same utility group, same utility, similar start/end direction, similar obstacle bypass"
    }]->
(:Route {guid:"R084", equipment_name:"WTNHJ02", utility_group:"Exhaust", utility:"ACID"})
```

### 4.2 Cypher 예시: 기준 데이터 내 유사 route 조회

```cypher
MATCH (q:Route {guid:$query_route_guid})-[:USES_UTILITY]->(qu:Utility)
MATCH (qe:Equipment)<-[:CONNECTED_TO]-(q)
MATCH (r:Route)-[:USES_UTILITY]->(ru:Utility {group:qu.group, code:qu.code})
MATCH (qe)<-[:CONNECTED_TO]-(r)
WHERE r.guid <> q.guid
WITH q, r,
     q.start_dir_x * r.start_dir_x +
     q.start_dir_y * r.start_dir_y +
     q.start_dir_z * r.start_dir_z AS start_sim,
     q.end_dir_x * r.end_dir_x +
     q.end_dir_y * r.end_dir_y +
     q.end_dir_z * r.end_dir_z AS end_sim
RETURN
  r.guid AS similar_route,
  r.equipment_name AS equipment,
  r.utility_group AS utility_group,
  r.utility AS utility,
  start_sim,
  end_sim,
  (start_sim + end_sim) / 2.0 AS direction_score
ORDER BY direction_score DESC
LIMIT 20;
```

이 질의는 query route와 동일 장비, 동일 유틸리티그룹, 동일 유틸리티에 속한 route만 비교한다.

### 4.3 유사도 기준

| 기준 | 설명 |
|---|---|
| 동일 장비 | `WTNHJ02` 같은 장비 기준 |
| 동일 유틸리티그룹 | `Exhaust` 같은 그룹 기준 |
| 동일 유틸리티 | `ACID` 같은 유틸리티 기준 |
| 시작/종단 방향 유사 | start/end direction |
| 전체 이동 방향 유사 | displacement |
| 경로 길이 유사 | total_length_mm |
| bend count 유사 | arrow pattern |
| 같은 장애물 유형 회피 | `NEAR_OBSTACLE` 관계 |
| 같은 공간 통과 | `PASSES_THROUGH` 관계 |

---

## 5. 장애물 회피 지식 추론

장애물 회피 지식도 기준 데이터 안에서만 추론해야 한다. 예를 들어 `WTNHJ02 + Exhaust + ACID` 배관이 DUCT 또는 WALL 근처에서 어떤 회피 방향을 선택했는지를 분석한다.

### 5.1 관계 예시

```text
(:Route {guid:"R001", equipment_name:"WTNHJ02", utility_group:"Exhaust", utility:"ACID"})
    -[:NEAR_OBSTACLE {
        nearest_distance_mm:180,
        required_clearance_mm:100,
        clearance_margin_mm:80,
        bypass_axis:"Z",
        bypass_side:"+Z",
        z_delta_near_obstacle_mm:650
    }]->
(:Obstacle {name:"DUCT_A01", type:"DUCT"})
```

### 5.2 기준 데이터에서 DUCT 장애물 회피 방향 추론

```cypher
MATCH (eq:Equipment {name:$equipment})<-[:CONNECTED_TO]-(r:Route)
MATCH (r)-[:USES_UTILITY]->(u:Utility {group:$utility_group, code:$utility})
MATCH (r)-[rel:NEAR_OBSTACLE]->(o:Obstacle {type:$obstacle_type})
RETURN
  rel.bypass_axis AS bypass_axis,
  rel.bypass_side AS bypass_side,
  count(*) AS count,
  avg(rel.clearance_margin_mm) AS avg_margin,
  avg(rel.z_delta_near_obstacle_mm) AS avg_z_delta
ORDER BY count DESC;
```

파라미터 예:

```json
{
  "equipment": "WTNHJ02",
  "utility_group": "Exhaust",
  "utility": "ACID",
  "obstacle_type": "DUCT"
}
```

결과 예시:

| bypass_axis | bypass_side | count | avg_margin | avg_z_delta |
|---|---|---:|---:|---:|
| Z | +Z | 12 | 145 | 720 |
| Y | -Y | 4 | 110 | 120 |
| X | +X | 2 | 95 | 80 |

해석: WTNHJ02의 Exhaust/ACID 배관이 DUCT 장애물을 만난 경우, 기존 설계는 대부분 `+Z` 상승 회피를 사용했다. 신규 라우팅에서도 같은 기준 데이터 조건에서는 DUCT 근처에서 Z 상승 후보를 우선 평가한다.

### 5.3 기준 데이터의 장애물 민감도 분석

```cypher
MATCH (eq:Equipment {name:$equipment})<-[:CONNECTED_TO]-(r:Route)
MATCH (r)-[:USES_UTILITY]->(u:Utility {group:$utility_group, code:$utility})
MATCH (r)-[rel:NEAR_OBSTACLE]->(o:Obstacle)
RETURN
  o.type AS obstacle_type,
  count(rel) AS obstacle_relation_count,
  avg(rel.clearance_margin_mm) AS avg_margin,
  min(rel.clearance_margin_mm) AS min_margin
ORDER BY obstacle_relation_count DESC;
```

이 질의는 특정 장비/유틸리티 조건에서 어떤 장애물 유형이 자주 등장하고, clearance margin이 얼마나 부족한지 확인한다.

---

## 6. 설계 규칙/반복 패턴 탐색

설계 규칙 탐색 역시 동일 장비, 동일 유틸리티그룹, 동일 유틸리티 조건으로 제한한다.

### 6.1 기준 데이터의 반복 시작 방향 규칙

```cypher
MATCH (eq:Equipment {name:$equipment})<-[:CONNECTED_TO]-(r:Route)
MATCH (r)-[:USES_UTILITY]->(u:Utility {group:$utility_group, code:$utility})
WITH r.start_direction AS start_direction, count(*) AS cnt
WITH collect({direction:start_direction, count:cnt}) AS stats, sum(cnt) AS total
UNWIND stats AS s
WITH s.direction AS direction,
     s.count AS cnt,
     total,
     toFloat(s.count) / total AS ratio
WHERE ratio >= 0.6
RETURN
  direction,
  cnt,
  total,
  ratio
ORDER BY ratio DESC;
```

결과 예시:

| direction | cnt | total | ratio |
|---|---:|---:|---:|
| +Z | 18 | 25 | 0.72 |

해석: WTNHJ02 + Exhaust + ACID 기준 데이터에서 72%가 +Z로 시작한다. 이 정도 비율이면 자동경로의 기본 시작 방향 후보로 등록할 수 있다.

### 6.2 기준 데이터의 공간별 반복 다발 패턴

```cypher
MATCH (eq:Equipment {name:$equipment})<-[:CONNECTED_TO]-(r:Route)
MATCH (r)-[:USES_UTILITY]->(u:Utility {group:$utility_group, code:$utility})
MATCH (r)-[:MEMBER_OF]->(b:BundleGroup)
MATCH (r)-[:PASSES_THROUGH]->(s:Space)
RETURN
  s.space_name AS space,
  b.direction AS bundle_direction,
  count(DISTINCT r) AS route_count,
  avg(b.avg_pitch_mm) AS avg_pitch
ORDER BY route_count DESC;
```

해석: WTNHJ02의 Exhaust/ACID 배관이 특정 공간에서 어떤 방향의 다발 배관으로 반복되는지 확인한다. Routing3D에서 중간 경유 영역 또는 rack level 후보를 정하는 데 활용할 수 있다.

### 6.3 기준 데이터의 bend count 규칙

```cypher
MATCH (eq:Equipment {name:$equipment})<-[:CONNECTED_TO]-(r:Route)
MATCH (r)-[:USES_UTILITY]->(u:Utility {group:$utility_group, code:$utility})
RETURN
  avg(r.bend_count) AS avg_bend,
  percentileCont(r.bend_count, 0.9) AS p90_bend,
  count(*) AS route_count;
```

해석: WTNHJ02 + Exhaust + ACID 조건에서 bend가 보통 몇 개 이하인지 알 수 있다. 신규 자동경로가 p90보다 훨씬 많은 bend를 만들면 품질이 낮은 후보로 판단할 수 있다.

---

## 7. 자동경로 탐색에서의 최종 활용 예

신규 요청 예:

```text
장비: WTNHJ02
Utility Group: Exhaust
Utility: ACID
Size: 80A
Start PoC: (1000, 2000, 3000)
End PoC: (9000, 7000, 4500)
주변 장애물: DUCT, WALL
```

처리 흐름:

1. PostgreSQL/PostGIS에서 주변 장애물, 공간, geometry 로딩
2. `WTNHJ02 + Exhaust + ACID` 기준으로 pgvector Top-K 기존 route 검색
3. Neo4j에서 같은 기준 데이터의 반복 시작/종단 방향 조회
4. Neo4j에서 같은 기준 데이터의 DUCT/WALL 장애물 회피 방향 통계 조회
5. Neo4j에서 같은 기준 데이터의 공간/BundleGroup/rack 패턴 조회
6. Routing3D에 heuristic 전달

Routing3D에 전달되는 지식 예:

```json
{
  "inference_key": {
    "equipment": "WTNHJ02",
    "utility_group": "Exhaust",
    "utility": "ACID"
  },
  "preferred_start_directions": ["+Z", "-Y"],
  "preferred_end_directions": ["-Y"],
  "preferred_bypass": {
    "DUCT": ["+Z"],
    "WALL": ["+X", "-Y"]
  },
  "preferred_axis_ratio": {
    "x": 0.45,
    "y": 0.35,
    "z": 0.20
  },
  "max_recommended_bend_count": 5,
  "preferred_bundle_spaces": ["CSF", "A/F"],
  "reference_routes": ["R001", "R084", "R132"]
}
```

이렇게 되면 Routing3D는 단순히 갈 수 있는 길을 찾는 것이 아니라, 동일 장비와 동일 유틸리티 조건에서 기존 설계자가 자주 선택한 방식에 가까운 설계다운 길을 우선 탐색할 수 있다.

---

## 8. 결론

Neo4j 지식그래프의 추론 기준은 반드시 다음과 같이 고정한다.

```text
동일 장비 + 동일 유틸리티그룹 + 동일 유틸리티
```

예를 들어 `WTNHJ02 + Exhaust + ACID`는 하나의 독립적인 설계 지식 집합이다. 이 집합 안에서 시작 방향, 종단 방향, 장애물 회피 방향, bend count, 공간 통과 패턴, 다발 배관 패턴을 추론해야 한다.

이 기준을 지키면 자동경로 탐색은 전체 평균 설계가 아니라, **해당 장비와 해당 유틸리티 조건에 맞는 설계 규칙**을 적용할 수 있다.
