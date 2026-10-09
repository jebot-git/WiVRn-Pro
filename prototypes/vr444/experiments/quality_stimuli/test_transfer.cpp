// SPDX-License-Identifier: MIT
// Exercises the exact application's upload/readback code on private VkImages.
// No OpenXR calls, runtime, sockets or headset are used by this test.
#define main quality_application_main
#include "app.cpp"
#undef main
struct PrivateImages : App {
    std::array<VkImage,2> owned{};std::array<VkDeviceMemory,2> image_memory{};
    PrivateImages(const fs::path& output,unsigned gpu,uint32_t width,uint32_t height,const std::string& name):App(Options{name,output,width,height,1,1,false}) {
        VkApplicationInfo ai{VK_STRUCTURE_TYPE_APPLICATION_INFO};ai.apiVersion=VK_API_VERSION_1_0;
        VkInstanceCreateInfo ci{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};ci.pApplicationInfo=&ai;VC(vkCreateInstance(&ci,nullptr,&instance));
        uint32_t n=0;VC(vkEnumeratePhysicalDevices(instance,&n,nullptr));require(n&&n<=32,"device count bound");std::vector<VkPhysicalDevice> devices(n);VC(vkEnumeratePhysicalDevices(instance,&n,devices.data()));require(gpu<n,"requested GPU missing");physical=devices[gpu];
        VkPhysicalDeviceProperties prop{};vkGetPhysicalDeviceProperties(physical,&prop);
        std::cout<<json({{"device",prop.deviceName},{"device_id",prop.deviceID},{"vendor_id",prop.vendorID},{"width",width},{"height",height},{"pattern",name},{"openxr_exercised",false}}).dump()<<'\n';
        vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,nullptr);std::vector<VkQueueFamilyProperties> families(n);vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,families.data());family=n;for(uint32_t i=0;i<n;++i)if(families[i].queueCount&&(families[i].queueFlags&VK_QUEUE_GRAPHICS_BIT)){family=i;break;}require(family<n,"graphics queue missing");
        float priority=1;VkDeviceQueueCreateInfo qi{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};qi.queueFamilyIndex=family;qi.queueCount=1;qi.pQueuePriorities=&priority;VkDeviceCreateInfo dc{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};dc.queueCreateInfoCount=1;dc.pQueueCreateInfos=&qi;VC(vkCreateDevice(physical,&dc,nullptr,&device));vkGetDeviceQueue(device,family,0,&queue);
        VkCommandPoolCreateInfo pc{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};pc.queueFamilyIndex=family;pc.flags=VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;VC(vkCreateCommandPool(device,&pc,nullptr,&pool));VkCommandBufferAllocateInfo ba{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};ba.commandPool=pool;ba.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY;ba.commandBufferCount=1;VC(vkAllocateCommandBuffers(device,&ba,&command));VkFenceCreateInfo fc{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};VC(vkCreateFence(device,&fc,nullptr,&fence));
        VkPhysicalDeviceMemoryProperties mp{};vkGetPhysicalDeviceMemoryProperties(physical,&mp);
        for(unsigned i=0;i<2;++i) {
            auto&e=eyes[i];e.width=width;e.height=height;e.source=quality_stimuli::pattern(name,width,height,i);e.size=e.source.size();e.sha=digest(e.source);init_buffer(e);
            VkImageCreateInfo ic{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};ic.imageType=VK_IMAGE_TYPE_2D;ic.format=VK_FORMAT_R8G8B8A8_SRGB;ic.extent={width,height,1};ic.mipLevels=1;ic.arrayLayers=1;ic.samples=VK_SAMPLE_COUNT_1_BIT;ic.tiling=VK_IMAGE_TILING_OPTIMAL;ic.usage=VK_IMAGE_USAGE_TRANSFER_SRC_BIT|VK_IMAGE_USAGE_TRANSFER_DST_BIT|VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;ic.sharingMode=VK_SHARING_MODE_EXCLUSIVE;ic.initialLayout=VK_IMAGE_LAYOUT_UNDEFINED;VC(vkCreateImage(device,&ic,nullptr,&owned[i]));
            VkMemoryRequirements mr{};vkGetImageMemoryRequirements(device,owned[i],&mr);uint32_t type=mp.memoryTypeCount;for(uint32_t k=0;k<mp.memoryTypeCount;++k)if(mr.memoryTypeBits&(1u<<k)){type=k;break;}require(type<mp.memoryTypeCount,"image memory missing");VkMemoryAllocateInfo ma{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};ma.allocationSize=mr.size;ma.memoryTypeIndex=type;VC(vkAllocateMemory(device,&ma,nullptr,&image_memory[i]));VC(vkBindImageMemory(device,owned[i],image_memory[i],0));
            e.images.resize(1,{XR_TYPE_SWAPCHAIN_IMAGE_VULKAN_KHR});e.images[0].image=owned[i];e.captured.resize(1,false);
        }
        VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};VC(vkBeginCommandBuffer(command,&begin));
        for(auto image:owned){VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};b.oldLayout=VK_IMAGE_LAYOUT_UNDEFINED;b.newLayout=VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;b.dstAccessMask=VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;b.srcQueueFamilyIndex=b.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED;b.image=image;b.subresourceRange={VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1};vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,0,0,nullptr,0,nullptr,1,&b);}
        VC(vkEndCommandBuffer(command));VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};submit.commandBufferCount=1;submit.pCommandBuffers=&command;VC(vkQueueSubmit(queue,1,&submit,fence));VC(vkWaitForFences(device,1,&fence,VK_TRUE,1000000000ull));
    }
    ~PrivateImages(){drain_or_terminate(device);for(unsigned i=0;i<2;++i){if(owned[i])vkDestroyImage(device,owned[i],nullptr);if(image_memory[i])vkFreeMemory(device,image_memory[i],nullptr);}}
};
int main(int argc,char**argv) {
    try{
        require(argc==3,"usage: test-transfer NEW_ABSOLUTE_OUTPUT GPU_INDEX");fs::path output=argv[1];require(output.is_absolute()&&!fs::exists(output),"new absolute output required");require(fs::create_directory(output),"cannot create test output");const auto gpu=number(argv[2]);unsigned cases=0,rejections=0;
        for(auto [w,h]:std::array<std::pair<uint32_t,uint32_t>,7>{{{1,1},{1,129},{17,33},{129,65},{4096,1},{1,4096},{576,640}}})for(const std::string name:{"chroma-grid","rgb-ramp","fine-detail"}) {
            PrivateImages app(output/(std::to_string(cases)+"-"+name),gpu,w,h,name);
            app.transfer({0,0});app.transfer({0,0});
            // Reuse same private images with different known bytes: catching a
            // stale readback or omitted copy cannot depend on an initial zero.
            for(auto&e:app.eyes){for(size_t i=0;i<e.size;i+=4){e.source[i]^=0x5a;e.source[i+1]^=0x96;e.source[i+2]^=0x3c;}std::memcpy(e.mapped,e.source.data(),e.size);}
            app.transfer({0,0});
            app.eyes[1].mapped[0]^=1;bool rejected=false;try{app.transfer({0,0});}catch(const std::exception&){rejected=true;}require(rejected,"omitted/mutated right upload accepted");++rejections;++cases;
        }
        std::cout<<json({{"complete",true},{"cases",cases},{"exact_stereo_transfers",cases*3},{"wrong_eye_rejections",rejections},{"openxr_exercised",false},{"headset_exercised",false},{"performance_claim",false}}).dump()<<'\n';return 0;
    }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}
}
