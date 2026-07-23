# UtilityPipeGroup Top-K Phase 6 운영·배포 가이드

## 1. 배포 구성

다음 파일을 동일 revision으로 배포한다.

```text
Tools/MigrateUtilityPipeGroupSchema.py
Tools/BuildUtilityPipeGroupVectors.py
Tools/utility_pipe_group_encoder.py
Tools/sql/create_route_utility_group_vector_tables.sql
Tools/sql/drop_route_utility_group_vector_tables.sql
Tools/requirements-utility-pipe-group.txt
Tools/ExtractBendFeaturePoints.py
Tools/geometry_ip_restore.py
Tools/sql/create_bend_feature_tables.sql
Tools/sql/drop_bend_feature_tables.sql
TopKSearchStandalone/*
TopK.3DViewer/*
Docs/ContextVector_and_TopK_User_Manual_KO.md
Docs/BendFeaturePoint_Development_Plan.md
```

`Tools/ExtractBendFeaturePoints.py`는 선택적(additive) 구성요소다. build를 아직 실행하지 않았거나
`TB_ROUTE_BEND_FEATURE_POINT`가 없는 환경에서도 그룹/개별 Top-K 검색과 Viewer는 정상 동작한다
(8절 참고) — 배포 순서에 강한 의존성을 추가하지 않는다.

비밀번호가 포함된 `Tools/tools.settings.json`과 `TopK.3DViewer/viewer.settings.json`은
환경별로 작성하며 Git이나 배포 공용 압축파일에 포함하지 않는다.

## 2. 신규 환경 구축 순서

```powershell
python -m pip install -r Tools\requirements-utility-pipe-group.txt

python Tools\MigrateUtilityPipeGroupSchema.py apply `
  --config Tools\tools.settings.json

python Tools\MigrateUtilityPipeGroupSchema.py verify `
  --config Tools\tools.settings.json

python Tools\BuildUtilityPipeGroupVectors.py build `
  --config Tools\tools.settings.json --scope-mode active --min-members 2

python Tools\BuildUtilityPipeGroupVectors.py validate `
  --config Tools\tools.settings.json --scope-mode active

python Tools\ExtractBendFeaturePoints.py create-schema --config Tools\tools.settings.json
python Tools\ExtractBendFeaturePoints.py build --config Tools\tools.settings.json --min-samples 3

dotnet build TopKSearchStandalone\TopKSearchStandalone.csproj -c Release
dotnet build TopK.3DViewer\TopK.3DViewer.csproj -c Release
```

`ExtractBendFeaturePoints.py build`는 기본값 `--scope-mode active`로 `BuildUtilityPipeGroupVectors.py`와
동일한 ACTIVE scope를 사용하므로, 반드시 같은 scope/revision일 때만 그룹 검색의 Pattern
Bend 축이 실제로 매칭된다(8절). 두 build 사이의 실행 순서 자체는 서로 독립적이다(서로 다른
테이블에 쓰며 서로를 읽지 않음) — 편의상 그룹 Vector build 다음에 두었을 뿐이다.

## 3. 배포 후 smoke test

```powershell
python Tools\BuildUtilityPipeGroupVectors.py status `
  --config Tools\tools.settings.json --scope-mode active

dotnet run --project TopKSearchStandalone\TopKSearchStandalone.csproj -c Release --no-build -- `
  --group-self-test

dotnet run --project TopKSearchStandalone\TopKSearchStandalone.csproj -c Release --no-build -- `
  --group-search --process CVD --equipment TNMHJ04 `
  --utility-group VACCUM --utility FORELINE --k 5 --dbname DDW_AI_DB

python Tools\ExtractBendFeaturePoints.py status --config Tools\tools.settings.json
```

Viewer 실행:

```powershell
& .\TopK.3DViewer\bin\Release\net8.0-windows\TopK.3DViewer.exe
```

## 4. 정기 운영

Route, Feature Vector, Context Vector 또는 ACTIVE revision이 바뀌면 그룹 Vector를 증분 생성한다.

```powershell
python Tools\BuildUtilityPipeGroupVectors.py build `
  --config Tools\tools.settings.json --scope-mode active

python Tools\BuildUtilityPipeGroupVectors.py validate `
  --config Tools\tools.settings.json --scope-mode active
```

변경 없는 `SOURCE_HASH`의 READY 그룹은 skip되고, 원본에서 사라진 그룹은 STALE로 전환된다.

## 5. 모니터링 기준

| 항목 | 기준 |
|---|---|
| ACTIVE scope | 정확히 1개 |
| READY 그룹 | 원본 프로파일 예상 범위와 일치 |
| Feature coverage | 유효 그룹 100% 권장 |
| Context coverage | ACTIVE Context 생성 대상 100% 권장 |
| 자기검색 제외 | 100% |
| 동일 입력 결정론 | 100% |
| 그룹 검색 P95 | 2초 이내 |
| Viewer K=5 | 일반 그룹 3초 이내 |
| 꺾임특징점 UNKNOWN 비율 | 20% 미만 (`ExtractBendFeaturePoints.py status`) — 초과 시 8절 참고 |

## 6. 장애 대응

| 오류 | 조치 |
|---|---|
| ACTIVE scope 0개/복수 | Context lifecycle의 READY revision 하나를 ACTIVE로 승격 |
| 그룹 테이블 없음 | Migration apply 후 verify |
| READY 그룹 없음 | 그룹 build 로그의 원본 coverage와 장비 키 확인 |
| 후보가 K보다 적음 | 다른 Utility를 섞지 않고 실제 가용 개수만 사용하는 정상 동작인지 확인 |
| Vector dimension 오류 | pgvector와 Feature/Context 30D 계약 확인 |
| Viewer 시작 오류 | Release 재빌드, Windows Application/.NET Runtime 로그 확인 |
| Viewer 빈 3D | 프리셋 선택, 카메라 맞춤, Route GUID 상세 연결 확인 |
| TB_ROUTE_BEND_FEATURE_POINT 없음(42P01) | 정상 fallback(8절) — 그룹/개별 검색과 Viewer는 계속 동작. 필요 시 build 실행 |
| 그룹 검색 Pattern 축이 bendFeature 반영 안 됨 | 두 build의 scope/revision 불일치 여부 확인 (`--scope-mode explicit`로 동일 값 지정) |

## 7. Rollback

1. Viewer에서 개별 배관 모드를 사용하면 신규 그룹 검색 기능을 사용하지 않을 수 있다.
2. 애플리케이션 rollback은 이전 Release 바이너리로 교체한다.
3. 신규 그룹 테이블 제거는 데이터 손실 작업이므로 백업과 별도 승인을 받은 뒤에만
   `Tools/sql/drop_route_utility_group_vector_tables.sql`을 검토·실행한다.
4. 그룹 테이블은 기존 개별 Top-K 테이블과 독립적이므로 제거해도 기존 개별 검색 데이터는
   변경되지 않는다.

## 8. Bend Feature Point 연동 (선택 구성요소)

`Docs/BendFeaturePoint_Development_Plan.md`에서 설계한 개별 꺾임점(원인 분류 포함) 파이프라인을
그룹 Top-K 검색과 Viewer에 연동했다. 이 절은 두 시스템 사이의 실제 의존 관계를 정리한다.

1. **데이터 연결**: `TB_ROUTE_BEND_FEATURE_POINT`에 `PROJECT_SCOPE_KEY`/`MODEL_REVISION_KEY`가
   있어야 그룹 검색이 올바른 scope의 꺾임점만 조회한다(v1.2). 구버전 DB에 남아있는 빈 문자열
   scope 행은 다음 build 전까지 조회 대상에서 자연히 제외된다.
2. **검색 반영 지점**: `UtilityPipeGroupMatcher.ScorePair()`의 Pattern 항목이, 두 멤버 모두
   꺾임점 데이터가 있으면 `구조(RLE Levenshtein) : coarse Feature[12:20] cosine : cause-aware
   꺾임점 시퀀스매칭 = 0.34 : 0.33 : 0.33`으로 섞이고, 한쪽이라도 없으면 기존
   `구조 : coarse = 0.5 : 0.5`로 자동 fallback한다(코드 주석 참고, `UtilityPipeGroupMatcher.cs`).
   개별 Route 검색(`TopKSearchStandalone.SearchAsync`)의 Pattern 축은 이번 변경에 포함하지
   않았다 — 그 경로는 좌표만 있는 Query라 꺾임점 시퀀스를 구성할 Query 폴리라인이 없다.
3. **Viewer 반영 지점**: `TopK.3DViewer`의 "꺾임원인 표시" 체크박스(기본 켜짐)가 현재 화면의
   Query/선택 Candidate 배관에 대해 `TB_ROUTE_BEND_FEATURE_POINT`를 조회해 원인별 색상 구
   마커로 표시한다(주황=장애물회피, 초록=목적지진입, 파랑=구역경계, 보라=그룹정렬, 회색=미분류).
4. **Fallback 보장**: 두 지점 모두 테이블 부재(42P01) 또는 빈 결과를 예외로 처리하지 않고
   조용히 무시한다 — Bend Feature Point build를 아직 실행하지 않은 환경에서도 그룹/개별
   Top-K 검색과 Viewer 3D 비교 화면은 기존과 동일하게 동작해야 한다.
5. **실행 순서**: `ExtractBendFeaturePoints.py build`와 `BuildUtilityPipeGroupVectors.py build`는
   서로 다른 테이블에 독립적으로 쓰므로 실행 순서 자체는 무관하다. 다만 두 build가 서로 다른
   `--scope-mode explicit` 값으로 실행되면(예: 서로 다른 PROJECT_SCOPE_KEY) 그룹 검색이 꺾임점을
   찾지 못해 자동으로 2번 항목의 fallback 경로로 빠진다 — 오류는 아니지만 의도한 정밀도가
   나오지 않으므로 6절 장애 대응 표를 참고해 scope 일치 여부를 먼저 확인한다.

## 9. 관련 문서

- `Docs/UtilityPipeGroup_TopK_Phase0_Data_Profile.md`
- `Docs/UtilityPipeGroup_TopK_Phase1_Schema_Implementation.md`
- `Docs/UtilityPipeGroup_TopK_Phase2_Vector_Builder.md`
- `Docs/UtilityPipeGroup_TopK_Phase3_Search_API.md`
- `Docs/UtilityPipeGroup_TopK_Phase4_3DViewer.md`
- `Docs/UtilityPipeGroup_TopK_Phase5_Evaluation.md`
- `Docs/ContextVector_and_TopK_User_Manual_KO.md`
- `Docs/BendFeaturePoint_Development_Plan.md` (개별 꺾임점 추출/원인분류/빈도집계 설계와 8절에서
  다룬 Top-K/Viewer 연동 지점)
