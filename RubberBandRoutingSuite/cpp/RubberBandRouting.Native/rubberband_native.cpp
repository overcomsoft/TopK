#define RUBBERBAND_NATIVE_EXPORTS
#include "rubberband_native.h"
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <queue>
#include <unordered_map>
#include <vector>

// This native engine mirrors the managed C# ManagedRubberBandEngine:
//   1. Straight rubber line from start to end (tension reference).
//   2. Pull the line through selected existing-design feature/control points.
//   3. Orthogonal A* obstacle avoidance on a sparse coordinate grid.
//   4. Distribute the centerline into multiple pipes.
// Collision testing uses a proper slab-based segment/AABB intersection so that
// arbitrary (non axis-aligned) rubber legs are handled correctly.

namespace {

constexpr double kEpsilon = 1e-6;
constexpr int kGridObstacleLimit = 48;
constexpr int kCorridorObstacleLimit = 256;
// Raised from 50,000: in a dense equipment cluster with many nearby obstacles/accumulated
// auto-routes, the grid can need far more expansions to find a genuinely narrow gap. Mitigates
// (does not guarantee-fix) widespread A* failure when many tasks are routed back-to-back in a
// tight area -- if it's still hit, the obstacles may genuinely leave no orthogonal gap at the
// current TrayWidth/SafetyMargin clearance.
constexpr int kMaxExpansions = 200000;

struct Segment { RbVec3 a; RbVec3 b; };

// Reason codes for rb_get_segment_reason; keep in sync with
// RubberBandRouting.Engine.SegmentReasons.NativeCodeOrder (Models.cs).
enum class SegmentReason : int { RouteStart = 0, FeatureSnap = 1, CollisionBypass = 2, DirectionChange = 3, ElevationChange = 4, RubberAlignment = 5 };

struct Engine {
    // pipe_count defaults to 1: no per-task data source indicates how many physical pipes a
    // given connection actually bundles, so forcing a multiplier here would just draw N
    // near-duplicate lines for every route. Callers with real bundling data set it explicitly.
    RbConfig cfg{5, 50.0, 600.0, 100.0, 100.0, 1, 100.0, 50.0};
    std::vector<RbAabb> obstacles;
    std::vector<RbVec3> features;
    std::vector<int> featureRequired;
    std::vector<Segment> segments;
    std::vector<std::vector<RbVec3>> pipes;
    std::vector<int> segmentReasons;
    int verticalBends = 0;
    int fallbackCount = 0;
    bool valid = true;
};

RbVec3 make(double x, double y, double z) { return {x, y, z}; }
RbVec3 sub(RbVec3 a, RbVec3 b) { return make(a.x - b.x, a.y - b.y, a.z - b.z); }
RbVec3 add(RbVec3 a, RbVec3 b) { return make(a.x + b.x, a.y + b.y, a.z + b.z); }
RbVec3 mul(RbVec3 a, double s) { return make(a.x * s, a.y * s, a.z * s); }
double dot(RbVec3 a, RbVec3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
double length(RbVec3 v) { return std::sqrt(dot(v, v)); }
double coord(const RbVec3& p, int axis) { return axis == 0 ? p.x : axis == 1 ? p.y : p.z; }
void set_coord(RbVec3& p, int axis, double v) { if (axis == 0) p.x = v; else if (axis == 1) p.y = v; else p.z = v; }

RbVec3 with_axis(RbVec3 p, int axis, double v) { set_coord(p, axis, v); return p; }
double aabb_min(const RbAabb& b, int axis) { return axis == 0 ? b.min_x : axis == 1 ? b.min_y : b.min_z; }
double aabb_max(const RbAabb& b, int axis) { return axis == 0 ? b.max_x : axis == 1 ? b.max_y : b.max_z; }
RbVec3 aabb_center(const RbAabb& b) { return make((b.min_x + b.max_x) / 2, (b.min_y + b.max_y) / 2, (b.min_z + b.max_z) / 2); }

int dominant_axis(RbVec3 d) {
    double ax = std::abs(d.x), ay = std::abs(d.y), az = std::abs(d.z);
    return ax >= ay && ax >= az ? 0 : ay >= az ? 1 : 2;
}

double seg_length(const Segment& s) { return length(sub(s.b, s.a)); }
bool is_vertical(const Segment& s) {
    auto d = sub(s.b, s.a);
    return std::abs(d.z) > std::max(std::abs(d.x), std::abs(d.y));
}

double distance_point_to_segment(RbVec3 p, RbVec3 a, RbVec3 b) {
    auto ab = sub(b, a);
    double len2 = dot(ab, ab);
    if (len2 <= kEpsilon) return length(sub(p, a));
    double t = std::clamp(dot(sub(p, a), ab) / len2, 0.0, 1.0);
    return length(sub(p, add(a, mul(ab, t))));
}

RbAabb expand_aabb(const RbAabb& b, double h, double v) {
    return {b.min_x - h, b.min_y - h, b.min_z - v, b.max_x + h, b.max_y + h, b.max_z + v, b.is_penetration};
}

bool aabb_overlap(const RbAabb& a, const RbAabb& b) {
    return a.min_x <= b.max_x && a.max_x >= b.min_x &&
           a.min_y <= b.max_y && a.max_y >= b.min_y &&
           a.min_z <= b.max_z && a.max_z >= b.min_z;
}

// Slab-based segment vs expanded-AABB test (matches C# SegmentIntersectsExpandedAabb).
bool segment_hits(const Segment& seg, const RbAabb& obs, const RbConfig& cfg) {
    RbAabb e = expand_aabb(obs, cfg.tray_width / 2.0 + cfg.safety_margin, cfg.tray_height / 2.0 + cfg.safety_margin);
    RbVec3 delta = sub(seg.b, seg.a);
    double tMin = 0.0, tMax = 1.0;
    for (int axis = 0; axis < 3; ++axis) {
        double start = coord(seg.a, axis);
        double dir = coord(delta, axis);
        double lo = aabb_min(e, axis), hi = aabb_max(e, axis);
        if (std::abs(dir) <= kEpsilon) {
            if (start < lo || start > hi) return false;
            continue;
        }
        double inv = 1.0 / dir;
        double t0 = (lo - start) * inv;
        double t1 = (hi - start) * inv;
        if (t0 > t1) std::swap(t0, t1);
        tMin = std::max(tMin, t0);
        tMax = std::min(tMax, t1);
        if (tMin > tMax) return false;
    }
    return true;
}

bool is_blocked(const Segment& seg, const std::vector<RbAabb>& obstacles, const RbConfig& cfg) {
    for (const auto& o : obstacles) {
        if (o.is_penetration) continue;
        if (segment_hits(seg, o, cfg)) return true;
    }
    return false;
}

std::vector<RbVec3> build_snapped_points(RbVec3 start, RbVec3 end, const std::vector<RbVec3>& features,
                                         const std::vector<int>& required, double tolerance) {
    RbVec3 route = sub(end, start);
    double len = length(route);
    if (len < kEpsilon || features.empty()) return {start, end};

    std::vector<RbVec3> points{start};
    double maxDetour = std::max(len * 2.5, 10000.0);
    for (size_t i = 0; i < features.size(); ++i) {
        const RbVec3& f = features[i];
        bool isRequired = i < required.size() && required[i] != 0;
        if (!isRequired) {
            if (length(sub(f, start)) <= tolerance || length(sub(f, end)) <= tolerance) continue;
            if (distance_point_to_segment(f, start, end) > maxDetour) continue;
        }
        if (points.empty() || length(sub(points.back(), f)) > std::max(tolerance, 100.0)) points.push_back(f);
    }
    if (length(sub(points.back(), end)) > tolerance) points.push_back(end);
    else points.back() = end;
    return points;
}

// Names why each final segment's leading corner exists. Mirrors ManagedRubberBandEngine's
// ClassifySegmentReasons so both engines report identical reason codes for equivalent routes.
std::vector<int> classify_segment_reasons(const std::vector<Segment>& segments, const std::vector<RbVec3>& features,
                                          const std::vector<RbAabb>& obstacles, const RbConfig& cfg) {
    std::vector<int> reasons;
    reasons.reserve(segments.size());
    double clearanceH = cfg.tray_width / 2.0 + cfg.safety_margin + 1.0;
    double clearanceZ = cfg.tray_height / 2.0 + cfg.safety_margin + 1.0;
    double totalH = clearanceH * 2.0, totalZ = clearanceZ * 2.0;
    double featureTolerance = std::max(cfg.snap_tolerance, 300.0);
    const double eps = 5.0;

    for (size_t i = 0; i < segments.size(); ++i) {
        if (i == 0) { reasons.push_back(static_cast<int>(SegmentReason::RouteStart)); continue; }

        RbVec3 joint = segments[i].a;
        bool nearFeature = false;
        for (const auto& f : features) {
            if (length(sub(f, joint)) <= featureTolerance) { nearFeature = true; break; }
        }
        if (nearFeature) { reasons.push_back(static_cast<int>(SegmentReason::FeatureSnap)); continue; }

        bool nearObstacle = false;
        for (const auto& obs : obstacles) {
            if (obs.is_penetration) continue;
            RbAabb e = expand_aabb(obs, totalH, totalZ);
            bool onX = std::abs(joint.x - e.min_x) <= eps || std::abs(joint.x - e.max_x) <= eps;
            bool onY = std::abs(joint.y - e.min_y) <= eps || std::abs(joint.y - e.max_y) <= eps;
            bool onZ = std::abs(joint.z - e.min_z) <= eps || std::abs(joint.z - e.max_z) <= eps;
            bool withinX = joint.x >= e.min_x - eps && joint.x <= e.max_x + eps;
            bool withinY = joint.y >= e.min_y - eps && joint.y <= e.max_y + eps;
            bool withinZ = joint.z >= e.min_z - eps && joint.z <= e.max_z + eps;
            if ((onX && withinY && withinZ) || (onY && withinX && withinZ) || (onZ && withinX && withinY)) { nearObstacle = true; break; }
        }
        if (nearObstacle) { reasons.push_back(static_cast<int>(SegmentReason::CollisionBypass)); continue; }

        const Segment& previous = segments[i - 1];
        if (dominant_axis(sub(previous.b, previous.a)) != dominant_axis(sub(segments[i].b, segments[i].a))) {
            reasons.push_back(static_cast<int>(SegmentReason::DirectionChange));
            continue;
        }

        if (std::abs(previous.b.z - segments[i].a.z) > 10.0 || std::abs(segments[i].b.z - segments[i].a.z) > 10.0) {
            reasons.push_back(static_cast<int>(SegmentReason::ElevationChange));
            continue;
        }

        reasons.push_back(static_cast<int>(SegmentReason::RubberAlignment));
    }
    return reasons;
}

void append_orthogonal(std::vector<Segment>& out, RbVec3 start, RbVec3 end) {
    RbVec3 delta = sub(end, start);
    int order[3] = {0, 1, 2};
    std::sort(order, order + 3, [&](int a, int b) { return std::abs(coord(delta, a)) > std::abs(coord(delta, b)); });
    RbVec3 cur = start;
    for (int k = 0; k < 3; ++k) {
        int axis = order[k];
        double d = coord(end, axis) - coord(cur, axis);
        if (std::abs(d) <= 1e-3) continue;
        RbVec3 next = with_axis(cur, axis, coord(end, axis));
        out.push_back({cur, next});
        cur = next;
    }
}

void append_merged(std::vector<Segment>& target, const std::vector<Segment>& source) {
    for (const auto& seg : source) {
        if (seg_length(seg) <= 1e-3) continue;
        if (target.empty()) { target.push_back(seg); continue; }
        Segment& last = target.back();
        if (length(sub(last.b, seg.a)) <= 1e-3 && dominant_axis(sub(last.b, last.a)) == dominant_axis(sub(seg.b, seg.a)))
            last.b = seg.b;
        else
            target.push_back(seg);
    }
}

std::vector<Segment> make_orthogonal_segments(const std::vector<RbVec3>& points) {
    std::vector<Segment> out;
    for (size_t i = 0; i + 1 < points.size(); ++i) append_orthogonal(out, points[i], points[i + 1]);
    return out;
}

std::vector<RbAabb> corridor_obstacles(RbVec3 start, RbVec3 end, const std::vector<RbAabb>& obstacles, const RbConfig& cfg) {
    double margin = std::max(cfg.tray_width * 2.0 + cfg.safety_margin, 2000.0);
    RbAabb corridor{
        std::min(start.x, end.x) - margin, std::min(start.y, end.y) - margin, std::min(start.z, end.z) - margin,
        std::max(start.x, end.x) + margin, std::max(start.y, end.y) + margin, std::max(start.z, end.z) + margin, 0};
    std::vector<RbAabb> result;
    for (const auto& o : obstacles) {
        if (o.is_penetration) continue;
        if (aabb_overlap(o, corridor)) result.push_back(o);
    }
    RbVec3 s = start, e = end;
    std::sort(result.begin(), result.end(), [&](const RbAabb& a, const RbAabb& b) {
        return distance_point_to_segment(aabb_center(a), s, e) < distance_point_to_segment(aabb_center(b), s, e);
    });
    if (static_cast<int>(result.size()) > kCorridorObstacleLimit) result.resize(kCorridorObstacleLimit);
    return result;
}

void add_coord(std::vector<double>& values, double value) {
    if (std::isfinite(value)) values.push_back(value);
}

std::vector<double> normalize_coords(std::vector<double> values) {
    std::sort(values.begin(), values.end());
    std::vector<double> out;
    for (double v : values)
        if (out.empty() || std::abs(out.back() - v) > 1.0) out.push_back(v);
    return out;
}

int index_of(const std::vector<double>& values, double value) {
    int best = 0;
    double bestDist = std::numeric_limits<double>::max();
    for (size_t i = 0; i < values.size(); ++i) {
        double d = std::abs(values[i] - value);
        if (d < bestDist) { bestDist = d; best = static_cast<int>(i); }
    }
    return best;
}

void build_astar_lines(RbVec3 start, RbVec3 end, const std::vector<RbAabb>& gridObstacles, const RbConfig& cfg,
                       std::vector<double>& xs, std::vector<double>& ys, std::vector<double>& zs) {
    double clearanceH = cfg.tray_width / 2.0 + cfg.safety_margin + 1.0;
    double clearanceZ = cfg.tray_height / 2.0 + cfg.safety_margin + 1.0;
    xs = {start.x, end.x};
    ys = {start.y, end.y};
    zs = {start.z, end.z};
    for (const auto& obs : gridObstacles) {
        RbAabb e = expand_aabb(obs, clearanceH, clearanceZ);
        add_coord(xs, e.min_x - clearanceH); add_coord(xs, e.max_x + clearanceH);
        add_coord(ys, e.min_y - clearanceH); add_coord(ys, e.max_y + clearanceH);
        add_coord(zs, e.min_z - clearanceZ); add_coord(zs, e.max_z + clearanceZ);
    }
    xs = normalize_coords(xs);
    ys = normalize_coords(ys);
    zs = normalize_coords(zs);
}

struct GridNode { int x, y, z; };

std::vector<Segment> nodes_to_segments(const std::vector<GridNode>& nodes,
                                       const std::vector<double>& xs, const std::vector<double>& ys, const std::vector<double>& zs) {
    auto vec = [&](const GridNode& n) { return make(xs[n.x], ys[n.y], zs[n.z]); };
    std::vector<Segment> out;
    for (size_t i = 0; i + 1 < nodes.size(); ++i) {
        Segment seg{vec(nodes[i]), vec(nodes[i + 1])};
        if (seg_length(seg) > 1e-3) append_merged(out, {seg});
    }
    return out;
}

std::vector<Segment> route_astar_leg(RbVec3 start, RbVec3 end, const std::vector<RbAabb>& obstacles, const RbConfig& cfg) {
    if (length(sub(end, start)) <= 1e-3) return {};

    std::vector<RbAabb> collision = corridor_obstacles(start, end, obstacles, cfg);
    std::vector<RbAabb> grid = collision;
    if (static_cast<int>(grid.size()) > kGridObstacleLimit) grid.resize(kGridObstacleLimit);

    std::vector<double> xs, ys, zs;
    build_astar_lines(start, end, grid, cfg, xs, ys, zs);
    int NY = static_cast<int>(ys.size()), NZ = static_cast<int>(zs.size());
    auto key = [&](const GridNode& n) -> int64_t { return (static_cast<int64_t>(n.x) * NY + n.y) * NZ + n.z; };
    auto vec = [&](const GridNode& n) { return make(xs[n.x], ys[n.y], zs[n.z]); };

    GridNode startNode{index_of(xs, start.x), index_of(ys, start.y), index_of(zs, start.z)};
    GridNode endNode{index_of(xs, end.x), index_of(ys, end.y), index_of(zs, end.z)};
    int64_t endKey = key(endNode);

    auto heuristic = [&](const GridNode& n) {
        return std::abs(xs[n.x] - xs[endNode.x]) + std::abs(ys[n.y] - ys[endNode.y]) + std::abs(zs[n.z] - zs[endNode.z]);
    };

    using QItem = std::pair<double, GridNode>;
    auto cmp = [](const QItem& a, const QItem& b) { return a.first > b.first; };
    std::priority_queue<QItem, std::vector<QItem>, decltype(cmp)> open(cmp);
    std::unordered_map<int64_t, double> cost;
    std::unordered_map<int64_t, GridNode> cameFrom;

    cost[key(startNode)] = 0.0;
    open.push({heuristic(startNode), startNode});

    int NX = static_cast<int>(xs.size());
    int expansions = 0;
    while (!open.empty() && expansions++ < kMaxExpansions) {
        GridNode current = open.top().second;
        open.pop();
        int64_t curKey = key(current);
        if (curKey == endKey) {
            std::vector<GridNode> path{current};
            while (cameFrom.count(curKey)) { current = cameFrom[curKey]; curKey = key(current); path.push_back(current); }
            std::reverse(path.begin(), path.end());
            return nodes_to_segments(path, xs, ys, zs);
        }

        GridNode neighbors[6] = {
            {current.x - 1, current.y, current.z}, {current.x + 1, current.y, current.z},
            {current.x, current.y - 1, current.z}, {current.x, current.y + 1, current.z},
            {current.x, current.y, current.z - 1}, {current.x, current.y, current.z + 1}};
        for (const auto& next : neighbors) {
            if (next.x < 0 || next.x >= NX || next.y < 0 || next.y >= NY || next.z < 0 || next.z >= NZ) continue;
            Segment seg{vec(current), vec(next)};
            if (seg_length(seg) <= 1e-3 || is_blocked(seg, collision, cfg)) continue;
            double newCost = cost[key(current)] + seg_length(seg);
            int64_t nk = key(next);
            auto it = cost.find(nk);
            if (it != cost.end() && newCost >= it->second) continue;
            cost[nk] = newCost;
            cameFrom[nk] = current;
            open.push({newCost + heuristic(next), next});
        }
    }
    return {};
}

std::vector<Segment> route_via_waypoints(const std::vector<RbVec3>& points, const std::vector<RbAabb>& obstacles,
                                         const RbConfig& cfg, int& fallbackCount) {
    fallbackCount = 0;
    std::vector<Segment> route;
    for (size_t i = 0; i + 1 < points.size(); ++i) {
        std::vector<Segment> leg = route_astar_leg(points[i], points[i + 1], obstacles, cfg);
        if (leg.empty()) {
            ++fallbackCount;
            leg = make_orthogonal_segments({points[i], points[i + 1]});
        }
        append_merged(route, leg);
    }
    return route;
}

std::vector<RbVec3> segments_to_polyline(const std::vector<Segment>& segments) {
    std::vector<RbVec3> points;
    if (segments.empty()) return points;
    points.push_back(segments.front().a);
    for (const auto& s : segments) points.push_back(s.b);
    return points;
}

bool is_segment_clear(RbVec3 a, RbVec3 b, const std::vector<RbAabb>& obstacles, const RbConfig& cfg) {
    Segment seg{a, b};
    for (const auto& o : obstacles) {
        if (!o.is_penetration && segment_hits(seg, o, cfg)) return false;
    }
    return true;
}

bool is_required_point(RbVec3 p, const std::vector<RbVec3>& requiredPoints) {
    for (const auto& r : requiredPoints) {
        if (length(sub(r, p)) < 1.0) return true;
    }
    return false;
}

// A pipe run only ever turns 90 degrees in plan view, so a shortcut spanning both X and Y with Z
// not dominant (a "horizontal-plane diagonal") is never allowed, on any leg. A true multi-axis
// diagonal (e.g. a sloped drop mixing Z with X/Y) is only allowed on the very first leg --
// leaving the start PoC/equipment -- matching how far a real installer can angle a pipe right as
// it leaves its connection point; every later leg must stay single-axis. Mirrors
// ManagedRubberBandEngine.IsShortcutDirectionAllowed.
bool is_shortcut_direction_allowed(RbVec3 delta, bool isStartLeg) {
    constexpr double axisEpsilon = 5.0;
    bool ax = std::fabs(delta.x) > axisEpsilon;
    bool ay = std::fabs(delta.y) > axisEpsilon;
    bool az = std::fabs(delta.z) > axisEpsilon;

    bool horizontalDiagonal = ax && ay && std::fabs(delta.z) <= std::max(std::fabs(delta.x), std::fabs(delta.y));
    if (horizontalDiagonal) return false;

    if (isStartLeg) return true;

    int activeAxes = (ax ? 1 : 0) + (ay ? 1 : 0) + (az ? 1 : 0);
    return activeAxes <= 1;
}

// Greedy "string pulling" pass: mirrors ManagedRubberBandEngine.ApplyLineOfSightShortcuts. From
// each vertex, extend to the farthest later vertex still reachable by a straight line clear of
// every obstacle, then continue from there. Turns the orthogonal A* staircase into a taut rubber
// line wherever nothing is in the way. Required waypoints (e.g. a forced start-drop stub) must
// remain their own vertex even when nothing obstructs a longer shortcut past them. Diagonal
// travel is restricted per is_shortcut_direction_allowed above.
std::vector<Segment> apply_line_of_sight_shortcuts(const std::vector<Segment>& segments, const std::vector<RbAabb>& obstacles,
                                                   const RbConfig& cfg, const std::vector<RbVec3>& requiredPoints) {
    std::vector<RbVec3> points = segments_to_polyline(segments);
    if (points.size() < 3) return segments;

    std::vector<RbVec3> simplified{points.front()};
    size_t i = 0;
    while (i < points.size() - 1) {
        size_t hardLimit = points.size() - 1;
        for (size_t k = i + 1; k + 1 < points.size(); ++k) {
            if (is_required_point(points[k], requiredPoints)) { hardLimit = k; break; }
        }

        bool isStartLeg = (i == 0);
        size_t farthest = i + 1;
        for (size_t j = i + 2; j <= hardLimit; ++j) {
            RbVec3 candidate = sub(points[j], simplified.back());
            if (!is_shortcut_direction_allowed(candidate, isStartLeg)) break;
            if (is_segment_clear(simplified.back(), points[j], obstacles, cfg)) farthest = j;
            else break;
        }
        simplified.push_back(points[farthest]);
        i = farthest;
    }

    std::vector<Segment> out;
    for (size_t k = 0; k + 1 < simplified.size(); ++k) {
        if (length(sub(simplified[k + 1], simplified[k])) > 1e-3) out.push_back({simplified[k], simplified[k + 1]});
    }
    return out;
}

// Closest-point-between-two-lines intersection: line 1 through p1 with direction d1, line 2
// through p2 with direction d2. Returns false for parallel/degenerate lines, or when the two
// lines' closest points are too far apart to represent a genuine (near-coplanar) corner.
bool try_intersect_lines(RbVec3 p1, RbVec3 d1, RbVec3 p2, RbVec3 d2, RbVec3& intersection) {
    auto a = dot(d1, d1);
    auto b = dot(d1, d2);
    auto c = dot(d2, d2);
    auto w = sub(p1, p2);
    auto d = dot(d1, w);
    auto e = dot(d2, w);
    auto denom = a * c - b * b;
    if (std::fabs(denom) < 1e-6) return false;

    auto t = (b * e - c * d) / denom;
    auto s = (a * e - b * d) / denom;
    auto pointOnLine1 = add(p1, mul(d1, t));
    auto pointOnLine2 = add(p2, mul(d2, s));
    if (length(sub(pointOnLine1, pointOnLine2)) > std::max(50.0, length(sub(p1, p2)) * 0.1)) return false;

    intersection = mul(add(pointOnLine1, pointOnLine2), 0.5);
    return true;
}

// Mirrors ManagedRubberBandEngine.MergeShortDoglegs: collapses a short "dogleg" -- two
// consecutive corners joined by a very short connecting segment -- into a single corner, by
// intersecting the extended incoming and outgoing directions. Only applied when the merged
// corner's two new legs stay clear of every obstacle and neither original corner is a required
// waypoint. LOS shortcutting above only replaces fully-straight runs; without this pass a short
// in-between segment sandwiched between two long legs survives and makes the display-side bend
// rounding (which shrinks the usable radius to a fraction of the shortest adjoining run) look
// pinched/zigzagged there.
std::vector<Segment> merge_short_doglegs(const std::vector<Segment>& segments, const std::vector<RbAabb>& obstacles,
                                          const RbConfig& cfg, const std::vector<RbVec3>& requiredPoints) {
    std::vector<RbVec3> points = segments_to_polyline(segments);
    if (points.size() < 4) return segments;

    auto mergeThreshold = std::max(cfg.tray_width * 0.75, 300.0);

    std::vector<RbVec3> result{points.front()};
    size_t idx = 1;
    while (idx <= points.size() - 2) {
        if (idx + 1 <= points.size() - 2 && !is_required_point(points[idx], requiredPoints) && !is_required_point(points[idx + 1], requiredPoints)) {
            auto jog = length(sub(points[idx + 1], points[idx]));
            if (jog > 1.0 && jog < mergeThreshold) {
                auto prev = result.back();
                auto next = points[idx + 2];
                auto inDir = sub(points[idx], prev);
                auto outDir = sub(next, points[idx + 1]);
                // Same direction restriction as the LOS shortcut: a merged corner may only
                // introduce a multi-axis diagonal when anchored at the true route start.
                bool isStartLeg = length(sub(prev, points.front())) < 1.0;
                RbVec3 merged{};
                if (length(inDir) > 1.0 && length(outDir) > 1.0
                    && try_intersect_lines(prev, inDir, points[idx + 1], outDir, merged)
                    && is_shortcut_direction_allowed(sub(merged, prev), isStartLeg)
                    && is_shortcut_direction_allowed(sub(next, merged), isStartLeg)
                    && is_segment_clear(prev, merged, obstacles, cfg)
                    && is_segment_clear(merged, next, obstacles, cfg)) {
                    result.push_back(merged);
                    idx += 2;
                    continue;
                }
            }
        }
        result.push_back(points[idx]);
        ++idx;
    }
    result.push_back(points.back());

    std::vector<Segment> out;
    for (size_t k = 0; k + 1 < result.size(); ++k) {
        if (length(sub(result[k + 1], result[k])) > 1e-3) out.push_back({result[k], result[k + 1]});
    }
    return out;
}

int count_vertical_bends(const std::vector<Segment>& segs) {
    int count = 0;
    bool hasPrev = false, prev = false;
    for (const auto& s : segs) {
        bool cur = is_vertical(s);
        if (hasPrev && prev != cur && cur) ++count;
        prev = cur;
        hasPrev = true;
    }
    return count;
}

bool segment_hits_pipe(const Segment& seg, const RbAabb& obs, double radius, double safety_margin) {
    RbAabb e = expand_aabb(obs, radius + safety_margin, radius + safety_margin);
    RbVec3 delta = sub(seg.b, seg.a);
    double tMin = 0.0, tMax = 1.0;
    for (int axis = 0; axis < 3; ++axis) {
        double start = coord(seg.a, axis);
        double dir = coord(delta, axis);
        double lo = aabb_min(e, axis), hi = aabb_max(e, axis);
        if (std::abs(dir) <= kEpsilon) {
            if (start < lo || start > hi) return false;
            continue;
        }
        double inv = 1.0 / dir;
        double t0 = (lo - start) * inv;
        double t1 = (hi - start) * inv;
        if (t0 > t1) std::swap(t0, t1);
        tMin = std::max(tMin, t0);
        tMax = std::min(tMax, t1);
        if (tMin > tMax) return false;
    }
    return true;
}

bool has_residual_collision(const std::vector<std::vector<RbVec3>>& pipes, const std::vector<RbAabb>& obstacles, const RbConfig& cfg) {
    double radius = cfg.pipe_diameter / 2.0;
    for (const auto& pipe : pipes) {
        for (size_t i = 0; i + 1 < pipe.size(); ++i) {
            Segment s{pipe[i], pipe[i + 1]};
            for (const auto& o : obstacles) {
                if (o.is_penetration) continue;
                if (segment_hits_pipe(s, o, radius, cfg.safety_margin)) return true;
            }
        }
    }
    return false;
}

RbVec3 normal_for(const Segment& s, bool hasPrev, RbVec3 prev) {
    if (is_vertical(s) && hasPrev) return prev;
    auto d = sub(s.b, s.a);
    if (std::abs(d.x) >= std::abs(d.y)) return make(0, d.x >= 0 ? 1 : -1, 0);
    return make(d.y >= 0 ? -1 : 1, 0, 0);
}

std::vector<RbVec3> compute_segment_normals(const std::vector<Segment>& segments) {
    std::vector<RbVec3> normals;
    normals.reserve(segments.size());
    bool hasPrev = false;
    RbVec3 prev{};
    for (const auto& s : segments) {
        RbVec3 n = normal_for(s, hasPrev, prev);
        normals.push_back(n);
        prev = n;
        hasPrev = true;
    }
    return normals;
}

void distribute(Engine& e) {
    e.pipes.clear();
    double half = (e.cfg.pipe_count - 1) / 2.0;
    std::vector<RbVec3> normals = compute_segment_normals(e.segments);
    for (int p = 0; p < e.cfg.pipe_count; ++p) {
        double offset = (p - half) * e.cfg.pipe_pitch;
        std::vector<RbVec3> path;
        for (size_t i = 0; i < e.segments.size(); ++i) {
            // Offset both ends of segment i by segment i's own normal so this pipe's edge stays
            // parallel to the centerline segment (mirrors ManagedRubberBandEngine.DistributePipes).
            // At a turn, the previous segment's offset end and this segment's offset start
            // differ (different normals) — that gap is the natural connecting jog a parallel
            // pipe bend has; previously the shared corner point used the wrong segment's normal,
            // drawing a diagonal, non-parallel edge instead of a clean bend.
            const RbVec3& n = normals[i];
            RbVec3 start = add(e.segments[i].a, mul(n, offset));
            RbVec3 end = add(e.segments[i].b, mul(n, offset));
            if (path.empty() || length(sub(path.back(), start)) > 1e-6) path.push_back(start);
            path.push_back(end);
        }
        e.pipes.push_back(std::move(path));
    }
}

} // namespace

extern "C" RB_API RbEngineHandle rb_create(void) { return new Engine(); }
extern "C" RB_API void rb_destroy(RbEngineHandle engine) { delete static_cast<Engine*>(engine); }

extern "C" RB_API int rb_initialize(RbEngineHandle engine, RbConfig config) {
    if (!engine) return 1;
    if (config.snap_tolerance <= 0.0) config.snap_tolerance = 100.0;
    static_cast<Engine*>(engine)->cfg = config;
    return 0;
}

extern "C" RB_API int rb_set_obstacles(RbEngineHandle engine, const RbAabb* obstacles, int count) {
    if (!engine || count < 0) return 1;
    auto* e = static_cast<Engine*>(engine);
    e->obstacles.assign(obstacles, obstacles + count);
    return 0;
}

extern "C" RB_API int rb_set_features(RbEngineHandle engine, const RbVec3* features, int count) {
    if (!engine || count < 0) return 1;
    auto* e = static_cast<Engine*>(engine);
    e->features.assign(features, features + count);
    e->featureRequired.assign(count, 0);
    return 0;
}

extern "C" RB_API int rb_set_feature_flags(RbEngineHandle engine, const int* required, int count) {
    if (!engine || count < 0) return 1;
    auto* e = static_cast<Engine*>(engine);
    if (count != static_cast<int>(e->features.size())) return 1;
    e->featureRequired.assign(required, required + count);
    return 0;
}

extern "C" RB_API int rb_execute(RbEngineHandle engine, RbVec3 start, RbVec3 end) {
    if (!engine) return 1;
    auto* e = static_cast<Engine*>(engine);

    // Step 1 straight rubber line + Step 2 pull through feature control points.
    std::vector<RbVec3> snapped = build_snapped_points(start, end, e->features, e->featureRequired, e->cfg.snap_tolerance);
    // Step 3 orthogonal A* obstacle avoidance per waypoint leg.
    std::vector<Segment> routed = route_via_waypoints(snapped, e->obstacles, e->cfg, e->fallbackCount);
    // Step 4 pull the staircase taut wherever a straight line-of-sight shortcut is obstacle-free.
    // Required features must remain their own vertex even when nothing blocks a longer shortcut.
    std::vector<RbVec3> requiredPoints;
    for (size_t i = 0; i < e->features.size(); ++i) {
        if (i < e->featureRequired.size() && e->featureRequired[i] != 0) requiredPoints.push_back(e->features[i]);
    }
    auto straightened = apply_line_of_sight_shortcuts(routed, e->obstacles, e->cfg, requiredPoints);
    // Step 5 collapse short doglegs (see merge_short_doglegs) so display-side bend rounding
    // doesn't get pinched at a leftover short in-between corner pair.
    e->segments = merge_short_doglegs(straightened, e->obstacles, e->cfg, requiredPoints);
    // Step 6 pipe distribution.
    distribute(*e);

    e->verticalBends = count_vertical_bends(e->segments);
    bool residual = has_residual_collision(e->pipes, e->obstacles, e->cfg);
    e->valid = !residual && e->fallbackCount == 0 && e->verticalBends <= e->cfg.max_vertical_bends;
    e->segmentReasons = classify_segment_reasons(e->segments, e->features, e->obstacles, e->cfg);
    return 0;
}

extern "C" RB_API int rb_get_segment_count(RbEngineHandle engine) {
    return engine ? static_cast<int>(static_cast<Engine*>(engine)->segments.size()) : 0;
}

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

extern "C" RB_API int rb_get_pipe_count(RbEngineHandle engine) {
    return engine ? static_cast<int>(static_cast<Engine*>(engine)->pipes.size()) : 0;
}

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

extern "C" RB_API int rb_get_vertical_bends(RbEngineHandle engine) {
    return engine ? static_cast<Engine*>(engine)->verticalBends : 0;
}

extern "C" RB_API int rb_get_fallback_count(RbEngineHandle engine) {
    return engine ? static_cast<Engine*>(engine)->fallbackCount : 0;
}

extern "C" RB_API int rb_is_valid(RbEngineHandle engine) {
    return engine && static_cast<Engine*>(engine)->valid ? 1 : 0;
}

extern "C" RB_API int rb_get_segment_reason(RbEngineHandle engine, int segment_index) {
    if (!engine) return -1;
    auto* e = static_cast<Engine*>(engine);
    if (segment_index < 0 || segment_index >= static_cast<int>(e->segmentReasons.size())) return -1;
    return e->segmentReasons[segment_index];
}
