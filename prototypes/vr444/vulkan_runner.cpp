// SPDX-License-Identifier: MIT
#include "vulkan_runner.hpp"
#include <vulkan/vulkan.h>
#include <array>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include <chrono>
#include <cstdlib>
namespace vr444 {
static void check(VkResult r) { if(r!=VK_SUCCESS)throw std::runtime_error("Vulkan error "+std::to_string(r)); }
// All handles are owned by this scope, including on early capability failure.
struct Context {
    VkInstance instance{};VkDevice device{};VkCommandPool pool{};VkDescriptorSetLayout dsl{};
    VkPipelineLayout pl{};VkDescriptorPool dp{};VkQueryPool qp{};VkFence fence{};
    std::array<VkBuffer,5> buffers{};std::array<VkDeviceMemory,5> mem{};std::array<void*,5> maps{};
    std::array<VkShaderModule,2> modules{};std::array<VkPipeline,2> pipelines{};
    ~Context(){if(device){vkDeviceWaitIdle(device);for(auto x:pipelines)if(x)vkDestroyPipeline(device,x,nullptr);
      for(auto x:modules)if(x)vkDestroyShaderModule(device,x,nullptr);
      if(fence)vkDestroyFence(device,fence,nullptr);
      if(qp)vkDestroyQueryPool(device,qp,nullptr);
      if(pool)vkDestroyCommandPool(device,pool,nullptr);
      if(dp)vkDestroyDescriptorPool(device,dp,nullptr);
      if(pl)vkDestroyPipelineLayout(device,pl,nullptr);
      if(dsl)vkDestroyDescriptorSetLayout(device,dsl,nullptr);
      for(unsigned i=0;i<5;i++){if(maps[i])vkUnmapMemory(device,mem[i]);if(buffers[i])vkDestroyBuffer(device,buffers[i],nullptr);if(mem[i])vkFreeMemory(device,mem[i],nullptr);}
      vkDestroyDevice(device,nullptr);}if(instance)vkDestroyInstance(instance,nullptr);}
};
GpuResult gpu_roundtrip(const std::vector<uint32_t>& rgba,unsigned w,unsigned h,unsigned q,const std::string& shaders,const Frame* decode_only,const std::vector<uint32_t>& tile_q) {
    // Validate public API before allocating resources.
    if(!w||!h||w>4096||h>4096||uint64_t(w)*h>8388608||rgba.size()!=size_t(w)*h||q<1||q>1024)throw std::runtime_error("invalid GPU input");
    std::string kernel=std::getenv("VR444_KERNEL")?std::getenv("VR444_KERNEL"):"baseline";
    if(kernel!="baseline" && kernel!="coop64" && kernel!="coop128")throw std::runtime_error("invalid VR444_KERNEL");
    bool cooperative=kernel!="baseline";unsigned group=kernel=="coop128"?128:64;
    Context c;VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};app.pApplicationName="vr444 research";app.apiVersion=VK_API_VERSION_1_1;
    VkInstanceCreateInfo ici{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};ici.pApplicationInfo=&app;check(vkCreateInstance(&ici,nullptr,&c.instance));
    uint32_t n=0;check(vkEnumeratePhysicalDevices(c.instance,&n,nullptr));if(!n)throw std::runtime_error("no Vulkan device");
    std::vector<VkPhysicalDevice> devices(n);check(vkEnumeratePhysicalDevices(c.instance,&n,devices.data()));
    unsigned selected=0;if(const char* s=std::getenv("VR444_DEVICE"))selected=std::stoul(s);
    if(selected>=n)throw std::runtime_error("VR444_DEVICE index unavailable");
    auto physical=devices[selected];
    VkPhysicalDeviceProperties props;vkGetPhysicalDeviceProperties(physical,&props);
    if(cooperative && (props.limits.maxComputeSharedMemorySize<6144 || props.limits.maxComputeWorkGroupInvocations<group || props.limits.maxComputeWorkGroupSize[0]<group))throw std::runtime_error("cooperative kernel exceeds device limits");
    if(props.apiVersion<VK_API_VERSION_1_1)throw std::runtime_error("Vulkan 1.1 required");
    uint32_t families=0;vkGetPhysicalDeviceQueueFamilyProperties(physical,&families,nullptr);std::vector<VkQueueFamilyProperties> family(families);vkGetPhysicalDeviceQueueFamilyProperties(physical,&families,family.data());
    uint32_t fi=0;while(fi<families && !(family[fi].queueFlags&VK_QUEUE_COMPUTE_BIT))fi++;
    if(fi==families||!family[fi].timestampValidBits)throw std::runtime_error("compute timestamps unavailable");
    float priority=1;VkDeviceQueueCreateInfo qi{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};qi.queueFamilyIndex=fi;qi.queueCount=1;qi.pQueuePriorities=&priority;
    VkDeviceCreateInfo di{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};di.queueCreateInfoCount=1;di.pQueueCreateInfos=&qi;check(vkCreateDevice(physical,&di,nullptr,&c.device));
    VkQueue queue;vkGetDeviceQueue(c.device,fi,0,&queue);
    unsigned count=((w+15)/16)*((h+15)/16);
    if(props.limits.maxPerStageDescriptorStorageBuffers<5 || props.limits.maxDescriptorSetStorageBuffers<5 || (cooperative?count:(count+31)/32)>props.limits.maxComputeWorkGroupCount[0])throw std::runtime_error("buffer descriptors or dispatch dimensions exceed device limits");
    const auto& quantizers=decode_only?decode_only->tile_q:tile_q;
    if(!quantizers.empty() && quantizers.size()!=count)throw std::runtime_error("invalid GPU quantizer map");
    for(auto tq:quantizers)if(tq<1 || tq>1024)throw std::runtime_error("invalid GPU tile q");
    std::array<VkDeviceSize,5> sizes{rgba.size()*4,size_t(count)*SLOT_WORDS*4,size_t(count)*4,rgba.size()*4,size_t(count)*4};
    VkPhysicalDeviceMemoryProperties mp;vkGetPhysicalDeviceMemoryProperties(physical,&mp);
    for(unsigned i=0;i<5;i++) {
        if(sizes[i]>props.limits.maxStorageBufferRange)throw std::runtime_error("storage buffer range too small");
        VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};bi.size=sizes[i];bi.usage=VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;bi.sharingMode=VK_SHARING_MODE_EXCLUSIVE;check(vkCreateBuffer(c.device,&bi,nullptr,&c.buffers[i]));
        VkMemoryRequirements mr;vkGetBufferMemoryRequirements(c.device,c.buffers[i],&mr);unsigned mt=0;
        auto wanted=VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
        while(mt<mp.memoryTypeCount && (!(mr.memoryTypeBits&(1u<<mt)) || (mp.memoryTypes[mt].propertyFlags&wanted)!=unsigned(wanted)))mt++;
        if(mt==mp.memoryTypeCount)throw std::runtime_error("baseline requires coherent mapped storage; add staging for this device");
        VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};ai.allocationSize=mr.size;ai.memoryTypeIndex=mt;check(vkAllocateMemory(c.device,&ai,nullptr,&c.mem[i]));
        check(vkBindBufferMemory(c.device,c.buffers[i],c.mem[i],0));check(vkMapMemory(c.device,c.mem[i],0,VK_WHOLE_SIZE,0,&c.maps[i]));std::memset(c.maps[i],0,sizes[i]);
    }
    std::memcpy(c.maps[0],rgba.data(),sizes[0]);
    for(unsigned i=0;i<count;i++)static_cast<uint32_t*>(c.maps[4])[i]=quantizers.empty()?q:quantizers[i];
    if(decode_only){
        if(decode_only->width!=w || decode_only->height!=h || decode_only->q!=q)throw std::runtime_error("decode metadata mismatch");
        decode(*decode_only); // Full bounded validation before the shader reads network data.
        std::memcpy(c.maps[1],decode_only->slots.data(),sizes[1]);std::memcpy(c.maps[2],decode_only->lengths.data(),sizes[2]);
    }
    std::array<VkDescriptorSetLayoutBinding,5> bindings{};
    for(unsigned i=0;i<5;i++)bindings[i]={i,VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,1,VK_SHADER_STAGE_COMPUTE_BIT,nullptr};
    VkDescriptorSetLayoutCreateInfo li{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};li.bindingCount=5;li.pBindings=bindings.data();check(vkCreateDescriptorSetLayout(c.device,&li,nullptr,&c.dsl));
    VkPushConstantRange range{VK_SHADER_STAGE_COMPUTE_BIT,0,16};VkPipelineLayoutCreateInfo pi{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};pi.setLayoutCount=1;pi.pSetLayouts=&c.dsl;pi.pushConstantRangeCount=1;pi.pPushConstantRanges=&range;check(vkCreatePipelineLayout(c.device,&pi,nullptr,&c.pl));
    VkDescriptorPoolSize ps{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,5};VkDescriptorPoolCreateInfo dpi{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};dpi.maxSets=1;dpi.poolSizeCount=1;dpi.pPoolSizes=&ps;check(vkCreateDescriptorPool(c.device,&dpi,nullptr,&c.dp));
    VkDescriptorSetAllocateInfo dai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};dai.descriptorPool=c.dp;dai.descriptorSetCount=1;dai.pSetLayouts=&c.dsl;VkDescriptorSet ds;check(vkAllocateDescriptorSets(c.device,&dai,&ds));
    std::array<VkDescriptorBufferInfo,5> infos;std::array<VkWriteDescriptorSet,5> writes{};
    for(unsigned i=0;i<5;i++){infos[i]={c.buffers[i],0,sizes[i]};writes[i].sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;writes[i].dstSet=ds;writes[i].dstBinding=i;writes[i].descriptorCount=1;writes[i].descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;writes[i].pBufferInfo=&infos[i];}
    vkUpdateDescriptorSets(c.device,5,writes.data(),0,nullptr);
    for(unsigned i=0;i<2;i++) {
        std::ifstream file(shaders+(i?"/decode":"/encode")+(cooperative?"-"+kernel:"")+".spv",std::ios::binary|std::ios::ate);if(!file)throw std::runtime_error("shader not found");
        auto bytes=file.tellg();if(bytes<=0 || bytes%4)throw std::runtime_error("invalid SPIR-V size");std::vector<uint32_t> code(size_t(bytes)/4);file.seekg(0);file.read(reinterpret_cast<char*>(code.data()),bytes);if(!file)throw std::runtime_error("shader read failed");
        VkShaderModuleCreateInfo sm{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};sm.codeSize=bytes;sm.pCode=code.data();check(vkCreateShaderModule(c.device,&sm,nullptr,&c.modules[i]));
        VkComputePipelineCreateInfo ci{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};ci.layout=c.pl;ci.stage.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;ci.stage.stage=VK_SHADER_STAGE_COMPUTE_BIT;ci.stage.module=c.modules[i];ci.stage.pName="main";check(vkCreateComputePipelines(c.device,VK_NULL_HANDLE,1,&ci,nullptr,&c.pipelines[i]));
    }
    VkCommandPoolCreateInfo pci{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};pci.queueFamilyIndex=fi;check(vkCreateCommandPool(c.device,&pci,nullptr,&c.pool));
    VkCommandBufferAllocateInfo cai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};cai.commandPool=c.pool;cai.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY;cai.commandBufferCount=1;VkCommandBuffer cmd;check(vkAllocateCommandBuffers(c.device,&cai,&cmd));
    VkQueryPoolCreateInfo qpi{VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO};qpi.queryType=VK_QUERY_TYPE_TIMESTAMP;qpi.queryCount=4;check(vkCreateQueryPool(c.device,&qpi,nullptr,&c.qp));
    VkFenceCreateInfo fci{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};check(vkCreateFence(c.device,&fci,nullptr,&c.fence));
    VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};check(vkBeginCommandBuffer(cmd,&begin));vkCmdResetQueryPool(cmd,c.qp,0,4);
    vkCmdBindDescriptorSets(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,c.pl,0,1,&ds,0,nullptr);uint32_t params[]{w,h,q,count};vkCmdPushConstants(cmd,c.pl,VK_SHADER_STAGE_COMPUTE_BIT,0,16,params);
    for(unsigned i=0;i<2;i++){
        vkCmdBindPipeline(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,c.pipelines[i]);vkCmdWriteTimestamp(cmd,VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,c.qp,i*2);
        if(i || !decode_only)vkCmdDispatch(cmd,cooperative?count:(count+31)/32,1,1);
        vkCmdWriteTimestamp(cmd,VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,c.qp,i*2+1);
        VkMemoryBarrier mb{VK_STRUCTURE_TYPE_MEMORY_BARRIER};mb.srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT;mb.dstAccessMask=i?VK_ACCESS_HOST_READ_BIT:VK_ACCESS_SHADER_READ_BIT;
        vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,i?VK_PIPELINE_STAGE_HOST_BIT:VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,0,1,&mb,0,nullptr,0,nullptr);
    }
    check(vkEndCommandBuffer(cmd));VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};submit.commandBufferCount=1;submit.pCommandBuffers=&cmd;
    auto start=std::chrono::steady_clock::now();check(vkQueueSubmit(queue,1,&submit,c.fence));check(vkWaitForFences(c.device,1,&c.fence,VK_TRUE,UINT64_MAX));
    double wall=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
    uint64_t timestamps[4];check(vkGetQueryPoolResults(c.device,c.qp,0,4,sizeof(timestamps),timestamps,8,VK_QUERY_RESULT_64_BIT|VK_QUERY_RESULT_WAIT_BIT));
    uint64_t mask=family[fi].timestampValidBits==64?UINT64_MAX:((uint64_t(1)<<family[fi].timestampValidBits)-1);
    double factor=props.limits.timestampPeriod/1e6;
    Frame f{w,h,q,std::vector<uint32_t>(size_t(count)*SLOT_WORDS),std::vector<uint32_t>(count)};std::vector<uint32_t> out(rgba.size());
    f.tile_q=quantizers;
    std::memcpy(f.slots.data(),c.maps[1],sizes[1]);std::memcpy(f.lengths.data(),c.maps[2],sizes[2]);std::memcpy(out.data(),c.maps[3],sizes[3]);
    return {std::move(f),std::move(out),double((timestamps[1]-timestamps[0])&mask)*factor,double((timestamps[3]-timestamps[2])&mask)*factor,wall,props.deviceName,props.deviceType==VK_PHYSICAL_DEVICE_TYPE_CPU};
}
}
