// SPDX-License-Identifier: GPL-3.0-or-later
#include "encoder/rate_control.h"
#include "encoder/ffmpeg/rate_control.h"
#include <cassert>
#include <functional>
#include <iostream>

using options = std::map<std::string, std::string>;
static void rejects(const options & values, bool supported = true)
{
	bool rejected = false;
	try { wivrn::cqp_quantizer(values, supported); }
	catch (const std::runtime_error &) { rejected = true; }
	assert(rejected);
}
int main()
{
	assert(!wivrn::cqp_quantizer({}, false));
	assert(!wivrn::cqp_quantizer({{"rate_control", "cbr"}}, true));
	assert(wivrn::cqp_quantizer({{"rate_control", "cqp"}, {"qp", "1"}}, true) == 1);
	assert(wivrn::cqp_quantizer({{"rate_control", "cqp"}, {"qp", "51"}}, true) == 51);
	for (const auto * value : {"0", "52", "-1", "24.5", "24x", "", "9999999999999"})
		rejects({{"rate_control", "cqp"}, {"qp", value}});
	rejects({{"qp", "24"}});
	rejects({{"rate_control", "icq"}});
	rejects({{"rate_control", "cqp"}});
	rejects({{"rate_control", "cqp"}, {"qp", "24"}}, false);
	for (const auto * key : {"b", "maxrate", "bufsize", "global_quality", "flags", "low_delay_brc", "look_ahead", "i_qoffset"})
		rejects({{"rate_control", "cqp"}, {"qp", "24"}, {key, "1"}});
	assert(wivrn::cqp_quantizer({{"rate_control", "cqp"}, {"qp", "24"}, {"low_delay_brc", "0"}}, true) == 24);
	auto * ctx = avcodec_alloc_context3(nullptr);
	assert(ctx);
	wivrn::configure_rate_control(*ctx, {}, 50'000'000, 90);
	assert(ctx->bit_rate == 50'000'000 && ctx->rc_max_rate == 50'000'000);
	assert(ctx->rc_buffer_size == 555555 && !(ctx->flags & AV_CODEC_FLAG_QSCALE));
	ctx->rc_min_rate = 50'000'000;
	ctx->rc_initial_buffer_occupancy = 10000;
	wivrn::configure_rate_control(*ctx, 24, 100'000'000, 90);
	assert(ctx->flags & AV_CODEC_FLAG_QSCALE);
	assert(ctx->global_quality == 24 * FF_QP2LAMBDA);
	assert(ctx->i_quant_factor == 1 && ctx->i_quant_offset == 0);
	assert(ctx->b_quant_factor == 1 && ctx->b_quant_offset == 0);
	assert(!ctx->bit_rate && !ctx->rc_max_rate && !ctx->rc_min_rate);
	assert(!ctx->rc_buffer_size && !ctx->rc_initial_buffer_occupancy);
	avcodec_free_context(&ctx);
	std::cout << "CQP parsing, conflict rejection, QP scaling and zero-VBV checks passed\n";
}
