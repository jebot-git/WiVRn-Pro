// SPDX-License-Identifier: MIT
#include "intra_v2.hpp"
#include <fstream>
#include <iostream>
#include <stdexcept>
int main(int argc,char** argv){
    if(argc!=3)throw std::runtime_error("usage: convert INPUT_VR44 OUTPUT_VRI2");
    std::ifstream input(argv[1],std::ios::binary);if(!input)throw std::runtime_error("input open");
    std::vector<uint8_t> bytes{std::istreambuf_iterator<char>(input),{}};
    auto frame=vr444::unpack(bytes);vr444::validate(frame);
    auto wire=intra_v2::encode(frame,nullptr,false);
    if(vr444::decode(frame)!=vr444::decode(intra_v2::decode(wire)))throw std::runtime_error("RGB mismatch");
    std::ofstream output(argv[2],std::ios::binary);output.write(reinterpret_cast<const char*>(wire.data()),wire.size());
    if(!output)throw std::runtime_error("output write");
    std::cout<<bytes.size()<<" -> "<<wire.size()<<" bytes; DC-only exact RGB roundtrip\n";
}
