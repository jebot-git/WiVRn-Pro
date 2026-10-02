// SPDX-License-Identifier: GPL-3.0-or-later
#include "spatial_source.h"
#include "imgui.h"
#ifdef __ANDROID__
#include "android/permissions.h"
#endif

namespace wivrn {
void spatial_source::draw_controls(bool passthrough_available, const char * finish_label) {
    ImGui::TextWrapped("Capture saved room markers in WiVRn. Look around all four directions, then hold still for each marker. Keep the playspace clear.");
    if (!current.supported) { ImGui::TextWrapped("Spatial anchor extensions are unavailable on this headset."); return; }
    if (!passthrough_available) ImGui::TextWrapped("Passthrough is required to start room capture.");
    if (!capturing) {
        ImGui::BeginDisabled(!passthrough_available);
        if (ImGui::Button("Start / recheck 12 markers")) start_capture(passthrough_available);
        ImGui::EndDisabled();
    } else {
        ImGui::TextWrapped("%s", guide.instruction.c_str());
        ImGui::TextWrapped("%s", guide.detail.c_str());
        ImGui::ProgressBar(guide.progress);
        ImGui::Text("Saved: %d / 12   Verified: %d / 12", guide.accepted_count(), guide.verified_count());
        if (ImGui::Button("Retry / recheck")) retry_requested = true;
        ImGui::SameLine();
        if (ImGui::Button("Skip")) skip_requested = true;
        ImGui::SameLine();
        if (ImGui::Button(finish_label)) stop_capture();
    }
    ImGui::Checkbox("Show marker axes", &preview_markers);
    if (ImGui::CollapsingHeader("Saved marker helper")) {
        ImGui::TextWrapped("Reload a saved marker if it cannot be located. Reload keeps its saved identity; it does not replace the anchor.");
        for (size_t i = 0; i < anchors.size(); ++i) {
            const auto & marker = current.markers[i];
            const bool located = (marker.pose.flags & (XR_SPACE_LOCATION_POSITION_VALID_BIT | XR_SPACE_LOCATION_ORIENTATION_VALID_BIT)) ==
                (XR_SPACE_LOCATION_POSITION_VALID_BIT | XR_SPACE_LOCATION_ORIENTATION_VALID_BIT);
            ImGui::PushID(int(i));
            ImGui::Text("%02u: %s / %s (result %d)", unsigned(i + 1),
                saved_marker(i) ? "saved UUID" : "not saved", located ? "located" : "unavailable", marker.result);
            ImGui::SameLine();
            ImGui::BeginDisabled(capturing || anchors[i]->busy() || !saved_marker(i));
            if (ImGui::SmallButton("Reload")) reload_marker(i);
            ImGui::EndDisabled();
            ImGui::PopID();
        }
    }
    ImGui::TextWrapped("Saved markers continue to localize while streaming. PC alignment comes from WiVRn's runtime spaces; no app switch or controller calibration is needed.");
    if (labels && !scene_permission()) {
#ifdef __ANDROID__
        if (ImGui::Button("Allow room scene access")) request_permission("com.oculus.permission.USE_SCENE", [](bool) {});
#endif
    } else if (labels) {
        ImGui::Text("Room entities: %u / 64   Query result: %d", current.entity_count, current.scene_result);
        if (current.scene_complete && ImGui::Button("Reload room scene")) {
            for (auto & space : scene_spaces) { if (space) xrDestroySpace(space); space = XR_NULL_HANDLE; }
            current.entities = {};
            current.entity_count = 0;
            current.scene_complete = false;
            scene_request = 0;
            scene_started = false;
        }
    }
}
}
