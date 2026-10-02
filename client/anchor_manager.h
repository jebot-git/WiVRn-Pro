// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "anchor_protocol.h"
#include <filesystem>
#include <string>

namespace wivrn::anchor
{
// Owned and called exclusively by the application's render/event thread.
class manager
{
public:
    struct dispatch
    {
        PFN_xrCreateSpatialAnchorFB create{};
        PFN_xrSetSpaceComponentStatusFB set_component{};
        PFN_xrGetSpaceComponentStatusFB get_component{};
        PFN_xrSaveSpaceFB save{};
        PFN_xrEraseSpaceFB erase{};
        PFN_xrQuerySpacesFB query{};
        PFN_xrRetrieveSpaceQueryResultsFB retrieve{};
        PFN_xrLocateSpace locate{};
        PFN_xrDestroySpace destroy{};
        static dispatch resolve(XrInstance, PFN_xrGetInstanceProcAddr);
        bool complete() const;
    };
    manager(XrSession, XrSpace stage, XrSpace controller, std::filesystem::path, dispatch, bool auto_load = true);
    ~manager();
    manager(const manager &) = delete;
    manager & operator=(const manager &) = delete;
    void execute(command, XrTime now);
    // Guided capture supplies a pose frozen after its tracking/dwell checks.
    bool create_at(uint64_t request, const XrPosef &, XrTime now);
    bool retry(uint64_t request, XrTime now);
    bool busy() const;
    void event(const XrEventDataBuffer &);
    sample tick(XrTime now);
private:
    dispatch api;
    XrSession session;
    XrSpace stage, controller, space = XR_NULL_HANDLE;
    std::filesystem::path file;
    sample current;
    XrAsyncRequestIdFB pending = 0;
    enum class task { none, create, query, save, erase } work = task::none;
    XrTime deadline = 0, place_at = 0, change_at = 0;
    bool want_save = false, want_erase = false, query_found = false, boot_load = true;
    bool enabling_locatable = false, enabling_storable = false;
    void fail(XrResult);
    void clear_space();
    bool component(XrSpaceComponentTypeFB, bool &requested);
    bool read_uuid();
    bool write_uuid();
};
}
