// SPDX-License-Identifier: MIT
// Read-only host physical-device inventory. No logical device/queue is created.
#include <vulkan/vulkan.h>
#include <iostream>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>
void check(VkResult r) { if(r!=VK_SUCCESS) throw std::runtime_error("Vulkan result "+std::to_string(r)); }
const char* boolean(bool value) { return value?"true":"false"; }
std::string quote(const char* value) { std::string out="\""; for(;*value;++value) { if(*value=='"'||*value=='\\') out+='\\'; if(static_cast<unsigned char>(*value)<32) throw std::runtime_error("device name control character"); out+=*value; } return out+'"'; }
struct Owner { VkInstance instance{}; ~Owner(){if(instance) vkDestroyInstance(instance,nullptr);} };
int main() { try {
    Owner o; VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO}; app.apiVersion=VK_API_VERSION_1_1;
    VkInstanceCreateInfo ci{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO}; ci.pApplicationInfo=&app; check(vkCreateInstance(&ci,nullptr,&o.instance));
    uint32_t count=0; check(vkEnumeratePhysicalDevices(o.instance,&count,nullptr)); std::vector<VkPhysicalDevice> devices(count);
    check(vkEnumeratePhysicalDevices(o.instance,&count,devices.data()));
    std::cout<<"{\"schema\":1,\"physical_query_only\":true,\"logical_device_created\":false,\"scope\":\"host-only query; no headset access\",\"devices\":[";
    for(unsigned index=0;index<count;++index) {
        auto physical=devices[index]; VkPhysicalDeviceProperties p{}; vkGetPhysicalDeviceProperties(physical,&p);
        uint32_t n=0; check(vkEnumerateDeviceExtensionProperties(physical,nullptr,&n,nullptr)); std::vector<VkExtensionProperties> list(n);
        check(vkEnumerateDeviceExtensionProperties(physical,nullptr,&n,list.data())); std::set<std::string> extensions;
        for(const auto& e:list) extensions.emplace(e.extensionName);
        auto advertised=[&](const char* name){return extensions.count(name)!=0;};
        bool timeline_query=p.apiVersion>=VK_API_VERSION_1_2||advertised(VK_KHR_TIMELINE_SEMAPHORE_EXTENSION_NAME);
        bool sync_query=p.apiVersion>=VK_API_VERSION_1_3||advertised(VK_KHR_SYNCHRONIZATION_2_EXTENSION_NAME);
        // Extension only for host-copy: this binary targets Vulkan1.1 and makes
        // no claim about Vulkan1.4 promotion if the EXT name is not advertised.
        bool host_query=advertised(VK_EXT_HOST_IMAGE_COPY_EXTENSION_NAME);
        VkPhysicalDeviceFeatures2 f{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
        VkPhysicalDeviceTimelineSemaphoreFeatures timeline{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TIMELINE_SEMAPHORE_FEATURES};
        VkPhysicalDeviceSynchronization2Features sync{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SYNCHRONIZATION_2_FEATURES};
        VkPhysicalDeviceHostImageCopyFeaturesEXT host{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_IMAGE_COPY_FEATURES_EXT};
        void** next=&f.pNext;
        if(timeline_query) { *next=&timeline; next=&timeline.pNext; }
        if(sync_query) { *next=&sync; next=&sync.pNext; }
        if(host_query) { *next=&host; next=&host.pNext; }
        if(p.apiVersion>=VK_API_VERSION_1_1) vkGetPhysicalDeviceFeatures2(physical,&f);
        else { timeline_query=sync_query=host_query=false; }
        VkPhysicalDeviceTimelineSemaphoreProperties timeline_props{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TIMELINE_SEMAPHORE_PROPERTIES};
        VkPhysicalDeviceHostImageCopyPropertiesEXT host_props{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_IMAGE_COPY_PROPERTIES_EXT};
        VkPhysicalDeviceProperties2 props{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2}; next=&props.pNext;
        if(timeline_query) { *next=&timeline_props; next=&timeline_props.pNext; }
        if(host_query) { *next=&host_props; next=&host_props.pNext; }
        if(p.apiVersion>=VK_API_VERSION_1_1) vkGetPhysicalDeviceProperties2(physical,&props);
        std::vector<VkImageLayout> source_layouts(host_props.copySrcLayoutCount),destination_layouts(host_props.copyDstLayoutCount);
        if(host_query) {
            host_props.pCopySrcLayouts=source_layouts.data(); host_props.pCopyDstLayouts=destination_layouts.data();
            vkGetPhysicalDeviceProperties2(physical,&props);
            source_layouts.resize(host_props.copySrcLayoutCount); destination_layouts.resize(host_props.copyDstLayoutCount);
        }
        bool general_destination=false;
        for(auto layout:destination_layouts) general_destination|=layout==VK_IMAGE_LAYOUT_GENERAL;
        VkResult image_result=VK_ERROR_FORMAT_NOT_SUPPORTED;
        VkHostImageCopyDevicePerformanceQueryEXT performance{VK_STRUCTURE_TYPE_HOST_IMAGE_COPY_DEVICE_PERFORMANCE_QUERY_EXT};
        if(host_query&&host.hostImageCopy) {
            VkPhysicalDeviceImageFormatInfo2 info{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2};
            info.format=VK_FORMAT_R8G8B8A8_SRGB; info.type=VK_IMAGE_TYPE_2D; info.tiling=VK_IMAGE_TILING_OPTIMAL;
            info.usage=VK_IMAGE_USAGE_HOST_TRANSFER_BIT_EXT|VK_IMAGE_USAGE_SAMPLED_BIT;
            VkImageFormatProperties2 result{VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2}; result.pNext=&performance;
            image_result=vkGetPhysicalDeviceImageFormatProperties2(physical,&info,&result);
        }
        std::cout<<(index?",":"")<<"{\"index\":"<<index<<",\"name\":"<<quote(p.deviceName)<<",\"device_type\":"<<p.deviceType<<",\"api_version\":"<<p.apiVersion<<",\"driver_version\":"<<p.driverVersion;
        auto feature=[&](const char* name,bool present,bool queried,bool supported) {
            std::cout<<",\""<<name<<"\":{\"advertised\":"<<boolean(present)<<",\"queried\":"<<boolean(queried)<<",\"supported\":"<<(queried?boolean(supported):"null")<<",\"enabled\":false}";
        };
        feature("timeline",advertised(VK_KHR_TIMELINE_SEMAPHORE_EXTENSION_NAME),timeline_query,timeline.timelineSemaphore);
        feature("synchronization2",advertised(VK_KHR_SYNCHRONIZATION_2_EXTENSION_NAME),sync_query,sync.synchronization2);
        feature("host_image_copy",advertised(VK_EXT_HOST_IMAGE_COPY_EXTENSION_NAME),host_query,host.hostImageCopy);
        std::cout<<",\"image_format_list_advertised\":"<<boolean(advertised(VK_KHR_IMAGE_FORMAT_LIST_EXTENSION_NAME))<<",\"max_timeline_difference\":"<<timeline_props.maxTimelineSemaphoreValueDifference;
        std::cout<<",\"host_copy_srgb_optimal\":{\"queried\":"<<boolean(host_query&&host.hostImageCopy)<<",\"format\":43,\"usage\":\"HOST_TRANSFER|SAMPLED\",\"vk_result\":"<<image_result<<",\"general_destination\":"<<boolean(general_destination)<<",\"optimal_device_access\":"<<boolean(performance.optimalDeviceAccess)<<",\"identical_memory_layout\":"<<boolean(performance.identicalMemoryLayout)<<",\"identical_memory_type_requirements\":"<<boolean(host_props.identicalMemoryTypeRequirements)<<"}}";
    }
    std::cout<<"]}\n"; return 0;
} catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; } }
