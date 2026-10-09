// SPDX-License-Identifier: MIT
#include "codec.hpp"
#include "intra_v2.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
static std::vector<uint32_t> scene(unsigned kind,unsigned w,unsigned h){
    if(kind<3)return vr444::scene(w,h,kind);std::vector<uint32_t> pixels(size_t(w)*h);
    for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){unsigned color;
        if(kind==3){unsigned gx=x%11,gy=y%17;bool text=y%43<17&&(gx==1||gy==1||gy==8||(gx==8&&gy<9));color=text?0x00f4f4f4:0x001b1d21;}
        else if(kind==4){bool edge=((x+2*y)/31)%2;color=edge?0x0027d4f1:0x00cb231a;if(x%73==0||y%61==0)color=0x0000ff00;}
        else color=0x005b7a99;pixels[size_t(y)*w+x]=color|0xff000000;
    }
    return pixels;
}
static void write(const std::filesystem::path& path,const std::vector<uint8_t>& bytes){std::ofstream file(path,std::ios::binary);file.write(reinterpret_cast<const char*>(bytes.data()),bytes.size());if(!file)throw std::runtime_error("write corpus");}
static void emit(const std::filesystem::path& path,const vr444::Frame& frame){write(path.string()+".payload",vr444::pack(frame));write(path.string()+".vri2",intra_v2::encode(frame,nullptr,false));}
int main(int argc,char** argv){if(argc!=2)throw std::runtime_error("usage: make_corpus OUT");std::filesystem::path out=argv[1];std::filesystem::create_directories(out/"synthetic");const char* names[]={"gradient","chroma-lines","seeded-noise","text-like","edges","constant"};
    for(unsigned kind=0;kind<6;++kind)for(unsigned q:{1u,8u})emit(out/"synthetic"/(std::string(names[kind])+"-q"+std::to_string(q)),vr444::encode(scene(kind,576,640),576,640,q));
    emit(out/"synthetic/partial-17x19-qmap",vr444::encode(scene(1,17,19),17,19,8,{1,8,17,1024}));
    emit(out/"synthetic/partial-1x1-constant",vr444::encode(scene(5,1,1),1,1,1));
    std::cout<<"14 synthetic fixtures generated\n";
}
