# learn_design_features.py 수정 결과 보고서

작성일: 2026-06-14 21:41:19

## 수정 목적

기존 구현은 PoC 주변 스텁을 단순히 “메인장비에서 하향 벡터”, “덕트/레터럴에서 상향 벡터”처럼 방향만 추정하는 수준이었습니다. 이 방식은 실제 사람이 설계한 배관의 시작부와 종단부 형상을 충분히 반영하지 못합니다.

이번 수정은 기존 설계 경로의 실제 폴리라인을 기준으로 다음 구간을 학습하도록 보완했습니다.

- 메인장비 START 스텁: 장비 PoC부터 실제 경로를 따라 첫 엘보 및 진입 직선 구간까지
- 덕트/레터럴 END 스텁: 덕트 또는 레터럴 PoC부터 실제 경로를 역방향으로 따라 첫 엘보까지

## 수정 파일

- D:\DINNO\DEV\AI-AutoRouting\TopKGen\Tools\learn_design_features.py

## 핵심 수정 내용

### 1. 실제 경로 기반 스텁 템플릿 학습 연결

`ExtractStubPatterns.py`의 기존 스텁 추출 알고리즘을 `learn_design_features.py` 학습 파이프라인에 연결했습니다.

- START 스텁은 장비 PoC에서 실제 배관 경로를 따라가며 첫 방향 전환점, 즉 첫 엘보를 찾습니다.
- END 스텁은 덕트/레터럴 PoC에서 경로를 역방향으로 따라가며 첫 엘보를 찾습니다.
- 추출된 스텁은 `TB_ROUTE_STUB_PATTERN`, `TB_ROUTE_STUB_TEMPLATE`에 저장됩니다.
- 자동설계용 통합 특징 테이블인 `route_feature_stub_template`에도 미러링됩니다.

### 2. Anchor/PoC 특징 저장 보완

`route_feature_anchor` 테이블 DDL 오류를 수정했습니다. 기존에는 `CREATE TABLE` 문이 주석에 붙어 실제 생성이 깨질 수 있었습니다.

저장 항목은 anchor 종류, 접속 face, PoC 좌표, 첫 엘보 좌표, PoC부터 첫 엘보 구간까지의 stub point 배열입니다.

### 3. 장애물과 경로 관계 특징 추가

`TB_BIM_OBSTACLE`의 AABB 정보를 읽어 배관 경로와 가까운 장애물 관계를 분석하도록 추가했습니다.

신규 테이블은 `route_feature_obstacle_relation`입니다.

저장 특징은 장애물 종류 및 주축, 최근접 거리, 필요 이격거리, 이격 여유, 우회 방향, 통과 축, 장애물 주변 Z 변화량, 장애물 전후 bend 개수, 직선거리 대비 실제 경로 길이 비율, 장애물-경로 연관 점수입니다.

### 4. 번들/척추선 템플릿 저장 추가

유틸리티 그룹별 공통 주행축, 선호 rack Z, trunk centerline, 구성 route 목록을 `route_feature_bundle_template`에 저장하도록 추가했습니다.

이 정보는 향후 자동배관에서 개별 배관을 독립적으로 라우팅하는 대신, 장비/유틸리티 그룹별 공통 간선으로 유도하는 데 사용할 수 있습니다.

### 5. DB 스키마 생성 안정화

- PostGIS 생성 구문이 주석 처리되어 있던 문제를 수정했습니다.
- `prepare_tables()`에서 PostGIS와 pgvector 활성화 순서를 분리했습니다.
- 기존 DB에 누락 컬럼이 있을 때 `route_feature_anchor`의 JSON 컬럼을 마이그레이션하도록 추가했습니다.

### 6. 보안/운영 보완

DB 비밀번호 기본값은 코드 고정값만 쓰지 않고 `DDW_AI_DB_PASSWORD` 환경변수를 우선 사용하도록 수정했습니다.

## 자동설계 적용 방식

1. 장비, 유틸리티 그룹, 유틸리티, 사이즈 조건으로 `route_feature_stub_template`에서 START/END 스텁 후보를 조회합니다.
2. 장비 PoC에는 START 템플릿을 적용하여 장비에서 실제 설계와 유사하게 내려오는 스텁을 생성합니다.
3. 덕트 또는 레터럴 PoC에는 END 템플릿을 적용하여 PoC에서 첫 엘보까지의 실제 설계형 스텁을 생성합니다.
4. 두 스텁의 free point 사이를 `route_feature_bundle_template`의 trunk/rack 선호 정보와 `route_feature_obstacle_relation`의 장애물 회피 특징을 비용 함수로 반영해 연결합니다.
5. 최종 경로는 단순 최단거리보다 기존 사람이 설계한 시작/종단부, 공통 간선, 장애물 회피 습관을 더 강하게 따르게 됩니다.

## 검증 결과

- Python AST 문법 검사: 통과
- CLI 로딩 검사: `python learn_design_features.py --help` 정상 출력

## 남은 보완 사항

- 실제 DB에 연결하여 `--project <프로젝트명>` 실행 검증이 필요합니다.
- 이 단계는 `route_feature_*`, `TB_ROUTE_STUB_*` 테이블을 생성/갱신하므로 운영 DB에서는 백업 또는 테스트 DB에서 먼저 수행하는 것을 권장합니다.
- `TB_BIM_OBSTACLE`의 `OST_TYPE`, `DDWORKS_TYPE` 값이 프로젝트마다 다르면 장애물 분류 규칙을 현장 데이터명에 맞춰 추가 튜닝해야 합니다.
- 스텁 템플릿은 통합 학습 파이프라인에서 `min_samples=1` 기준으로 우선 집계합니다. 데이터가 충분히 쌓인 프로젝트에서는 3~5 이상으로 올리면 더 보수적인 자동설계 패턴을 만들 수 있습니다.

## 추가 런타임 수정 내역

작성일: 2026-06-14 21:53:34

`python Tools/learn_design_features.py --project all` 실행 중 발견된 오류를 추가 수정했습니다.

### 1. `KeyError: 'guid'` 수정

`self.routes` 구조에서 GUID는 `r['guid']`에 있고 `r['meta']['guid']`에는 없습니다. 새로 추가한 anchor, obstacle, bundle 저장 로직에서 잘못 참조하던 `meta['guid']`를 `r['guid']`로 수정했습니다.

### 2. 기존 테이블 unique index 마이그레이션 추가

기존 DB에 이미 생성된 `route_feature_anchor`, `route_feature_stub_template`, `route_feature_bundle_template` 테이블에는 신규 UPSERT가 요구하는 unique index가 없을 수 있습니다. `prepare_tables()`에서 중복 row를 정리한 뒤 필요한 unique index를 생성하도록 보완했습니다.

### 3. 스텁 추출 route 필터 보완

`ExtractStubPatterns.py`가 `EQUIPMENT_NAME` 컬럼만 기준으로 필터링하던 문제를 수정했습니다. 이제 `EQUIPMENT_NAME`, `EQUIPMENT_TAG`, `SOURCE_OWNER_NAME`, `SOURCE_EQUIPMENT_NAME` 후보 컬럼을 OR 조건으로 검색합니다.

### 4. 스텁 템플릿 빌드 기준 보완

프로젝트명과 실제 route의 `MAIN_EQUIPMENT_NAME`이 다를 수 있어, 추출된 샘플의 실제 메인장비명을 기준으로 템플릿을 빌드하고 현재 프로젝트 ID 아래에 미러링하도록 수정했습니다.

### 5. `DIR_SEQ` JSON 변환 오류 수정

`DIR_SEQ` 값은 `-y,-z` 같은 문자열이므로 JSONB에 직접 캐스팅할 수 없습니다. `dir_seq_json`에는 `['-y', '-z']` 형태의 JSON 배열로 변환해 저장하도록 수정했습니다.

## 재검증 결과

다음 명령으로 단일 프로젝트 검증을 완료했습니다.

```powershell
python Tools/learn_design_features.py --project "CHILLER 002" --report false
```

결과:

- route 로드: 2개
- 스텁 샘플: 4개
- 스텁 템플릿: 4개
- 장애물-경로 관계: 321개
- 최종 DB 저장 완료

전체 `--project all --report false` 검증은 5분 제한을 초과하여 중단했습니다. 단일 프로젝트 기준으로는 이번 오류들이 해결된 상태입니다.

## 대문자 테이블/필드명 반영 내역

작성일: 2026-06-14 22:01:52

기존 DDW_AI_DB의 명명 규칙에 맞춰 신규 생성 특징 테이블과 필드명을 모두 대문자 quoted identifier 형식으로 변경했습니다.

변경된 신규 테이블:

- `TB_ROUTE_FEATURE_PATH`
- `TB_ROUTE_FEATURE_ANCHOR`
- `TB_ROUTE_FEATURE_STUB_TEMPLATE`
- `TB_ROUTE_FEATURE_BUNDLE_TEMPLATE`
- `TB_ROUTE_FEATURE_OBSTACLE_RELATION`
- `TB_ROUTE_FEATURE_GROUP_PROFILE`

예시 필드명:

- `PROJECT_ID`
- `ROUTE_PATH_GUID`
- `MAIN_EQUIPMENT_NAME`
- `UTILITY_GROUP`
- `GEOM_3D`
- `ANCHOR_KIND`
- `DIR_SEQ_JSON`
- `TRUNK_CENTERLINE_GEOM`

수정 범위:

- DDL 생성문
- `prepare_tables()` 마이그레이션 및 unique index 생성문
- 개별 경로 저장 SQL
- Anchor/PoC 저장 SQL
- 장애물 관계 저장 SQL
- 스텁 템플릿 미러링 SQL
- 번들 템플릿 저장 SQL
- 그룹 프로파일 저장 SQL

검증 결과:

- Python AST 문법 검사 통과
- `python Tools/learn_design_features.py --project "CHILLER 002" --report false` 실행 성공
- `information_schema.columns` 기준 신규 테이블/컬럼명이 대문자로 생성된 것 확인
