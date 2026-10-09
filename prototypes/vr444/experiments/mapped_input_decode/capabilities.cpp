// SPDX-License-Identifier: MIT
// Host physical-device query only; no logical device, allocation or queue.
#include <vulkan/vulkan.h>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
void check(VkResult r){if(r!=VK_SUCCESS)throw std::runtime_error("Vulkan result "+std::to_string(r));}
std::string quote(const char* p){std::string out="\"";for(;*p;++p){if(*p=='"'||*p=='\\')out+='\\';if(static_cast<unsigned char>(*p)<32)throw std::runtime_error("name control character");out+=*p;}return out+'"';}
struct Owner{VkInstance instance{};~Owner(){if(instance)vkDestroyInstance(instance,nullptr);}};
int main(){try{Owner o;VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};app.apiVersion=VK_API_VERSION_1_1;
    VkInstanceCreateInfo ci{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};ci.pApplicationInfo=&app;check(vkCreateInstance(&ci,nullptr,&o.instance));
    uint32_t n=0;check(vkEnumeratePhysicalDevices(o.instance,&n,nullptr));std::vector<VkPhysicalDevice> devices(n);check(vkEnumeratePhysicalDevices(o.instance,&n,devices.data()));
    std::cout<<"{\"schema\":1,\"physical_query_only\":true,\"logical_device_created\":false,\"headset_used\":false,\"devices\":[";
    for(unsigned i=0;i<n;++i){auto device=devices[i];VkPhysicalDeviceProperties p{};vkGetPhysicalDeviceProperties(device,&p);
        VkPhysicalDeviceMemoryProperties m{};vkGetPhysicalDeviceMemoryProperties(device,&m);
        uint32_t e=0;check(vkEnumerateDeviceExtensionProperties(device,nullptr,&e,nullptr));std::vector<VkExtensionProperties> extensions(e);check(vkEnumerateDeviceExtensionProperties(device,nullptr,&e,extensions.data()));
        bool external=false;for(const auto& ext:extensions)external|=std::string(ext.extensionName)==VK_EXT_EXTERNAL_MEMORY_HOST_EXTENSION_NAME;
        VkPhysicalDeviceExternalMemoryHostPropertiesEXT host{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_MEMORY_HOST_PROPERTIES_EXT};
        VkExternalBufferProperties buffer{VK_STRUCTURE_TYPE_EXTERNAL_BUFFER_PROPERTIES};bool queried=external&&p.apiVersion>=VK_API_VERSION_1_1;
        if(queried){VkPhysicalDeviceProperties2 props{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};props.pNext=&host;vkGetPhysicalDeviceProperties2(device,&props);
            VkPhysicalDeviceExternalBufferInfo info{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_BUFFER_INFO};info.usage=VK_BUFFER_USAGE_STORAGE_BUFFER_BIT|VK_BUFFER_USAGE_TRANSFER_SRC_BIT;info.handleType=VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT;
            vkGetPhysicalDeviceExternalBufferProperties(device,&info,&buffer);}
        std::cout<<(i?",":"")<<"{\"index\":"<<i<<",\"device\":"<<quote(p.deviceName)<<",\"type\":"<<p.deviceType<<",\"driver\":"<<p.driverVersion<<",\"memory_types\":[";
        for(unsigned t=0;t<m.memoryTypeCount;++t)std::cout<<(t?",":"")<<"{\"index\":"<<t<<",\"flags\":"<<m.memoryTypes[t].propertyFlags<<",\"heap\":"<<m.memoryTypes[t].heapIndex<<"}";
        std::cout<<"],\"external_host_advertised\":"<<(external?"true":"false")<<",\"external_host_queried\":"<<(queried?"true":"false")<<",\"host_pointer_alignment\":"<<host.minImportedHostPointerAlignment
                 <<",\"host_buffer_usage\":"<<(VK_BUFFER_USAGE_STORAGE_BUFFER_BIT|VK_BUFFER_USAGE_TRANSFER_SRC_BIT)<<",\"external_features\":"<<buffer.externalMemoryProperties.externalMemoryFeatures
                 <<",\"compatible_handles\":"<<buffer.externalMemoryProperties.compatibleHandleTypes<<",\"buffer_memory_type_compatibility_not_queried\":true,\"host_pointer_import_not_attempted\":true}";
    }std::cout<<"]}\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
