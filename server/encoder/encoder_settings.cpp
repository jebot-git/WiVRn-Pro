#include "fork_policy.h"
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
#include "encoder_settings.h"

#include "driver/configuration.h"
#include "driver/wivrn_session.h"
#include "util/u_logging.h"
#include "utils/wivrn_vk_bundle.h"
#include "video_encoder.h"
#include "wivrn_packets.h"

#include <magic_enum.hpp>
#include <string>
#include <vulkan/vulkan.hpp>

#include "wivrn_config.h"

#if WIVRN_USE_VAAPI || WIVRN_USE_QSV
#include "ffmpeg/video_encoder_va.h"
#include <libavutil/ffversion.h>
#endif

namespace wivrn
{

static const double passthrough_bitrate_factor = 0.05;

static void split_bitrate(std::array<wivrn::encoder_settings, 3> & encoders, uint64_t bitrate)
{
	assert(bitrate > 0);
	double total_weight = 0;
	for (auto [i, encoder]: std::ranges::enumerate_view(encoders))
	{
		double w = encoder.width * encoder.height;
		if (i == 2)
			w *= passthrough_bitrate_factor;
		switch (encoder.codec)
		{
			case wivrn::h264:
				w *= 2;
				break;
			case wivrn::h265:
			case wivrn::av1:
			case wivrn::raw:
				break;
		}
		encoder.bitrate = w;
		total_weight += w;
	}

	for (auto & encoder: encoders)
	{
		encoder.bitrate_multiplier = encoder.bitrate / total_weight;
		encoder.bitrate = encoder.bitrate_multiplier * bitrate;
	}
}

void print_encoders(const std::array<wivrn::encoder_settings, 3> & encoders)
{
	std::stringstream str;
	str << "Encoder configuration:";
	for (auto & encoder: encoders)
	{
		str << "\n\t* " << encoder.encoder_name << " (" << magic_enum::enum_name(encoder.codec) << " " << encoder.bit_depth << "-bit)"
		    << "\n\t  size: " << encoder.width << "x" << encoder.height
		    << "\n\t  bitrate: " << int(encoder.bitrate / 100'000) / 10. << "Mbit/s";
	}
	U_LOG_I("%s", str.str().c_str());
}

namespace
{
class prober
{
	wivrn::vk_bundle & vk;
	const from_headset::headset_info_packet & info;

#if WIVRN_USE_VAAPI || WIVRN_USE_QSV
	std::map<std::pair<video_codec, bool>, bool> vaapi_support;

	bool check_vaapi(video_codec codec, bool qsv = false)
	{
		if (auto it = vaapi_support.find({codec, qsv}); it != vaapi_support.end())
			return it->second;
		try
		{
			video_encoder_va test(
			        vk,
			        encoder_settings{
			                .width = 800,
			                .height = 800,
			                .codec = codec,
			                .fps = 60,
			                .encoder_name = qsv ? encoder_qsv : encoder_vaapi,
			                .bitrate = 50'000'000,
			                .bit_depth = 8,
			        },
			        0);
			vaapi_support[{codec, qsv}] = true;
			return true;
		}
		catch (std::exception & e)
		{
			vaapi_support[{codec, qsv}] = false;
			U_LOG_I("%s not supported for %s: %s", qsv ? "QSV" : "VAAPI", std::string(magic_enum::enum_name(codec)).c_str(), e.what());
			return false;
		}
	}
#endif

#if WIVRN_USE_VULKAN_ENCODE

	bool has_vk(video_codec codec)
	{
		if (vk.encode_queues.empty())
			return false;
		if (not std::get<vk::PhysicalDeviceVideoMaintenance1FeaturesKHR>(vk.feat).videoMaintenance1)
		{
			U_LOG_I("Cannot use vulkan video encode without VideoMaintenance1 feature");
			return false;
		}
		auto prop = vk.physical_device.getQueueFamilyProperties2<vk::StructureChain<vk::QueueFamilyProperties2, vk::QueueFamilyVideoPropertiesKHR>>();
		const auto flags = prop.at(vk.encode_queues[0].family_index).get<vk::QueueFamilyVideoPropertiesKHR>().videoCodecOperations;
		switch (codec)
		{
			case h264: {
				auto res = vk.has_device_ext(VK_KHR_VIDEO_ENCODE_H264_EXTENSION_NAME) and flags & vk::VideoCodecOperationFlagBitsKHR::eEncodeH264;
				if (not res)
					U_LOG_I("GPU does not support H.264 Vulkan video encode");
				return res;
			}
			case h265: {
				auto res = vk.has_device_ext(VK_KHR_VIDEO_ENCODE_H265_EXTENSION_NAME) and flags & vk::VideoCodecOperationFlagBitsKHR::eEncodeH265;
				if (not res)
					U_LOG_I("GPU does not support H.265 Vulkan video encode");
				return res;
			}
			case av1:
				U_LOG_D("Vulkan video encode for AV1 is not implemented in WiVRn");
			case raw:
				return false;
		}
		U_LOG_E("Invalid codec %d", int(codec));
		return false;
	}
#endif

public:
	prober(wivrn::vk_bundle & vk, const from_headset::headset_info_packet & info) :
	        vk(vk), info(info) {}

	std::pair<std::string, video_codec> select_encoder(const configuration::encoder & config)
	{
		if (!fork_policy::encoder(config.name))
			throw std::runtime_error("Encoder removed from this fork: " + config.name);
		if (config.codec && *config.codec != h264 && *config.codec != h265)
			throw std::runtime_error("Quest Pro supports only H.264 / HEVC in this fork");
		auto codecs = config.codec ? std::vector{*config.codec} : info.supported_codecs;
		std::erase_if(codecs, [&](auto codec) {
			return (codec != h264 && codec != h265) || !std::ranges::contains(info.supported_codecs, codec);
		});
#if WIVRN_USE_QSV
		if (config.name == encoder_qsv)
			for (auto codec: codecs)
				if (check_vaapi(codec, true))
					return {encoder_qsv, codec};
#else
		if (config.name == encoder_qsv)
			throw std::runtime_error("QSV was not built; configure WIVRN_USE_QSV=ON");
#endif
#if WIVRN_USE_VULKAN_ENCODE
		if (config.name.empty() || config.name == encoder_vulkan)
			for (auto codec: codecs)
				if (has_vk(codec))
					return {encoder_vulkan, codec};
#endif
#if WIVRN_USE_VAAPI
		if (config.name.empty() || config.name == encoder_vaapi)
			for (auto codec: codecs)
				if (check_vaapi(codec))
					return {encoder_vaapi, codec};
#endif
#if WIVRN_USE_QSV
		if (config.name.empty())
			for (auto codec: codecs)
				if (check_vaapi(codec, true))
					return {encoder_qsv, codec};
#endif
		throw std::runtime_error("No usable Intel Arc hardware encoder for the requested Quest Pro codec");
	}
};
} // namespace

static uint16_t align(uint16_t value, uint16_t alignment)
{
	return ((value + alignment - 1) / alignment) * alignment;
}

std::array<encoder_settings, 3> get_encoder_settings(wivrn::vk_bundle & bundle, wivrn_session & session)
{
	configuration config;

	std::array<wivrn::encoder_settings, 3> res;
	const auto & info = session.get_info();
	const auto settings = *session.get_settings();

	prober prober{bundle, info};

	for (auto [src, dst]: std::ranges::zip_view(config.encoders, res))
	{
		dst.fps = session.default_fps();
		dst.options = src.options;
		dst.device = src.device;

		std::tie(dst.encoder_name, dst.codec) = prober.select_encoder(src);
	}

	auto width = align(info.stream_eye_width, 64);
	auto height = align(info.stream_eye_height, 64);

	for (auto [i, dst]: std::ranges::enumerate_view(res))
	{
		dst.width = width;
		dst.height = height;
		if (i == 2) // alpha channel
			dst.height /= 2;
	}

	auto bit_depth = config.bit_depth ? config.bit_depth : info.bit_depth;

	if (bit_depth and bit_depth != 8 and bit_depth != 10)
		throw std::runtime_error("invalid bit-depth setting. supported values: 8, 10");

	if (std::ranges::contains(res, video_codec::h264, &encoder_settings::codec))
		bit_depth = 8;
	else if (not bit_depth)
		bit_depth = 10;

	auto check_format = [&](vk::Format format) {
		try
		{
			auto props = bundle.physical_device.getImageFormatProperties(
			        format,
			        vk::ImageType::e2D,
			        vk::ImageTiling::eOptimal,
			        vk::ImageUsageFlagBits::eStorage | vk::ImageUsageFlagBits::eTransferSrc);
			return props.maxArrayLayers >= 3 and
			       props.maxExtent.depth >= 1 and
			       props.maxExtent.width >= width and
			       props.maxExtent.height >= height;
		}
		catch (vk::FormatNotSupportedError &)
		{
			return false;
		}
	};

#if WIVRN_USE_VAAPI || WIVRN_USE_QSV
	auto check_vaapi = [&](int bit_depth) {
		for (const auto & encoder: res)
		{
			if (encoder.encoder_name == encoder_vaapi || encoder.encoder_name == encoder_qsv)
			{
				try
				{
					video_encoder_va{
					        bundle,
					        encoder_settings{
					                .width = 800,
					                .height = 800,
					                .codec = encoder.codec,
					                .fps = 60,
					                .encoder_name = encoder.encoder_name,
					                .bitrate = 50'000'000,
					                .bit_depth = bit_depth,
					        },
					        0};
				}
				catch (std::exception & e)
				{
					U_LOG_I("vaapi not supported for %s %d bits", std::string(magic_enum::enum_name(encoder.codec)).c_str(), bit_depth);
					return false;
				}
			}
		}
		return true;
	};
#else
	auto check_vaapi = [&](int) {
		return true;
	};
#endif

	if (bit_depth == 10 and not(check_format(vk::Format::eR16Unorm) and check_vaapi(*bit_depth)))
	{
		U_LOG_W("GPU does not have sufficient support for 10-bit images, reverting to 8");
		bit_depth = 8;
	}
	if (bit_depth == 8 and not check_format(vk::Format::eR8Unorm))
	{
		U_LOG_W("GPU does not have sufficient support for 8-bit images");
	}

	for (auto & i: res)
		i.bit_depth = bit_depth.value_or(10);

	split_bitrate(res, settings.bitrate_bps);
	return res;
}
} // namespace wivrn
