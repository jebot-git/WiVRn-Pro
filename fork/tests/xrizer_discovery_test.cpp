// SPDX-License-Identifier: GPL-3.0-or-later
#include "xrizer_discovery.h"
#include <cassert>
#include <fstream>
#include <unistd.h>
int main() {
    auto root = std::filesystem::temp_directory_path() / ("wivrn-xrizer-test-" + std::to_string(getpid()));
    std::filesystem::create_directories(root / "empty");
    std::filesystem::create_directories(root / "installed/bin/linux64");
    assert(wivrn::find_system_xrizer({root / "empty", root / "installed"}).empty());
    std::ofstream(root / "installed/bin/linux64/vrclient.so").put('x');
    assert(wivrn::find_system_xrizer({root / "empty", root / "installed"}) == root / "installed");
    std::filesystem::remove_all(root);
}
