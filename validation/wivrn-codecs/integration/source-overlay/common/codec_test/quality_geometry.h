// SPDX-License-Identifier: MIT
#pragma once
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string_view>

namespace wivrn::codec_test::quality_geometry {
// Explicit quality-only profiles, not scaling or arbitrary dimensions. Missing
// configuration and "off" retain the existing diagnostic/latency geometry.
struct Profile {
    uint16_t width=576, height=640;
    bool quality=false;
    friend constexpr bool operator==(Profile,Profile)=default;
};
inline Profile parse(std::optional<std::string_view> value) {
    if (!value || *value=="off") return {};
    if (*value=="q1-768x864") return {768,864,true};
    if (*value=="q1-960x1056") return {960,1056,true};
    if (*value=="q1-1152x1280") return {1152,1280,true};
    throw std::runtime_error("quality geometry requires off or an exact supported q1-WxH token");
}
inline Profile client(std::optional<std::string_view> value,bool submitted_capture,std::string_view payload) {
    auto profile=parse(value);
    if (profile.quality && (!submitted_capture || payload!="rrb32"))
        throw std::runtime_error("larger quality geometry requires RRB32 and submitted-image capture");
    return profile;
}
inline Profile server(std::optional<std::string_view> value,std::string_view mode,std::string_view q,
                      std::string_view compression,std::string_view capture,std::string_view source_trace,
                      uint32_t peer_width,uint32_t peer_height) {
    auto profile=parse(value);
    if (profile.quality && (mode!="rrb32-q1" || q!="1" || compression!="none" || capture!="1" ||
                            source_trace.empty() || source_trace.front()!='/'))
        throw std::runtime_error("larger quality geometry requires explicit exact-Q1, capture and source-trace profile");
    if (peer_width!=profile.width || peer_height!=profile.height)
        throw std::runtime_error("codec test peer geometry does not match explicit host quality profile");
    return profile;
}
} // namespace wivrn::codec_test::quality_geometry
