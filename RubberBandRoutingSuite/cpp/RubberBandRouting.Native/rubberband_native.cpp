#define RUBBERBAND_NATIVE_EXPORTS
#include "rubberband_native.h"
#include <algorithm>
#include <cmath>
#include <memory>
#include <vector>

namespace {
struct Segment { RbVec3 a; RbVec3 b; };
struct Engine {
    RbConfig cfg{5, 50.0, 600.0, 100.0, 100.0, 3};
    std::vector<RbAabb> obstacles;
    std::vector<Segment> segments;
    std::vector<std::vector<RbVec3>> pipes;
};

static RbVec3 make(double x, double y, double z) { return {x, y, z}; }
static double coord(const RbVec3& p, int axis) { return axis == 0 ? p.x : axis == 1 ? p.y : p.z; }
static void set_coord(RbVec3& p, int axis, double v) { if (axis == 0) p.x = v; else if (axis == 1) p.y = v; else p.z = v; }
static RbVec3 sub(RbVec3 a, RbVec3 b) { return make(a.x - b.x, a.y - b.y, a.z - b.z); }
static RbVec3 add(RbVec3 a, RbVec3 b) { return make(a.x + b.x, a.y + b.y, a.z + b.z); }
static RbVec3 mul(RbVec3 a, double s) { return make(a.x * s, a.y * s, a.z * s); }
static double abs_coord(const RbVec3& p, int axis) { return std::abs(coord(p, axis)); }
static int dominant_axis(RbVec3 d) {
    double ax = std::abs(d.x), ay = std::abs(d.y), az = std::abs(d.z);
    return ax >= ay && ax >= az ? 0 : ay >= az ? 1 : 2;
}
static bool is_vertical(const Segment& s) {
    auto d = sub(s.b, s.a);
    return std::abs(d.z) > std::max(std::abs(d.x), std::abs(d.y));
}
static void append_orthogonal(std::vector<Segment>& out, RbVec3 start, RbVec3 end) {
    RbVec3 cur = start;
    for (int axis = 0; axis < 3; ++axis) {
        double delta = coord(end, axis) - coord(cur, axis);
        if (std::abs(delta) <= 1e-3) continue;
        RbVec3 next = cur;
        set_coord(next, axis, coord(end, axis));
        out.push_back({cur, next});
        cur = next;
    }
}
static RbAabb expand(const RbAabb& b, const RbConfig& cfg) {
    double h = cfg.tray_width / 2.0 + cfg.safety_margin;
    double v = cfg.tray_height / 2.0 + cfg.safety_margin;
    return {b.min_x - h, b.min_y - h, b.min_z - v, b.max_x + h, b.max_y + h, b.max_z + v, b.is_penetration};
}
static bool within(double v, double mn, double mx) { return v >= mn - 1e-6 && v <= mx + 1e-6; }
static bool intersects(const Segment& s, const RbAabb& raw, const RbConfig& cfg) {
    auto b = expand(raw, cfg);
    auto d = sub(s.b, s.a);
    int axis = dominant_axis(d);
    int c1 = (axis + 1) % 3, c2 = (axis + 2) % 3;
    double mn[3] = {b.min_x, b.min_y, b.min_z};
    double mx[3] = {b.max_x, b.max_y, b.max_z};
    if (!within(coord(s.a, c1), mn[c1], mx[c1]) || !within(coord(s.a, c2), mn[c2], mx[c2])) return false;
    double a0 = std::min(coord(s.a, axis), coord(s.b, axis));
    double a1 = std::max(coord(s.a, axis), coord(s.b, axis));
    return std::max(a0, mn[axis]) <= std::min(a1, mx[axis]);
}
static int count_vertical_bends(const std::vector<Segment>& segs) {
    int count = 0;
    bool has_prev = false;
    bool prev = false;
    for (auto& s : segs) {
        bool cur = is_vertical(s);
        if (has_prev && prev != cur && cur) ++count;
        prev = cur;
        has_prev = true;
    }
    return count;
}
static std::vector<RbVec3> bypass(const Segment& s, const RbAabb& o, const RbConfig& cfg, int remaining) {
    if (remaining >= 2) {
        double clearance = cfg.tray_width / 2.0 + cfg.tray_height + cfg.safety_margin + 1.0;
        double z = o.max_z + clearance;
        return {make(s.a.x, s.a.y, z), make(s.b.x, s.b.y, z)};
    }
    auto d = sub(s.b, s.a);
    int axis = dominant_axis(d);
    int side_axis = axis == 0 ? 1 : 0;
    double clearance = cfg.tray_width / 2.0 + cfg.safety_margin + 1.0;
    double mn[3] = {o.min_x, o.min_y, o.min_z};
    double mx[3] = {o.max_x, o.max_y, o.max_z};
    double low = mn[side_axis] - clearance;
    double high = mx[side_axis] + clearance;
    double side = std::abs(coord(s.a, side_axis) - low) <= std::abs(coord(s.a, side_axis) - high) ? low : high;
    RbVec3 p1 = s.a, p2 = s.a, p3 = s.b;
    set_coord(p1, side_axis, side);
    p2 = p1;
    set_coord(p2, axis, mx[axis] + clearance);
    set_coord(p3, side_axis, side);
    return {p1, p2, p3};
}
static RbVec3 normal_for(const Segment& s, bool has_prev, RbVec3 prev) {
    if (is_vertical(s) && has_prev) return prev;
    auto d = sub(s.b, s.a);
    if (std::abs(d.x) >= std::abs(d.y)) return make(0, d.x >= 0 ? 1 : -1, 0);
    return make(d.y >= 0 ? -1 : 1, 0, 0);
}
static void distribute(Engine& e) {
    e.pipes.clear();
    double half = (e.cfg.pipe_count - 1) / 2.0;
    for (int p = 0; p < e.cfg.pipe_count; ++p) {
        double offset = (p - half) * e.cfg.pipe_pitch;
        std::vector<RbVec3> path;
        bool has_prev = false;
        RbVec3 prev{};
        for (size_t i = 0; i < e.segments.size(); ++i) {
            auto n = normal_for(e.segments[i], has_prev, prev);
            prev = n;
            has_prev = true;
            path.push_back(add(e.segments[i].a, mul(n, offset)));
            if (i + 1 == e.segments.size()) path.push_back(add(e.segments[i].b, mul(n, offset)));
        }
        e.pipes.push_back(std::move(path));
    }
}
}

extern "C" RB_API RbEngineHandle rb_create(void) { return new Engine(); }
extern "C" RB_API void rb_destroy(RbEngineHandle engine) { delete static_cast<Engine*>(engine); }
extern "C" RB_API int rb_initialize(RbEngineHandle engine, RbConfig config) { if (!engine) return 1; static_cast<Engine*>(engine)->cfg = config; return 0; }
extern "C" RB_API int rb_set_obstacles(RbEngineHandle engine, const RbAabb* obstacles, int count) {
    if (!engine || count < 0) return 1;
    auto* e = static_cast<Engine*>(engine);
    e->obstacles.assign(obstacles, obstacles + count);
    return 0;
}
extern "C" RB_API int rb_execute(RbEngineHandle engine, RbVec3 start, RbVec3 end) {
    if (!engine) return 1;
    auto* e = static_cast<Engine*>(engine);
    e->segments.clear();
    append_orthogonal(e->segments, start, end);
    for (int iter = 0; iter < 40; ++iter) {
        bool changed = false;
        for (size_t i = 0; i < e->segments.size() && !changed; ++i) {
            for (auto& o : e->obstacles) {
                if (o.is_penetration || !intersects(e->segments[i], o, e->cfg)) continue;
                int remaining = e->cfg.max_vertical_bends - count_vertical_bends(e->segments);
                auto pts = bypass(e->segments[i], o, e->cfg, remaining);
                std::vector<Segment> repl;
                RbVec3 cur = e->segments[i].a;
                for (auto p : pts) { append_orthogonal(repl, cur, p); cur = p; }
                append_orthogonal(repl, cur, e->segments[i].b);
                e->segments.erase(e->segments.begin() + static_cast<long long>(i));
                e->segments.insert(e->segments.begin() + static_cast<long long>(i), repl.begin(), repl.end());
                changed = true;
                break;
            }
        }
        if (!changed) break;
    }
    distribute(*e);
    return 0;
}
extern "C" RB_API int rb_get_segment_count(RbEngineHandle engine) { return engine ? static_cast<int>(static_cast<Engine*>(engine)->segments.size()) : 0; }
extern "C" RB_API int rb_copy_segments(RbEngineHandle engine, RbVec3* out_points, int max_points) {
    if (!engine) return 0;
    auto* e = static_cast<Engine*>(engine);
    int needed = static_cast<int>(e->segments.size()) + (e->segments.empty() ? 0 : 1);
    if (!out_points || max_points <= 0) return needed;
    int n = 0;
    if (!e->segments.empty() && n < max_points) out_points[n++] = e->segments.front().a;
    for (auto& s : e->segments) if (n < max_points) out_points[n++] = s.b;
    return n;
}
extern "C" RB_API int rb_get_pipe_count(RbEngineHandle engine) { return engine ? static_cast<int>(static_cast<Engine*>(engine)->pipes.size()) : 0; }
extern "C" RB_API int rb_copy_pipe_path(RbEngineHandle engine, int pipe_index, RbVec3* out_points, int max_points) {
    if (!engine) return 0;
    auto* e = static_cast<Engine*>(engine);
    if (pipe_index < 0 || pipe_index >= static_cast<int>(e->pipes.size())) return 0;
    auto& path = e->pipes[pipe_index];
    if (!out_points || max_points <= 0) return static_cast<int>(path.size());
    int n = std::min<int>(max_points, static_cast<int>(path.size()));
    for (int i = 0; i < n; ++i) out_points[i] = path[i];
    return n;
}
