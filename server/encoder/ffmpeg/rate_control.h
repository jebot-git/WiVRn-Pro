// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <optional>
#include <cstdint>
extern "C"
{
#include <libavcodec/avcodec.h>
}
namespace wivrn
{
inline void configure_rate_control(AVCodecContext & ctx, std::optional<int> qp, uint64_t bitrate, float fps)
{
	if (qp)
	{
		ctx.flags |= AV_CODEC_FLAG_QSCALE;
		ctx.global_quality = *qp * FF_QP2LAMBDA;
		// Keep I/P QPs identical; FFmpeg otherwise applies its default I offset.
		ctx.i_quant_factor = 1.0;
		ctx.i_quant_offset = 0;
		ctx.b_quant_factor = 1.0;
		ctx.b_quant_offset = 0;
		ctx.bit_rate = ctx.rc_min_rate = ctx.rc_max_rate = 0;
		ctx.rc_buffer_size = ctx.rc_initial_buffer_occupancy = 0;
	}
	else
	{
		ctx.bit_rate = ctx.rc_max_rate = bitrate;
		ctx.rc_buffer_size = int(bitrate / fps);
	}
}
} // namespace wivrn
