// SPDX-License-Identifier: MIT
// Read-only headless OpenXR provider. Does not submit layers or request focus.
#define XR_USE_TIMESPEC
#include <ctime>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <array>
#include <chrono>
#include <csignal>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
using json = nlohmann::json;
static volatile std::sig_atomic_t stopping = 0;
static void stop(int) { stopping = 1; }
static void check(XrResult r, const char *operation) {
    if (XR_FAILED(r)) throw std::runtime_error(std::string(operation)+": OpenXR result "+std::to_string(r));
}
static void emit(const json &data) { std::cout << data.dump() << std::endl; }
struct Handles {
    XrInstance instance{}; XrSession session{}; XrActionSet actions{};
    std::vector<XrSpace> spaces;
    ~Handles() {
        for (auto space : spaces) if (space) xrDestroySpace(space);
        if (session) xrDestroySession(session);
        if (actions) xrDestroyActionSet(actions);
        if (instance) xrDestroyInstance(instance);
    }
};
int main(int argc, char **argv) {
    std::signal(SIGTERM,stop); std::signal(SIGINT,stop);
    try {
        bool probe = argc > 1 && std::string(argv[1]) == "--probe";
        int samples = 0;
        if (argc == 3 && std::string(argv[1]) == "--samples") samples = std::stoi(argv[2]);
        uint32_t count = 0;
        check(xrEnumerateInstanceExtensionProperties(nullptr,0,&count,nullptr),"enumerate extensions");
        std::vector<XrExtensionProperties> props(count,{XR_TYPE_EXTENSION_PROPERTIES});
        check(xrEnumerateInstanceExtensionProperties(nullptr,count,&count,props.data()),"enumerate extensions");
        std::vector<std::string> extensions;
        for (auto &prop : props) extensions.emplace_back(prop.extensionName);
        auto has = [&](const char *name) { return std::find(extensions.begin(),extensions.end(),name) != extensions.end(); };
        bool headless = has(XR_MND_HEADLESS_EXTENSION_NAME) && has(XR_KHR_CONVERT_TIMESPEC_TIME_EXTENSION_NAME);
        emit({{"type","capabilities"},{"headless",headless},{"extensions",extensions},
              {"native_anchors",false},{"anchor_provider","not_implemented"}});
        if (probe) return 0;
        if (!headless) throw std::runtime_error("Headless/time-conversion extensions unavailable");
        Handles h;
        const char *enabled[] = {XR_MND_HEADLESS_EXTENSION_NAME,XR_KHR_CONVERT_TIMESPEC_TIME_EXTENSION_NAME};
        XrInstanceCreateInfo create{XR_TYPE_INSTANCE_CREATE_INFO};
        std::strcpy(create.applicationInfo.applicationName,"VRM Room Bridge");
        create.applicationInfo.apiVersion = XR_API_VERSION_1_0;
        create.enabledExtensionCount = 2; create.enabledExtensionNames = enabled;
        check(xrCreateInstance(&create,&h.instance),"create instance");
        XrInstanceProperties runtime{XR_TYPE_INSTANCE_PROPERTIES};
        check(xrGetInstanceProperties(h.instance,&runtime),"runtime properties");
        // Refuse an accidentally selected different runtime; this provider is WiVRn-specific.
        if (std::string(runtime.runtimeName).find("WiVRn") == std::string::npos && std::string(runtime.runtimeName).find("Monado") == std::string::npos)
            throw std::runtime_error("Selected runtime is not WiVRn/Monado");
        XrSystemGetInfo system_info{XR_TYPE_SYSTEM_GET_INFO}; system_info.formFactor=XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
        XrSystemId system{}; check(xrGetSystem(h.instance,&system_info,&system),"get system");
        XrActionSetCreateInfo set_info{XR_TYPE_ACTION_SET_CREATE_INFO};
        std::strcpy(set_info.actionSetName,"bridge_tracking"); std::strcpy(set_info.localizedActionSetName,"Bridge tracking");
        check(xrCreateActionSet(h.instance,&set_info,&h.actions),"create action set");
        std::array<XrPath,2> hands{};
        check(xrStringToPath(h.instance,"/user/hand/left",&hands[0]),"left hand path");
        check(xrStringToPath(h.instance,"/user/hand/right",&hands[1]),"right hand path");
        XrActionCreateInfo action_info{XR_TYPE_ACTION_CREATE_INFO};
        std::strcpy(action_info.actionName,"grip_pose"); std::strcpy(action_info.localizedActionName,"Grip pose");
        action_info.actionType=XR_ACTION_TYPE_POSE_INPUT; action_info.countSubactionPaths=2; action_info.subactionPaths=hands.data();
        XrAction grip{}; check(xrCreateAction(h.actions,&action_info,&grip),"create grip action");
        for (const char *profile : {"/interaction_profiles/khr/simple_controller","/interaction_profiles/oculus/touch_controller"}) {
            XrPath profile_path{},left{},right{};
            check(xrStringToPath(h.instance,profile,&profile_path),"profile path");
            check(xrStringToPath(h.instance,"/user/hand/left/input/grip/pose",&left),"left grip path");
            check(xrStringToPath(h.instance,"/user/hand/right/input/grip/pose",&right),"right grip path");
            XrActionSuggestedBinding bindings[]={{grip,left},{grip,right}};
            XrInteractionProfileSuggestedBinding suggested{XR_TYPE_INTERACTION_PROFILE_SUGGESTED_BINDING};
            suggested.interactionProfile=profile_path; suggested.countSuggestedBindings=2; suggested.suggestedBindings=bindings;
            auto result=xrSuggestInteractionProfileBindings(h.instance,&suggested);
            if (XR_FAILED(result) && result!=XR_ERROR_PATH_UNSUPPORTED) check(result,"suggest bindings");
        }
        XrSessionCreateInfo session_info{XR_TYPE_SESSION_CREATE_INFO}; session_info.systemId=system;
        check(xrCreateSession(h.instance,&session_info,&h.session),"create headless session");
        XrSessionActionSetsAttachInfo attach{XR_TYPE_SESSION_ACTION_SETS_ATTACH_INFO};attach.countActionSets=1;attach.actionSets=&h.actions;
        check(xrAttachSessionActionSets(h.session,&attach),"attach actions");
        auto make_reference=[&](XrReferenceSpaceType type) {
            XrReferenceSpaceCreateInfo info{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};info.referenceSpaceType=type;info.poseInReferenceSpace.orientation.w=1;
            XrSpace space{};check(xrCreateReferenceSpace(h.session,&info,&space),"create reference space");h.spaces.push_back(space);return space;
        };
        auto stage=make_reference(XR_REFERENCE_SPACE_TYPE_STAGE);
        auto view=make_reference(XR_REFERENCE_SPACE_TYPE_VIEW);
        std::array<XrSpace,2> grips{};
        for (size_t i=0;i<2;++i) {
            XrActionSpaceCreateInfo info{XR_TYPE_ACTION_SPACE_CREATE_INFO};info.action=grip;info.subactionPath=hands[i];info.poseInActionSpace.orientation.w=1;
            check(xrCreateActionSpace(h.session,&info,&grips[i]),"create grip space");h.spaces.push_back(grips[i]);
        }
        PFN_xrConvertTimespecTimeToTimeKHR convert{};
        check(xrGetInstanceProcAddr(h.instance,"xrConvertTimespecTimeToTimeKHR",reinterpret_cast<PFN_xrVoidFunction *>(&convert)),"get time conversion");
        bool running=false; uint64_t sequence=0,generation=0; XrTime change_at=0;
        emit({{"type","runtime"},{"name",runtime.runtimeName},{"reference_space","pc_stage"}});
        while (!stopping && (!samples || sequence<uint64_t(samples))) {
            for (;;) {
                XrEventDataBuffer event{XR_TYPE_EVENT_DATA_BUFFER};auto result=xrPollEvent(h.instance,&event);
                if (result==XR_EVENT_UNAVAILABLE) break;
                check(result,"poll events");
                if (event.type==XR_TYPE_EVENT_DATA_INSTANCE_LOSS_PENDING) throw std::runtime_error("OpenXR instance lost");
                if (event.type==XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED) {
                    auto &e=reinterpret_cast<XrEventDataSessionStateChanged &>(event);
                    if (e.state==XR_SESSION_STATE_READY) {
                        XrSessionBeginInfo begin{XR_TYPE_SESSION_BEGIN_INFO};begin.primaryViewConfigurationType=XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
                        check(xrBeginSession(h.session,&begin),"begin headless session");running=true;
                    }
                    if (e.state==XR_SESSION_STATE_STOPPING) {check(xrEndSession(h.session),"end session");running=false;}
                    if (e.state==XR_SESSION_STATE_EXITING || e.state==XR_SESSION_STATE_LOSS_PENDING) stopping=1;
                }
                if (event.type==XR_TYPE_EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING) {
                    auto &e=reinterpret_cast<XrEventDataReferenceSpaceChangePending &>(event);
                    if (e.referenceSpaceType==XR_REFERENCE_SPACE_TYPE_STAGE) {++generation;change_at=std::max(change_at,e.changeTime);}
                }
            }
            if (running) {
                timespec clock{};clock_gettime(CLOCK_MONOTONIC,&clock);XrTime now{};
                check(convert(h.instance,&clock,&now),"convert time");
                XrActiveActionSet active{h.actions,XR_NULL_PATH};XrActionsSyncInfo sync{XR_TYPE_ACTIONS_SYNC_INFO};sync.countActiveActionSets=1;sync.activeActionSets=&active;
                auto synced=xrSyncActions(h.session,&sync);check(synced,"sync actions");
                auto locate=[&](XrSpace space,bool active_pose) {
                    XrSpaceLocation location{XR_TYPE_SPACE_LOCATION};location.pose.orientation.w=1;
                    auto result=xrLocateSpace(space,stage,now,&location);
                    bool valid=XR_SUCCEEDED(result)&&active_pose&&now>=change_at&&(location.locationFlags&3)==3;
                    const auto &p=location.pose.position;const auto &q=location.pose.orientation;
                    return json{{"valid",valid},{"flags",valid?location.locationFlags:0},{"position",{p.x,p.y,p.z}},{"rotation",{q.x,q.y,q.z,q.w}}};
                };
                json devices;devices["head"]=locate(view,true);
                for (size_t i=0;i<2;++i) {
                    XrActionStateGetInfo info{XR_TYPE_ACTION_STATE_GET_INFO};info.action=grip;info.subactionPath=hands[i];
                    XrActionStatePose state{XR_TYPE_ACTION_STATE_POSE};check(xrGetActionStatePose(h.session,&info,&state),"grip state");
                    auto value=locate(grips[i],state.isActive&&synced==XR_SUCCESS);value["pose_space"]="grip";
                    XrInteractionProfileState profile{XR_TYPE_INTERACTION_PROFILE_STATE};char name[XR_MAX_PATH_LENGTH]{};uint32_t count{};
                    if(XR_SUCCEEDED(xrGetCurrentInteractionProfile(h.session,hands[i],&profile))&&profile.interactionProfile!=XR_NULL_PATH&&
                       XR_SUCCEEDED(xrPathToString(h.instance,profile.interactionProfile,sizeof(name),&count,name)))value["interaction_profile"]=name;
                    devices[i==0?"left_hand":"right_hand"]=value;
                }
                XrExtent2Df bounds{};
                const auto bounds_result=xrGetReferenceSpaceBoundsRect(h.session,XR_REFERENCE_SPACE_TYPE_STAGE,&bounds);
                json boundary={{"kind","stage_rectangle"},{"available",bounds_result==XR_SUCCESS && bounds.width>0 && bounds.height>0},
                               {"width",bounds.width},{"depth",bounds.height},{"result",bounds_result},
                               {"source","runtime_reported"},{"guardian_polygon",false}};
                emit({{"type","tracking"},{"sequence",++sequence},{"generation",generation},
                      {"sample_time_ns",std::to_string(now)},{"reference_space","pc_stage"},{"devices",devices},
                      {"boundary",boundary},{"anchors",json::object()}});
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
    } catch (const std::exception &e) {emit({{"type","error"},{"message",e.what()}});return 1;}
}
