// SPDX-License-Identifier: MIT
// Read-only capability inventory; no performance or hardware identity inference.
#include <vulkan/vulkan.h>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <vector>
#include <set>
#include <stdexcept>
static void ok(VkResult r){if(r!=VK_SUCCESS)throw std::runtime_error("Vulkan result "+std::to_string(r));}
static std::string quote(const char* s){std::ostringstream o;o<<'"';for(const unsigned char* p=reinterpret_cast<const unsigned char*>(s);*p;p++){if(*p=='"'||*p=='\\')o<<'\\'<<*p;else if(*p<32)o<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<unsigned(*p);else o<<*p;}o<<'"';return o.str();}
struct Instance{VkInstance value{};~Instance(){if(value)vkDestroyInstance(value,nullptr);}};
int main()try{
    Instance instance;VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};app.apiVersion=VK_API_VERSION_1_1;
    VkInstanceCreateInfo ci{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};ci.pApplicationInfo=&app;ok(vkCreateInstance(&ci,nullptr,&instance.value));
    uint32_t n=0;ok(vkEnumeratePhysicalDevices(instance.value,&n,nullptr));std::vector<VkPhysicalDevice> devices(n);ok(vkEnumeratePhysicalDevices(instance.value,&n,devices.data()));
    std::cout<<"{\"schema\":1,\"devices\":[";
    for(unsigned d=0;d<n;d++){
        if(d)std::cout<<',';auto gpu=devices[d];VkPhysicalDeviceProperties props;vkGetPhysicalDeviceProperties(gpu,&props);
        uint32_t ec=0;ok(vkEnumerateDeviceExtensionProperties(gpu,nullptr,&ec,nullptr));std::vector<VkExtensionProperties> extensions(ec);ok(vkEnumerateDeviceExtensionProperties(gpu,nullptr,&ec,extensions.data()));
        std::set<std::string> names;for(auto e:extensions)names.insert(e.extensionName);
        bool api11=props.apiVersion>=VK_API_VERSION_1_1,api12=props.apiVersion>=VK_API_VERSION_1_2,api13=props.apiVersion>=VK_API_VERSION_1_3;
        VkPhysicalDeviceSubgroupProperties subgroup{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_PROPERTIES};
        VkPhysicalDeviceSubgroupSizeControlPropertiesEXT subgroup_control{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_SIZE_CONTROL_PROPERTIES_EXT};
        VkPhysicalDeviceProperties2 p2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};p2.pNext=&subgroup;
        bool size_control=api13||names.count("VK_EXT_subgroup_size_control");if(size_control)subgroup.pNext=&subgroup_control;
        if(api11)vkGetPhysicalDeviceProperties2(gpu,&p2);
        VkPhysicalDeviceFeatures2 features{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
        VkPhysicalDeviceShaderFloat16Int8Features f16{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_FLOAT16_INT8_FEATURES};
        VkPhysicalDevice8BitStorageFeatures storage8{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_8BIT_STORAGE_FEATURES};
        VkPhysicalDeviceSubgroupSizeControlFeaturesEXT control{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_SIZE_CONTROL_FEATURES_EXT};
        VkPhysicalDeviceTimelineSemaphoreFeatures timeline{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TIMELINE_SEMAPHORE_FEATURES};
        void** next=&features.pNext;
        if(api12||names.count("VK_KHR_shader_float16_int8")){*next=&f16;next=&f16.pNext;}
        if(api12||names.count("VK_KHR_8bit_storage")){*next=&storage8;next=&storage8.pNext;}
        if(size_control){*next=&control;next=&control.pNext;}
        if(api12||names.count("VK_KHR_timeline_semaphore")){*next=&timeline;next=&timeline.pNext;}
        if(api11)vkGetPhysicalDeviceFeatures2(gpu,&features);else vkGetPhysicalDeviceFeatures(gpu,&features.features);
        const auto& l=props.limits;
        std::cout<<"{\"index\":"<<d<<",\"name\":"<<quote(props.deviceName)<<",\"vendor_id\":"<<props.vendorID<<",\"device_id\":"<<props.deviceID
                 <<",\"device_type\":"<<props.deviceType<<",\"api_version\":"<<props.apiVersion<<",\"driver_version_raw\":"<<props.driverVersion
                 <<",\"limits\":{\"shared_bytes\":"<<l.maxComputeSharedMemorySize<<",\"workgroup_invocations\":"<<l.maxComputeWorkGroupInvocations
                 <<",\"workgroup_x\":"<<l.maxComputeWorkGroupSize[0]<<",\"dispatch_x\":"<<l.maxComputeWorkGroupCount[0]<<",\"storage_buffer_range\":"<<l.maxStorageBufferRange
                 <<",\"storage_buffers_per_stage\":"<<l.maxPerStageDescriptorStorageBuffers<<",\"storage_buffers_per_set\":"<<l.maxDescriptorSetStorageBuffers
                 <<",\"image_dimension_2d\":"<<l.maxImageDimension2D<<",\"timestamp_period_ns\":"<<l.timestampPeriod<<"}"
                 <<",\"features\":{\"int16\":"<<features.features.shaderInt16<<",\"float16\":"<<f16.shaderFloat16<<",\"int8\":"<<f16.shaderInt8
                 <<",\"storage_buffer_8bit\":"<<storage8.storageBuffer8BitAccess<<",\"timeline_semaphore\":"<<timeline.timelineSemaphore
                 <<",\"subgroup_size_control\":"<<control.subgroupSizeControl<<",\"compute_full_subgroups\":"<<control.computeFullSubgroups<<"}"
                 <<",\"subgroup\":{\"size\":"<<subgroup.subgroupSize<<",\"stages_mask\":"<<subgroup.supportedStages<<",\"operations_mask\":"<<subgroup.supportedOperations
                 <<",\"min_size\":"<<subgroup_control.minSubgroupSize<<",\"max_size\":"<<subgroup_control.maxSubgroupSize<<",\"required_size_stages_mask\":"<<subgroup_control.requiredSubgroupSizeStages<<"}";
        VkPhysicalDeviceMemoryProperties memory;vkGetPhysicalDeviceMemoryProperties(gpu,&memory);std::cout<<",\"heaps\":[";
        for(unsigned i=0;i<memory.memoryHeapCount;i++){if(i)std::cout<<',';std::cout<<"{\"bytes\":"<<memory.memoryHeaps[i].size<<",\"flags\":"<<memory.memoryHeaps[i].flags<<"}";}std::cout<<"],\"memory_types\":[";
        for(unsigned i=0;i<memory.memoryTypeCount;i++){if(i)std::cout<<',';std::cout<<"{\"heap\":"<<memory.memoryTypes[i].heapIndex<<",\"flags\":"<<memory.memoryTypes[i].propertyFlags<<"}";}std::cout<<"],\"queues\":[";
        uint32_t qc=0;vkGetPhysicalDeviceQueueFamilyProperties(gpu,&qc,nullptr);std::vector<VkQueueFamilyProperties> queues(qc);vkGetPhysicalDeviceQueueFamilyProperties(gpu,&qc,queues.data());
        for(unsigned i=0;i<qc;i++){if(i)std::cout<<',';std::cout<<"{\"flags\":"<<queues[i].queueFlags<<",\"count\":"<<queues[i].queueCount<<",\"timestamp_valid_bits\":"<<queues[i].timestampValidBits<<"}";}std::cout<<"],\"formats\":[";
        bool first=true;for(auto format:{VK_FORMAT_R8_UNORM,VK_FORMAT_R16_SFLOAT,VK_FORMAT_R8G8B8A8_UNORM,VK_FORMAT_G8_B8_R8_3PLANE_444_UNORM,VK_FORMAT_G8_B8R8_2PLANE_420_UNORM}){
            if(!first)std::cout<<',';first=false;VkFormatProperties fp;vkGetPhysicalDeviceFormatProperties(gpu,format,&fp);
            std::cout<<"{\"format\":"<<format<<",\"optimal_features\":"<<fp.optimalTilingFeatures<<",\"linear_features\":"<<fp.linearTilingFeatures<<"}";
        }
        std::cout<<"],\"extensions\":[";first=true;for(auto& name:names){if(!first)std::cout<<',';first=false;std::cout<<quote(name.c_str());}std::cout<<"]}";
    }
    std::cout<<"]}\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
