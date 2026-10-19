// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <cerrno>
#include <filesystem>
#include <span>
#include <string>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
namespace wivrn
{
// Publish an immutable snapshot. A reader keeps the previous inode while the
// next frame is written; no lock or partial frame can interrupt a capture.
inline bool publish_hands_frame(const std::filesystem::path & path, std::span<const std::byte> header, std::span<const std::byte> pixels)
{
    std::string temporary = path.string() + ".XXXXXX";
    int fd = mkostemp(temporary.data(), O_CLOEXEC);
    if (fd < 0) return false;
    bool good = fchmod(fd, 0600) == 0;
    for (auto data : {header, pixels}) {
        while (good && !data.empty()) {
            auto n = write(fd, data.data(), data.size());
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) { good = false; break; }
            data = data.subspan(size_t(n));
        }
    }
    if (close(fd)) good = false;
    if (good) good = rename(temporary.c_str(), path.c_str()) == 0;
    if (!good) unlink(temporary.c_str());
    return good;
}
}
