using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Media3D;
using System.Windows.Threading;
using HelixToolkit.Wpf;
using RubberBandRouting.Engine;

namespace RubberBandRouting.Viewer;

public partial class MainWindow : Window
{
    private readonly List<Visual3D> _areaVisuals = new();
    private readonly List<Visual3D> _obstacleVisuals = new();
    private readonly List<Visual3D> _equipmentVisuals = new();
    private readonly List<Visual3D> _ductVisuals = new();
    private readonly List<Visual3D> _pocVisuals = new();
    private readonly List<Visual3D> _selectedEndpointVisuals = new();
    private readonly List<Visual3D> _routeVisuals = new();
    private readonly List<Visual3D> _selectedStepVisuals = new();
    private readonly List<Visual3D> _featureVisuals = new();
    private readonly List<Visual3D> _existingRouteVisuals = new();
    private readonly PostgresRoutingDataLoader _loader = new();
    private readonly List<ResultRow> _resultRows = new();
    private List<TaskRow>? _lastRoutedTaskRows;
    private string _lastRoutedScope = string.Empty;
    private DispatcherTimer? _settingChangeTimer;
    private readonly Stopwatch _fpsWatch = Stopwatch.StartNew();
    // Maps a clickable pipe visual (auto-route or existing-route tube) back to the ResultRow/
    // ExistingRoutePath it represents, so a 3D click can show its properties and select the
    // matching data grid row.
    private readonly Dictionary<Visual3D, object> _visualOwners = new();
    private Point _viewportMouseDownPos;
    private RoutingScene? _scene;
    private List<TaskRow> _allTaskRows = new();
    private List<TaskRow> _visibleTaskRows = new();
    private bool _loadedInitialScene;
    private int _frameCount;
    private static readonly Color[] ExistingRouteGroupColors =
    {
        Color.FromRgb(255, 179, 71),
        Color.FromRgb(125, 211, 252),
        Color.FromRgb(167, 139, 250),
        Color.FromRgb(52, 211, 153),
        Color.FromRgb(251, 113, 133),
        Color.FromRgb(250, 204, 21),
        Color.FromRgb(96, 165, 250),
        Color.FromRgb(244, 114, 182),
        Color.FromRgb(45, 212, 191),
        Color.FromRgb(251, 146, 60),
        Color.FromRgb(190, 242, 100),
        Color.FromRgb(216, 180, 254)
    };
    private static readonly Color[] AutoRouteGroupColors =
    {
        Color.FromRgb(34, 197, 94),
        Color.FromRgb(236, 72, 153),
        Color.FromRgb(14, 165, 233),
        Color.FromRgb(239, 68, 68),
        Color.FromRgb(168, 85, 247),
        Color.FromRgb(20, 184, 166),
        Color.FromRgb(132, 204, 22),
        Color.FromRgb(249, 115, 22),
        Color.FromRgb(99, 102, 241),
        Color.FromRgb(244, 63, 94)
    };

    private string? _lastProjectDisplayName;

    public MainWindow()
    {
        InitializeComponent();
        // Default to the native C++ engine when its DLL is present; NativeRubberBandEngine.IsAvailable
        // probes rb_create() once and caches the result, so this is cheap even if the DLL is missing.
        ChkUseNativeEngine.IsChecked = NativeRubberBandEngine.IsAvailable;
        ApplySavedConnectionSettings();
        Loaded += async (_, _) => await LoadProjectsAsync();
        Closing += (_, _) => SaveConnectionSettings();
        CompositionTarget.Rendering += CompositionTarget_Rendering;
    }

    private void ApplySavedConnectionSettings()
    {
        var settings = ViewerSettings.Load();
        TxtHost.Text = settings.Host;
        TxtPort.Text = settings.PortText;
        TxtUser.Text = settings.Username;
        TxtDatabase.Text = settings.Database;
        var password = settings.DecryptPassword();
        if (password != null) TxtPassword.Password = password;
        _lastProjectDisplayName = settings.LastProjectDisplayName;
    }

    private void SaveConnectionSettings()
    {
        var settings = new ViewerSettings
        {
            Host = string.IsNullOrWhiteSpace(TxtHost.Text) ? "localhost" : TxtHost.Text.Trim(),
            Port = ReadInt(TxtPort.Text, 5432),
            Username = string.IsNullOrWhiteSpace(TxtUser.Text) ? "postgres" : TxtUser.Text.Trim(),
            Database = string.IsNullOrWhiteSpace(TxtDatabase.Text) ? "DDW_AI_DB" : TxtDatabase.Text.Trim(),
            LastProjectDisplayName = (CmbProjects.SelectedItem as RoutingProject)?.DisplayName ?? _lastProjectDisplayName
        };
        settings.EncryptPassword(TxtPassword.Password);
        settings.Save();
    }

    private async void BtnLoadProjects_Click(object sender, RoutedEventArgs e) => await LoadProjectsAsync();
    private async void BtnLoadScene_Click(object sender, RoutedEventArgs e) => await LoadSceneAsync();
    private async void BtnRouteGroup_Click(object sender, RoutedEventArgs e) => await RouteGroupAsync();
    private async void BtnRouteUtility_Click(object sender, RoutedEventArgs e) => await RouteUtilityAsync();
    private async void BtnShowExistingRoutes_Click(object sender, RoutedEventArgs e) => await ShowExistingRoutesAsync();

    private void BtnCompareRoutes_Click(object sender, RoutedEventArgs e)
    {
        if (_resultRows.Count == 0)
        {
            TxtStatus.Text = "비교할 자동설계 경로가 없습니다. 먼저 라우팅을 실행하세요.";
            return;
        }
        var window = new CompareRoutesWindow(BuildCompareEntries()) { Owner = this };
        window.Show();
    }

    private List<RouteCompareEntry> BuildCompareEntries() => _resultRows.Select(r =>
    {
        var existing = r.MatchedExistingRoute;
        var existingPoints = existing != null ? OrientedExistingRoutePoints(existing, r.Task) : new List<Vec3>();
        var matchNote = existing == null
            ? "매칭 없음"
            : !string.IsNullOrWhiteSpace(r.Task.RoutePathGuid) && string.Equals(r.Task.RoutePathGuid, existing.RoutePathGuid, StringComparison.OrdinalIgnoreCase)
                ? "GUID 직접매칭"
                : "조건 fallback";
        var autoSteps = r.StepRows.Select(s => new SegmentInfoRow(s.Index, s.SegmentType, s.Start, s.End, s.Direction, s.LengthMm, s.Reason)).ToList();
        var existingSteps = BuildExistingStepRows(existingPoints);
        return new RouteCompareEntry(r.Index, r.Group, r.Utility, r.StartPoC, r.EndPoC, matchNote, existingPoints, r.RouteSegments, existingSteps, autoSteps, r.FeatureWaypoints);
    }).ToList();

    private static List<SegmentInfoRow> BuildExistingStepRows(List<Vec3> points)
    {
        var rows = new List<SegmentInfoRow>();
        for (var i = 0; i < points.Count - 1; i++)
        {
            var segment = new RouteSegment(points[i], points[i + 1]);
            var reason = i == 0 ? "기존경로 시작점" : i == points.Count - 2 ? "기존경로 종단점" : "실시공 꺾임점";
            rows.Add(new SegmentInfoRow(
                i + 1,
                i == 0 ? "시작" : "꺾임",
                FormatVec(segment.Start),
                FormatVec(segment.End),
                SegmentDirection(segment),
                segment.Length,
                reason));
        }
        return rows;
    }

    private void BtnResetCamera_Click(object sender, RoutedEventArgs e) => FitProjectToViewport();

    private void BtnClearRoutes_Click(object sender, RoutedEventArgs e)
    {
        ClearVisuals(_routeVisuals);
        ClearVisuals(_selectedStepVisuals);
        _resultRows.Clear();
        GridResults.ItemsSource = null;
        GridResults.ItemsSource = _resultRows;
        SetDetailGrids(null);
        GridAnalysis.ItemsSource = new[] { new AnalysisRow("상태", "자동설계 경로가 삭제되었습니다.") };
        UpdateVisibleObjectText();
    }

    private void BtnDeleteSelectedRoute_Click(object sender, RoutedEventArgs e)
    {
        if (GridResults.SelectedItem is not ResultRow selected) return;
        _resultRows.Remove(selected);
        GridResults.ItemsSource = null;
        GridResults.ItemsSource = _resultRows;
        if (_resultRows.Count > 0) GridResults.SelectedIndex = Math.Clamp(selected.Index - 1, 0, _resultRows.Count - 1);
        RedrawAutoRoutes(GridResults.SelectedItem as ResultRow);
        TxtStatus.Text = $"경로 #{selected.Index} ({selected.StartPoC} → {selected.EndPoC})를 삭제했습니다.";
        UpdateVisibleObjectText();
    }

    private async void BtnReplayDebug_Click(object sender, RoutedEventArgs e)
    {
        if (GridResults.SelectedItem is not ResultRow selectedRow)
        {
            TxtStatus.Text = "리플레이할 자동설계 경로를 결과 목록에서 먼저 선택하세요.";
            return;
        }

        var wasNative = ChkUseNativeEngine.IsChecked;
        if (ChkUseNativeEngine.IsChecked == true)
        {
            // Temporarily switch to Managed C# to capture segment-by-segment debug text trace
            ChkUseNativeEngine.IsChecked = false;
        }

        var task = selectedRow.Task;
        TxtStatus.Text = $"[{task.Group}] {task.Utility} 경로 디버그 리플레이 중...";

        var logPath = @"d:\DINNO\DEV\AI-AutoRouting\TopKGen\Docs\RubberBandRouting_DebugTrace.log";
        try
        {
            var dir = Path.GetDirectoryName(logPath);
            if (dir != null) Directory.CreateDirectory(dir);
            File.WriteAllText(logPath, $"=== RubberBand Replay Debug Trace - {DateTime.Now} ===\r\n" +
                                       $"Utility Group: {task.Group}, Utility: {task.Utility}\r\n" +
                                       $"Start PoC: {task.SourceName} ({task.Start.X:F0}, {task.Start.Y:F0}, {task.Start.Z:F0})\r\n" +
                                       $"End PoC: {task.TargetName} ({task.End.X:F0}, {task.End.Y:F0}, {task.End.Z:F0})\r\n\r\n");
        }
        catch { }

        var options = ReadOptions();
        var baseObstacles = _scene.CollisionObstacles.ToList();
        var engine = ResolveEngine();

        ResultRow? newRow = null;
        await RunBusyAsync("디버그 리플레이 연산 중...", async () =>
        {
            var totalSw = Stopwatch.StartNew();
            var computed = await Task.Run(() => ComputeRoutes(new[] { new TaskRow(selectedRow.Index, selectedRow.Task) }, options, baseObstacles, engine));
            totalSw.Stop();
            if (computed.Count > 0)
            {
                newRow = computed[0];
            }
        });

        if (wasNative == true)
        {
            ChkUseNativeEngine.IsChecked = true;
        }

        if (newRow != null)
        {
            var idx = _resultRows.IndexOf(selectedRow);
            if (idx >= 0)
            {
                _resultRows[idx] = newRow;
            }
            else
            {
                _resultRows.Add(newRow);
            }

            GridResults.ItemsSource = null;
            GridResults.ItemsSource = _resultRows;
            GridResults.SelectedItem = newRow;
            GridResults.ScrollIntoView(newRow);
            RedrawAutoRoutes(newRow);

            TxtStatus.Text = $"디버그 리플레이 완료. 로그가 Docs/RubberBandRouting_DebugTrace.log에 기록되었습니다.";

            try
            {
                if (File.Exists(logPath))
                {
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = "notepad.exe",
                        Arguments = $"\"{logPath}\"",
                        UseShellExecute = true
                    });
                }
            }
            catch (Exception ex)
            {
                TxtStatus.Text += $" (메모장 실행 실패: {ex.Message})";
            }
        }
    }

    private async Task LoadProjectsAsync()
    {
        RoutingProject? selectedProject = null;

        await RunBusyAsync("프로젝트 목록을 불러오는 중...", async () =>
        {
            var projects = await _loader.ListProjectsAsync(ReadDbOptions());
            CmbProjects.ItemsSource = projects;
            var remembered = _lastProjectDisplayName != null
                ? projects.FirstOrDefault(p => p.DisplayName == _lastProjectDisplayName)
                : null;
            if (remembered != null)
            {
                CmbProjects.SelectedItem = remembered;
                selectedProject = remembered;
            }
            else if (projects.Count > 0)
            {
                CmbProjects.SelectedIndex = 0;
                selectedProject = projects[0];
            }
            TxtStatus.Text = $"프로젝트 {projects.Count}건을 불러왔습니다.";
            GridAnalysis.ItemsSource = projects.Take(20).Select(p => new AnalysisRow(p.Index.ToString(CultureInfo.InvariantCulture), p.DisplayName)).ToList();
            // Connection succeeded (the query above didn't throw), so remember these credentials
            // for next launch — this is the point we actually know host/user/password/database work.
            if (projects.Count > 0) SaveConnectionSettings();
        });

        if (!_loadedInitialScene && selectedProject != null)
        {
            _loadedInitialScene = true;
            if ((CmbProjects.SelectedItem as RoutingProject) != selectedProject)
            {
                CmbProjects.SelectedItem = selectedProject;
            }
            await LoadSceneAsync();
        }
    }

    private async Task LoadSceneAsync()
    {
        if (CmbProjects.SelectedItem is not RoutingProject project)
        {
            TxtStatus.Text = "먼저 프로젝트를 선택하세요.";
            return;
        }

        await RunBusyAsync("PostgreSQL에서 씬을 불러오는 중...", async () =>
        {
            ClearSceneVisuals();
            ClearVisuals(_routeVisuals);
            ClearVisuals(_selectedEndpointVisuals);
            ClearVisuals(_selectedStepVisuals);
            ClearVisuals(_existingRouteVisuals);
            _resultRows.Clear();
            GridResults.ItemsSource = null;
            GridResults.ItemsSource = _resultRows;

            _scene = await _loader.LoadSceneAsync(ReadDbOptions(), project);
            DrawScene(_scene);
            BuildTaskRows(_scene);
            PopulateUtilityFilters();
            ApplyTaskFilter();
            DrawExistingRoutePaths();
            SetSceneAnalysis(_scene);
            SetDetailGrids(null);
            var warning = _scene.LoadWarnings.Count > 0 ? $" | ⚠ 로드 경고 {_scene.LoadWarnings.Count}건" : string.Empty;
            TxtStatus.Text = $"씬 로딩 완료: 라우팅 태스크 {_scene.Tasks.Count}건, 기존경로 {_scene.ExistingRoutePaths.Count}건.{warning}";
            ScheduleProjectZoomFit();

            // Save credentials and last project immediately upon successful scene load,
            // so we don't rely only on the window's Closing event.
            SaveConnectionSettings();
        });
    }

    private void BuildTaskRows(RoutingScene scene)
    {
        _allTaskRows = scene.Tasks.Select((t, i) => new TaskRow(i + 1, t)).ToList();
    }

    private void PopulateUtilityFilters()
    {
        var groups = _allTaskRows
            .Select(x => string.IsNullOrWhiteSpace(x.Group) ? "?" : x.Group)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(x => x)
            .ToList();
        CmbUtilityGroups.ItemsSource = groups;
        if (groups.Count > 0 && CmbUtilityGroups.SelectedIndex < 0) CmbUtilityGroups.SelectedIndex = 0;
    }

    private void CmbUtilityGroups_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_allTaskRows.Count == 0) return;
        var group = SelectedGroup();
        var utilities = _allTaskRows
            .Where(x => GroupMatches(x, group))
            .Select(x => x.Utility)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(x => x)
            .ToList();
        ListUtilities.ItemsSource = utilities;
        if (utilities.Count > 0) ListUtilities.SelectedIndex = 0;
        ApplyTaskFilter();
        RefreshExistingRoutesForCurrentGroup();
    }

    private void ListUtilities_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        ApplyTaskFilter();
    }

    private void ApplyTaskFilter()
    {
        var group = SelectedGroup();
        var utility = SelectedUtility();
        _visibleTaskRows = _allTaskRows
            .Where(x => GroupMatches(x, group))
            .Where(x => string.IsNullOrWhiteSpace(utility) || string.Equals(x.Utility, utility, StringComparison.OrdinalIgnoreCase))
            .ToList();
        GridTasks.ItemsSource = _visibleTaskRows;
        if (_visibleTaskRows.Count > 0) GridTasks.SelectedIndex = 0;
        else HighlightTaskEndpoints(null);

        // Keep 3D auto-routes and feature points synchronized with the active filter.
        RedrawAutoRoutes(GridResults.SelectedItem as ResultRow);
    }

    private void GridTasks_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        HighlightTaskEndpoints(GridTasks.SelectedItem as TaskRow);
    }

    private void HighlightTaskEndpoints(TaskRow? row)
    {
        ClearVisuals(_selectedEndpointVisuals);
        if (row == null)
        {
            GridAnalysis.ItemsSource = null;
            return;
        }

        var diameter = row.Task.DiameterMm > 0 ? Math.Clamp(row.Task.DiameterMm, 80, 520) : 180;
        AddSphere(row.Task.Start, Math.Max(diameter * 0.75, 180), Brushes.Red, _selectedEndpointVisuals);
        AddSphere(row.Task.End, Math.Max(diameter * 0.75, 180), Brushes.DodgerBlue, _selectedEndpointVisuals);
        DrawPath(new[] { row.Task.Start, row.Task.End }, Brushes.White, Math.Max(diameter * 0.15, 24), _selectedEndpointVisuals);

        // Load features from the matched existing route
        var matchedPath = FindMatchedExistingRoute(row.Task);
        List<AnalysisRow> analysisRows = new()
        {
            new AnalysisRow("유틸리티 그룹", row.Group),
            new AnalysisRow("유틸리티", row.Utility),
            new AnalysisRow("시작 PoC", row.SourceName),
            new AnalysisRow("종단 PoC", row.TargetName),
            new AnalysisRow("배관 관경(mm)", row.Task.DiameterMm.ToString(CultureInfo.InvariantCulture)),
            new AnalysisRow("기존경로 매칭", matchedPath != null ? (matchedPath.RoutePathGuid ?? "조건 매칭") : "매칭 없음")
        };

        if (matchedPath != null)
        {
            var featureInfo = BuildFeatureWaypoints(row.Task, ReadOptions());
            var features = featureInfo.Waypoints;

            // Draw features as semitransparent cubes in _selectedEndpointVisuals
            var halfSize = 69.0;
            foreach (var feature in features)
            {
                var color = FeatureRoleColor(feature.Role);
                var box = new Aabb(
                    new Vec3(feature.Position.X - halfSize, feature.Position.Y - halfSize, feature.Position.Z - halfSize),
                    new Vec3(feature.Position.X + halfSize, feature.Position.Y + halfSize, feature.Position.Z + halfSize));
                var owner = new TaskFeatureInfo(feature, row);
                AddBox(box, Color.FromArgb(128, color.R, color.G, color.B), _selectedEndpointVisuals, owner);
            }

            analysisRows.Add(new AnalysisRow("기존설계 특징점", $"{features.Count} 개"));
            analysisRows.Add(new AnalysisRow("특징점 상세", FeatureRoleSummary(features)));
        }
        else
        {
            analysisRows.Add(new AnalysisRow("기존설계 특징점", "0 개 (기존설계 매칭 없음)"));
        }

        GridAnalysis.ItemsSource = analysisRows;
    }

    private void Viewport_PreviewMouseLeftButtonDown(object sender, MouseButtonEventArgs e) => _viewportMouseDownPos = e.GetPosition(Viewport);

    // A click is only recognized if the mouse barely moved between down/up — otherwise this was
    // a camera drag (HelixToolkit's own rotate/pan handling), not a pick attempt.
    private void Viewport_PreviewMouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        var pos = e.GetPosition(Viewport);
        if ((pos - _viewportMouseDownPos).Length > 4) return;
        PickVisualAt(pos);
    }

    private void PickVisualAt(Point position)
    {
        if (VisualTreeHelper.HitTest(Viewport.Viewport, position) is not RayMeshGeometry3DHitTestResult hit) return;
        if (hit.VisualHit is not Visual3D visual) return;
        if (!_visualOwners.TryGetValue(visual, out var owner)) return;

        switch (owner)
        {
            case FeaturePointInfo featureInfo:
                // Select the owning route first (refreshes highlight + the normal per-route tabs),
                // then overwrite the analysis grid with the specific feature point's own properties.
                GridResults.SelectedItem = featureInfo.Route;
                GridResults.ScrollIntoView(featureInfo.Route);
                ShowFeaturePointProperties(featureInfo);
                break;
            case TaskFeatureInfo taskFeatureInfo:
                ShowTaskFeatureProperties(taskFeatureInfo);
                break;
            case ResultRow resultRow:
                GridResults.SelectedItem = resultRow;
                GridResults.ScrollIntoView(resultRow);
                break;
            case ExistingRoutePath existingPath:
                SelectExistingRoutePath(existingPath);
                break;
        }
    }

    private void ShowFeaturePointProperties(FeaturePointInfo info)
    {
        var feature = info.Feature;
        var route = info.Route;
        GridAnalysis.ItemsSource = new[]
        {
            new AnalysisRow("종류", "특징점 (기존설계 특징점)"),
            new AnalysisRow("역할", FeatureRoleLabel(feature.Role)),
            new AnalysisRow("필수 여부", feature.Required ? "필수 (Required)" : "선택"),
            new AnalysisRow("위치", FormatVec(feature.Position)),
            new AnalysisRow("소속 경로", $"[{route.Group}] {route.Utility}"),
            new AnalysisRow("소속 경로 시작PoC", route.StartPoC),
            new AnalysisRow("소속 경로 종단PoC", route.EndPoC)
        };
    }

    private void ShowTaskFeatureProperties(TaskFeatureInfo info)
    {
        var feature = info.Feature;
        var task = info.Task;
        GridAnalysis.ItemsSource = new[]
        {
            new AnalysisRow("종류", "기존설계 특징점 (태스크 선택)"),
            new AnalysisRow("역할", FeatureRoleLabel(feature.Role)),
            new AnalysisRow("필수 여부", feature.Required ? "필수 (Required)" : "선택"),
            new AnalysisRow("위치", FormatVec(feature.Position)),
            new AnalysisRow("소속 태스크", $"[{task.Group}] {task.Utility}"),
            new AnalysisRow("태스크 시작PoC", task.SourceName),
            new AnalysisRow("태스크 종단PoC", task.TargetName)
        };
    }

    // Existing-design pipes have no dedicated result grid of their own — clicking one selects the
    // matching row in the "③ 개별 PoC" task grid (if a matching task exists) and shows the path's
    // own properties (GUID, utility, endpoints, diameter, length) in the analysis grid.
    private void SelectExistingRoutePath(ExistingRoutePath path)
    {
        var match = _visibleTaskRows.FirstOrDefault(t => !string.IsNullOrWhiteSpace(t.Task.RoutePathGuid) && string.Equals(t.Task.RoutePathGuid, path.RoutePathGuid, StringComparison.OrdinalIgnoreCase))
            ?? _visibleTaskRows.FirstOrDefault(t => ReferenceEquals(FindMatchedExistingRoute(t.Task), path));
        if (match != null)
        {
            GridTasks.SelectedItem = match;
            GridTasks.ScrollIntoView(match);
        }

        var length = 0.0;
        for (var i = 1; i < path.Points.Count; i++) length += (path.Points[i] - path.Points[i - 1]).Length;
        GridAnalysis.ItemsSource = new[]
        {
            new AnalysisRow("종류", "기존설계 배관"),
            new AnalysisRow("기존경로 GUID", path.RoutePathGuid),
            new AnalysisRow("그룹", path.Group ?? "-"),
            new AnalysisRow("유틸리티", path.Utility ?? "-"),
            new AnalysisRow("시작PoC", path.SourceName ?? "-"),
            new AnalysisRow("종단PoC", path.TargetName ?? "-"),
            new AnalysisRow("관경 (mm)", path.DiameterMm > 0 ? path.DiameterMm.ToString("N0", CultureInfo.InvariantCulture) : "-"),
            new AnalysisRow("점 개수", path.Points.Count.ToString(CultureInfo.InvariantCulture)),
            new AnalysisRow("총 길이 (mm)", length.ToString("N0", CultureInfo.InvariantCulture))
        };
    }

    private async Task RouteGroupAsync()
    {
        if (await EnsureSceneAsync())
        {
            var group = SelectedGroup();
            await RouteRowsAsync(_allTaskRows.Where(x => GroupMatches(x, group)).ToList(), $"그룹 {group}");
        }
    }

    private async Task RouteUtilityAsync()
    {
        if (await EnsureSceneAsync())
        {
            var utility = SelectedUtility();
            var rows = _visibleTaskRows.Where(x => string.IsNullOrWhiteSpace(utility) || string.Equals(x.Utility, utility, StringComparison.OrdinalIgnoreCase)).ToList();
            await RouteRowsAsync(rows, $"유틸리티 {utility}");
        }
    }

    private async Task<bool> EnsureSceneAsync()
    {
        if (_scene != null) return true;
        await LoadSceneAsync();
        return _scene != null;
    }

    private async Task ShowExistingRoutesAsync()
    {
        if (!await EnsureSceneAsync() || _scene == null) return;
        await RunBusyAsync("PostgreSQL 기존경로를 그리는 중...", () =>
        {
            var count = DrawExistingRoutePaths();
            GridAnalysis.ItemsSource = new[]
            {
                new AnalysisRow("기존경로", $"{count:N0} / {_scene.ExistingRoutePaths.Count:N0} 표시"),
                new AnalysisRow("필터", $"그룹={SelectedGroup()}"),
                new AnalysisRow("색상", "유틸리티 그룹별 고정 색상"),
                new AnalysisRow("관경", "TB_ROUTE_PATH.SOURCE_SIZE 반영"),
                new AnalysisRow("DB", "TB_ROUTE_PATH + TB_ROUTE_SEGMENTS + TB_ROUTE_SEGMENT_DETAIL")
            };
            TxtStatus.Text = $"기존경로 표시 완료: {count}/{_scene.ExistingRoutePaths.Count}.";
            return Task.CompletedTask;
        });
    }

    private int DrawExistingRoutePaths()
    {
        if (_scene == null) return 0;
        ClearVisuals(_existingRouteVisuals);
        var paths = FilterExistingRoutes(_scene.ExistingRoutePaths).Take(250).ToList();
        for (var i = 0; i < paths.Count; i++)
        {
            var diameter = ExistingRouteDiameter(paths[i]);
            DrawPath(paths[i].Points, ExistingRouteBrush(paths[i].Group), diameter, _existingRouteVisuals, paths[i]);
        }
        UpdateVisibleObjectText();
        return paths.Count;
    }

    private static double ExistingRouteDiameter(ExistingRoutePath path)
    {
        if (path.DiameterMm > 0) return Math.Clamp(path.DiameterMm, 20, 500);
        return 50;
    }

    private static Brush ExistingRouteBrush(string? group)
    {
        var normalized = NormalizeGroup(group);
        unchecked
        {
            var hash = 17;
            foreach (var ch in normalized) hash = hash * 31 + char.ToUpperInvariant(ch);
            var color = ExistingRouteGroupColors[(hash & 0x7FFFFFFF) % ExistingRouteGroupColors.Length];
            var brush = new SolidColorBrush(color);
            brush.Freeze();
            return brush;
        }
    }
    private IEnumerable<ExistingRoutePath> FilterExistingRoutes(IEnumerable<ExistingRoutePath> paths)
    {
        var group = SelectedGroup();
        return paths
            .Where(x => string.IsNullOrWhiteSpace(group) || string.Equals(NormalizeGroup(x.Group), group, StringComparison.OrdinalIgnoreCase));
    }

    private void RefreshExistingRoutesForCurrentGroup()
    {
        if (_scene == null || TglExistingRoutes.IsChecked != true) return;
        var count = DrawExistingRoutePaths();
        TxtStatus.Text = $"그룹 '{SelectedGroup()}' 기준으로 기존경로 필터링: {count}/{_scene.ExistingRoutePaths.Count}.";
    }

    private async Task RouteRowsAsync(IReadOnlyList<TaskRow> rows, string scope)
    {
        if (_scene == null) return;
        if (rows.Count == 0)
        {
            TxtStatus.Text = "선택된 라우팅 작업이 없습니다.";
            return;
        }

        _lastRoutedTaskRows = rows.ToList();
        _lastRoutedScope = scope;

        // Clear or initialize the debug log file for a fresh batch trace
        var logPath = @"d:\DINNO\DEV\AI-AutoRouting\TopKGen\Docs\RubberBandRouting_DebugTrace.log";
        try
        {
            var dir = Path.GetDirectoryName(logPath);
            if (dir != null) Directory.CreateDirectory(dir);
            File.WriteAllText(logPath, $"=== RubberBand AutoRouting Debug Trace - {DateTime.Now} (Scope: {scope}) ===\r\n\r\n");
        }
        catch { }

        const int maxTasks = 200;
        var max = Math.Min(rows.Count, maxTasks);
        var options = ReadOptions();
        var baseObstacles = _scene.CollisionObstacles.ToList();
        var snapshot = rows.Take(max).ToList();
        var engine = ResolveEngine(); // captured on UI thread; checkbox state isn't safe to read from Task.Run

        await RunBusyAsync($"{scope} 라우팅 중...", async () =>
        {
            ClearVisuals(_routeVisuals);
            ClearVisuals(_selectedEndpointVisuals);
            ClearVisuals(_selectedStepVisuals);
            ClearVisuals(_featureVisuals);
            _resultRows.Clear();

            // Heavy A* routing runs off the UI thread; only the result binding/drawing is marshalled back.
            var totalSw = Stopwatch.StartNew();
            var computed = await Task.Run(() => ComputeRoutes(snapshot, options, baseObstacles, engine));
            totalSw.Stop();
            _resultRows.AddRange(computed);

            GridResults.ItemsSource = null;
            GridResults.ItemsSource = _resultRows;
            if (_resultRows.Count > 0) GridResults.SelectedIndex = 0;
            RedrawAutoRoutes(GridResults.SelectedItem as ResultRow);
            var truncated = rows.Count > max ? $" | ⚠ 상한 {max}개 적용, {rows.Count - max}개 생략" : string.Empty;
            var engineLabel = engine is NativeRubberBandEngine ? "네이티브(C++)" : "관리형(C#)";
            var logLabel = engine is ManagedRubberBandEngine ? " (디버그 로그가 Docs/RubberBandRouting_DebugTrace.log에 기록됨)" : "";
            TxtStatus.Text = $"자동설계 완료: {max}/{rows.Count}건, {totalSw.Elapsed.TotalMilliseconds:N0} ms 소요. [{engineLabel}]{truncated}{logLabel}";
            UpdateVisibleObjectText();
        });
    }

    private IRubberBandEngine ResolveEngine()
    {
        if (ChkUseNativeEngine.IsChecked == true && NativeRubberBandEngine.IsAvailable) return new NativeRubberBandEngine();
        return new ManagedRubberBandEngine();
    }

    // Auto-routes from earlier tasks accumulate as obstacles for later ones (§6), but a pipe must
    // not be forced to jog sideways around its own bundle-mate — a task sharing the same
    // Group+Utility is expected to run parallel in the same tray/rack, not dodge it. Tagging each
    // route obstacle with its owning task's Group+Utility lets later tasks exclude only their own
    // siblings while still avoiding every other utility's pipes and the base scene obstacles.
    private sealed record RouteObstacleEntry(Aabb Box, string Group, string Utility);

    private List<ResultRow> ComputeRoutes(IReadOnlyList<TaskRow> rows, RubberBandOptions options, List<Aabb> accumulatedObstacles, IRubberBandEngine engine)
    {
        var results = new List<ResultRow>();
        var routeObstacles = new List<RouteObstacleEntry>();
        for (var i = 0; i < rows.Count; i++)
        {
            var row = rows[i];
            var sw = Stopwatch.StartNew();
            var taskGroup = NormalizeGroup(row.Group);
            var taskUtility = NormalizeGroup(row.Utility);
            var obstaclesForTask = accumulatedObstacles
                .Concat(routeObstacles
                    .Where(o => !(string.Equals(o.Group, taskGroup, StringComparison.OrdinalIgnoreCase) && string.Equals(o.Utility, taskUtility, StringComparison.OrdinalIgnoreCase)))
                    .Select(o => o.Box))
                .ToList();
            var displayDiameter = AutoRouteDiameter(row.Task);
            var taskOptions = new RubberBandOptions
            {
                MaxVerticalBends = options.MaxVerticalBends,
                SafetyMargin = options.SafetyMargin,
                TrayWidth = options.TrayWidth,
                TrayHeight = options.TrayHeight,
                PipePitch = options.PipePitch,
                PipeCount = options.PipeCount,
                SnapTolerance = options.SnapTolerance,
                BendRadiusFactor = options.BendRadiusFactor,
                PipeDiameter = displayDiameter
            };

            var featureInfo = BuildFeatureWaypoints(row.Task, taskOptions);
            var result = Route(engine, row.Task, obstaclesForTask, taskOptions, featureInfo.Waypoints);
            sw.Stop();
            // Decide rounded vs sharp against scene + earlier (non-sibling) routes, before this route joins the obstacle set.
            var roundSafe = IsRoundedPathClear(result, displayDiameter, obstaclesForTask, taskOptions);
            if (result.FinalSegments.Count > 0)
            {
                routeObstacles.AddRange(BuildRouteObstacles(result.FinalSegments, taskOptions, $"auto_route_{i + 1}")
                    .Select(b => new RouteObstacleEntry(b, taskGroup, taskUtility)));
            }

            // Cross-check and populate collision points for both native (C++) and managed (C#) engine outputs
            if (result.CollisionPoints.Count == 0 && result.ValidationIssues.Contains("residual_collision"))
            {
                PopulateCollisionPoints(result, obstaclesForTask, taskOptions);
            }

            // Cross-check and populate vertical bend coordinates for both engines
            if (result.VerticalBendPoints.Count == 0 && result.FinalSegments.Count > 0)
            {
                ManagedRubberBandEngine.FindVerticalBends(result.FinalSegments, result.VerticalBendPoints);
            }

            var failure = result.IsValid ? string.Empty : string.Join("; ", result.ValidationIssues);
            var analysis = BuildAnalysisRows(row, result, featureInfo);
            var steps = BuildRouteStepRows(result, featureInfo);
            var segments = result.FinalSegments.Select((s, n) => new SegmentDetailRow(n + 1, FormatVec(s.Start), FormatVec(s.End), s.Length)).ToList();
            var matchedExisting = FindMatchedExistingRoute(row.Task);
            results.Add(new ResultRow(
                i + 1,
                result.IsValid ? "성공" : "확인",
                failure,
                row.Group,
                row.Utility,
                row.SourceName,
                row.TargetName,
                row.Task.Start,
                row.Task.End,
                result.TotalLength,
                result.VerticalBends,
                result.FinalSegments.Count,
                result.FinalSegments.ToList(),
                featureInfo.Waypoints.ToList(),
                AutoRouteDiameter(row.Task),
                AutoRouteBrush(row.Group),
                AutoRouteRowBrush(row.Group),
                analysis,
                steps,
                segments,
                roundSafe,
                result.PipePaths.Select(p => p.ToList()).ToList(),
                sw.Elapsed.TotalMilliseconds,
                row.Task,
                matchedExisting,
                result.CollisionPoints.ToList(),
                result.FallbackLegs.ToList(),
                result.VerticalBendPoints.ToList()));
        }
        return results;
    }

    private static void PopulateCollisionPoints(RubberBandResult result, List<Aabb> obstacles, RubberBandOptions options)
    {
        var radius = options.PipeDiameter / 2.0;
        foreach (var pipe in result.PipePaths)
        {
            for (var i = 0; i < pipe.Count - 1; i++)
            {
                var seg = new RouteSegment(pipe[i], pipe[i + 1]);
                foreach (var obs in obstacles.Where(o => !o.IsPenetration))
                {
                    if (ManagedRubberBandEngine.SegmentIntersectsPipeAabb(seg, obs, radius, options.SafetyMargin))
                    {
                        var cp = ManagedRubberBandEngine.ClosestPointOnSegment(obs.Center, seg.Start, seg.End);
                        if (!result.CollisionPoints.Any(p => (p - cp).Length <= 100.0))
                        {
                            result.CollisionPoints.Add(cp);
                        }
                    }
                }
            }
        }
    }

    private RubberBandResult Route(IRubberBandEngine engine, RouteTask task, IReadOnlyList<Aabb> obstacles, RubberBandOptions options, IReadOnlyList<RouteFeature> featureWaypoints)
    {
        var startStubEnd = RequiredStartDropPoint(task, options, featureWaypoints);
        if (startStubEnd == null) return engine.Route(task.Start, task.End, obstacles, featureWaypoints, options);

        var stubEnd = startStubEnd.Value;
        var filteredFeatures = featureWaypoints
            .Where(f => Distance(f.Position, task.Start) > Math.Max(options.SnapTolerance, 250))
            .ToList();
        var tail = engine.Route(stubEnd, task.End, obstacles, filteredFeatures, options);
        return PrependStartStub(task.Start, stubEnd, tail);
    }

    private static Vec3? RequiredStartDropPoint(RouteTask task, RubberBandOptions options, IReadOnlyList<RouteFeature> featureWaypoints)
    {
        var minDrop = Math.Max(options.TrayHeight, 100);
        if (task.Start.Z <= task.End.Z + minDrop) return null;

        // Prefer the existing-design feature's intermediate elevation (ApplyStartVerticalStub,
        // which reads the legacy route's actual first move) over an unconditional full drop to
        // the final duct elevation — dropping straight to task.End.Z regardless of the legacy
        // design's real trunk/tray height produced oversized initial plunges.
        var featureStubZ = featureWaypoints
            .Where(f => f.Required && f.Role == RouteFeatureRole.StartStub)
            .Where(f => Math.Abs(f.Position.X - task.Start.X) < 1 && Math.Abs(f.Position.Y - task.Start.Y) < 1)
            .Select(f => (double?)f.Position.Z)
            .FirstOrDefault();

        var targetZ = featureStubZ ?? task.End.Z;
        var stubEnd = new Vec3(task.Start.X, task.Start.Y, targetZ);
        return Distance(task.Start, stubEnd) >= 50 ? stubEnd : null;
    }

    private static RubberBandResult PrependStartStub(Vec3 start, Vec3 stubEnd, RubberBandResult tail)
    {
        var stub = new RouteSegment(start, stubEnd);
        var result = new RubberBandResult
        {
            TotalLength = stub.Length + tail.TotalLength,
            VerticalBends = tail.VerticalBends + (tail.FinalSegments.Count > 0 && !tail.FinalSegments[0].IsVertical ? 1 : 0),
            IsValid = tail.IsValid
        };

        var step = new RubberBandStep { StepIndex = 0, Description = "Required start PoC -Z drop" };
        step.Segments.Add(stub);
        step.Waypoints.Add(start);
        step.Waypoints.Add(stubEnd);
        result.Steps.Add(step);
        foreach (var sourceStep in tail.Steps) result.Steps.Add(sourceStep);
        result.FinalSegments.Add(stub);
        result.FinalSegments.AddRange(tail.FinalSegments);
        // Extend each parallel pipe path through the stub too, using the same lateral offset
        // that pipe already has at the tail's first point, so multi-pipe rendering doesn't miss
        // the vertical drop that only the (single) centerline used to show.
        foreach (var pipe in tail.PipePaths)
        {
            var extended = new List<Vec3>(pipe.Count + 1);
            if (pipe.Count > 0) extended.Add(start + (pipe[0] - stubEnd));
            extended.AddRange(pipe);
            result.PipePaths.Add(extended);
        }
        result.ValidationIssues.AddRange(tail.ValidationIssues);
        result.SegmentReasonCodes.Add(SegmentReasons.StartDropStub);
        result.SegmentReasonCodes.AddRange(tail.SegmentReasonCodes);
        return result;
    }

    private void RedrawAutoRoutes(ResultRow? selected)
    {
        ClearVisuals(_routeVisuals);
        ClearVisuals(_featureVisuals);

        var group = SelectedGroup();
        var utility = SelectedUtility();

        foreach (var row in _resultRows)
        {
            // Filter auto-routes to only render those matching the currently selected utility filter.
            if (!string.IsNullOrWhiteSpace(group) && !string.Equals(NormalizeGroup(row.Group), group, StringComparison.OrdinalIgnoreCase))
                continue;
            if (!string.IsNullOrWhiteSpace(utility) && !string.Equals(row.Utility, utility, StringComparison.OrdinalIgnoreCase))
                continue;

            var isSelected = selected != null && ReferenceEquals(row, selected);
            var brush = isSelected ? Brushes.Yellow : row.RouteBrush;
            var diameter = isSelected ? Math.Max(row.RouteDiameter + 70, row.RouteDiameter * 1.55) : row.RouteDiameter;
            if (row.PipePaths.Count > 1)
            {
                // Render every distributed pipe path (tray bundle), not just the representative
                // centerline; RoundSafe is decided against the centerline as an approximation
                // since all pipes share the same bend geometry offset by a fixed pitch.
                foreach (var pipe in row.PipePaths) DrawRoundedPolyline(pipe, brush, diameter, _routeVisuals, row.RoundSafe, row);
            }
            else
            {
                DrawRoundedSegments(row.RouteSegments, brush, diameter, _routeVisuals, row.RoundSafe, row);
            }
            DrawFeaturePoints(row.FeatureWaypoints, isSelected, row);
        }
    }

    private void DrawFeaturePoints(IReadOnlyList<RouteFeature> features, bool isSelected, ResultRow? owningRoute = null)
    {
        var halfSize = isSelected ? 92.0 : 69.0;
        foreach (var feature in features)
        {
            var color = isSelected ? Colors.White : FeatureRoleColor(feature.Role);
            var box = new Aabb(
                new Vec3(feature.Position.X - halfSize, feature.Position.Y - halfSize, feature.Position.Z - halfSize),
                new Vec3(feature.Position.X + halfSize, feature.Position.Y + halfSize, feature.Position.Z + halfSize));
            object? owner = owningRoute != null ? new FeaturePointInfo(feature, owningRoute) : null;
            AddBox(box, Color.FromArgb(128, color.R, color.G, color.B), _featureVisuals, owner);
        }
    }

    private static Color FeatureRoleColor(RouteFeatureRole role) => role switch
    {
        RouteFeatureRole.StartStub => Colors.OrangeRed,
        RouteFeatureRole.Bend => Colors.Magenta,
        RouteFeatureRole.ElevationChange => Colors.Cyan,
        RouteFeatureRole.TrunkGuide => Colors.MediumPurple,
        RouteFeatureRole.EndApproach => Colors.LimeGreen,
        _ => Colors.Magenta
    };

    private static double AutoRouteDiameter(RouteTask task)
    {
        if (task.DiameterMm > 0) return Math.Clamp(task.DiameterMm, 20, 500);
        return 80;
    }

    private static Brush AutoRouteBrush(string? group)
    {
        var color = AutoRouteColor(group);
        var brush = new SolidColorBrush(color);
        brush.Freeze();
        return brush;
    }

    private static Brush AutoRouteRowBrush(string? group)
    {
        var color = AutoRouteColor(group);
        var brush = new SolidColorBrush(Color.FromArgb(110, color.R, color.G, color.B));
        brush.Freeze();
        return brush;
    }

    private static Color AutoRouteColor(string? group)
    {
        var normalized = NormalizeGroup(group);
        unchecked
        {
            var hash = 23;
            foreach (var ch in normalized) hash = hash * 37 + char.ToUpperInvariant(ch);
            return AutoRouteGroupColors[(hash & 0x7FFFFFFF) % AutoRouteGroupColors.Length];
        }
    }

    private static IEnumerable<Aabb> BuildRouteObstacles(IEnumerable<RouteSegment> segments, RubberBandOptions options, string routeName)
    {
        var halfWidth = Math.Max(options.TrayWidth * 0.5, options.PipePitch * Math.Max(1, options.PipeCount - 1) * 0.5) + options.SafetyMargin;
        var halfHeight = Math.Max(options.TrayHeight * 0.5, 25) + options.SafetyMargin;
        var index = 0;
        foreach (var segment in segments)
        {
            if (segment.Length < 1) continue;
            var min = new Vec3(
                Math.Min(segment.Start.X, segment.End.X) - halfWidth,
                Math.Min(segment.Start.Y, segment.End.Y) - halfWidth,
                Math.Min(segment.Start.Z, segment.End.Z) - halfHeight);
            var max = new Vec3(
                Math.Max(segment.Start.X, segment.End.X) + halfWidth,
                Math.Max(segment.Start.Y, segment.End.Y) + halfWidth,
                Math.Max(segment.Start.Z, segment.End.Z) + halfHeight);
            yield return new Aabb(min, max, false, $"{routeName}_{++index}");
        }
    }

    private void DrawScene(RoutingScene scene)
    {
        AddWireBox(scene.Project.Bounds, Brushes.LimeGreen, 28, _areaVisuals);
        foreach (var item in scene.Obstacles) AddBox(item.Bounds, item.IsPassThrough ? Color.FromArgb(30, 148, 163, 184) : Color.FromArgb(72, 156, 163, 175), _obstacleVisuals);
        foreach (var item in scene.Equipment) AddBox(item.Bounds, Color.FromArgb(77, 245, 158, 11), _equipmentVisuals);
        foreach (var item in scene.DuctLaterals)
        {
            var color = string.Equals(item.Category, "LATERAL", StringComparison.OrdinalIgnoreCase)
                ? Color.FromArgb(110, 45, 212, 191)
                : Color.FromArgb(110, 34, 197, 94);
            AddBox(item.Bounds, color, _ductVisuals);
        }
        foreach (var poc in scene.EquipmentPocs.Where(p => p.IsRouteStart)) AddSphere(poc.Position, PocMarkerRadius(poc), Brushes.Red, _pocVisuals);
        foreach (var poc in scene.DuctLateralPocs.Where(p => p.IsRouteEnd)) AddSphere(poc.Position, PocMarkerRadius(poc), Brushes.DodgerBlue, _pocVisuals);
        ApplyLayerVisibility();
    }


    private static double PocMarkerRadius(PocPoint poc)
    {
        if (poc.SizeMm > 0) return Math.Clamp(poc.SizeMm * 0.5, 60, 260);
        return 140;
    }
    private void GridResults_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        var row = GridResults.SelectedItem as ResultRow;
        SetDetailGrids(row);
        ClearVisuals(_selectedStepVisuals);
        HighlightResultEndpoints(row);
        RedrawAutoRoutes(row);
    }

    private void HighlightResultEndpoints(ResultRow? row)
    {
        ClearVisuals(_selectedEndpointVisuals);
        if (row == null) return;

        var radius = Math.Max(row.RouteDiameter * 0.75, 180);
        AddSphere(row.StartPoint, radius, Brushes.Red, _selectedEndpointVisuals);
        AddSphere(row.EndPoint, radius, Brushes.DodgerBlue, _selectedEndpointVisuals);
        DrawPath(new[] { row.StartPoint, row.EndPoint }, Brushes.Gray, Math.Max(row.RouteDiameter * 0.18, 28), _selectedEndpointVisuals);

        // Highlight collision points in 3D using semi-transparent red spheres
        if (row.CollisionPoints != null && row.CollisionPoints.Count > 0)
        {
            var collBrush = new SolidColorBrush(Color.FromArgb(160, 255, 0, 0)); // semi-transparent red
            var collRadius = Math.Max(row.RouteDiameter * 1.5, 300); // larger than normal endpoints
            foreach (var pt in row.CollisionPoints)
            {
                AddSphere(pt, collRadius, collBrush, _selectedEndpointVisuals);
            }
        }

        // Highlight A* search fallback legs in 3D using semi-transparent thick orange segments & spheres
        if (row.FallbackLegs != null && row.FallbackLegs.Count > 0)
        {
            var fallbackBrush = new SolidColorBrush(Color.FromArgb(180, 255, 128, 0)); // semi-transparent orange
            var fallbackDiameter = Math.Max(row.RouteDiameter * 1.4, 200);
            foreach (var leg in row.FallbackLegs)
            {
                DrawRoundedSegments(new[] { leg }, fallbackBrush, fallbackDiameter, _selectedEndpointVisuals, false, null);
                AddSphere(leg.Start, fallbackDiameter * 0.8, fallbackBrush, _selectedEndpointVisuals);
                AddSphere(leg.End, fallbackDiameter * 0.8, fallbackBrush, _selectedEndpointVisuals);
            }
        }

        // Highlight all vertical bend (Z direction change) elbow points in 3D using semi-transparent purple spheres when limit exceeded
        if (row.VerticalBendPoints != null && row.VerticalBendPoints.Count > 0 && row.FailureReason.Contains("vertical_bends_exceeded"))
        {
            var bendBrush = new SolidColorBrush(Color.FromArgb(160, 255, 0, 255)); // semi-transparent magenta
            var bendRadius = Math.Max(row.RouteDiameter * 1.1, 220);
            foreach (var pt in row.VerticalBendPoints)
            {
                AddSphere(pt, bendRadius, bendBrush, _selectedEndpointVisuals);
            }
        }
    }

    private void GridStepDetails_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        var route = GridResults.SelectedItem as ResultRow;
        var step = GridStepDetails.SelectedItem as StepDetailRow;
        HighlightStepSegment(route, step);
    }

    private void HighlightStepSegment(ResultRow? route, StepDetailRow? step)
    {
        ClearVisuals(_selectedStepVisuals);
        if (route == null || step == null) return;

        var index = step.Index - 1;
        if (index < 0 || index >= route.RouteSegments.Count) return;

        var segment = route.RouteSegments[index];
        var diameter = Math.Max(route.RouteDiameter + 130, route.RouteDiameter * 2.1);
        DrawRoundedSegments(new[] { segment }, Brushes.DeepSkyBlue, diameter, _selectedStepVisuals);
    }
    private void SetSceneAnalysis(RoutingScene scene)
    {
        var rows = new List<AnalysisRow>
        {
            new AnalysisRow("프로젝트", scene.Project.DisplayName),
            new AnalysisRow("장애물", scene.Obstacles.Count.ToString("N0", CultureInfo.InvariantCulture)),
            new AnalysisRow("장비", scene.Equipment.Count.ToString("N0", CultureInfo.InvariantCulture)),
            new AnalysisRow("덕트/레터럴", scene.DuctLaterals.Count.ToString("N0", CultureInfo.InvariantCulture)),
            new AnalysisRow("종단 PoC", (scene.EquipmentPocs.Count(p => p.IsRouteStart) + scene.DuctLateralPocs.Count(p => p.IsRouteEnd)).ToString("N0", CultureInfo.InvariantCulture)),
            new AnalysisRow("라우팅 태스크", scene.Tasks.Count.ToString("N0", CultureInfo.InvariantCulture)),
            new AnalysisRow("기존경로", scene.ExistingRoutePaths.Count.ToString("N0", CultureInfo.InvariantCulture))
        };
        foreach (var warning in scene.LoadWarnings) rows.Add(new AnalysisRow("⚠ 로드 경고", warning));
        GridAnalysis.ItemsSource = rows;
    }

    private void SetDetailGrids(ResultRow? row)
    {
        GridAnalysis.ItemsSource = row == null ? new List<AnalysisRow>() : row.AnalysisRows;
        GridStepDetails.ItemsSource = row == null ? new List<StepDetailRow>() : row.StepRows;
        GridSegmentDetails.ItemsSource = row == null ? new List<SegmentDetailRow>() : row.SegmentRows;
        GridErrorList.ItemsSource = row == null ? new List<ErrorDetailRow>() : BuildErrorRows(row);
    }

    private static List<ErrorDetailRow> BuildErrorRows(ResultRow row)
    {
        var rows = new List<ErrorDetailRow>();
        var index = 1;

        var hasCollision = row.FailureReason.Contains("residual_collision");
        var hasFallback = row.FailureReason.Contains("astar_fallback_used");
        var hasBendExceeded = row.FailureReason.Contains("vertical_bends_exceeded");

        if (row.CollisionPoints != null && hasCollision)
        {
            foreach (var pt in row.CollisionPoints)
            {
                rows.Add(new ErrorDetailRow(index++, "장애물 충돌", $"충돌 좌표: ({pt.X:F0}, {pt.Y:F0}, {pt.Z:F0})", pt));
            }
        }

        if (row.FallbackLegs != null && hasFallback)
        {
            foreach (var leg in row.FallbackLegs)
            {
                rows.Add(new ErrorDetailRow(index++, "A* 탐색 실패", $"실패 구간: ({leg.Start.X:F0}, {leg.Start.Y:F0}, {leg.Start.Z:F0}) ➔ ({leg.End.X:F0}, {leg.End.Y:F0}, {leg.End.Z:F0})", leg));
            }
        }

        if (row.VerticalBendPoints != null && hasBendExceeded)
        {
            foreach (var pt in row.VerticalBendPoints)
            {
                rows.Add(new ErrorDetailRow(index++, "수직 꺾임 초과", $"수직 Bend 좌표: ({pt.X:F0}, {pt.Y:F0}, {pt.Z:F0})", pt));
            }
        }

        return rows;
    }

    private void GridErrorList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        var route = GridResults.SelectedItem as ResultRow;
        var error = GridErrorList.SelectedItem as ErrorDetailRow;
        HighlightErrorLocation(route, error);
    }

    private void HighlightErrorLocation(ResultRow? route, ErrorDetailRow? error)
    {
        ClearVisuals(_selectedStepVisuals);
        if (route == null || error == null) return;

        var highlightDiameter = Math.Max(route.RouteDiameter * 1.8, 300);
        var highlightBrush = Brushes.Yellow;

        if (error.GeometryData is Vec3 pt)
        {
            AddSphere(pt, highlightDiameter * 0.7, highlightBrush, _selectedStepVisuals);
            LookAtPoint(pt);
        }
        else if (error.GeometryData is RouteSegment seg)
        {
            DrawRoundedSegments(new[] { seg }, highlightBrush, highlightDiameter, _selectedStepVisuals, false, null);
            AddSphere(seg.Start, highlightDiameter * 0.5, highlightBrush, _selectedStepVisuals);
            AddSphere(seg.End, highlightDiameter * 0.5, highlightBrush, _selectedStepVisuals);
            
            var mid = seg.Start + (seg.End - seg.Start) * 0.5;
            LookAtPoint(mid);
        }
    }

    private void LookAtPoint(Vec3 pt)
    {
        if (Viewport != null && Viewport.Camera is ProjectionCamera pc)
        {
            var dir = pc.LookDirection;
            dir.Normalize();
            var distance = 1500.0; // typical zoom distance
            pc.Position = new Point3D(pt.X - dir.X * distance, pt.Y - dir.Y * distance, pt.Z - dir.Z * distance);
            pc.LookDirection = new Vector3D(dir.X * distance, dir.Y * distance, dir.Z * distance);
        }
    }

    private FeatureRouteInfo BuildFeatureWaypoints(RouteTask task, RubberBandOptions options)
    {
        if (_scene == null) return FeatureRouteInfo.Empty;
        var path = FindMatchedExistingRoute(task);
        if (path == null) return FeatureRouteInfo.Empty;

        var waypoints = ExtractExistingRouteFeatures(path, task, options);
        ApplyStartVerticalStub(task, path, waypoints, options);
        var mode = !string.IsNullOrWhiteSpace(task.RoutePathGuid) && string.Equals(task.RoutePathGuid, path.RoutePathGuid, StringComparison.OrdinalIgnoreCase)
            ? "GUID 직접매칭"
            : "조건 fallback";
        return new FeatureRouteInfo(mode, path.RoutePathGuid, waypoints);
    }

    private ExistingRoutePath? FindMatchedExistingRoute(RouteTask task)
    {
        if (_scene == null) return null;
        if (!string.IsNullOrWhiteSpace(task.RoutePathGuid))
        {
            var direct = _scene.ExistingRoutePaths.FirstOrDefault(x => string.Equals(x.RoutePathGuid, task.RoutePathGuid, StringComparison.OrdinalIgnoreCase));
            if (direct != null) return direct;
        }

        // 1. Strict match: must match both group and utility exactly
        var exactMatch = _scene.ExistingRoutePaths
            .Where(x => string.Equals(NormalizeGroup(x.Group), NormalizeGroup(task.Group), StringComparison.OrdinalIgnoreCase))
            .Where(x => string.Equals(NormalizeGroup(x.Utility), NormalizeGroup(task.Utility), StringComparison.OrdinalIgnoreCase))
            .Where(x => x.Points.Count >= 2)
            .Select(x => new { Path = x, Score = ExistingRouteMatchScore(task, x, exactMetaMatch: true) })
            .OrderBy(x => x.Score)
            .FirstOrDefault(x => x.Score < 8000);

        if (exactMatch != null) return exactMatch.Path;

        // 2. Loose match fallback: relax group/utility checks but penalize mismatches
        return _scene.ExistingRoutePaths
            .Where(x => x.Points.Count >= 2)
            .Select(x => new { Path = x, Score = ExistingRouteMatchScore(task, x, exactMetaMatch: false) })
            .OrderBy(x => x.Score)
            .FirstOrDefault(x => x.Score < 10000)?.Path;
    }

    private static double ExistingRouteMatchScore(RouteTask task, ExistingRoutePath path, bool exactMetaMatch)
    {
        if (path.Points.Count < 2) return double.MaxValue;

        // Base distance gap between endpoints (minimum of forward or reverse)
        var forward = Distance(task.Start, path.Points[0]) + Distance(task.End, path.Points[^1]);
        var reverse = Distance(task.Start, path.Points[^1]) + Distance(task.End, path.Points[0]);
        var score = Math.Min(forward, reverse);

        // Z-level (Elevation) Gap: penalize height difference (0.5 weight per mm)
        var zGap = Math.Abs(task.Start.Z - path.Points[0].Z) + Math.Abs(task.End.Z - path.Points[^1].Z);
        score += zGap * 0.5;

        // Source/Target name match bonus
        if (!string.IsNullOrWhiteSpace(task.SourceName) && !string.IsNullOrWhiteSpace(path.SourceName) && 
            string.Equals(task.SourceName, path.SourceName, StringComparison.OrdinalIgnoreCase)) score -= 1000;
        if (!string.IsNullOrWhiteSpace(task.TargetName) && !string.IsNullOrWhiteSpace(path.TargetName) && 
            string.Equals(task.TargetName, path.TargetName, StringComparison.OrdinalIgnoreCase)) score -= 1000;

        // Pipe Size (Diameter) match/difference penalty
        if (task.DiameterMm > 0 && path.DiameterMm > 0)
        {
            var sizeDiff = Math.Abs(task.DiameterMm - path.DiameterMm);
            if (sizeDiff < 1e-3)
            {
                score -= 300; // Bonus for exact diameter match
            }
            else
            {
                score += sizeDiff * 5.0; // Penalty for size difference
            }
        }

        // If doing loose match fallback, penalize group/utility mismatches
        if (!exactMetaMatch)
        {
            var taskGroup = NormalizeGroup(task.Group);
            var pathGroup = NormalizeGroup(path.Group);
            if (!string.Equals(taskGroup, pathGroup, StringComparison.OrdinalIgnoreCase))
            {
                score += 3000; // Heavy penalty for group mismatch
            }

            var taskUtil = NormalizeGroup(task.Utility);
            var pathUtil = NormalizeGroup(path.Utility);
            if (!string.Equals(taskUtil, pathUtil, StringComparison.OrdinalIgnoreCase))
            {
                score += 2000; // Medium penalty for utility mismatch
            }
        }

        return score;
    }

    private static void ApplyStartVerticalStub(RouteTask task, ExistingRoutePath path, List<RouteFeature> waypoints, RubberBandOptions options)
    {
        if (Math.Abs(task.Start.Z - task.End.Z) < Math.Max(options.TrayHeight, 100)) return;
        if (path.Points.Count < 2) return;

        var oriented = OrientedExistingRoutePoints(path, task);
        if (oriented.Count < 2) return;
        var first = oriented[0];
        var second = oriented[1];

        var legacyDelta = second - first;
        var legacyStartsVertical = DominantAxis(legacyDelta) == 2;
        var targetZ = legacyStartsVertical ? second.Z : task.End.Z;
        if (task.Start.Z > task.End.Z) targetZ = Math.Min(targetZ, task.Start.Z - Math.Max(options.TrayHeight, 100));
        else targetZ = Math.Max(targetZ, task.Start.Z + Math.Max(options.TrayHeight, 100));

        var verticalPoint = new Vec3(task.Start.X, task.Start.Y, targetZ);
        if (Distance(verticalPoint, task.Start) < 50 || Distance(verticalPoint, task.End) < 50) return;
        waypoints.RemoveAll(f => Distance(f.Position, verticalPoint) < Math.Max(options.SnapTolerance, 150));
        // Required=true: this stub reflects the legacy design's forced start drop and must not be
        // dropped by the engine's tolerance/detour filtering the way optional features can be.
        waypoints.Insert(0, new RouteFeature(verticalPoint, RouteFeatureRole.StartStub, Required: true));
    }
    private static List<Vec3> OrientedExistingRoutePoints(ExistingRoutePath path, RouteTask task)
    {
        var points = path.Points.ToList();
        if (points.Count < 2) return points;

        var forward = Distance(task.Start, points[0]) + Distance(task.End, points[^1]);
        var reverse = Distance(task.Start, points[^1]) + Distance(task.End, points[0]);
        if (reverse < forward) points.Reverse();
        return points;
    }
    private static List<RouteFeature> ExtractExistingRouteFeatures(ExistingRoutePath path, RouteTask task, RubberBandOptions options)
    {
        var source = OrientedExistingRoutePoints(path, task);
        if (source.Count < 3) return new List<RouteFeature>();

        var candidates = new List<(double Order, Vec3 Point, RouteFeatureRole Role)>();
        void Add(double order, Vec3 point, RouteFeatureRole role) => candidates.Add((order, point, role));

        Add(1, source[1], RouteFeatureRole.StartStub);
        Add(source.Count - 2, source[^2], RouteFeatureRole.EndApproach);

        for (var i = 1; i < source.Count - 1; i++)
        {
            var prev = source[i] - source[i - 1];
            var next = source[i + 1] - source[i];
            if (prev.Length < 1 || next.Length < 1) continue;

            var axisChanged = DominantAxis(prev) != DominantAxis(next);
            var zChanged = Math.Abs(prev.Z) > 10 || Math.Abs(next.Z) > 10;
            if (zChanged) Add(i, source[i], RouteFeatureRole.ElevationChange);
            else if (axisChanged) Add(i, source[i], RouteFeatureRole.Bend);
        }

        for (var i = 0; i < source.Count - 1; i++)
        {
            var a = source[i];
            var b = source[i + 1];
            var length = Distance(a, b);
            if (length < 4000) continue;
            var chunks = Math.Min(3, (int)Math.Floor(length / 4000));
            for (var n = 1; n <= chunks; n++)
            {
                var t = n / (double)(chunks + 1);
                Add(i + t, Lerp(a, b, t), RouteFeatureRole.TrunkGuide);
            }
        }

        var minEndpointDistance = Math.Max(250, options.SnapTolerance);
        var ordered = candidates
            .OrderBy(x => x.Order)
            .Where(x => Distance(x.Point, task.Start) > minEndpointDistance && Distance(x.Point, task.End) > minEndpointDistance)
            .ToList();

        var cleaned = new List<(Vec3 Point, RouteFeatureRole Role)>();
        foreach (var item in ordered)
        {
            if (cleaned.Count == 0 || Distance(cleaned[^1].Point, item.Point) > Math.Max(250, options.SnapTolerance))
                cleaned.Add((item.Point, item.Role));
        }

        const int maxFeatures = 28;
        List<(Vec3 Point, RouteFeatureRole Role)> selected;
        if (cleaned.Count <= maxFeatures)
        {
            selected = cleaned;
        }
        else
        {
            // Trim by role priority (bends/elevation changes over trunk-guide filler points),
            // then restore original path order so the rubber line still walks the route in sequence.
            selected = cleaned
                .Select((item, idx) => (item.Point, item.Role, Idx: idx))
                .OrderByDescending(x => FeatureRoleWeight(x.Role))
                .ThenBy(x => x.Idx)
                .Take(maxFeatures)
                .OrderBy(x => x.Idx)
                .Select(x => (x.Point, x.Role))
                .ToList();
        }

        return selected.Select(x => new RouteFeature(x.Point, x.Role)).ToList();
    }

    private static int FeatureRoleWeight(RouteFeatureRole role) => role switch
    {
        RouteFeatureRole.StartStub => 3,
        RouteFeatureRole.EndApproach => 3,
        RouteFeatureRole.Bend => 2,
        RouteFeatureRole.ElevationChange => 2,
        RouteFeatureRole.TrunkGuide => 1,
        _ => 0
    };

    private static int DominantAxis(Vec3 v)
    {
        var ax = Math.Abs(v.X);
        var ay = Math.Abs(v.Y);
        var az = Math.Abs(v.Z);
        return ax >= ay && ax >= az ? 0 : ay >= az ? 1 : 2;
    }

    private static double Distance(Vec3 a, Vec3 b) => (a - b).Length;
    private static Vec3 Lerp(Vec3 a, Vec3 b, double t) => new(a.X + (b.X - a.X) * t, a.Y + (b.Y - a.Y) * t, a.Z + (b.Z - a.Z) * t);
    private static List<StepDetailRow> BuildRouteStepRows(RubberBandResult result, FeatureRouteInfo featureInfo)
    {
        var rows = new List<StepDetailRow>();
        for (var i = 0; i < result.FinalSegments.Count; i++)
        {
            var segment = result.FinalSegments[i];
            // The reason is authoritative from the engine that produced this route (managed or
            // native) — the viewer no longer infers it post-hoc from proximity heuristics.
            var code = i < result.SegmentReasonCodes.Count ? result.SegmentReasonCodes[i] : SegmentReasons.RubberAlignment;
            rows.Add(new StepDetailRow(
                i + 1,
                i == 0 ? "시작" : "꺾임",
                FormatVec(segment.Start),
                FormatVec(segment.End),
                SegmentDirection(segment),
                segment.Length,
                SegmentReasonLabel(code)));
        }
        return rows;
    }

    private static string SegmentReasonLabel(string code) => code switch
    {
        SegmentReasons.RouteStart => "장비 시작 PoC에서 출발",
        SegmentReasons.StartDropStub => "시작 PoC 수직 드롭 스텁",
        SegmentReasons.FeatureSnap => "기존설계 특징점 스냅으로 꺾임",
        SegmentReasons.CollisionBypass => "장애물 충돌 회피로 우회 꺾임",
        SegmentReasons.DirectionChange => "고무줄 장력 방향 변경점",
        SegmentReasons.ElevationChange => "Z 고도 변경 구간",
        SegmentReasons.RubberAlignment => "고무줄 control polyline 정렬",
        _ => code
    };

    private static string SegmentDirection(RouteSegment segment)
    {
        var d = segment.Delta;
        var axis = DominantAxis(d);
        var sign = axis switch { 0 => Math.Sign(d.X), 1 => Math.Sign(d.Y), _ => Math.Sign(d.Z) };
        var prefix = sign < 0 ? "-" : "+";
        var nonZero = new[] { Math.Abs(d.X), Math.Abs(d.Y), Math.Abs(d.Z) }.Count(v => v > 1e-3);
        if (nonZero > 1) return "Rubber";
        return axis switch { 0 => prefix + "X", 1 => prefix + "Y", _ => prefix + "Z" };
    }
    private static List<AnalysisRow> BuildAnalysisRows(TaskRow row, RubberBandResult result, FeatureRouteInfo featureInfo)
    {
        var rows = new List<AnalysisRow>
        {
            new AnalysisRow("상태", result.IsValid ? "성공" : "확인 필요"),
            new AnalysisRow("유틸리티", row.UtilityLabel),
            new AnalysisRow("시작PoC", row.SourceName),
            new AnalysisRow("종단PoC", row.TargetName),
            new AnalysisRow("기존경로 매칭", featureInfo.MatchMode),
            new AnalysisRow("기존경로 GUID", featureInfo.RoutePathGuid ?? "-"),
            new AnalysisRow("특징점 구성", FeatureRoleSummary(featureInfo.Waypoints)),
            new AnalysisRow("특징점", featureInfo.Waypoints.Count.ToString(CultureInfo.InvariantCulture)),
            new AnalysisRow("총 길이", $"{result.TotalLength:N0} mm"),
            new AnalysisRow("수직 Bend", result.VerticalBends.ToString(CultureInfo.InvariantCulture)),
            new AnalysisRow("세그먼트", result.FinalSegments.Count.ToString(CultureInfo.InvariantCulture)),
            new AnalysisRow("검증", result.ValidationIssues.Count == 0 ? "이상 없음" : string.Join("; ", result.ValidationIssues))
        };

        if (result.CollisionPoints.Count > 0)
        {
            rows.Add(new AnalysisRow("충돌 개수", $"{result.CollisionPoints.Count} 개"));
            for (int i = 0; i < result.CollisionPoints.Count; i++)
            {
                var pt = result.CollisionPoints[i];
                rows.Add(new AnalysisRow($"충돌점 #{i + 1}", $"({pt.X:F0}, {pt.Y:F0}, {pt.Z:F0})"));
            }
        }

        if (result.FallbackLegs.Count > 0)
        {
            rows.Add(new AnalysisRow("A* 실패 구간 수", $"{result.FallbackLegs.Count} 개"));
            for (int i = 0; i < result.FallbackLegs.Count; i++)
            {
                var leg = result.FallbackLegs[i];
                rows.Add(new AnalysisRow($"실패구간 #{i + 1}", $"({leg.Start.X:F0}, {leg.Start.Y:F0}, {leg.Start.Z:F0}) ➔ ({leg.End.X:F0}, {leg.End.Y:F0}, {leg.End.Z:F0})"));
            }
        }

        if (result.VerticalBendPoints.Count > 0)
        {
            rows.Add(new AnalysisRow("수직 Bend 위치 수", $"{result.VerticalBendPoints.Count} 개"));
            for (int i = 0; i < result.VerticalBendPoints.Count; i++)
            {
                var pt = result.VerticalBendPoints[i];
                rows.Add(new AnalysisRow($"수직 Bend #{i + 1}", $"({pt.X:F0}, {pt.Y:F0}, {pt.Z:F0})"));
            }
        }

        return rows;
    }

    private static string FeatureRoleSummary(IReadOnlyList<RouteFeature> features)
    {
        if (features.Count == 0) return "-";
        return string.Join(", ", features
            .GroupBy(f => f.Role)
            .OrderByDescending(g => g.Count())
            .Select(g => $"{FeatureRoleLabel(g.Key)} {g.Count()}"));
    }

    private static string FeatureRoleLabel(RouteFeatureRole role) => role switch
    {
        RouteFeatureRole.StartStub => "시작스텁",
        RouteFeatureRole.Bend => "꺾임",
        RouteFeatureRole.ElevationChange => "고도변경",
        RouteFeatureRole.TrunkGuide => "트렁크",
        RouteFeatureRole.EndApproach => "종단접근",
        _ => "기타"
    };

    private void ScheduleProjectZoomFit() => Dispatcher.BeginInvoke(new Action(FitProjectToViewport), DispatcherPriority.ContextIdle);

    private void FitProjectToViewport()
    {
        if (_scene == null)
        {
            Viewport.ZoomExtents(500);
            return;
        }
        var bounds = _scene.Project.Bounds;
        var center = bounds.Center;
        var sx = Math.Max(1, bounds.Max.X - bounds.Min.X);
        var sy = Math.Max(1, bounds.Max.Y - bounds.Min.Y);
        var sz = Math.Max(1, bounds.Max.Z - bounds.Min.Z);
        var radius = Math.Sqrt(sx * sx + sy * sy + sz * sz) * 0.5;
        if (Viewport.Camera is not PerspectiveCamera camera || radius <= 1)
        {
            Viewport.ZoomExtents(500);
            return;
        }
        var direction = new Vector3D(1.35, -1.55, 0.85);
        direction.Normalize();
        var fovRadians = Math.Max(10, camera.FieldOfView) * Math.PI / 180.0;
        var distance = Math.Max(radius / Math.Tan(fovRadians * 0.5) * 1.35, radius * 2.4);
        var target = ToPoint3D(center);
        var position = target + direction * distance;
        camera.Position = position;
        camera.LookDirection = target - position;
        camera.UpDirection = new Vector3D(0, 0, 1);
        camera.NearPlaneDistance = 10.0;
        camera.FarPlaneDistance = 10000000.0;
        Viewport.InvalidateVisual();
    }

    private PostgresConnectionOptions ReadDbOptions() => new()
    {
        Host = string.IsNullOrWhiteSpace(TxtHost.Text) ? "localhost" : TxtHost.Text.Trim(),
        Port = ReadInt(TxtPort.Text, 5432),
        Username = string.IsNullOrWhiteSpace(TxtUser.Text) ? "postgres" : TxtUser.Text.Trim(),
        Password = TxtPassword.Password,
        Database = string.IsNullOrWhiteSpace(TxtDatabase.Text) ? "DDW_AI_DB" : TxtDatabase.Text.Trim()
    };

    private RubberBandOptions ReadOptions() => new()
    {
        SafetyMargin = TxtSafetyMargin != null ? ReadDouble(TxtSafetyMargin.Text, 50.0) : 50.0,
        TrayWidth = TxtTrayWidth != null ? ReadDouble(TxtTrayWidth.Text, 600.0) : 600.0,
        TrayHeight = TxtTrayHeight != null ? ReadDouble(TxtTrayHeight.Text, 100.0) : 100.0,
        PipePitch = TxtPipePitch != null ? ReadDouble(TxtPipePitch.Text, 100.0) : 100.0,
        PipeCount = Math.Max(1, TxtPipeCount != null ? ReadInt(TxtPipeCount.Text, 1) : 1),
        BendRadiusFactor = TxtBendRadiusFactor != null ? ReadDouble(TxtBendRadiusFactor.Text, 3.0) : 3.0,
        MaxVerticalBends = TxtMaxVerticalBends != null ? ReadInt(TxtMaxVerticalBends.Text, 99) : 99,
        SnapTolerance = TxtSnapTolerance != null ? ReadDouble(TxtSnapTolerance.Text, 100.0) : 100.0,
        EnableDebugLog = ChkEnableDebugLog != null ? ChkEnableDebugLog.IsChecked == true : true
    };

    private async Task ReRouteCurrentAsync()
    {
        if (_lastRoutedTaskRows == null || _lastRoutedTaskRows.Count == 0) return;
        await RouteRowsAsync(_lastRoutedTaskRows, _lastRoutedScope);
    }

    private void TriggerReRoute()
    {
        if (_lastRoutedTaskRows == null || _lastRoutedTaskRows.Count == 0) return;

        if (_settingChangeTimer == null)
        {
            _settingChangeTimer = new DispatcherTimer
            {
                Interval = TimeSpan.FromMilliseconds(400)
            };
            _settingChangeTimer.Tick += async (s, e) =>
            {
                _settingChangeTimer.Stop();
                await ReRouteCurrentAsync();
            };
        }

        _settingChangeTimer.Stop();
        _settingChangeTimer.Start();
    }

    private void SettingTextBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        TriggerReRoute();
    }

    private async void SettingTextBox_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter)
        {
            _settingChangeTimer?.Stop();
            await ReRouteCurrentAsync();
        }
    }

    private async void SettingTextBox_LostFocus(object sender, RoutedEventArgs e)
    {
        _settingChangeTimer?.Stop();
        await ReRouteCurrentAsync();
    }

    private async Task RunBusyAsync(string status, Func<Task> action)
    {
        try
        {
            SetBusy(true, status);
            await action();
        }
        catch (Exception ex)
        {
            TxtStatus.Text = "오류가 발생했습니다.";
            GridAnalysis.ItemsSource = new[] { new AnalysisRow("오류", ex.Message), new AnalysisRow("상세", ex.ToString()) };
        }
        finally
        {
            SetBusy(false, TxtStatus.Text);
        }
    }

    private void SetBusy(bool isBusy, string status)
    {
        TxtStatus.Text = status;
        BtnLoadProjects.IsEnabled = !isBusy;
        BtnLoadScene.IsEnabled = !isBusy;
        BtnRouteSelectedGroup.IsEnabled = !isBusy;
        BtnRouteUtility.IsEnabled = !isBusy;
        BtnShowExistingRoutes.IsEnabled = !isBusy;
    }

    private void LayerToggle_Changed(object sender, RoutedEventArgs e) => ApplyLayerVisibility();

    private async void ChkUseNativeEngine_Changed(object sender, RoutedEventArgs e)
    {
        if (!IsLoaded) return;
        if (ChkUseNativeEngine.IsChecked == true && !NativeRubberBandEngine.IsAvailable)
        {
            ChkUseNativeEngine.IsChecked = false;
            TxtStatus.Text = "RubberBandRouting.Native.dll을 찾을 수 없습니다. cpp/RubberBandRouting.Native/build_msvc.bat로 빌드한 뒤 다시 시도하세요. 관리형(C#) 엔진을 사용합니다.";
            return;
        }
        TxtStatus.Text = ChkUseNativeEngine.IsChecked == true ? "네이티브 C++ 엔진을 사용합니다." : "관리형(C#) 엔진을 사용합니다.";
        await ReRouteCurrentAsync();
    }

    private void ApplyLayerVisibility()
    {
        if (!IsLoaded) return;
        SetLayerVisible(_areaVisuals, TglArea.IsChecked == true);
        SetLayerVisible(_obstacleVisuals, TglObstacles.IsChecked == true);
        SetLayerVisible(_equipmentVisuals, TglEquipment.IsChecked == true);
        SetLayerVisible(_ductVisuals, TglDucts.IsChecked == true);
        SetLayerVisible(_pocVisuals, TglPocs.IsChecked == true);
        SetLayerVisible(_selectedEndpointVisuals, TglPocs.IsChecked == true);
        SetLayerVisible(_routeVisuals, TglRoutes.IsChecked == true);
        SetLayerVisible(_selectedStepVisuals, TglRoutes.IsChecked == true);
        SetLayerVisible(_featureVisuals, TglFeaturePoints.IsChecked == true);
        SetLayerVisible(_existingRouteVisuals, TglExistingRoutes.IsChecked == true);
        UpdateVisibleObjectText();
    }

    private void SetLayerVisible(List<Visual3D> visuals, bool visible)
    {
        foreach (var visual in visuals)
        {
            var exists = Viewport.Children.Contains(visual);
            if (visible && !exists) Viewport.Children.Add(visual);
            if (!visible && exists) Viewport.Children.Remove(visual);
        }
    }

    private void ClearSceneVisuals()
    {
        ClearVisuals(_areaVisuals);
        ClearVisuals(_obstacleVisuals);
        ClearVisuals(_equipmentVisuals);
        ClearVisuals(_ductVisuals);
        ClearVisuals(_pocVisuals);
        ClearVisuals(_selectedEndpointVisuals);
        UpdateVisibleObjectText();
    }

    private void DrawRoundedSegments(IReadOnlyList<RouteSegment>? segments, Brush brush, double diameter, List<Visual3D> bucket, bool allowRounding = true, object? owner = null)
    {
        if (segments == null || segments.Count == 0) return;
        DrawRoundedPolyline(RouteSegmentsToPolyline(segments), brush, diameter, bucket, allowRounding, owner);
    }

    private void DrawRoundedPolyline(IReadOnlyList<Vec3>? points, Brush brush, double diameter, List<Visual3D> bucket, bool allowRounding = true, object? owner = null)
    {
        if (points == null || points.Count < 2) return;
        // Fall back to the sharp polyline when corner rounding would clip an obstacle.
        var display = allowRounding ? BuildRoundedBendPolyline(points, BendRadius(diameter, ReadOptions().BendRadiusFactor)) : points;
        DrawPath(display, brush, diameter, bucket, owner);
    }

    // Runs off the UI thread during routing: true if the rounded display path stays clear of obstacles.
    private static bool IsRoundedPathClear(RubberBandResult result, double diameter, IReadOnlyList<Aabb> obstacles, RubberBandOptions options)
    {
        var h = diameter / 2.0 + options.SafetyMargin;
        var v = diameter / 2.0 + options.SafetyMargin;
        var radius = BendRadius(diameter, options.BendRadiusFactor);

        if (result.PipePaths.Count > 0)
        {
            foreach (var pipePoints in result.PipePaths)
            {
                var rounded = BuildRoundedBendPolyline(pipePoints, radius);
                foreach (var obs in obstacles)
                {
                    if (obs.IsPenetration) continue;
                    var expanded = obs.Expand(h, v);
                    for (var i = 0; i < rounded.Count - 1; i++)
                        if (SegmentIntersectsBox(rounded[i], rounded[i + 1], expanded)) return false;
                }
            }
            return true;
        }
        else
        {
            var points = RouteSegmentsToPolyline(result.FinalSegments);
            var rounded = BuildRoundedBendPolyline(points, radius);
            foreach (var obs in obstacles)
            {
                if (obs.IsPenetration) continue;
                var expanded = obs.Expand(h, v);
                for (var i = 0; i < rounded.Count - 1; i++)
                    if (SegmentIntersectsBox(rounded[i], rounded[i + 1], expanded)) return false;
            }
            return true;
        }
    }

    private static bool SegmentIntersectsBox(Vec3 a, Vec3 b, Aabb box)
    {
        var delta = b - a;
        double tMin = 0, tMax = 1;
        for (var axis = 0; axis < 3; axis++)
        {
            var start = a[axis];
            var dir = delta[axis];
            if (Math.Abs(dir) <= 1e-9)
            {
                if (start < box.Min[axis] || start > box.Max[axis]) return false;
                continue;
            }
            var inv = 1.0 / dir;
            var t0 = (box.Min[axis] - start) * inv;
            var t1 = (box.Max[axis] - start) * inv;
            if (t0 > t1) (t0, t1) = (t1, t0);
            tMin = Math.Max(tMin, t0);
            tMax = Math.Min(tMax, t1);
            if (tMin > tMax) return false;
        }
        return true;
    }

    private static List<Vec3> RouteSegmentsToPolyline(IReadOnlyList<RouteSegment> segments)
    {
        var points = new List<Vec3>();
        if (segments.Count == 0) return points;
        points.Add(segments[0].Start);
        foreach (var segment in segments)
        {
            if (points.Count == 0 || Distance(points[^1], segment.End) > 1) points.Add(segment.End);
        }
        return points;
    }

    private static List<Vec3> BuildRoundedBendPolyline(IReadOnlyList<Vec3> points, double requestedRadius)
    {
        if (points.Count < 3) return points.ToList();
        var result = new List<Vec3> { points[0] };
        const int arcSteps = 8;

        for (var i = 1; i < points.Count - 1; i++)
        {
            var prev = points[i - 1];
            var corner = points[i];
            var next = points[i + 1];
            var inVec = corner - prev;
            var outVec = next - corner;
            var inLen = inVec.Length;
            var outLen = outVec.Length;
            if (inLen < 1 || outLen < 1)
            {
                AddDistinct(result, corner);
                continue;
            }

            var inDir = inVec * (1.0 / inLen);
            var outDir = outVec * (1.0 / outLen);
            var dot = Vec3.Dot(inDir, outDir);
            if (Math.Abs(dot) > 0.05)
            {
                AddDistinct(result, corner);
                continue;
            }

            var radius = Math.Min(requestedRadius, Math.Min(inLen, outLen) * 0.45);
            if (radius < 20)
            {
                AddDistinct(result, corner);
                continue;
            }

            var before = corner - inDir * radius;
            var after = corner + outDir * radius;
            var center = before + outDir * radius;
            AddDistinct(result, before);
            for (var step = 1; step < arcSteps; step++)
            {
                var theta = (Math.PI * 0.5) * step / arcSteps;
                var offset = (outDir * (-Math.Cos(theta)) + inDir * Math.Sin(theta)) * radius;
                AddDistinct(result, center + offset);
            }
            AddDistinct(result, after);
        }

        AddDistinct(result, points[^1]);
        return result;
    }

    // R = bendRadiusFactor × outer diameter (standard pipe fabrication practice commonly
    // requires at least 3D for a smooth, non-kinking bend — see RubberBandOptions.BendRadiusFactor).
    // No artificial upper clamp: BuildRoundedBendPolyline already caps the usable radius to the
    // available straight-run length (Math.Min(requestedRadius, runLength * 0.45)) and falls back
    // to a sharp corner below ~20mm, so an intentionally large factor (5D, 7D, ...) is honored
    // wherever there's room for it instead of being silently truncated.
    private static double BendRadius(double diameter, double bendRadiusFactor) => Math.Max(diameter, 0) * Math.Max(bendRadiusFactor, 0);

    private static void AddDistinct(List<Vec3> points, Vec3 point)
    {
        if (points.Count == 0 || Distance(points[^1], point) > 1) points.Add(point);
    }
    private void DrawSegments(IEnumerable<RouteSegment>? segments, Brush brush, double diameter, List<Visual3D> bucket)
    {
        if (segments == null) return;
        foreach (var segment in segments) DrawPath(new[] { segment.Start, segment.End }, brush, diameter, bucket);
    }

    private void DrawPath(IEnumerable<Vec3> points, Brush brush, double diameter, List<Visual3D> bucket, object? owner = null)
    {
        var collection = new Point3DCollection(points.Select(ToPoint3D));
        if (collection.Count < 2) return;
        var tube = new TubeVisual3D { Path = collection, Diameter = diameter, Fill = brush, IsPathClosed = false };
        bucket.Add(tube);
        if (owner != null) _visualOwners[tube] = owner;
        if (ShouldShowBucket(bucket)) Viewport.Children.Add(tube);
        UpdateVisibleObjectText();
    }


    private void AddWireBox(Aabb box, Brush brush, double diameter, List<Visual3D> bucket)
    {
        var min = box.Min;
        var max = box.Max;
        var p000 = new Vec3(min.X, min.Y, min.Z);
        var p100 = new Vec3(max.X, min.Y, min.Z);
        var p010 = new Vec3(min.X, max.Y, min.Z);
        var p110 = new Vec3(max.X, max.Y, min.Z);
        var p001 = new Vec3(min.X, min.Y, max.Z);
        var p101 = new Vec3(max.X, min.Y, max.Z);
        var p011 = new Vec3(min.X, max.Y, max.Z);
        var p111 = new Vec3(max.X, max.Y, max.Z);

        DrawPath(new[] { p000, p100 }, brush, diameter, bucket);
        DrawPath(new[] { p010, p110 }, brush, diameter, bucket);
        DrawPath(new[] { p001, p101 }, brush, diameter, bucket);
        DrawPath(new[] { p011, p111 }, brush, diameter, bucket);
        DrawPath(new[] { p000, p010 }, brush, diameter, bucket);
        DrawPath(new[] { p100, p110 }, brush, diameter, bucket);
        DrawPath(new[] { p001, p011 }, brush, diameter, bucket);
        DrawPath(new[] { p101, p111 }, brush, diameter, bucket);
        DrawPath(new[] { p000, p001 }, brush, diameter, bucket);
        DrawPath(new[] { p100, p101 }, brush, diameter, bucket);
        DrawPath(new[] { p010, p011 }, brush, diameter, bucket);
        DrawPath(new[] { p110, p111 }, brush, diameter, bucket);
    }
    private void AddBox(Aabb box, Color color, List<Visual3D> bucket, object? owner = null)
    {
        var visual = new BoxVisual3D
        {
            Center = ToPoint3D(box.Center),
            Length = box.Max.X - box.Min.X,
            Width = box.Max.Y - box.Min.Y,
            Height = box.Max.Z - box.Min.Z,
            Fill = new SolidColorBrush(color)
        };
        bucket.Add(visual);
        if (owner != null) _visualOwners[visual] = owner;
        if (ShouldShowBucket(bucket)) Viewport.Children.Add(visual);
    }

    private void AddSphere(Vec3 point, double radius, Brush brush, List<Visual3D> bucket)
    {
        var visual = new SphereVisual3D { Center = ToPoint3D(point), Radius = radius, Fill = brush };
        bucket.Add(visual);
        if (ShouldShowBucket(bucket)) Viewport.Children.Add(visual);
    }

    private bool ShouldShowBucket(List<Visual3D> bucket)
    {
        if (ReferenceEquals(bucket, _areaVisuals)) return TglArea.IsChecked == true;
        if (ReferenceEquals(bucket, _obstacleVisuals)) return TglObstacles.IsChecked == true;
        if (ReferenceEquals(bucket, _equipmentVisuals)) return TglEquipment.IsChecked == true;
        if (ReferenceEquals(bucket, _ductVisuals)) return TglDucts.IsChecked == true;
        if (ReferenceEquals(bucket, _pocVisuals)) return TglPocs.IsChecked == true;
        if (ReferenceEquals(bucket, _selectedEndpointVisuals)) return TglPocs.IsChecked == true;
        if (ReferenceEquals(bucket, _routeVisuals)) return TglRoutes.IsChecked == true;
        if (ReferenceEquals(bucket, _selectedStepVisuals)) return TglRoutes.IsChecked == true;
        if (ReferenceEquals(bucket, _featureVisuals)) return TglFeaturePoints.IsChecked == true;
        if (ReferenceEquals(bucket, _existingRouteVisuals)) return TglExistingRoutes.IsChecked == true;
        return true;
    }

    private void ClearVisuals(List<Visual3D> visuals)
    {
        foreach (var visual in visuals)
        {
            Viewport.Children.Remove(visual);
            _visualOwners.Remove(visual);
        }
        visuals.Clear();
        UpdateVisibleObjectText();
    }

    private void CompositionTarget_Rendering(object? sender, EventArgs e)
    {
        _frameCount++;
        if (_fpsWatch.Elapsed.TotalSeconds < 0.5) return;
        var fps = _frameCount / _fpsWatch.Elapsed.TotalSeconds;
        TxtFps.Text = $"FPS {fps,5:0.0}";
        _frameCount = 0;
        _fpsWatch.Restart();
        UpdateVisibleObjectText();
    }

    private void UpdateVisibleObjectText()
    {
        if (!IsLoaded) return;
        var dynamicCount = Viewport.Children.OfType<Visual3D>().Count() - 2;
        TxtVisibleObjects.Text = $"렌더 {Math.Max(0, dynamicCount)} obj";
    }

    private string SelectedGroup() => CmbUtilityGroups.SelectedItem?.ToString() ?? string.Empty;
    private string SelectedUtility() => ListUtilities.SelectedItem?.ToString() ?? string.Empty;
    private static string NormalizeGroup(string? group) => string.IsNullOrWhiteSpace(group) ? "?" : group.Trim();
    private static bool GroupMatches(TaskRow row, string group) => string.IsNullOrWhiteSpace(group) || string.Equals(row.Group, group, StringComparison.OrdinalIgnoreCase);
    private static string FormatVec(Vec3 p) => $"({p.X:N0},{p.Y:N0},{p.Z:N0})";
    private static int ReadInt(string text, int fallback) => int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out var v) ? v : fallback;
    private static double ReadDouble(string text, double fallback) => double.TryParse(text, NumberStyles.Any, CultureInfo.InvariantCulture, out var v) ? v : fallback;
    private static Point3D ToPoint3D(Vec3 p) => new(p.X, p.Y, p.Z);

    private sealed record TaskRow(int Index, RouteTask Task)
    {
        public string Group => string.IsNullOrWhiteSpace(Task.Group) ? "?" : Task.Group!;
        public string Utility => string.IsNullOrWhiteSpace(Task.Utility) ? "?" : Task.Utility!;
        public string SourceName => Task.SourceName ?? "Start";
        public string TargetName => Task.TargetName ?? "End";
        public string UtilityLabel => Task.UtilityLabel;
    }

    private sealed record FeatureRouteInfo(string MatchMode, string? RoutePathGuid, List<RouteFeature> Waypoints)
    {
        public static FeatureRouteInfo Empty { get; } = new("없음", null, new List<RouteFeature>());
    }

    private sealed record ResultRow(
        int Index,
        string Status,
        string FailureReason,
        string Group,
        string Utility,
        string StartPoC,
        string EndPoC,
        Vec3 StartPoint,
        Vec3 EndPoint,
        double LengthMm,
        int VerticalBends,
        int SegmentCount,
        List<RouteSegment> RouteSegments,
        List<RouteFeature> FeatureWaypoints,
        double RouteDiameter,
        Brush RouteBrush,
        Brush RouteRowBrush,
        List<AnalysisRow> AnalysisRows,
        List<StepDetailRow> StepRows,
        List<SegmentDetailRow> SegmentRows,
        bool RoundSafe,
        List<List<Vec3>> PipePaths,
        double ElapsedMs,
        RouteTask Task,
        ExistingRoutePath? MatchedExistingRoute,
        List<Vec3> CollisionPoints,
        List<RouteSegment> FallbackLegs,
        List<Vec3> VerticalBendPoints)
    {
        public string LengthText => $"{LengthMm:N0}";
        public string ElapsedText => $"{ElapsedMs:N0}";
    }

    private sealed record AnalysisRow(string Name, string Value);
    private sealed record ErrorDetailRow(int Index, string ErrorType, string Description, object GeometryData);
    // Carries which route a clicked feature-point marker belongs to, so a 3D click can show both
    // the feature's own properties and select/highlight its owning route.
    private sealed record FeaturePointInfo(RouteFeature Feature, ResultRow Route);
    private sealed record TaskFeatureInfo(RouteFeature Feature, TaskRow Task);
    private sealed record StepDetailRow(int Index, string SegmentType, string Start, string End, string Direction, double LengthMm, string Reason)
    {
        public string LengthText => $"{LengthMm:N0}";
    }
    private sealed record SegmentDetailRow(int Index, string Start, string End, double LengthMm)
    {
        public string LengthText => $"{LengthMm:N0}";
    }
}




































