#pragma once

#ifdef _WIN32
  #ifdef RUBBERBAND_NATIVE_EXPORTS
    #define RB_API __declspec(dllexport)
  #else
    #define RB_API __declspec(dllimport)
  #endif
#else
  #define RB_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct RbVec3 { double x, y, z; } RbVec3;
typedef struct RbAabb { double min_x, min_y, min_z, max_x, max_y, max_z; int is_penetration; } RbAabb;
typedef struct RbConfig { int max_vertical_bends; double safety_margin, tray_width, tray_height, pipe_pitch; int pipe_count; double snap_tolerance; } RbConfig;

typedef void* RbEngineHandle;

/* Lifetime */
RB_API RbEngineHandle rb_create(void);
RB_API void rb_destroy(RbEngineHandle engine);

/* Configuration and inputs */
RB_API int rb_initialize(RbEngineHandle engine, RbConfig config);
RB_API int rb_set_obstacles(RbEngineHandle engine, const RbAabb* obstacles, int count);
/* Existing-design feature/control points the rubber line is pulled through. Optional.
   Resets any previously set required-flags (see rb_set_feature_flags) to all-false. */
RB_API int rb_set_features(RbEngineHandle engine, const RbVec3* features, int count);
/* Optional: mark a subset of the features passed to rb_set_features as required, i.e. always
   pulled through regardless of the snap-tolerance/detour filtering that optional features go
   through. `count` must equal the count passed to the preceding rb_set_features call. */
RB_API int rb_set_feature_flags(RbEngineHandle engine, const int* required, int count);

/* Run the rubber-band routing pipeline: straight rubber line -> feature snap ->
   orthogonal A* obstacle avoidance -> pipe distribution. Returns 0 on success. */
RB_API int rb_execute(RbEngineHandle engine, RbVec3 start, RbVec3 end);

/* Result: routed centerline as a polyline (segment_count + 1 points). */
RB_API int rb_get_segment_count(RbEngineHandle engine);
RB_API int rb_copy_segments(RbEngineHandle engine, RbVec3* out_points, int max_points);

/* Result: distributed pipe paths. */
RB_API int rb_get_pipe_count(RbEngineHandle engine);
RB_API int rb_copy_pipe_path(RbEngineHandle engine, int pipe_index, RbVec3* out_points, int max_points);

/* Diagnostics for the last rb_execute call. */
RB_API int rb_get_vertical_bends(RbEngineHandle engine);
RB_API int rb_get_fallback_count(RbEngineHandle engine); /* A* legs that fell back to a raw orthogonal path */
RB_API int rb_is_valid(RbEngineHandle engine);           /* 1 if no residual collision and no fallback */

/* Reason code for why FinalSegments[segment_index]'s leading corner exists. One of:
   0 route_start, 1 feature_snap, 2 collision_bypass, 3 direction_change, 4 elevation_change,
   5 rubber_alignment. Returns -1 if the index is out of range. Keep this order in sync with
   RubberBandRouting.Engine's SegmentReasons.NativeCodeOrder in Models.cs. */
RB_API int rb_get_segment_reason(RbEngineHandle engine, int segment_index);

#ifdef __cplusplus
}
#endif
