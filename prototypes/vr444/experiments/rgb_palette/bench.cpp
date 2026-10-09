// SPDX-License-Identifier: MIT
#include "rgb_palette.hpp"
#include "codec.hpp"
#include "direct.hpp"
#include "wpc.hpp"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <random>
using Clock=std::chrono::steady_clock;
using Bytes=std::vector<uint8_t>;
static void need(bool v){if(!v)throw std::runtime_error("benchmark check failed");}
static double ms(Clock::time_point a,Clock::time_point b){return std::chrono::duration<double,std::milli>(b-a).count();}
static Bytes read(const std::filesystem::path& p){auto n=std::filesystem::file_size(p);need(n>0&&n<=wpc::wire_limit);Bytes b(n);std::ifstream f(p,std::ios::binary);f.read(reinterpret_cast<char*>(b.data()),n);need(bool(f));return b;}
static std::string quote(const std::string& s){std::string r="\"";for(char c:s){need(c>=32);if(c=='\\'||c=='"')r+='\\';r+=c;}return r+'"';}
static void dist(const std::vector<double>& samples){auto v=samples;std::sort(v.begin(),v.end());std::cout<<"{\"p50\":"<<v[(v.size()-1)/2]<<",\"p95\":"<<v[(95*v.size()+99)/100-1]<<",\"max\":"<<v.back()<<",\"samples\":[";for(size_t i=0;i<samples.size();++i)std::cout<<(i?",":"")<<samples[i];std::cout<<"]}";}
struct Selected {std::span<const uint8_t> bytes;bool compressed;};
static Selected select(std::span<const uint8_t> b,wpc::Workspace& w){wpc::encode(b,wpc::wire_limit,w);if(w.wire[5]==1 && w.wire.size()+64<=b.size())return {w.wire,true};return {b,false};}
int main(int argc,char**argv){try{
    need(argc==3);unsigned reps=std::stoul(argv[2]);need(reps>=5&&reps<=200);
    std::vector<std::filesystem::path> paths;
    for(auto&e:std::filesystem::recursive_directory_iterator(argv[1]))if(e.is_regular_file()&&e.path().extension()==".payload")paths.push_back(e.path());
    std::sort(paths.begin(),paths.end());need(!paths.empty()&&paths.size()<=512);
    std::mt19937 rng(1526);std::shuffle(paths.begin(),paths.end(),rng);
    std::cout<<std::setprecision(9)<<"{\"schema\":1,\"pass\":true,\"domain\":\"offline host only\",\"backend\":"<<quote(direct::vector_backend())<<",\"arm_crc\":"<<(wpc::arm_crc_enabled()?"true":"false")<<",\"repetitions\":"<<reps<<",\"warmups\":3,\"cases\":[";
    size_t ci=0,total=0;
    for(auto& p:paths){
        auto wire=read(p);total+=wire.size();need(total<=64*1024*1024);
        auto shape=direct::preflight(wire);auto oracle=vr444::decode(vr444::unpack(wire));
        std::vector<uint32_t> out(oracle.size()),host_rgb(oracle.size());
        rgb_palette::Stats stats;auto rpt=rgb_palette::encode(oracle,shape.width,shape.height,&stats);
        need(rgb_palette::decode(rpt).rgba==oracle);wpc::Workspace old_w,rpt_w;
        auto old=select(wire,old_w);auto wrapped=select(rpt,rpt_w);
        size_t old_bytes=old.bytes.size(),rpt_lz_bytes=wrapped.bytes.size();bool old_c=old.compressed,rpt_c=wrapped.compressed;
        std::array<std::vector<double>,3> encode_times,decode_times,total_times;
        std::vector<double> rgb_times;
        for(unsigned rep=0;rep<reps+3;++rep){
            for(unsigned j=0;j<3;++j){unsigned which=(j+rep)%3;std::fill(out.begin(),out.end(),0x12345678u);
                auto a=Clock::now();double rgb_ms=0;Selected selected;
                if(which==0) selected=select(wire,old_w);
                else {
                    auto b=Clock::now();auto host_shape=direct::preflight(wire);direct::decode(wire,host_shape,host_rgb,true);auto c=Clock::now();rgb_ms=ms(b,c);
                    rpt=rgb_palette::encode(host_rgb,shape.width,shape.height);
                    selected=which==1?Selected{rpt,false}:select(rpt,rpt_w);
                }
                auto b=Clock::now();auto input=selected.bytes;
                if(selected.compressed){auto& ws=which==0?old_w:rpt_w;wpc::decode(input,ws);input=ws.decoded;}
                if(which==0){auto s=direct::preflight(input);direct::decode(input,s,out,true);}
                else rgb_palette::decode_into(input,out);
                auto c=Clock::now();need(out==oracle);
                if(rep>=3){encode_times[which].push_back(ms(a,b));decode_times[which].push_back(ms(b,c));total_times[which].push_back(ms(a,c));if(which==1)rgb_times.push_back(rgb_ms);}
            }
        }
        std::cout<<(ci++?",":"")<<"{\"name\":"<<quote(std::filesystem::relative(p,argv[1]).string())<<",\"width\":"<<shape.width<<",\"height\":"<<shape.height<<",\"q\":"<<shape.q<<",\"input_vr444_bytes\":"<<wire.size()<<",\"decoded_rgba_bytes\":"<<oracle.size()*4<<",\"exact\":true,\"tiles\":{\"raw\":"<<stats.raw<<",\"constant\":"<<stats.constant<<",\"palette2\":"<<stats.palette2<<",\"palette4\":"<<stats.palette4<<",\"palette16\":"<<stats.palette16<<",\"runs\":"<<stats.runs<<"},\"host_vr444_decode_ms\":";dist(rgb_times);
        std::cout<<",\"profiles\":[";
        const char* names[]={"vr444_lz4","rpt1","rpt1_lz4"};size_t sizes[]={old_bytes,rpt.size(),rpt_lz_bytes};bool compressed[]={old_c,false,rpt_c};
        for(unsigned i=0;i<3;++i){std::cout<<(i?",":"")<<"{\"name\":"<<quote(names[i])<<",\"wire_bytes\":"<<sizes[i]<<",\"lz4_selected\":"<<(compressed[i]?"true":"false")<<",\"post_vr444_server_ms\":";dist(encode_times[i]);std::cout<<",\"client_decode_ms\":";dist(decode_times[i]);std::cout<<",\"post_vr444_combined_ms\":";dist(total_times[i]);std::cout<<"}";}
        std::cout<<"]}"<<std::flush;
    }
    std::cout<<"]}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
