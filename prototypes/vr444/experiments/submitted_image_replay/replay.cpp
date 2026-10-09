// SPDX-License-Identifier: GPL-3.0-or-later
// Bounded private Vulkan graphics replay; no XR/runtime/network interfaces.
#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstring>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>
#include <vulkan/vulkan.h>
namespace fs = std::filesystem;
void need(bool x, const char *message) {
    if (!x)
        throw std::runtime_error(message);
}
std::string quote(const char *text) {
    std::string out = "\"";
    for (const unsigned char c : std::string(text)) {
        need(c >= 32, "JSON control character");
        if (c == 34 || c == 92)
            out += char(92);
        out += char(c);
    }
    return out + "\"";
}
void vkcheck(VkResult x) {
    if (x != VK_SUCCESS)
        throw std::runtime_error("Vulkan result " + std::to_string(x));
}
std::vector<uint8_t> read(const fs::path &p, size_t cap) {
    auto size = fs::file_size(p);
    need(size && size <= cap, "file size bound");
    std::vector<uint8_t> b(size);
    std::ifstream f(p, std::ios::binary);
    f.read((char *)b.data(), b.size());
    need(bool(f), "file read");
    return b;
}
void write(const fs::path &p, const std::vector<uint8_t> &b) {
    need(!fs::exists(p), "output exists");
    std::ofstream f(p, std::ios::binary);
    f.write((const char *)b.data(), b.size());
    f.close();
    need(bool(f), "output write");
}
struct Reader {
    std::vector<uint8_t> b;
    size_t pos = 0;
    uint32_t u32() {
        need(pos + 4 <= b.size(), "short replay job");
        uint32_t v = 0;
        for (unsigned i = 0; i < 4; ++i)
            v |= uint32_t(b[pos++]) << (i * 8);
        return v;
    }
    std::vector<uint8_t> bytes(size_t n) {
        need(n <= b.size() - pos, "short image");
        std::vector<uint8_t> v(b.begin() + pos, b.begin() + pos + n);
        pos += n;
        return v;
    }
};
struct Push {
    int32_t rgb[4], alpha[4];
    float scale[4], bias[4], mask[12];
};
static_assert(sizeof(Push) == 112);
struct Vertex {
    float x, y;
    uint32_t u, v;
};
static_assert(sizeof(Vertex) == 16);
struct Eye {
    uint32_t width, height;
    Push pc{};
    std::vector<uint32_t> x, y;
    std::vector<uint8_t> rgba;
    std::vector<Vertex> vertices;
};
struct Job {
    uint32_t width, height;
    VkFormat format;
    std::array<Eye, 2> eyes;
};
uint64_t pixels(uint32_t w, uint32_t h) {
    need(w && h && w <= 4096 && h <= 4096 && uint64_t(w) * h <= 4194304, "image dimensions");
    return uint64_t(w) * h;
}
uint32_t axis(const std::vector<uint32_t> &a) {
    need(a.size() && a.size() <= 129 && a.size() % 2 == 1, "foveation length");
    uint64_t n = 0;
    for (size_t i = 0; i < a.size(); ++i) {
        need(a[i] <= 4096, "foveation segment bound");
        n += uint64_t(a[i]) * (std::abs(int(a.size() / 2) - int(i)) + 1);
    }
    need(n && n <= 4096, "foveation output bound");
    return n;
}
void geometry(Eye &e, uint32_t w, uint32_t h) {
    // Statement order/float precision mirror stream_defoveator::defoveate.
    float ox = float(-0.5 * w), oy = float(-0.5 * h), sx = float(2. / w), sy = float(2. / h);
    uint32_t u = 0, v = 0;
    for (size_t iy = 0; iy < e.y.size(); ++iy) {
        int ry = std::abs(int(e.y.size() / 2) - int(iy)) + 1;
        u = 0;
        ox = float(-0.5 * w);
        for (size_t ix = 0; ix < e.x.size(); ++ix) {
            int rx = std::abs(int(e.x.size() / 2) - int(ix)) + 1;
            e.vertices.push_back({ox * sx, oy * sy, u, v});
            e.vertices.push_back({ox * sx, (oy + float(e.y[iy] * ry)) * sy, u, v + e.y[iy]});
            u += e.x[ix];
            ox += float(e.x[ix] * rx);
        }
        e.vertices.push_back({ox * sx, oy * sy, u, v});
        v += e.y[iy];
        oy += float(e.y[iy] * ry);
        e.vertices.push_back({ox * sx, oy * sy, u, v});
        e.vertices.push_back({ox * sx, oy * sy, u, v});
    }
    need(e.vertices.size() == (2 * (e.x.size() + 1) + 1) * e.y.size(), "vertex count");
}
Job load(const fs::path &path) {
    Reader r{read(path, 40 * 1024 * 1024)};
    need(r.u32() == 0x31504c52 && r.u32() == 1, "replay job magic/version");
    Job j{};
    j.width = r.u32();
    j.height = r.u32();
    pixels(j.width, j.height);
    j.format = VkFormat(r.u32());
    need(j.format == VK_FORMAT_R8G8B8A8_SRGB || j.format == VK_FORMAT_B8G8R8A8_SRGB, "output format");
    need(r.u32() == 2, "stereo eye count");
    for (auto &e : j.eyes) {
        e.width = r.u32();
        e.height = r.u32();
        auto n = pixels(e.width, e.height);
        auto nx = r.u32(), ny = r.u32();
        need(nx && ny && nx <= 129 && ny <= 129, "foveation count");
        need(r.u32() == 0 && r.u32() == 0, "only alpha=0 / shader_srgb_decode=false supported");
        for (auto &v : e.pc.rgb)
            v = std::bit_cast<int32_t>(r.u32());
        for (auto &v : e.pc.alpha)
            v = std::bit_cast<int32_t>(r.u32());
        for (float *a : {e.pc.scale, e.pc.bias, e.pc.mask}) {
            unsigned count = a == e.pc.mask ? 12 : 4;
            for (unsigned i = 0; i < count; ++i) {
                a[i] = std::bit_cast<float>(r.u32());
                need(std::isfinite(a[i]), "nonfinite constant");
            }
        }
        need(e.pc.rgb[0] == 0 && e.pc.rgb[1] == 0 && e.pc.rgb[2] == int(e.width) &&
                 e.pc.rgb[3] == int(e.height),
             "only complete source rect supported");
        for (unsigned i = 0; i < nx; ++i)
            e.x.push_back(r.u32());
        for (unsigned i = 0; i < ny; ++i)
            e.y.push_back(r.u32());
        need(axis(e.x) == j.width && axis(e.y) == j.height, "output geometry mismatch");
        uint64_t sx = 0, sy = 0;
        for (auto n : e.x)
            sx += n;
        for (auto n : e.y)
            sy += n;
        need(sx == e.width && sy == e.height, "source geometry mismatch");
        e.rgba = r.bytes(n * 4);
        for (size_t i = 3; i < e.rgba.size(); i += 4)
            need(e.rgba[i] == 255, "opaque source required");
        geometry(e, j.width, j.height);
    }
    need(r.pos == r.b.size(), "trailing replay bytes");
    return j;
}
struct Buffer {
    VkBuffer h{};
    VkDeviceMemory m{};
    void *map{};
    VkDeviceSize size{};
};
struct Image {
    VkImage h{};
    VkDeviceMemory m{};
    VkImageView v{};
};
struct GPU {
    VkInstance instance{};
    VkPhysicalDevice physical{};
    VkPhysicalDeviceProperties props{};
    VkPhysicalDeviceMemoryProperties memory{};
    VkDevice d{};
    VkQueue queue{};
    uint32_t family{};
    VkCommandPool pool{};
    VkCommandBuffer cmd{};
    VkFence fence{};
    VkSampler sampler{};
    VkRenderPass pass{};
    VkDescriptorSetLayout ds_layout{};
    VkDescriptorPool ds_pool{};
    VkPipelineLayout layout{};
    VkPipeline pipeline{};
    VkShaderModule vs{}, ps{};
    std::array<Buffer, 2> upload{}, vertex{}, output{};
    std::array<Image, 2> input{};
    Image target{};
    std::array<VkImageView, 2> target_views{};
    std::array<VkFramebuffer, 2> fb{};
    std::array<VkDescriptorSet, 2> sets{};
    ~GPU() {
        if (d) {
            auto result = vkDeviceWaitIdle(d);
            if (result != VK_SUCCESS && result != VK_ERROR_DEVICE_LOST)
                std::terminate();
            if (pipeline)
                vkDestroyPipeline(d, pipeline, nullptr);
            if (layout)
                vkDestroyPipelineLayout(d, layout, nullptr);
            if (vs)
                vkDestroyShaderModule(d, vs, nullptr);
            if (ps)
                vkDestroyShaderModule(d, ps, nullptr);
            for (auto v : fb)
                if (v)
                    vkDestroyFramebuffer(d, v, nullptr);
            if (pass)
                vkDestroyRenderPass(d, pass, nullptr);
            if (ds_pool)
                vkDestroyDescriptorPool(d, ds_pool, nullptr);
            if (ds_layout)
                vkDestroyDescriptorSetLayout(d, ds_layout, nullptr);
            if (sampler)
                vkDestroySampler(d, sampler, nullptr);
            if (fence)
                vkDestroyFence(d, fence, nullptr);
            if (pool)
                vkDestroyCommandPool(d, pool, nullptr);
            for (auto v : target_views)
                if (v)
                    vkDestroyImageView(d, v, nullptr);
            auto drop_image = [&](Image i) {
                if (i.v)
                    vkDestroyImageView(d, i.v, nullptr);
                if (i.h)
                    vkDestroyImage(d, i.h, nullptr);
                if (i.m)
                    vkFreeMemory(d, i.m, nullptr);
            };
            for (auto i : input)
                drop_image(i);
            drop_image(target);
            for (auto array : {upload, vertex, output})
                for (auto b : array) {
                    if (b.map)
                        vkUnmapMemory(d, b.m);
                    if (b.h)
                        vkDestroyBuffer(d, b.h, nullptr);
                    if (b.m)
                        vkFreeMemory(d, b.m, nullptr);
                }
            vkDestroyDevice(d, nullptr);
        }
        if (instance)
            vkDestroyInstance(instance, nullptr);
    }
    uint32_t type(uint32_t bits, VkMemoryPropertyFlags flags) {
        for (uint32_t i = 0; i < memory.memoryTypeCount; ++i)
            if ((bits & (1u << i)) && (memory.memoryTypes[i].propertyFlags & flags) == flags)
                return i;
        throw std::runtime_error("memory type unavailable");
    }
    void buffer(Buffer &b, VkDeviceSize size, VkBufferUsageFlags usage) {
        b.size = size;
        VkBufferCreateInfo ci{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
        ci.size = size;
        ci.usage = usage;
        vkcheck(vkCreateBuffer(d, &ci, nullptr, &b.h));
        VkMemoryRequirements r;
        vkGetBufferMemoryRequirements(d, b.h, &r);
        VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
        ai.allocationSize = r.size;
        ai.memoryTypeIndex = type(r.memoryTypeBits,
                                  VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
        vkcheck(vkAllocateMemory(d, &ai, nullptr, &b.m));
        vkcheck(vkBindBufferMemory(d, b.h, b.m, 0));
        vkcheck(vkMapMemory(d, b.m, 0, size, 0, &b.map));
    }
    void image(Image &i, uint32_t w, uint32_t h, VkFormat format, uint32_t layers, VkImageUsageFlags usage) {
        VkImageFormatProperties supported{};
        vkcheck(vkGetPhysicalDeviceImageFormatProperties(physical, format, VK_IMAGE_TYPE_2D,
                                                         VK_IMAGE_TILING_OPTIMAL, usage, 0, &supported));
        need(w <= supported.maxExtent.width && h <= supported.maxExtent.height &&
                 layers <= supported.maxArrayLayers,
             "image format size unsupported");
        VkImageCreateInfo ci{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
        ci.imageType = VK_IMAGE_TYPE_2D;
        ci.format = format;
        ci.extent = {w, h, 1};
        ci.mipLevels = 1;
        ci.arrayLayers = layers;
        ci.samples = VK_SAMPLE_COUNT_1_BIT;
        ci.tiling = VK_IMAGE_TILING_OPTIMAL;
        ci.usage = usage;
        vkcheck(vkCreateImage(d, &ci, nullptr, &i.h));
        VkMemoryRequirements r;
        vkGetImageMemoryRequirements(d, i.h, &r);
        VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
        ai.allocationSize = r.size;
        ai.memoryTypeIndex = type(r.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
        vkcheck(vkAllocateMemory(d, &ai, nullptr, &i.m));
        vkcheck(vkBindImageMemory(d, i.h, i.m, 0));
    }
    VkImageView view(VkImage image, VkFormat format, unsigned layer, bool array) {
        VkImageViewCreateInfo ci{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
        ci.image = image;
        ci.viewType = array ? VK_IMAGE_VIEW_TYPE_2D_ARRAY : VK_IMAGE_VIEW_TYPE_2D;
        ci.format = format;
        ci.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, layer, 1};
        VkImageView v{};
        vkcheck(vkCreateImageView(d, &ci, nullptr, &v));
        return v;
    }
    VkShaderModule shader(const fs::path &path) {
        auto bytes = read(path, 1024 * 1024);
        need(bytes.size() % 4 == 0, "shader alignment");
        std::vector<uint32_t> words(bytes.size() / 4);
        std::memcpy(words.data(), bytes.data(), bytes.size());
        need(words[0] == 0x07230203, "shader magic");
        VkShaderModuleCreateInfo ci{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
        ci.codeSize = bytes.size();
        ci.pCode = words.data();
        VkShaderModule s{};
        vkcheck(vkCreateShaderModule(d, &ci, nullptr, &s));
        return s;
    }
    void init(const Job &j, uint32_t selected, const fs::path &vert, const fs::path &frag) {
        VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
        app.pApplicationName = "Bound native submitted-image replay";
        app.apiVersion = VK_API_VERSION_1_1;
        VkInstanceCreateInfo ic{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
        ic.pApplicationInfo = &app;
        vkcheck(vkCreateInstance(&ic, nullptr, &instance));
        uint32_t count = 0;
        vkcheck(vkEnumeratePhysicalDevices(instance, &count, nullptr));
        std::vector<VkPhysicalDevice> ds(count);
        vkcheck(vkEnumeratePhysicalDevices(instance, &count, ds.data()));
        need(selected < count, "device index");
        physical = ds[selected];
        vkGetPhysicalDeviceProperties(physical, &props);
        vkGetPhysicalDeviceMemoryProperties(physical, &memory);
        need(props.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU ||
                 props.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU,
             "hardware GPU required");
        need(props.apiVersion >= VK_API_VERSION_1_1 && props.limits.maxPushConstantsSize >= sizeof(Push) &&
                 props.limits.maxFramebufferWidth >= j.width &&
                 props.limits.maxFramebufferHeight >= j.height && props.limits.maxImageArrayLayers >= 2,
             "device limits");
        VkFormatProperties fp;
        vkGetPhysicalDeviceFormatProperties(physical, VK_FORMAT_R8G8B8A8_SRGB, &fp);
        need((fp.optimalTilingFeatures & VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT) != 0,
             "SRGB linear sampling unsupported");
        vkGetPhysicalDeviceQueueFamilyProperties(physical, &count, nullptr);
        std::vector<VkQueueFamilyProperties> qs(count);
        vkGetPhysicalDeviceQueueFamilyProperties(physical, &count, qs.data());
        while (family < count && !(qs[family].queueFlags & VK_QUEUE_GRAPHICS_BIT))
            ++family;
        need(family < count, "graphics queue unavailable");
        float priority = 1;
        VkDeviceQueueCreateInfo qi{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
        qi.queueFamilyIndex = family;
        qi.queueCount = 1;
        qi.pQueuePriorities = &priority;
        VkDeviceCreateInfo dc{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
        dc.queueCreateInfoCount = 1;
        dc.pQueueCreateInfos = &qi;
        vkcheck(vkCreateDevice(physical, &dc, nullptr, &d));
        vkGetDeviceQueue(d, family, 0, &queue);
        VkCommandPoolCreateInfo cp{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
        cp.queueFamilyIndex = family;
        vkcheck(vkCreateCommandPool(d, &cp, nullptr, &pool));
        VkCommandBufferAllocateInfo ca{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
        ca.commandPool = pool;
        ca.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
        ca.commandBufferCount = 1;
        vkcheck(vkAllocateCommandBuffers(d, &ca, &cmd));
        VkFenceCreateInfo fc{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
        vkcheck(vkCreateFence(d, &fc, nullptr, &fence));
        VkSamplerCreateInfo si{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
        si.magFilter = si.minFilter = VK_FILTER_LINEAR;
        si.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
        si.addressModeU = si.addressModeV = si.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
        vkcheck(vkCreateSampler(d, &si, nullptr, &sampler));
        image(target, j.width, j.height, j.format, 2,
              VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT);
        for (unsigned eye = 0; eye < 2; ++eye) {
            const auto &e = j.eyes[eye];
            buffer(upload[eye], e.rgba.size(), VK_BUFFER_USAGE_TRANSFER_SRC_BIT);
            std::memcpy(upload[eye].map, e.rgba.data(), e.rgba.size());
            buffer(vertex[eye], e.vertices.size() * sizeof(Vertex), VK_BUFFER_USAGE_VERTEX_BUFFER_BIT);
            std::memcpy(vertex[eye].map, e.vertices.data(), vertex[eye].size);
            buffer(output[eye], uint64_t(j.width) * j.height * 4, VK_BUFFER_USAGE_TRANSFER_DST_BIT);
            std::memset(output[eye].map, 0x5a, output[eye].size);
            image(input[eye], e.width, e.height, VK_FORMAT_R8G8B8A8_SRGB, 1,
                  VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT);
            input[eye].v = view(input[eye].h, VK_FORMAT_R8G8B8A8_SRGB, 0, false);
            target_views[eye] = view(target.h, j.format, eye, true);
        }
        VkAttachmentDescription attachment{};
        attachment.format = j.format;
        attachment.samples = VK_SAMPLE_COUNT_1_BIT;
        attachment.loadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE;
        attachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
        attachment.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
        attachment.finalLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
        VkAttachmentReference color{0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
        VkSubpassDescription sub{};
        sub.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS;
        sub.colorAttachmentCount = 1;
        sub.pColorAttachments = &color;
        VkRenderPassCreateInfo rp{VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO};
        rp.attachmentCount = 1;
        rp.pAttachments = &attachment;
        rp.subpassCount = 1;
        rp.pSubpasses = &sub;
        vkcheck(vkCreateRenderPass(d, &rp, nullptr, &pass));
        for (unsigned eye = 0; eye < 2; ++eye) {
            VkFramebufferCreateInfo fi{VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO};
            fi.renderPass = pass;
            fi.attachmentCount = 1;
            fi.pAttachments = &target_views[eye];
            fi.width = j.width;
            fi.height = j.height;
            fi.layers = 1;
            vkcheck(vkCreateFramebuffer(d, &fi, nullptr, &fb[eye]));
        }
        VkDescriptorSetLayoutBinding binding{0, VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 1,
                                             VK_SHADER_STAGE_FRAGMENT_BIT, &sampler};
        VkDescriptorSetLayoutCreateInfo dl{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
        dl.bindingCount = 1;
        dl.pBindings = &binding;
        vkcheck(vkCreateDescriptorSetLayout(d, &dl, nullptr, &ds_layout));
        VkDescriptorPoolSize size{VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 2};
        VkDescriptorPoolCreateInfo dp{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
        dp.maxSets = 2;
        dp.poolSizeCount = 1;
        dp.pPoolSizes = &size;
        vkcheck(vkCreateDescriptorPool(d, &dp, nullptr, &ds_pool));
        std::array<VkDescriptorSetLayout, 2> layouts{ds_layout, ds_layout};
        VkDescriptorSetAllocateInfo da{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
        da.descriptorPool = ds_pool;
        da.descriptorSetCount = 2;
        da.pSetLayouts = layouts.data();
        vkcheck(vkAllocateDescriptorSets(d, &da, sets.data()));
        for (unsigned eye = 0; eye < 2; ++eye) {
            VkDescriptorImageInfo image_info{sampler, input[eye].v, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL};
            VkWriteDescriptorSet w{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
            w.dstSet = sets[eye];
            w.descriptorCount = 1;
            w.descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
            w.pImageInfo = &image_info;
            vkUpdateDescriptorSets(d, 1, &w, 0, nullptr);
        }
        VkPushConstantRange pc{VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, sizeof(Push)};
        VkPipelineLayoutCreateInfo pl{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
        pl.setLayoutCount = 1;
        pl.pSetLayouts = &ds_layout;
        pl.pushConstantRangeCount = 1;
        pl.pPushConstantRanges = &pc;
        vkcheck(vkCreatePipelineLayout(d, &pl, nullptr, &layout));
        vs = shader(vert);
        ps = shader(frag);
        std::array<uint32_t, 2> data{0, 0};
        std::array<VkSpecializationMapEntry, 2> entries{{{0, 0, 4}, {1, 4, 4}}};
        VkSpecializationInfo spec{2, entries.data(), sizeof(data), data.data()};
        std::array<VkPipelineShaderStageCreateInfo, 2> stages{};
        for (auto &s : stages) {
            s.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
            s.pName = "main";
        }
        stages[0].stage = VK_SHADER_STAGE_VERTEX_BIT;
        stages[0].module = vs;
        stages[1].stage = VK_SHADER_STAGE_FRAGMENT_BIT;
        stages[1].module = ps;
        stages[1].pSpecializationInfo = &spec;
        VkVertexInputBindingDescription vb{0, sizeof(Vertex), VK_VERTEX_INPUT_RATE_VERTEX};
        std::array<VkVertexInputAttributeDescription, 2> attrs{
            {{0, 0, VK_FORMAT_R32G32_SFLOAT, 0}, {1, 0, VK_FORMAT_R32G32_UINT, 8}}};
        VkPipelineVertexInputStateCreateInfo vi{VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO};
        vi.vertexBindingDescriptionCount = 1;
        vi.pVertexBindingDescriptions = &vb;
        vi.vertexAttributeDescriptionCount = 2;
        vi.pVertexAttributeDescriptions = attrs.data();
        VkPipelineInputAssemblyStateCreateInfo ia{
            VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO};
        ia.topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_STRIP;
        VkViewport viewport{0, 0, float(j.width), float(j.height), 0, 1};
        VkRect2D scissor{{0, 0}, {j.width, j.height}};
        VkPipelineViewportStateCreateInfo vp{VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO};
        vp.viewportCount = 1;
        vp.pViewports = &viewport;
        vp.scissorCount = 1;
        vp.pScissors = &scissor;
        VkPipelineRasterizationStateCreateInfo ra{VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO};
        ra.polygonMode = VK_POLYGON_MODE_FILL;
        ra.lineWidth = 1;
        VkPipelineMultisampleStateCreateInfo ms{VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO};
        ms.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;
        VkPipelineColorBlendAttachmentState ba{};
        ba.colorWriteMask = 15;
        VkPipelineColorBlendStateCreateInfo blend{VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO};
        blend.attachmentCount = 1;
        blend.pAttachments = &ba;
        VkGraphicsPipelineCreateInfo pi{VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO};
        pi.stageCount = 2;
        pi.pStages = stages.data();
        pi.pVertexInputState = &vi;
        pi.pInputAssemblyState = &ia;
        pi.pViewportState = &vp;
        pi.pRasterizationState = &ra;
        pi.pMultisampleState = &ms;
        pi.pColorBlendState = &blend;
        pi.layout = layout;
        pi.renderPass = pass;
        vkcheck(vkCreateGraphicsPipelines(d, VK_NULL_HANDLE, 1, &pi, nullptr, &pipeline));
    }
    void run(const Job &j, bool omit_right) {
        VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
        begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
        vkcheck(vkBeginCommandBuffer(cmd, &begin));
        VkMemoryBarrier host{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
        host.srcAccessMask = VK_ACCESS_HOST_WRITE_BIT;
        host.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_VERTEX_ATTRIBUTE_READ_BIT;
        vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_HOST_BIT,
                             VK_PIPELINE_STAGE_TRANSFER_BIT | VK_PIPELINE_STAGE_VERTEX_INPUT_BIT, 0, 1, &host,
                             0, nullptr, 0, nullptr);
        for (unsigned eye = 0; eye < 2; ++eye) {
            VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
            b.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
            b.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
            b.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
            b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            b.image = input[eye].h;
            b.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
            vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0,
                                 nullptr, 0, nullptr, 1, &b);
            VkBufferImageCopy copy{};
            copy.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
            copy.imageExtent = {j.eyes[eye].width, j.eyes[eye].height, 1};
            vkCmdCopyBufferToImage(cmd, upload[eye].h, input[eye].h, b.newLayout, 1, &copy);
            b.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
            b.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
            b.oldLayout = b.newLayout;
            b.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
            vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT,
                                 0, 0, nullptr, 0, nullptr, 1, &b);
            if (omit_right && eye == 1)
                continue;
            VkRenderPassBeginInfo rp{VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO};
            rp.renderPass = pass;
            rp.framebuffer = fb[eye];
            rp.renderArea = {{0, 0}, {j.width, j.height}};
            vkCmdBeginRenderPass(cmd, &rp, VK_SUBPASS_CONTENTS_INLINE);
            vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline);
            vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, layout, 0, 1, &sets[eye], 0,
                                    nullptr);
            vkCmdPushConstants(cmd, layout, VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0,
                               sizeof(Push), &j.eyes[eye].pc);
            VkDeviceSize off = 0;
            vkCmdBindVertexBuffers(cmd, 0, 1, &vertex[eye].h, &off);
            vkCmdDraw(cmd, j.eyes[eye].vertices.size(), 1, 0, 0);
            vkCmdEndRenderPass(cmd);
            b.srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;
            b.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
            b.oldLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
            b.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
            b.image = target.h;
            b.subresourceRange.baseArrayLayer = eye;
            vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
                                 VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 0, nullptr, 1, &b);
            copy.imageSubresource.baseArrayLayer = eye;
            copy.imageExtent = {j.width, j.height, 1};
            vkCmdCopyImageToBuffer(cmd, target.h, b.newLayout, output[eye].h, 1, &copy);
        }
        VkMemoryBarrier visibility{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
        visibility.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
        visibility.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
        vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 1,
                             &visibility, 0, nullptr, 0, nullptr);
        vkcheck(vkEndCommandBuffer(cmd));
        VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
        si.commandBufferCount = 1;
        si.pCommandBuffers = &cmd;
        vkcheck(vkQueueSubmit(queue, 1, &si, fence));
        vkcheck(vkWaitForFences(d, 1, &fence, VK_TRUE, UINT64_MAX));
    }
};
int main(int argc, char **argv) {
    try {
        need(argc >= 2,
             "usage: replay JOB --cpu-only | replay JOB VERT_SPV FRAG_SPV OUT_PREFIX DEVICE [--omit-right]");
        auto job = load(argv[1]);
        if (argc == 3 && std::string(argv[2]) == "--cpu-only") {
            std::cout << "{\"job_valid\":true,\"eyes\":2,\"gpu_used\":false}\n";
            return 0;
        }
        need(argc == 6 || argc == 7, "argument count");
        bool omit = argc == 7;
        need(!omit || std::string(argv[6]) == "--omit-right", "unknown control");
        size_t end = 0;
        auto device = std::stoul(argv[5], &end);
        need(end == std::strlen(argv[5]) && device < 16, "device index");
        GPU gpu;
        gpu.init(job, device, argv[2], argv[3]);
        gpu.run(job, omit);
        for (unsigned eye = 0; eye < 2; ++eye) {
            auto p = (uint8_t *)gpu.output[eye].map;
            write(std::string(argv[4]) + "-eye-" + std::to_string(eye) + ".raw",
                  {p, p + gpu.output[eye].size});
        }
        std::cout << "{\"replay_completed\":true,\"eyes\":2,\"device\":" << quote(gpu.props.deviceName)
                  << ",\"vendor_id\":" << gpu.props.vendorID << ",\"device_id\":" << gpu.props.deviceID
                  << ",\"driver\":" << gpu.props.driverVersion << ",\"api_version\":" << gpu.props.apiVersion
                  << ",\"device_type\":" << gpu.props.deviceType << ",\"queue_family\":" << gpu.family
                  << ",\"width\":" << job.width << ",\"height\":" << job.height
                  << ",\"vk_format\":" << job.format << ",\"omission_control\":" << (omit ? "true" : "false")
                  << ",\"openxr_used\":false,\"quality_qualified\":false,\"performance_claim\":false}\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << "submitted-image-replay: " << e.what() << '\n';
        return 1;
    }
}
