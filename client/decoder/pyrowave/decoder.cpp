/*
 * WiVRn VR streaming
 * Copyright (C) 2025  Patrick Nicolas <patricknicolas@laposte.net>
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

#include "decoder.h"

#include "application.h"
#include "scenes/stream.h"

namespace
{
struct pyrowave_blit_handle : public decoder::blit_handle
{
	std::atomic_bool & free;

	pyrowave_blit_handle(
	        const wivrn::from_headset::feedback & feedback,
	        const wivrn::to_headset::video_stream_data_shard::view_info_t & view_info,
	        vk::ImageView image_view,
	        vk::Image image,
            vk::Extent2D extent,
	        vk::ImageLayout & current_layout,
	        vk::Semaphore semaphore,
	        uint64_t & semaphore_val,
	        std::atomic_bool & free) :
	        decoder::blit_handle{feedback, view_info, image_view, image, extent, current_layout, semaphore, &semaphore_val},
	        free(free) {}
	~pyrowave_blit_handle()
	{
		free = true;
	};
};
} // namespace

namespace wivrn
{
pyrowave_decoder::pyrowave_decoder(
        vk::raii::Device & device,
        vk::raii::PhysicalDevice & physical_device,
        uint32_t vk_queue_family_index,
        const wivrn::to_headset::video_stream_description & description,
        uint8_t stream_index,
        std::weak_ptr<scenes::stream> scene,
        shard_accumulator * accumulator) :
        extent{description.width, stream_index == 2 ? description.height / 2u : description.height},
        conversion(device, extent),
        sampler_(device, {.magFilter = vk::Filter::eNearest, .minFilter = vk::Filter::eNearest,
            .addressModeU = vk::SamplerAddressMode::eClampToEdge, .addressModeV = vk::SamplerAddressMode::eClampToEdge,
            .addressModeW = vk::SamplerAddressMode::eClampToEdge}),
        weak_scene(scene),
        accumulator(accumulator),
        dec(physical_device, device, extent.width, extent.height, PyroWave::ChromaSubsampling::Chroma444, false, true, application::pyrowave_native_fp16)
{
    if (description.pyrowave_chroma != 1 || description.pyrowave_wavelet != 2)
        throw std::runtime_error("Unsupported PyroWave chroma/wavelet configuration");
    for (auto & item : image_pool) {
        item.image = image_allocation(device, {.imageType = vk::ImageType::e2D, .format = vk::Format::eR8G8B8A8Unorm,
            .extent = {extent.width, extent.height, 1}, .mipLevels = 1, .arrayLayers = 1,
            .usage = vk::ImageUsageFlagBits::eSampled | vk::ImageUsageFlagBits::eStorage},
            {.usage = VMA_MEMORY_USAGE_AUTO}, "PyroWave RGBA output");
        item.view_full = device.createImageView({.image = item.image, .viewType = vk::ImageViewType::e2D,
            .format = vk::Format::eR8G8B8A8Unorm,
            .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = 1}});

		item.semaphore = vk::raii::Semaphore(
		        device,
		        vk::StructureChain{
		                vk::SemaphoreCreateInfo{},
		                vk::SemaphoreTypeCreateInfo{
		                        .semaphoreType = vk::SemaphoreType::eTimeline,
		                },
		        }
		                .get());

	}

	pending.lock()->input = std::make_unique<PyroWave::DecoderInput>(dec);
	input_acc = std::make_unique<PyroWave::DecoderInput>(dec);

	worker = std::thread([&, vk_queue_family_index] { worker_function(vk_queue_family_index); });
}

pyrowave_decoder::~pyrowave_decoder()
{
	exiting = true;
	pending.lock().notify_all();
	if (worker.joinable())
		worker.join();
}

void pyrowave_decoder::push_data(std::span<std::span<const uint8_t>> data, uint64_t frame_index, bool partial)
{
    // partial marks incremental shard delivery; retain it until frame_completed.
    if (input_frame != frame_index) { input_acc->clear(); input_frame = frame_index; }
	for (auto & item: data)
		if (!input_acc->push_data(item)) throw std::runtime_error("Invalid PyroWave packet");
}

void pyrowave_decoder::frame_completed(
        const from_headset::feedback & feedback,
        const to_headset::video_stream_data_shard::view_info_t & view_info)
{
	{
        if (!input_acc->complete()) { input_acc->clear(); return; }
		auto locked = pending.lock();
		std::swap(input_acc, locked->input);
		locked->ready = true;
		locked->feedback = feedback;
		locked->view_info = view_info;
		locked.notify_all();
	}
	input_acc->clear();
}

pyrowave_decoder::image * pyrowave_decoder::get_free()
{
	for (auto & item: image_pool)
	{
		if (item.free.exchange(false))
			return &item;
	}
	return nullptr;
}

void pyrowave_decoder::worker_function(uint32_t queue_family_index)
{
	auto & device = dec.device;
	vk::raii::CommandPool command_pool(
	        device,
	        vk::CommandPoolCreateInfo{
	                .flags = vk::CommandPoolCreateFlagBits::eResetCommandBuffer,
	                .queueFamilyIndex = queue_family_index,
	        });
	vk::raii::CommandBuffer cmd_buf(
	        std::move(device.allocateCommandBuffers(vk::CommandBufferAllocateInfo{
	                .commandPool = *command_pool,
	                .commandBufferCount = 1,
	        })[0]));
	vk::raii::Fence fence(device, vk::FenceCreateInfo{});

	vk::raii::QueryPool qp(
	        device, vk::QueryPoolCreateInfo{
	                        .queryType = vk::QueryType::eTimestamp,
	                        .queryCount = 2,
	                });

	from_headset::feedback feedback;
	to_headset::video_stream_data_shard::view_info_t view_info;

	auto input = std::make_unique<PyroWave::DecoderInput>(dec);

	// Timestamp completed GPU decoding; feedback is stamped at actual delivery.
	uint64_t decoded_count = 0;
	float timestamp_period = application::get_physical_device_properties().limits.timestampPeriod;

	while (not exiting)
	{
		try
		{
			{
				auto locked = pending.lock();
				locked.wait([&]() { return exiting or locked->ready; });
				if (exiting)
					return;

				feedback = locked->feedback;
				view_info = locked->view_info;

				std::swap(locked->input, input);
				locked->input->clear();
				locked->ready = false;
			}


			auto item = get_free();
			if (not item)
			{
				spdlog::warn("No image available in pool, discard frame");
				continue;
			}

			auto handle = std::make_shared<pyrowave_blit_handle>(
			        feedback,
			        view_info,
			        *item->view_full,
			        item->image,
                    extent,
			        item->current_layout,
			        *item->semaphore,
			        item->semaphore_val,
			        item->free);
			dec.device.resetFences(*fence);
			cmd_buf.reset();
			cmd_buf.begin({.flags = vk::CommandBufferUsageFlagBits::eOneTimeSubmit});
			cmd_buf.resetQueryPool(*qp, 0, 2);
			cmd_buf.writeTimestamp(vk::PipelineStageFlagBits::eComputeShader, *qp, 0);

			if (item->current_layout != vk::ImageLayout::eGeneral)
			{
				item->current_layout = vk::ImageLayout::eGeneral;
				vk::ImageMemoryBarrier barrier{
				        .srcAccessMask = vk::AccessFlagBits::eNone,
				        .dstAccessMask = vk::AccessFlagBits::eMemoryWrite,
				        .oldLayout = vk::ImageLayout::eUndefined,
				        .newLayout = vk::ImageLayout::eGeneral,
				        .image = item->image,
				        .subresourceRange = {
				                .aspectMask = vk::ImageAspectFlagBits::eColor,
				                .levelCount = 1,
				                .layerCount = 1,
				        },
				};
				cmd_buf.pipelineBarrier(
				        vk::PipelineStageFlagBits::eAllCommands,
				        vk::PipelineStageFlagBits::eComputeShader,
				        {},
				        {},
				        {},
				        barrier);
			}
            auto planes = conversion.begin(cmd_buf);
            if (!dec.decode(cmd_buf, *input, planes)) throw std::runtime_error("PyroWave GPU decode failed");
            conversion.convert(cmd_buf, *item->view_full);
			{
				vk::ImageMemoryBarrier barrier{
				        .srcAccessMask = vk::AccessFlagBits::eMemoryWrite,
				        .dstAccessMask = vk::AccessFlagBits::eMemoryRead,
				        .oldLayout = vk::ImageLayout::eGeneral,
				        .newLayout = vk::ImageLayout::eGeneral,
				        .image = item->image,
				        .subresourceRange = {
				                .aspectMask = vk::ImageAspectFlagBits::eColor,
				                .levelCount = 1,
				                .layerCount = 1,
				        },
				};
				cmd_buf.pipelineBarrier(
				        vk::PipelineStageFlagBits::eComputeShader,
				        vk::PipelineStageFlagBits::eFragmentShader,
				        {},
				        {},
				        {},
				        barrier);
			}
			cmd_buf.writeTimestamp(vk::PipelineStageFlagBits::eComputeShader, *qp, 1);
			cmd_buf.end();

			application::get_queue().lock()->submit(
			        vk::StructureChain{
			                vk::SubmitInfo{
			                        .commandBufferCount = 1,
			                        .pCommandBuffers = &*cmd_buf,
			                        .signalSemaphoreCount = 1,
			                        .pSignalSemaphores = &*item->semaphore,
			                },
			                vk::TimelineSemaphoreSubmitInfo{
			                        .signalSemaphoreValueCount = 1,
			                        .pSignalSemaphoreValues = &++item->semaphore_val,
			                },
			        }
			                .get(),
			        *fence);

            auto res = dec.device.waitForFences(*fence, true, UINT64_MAX);
            if (res != vk::Result::eSuccess) throw std::runtime_error("PyroWave completion fence failed");
            if (auto scene = weak_scene.lock()) scene->push_blit_handle(accumulator, std::move(handle));

			std::array<uint64_t, 2> times;
			res = (*device).getQueryPoolResults(
			        *qp, 0, 2, sizeof(uint64_t) * 2, times.data(), sizeof(uint64_t), vk::QueryResultFlagBits::eWait | vk::QueryResultFlagBits::e64);
			if (res == vk::Result::eSuccess)
				{
                    const double gpu_ms = (times[1] - times[0]) * timestamp_period / 1e6;
                    if (++decoded_count % 90 == 0) spdlog::info("PYROWAVE_GPU frame={} gpu_ms={} chroma=444 wavelet=haar complete=true", feedback.frame_index, gpu_ms);
                }
		}
		catch (std::exception & e)
		{
			spdlog::warn("pyrowave exception: {}", e.what());
		}
	}
}

std::vector<wivrn::video_codec> pyrowave_decoder::supported_codecs()
{
	return {video_codec::pyrowave};
}
} // namespace wivrn
