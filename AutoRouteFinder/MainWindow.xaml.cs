using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Media3D;
using HelixToolkit.Wpf;
using Npgsql;
using AutoRouteFinder.Models;
using AutoRoutingLibrary.Core;
using AutoRoutingLibrary.Models;

namespace AutoRouteFinder
{
    [ValueConversion(typeof(string), typeof(Brush))]
    public class UtilityGroupToBrushConverter : IValueConverter
    {
        public object Convert(object value, Type targetType, object parameter, System.Globalization.CultureInfo culture)
        {
            if (value is string grp)
            {
                return grp.ToLowerInvariant() switch
                {
                    "exhaust" => new SolidColorBrush(Color.FromRgb(220, 100, 100)),
                    "gas" => new SolidColorBrush(Color.FromRgb(100, 200, 100)),
                    "vacuum" => new SolidColorBrush(Color.FromRgb(100, 150, 220)),
                    "water" => new SolidColorBrush(Color.FromRgb(220, 180, 80)),
                    "toxic" => new SolidColorBrush(Color.FromRgb(200, 100, 220)),
                    _ => Brushes.White
                };
            }
            return Brushes.White;
        }

        public object ConvertBack(object value, Type targetType, object parameter, System.Globalization.CultureInfo culture)
        {
            throw new NotImplementedException();
        }
    }

    public sealed class FeatureProfileUI
    {
        public string UtilityGroup { get; init; } = string.Empty;
        public string PreferredSourceFace { get; init; } = "Any";
        public string PreferredTargetFace { get; init; } = "Any";
        public List<double> PreferredRackZs { get; init; } = new();
        public int SpinePointsCount { get; init; }
        public int TopKCount { get; init; }

        public string PreferredRackZsString =>
            PreferredRackZs == null || PreferredRackZs.Count == 0 
                ? "N/A" 
                : string.Join(", ", PreferredRackZs.Select(z => $"{z:F0}mm"));
    }

    public sealed class RouteResultUI
    {
        public string Utility { get; init; } = string.Empty;
        public string Status { get; set; } = "대기";
        public double LengthMm { get; set; }
        public int Turns { get; set; }
        public string FailReason { get; set; } = string.Empty;

        public string LengthText => Status == "성공" ? $"{LengthMm:N0}" : "-";
        public string TurnsText => Status == "성공" ? $"{Turns}" : "-";

        // 상세 프로세스 출력을 위한 속성
        public TaskInfo? TaskInfo { get; set; }
        public long ExpandedNodes { get; set; }
        public double ElapsedMs { get; set; }
        public TubeVisual3D? Visual { get; set; }
        public GoalDirection GoalDirConstraint { get; set; }
        public List<double> PreferredRackZs { get; } = new();
        public bool HasSpineCorridor { get; set; }
    }

    public partial class MainWindow : Window
    {
        private DbConfig? _db;
        private SceneData? _currentSceneData;
        private Dictionary<string, FeatureProfileRow> _featureProfiles = new(StringComparer.OrdinalIgnoreCase);
        private bool _isLoaded;

        // 3D Visual3D 객체 그룹 리스트
        private readonly List<Visual3D> _obstacleVisuals = new();
        private readonly List<(Visual3D Visual, bool IsMain)> _equipmentVisuals = new();
        private readonly List<(Visual3D Visual, string Category, string Utility)> _ductLateralVisuals = new();
        private readonly List<(Visual3D Visual, string Group, string Utility)> _existingPipeVisuals = new();
        private readonly List<(Visual3D Visual, string Group, string Utility)> _autoPipeVisuals = new();
        private readonly List<(Visual3D Visual, string Group, string Utility)> _pocVisuals = new();
        private readonly List<Visual3D> _spineVisuals = new();
        private readonly List<Visual3D> _topKVisuals = new();

        public MainWindow()
        {
            InitializeComponent();
            this.Loaded += (s, e) =>
            {
                LoadSettings();
                _isLoaded = true;
                ConnectAndLoadProjects();
            };
        }

        private void LoadSettings()
        {
            try
            {
                string exeDir = AppDomain.CurrentDomain.BaseDirectory;
                string path = Path.Combine(exeDir, "grouppatternviewer.settings.json");
                if (!File.Exists(path) && File.Exists("grouppatternviewer.settings.json"))
                {
                    path = "grouppatternviewer.settings.json";
                }

                if (File.Exists(path))
                {
                    var json = File.ReadAllText(path, System.Text.Encoding.UTF8);
                    using var doc = JsonDocument.Parse(json);
                    if (doc.RootElement.TryGetProperty("db", out var dbProp))
                    {
                        string host = dbProp.TryGetProperty("host", out var h) ? h.GetString() ?? "localhost" : "localhost";
                        int port = dbProp.TryGetProperty("port", out var p) ? p.GetInt32() : 5432;
                        string dbname = dbProp.TryGetProperty("database", out var d) ? d.GetString() ?? "DDW_AI_DB" : "DDW_AI_DB";
                        string user = dbProp.TryGetProperty("user", out var u) ? u.GetString() ?? "postgres" : "postgres";
                        string pwd = dbProp.TryGetProperty("password", out var pw) ? pw.GetString() ?? "dinno" : "dinno";

                        _db = new DbConfig
                        {
                            Host = host,
                            Port = port,
                            Database = dbname,
                            User = user,
                            Password = pwd
                        };
                    }
                }
                else
                {
                    _db = new DbConfig
                    {
                        Host = "localhost",
                        Port = 5432,
                        Database = "DDW_AI_DB",
                        User = "postgres",
                        Password = "dinno"
                    };
                }
            }
            catch
            {
                _db = new DbConfig
                {
                    Host = "localhost",
                    Port = 5432,
                    Database = "DDW_AI_DB",
                    User = "postgres",
                    Password = "dinno"
                };
            }
        }

        private async void ConnectAndLoadProjects()
        {
            if (_db == null) return;

            TxtConnStatus.Text = "● DB 연결 중...";
            TxtConnStatus.Foreground = Brushes.Orange;

            try
            {
                List<ProjectInfo>? projects = null;
                await Task.Run(() =>
                {
                    using var conn = new NpgsqlConnection(_db.ConnectionString);
                    conn.Open();
                    projects = ObstacleDbLoader.ListProjects(_db);
                });

                TxtConnStatus.Text = "● DB 로드 완료";
                TxtConnStatus.Foreground = new SolidColorBrush(Color.FromRgb(46, 139, 87));

                CmbProject.SelectionChanged -= CmbProject_SelectionChanged;
                CmbProject.ItemsSource = projects;
                CmbProject.SelectionChanged += CmbProject_SelectionChanged;

                if (projects != null && projects.Count > 0)
                {
                    CmbProject.SelectedIndex = 0;
                }
            }
            catch (Exception ex)
            {
                TxtConnStatus.Text = "● 연결 실패";
                TxtConnStatus.Foreground = Brushes.Crimson;
                TxtStatus.Text = $"에러: {ex.Message}";
            }
        }

        private async void CmbProject_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (CmbProject.SelectedItem is not ProjectInfo selectedProj || _db == null) return;

            GridLoading.Visibility = Visibility.Visible;
            TxtStatus.Text = "프로젝트 데이터를 가져오는 중...";

            try
            {
                // 3D 뷰 및 상태 데이터 초기화
                Clear3DVisuals();
                _autoPipeVisuals.Clear();
                DgRouteResults.ItemsSource = null;
                TxtRouteDetailLog.Text = "경로를 선택하면 생성 프로세스 상세 정보가 여기에 나타납니다.";

                // 그리드 원점 및 크기 세팅
                double cx = (selectedProj.MinX + selectedProj.MaxX) / 2.0;
                double cy = (selectedProj.MinY + selectedProj.MaxY) / 2.0;
                double cz = selectedProj.MinZ;
                GridLines.Center = new Point3D(cx, cy, cz);
                GridLines.Width = Math.Max(10000, (selectedProj.MaxX - selectedProj.MinX) * 1.5);
                GridLines.Length = Math.Max(10000, (selectedProj.MaxY - selectedProj.MinY) * 1.5);

                // DB 씬 및 특징 프로필 로드
                var sceneData = await Task.Run(() => ObstacleDbLoader.LoadScene(_db, selectedProj));
                _currentSceneData = sceneData;

                var profiles = await Task.Run(() => ObstacleDbLoader.LoadFeatureProfiles(_db, selectedProj.GroupName));
                _featureProfiles = profiles;

                // 3D 렌더링
                RenderScene(sceneData);

                // 특징 프로필 탭 바인딩
                BindFeatureProfiles();

                // 유틸리티 그룹 콤보박스 바인딩 및 초기화
                PopulateUtilityGroups();

                Viewport3D.ZoomExtents(500);
                TxtStatus.Text = $"프로젝트 '{selectedProj.GroupName}' 로딩 성공 (장애물 {sceneData.Obstacles.Count}개, 작업 {sceneData.Tasks.Count}개)";
            }
            catch (Exception ex)
            {
                TxtStatus.Text = $"에러: {ex.Message}";
                MessageBox.Show(this, $"프로젝트 로드 중 오류 발생:\n{ex.Message}", "에러", MessageBoxButton.OK, MessageBoxImage.Error);
            }
            finally
            {
                GridLoading.Visibility = Visibility.Collapsed;
            }
        }

        private void BindFeatureProfiles()
        {
            if (_featureProfiles == null) return;
            string selectedGroup = CmbUtilityGroup.SelectedItem as string ?? "전체";

            var uiList = new List<FeatureProfileUI>();
            foreach (var kvp in _featureProfiles)
            {
                if (selectedGroup != "전체" && !string.Equals(kvp.Value.UtilityGroup, selectedGroup, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                int nodeCount = 0;
                try
                {
                    using var doc = JsonDocument.Parse(kvp.Value.TrunkCenterlineJson);
                    if (doc.RootElement.ValueKind == JsonValueKind.Array)
                    {
                        nodeCount = doc.RootElement.GetArrayLength();
                    }
                }
                catch { }

                uiList.Add(new FeatureProfileUI
                {
                    UtilityGroup = kvp.Value.UtilityGroup,
                    PreferredSourceFace = kvp.Value.PreferredSourceFace,
                    PreferredTargetFace = kvp.Value.PreferredTargetFace,
                    PreferredRackZs = kvp.Value.PreferredRackZs,
                    SpinePointsCount = nodeCount,
                    TopKCount = kvp.Value.TopKCount
                });
            }
            LstFeatureProfiles.ItemsSource = uiList;
        }

        private void ApplyFilters()
        {
            if (_currentSceneData == null) return;

            string selectedGroup = CmbUtilityGroup.SelectedItem as string ?? "전체";
            string selectedUtil = CmbUtility.SelectedItem as string ?? "전체";
            var searchText = TxtSearchGroup.Text.Trim().ToLower();

            var filtered = _currentSceneData.Tasks.Where(t =>
                (selectedGroup == "전체" || string.Equals(t.Group, selectedGroup, StringComparison.OrdinalIgnoreCase)) &&
                (selectedUtil == "전체" || string.Equals(t.Utility, selectedUtil, StringComparison.OrdinalIgnoreCase)) &&
                (string.IsNullOrEmpty(searchText) || 
                 (t.Group != null && t.Group.ToLower().Contains(searchText)) ||
                 (t.Utility != null && t.Utility.ToLower().Contains(searchText)))
            ).ToList();

            DgTasks.ItemsSource = filtered;
            TxtTaskCount.Text = $"대상 배관: {filtered.Count}개";

            BindFeatureProfiles();

            // 3D 뷰어에 선택된 유틸리티 그룹의 Spine 시각화 반영
            ClearSpineVisuals();
            if (selectedGroup != "전체" && _featureProfiles != null && _featureProfiles.TryGetValue(selectedGroup, out var profile))
            {
                if (!string.IsNullOrEmpty(profile.TrunkCenterlineJson))
                {
                    DrawSpineVisual(profile.TrunkCenterlineJson, selectedGroup);
                }
            }
        }

        private void FilterFields_Changed(object sender, KeyEventArgs e)
        {
            if (_isLoaded) ApplyFilters();
        }

        private void RenderScene(SceneData sd)
        {
            Clear3DVisuals();

            // 1. 장애물 그리기 (회색 반투명)
            var obstacleBrush = new SolidColorBrush(Color.FromArgb(40, 128, 128, 128));
            foreach (var o in sd.Obstacles)
            {
                double dx = o.MaxX - o.MinX;
                double dy = o.MaxY - o.MinY;
                double dz = o.MaxZ - o.MinZ;
                var box = new BoxVisual3D
                {
                    Center = new Point3D(o.MinX + dx / 2, o.MinY + dy / 2, o.MinZ + dz / 2),
                    Length = dx,
                    Width = dy,
                    Height = dz,
                    Fill = obstacleBrush,
                    Visible = ChkShowObstacles.IsChecked == true
                };
                Viewport3D.Children.Add(box);
                _obstacleVisuals.Add(box);
            }

            // 2. 장비 그리기 (주설비: 황금색, 부대장비: 연한 구리색/Peru)
            var mainEquipBrush = new SolidColorBrush(Color.FromArgb(70, 218, 165, 32)); // Gold
            var subEquipBrush = new SolidColorBrush(Color.FromArgb(60, 205, 133, 63)); // Peru (SubTool)
            foreach (var e in sd.Equipment)
            {
                double dx = e.MaxX - e.MinX;
                double dy = e.MaxY - e.MinY;
                double dz = e.MaxZ - e.MinZ;
                var box = new BoxVisual3D
                {
                    Center = new Point3D(e.MinX + dx / 2, e.MinY + dy / 2, e.MinZ + dz / 2),
                    Length = dx,
                    Width = dy,
                    Height = dz,
                    Fill = e.IsMain ? mainEquipBrush : subEquipBrush,
                    Visible = e.IsMain ? (ChkShowMainEquipment.IsChecked == true) : (ChkShowSubEquipment.IsChecked == true)
                };
                Viewport3D.Children.Add(box);
                _equipmentVisuals.Add((box, e.IsMain));
            }

            // 2.5. 덕트 및 레터럴 그리기 (덕트: 스틸블루, 레터럴: 아쿠아마린)
            var ductBrush = new SolidColorBrush(Color.FromArgb(80, 176, 196, 222)); // LightSteelBlue
            var lateralBrush = new SolidColorBrush(Color.FromArgb(80, 102, 205, 170)); // MediumAquamarine
            foreach (var dl in sd.DuctsLaterals)
            {
                double dx = dl.MaxX - dl.MinX;
                double dy = dl.MaxY - dl.MinY;
                double dz = dl.MaxZ - dl.MinZ;
                var box = new BoxVisual3D
                {
                    Center = new Point3D(dl.MinX + dx / 2, dl.MinY + dy / 2, dl.MinZ + dz / 2),
                    Length = dx,
                    Width = dy,
                    Height = dz,
                    Fill = dl.IsLateral ? lateralBrush : ductBrush,
                    Visible = ChkShowDuctsLaterals.IsChecked == true
                };
                Viewport3D.Children.Add(box);
                _ductLateralVisuals.Add((box, dl.Category, dl.Utility ?? ""));
            }

            // 3. 기존 설계 배관 그리기 (반투명 주황/초록)
            var converter = new UtilityGroupToBrushConverter();
            foreach (var pipe in sd.ExistingPipes)
            {
                if (pipe.Points == null || pipe.Points.Count < 2) continue;

                var pts = new Point3DCollection();
                foreach (var p in pipe.Points) pts.Add(new Point3D(p.X, p.Y, p.Z));

                var brush = converter.Convert(pipe.Group, typeof(Brush), null, System.Globalization.CultureInfo.InvariantCulture) as Brush ?? Brushes.Green;
                var transparentBrush = brush.Clone();
                transparentBrush.Opacity = 0.45; // 기존 설계는 반투명 튜브로 표기

                var tube = new TubeVisual3D
                {
                    Path = pts,
                    Diameter = pipe.DiameterMm > 0 ? pipe.DiameterMm : 100,
                    Fill = transparentBrush,
                    IsPathClosed = false,
                    Visible = ChkShowExistingPipes.IsChecked == true
                };
                Viewport3D.Children.Add(tube);
                _existingPipeVisuals.Add((tube, pipe.Group ?? "", pipe.Utility ?? ""));
            }

            // 4. 시작/종단 PoC 마커 표시 (시작=빨강, 종단=파랑 구체, 관경보다 20% 더 크게 렌더링)
            foreach (var task in sd.Tasks)
            {
                double sphereRadius = (task.DiameterMm > 0 ? task.DiameterMm : 100.0) * 0.6;
                var startSphere = new SphereVisual3D
                {
                    Center = new Point3D(task.Sx, task.Sy, task.Sz),
                    Radius = sphereRadius,
                    Fill = Brushes.Red
                };
                var endSphere = new SphereVisual3D
                {
                    Center = new Point3D(task.Gx, task.Gy, task.Gz),
                    Radius = sphereRadius,
                    Fill = Brushes.Blue
                };

                Viewport3D.Children.Add(startSphere);
                Viewport3D.Children.Add(endSphere);
                _pocVisuals.Add((startSphere, task.Group ?? "", task.Utility ?? ""));
                _pocVisuals.Add((endSphere, task.Group ?? "", task.Utility ?? ""));
            }
        }

        private void Clear3DVisuals()
        {
            foreach (var v in _obstacleVisuals) Viewport3D.Children.Remove(v);
            _obstacleVisuals.Clear();
            foreach (var v in _equipmentVisuals) Viewport3D.Children.Remove(v.Visual);
            _equipmentVisuals.Clear();
            foreach (var v in _ductLateralVisuals) Viewport3D.Children.Remove(v.Visual);
            _ductLateralVisuals.Clear();
            foreach (var v in _existingPipeVisuals) Viewport3D.Children.Remove(v.Visual);
            _existingPipeVisuals.Clear();
            foreach (var v in _pocVisuals) Viewport3D.Children.Remove(v.Visual);
            _pocVisuals.Clear();
            ClearSpineVisuals();
            ClearTopKVisuals();
            ClearAutoRoutingVisuals();
        }

        private void ClearAutoRoutingVisuals()
        {
            foreach (var v in _autoPipeVisuals) Viewport3D.Children.Remove(v.Visual);
            _autoPipeVisuals.Clear();
        }

        private void ClearSpineVisuals()
        {
            foreach (var v in _spineVisuals) Viewport3D.Children.Remove(v);
            _spineVisuals.Clear();
        }

        private void ClearTopKVisuals()
        {
            foreach (var v in _topKVisuals) Viewport3D.Children.Remove(v);
            _topKVisuals.Clear();
        }

        private void DrawSpineVisual(string json, string groupName)
        {
            try
            {
                var pts = new List<Point3D>();
                using var doc = JsonDocument.Parse(json);
                if (doc.RootElement.ValueKind != JsonValueKind.Array) return;

                foreach (var elem in doc.RootElement.EnumerateArray())
                {
                    double x = elem.GetProperty("X").GetDouble();
                    double y = elem.GetProperty("Y").GetDouble();
                    double z = elem.GetProperty("Z").GetDouble();
                    pts.Add(new Point3D(x, y, z));
                }

                if (pts.Count >= 2)
                {
                    var ptsCol = new Point3DCollection(pts);
                    var spineBrush = new SolidColorBrush(Color.FromRgb(255, 128, 0)); // Bright Orange
                    var tube = new TubeVisual3D
                    {
                        Path = ptsCol,
                        Diameter = 120.0,
                        Fill = spineBrush,
                        IsPathClosed = false,
                        Visible = ChkShowSpines.IsChecked == true
                    };
                    Viewport3D.Children.Add(tube);
                    _spineVisuals.Add(tube);
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[경고] Spine rendering 실패: {ex.Message}");
            }
        }

        private void DrawTopKVisuals(List<RoutingAI.Standalone.SearchResult> results)
        {
            var topKColors = new Color[] {
                Color.FromRgb(186, 85, 211),   // MediumOrchid
                Color.FromRgb(147, 112, 219),  // MediumSlateBlue
                Color.FromRgb(138, 43, 226)    // BlueViolet
            };

            for (int rIdx = 0; rIdx < results.Count; rIdx++)
            {
                var res = results[rIdx];
                var pathPts = LoadPathPoints(res.RoutePathGuid);
                if (pathPts.Count < 2) continue;

                var ptsCol = new Point3DCollection();
                foreach (var p in pathPts)
                {
                    ptsCol.Add(new Point3D(p.X, p.Y, p.Z));
                }

                var color = topKColors[rIdx % topKColors.Length];
                var brush = new SolidColorBrush(color) { Opacity = 0.75 };

                var tube = new TubeVisual3D
                {
                    Path = ptsCol,
                    Diameter = 80.0, // Thinner
                    Fill = brush,
                    IsPathClosed = false,
                    Visible = ChkShowTopKPipes.IsChecked == true
                };
                Viewport3D.Children.Add(tube);
                _topKVisuals.Add(tube);
            }
        }

        private void ShowSpineForSelectedTask(TaskInfo task)
        {
            ClearSpineVisuals();
            if (string.IsNullOrEmpty(task.Group)) return;

            if (_featureProfiles.TryGetValue(task.Group, out var profile) && !string.IsNullOrEmpty(profile.TrunkCenterlineJson))
            {
                DrawSpineVisual(profile.TrunkCenterlineJson, task.Group);
            }
        }

        private async void ShowTopKForSelectedTask(TaskInfo task)
        {
            if (_db == null || _currentSceneData == null) return;

            LstTopKDesigns.ItemsSource = null;
            TxtSelectedTaskInfo.Text = "유사설계 검색 중...";
            ClearTopKVisuals();

            try
            {
                var (results, _) = await RoutingAI.Standalone.TopKSearchStandalone.SearchAsync(
                    db: new RoutingAI.Standalone.DbConfig(
                        Host: _db.Host,
                        Port: _db.Port,
                        Database: _db.Database,
                        User: _db.User,
                        Password: _db.Password
                    ),
                    processName: "",
                    equipmentName: task.EquipmentTag ?? "",
                    utilityGroup: task.Group ?? "",
                    utility: task.Utility ?? "",
                    startXyz: (task.Sx, task.Sy, task.Sz),
                    endXyz: (task.Gx, task.Gy, task.Gz),
                    k: 3
                );

                if (results == null || results.Count == 0)
                {
                    TxtSelectedTaskInfo.Text = $"선택된 작업: {task.Utility} ({task.Group})\n유사설계 결과가 없습니다.";
                    return;
                }

                TxtSelectedTaskInfo.Text = $"선택된 작업: {task.Utility} ({task.Group}), K={results.Count} 유사설계 매칭";
                LstTopKDesigns.ItemsSource = results;

                DrawTopKVisuals(results);
            }
            catch (Exception ex)
            {
                TxtSelectedTaskInfo.Text = $"오류: {ex.Message}";
            }
        }

        private void Option_Checked(object sender, RoutedEventArgs e)
        {
            UpdateAllVisibilities();
        }

        private void PopulateUtilityGroups()
        {
            if (_currentSceneData == null) return;

            var groups = _currentSceneData.Tasks
                .Select(t => t.Group)
                .Where(g => !string.IsNullOrEmpty(g))
                .Distinct()
                .OrderBy(g => g)
                .ToList();

            CmbUtilityGroup.SelectionChanged -= CmbUtilityGroup_SelectionChanged;
            var groupItems = new List<string> { "전체" };
            foreach (var g in groups) groupItems.Add(g!);
            CmbUtilityGroup.ItemsSource = groupItems;
            CmbUtilityGroup.SelectedIndex = 0;
            CmbUtilityGroup.SelectionChanged += CmbUtilityGroup_SelectionChanged;

            PopulateUtilities();
        }

        private void PopulateUtilities()
        {
            if (_currentSceneData == null) return;

            string selectedGroup = CmbUtilityGroup.SelectedItem as string ?? "전체";

            var utils = _currentSceneData.Tasks
                .Where(t => selectedGroup == "전체" || string.Equals(t.Group, selectedGroup, StringComparison.OrdinalIgnoreCase))
                .Select(t => t.Utility)
                .Where(u => !string.IsNullOrEmpty(u))
                .Distinct()
                .OrderBy(u => u)
                .ToList();

            CmbUtility.SelectionChanged -= CmbUtility_SelectionChanged;
            var utilItems = new List<string> { "전체" };
            foreach (var u in utils) utilItems.Add(u!);
            CmbUtility.ItemsSource = utilItems;
            CmbUtility.SelectedIndex = 0;
            CmbUtility.SelectionChanged += CmbUtility_SelectionChanged;

            ApplyFilters();
            UpdateAllVisibilities();
        }

        private void CmbUtilityGroup_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            PopulateUtilities();
        }

        private void CmbUtility_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            ApplyFilters();
            UpdateAllVisibilities();
        }

        private void UpdateAllVisibilities()
        {
            if (!_isLoaded) return;

            // 1. 장애물 및 장비 (체크박스만 반영)
            foreach (var v in _obstacleVisuals) { if (v is HelixToolkit.Wpf.MeshElement3D mv) mv.Visible = ChkShowObstacles.IsChecked == true; }
            
            bool showMain = ChkShowMainEquipment.IsChecked == true;
            bool showSub = ChkShowSubEquipment.IsChecked == true;
            foreach (var item in _equipmentVisuals)
            {
                if (item.Visual is HelixToolkit.Wpf.MeshElement3D mv)
                {
                    mv.Visible = item.IsMain ? showMain : showSub;
                }
            }

            // 2. 파이프 및 PoC 마커 (필터 조건 + 체크박스)
            string selectedGroup = CmbUtilityGroup.SelectedItem as string ?? "전체";
            string selectedUtil = CmbUtility.SelectedItem as string ?? "전체";

            bool showExisting = ChkShowExistingPipes.IsChecked == true;
            bool showAuto = ChkShowAutoPipes.IsChecked == true;
            bool showDucts = ChkShowDuctsLaterals.IsChecked == true;

            foreach (var item in _existingPipeVisuals)
            {
                bool matchGroup = (selectedGroup == "전체" || string.Equals(item.Group, selectedGroup, StringComparison.OrdinalIgnoreCase));
                bool matchUtil = (selectedUtil == "전체" || string.Equals(item.Utility, selectedUtil, StringComparison.OrdinalIgnoreCase));
                if (item.Visual is HelixToolkit.Wpf.MeshElement3D mv)
                {
                    mv.Visible = showExisting && matchGroup && matchUtil;
                }
            }

            foreach (var item in _autoPipeVisuals)
            {
                bool matchGroup = (selectedGroup == "전체" || string.Equals(item.Group, selectedGroup, StringComparison.OrdinalIgnoreCase));
                bool matchUtil = (selectedUtil == "전체" || string.Equals(item.Utility, selectedUtil, StringComparison.OrdinalIgnoreCase));
                if (item.Visual is HelixToolkit.Wpf.MeshElement3D mv)
                {
                    mv.Visible = showAuto && matchGroup && matchUtil;
                }
            }

            foreach (var item in _pocVisuals)
            {
                bool matchGroup = (selectedGroup == "전체" || string.Equals(item.Group, selectedGroup, StringComparison.OrdinalIgnoreCase));
                bool matchUtil = (selectedUtil == "전체" || string.Equals(item.Utility, selectedUtil, StringComparison.OrdinalIgnoreCase));
                if (item.Visual is HelixToolkit.Wpf.MeshElement3D mv)
                {
                    mv.Visible = matchGroup && matchUtil;
                }
            }

            // 3. 덕트 및 레터럴 (체크박스만 반영)
            foreach (var item in _ductLateralVisuals)
            {
                if (item.Visual is HelixToolkit.Wpf.MeshElement3D mv)
                {
                    mv.Visible = showDucts;
                }
            }

            // 4. 공용 척추선 및 유사설계 Top-K (체크박스 반영)
            bool showSpines = ChkShowSpines.IsChecked == true;
            bool showTopK = ChkShowTopKPipes.IsChecked == true;

            foreach (var v in _spineVisuals)
            {
                if (v is TubeVisual3D tube) tube.Visible = showSpines;
            }

            foreach (var v in _topKVisuals)
            {
                if (v is TubeVisual3D tube) tube.Visible = showTopK;
            }
        }

        private void BtnZoomExtents_Click(object sender, RoutedEventArgs e)
        {
            Viewport3D.ZoomExtents(500);
        }

        private void BtnCopyToClipboard_Click(object sender, RoutedEventArgs e)
        {
            try
            {
                Viewport3D.Copy();
                TxtStatus.Text = "● 화면 복사 완료 (클립보드 저장됨)";
            }
            catch (Exception ex)
            {
                try
                {
                    // 대체 캡처 방식
                    int w = (int)Viewport3D.ActualWidth;
                    int h = (int)Viewport3D.ActualHeight;
                    if (w > 0 && h > 0)
                    {
                        var rtb = new System.Windows.Media.Imaging.RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32);
                        rtb.Render(Viewport3D);
                        Clipboard.SetImage(rtb);
                        TxtStatus.Text = "● 화면 복사 완료 (대체 캡처)";
                    }
                }
                catch (Exception ex2)
                {
                    TxtStatus.Text = $"화면 복사 실패: {ex2.Message}";
                    MessageBox.Show(this, $"화면 복사 실패:\n{ex2.Message}\n{ex.Message}", "에러", MessageBoxButton.OK, MessageBoxImage.Error);
                }
            }
        }

        private void BtnClear3D_Click(object sender, RoutedEventArgs e)
        {
            Clear3DVisuals();
            _autoPipeVisuals.Clear();
            DgRouteResults.ItemsSource = null;
            TxtRouteDetailLog.Text = "경로를 선택하면 생성 프로세스 상세 정보가 여기에 나타납니다.";
        }

        private void DgTasks_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (DgTasks.SelectedItem is TaskInfo task)
            {
                // 해당 작업 포커싱
                var look = new Point3D((task.Sx + task.Gx) / 2.0, (task.Sy + task.Gy) / 2.0, (task.Sz + task.Gz) / 2.0);
                Viewport3D.Camera.LookAt(look, 3000, 500);

                ShowSpineForSelectedTask(task);
                ShowTopKForSelectedTask(task);
            }
        }

        private void DgRouteResults_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (DgRouteResults.ItemsSource is System.Collections.ObjectModel.ObservableCollection<RouteResultUI> list)
            {
                var selected = DgRouteResults.SelectedItem as RouteResultUI;

                foreach (var item in list)
                {
                    if (item.Visual is TubeVisual3D tube)
                    {
                        if (item == selected)
                        {
                            // Highlight in Yellow
                            tube.Fill = Brushes.Yellow;

                            // Focus camera on the path center
                            if (tube.Path != null && tube.Path.Count > 0)
                            {
                                var center = GetPathCenter(tube.Path);
                                Viewport3D.Camera.LookAt(center, 2000, 300);
                            }
                        }
                        else
                        {
                            // Reset to default Cyan
                            tube.Fill = new SolidColorBrush(Color.FromRgb(0, 255, 255));
                        }
                    }
                }

                if (selected != null)
                {
                    TxtRouteDetailLog.Text = BuildRouteDetailLog(selected);
                }
                else
                {
                    TxtRouteDetailLog.Text = "경로를 선택하면 생성 프로세스 상세 정보가 여기에 나타납니다.";
                }
            }
        }

        private Point3D GetPathCenter(Point3DCollection path)
        {
            double sumX = 0, sumY = 0, sumZ = 0;
            foreach (var p in path)
            {
                sumX += p.X;
                sumY += p.Y;
                sumZ += p.Z;
            }
            return new Point3D(sumX / path.Count, sumY / path.Count, sumZ / path.Count);
        }

        private string BuildRouteDetailLog(RouteResultUI r)
        {
            if (r.TaskInfo == null) return "선택한 경로의 태스크 정보가 존재하지 않습니다.";

            var sb = new System.Text.StringBuilder();
            sb.AppendLine($"[설계 단계 1] 작업 설정 조회");
            sb.AppendLine($"  • 대상 설비(장비): {r.TaskInfo.EquipmentTag ?? "미지정"}");
            sb.AppendLine($"  • 유틸리티 그룹: {r.TaskInfo.Group ?? "미지정"} ({r.TaskInfo.Utility ?? "미지정"})");
            sb.AppendLine($"  • 관경(Diameter): {(r.TaskInfo.DiameterMm > 0 ? $"{r.TaskInfo.DiameterMm:F0}A" : "100A")}");
            sb.AppendLine($"  • 시작 PoC: {r.TaskInfo.PocName ?? "Start"} ({r.TaskInfo.Sx:F0}, {r.TaskInfo.Sy:F0}, {r.TaskInfo.Sz:F0})");
            sb.AppendLine($"  • 종단 PoC: {r.TaskInfo.EndName ?? "End"} ({r.TaskInfo.Gx:F0}, {r.TaskInfo.Gy:F0}, {r.TaskInfo.Gz:F0})");
            sb.AppendLine();

            sb.AppendLine($"[설계 단계 2] 기존 설계 특징점(Design Feature Profile) 주입");
            if (r.PreferredRackZs.Count > 0)
            {
                sb.AppendLine($"  • 선호 Rack Z고도 제약: {string.Join(", ", r.PreferredRackZs.Select(z => $"{z:F0}mm"))}");
            }
            else
            {
                sb.AppendLine($"  • 선호 Rack Z고도 제약: 없음 (전체 공간 탐색)");
            }

            if (r.GoalDirConstraint != GoalDirection.Any)
            {
                sb.AppendLine($"  • 선호 접속 방향 제약: {r.GoalDirConstraint} (포트 직진성 확보)");
            }
            else
            {
                sb.AppendLine($"  • 선호 접속 방향 제약: 없음 (자유 진입)");
            }

            sb.AppendLine($"  • 공용 척추선(Spine) 회랑 가중치: {(r.HasSpineCorridor ? "활성화 (기존 설계 척추선 레인 강도 반영)" : "비활성화")}");
            sb.AppendLine();

            sb.AppendLine($"[설계 단계 3] Native Routing3D A* 탐색 기동");
            sb.AppendLine($"  • 탐색 노드(Voxel) 확장 수: {r.ExpandedNodes:N0} 노드");
            sb.AppendLine($"  • 연산 소요 시간: {r.ElapsedMs:F2} ms");
            sb.AppendLine($"  • 탐색 결과: {r.Status}");
            if (r.Status == "실패")
            {
                string desc = r.FailReason switch
                {
                    "StartBlocked" => "시작포트 영역이 다른 설비/장애물에 가로막혀 경로 확장에 실패했습니다.",
                    "GoalBlocked" => "종단포트 영역이 다른 설비/장애물에 가로막혀 경로 확장에 실패했습니다.",
                    "CorridorMiss" => "공용 척추선 회랑 진입 불가로 탐색에 실패했습니다.",
                    "GoalDirBlocked" => "접속 방향(Goal Direction) 제약을 만족하는 진입 경로를 찾지 못했습니다.",
                    "NoPath" => "장애물이 너무 조밀하여 유효한 연결 경로를 찾을 수 없습니다.",
                    _ => r.FailReason
                };
                sb.AppendLine($"  • 실패 상세 원인: {r.FailReason} ({desc})");
            }
            else if (r.Status == "대기")
            {
                sb.AppendLine($"  • 대기 중: 연산이 아직 수행되지 않았습니다.");
            }
            else
            {
                sb.AppendLine($"  • 탐색 성공: 최적 경로 탐색 완료.");
            }
            sb.AppendLine();

            if (r.Status == "성공")
            {
                sb.AppendLine($"[설계 단계 4] 경로 최적화 및 튜브 렌더링");
                sb.AppendLine($"  • 최종 배관 설계 길이: {r.LengthMm:N0} mm");
                sb.AppendLine($"  • 꺾임 횟수(Turns): {r.Turns} 회");
            }

            return sb.ToString();
        }

        // 척추선 Json을 파싱해 회랑 셀로 복셀화하는 헬퍼 메서드
        private List<PathCell> BuildFeatureSpineCells(string json, GridMeta g, int dilate)
        {
            var cell = g.CellMm;
            var list = new List<PathCell>();
            if (cell <= 0) return list;

            var set = new HashSet<(int, int, int)>();
            try
            {
                using var doc = JsonDocument.Parse(json);
                var root = doc.RootElement;
                if (root.ValueKind == JsonValueKind.Array)
                {
                    var pts = new List<Pt3>();
                    foreach (var item in root.EnumerateArray())
                    {
                        if (item.TryGetProperty("X", out var xProp) &&
                            item.TryGetProperty("Y", out var yProp) &&
                            item.TryGetProperty("Z", out var zProp))
                        {
                            pts.Add(new Pt3(xProp.GetDouble(), yProp.GetDouble(), zProp.GetDouble()));
                        }
                    }

                    for (int i = 1; i < pts.Count; i++)
                    {
                        var a = pts[i - 1];
                        var b = pts[i];
                        double dx = b.X - a.X, dy = b.Y - a.Y, dz = b.Z - a.Z;
                        double len = Math.Sqrt(dx * dx + dy * dy + dz * dz);
                        int steps = Math.Max(1, (int)(len / (cell * 0.5)));
                        for (int sIdx = 0; sIdx <= steps; sIdx++)
                        {
                            double tt = (double)sIdx / steps;
                            int ci = (int)Math.Floor((a.X + dx * tt - g.Ox) / cell);
                            int cj = (int)Math.Floor((a.Y + dy * tt - g.Oy) / cell);
                            int ck = (int)Math.Floor((a.Z + dz * tt - g.Oz) / cell);

                            for (int di = -dilate; di <= dilate; di++)
                                for (int dj = -dilate; dj <= dilate; dj++)
                                    for (int dk = -dilate; dk <= dilate; dk++)
                                    {
                                        int ii = ci + di, jj = cj + dj, kk = ck + dk;
                                        if (ii < 0 || jj < 0 || kk < 0 || ii >= g.Nx || jj >= g.Ny || kk >= g.Nz) continue;
                                        set.Add((ii, jj, kk));
                                    }
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[경고] Spine JSON 파싱 실패: {ex.Message}");
            }

            foreach (var (i, j, k) in set)
            {
                list.Add(new PathCell(i, j, k));
            }
            return list;
        }

        private static GoalDirection ConvertFaceToGoalDir(string face)
        {
            if (string.IsNullOrEmpty(face)) return GoalDirection.Any;
            return face.ToLowerInvariant().Trim() switch
            {
                "+x" => GoalDirection.PositiveX,
                "-x" => GoalDirection.NegativeX,
                "+y" => GoalDirection.PositiveY,
                "-y" => GoalDirection.NegativeY,
                "+z" => GoalDirection.PositiveZ,
                "-z" => GoalDirection.NegativeZ,
                _ => GoalDirection.Any
            };
        }

        private List<Pt3> LoadPathPoints(string guid)
        {
            var pts = new List<Pt3>();
            if (_db == null) return pts;
            using var conn = new Npgsql.NpgsqlConnection(_db.ConnectionString);
            conn.Open();
            using var cmd = new Npgsql.NpgsqlCommand(
                @"SELECT sd.""FROM_POSX"", sd.""FROM_POSY"", sd.""FROM_POSZ"",
                         sd.""TO_POSX"",   sd.""TO_POSY"",   sd.""TO_POSZ""
                    FROM ""TB_ROUTE_SEGMENT_DETAIL"" sd
                    JOIN ""TB_ROUTE_SEGMENTS"" s ON s.""SEGMENT_GUID"" = sd.""SEGMENT_GUID""
                   WHERE s.""ROUTE_PATH_GUID"" = @guid
                   ORDER BY s.""ORDER"", sd.""ORDER""", conn);
            cmd.Parameters.AddWithValue("@guid", guid);

            using var r = cmd.ExecuteReader();
            Pt3? lastTo = null;
            while (r.Read())
            {
                double fx = r.IsDBNull(0) ? 0 : r.GetDouble(0);
                double fy = r.IsDBNull(1) ? 0 : r.GetDouble(1);
                double fz = r.IsDBNull(2) ? 0 : r.GetDouble(2);
                double tx = r.IsDBNull(3) ? 0 : r.GetDouble(3);
                double ty = r.IsDBNull(4) ? 0 : r.GetDouble(4);
                double tz = r.IsDBNull(5) ? 0 : r.GetDouble(5);

                Pt3 from = new Pt3(fx, fy, fz);
                Pt3 to = new Pt3(tx, ty, tz);

                if (lastTo.HasValue)
                {
                    double d2 = (lastTo.Value.X - from.X) * (lastTo.Value.X - from.X) +
                                (lastTo.Value.Y - from.Y) * (lastTo.Value.Y - from.Y) +
                                (lastTo.Value.Z - from.Z) * (lastTo.Value.Z - from.Z);
                    if (d2 <= 100.0)
                    {
                        from = lastTo.Value;
                    }
                }

                if (pts.Count == 0)
                {
                    pts.Add(from);
                }
                else
                {
                    double d1 = (pts[pts.Count - 1].X - from.X) * (pts[pts.Count - 1].X - from.X) +
                                (pts[pts.Count - 1].Y - from.Y) * (pts[pts.Count - 1].Y - from.Y) +
                                (pts[pts.Count - 1].Z - from.Z) * (pts[pts.Count - 1].Z - from.Z);
                    if (d1 > 1.0)
                    {
                        pts.Add(from);
                    }
                }

                double dto = (pts[pts.Count - 1].X - to.X) * (pts[pts.Count - 1].X - to.X) +
                            (pts[pts.Count - 1].Y - to.Y) * (pts[pts.Count - 1].Y - to.Y) +
                            (pts[pts.Count - 1].Z - to.Z) * (pts[pts.Count - 1].Z - to.Z);
                if (dto > 1.0)
                {
                    pts.Add(to);
                }
                lastTo = to;
            }
            return pts;
        }

        private List<PathCell> VoxelizePathPoints(List<Pt3> pts, GridMeta g, int dilate)
        {
            var cell = g.CellMm;
            var list = new List<PathCell>();
            if (cell <= 0 || pts.Count < 2) return list;

            var set = new HashSet<(int, int, int)>();
            for (int i = 1; i < pts.Count; i++)
            {
                var a = pts[i - 1];
                var b = pts[i];
                double dx = b.X - a.X, dy = b.Y - a.Y, dz = b.Z - a.Z;
                double len = Math.Sqrt(dx * dx + dy * dy + dz * dz);
                int steps = Math.Max(1, (int)(len / (cell * 0.5)));
                for (int sIdx = 0; sIdx <= steps; sIdx++)
                {
                    double tt = (double)sIdx / steps;
                    int ci = (int)Math.Floor((a.X + dx * tt - g.Ox) / cell);
                    int cj = (int)Math.Floor((a.Y + dy * tt - g.Oy) / cell);
                    int ck = (int)Math.Floor((a.Z + dz * tt - g.Oz) / cell);

                    for (int di = -dilate; di <= dilate; di++)
                        for (int dj = -dilate; dj <= dilate; dj++)
                            for (int dk = -dilate; dk <= dilate; dk++)
                            {
                                int ii = ci + di, jj = cj + dj, kk = ck + dk;
                                if (ii < 0 || jj < 0 || kk < 0 || ii >= g.Nx || jj >= g.Ny || kk >= g.Nz) continue;
                                set.Add((ii, jj, kk));
                            }
                }
            }

            foreach (var tp in set)
            {
                list.Add(new PathCell(tp.Item1, tp.Item2, tp.Item3));
            }
            return list;
        }

        private async void BtnRunRouting_Click(object sender, RoutedEventArgs e)
        {
            if (_currentSceneData == null || _db == null)
            {
                MessageBox.Show(this, "프로젝트 데이터를 먼저 로드해 주세요.", "알림", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            var tasksToRoute = DgTasks.ItemsSource as List<TaskInfo>;
            if (tasksToRoute == null || tasksToRoute.Count == 0)
            {
                MessageBox.Show(this, "라우팅 대상 작업이 존재하지 않습니다.", "알림", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            BtnRunRouting.IsEnabled = false;
            GridLoading.Visibility = Visibility.Visible;
            TxtLoading.Text = "자동 설계 경로 탐색 중...";
            TxtStatus.Text = "경로 탐색 연산 기동...";

            ClearAutoRoutingVisuals();
            TxtRouteDetailLog.Text = "자동 설계 탐색 진행 중입니다...";

            bool useFeatures = ChkUseFeatureProfile.IsChecked == true;
            bool useTopK = ChkUseTopKSimilarity.IsChecked == true;
            
            // DgRouteResults 데이터 바인딩 및 대기 상태로 초기 설정
            var results = new System.Collections.ObjectModel.ObservableCollection<RouteResultUI>();
            for (int i = 0; i < tasksToRoute.Count; i++)
            {
                var task = tasksToRoute[i];
                var uiResult = new RouteResultUI
                {
                    Utility = task.Utility ?? "(미정)",
                    Status = "대기",
                    TaskInfo = task
                };
                
                if (useFeatures && !string.IsNullOrEmpty(task.Group) && _featureProfiles.TryGetValue(task.Group, out var profile))
                {
                    if (profile.PreferredRackZs != null)
                    {
                        uiResult.PreferredRackZs.AddRange(profile.PreferredRackZs);
                    }
                    uiResult.GoalDirConstraint = ConvertFaceToGoalDir(profile.PreferredTargetFace);
                    uiResult.HasSpineCorridor = !string.IsNullOrEmpty(profile.TrunkCenterlineJson);
                }
                
                results.Add(uiResult);
            }
            DgRouteResults.ItemsSource = results;
            if (results.Count > 0)
            {
                DgRouteResults.SelectedIndex = 0;
            }

            try
            {
                await Task.Run(() =>
                {
                    double cellMm = _currentSceneData.Grid.CellMm;
                    var g = _currentSceneData.Grid;

                    using var engine = new Routing3DEngine();
                    engine.SetGrid(cellMm, g.Ox, g.Oy, g.Oz, g.Nx, g.Ny, g.Nz);

                    // 1. 장애물 주입
                    foreach (var obs in _currentSceneData.Obstacles)
                    {
                        if (obs.IsPassThrough)
                            engine.AddPassthrough(obs.MinX, obs.MinY, obs.MinZ, obs.MaxX, obs.MaxY, obs.MaxZ);
                        else
                            engine.AddObstacle(obs.MinX, obs.MinY, obs.MinZ, obs.MaxX, obs.MaxY, obs.MaxZ);
                    }

                    // 2. 장비 주입
                    foreach (var eq in _currentSceneData.Equipment)
                    {
                        engine.AddObstacle(eq.MinX, eq.MinY, eq.MinZ, eq.MaxX, eq.MaxY, eq.MaxZ);
                    }

                    // 3. 작업 추가 및 특징점 연계 설정
                    var engineTaskIndices = new List<int>();
                    var featureCorridors = new List<PathCell>();
                    var preferredRackZs = new HashSet<int>();

                    for (int i = 0; i < tasksToRoute.Count; i++)
                    {
                        var task = tasksToRoute[i];
                        int tIdx = engine.AddTask(task.Sx, task.Sy, task.Sz, task.Gx, task.Gy, task.Gz, task.Utility, task.Group);
                        engineTaskIndices.Add(tIdx);

                        if (task.DiameterMm > 0)
                        {
                            engine.SetTaskDiameter(tIdx, task.DiameterMm);
                        }

                        // 특징점 적용
                        if (useFeatures && !string.IsNullOrEmpty(task.Group) && _featureProfiles.TryGetValue(task.Group, out var profile))
                        {
                            // A. 선호 랙 Z고도 캐싱
                            if (profile.PreferredRackZs != null)
                            {
                                foreach (var z in profile.PreferredRackZs)
                                {
                                    int zk = (int)Math.Floor((z - g.Oz) / cellMm);
                                    if (zk >= 0 && zk < g.Nz) preferredRackZs.Add(zk);
                                }
                            }

                            // B. 공용 척추선(Spine) 격자 회랑 복셀화 추출
                            if (!string.IsNullOrEmpty(profile.TrunkCenterlineJson))
                            {
                                var corrCells = BuildFeatureSpineCells(profile.TrunkCenterlineJson, g, 2);
                                featureCorridors.AddRange(corrCells);
                            }

                            // C. 목표 접속면 방향 제약 주입
                            var dir = ConvertFaceToGoalDir(profile.PreferredTargetFace);
                            if (dir != GoalDirection.Any)
                            {
                                engine.SetTaskGoalDirection(tIdx, dir);
                            }
                        }

                        // D. 유사설계 Top-K 회랑 연계 적용
                        if (useTopK && _db != null)
                        {
                            try
                            {
                                // Top-K 유사 설계 검색 실행
                                var searchTask = RoutingAI.Standalone.TopKSearchStandalone.SearchAsync(
                                    db: new RoutingAI.Standalone.DbConfig(
                                        Host: _db.Host,
                                        Port: _db.Port,
                                        Database: _db.Database,
                                        User: _db.User,
                                        Password: _db.Password
                                    ),
                                    processName: "",
                                    equipmentName: task.EquipmentTag ?? "",
                                    utilityGroup: task.Group ?? "",
                                    utility: task.Utility ?? "",
                                    startXyz: (task.Sx, task.Sy, task.Sz),
                                    endXyz: (task.Gx, task.Gy, task.Gz),
                                    k: 3
                                );

                                var (topKResults, _) = searchTask.GetAwaiter().GetResult();

                                foreach (var res in topKResults)
                                {
                                    // 유사 배관의 상세 경로 포인트들을 DB에서 조회
                                    var pathPts = LoadPathPoints(res.RoutePathGuid);
                                    if (pathPts.Count >= 2)
                                    {
                                        // 경로의 꺾임 부분들을 격자 회랑 셀로 변환하여 주입
                                        var pathCorrCells = VoxelizePathPoints(pathPts, g, 2);
                                        featureCorridors.AddRange(pathCorrCells);
                                    }
                                }
                            }
                            catch (Exception ex)
                            {
                                System.Diagnostics.Debug.WriteLine($"[경고] Top-K 유사설계 회랑 생성 실패: {ex.Message}");
                            }
                        }
                    }

                    // w_corridor 및 rackLevels 주입
                    double wCorr = ((useFeatures || useTopK) && featureCorridors.Count > 0) ? cellMm * 0.5 : 0.0;
                    int[]? rackLevels = preferredRackZs.Count > 0 ? preferredRackZs.ToArray() : null;

                    engine.SetParameters(new RoutingParameters
                    {
                        CellMm = cellMm,
                        TurnCostMm = 500.0,
                        ClearanceCostMm = 10.0,
                        CorridorCostMm = wCorr,
                        HeuristicWeight = 2.0,
                        NearGoalHeuristicWeight = 1.0,
                        ClearanceRadiusCells = 2,
                        ClearanceConnectivity = 6,
                        CorridorRadiusCells = 2,
                        RackLevels = rackLevels != null ? new List<int>(rackLevels) : new List<int>()
                    });

                    if ((useFeatures || useTopK) && featureCorridors.Count > 0)
                    {
                        engine.SetCorridorCells(featureCorridors);
                    }

                    // 4. 실시간 경로 연산 및 UI 갱신 실행
                    try
                    {
                        engine.RouteMultiProgress("longest", progress =>
                        {
                            Dispatcher.Invoke(() =>
                            {
                                int listIndex = engineTaskIndices.IndexOf(progress.TaskIndex);
                                if (listIndex >= 0 && listIndex < results.Count)
                                {
                                    var uiResult = results[listIndex];
                                    uiResult.Status = progress.Success ? "성공" : "실패";
                                    uiResult.LengthMm = progress.LengthMm;
                                    uiResult.Turns = progress.Turns;
                                    uiResult.FailReason = progress.Success ? "" : engine.GetResult(progress.TaskIndex).Fail.ToString();
                                    uiResult.ExpandedNodes = progress.ExpandedNodes;
                                    uiResult.ElapsedMs = progress.ElapsedMs;

                                    // 성공 시 실시간으로 화면에 튜브 생성
                                    if (progress.Success && progress.Path != null && progress.Path.Count >= 2)
                                    {
                                        var pathPts = new Point3DCollection();
                                        foreach (var cell in progress.Path)
                                        {
                                            double wx = g.Ox + (cell.I + 0.5) * cellMm;
                                            double wy = g.Oy + (cell.J + 0.5) * cellMm;
                                            double wz = g.Oz + (cell.K + 0.5) * cellMm;
                                            pathPts.Add(new Point3D(wx, wy, wz));
                                        }

                                        var brush = new SolidColorBrush(Color.FromRgb(0, 255, 255)); // Cyan
                                        var tube = new TubeVisual3D
                                        {
                                            Path = pathPts,
                                            Diameter = uiResult.TaskInfo!.DiameterMm > 0 ? uiResult.TaskInfo.DiameterMm : 100,
                                            Fill = brush,
                                            IsPathClosed = false,
                                            Visible = ChkShowAutoPipes.IsChecked == true
                                        };
                                        Viewport3D.Children.Add(tube);
                                        _autoPipeVisuals.Add((tube, uiResult.TaskInfo.Group ?? "", uiResult.TaskInfo.Utility ?? ""));
                                        uiResult.Visual = tube;
                                        
                                        // Apply active filter instantly to new tube
                                        UpdateAllVisibilities();
                                    }

                                    // DgRouteResults 실시간 데이터 그리드 셀 갱신
                                    DgRouteResults.Items.Refresh();

                                    // 만약 현재 선택된 경로 결과가 업데이트되었다면 로그 텍스트도 갱신
                                    if (DgRouteResults.SelectedItem == uiResult)
                                    {
                                        TxtRouteDetailLog.Text = BuildRouteDetailLog(uiResult);
                                    }
                                }

                                int done = results.Count(x => x.Status != "대기");
                                int okCount = results.Count(x => x.Status == "성공");
                                TxtRouteResult.Text = $"자동설계 결과 (성공: {okCount}, 진행: {done}/{results.Count})";
                                TxtStatus.Text = $"자동 라우팅 진행 중... ({done}/{results.Count})";
                            });
                        });
                    }
                    catch (Exception ex)
                    {
                        System.Diagnostics.Debug.WriteLine($"RouteMultiProgress 실패: {ex.Message}");
                        throw;
                    }
                });

                // 전체 연산 완료 후 최종 마크업
                int finalOkCount = results.Count(x => x.Status == "성공");
                TxtRouteResult.Text = $"자동설계 결과 (성공: {finalOkCount}/{results.Count})";
                TxtStatus.Text = $"자동 라우팅 완료 - 성공 {finalOkCount}개, 실패 {results.Count - finalOkCount}개";
            }
            catch (Exception ex)
            {
                TxtStatus.Text = $"에러: 경로 탐색 연산 실패 ({ex.Message})";
                MessageBox.Show(this, $"경로 탐색 도중 오류가 발생했습니다:\n{ex.Message}", "에러", MessageBoxButton.OK, MessageBoxImage.Error);
            }

            GridLoading.Visibility = Visibility.Collapsed;
            BtnRunRouting.IsEnabled = true;
        }
    }
}
