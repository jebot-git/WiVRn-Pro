// SPDX-License-Identifier: MIT
#pragma once
#include <algorithm>
#include <bit>
#include <cstring>
#include <charconv>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string_view>
#include <vector>

namespace wivrn::codec_test::pacer_tail {
inline constexpr uint32_t minimum_samples = 128;
inline constexpr int64_t downward_step_ns = 1'000'000;
inline uint32_t parse_window(const char * text) {
    if (!text || !*text) return 0;
    uint32_t value = 0;
    const std::string_view s(text);
    auto [end, error] = std::from_chars(s.data(), s.data()+s.size(), value);
    if (error != std::errc{} || end != s.data()+s.size() ||
        (value != 0 && (value < 360 || value > 1800)))
        throw std::runtime_error("WIVRN_CODEC_TEST_PACER_WINDOW_FRAMES requires 0 or 360..1800");
    return value;
}
inline int64_t checked_margin_bits(uint32_t bits) {
    // No float expression: -ffast-math permits assuming even float parameters
    // are finite. Interpret the stored IEEE754 representation with integers.
    // Exact positive float value times 1e6 is truncated, never rounded upward.
    if (bits > 0x41a00000u) return -1; // Negative, >20, infinity or NaN.
    const uint32_t exponent=(bits>>23)&255, fraction=bits&0x7fffff;
    const uint64_t mantissa=exponent ? fraction|0x800000u : fraction;
    const uint32_t shift=exponent ? 150-exponent : 149;
    return shift>=64 ? 0 : int64_t((mantissa*1'000'000u)>>shift);
}
inline int64_t checked_margin_ns(const float & value) {
    static_assert(sizeof(float)==sizeof(uint32_t) && std::numeric_limits<float>::is_iec559);
    uint32_t bits;std::memcpy(&bits,&value,sizeof(bits));return checked_margin_bits(bits);
}
struct Decision {
    int64_t boundary = -1, newest = -1, oldest_recent = -1, previous_newest = -1;
    uint32_t window = 0, legacy_count = 0, recent_count = 0;
    int64_t legacy_tail = 0, recent_tail = 0, margin = 0, previous = 0, applied = 0;
    bool valid = false, recent_used = false, slew_limited = false, decrease_blocked = false;
};
// Frame age is relative to the newest valid feedback in this immutable bank,
// bounded by the actual last issued frame ID captured with that bank. Timestamps
// are host-domain scheduled present and clock-converted client ready, not pure
// decode duration or photon timestamps. Keep the original order statistic.
template<class Samples>
Decision choose(const Samples & samples, uint32_t window, int64_t boundary,
                int64_t margin, int64_t previous, int64_t previous_newest = -1) {
    Decision out{.boundary=boundary,.previous_newest=previous_newest,.window=window,.margin=margin,.previous=previous};
    if ((window != 0 && (window < 360 || window > 1800)) || boundary < 0 ||
        margin < 0 || margin > 20'000'000 || previous < 0 || previous_newest < -1) return out;
    std::vector<int64_t> legacy, recent, identities;
    if (samples.size() > 5000) return out;
    legacy.reserve(samples.size()); recent.reserve(samples.size()); identities.reserve(samples.size());
    for (const auto & s : samples) {
        if (s.decoded <= s.present) continue; // Identical legacy admission.
        if (s.present <= 0 || s.frame_id < 0 || s.frame_id > boundary) return out;
        out.newest = std::max(out.newest,s.frame_id);
        legacy.push_back(s.decoded-s.present); identities.push_back(s.frame_id);
    }
    if (out.newest < previous_newest) return out;
    if (legacy.empty()) return out; // Leave current budget unchanged.
    std::sort(identities.begin(),identities.end());
    if (std::adjacent_find(identities.begin(),identities.end()) != identities.end()) return out;
    const auto low = std::max<int64_t>(0, out.newest-int64_t(window)+1);
    for (const auto & s : samples) {
        if (s.decoded > s.present && s.frame_id >= low && s.frame_id <= out.newest) {
            recent.push_back(s.decoded-s.present);
            if (out.oldest_recent < 0 || s.frame_id < out.oldest_recent) out.oldest_recent=s.frame_id;
        }
    }
    auto quantile=[](auto & v) {
        auto at=v.begin()+(v.size()*995)/1000;
        std::nth_element(v.begin(),at,v.end());return *at;
    };
    out.legacy_count=legacy.size();out.recent_count=recent.size();
    out.legacy_tail=quantile(legacy);
    if (!recent.empty()) out.recent_tail=quantile(recent);
    out.recent_used=window && out.recent_count>=minimum_samples;
    auto tail=out.recent_used ? out.recent_tail : out.legacy_tail;
    if (tail > std::numeric_limits<int64_t>::max()-margin) return out;
    out.applied=tail+margin;
    // Never undershoot the selected tail+margin. Sparse fallback and increases
    // apply immediately; only a supported recent-window decrease is slewed.
    if (previous > out.applied && out.newest == previous_newest) {
        out.applied=previous;out.decrease_blocked=true;
    } else if (out.recent_used && previous > out.applied && previous-out.applied>downward_step_ns) {
        out.applied=previous-downward_step_ns;out.slew_limited=true;
    }
    out.valid=true;return out;
}

// Worker-only append; export only after that worker has joined. Bounded summary
// telemetry does not pretend to include the underlying feedback samples.
struct Update { uint64_t pacer=0, epoch=0, batch=0; bool stale_epoch=false; Decision decision; };
class Trace {
    static constexpr std::size_t capacity=4096;
    FILE * output=nullptr;
    std::unique_ptr<Update[]> records;
    std::size_t count=0, dropped=0;
public:
    const uint32_t window;
    explicit Trace(const char * option, const char * path, bool predictor_trace) : window(parse_window(option)) {
        if (!window) {
            if (path && *path) throw std::runtime_error("pacer tail output requires enabled window");
            return;
        }
        if (!predictor_trace || !path || path[0]!='/')
            throw std::runtime_error("pacer window requires predictor trace and absolute new tail trace");
        records=std::make_unique<Update[]>(capacity);
        output=std::fopen(path,"wx");
        if (!output) throw std::runtime_error("cannot create new pacer tail trace");
    }
    Trace(const Trace&)=delete;Trace& operator=(const Trace&)=delete;
    void append(Update value) noexcept {
        if (!output) return;
        if (count==capacity) { ++dropped;return; }
        records[count++]=value;
    }
    ~Trace() {
        if (!output) return;
        std::fprintf(output,"{\"type\":\"header\",\"schema\":1,\"window_frames\":%u,\"minimum_samples\":128,\"quantile_numerator\":995,\"quantile_denominator\":1000,\"downward_step_ns\":1000000,\"capacity\":4096,\"diagnostic_only\":true,\"raw_feedback_recorded\":false}\n",window);
        for(std::size_t i=0;i<count;++i) {
            const auto & r=records[i];const auto & d=r.decision;
            std::fprintf(output,"{\"type\":\"update\",\"sequence\":%zu,\"pacer_id\":%llu,\"epoch\":%llu,\"batch\":%llu,\"stale_epoch\":%s,\"boundary_frame\":%lld,\"newest_feedback_frame\":%lld,\"oldest_recent_frame\":%lld,\"previous_newest_frame\":%lld,\"legacy_count\":%u,\"recent_count\":%u,\"legacy_tail_ns\":%lld,\"recent_tail_ns\":%lld,\"margin_ns\":%lld,\"previous_budget_ns\":%lld,\"applied_budget_ns\":%lld,\"valid\":%s,\"recent_used\":%s,\"slew_limited\":%s,\"decrease_blocked\":%s}\n",
                i+1,(unsigned long long)r.pacer,(unsigned long long)r.epoch,(unsigned long long)r.batch,r.stale_epoch?"true":"false",
                (long long)d.boundary,(long long)d.newest,(long long)d.oldest_recent,(long long)d.previous_newest,d.legacy_count,d.recent_count,
                (long long)d.legacy_tail,(long long)d.recent_tail,(long long)d.margin,(long long)d.previous,(long long)d.applied,
                d.valid?"true":"false",d.recent_used?"true":"false",d.slew_limited?"true":"false",d.decrease_blocked?"true":"false");
        }
        if (!std::ferror(output)) std::fprintf(output,"{\"type\":\"footer\",\"records\":%zu,\"dropped\":%zu,\"complete\":true}\n",count,dropped);
        std::fclose(output);
    }
};
} // namespace wivrn::codec_test::pacer_tail
