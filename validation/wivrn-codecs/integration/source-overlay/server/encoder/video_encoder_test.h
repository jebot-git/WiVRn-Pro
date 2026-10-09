// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "video_encoder.h"
#include <memory>
namespace wivrn {
std::unique_ptr<video_encoder> make_test_encoder(vk_bundle&, const encoder_settings&, uint8_t);
}
