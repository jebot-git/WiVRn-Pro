// SPDX-License-Identifier: MIT
#pragma once
#include "../rowfilter_gpu/fixtures.hpp"
namespace rowfilter_image {
using namespace rowfilter_gpu;
struct Result {
    Measurement stages;
    double total_ms{},poison_ms{};
    uint64_t uploaded_bytes{};
    unsigned submissions{};
};
struct ImagePath {
    Session session;
    unsigned columns,mode;
    bool cpu;
    std::array<rowfilter::Workspace,2> workspace;
    std::array<std::vector<uint32_t>,2> decoded;
    ImagePath(unsigned width,unsigned height,const std::filesystem::path& shaders,unsigned cols,unsigned output,bool cpu_decode,unsigned selected=0)
        :session(width,height,shaders/("shaders-"+std::to_string(cols)),selected,false,false),columns(cols),mode(output),cpu(cpu_decode) {
        require(cols==1||cols==4||cols==16,"column layout");
        require((cpu&&mode==0)||(!cpu&&(mode==1||mode==2)),"pipeline output arm");
        require(session.properties.deviceType==VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU||session.properties.deviceType==VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU,"hardware GPU required");
        if(cpu)for(auto& pixels:decoded)pixels.resize(size_t(width)*height);
    }
    std::string name() const {return cpu?"cpu_simd_rgba_copy":"gpu_c"+std::to_string(columns)+(mode==1?"_unorm":"_uint");}
    Measurement copy_rgba(Measurement m) {
        auto& s=session;auto start=Clock::now();s.begin();vkCmdResetQueryPool(s.command,s.o.queries,0,2);
        s.memory_barrier(VK_ACCESS_HOST_WRITE_BIT,VK_ACCESS_TRANSFER_READ_BIT,VK_PIPELINE_STAGE_HOST_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT);
        vkCmdWriteTimestamp(s.command,VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,s.o.queries,0);
        for(unsigned eye=0;eye<2;++eye) {
            auto& image=s.o.images[0][eye];require(image.layout==VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,"CPU copy image layout");
            auto region=s.region();vkCmdCopyBufferToImage(s.command,s.o.inputs[eye][0].handle,image.handle,image.layout,1,&region);
            s.barrier(image,VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,VK_ACCESS_TRANSFER_WRITE_BIT,VK_ACCESS_SHADER_READ_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);
        }
        vkCmdWriteTimestamp(s.command,VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,s.o.queries,1);s.submit();m.pipeline_wall_ms=elapsed(start);
        uint64_t stamps[2]{};check(vkGetQueryPoolResults(s.o.device,s.o.queries,0,2,sizeof(stamps),stamps,8,VK_QUERY_RESULT_64_BIT));
        uint64_t mask=s.timestamp_bits==64?UINT64_MAX:(uint64_t(1)<<s.timestamp_bits)-1;
        m.gpu_ms=double((stamps[1]-stamps[0])&mask)*s.properties.limits.timestampPeriod/1e6;
        return m;
    }
    Result run(const Fixture& fixture) {
        auto start=Clock::now();Result result;auto& s=session;const unsigned before=s.submissions;
        require(fixture.width==s.width&&fixture.height==s.height,"fixture shape");
        const unsigned groups=(s.width+columns-1)/columns;
        require(groups<=s.properties.limits.maxComputeWorkGroupCount[0],"dispatch capability");
        // Both full packets are admitted before any CPU prefix reconstruction,
        // mapped write, poison, queue work or output-image change.
        std::array<rowfilter::Shape,2> shapes{
            rowfilter::unpack_rgb(fixture.wire[0],workspace[0]),
            rowfilter::unpack_rgb(fixture.wire[1],workspace[1])};
        for(unsigned eye=0;eye<2;++eye) {
            require(shapes[eye].width==s.width&&shapes[eye].height==s.height,"RRF stereo shape");
            size_t bytes=cpu?size_t(s.width)*s.height*4:shapes[eye].raw_bytes;
            require(((bytes+3)&~size_t(3))<=s.o.inputs[eye][0].bytes,"staging allocation");
        }
        if(cpu)for(unsigned eye=0;eye<2;++eye) {
            if(shapes[eye].mode==2)rowfilter::restore(workspace[eye].rgb,size_t(s.width)*3,true);
            rowfilter::detail::expand(workspace[eye].rgb,decoded[eye],true);
        }
        result.stages.validation_ms=elapsed(start);
        auto stage=Clock::now();s.poison(mode);result.poison_ms=elapsed(stage);stage=Clock::now();
        for(unsigned eye=0;eye<2;++eye) {
            auto& input=s.o.inputs[eye];const void* source=cpu?static_cast<const void*>(decoded[eye].data()):static_cast<const void*>(workspace[eye].rgb.data());
            size_t bytes=cpu?decoded[eye].size()*4:workspace[eye].rgb.size();
            std::memcpy(input[0].mapped,source,bytes);
            std::memset(static_cast<uint8_t*>(input[0].mapped)+bytes,0,((bytes+3)&~size_t(3))-bytes);
            if(!cpu){*static_cast<uint32_t*>(input[1].mapped)=shapes[eye].mode;*static_cast<uint32_t*>(input[2].mapped)=shapes[eye].raw_bytes;}
            result.uploaded_bytes+=bytes;
        }
        result.stages.input_copy_ms=elapsed(stage);
        result.stages=cpu?copy_rgba(result.stages):s.execute_pipeline(mode,result.stages,groups);
        result.submissions=s.submissions-before;result.total_ms=elapsed(start);
        require(result.submissions==2,"poison and readiness submissions");
        return result;
    }
    Measurement verify(const Fixture& fixture) { return session.verify(mode,fixture.expected); }
};
} // namespace rowfilter_image
