// SPDX-License-Identifier: GPL-3.0-or-later
// Separate Quest app; reuses the tested GPL anchor manager, not WiVRn networking.
#define XR_USE_PLATFORM_ANDROID
#define XR_USE_GRAPHICS_API_OPENGL_ES
#include <jni.h>
#include <EGL/egl.h>
#include <GLES3/gl3.h>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>
#include <android/log.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <atomic>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <iomanip>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <vector>
#include <nlohmann/json.hpp>
#include "anchor_manager.h"
#include "guided_capture.h"
#include "capture_hud.h"
using json=nlohmann::json;
using namespace std::chrono_literals;
static std::atomic<bool> resumed{false}, stopping{false}, launched{false};
static bool calibration_mode=false;
static JavaVM *vm{};
static jobject activity{};
static std::mutex wire_mutex;
static int wire=-1, port=47845;
static std::string token, stream_id;
static uint64_t sequence=0;
static auto retry=std::chrono::steady_clock::time_point{};
static void check(XrResult r,const char *what) {
    if (XR_FAILED(r)) throw std::runtime_error(std::string(what)+": "+std::to_string(r));
}
static bool send_all(const std::string &s) {
    size_t sent=0;
    while(sent<s.size()) {
        auto n=send(wire,s.data()+sent,s.size()-sent,MSG_NOSIGNAL);
        if(n<=0) return false;
        sent+=n;
    }
    return true;
}
static void emit(json data) {
    std::lock_guard lock(wire_mutex);
    data["type"]="quest_snapshot";data["source_session"]=stream_id;data["sequence"]=++sequence;
    data["reference_space"]="quest_stage";
    if(data.contains("scene") && data["scene"].contains("entities")) {
        auto &entities=data["scene"]["entities"];
        auto priority=[](const json &entity){
            auto name=entity.value("labels",std::string{});
            if(name.find("FLOOR")!=std::string::npos)return 0;
            if(name.find("TABLE")!=std::string::npos||name.find("BED")!=std::string::npos||name.find("COUCH")!=std::string::npos)return 1;
            if(name.find("CEILING")!=std::string::npos)return 2;
            return 3;
        };
        std::stable_sort(entities.begin(),entities.end(),[&](const json &a,const json &b){return priority(a)<priority(b);});
        data["scene"]["truncated"]=false;
        while(data.dump().size()>24000 && !entities.empty()) {entities.erase(entities.end()-1);data["scene"]["truncated"]=true;}
    }
    if(token.empty()) return;
    if(wire<0) {
        auto now=std::chrono::steady_clock::now();if(now<retry)return;retry=now+1s;
        wire=socket(AF_INET,SOCK_STREAM,0); if(wire<0)return;
        timeval timeout{0,200000};setsockopt(wire,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
        sockaddr_in address{};address.sin_family=AF_INET;address.sin_port=htons(port);address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
        if(connect(wire,reinterpret_cast<sockaddr *>(&address),sizeof(address))!=0 ||
           !send_all(json{{"version",1},{"op","provide_quest"},{"token",token}}.dump()+"\n")) {
            close(wire);wire=-1;return;
        }
    }
    if(!send_all(data.dump()+"\n")) {close(wire);wire=-1;}
}
static std::string uuid_text(const uint8_t *bytes) {
    std::ostringstream out;for(int i=0;i<16;++i)out<<std::hex<<std::setw(2)<<std::setfill('0')<<int(bytes[i]);return out.str();
}
static json pose(const XrPosef &p,XrSpaceLocationFlags flags) {
    return {{"valid",(flags&3)==3},{"flags",flags},
            {"position",{p.position.x,p.position.y,p.position.z}},
            {"rotation",{p.orientation.x,p.orientation.y,p.orientation.z,p.orientation.w}}};
}
template<class T> static T function(XrInstance instance,const char *name) {
    T result{};xrGetInstanceProcAddr(instance,name,reinterpret_cast<PFN_xrVoidFunction *>(&result));return result;
}
struct CaptureControls {
    XrActionSet set{};
    XrAction grip{};
    std::array<XrPath,2> hands{};
    std::array<XrSpace,2> grips{};
    bool synced=false;
    std::array<XrAction,2> actions{};
    std::array<bool,2> armed{};
    CaptureControls(XrInstance instance,XrSession session) {
        XrActionSetCreateInfo info{XR_TYPE_ACTION_SET_CREATE_INFO};
        strcpy(info.actionSetName,"capture");strcpy(info.localizedActionSetName,"Guided capture");
        check(xrCreateActionSet(instance,&info,&set),"capture action set");
        const char *names[]={"retry","skip"};
        const char *bindings[]={"/user/hand/left/input/x/click","/user/hand/right/input/a/click"};
        std::array<XrActionSuggestedBinding,4> suggested{};
        for(size_t i=0;i<actions.size();++i) {
            XrActionCreateInfo action{XR_TYPE_ACTION_CREATE_INFO};action.actionType=XR_ACTION_TYPE_BOOLEAN_INPUT;
            strcpy(action.actionName,names[i]);strcpy(action.localizedActionName,names[i]);
            check(xrCreateAction(set,&action,&actions[i]),"capture action");
            XrPath path{};check(xrStringToPath(instance,bindings[i],&path),"button path");
            suggested[i]={actions[i],path};
        }
        check(xrStringToPath(instance,"/user/hand/left",&hands[0]),"left hand");
        check(xrStringToPath(instance,"/user/hand/right",&hands[1]),"right hand");
        XrActionCreateInfo action{XR_TYPE_ACTION_CREATE_INFO};action.actionType=XR_ACTION_TYPE_POSE_INPUT;
        strcpy(action.actionName,"grip_pose");strcpy(action.localizedActionName,"Controller grip pose");
        action.countSubactionPaths=hands.size();action.subactionPaths=hands.data();
        check(xrCreateAction(set,&action,&grip),"grip action");
        for(size_t i=0;i<hands.size();++i){
            XrPath path{};check(xrStringToPath(instance,i==0?"/user/hand/left/input/grip/pose":"/user/hand/right/input/grip/pose",&path),"grip path");
            suggested[i+2]={grip,path};
        }
        XrInteractionProfileSuggestedBinding bindings_info{XR_TYPE_INTERACTION_PROFILE_SUGGESTED_BINDING};
        check(xrStringToPath(instance,"/interaction_profiles/oculus/touch_controller",&bindings_info.interactionProfile),"touch profile");
        bindings_info.countSuggestedBindings=suggested.size();bindings_info.suggestedBindings=suggested.data();
        check(xrSuggestInteractionProfileBindings(instance,&bindings_info),"capture bindings");
        XrSessionActionSetsAttachInfo attach{XR_TYPE_SESSION_ACTION_SETS_ATTACH_INFO};attach.countActionSets=1;attach.actionSets=&set;
        check(xrAttachSessionActionSets(session,&attach),"attach capture controls");
        for(size_t i=0;i<grips.size();++i){
            XrActionSpaceCreateInfo space{XR_TYPE_ACTION_SPACE_CREATE_INFO};space.action=grip;space.subactionPath=hands[i];space.poseInActionSpace.orientation.w=1;
            check(xrCreateActionSpace(session,&space,&grips[i]),"grip space");
        }
    }
    std::array<bool,2> poll(XrSession session,bool focused) {
        std::array<bool,2> pressed{};
        synced=false;
        if(!focused){armed.fill(false);return pressed;}
        XrActiveActionSet active{set,XR_NULL_PATH};XrActionsSyncInfo sync{XR_TYPE_ACTIONS_SYNC_INFO};sync.countActiveActionSets=1;sync.activeActionSets=&active;
        auto result=xrSyncActions(session,&sync);
        if(result==XR_SESSION_NOT_FOCUSED){armed.fill(false);return pressed;}
        check(result,"capture input sync");
        synced=result==XR_SUCCESS;
        for(size_t i=0;i<actions.size();++i) {
            XrActionStateGetInfo get{XR_TYPE_ACTION_STATE_GET_INFO};get.action=actions[i];
            XrActionStateBoolean state{XR_TYPE_ACTION_STATE_BOOLEAN};check(xrGetActionStateBoolean(session,&get,&state),"capture button");
            if(!state.isActive){armed[i]=false;continue;}
            if(!state.currentState)armed[i]=true;
            else if(armed[i]){pressed[i]=true;armed[i]=false;}
        }
        return pressed;
    }
    json locate(XrInstance instance,XrSession session,XrSpace stage,XrTime time) {
        json devices=json::object();
        for(size_t i=0;i<grips.size();++i){
            XrSpaceLocation loc{XR_TYPE_SPACE_LOCATION};loc.pose.orientation.w=1;
            XrActionStateGetInfo get{XR_TYPE_ACTION_STATE_GET_INFO};get.action=grip;get.subactionPath=hands[i];
            XrActionStatePose state{XR_TYPE_ACTION_STATE_POSE};
            bool active=synced&&XR_SUCCEEDED(xrGetActionStatePose(session,&get,&state))&&state.isActive;
            if(!active||XR_FAILED(xrLocateSpace(grips[i],stage,time,&loc)))loc.locationFlags=0;
            auto value=pose(loc.pose,loc.locationFlags);value["pose_space"]="grip";
            XrInteractionProfileState profile{XR_TYPE_INTERACTION_PROFILE_STATE};char name[XR_MAX_PATH_LENGTH]{};uint32_t count{};
            if(XR_SUCCEEDED(xrGetCurrentInteractionProfile(session,hands[i],&profile))&&profile.interactionProfile!=XR_NULL_PATH&&
               XR_SUCCEEDED(xrPathToString(instance,profile.interactionProfile,sizeof(name),&count,name)))value["interaction_profile"]=name;
            devices[i==0?"left_hand":"right_hand"]=value;
        }
        return devices;
    }
};
struct CaptureHud {
    XrSwapchain swapchain{};
    std::vector<XrSwapchainImageOpenGLESKHR> images;
    XrCompositionLayerQuad layer{XR_TYPE_COMPOSITION_LAYER_QUAD};
    CaptureHud(XrSession session,XrSpace view) {
        XrSwapchainCreateInfo info{XR_TYPE_SWAPCHAIN_CREATE_INFO};
        info.usageFlags=XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT|XR_SWAPCHAIN_USAGE_SAMPLED_BIT;
        info.format=GL_RGBA8;info.sampleCount=1;info.width=room_capture::hud_width;info.height=room_capture::hud_height;
        info.faceCount=info.arraySize=info.mipCount=1;
        check(xrCreateSwapchain(session,&info,&swapchain),"guidance panel");
        uint32_t count{};check(xrEnumerateSwapchainImages(swapchain,0,&count,nullptr),"panel images");
        images.resize(count,{XR_TYPE_SWAPCHAIN_IMAGE_OPENGL_ES_KHR});
        check(xrEnumerateSwapchainImages(swapchain,count,&count,reinterpret_cast<XrSwapchainImageBaseHeader *>(images.data())),"panel textures");
        layer.space=view;layer.eyeVisibility=XR_EYE_VISIBILITY_BOTH;
        layer.subImage.swapchain=swapchain;layer.subImage.imageRect.extent={room_capture::hud_width,room_capture::hud_height};
        layer.pose={{0,0,0,1},{0,0.35f,-1.3f}};layer.size={1.2f,0.45f};
    }
    void update(const std::vector<std::string> &lines,float progress) {
        auto pixels=room_capture::hud_pixels(lines,progress);
        uint32_t index{};XrSwapchainImageAcquireInfo acquire{XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO};check(xrAcquireSwapchainImage(swapchain,&acquire,&index),"acquire panel");
        XrSwapchainImageWaitInfo wait{XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO};wait.timeout=XR_INFINITE_DURATION;check(xrWaitSwapchainImage(swapchain,&wait),"wait panel");
        glBindTexture(GL_TEXTURE_2D,images[index].image);
        glTexSubImage2D(GL_TEXTURE_2D,0,0,0,room_capture::hud_width,room_capture::hud_height,GL_RGBA,GL_UNSIGNED_BYTE,pixels.data());glFlush();
        XrSwapchainImageReleaseInfo release{XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};check(xrReleaseSwapchainImage(swapchain,&release),"release panel");
    }
};
static void run(std::string directory) {
    JNIEnv *env{};vm->AttachCurrentThread(&env,nullptr);
    try {
        auto initialize=function<PFN_xrInitializeLoaderKHR>(XR_NULL_HANDLE,"xrInitializeLoaderKHR");
        XrLoaderInitInfoAndroidKHR loader{XR_TYPE_LOADER_INIT_INFO_ANDROID_KHR};loader.applicationVM=vm;loader.applicationContext=activity;
        if(initialize)check(initialize(reinterpret_cast<XrLoaderInitInfoBaseHeaderKHR *>(&loader)),"initialize loader");
        uint32_t count=0;check(xrEnumerateInstanceExtensionProperties(nullptr,0,&count,nullptr),"extensions");
        std::vector<XrExtensionProperties> props(count,{XR_TYPE_EXTENSION_PROPERTIES});
        check(xrEnumerateInstanceExtensionProperties(nullptr,count,&count,props.data()),"extensions");
        std::vector<std::string> supported;for(auto &p:props)supported.emplace_back(p.extensionName);
        auto has=[&](const char *n){return std::find(supported.begin(),supported.end(),n)!=supported.end();};
        std::vector<const char *> enabled={XR_KHR_ANDROID_CREATE_INSTANCE_EXTENSION_NAME,XR_KHR_OPENGL_ES_ENABLE_EXTENSION_NAME};
        for(const char *name:{XR_FB_SPATIAL_ENTITY_EXTENSION_NAME,XR_FB_SPATIAL_ENTITY_STORAGE_EXTENSION_NAME,
            XR_FB_SPATIAL_ENTITY_QUERY_EXTENSION_NAME,XR_FB_SCENE_EXTENSION_NAME,XR_FB_PASSTHROUGH_EXTENSION_NAME})
            if(has(name))enabled.push_back(name);
        bool anchor_support=has(XR_FB_SPATIAL_ENTITY_EXTENSION_NAME)&&has(XR_FB_SPATIAL_ENTITY_STORAGE_EXTENSION_NAME)&&has(XR_FB_SPATIAL_ENTITY_QUERY_EXTENSION_NAME);
        json caps={{"extensions",supported},{"native_anchors",anchor_support},
                   {"scene",has(XR_FB_SCENE_EXTENSION_NAME)},{"headless",has("XR_MND_headless")}};
        emit({{"status","starting"},{"capabilities",caps}});
        XrInstanceCreateInfoAndroidKHR android{XR_TYPE_INSTANCE_CREATE_INFO_ANDROID_KHR};android.applicationVM=vm;android.applicationActivity=activity;
        XrInstanceCreateInfo info{XR_TYPE_INSTANCE_CREATE_INFO};info.next=&android;
        strcpy(info.applicationInfo.applicationName,"Companion Room Source");info.applicationInfo.apiVersion=XR_API_VERSION_1_0;
        info.enabledExtensionCount=enabled.size();info.enabledExtensionNames=enabled.data();
        XrInstance instance{};check(xrCreateInstance(&info,&instance),"create instance");
        XrSystemGetInfo sys{XR_TYPE_SYSTEM_GET_INFO};sys.formFactor=XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
        XrSystemId system{};check(xrGetSystem(instance,&sys,&system),"get system");
        XrSystemProperties system_properties{XR_TYPE_SYSTEM_PROPERTIES};check(xrGetSystemProperties(instance,system,&system_properties),"system properties");
        const uint32_t layer_limit=system_properties.graphicsProperties.maxLayerCount;
        if(layer_limit<3)throw std::runtime_error("Not enough compositor layers for capture guidance");
        caps["max_composition_layers"]=layer_limit;caps["guided_marker_limit"]=room_capture::marker_count;
        auto requirements=function<PFN_xrGetOpenGLESGraphicsRequirementsKHR>(instance,"xrGetOpenGLESGraphicsRequirementsKHR");
        XrGraphicsRequirementsOpenGLESKHR required{XR_TYPE_GRAPHICS_REQUIREMENTS_OPENGL_ES_KHR};check(requirements(instance,system,&required),"graphics requirements");
        EGLDisplay display=eglGetDisplay(EGL_DEFAULT_DISPLAY);EGLint major{},minor{};
        if(!eglInitialize(display,&major,&minor))throw std::runtime_error("EGL initialize failed");
        const EGLint attributes[]={EGL_RENDERABLE_TYPE,EGL_OPENGL_ES3_BIT,EGL_SURFACE_TYPE,EGL_PBUFFER_BIT,EGL_RED_SIZE,8,EGL_GREEN_SIZE,8,EGL_BLUE_SIZE,8,EGL_ALPHA_SIZE,8,EGL_NONE};
        EGLConfig config{};EGLint configs{};eglChooseConfig(display,attributes,&config,1,&configs);
        const EGLint context_attributes[]={EGL_CONTEXT_CLIENT_VERSION,3,EGL_NONE};
        EGLContext context=eglCreateContext(display,config,EGL_NO_CONTEXT,context_attributes);
        const EGLint surface_attributes[]={EGL_WIDTH,16,EGL_HEIGHT,16,EGL_NONE};
        EGLSurface surface=eglCreatePbufferSurface(display,config,surface_attributes);
        if(!eglMakeCurrent(display,surface,surface,context))throw std::runtime_error("EGL context failed");
        XrGraphicsBindingOpenGLESAndroidKHR graphics{XR_TYPE_GRAPHICS_BINDING_OPENGL_ES_ANDROID_KHR};graphics.display=display;graphics.config=config;graphics.context=context;
        XrSessionCreateInfo create{XR_TYPE_SESSION_CREATE_INFO};create.next=&graphics;create.systemId=system;
        XrSession session{};check(xrCreateSession(instance,&create,&session),"create session");
        std::vector<XrSpace> spaces;
        auto space=[&](XrReferenceSpaceType type,float x=0,float y=0,float z=0){
            XrReferenceSpaceCreateInfo create{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};create.referenceSpaceType=type;create.poseInReferenceSpace.orientation.w=1;create.poseInReferenceSpace.position={x,y,z};
            XrSpace s{};check(xrCreateReferenceSpace(session,&create,&s),"create space");spaces.push_back(s);return s;};
        XrSpace stage=space(XR_REFERENCE_SPACE_TYPE_STAGE), view=space(XR_REFERENCE_SPACE_TYPE_VIEW);
        CaptureControls controls(instance,session);
        CaptureHud hud(session,view);
        room_capture::guide guide;
        std::array<room_capture::observation,room_capture::marker_count> observations{};
        std::array<std::filesystem::path,room_capture::marker_count> marker_files;
        auto marker_directory=std::filesystem::path(directory)/"guided-v1";
        std::filesystem::create_directories(marker_directory);
        uint64_t capture_request=0;
        std::vector<std::unique_ptr<wivrn::anchor::manager>> anchors;
        auto dispatch=anchor_support?wivrn::anchor::manager::dispatch::resolve(instance,xrGetInstanceProcAddr):wivrn::anchor::manager::dispatch{};
        for(int i=0;i<room_capture::marker_count;++i) {
            marker_files[i]=marker_directory/("marker-"+std::to_string(i)+".uuid");
            observations[i].saved_file=std::filesystem::exists(marker_files[i]);
            // Calibration relocalizes existing markers, but never creates/saves replacements.
            anchors.push_back(std::make_unique<wivrn::anchor::manager>(session,stage,view,marker_files[i],dispatch,calibration_mode));
        }
        auto query=function<PFN_xrQuerySpacesFB>(instance,"xrQuerySpacesFB");
        auto retrieve=function<PFN_xrRetrieveSpaceQueryResultsFB>(instance,"xrRetrieveSpaceQueryResultsFB");
        auto labels=function<PFN_xrGetSpaceSemanticLabelsFB>(instance,"xrGetSpaceSemanticLabelsFB");
        auto boundary=function<PFN_xrGetSpaceBoundary2DFB>(instance,"xrGetSpaceBoundary2DFB");
        auto box=function<PFN_xrGetSpaceBoundingBox3DFB>(instance,"xrGetSpaceBoundingBox3DFB");
        auto enable_component=function<PFN_xrSetSpaceComponentStatusFB>(instance,"xrSetSpaceComponentStatusFB");
        XrAsyncRequestIdFB scene_request=0;bool scene_started=false;int scene_result=0;bool scene_complete=false;
        std::vector<XrSpaceQueryResultFB> scene_spaces;json scene_geometry=json::array();
        XrPassthroughFB passthrough{};XrPassthroughLayerFB passthrough_layer{};
        auto create_passthrough=function<PFN_xrCreatePassthroughFB>(instance,"xrCreatePassthroughFB");
        auto create_layer=function<PFN_xrCreatePassthroughLayerFB>(instance,"xrCreatePassthroughLayerFB");
        if(create_passthrough&&create_layer) {
            XrPassthroughCreateInfoFB p{XR_TYPE_PASSTHROUGH_CREATE_INFO_FB};p.flags=XR_PASSTHROUGH_IS_RUNNING_AT_CREATION_BIT_FB;
            check(create_passthrough(session,&p,&passthrough),"passthrough");
            XrPassthroughLayerCreateInfoFB l{XR_TYPE_PASSTHROUGH_LAYER_CREATE_INFO_FB};l.passthrough=passthrough;l.flags=XR_PASSTHROUGH_IS_RUNNING_AT_CREATION_BIT_FB;l.purpose=XR_PASSTHROUGH_LAYER_PURPOSE_RECONSTRUCTION_FB;
            check(create_layer(session,&l,&passthrough_layer),"passthrough layer");
        }
        XrSwapchainCreateInfo swap{XR_TYPE_SWAPCHAIN_CREATE_INFO};swap.createFlags=XR_SWAPCHAIN_CREATE_STATIC_IMAGE_BIT;swap.usageFlags=XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT|XR_SWAPCHAIN_USAGE_SAMPLED_BIT;
        swap.format=GL_RGBA8;swap.sampleCount=1;swap.width=128;swap.height=128;swap.faceCount=1;swap.arraySize=1;swap.mipCount=1;
        XrSwapchain texture{};check(xrCreateSwapchain(session,&swap,&texture),"cross swapchain");
        uint32_t images=0;check(xrEnumerateSwapchainImages(texture,0,&images,nullptr),"images");
        std::vector<XrSwapchainImageOpenGLESKHR> textures(images,{XR_TYPE_SWAPCHAIN_IMAGE_OPENGL_ES_KHR});
        check(xrEnumerateSwapchainImages(texture,images,&images,reinterpret_cast<XrSwapchainImageBaseHeader *>(textures.data())),"images");
        uint32_t index{};XrSwapchainImageAcquireInfo acquire{XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO};check(xrAcquireSwapchainImage(texture,&acquire,&index),"acquire texture");
        XrSwapchainImageWaitInfo wait{XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO};wait.timeout=XR_INFINITE_DURATION;check(xrWaitSwapchainImage(texture,&wait),"wait texture");
        GLuint framebuffer{};glGenFramebuffers(1,&framebuffer);glBindFramebuffer(GL_FRAMEBUFFER,framebuffer);glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,textures[index].image,0);
        glViewport(0,0,128,128);glClearColor(0,0,0,0);glClear(GL_COLOR_BUFFER_BIT);glEnable(GL_SCISSOR_TEST);glClearColor(0.1,0.9,1,1);
        glScissor(57,12,14,104);glClear(GL_COLOR_BUFFER_BIT);glScissor(12,57,104,14);glClear(GL_COLOR_BUFFER_BIT);glDisable(GL_SCISSOR_TEST);glBindFramebuffer(GL_FRAMEBUFFER,0);glFlush();
        XrSwapchainImageReleaseInfo release{XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};check(xrReleaseSwapchainImage(texture,&release),"release texture");
        bool running=false;XrSessionState state=XR_SESSION_STATE_UNKNOWN;uint64_t generation=0, frames=0;
        auto last_emit=std::chrono::steady_clock::now();
        auto last_hud=std::chrono::steady_clock::time_point{};
        XrTime reference_change_at=0;
        while(!stopping) {
            for(;;) {
                XrEventDataBuffer event{XR_TYPE_EVENT_DATA_BUFFER};auto result=xrPollEvent(instance,&event);if(result==XR_EVENT_UNAVAILABLE)break;check(result,"poll events");
                for(auto &anchor:anchors)anchor->event(event);
                if(event.type==XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED) {
                    state=reinterpret_cast<XrEventDataSessionStateChanged &>(event).state;
                    if(state!=XR_SESSION_STATE_FOCUSED)guide.suspend();
                    emit({{"status","session_event"},{"xr_state",state},{"resumed",resumed.load()},{"capabilities",caps}});
                    if(state==XR_SESSION_STATE_READY){XrSessionBeginInfo begin{XR_TYPE_SESSION_BEGIN_INFO};begin.primaryViewConfigurationType=XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;check(xrBeginSession(session,&begin),"begin");running=true;}
                    if(state==XR_SESSION_STATE_STOPPING){check(xrEndSession(session),"end");running=false;}
                    if(state==XR_SESSION_STATE_EXITING||state==XR_SESSION_STATE_LOSS_PENDING)stopping=true;
                }
                if(event.type==XR_TYPE_EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING){
                    auto &change=reinterpret_cast<XrEventDataReferenceSpaceChangePending &>(event);
                    if(change.referenceSpaceType==XR_REFERENCE_SPACE_TYPE_STAGE||change.referenceSpaceType==XR_REFERENCE_SPACE_TYPE_LOCAL){
                        ++generation;reference_change_at=std::max(reference_change_at,change.changeTime);guide.reference_changed();
                    }
                }
                if(event.type==XR_TYPE_EVENT_DATA_SPACE_QUERY_COMPLETE_FB){auto &e=reinterpret_cast<XrEventDataSpaceQueryCompleteFB &>(event);if(e.requestId==scene_request){scene_result=e.result;scene_complete=true;}}
                if(event.type==XR_TYPE_EVENT_DATA_SPACE_QUERY_RESULTS_AVAILABLE_FB) {
                    auto &e=reinterpret_cast<XrEventDataSpaceQueryResultsAvailableFB &>(event);
                    if(e.requestId==scene_request&&retrieve) {
                        XrSpaceQueryResultsFB results{XR_TYPE_SPACE_QUERY_RESULTS_FB};check(retrieve(session,scene_request,&results),"scene count");
                        if(results.resultCountOutput>64)throw std::runtime_error("Scene result limit exceeded");
                        std::vector<XrSpaceQueryResultFB> entries(results.resultCountOutput);results.resultCapacityInput=entries.size();results.results=entries.data();
                        check(retrieve(session,scene_request,&results),"scene results");
                        for(auto &entry:entries) {
                            if(scene_spaces.size()>=64){xrDestroySpace(entry.space);continue;}
                            json geom={{"uuid",uuid_text(entry.uuid.data)}};
                            char text[512]{};XrSemanticLabelsFB label{XR_TYPE_SEMANTIC_LABELS_FB};label.bufferCapacityInput=sizeof(text);label.buffer=text;
                            if(labels&&XR_SUCCEEDED(labels(session,entry.space,&label)))geom["labels"]=text;
                            if(boundary){XrBoundary2DFB b{XR_TYPE_BOUNDARY_2D_FB};if(XR_SUCCEEDED(boundary(session,entry.space,&b))&&b.vertexCountOutput<=256){std::vector<XrVector2f> v(b.vertexCountOutput);b.vertexCapacityInput=v.size();b.vertices=v.data();if(XR_SUCCEEDED(boundary(session,entry.space,&b))){geom["boundary_2d"]=json::array();for(auto &p:v)geom["boundary_2d"].push_back({p.x,p.y});}}}
                            if(box){XrRect3DfFB b{};if(XR_SUCCEEDED(box(session,entry.space,&b)))geom["box"]={{"offset",{b.offset.x,b.offset.y,b.offset.z}},{"extent",{b.extent.width,b.extent.height,b.extent.depth}}};}
                            if(enable_component){XrSpaceComponentStatusSetInfoFB set{XR_TYPE_SPACE_COMPONENT_STATUS_SET_INFO_FB};set.componentType=XR_SPACE_COMPONENT_TYPE_LOCATABLE_FB;set.enabled=XR_TRUE;set.timeout=0;XrAsyncRequestIdFB request{};enable_component(entry.space,&set,&request);}
                            scene_geometry.push_back(geom);scene_spaces.push_back(entry);
                        }
                    }
                }
            }
            if(!running){std::this_thread::sleep_for(20ms);continue;}
            XrFrameWaitInfo frame_wait{XR_TYPE_FRAME_WAIT_INFO};XrFrameState frame{XR_TYPE_FRAME_STATE};check(xrWaitFrame(session,&frame_wait,&frame),"wait frame");
            XrFrameBeginInfo begin{XR_TYPE_FRAME_BEGIN_INFO};check(xrBeginFrame(session,&begin),"begin frame");++frames;
            XrSpaceLocation head{XR_TYPE_SPACE_LOCATION};xrLocateSpace(view,stage,frame.predictedDisplayTime,&head);
            if(!scene_started&&frames>60&&query&&labels){
                scene_started=true;XrSpaceStorageLocationFilterInfoFB storage{XR_TYPE_SPACE_STORAGE_LOCATION_FILTER_INFO_FB};storage.location=XR_SPACE_STORAGE_LOCATION_LOCAL_FB;XrSpaceComponentFilterInfoFB filter{XR_TYPE_SPACE_COMPONENT_FILTER_INFO_FB};filter.next=&storage;filter.componentType=XR_SPACE_COMPONENT_TYPE_SEMANTIC_LABELS_FB;
                XrSpaceQueryInfoFB q{XR_TYPE_SPACE_QUERY_INFO_FB};q.queryAction=XR_SPACE_QUERY_ACTION_LOAD_FB;q.maxResultCount=64;q.timeout=0;q.filter=reinterpret_cast<XrSpaceFilterInfoBaseHeaderFB *>(&filter);
                scene_result=query(session,reinterpret_cast<XrSpaceQueryInfoBaseHeaderFB *>(&q),&scene_request);
                if(XR_FAILED(scene_result))scene_complete=true;
            }
            const bool focused=resumed&&state==XR_SESSION_STATE_FOCUSED&&frame.predictedDisplayTime>=reference_change_at;
            const auto buttons=controls.poll(session,focused);
            for(size_t i=0;i<anchors.size();++i){
                observations[i].anchor=anchors[i]->tick(frame.predictedDisplayTime);
                observations[i].busy=anchors[i]->busy();
                if(observations[i].anchor.persisted)observations[i].saved_file=std::filesystem::exists(marker_files[i]);
            }
            if(auto action=guide.tick(frame.predictedDisplayTime,head,!calibration_mode&&focused&&passthrough_layer,observations,buttons[0],buttons[1])){
                auto &anchor=*anchors[action->slot];bool ok=true;
                switch(action->op){
                case room_capture::operation::create:ok=anchor.create_at(++capture_request,action->pose,frame.predictedDisplayTime);break;
                case room_capture::operation::load:anchor.execute({++capture_request,wivrn::anchor::operation::load},frame.predictedDisplayTime);break;
                case room_capture::operation::retry:ok=anchor.retry(++capture_request,frame.predictedDisplayTime);break;
                }
                if(!ok)guide.rejected();
            }
            if(!passthrough_layer){guide.instruction="PASSTHROUGH NOT AVAILABLE";guide.detail="GUIDED CAPTURE IS PAUSED";}
            if(std::chrono::steady_clock::now()-last_hud>=200ms){
                last_hud=std::chrono::steady_clock::now();
                if(calibration_mode)hud.update({"CONTROLLER CALIBRATION","LEAVE BOTH CONTROLLERS STILL","ON STABLE SURFACES - KEEP HEADSET AWAKE",
                    "PC WILL SWITCH APPS AUTOMATICALLY","DO NOT RECENTER OR MOVE CONTROLLERS","MARKER CAPTURE IS PAUSED"},0);
                else hud.update({"ROOM CAPTURE - FOUR STAGES",guide.instruction,guide.detail,
                    "SAVED "+std::to_string(guide.accepted_count())+"/12  VERIFIED "+std::to_string(guide.verified_count())+"/12",
                    "X RETRY OR RECHECK - A SKIP",
                    "KEEP THE AREA CLEAR - TURN IN PLACE"},guide.progress);
            }
            json markers=json::object();std::array<XrCompositionLayerQuad,room_capture::marker_count> quads{};
            std::vector<const XrCompositionLayerBaseHeader *> layers;
            XrCompositionLayerPassthroughFB pass{XR_TYPE_COMPOSITION_LAYER_PASSTHROUGH_FB};pass.layerHandle=passthrough_layer;
            if(passthrough_layer&&layer_limit>2)layers.push_back(reinterpret_cast<XrCompositionLayerBaseHeader *>(&pass));
            for(size_t n=0;n<anchors.size();++n){
                // Keep the current marker visible first if a runtime has a small layer budget.
                size_t i=(n+std::min(guide.slot,room_capture::marker_count-1))%anchors.size();
                const auto &s=observations[i].anchor;
                if(std::any_of(s.uuid.begin(),s.uuid.end(),[](auto b){return b!=0;})){
                    json marker=pose(s.pose,s.flags);marker["persisted"]=s.persisted;marker["state"]=int(s.status);marker["result"]=s.result;marker["slot"]=i;
                    marker["verified"]=guide.verified[i];marker["skipped"]=guide.skipped[i];markers[uuid_text(s.uuid.data())]=marker;
                }
                if((s.flags&3)==3&&resumed&&layers.size()+1<layer_limit){auto &q=quads[i];q.type=XR_TYPE_COMPOSITION_LAYER_QUAD;q.layerFlags=XR_COMPOSITION_LAYER_BLEND_TEXTURE_SOURCE_ALPHA_BIT;q.space=stage;q.eyeVisibility=XR_EYE_VISIBILITY_BOTH;q.subImage.swapchain=texture;q.subImage.imageRect.extent={128,128};q.pose=s.pose;q.size={0.15f,0.15f};layers.push_back(reinterpret_cast<XrCompositionLayerBaseHeader *>(&q));}
            }
            layers.push_back(reinterpret_cast<XrCompositionLayerBaseHeader *>(&hud.layer));
            if(std::chrono::steady_clock::now()-last_emit>=100ms){
                last_emit=std::chrono::steady_clock::now();XrExtent2Df extent{};auto result=xrGetReferenceSpaceBoundsRect(session,XR_REFERENCE_SPACE_TYPE_STAGE,&extent);
                json entities=scene_geometry;for(size_t i=0;i<scene_spaces.size();++i){XrSpaceLocation loc{XR_TYPE_SPACE_LOCATION};xrLocateSpace(scene_spaces[i].space,stage,frame.predictedDisplayTime,&loc);entities[i]["pose"]=pose(loc.pose,loc.locationFlags);}
                json devices=controls.locate(instance,session,stage,frame.predictedDisplayTime);devices["head"]=pose(head.pose,head.locationFlags);
                emit({{"status",resumed.load()?"tracking":"paused"},{"xr_state",state},{"resumed",resumed.load()},{"generation",generation},
                    {"sample_time_ns",std::to_string(frame.predictedDisplayTime)},{"capabilities",caps},
                    {"devices",devices},{"anchors",markers},
                    {"capture",{{"version",1},{"phase",guide.phase_name()},{"stage",std::min(guide.state==room_capture::guide::phase::scan?guide.scan_stage+1:guide.slot/3+1,4)},
                        {"slot",guide.slot},{"target_count",room_capture::marker_count},{"accepted_count",guide.accepted_count()},
                        {"verified_count",guide.verified_count()},{"complete",guide.complete()},{"coverage",guide.coverage},
                        {"skipped",guide.skipped},{"instruction",guide.instruction},{"detail",guide.detail}}},
                    {"boundary",{{"kind","stage_rectangle"},{"available",result==XR_SUCCESS},{"width",extent.width},{"depth",extent.height},{"result",result},{"guardian_polygon",false}}},
                    {"scene",{{"query_started",scene_started},{"query_result",scene_result},{"complete",scene_complete},{"entity_limit",64},{"entities",entities}}}});
            }
            XrFrameEndInfo end{XR_TYPE_FRAME_END_INFO};end.displayTime=frame.predictedDisplayTime;end.environmentBlendMode=XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
            end.layerCount=frame.shouldRender?layers.size():0;end.layers=layers.data();check(xrEndFrame(session,&end),"end frame");
        }
        anchors.clear();for(auto &s:scene_spaces)xrDestroySpace(s.space);for(auto s:spaces)xrDestroySpace(s);for(auto s:controls.grips)xrDestroySpace(s);
        xrDestroySwapchain(hud.swapchain);xrDestroySwapchain(texture);xrDestroySession(session);xrDestroyActionSet(controls.set);xrDestroyInstance(instance);
        eglMakeCurrent(display,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT);eglDestroySurface(display,surface);eglDestroyContext(display,context);eglTerminate(display);
        emit({{"status","stopped"}});
    } catch(const std::exception &e){
        __android_log_print(ANDROID_LOG_ERROR,"RoomSource","%s",e.what());emit({{"status","error"},{"error",e.what()}});
    }
    vm->DetachCurrentThread();
}
extern "C" JNIEXPORT void JNICALL Java_org_vrmcompanion_roomsource_MainActivity_nativeStart(JNIEnv *env,jobject self,jint target,jstring secret,jstring path,jboolean calibration) {
    if(launched.exchange(true))return;
    env->GetJavaVM(&vm);activity=env->NewGlobalRef(self);port=target;
    calibration_mode=calibration;
    const char *s=secret?env->GetStringUTFChars(secret,nullptr):nullptr;token=s?s:"";if(s)env->ReleaseStringUTFChars(secret,s);
    const char *p=env->GetStringUTFChars(path,nullptr);std::string directory=p;env->ReleaseStringUTFChars(path,p);
    stream_id=std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
    std::thread(run,directory).detach();
}
extern "C" JNIEXPORT void JNICALL Java_org_vrmcompanion_roomsource_MainActivity_nativeLifecycle(JNIEnv *,jobject,jboolean active,jboolean stop) {
    resumed=active;if(stop)stopping=true;
    emit({{"status",active?"resuming":"paused"},{"resumed",bool(active)}});
}
