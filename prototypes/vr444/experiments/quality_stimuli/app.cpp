// SPDX-License-Identifier: MIT
// Bounded diagnostic OpenXR application. No codec/client-derived reference.
#define XR_USE_GRAPHICS_API_VULKAN
#include <vulkan/vulkan.h>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>
#include <openssl/sha.h>
#include <nlohmann/json.hpp>
#include "pattern.hpp"
#include <algorithm>
#include <array>
#include <bit>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <thread>
#include <set>
#include <unistd.h>

using nlohmann::json;
namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
static void require(bool value, const char* why) { if (!value) throw std::runtime_error(why); }
static void xrcheck(XrResult v, const char* name) { if (XR_FAILED(v)) throw std::runtime_error(std::string(name)+": "+std::to_string(v)); }
static void vkcheck(VkResult v, const char* name) { if (v != VK_SUCCESS) throw std::runtime_error(std::string(name)+": "+std::to_string(v)); }
static void drain_or_terminate(VkDevice device) noexcept {
    if (!device) return;
    const auto result=vkDeviceWaitIdle(device);
    // Unknown completion cannot authorize destruction of submitted resources.
    // DEVICE_LOST is terminal and Vulkan permits object destruction afterward.
    if (result!=VK_SUCCESS&&result!=VK_ERROR_DEVICE_LOST) std::terminate();
}
#define XC(expr) xrcheck((expr), #expr)
#define VC(expr) vkcheck((expr), #expr)
static std::string digest(const std::vector<uint8_t>& bytes) {
    std::array<unsigned char,SHA256_DIGEST_LENGTH> out{};
    SHA256(bytes.data(), bytes.size(), out.data());
    std::ostringstream s; s << std::hex << std::setfill('0');
    for (auto c:out) s << std::setw(2) << unsigned(c);
    return s.str();
}
static void bytes(const fs::path& path, const std::vector<uint8_t>& value) {
    require(!fs::exists(path), "refusing to replace evidence");
    std::ofstream f(path,std::ios::binary); f.exceptions(std::ios::badbit|std::ios::failbit);
    f.write(reinterpret_cast<const char*>(value.data()),value.size()); f.close();
}
struct Options {
    std::string pattern; fs::path output; uint32_t width=0,height=0,frames=900,seconds=30;
    bool emit=false;
};
static uint32_t number(const char* text) {
    std::string s=text; require(!s.empty() && s.size()<10 && s.find_first_not_of("0123456789")==std::string::npos,"invalid unsigned integer");
    const auto v=std::stoull(s); require(v<=UINT32_MAX,"integer overflow"); return uint32_t(v);
}
static Options parse(int argc,char** argv) {
    Options o; std::set<std::string> seen;
    for (int i=1;i<argc;++i) {
        const std::string k=argv[i]; require(seen.insert(k).second,"duplicate argument");
        if(k=="--emit-fixtures") {o.emit=true;continue;}
        require(i+1<argc,"missing option value"); const char* v=argv[++i];
        if(k=="--pattern") o.pattern=v;
        else if(k=="--output") o.output=v;
        else if(k=="--width") o.width=number(v);
        else if(k=="--height") o.height=number(v);
        else if(k=="--frames") o.frames=number(v);
        else if(k=="--seconds") o.seconds=number(v);
        else throw std::runtime_error("unknown argument");
    }
    require(quality_stimuli::known(o.pattern),"choose chroma-grid, rgb-ramp or fine-detail");
    require(o.output.is_absolute() && !fs::exists(o.output),"output must be a new absolute directory");
    require(bool(o.width)==bool(o.height),"both dimensions required together");
    if(o.width) quality_stimuli::shape(o.width,o.height);
    require(o.frames>=1 && o.frames<=10800 && o.seconds>=1 && o.seconds<=120,"frame/time bound exceeded");
    require(!o.emit || o.width,"offline fixtures require explicit dimensions");
    return o;
}
static json pose(const XrPosef& p) {
    return {{"orientation",{p.orientation.x,p.orientation.y,p.orientation.z,p.orientation.w}},
            {"position",{p.position.x,p.position.y,p.position.z}},
            {"orientation_bits",{std::bit_cast<uint32_t>(p.orientation.x),std::bit_cast<uint32_t>(p.orientation.y),std::bit_cast<uint32_t>(p.orientation.z),std::bit_cast<uint32_t>(p.orientation.w)}},
            {"position_bits",{std::bit_cast<uint32_t>(p.position.x),std::bit_cast<uint32_t>(p.position.y),std::bit_cast<uint32_t>(p.position.z)}}};
}
static json fov(const XrFovf& f) {
    return {{"radians",{f.angleLeft,f.angleRight,f.angleUp,f.angleDown}},
            {"bits",{std::bit_cast<uint32_t>(f.angleLeft),std::bit_cast<uint32_t>(f.angleRight),std::bit_cast<uint32_t>(f.angleUp),std::bit_cast<uint32_t>(f.angleDown)}}};
}
struct Eye {
    XrSwapchain swap=XR_NULL_HANDLE; uint32_t width=0,height=0;
    std::vector<XrSwapchainImageVulkanKHR> images;
    std::vector<bool> captured;
    std::vector<uint8_t> source; std::string sha;
    VkBuffer buffer=VK_NULL_HANDLE; VkDeviceMemory memory=VK_NULL_HANDLE;
    uint8_t* mapped=nullptr; size_t size=0;
};
struct App {
    Options opt; std::ofstream log;
    XrInstance xr=XR_NULL_HANDLE; XrSystemId system=XR_NULL_SYSTEM_ID;
    XrSession session=XR_NULL_HANDLE; XrSpace space=XR_NULL_HANDLE;
    VkInstance instance=VK_NULL_HANDLE; VkPhysicalDevice physical=VK_NULL_HANDLE;
    VkDevice device=VK_NULL_HANDLE; VkQueue queue=VK_NULL_HANDLE; uint32_t family=0;
    VkCommandPool pool=VK_NULL_HANDLE; VkCommandBuffer command=VK_NULL_HANDLE; VkFence fence=VK_NULL_HANDLE;
    std::array<Eye,2> eyes{}; bool running=false,terminal=false;
    uint32_t frames=0,submitted=0; XrSessionState state=XR_SESSION_STATE_UNKNOWN;
    Clock::time_point deadline;
    explicit App(Options options):opt(std::move(options)) {
        require(fs::create_directory(opt.output),"cannot create output directory");
        log.open(opt.output/"app.jsonl"); log.exceptions(std::ios::badbit|std::ios::failbit);
    }
    ~App() {
        // Runtime calls and GPU work are single-threaded, using the binding's
        // exact VkQueue. External timeout is still required for stuck drivers.
        drain_or_terminate(device);
        for(auto& e:eyes) {
            if(e.swap) xrDestroySwapchain(e.swap);
            if(e.mapped) vkUnmapMemory(device,e.memory);
            if(e.buffer) vkDestroyBuffer(device,e.buffer,nullptr);
            if(e.memory) vkFreeMemory(device,e.memory,nullptr);
        }
        if(space) xrDestroySpace(space);
        if(session) xrDestroySession(session);
        if(fence) vkDestroyFence(device,fence,nullptr);
        if(pool) vkDestroyCommandPool(device,pool,nullptr);
        if(device) vkDestroyDevice(device,nullptr);
        if(instance) vkDestroyInstance(instance,nullptr);
        if(xr) xrDestroyInstance(xr);
    }
    void record(json j) {log << j.dump() << '\n'; log.flush();}
    template<class T> T fn(const char* name) { PFN_xrVoidFunction p=nullptr; XC(xrGetInstanceProcAddr(xr,name,&p));require(p,"missing XR procedure");return reinterpret_cast<T>(p); }
    void init() {
        XrInstanceCreateInfo ic{XR_TYPE_INSTANCE_CREATE_INFO};
        std::strcpy(ic.applicationInfo.applicationName,"sub15-quality-stimuli");
        ic.applicationInfo.applicationVersion=1; ic.applicationInfo.apiVersion=XR_MAKE_VERSION(1,0,0);
        const char* extension=XR_KHR_VULKAN_ENABLE2_EXTENSION_NAME;
        ic.enabledExtensionCount=1;ic.enabledExtensionNames=&extension; XC(xrCreateInstance(&ic,&xr));
        XrSystemGetInfo si{XR_TYPE_SYSTEM_GET_INFO};si.formFactor=XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;XC(xrGetSystem(xr,&si,&system));
        XrGraphicsRequirementsVulkan2KHR req{XR_TYPE_GRAPHICS_REQUIREMENTS_VULKAN2_KHR};
        XC(fn<PFN_xrGetVulkanGraphicsRequirements2KHR>("xrGetVulkanGraphicsRequirements2KHR")(xr,system,&req));
        require(req.minApiVersionSupported<=XR_MAKE_VERSION(1,3,0),"unsupported minimum Vulkan version");
        const auto major=uint32_t(XR_VERSION_MAJOR(req.minApiVersionSupported));
        const auto minor=uint32_t(XR_VERSION_MINOR(req.minApiVersionSupported));
        VkApplicationInfo ai{VK_STRUCTURE_TYPE_APPLICATION_INFO};ai.pApplicationName="sub15-quality-stimuli";ai.apiVersion=VK_MAKE_API_VERSION(0,major,minor,0);
        VkInstanceCreateInfo vi{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};vi.pApplicationInfo=&ai;
        XrVulkanInstanceCreateInfoKHR xi{XR_TYPE_VULKAN_INSTANCE_CREATE_INFO_KHR};xi.systemId=system;xi.pfnGetInstanceProcAddr=vkGetInstanceProcAddr;xi.vulkanCreateInfo=&vi;
        VkResult vr=VK_ERROR_UNKNOWN;
        XC(fn<PFN_xrCreateVulkanInstanceKHR>("xrCreateVulkanInstanceKHR")(xr,&xi,&instance,&vr));VC(vr);
        XrVulkanGraphicsDeviceGetInfoKHR dg{XR_TYPE_VULKAN_GRAPHICS_DEVICE_GET_INFO_KHR};dg.systemId=system;dg.vulkanInstance=instance;
        XC(fn<PFN_xrGetVulkanGraphicsDevice2KHR>("xrGetVulkanGraphicsDevice2KHR")(xr,&dg,&physical));
        uint32_t n=0;vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,nullptr);require(n&&n<=256,"invalid queue count");
        std::vector<VkQueueFamilyProperties> qs(n);vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,qs.data());
        family=n;for(uint32_t i=0;i<n;++i)if(qs[i].queueCount&&(qs[i].queueFlags&VK_QUEUE_GRAPHICS_BIT)){family=i;break;}
        require(family<n,"no graphics queue");float priority=1;
        VkDeviceQueueCreateInfo qi{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};qi.queueFamilyIndex=family;qi.queueCount=1;qi.pQueuePriorities=&priority;
        VkDeviceCreateInfo dc{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};dc.queueCreateInfoCount=1;dc.pQueueCreateInfos=&qi;
        XrVulkanDeviceCreateInfoKHR xd{XR_TYPE_VULKAN_DEVICE_CREATE_INFO_KHR};xd.systemId=system;xd.pfnGetInstanceProcAddr=vkGetInstanceProcAddr;xd.vulkanPhysicalDevice=physical;xd.vulkanCreateInfo=&dc;
        XC(fn<PFN_xrCreateVulkanDeviceKHR>("xrCreateVulkanDeviceKHR")(xr,&xd,&device,&vr));VC(vr);vkGetDeviceQueue(device,family,0,&queue);
        XrGraphicsBindingVulkan2KHR binding{XR_TYPE_GRAPHICS_BINDING_VULKAN2_KHR};binding.instance=instance;binding.physicalDevice=physical;binding.device=device;binding.queueFamilyIndex=family;binding.queueIndex=0;
        XrSessionCreateInfo sc{XR_TYPE_SESSION_CREATE_INFO};sc.next=&binding;sc.systemId=system;XC(xrCreateSession(xr,&sc,&session));
        uint32_t count=0;XC(xrEnumerateEnvironmentBlendModes(xr,system,XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO,0,&count,nullptr));require(count&&count<=16,"bad blend mode count");
        std::vector<XrEnvironmentBlendMode> blend(count);XC(xrEnumerateEnvironmentBlendModes(xr,system,XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO,count,&count,blend.data()));
        require(std::find(blend.begin(),blend.end(),XR_ENVIRONMENT_BLEND_MODE_OPAQUE)!=blend.end(),"opaque blend required");
        XrReferenceSpaceCreateInfo rs{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};rs.referenceSpaceType=XR_REFERENCE_SPACE_TYPE_VIEW;rs.poseInReferenceSpace.orientation.w=1;XC(xrCreateReferenceSpace(session,&rs,&space));
        XC(xrEnumerateSwapchainFormats(session,0,&count,nullptr));require(count&&count<=256,"bad format count");
        std::vector<int64_t> formats(count);XC(xrEnumerateSwapchainFormats(session,count,&count,formats.data()));
        require(std::find(formats.begin(),formats.end(),VK_FORMAT_R8G8B8A8_SRGB)!=formats.end(),"RGBA8 SRGB swapchain required; no silent format fallback");
        XC(xrEnumerateViewConfigurationViews(xr,system,XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO,0,&count,nullptr));require(count==2,"exactly two views required");
        std::array<XrViewConfigurationView,2> views{{{XR_TYPE_VIEW_CONFIGURATION_VIEW},{XR_TYPE_VIEW_CONFIGURATION_VIEW}}};
        XC(xrEnumerateViewConfigurationViews(xr,system,XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO,2,&count,views.data()));require(count==2,"view count changed");
        VkCommandPoolCreateInfo pc{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};pc.queueFamilyIndex=family;pc.flags=VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;VC(vkCreateCommandPool(device,&pc,nullptr,&pool));
        VkCommandBufferAllocateInfo ca{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};ca.commandPool=pool;ca.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY;ca.commandBufferCount=1;VC(vkAllocateCommandBuffers(device,&ca,&command));
        VkFenceCreateInfo fc{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};VC(vkCreateFence(device,&fc,nullptr,&fence));
        VkPhysicalDeviceProperties props{};vkGetPhysicalDeviceProperties(physical,&props);
        record({{"type","header"},{"schema",1},{"pattern",opt.pattern},{"source_generation","direct_encoded_srgb_byte_upload"},{"source_stage","application_swapchain_before_compositor"},{"quality_only",true},{"latency_comparable",false},{"width_requested",opt.width},{"height_requested",opt.height},{"frame_limit",opt.frames},{"seconds_limit",opt.seconds},{"space","VIEW"},{"format",VK_FORMAT_R8G8B8A8_SRGB},{"format_name","R8G8B8A8_SRGB"},{"opaque",true},{"top_left_origin",true},{"device",props.deviceName},{"vendor_id",props.vendorID},{"device_id",props.deviceID},{"queue_family",family},{"graphics_api_version",ai.apiVersion}});
        for(unsigned i=0;i<2;++i) init_eye(i,views[i]);
    }
    void init_eye(unsigned index,const XrViewConfigurationView& view) {
        auto& e=eyes[index];e.width=opt.width?opt.width:view.recommendedImageRectWidth;e.height=opt.height?opt.height:view.recommendedImageRectHeight;
        quality_stimuli::shape(e.width,e.height);require(e.width<=view.maxImageRectWidth&&e.height<=view.maxImageRectHeight,"requested shape exceeds runtime maximum");
        XrSwapchainCreateInfo sw{XR_TYPE_SWAPCHAIN_CREATE_INFO};sw.usageFlags=XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT|XR_SWAPCHAIN_USAGE_TRANSFER_DST_BIT|XR_SWAPCHAIN_USAGE_TRANSFER_SRC_BIT;
        sw.format=VK_FORMAT_R8G8B8A8_SRGB;sw.sampleCount=1;sw.width=e.width;sw.height=e.height;sw.faceCount=1;sw.arraySize=1;sw.mipCount=1;XC(xrCreateSwapchain(session,&sw,&e.swap));
        uint32_t n=0;XC(xrEnumerateSwapchainImages(e.swap,0,&n,nullptr));require(n&&n<=16,"swapchain image bound exceeded");
        e.images.resize(n,{XR_TYPE_SWAPCHAIN_IMAGE_VULKAN_KHR});e.captured.resize(n,false);
        XC(xrEnumerateSwapchainImages(e.swap,n,&n,reinterpret_cast<XrSwapchainImageBaseHeader*>(e.images.data())));require(n==e.images.size(),"swapchain count changed");
        e.source=quality_stimuli::pattern(opt.pattern,e.width,e.height,index);e.sha=digest(e.source);e.size=e.source.size();
        bytes(opt.output/("eye"+std::to_string(index)+".source.rgba"),e.source);
        auto [type,flags]=init_buffer(e);
        record({{"type","eye"},{"eye",index},{"width",e.width},{"height",e.height},{"recommended_width",view.recommendedImageRectWidth},{"recommended_height",view.recommendedImageRectHeight},{"source_file","eye"+std::to_string(index)+".source.rgba"},{"source_sha256",e.sha},{"source_bytes",e.size},{"swapchain_images",n},{"memory_type",type},{"memory_flags",flags}});
    }
    std::pair<uint32_t,VkMemoryPropertyFlags> init_buffer(Eye& e) {
        require(e.size==size_t(e.width)*e.height*4&&e.source.size()==e.size,"buffer source shape mismatch");
        VkBufferCreateInfo bc{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};bc.size=2*e.size;bc.usage=VK_BUFFER_USAGE_TRANSFER_SRC_BIT|VK_BUFFER_USAGE_TRANSFER_DST_BIT;bc.sharingMode=VK_SHARING_MODE_EXCLUSIVE;VC(vkCreateBuffer(device,&bc,nullptr,&e.buffer));
        VkMemoryRequirements mr{};vkGetBufferMemoryRequirements(device,e.buffer,&mr);
        VkPhysicalDeviceMemoryProperties mp{};vkGetPhysicalDeviceMemoryProperties(physical,&mp);uint32_t type=mp.memoryTypeCount;
        const auto needed=VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
        for(uint32_t i=0;i<mp.memoryTypeCount;++i)if((mr.memoryTypeBits&(1u<<i))&&(mp.memoryTypes[i].propertyFlags&needed)==needed){type=i;break;}
        require(type<mp.memoryTypeCount,"coherent host-visible memory required");
        VkMemoryAllocateInfo ma{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};ma.allocationSize=mr.size;ma.memoryTypeIndex=type;VC(vkAllocateMemory(device,&ma,nullptr,&e.memory));VC(vkBindBufferMemory(device,e.buffer,e.memory,0));
        void* p=nullptr;VC(vkMapMemory(device,e.memory,0,VK_WHOLE_SIZE,0,&p));e.mapped=static_cast<uint8_t*>(p);std::memcpy(e.mapped,e.source.data(),e.size);
        return {type,mp.memoryTypes[type].propertyFlags};
    }
    void events() {
        while(true) {
            XrEventDataBuffer event{XR_TYPE_EVENT_DATA_BUFFER};const auto r=xrPollEvent(xr,&event);if(r==XR_EVENT_UNAVAILABLE)break;XC(r);
            if(event.type==XR_TYPE_EVENT_DATA_INSTANCE_LOSS_PENDING)terminal=true;
            if(event.type==XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED) {
                const auto& change=*reinterpret_cast<XrEventDataSessionStateChanged*>(&event);require(change.session==session,"foreign session event");state=change.state;
                record({{"type","session_state"},{"state",int(state)},{"xr_time",change.time}});
                if(state==XR_SESSION_STATE_READY&&!running) {XrSessionBeginInfo bi{XR_TYPE_SESSION_BEGIN_INFO};bi.primaryViewConfigurationType=XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;XC(xrBeginSession(session,&bi));running=true;}
                if(state==XR_SESSION_STATE_STOPPING&&running) {XC(xrEndSession(session));running=false;terminal=true;}
                if(state==XR_SESSION_STATE_EXITING||state==XR_SESSION_STATE_LOSS_PENDING)terminal=true;
            }
        }
    }
    void transfer(const std::array<uint32_t,2>& indices) {
        VC(vkResetCommandBuffer(command,0));VC(vkResetFences(device,1,&fence));
        VkCommandBufferBeginInfo cb{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};cb.flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;VC(vkBeginCommandBuffer(command,&cb));
        VkMemoryBarrier host{VK_STRUCTURE_TYPE_MEMORY_BARRIER};host.srcAccessMask=VK_ACCESS_HOST_WRITE_BIT;host.dstAccessMask=VK_ACCESS_TRANSFER_READ_BIT|VK_ACCESS_TRANSFER_WRITE_BIT;
        vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_HOST_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,0,1,&host,0,nullptr,0,nullptr);
        for(unsigned i=0;i<2;++i) {
            auto& e=eyes[i];require(indices[i]<e.images.size(),"bad acquired image index");
            std::memset(e.mapped+e.size,0xA5,e.size);
            VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};b.srcAccessMask=VK_ACCESS_MEMORY_READ_BIT|VK_ACCESS_MEMORY_WRITE_BIT;b.dstAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT;
            b.oldLayout=VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;b.newLayout=VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;b.srcQueueFamilyIndex=b.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED;b.image=e.images[indices[i]].image;b.subresourceRange={VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1};
            vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,0,0,nullptr,0,nullptr,1,&b);
            VkBufferImageCopy c{};c.imageSubresource={VK_IMAGE_ASPECT_COLOR_BIT,0,0,1};c.imageExtent={e.width,e.height,1};
            vkCmdCopyBufferToImage(command,e.buffer,b.image,VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,1,&c);
            b.srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT;b.dstAccessMask=VK_ACCESS_TRANSFER_READ_BIT;b.oldLayout=VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;b.newLayout=VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
            vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,0,0,nullptr,0,nullptr,1,&b);
            c.bufferOffset=e.size;vkCmdCopyImageToBuffer(command,b.image,VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,e.buffer,1,&c);
            b.srcAccessMask=VK_ACCESS_TRANSFER_READ_BIT;b.dstAccessMask=VK_ACCESS_COLOR_ATTACHMENT_READ_BIT|VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;b.oldLayout=VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;b.newLayout=VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
            vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,0,0,nullptr,0,nullptr,1,&b);
        }
        VkMemoryBarrier read{VK_STRUCTURE_TYPE_MEMORY_BARRIER};read.srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT;read.dstAccessMask=VK_ACCESS_HOST_READ_BIT;
        vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&read,0,nullptr,0,nullptr);
        VC(vkEndCommandBuffer(command));VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};submit.commandBufferCount=1;submit.pCommandBuffers=&command;VC(vkQueueSubmit(queue,1,&submit,fence));
        VC(vkWaitForFences(device,1,&fence,VK_TRUE,1000000000ull));
        for(unsigned i=0;i<2;++i) {
            auto& e=eyes[i];require(std::memcmp(e.mapped+e.size,e.source.data(),e.size)==0,"actual application image readback differs from source");
            if(!e.captured[indices[i]]) {
                std::vector<uint8_t> captured(e.mapped+e.size,e.mapped+2*e.size);const auto name="eye"+std::to_string(i)+"-image"+std::to_string(indices[i])+".readback.rgba";
                bytes(opt.output/name,captured);record({{"type","image_capture"},{"eye",i},{"image_index",indices[i]},{"file",name},{"sha256",digest(captured)},{"bytes",captured.size()},{"exact_source",true}});e.captured[indices[i]]=true;
            }
        }
    }
    void frame() {
        XrFrameWaitInfo wi{XR_TYPE_FRAME_WAIT_INFO};XrFrameState fs{XR_TYPE_FRAME_STATE};XC(xrWaitFrame(session,&wi,&fs));
        XrFrameBeginInfo begin{XR_TYPE_FRAME_BEGIN_INFO};XC(xrBeginFrame(session,&begin));++frames;
        std::array<XrCompositionLayerProjectionView,2> layer_views{{{XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW},{XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW}}};
        XrCompositionLayerProjection layer{XR_TYPE_COMPOSITION_LAYER_PROJECTION};layer.space=space;layer.viewCount=2;layer.views=layer_views.data();
        bool rendered=false;json details=json::array();
        if(fs.shouldRender) {
            XrViewLocateInfo li{XR_TYPE_VIEW_LOCATE_INFO};li.viewConfigurationType=XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;li.displayTime=fs.predictedDisplayTime;li.space=space;
            XrViewState vs{XR_TYPE_VIEW_STATE};std::array<XrView,2> views{{{XR_TYPE_VIEW},{XR_TYPE_VIEW}}};uint32_t n=0;XC(xrLocateViews(session,&li,&vs,2,&n,views.data()));require(n==2,"locate views changed count");
            const auto needed=XR_VIEW_STATE_POSITION_VALID_BIT|XR_VIEW_STATE_ORIENTATION_VALID_BIT;
            if((vs.viewStateFlags&needed)==needed) {
                std::array<uint32_t,2> indices{};
                for(unsigned i=0;i<2;++i) {XrSwapchainImageAcquireInfo ai{XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO};XC(xrAcquireSwapchainImage(eyes[i].swap,&ai,&indices[i]));XrSwapchainImageWaitInfo wa{XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO};wa.timeout=1000000000ll;const auto r=xrWaitSwapchainImage(eyes[i].swap,&wa);require(r==XR_SUCCESS,"swapchain wait did not complete successfully");}
                transfer(indices);
                for(unsigned i=0;i<2;++i) {
                    auto& e=eyes[i];XrSwapchainImageReleaseInfo ri{XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};XC(xrReleaseSwapchainImage(e.swap,&ri));
                    layer_views[i].pose=views[i].pose;layer_views[i].fov=views[i].fov;layer_views[i].subImage={e.swap,{{0,0},{int32_t(e.width),int32_t(e.height)}},0};
                    details.push_back({{"eye",i},{"image_index",indices[i]},{"width",e.width},{"height",e.height},{"source_sha256",e.sha},{"readback_exact",true},{"pose",pose(views[i].pose)},{"fov",fov(views[i].fov)}});
                }
                rendered=true;
            }
        }
        const XrCompositionLayerBaseHeader* layers[]={reinterpret_cast<XrCompositionLayerBaseHeader*>(&layer)};
        XrFrameEndInfo end{XR_TYPE_FRAME_END_INFO};end.displayTime=fs.predictedDisplayTime;end.environmentBlendMode=XR_ENVIRONMENT_BLEND_MODE_OPAQUE;end.layerCount=rendered?1:0;end.layers=rendered?layers:nullptr;XC(xrEndFrame(session,&end));
        if(rendered)++submitted;
        record({{"type","frame"},{"sequence",frames},{"predicted_display_time",fs.predictedDisplayTime},{"predicted_display_period",fs.predictedDisplayPeriod},{"should_render",bool(fs.shouldRender)},{"projection_submitted",rendered},{"views",details}});
    }
    void run() {
        deadline=Clock::now()+std::chrono::seconds(opt.seconds);init();
        while(!terminal&&frames<opt.frames&&Clock::now()<deadline) {events();if(terminal)break;if(running)frame();else std::this_thread::sleep_for(std::chrono::milliseconds(5));}
        const auto reason=terminal?"runtime_terminal":frames==opt.frames?"frame_limit":"time_limit";
        // Request a normal STOPPING/end-session handshake, but never invent a
        // successful app end if the runtime has not responded within two seconds.
        if(running&&!terminal) {XC(xrRequestExitSession(session));const auto stop=Clock::now()+std::chrono::seconds(2);while(running&&Clock::now()<stop){events();if(running)std::this_thread::sleep_for(std::chrono::milliseconds(5));}}
        VC(vkDeviceWaitIdle(device));
        require(submitted>0,"no projection frame was submitted");
        record({{"type","footer"},{"complete",true},{"frames",frames},{"submitted",submitted},{"stop_reason",reason},{"session_ended",!running},{"quality_only",true},{"latency_comparable",false}});
    }
};
int main(int argc,char**argv) {
    try {
        auto opt=parse(argc,argv);
        if(opt.emit) {
            require(fs::create_directory(opt.output),"cannot create output directory");json manifest={{"schema",1},{"pattern",opt.pattern},{"width",opt.width},{"height",opt.height},{"source_generation","cpu_fixture_only"},{"openxr_exercised",false},{"gpu_exercised",false},{"eyes",json::array()}};
            for(unsigned i=0;i<2;++i){auto data=quality_stimuli::pattern(opt.pattern,opt.width,opt.height,i);auto file="eye"+std::to_string(i)+".source.rgba";bytes(opt.output/file,data);manifest["eyes"].push_back({{"eye",i},{"file",file},{"bytes",data.size()},{"sha256",digest(data)}});}
            std::ofstream f(opt.output/"fixtures.json");f.exceptions(std::ios::badbit|std::ios::failbit);f<<manifest.dump(2)<<'\n';return 0;
        }
        App app(std::move(opt));app.run();return 0;
    } catch(const std::exception& e) {std::cerr<<"quality-stimuli: "<<e.what()<<'\n';return 1;}
}
