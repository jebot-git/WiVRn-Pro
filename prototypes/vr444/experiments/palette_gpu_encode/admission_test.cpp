// SPDX-License-Identifier: MIT
#include "encoder.hpp"
using namespace palette_encode;
int main() {
    const std::array<uint32_t,1> valid{0xff193858},invalid{0x80193858};
    validate(valid,1,1);
    auto reject=[](auto&& fn) { bool rejected=false;try { fn(); }catch(const std::exception&) { rejected=true; }require(rejected,"admission accepted invalid source"); };
    reject([&]{validate(valid,0,1);});reject([&]{validate(valid,1,0);});
    reject([&]{validate(valid,4097,1);});reject([&]{validate(valid,1,4097);});
    reject([&]{validate(valid,4096,1025);});reject([&]{validate(valid,2,1);});reject([&]{validate(invalid,1,1);});
    const std::array<uint8_t,7> independent_body{3,0,1,0,0x58,0x38,0x19};
    auto wire=assemble(independent_body,1,1);require(wire==rgb_palette::encode(valid,1,1),"header/CRC assembly mismatch");
    require(rgb_palette::decode(wire).rgba==std::vector<uint32_t>(valid.begin(),valid.end()),"assembled bytes mismatch");
    VkPhysicalDeviceProperties properties{};require(!supported(properties,1),"unknown capabilities accepted");
    properties.apiVersion=VK_API_VERSION_1_1;properties.deviceType=VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU;
    auto& l=properties.limits;l.maxComputeWorkGroupInvocations=128;l.maxComputeWorkGroupSize[0]=128;l.maxComputeWorkGroupCount[0]=1;
    l.maxComputeSharedMemorySize=8192;l.maxPushConstantsSize=16;l.maxPerStageDescriptorStorageBuffers=l.maxDescriptorSetStorageBuffers=6;
    require(supported(properties,1),"supported limits rejected");require(!supported(properties,2),"oversized dispatch accepted");
    for(auto member:{&VkPhysicalDeviceLimits::maxComputeWorkGroupInvocations,&VkPhysicalDeviceLimits::maxComputeSharedMemorySize,
                     &VkPhysicalDeviceLimits::maxPushConstantsSize,&VkPhysicalDeviceLimits::maxPerStageDescriptorStorageBuffers,
                     &VkPhysicalDeviceLimits::maxDescriptorSetStorageBuffers}) { auto bad=properties;--(bad.limits.*member);require(!supported(bad,1),"insufficient limit accepted"); }
    auto bad=properties;bad.limits.maxComputeWorkGroupSize[0]=127;require(!supported(bad,1),"small workgroup accepted");
    bad=properties;bad.apiVersion=VK_API_VERSION_1_0;require(!supported(bad,1),"old API accepted");
    bad=properties;bad.deviceType=VK_PHYSICAL_DEVICE_TYPE_CPU;require(!supported(bad,1),"software device accepted");
    std::cout<<"{\"pass\":true,\"invalid_source_cases\":7,\"negative_capability_cases\":10,\"independent_body_assembly\":true,\"vulkan_initialized\":false}\n";
}
