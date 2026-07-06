using System;
using System.Collections.Generic;
using Npgsql;
using AutoRoutingLibrary.Models;
using AutoRoutingLibrary.Core;

namespace AutoRouteFinder.Models
{
    public sealed class ProjectInfo
    {
        public int ProjectId { get; init; }
        public string GroupId { get; init; } = string.Empty;
        public string GroupName { get; init; } = string.Empty;
        public string? Bay { get; init; }
        public string? Process { get; init; }
        public double MinX, MinY, MinZ, MaxX, MaxY, MaxZ;

        public string SourceFile => GroupName;

        public string Display => $"{GroupName} / {Bay ?? "?"} / {Process ?? "?"}";

        public override string ToString() => Display;
    }

    public sealed class FeatureProfileRow
    {
        public string ProjectId { get; init; } = string.Empty;
        public string UtilityGroup { get; init; } = string.Empty;
        public string PreferredSourceFace { get; init; } = "Any";
        public string PreferredTargetFace { get; init; } = "Any";
        public List<double> PreferredRackZs { get; init; } = new();
        public string TrunkCenterlineJson { get; init; } = "[]";
        public int TopKCount { get; set; } = 0;
    }

    public static class ObstacleDbLoader
    {
        private const double ScopeMarginMm = 500.0;

        public static List<ProjectInfo> ListProjects(DbConfig config)
        {
            var list = new List<ProjectInfo>();
            using var conn = new NpgsqlConnection(config.ConnectionString);
            conn.Open();
            using var cmd = new NpgsqlCommand(
                @"SELECT ""TAG_GROUP_ID"",""TAG_GROUP_NM"",""BAY_GROUP_NM"",""PROCESS_GROUP_NM"",
                         ""AABB_MINX"",""AABB_MINY"",""AABB_MINZ"",""AABB_MAXX"",""AABB_MAXY"",""AABB_MAXZ""
                  FROM ""TB_SPACE_GROUP_INFO""
                  ORDER BY ""PROCESS_GROUP_NM"",""TAG_GROUP_NM""", conn);
            using var r = cmd.ExecuteReader();
            int seq = 1;
            while (r.Read())
            {
                list.Add(new ProjectInfo
                {
                    ProjectId = seq++,
                    GroupId = r.IsDBNull(0) ? string.Empty : r.GetString(0),
                    GroupName = r.IsDBNull(1) ? string.Empty : r.GetString(1),
                    Bay = r.IsDBNull(2) ? null : r.GetString(2),
                    Process = r.IsDBNull(3) ? null : r.GetString(3),
                    MinX = Dbl(r, 4), MinY = Dbl(r, 5), MinZ = Dbl(r, 6),
                    MaxX = Dbl(r, 7), MaxY = Dbl(r, 8), MaxZ = Dbl(r, 9),
                });
            }
            return list;
        }

        public static SceneData LoadScene(DbConfig config, int projectId, double cellMm = 25.0, bool connectedOnly = true)
        {
            var projects = ListProjects(config);
            var proj = projects.Find(p => p.ProjectId == projectId)
                       ?? throw new InvalidOperationException($"프로젝트 순번 {projectId} 가 없습니다.");
            return LoadScene(config, proj, cellMm, connectedOnly);
        }

        public static SceneData LoadScene(DbConfig config, ProjectInfo proj, double cellMm = 25.0, bool connectedOnly = true)
        {
            using var conn = new NpgsqlConnection(config.ConnectionString);
            conn.Open();

            double minx = proj.MinX - ScopeMarginMm, maxx = proj.MaxX + ScopeMarginMm;
            double miny = proj.MinY - ScopeMarginMm, maxy = proj.MaxY + ScopeMarginMm;
            double minz = proj.MinZ - ScopeMarginMm, maxz = proj.MaxZ + ScopeMarginMm;
            var data = new SceneData { SourceFile = proj.GroupName };

            void SetXY(NpgsqlCommand c)
            {
                c.Parameters.AddWithValue("@minx", minx); c.Parameters.AddWithValue("@maxx", maxx);
                c.Parameters.AddWithValue("@miny", miny); c.Parameters.AddWithValue("@maxy", maxy);
            }
            const string IsectXY =
                @" ""AABB_MINX""<=@maxx AND ""AABB_MAXX"">=@minx AND ""AABB_MINY""<=@maxy AND ""AABB_MAXY"">=@miny ";

            // 1) 장애물
            using (var cmd = new NpgsqlCommand(
                @"SELECT ""AABB_MINX"",""AABB_MINY"",""AABB_MINZ"",""AABB_MAXX"",""AABB_MAXY"",""AABB_MAXZ"",
                         ""INSTANCE_NAME"",""OST_TYPE"",""DDWORKS_TYPE"",""COLLISION_PASS""
                  FROM ""TB_BIM_OBSTACLE"" WHERE" + IsectXY, conn))
            {
                SetXY(cmd);
                using var r = cmd.ExecuteReader();
                while (r.Read())
                {
                    double mnx = Dbl(r, 0), mny = Dbl(r, 1), mnz = Dbl(r, 2);
                    double mxx = Dbl(r, 3), mxy = Dbl(r, 4), mxz = Dbl(r, 5);
                    if (mxx <= mnx || mxy <= mny || mxz <= mnz) continue;
                    string name = r.IsDBNull(6) ? string.Empty : r.GetString(6);
                    if (name.IndexOf("damper", StringComparison.OrdinalIgnoreCase) >= 0) continue;
                    mnx = Math.Max(mnx, minx); mny = Math.Max(mny, miny); mnz = Math.Max(mnz, minz);
                    mxx = Math.Min(mxx, maxx); mxy = Math.Min(mxy, maxy); mxz = Math.Min(mxz, maxz);
                    if (mxx <= mnx || mxy <= mny || mxz <= mnz) continue;
                    data.Obstacles.Add(new ObstacleBox
                    {
                        MinX = mnx, MinY = mny, MinZ = mnz, MaxX = mxx, MaxY = mxy, MaxZ = mxz,
                        Name = name,
                        OstType = r.IsDBNull(7) ? string.Empty : r.GetString(7),
                        DdworksType = r.IsDBNull(8) ? string.Empty : r.GetString(8),
                        PassThroughOverride = r.IsDBNull(9) ? (bool?)null : (r.GetInt64(9) != 0),
                    });
                }
            }

            // 2) 장비
            using (var cmd = new NpgsqlCommand(
                @"SELECT ""INSTANCE_NAME"",""MAIN_SUB_TYPE"",
                         ""AABB_MINX"",""AABB_MINY"",""AABB_MINZ"",""AABB_MAXX"",""AABB_MAXY"",""AABB_MAXZ""
                  FROM ""TB_EQUIPMENTS"" WHERE" + IsectXY, conn))
            {
                SetXY(cmd);
                using var r = cmd.ExecuteReader();
                while (r.Read())
                {
                    double mnx = Dbl(r, 2), mny = Dbl(r, 3), mnz = Dbl(r, 4);
                    double mxx = Dbl(r, 5), mxy = Dbl(r, 6), mxz = Dbl(r, 7);
                    if (mxx <= mnx || mxy <= mny || mxz <= mnz) continue;
                    data.Equipment.Add(new EquipmentBox
                    {
                        Name = r.IsDBNull(0) ? string.Empty : r.GetString(0),
                        IsMain = !r.IsDBNull(1) && string.Equals(r.GetString(1), "MainTool", StringComparison.OrdinalIgnoreCase),
                        MinX = mnx, MinY = mny, MinZ = mnz, MaxX = mxx, MaxY = mxy, MaxZ = mxz,
                    });
                }
            }

            // 3) 종단
            LoadDuctLateral(conn, "TB_LATERAL_PIPE", "LATERAL", IsectXY, SetXY, data.DuctsLaterals);
            LoadDuctLateral(conn, "TB_DUCT", "DUCT", IsectXY, SetXY, data.DuctsLaterals);

            // 4) 공간
            using (var cmd = new NpgsqlCommand(
                @"SELECT ""SPACE_NAME"",""AABB_MINX"",""AABB_MINY"",""AABB_MINZ"",""AABB_MAXX"",""AABB_MAXY"",""AABB_MAXZ""
                  FROM ""TB_SPACE_INFO"" WHERE" + IsectXY + @" ORDER BY ""AABB_MINZ""", conn))
            {
                SetXY(cmd);
                using var r = cmd.ExecuteReader();
                while (r.Read())
                {
                    double smnx = Math.Max(Dbl(r, 1), proj.MinX), smny = Math.Max(Dbl(r, 2), proj.MinY), smnz = Math.Max(Dbl(r, 3), proj.MinZ);
                    double smxx = Math.Min(Dbl(r, 4), proj.MaxX), smxy = Math.Min(Dbl(r, 5), proj.MaxY), smxz = Math.Min(Dbl(r, 6), proj.MaxZ);
                    if (smxx <= smnx || smxy <= smny || smxz <= smnz) continue;
                    data.Spaces.Add(new SpaceArea
                    {
                        Name = r.IsDBNull(0) ? string.Empty : r.GetString(0),
                        MinX = smnx, MinY = smny, MinZ = smnz,
                        MaxX = smxx, MaxY = smxy, MaxZ = smxz,
                    });
                }
            }

            // 5) 작업 및 기존배관
            try { LoadRoutesAndTasks(conn, minx, maxx, miny, maxy, data); }
            catch { }

            // 6) 격자 메타
            double gzlo = minz, gzhi = maxz;
            foreach (var t in data.Tasks)
            {
                gzlo = Math.Min(gzlo, Math.Min(t.Sz, t.Gz) - ScopeMarginMm);
                gzhi = Math.Max(gzhi, Math.Max(t.Sz, t.Gz) + ScopeMarginMm);
            }
            data.Grid = ComputeGrid(minx, miny, gzlo, maxx, maxy, gzhi, cellMm);
            return data;
        }

        private static void LoadDuctLateral(NpgsqlConnection conn, string table, string category,
                                            string isectXY, Action<NpgsqlCommand> setXY, List<DuctLateral> outList)
        {
            using var cmd = new NpgsqlCommand(
                $@"SELECT ""INSTANCE_NAME"",""UTILITY"",
                          ""AABB_MINX"",""AABB_MINY"",""AABB_MINZ"",""AABB_MAXX"",""AABB_MAXY"",""AABB_MAXZ""
                   FROM ""{table}"" WHERE" + isectXY, conn);
            setXY(cmd);
            using var r = cmd.ExecuteReader();
            while (r.Read())
            {
                double mnx = Dbl(r, 2), mny = Dbl(r, 3), mnz = Dbl(r, 4);
                double mxx = Dbl(r, 5), mxy = Dbl(r, 6), mxz = Dbl(r, 7);
                if (mxx <= mnx || mxy <= mny || mxz <= mnz) continue;
                outList.Add(new DuctLateral
                {
                    Name = r.IsDBNull(0) ? string.Empty : r.GetString(0),
                    Category = category,
                    Utility = r.IsDBNull(1) ? null : r.GetString(1),
                    MinX = mnx, MinY = mny, MinZ = mnz, MaxX = mxx, MaxY = mxy, MaxZ = mxz,
                });
            }
        }

        private static void LoadRoutesAndTasks(NpgsqlConnection conn,
            double minx, double maxx, double miny, double maxy, SceneData data)
        {
            using var cmd = new NpgsqlCommand(
                @"SELECT s.""ROUTE_PATH_GUID"", rp.""UTILITY_GROUP"", rp.""SOURCE_UTILITY"", rp.""SOURCE_SIZE"",
                         rp.""EQUIPMENT_NAME"", rp.""TARGET_OWNER_NAME"",
                         sd.""FROM_POSX"", sd.""FROM_POSY"", sd.""FROM_POSZ"",
                         sd.""TO_POSX"",   sd.""TO_POSY"",   sd.""TO_POSZ"",
                         rp.""SOURCE_POSX"", rp.""SOURCE_POSY"", rp.""SOURCE_POSZ"",
                         rp.""TARGET_POSX"", rp.""TARGET_POSY"", rp.""TARGET_POSZ"",
                         sd.""TYPE"", rp.""EQUIPMENT_TAG""
                    FROM ""TB_ROUTE_SEGMENT_DETAIL"" sd
                    JOIN ""TB_ROUTE_SEGMENTS"" s ON s.""SEGMENT_GUID"" = sd.""SEGMENT_GUID""
                    JOIN ""TB_ROUTE_PATH"" rp    ON rp.""ROUTE_PATH_GUID"" = s.""ROUTE_PATH_GUID""
                   WHERE rp.""SOURCE_POSX"" BETWEEN @minx AND @maxx
                     AND rp.""SOURCE_POSY"" BETWEEN @miny AND @maxy
                   ORDER BY s.""ROUTE_PATH_GUID"", s.""ORDER"", sd.""ORDER""", conn);
            cmd.Parameters.AddWithValue("@minx", minx); cmd.Parameters.AddWithValue("@maxx", maxx);
            cmd.Parameters.AddWithValue("@miny", miny); cmd.Parameters.AddWithValue("@maxy", maxy);

            using var r = cmd.ExecuteReader();
            string? curGuid = null;
            ExistingPipe? cur = null;
            Pt3? curStart = null, curEnd = null;
            Pt3? lastTo = null;
            TaskInfo? currentTask = null;

            var tempSegs = new List<RawSeg>();
            void Flush()
            {
                if (cur == null) return;
                
                // IP 복원 전처리를 거친 포인트 리스트 생성
                var reconstructedPts = ReconstructIntersectionPoints(tempSegs);
                foreach (var pt in reconstructedPts)
                {
                    if (cur.Points.Count == 0 || Dist2(cur.Points[cur.Points.Count - 1], pt) > 1.0)
                        cur.Points.Add(pt);
                }

                if (curStart.HasValue && curEnd.HasValue)
                    TrimToBoundary(cur.Points, curStart.Value, curEnd.Value);
                if (cur.Points.Count >= 2) data.ExistingPipes.Add(cur);
                tempSegs.Clear();
            }

            while (r.Read())
            {
                string g = r.GetString(0);
                
                Pt3? rowFrom = (r.IsDBNull(6) || r.IsDBNull(7) || r.IsDBNull(8))
                    ? (Pt3?)null : new Pt3(Dbl(r, 6), Dbl(r, 7), Dbl(r, 8));
                Pt3? rowTo = (r.IsDBNull(9) || r.IsDBNull(10) || r.IsDBNull(11))
                    ? (Pt3?)null : new Pt3(Dbl(r, 9), Dbl(r, 10), Dbl(r, 11));

                bool isNewGuid = !string.Equals(curGuid, g, StringComparison.Ordinal);
                bool isDisconnected = isNewGuid;

                if (!isDisconnected && lastTo.HasValue && rowFrom.HasValue)
                {
                    double dist2 = Dist2(lastTo.Value, rowFrom.Value);
                    if (dist2 > 100.0) 
                    {
                        isDisconnected = true;
                    }
                    else
                    {
                        rowFrom = lastTo.Value;
                    }
                }

                if (isDisconnected)
                {
                    Flush();
                    curGuid = g;
                    string? util = r.IsDBNull(2) ? null : r.GetString(2);
                    string? grp = r.IsDBNull(1) ? null : r.GetString(1);
                    cur = new ExistingPipe
                    {
                        RoutePathGuid = g,
                        Group = grp,
                        Utility = util,
                        DiameterMm = r.IsDBNull(3) ? 0 : ParsePipeSizeMm(r.GetString(3)),
                    };
                    curStart = (r.IsDBNull(12) || r.IsDBNull(13) || r.IsDBNull(14))
                        ? (Pt3?)null : new Pt3(Dbl(r, 12), Dbl(r, 13), Dbl(r, 14));
                    curEnd = (r.IsDBNull(15) || r.IsDBNull(16) || r.IsDBNull(17))
                        ? (Pt3?)null : new Pt3(Dbl(r, 15), Dbl(r, 16), Dbl(r, 17));
                    cur.SourcePos = curStart;
                    cur.TargetPos = curEnd;

                    if (isNewGuid)
                    {
                        lastTo = null;
                        if (curStart.HasValue && curEnd.HasValue)
                        {
                            currentTask = new TaskInfo
                            {
                                RoutePathGuid = g,
                                Sx = curStart.Value.X, Sy = curStart.Value.Y, Sz = curStart.Value.Z,
                                Gx = curEnd.Value.X, Gy = curEnd.Value.Y, Gz = curEnd.Value.Z,
                                Utility = util, Group = grp,
                                PocName = r.IsDBNull(4) ? null : r.GetString(4),
                                EndName = r.IsDBNull(5) ? null : r.GetString(5),
                                DiameterMm = r.IsDBNull(3) ? 0 : ParsePipeSizeMm(r.GetString(3)),
                                EquipmentTag = r.IsDBNull(19) ? null : r.GetString(19)
                            };
                            data.Tasks.Add(currentTask);
                        }
                        else
                        {
                            currentTask = null;
                        }
                    }
                }

                if (rowFrom.HasValue && rowTo.HasValue)
                {
                    string? type = r.IsDBNull(18) ? null : r.GetString(18);
                    tempSegs.Add(new RawSeg { From = rowFrom.Value, To = rowTo.Value, Type = type });
                    lastTo = rowTo.Value;
                }
            }
            Flush();
        }

        public static Dictionary<string, FeatureProfileRow> LoadFeatureProfiles(DbConfig config, string sourceFile)
        {
            var dict = new Dictionary<string, FeatureProfileRow>(StringComparer.OrdinalIgnoreCase);
            try
            {
                using var conn = new NpgsqlConnection(config.ConnectionString);
                conn.Open();

                // 1. Query TopK counts per utility group
                var topKCounts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
                try
                {
                    using (var cmdCount = new NpgsqlCommand(
                        @"SELECT ""UTILITY_GROUP"", COUNT(*) 
                          FROM ""TB_ROUTE_FEATURE_VECTOR"" 
                          WHERE ""EQUIPMENT_NAME"" = @proj 
                          GROUP BY ""UTILITY_GROUP""", conn))
                    {
                        cmdCount.Parameters.AddWithValue("@proj", sourceFile);
                        using (var rCount = cmdCount.ExecuteReader())
                        {
                            while (rCount.Read())
                            {
                                string grp = rCount.IsDBNull(0) ? string.Empty : rCount.GetString(0).Trim();
                                int count = rCount.IsDBNull(1) ? 0 : (int)rCount.GetInt64(1);
                                topKCounts[grp] = count;
                            }
                        }
                    }
                }
                catch (Exception exCount)
                {
                    System.Diagnostics.Debug.WriteLine($"[경고] TB_ROUTE_FEATURE_VECTOR 카운트 로드 실패: {exCount.Message}");
                }

                // 2. Query feature group profiles
                using var cmd = new NpgsqlCommand(
                    @"SELECT ""utility_group"", ""preferred_source_face"", ""preferred_target_face"", ""preferred_rack_zs"", ""trunk_centerline_json""
                      FROM ""route_feature_group_profile""
                      WHERE ""project_id"" = @proj", conn);
                cmd.Parameters.AddWithValue("@proj", sourceFile);
                using var r = cmd.ExecuteReader();
                while (r.Read())
                {
                    string utg = r.IsDBNull(0) ? string.Empty : r.GetString(0);
                    string srcFace = r.IsDBNull(1) ? "Any" : r.GetString(1);
                    string tgtFace = r.IsDBNull(2) ? "Any" : r.GetString(2);
                    
                    var rackZs = new List<double>();
                    if (!r.IsDBNull(3))
                    {
                        try
                        {
                            var vals = r.GetFieldValue<double[]>(3);
                            if (vals != null) rackZs.AddRange(vals);
                        }
                        catch
                        {
                            try
                            {
                                var raw = r.GetValue(3);
                                if (raw is double[] arr) rackZs.AddRange(arr);
                            }
                            catch { }
                        }
                    }
                    string trunkJson = r.IsDBNull(4) ? "[]" : r.GetString(4);

                    int tkCount = 0;
                    topKCounts.TryGetValue(utg, out tkCount);

                    dict[utg] = new FeatureProfileRow
                    {
                        ProjectId = sourceFile,
                        UtilityGroup = utg,
                        PreferredSourceFace = srcFace,
                        PreferredTargetFace = tgtFace,
                        PreferredRackZs = rackZs,
                        TrunkCenterlineJson = trunkJson,
                        TopKCount = tkCount
                    };
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[경고] LoadFeatureProfiles 실패: {ex.Message}");
            }
            return dict;
        }

        internal class RawSeg
        {
            public Pt3 From { get; set; }
            public Pt3 To { get; set; }
            public string? Type { get; set; }
        }

        internal static List<Pt3> ReconstructIntersectionPoints(List<RawSeg> segments)
        {
            var pts = new List<Pt3>();
            if (segments.Count == 0) return pts;

            pts.Add(segments[0].From);

            for (int i = 0; i < segments.Count; i++)
            {
                var curSeg = segments[i];

                if (curSeg.Type == "ELBOW" && i > 0 && i < segments.Count - 1)
                {
                    var prevSeg = segments[i - 1];
                    var nextSeg = segments[i + 1];

                    Pt3 v1 = Sub(prevSeg.To, prevSeg.From);
                    Pt3 v2 = Sub(nextSeg.To, nextSeg.From);

                    double l1 = Len(v1);
                    double l2 = Len(v2);

                    if (l1 > 1e-3 && l2 > 1e-3)
                    {
                        v1 = new Pt3(v1.X / l1, v1.Y / l1, v1.Z / l1);
                        v2 = new Pt3(v2.X / l2, v2.Y / l2, v2.Z / l2);

                        Pt3 p1 = prevSeg.To;
                        Pt3 p2 = nextSeg.From;
                        Pt3 w0 = Sub(p1, p2);

                        double a = Dot(v1, v1);
                        double b = Dot(v1, v2);
                        double c = Dot(v2, v2);
                        double d = Dot(v1, w0);
                        double e = Dot(v2, w0);

                        double denom = a * c - b * b;
                        if (denom > 1e-6)
                        {
                            double t = (b * e - c * d) / denom;
                            double s = (a * e - b * d) / denom;

                            Pt3 q1 = Add(p1, Mult(v1, t));
                            Pt3 q2 = Add(p2, Mult(v2, s));

                            Pt3 ip = new Pt3((q1.X + q2.X) * 0.5, (q1.Y + q2.Y) * 0.5, (q1.Z + q2.Z) * 0.5);

                            double skewDist2 = Dist2(q1, q2);
                            if (skewDist2 < 500.0 * 500.0)
                            {
                                if (pts.Count > 0)
                                    pts[pts.Count - 1] = ip;
                                else
                                    pts.Add(ip);

                                continue;
                            }
                        }
                    }
                }

                Pt3 toPt = curSeg.To;
                if (pts.Count == 0 || Dist2(pts[pts.Count - 1], toPt) > 1.0)
                {
                    pts.Add(toPt);
                }
            }

            return pts;
        }

        private static Pt3 Sub(Pt3 a, Pt3 b) => new Pt3(a.X - b.X, a.Y - b.Y, a.Z - b.Z);
        private static Pt3 Add(Pt3 a, Pt3 b) => new Pt3(a.X + b.X, a.Y + b.Y, a.Z + b.Z);
        private static Pt3 Mult(Pt3 a, double s) => new Pt3(a.X * s, a.Y * s, a.Z * s);
        private static double Dot(Pt3 a, Pt3 b) => a.X * b.X + a.Y * b.Y + a.Z * b.Z;
        private static double Len(Pt3 a) => Math.Sqrt(a.X * a.X + a.Y * a.Y + a.Z * a.Z);

        private static double Dbl(NpgsqlDataReader r, int i) => r.IsDBNull(i) ? 0.0 : r.GetDouble(i);
        private static double Dist2(Pt3 a, Pt3 b)
        {
            double dx = a.X - b.X, dy = a.Y - b.Y, dz = a.Z - b.Z;
            return dx * dx + dy * dy + dz * dz;
        }

        public static double ParsePipeSizeMm(string? size)
        {
            if (string.IsNullOrWhiteSpace(size)) return 0;
            string tok = size.Trim().Split('X', 'x')[0].Trim();
            if (tok.Length < 2) return 0;
            char unit = char.ToUpperInvariant(tok[tok.Length - 1]);
            string num = tok.Substring(0, tok.Length - 1).Trim();
            if (unit == 'A')
                return double.TryParse(num, System.Globalization.NumberStyles.Any,
                    System.Globalization.CultureInfo.InvariantCulture, out var mm) ? mm : 0;
            if (unit == 'B')
            {
                double inch = ParseInch(num);
                return inch > 0 ? inch * 25.4 : 0;
            }
            return 0;
        }

        private static double ParseInch(string s)
        {
            s = s.Trim().Replace('-', ' ');
            if (s.Contains('/'))
            {
                var parts = s.Split(' ');
                double whole = 0; string frac = s;
                if (parts.Length == 2) { double.TryParse(parts[0], out whole); frac = parts[1]; }
                var fp = frac.Split('/');
                if (fp.Length == 2 && double.TryParse(fp[0], out var a) && double.TryParse(fp[1], out var b) && b != 0)
                    return whole + a / b;
                return whole;
            }
            return double.TryParse(s, System.Globalization.NumberStyles.Any,
                System.Globalization.CultureInfo.InvariantCulture, out var v) ? v : 0;
        }

        private static void TrimToBoundary(List<Pt3> path, Pt3 startPos, Pt3 endPos)
        {
            if (path.Count < 2) return;
            int si = 0, ei = path.Count - 1;
            double sb = double.MaxValue, eb = double.MaxValue;
            for (int i = 0; i < path.Count; i++)
            {
                double ds = Dist2(path[i], startPos);
                double de = Dist2(path[i], endPos);
                if (ds < sb) { sb = ds; si = i; }
                if (de < eb) { eb = de; ei = i; }
            }
            if (si > ei) { var t = si; si = ei; ei = t; }
            if (si == 0 && ei == path.Count - 1) return;
            var trimmed = path.GetRange(si, ei - si + 1);
            path.Clear();
            path.AddRange(trimmed);
        }

        private static GridMeta ComputeGrid(double cxmin, double cymin, double czmin,
                                            double cxmax, double cymax, double czmax, double cellMm)
        {
            return new GridMeta
            {
                CellMm = cellMm,
                Ox = cxmin, Oy = cymin, Oz = czmin,
                Nx = Math.Max(1, (int)Math.Ceiling((cxmax - cxmin) / cellMm)),
                Ny = Math.Max(1, (int)Math.Ceiling((cymax - cymin) / cellMm)),
                Nz = Math.Max(1, (int)Math.Ceiling((czmax - czmin) / cellMm)),
            };
        }
    }
}
