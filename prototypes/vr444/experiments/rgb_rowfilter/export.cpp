// SPDX-License-Identifier: MIT
#include "rowfilter.hpp"
#include "codec.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
namespace fs=std::filesystem;
using rowfilter::Bytes;
static void need(bool b){if(!b)throw std::runtime_error("rowfilter export bounds/I/O");}
static Bytes read(const fs::path&p){auto n=fs::file_size(p);need(n>0&&n<=16*1024*1024);Bytes b(n);std::ifstream f(p,std::ios::binary);f.read(reinterpret_cast<char*>(b.data()),n);need(bool(f));return b;}
static void write(const fs::path&p,rowfilter::View bytes){fs::create_directories(p.parent_path());std::ofstream f(p,std::ios::binary);f.write(reinterpret_cast<const char*>(bytes.data()),bytes.size());need(bool(f));}
static std::string quote(const std::string&s){std::string r="\"";for(char c:s){need(c>=32);if(c=='\\'||c=='"')r+='\\';r+=c;}return r+'"';}
int main(int argc,char**argv){try{
    need(argc==3);fs::path root=argv[1],out=argv[2];need(!fs::exists(out));std::vector<fs::path>paths;
    for(auto&e:fs::recursive_directory_iterator(root))if(e.is_regular_file()&&e.path().extension()==".payload")paths.push_back(e.path());
    std::sort(paths.begin(),paths.end());need(!paths.empty()&&paths.size()<=512);fs::create_directories(out);size_t total=0;unsigned row=0;
    std::cout<<"{\"schema\":1,\"policy\":\"up with exact raw fallback\",\"cases\":[";
    for(auto&p:paths){auto raw=read(p);total+=raw.size();need(total<=64*1024*1024);auto frame=vr444::unpack(raw);auto rgba=vr444::decode(frame);rowfilter::Workspace tx,rx;
        auto wire=rowfilter::encode(rgba,frame.width,frame.height,rowfilter::Policy::up,tx);std::vector<uint32_t>checked(rgba.size());rowfilter::decode_into(wire,checked,rx);need(checked==rgba);
        auto name=fs::relative(p,root),packet=name,pixels=name;packet.replace_extension(".rrf");pixels.replace_extension(".rgba");write(out/packet,wire);
        Bytes bytes(rgba.size()*4);for(size_t i=0;i<rgba.size();++i)for(unsigned c=0;c<4;++c)bytes[i*4+c]=uint8_t(rgba[i]>>(8*c));write(out/pixels,bytes);
        std::cout<<(row++?",":"")<<"{\"source\":"<<quote(name.generic_string())<<",\"rrf\":"<<quote(packet.generic_string())<<",\"rgba\":"<<quote(pixels.generic_string())<<",\"width\":"<<frame.width<<",\"height\":"<<frame.height<<",\"q\":"<<frame.q<<",\"mode\":"<<unsigned(wire[5])<<",\"wire_bytes\":"<<wire.size()<<",\"exact\":true}";
    }
    std::cout<<"]}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
