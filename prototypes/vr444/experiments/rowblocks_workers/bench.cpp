// SPDX-License-Identifier: MIT
#include "workers.hpp"
#include "codec.hpp"
#include "direct.hpp"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <random>
#include <time.h>
using rowworkers::Bytes;using rowworkers::View;
namespace fs=std::filesystem;using Clock=std::chrono::steady_clock;
constexpr unsigned profiles=8;
const char*names[]={"vr444_serial","rrf_up_serial","rrb32_serial","rrb32_workers2","rrb32_workers4","rrb128_serial","rrb128_workers2","rrb128_workers4"};
static void need(bool b){if(!b)throw std::runtime_error("stereo worker benchmark exactness/bounds failure");}
static double ms(Clock::time_point a,Clock::time_point b){return std::chrono::duration<double,std::milli>(b-a).count();}
static uint64_t cpu(){timespec t{};need(clock_gettime(CLOCK_PROCESS_CPUTIME_ID,&t)==0);return uint64_t(t.tv_sec)*1000000000+uint64_t(t.tv_nsec);}
static Bytes read(const fs::path&p){auto n=fs::file_size(p);need(n>0&&n<=16*1024*1024);Bytes b(n);std::ifstream f(p,std::ios::binary);f.read(reinterpret_cast<char*>(b.data()),n);need(bool(f));return b;}
static std::string quote(const std::string&s){std::string r="\"";for(char c:s){need(c>=32);if(c=='\\'||c=='"')r+='\\';r+=c;}return r+'"';}
static void dist(const std::vector<double>&samples){auto v=samples;std::sort(v.begin(),v.end());std::cout<<"{\"p50\":"<<v[(v.size()-1)/2]<<",\"p95\":"<<v[(95*v.size()+99)/100-1]<<",\"max\":"<<v.back()<<",\"samples\":[";for(size_t i=0;i<samples.size();++i)std::cout<<(i?",":"")<<samples[i];std::cout<<"]}";}
static View plain(View raw,wpc::Workspace&w,bool&compressed){compressed=false;if(raw.size()>wpc::input_limit)return raw;wpc::encode(raw,wpc::wire_limit,w);if(w.wire[5]==1&&w.wire.size()+64<=raw.size()){compressed=true;return w.wire;}return raw;}
struct Input {std::string name;std::array<fs::path,2>path;bool synthetic=false;};
int main(int argc,char**argv){try{
    need(argc==3);unsigned reps=std::stoul(argv[2]);need(reps>=5&&reps<=100);std::map<std::string,Input>grouped;
    for(auto&e:fs::recursive_directory_iterator(argv[1]))if(e.is_regular_file()&&e.path().extension()==".payload"){
        auto relative=fs::relative(e.path(),argv[1]);auto key=relative.generic_string();bool synthetic=key.starts_with("synthetic/");
        if(synthetic){need(!grouped.contains(key));grouped[key]={key,{e.path(),e.path()},true};}
        else{auto stem=relative.stem().string();need(stem.size()>2&&stem[stem.size()-2]=='-'&&(stem.back()=='0'||stem.back()=='1'));
            unsigned eye=unsigned(stem.back()-'0');key=(relative.parent_path()/stem.substr(0,stem.size()-2)).generic_string();auto&item=grouped[key];
            need(item.path[eye].empty());item.name=key;item.path[eye]=e.path();}
    }
    std::vector<Input>inputs;for(auto&[_,p]:grouped){need(!p.path[0].empty()&&!p.path[1].empty());inputs.push_back(p);}
    need(!inputs.empty()&&inputs.size()<=128);std::shuffle(inputs.begin(),inputs.end(),std::mt19937(4443));
    unsigned dummy=0;auto noop=[](void*,size_t,unsigned){};auto start2=Clock::now();rowworkers::Pool pool2(2);pool2.run(1,&dummy,noop);auto end2=Clock::now();
    auto start4=Clock::now();rowworkers::Pool pool4(4);pool4.run(1,&dummy,noop);auto end4=Clock::now();
    std::cout<<std::setprecision(9)<<"{\"schema\":1,\"pass\":true,\"domain\":\"offline host stereo CPU\",\"backend\":"<<quote(rowfilter::detail::backend())<<",\"repetitions\":"<<reps<<",\"warmups\":3,\"pool2_startup_ms\":"<<ms(start2,end2)<<",\"pool4_startup_ms\":"<<ms(start4,end4)<<",\"cases\":[";unsigned ci=0;size_t total=0;
    for(auto&input:inputs){std::array<Bytes,2>raw{read(input.path[0]),read(input.path[1])};total+=raw[0].size()+raw[1].size();need(total<=128*1024*1024);
        std::array shapes{direct::preflight(raw[0]),direct::preflight(raw[1])};need(shapes[0].width==shapes[1].width&&shapes[0].height==shapes[1].height&&shapes[0].q==shapes[1].q);
        auto width=shapes[0].width,height=shapes[0].height;std::array<std::vector<uint32_t>,2>oracle,source,decoded;
        for(unsigned e=0;e<2;++e){oracle[e]=vr444::decode(vr444::unpack(raw[e]));source[e].resize(oracle[e].size());decoded[e].resize(oracle[e].size());}
        std::array<wpc::Workspace,2>base_tx,base_rx;std::array<rowfilter::Workspace,2>global_tx,global_rx;
        std::array<std::array<rowblocks::Workspace,2>,6>tx;std::array<rowworkers::Workspace,6>rx;
        std::array<std::vector<double>,profiles>server,client,client_cpu,combined;std::array<size_t,profiles>sizes{};
        for(unsigned rep=0;rep<reps+3;++rep)for(unsigned j=0;j<profiles;++j){unsigned which=(j+rep)%profiles;std::array<bool,2>compressed{};std::array<View,2>wire;
            auto begin=Clock::now();
            for(unsigned e=0;e<2;++e){
                if(which){direct::decode(raw[e],shapes[e],source[e],true);
                    if(which==1)wire[e]=rowfilter::encode(source[e],width,height,rowfilter::Policy::up,global_tx[e]);
                    else wire[e]=rowblocks::encode(source[e],width,height,which<5?32:128,rowfilter::Policy::up,tx[which-2][e]);
                }else wire[e]=plain(raw[e],base_tx[e],compressed[e]);
            }
            auto encoded=Clock::now();auto cpu_begin=cpu();std::array<std::span<const uint32_t>,2>out;
            if(which>1){auto mod=(which-2)%3;auto pair=rowworkers::decode_pair(wire[0],wire[1],rx[which-2],mod==0?nullptr:mod==1?&pool2:&pool4);out={pair.left,pair.right};}
            else for(unsigned e=0;e<2;++e){
                if(which)rowfilter::decode_into(wire[e],decoded[e],global_rx[e]);
                else{View b=wire[e];if(compressed[e]){wpc::decode(b,base_rx[e]);b=base_rx[e].decoded;}auto shape=direct::preflight(b);direct::decode(b,shape,decoded[e],true);}
                out[e]=decoded[e];
            }
            auto cpu_end=cpu();auto finished=Clock::now();
            for(unsigned e=0;e<2;++e){need(out[e].size()==oracle[e].size()&&std::equal(oracle[e].begin(),oracle[e].end(),out[e].begin()));if(which)need(source[e]==oracle[e]);}
            size_t size=wire[0].size()+wire[1].size();if(rep==0)sizes[which]=size;else need(sizes[which]==size);
            if(rep>=3){server[which].push_back(ms(begin,encoded));client[which].push_back(ms(encoded,finished));client_cpu[which].push_back(double(cpu_end-cpu_begin)/1e6);combined[which].push_back(ms(begin,finished));}
        }
        for(unsigned e=0;e<2;++e)for(unsigned group:{0,3})need(tx[group][e].wire==tx[group+1][e].wire&&tx[group][e].wire==tx[group+2][e].wire);
        std::cout<<(ci++?",":"")<<"{\"name\":"<<quote(input.name)<<",\"left_source\":"<<quote(fs::relative(input.path[0],argv[1]).generic_string())<<",\"right_source\":"<<quote(fs::relative(input.path[1],argv[1]).generic_string())<<",\"synthetic_duplicate_eye\":"<<(input.synthetic?"true":"false")<<",\"width\":"<<width<<",\"height\":"<<height<<",\"q\":"<<shapes[0].q<<",\"exact_stereo\":true,\"serial_worker_wire_equal\":true,\"profiles\":[";
        for(unsigned i=0;i<profiles;++i){std::cout<<(i?",":"")<<"{\"name\":"<<quote(names[i])<<",\"stereo_wire_bytes\":"<<sizes[i]<<",\"server_transcode_ms\":";dist(server[i]);std::cout<<",\"client_decode_wall_ms\":";dist(client[i]);std::cout<<",\"client_process_cpu_ms\":";dist(client_cpu[i]);std::cout<<",\"server_plus_client_wall_ms\":";dist(combined[i]);std::cout<<"}";}
        std::cout<<"]}"<<std::flush;
    }std::cout<<"]}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
