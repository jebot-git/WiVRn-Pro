// SPDX-License-Identifier: MIT
#include "rowblocks.hpp"
#include "codec.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
namespace fs=std::filesystem;using rowblocks::Bytes;
static void need(bool b){if(!b)throw std::runtime_error("rowblocks export bounds/I/O");}
static Bytes read(const fs::path&p){auto n=fs::file_size(p);need(n>0&&n<=16*1024*1024);Bytes b(n);std::ifstream f(p,std::ios::binary);f.read(reinterpret_cast<char*>(b.data()),n);need(bool(f));return b;}
static void write(const fs::path&p,rowblocks::View bytes){fs::create_directories(p.parent_path());std::ofstream f(p,std::ios::binary);f.write(reinterpret_cast<const char*>(bytes.data()),bytes.size());need(bool(f));}
static std::string quote(const std::string&s){std::string r="\"";for(char c:s){need(c>=32);if(c=='\\'||c=='"')r+='\\';r+=c;}return r+'"';}
int main(int argc,char**argv){try{
    need(argc==3);fs::path root=argv[1],out=argv[2];need(!fs::exists(out));std::vector<fs::path>paths;
    for(auto&e:fs::recursive_directory_iterator(root))if(e.is_regular_file()&&e.path().extension()==".payload")paths.push_back(e.path());
    std::sort(paths.begin(),paths.end());need(!paths.empty()&&paths.size()<=512);fs::create_directories(out);size_t total=0;unsigned ci=0;
    std::cout<<"{\"schema\":1,\"policy\":\"up with exact raw fallback\",\"cases\":[";
    for(auto&p:paths){auto raw=read(p);total+=raw.size();need(total<=64*1024*1024);auto frame=vr444::unpack(raw);auto rgba=vr444::decode(frame);rowblocks::Workspace tx,rx;
        auto name=fs::relative(p,root),pixels=name;pixels.replace_extension(".rgba");Bytes bytes(rgba.size()*4);
        for(size_t i=0;i<rgba.size();++i)for(unsigned c=0;c<4;++c)bytes[i*4+c]=uint8_t(rgba[i]>>(8*c));write(out/pixels,bytes);
        for(uint32_t rows:{16,32,64,128}){auto wire=rowblocks::encode(rgba,frame.width,frame.height,rows,rowfilter::Policy::up,tx);
            std::vector<uint32_t>checked(rgba.size());auto index=rowblocks::decode_into(wire,checked,rx);need(checked==rgba);
            auto packet=name;packet.replace_extension(".r"+std::to_string(rows)+".rrb");write(out/packet,wire);
            std::cout<<(ci++?",":"")<<"{\"source\":"<<quote(name.generic_string())<<",\"rrb\":"<<quote(packet.generic_string())<<",\"rgba\":"<<quote(pixels.generic_string())<<",\"width\":"<<frame.width<<",\"height\":"<<frame.height<<",\"q\":"<<frame.q<<",\"block_rows\":"<<rows<<",\"blocks\":"<<index.blocks.size()<<",\"wire_bytes\":"<<wire.size()<<",\"exact\":true}";
        }
    }std::cout<<"]}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
