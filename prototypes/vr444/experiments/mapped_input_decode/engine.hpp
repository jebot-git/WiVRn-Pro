// SPDX-License-Identifier: MIT
#pragma once
#include "pipeline.hpp"
namespace mapped_input {
constexpr std::array<const char*,9> names{
    "rrf_cpu_reference", "rrf_staged_default", "rrf_direct_default", "rrf_staged_cached", "rrf_direct_cached",
    "rrb32_staged_default", "rrb32_direct_default", "rrb32_staged_cached", "rrb32_direct_cached"};
struct Engine{
    unsigned arm,variant;
    bool cached,direct;
    std::unique_ptr<DirectPath> mapped;
    std::unique_ptr<rowfilter_image::ImagePath> rrf;
    std::unique_ptr<rowblocks_image::ImagePath> rrb;
    Engine(unsigned width,unsigned height,const std::filesystem::path& shaders,unsigned which,unsigned selected)
        :arm(which),variant(which>=5?2:0),cached(which==3||which==4||which==7||which==8),direct(which==2||which==4||which==6||which==8){
        require(arm<names.size(),"mapped arm index");
        if(direct)mapped=std::make_unique<DirectPath>(width,height,shaders,variant!=0,cached,selected);
        else if(variant)rrb=std::make_unique<rowblocks_image::ImagePath>(width,height,shaders,false,selected,cached);
        else rrf=std::make_unique<rowfilter_image::ImagePath>(width,height,shaders/"rrf",4,arm?1:0,arm==0,selected,false,cached);
        if(cached)for(const auto& eye:session().o.inputs)require(eye[0].flags&VK_MEMORY_PROPERTY_HOST_CACHED_BIT,"requested cached input was not selected");
    }
    Session& session(){return mapped?mapped->session:rrb?rrb->session:rrf->session;}
    unsigned mode()const{return arm?1:0;}
    Result run(const Fixture& fixture){
        if(mapped)return mapped->run(fixture);
        Result r;r.readiness=rrb?rrb->run(fixture):rrf->run(fixture);
        r.intermediate_pixel_copy_bytes=r.readiness.uploaded_bytes;r.mapped_output_bytes=r.readiness.uploaded_bytes;
        if(rrb)for(const auto& index:rrb->prepared.indices)r.metadata_bytes+=(1+index.blocks.size()*4)*4+4;
        else if(arm)r.metadata_bytes=16;
        return r;
    }
};
}
