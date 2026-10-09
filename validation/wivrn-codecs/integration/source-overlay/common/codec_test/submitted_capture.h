// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
// Diagnostic acquisition contract; no image quality or displayed-pixel verdict.
#include <simdjson.h>
#include <array>
#include <cerrno>
#include <cstdint>
#include <filesystem>
#include <fcntl.h>
#include <fstream>
#include <span>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace wivrn::codec_test::submitted_capture {
struct Config { bool enabled = false; uint32_t max_pairs = 6, frame_stride = 90; };
inline Config parse(std::string_view text) {
    if (text.empty() || text.size() > 1024) throw std::runtime_error("quality capture config size");
    simdjson::dom::parser parser;
    auto document = parser.parse(text.data(), text.size());
    Config config; unsigned seen = 0;
    for (auto field : document.get_object()) {
        unsigned bit;
        if (field.key == "enabled") { bit = 1; config.enabled = bool(field.value.get_bool()); }
        else if (field.key == "max_pairs") { bit = 2; auto n = uint64_t(field.value.get_uint64());
            if (n < 1 || n > 8) throw std::runtime_error("quality capture max_pairs"); config.max_pairs = n; }
        else if (field.key == "frame_stride") { bit = 4; auto n = uint64_t(field.value.get_uint64());
            if (n < 1 || n > 900) throw std::runtime_error("quality capture frame_stride"); config.frame_stride = n; }
        else throw std::runtime_error("quality capture unknown config key");
        if (seen & bit) throw std::runtime_error("quality capture duplicate config key");
        seen |= bit;
    }
    if (!(seen & 1)) throw std::runtime_error("quality capture enabled key required");
    return config;
}
inline Config load(const std::filesystem::path& path) {
    if (!std::filesystem::exists(path)) return {};
    if (std::filesystem::file_size(path) > 1024) throw std::runtime_error("quality capture config size");
    std::ifstream file(path, std::ios::binary);
    if (!file) throw std::runtime_error("quality capture config read");
    std::string text((std::istreambuf_iterator<char>(file)), {});
    return parse(text);
}
inline uint64_t bytes(uint32_t width, uint32_t height) {
    if (!width || !height || width > 4096 || height > 4096 || uint64_t(width)*height > 4*1024*1024)
        throw std::runtime_error("quality capture eye exceeds 4MP/4096-axis bound");
    return uint64_t(width)*height*4;
}
struct Budget {
    Config config;
    uint32_t attempts = 0;
    bool have_frame = false;
    uint64_t last_frame = 0;
    bool take(uint64_t frame) {
        if (!config.enabled || attempts >= config.max_pairs) return false;
        if (have_frame && (frame <= last_frame || frame-last_frame < config.frame_stride)) return false;
        have_frame = true; last_frame = frame; ++attempts; return true;
    }
};
// O_EXCL never overwrites existing evidence. A short write leaves an incomplete
// artifact and throws; the final pair.json completion marker is written last.
inline void exclusive_write(const std::filesystem::path& path, std::span<const uint8_t> data) {
    int fd = ::open(path.c_str(), O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC, 0600);
    if (fd < 0) throw std::runtime_error("quality capture exclusive output open");
    size_t offset = 0;
    while (offset < data.size()) {
        const auto n = ::write(fd, data.data()+offset, data.size()-offset);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { ::close(fd); throw std::runtime_error("quality capture output write"); }
        offset += size_t(n);
    }
    const int synced = ::fsync(fd), closed = ::close(fd);
    if (synced || closed) throw std::runtime_error("quality capture output sync/close");
}
inline void exclusive_write(const std::filesystem::path& path, const std::string& text) {
    exclusive_write(path, std::span(reinterpret_cast<const uint8_t*>(text.data()), text.size()));
}
} // namespace
