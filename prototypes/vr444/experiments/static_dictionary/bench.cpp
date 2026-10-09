// SPDX-License-Identifier: MIT
#include "dictionary.hpp"
#include "codec.hpp"
#include "direct.hpp"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <random>
namespace fs=std::filesystem;
using Clock=std::chrono::steady_clock;
using dictionary::Bytes;using dictionary::View;
static void need(bool v){if(!v)throw std::runtime_error("dictionary benchmark check failed");}
static double ms(Clock::time_point a,Clock::time_point b){return std::chrono::duration<double,std::milli>(b-a).count();}
static Bytes read(const fs::path&p,size_t limit){auto n=fs::file_size(p);need(n>0&&n<=limit);Bytes b(n);std::ifstream f(p,std::ios::binary);f.read(reinterpret_cast<char*>(b.data()),n);need(bool(f));return b;}
static std::string quote(const std::string&s){std::string r="\"";for(char c:s){need(c>=32);if(c=='"'||c=='\\')r+='\\';r+=c;}return r+'"';}
static void dist(const std::vector<double>&samples){auto v=samples;std::sort(v.begin(),v.end());std::cout<<"{\"p50\":"<<v[(v.size()-1)/2]<<",\"p95\":"<<v[(95*v.size()+99)/100-1]<<",\"max\":"<<v.back()<<",\"samples\":[";for(size_t i=0;i<samples.size();++i)std::cout<<(i?",":"")<<samples[i];std::cout<<"]}";}
struct Selected{View bytes;unsigned mode;}; //0 raw,1 existing WPC1,2 new dictionary DPC1
static Selected plain(View raw,wpc::Workspace&w){if(raw.size()>wpc::input_limit)return{raw,0};wpc::encode(raw,wpc::wire_limit,w);if(w.wire[5]==1&&w.wire.size()+64<=raw.size())return{w.wire,1};return{raw,0};}
int main(int argc,char**argv){try{
    need(argc==4);unsigned reps=std::stoul(argv[3]);need(reps>=5&&reps<=100);
    auto training=read(argv[2],dictionary::max_dictionary);auto a=Clock::now();dictionary::Dictionary fast(training);auto b=Clock::now();dictionary::Dictionary slow(training,true);auto c=Clock::now();
    double fast_init=ms(a,b),slow_init=ms(b,c);
    std::vector<fs::path> paths;for(auto&e:fs::recursive_directory_iterator(argv[1]))if(e.is_regular_file()&&e.path().extension()==".payload")paths.push_back(e.path());
    std::sort(paths.begin(),paths.end());need(!paths.empty()&&paths.size()<=512);std::shuffle(paths.begin(),paths.end(),std::mt19937(8133));
    std::cout<<std::setprecision(9)<<"{\"schema\":1,\"pass\":true,\"domain\":\"offline host only\",\"dictionary_sha256\":"<<quote(fast.hex())<<",\"dictionary_bytes\":"<<training.size()<<",\"dictionary_init_fast_ms\":"<<fast_init<<",\"dictionary_init_slow_ms\":"<<slow_init<<",\"repetitions\":"<<reps<<",\"warmups\":3,\"cases\":[";
    unsigned ci=0;size_t total=0;
    for(auto&p:paths){auto raw=read(p,dictionary::max_raw);total+=raw.size();need(total<=64*1024*1024);auto shape=direct::preflight(raw);auto oracle=vr444::decode(vr444::unpack(raw));std::vector<uint32_t> pixels(oracle.size());
        std::array<wpc::Workspace,3> base_tx,base_rx;std::array<dictionary::Workspace,2> dict_tx,dict_rx;
        std::array<std::vector<double>,3> enc,unwrap,client,combined;std::array<size_t,3> sizes{};std::array<unsigned,3> modes{};
        std::array<size_t,2> candidate_sizes{};std::array<bool,2> candidate_compressed{};
        // Validate the dictionary attempts even when the adaptive path declines
        // them. This is outside measured work and uses separate workspaces.
        for(unsigned i=0;i<2;++i){dictionary::Workspace tx,rx;const auto&dict=i?slow:fast;auto attempt=dictionary::encode(raw,dict,tx);
            auto bytes=attempt.compressed?dictionary::decode(attempt.bytes,dict,rx):attempt.bytes;
            need(std::equal(raw.begin(),raw.end(),bytes.begin(),bytes.end()));candidate_sizes[i]=attempt.bytes.size();candidate_compressed[i]=attempt.compressed;}
        for(unsigned rep=0;rep<reps+3;++rep)for(unsigned j=0;j<3;++j){unsigned which=(j+rep)%3;const dictionary::Dictionary&dict=which==2?slow:fast;
            auto start=Clock::now();auto selected=plain(raw,base_tx[which]);
            if(which){auto attempt=dictionary::encode(raw,dict,dict_tx[which-1]);if(attempt.compressed&&attempt.bytes.size()<selected.bytes.size())selected={attempt.bytes,2};}
            auto encoded=Clock::now();View input=selected.bytes;
            if(selected.mode==1){wpc::decode(input,base_rx[which]);input=base_rx[which].decoded;}
            if(selected.mode==2)input=dictionary::decode(input,dict,dict_rx[which-1]);
            auto unwrapped=Clock::now();auto current_shape=direct::preflight(input);direct::decode(input,current_shape,pixels,true);auto finished=Clock::now();
            need(std::equal(raw.begin(),raw.end(),input.begin(),input.end())&&pixels==oracle);
            if(rep==0){sizes[which]=selected.bytes.size();modes[which]=selected.mode;}else need(sizes[which]==selected.bytes.size()&&modes[which]==selected.mode);
            if(rep>=3){enc[which].push_back(ms(start,encoded));unwrap[which].push_back(ms(encoded,unwrapped));client[which].push_back(ms(encoded,finished));combined[which].push_back(ms(start,finished));}
        }
        std::cout<<(ci++?",":"")<<"{\"name\":"<<quote(fs::relative(p,argv[1]).generic_string())<<",\"width\":"<<shape.width<<",\"height\":"<<shape.height<<",\"q\":"<<shape.q<<",\"raw_bytes\":"<<raw.size()<<",\"exact\":true,\"profiles\":[";
        const char* names[]={"plain_lz4","dictionary_fast_adaptive","dictionary_slow_adaptive"};
        for(unsigned i=0;i<3;++i){std::cout<<(i?",":"")<<"{\"name\":"<<quote(names[i])<<",\"wire_bytes\":"<<sizes[i]<<",\"selected_mode\":"<<modes[i];
            if(i)std::cout<<",\"dictionary_candidate_bytes\":"<<candidate_sizes[i-1]<<",\"dictionary_candidate_compressed\":"<<(candidate_compressed[i-1]?"true":"false");
            std::cout<<",\"encode_ms\":";dist(enc[i]);std::cout<<",\"unwrap_ms\":";dist(unwrap[i]);std::cout<<",\"client_with_vr444_ms\":";dist(client[i]);std::cout<<",\"encode_plus_client_ms\":";dist(combined[i]);std::cout<<"}";}
        std::cout<<"]}"<<std::flush;
    }
    std::cout<<"]}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
