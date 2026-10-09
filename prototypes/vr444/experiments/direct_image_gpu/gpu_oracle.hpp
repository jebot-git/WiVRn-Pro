// SPDX-License-Identifier: MIT
#pragma once
// Shared standalone image/byte/color oracle. No XR or network operations.
#include "codec.hpp"
#include "host_copy.hpp"
#include <vulkan/vulkan.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace direct_image {
using Clock = std::chrono::steady_clock;
constexpr unsigned modes = 3;
constexpr const char* names[modes] = {"buffer_copy", "image_unorm", "image_uint"};
inline void require(bool ok, const std::string& message) { if (!ok) throw std::runtime_error(message); }
inline void check(VkResult result) { require(result == VK_SUCCESS, "Vulkan result " + std::to_string(result)); }
inline double elapsed(Clock::time_point start) { return std::chrono::duration<double,std::milli>(Clock::now()-start).count(); }
inline std::string quote(const std::string& value) {
    std::string out="\"";
    for (unsigned char c:value) {
        if (c=='"'||c=='\\') out+='\\';
        require(c>=32, "control character in JSON string");
        out+=char(c);
    }
    return out+'"';
}
inline std::vector<const char*> enabled_extensions(bool request_format_list,const std::vector<VkExtensionProperties>* queried) {
    if(!request_format_list) return {};
    require(queried,"requested format-list extension has not been queried");
    require(std::any_of(queried->begin(),queried->end(),[](const auto& e){return std::string(e.extensionName)==VK_KHR_IMAGE_FORMAT_LIST_EXTENSION_NAME;}),"requested VK_KHR_image_format_list unavailable");
    return {VK_KHR_IMAGE_FORMAT_LIST_EXTENSION_NAME};
}
inline void extension_gate_test() {
    require(enabled_extensions(false,nullptr).empty(),"disabled extension unexpectedly enabled");
    std::vector<VkExtensionProperties> empty,unrelated(1),advertised(1);
    std::strcpy(unrelated[0].extensionName,"VK_KHR_timeline_semaphore");
    std::strcpy(advertised[0].extensionName,VK_KHR_IMAGE_FORMAT_LIST_EXTENSION_NAME);
    const std::array<const std::vector<VkExtensionProperties>*,3> missing{nullptr,&empty,&unrelated};
    for(const auto* observed:missing) {
        bool rejected=false;
        try { enabled_extensions(true,observed); } catch(const std::exception&) { rejected=true; }
        require(rejected,"unqueried/missing requested extension accepted");
    }
    require(enabled_extensions(true,&advertised).size()==1,"advertised extension rejected");
}
struct Buffer { VkBuffer handle{}; VkDeviceMemory memory{}; void* mapped{}; VkDeviceSize bytes{}; unsigned type{},flags{}; };
struct Image { VkImage handle{}; VkDeviceMemory memory{}; VkImageView storage{},sample{}; VkImageLayout layout=VK_IMAGE_LAYOUT_UNDEFINED; };
struct Pipeline { VkDescriptorSetLayout descriptors{}; VkPipelineLayout layout{}; VkShaderModule shader{}; VkPipeline handle{}; };
struct Owner {
    VkInstance instance{}; VkDevice device{};
    VkCommandPool pool{}; VkFence fence{}; VkQueryPool queries{}; VkDescriptorPool descriptors{}; VkSampler sampler{};
    std::array<std::array<Buffer,3>,2> inputs{};
    std::array<Buffer,2> output{},raw{},linear{};
    std::array<std::array<Image,2>,modes> images{};
    std::array<Pipeline,modes+1> pipelines{};
    ~Owner() {
        if (device) {
            // This private device is owned by this executable, never borrowed from an XR app.
            vkDeviceWaitIdle(device);
            if (queries) vkDestroyQueryPool(device,queries,nullptr);
            if (fence) vkDestroyFence(device,fence,nullptr);
            if (pool) vkDestroyCommandPool(device,pool,nullptr);
            for (auto p:pipelines) {
                if(p.handle) vkDestroyPipeline(device,p.handle,nullptr);
                if(p.shader) vkDestroyShaderModule(device,p.shader,nullptr);
                if(p.layout) vkDestroyPipelineLayout(device,p.layout,nullptr);
            }
            if(descriptors) vkDestroyDescriptorPool(device,descriptors,nullptr);
            for(auto p:pipelines) if(p.descriptors) vkDestroyDescriptorSetLayout(device,p.descriptors,nullptr);
            if(sampler) vkDestroySampler(device,sampler,nullptr);
            for(auto pair:images) for(auto image:pair) {
                if(image.sample) vkDestroyImageView(device,image.sample,nullptr);
                if(image.storage) vkDestroyImageView(device,image.storage,nullptr);
                if(image.handle) vkDestroyImage(device,image.handle,nullptr);
                if(image.memory) vkFreeMemory(device,image.memory,nullptr);
            }
            auto release=[&](Buffer b) {
                if(b.mapped) vkUnmapMemory(device,b.memory);
                if(b.handle) vkDestroyBuffer(device,b.handle,nullptr);
                if(b.memory) vkFreeMemory(device,b.memory,nullptr);
            };
            for(auto eye:inputs) for(auto b:eye) release(b);
            for(auto list:{output,raw,linear}) for(auto b:list) release(b);
            vkDestroyDevice(device,nullptr);
        }
        if(instance) vkDestroyInstance(instance,nullptr);
    }
};
struct Measurement { double validation_ms{},input_copy_ms{},gpu_ms{},pipeline_wall_ms{},oracle_ms{},max_linear_error{},max_srgb_code_error{}; };
struct Session {
    Owner o;
    unsigned width,height,tiles;
    VkPhysicalDevice physical{}; VkPhysicalDeviceProperties properties{}; VkPhysicalDeviceMemoryProperties memory{};
    VkQueue queue{}; VkCommandBuffer command{}; unsigned family{},timestamp_bits{},submissions=0; bool failed=false,use_format_list=false,use_host_copy=false,host_image_available=false;
    host_copy::Support host;
    std::array<std::array<VkDescriptorSet,2>,modes> decode_sets{},sample_sets{};
    Session(unsigned w,unsigned h,const std::filesystem::path& shaders,unsigned selected,bool software,bool format_list,unsigned index_words=1,bool host_copy=false)
        :width(w),height(h),tiles(((w+15)/16)*((h+15)/16)),use_format_list(format_list),use_host_copy(host_copy) {
        require(w&&h&&w<=4096&&h<=4096&&uint64_t(w)*h<=8388608,"dimensions");
        require(index_words>=1&&index_words<=16,"index stride bound");
        VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
        app.pApplicationName="VR444 direct image offline oracle"; app.apiVersion=VK_API_VERSION_1_1;
        VkInstanceCreateInfo ci{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO}; ci.pApplicationInfo=&app;
        check(vkCreateInstance(&ci,nullptr,&o.instance));
        uint32_t n=0; check(vkEnumeratePhysicalDevices(o.instance,&n,nullptr));
        std::vector<VkPhysicalDevice> devices(n); check(vkEnumeratePhysicalDevices(o.instance,&n,devices.data()));
        require(selected<n,"device index"); physical=devices[selected];
        vkGetPhysicalDeviceProperties(physical,&properties); vkGetPhysicalDeviceMemoryProperties(physical,&memory);
        require(properties.apiVersion>=VK_API_VERSION_1_1,"Vulkan 1.1 required");
        require(software||properties.deviceType!=VK_PHYSICAL_DEVICE_TYPE_CPU,"software Vulkan needs explicit --allow-software");
        const auto& l=properties.limits;
        require(l.maxComputeWorkGroupInvocations>=128&&l.maxComputeWorkGroupSize[0]>=128&&
                l.maxComputeWorkGroupCount[0]>=std::max(tiles,(w*h+63)/64)&&l.maxComputeSharedMemorySize>=6144&&
                l.maxPushConstantsSize>=16&&l.maxImageDimension2D>=std::max(w,h)&&
                l.maxPerStageDescriptorStorageBuffers>=4&&l.maxDescriptorSetStorageBuffers>=4&&
                l.maxPerStageDescriptorStorageImages>=1&&l.maxPerStageDescriptorSamplers>=1&&
                l.maxPerStageDescriptorSampledImages>=1,"device limits");
        vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,nullptr);
        std::vector<VkQueueFamilyProperties> qs(n); vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,qs.data());
        while(family<n&&((qs[family].queueFlags&(VK_QUEUE_GRAPHICS_BIT|VK_QUEUE_COMPUTE_BIT))!=
              (VK_QUEUE_GRAPHICS_BIT|VK_QUEUE_COMPUTE_BIT)||!qs[family].timestampValidBits)) ++family;
        require(family<n,"graphics+compute+timestamp queue required"); timestamp_bits=qs[family].timestampValidBits;
        std::vector<const char*> extensions;
        if(format_list||use_host_copy) {
            check(vkEnumerateDeviceExtensionProperties(physical,nullptr,&n,nullptr));
            std::vector<VkExtensionProperties> available(n); check(vkEnumerateDeviceExtensionProperties(physical,nullptr,&n,available.data()));
            if(format_list)extensions=enabled_extensions(true,&available);
            if(use_host_copy){auto extra=host.query(physical,w,h,available);extensions.insert(extensions.end(),extra.begin(),extra.end());}
        }
        float priority=1;
        VkDeviceQueueCreateInfo qi{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO}; qi.queueFamilyIndex=family; qi.queueCount=1; qi.pQueuePriorities=&priority;
        VkDeviceCreateInfo di{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO}; di.queueCreateInfoCount=1; di.pQueueCreateInfos=&qi;
        di.enabledExtensionCount=extensions.size(); di.ppEnabledExtensionNames=extensions.data();
        if(use_host_copy)di.pNext=&host.enabled;
        check(vkCreateDevice(physical,&di,nullptr,&o.device)); vkGetDeviceQueue(o.device,family,0,&queue);
        if(use_host_copy)host.load(o.device);
        for(unsigned eye=0;eye<2;++eye) {
            make_buffer(o.inputs[eye][0],VkDeviceSize(tiles)*vr444::SLOT_WORDS*4,VK_BUFFER_USAGE_STORAGE_BUFFER_BIT|VK_BUFFER_USAGE_TRANSFER_SRC_BIT,true);
            make_buffer(o.inputs[eye][1],VkDeviceSize(tiles)*index_words*4,VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,true);
            make_buffer(o.inputs[eye][2],VkDeviceSize(tiles)*4,VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,true);
            make_buffer(o.output[eye],VkDeviceSize(w)*h*4,VK_BUFFER_USAGE_STORAGE_BUFFER_BIT|VK_BUFFER_USAGE_TRANSFER_SRC_BIT|VK_BUFFER_USAGE_TRANSFER_DST_BIT,false);
            make_buffer(o.raw[eye],VkDeviceSize(w)*h*4,VK_BUFFER_USAGE_TRANSFER_DST_BIT,true);
            make_buffer(o.linear[eye],VkDeviceSize(w)*h*16,VK_BUFFER_USAGE_STORAGE_BUFFER_BIT|VK_BUFFER_USAGE_TRANSFER_DST_BIT,true);
            for(unsigned mode=0;mode<modes;++mode) make_image(o.images[mode][eye],mode);
        }
        VkSamplerCreateInfo si{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO}; si.magFilter=si.minFilter=VK_FILTER_NEAREST;
        si.mipmapMode=VK_SAMPLER_MIPMAP_MODE_NEAREST; si.addressModeU=si.addressModeV=si.addressModeW=VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
        si.maxLod=0; check(vkCreateSampler(o.device,&si,nullptr,&o.sampler));
        std::array<VkDescriptorPoolSize,3> sizes{{{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,40},{VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,4},{VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,6}}};
        VkDescriptorPoolCreateInfo dp{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO}; dp.maxSets=12; dp.poolSizeCount=sizes.size(); dp.pPoolSizes=sizes.data();
        check(vkCreateDescriptorPool(o.device,&dp,nullptr,&o.descriptors));
        const char* shader_names[]{"buffer.spv","unorm.spv","uint.spv","sample.spv"};
        for(unsigned mode=0;mode<=modes;++mode) {
            std::vector<VkDescriptorSetLayoutBinding> bindings;
            if(mode==modes) bindings={{0,VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,1,VK_SHADER_STAGE_COMPUTE_BIT,nullptr},{1,VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,1,VK_SHADER_STAGE_COMPUTE_BIT,nullptr}};
            else for(unsigned binding:{1u,2u,3u,4u}) bindings.push_back({binding,binding==3&&mode?VK_DESCRIPTOR_TYPE_STORAGE_IMAGE:VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,1,VK_SHADER_STAGE_COMPUTE_BIT,nullptr});
            make_pipeline(o.pipelines[mode],bindings,shaders/shader_names[mode]);
        }
        for(unsigned mode=0;mode<modes;++mode) for(unsigned eye=0;eye<2;++eye) {
            decode_sets[mode][eye]=allocate_set(o.pipelines[mode].descriptors);
            sample_sets[mode][eye]=allocate_set(o.pipelines[modes].descriptors);
            auto& in=o.inputs[eye]; auto& im=o.images[mode][eye];
            write_buffer(decode_sets[mode][eye],1,in[0]); write_buffer(decode_sets[mode][eye],2,in[1]); write_buffer(decode_sets[mode][eye],4,in[2]);
            if(mode) write_image(decode_sets[mode][eye],3,im.storage,VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,VK_IMAGE_LAYOUT_GENERAL);
            else write_buffer(decode_sets[mode][eye],3,o.output[eye]);
            write_image(sample_sets[mode][eye],0,im.sample,VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
            write_buffer(sample_sets[mode][eye],1,o.linear[eye]);
        }
        VkCommandPoolCreateInfo cp{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO}; cp.queueFamilyIndex=family; cp.flags=VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
        check(vkCreateCommandPool(o.device,&cp,nullptr,&o.pool));
        VkCommandBufferAllocateInfo ca{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO}; ca.commandPool=o.pool; ca.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY; ca.commandBufferCount=1;
        check(vkAllocateCommandBuffers(o.device,&ca,&command));
        VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO}; check(vkCreateFence(o.device,&fi,nullptr,&o.fence));
        VkQueryPoolCreateInfo qp{VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO}; qp.queryType=VK_QUERY_TYPE_TIMESTAMP; qp.queryCount=2;
        check(vkCreateQueryPool(o.device,&qp,nullptr,&o.queries));
    }
    unsigned memory_type(uint32_t bits,VkMemoryPropertyFlags required) {
        for(unsigned i=0;i<memory.memoryTypeCount;++i) if((bits&(1u<<i))&&(memory.memoryTypes[i].propertyFlags&required)==required) return i;
        throw std::runtime_error("required memory type unavailable");
    }
    void make_buffer(Buffer& b,VkDeviceSize bytes,VkBufferUsageFlags usage,bool host) {
        require(!(usage&VK_BUFFER_USAGE_STORAGE_BUFFER_BIT)||bytes<=properties.limits.maxStorageBufferRange,"storage buffer size");
        b.bytes=bytes;
        VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO}; bi.size=bytes; bi.usage=usage;
        check(vkCreateBuffer(o.device,&bi,nullptr,&b.handle));
        VkMemoryRequirements req{}; vkGetBufferMemoryRequirements(o.device,b.handle,&req);
        b.type=memory_type(req.memoryTypeBits,host?VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT:VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
        b.flags=memory.memoryTypes[b.type].propertyFlags;
        VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO}; ai.allocationSize=req.size; ai.memoryTypeIndex=b.type;
        check(vkAllocateMemory(o.device,&ai,nullptr,&b.memory)); check(vkBindBufferMemory(o.device,b.handle,b.memory,0));
        if(host) check(vkMapMemory(o.device,b.memory,0,bytes,0,&b.mapped));
    }
    void make_image(Image& image,unsigned mode) {
        const VkFormat base=mode==0?VK_FORMAT_R8G8B8A8_SRGB:mode==1?VK_FORMAT_R8G8B8A8_UNORM:VK_FORMAT_R8G8B8A8_UINT;
        VkFormatProperties fp{}; vkGetPhysicalDeviceFormatProperties(physical,base,&fp);
        VkFormatFeatureFlags required=VK_FORMAT_FEATURE_TRANSFER_SRC_BIT|VK_FORMAT_FEATURE_TRANSFER_DST_BIT|VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT;
        if(mode) required|=VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT;
        require((fp.optimalTilingFeatures&required)==required,"required base format features unavailable");
        vkGetPhysicalDeviceFormatProperties(physical,VK_FORMAT_R8G8B8A8_SRGB,&fp);
        require(fp.optimalTilingFeatures&VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT,"SRGB sampling unsupported");
        std::array<VkFormat,2> formats{base,VK_FORMAT_R8G8B8A8_SRGB};
        VkImageFormatListCreateInfo list{VK_STRUCTURE_TYPE_IMAGE_FORMAT_LIST_CREATE_INFO}; list.viewFormatCount=2; list.pViewFormats=formats.data();
        VkImageCreateInfo ii{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
        ii.flags=mode?VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT:0; ii.imageType=VK_IMAGE_TYPE_2D; ii.format=base;
        ii.extent={width,height,1}; ii.mipLevels=ii.arrayLayers=1; ii.samples=VK_SAMPLE_COUNT_1_BIT; ii.tiling=VK_IMAGE_TILING_OPTIMAL;
        ii.usage=VK_IMAGE_USAGE_SAMPLED_BIT|VK_IMAGE_USAGE_TRANSFER_SRC_BIT|VK_IMAGE_USAGE_TRANSFER_DST_BIT;
        if(!mode&&use_host_copy)ii.usage|=VK_IMAGE_USAGE_HOST_TRANSFER_BIT_EXT;
        if(mode) ii.usage|=VK_IMAGE_USAGE_STORAGE_BIT;
        if(mode&&use_format_list) ii.pNext=&list;
        VkPhysicalDeviceImageFormatInfo2 query{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2};
        query.format=base; query.type=ii.imageType; query.tiling=ii.tiling; query.usage=ii.usage; query.flags=ii.flags; query.pNext=ii.pNext;
        VkImageFormatProperties2 supported{VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2};
        check(vkGetPhysicalDeviceImageFormatProperties2(physical,&query,&supported));
        require(width<=supported.imageFormatProperties.maxExtent.width&&height<=supported.imageFormatProperties.maxExtent.height,"image format dimensions");
        check(vkCreateImage(o.device,&ii,nullptr,&image.handle));
        VkMemoryRequirements req{}; vkGetImageMemoryRequirements(o.device,image.handle,&req);
        VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO}; ai.allocationSize=req.size; ai.memoryTypeIndex=memory_type(req.memoryTypeBits,VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
        check(vkAllocateMemory(o.device,&ai,nullptr,&image.memory)); check(vkBindImageMemory(o.device,image.handle,image.memory,0));
        auto view=[&](VkFormat format,VkImageUsageFlags usage,VkImageView& handle) {
            // SRGB has no storage usage. Per-view usage restriction is core Vulkan 1.1.
            VkImageViewUsageCreateInfo vu{VK_STRUCTURE_TYPE_IMAGE_VIEW_USAGE_CREATE_INFO}; vu.usage=usage;
            VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO}; vi.pNext=&vu; vi.image=image.handle; vi.viewType=VK_IMAGE_VIEW_TYPE_2D; vi.format=format;
            vi.subresourceRange={VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1}; check(vkCreateImageView(o.device,&vi,nullptr,&handle));
        };
        if(mode) view(base,VK_IMAGE_USAGE_STORAGE_BIT,image.storage);
        view(VK_FORMAT_R8G8B8A8_SRGB,VK_IMAGE_USAGE_SAMPLED_BIT,image.sample);
    }
    void make_pipeline(Pipeline& p,const std::vector<VkDescriptorSetLayoutBinding>& bindings,const std::filesystem::path& path) {
        VkDescriptorSetLayoutCreateInfo dl{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO}; dl.bindingCount=bindings.size(); dl.pBindings=bindings.data();
        check(vkCreateDescriptorSetLayout(o.device,&dl,nullptr,&p.descriptors));
        VkPushConstantRange pc{VK_SHADER_STAGE_COMPUTE_BIT,0,16};
        VkPipelineLayoutCreateInfo pl{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO}; pl.setLayoutCount=1; pl.pSetLayouts=&p.descriptors; pl.pushConstantRangeCount=1; pl.pPushConstantRanges=&pc;
        check(vkCreatePipelineLayout(o.device,&pl,nullptr,&p.layout));
        const auto bytes=std::filesystem::file_size(path); require(bytes&&bytes%4==0&&bytes<=16*1024*1024,"shader size");
        std::vector<uint32_t> code(bytes/4); std::ifstream file(path,std::ios::binary); file.read(reinterpret_cast<char*>(code.data()),bytes); require(bool(file),"shader read");
        VkShaderModuleCreateInfo sm{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO}; sm.codeSize=bytes; sm.pCode=code.data();
        check(vkCreateShaderModule(o.device,&sm,nullptr,&p.shader));
        VkComputePipelineCreateInfo pi{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO}; pi.layout=p.layout;
        pi.stage.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO; pi.stage.stage=VK_SHADER_STAGE_COMPUTE_BIT; pi.stage.module=p.shader; pi.stage.pName="main";
        check(vkCreateComputePipelines(o.device,VK_NULL_HANDLE,1,&pi,nullptr,&p.handle));
    }
    VkDescriptorSet allocate_set(VkDescriptorSetLayout layout) {
        VkDescriptorSet result{};
        VkDescriptorSetAllocateInfo a{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO}; a.descriptorPool=o.descriptors; a.descriptorSetCount=1; a.pSetLayouts=&layout;
        check(vkAllocateDescriptorSets(o.device,&a,&result)); return result;
    }
    void write_buffer(VkDescriptorSet set,unsigned binding,const Buffer& b) {
        VkDescriptorBufferInfo info{b.handle,0,b.bytes};
        VkWriteDescriptorSet w{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET}; w.dstSet=set; w.dstBinding=binding; w.descriptorCount=1; w.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER; w.pBufferInfo=&info;
        vkUpdateDescriptorSets(o.device,1,&w,0,nullptr);
    }
    void write_image(VkDescriptorSet set,unsigned binding,VkImageView view,VkDescriptorType type,VkImageLayout layout) {
        VkDescriptorImageInfo info{type==VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER?o.sampler:VK_NULL_HANDLE,view,layout};
        VkWriteDescriptorSet w{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET}; w.dstSet=set; w.dstBinding=binding; w.descriptorCount=1; w.descriptorType=type; w.pImageInfo=&info;
        vkUpdateDescriptorSets(o.device,1,&w,0,nullptr);
    }
    void begin() {
        require(!failed,"session poisoned by an uncertain submission"); check(vkResetCommandBuffer(command,0));
        host_image_available=false;
        VkCommandBufferBeginInfo b{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO}; b.flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT; check(vkBeginCommandBuffer(command,&b));
    }
    void submit() {
        check(vkEndCommandBuffer(command)); check(vkResetFences(o.device,1,&o.fence));
        VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO}; si.commandBufferCount=1; si.pCommandBuffers=&command;
        failed=true; check(vkQueueSubmit(queue,1,&si,o.fence)); ++submissions; check(vkWaitForFences(o.device,1,&o.fence,VK_TRUE,10'000'000'000)); failed=false;
    }
    void barrier(Image& image,VkImageLayout next,VkAccessFlags src,VkAccessFlags dst,VkPipelineStageFlags before,VkPipelineStageFlags after) {
        VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER}; b.oldLayout=image.layout; b.newLayout=next; b.srcAccessMask=src; b.dstAccessMask=dst;
        b.srcQueueFamilyIndex=b.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED; b.image=image.handle; b.subresourceRange={VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1};
        vkCmdPipelineBarrier(command,before,after,0,0,nullptr,0,nullptr,1,&b); image.layout=next;
    }
    void memory_barrier(VkAccessFlags src,VkAccessFlags dst,VkPipelineStageFlags before,VkPipelineStageFlags after) {
        VkMemoryBarrier b{VK_STRUCTURE_TYPE_MEMORY_BARRIER}; b.srcAccessMask=src; b.dstAccessMask=dst; vkCmdPipelineBarrier(command,before,after,0,1,&b,0,nullptr,0,nullptr);
    }
    VkBufferImageCopy region() const { VkBufferImageCopy r{}; r.imageSubresource={VK_IMAGE_ASPECT_COLOR_BIT,0,0,1}; r.imageExtent={width,height,1}; return r; }
    void bind(unsigned pipeline,VkDescriptorSet set) {
        auto& p=o.pipelines[pipeline]; uint32_t constants[]{width,height,0,tiles};
        vkCmdBindPipeline(command,VK_PIPELINE_BIND_POINT_COMPUTE,p.handle);
        vkCmdBindDescriptorSets(command,VK_PIPELINE_BIND_POINT_COMPUTE,p.layout,0,1,&set,0,nullptr);
        vkCmdPushConstants(command,p.layout,VK_SHADER_STAGE_COMPUTE_BIT,0,sizeof(constants),constants);
    }
    void poison(unsigned mode) {
        begin();
        for(unsigned eye=0;eye<2;++eye) {
            auto& im=o.images[mode][eye];
            barrier(im,VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,im.layout==VK_IMAGE_LAYOUT_UNDEFINED?0:VK_ACCESS_MEMORY_READ_BIT|VK_ACCESS_MEMORY_WRITE_BIT,
                    VK_ACCESS_TRANSFER_WRITE_BIT,VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT);
            VkClearColorValue clear{}; VkImageSubresourceRange range{VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1};
            vkCmdClearColorImage(command,im.handle,im.layout,&clear,1,&range);
            vkCmdFillBuffer(command,o.output[eye].handle,0,VK_WHOLE_SIZE,0);
            vkCmdFillBuffer(command,o.linear[eye].handle,0,VK_WHOLE_SIZE,0x7fc00000); // NaN poison
        }
        memory_barrier(VK_ACCESS_TRANSFER_WRITE_BIT,VK_ACCESS_SHADER_WRITE_BIT|VK_ACCESS_TRANSFER_WRITE_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT|VK_PIPELINE_STAGE_TRANSFER_BIT);
        // A fence proves completion; host image transitions additionally need
        // availability of the preceding GPU clear writes to HOST_READ.
        if(!mode&&use_host_copy)memory_barrier(VK_ACCESS_TRANSFER_WRITE_BIT,VK_ACCESS_HOST_READ_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_HOST_BIT);
        submit();
        host_image_available=!mode&&use_host_copy;
        for(auto& raw:o.raw) std::memset(raw.mapped,0x5a,raw.bytes);
    }
    void host_upload(const std::array<std::vector<uint32_t>,2>& pixels) {
        require(use_host_copy&&host_image_available&&!failed,"host copy requires completed available poison images");
        for(unsigned eye=0;eye<2;++eye){require(pixels[eye].size()==size_t(width)*height,"host copy stereo shape");require(o.images[0][eye].layout==VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,"host copy old layout");}
        // Keep both image owners until every host call and the next submit have
        // completed. Any partial Vulkan failure permanently poisons this session.
        failed=true;host_image_available=false;
        for(unsigned eye=0;eye<2;++eye) {
            auto& image=o.images[0][eye];
            VkHostImageLayoutTransitionInfoEXT transition{VK_STRUCTURE_TYPE_HOST_IMAGE_LAYOUT_TRANSITION_INFO_EXT};transition.image=image.handle;transition.oldLayout=image.layout;transition.newLayout=VK_IMAGE_LAYOUT_GENERAL;transition.subresourceRange={VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1};
            check(host.transition(o.device,1,&transition));image.layout=transition.newLayout;
            VkMemoryToImageCopyEXT region{VK_STRUCTURE_TYPE_MEMORY_TO_IMAGE_COPY_EXT};region.pHostPointer=pixels[eye].data();region.imageSubresource={VK_IMAGE_ASPECT_COLOR_BIT,0,0,1};region.imageExtent={width,height,1};
            VkCopyMemoryToImageInfoEXT copy{VK_STRUCTURE_TYPE_COPY_MEMORY_TO_IMAGE_INFO_EXT};copy.dstImage=image.handle;copy.dstImageLayout=image.layout;copy.regionCount=1;copy.pRegions=&region;
            // flags=0: ordinary tightly packed RGBA, never opaque swizzled memcpy.
            check(host.copy(o.device,&copy));
            transition.oldLayout=image.layout;transition.newLayout=VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
            check(host.transition(o.device,1,&transition));image.layout=transition.newLayout;
        }
        failed=false;
    }
    Measurement run(const std::array<vr444::Frame,2>& frames,unsigned mode,const std::array<std::vector<uint32_t>,2>& expected) {
        require(mode<modes,"mode"); Measurement m;
        auto start=Clock::now();
        // Both eyes validate before any mapped writes; failure cannot submit a partial pair.
        for(const auto& f:frames) { require(f.width==width&&f.height==height,"frame shape"); vr444::validate(f); }
        m.validation_ms=elapsed(start); poison(mode);
        start=Clock::now();
        for(unsigned eye=0;eye<2;++eye) {
            const auto& f=frames[eye]; auto& b=o.inputs[eye];
            require(b[0].bytes==f.slots.size()*4&&b[1].bytes==f.lengths.size()*4,"legacy input allocation mismatch");
            std::memcpy(b[0].mapped,f.slots.data(),b[0].bytes); std::memcpy(b[1].mapped,f.lengths.data(),b[1].bytes);
            auto* q=static_cast<uint32_t*>(b[2].mapped);
            for(unsigned tile=0;tile<tiles;++tile) q[tile]=f.tile_q.empty()?f.q:f.tile_q[tile];
        }
        m.input_copy_ms=elapsed(start); return execute(mode,expected,m);
    }
    // Caller must fully preflight both immutable inputs, poison, and populate
    // descriptors before entering this shared dispatch/transfer/image oracle.
    Measurement execute(unsigned mode,const std::array<std::vector<uint32_t>,2>& expected,Measurement m={},unsigned dispatch_groups=0) {
        for(const auto& pixels:expected) require(pixels.size()==size_t(width)*height,"oracle shape");
        return verify(mode,expected,execute_pipeline(mode,m,dispatch_groups));
    }
    // Benchmark callers time image readiness and then invoke verify separately.
    // This method never substitutes for the exactness oracle.
    Measurement execute_pipeline(unsigned mode,Measurement m={},unsigned dispatch_groups=0) {
        require(mode<modes,"mode");
        const unsigned groups=dispatch_groups?dispatch_groups:tiles;
        require(groups<=properties.limits.maxComputeWorkGroupCount[0],"decode dispatch limit");
        auto start=Clock::now(); begin(); vkCmdResetQueryPool(command,o.queries,0,2);
        memory_barrier(VK_ACCESS_HOST_WRITE_BIT,VK_ACCESS_SHADER_READ_BIT,VK_PIPELINE_STAGE_HOST_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);
        vkCmdWriteTimestamp(command,VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,o.queries,0);
        for(unsigned eye=0;eye<2;++eye) {
            auto& im=o.images[mode][eye];
            if(mode) barrier(im,VK_IMAGE_LAYOUT_GENERAL,VK_ACCESS_TRANSFER_WRITE_BIT,VK_ACCESS_SHADER_WRITE_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);
            bind(mode,decode_sets[mode][eye]); vkCmdDispatch(command,groups,1,1);
            if(!mode) {
                memory_barrier(VK_ACCESS_SHADER_WRITE_BIT,VK_ACCESS_TRANSFER_READ_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT);
                auto r=region(); vkCmdCopyBufferToImage(command,o.output[eye].handle,im.handle,im.layout,1,&r);
            }
            barrier(im,VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,mode?VK_ACCESS_SHADER_WRITE_BIT:VK_ACCESS_TRANSFER_WRITE_BIT,VK_ACCESS_SHADER_READ_BIT,
                    mode?VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT:VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);
        }
        vkCmdWriteTimestamp(command,VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,o.queries,1); submit(); m.pipeline_wall_ms=elapsed(start);
        uint64_t stamps[2]{}; check(vkGetQueryPoolResults(o.device,o.queries,0,2,sizeof(stamps),stamps,8,VK_QUERY_RESULT_64_BIT));
        uint64_t mask=timestamp_bits==64?UINT64_MAX:(uint64_t(1)<<timestamp_bits)-1;
        m.gpu_ms=double((stamps[1]-stamps[0])&mask)*properties.limits.timestampPeriod/1e6;
        return m;
    }
    Measurement verify(unsigned mode,const std::array<std::vector<uint32_t>,2>& expected,Measurement m={}) {
        require(mode<modes,"mode");
        for(const auto& pixels:expected) require(pixels.size()==size_t(width)*height,"oracle shape");
        auto start=Clock::now(); begin();
        memory_barrier(VK_ACCESS_HOST_WRITE_BIT,VK_ACCESS_TRANSFER_WRITE_BIT,VK_PIPELINE_STAGE_HOST_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT);
        for(unsigned eye=0;eye<2;++eye) {
            bind(modes,sample_sets[mode][eye]); vkCmdDispatch(command,(width*height+63)/64,1,1);
            auto& im=o.images[mode][eye];
            barrier(im,VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,VK_ACCESS_SHADER_READ_BIT,VK_ACCESS_TRANSFER_READ_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT);
            auto r=region(); vkCmdCopyImageToBuffer(command,im.handle,im.layout,o.raw[eye].handle,1,&r);
        }
        memory_barrier(VK_ACCESS_TRANSFER_WRITE_BIT|VK_ACCESS_SHADER_WRITE_BIT,VK_ACCESS_HOST_READ_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT|VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT);
        submit();
        for(unsigned eye=0;eye<2;++eye) {
            require(expected[eye].size()==size_t(width)*height,"oracle shape");
            require(std::memcmp(o.raw[eye].mapped,expected[eye].data(),o.raw[eye].bytes)==0,"image bytes differ: "+std::string(names[mode])+" eye "+std::to_string(eye));
            auto* sampled=static_cast<float*>(o.linear[eye].mapped);
            for(size_t i=0;i<expected[eye].size();++i) for(unsigned c=0;c<4;++c) {
                double code=double((expected[eye][i]>>(8*c))&255)/255;
                double value=c==3?code:code<=.04045?code/12.92:std::pow((code+.055)/1.055,2.4);
                double actual=sampled[4*i+c]; require(std::isfinite(actual),"nonfinite SRGB sample");
                m.max_linear_error=std::max(m.max_linear_error,std::abs(actual-value));
                // The hardware SRGB conversion may approximate ideal FP64 EOTF.
                // Require its ideal inverse to preserve the exact original 8-bit
                // code, then additionally compare sampled float bits to baseline.
                require(actual>=0&&actual<=1,"SRGB sample range");
                double encoded=c==3?actual:actual<=.0031308?actual*12.92:1.055*std::pow(actual,1/2.4)-.055;
                double code_error=std::abs(encoded-code)*255;
                m.max_srgb_code_error=std::max(m.max_srgb_code_error,code_error);
                if(!(c==3?actual==code:code_error<.5))
                    throw std::runtime_error("SRGB sample does not preserve code after one EOTF/OETF: "+std::string(names[mode])+" eye="+std::to_string(eye)+" pixel="+std::to_string(i)+" channel="+std::to_string(c));
            }
        }
        m.oracle_ms=elapsed(start); return m;
    }
};

} // namespace direct_image
