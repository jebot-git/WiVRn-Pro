// SPDX-License-Identifier: MIT
#include "wpc.hpp"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <random>
#include <string>

namespace fs=std::filesystem;
using Clock=std::chrono::steady_clock;
wpc::Bytes read(const fs::path &p) {
    auto size=fs::file_size(p); wpc::require(size>0 && size<=wpc::wire_limit);
    std::ifstream f(p,std::ios::binary); wpc::require(bool(f));
    wpc::Bytes b(size); f.read(reinterpret_cast<char*>(b.data()),size); wpc::require(f.gcount()==std::streamsize(size));
    wpc::require(f.peek()==std::char_traits<char>::eof()); return b;
}
void write(const fs::path&p,wpc::View b) { std::ofstream f(p,std::ios::binary); f.write(reinterpret_cast<const char*>(b.data()),b.size()); wpc::require(bool(f)); }
bool rejected(wpc::View b) { wpc::Workspace w; try { wpc::decode(b,w); return false; } catch(const std::runtime_error&) { return true; } }
void test() {
    std::mt19937 rng(8008); size_t trips=0, trunc=0, mutations=0;
    wpc::Bytes check={'1','2','3','4','5','6','7','8','9'}; wpc::require(wpc::crc(check)==0xcbf43926u);
    for(size_t n:{size_t(1),size_t(255),size_t(256),size_t(1100),size_t(16385),size_t(100000)}) {
        for(int pattern=0;pattern<3;++pattern) {
            wpc::Bytes raw(n); for(size_t i=0;i<n;++i) raw[i]=pattern==0?0:pattern==1?uint8_t(i%43):uint8_t(rng());
            wpc::require(wpc::crc(raw)==wpc::crc_table(raw));
            for(uint32_t block:{1100u,16384u,wpc::wire_limit}) {
                wpc::Workspace w; wpc::encode(raw,block,w); wpc::decode(w.wire,w); wpc::require(raw==w.decoded); ++trips;
                for(size_t cut=0;cut<w.wire.size();cut+=(w.wire.size()<2048?1:std::max<size_t>(1,w.wire.size()/97))) { wpc::require(rejected(wpc::View(w.wire).first(cut))); ++trunc; }
                auto b=w.wire; b.push_back(0); wpc::require(rejected(b));
                for(int j=0;j<100;++j) {
                    b=w.wire; auto pos=rng()%b.size(); b[pos]^=uint8_t(1u<<(rng()%8));
                    // Different valid compressed representations can produce identical bytes.
                    wpc::Workspace changed; bool accepted=true; try { wpc::decode(b,changed); } catch(const std::runtime_error&) { accepted=false; }
                    if(accepted)wpc::require(changed.decoded==raw); ++mutations;
                }
            }
        }
    }
    wpc::Workspace w; wpc::Bytes max(wpc::input_limit,7); wpc::encode(max,wpc::wire_limit,w); wpc::decode(w.wire,w); wpc::require(w.decoded==max);
    max.push_back(0); bool failed=false; try { wpc::encode(max,wpc::wire_limit,w); } catch(const std::runtime_error&) { failed=true; } wpc::require(failed);
    max.resize(wpc::input_limit); for(auto&v:max)v=uint8_t(rng());
    wpc::encode(max,wpc::wire_limit,w); wpc::require(w.wire.size()==wpc::wire_limit && w.wire[5]==0);
    wpc::decode(w.wire,w); wpc::require(w.decoded==max);
    // Reject high expansion and reserved bytes before allocating output.
    wpc::Bytes raw(9000,0); wpc::encode(raw,1100,w);
    for(size_t p:{size_t(4),size_t(5),size_t(6),size_t(7),size_t(25),size_t(26),size_t(27)}) { auto b=w.wire; b[p]=255; wpc::require(rejected(b)); }
    auto b=w.wire; for(int i=8;i<12;++i)b[i]=255; wpc::require(rejected(b));
    auto throws=[](auto fn){bool no=true;try{fn();}catch(const std::runtime_error&){no=false;}wpc::require(!no);};
    throws([&]{wpc::encode(w.wire,1100,w);});
    throws([&]{wpc::encode(w.scratch,1100,w);});
    w.decoded=w.wire; throws([&]{wpc::decode(w.decoded,w);});
    std::cout<<"{\"status\":\"pass\",\"roundtrips\":"<<trips+2<<",\"truncations\":"<<trunc<<",\"mutations\":"<<mutations<<",\"maximum_input_bytes\":"<<wpc::input_limit<<",\"maximum_wire_bytes\":"<<wpc::wire_limit<<"}\n";
}
double ms(Clock::time_point a,Clock::time_point b) { return std::chrono::duration<double,std::milli>(b-a).count(); }
void dist(std::vector<double> v) { std::sort(v.begin(),v.end()); double sum=0; for(auto x:v)sum+=x; std::cout<<"{\"p50\":"<<v[(v.size()-1)/2]<<",\"p95\":"<<v[(95*v.size()+99)/100-1]<<",\"max\":"<<v.back()<<",\"mean\":"<<sum/v.size()<<"}"; }
int main(int argc,char**argv) { try {
    if(argc==2&&std::string(argv[1])=="self-test") { test(); return 0; }
    if(argc==5&&std::string(argv[1])=="encode") { auto raw=read(argv[2]); wpc::Workspace w; wpc::encode(raw,uint32_t(std::stoul(argv[4])),w); write(argv[3],w.wire); return 0; }
    if(argc==4&&std::string(argv[1])=="decode") { auto b=read(argv[2]); wpc::Workspace w; wpc::decode(b,w); write(argv[3],w.decoded); return 0; }
    wpc::require(argc==4&&std::string(argv[1])=="bench");
    int reps=std::stoi(argv[3]); wpc::require(reps>=5&&reps<=200);
    std::vector<fs::path> paths; for(auto &e:fs::directory_iterator(argv[2])) if(e.path().extension()==".payload")paths.push_back(e.path()); std::sort(paths.begin(),paths.end()); wpc::require(!paths.empty());
    std::cout<<"{\"lz4\":\""<<LZ4_versionString()<<"\",\"arm_crc\":"<<(wpc::arm_crc_enabled()?"true":"false")<<",\"repetitions\":"<<reps<<",\"timing\":\"steady_clock; warmed capacity reuse; framing/copies/CRC included; IO and exact comparison excluded\",\"rows\":[";
    bool comma=false;
    struct Job {fs::path p;uint32_t block;}; std::vector<Job> jobs;
    for(auto&p:paths)for(uint32_t block:{wpc::wire_limit,16384u,1100u})jobs.push_back({p,block});
    std::shuffle(jobs.begin(),jobs.end(),std::mt19937(8008));
    for(auto&job:jobs) {
        auto raw=read(job.p); auto block=job.block; wpc::Workspace w;
        for(int i=0;i<3;++i){wpc::encode(raw,block,w);wpc::decode(w.wire,w);wpc::require(w.decoded==raw);}
        std::vector<double> enc,dec,check,copy;
        for(int i=0;i<reps;++i) {
            auto a=Clock::now(); wpc::encode(raw,block,w); auto b=Clock::now(); wpc::decode(w.wire,w); auto c=Clock::now(); wpc::require(w.decoded==raw);
            auto d=Clock::now(); volatile auto crc=wpc::crc(raw); (void)crc; auto e=Clock::now();
            auto f=Clock::now(); std::memcpy(w.decoded.data(),raw.data(),raw.size()); auto g=Clock::now(); wpc::require(w.decoded==raw);
            enc.push_back(ms(a,b));dec.push_back(ms(b,c));check.push_back(ms(d,e));copy.push_back(ms(f,g));
        }
        if(comma)std::cout<<",";comma=true;
        auto name=job.p.filename().string(); wpc::require(name.find_first_of("\"\\\n\r")==std::string::npos);
        std::cout<<"{\"sample\":\""<<name<<"\",\"block\":"<<block<<",\"original_bytes\":"<<raw.size()<<",\"wrapped_bytes\":"<<w.wire.size()<<",\"raw_escape\":"<<(w.wire[5]==0?"true":"false")<<",\"encode_ms\":";dist(enc);std::cout<<",\"decode_ms\":";dist(dec);std::cout<<",\"crc_ms\":";dist(check);std::cout<<",\"copy_ms\":";dist(copy);std::cout<<"}";
    }
    std::cout<<"]}\n"; return 0;
} catch(const std::exception&e) { std::cerr<<e.what()<<"\n"; return 1; } }
