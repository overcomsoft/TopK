# RubberBandRouting.Native

Native C++ implementation stub for the RubberBandRoutingSuite engine.

The target algorithm is a rubber-band control-point router, not a fixed X/Y/Z orthogonal segment generator:

1. Create an initial straight rubber line from start PoC to end PoC.
2. Pull the rubber line through selected existing-design feature/control points.
3. Push colliding segments away from expanded obstacle volumes by inserting bypass control points.
4. Let the viewer or final geometry stage apply pipe diameter and bend-radius correction.

Build on Windows from a Visual Studio Developer Command Prompt:

```bat
build_msvc.bat
```

The exported C API is declared in `rubberband_native.h` and can be called from C# through P/Invoke.
