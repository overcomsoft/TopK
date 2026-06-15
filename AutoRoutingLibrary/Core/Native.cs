using System;
using System.Runtime.InteropServices;
using System.Text;

namespace AutoRoutingLibrary.Core
{
    internal static class Native
    {
        private const string Dll = "routing3d_capi";
        private const CallingConvention Cdecl = CallingConvention.Cdecl;

        [StructLayout(LayoutKind.Sequential)]
        internal struct R3dGrid
        {
            public double cell_mm;
            public double ox, oy, oz;
            public int nx, ny, nz;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct R3dParams
        {
            public double cell_mm, w_turn, w_clear;
            public double w_corridor;
            public double w_heur;
            public double w_heur_near;
            public int clearance_radius, clearance_connectivity;
            public int corridor_radius;
            public int rack_level_count;
            [MarshalAs(UnmanagedType.ByValArray, SizeConst = 8)]
            public int[] rack_levels;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct R3dResult
        {
            public int success;
            public double length_mm;
            public double cost_mm;
            public int turns;
            public long expanded_nodes;
            public double elapsed_ms;
            public int path_len;
            public int visited_len;
            public int fail_reason;
        }

        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern IntPtr r3d_version();
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern void r3d_free_string(IntPtr s);

        [DllImport(Dll, CallingConvention = Cdecl)]
        internal static extern int r3d_route_scene_text(byte[] sceneUtf8, byte[] modeUtf8, byte[] priorityUtf8, out IntPtr outScene);

        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern IntPtr r3d_create();
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern void r3d_destroy(IntPtr e);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_load_scene_text(IntPtr e, byte[] sceneUtf8);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_set_grid(IntPtr e, in R3dGrid g);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_set_params(IntPtr e, in R3dParams p);

        [DllImport(Dll, CallingConvention = Cdecl)]
        internal static extern int r3d_add_obstacle(IntPtr e, double minx, double miny, double minz, double maxx, double maxy, double maxz);

        [DllImport(Dll, CallingConvention = Cdecl)]
        internal static extern int r3d_add_passthrough(IntPtr e, double minx, double miny, double minz, double maxx, double maxy, double maxz);

        [DllImport(Dll, CallingConvention = Cdecl)]
        internal static extern int r3d_add_task(IntPtr e, double sx, double sy, double sz, double gx, double gy, double gz,
            byte[]? utilityUtf8, byte[]? utilityGroupUtf8);

        [DllImport(Dll, CallingConvention = Cdecl)]
        internal static extern int r3d_set_task_endpoints(IntPtr e, int task, double sx, double sy, double sz, double gx, double gy, double gz);

        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_set_task_diameter(IntPtr e, int task, double diameterMm);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_set_task_goal_dir(IntPtr e, int task, int axis);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_route_multi(IntPtr e, byte[] priorityUtf8);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_set_corridor_cells(IntPtr e, int[]? ijk, int n);

        [UnmanagedFunctionPointer(Cdecl)]
        internal delegate int R3dProgressFn(IntPtr user, int phase, int orderIndex, int taskIndex, int success,
            double lengthMm, int turns, long expandedNodes, double elapsedMs, int done, int total, double progress01,
            IntPtr pathIjk, int pathLen);

        [DllImport(Dll, CallingConvention = Cdecl)]
        internal static extern int r3d_route_multi_progress(IntPtr e, byte[] priorityUtf8, R3dProgressFn cb, IntPtr user);

        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_route_task(IntPtr e, int task, out R3dResult outRes);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_route_ripup(IntPtr e, byte[] priorityUtf8, int maxRounds, int maxRipup);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_route_corridor(IntPtr e, int factor, int radius);

        [DllImport(Dll, CallingConvention = Cdecl)]
        internal static extern int r3d_route_corridor_multi(IntPtr e, int factor, int radius, byte[] priorityUtf8, int pipeRadius);

        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_get_result(IntPtr e, int task, out R3dResult outRes);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_copy_path(IntPtr e, int task, [Out] int[] buf, int bufCells);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_copy_visited(IntPtr e, int task, [Out] int[] buf, int bufCells);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_copy_blocked(IntPtr e, [Out] int[]? buf, int bufCells);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_copy_passthrough(IntPtr e, [Out] int[]? buf, int bufCells);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_set_collect_visited(IntPtr e, int enabled);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_set_pipe_radius(IntPtr e, int radiusCells);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_set_per_task_radius(IntPtr e, int enabled);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_set_cbs_depth(IntPtr e, int depth);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_set_min_straight(IntPtr e, double mult);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_set_pipe_gap(IntPtr e, double gapMm);
        [DllImport(Dll, CallingConvention = Cdecl)] internal static extern int r3d_dump_scene_text(IntPtr e, out IntPtr outScene);

        internal static byte[] Utf8(string s) => Encoding.UTF8.GetBytes(s + "\0");
        internal static byte[]? Utf8OrNull(string? s) => s is null ? null : Utf8(s);

        internal static string TakeString(IntPtr p)
        {
            if (p == IntPtr.Zero) return string.Empty;
            try { return Marshal.PtrToStringUTF8(p) ?? string.Empty; }
            finally { r3d_free_string(p); }
        }

        internal static string VersionString() => Marshal.PtrToStringUTF8(r3d_version()) ?? "(unknown)";
    }
}
