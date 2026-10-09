// SPDX-License-Identifier: MIT
#include "rowfilter.hpp"
#include "codec.hpp"
#include "direct.hpp"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <random>
using rowfilter::Bytes;using rowfilter::View;
namespace fs=std::filesystem;using Clock=std::chrono::steady_clock;
#if defined(RRF_COMPARE_SIMD)
constexpr unsigned profiles=7;constexpr bool compare_simd=true;
const char*names[]={"vr444_plain_lz4","scalar_rgb_plain_lz4","scalar_rgb_up_lz4","scalar_rgb_adaptive_lz4","simd_rgb_plain_lz4","simd_rgb_up_lz4","simd_rgb_adaptive_lz4"};
#else
constexpr unsigned profiles=4;constexpr bool compare_simd=false;
const char*names[]={"vr444_plain_lz4","rgb_plain_lz4","rgb_up_lz4","rgb_adaptive_lz4"};
#endif
static void need(bool b){if(!b)throw std::runtime_error("rowfilter benchmark exactness/bounds failure");}
static double ms(Clock::time_point a,Clock::time_point b){return std::chrono::duration<double,std::milli>(b-a).count();}
static Bytes read(const fs::path&p){auto n=fs::file_size(p);need(n>0&&n<=16*1024*1024);Bytes b(n);std::ifstream f(p,std::ios::binary);f.read(reinterpret_cast<char*>(b.data()),n);need(bool(f));return b;}
static std::string quote(const std::string&s){std::string r="\"";for(char c:s){need(c>=32);if(c=='\\'||c=='"')r+='\\';r+=c;}return r+'"';}
static void dist(const std::vector<double>&samples){auto v=samples;std::sort(v.begin(),v.end());std::cout<<"{\"p50\":"<<v[(v.size()-1)/2]<<",\"p95\":"<<v[(95*v.size()+99)/100-1]<<",\"max\":"<<v.back()<<",\"samples\":[";for(size_t i=0;i<samples.size();++i)std::cout<<(i?",":"")<<samples[i];std::cout<<"]}";}
static View plain(View raw,wpc::Workspace&w,bool&compressed){compressed=false;if(raw.size()>wpc::input_limit)return raw;wpc::encode(raw,wpc::wire_limit,w);if(w.wire[5]==1&&w.wire.size()+64<=raw.size()){compressed=true;return w.wire;}return raw;}
int main(int argc,char**argv){try{
    need(argc==3);unsigned reps=std::stoul(argv[2]);need(reps>=5&&reps<=100);std::vector<fs::path>paths;
    for(auto&e:fs::recursive_directory_iterator(argv[1]))if(e.is_regular_file()&&e.path().extension()==".payload")paths.push_back(e.path());
    std::sort(paths.begin(),paths.end());need(!paths.empty()&&paths.size()<=512);std::shuffle(paths.begin(),paths.end(),std::mt19937(4443));
    std::cout<<std::setprecision(9)<<"{\"schema\":1,\"pass\":true,\"domain\":\"offline host only\",\"simd_backend\":"<<quote(rowfilter::detail::backend())<<",\"scalar_simd_comparison\":"<<(compare_simd?"true":"false")<<",\"repetitions\":"<<reps<<",\"warmups\":3,\"cases\":[";unsigned ci=0;size_t total=0;
    for(auto&p:paths){auto raw=read(p);total+=raw.size();need(total<=64*1024*1024);auto shape=direct::preflight(raw);need(size_t(shape.width)*shape.height<=rowfilter::max_pixels);
        auto oracle=vr444::decode(vr444::unpack(raw));std::vector<uint32_t>pixels(oracle.size()),source(oracle.size());
        wpc::Workspace base_tx,base_rx;std::array<rowfilter::Workspace,profiles-1>tx,rx;
        std::array<std::vector<double>,profiles>server,source_decode,encode,client,combined;std::array<size_t,profiles>sizes{};std::array<unsigned,profiles>modes{};
        for(unsigned rep=0;rep<reps+3;++rep)for(unsigned j=0;j<profiles;++j){unsigned which=(j+rep)%profiles;bool compressed=false;View wire;bool simd=!compare_simd||which>=4;
            auto start=Clock::now(),reconstructed=start;
            if(which){direct::decode(raw,shape,source,true);reconstructed=Clock::now();wire=rowfilter::encode(source,shape.width,shape.height,rowfilter::Policy((which-1)%3+1),tx[which-1],simd);}
            else wire=plain(raw,base_tx,compressed);
            auto encoded=Clock::now();
            if(which)rowfilter::decode_into(wire,pixels,rx[which-1],simd);
            else{View input=wire;if(compressed){wpc::decode(wire,base_rx);input=base_rx.decoded;}auto current=direct::preflight(input);direct::decode(input,current,pixels,true);}
            auto finished=Clock::now();need(pixels==oracle);if(which)need(source==oracle);
            unsigned mode=which?wire[5]:unsigned(compressed);
            if(rep==0){sizes[which]=wire.size();modes[which]=mode;}else need(sizes[which]==wire.size()&&modes[which]==mode);
            if(rep>=3){server[which].push_back(ms(start,encoded));source_decode[which].push_back(ms(start,reconstructed));encode[which].push_back(ms(reconstructed,encoded));client[which].push_back(ms(encoded,finished));combined[which].push_back(ms(start,finished));}
        }
        std::cout<<(ci++?",":"")<<"{\"name\":"<<quote(fs::relative(p,argv[1]).generic_string())<<",\"width\":"<<shape.width<<",\"height\":"<<shape.height<<",\"q\":"<<shape.q<<",\"raw_vr444_bytes\":"<<raw.size()<<",\"exact_decoded_rgba\":true,\"profiles\":[";
        if(compare_simd)for(unsigned i=1;i<4;++i)need(sizes[i]==sizes[i+3]&&modes[i]==modes[i+3]&&tx[i-1].wire==tx[i+2].wire);
        for(unsigned i=0;i<profiles;++i){std::cout<<(i?",":"")<<"{\"name\":"<<quote(names[i])<<",\"wire_bytes\":"<<sizes[i]<<",\"selected_mode\":"<<modes[i]<<",\"server_transcode_ms\":";dist(server[i]);std::cout<<",\"source_reconstruction_ms\":";dist(source_decode[i]);std::cout<<",\"wrapper_encode_ms\":";dist(encode[i]);std::cout<<",\"client_decode_ms\":";dist(client[i]);std::cout<<",\"server_plus_client_ms\":";dist(combined[i]);std::cout<<"}";}
        std::cout<<"]}"<<std::flush;
    }
    std::cout<<"]}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
