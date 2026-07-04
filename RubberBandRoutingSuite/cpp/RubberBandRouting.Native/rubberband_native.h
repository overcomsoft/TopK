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
typedef struct RbConfig { int max_vertical_bends; double safety_margin, tray_width, tray_height, pipe_pitch; int pipe_count; } RbConfig;

typedef void* RbEngineHandle;

RB_API RbEngineHandle rb_create(void);
RB_API void rb_destroy(RbEngineHandle engine);
RB_API int rb_initialize(RbEngineHandle engine, RbConfig config);
RB_API int rb_set_obstacles(RbEngineHandle engine, const RbAabb* obstacles, int count);
RB_API int rb_execute(RbEngineHandle engine, RbVec3 start, RbVec3 end);
RB_API int rb_get_segment_count(RbEngineHandle engine);
RB_API int rb_copy_segments(RbEngineHandle engine, RbVec3* out_points, int max_points);
RB_API int rb_get_pipe_count(RbEngineHandle engine);
RB_API int rb_copy_pipe_path(RbEngineHandle engine, int pipe_index, RbVec3* out_points, int max_points);

#ifdef __cplusplus
}
#endif
