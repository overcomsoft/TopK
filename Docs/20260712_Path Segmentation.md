**배관 경로 삼분할 (Path Segmentation)**  
**개발 문서**

DDW AI AutoRouting System | v1.3 | 2026-07-12

**1\. 개요 (Overview)**  
반도체 팹 배관 자동설계 시스템에서 기존 설계 배관 경로(Route Path) 하나를 엔지니어링 의미 단위인 세 구간으로 분할합니다.

| 구간 | 색상 | 정의 |
| ----- | ----- | ----- |
| Start Stub(시작 인입부) | 🟠 주황 | 장비 PoC → A/F 구역 수평 이동 → 격자보 관통 수직 하강 → CSF 진입점 |
| Middle Trunk(중앙 본선) | 🟢 녹색 | Start Free Point → End Free Point 사이의 주 배관 구간 |
| End Stub(종단 도출부) | 🔵 청색 | 덕트/레터럴 PoC → 댐퍼 통과 → 첫 번째 엘보(방향 전환점) |

**경로 구조: \[장비 PoC\] ──Start Stub──\[Start FP\]═══Middle Trunk═══\[End FP\]──End Stub──\[덕트 PoC\]**

**2\. 원본 데이터 (Source Data)**

**2.1 주요 원본 테이블**

| 테이블명 | 역할 | 주요 컬럼 |
| ----- | ----- | ----- |
| TB\_ROUTE\_PATH | 배관 경로 메타데이터 | ROUTE\_PATH\_GUID (PK), UTILITY\_GROUP, SOURCE\_UTILITY, SOURCE\_SIZE, EQUIPMENT\_NAME, TARGET\_OWNER\_NAME, SOURCE\_POSx/y/z, TARGET\_POSx/y/z |
| TB\_ROUTE\_SEGMENTS | 경로 내 세그먼트 목록 | SEGMENT\_GUID (PK), ROUTE\_PATH\_GUID (FK), ORDER |
| TB\_ROUTE\_SEGMENT\_DETAIL | 세그먼트 상세 좌표 | SEGMENT\_GUID (FK), ORDER, FROM\_POSx/y/z, TO\_POSx/y/z, TYPE |
| TB\_EQUIPMENTS | 장비 AABB 정보 | INSTANCE\_NAME, AABB\_MIN/MAX x/y/z |
| TB\_DUCT | 덕트 AABB 정보 | INSTANCE\_NAME, AABB\_MIN/MAX x/y/z |
| TB\_LATERAL\_PIPE | 레터럴 배관 정보 | INSTANCE\_NAME, AABB\_MIN/MAX x/y/z |

**2.2 컬럼 상세 설명**

| 테이블 | 컬럼 | 설명 |
| ----- | ----- | ----- |
| TB\_ROUTE\_PATH | ROUTE\_PATH\_GUID | 경로 식별자 (UUID) |
| TB\_ROUTE\_PATH | SOURCE\_POSx/y/z | 장비 측 시작 PoC 좌표 (단위: mm) |
| TB\_ROUTE\_PATH | TARGET\_POSx/y/z | 덕트/레터럴 측 종단 PoC 좌표 (단위: mm) |
| TB\_ROUTE\_PATH | UTILITY\_GROUP | 유틸리티 그룹 (Gas, Water, Exhaust …) |
| TB\_ROUTE\_PATH | SOURCE\_SIZE | 배관 구경 문자열 (예: "1/2inch", "1inch") |
| TB\_ROUTE\_SEGMENTS | ORDER | 세그먼트 순서 (오름차순 정렬 기준) |
| TB\_ROUTE\_SEGMENT\_DETAIL | ORDER | 세그먼트 내 상세 좌표 순서 |
| TB\_ROUTE\_SEGMENT\_DETAIL | FROM\_POS / TO\_POS | 각 상세 선분의 시작/끝 3D 좌표 |
| TB\_ROUTE\_SEGMENT\_DETAIL | TYPE | 세그먼트 유형 (PIPE, ELBOW, FITTING …) |

**2.3 공간 구역(Spatial Zone) 정의**  
팹 설비 공간은 수직으로 다음 구역들로 구분됩니다. Z \= 13,700 mm 가 Start Stub / Middle Trunk 분할 기준 경계값입니다.

| 구역 | 높이(Z) 범위 | 역할 |
| ----- | ----- | ----- |
| CR (Clean Room) | Z ≥ 15,235 mm | 반도체 공정 장비 설치 공간 |
| A/F (Air Flow) | 13,700 \< Z \< 15,235 mm | 공조 구역, 수평 배관이 집중되는 층 |
| CSF (Clean Sub-Fab) | Z ≤ 13,700 mm | 하부 설비 공간, Middle Trunk 구간 |

**3\. 변환 단계별 프로세스**

**3.1 전체 파이프라인**  
① DB 조회 (TB\_ROUTE\_PATH ⋈ TB\_ROUTE\_SEGMENTS ⋈ TB\_ROUTE\_SEGMENT\_DETAIL)  
 ↓ load\_route\_data\_bulk()  
② 폴리라인 복원 (FROM/TO 좌표 순서 연결, 중복점 제거 1mm 임계값)  
 ↓  
③ ELBOW IP 복원 (인접 직선 연장선 교차점 수학 계산)  
 ↓ segment\_route(points)  
④ 삼분할 알고리즘 실행  
 ├─ Start Stub: CSF 경계 탐색 (Z ≤ 13,700) → Fallback: 수직런 탐색  
 ├─ End Stub: 종단 역방향 스캔 → 첫 방향 전환 엘보 정점  
 └─ Middle Trunk: points\[start\_idx .. end\_idx\]  
 ↓ WKT 변환  
⑤ PostGIS LINESTRING Z / POINT Z 직렬화  
 ↓ UPSERT (page\_size=200)  
⑥ TB\_ROUTE\_PATH\_SEGMENTATION 저장

**3.2 Step 1 — 폴리라인 복원**  
아래 SQL로 모든 경로의 세그먼트 좌표를 한 번에 조회합니다:

SELECT  
 rp."ROUTE\_PATH\_GUID",  
 sd."FROM\_POSX", sd."FROM\_POSY", sd."FROM\_POSZ",  
 sd."TO\_POSX", sd."TO\_POSY", sd."TO\_POSZ"  
FROM "TB\_ROUTE\_PATH" rp  
JOIN "TB\_ROUTE\_SEGMENTS" rs  
 ON rp."ROUTE\_PATH\_GUID" \= rs."ROUTE\_PATH\_GUID"  
JOIN "TB\_ROUTE\_SEGMENT\_DETAIL" sd  
 ON rs."SEGMENT\_GUID" \= sd."SEGMENT\_GUID"  
ORDER BY rp."ROUTE\_PATH\_GUID", rs."ORDER", sd."ORDER"

조회 결과를 Python에서 폴리라인으로 재구성할 때, 1mm 이하 중복점을 제거합니다:

pts \= \[\]  
for d in details:  
 pt\_from \= (float(d\["FROM\_POSX"\]), float(d\["FROM\_POSY"\]), float(d\["FROM\_POSZ"\]))  
 pt\_to \= (float(d\["TO\_POSX"\]), float(d\["TO\_POSY"\]), float(d\["TO\_POSZ"\]))  
 if not pts:  
 pts.append(pt\_from)  
 elif dist(pts\[-1\], pt\_from) \> 1e-3: \# 1mm 이하 중복점 제거  
 pts.append(pt\_from)  
 if dist(pts\[-1\], pt\_to) \> 1e-3:  
 pts.append(pt\_to)

**3.3 Step 2 — ELBOW IP 복원**  
BIM 원본 데이터에서 엘보 구간(TYPE="ELBOW")은 인접 직선 세그먼트의 연장선 교차점(Intersection Point, IP)을 수학적으로 복원합니다.

인접 직선 세그먼트: prev\_seg ──\[elbow\_seg\]── next\_seg

교차점 복원:  
 v1 \= normalize(prev\_seg.to \- prev\_seg.from)  
 v2 \= normalize(next\_seg.to \- next\_seg.from)  
 → 최근접점 q1, q2 계산  
 ip \= (q1 \+ q2) / 2 \[조건: skew\_dist \< 500mm\]

결과: IP가 폴리라인의 꺾임점(Vertex)으로 삽입됨

**3.4 Step 3 — 삼분할 알고리즘**  
*상세 내용은 4절 핵심 알고리즘을 참조하십시오.*

**3.5 Step 4 — WKT 변환 및 DB 저장**  
\# WKT 직렬화  
start\_wkt \= "LINESTRING Z (x1 y1 z1, x2 y2 z2, ...)"  
sfp\_wkt \= "POINT Z (x y z)" \# Start Free Point  
efp\_wkt \= "POINT Z (x y z)" \# End Free Point

\# DB 적재 (UPSERT, page\_size=200 배치)  
INSERT INTO "TB\_ROUTE\_PATH\_SEGMENTATION" (...)  
VALUES (%s, ST\_GeomFromText(%s, 0), ...)  
ON CONFLICT ("ROUTE\_PATH\_GUID") DO UPDATE SET ...

**4\. 핵심 알고리즘**

**4.1 axis\_snap(d) — 벡터 → 6축 방향 스냅**  
임의의 3D 벡터를 6개 직교 방향 중 지배축으로 매핑합니다. 이 함수가 모든 방향 판별의 기초가 됩니다.

| 인덱스 | 방향 | 설명 |
| ----- | ----- | ----- |
| 0 | \+X | X축 양의 방향 |
| 1 | \-X | X축 음의 방향 |
| 2 | \+Y | Y축 양의 방향 |
| 3 | \-Y | Y축 음의 방향 |
| 4 | \+Z | Z축 양의 방향 (수직 상승) |
| 5 | \-Z | Z축 음의 방향 (수직 하강) |

def axis\_snap(d: tuple) \-\> int:  
 values \= \[abs(d\[0\]), abs(d\[1\]), abs(d\[2\])\]  
 ax \= max(range(3), key=lambda i: values\[i\]) \# 지배 축 (0=X, 1=Y, 2=Z)  
 return ax \* 2 \+ (0 if d\[ax\] \>= 0 else 1\)

\# Z축 판별: axis\_snap(d) // 2 \== 2

**4.2 Start Stub 분할 알고리즘**

**현업 정의: 장비 PoC → A/F 구역 수평 이동 → 격자보 관통 수직 하강 → CSF 구역 진입점**

**Phase A — CSF 경계 탐색 (우선 적용)**  
INPUT: points\[0..N-1\] (장비 PoC부터 순서대로)

IF points\[0\].Z \>= 13700: \# 시작점이 A/F 이상 구역인 경우  
 FOR i \= 1 to N-1:  
 IF points\[i\].Z \<= 13700: \# CSF 구역 진입점 발견  
 start\_idx \= i \# 이 점까지 Start Stub에 포함  
 matched\_csf \= True  
 BREAK

OUTPUT: start\_stub \= points\[0 .. start\_idx\] (start\_idx 포함)  
 start\_free\_point \= points\[start\_idx\]

**Phase B — Fallback: 수직 런 탐색 (Phase A 실패 시)**  
IF NOT matched\_csf:  
 \# 50mm 이상 첫 유의미한 세그먼트의 지배축 탐색  
 first\_axis \= 첫 dist \>= 50mm 세그먼트의 axis\_snap(b-a) // 2

 IF first\_axis \== Z (수직):  
 \# 수직 런이 끝나는 지점까지 포함  
 start\_idx \= Z축 방향 런이 끝나는 인덱스  
 ELSE:  
 start\_idx \= 1 \# 수평 시작 \= 첫 세그먼트 끝만 포함

**실제 경로 예시 (WTNHJ02 Water)**

| 인덱스 | Z값 (mm) | 방향 | 길이 | 구역 | 비고 |
| ----- | ----- | ----- | ----- | ----- | ----- |
| \[0\] | 15,495 | \- | \- | CR | 장비 PoC (시작) |
| \[1\] | 15,145 | Z↓ | 350 mm | CR | 수직 하강 |
| \[2\] | 15,135 | Z↓ | 13 mm | A/F 진입 |  |
| \[3\] | 15,135 | Y→ | 181 mm | A/F | 수평 이동 시작 |
| \[4\] | 15,135 | X→ | 13 mm | A/F |  |
| \[5\] | 15,135 | X→ | 913 mm | A/F |  |
| \[6\~12\] | 15,135 | ... | ... | A/F | A/F 수평 이동 계속 |
| \[13\] | 11,970 | Z↓ | 3,156 mm | CSF 진입 | ★ Z≤13700 첫 진입 → start\_idx=13 |
| \[14+\] | 11,960\~ | ... | ... | CSF | Middle Trunk |

**결과: Start Stub \= pts\[0..13\] (15개 점, Z=15,495 → 11,970, 길이 ≈ 2,744 mm)**

**미세 지터 필터링 (50mm 임계값)**  
BIM 데이터에는 배관 이음, 피팅 부위에 50mm 미만의 미세 선분(지터)이 포함됩니다. 이를 방향 판정에서 제외하여 잘못된 축 판별을 방지합니다:

for i in range(len(points) \- 1):  
 a, b \= points\[i\], points\[i+1\]  
 L \= dist(a, b)  
 if L \< 50.0: \# ← 50mm 미만 지터는 건너뜀  
 end\_idx \= i \+ 1  
 continue  
 axis \= axis\_snap(vec\_sub(b, a)) // 2  
 if axis \== first\_axis:  
 first\_run\_len \+= L  
 end\_idx \= i \+ 1  
 else:  
 break

**4.3 End Stub 분할 알고리즘**

**현업 정의: 종단 덕트/레터럴 PoC에서 역방향으로 탐색하여 첫 번째 방향 전환 엘보 정점까지**

INPUT: points\[start\_idx .. N-1\]

\# 역방향 첫 방향 탐색  
last\_axis \= points\[N-2..N-1\] 중 50mm+ 첫 세그먼트의 지배축

\# 역방향 스캔  
FOR i \= N-2 downto start\_idx:  
 IF dist(points\[i\], points\[i+1\]) \< 50mm:  
 end\_idx \= i \# 지터는 건너뜀  
 CONTINUE  
 curr\_axis \= 이 세그먼트의 지배축  
 IF curr\_axis \!= last\_axis: \# 방향 전환 발생 \= 엘보  
 end\_idx \= i \+ 1 \# 엘보 정점 \= points\[i+1\]  
 BREAK  
 ELSE:  
 end\_idx \= i

OUTPUT: end\_stub \= points\[end\_idx .. N-1\]  
 end\_free\_point \= points\[end\_idx\]

**진입방향(Entry Direction) 계산**  
End Stub이 덕트/레터럴 PoC로 진입하는 방향을 축정렬 단위벡터로 함께 계산하여 저장합니다. 위 역방향 스캔에서 찾은 last\_axis(비교용, 부호 제거된 축 인덱스)와 별도로, 부호를 포함한 원본 axis\_snap() 반환값(last\_axis\_full, 0~5)을 보존하여 6방향 단위벡터로 변환합니다:

entry\_direction \= AXIS\_VECTORS\[last\_axis\_full\] \# 예: (0, 0, \-1) \= \-Z 방향으로 진입(하강)

유의미한(50mm 이상) 세그먼트를 하나도 찾지 못하면(END\_STUB 트렁크 구간이 극히 짧은 경우) entry\_direction은 None으로 남습니다. 실제 DB(827개 경로) 기준 805개 경로(97.3%)에서 값이 산출되며, 나머지 22개는 NULL로 저장됩니다.

**실제 검증 사례 (댐퍼 포함 경로, 7건 전수 확인)**  
TB\_ROUTE\_SEGMENT\_DETAIL.TYPE \= 'DAMPER'/'DAMPER\_DUCT'가 존재하는 7개 경로를 전수 조사한 결과, 댐퍼와 TAKEOFF 세그먼트는 항상 직전 PIPE 구간과 동일한 축(Z) 방향으로 일직선 배치되어 있었습니다. 따라서 TYPE 컬럼을 별도로 조회하지 않아도, 기하학적 축 비교만으로 "덕트 Takeoff → 댐퍼 통과 → 첫 번째 엘보"까지의 End Stub 범위가 정확히 산출됩니다. 이 사실이 확인되어, 댐퍼 식별을 위한 별도의 TYPE 기반 분기 로직은 추가하지 않았습니다(향후 다른 축 방향의 댐퍼가 발견될 경우 재검토 필요).

**4.4 Middle Trunk 분할**  
middle\_trunk\_pts \= points\[start\_idx : end\_idx \+ 1\]

\# 예외 처리: 구간이 너무 짧으면 두 Free Point를 직선으로 연결  
if len(middle\_trunk\_pts) \< 2:  
 middle\_trunk\_pts \= \[start\_free\_point, end\_free\_point\]

**5\. 저장 데이터 (Output Schema)**

**5.1 TB\_ROUTE\_PATH\_SEGMENTATION DDL**  
CREATE TABLE "TB\_ROUTE\_PATH\_SEGMENTATION" (  
 "ROUTE\_PATH\_GUID" text PRIMARY KEY, \-- 경로 식별자  
 "START\_STUB\_GEOM" geometry(LineStringZ, 0), \-- Start Stub 3D 선형  
 "MIDDLE\_TRUNK\_GEOM" geometry(LineStringZ, 0), \-- Middle Trunk 3D 선형  
 "END\_STUB\_GEOM" geometry(LineStringZ, 0), \-- End Stub 3D 선형  
 "START\_FREE\_POINT" geometry(PointZ, 0), \-- Trunk 시작 연결점  
 "END\_FREE\_POINT" geometry(PointZ, 0), \-- Trunk 끝 연결점  
 "END\_ENTRY\_DIR\_X" double precision, \-- 진입방향 단위벡터 X  
 "END\_ENTRY\_DIR\_Y" double precision, \-- 진입방향 단위벡터 Y  
 "END\_ENTRY\_DIR\_Z" double precision, \-- 진입방향 단위벡터 Z  
 "CREATED\_AT" timestamp DEFAULT now()  
);

\-- PostGIS 공간 인덱스 (GiST)  
CREATE INDEX "IX\_TRPS\_START\_STUB" ON ... USING gist("START\_STUB\_GEOM");  
CREATE INDEX "IX\_TRPS\_MIDDLE\_TRUNK" ON ... USING gist("MIDDLE\_TRUNK\_GEOM");  
CREATE INDEX "IX\_TRPS\_END\_STUB" ON ... USING gist("END\_STUB\_GEOM");

**5.2 컬럼 상세**

| 컬럼 | 타입 | 설명 |
| ----- | ----- | ----- |
| ROUTE\_PATH\_GUID | text PK | TB\_ROUTE\_PATH의 GUID와 1:1 대응 |
| START\_STUB\_GEOM | geometry(LineStringZ) | 장비 PoC → CSF 진입점까지의 3D 폴리라인 |
| MIDDLE\_TRUNK\_GEOM | geometry(LineStringZ) | CSF 구역 내 주 배관 경로 |
| END\_STUB\_GEOM | geometry(LineStringZ) | 종단 덕트 PoC → 첫 엘보까지의 3D 폴리라인 |
| START\_FREE\_POINT | geometry(PointZ) | Start Stub 마지막 점 \= Middle Trunk 시작 연결점 |
| END\_FREE\_POINT | geometry(PointZ) | End Stub 첫 점 \= Middle Trunk 끝 연결점 |
| END\_ENTRY\_DIR\_X/Y/Z | double precision | End Stub이 종단 PoC로 진입하는 축정렬 단위벡터 (예: (0,0,-1)). 유의미한 세그먼트를 찾지 못하면 NULL |
| CREATED\_AT | timestamp | 삼분할 계산 수행 시각 |

**5.3 WKT 저장 예시**  
\-- START\_STUB\_GEOM 예시 (WTNHJ02 Water 경로)  
LINESTRING Z (  
 205147.6 15010.9 15495, \-- 장비 PoC  
 205147.6 15010.9 15145, \-- 수직 하강 (CR)  
 205147.6 15010.9 15135, \-- A/F 진입  
 205147.6 14990.0 15135, \-- A/F 수평 이동 (Y)  
 ... \-- A/F 수평 이동 계속  
 205147.6 13890.0 11970 \-- 격자보 통과 후 CSF 진입 ← 분할 경계  
)

\-- START\_FREE\_POINT  
POINT Z (205147.6 13890.0 11970\)

\-- MIDDLE\_TRUNK\_GEOM: CSF 구역 수평/수직 이동  
\-- END\_STUB\_GEOM: 덕트 Takeoff → 댐퍼 → 첫 엘보 (실제 예시, WTNHJ02 Water 경로)  
\-- END\_ENTRY\_DIR\_X/Y/Z: (0, 0, \-1) → \-Z 방향(하강)으로 덕트 PoC에 진입

**5.4 PostGIS 활용 쿼리**  
\-- Start Stub 길이 조회 (mm 단위)  
SELECT "ROUTE\_PATH\_GUID",  
 ST\_Length("START\_STUB\_GEOM") AS start\_len\_mm,  
 ST\_NPoints("START\_STUB\_GEOM") AS stub\_npts  
FROM "TB\_ROUTE\_PATH\_SEGMENTATION"  
ORDER BY start\_len\_mm DESC;

\-- Z 범위 조회 (구역 판별)  
SELECT  
 "ROUTE\_PATH\_GUID",  
 ST\_ZMin("START\_STUB\_GEOM") AS stub\_z\_min,  
 ST\_ZMax("START\_STUB\_GEOM") AS stub\_z\_max  
FROM "TB\_ROUTE\_PATH\_SEGMENTATION";

**6\. 실행 명령**

| 명령 | 설명 |
| ----- | ----- |
| python Tools/PathSegmenter.py create-schema \--password dinno | TB\_ROUTE\_PATH\_SEGMENTATION 테이블 DDL 생성 (PostGIS 포함) |
| python Tools/PathSegmenter.py run-all \--password dinno | 스키마 생성 \+ 전체 경로 삼분할 연산 수행 \+ DB UPSERT |

**처리 시간: 827개 경로 기준 약 50\~90초 (DB 서버 성능에 따라 상이)**

**7\. WPF 뷰어 연동**

**7.1 데이터 로딩 흐름**  
DB WKT (LINESTRING Z)  
 ↓ ST\_AsText() → PostgresRoutingDataLoader.cs  
 ↓ ParseLineStringZ() → List\<Vec3\> (3D 점열)  
 ↓  
ExistingRoutePath  
 .StartStubPoints → 주황(OrangeRed)으로 렌더링  
 .MiddleTrunkPoints → 녹색(LimeGreen)으로 렌더링  
 .EndStubPoints → 청색(Cyan)으로 렌더링  
 .Points (전체 합산) → 카메라 Fit 기준

**7.2 수직 뷰 카메라 자동 방향 선택**  
수직 단면 뷰(ViewportZ)는 경로의 주 수평 이동 방향을 자동 감지하여 직각 방향에서 관찰합니다. Y 이동이 지배적이면 X 방향에서, X 이동이 지배적이면 Y 방향에서 바라봅니다.

bool yDominant \= sizeY \>= sizeX;  
if (yDominant) {  
 // Y 방향 이동 → X축(+X)에서 바라봄 → Y-Z 평면 단면도  
 camZ.LookDirection \= new Vector3D(-1, 0, 0);  
 camZ.Width \= Math.Max(sizeY, sizeZ);  
} else {  
 // X 방향 이동 → Y축(+Y)에서 바라봄 → X-Z 평면 단면도  
 camZ.LookDirection \= new Vector3D(0, \-1, 0);  
 camZ.Width \= Math.Max(sizeX, sizeZ);  
}

**8\. 알려진 제한 및 특이 케이스**

| 항목 | 설명 | 개선 방향 |
| ----- | ----- | ----- |
| CSF 경계 하드코딩 | Z=13,700mm가 소스 코드에 상수로 박혀 있음 | 사이트별 설정 파일(tool\_config)에서 주입 가능하도록 리팩토링 |
| A/F 내 완결 경로 | 시작/끝이 모두 A/F(Z\>13,700)인 짧은 경로는 matched\_csf=False → Fallback 처리 | Utility/장비 유형별 예외 룰 추가 검토 |
| ELBOW IP 복원 정확도 | skew\_dist\<500mm 조건 하에서만 IP 복원. 대형 엘보는 그대로 TO\_POS 사용 | 임계값 조정 또는 엘보 반경 기반 정확한 IP 산정 |
| 경로 방향 일관성 | TB\_ROUTE\_SEGMENT\_DETAIL 좌표가 항상 장비→덕트 방향으로 정렬됨을 가정 | SOURCE\_POS 거리 비교로 방향 자동 감지 (orient\_points 참조) |
| 진입방향(END\_ENTRY\_DIR) 미확정 | End Stub 트렁크 구간에서 50mm 이상 유의미한 세그먼트를 찾지 못하면 NULL로 저장됨. 실제 DB 827개 경로 중 22개(2.7%)가 해당 | 최소 길이 미달 시 Middle Trunk 방향으로 대체 추정하는 Fallback 규칙 검토 |

**9\. 관련 파일 목록**

| 파일 | 역할 |
| ----- | ----- |
| Tools/PathSegmenter.py | 삼분할 연산 및 TB\_ROUTE\_PATH\_SEGMENTATION DB 적재 메인 스크립트 |
| Tools/ExtractStubPatterns.py | Stub 패턴 24D 특징 벡터 추출, 템플릿 집계, 신규 Stub 후보 생성 |
| Tools/sql/create\_path\_segmentation\_table.sql | TB\_ROUTE\_PATH\_SEGMENTATION 테이블 DDL (PostGIS) |
| RubberBandRouting.Engine/PostgresRoutingDataLoader.cs | C\# 뷰어 DB 로딩 (WKT → Vec3 파싱) |
| RubberBandRouting.Viewer/SegmentViewerWindow.xaml.cs | WPF 3D/2D 뷰어 렌더링 및 카메라 제어 |

