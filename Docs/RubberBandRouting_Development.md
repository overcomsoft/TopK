# 3차원 고무줄 배관 라우팅 엔진 개발 보고서

본 문서는 `RubberBandRoutingSuite`의 C# 고무줄 라우팅 엔진과 WPF 3D Viewer 개발 현황을 기록한다. 현재 구현은 기존 Python PoC와 별도로 신규 작성된 C# 엔진/뷰어 기반이며, PostgreSQL의 기존 배관 경로와 PoC 데이터를 읽어 자동 배관 중심선을 생성하고 HelixToolkit 3D 뷰어에 표시한다.

> 2026-07-05 업데이트: 기존의 "시작점-종단점 직교 축 분해" 방식은 고무줄 밴딩 원리와 맞지 않아 제거하였다. 현재 엔진의 1차 경로는 시작PoC와 종단PoC를 잇는 직선 고무줄이며, 기존설계 특징점은 이 고무줄을 당기는 control point로 사용한다.

---

## 1. 개발 목적

1. PostgreSQL 설계 DB에서 장비 PoC, 덕트/레터럴 PoC, 장애물, 기존 배관 경로를 자동 조회한다.
2. 기존 배관 설계의 특징점을 활용하여 신규 자동경로가 기존 설계와 유사한 흐름을 갖도록 한다.
3. 자동설계된 경로는 다음 경로 탐색 시 장애물로 누적하여 배관끼리 겹치지 않도록 한다.
4. WPF HelixToolkit 3D Viewer에서 장애물, 공간 CubeBox, 기존경로, 자동경로, PoC, 특징점, FPS/렌더 객체 수를 확인한다.
5. 자동설계 결과 리스트, 분석결과, 단계별 경로, 세그먼트 상세를 데이터그리드로 제공한다.

---

## 2. 현재 구성

| 영역 | 주요 파일 | 역할 |
|---|---|---|
| C# Engine | `RubberBandRoutingSuite/src/RubberBandRouting.Engine/ManagedRubberBandEngine.cs` | 고무줄 control point 기반 경로 생성, 충돌 회피, 검증 |
| Engine Models | `RubberBandRoutingSuite/src/RubberBandRouting.Engine/Models.cs` | `Vec3`, `Aabb`, `RouteSegment`, `RubberBandResult` 등 데이터 구조 |
| PostgreSQL Loader | `RubberBandRoutingSuite/src/RubberBandRouting.Engine/PostgresRoutingDataLoader.cs` | 프로젝트, 장애물, PoC, 기존경로, 경로 태스크 조회 |
| WPF Viewer | `RubberBandRoutingSuite/src/RubberBandRouting.Viewer/MainWindow.xaml(.cs)` | DB 연결, 프로젝트 로딩, 3D 렌더링, 자동경로 실행/결과 표시 |
| C++ Native Stub | `RubberBandRoutingSuite/cpp/RubberBandRouting.Native` | C API 연동 검토용 네이티브 엔진 스텁 |

---

## 3. 고무줄 밴딩 알고리즘 원리

고무줄 밴딩 모델은 "처음부터 X/Y/Z 순서로 꺾는 알고리즘"이 아니다. 원리는 다음과 같다.

1. 시작PoC와 종단PoC 사이에 하나의 직선 고무줄을 건다.
2. 기존설계에서 추출한 특징점 중 현재 라우팅 조건에 맞는 점을 선택한다.
3. 선택된 특징점이 고무줄을 당기며, 경로의 중간 control point가 된다.
4. 장애물과 충돌하는 구간이 있으면 해당 위치에 우회 control point를 추가한다.
5. 최종 표시 단계에서 배관 관경과 bend radius를 반영하여 둥근 bend 형상으로 보정한다.

따라서 축 이동 순서(`X -> Y -> Z`, `Z -> X -> Y` 등)는 고무줄 알고리즘의 본질이 아니다. 축 순서 기반 생성은 단순 직교 라우터의 성격이며, 기존설계 특징점 기반 고무줄 밴딩과는 구분해야 한다.

---

## 4. 현재 구현된 라우팅 단계

### 4.1 Step 1 - Initial straight rubber tension

- 입력: 시작점 `S`, 종단점 `D`
- 처리: `S -> D`를 하나의 직선 rubber segment로 생성
- 구현: `ManagedRubberBandEngine.MakeRubberLineSegments(new[] { start, end })`
- 목적: 장애물이나 특징점을 적용하기 전의 가장 자연스러운 장력 기준선 생성

기존 구현의 문제였던 `MakeOrthogonalSegments(new[] { start, end })` 방식은 제거하였다. 이 변경으로 시작점이 종단점보다 높은 경우에도 엔진이 임의로 좌우 축 이동을 먼저 선택하지 않는다.

### 4.2 Step 2 - Pull snap by existing-design control points

- 입력: 기존 설계에서 추출된 특징점 목록
- 처리: `S -> feature1 -> feature2 -> ... -> D` 형태의 rubber control polyline 생성
- 구현: `BuildSnappedPointList()`와 `MakeRubberLineSegments()` 조합
- 표시: 뷰어 하단 `특징점` 토글로 표시/숨김 가능

특징점은 단순한 경유 좌표가 아니라, 기존 배관의 흐름을 재현하기 위해 고무줄을 당기는 control point로 취급한다.

### 4.3 Step 3 - Push collision resolution

- 입력: Step 2의 rubber segment와 장애물 AABB 목록
- 처리: 각 rubber segment와 확장 장애물 AABB의 교차 여부를 검사
- 충돌 시: 장애물 상/하 또는 측면으로 우회하는 control point를 삽입
- 구현: `ResolveCollisions()`, `SegmentIntersectsExpandedAabb()`, `BuildBypass()`

2026-07-05 수정으로 충돌 검사는 축 정렬 선분 전용 판정이 아니라 일반 3D segment-AABB 교차 판정으로 변경하였다. 따라서 rubber segment가 임의 방향이어도 장애물 충돌을 검출할 수 있다.

### 4.4 Step 4 - Final display bend correction

- 엔진 결과는 중심선 control polyline이다.
- 뷰어 표시 단계에서 배관 관경을 읽고 bend radius를 계산하여 꺾임부를 둥글게 보정한다.
- 목적은 기존설계 배관처럼 관경에 따른 bending radius가 반영된 시각 결과를 제공하는 것이다.

---

## 5. 기존설계 특징점 활용 방식

WPF Viewer는 PostgreSQL에서 기존경로(`TB_ROUTE_PATH`, `TB_ROUTE_SEGMENTS`, `TB_ROUTE_SEGMENT_DETAIL` 계열)를 조회하고, 자동경로 태스크와 가장 가까운 기존 경로를 매칭한다.

현재 특징점으로 활용하는 대표 요소는 다음과 같다.

| 특징점 유형 | 의미 | 자동경로 반영 |
|---|---|---|
| Start stub | 장비 PoC에서 배관이 처음 빠져나가는 방향/드롭 | 시작 직후 control point 후보 |
| Existing bend | 기존 배관의 방향 전환점 | 주요 꺾임 후보 |
| Z change | 고도 변경 구간 | 수직 이동 후보 |
| Trunk guide | 긴 공용 직선 배관 흐름 | 기존 배관과 유사한 주행 방향 유도 |
| End approach | 덕트/레터럴 PoC 접근 방향 | 종단부 접속 방향 보정 |

향후에는 단순 `Vec3` 목록 대신 `RouteFeature` 모델을 도입하여 각 특징점의 역할, 우선순위, 접선 방향, 필수/선택 여부를 명시하는 것이 필요하다.

---

## 6. 자동경로 누적 장애물 처리

자동설계는 태스크를 순차 실행한다. 한 경로가 생성되면 해당 경로의 배관 envelope을 AABB 장애물로 변환하여 다음 태스크의 장애물 목록에 추가한다.

이 방식으로 나중에 생성되는 경로는 앞서 생성된 자동경로를 피해야 한다. 즉, 자동설계 경로도 기존 장애물과 동일하게 collision 대상이 된다.

---

## 7. 뷰어 기능 현황

- DB 연결 정보 입력 및 프로젝트 로딩
- 유틸리티 그룹/유틸리티별 경로 태스크 필터링
- 기존 배관 표시 및 유틸리티 그룹별 색상 표시
- 선택 유틸리티만 기존배관/자동경로 대상 필터링
- 공간 CubeBox 선형 표시
- 장애물, 장비, 덕트, 레터럴, 기존배관, 자동경로, PoC, 특징점 표시 토글
- 자동설계 결과 데이터그리드 표시
- 결과 선택 시 3D View에서 선택 경로 강조 표시
- 분석결과, 단계별 경로, 세그먼트 상세 데이터그리드 표시
- FPS 및 렌더 객체 수 표시
- 프로젝트 로딩 후 ZoomFit 처리

---

## 8. 검증 상태

최근 빌드 검증:

```powershell
dotnet build .\RubberBandRoutingSuite\RubberBandRoutingSuite.sln -c Debug --nologo -v:minimal /p:UseAppHost=false /p:OutDir=C:\tmp\RubberBandRoutingSuite-build\
```

결과:

- 빌드 성공
- 오류 0개
- HelixToolkit.Wpf `NU1701` 호환성 경고 2개 존재

---

## 9. 남은 개선 항목

1. `RouteFeature` 데이터 구조 추가
   - 현재 특징점은 `Vec3` 좌표만 전달된다.
   - 시작 드롭, bend, trunk, end approach 같은 의미가 엔진에 명시적으로 전달되어야 한다.

2. 특징점 선택/스코어링 고도화
   - 기존경로의 모든 특징점을 그대로 쓰지 않고, 시작-종단 rubber line과의 거리, 유틸리티, 관경, 고도층, 기존경로 유사도를 기준으로 선별해야 한다.

3. 최종 배관 형상 검증
   - 표시 단계의 둥근 bend가 장애물과 다시 충돌하지 않는지 검증해야 한다.

4. C++ 엔진 동기화
   - 현재 C# 엔진이 우선 수정되었다.
   - C++ Native 엔진도 동일한 rubber control point 모델로 맞춰야 한다.

5. 단계별 사유 데이터 구조화
   - 현재 단계별 경로 사유는 뷰어에서 추론한다.
   - 엔진이 각 segment/control point 생성 사유를 직접 반환하도록 확장하는 것이 바람직하다.
