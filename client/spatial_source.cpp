// SPDX-License-Identifier: GPL-3.0-or-later
#include "spatial_source.h"
#include "imgui.h"
#include <algorithm>
#include <cstring>
#include <random>
#ifdef __ANDROID__
#include "android/permissions.h"
#endif

namespace wivrn {
namespace {
template<typename T> T function(XrInstance instance, const char * name) {
    T result{};
    xrGetInstanceProcAddr(instance, name, reinterpret_cast<PFN_xrVoidFunction *>(&result));
    return result;
}
bool scene_permission() {
#ifdef __ANDROID__
    return check_permission("com.oculus.permission.USE_SCENE");
#else
    return true;
#endif
}
}
spatial_source::spatial_source(XrInstance instance, XrSession session, XrSpace stage,
        XrSpace view, std::filesystem::path directory, bool supported, bool scene_supported) :
    instance(instance), session(session), stage(stage), view(view), directory(std::move(directory)),
    api(supported ? anchor::manager::dispatch::resolve(instance, xrGetInstanceProcAddr) : anchor::manager::dispatch{}) {
    std::random_device random;
    current.stream = (uint64_t(random()) << 32) | random();
    if (!current.stream) current.stream = 1;
    current.generation = 1;
    current.supported = supported && api.complete();
    std::filesystem::create_directories(this->directory);
    for (size_t i = 0; i < anchors.size(); ++i)
        anchors[i] = std::make_unique<anchor::manager>(session, stage, view,
            this->directory / ("marker-" + std::to_string(i) + ".uuid"), api);
    if (scene_supported && current.supported) {
        labels = function<PFN_xrGetSpaceSemanticLabelsFB>(instance, "xrGetSpaceSemanticLabelsFB");
        boundary = function<PFN_xrGetSpaceBoundary2DFB>(instance, "xrGetSpaceBoundary2DFB");
        box = function<PFN_xrGetSpaceBoundingBox3DFB>(instance, "xrGetSpaceBoundingBox3DFB");
    }
}
spatial_source::~spatial_source() {
    for (auto space : scene_spaces) if (space) xrDestroySpace(space);
}
void spatial_source::query_scene(XrTime now) {
    if (scene_started || !labels || !scene_permission()) return;
    scene_started = true;
    XrSpaceStorageLocationFilterInfoFB storage{XR_TYPE_SPACE_STORAGE_LOCATION_FILTER_INFO_FB};
    storage.location = XR_SPACE_STORAGE_LOCATION_LOCAL_FB;
    XrSpaceComponentFilterInfoFB filter{XR_TYPE_SPACE_COMPONENT_FILTER_INFO_FB};
    filter.next = &storage;
    filter.componentType = XR_SPACE_COMPONENT_TYPE_SEMANTIC_LABELS_FB;
    XrSpaceQueryInfoFB query{XR_TYPE_SPACE_QUERY_INFO_FB};
    query.queryAction = XR_SPACE_QUERY_ACTION_LOAD_FB;
    query.maxResultCount = spatial::entity_limit;
    query.timeout = 15'000'000'000;
    query.filter = reinterpret_cast<XrSpaceFilterInfoBaseHeaderFB *>(&filter);
    current.scene_result = api.query(session, reinterpret_cast<XrSpaceQueryInfoBaseHeaderFB *>(&query), &scene_request);
    current.scene_complete = XR_FAILED(current.scene_result);
    scene_deadline = now + 15'000'000'000;
}
void spatial_source::scene_results() {
    XrSpaceQueryResultsFB results{XR_TYPE_SPACE_QUERY_RESULTS_FB};
    auto result = api.retrieve(session, scene_request, &results);
    if (XR_FAILED(result) || results.resultCountOutput > spatial::entity_limit) {
        current.scene_result = XR_FAILED(result) ? result : XR_ERROR_LIMIT_REACHED;
        return;
    }
    std::array<XrSpaceQueryResultFB, spatial::entity_limit> entries{};
    results.resultCapacityInput = entries.size();
    results.results = entries.data();
    result = api.retrieve(session, scene_request, &results);
    if (XR_FAILED(result)) { current.scene_result = result; return; }
    for (uint32_t i = 0; i < results.resultCountOutput; ++i) {
        const auto & entry = entries[i];
        if (current.entity_count == spatial::entity_limit) { xrDestroySpace(entry.space); continue; }
        auto index = current.entity_count++;
        scene_spaces[index] = entry.space;
        auto & entity = current.entities[index];
        std::copy_n(entry.uuid.data, 16, entity.uuid.begin());
        XrSemanticLabelsFB text{XR_TYPE_SEMANTIC_LABELS_FB};
        text.bufferCapacityInput = entity.labels.size();
        text.buffer = reinterpret_cast<char *>(entity.labels.data());
        if (XR_FAILED(labels(session, entry.space, &text))) entity.labels.fill(0);
        entity.labels.back() = 0;
        if (boundary) {
            XrBoundary2DFB data{XR_TYPE_BOUNDARY_2D_FB};
            if (XR_SUCCEEDED(boundary(session, entry.space, &data))) {
                entity.boundary_omitted = data.vertexCountOutput > spatial::boundary_limit;
                if (!entity.boundary_omitted && data.vertexCountOutput) {
                    data.vertexCapacityInput = entity.boundary.size();
                    data.vertices = entity.boundary.data();
                    if (XR_SUCCEEDED(boundary(session, entry.space, &data))) entity.boundary_count = data.vertexCountOutput;
                }
            }
        }
        if (box) {
            XrRect3DfFB data{};
            entity.has_box = XR_SUCCEEDED(box(session, entry.space, &data));
            if (entity.has_box) {
                entity.box_offset = {data.offset.x, data.offset.y, data.offset.z};
                entity.box_extent = {data.extent.width, data.extent.height, data.extent.depth};
            }
        }
        XrSpaceComponentStatusSetInfoFB enable{XR_TYPE_SPACE_COMPONENT_STATUS_SET_INFO_FB};
        enable.componentType = XR_SPACE_COMPONENT_TYPE_LOCATABLE_FB;
        enable.enabled = XR_TRUE;
        XrAsyncRequestIdFB id{};
        api.set_component(entry.space, &enable, &id);
    }
}
void spatial_source::event(const XrEventDataBuffer & event) {
    for (auto & anchor : anchors) anchor->event(event);
    if (event.type == XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED &&
            reinterpret_cast<const XrEventDataSessionStateChanged &>(event).state != XR_SESSION_STATE_FOCUSED)
        guide.suspend();
    if (event.type == XR_TYPE_EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING) {
        const auto & change = reinterpret_cast<const XrEventDataReferenceSpaceChangePending &>(event);
        if (change.referenceSpaceType == XR_REFERENCE_SPACE_TYPE_STAGE || change.referenceSpaceType == XR_REFERENCE_SPACE_TYPE_LOCAL) {
            ++current.generation;
            change_at = std::max(change_at, change.changeTime);
            guide.reference_changed();
            last_sent = 0;
        }
    }
    if (event.type == XR_TYPE_EVENT_DATA_SPACE_QUERY_RESULTS_AVAILABLE_FB && scene_request) {
        const auto & query = reinterpret_cast<const XrEventDataSpaceQueryResultsAvailableFB &>(event);
        if (query.requestId == scene_request) scene_results();
    }
    if (event.type == XR_TYPE_EVENT_DATA_SPACE_QUERY_COMPLETE_FB && scene_request) {
        const auto & query = reinterpret_cast<const XrEventDataSpaceQueryCompleteFB &>(event);
        if (query.requestId == scene_request) {
            current.scene_complete = true;
            current.scene_result = query.result;
        }
    }
}
std::optional<spatial::snapshot> spatial_source::tick(XrTime now, bool focused) {
    bool active = focused && now >= change_at;
    if (active != current.active) last_sent = 0;
    current.active = active;
    XrSpaceLocation head{XR_TYPE_SPACE_LOCATION};
    head.pose.orientation.w = 1;
    if (!active || XR_FAILED(xrLocateSpace(view, stage, now, &head))) head.locationFlags = 0;
    current.head = {head.pose, head.locationFlags};
    std::array<room_capture::observation, spatial::marker_count> observations{};
    for (size_t i = 0; i < anchors.size(); ++i) {
        auto value = anchors[i]->tick(now);
        if (!active) value.flags = 0;
        current.markers[i] = {value.uuid, {value.pose, value.flags}, uint8_t(value.status), value.result, value.persisted};
        std::error_code error;
        observations[i] = {value, std::filesystem::exists(directory / ("marker-" + std::to_string(i) + ".uuid"), error), anchors[i]->busy()};
    }
    if (capturing) {
        if (auto op = guide.tick(now, head, active, observations,
                std::exchange(retry_requested, false), std::exchange(skip_requested, false))) {
            auto & anchor = *anchors[op->slot];
            if (op->op == room_capture::operation::create) {
                if (!anchor.create_at(++request, op->pose, now)) guide.rejected();
            } else if (op->op == room_capture::operation::load) {
                anchor.execute({++request, anchor::operation::load}, now);
            } else if (!anchor.retry(++request, now)) guide.rejected();
        }
    }
    if (active) query_scene(now);
    if (scene_started && !current.scene_complete && now > scene_deadline) {
        // Keep the request owned until its actual completion event arrives.
        current.scene_result = XR_TIMEOUT_EXPIRED;
    }
    for (size_t i = 0; i < current.entity_count; ++i) {
        XrSpaceLocation pose{XR_TYPE_SPACE_LOCATION};
        pose.pose.orientation.w = 1;
        if (!active || XR_FAILED(xrLocateSpace(scene_spaces[i], stage, now, &pose))) pose.locationFlags = 0;
        current.entities[i].pose = {pose.pose, pose.locationFlags};
    }
    current.bounds_available = xrGetReferenceSpaceBoundsRect(session, XR_REFERENCE_SPACE_TYPE_STAGE, &current.bounds) == XR_SUCCESS;
    current.capturing = capturing;
    current.accepted = guide.accepted_count();
    current.verified = guide.verified_count();
    // Scene geometry is bounded; five reliable snapshots per second keep it off the video channel.
    if (last_sent && now - last_sent < 200'000'000) return {};
    current.time = now;
    ++current.sequence;
    last_sent = now;
    return current;
}
void spatial_source::draw_controls(bool passthrough_available) {
    ImGui::TextWrapped("Capture saved room markers in WiVRn. Look around all four directions, then hold still for each marker. Keep the playspace clear.");
    if (!current.supported) { ImGui::TextWrapped("Spatial anchor extensions are unavailable on this headset."); return; }
    if (!passthrough_available) ImGui::TextWrapped("Passthrough is required to start room capture.");
    if (!capturing) {
        ImGui::BeginDisabled(!passthrough_available);
        if (ImGui::Button("Start / recheck 12 markers")) { guide = {}; capturing = true; }
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
        if (ImGui::Button("Return to streaming")) { capturing = false; guide.suspend(); }
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
