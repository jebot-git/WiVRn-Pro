/*
 * WiVRn VR streaming
 * Copyright (C) 2022  Guillaume Meunier <guillaume.meunier@centraliens.net>
 * Copyright (C) 2022  Patrick Nicolas <patricknicolas@laposte.net>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

#include "android_decoder.h"
#include "application.h"
#include "scenes/stream.h"
#include "utils/named_thread.h"
#include "packed444/layout.h"
#include "packed444/unpacker.h"
#include "vk/allocation.h"
#include <android/hardware_buffer.h>
#include <array>
#include <atomic>
#include <cassert>
#include <magic_enum.hpp>
#include <media/NdkImage.h>
#include <media/NdkImageReader.h>
#include <media/NdkMediaCodec.h>
#include <media/NdkMediaFormat.h>
#include <mutex>
#include <ranges>
#include <spdlog/spdlog.h>
#include <thread>
#include <vulkan/vulkan.hpp>
#include <vulkan/vulkan_android.h>
#include <vulkan/vulkan_raii.hpp>

DEUGLIFY(AMediaFormat)

struct wivrn::android::decoder::mapped_hardware_buffer
{
	vk::raii::DeviceMemory memory = nullptr;
	vk::raii::Image vimage = nullptr;
	vk::Extent2D extent{};
	vk::raii::ImageView image_view = nullptr;
	vk::ImageLayout layout = vk::ImageLayout::eUndefined;
};

// Blit handles retain this allocation and its ordinary RGBA sampler even when
// the decoder generation is retired before the renderer releases its frames.
struct wivrn::android::decoder::packed_output_image
{
	image_allocation image;
	vk::raii::ImageView view = nullptr;
	std::shared_ptr<vk::raii::Sampler> sampler;
	vk::ImageLayout layout = vk::ImageLayout::eUndefined;
	std::atomic_bool free = true;
};

namespace
{
const char * mime(wivrn::video_codec codec)
{
	using c = wivrn::video_codec;
	switch (codec)
	{
		case c::h264:
			return "video/avc";
		case c::h265:
			return "video/hevc";
		case c::av1:
			return "video/av01";
		case c::pyrowave:
		case c::raw:
			break;
	}
	assert(false);
	__builtin_unreachable();
}

void check(media_status_t status, const char * msg)
{
	if (status != AMEDIA_OK)
	{
		spdlog::error("{}: MediaCodec error {}", msg, (int)status);
		throw std::runtime_error("MediaCodec error");
	}
}

struct android_blit_handle : public wivrn::decoder::blit_handle
{
	std::shared_ptr<wivrn::android::decoder::mapped_hardware_buffer> vk_data;
	std::shared_ptr<AImageReader> image_reader;
	AImage_ptr aimage;
	android_blit_handle(
	        const wivrn::from_headset::feedback & feedback,
	        const wivrn::to_headset::video_stream_data_shard::view_info_t & view_info,
	        vk::ImageView image_view,
	        vk::Image image,
	        vk::Extent2D extent,
	        vk::ImageLayout & current_layout,
	        decltype(vk_data) vk_data,
	        decltype(image_reader) image_reader,
	        decltype(aimage) aimage) :
	        wivrn::decoder::blit_handle(feedback, view_info, image_view, image, extent, current_layout),
	        vk_data(vk_data),
	        image_reader(image_reader),
	        aimage(std::move(aimage)) {}
};

struct packed_blit_handle : public wivrn::decoder::blit_handle
{
	std::shared_ptr<wivrn::android::decoder::packed_output_image> output;
	std::shared_ptr<AImageReader> image_reader;
	packed_blit_handle(const wivrn::from_headset::feedback & feedback,
	                   const wivrn::to_headset::video_stream_data_shard::view_info_t & view_info,
	                   vk::Extent2D extent,
	                   std::shared_ptr<wivrn::android::decoder::packed_output_image> output,
	                   std::shared_ptr<AImageReader> image_reader) :
	        wivrn::decoder::blit_handle{feedback, view_info, *output->view, output->image,
	                             extent, output->layout}, output(std::move(output)),
	        image_reader(std::move(image_reader)) {}
	~packed_blit_handle() { output->free.store(true, std::memory_order_release); }
};

} // namespace

namespace wivrn::android
{

decoder::decoder(
        vk::raii::Device & device,
        vk::raii::PhysicalDevice & physical_device,
        uint32_t queue_family_index,
        const wivrn::to_headset::video_stream_description & description,
        uint8_t stream_index,
        std::weak_ptr<scenes::stream> weak_scene,
        shard_accumulator * accumulator) :
        stream_index(stream_index), device(device), queue_family_index(queue_family_index),
        weak_scene(weak_scene), accumulator(accumulator)
{
	spdlog::info("hbm_mutex.native_handle() = {}", (void *)hbm_mutex.native_handle());

	auto width = description.width;
	auto height = description.height / (stream_index == 2 ? 2 : 1);
	packed444 = description.layout == wivrn::video_frame_layout::packed444_v1;
	if (packed444)
	{
		auto coded = wivrn::packed444::coded_extent(width, height);
		packed_extent = vk::Extent2D{uint32_t(width), uint32_t(height)};
		width = coded.width;
		initialize_packed_output();
	}

	AImageReader * ir;
	check(AImageReader_newWithUsage(
	              width,
	              height,
	              AIMAGE_FORMAT_PRIVATE,
	              AHARDWAREBUFFER_USAGE_CPU_READ_NEVER | AHARDWAREBUFFER_USAGE_CPU_WRITE_NEVER | AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE,
	              scenes::stream::image_buffer_size + 4 /* maxImages */,
	              &ir),
	      "AImageReader_newWithUsage");
	image_callback->owner = this;
	image_reader.reset(ir, [state = image_callback](AImageReader * reader) {
		// Keep queued callbacks' context alive until the reader joins its looper.
		AImageReader_delete(reader);
	});

	AImageReader_ImageListener listener{image_callback.get(), on_image_available};
	check(AImageReader_setImageListener(ir, &listener), "AImageReader_setImageListener");

	vkGetAndroidHardwareBufferPropertiesANDROID =
	        application::get_vulkan_proc<PFN_vkGetAndroidHardwareBufferPropertiesANDROID>(
	                "vkGetAndroidHardwareBufferPropertiesANDROID");
	{
		AMediaFormat_ptr format(AMediaFormat_new());
		AMediaFormat_setString(format.get(), AMEDIAFORMAT_KEY_MIME, mime(description.codec[stream_index]));

		AMediaFormat_setInt32(format.get(), AMEDIAFORMAT_KEY_WIDTH, width);
		AMediaFormat_setInt32(format.get(), AMEDIAFORMAT_KEY_HEIGHT, height);
		AMediaFormat_setInt32(format.get(), AMEDIAFORMAT_KEY_OPERATING_RATE, std::ceil(description.frame_rate));
		AMediaFormat_setInt32(format.get(), AMEDIAFORMAT_KEY_PRIORITY, 0);
		if (packed444)
			AMediaFormat_setInt32(format.get(), "color-range", 1 /* full range, no carrier remapping */);

		media_codec.reset(AMediaCodec_createDecoderByType(mime(description.codec[stream_index])));

		if (not media_codec)
			throw std::runtime_error(std::string("Cannot create decoder for MIME type ") + mime(description.codec[stream_index]));

		char * codec_name;
		check(AMediaCodec_getName(media_codec.get(), &codec_name), "AMediaCodec_getName");
		spdlog::info("Created MediaCodec decoder \"{}\"", codec_name);
		const bool low_latency = application::get_config().decoder_low_latency;
		const bool qualcomm = std::string_view(codec_name).find("qti") != std::string_view::npos || std::string_view(codec_name).find("qcom") != std::string_view::npos;
		AMediaCodec_releaseName(media_codec.get(), codec_name);
		if (low_latency)
		{
			// Literal keeps compatibility with the API 29 minimum. Older codecs
			// ignore unknown standard keys; Qualcomm gets its vendor key too.
			AMediaFormat_setInt32(format.get(), "low-latency", 1);
			if (qualcomm)
				AMediaFormat_setInt32(format.get(), "vendor.qti-ext-dec-low-latency.enable", 1);
		}

		ANativeWindow * window;

		check(AImageReader_getWindow(image_reader.get(), &window), "AImageReader_getWindow");

		AMediaCodecOnAsyncNotifyCallback callback{
		        .onAsyncInputAvailable = decoder::on_media_input_available,
		        .onAsyncOutputAvailable = decoder::on_media_output_available,
		        .onAsyncFormatChanged = decoder::on_media_format_changed,
		        .onAsyncError = decoder::on_media_error,
		};
		check(AMediaCodec_setAsyncNotifyCallback(media_codec.get(), callback, this),
		      "AMediaCodec_setAsyncNotifyCallback");

		auto status = AMediaCodec_configure(media_codec.get(), format.get(), window, nullptr, 0);
		if (status != AMEDIA_OK && low_latency)
		{
			spdlog::warn("Decoder {} rejected low-latency configuration ({}), retrying without hints", stream_index, int(status));
			// Start with a fresh codec after a failed configure.
			media_codec.reset();
			media_codec.reset(AMediaCodec_createDecoderByType(mime(description.codec[stream_index])));
			if (!media_codec)
				throw std::runtime_error("Cannot recreate decoder");
			check(AMediaCodec_setAsyncNotifyCallback(media_codec.get(), callback, this), "AMediaCodec_setAsyncNotifyCallback");
			AMediaFormat_clear(format.get());
			AMediaFormat_setString(format.get(), AMEDIAFORMAT_KEY_MIME, mime(description.codec[stream_index]));
			AMediaFormat_setInt32(format.get(), AMEDIAFORMAT_KEY_WIDTH, width);
			AMediaFormat_setInt32(format.get(), AMEDIAFORMAT_KEY_HEIGHT, height);
			AMediaFormat_setInt32(format.get(), AMEDIAFORMAT_KEY_OPERATING_RATE, std::ceil(description.frame_rate));
			AMediaFormat_setInt32(format.get(), AMEDIAFORMAT_KEY_PRIORITY, 0);
			if (packed444)
				AMediaFormat_setInt32(format.get(), "color-range", 1);
			status = AMediaCodec_configure(media_codec.get(), format.get(), window, nullptr, 0);
		}
		check(status, "AMediaCodec_configure");
		spdlog::info("Decoder {} low-latency requested: {}", stream_index, low_latency);

		check(AMediaCodec_start(media_codec.get()), "AMediaCodec_start");
	}
	worker = utils::named_thread(
	        "decoder-" + std::to_string(stream_index),
	        [this]() {
		        while (true)
		        {
			        try
			        {
				        if (jobs.pop()())
					        return;
			        }
			        catch (const utils::sync_queue_closed & e)
			        {
				        return;
			        }
			        catch (const std::exception & e)
			        {
				        spdlog::error("error in decoder thread: {}", e.what());
			        }
		        }
	        });
}

decoder::~decoder()
{
	// Wake an image callback waiting for metadata before waiting for that callback.
	frame_infos.close();
	{
		std::lock_guard lock(image_callback->mutex);
		image_callback->owner = nullptr;
	}
	AImageReader_setImageListener(image_reader.get(), nullptr);
	if (media_codec)
	{
		AMediaCodec_stop(media_codec.get());
		jobs.push([]() { return true; });
		if (worker.joinable())
			worker.join();
	}
	input_buffers.close();
	jobs.close();

	if (worker.joinable())
		worker.join();

	spdlog::info("decoder::~decoder");
}

void decoder::push_data(std::span<std::span<const uint8_t>> data, uint64_t frame_index, bool partial)
{
	if (current_input_buffer.data == nullptr)
		current_input_buffer = input_buffers.pop();
	else if (current_input_buffer.frame_index != frame_index)
	{
		// Reuse the input buffer, discard existing data
		current_input_buffer.data_size = 0;
	}
	current_input_buffer.frame_index = frame_index;

	for (const auto & sub_data: data)
	{
		if (current_input_buffer.data_size + sub_data.size() > current_input_buffer.capacity)
		{
			spdlog::error("data to decode is larger than decoder buffer, skipping frame");
			return;
		}

		memcpy(current_input_buffer.data + current_input_buffer.data_size, sub_data.data(), sub_data.size());
		current_input_buffer.data_size += sub_data.size();
	}

	if (partial)
		return;

	jobs.push([=, idx = current_input_buffer.idx, data_size = current_input_buffer.data_size, mc = media_codec.get()]() {
		uint64_t timestamp = frame_index * 10'000;
		auto status = AMediaCodec_queueInputBuffer(mc, idx, 0, data_size, timestamp, 0);
		if (status != AMEDIA_OK)
			spdlog::error("AMediaCodec_queueInputBuffer: MediaCodec error {}({})",
			              int(status),
			              std::string(magic_enum::enum_name(status)).c_str());
		return false;
	});
	current_input_buffer = input_buffer{};
}

void decoder::frame_completed(const wivrn::from_headset::feedback & feedback, const wivrn::to_headset::video_stream_data_shard::view_info_t & view_info)
{
	if (not media_codec)
	{
		// If media_codec is not initialized, frame processing ends here
		if (auto scene = weak_scene.lock())
			scene->send_feedback(feedback);
	}

	// nothing required for decoder, mediacodec will callback when done
	frame_infos.push(frame_info{
	        .feedback = feedback,
	        .view_info = view_info,
	});
}

void decoder::on_image_available(void * context, AImageReader * reader)
{
	try
	{
		auto & state = *static_cast<image_callback_state *>(context);
		// A scene's last reference can be released by this callback. Keep it
		// alive until after unlocking, so its decoder teardown cannot reenter
		// the callback gate while we hold it.
		std::shared_ptr<scenes::stream> scene;
		{
			std::lock_guard lock(state.mutex);
			if (state.owner)
			{
				scene = state.owner->weak_scene.lock();
				if (scene)
					state.owner->on_image_available(reader);
			}
		}
	}
	catch (const utils::sync_queue_closed &)
	{
		// Decoder teardown wakes callbacks waiting for frame metadata.
	}
	catch (std::exception & e)
	{
		spdlog::error("Exception in decoder::on_image_available: {}", e.what());
	}
	catch (...)
	{
		spdlog::error("Unknown exception in decoder::on_image_available");
	}
}

void decoder::on_image_available(AImageReader * reader)
{
	assert(reader == image_reader.get());
	// Executed on image reader thread

	decltype(android_blit_handle::aimage) image;
	AImage * tmp;
	auto status = AImageReader_acquireLatestImage(image_reader.get(), &tmp);
	// acquireLatestImage can consume images belonging to already queued callbacks.
	if (status == AMEDIA_IMGREADER_NO_BUFFER_AVAILABLE)
		return;
	check(status, "AImageReader_acquireLatestImage");
	image.reset(tmp);

	int64_t fake_timestamp_ns;
	check(AImage_getTimestamp(image.get(), &fake_timestamp_ns), "AImage_getTimestamp");
	uint64_t frame_index = (fake_timestamp_ns + 5'000'000) / (10'000'000);

	frame_infos.drop_until([frame_index](auto & x) { return x.feedback.frame_index >= frame_index; });

	auto info = frame_infos.pop_if([frame_index](auto & x) { return x.feedback.frame_index == frame_index; });

	if (!info)
	{
		spdlog::warn("No frame info for frame {}, dropping frame", frame_index);
		return;
	}

	assert(info->feedback.frame_index == frame_index);

	auto vk_data = map_hardware_buffer(image.get());
	if (packed444)
	{
		publish_packed_image(std::move(image), std::move(vk_data), *info);
		return;
	}

	auto handle = std::make_shared<android_blit_handle>(
	        info->feedback,
	        info->view_info,
	        *vk_data->image_view,
	        *vk_data->vimage,
	        vk_data->extent,
	        vk_data->layout,
	        std::move(vk_data),
	        image_reader,
	        std::move(image));

	if (auto scene = weak_scene.lock())
		scene->push_blit_handle(accumulator, std::move(handle));
}

void decoder::initialize_packed_output()
{
	packed_sampler = std::make_shared<vk::raii::Sampler>(device, vk::SamplerCreateInfo{
	        .magFilter = vk::Filter::eLinear, .minFilter = vk::Filter::eLinear,
	        .mipmapMode = vk::SamplerMipmapMode::eNearest,
	        .addressModeU = vk::SamplerAddressMode::eClampToEdge,
	        .addressModeV = vk::SamplerAddressMode::eClampToEdge,
	        .addressModeW = vk::SamplerAddressMode::eClampToEdge});
	// Three queued display frames plus current/in-flight references leave room
	// for a new completed frame. Never overwrite an image held by the renderer.
	for (size_t i = 0; i < scenes::stream::image_buffer_size + 5; ++i)
	{
		auto image = std::make_shared<packed_output_image>();
		image->image = image_allocation(device, vk::ImageCreateInfo{
		        .imageType = vk::ImageType::e2D, .format = vk::Format::eR8G8B8A8Unorm,
		        .extent = {packed_extent.width, packed_extent.height, 1},
		        .mipLevels = 1, .arrayLayers = 1,
		        .usage = vk::ImageUsageFlagBits::eSampled | vk::ImageUsageFlagBits::eStorage},
		        {.usage = VMA_MEMORY_USAGE_AUTO}, "Packed444 RGBA output");
		image->view = device.createImageView({.image = image->image,
		        .viewType = vk::ImageViewType::e2D, .format = vk::Format::eR8G8B8A8Unorm,
		        .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor,
		                             .levelCount = 1, .layerCount = 1}});
		image->sampler = packed_sampler;
		packed_images.push_back(std::move(image));
	}
	packed_command_pool = device.createCommandPool({
	        .flags = vk::CommandPoolCreateFlagBits::eResetCommandBuffer,
	        .queueFamilyIndex = queue_family_index});
	packed_command = std::move(device.allocateCommandBuffers({
	        .commandPool = *packed_command_pool, .commandBufferCount = 1})[0]);
	packed_fence = device.createFence({});
}

void decoder::publish_packed_image(AImage_ptr image,
                                  std::shared_ptr<mapped_hardware_buffer> imported,
                                  const frame_info & info)
{
	// This callback runs under image_callback->mutex, serializing descriptor,
	// command-buffer and output-pool use with other callbacks and teardown.
	AImageCropRect crop{};
	check(AImage_getCropRect(image.get(), &crop), "AImage_getCropRect");
	if (crop.left != 0 || crop.top != 0 ||
	    crop.right != int32_t(packed_extent.width * 2) || crop.bottom != int32_t(packed_extent.height))
		throw std::runtime_error("Packed444 decoder returned an incompatible crop rectangle");

	std::shared_ptr<packed_output_image> output;
	for (auto & candidate: packed_images)
	{
		if (candidate->free.exchange(false, std::memory_order_acquire))
		{
			output = candidate;
			break;
		}
	}
	if (!output)
	{
		spdlog::warn("No Packed444 output image available; dropping frame {}", info.feedback.frame_index);
		return;
	}
	auto handle = std::make_shared<packed_blit_handle>(info.feedback, info.view_info, packed_extent, output, image_reader);
	auto binding = packed_unpacker->create_binding(*imported->image_view, *output->view);
	device.resetFences(*packed_fence);
	packed_command.reset();
	packed_command.begin({.flags = vk::CommandBufferUsageFlagBits::eOneTimeSubmit});
	std::array<vk::ImageMemoryBarrier, 2> acquire{{
	        {.srcAccessMask = vk::AccessFlagBits::eNone,
	         .dstAccessMask = vk::AccessFlagBits::eShaderRead,
	         .oldLayout = imported->layout, .newLayout = vk::ImageLayout::eGeneral,
	         .srcQueueFamilyIndex = VK_QUEUE_FAMILY_FOREIGN_EXT,
	         .dstQueueFamilyIndex = queue_family_index, .image = *imported->vimage,
	         .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor,
	                              .levelCount = 1, .layerCount = 1}},
	        {.srcAccessMask = output->layout == vk::ImageLayout::eUndefined ? vk::AccessFlagBits::eNone : vk::AccessFlagBits::eShaderRead,
	         .dstAccessMask = vk::AccessFlagBits::eShaderWrite,
	         .oldLayout = output->layout, .newLayout = vk::ImageLayout::eGeneral,
	         .srcQueueFamilyIndex = vk::QueueFamilyIgnored,
	         .dstQueueFamilyIndex = vk::QueueFamilyIgnored, .image = output->image,
	         .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor,
	                              .levelCount = 1, .layerCount = 1}},
	}};
	packed_command.pipelineBarrier(vk::PipelineStageFlagBits::eAllCommands,
	                               vk::PipelineStageFlagBits::eComputeShader, {}, {}, {}, acquire);
	packed_unpacker->record(packed_command, binding, imported->extent);
	std::array<vk::ImageMemoryBarrier, 2> release{{
	        {.srcAccessMask = vk::AccessFlagBits::eShaderRead,
	         .dstAccessMask = vk::AccessFlagBits::eNone,
	         .oldLayout = vk::ImageLayout::eGeneral, .newLayout = vk::ImageLayout::eGeneral,
	         .srcQueueFamilyIndex = queue_family_index,
	         .dstQueueFamilyIndex = VK_QUEUE_FAMILY_FOREIGN_EXT, .image = *imported->vimage,
	         .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor,
	                              .levelCount = 1, .layerCount = 1}},
	        {.srcAccessMask = vk::AccessFlagBits::eShaderWrite,
	         .dstAccessMask = vk::AccessFlagBits::eShaderRead,
	         .oldLayout = vk::ImageLayout::eGeneral, .newLayout = vk::ImageLayout::eGeneral,
	         .srcQueueFamilyIndex = vk::QueueFamilyIgnored,
	         .dstQueueFamilyIndex = vk::QueueFamilyIgnored, .image = output->image,
	         .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor,
	                              .levelCount = 1, .layerCount = 1}},
	}};
	packed_command.pipelineBarrier(vk::PipelineStageFlagBits::eComputeShader,
	                               vk::PipelineStageFlagBits::eAllCommands, {}, {}, {}, release);
	packed_command.end();
	application::get_queue().lock()->submit(vk::SubmitInfo{
	        .commandBufferCount = 1, .pCommandBuffers = &*packed_command}, *packed_fence);
	// Retain the AImage, imported AHB memory, immutable binding and output until
	// completion, then return the media image to its reader before rendering.
	if (device.waitForFences(*packed_fence, true, UINT64_MAX) != vk::Result::eSuccess)
		throw std::runtime_error("Packed444 GPU unpack completion failed");
	imported->layout = vk::ImageLayout::eGeneral;
	output->layout = vk::ImageLayout::eGeneral;
	image.reset();
	if (auto scene = weak_scene.lock())
		scene->push_blit_handle(accumulator, std::move(handle));
}

void decoder::create_sampler(const AHardwareBuffer_Desc & buffer_desc, vk::AndroidHardwareBufferFormatPropertiesANDROID & ahb_format)
{
	assert(ahb_format.externalFormat != 0);
	spdlog::info("AndroidHardwareBufferProperties");
	spdlog::info("  Vulkan format: {}", vk::to_string(ahb_format.format));
	spdlog::info("  External format: {:#x}", ahb_format.externalFormat);
	spdlog::info("  Format features: {}", vk::to_string(ahb_format.formatFeatures));
	spdlog::info("  samplerYcbcrConversionComponents: ({}, {}, {}, {})",
	             vk::to_string(ahb_format.samplerYcbcrConversionComponents.r),
	             vk::to_string(ahb_format.samplerYcbcrConversionComponents.g),
	             vk::to_string(ahb_format.samplerYcbcrConversionComponents.b),
	             vk::to_string(ahb_format.samplerYcbcrConversionComponents.a));
	spdlog::info("  Suggested YCbCr model: {}", vk::to_string(ahb_format.suggestedYcbcrModel));
	spdlog::info("  Suggested YCbCr range: {}", vk::to_string(ahb_format.suggestedYcbcrRange));
	spdlog::info("  Suggested X chroma offset: {}", vk::to_string(ahb_format.suggestedXChromaOffset));
	spdlog::info("  Suggested Y chroma offset: {}", vk::to_string(ahb_format.suggestedYChromaOffset));

	vk::Filter yuv_filter;
	if (!packed444 && (ahb_format.formatFeatures & vk::FormatFeatureFlagBits::eSampledImageYcbcrConversionLinearFilter))
		yuv_filter = vk::Filter::eLinear;
	else
		yuv_filter = vk::Filter::eNearest;

	// Create VkSamplerYcbcrConversion
	vk::StructureChain ycbcr_create_info{
	        vk::SamplerYcbcrConversionCreateInfo{
	                .format = vk::Format::eUndefined,
	                .ycbcrModel = ahb_format.suggestedYcbcrModel,
	                .ycbcrRange = ahb_format.suggestedYcbcrRange,
	                .components = ahb_format.samplerYcbcrConversionComponents,
	                .xChromaOffset = ahb_format.suggestedXChromaOffset,
	                .yChromaOffset = ahb_format.suggestedYChromaOffset,
	                .chromaFilter = yuv_filter,
	        },
	        vk::ExternalFormatANDROID{
	                .externalFormat = ahb_format.externalFormat,
	        },
	};

	// suggested values from decoder don't actually read the metadata, so it's garbage
	ycbcr_create_info.get<vk::SamplerYcbcrConversionCreateInfo>().ycbcrRange = vk::SamplerYcbcrRange::eItuFull;
	ycbcr_create_info.get<vk::SamplerYcbcrConversionCreateInfo>().ycbcrModel = vk::SamplerYcbcrModelConversion::eYcbcr709;
	if (packed444)
	{
		// Packed carrier values are data: range expansion or matrix conversion
		// before unpacking would corrupt the independently stored colour planes.
		ycbcr_create_info.get<vk::SamplerYcbcrConversionCreateInfo>().ycbcrModel = vk::SamplerYcbcrModelConversion::eRgbIdentity;
	}

	ycbcr_conversion = vk::raii::SamplerYcbcrConversion(device, ycbcr_create_info.get<vk::SamplerYcbcrConversionCreateInfo>());

	// Create VkSampler
	vk::StructureChain sampler_info{
	        vk::SamplerCreateInfo{
	                .magFilter = yuv_filter,
	                .minFilter = yuv_filter,
	                .mipmapMode = vk::SamplerMipmapMode::eNearest,
	                .addressModeU = vk::SamplerAddressMode::eClampToEdge,
	                .addressModeV = vk::SamplerAddressMode::eClampToEdge,
	                .addressModeW = vk::SamplerAddressMode::eClampToEdge,
	                .mipLodBias = 0.0f,
	                .anisotropyEnable = VK_FALSE,
	                .maxAnisotropy = 1,
	                .compareEnable = VK_FALSE,
	                .compareOp = vk::CompareOp::eNever,
	                .minLod = 0.0f,
	                .maxLod = 0.0f,
	                .borderColor = vk::BorderColor::eFloatOpaqueWhite, // TODO TBC
	                .unnormalizedCoordinates = VK_FALSE,
	        },
	        vk::SamplerYcbcrConversionInfo{
	                .conversion = *ycbcr_conversion,
	        },
	};

	ycbcr_sampler = vk::raii::Sampler(device, sampler_info.get());
}

std::shared_ptr<decoder::mapped_hardware_buffer> decoder::map_hardware_buffer(AImage * image)
{
	AHardwareBuffer * hardware_buffer;
	check(AImage_getHardwareBuffer(image, &hardware_buffer), "AImage_getHardwareBuffer");

	std::unique_lock lock(hbm_mutex);

	AHardwareBuffer_Desc buffer_desc{};
	AHardwareBuffer_describe(hardware_buffer, &buffer_desc);

	auto [properties, format_properties] = device.getAndroidHardwareBufferPropertiesANDROID<vk::AndroidHardwareBufferPropertiesANDROID, vk::AndroidHardwareBufferFormatPropertiesANDROID>(*hardware_buffer);

	if (!*ycbcr_sampler || memcmp(&ahb_format, &format_properties, sizeof(format_properties)))
	{
		if (packed444)
		{
			// Image callbacks serialize unpacking and wait for their own fence.
			// Destroy immutable-sampler descriptors and imported views first.
			packed_unpacker.reset();
			hardware_buffer_map.clear();
			ycbcr_sampler = nullptr;
			ycbcr_conversion = nullptr;
		}
		memcpy(&ahb_format, &format_properties, sizeof(format_properties));
		spdlog::info("decoded image size: {}x{}", buffer_desc.width, buffer_desc.height);
		create_sampler(buffer_desc, ahb_format);
		if (packed444)
			packed_unpacker = std::make_unique<wivrn::packed444::Unpacker>(device, packed_extent, *ycbcr_sampler);
		hardware_buffer_map.clear();
		// TODO recreate the normal YCbCr reprojector pipeline on a format
		// change. Packed output keeps its ordinary RGBA sampler unchanged.
	}

	auto it = hardware_buffer_map.find(hardware_buffer);
	if (it != hardware_buffer_map.end())
		return it->second;

	vk::StructureChain img_info{
	        vk::ImageCreateInfo{
	                .flags = {},
	                .imageType = vk::ImageType::e2D,
	                .format = vk::Format::eUndefined,
	                .extent = {buffer_desc.width, buffer_desc.height, 1},
	                .mipLevels = 1,
	                .arrayLayers = 1,
	                .samples = vk::SampleCountFlagBits::e1,
	                .tiling = vk::ImageTiling::eOptimal,
	                .usage = vk::ImageUsageFlagBits::eSampled,
	                .sharingMode = vk::SharingMode::eExclusive,
	                .initialLayout = vk::ImageLayout::eUndefined,
	        },
	        vk::ExternalMemoryImageCreateInfo{
	                .handleTypes = vk::ExternalMemoryHandleTypeFlagBits::eAndroidHardwareBufferANDROID,
	        },
	        vk::ExternalFormatANDROID{
	                .externalFormat = format_properties.externalFormat,
	        },
	};

	vk::raii::Image vimage(device, img_info.get());

	assert(properties.memoryTypeBits != 0);
	vk::StructureChain mem_info{
	        vk::MemoryAllocateInfo{
	                .allocationSize = properties.allocationSize,
	                .memoryTypeIndex = (uint32_t)(ffs(properties.memoryTypeBits) - 1),
	        },
	        vk::MemoryDedicatedAllocateInfo{
	                .image = *vimage,
	        },
	        vk::ImportAndroidHardwareBufferInfoANDROID{
	                .buffer = hardware_buffer,
	        },
	};

	vk::raii::DeviceMemory memory(device, mem_info.get());

	vimage.bindMemory(*memory, 0);

	vk::StructureChain iv_info{
	        vk::ImageViewCreateInfo{
	                .image = *vimage,
	                .viewType = vk::ImageViewType::e2D,
	                .format = vk::Format::eUndefined,
	                .subresourceRange = {
	                        .aspectMask = vk::ImageAspectFlagBits::eColor,
	                        .baseMipLevel = 0,
	                        .levelCount = 1,
	                        .baseArrayLayer = 0,
	                        .layerCount = 1,
	                },
	        },
	        vk::SamplerYcbcrConversionInfo{
	                .conversion = *ycbcr_conversion,
	        },
	};

	application::ignore_debug_reports_for(*vimage);
	vk::raii::ImageView image_view(device, iv_info.get());
	application::unignore_debug_reports_for(*vimage);

	auto handle = std::make_shared<mapped_hardware_buffer>();
	handle->vimage = std::move(vimage);
	handle->image_view = std::move(image_view);
	handle->memory = std::move(memory);
	handle->extent = vk::Extent2D{
	        .width = buffer_desc.width,
	        .height = buffer_desc.height,
	};

	hardware_buffer_map[hardware_buffer] = handle;
	return handle;
}

void decoder::on_media_error(AMediaCodec *, void * userdata, media_status_t error, int32_t actionCode, const char * detail)
{
	spdlog::warn("Mediacodec error: {}", detail);

	if (error == AMEDIA_ERROR_MALFORMED)
	{
		// Send an empty feedback packet, encoder will know we are lost
		auto self = (decoder *)userdata;
		if (auto scene = self->weak_scene.lock())
			scene->send_feedback(
			        wivrn::from_headset::feedback{
			                .stream_index = self->stream_index});
	}
}
void decoder::on_media_format_changed(AMediaCodec *, void * userdata, AMediaFormat *)
{
	spdlog::info("Mediacodec format changed");
}
void decoder::on_media_input_available(AMediaCodec * media_codec, void * userdata, int32_t index)
{
	auto self = (decoder *)userdata;
	size_t size;
	uint8_t * buffer = AMediaCodec_getInputBuffer(media_codec, index, &size);
	self->input_buffers.push({
	        .idx = index,
	        .capacity = size,
	        .data = buffer,
	});
}
void decoder::on_media_output_available(AMediaCodec * media_codec, void * userdata, int32_t index, AMediaCodecBufferInfo * bufferInfo)
{
	auto self = (decoder *)userdata;
	self->jobs.push([=]() {
		auto status = AMediaCodec_releaseOutputBuffer(media_codec, index, true);
		// will trigger on_image_available through ImageReader
		if (status != AMEDIA_OK)
			spdlog::error("AMediaCodec_releaseOutputBuffer: MediaCodec error {}({})",
			              int(status),
			              std::string(magic_enum::enum_name(status)).c_str());
		return false;
	});
}

static bool hardware_accelerated(AMediaCodec * media_codec)
{
	// MediaCodecInfo has isHardwareAccelerated, but this does not exist in NDK.
	char * name;
	AMediaCodec_getName(media_codec, &name);
	auto release = [&]() {
		AMediaCodec_releaseName(media_codec, name);
	};
	for (const char * prefix: {
	             "OMX.google",
	             "c2.android",
	     })
	{
		if (std::string_view(name).starts_with(prefix))
		{
			release();
			return false;
		}
	}
	release();
	return true;
}

void decoder::supported_codecs(std::vector<wivrn::video_codec> & result)
{
	// Make sure we update this code when codecs are changed
	static_assert(magic_enum::enum_count<wivrn::video_codec>() == 5);

	// In order or preference, from preferred to least preferred
	for (auto codec: {
	             wivrn::video_codec::h265,
	             wivrn::video_codec::h264,
	     })
	{
		AMediaCodec_ptr media_codec(AMediaCodec_createDecoderByType(mime(codec)));

		bool supported = media_codec and hardware_accelerated(media_codec.get());
		if (supported)
			result.push_back(codec);

		spdlog::info("video codec {}: {}supported", magic_enum::enum_name(codec), supported ? "" : "NOT ");
	}
}

} // namespace wivrn::android
