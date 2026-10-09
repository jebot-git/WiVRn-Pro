// SPDX-License-Identifier: GPL-3.0-or-later
#include "staged_rowblocks_image.h"
#include <exception>
#include <array>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace wivrn::staged_rowblocks {
static_assert(sizeof(Block) == 16);
namespace {
void need(bool ok, const char* message) { if (!ok) throw std::runtime_error(message); }
void check(VkResult result) {
    if (result != VK_SUCCESS) throw std::runtime_error("staged RRB Vulkan result " + std::to_string(result));
}
constexpr VkImageSubresourceRange color{VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
struct Common {
    VkDevice device;
    VkDescriptorSetLayout descriptors{};
    VkPipelineLayout layout{};
    VkShaderModule shader{};
    VkPipeline pipeline{};
    explicit Common(VkDevice d): device(d) {}
    ~Common() {
        if (pipeline) vkDestroyPipeline(device, pipeline, nullptr);
        if (shader) vkDestroyShaderModule(device, shader, nullptr);
        if (layout) vkDestroyPipelineLayout(device, layout, nullptr);
        if (descriptors) vkDestroyDescriptorSetLayout(device, descriptors, nullptr);
    }
};
struct Buffer { VkBuffer handle{}; VkDeviceMemory memory{}; void* mapped{}; VkDeviceSize bytes{}; };
uint32_t memory_type(const VkPhysicalDeviceMemoryProperties& p, uint32_t bits, VkMemoryPropertyFlags flags) {
    for (uint32_t i = 0; i < p.memoryTypeCount; ++i)
        if ((bits & (1u << i)) && (p.memoryTypes[i].propertyFlags & flags) == flags) return i;
    throw std::runtime_error("staged RRB required memory unavailable");
}
}

void validate(const Input& in) {
    need(in.width && in.height && in.width <= 4096 && in.height <= 4096 &&
         uint64_t(in.width) * in.height <= 4194304, "staged RRB dimensions");
    need(in.rgb.size() == uint64_t(in.width) * in.height * 3, "staged RRB residual size");
    need(!in.blocks.empty() && in.blocks.size() <= 256, "staged RRB block count");
    uint32_t row = 0;
    for (const auto& b : in.blocks) {
        need(b.row == row && b.height && b.height <= 128 && b.height <= in.height - row &&
             b.mode <= 2 && b.raw_offset == uint64_t(row) * in.width * 3,
             "staged RRB block coverage or mode");
        row += b.height;
    }
    need(row == in.height, "staged RRB incomplete row coverage");
}

struct Slot {
    std::shared_ptr<Common> common;
    Identity id{};
    std::array<Buffer, 2> input{};
    VkImage image{};
    VkDeviceMemory memory{};
    VkImageView storage{}, sample{};
    VkCommandPool pool{};
    VkCommandBuffer command{};
    VkFence fence{};
    VkDescriptorPool descriptors{};
    VkDescriptorSet set{};
    enum class State { idle, recorded, submitted, ready, lost, uncertain } state = State::idle;
    explicit Slot(std::shared_ptr<Common> c): common(std::move(c)) {}
    // Pending destruction observes producer completion before mapped input,
    // command/fence, or image resources can be reused or released. This wait is
    // deliberately not a global device/queue wait. An external process watchdog
    // is required for a hung device; guessing completion would be unsafe.
    void drain() noexcept {
        if (state == State::uncertain) std::terminate();
        if (state == State::submitted) {
            const auto result = vkWaitForFences(common->device, 1, &fence, VK_TRUE, UINT64_MAX);
            if (result != VK_SUCCESS && result != VK_ERROR_DEVICE_LOST) std::terminate();
            state = result == VK_SUCCESS ? State::ready : State::lost;
        }
    }
    ~Slot() {
        drain();
        auto device = common->device;
        if (fence) vkDestroyFence(device, fence, nullptr);
        if (pool) vkDestroyCommandPool(device, pool, nullptr);
        if (descriptors) vkDestroyDescriptorPool(device, descriptors, nullptr);
        if (sample) vkDestroyImageView(device, sample, nullptr);
        if (storage) vkDestroyImageView(device, storage, nullptr);
        if (image) vkDestroyImage(device, image, nullptr);
        if (memory) vkFreeMemory(device, memory, nullptr);
        for (auto b : input) {
            if (b.mapped) vkUnmapMemory(device, b.memory);
            if (b.handle) vkDestroyBuffer(device, b.handle, nullptr);
            if (b.memory) vkFreeMemory(device, b.memory, nullptr);
        }
    }
};

struct Pool::Impl {
    std::shared_ptr<Common> common;
    std::vector<std::shared_ptr<Slot>> slots;
    uint32_t width, height, eye, max_groups;
    uint64_t generation, previous = 0;
    bool has_previous = false;
    Impl(VkPhysicalDevice physical, VkDevice device, uint32_t family,
         uint32_t w, uint32_t h, uint64_t gen, uint32_t e,
         std::span<const uint32_t> code, uint32_t count):
        common(std::make_shared<Common>(device)), width(w), height(h), eye(e), generation(gen) {
        need(physical && device && w && h && w <= 4096 && h <= 4096 &&
             uint64_t(w) * h <= 4194304 && gen && e < 2 && count && count <= 8,
             "staged RRB pool configuration");
        need(code.size() >= 5 && code.size_bytes() <= 1024 * 1024 && code[0] == 0x07230203,
             "staged RRB SPIR-V size or magic");
        VkPhysicalDeviceProperties props{};
        VkPhysicalDeviceMemoryProperties memory{};
        vkGetPhysicalDeviceProperties(physical, &props);
        vkGetPhysicalDeviceMemoryProperties(physical, &memory);
        need(props.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU ||
             props.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU,
             "staged RRB requires an actual hardware GPU");
        const auto& l = props.limits;
        max_groups = ((w + 127) / 128) * 256;
        need(props.apiVersion >= VK_API_VERSION_1_1 && l.maxComputeWorkGroupInvocations >= 128 &&
             l.maxComputeWorkGroupSize[0] >= 128 && l.maxComputeWorkGroupCount[0] >= max_groups &&
             l.maxPushConstantsSize >= 16 && l.maxPerStageDescriptorStorageBuffers >= 2 &&
             l.maxDescriptorSetStorageBuffers >= 2 && l.maxPerStageDescriptorStorageImages >= 1 &&
             l.maxDescriptorSetStorageImages >= 1 && l.maxImageDimension2D >= w && l.maxImageDimension2D >= h &&
             l.maxStorageBufferRange >= uint64_t(w) * h * 3 + 3 && l.maxStorageBufferRange >= 4100,
             "staged RRB device limits");
        uint32_t n = 0;
        vkGetPhysicalDeviceQueueFamilyProperties(physical, &n, nullptr);
        std::vector<VkQueueFamilyProperties> families(n);
        vkGetPhysicalDeviceQueueFamilyProperties(physical, &n, families.data());
        need(family < n && (families[family].queueFlags & (VK_QUEUE_COMPUTE_BIT | VK_QUEUE_GRAPHICS_BIT)) ==
             (VK_QUEUE_COMPUTE_BIT | VK_QUEUE_GRAPHICS_BIT), "staged RRB requires graphics/compute application family");
        VkFormatProperties format{};
        vkGetPhysicalDeviceFormatProperties(physical, VK_FORMAT_R8G8B8A8_UNORM, &format);
        constexpr auto base_features = VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT | VK_FORMAT_FEATURE_TRANSFER_SRC_BIT |
                                       VK_FORMAT_FEATURE_TRANSFER_DST_BIT;
        need((format.optimalTilingFeatures & base_features) == base_features, "staged RRB UNORM features");
        vkGetPhysicalDeviceFormatProperties(physical, VK_FORMAT_R8G8B8A8_SRGB, &format);
        constexpr auto sample_features = VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT | VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT;
        need((format.optimalTilingFeatures & sample_features) == sample_features, "staged RRB SRGB linear sampling");
        constexpr auto usage = VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_SAMPLED_BIT |
                               VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
        VkImageFormatProperties support{};
        check(vkGetPhysicalDeviceImageFormatProperties(physical, VK_FORMAT_R8G8B8A8_UNORM, VK_IMAGE_TYPE_2D,
              VK_IMAGE_TILING_OPTIMAL, usage, VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT, &support));
        need(w <= support.maxExtent.width && h <= support.maxExtent.height &&
             (support.sampleCounts & VK_SAMPLE_COUNT_1_BIT), "staged RRB image usage support");

        std::array<VkDescriptorSetLayoutBinding, 3> bindings{{
            {0, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr},
            {1, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr},
            {2, VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr}}};
        VkDescriptorSetLayoutCreateInfo dl{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
        dl.bindingCount = bindings.size(); dl.pBindings = bindings.data();
        check(vkCreateDescriptorSetLayout(device, &dl, nullptr, &common->descriptors));
        VkPushConstantRange push{VK_SHADER_STAGE_COMPUTE_BIT, 0, 16};
        VkPipelineLayoutCreateInfo pl{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
        pl.setLayoutCount = 1; pl.pSetLayouts = &common->descriptors;
        pl.pushConstantRangeCount = 1; pl.pPushConstantRanges = &push;
        check(vkCreatePipelineLayout(device, &pl, nullptr, &common->layout));
        VkShaderModuleCreateInfo sm{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
        sm.codeSize = code.size_bytes(); sm.pCode = code.data();
        check(vkCreateShaderModule(device, &sm, nullptr, &common->shader));
        VkComputePipelineCreateInfo cp{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
        cp.layout = common->layout; cp.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
        cp.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT; cp.stage.module = common->shader; cp.stage.pName = "main";
        check(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &cp, nullptr, &common->pipeline));

        for (uint32_t i = 0; i < count; ++i) {
            auto s = std::make_shared<Slot>(common); // Own partial construction too.
            for (uint32_t b = 0; b < 2; ++b) {
                auto& input = s->input[b]; input.bytes = b ? 256 * sizeof(Block) : (uint64_t(w) * h * 3 + 3) & ~uint64_t(3);
                VkBufferCreateInfo bc{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
                bc.size = input.bytes; bc.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
                check(vkCreateBuffer(device, &bc, nullptr, &input.handle));
                VkMemoryRequirements req{}; vkGetBufferMemoryRequirements(device, input.handle, &req);
                VkMemoryAllocateInfo ma{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO}; ma.allocationSize = req.size;
                ma.memoryTypeIndex = memory_type(memory, req.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
                check(vkAllocateMemory(device, &ma, nullptr, &input.memory));
                check(vkBindBufferMemory(device, input.handle, input.memory, 0));
                check(vkMapMemory(device, input.memory, 0, input.bytes, 0, &input.mapped));
            }
            VkImageCreateInfo ic{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
            ic.flags = VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT; ic.imageType = VK_IMAGE_TYPE_2D;
            ic.format = VK_FORMAT_R8G8B8A8_UNORM; ic.extent = {w, h, 1}; ic.mipLevels = ic.arrayLayers = 1;
            ic.samples = VK_SAMPLE_COUNT_1_BIT; ic.tiling = VK_IMAGE_TILING_OPTIMAL; ic.usage = usage;
            check(vkCreateImage(device, &ic, nullptr, &s->image));
            VkMemoryRequirements req{}; vkGetImageMemoryRequirements(device, s->image, &req);
            VkMemoryAllocateInfo ma{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO}; ma.allocationSize = req.size;
            ma.memoryTypeIndex = memory_type(memory, req.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
            check(vkAllocateMemory(device, &ma, nullptr, &s->memory));
            check(vkBindImageMemory(device, s->image, s->memory, 0));
            auto view = [&](VkFormat f, VkImageUsageFlags u, VkImageView& v) {
                VkImageViewUsageCreateInfo restricted{VK_STRUCTURE_TYPE_IMAGE_VIEW_USAGE_CREATE_INFO}; restricted.usage = u;
                VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO}; vi.pNext = &restricted;
                vi.image = s->image; vi.viewType = VK_IMAGE_VIEW_TYPE_2D; vi.format = f; vi.subresourceRange = color;
                check(vkCreateImageView(device, &vi, nullptr, &v));
            };
            view(VK_FORMAT_R8G8B8A8_UNORM, VK_IMAGE_USAGE_STORAGE_BIT, s->storage);
            view(VK_FORMAT_R8G8B8A8_SRGB, VK_IMAGE_USAGE_SAMPLED_BIT, s->sample);
            std::array<VkDescriptorPoolSize, 2> sizes{{{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 2}, {VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 1}}};
            VkDescriptorPoolCreateInfo dp{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
            dp.maxSets = 1; dp.poolSizeCount = sizes.size(); dp.pPoolSizes = sizes.data();
            check(vkCreateDescriptorPool(device, &dp, nullptr, &s->descriptors));
            VkDescriptorSetAllocateInfo da{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
            da.descriptorPool = s->descriptors; da.descriptorSetCount = 1; da.pSetLayouts = &common->descriptors;
            check(vkAllocateDescriptorSets(device, &da, &s->set));
            std::array<VkDescriptorBufferInfo, 2> bi{{{s->input[0].handle, 0, s->input[0].bytes}, {s->input[1].handle, 0, s->input[1].bytes}}};
            VkDescriptorImageInfo ii{VK_NULL_HANDLE, s->storage, VK_IMAGE_LAYOUT_GENERAL};
            std::array<VkWriteDescriptorSet, 3> writes{};
            for (uint32_t b = 0; b < 3; ++b) {
                writes[b].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET; writes[b].dstSet = s->set; writes[b].dstBinding = b;
                writes[b].descriptorCount = 1; writes[b].descriptorType = b == 2 ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE : VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                if (b == 2) writes[b].pImageInfo = &ii; else writes[b].pBufferInfo = &bi[b];
            }
            vkUpdateDescriptorSets(device, writes.size(), writes.data(), 0, nullptr);
            VkCommandPoolCreateInfo pi{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
            pi.queueFamilyIndex = family; pi.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
            check(vkCreateCommandPool(device, &pi, nullptr, &s->pool));
            VkCommandBufferAllocateInfo ca{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
            ca.commandPool = s->pool; ca.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY; ca.commandBufferCount = 1;
            check(vkAllocateCommandBuffers(device, &ca, &s->command));
            VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
            check(vkCreateFence(device, &fi, nullptr, &s->fence));
            slots.push_back(std::move(s));
        }
    }
    std::optional<Pending> prepare(uint64_t frame, const Input& in) {
        validate(in);
        need(in.width == width && in.height == height, "staged RRB pool/input shape mismatch");
        need(!has_previous || frame > previous, "staged RRB nonadvancing frame identity");
        std::shared_ptr<Slot> s;
        for (const auto& candidate : slots)
            if (candidate.use_count() == 1 && (candidate->state == Slot::State::idle || candidate->state == Slot::State::ready)) { s = candidate; break; }
        if (!s) return std::nullopt;
        // No view or GPU reader can exist for this slot. All complete metadata
        // checks above precede the first host write. The source is immutable for
        // this call; future changes cannot affect the private staging copy.
        std::memcpy(s->input[0].mapped, in.rgb.data(), in.rgb.size());
        std::memset(static_cast<uint8_t*>(s->input[0].mapped) + in.rgb.size(), 0, s->input[0].bytes - in.rgb.size());
        std::memcpy(s->input[1].mapped, in.blocks.data(), in.blocks.size_bytes());
        check(vkResetCommandBuffer(s->command, 0));
        VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO}; begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
        check(vkBeginCommandBuffer(s->command, &begin));
        VkMemoryBarrier host{VK_STRUCTURE_TYPE_MEMORY_BARRIER}; host.srcAccessMask = VK_ACCESS_HOST_WRITE_BIT; host.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
        vkCmdPipelineBarrier(s->command, VK_PIPELINE_STAGE_HOST_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 1, &host, 0, nullptr, 0, nullptr);
        VkImageMemoryBarrier image{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
        image.srcQueueFamilyIndex = image.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        image.image = s->image; image.subresourceRange = color;
        image.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED; image.newLayout = VK_IMAGE_LAYOUT_GENERAL;
        image.dstAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
        vkCmdPipelineBarrier(s->command, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 0, nullptr, 0, nullptr, 1, &image);
        uint32_t params[]{width, height, uint32_t(in.blocks.size()), uint32_t(in.rgb.size())};
        vkCmdBindPipeline(s->command, VK_PIPELINE_BIND_POINT_COMPUTE, common->pipeline);
        vkCmdBindDescriptorSets(s->command, VK_PIPELINE_BIND_POINT_COMPUTE, common->layout, 0, 1, &s->set, 0, nullptr);
        vkCmdPushConstants(s->command, common->layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(params), params);
        vkCmdDispatch(s->command, ((width + 127) / 128) * uint32_t(in.blocks.size()), 1, 1);
        image.oldLayout = VK_IMAGE_LAYOUT_GENERAL; image.newLayout = Lease::layout;
        image.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT; image.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
        vkCmdPipelineBarrier(s->command, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
            VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT,
            0, 0, nullptr, 0, nullptr, 1, &image);
        check(vkEndCommandBuffer(s->command));
        s->id = {generation, frame, eye}; s->state = Slot::State::recorded;
        previous = frame; has_previous = true;
        return Pending(std::move(s));
    }
};

Lease::Lease(std::shared_ptr<Slot> s): slot(std::move(s)) {}
Identity Lease::identity() const { need(bool(slot), "empty staged RRB lease"); return slot->id; }
VkImage Lease::image() const { need(bool(slot), "empty staged RRB lease"); return slot->image; }
VkImageView Lease::view() const { need(bool(slot), "empty staged RRB lease"); return slot->sample; }
Pending::Pending(std::shared_ptr<Slot> s): slot(std::move(s)) {}
Pending::Pending(Pending&& other) noexcept: slot(std::move(other.slot)) {}
Pending::~Pending() {
    if (!slot) return;
    slot->drain();
    if (slot->state == Slot::State::recorded) slot->state = Slot::State::idle;
}
void Pending::submit(VkQueue queue) {
    need(slot && slot->state == Slot::State::recorded && queue, "staged RRB submit state");
    check(vkResetFences(slot->common->device, 1, &slot->fence));
    VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO}; si.commandBufferCount = 1; si.pCommandBuffers = &slot->command;
    auto result = vkQueueSubmit(queue, 1, &si, slot->fence);
    // A failed submit is not a reusable frame. Device-lost permits teardown;
    // other failure results do not constitute a producer completion proof.
    if (result != VK_SUCCESS) {
        slot->state = result == VK_ERROR_DEVICE_LOST ? Slot::State::lost : Slot::State::uncertain;
        check(result);
    }
    slot->state = Slot::State::submitted;
}
Lease Pending::publish(uint64_t timeout_ns) {
    need(slot && slot->state == Slot::State::submitted, "staged RRB publication requires submission");
    const auto result = vkWaitForFences(slot->common->device, 1, &slot->fence, VK_TRUE, timeout_ns);
    if (result == VK_ERROR_DEVICE_LOST) slot->state = Slot::State::lost;
    check(result);
    slot->state = Slot::State::ready;
    return Lease(std::move(slot));
}
Pool::Pool(VkPhysicalDevice p, VkDevice d, uint32_t f, uint32_t w, uint32_t h,
           uint64_t g, uint32_t e, std::span<const uint32_t> code, uint32_t count):
    impl(std::make_unique<Impl>(p, d, f, w, h, g, e, code, count)) {}
Pool::~Pool() = default;
std::optional<Pending> Pool::prepare(uint64_t frame, const Input& input) { return impl->prepare(frame, input); }
} // namespace wivrn::staged_rowblocks
