// SPDX-License-Identifier: GPL-3.0-or-later
#include "anchor_manager.h"
#include <algorithm>
#include <chrono>
#include <fstream>
#include <iomanip>
#include <vector>

namespace wivrn::anchor
{
manager::dispatch manager::dispatch::resolve(XrInstance instance, PFN_xrGetInstanceProcAddr get)
{
    dispatch out;
#define LOAD(member, name) get(instance, #name, reinterpret_cast<PFN_xrVoidFunction *>(&out.member))
    LOAD(create, xrCreateSpatialAnchorFB);
    LOAD(set_component, xrSetSpaceComponentStatusFB);
    LOAD(get_component, xrGetSpaceComponentStatusFB);
    LOAD(save, xrSaveSpaceFB);
    LOAD(erase, xrEraseSpaceFB);
    LOAD(query, xrQuerySpacesFB);
    LOAD(retrieve, xrRetrieveSpaceQueryResultsFB);
    LOAD(locate, xrLocateSpace);
    LOAD(destroy, xrDestroySpace);
#undef LOAD
    return out;
}
bool manager::dispatch::complete() const
{
    return create && set_component && get_component && save && erase && query && retrieve && locate && destroy;
}
manager::manager(XrSession s, XrSpace base, XrSpace hand, std::filesystem::path path, dispatch functions, bool auto_load) :
    api(functions), session(s), stage(base), controller(hand), file(std::move(path)), boot_load(auto_load)
{
    current.stream = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
    current.status = api.complete() ? state::empty : state::unsupported;
}
manager::~manager() { clear_space(); }
void manager::clear_space()
{
    if (space != XR_NULL_HANDLE) api.destroy(space);
    space = XR_NULL_HANDLE;
    current.flags = 0;
    enabling_locatable = enabling_storable = false;
}
void manager::fail(XrResult result)
{
    want_save = want_erase = false;
    current.result = result;
    current.status = state::error;
    current.flags = 0;
}
bool manager::read_uuid()
{
    std::ifstream input(file);
    std::string text;
    if (!(input >> text) || text.size() != 32 || text.find_first_not_of("0123456789abcdefABCDEF") != std::string::npos) return false;
    for (size_t i = 0; i < 16; ++i) current.uuid[i] = std::stoul(text.substr(i * 2, 2), nullptr, 16);
    return std::ranges::any_of(current.uuid, [](auto b) { return b != 0; });
}
bool manager::write_uuid()
{
    auto temporary = file;
    temporary += ".tmp";
    std::ofstream output(temporary, std::ios::trunc);
    for (auto b : current.uuid) output << std::hex << std::setw(2) << std::setfill('0') << int(b);
    output << '\n'; output.close();
    if (!output) return false;
    std::error_code error;
    std::filesystem::rename(temporary, file, error);
    return !error;
}
void manager::execute(command c, XrTime now)
{
    if (!api.complete()) return;
    // Only one asynchronous operation at a time; duplicate requests are idempotent.
    if (c.request != 0 && c.request <= current.request) return;
    if (work != task::none || place_at || want_save || want_erase) return;
    current.request = c.request;
    current.result = XR_SUCCESS;
    boot_load = false;
    if (c.op == operation::create)
    {
        // Never silently replace a saved anchor.
        if (space || std::filesystem::exists(file)) { fail(XR_ERROR_CALL_ORDER_INVALID); return; }
        place_at = now + 3'000'000'000;
        current.status = state::placing;
        return;
    }
    if (c.op == operation::load)
    {
        clear_space();
        current.persisted = false;
        if (!read_uuid()) { fail(XR_ERROR_FILE_CONTENTS_INVALID); return; }
        XrUuidEXT uuid{};
        std::copy(current.uuid.begin(), current.uuid.end(), uuid.data);
        XrSpaceUuidFilterInfoFB filter{XR_TYPE_SPACE_UUID_FILTER_INFO_FB};
        filter.uuidCount = 1; filter.uuids = &uuid;
        XrSpaceStorageLocationFilterInfoFB storage{XR_TYPE_SPACE_STORAGE_LOCATION_FILTER_INFO_FB};
        storage.location = XR_SPACE_STORAGE_LOCATION_LOCAL_FB;
        filter.next = &storage;
        XrSpaceQueryInfoFB info{XR_TYPE_SPACE_QUERY_INFO_FB};
        info.queryAction = XR_SPACE_QUERY_ACTION_LOAD_FB;
        info.maxResultCount = 1; info.timeout = 10'000'000'000;
        info.filter = reinterpret_cast<XrSpaceFilterInfoBaseHeaderFB *>(&filter);
        auto result = api.query(session, reinterpret_cast<XrSpaceQueryInfoBaseHeaderFB *>(&info), &pending);
        if (XR_FAILED(result)) { fail(result); return; }
        query_found = false; work = task::query; current.status = state::loading;
    }
    else if (c.op == operation::erase)
    {
        if (!space) { fail(XR_ERROR_CALL_ORDER_INVALID); return; }
        if (!current.persisted)
        {
            clear_space(); current.uuid = {}; current.status = state::empty; return;
        }
        // Querying a saved entity does not enable its STORABLE component.
        // Wait for that asynchronous enablement before calling xrEraseSpaceFB.
        want_erase = true; current.status = state::erasing; current.flags = 0;
    }
    deadline = now + 15'000'000'000;
}
bool manager::busy() const
{
    return work != task::none || place_at || want_save || want_erase;
}
bool manager::create_at(uint64_t request, const XrPosef &pose, XrTime now)
{
    if (!api.complete() || busy() || request <= current.request) return false;
    boot_load = false;
    current.request = request;
    if (space || std::filesystem::exists(file)) { fail(XR_ERROR_CALL_ORDER_INVALID); return false; }
    XrSpatialAnchorCreateInfoFB info{XR_TYPE_SPATIAL_ANCHOR_CREATE_INFO_FB};
    info.space = stage; info.poseInSpace = pose; info.time = now;
    auto result = api.create(session, &info, &pending);
    if (XR_FAILED(result)) { fail(result); return false; }
    current.result = XR_SUCCESS; current.status = state::creating;
    work = task::create; deadline = now + 15'000'000'000;
    return true;
}
bool manager::retry(uint64_t request, XrTime now)
{
    if (!api.complete() || busy() || request <= current.request) return false;
    if (std::filesystem::exists(file))
        execute({request, operation::load}, now);
    else
    {
        current.request = request; boot_load = false; current.result = XR_SUCCESS;
        // A failed save retains the same anchor; never leak/replace it on retry.
        if (space)
        {
            if (current.persisted && write_uuid()) { current.status = state::lost; return true; }
            enabling_locatable = enabling_storable = false;
            want_save = true; current.status = state::saving; deadline = now + 15'000'000'000;
        }
        else { current.status = state::empty; current.flags = 0; }
    }
    return true;
}
bool manager::component(XrSpaceComponentTypeFB kind, bool &requested)
{
    XrSpaceComponentStatusFB status{XR_TYPE_SPACE_COMPONENT_STATUS_FB};
    auto result = api.get_component(space, kind, &status);
    if (XR_FAILED(result)) { fail(result); return false; }
    if (status.enabled) return true;
    if (!status.changePending && !requested)
    {
        XrSpaceComponentStatusSetInfoFB info{XR_TYPE_SPACE_COMPONENT_STATUS_SET_INFO_FB};
        info.componentType = kind; info.enabled = XR_TRUE; info.timeout = 0;
        XrAsyncRequestIdFB request{};
        result = api.set_component(space, &info, &request);
        if (XR_FAILED(result) && result != XR_ERROR_SPACE_COMPONENT_STATUS_ALREADY_SET_FB) fail(result);
        requested = true;
    }
    return false;
}
void manager::event(const XrEventDataBuffer &buffer)
{
    if (buffer.type == XR_TYPE_EVENT_DATA_SPACE_SET_STATUS_COMPLETE_FB)
    {
        const auto &e = reinterpret_cast<const XrEventDataSpaceSetStatusCompleteFB &>(buffer);
        if (e.space == space && XR_FAILED(e.result)) { want_save = false; fail(e.result); }
    }
    if (buffer.type == XR_TYPE_EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING)
    {
        const auto &e = reinterpret_cast<const XrEventDataReferenceSpaceChangePending &>(buffer);
        if (e.referenceSpaceType == XR_REFERENCE_SPACE_TYPE_STAGE || e.referenceSpaceType == XR_REFERENCE_SPACE_TYPE_LOCAL)
        {
            change_at = std::max(change_at, e.changeTime);
            ++current.generation;
            current.flags = 0;
        }
    }
    if (buffer.type == XR_TYPE_EVENT_DATA_SPATIAL_ANCHOR_CREATE_COMPLETE_FB && work == task::create)
    {
        const auto &e = reinterpret_cast<const XrEventDataSpatialAnchorCreateCompleteFB &>(buffer);
        if (e.requestId != pending) return;
        work = task::none;
        if (XR_FAILED(e.result)) { fail(e.result); return; }
        space = e.space; std::copy(std::begin(e.uuid.data), std::end(e.uuid.data), current.uuid.begin());
        want_save = true; current.status = state::saving; deadline = 0;
    }
    if (buffer.type == XR_TYPE_EVENT_DATA_SPACE_QUERY_RESULTS_AVAILABLE_FB && work == task::query)
    {
        const auto &e = reinterpret_cast<const XrEventDataSpaceQueryResultsAvailableFB &>(buffer);
        if (e.requestId != pending) return;
        XrSpaceQueryResultsFB results{XR_TYPE_SPACE_QUERY_RESULTS_FB};
        auto result = api.retrieve(session, pending, &results);
        if (XR_FAILED(result)) { fail(result); return; }
        if (results.resultCountOutput > 16) { fail(XR_ERROR_LIMIT_REACHED); return; }
        std::vector<XrSpaceQueryResultFB> entries(results.resultCountOutput);
        results.resultCapacityInput = entries.size(); results.results = entries.data();
        result = api.retrieve(session, pending, &results);
        if (XR_FAILED(result)) { fail(result); return; }
        for (const auto &entry : entries)
        {
            if (!query_found && std::equal(current.uuid.begin(), current.uuid.end(), entry.uuid.data))
            { space = entry.space; query_found = true; current.persisted = true; }
            else if (entry.space != space) api.destroy(entry.space);
        }
    }
    if (buffer.type == XR_TYPE_EVENT_DATA_SPACE_QUERY_COMPLETE_FB && work == task::query)
    {
        const auto &e = reinterpret_cast<const XrEventDataSpaceQueryCompleteFB &>(buffer);
        if (e.requestId != pending) return;
        work = task::none;
        if (XR_FAILED(e.result) || !query_found) { fail(XR_FAILED(e.result) ? e.result : XR_ERROR_HANDLE_INVALID); return; }
        current.status = state::lost;
    }
    if (buffer.type == XR_TYPE_EVENT_DATA_SPACE_SAVE_COMPLETE_FB && work == task::save)
    {
        const auto &e = reinterpret_cast<const XrEventDataSpaceSaveCompleteFB &>(buffer);
        if (e.requestId != pending) return;
        work = task::none;
        if (XR_FAILED(e.result)) { fail(e.result); return; }
        current.persisted = true;
        if (!write_uuid()) { fail(XR_ERROR_FILE_ACCESS_ERROR); return; }
        current.status = state::lost;
    }
    if (buffer.type == XR_TYPE_EVENT_DATA_SPACE_ERASE_COMPLETE_FB && work == task::erase)
    {
        const auto &e = reinterpret_cast<const XrEventDataSpaceEraseCompleteFB &>(buffer);
        if (e.requestId != pending) return;
        work = task::none;
        if (XR_FAILED(e.result)) { fail(e.result); return; }
        clear_space(); current.persisted = false; current.uuid = {};
        std::error_code error; std::filesystem::remove(file, error);
        if (error) { fail(XR_ERROR_FILE_ACCESS_ERROR); return; }
        current.status = state::empty;
    }
}
sample manager::tick(XrTime now)
{
    current.time = now; ++current.sequence; current.flags = 0;
    if (!api.complete()) return current;
    if (boot_load)
    {
        boot_load = false;
        if (std::filesystem::exists(file)) execute({0, operation::load}, now);
    }
    if (place_at && now >= place_at)
    {
        place_at = 0;
        XrSpaceLocation location{XR_TYPE_SPACE_LOCATION};
        auto result = api.locate(controller, stage, now, &location);
        constexpr auto required = XR_SPACE_LOCATION_POSITION_VALID_BIT | XR_SPACE_LOCATION_ORIENTATION_VALID_BIT |
                                  XR_SPACE_LOCATION_POSITION_TRACKED_BIT | XR_SPACE_LOCATION_ORIENTATION_TRACKED_BIT;
        if (XR_FAILED(result) || (location.locationFlags & required) != required)
        { fail(XR_FAILED(result) ? result : XR_ERROR_POSE_INVALID); return current; }
        XrSpatialAnchorCreateInfoFB info{XR_TYPE_SPATIAL_ANCHOR_CREATE_INFO_FB};
        info.space = stage; info.poseInSpace = location.pose; info.time = now;
        result = api.create(session, &info, &pending);
        if (XR_FAILED(result)) { fail(result); return current; }
        work = task::create; current.status = state::creating; deadline = now + 15'000'000'000;
    }
    // Creation can finish after its timeout; give the subsequent save its own
    // deadline, measured from the first tick that can process that completion.
    if (want_save && deadline == 0) deadline = now + 15'000'000'000;
    if ((work != task::none || want_save || want_erase) && now > deadline)
    { fail(XR_TIMEOUT_EXPIRED); return current; } // Pending async work stays locked until its completion.
    if (space && work == task::none && current.status != state::error)
    {
        bool locatable = component(XR_SPACE_COMPONENT_TYPE_LOCATABLE_FB, enabling_locatable);
        if (want_save && component(XR_SPACE_COMPONENT_TYPE_STORABLE_FB, enabling_storable))
        {
            want_save = false;
            XrSpaceSaveInfoFB info{XR_TYPE_SPACE_SAVE_INFO_FB};
            info.space = space; info.location = XR_SPACE_STORAGE_LOCATION_LOCAL_FB;
            info.persistenceMode = XR_SPACE_PERSISTENCE_MODE_INDEFINITE_FB;
            auto result = api.save(session, &info, &pending);
            if (XR_FAILED(result)) fail(result);
            else { work = task::save; deadline = now + 15'000'000'000; current.status = state::saving; }
        }
        if (want_erase && component(XR_SPACE_COMPONENT_TYPE_STORABLE_FB, enabling_storable))
        {
            want_erase = false;
            XrSpaceEraseInfoFB info{XR_TYPE_SPACE_ERASE_INFO_FB};
            info.space = space; info.location = XR_SPACE_STORAGE_LOCATION_LOCAL_FB;
            auto result = api.erase(session, &info, &pending);
            if (XR_FAILED(result)) fail(result);
            else { work = task::erase; deadline = now + 15'000'000'000; }
        }
        if (locatable && work == task::none && !want_save && !want_erase && current.status != state::error && now >= change_at)
        {
            XrSpaceLocation location{XR_TYPE_SPACE_LOCATION};
            auto result = api.locate(space, stage, now, &location);
            constexpr auto required = XR_SPACE_LOCATION_POSITION_VALID_BIT | XR_SPACE_LOCATION_ORIENTATION_VALID_BIT;
            if (XR_SUCCEEDED(result) && (location.locationFlags & required) == required)
            { current.pose = location.pose; current.flags = location.locationFlags; current.status = state::localized; }
            else current.status = state::lost;
        }
    }
    return current;
}
}
