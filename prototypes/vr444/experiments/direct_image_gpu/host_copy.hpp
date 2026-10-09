// SPDX-License-Identifier: MIT
#pragma once
#include <vulkan/vulkan.h>
#include <algorithm>
#include <array>
#include <stdexcept>
#include <string>
#include <vector>

namespace direct_image::host_copy {
struct Unsupported:std::runtime_error {using std::runtime_error::runtime_error;};
inline void supported(bool ok,const char* why) {if(!ok)throw Unsupported(why);}
struct Evidence {
    bool queried=false,dependencies=false,feature=false,format=false,image_usage=false;
    std::vector<VkImageLayout> sources,destinations;
    bool optimal_device_access=false,identical_memory_layout=false,identical_memory_type_requirements=false;
    VkImageUsageFlags usage=VK_IMAGE_USAGE_HOST_TRANSFER_BIT_EXT|VK_IMAGE_USAGE_SAMPLED_BIT|VK_IMAGE_USAGE_TRANSFER_SRC_BIT|VK_IMAGE_USAGE_TRANSFER_DST_BIT;
};
inline bool has(const std::vector<VkImageLayout>& list,VkImageLayout layout){return std::find(list.begin(),list.end(),layout)!=list.end();}
inline void gate(const Evidence& e) {
    supported(e.queried,"host copy support was not queried");
    supported(e.dependencies,"host copy EXT dependencies unavailable");
    supported(e.feature,"queried hostImageCopy feature is false");
    supported(e.format,"SRGB optimal HOST_IMAGE_TRANSFER format feature unavailable");
    supported(e.image_usage,"combined SRGB optimal host/sample/transfer usage or dimensions unavailable");
    supported(has(e.sources,VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL)&&has(e.sources,VK_IMAGE_LAYOUT_GENERAL),"host transition source layouts unavailable");
    supported(has(e.destinations,VK_IMAGE_LAYOUT_GENERAL)&&has(e.destinations,VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL),"host transition destination layouts unavailable");
}
struct Support {
    Evidence evidence;
    VkPhysicalDeviceHostImageCopyFeaturesEXT enabled{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_IMAGE_COPY_FEATURES_EXT};
    PFN_vkCopyMemoryToImageEXT copy{};
    PFN_vkTransitionImageLayoutEXT transition{};
    std::vector<const char*> query(VkPhysicalDevice physical,unsigned width,unsigned height,const std::vector<VkExtensionProperties>& advertised) {
        // This prototype requests Vulkan 1.1 and explicitly enables the EXT
        // dependencies. A core-only implementation is deliberately unsupported.
        std::vector<const char*> extensions{VK_EXT_HOST_IMAGE_COPY_EXTENSION_NAME,VK_KHR_COPY_COMMANDS_2_EXTENSION_NAME,VK_KHR_FORMAT_FEATURE_FLAGS_2_EXTENSION_NAME};
        evidence.queried=true;evidence.dependencies=std::all_of(extensions.begin(),extensions.end(),[&](const char* name){return std::any_of(advertised.begin(),advertised.end(),[&](const auto& item){return std::string(item.extensionName)==name;});});
        supported(evidence.dependencies,"host copy EXT dependencies unavailable");
        VkPhysicalDeviceFeatures2 features{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};features.pNext=&enabled;
        vkGetPhysicalDeviceFeatures2(physical,&features);evidence.feature=enabled.hostImageCopy;
        supported(evidence.feature,"queried hostImageCopy feature is false");
        VkPhysicalDeviceHostImageCopyPropertiesEXT host{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_IMAGE_COPY_PROPERTIES_EXT};
        VkPhysicalDeviceProperties2 properties{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};properties.pNext=&host;
        vkGetPhysicalDeviceProperties2(physical,&properties);
        evidence.sources.resize(host.copySrcLayoutCount);evidence.destinations.resize(host.copyDstLayoutCount);
        host.pCopySrcLayouts=evidence.sources.data();host.pCopyDstLayouts=evidence.destinations.data();vkGetPhysicalDeviceProperties2(physical,&properties);
        supported(host.copySrcLayoutCount<=evidence.sources.size()&&host.copyDstLayoutCount<=evidence.destinations.size(),"host layout count changed");
        evidence.sources.resize(host.copySrcLayoutCount);evidence.destinations.resize(host.copyDstLayoutCount);evidence.identical_memory_type_requirements=host.identicalMemoryTypeRequirements;
        VkFormatProperties3KHR format3{VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_3_KHR};VkFormatProperties2 format2{VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2};format2.pNext=&format3;
        vkGetPhysicalDeviceFormatProperties2(physical,VK_FORMAT_R8G8B8A8_SRGB,&format2);
        evidence.format=(format3.optimalTilingFeatures&VK_FORMAT_FEATURE_2_HOST_IMAGE_TRANSFER_BIT_EXT)!=0;
        VkPhysicalDeviceImageFormatInfo2 info{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2};info.format=VK_FORMAT_R8G8B8A8_SRGB;info.type=VK_IMAGE_TYPE_2D;info.tiling=VK_IMAGE_TILING_OPTIMAL;info.usage=evidence.usage;
        VkHostImageCopyDevicePerformanceQueryEXT performance{VK_STRUCTURE_TYPE_HOST_IMAGE_COPY_DEVICE_PERFORMANCE_QUERY_EXT};VkImageFormatProperties2 result{VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2};result.pNext=&performance;
        VkResult status=vkGetPhysicalDeviceImageFormatProperties2(physical,&info,&result);
        evidence.image_usage=status==VK_SUCCESS&&width<=result.imageFormatProperties.maxExtent.width&&height<=result.imageFormatProperties.maxExtent.height&&(result.imageFormatProperties.sampleCounts&VK_SAMPLE_COUNT_1_BIT);
        evidence.optimal_device_access=performance.optimalDeviceAccess;evidence.identical_memory_layout=performance.identicalMemoryLayout;
        gate(evidence);return extensions;
    }
    void load(VkDevice device) {
        copy=reinterpret_cast<PFN_vkCopyMemoryToImageEXT>(vkGetDeviceProcAddr(device,"vkCopyMemoryToImageEXT"));
        transition=reinterpret_cast<PFN_vkTransitionImageLayoutEXT>(vkGetDeviceProcAddr(device,"vkTransitionImageLayoutEXT"));
        supported(copy&&transition,"enabled host copy entry points unavailable");
    }
};
} // namespace direct_image::host_copy
