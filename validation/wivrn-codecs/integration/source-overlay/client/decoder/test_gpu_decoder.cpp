// SPDX-License-Identifier: GPL-3.0-or-later
#ifdef WIVRN_CODEC_TESTS
#include "test_gpu_decoder.h"
#include <array>
#include <cstring>
#include <fstream>
#include <source_location>
#include <stdexcept>
#include <vector>

namespace wivrn {
namespace {
void check(VkResult result, const std::source_location where = std::source_location::current()) {
    if (result != VK_SUCCESS)
        throw std::runtime_error("test GPU decoder Vulkan error " + std::to_string(result) +
                                 " at " + where.file_name() + ":" + std::to_string(where.line()));
}

// Separate RAII owner so partially constructed Impl instances also release all
// resources. The borrowed device is never waited on or destroyed here.
struct Resources {
    VkDevice device;
    std::array<VkBuffer, 5> buffers{};
    std::array<VkDeviceMemory, 5> memory{};
    std::array<void*, 5> mapped{};
    VkDescriptorSetLayout descriptor_layout{};
    VkPipelineLayout pipeline_layout{};
    VkDescriptorPool descriptor_pool{};
    VkShaderModule shader{};
    VkPipeline pipeline{};

    explicit Resources(VkDevice device): device(device) {}
    ~Resources() {
        if (pipeline) vkDestroyPipeline(device, pipeline, nullptr);
        if (shader) vkDestroyShaderModule(device, shader, nullptr);
        if (descriptor_pool) vkDestroyDescriptorPool(device, descriptor_pool, nullptr);
        if (pipeline_layout) vkDestroyPipelineLayout(device, pipeline_layout, nullptr);
        if (descriptor_layout) vkDestroyDescriptorSetLayout(device, descriptor_layout, nullptr);
        for (unsigned i = 0; i < buffers.size(); ++i) {
            if (mapped[i]) vkUnmapMemory(device, memory[i]);
            if (buffers[i]) vkDestroyBuffer(device, buffers[i], nullptr);
            if (memory[i]) vkFreeMemory(device, memory[i], nullptr);
        }
    }
};
}

struct test_gpu_decoder::Impl {
    Resources owned;
    uint32_t width, height, tiles, groups;
    VkDescriptorSet descriptor{};
    std::array<VkDeviceSize, 5> sizes{};
    std::array<bool, 5> coherent{};

    Impl(VkPhysicalDevice physical, VkDevice device, uint32_t family,
         uint32_t w, uint32_t h, const std::string& shaders, const std::string& kernel):
        owned(device), width(w), height(h) {
        if (!physical || !device || !w || !h || w > 4096 || h > 4096 || uint64_t(w)*h > 8388608)
            throw std::runtime_error("invalid test GPU decoder device/dimensions");
        if (kernel != "baseline" && kernel != "coop64" && kernel != "coop128" && kernel != "coop128dc")
            throw std::runtime_error("invalid test GPU decoder kernel");
        const bool cooperative = kernel != "baseline";
        const uint32_t lanes = cooperative ? ((kernel == "coop128" || kernel == "coop128dc") ? 128 : 64) : 32;
        tiles = ((w + 15)/16)*((h + 15)/16);
        groups = cooperative ? tiles : (tiles + 31)/32;
        VkPhysicalDeviceProperties properties;
        vkGetPhysicalDeviceProperties(physical, &properties);
        if (properties.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU)
            throw std::runtime_error("software Vulkan forbidden in hardware integration");
        const auto& limits = properties.limits;
        if (properties.apiVersion < VK_API_VERSION_1_1 || limits.maxComputeWorkGroupSize[0] < lanes ||
            limits.maxComputeWorkGroupInvocations < lanes || limits.maxComputeWorkGroupCount[0] < groups ||
            limits.maxPerStageDescriptorStorageBuffers < 5 || limits.maxDescriptorSetStorageBuffers < 5 ||
            (cooperative && limits.maxComputeSharedMemorySize < 6144))
            throw std::runtime_error("test GPU decoder exceeds device limits");
        uint32_t family_count = 0;
        vkGetPhysicalDeviceQueueFamilyProperties(physical, &family_count, nullptr);
        std::vector<VkQueueFamilyProperties> families(family_count);
        vkGetPhysicalDeviceQueueFamilyProperties(physical, &family_count, families.data());
        if (family >= family_count || !(families[family].queueFlags & VK_QUEUE_COMPUTE_BIT))
            throw std::runtime_error("test GPU decoder requires a compute-capable application queue");

        sizes = {4, VkDeviceSize(tiles)*vr444::SLOT_WORDS*4, VkDeviceSize(tiles)*4,
                 VkDeviceSize(w)*h*4, VkDeviceSize(tiles)*4};
        VkPhysicalDeviceMemoryProperties memory_properties;
        vkGetPhysicalDeviceMemoryProperties(physical, &memory_properties);
        for (unsigned i = 0; i < owned.buffers.size(); ++i) {
            if (sizes[i] > limits.maxStorageBufferRange)
                throw std::runtime_error("test GPU decoder exceeds storage buffer range");
            VkBufferCreateInfo buffer{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
            buffer.size = sizes[i];
            buffer.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
            if (i == 3) buffer.usage |= VK_BUFFER_USAGE_TRANSFER_SRC_BIT;
            buffer.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
            check(vkCreateBuffer(device, &buffer, nullptr, &owned.buffers[i]));
            VkMemoryRequirements requirements;
            vkGetBufferMemoryRequirements(device, owned.buffers[i], &requirements);
            const VkMemoryPropertyFlags required = i == 3 ? VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT : VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT;
            uint32_t type = UINT32_MAX;
            for (uint32_t j = 0; j < memory_properties.memoryTypeCount; ++j) {
                const auto flags = memory_properties.memoryTypes[j].propertyFlags;
                if ((requirements.memoryTypeBits & (1u << j)) && (flags & required) == required) {
                    type = j;
                    if (i == 3 || (flags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) break;
                }
            }
            if (type == UINT32_MAX) throw std::runtime_error("test GPU decoder memory type unavailable");
            coherent[i] = memory_properties.memoryTypes[type].propertyFlags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
            VkMemoryAllocateInfo allocation{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
            allocation.allocationSize = requirements.size;
            allocation.memoryTypeIndex = type;
            check(vkAllocateMemory(device, &allocation, nullptr, &owned.memory[i]));
            check(vkBindBufferMemory(device, owned.buffers[i], owned.memory[i], 0));
            if (i != 3) check(vkMapMemory(device, owned.memory[i], 0, VK_WHOLE_SIZE, 0, &owned.mapped[i]));
        }

        std::array<VkDescriptorSetLayoutBinding, 5> bindings{};
        for (unsigned i = 0; i < bindings.size(); ++i)
            bindings[i] = {i, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr};
        VkDescriptorSetLayoutCreateInfo descriptor_layout{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
        descriptor_layout.bindingCount = bindings.size();
        descriptor_layout.pBindings = bindings.data();
        check(vkCreateDescriptorSetLayout(device, &descriptor_layout, nullptr, &owned.descriptor_layout));
        VkPushConstantRange push_constants{VK_SHADER_STAGE_COMPUTE_BIT, 0, 16};
        VkPipelineLayoutCreateInfo pipeline_layout{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
        pipeline_layout.setLayoutCount = 1;
        pipeline_layout.pSetLayouts = &owned.descriptor_layout;
        pipeline_layout.pushConstantRangeCount = 1;
        pipeline_layout.pPushConstantRanges = &push_constants;
        check(vkCreatePipelineLayout(device, &pipeline_layout, nullptr, &owned.pipeline_layout));
        VkDescriptorPoolSize pool_size{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 5};
        VkDescriptorPoolCreateInfo pool{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
        pool.maxSets = 1;
        pool.poolSizeCount = 1;
        pool.pPoolSizes = &pool_size;
        check(vkCreateDescriptorPool(device, &pool, nullptr, &owned.descriptor_pool));
        VkDescriptorSetAllocateInfo descriptor_allocation{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
        descriptor_allocation.descriptorPool = owned.descriptor_pool;
        descriptor_allocation.descriptorSetCount = 1;
        descriptor_allocation.pSetLayouts = &owned.descriptor_layout;
        check(vkAllocateDescriptorSets(device, &descriptor_allocation, &descriptor));
        std::array<VkDescriptorBufferInfo, 5> infos{};
        std::array<VkWriteDescriptorSet, 5> writes{};
        for (unsigned i = 0; i < writes.size(); ++i) {
            infos[i] = {owned.buffers[i], 0, sizes[i]};
            writes[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
            writes[i].dstSet = descriptor;
            writes[i].dstBinding = i;
            writes[i].descriptorCount = 1;
            writes[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
            writes[i].pBufferInfo = &infos[i];
        }
        vkUpdateDescriptorSets(device, writes.size(), writes.data(), 0, nullptr);

        const auto filename = shaders + "/decode" + (cooperative ? "-" + kernel : "") + ".spv";
        std::ifstream file(filename, std::ios::binary | std::ios::ate);
        if (!file) throw std::runtime_error("test GPU decoder shader not found: " + filename);
        const auto bytes = file.tellg();
        if (bytes <= 0 || bytes % 4 || bytes > 16*1024*1024)
            throw std::runtime_error("invalid test GPU decoder SPIR-V size");
        std::vector<uint32_t> code(size_t(bytes)/4);
        file.seekg(0);
        file.read(reinterpret_cast<char*>(code.data()), bytes);
        if (!file) throw std::runtime_error("test GPU decoder shader read failed");
        VkShaderModuleCreateInfo shader{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
        shader.codeSize = size_t(bytes);
        shader.pCode = code.data();
        check(vkCreateShaderModule(device, &shader, nullptr, &owned.shader));
        VkComputePipelineCreateInfo pipeline{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
        pipeline.layout = owned.pipeline_layout;
        pipeline.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
        pipeline.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
        pipeline.stage.module = owned.shader;
        pipeline.stage.pName = "main";
        check(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &pipeline, nullptr, &owned.pipeline));
    }

    void record(VkCommandBuffer command, const vr444::Frame& frame) {
        if (!command || frame.width != width || frame.height != height)
            throw std::runtime_error("test GPU decoder command/dimensions mismatch");
        vr444::validate(frame); // No mapped writes occur until all frame validation succeeds.
        std::memcpy(owned.mapped[1], frame.slots.data(), sizes[1]);
        std::memcpy(owned.mapped[2], frame.lengths.data(), sizes[2]);
        auto* quantizers = static_cast<uint32_t*>(owned.mapped[4]);
        for (uint32_t i = 0; i < tiles; ++i)
            quantizers[i] = frame.tile_q.empty() ? frame.q : frame.tile_q[i];
        for (unsigned i: {1u, 2u, 4u}) {
            if (coherent[i]) continue;
            VkMappedMemoryRange range{VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE};
            range.memory = owned.memory[i];
            range.offset = 0;
            range.size = VK_WHOLE_SIZE;
            check(vkFlushMappedMemoryRanges(owned.device, 1, &range));
        }
        VkMemoryBarrier inputs{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
        inputs.srcAccessMask = VK_ACCESS_HOST_WRITE_BIT;
        inputs.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
        vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_HOST_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                             0, 1, &inputs, 0, nullptr, 0, nullptr);
        vkCmdBindPipeline(command, VK_PIPELINE_BIND_POINT_COMPUTE, owned.pipeline);
        vkCmdBindDescriptorSets(command, VK_PIPELINE_BIND_POINT_COMPUTE, owned.pipeline_layout,
                                0, 1, &descriptor, 0, nullptr);
        const uint32_t parameters[]{width, height, frame.q, tiles};
        vkCmdPushConstants(command, owned.pipeline_layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(parameters), parameters);
        vkCmdDispatch(command, groups, 1, 1);
        VkBufferMemoryBarrier output_ready{VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER};
        output_ready.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
        output_ready.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
        output_ready.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        output_ready.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        output_ready.buffer = owned.buffers[3];
        output_ready.offset = 0;
        output_ready.size = sizes[3];
        vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
                             0, 0, nullptr, 1, &output_ready, 0, nullptr);
    }
};

test_gpu_decoder::test_gpu_decoder(VkPhysicalDevice physical, VkDevice device, uint32_t family,
                                 uint32_t width, uint32_t height, const std::string& shaders, const std::string& kernel):
    impl(std::make_unique<Impl>(physical, device, family, width, height, shaders, kernel)) {}
test_gpu_decoder::~test_gpu_decoder() = default;
void test_gpu_decoder::record_decode(VkCommandBuffer command, const vr444::Frame& frame) { impl->record(command, frame); }
VkBuffer test_gpu_decoder::output() const { return impl->owned.buffers[3]; }
}
#endif
