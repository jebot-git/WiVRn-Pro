// SPDX-License-Identifier: GPL-3.0-or-later
// Optional hardware smoke: explicitly A770 renderD129, no WiVRn server startup.
#include "encoder/ffmpeg/rate_control.h"
extern "C"
{
#include <libavutil/hwcontext.h>
#include <libavutil/log.h>
}
#include <cassert>
#include <cstring>
#include <iostream>
#include <stdexcept>

static void check(int status, const char * operation)
{
	if (status < 0)
	{
		char error[AV_ERROR_MAX_STRING_SIZE];
		av_strerror(status, error, sizeof(error));
		throw std::runtime_error(std::string(operation) + ": " + error);
	}
}
static void smoke(const char * name, int fps)
{
	AVBufferRef * va = nullptr;
	AVBufferRef * qsv = nullptr;
	check(av_hwdevice_ctx_create(&va, AV_HWDEVICE_TYPE_VAAPI, "/dev/dri/renderD129", nullptr, 0), "VAAPI device");
	check(av_hwdevice_ctx_create_derived(&qsv, AV_HWDEVICE_TYPE_QSV, va, 0), "QSV device");
	const auto * codec = avcodec_find_encoder_by_name(name);
	assert(codec);
	auto * ctx = avcodec_alloc_context3(codec);
	ctx->width = 576;
	ctx->height = 640;
	ctx->framerate = {fps, 1};
	ctx->time_base = {1, fps};
	ctx->gop_size = 240;
	ctx->max_b_frames = 0;
	ctx->pix_fmt = AV_PIX_FMT_NV12;
	ctx->hw_device_ctx = av_buffer_ref(qsv);
	ctx->color_range = AVCOL_RANGE_JPEG;
	ctx->colorspace = AVCOL_SPC_BT709;
	ctx->color_trc = AVCOL_TRC_BT709;
	ctx->color_primaries = AVCOL_PRI_BT709;
	wivrn::configure_rate_control(*ctx, 24, 100'000'000, fps);
	AVDictionary * opts = nullptr;
	for (const auto * pair : {"preset=veryfast", "async_depth=1", "low_power=1", "low_delay_brc=0", "look_ahead_depth=0", "forced_idr=1"})
		check(av_dict_parse_string(&opts, pair, "=", ",", 0), "options");
	av_dict_set(&opts, codec->id == AV_CODEC_ID_HEVC ? "gpb" : "look_ahead", "0", 0);
	check(avcodec_open2(ctx, codec, &opts), "open");
	assert(!av_dict_count(opts));
	av_dict_free(&opts);
	assert(!ctx->bit_rate && !ctx->rc_max_rate && !ctx->rc_buffer_size);
	auto * frame = av_frame_alloc();
	frame->format = AV_PIX_FMT_NV12;
	frame->width = 576;
	frame->height = 640;
	check(av_frame_get_buffer(frame, 64), "frame");
	auto * packet = av_packet_alloc();
	int packets = 0;
	int64_t bytes = 0;
	auto receive = [&] {
		for (;;)
		{
			const auto err = avcodec_receive_packet(ctx, packet);
			if (err == AVERROR(EAGAIN) || err == AVERROR_EOF)
				break;
			check(err, "receive");
			++packets;
			bytes += packet->size;
			av_packet_unref(packet);
		}
	};
	for (int n = 0; n < 90; ++n)
	{
		check(av_frame_make_writable(frame), "writable");
		for (int y = 0; y < 640; ++y)
			for (int x = 0; x < 576; ++x)
				frame->data[0][y * frame->linesize[0] + x] = (x + y + n) % 256;
		for (int y = 0; y < 320; ++y)
			memset(frame->data[1] + y * frame->linesize[1], 128, 576);
		frame->pts = n;
		frame->pict_type = n % 45 ? AV_PICTURE_TYPE_P : AV_PICTURE_TYPE_I;
		check(avcodec_send_frame(ctx, frame), "send");
		receive();
	}
	check(avcodec_send_frame(ctx, nullptr), "flush");
	receive();
	assert(packets == 90);
	std::cout << name << " fps=" << fps << " qp=24 packets=" << packets << " bytes=" << bytes << " PASS\n";
	av_packet_free(&packet);
	av_frame_free(&frame);
	avcodec_free_context(&ctx);
	av_buffer_unref(&qsv);
	av_buffer_unref(&va);
}
int main()
{
	av_log_set_level(AV_LOG_VERBOSE);
	for (const auto * codec : {"hevc_qsv", "h264_qsv"})
		for (int fps : {90, 72})
			smoke(codec, fps);
}
