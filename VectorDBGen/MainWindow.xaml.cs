using ClosedXML.Excel;
using Microsoft.Win32;
using Npgsql;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Windows;
using System.Windows.Controls;
using VectorDBGen.Models;

namespace VectorDBGen
{
    /// <summary>독립 실행형 PostgreSQL 벡터 테이블 빌드 윈도우.
    /// Python 엔진(BuildFeatureVectors / BuildContextVectors / BuildDesignGroups / BuildSegmentTemplates)을
    /// 비동기로 호출하고 stdout/stderr를 실시간 스트리밍으로 표시한다.
    /// 실행 전 반드시 DB 접속 테스트를 통과해야 한다.</summary>
    public partial class MainWindow : Window
    {
        private DbConfig _db;                    // 접속 테스트 성공 시 갱신
        private bool _connected;                 // 접속 검증 완료 플래그
        private Process? _proc;
        private CancellationTokenSource? _cts;
        private Stopwatch? _sw;
        private DispatcherTimerLite? _statusTimer;
        private AppSettings _settings = new();
        private string? _loadedSettingsPath;

        // ───────────────────────────────────────────
        // DDL 실행 리포트 (엑셀 저장용)
        // ───────────────────────────────────────────
        private DdlReport? _lastReport;   // BtnExportReport 대상

        /// <summary>DDL/재생성/일괄생성 실행 한 건의 요약. 엑셀 저장에 사용.</summary>
        private sealed class DdlReport
        {
            public string Action { get; set; } = "";     // "스키마 초기화"/"테이블 재생성"/"전체 생성"
            public string Builder { get; set; } = "";    // 선택된 빌더 태그 또는 "all"
            public DateTime StartedAt { get; set; }
            public DateTime FinishedAt { get; set; }
            public List<DdlFileRun> Files { get; set; } = new();
            public List<TableSnapshot> BeforeState { get; set; } = new();
            public List<TableSnapshot> AfterState { get; set; } = new();
            public int TotalOk     => Files.Sum(f => f.Statements.Count(s => s.Success));
            public int TotalFail   => Files.Sum(f => f.Statements.Count(s => !s.Success));
            public double TotalMs  => (FinishedAt - StartedAt).TotalMilliseconds;
        }

        private sealed class DdlFileRun
        {
            public string FileName { get; set; } = "";
            public string FilePath { get; set; } = "";
            public int FileSizeChars { get; set; }
            public List<StmtResult> Statements { get; set; } = new();
        }

        private sealed class StmtResult
        {
            public int Order { get; set; }
            public string Preview { get; set; } = "";
            public bool Success { get; set; }
            public string? Error { get; set; }
            public double ElapsedMs { get; set; }
        }

        private sealed class TableSnapshot
        {
            public string TableName { get; set; } = "";
            public bool Exists { get; set; }
            public long RowCount { get; set; }
            public bool HasHnswIndex { get; set; }
        }

        private sealed class AppSettings
        {
            public DbConfig Db { get; set; } = new();
            public string? PythonExe { get; set; }
            public string? ScriptDirectory { get; set; }
            public string? DdlDirectory { get; set; }
            public string? DefaultOutputDirectory { get; set; }
        }

        // 대상 벡터 테이블 — DDL 로 생성 가능
        private static readonly string[] _targetVectorTables =
        {
            "TB_ROUTE_FEATURE_VECTOR",
            "TB_ROUTE_CONTEXT_VECTOR",
            "TB_ROUTE_DESIGN_GROUP",
            "TB_ROUTE_SEGMENT_TEMPLATE",
        };

        // 소스 데이터 테이블 — 외부 수급 파이프라인이 채워야 함 (DDL 대상 아님)
        private static readonly string[] _sourceDataTables =
        {
            "TB_ROUTE_PATH",
            "TB_ROUTE_SEGMENTS",
            "TB_ROUTE_SEGMENT_DETAIL",
            "TB_BIM_OBSTACLES",
        };

        // 빌더별 필수 소스 테이블 — 누락 시 Python 엔진 실행 전 거부
        private static readonly Dictionary<string, string[]> _builderSourceRequirements = new()
        {
            ["feature"] = new[] { "TB_ROUTE_PATH", "TB_ROUTE_SEGMENTS", "TB_ROUTE_SEGMENT_DETAIL" },
            ["context"] = new[] { "TB_ROUTE_PATH", "TB_ROUTE_SEGMENTS", "TB_ROUTE_SEGMENT_DETAIL", "TB_BIM_OBSTACLES" },
            ["group"]   = new[] { "TB_ROUTE_PATH" },
            ["segment"] = new[] { "TB_ROUTE_PATH", "TB_ROUTE_SEGMENTS", "TB_ROUTE_SEGMENT_DETAIL" },
        };

        // 빌더별 대상 벡터 테이블 (Python 엔진이 INSERT/DELETE 하므로 사전에 존재해야 함)
        private static readonly Dictionary<string, string> _builderTargetTable = new()
        {
            ["feature"] = "TB_ROUTE_FEATURE_VECTOR",
            ["context"] = "TB_ROUTE_CONTEXT_VECTOR",
            ["group"]   = "TB_ROUTE_DESIGN_GROUP",
            ["segment"] = "TB_ROUTE_SEGMENT_TEMPLATE",
        };

        // 빌더별 DDL 파일명
        private static readonly Dictionary<string, string> _builderDdlFile = new()
        {
            ["feature"] = "create_feature_vector_table.sql",
            ["context"] = "create_context_vector_table.sql",
            ["group"]   = "create_auto_design_tables.sql",
            ["segment"] = "create_auto_design_tables.sql",
        };

        private static readonly Dictionary<string, string> _builderScriptFile = new()
        {
            ["feature"] = "BuildFeatureVectors.py",
            ["context"] = "BuildContextVectors.py",
            ["group"]   = "BuildDesignGroups.py",
            ["segment"] = "BuildSegmentTemplates.py",
        };

        private AppSettings LoadSettings()
        {
            var options = new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true,
                ReadCommentHandling = JsonCommentHandling.Skip,
                AllowTrailingCommas = true,
            };

            foreach (var path in GetSettingsCandidates())
            {
                if (!File.Exists(path)) continue;
                try
                {
                    var json = File.ReadAllText(path, Encoding.UTF8);
                    var settings = JsonSerializer.Deserialize<AppSettings>(json, options) ?? new AppSettings();
                    _loadedSettingsPath = path;
                    return settings;
                }
                catch (Exception ex)
                {
                    MessageBox.Show(this,
                        $"VectorDBGen settings file could not be loaded.\n\n{path}\n\n{ex.Message}",
                        "Settings load failed",
                        MessageBoxButton.OK,
                        MessageBoxImage.Warning);
                    _loadedSettingsPath = path;
                    return new AppSettings();
                }
            }

            return new AppSettings();
        }

        private static IEnumerable<string> GetSettingsCandidates()
        {
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var root in GetSearchRoots())
            {
                var path = Path.Combine(root, "vectordbgen.settings.json");
                if (seen.Add(path)) yield return path;
            }
        }

        private static IEnumerable<string> GetSearchRoots()
        {
            var starts = new[]
            {
                AppDomain.CurrentDomain.BaseDirectory,
                Directory.GetCurrentDirectory(),
            };
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var start in starts)
            {
                string? dir = Path.GetFullPath(start);
                for (int i = 0; i < 8 && !string.IsNullOrEmpty(dir); i++)
                {
                    if (seen.Add(dir)) yield return dir;
                    dir = Path.GetDirectoryName(dir);
                }
            }
        }

        private static string ExpandPath(string path)
        {
            var expanded = Environment.ExpandEnvironmentVariables(path);
            if (expanded.StartsWith("~" + Path.DirectorySeparatorChar) ||
                expanded.StartsWith("~" + Path.AltDirectorySeparatorChar))
            {
                expanded = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                    expanded[2..]);
            }
            return Path.GetFullPath(expanded);
        }

        private static async Task<bool> TableExistsAsync(NpgsqlConnection conn, string tableName)
        {
            await using var cmd = new NpgsqlCommand(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = @t;", conn);
            cmd.Parameters.AddWithValue("t", tableName);
            var r = await cmd.ExecuteScalarAsync();
            return Convert.ToInt32(r) > 0;
        }

        public MainWindow()
        {
            InitializeComponent();
            _settings = LoadSettings();
            _db = _settings.Db ?? new DbConfig();
            _db = new DbConfig();   // 기본값 (localhost:5432/AUTOROUTINGV7/postgres)
            // DB 폼 초기값
            TxtHost.Text         = _db.Host;
            _db = _settings.Db ?? new DbConfig();
            TxtHost.Text         = _db.Host;
            TxtPort.Text         = _db.Port.ToString();
            TxtDbname.Text       = _db.Database;
            TxtUser.Text         = _db.User;
            TxtPassword.Password = _db.Password;
            TxtDbInfo.Text       = $"DB: (미접속) {_db.User}@{_db.Host}:{_db.Port}/{_db.Database}";
            if (!string.IsNullOrWhiteSpace(_settings.DefaultOutputDirectory))
            {
                var outputDir = ExpandPath(_settings.DefaultOutputDirectory);
                TxtSaveNorm.Text = Path.Combine(outputDir, "db_norm_params.json");
            }
            CmbBuilder.SelectedIndex = 0;
            SetRunButtonsEnabled(false);
            // 접속 전에는 실행 비활성
            SetRunButtonsEnabled(false);
        }



        /// <summary>실행 / 1~4 일괄 실행 두 버튼을 묶어서 활성/비활성 토글.</summary>
        private void SetRunButtonsEnabled(bool enabled)
        {
            BtnRun.IsEnabled = enabled;
            BtnRunAll.IsEnabled = enabled;
        }

        // ───────────────────────────────────────────
        // DB 접속 테스트
        // ───────────────────────────────────────────

        /// <summary>사용자가 입력한 DB 정보로 실제 연결을 시도한다.
        /// 성공 시 _db 갱신 + BtnRun 활성. 실패 시 로그에 원인 표시.</summary>
        private async void BtnConnect_Click(object sender, RoutedEventArgs e)
        {
            // UI 스레드에서 현재 값 읽기 (PasswordBox/TextBox 는 DependencyObject)
            string host = TxtHost.Text?.Trim() ?? "";
            string portStr = TxtPort.Text?.Trim() ?? "";
            string dbname = TxtDbname.Text?.Trim() ?? "";
            string user = TxtUser.Text?.Trim() ?? "";
            string password = TxtPassword.Password ?? "";

            if (!int.TryParse(portStr, out int port) || port <= 0 || port > 65535)
            {
                SetConnStatus(false, "Port 값이 올바르지 않습니다 (1~65535)");
                return;
            }
            if (string.IsNullOrEmpty(host) || string.IsNullOrEmpty(dbname) || string.IsNullOrEmpty(user))
            {
                SetConnStatus(false, "Host/Database/User는 비어있을 수 없습니다");
                return;
            }

            var candidate = new DbConfig
            {
                Host = host, Port = port, Database = dbname,
                User = user, Password = password,
            };

            BtnConnect.IsEnabled = false;
            TxtConnStatus.Text = "● 연결 시도 중...";
            TxtConnStatus.Foreground = System.Windows.Media.Brushes.DarkOrange;
            AppendLog($"[DB 접속 시도] {user}@{host}:{port}/{dbname}\n");

            var sw = Stopwatch.StartNew();
            try
            {
                await using var conn = new NpgsqlConnection(candidate.ConnectionString);
                await conn.OpenAsync();
                // 간단한 검증 쿼리 + pgvector 확장 존재 여부 확인 (경고만)
                string? dbVersion = null;
                bool hasPgvector = false;
                await using (var cmd = new NpgsqlCommand("SELECT version();", conn))
                {
                    var r = await cmd.ExecuteScalarAsync();
                    dbVersion = r?.ToString();
                }
                await using (var cmd = new NpgsqlCommand(
                    "SELECT COUNT(*) FROM pg_extension WHERE extname='vector';", conn))
                {
                    var r = await cmd.ExecuteScalarAsync();
                    hasPgvector = Convert.ToInt32(r) > 0;
                }
                sw.Stop();

                // 테이블 존재 여부 일괄 점검 — 대상(벡터) vs 소스(수급) 분리
                var missingTarget = new List<string>();
                var missingSource = new List<string>();
                foreach (var tbl in _targetVectorTables)
                {
                    if (!await TableExistsAsync(conn, tbl)) missingTarget.Add(tbl);
                }
                foreach (var tbl in _sourceDataTables)
                {
                    if (!await TableExistsAsync(conn, tbl)) missingSource.Add(tbl);
                }
                sw.Stop();

                _db = candidate;
                _connected = true;
                SetConnStatus(true, $"접속 성공 ({sw.Elapsed.TotalMilliseconds:F0} ms)");
                TxtDbInfo.Text = $"DB: {_db.User}@{_db.Host}:{_db.Port}/{_db.Database}";
                AppendLog($"[DB 접속 성공] {sw.Elapsed.TotalMilliseconds:F0} ms\n");
                if (dbVersion != null) AppendLog($"  version: {Truncate(dbVersion, 80)}\n");
                AppendLog(hasPgvector
                    ? "  pgvector extension: OK\n"
                    : "  [warn] pgvector extension 없음 — 스키마 초기화로 설치 필요 (superuser 권한)\n");

                // 대상 벡터 테이블 (DDL 로 생성 가능)
                if (missingTarget.Count == 0)
                    AppendLog("  대상 벡터 테이블 4종 모두 존재\n");
                else
                {
                    AppendLog($"  [info] 누락 대상 테이블({missingTarget.Count}): {string.Join(", ", missingTarget)}\n");
                    AppendLog("         → '스키마 초기화 (DDL)' 버튼으로 생성 가능\n");
                }
                // 소스 데이터 테이블 (외부 수급 필요)
                if (missingSource.Count == 0)
                    AppendLog("  소스 데이터 테이블 4종 모두 존재\n\n");
                else
                {
                    AppendLog($"  [warn] 누락 소스 테이블({missingSource.Count}): {string.Join(", ", missingSource)}\n", error: true);
                    AppendLog("         → 이 테이블들은 벡터 DDL로 생성되지 않습니다 (상위 BIM/Route 데이터 수급 파이프라인 필요)\n", error: true);
                    if (missingSource.Contains("TB_BIM_OBSTACLES"))
                        AppendLog("         → TB_BIM_OBSTACLES 누락 시 Context Vector 빌더는 실행 불가\n", error: true);
                    if (missingSource.Contains("TB_ROUTE_PATH"))
                        AppendLog("         → TB_ROUTE_PATH 누락 시 모든 빌더 실행 불가\n", error: true);
                    AppendLog("\n");
                }

                SetRunButtonsEnabled(true);
                SetSchemaButtonsEnabled(true);
            }
            catch (Exception ex)
            {
                sw.Stop();
                _connected = false;
                SetConnStatus(false, "접속 실패");
                AppendLog($"[DB 접속 실패] {ex.GetType().Name}: {ex.Message}\n\n", error: true);
                SetRunButtonsEnabled(false);
                SetSchemaButtonsEnabled(false);
            }
            finally
            {
                BtnConnect.IsEnabled = true;
            }
        }

        /// <summary>DB 폼 값이 바뀌면 재접속이 필요하다는 의미로 Run을 비활성한다.</summary>
        private void DbField_Changed(object sender, RoutedEventArgs e)
        {
            if (!_connected) return;
            _connected = false;
            SetRunButtonsEnabled(false);
            SetSchemaButtonsEnabled(false);
            SetConnStatus(false, "설정 변경됨 — 재접속 필요");
        }

        private void SetConnStatus(bool ok, string message)
        {
            TxtConnStatus.Text = (ok ? "● " : "● ") + message;
            TxtConnStatus.Foreground = ok
                ? new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(0x2E, 0x7D, 0x32))
                : new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(0xB0, 0x00, 0x20));
        }

        private static string Truncate(string s, int max) => s.Length > max ? s[..max] + "…" : s;

        // ───────────────────────────────────────────
        // 스키마 초기화 (DDL 실행)
        // ───────────────────────────────────────────

        /// <summary>현재 선택된 빌더에 필요한 DDL 파일을 Npgsql로 실행한다.
        /// 모든 DDL은 CREATE TABLE IF NOT EXISTS 기반이므로 기존 데이터는 보존된다.</summary>
        private async void BtnInitSchema_Click(object sender, RoutedEventArgs e)
        {
            if (!_connected)
            {
                AppendLog("[오류] DB 접속 테스트를 먼저 통과해야 합니다.\n", error: true);
                return;
            }
            var tag = (CmbBuilder.SelectedItem as ComboBoxItem)?.Tag as string ?? "";
            if (!_builderDdlFile.TryGetValue(tag, out var ddlFile))
            {
                AppendLog("[오류] 빌더가 선택되지 않았습니다.\n", error: true);
                return;
            }

            await RunDdlWithReportAsync("스키마 초기화", tag, new[] { ddlFile });
        }

        /// <summary>DDL 1개 이상 연속 실행 + 리포트 수집.
        /// 전후 테이블 상태 스냅샷 포함. 실행 후 _lastReport 갱신 + 리포트 버튼 활성화.</summary>
        private async Task RunDdlWithReportAsync(string action, string builderTag, IEnumerable<string> ddlFiles)
        {
            // 버튼 잠금
            SetSchemaButtonsEnabled(false);
            SetRunButtonsEnabled(false);
            TxtStatus.Text = $"{action} 실행 중...";

            var report = new DdlReport
            {
                Action = action,
                Builder = builderTag,
                StartedAt = DateTime.Now,
            };
            // 모든 벡터/AutoDesign 관련 테이블 대상 (풍부한 감사용)
            var snapshotTargets = _targetVectorTables.Append("TB_ROUTE_AUTO_DESIGN").Distinct().ToArray();
            report.BeforeState = await SnapshotTablesAsync(snapshotTargets);

            int totalOk = 0, totalFail = 0;
            foreach (var ddlFile in ddlFiles)
            {
                var fr = new DdlFileRun();
                var (ok, fail) = await RunDdlAsync(ddlFile, action, fr);
                report.Files.Add(fr);
                if (ok < 0) { totalFail++; continue; }
                totalOk += ok; totalFail += fail;
            }

            report.AfterState = await SnapshotTablesAsync(snapshotTargets);
            report.FinishedAt = DateTime.Now;
            _lastReport = report;

            // 리포트 요약 로그
            AppendLog($"\n── 리포트 요약 ──────────────────────────────\n");
            AppendLog($"  액션   : {report.Action}\n");
            AppendLog($"  DDL 수 : {report.Files.Count}\n");
            AppendLog($"  문장   : 성공 {report.TotalOk} / 실패 {report.TotalFail}\n");
            AppendLog($"  경과   : {report.TotalMs:F0} ms\n");
            foreach (var (b, a) in report.BeforeState.Zip(report.AfterState))
            {
                string delta = (a.RowCount - b.RowCount).ToString("+#,##0;-#,##0;0");
                AppendLog($"  {b.TableName,-28} {b.RowCount,10:N0} → {a.RowCount,10:N0} ({delta})\n");
            }
            AppendLog($"────────────────────────────────────────────\n");

            TxtStatus.Text = totalFail == 0
                ? $"{action} 완료 ({totalOk}문 OK, {report.TotalMs:F0}ms) — 리포트 저장 가능"
                : $"{action} 부분 실패 ({totalOk} OK / {totalFail} FAIL) — 리포트로 상세 확인";

            SetSchemaButtonsEnabled(_connected);
            SetRunButtonsEnabled(_connected);
            BtnExportReport.IsEnabled = true;
        }

        private void SetSchemaButtonsEnabled(bool enabled)
        {
            BtnInitSchema.IsEnabled = enabled;
            BtnRebuildTable.IsEnabled = enabled;
            BtnCreateAllTables.IsEnabled = enabled;
        }

        // ───────────────────────────────────────────
        // 테이블 재생성 (DROP CASCADE + DDL)
        // ───────────────────────────────────────────

        /// <summary>선택 빌더의 대상 테이블을 DROP CASCADE 후 DDL 재실행.
        /// 파괴적 작업이므로 MessageBox 확인 필요.</summary>
        private async void BtnRebuildTable_Click(object sender, RoutedEventArgs e)
        {
            if (!_connected)
            {
                AppendLog("[오류] DB 접속 테스트를 먼저 통과해야 합니다.\n", error: true);
                return;
            }
            var tag = (CmbBuilder.SelectedItem as ComboBoxItem)?.Tag as string ?? "";
            if (!_builderTargetTable.TryGetValue(tag, out var targetTable)
                || !_builderDdlFile.TryGetValue(tag, out var ddlFile))
            {
                AppendLog("[오류] 빌더가 선택되지 않았습니다.\n", error: true);
                return;
            }

            // 파괴적 작업 확인
            var msg = $"[주의] 아래 작업을 수행합니다 — 기존 데이터는 모두 삭제됩니다.\n\n" +
                      $"  1) DROP TABLE \"{targetTable}\" CASCADE\n" +
                      $"  2) {ddlFile} 재실행으로 재생성\n\n" +
                      $"CASCADE 로 관련 뷰/함수/FK 제약도 함께 삭제됩니다.\n" +
                      $"계속하시겠습니까?";
            var r = MessageBox.Show(this, msg, "테이블 재생성 확인",
                MessageBoxButton.YesNo, MessageBoxImage.Warning, MessageBoxResult.No);
            if (r != MessageBoxResult.Yes) return;

            SetSchemaButtonsEnabled(false);
            SetRunButtonsEnabled(false);
            TxtStatus.Text = "테이블 재생성 중...";

            var report = new DdlReport
            {
                Action = "테이블 재생성",
                Builder = tag,
                StartedAt = DateTime.Now,
            };
            var snapshotTargets = _targetVectorTables.Append("TB_ROUTE_AUTO_DESIGN").Distinct().ToArray();
            report.BeforeState = await SnapshotTablesAsync(snapshotTargets);

            // ── 1. DROP CASCADE
            AppendLog($"\n===== 테이블 재생성 시작 =====\n");
            AppendLog($"> DROP TABLE IF EXISTS \"{targetTable}\" CASCADE\n");
            var drop = await DropTableCascadeAsync(targetTable);
            var dropFile = new DdlFileRun { FileName = "<DROP CASCADE>", FilePath = "-", FileSizeChars = 0 };
            dropFile.Statements.Add(new StmtResult
            {
                Order = 1,
                Preview = $"DROP TABLE IF EXISTS \"{targetTable}\" CASCADE",
                Success = drop.Ok,
                Error = drop.Error,
                ElapsedMs = 0,
            });
            report.Files.Add(dropFile);
            if (!drop.Ok)
            {
                AppendLog($"  [fail] {drop.Error}\n", error: true);
                AppendLog("  DROP 실패 — DDL 재실행 생략\n", error: true);
            }
            else
            {
                AppendLog($"  [ok] DROP 완료\n");
                // ── 2. DDL 재실행
                var fr = new DdlFileRun();
                await RunDdlAsync(ddlFile, "테이블 재생성 (DDL)", fr);
                report.Files.Add(fr);
            }

            report.AfterState = await SnapshotTablesAsync(snapshotTargets);
            report.FinishedAt = DateTime.Now;
            _lastReport = report;

            AppendLog($"\n── 리포트 요약 ──────────────────────────────\n");
            AppendLog($"  액션   : {report.Action} / 빌더 {report.Builder}\n");
            AppendLog($"  문장   : 성공 {report.TotalOk} / 실패 {report.TotalFail}\n");
            AppendLog($"  경과   : {report.TotalMs:F0} ms\n");
            foreach (var (b, a) in report.BeforeState.Zip(report.AfterState))
            {
                AppendLog($"  {b.TableName,-28} {b.RowCount,10:N0} → {a.RowCount,10:N0}" +
                          $"  (Exists: {b.Exists}→{a.Exists})\n");
            }
            AppendLog($"────────────────────────────────────────────\n");

            TxtStatus.Text = report.TotalFail == 0
                ? $"테이블 재생성 완료 ({report.TotalMs:F0}ms)"
                : $"테이블 재생성 부분 실패 ({report.TotalFail} FAIL)";

            SetSchemaButtonsEnabled(_connected);
            SetRunButtonsEnabled(_connected);
            BtnExportReport.IsEnabled = true;
        }

        // ───────────────────────────────────────────
        // 전체 벡터 테이블 생성
        // ───────────────────────────────────────────

        /// <summary>3종 DDL 파일을 순차 실행 (CREATE IF NOT EXISTS, 기존 데이터 보존).</summary>
        private async void BtnCreateAllTables_Click(object sender, RoutedEventArgs e)
        {
            if (!_connected)
            {
                AppendLog("[오류] DB 접속 테스트를 먼저 통과해야 합니다.\n", error: true);
                return;
            }
            var allDdls = new[]
            {
                "create_feature_vector_table.sql",
                "create_context_vector_table.sql",
                "create_auto_design_tables.sql",
            };
            await RunDdlWithReportAsync("전체 벡터 테이블 생성", "all", allDdls);
        }

        // ───────────────────────────────────────────
        // 1~4 일괄 실행 (Feature → Context → Group → Segment)
        // ───────────────────────────────────────────

        /// <summary>BtnRunAll 단계 시작 시 대상 벡터 테이블을 깨끗한 상태로 만든다.
        ///   • 테이블 미존재  → DDL 자동 실행으로 생성
        ///   • 테이블 존재 (행 0)   → 그대로 사용
        ///   • 테이블 존재 (데이터) → TRUNCATE RESTART IDENTITY 로 비움
        /// TRUNCATE 가 FK 충돌(예: TB_ROUTE_DESIGN_GROUP ← TB_ROUTE_AUTO_DESIGN)로 실패하면,
        /// 경고만 남기고 통과시켜 Python 엔진의 DELETE FROM 처리에 위임한다.</summary>
        /// <returns>true = 다음 단계 진행 가능 / false = 이 단계 스킵</returns>
        private async Task<bool> EnsureFreshTargetTableAsync(string tag)
        {
            if (!_builderTargetTable.TryGetValue(tag, out var targetTable))
                return true;   // 정의 안된 빌더면 통과

            bool exists;
            long rowCount = 0;
            try
            {
                await using var conn = new NpgsqlConnection(_db.ConnectionString);
                await conn.OpenAsync();
                exists = await TableExistsAsync(conn, targetTable);
                if (exists)
                {
                    await using var cnt = new NpgsqlCommand($"SELECT COUNT(*) FROM \"{targetTable}\";", conn);
                    rowCount = Convert.ToInt64(await cnt.ExecuteScalarAsync());
                }
            }
            catch (Exception ex)
            {
                AppendLog($"  [정책 검사 실패] {targetTable}: {ex.Message}\n", error: true);
                return false;
            }

            // ① 미존재 → DDL 자동 실행으로 생성
            if (!exists)
            {
                if (!_builderDdlFile.TryGetValue(tag, out var ddlFile))
                {
                    AppendLog($"  [실행 거부] {targetTable} 미존재 + DDL 파일 매핑 없음\n", error: true);
                    return false;
                }
                AppendLog($"  [정책] {targetTable} 미존재 → DDL 자동 실행 ({ddlFile})\n");
                var (ok, _) = await RunDdlAsync(ddlFile, "자동 DDL");
                if (ok < 0) return false;
                // 재확인
                try
                {
                    await using var conn = new NpgsqlConnection(_db.ConnectionString);
                    await conn.OpenAsync();
                    if (!await TableExistsAsync(conn, targetTable))
                    {
                        AppendLog($"  [실행 거부] DDL 후에도 {targetTable} 생성되지 않음 (권한 또는 pgvector 확인)\n", error: true);
                        return false;
                    }
                }
                catch (Exception ex)
                {
                    AppendLog($"  [실행 거부] 재확인 오류: {ex.Message}\n", error: true);
                    return false;
                }
                return true;
            }

            // ② 존재 + 빈 → 정리 불필요
            if (rowCount == 0)
            {
                AppendLog($"  [정책] {targetTable} 존재 (행 0) → 그대로 사용\n");
                return true;
            }

            // ③ 존재 + 데이터 → TRUNCATE 시도
            AppendLog($"  [정책] {targetTable} 존재 (행 {rowCount:N0}) → TRUNCATE 후 INSERT\n");
            try
            {
                await using var conn = new NpgsqlConnection(_db.ConnectionString);
                await conn.OpenAsync();
                await using var trunc = new NpgsqlCommand(
                    $"TRUNCATE TABLE \"{targetTable}\" RESTART IDENTITY;", conn);
                await trunc.ExecuteNonQueryAsync();
                AppendLog($"    [ok] TRUNCATE 완료 ({rowCount:N0}건 비움 + IDENTITY 리셋)\n");
            }
            catch (Exception ex)
            {
                AppendLog($"    [경고] TRUNCATE 실패 ({ex.GetType().Name}: {ex.Message})\n", error: true);
                AppendLog($"    Python 엔진의 DELETE FROM 로 위임 진행 (TB_ROUTE_DESIGN_GROUP 의 FK 등 정상 케이스 포함)\n");
            }
            return true;
        }

        /// <summary>4개 빌더(Feature/Context/Group/Segment)를 순차 실행한다.
        /// 각 단계는 (a) 대상 테이블 정책 적용 → (b) 사전 검사 → (c) Python 엔진 호출 순으로 진행되며,
        /// 한 단계가 실패해도 나머지 단계를 계속 진행한 뒤 마지막에 일괄 요약을 출력한다.
        /// 취소 버튼은 진행 중 단계를 중단시키고 이후 단계를 스킵 처리한다.</summary>
        private async void BtnRunAll_Click(object sender, RoutedEventArgs e)
        {
            if (!_connected)
            {
                AppendLog("[오류] DB 접속 테스트를 먼저 통과해야 합니다.\n", error: true);
                return;
            }

            var confirmMsg =
                "1~4 빌더를 순차 실행합니다.\n\n" +
                "  1) Feature Vector  (TB_ROUTE_FEATURE_VECTOR · 30D)\n" +
                "  2) Context Vector  (TB_ROUTE_CONTEXT_VECTOR · 24D)\n" +
                "  3) Design Group    (TB_ROUTE_DESIGN_GROUP)\n" +
                "  4) Segment Template (TB_ROUTE_SEGMENT_TEMPLATE)\n\n" +
                "각 단계는 대상 벡터 테이블을 전체 삭제 후 재생성합니다.\n" +
                "8,828건 기준 Context 단계만 약 70초 — 총 수 분 소요 예상.\n\n" +
                "현재 빌더 옵션 패널에 입력된 파라미터(반경/Limit/MinMembers/역할 필터)가 그대로 사용됩니다.\n\n" +
                "계속하시겠습니까?";
            var confirm = MessageBox.Show(this, confirmMsg, "1~4 일괄 실행 확인",
                MessageBoxButton.YesNo, MessageBoxImage.Question, MessageBoxResult.Yes);
            if (confirm != MessageBoxResult.Yes) return;

            SetRunButtonsEnabled(false);
            SetSchemaButtonsEnabled(false);
            BtnCancel.IsEnabled = true;
            PbRun.Visibility = Visibility.Visible;
            PbRun.IsIndeterminate = true;

            _cts = new CancellationTokenSource();
            var totalSw = Stopwatch.StartNew();
            var summary = new List<(string Tag, string Script, int ExitCode, double Secs)>();

            AppendLog($"\n##### 1~4 일괄 실행 시작 ({DateTime.Now:HH:mm:ss}) #####\n");

            var builders = new[] { "feature", "context", "group", "segment" };
            for (int i = 0; i < builders.Length; i++)
            {
                int step = i + 1;
                string tag = builders[i];
                string stepLabel = $"[{step}/4] ";

                AppendLog($"\n────────────── {stepLabel}{DescribeBuilder(tag)} ──────────────\n");

                if (_cts.IsCancellationRequested)
                {
                    AppendLog($"  [중단] 사용자 취소 — 단계 {step}/4 스킵\n", error: true);
                    summary.Add((tag, "(skipped)", -2, 0));
                    continue;
                }

                // (a) 대상 테이블 정책 — 미존재 시 생성, 데이터 있으면 TRUNCATE
                if (!await EnsureFreshTargetTableAsync(tag))
                {
                    summary.Add((tag, "(table prep fail)", -3, 0));
                    continue;
                }

                // (b) 사전 검사 — 소스 테이블 존재 검증 (대상은 위에서 이미 처리됨)
                if (!await PreflightCheckAsync(tag))
                {
                    summary.Add((tag, "(preflight fail)", -3, 0));
                    continue;
                }

                // CLI 인자 구성 — 각 빌더 옵션 패널의 현재 값 사용
                string scriptName, cliArgs;
                try { (scriptName, cliArgs) = BuildCommand(tag); }
                catch (Exception ex)
                {
                    AppendLog($"  [오류] 인자 구성 실패: {ex.Message}\n", error: true);
                    summary.Add((tag, "(arg fail)", -4, 0));
                    continue;
                }

                var scriptDir = ResolveScriptDir(scriptName);
                if (scriptDir == null)
                {
                    AppendLog($"  [오류] {scriptName} 위치를 찾을 수 없습니다.\n", error: true);
                    summary.Add((tag, scriptName, -5, 0));
                    continue;
                }

                TxtStatus.Text = $"{stepLabel}실행 중: {scriptName} ...";
                AppendLog($"\n> python {scriptName} {cliArgs}\nCWD: {scriptDir}\n\n");

                _sw = Stopwatch.StartNew();
                _statusTimer = new DispatcherTimerLite(() =>
                {
                    if (_sw != null && _sw.IsRunning)
                        TxtStatus.Text = $"{stepLabel}실행 중 ({_sw.Elapsed.TotalSeconds:F1}s): {scriptName}";
                }, TimeSpan.FromMilliseconds(500));

                int exitCode = -1;
                try
                {
                    exitCode = await RunPythonAsync(scriptDir, scriptName, cliArgs, _cts.Token);
                }
                catch (OperationCanceledException)
                {
                    AppendLog("\n  [사용자 취소] 진행 중 단계 종료 — 이후 단계 스킵\n", error: true);
                    exitCode = -10;
                }
                catch (Exception ex)
                {
                    AppendLog($"\n  [예외] {ex.GetType().Name}: {ex.Message}\n", error: true);
                    exitCode = -11;
                }
                finally
                {
                    _sw?.Stop();
                    _statusTimer?.Stop();
                }
                double secs = _sw?.Elapsed.TotalSeconds ?? 0;
                summary.Add((tag, scriptName, exitCode, secs));

                if (exitCode == 0)
                    AppendLog($"\n  ----- {stepLabel}완료 ({secs:F1}s, ExitCode=0) -----\n");
                else
                    AppendLog($"\n  ----- {stepLabel}실패 ({secs:F1}s, ExitCode={exitCode}) — 다음 단계 진행 -----\n", error: true);

                _proc = null;
            }

            totalSw.Stop();
            PbRun.IsIndeterminate = false;
            PbRun.Visibility = Visibility.Collapsed;
            BtnCancel.IsEnabled = false;
            SetRunButtonsEnabled(_connected);
            SetSchemaButtonsEnabled(_connected);
            _proc = null;
            _cts?.Dispose();
            _cts = null;

            int okCount = summary.Count(s => s.ExitCode == 0);
            int failCount = summary.Count - okCount;
            double totalSecs = totalSw.Elapsed.TotalSeconds;

            AppendLog($"\n##### 1~4 일괄 실행 종료 (총 {totalSecs:F1}s) #####\n");
            AppendLog($"  성공 {okCount}/4, 실패 {failCount}/4\n");
            foreach (var s in summary)
            {
                string status = s.ExitCode == 0 ? "OK" : $"FAIL({s.ExitCode})";
                AppendLog($"  - {s.Tag,-8} {s.Script,-30} {status,-12} {s.Secs,7:F1}s\n");
            }
            AppendLog($"##############################################\n");

            TxtStatus.Text = failCount == 0
                ? $"1~4 일괄 실행 완료 ({totalSecs:F1}s) — 성공 {okCount}/4"
                : $"1~4 일괄 실행 종료 ({totalSecs:F1}s) — 성공 {okCount}/4, 실패 {failCount}/4";
        }

        /// <summary>빌더 태그를 사람이 읽기 쉬운 라벨로 변환 (로그용).</summary>
        private static string DescribeBuilder(string tag) => tag switch
        {
            "feature" => "Feature Vector (TB_ROUTE_FEATURE_VECTOR · 30D)",
            "context" => "Context Vector (TB_ROUTE_CONTEXT_VECTOR · 24D)",
            "group"   => "Design Group (TB_ROUTE_DESIGN_GROUP)",
            "segment" => "Segment Template (TB_ROUTE_SEGMENT_TEMPLATE)",
            _         => tag,
        };

        // ───────────────────────────────────────────
        // 리포트 엑셀 저장
        // ───────────────────────────────────────────

        /// <summary>가장 최근의 DDL 실행 리포트(_lastReport)를 .xlsx 로 저장.</summary>
        private void BtnExportReport_Click(object sender, RoutedEventArgs e)
        {
            if (_lastReport == null)
            {
                AppendLog("[오류] 저장할 리포트가 없습니다. 스키마 초기화/재생성/전체생성 먼저 실행하세요.\n", error: true);
                return;
            }

            var suggested = $"VectorDb_{SafeName(_lastReport.Action)}_{_lastReport.StartedAt:yyyyMMdd_HHmmss}.xlsx";
            var dlg = new SaveFileDialog
            {
                Filter = "Excel Workbook (*.xlsx)|*.xlsx",
                FileName = SafeFileName(suggested),
                Title = "VectorDB DDL 실행 리포트 저장",
            };
            if (dlg.ShowDialog() != true) return;

            BtnExportReport.IsEnabled = false;
            try
            {
                ExportReportToExcel(_lastReport, dlg.FileName);
                AppendLog($"\n[리포트 저장] {dlg.FileName}\n");
                TxtStatus.Text = $"리포트 저장 완료: {Path.GetFileName(dlg.FileName)}";
            }
            catch (IOException ex)
            {
                AppendLog($"[리포트 저장 실패] 파일 열림/접근 불가: {ex.Message}\n", error: true);
            }
            catch (Exception ex)
            {
                AppendLog($"[리포트 저장 실패] {ex.GetType().Name}: {ex.Message}\n", error: true);
            }
            finally
            {
                BtnExportReport.IsEnabled = true;
            }
        }

        private static string SafeName(string s)
            => new string(s.Select(c => char.IsLetterOrDigit(c) || c == '_' ? c : '_').ToArray());

        private static string SafeFileName(string s)
        {
            foreach (var c in Path.GetInvalidFileNameChars()) s = s.Replace(c, '_');
            return s;
        }

        private static void ExportReportToExcel(DdlReport rpt, string outPath)
        {
            using var wb = new XLWorkbook();

            // ── 시트 1: 요약 ────────────────────────────────────────
            var ws1 = wb.Worksheets.Add("01_요약");
            int r = 1;
            WriteTitle(ws1, ref r, "VectorDB DDL 실행 리포트");
            WriteKv(ws1, ref r, "Action",        rpt.Action);
            WriteKv(ws1, ref r, "Builder Tag",   rpt.Builder);
            WriteKv(ws1, ref r, "Started At",    rpt.StartedAt.ToString("yyyy-MM-dd HH:mm:ss"));
            WriteKv(ws1, ref r, "Finished At",   rpt.FinishedAt.ToString("yyyy-MM-dd HH:mm:ss"));
            WriteKv(ws1, ref r, "Elapsed",       $"{rpt.TotalMs:F0} ms");
            WriteKv(ws1, ref r, "DDL Files",     rpt.Files.Count.ToString());
            WriteKv(ws1, ref r, "Statements OK", rpt.TotalOk.ToString());
            WriteKv(ws1, ref r, "Statements FAIL", rpt.TotalFail.ToString());
            ws1.Columns().AdjustToContents();

            // ── 시트 2: 파일별 실행 내역 ─────────────────────────────
            var ws2 = wb.Worksheets.Add("02_문장내역");
            r = 1;
            WriteTitle(ws2, ref r, "Statement Execution Log");
            WriteHeaders(ws2, r++, "File", "Order", "Preview", "Status", "Elapsed(ms)", "Error");
            foreach (var f in rpt.Files)
            {
                foreach (var s in f.Statements)
                {
                    ws2.Cell(r, 1).Value = f.FileName;
                    ws2.Cell(r, 2).Value = s.Order;
                    ws2.Cell(r, 3).Value = s.Preview;
                    ws2.Cell(r, 4).Value = s.Success ? "OK" : "FAIL";
                    ws2.Cell(r, 4).Style.Font.FontColor = s.Success
                        ? XLColor.FromArgb(0x2E, 0x7D, 0x32)
                        : XLColor.FromArgb(0xB0, 0, 0x20);
                    ws2.Cell(r, 5).Value = s.ElapsedMs;
                    ws2.Cell(r, 5).Style.NumberFormat.Format = "#,##0.0";
                    ws2.Cell(r, 6).Value = s.Error ?? "";
                    r++;
                }
            }
            ws2.Columns().AdjustToContents();
            ws2.Column(3).Width = Math.Min(ws2.Column(3).Width, 80);
            ws2.Column(6).Width = Math.Min(ws2.Column(6).Width, 80);

            // ── 시트 3: 테이블 전후 상태 ─────────────────────────────
            var ws3 = wb.Worksheets.Add("03_테이블상태");
            r = 1;
            WriteTitle(ws3, ref r, "Table State (Before / After)");
            WriteHeaders(ws3, r++, "Table", "Before Exists", "Before Rows", "Before HNSW",
                                     "After Exists", "After Rows", "After HNSW", "Delta Rows");
            foreach (var (b, a) in rpt.BeforeState.Zip(rpt.AfterState))
            {
                ws3.Cell(r, 1).Value = b.TableName;
                ws3.Cell(r, 2).Value = b.Exists ? "O" : "X";
                ws3.Cell(r, 3).Value = b.RowCount;
                ws3.Cell(r, 3).Style.NumberFormat.Format = "#,##0";
                ws3.Cell(r, 4).Value = b.HasHnswIndex ? "O" : "X";
                ws3.Cell(r, 5).Value = a.Exists ? "O" : "X";
                ws3.Cell(r, 6).Value = a.RowCount;
                ws3.Cell(r, 6).Style.NumberFormat.Format = "#,##0";
                ws3.Cell(r, 7).Value = a.HasHnswIndex ? "O" : "X";
                ws3.Cell(r, 8).Value = a.RowCount - b.RowCount;
                ws3.Cell(r, 8).Style.NumberFormat.Format = "+#,##0;-#,##0;0";
                r++;
            }
            ws3.Columns().AdjustToContents();

            // ── 시트 4: 파일 메타 ────────────────────────────────────
            var ws4 = wb.Worksheets.Add("04_파일메타");
            r = 1;
            WriteTitle(ws4, ref r, "DDL Files");
            WriteHeaders(ws4, r++, "File Name", "Path", "Size (chars)", "Statements", "OK", "FAIL");
            foreach (var f in rpt.Files)
            {
                int ok = f.Statements.Count(s => s.Success);
                int fail = f.Statements.Count(s => !s.Success);
                ws4.Cell(r, 1).Value = f.FileName;
                ws4.Cell(r, 2).Value = f.FilePath;
                ws4.Cell(r, 3).Value = f.FileSizeChars;
                ws4.Cell(r, 3).Style.NumberFormat.Format = "#,##0";
                ws4.Cell(r, 4).Value = f.Statements.Count;
                ws4.Cell(r, 5).Value = ok;
                ws4.Cell(r, 6).Value = fail;
                r++;
            }
            ws4.Columns().AdjustToContents();

            wb.SaveAs(outPath);
        }

        private static void WriteTitle(IXLWorksheet ws, ref int row, string text)
        {
            var c = ws.Cell(row, 1);
            c.Value = text;
            c.Style.Font.Bold = true;
            c.Style.Font.FontSize = 12;
            c.Style.Font.FontColor = XLColor.FromArgb(0x20, 0x70, 0xC0);
            row++;
        }
        private static void WriteHeaders(IXLWorksheet ws, int row, params string[] headers)
        {
            for (int i = 0; i < headers.Length; i++)
            {
                var c = ws.Cell(row, i + 1);
                c.Value = headers[i];
                c.Style.Font.Bold = true;
                c.Style.Fill.BackgroundColor = XLColor.FromArgb(0xE8, 0xF0, 0xFA);
            }
        }
        private static void WriteKv(IXLWorksheet ws, ref int row, string key, string value)
        {
            ws.Cell(row, 1).Value = key;
            ws.Cell(row, 1).Style.Font.Bold = true;
            ws.Cell(row, 2).Value = value;
            row++;
        }

        /// <summary>DDL 파일을 찾아 Npgsql로 문장별 실행. 재사용 가능한 공통 루틴.
        /// <paramref name="fileRunCollect"/> 가 주어지면 문장별 결과를 리포트에 누적한다.</summary>
        /// <returns>(성공 문장 수, 실패 문장 수). DDL 파일 자체를 못 찾으면 (-1, -1).</returns>
        private async Task<(int Ok, int Fail)> RunDdlAsync(string ddlFile, string label,
                                                            DdlFileRun? fileRunCollect = null)
        {
            var scriptDir = ResolveScriptDir(ddlFile);
            if (scriptDir == null)
            {
                AppendLog($"[{label} 오류] DDL 파일을 찾을 수 없음: {ddlFile}\n", error: true);
                return (-1, -1);
            }
            var ddlPath = Path.Combine(scriptDir, ddlFile);
            string ddlSql;
            try { ddlSql = File.ReadAllText(ddlPath, Encoding.UTF8); }
            catch (Exception ex)
            {
                AppendLog($"[{label} 오류] DDL 파일 읽기 실패: {ex.Message}\n", error: true);
                return (-1, -1);
            }

            if (fileRunCollect != null)
            {
                fileRunCollect.FileName = ddlFile;
                fileRunCollect.FilePath = ddlPath;
                fileRunCollect.FileSizeChars = ddlSql.Length;
            }

            AppendLog($"\n===== {label} 시작 =====\n");
            AppendLog($"DDL 파일: {ddlPath} ({ddlSql.Length:N0} chars)\n\n");

            var sw = Stopwatch.StartNew();
            int ok = 0, fail = 0;
            int order = 1;
            try
            {
                await using var conn = new NpgsqlConnection(_db.ConnectionString);
                await conn.OpenAsync();

                var statements = SplitSqlStatements(ddlSql);
                foreach (var stmt in statements)
                {
                    if (string.IsNullOrWhiteSpace(stmt)) continue;
                    string preview = stmt.Length > 80 ? stmt[..80].Replace('\n', ' ') + "…" : stmt.Replace('\n', ' ');
                    AppendLog($"> {preview}\n");
                    var stmtSw = Stopwatch.StartNew();
                    var result = new StmtResult { Order = order++, Preview = preview };
                    try
                    {
                        await using var cmd = new NpgsqlCommand(stmt, conn);
                        await cmd.ExecuteNonQueryAsync();
                        stmtSw.Stop();
                        ok++;
                        result.Success = true;
                        result.ElapsedMs = stmtSw.Elapsed.TotalMilliseconds;
                    }
                    catch (Exception ex)
                    {
                        stmtSw.Stop();
                        fail++;
                        result.Success = false;
                        result.Error = $"{ex.GetType().Name}: {ex.Message}";
                        result.ElapsedMs = stmtSw.Elapsed.TotalMilliseconds;
                        AppendLog($"  [fail] {result.Error}\n", error: true);
                    }
                    fileRunCollect?.Statements.Add(result);
                }
                sw.Stop();
                AppendLog($"\n===== {label} 완료 ({sw.Elapsed.TotalSeconds:F1}s, 성공 {ok} / 실패 {fail}) =====\n");
            }
            catch (Exception ex)
            {
                sw.Stop();
                AppendLog($"\n===== {label} 실패 ({sw.Elapsed.TotalSeconds:F1}s): {ex.Message} =====\n", error: true);
            }
            return (ok, fail);
        }

        /// <summary>지정 테이블들의 현재 스냅샷(존재/행수/HNSW) 을 가져온다 — 리포트용.</summary>
        private async Task<List<TableSnapshot>> SnapshotTablesAsync(IEnumerable<string> tableNames)
        {
            var list = new List<TableSnapshot>();
            try
            {
                await using var conn = new NpgsqlConnection(_db.ConnectionString);
                await conn.OpenAsync();
                foreach (var t in tableNames)
                {
                    var snap = new TableSnapshot { TableName = t };
                    try
                    {
                        snap.Exists = await TableExistsAsync(conn, t);
                        if (snap.Exists)
                        {
                            await using var cnt = new NpgsqlCommand($"SELECT COUNT(*) FROM \"{t}\";", conn);
                            snap.RowCount = (long)(await cnt.ExecuteScalarAsync())!;
                            await using var idx = new NpgsqlCommand(@"
                                SELECT COUNT(*) FROM pg_index i
                                  JOIN pg_class ci ON ci.oid = i.indexrelid
                                  JOIN pg_class ct ON ct.oid = i.indrelid
                                  JOIN pg_am    am ON am.oid = ci.relam
                                 WHERE ct.relname = @t AND am.amname = 'hnsw';", conn);
                            idx.Parameters.AddWithValue("t", t);
                            snap.HasHnswIndex = Convert.ToInt32(await idx.ExecuteScalarAsync()) > 0;
                        }
                    }
                    catch { /* 개별 실패는 무시 */ }
                    list.Add(snap);
                }
            }
            catch (Exception ex)
            {
                AppendLog($"[스냅샷 실패] {ex.Message}\n", error: true);
            }
            return list;
        }

        /// <summary>DROP TABLE ... CASCADE 실행 (뷰/함수/FK 모두 정리). 멱등 (IF EXISTS).</summary>
        private async Task<(bool Ok, string? Error)> DropTableCascadeAsync(string tableName)
        {
            try
            {
                await using var conn = new NpgsqlConnection(_db.ConnectionString);
                await conn.OpenAsync();
                await using var cmd = new NpgsqlCommand(
                    $"DROP TABLE IF EXISTS \"{tableName}\" CASCADE;", conn);
                await cmd.ExecuteNonQueryAsync();
                return (true, null);
            }
            catch (Exception ex)
            {
                return (false, $"{ex.GetType().Name}: {ex.Message}");
            }
        }

        /// <summary>빌더 실행 전 DB 상태 사전 검사.
        ///   (1) 대상 벡터 테이블이 없으면 자동으로 해당 DDL 실행 (CREATE TABLE IF NOT EXISTS 기반이라 안전)
        ///   (2) 소스 데이터 테이블이 누락되면 거부 (외부 수급 필요하므로 자동 생성 불가)
        /// </summary>
        /// <returns>true = 실행 가능 / false = 실행 거부</returns>
        private async Task<bool> PreflightCheckAsync(string tag)
        {
            if (_builderScriptFile.TryGetValue(tag, out var scriptFile) && ResolveScriptDir(scriptFile) == null)
            {
                AppendLog($"\n[preflight failed] Builder script not found: {scriptFile}\n", error: true);
                AppendLog("Searched script locations:\n" + DescribeSearchLocations(_settings.ScriptDirectory, includeToolsDir: true) + "\n", error: true);
                AppendLog("Set ScriptDirectory in vectordbgen.settings.json or place the builder script near the executable/project root.\n\n", error: true);
                return false;
            }

            if (_builderDdlFile.TryGetValue(tag, out var requiredDdlFile) && ResolveScriptDir(requiredDdlFile) == null)
            {
                AppendLog($"\n[preflight failed] DDL file not found: {requiredDdlFile}\n", error: true);
                AppendLog("Searched DDL locations:\n" + DescribeSearchLocations(_settings.DdlDirectory, includeToolsDir: false) + "\n", error: true);
                AppendLog("Set DdlDirectory in vectordbgen.settings.json or place the DDL file near the executable/project root.\n\n", error: true);
                return false;
            }

            // ── 1. 대상 벡터 테이블 자동 DDL ──────────────────────────
            if (_builderTargetTable.TryGetValue(tag, out var targetTable)
                && _builderDdlFile.TryGetValue(tag, out var ddlFile))
            {
                bool targetExists;
                try
                {
                    await using var conn = new NpgsqlConnection(_db.ConnectionString);
                    await conn.OpenAsync();
                    targetExists = await TableExistsAsync(conn, targetTable);
                }
                catch (Exception ex)
                {
                    AppendLog($"[사전 검사 실패] DB 접근 오류: {ex.Message}\n", error: true);
                    return false;
                }

                if (!targetExists)
                {
                    AppendLog($"\n[자동 DDL] 대상 테이블 {targetTable} 이(가) 존재하지 않음 → {ddlFile} 자동 실행\n");
                    var (ok, fail) = await RunDdlAsync(ddlFile, "자동 DDL");
                    if (ok < 0)
                    {
                        AppendLog("[실행 거부] DDL 파일을 찾을 수 없어 자동 생성 실패.\n", error: true);
                        return false;
                    }
                    // 재확인
                    try
                    {
                        await using var conn = new NpgsqlConnection(_db.ConnectionString);
                        await conn.OpenAsync();
                        if (!await TableExistsAsync(conn, targetTable))
                        {
                            AppendLog($"[실행 거부] 자동 DDL 후에도 {targetTable} 이(가) 생성되지 않음.\n", error: true);
                            AppendLog("           (DDL 로그 확인: 권한 부족 또는 pgvector 확장 미설치 가능)\n", error: true);
                            return false;
                        }
                    }
                    catch (Exception ex)
                    {
                        AppendLog($"[실행 거부] 테이블 재확인 오류: {ex.Message}\n", error: true);
                        return false;
                    }
                }
            }

            // ── 2. 소스 데이터 테이블 존재 검사 ───────────────────────
            if (!_builderSourceRequirements.TryGetValue(tag, out var required))
                return true;   // 정의 안된 빌더면 통과

            var missing = new List<string>();
            try
            {
                await using var conn = new NpgsqlConnection(_db.ConnectionString);
                await conn.OpenAsync();
                foreach (var t in required)
                    if (!await TableExistsAsync(conn, t)) missing.Add(t);
            }
            catch (Exception ex)
            {
                AppendLog($"[사전 검사 실패] DB 접근 오류: {ex.Message}\n", error: true);
                return false;
            }

            if (missing.Count == 0) return true;

            AppendLog("\n[실행 거부] 필수 소스 테이블 누락:\n", error: true);
            foreach (var t in missing)
                AppendLog($"  - {t}\n", error: true);
            AppendLog("\n해결 방법:\n", error: true);
            AppendLog("  1) 이 테이블들은 벡터 DDL 대상이 아님 — 상위 BIM/Route 데이터 수급 파이프라인을\n", error: true);
            AppendLog("     먼저 실행해 테이블을 채운 뒤 재시도하세요.\n", error: true);
            if (missing.Contains("TB_BIM_OBSTACLES"))
                AppendLog("  2) Context Vector 빌드는 TB_BIM_OBSTACLES 없이 불가합니다.\n", error: true);
            if (missing.Contains("TB_ROUTE_PATH"))
                AppendLog("  2) TB_ROUTE_PATH 는 모든 빌더의 최소 요구사항입니다.\n", error: true);
            AppendLog("\n", error: true);
            return false;
        }

        /// <summary>PostgreSQL 문법을 인식하는 SQL 분할기.
        /// 세미콜론(;)을 기준으로 분할하되 아래 컨텍스트 내부의 ;는 무시한다:
        ///   - $$...$$ / $tag$...$tag$ 달러-따옴표 (함수 본문)
        ///   - '...' 단일-따옴표 (리터럴, '' 이스케이프 포함)
        ///   - -- 한 줄 주석
        ///   - /* ... */ 블록 주석</summary>
        private static List<string> SplitSqlStatements(string sql)
        {
            var result = new List<string>();
            var sb = new StringBuilder();
            bool inLineComment = false;
            bool inBlockComment = false;
            bool inSingleQuote = false;
            string? dollarTag = null;   // null 이 아니면 $tag$ 내부

            int i = 0;
            int n = sql.Length;
            while (i < n)
            {
                char ch = sql[i];
                char next = i + 1 < n ? sql[i + 1] : '\0';

                if (inLineComment)
                {
                    sb.Append(ch);
                    if (ch == '\n') inLineComment = false;
                    i++; continue;
                }
                if (inBlockComment)
                {
                    sb.Append(ch);
                    if (ch == '*' && next == '/')
                    {
                        sb.Append(next);
                        i += 2;
                        inBlockComment = false;
                        continue;
                    }
                    i++; continue;
                }

                if (dollarTag != null)
                {
                    if (ch == '$' && i + dollarTag.Length <= n
                        && sql.Substring(i, dollarTag.Length) == dollarTag)
                    {
                        sb.Append(dollarTag);
                        i += dollarTag.Length;
                        dollarTag = null;
                        continue;
                    }
                    sb.Append(ch);
                    i++; continue;
                }

                if (inSingleQuote)
                {
                    sb.Append(ch);
                    if (ch == '\'')
                    {
                        if (next == '\'')
                        {
                            sb.Append(next);
                            i += 2;
                            continue;
                        }
                        inSingleQuote = false;
                    }
                    i++; continue;
                }

                if (ch == '-' && next == '-')
                {
                    inLineComment = true;
                    sb.Append(ch); sb.Append(next);
                    i += 2; continue;
                }
                if (ch == '/' && next == '*')
                {
                    inBlockComment = true;
                    sb.Append(ch); sb.Append(next);
                    i += 2; continue;
                }
                if (ch == '\'')
                {
                    inSingleQuote = true;
                    sb.Append(ch);
                    i++; continue;
                }
                if (ch == '$')
                {
                    int j = i + 1;
                    while (j < n)
                    {
                        char c = sql[j];
                        if (c == '$') break;
                        if (!(char.IsLetterOrDigit(c) || c == '_')) { j = -1; break; }
                        j++;
                    }
                    if (j > i && j < n && sql[j] == '$')
                    {
                        string tag = sql.Substring(i, j - i + 1);
                        sb.Append(tag);
                        i = j + 1;
                        dollarTag = tag;
                        continue;
                    }
                    sb.Append(ch);
                    i++; continue;
                }

                if (ch == ';')
                {
                    var s = sb.ToString().Trim();
                    if (!string.IsNullOrWhiteSpace(s)) result.Add(s);
                    sb.Clear();
                    i++; continue;
                }

                sb.Append(ch);
                i++;
            }

            var tail = sb.ToString().Trim();
            if (!string.IsNullOrWhiteSpace(tail)) result.Add(tail);
            return result;
        }

        private void CmbBuilder_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (PnlFeature == null) return;  // 초기화 중
            PnlFeature.Visibility = Visibility.Collapsed;
            PnlContext.Visibility = Visibility.Collapsed;
            PnlGroup.Visibility = Visibility.Collapsed;
            PnlSegment.Visibility = Visibility.Collapsed;
            var tag = (CmbBuilder.SelectedItem as ComboBoxItem)?.Tag as string;
            switch (tag)
            {
                case "feature": PnlFeature.Visibility = Visibility.Visible; break;
                case "context": PnlContext.Visibility = Visibility.Visible; break;
                case "group":   PnlGroup.Visibility   = Visibility.Visible; break;
                case "segment": PnlSegment.Visibility = Visibility.Visible; break;
            }
        }

        // ───────────────────────────────────────────
        // 파일 선택 Browse
        // ───────────────────────────────────────────
        private void BtnBrowseSaveNorm_Click(object sender, RoutedEventArgs e)
        {
            var dlg = new SaveFileDialog
            {
                Filter = "JSON (*.json)|*.json",
                FileName = "db_norm_params.json",
                OverwritePrompt = false,
            };
            if (dlg.ShowDialog() == true) TxtSaveNorm.Text = dlg.FileName;
        }

        private void BtnBrowseSaveJson_Click(object sender, RoutedEventArgs e)
        {
            var dlg = new SaveFileDialog
            {
                Filter = "JSON (*.json)|*.json",
                FileName = "db_feature_vectors.json",
                OverwritePrompt = false,
            };
            if (dlg.ShowDialog() == true) TxtSaveJson.Text = dlg.FileName;
        }

        // ───────────────────────────────────────────
        // 실행
        // ───────────────────────────────────────────
        private async void BtnRun_Click(object sender, RoutedEventArgs e)
        {
            if (!_connected)
            {
                AppendLog("[오류] DB 접속 테스트를 먼저 통과해야 합니다.\n", error: true);
                return;
            }
            var tag = (CmbBuilder.SelectedItem as ComboBoxItem)?.Tag as string;
            if (string.IsNullOrEmpty(tag)) return;

            // 사전 검사 — 빌더별 필수 소스 테이블 + 대상 테이블 자동 DDL
            if (!await PreflightCheckAsync(tag))
            {
                return;   // 로그에 원인 출력 후 실행 거부
            }

            string scriptName; string cliArgs;
            try { (scriptName, cliArgs) = BuildCommand(tag); }
            catch (Exception ex)
            {
                AppendLog($"[오류] 인자 구성 실패: {ex.Message}\n", error: true);
                return;
            }

            var scriptDir = ResolveScriptDir(scriptName);
            if (scriptDir == null)
            {
                AppendLog($"[오류] {scriptName} 위치를 찾을 수 없습니다.\n", error: true);
                return;
            }

            SetRunButtonsEnabled(false);
            SetSchemaButtonsEnabled(false);
            BtnCancel.IsEnabled = true;
            PbRun.Visibility = Visibility.Visible;
            PbRun.IsIndeterminate = true;
            TxtStatus.Text = $"실행 중: {scriptName} ...";
            AppendLog($"\n===== {scriptName} 시작 =====\n> python {scriptName} {cliArgs}\nCWD: {scriptDir}\n\n");

            _cts = new CancellationTokenSource();
            _sw = Stopwatch.StartNew();
            _statusTimer = new DispatcherTimerLite(() =>
            {
                if (_sw != null && _sw.IsRunning)
                    TxtStatus.Text = $"실행 중 ({_sw.Elapsed.TotalSeconds:F1}s): {scriptName}";
            }, TimeSpan.FromMilliseconds(500));

            int exitCode = -1;
            try
            {
                exitCode = await RunPythonAsync(scriptDir, scriptName, cliArgs, _cts.Token);
            }
            catch (OperationCanceledException)
            {
                AppendLog("\n[사용자 취소 요청] 프로세스 종료 중...\n", error: true);
            }
            catch (Exception ex)
            {
                AppendLog($"\n[예외] {ex.Message}\n", error: true);
            }
            finally
            {
                _sw?.Stop();
                _statusTimer?.Stop();
                PbRun.IsIndeterminate = false;
                PbRun.Visibility = Visibility.Collapsed;
                SetRunButtonsEnabled(_connected);
                SetSchemaButtonsEnabled(_connected);
                BtnCancel.IsEnabled = false;

                double secs = _sw?.Elapsed.TotalSeconds ?? 0;
                if (exitCode == 0)
                {
                    TxtStatus.Text = $"완료 ({secs:F1}s) — ExitCode=0";
                    AppendLog($"\n===== 완료 ({secs:F1}s, ExitCode=0) =====\n");
                }
                else
                {
                    TxtStatus.Text = $"실패 ({secs:F1}s) — ExitCode={exitCode}";
                    AppendLog($"\n===== 실패 ({secs:F1}s, ExitCode={exitCode}) =====\n", error: true);
                }
                _proc = null;
                _cts?.Dispose();
                _cts = null;
            }
        }

        private void BtnCancel_Click(object sender, RoutedEventArgs e)
        {
            try
            {
                _cts?.Cancel();
                if (_proc != null && !_proc.HasExited)
                    _proc.Kill(entireProcessTree: true);
            }
            catch (Exception ex)
            {
                AppendLog($"[취소 오류] {ex.Message}\n", error: true);
            }
        }

        // ───────────────────────────────────────────
        // Python 실행 (async stdout/stderr streaming)
        // ───────────────────────────────────────────
        private async Task<int> RunPythonAsync(string cwd, string script, string args, CancellationToken ct)
        {
            var psi = new ProcessStartInfo
            {
                FileName = string.IsNullOrWhiteSpace(_settings.PythonExe) ? "python" : ExpandPath(_settings.PythonExe),
                WorkingDirectory = cwd,
                Arguments = $"-u {script} {args}",   // -u: unbuffered stdout
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
            };
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";

            _proc = new Process { StartInfo = psi, EnableRaisingEvents = true };
            _proc.OutputDataReceived += (s, ev) =>
            {
                if (ev.Data != null) AppendLog(ev.Data + "\n");
            };
            _proc.ErrorDataReceived += (s, ev) =>
            {
                if (ev.Data != null) AppendLog(ev.Data + "\n", error: true);
            };

            if (!_proc.Start())
                throw new InvalidOperationException("python 프로세스 시작 실패 (PATH 확인)");

            _proc.BeginOutputReadLine();
            _proc.BeginErrorReadLine();

            try { await _proc.WaitForExitAsync(ct); }
            catch (OperationCanceledException)
            {
                if (!_proc.HasExited) _proc.Kill(entireProcessTree: true);
                throw;
            }
            _proc.WaitForExit();
            return _proc.ExitCode;
        }

        // ───────────────────────────────────────────
        // CLI 명령 구성
        // ───────────────────────────────────────────
        private static string CliQuote(string value)
            => "\"" + (value ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";

        private (string Script, string Args) BuildCommand(string tag)
        {
            string db = $"--host {CliQuote(_db.Host)} --port {_db.Port} --dbname {CliQuote(_db.Database)} --user {CliQuote(_db.User)} --password {CliQuote(_db.Password)}";
            switch (tag)
            {
                case "feature":
                    {
                        var sb = new StringBuilder();
                        sb.Append($"from-db {db}");
                        var saveNorm = TxtSaveNorm.Text?.Trim() ?? "";
                        if (!string.IsNullOrEmpty(saveNorm)) sb.Append($" --save_norm \"{saveNorm}\"");
                        var saveJson = TxtSaveJson.Text?.Trim() ?? "";
                        if (!string.IsNullOrEmpty(saveJson)) sb.Append($" --save_json \"{saveJson}\"");
                        return ("BuildFeatureVectors.py", sb.ToString());
                    }
                case "context":
                    {
                        var sb = new StringBuilder(db);
                        if (double.TryParse(TxtStartRadius.Text, out var sr)) sb.Append($" --start-radius {sr}");
                        if (double.TryParse(TxtEndRadius.Text, out var er)) sb.Append($" --end-radius {er}");
                        var lim = TxtCtxLimit.Text?.Trim() ?? "";
                        if (!string.IsNullOrEmpty(lim) && int.TryParse(lim, out var lv) && lv > 0)
                            sb.Append($" --limit {lv}");
                        return ("BuildContextVectors.py", sb.ToString());
                    }
                case "group":
                    {
                        var sb = new StringBuilder(db);
                        if (int.TryParse(TxtMinMembers.Text, out var mm) && mm > 0)
                            sb.Append($" --min-members {mm}");
                        return ("BuildDesignGroups.py", sb.ToString());
                    }
                case "segment":
                    {
                        var sb = new StringBuilder(db);
                        var role = (CmbSegRole.SelectedItem as ComboBoxItem)?.Tag as string ?? "";
                        if (!string.IsNullOrEmpty(role)) sb.Append($" --role {role}");
                        return ("BuildSegmentTemplates.py", sb.ToString());
                    }
                default:
                    throw new InvalidOperationException($"Unknown builder tag: {tag}");
            }
        }

        /// <summary>실행 디렉터리 기준으로 위쪽으로 6단계까지 탐색하며 파일을 찾는다.
        /// 최후에는 RoutingAI 표준 위치(d:\DINNO\DEV\AI-AutoRouting\RoutingAI\src) 를 fallback 으로 시도.</summary>
        private string? ResolveScriptDir(string scriptName)
        {
            var configuredDirectory = scriptName.EndsWith(".sql", StringComparison.OrdinalIgnoreCase)
                ? _settings.DdlDirectory
                : _settings.ScriptDirectory;
            var resolved = ResolveFilePath(scriptName, configuredDirectory, includeToolsDir: !scriptName.EndsWith(".sql", StringComparison.OrdinalIgnoreCase));
            if (resolved != null) return Path.GetDirectoryName(resolved);

            var exeDir = AppDomain.CurrentDomain.BaseDirectory;
            string? dir = exeDir;
            for (int i = 0; i < 6 && dir != null; i++)
            {
                if (File.Exists(Path.Combine(dir, scriptName))) return dir;
                dir = Path.GetDirectoryName(dir);
            }
            // fallback — RoutingAI 표준 src 디렉터리
            var fallback = @"d:\DINNO\DEV\AI-AutoRouting\RoutingAI\src";
            return File.Exists(Path.Combine(fallback, scriptName)) ? fallback : null;
        }

        // ───────────────────────────────────────────
        // 로그
        // ───────────────────────────────────────────
        private string? ResolveDdlPath(string ddlFile)
            => ResolveFilePath(ddlFile, _settings.DdlDirectory, includeToolsDir: false);

        private string? ResolveBuilderScriptPath(string scriptName)
            => ResolveFilePath(scriptName, _settings.ScriptDirectory, includeToolsDir: true);

        private string? ResolveFilePath(string fileName, string? configuredDirectory, bool includeToolsDir)
        {
            if (Path.IsPathRooted(fileName))
                return File.Exists(fileName) ? Path.GetFullPath(fileName) : null;

            var candidateDirs = new List<string>();
            if (!string.IsNullOrWhiteSpace(configuredDirectory))
                candidateDirs.Add(ExpandPath(configuredDirectory));

            foreach (var root in GetSearchRoots())
            {
                candidateDirs.Add(root);
                if (includeToolsDir) candidateDirs.Add(Path.Combine(root, "Tools"));
            }

            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var dir in candidateDirs)
            {
                if (string.IsNullOrWhiteSpace(dir) || !seen.Add(dir)) continue;
                var path = Path.Combine(dir, fileName);
                if (File.Exists(path)) return Path.GetFullPath(path);
            }

            return null;
        }

        private string DescribeSearchLocations(string? configuredDirectory, bool includeToolsDir)
        {
            var dirs = new List<string>();
            if (!string.IsNullOrWhiteSpace(configuredDirectory))
                dirs.Add(ExpandPath(configuredDirectory));
            foreach (var root in GetSearchRoots())
            {
                dirs.Add(root);
                if (includeToolsDir) dirs.Add(Path.Combine(root, "Tools"));
            }

            return string.Join("\n", dirs.Distinct(StringComparer.OrdinalIgnoreCase).Select(d => $"  - {d}"));
        }

        private void AppendLog(string text, bool error = false)
        {
            if (!Dispatcher.CheckAccess())
            {
                Dispatcher.BeginInvoke(new Action(() => AppendLog(text, error)));
                return;
            }
            TxtLog.AppendText(text);
            TxtLog.ScrollToEnd();
        }

        private void BtnSaveLog_Click(object sender, RoutedEventArgs e)
        {
            var dlg = new SaveFileDialog
            {
                Filter = "Text (*.txt)|*.txt|Log (*.log)|*.log",
                FileName = $"vectordb_build_{DateTime.Now:yyyyMMdd_HHmmss}.log",
            };
            if (dlg.ShowDialog() != true) return;
            try
            {
                File.WriteAllText(dlg.FileName, TxtLog.Text, Encoding.UTF8);
                TxtStatus.Text = $"로그 저장: {Path.GetFileName(dlg.FileName)}";
            }
            catch (Exception ex)
            {
                AppendLog($"[로그 저장 실패] {ex.Message}\n", error: true);
            }
        }

        private void BtnClearLog_Click(object sender, RoutedEventArgs e)
        {
            TxtLog.Clear();
        }

        private void BtnClose_Click(object sender, RoutedEventArgs e)
        {
            if (_proc != null && !_proc.HasExited)
            {
                var r = MessageBox.Show(this,
                    "빌드가 실행 중입니다. 중단하고 닫을까요?",
                    "VectorDB 생성", MessageBoxButton.YesNo, MessageBoxImage.Question);
                if (r != MessageBoxResult.Yes) return;
                try { _cts?.Cancel(); _proc.Kill(entireProcessTree: true); } catch { }
            }
            Close();
        }
    }

    /// <summary>UI 스레드에서 주기적으로 Action을 호출하는 경량 타이머.</summary>
    internal sealed class DispatcherTimerLite
    {
        private readonly System.Windows.Threading.DispatcherTimer _t;
        public DispatcherTimerLite(Action tick, TimeSpan interval)
        {
            _t = new System.Windows.Threading.DispatcherTimer { Interval = interval };
            _t.Tick += (s, e) => tick();
            _t.Start();
        }
        public void Stop() => _t.Stop();
    }
}
