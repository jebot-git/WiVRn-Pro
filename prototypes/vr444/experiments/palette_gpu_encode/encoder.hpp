// SPDX-License-Identifier: MIT
#pragma once
#include "../direct_image_gpu/gpu_oracle.hpp"
#include "rgb_palette.hpp"
#include "wpc.hpp"
#include <bit>
namespace palette_encode {
using namespace direct_image;
static_assert(std::endian::native==std::endian::little);
inline bool supported(const VkPhysicalDeviceProperties& properties,unsigned tiles) {
    const auto& l=properties.limits;
    return properties.apiVersion>=VK_API_VERSION_1_1&&
        (properties.deviceType==VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU||properties.deviceType==VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU)&&
        tiles&&l.maxComputeWorkGroupInvocations>=128&&l.maxComputeWorkGroupSize[0]>=128&&
        l.maxComputeWorkGroupCount[0]>=tiles&&l.maxComputeSharedMemorySize>=8192&&
        l.maxPushConstantsSize>=16&&l.maxPerStageDescriptorStorageBuffers>=6&&l.maxDescriptorSetStorageBuffers>=6;
}
inline void validate(std::span<const uint32_t> rgba,unsigned width,unsigned height) {
    require(width&&height&&width<=rgb_palette::max_axis&&height<=rgb_palette::max_axis&&uint64_t(width)*height<=rgb_palette::max_pixels,"RPT dimensions");
    require(rgba.size()==size_t(width)*height,"RPT pixel count");
    for(auto p:rgba)if((p>>24)!=255)throw std::runtime_error("RPT requires opaque exact RGB pixels");
}
inline void set32(rgb_palette::Bytes& p,size_t at,uint32_t value) { for(unsigned b=0;b<4;++b)p[at+b]=value>>(8*b); }
inline rgb_palette::Bytes assemble(std::span<const uint8_t> body,unsigned width,unsigned height) {
    require(body.size()<=rgb_palette::max_wire_bytes-32,"RPT body bound");
    rgb_palette::Bytes out(32+body.size());std::copy(body.begin(),body.end(),out.begin()+32);
    set32(out,0,0x31545052);set32(out,4,1|(16<<16));set32(out,8,width);set32(out,12,height);
    set32(out,16,((width+15)/16)*((height+15)/16));set32(out,20,body.size());
    set32(out,24,wpc::crc(std::span(out).subspan(32)));set32(out,28,wpc::crc(std::span(out).first(28)));return out;
}
struct Result { rgb_palette::Bytes wire; double validation_ms{},upload_ms{},gpu_copy_ms{},submit_wait_ms{},assembly_crc_ms{}; };
struct Encoder {
    Owner o; VkPhysicalDeviceProperties properties{};VkPhysicalDeviceMemoryProperties memory{};
    unsigned width,height,tiles,blocks,timestamp_bits=0,submissions=0;
    VkQueue queue{};VkCommandBuffer command{};std::array<VkDescriptorSet,3> sets{};
    Buffer& buffer(unsigned n) { return o.inputs[n/3][n%3]; }
    Encoder(unsigned w,unsigned h,const std::filesystem::path& shaders,unsigned selected):width(w),height(h),tiles(((w+15)/16)*((h+15)/16)),blocks((tiles+255)/256) {
        require(w&&h&&w<=4096&&h<=4096&&uint64_t(w)*h<=rgb_palette::max_pixels,"encoder dimensions");
        VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};app.pApplicationName="Offline RPT1 GPU encoder";app.apiVersion=VK_API_VERSION_1_1;
        VkInstanceCreateInfo ci{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};ci.pApplicationInfo=&app;check(vkCreateInstance(&ci,nullptr,&o.instance));
        uint32_t n=0;check(vkEnumeratePhysicalDevices(o.instance,&n,nullptr));std::vector<VkPhysicalDevice> physical(n);check(vkEnumeratePhysicalDevices(o.instance,&n,physical.data()));require(selected<n,"device index");
        auto p=physical[selected];vkGetPhysicalDeviceProperties(p,&properties);vkGetPhysicalDeviceMemoryProperties(p,&memory);
        require(supported(properties,tiles),"hardware Vulkan1.1 encoder limits unavailable");
        vkGetPhysicalDeviceQueueFamilyProperties(p,&n,nullptr);std::vector<VkQueueFamilyProperties> families(n);vkGetPhysicalDeviceQueueFamilyProperties(p,&n,families.data());
        unsigned family=0;while(family<n&&(!(families[family].queueFlags&VK_QUEUE_COMPUTE_BIT)||!families[family].timestampValidBits))++family;require(family<n,"compute timestamp queue");timestamp_bits=families[family].timestampValidBits;
        float priority=1;VkDeviceQueueCreateInfo qi{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};qi.queueFamilyIndex=family;qi.queueCount=1;qi.pQueuePriorities=&priority;
        VkDeviceCreateInfo di{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};di.queueCreateInfoCount=1;di.pQueueCreateInfos=&qi;check(vkCreateDevice(p,&di,nullptr,&o.device));vkGetDeviceQueue(o.device,family,0,&queue);
        const std::array<VkDeviceSize,6> sizes{size_t(w)*h*4,size_t(tiles)*772,size_t(tiles)*4,size_t(tiles)*4,size_t(blocks)*4,size_t(tiles)*772+8};
        for(unsigned i=0;i<6;++i)make_buffer(buffer(i),sizes[i],VK_BUFFER_USAGE_STORAGE_BUFFER_BIT|VK_BUFFER_USAGE_TRANSFER_DST_BIT|VK_BUFFER_USAGE_TRANSFER_SRC_BIT,i==0);
        make_buffer(o.raw[0],sizes[5],VK_BUFFER_USAGE_TRANSFER_DST_BIT,true);
        VkDescriptorPoolSize dp{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,18};VkDescriptorPoolCreateInfo pool{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};pool.maxSets=3;pool.poolSizeCount=1;pool.pPoolSizes=&dp;check(vkCreateDescriptorPool(o.device,&pool,nullptr,&o.descriptors));
        const std::array<const char*,3> names{"encode.spv","scan.spv","compact.spv"};
        for(unsigned i=0;i<3;++i) {
            pipeline(o.pipelines[i],shaders/names[i]);VkDescriptorSetAllocateInfo a{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};a.descriptorPool=o.descriptors;a.descriptorSetCount=1;a.pSetLayouts=&o.pipelines[i].descriptors;check(vkAllocateDescriptorSets(o.device,&a,&sets[i]));
            for(unsigned b=0;b<6;++b) { VkDescriptorBufferInfo info{buffer(b).handle,0,buffer(b).bytes};VkWriteDescriptorSet write{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};write.dstSet=sets[i];write.dstBinding=b;write.descriptorCount=1;write.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;write.pBufferInfo=&info;vkUpdateDescriptorSets(o.device,1,&write,0,nullptr); }
        }
        VkCommandPoolCreateInfo cp{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};cp.flags=VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;cp.queueFamilyIndex=family;check(vkCreateCommandPool(o.device,&cp,nullptr,&o.pool));
        VkCommandBufferAllocateInfo ca{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};ca.commandPool=o.pool;ca.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY;ca.commandBufferCount=1;check(vkAllocateCommandBuffers(o.device,&ca,&command));
        VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};check(vkCreateFence(o.device,&fi,nullptr,&o.fence));
        VkQueryPoolCreateInfo qp{VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO};qp.queryType=VK_QUERY_TYPE_TIMESTAMP;qp.queryCount=2;check(vkCreateQueryPool(o.device,&qp,nullptr,&o.queries));
    }
    void make_buffer(Buffer& b,VkDeviceSize bytes,VkBufferUsageFlags usage,bool host) {
        require(!(usage&VK_BUFFER_USAGE_STORAGE_BUFFER_BIT)||bytes<=properties.limits.maxStorageBufferRange,"storage range");b.bytes=bytes;
        VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};bi.size=bytes;bi.usage=usage;check(vkCreateBuffer(o.device,&bi,nullptr,&b.handle));VkMemoryRequirements req{};vkGetBufferMemoryRequirements(o.device,b.handle,&req);
        const auto wanted=host?VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT:VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
        while(b.type<memory.memoryTypeCount&&(!(req.memoryTypeBits&(1u<<b.type))||(memory.memoryTypes[b.type].propertyFlags&wanted)!=unsigned(wanted)))++b.type;require(b.type<memory.memoryTypeCount,"memory type");b.flags=memory.memoryTypes[b.type].propertyFlags;
        VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};ai.allocationSize=req.size;ai.memoryTypeIndex=b.type;check(vkAllocateMemory(o.device,&ai,nullptr,&b.memory));check(vkBindBufferMemory(o.device,b.handle,b.memory,0));if(host)check(vkMapMemory(o.device,b.memory,0,bytes,0,&b.mapped));
    }
    void pipeline(Pipeline& p,const std::filesystem::path& path) {
        std::array<VkDescriptorSetLayoutBinding,6> bindings{};for(unsigned i=0;i<6;++i)bindings[i]={i,VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,1,VK_SHADER_STAGE_COMPUTE_BIT,nullptr};
        VkDescriptorSetLayoutCreateInfo dl{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};dl.bindingCount=6;dl.pBindings=bindings.data();check(vkCreateDescriptorSetLayout(o.device,&dl,nullptr,&p.descriptors));
        VkPushConstantRange range{VK_SHADER_STAGE_COMPUTE_BIT,0,16};VkPipelineLayoutCreateInfo pl{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};pl.setLayoutCount=1;pl.pSetLayouts=&p.descriptors;pl.pushConstantRangeCount=1;pl.pPushConstantRanges=&range;check(vkCreatePipelineLayout(o.device,&pl,nullptr,&p.layout));
        auto bytes=std::filesystem::file_size(path);require(bytes&&bytes%4==0&&bytes<=1024*1024,"shader size");std::vector<uint32_t> code(bytes/4);std::ifstream file(path,std::ios::binary);file.read(reinterpret_cast<char*>(code.data()),bytes);require(bool(file),"shader read");
        VkShaderModuleCreateInfo sm{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};sm.codeSize=bytes;sm.pCode=code.data();check(vkCreateShaderModule(o.device,&sm,nullptr,&p.shader));
        VkComputePipelineCreateInfo pi{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};pi.layout=p.layout;pi.stage={VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,nullptr,0,VK_SHADER_STAGE_COMPUTE_BIT,p.shader,"main",nullptr};check(vkCreateComputePipelines(o.device,VK_NULL_HANDLE,1,&pi,nullptr,&p.handle));
    }
    void barrier(VkPipelineStageFlags src,VkAccessFlags source,VkPipelineStageFlags dst,VkAccessFlags destination) {
        VkMemoryBarrier b{VK_STRUCTURE_TYPE_MEMORY_BARRIER};b.srcAccessMask=source;b.dstAccessMask=destination;vkCmdPipelineBarrier(command,src,dst,0,1,&b,0,nullptr,0,nullptr);
    }
    Result run(std::span<const uint32_t> rgba) {
        Result result;auto start=Clock::now();validate(rgba,width,height);result.validation_ms=elapsed(start);start=Clock::now();std::memcpy(buffer(0).mapped,rgba.data(),rgba.size_bytes());result.upload_ms=elapsed(start);
        std::memset(o.raw[0].mapped,0xa5,o.raw[0].bytes);start=Clock::now();
        check(vkResetFences(o.device,1,&o.fence));check(vkResetCommandBuffer(command,0));VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};begin.flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;check(vkBeginCommandBuffer(command,&begin));
        vkCmdResetQueryPool(command,o.queries,0,2);vkCmdWriteTimestamp(command,VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,o.queries,0);
        for(unsigned b=1;b<6;++b)vkCmdFillBuffer(command,buffer(b).handle,0,buffer(b).bytes,b==5?0:0xa5a5a5a5);
        barrier(VK_PIPELINE_STAGE_TRANSFER_BIT,VK_ACCESS_TRANSFER_WRITE_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_ACCESS_SHADER_READ_BIT|VK_ACCESS_SHADER_WRITE_BIT);
        const std::array<unsigned,4> push{width,height,tiles,blocks};
        for(unsigned pass=0;pass<3;++pass) {
            auto& p=o.pipelines[pass];vkCmdBindPipeline(command,VK_PIPELINE_BIND_POINT_COMPUTE,p.handle);vkCmdBindDescriptorSets(command,VK_PIPELINE_BIND_POINT_COMPUTE,p.layout,0,1,&sets[pass],0,nullptr);vkCmdPushConstants(command,p.layout,VK_SHADER_STAGE_COMPUTE_BIT,0,16,push.data());vkCmdDispatch(command,pass==1?blocks:tiles,1,1);
            barrier(VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_ACCESS_SHADER_WRITE_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT|VK_PIPELINE_STAGE_TRANSFER_BIT,VK_ACCESS_SHADER_READ_BIT|VK_ACCESS_SHADER_WRITE_BIT|VK_ACCESS_TRANSFER_READ_BIT);
        }
        VkBufferCopy copy{0,0,o.raw[0].bytes};vkCmdCopyBuffer(command,buffer(5).handle,o.raw[0].handle,1,&copy);
        barrier(VK_PIPELINE_STAGE_TRANSFER_BIT,VK_ACCESS_TRANSFER_WRITE_BIT,VK_PIPELINE_STAGE_HOST_BIT,VK_ACCESS_HOST_READ_BIT);vkCmdWriteTimestamp(command,VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,o.queries,1);check(vkEndCommandBuffer(command));
        VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};submit.commandBufferCount=1;submit.pCommandBuffers=&command;check(vkQueueSubmit(queue,1,&submit,o.fence));++submissions;check(vkWaitForFences(o.device,1,&o.fence,true,10'000'000'000ull));result.submit_wait_ms=elapsed(start);
        std::array<uint64_t,2> timestamps{};check(vkGetQueryPoolResults(o.device,o.queries,0,2,sizeof(timestamps),timestamps.data(),8,VK_QUERY_RESULT_64_BIT|VK_QUERY_RESULT_WAIT_BIT));auto mask=timestamp_bits==64?~uint64_t(0):(uint64_t(1)<<timestamp_bits)-1;result.gpu_copy_ms=((timestamps[1]-timestamps[0])&mask)*properties.limits.timestampPeriod/1e6;
        auto* words=static_cast<const uint32_t*>(o.raw[0].mapped);require(words[0]>=tiles*7&&words[0]<=tiles*772,"GPU body length");
        start=Clock::now();result.wire=assemble({reinterpret_cast<const uint8_t*>(words+1),words[0]},width,height);result.assembly_crc_ms=elapsed(start);return result;
    }
};
}
