using System.Collections.Generic;

namespace AutoRouteFinder.Models
{
    /// <summary>격자 메타(셀 크기/원점/셀 개수). 단위 mm.</summary>
    public sealed class GridMeta
    {
        public double CellMm { get; set; } = 50.0;
        public double Ox { get; set; }
        public double Oy { get; set; }
        public double Oz { get; set; }
        public int Nx { get; set; } = 1;
        public int Ny { get; set; } = 1;
        public int Nz { get; set; } = 1;
    }

    /// <summary>장애물 AABB(mm).</summary>
    public sealed class ObstacleBox
    {
        public string Name { get; set; } = string.Empty;
        public string DdworksType { get; set; } = string.Empty;
        public string OstType { get; set; } = string.Empty;
        public double MinX, MinY, MinZ, MaxX, MaxY, MaxZ;

        public bool? PassThroughOverride { get; set; }

        public bool IsPassThrough
        {
            get
            {
                if (PassThroughOverride.HasValue) return PassThroughOverride.Value;
                var ost = (OstType ?? string.Empty).Trim();
                if (string.Equals(ost, "OST_Floors", System.StringComparison.OrdinalIgnoreCase)) return true;
                if (string.Equals(ost, "OST_Ceilings", System.StringComparison.OrdinalIgnoreCase)) return true;
                if (string.Equals(ost, "OST_StructuralFraming", System.StringComparison.OrdinalIgnoreCase) &&
                    string.Equals((DdworksType ?? string.Empty).Trim(), "BEAM_STRUCTURE", System.StringComparison.OrdinalIgnoreCase))
                    return true;
                return false;
            }
        }
    }

    /// <summary>라우팅 작업(start→end, 유틸리티 메타).</summary>
    public sealed class TaskInfo
    {
        public string? RoutePathGuid { get; set; }
        public double Sx, Sy, Sz, Gx, Gy, Gz;
        public string? Utility { get; set; }
        public string? Group { get; set; }
        public string? EquipmentTag { get; set; }

        public string? PocName { get; set; }
        public string? EndName { get; set; }
        public double DiameterMm { get; set; }

        public string UtilityLabel =>
            $"[{(string.IsNullOrEmpty(Group) ? "?" : Group)}] {(string.IsNullOrEmpty(Utility) ? "?" : Utility)}";
    }

    /// <summary>공간 영역(TB_BIM_SPACE_INFO) — 층/구역(CR, A/F, CSF 등) AABB(mm) + 이름.</summary>
    public sealed class SpaceArea
    {
        public string Name { get; set; } = string.Empty;
        public double MinX, MinY, MinZ, MaxX, MaxY, MaxZ;
    }

    /// <summary>장비(TB_BIM_EQUIPMENT) — AABB(mm) + 이름 + 메인 여부.</summary>
    public sealed class EquipmentBox
    {
        public string Name { get; set; } = string.Empty;
        public bool IsMain { get; set; }
        public double MinX, MinY, MinZ, MaxX, MaxY, MaxZ;
    }

    /// <summary>덕트/레터럴(TB_BIM_DUCT_LATERAL) — AABB(mm) + 카테고리(DUCT/LATERAL) + 유틸리티.</summary>
    public sealed class DuctLateral
    {
        public string Name { get; set; } = string.Empty;
        public string Category { get; set; } = string.Empty;
        public string? Utility { get; set; }
        public double MinX, MinY, MinZ, MaxX, MaxY, MaxZ;

        public bool IsLateral => string.Equals(Category, "LATERAL", System.StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>기존 설계배관 한 줄(TB_ROUTE_PATH 폴리라인). 좌표는 월드 mm.</summary>
    public sealed class ExistingPipe
    {
        public List<Pt3> Points { get; } = new();
        public string? RoutePathGuid { get; set; }
        public string? Utility { get; set; }
        public string? Group { get; set; }
        public double DiameterMm { get; set; }

        public Pt3? SourcePos { get; set; }
        public Pt3? TargetPos { get; set; }

        public string Label =>
            $"[{(string.IsNullOrEmpty(Group) ? "?" : Group)}] {(string.IsNullOrEmpty(Utility) ? "?" : Utility)}";
    }

    /// <summary>배관 자재(연결부) 1개 — 실제 부속(ELBOW/TEE/VALVE/FLANGE 등). 위치=FROM/TO 중점(월드 mm).</summary>
    public sealed class PipeFitting
    {
        public string Type { get; set; } = string.Empty;
        public string? Size { get; set; }
        public double X, Y, Z;
        public string? Utility { get; set; }
        public double DiameterMm { get; set; }
    }

    /// <summary>3D 점(월드 mm).</summary>
    public struct Pt3
    {
        public double X, Y, Z;
        public Pt3(double x, double y, double z) { X = x; Y = y; Z = z; }
    }

    /// <summary>프로젝트 씬 데이터.</summary>
    public sealed class SceneData
    {
        public GridMeta Grid { get; set; } = new();
        public List<ObstacleBox> Obstacles { get; } = new();
        public List<TaskInfo> Tasks { get; } = new();
        public List<SpaceArea> Spaces { get; } = new();
        public List<EquipmentBox> Equipment { get; } = new();
        public List<DuctLateral> DuctsLaterals { get; } = new();
        public List<ExistingPipe> ExistingPipes { get; } = new();
        public List<PipeFitting> Fittings { get; } = new();
        public string SourceFile { get; set; } = string.Empty;
        public string RawText { get; set; } = string.Empty;
    }
}
