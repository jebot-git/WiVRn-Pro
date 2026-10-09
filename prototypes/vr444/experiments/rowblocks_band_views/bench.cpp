// SPDX-License-Identifier: MIT
#include "encoder.hpp"
#include "codec.hpp"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <random>
#include <time.h>
using rowencoder::Bytes;using rowencoder::View;
namespace fs=std::filesystem;using Clock=std::chrono::steady_clock;
constexpr unsigned profiles=12;
const char*names[]={"vr444_serial","rrf_up_serial","rrb32_rgb_vector_copy_workers4","rrb32_fused_rgba_workers4","rrb32_band_view_serial","rrb32_band_view_workers2","rrb32_band_view_workers4","rrb128_rgb_vector_copy_workers4","rrb128_fused_rgba_workers4","rrb128_band_view_serial","rrb128_band_view_workers2","rrb128_band_view_workers4"};
static void need(bool b){if(!b)throw std::runtime_error("encoder benchmark bounds/exactness");}
static double ms(Clock::time_point a,Clock::time_point b){return std::chrono::duration<double,std::milli>(b-a).count();}
static uint64_t cpu(){timespec t{};need(clock_gettime(CLOCK_PROCESS_CPUTIME_ID,&t)==0);return uint64_t(t.tv_sec)*1000000000+uint64_t(t.tv_nsec);}
static Bytes read(const fs::path&p){size_t n=fs::file_size(p);need(n>0&&n<=16*1024*1024);Bytes b(n);std::ifstream f(p,std::ios::binary);f.read(reinterpret_cast<char*>(b.data()),std::streamsize(n));need(bool(f));return b;}
static std::string quote(const std::string&s){std::string r="\"";for(char c:s){need(c>=32);if(c=='\\'||c=='\"')r+='\\';r+=c;}return r+'\"';}
static void dist(const std::vector<double>&samples){auto v=samples;std::sort(v.begin(),v.end());std::cout<<"{\"p50\":"<<v[(v.size()-1)/2]<<",\"p95\":"<<v[(95*v.size()+99)/100-1]<<",\"max\":"<<v.back()<<",\"samples\":[";for(size_t i=0;i<samples.size();++i)std::cout<<(i?",":"")<<samples[i];std::cout<<"]}";}
static View plain(View raw,wpc::Workspace&w,bool&compressed){compressed=false;if(raw.size()>wpc::input_limit)return raw;wpc::encode(raw,wpc::wire_limit,w);if(w.wire[5]==1&&w.wire.size()+64<=raw.size()){compressed=true;return w.wire;}return raw;}
static bool same(View a,View b){return a.size()==b.size()&&std::equal(a.begin(),a.end(),b.begin());}
struct Input{std::string name;std::array<fs::path,2>path;bool synthetic=false;};
int main(int argc,char**argv){try{
    need(argc==3);unsigned reps=std::stoul(argv[2]);need(reps>=5&&reps<=100);std::map<std::string,Input>grouped;
    for(auto&e:fs::recursive_directory_iterator(argv[1]))if(e.is_regular_file()&&e.path().extension()==".payload"){
        auto relative=fs::relative(e.path(),argv[1]);auto key=relative.generic_string();bool synthetic=key.starts_with("synthetic/");
        if(synthetic){need(!grouped.contains(key));grouped[key]={key,{e.path(),e.path()},true};}
        else{auto stem=relative.stem().string();need(stem.size()>2&&stem[stem.size()-2]=='-'&&(stem.back()=='0'||stem.back()=='1'));unsigned eye=unsigned(stem.back()-'0');key=(relative.parent_path()/stem.substr(0,stem.size()-2)).generic_string();auto&item=grouped[key];need(item.path[eye].empty());item.name=key;item.path[eye]=e.path();}
    }
    std::vector<Input>inputs;for(auto&[_,p]:grouped){need(!p.path[0].empty()&&!p.path[1].empty());inputs.push_back(p);}need(!inputs.empty()&&inputs.size()<=128);std::shuffle(inputs.begin(),inputs.end(),std::mt19937(20261009));
    rowworkers::Pool server2(2),server4(4),client4(4);unsigned dummy=0;auto noop=[](void*,size_t,unsigned){};for(auto*p:{&server2,&server4,&client4})p->run(1,&dummy,noop);
    std::cout<<std::setprecision(9)<<"{\"schema\":1,\"pass\":true,\"domain\":\"offline paired host transcode and decode\",\"repetitions\":"<<reps<<",\"warmups\":3,\"all_rrb_client_worker_count\":4,\"backend\":"<<quote(rowfilter::detail::backend())<<",\"rgb_decoder_backend\":"<<quote(bandrgb::vector_backend())<<",\"cases\":[";unsigned ci=0;size_t total=0;
    for(auto&input:inputs){std::array<Bytes,2>raw{read(input.path[0]),read(input.path[1])};total+=raw[0].size()+raw[1].size();need(total<=128*1024*1024);
        std::array shapes{direct::preflight(raw[0]),direct::preflight(raw[1])};need(shapes[0].width==shapes[1].width&&shapes[0].height==shapes[1].height&&shapes[0].q==shapes[1].q);auto width=shapes[0].width,height=shapes[0].height;
        std::array<std::vector<uint32_t>,2>oracle,source,decoded;std::array<std::array<Bytes,2>,3>canonical;
        for(unsigned e=0;e<2;++e){oracle[e]=vr444::decode(vr444::unpack(raw[e]));source[e].resize(oracle[e].size());decoded[e].resize(oracle[e].size());
            rowfilter::Workspace global;auto g=rowfilter::encode(oracle[e],width,height,rowfilter::Policy::up,global);canonical[0][e]={g.begin(),g.end()};
            rowblocks::Workspace block;for(unsigned i=0;i<2;++i){auto b=rowblocks::encode(oracle[e],width,height,i?128:32,rowfilter::Policy::up,block);canonical[i+1][e]={b.begin(),b.end()};}
        }
        std::array<wpc::Workspace,2>base_tx,base_rx;std::array<rowfilter::Workspace,2>global_tx,global_rx;
        std::array<rowrgbvec::Workspace,2>scalar_stores;std::array<rowfused::Workspace,2>staged;std::array<rowbands::Workspace,6>fused;std::array<rowworkers::Workspace,10>client;
        std::array<std::vector<double>,profiles>server,server_cpu,decode_wall,decode_cpu,combined;std::array<size_t,profiles>sizes{},capacities{};
        for(unsigned rep=0;rep<reps+3;++rep)for(unsigned j=0;j<profiles;++j){unsigned which=(j+rep)%profiles;std::array<bool,2>compressed{};std::array<View,2>wire;
            auto begin=Clock::now();auto sb=cpu();
            if(which>=2&&(which-2)%5>=2){unsigned index=which<7?which-4:which-6;unsigned kind=(which-2)%5;auto out=rowbands::transcode_pair(raw[0],raw[1],which<7?32:128,rowfilter::Policy::up,fused[index],kind==2?nullptr:kind==3?&server2:&server4);wire={out.left,out.right};}
            else if(which>=2&&(which-2)%5==0){auto out=rowrgbvec::transcode_pair(raw[0],raw[1],which<7?32:128,rowfilter::Policy::up,scalar_stores[which<7?0:1],&server4);wire={out.left,out.right};}
            else if(which>=2&&(which-2)%5==1){auto out=rowfused::transcode_pair(raw[0],raw[1],which<7?32:128,rowfilter::Policy::up,staged[which<7?0:1],&server4);wire={out.left,out.right};}
            else for(unsigned eye=0;eye<2;++eye){
                if(which){auto shape=direct::preflight(raw[eye]);direct::decode(raw[eye],shape,source[eye],true);
                    if(which==1)wire[eye]=rowfilter::encode(source[eye],width,height,rowfilter::Policy::up,global_tx[eye]);
                    else need(false);
                }else wire[eye]=plain(raw[eye],base_tx[eye],compressed[eye]);
            }
            auto se=cpu();auto encoded=Clock::now();auto cb=cpu();std::array<std::span<const uint32_t>,2>out;
            if(which>=2){auto pair=rowworkers::decode_pair(wire[0],wire[1],client[which-2],&client4);out={pair.left,pair.right};}
            else for(unsigned eye=0;eye<2;++eye){if(which)rowfilter::decode_into(wire[eye],decoded[eye],global_rx[eye]);else{View b=wire[eye];if(compressed[eye]){wpc::decode(b,base_rx[eye]);b=base_rx[eye].decoded;}auto shape=direct::preflight(b);direct::decode(b,shape,decoded[eye],true);}out[eye]=decoded[eye];}
            auto ce=cpu();auto finish=Clock::now();
            for(unsigned eye=0;eye<2;++eye){need(out[eye].size()==oracle[eye].size()&&std::equal(oracle[eye].begin(),oracle[eye].end(),out[eye].begin()));if(which)need(same(wire[eye],canonical[which==1?0:which<7?1:2][eye]));}
            if(which>=2&&(which-2)%5>=2)capacities[which]=std::max(capacities[which],rowbands::retained_capacity_bytes(fused[which<7?which-4:which-6]));
            size_t size=wire[0].size()+wire[1].size();if(rep==0)sizes[which]=size;else need(sizes[which]==size);
            if(rep>=3){server[which].push_back(ms(begin,encoded));server_cpu[which].push_back(double(se-sb)/1e6);decode_wall[which].push_back(ms(encoded,finish));decode_cpu[which].push_back(double(ce-cb)/1e6);combined[which].push_back(ms(begin,finish));}
        }
        std::cout<<(ci++?",":"")<<"{\"name\":"<<quote(input.name)<<",\"left_source\":"<<quote(fs::relative(input.path[0],argv[1]).generic_string())<<",\"right_source\":"<<quote(fs::relative(input.path[1],argv[1]).generic_string())<<",\"synthetic_duplicate_eye\":"<<(input.synthetic?"true":"false")<<",\"width\":"<<width<<",\"height\":"<<height<<",\"q\":"<<shapes[0].q<<",\"exact_stereo\":true,\"canonical_reference_wire_equal\":true,\"profiles\":[";
        for(unsigned i=0;i<profiles;++i){std::cout<<(i?",":"")<<"{\"name\":"<<quote(names[i])<<",\"stereo_wire_bytes\":"<<sizes[i]<<",\"fused_retained_staging_capacity_bytes\":"<<(i>=2&&(i-2)%5>=2?std::to_string(capacities[i]):"null")<<",\"server_transcode_wall_ms\":";dist(server[i]);std::cout<<",\"server_process_cpu_ms\":";dist(server_cpu[i]);std::cout<<",\"client_decode_wall_ms\":";dist(decode_wall[i]);std::cout<<",\"client_process_cpu_ms\":";dist(decode_cpu[i]);std::cout<<",\"server_plus_client_wall_ms\":";dist(combined[i]);std::cout<<"}";}
        std::cout<<"]}"<<std::flush;
    }std::cout<<"]}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
