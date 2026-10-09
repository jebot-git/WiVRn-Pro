// SPDX-License-Identifier: MIT
#include "compact.hpp"
#include "bounded_file.hpp"
#include <chrono>
#include <iostream>

int main() {
    compact::self_test();
    const auto directory = std::filesystem::temp_directory_path() /
        ("compact-file-test-" + std::to_string(
            std::chrono::steady_clock::now().time_since_epoch().count()));
    if (!std::filesystem::create_directory(directory))
        throw std::runtime_error("test directory exists");
    struct Cleanup {
        std::filesystem::path path;
        ~Cleanup() { std::error_code ignored; std::filesystem::remove_all(path, ignored); }
    } cleanup{directory};
    const auto file = directory / "input";
    { std::ofstream output(file, std::ios::binary); output << "abcd"; }
    if (compact::read_bounded(file, 4) != std::vector<uint8_t>{'a','b','c','d'})
        throw std::runtime_error("exact file-size cap failed");
    bool rejected = false;
    try { compact::read_bounded(file, 3); }
    catch (const std::runtime_error&) { rejected = true; }
    if (!rejected) throw std::runtime_error("oversize file accepted");
    { std::ofstream output(file, std::ios::binary | std::ios::trunc); }
    if (!compact::read_bounded(file, 0).empty())
        throw std::runtime_error("empty file changed");
    rejected = false;
    try { compact::read_bounded(directory / "missing", 4); }
    catch (const std::filesystem::filesystem_error&) { rejected = true; }
    if (!rejected) throw std::runtime_error("missing file accepted");
    std::cout << "Compact validator: headers/q maps, truncation, malformed cases, "
                 "3000 mutations, and bounded-file tests passed\n";
}
