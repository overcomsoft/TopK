using System.ComponentModel;
using System.Globalization;
using System.Runtime.CompilerServices;
using System.Windows;
using Microsoft.Win32;
using Npgsql;
using RoutingAI.Standalone;
using TopK.ThreeDViewer.Models;
using TopK.ThreeDViewer.Services;

namespace TopK.ThreeDViewer;

/// <summary>
/// Top-K 검색이 의존하는 7개 특징점·패턴 DB(Tools/*.py)를 개별 또는 순서대로 생성하는 다이얼로그.
/// 각 생성기는 클릭 시 대상 테이블 존재 여부를 먼저 확인하고, 없으면 해당 스크립트의
/// create-schema를 실행한 뒤 build/run-all을 수행한다. Docs/BendFeaturePoint_Development_Plan.md,
/// Docs/UtilityPipeGroup_TopK_Development_Plan.md에서 설계한 CLI 관례를 그대로 재사용한다.
/// </summary>
public partial class FeatureGenerationDialog : Window
{
    private readonly DbConfig _db;
    private readonly PythonGeneratorRunner _runner;
    private readonly List<GeneratorRowViewModel> _rows;
    private CancellationTokenSource? _cts;
    private bool _isRunning;
    private string _activeProjectScopeKey = "";
    private string _activeModelRevisionKey = "";
    private bool _hasActiveScope;

    public FeatureGenerationDialog(DbConfig db, string pythonExe)
    {
        InitializeComponent();
        _db = db;
        _runner = new PythonGeneratorRunner(pythonExe, PythonGeneratorRunner.ResolveToolsDirectory());
        _rows = GeneratorCatalog.All
            .Select((def, index) => new GeneratorRowViewModel(def, index + 1))
            .ToList();
        GridGenerators.ItemsSource = _rows;
        Loaded += async (_, _) => await RefreshScopeAsync();
        Closing += (_, _) => _cts?.Cancel();
    }

    private async void RefreshScope_Click(object sender, RoutedEventArgs e) => await RefreshScopeAsync();

    private async Task RefreshScopeAsync()
    {
        TxtActiveScope.Text = "ACTIVE scope: 확인 중...";
        try
        {
            await using var conn = new NpgsqlConnection(_db.ToConnectionString());
            await conn.OpenAsync();
            const string sql = """
                SELECT "PROJECT_SCOPE_KEY","MODEL_REVISION_KEY"
                  FROM "TB_ROUTE_SOURCE_SCOPE_MANIFEST" WHERE "STATUS"='ACTIVE'
                 ORDER BY "PROJECT_SCOPE_KEY","MODEL_REVISION_KEY" LIMIT 2
                """;
            await using var cmd = new NpgsqlCommand(sql, conn);
            var rows = new List<(string, string)>();
            await using (var reader = await cmd.ExecuteReaderAsync())
            {
                while (await reader.ReadAsync())
                    rows.Add((reader.GetString(0), reader.GetString(1)));
            }

            if (rows.Count == 1)
            {
                (_activeProjectScopeKey, _activeModelRevisionKey) = rows[0];
                _hasActiveScope = true;
                TxtActiveScope.Text = $"ACTIVE scope: {_activeProjectScopeKey} / {_activeModelRevisionKey}";
            }
            else
            {
                _hasActiveScope = false;
                _activeProjectScopeKey = _activeModelRevisionKey = "";
                TxtActiveScope.Text = rows.Count == 0
                    ? "ACTIVE scope: 없음 — Utility Pipe Group Vector/Bend Feature Point는 비활성화됩니다 (Tools/ApplyRouteSourceScope.py로 먼저 등록 필요)"
                    : $"ACTIVE scope: {rows.Count}개(복수) — 명확히 하나로 정리해야 합니다";
            }
        }
        catch (Exception ex)
        {
            _hasActiveScope = false;
            TxtActiveScope.Text = $"ACTIVE scope 확인 실패: {ex.Message}";
        }
        UpdateRowAvailability();
    }

    private void UpdateRowAvailability()
    {
        foreach (var row in _rows)
        {
            var scopeOk = !row.Definition.RequiresActiveScope || _hasActiveScope;
            row.CanRun = !_isRunning && scopeOk;
        }
    }

    private async void RunOne_Click(object sender, RoutedEventArgs e)
    {
        if (((FrameworkElement)sender).Tag is not GeneratorRowViewModel row) return;
        await RunGeneratorAsync(row);
    }

    private async void RunAll_Click(object sender, RoutedEventArgs e)
    {
        foreach (var row in _rows)
        {
            if (row.Definition.RequiresActiveScope && !_hasActiveScope)
            {
                AppendLog($"[skip] {row.DisplayName}: ACTIVE scope가 없어 건너뜁니다.", error: true);
                continue;
            }
            if (row.Definition.IsOptional)
            {
                AppendLog($"[skip] {row.DisplayName}: 선택 항목이라 전체 실행에서 제외합니다. " +
                          "필요하면 해당 행의 [생성] 버튼으로 개별 실행하세요.");
                continue;
            }
            var ok = await RunGeneratorAsync(row);
            if (!ok)
            {
                AppendLog("=== 전체 실행 중단 (이전 단계 실패) ===", error: true);
                break;
            }
        }
    }

    private async Task<bool> RunGeneratorAsync(GeneratorRowViewModel row)
    {
        if (_isRunning) return false;
        _isRunning = true;
        UpdateRowAvailability();
        BtnRunAll.IsEnabled = false;
        BtnCancel.IsEnabled = true;
        row.Status = "실행중";
        _cts = new CancellationTokenSource();
        var success = false;
        try
        {
            AppendLog($"=== [{row.DisplayName}] 시작 ===");

            // 데이터 입력 전 항상 대상 테이블 스키마를 먼저 점검한다.
            var exists = await CheckTableExistsAsync(row.Definition.TargetTable);
            if (exists)
            {
                AppendLog($"[{row.DisplayName}] 스키마 사전검토: {row.Definition.TargetTable} 이미 존재");
            }
            else if (row.Definition.BuildCreateSchemaArgs is { } buildCreateSchemaArgs)
            {
                AppendLog($"[{row.DisplayName}] 스키마 사전검토: {row.Definition.TargetTable} 없음 → create-schema 실행");
                var schemaArgs = buildCreateSchemaArgs(_db);
                var schemaExit = await _runner.RunAsync(
                    row.Definition.ScriptFile, schemaArgs, AppendLog, _cts.Token);
                if (schemaExit != 0)
                {
                    AppendLog($"[{row.DisplayName}] create-schema 실패 (exit={schemaExit})", error: true);
                    row.Status = "실패";
                    return false;
                }
                exists = await CheckTableExistsAsync(row.Definition.TargetTable);
                if (!exists)
                {
                    AppendLog($"[{row.DisplayName}] create-schema 후에도 {row.Definition.TargetTable}이 없습니다.", error: true);
                    row.Status = "실패";
                    return false;
                }
                AppendLog($"[{row.DisplayName}] 스키마 생성 완료 ({row.Definition.TargetTable})");
            }
            else
            {
                // 별도 create-schema 커맨드가 없는 스크립트(예: 30D 특징벡터) — 이번 실행이
                // 자체적으로 CREATE TABLE IF NOT EXISTS를 수행한 뒤 데이터를 입력한다.
                AppendLog($"[{row.DisplayName}] 스키마 사전검토: {row.Definition.TargetTable} 없음 → 이번 실행에서 자체 생성됩니다");
            }

            var buildArgs = row.Definition.BuildArgs(_db, _activeProjectScopeKey, _activeModelRevisionKey);
            var exitCode = await _runner.RunAsync(row.Definition.ScriptFile, buildArgs, AppendLog, _cts.Token);
            success = exitCode == 0;
            row.Status = success ? "성공" : "실패";
            row.LastRun = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture);
            AppendLog($"=== [{row.DisplayName}] 종료 (exit={exitCode}) ===", error: !success);
            return success;
        }
        catch (OperationCanceledException)
        {
            row.Status = "취소됨";
            AppendLog($"=== [{row.DisplayName}] 취소됨 ===", error: true);
            return false;
        }
        catch (Exception ex)
        {
            row.Status = "실패";
            AppendLog($"[{row.DisplayName}] 오류: {ex.Message}", error: true);
            return false;
        }
        finally
        {
            _isRunning = false;
            BtnRunAll.IsEnabled = true;
            BtnCancel.IsEnabled = false;
            _cts?.Dispose();
            _cts = null;
            UpdateRowAvailability();
        }
    }

    private async Task<bool> CheckTableExistsAsync(string tableName)
    {
        await using var conn = new NpgsqlConnection(_db.ToConnectionString());
        await conn.OpenAsync();
        return await _runner.TableExistsAsync(conn, tableName);
    }

    private void AppendLog(string line, bool error = false)
    {
        Dispatcher.Invoke(() =>
        {
            TxtLog.AppendText(line + Environment.NewLine);
            TxtLog.ScrollToEnd();
        });
    }

    private void Cancel_Click(object sender, RoutedEventArgs e)
    {
        _cts?.Cancel();
        _runner.CancelCurrent();
    }

    private void SaveLog_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new SaveFileDialog
        {
            Filter = "Log files (*.log;*.txt)|*.log;*.txt|All files (*.*)|*.*",
            FileName = $"feature_generation_{DateTime.Now:yyyyMMdd_HHmmss}.log"
        };
        if (dialog.ShowDialog(this) == true)
            System.IO.File.WriteAllText(dialog.FileName, TxtLog.Text);
    }

    private void ClearLog_Click(object sender, RoutedEventArgs e) => TxtLog.Clear();

    private void Close_Click(object sender, RoutedEventArgs e) => Close();
}

/// <summary>DataGrid 한 행에 대응하는 뷰모델. 상태/마지막 실행 시각이 바뀔 때 UI에 즉시 반영되도록
/// INotifyPropertyChanged를 구현한다.</summary>
public sealed class GeneratorRowViewModel : INotifyPropertyChanged
{
    private string _status = "대기";
    private string _lastRun = "-";
    private bool _canRun = true;

    public GeneratorRowViewModel(GeneratorDefinition definition, int order)
    {
        Definition = definition;
        Order = order;
    }

    public GeneratorDefinition Definition { get; }
    public int Order { get; }
    public string DisplayName => Definition.DisplayName;
    public string TargetTable => Definition.TargetTable;

    public string Status
    {
        get => _status;
        set { _status = value; OnPropertyChanged(); }
    }

    public string LastRun
    {
        get => _lastRun;
        set { _lastRun = value; OnPropertyChanged(); }
    }

    public bool CanRun
    {
        get => _canRun;
        set { _canRun = value; OnPropertyChanged(); }
    }

    public event PropertyChangedEventHandler? PropertyChanged;
    private void OnPropertyChanged([CallerMemberName] string? name = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}
