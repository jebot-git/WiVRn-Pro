// SPDX-License-Identifier: MIT
#pragma once
#include <filesystem>
#include <fstream>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

namespace wivrn::codec_test::quality_sampler {
enum class Mode { linear, nearest };
inline Mode parse(std::optional<std::string_view> token) {
    if (!token || *token=="off") return Mode::linear;
    if (*token=="q1-nearest") return Mode::nearest;
    throw std::runtime_error("quality sampler requires off or q1-nearest");
}
inline std::optional<std::string> read(const std::filesystem::path& path) {
    std::error_code error;auto status=std::filesystem::symlink_status(path,error);
    if (status.type()==std::filesystem::file_type::not_found &&
        (!error || error==std::errc::no_such_file_or_directory)) return std::nullopt;
    if (error || !std::filesystem::is_regular_file(status))
        throw std::runtime_error("quality sampler requires a regular configuration file");
    std::ifstream file(path,std::ios::binary);
    if (!file) throw std::runtime_error("cannot read quality sampler configuration");
    char buffer[65];file.read(buffer,sizeof(buffer));auto count=file.gcount();
    if (file.bad() || count>64) throw std::runtime_error("quality sampler configuration bound");
    std::string token(buffer,size_t(count));
    if (!token.empty() && token.back()=='\n') token.pop_back();
    if (!token.empty() && token.back()=='\r') token.pop_back();
    return token;
}
inline Mode client(Mode mode,bool capture,std::string_view payload,std::string_view compression,bool paired) {
    if (mode==Mode::nearest && (!capture || payload!="rrb32" || compression!="none" || !paired))
        throw std::runtime_error("quality nearest requires paired RRB, no outer compression and submitted capture");
    return mode;
}
inline Mode server(std::optional<std::string_view> token,bool enabled,std::string_view codec,
                   std::string_view q,std::string_view compression,std::string_view capture,std::string_view trace) {
    auto mode=parse(token);
    if (mode==Mode::nearest && (!enabled || codec!="rrb32-q1" || q!="1" || compression!="none"
                              || capture!="1" || trace.empty() || trace.front()!='/'))
        throw std::runtime_error("quality nearest requires explicit host RRB-Q1/capture/source trace profile");
    return mode;
}
}
