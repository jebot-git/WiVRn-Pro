// SPDX-License-Identifier: MIT
// Offscreen regression using the production reprojection vertex/fragment shaders.
// No OpenXR session, codec, optical observation or performance benchmark.
// Usage: color_contract_probe reprojection.vert.spv reprojection.frag.spv [device_index]
#include <vulkan/vulkan.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
constexpr uint32_t W = 256, H = 8;
constexpr double code_tolerance = 2.0;
void check(VkResult r) { if (r != VK_SUCCESS) throw std::runtime_error("Vulkan error " + std::to_string(r)); }
struct Buffer { VkBuffer handle{}; VkDeviceMemory memory{}; void* mapped{}; };
struct Image { VkImage handle{}; VkDeviceMemory memory{}; VkImageView view{}; bool initialized = false; };
struct Vertex { float position[2]; uint32_t uv[2]; };
struct Constants {
    int32_t rgb_rect[4]{0, 0, int32_t(W), int32_t(H)};
    int32_t a_rect[4]{0, 0, 1, 1};
    float scale[4]{1, 1, 1, 1};
    float bias[4]{};
    float cutout[12]{};
};
static_assert(sizeof(Constants) == 112 && sizeof(Vertex) == 16);
std::vector<uint32_t> spirv(const char* path) {
    std::ifstream f(path, std::ios::binary | std::ios::ate);
    if (!f) throw std::runtime_error("cannot open shader");
    auto n = f.tellg();
    if (n <= 0 || n % 4 || n > 16 * 1024 * 1024) throw std::runtime_error("shader size");
    std::vector<uint32_t> bytes(size_t(n) / 4);
    f.seekg(0); f.read(reinterpret_cast<char*>(bytes.data()), n);
    if (!f) throw std::runtime_error("shader read");
    return bytes;
}
double eotf(double s) { return s <= 0.04045 ? s / 12.92 : std::pow((s + 0.055) / 1.055, 2.4); }
double oetf(double s) { return s <= 0.0031308 ? s * 12.92 : 1.055 * std::pow(s, 1.0 / 2.4) - 0.055; }
std::array<uint8_t, 4> pixel(unsigned x, unsigned y, unsigned eye) {
    x = eye ? W - 1 - x : x;
    switch (y) {
        case 0: return {uint8_t(x), uint8_t(x), uint8_t(x), 255};
        case 1: return {uint8_t(x), 0, 0, 255};
        case 2: return {0, uint8_t(x), 0, 255};
        case 3: return {0, 0, uint8_t(x), 255};
        case 4: return {uint8_t(x & 1 ? 255 : 0), uint8_t(x & 1 ? 0 : 255), 128, 255};
        case 5: return {uint8_t(x < W / 2 ? 0 : 255), 64, 192, 255};
        case 6: return {16, 128, 235, 255};
        default: return {uint8_t((x * 17) % 256), uint8_t((x * 29) % 256), uint8_t((x * 61) % 256), 255};
    }
}
struct Validation { double error = 0; unsigned x = 0, y = 0, channel = 0, actual = 0; double expected = 0; };
Validation verify(const uint8_t* bytes, unsigned eye, bool srgb_view, bool manual, double offset, float scale, float bias) {
    Validation worst;
    for (unsigned y = 0; y < H; ++y) for (unsigned x = 0; x < W; ++x) for (unsigned c = 0; c < 4; ++c) {
        auto component = [&](unsigned ix, unsigned iy) {
            double value = pixel(std::min(ix, W - 1), std::min(iy, H - 1), eye)[c] / 255.0;
            return srgb_view && c < 3 ? eotf(value) : value;
        };
        double value = (1 - offset) * (1 - offset) * component(x, y)
                     + offset * (1 - offset) * component(x + 1, y)
                     + (1 - offset) * offset * component(x, y + 1)
                     + offset * offset * component(x + 1, y + 1);
        if (manual && c < 3) value = eotf(value);
        if (c < 3) value = value * scale + bias;
        double expected = 255.0 * std::clamp(c < 3 ? oetf(value) : value, 0.0, 1.0);
        unsigned actual = bytes[(y * W + x) * 4 + c];
        double error = std::abs(actual - expected);
        if (error > worst.error) worst = {error, x, y, c, actual, expected};
    }
    return worst;
}
struct Context {
    VkInstance instance{}; VkPhysicalDevice physical{}; VkDevice device{}; VkQueue queue{};
    VkPhysicalDeviceProperties properties{}; VkPhysicalDeviceMemoryProperties memory{}; uint32_t family = 0;
    Buffer upload, vertices, readback;
    Image linear_input, srgb_input, output;
    VkSampler sampler{}; VkRenderPass renderpass{}; VkFramebuffer framebuffer{};
    VkDescriptorSetLayout descriptor_layout{}; VkDescriptorPool descriptor_pool{}; VkDescriptorSet descriptor{};
    VkPipelineLayout layout{}; std::array<VkShaderModule, 2> shaders{}; std::array<VkPipeline, 2> pipelines{};
    VkCommandPool command_pool{}; VkCommandBuffer command{}; VkFence fence{};
    ~Context() {
        if (device) {
            vkDeviceWaitIdle(device);
            if (fence) vkDestroyFence(device, fence, nullptr);
            if (command_pool) vkDestroyCommandPool(device, command_pool, nullptr);
            for (auto p : pipelines) if (p) vkDestroyPipeline(device, p, nullptr);
            for (auto s : shaders) if (s) vkDestroyShaderModule(device, s, nullptr);
            if (layout) vkDestroyPipelineLayout(device, layout, nullptr);
            if (descriptor_pool) vkDestroyDescriptorPool(device, descriptor_pool, nullptr);
            if (descriptor_layout) vkDestroyDescriptorSetLayout(device, descriptor_layout, nullptr);
            if (framebuffer) vkDestroyFramebuffer(device, framebuffer, nullptr);
            if (renderpass) vkDestroyRenderPass(device, renderpass, nullptr);
            if (sampler) vkDestroySampler(device, sampler, nullptr);
            for (auto image : {linear_input, srgb_input, output}) {
                if (image.view) vkDestroyImageView(device, image.view, nullptr);
                if (image.handle) vkDestroyImage(device, image.handle, nullptr);
                if (image.memory) vkFreeMemory(device, image.memory, nullptr);
            }
            for (auto b : {upload, vertices, readback}) {
                if (b.mapped) vkUnmapMemory(device, b.memory);
                if (b.handle) vkDestroyBuffer(device, b.handle, nullptr);
                if (b.memory) vkFreeMemory(device, b.memory, nullptr);
            }
            vkDestroyDevice(device, nullptr);
        }
        if (instance) vkDestroyInstance(instance, nullptr);
    }
    uint32_t memory_type(uint32_t bits, VkMemoryPropertyFlags flags) {
        for (uint32_t i = 0; i < memory.memoryTypeCount; ++i)
            if ((bits & (1u << i)) && (memory.memoryTypes[i].propertyFlags & flags) == flags) return i;
        throw std::runtime_error("required memory type absent");
    }
    void make_buffer(Buffer& b, VkDeviceSize size, VkBufferUsageFlags usage) {
        VkBufferCreateInfo info{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO}; info.size = size; info.usage = usage;
        check(vkCreateBuffer(device, &info, nullptr, &b.handle));
        VkMemoryRequirements req{}; vkGetBufferMemoryRequirements(device, b.handle, &req);
        VkMemoryAllocateInfo alloc{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO}; alloc.allocationSize = req.size;
        alloc.memoryTypeIndex = memory_type(req.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
        check(vkAllocateMemory(device, &alloc, nullptr, &b.memory)); check(vkBindBufferMemory(device, b.handle, b.memory, 0));
        check(vkMapMemory(device, b.memory, 0, size, 0, &b.mapped));
    }
    void make_image(Image& image, VkFormat format, VkImageUsageFlags usage) {
        VkFormatProperties props{}; vkGetPhysicalDeviceFormatProperties(physical, format, &props);
        auto wanted = usage & VK_IMAGE_USAGE_SAMPLED_BIT
            ? VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT | VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT | VK_FORMAT_FEATURE_TRANSFER_DST_BIT
            : VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT | VK_FORMAT_FEATURE_TRANSFER_SRC_BIT;
        if ((props.optimalTilingFeatures & wanted) != wanted) throw std::runtime_error("required image features absent");
        VkImageCreateInfo info{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO}; info.imageType = VK_IMAGE_TYPE_2D; info.format = format;
        info.extent = {W, H, 1}; info.mipLevels = info.arrayLayers = 1; info.samples = VK_SAMPLE_COUNT_1_BIT;
        info.tiling = VK_IMAGE_TILING_OPTIMAL; info.usage = usage;
        check(vkCreateImage(device, &info, nullptr, &image.handle)); VkMemoryRequirements req{};
        vkGetImageMemoryRequirements(device, image.handle, &req);
        VkMemoryAllocateInfo alloc{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO}; alloc.allocationSize = req.size;
        alloc.memoryTypeIndex = memory_type(req.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
        check(vkAllocateMemory(device, &alloc, nullptr, &image.memory)); check(vkBindImageMemory(device, image.handle, image.memory, 0));
        VkImageViewCreateInfo view{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO}; view.image = image.handle;
        view.viewType = VK_IMAGE_VIEW_TYPE_2D; view.format = format; view.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
        check(vkCreateImageView(device, &view, nullptr, &image.view));
    }
    void init(const char* vert, const char* frag, uint32_t selected) {
        VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO}; app.pApplicationName = "WiVRn reprojection color regression"; app.apiVersion = VK_API_VERSION_1_1;
        VkInstanceCreateInfo ici{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO}; ici.pApplicationInfo = &app;
        check(vkCreateInstance(&ici, nullptr, &instance)); uint32_t count = 0;
        check(vkEnumeratePhysicalDevices(instance, &count, nullptr)); std::vector<VkPhysicalDevice> devices(count);
        check(vkEnumeratePhysicalDevices(instance, &count, devices.data()));
        if (selected >= count) throw std::runtime_error("device index out of range");
        physical = devices[selected]; vkGetPhysicalDeviceProperties(physical, &properties);
        if (properties.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU) throw std::runtime_error("software Vulkan forbidden");
        vkGetPhysicalDeviceMemoryProperties(physical, &memory); vkGetPhysicalDeviceQueueFamilyProperties(physical, &count, nullptr);
        std::vector<VkQueueFamilyProperties> families(count); vkGetPhysicalDeviceQueueFamilyProperties(physical, &count, families.data());
        while (family < count && !(families[family].queueFlags & VK_QUEUE_GRAPHICS_BIT)) ++family;
        if (family == count) throw std::runtime_error("graphics queue unavailable");
        float priority = 1; VkDeviceQueueCreateInfo qi{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO}; qi.queueFamilyIndex = family; qi.queueCount = 1; qi.pQueuePriorities = &priority;
        VkDeviceCreateInfo di{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO}; di.queueCreateInfoCount = 1; di.pQueueCreateInfos = &qi;
        check(vkCreateDevice(physical, &di, nullptr, &device)); vkGetDeviceQueue(device, family, 0, &queue);
        make_buffer(upload, W * H * 4, VK_BUFFER_USAGE_TRANSFER_SRC_BIT);
        make_buffer(vertices, sizeof(Vertex) * 4, VK_BUFFER_USAGE_VERTEX_BUFFER_BIT);
        make_buffer(readback, W * H * 4, VK_BUFFER_USAGE_TRANSFER_DST_BIT);
        make_image(linear_input, VK_FORMAT_R8G8B8A8_UNORM, VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT);
        make_image(srgb_input, VK_FORMAT_R8G8B8A8_SRGB, VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT);
        make_image(output, VK_FORMAT_R8G8B8A8_SRGB, VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT);
        VkSamplerCreateInfo si{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO}; si.magFilter = si.minFilter = VK_FILTER_LINEAR;
        si.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST; si.addressModeU = si.addressModeV = si.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
        check(vkCreateSampler(device, &si, nullptr, &sampler));
        VkAttachmentDescription attachment{}; attachment.format = VK_FORMAT_R8G8B8A8_SRGB; attachment.samples = VK_SAMPLE_COUNT_1_BIT;
        attachment.loadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE; attachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
        attachment.initialLayout = attachment.finalLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
        VkAttachmentReference ref{0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL}; VkSubpassDescription subpass{};
        subpass.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS; subpass.colorAttachmentCount = 1; subpass.pColorAttachments = &ref;
        VkRenderPassCreateInfo rp{VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO}; rp.attachmentCount = 1; rp.pAttachments = &attachment; rp.subpassCount = 1; rp.pSubpasses = &subpass;
        check(vkCreateRenderPass(device, &rp, nullptr, &renderpass));
        VkFramebufferCreateInfo fb{VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO}; fb.renderPass = renderpass; fb.attachmentCount = 1;
        fb.pAttachments = &output.view; fb.width = W; fb.height = H; fb.layers = 1; check(vkCreateFramebuffer(device, &fb, nullptr, &framebuffer));
        VkDescriptorSetLayoutBinding binding{0, VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 1, VK_SHADER_STAGE_FRAGMENT_BIT, &sampler};
        VkDescriptorSetLayoutCreateInfo dl{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO}; dl.bindingCount = 1; dl.pBindings = &binding;
        check(vkCreateDescriptorSetLayout(device, &dl, nullptr, &descriptor_layout));
        VkDescriptorPoolSize ps{VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 1}; VkDescriptorPoolCreateInfo dp{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
        dp.maxSets = 1; dp.poolSizeCount = 1; dp.pPoolSizes = &ps; check(vkCreateDescriptorPool(device, &dp, nullptr, &descriptor_pool));
        VkDescriptorSetAllocateInfo da{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO}; da.descriptorPool = descriptor_pool; da.descriptorSetCount = 1; da.pSetLayouts = &descriptor_layout;
        check(vkAllocateDescriptorSets(device, &da, &descriptor));
        VkPushConstantRange pc{VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, sizeof(Constants)};
        VkPipelineLayoutCreateInfo pl{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO}; pl.setLayoutCount = 1; pl.pSetLayouts = &descriptor_layout; pl.pushConstantRangeCount = 1; pl.pPushConstantRanges = &pc;
        check(vkCreatePipelineLayout(device, &pl, nullptr, &layout));
        const char* paths[]{vert, frag};
        for (unsigned i = 0; i < 2; ++i) { auto words = spirv(paths[i]); VkShaderModuleCreateInfo sm{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO}; sm.codeSize = words.size() * 4; sm.pCode = words.data(); check(vkCreateShaderModule(device, &sm, nullptr, &shaders[i])); }
        VkVertexInputBindingDescription vb{0, sizeof(Vertex), VK_VERTEX_INPUT_RATE_VERTEX};
        VkVertexInputAttributeDescription va[]{{0, 0, VK_FORMAT_R32G32_SFLOAT, 0}, {1, 0, VK_FORMAT_R32G32_UINT, 8}};
        VkPipelineVertexInputStateCreateInfo vi{VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO}; vi.vertexBindingDescriptionCount = 1; vi.pVertexBindingDescriptions = &vb; vi.vertexAttributeDescriptionCount = 2; vi.pVertexAttributeDescriptions = va;
        VkPipelineInputAssemblyStateCreateInfo ia{VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO}; ia.topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_STRIP;
        VkViewport viewport{0, 0, float(W), float(H), 0, 1}; VkRect2D scissor{{0, 0}, {W, H}};
        VkPipelineViewportStateCreateInfo vp{VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO}; vp.viewportCount = 1; vp.pViewports = &viewport; vp.scissorCount = 1; vp.pScissors = &scissor;
        VkPipelineRasterizationStateCreateInfo rs{VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO}; rs.polygonMode = VK_POLYGON_MODE_FILL; rs.lineWidth = 1;
        VkPipelineMultisampleStateCreateInfo ms{VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO}; ms.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;
        VkPipelineColorBlendAttachmentState cb{}; cb.colorWriteMask = VK_COLOR_COMPONENT_R_BIT | VK_COLOR_COMPONENT_G_BIT | VK_COLOR_COMPONENT_B_BIT | VK_COLOR_COMPONENT_A_BIT;
        VkPipelineColorBlendStateCreateInfo blend{VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO}; blend.attachmentCount = 1; blend.pAttachments = &cb;
        for (unsigned manual = 0; manual < 2; ++manual) {
            const uint32_t values[]{0, manual}; // alpha=0; constant_id1 controls the extra EOTF.
            const VkSpecializationMapEntry entries[]{{0, 0, 4}, {1, 4, 4}};
            VkSpecializationInfo spec{2, entries, sizeof(values), values};
            VkPipelineShaderStageCreateInfo stages[]{{VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0, VK_SHADER_STAGE_VERTEX_BIT, shaders[0], "main", nullptr}, {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0, VK_SHADER_STAGE_FRAGMENT_BIT, shaders[1], "main", &spec}};
            VkGraphicsPipelineCreateInfo pi{VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO}; pi.stageCount = 2; pi.pStages = stages; pi.pVertexInputState = &vi; pi.pInputAssemblyState = &ia; pi.pViewportState = &vp; pi.pRasterizationState = &rs; pi.pMultisampleState = &ms; pi.pColorBlendState = &blend; pi.layout = layout; pi.renderPass = renderpass;
            check(vkCreateGraphicsPipelines(device, VK_NULL_HANDLE, 1, &pi, nullptr, &pipelines[manual]));
        }
        VkCommandPoolCreateInfo cp{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO}; cp.queueFamilyIndex = family; cp.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT; check(vkCreateCommandPool(device, &cp, nullptr, &command_pool));
        VkCommandBufferAllocateInfo ca{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO}; ca.commandPool = command_pool; ca.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY; ca.commandBufferCount = 1; check(vkAllocateCommandBuffers(device, &ca, &command));
        VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO}; check(vkCreateFence(device, &fi, nullptr, &fence));
    }
    const uint8_t* draw(bool srgb, bool manual, unsigned eye, float offset, float scale, float bias) {
        auto& input = srgb ? srgb_input : linear_input;
        for (unsigned y = 0; y < H; ++y) for (unsigned x = 0; x < W; ++x) { auto p = pixel(x, y, eye); std::memcpy(static_cast<uint8_t*>(upload.mapped) + (y * W + x) * 4, p.data(), 4); }
        // Use dyadic coordinates and a 2W x 2H quad: exact center/quarter/half
        // sample locations need no 1/(W+1) or 1/(H+1) interpolation reciprocal.
        // Non-dyadic geometry exposed Adreno interpolation bleed at zero edges.
        // Coverage, intended per-pixel UV and the reference/tolerance stay fixed.
        const float l = -1 - 2 * offset / W, r = 3 - 2 * offset / W;
        const float t = -1 - 2 * offset / H, b = 3 - 2 * offset / H;
        const Vertex quad[]{{{l, t}, {0, 0}}, {{l, b}, {0, 2 * H}}, {{r, t}, {2 * W, 0}}, {{r, b}, {2 * W, 2 * H}}};
        std::memcpy(vertices.mapped, quad, sizeof(quad));
        VkDescriptorImageInfo image_info{sampler, input.view, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL}; VkWriteDescriptorSet write{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET}; write.dstSet = descriptor; write.dstBinding = 0; write.descriptorCount = 1; write.descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER; write.pImageInfo = &image_info; vkUpdateDescriptorSets(device, 1, &write, 0, nullptr);
        check(vkResetCommandBuffer(command, 0)); VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO}; check(vkBeginCommandBuffer(command, &begin));
        VkMemoryBarrier host{VK_STRUCTURE_TYPE_MEMORY_BARRIER}; host.srcAccessMask = VK_ACCESS_HOST_WRITE_BIT; host.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_VERTEX_ATTRIBUTE_READ_BIT;
        vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_HOST_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT | VK_PIPELINE_STAGE_VERTEX_INPUT_BIT, 0, 1, &host, 0, nullptr, 0, nullptr);
        VkImageMemoryBarrier im{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER}; im.srcAccessMask = input.initialized ? VK_ACCESS_SHADER_READ_BIT : 0; im.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT; im.oldLayout = input.initialized ? VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL : VK_IMAGE_LAYOUT_UNDEFINED; im.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL; im.srcQueueFamilyIndex = im.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED; im.image = input.handle; im.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
        vkCmdPipelineBarrier(command, input.initialized ? VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT : VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 0, nullptr, 1, &im);
        VkBufferImageCopy copy{}; copy.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}; copy.imageExtent = {W, H, 1}; vkCmdCopyBufferToImage(command, upload.handle, input.handle, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy);
        im.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT; im.dstAccessMask = VK_ACCESS_SHADER_READ_BIT; im.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL; im.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
        vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT, 0, 0, nullptr, 0, nullptr, 1, &im);
        im.image = output.handle; im.srcAccessMask = output.initialized ? VK_ACCESS_TRANSFER_READ_BIT : 0; im.dstAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT; im.oldLayout = output.initialized ? VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL : VK_IMAGE_LAYOUT_UNDEFINED; im.newLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
        vkCmdPipelineBarrier(command, output.initialized ? VK_PIPELINE_STAGE_TRANSFER_BIT : VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, 0, 0, nullptr, 0, nullptr, 1, &im);
        VkRenderPassBeginInfo rp{VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO}; rp.renderPass = renderpass; rp.framebuffer = framebuffer; rp.renderArea = {{0, 0}, {W, H}};
        vkCmdBeginRenderPass(command, &rp, VK_SUBPASS_CONTENTS_INLINE); vkCmdBindPipeline(command, VK_PIPELINE_BIND_POINT_GRAPHICS, pipelines[manual]); vkCmdBindDescriptorSets(command, VK_PIPELINE_BIND_POINT_GRAPHICS, layout, 0, 1, &descriptor, 0, nullptr);
        Constants pc; for (unsigned c = 0; c < 3; ++c) { pc.scale[c] = scale; pc.bias[c] = bias; }
        vkCmdPushConstants(command, layout, VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, sizeof(pc), &pc);
        VkDeviceSize zero = 0; vkCmdBindVertexBuffers(command, 0, 1, &vertices.handle, &zero); vkCmdDraw(command, 4, 1, 0, 0); vkCmdEndRenderPass(command);
        im.srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT; im.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT; im.oldLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL; im.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
        vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 0, nullptr, 1, &im);
        vkCmdCopyImageToBuffer(command, output.handle, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, readback.handle, 1, &copy);
        VkMemoryBarrier read{VK_STRUCTURE_TYPE_MEMORY_BARRIER}; read.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT; read.dstAccessMask = VK_ACCESS_HOST_READ_BIT; vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &read, 0, nullptr, 0, nullptr);
        check(vkEndCommandBuffer(command)); check(vkResetFences(device, 1, &fence)); VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO}; submit.commandBufferCount = 1; submit.pCommandBuffers = &command;
        check(vkQueueSubmit(queue, 1, &submit, fence)); check(vkWaitForFences(device, 1, &fence, VK_TRUE, 3'000'000'000));
        input.initialized = output.initialized = true; return static_cast<const uint8_t*>(readback.mapped);
    }
};
}
int main(int argc, char** argv) {
    try {
        if (argc < 3 || argc > 4) throw std::runtime_error("usage: color_contract_probe vert.spv frag.spv [device_index]");
        Context c; c.init(argv[1], argv[2], argc == 4 ? std::stoul(argv[3]) : 0);
        struct Policy { const char* name; bool srgb, hmd, sampled_linear, old_control; };
        const Policy policies[]{{"test_srgb_corrected", true, true, true, false}, {"legacy_unorm_manual", false, true, false, false}, {"legacy_unorm_no_manual", false, false, false, false}, {"old_test_double_decode_control", true, true, false, true}, {"test_srgb_hmd_false", true, false, true, false}, {"test_srgb_corrected_reuse", true, true, true, false}};
        std::printf("{\"schema\":1,\"scope\":\"offscreen graphics using actual reprojection shaders; no OpenXR, codec or photons\",\"device\":\"%s\",\"vendor_id\":%u,\"device_id\":%u,\"driver_version\":%u,\"width\":%u,\"height\":%u,\"tolerance_8bit_codes\":%.1f,\"cases\":[\n", c.properties.deviceName, c.properties.vendorID, c.properties.deviceID, c.properties.driverVersion, W, H, code_tolerance);
        bool first = true, all = true; unsigned tested = 0;
        for (auto p : policies) for (unsigned eye = 0; eye < 2; ++eye) for (float offset : {0.f, 0.25f, 0.5f}) for (unsigned dim = 0; dim < 2; ++dim) {
            bool manual = p.hmd && !p.sampled_linear; float scale = dim ? 0.65f : 1.f, bias = dim ? 0.04f : 0.f;
            const auto* result = c.draw(p.srgb, manual, eye, offset, scale, bias);
            auto expected_policy = verify(result, eye, p.srgb, manual, offset, scale, bias);
            auto once_only = verify(result, eye, true, false, offset, scale, bias);
            bool pass = expected_policy.error <= code_tolerance;
            if (p.old_control) pass &= once_only.error >= 20;
            else if (p.sampled_linear) pass &= once_only.error <= code_tolerance;
            all &= pass; ++tested;
            if (!first) std::printf(",\n");
            first = false;
            std::printf("{\"policy\":\"%s\",\"eye_fixture\":%u,\"sample_offset\":%.2f,\"scale\":%.2f,\"bias\":%.2f,\"srgb_view\":%s,\"shader_eotf\":%s,\"expected_policy_error_codes\":%.6f,\"once_only_error_codes\":%.6f,\"old_bug_control\":%s,\"gray128_output_code\":%u,\"worst_policy_sample\":{\"x\":%u,\"y\":%u,\"channel\":%u,\"actual\":%u,\"expected\":%.6f},\"pass\":%s}", p.name, eye, offset, scale, bias, p.srgb ? "true" : "false", manual ? "true" : "false", expected_policy.error, once_only.error, p.old_control ? "true" : "false", result[(eye ? 127 : 128) * 4], expected_policy.x, expected_policy.y, expected_policy.channel, expected_policy.actual, expected_policy.expected, pass ? "true" : "false");
        }
        std::printf("\n],\"cases_tested\":%u,\"all_pass\":%s}\n", tested, all ? "true" : "false"); return all ? 0 : 1;
    } catch (const std::exception& e) { std::fprintf(stderr, "color contract probe: %s\n", e.what()); return 2; }
}
