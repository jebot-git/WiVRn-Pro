// SPDX-License-Identifier: MIT
#pragma once
#include "codec.hpp"
namespace vr444 {
// Degrees in the same eye-relative visual space. For warped streams, the
// renderer must supply footprints through its inverse spatial warp.
struct Footprint { double left, right, bottom, top; };
struct Gaze { double yaw, pitch, confidence; int64_t time_ns; bool valid; };
struct FoveationConfig {
    double core_deg=6, guard_deg=2, transition_deg=12;
    double confidence_min=.6, stale_ms=50, predict_max_ms=25, velocity_max=720;
    double prediction_max_deg=5;
    unsigned core_q=4, transition_q=32, peripheral_q=128, fallback_q=16;
};
struct QualityMap {
    std::vector<uint32_t> q, priority; // 0 protected, 1 transition, 2 peripheral.
    bool fallback;
    double predicted_yaw, predicted_pitch, protected_radius;
};
struct SpatialWarp { Footprint fov; double x0,x1,y0,y1,density; };
SpatialWarp spatial_warp(Footprint fov,double gaze_yaw,double gaze_pitch,double radius_deg=8,double density=8);
std::vector<Footprint> warped_footprints(unsigned w,unsigned h,const SpatialWarp& warp);
std::vector<uint32_t> warp_rgb(const std::vector<uint32_t>& source,unsigned sw,unsigned sh,
                             unsigned width,unsigned height,const SpatialWarp& warp);
std::vector<Footprint> rectilinear_footprints(unsigned w,unsigned h,Footprint fov);
QualityMap gaze_map(const std::vector<Footprint>& footprints,Gaze gaze,Gaze previous,
                    int64_t display_ns,const FoveationConfig& config={});
struct DynamicFrame { std::vector<uint32_t> pixels; QualityMap quality; SpatialWarp warp; };
DynamicFrame prepare_foveated(const std::vector<uint32_t>& source,unsigned sw,unsigned sh,
                             unsigned width,unsigned height,Footprint fov,Gaze gaze,Gaze previous,
                             int64_t display_ns,const FoveationConfig& config={});
struct BudgetResult { Frame frame; bool fits; unsigned passes; size_t wire_bytes; };
// Reference allocator, not a real-time controller: protects gaze tiles and
// refuses a frame if peripheral/transition degradation cannot meet the cap.
BudgetResult encode_budgeted(const std::vector<uint32_t>& rgba,unsigned w,unsigned h,
                             const QualityMap& map,size_t wire_budget);
size_t modeled_wire_bytes(const Frame& frame);
double protected_psnr(const std::vector<uint32_t>& a,const std::vector<uint32_t>& b,
                      unsigned w,const QualityMap& map);
void foveation_tests();
void foveation_bench();
}
