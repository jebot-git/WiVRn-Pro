// SPDX-License-Identifier: MIT
#pragma once
#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
#include <span>

namespace wivrn::codec_test::stereo_publication {

// Compare every serialized view field, including float bit patterns, not padding
// or a probabilistic hash. The native translation-unit check instantiates this
// with video_stream_data_shard::view_info_t.
template<class View>
bool same_view(const View& a, const View& b) noexcept {
    if (a.display_time != b.display_time || a.alpha != b.alpha) return false;
    const auto same_float = [](float x, float y) { return std::bit_cast<uint32_t>(x) == std::bit_cast<uint32_t>(y); };
    for (unsigned eye = 0; eye < 2; ++eye) {
        const auto& p = a.pose[eye]; const auto& q = b.pose[eye];
        const auto& f = a.fov[eye]; const auto& g = b.fov[eye];
        if (!same_float(p.orientation.x, q.orientation.x) || !same_float(p.orientation.y, q.orientation.y)
            || !same_float(p.orientation.z, q.orientation.z) || !same_float(p.orientation.w, q.orientation.w)
            || !same_float(p.position.x, q.position.x) || !same_float(p.position.y, q.position.y)
            || !same_float(p.position.z, q.position.z) || !same_float(f.angleLeft, g.angleLeft)
            || !same_float(f.angleRight, g.angleRight) || !same_float(f.angleUp, g.angleUp)
            || !same_float(f.angleDown, g.angleDown) || a.foveation[eye].x != b.foveation[eye].x
            || a.foveation[eye].y != b.foveation[eye].y) return false;
    }
    return true;
}

// Caller holds the existing decoder shared lock and frames exclusive lock, and
// has checked both accumulator identities. Both producer uploads must already
// have completed. No callback, allocation, I/O or throwing work occurs between
// the first history change and completion of the second history change.
// On false, incoming handles and both histories are unchanged. On true, incoming
// holds the evicted objects so their feedback/lifetime can be handled unlocked.
template<class Handle>
bool replace_pair(std::array<std::span<Handle>, 2> histories,
                  std::array<Handle, 2>& incoming, int64_t ready) noexcept {
    if (!incoming[0] || !incoming[1] || histories[0].empty() || histories[1].empty() || ready <= 0)
        return false;
    const auto& left = *incoming[0]; const auto& right = *incoming[1];
    if (left.feedback.stream_index != 0 || right.feedback.stream_index != 1
        || left.feedback.frame_index != right.feedback.frame_index
        || !same_view(left.view_info, right.view_info)
        || left.feedback.received_first_packet <= 0 || right.feedback.received_first_packet <= 0
        || left.feedback.received_first_packet > ready || right.feedback.received_first_packet > ready)
        return false;
    for (unsigned eye = 0; eye < 2; ++eye)
        for (const auto& retained : histories[eye])
            if (retained && retained->feedback.frame_index >= left.feedback.frame_index) return false;
    incoming[0]->feedback.received_from_decoder = ready;
    incoming[1]->feedback.received_from_decoder = ready;
    for (unsigned eye = 0; eye < 2; ++eye) {
        auto history = histories[eye];
        std::rotate(history.begin(), history.begin() + 1, history.end());
        std::swap(incoming[eye], history.back());
    }
    return true;
}
} // namespace wivrn::codec_test::stereo_publication
