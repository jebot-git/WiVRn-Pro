// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <filesystem>
#include <vector>
namespace wivrn {
inline std::filesystem::path find_system_xrizer(const std::vector<std::filesystem::path> & roots = {
    "/usr/local/lib64/xrizer", "/usr/local/lib/xrizer", "/usr/lib64/xrizer",
    "/usr/lib/xrizer", "/usr/lib/x86_64-linux-gnu/xrizer", "/usr/lib/aarch64-linux-gnu/xrizer", "/opt/xrizer"})
{
    for (const auto & root : roots) {
        std::error_code error;
        if (std::filesystem::is_regular_file(root / "bin/linux64/vrclient.so", error))
            return root;
    }
    return {};
}
}
