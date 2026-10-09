// SPDX-License-Identifier: MIT
#include "compact.hpp"
#include "bounded_file.hpp"
#include "codec.hpp"
#include "vulkan_runner.hpp"
#include <vulkan/vulkan.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#ifndef COMPACT_LEGACY_LABEL
#define COMPACT_LEGACY_LABEL "scalar-lane-zero"
#endif
using Clock=std::chrono::steady_clock;
static void check(VkResult r){if(r!=VK_SUCCESS)throw std::runtime_error("Vulkan result "+std::to_string(r));}

static std::string quote(const std::string& s){std::string out="\"";for(char c:s){if(c=='"'||c=='\\')out+='\\';out+=c;}return out+'"';}
static double elapsed(Clock::time_point start){return std::chrono::duration<double,std::milli>(Clock::now()-start).count();}
struct Result{std::vector<uint32_t> rgba;double gpu_ms;};
struct Handles{
    VkInstance instance{};VkDevice device{};VkCommandPool pool{};VkDescriptorSetLayout layout{};
    VkPipelineLayout pipeline_layout{};VkDescriptorPool descriptors{};VkPipeline pipeline{};VkShaderModule shader{};VkQueryPool queries{};VkFence fence{};
    std::array<VkBuffer,4> buffers{};std::array<VkDeviceMemory,4> memory{};std::array<void*,4> maps{};
    ~Handles(){if(device){vkDeviceWaitIdle(device);if(pipeline)vkDestroyPipeline(device,pipeline,nullptr);if(shader)vkDestroyShaderModule(device,shader,nullptr);
        if(fence)vkDestroyFence(device,fence,nullptr);if(queries)vkDestroyQueryPool(device,queries,nullptr);if(pool)vkDestroyCommandPool(device,pool,nullptr);
        if(descriptors)vkDestroyDescriptorPool(device,descriptors,nullptr);if(pipeline_layout)vkDestroyPipelineLayout(device,pipeline_layout,nullptr);if(layout)vkDestroyDescriptorSetLayout(device,layout,nullptr);
        for(unsigned i=0;i<4;++i){if(maps[i])vkUnmapMemory(device,memory[i]);if(buffers[i])vkDestroyBuffer(device,buffers[i],nullptr);if(memory[i])vkFreeMemory(device,memory[i],nullptr);}vkDestroyDevice(device,nullptr);}if(instance)vkDestroyInstance(instance,nullptr);}
};
class Session{
    Handles c;unsigned width,height,count;size_t wire_capacity;VkQueue queue{};VkCommandBuffer command{};VkDescriptorSet descriptor{};uint64_t timestamp_mask{};double factor{};bool poisoned=false;
public:
    VkPhysicalDeviceProperties properties{};
    Session(unsigned w,unsigned h,size_t capacity,const std::filesystem::path& shader,unsigned index):width(w),height(h),count(((w+15)/16)*((h+15)/16)),wire_capacity((capacity+3)&~size_t(3)){
        VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};app.pApplicationName="compact GPU prototype";app.apiVersion=VK_API_VERSION_1_1;
        VkInstanceCreateInfo instance_info{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};instance_info.pApplicationInfo=&app;check(vkCreateInstance(&instance_info,nullptr,&c.instance));
        uint32_t n=0;check(vkEnumeratePhysicalDevices(c.instance,&n,nullptr));std::vector<VkPhysicalDevice> devices(n);check(vkEnumeratePhysicalDevices(c.instance,&n,devices.data()));if(index>=n)throw std::runtime_error("device index unavailable");auto physical=devices[index];
        vkGetPhysicalDeviceProperties(physical,&properties);if(properties.apiVersion<VK_API_VERSION_1_1||properties.deviceType==VK_PHYSICAL_DEVICE_TYPE_CPU)throw std::runtime_error("hardware Vulkan1.1 required");
        uint32_t nf=0;vkGetPhysicalDeviceQueueFamilyProperties(physical,&nf,nullptr);std::vector<VkQueueFamilyProperties> families(nf);vkGetPhysicalDeviceQueueFamilyProperties(physical,&nf,families.data());
        unsigned family=0;while(family<nf&&!(families[family].queueFlags&VK_QUEUE_COMPUTE_BIT))++family;
        if(family==nf||!families[family].timestampValidBits)throw std::runtime_error("compute timestamps unavailable");
        if(properties.limits.maxComputeSharedMemorySize<6148||properties.limits.maxComputeWorkGroupInvocations<128||properties.limits.maxComputeWorkGroupSize[0]<128||properties.limits.maxComputeWorkGroupCount[0]<count||properties.limits.maxPerStageDescriptorStorageBuffers<4||properties.limits.maxDescriptorSetStorageBuffers<4)throw std::runtime_error("device compute limits");
        float priority=1;VkDeviceQueueCreateInfo qi{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};qi.queueFamilyIndex=family;qi.queueCount=1;qi.pQueuePriorities=&priority;
        VkDeviceCreateInfo di{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};di.queueCreateInfoCount=1;di.pQueueCreateInfos=&qi;check(vkCreateDevice(physical,&di,nullptr,&c.device));vkGetDeviceQueue(c.device,family,0,&queue);
        std::array<VkDeviceSize,4> sizes{wire_capacity,size_t(count)*8,size_t(w)*h*4,size_t(count)*4};VkPhysicalDeviceMemoryProperties memory;vkGetPhysicalDeviceMemoryProperties(physical,&memory);
        for(unsigned i=0;i<4;++i){
            if(sizes[i]>properties.limits.maxStorageBufferRange)throw std::runtime_error("storage buffer limit");
            VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};bi.size=sizes[i];bi.usage=VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;bi.sharingMode=VK_SHARING_MODE_EXCLUSIVE;check(vkCreateBuffer(c.device,&bi,nullptr,&c.buffers[i]));VkMemoryRequirements requirements;vkGetBufferMemoryRequirements(c.device,c.buffers[i],&requirements);
            unsigned type=0;unsigned flags=VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
            while(type<memory.memoryTypeCount&&(!(requirements.memoryTypeBits&(1u<<type))||(memory.memoryTypes[type].propertyFlags&flags)!=flags))++type;
            if(type==memory.memoryTypeCount)throw std::runtime_error("coherent host storage unavailable");
            VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};ai.allocationSize=requirements.size;ai.memoryTypeIndex=type;check(vkAllocateMemory(c.device,&ai,nullptr,&c.memory[i]));check(vkBindBufferMemory(c.device,c.buffers[i],c.memory[i],0));check(vkMapMemory(c.device,c.memory[i],0,VK_WHOLE_SIZE,0,&c.maps[i]));
        }
        std::array<VkDescriptorSetLayoutBinding,4> bindings{};for(unsigned i=0;i<4;++i)bindings[i]={i,VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,1,VK_SHADER_STAGE_COMPUTE_BIT,nullptr};
        VkDescriptorSetLayoutCreateInfo li{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};li.bindingCount=4;li.pBindings=bindings.data();check(vkCreateDescriptorSetLayout(c.device,&li,nullptr,&c.layout));
        VkPushConstantRange push{VK_SHADER_STAGE_COMPUTE_BIT,0,16};VkPipelineLayoutCreateInfo pi{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};pi.setLayoutCount=1;pi.pSetLayouts=&c.layout;pi.pushConstantRangeCount=1;pi.pPushConstantRanges=&push;check(vkCreatePipelineLayout(c.device,&pi,nullptr,&c.pipeline_layout));
        VkDescriptorPoolSize size{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,4};VkDescriptorPoolCreateInfo dpi{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};dpi.maxSets=1;dpi.poolSizeCount=1;dpi.pPoolSizes=&size;check(vkCreateDescriptorPool(c.device,&dpi,nullptr,&c.descriptors));
        VkDescriptorSetAllocateInfo dai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};dai.descriptorPool=c.descriptors;dai.descriptorSetCount=1;dai.pSetLayouts=&c.layout;check(vkAllocateDescriptorSets(c.device,&dai,&descriptor));
        std::array<VkDescriptorBufferInfo,4> infos;std::array<VkWriteDescriptorSet,4> writes{};
        for(unsigned i=0;i<4;++i){infos[i]={c.buffers[i],0,sizes[i]};writes[i].sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;writes[i].dstSet=descriptor;writes[i].dstBinding=i;writes[i].descriptorCount=1;writes[i].descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;writes[i].pBufferInfo=&infos[i];}vkUpdateDescriptorSets(c.device,4,writes.data(),0,nullptr);
        auto bytes=compact::read_bounded(shader,4*1024*1024);if(bytes.empty()||bytes.size()%4)throw std::runtime_error("SPIR-V length");std::vector<uint32_t> code(bytes.size()/4);std::memcpy(code.data(),bytes.data(),bytes.size());
        VkShaderModuleCreateInfo sm{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};sm.codeSize=bytes.size();sm.pCode=code.data();check(vkCreateShaderModule(c.device,&sm,nullptr,&c.shader));VkComputePipelineCreateInfo ci{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};ci.layout=c.pipeline_layout;ci.stage.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;ci.stage.stage=VK_SHADER_STAGE_COMPUTE_BIT;ci.stage.module=c.shader;ci.stage.pName="main";check(vkCreateComputePipelines(c.device,VK_NULL_HANDLE,1,&ci,nullptr,&c.pipeline));
        VkCommandPoolCreateInfo cp{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};cp.flags=VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;cp.queueFamilyIndex=family;check(vkCreateCommandPool(c.device,&cp,nullptr,&c.pool));VkCommandBufferAllocateInfo ca{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};ca.commandPool=c.pool;ca.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY;ca.commandBufferCount=1;check(vkAllocateCommandBuffers(c.device,&ca,&command));
        VkQueryPoolCreateInfo qp{VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO};qp.queryType=VK_QUERY_TYPE_TIMESTAMP;qp.queryCount=2;check(vkCreateQueryPool(c.device,&qp,nullptr,&c.queries));VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};check(vkCreateFence(c.device,&fi,nullptr,&c.fence));
        unsigned bits=families[family].timestampValidBits;timestamp_mask=bits==64?UINT64_MAX:((uint64_t(1)<<bits)-1);factor=properties.limits.timestampPeriod/1e6;
    }
    Result run(const compact::Frame& f){
        if(poisoned)throw std::runtime_error("previous GPU submission failed");
        if(f.width!=width||f.height!=height||f.count!=count||f.records.size()!=count||f.wire.size()>wire_capacity)throw std::runtime_error("session frame shape");
        size_t padded=(f.wire.size()+3)&~size_t(3);std::memcpy(c.maps[0],f.wire.data(),f.wire.size());std::memset(static_cast<uint8_t*>(c.maps[0])+f.wire.size(),0,padded-f.wire.size());std::memcpy(c.maps[1],f.records.data(),count*8);
        std::fill_n(static_cast<uint32_t*>(c.maps[2]),size_t(width)*height,0xdeadbeefu);std::memset(c.maps[3],255,count*4);
        check(vkResetCommandBuffer(command,0));VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};check(vkBeginCommandBuffer(command,&begin));vkCmdResetQueryPool(command,c.queries,0,2);
        VkMemoryBarrier barrier{VK_STRUCTURE_TYPE_MEMORY_BARRIER};barrier.srcAccessMask=VK_ACCESS_HOST_WRITE_BIT;barrier.dstAccessMask=VK_ACCESS_SHADER_READ_BIT|VK_ACCESS_SHADER_WRITE_BIT;vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_HOST_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,0,1,&barrier,0,nullptr,0,nullptr);
        vkCmdBindPipeline(command,VK_PIPELINE_BIND_POINT_COMPUTE,c.pipeline);vkCmdBindDescriptorSets(command,VK_PIPELINE_BIND_POINT_COMPUTE,c.pipeline_layout,0,1,&descriptor,0,nullptr);uint32_t params[]{width,height,count,uint32_t(f.wire.size())};vkCmdPushConstants(command,c.pipeline_layout,VK_SHADER_STAGE_COMPUTE_BIT,0,16,params);
        vkCmdWriteTimestamp(command,VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,c.queries,0);vkCmdDispatch(command,count,1,1);vkCmdWriteTimestamp(command,VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,c.queries,1);
        barrier.srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT;barrier.dstAccessMask=VK_ACCESS_HOST_READ_BIT;vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&barrier,0,nullptr,0,nullptr);check(vkEndCommandBuffer(command));check(vkResetFences(c.device,1,&c.fence));VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};submit.commandBufferCount=1;submit.pCommandBuffers=&command;poisoned=true;check(vkQueueSubmit(queue,1,&submit,c.fence));check(vkWaitForFences(c.device,1,&c.fence,VK_TRUE,1'000'000'000));poisoned=false;
        for(unsigned i=0;i<count;++i)if(static_cast<uint32_t*>(c.maps[3])[i]!=0)throw std::runtime_error("GPU tile "+std::to_string(i)+" status "+std::to_string(static_cast<uint32_t*>(c.maps[3])[i]));
        uint64_t stamps[2];check(vkGetQueryPoolResults(c.device,c.queries,0,2,sizeof(stamps),stamps,8,VK_QUERY_RESULT_64_BIT));Result result{std::vector<uint32_t>(size_t(width)*height),double((stamps[1]-stamps[0])&timestamp_mask)*factor};std::memcpy(result.rgba.data(),c.maps[2],result.rgba.size()*4);return result;
    }
};
struct Sample{std::string name;std::vector<uint8_t> wire,legacy;};
static void distribution(const char* name,std::vector<double> values){std::sort(values.begin(),values.end());auto n=values.size();std::cout<<",\""<<name<<"\":{\"count\":"<<n<<",\"p50\":"<<values[(n-1)/2]<<",\"p95\":"<<values[(n*95+99)/100-1]<<",\"max\":"<<values.back()<<"}";}
int main(int argc,char** argv){try{
    if(argc<3||argc>6)throw std::runtime_error("usage: compact_gpu_probe SHADER_DIR CORPUS_DIR [DEVICE_INDEX=0] [REPETITIONS=5] [LEGACY_KERNEL=coop128]");
    unsigned device=argc>3?std::stoul(argv[3]):0,repetitions=argc>4?std::stoul(argv[4]):5;std::string legacy_kernel=argc>5?argv[5]:"coop128";if(repetitions<1||repetitions>30)throw std::runtime_error("repetition bound");
    std::filesystem::path shaders=argv[1],corpus=argv[2];std::vector<Sample> samples;size_t capacity=4;
    std::vector<std::filesystem::path> paths;for(const auto& entry:std::filesystem::recursive_directory_iterator(corpus))if(entry.path().extension()==".vri2"){if(paths.size()>=256)throw std::runtime_error("corpus file-count cap");paths.push_back(entry.path());}std::sort(paths.begin(),paths.end());
    size_t remaining=128*1024*1024;constexpr size_t packet_cap=28+size_t(65536)*1284;
    for(auto path:paths){auto legacy=path;legacy.replace_extension(".payload");auto wire=compact::read_bounded(path,std::min(packet_cap,remaining));remaining-=wire.size();compact::prepare(wire);capacity=std::max(capacity,wire.size());auto original=compact::read_bounded(legacy,std::min(packet_cap,remaining));remaining-=original.size();samples.push_back({std::filesystem::relative(path,corpus).string(),std::move(wire),std::move(original)});}
    if(samples.empty())throw std::runtime_error("empty corpus");
    std::unique_ptr<Session> compact_gpu;std::unique_ptr<vr444::GpuSession> legacy_gpu;unsigned w=0,h=0;bool first=true;std::string device_name;
    std::cout<<"{\"schema\":1,\"experiment\":\"compact-vri2-gpu\",\"legacy_kernel\":"<<quote(legacy_kernel)<<",\"compact_workgroup\":128,\"compact_legacy_inverse\":"<<quote(COMPACT_LEGACY_LABEL)<<",\"repetitions\":"<<repetitions<<",\"wire_buffer_capacity\":"<<((capacity+3)&~size_t(3))<<",\"cases\":[\n";
    for(const auto& sample:samples){
        auto frame=compact::prepare(sample.wire);auto reference_frame=vr444::unpack(sample.legacy);auto reference=vr444::decode(reference_frame);
        double compact_init=0,legacy_init=0;if(w!=frame.width||h!=frame.height){w=frame.width;h=frame.height;auto started=Clock::now();compact_gpu=std::make_unique<Session>(w,h,capacity,shaders/"compact-decode.spv",device);compact_init=elapsed(started);started=Clock::now();legacy_gpu=std::make_unique<vr444::GpuSession>(w,h,shaders.string(),legacy_kernel,device);legacy_init=elapsed(started);device_name=compact_gpu->properties.deviceName;}
        auto verify=[&](const auto& pixels,const char* path){if(pixels!=reference){size_t at=0;while(at<pixels.size()&&at<reference.size()&&pixels[at]==reference[at])++at;throw std::runtime_error(std::string(path)+" RGB mismatch "+sample.name+" pixel="+std::to_string(at)+" expected="+std::to_string(reference.at(at))+" actual="+std::to_string(pixels.at(at)));}};
        verify(compact_gpu->run(frame).rgba,"compact warmup");verify(legacy_gpu->decode(reference_frame).rgba,"legacy warmup");
        std::vector<double> prepare_ms,compact_wall,compact_total,compact_times,legacy_wall,legacy_times;
        for(unsigned repeat=0;repeat<repetitions;++repeat){
            auto do_compact=[&]{auto start=Clock::now();auto validated=compact::prepare(sample.wire);double prep=elapsed(start);auto dispatch=Clock::now();auto result=compact_gpu->run(validated);double wall=elapsed(dispatch);prepare_ms.push_back(prep);compact_wall.push_back(wall);compact_total.push_back(elapsed(start));compact_times.push_back(result.gpu_ms);verify(result.rgba,"compact repeat");};
            auto do_legacy=[&]{auto start=Clock::now();auto result=legacy_gpu->decode(reference_frame);legacy_wall.push_back(elapsed(start));legacy_times.push_back(result.decode_ms);verify(result.rgba,"legacy repeat");};
            if(repeat%2){do_legacy();do_compact();}else{do_compact();do_legacy();}
        }
        if(!first)std::cout<<",\n";first=false;
        std::cout<<"{\"name\":"<<quote(sample.name)<<",\"width\":"<<w<<",\"height\":"<<h<<",\"q\":"<<frame.q<<",\"tiles\":"<<frame.count<<",\"constant_tiles\":"<<frame.constant_tiles<<",\"compact_wire_bytes\":"<<sample.wire.size()<<",\"compact_input_copy_bytes\":"<<(((sample.wire.size()+3)&~size_t(3))+frame.count*8)<<",\"legacy_input_copy_bytes\":"<<size_t(frame.count)*(vr444::SLOT_WORDS*4+8)<<",\"output_readback_bytes\":"<<size_t(w)*h*4<<",\"rgba_exact\":true,\"all_tile_status_zero\":true,\"compact_init_ms\":"<<compact_init<<",\"legacy_init_ms\":"<<legacy_init;
        distribution("compact_prepare_ms",prepare_ms);distribution("compact_dispatch_readback_ms",compact_wall);distribution("compact_total_ms",compact_total);distribution("compact_gpu_ms",compact_times);distribution("legacy_total_ms",legacy_wall);distribution("legacy_gpu_ms",legacy_times);std::cout<<"}"<<std::flush;
    }
    std::cout<<"\n],\"device\":"<<quote(device_name)<<",\"vendor_id\":"<<compact_gpu->properties.vendorID<<",\"device_id\":"<<compact_gpu->properties.deviceID<<",\"status\":\"pass\",\"scope\":\"Standalone coherent mapped buffers and full RGBA readback; not live decoder or motion-to-photon.\"}\n";return 0;
}catch(const std::exception& e){std::cerr<<"compact_gpu_probe: "<<e.what()<<"\n";return 1;}}
