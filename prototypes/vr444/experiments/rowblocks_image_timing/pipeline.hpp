// SPDX-License-Identifier: MIT
#pragma once
#include "../rowblocks_gpu/fixtures.hpp"
#include "../rowfilter_image_timing/pipeline.hpp"
namespace rowblocks_image {
using namespace rowblocks_gpu;
using rowfilter_image::Result;
struct ImagePath {
    Session session;
    Prepared prepared;
    bool cpu;
    unsigned mode;
    std::array<std::vector<uint32_t>,2> decoded;
    ImagePath(unsigned width,unsigned height,const std::filesystem::path& shaders,bool on_cpu,unsigned selected,bool cached_input=false)
        :session(width,height,shaders/"rrb/shaders",selected,false,false,8,false,cached_input),cpu(on_cpu),mode(cpu?0:1) {
        require(session.properties.deviceType==VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU||session.properties.deviceType==VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU,"hardware GPU required");
        if(cpu)for(auto& pixels:decoded)pixels.resize(size_t(width)*height);
    }
    Measurement copy_rgba(Measurement m) {
        auto& s=session;auto start=Clock::now();s.begin();vkCmdResetQueryPool(s.command,s.o.queries,0,2);
        s.memory_barrier(VK_ACCESS_HOST_WRITE_BIT,VK_ACCESS_TRANSFER_READ_BIT,VK_PIPELINE_STAGE_HOST_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT);
        vkCmdWriteTimestamp(s.command,VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,s.o.queries,0);
        for(unsigned eye=0;eye<2;++eye){auto& image=s.o.images[0][eye];
            require(image.layout==VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,"CPU copy image layout");
            auto region=s.region();vkCmdCopyBufferToImage(s.command,s.o.inputs[eye][0].handle,image.handle,image.layout,1,&region);
            s.barrier(image,VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,VK_ACCESS_TRANSFER_WRITE_BIT,VK_ACCESS_SHADER_READ_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);
        }
        vkCmdWriteTimestamp(s.command,VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,s.o.queries,1);s.submit();m.pipeline_wall_ms=elapsed(start);
        uint64_t stamps[2]{};check(vkGetQueryPoolResults(s.o.device,s.o.queries,0,2,sizeof(stamps),stamps,8,VK_QUERY_RESULT_64_BIT));
        uint64_t mask=s.timestamp_bits==64?UINT64_MAX:(uint64_t(1)<<s.timestamp_bits)-1;
        m.gpu_ms=double((stamps[1]-stamps[0])&mask)*s.properties.limits.timestampPeriod/1e6;return m;
    }
    Result run(const Fixture& fixture){
        auto start=Clock::now();auto& s=session;Result r;unsigned before=s.submissions;
        prepared.admit(s,fixture); // Both complete packets before restoration or any mapped/image write.
        if(cpu){
            for(unsigned eye=0;eye<2;++eye)require(decoded[eye].size()*4<=s.o.inputs[eye][0].bytes,"RGBA staging capacity");
            for(unsigned eye=0;eye<2;++eye){auto& rgb=prepared.work[eye].rgb;const auto& index=prepared.indices[eye];size_t stride=size_t(s.width)*3;
                for(const auto& block:index.blocks)if(block.mode==2)
                    for(size_t row=stride;row<block.raw_bytes;row+=stride)
                        rowfilter::detail::add_row(std::span(rgb).subspan(block.raw_offset+row,stride),rowblocks::View(rgb).subspan(block.raw_offset+row-stride,stride),true);
                rowfilter::detail::expand(rgb,decoded[eye],true);
            }
        }
        r.stages.validation_ms=elapsed(start);auto stage=Clock::now();s.poison(mode);r.poison_ms=elapsed(stage);stage=Clock::now();
        if(cpu)for(unsigned eye=0;eye<2;++eye)std::memcpy(s.o.inputs[eye][0].mapped,decoded[eye].data(),decoded[eye].size()*4);
        else prepared.upload(s);
        r.uploaded_bytes=uint64_t(s.width)*s.height*(cpu?8:6);r.stages.input_copy_ms=elapsed(stage);
        r.stages=cpu?copy_rgba(r.stages):s.execute_pipeline(mode,r.stages,prepared.groups);
        r.submissions=s.submissions-before;r.total_ms=elapsed(start);require(r.submissions==2,"poison plus readiness submissions");return r;
    }
};
struct Engine {
    unsigned variant;
    std::string name;
    std::unique_ptr<rowfilter_image::ImagePath> rrf;
    std::unique_ptr<ImagePath> rrb;
    Engine(unsigned w,unsigned h,const std::filesystem::path& shaders,unsigned v,bool cpu,unsigned selected):variant(v){
        if(v){rrb=std::make_unique<ImagePath>(w,h,shaders,cpu,selected);name="rrb"+std::to_string(8u<<v)+(cpu?"_cpu_simd_rgba_copy":"_gpu_unorm");}
        else{rrf=std::make_unique<rowfilter_image::ImagePath>(w,h,shaders/"rrf",4,cpu?0:1,cpu,selected);name=cpu?"rrf_cpu_simd_rgba_copy":"rrf_gpu_c4_unorm";}
    }
    Session& session(){return variant?rrb->session:rrf->session;}
    unsigned mode()const{return variant?rrb->mode:rrf->mode;}
    Result run(const Fixture& fixture){return variant?rrb->run(fixture):rrf->run(fixture);}
};
}
