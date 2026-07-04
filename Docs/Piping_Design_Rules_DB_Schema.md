# 배관 설계 규칙(Piping Design Rules) DB 스키마 설계 및 가이드

본 문서는 배관 설계 규칙 PDF에 기재된 21가지 규칙을 AI 자동 라우팅 엔진이 런타임에 쿼리하여 기하학적 제약조건 및 부품 매핑에 활용할 수 있도록 설계한 관계형 데이터베이스(RDB) 스키마 구조를 정의합니다.

---

## 1. DB 설계 기본 원칙

1. **하드코딩 방지**: 규칙의 수치나 물리적 제약 조건을 소스 코드에 직접 코딩하지 않고, DB 테이블로 분리하여 유틸리티 종류, 배관 재질, 사이즈 등에 따라 동적 조회하도록 설계합니다.
2. **정규화 및 인덱싱**: 최단 경로 탐색(A* 알고리즘 등) 중의 충돌/이격 검사가 매우 빈번하게 일어나므로, 탐색 조건(`MATERIAL`, `SIZE`, `UTILITY` 등)을 인덱스 키로 설정하여 즉각 검색이 가능하도록 합니다.
3. **물리적 일관성**: 길이 단위를 `mm`, 각도 단위를 `degree`로 일원화합니다.

---

## 2. 테이블 스키마 정의 (DDL)

```sql
-- 1. 배관끼리의 이격 및 장애물 이격 기준 테이블 (Rule 1, 2, 3, 4, 5, 6)
CREATE TABLE "TB_RULE_CLEARANCE_STANDARD" (
    "RULE_ID" serial PRIMARY KEY,
    "TARGET_TYPE_A" text NOT NULL,        -- 'PIPE', 'DUCT_WALL', 'LATTICE_BEAM', 'TAKEOFF_OUTLINE' 등
    "TARGET_TYPE_B" text NOT NULL,        -- 'PIPE', 'DUCT_WALL', 'TAKEOFF_OUTLINE' 등
    "RELATION_TYPE" text NOT NULL,        -- 'PARALLEL' (동일선상), 'OFFSET' (어긋난 배치), 'VERTICAL' (수직), 'ALL' (무관)
    "UTILITY_GROUP" text DEFAULT 'ALL',   -- 'EXH', 'GAS', 'VAC' 등 적용 공종
    "MIN_DISTANCE_MM" double precision NOT NULL,
    "RECOMMENDED_DISTANCE_MM" double precision,
    "REMARKS" text
);

CREATE INDEX "IX_TRCS_LOOKUP" ON "TB_RULE_CLEARANCE_STANDARD" ("TARGET_TYPE_A", "TARGET_TYPE_B", "RELATION_TYPE");

-- 2. 배관 재질/사이즈별 이격거리 테이블 (Rule 7)
CREATE TABLE "TB_RULE_PIPE_SPACING" (
    "RULE_ID" serial PRIMARY KEY,
    "MATERIAL" text NOT NULL,             -- 'GALV', '구조관', 'FM PVC'
    "PIPE_SIZE" text NOT NULL,            -- '50A', '100A', '125A', '150A', '200A', '250A'
    "MIN_DISTANCE_MM" double precision NOT NULL,
    "RECOMMENDED_DISTANCE_MM" double precision NOT NULL
);

CREATE UNIQUE INDEX "UX_TRPS_LOOKUP" ON "TB_RULE_PIPE_SPACING" ("MATERIAL", "PIPE_SIZE");

-- 3. 사선 배관 및 격자 통과 기하적 제약 테이블 (Rule 8, 9, 10, 11, 12, 13)
CREATE TABLE "TB_RULE_OBLIQUE_ROUTING" (
    "RULE_ID" serial PRIMARY KEY,
    "UTILITY_GROUP" text NOT NULL,        -- 'GAS', 'EXH', 'VAC', 'ALL'
    "MAX_STRAIGHT_LENGTH_MM" double precision DEFAULT 2000.0,
    "PREFERRED_BEND_COUNT" integer DEFAULT 2,
    "MAX_BEND_COUNT" integer DEFAULT 4,
    "PREFERRED_ANGLES" integer[] DEFAULT '{30, 45, 60}', -- 우선순위 각도 배열
    "FLANGE_OMISSION_LIMIT_MM" double precision DEFAULT 2000.0,
    "LATTICE_PASS_MIN_CLEARANCE_MM" double precision DEFAULT 50.0
);

CREATE UNIQUE INDEX "UX_TROR_LOOKUP" ON "TB_RULE_OBLIQUE_ROUTING" ("UTILITY_GROUP");

-- 4. 자재 선택 및 매핑 테이블 (Rule 15, 16, 18)
CREATE TABLE "TB_RULE_MATERIAL_MAPPING" (
    "RULE_ID" serial PRIMARY KEY,
    "EXHAUST_TYPE" text NOT NULL,         -- '열배기', '캐비넷배기', '알카리배기', '산배기', '유기배기', 'PFC' 등
    "UTILITY_GROUP" text,
    "ALLOWED_MATERIALS" text[] NOT NULL,  -- '{GALV, 구조관}'
    "ALLOWED_SIZES" text[] NOT NULL       -- '{50, 100, 125, 150, 200, 250}'
);

CREATE UNIQUE INDEX "UX_TRMM_LOOKUP" ON "TB_RULE_MATERIAL_MAPPING" ("EXHAUST_TYPE");

-- 5. Reducer 치수 규격 테이블 (Rule 17)
CREATE TABLE "TB_RULE_REDUCER_SPEC" (
    "RULE_ID" serial PRIMARY KEY,
    "OD1_MM" double precision NOT NULL,
    "OD2_MM" double precision NOT NULL,
    "FIXED_LENGTH_MM" double precision NOT NULL
);

CREATE UNIQUE INDEX "UX_TRRS_LOOKUP" ON "TB_RULE_REDUCER_SPEC" ("OD1_MM", "OD2_MM");

-- 6. 고정 설치 좌표/오프셋 규칙 테이블 (Rule 14, 19, 20, 21)
CREATE TABLE "TB_RULE_ALIGNMENT_OFFSET" (
    "RULE_ID" serial PRIMARY KEY,
    "TARGET_COMPONENT" text NOT NULL,     -- 'H_BEAM', 'GIB', 'TEE', 'LEAK_CHECK_PORT'
    "REFERENCE_OBJECT" text NOT NULL,     -- 'H_BEAM_BOP', 'GRATING_BOTTOM', 'GIB_BOTTOM', 'BELLOWS_TOP_POC'
    "ALIGN_METHOD" text NOT NULL,         -- 'BOP_ALIGN', 'OFFSET_VERTICAL'
    "OFFSET_MM" double precision NOT NULL,
    "SPEC_JSON" jsonb,                    -- 컴포넌트별 상세 스펙 (NW25, NRC Clamp 등 고정 정보 수용)
    "REMARKS" text
);

CREATE UNIQUE INDEX "UX_TRAO_LOOKUP" ON "TB_RULE_ALIGNMENT_OFFSET" ("TARGET_COMPONENT");
```

---

## 3. 규칙별 DB 삽입 데이터 예시 (INSERT)

### 3.1 Clearance 규칙 (이격 / 간격)
```sql
-- Rule 1: 격자보 상부면에서 배관 외경 기준 최소 50mm 이격
INSERT INTO "TB_RULE_CLEARANCE_STANDARD" ("TARGET_TYPE_A", "TARGET_TYPE_B", "RELATION_TYPE", "UTILITY_GROUP", "MIN_DISTANCE_MM", "REMARKS")
VALUES ('LATTICE_BEAM', 'PIPE', 'VERTICAL', 'ALL', 50.0, '격자보 상부면 배관 외경 기준 최소 50mm 이격');

-- Rule 2: Takeoff 아웃라인 간격 동일선상 최소 300mm 이격
INSERT INTO "TB_RULE_CLEARANCE_STANDARD" ("TARGET_TYPE_A", "TARGET_TYPE_B", "RELATION_TYPE", "UTILITY_GROUP", "MIN_DISTANCE_MM", "REMARKS")
VALUES ('TAKEOFF_OUTLINE', 'TAKEOFF_OUTLINE', 'PARALLEL', 'EXH', 300.0, '동일선상 배치 시 아웃라인 옆면 기준 이격');

-- Rule 3: Takeoff 아웃라인 간격 비동일선상 최소 100mm 이격
INSERT INTO "TB_RULE_CLEARANCE_STANDARD" ("TARGET_TYPE_A", "TARGET_TYPE_B", "RELATION_TYPE", "UTILITY_GROUP", "MIN_DISTANCE_MM", "REMARKS")
VALUES ('TAKEOFF_OUTLINE', 'TAKEOFF_OUTLINE', 'OFFSET', 'EXH', 100.0, '상하좌우 어긋난 배치 시 이격');

-- Rule 4: Duct 벽면에서 Takeoff 아웃라인 최소 50mm 이격
INSERT INTO "TB_RULE_CLEARANCE_STANDARD" ("TARGET_TYPE_A", "TARGET_TYPE_B", "RELATION_TYPE", "UTILITY_GROUP", "MIN_DISTANCE_MM", "REMARKS")
VALUES ('DUCT_WALL', 'TAKEOFF_OUTLINE', 'ALL', 'EXH', 50.0, 'Duct 벽면에서 Takeoff 아웃라인 옆면까지 최소 이격');

-- Rule 6: SCR Bypass 배관 이격 (수직 140mm, 수평 120mm)
INSERT INTO "TB_RULE_CLEARANCE_STANDARD" ("TARGET_TYPE_A", "TARGET_TYPE_B", "RELATION_TYPE", "UTILITY_GROUP", "MIN_DISTANCE_MM", "REMARKS")
VALUES ('PIPE', 'PIPE', 'VERTICAL', 'EXH', 140.0, 'SCR Bypass 배관 간격 수직구간');
INSERT INTO "TB_RULE_CLEARANCE_STANDARD" ("TARGET_TYPE_A", "TARGET_TYPE_B", "RELATION_TYPE", "UTILITY_GROUP", "MIN_DISTANCE_MM", "REMARKS")
VALUES ('PIPE', 'PIPE', 'PARALLEL', 'EXH', 120.0, 'SCR Bypass 배관 간격 수평구간');
```

### 3.2 배관 재질/사이즈별 이격 (Rule 7)
```sql
-- [GALV] 재질 기준
INSERT INTO "TB_RULE_PIPE_SPACING" ("MATERIAL", "PIPE_SIZE", "MIN_DISTANCE_MM", "RECOMMENDED_DISTANCE_MM") VALUES
('GALV', '100A', 150.0, 170.0),
('GALV', '125A', 170.0, 200.0),
('GALV', '150A', 200.0, 230.0),
('GALV', '200A', 250.0, 280.0),
('GALV', '250A', 300.0, 330.0);

-- [구조관] 재질 기준
INSERT INTO "TB_RULE_PIPE_SPACING" ("MATERIAL", "PIPE_SIZE", "MIN_DISTANCE_MM", "RECOMMENDED_DISTANCE_MM") VALUES
('구조관', '50A', 90.0, 120.0),
('구조관', '100A', 150.0, 170.0),
('구조관', '125A', 170.0, 200.0),
('구조관', '150A', 200.0, 230.0),
('구조관', '200A', 250.0, 280.0),
('구조관', '250A', 300.0, 330.0);

-- [FM PVC] 재질 기준
INSERT INTO "TB_RULE_PIPE_SPACING" ("MATERIAL", "PIPE_SIZE", "MIN_DISTANCE_MM", "RECOMMENDED_DISTANCE_MM") VALUES
('FM PVC', '100A', 160.0, 190.0),
('FM PVC', '125A', 190.0, 220.0);
```

### 3.3 사선 배관 룰 (Rule 8, 9, 10, 11, 12, 13)
```sql
-- GAS, EXH, VAC 전체 공종 공통 적용 사선 룰
INSERT INTO "TB_RULE_OBLIQUE_ROUTING" 
("UTILITY_GROUP", "MAX_STRAIGHT_LENGTH_MM", "PREFERRED_BEND_COUNT", "MAX_BEND_COUNT", "PREFERRED_ANGLES", "FLANGE_OMISSION_LIMIT_MM", "LATTICE_PASS_MIN_CLEARANCE_MM")
VALUES ('ALL', 2000.0, 2, 4, '{30, 45, 60}', 2000.0, 50.0);
```

### 3.4 자재 매핑 및 Reducer 규격 (Rule 17, 18)
```sql
-- 배기 성상별 자재 매핑
INSERT INTO "TB_RULE_MATERIAL_MAPPING" ("EXHAUST_TYPE", "UTILITY_GROUP", "ALLOWED_MATERIALS", "ALLOWED_SIZES") VALUES
('열배기', 'EXH', '{GALV, 구조관}', '{50, 100, 125, 150, 200, 250}'),
('알카리배기', 'EXH', '{FM PVC}', '{50, 100, 125, 150, 200, 250}');

-- Reducer 치수 규격
INSERT INTO "TB_RULE_REDUCER_SPEC" ("OD1_MM", "OD2_MM", "FIXED_LENGTH_MM") VALUES
(40.0, 100.0, 100.0),
(50.0, 100.0, 100.0),
(100.0, 125.0, 130.0),
(100.0, 160.0, 130.0),
(160.0, 200.0, 152.0);
```

### 3.5 BOP 정렬 및 오프셋 설치 룰 (Rule 14, 19, 20, 21)
```sql
-- H-Beam BOP 정렬
INSERT INTO "TB_RULE_ALIGNMENT_OFFSET" ("TARGET_COMPONENT", "REFERENCE_OBJECT", "ALIGN_METHOD", "OFFSET_MM", "REMARKS")
VALUES ('H_BEAM', 'H_BEAM_BOP', 'BOP_ALIGN', 0.0, '수평 배관 설계 시 BOP를 H-Beam BOP에 맞춤');

-- GIB 고정 설치 위치
INSERT INTO "TB_RULE_ALIGNMENT_OFFSET" ("TARGET_COMPONENT", "REFERENCE_OBJECT", "ALIGN_METHOD", "OFFSET_MM", "REMARKS")
VALUES ('GIB', 'GRATING_BOTTOM', 'OFFSET_VERTICAL', -250.0, '그레이팅 하부면 기준 수직 250mm 하단 고정');

-- 분기 Tee 생성 위치
INSERT INTO "TB_RULE_ALIGNMENT_OFFSET" ("TARGET_COMPONENT", "REFERENCE_OBJECT", "ALIGN_METHOD", "OFFSET_MM", "REMARKS")
VALUES ('TEE', 'GIB_BOTTOM', 'OFFSET_VERTICAL', -300.0, 'GIB 하부면에서 300mm 하단 지점 분기');

-- Leak Check Port 설치 위치
INSERT INTO "TB_RULE_ALIGNMENT_OFFSET" ("TARGET_COMPONENT", "REFERENCE_OBJECT", "ALIGN_METHOD", "OFFSET_MM", "SPEC_JSON", "REMARKS")
VALUES ('LEAK_CHECK_PORT', 'BELLOWS_TOP_POC', 'OFFSET_VERTICAL', 150.0, 
        '{"size": "NW25", "length": 50.0, "clamp": "NRC"}'::jsonb, 
        'Bellows 상부 PoC 기준 NW25 고정 설치');
```

---

## 4. 라우팅 엔진 내의 연동 시나리오

1. **A* 알고리즘 Node 확장 시 충돌/이격 필터링**:
   - `TB_RULE_PIPE_SPACING`에서 현재 배관의 재질/사이즈에 해당하는 `MIN_DISTANCE_MM`을 쿼리하여, 주변 배관 객체와의 충돌 영역(OBB)을 확장해 간섭 검사를 진행합니다. (Rule 5와 연계)
2. **사선 경로 분기 로직 (Oblique Fallback)**:
   - 격자보 주변에서 수직 장애물로 인해 직진이 불가능한 경우, `TB_RULE_OBLIQUE_ROUTING`에서 `PREFERRED_ANGLES`(`{30, 45, 60}`) 배열을 읽어와 가장 완만한 각도 순서대로 꺾인 후보 노드를 생성합니다.
3. **자재 수량 및 스펙 자동 계산**:
   - 경로가 완성되면, 시점/종점의 사이즈 차이에 따라 `TB_RULE_REDUCER_SPEC`에서 고정 길이를 조회하여 실제 Reducer 형상(3D Box)을 모델에 자동 생성해 삽입합니다.
