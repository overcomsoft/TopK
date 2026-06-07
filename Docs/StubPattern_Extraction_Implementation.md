# Stub 패턴 추출/저장/활용 기능 구현 문서

## 1. 구현 개요

본 기능은 DDW_AI_DB의 기존 설계 배관 데이터를 사용하여 Start Stub과 End Stub의 배관 패턴을 추출하고, 이를 DB에 저장한 뒤 신규 자동배관설계에서 후보 Stub으로 재사용하기 위한 Python 도구이다.

구현 파일:

- `Tools/ExtractStubPatterns.py`
- `Tools/sql/create_route_stub_pattern_tables.sql`

기존 공통 설정:

- `Tools/tool_config.py`
- `Tools/tools.settings.json` 또는 CLI 인자

## 2. 주요 기능

| 기능 | Subcommand | 설명 |
| --- | --- | --- |
| 스키마 생성 | `create-schema` | Stub 패턴 저장 테이블 생성 |
| 기존 배관에서 추출 | `extract` | `TB_ROUTE_PATH/SEGMENTS/SEGMENT_DETAIL`에서 Start/End Stub 추출 |
| 템플릿 집계 | `build-template` | 개별 Stub sample을 조건별 template으로 집계 |
| 일괄 실행 | `run-all` | schema 생성, 추출, 템플릿 집계를 한 번에 수행 |
| 패턴 조회 | `query-template` | 메인장비/유틸리티그룹/유틸리티/사이즈 기준 template 조회 |
| 신규 Stub 생성 | `make-stub` | 신규 source/target PoC에 맞는 Start/End Stub 후보 생성 |
| 기존 경로 검증 | `validate-existing-route` | 특정 route path에서 추출 결과를 JSON으로 확인 |

## 3. 저장 테이블

### 3.1 `TB_ROUTE_STUB_PATTERN`

기존 route path 하나에서 추출한 Start/End Stub sample을 저장한다.

주요 컬럼:

- `PATTERN_ID`
- `ROUTE_PATH_GUID`
- `STUB_KIND`: `START` 또는 `END`
- `ANCHOR_KIND`: `EQUIP`, `DUCT`, `LATERAL`
- `MAIN_EQUIPMENT_NAME`
- `UTILITY_GROUP`
- `UTILITY`
- `SIZE`
- `FACE`
- `DIR_SEQ`
- `N_BENDS`
- `RISE_MM`
- `OFFSET_MM`
- `DIAMETER_MM`
- `STUB_LENGTH_MM`
- `STUB_POINTS`
- `FEAT`: pgvector 사용 가능 시 `vector(24)`
- `DIR_UNIT`: pgvector 사용 가능 시 `vector(3)`
- `FEAT_JSON`, `DIR_UNIT_JSON`: fallback 및 디버깅용

### 3.2 `TB_ROUTE_STUB_TEMPLATE`

Stub sample을 그룹화한 재사용 template 저장 테이블이다.

그룹 기준:

- `STUB_KIND`
- `ANCHOR_KIND`
- `MAIN_EQUIPMENT_NAME`
- `UTILITY_GROUP`
- `UTILITY`
- `SIZE`
- `FACE`
- `DIR_SEQ`

### 3.3 `TB_ROUTE_STUB_APPLICATION_LOG`

신규 자동배관설계에 Stub template을 적용한 이력을 저장한다.

## 4. 추출 알고리즘

PDF 문서의 규칙을 반영해 다음 순서로 처리한다.

1. `ROUTE_PATH_GUID`별로 segment/detail 좌표를 읽어 폴리라인을 복원한다.
2. Start Stub은 `SOURCE_POS`가 앞에 오도록 정렬한다.
3. End Stub은 `TARGET_POS`가 앞에 오도록 역방향 정렬한다.
4. 각 세그먼트 방향을 6축 `+x,-x,+y,-y,+z,-z` 중 하나로 스냅한다.
5. 연속 동일 방향을 run으로 압축한다.
6. `250mm` 미만 run은 설계 지터로 보고 인접 run에 흡수한다.
7. 첫 run의 축을 수직축으로 보고, 축이 달라지는 첫 run을 엘보로 판단한다.
8. Stub 길이는 `수직 run + min(첫 엘보 run, 800mm)`로 자른다.
9. 전체 Stub 길이는 최대 `4000mm`로 제한한다.
10. Anchor AABB 기준 face, rise, offset, 24D feature, 3D direction unit을 계산한다.

## 5. 실행 예시

### 5.1 스키마 생성

```powershell
python Tools\ExtractStubPatterns.py create-schema `
  --host localhost `
  --port 5432 `
  --dbname DDW_AI_DB `
  --user postgres `
  --password <password>
```

### 5.2 Dry-run 추출

DB 저장 없이 10개 route만 추출한다.

```powershell
python Tools\ExtractStubPatterns.py extract `
  --host localhost `
  --dbname DDW_AI_DB `
  --user postgres `
  --password <password> `
  --limit 10 `
  --dry-run `
  --export-json data\output\stub_samples_preview.json
```

### 5.3 전체 추출 및 저장

```powershell
python Tools\ExtractStubPatterns.py extract `
  --config Tools\tools.settings.json `
  --main-equipment WTNHJ02 `
  --utility-group Water `
  --replace
```

### 5.4 템플릿 집계

```powershell
python Tools\ExtractStubPatterns.py build-template `
  --config Tools\tools.settings.json `
  --min-samples 3 `
  --replace
```

### 5.5 전체 실행

```powershell
python Tools\ExtractStubPatterns.py run-all `
  --config Tools\tools.settings.json `
  --limit 1000 `
  --min-samples 3 `
  --replace
```

## 6. 신규 자동배관설계 활용

### 6.1 Template 조회

```powershell
python Tools\ExtractStubPatterns.py query-template `
  --config Tools\tools.settings.json `
  --main-equipment WTNHJ02 `
  --utility-group Water `
  --utility PCWS `
  --size 40A `
  --stub-kind START `
  --max-candidates 5
```

조회 fallback 우선순위:

1. 메인장비 + 유틸리티그룹 + 유틸리티 + 사이즈
2. 메인장비 + 유틸리티그룹 + 유틸리티
3. 유틸리티그룹 + 유틸리티
4. 유틸리티그룹

### 6.2 신규 PoC에 Stub 후보 생성

```powershell
python Tools\ExtractStubPatterns.py make-stub `
  --config Tools\tools.settings.json `
  --main-equipment WTNHJ02 `
  --utility-group Water `
  --utility PCWS `
  --size 40A `
  --source-pos 1000,2000,3000 `
  --target-pos 7000,9000,4500 `
  --source-anchor-min 500,1500,2500 `
  --source-anchor-max 1500,2500,3500 `
  --target-anchor-min 6500,8500,4000 `
  --target-anchor-max 7500,9500,5000 `
  --max-candidates 5 `
  --export-json data\output\stub_candidates.json
```

출력 구조:

- `start_stub.points`
- `end_stub.points`
- `middle_route`
- `score`
- `template_id`

중간 자동 라우팅 엔진은 `start_stub.free_point`와 `end_stub.free_point`를 시작/종료점으로 사용하면 된다.

## 7. 검증 결과

개발 중 수행한 검증:

```powershell
python -m py_compile Tools\ExtractStubPatterns.py
python Tools\ExtractStubPatterns.py --help
python Tools\ExtractStubPatterns.py extract --host localhost --port 5432 --dbname DDW_AI_DB --user postgres --password dinno --limit 1 --dry-run
```

Dry-run 결과:

- route 1건 로드
- equipment anchor 476건 로드
- target anchor 879건 로드
- Start Stub 1건 추출
- End Stub은 해당 샘플에서 anchor 매칭 실패로 스킵

## 8. 향후 보완 포인트

- End Stub anchor 매칭 정확도 향상
  - target PoC가 `TB_DUCT`, `TB_LATERAL_PIPE` AABB 밖에 있는 경우 PoC ID 기반 매칭 추가
- collision check 연동
  - Stub candidate와 BIM obstacle 간섭 검사
- 중간 라우팅 엔진과 직접 연동
  - 현재는 `middle_route` 시작/종료점 JSON 제공 단계
- Template score 고도화
  - sample count 외에 방향 적합도, target 방향, 충돌 가능성, 길이 penalty 반영

