// SPDX-License-Identifier: MIT
#pragma once
#include "../wire.h"
#include "band_encoder.hpp"
#include <memory>
#include <optional>

namespace wivrn::codec_test::rrb {
constexpr size_t stereo_wire_cap=262144; // Includes both 48-byte WCT1 envelopes.
constexpr uint32_t block_rows=32;
inline void require(bool ok){if(!ok)throw std::runtime_error("invalid native RRB32 pair");}
inline void validate_pair(const std::array<Header,2>&h,const std::array<std::span<const uint8_t>,2>&body){
    for(unsigned e=0;e<2;++e){validate(h[e]);require(h[e].eye==e&&h[e].kind==Kind::rrb32&&h[e].flags==0);}
    require(h[0].frame==h[1].frame&&h[0].warp==h[1].warp&&h[0].width==h[1].width&&h[0].height==h[1].height);
    require(body[0].size()<=stereo_wire_cap-2*header_size&&body[1].size()<=stereo_wire_cap-2*header_size-body[0].size());
    // Both complete grammars, inner CRCs and shapes precede any reconstruction.
    for(unsigned e=0;e<2;++e){auto i=rowblocks::inspect(body[e]);require(i.width==h[e].width&&i.height==h[e].height&&i.block_rows==block_rows);}
}
struct Pair {
    rowworkers::Workspace cpu;
    std::array<rowblocks::Workspace,2> residual;
    std::array<rowblocks::Index,2> index;
    uint64_t frame=0,warp=0;
    uint32_t width=0,height=0;
    double decode_ms=0;
};
// Generation-owned persistent pool. Exactly one call per admitted eye/frame;
// the native stereo admission barrier prevents the next frame starting early.
// Input bodies remain immutable/alive until BOTH calls return. Returned shared
// leases prevent reuse while any consumer retains private output. stop() wakes
// an orphan waiter; destruction is after the native workers have joined.
class PairDecoder {
    rowworkers::Pool pool;
    bool residual_only;
    std::mutex mutex;std::condition_variable cv;
    bool stopped=false,ready=false;
    std::optional<uint64_t> frame;
    std::array<bool,2> present{};
    std::array<Header,2> headers{};
    std::array<std::span<const uint8_t>,2> bodies{};
    std::exception_ptr error;
    std::shared_ptr<Pair> output=std::make_shared<Pair>();
public:
    PairDecoder(unsigned workers,bool residual=false):pool(workers),residual_only(residual){require(workers==2||workers==4);}
    unsigned workers()const{return pool.size();}
    void stop(){std::lock_guard lock(mutex);stopped=true;cv.notify_all();}
    std::shared_ptr<const Pair> acquire(Header h,std::span<const uint8_t>body){
        require(h.eye<2);std::unique_lock lock(mutex);require(!stopped);
        if(!frame||*frame!=h.frame){
            require(!frame||ready);require(!frame||h.frame>*frame);
            require(output.use_count()==1);frame=h.frame;present={};ready=false;error=nullptr;bodies={};
        }
        require(!present[h.eye]);present[h.eye]=true;headers[h.eye]=h;bodies[h.eye]=body;
        if(present[0]&&present[1]){
            try{
                const auto began=std::chrono::steady_clock::now();validate_pair(headers,bodies);
                if(residual_only){
                    for(unsigned e=0;e<2;++e)output->index[e]=rowblocks::unpack_rgb(bodies[e],output->residual[e]);
                }else rowworkers::decode_pair(bodies[0],bodies[1],output->cpu,&pool);
                output->frame=h.frame;output->warp=h.warp;output->width=h.width;output->height=h.height;
                output->decode_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-began).count();
            }catch(...){output->cpu.clear();for(auto&w:output->residual)w.rgb.clear();for(auto&i:output->index)i.blocks.clear();error=std::current_exception();}
            ready=true;bodies={};cv.notify_all();
        }else cv.wait(lock,[&]{return stopped||ready;});
        require(!stopped);if(error)std::rethrow_exception(error);return output;
    }
};
// Collect complete producer-finished handles privately. Only the second eye
// may return a pair, and any missing/failed eye suppresses the whole pair.
// Caller publishes the returned pair atomically using the native stream lock.
template<class T>class Publication {
    std::mutex mutex;std::optional<uint64_t> frame;std::array<bool,2>seen{};std::array<std::shared_ptr<T>,2>pending{};
public:
    std::optional<std::array<std::shared_ptr<T>,2>> arrive(unsigned eye,uint64_t id,std::shared_ptr<T>value){
        require(eye<2);std::lock_guard lock(mutex);
        if(!frame||*frame!=id){require(!frame||(seen[0]&&seen[1]&&id>*frame));frame=id;seen={};pending={};}
        require(!seen[eye]);seen[eye]=true;pending[eye]=std::move(value);
        if(!seen[0]||!seen[1])return {};
        auto pair=std::move(pending);pending={};if(!pair[0]||!pair[1])return {};return pair;
    }
};
// The server's existing encoder batch stages both eyes before sending. Adapt
// the validated band-view algorithm to one eye without duplicating its work;
// joint envelope-byte admission remains the encoder batch's responsibility.
inline rowblocks::View transcode_eye(rowblocks::View input,rowbands::Workspace&w,rowworkers::Pool&pool){
    rowblocks::need(!rowbands::alias(input,w));
    try{
        w.shape[0]=direct::preflight(input);rowfilter::pixels(w.shape[0].width,w.shape[0].height);
        const size_t n=(w.shape[0].height+block_rows-1)/block_rows;
        w.block_rows=block_rows;w.policy=rowfilter::Policy::adaptive;
        rowbands::index_rows(input,w.shape[0],w.row_offsets[0]);w.blocks[0].resize(n);w.blocks[1].clear();
        rowbands::Context context{{input,{}},&w};pool.run(n,&context,rowbands::fused_job);
        rowencoder::assemble(w,0);return w.wire[0];
    }catch(...){w.clear();throw;}
}
}
