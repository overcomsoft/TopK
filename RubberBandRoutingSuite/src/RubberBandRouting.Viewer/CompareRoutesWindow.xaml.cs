using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Media3D;
using HelixToolkit.Wpf;
using RubberBandRouting.Engine;

namespace RubberBandRouting.Viewer;

/// <summary>
/// One row of the comparison list: a routed task's matched existing-design polyline (if any)
/// alongside the freshly computed auto-route segments.
/// </summary>
internal sealed record RouteCompareEntry(
    int Index,
    string Group,
    string Utility,
    string StartPoC,
    string EndPoC,
    string MatchNote,
    List<Vec3> ExistingPoints,
    List<RouteSegment> AutoSegments,
    List<SegmentInfoRow> ExistingSteps,
    List<SegmentInfoRow> AutoSteps,
    List<RouteFeature> FeatureWaypoints)
{
    public bool HasExisting => ExistingPoints.Count >= 2;
    public double ExistingLength => PolylineLength(ExistingPoints);
    public double AutoLength => AutoSegments.Sum(s => s.Length);
    public int ExistingSegmentCount => Math.Max(0, ExistingPoints.Count - 1);
    public int AutoSegmentCount => AutoSegments.Count;
    public int ExistingVerticalBends => CountVerticalBends(ExistingPoints);
    public int AutoVerticalBends => CountVerticalBends(AutoPoints);

    public List<Vec3> AutoPoints =>
        AutoSegments.Count == 0 ? new List<Vec3>() : new[] { AutoSegments[0].Start }.Concat(AutoSegments.Select(s => s.End)).ToList();

    private static double PolylineLength(List<Vec3> points)
    {
        var total = 0.0;
        for (var i = 1; i < points.Count; i++) total += (points[i] - points[i - 1]).Length;
        return total;
    }

    private static int CountVerticalBends(List<Vec3> points)
    {
        if (points.Count < 3) return 0;
        var count = 0;
        bool? prevVertical = null;
        for (var i = 1; i < points.Count; i++)
        {
            var d = points[i] - points[i - 1];
            if (d.Length < 1) continue;
            var vertical = Math.Abs(d.Z) > Math.Max(Math.Abs(d.X), Math.Abs(d.Y));
            if (prevVertical.HasValue && prevVertical.Value != vertical) count++;
            prevVertical = vertical;
        }
        return count;
    }
}

internal sealed record AnalysisCompareRow(string Name, string ExistingValue, string AutoValue, string DiffValue);

/// <summary>
/// Mirrors MainWindow's "단계별 경로" tab shape (StepDetailRow) so the comparison dialog's two
/// per-viewport segment grids show the same level of detail as the main routing result view.
/// </summary>
internal sealed record SegmentInfoRow(int Index, string SegmentType, string Start, string End, string Direction, double LengthMm, string Reason)
{
    public string LengthText => $"{LengthMm:N0}";
}

/// <summary>
/// Non-modal dialog opened from MainWindow's "⇄ 기존경로와 비교" button. Left: list of routed
/// tasks with a matched existing-design path. Selecting one draws both polylines segment-by-segment
/// (one distinct color per segment) in two side-by-side 3D views, each with its own segment grid to
/// the right; selecting a grid row highlights that single segment in its 3D view. A comparison
/// table underneath summarizes length/bend differences.
/// </summary>
public partial class CompareRoutesWindow : Window
{
    private static readonly Color[] SegmentColors =
    {
        Color.FromRgb(56, 189, 248), Color.FromRgb(244, 114, 182), Color.FromRgb(163, 230, 53),
        Color.FromRgb(250, 204, 21), Color.FromRgb(167, 139, 250), Color.FromRgb(45, 212, 191),
        Color.FromRgb(251, 146, 60), Color.FromRgb(248, 113, 113)
    };

    private readonly List<Visual3D> _existingVisuals = new();
    private readonly List<Visual3D> _autoVisuals = new();
    private RouteCompareEntry? _currentEntry;
    private int? _existingHighlightIndex;
    private int? _autoHighlightIndex;
    private bool _initialFitDone;
    private bool _isSyncingCameras;

    internal CompareRoutesWindow(IReadOnlyList<RouteCompareEntry> entries)
    {
        InitializeComponent();
        GridEntries.ItemsSource = entries;
        if (entries.Count > 0) GridEntries.SelectedIndex = 0;
        // The very first fit can land on a still-unmeasured viewport (0-size layout) if triggered
        // from the constructor/SelectionChanged before the window has actually been shown, so the
        // route ends up drawn but framed by a stale/default camera. ContentRendered guarantees a
        // real layout+render pass has happened, so re-fit both viewports once there.
        ContentRendered += (_, _) => FitBothOnce();
    }

    private void FitBothOnce()
    {
        if (_initialFitDone) return;
        _initialFitDone = true;
        if (_currentEntry == null) return;
        FitViewportToPoints(ViewportExisting, _currentEntry.ExistingPoints);
        FitViewportToPoints(ViewportAuto, _currentEntry.AutoPoints);
    }

    private void BtnFitExisting_Click(object sender, RoutedEventArgs e)
    {
        if (_currentEntry != null) FitViewportToPoints(ViewportExisting, _currentEntry.ExistingPoints);
    }

    private void BtnFitAuto_Click(object sender, RoutedEventArgs e)
    {
        if (_currentEntry != null) FitViewportToPoints(ViewportAuto, _currentEntry.AutoPoints);
    }

    private void ChkSyncCamera_Checked(object sender, RoutedEventArgs e)
    {
        SyncCameras(fromExisting: true);
    }

    private void ChkSyncCamera_Unchecked(object sender, RoutedEventArgs e)
    {
    }

    private void ViewportExisting_CameraChanged(object sender, RoutedEventArgs e)
    {
        if (_isSyncingCameras || ChkSyncCamera?.IsChecked != true) return;
        SyncCameras(fromExisting: true);
    }

    private void ViewportAuto_CameraChanged(object sender, RoutedEventArgs e)
    {
        if (_isSyncingCameras || ChkSyncCamera?.IsChecked != true) return;
        SyncCameras(fromExisting: false);
    }

    private void SyncCameras(bool fromExisting)
    {
        if (_isSyncingCameras) return;
        _isSyncingCameras = true;
        try
        {
            var source = fromExisting ? ViewportExisting : ViewportAuto;
            var target = fromExisting ? ViewportAuto : ViewportExisting;
            if (source?.Camera is ProjectionCamera srcProj && target?.Camera is ProjectionCamera tgtProj)
            {
                CopyCamera(srcProj, tgtProj);
            }
        }
        finally
        {
            _isSyncingCameras = false;
        }
    }

    private static void CopyCamera(ProjectionCamera source, ProjectionCamera target)
    {
        if (source == null || target == null) return;
        target.Position = source.Position;
        target.LookDirection = source.LookDirection;
        target.UpDirection = source.UpDirection;
        target.NearPlaneDistance = source.NearPlaneDistance;
        target.FarPlaneDistance = source.FarPlaneDistance;

        if (source is PerspectiveCamera srcPersp && target is PerspectiveCamera tgtPersp)
        {
            tgtPersp.FieldOfView = srcPersp.FieldOfView;
        }
        else if (source is OrthographicCamera srcOrtho && target is OrthographicCamera tgtOrtho)
        {
            tgtOrtho.Width = srcOrtho.Width;
        }
    }

    /// <summary>
    /// HelixViewport3D.ZoomExtents() fits the camera to EVERY visual in the viewport, including the
    /// always-present 30000x30000 GridLinesVisual3D reference grid — since the actual pipe route is
    /// tiny next to that grid, the camera zoomed out to the grid's scale and the route appeared as a
    /// speck in a corner. Computing the fit from the route's own points (same math as MainWindow's
    /// FitProjectToViewport) targets the route itself instead of the whole visual tree.
    /// </summary>
    private static void FitViewportToPoints(HelixViewport3D viewport, IReadOnlyList<Vec3> points)
    {
        if (points.Count == 0 || viewport.Camera is not PerspectiveCamera camera)
        {
            viewport.ZoomExtents(200);
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

    private void GridEntries_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        _currentEntry = GridEntries.SelectedItem as RouteCompareEntry;
        _existingHighlightIndex = null;
        _autoHighlightIndex = null;

        if (_currentEntry == null)
        {
            GridAnalysis.ItemsSource = null;
            GridExistingSegments.ItemsSource = null;
            GridAutoSegments.ItemsSource = null;
            return;
        }

        RedrawExisting(fitCamera: true);
        RedrawAuto(fitCamera: true);
        GridAnalysis.ItemsSource = BuildAnalysis(_currentEntry);
        GridExistingSegments.ItemsSource = _currentEntry.ExistingSteps;
        GridAutoSegments.ItemsSource = _currentEntry.AutoSteps;
    }

    private void GridExistingSegments_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        _existingHighlightIndex = (GridExistingSegments.SelectedItem as SegmentInfoRow)?.Index - 1;
        RedrawExisting(fitCamera: false);
    }

    private void GridAutoSegments_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        _autoHighlightIndex = (GridAutoSegments.SelectedItem as SegmentInfoRow)?.Index - 1;
        RedrawAuto(fitCamera: false);
    }

    private void RedrawExisting(bool fitCamera)
    {
        if (_currentEntry == null) return;
        DrawPolyline(_currentEntry.ExistingPoints, _existingVisuals, ViewportExisting, _existingHighlightIndex, fitCamera);
        // The feature waypoints the auto-router pulled from the matched existing design are shown
        // on both sides — they're real 3D positions sampled from the existing polyline itself, so
        // seeing them against the existing route too makes it clear where each one came from.
        DrawFeatureMarkers(_currentEntry.FeatureWaypoints, _existingVisuals, ViewportExisting);
    }

    private void RedrawAuto(bool fitCamera)
    {
        if (_currentEntry == null) return;
        DrawPolyline(_currentEntry.AutoPoints, _autoVisuals, ViewportAuto, _autoHighlightIndex, fitCamera);
        DrawFeatureMarkers(_currentEntry.FeatureWaypoints, _autoVisuals, ViewportAuto);
    }

    private static readonly Dictionary<RouteFeatureRole, Color> FeatureRoleColors = new()
    {
        [RouteFeatureRole.StartStub] = Colors.OrangeRed,
        [RouteFeatureRole.Bend] = Colors.Magenta,
        [RouteFeatureRole.ElevationChange] = Colors.Cyan,
        [RouteFeatureRole.TrunkGuide] = Colors.MediumPurple,
        [RouteFeatureRole.EndApproach] = Colors.LimeGreen
    };

    private static void DrawFeatureMarkers(List<RouteFeature> features, List<Visual3D> bucket, HelixViewport3D viewport)
    {
        const double size = 120;
        foreach (var feature in features)
        {
            var color = FeatureRoleColors.TryGetValue(feature.Role, out var c) ? c : Colors.Magenta;
            var box = new BoxVisual3D
            {
                Center = ToPoint3D(feature.Position),
                Length = size,
                Width = size,
                Height = size,
                Fill = new SolidColorBrush(Color.FromArgb(128, color.R, color.G, color.B))
            };
            bucket.Add(box);
            viewport.Children.Add(box);
        }
    }

    private void DrawPolyline(List<Vec3> points, List<Visual3D> bucket, HelixViewport3D viewport, int? highlightIndex, bool fitCamera)
    {
        ClearVisuals(bucket, viewport);
        for (var i = 0; i < points.Count - 1; i++) AddSegment(points[i], points[i + 1], i, bucket, viewport, i == highlightIndex);
        foreach (var p in points) AddVertexMarker(p, bucket, viewport, false);
        if (highlightIndex is int hi && hi >= 0 && hi < points.Count - 1)
        {
            AddVertexMarker(points[hi], bucket, viewport, true);
            AddVertexMarker(points[hi + 1], bucket, viewport, true);
        }
        // Deferring this to the dispatcher (e.g. ContextIdle) turned out to be less reliable than
        // fitting immediately here — the window's own ContentRendered handler (see FitBothOnce)
        // covers the one case where an immediate fit can't work yet (before the window is shown).
        if (fitCamera) FitViewportToPoints(viewport, points);
    }

    private static void AddSegment(Vec3 a, Vec3 b, int index, List<Visual3D> bucket, HelixViewport3D viewport, bool highlighted)
    {
        var color = highlighted ? Colors.Yellow : SegmentColors[index % SegmentColors.Length];
        var tube = new TubeVisual3D
        {
            Path = new Point3DCollection(new[] { ToPoint3D(a), ToPoint3D(b) }),
            Diameter = highlighted ? 170 : 80,
            Fill = new SolidColorBrush(color)
        };
        bucket.Add(tube);
        viewport.Children.Add(tube);
    }

    private static void AddVertexMarker(Vec3 p, List<Visual3D> bucket, HelixViewport3D viewport, bool highlighted)
    {
        var sphere = new SphereVisual3D
        {
            Center = ToPoint3D(p),
            Radius = highlighted ? 130 : 60,
            Fill = highlighted ? Brushes.Red : Brushes.White
        };
        bucket.Add(sphere);
        viewport.Children.Add(sphere);
    }

    private static void ClearVisuals(List<Visual3D> bucket, HelixViewport3D viewport)
    {
        foreach (var v in bucket) viewport.Children.Remove(v);
        bucket.Clear();
    }

    private static List<AnalysisCompareRow> BuildAnalysis(RouteCompareEntry entry)
    {
        var rows = new List<AnalysisCompareRow> { new("매칭 상태", entry.MatchNote, "-", "-") };

        if (!entry.HasExisting)
        {
            rows.Add(new AnalysisCompareRow("참고", "이 경로에 매칭되는 기존설계 경로가 없어 자동경로만 표시합니다.", "-", "-"));
            return rows;
        }

        var segDiff = entry.AutoSegmentCount - entry.ExistingSegmentCount;
        rows.Add(new AnalysisCompareRow(
            "세그먼트 수",
            entry.ExistingSegmentCount.ToString(CultureInfo.InvariantCulture),
            entry.AutoSegmentCount.ToString(CultureInfo.InvariantCulture),
            Signed(segDiff)));

        var lenDiff = entry.AutoLength - entry.ExistingLength;
        rows.Add(new AnalysisCompareRow(
            "총 길이 (mm)",
            entry.ExistingLength.ToString("N0", CultureInfo.InvariantCulture),
            entry.AutoLength.ToString("N0", CultureInfo.InvariantCulture),
            Signed(lenDiff, "N0")));

        if (entry.ExistingLength > 1)
        {
            var pct = lenDiff / entry.ExistingLength * 100.0;
            rows.Add(new AnalysisCompareRow("길이 변화율", "-", "-", Signed(pct, "N1") + "%"));
        }

        var bendDiff = entry.AutoVerticalBends - entry.ExistingVerticalBends;
        rows.Add(new AnalysisCompareRow(
            "수직 Bend 수",
            entry.ExistingVerticalBends.ToString(CultureInfo.InvariantCulture),
            entry.AutoVerticalBends.ToString(CultureInfo.InvariantCulture),
            Signed(bendDiff)));

        if (entry.AutoSegments.Count > 0)
        {
            var startGap = (entry.ExistingPoints[0] - entry.AutoSegments[0].Start).Length;
            var endGap = (entry.ExistingPoints[^1] - entry.AutoSegments[^1].End).Length;
            rows.Add(new AnalysisCompareRow(
                "시작/종단 위치 오차 (mm)",
                "-", "-",
                $"{startGap:N0} / {endGap:N0}"));
        }

        return rows;
    }

    private static string Signed(double value, string format = "0") =>
        (value > 0 ? "+" : string.Empty) + value.ToString(format, CultureInfo.InvariantCulture);

    private static Point3D ToPoint3D(Vec3 p) => new(p.X, p.Y, p.Z);
}
