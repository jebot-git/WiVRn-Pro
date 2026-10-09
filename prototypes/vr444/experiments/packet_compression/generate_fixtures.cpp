// SPDX-License-Identifier: MIT
#include "codec.hpp"
#include <filesystem>
#include <fstream>
int main(int argc,char**argv) {
    if(argc!=2)return 2;
    std::filesystem::create_directories(argv[1]);
    for(unsigned kind=0;kind<3;kind++)for(unsigned q:{1u,8u}) {
        auto pixels=vr444::scene(576,640,kind);
        auto bytes=vr444::pack(vr444::encode(pixels,576,640,q));
        auto path=std::filesystem::path(argv[1])/(std::to_string(kind)+"-q"+std::to_string(q)+".payload");
        std::ofstream out(path,std::ios::binary);
        out.write(reinterpret_cast<const char*>(bytes.data()),bytes.size());
        if(!out)return 3;
    }
}
