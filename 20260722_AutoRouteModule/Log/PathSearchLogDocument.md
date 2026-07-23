# GridAStar3D 경로 탐색 로그 문서

## 개요
GridAStar3D 경로 탐색 알고리즘의 실행 결과와 상세 정보를 JSON 형식으로 기록하는 로깅 시스템입니다.

로그는 **가독성 향상을 위해 여러 파일로 분할 저장**됩니다:
- **메인 로그 파일**: 경로 탐색 메타데이터와 통계 정보
- **RawPath 파일**: 압축 전 원본 경로 데이터
- **SimplifiedPath 파일**: 단순화된 최종 경로 데이터
- **SearchNodes 파일**: 탐색 과정에서 방문한 모든 노드 정보

## 파일 네이밍 규칙

모든 로그 파일은 **현재 시간 기반의 타임스탬프**를 사용하여 자동 생성됩니다:

```
PathLog_YYYYMMDD_HHMMSS.json              // 메인 로그
PathLog_YYYYMMDD_HHMMSS_RawPath.json      // 원본 경로
PathLog_YYYYMMDD_HHMMSS_SimplifiedPath.json  // 단순화 경로
PathLog_YYYYMMDD_HHMMSS_SearchNodes.json  // 탐색 노드
```

예시:
```
PathLog_20240315_143025.json
PathLog_20240315_143025_RawPath.json
PathLog_20240315_143025_SimplifiedPath.json
PathLog_20240315_143025_SearchNodes.json
```

## 로깅 활성화 방법

```csharp
using AutoRouteModule.Debug;

// 로깅 활성화
LoggingState.EnableLogging = true;

var pathfinder = new GridAStar3D();
PathResult result = pathfinder.FindPath(start, goal, ...);

// 결과에서 로그 파일 경로 확인
if (!string.IsNullOrEmpty(result.LogFilePath))
{
	Console.WriteLine($"로그 저장됨: {result.LogFilePath}");
}

// 로깅 비활성화
LoggingState.EnableLogging = false;
```

## 로그 파일 읽기

### 특정 로그 파일 읽기

```csharp
// 메인 로그 읽기
var log = LogManager.LoadPathSearchLog("Logs/PathLog_20240315_143025.json");

if (log != null)
{
	Console.WriteLine($"성공 여부: {log.Success}");
	Console.WriteLine($"경과 시간: {log.ElapsedTimeMs}ms");
	Console.WriteLine($"탐색 노드 수: {log.SearchNodeCount}");
}
```

### 마지막 저장된 로그 읽기

```csharp
// 로그 생성
LoggingState.EnableLogging = true;
PathResult result = pathfinder.FindPath(...);
LoggingState.EnableLogging = false;

// 마지막 저장된 로그 읽기
var lastLog = LogManager.LoadLastSavedLog();

if (lastLog != null)
{
	Console.WriteLine("마지막 저장 로그 로드 성공");
	Console.WriteLine($"마지막 경로: {LogManager.LastSavedLogPath}");
}
```

### 개별 경로 파일 읽기

```csharp
var mainLog = LogManager.LoadPathSearchLog(result.LogFilePath);

if (mainLog != null && !string.IsNullOrEmpty(mainLog.BaseLogPath))
{
	// Raw Path 읽기
	var rawPath = LogManager.LoadRawPath(mainLog.BaseLogPath);
	Console.WriteLine($"Raw Path 노드 수: {rawPath?.Count ?? 0}");

	// Simplified Path 읽기
	var simplifiedPath = LogManager.LoadSimplifiedPath(mainLog.BaseLogPath);
	Console.WriteLine($"Simplified Path 노드 수: {simplifiedPath?.Count ?? 0}");

	// Search Nodes 읽기
	var searchNodes = LogManager.LoadSearchNodes(mainLog.BaseLogPath);
	Console.WriteLine($"Search Nodes 수: {searchNodes?.Count ?? 0}");
}
```

### 전체 로그 한번에 읽기

```csharp
// 전체 로그 한번에 읽기
var completeLog = LogManager.LoadCompleteLog("Logs/PathLog_20240315_143025.json");

if (completeLog != null)
{
	Console.WriteLine($"성공: {completeLog.MainLog?.Success}");
	Console.WriteLine($"Raw Path: {completeLog.RawPath?.Count ?? 0} 노드");
	Console.WriteLine($"Simplified Path: {completeLog.SimplifiedPath?.Count ?? 0} 노드");
	Console.WriteLine($"Search Nodes: {completeLog.SearchNodes?.Count ?? 0} 개");
}

// 또는 마지막 저장된 전체 로그 읽기
var lastCompleteLog = LogManager.LoadLastCompleteLog();
```

## API 레퍼런스

### LogManager 클래스

#### 저장 메서드
- `SavePathSearchLog(PathSearchLog, string)`: 메인 로그 저장
- `CreateAndSavePathSearchLog(PathResult, Vector3, Vector3, double, string)`: 경로 결과로부터 로그 생성 및 저장

#### 읽기 메서드
- `LoadPathSearchLog(string logFilePath)`: 메인 로그 파일 읽기
- `LoadLastSavedLog()`: 마지막 저장된 로그 읽기
- `LoadRawPath(string baseLogPath)`: Raw Path 파일 읽기
- `LoadSimplifiedPath(string baseLogPath)`: Simplified Path 파일 읽기
- `LoadSearchNodes(string baseLogPath)`: Search Nodes 파일 읽기
- `LoadCompleteLog(string mainLogFilePath)`: 전체 로그 한번에 읽기
- `LoadLastCompleteLog()`: 마지막 저장된 전체 로그 읽기

#### 프로퍼티
- `LastSavedLogPath`: 마지막으로 저장된 로그 파일 경로 (읽기 전용)

### CompleteLogData 클래스
전체 로그 데이터를 담는 클래스
- `MainLog`: PathSearchLog? - 메인 로그
- `RawPath`: List<Vector3>? - 원본 경로
- `SimplifiedPath`: List<Vector3>? - 단순화된 경로
- `SearchNodes`: List<PathSearchNodeLog>? - 탐색 노드 목록

## 로그 파일 구조

### 1. 메인 로그 파일 (PathLog_YYYYMMDD_HHMMSS.json)

메타데이터, 통계, 분석 결과 및 **다른 파일들의 경로 참조**만 포함합니다.

```json
{
  "success": true,
  "result_code": "SUCCESS",
  "elapsed_time_ms": 45.3,
  "search_node_count": 1523,
  "turn_count": 8,
  "total_pipe_length": 125.7,
  "segment_lengths": [
	{
	  "length": 10.5,
	  "direction": { "x": 1.0, "y": 0.0, "z": 0.0 },
	  "start_position": { "x": 0.0, "y": 0.0, "z": 0.0 },
	  "end_position": { "x": 10.5, "y": 0.0, "z": 0.0 }
	}
  ],
  "final_arrival_cost": 125.7,
  "timestamp": "2024-03-15 14:30:25.123",
  "start_position": { "x": 0.0, "y": 0.0, "z": 0.0 },
  "goal_position": { "x": 100.0, "y": 100.0, "z": 50.0 },
  "raw_path_file": "PathLog_20240315_143025_RawPath.json",
  "simplified_path_file": "PathLog_20240315_143025_SimplifiedPath.json",
  "search_nodes_file": "PathLog_20240315_143025_SearchNodes.json"
}
```

### 2. RawPath 파일 (PathLog_YYYYMMDD_HHMMSS_RawPath.json)

```json
{
  "path_type": "raw_path",
  "node_count": 1250,
  "nodes": [
	{ "x": 0.0, "y": 0.0, "z": 0.0 },
	{ "x": 1.0, "y": 0.0, "z": 0.0 }
  ]
}
```

### 3. SimplifiedPath 파일 (PathLog_YYYYMMDD_HHMMSS_SimplifiedPath.json)

```json
{
  "path_type": "simplified_path",
  "node_count": 12,
  "nodes": [
	{ "x": 0.0, "y": 0.0, "z": 0.0 },
	{ "x": 10.5, "y": 0.0, "z": 0.0 }
  ]
}
```

### 4. SearchNodes 파일 (PathLog_YYYYMMDD_HHMMSS_SearchNodes.json)

```json
{
  "total_count": 1523,
  "search_nodes": [
	{
	  "g_cost": 10.0,
	  "h_cost": 150.5,
	  "total_cost": 160.5,
	  "position": { "x": 5.0, "y": 3.0, "z": 2.0 },
	  "is_collision": false
	}
  ]
}
```

## 로그 파일 관리

### 로그 디렉토리 구조

```
Logs/
  ├── PathLog_20240315_143025.json
  ├── PathLog_20240315_143025_RawPath.json
  ├── PathLog_20240315_143025_SimplifiedPath.json
  ├── PathLog_20240315_143025_SearchNodes.json
  ├── PathLog_20240315_143130.json
  ├── PathLog_20240315_143130_RawPath.json
  └── ...
```

### 파일명으로 관련 로그 찾기

타임스탬프 기반이므로 파일명 패턴 매칭으로 쉽게 그룹화 가능:

```csharp
string basePattern = "PathLog_20240315_143025";
string mainLog = $"{basePattern}.json";
string rawPath = $"{basePattern}_RawPath.json";
string simplifiedPath = $"{basePattern}_SimplifiedPath.json";
string searchNodes = $"{basePattern}_SearchNodes.json";
```

## 성능 고려사항

- `LoggingState.EnableLogging = false`일 때는 **오버헤드 없음** (로그 수집 자체가 비활성화됨)
- 로깅 활성화 시 메모리 사용량 증가 (탐색 노드 기록)
- 대량의 경로 데이터는 별도 파일로 분할되어 **메인 로그의 가독성 유지**
- 로그 읽기는 필요한 파일만 선택적으로 로드 가능

## 주의사항

1. **로깅 활성화 필수**: `LoggingState.EnableLogging = true` 설정 없이는 로그가 생성되지 않습니다.
2. **디렉토리 자동 생성**: 로그 디렉토리가 없으면 자동으로 생성됩니다.
3. **파일명 중복 방지**: 초 단위 타임스탬프 사용으로 중복 방지 (동일 초에 여러 탐색 시 덮어쓰기 가능)
4. **디스크 공간**: 대규모 탐색 시 SearchNodes 파일이 매우 클 수 있으므로 디스크 공간 고려 필요
5. **예외 처리**: 파일 읽기 실패 시 null 반환, 콘솔에 오류 메시지 출력

## 결과 코드 (RESULT_CODES)

| 코드 | 설명 |
|------|------|
| `SUCCESS` | 경로 탐색 성공 |
| `FAIL_TO_PATHFIND` | 경로를 찾지 못함 |
| `FAIL_TO_START_POINT` | 시작점이 차단됨 |
| `FAIL_TO_END_POINT` | 목표점이 차단됨 |
| `TIMEOUT` | 시간 초과 |
| `CANCELLED` | 사용자에 의한 중단 |
