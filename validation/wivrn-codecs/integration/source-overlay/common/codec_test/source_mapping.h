// SPDX-License-Identifier: MIT
#pragma once
// Test-only immutable source mapping evidence. No source pixels or pose are
// synthesized; native image handles identify objects only within this process.
#include <algorithm>
#include <array>
#include <bit>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <mutex>
#include <atomic>
#include <span>
#include <stdexcept>
#include <string_view>
#include <cstdint>

namespace wivrn::codec_test::source_mapping {
inline constexpr uint32_t max_axis=4096, max_params=129, stride=90, capacity=128;
struct Eye {
    uint64_t swapchain=0,image=0,image_view=0;
    uint32_t image_index=0,array_index=0,source_width=0,source_height=0;
    uint32_t source_array_size=0,source_face_count=0,source_sample_count=0,source_mip_count=0,source_image_count=0;
    uint64_t source_create_flags=0;
    uint32_t source_format=0,view_format=0;
    std::array<int32_t,4> rect{};
    std::array<uint32_t,4> fov_bits_lrud{};
    uint32_t x_params_count=0,y_params_count=0;
    std::array<uint16_t,max_params> x_params{},y_params{};
    std::array<uint32_t,max_axis+1> x_edges{},y_edges{};
};
struct Record {
    uint64_t compositor=0,frame=0,warp=0,semaphore_value=0;
    int64_t layer_target_ns=0,submit_return_host_ns=0,complete_host_ns=0;
    uint64_t layer_flags=0;
    uint32_t width=0,height=0,layer_count=0,layer_type=0,layer_index=0;
    bool flip_y=false,direct_projection=false,view_space=false;
    std::array<Eye,2> eyes{};
};
inline bool supported(const Record& r) noexcept {
    if(!r.compositor || r.frame>uint64_t(INT64_MAX) || r.frame%stride || !r.semaphore_value ||
       r.layer_target_ns<=0 || r.submit_return_host_ns<=0 || r.complete_host_ns<r.submit_return_host_ns ||
       r.view_space || !r.direct_projection || r.layer_count!=1 || r.layer_index!=0 || r.layer_type>1 ||
       !r.width || !r.height || r.width>max_axis || r.height>max_axis || uint64_t(r.width)*r.height>4194304) return false;
    for(const auto& e:r.eyes) {
        if(!e.swapchain || !e.image || !e.image_view || !e.source_width || !e.source_height ||
           e.source_width>max_axis || e.source_height>max_axis || uint64_t(e.source_width)*e.source_height>4194304 ||
           !e.x_params_count || !e.y_params_count || e.x_params_count>max_params || e.y_params_count>max_params ||
           !(e.x_params_count&1) || !(e.y_params_count&1) || e.rect[0]<0 || e.rect[1]<0 ||
           e.rect[2]<=0 || e.rect[3]<=0 || uint64_t(e.rect[0])+uint32_t(e.rect[2])>e.source_width ||
           uint64_t(e.rect[1])+uint32_t(e.rect[3])>e.source_height ||
           e.source_format!=43 || e.view_format!=43 || e.source_array_size!=1 || e.source_face_count!=1 ||
           e.source_sample_count!=1 || e.source_mip_count!=1 || !e.source_image_count || e.source_image_count>16 ||
           e.image_index>=e.source_image_count || e.array_index!=0) return false;
        // Initial application route is opaque RGBA8_SRGB, positive subrects;
        // descending X is not reordered by the actual shader and is rejected.
        for(uint32_t x=0;x<r.width;++x)
            if(e.x_edges[x]>=e.x_edges[x+1] || e.x_edges[x+1]>e.source_width) return false;
        for(uint32_t y=0;y<r.height;++y)
            if(e.y_edges[y]>e.source_height || e.y_edges[y+1]>e.source_height ||
               (r.flip_y ? e.y_edges[y]<=e.y_edges[y+1] : e.y_edges[y]>=e.y_edges[y+1])) return false;
    }
    return true;
}
class Trace {
    FILE* output=nullptr;
    std::unique_ptr<Record[]> records;
    uint32_t count=0,dropped=0,failed=0;
    std::atomic<bool> active{false};
    std::mutex mutex;
    template<class T> void array(std::span<const T> values) noexcept {
        std::fputc('[',output);
        for(size_t i=0;i<values.size();++i)std::fprintf(output,"%s%lld",i?",":"",(long long)values[i]);
        std::fputc(']',output);
    }
public:
    explicit Trace(const char* path,bool contract=true) {
        if(!path||!*path)return;
        if(!contract || path[0]!='/')throw std::runtime_error("source mapping requires explicit Q1 quality/source capture and absolute new trace path");
        records=std::make_unique<Record[]>(capacity);
        output=std::fopen(path,"wx");if(!output)throw std::runtime_error("cannot create source mapping trace");active=true;
    }
    Trace(const Trace&)=delete;Trace&operator=(const Trace&)=delete;
    bool enabled() const noexcept {return active.load();}
    bool sample(uint64_t frame) const noexcept{return active.load()&&frame<=uint64_t(INT64_MAX)&&frame%stride==0;}
    void failure() noexcept {std::lock_guard lock(mutex);if(active)++failed;}
    void append(const Record& r) noexcept {
        std::lock_guard lock(mutex);
        if(!active)return;
        if(!supported(r)){++failed;return;}
        if(count==capacity){++dropped;return;}
        records[count++]=r;
    }
    void flush() noexcept {
        std::lock_guard lock(mutex);
        if(!active)return;
        active=false;
        std::fprintf(output,"{\"type\":\"header\",\"schema\":1,\"capacity\":128,\"sample_stride\":90,\"max_axis\":4096,\"max_params\":129,\"shader\":\"foveation_rgb.comp\",\"sampling\":\"integer_texelFetch_max4x4_average_linear_then_srgb\",\"opaque_rgba_srgb_only\":true,\"quality_only\":true,\"latency_eligible\":false,\"native_handle_scope\":\"process_local_only\"}\n");
        for(uint32_t i=0;i<count;++i){const auto&r=records[i];
            std::fprintf(output,"{\"type\":\"mapping\",\"sequence\":%u,\"compositor_id\":%llu,\"frame\":%llu,\"warp\":%llu,\"semaphore_value\":%llu,\"layer_target_host_ns\":%lld,\"submit_return_host_ns\":%lld,\"complete_host_ns\":%lld,\"layer_flags\":%llu,\"width\":%u,\"height\":%u,\"layer_count\":%u,\"layer_type\":%u,\"layer_index\":%u,\"flip_y\":%s,\"direct_projection\":%s,\"view_space\":%s,\"producer_complete\":true,\"eyes\":[",i+1,(unsigned long long)r.compositor,(unsigned long long)r.frame,(unsigned long long)r.warp,(unsigned long long)r.semaphore_value,(long long)r.layer_target_ns,(long long)r.submit_return_host_ns,(long long)r.complete_host_ns,(unsigned long long)r.layer_flags,r.width,r.height,r.layer_count,r.layer_type,r.layer_index,r.flip_y?"true":"false",r.direct_projection?"true":"false",r.view_space?"true":"false");
            for(uint32_t eye=0;eye<2;++eye){const auto&e=r.eyes[eye];
                std::fprintf(output,"%s{\"eye\":%u,\"swapchain_id\":%llu,\"image_id\":%llu,\"image_view_id\":%llu,\"image_index\":%u,\"array_index\":%u,\"source_width\":%u,\"source_height\":%u,\"source_create_flags\":%llu,\"source_vk_format\":%u,\"view_vk_format\":%u,\"view_format_provenance\":\"pinned_comp_swapchain_view_creation\",\"src_rect\":",eye?",":"",eye,(unsigned long long)e.swapchain,(unsigned long long)e.image,(unsigned long long)e.image_view,e.image_index,e.array_index,e.source_width,e.source_height,(unsigned long long)e.source_create_flags,e.source_format,e.view_format);
                array<int32_t>(e.rect);
                std::fprintf(output,",\"source_array_size\":%u,\"source_face_count\":%u,\"source_sample_count\":%u,\"source_mip_count\":%u,\"source_image_count\":%u",e.source_array_size,e.source_face_count,e.source_sample_count,e.source_mip_count,e.source_image_count);std::fputs(",\"source_fov_bits_lrud\":",output);array<uint32_t>(e.fov_bits_lrud);
                std::fputs(",\"x_params\":",output);array<uint16_t>({e.x_params.data(),e.x_params_count});
                std::fputs(",\"y_params\":",output);array<uint16_t>({e.y_params.data(),e.y_params_count});
                std::fputs(",\"x_edges\":",output);array<uint32_t>({e.x_edges.data(),r.width+1});
                std::fputs(",\"y_edges\":",output);array<uint32_t>({e.y_edges.data(),r.height+1});std::fputc('}',output);
            }std::fputs("]}\n",output);
        }
        if(!std::ferror(output))std::fprintf(output,"{\"type\":\"footer\",\"records\":%u,\"dropped\":%u,\"failed\":%u,\"complete\":%s}\n",count,dropped,failed,failed||dropped?"false":"true");
        std::fclose(output);output=nullptr;
    }
    ~Trace(){flush();}
};
// Once GPU-path admission begins, any exception/timeout before known producer
// completion invalidates the entire trace, including unsampled frame failures.
class Completion {
    Trace& trace_; bool done_=false;
public:
    explicit Completion(Trace& value) noexcept:trace_(value){}
    Completion(const Completion&)=delete;Completion&operator=(const Completion&)=delete;
    ~Completion(){if(!done_)trace_.failure();}
    void complete() noexcept {done_=true;}
};
inline Trace& trace() {
    auto env=[](const char*name)->std::string_view{const auto p=std::getenv(name);return p?std::string_view(p):std::string_view{};};
    const auto source=env("WIVRN_CODEC_SOURCE_TRACE"),evidence=env("WIVRN_CODEC_TEST_EVIDENCE");
    static Trace value(std::getenv("WIVRN_CODEC_SOURCE_MAPPING_TRACE"),
        env("WIVRN_CODEC_TEST")=="rrb32-q1"&&env("WIVRN_CODEC_TEST_Q")=="1"&&env("WIVRN_CODEC_TEST_COMPRESSION")=="none"&&
        env("WIVRN_CODEC_TEST_QUALITY_CAPTURE")=="1"&&!source.empty()&&source.front()=='/'&&!evidence.empty()&&evidence.front()=='/'&&
        env("WIVRN_CODEC_TEST_FRAME_STRIDE")=="1");
    return value;
}
} // namespace wivrn::codec_test::source_mapping
