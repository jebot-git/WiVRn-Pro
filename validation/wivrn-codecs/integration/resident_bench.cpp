// SPDX-License-Identifier: MIT
// Standalone hardware regression for the application-device decoder helper.
// Build with test_gpu_decoder.cpp, codec.cpp, -DWIVRN_CODEC_TESTS and -lvulkan.
#include "test_gpu_decoder.h"
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {
void check(VkResult result) {
    if (result != VK_SUCCESS) throw std::runtime_error("Vulkan result " + std::to_string(result));
}
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

struct Context {
    VkInstance instance{};
    VkPhysicalDevice physical{};
    VkDevice device{};
    VkQueue queue{};
    uint32_t family{};
    VkCommandPool pool{};
    VkCommandBuffer command{};
    VkFence fence{};
    VkQueryPool queries{};
    VkBuffer readback{};
    VkDeviceMemory memory{};
    void* mapped{};
    uint64_t timestamp_mask{};
    double timestamp_factor{};

    ~Context() {
        if (device) {
            auto result = vkDeviceWaitIdle(device);
            if (result != VK_SUCCESS && result != VK_ERROR_DEVICE_LOST) std::abort();
            if (mapped) vkUnmapMemory(device, memory);
            if (readback) vkDestroyBuffer(device, readback, nullptr);
            if (memory) vkFreeMemory(device, memory, nullptr);
            if (queries) vkDestroyQueryPool(device, queries, nullptr);
            if (fence) vkDestroyFence(device, fence, nullptr);
            if (pool) vkDestroyCommandPool(device, pool, nullptr);
            vkDestroyDevice(device, nullptr);
        }
        if (instance) vkDestroyInstance(instance, nullptr);
    }
    void initialize(uint32_t selected) {
        VkApplicationInfo application{VK_STRUCTURE_TYPE_APPLICATION_INFO};
        application.pApplicationName = "WiVRn resident decoder regression";
        application.apiVersion = VK_API_VERSION_1_1;
        VkInstanceCreateInfo create_instance{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
        create_instance.pApplicationInfo = &application;
        check(vkCreateInstance(&create_instance, nullptr, &instance));
        uint32_t count = 0;
        check(vkEnumeratePhysicalDevices(instance, &count, nullptr));
        require(selected < count, "selected GPU unavailable");
        std::vector<VkPhysicalDevice> devices(count);
        check(vkEnumeratePhysicalDevices(instance, &count, devices.data()));
        physical = devices[selected];
        VkPhysicalDeviceProperties properties;
        vkGetPhysicalDeviceProperties(physical, &properties);
        require(properties.deviceType != VK_PHYSICAL_DEVICE_TYPE_CPU, "software Vulkan forbidden");
        vkGetPhysicalDeviceQueueFamilyProperties(physical, &count, nullptr);
        std::vector<VkQueueFamilyProperties> families(count);
        vkGetPhysicalDeviceQueueFamilyProperties(physical, &count, families.data());
        family = 0;
        while (family < count && (!(families[family].queueFlags & VK_QUEUE_COMPUTE_BIT) || !families[family].timestampValidBits)) ++family;
        require(family < count, "compute queue with timestamps unavailable");
        const auto bits = families[family].timestampValidBits;
        timestamp_mask = bits == 64 ? UINT64_MAX : (uint64_t(1) << bits) - 1;
        timestamp_factor = properties.limits.timestampPeriod/1e6;
        const float priority = 1;
        VkDeviceQueueCreateInfo create_queue{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
        create_queue.queueFamilyIndex = family;
        create_queue.queueCount = 1;
        create_queue.pQueuePriorities = &priority;
        VkDeviceCreateInfo create_device{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
        create_device.queueCreateInfoCount = 1;
        create_device.pQueueCreateInfos = &create_queue;
        check(vkCreateDevice(physical, &create_device, nullptr, &device));
        vkGetDeviceQueue(device, family, 0, &queue);
        VkCommandPoolCreateInfo create_pool{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
        create_pool.queueFamilyIndex = family;
        create_pool.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
        check(vkCreateCommandPool(device, &create_pool, nullptr, &pool));
        VkCommandBufferAllocateInfo allocate_command{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
        allocate_command.commandPool = pool;
        allocate_command.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
        allocate_command.commandBufferCount = 1;
        check(vkAllocateCommandBuffers(device, &allocate_command, &command));
        VkFenceCreateInfo create_fence{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
        check(vkCreateFence(device, &create_fence, nullptr, &fence));
        VkQueryPoolCreateInfo create_queries{VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO};
        create_queries.queryType = VK_QUERY_TYPE_TIMESTAMP;
        create_queries.queryCount = 2;
        check(vkCreateQueryPool(device, &create_queries, nullptr, &queries));
        VkBufferCreateInfo create_buffer{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
        create_buffer.size = 576*640*4;
        create_buffer.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;
        create_buffer.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        check(vkCreateBuffer(device, &create_buffer, nullptr, &readback));
        VkMemoryRequirements requirements;
        vkGetBufferMemoryRequirements(device, readback, &requirements);
        VkPhysicalDeviceMemoryProperties memory_properties;
        vkGetPhysicalDeviceMemoryProperties(physical, &memory_properties);
        const auto required = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
        uint32_t type = 0;
        while (type < memory_properties.memoryTypeCount &&
               (!(requirements.memoryTypeBits & (1u << type)) ||
                (memory_properties.memoryTypes[type].propertyFlags & required) != required)) ++type;
        require(type < memory_properties.memoryTypeCount, "coherent readback memory unavailable");
        VkMemoryAllocateInfo allocation{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
        allocation.allocationSize = requirements.size;
        allocation.memoryTypeIndex = type;
        check(vkAllocateMemory(device, &allocation, nullptr, &memory));
        check(vkBindBufferMemory(device, readback, memory, 0));
        check(vkMapMemory(device, memory, 0, VK_WHOLE_SIZE, 0, &mapped));
    }

    double decode(wivrn::test_gpu_decoder& decoder, const vr444::Frame& frame,
                  const vr444::Frame* rejected = nullptr) {
        check(vkResetCommandBuffer(command, 0));
        VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
        begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
        check(vkBeginCommandBuffer(command, &begin));
        vkCmdResetQueryPool(command, queries, 0, 2);
        vkCmdWriteTimestamp(command, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, queries, 0);
        decoder.record_decode(command, frame);
        if (rejected) {
            // The malformed call must not mutate the input of the already
            // recorded, not-yet-submitted valid frame, nor append GPU commands.
            bool threw = false;
            try { decoder.record_decode(command, *rejected); }
            catch (const std::runtime_error&) { threw = true; }
            require(threw, "malformed frame accepted by resident helper");
        }
        vkCmdWriteTimestamp(command, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, queries, 1);
        VkBufferCopy copy{0, 0, VkDeviceSize(frame.width)*frame.height*4};
        vkCmdCopyBuffer(command, decoder.output(), readback, 1, &copy);
        VkMemoryBarrier host{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
        host.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
        host.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
        vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
                             0, 1, &host, 0, nullptr, 0, nullptr);
        check(vkEndCommandBuffer(command));
        check(vkResetFences(device, 1, &fence));
        VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};
        submit.commandBufferCount = 1;
        submit.pCommandBuffers = &command;
        auto result = vkQueueSubmit(queue, 1, &submit, fence);
        if (result == VK_SUCCESS) result = vkWaitForFences(device, 1, &fence, VK_TRUE, 5'000'000'000);
        if (result != VK_SUCCESS) {
            // Keep helper resources alive until pending GPU work is drained.
            auto idle = vkDeviceWaitIdle(device);
            if (idle != VK_SUCCESS && idle != VK_ERROR_DEVICE_LOST) std::abort();
            check(result);
        }
        uint64_t times[2];
        check(vkGetQueryPoolResults(device, queries, 0, 2, sizeof(times), times, sizeof(uint64_t), VK_QUERY_RESULT_64_BIT));
        const auto reference = vr444::decode(frame);
        require(!std::memcmp(mapped, reference.data(), reference.size()*4), "resident GPU output differs from CPU reference");
        return double((times[1] - times[0]) & timestamp_mask)*timestamp_factor;
    }
};

std::vector<std::pair<std::string, vr444::Frame>> malformed(const vr444::Frame& good) {
    std::vector<std::pair<std::string, vr444::Frame>> cases;
    auto add = [&](std::string name, auto mutate) { auto frame = good; mutate(frame); cases.emplace_back(std::move(name), std::move(frame)); };
    add("dimension_mismatch", [](auto& f) { ++f.width; });
    add("zero_quantizer", [](auto& f) { f.q = 0; });
    add("truncated_slots", [](auto& f) { f.slots.pop_back(); });
    add("missing_tile_length", [](auto& f) { f.lengths.pop_back(); });
    add("zero_tile_length", [](auto& f) { f.lengths[0] = 0; });
    add("oversized_tile_length", [](auto& f) { f.lengths[0] = vr444::SLOT_WORDS + 1; });
    add("quantizer_map_size", [](auto& f) { f.tile_q.resize(f.lengths.size() + 1, 1); });
    add("invalid_tile_quantizer", [](auto& f) { f.tile_q.assign(f.lengths.size(), 1); f.tile_q[0] = 0; });
    add("invalid_dc", [](auto& f) { f.slots[0] |= 0x7ff; });
    add("invalid_group_mode", [](auto& f) { f.slots[0] = (f.slots[0] & 0x7ffu) | (1u << 11) | (1u << 12) | (3u << 16); });
    auto flat = vr444::encode(std::vector<uint32_t>(size_t(good.width)*good.height, 0xff304050), good.width, good.height, 1);
    flat.slots[flat.lengths[0] - 1] |= 0x80000000;
    cases.emplace_back("nonzero_padding", std::move(flat));
    return cases;
}
}

int main(int argc, char** argv) {
    try {
        require(argc >= 2 && argc <= 3, "usage: resident_bench SHADER_DIRECTORY [GPU_INDEX]");
        Context context;
        context.initialize(argc == 3 ? std::stoul(argv[2]) : 0);
        unsigned valid_runs = 0, rejected_runs = 0;
        for (const std::string kernel: {"baseline", "coop64", "coop128"}) {
            // Exercise partial construction cleanup (all buffers/descriptors
            // exist by the time opening this missing shader fails).
            bool rejected_shader = false;
            try { wivrn::test_gpu_decoder missing(context.physical, context.device, context.family, 17, 19, std::string(argv[1]) + "/missing", kernel); }
            catch (const std::runtime_error&) { rejected_shader = true; }
            require(rejected_shader, "missing shader accepted");
            for (const auto [width, height]: {std::pair{1u, 1u}, {17u, 19u}, {576u, 640u}}) {
                wivrn::test_gpu_decoder decoder(context.physical, context.device, context.family, width, height, argv[1], kernel);
                for (unsigned step = 0; step < 4; ++step) {
                    const unsigned q = step % 2 ? 8 : 1;
                    const auto source = vr444::scene(width, height, step % 3);
                    std::vector<uint32_t> map;
                    if (step == 2) {
                        map.resize(((width + 15)/16)*((height + 15)/16));
                        for (unsigned i = 0; i < map.size(); ++i) map[i] = i % 2 ? 8 : 1;
                    }
                    const auto frame = vr444::encode(source, width, height, q, map);
                    const auto gpu_ms = context.decode(decoder, frame);
                    if (q == 1 && map.empty()) require(!std::memcmp(context.mapped, source.data(), source.size()*4), "lossless source mismatch");
                    ++valid_runs;
                    std::cout << "{\"kernel\":\"" << kernel << "\",\"width\":" << width << ",\"height\":" << height
                              << ",\"q\":" << q << ",\"scene\":" << step % 3 << ",\"tile_map\":" << (!map.empty() ? "true" : "false")
                              << ",\"gpu_decode_ms\":" << gpu_ms << ",\"matches_cpu\":true}\n";
                }
                if (width == 17) {
                    const auto reference = vr444::encode(vr444::scene(width, height, 2), width, height, 8);
                    for (const auto& [name, invalid]: malformed(reference)) {
                        context.decode(decoder, reference, &invalid);
                        ++rejected_runs;
                        std::cout << "{\"kernel\":\"" << kernel << "\",\"malformed\":\"" << name << "\",\"rejected_without_input_mutation\":true}\n";
                    }
                    // Fresh valid input after the rejection sequence must work.
                    context.decode(decoder, vr444::encode(vr444::scene(width, height, 1), width, height, 1));
                    ++valid_runs;
                }
            }
        }
        std::cout << "{\"passed\":true,\"valid_frames\":" << valid_runs << ",\"malformed_rejections\":" << rejected_runs << "}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
