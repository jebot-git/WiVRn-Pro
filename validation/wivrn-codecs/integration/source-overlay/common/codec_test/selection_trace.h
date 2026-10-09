// SPDX-License-Identifier: MIT
#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
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
enum class Policy { closest_target, bounded_newest };
enum class Reason { unavailable, closest_target, bounded_newest, no_complete_fallback, no_eligible_fallback };

// Count render attempts separately from successful projection submissions.
// Constant storage; render-thread owned. No allocation, clocks, locks, or I/O.
enum class Skip : std::size_t { should_not_render, decoders_unavailable, shutdown, no_complete_pair, retained_pair, count };
inline constexpr std::array<const char*, static_cast<std::size_t>(Skip::count)> skip_names{
    "should_not_render", "decoders_unavailable", "shutdown", "no_complete_pair", "retained_pair"};
inline bool increment_attempt_count(uint64_t& value) {
    if (value >= static_cast<uint64_t>(std::numeric_limits<int64_t>::max())) return false;
    ++value;
    return true;
}
struct Attempts {
    uint64_t render_attempt = 0, previous_submission_attempt = 0;
    int64_t previous_submission_target = 0;
    std::array<uint64_t, skip_names.size()> skipped{};
    uint64_t aborted = 0;
    bool valid = true;
};
class AttemptLedger {
    Attempts interval;
    int64_t previous_render_target = 0, current_target = 0;
    bool active = false;
    void increment(uint64_t& value) {
        if (!increment_attempt_count(value)) interval.valid = false;
    }
public:
    bool enabled = false;
    void begin(int64_t target, int64_t prior_target) {
        if (!enabled) return;
        if (active) abort();
        if (target <= 0 || prior_target != previous_render_target ||
            (previous_render_target && target <= previous_render_target))
            interval.valid = false;
        increment(interval.render_attempt);
        current_target = target;
        previous_render_target = target;
        active = true;
    }
    void skip(Skip reason) {
        if (!enabled) return;
        const auto index = static_cast<std::size_t>(reason);
        if (!active || index >= interval.skipped.size()) { interval.valid = false; return; }
        increment(interval.skipped[index]);
        active = false;
    }
    void abort() {
        if (!enabled || !active) return;
        increment(interval.aborted);
        interval.valid = false; // A caught failure is never a benign skipped frame.
        active = false;
    }
    Attempts submit() {
        if (!enabled) return {};
        if (!active) interval.valid = false;
        const auto snapshot = interval;
        interval.previous_submission_attempt = interval.render_attempt;
        interval.previous_submission_target = current_target;
        interval.skipped.fill(0);
        interval.aborted = 0;
        active = false;
        return snapshot;
    }
};
// Any return/exception that did not explicitly finish an attempt poisons future
// accounting. Failed xrEndFrame or partial rendering cannot masquerade as skips.
struct AttemptGuard {
    AttemptLedger& ledger;
    AttemptGuard(AttemptLedger& value, int64_t target, int64_t prior) : ledger(value) { ledger.begin(target, prior); }
    ~AttemptGuard() { ledger.abort(); }
    AttemptGuard(const AttemptGuard&) = delete;
    AttemptGuard& operator=(const AttemptGuard&) = delete;
};

struct Record {
    uint32_t schema = 2; // Offline policy fixtures remain schema 2; live opts in to 3.
    uint64_t sequence = 0;
    bool after_fence = false, alpha_required = false, repeat_previous = false;
    bool projection_submitted = false;
    int64_t wait_begin = 0, wait_end = 0, render_entry = 0;
    int64_t selection_begin = 0, selection_end = 0, predicted_display = 0;
    int64_t end_frame_return = 0;
    Policy policy = Policy::closest_target;
    int64_t predicted_period = 0, previous_predicted_display = 0, max_ahead = 0;
    bool selected_alpha_required = false;
    Reason reason = Reason::unavailable;
    std::array<Frame, decoder_count * history_slots> history{};
    std::array<Frame, decoder_count> selected{}, closest{}, previous{};
    std::array<int, decoder_count> chosen_slots{-1, -1, -1};
    std::array<bool, history_slots> eligible_left_slots{};
    Attempts attempts;
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
    case Reason::bounded_newest: return "bounded_newest";
    case Reason::no_complete_fallback: return "no_complete_fallback";
    case Reason::no_eligible_fallback: return "no_eligible_fallback";
    default: return "unavailable";
    }
}

inline const char* policy_name(Policy value) {
    return value == Policy::bounded_newest ? "bounded-newest" : "closest-target";
}

inline void write_frame(std::ostream& out, const Frame& frame) {
    out << "\"present\":" << (frame.present ? "true" : "false");
    if (frame.present)
        out << ",\"frame\":" << frame.frame << ",\"warp\":" << frame.warp
            << ",\"view_display_time_xr_ns\":" << frame.view_display_time
            << ",\"receive_xr_ns\":" << frame.receive << ",\"ready_xr_ns\":" << frame.ready
            << ",\"alpha\":" << (frame.alpha ? "true" : "false");
}

inline void write_attempts(std::ostream& out, const Attempts& a) {
    out << "{\"render_attempt\":" << a.render_attempt
        << ",\"previous_submission_attempt\":" << a.previous_submission_attempt
        << ",\"previous_submission_target_xr_ns\":" << a.previous_submission_target
        << ",\"aborted\":" << a.aborted << ",\"valid\":" << (a.valid ? "true" : "false")
        << ",\"skipped\":{";
    for (std::size_t i = 0; i < a.skipped.size(); ++i) {
        if (i) out << ',';
        out << '\"' << skip_names[i] << "\":" << a.skipped[i];
    }
    out << "}}";
}

// Invoke outside frames_mutex, only after successful xrEndFrame.
inline void write_json(std::ostream& out, const Record& r) {
    out << "{\"schema\":" << r.schema << ",\"sequence\":" << r.sequence
        << ",\"selection_phase\":\"" << (r.after_fence ? "after-fence" : "early") << "\""
        << ",\"selection_policy\":\"" << policy_name(r.policy) << "\""
        << ",\"predicted_display_period_xr_ns\":" << r.predicted_period
        << ",\"previous_predicted_display_xr_ns\":" << r.previous_predicted_display
        << ",\"max_ahead_xr_ns\":" << r.max_ahead
        << ",\"wait_frame_begin_xr_ns\":" << r.wait_begin
        << ",\"wait_frame_end_xr_ns\":" << r.wait_end
        << ",\"render_entry_xr_ns\":" << r.render_entry
        << ",\"selection_begin_xr_ns\":" << r.selection_begin
        << ",\"selection_end_xr_ns\":" << r.selection_end
        << ",\"predicted_display_xr_ns\":" << r.predicted_display
        << ",\"end_frame_return_xr_ns\":" << r.end_frame_return
        << ",\"alpha_required\":" << (r.alpha_required ? "true" : "false")
        << ",\"selected_alpha_required\":" << (r.selected_alpha_required ? "true" : "false")
        << ",\"projection_submitted\":" << (r.projection_submitted ? "true" : "false")
        << ",\"reason\":\"" << reason_name(r.reason) << "\""
        << ",\"repeat_previous\":" << (r.repeat_previous ? "true" : "false")
        << ",\"history\":[";
    for (std::size_t i = 0; i < r.history.size(); ++i) {
        if (i) out << ',';
        out << "{\"decoder\":" << i / history_slots << ",\"slot\":" << i % history_slots << ',';
        write_frame(out, r.history[i]); out << '}';
    }
    auto frames = [&](const char* key, const auto& values) {
        out << "],\"" << key << "\":[";
        for (std::size_t i = 0; i < values.size(); ++i) {
            if (i) out << ',';
            out << "{\"decoder\":" << i << ',';
            write_frame(out, values[i]); out << '}';
        }
    };
    frames("selected", r.selected);
    frames("closest", r.closest);
    frames("previous", r.previous);
    out << "],\"chosen_slots\":[";
    for (std::size_t i = 0; i < r.chosen_slots.size(); ++i) {
        if (i) out << ',';
        out << r.chosen_slots[i];
    }
    out << "],\"eligible_left_slots\":[";
    for (std::size_t i = 0; i < r.eligible_left_slots.size(); ++i) {
        if (i) out << ',';
        out << (r.eligible_left_slots[i] ? "true" : "false");
    }
    out << ']';
    if (r.schema == 3) {
        out << ",\"render_attempts\":";
        write_attempts(out, r.attempts);
    }
    out << "}\n";
}
}
