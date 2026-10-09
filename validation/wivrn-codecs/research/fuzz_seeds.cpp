// SPDX-License-Identifier: MIT
#include "codec.hpp"
#include <array>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const std::filesystem::path out(argv[1]);
    if (!std::filesystem::create_directory(out)) return 2;
    unsigned index = 0;
    for (auto dimensions : {std::array{1u, 1u}, std::array{17u, 19u}, std::array{64u, 64u}})
        for (unsigned kind = 0; kind < 4; ++kind)
            for (unsigned q : {1u, 8u, 17u, 1024u})
                for (bool tile_q : {false, true}) {
                    auto pixels = kind == 3 ? std::vector<uint32_t>(dimensions[0] * dimensions[1], 0xff456789)
                                            : vr444::scene(dimensions[0], dimensions[1], kind);
                    std::vector<uint32_t> quantizers;
                    if (tile_q) {
                        quantizers.resize(((dimensions[0] + 15) / 16) * ((dimensions[1] + 15) / 16));
                        for (size_t i = 0; i < quantizers.size(); ++i)
                            quantizers[i] = std::array{1u, 8u, 17u, 1024u}[i % 4];
                    }
                    const auto bytes = vr444::pack(vr444::encode(pixels, dimensions[0], dimensions[1], q, quantizers));
                    std::ofstream stream(out / ("seed-" + std::to_string(index++)), std::ios::binary);
                    stream.write(reinterpret_cast<const char*>(bytes.data()), bytes.size());
                    if (!stream) return 3;
                }
    return index == 96 ? 0 : 4;
}
