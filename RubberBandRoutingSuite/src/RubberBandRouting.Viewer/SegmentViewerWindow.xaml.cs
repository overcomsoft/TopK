using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Media3D;
using HelixToolkit.Wpf;
using RubberBandRouting.Engine;

namespace RubberBandRouting.Viewer;

public partial class SegmentViewerWindow : Window
{
    private readonly List<ExistingRoutePath> _allPaths;
    private readonly List<SpatialZone> _spatialZones;
    private readonly List<Visual3D> _visuals3D = new();
    private readonly List<Visual3D> _visualsXY = new();
    private readonly List<Visual3D> _visualsZ = new();
    private readonly Dictionary<Visual3D, object> _visualOwners = new();
    private readonly List<Visual3D> _selectedVisuals = new();
    private readonly List<Brush> _originalBrushes = new();
    private readonly List<object> _orderedPathObjects = new();
    private Point _viewportMouseDownPos;
    private bool _isUpdatingFilters;

    internal sealed class PathViewModel
    {
        public ExistingRoutePath Path { get; }
        public string DisplayName => $"[{(string.IsNullOrWhiteSpace(Path.Group) ? "?" : Path.Group)}] {(string.IsNullOrWhiteSpace(Path.Utility) ? "?" : Path.Utility)}";
        public string DetailText => $"{(string.IsNullOrWhiteSpace(Path.SourceName) ? "Start" : Path.SourceName)} -> {(string.IsNullOrWhiteSpace(Path.TargetName) ? "End" : Path.TargetName)} ({Path.DiameterMm:F0}mm)";

        public PathViewModel(ExistingRoutePath path)
        {
            Path = path;
        }
    }

    public SegmentViewerWindow(List<ExistingRoutePath> paths, List<SpatialZone> spatialZones)
    {
        InitializeComponent();
        _allPaths = paths ?? new List<ExistingRoutePath>();
        _spatialZones = spatialZones ?? new List<SpatialZone>();
        
        PopulateFilterComboBoxes();
        ApplyFilters();
    }

    private void PopulateFilterComboBoxes()
    {
        _isUpdatingFilters = true;

        var equipments = _allPaths
            .Select(p => p.SourceName)
            .Where(s => !string.IsNullOrWhiteSpace(s))
            .Distinct()
            .OrderBy(s => s)
            .ToList();

        var groups = _allPaths
            .Select(p => p.Group)
            .Where(g => !string.IsNullOrWhiteSpace(g))
            .Distinct()
            .OrderBy(g => g)
            .ToList();

        var utilities = _allPaths
            .Select(p => p.Utility)
            .Where(u => !string.IsNullOrWhiteSpace(u))
            .Distinct()
            .OrderBy(u => u)
            .ToList();

        CmbEquipment.Items.Clear();
        CmbEquipment.Items.Add("-- 전체 --");
        foreach (var eq in equipments) CmbEquipment.Items.Add(eq);
        CmbEquipment.SelectedIndex = 0;

        CmbUtilityGroup.Items.Clear();
        CmbUtilityGroup.Items.Add("-- 전체 --");
        foreach (var grp in groups) CmbUtilityGroup.Items.Add(grp);
        CmbUtilityGroup.SelectedIndex = 0;

        CmbUtility.Items.Clear();
        CmbUtility.Items.Add("-- 전체 --");
        foreach (var ut in utilities) CmbUtility.Items.Add(ut);
        CmbUtility.SelectedIndex = 0;

        _isUpdatingFilters = false;
    }

    private void Filter_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_isUpdatingFilters) return;
        ApplyFilters();
    }

    private void ApplyFilters()
    {
        var eqFilter = CmbEquipment.SelectedItem?.ToString();
        var grpFilter = CmbUtilityGroup.SelectedItem?.ToString();
        var utFilter = CmbUtility.SelectedItem?.ToString();

        if (eqFilter == "-- 전체 --") eqFilter = null;
        if (grpFilter == "-- 전체 --") grpFilter = null;
        if (utFilter == "-- 전체 --") utFilter = null;

        var filtered = _allPaths.AsEnumerable();

        if (eqFilter != null)
        {
            filtered = filtered.Where(p => string.Equals(p.SourceName, eqFilter, StringComparison.OrdinalIgnoreCase));
        }
        if (grpFilter != null)
        {
            filtered = filtered.Where(p => string.Equals(p.Group, grpFilter, StringComparison.OrdinalIgnoreCase));
        }
        if (utFilter != null)
        {
            filtered = filtered.Where(p => string.Equals(p.Utility, utFilter, StringComparison.OrdinalIgnoreCase));
        }

        var viewModels = filtered.Select(p => new PathViewModel(p)).ToList();
        LstPaths.ItemsSource = viewModels;

        if (viewModels.Count > 0)
        {
            LstPaths.SelectedIndex = 0;
        }
        else
        {
            Clear3DView();
            TxtSelectedGuid.Text = "선택 없음";
            TxtStartLen.Text = "0 mm";
            TxtTrunkLen.Text = "0 mm";
            TxtEndLen.Text = "0 mm";
        }
    }

    private void BtnResetFilters_Click(object sender, RoutedEventArgs e)
    {
        _isUpdatingFilters = true;
        CmbEquipment.SelectedIndex = 0;
        CmbUtilityGroup.SelectedIndex = 0;
        CmbUtility.SelectedIndex = 0;
        _isUpdatingFilters = false;
        ApplyFilters();
    }

    private void LstPaths_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (LstPaths.SelectedItem is not PathViewModel selected)
        {
            Clear3DView();
            return;
        }

        DrawPathSegments(selected.Path);
        PopulatePathDetailsTab(selected.Path);
    }

    private void Clear3DView()
    {
        foreach (var v in _visuals3D) Viewport3D.Children.Remove(v);
        _visuals3D.Clear();

        foreach (var v in _visualsXY) ViewportXY.Children.Remove(v);
        _visualsXY.Clear();

        foreach (var v in _visualsZ) ViewportZ.Children.Remove(v);
        _visualsZ.Clear();

        _visualOwners.Clear();
        _selectedVisuals.Clear();
        _originalBrushes.Clear();
        _orderedPathObjects.Clear();
        PnlAttributes.Children.Clear();
        PnlAttributes.Children.Add(new TextBlock { Text = "3D 뷰의 배관/피팅을 클릭하세요.", Foreground = Brushes.SlateGray, FontSize = 11, TextWrapping = TextWrapping.Wrap });

        if (TxtPathInfoTitle != null) TxtPathInfoTitle.Text = "선택된 경로가 없습니다.";
        if (LvwPathSegments != null) LvwPathSegments.ItemsSource = null;
    }

    private void DrawPathSegments(ExistingRoutePath path)
    {
        Clear3DView();

        DrawSpatialZones(_visuals3D, Viewport3D);
        DrawSpatialZones(_visualsXY, ViewportXY);
        DrawSpatialZones(_visualsZ, ViewportZ);

        TxtSelectedGuid.Text = path.RoutePathGuid;

        var startLen = GetPolylineLength(path.StartStubPoints);
        var trunkLen = GetPolylineLength(path.MiddleTrunkPoints);
        var endLen = GetPolylineLength(path.EndStubPoints);

        TxtStartLen.Text = $"{startLen:N0} mm";
        TxtTrunkLen.Text = $"{trunkLen:N0} mm";
        TxtEndLen.Text = $"{endLen:N0} mm";

        var hasSegments = path.StartStubPoints.Count > 0 || path.MiddleTrunkPoints.Count > 0 || path.EndStubPoints.Count > 0;
        
        if (!hasSegments)
        {
            BuildAndDrawDetailedSection(path.Points, Colors.White, 80, "일반 본선", path);
        }
        else
        {
            BuildAndDrawDetailedSection(path.StartStubPoints, Colors.OrangeRed, 100, "시작 인입부 (Start Stub)", path);
            BuildAndDrawDetailedSection(path.MiddleTrunkPoints, Colors.LimeGreen, 100, "중앙 본선 (Middle Trunk)", path);
            BuildAndDrawDetailedSection(path.EndStubPoints, Colors.Cyan, 100, "종단 도출부 (End Stub)", path);
        }

        FitCameraToCurrentPath(path.Points);
    }

    private void BuildAndDrawDetailedSection(List<Vec3> points, Color color, double diameter, string sectionName, ExistingRoutePath path)
    {
        if (points.Count == 0) return;

        // 1. Add Start PoC
        {
            var pStart = points[0];
            var startMeta = new FittingObjectMetadata(pStart, 0.0, new Vec3(0,0,0), new Vec3(0,0,0), "인입점 (Start PoC)", sectionName, path);
            _orderedPathObjects.Add(startMeta);
            AddIndividualMarker(pStart, new SolidColorBrush(color), diameter * 0.6, _visuals3D, Viewport3D, startMeta);
            AddIndividualMarker(pStart, new SolidColorBrush(color), diameter * 0.6, _visualsXY, ViewportXY, startMeta);
            AddIndividualMarker(pStart, new SolidColorBrush(color), diameter * 0.6, _visualsZ, ViewportZ, startMeta);
        }

        // 2. Loop through rest
        for (int i = 0; i < points.Count - 1; i++)
        {
            var p0 = points[i];
            var p1 = points[i + 1];
            var length = (p1 - p0).Length;
            if (length < 1e-3) continue;

            // Add Straight Pipe
            var pipeMeta = new PipeObjectMetadata(p0, p1, length, GetDirectionName(p1 - p0), sectionName, path);
            _orderedPathObjects.Add(pipeMeta);
            DrawIndividualTube(p0, p1, color, diameter, _visuals3D, Viewport3D, pipeMeta);
            DrawIndividualTube(p0, p1, color, diameter, _visualsXY, ViewportXY, pipeMeta);
            DrawIndividualTube(p0, p1, color, diameter, _visualsZ, ViewportZ, pipeMeta);

            // Add Elbow at p1 if it is a direction change (and not the end of the section)
            if (i + 1 < points.Count - 1)
            {
                var next = points[i + 2];
                var v1 = p1 - p0;
                var v2 = next - p1;
                var len1 = v1.Length;
                var len2 = v2.Length;

                if (len1 > 1e-3 && len2 > 1e-3)
                {
                    double dot = v1.X * v2.X + v1.Y * v2.Y + v1.Z * v2.Z;
                    double cosTheta = dot / (len1 * len2);
                    cosTheta = Math.Max(-1.0, Math.Min(1.0, cosTheta));
                    double angle = Math.Acos(cosTheta) * 180.0 / Math.PI;

                    if (angle > 1.0)
                    {
                        var elbowMeta = new FittingObjectMetadata(p1, angle, v1, v2, "엘보 (Elbow)", sectionName, path);
                        _orderedPathObjects.Add(elbowMeta);
                        AddIndividualMarker(p1, new SolidColorBrush(color), diameter * 0.6, _visuals3D, Viewport3D, elbowMeta);
                        AddIndividualMarker(p1, new SolidColorBrush(color), diameter * 0.6, _visualsXY, ViewportXY, elbowMeta);
                        AddIndividualMarker(p1, new SolidColorBrush(color), diameter * 0.6, _visualsZ, ViewportZ, elbowMeta);
                    }
                }
            }
        }

        // 3. Add End PoC
        {
            var pEnd = points[points.Count - 1];
            var endMeta = new FittingObjectMetadata(pEnd, 0.0, new Vec3(0,0,0), new Vec3(0,0,0), "도출점 (End PoC)", sectionName, path);
            _orderedPathObjects.Add(endMeta);
            AddIndividualMarker(pEnd, new SolidColorBrush(color), diameter * 0.6, _visuals3D, Viewport3D, endMeta);
            AddIndividualMarker(pEnd, new SolidColorBrush(color), diameter * 0.6, _visualsXY, ViewportXY, endMeta);
            AddIndividualMarker(pEnd, new SolidColorBrush(color), diameter * 0.6, _visualsZ, ViewportZ, endMeta);
        }
    }

    private void DrawIndividualTube(Vec3 p0, Vec3 p1, Color color, double diameter, List<Visual3D> bucket, HelixViewport3D viewport, object owner)
    {
        var collection = new Point3DCollection(new[] { new Point3D(p0.X, p0.Y, p0.Z), new Point3D(p1.X, p1.Y, p1.Z) });
        var tube = new TubeVisual3D
        {
            Path = collection,
            Diameter = diameter,
            Fill = new SolidColorBrush(color)
        };
        bucket.Add(tube);
        viewport.Children.Add(tube);
        _visualOwners[tube] = owner;
    }

    private void AddIndividualMarker(Vec3 p, Brush brush, double radius, List<Visual3D> bucket, HelixViewport3D viewport, object owner)
    {
        var sphere = new SphereVisual3D
        {
            Center = new Point3D(p.X, p.Y, p.Z),
            Radius = radius,
            Fill = brush
        };
        bucket.Add(sphere);
        viewport.Children.Add(sphere);
        _visualOwners[sphere] = owner;
    }

    private static string GetDirectionName(Vec3 d)
    {
        var values = new[] { Math.Abs(d.X), Math.Abs(d.Y), Math.Abs(d.Z) };
        var ax = 0;
        if (values[1] > values[0]) ax = 1;
        if (values[2] > values[ax]) ax = 2;

        var isPositive = true;
        if (ax == 0 && d.X < 0) isPositive = false;
        else if (ax == 1 && d.Y < 0) isPositive = false;
        else if (ax == 2 && d.Z < 0) isPositive = false;

        var axName = ax switch { 0 => "X", 1 => "Y", _ => "Z" };
        return (isPositive ? "+" : "-") + axName;
    }

    private double GetPolylineLength(List<Vec3> points)
    {
        if (points.Count < 2) return 0;
        var total = 0.0;
        for (var i = 1; i < points.Count; i++) total += (points[i] - points[i - 1]).Length;
        return total;
    }

    private void BtnFitCamera_Click(object sender, RoutedEventArgs e)
    {
        if (LstPaths.SelectedItem is PathViewModel selected)
        {
            FitCameraToCurrentPath(selected.Path.Points);
        }
    }

    private void FitCameraToCurrentPath(List<Vec3> points)
    {
        // 1. Perspective camera fit for main Viewport3D
        if (points.Count > 0 && Viewport3D.Camera is PerspectiveCamera camera)
        {
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
        else
        {
            Viewport3D.ZoomExtents(200);
        }

        // 2. Orthographic camera fits for 2D views
        double minX2 = double.MaxValue, maxX2 = double.MinValue;
        double minY2 = double.MaxValue, maxY2 = double.MinValue;
        double minZ2 = double.MaxValue, maxZ2 = double.MinValue;

        if (points.Count > 0)
        {
            minX2 = points.Min(p => p.X); maxX2 = points.Max(p => p.X);
            minY2 = points.Min(p => p.Y); maxY2 = points.Max(p => p.Y);
            minZ2 = points.Min(p => p.Z); maxZ2 = points.Max(p => p.Z);
        }
        else if (_spatialZones.Count > 0)
        {
            foreach (var zone in _spatialZones)
            {
                minX2 = Math.Min(minX2, zone.Bounds.Min.X);
                maxX2 = Math.Max(maxX2, zone.Bounds.Max.X);
                minY2 = Math.Min(minY2, zone.Bounds.Min.Y);
                maxY2 = Math.Max(maxY2, zone.Bounds.Max.Y);
                minZ2 = Math.Min(minZ2, zone.Bounds.Min.Z);
                maxZ2 = Math.Max(maxZ2, zone.Bounds.Max.Z);
            }
        }
        else
        {
            minX2 = 0; maxX2 = 10000;
            minY2 = 0; maxY2 = 10000;
            minZ2 = 0; maxZ2 = 10000;
        }

        var centerX = (minX2 + maxX2) / 2.0;
        var centerY = (minY2 + maxY2) / 2.0;
        var centerZ = (minZ2 + maxZ2) / 2.0;

        // 패딩을 20% 추가하여 경로 양 끝이 잘리지 않도록 함
        var sizeX = Math.Max(500, (maxX2 - minX2) * 1.2);
        var sizeY = Math.Max(500, (maxY2 - minY2) * 1.2);
        var sizeZ = Math.Max(500, (maxZ2 - minZ2) * 1.2);

        // HelixViewport3D 렌더링 완료 후에 카메라를 적용해야 반영됨
        // DispatcherPriority.Loaded: 레이아웃+렌더 완료 후 → ActualWidth/ActualHeight 확정된 시점
        Dispatcher.BeginInvoke(System.Windows.Threading.DispatcherPriority.Loaded, () =>
        {
            // ── 수평(XY) 뷰: Z축 위에서 아래로 내려다봄 ──────────────────────────────
            // 뷰포트 aspect ratio 계산 (width/height)
            double xyAspect = ViewportXY.ActualWidth > 0 && ViewportXY.ActualHeight > 0
                ? ViewportXY.ActualWidth / ViewportXY.ActualHeight
                : 1.5;

            // OrthographicCamera.Width = 화면 수평 범위(world units)
            // 화면 수직 범위 = Width / aspect
            // → 두 축(X, Y) 모두 보이려면:
            //   Width >= sizeX  AND  Width / aspect >= sizeY  →  Width >= sizeY * aspect
            double neededXY = Math.Max(sizeX, sizeY * xyAspect) * 1.1;

            if (ViewportXY.Camera is not OrthographicCamera camXY)
            {
                camXY = new OrthographicCamera();
                ViewportXY.Camera = camXY;
            }
            camXY.Position = new Point3D(centerX, centerY, centerZ + 500000);
            camXY.LookDirection = new Vector3D(0, 0, -1);
            camXY.UpDirection = new Vector3D(0, 1, 0);
            camXY.Width = neededXY;
            camXY.NearPlaneDistance = 1.0;
            camXY.FarPlaneDistance = 2000000.0;

            // ── 수직(단면) 뷰: 경로의 주 수평 이동 방향 직각에서 바라봄 ───────────────
            // Y 이동 지배적 → +X에서 바라봄(Y-Z 평면), X 이동 지배적 → +Y에서 바라봄(X-Z 평면)
            double zAspect = ViewportZ.ActualWidth > 0 && ViewportZ.ActualHeight > 0
                ? ViewportZ.ActualWidth / ViewportZ.ActualHeight
                : 1.5;

            if (ViewportZ.Camera is not OrthographicCamera camZ)
            {
                camZ = new OrthographicCamera();
                ViewportZ.Camera = camZ;
            }

            bool yDominant = sizeY >= sizeX;
            double domHoriz = yDominant ? sizeY : sizeX;

            // Width(수평) >= domHoriz  AND  Width/aspect(수직) >= sizeZ
            // → Width >= Math.Max(domHoriz, sizeZ * aspect)
            double neededZ = Math.Max(domHoriz, sizeZ * zAspect) * 1.1;

            if (yDominant)
            {
                // Y 방향 이동 → X축(+X)에서 바라봄 → Y-Z 평면 표시
                camZ.Position = new Point3D(centerX + 500000, centerY, centerZ);
                camZ.LookDirection = new Vector3D(-1, 0, 0);
                camZ.UpDirection = new Vector3D(0, 0, 1);
            }
            else
            {
                // X 방향 이동 → Y축(+Y)에서 바라봄 → X-Z 평면 표시
                camZ.Position = new Point3D(centerX, centerY + 500000, centerZ);
                camZ.LookDirection = new Vector3D(0, -1, 0);
                camZ.UpDirection = new Vector3D(0, 0, 1);
            }
            camZ.Width = neededZ;
            camZ.NearPlaneDistance = 1.0;
            camZ.FarPlaneDistance = 2000000.0;
        });
    }

    private void DrawSpatialZones(List<Visual3D> bucket, HelixViewport3D viewport)
    {
        if (_spatialZones == null) return;
        foreach (var zone in _spatialZones)
        {
            var brush = GetSpatialZoneBrush(zone.Name);
            AddWireBox(zone.Bounds, brush, 20.0, bucket, viewport);
            AddTextLabel(zone.Name, zone.Bounds.Center, bucket, viewport);
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

    private void AddWireBox(Aabb box, Brush brush, double diameter, List<Visual3D> bucket, HelixViewport3D viewport)
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

        DrawWireLine(new[] { p000, p100 }, brush, diameter, bucket, viewport);
        DrawWireLine(new[] { p010, p110 }, brush, diameter, bucket, viewport);
        DrawWireLine(new[] { p001, p101 }, brush, diameter, bucket, viewport);
        DrawWireLine(new[] { p011, p111 }, brush, diameter, bucket, viewport);
        DrawWireLine(new[] { p000, p010 }, brush, diameter, bucket, viewport);
        DrawWireLine(new[] { p100, p110 }, brush, diameter, bucket, viewport);
        DrawWireLine(new[] { p001, p011 }, brush, diameter, bucket, viewport);
        DrawWireLine(new[] { p101, p111 }, brush, diameter, bucket, viewport);
        DrawWireLine(new[] { p000, p001 }, brush, diameter, bucket, viewport);
        DrawWireLine(new[] { p100, p101 }, brush, diameter, bucket, viewport);
        DrawWireLine(new[] { p010, p011 }, brush, diameter, bucket, viewport);
        DrawWireLine(new[] { p110, p111 }, brush, diameter, bucket, viewport);
    }

    private void DrawWireLine(IEnumerable<Vec3> points, Brush brush, double diameter, List<Visual3D> bucket, HelixViewport3D viewport)
    {
        var collection = new Point3DCollection(points.Select(p => new Point3D(p.X, p.Y, p.Z)));
        if (collection.Count < 2) return;
        var tube = new TubeVisual3D { Path = collection, Diameter = diameter, Fill = brush, IsPathClosed = false };
        bucket.Add(tube);
        viewport.Children.Add(tube);
    }

    private void AddTextLabel(string text, Vec3 position, List<Visual3D> bucket, HelixViewport3D viewport)
    {
        var visual = new BillboardTextVisual3D
        {
            Position = new Point3D(position.X, position.Y, position.Z),
            Text = text,
            Foreground = Brushes.White,
            Background = new SolidColorBrush(Color.FromArgb(180, 15, 23, 42)),
            FontSize = 13,
            Padding = new Thickness(5, 3, 5, 3),
            HorizontalAlignment = HorizontalAlignment.Center,
            VerticalAlignment = VerticalAlignment.Center
        };
        bucket.Add(visual);
        viewport.Children.Add(visual);
    }

    private void Viewport3D_PreviewMouseLeftButtonDown(object sender, System.Windows.Input.MouseButtonEventArgs e)
    {
        _viewportMouseDownPos = e.GetPosition(Viewport3D);
    }

    private void Viewport3D_PreviewMouseLeftButtonUp(object sender, System.Windows.Input.MouseButtonEventArgs e)
    {
        var pos = e.GetPosition(Viewport3D);
        if ((pos - _viewportMouseDownPos).Length > 4) return;
        PickVisualAt(pos);
    }

    private void PickVisualAt(Point position)
    {
        if (VisualTreeHelper.HitTest(Viewport3D.Viewport, position) is not RayMeshGeometry3DHitTestResult hit) return;
        if (hit.VisualHit is not Visual3D visual) return;
        if (!_visualOwners.TryGetValue(visual, out var owner)) return;

        if (LvwPathSegments != null && LvwPathSegments.ItemsSource is IEnumerable<PathSegmentRow> rows)
        {
            var match = System.Linq.Enumerable.FirstOrDefault(rows, r => r.AssociatedMetadata == owner);
            if (match != null)
            {
                LvwPathSegments.SelectedItem = match;
                LvwPathSegments.ScrollIntoView(match);
                return;
            }
        }

        HighlightVisualsWithOwner(owner);

        if (owner is PipeObjectMetadata pipeMeta)
        {
            ShowPipeAttributes(pipeMeta);
        }
        else if (owner is FittingObjectMetadata fittingMeta)
        {
            ShowFittingAttributes(fittingMeta);
        }
    }

    private void LvwPathSegments_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (LvwPathSegments.SelectedItem is not PathSegmentRow selected) return;
        if (selected.AssociatedMetadata == null) return;

        var owner = selected.AssociatedMetadata;

        HighlightVisualsWithOwner(owner);

        if (owner is PipeObjectMetadata pipeMeta)
        {
            ShowPipeAttributes(pipeMeta);
        }
        else if (owner is FittingObjectMetadata fittingMeta)
        {
            ShowFittingAttributes(fittingMeta);
        }
    }

    private void HighlightVisualsWithOwner(object owner)
    {
        RestoreSelectedVisuals();

        foreach (var pair in _visualOwners)
        {
            if (pair.Value == owner)
            {
                var visual = pair.Key;
                if (visual is TubeVisual3D tube)
                {
                    _selectedVisuals.Add(tube);
                    _originalBrushes.Add(tube.Fill);
                    tube.Fill = Brushes.Yellow;
                }
                else if (visual is SphereVisual3D sphere)
                {
                    _selectedVisuals.Add(sphere);
                    _originalBrushes.Add(sphere.Fill);
                    sphere.Fill = Brushes.Yellow;
                }
            }
        }
    }

    private void RestoreSelectedVisuals()
    {
        for (int i = 0; i < _selectedVisuals.Count; i++)
        {
            var visual = _selectedVisuals[i];
            var brush = _originalBrushes[i];
            if (visual is TubeVisual3D tube)
            {
                tube.Fill = brush;
            }
            else if (visual is SphereVisual3D sphere)
            {
                sphere.Fill = brush;
            }
        }
        _selectedVisuals.Clear();
        _originalBrushes.Clear();
    }

    private void ShowPipeAttributes(PipeObjectMetadata metadata)
    {
        PnlAttributes.Children.Clear();

        void AddRow(string label, string value, Brush valBrush = null)
        {
            var sp = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 2, 0, 2) };
            sp.Children.Add(new TextBlock { Text = label + ": ", Foreground = Brushes.SlateGray, FontSize = 11, Width = 80 });
            sp.Children.Add(new TextBlock { Text = value, Foreground = valBrush ?? Brushes.White, FontSize = 11, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap, Width = 190 });
            PnlAttributes.Children.Add(sp);
        }

        AddRow("객체 종류", "직선 배관 (Straight Pipe)", Brushes.Yellow);
        AddRow("소속 유틸", $"[{metadata.Path.Group}] {metadata.Path.Utility}", Brushes.Cyan);
        AddRow("배관 크기", $"{metadata.Path.DiameterMm:F0} mm");
        AddRow("배관 길이", $"{metadata.Length:N0} mm", Brushes.LimeGreen);
        AddRow("흐름 방향", metadata.Direction, Brushes.Orange);
        AddRow("소속 구간", metadata.SectionName);
        AddRow("시작 좌표", $"({metadata.Start.X:F0}, {metadata.Start.Y:F0}, {metadata.Start.Z:F0})");
        AddRow("끝 좌표", $"({metadata.End.X:F0}, {metadata.End.Y:F0}, {metadata.End.Z:F0})");
        AddRow("소속 경로", metadata.Path.RoutePathGuid);
    }

    private void ShowFittingAttributes(FittingObjectMetadata metadata)
    {
        PnlAttributes.Children.Clear();

        void AddRow(string label, string value, Brush valBrush = null)
        {
            var sp = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 2, 0, 2) };
            sp.Children.Add(new TextBlock { Text = label + ": ", Foreground = Brushes.SlateGray, FontSize = 11, Width = 80 });
            sp.Children.Add(new TextBlock { Text = value, Foreground = valBrush ?? Brushes.White, FontSize = 11, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap, Width = 190 });
            PnlAttributes.Children.Add(sp);
        }

        AddRow("객체 종류", metadata.TypeName, Brushes.Yellow);
        AddRow("소속 유틸", $"[{metadata.Path.Group}] {metadata.Path.Utility}", Brushes.Cyan);
        if (metadata.TypeName.Contains("엘보"))
        {
            AddRow("굴곡 각도", $"{metadata.Angle:F1} °", Brushes.Orange);
        }
        AddRow("배관 크기", $"{metadata.Path.DiameterMm:F0} mm");
        AddRow("소속 구간", metadata.SectionName);
        AddRow("자재 위치", $"({metadata.Position.X:F0}, {metadata.Position.Y:F0}, {metadata.Position.Z:F0})");
        AddRow("소속 경로", metadata.Path.RoutePathGuid);
    }

    internal class PipeObjectMetadata
    {
        public Vec3 Start { get; }
        public Vec3 End { get; }
        public double Length { get; }
        public string Direction { get; }
        public string SectionName { get; }
        public ExistingRoutePath Path { get; }

        public PipeObjectMetadata(Vec3 start, Vec3 end, double length, string direction, string sectionName, ExistingRoutePath path)
        {
            Start = start;
            End = end;
            Length = length;
            Direction = direction;
            SectionName = sectionName;
            Path = path;
        }
    }

    internal class FittingObjectMetadata
    {
        public Vec3 Position { get; }
        public double Angle { get; }
        public Vec3 Incoming { get; }
        public Vec3 Outgoing { get; }
        public string TypeName { get; }
        public string SectionName { get; }
        public ExistingRoutePath Path { get; }

        public FittingObjectMetadata(Vec3 position, double angle, Vec3 incoming, Vec3 outgoing, string typeName, string sectionName, ExistingRoutePath path)
        {
            Position = position;
            Angle = angle;
            Incoming = incoming;
            Outgoing = outgoing;
            TypeName = typeName;
            SectionName = sectionName;
            Path = path;
        }
    }

    private void PopulatePathDetailsTab(ExistingRoutePath path)
    {
        if (TxtPathInfoTitle == null || LvwPathSegments == null) return;

        TxtPathInfoTitle.Text = $"경로: [{path.Group}] {path.Utility} ({path.DiameterMm:F0}mm)";

        var list = new List<PathSegmentRow>();
        var seq = 1;

        foreach (var obj in _orderedPathObjects)
        {
            if (obj is PipeObjectMetadata pipe)
            {
                list.Add(new PathSegmentRow
                {
                    Seq = seq++,
                    TypeName = "직선 배관",
                    LengthOrAngle = $"{pipe.Length:N0} mm",
                    SizeText = $"{pipe.Path.DiameterMm:F0} mm",
                    AssociatedMetadata = pipe
                });
            }
            else if (obj is FittingObjectMetadata fitting)
            {
                string lenAngle = "-";
                if (fitting.TypeName.Contains("엘보"))
                {
                    lenAngle = $"{fitting.Angle:F1}°";
                }

                list.Add(new PathSegmentRow
                {
                    Seq = seq++,
                    TypeName = fitting.TypeName,
                    LengthOrAngle = lenAngle,
                    SizeText = $"{fitting.Path.DiameterMm:F0} mm",
                    AssociatedMetadata = fitting
                });
            }
        }

        LvwPathSegments.ItemsSource = list;
    }

    public class PathSegmentRow
    {
        public int Seq { get; set; }
        public string TypeName { get; set; } = string.Empty;
        public string LengthOrAngle { get; set; } = string.Empty;
        public string SizeText { get; set; } = string.Empty;
        public object AssociatedMetadata { get; set; }
    }
}
