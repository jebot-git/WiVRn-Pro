// SPDX-License-Identifier: GPL-3.0-or-later
#ifdef WIVRN_CODEC_TESTS
#include "test_decoder.h"
#include "test_gpu_decoder.h"
#include "staged_rowblocks_image.h"
#include "application.h"
#include "scenes/stream.h"
#include "codec_test/wire.h"
#include "codec_test/rrb/native.h"
#include "codec_test/stereo_admission.h"
#include "codec_test/metadata.h"
#include "codec_test/quality_sampler.h"
#include "codec_test/submitted_capture.h"
#include "codec_test/compression/frame_compression.h"
#include "codec_test/vr444/vulkan_runner.hpp"
#include "codec_test/vr444/direct.hpp"
#include "vk/allocation.h"
#include <android/asset_manager.h>
#include <android/hardware_buffer.h>
#include "android/jnipp.h"
#include <condition_variable>
#include <deque>
#include <fstream>
#include <thread>
#include <spdlog/spdlog.h>
namespace wivrn {
struct test_decode_job {
    codec_test::Header header;
    std::vector<uint8_t> bytes;
    from_headset::feedback feedback;
    to_headset::video_stream_data_shard::view_info_t view;
    std::chrono::steady_clock::time_point enqueued;
};
struct test_publish_eye { std::shared_ptr<decoder::blit_handle> handle; shard_accumulator* accumulator; };
class test_stereo_state {
public:
    codec_test::StereoAdmission<test_decode_job> admission;
    bool enabled=true;
    std::unique_ptr<codec_test::rrb::PairDecoder> rrb_decoder;
    codec_test::rrb::Publication<test_publish_eye> rrb_publication;
    bool rrb_gpu_output=false;
    codec_test::quality_sampler::Mode rgb_filter=codec_test::quality_sampler::Mode::linear;
    uint64_t generation=0;
    static std::string choice(const char* name,std::string fallback){
        std::ifstream file(application::get_config_path()/name);if(!file)return fallback;
        std::string value,extra;if(!(file>>value)||(file>>extra))throw std::runtime_error("invalid RRB config");return value;
    }
    test_stereo_state() {
        static std::atomic<uint64_t> next_generation{0};
        auto previous=next_generation.load();
        do{if(previous==UINT64_MAX)throw std::runtime_error("test generation exhausted");}
        while(!next_generation.compare_exchange_weak(previous,previous+1));
        generation=previous+1;
        std::string policy="paired";
        {std::ifstream choice(application::get_config_path()/"codec-test-pairing.txt");if(choice)choice>>policy;}
        if(policy!="paired"&&policy!="independent")throw std::runtime_error("invalid test stereo pairing policy");
        enabled=policy=="paired";
        auto payload=choice("codec-test-payload.txt","vr444");
        if(payload!="vr444"&&payload!="rrb32")throw std::runtime_error("invalid test payload opt-in");
        const auto filter_token=codec_test::quality_sampler::read(application::get_config_path()/"codec-test-quality-filter.txt");
        rgb_filter=codec_test::quality_sampler::parse(filter_token ? std::optional<std::string_view>(*filter_token) : std::nullopt);
        if(rgb_filter==codec_test::quality_sampler::Mode::nearest){
            const auto capture=codec_test::submitted_capture::load(application::get_config_path()/"codec-test-quality-capture.json");
            codec_test::quality_sampler::client(rgb_filter,capture.enabled,payload,choice("codec-test-compression.txt","none"),enabled);
        }
        if(payload=="rrb32"){
            if(!enabled)throw std::runtime_error("RRB32 requires complete stereo pairing");
            auto count=choice("codec-test-rrb-workers.txt","4");
            if(count!="2"&&count!="4")throw std::runtime_error("RRB32 workers must be2 or4");
            auto output=choice("codec-test-rrb-output.txt","cpu");
            if(output!="cpu"&&output!="staged-gpu")throw std::runtime_error("invalid RRB32 output mode");
            rrb_gpu_output=output=="staged-gpu";
            rrb_decoder=std::make_unique<codec_test::rrb::PairDecoder>(count=="4"?4:2,rrb_gpu_output);
        }
    }
};
std::shared_ptr<test_stereo_state> make_test_stereo_state() { return std::make_shared<test_stereo_state>(); }
namespace {
class test_decoder final:public decoder {
    vk::raii::Device& device;
    vk::raii::PhysicalDevice& physical;
    uint32_t queue_family;
    vk::raii::QueryPool queries=nullptr;
    double timestamp_factor=0;
    uint64_t timestamp_mask=0;
    vk::Extent2D extent;
    uint8_t eye;
    std::weak_ptr<scenes::stream> scene;
    shard_accumulator* accumulator;
    vk::Filter rgb_filter=vk::Filter::eLinear;
    vk::raii::Sampler rgb_sampler;
    vk::raii::CommandPool pool;
    vk::raii::CommandBuffer cmd=nullptr;
    vk::raii::Fence fence;
    struct ImageSlot {
        image_allocation image;
        buffer_allocation staging;
        uint32_t staging_memory_type=UINT32_MAX;
        VkMemoryPropertyFlags staging_memory_flags=0;
        vk::raii::ImageView view=nullptr;
        vk::ImageLayout layout=vk::ImageLayout::eUndefined;
    };
    std::array<std::shared_ptr<ImageSlot>,8> images;
    struct Handle:blit_handle {
        std::shared_ptr<ImageSlot> storage;
        Handle(const from_headset::feedback& fb,const to_headset::video_stream_data_shard::view_info_t& vi,
               vk::Extent2D size,std::shared_ptr<ImageSlot> s,bool sampled_linear):
            blit_handle{fb,vi,*s->view,s->image,size,s->layout},storage(std::move(s)){
            sampled_rgb_is_linear=sampled_linear;
        }
    };
    struct GpuHandle:blit_handle {
        vk::ImageLayout layout=vk::ImageLayout::eShaderReadOnlyOptimal;
        staged_rowblocks::Lease storage;
        GpuHandle(const from_headset::feedback&fb,const to_headset::video_stream_data_shard::view_info_t&vi,vk::Extent2D size,staged_rowblocks::Lease s):
            blit_handle{fb,vi,vk::ImageView(s.view()),vk::Image(s.image()),size,layout},storage(std::move(s)){sampled_rgb_is_linear=true;}
    };
    std::unique_ptr<staged_rowblocks::Pool> rrb_images;
    using Job=test_decode_job;
    std::shared_ptr<test_stereo_state> stereo;
    bool paired() const { return eye<2 && stereo && stereo->enabled; }
    struct PairCompletion {
        test_decoder& owner;
        uint64_t frame;
        bool success=false,rrb=false,arrived=false;
        ~PairCompletion() {
            if(rrb&&!arrived)owner.stereo->rrb_publication.arrive(owner.eye,frame,{});
            if(owner.paired())owner.stereo->admission.finish(owner.eye,frame,success);
        }
    };
    std::vector<uint8_t> assembling;
    uint64_t assembling_frame=UINT64_MAX;
    std::mutex mutex;std::condition_variable cv;std::deque<Job> pending;bool stopping=false;
    std::thread worker;
    jobject java_codec=nullptr;
    jmethodID java_decode=nullptr,java_close=nullptr;
    std::filesystem::path folder,shaders;
    std::ofstream events;
    std::unique_ptr<vr444::GpuSession> reference_gpu;
    std::unique_ptr<test_gpu_decoder> gpu;
    std::vector<uint32_t> cpu_pixels;
    std::string backend="resident";
    std::string cpu_output_mode="cached-copy";
    std::string kernel="baseline";
    bool compression_enabled=false;
    codec_test::compression::Workspace decompression_workspace;
    std::atomic<uint64_t> dropped{0};
    // A failed/timed-out submission may still own cmd, its fence, and staging.
    // Only the worker changes this; teardown reads it after joining the worker.
    bool upload_inflight=false;
    staged_rowblocks::Lease inflight_rrb_readback;
public:
    test_decoder(vk::raii::Device& d,vk::raii::PhysicalDevice& pd,uint32_t family,const to_headset::video_stream_description& desc,uint8_t e,
                 std::weak_ptr<scenes::stream> s,shard_accumulator* a,std::shared_ptr<test_stereo_state> state):device(d),physical(pd),queue_family(family),
        extent{desc.width,uint32_t(desc.height/(e==2?2:1))},eye(e),scene(s),accumulator(a),
        rgb_filter(state && state->rgb_filter==codec_test::quality_sampler::Mode::nearest ? vk::Filter::eNearest : vk::Filter::eLinear),
        rgb_sampler(d,{.magFilter=rgb_filter,.minFilter=rgb_filter,.mipmapMode=vk::SamplerMipmapMode::eNearest,
            .addressModeU=vk::SamplerAddressMode::eClampToEdge,.addressModeV=vk::SamplerAddressMode::eClampToEdge,.addressModeW=vk::SamplerAddressMode::eClampToEdge}),
        pool(d,{.flags=vk::CommandPoolCreateFlagBits::eResetCommandBuffer,.queueFamilyIndex=family}),
        fence(d,{.flags=vk::FenceCreateFlagBits::eSignaled}) {
        stereo=std::move(state);
        if(stereo&&stereo->rrb_decoder)rowfilter::pixels(extent.width,extent.height);
        folder=application::get_config_path()/"codec-test";
        // Each decoder starts its worker before the next eye is constructed.
        // Separate paths prevent another eye truncating a shader being loaded.
        shaders=folder/("shaders-"+std::to_string(eye));
        std::filesystem::create_directories(shaders);
        for(const char* file:{"encode.spv","decode.spv","encode-coop64.spv","decode-coop64.spv","encode-coop128.spv","decode-coop128.spv","decode-coop128dc.spv"}){
            auto asset=AAssetManager_open(application::asset_manager(),("codec-test/"+std::string(file)).c_str(),AASSET_MODE_BUFFER);
            if(!asset)throw std::runtime_error("missing codec test shader asset");
            std::ofstream(shaders/file,std::ios::binary).write(static_cast<const char*>(AAsset_getBuffer(asset)),AAsset_getLength(asset));
            AAsset_close(asset);
        }
        cmd=std::move(device.allocateCommandBuffers({.commandPool=*pool,.level=vk::CommandBufferLevel::ePrimary,.commandBufferCount=1})[0]);
        for(auto& item:images){
            item=std::make_shared<ImageSlot>();
            item->image=image_allocation(device,{.imageType=vk::ImageType::e2D,.format=vk::Format::eR8G8B8A8Srgb,
                .extent={extent.width,extent.height,1},.mipLevels=1,.arrayLayers=1,.samples=vk::SampleCountFlagBits::e1,
                .tiling=vk::ImageTiling::eOptimal,.usage=vk::ImageUsageFlagBits::eTransferDst|vk::ImageUsageFlagBits::eSampled},
                {.usage=VMA_MEMORY_USAGE_AUTO});
            item->view=vk::raii::ImageView(device,{.image=item->image,.viewType=vk::ImageViewType::e2D,.format=vk::Format::eR8G8B8A8Srgb,
                .subresourceRange={.aspectMask=vk::ImageAspectFlagBits::eColor,.levelCount=1,.layerCount=1}});
            item->staging=buffer_allocation(device,{.size=uint64_t(extent.width)*extent.height*4,.usage=vk::BufferUsageFlagBits::eTransferSrc|vk::BufferUsageFlagBits::eTransferDst},
                {.requiredFlags=VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT});item->staging.map();
            VmaAllocationInfo allocation_info{};
            vmaGetAllocationInfo(vk_allocator::instance(),static_cast<VmaAllocation>(item->staging),&allocation_info);
            item->staging_memory_type=allocation_info.memoryType;
            item->staging_memory_flags=item->staging.properties();
            constexpr VkMemoryPropertyFlags required=VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
            if((item->staging_memory_flags&required)!=required)
                throw std::runtime_error("codec test staging must be host-visible and coherent");
        }
        {std::ifstream choice(application::get_config_path()/"codec-test-kernel.txt");if(choice)choice>>kernel;}
        {
            std::string mode="none";
            std::ifstream choice(application::get_config_path()/"codec-test-compression.txt");if(choice)choice>>mode;
            if(mode!="none" && mode!="lz4")throw std::runtime_error("invalid test compression policy");
            compression_enabled=mode=="lz4";
        }
        {std::ifstream choice(application::get_config_path()/"codec-test-backend.txt");if(choice)choice>>backend;}
        if(backend!="resident"&&backend!="readback"&&backend!="cpu-neon")throw std::runtime_error("invalid decoder backend");
        {
            std::ifstream choice(application::get_config_path()/"codec-test-cpu-output.txt");
            if(choice){
                std::string extra;
                if(!(choice>>cpu_output_mode)||(choice>>extra))throw std::runtime_error("invalid CPU output configuration");
            }
        }
        if(cpu_output_mode!="cached-copy"&&cpu_output_mode!="mapped-direct")throw std::runtime_error("invalid CPU output mode");
        if(cpu_output_mode=="mapped-direct"&&backend!="cpu-neon")throw std::runtime_error("mapped-direct requires cpu-neon");
        if(stereo&&stereo->rrb_decoder&&(backend!="cpu-neon"||cpu_output_mode!="cached-copy"))
            throw std::runtime_error("RRB32 CPU requires cpu-neon cached-copy");
        if(backend=="cpu-neon") {
            if(kernel!="direct-neon" || std::string_view(direct::vector_backend())!="aarch64-neon")
                throw std::runtime_error("cpu-neon requires the direct-neon ARM backend");
        } else if(kernel!="baseline"&&kernel!="coop64"&&kernel!="coop128"&&kernel!="coop128dc")
            throw std::runtime_error("invalid test GPU kernel");
        auto queue_props=physical.getQueueFamilyProperties();
        auto bits=queue_props.at(family).timestampValidBits;
        if(!bits)throw std::runtime_error("decode timestamps unavailable");
        timestamp_mask=bits==64?UINT64_MAX:((uint64_t(1)<<bits)-1);
        timestamp_factor=physical.getProperties().limits.timestampPeriod/1e6;
        queries=vk::raii::QueryPool(device,{.queryType=vk::QueryType::eTimestamp,.queryCount=2});
        events.open(folder/("decode-"+std::to_string(eye)+".jsonl"));
        worker=std::thread([this]{work();});
    }
    ~test_decoder() override {
        // Stop is shared and nonblocking: retiring either eye wakes both
        // workers before their sequential destructors join, avoiding teardown
        // waiting forever for a missing peer or an incomplete pending pair.
        if(paired())stereo->admission.stop();
        if(stereo&&stereo->rrb_decoder)stereo->rrb_decoder->stop();
        {std::lock_guard lock(mutex);stopping=true;}cv.notify_all();if(worker.joinable())worker.join();
        if(upload_inflight){
            try{application::get_queue().lock()->waitIdle();}
            catch(const vk::DeviceLostError& e){spdlog::error("codec test upload teardown after device loss: {}",e.what());}
            catch(const std::exception& e){
                // Without completion or device loss, freeing resources might race
                // pending GPU work. Fail the dedicated test process closed.
                spdlog::critical("codec test upload completion unknown: {}",e.what());
                std::terminate();
            }
        }
        events<<"{\"type\":\"summary\",\"queue_dropped\":"<<dropped.load();
        write_pair_stats();events<<"}\n";
    }
    vk::Sampler sampler() override{return *rgb_sampler;}
    // The SRGB image view already applies the EOTF before texture filtering.
    bool sampled_rgb_is_linear() const override{return true;}
    void push_data(std::span<std::span<const uint8_t>> data,uint64_t frame,bool) override {
        if(frame!=assembling_frame){assembling.clear();assembling_frame=frame;}
        for(auto part:data){if(assembling.size()+part.size()>codec_test::header_size+codec_test::max_payload)throw std::runtime_error("test frame bound");assembling.insert(assembling.end(),part.begin(),part.end());}
    }
    void frame_completed(const from_headset::feedback& fb,const to_headset::video_stream_data_shard::view_info_t& vi) override {
        auto h=codec_test::unpack(assembling);
        if(rgb_filter==vk::Filter::eNearest && (h.kind!=codec_test::Kind::rrb32 || h.flags))
            throw std::runtime_error("quality nearest accepts only checked unwrapped RRB pairs");
        if(h.kind==codec_test::Kind::rrb32&&(!paired()||!stereo->rrb_decoder||h.flags||assembling.size()>codec_test::rrb::stereo_wire_cap-codec_test::header_size))
            throw std::runtime_error("RRB32 payload requires bounded paired opt-in");
        if(h.flags && !compression_enabled)throw std::runtime_error("compressed test payload not opted in");
        if(h.frame!=fb.frame_index||h.eye!=eye||h.width!=extent.width||h.height!=extent.height||h.warp!=codec_test::warp_hash(vi))
            throw std::runtime_error("codec payload/frame/warp identity mismatch");
        Job job{h,std::move(assembling),fb,vi,std::chrono::steady_clock::now()};
        if(paired()){
            if(!stereo->admission.submit(h,std::move(job)))++dropped;
        }else{
            std::lock_guard lock(mutex);
            if(!pending.empty()){dropped.fetch_add(pending.size());pending.clear();}
            pending.push_back(std::move(job));cv.notify_one();
        }
    }
private:
    void write_pair_stats() {
        events<<",\"pairing\":\""<<(paired()?"paired":"independent")<<"\"";
        if(!paired())return;
        auto stats=stereo->admission.snapshot();
        events<<",\"pair_admitted\":"<<stats.admitted_pairs
              <<",\"pair_completed\":"<<stats.completed_pairs
              <<",\"pair_failed\":"<<stats.failed_pairs
              <<",\"pair_dropped_eyes\":"<<stats.dropped_eyes
              <<",\"pair_stale_eyes\":"<<stats.stale_eyes
              <<",\"pair_duplicate_eyes\":"<<stats.duplicate_eyes
              <<",\"pair_metadata_mismatch\":"<<stats.mismatched_pairs
              <<",\"pair_abandoned\":"<<stats.abandoned_pairs
              <<",\"pair_pending_dropped_pairs\":"<<stats.pending_dropped_pairs
              <<",\"pair_orphan_dropped_eyes\":"<<stats.orphan_dropped_eyes
              <<",\"pair_pending_frames\":"<<stats.pending_frames;
    }
    std::vector<uint32_t> hevc(std::span<const uint8_t> bytes,uint64_t index) {
        auto& env=jni::jni_thread::env();
        if(!java_codec){
            auto activity=application::native_app()->activity->clazz;
            auto cls=env.GetObjectClass(activity);
            auto factory=env.GetMethodID(cls,"createCodecTestDecoder","(II)Ljava/lang/Object;");
            jni::details::handle_java_exception();
            auto local=env.CallObjectMethod(activity,factory,int(extent.width),int(extent.height));
            env.DeleteLocalRef(cls);jni::details::handle_java_exception();
            java_codec=env.NewGlobalRef(local);env.DeleteLocalRef(local);
            cls=env.GetObjectClass(java_codec);
            java_decode=env.GetMethodID(cls,"decode","([BJ)[B");java_close=env.GetMethodID(cls,"close","()V");
            env.DeleteLocalRef(cls);jni::details::handle_java_exception();
        }
        auto input=env.NewByteArray(bytes.size());jni::details::handle_java_exception();
        env.SetByteArrayRegion(input,0,bytes.size(),reinterpret_cast<const jbyte*>(bytes.data()));
        auto output=static_cast<jbyteArray>(env.CallObjectMethod(java_codec,java_decode,input,jlong(index)));
        env.DeleteLocalRef(input);jni::details::handle_java_exception();
        auto length=env.GetArrayLength(output);
        if(length!=int(size_t(extent.width)*extent.height*4)){env.DeleteLocalRef(output);throw std::runtime_error("raw luma output size");}
        std::vector<uint32_t> pixels(size_t(extent.width)*extent.height);
        env.GetByteArrayRegion(output,0,length,reinterpret_cast<jbyte*>(pixels.data()));env.DeleteLocalRef(output);
        jni::details::handle_java_exception();return pixels;
    }
    void work_rrb_gpu(const Job&job,const codec_test::rrb::Pair&pair,PairCompletion&completion,
                      std::chrono::steady_clock::time_point began,double queue_ms){
        auto h=job.header;
        if(!rrb_images){
            std::unique_ptr<AAsset,decltype(&AAsset_close)> asset(AAssetManager_open(application::asset_manager(),"codec-test/staged-rowblocks-image.spv",AASSET_MODE_BUFFER),AAsset_close);
            if(!asset)throw std::runtime_error("missing staged RRB shader");
            auto length=AAsset_getLength(asset.get());
            if(length<20||length>1024*1024||length%4)throw std::runtime_error("invalid staged RRB shader bytes");
            const void* asset_bytes=AAsset_getBuffer(asset.get());
            if(!asset_bytes)throw std::runtime_error("unavailable staged RRB shader buffer");
            std::vector<uint32_t> code(size_t(length)/4);std::memcpy(code.data(),asset_bytes,length);
            rrb_images=std::make_unique<staged_rowblocks::Pool>(VkPhysicalDevice(*physical),VkDevice(*device),queue_family,
                extent.width,extent.height,stereo->generation,eye,code);
        }
        std::vector<staged_rowblocks::Block> blocks;blocks.reserve(pair.index[eye].blocks.size());
        for(const auto&b:pair.index[eye].blocks){
            if(b.raw_offset>UINT32_MAX)throw std::runtime_error("RRB GPU offset bound");
            blocks.push_back({b.row,b.height,b.mode,uint32_t(b.raw_offset)});
        }
        staged_rowblocks::Input input{pair.width,pair.height,pair.residual[eye].rgb,blocks};
        auto prepare_start=std::chrono::steady_clock::now();
        auto pending_image=rrb_images->prepare(h.frame,input);
        if(!pending_image){++dropped;return;}
        const double staging_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-prepare_start).count();
        auto submit_start=std::chrono::steady_clock::now();
        {auto queue=application::get_queue().lock();pending_image->submit(VkQueue(**queue));}
        auto lease=pending_image->publish(1'000'000'000);
        const double submit_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-submit_start).count();
        auto id=lease.identity();
        if(id.generation!=stereo->generation||id.frame!=h.frame||id.eye!=eye)throw std::runtime_error("RRB image lease identity");
        // Diagnostic captures read the actual produced image in this private lease.
        // The same application queue and owned fence order/retire the copy, and its
        // shader-read layout is restored before any renderer can acquire a handle.
        const bool capture=h.frame%90==0;double snapshot_ms=0;
        if(capture){
            auto start=std::chrono::steady_clock::now();std::shared_ptr<ImageSlot> scratch;
            for(auto&item:images)if(item.use_count()==1){scratch=item;break;}
            if(!scratch){++dropped;return;}
            cmd.reset();cmd.begin({.flags=vk::CommandBufferUsageFlagBits::eOneTimeSubmit});
            vk::ImageMemoryBarrier b{.srcAccessMask=vk::AccessFlagBits::eShaderWrite|vk::AccessFlagBits::eShaderRead,.dstAccessMask=vk::AccessFlagBits::eTransferRead,
                .oldLayout=vk::ImageLayout::eShaderReadOnlyOptimal,.newLayout=vk::ImageLayout::eTransferSrcOptimal,
                .srcQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,.image=vk::Image(lease.image()),
                .subresourceRange={.aspectMask=vk::ImageAspectFlagBits::eColor,.levelCount=1,.layerCount=1}};
            cmd.pipelineBarrier(vk::PipelineStageFlagBits::eAllCommands,vk::PipelineStageFlagBits::eTransfer,{},{},{},b);
            cmd.copyImageToBuffer(vk::Image(lease.image()),vk::ImageLayout::eTransferSrcOptimal,scratch->staging,vk::BufferImageCopy{
                .imageSubresource={.aspectMask=vk::ImageAspectFlagBits::eColor,.layerCount=1},.imageExtent={extent.width,extent.height,1}});
            b.srcAccessMask=vk::AccessFlagBits::eTransferRead;b.dstAccessMask=vk::AccessFlagBits::eShaderRead;
            b.oldLayout=vk::ImageLayout::eTransferSrcOptimal;b.newLayout=vk::ImageLayout::eShaderReadOnlyOptimal;
            vk::MemoryBarrier host{.srcAccessMask=vk::AccessFlagBits::eTransferWrite,.dstAccessMask=vk::AccessFlagBits::eHostRead};
            cmd.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer,vk::PipelineStageFlagBits::eAllCommands|vk::PipelineStageFlagBits::eHost,{},host,{},b);cmd.end();
            inflight_rrb_readback=lease;
            device.resetFences(*fence);vk::CommandBuffer command=*cmd;upload_inflight=true;
            application::get_queue().lock()->submit(vk::SubmitInfo{.commandBufferCount=1,.pCommandBuffers=&command},*fence);
            if(device.waitForFences(*fence,true,1'000'000'000)!=vk::Result::eSuccess){
                // The member lease survives stack unwinding. Native teardown drains
                // the queue before destroying it, exactly like private upload storage.
                throw std::runtime_error("RRB diagnostic readback timeout");
            }
            upload_inflight=false;inflight_rrb_readback={};
            std::ofstream(folder/(std::to_string(h.frame)+"-"+std::to_string(eye)+".rgba"),std::ios::binary).write(
                reinterpret_cast<const char*>(scratch->staging.data<uint8_t>()),size_t(extent.width)*extent.height*4);
            snapshot_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
        }
        events<<"{\"frame\":"<<h.frame<<",\"eye\":"<<unsigned(eye)<<",\"warp\":"<<h.warp
              <<",\"rgb_sampler_filter\":\""<<(rgb_filter==vk::Filter::eNearest?"NEAREST":"LINEAR")<<"\",\"effective_codec\":\"rrb32\",\"rrb_output\":\"staged-gpu\",\"rrb_workers\":0,\"configured_rrb_workers\":"<<stereo->rrb_decoder->workers()
              <<",\"rrb_generation\":"<<stereo->generation<<",\"rrb_block_rows\":32,\"stereo_envelope_cap\":262144,\"decoder_backend\":\"cpu-neon\",\"decoder_kernel\":\"direct-neon\""
              <<",\"compression_policy\":\"none\",\"compression_applied\":false,\"bytes\":"<<job.bytes.size()
              <<",\"decoded_payload_bytes\":"<<job.bytes.size()-codec_test::header_size<<",\"decompression_ms\":0"
              <<",\"pair_decode_ms\":"<<pair.decode_ms<<",\"pair_decode_scope\":\"stereo_once_reported_per_eye\",\"rrb_vector_backend\":\"not_used_residual_unpack\",\"cpu_decode_ms\":0"
              <<",\"gpu_timing_valid\":false,\"decode_gpu_ms\":0,\"cpu_vector_backend\":\"not_used_residual_unpack\",\"cpu_output_mode\":\"not_used\""
              <<",\"staging_copy_ms\":0,\"staging_copy_timing_valid\":false,\"staging_prepare_ms\":"<<staging_ms<<",\"submit_fence_wait_ms\":"<<submit_ms<<",\"queue_wait_ms\":"<<queue_ms
              <<",\"capture_source\":\""<<(capture?"rrb_gpu_image_readback":"not_captured")<<"\",\"capture_snapshot_ms\":"<<snapshot_ms
              <<",\"queue_dropped_so_far\":"<<dropped.load()<<",\"decode_upload_ms\":"<<std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-began).count();write_pair_stats();events<<"}\n";events.flush();
        if(auto target=scene.lock()){
            auto handle=std::make_shared<GpuHandle>(job.feedback,job.view,extent,std::move(lease));
            handle->codec_test_sampler_bound=true;handle->codec_test_rgb_filter=rgb_filter;handle->codec_test_rgb_sampler=*rgb_sampler;
            auto ready=stereo->rrb_publication.arrive(eye,h.frame,std::make_shared<test_publish_eye>(test_publish_eye{handle,accumulator}));
            completion.arrived=true;completion.success=true;
            if(ready)completion.success=target->push_test_blit_pair({(*ready)[0]->accumulator,(*ready)[1]->accumulator},{(*ready)[0]->handle,(*ready)[1]->handle});
        }
    }
    void work(){
        jni::jni_thread::setup_thread(application::native_app()->activity->vm);
        while(true){
            Job job;
            if(paired()){
                auto next=stereo->admission.wait_take(eye);if(!next)break;job=std::move(*next);
            }else{
                std::unique_lock lock(mutex);cv.wait(lock,[this]{return stopping||!pending.empty();});if(stopping)break;job=std::move(pending.front());pending.pop_front();
            }
            PairCompletion completion{*this,job.header.frame,false,job.header.kind==codec_test::Kind::rrb32};
            try{
                auto h=job.header;auto body=std::span<const uint8_t>(job.bytes).subspan(codec_test::header_size);
                auto began=std::chrono::steady_clock::now();
                auto queue_ms=std::chrono::duration<double,std::milli>(began-job.enqueued).count();
                std::shared_ptr<const codec_test::rrb::Pair> rrb_pair;
                if(h.kind==codec_test::Kind::rrb32)rrb_pair=stereo->rrb_decoder->acquire(h,body);
                if(rrb_pair&&stereo->rrb_gpu_output){work_rrb_gpu(job,*rrb_pair,completion,began,queue_ms);continue;}
                std::shared_ptr<ImageSlot> image;
                for(auto& item:images)if(item.use_count()==1){image=item;break;}
                if(!image){++dropped;continue;}
                auto decompress_start=std::chrono::steady_clock::now();
                if(h.flags & codec_test::flag_lz4)body=codec_test::compression::decode(body,decompression_workspace);
                double decompress_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-decompress_start).count();
                std::vector<uint32_t> pixels;double gpu_ms=0, cpu_ms=0, staging_copy_ms=0, capture_snapshot_ms=0;
                const bool resident=h.kind==codec_test::Kind::vr444 && backend=="resident";
                const bool cpu_direct=h.kind==codec_test::Kind::vr444&&backend=="cpu-neon";
                const bool cpu_mapped=cpu_direct&&cpu_output_mode=="mapped-direct";
                if(backend=="cpu-neon" && h.kind!=codec_test::Kind::vr444 && h.kind!=codec_test::Kind::rrb32)throw std::runtime_error("CPU direct backend requires VR444");
                const bool capture=h.frame%90==0;
                vr444::Frame decoded_frame{};
                if(rrb_pair){
                    cpu_ms=rrb_pair->decode_ms;
                }else if(cpu_direct) {
                    auto cpu_start=std::chrono::steady_clock::now();
                    auto shape=direct::preflight(body);
                    if(shape.width!=extent.width||shape.height!=extent.height)throw std::runtime_error("direct VR444 dimensions mismatch");
                    const size_t pixel_count=size_t(extent.width)*extent.height;
                    if(cpu_mapped){
                        // This slot has no published handle, and the previous upload
                        // completed before this worker can start another job. A parse
                        // error may change private staging bytes but cannot submit or
                        // publish them. Coherent host writes precede queue submission.
                        direct::decode(body,shape,std::span(image->staging.data<uint32_t>(),pixel_count),true);
                    }else{
                        cpu_pixels.resize(pixel_count);
                        // Private cached output is never uploaded or published after a parse error.
                        direct::decode(body,shape,cpu_pixels,true);
                    }
                    cpu_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-cpu_start).count();
                }else if(h.kind==codec_test::Kind::vr444){
                    auto frame=vr444::unpack(std::vector<uint8_t>(body.begin(),body.end()));
                    if(frame.width!=extent.width||frame.height!=extent.height)throw std::runtime_error("vr444 dimensions mismatch");
                    if(resident){
                        if(!gpu)gpu=std::make_unique<test_gpu_decoder>(VkPhysicalDevice(*physical),VkDevice(*device),queue_family,extent.width,extent.height,shaders.string(),kernel);
                        decoded_frame=std::move(frame);
                    }else{
                        if(!reference_gpu)reference_gpu=std::make_unique<vr444::GpuSession>(extent.width,extent.height,shaders.string(),kernel);
                        auto result=reference_gpu->decode(frame);gpu_ms=result.decode_ms;
                        if(result.software)throw std::runtime_error("software Vulkan forbidden");pixels=std::move(result.rgba);
                    }
                }else if(h.kind==codec_test::Kind::raw_rgba){
                    if(body.size()!=size_t(extent.width)*extent.height*4)throw std::runtime_error("raw RGBA length");
                    pixels.resize(size_t(extent.width)*extent.height);std::memcpy(pixels.data(),body.data(),body.size());
                }else pixels=hevc(body,h.frame);
                if(!resident&&!cpu_mapped){
                    auto copy_start=std::chrono::steady_clock::now();
                    const auto& upload_pixels=rrb_pair?rrb_pair->cpu.rgba[eye]:cpu_direct?cpu_pixels:pixels;
                    std::memcpy(image->staging.data<uint8_t>(),upload_pixels.data(),upload_pixels.size()*4);
                    staging_copy_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-copy_start).count();
                }
                cmd.reset();cmd.begin({.flags=vk::CommandBufferUsageFlagBits::eOneTimeSubmit});
                vk::Buffer source=image->staging;
                if(resident){
                    cmd.resetQueryPool(*queries,0,2);
                    cmd.writeTimestamp(vk::PipelineStageFlagBits::eTopOfPipe,*queries,0);
                    gpu->record_decode(VkCommandBuffer(*cmd),decoded_frame);
                    cmd.writeTimestamp(vk::PipelineStageFlagBits::eBottomOfPipe,*queries,1);
                    source=gpu->output();
                    if(capture){
                        cmd.copyBuffer(source,image->staging,vk::BufferCopy{.size=uint64_t(extent.width)*extent.height*4});
                        vk::MemoryBarrier readback{.srcAccessMask=vk::AccessFlagBits::eTransferWrite,.dstAccessMask=vk::AccessFlagBits::eHostRead};
                        cmd.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer,vk::PipelineStageFlagBits::eHost,{},{readback},{},{});
                    }
                }
                vk::ImageMemoryBarrier barrier{.srcAccessMask=vk::AccessFlagBits::eMemoryRead,.dstAccessMask=vk::AccessFlagBits::eTransferWrite,
                    .oldLayout=image->layout,.newLayout=vk::ImageLayout::eTransferDstOptimal,.image=image->image,
                    .subresourceRange={.aspectMask=vk::ImageAspectFlagBits::eColor,.levelCount=1,.layerCount=1}};
                cmd.pipelineBarrier(vk::PipelineStageFlagBits::eAllCommands,vk::PipelineStageFlagBits::eTransfer,{},{},{},barrier);
                cmd.copyBufferToImage(source,image->image,vk::ImageLayout::eTransferDstOptimal,vk::BufferImageCopy{
                    .imageSubresource={.aspectMask=vk::ImageAspectFlagBits::eColor,.layerCount=1},.imageExtent={extent.width,extent.height,1}});
                barrier.srcAccessMask=vk::AccessFlagBits::eTransferWrite;barrier.dstAccessMask=vk::AccessFlagBits::eShaderRead;
                barrier.oldLayout=vk::ImageLayout::eTransferDstOptimal;barrier.newLayout=vk::ImageLayout::eGeneral;
                cmd.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer,vk::PipelineStageFlagBits::eAllCommands,{},{},{},barrier);cmd.end();
                device.resetFences(*fence);vk::CommandBuffer command=*cmd;
                upload_inflight=true;
                auto submit_start=std::chrono::steady_clock::now();
                application::get_queue().lock()->submit(vk::SubmitInfo{.commandBufferCount=1,.pCommandBuffers=&command},*fence);
                if(device.waitForFences(*fence,true,1'000'000'000)!=vk::Result::eSuccess)throw std::runtime_error("image upload timeout");
                double submit_wait_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-submit_start).count();
                upload_inflight=false;
                image->layout=vk::ImageLayout::eGeneral;
                if(resident){
                    uint64_t times[2];
                    if(vkGetQueryPoolResults(VkDevice(*device),VkQueryPool(*queries),0,2,sizeof(times),times,sizeof(uint64_t),VK_QUERY_RESULT_64_BIT)!=VK_SUCCESS)throw std::runtime_error("decode timestamp read failed");
                    gpu_ms=double((times[1]-times[0])&timestamp_mask)*timestamp_factor;
                    if(capture){
                        auto snapshot_start=std::chrono::steady_clock::now();
                        pixels.resize(size_t(extent.width)*extent.height);
                        std::memcpy(pixels.data(),image->staging.data<uint8_t>(),pixels.size()*4);
                        capture_snapshot_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-snapshot_start).count();
                    }
                }
                if(cpu_mapped&&capture){
                    auto snapshot_start=std::chrono::steady_clock::now();
                    // Preserve the exact CPU-produced bytes in owned storage before
                    // publication or slot reuse. This is a CPU snapshot, not an image
                    // readback or evidence of the pixels displayed by the compositor.
                    pixels.resize(size_t(extent.width)*extent.height);
                    std::memcpy(pixels.data(),image->staging.data<uint8_t>(),pixels.size()*4);
                    capture_snapshot_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-snapshot_start).count();
                }
                const char* capture_source=!capture?"not_captured":cpu_mapped?"cpu_mapped_staging_snapshot":rrb_pair?"rrb_pair_cpu_output":cpu_direct?"cpu_cached_output":resident?"gpu_output_readback":"decoded_cpu_output";
                events<<"{\"frame\":"<<h.frame<<",\"eye\":"<<unsigned(eye)<<",\"warp\":"<<h.warp
                      <<",\"decoder_kernel\":\""<<kernel<<"\",\"decoder_backend\":\""<<backend<<"\",\"compression_policy\":\""<<(compression_enabled?"lz4":"none")
                      <<"\",\"bytes\":"<<job.bytes.size()<<",\"compression_applied\":"<<((h.flags & codec_test::flag_lz4)?"true":"false")
                      <<",\"decoded_payload_bytes\":"<<body.size()<<",\"decompression_ms\":"<<decompress_ms<<",\"decode_gpu_ms\":"<<gpu_ms
                      <<",\"gpu_timing_valid\":"<<((h.kind==codec_test::Kind::vr444&&!cpu_direct)?"true":"false")
                      <<",\"rgb_sampler_filter\":\""<<(rgb_filter==vk::Filter::eNearest?"NEAREST":"LINEAR")<<"\",\"effective_codec\":\""<<(rrb_pair?"rrb32":h.kind==codec_test::Kind::vr444?"vr444":"legacy")
                      <<"\",\"rrb_output\":\""<<(rrb_pair?"cpu":"not_used")
                      <<"\",\"rrb_workers\":"<<(rrb_pair?stereo->rrb_decoder->workers():0)
                      <<",\"configured_rrb_workers\":"<<(rrb_pair?stereo->rrb_decoder->workers():0)
                      <<",\"rrb_generation\":"<<(rrb_pair?stereo->generation:0)
                      <<",\"rrb_block_rows\":"<<(rrb_pair?32:0)
                      <<",\"stereo_envelope_cap\":"<<(rrb_pair?262144:0)
                      <<",\"pair_decode_ms\":"<<(rrb_pair?rrb_pair->decode_ms:0)
                      <<",\"pair_decode_scope\":\""<<(rrb_pair?"stereo_once_reported_per_eye":"not_used")
                      <<"\",\"rrb_vector_backend\":\""<<(rrb_pair?codec_test::rrb::worker_vector_backend():"not_used")
                      <<"\",\"cpu_decode_ms\":"<<(rrb_pair?0:cpu_ms)<<",\"cpu_vector_backend\":\""<<(cpu_direct?direct::vector_backend():"not_used")
                      <<"\",\"cpu_decode_implementation\":\""<<(rrb_pair?"rrb32-rowblocks-workers":cpu_direct?"vr444-direct":"legacy")
                      <<"\",\"cpu_output_mode\":\""<<(cpu_direct?cpu_output_mode:"not_used")<<"\",\"staging_memory_type\":"<<image->staging_memory_type
                      <<",\"staging_memory_flags\":"<<image->staging_memory_flags<<",\"staging_copy_ms\":"<<staging_copy_ms
                      <<",\"capture_source\":\""<<capture_source<<"\",\"capture_snapshot_ms\":"<<capture_snapshot_ms
                      <<",\"submit_fence_wait_ms\":"<<submit_wait_ms<<",\"queue_wait_ms\":"<<queue_ms<<",\"queue_dropped_so_far\":"<<dropped.load()
                      <<",\"decode_upload_ms\":"<<std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-began).count();write_pair_stats();events<<"}\n";events.flush();
                if(capture){const auto& captured_pixels=rrb_pair?rrb_pair->cpu.rgba[eye]:cpu_direct&&!cpu_mapped?cpu_pixels:pixels;std::ofstream(folder/(std::to_string(h.frame)+"-"+std::to_string(eye)+".rgba"),std::ios::binary).write(reinterpret_cast<const char*>(captured_pixels.data()),captured_pixels.size()*4);}
                if(auto target=scene.lock()){
                    auto handle=std::make_shared<Handle>(job.feedback,job.view,extent,image,sampled_rgb_is_linear());
            handle->codec_test_sampler_bound=true;handle->codec_test_rgb_filter=rgb_filter;handle->codec_test_rgb_sampler=*rgb_sampler;
                    if(rrb_pair){
                        auto ready=stereo->rrb_publication.arrive(eye,h.frame,std::make_shared<test_publish_eye>(test_publish_eye{handle,accumulator}));
                        completion.arrived=true;completion.success=true;
                        if(ready)completion.success=target->push_test_blit_pair({(*ready)[0]->accumulator,(*ready)[1]->accumulator},{(*ready)[0]->handle,(*ready)[1]->handle});
                    }else{target->push_blit_handle(accumulator,std::move(handle));completion.success=true;}
                }
            }catch(const std::exception& e){
                ++dropped;spdlog::error("codec test decode: {}",e.what());
                events<<"{\"frame\":"<<job.feedback.frame_index<<",\"failed\":true}\n";events.flush();
                // Do not reset or overwrite resources while an upload might be pending.
                // Teardown drains the queue before these resources are destroyed.
                if(upload_inflight){if(paired())stereo->admission.stop();break;}
            }
        }
        if(java_codec){auto& env=jni::jni_thread::env();env.CallVoidMethod(java_codec,java_close);if(env.ExceptionCheck())env.ExceptionClear();env.DeleteGlobalRef(java_codec);java_codec=nullptr;}
    }
};
}
std::shared_ptr<decoder> make_test_decoder(vk::raii::Device& device,vk::raii::PhysicalDevice& physical,uint32_t family,
    const to_headset::video_stream_description& desc,uint8_t eye,std::weak_ptr<scenes::stream> scene,shard_accumulator* acc){
    auto target=scene.lock();
    auto state=target?target->codec_test_stereo_state():nullptr;
    if(eye<2 && !state)throw std::runtime_error("test stereo generation missing");
    return std::make_shared<test_decoder>(device,physical,family,desc,eye,scene,acc,std::move(state));
}
}
#endif
