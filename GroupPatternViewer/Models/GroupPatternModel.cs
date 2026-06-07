using System.Collections.Generic;

namespace GroupPatternViewer.Models
{
    public class GroupPatternModel
    {
        public string GroupId { get; set; } = string.Empty;
        public string TagGroupNm { get; set; } = string.Empty;
        public string Utility { get; set; } = string.Empty;
        public string UtilityGroup { get; set; } = string.Empty;
        public int NMembers { get; set; }

        public double AvgSimilarity { get; set; }
        public double TrunkZ { get; set; }
        public double TrunkXySpread { get; set; }
        public double PitchMm { get; set; }
        public int NOrthoBends { get; set; }
        public string MemberGuidsJson { get; set; } = "[]";
        public string PatternSeq { get; set; } = string.Empty;
        public string SectionBoundsJson { get; set; } = "[]";
        public string PocList { get; set; } = string.Empty;
    }

    public class SegmentDetailRow
    {
        public string RoutePathGuid { get; set; } = string.Empty;
        public double FromX { get; set; }
        public double FromY { get; set; }
        public double FromZ { get; set; }
        public double ToX { get; set; }
        public double ToY { get; set; }
        public double ToZ { get; set; }
        public int SegOrder { get; set; }
        public int DetailOrder { get; set; }
        public double DiameterMm { get; set; }
    }

    public class SectionBound
    {
        public List<double> Min { get; set; } = new();
        public List<double> Max { get; set; } = new();
        public string Type { get; set; } = string.Empty;
    }
}
