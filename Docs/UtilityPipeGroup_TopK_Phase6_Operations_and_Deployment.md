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
TopKSearchStandalone/*
TopK.3DViewer/*
Docs/ContextVector_and_TopK_User_Manual_KO.md
```

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

dotnet build TopKSearchStandalone\TopKSearchStandalone.csproj -c Release
dotnet build TopK.3DViewer\TopK.3DViewer.csproj -c Release
```

## 3. 배포 후 smoke test

```powershell
python Tools\BuildUtilityPipeGroupVectors.py status `
  --config Tools\tools.settings.json --scope-mode active

dotnet run --project TopKSearchStandalone\TopKSearchStandalone.csproj -c Release --no-build -- `
  --group-self-test

dotnet run --project TopKSearchStandalone\TopKSearchStandalone.csproj -c Release --no-build -- `
  --group-search --process CVD --equipment TNMHJ04 `
  --utility-group VACCUM --utility FORELINE --k 5 --dbname DDW_AI_DB
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

## 7. Rollback

1. Viewer에서 개별 배관 모드를 사용하면 신규 그룹 검색 기능을 사용하지 않을 수 있다.
2. 애플리케이션 rollback은 이전 Release 바이너리로 교체한다.
3. 신규 그룹 테이블 제거는 데이터 손실 작업이므로 백업과 별도 승인을 받은 뒤에만
   `Tools/sql/drop_route_utility_group_vector_tables.sql`을 검토·실행한다.
4. 그룹 테이블은 기존 개별 Top-K 테이블과 독립적이므로 제거해도 기존 개별 검색 데이터는
   변경되지 않는다.

## 8. 관련 문서

- `Docs/UtilityPipeGroup_TopK_Phase0_Data_Profile.md`
- `Docs/UtilityPipeGroup_TopK_Phase1_Schema_Implementation.md`
- `Docs/UtilityPipeGroup_TopK_Phase2_Vector_Builder.md`
- `Docs/UtilityPipeGroup_TopK_Phase3_Search_API.md`
- `Docs/UtilityPipeGroup_TopK_Phase4_3DViewer.md`
- `Docs/UtilityPipeGroup_TopK_Phase5_Evaluation.md`
- `Docs/ContextVector_and_TopK_User_Manual_KO.md`
