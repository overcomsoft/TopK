# RubberBandRouting.Native

Native C++ implementation of the RubberBandRoutingSuite engine. It is kept in sync
with the managed C# `ManagedRubberBandEngine` and implements the same pipeline:

1. Create an initial straight rubber line from the start PoC to the end PoC.
2. Pull the rubber line through selected existing-design feature/control points
   (`rb_set_features`), producing an ordered waypoint list.
3. Route each waypoint leg with an orthogonal **A\*** search over a sparse coordinate
   grid derived from the expanded obstacle bounds, avoiding collisions. Collision
   testing uses a proper slab-based segment/AABB intersection, so non axis-aligned
   legs are handled correctly. A leg that finds no path falls back to a raw
   orthogonal path and is reported via `rb_get_fallback_count`.
4. Distribute the routed centerline into `pipe_count` parallel pipes.

`rb_is_valid` returns 1 only when there is no residual collision, no A\* fallback,
and the vertical-bend budget is respected.

## API

The exported C API is declared in `rubberband_native.h` and is callable from C#
through P/Invoke. Typical sequence:

```c
RbEngineHandle h = rb_create();
RbConfig cfg = {5, 50.0, 600.0, 100.0, 100.0, 3, 100.0};
rb_initialize(h, cfg);
rb_set_obstacles(h, obstacles, obstacleCount);
rb_set_features(h, features, featureCount);   // optional
rb_execute(h, start, end);

int n = rb_copy_segments(h, NULL, 0);         // query point count
RbVec3* pts = malloc(sizeof(RbVec3) * n);
rb_copy_segments(h, pts, n);                  // copy centerline
rb_destroy(h);
```

> `RbConfig` field order is `max_vertical_bends, safety_margin, tray_width,
> tray_height, pipe_pitch, pipe_count, snap_tolerance`. Match this exactly in the
> C# `[StructLayout(LayoutKind.Sequential)]` marshalling struct.

## Build

From a Visual Studio Developer Command Prompt:

```bat
build_msvc.bat
```

This produces `RubberBandRouting.Native.dll`.
