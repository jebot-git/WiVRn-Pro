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

#pragma once
#include "video_encoder.h"
#include "pyrowave_encoder.h"
#include "utils/wivrn_vk_bundle.h"

namespace wivrn
{
class video_encoder_pyrowave : public video_encoder
{
    vk_bundle & bundle;
    struct slot_state
    {
        std::unique_ptr<PyroWave::Encoder> encoder;
        vk::raii::CommandPool pool = nullptr;
        vk::raii::CommandBuffer command = nullptr;
        vk::raii::Fence fence = nullptr;
        std::array<vk::raii::ImageView, 3> views{nullptr, nullptr, nullptr};
        buffer_allocation meta, output;
    };
    std::array<slot_state, num_slots> state;
    uint64_t bitrate;
    float fps;
public:
    video_encoder_pyrowave(vk_bundle &, const encoder_settings &, uint8_t stream_idx);
    ~video_encoder_pyrowave();
private:
    void present_image(vk::Image, vk::SemaphoreSubmitInfo, uint8_t slot, uint64_t frame_index) override;
    std::optional<data> encode(uint8_t slot, uint64_t frame_index) override;
    const char * backend_name() const override { return "pyrowave"; }
};
}
