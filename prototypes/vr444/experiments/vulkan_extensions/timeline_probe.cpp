// SPDX-License-Identifier: MIT
#include "retirement_ring.hpp"
#include <vulkan/vulkan.h>
#include <array>
#include <chrono>
#include <cstring>
#include <iostream>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>
constexpr unsigned slots=3,eye_bytes=4096;
void need(bool condition,const char* message) { if(!condition) throw std::runtime_error(message); }
void check(VkResult result) { need(result==VK_SUCCESS,("Vulkan result "+std::to_string(result)).c_str()); }
struct Buffer { VkBuffer buffer{}; VkDeviceMemory memory{}; uint32_t* mapped{}; };
struct Owner {
    VkInstance instance{}; VkDevice device{}; VkCommandPool pool{};
    std::array<VkSemaphore,2> semaphores{};
    std::array<std::array<Buffer,2>,slots> decoded{}; std::array<Buffer,slots> readback{};
    ~Owner() {
        if(device) {
            vkDeviceWaitIdle(device);
            if(pool) vkDestroyCommandPool(device,pool,nullptr);
            for(auto semaphore:semaphores) if(semaphore) vkDestroySemaphore(device,semaphore,nullptr);
            auto release=[&](Buffer b) { if(b.mapped) vkUnmapMemory(device,b.memory); if(b.buffer) vkDestroyBuffer(device,b.buffer,nullptr); if(b.memory) vkFreeMemory(device,b.memory,nullptr); };
            for(auto pair:decoded) for(auto b:pair) release(b);
            for(auto b:readback) release(b);
            vkDestroyDevice(device,nullptr);
        }
        if(instance) vkDestroyInstance(instance,nullptr);
    }
};
struct Probe {
    Owner o; retirement::Ring<slots> ring;
    VkPhysicalDeviceProperties properties{}; VkPhysicalDeviceMemoryProperties memory{};
    VkQueue queue{}; std::array<std::array<VkCommandBuffer,2>,slots> commands{};
    PFN_vkGetSemaphoreCounterValueKHR counter{}; PFN_vkWaitSemaphoresKHR wait{}; PFN_vkQueueSubmit2KHR submit2{};
    bool sync2=false; unsigned decoded=0,consumed=0,pressure=0,discarded=0,reused=0,verified=0;
    std::array<unsigned,slots> generations{}; uint64_t last_consumer_frame=0;
    explicit Probe(bool modern,unsigned selected):sync2(modern) {
        VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO}; app.apiVersion=VK_API_VERSION_1_1;
        VkInstanceCreateInfo ci{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO}; ci.pApplicationInfo=&app; check(vkCreateInstance(&ci,nullptr,&o.instance));
        uint32_t n=0; check(vkEnumeratePhysicalDevices(o.instance,&n,nullptr)); std::vector<VkPhysicalDevice> physical(n); check(vkEnumeratePhysicalDevices(o.instance,&n,physical.data()));
        need(selected<n,"physical device index"); auto p=physical[selected]; vkGetPhysicalDeviceProperties(p,&properties); vkGetPhysicalDeviceMemoryProperties(p,&memory);
        need(properties.apiVersion>=VK_API_VERSION_1_1,"Vulkan1.1 required"); need(properties.deviceType!=VK_PHYSICAL_DEVICE_TYPE_CPU,"hardware required for this host qualification");
        check(vkEnumerateDeviceExtensionProperties(p,nullptr,&n,nullptr)); std::vector<VkExtensionProperties> available(n); check(vkEnumerateDeviceExtensionProperties(p,nullptr,&n,available.data()));
        std::set<std::string> names; for(auto e:available) names.emplace(e.extensionName);
        need(names.count(VK_KHR_TIMELINE_SEMAPHORE_EXTENSION_NAME),"timeline extension not advertised");
        if(sync2) need(names.count(VK_KHR_SYNCHRONIZATION_2_EXTENSION_NAME),"synchronization2 extension not advertised");
        VkPhysicalDeviceTimelineSemaphoreFeatures timeline{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TIMELINE_SEMAPHORE_FEATURES};
        VkPhysicalDeviceSynchronization2Features modern_feature{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SYNCHRONIZATION_2_FEATURES};
        if(sync2) timeline.pNext=&modern_feature;
        VkPhysicalDeviceFeatures2 features{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2}; features.pNext=&timeline; vkGetPhysicalDeviceFeatures2(p,&features);
        need(timeline.timelineSemaphore,"timeline feature unavailable"); if(sync2) need(modern_feature.synchronization2,"synchronization2 feature unavailable");
        VkPhysicalDeviceTimelineSemaphoreProperties limits{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TIMELINE_SEMAPHORE_PROPERTIES};
        VkPhysicalDeviceProperties2 properties2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2}; properties2.pNext=&limits; vkGetPhysicalDeviceProperties2(p,&properties2);
        need(limits.maxTimelineSemaphoreValueDifference>=slots,"timeline pending value range");
        vkGetPhysicalDeviceQueueFamilyProperties(p,&n,nullptr); std::vector<VkQueueFamilyProperties> families(n); vkGetPhysicalDeviceQueueFamilyProperties(p,&n,families.data());
        unsigned family=0; while(family<n&&!(families[family].queueFlags&(VK_QUEUE_TRANSFER_BIT|VK_QUEUE_GRAPHICS_BIT|VK_QUEUE_COMPUTE_BIT))) ++family; need(family<n,"transfer queue unavailable");
        std::vector<const char*> extensions{VK_KHR_TIMELINE_SEMAPHORE_EXTENSION_NAME}; if(sync2) extensions.push_back(VK_KHR_SYNCHRONIZATION_2_EXTENSION_NAME);
        float priority=1; VkDeviceQueueCreateInfo qi{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO}; qi.queueFamilyIndex=family; qi.queueCount=1; qi.pQueuePriorities=&priority;
        VkDeviceCreateInfo di{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO}; di.pNext=&timeline; di.queueCreateInfoCount=1; di.pQueueCreateInfos=&qi; di.enabledExtensionCount=extensions.size(); di.ppEnabledExtensionNames=extensions.data();
        check(vkCreateDevice(p,&di,nullptr,&o.device)); vkGetDeviceQueue(o.device,family,0,&queue);
        counter=reinterpret_cast<PFN_vkGetSemaphoreCounterValueKHR>(vkGetDeviceProcAddr(o.device,"vkGetSemaphoreCounterValueKHR"));
        wait=reinterpret_cast<PFN_vkWaitSemaphoresKHR>(vkGetDeviceProcAddr(o.device,"vkWaitSemaphoresKHR"));
        need(counter&&wait,"timeline entry points unavailable");
        if(sync2) { submit2=reinterpret_cast<PFN_vkQueueSubmit2KHR>(vkGetDeviceProcAddr(o.device,"vkQueueSubmit2KHR")); need(submit2,"synchronization2 entry point unavailable"); }
        VkSemaphoreTypeCreateInfo type{VK_STRUCTURE_TYPE_SEMAPHORE_TYPE_CREATE_INFO}; type.semaphoreType=VK_SEMAPHORE_TYPE_TIMELINE;
        VkSemaphoreCreateInfo si{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO}; si.pNext=&type;
        for(auto& semaphore:o.semaphores) check(vkCreateSemaphore(o.device,&si,nullptr,&semaphore));
        VkCommandPoolCreateInfo cp{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO}; cp.flags=VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT; cp.queueFamilyIndex=family;
        check(vkCreateCommandPool(o.device,&cp,nullptr,&o.pool));
        for(unsigned slot=0;slot<slots;++slot) {
            for(auto& b:o.decoded[slot]) make_buffer(b,eye_bytes,false);
            make_buffer(o.readback[slot],eye_bytes*2,true);
            VkCommandBufferAllocateInfo ca{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO}; ca.commandPool=o.pool; ca.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY; ca.commandBufferCount=2;
            check(vkAllocateCommandBuffers(o.device,&ca,commands[slot].data()));
        }
    }
    void make_buffer(Buffer& b,unsigned bytes,bool host) {
        VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO}; bi.size=bytes; bi.usage=VK_BUFFER_USAGE_TRANSFER_SRC_BIT|VK_BUFFER_USAGE_TRANSFER_DST_BIT;
        check(vkCreateBuffer(o.device,&bi,nullptr,&b.buffer)); VkMemoryRequirements req{}; vkGetBufferMemoryRequirements(o.device,b.buffer,&req);
        unsigned type=0; VkMemoryPropertyFlags desired=host?VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT:VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
        while(type<memory.memoryTypeCount&&(!(req.memoryTypeBits&(1u<<type))||(memory.memoryTypes[type].propertyFlags&desired)!=desired)) ++type;
        need(type<memory.memoryTypeCount,"buffer memory unavailable");
        VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO}; ai.allocationSize=req.size; ai.memoryTypeIndex=type; check(vkAllocateMemory(o.device,&ai,nullptr,&b.memory)); check(vkBindBufferMemory(o.device,b.buffer,b.memory,0));
        if(host) { void* mapped=nullptr; check(vkMapMemory(o.device,b.memory,0,bytes,0,&mapped)); b.mapped=static_cast<uint32_t*>(mapped); std::memset(mapped,0xa5,bytes); }
    }
    void begin(VkCommandBuffer command) {
        check(vkResetCommandBuffer(command,0)); VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO}; bi.flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT; check(vkBeginCommandBuffer(command,&bi));
    }
    void submit(VkCommandBuffer command,unsigned signal_domain,uint64_t signal,uint64_t decode_wait=0) {
        check(vkEndCommandBuffer(command)); VkResult result;
        if(sync2) {
            VkSemaphoreSubmitInfo wait_info{VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO}; wait_info.semaphore=o.semaphores[0]; wait_info.value=decode_wait; wait_info.stageMask=VK_PIPELINE_STAGE_2_TRANSFER_BIT;
            VkSemaphoreSubmitInfo signal_info{VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO}; signal_info.semaphore=o.semaphores[signal_domain]; signal_info.value=signal; signal_info.stageMask=VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
            VkCommandBufferSubmitInfo command_info{VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO}; command_info.commandBuffer=command;
            VkSubmitInfo2 si{VK_STRUCTURE_TYPE_SUBMIT_INFO_2}; si.waitSemaphoreInfoCount=decode_wait?1:0; si.pWaitSemaphoreInfos=&wait_info; si.signalSemaphoreInfoCount=1; si.pSignalSemaphoreInfos=&signal_info; si.commandBufferInfoCount=1; si.pCommandBufferInfos=&command_info;
            result=submit2(queue,1,&si,VK_NULL_HANDLE);
        } else {
            VkTimelineSemaphoreSubmitInfo values{VK_STRUCTURE_TYPE_TIMELINE_SEMAPHORE_SUBMIT_INFO}; values.waitSemaphoreValueCount=decode_wait?1:0; values.pWaitSemaphoreValues=&decode_wait; values.signalSemaphoreValueCount=1; values.pSignalSemaphoreValues=&signal;
            VkPipelineStageFlags stage=VK_PIPELINE_STAGE_TRANSFER_BIT;
            VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO}; si.pNext=&values; si.waitSemaphoreCount=decode_wait?1:0; si.pWaitSemaphores=&o.semaphores[0]; si.pWaitDstStageMask=&stage; si.signalSemaphoreCount=1; si.pSignalSemaphores=&o.semaphores[signal_domain]; si.commandBufferCount=1; si.pCommandBuffers=&command;
            result=vkQueueSubmit(queue,1,&si,VK_NULL_HANDLE);
        }
        if(result!=VK_SUCCESS) ring.poison(); check(result);
    }
    void producer(uint64_t frame) {
        auto token=ring.reserve(frame); if(!token) { ++pressure; return; }
        if(generations[token->slot]++) ++reused;
        auto command=commands[token->slot][0]; begin(command);
        for(unsigned eye=0;eye<2;++eye) vkCmdFillBuffer(command,o.decoded[token->slot][eye].buffer,0,eye_bytes,uint32_t(frame*2+eye));
        submit(command,0,ring.decode_value(*token)); ++decoded;
    }
    void consumer() {
        unsigned ready=0; for(const auto& slot:ring.slots()) ready+=slot.phase==retirement::Phase::ready;
        auto token=ring.acquire_latest(); if(!token) return;
        discarded+=ready-1;
        need(token->frame>last_consumer_frame,"consumer freshness regressed"); last_consumer_frame=token->frame;
        auto decode_value=ring.ready_value(*token); auto command=commands[token->slot][1]; begin(command);
        for(unsigned eye=0;eye<2;++eye) { VkBufferCopy copy{0,eye*eye_bytes,eye_bytes}; vkCmdCopyBuffer(command,o.decoded[token->slot][eye].buffer,o.readback[token->slot].buffer,1,&copy); }
        VkMemoryBarrier b{VK_STRUCTURE_TYPE_MEMORY_BARRIER}; b.srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT; b.dstAccessMask=VK_ACCESS_HOST_READ_BIT;
        vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&b,0,nullptr,0,nullptr);
        auto retire=ring.consumer_submit(*token); submit(command,1,retire,decode_value); ++consumed;
    }
    void poll() {
        uint64_t retire=0,decode=0;
        // Query consumer first. A later decode read cannot lag a dependency of an
        // already observed consumer signal on this private single queue.
        check(counter(o.device,o.semaphores[1],&retire)); check(counter(o.device,o.semaphores[0],&decode));
        // Confirm only already-observed values with a zero-time host wait before
        // mapped reads or command-buffer reset. This never waits for future work.
        // The consumer's TRANSFER_WRITE -> HOST_READ barrier supplies visibility.
        if(retire>ring.completed_retire()||decode>ring.completed_decode()) {
            const uint64_t observed[]{decode,retire};
            VkSemaphoreWaitInfo info{VK_STRUCTURE_TYPE_SEMAPHORE_WAIT_INFO}; info.semaphoreCount=2; info.pSemaphores=o.semaphores.data(); info.pValues=observed;
            auto result=wait(o.device,&info,0);
            if(result!=VK_SUCCESS) ring.poison();
            check(result);
        }
        for(unsigned i=0;i<slots;++i) {
            const auto& slot=ring.slots()[i];
            if(slot.phase!=retirement::Phase::retiring||slot.retire>retire) continue;
            // Read before observe releases this generation for the producer.
            for(unsigned eye=0;eye<2;++eye) for(unsigned word=0;word<eye_bytes/4;++word)
                need(o.readback[i].mapped[eye*eye_bytes/4+word]==uint32_t(slot.frame*2+eye),"frame/eye/generation changed before consumer retirement");
            ++verified;
        }
        ring.observe(decode,retire);
    }
    void bounded_wait() {
        unsigned domain; uint64_t value;
        if(ring.completed_decode()<ring.last_decode()) { domain=0; value=ring.completed_decode()+1; }
        else if(ring.completed_retire()<ring.last_retire()) { domain=1; value=ring.completed_retire()+1; }
        else return;
        VkSemaphoreWaitInfo info{VK_STRUCTURE_TYPE_SEMAPHORE_WAIT_INFO}; info.semaphoreCount=1; info.pSemaphores=&o.semaphores[domain]; info.pValues=&value;
        auto result=wait(o.device,&info,1'000'000);
        if(result!=VK_SUCCESS&&result!=VK_TIMEOUT) { ring.poison(); check(result); }
    }
    void run(unsigned frames) {
        unsigned produced=0,iterations=0; auto start=std::chrono::steady_clock::now();
        for(;;) {
            need(std::chrono::steady_clock::now()-start<std::chrono::seconds(15),"qualification timeout; resources remain owned until device drain");
            for(unsigned burst=0;burst<5&&produced<frames;++burst) producer(++produced);
            poll(); if(++iterations%3||produced==frames) consumer(); poll();
            bool idle=true; for(const auto& slot:ring.slots()) idle&=slot.phase==retirement::Phase::free;
            if(produced==frames&&idle) break;
            bounded_wait();
        }
        need(consumed==verified&&consumed>=3&&reused>=3&&pressure>0,"retirement/pressure/reuse coverage incomplete");
        need(decoded==consumed+discarded,"admitted pair accounting");
    }
};
int main(int argc,char** argv) { try {
    bool sync=false; unsigned selected=0;
    for(int i=1;i<argc;++i) { std::string arg=argv[i]; if(arg=="--sync2") sync=true; else if(arg=="--device"&&i+1<argc) { size_t end=0; auto value=std::stoul(argv[++i],&end); need(end==std::strlen(argv[i])&&value<16,"device index"); selected=value; } else throw std::runtime_error("usage: timeline-probe [--sync2] [--device N]"); }
    Probe probe(sync,selected); probe.run(512);
    std::cout<<"{\"schema\":1,\"pass\":true,\"device\":\""<<probe.properties.deviceName<<"\",\"timeline_enabled\":true,\"synchronization2_enabled\":"<<(sync?"true":"false")<<",\"slots\":3,\"offered_pairs\":512,\"decoded_pairs\":"<<probe.decoded<<",\"consumed_pairs\":"<<probe.consumed<<",\"verified_pairs\":"<<probe.verified<<",\"pressure_drops\":"<<probe.pressure<<",\"unacquired_completed_drops\":"<<probe.discarded<<",\"slot_reuses\":"<<probe.reused<<",\"headset_used\":false,\"performance_claim\":false,\"scope\":\"Private queue buffer identity/lifetime model; no images, codec, XR or presentation latency\"}\n";
    return 0;
} catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; } }
