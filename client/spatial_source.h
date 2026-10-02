// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "anchor_manager.h"
#include "guided_capture.h"
#include "room_calibration.h"
#include "spatial_protocol.h"
#include <memory>
#include <optional>

namespace wivrn {
// Render/event thread only. Owned by application for the XR session, sharing its STAGE.
class spatial_source {
    XrInstance instance;
    XrSession session;
    XrSpace stage, view;
    std::filesystem::path directory;
    anchor::manager::dispatch api;
    std::array<std::unique_ptr<anchor::manager>, spatial::marker_count> anchors;
    std::array<XrSpace, spatial::entity_limit> scene_spaces{};
    spatial::snapshot current;
    room_capture::guide guide;
    std::optional<room_calibration::spot> calibration_spot;
    bool show_calibration = false;
    std::string calibration_message;
    XrTime last_sent = 0, change_at = 0, scene_deadline = 0;
    uint64_t request = 0;
    XrAsyncRequestIdFB scene_request = 0;
    bool capturing = false, retry_requested = false, skip_requested = false;
    bool scene_started = false, preview_markers = false;
    XrTime last_tick = 0;
    static bool scene_permission();
    PFN_xrGetSpaceSemanticLabelsFB labels{};
    PFN_xrGetSpaceBoundary2DFB boundary{};
    PFN_xrGetSpaceBoundingBox3DFB box{};
    void query_scene(XrTime);
    void scene_results();
public:
    spatial_source(XrInstance, XrSession, XrSpace stage, XrSpace view,
                   std::filesystem::path directory, bool supported, bool scene_supported);
    ~spatial_source();
    void event(const XrEventDataBuffer &);
    std::optional<spatial::snapshot> tick(XrTime, bool focused);
    void draw_controls(bool passthrough_available, const char * finish_label = "Return to streaming");
    bool start_capture(bool passthrough_available);
    void stop_capture();
    bool saved_marker(size_t slot) const;
    bool reload_marker(size_t slot);
    bool markers_visible() const { return capturing || preview_markers || show_calibration; }
    static constexpr size_t visual_marker_count = spatial::marker_count + 2;
    spatial::location visual_marker(size_t slot) const;
    bool save_calibration_spot();
    bool capture_active() const { return capturing; }
    const spatial::snapshot & sample() const { return current; }
};
}
