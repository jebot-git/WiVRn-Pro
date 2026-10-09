// SPDX-License-Identifier: MIT
#pragma once
#include "../rgb_rowblocks/rowblocks.hpp"
#include <atomic>
#include <condition_variable>
#include <exception>
#include <mutex>
#include <thread>

namespace rowworkers {
using rowblocks::Bytes;using rowblocks::View;using rowblocks::need;
constexpr size_t max_jobs=2*rowblocks::max_blocks;
// Fixed persistent workers, at most one synchronous batch, no task queue. The
// caller owns the pool lifetime and must not destroy it during a running call.
class Pool {
public:
    using Callback=void(*)(void*,size_t,unsigned);
    explicit Pool(unsigned count){
        need(count>=1&&count<=4);
        try{for(unsigned i=0;i<count;++i)threads.emplace_back([this,i]{worker(i);});}
        catch(...){stop_and_join();throw;}
    }
    ~Pool(){stop_and_join();}
    Pool(const Pool&)=delete;Pool&operator=(const Pool&)=delete;
    unsigned size()const{return unsigned(threads.size());}
    void run(size_t jobs,void*context,Callback callback){
        need(jobs>0&&jobs<=max_jobs&&context&&callback);
        std::unique_lock lock(mutex);need(!busy&&!stop&&generation<UINT64_MAX);
        busy=true;count=jobs;ctx=context;fn=callback;next.store(0);failed.store(false);error=nullptr;
        remaining=threads.size();++generation;wake.notify_all();
        done.wait(lock,[&]{return remaining==0;});
        auto failure=error;busy=false;ctx=nullptr;fn=nullptr;
        lock.unlock();if(failure)std::rethrow_exception(failure);
    }
private:
    std::vector<std::thread>threads;
    std::mutex mutex;std::condition_variable wake,done;
    bool stop=false,busy=false;uint64_t generation=0;size_t remaining=0,count=0;
    void*ctx=nullptr;Callback fn=nullptr;std::exception_ptr error;
    std::atomic<size_t>next{0};std::atomic<bool>failed{false};
    void stop_and_join(){
        {std::lock_guard lock(mutex);stop=true;}wake.notify_all();
        for(auto&t:threads)if(t.joinable())t.join();
    }
    void worker(unsigned id){
        uint64_t seen=0;std::unique_lock lock(mutex);
        for(;;){wake.wait(lock,[&]{return stop||generation!=seen;});if(stop)return;
            seen=generation;auto callback=fn;auto context=ctx;auto limit=count;lock.unlock();
            try{while(!failed.load(std::memory_order_relaxed)){
                    size_t job=next.fetch_add(1,std::memory_order_relaxed);if(job>=limit)break;
                    callback(context,job,id);
                }}catch(...){failed.store(true,std::memory_order_relaxed);std::lock_guard guard(mutex);if(!error)error=std::current_exception();}
            lock.lock();if(--remaining==0)done.notify_one();
        }
    }
};

struct Workspace {
    std::array<Bytes,2>rgb;
    std::array<std::vector<uint32_t>,2>rgba;
    std::array<rowblocks::Index,2>index;
    void clear(){for(auto&b:rgb)b.clear();for(auto&b:rgba)b.clear();for(auto&i:index)i.blocks.clear();}
};
struct Stereo {std::span<const uint32_t>left,right;uint32_t width,height;};
template<class T>inline bool alias(View input,const std::vector<T>&buffer){
    if(input.empty()||!buffer.capacity())return false;
    auto a=reinterpret_cast<uintptr_t>(input.data()),b=reinterpret_cast<uintptr_t>(buffer.data());
    return a<=b?b-a<input.size():a-b<buffer.capacity()*sizeof(T);
}
inline bool alias(View input,const Workspace&w){
    for(unsigned eye=0;eye<2;++eye)
        if(alias(input,w.rgb[eye])||alias(input,w.rgba[eye])||alias(input,w.index[eye].blocks))return true;
    return false;
}
struct Context {std::array<View,2>wires;Workspace*workspace;};
inline void decode_job(void*opaque,size_t job,unsigned){
    auto&c=*static_cast<Context*>(opaque);auto&w=*c.workspace;
    unsigned eye=job>=w.index[0].blocks.size();size_t local=job-(eye?w.index[0].blocks.size():0);
    auto&index=w.index[eye];auto&b=index.blocks[local];
    auto body=c.wires[eye].subspan(b.offset+rowfilter::header_bytes,b.length-rowfilter::header_bytes);
    auto rgb=std::span(w.rgb[eye]).subspan(b.raw_offset,b.raw_bytes);
    if(b.mode){int n=LZ4_decompress_safe(reinterpret_cast<const char*>(body.data()),reinterpret_cast<char*>(rgb.data()),int(body.size()),int(rgb.size()));need(n==int(rgb.size()));}
    else std::copy(body.begin(),body.end(),rgb.begin());
    if(b.mode==2){size_t stride=size_t(index.width)*3;
        for(size_t row=stride;row<rgb.size();row+=stride)
            rowfilter::detail::add_row(rgb.subspan(row,stride),View(rgb).subspan(row-stride,stride),true);
    }
    auto output=std::span(w.rgba[eye]).subspan(size_t(b.row)*index.width,size_t(b.height)*index.width);
    rowfilter::detail::expand(rgb,output,true);
}
// Both frames are fully inspected before dispatch. Workers write only disjoint
// private staging ranges. The lease is returned only after all jobs complete;
// a malformed eye or worker failure clears both staged eyes. The caller must
// validate session/frame/config identity and perform its own atomic publication.
// Inputs and workspace are exclusively owned for the call; the returned spans
// last only until the next decode/clear/destruction of this workspace.
inline Stereo decode_pair(View left,View right,Workspace&w,Pool*pool=nullptr){
    need(!alias(left,w)&&!alias(right,w));
    // Successful calls overwrite every admitted staging byte. Retain sizes so
    // reuse does not redundantly zero both RGB and RGBA before reconstruction;
    // any failure still invalidates/clears both eyes in the catch below.
    try{w.index[0]=rowblocks::inspect(left);w.index[1]=rowblocks::inspect(right);
        need(w.index[0].width==w.index[1].width&&w.index[0].height==w.index[1].height);
        size_t pixels=size_t(w.index[0].width)*w.index[0].height;
        for(unsigned eye=0;eye<2;++eye){w.rgb[eye].resize(pixels*3);w.rgba[eye].resize(pixels);}
        Context context{{left,right},&w};size_t jobs=w.index[0].blocks.size()+w.index[1].blocks.size();need(jobs<=max_jobs);
        if(pool)pool->run(jobs,&context,decode_job);else for(size_t i=0;i<jobs;++i)decode_job(&context,i,0);
        return {w.rgba[0],w.rgba[1],w.index[0].width,w.index[0].height};
    }catch(...){w.clear();throw;}
}
}
