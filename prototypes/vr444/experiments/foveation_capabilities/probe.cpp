// SPDX-License-Identifier: MIT
// Read-only physical-device capability query: no VkDevice, rendering or OpenXR.
#include <vulkan/vulkan.h>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
void check(VkResult r){if(r!=VK_SUCCESS)throw std::runtime_error("Vulkan query failed: "+std::to_string(r));}
std::string quote(const char*p){std::string s="\"";for(;*p;++p){if(*p=='"'||*p=='\\')s+='\\';if(static_cast<unsigned char>(*p)<32)throw std::runtime_error("control byte in name");s+=*p;}return s+'"';}
int main(){VkInstance instance=VK_NULL_HANDLE;try{
    VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};app.pApplicationName="foveation-capabilities";app.apiVersion=VK_API_VERSION_1_1;
    VkInstanceCreateInfo ci{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};ci.pApplicationInfo=&app;check(vkCreateInstance(&ci,nullptr,&instance));
    uint32_t count=0;check(vkEnumeratePhysicalDevices(instance,&count,nullptr));std::vector<VkPhysicalDevice> devices(count);check(vkEnumeratePhysicalDevices(instance,&count,devices.data()));
    std::cout<<"{\"scope\":\"physical-device feature/property query only; no logical device, rendering, gaze or OpenXR tested\",\"devices\":[";bool comma=false;
    for(auto device:devices){
        uint32_t n=0;check(vkEnumerateDeviceExtensionProperties(device,nullptr,&n,nullptr));std::vector<VkExtensionProperties> ext(n);check(vkEnumerateDeviceExtensionProperties(device,nullptr,&n,ext.data()));
        auto has=[&](const char* name){for(auto&e:ext)if(std::string(e.extensionName)==name)return true;return false;};
        bool density=has(VK_EXT_FRAGMENT_DENSITY_MAP_EXTENSION_NAME),deferred=has(VK_EXT_FRAGMENT_DENSITY_MAP_2_EXTENSION_NAME),offset=has(VK_QCOM_FRAGMENT_DENSITY_MAP_OFFSET_EXTENSION_NAME),rate=has(VK_KHR_FRAGMENT_SHADING_RATE_EXTENSION_NAME);
        VkPhysicalDeviceFeatures2 f{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
        VkPhysicalDeviceFragmentDensityMapFeaturesEXT fd{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FRAGMENT_DENSITY_MAP_FEATURES_EXT};
        VkPhysicalDeviceFragmentDensityMap2FeaturesEXT f2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FRAGMENT_DENSITY_MAP_2_FEATURES_EXT};
        VkPhysicalDeviceFragmentDensityMapOffsetFeaturesQCOM fo{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FRAGMENT_DENSITY_MAP_OFFSET_FEATURES_QCOM};
        VkPhysicalDeviceFragmentShadingRateFeaturesKHR fr{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FRAGMENT_SHADING_RATE_FEATURES_KHR};
        auto link=[&](auto& item){item.pNext=f.pNext;f.pNext=&item;};
        if(density)link(fd);if(deferred)link(f2);if(offset)link(fo);if(rate)link(fr);vkGetPhysicalDeviceFeatures2(device,&f);
        VkPhysicalDeviceProperties2 p{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};
        VkPhysicalDeviceFragmentDensityMapPropertiesEXT pd{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FRAGMENT_DENSITY_MAP_PROPERTIES_EXT};
        VkPhysicalDeviceFragmentDensityMap2PropertiesEXT p2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FRAGMENT_DENSITY_MAP_2_PROPERTIES_EXT};
        VkPhysicalDeviceFragmentDensityMapOffsetPropertiesQCOM po{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FRAGMENT_DENSITY_MAP_OFFSET_PROPERTIES_QCOM};
        VkPhysicalDeviceFragmentShadingRatePropertiesKHR pr{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FRAGMENT_SHADING_RATE_PROPERTIES_KHR};
        auto property=[&](auto& item){item.pNext=p.pNext;p.pNext=&item;};
        if(density)property(pd);if(deferred)property(p2);if(offset)property(po);if(rate)property(pr);vkGetPhysicalDeviceProperties2(device,&p);
        if(comma)std::cout<<",";comma=true;
        std::cout<<"{\"name\":"<<quote(p.properties.deviceName)<<",\"vendor_id\":"<<p.properties.vendorID<<",\"device_id\":"<<p.properties.deviceID<<",\"driver_version\":"<<p.properties.driverVersion<<",\"api_version\":"<<p.properties.apiVersion;
        std::cout<<",\"density_map\":{\"queried\":"<<density<<",\"supported\":"<<fd.fragmentDensityMap<<",\"dynamic\":"<<fd.fragmentDensityMapDynamic<<",\"non_subsampled_images\":"<<fd.fragmentDensityMapNonSubsampledImages<<",\"min_texel\":["<<pd.minFragmentDensityTexelSize.width<<","<<pd.minFragmentDensityTexelSize.height<<"],\"max_texel\":["<<pd.maxFragmentDensityTexelSize.width<<","<<pd.maxFragmentDensityTexelSize.height<<"]}";
        std::cout<<",\"density_map2\":{\"queried\":"<<deferred<<",\"deferred\":"<<f2.fragmentDensityMapDeferred<<",\"subsampled_loads\":"<<p2.subsampledLoads<<",\"coarse_reconstruction_early_access\":"<<p2.subsampledCoarseReconstructionEarlyAccess<<",\"max_subsampled_array_layers\":"<<p2.maxSubsampledArrayLayers<<",\"max_subsampled_samplers\":"<<p2.maxDescriptorSetSubsampledSamplers<<"}";
        std::cout<<",\"density_offset\":{\"queried\":"<<offset<<",\"supported\":"<<fo.fragmentDensityMapOffset<<",\"granularity\":["<<po.fragmentDensityOffsetGranularity.width<<","<<po.fragmentDensityOffsetGranularity.height<<"]}";
        std::cout<<",\"shading_rate\":{\"queried\":"<<rate<<",\"pipeline\":"<<fr.pipelineFragmentShadingRate<<",\"primitive\":"<<fr.primitiveFragmentShadingRate<<",\"attachment\":"<<fr.attachmentFragmentShadingRate<<",\"layered_attachments\":"<<pr.layeredShadingRateAttachments<<",\"max_fragment\":["<<pr.maxFragmentSize.width<<","<<pr.maxFragmentSize.height<<"]}}";
    }
    std::cout<<"]}\n";vkDestroyInstance(instance,nullptr);return 0;
}catch(const std::exception&e){if(instance)vkDestroyInstance(instance,nullptr);std::cerr<<e.what()<<"\n";return 1;}}
