// SPDX-License-Identifier: MIT
#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <ostream>
#include <type_traits>

namespace wivrn::codec_test::selection_trace {
inline constexpr std::size_t decoder_count = 3, history_slots = 3;
inline constexpr uint64_t record_limit = 4096;

// Plain fixed-size copies only. No resource ownership, pose vectors, allocation,
// clock calls or serialization are needed while the live frames mutex is held.
struct Frame {
    bool present = false;
    uint64_t frame = 0, warp = 0;
    int64_t view_display_time = 0, receive = 0, ready = 0;
    bool alpha = false;
};
enum class Reason { unavailable, closest_target, no_complete_fallback };
struct Record {
    uint64_t sequence = 0;
    bool after_fence = false, alpha_required = false, repeat_previous = false;
    bool projection_submitted = false;
    int64_t wait_begin = 0, wait_end = 0, render_entry = 0;
    int64_t selection_begin = 0, selection_end = 0, predicted_display = 0;
    int64_t end_frame_return = 0;
    Reason reason = Reason::unavailable;
    std::array<Frame, decoder_count * history_slots> history{};
    std::array<Frame, decoder_count> selected{};
};
static_assert(std::is_trivially_copyable_v<Frame> && std::is_trivially_copyable_v<Record>);

inline bool same_selection(const std::array<Frame, decoder_count>& a,
                           const std::array<Frame, decoder_count>& b) {
    if (!a[0].present || !a[1].present || !b[0].present || !b[1].present)
        return false;
    for (std::size_t i = 0; i < decoder_count; ++i)
        if (a[i].present != b[i].present ||
            (a[i].present && (a[i].frame != b[i].frame || a[i].warp != b[i].warp)))
            return false;
    return true;
}

struct Budget {
    bool enabled = false;
    uint64_t records = 0, dropped = 0;
    bool capture() const { return enabled && records < record_limit; }
    // Called only following successful xrEndFrame for a rendered selection.
    // Once capped, the existing presentation log carries the explicit drop count.
    bool commit() {
        if (!enabled) return false;
        if (records >= record_limit) { ++dropped; return false; }
        ++records;
        return true;
    }
};

inline const char* reason_name(Reason value) {
    switch (value) {
    case Reason::closest_target: return "closest_target";
    case Reason::no_complete_fallback: return "no_complete_fallback";
    default: return "unavailable";
    }
}

inline void write_frame(std::ostream& out, const Frame& frame) {
    out << "\"present\":" << (frame.present ? "true" : "false");
    if (frame.present)
        out << ",\"frame\":" << frame.frame << ",\"warp\":" << frame.warp
            << ",\"view_display_time_xr_ns\":" << frame.view_display_time
            << ",\"receive_xr_ns\":" << frame.receive << ",\"ready_xr_ns\":" << frame.ready
            << ",\"alpha\":" << (frame.alpha ? "true" : "false");
}

// Invoke outside frames_mutex, only after successful xrEndFrame.
inline void write_json(std::ostream& out, const Record& r) {
    out << "{\"schema\":1,\"sequence\":" << r.sequence
        << ",\"selection_phase\":\"" << (r.after_fence ? "after-fence" : "early") << "\""
        << ",\"wait_frame_begin_xr_ns\":" << r.wait_begin
        << ",\"wait_frame_end_xr_ns\":" << r.wait_end
        << ",\"render_entry_xr_ns\":" << r.render_entry
        << ",\"selection_begin_xr_ns\":" << r.selection_begin
        << ",\"selection_end_xr_ns\":" << r.selection_end
        << ",\"predicted_display_xr_ns\":" << r.predicted_display
        << ",\"end_frame_return_xr_ns\":" << r.end_frame_return
        << ",\"alpha_required\":" << (r.alpha_required ? "true" : "false")
        << ",\"projection_submitted\":" << (r.projection_submitted ? "true" : "false")
        << ",\"reason\":\"" << reason_name(r.reason) << "\""
        << ",\"repeat_previous\":" << (r.repeat_previous ? "true" : "false")
        << ",\"history\":[";
    for (std::size_t i = 0; i < r.history.size(); ++i) {
        if (i) out << ',';
        out << "{\"decoder\":" << i / history_slots << ",\"slot\":" << i % history_slots << ',';
        write_frame(out, r.history[i]); out << '}';
    }
    out << "],\"selected\":[";
    for (std::size_t i = 0; i < r.selected.size(); ++i) {
        if (i) out << ',';
        out << "{\"decoder\":" << i << ',';
        write_frame(out, r.selected[i]); out << '}';
    }
    out << "]}\n";
}
}
