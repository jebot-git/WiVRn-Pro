// SPDX-License-Identifier: MIT
#include "direct.hpp"
#include "codec.hpp"
#include <vulkan/vulkan.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <stdexcept>
using Clock=std::chrono::steady_clock;
static double elapsed(Clock::time_point start){return std::chrono::duration<double,std::milli>(Clock::now()-start).count();}
static void require(bool ok,const char* s){if(!ok)throw std::runtime_error(s);}
static void check(VkResult r){if(r!=VK_SUCCESS)throw std::runtime_error("Vulkan result "+std::to_string(r));}
static std::string quote(const std::string& s){std::string r="\"";for(char c:s){if(c=='\\'||c=='\"')r+='\\';r+=c;}return r+'\"';}
static void distribution(const char* key,std::vector<double> v){std::sort(v.begin(),v.end());std::cout<<",\""<<key<<"\":{\"p50\":"<<v[(v.size()-1)/2]<<",\"p95\":"<<v[(v.size()*95+99)/100-1]<<",\"max\":"<<v.back()<<",\"samples\":[";for(size_t i=0;i<v.size();++i)std::cout<<(i?",":"")<<v[i];std::cout<<"]}";}
static std::vector<uint8_t> read(const std::filesystem::path& p){size_t n=std::filesystem::file_size(p);require(n<=16*1024*1024,"file size");std::vector<uint8_t> v(n);std::ifstream f(p,std::ios::binary);f.read(reinterpret_cast<char*>(v.data()),n);require(bool(f),"file read");return v;}
// Same deterministic generated fixtures as the immutable first decode probe.
struct Sample {std::string name;std::vector<uint8_t> wire;};
static std::vector<Sample> generated(){
 std::vector<Sample> samples;const std::pair<unsigned,unsigned> dims[]={{1,1},{1,17},{17,1},{15,33},{17,19},{31,31},{32,32},{65,47},{576,640}};
 for(auto [w,h]:dims)for(unsigned kind=0;kind<4;++kind){std::vector<uint32_t> pixels(size_t(w)*h);unsigned seed=1234567;for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){seed=1664525u*seed+1013904223u;unsigned tile=(y/16)*((w+15)/16)+x/16;uint32_t p=0xff000000u|((tile*0x152b49u)&0xffffffu);if(kind==1&&tile%2)p=0xff000000u|(seed&0xffffffu);if(kind==2&&x%16==3&&y%16==4)p^=1u;if(kind==3)p=0xff000000u|(((x+y)&1)?0xffffffu:0);pixels[size_t(y)*w+x]=p;}
 std::vector<uint32_t> map(((w+15)/16)*((h+15)/16));for(size_t i=0;i<map.size();++i)map[i]=std::array<unsigned,4>{1,8,17,1024}[i%4];samples.push_back({"edge/"+std::to_string(w)+"x"+std::to_string(h)+"-kind"+std::to_string(kind),vr444::pack(vr444::encode(pixels,w,h,1,map))});}
 for(unsigned active=0;active<48;++active){auto f=vr444::encode(std::vector<uint32_t>(256,0xff808080u),16,16,1);std::fill(f.slots.begin(),f.slots.end(),0);unsigned bit=0;auto put=[&](unsigned value,unsigned n){for(unsigned i=0;i<n;++i,++bit)f.slots[bit/32]|=((value>>i)&1u)<<(bit%32);};for(unsigned c=0;c<3;++c){put(c==0?256:0,11);for(unsigned group=0;group<16;++group){bool on=c*16+group==active;put(on,1);if(on){put(1,4);put(0,2);put(1,1);}}}f.lengths[0]=(bit+31)/32;vr444::validate(f);samples.push_back({"edge/three-word-nonconstant-ac"+std::to_string(active),vr444::pack(f)});}
 return samples;
}

struct Owner {
 VkInstance instance{};VkDevice device{};VkImage image{};VkDeviceMemory image_memory{};
 VkCommandPool pool{};VkFence fence{};std::array<VkBuffer,2> buffers{};
 std::array<VkDeviceMemory,2> memory{};std::array<void*,2> maps{};
 ~Owner(){if(device){vkDeviceWaitIdle(device);if(fence)vkDestroyFence(device,fence,nullptr);if(pool)vkDestroyCommandPool(device,pool,nullptr);if(image)vkDestroyImage(device,image,nullptr);if(image_memory)vkFreeMemory(device,image_memory,nullptr);for(unsigned i=0;i<2;++i){if(maps[i])vkUnmapMemory(device,memory[i]);if(buffers[i])vkDestroyBuffer(device,buffers[i],nullptr);if(memory[i])vkFreeMemory(device,memory[i],nullptr);}vkDestroyDevice(device,nullptr);}if(instance)vkDestroyInstance(instance,nullptr);}
};
struct Upload {
 Owner o;VkQueue queue{};VkCommandBuffer cmd{};VkPhysicalDeviceProperties props{};
 unsigned width,height;size_t bytes;VkImageLayout layout=VK_IMAGE_LAYOUT_UNDEFINED;
 std::array<unsigned,2> buffer_memory_flags{};unsigned image_memory_flags{};VkPhysicalDeviceMemoryProperties memory_properties{};std::array<unsigned,2> buffer_memory_types{};bool failed=false;
 Upload(unsigned w,unsigned h):width(w),height(h),bytes(size_t(w)*h*4){
  VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};app.pApplicationName="VR444 direct upload oracle";app.apiVersion=VK_API_VERSION_1_1;
  VkInstanceCreateInfo ci{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};ci.pApplicationInfo=&app;check(vkCreateInstance(&ci,nullptr,&o.instance));
  uint32_t count=0;check(vkEnumeratePhysicalDevices(o.instance,&count,nullptr));require(count>0,"no Vulkan device");std::vector<VkPhysicalDevice> devices(count);check(vkEnumeratePhysicalDevices(o.instance,&count,devices.data()));auto physical=devices[0];vkGetPhysicalDeviceProperties(physical,&props);require(props.deviceType!=VK_PHYSICAL_DEVICE_TYPE_CPU,"software Vulkan forbidden");require(props.apiVersion>=VK_API_VERSION_1_1,"Vulkan 1.1 required");
  VkFormatProperties format{};vkGetPhysicalDeviceFormatProperties(physical,VK_FORMAT_R8G8B8A8_SRGB,&format);auto features=VK_FORMAT_FEATURE_TRANSFER_SRC_BIT|VK_FORMAT_FEATURE_TRANSFER_DST_BIT|VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT;require((format.optimalTilingFeatures&features)==unsigned(features),"SRGB optimal transfer/sample unsupported");
  uint32_t n=0;vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,nullptr);std::vector<VkQueueFamilyProperties> qs(n);vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,qs.data());unsigned family=0;while(family<n&&!(qs[family].queueFlags&VK_QUEUE_GRAPHICS_BIT))++family;require(family<n,"no graphics queue");
  float priority=1;VkDeviceQueueCreateInfo qi{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};qi.queueFamilyIndex=family;qi.queueCount=1;qi.pQueuePriorities=&priority;VkDeviceCreateInfo di{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};di.queueCreateInfoCount=1;di.pQueueCreateInfos=&qi;check(vkCreateDevice(physical,&di,nullptr,&o.device));vkGetDeviceQueue(o.device,family,0,&queue);
  VkPhysicalDeviceMemoryProperties mp{};vkGetPhysicalDeviceMemoryProperties(physical,&mp);memory_properties=mp;
  auto choose=[&](uint32_t mask,VkMemoryPropertyFlags wanted){for(unsigned i=0;i<mp.memoryTypeCount;++i)if((mask&(1u<<i))&&(mp.memoryTypes[i].propertyFlags&wanted)==wanted)return i;throw std::runtime_error("memory type unavailable");};
  for(unsigned i=0;i<2;++i){VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};bi.size=bytes;bi.usage=VK_BUFFER_USAGE_TRANSFER_SRC_BIT|VK_BUFFER_USAGE_TRANSFER_DST_BIT;bi.sharingMode=VK_SHARING_MODE_EXCLUSIVE;check(vkCreateBuffer(o.device,&bi,nullptr,&o.buffers[i]));VkMemoryRequirements req{};vkGetBufferMemoryRequirements(o.device,o.buffers[i],&req);unsigned type=choose(req.memoryTypeBits,VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);buffer_memory_types[i]=type;buffer_memory_flags[i]=mp.memoryTypes[type].propertyFlags;VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};ai.allocationSize=req.size;ai.memoryTypeIndex=type;check(vkAllocateMemory(o.device,&ai,nullptr,&o.memory[i]));check(vkBindBufferMemory(o.device,o.buffers[i],o.memory[i],0));check(vkMapMemory(o.device,o.memory[i],0,VK_WHOLE_SIZE,0,&o.maps[i]));}
  VkImageCreateInfo ii{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};ii.imageType=VK_IMAGE_TYPE_2D;ii.format=VK_FORMAT_R8G8B8A8_SRGB;ii.extent={width,height,1};ii.mipLevels=1;ii.arrayLayers=1;ii.samples=VK_SAMPLE_COUNT_1_BIT;ii.tiling=VK_IMAGE_TILING_OPTIMAL;ii.usage=VK_IMAGE_USAGE_TRANSFER_DST_BIT|VK_IMAGE_USAGE_TRANSFER_SRC_BIT|VK_IMAGE_USAGE_SAMPLED_BIT;ii.sharingMode=VK_SHARING_MODE_EXCLUSIVE;check(vkCreateImage(o.device,&ii,nullptr,&o.image));VkMemoryRequirements req{};vkGetImageMemoryRequirements(o.device,o.image,&req);unsigned type=choose(req.memoryTypeBits,VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);image_memory_flags=mp.memoryTypes[type].propertyFlags;VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};ai.allocationSize=req.size;ai.memoryTypeIndex=type;check(vkAllocateMemory(o.device,&ai,nullptr,&o.image_memory));check(vkBindImageMemory(o.device,o.image,o.image_memory,0));
  VkCommandPoolCreateInfo pi{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};pi.flags=VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;pi.queueFamilyIndex=family;check(vkCreateCommandPool(o.device,&pi,nullptr,&o.pool));VkCommandBufferAllocateInfo ca{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};ca.commandPool=o.pool;ca.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY;ca.commandBufferCount=1;check(vkAllocateCommandBuffers(o.device,&ca,&cmd));VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};check(vkCreateFence(o.device,&fi,nullptr,&o.fence));
 }
 void begin(){require(!failed,"poisoned Vulkan session");check(vkResetCommandBuffer(cmd,0));VkCommandBufferBeginInfo b{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};b.flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;check(vkBeginCommandBuffer(cmd,&b));}
 void barrier(VkImageLayout next,VkAccessFlags src,VkAccessFlags dst,VkPipelineStageFlags a,VkPipelineStageFlags b){VkImageMemoryBarrier ib{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};ib.srcAccessMask=src;ib.dstAccessMask=dst;ib.oldLayout=layout;ib.newLayout=next;ib.srcQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED;ib.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED;ib.image=o.image;ib.subresourceRange={VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1};vkCmdPipelineBarrier(cmd,a,b,0,0,nullptr,0,nullptr,1,&ib);layout=next;}
 double submit(){check(vkEndCommandBuffer(cmd));check(vkResetFences(o.device,1,&o.fence));VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};si.commandBufferCount=1;si.pCommandBuffers=&cmd;auto start=Clock::now();failed=true;check(vkQueueSubmit(queue,1,&si,o.fence));check(vkWaitForFences(o.device,1,&o.fence,VK_TRUE,1'000'000'000));failed=false;return elapsed(start);}
 void poison_image(){begin();barrier(VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,layout==VK_IMAGE_LAYOUT_UNDEFINED?0:VK_ACCESS_MEMORY_READ_BIT,VK_ACCESS_TRANSFER_WRITE_BIT,VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT);VkClearColorValue clear{};VkImageSubresourceRange range{VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1};vkCmdClearColorImage(cmd,o.image,layout,&clear,1,&range);barrier(VK_IMAGE_LAYOUT_GENERAL,VK_ACCESS_TRANSFER_WRITE_BIT,VK_ACCESS_SHADER_READ_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_ALL_COMMANDS_BIT);submit();}
 VkBufferImageCopy region(){VkBufferImageCopy r{};r.imageSubresource={VK_IMAGE_ASPECT_COLOR_BIT,0,0,1};r.imageExtent={width,height,1};return r;}
 double upload(){begin();barrier(VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,VK_ACCESS_MEMORY_READ_BIT,VK_ACCESS_TRANSFER_WRITE_BIT,VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT);auto r=region();vkCmdCopyBufferToImage(cmd,o.buffers[0],o.image,layout,1,&r);barrier(VK_IMAGE_LAYOUT_GENERAL,VK_ACCESS_TRANSFER_WRITE_BIT,VK_ACCESS_SHADER_READ_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_ALL_COMMANDS_BIT);return submit();}
 double readback(){begin();VkMemoryBarrier host{VK_STRUCTURE_TYPE_MEMORY_BARRIER};host.srcAccessMask=VK_ACCESS_HOST_WRITE_BIT;host.dstAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT;vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_HOST_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,0,1,&host,0,nullptr,0,nullptr);barrier(VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,VK_ACCESS_MEMORY_READ_BIT|VK_ACCESS_MEMORY_WRITE_BIT,VK_ACCESS_TRANSFER_READ_BIT,VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT);auto r=region();vkCmdCopyImageToBuffer(cmd,o.image,layout,o.buffers[1],1,&r);barrier(VK_IMAGE_LAYOUT_GENERAL,VK_ACCESS_TRANSFER_READ_BIT,VK_ACCESS_SHADER_READ_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_ALL_COMMANDS_BIT);VkMemoryBarrier mb{VK_STRUCTURE_TYPE_MEMORY_BARRIER};mb.srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT;mb.dstAccessMask=VK_ACCESS_HOST_READ_BIT;vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&mb,0,nullptr,0,nullptr);return submit();}
};

int main(int argc,char** argv){try{
 require(argc==3,"usage: mapped-probe CORPUS REPS");unsigned reps=std::stoul(argv[2]);require(reps>=1&&reps<=100,"reps bound");
 std::vector<Sample> samples;std::vector<std::filesystem::path> paths;
 for(auto& e:std::filesystem::recursive_directory_iterator(argv[1]))if(e.is_regular_file()&&e.path().extension()==".payload")paths.push_back(e.path());
 std::sort(paths.begin(),paths.end());require(paths.size()<=512,"corpus count bound");size_t total=0;
 for(auto& path:paths){auto bytes=read(path);total+=bytes.size();require(total<=64*1024*1024,"corpus bound");samples.push_back({std::filesystem::relative(path,argv[1]).string(),std::move(bytes)});}
 auto more=generated();samples.insert(samples.end(),std::make_move_iterator(more.begin()),std::make_move_iterator(more.end()));
 std::map<std::pair<unsigned,unsigned>,std::unique_ptr<Upload>> contexts;
 std::cout<<"{\"schema\":1,\"vector_backend\":"<<quote(direct::vector_backend())<<",\"format\":\"VK_FORMAT_R8G8B8A8_SRGB\",\"optimal_tiling\":true,\"validation_extra_transfer_src_usage\":true,\"warmups\":3,\"repetitions\":"<<reps<<",\"order\":\"first=(rep+case_index)%2; 0=cached_copy,1=mapped_direct\",\"cases\":["<<std::flush;
 unsigned case_number=0;
 const char* arm_names[]={"cached_copy","mapped_direct"};
 const char* keys[]={"cpu_decode_ms","staging_copy_ms","upload_submit_fence_ms","decode_through_upload_ms","readback_submit_fence_ms","readback_record_submit_fence_ms"};
 for(auto& sample:samples){
  auto shape=direct::preflight(sample.wire);auto oracle=vr444::decode(vr444::unpack(sample.wire));std::vector<uint32_t> pixels(oracle.size());
  auto key=std::make_pair(shape.width,shape.height);if(!contexts[key])contexts[key]=std::make_unique<Upload>(shape.width,shape.height);auto& u=*contexts[key];
  require(reinterpret_cast<uintptr_t>(u.o.maps[0])%alignof(uint32_t)==0,"mapped output alignment");
  std::array<std::array<std::vector<double>,6>,2> timing;direct::Stats stats{};
  for(unsigned rep=0;rep<reps+3;++rep)for(unsigned j=0;j<2;++j){
   unsigned arm=(rep+case_number+j)%2;
   // Both arms start from the same poisoned image/staging/readback state.
   u.poison_image();std::fill(pixels.begin(),pixels.end(),0x5a5a5a5a);
   std::memset(u.o.maps[0],0x5a,u.bytes);std::memset(u.o.maps[1],0x5a,u.bytes);
   auto output=arm?std::span(static_cast<uint32_t*>(u.o.maps[0]),oracle.size()):std::span(pixels);
   auto whole=Clock::now();auto start=Clock::now();stats=direct::decode(sample.wire,shape,output,true);double decode=elapsed(start);
   double copy=0;
   if(!arm){start=Clock::now();std::memcpy(u.o.maps[0],pixels.data(),u.bytes);copy=elapsed(start);}
   double upload=u.upload();double pipeline=elapsed(whole);
   // Host comparison comes after the timed upload for both arms. It is not
   // allowed to introduce mapped reads between decode and GPU submission.
   require(std::memcmp(output.data(),oracle.data(),u.bytes)==0,"decoded RGBA oracle mismatch");
   start=Clock::now();double read_fence=u.readback();double read_total=elapsed(start);
   require(std::memcmp(u.o.maps[1],oracle.data(),u.bytes)==0,"optimal SRGB image byte mismatch");
   if(rep>=3){std::array<double,6> values{decode,copy,upload,pipeline,read_fence,read_total};for(unsigned k=0;k<6;++k)timing[arm][k].push_back(values[k]);}
  }
  std::cout<<(case_number?",":"")<<"{\"name\":"<<quote(sample.name)<<",\"width\":"<<shape.width<<",\"height\":"<<shape.height<<",\"wire_bytes\":"<<sample.wire.size()<<",\"rgba_bytes\":"<<u.bytes<<",\"constant_tiles\":"<<stats.constant_tiles<<",\"mixed_tiles\":"<<stats.mixed_tiles<<",\"device\":"<<quote(u.props.deviceName)<<",\"device_type\":"<<u.props.deviceType<<",\"staging_memory_flags\":"<<u.buffer_memory_flags[0]<<",\"staging_memory_type\":"<<u.buffer_memory_types[0]<<",\"readback_memory_flags\":"<<u.buffer_memory_flags[1]<<",\"image_memory_flags\":"<<u.image_memory_flags<<",\"memory_types\":[";
  for(unsigned i=0;i<u.memory_properties.memoryTypeCount;++i){auto t=u.memory_properties.memoryTypes[i];std::cout<<(i?",":"")<<"{\"index\":"<<i<<",\"flags\":"<<t.propertyFlags<<",\"heap\":"<<t.heapIndex<<",\"heap_bytes\":"<<u.memory_properties.memoryHeaps[t.heapIndex].size<<"}";}
  std::cout<<"],\"arms\":{";
  for(unsigned arm=0;arm<2;++arm){std::cout<<(arm?",":"")<<quote(arm_names[arm])<<":{\"cpu_exact\":true,\"image_readback_exact\":true";for(unsigned k=0;k<6;++k)distribution(keys[k],timing[arm][k]);std::cout<<"}";}
  // Chronological paired samples preserve the per-repetition A/B association.
  std::cout<<"},\"paired_iterations\":[";
  for(unsigned i=0;i<reps;++i){std::cout<<(i?",":"")<<"{\"measured_rep\":"<<i<<",\"first_arm\":"<<quote(arm_names[(i+3+case_number)%2]);for(unsigned arm=0;arm<2;++arm){std::cout<<","<<quote(arm_names[arm])<<":{";for(unsigned k=0;k<6;++k)std::cout<<(k?",":"")<<quote(keys[k])<<":"<<timing[arm][k][i];std::cout<<"}";}std::cout<<"}";}
  std::cout<<"]}"<<std::flush;++case_number;
 }
 std::cout<<"],\"pass\":true}\n";
 }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
