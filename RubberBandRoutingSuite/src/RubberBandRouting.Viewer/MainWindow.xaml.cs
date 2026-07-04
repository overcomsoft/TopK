using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
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
    private readonly Stopwatch _fpsWatch = Stopwatch.StartNew();
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

    public MainWindow()
    {
        InitializeComponent();
        Loaded += async (_, _) => await LoadProjectsAsync();
        CompositionTarget.Rendering += CompositionTarget_Rendering;
    }

    private async void BtnLoadProjects_Click(object sender, RoutedEventArgs e) => await LoadProjectsAsync();
    private async void BtnLoadScene_Click(object sender, RoutedEventArgs e) => await LoadSceneAsync();
    private async void BtnRouteAll_Click(object sender, RoutedEventArgs e) => await RouteAllAsync();
    private async void BtnRouteGroup_Click(object sender, RoutedEventArgs e) => await RouteGroupAsync();
    private async void BtnRouteUtility_Click(object sender, RoutedEventArgs e) => await RouteUtilityAsync();
    private async void BtnShowExistingRoutes_Click(object sender, RoutedEventArgs e) => await ShowExistingRoutesAsync();
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

    private async Task LoadProjectsAsync()
    {
        await RunBusyAsync("Loading projects...", async () =>
        {
            var projects = await _loader.ListProjectsAsync(ReadDbOptions());
            CmbProjects.ItemsSource = projects;
            if (projects.Count > 0) CmbProjects.SelectedIndex = 0;
            TxtStatus.Text = $"Loaded {projects.Count} projects.";
            GridAnalysis.ItemsSource = projects.Take(20).Select(p => new AnalysisRow(p.Index.ToString(CultureInfo.InvariantCulture), p.DisplayName)).ToList();
        });

        if (!_loadedInitialScene && CmbProjects.SelectedItem is RoutingProject)
        {
            _loadedInitialScene = true;
            await LoadSceneAsync();
        }
    }

    private async Task LoadSceneAsync()
    {
        if (CmbProjects.SelectedItem is not RoutingProject project)
        {
            TxtStatus.Text = "Select a project first.";
            return;
        }

        await RunBusyAsync("Loading scene from PostgreSQL...", async () =>
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
            TxtStatus.Text = $"Scene loaded: {_scene.Tasks.Count} route tasks, {_scene.ExistingRoutePaths.Count} existing paths.";
            ScheduleProjectZoomFit();
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
    }

    private void GridTasks_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        HighlightTaskEndpoints(GridTasks.SelectedItem as TaskRow);
    }

    private void HighlightTaskEndpoints(TaskRow? row)
    {
        ClearVisuals(_selectedEndpointVisuals);
        if (row == null) return;

        var diameter = row.Task.DiameterMm > 0 ? Math.Clamp(row.Task.DiameterMm, 80, 520) : 180;
        AddSphere(row.Task.Start, Math.Max(diameter * 0.75, 180), Brushes.Red, _selectedEndpointVisuals);
        AddSphere(row.Task.End, Math.Max(diameter * 0.75, 180), Brushes.DodgerBlue, _selectedEndpointVisuals);
        DrawPath(new[] { row.Task.Start, row.Task.End }, Brushes.White, Math.Max(diameter * 0.15, 24), _selectedEndpointVisuals);
    }
    private async Task RouteGroupAsync()
    {
        if (await EnsureSceneAsync())
        {
            var group = SelectedGroup();
            await RouteRowsAsync(_allTaskRows.Where(x => GroupMatches(x, group)).ToList(), $"Group {group}");
        }
    }

    private async Task RouteUtilityAsync()
    {
        if (await EnsureSceneAsync())
        {
            var utility = SelectedUtility();
            var rows = _visibleTaskRows.Where(x => string.IsNullOrWhiteSpace(utility) || string.Equals(x.Utility, utility, StringComparison.OrdinalIgnoreCase)).ToList();
            await RouteRowsAsync(rows, $"Utility {utility}");
        }
    }

    private async Task RouteAllAsync()
    {
        if (!await EnsureSceneAsync()) return;
        var group = SelectedGroup();
        var rows = string.IsNullOrWhiteSpace(group)
            ? _allTaskRows
            : _allTaskRows.Where(x => GroupMatches(x, group)).ToList();
        await RouteRowsAsync(rows, string.IsNullOrWhiteSpace(group) ? "All tasks" : $"Group {group}");
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
        await RunBusyAsync("Drawing existing PostgreSQL route paths...", () =>
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
            TxtStatus.Text = $"Existing route paths drawn: {count}/{_scene.ExistingRoutePaths.Count}.";
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
            DrawPath(paths[i].Points, ExistingRouteBrush(paths[i].Group), diameter, _existingRouteVisuals);
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
        TxtStatus.Text = $"Existing route paths filtered by group '{SelectedGroup()}': {count}/{_scene.ExistingRoutePaths.Count}.";
    }

    private async Task RouteRowsAsync(IReadOnlyList<TaskRow> rows, string scope)
    {
        if (_scene == null) return;
        if (rows.Count == 0)
        {
            TxtStatus.Text = "선택된 라우팅 작업이 없습니다.";
            return;
        }

        await RunBusyAsync($"Routing {scope}...", () =>
        {
            ClearVisuals(_routeVisuals);
            ClearVisuals(_selectedEndpointVisuals);
            ClearVisuals(_selectedStepVisuals);
            ClearVisuals(_featureVisuals);
            _resultRows.Clear();
            var max = Math.Min(rows.Count, 200);
            var options = ReadOptions();
            var accumulatedObstacles = _scene.CollisionObstacles.ToList();

            for (var i = 0; i < max; i++)
            {
                var row = rows[i];
                var featureInfo = BuildFeatureWaypoints(row.Task, options);
                var result = Route(row.Task, accumulatedObstacles, options, featureInfo.Waypoints);
                if (result.FinalSegments.Count > 0)
                {
                    accumulatedObstacles.AddRange(BuildRouteObstacles(result.FinalSegments, options, $"auto_route_{i + 1}"));
                }
                var failure = result.IsValid ? string.Empty : string.Join("; ", result.ValidationIssues);
                var analysis = BuildAnalysisRows(row, result, featureInfo);
                var steps = BuildRouteStepRows(result, featureInfo);
                var segments = result.FinalSegments.Select((s, n) => new SegmentDetailRow(n + 1, FormatVec(s.Start), FormatVec(s.End), s.Length)).ToList();
                _resultRows.Add(new ResultRow(
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
                    segments));
            }

            GridResults.ItemsSource = null;
            GridResults.ItemsSource = _resultRows;
            if (_resultRows.Count > 0) GridResults.SelectedIndex = 0;
            RedrawAutoRoutes(GridResults.SelectedItem as ResultRow);
            TxtStatus.Text = $"Auto routing completed: {max}/{rows.Count} tasks.";
            UpdateVisibleObjectText();
            return Task.CompletedTask;
        });
    }

    private RubberBandResult Route(RouteTask task, IReadOnlyList<Aabb> obstacles, RubberBandOptions options, IReadOnlyList<Vec3> featureWaypoints)
    {
        var engine = new ManagedRubberBandEngine();
        var startStubEnd = RequiredStartDropPoint(task, options);
        if (startStubEnd == null) return engine.Route(task.Start, task.End, obstacles, featureWaypoints, options);

        var stubEnd = startStubEnd.Value;
        var filteredFeatures = featureWaypoints
            .Where(p => Distance(p, task.Start) > Math.Max(options.SnapTolerance, 250))
            .ToList();
        var tail = engine.Route(stubEnd, task.End, obstacles, filteredFeatures, options);
        return PrependStartStub(task.Start, stubEnd, tail);
    }
    private static Vec3? RequiredStartDropPoint(RouteTask task, RubberBandOptions options)
    {
        var minDrop = Math.Max(options.TrayHeight, 100);
        if (task.Start.Z <= task.End.Z + minDrop) return null;

        var stubEnd = new Vec3(task.Start.X, task.Start.Y, task.End.Z);
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
        result.PipePaths.AddRange(tail.PipePaths);
        result.ValidationIssues.AddRange(tail.ValidationIssues);
        return result;
    }

    private void RedrawAutoRoutes(ResultRow? selected)
    {
        ClearVisuals(_routeVisuals);
        foreach (var row in _resultRows)
        {
            var isSelected = selected != null && ReferenceEquals(row, selected);
            var brush = isSelected ? Brushes.Yellow : row.RouteBrush;
            var diameter = isSelected ? Math.Max(row.RouteDiameter + 70, row.RouteDiameter * 1.55) : row.RouteDiameter;
            DrawRoundedSegments(row.RouteSegments, brush, diameter, _routeVisuals);
            DrawFeaturePoints(row.FeatureWaypoints, isSelected);
        }
    }

    private void DrawFeaturePoints(IReadOnlyList<Vec3> points, bool isSelected)
    {
        var brush = isSelected ? Brushes.White : Brushes.Magenta;
        var radius = isSelected ? 150 : 110;
        foreach (var point in points) AddSphere(point, radius, brush, _featureVisuals);
    }

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
        foreach (var item in scene.Equipment) AddBox(item.Bounds, Color.FromArgb(90, 245, 158, 11), _equipmentVisuals);
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
        GridAnalysis.ItemsSource = new[]
        {
            new AnalysisRow("Project", scene.Project.DisplayName),
            new AnalysisRow("Obstacles", scene.Obstacles.Count.ToString("N0", CultureInfo.InvariantCulture)),
            new AnalysisRow("Equipment", scene.Equipment.Count.ToString("N0", CultureInfo.InvariantCulture)),
            new AnalysisRow("Duct/Lateral", scene.DuctLaterals.Count.ToString("N0", CultureInfo.InvariantCulture)),
            new AnalysisRow("Endpoint PoC", (scene.EquipmentPocs.Count(p => p.IsRouteStart) + scene.DuctLateralPocs.Count(p => p.IsRouteEnd)).ToString("N0", CultureInfo.InvariantCulture)),
            new AnalysisRow("Route tasks", scene.Tasks.Count.ToString("N0", CultureInfo.InvariantCulture)),
            new AnalysisRow("Existing paths", scene.ExistingRoutePaths.Count.ToString("N0", CultureInfo.InvariantCulture))
        };
    }

    private void SetDetailGrids(ResultRow? row)
    {
        GridAnalysis.ItemsSource = row == null ? new List<AnalysisRow>() : row.AnalysisRows;
        GridStepDetails.ItemsSource = row == null ? new List<StepDetailRow>() : row.StepRows;
        GridSegmentDetails.ItemsSource = row == null ? new List<SegmentDetailRow>() : row.SegmentRows;
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

        return _scene.ExistingRoutePaths
            .Where(x => string.IsNullOrWhiteSpace(task.Group) || string.Equals(NormalizeGroup(x.Group), NormalizeGroup(task.Group), StringComparison.OrdinalIgnoreCase))
            .Where(x => string.IsNullOrWhiteSpace(task.Utility) || string.Equals(NormalizeGroup(x.Utility), NormalizeGroup(task.Utility), StringComparison.OrdinalIgnoreCase))
            .Where(x => x.Points.Count >= 2)
            .Select(x => new { Path = x, Score = ExistingRouteMatchScore(task, x) })
            .OrderBy(x => x.Score)
            .FirstOrDefault(x => x.Score < 8000)?.Path;
    }

    private static double ExistingRouteMatchScore(RouteTask task, ExistingRoutePath path)
    {
        if (path.Points.Count < 2) return double.MaxValue;
        var forward = Distance(task.Start, path.Points[0]) + Distance(task.End, path.Points[^1]);
        var reverse = Distance(task.Start, path.Points[^1]) + Distance(task.End, path.Points[0]);
        var score = Math.Min(forward, reverse);
        if (!string.IsNullOrWhiteSpace(task.SourceName) && !string.IsNullOrWhiteSpace(path.SourceName) && string.Equals(task.SourceName, path.SourceName, StringComparison.OrdinalIgnoreCase)) score -= 500;
        if (!string.IsNullOrWhiteSpace(task.TargetName) && !string.IsNullOrWhiteSpace(path.TargetName) && string.Equals(task.TargetName, path.TargetName, StringComparison.OrdinalIgnoreCase)) score -= 500;
        return score;
    }

    private static void ApplyStartVerticalStub(RouteTask task, ExistingRoutePath path, List<Vec3> waypoints, RubberBandOptions options)
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
        waypoints.RemoveAll(p => Distance(p, verticalPoint) < Math.Max(options.SnapTolerance, 150));
        waypoints.Insert(0, verticalPoint);
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
    private static List<Vec3> ExtractExistingRouteFeatures(ExistingRoutePath path, RouteTask task, RubberBandOptions options)
    {
        var source = OrientedExistingRoutePoints(path, task);
        if (source.Count < 3) return new List<Vec3>();

        var candidates = new List<(double Order, Vec3 Point)>();
        void Add(double order, Vec3 point) => candidates.Add((order, point));

        Add(1, source[1]);
        Add(source.Count - 2, source[^2]);

        for (var i = 1; i < source.Count - 1; i++)
        {
            var prev = source[i] - source[i - 1];
            var next = source[i + 1] - source[i];
            if (prev.Length < 1 || next.Length < 1) continue;

            var axisChanged = DominantAxis(prev) != DominantAxis(next);
            var zChanged = Math.Abs(prev.Z) > 10 || Math.Abs(next.Z) > 10;
            if (axisChanged || zChanged) Add(i, source[i]);
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
                Add(i + t, Lerp(a, b, t));
            }
        }

        var minEndpointDistance = Math.Max(250, options.SnapTolerance);
        var ordered = candidates
            .OrderBy(x => x.Order)
            .Select(x => x.Point)
            .Where(p => Distance(p, task.Start) > minEndpointDistance && Distance(p, task.End) > minEndpointDistance)
            .ToList();

        var cleaned = new List<Vec3>();
        foreach (var point in ordered)
        {
            if (cleaned.Count == 0 || Distance(cleaned[^1], point) > Math.Max(250, options.SnapTolerance)) cleaned.Add(point);
        }

        const int maxFeatures = 28;
        if (cleaned.Count <= maxFeatures) return cleaned;
        var sampled = new List<Vec3>();
        for (var i = 0; i < maxFeatures; i++)
        {
            var index = (int)Math.Round(i * (cleaned.Count - 1) / (double)(maxFeatures - 1));
            sampled.Add(cleaned[index]);
        }
        return sampled;
    }

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
        var collisions = result.Steps.SelectMany(s => s.CollisionPoints).ToList();
        var features = featureInfo.Waypoints;
        for (var i = 0; i < result.FinalSegments.Count; i++)
        {
            var segment = result.FinalSegments[i];
            var reason = SegmentReason(i, segment, result.FinalSegments, features, collisions);
            rows.Add(new StepDetailRow(
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

    private static string SegmentReason(int index, RouteSegment segment, IReadOnlyList<RouteSegment> allSegments, IReadOnlyList<Vec3> features, IReadOnlyList<Vec3> collisions)
    {
        if (index == 0) return "장비 시작 PoC에서 출발";
        var joint = segment.Start;
        if (features.Any(p => Distance(p, joint) <= 300)) return "기존설계 특징점 스냅으로 꺾임";
        if (collisions.Any(p => Distance(p, joint) <= 600)) return "장애물 충돌 회피로 우회 꺾임";
        var previous = allSegments[index - 1];
        if (DominantAxis(previous.Delta) != DominantAxis(segment.Delta)) return "고무줄 장력 방향 변경점";
        if (Math.Abs(previous.End.Z - segment.Start.Z) > 10 || Math.Abs(segment.Delta.Z) > 10) return "Z 고도 변경 구간";
        return "고무줄 control polyline 정렬";
    }

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
    private static List<AnalysisRow> BuildAnalysisRows(TaskRow row, RubberBandResult result, FeatureRouteInfo featureInfo) => new()
    {
        new AnalysisRow("상태", result.IsValid ? "성공" : "확인 필요"),
        new AnalysisRow("유틸리티", row.UtilityLabel),
        new AnalysisRow("시작PoC", row.SourceName),
        new AnalysisRow("종단PoC", row.TargetName),
        new AnalysisRow("기존경로 매칭", featureInfo.MatchMode),
        new AnalysisRow("기존경로 GUID", featureInfo.RoutePathGuid ?? "-"),
        new AnalysisRow("특징점", featureInfo.Waypoints.Count.ToString(CultureInfo.InvariantCulture)),
        new AnalysisRow("총 길이", $"{result.TotalLength:N0} mm"),
        new AnalysisRow("수직 Bend", result.VerticalBends.ToString(CultureInfo.InvariantCulture)),
        new AnalysisRow("세그먼트", result.FinalSegments.Count.ToString(CultureInfo.InvariantCulture)),
        new AnalysisRow("검증", result.ValidationIssues.Count == 0 ? "이상 없음" : string.Join("; ", result.ValidationIssues))
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
        camera.NearPlaneDistance = Math.Max(1, distance - radius * 3.0);
        camera.FarPlaneDistance = distance + radius * 5.0;
        Viewport.InvalidateVisual();
    }

    private PostgresConnectionOptions ReadDbOptions() => new()
    {
        Host = string.IsNullOrWhiteSpace(TxtHost.Text) ? "localhost" : TxtHost.Text.Trim(),
        Port = ReadInt(TxtPort.Text, 5432),
        Username = string.IsNullOrWhiteSpace(TxtUser.Text) ? "postgres" : TxtUser.Text.Trim(),
        Password = TxtPassword.Text,
        Database = string.IsNullOrWhiteSpace(TxtDatabase.Text) ? "DDW_AI_DB" : TxtDatabase.Text.Trim()
    };

    private RubberBandOptions ReadOptions() => new()
    {
        MaxVerticalBends = 5,
        SafetyMargin = 50,
        TrayWidth = 600,
        TrayHeight = 100,
        PipePitch = 100,
        PipeCount = 3
    };

    private async Task RunBusyAsync(string status, Func<Task> action)
    {
        try
        {
            SetBusy(true, status);
            await action();
        }
        catch (Exception ex)
        {
            TxtStatus.Text = "Error";
            GridAnalysis.ItemsSource = new[] { new AnalysisRow("Error", ex.Message), new AnalysisRow("Detail", ex.ToString()) };
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
        BtnRouteGroup.IsEnabled = !isBusy;
        BtnRouteSelectedGroup.IsEnabled = !isBusy;
        BtnRouteUtility.IsEnabled = !isBusy;
        BtnRouteAll.IsEnabled = !isBusy;
        BtnShowExistingRoutes.IsEnabled = !isBusy;
    }

    private void LayerToggle_Changed(object sender, RoutedEventArgs e) => ApplyLayerVisibility();

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

    private void DrawRoundedSegments(IReadOnlyList<RouteSegment>? segments, Brush brush, double diameter, List<Visual3D> bucket)
    {
        if (segments == null || segments.Count == 0) return;
        var points = RouteSegmentsToPolyline(segments);
        var rounded = BuildRoundedBendPolyline(points, BendRadius(diameter));
        DrawPath(rounded, brush, diameter, bucket);
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

    private static double BendRadius(double diameter)
    {
        var radius = Math.Max(diameter * 1.5, diameter + 80);
        return Math.Clamp(radius, 120, 1800);
    }

    private static void AddDistinct(List<Vec3> points, Vec3 point)
    {
        if (points.Count == 0 || Distance(points[^1], point) > 1) points.Add(point);
    }
    private void DrawSegments(IEnumerable<RouteSegment>? segments, Brush brush, double diameter, List<Visual3D> bucket)
    {
        if (segments == null) return;
        foreach (var segment in segments) DrawPath(new[] { segment.Start, segment.End }, brush, diameter, bucket);
    }

    private void DrawPath(IEnumerable<Vec3> points, Brush brush, double diameter, List<Visual3D> bucket)
    {
        var collection = new Point3DCollection(points.Select(ToPoint3D));
        if (collection.Count < 2) return;
        var tube = new TubeVisual3D { Path = collection, Diameter = diameter, Fill = brush, IsPathClosed = false };
        bucket.Add(tube);
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
    private void AddBox(Aabb box, Color color, List<Visual3D> bucket)
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
        foreach (var visual in visuals) Viewport.Children.Remove(visual);
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
    private static Point3D ToPoint3D(Vec3 p) => new(p.X, p.Y, p.Z);

    private sealed record TaskRow(int Index, RouteTask Task)
    {
        public string Group => string.IsNullOrWhiteSpace(Task.Group) ? "?" : Task.Group!;
        public string Utility => string.IsNullOrWhiteSpace(Task.Utility) ? "?" : Task.Utility!;
        public string SourceName => Task.SourceName ?? "Start";
        public string TargetName => Task.TargetName ?? "End";
        public string UtilityLabel => Task.UtilityLabel;
    }

    private sealed record FeatureRouteInfo(string MatchMode, string? RoutePathGuid, List<Vec3> Waypoints)
    {
        public static FeatureRouteInfo Empty { get; } = new("없음", null, new List<Vec3>());
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
        List<Vec3> FeatureWaypoints,
        double RouteDiameter,
        Brush RouteBrush,
        Brush RouteRowBrush,
        List<AnalysisRow> AnalysisRows,
        List<StepDetailRow> StepRows,
        List<SegmentDetailRow> SegmentRows)
    {
        public string LengthText => $"{LengthMm:N0}";
    }

    private sealed record AnalysisRow(string Name, string Value);
    private sealed record StepDetailRow(int Index, string SegmentType, string Start, string End, string Direction, double LengthMm, string Reason)
    {
        public string LengthText => $"{LengthMm:N0}";
    }
    private sealed record SegmentDetailRow(int Index, string Start, string End, double LengthMm)
    {
        public string LengthText => $"{LengthMm:N0}";
    }
}




































