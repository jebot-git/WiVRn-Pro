// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#ifdef WIVRN_CODEC_TESTS
#include "codec_test/submitted_capture.h"
#include "vk/allocation.h"
#include <cmath>
#include <iomanip>
#include <locale>
#include <sstream>
#include <vulkan/vulkan_raii.hpp>

// Render-thread only. Owned after the stream fence/command buffer; the stream
// terminal queue drain runs before this object's buffers can be destroyed.
// Copy + fence wait happens while the XR image is still acquired. File exposure
// waits for successful xrEndFrame, so these are submitted images, not photons.
class stream_quality_capture {
    using Config = wivrn::codec_test::submitted_capture::Config;
    wivrn::codec_test::submitted_capture::Budget budget_;
    std::filesystem::path folder_;
    std::array<buffer_allocation, 2> buffers_;
    std::array<uint8_t*, 2> mapped_{};
    std::array<XrExtent2Di, 2> extents_{};
    std::array<uint64_t, 2> bytes_{};
    vk::Format format_{};
    uint32_t image_index_ = 0;
    bool active_ = false, pending_ = false, observed_ = false;

public:
    explicit stream_quality_capture(const std::filesystem::path& config_root) :
        budget_{wivrn::codec_test::submitted_capture::load(config_root/"codec-test-quality-capture.json")},
        folder_(config_root/"codec-test/quality") {
        if (enabled()) {
            std::filesystem::create_directories(folder_.parent_path());
            if (!std::filesystem::create_directory(folder_))
                throw std::runtime_error("quality capture output directory already exists");
        }
    }
    bool enabled() const { return budget_.config.enabled; }
    void cancel_ready() {
        if (pending_) throw std::runtime_error("quality capture prior queue completion unknown");
        active_=observed_=false;
    }
    bool record(vk::raii::Device& device, vk::raii::CommandBuffer& command,
                vk::Image image, vk::Format format, uint32_t image_index,
                std::array<XrExtent2Di,2> extents, uint64_t frame) {
        if (pending_) throw std::runtime_error("quality capture prior queue completion unknown");
        active_ = observed_ = false; // Prior failed xrEndFrame has no completion marker.
        if (!budget_.take(frame)) return false;
        if (format != vk::Format::eR8G8B8A8Srgb && format != vk::Format::eB8G8R8A8Srgb)
            throw std::runtime_error("quality capture unsupported actual swapchain format");
        if (extents[0].width != extents[1].width || extents[0].height != extents[1].height)
            throw std::runtime_error("quality capture unequal viewports require a separate ray mapping");
        format_ = format; image_index_ = image_index; extents_ = extents;
        for (unsigned eye=0; eye<2; ++eye) {
            bytes_[eye] = wivrn::codec_test::submitted_capture::bytes(extents[eye].width,extents[eye].height);
            buffers_[eye] = buffer_allocation(device,
                vk::BufferCreateInfo{.size=bytes_[eye],.usage=vk::BufferUsageFlagBits::eTransferDst},
                VmaAllocationCreateInfo{.requiredFlags=VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
                                        .preferredFlags=VK_MEMORY_PROPERTY_HOST_CACHED_BIT},
                "codec submitted-image readback");
            mapped_[eye] = buffers_[eye].data<uint8_t>();
        }
        vk::ImageMemoryBarrier transfer{
            .srcAccessMask=vk::AccessFlagBits::eColorAttachmentWrite,
            .dstAccessMask=vk::AccessFlagBits::eTransferRead,
            .oldLayout=vk::ImageLayout::eColorAttachmentOptimal,
            .newLayout=vk::ImageLayout::eTransferSrcOptimal,
            .srcQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,
            .image=image,
            .subresourceRange={.aspectMask=vk::ImageAspectFlagBits::eColor,.levelCount=1,.layerCount=2}};
        command.pipelineBarrier(vk::PipelineStageFlagBits::eColorAttachmentOutput,
                                vk::PipelineStageFlagBits::eTransfer,{}, {}, {}, transfer);
        std::array<vk::BufferMemoryBarrier,2> host;
        for (unsigned eye=0; eye<2; ++eye) {
            const vk::BufferImageCopy region{
                .imageSubresource={.aspectMask=vk::ImageAspectFlagBits::eColor,.mipLevel=0,.baseArrayLayer=eye,.layerCount=1},
                .imageExtent={uint32_t(extents[eye].width),uint32_t(extents[eye].height),1}};
            command.copyImageToBuffer(image,vk::ImageLayout::eTransferSrcOptimal,vk::Buffer(buffers_[eye]),region);
            host[eye] = vk::BufferMemoryBarrier{
                .srcAccessMask=vk::AccessFlagBits::eTransferWrite,.dstAccessMask=vk::AccessFlagBits::eHostRead,
                .srcQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,
                .buffer=vk::Buffer(buffers_[eye]),.offset=0,.size=bytes_[eye]};
        }
        command.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer,vk::PipelineStageFlagBits::eHost,{}, {},host,{});
        transfer.srcAccessMask=vk::AccessFlagBits::eTransferRead;
        transfer.dstAccessMask=vk::AccessFlagBits::eColorAttachmentRead|vk::AccessFlagBits::eColorAttachmentWrite;
        transfer.oldLayout=vk::ImageLayout::eTransferSrcOptimal;
        transfer.newLayout=vk::ImageLayout::eColorAttachmentOptimal;
        command.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer,vk::PipelineStageFlagBits::eColorAttachmentOutput,{}, {}, {},transfer);
        active_=true;
        return true;
    }
    void before_submit() { if (active_) pending_=true; }
    void observe(vk::raii::Device& device, vk::Fence fence) {
        if (!active_) return;
        if (!pending_) throw std::runtime_error("quality capture missing queue submit");
        // External acquisition watchdog bounds a broken driver. Never release an
        // in-flight readback allocation on a finite-timeout exception.
        if (device.waitForFences(fence,VK_TRUE,UINT64_MAX) != vk::Result::eSuccess)
            throw std::runtime_error("quality capture readback fence not completed");
        pending_=false; observed_=true;
    }
    static void number(std::ostream& out, float n) {
        if (!std::isfinite(n)) throw std::runtime_error("quality capture nonfinite metadata");
        out << n;
    }
    static void fov(std::ostream& out, const XrFovf& value) {
        out << '['; number(out,value.angleLeft); out << ','; number(out,value.angleRight);
        out << ','; number(out,value.angleDown); out << ','; number(out,value.angleUp); out << ']';
    }
    static void pose(std::ostream& out, const XrPosef& value) {
        out << "{\"position\":["; number(out,value.position.x);out<<',';number(out,value.position.y);out<<',';number(out,value.position.z);
        out << "],\"orientation_xyzw\":["; number(out,value.orientation.x);out<<',';number(out,value.orientation.y);out<<',';number(out,value.orientation.z);out<<',';number(out,value.orientation.w);out<<"]}";
    }
    template<class Handles, class Inputs, class Masks, class Description>
    void finish(const Handles& handles, const Inputs& inputs, const Masks& masks,
                const Description& description, const std::array<XrCompositionLayerProjectionView,2>& views,
                XrTime submitted, XrTime predicted, float scale, float bias, bool alpha,
                uint64_t requested_filter, uint64_t submitted_filter, bool filter_extension, bool hmd_srgb_conversion) {
        if (!active_) return;
        if (pending_ || !observed_ || submitted<=0) throw std::runtime_error("quality capture completion contract");
        const std::string stem="capture-"+std::to_string(budget_.attempts);
        std::array<std::string,2> metadata;
        for (unsigned eye=0; eye<2; ++eye) {
            const auto& handle=handles[eye]; const auto& view=views[eye];
            if (!handle || view.subImage.imageArrayIndex!=eye || view.subImage.imageRect.offset.x || view.subImage.imageRect.offset.y
                || view.subImage.imageRect.extent.width!=extents_[eye].width || view.subImage.imageRect.extent.height!=extents_[eye].height)
                throw std::runtime_error("quality capture submitted subimage mismatch");
            std::ostringstream out; out.imbue(std::locale::classic());out<<std::setprecision(9);
            const bool test_sampler=description.codec[eye]==wivrn::video_codec::raw;
            if(test_sampler && (!handle->codec_test_sampler_bound || handle->codec_test_rgb_sampler!=inputs[eye].sampler_rgb ||
                (handle->codec_test_rgb_filter!=vk::Filter::eLinear && handle->codec_test_rgb_filter!=vk::Filter::eNearest)))
                throw std::runtime_error("quality capture actual test sampler identity mismatch");
            const char* sampler_filter=handle->codec_test_rgb_filter==vk::Filter::eNearest ? "NEAREST" : "LINEAR";
            out << "{\"schema\":1,\"capture_id\":"<<budget_.attempts<<",\"source_boundary\":\"submitted-image\",\"eye\":"<<eye
                <<",\"frame\":"<<handle->feedback.frame_index<<",\"warp\":"<<wivrn::codec_test::warp_hash(handle->view_info)
                <<",\"decoder_generation_first_frame\":"<<description.first_frame
                <<",\"raw_file\":\""<<stem<<"-eye-"<<eye<<".raw\",\"bytes\":"<<bytes_[eye]
                <<",\"width\":"<<extents_[eye].width<<",\"height\":"<<extents_[eye].height
                <<",\"row_bytes\":"<<extents_[eye].width*4<<",\"row_order\":\"top-to-bottom\",\"channel_order\":\""
                <<(format_==vk::Format::eR8G8B8A8Srgb?"RGBA":"BGRA")<<"\",\"vk_format\":"<<int(format_)
                <<",\"srgb_encoded_rgb\":true,\"alpha_unorm\":true,\"canonical_rgba\":"<<(format_==vk::Format::eR8G8B8A8Srgb?"true":"false")
                <<",\"swapchain_image_index\":"<<image_index_<<",\"array_layer\":"<<eye
                <<",\"subimage_rect\":[0,0,"<<extents_[eye].width<<','<<extents_[eye].height<<']'
                <<",\"successful_submit_xr_ns\":"<<submitted<<",\"predicted_display_xr_ns\":"<<predicted
                <<",\"readback_fence_observed\":true,\"projection_submitted\":true,\"transfer_src_usage\":true"
                <<",\"render_fov_left_right_down_up_radians\":";fov(out,view.fov);
            out << ",\"encoded_input_fov_left_right_down_up_radians\":";fov(out,handle->view_info.fov[eye]);
            out << ",\"unwarped_fov\":";fov(out,view.fov);
            out << ",\"render_pose\":";pose(out,view.pose);
            out << ",\"encoded_input_pose\":";pose(out,handle->view_info.pose[eye]);
            out << ",\"encoded_input_extent\":["<<inputs[eye].rect_rgb.extent.width<<','<<inputs[eye].rect_rgb.extent.height<<']';
            out << ",\"rect_rgb\":["<<inputs[eye].rect_rgb.offset.x<<','<<inputs[eye].rect_rgb.offset.y<<','<<inputs[eye].rect_rgb.extent.width<<','<<inputs[eye].rect_rgb.extent.height<<']'
                <<",\"rect_a\":["<<inputs[eye].rect_a.offset.x<<','<<inputs[eye].rect_a.offset.y<<','<<inputs[eye].rect_a.extent.width<<','<<inputs[eye].rect_a.extent.height<<']'
                <<",\"alpha_image_bound\":"<<(inputs[eye].a?"true":"false")<<",\"alpha_sampler_bound\":"<<(inputs[eye].sampler_a?"true":"false")
                <<",\"shader_alpha_specialization\":"<<(inputs[eye].sampler_a?1:0)
                <<",\"hmd_needs_srgb_conversion\":"<<(hmd_srgb_conversion?"true":"false")
                <<",\"shader_srgb_decode\":"<<(hmd_srgb_conversion && !inputs[eye].sampled_rgb_is_linear?"true":"false")
                <<",\"input_image_layout\":"<<int(inputs[eye].layout_rgb)<<",\"input_view_format_source_bound\":"<<(test_sampler?"true":"false")
                <<",\"input_view_vk_format\":";
            if(test_sampler)out<<int(vk::Format::eR8G8B8A8Srgb);else out<<"null";
            out << ",\"shader_load_names\":[\"reprojection.vert\",\"reprojection.frag\"],\"shader_bytes_bound_by_collector\":false";
            out << ",\"foveation_x\":[";bool comma=false;
            for(auto n:handle->view_info.foveation[eye].x){if(comma)out<<',';out<<n;comma=true;}
            out << "],\"foveation_y\":[";comma=false;
            for(auto n:handle->view_info.foveation[eye].y){if(comma)out<<',';out<<n;comma=true;}
            out << "],\"scale_rgba\":[";number(out,scale);out<<',';number(out,scale);out<<',';number(out,scale);out<<",1],\"bias_rgba\":[";
            number(out,bias);out<<',';number(out,bias);out<<',';number(out,bias);out<<",0],\"cutout_mask\":[";
            for(unsigned row=0;row<3;++row){if(row)out<<',';out<<'[';for(unsigned col=0;col<4;++col){if(col)out<<',';number(out,masks[eye][row][col]);}out<<']';}
            out << "],\"output_alpha\":"<<(alpha?"true":"false")<<",\"sampled_rgb_is_linear\":"<<(inputs[eye].sampled_rgb_is_linear?"true":"false")
                <<",\"sampler_contract_source_bound\":"<<(test_sampler?"true":"false")
                <<",\"sampler_runtime_queried\":false,\"sampler_contract\":";
            if(test_sampler) out<<"{\"origin\":\"test_decoder::rgb_sampler\",\"min\":\""<<sampler_filter<<"\",\"mag\":\""<<sampler_filter<<"\",\"mipmap\":\"NEAREST\",\"address_uvw\":\"CLAMP_TO_EDGE\",\"normalized_coordinates\":true,\"min_lod\":0,\"max_lod\":0,\"lod_bias\":0,\"anisotropy\":false,\"max_anisotropy\":0,\"compare\":false,\"compare_op\":\"NEVER\",\"border_color\":\"FLOAT_TRANSPARENT_BLACK\"}";
            else out<<"null";
            out << ",\"requested_filter_flags\":"<<requested_filter<<",\"submitted_filter_flags\":"<<submitted_filter
                <<",\"filter_extension_enabled\":"<<(filter_extension?"true":"false")
                <<",\"runtime_filter_effect_verified\":false,\"displayed_pixels_verified\":false,\"independent_reference\":false,\"quality_pass\":false,\"latency_eligible\":false}\n";
            metadata[eye]=out.str();
        }
        // Validate both metadata objects before any file is exposed. Completion
        // marker last: a disk error cannot turn a partial stereo pair into pass.
        for(unsigned eye=0;eye<2;++eye) {
            wivrn::codec_test::submitted_capture::exclusive_write(folder_/(stem+"-eye-"+std::to_string(eye)+".raw"),std::span(mapped_[eye],bytes_[eye]));
            wivrn::codec_test::submitted_capture::exclusive_write(folder_/(stem+"-eye-"+std::to_string(eye)+".json"),metadata[eye]);
        }
        wivrn::codec_test::submitted_capture::exclusive_write(folder_/(stem+"-pair.json"),
            "{\"schema\":1,\"capture_id\":"+std::to_string(budget_.attempts)+",\"complete_eyes\":[0,1],\"source_boundary\":\"submitted-image\",\"quality_pass\":false,\"latency_eligible\":false}\n");
        active_=false;
    }
};
#endif
