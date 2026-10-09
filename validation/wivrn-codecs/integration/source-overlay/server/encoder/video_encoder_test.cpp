// SPDX-License-Identifier: GPL-3.0-or-later
#include "video_encoder_test.h"
#include "encoder_settings.h"
#include "utils/wivrn_vk_bundle.h"
#include "vk/allocation.h"
#include "codec_test/wire.h"
#include "codec_test/metadata.h"
#include "codec_test/compression/frame_compression.h"
#include "codec_test/vr444/vulkan_runner.hpp"
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <filesystem>
extern "C" {
#include <libavcodec/avcodec.h>
#include <libavutil/hwcontext.h>
#include <libavutil/opt.h>
}
namespace wivrn {
namespace {
class intra_idr : public idr_handler {
public:void on_feedback(const from_headset::feedback&) override {} void reset() override {} bool should_skip(uint64_t) override{return false;}
};
void avcheck(int r,const char* message){if(r<0)throw std::runtime_error(std::string(message)+": "+std::to_string(r));}
class test_encoder final : public video_encoder {
    vk_bundle& vk;
    codec_test::Kind kind;
    vk::raii::CommandPool pool;
    struct Slot { buffer_allocation buffer; vk::raii::CommandBuffer cmd=nullptr; vk::raii::Fence fence=nullptr; };
    std::array<Slot,num_slots> slots;
    AVCodecContext* codec=nullptr;
    AVBufferRef* va=nullptr;
    AVBufferRef* qsv=nullptr;
    AVFrame* frame=nullptr;
    std::string shaders;
    std::filesystem::path evidence;
    unsigned quantizer=8;
    unsigned frame_stride=12;
    bool compression_enabled=false;
    codec_test::compression::Workspace compression_workspace;
    std::ofstream events;
    std::unique_ptr<vr444::GpuSession> gpu;
public:
    test_encoder(vk_bundle& v,const encoder_settings& s,uint8_t eye):
        video_encoder(v,eye,v.queue.family_index,s,std::make_unique<intra_idr>(),true),vk(v),
        kind(std::string_view(std::getenv("WIVRN_CODEC_TEST"))=="hevc-carrier"?codec_test::Kind::hevc_carrier:(std::string_view(std::getenv("WIVRN_CODEC_TEST"))=="raw-rgba"?codec_test::Kind::raw_rgba:codec_test::Kind::vr444)),
        pool(v.device,{.flags=vk::CommandPoolCreateFlagBits::eResetCommandBuffer,.queueFamilyIndex=v.queue.family_index}) {
        if(const char* p=std::getenv("WIVRN_CODEC_TEST_SHADERS")) shaders=p;
        if(const char* p=std::getenv("WIVRN_CODEC_TEST_EVIDENCE")){evidence=p;std::filesystem::create_directories(evidence);}
        if(!evidence.empty()) events.open(evidence/("encode-"+std::to_string(eye)+".jsonl"));
        if(const char* p=std::getenv("WIVRN_CODEC_TEST_Q"))quantizer=std::stoul(p);
        if(const char* p=std::getenv("WIVRN_CODEC_TEST_FRAME_STRIDE"))frame_stride=std::stoul(p);
        if(frame_stride<1||frame_stride>90)throw std::runtime_error("invalid frame stride");
        if(quantizer<1||quantizer>1024)throw std::runtime_error("invalid test quantizer");
        if(const char* p=std::getenv("WIVRN_CODEC_TEST_COMPRESSION")){
            if(std::string_view(p)!="none" && std::string_view(p)!="lz4")throw std::runtime_error("invalid test compression");
            compression_enabled=std::string_view(p)=="lz4";
        }
        if(compression_enabled && kind!=codec_test::Kind::vr444)throw std::runtime_error("LZ4 wrapper requires VR444 test mode");
        auto commands=v.device.allocateCommandBuffers({.commandPool=*pool,.level=vk::CommandBufferLevel::ePrimary,.commandBufferCount=num_slots});
        for(unsigned i=0;i<num_slots;++i){
            slots[i].buffer=buffer_allocation(v.device,{.size=uint64_t(s.width)*s.height*4,.usage=vk::BufferUsageFlagBits::eTransferDst},
                {.requiredFlags=VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT});
            slots[i].buffer.map();slots[i].cmd=std::move(commands[i]);
            slots[i].fence=vk::raii::Fence(v.device,{.flags=vk::FenceCreateFlagBits::eSignaled});
        }
        if(kind==codec_test::Kind::vr444 && eye<2) {
            gpu=std::make_unique<vr444::GpuSession>(s.width,s.height,shaders,
                std::getenv("VR444_KERNEL")?std::getenv("VR444_KERNEL"):"baseline");
        }
        if(kind==codec_test::Kind::hevc_carrier && eye<2) {
            try{
                std::string node=s.device.value_or("/dev/dri/renderD129");
                avcheck(av_hwdevice_ctx_create(&va,AV_HWDEVICE_TYPE_VAAPI,node.c_str(),nullptr,0),"VA device");
                avcheck(av_hwdevice_ctx_create_derived(&qsv,AV_HWDEVICE_TYPE_QSV,va,0),"QSV device");
                const AVCodec* c=avcodec_find_encoder_by_name("hevc_qsv");if(!c)throw std::runtime_error("hevc_qsv unavailable");
                codec=avcodec_alloc_context3(c);if(!codec)throw std::bad_alloc();
                codec->width=s.width;codec->height=s.height*3;codec->pix_fmt=AV_PIX_FMT_NV12;
                codec->time_base={1,90000};codec->framerate={int(s.fps*1000),1000};codec->max_b_frames=0;
                // Independent frames give deterministic recovery for this correctness integration.
                codec->gop_size=1;codec->color_range=AVCOL_RANGE_JPEG;codec->bit_rate=s.bitrate;codec->rc_max_rate=s.bitrate;
                codec->rc_buffer_size=std::max<uint64_t>(1,s.bitrate/std::max(1.f,s.fps));codec->hw_device_ctx=av_buffer_ref(qsv);
                AVDictionary* opts=nullptr;av_dict_set(&opts,"async_depth","1",0);av_dict_set(&opts,"preset","veryfast",0);
                av_dict_set(&opts,"look_ahead_depth","0",0);av_dict_set(&opts,"gpb","0",0);
                int r=avcodec_open2(codec,c,&opts);av_dict_free(&opts);avcheck(r,"open QSV test carrier");
                frame=av_frame_alloc();if(!frame)throw std::bad_alloc();frame->format=codec->pix_fmt;frame->width=codec->width;frame->height=codec->height;
                frame->color_range=AVCOL_RANGE_JPEG;avcheck(av_frame_get_buffer(frame,64),"carrier frame allocation");
            } catch(...) {av_frame_free(&frame);avcodec_free_context(&codec);av_buffer_unref(&qsv);av_buffer_unref(&va);throw;}
        }
    }
    ~test_encoder() override { drain_sender(); for(auto& s:slots) (void)vk.device.waitForFences(*s.fence,true,1'000'000'000); av_frame_free(&frame);avcodec_free_context(&codec);av_buffer_unref(&qsv);av_buffer_unref(&va); }
private:
    void present_image(vk::Image image,vk::SemaphoreSubmitInfo wait,uint8_t slot,uint64_t) override {
        auto& s=slots[slot];if(vk.device.waitForFences(*s.fence,true,1'000'000'000)!=vk::Result::eSuccess)throw std::runtime_error("test readback fence timeout");
        s.cmd.reset();s.cmd.begin({.flags=vk::CommandBufferUsageFlagBits::eOneTimeSubmit});
        s.cmd.copyImageToBuffer(image,vk::ImageLayout::eGeneral,s.buffer,vk::BufferImageCopy{
            .imageSubresource={.aspectMask=vk::ImageAspectFlagBits::eColor,.baseArrayLayer=stream_idx,.layerCount=1},
            .imageExtent={extent.width,extent.height,1}});
        vk::MemoryBarrier barrier{.srcAccessMask=vk::AccessFlagBits::eTransferWrite,.dstAccessMask=vk::AccessFlagBits::eHostRead};
        s.cmd.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer,vk::PipelineStageFlagBits::eHost,{},barrier,{},{});s.cmd.end();
        vk::CommandBufferSubmitInfo command{.commandBuffer=*s.cmd};
        wait.stageMask=vk::PipelineStageFlagBits2::eAllCommands;
        std::lock_guard lock(vk.queue.mutex);vk.device.resetFences(*s.fence);
        vk.queue.queue.submit2(vk::SubmitInfo2{.waitSemaphoreInfoCount=1,.pWaitSemaphoreInfos=&wait,.commandBufferInfoCount=1,.pCommandBufferInfos=&command},*s.fence);
    }
    std::optional<data> encode(uint8_t slot,uint64_t index) override {
        if(index%frame_stride!=0)return {};
        if(stream_idx>1)throw std::runtime_error("codec test requires opaque projection, no alpha stream");
        auto& s=slots[slot];if(vk.device.waitForFences(*s.fence,true,1'000'000'000)!=vk::Result::eSuccess)throw std::runtime_error("test readback timeout");
        auto began=std::chrono::steady_clock::now();
        std::vector<uint32_t> rgb(size_t(extent.width)*extent.height);std::memcpy(rgb.data(),s.buffer.data<uint8_t>(),rgb.size()*4);
        std::vector<uint8_t> bytes;double encode_gpu_ms=0;
        if(kind==codec_test::Kind::vr444){
            auto result=gpu->encode(rgb,quantizer);encode_gpu_ms=result.encode_ms;
            if(result.software)throw std::runtime_error("software Vulkan forbidden in hardware integration");
            bytes=vr444::pack(result.frame);
            if(!evidence.empty() && index%90==0){
                auto reference=vr444::decode(result.frame);
                std::ofstream(evidence/(std::to_string(index)+"-"+std::to_string(stream_idx)+".reference.rgba"),std::ios::binary).write(reinterpret_cast<const char*>(reference.data()),reference.size()*4);
            }
        }else if(kind==codec_test::Kind::raw_rgba){
            auto p=reinterpret_cast<const uint8_t*>(rgb.data());bytes.assign(p,p+rgb.size()*4);
        }else{
            avcheck(av_frame_make_writable(frame),"writable carrier");
            for(unsigned c=0;c<3;++c)for(unsigned y=0;y<extent.height;++y)for(unsigned x=0;x<extent.width;++x)
                frame->data[0][(c*extent.height+y)*frame->linesize[0]+x]=uint8_t(rgb[y*extent.width+x]>>(8*c));
            for(int y=0;y<frame->height/2;++y)std::memset(frame->data[1]+y*frame->linesize[1],128,frame->width);
            frame->pts=index;frame->pict_type=AV_PICTURE_TYPE_I;
            avcheck(avcodec_send_frame(codec,frame),"send QSV frame");
            AVPacket* packet=av_packet_alloc();if(!packet)throw std::bad_alloc();
            int r=avcodec_receive_packet(codec,packet);
            if(r>=0) { bytes.assign(packet->data,packet->data+packet->size); }
            av_packet_free(&packet);avcheck(r,"receive QSV frame (delayed output unsupported)");
        }
        if(!evidence.empty() && index%90==0){
            auto stem=std::to_string(index)+"-"+std::to_string(stream_idx);
            std::ofstream(evidence/(stem+".rgba"),std::ios::binary).write(reinterpret_cast<const char*>(rgb.data()),rgb.size()*4);
            std::ofstream(evidence/(stem+".payload"),std::ios::binary).write(reinterpret_cast<const char*>(bytes.data()),bytes.size());
        }
        auto compression_start=std::chrono::steady_clock::now();
        codec_test::compression::Selection selected{bytes,false,false,bytes.size(),bytes.size()};
        if(compression_enabled) selected=codec_test::compression::select(bytes,compression_workspace);
        double compression_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-compression_start).count();
        auto output=std::make_shared<std::vector<uint8_t>>(codec_test::pack({kind,extent.width,extent.height,stream_idx,index,codec_test::warp_hash(frame_view_info()),selected.compressed?codec_test::flag_lz4:0},selected.payload));
        if(!evidence.empty() && index%90==0)
            std::ofstream(evidence/(std::to_string(index)+"-"+std::to_string(stream_idx)+".envelope"),std::ios::binary).write(reinterpret_cast<const char*>(output->data()),output->size());
        events << "{\"frame\":" << index << ",\"eye\":" << unsigned(stream_idx)
            << ",\"warp\":" << codec_test::warp_hash(frame_view_info()) << ",\"payload_bytes\":" << output->size()
            << ",\"encode_gpu_ms\":" << encode_gpu_ms
            << ",\"compression_requested\":" << (compression_enabled?"true":"false")
            << ",\"compression_attempted\":" << (selected.attempted?"true":"false")
            << ",\"compression_applied\":" << (selected.compressed?"true":"false")
            << ",\"uncompressed_payload_bytes\":" << selected.original_bytes
            << ",\"wire_payload_bytes\":" << selected.wire_bytes
            << ",\"compression_ms\":" << compression_ms
            << ",\"encode_cpu_wall_ms\":" << std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-began).count() << "}\n";
        events.flush();
        return data{.encoder=this,.span=*output,.mem=output,.prefer_control=true};
    }
};
}
std::unique_ptr<video_encoder> make_test_encoder(vk_bundle& vk,const encoder_settings& settings,uint8_t eye){return std::make_unique<test_encoder>(vk,settings,eye);}
}
