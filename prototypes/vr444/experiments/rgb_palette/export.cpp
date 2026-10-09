// SPDX-License-Identifier: MIT
#include "rgb_palette.hpp"
#include "codec.hpp"
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <span>
#include <stdexcept>
namespace fs=std::filesystem;
using Bytes=std::vector<uint8_t>;
static void need(bool v){if(!v)throw std::runtime_error("export check failed");}
static Bytes read(const fs::path& p){auto n=fs::file_size(p);need(n>0&&n<=16*1024*1024);Bytes b(n);std::ifstream f(p,std::ios::binary);f.read(reinterpret_cast<char*>(b.data()),n);need(bool(f));return b;}
static void write(const fs::path&p,std::span<const uint8_t>b){fs::create_directories(p.parent_path());std::ofstream f(p,std::ios::binary);f.write(reinterpret_cast<const char*>(b.data()),b.size());need(bool(f));}
static Bytes rgba_bytes(std::span<const uint32_t>p){Bytes b;b.reserve(4*p.size());for(auto c:p)for(unsigned i=0;i<4;++i)b.push_back(uint8_t(c>>(8*i)));return b;}
static std::string quote(const std::string&s){std::string r="\"";for(char c:s){need(c>=32);if(c=='"'||c=='\\')r+='\\';r+=c;}return r+'"';}
int main(int argc,char**argv){try{
    if(argc==4&&std::string(argv[1])=="verify"){
        auto wire=read(argv[2]);auto expected=read(argv[3]);auto out=rgb_palette::decode(wire);
        need(rgba_bytes(out.rgba)==expected);auto layout=rgb_palette::inspect(wire);
        need(layout.width==out.width&&layout.height==out.height);std::cout<<"{\"exact\":true}\n";return 0;
    }
    need(argc==3);fs::path corpus=fs::canonical(argv[1]),output=fs::absolute(argv[2]);
    std::vector<fs::path> paths;for(auto&e:fs::recursive_directory_iterator(corpus))if(e.is_regular_file()&&e.path().extension()==".payload")paths.push_back(e.path());
    std::sort(paths.begin(),paths.end());need(!paths.empty()&&paths.size()<=512);size_t total=0;unsigned count=0;
    std::cout<<"{\"schema\":1,\"cases\":[";
    for(const auto&p:paths){auto input=read(p);total+=input.size();need(total<=64*1024*1024);
        auto f=vr444::unpack(input);auto pixels=vr444::decode(f);auto wire=rgb_palette::encode(pixels,f.width,f.height);
        auto decoded=rgb_palette::decode(wire);need(decoded.rgba==pixels);auto layout=rgb_palette::inspect(wire);
        fs::path name=fs::relative(p,corpus);name.replace_extension();auto rpt=name;rpt+=".rpt";auto rgba=name;rgba+=".rgba";
        write(output/rpt,wire);auto bytes=rgba_bytes(pixels);write(output/rgba,bytes);
        std::cout<<(count++?",":"")<<"{\"name\":"<<quote(name.generic_string())<<",\"rpt\":"<<quote(rpt.generic_string())<<",\"rgba\":"<<quote(rgba.generic_string())<<",\"width\":"<<f.width<<",\"height\":"<<f.height<<",\"q\":"<<f.q<<",\"wire_bytes\":"<<wire.size()<<",\"tiles\":"<<layout.tiles.size()<<",\"exact\":true}";
    }
    std::cout<<"]}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
