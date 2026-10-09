// SPDX-License-Identifier: MIT
#pragma once
#include <cstdlib>
#include <string_view>
namespace wivrn::codec_test {
inline bool enabled() {
#ifdef WIVRN_CODEC_TESTS
    const char* mode=std::getenv("WIVRN_CODEC_TEST");
    return mode && (std::string_view(mode)=="vulkan-vr444"||std::string_view(mode)=="hevc-carrier"||std::string_view(mode)=="raw-rgba"||std::string_view(mode)=="rrb32-q1"||std::string_view(mode)=="rrb32-vr444");
#else
    return false;
#endif
}
}
