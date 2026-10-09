// SPDX-License-Identifier: MIT
#pragma once
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <vector>
namespace compact {
inline std::vector<uint8_t> read_bounded(const std::filesystem::path& path,
                                       size_t limit) {
    // Check before allocation; fixed-length read plus EOF check also rejects a
    // file that changes size between stat and read without growing the buffer.
    const auto size = std::filesystem::file_size(path);
    if (size > limit)
        throw std::runtime_error("file exceeds byte cap: " + path.string());
    std::ifstream input(path, std::ios::binary);
    if (!input)
        throw std::runtime_error("cannot open: " + path.string());
    std::vector<uint8_t> result(static_cast<size_t>(size));
    if (size && !input.read(reinterpret_cast<char*>(result.data()), size))
        throw std::runtime_error("short file read: " + path.string());
    if (input.peek() != std::char_traits<char>::eof() || input.bad())
        throw std::runtime_error("file changed or read failed: " + path.string());
    return result;
}
} // namespace compact
