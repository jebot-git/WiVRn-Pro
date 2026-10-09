// SPDX-License-Identifier: MIT
// Standalone upload/sample probe; no OpenXR, video codec, encoder-quality or
// photon-latency claim. Usage: texture_sample_bench shader.spv [device_index] [frames] [--manual-srgb-only]
// Fixture source: https://learn.microsoft.com/en-us/windows/uwp/graphics-concepts/bc7-format
// Mode 6: unary 7-bit mode, eight 7-bit endpoint components, two endpoint P-bits,
// and 63 index bits (3-bit first anchor + 15 four-bit indices). Equal endpoints
// with P=0 exactly represent any even RGBA value. Every block has its own color.
#include <vulkan/vulkan.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>
#ifdef TEXTURE_BCDEC_HEADER
#define BCDEC_IMPLEMENTATION
#include TEXTURE_BCDEC_HEADER
#endif
namespace {
constexpr uint32_t W=576,H=640;
constexpr unsigned warmups=5;
void check(VkResult r){if(r!=VK_SUCCESS)throw std::runtime_error("Vulkan error "+std::to_string(r));}
using Clock=std::chrono::steady_clock;
double elapsed(Clock::time_point t){return std::chrono::duration<double,std::milli>(Clock::now()-t).count();}
std::array<uint8_t,4> color(unsigned bx,unsigned by,unsigned seed){
    return {uint8_t(((bx*3+seed*7)%128)*2),uint8_t(((by*5+seed*11)%128)*2),
            uint8_t(((bx*13+by*17+seed*19)%128)*2),254};
}
std::array<uint8_t,16> bc7(const std::array<uint8_t,4>& c){
    std::array<uint8_t,16> b{};unsigned bit=0;
    auto put=[&](unsigned v,unsigned n){for(unsigned k=0;k<n;++k,++bit)b[bit/8]|=((v>>k)&1)<<(bit%8);};
    put(64,7);for(unsigned ch=0;ch<4;++ch){put(c[ch]>>1,7);put(c[ch]>>1,7);}
    put(0,2);put(0,3);for(unsigned i=1;i<16;++i)put(0,4);
    if(bit!=128)throw std::runtime_error("BC7 fixture size");
#ifdef TEXTURE_BCDEC_HEADER
    uint8_t decoded[64]{};bcdec_bc7(b.data(),decoded,16);
    for(unsigned i=0;i<16;++i)for(unsigned ch=0;ch<4;++ch)
        if(decoded[i*4+ch]!=c[ch])throw std::runtime_error("independent BC7 fixture decode mismatch");
#endif
    return b;
}
std::vector<uint8_t> fixture(bool compressed,unsigned seed){
    std::vector<uint8_t> bytes(compressed?size_t(W)*H:size_t(W)*H*4);
    for(unsigned by=0;by<H/4;++by)for(unsigned bx=0;bx<W/4;++bx){
        auto c=color(bx,by,seed);
        if(compressed){auto b=bc7(c);std::memcpy(bytes.data()+(by*(W/4)+bx)*16,b.data(),16);}
        else for(unsigned y=0;y<4;++y)for(unsigned x=0;x<4;++x)
            std::memcpy(bytes.data()+((by*4+y)*W+bx*4+x)*4,c.data(),4);
    }return bytes;
}
float component(unsigned x,unsigned y,unsigned ch,unsigned seed,bool srgb){
    static const auto linear=[](){std::array<float,256> lut{};for(unsigned i=0;i<256;++i){float s=i/255.f;lut[i]=s<=0.04045f?s/12.92f:std::pow((s+0.055f)/1.055f,2.4f);}return lut;}();
    auto code=color(x/4,y/4,seed)[ch];return srgb&&ch<3?linear[code]:code/255.f;
}
struct Validation {float error=0,actual=0,expected=0;unsigned x=0,y=0,channel=0,seed=0;std::array<unsigned,4> source_codes{};};
Validation verify(const float* output,unsigned seed,bool srgb,bool linear){
    Validation worst;
    for(unsigned y=0;y<H;++y)for(unsigned x=0;x<W;++x)for(unsigned ch=0;ch<4;++ch){
        float expected=component(x,y,ch,seed,srgb);
        if(linear){
            unsigned x1=std::min(x+1,W-1),y1=std::min(y+1,H-1);
            expected=0.5625f*expected+0.1875f*component(x1,y,ch,seed,srgb)
                +0.1875f*component(x,y1,ch,seed,srgb)+0.0625f*component(x1,y1,ch,seed,srgb);
        }
        float actual=output[(size_t(y)*W+x)*4+ch];
        if(!std::isfinite(actual))throw std::runtime_error("nonfinite sample output");
        float error=std::abs(actual-expected);
        if(error>worst.error){
            unsigned x1=std::min(x+1,W-1),y1=std::min(y+1,H-1);
            worst={error,actual,expected,x,y,ch,seed,{color(x/4,y/4,seed)[ch],color(x1/4,y/4,seed)[ch],color(x/4,y1/4,seed)[ch],color(x1/4,y1/4,seed)[ch]}};
        }
    }
    return worst;
}
struct Buffer {VkBuffer buffer{};VkDeviceMemory memory{};void* mapped{};};
struct Context {
    VkInstance instance{};VkPhysicalDevice physical{};VkDevice device{};VkQueue queue{};
    VkPhysicalDeviceProperties props{};VkPhysicalDeviceMemoryProperties memory{};
    unsigned family{};uint64_t timestamp_mask{};double ms_per_tick{};
    VkCommandPool pool{};VkCommandBuffer cmd{};VkFence fence{};VkQueryPool queries{};
    VkDescriptorSetLayout dsl{};VkPipelineLayout layout{};VkDescriptorPool descriptors{};
    VkDescriptorSet ds{};VkShaderModule shader{};VkPipeline pipeline{};
    VkImage image{};VkDeviceMemory image_memory{};VkImageView view{};
    std::array<VkSampler,2> samplers{};Buffer upload,output,readback;
    ~Context(){if(device){vkDeviceWaitIdle(device);if(pipeline)vkDestroyPipeline(device,pipeline,nullptr);
        if(shader)vkDestroyShaderModule(device,shader,nullptr);
        if(layout)vkDestroyPipelineLayout(device,layout,nullptr);
        if(descriptors)vkDestroyDescriptorPool(device,descriptors,nullptr);
        if(dsl)vkDestroyDescriptorSetLayout(device,dsl,nullptr);
        for(auto s:samplers)if(s)vkDestroySampler(device,s,nullptr);
        if(view)vkDestroyImageView(device,view,nullptr);
        if(image)vkDestroyImage(device,image,nullptr);
        if(image_memory)vkFreeMemory(device,image_memory,nullptr);
        for(auto b:{upload,output,readback}){if(b.mapped)vkUnmapMemory(device,b.memory);if(b.buffer)vkDestroyBuffer(device,b.buffer,nullptr);if(b.memory)vkFreeMemory(device,b.memory,nullptr);}
        if(queries)vkDestroyQueryPool(device,queries,nullptr);
        if(fence)vkDestroyFence(device,fence,nullptr);
        if(pool)vkDestroyCommandPool(device,pool,nullptr);
        vkDestroyDevice(device,nullptr);}
        if(instance)vkDestroyInstance(instance,nullptr);}
    unsigned memory_type(uint32_t bits,VkMemoryPropertyFlags required){
        for(unsigned i=0;i<memory.memoryTypeCount;++i)if((bits&(1u<<i))&&(memory.memoryTypes[i].propertyFlags&required)==required)return i;
        throw std::runtime_error("required memory type unavailable");
    }
    void buffer(Buffer& b,VkDeviceSize size,VkBufferUsageFlags usage,bool host){
        VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};bi.size=size;bi.usage=usage;
        check(vkCreateBuffer(device,&bi,nullptr,&b.buffer));VkMemoryRequirements mr;vkGetBufferMemoryRequirements(device,b.buffer,&mr);
        VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};ai.allocationSize=mr.size;
        ai.memoryTypeIndex=memory_type(mr.memoryTypeBits,host?VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT:VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
        check(vkAllocateMemory(device,&ai,nullptr,&b.memory));check(vkBindBufferMemory(device,b.buffer,b.memory,0));
        if(host)check(vkMapMemory(device,b.memory,0,size,0,&b.mapped));
    }
    bool init(VkFormat format,bool compressed,const std::string& shader_path,unsigned device_index){
        VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};app.pApplicationName="texture sample benchmark";app.apiVersion=VK_API_VERSION_1_1;
        VkInstanceCreateInfo ici{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};ici.pApplicationInfo=&app;check(vkCreateInstance(&ici,nullptr,&instance));
        uint32_t n=0;check(vkEnumeratePhysicalDevices(instance,&n,nullptr));std::vector<VkPhysicalDevice> devices(n);check(vkEnumeratePhysicalDevices(instance,&n,devices.data()));
        if(device_index>=n)throw std::runtime_error("physical device unavailable");
        physical=devices[device_index];vkGetPhysicalDeviceProperties(physical,&props);
        if(props.deviceType==VK_PHYSICAL_DEVICE_TYPE_CPU)throw std::runtime_error("software Vulkan is forbidden");
        VkImageFormatProperties ifp{};VkFormatProperties fp{};vkGetPhysicalDeviceFormatProperties(physical,format,&fp);
        VkFormatFeatureFlags wanted=VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT|VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT|VK_FORMAT_FEATURE_TRANSFER_DST_BIT;
        if((fp.optimalTilingFeatures&wanted)!=wanted||vkGetPhysicalDeviceImageFormatProperties(physical,format,VK_IMAGE_TYPE_2D,VK_IMAGE_TILING_OPTIMAL,VK_IMAGE_USAGE_SAMPLED_BIT|VK_IMAGE_USAGE_TRANSFER_DST_BIT,0,&ifp)!=VK_SUCCESS)return false;
        if(ifp.maxExtent.width<W||ifp.maxExtent.height<H)throw std::runtime_error("image dimensions unsupported");
        vkGetPhysicalDeviceMemoryProperties(physical,&memory);vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,nullptr);
        std::vector<VkQueueFamilyProperties> families(n);vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,families.data());
        while(family<n&&(!(families[family].queueFlags&VK_QUEUE_COMPUTE_BIT)||!families[family].timestampValidBits))++family;
        if(family==n)throw std::runtime_error("compute timestamp queue unavailable");
        unsigned bits=families[family].timestampValidBits;timestamp_mask=bits==64?UINT64_MAX:(uint64_t(1)<<bits)-1;ms_per_tick=props.limits.timestampPeriod/1e6;
        float priority=1;VkDeviceQueueCreateInfo qi{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};qi.queueFamilyIndex=family;qi.queueCount=1;qi.pQueuePriorities=&priority;
        VkPhysicalDeviceFeatures supported{},enabled{};vkGetPhysicalDeviceFeatures(physical,&supported);enabled.textureCompressionBC=supported.textureCompressionBC;
        VkDeviceCreateInfo di{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};di.queueCreateInfoCount=1;di.pQueueCreateInfos=&qi;di.pEnabledFeatures=&enabled;check(vkCreateDevice(physical,&di,nullptr,&device));vkGetDeviceQueue(device,family,0,&queue);
        buffer(upload,size_t(W)*H*(compressed?1:4),VK_BUFFER_USAGE_TRANSFER_SRC_BIT,true);
        buffer(output,size_t(W)*H*16,VK_BUFFER_USAGE_STORAGE_BUFFER_BIT|VK_BUFFER_USAGE_TRANSFER_SRC_BIT,false);
        buffer(readback,size_t(W)*H*16,VK_BUFFER_USAGE_TRANSFER_DST_BIT,true);
        VkImageCreateInfo ii{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};ii.imageType=VK_IMAGE_TYPE_2D;ii.format=format;ii.extent={W,H,1};ii.mipLevels=1;ii.arrayLayers=1;ii.samples=VK_SAMPLE_COUNT_1_BIT;ii.tiling=VK_IMAGE_TILING_OPTIMAL;ii.usage=VK_IMAGE_USAGE_SAMPLED_BIT|VK_IMAGE_USAGE_TRANSFER_DST_BIT;
        check(vkCreateImage(device,&ii,nullptr,&image));VkMemoryRequirements mr;vkGetImageMemoryRequirements(device,image,&mr);
        VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};ai.allocationSize=mr.size;ai.memoryTypeIndex=memory_type(mr.memoryTypeBits,VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);check(vkAllocateMemory(device,&ai,nullptr,&image_memory));check(vkBindImageMemory(device,image,image_memory,0));
        VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};vi.image=image;vi.viewType=VK_IMAGE_VIEW_TYPE_2D;vi.format=format;vi.subresourceRange={VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1};check(vkCreateImageView(device,&vi,nullptr,&view));
        for(unsigned i=0;i<2;++i){VkSamplerCreateInfo si{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};si.magFilter=si.minFilter=i?VK_FILTER_LINEAR:VK_FILTER_NEAREST;si.mipmapMode=VK_SAMPLER_MIPMAP_MODE_NEAREST;si.addressModeU=si.addressModeV=si.addressModeW=VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;check(vkCreateSampler(device,&si,nullptr,&samplers[i]));}
        VkDescriptorSetLayoutBinding bindings[]={{0,VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,1,VK_SHADER_STAGE_COMPUTE_BIT,nullptr},{1,VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,1,VK_SHADER_STAGE_COMPUTE_BIT,nullptr}};
        VkDescriptorSetLayoutCreateInfo li{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};li.bindingCount=2;li.pBindings=bindings;check(vkCreateDescriptorSetLayout(device,&li,nullptr,&dsl));
        VkPushConstantRange range{VK_SHADER_STAGE_COMPUTE_BIT,0,16};VkPipelineLayoutCreateInfo pli{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};pli.setLayoutCount=1;pli.pSetLayouts=&dsl;pli.pushConstantRangeCount=1;pli.pPushConstantRanges=&range;check(vkCreatePipelineLayout(device,&pli,nullptr,&layout));
        VkDescriptorPoolSize sizes[]={{VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,1},{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,1}};
        VkDescriptorPoolCreateInfo dpi{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};dpi.maxSets=1;dpi.poolSizeCount=2;dpi.pPoolSizes=sizes;check(vkCreateDescriptorPool(device,&dpi,nullptr,&descriptors));
        VkDescriptorSetAllocateInfo dai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};dai.descriptorPool=descriptors;dai.descriptorSetCount=1;dai.pSetLayouts=&dsl;check(vkAllocateDescriptorSets(device,&dai,&ds));
        std::ifstream file(shader_path,std::ios::binary|std::ios::ate);if(!file)throw std::runtime_error("shader unavailable");auto bytes=file.tellg();if(bytes<=0||bytes%4)throw std::runtime_error("bad shader size");std::vector<uint32_t> words(size_t(bytes)/4);file.seekg(0);file.read(reinterpret_cast<char*>(words.data()),bytes);if(!file)throw std::runtime_error("shader read failed");
        VkShaderModuleCreateInfo sm{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};sm.codeSize=size_t(bytes);sm.pCode=words.data();check(vkCreateShaderModule(device,&sm,nullptr,&shader));
        VkComputePipelineCreateInfo pi{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};pi.layout=layout;pi.stage={VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,nullptr,0,VK_SHADER_STAGE_COMPUTE_BIT,shader,"main",nullptr};check(vkCreateComputePipelines(device,VK_NULL_HANDLE,1,&pi,nullptr,&pipeline));
        VkCommandPoolCreateInfo pci{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};pci.queueFamilyIndex=family;pci.flags=VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;check(vkCreateCommandPool(device,&pci,nullptr,&pool));
        VkCommandBufferAllocateInfo cai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};cai.commandPool=pool;cai.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY;cai.commandBufferCount=1;check(vkAllocateCommandBuffers(device,&cai,&cmd));
        VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};check(vkCreateFence(device,&fi,nullptr,&fence));VkQueryPoolCreateInfo qpi{VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO};qpi.queryType=VK_QUERY_TYPE_TIMESTAMP;qpi.queryCount=6;check(vkCreateQueryPool(device,&qpi,nullptr,&queries));return true;
    }
    void descriptor(bool linear){
        VkDescriptorImageInfo image_info{samplers[linear],view,VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL};VkDescriptorBufferInfo buffer_info{output.buffer,0,VK_WHOLE_SIZE};
        VkWriteDescriptorSet writes[2]{};for(unsigned i=0;i<2;++i){writes[i].sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;writes[i].dstSet=ds;writes[i].dstBinding=i;writes[i].descriptorCount=1;}
        writes[0].descriptorType=VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;writes[0].pImageInfo=&image_info;writes[1].descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;writes[1].pBufferInfo=&buffer_info;vkUpdateDescriptorSets(device,2,writes,0,nullptr);
    }
    std::array<double,4> run(const std::vector<uint8_t>& bytes,bool first,bool linear,bool manual){
        auto began=Clock::now();std::memcpy(upload.mapped,bytes.data(),bytes.size());check(vkResetCommandBuffer(cmd,0));
        VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};bi.flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;check(vkBeginCommandBuffer(cmd,&bi));vkCmdResetQueryPool(cmd,queries,0,6);
        VkMemoryBarrier host{VK_STRUCTURE_TYPE_MEMORY_BARRIER};host.srcAccessMask=VK_ACCESS_HOST_WRITE_BIT;host.dstAccessMask=VK_ACCESS_TRANSFER_READ_BIT;
        vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_HOST_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,0,1,&host,0,nullptr,0,nullptr);
        vkCmdWriteTimestamp(cmd,VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,queries,0);
        VkImageMemoryBarrier im{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};im.srcAccessMask=first?0:VK_ACCESS_SHADER_READ_BIT;im.dstAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT;im.oldLayout=first?VK_IMAGE_LAYOUT_UNDEFINED:VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;im.newLayout=VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;im.srcQueueFamilyIndex=im.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED;im.image=image;im.subresourceRange={VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1};
        vkCmdPipelineBarrier(cmd,first?VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT:VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,0,0,nullptr,0,nullptr,1,&im);
        VkBufferImageCopy region{};region.imageSubresource={VK_IMAGE_ASPECT_COLOR_BIT,0,0,1};region.imageExtent={W,H,1};vkCmdCopyBufferToImage(cmd,upload.buffer,image,VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,1,&region);
        vkCmdWriteTimestamp(cmd,VK_PIPELINE_STAGE_TRANSFER_BIT,queries,1);
        im.srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT;im.dstAccessMask=VK_ACCESS_SHADER_READ_BIT;im.oldLayout=VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;im.newLayout=VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
        VkMemoryBarrier out{VK_STRUCTURE_TYPE_MEMORY_BARRIER};out.srcAccessMask=VK_ACCESS_TRANSFER_READ_BIT;out.dstAccessMask=VK_ACCESS_SHADER_WRITE_BIT;
        vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,0,1,&out,0,nullptr,1,&im);
        vkCmdWriteTimestamp(cmd,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,queries,2);
        vkCmdBindPipeline(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,pipeline);vkCmdBindDescriptorSets(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,layout,0,1,&ds,0,nullptr);
        struct Params{uint32_t w,h;float offset;uint32_t manual;}params{W,H,linear?0.25f:0.f,uint32_t(manual)};vkCmdPushConstants(cmd,layout,VK_SHADER_STAGE_COMPUTE_BIT,0,16,&params);vkCmdDispatch(cmd,(W+7)/8,(H+7)/8,1);
        vkCmdWriteTimestamp(cmd,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,queries,3);
        out.srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT;out.dstAccessMask=VK_ACCESS_TRANSFER_READ_BIT;vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,0,1,&out,0,nullptr,0,nullptr);
        vkCmdWriteTimestamp(cmd,VK_PIPELINE_STAGE_TRANSFER_BIT,queries,4);VkBufferCopy copy{0,0,size_t(W)*H*16};vkCmdCopyBuffer(cmd,output.buffer,readback.buffer,1,&copy);vkCmdWriteTimestamp(cmd,VK_PIPELINE_STAGE_TRANSFER_BIT,queries,5);
        out.srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT;out.dstAccessMask=VK_ACCESS_HOST_READ_BIT;vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&out,0,nullptr,0,nullptr);check(vkEndCommandBuffer(cmd));check(vkResetFences(device,1,&fence));
        VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};submit.commandBufferCount=1;submit.pCommandBuffers=&cmd;check(vkQueueSubmit(queue,1,&submit,fence));check(vkWaitForFences(device,1,&fence,VK_TRUE,2'000'000'000));double wall=elapsed(began);
        uint64_t t[6];check(vkGetQueryPoolResults(device,queries,0,6,sizeof(t),t,8,VK_QUERY_RESULT_64_BIT));return {double((t[1]-t[0])&timestamp_mask)*ms_per_tick,double((t[3]-t[2])&timestamp_mask)*ms_per_tick,double((t[5]-t[4])&timestamp_mask)*ms_per_tick,wall};
    }
};
void stats(const char* key,std::vector<double> v){std::sort(v.begin(),v.end());auto p=[&](double q){return v[std::min(v.size()-1,size_t(std::ceil(q*v.size())-1))];};std::printf("\"%s\":{\"p50\":%.6f,\"p95\":%.6f,\"max\":%.6f}",key,p(.5),p(.95),v.back());}
}
int main(int argc,char** argv){
    try{
        if(argc==2&&std::string(argv[1])=="--fixture-check"){
#ifdef TEXTURE_BCDEC_HEADER
            for(unsigned seed=0;seed<128;++seed)(void)fixture(true,seed);
            std::puts("{\"independent_bcdec_reference\":true,\"frames\":128,\"blocks\":2949120,\"pass\":true}");return 0;
#else
            throw std::runtime_error("fixture-check requires compilation with TEXTURE_BCDEC_HEADER naming independent bcdec.h");
#endif
        }
        if(argc<2||argc>5)throw std::runtime_error("usage: texture_sample_bench shader.spv [device_index] [frames] [--manual-srgb-only]");
        bool manual=argc==5;if(manual&&std::string(argv[4])!="--manual-srgb-only")throw std::runtime_error("unknown experiment flag");
        unsigned selected=argc>2?std::stoul(argv[2]):0,frames=argc>3?std::stoul(argv[3]):30;if(frames<5||frames>300)throw std::runtime_error("frames must be 5..300");
        struct Format{const char* name;VkFormat vk;bool compressed,srgb;};
        std::vector<Format> formats={{"RGBA8_UNORM",VK_FORMAT_R8G8B8A8_UNORM,false,false},{"RGBA8_SRGB",VK_FORMAT_R8G8B8A8_SRGB,false,true},{"BC7_UNORM",VK_FORMAT_BC7_UNORM_BLOCK,true,false},{"BC7_SRGB",VK_FORMAT_BC7_SRGB_BLOCK,true,true}};
        if(manual)formats={{"RGBA8_UNORM_MANUAL_SRGB",VK_FORMAT_R8G8B8A8_UNORM,false,true},{"BC7_UNORM_MANUAL_SRGB",VK_FORMAT_BC7_UNORM_BLOCK,true,true}};
        std::printf("{\"schema\":3,\"scope\":\"synthetic texture upload/sample/readback; not encoder or display latency\",\"srgb_experiment\":\"%s\",\"width\":%u,\"height\":%u,\"warmup_frames_per_case\":%u,\"reference_tolerance\":0.0021,\"cases\":[\n",manual?"manual_EOTF_before_filtering":"hardware_sampling",W,H,warmups);bool first_case=true,all_pass=true;
        for(auto f:formats){Context c;bool supported=c.init(f.vk,f.compressed,argv[1],selected);bool first_image=true;
            for(unsigned filter=0;filter<2;++filter){if(!first_case)std::printf(",\n");first_case=false;
                std::printf("{\"format\":\"%s\",\"filter\":\"%s\",\"device\":\"%s\",\"supported\":%s",f.name,filter?"linear_quarter_texel":"nearest_center",c.props.deviceName,supported?"true":"false");
                if(!supported){std::printf("}");continue;}c.descriptor(filter);std::array<std::vector<double>,4> times;Validation worst;
                for(unsigned i=0;i<frames+warmups;++i){auto bytes=fixture(f.compressed,i);auto t=c.run(bytes,first_image,filter,manual);first_image=false;
                    // Verification outside wall timing, every frame including warm-up.
                    auto result=verify(static_cast<const float*>(c.readback.mapped),i,f.srgb,filter);if(result.error>worst.error)worst=result;
                    if(i>=warmups)for(unsigned j=0;j<4;++j)times[j].push_back(t[j]);}
                bool passed=worst.error<=0.0021f;all_pass&=passed;
                std::printf(",\"frames\":%u,\"reference_pass\":%s,\"max_abs_linear_error\":%.8f,\"worst_sample\":{\"seed\":%u,\"x\":%u,\"y\":%u,\"channel\":%u,\"actual\":%.9f,\"expected\":%.9f,\"source_codes_00_10_01_11\":[%u,%u,%u,%u]},\"payload_bytes\":%u,",frames,passed?"true":"false",worst.error,worst.seed,worst.x,worst.y,worst.channel,worst.actual,worst.expected,worst.source_codes[0],worst.source_codes[1],worst.source_codes[2],worst.source_codes[3],W*H*(f.compressed?1:4));
                if(!passed)std::fprintf(stderr,"reference failure: format=%s filter=%u seed=%u x=%u y=%u channel=%u actual=%.9f expected=%.9f error=%.9f source=[%u,%u,%u,%u]\n",f.name,filter,worst.seed,worst.x,worst.y,worst.channel,worst.actual,worst.expected,worst.error,worst.source_codes[0],worst.source_codes[1],worst.source_codes[2],worst.source_codes[3]);
                const char* keys[]={"upload_gpu_ms","sample_gpu_ms","readback_gpu_ms","upload_sample_readback_wall_ms"};for(unsigned j=0;j<4;++j){if(j)std::printf(",");stats(keys[j],times[j]);}std::printf("}");std::fflush(stdout);
            }
        }std::printf("\n],\"all_supported_cases_reference_pass\":%s}\n",all_pass?"true":"false");return all_pass?0:1;
    }catch(const std::exception& e){std::fprintf(stderr,"texture benchmark failed: %s\n",e.what());return 1;}
}
