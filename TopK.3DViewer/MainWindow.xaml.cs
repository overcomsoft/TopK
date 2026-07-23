using System.Collections.ObjectModel;
using System.Globalization;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Media3D;
using HelixToolkit.Wpf;
using RoutingAI.Standalone;
using TopK.ThreeDViewer.Models;
using TopK.ThreeDViewer.Services;

namespace TopK.ThreeDViewer;

/// <summary>
/// Top-K 조건입력, 검색 API 호출, 실제 경로점 로드, 3D 렌더링을 조정하는 메인 화면.
/// 데이터 계산은 TopKSearchStandalone, 읽기 SQL은 ViewerDatabaseService에 분리한다.
/// </summary>
public partial class MainWindow : Window
{
    private static readonly Color[] RouteColors =
    [
        Colors.DeepSkyBlue, Colors.LimeGreen, Colors.Orange, Colors.Magenta,
        Colors.Gold, Colors.Cyan, Colors.Salmon, Colors.MediumPurple,
        Colors.Chartreuse, Colors.Tomato
    ];

    private readonly ObservableCollection<TopKRouteItem> _routes = [];
    private readonly ObservableCollection<TopKGroupItem> _groupRoutes = [];
    private readonly ObservableCollection<GroupPatternResultItem> _groupPatternResults = [];
    private readonly ObservableCollection<StubPatternResultItem> _stubPatternResults = [];
    private ViewerSettings _settings = new();
    private ViewerDatabaseService? _database;
    private Point3D _queryStart;
    private Point3D _queryEnd;
    private bool _suppressSelectionRefresh;
    private List<Point3D> _presetRoutePoints = [];
    private string _presetRouteGuid = "";
    private UtilityPipeGroupDescriptor? _queryGroup;
    private readonly Dictionary<string, List<Point3D>> _queryGroupPoints =
        new(StringComparer.OrdinalIgnoreCase);
    private readonly HashSet<string> _queryReconstructedRouteGuids =
        new(StringComparer.OrdinalIgnoreCase);
    private GroupPatternDescriptor? _queryGroupPattern;
    private StubPatternDescriptor? _queryStubPattern;
    private List<Point3D> _activePatternScenePoints = [];
    private string _lastWeightProfile = "";
    private FeatureGenerationDialog? _featureGenerationDialog;

    // InitializeComponent가 XAML을 위에서 아래로 생성하는 동안 상단 CheckBox 이벤트가 먼저
    // 발생할 수 있다. 이 시점에는 RdoGroupMode가 아직 null이므로 개별 모드를 기본값으로 본다.
    private bool IsGroupMode => RdoGroupMode?.IsChecked == true;
    private bool IsGroupPatternMode => RdoGroupPatternMode?.IsChecked == true;
    private bool IsStubPatternMode => RdoStubPatternMode?.IsChecked == true;

    public MainWindow()
    {
        InitializeComponent();
        GridResults.ItemsSource = _routes;
        GridGroupResults.ItemsSource = _groupRoutes;
        GridGroupPatternResults.ItemsSource = _groupPatternResults;
        GridStubPatternResults.ItemsSource = _stubPatternResults;
        Loaded += MainWindow_Loaded;
    }

    private void MainWindow_Loaded(object sender, RoutedEventArgs e)
    {
        _settings = ViewerSettings.Load();
        TxtHost.Text = _settings.Host;
        TxtPort.Text = _settings.Port.ToString(CultureInfo.InvariantCulture);
        TxtDatabase.Text = _settings.Database;
        TxtUser.Text = _settings.User;
        TxtPassword.Password = _settings.Password;
        TxtK.Text = _settings.DefaultK.ToString(CultureInfo.InvariantCulture);
        ChkUseContext.IsChecked = _settings.UseObstacleContext;
        TxtWeightPosition.Text = _settings.WeightPosition.ToString("G", CultureInfo.InvariantCulture);
        TxtWeightPattern.Text = _settings.WeightPattern.ToString("G", CultureInfo.InvariantCulture);
        TxtWeightVector.Text = _settings.WeightVector.ToString("G", CultureInfo.InvariantCulture);
        TxtWeightContext.Text = _settings.WeightContext.ToString("G", CultureInfo.InvariantCulture);
        ChkRedistributeMissingPattern.IsChecked = _settings.RedistributeMissingPatternWeight;
        RdoGroupMode.IsChecked = _settings.SearchUnit.Equals("Group", StringComparison.OrdinalIgnoreCase);
        RdoIndividualMode.IsChecked = !RdoGroupMode.IsChecked;
        SelectComboContent(CmbGroupSizeMode, _settings.GroupSizeMatchMode);
        SelectComboContent(CmbGroupViewMode, _settings.GroupComparisonView);
        TxtGroupMatchedWeight.Text = _settings.GroupMatchedWeight.ToString("G6", CultureInfo.InvariantCulture);
        TxtGroupArrangementWeight.Text = _settings.GroupArrangementWeight.ToString("G6", CultureInfo.InvariantCulture);
        ChkShowUnmatchedGroupMembers.IsChecked = _settings.ShowUnmatchedGroupMembers;
        ApplySearchModeUi();
        LegendObstacles.Visibility = ChkShowObstacles.IsChecked == true ? Visibility.Visible : Visibility.Collapsed;
        LegendBendFeature.Visibility = ChkShowBendFeatures.IsChecked == true ? Visibility.Visible : Visibility.Collapsed;
        LegendPathSegment.Visibility = ChkShowPathSegments.IsChecked == true ? Visibility.Visible : Visibility.Collapsed;
        _settings.Save(); // 파일이 없으면 초기 viewer.settings.json을 생성한다.
        TxtStatus.Text = $"DB 연결 및 조건 로드를 실행하세요. 설정: {_settings.SettingsFilePath}";
    }

    private async void Connect_Click(object sender, RoutedEventArgs e)
    {
        await RunBusyAsync("DB 연결 및 검색조건 로드 중...", async () =>
        {
            _settings = ReadSettingsFromUi();
            _settings.Save();
            _database = new ViewerDatabaseService(_settings.ToConnectionString());
            var connection = await _database.TestConnectionAsync();
            var catalogTask = _database.LoadFilterCatalogAsync();
            var presetsTask = _database.LoadPresetsAsync();
            var groupPresetsTask = UtilityPipeGroupSearch.FetchPresetsAsync(_settings.ToDbConfig(), limit: 1000);
            var groupPatternPresetsTask = GroupPatternSearch.FetchPresetsAsync(_settings.ToDbConfig(), limit: 1000);
            var stubPatternPresetsTask = StubPatternSearch.FetchPresetsAsync(_settings.ToDbConfig(), limit: 1000);
            await Task.WhenAll(catalogTask, presetsTask, groupPresetsTask, groupPatternPresetsTask, stubPatternPresetsTask);

            SetCombo(CmbProcess, catalogTask.Result.Processes);
            SetCombo(CmbEquipment, catalogTask.Result.Equipments);
            SetCombo(CmbUtilityGroup, catalogTask.Result.UtilityGroups);
            SetCombo(CmbUtility, catalogTask.Result.Utilities);
            SetCombo(CmbSize, catalogTask.Result.Sizes);
            CmbPreset.ItemsSource = presetsTask.Result;
            CmbGroupPreset.ItemsSource = groupPresetsTask.Result;
            CmbGroupPatternPreset.ItemsSource = groupPatternPresetsTask.Result;
            CmbStubPatternPreset.ItemsSource = stubPatternPresetsTask.Result;
            TxtStatus.Text = $"연결 성공: {connection}, 개별 프리셋 {presetsTask.Result.Count:N0}건, " +
                             $"그룹 프리셋 {groupPresetsTask.Result.Count:N0}건, " +
                             $"다발배관 패턴 {groupPatternPresetsTask.Result.Count:N0}건, " +
                             $"Stub 패턴 {stubPatternPresetsTask.Result.Count:N0}건";
        });
    }

    private void SearchMode_Changed(object sender, RoutedEventArgs e)
    {
        if (!IsLoaded) return;
        ApplySearchModeUi();
        var modeLabel = IsGroupMode ? "Utility 배관 그룹"
            : IsGroupPatternMode ? "다발배관 패턴"
            : IsStubPatternMode ? "Stub 패턴"
            : "개별 배관";
        SaveUiSettings($"{modeLabel} 검색 모드로 변경했습니다.");
    }

    private void ApplySearchModeUi()
    {
        if (GrpRoutePreset is null) return;
        var isPatternMode = IsGroupPatternMode || IsStubPatternMode;
        GrpRoutePreset.Visibility = !IsGroupMode && !isPatternMode ? Visibility.Visible : Visibility.Collapsed;
        GrpGroupPreset.Visibility = IsGroupMode ? Visibility.Visible : Visibility.Collapsed;
        GrpGroupPatternPreset.Visibility = IsGroupPatternMode ? Visibility.Visible : Visibility.Collapsed;
        GrpStubPatternPreset.Visibility = IsStubPatternMode ? Visibility.Visible : Visibility.Collapsed;
        GrpCoordinates.Visibility = !IsGroupMode && !isPatternMode ? Visibility.Visible : Visibility.Collapsed;
        GrpGroupOptions.Visibility = IsGroupMode ? Visibility.Visible : Visibility.Collapsed;
        GrpFilters.Visibility = isPatternMode ? Visibility.Collapsed : Visibility.Visible;
        GrpWeights.Visibility = isPatternMode ? Visibility.Collapsed : Visibility.Visible;
        CmbSize.IsEnabled = !IsGroupMode;
        TxtPattern.IsEnabled = !IsGroupMode;
        LblSize.IsEnabled = !IsGroupMode;
        LblPattern.IsEnabled = !IsGroupMode;
        GridResults.Visibility = !IsGroupMode && !isPatternMode ? Visibility.Visible : Visibility.Collapsed;
        GridGroupResults.Visibility = IsGroupMode ? Visibility.Visible : Visibility.Collapsed;
        GridGroupPatternResults.Visibility = IsGroupPatternMode ? Visibility.Visible : Visibility.Collapsed;
        GridStubPatternResults.Visibility = IsStubPatternMode ? Visibility.Visible : Visibility.Collapsed;
        BtnSearch.Content = IsGroupMode ? "그룹 Top-K 검색 및 3D 비교"
            : IsGroupPatternMode ? "유사 다발배관 패턴 검색"
            : IsStubPatternMode ? "유사 Stub 패턴 검색"
            : "Top-K 검색 및 3D 로드";
        TxtResultSummary.Text = IsGroupMode ? "그룹 검색 전"
            : IsGroupPatternMode ? "다발배관 패턴 검색 전"
            : IsStubPatternMode ? "Stub 패턴 검색 전"
            : "검색 전";
    }

    private async void Preset_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (IsGroupMode) return;
        if (CmbPreset.SelectedItem is not RoutePresetItem preset) return;
        CmbProcess.Text = preset.Process;
        CmbEquipment.Text = preset.Equipment;
        CmbUtilityGroup.Text = preset.UtilityGroup;
        CmbUtility.Text = preset.Utility;
        CmbSize.Text = preset.Size;
        SetPoint(TxtStartX, TxtStartY, TxtStartZ, preset.Start);
        SetPoint(TxtEndX, TxtEndY, TxtEndZ, preset.End);
        _queryStart = preset.Start;
        _queryEnd = preset.End;
        _presetRouteGuid = preset.RoutePathGuid;

        await RunBusyAsync("프리셋 기존 배관과 주변 장애물 로드 중...", async () =>
        {
            _settings = ReadSettingsFromUi();
            _settings.Save();
            _database ??= new ViewerDatabaseService(_settings.ToConnectionString());
            _presetRoutePoints = await _database.LoadRoutePointsAsync(preset.RoutePathGuid);
            if (_presetRoutePoints.Count < 2) _presetRoutePoints = [preset.Start, preset.End];

            _suppressSelectionRefresh = true;
            _routes.Clear();
            GridResults.SelectedIndex = -1;
            _suppressSelectionRefresh = false;
            RouteLayer.Children.Clear();
            PresetRouteLayer.Children.Clear();
            AddPipePath(PresetRouteLayer, _presetRoutePoints, Colors.DeepSkyBlue, 90, 1.0);
            RenderMarkers();
            TxtResultSummary.Text = "프리셋 기존 배관 표시 중 · Top-K 검색 전";
            TxtDetails.Text = $"""
                기존 Route 프리셋
                Route GUID     : {preset.RoutePathGuid}
                Process        : {preset.Process}
                Equipment      : {preset.Equipment}
                Utility / Size : {preset.Utility} / {preset.Size}
                Point Count    : {_presetRoutePoints.Count:N0}
                Geometry       : DB 기존 상세경로
                """;
            UpdateSceneGrid();
            if (ChkShowObstacles.IsChecked == true) await LoadAndRenderObstaclesAsync();
            if (ChkShowBendFeatures.IsChecked == true) await LoadAndRenderBendFeaturesAsync();
            if (ChkShowPathSegments.IsChecked == true) await LoadAndRenderPathSegmentsAsync();
            ZoomToSelectedRoute();
            TxtStatus.Text = $"프리셋 3D 로드 완료: {preset.RoutePathGuid}, {_presetRoutePoints.Count:N0} points";
        });
    }

    private async void GroupPreset_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!IsGroupMode || CmbGroupPreset.SelectedItem is not UtilityPipeGroupPreset preset) return;
        await RunBusyAsync("Utility 배관 Query 그룹 로드 중...", async () =>
        {
            _settings = ReadSettingsFromUi();
            _settings.Save();
            _database ??= new ViewerDatabaseService(_settings.ToConnectionString());
            _queryGroup = await UtilityPipeGroupSearch.LoadGroupAsync(_settings.ToDbConfig(), preset.GroupVectorId)
                          ?? throw new InvalidOperationException($"READY 그룹을 찾을 수 없습니다: {preset.GroupVectorId}");

            CmbProcess.Text = _queryGroup.ProcessName;
            CmbEquipment.Text = _queryGroup.EquipmentInstanceKey;
            CmbUtilityGroup.Text = _queryGroup.UtilityGroup;
            CmbUtility.Text = _queryGroup.Utility;
            GridGroupMembers.ItemsSource = _queryGroup.Members.Select(member =>
                new UtilityPipeGroupMemberRow(member.MemberOrder, member.RoutePathGuid, member.Size,
                    member.DirectionPattern, member.TotalLengthMm)).ToArray();

            var loaded = await _database.LoadRoutePointsBatchAsync(
                _queryGroup.Members.Select(member => member.RoutePathGuid));
            _queryGroupPoints.Clear();
            _queryReconstructedRouteGuids.Clear();
            foreach (var member in _queryGroup.Members)
            {
                if (loaded.TryGetValue(member.RoutePathGuid, out var points) && points.Count >= 2)
                    _queryGroupPoints[member.RoutePathGuid] = points;
                else
                {
                    _queryGroupPoints[member.RoutePathGuid] = BuildMemberFallbackPolyline(member);
                    _queryReconstructedRouteGuids.Add(member.RoutePathGuid);
                }
            }

            _groupRoutes.Clear();
            GridGroupResults.SelectedItem = null;
            RouteLayer.Children.Clear();
            RenderGroupQuery();
            RenderGroupMarkers();
            UpdateSceneGrid();
            TxtActiveScope.Text = $"ACTIVE scope: {_queryGroup.ProjectScopeKey} / {Short(_queryGroup.ModelRevisionKey, 32)}";
            TxtResultSummary.Text = "Query 그룹 표시 중 · Top-K 검색 전";
            TxtDetails.Text = BuildQueryGroupDetails(_queryGroup);
            if (ChkShowObstacles.IsChecked == true) await LoadAndRenderObstaclesAsync();
            if (ChkShowBendFeatures.IsChecked == true) await LoadAndRenderBendFeaturesAsync();
            if (ChkShowPathSegments.IsChecked == true) await LoadAndRenderPathSegmentsAsync();
            ZoomToSelectedRoute();
            TxtStatus.Text = $"Query 그룹 로드 완료: {_queryGroup.MemberCount}개 배관, " +
                             $"실제 3D {_queryGroupPoints.Values.Count(points => points.Count >= 2)}개";
        });
    }

    private async void GroupPatternPreset_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!IsGroupPatternMode || CmbGroupPatternPreset.SelectedItem is not GroupPatternPreset preset) return;
        await RunBusyAsync("다발배관 패턴 Query 로드 중...", async () =>
        {
            _settings = ReadSettingsFromUi();
            _settings.Save();
            _queryGroupPattern = await GroupPatternSearch.LoadAsync(_settings.ToDbConfig(), preset.GroupId)
                ?? throw new InvalidOperationException($"패턴을 찾을 수 없습니다: {preset.GroupId}");

            _groupPatternResults.Clear();
            GridGroupPatternResults.SelectedItem = null;
            RouteLayer.Children.Clear();
            RenderGroupPatternQuery();
            UpdateSceneGrid();
            TxtResultSummary.Text = "Query 패턴 표시 중 · 검색 전";
            TxtDetails.Text = BuildGroupPatternDetails(_queryGroupPattern, null);
            ZoomToSelectedRoute();
            TxtStatus.Text = $"Query 다발배관 패턴 로드 완료: {preset.GroupId}, " +
                             $"트렁크 라인 {_queryGroupPattern.TrunkLines.Count}개";
        });
    }

    private async void StubPatternPreset_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!IsStubPatternMode || CmbStubPatternPreset.SelectedItem is not StubPatternPreset preset) return;
        await RunBusyAsync("Stub 패턴 Query 로드 중...", async () =>
        {
            _settings = ReadSettingsFromUi();
            _settings.Save();
            _queryStubPattern = await StubPatternSearch.LoadAsync(_settings.ToDbConfig(), preset.PatternId)
                ?? throw new InvalidOperationException($"패턴을 찾을 수 없습니다: {preset.PatternId}");

            _stubPatternResults.Clear();
            GridStubPatternResults.SelectedItem = null;
            RouteLayer.Children.Clear();
            RenderStubPatternQuery();
            UpdateSceneGrid();
            TxtResultSummary.Text = "Query Stub 패턴 표시 중 · 검색 전";
            TxtDetails.Text = BuildStubPatternDetails(_queryStubPattern, null);
            ZoomToSelectedRoute();
            TxtStatus.Text = $"Query Stub 패턴 로드 완료: {preset.PatternId}";
        });
    }

    private async void Search_Click(object sender, RoutedEventArgs e)
    {
        await RunBusyAsync("Top-K 검색 및 3D 경로 로드 중...", async () =>
        {
            _settings = ReadSettingsFromUi();
            _settings.Save();
            _database = new ViewerDatabaseService(_settings.ToConnectionString());
            if (IsGroupMode)
            {
                await SearchGroupsAsync();
                return;
            }
            if (IsGroupPatternMode)
            {
                await SearchGroupPatternsAsync();
                return;
            }
            if (IsStubPatternMode)
            {
                await SearchStubPatternsAsync();
                return;
            }
            _queryStart = ReadPoint(TxtStartX, TxtStartY, TxtStartZ, "시작점");
            _queryEnd = ReadPoint(TxtEndX, TxtEndY, TxtEndZ, "종점");
            var k = ParseInt(TxtK.Text, "K", 1, 50);
            var useContext = ChkUseContext.IsChecked == true;
            var rerankWeights = _settings.ToRerankWeights();
            var redistributeMissingPattern = ChkRedistributeMissingPattern.IsChecked == true;

            // Feature table에는 원본 상세경로와 GUID가 끊긴 legacy row가 포함될 수 있다.
            // 요청 K보다 넓은 검색 pool을 받아 실제 상세점이 있는 후보를 우선 선별한다.
            var searchPoolK = Math.Clamp(Math.Max(k * 20, 100), 100, 1000);
            var (results, meta) = await TopKSearchStandalone.SearchAsync(
                db: _settings.ToDbConfig(),
                processName: Value(CmbProcess),
                equipmentName: Value(CmbEquipment),
                utilityGroup: Value(CmbUtilityGroup),
                utility: Value(CmbUtility),
                startXyz: (_queryStart.X, _queryStart.Y, _queryStart.Z),
                endXyz: (_queryEnd.X, _queryEnd.Y, _queryEnd.Z),
                k: searchPoolK,
                size: Value(CmbSize),
                queryPattern: TxtPattern.Text.Trim(),
                useObstacleContext: useContext,
                rerankWeights: rerankWeights,
                redistributeMissingPatternWeight: redistributeMissingPattern);

            var pointsByGuid = await _database.LoadRoutePointsBatchAsync(results.Select(r => r.RoutePathGuid));
            var endpointsByGuid = await _database.LoadRouteEndpointsBatchAsync(results.Select(r => r.RoutePathGuid));
            var loaded = new List<TopKRouteItem>(k);

            // 1차: 원본 DB 상세 polyline이 실제 존재하는 후보를 검색점수 순으로 채운다.
            foreach (var result in results)
            {
                if (!pointsByGuid.TryGetValue(result.RoutePathGuid, out var points) || points.Count < 2) continue;
                endpointsByGuid.TryGetValue(result.RoutePathGuid, out var endpoint);
                loaded.Add(new TopKRouteItem
                {
                    Search = result with { Rank = loaded.Count + 1 },
                    Points = points,
                    GeometrySource = "DB 상세경로",
                    IsExactGeometry = true,
                    TargetOwnerName = endpoint?.TargetOwnerName ?? "",
                    TargetKind = endpoint?.TargetKind ?? ""
                });
                if (loaded.Count == k) break;
            }

            // 2차: 현재 DB와 GUID 연결이 끊긴 legacy vector만 남으면 시작/종점 메타데이터로
            // 직교 fallback을 만들어 사용자가 요청한 K개를 항상 비교할 수 있게 한다.
            foreach (var result in results.Where(r => loaded.All(x => x.Guid != r.RoutePathGuid)))
            {
                if (loaded.Count == k) break;
                endpointsByGuid.TryGetValue(result.RoutePathGuid, out var endpoint);
                loaded.Add(new TopKRouteItem
                {
                    Search = result with { Rank = loaded.Count + 1 },
                    Points = BuildMetadataFallbackPolyline(result),
                    GeometrySource = "Feature 메타데이터 재구성(원본 GUID 미연결)",
                    IsExactGeometry = false,
                    TargetOwnerName = endpoint?.TargetOwnerName ?? "",
                    TargetKind = endpoint?.TargetKind ?? ""
                });
            }

            _presetRoutePoints = [];
            _presetRouteGuid = "";
            PresetRouteLayer.Children.Clear();
            _routes.Clear();
            foreach (var route in loaded) _routes.Add(route);
            _suppressSelectionRefresh = true;
            GridResults.SelectedIndex = _routes.Count > 0 ? 0 : -1;
            _suppressSelectionRefresh = false;
            RenderMarkers();
            RenderRoutes();
            UpdateSelectedRouteDetails();
            UpdateSceneGrid();

            TxtActiveScope.Text = useContext
                ? $"ACTIVE scope: {meta.ContextProjectScopeKey} / {Short(meta.ContextModelRevisionKey, 32)}"
                : "Context: 사용 안 함(Baseline)";
            var exactCount = _routes.Count(r => r.IsExactGeometry);
            _lastWeightProfile = meta.RerankWeightProfile;
            TxtResultSummary.Text =
                $"{_routes.Count:N0}건 · 실제 {exactCount:N0} · 재구성 {_routes.Count - exactCount:N0} · " +
                FriendlyWeightProfile(_lastWeightProfile);
            TxtStatus.Text =
                $"검색 완료: {_routes.Count}건, {meta.SearchTimeMs:F1}ms, " +
                $"상세경로={exactCount}/{_routes.Count}, Context coverage={meta.ContextCoverage:P1}";

            if (ChkShowObstacles.IsChecked == true) await LoadAndRenderObstaclesAsync();
            if (ChkShowBendFeatures.IsChecked == true) await LoadAndRenderBendFeaturesAsync();
            if (ChkShowPathSegments.IsChecked == true) await LoadAndRenderPathSegmentsAsync();
            if (ChkAutoZoom.IsChecked == true) ZoomToSelectedRoute();
        });
    }

    /// <summary>
    /// 선택된 장비 + Utility Group + Utility 전체 배관을 Query로 사용하여 그룹 Top-K를 실행하고,
    /// 각 후보 멤버의 실제 상세 경로를 일괄 조회해 3D Pair 비교 화면을 구성한다.
    /// </summary>
    private async Task SearchGroupsAsync()
    {
        if (_queryGroup is null)
            throw new InvalidOperationException("먼저 Utility 배관 그룹 프리셋을 선택하세요.");
        if (_database is null) throw new InvalidOperationException("DB 연결이 필요합니다.");

        var k = ParseInt(TxtK.Text, "K", 1, 50);
        if (!Enum.TryParse<GroupSizeMatchMode>(ComboContent(CmbGroupSizeMode), true, out var sizeMode))
            sizeMode = GroupSizeMatchMode.PreferExact;
        var options = new UtilityPipeGroupSearchOptions
        {
            K = k,
            SizeMatchMode = sizeMode,
            PairWeights = _settings.ToRerankWeights(),
            MatchedWeight = _settings.GroupMatchedWeight / 100.0,
            ArrangementWeight = _settings.GroupArrangementWeight / 100.0
        };
        var (results, meta) = await UtilityPipeGroupSearch.SearchAsync(
            _settings.ToDbConfig(), _queryGroup.GroupVectorId, options);

        var candidateMembers = results.SelectMany(result => result.Candidate.Members).ToArray();
        var loaded = await _database.LoadRoutePointsBatchAsync(
            candidateMembers.Select(member => member.RoutePathGuid));
        var endpointsByGuid = await _database.LoadRouteEndpointsBatchAsync(
            candidateMembers.Select(member => member.RoutePathGuid));

        _groupRoutes.Clear();
        foreach (var result in results)
        {
            var points = new Dictionary<string, List<Point3D>>(StringComparer.OrdinalIgnoreCase);
            var reconstructed = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var member in result.Candidate.Members)
            {
                if (loaded.TryGetValue(member.RoutePathGuid, out var routePoints) && routePoints.Count >= 2)
                    points[member.RoutePathGuid] = routePoints;
                else
                {
                    points[member.RoutePathGuid] = BuildMemberFallbackPolyline(member);
                    reconstructed.Add(member.RoutePathGuid);
                }
            }
            _groupRoutes.Add(new TopKGroupItem
            {
                Search = result, PointsByRouteGuid = points, ReconstructedRouteGuids = reconstructed,
                EndpointsByRouteGuid = endpointsByGuid
            });
        }

        _routes.Clear();
        _suppressSelectionRefresh = true;
        GridGroupResults.SelectedIndex = _groupRoutes.Count > 0 ? 0 : -1;
        _suppressSelectionRefresh = false;
        RenderGroupComparison();
        UpdateSelectedGroupDetails();
        UpdateSceneGrid();
        TxtActiveScope.Text = $"ACTIVE scope: {meta.ProjectScopeKey} / {Short(meta.ModelRevisionKey, 32)}";
        _lastWeightProfile = meta.PairWeightProfile;
        TxtResultSummary.Text =
            $"{_groupRoutes.Count:N0}개 그룹 · Query {_queryGroup.MemberCount} pipes · " +
            $"Size={meta.SizeMatchMode} · {meta.SearchTimeMs:F1}ms";
        TxtStatus.Text = $"그룹 검색 완료: ANN {meta.AnnCandidateCount:N0}개 → Top-{meta.ReturnedCount:N0}, " +
                         $"Pair W={meta.PairWeightProfile}, Group W={meta.MatchedWeight:P0}/{meta.ArrangementWeight:P0}";
        if (ChkShowObstacles.IsChecked == true) await LoadAndRenderObstaclesAsync();
        if (ChkShowBendFeatures.IsChecked == true) await LoadAndRenderBendFeaturesAsync();
        if (ChkAutoZoom.IsChecked == true) ZoomToSelectedRoute();
    }

    /// <summary>
    /// 선택된 다발배관 패턴을 Query로 형상(FEAT) 유사 검색을 실행하고, 결과 트렁크 라인을 3D에
    /// 겹쳐 그린다. UtilityPipeGroupSearch와 달리 멤버 단위 매칭이 없어 렌더링도 더 단순하다.
    /// </summary>
    private async Task SearchGroupPatternsAsync()
    {
        if (_queryGroupPattern is null)
            throw new InvalidOperationException("먼저 다발배관 패턴 프리셋을 선택하세요.");
        var k = ParseInt(TxtK.Text, "K", 1, 50);
        var (results, meta) = await GroupPatternSearch.SearchAsync(
            _settings.ToDbConfig(), _queryGroupPattern.GroupId, new GroupPatternSearchOptions { K = k });

        _groupPatternResults.Clear();
        foreach (var result in results) _groupPatternResults.Add(new GroupPatternResultItem { Search = result });
        _routes.Clear();
        _groupRoutes.Clear();
        _suppressSelectionRefresh = true;
        GridGroupPatternResults.SelectedIndex = _groupPatternResults.Count > 0 ? 0 : -1;
        _suppressSelectionRefresh = false;
        RenderGroupPatternQuery();
        UpdateSceneGrid();
        TxtResultSummary.Text = $"{_groupPatternResults.Count:N0}건 · {meta.SearchTimeMs:F1}ms";
        TxtStatus.Text = $"다발배관 패턴 검색 완료: ANN {meta.AnnCandidateCount:N0}개 → Top-{meta.ReturnedCount:N0}";
        if (ChkAutoZoom.IsChecked == true) ZoomToSelectedRoute();
    }

    /// <summary>선택된 Stub 패턴을 Query로 FEAT+DIR_UNIT 유사 검색을 실행하고, 결과 Stub 점열과
    /// anchor AABB를 3D에 겹쳐 그린다.</summary>
    private async Task SearchStubPatternsAsync()
    {
        if (_queryStubPattern is null)
            throw new InvalidOperationException("먼저 Stub 패턴 프리셋을 선택하세요.");
        var k = ParseInt(TxtK.Text, "K", 1, 50);
        var (results, meta) = await StubPatternSearch.SearchAsync(
            _settings.ToDbConfig(), _queryStubPattern.PatternId, new StubPatternSearchOptions { K = k });

        _stubPatternResults.Clear();
        foreach (var result in results) _stubPatternResults.Add(new StubPatternResultItem { Search = result });
        _routes.Clear();
        _groupRoutes.Clear();
        _suppressSelectionRefresh = true;
        GridStubPatternResults.SelectedIndex = _stubPatternResults.Count > 0 ? 0 : -1;
        _suppressSelectionRefresh = false;
        RenderStubPatternQuery();
        UpdateSceneGrid();
        TxtResultSummary.Text = $"{_stubPatternResults.Count:N0}건 · {meta.SearchTimeMs:F1}ms";
        TxtStatus.Text = $"Stub 패턴 검색 완료: ANN {meta.AnnCandidateCount:N0}개 → Top-{meta.ReturnedCount:N0}";
        if (ChkAutoZoom.IsChecked == true) ZoomToSelectedRoute();
    }

    private void RenderGroupPatternQuery()
    {
        PresetRouteLayer.Children.Clear();
        _activePatternScenePoints = [];
        if (_queryGroupPattern is null) return;
        foreach (var line in _queryGroupPattern.TrunkLines)
        {
            var points = line.Points.Select(p => new Point3D(p.X, p.Y, p.Z)).ToList();
            AddPipePath(PresetRouteLayer, points, Colors.DeepSkyBlue, 70, 0.85);
            _activePatternScenePoints.AddRange(points);
        }
    }

    private void GroupPatternResults_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        RenderGroupPatternQuery();
        RouteLayer.Children.Clear();
        if (GridGroupPatternResults.SelectedItem is GroupPatternResultItem item)
        {
            foreach (var line in item.Search.Candidate.TrunkLines)
            {
                var points = line.Points.Select(p => new Point3D(p.X, p.Y, p.Z)).ToList();
                AddPipePath(RouteLayer, points, Colors.LimeGreen, 85, 1.0);
                _activePatternScenePoints.AddRange(points);
            }
            TxtDetails.Text = BuildGroupPatternDetails(_queryGroupPattern, item);
        }
        else
        {
            TxtDetails.Text = BuildGroupPatternDetails(_queryGroupPattern, null);
        }
        UpdateSceneGrid();
        if (_suppressSelectionRefresh) return;
        if (ChkAutoZoom.IsChecked == true) ZoomToSelectedRoute();
    }

    private void RenderStubPatternQuery()
    {
        PresetRouteLayer.Children.Clear();
        _activePatternScenePoints = [];
        if (_queryStubPattern is null) return;
        AddPipePath(PresetRouteLayer, _queryStubPattern.StubPoints
            .Select(p => new Point3D(p.X, p.Y, p.Z)).ToList(), Colors.DeepSkyBlue, 60, 0.85);
        var min = new Point3D(_queryStubPattern.AnchorMin.X, _queryStubPattern.AnchorMin.Y, _queryStubPattern.AnchorMin.Z);
        var max = new Point3D(_queryStubPattern.AnchorMax.X, _queryStubPattern.AnchorMax.Y, _queryStubPattern.AnchorMax.Z);
        AddWireBox(PresetRouteLayer, min, max, Colors.SkyBlue);
        _activePatternScenePoints.AddRange(_queryStubPattern.StubPoints.Select(p => new Point3D(p.X, p.Y, p.Z)));
        _activePatternScenePoints.Add(min);
        _activePatternScenePoints.Add(max);
    }

    private void StubPatternResults_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        RenderStubPatternQuery();
        RouteLayer.Children.Clear();
        if (GridStubPatternResults.SelectedItem is StubPatternResultItem item)
        {
            AddPipePath(RouteLayer, item.StubPoints, Colors.LimeGreen, 75, 1.0);
            AddWireBox(RouteLayer, item.AnchorMin, item.AnchorMax, Colors.LightGreen);
            _activePatternScenePoints.AddRange(item.StubPoints);
            _activePatternScenePoints.Add(item.AnchorMin);
            _activePatternScenePoints.Add(item.AnchorMax);
            TxtDetails.Text = BuildStubPatternDetails(_queryStubPattern, item);
        }
        else
        {
            TxtDetails.Text = BuildStubPatternDetails(_queryStubPattern, null);
        }
        UpdateSceneGrid();
        if (_suppressSelectionRefresh) return;
        if (ChkAutoZoom.IsChecked == true) ZoomToSelectedRoute();
    }

    private static string BuildGroupPatternDetails(GroupPatternDescriptor? query, GroupPatternResultItem? candidate)
    {
        if (query is null) return "다발배관 패턴을 선택하세요.";
        var text = $"""
            Query 다발배관 패턴
            Group ID     : {query.GroupId}
            Equipment    : {query.EquipmentTag}
            Utility      : {query.UtilityGroup}/{query.Utility}
            멤버 수      : {query.NMembers}
            Pitch        : {query.PitchMm:F1} mm (CV={query.PitchCv:F3}, {(query.IsEqualSpacing ? "등간격" : "비등간격")})
            Offset Axis  : {query.OffsetAxis}
            Trunk Z      : {query.TrunkZ:F1}
            Ortho Bends  : {query.NOrthoBends} ({query.OrthoPattern})
            """;
        if (candidate is null) return text;
        var c = candidate.Search.Candidate;
        return text + $"""


            선택 후보 (Rank {candidate.Rank})
            Group ID     : {c.GroupId}
            Similarity   : {candidate.Score:F4}
            Equipment    : {c.EquipmentTag}
            멤버 수      : {c.NMembers}
            Pitch        : {c.PitchMm:F1} mm (CV={c.PitchCv:F3}, {(c.IsEqualSpacing ? "등간격" : "비등간격")})
            Offset Axis  : {c.OffsetAxis}
            """;
    }

    private static string BuildStubPatternDetails(StubPatternDescriptor? query, StubPatternResultItem? candidate)
    {
        if (query is null) return "Stub 패턴을 선택하세요.";
        var text = $"""
            Query Stub 패턴
            Pattern ID   : {query.PatternId}
            Stub Kind    : {query.StubKind} / Anchor {query.AnchorKind} ({query.AnchorName})
            Equipment    : {query.MainEquipmentName}
            Utility      : {query.UtilityGroup}/{query.Utility}
            Size         : {query.Size}
            Face         : {query.Face}
            Dir Seq      : {query.DirSeq}
            Rise/Offset  : {query.RiseMm:F1} / {query.OffsetMm:F1} mm
            Stub Length  : {query.StubLengthMm:F1} mm
            """;
        if (candidate is null) return text;
        var c = candidate.Search.Candidate;
        return text + $"""


            선택 후보 (Rank {candidate.Rank})
            Pattern ID   : {c.PatternId}
            Similarity   : {candidate.Score:F4} (Feature {candidate.FeatureScore:F4} / Direction {candidate.DirectionScore:F4})
            Equipment    : {c.MainEquipmentName}
            Size         : {c.Size}
            Face         : {c.Face}
            Dir Seq      : {c.DirSeq}
            Rise/Offset  : {c.RiseMm:F1} / {c.OffsetMm:F1} mm
            """;
    }

    private async void Results_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        RenderRoutes();
        UpdateSelectedRouteDetails();
        UpdateSceneGrid();
        if (_suppressSelectionRefresh || GridResults.SelectedItem is not TopKRouteItem) return;

        if (ChkShowObstacles.IsChecked == true && _database is not null)
            await RunBusyAsync("선택 경로 주변 장애물 로드 중...", LoadAndRenderObstaclesAsync);
        if (ChkShowBendFeatures.IsChecked == true && _database is not null)
            await RunBusyAsync("선택 경로 꺾임특징점 로드 중...", LoadAndRenderBendFeaturesAsync);
        if (ChkShowPathSegments.IsChecked == true && _database is not null)
            await RunBusyAsync("선택 경로 구간분할 로드 중...", LoadAndRenderPathSegmentsAsync);
        if (ChkAutoZoom.IsChecked == true) ZoomToSelectedRoute();
    }

    private async void GroupResults_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!IsGroupMode) return;
        RenderGroupComparison();
        UpdateSelectedGroupDetails();
        UpdateSceneGrid();
        if (_suppressSelectionRefresh || GridGroupResults.SelectedItem is not TopKGroupItem) return;
        if (ChkShowObstacles.IsChecked == true && _database is not null)
            await RunBusyAsync("Query 그룹 주변 장애물 로드 중...", LoadAndRenderObstaclesAsync);
        if (ChkShowBendFeatures.IsChecked == true && _database is not null)
            await RunBusyAsync("Query 그룹 꺾임특징점 로드 중...", LoadAndRenderBendFeaturesAsync);
        if (ChkShowPathSegments.IsChecked == true && _database is not null)
            await RunBusyAsync("Query 그룹 구간분할 로드 중...", LoadAndRenderPathSegmentsAsync);
        if (ChkAutoZoom.IsChecked == true) ZoomToSelectedRoute();
    }

    private void UpdateSelectedRouteDetails()
    {
        if (GridResults.SelectedItem is not TopKRouteItem item)
        {
            TxtDetails.Text = "경로를 선택하세요.";
            return;
        }

        var r = item.Search;
        TxtDetails.Text = $"""
            Rank             : {r.Rank}
            Route GUID       : {r.RoutePathGuid}
            Process          : {r.ProcessName}
            시작 PoC 메인장비 : {r.EquipmentName}
            종단 PoC 객체     : {(item.TargetOwnerName.Length > 0 ? item.TargetDisplay : "(정보 없음)")}
            Utility Group    : {r.UtilityGroup}
            Utility / Size   : {r.Utility} / {r.Size}
            Direction Pattern: {r.DirectionPattern}
            Length / Steps   : {r.TotalLengthMm:N1} mm / {r.StepCount}
            Point Count      : {item.Points.Count:N0}
            Geometry Source  : {item.GeometrySource}
            Weight Profile   : {FriendlyWeightProfile(_lastWeightProfile)}

            Similarity       : {r.SimilarityScore:F6}
              Position       : {r.ScorePosition:F6}
              Pattern        : {r.ScorePattern:F6}
              Feature Vector : {r.ScoreVector:F6}
              Context Vector : {r.ScoreContext:F6}
            Cosine Distance  : {r.CosineDistance:F6}

            Candidate Start  : {Format(r.StartXyz)}
            Candidate End    : {Format(r.EndXyz)}
            """;
    }

    private void UpdateSelectedGroupDetails()
    {
        if (GridGroupResults.SelectedItem is not TopKGroupItem item)
        {
            TxtDetails.Text = _queryGroup is null ? "그룹 프리셋을 선택하세요." : BuildQueryGroupDetails(_queryGroup);
            return;
        }

        var r = item.Search;
        var text = new StringBuilder();
        text.AppendLine($"Rank / Group ID : {r.Rank} / {r.Candidate.GroupVectorId}");
        text.AppendLine($"Process / 시작 PoC 메인장비 : {r.Candidate.ProcessName} / {r.Candidate.EquipmentInstanceKey}");
        text.AppendLine($"종단 PoC 객체    : {(item.TargetSummary.Length > 0 ? item.TargetSummary : "(정보 없음)")}");
        text.AppendLine($"Utility          : {r.Candidate.UtilityGroup} / {r.Candidate.Utility}");
        text.AppendLine($"배관 수 / Size   : {r.Candidate.MemberCount} / {item.Sizes}");
        text.AppendLine($"Geometry         : 실제 {item.ExactGeometryCount} / 재구성 {item.ReconstructedCount}");
        text.AppendLine($"3D 비교 모드     : {_settings.GroupComparisonView}");
        text.AppendLine();
        text.AppendLine("그룹 최종 유사도 계산");
        text.AppendLine($"  Pair 평균      : {r.MatchedAverage:F6} × {r.MatchedWeight:F6} = {r.MatchedContribution:F6}");
        text.AppendLine($"  배치 유사도    : {r.Arrangement:F6} × {r.ArrangementWeight:F6} = {r.ArrangementContribution:F6}");
        text.AppendLine($"  Coverage       : {r.Coverage:F6}");
        text.AppendLine($"  최종 유사도    : {r.Formula}");
        text.AppendLine();
        text.AppendLine($"멤버 Pair ({r.Matches.Count}개)");
        foreach (var (match, index) in r.Matches.Select((value, index) => (value, index)))
        {
            var s = match.Score;
            text.AppendLine($"#{index + 1} Q[{match.Query.MemberOrder}:{match.Query.Size}] → " +
                            $"C[{match.Candidate.MemberOrder}:{match.Candidate.Size}]  score={s.AdjustedSimilarity:F6}");
            text.AppendLine($"   Geometry Q={GeometrySource(match.Query.RoutePathGuid, _queryReconstructedRouteGuids)} / " +
                            $"C={GeometrySource(match.Candidate.RoutePathGuid, item.ReconstructedRouteGuids)}" +
                            (s.SizeCompatible ? "" : " / SIZE 불일치"));
            text.AppendLine($"   Position {s.Position:F6} × {s.WeightPosition:F6} = {s.ContributionPosition:F6}");
            text.AppendLine($"   Pattern  {s.Pattern:F6} × {s.WeightPattern:F6} = {s.ContributionPattern:F6}");
            text.AppendLine($"   Feature  {s.Feature:F6} × {s.WeightFeature:F6} = {s.ContributionFeature:F6}");
            text.AppendLine($"   Context  {s.Context:F6} × {s.WeightContext:F6} = {s.ContributionContext:F6}");
            text.AppendLine($"   Base {s.BaseSimilarity:F6} × Size {s.SizeScore:F6} = {s.AdjustedSimilarity:F6}");
        }
        if (r.UnmatchedQueryMembers.Count > 0)
            text.AppendLine($"미매칭 Query     : {string.Join(", ", r.UnmatchedQueryMembers.Select(m => $"#{m.MemberOrder}:{m.Size}"))}");
        if (r.UnmatchedCandidateMembers.Count > 0)
            text.AppendLine($"미매칭 Candidate : {string.Join(", ", r.UnmatchedCandidateMembers.Select(m => $"#{m.MemberOrder}:{m.Size}"))}");
        TxtDetails.Text = text.ToString();
    }

    private string BuildQueryGroupDetails(UtilityPipeGroupDescriptor group) => $"""
        Query Utility 배관 그룹
        Group ID        : {group.GroupVectorId}
        Process         : {group.ProcessName}
        Equipment       : {group.EquipmentInstanceKey}
        Utility         : {group.UtilityGroup} / {group.Utility}
        Member Count    : {group.MemberCount}
        Size Signature  : {string.Join(", ", group.SizeSignature.Select(item => $"{item.Key}:{item.Value}"))}
        Feature Coverage: {group.FeatureCoverage:P1}
        Context Coverage: {group.ContextCoverage:P1}
        Geometry        : 실제 {group.MemberCount - _queryReconstructedRouteGuids.Count} / 재구성 {_queryReconstructedRouteGuids.Count}

        파란색 배관은 선택한 Query 그룹의 기존 배관입니다.
        Top-K 검색 후 같은 색의 Query/Candidate 배관이 Hungarian Pair입니다.
        """;

    private void RenderRoutes()
    {
        if (IsGroupMode)
        {
            RenderGroupComparison();
            return;
        }
        if (RouteLayer is null) return;
        RouteLayer.Children.Clear();
        var selected = GridResults.SelectedItem as TopKRouteItem;
        var showAll = ChkShowAllRoutes.IsChecked == true;
        foreach (var route in _routes)
        {
            if (!showAll && route != selected) continue;
            if (route.Points.Count < 2) continue;
            var isSelected = route == selected;
            var color = isSelected ? Colors.White : RouteColors[(route.Rank - 1) % RouteColors.Length];
            if (isSelected)
            {
                AddPipePath(RouteLayer, route.Points, color, 70, 1.0);
            }
            else
            {
                RouteLayer.Children.Add(new LinesVisual3D
                {
                    Points = ToLinePairs(route.Points),
                    Color = color,
                    Thickness = Math.Max(1.5, 4.0 - route.Rank * 0.25)
                });
            }
        }
    }

    /// <summary>검색 전 Query 그룹의 기존 배관 전체를 파란색 파이프로 표시한다.</summary>
    private void RenderGroupQuery()
    {
        PresetRouteLayer.Children.Clear();
        foreach (var points in _queryGroupPoints.Values.Where(points => points.Count >= 2))
            AddPipePath(PresetRouteLayer, points, Colors.DeepSkyBlue, 55, 0.82);
    }

    /// <summary>
    /// 선택 후보의 Hungarian Pair는 Query와 Candidate를 같은 색으로 표시한다.
    /// 매칭되지 않은 Query/Candidate는 각각 빨강/주황으로 표시해 Coverage 손실 원인을 드러낸다.
    /// </summary>
    private void RenderGroupComparison()
    {
        if (!IsGroupMode || RouteLayer is null) return;
        RouteLayer.Children.Clear();
        PresetRouteLayer.Children.Clear();
        var selected = GridGroupResults.SelectedItem as TopKGroupItem;
        if (selected is null)
        {
            RenderGroupQuery();
            return;
        }

        var candidatePoints = GetDisplayedCandidatePoints(selected);
        for (var index = 0; index < selected.Search.Matches.Count; index++)
        {
            var match = selected.Search.Matches[index];
            var color = RouteColors[index % RouteColors.Length];
            if (_queryGroupPoints.TryGetValue(match.Query.RoutePathGuid, out var queryPath))
                AddPipePath(PresetRouteLayer, queryPath, color, 58, 0.58);
            if (candidatePoints.TryGetValue(match.Candidate.RoutePathGuid, out var candidatePath))
            {
                AddPipePath(RouteLayer, candidatePath, color, 72, 1.0);
                if (!match.Score.SizeCompatible)
                    RouteLayer.Children.Add(new LinesVisual3D
                    {
                        Points = ToLinePairs(candidatePath), Color = Colors.Yellow, Thickness = 5.0
                    });
            }
        }

        if (_settings.ShowUnmatchedGroupMembers)
        {
            foreach (var member in selected.Search.UnmatchedQueryMembers)
                if (_queryGroupPoints.TryGetValue(member.RoutePathGuid, out var points))
                    AddPipePath(PresetRouteLayer, points, Colors.Red, 62, 0.85);
            foreach (var member in selected.Search.UnmatchedCandidateMembers)
                if (candidatePoints.TryGetValue(member.RoutePathGuid, out var points))
                    AddPipePath(RouteLayer, points, Colors.DarkOrange, 72, 1.0);
        }

        // 실제좌표 모드에서만 나머지 Top-K 그룹을 얇은 선으로 함께 보여준다.
        if (ChkShowAllRoutes.IsChecked == true &&
            _settings.GroupComparisonView.Equals("Original", StringComparison.OrdinalIgnoreCase))
        {
            foreach (var group in _groupRoutes.Where(group => group != selected))
            {
                var color = RouteColors[(group.Rank - 1) % RouteColors.Length];
                foreach (var points in group.PointsByRouteGuid.Values.Where(points => points.Count >= 2))
                    RouteLayer.Children.Add(new LinesVisual3D
                    {
                        Points = ToLinePairs(points), Color = color,
                        Thickness = Math.Max(1.0, 2.8 - group.Rank * 0.18)
                    });
            }
        }
    }

    private Dictionary<string, List<Point3D>> GetDisplayedCandidatePoints(TopKGroupItem item)
    {
        if (!_settings.GroupComparisonView.Equals("SideBySide", StringComparison.OrdinalIgnoreCase))
            return item.PointsByRouteGuid;
        var queryPoints = _queryGroupPoints.Values.SelectMany(points => points).ToArray();
        var candidatePoints = item.PointsByRouteGuid.Values.SelectMany(points => points).ToArray();
        if (queryPoints.Length == 0 || candidatePoints.Length == 0) return item.PointsByRouteGuid;

        var queryMaxX = queryPoints.Max(point => point.X);
        var queryWidth = queryMaxX - queryPoints.Min(point => point.X);
        var candidateMinX = candidatePoints.Min(point => point.X);
        var queryCenterY = (queryPoints.Min(point => point.Y) + queryPoints.Max(point => point.Y)) / 2.0;
        var queryCenterZ = (queryPoints.Min(point => point.Z) + queryPoints.Max(point => point.Z)) / 2.0;
        var candidateCenterY = (candidatePoints.Min(point => point.Y) + candidatePoints.Max(point => point.Y)) / 2.0;
        var candidateCenterZ = (candidatePoints.Min(point => point.Z) + candidatePoints.Max(point => point.Z)) / 2.0;
        var shift = new Vector3D(
            queryMaxX + Math.Max(queryWidth * 0.20, 1500) - candidateMinX,
            queryCenterY - candidateCenterY,
            queryCenterZ - candidateCenterZ);
        return item.PointsByRouteGuid.ToDictionary(
            pair => pair.Key,
            pair => pair.Value.Select(point => point + shift).ToList(),
            StringComparer.OrdinalIgnoreCase);
    }

    private void RenderMarkers()
    {
        MarkerLayer.Children.Clear();
        if (IsGroupMode)
        {
            RenderGroupMarkers();
            return;
        }
        MarkerLayer.Children.Add(new SphereVisual3D
        {
            Center = _queryStart, Radius = 120, Fill = Brushes.LimeGreen
        });
        MarkerLayer.Children.Add(new SphereVisual3D
        {
            Center = _queryEnd, Radius = 120, Fill = Brushes.OrangeRed
        });
    }

    private void RenderGroupMarkers()
    {
        MarkerLayer.Children.Clear();
        foreach (var points in _queryGroupPoints.Values.Where(points => points.Count >= 2))
        {
            MarkerLayer.Children.Add(new SphereVisual3D
                { Center = points[0], Radius = 75, Fill = Brushes.LimeGreen });
            MarkerLayer.Children.Add(new SphereVisual3D
                { Center = points[^1], Radius = 75, Fill = Brushes.OrangeRed });
        }
    }

    private async void ObstacleToggle_Changed(object sender, RoutedEventArgs e)
    {
        if (LegendObstacles is not null)
            LegendObstacles.Visibility = ChkShowObstacles.IsChecked == true ? Visibility.Visible : Visibility.Collapsed;
        if (!IsLoaded || ObstacleLayer is null) return;
        if (ChkShowObstacles.IsChecked != true)
        {
            ObstacleLayer.Children.Clear();
            return;
        }
        if (GetActiveScenePoints().Count < 2 || _database is null) return;
        await RunBusyAsync("주변 구조 BIM 로드 중...", LoadAndRenderObstaclesAsync);
        ZoomToSelectedRoute();
    }

    private async Task LoadAndRenderObstaclesAsync()
    {
        if (_database is null) return;
        // Side-by-Side 후보는 시각 비교를 위한 가상 이동 좌표이다. 장애물은 DB 원좌표인
        // Query 그룹 주변에서만 조회하여 존재하지 않는 위치의 BIM이 섞이지 않게 한다.
        IReadOnlyList<Point3D> activePoints = IsGroupMode
            ? _queryGroupPoints.Values.SelectMany(points => points).ToArray()
            : GetActiveScenePoints();
        if (activePoints.Count < 2) return;

        // Top-K 전체는 서로 멀리 떨어진 기존 설계일 수 있다. 선택 경로의 주변만 조회해야
        // 화면에 필요한 장애물이 limit 앞부분에서 누락되지 않고 카메라도 과도하게 멀어지지 않는다.
        var allPoints = activePoints;
        var min = new Point3D(allPoints.Min(p => p.X), allPoints.Min(p => p.Y), allPoints.Min(p => p.Z));
        var max = new Point3D(allPoints.Max(p => p.X), allPoints.Max(p => p.Y), allPoints.Max(p => p.Z));
        var obstacles = await _database.LoadObstaclesAsync(min, max, 1000, _settings.ObstacleLimit);

        ObstacleLayer.Children.Clear();
        AddObstacleGroup(
            obstacles.Where(o => o.Type.StartsWith("COLUMN", StringComparison.OrdinalIgnoreCase)),
            Color.FromRgb(0x7D, 0x8C, 0x9B), 0.30, Colors.LightSlateGray);
        AddObstacleGroup(
            obstacles.Where(o => o.Type.StartsWith("BEAM", StringComparison.OrdinalIgnoreCase)),
            Color.FromRgb(0xC8, 0x63, 0x63), 0.28, Colors.IndianRed);
        TxtStatus.Text += $", 주변 구조 BIM={obstacles.Count:N0}건";
    }

    private async void BendFeatureToggle_Changed(object sender, RoutedEventArgs e)
    {
        if (LegendBendFeature is not null)
            LegendBendFeature.Visibility = ChkShowBendFeatures.IsChecked == true ? Visibility.Visible : Visibility.Collapsed;
        if (!IsLoaded || BendFeatureLayer is null) return;
        if (ChkShowBendFeatures.IsChecked != true)
        {
            BendFeatureLayer.Children.Clear();
            return;
        }
        if (_database is null) return;
        await RunBusyAsync("꺾임특징점 로드 중...", LoadAndRenderBendFeaturesAsync);
    }

    /// <summary>
    /// Tools/ExtractBendFeaturePoints.py가 적재한 TB_ROUTE_BEND_FEATURE_POINT를 현재 화면에
    /// 표시 중인 배관(개별 선택 Route, 또는 그룹모드의 Query+선택 후보 그룹 멤버)에 한해 조회하고
    /// 원인(CAUSE)별로 색상을 구분한 큐브 마커로 표시한다. build 미실행 환경에서는 빈 결과로
    /// 조용히 넘어가므로(ViewerDatabaseService 참고) 이 기능이 없어도 기존 화면은 그대로 동작한다.
    /// </summary>
    private async Task LoadAndRenderBendFeaturesAsync()
    {
        BendFeatureLayer.Children.Clear();
        if (_database is null) return;
        var guids = GetActiveRouteGuids();
        if (guids.Count == 0) return;
        var markers = await _database.LoadBendFeaturePointsAsync(guids);
        foreach (var marker in markers)
        {
            const double side = 140;
            BendFeatureLayer.Children.Add(new BoxVisual3D
            {
                Center = marker.Point,
                Length = side,
                Width = side,
                Height = side,
                Fill = new SolidColorBrush(BendCauseColor(marker.Cause))
            });
        }
        TxtStatus.Text += $", 꺾임특징점={markers.Count:N0}건";
    }

    private async void PathSegmentToggle_Changed(object sender, RoutedEventArgs e)
    {
        if (LegendPathSegment is not null)
            LegendPathSegment.Visibility = ChkShowPathSegments.IsChecked == true ? Visibility.Visible : Visibility.Collapsed;
        if (!IsLoaded || PathSegmentLayer is null) return;
        if (ChkShowPathSegments.IsChecked != true)
        {
            PathSegmentLayer.Children.Clear();
            return;
        }
        if (_database is null) return;
        await RunBusyAsync("구간분할 로드 중...", LoadAndRenderPathSegmentsAsync);
    }

    /// <summary>
    /// Tools/PathSegmenter.py가 적재한 TB_ROUTE_PATH_SEGMENTATION을 현재 화면에 표시 중인
    /// 배관(개별 선택 Route, 또는 그룹모드의 Query+선택 후보 그룹 멤버)에 한해 조회하고
    /// Start Stub/Middle Trunk/End Stub을 3색 선으로 겹쳐 그린다. 다발배관 패턴/Stub 패턴
    /// 모드는 개별 route 선택 개념이 없어 GetActiveRouteGuids가 빈 목록을 돌려주므로 자연히
    /// 아무것도 그리지 않는다. build 미실행 환경에서는 빈 결과로 조용히 넘어간다.
    /// </summary>
    private async Task LoadAndRenderPathSegmentsAsync()
    {
        PathSegmentLayer.Children.Clear();
        if (_database is null) return;
        var guids = GetActiveRouteGuids();
        if (guids.Count == 0) return;
        var segments = await _database.LoadPathSegmentationBatchAsync(guids);
        foreach (var segment in segments)
        {
            AddPipePath(PathSegmentLayer, segment.StartStub.ToList(), Colors.Gold, 95, 0.9);
            AddPipePath(PathSegmentLayer, segment.MiddleTrunk.ToList(), Colors.WhiteSmoke, 95, 0.55);
            AddPipePath(PathSegmentLayer, segment.EndStub.ToList(), Colors.Cyan, 95, 0.9);
        }
        TxtStatus.Text += $", 구간분할={segments.Count:N0}건";
    }

    private IReadOnlyList<string> GetActiveRouteGuids()
    {
        if (IsGroupMode)
        {
            var guids = new HashSet<string>(_queryGroupPoints.Keys, StringComparer.OrdinalIgnoreCase);
            if (GridGroupResults.SelectedItem is TopKGroupItem group)
                foreach (var guid in GetDisplayedCandidatePoints(group).Keys) guids.Add(guid);
            return guids.ToArray();
        }
        if (GridResults.SelectedItem is TopKRouteItem route) return [route.Guid];
        return string.IsNullOrWhiteSpace(_presetRouteGuid) ? [] : [_presetRouteGuid];
    }

    private static Color BendCauseColor(string cause) => cause switch
    {
        "OBSTACLE_AVOID" => Colors.OrangeRed,
        "DESTINATION_ENTRY" => Colors.LimeGreen,
        "ZONE_CONSTRAINT" => Colors.DeepSkyBlue,
        "GROUP_ALIGNMENT" => Colors.MediumPurple,
        _ => Colors.Gainsboro,
    };

    private void RouteDisplay_Changed(object sender, RoutedEventArgs e) => RenderRoutes();

    private void ZoomExtents_Click(object sender, RoutedEventArgs e)
    {
        ZoomToSelectedRoute();
    }

    private void FeatureGeneration_Click(object sender, RoutedEventArgs e)
    {
        if (_featureGenerationDialog is { IsLoaded: true })
        {
            _featureGenerationDialog.Activate();
            return;
        }
        _settings = ReadSettingsFromUi();
        _settings.Save();
        _featureGenerationDialog = new FeatureGenerationDialog(_settings.ToDbConfig(), _settings.PythonExe)
        {
            Owner = this
        };
        _featureGenerationDialog.Closed += (_, _) => _featureGenerationDialog = null;
        _featureGenerationDialog.Show();
    }

    private void ClearViewer_Click(object sender, RoutedEventArgs e)
    {
        _routes.Clear();
        _groupRoutes.Clear();
        _groupPatternResults.Clear();
        _stubPatternResults.Clear();
        RouteLayer.Children.Clear();
        PresetRouteLayer.Children.Clear();
        _presetRoutePoints = [];
        _presetRouteGuid = "";
        _queryGroup = null;
        _queryGroupPoints.Clear();
        _queryReconstructedRouteGuids.Clear();
        _queryGroupPattern = null;
        _queryStubPattern = null;
        _activePatternScenePoints = [];
        GridGroupMembers.ItemsSource = null;
        MarkerLayer.Children.Clear();
        ObstacleLayer.Children.Clear();
        BendFeatureLayer.Children.Clear();
        PathSegmentLayer.Children.Clear();
        GridResults.SelectedItem = null;
        GridGroupResults.SelectedItem = null;
        GridGroupPatternResults.SelectedItem = null;
        GridStubPatternResults.SelectedItem = null;
        TxtDetails.Clear();
        TxtResultSummary.Text = "검색 전";
        TxtStatus.Text = "3D 화면과 검색결과를 초기화했습니다.";
    }

    private async Task RunBusyAsync(string status, Func<Task> action)
    {
        try
        {
            Progress.Visibility = Visibility.Visible;
            BtnSearch.IsEnabled = false;
            TxtStatus.Text = status;
            await action();
        }
        catch (Exception ex)
        {
            TxtStatus.Text = $"오류: {ex.Message}";
            var message = ex is Npgsql.PostgresException postgres
                ? $"PostgreSQL 오류 ({postgres.SqlState})\n{postgres.MessageText}"
                : ex.Message;
            MessageBox.Show(this, message, "TopK.3DViewer 오류", MessageBoxButton.OK, MessageBoxImage.Error);
        }
        finally
        {
            Progress.Visibility = Visibility.Collapsed;
            BtnSearch.IsEnabled = true;
        }
    }

    private ViewerSettings ReadSettingsFromUi()
    {
        // 새 객체를 만들면 Load()에서 결정한 설정 파일 경로가 사라질 수 있으므로
        // 현재 인스턴스를 갱신한다. 실행 위치가 달라도 처음 읽은 JSON에 계속 저장된다.
        _settings.Host = TxtHost.Text.Trim();
        _settings.Port = ParseInt(TxtPort.Text, "Port", 1, 65535);
        _settings.Database = TxtDatabase.Text.Trim();
        _settings.User = TxtUser.Text.Trim();
        _settings.Password = TxtPassword.Password;
        _settings.DefaultK = ParseInt(TxtK.Text, "K", 1, 50);
        _settings.UseObstacleContext = ChkUseContext.IsChecked == true;
        _settings.WeightPosition = ParseWeight(TxtWeightPosition.Text, "Position");
        _settings.WeightPattern = ParseWeight(TxtWeightPattern.Text, "Pattern");
        _settings.WeightVector = ParseWeight(TxtWeightVector.Text, "Feature");
        _settings.WeightContext = ParseWeight(TxtWeightContext.Text, "Context");
        _settings.RedistributeMissingPatternWeight = ChkRedistributeMissingPattern.IsChecked == true;
        _settings.SearchUnit = IsGroupMode ? "Group" : "Individual";
        _settings.GroupSizeMatchMode = ComboContent(CmbGroupSizeMode);
        _settings.GroupMatchedWeight = ParseWeight(TxtGroupMatchedWeight.Text, "그룹 Pair 평균");
        _settings.GroupArrangementWeight = ParseWeight(TxtGroupArrangementWeight.Text, "그룹 배치");
        _settings.GroupComparisonView = ComboContent(CmbGroupViewMode);
        _settings.ShowUnmatchedGroupMembers = ChkShowUnmatchedGroupMembers.IsChecked == true;
        _settings.EqualizeEnabledWeights();
        _settings.NormalizeGroupWeights();
        ApplyWeightEditors(_settings);
        TxtGroupMatchedWeight.Text = _settings.GroupMatchedWeight.ToString("G6", CultureInfo.InvariantCulture);
        TxtGroupArrangementWeight.Text = _settings.GroupArrangementWeight.ToString("G6", CultureInfo.InvariantCulture);
        return _settings;
    }

    private void ResetWeights_Click(object sender, RoutedEventArgs e)
    {
        TxtWeightPosition.Text = "25";
        TxtWeightPattern.Text = "25";
        TxtWeightVector.Text = "25";
        TxtWeightContext.Text = "25";
        ChkRedistributeMissingPattern.IsChecked = true;
        SaveUiSettings("모든 유사도 항목을 활성화하여 25%씩 균등 배분했습니다.");
    }

    private void WeightEditor_LostKeyboardFocus(object sender, System.Windows.Input.KeyboardFocusChangedEventArgs e) =>
        SaveUiSettings("활성 가중치를 균등 배분하고 JSON에 저장했습니다.");

    private void SettingsOption_Changed(object sender, RoutedEventArgs e)
    {
        if (IsLoaded) SaveUiSettings("검색 옵션을 JSON에 저장했습니다.");
    }

    private void GroupWeight_LostKeyboardFocus(object sender, System.Windows.Input.KeyboardFocusChangedEventArgs e) =>
        SaveUiSettings("그룹 Match/Arrangement 가중치를 정규화하고 JSON에 저장했습니다.");

    private void GroupOption_Changed(object sender, RoutedEventArgs e)
    {
        if (!IsLoaded) return;
        SaveUiSettings("그룹 검색/표시 옵션을 JSON에 저장했습니다.");
        RenderGroupComparison();
        UpdateSceneGrid();
    }

    private void SaveUiSettings(string status)
    {
        try
        {
            _settings = ReadSettingsFromUi();
            _settings.Save();
            TxtStatus.Text = $"{status} ({_settings.SettingsFilePath})";
        }
        catch (Exception ex)
        {
            TxtStatus.Text = $"설정 저장 오류: {ex.Message}";
        }
    }

    private void ApplyWeightEditors(ViewerSettings settings)
    {
        TxtWeightPosition.Text = settings.WeightPosition.ToString("G6", CultureInfo.InvariantCulture);
        TxtWeightPattern.Text = settings.WeightPattern.ToString("G6", CultureInfo.InvariantCulture);
        TxtWeightVector.Text = settings.WeightVector.ToString("G6", CultureInfo.InvariantCulture);
        TxtWeightContext.Text = settings.WeightContext.ToString("G6", CultureInfo.InvariantCulture);
    }

    private static double ParseWeight(string value, string name)
    {
        var valid = double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out var result) ||
                    double.TryParse(value, NumberStyles.Float, CultureInfo.CurrentCulture, out result);
        if (!valid || !double.IsFinite(result) || result < 0)
            throw new ArgumentException($"{name} 가중치는 0 이상의 숫자여야 합니다: '{value}'");
        return result;
    }

    private static string FriendlyWeightProfile(string profile)
    {
        if (!profile.StartsWith("custom:", StringComparison.OrdinalIgnoreCase)) return profile;
        var parts = profile.Split(':');
        var values = parts.Length > 1 ? parts[1].Split('/') : [];
        if (values.Length != 4) return profile;
        var parsed = values.Select(value =>
            double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out var number)
                ? number * 100.0
                : 0.0).ToArray();
        var suffix = profile.Contains("pattern-auto-off", StringComparison.OrdinalIgnoreCase)
            ? " (Pattern 자동 제외)"
            : "";
        return $"W Pos/Pat/Feat/Ctx={parsed[0]:F1}/{parsed[1]:F1}/{parsed[2]:F1}/{parsed[3]:F1}%{suffix}";
    }

    private static void SetCombo(ComboBox combo, IReadOnlyList<string> values)
    {
        var previous = combo.Text;
        combo.ItemsSource = values;
        combo.Text = previous;
    }

    private static string Value(ComboBox combo) => combo.Text.Trim();

    private static string ComboContent(ComboBox combo) =>
        combo.SelectedItem is ComboBoxItem item ? Convert.ToString(item.Content, CultureInfo.InvariantCulture) ?? "" : combo.Text.Trim();

    private static void SelectComboContent(ComboBox combo, string value)
    {
        foreach (var candidate in combo.Items.OfType<ComboBoxItem>())
        {
            if (!string.Equals(Convert.ToString(candidate.Content, CultureInfo.InvariantCulture), value,
                    StringComparison.OrdinalIgnoreCase)) continue;
            combo.SelectedItem = candidate;
            return;
        }
        if (combo.Items.Count > 0) combo.SelectedIndex = 0;
    }

    private static Point3D ReadPoint(TextBox x, TextBox y, TextBox z, string name) => new(
        ParseDouble(x.Text, $"{name} X"), ParseDouble(y.Text, $"{name} Y"), ParseDouble(z.Text, $"{name} Z"));

    private static void SetPoint(TextBox x, TextBox y, TextBox z, Point3D point)
    {
        x.Text = point.X.ToString("G", CultureInfo.InvariantCulture);
        y.Text = point.Y.ToString("G", CultureInfo.InvariantCulture);
        z.Text = point.Z.ToString("G", CultureInfo.InvariantCulture);
    }

    private static double ParseDouble(string value, string name)
    {
        if (!double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out var result) || !double.IsFinite(result))
            throw new ArgumentException($"{name} 좌표가 올바른 숫자가 아닙니다: '{value}'");
        return result;
    }

    private static int ParseInt(string value, string name, int min, int max)
    {
        if (!int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out var result) || result < min || result > max)
            throw new ArgumentException($"{name}은(는) {min}~{max} 범위의 정수여야 합니다: '{value}'");
        return result;
    }

    /// <summary>
    /// 선택 경로를 화면 두께 선이 아닌 실제 원통 파이프 체인으로 만든다.
    /// 선택되지 않은 Top-K는 비교 성능을 위해 LinesVisual3D로 유지한다.
    /// </summary>
    private static void AddPipePath(ModelVisual3D layer, IReadOnlyList<Point3D> points,
        Color color, double diameter, double opacity)
    {
        var brush = new SolidColorBrush(Color.FromArgb((byte)(255 * opacity), color.R, color.G, color.B));
        brush.Freeze();
        var material = new DiffuseMaterial(brush);
        material.Freeze();
        for (var i = 0; i < points.Count - 1; i++)
        {
            if ((points[i + 1] - points[i]).Length < 0.1) continue;
            layer.Children.Add(new PipeVisual3D
            {
                Point1 = points[i],
                Point2 = points[i + 1],
                Diameter = diameter,
                Material = material,
                BackMaterial = material
            });
        }
    }

    /// <summary>Stub Pattern의 anchor AABB를 얇은 와이어프레임 상자로 표시한다.</summary>
    private static void AddWireBox(ModelVisual3D layer, Point3D min, Point3D max, Color color)
    {
        var edges = new Point3DCollection(24);
        AddBoxEdges(edges, min, max);
        edges.Freeze();
        layer.Children.Add(new LinesVisual3D { Points = edges, Color = color, Thickness = 0.8 });
    }

    /// <summary>동일 유형 장애물을 반투명 솔리드와 외곽선으로 함께 표시한다.</summary>
    private void AddObstacleGroup(IEnumerable<BimObstacle> source, Color fill,
        double opacity, Color outline)
    {
        var obstacles = source.Where(IsValidBox).ToList();
        if (obstacles.Count == 0) return;
        ObstacleLayer.Children.Add(CreateSolidBoxBatch(obstacles, fill, opacity));

        var edges = new Point3DCollection(obstacles.Count * 24);
        foreach (var obstacle in obstacles) AddBoxEdges(edges, obstacle.Minimum, obstacle.Maximum);
        edges.Freeze();
        ObstacleLayer.Children.Add(new LinesVisual3D
        {
            Points = edges,
            Color = outline,
            Thickness = 0.8
        });
    }

    private static bool IsValidBox(BimObstacle obstacle) =>
        obstacle.Maximum.X >= obstacle.Minimum.X &&
        obstacle.Maximum.Y >= obstacle.Minimum.Y &&
        obstacle.Maximum.Z >= obstacle.Minimum.Z;

    /// <summary>
    /// 장애물마다 BoxVisual3D를 만들면 수천 개의 draw call이 발생한다. 모든 AABB를 하나의
    /// MeshGeometry3D로 합쳐 선택 경로를 회전·확대할 때도 화면 응답성을 유지한다.
    /// </summary>
    private static ModelVisual3D CreateSolidBoxBatch(IReadOnlyList<BimObstacle> obstacles,
        Color color, double opacity)
    {
        var positions = new Point3DCollection(obstacles.Count * 8);
        var indices = new Int32Collection(obstacles.Count * 36);
        int[,] faces =
        {
            {0,1,2,3}, {4,7,6,5}, {0,4,5,1},
            {2,6,7,3}, {0,3,7,4}, {1,5,6,2}
        };

        foreach (var obstacle in obstacles)
        {
            var min = obstacle.Minimum;
            var max = obstacle.Maximum;
            var offset = positions.Count;
            positions.Add(new Point3D(min.X, min.Y, min.Z));
            positions.Add(new Point3D(max.X, min.Y, min.Z));
            positions.Add(new Point3D(max.X, max.Y, min.Z));
            positions.Add(new Point3D(min.X, max.Y, min.Z));
            positions.Add(new Point3D(min.X, min.Y, max.Z));
            positions.Add(new Point3D(max.X, min.Y, max.Z));
            positions.Add(new Point3D(max.X, max.Y, max.Z));
            positions.Add(new Point3D(min.X, max.Y, max.Z));

            for (var face = 0; face < faces.GetLength(0); face++)
            {
                var a = offset + faces[face, 0];
                var b = offset + faces[face, 1];
                var c = offset + faces[face, 2];
                var d = offset + faces[face, 3];
                indices.Add(a); indices.Add(b); indices.Add(c);
                indices.Add(a); indices.Add(c); indices.Add(d);
            }
        }

        positions.Freeze();
        indices.Freeze();
        var mesh = new MeshGeometry3D { Positions = positions, TriangleIndices = indices };
        mesh.Freeze();
        var brush = new SolidColorBrush(Color.FromArgb((byte)(255 * opacity), color.R, color.G, color.B));
        brush.Freeze();
        var material = new DiffuseMaterial(brush);
        material.Freeze();
        var model = new GeometryModel3D(mesh, material) { BackMaterial = material };
        return new ModelVisual3D { Content = model };
    }

    /// <summary>고정 원점 Grid 때문에 원거리 플랜트 좌표의 경로가 작아지는 문제를 방지한다.</summary>
    private void UpdateSceneGrid()
    {
        var points = GetActiveScenePoints();
        if (points.Count == 0) return;
        var minX = points.Min(p => p.X);
        var maxX = points.Max(p => p.X);
        var minY = points.Min(p => p.Y);
        var maxY = points.Max(p => p.Y);
        var minZ = points.Min(p => p.Z);
        var size = Math.Clamp(Math.Max(maxX - minX, maxY - minY) + 4000, 10000, 100000);
        SceneGrid.Center = new Point3D((minX + maxX) / 2, (minY + maxY) / 2, minZ);
        SceneGrid.Width = size;
        SceneGrid.Length = size;
    }

    /// <summary>전체 씬이 아니라 선택 경로의 실제 좌표 범위에 카메라를 맞춘다.</summary>
    private void ZoomToSelectedRoute()
    {
        var points = GetActiveScenePoints();
        if (points.Count < 2) return;
        var minX = points.Min(p => p.X);
        var maxX = points.Max(p => p.X);
        var minY = points.Min(p => p.Y);
        var maxY = points.Max(p => p.Y);
        var minZ = points.Min(p => p.Z);
        var maxZ = points.Max(p => p.Z);
        var padX = Math.Max((maxX - minX) * 0.15, 500);
        var padY = Math.Max((maxY - minY) * 0.15, 500);
        var padZ = Math.Max((maxZ - minZ) * 0.15, 500);
        Viewport.ZoomExtents(new Rect3D(
            minX - padX, minY - padY, minZ - padZ,
            Math.Max(maxX - minX, 1) + padX * 2,
            Math.Max(maxY - minY, 1) + padY * 2,
            Math.Max(maxZ - minZ, 1) + padZ * 2), 500);
    }

    private IReadOnlyList<Point3D> GetActiveScenePoints()
    {
        if (IsGroupPatternMode || IsStubPatternMode) return _activePatternScenePoints;
        if (IsGroupMode)
        {
            var points = _queryGroupPoints.Values.SelectMany(route => route).ToList();
            if (GridGroupResults.SelectedItem is TopKGroupItem group)
                points.AddRange(GetDisplayedCandidatePoints(group).Values.SelectMany(route => route));
            return points;
        }
        if (GridResults.SelectedItem is TopKRouteItem route && route.Points.Count > 0)
            return route.Points;
        return _presetRoutePoints;
    }

    /// <summary>
    /// legacy Feature row에 원본 상세 GUID가 없을 때도 빈 화면이 되지 않도록 시작/종점 사이를
    /// 3축 직교 polyline으로 재구성한다. 상세창에 재구성임을 명확히 표시한다.
    /// </summary>
    private static List<Point3D> BuildMetadataFallbackPolyline(SearchResult result)
    {
        var start = new Point3D(result.StartXyz.X, result.StartXyz.Y, result.StartXyz.Z);
        var end = new Point3D(result.EndXyz.X, result.EndXyz.Y, result.EndXyz.Z);
        var verticalFirst = result.DirectionPattern.Split('-', StringSplitOptions.RemoveEmptyEntries)
            .FirstOrDefault()?.Equals("R", StringComparison.OrdinalIgnoreCase) == true;
        var points = verticalFirst
            ? new List<Point3D>
            {
                start,
                new(start.X, start.Y, end.Z),
                new(end.X, start.Y, end.Z),
                end
            }
            : new List<Point3D>
            {
                start,
                new(end.X, start.Y, start.Z),
                new(end.X, end.Y, start.Z),
                end
            };
        return points.Where((point, index) => index == 0 || (point - points[index - 1]).Length > 1e-6).ToList();
    }

    private static List<Point3D> BuildMemberFallbackPolyline(UtilityPipeGroupMember member)
    {
        var start = new Point3D(member.StartX, member.StartY, member.StartZ);
        var end = new Point3D(member.EndX, member.EndY, member.EndZ);
        var proxy = new[]
        {
            start,
            new Point3D(end.X, start.Y, start.Z),
            new Point3D(end.X, end.Y, start.Z),
            end
        };
        return proxy.Where((point, index) => index == 0 || (point - proxy[index - 1]).Length > 1e-6).ToList();
    }

    private static Point3DCollection ToLinePairs(IReadOnlyList<Point3D> points)
    {
        var pairs = new Point3DCollection((points.Count - 1) * 2);
        for (var i = 0; i < points.Count - 1; i++)
        {
            pairs.Add(points[i]);
            pairs.Add(points[i + 1]);
        }
        return pairs;
    }

    private static void AddBoxEdges(Point3DCollection target, Point3D min, Point3D max)
    {
        var p = new[]
        {
            new Point3D(min.X,min.Y,min.Z), new Point3D(max.X,min.Y,min.Z),
            new Point3D(max.X,max.Y,min.Z), new Point3D(min.X,max.Y,min.Z),
            new Point3D(min.X,min.Y,max.Z), new Point3D(max.X,min.Y,max.Z),
            new Point3D(max.X,max.Y,max.Z), new Point3D(min.X,max.Y,max.Z)
        };
        int[,] edges =
        {
            {0,1},{1,2},{2,3},{3,0}, {4,5},{5,6},{6,7},{7,4}, {0,4},{1,5},{2,6},{3,7}
        };
        for (var i = 0; i < edges.GetLength(0); i++)
        {
            target.Add(p[edges[i, 0]]);
            target.Add(p[edges[i, 1]]);
        }
    }

    private static string Short(string value, int length) =>
        string.IsNullOrEmpty(value) || value.Length <= length ? value : value[..length] + "…";

    private static string Format((double X, double Y, double Z) point) =>
        $"({point.X:N1}, {point.Y:N1}, {point.Z:N1})";

    private static string GeometrySource(string routeGuid, IReadOnlySet<string> reconstructed) =>
        reconstructed.Contains(routeGuid) ? "메타데이터 재구성" : "DB 실제 상세경로";
}
