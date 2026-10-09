// SPDX-License-Identifier: MIT
#include "workers.hpp"
#include <chrono>
#include <fstream>
#include <iostream>
#include <random>
using namespace rowworkers;
static size_t trips=0,rejections=0;
template<class F>static void reject(F f){bool failed=false;try{f();}catch(const std::runtime_error&){failed=true;}need(failed);++rejections;}
static Bytes read(const char*p){std::ifstream f(p,std::ios::binary);need(bool(f));return {std::istreambuf_iterator<char>(f),{}};}
static void empty(const Workspace&w){for(unsigned e=0;e<2;++e)need(w.rgb[e].empty()&&w.rgba[e].empty()&&w.index[e].blocks.empty());}
int main(int argc,char**argv){try{
    Pool one(1),two(2),four(4);std::array<Pool*,4>pools{nullptr,&one,&two,&four};Workspace w;
    if(argc==3){auto wire=read(argv[1]),rgba=read(argv[2]);
        for(auto*p:pools){auto s=decode_pair(wire,wire,w,p);need(rowfilter::bytes(s.left).size()==rgba.size());need(std::equal(rgba.begin(),rgba.end(),rowfilter::bytes(s.left).begin()));need(std::equal(s.left.begin(),s.left.end(),s.right.begin()));}
        std::cout<<"{\"golden_exact_serial_and_workers\":true}\n";return 0;}
    need(argc==1);reject([]{Pool bad(0);});reject([]{Pool bad(5);});
    struct Counts {std::array<std::atomic<unsigned>,max_jobs>seen{};std::atomic<unsigned>sum{0};};Counts counts;
    auto count=[](void*opaque,size_t job,unsigned worker){need(worker<4&&job<max_jobs);auto&c=*static_cast<Counts*>(opaque);++c.seen[job];++c.sum;};
    for(auto*p:{&one,&two,&four})for(size_t jobs:{size_t(1),size_t(2),size_t(17),max_jobs}){
        for(auto&n:counts.seen)n.store(0);counts.sum=0;p->run(jobs,&counts,count);
        need(counts.sum==jobs);for(size_t i=0;i<max_jobs;++i)need(counts.seen[i]==unsigned(i<jobs));}
    for(auto*p:{&one,&two,&four}){
        reject([&]{p->run(0,&counts,count);});reject([&]{p->run(max_jobs+1,&counts,count);});
        reject([&]{p->run(1,nullptr,count);});reject([&]{p->run(1,&counts,nullptr);});
        reject([&]{p->run(max_jobs,&counts,[](void*,size_t,unsigned){throw std::runtime_error("injected job failure");});});
        counts.sum=0;p->run(max_jobs,&counts,count);need(counts.sum==max_jobs);
    }
    // A second caller cannot queue or replace an active frame batch.
    struct Gate {std::mutex mutex;std::condition_variable cv;bool entered=false,release=false;};Gate gate;
    std::exception_ptr submitted_error;
    std::thread submitter([&]{try{four.run(1,&gate,[](void*opaque,size_t,unsigned){auto&g=*static_cast<Gate*>(opaque);
        std::unique_lock lock(g.mutex);g.entered=true;g.cv.notify_all();g.cv.wait(lock,[&]{return g.release;});});}catch(...){submitted_error=std::current_exception();}});
    std::unique_lock gate_lock(gate.mutex);bool entered=gate.cv.wait_for(gate_lock,std::chrono::seconds(2),[&]{return gate.entered;});gate_lock.unlock();
    bool active_rejected=false;try{four.run(1,&counts,count);}catch(const std::runtime_error&){active_rejected=true;}
    gate_lock.lock();gate.release=true;gate_lock.unlock();gate.cv.notify_all();submitter.join();
    need(entered&&active_rejected&&!submitted_error);++rejections;
    // Stable generation publication and worker completion across many batches.
    counts.sum=0;for(unsigned rep=0;rep<1000;++rep)four.run(3,&counts,count);need(counts.sum==3000);
    std::mt19937 rng(51092026);rowblocks::Workspace txl,txr;
    for(auto [width,height]:{std::pair{1u,1u},{1u,4096u},{17u,17u},{17u,65u},{35u,129u},{576u,640u}})
        for(unsigned kind=0;kind<3;++kind){std::vector<uint32_t>left(size_t(width)*height),right(left.size());
            for(size_t i=0;i<left.size();++i){left[i]=(kind==0?0x123456:kind==1?unsigned(i*713):rng())|0xff000000;right[i]=(kind==0?0xabcdef:kind==1?unsigned(i*419):rng())|0xff000000;}
            for(uint32_t rows:{16,32,128}){auto l=rowblocks::encode(left,width,height,rows,rowfilter::Policy::up,txl),r=rowblocks::encode(right,width,height,rows,rowfilter::Policy::adaptive,txr);
                for(auto*p:pools){auto s=decode_pair(l,r,w,p);need(s.width==width&&s.height==height&&std::equal(left.begin(),left.end(),s.left.begin())&&std::equal(right.begin(),right.end(),s.right.begin()));++trips;}
            }
        }
    // A retained lease lives in a different workspace from a new candidate.
    std::vector<uint32_t>left(17*65,0xff123456),right(left.size(),0xffabcdef);
    auto l=rowblocks::encode(left,17,65,16,rowfilter::Policy::up,txl),r=rowblocks::encode(right,17,65,32,rowfilter::Policy::up,txr);
    Workspace published;auto good=decode_pair(l,r,published,&four);Bytes original(r.begin(),r.end());
    auto old_unchanged=[&]{need(std::equal(left.begin(),left.end(),good.left.begin())&&std::equal(right.begin(),right.end(),good.right.begin()));};
    for(size_t n=0;n<original.size();++n){reject([&]{decode_pair(l,View(original).first(n),w,&four);});empty(w);old_unchanged();}
    for(unsigned eye=0;eye<2;++eye)for(unsigned i=0;i<2000;++i){Bytes bad=(eye?Bytes(r.begin(),r.end()):Bytes(l.begin(),l.end()));bad[rng()%bad.size()]^=uint8_t(1u<<(rng()%8));
        reject([&]{decode_pair(eye?l:View(bad),eye?View(bad):r,w,&four);});empty(w);old_unchanged();}
    // Aliasing is a precondition failure; it must not invalidate a prior lease.
    reject([&]{decode_pair(rowfilter::bytes(good.left),r,published,&four);});old_unchanged();
    for(unsigned eye=0;eye<2;++eye){auto&index=published.index[eye].blocks;
        View index_bytes(reinterpret_cast<const uint8_t*>(index.data()),index.size()*sizeof(rowblocks::Block));
        reject([&]{decode_pair(index_bytes,r,published,&four);});
        need(published.rgba[0].size()==left.size()&&published.rgba[1].size()==right.size()&&!index.empty());old_unchanged();}
    std::vector<uint32_t>different(17*64,0xff112233);auto wrong=rowblocks::encode(different,17,64,16,rowfilter::Policy::up,txr);
    reject([&]{decode_pair(l,wrong,w,&four);});empty(w);old_unchanged();
    // Both maximum4MP eyes and bounded512jobs (height4096) have disjoint staging.
    std::vector<uint32_t>maximum(rowfilter::max_pixels,0xff223344),other(maximum.size(),0xffbbccdd);
    l=rowblocks::encode(maximum,1024,4096,16,rowfilter::Policy::up,txl);r=rowblocks::encode(other,1024,4096,16,rowfilter::Policy::up,txr);
    auto s=decode_pair(l,r,w,&four);need(s.left.size()==maximum.size()&&std::equal(maximum.begin(),maximum.end(),s.left.begin())&&std::equal(other.begin(),other.end(),s.right.begin()));++trips;
    std::cout<<"{\"pass\":true,\"stereo_roundtrips\":"<<trips<<",\"rejections\":"<<rejections<<",\"corruption_cases\":4000,\"worker_counts\":[1,2,4],\"max_jobs\":"<<max_jobs<<",\"generation_stress_batches\":1000,\"maximum_pixels_per_eye\":"<<rowfilter::max_pixels<<",\"previous_separate_lease_retained\":true}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
