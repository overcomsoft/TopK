using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Media3D;
using HelixToolkit.Wpf;
using RubberBandRouting.Engine;

namespace RubberBandRouting.Viewer;

public partial class GroupPatternViewerWindow : Window
{
    private readonly List<ExistingRoutePath> _allPaths;
    private readonly List<SpatialZone> _spatialZones;
    private readonly Dictionary<string, SceneObject> _equipmentByName;
    private readonly Dictionary<string, SceneObject> _ductLateralsByName;
    private readonly Dictionary<string, ExistingRoutePath> _pathByGuid;
    private readonly PostgresConnectionOptions _dbOptions;
    private readonly List<Visual3D> _baseVisuals = new();
    private readonly List<Visual3D> _bundleVisuals = new();
    private readonly List<Visual3D> _zoneVisuals = new();
    private readonly List<Visual3D> _ownerVisuals = new();
    private readonly Dictionary<string, SolidColorBrush> _groupBrushes = new();
    private List<BundleRow> _bundles = new();
    private bool _isUpdatingFilters;

    private const double DefaultMemberDiameterMm = 50.0;
    private const double MemberHighlightMarginMm = 2.0;

    private static readonly Color[] GroupPalette =
    {
        Color.FromRgb(34, 197, 94),
        Color.FromRgb(0, 191, 255),
        Color.FromRgb(186, 85, 211),
        Color.FromRgb(255, 165, 0),
        Color.FromRgb(255, 99, 71),
        Color.FromRgb(250, 204, 21),
        Color.FromRgb(96, 165, 250),
        Color.FromRgb(244, 114, 182),
    };

    internal sealed class BundleRow
    {
        public string GroupId { get; }
        public int NMembers { get; }
        public double PitchMm { get; }
        public double PitchCv { get; }
        public bool IsEqualSpacing { get; }
        public string OffsetAxis { get; }
        public int NOrthoBends { get; }
        public double TrunkLen { get; }
        public double MemberDiameter { get; }
        public SolidColorBrush ColorBrush { get; }
        public List<List<Vec3>> MemberPaths { get; }
        public List<List<Vec3>> TrunkPaths { get; }
        public string Utility { get; }

        public BundleRow(string groupId, int nMembers, double pitchMm, double pitchCv, bool isEqualSpacing,
            string offsetAxis, int nOrthoBends, double trunkLen, double memberDiameter, SolidColorBrush colorBrush,
            List<List<Vec3>> memberPaths, List<List<Vec3>> trunkPaths, string utility)
        {
            GroupId = groupId;
            NMembers = nMembers;
            PitchMm = pitchMm;
            PitchCv = pitchCv;
            IsEqualSpacing = isEqualSpacing;
            OffsetAxis = offsetAxis;
            NOrthoBends = nOrthoBends;
            TrunkLen = trunkLen;
            MemberDiameter = memberDiameter;
            ColorBrush = colorBrush;
            MemberPaths = memberPaths;
            TrunkPaths = trunkPaths;
            Utility = utility;
        }

        public string ShortId => GroupId.Length > 8 ? GroupId[..8] : GroupId;

        public string AxisBadge => OffsetAxis switch
        {
            "HORIZONTAL" => "⬌ 수평",
            "VERTICAL" => "⬍ 수직",
            "MIXED" => "◇ 혼합",
            _ => "? 미상"
        };

        public Brush AxisBadgeBg => OffsetAxis switch
        {
            "HORIZONTAL" => Brushes.Orange,
            "VERTICAL" => Brushes.MediumPurple,
            "MIXED" => Brushes.LightSlateGray,
            _ => Brushes.DimGray
        };

        public string SpacingText => IsEqualSpacing ? $"등간격 (CV {PitchCv:F2})" : $"불균일 (CV {PitchCv:F2})";

        public string SummaryText => $"멤버 {NMembers}개 | 피치 {PitchMm:N0}mm | {SpacingText} | 굽힘 {NOrthoBends}회 | 연장 {TrunkLen:N0}mm";
    }

    public GroupPatternViewerWindow(List<ExistingRoutePath> allPaths, List<SpatialZone> spatialZones,
        PostgresConnectionOptions dbOptions, List<SceneObject>? equipment = null, List<SceneObject>? ductLaterals = null)
    {
        InitializeComponent();
        _allPaths = allPaths ?? new List<ExistingRoutePath>();
        _spatialZones = spatialZones ?? new List<SpatialZone>();
        _equipmentByName = BuildNameLookup(equipment);
        _ductLateralsByName = BuildNameLookup(ductLaterals);
        _pathByGuid = _allPaths
            .Where(p => !string.IsNullOrWhiteSpace(p.RoutePathGuid))
            .GroupBy(p => p.RoutePathGuid!, StringComparer.OrdinalIgnoreCase)
            .ToDictionary(g => g.Key, g => g.First(), StringComparer.OrdinalIgnoreCase);
        _dbOptions = dbOptions;
        PopulateFilterComboBoxes();
        DrawSpatialZones();
    }

    private static Dictionary<string, SceneObject> BuildNameLookup(List<SceneObject>? items)
    {
        if (items == null || items.Count == 0) return new Dictionary<string, SceneObject>(StringComparer.OrdinalIgnoreCase);
        return items
            .Where(x => !string.IsNullOrWhiteSpace(x.Name))
            .GroupBy(x => x.Name, StringComparer.OrdinalIgnoreCase)
            .ToDictionary(g => g.Key, g => g.First(), StringComparer.OrdinalIgnoreCase);
    }

    // 선택된 장비/유틸리티그룹에 해당하는 배관들의 시작 PoC 소유 장비, 종단 PoC 소유 덕트/레터럴을
    // 반투명(30%) 박스로 표시. 색상은 MainWindow/SegmentViewerWindow와 동일(장비=amber, 덕트=green, 레터럴=teal).
    private void DrawEndpointOwners(List<ExistingRoutePath> matched)
    {
        var drawnEquipment = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var drawnDuctLaterals = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        foreach (var path in matched)
        {
            if (!string.IsNullOrWhiteSpace(path.SourceName) && drawnEquipment.Add(path.SourceName!) &&
                _equipmentByName.TryGetValue(path.SourceName!, out var equipment))
            {
                AddSolidBox(equipment.Bounds, Color.FromArgb(77, 245, 158, 11), _ownerVisuals);
            }

            if (!string.IsNullOrWhiteSpace(path.TargetName) && drawnDuctLaterals.Add(path.TargetName!) &&
                _ductLateralsByName.TryGetValue(path.TargetName!, out var ductLateral))
            {
                var color = string.Equals(ductLateral.Category, "LATERAL", StringComparison.OrdinalIgnoreCase)
                    ? Color.FromArgb(77, 45, 212, 191)
                    : Color.FromArgb(77, 34, 197, 94);
                AddSolidBox(ductLateral.Bounds, color, _ownerVisuals);
            }
        }
    }

    private void AddSolidBox(Aabb box, Color color, List<Visual3D> bucket)
    {
        var visual = new BoxVisual3D
        {
            Center = new Point3D(box.Center.X, box.Center.Y, box.Center.Z),
            Length = box.Max.X - box.Min.X,
            Width = box.Max.Y - box.Min.Y,
            Height = box.Max.Z - box.Min.Z,
            Fill = new SolidColorBrush(color)
        };
        bucket.Add(visual);
        Viewport3D.Children.Add(visual);
    }

    private void DrawSpatialZones()
    {
        foreach (var zone in _spatialZones)
        {
            var brush = GetSpatialZoneBrush(zone.Name);
            AddWireBox(zone.Bounds, brush, 12.0, _zoneVisuals);
            AddBillboardText(zone.Name, zone.Bounds.Center, _zoneVisuals);
        }
    }

    private static Brush GetSpatialZoneBrush(string name)
    {
        var upper = name.ToUpperInvariant();
        if (upper.Contains("CR")) return Brushes.Yellow;
        if (upper.Contains("A/F") || upper.Contains("AF")) return Brushes.Cyan;
        if (upper.Contains("CSF")) return Brushes.Magenta;
        if (upper.Contains("FSF")) return Brushes.Orange;
        return Brushes.LightGray;
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

        DrawTube(new List<Vec3> { p000, p100 }, brush, diameter, bucket);
        DrawTube(new List<Vec3> { p010, p110 }, brush, diameter, bucket);
        DrawTube(new List<Vec3> { p001, p101 }, brush, diameter, bucket);
        DrawTube(new List<Vec3> { p011, p111 }, brush, diameter, bucket);
        DrawTube(new List<Vec3> { p000, p010 }, brush, diameter, bucket);
        DrawTube(new List<Vec3> { p100, p110 }, brush, diameter, bucket);
        DrawTube(new List<Vec3> { p001, p011 }, brush, diameter, bucket);
        DrawTube(new List<Vec3> { p101, p111 }, brush, diameter, bucket);
        DrawTube(new List<Vec3> { p000, p001 }, brush, diameter, bucket);
        DrawTube(new List<Vec3> { p100, p101 }, brush, diameter, bucket);
        DrawTube(new List<Vec3> { p010, p011 }, brush, diameter, bucket);
        DrawTube(new List<Vec3> { p110, p111 }, brush, diameter, bucket);
    }

    private void PopulateFilterComboBoxes()
    {
        _isUpdatingFilters = true;

        var equipments = _allPaths
            .Select(p => p.SourceName)
            .Where(s => !string.IsNullOrWhiteSpace(s))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(s => s)
            .ToList();

        var groups = _allPaths
            .Select(p => p.Group)
            .Where(g => !string.IsNullOrWhiteSpace(g))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(g => g)
            .ToList();

        CmbEquipment.ItemsSource = equipments;
        CmbUtilityGroup.ItemsSource = groups;

        _isUpdatingFilters = false;
    }

    private async void Filter_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_isUpdatingFilters) return;
        await LoadAsync();
    }

    private async Task LoadAsync()
    {
        var equipment = CmbEquipment.SelectedItem as string;
        var utilGroup = CmbUtilityGroup.SelectedItem as string;

        if (string.IsNullOrWhiteSpace(equipment) || string.IsNullOrWhiteSpace(utilGroup))
        {
            TxtStatus.Text = "장비와 유틸리티 그룹을 모두 선택하세요.";
            return;
        }

        Clear3D();
        _bundles = new List<BundleRow>();
        LstBundles.ItemsSource = null;
        TxtStatus.Text = "조회 중...";

        // 1. 전체 유틸리티배관(선택한 장비 + 유틸리티그룹) — 배경 배관으로 표시
        var matched = _allPaths.Where(p =>
            string.Equals(p.SourceName, equipment, StringComparison.OrdinalIgnoreCase) &&
            string.Equals(p.Group, utilGroup, StringComparison.OrdinalIgnoreCase)).ToList();

        var allPointsForFit = new List<Vec3>();
        var baseBrush = new SolidColorBrush(Color.FromArgb(90, 148, 163, 184));
        baseBrush.Freeze();
        foreach (var path in matched)
        {
            if (path.Points.Count < 2) continue;

            if (path.Fittings != null && path.Fittings.Count > 0)
            {
                DrawDetailedSegments(path, baseBrush, _baseVisuals);
            }
            else
            {
                var baseDiameter = path.DiameterMm > 0 ? path.DiameterMm : DefaultMemberDiameterMm;
                DrawTube(path.Points, baseBrush, baseDiameter, _baseVisuals);
            }

            allPointsForFit.AddRange(path.Points);
        }

        DrawEndpointOwners(matched);

        // 2. 그룹배관 패턴 조회 (TB_ROUTE_GROUP_PATTERN, Middle Trunk/CSF 구간 기준으로 ExportGroupPattern.py가 산출)
        try
        {
            // EQUIPMENT_NAME(예: "Mechanical Equipment//Autodesk.Revit.DB.FamilyInstance//kscta01//kscta01//")과
            // TB_ROUTE_PATH.EQUIPMENT_TAG(예: "KSCTA01")는 서로 다른 컬럼이라 단순 문자열 가공으로는 매칭되지 않는다.
            // ExportGroupPattern.py도 EQUIPMENT_TAG 기준으로 저장하므로, 실제 배관 레코드에 저장된 EQUIPMENT_TAG 값을 그대로 사용한다.
            var eqTag = matched.Select(p => p.EquipmentTag).FirstOrDefault(t => !string.IsNullOrWhiteSpace(t))
                        ?? equipment.TrimEnd('_').Trim();

            using var conn = new Npgsql.NpgsqlConnection(_dbOptions.ConnectionString);
            await conn.OpenAsync();

            const string sql = @"
                SELECT ""GROUP_ID"", ""N_MEMBERS"", ""PITCH_MM"", ""PITCH_CV"", ""IS_EQUAL_SPACING"", ""OFFSET_AXIS"",
                       ""N_ORTHO_BENDS"", ""TRUNK_LEN"", ""MEMBER_GUIDS""::text AS member_guids_json,
                       ST_AsText(""GEOM_3D"") AS geom_wkt, ST_AsText(""TRUNK_GEOM_3D"") AS trunk_wkt,
                       ""UTILITY""
                FROM ""TB_ROUTE_GROUP_PATTERN""
                WHERE TRIM(UPPER(""EQUIPMENT_TAG"")) = TRIM(UPPER(@eqTag))
                  AND TRIM(UPPER(""UTILITY_GROUP"")) = TRIM(UPPER(@utilGroup))
                ORDER BY ""N_MEMBERS"" DESC";

            using var cmd = new Npgsql.NpgsqlCommand(sql, conn);
            cmd.Parameters.AddWithValue("eqTag", eqTag);
            cmd.Parameters.AddWithValue("utilGroup", utilGroup);

            using var reader = await cmd.ExecuteReaderAsync();
            var idx = 0;
            var rows = new List<BundleRow>();
            while (await reader.ReadAsync())
            {
                var groupId = reader.GetString(0);
                var nMembers = reader.GetInt32(1);
                var pitchMm = reader.GetDouble(2);
                var pitchCv = reader.IsDBNull(3) ? 0.0 : reader.GetDouble(3);
                var isEqual = !reader.IsDBNull(4) && reader.GetBoolean(4);
                var offsetAxis = reader.IsDBNull(5) ? "UNKNOWN" : reader.GetString(5);
                var nBends = reader.GetInt32(6);
                var trunkLen = reader.IsDBNull(7) ? 0.0 : reader.GetDouble(7);
                var memberGuidsJson = reader.IsDBNull(8) ? "" : reader.GetString(8);
                var geomWkt = reader.IsDBNull(9) ? "" : reader.GetString(9);
                var trunkWkt = reader.IsDBNull(10) ? "" : reader.GetString(10);
                var utility = reader.IsDBNull(11) ? "" : reader.GetString(11);

                var brush = GetGroupBrush(groupId, idx++);
                var memberPaths = ParseWktMultiLineStringZ(geomWkt);
                var trunkPaths = ParseWktMultiLineStringZ(trunkWkt);
                var memberDiameter = ResolveMemberDiameter(memberGuidsJson);

                rows.Add(new BundleRow(groupId, nMembers, pitchMm, pitchCv, isEqual, offsetAxis, nBends, trunkLen,
                    memberDiameter, brush, memberPaths, trunkPaths, utility));
            }

            _bundles = rows;
            LstBundles.ItemsSource = _bundles;

            DrawAllBundles(null);

            if (allPointsForFit.Count == 0)
            {
                allPointsForFit.AddRange(_bundles.SelectMany(b => b.MemberPaths.SelectMany(x => x)));
            }
            if (allPointsForFit.Count > 0) FitCamera(allPointsForFit);

            var horizCount = _bundles.Count(b => b.OffsetAxis == "HORIZONTAL");
            var vertCount = _bundles.Count(b => b.OffsetAxis == "VERTICAL");
            var mixedCount = _bundles.Count(b => b.OffsetAxis == "MIXED");
            var equalCount = _bundles.Count(b => b.IsEqualSpacing);
            TxtStatus.Text = $"배경배관 {matched.Count}개, 그룹배관 {_bundles.Count}개 " +
                              $"(수평 {horizCount} / 수직 {vertCount} / 혼합 {mixedCount}, 등간격 {equalCount}개) 로딩 완료.";
        }
        catch (Exception ex)
        {
            MessageBox.Show($"그룹배관 패턴 조회 중 오류가 발생했습니다:\n{ex.Message}", "데이터베이스 오류", MessageBoxButton.OK, MessageBoxImage.Error);
            TxtStatus.Text = "그룹배관 패턴 조회 실패.";
        }
    }

    // MEMBER_GUIDS(jsonb 배열)에 속한 실제 배관들의 관경(DiameterMm) 중 최댓값 + 2mm를 다발 강조 굵기로 사용.
    // 실제 배관을 뒤덮는 굵은 튜브 대신, 실제 파이프에 밀착된 얇은 하이라이트로 보이게 하기 위함.
    private double ResolveMemberDiameter(string memberGuidsJson)
    {
        if (string.IsNullOrWhiteSpace(memberGuidsJson)) return DefaultMemberDiameterMm + MemberHighlightMarginMm;

        double maxDiameter = 0.0;
        try
        {
            foreach (var guid in JsonSerializer.Deserialize<string[]>(memberGuidsJson) ?? Array.Empty<string>())
            {
                if (!string.IsNullOrWhiteSpace(guid) && _pathByGuid.TryGetValue(guid, out var path) && path.DiameterMm > maxDiameter)
                {
                    maxDiameter = path.DiameterMm;
                }
            }
        }
        catch (JsonException)
        {
            // MEMBER_GUIDS 파싱 실패 시 기본값 사용
        }

        return (maxDiameter > 0 ? maxDiameter : DefaultMemberDiameterMm) + MemberHighlightMarginMm;
    }

    private SolidColorBrush GetGroupBrush(string groupId, int index)
    {
        if (_groupBrushes.TryGetValue(groupId, out var brush)) return brush;
        var color = GroupPalette[index % GroupPalette.Length];
        var newBrush = new SolidColorBrush(color);
        newBrush.Freeze();
        _groupBrushes[groupId] = newBrush;
        return newBrush;
    }

    private void LstBundles_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        var selected = LstBundles.SelectedItem as BundleRow;
        DrawAllBundles(selected);

        if (selected != null)
        {
            TxtSelectedGroup.Text = selected.ShortId;
            var pts = selected.MemberPaths.SelectMany(x => x).Concat(selected.TrunkPaths.SelectMany(x => x)).ToList();
            if (pts.Count > 0) FitCamera(pts);
        }
        else
        {
            TxtSelectedGroup.Text = "선택 없음";
        }
    }

    private void DrawAllBundles(BundleRow? selected)
    {
        ClearVisuals(_bundleVisuals);

        foreach (var row in _bundles)
        {
            bool isSelected = selected != null && selected.GroupId == row.GroupId;
            bool hasSelection = selected != null;

            SolidColorBrush memberBrush;
            double memberDiameter;
            SolidColorBrush trunkBrush;
            double trunkDiameter;

            // 다발 강조 굵기는 실제 배관 관경(row.MemberDiameter = 실제 관경 + 2mm) 기준 — 실제 배관을 뒤덮지 않고 얇게 밀착 표시.
            if (isSelected)
            {
                memberBrush = new SolidColorBrush(Color.FromArgb(255, row.ColorBrush.Color.R, row.ColorBrush.Color.G, row.ColorBrush.Color.B));
                memberDiameter = row.MemberDiameter;
                trunkBrush = row.ColorBrush;
                trunkDiameter = row.MemberDiameter * 0.5;
            }
            else if (hasSelection)
            {
                memberBrush = new SolidColorBrush(Color.FromArgb(35, row.ColorBrush.Color.R, row.ColorBrush.Color.G, row.ColorBrush.Color.B));
                memberDiameter = row.MemberDiameter;
                trunkBrush = new SolidColorBrush(Color.FromArgb(35, row.ColorBrush.Color.R, row.ColorBrush.Color.G, row.ColorBrush.Color.B));
                trunkDiameter = row.MemberDiameter * 0.2;
            }
            else
            {
                memberBrush = new SolidColorBrush(Color.FromArgb(150, row.ColorBrush.Color.R, row.ColorBrush.Color.G, row.ColorBrush.Color.B));
                memberDiameter = row.MemberDiameter;
                trunkBrush = row.ColorBrush;
                trunkDiameter = row.MemberDiameter * 0.35;
            }

            foreach (var line in row.MemberPaths) DrawTube(line, memberBrush, memberDiameter, _bundleVisuals);
            foreach (var line in row.TrunkPaths) DrawTube(line, trunkBrush, trunkDiameter, _bundleVisuals);

            // 수평/수직/혼합 라벨을 대표 중심선 중간 지점에 3D 텍스트로 표시
            var allTrunkPts = row.TrunkPaths.SelectMany(x => x).ToList();
            if (allTrunkPts.Count > 0 && (!hasSelection || isSelected))
            {
                var mid = allTrunkPts[allTrunkPts.Count / 2];
                AddBillboardText(row.AxisBadge, mid, _bundleVisuals);
            }
        }

        ApplyLayerVisibility();
    }

    private void DrawTube(List<Vec3> points, Brush brush, double diameter, List<Visual3D> bucket)
    {
        if (points.Count < 2) return;
        var collection = new Point3DCollection(points.Select(p => new Point3D(p.X, p.Y, p.Z)));
        var tube = new TubeVisual3D { Path = collection, Diameter = diameter, Fill = brush };
        bucket.Add(tube);
        Viewport3D.Children.Add(tube);
    }

    private void DrawDetailedSegments(ExistingRoutePath path, Brush brush, List<Visual3D> bucket)
    {
        if (path.Fittings == null || path.Fittings.Count == 0) return;

        foreach (var f in path.Fittings)
        {
            if (f.GlbData != null && f.GlbData.Length > 0)
            {
                var mesh = GlbParser.Parse(f.GlbData);
                if (mesh == null) continue;

                GlbParser.TransformMeshToObb(mesh, f.Lbb, f.Rbb, f.Ltb, f.Lbf);

                var material = new DiffuseMaterial(brush);
                var model = new GeometryModel3D(mesh, material) { BackMaterial = material };
                var modelVisual = new ModelVisual3D { Content = model };

                bucket.Add(modelVisual);
                Viewport3D.Children.Add(modelVisual);
            }
            else
            {
                // Draw Pipe / Bending cylinders using OBB axes
                var vX = f.Rbb - f.Lbb;
                var vY = f.Ltb - f.Lbb;
                var vZ = f.Lbf - f.Lbb;

                double lenX = vX.Length;
                double lenY = vY.Length;
                double lenZ = vZ.Length;

                Vec3 startPt, endPt;
                if (lenX >= lenY && lenX >= lenZ)
                {
                    startPt = f.Lbb + (vY + vZ) * 0.5;
                    endPt = f.Rbb + (vY + vZ) * 0.5;
                }
                else if (lenY >= lenX && lenY >= lenZ)
                {
                    startPt = f.Lbb + (vX + vZ) * 0.5;
                    endPt = f.Ltb + (vX + vZ) * 0.5;
                }
                else
                {
                    startPt = f.Lbb + (vX + vY) * 0.5;
                    endPt = f.Lbf + (vX + vY) * 0.5;
                }

                if ((endPt - startPt).Length < 1.0) continue;

                var diameter = path.DiameterMm > 0 ? path.DiameterMm : 100.0;
                var pts = new[] { startPt, endPt };
                var collection = new Point3DCollection(pts.Select(p => new Point3D(p.X, p.Y, p.Z)));

                var tube = new TubeVisual3D { Path = collection, Diameter = diameter, Fill = brush };
                bucket.Add(tube);
                Viewport3D.Children.Add(tube);
            }
        }
    }

    private void AddBillboardText(string text, Vec3 position, List<Visual3D> bucket)
    {
        var visual = new BillboardTextVisual3D
        {
            Position = new Point3D(position.X, position.Y, position.Z + 300),
            Text = text,
            Foreground = Brushes.White,
            Background = new SolidColorBrush(Color.FromArgb(200, 15, 23, 42)),
            FontSize = 13,
            FontWeight = FontWeights.Bold,
            Padding = new Thickness(5, 3, 5, 3),
            HorizontalAlignment = HorizontalAlignment.Center,
            VerticalAlignment = VerticalAlignment.Center
        };
        bucket.Add(visual);
        Viewport3D.Children.Add(visual);
    }

    private void Clear3D()
    {
        ClearVisuals(_baseVisuals);
        ClearVisuals(_bundleVisuals);
        ClearVisuals(_ownerVisuals);
    }

    private void ClearVisuals(List<Visual3D> bucket)
    {
        foreach (var v in bucket) Viewport3D.Children.Remove(v);
        bucket.Clear();
    }

    private void ApplyLayerVisibility()
    {
        // 현재는 별도 레이어 토글이 없으므로 항상 표시 상태를 유지 — 향후 필요 시 확장 지점.
    }

    private void BtnFitCamera_Click(object sender, RoutedEventArgs e)
    {
        var pts = _allPaths
            .Where(p => string.Equals(p.SourceName, CmbEquipment.SelectedItem as string, StringComparison.OrdinalIgnoreCase) &&
                        string.Equals(p.Group, CmbUtilityGroup.SelectedItem as string, StringComparison.OrdinalIgnoreCase))
            .SelectMany(p => p.Points)
            .ToList();
        if (pts.Count == 0) pts = _bundles.SelectMany(b => b.MemberPaths.SelectMany(x => x)).ToList();
        if (pts.Count > 0) FitCamera(pts);
    }

    private void FitCamera(List<Vec3> points)
    {
        if (points.Count == 0 || Viewport3D.Camera is not PerspectiveCamera camera)
        {
            Viewport3D.ZoomExtents(200);
            return;
        }

        var minX = points.Min(p => p.X); var maxX = points.Max(p => p.X);
        var minY = points.Min(p => p.Y); var maxY = points.Max(p => p.Y);
        var minZ = points.Min(p => p.Z); var maxZ = points.Max(p => p.Z);
        var center = new Point3D((minX + maxX) / 2.0, (minY + maxY) / 2.0, (minZ + maxZ) / 2.0);
        var sx = Math.Max(1, maxX - minX);
        var sy = Math.Max(1, maxY - minY);
        var sz = Math.Max(1, maxZ - minZ);
        var radius = Math.Max(200, Math.Sqrt(sx * sx + sy * sy + sz * sz) * 0.5);

        var direction = new Vector3D(1.35, -1.55, 0.85);
        direction.Normalize();
        var fovRadians = Math.Max(10, camera.FieldOfView) * Math.PI / 180.0;
        var distance = Math.Max(radius / Math.Tan(fovRadians * 0.5) * 1.35, radius * 2.4);
        var position = center + direction * distance;
        camera.Position = position;
        camera.LookDirection = center - position;
        camera.UpDirection = new Vector3D(0, 0, 1);
        camera.NearPlaneDistance = 10.0;
        camera.FarPlaneDistance = 10000000.0;
    }

    private static List<List<Vec3>> ParseWktMultiLineStringZ(string wkt)
    {
        var result = new List<List<Vec3>>();
        if (string.IsNullOrEmpty(wkt)) return result;

        var cleanWkt = wkt.Trim();
        if (!cleanWkt.StartsWith("MULTILINESTRING Z", StringComparison.OrdinalIgnoreCase) &&
            !cleanWkt.StartsWith("MULTILINESTRINGZ", StringComparison.OrdinalIgnoreCase))
        {
            return result;
        }

        var firstParen = cleanWkt.IndexOf('(');
        var lastParen = cleanWkt.LastIndexOf(')');
        if (firstParen == -1 || lastParen == -1 || lastParen <= firstParen) return result;

        var inner = cleanWkt.Substring(firstParen + 1, lastParen - firstParen - 1).Trim();

        var depth = 0;
        var currentPart = new System.Text.StringBuilder();
        var parts = new List<string>();

        foreach (var ch in inner)
        {
            if (ch == '(')
            {
                depth++;
                if (depth == 1) continue;
            }
            else if (ch == ')')
            {
                depth--;
                if (depth == 0)
                {
                    parts.Add(currentPart.ToString());
                    currentPart.Clear();
                    continue;
                }
            }

            if (depth > 0)
            {
                currentPart.Append(ch);
            }
        }

        foreach (var part in parts)
        {
            var pts = new List<Vec3>();
            var pointsSplit = part.Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries);
            foreach (var pStr in pointsSplit)
            {
                var coords = pStr.Trim().Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
                if (coords.Length >= 3 &&
                    double.TryParse(coords[0], CultureInfo.InvariantCulture, out var x) &&
                    double.TryParse(coords[1], CultureInfo.InvariantCulture, out var y) &&
                    double.TryParse(coords[2], CultureInfo.InvariantCulture, out var z))
                {
                    pts.Add(new Vec3(x, y, z));
                }
            }
            if (pts.Count > 0)
            {
                result.Add(pts);
            }
        }

        return result;
    }
}
