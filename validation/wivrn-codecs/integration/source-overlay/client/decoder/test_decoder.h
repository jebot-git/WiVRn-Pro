// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "decoder.h"
namespace wivrn {
class test_stereo_state;
std::shared_ptr<test_stereo_state> make_test_stereo_state();
std::shared_ptr<decoder> make_test_decoder(vk::raii::Device&,vk::raii::PhysicalDevice&,uint32_t,
    const to_headset::video_stream_description&,uint8_t,std::weak_ptr<scenes::stream>,shard_accumulator*);
}
