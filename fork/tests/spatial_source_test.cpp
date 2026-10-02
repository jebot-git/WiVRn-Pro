// SPDX-License-Identifier: GPL-3.0-or-later
#include "spatial_source.h"
#include <cassert>
#include <cstring>
#include <fstream>
#include <iostream>
#include <source_location>
#include <unistd.h>

static int checks = 0, creates = 0, saves = 0, destroys = 0;
static void check(bool ok, std::source_location at = std::source_location::current()) {
    if (!ok) std::cerr << at.line() << " check failed\n";
    assert(ok); ++checks;
}
static auto session = reinterpret_cast<XrSession>(1);
static auto stage = reinterpret_cast<XrSpace>(2);
static auto view = reinterpret_cast<XrSpace>(3);
static auto anchor_space = reinterpret_cast<XrSpace>(4);
static XrAsyncRequestIdFB serial = 0;
static bool found = true, tracked = true;
static XrPosef head{{0,0,0,1},{0,1.6f,0}}, captured{};
static XrUuidEXT uuid{{1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16}};
static XrResult XRAPI_CALL create(XrSession, const XrSpatialAnchorCreateInfoFB *i, XrAsyncRequestIdFB *r) {
    check(i->space == stage); captured = i->poseInSpace; *r = ++serial; ++creates; return XR_SUCCESS;
}
static XrResult XRAPI_CALL set_component(XrSpace, const XrSpaceComponentStatusSetInfoFB *, XrAsyncRequestIdFB *r) { *r = ++serial; return XR_SUCCESS; }
static XrResult XRAPI_CALL get_component(XrSpace, XrSpaceComponentTypeFB, XrSpaceComponentStatusFB *s) { s->enabled = XR_TRUE; return XR_SUCCESS; }
static XrResult XRAPI_CALL save(XrSession, const XrSpaceSaveInfoFB *, XrAsyncRequestIdFB *r) { *r = ++serial; ++saves; return XR_SUCCESS; }
static XrResult XRAPI_CALL erase(XrSession, const XrSpaceEraseInfoFB *, XrAsyncRequestIdFB *) { check(false); return XR_ERROR_RUNTIME_FAILURE; }
static XrResult XRAPI_CALL query(XrSession, const XrSpaceQueryInfoBaseHeaderFB *, XrAsyncRequestIdFB *r) { *r = ++serial; return XR_SUCCESS; }
static XrResult XRAPI_CALL retrieve(XrSession, XrAsyncRequestIdFB, XrSpaceQueryResultsFB *r) {
    r->resultCountOutput = found ? 1 : 0;
    if (found && r->resultCapacityInput) r->results[0] = {anchor_space, uuid};
    return XR_SUCCESS;
}
extern "C" XrResult XRAPI_CALL xrLocateSpace(XrSpace space, XrSpace base, XrTime, XrSpaceLocation *l) {
    check(base == stage); l->pose = space == view ? head : captured; l->locationFlags = tracked ? 15 : 0; return XR_SUCCESS;
}
extern "C" XrResult XRAPI_CALL xrDestroySpace(XrSpace) { ++destroys; return XR_SUCCESS; }
extern "C" XrResult XRAPI_CALL xrGetReferenceSpaceBoundsRect(XrSession, XrReferenceSpaceType, XrExtent2Df *b) { *b = {2,3}; return XR_SUCCESS; }
extern "C" XrResult XRAPI_CALL xrGetInstanceProcAddr(XrInstance, const char *name, PFN_xrVoidFunction *p) {
#define FN(x, f) if (!std::strcmp(name, #x)) { *p = reinterpret_cast<PFN_xrVoidFunction>(f); return XR_SUCCESS; }
    FN(xrCreateSpatialAnchorFB, create)
    FN(xrSetSpaceComponentStatusFB, set_component)
    FN(xrGetSpaceComponentStatusFB, get_component)
    FN(xrSaveSpaceFB, save)
    FN(xrEraseSpaceFB, erase)
    FN(xrQuerySpacesFB, query)
    FN(xrRetrieveSpaceQueryResultsFB, retrieve)
    FN(xrLocateSpace, xrLocateSpace)
    FN(xrDestroySpace, xrDestroySpace)
#undef FN
    *p = nullptr; return XR_ERROR_FUNCTION_UNSUPPORTED;
}
template<class T> void event(wivrn::spatial_source &s, T e) {
    XrEventDataBuffer b{}; static_assert(sizeof(e) <= sizeof(b)); std::memcpy(&b, &e, sizeof(e)); s.event(b);
}
static void queried(wivrn::spatial_source &s) {
    XrEventDataSpaceQueryResultsAvailableFB e{XR_TYPE_EVENT_DATA_SPACE_QUERY_RESULTS_AVAILABLE_FB}; e.requestId = serial; event(s, e);
    XrEventDataSpaceQueryCompleteFB done{XR_TYPE_EVENT_DATA_SPACE_QUERY_COMPLETE_FB}; done.requestId = serial; done.result = XR_SUCCESS; event(s, done);
}
int main() {
    auto path = std::filesystem::temp_directory_path() / ("wivrn-source-test-" + std::to_string(getpid()));
    std::filesystem::create_directories(path);
    auto instance = reinterpret_cast<XrInstance>(1);
    {
        wivrn::spatial_source s(instance, session, stage, view, path, false, false);
        check(!s.start_capture(true) && !s.reload_marker(0));
        check(!s.tick(1, true)->supported);
    }
    uint64_t source_session = 0;
    {
        wivrn::spatial_source s(instance, session, stage, view, path, true, false);
        check(!s.start_capture(false) && !s.markers_visible());
        check(s.start_capture(true) && s.markers_visible());
        check(!s.start_capture(true) && !s.reload_marker(0));
        XrTime now = 1;
        auto tick = [&] { now += 100'000'000; s.tick(now, true); };
        // Complete the initial four-direction survey, then wait for one creation.
        for (int direction = 0; direction < 4; ++direction) {
            head.orientation = room_capture::rotation(-direction * room_capture::pi / 2);
            for (int i = 0; i < 13; ++i) tick();
        }
        head.orientation = room_capture::rotation(0);
        for (int i = 0; i < 20 && !creates; ++i) tick();
        check(creates == 1);
        source_session = s.sample().stream;
        auto sequence = s.sample().sequence;
        // Leaving capture / changing the consuming scene must retain async work.
        s.stop_capture(); check(!s.capture_active() && !s.markers_visible());
        check(!s.reload_marker(0));
        XrEventDataSpatialAnchorCreateCompleteFB created{XR_TYPE_EVENT_DATA_SPATIAL_ANCHOR_CREATE_COMPLETE_FB};
        created.requestId = serial; created.result = XR_SUCCESS; created.space = anchor_space; created.uuid = uuid; event(s, created);
        tick(); check(saves == 1);
        XrEventDataSpaceSaveCompleteFB saved{XR_TYPE_EVENT_DATA_SPACE_SAVE_COMPLETE_FB};
        saved.requestId = serial; saved.result = XR_SUCCESS; saved.space = anchor_space; saved.uuid = uuid; event(s, saved);
        tick(); tick();
        check(s.saved_marker(0) && !s.saved_marker(12));
        check(s.sample().stream == source_session && s.sample().sequence > sequence);
        check(s.sample().markers[0].persisted && s.sample().markers[0].pose.flags == 15);
        check(s.save_calibration_spot() && s.markers_visible());
        check(s.visual_marker(wivrn::spatial::marker_count).flags == 15);
        check(s.visual_marker(0).flags == 0); // Guide alone does not enable all marker axes.
        check(s.visual_marker(wivrn::spatial_source::visual_marker_count).flags == 0);
        check(!s.reload_marker(1) && !s.reload_marker(12));
        check(s.reload_marker(0) && !s.reload_marker(0));
        found = false; queried(s); tick();
        // A saved UUID remains reloadable after an unsuccessful localization.
        check(s.saved_marker(0) && !s.sample().markers[0].persisted);
        check(s.visual_marker(wivrn::spatial::marker_count).flags == 0);
        check(s.reload_marker(0)); found = true; queried(s); tick();
        check(s.sample().markers[0].pose.flags == 15 && creates == 1);
        auto inactive = s.tick(now + 1, false);
        check(inactive && !inactive->active && inactive->head.flags == 0 && inactive->markers[0].pose.flags == 0);
        XrEventDataReferenceSpaceChangePending change{XR_TYPE_EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING};
        change.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_STAGE; change.changeTime = now + 1'000'000'000; event(s, change);
        check(s.visual_marker(wivrn::spatial::marker_count).flags == 0 && !s.save_calibration_spot());
        check(!s.tick(now + 200'000'000, true)->active);
        auto after = s.tick(change.changeTime, true);
        check(after && after->active && after->generation == 2 && after->markers[0].pose.flags == 15);
        check(s.visual_marker(wivrn::spatial::marker_count).flags == 15);
        check(!s.tick(change.changeTime + 1, true)); // Five-Hz publication limit.
        tracked = false; s.tick(change.changeTime + 200'000'000, true);
        check(s.sample().head.flags == 0 && s.sample().markers[0].pose.flags == 0);
        check(s.visual_marker(wivrn::spatial::marker_count).flags == 0);
        tracked = true;
    }
    {
        // New XR/application session auto-loads existing files, with a new epoch.
        wivrn::spatial_source s(instance, session, stage, view, path, true, false);
        auto sample = s.tick(1, true);
        check(sample->stream != source_session && sample->generation == 1);
        check(sample->markers[0].status == uint8_t(wivrn::anchor::state::loading));
        check(!s.markers_visible()); // Loading a saved guide never enables an overlay implicitly.
        queried(s); s.tick(2, true);
        check(s.sample().markers[0].persisted && s.sample().markers[0].pose.flags == 15);
    }
    check(destroys == 3 && creates == 1);
    std::filesystem::remove_all(path);
    std::cout << "SPATIAL_SOURCE " << checks << " checks passed\n";
}
