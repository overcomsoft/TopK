using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Media3D;
using HelixToolkit.Wpf;
using Npgsql;
using GroupPatternViewer.Models;

namespace GroupPatternViewer
{
    public partial class MainWindow : Window
    {
        private DbConfig? _db;
        private List<GroupPatternModel> _allPatterns = new();
        private readonly List<Visual3D> _projectVisuals = new();
        private readonly List<Visual3D> _groupVisuals = new();
        private SceneData? _currentSceneData;
        private GroupPatternModel? _currentSelectedPattern;
        private List<SegmentDetailRow>? _currentSelectedPipeSegments;
        private bool _isLoaded;
        private TubeVisual3D? _lastClickedTube;
        private Brush? _lastClickedTubeOriginalBrush;
        private readonly Dictionary<Visual3D, object> _visualTags = new();
        private readonly Dictionary<TaskInfo, (SphereVisual3D start, SphereVisual3D end)> _pocVisualMap = new();


        public MainWindow()
        {
            InitializeComponent();
            
            this.Loaded += (s, e) =>
            {
                LoadSettings();
                _isLoaded = true;
            };
        }

        #region DB 설정 저장/로드

        private void LoadSettings()
        {
            try
            {
                string exeDir = AppDomain.CurrentDomain.BaseDirectory;
                string path = Path.Combine(exeDir, "grouppatternviewer.settings.json");
                if (!File.Exists(path))
                {
                    path = "grouppatternviewer.settings.json";
                }

                if (File.Exists(path))
                {
                    var json = File.ReadAllText(path, Encoding.UTF8);
                    using var doc = JsonDocument.Parse(json);
                    if (doc.RootElement.TryGetProperty("db", out var dbProp))
                    {
                        if (dbProp.TryGetProperty("host", out var h)) TxtHost.Text = h.GetString();
                        if (dbProp.TryGetProperty("port", out var p)) TxtPort.Text = p.GetInt32().ToString();
                        if (dbProp.TryGetProperty("database", out var d)) TxtDbname.Text = d.GetString();
                        if (dbProp.TryGetProperty("user", out var u)) TxtUser.Text = u.GetString();
                        if (dbProp.TryGetProperty("password", out var pwd)) TxtPassword.Password = pwd.GetString();
                    }
                }
                else
                {
                    TxtHost.Text = "localhost";
                    TxtPort.Text = "5432";
                    TxtDbname.Text = "DDW_AI_DB";
                    TxtUser.Text = "postgres";
                    TxtPassword.Password = "dinno";
                }
            }
            catch
            {
                // ignore settings loading errors
            }
        }

        private void SaveSettings(DbConfig db)
        {
            try
            {
                string exeDir = AppDomain.CurrentDomain.BaseDirectory;
                string path = Path.Combine(exeDir, "grouppatternviewer.settings.json");
                var settings = new
                {
                    db = new
                    {
                        host = db.Host,
                        port = db.Port,
                        database = db.Database,
                        user = db.User,
                        password = db.Password
                    }
                };
                var json = JsonSerializer.Serialize(settings, new JsonSerializerOptions { WriteIndented = true });
                File.WriteAllText(path, json, Encoding.UTF8);
            }
            catch
            {
                // ignore settings saving errors
            }
        }

        private void DbField_Changed(object sender, RoutedEventArgs e)
        {
            if (!_isLoaded) return;
            TxtConnStatus.Text = "● 설정 변경됨 — 연결 필요";
            TxtConnStatus.Foreground = Brushes.DarkOrange;
            BtnRefresh.IsEnabled = false;
        }

        #endregion

        #region DB 연결 및 프로젝트 로드

        private async void BtnConnect_Click(object sender, RoutedEventArgs e)
        {
            string host = TxtHost.Text?.Trim() ?? "";
            string portStr = TxtPort.Text?.Trim() ?? "";
            string dbname = TxtDbname.Text?.Trim() ?? "";
            string user = TxtUser.Text?.Trim() ?? "";
            string password = TxtPassword.Password ?? "";

            if (!int.TryParse(portStr, out int port) || port <= 0 || port > 65535)
            {
                MessageBox.Show(this, "Port 값이 올바르지 않습니다 (1~65535)", "오류", MessageBoxButton.OK, MessageBoxImage.Error);
                return;
            }
            if (string.IsNullOrEmpty(host) || string.IsNullOrEmpty(dbname) || string.IsNullOrEmpty(user))
            {
                MessageBox.Show(this, "Host/Database/User는 비어있을 수 없습니다", "오류", MessageBoxButton.OK, MessageBoxImage.Error);
                return;
            }

            var candidate = new DbConfig
            {
                Host = host,
                Port = port,
                Database = dbname,
                User = user,
                Password = password
            };

            BtnConnect.IsEnabled = false;
            TxtConnStatus.Text = "● 연결 및 프로젝트 로드 중...";
            TxtConnStatus.Foreground = Brushes.Orange;

            try
            {
                List<ProjectInfo>? projects = null;
                await Task.Run(() =>
                {
                    using var conn = new NpgsqlConnection(candidate.ConnectionString);
                    conn.Open();
                    projects = ObstacleDbLoader.ListProjects(candidate);
                });

                _db = candidate;
                SaveSettings(_db);

                TxtConnStatus.Text = "● 연결 완료";
                TxtConnStatus.Foreground = new SolidColorBrush(Color.FromRgb(46, 139, 87));
                BtnRefresh.IsEnabled = true;

                // 콤보박스 바인딩
                CmbProject.SelectionChanged -= CmbProject_SelectionChanged;
                CmbProject.ItemsSource = projects;
                CmbProject.SelectionChanged += CmbProject_SelectionChanged;

                if (projects != null && projects.Count > 0)
                {
                    CmbProject.SelectedIndex = 0; // CmbProject_SelectionChanged 트리거
                }
                else
                {
                    MessageBox.Show(this, "프로젝트(TB_SPACE_GROUP_INFO) 목록이 비어 있습니다.", "정보", MessageBoxButton.OK, MessageBoxImage.Information);
                }
            }
            catch (Exception ex)
            {
                TxtConnStatus.Text = "● 연결 실패";
                TxtConnStatus.Foreground = Brushes.Crimson;
                MessageBox.Show(this, $"DB 연결 또는 프로젝트 로드 실패:\n{ex.Message}", "에러", MessageBoxButton.OK, MessageBoxImage.Error);
            }
            finally
            {
                BtnConnect.IsEnabled = true;
            }
        }

        private async void CmbProject_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (CmbProject.SelectedItem is not ProjectInfo selectedProj || _db == null) return;

            GridLoading.Visibility = Visibility.Visible;
            try
            {
                // 0. 기존 선택/패턴 정보 초기화
                _currentSelectedPattern = null;
                _currentSelectedPipeSegments = null;
                ClearGroupVisuals();

                // 1. 바닥 그리드 위치/크기 조정
                double cx = (selectedProj.MinX + selectedProj.MaxX) / 2.0;

                double cy = (selectedProj.MinY + selectedProj.MaxY) / 2.0;
                double cz = selectedProj.MinZ;
                GridLines.Center = new Point3D(cx, cy, cz);
                GridLines.Width = Math.Max(10000, (selectedProj.MaxX - selectedProj.MinX) * 1.5);
                GridLines.Length = Math.Max(10000, (selectedProj.MaxY - selectedProj.MinY) * 1.5);

                // 2. 프로젝트 AABB 스코프 기하 정보(장애물, 설비 등) 로드
                var sceneData = await Task.Run(() => ObstacleDbLoader.LoadScene(_db, selectedProj));
                _currentSceneData = sceneData;

                // 3. 기하 시각화 렌더링
                RenderProjectScene(sceneData);

                // 4. 해당 프로젝트의 그룹배관 패턴 리스트만 필터링 로드
                await LoadPatternsForProjectAsync(selectedProj.GroupName);

                // 5. 3D 카메라 줌 맞춤
                Viewport3D.ZoomExtents(500);
            }
            catch (Exception ex)
            {
                MessageBox.Show(this, $"프로젝트 데이터 로딩 실패:\n{ex.Message}", "에러", MessageBoxButton.OK, MessageBoxImage.Error);
            }
            finally
            {
                GridLoading.Visibility = Visibility.Collapsed;
            }
        }

        private async Task LoadPatternsForProjectAsync(string groupName)
        {
            if (_db == null) return;

            try
            {
                var list = new List<GroupPatternModel>();
                await Task.Run(async () =>
                {
                    await using var conn = new NpgsqlConnection(_db.ConnectionString);
                    await conn.OpenAsync();

                    var query = @"
                        SELECT 
                            pat.""GROUP_ID"", 
                            pat.""TAG_GROUP_NM"", 
                            pat.""UTILITY"", 
                            pat.""N_MEMBERS"", 
                            pat.""AVG_SIMILARITY"", 
                            pat.""TRUNK_Z"", 
                            pat.""TRUNK_XY_SPREAD"", 
                            pat.""PITCH_MM"", 
                            pat.""N_ORTHO_BENDS"", 
                            pat.""MEMBER_GUIDS""::text AS ""MEMBER_GUIDS_TXT"", 
                            pat.""PATTERN_SEQ"", 
                            pat.""SECTION_BOUNDS""::text AS ""SECTION_BOUNDS_TXT"",
                            (
                                SELECT rp.""UTILITY_GROUP"" 
                                FROM ""TB_ROUTE_PATH"" rp 
                                WHERE rp.""ROUTE_PATH_GUID"" = (pat.""MEMBER_GUIDS""->>0) 
                                LIMIT 1
                            ) AS ""UTILITY_GROUP"",
                            (
                                SELECT string_agg(DISTINCT val, ', ')
                                FROM (
                                    SELECT DISTINCT unnest(ARRAY[rp.""EQUIPMENT_NAME"", rp.""TARGET_OWNER_NAME""]) AS val
                                    FROM ""TB_ROUTE_PATH"" rp
                                    WHERE rp.""ROUTE_PATH_GUID"" = ANY(
                                        ARRAY(
                                            SELECT jsonb_array_elements_text(pat.""MEMBER_GUIDS"")
                                        )
                                    )
                                ) sub
                                WHERE val IS NOT NULL AND val <> ''
                            ) AS ""POC_LIST""
                        FROM ""TB_ROUTE_GROUP_PATTERN"" pat
                        WHERE pat.""TAG_GROUP_NM"" = @groupName
                        ORDER BY pat.""UTILITY"";";

                    await using (var cmd = new NpgsqlCommand(query, conn))
                    {
                        cmd.Parameters.AddWithValue("groupName", groupName);
                        await using var reader = await cmd.ExecuteReaderAsync();
                        while (await reader.ReadAsync())
                        {
                            list.Add(new GroupPatternModel
                            {
                                GroupId = reader.IsDBNull(0) ? "" : reader.GetString(0),
                                TagGroupNm = reader.IsDBNull(1) ? "" : reader.GetString(1),
                                Utility = reader.IsDBNull(2) ? "" : reader.GetString(2),
                                NMembers = reader.IsDBNull(3) ? 0 : reader.GetInt32(3),
                                AvgSimilarity = reader.IsDBNull(4) ? 0.0 : reader.GetDouble(4),
                                TrunkZ = reader.IsDBNull(5) ? 0.0 : reader.GetDouble(5),
                                TrunkXySpread = reader.IsDBNull(6) ? 0.0 : reader.GetDouble(6),
                                PitchMm = reader.IsDBNull(7) ? 0.0 : reader.GetDouble(7),
                                NOrthoBends = reader.IsDBNull(8) ? 0 : reader.GetInt32(8),
                                MemberGuidsJson = reader.IsDBNull(9) ? "[]" : reader.GetString(9),
                                PatternSeq = reader.IsDBNull(10) ? "" : reader.GetString(10),
                                SectionBoundsJson = reader.IsDBNull(11) ? "[]" : reader.GetString(11),
                                UtilityGroup = reader.IsDBNull(12) ? "" : reader.GetString(12),
                                PocList = reader.IsDBNull(13) ? "" : reader.GetString(13)
                            });
                        }
                    }
                });

                _allPatterns = list;

                // 유틸리티 그룹 종류 별 필터 채우기
                var groups = _allPatterns.Select(p => p.UtilityGroup).Where(g => !string.IsNullOrEmpty(g)).Distinct().OrderBy(g => g).ToList();
                CmbUtilityGroup.SelectionChanged -= CmbUtilityGroup_SelectionChanged;
                CmbUtilityGroup.Items.Clear();
                CmbUtilityGroup.Items.Add(new ComboBoxItem { Content = "전체", IsSelected = true, Foreground = System.Windows.Media.Brushes.Black });
                foreach (var g in groups)
                {
                    CmbUtilityGroup.Items.Add(new ComboBoxItem { Content = g, Foreground = System.Windows.Media.Brushes.Black });
                }
                CmbUtilityGroup.SelectedIndex = 0;
                CmbUtilityGroup.SelectionChanged += CmbUtilityGroup_SelectionChanged;

                // 유틸리티 종류 별 필터 리프레시
                UpdateUtilityComboBox();

                ApplyFilters();
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"LoadPatternsForProjectAsync Error: {ex.Message}");
                throw;
            }
        }

        private void CmbUtilityGroup_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (!_isLoaded) return;
            UpdateUtilityComboBox();
            ApplyFilters();
        }

        private void UpdateUtilityComboBox()
        {
            string selectedGroup = "";
            if (CmbUtilityGroup.SelectedItem is ComboBoxItem cbiGroup)
            {
                selectedGroup = cbiGroup.Content.ToString() == "전체" ? "" : cbiGroup.Content.ToString() ?? "";
            }

            var utilities = _allPatterns
                .Where(p => string.IsNullOrEmpty(selectedGroup) || p.UtilityGroup.Equals(selectedGroup, StringComparison.OrdinalIgnoreCase))
                .Select(p => p.Utility)
                .Distinct()
                .OrderBy(u => u)
                .ToList();

            CmbUtility.SelectionChanged -= FilterFields_Changed;
            CmbUtility.Items.Clear();
            CmbUtility.Items.Add(new ComboBoxItem { Content = "전체", IsSelected = true, Foreground = System.Windows.Media.Brushes.Black });
            foreach (var util in utilities)
            {
                CmbUtility.Items.Add(new ComboBoxItem { Content = util, Foreground = System.Windows.Media.Brushes.Black });
            }
            CmbUtility.SelectedIndex = 0;
            CmbUtility.SelectionChanged += FilterFields_Changed;
        }

        private void ApplyFilters()
        {
            var searchText = TxtSearchGroup.Text.Trim().ToLower();
            
            string selectedGroup = "";
            if (CmbUtilityGroup.SelectedItem is ComboBoxItem cbiGroup)
            {
                selectedGroup = cbiGroup.Content.ToString() == "전체" ? "" : cbiGroup.Content.ToString() ?? "";
            }

            string selectedUtil = "";
            if (CmbUtility.SelectedItem is ComboBoxItem cbiUtil)
            {
                selectedUtil = cbiUtil.Content.ToString() == "전체" ? "" : cbiUtil.Content.ToString() ?? "";
            }

            var filtered = _allPatterns.Where(p =>
                (string.IsNullOrEmpty(searchText) || p.TagGroupNm.ToLower().Contains(searchText)) &&
                (string.IsNullOrEmpty(selectedGroup) || p.UtilityGroup.Equals(selectedGroup, StringComparison.OrdinalIgnoreCase)) &&
                (string.IsNullOrEmpty(selectedUtil) || p.Utility.Equals(selectedUtil, StringComparison.OrdinalIgnoreCase))
            ).ToList();

            DgGroups.ItemsSource = filtered;
            TxtGroupCount.Text = $"검색된 그룹: {filtered.Count}개";
        }


        private void FilterFields_Changed(object sender, RoutedEventArgs e)
        {
            if (_isLoaded)
            {
                ApplyFilters();
            }
        }

        private void BtnRefresh_Click(object sender, RoutedEventArgs e)
        {
            CmbProject_SelectionChanged(CmbProject, null!);
        }

        #endregion

        #region 3D 시각화

        private void RenderProjectScene(SceneData scene)
        {
            ClearProjectVisuals();
            if (scene == null) return;

            // 1. 장애물 그리기 (BIM Obstacles)
            if (ChkShowObstacles.IsChecked == true)
            {
                if (scene.Obstacles.Count > 0)
                {
                    var mb = new MeshBuilder(false, false);
                    var mbPass = new MeshBuilder(false, false);
                    foreach (var o in scene.Obstacles)
                    {
                        var center = new Point3D((o.MinX + o.MaxX) / 2.0, (o.MinY + o.MaxY) / 2.0, (o.MinZ + o.MaxZ) / 2.0);
                        if (o.IsPassThrough)
                            mbPass.AddBox(center, o.MaxX - o.MinX, o.MaxY - o.MinY, o.MaxZ - o.MinZ);
                        else
                            mb.AddBox(center, o.MaxX - o.MinX, o.MaxY - o.MinY, o.MaxZ - o.MinZ);
                    }

                    if (mb.Positions.Count > 0)
                    {
                        var model = Geometry(mb, Color.FromRgb(150, 150, 150), 30);
                        var visual = new ModelVisual3D { Content = model };
                        Viewport3D.Children.Add(visual);
                        _projectVisuals.Add(visual);
                    }
                    if (mbPass.Positions.Count > 0)
                    {
                        var model = Geometry(mbPass, Color.FromRgb(90, 200, 160), 20);
                        var visual = new ModelVisual3D { Content = model };
                        Viewport3D.Children.Add(visual);
                        _projectVisuals.Add(visual);
                    }
                }
            }

            // 2. 장비 그리기 (Equipment)
            if (ChkShowEquipment.IsChecked == true)
            {
                if (scene.Equipment.Count > 0)
                {
                    var mbMain = new MeshBuilder(false, false);
                    var mbSub = new MeshBuilder(false, false);
                    foreach (var eq in scene.Equipment)
                    {
                        var center = new Point3D((eq.MinX + eq.MaxX) / 2.0, (eq.MinY + eq.MaxY) / 2.0, (eq.MinZ + eq.MaxZ) / 2.0);
                        if (eq.IsMain)
                            mbMain.AddBox(center, eq.MaxX - eq.MinX, eq.MaxY - eq.MinY, eq.MaxZ - eq.MinZ);
                        else
                            mbSub.AddBox(center, eq.MaxX - eq.MinX, eq.MaxY - eq.MinY, eq.MaxZ - eq.MinZ);
                    }

                    if (mbMain.Positions.Count > 0)
                    {
                        var model = Geometry(mbMain, Color.FromRgb(255, 140, 0), 100);
                        var visual = new ModelVisual3D { Content = model };
                        Viewport3D.Children.Add(visual);
                        _projectVisuals.Add(visual);
                    }
                    if (mbSub.Positions.Count > 0)
                    {
                        var model = Geometry(mbSub, Color.FromRgb(255, 190, 90), 60);
                        var visual = new ModelVisual3D { Content = model };
                        Viewport3D.Children.Add(visual);
                        _projectVisuals.Add(visual);
                    }
                }
            }

            // 3. 덕트 & 레터럴 그리기 (Ducts & Laterals)
            if (ChkShowDucts.IsChecked == true)
            {
                if (scene.DuctsLaterals.Count > 0)
                {
                    var mbLat = new MeshBuilder(false, false);
                    var mbDuct = new MeshBuilder(false, false);
                    const double MinThick = 40;
                    foreach (var d in scene.DuctsLaterals)
                    {
                        var center = new Point3D((d.MinX + d.MaxX) / 2.0, (d.MinY + d.MaxY) / 2.0, (d.MinZ + d.MaxZ) / 2.0);
                        double sx = Math.Max(d.MaxX - d.MinX, MinThick);
                        double sy = Math.Max(d.MaxY - d.MinY, MinThick);
                        double sz = Math.Max(d.MaxZ - d.MinZ, MinThick);
                        if (d.IsLateral)
                            mbLat.AddBox(center, sx, sy, sz);
                        else
                            mbDuct.AddBox(center, sx, sy, sz);
                    }

                    if (mbLat.Positions.Count > 0)
                    {
                        var model = Geometry(mbLat, Color.FromRgb(90, 210, 130), 80);
                        var visual = new ModelVisual3D { Content = model };
                        Viewport3D.Children.Add(visual);
                        _projectVisuals.Add(visual);
                    }
                    if (mbDuct.Positions.Count > 0)
                    {
                        var model = Geometry(mbDuct, Color.FromRgb(110, 175, 220), 70);
                        var visual = new ModelVisual3D { Content = model };
                        Viewport3D.Children.Add(visual);
                        _projectVisuals.Add(visual);
                    }
                }
            }

            // 4. 기존 설계배관 (Existing Background Pipes)
            if (ChkShowBackgroundPipes.IsChecked == true)
            {
                if (scene.ExistingPipes.Count > 0)
                {
                    foreach (var pipe in scene.ExistingPipes)
                    {
                        if (pipe.Points.Count < 2) continue;
                        var pts = pipe.Points.Select(p => new Point3D(p.X, p.Y, p.Z)).ToList();
                        var tube = new TubeVisual3D
                        {
                            Path = new Point3DCollection(pts),
                            Diameter = pipe.DiameterMm > 0 ? pipe.DiameterMm : 80.0,
                            ThetaDiv = 10,
                            Fill = GetBackgroundPipeBrush(pipe.Group ?? "", pipe.Utility ?? "")
                        };
                        Viewport3D.Children.Add(tube);
                        _projectVisuals.Add(tube);
                        _visualTags[tube] = pipe; // 모든 분할 튜브에 원본 pipe 객체 매핑
                    }
                }
            }

            // 5. 시작/종단 PoC 구 그리기
            if (scene.Tasks.Count > 0)
            {
                foreach (var t in scene.Tasks)
                {
                    double baseDia = t.DiameterMm > 0 ? t.DiameterMm : 80.0;
                    double defaultRadius = baseDia / 2.0 + 10.0;

                    var startSphere = new SphereVisual3D
                    {
                        Center = new Point3D(t.Sx, t.Sy, t.Sz),
                        Radius = defaultRadius,
                        Fill = Brushes.Red
                    };
                    var endSphere = new SphereVisual3D
                    {
                        Center = new Point3D(t.Gx, t.Gy, t.Gz),
                        Radius = defaultRadius,
                        Fill = Brushes.Blue
                    };

                    Viewport3D.Children.Add(startSphere);
                    Viewport3D.Children.Add(endSphere);
                    _projectVisuals.Add(startSphere);
                    _projectVisuals.Add(endSphere);

                    _pocVisualMap[t] = (startSphere, endSphere);
                }
            }

            UpdatePocSpheresHighlight();
        }

        private void UpdatePocSpheresHighlight()
        {
            // 1. 전체 리셋
            foreach (var pair in _pocVisualMap)
            {
                var t = pair.Key;
                double baseDia = t.DiameterMm > 0 ? t.DiameterMm : 80.0;
                double defaultRadius = baseDia / 2.0 + 10.0;

                pair.Value.start.Radius = defaultRadius;
                pair.Value.end.Radius = defaultRadius;
            }

            // 2. 선택된 패턴이 있다면 확대
            if (_currentSelectedPattern != null && _currentSceneData != null)
            {
                List<string>? memberGuids = null;
                try
                {
                    memberGuids = JsonSerializer.Deserialize<List<string>>(_currentSelectedPattern.MemberGuidsJson);
                }
                catch { }

                if (memberGuids != null)
                {
                    var guidSet = new HashSet<string>(memberGuids, StringComparer.OrdinalIgnoreCase);
                    foreach (var pair in _pocVisualMap)
                    {
                        var t = pair.Key;
                        if (t.RoutePathGuid != null && guidSet.Contains(t.RoutePathGuid))
                        {
                            double baseDia = t.DiameterMm > 0 ? t.DiameterMm : 80.0;
                            double highlightRadius = baseDia / 2.0 + 45.0;

                            pair.Value.start.Radius = highlightRadius;
                            pair.Value.end.Radius = highlightRadius;
                        }
                    }
                }
            }
        }


        private async void DgGroups_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (e != null) // 실제 그리드 선택 변경
            {
                if (DgGroups.SelectedItem is not GroupPatternModel selected)
                {
                    _currentSelectedPattern = null;
                    _currentSelectedPipeSegments = null;
                    GridSelectedInfo.Visibility = Visibility.Collapsed;
                    ClearGroupVisuals();
                    UpdatePocSpheresHighlight();
                    return;
                }

                _currentSelectedPattern = selected;
                UpdatePocSpheresHighlight();
                GridSelectedInfo.Visibility = Visibility.Visible;
                TxtSelectedGroup.Text = selected.UtilityGroup;
                TxtSelectedUtil.Text = selected.Utility;
                TxtSelectedId.Text = selected.GroupId;
                TxtSelectedSim.Text = $"{selected.AvgSimilarity:F2}";
                TxtSelectedBends.Text = $"{selected.NOrthoBends}";


                List<string>? memberGuids = null;
                try
                {
                    memberGuids = JsonSerializer.Deserialize<List<string>>(selected.MemberGuidsJson);
                    TxtSelectedGuids.Text = memberGuids != null ? string.Join(",\n", memberGuids.Select(g => g.Length > 8 ? g.Substring(0, 8) + "..." : g)) : "[]";
                }
                catch
                {
                    TxtSelectedGuids.Text = "파싱 실패";
                }

                // 기존 패턴 비주얼만 지우기 (프로젝트 배경은 유지)
                ClearGroupVisuals();

                if (memberGuids == null || memberGuids.Count == 0 || _db == null)
                {
                    _currentSelectedPipeSegments = null;
                    return;
                }

                GridLoading.Visibility = Visibility.Visible;
                try
                {
                    // 1. 배관 3D 데이터 로드 (비동기)
                    var pipeSegments = await Task.Run(() => LoadPipeSegments(memberGuids));
                    _currentSelectedPipeSegments = pipeSegments;

                    // 2. 3D 드로잉
                    RenderGroupScene(_currentSelectedPattern, _currentSelectedPipeSegments);

                    // 3. 카메라 자동 포커스 (선택이 실제로 바뀌었을 때만 줌 수행)
                    if (ChkShowPipes.IsChecked == true || ChkShowBoxes.IsChecked == true)
                    {
                        Viewport3D.ZoomExtents(500); // 500ms 애니메이션
                    }
                }
                catch (Exception ex)
                {
                    MessageBox.Show(this, $"3D 렌더링 실패:\n{ex.Message}", "에러", MessageBoxButton.OK, MessageBoxImage.Error);
                }
                finally
                {
                    GridLoading.Visibility = Visibility.Collapsed;
                }
            }
            else // e == null: 체크박스 변경 등으로 리렌더링만 필요 시
            {
                if (_currentSelectedPattern == null || _currentSelectedPipeSegments == null) return;

                ClearGroupVisuals();
                RenderGroupScene(_currentSelectedPattern, _currentSelectedPipeSegments);
            }
        }


        private List<SegmentDetailRow> LoadPipeSegments(List<string> guids)
        {
            var results = new List<SegmentDetailRow>();
            if (_db == null) return results;

            try
            {
                using var conn = new NpgsqlConnection(_db.ConnectionString);
                conn.Open();

                var query = @"
                    SELECT 
                        rp.""ROUTE_PATH_GUID"",
                        sd.""FROM_POSX"", sd.""FROM_POSY"", sd.""FROM_POSZ"",
                        sd.""TO_POSX"",   sd.""TO_POSY"",   sd.""TO_POSZ"",
                        rs.""ORDER"" AS seg_order,
                        sd.""ORDER"" AS detail_order,
                        rp.""SOURCE_SIZE""
                    FROM ""TB_ROUTE_PATH"" rp
                    JOIN ""TB_ROUTE_SEGMENTS"" rs ON rp.""ROUTE_PATH_GUID"" = rs.""ROUTE_PATH_GUID""
                    JOIN ""TB_ROUTE_SEGMENT_DETAIL"" sd ON rs.""SEGMENT_GUID"" = sd.""SEGMENT_GUID""
                    WHERE rp.""ROUTE_PATH_GUID"" = ANY(@guids)
                    ORDER BY rp.""ROUTE_PATH_GUID"", rs.""ORDER"", sd.""ORDER"";";

                using var cmd = new NpgsqlCommand(query, conn);
                cmd.Parameters.AddWithValue("guids", guids.ToArray());

                using var reader = cmd.ExecuteReader();
                while (reader.Read())
                {
                    string? sizeStr = reader.IsDBNull(9) ? null : reader.GetString(9);
                    double diameter = Models.ObstacleDbLoader.ParsePipeSizeMm(sizeStr);

                    results.Add(new SegmentDetailRow
                    {
                        RoutePathGuid = reader.IsDBNull(0) ? "" : reader.GetString(0).Trim(),
                        FromX = reader.IsDBNull(1) ? 0.0 : Convert.ToDouble(reader.GetValue(1)),
                        FromY = reader.IsDBNull(2) ? 0.0 : Convert.ToDouble(reader.GetValue(2)),
                        FromZ = reader.IsDBNull(3) ? 0.0 : Convert.ToDouble(reader.GetValue(3)),
                        ToX = reader.IsDBNull(4) ? 0.0 : Convert.ToDouble(reader.GetValue(4)),
                        ToY = reader.IsDBNull(5) ? 0.0 : Convert.ToDouble(reader.GetValue(5)),
                        ToZ = reader.IsDBNull(6) ? 0.0 : Convert.ToDouble(reader.GetValue(6)),
                        SegOrder = reader.IsDBNull(7) ? 0 : reader.GetInt32(7),
                        DetailOrder = reader.IsDBNull(8) ? 0 : reader.GetInt32(8),
                        DiameterMm = diameter
                    });
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"LoadPipeSegments Error: {ex.Message}");
                throw;
            }
            return results;
        }

        private void RenderGroupScene(GroupPatternModel selected, List<SegmentDetailRow> segmentRows)
        {
            // 1. 배관 그리기
            if (ChkShowPipes.IsChecked == true && segmentRows.Count > 0)
            {
                var grouped = segmentRows.GroupBy(r => r.RoutePathGuid);
                int index = 0;
                foreach (var g in grouped)
                {
                    var sortedRows = g.OrderBy(r => r.SegOrder).ThenBy(r => r.DetailOrder).ToList();
                    
                    var currentPath = new List<Point3D>();
                    var paths = new List<List<Point3D>>();

                    foreach (var row in sortedRows)
                    {
                        var ptFrom = new Point3D(row.FromX, row.FromY, row.FromZ);
                        var ptTo = new Point3D(row.ToX, row.ToY, row.ToZ);

                        // 1. 시작점과 끝점이 같은 0.0mm 길이의 더미 세그먼트는 제외
                        if ((ptFrom - ptTo).LengthSquared < 1.0) continue;

                        if (currentPath.Count == 0)
                        {
                            currentPath.Add(ptFrom);
                            currentPath.Add(ptTo);
                        }
                        else
                        {
                            double dist = (currentPath[^1] - ptFrom).Length;
                            if (dist < 10.0)
                            {
                                if ((currentPath[^1] - ptTo).LengthSquared >= 1.0)
                                {
                                    currentPath.Add(ptTo);
                                }
                            }
                            else
                            {
                                paths.Add(currentPath);
                                currentPath = new List<Point3D> { ptFrom, ptTo };
                            }
                        }
                    }
                    if (currentPath.Count >= 2)
                    {
                        paths.Add(currentPath);
                    }

                    var brush = GetGroupPipeBrush(selected.UtilityGroup, selected.Utility, index);

                    var firstRow = sortedRows.FirstOrDefault();
                    double baseDiameter = (firstRow != null) ? firstRow.DiameterMm : 0.0;
                    if (baseDiameter <= 0.0) baseDiameter = 40.0;
                    double visualDiameter = baseDiameter + 15.0; // 실제 관경보다 15mm 크게 설정

                    foreach (var path in paths)
                    {
                        var tube = new TubeVisual3D
                        {
                            Path = new Point3DCollection(path),
                            Diameter = visualDiameter,
                            ThetaDiv = 12,
                            Fill = brush
                        };
                        Viewport3D.Children.Add(tube);
                        _groupVisuals.Add(tube);
                        _visualTags[tube] = new GroupPipeTag { Pattern = selected, RoutePathGuid = g.Key, DiameterMm = visualDiameter };
                    }
                    index++;
                }

                // 1.5. 3D 텍스트 라벨 그리기 (그룹 구분을 위한 빌보드 텍스트)
                double sumX = 0, sumY = 0, sumZ = 0;
                int count = 0;
                foreach (var row in segmentRows)
                {
                    sumX += row.FromX + row.ToX;
                    sumY += row.FromY + row.ToY;
                    sumZ += row.FromZ + row.ToZ;
                    count += 2;
                }
                if (count > 0)
                {
                    var center = new Point3D(sumX / count, sumY / count, sumZ / count + 1500);
                    var labelVisual = new BillboardTextVisual3D
                    {
                        Position = center,
                        Text = $"[{selected.UtilityGroup}] {selected.TagGroupNm} - {selected.Utility}",
                        Foreground = Brushes.White,
                        Background = new SolidColorBrush(Color.FromArgb(200, 45, 45, 48)),
                        FontSize = 13,
                        Padding = new Thickness(6, 4, 6, 4),
                        HorizontalAlignment = HorizontalAlignment.Center,
                        VerticalAlignment = VerticalAlignment.Center
                    };
                    Viewport3D.Children.Add(labelVisual);
                    _groupVisuals.Add(labelVisual);
                }
            }

            // 2. 구간 Boundary Box 그리기
            if (ChkShowBoxes.IsChecked == true && !string.IsNullOrEmpty(selected.SectionBoundsJson))
            {
                try
                {
                    var sections = JsonSerializer.Deserialize<List<SectionBound>>(
                        selected.SectionBoundsJson, 
                        new JsonSerializerOptions { PropertyNameCaseInsensitive = true }
                    );

                    if (sections != null)
                    {
                        foreach (var sec in sections)
                        {
                            if (sec.Min.Count < 3 || sec.Max.Count < 3) continue;

                            double minX = sec.Min[0];
                            double minY = sec.Min[1];
                            double minZ = sec.Min[2];
                            
                            double maxX = sec.Max[0];
                            double maxY = sec.Max[1];
                            double maxZ = sec.Max[2];

                            double dx = Math.Max(50.0, maxX - minX);
                            double dy = Math.Max(50.0, maxY - minY);
                            double dz = Math.Max(50.0, maxZ - minZ);

                            var center = new Point3D(
                                (minX + maxX) / 2.0,
                                (minY + maxY) / 2.0,
                                (minZ + maxZ) / 2.0
                            );

                            var boxBrush = GetSectionBrush(sec.Type);

                            var box = new BoxVisual3D
                            {
                                Center = center,
                                Length = dx,
                                Width = dy,
                                Height = dz,
                                Fill = boxBrush
                            };
                            
                            Viewport3D.Children.Add(box);
                            _groupVisuals.Add(box);
                        }
                    }
                }
                catch (Exception ex)
                {
                    System.Diagnostics.Debug.WriteLine($"Section bounds rendering failed: {ex.Message}");
                }
            }
        }

        private Color GetUtilityBaseColor(string utilityGroup, string utility, int index)
        {
            string cleanGroup = (utilityGroup ?? "").Trim().ToUpper();
            string cleanUtil = (utility ?? "").Trim().ToUpper();
            Color baseColor;

            if (cleanGroup.Contains("TOXIC"))
                baseColor = Color.FromRgb(231, 76, 60); // Vivid Red
            else if (cleanGroup.Contains("CHEMICAL"))
                baseColor = Color.FromRgb(155, 89, 182); // Bright Purple
            else if (cleanGroup.Contains("GAS"))
                baseColor = Color.FromRgb(46, 204, 113); // Emerald Green
            else if (cleanGroup.Contains("UPW"))
                baseColor = Color.FromRgb(26, 188, 156); // Turquoise/Teal
            else if (cleanGroup.Contains("EXHAUST"))
                baseColor = Color.FromRgb(241, 196, 15); // Sun Yellow
            else if (cleanGroup.Contains("VACCUM") || cleanGroup.Contains("VACUUM"))
                baseColor = Color.FromRgb(127, 140, 141); // Slate Gray
            else if (cleanGroup.Contains("WASTE"))
                baseColor = Color.FromRgb(211, 84, 0); // Rust Orange
            else if (cleanGroup.Contains("WATER") || cleanUtil.Contains("PCWS") || cleanUtil.Contains("PCWR") || cleanUtil.Contains("LPR"))
                baseColor = Color.FromRgb(52, 152, 219); // Ocean Blue
            else
            {
                // Fallback based on index
                var colors = new Color[]
                {
                    Color.FromRgb(232, 67, 147), // Magenta
                    Color.FromRgb(255, 127, 80),  // Coral
                    Color.FromRgb(0, 206, 209),   // Dark Turquoise
                    Color.FromRgb(70, 130, 180)   // Steel Blue
                };
                baseColor = colors[index % colors.Length];
            }

            if (index > 0)
            {
                byte r = (byte)Math.Max(0, Math.Min(255, baseColor.R + (index * 25) % 80 - 40));
                byte g = (byte)Math.Max(0, Math.Min(255, baseColor.G + (index * 25) % 80 - 40));
                byte b = (byte)Math.Max(0, Math.Min(255, baseColor.B + (index * 25) % 80 - 40));
                baseColor = Color.FromRgb(r, g, b);
            }

            return baseColor;
        }

        private Brush GetBackgroundPipeBrush(string utilityGroup, string utility)
        {
            var baseColor = GetUtilityBaseColor(utilityGroup, utility, 0);
            var brush = new SolidColorBrush(baseColor);
            brush.Opacity = 0.35; // 기존 설계배관: 옅고 투명하게 유틸리티 컬러 표현
            brush.Freeze();
            return brush;
        }

        private Brush GetGroupPipeBrush(string utilityGroup, string utility, int index)
        {
            var baseColor = GetUtilityBaseColor(utilityGroup, utility, index);
            
            // 기존배관 대비 좀 더 진하고 어두운 톤으로 명도 조정 (0.6배)
            byte r = (byte)(baseColor.R * 0.6);
            byte g = (byte)(baseColor.G * 0.6);
            byte b = (byte)(baseColor.B * 0.6);
            var darkColor = Color.FromRgb(r, g, b);

            var brush = new SolidColorBrush(darkColor);
            brush.Opacity = 0.85; // 그룹배관: 좀 더 뚜렷하고 진하게 표현
            brush.Freeze();
            return brush;
        }


        private Brush GetSectionBrush(string type)
        {
            Color c = type.ToUpper().Trim() switch
            {
                "V" => Color.FromArgb(50, 46, 204, 113),  // Green for Vertical
                "H" => Color.FromArgb(50, 52, 152, 219),  // Blue for Horizontal
                "D" => Color.FromArgb(50, 230, 126, 34),  // Orange for Diagonal
                _   => Color.FromArgb(40, 149, 165, 166)
            };

            var brush = new SolidColorBrush(c);
            brush.Freeze();
            return brush;
        }

        private static GeometryModel3D Geometry(MeshBuilder mb, Color color, byte alpha)
        {
            var mat = MaterialFor(color, alpha);
            return new GeometryModel3D { Geometry = mb.ToMesh(), Material = mat, BackMaterial = mat };
        }

        private static Material MaterialFor(Color color, byte alpha)
        {
            var c = Color.FromArgb(alpha, color.R, color.G, color.B);
            return new DiffuseMaterial(new SolidColorBrush(c));
        }

        private void ClearProjectVisuals()
        {
            foreach (var visual in _projectVisuals)
            {
                Viewport3D.Children.Remove(visual);
                _visualTags.Remove(visual);
            }
            _projectVisuals.Clear();
            _pocVisualMap.Clear();
            ResetClickedPipeInfo();
        }

        private void ClearGroupVisuals()
        {
            foreach (var visual in _groupVisuals)
            {
                Viewport3D.Children.Remove(visual);
                _visualTags.Remove(visual);
            }
            _groupVisuals.Clear();
            ResetClickedPipeInfo();
        }

        #endregion

        #region UI 핸들러

        private void Option_Checked(object sender, RoutedEventArgs e)
        {
            if (!_isLoaded) return;

            if (sender == ChkShowGrid)
            {
                if (ChkShowGrid.IsChecked == true)
                {
                    if (!Viewport3D.Children.Contains(GridLines))
                        Viewport3D.Children.Add(GridLines);
                }
                else
                {
                    Viewport3D.Children.Remove(GridLines);
                }
                return;
            }

            if (sender == ChkShowObstacles || sender == ChkShowEquipment || sender == ChkShowDucts || sender == ChkShowBackgroundPipes)
            {
                if (_currentSceneData != null)
                {
                    RenderProjectScene(_currentSceneData);
                }
                return;
            }

            DgGroups_SelectionChanged(DgGroups, null!);
        }


        private void BtnZoomExtents_Click(object sender, RoutedEventArgs e)
        {
            Viewport3D.ZoomExtents(300);
        }

        private void BtnClear3D_Click(object sender, RoutedEventArgs e)
        {
            ClearGroupVisuals();
            ClearProjectVisuals();
        }

        private void ResetClickedPipeInfo()
        {
            if (_lastClickedTube != null && _lastClickedTubeOriginalBrush != null)
            {
                _lastClickedTube.Fill = _lastClickedTubeOriginalBrush;
            }
            _lastClickedTube = null;
            _lastClickedTubeOriginalBrush = null;

            if (GridClickedPipeInfo != null && TxtClickedPipePlaceholder != null)
            {
                GridClickedPipeInfo.Visibility = Visibility.Collapsed;
                TxtClickedPipePlaceholder.Visibility = Visibility.Visible;
            }
        }

        private void Viewport3D_MouseDown(object sender, MouseButtonEventArgs e)
        {
            if (e.ChangedButton != MouseButton.Left) return;

            // 1. 기존 하이라이트 복원
            if (_lastClickedTube != null && _lastClickedTubeOriginalBrush != null)
            {
                _lastClickedTube.Fill = _lastClickedTubeOriginalBrush;
            }
            _lastClickedTube = null;
            _lastClickedTubeOriginalBrush = null;

            // 2. Hit Test 수행
            var hits = Viewport3D.Viewport.FindHits(e.GetPosition(Viewport3D));
            TubeVisual3D? hitTube = null;
            object? tagData = null;

            foreach (var hit in hits)
            {
                if (hit.Visual is TubeVisual3D tube && _visualTags.TryGetValue(tube, out var tag))
                {
                    hitTube = tube;
                    tagData = tag;
                    break;
                }
            }

            // 3. 속성 바인딩 및 하이라이트 적용
            if (hitTube != null && tagData != null)
            {
                _lastClickedTube = hitTube;
                _lastClickedTubeOriginalBrush = hitTube.Fill;

                // 하이라이트 색상 (Gold, 90% 불투명) 적용
                var highlightBrush = new SolidColorBrush(Colors.Gold);
                highlightBrush.Opacity = 0.9;
                highlightBrush.Freeze();
                hitTube.Fill = highlightBrush;

                if (tagData is ExistingPipe pipe)
                {
                    TxtClickedType.Text = "기존배관 (배경)";
                    TxtClickedType.Foreground = new SolidColorBrush(Color.FromRgb(224, 224, 224));
                    TxtClickedGuid.Text = pipe.RoutePathGuid ?? "-";
                    TxtClickedUtil.Text = pipe.Utility ?? "-";
                    TxtClickedGroup.Text = pipe.Group ?? "-";
                    TxtClickedSize.Text = pipe.DiameterMm > 0 ? $"{pipe.DiameterMm:F0} mm" : "-";
                }
                else if (tagData is GroupPipeTag tag)
                {
                    TxtClickedType.Text = "그룹배관 (패턴)";
                    TxtClickedType.Foreground = new SolidColorBrush(Color.FromRgb(255, 185, 0));
                    TxtClickedGuid.Text = tag.RoutePathGuid;
                    TxtClickedUtil.Text = tag.Pattern.Utility;
                    TxtClickedGroup.Text = tag.Pattern.UtilityGroup;
                    TxtClickedSize.Text = $"{tag.DiameterMm - 15.0:F0} mm (외경 버퍼 적용: {tag.DiameterMm:F0} mm)";
                }

                GridClickedPipeInfo.Visibility = Visibility.Visible;
                TxtClickedPipePlaceholder.Visibility = Visibility.Collapsed;
            }
            else
            {
                // 빈 공간 클릭 시 속성 닫기
                GridClickedPipeInfo.Visibility = Visibility.Collapsed;
                TxtClickedPipePlaceholder.Visibility = Visibility.Visible;
            }
        }

        #endregion
    }

    public class GroupPipeTag
    {
        public GroupPatternModel Pattern { get; set; } = null!;
        public string RoutePathGuid { get; set; } = string.Empty;
        public double DiameterMm { get; set; }
    }

    public class UtilityGroupToBrushConverter : System.Windows.Data.IValueConverter
    {
        public object Convert(object value, Type targetType, object parameter, System.Globalization.CultureInfo culture)
        {
            if (value is not string group) return System.Windows.Media.Brushes.LightGray;

            string cleanGroup = group.Trim().ToUpper();

            SolidColorBrush brush;
            if (cleanGroup.Contains("TOXIC"))
                brush = new SolidColorBrush(Color.FromRgb(240, 100, 100)); // 연한 Red
            else if (cleanGroup.Contains("CHEMICAL"))
                brush = new SolidColorBrush(Color.FromRgb(200, 150, 240)); // 연한 Purple
            else if (cleanGroup.Contains("GAS"))
                brush = new SolidColorBrush(Color.FromRgb(100, 240, 140)); // 연한 Green
            else if (cleanGroup.Contains("UPW"))
                brush = new SolidColorBrush(Color.FromRgb(100, 230, 220)); // 연한 Turquoise/Teal
            else if (cleanGroup.Contains("EXHAUST"))
                brush = new SolidColorBrush(Color.FromRgb(250, 230, 100)); // 연한 Yellow
            else if (cleanGroup.Contains("VACCUM") || cleanGroup.Contains("VACUUM"))
                brush = new SolidColorBrush(Color.FromRgb(180, 180, 180)); // 밝은 Slate Gray
            else if (cleanGroup.Contains("WASTE"))
                brush = new SolidColorBrush(Color.FromRgb(250, 160, 80));  // 연한 Orange
            else if (cleanGroup.Contains("WATER") || cleanGroup.Contains("PCW") || cleanGroup.Contains("LPR"))
                brush = new SolidColorBrush(Color.FromRgb(100, 180, 250)); // 연한 Blue
            else
                return System.Windows.Media.Brushes.LightGray;

            brush.Freeze();
            return brush;
        }

        public object ConvertBack(object value, Type targetType, object parameter, System.Globalization.CultureInfo culture)
        {
            throw new NotImplementedException();
        }
    }
}
