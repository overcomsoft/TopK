using System.Collections.Generic;
using System.Windows.Media.Media3D;
using HelixToolkit.Wpf;

namespace GroupPatternViewer.Models
{
    public class SegmentClusterModel
    {
        public int GroupId { get; set; }
        public string EquipmentTag { get; set; } = string.Empty;
        public string UtilityGroup { get; set; } = string.Empty;
        public string Direction { get; set; } = string.Empty;
        public double RepX { get; set; }
        public double RepY { get; set; }
        public double RepZ { get; set; }
        public double RepLength { get; set; }
        public int SegmentCount { get; set; }
        
        public string DisplaySummary => $"[{UtilityGroup}] {EquipmentTag}";
        
        public List<(Point3D StartPt, Point3D EndPt)> Segments { get; set; } = new();
        
        // 시각화 객체를 보관 (클릭 시 하이라이트를 위함)
        public List<TubeVisual3D> Visuals { get; set; } = new();
    }
}
