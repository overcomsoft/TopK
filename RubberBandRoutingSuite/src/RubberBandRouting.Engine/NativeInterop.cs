using System;
using System.Runtime.InteropServices;

namespace RubberBandRouting.Engine;

/// <summary>
/// P/Invoke declarations for RubberBandRouting.Native (rubberband_native.h / .cpp).
/// Struct field order and types must match the C header exactly.
/// </summary>
internal static class NativeMethods
{
    private const string LibraryName = "RubberBandRouting.Native";

    [StructLayout(LayoutKind.Sequential)]
    public struct RbVec3
    {
        public double X, Y, Z;
        public RbVec3(double x, double y, double z) { X = x; Y = y; Z = z; }
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct RbAabb
    {
        public double MinX, MinY, MinZ, MaxX, MaxY, MaxZ;
        public int IsPenetration;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct RbConfig
    {
        public int MaxVerticalBends;
        public double SafetyMargin;
        public double TrayWidth;
        public double TrayHeight;
        public double PipePitch;
        public int PipeCount;
        public double SnapTolerance;
    }

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    public static extern IntPtr rb_create();

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    public static extern void rb_destroy(IntPtr engine);

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    public static extern int rb_initialize(IntPtr engine, RbConfig config);

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    public static extern int rb_set_obstacles(IntPtr engine, RbAabb[] obstacles, int count);

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    public static extern int rb_set_features(IntPtr engine, RbVec3[] features, int count);

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    public static extern int rb_set_feature_flags(IntPtr engine, int[] required, int count);

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    public static extern int rb_execute(IntPtr engine, RbVec3 start, RbVec3 end);

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    public static extern int rb_get_segment_count(IntPtr engine);

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    public static extern int rb_copy_segments(IntPtr engine, [In, Out] RbVec3[]? outPoints, int maxPoints);

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    public static extern int rb_get_pipe_count(IntPtr engine);

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    public static extern int rb_copy_pipe_path(IntPtr engine, int pipeIndex, [In, Out] RbVec3[]? outPoints, int maxPoints);

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    public static extern int rb_get_vertical_bends(IntPtr engine);

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    public static extern int rb_get_fallback_count(IntPtr engine);

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    public static extern int rb_is_valid(IntPtr engine);

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    public static extern int rb_get_segment_reason(IntPtr engine, int segmentIndex);
}
