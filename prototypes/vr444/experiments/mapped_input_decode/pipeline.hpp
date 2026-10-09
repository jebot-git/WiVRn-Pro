// SPDX-License-Identifier: MIT
#pragma once
#include "pair.hpp"
#include "../rowblocks_image_timing/fixtures.hpp"
namespace mapped_input {
using namespace direct_image;
using rowfilter_gpu::Fixture;
struct Result{
    rowfilter_image::Result readiness;
    bool split_decode=false;
    double preflight_ms{},decode_ms{};
    uint64_t intermediate_pixel_copy_bytes{},mapped_output_bytes{},metadata_bytes{};
};
struct DirectPath{
    Session session;
    bool blocks;
    DirectPath(unsigned width,unsigned height,const std::filesystem::path& shaders,bool row_blocks,bool cached,unsigned selected)
        :session(width,height,shaders/(row_blocks?"rrb/shaders":"rrf/shaders-4"),selected,false,false,row_blocks?8:1,false,cached),blocks(row_blocks){
        require(session.properties.deviceType==VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU||session.properties.deviceType==VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU,"hardware GPU required");
        for(const auto& input:session.o.inputs)require((input[0].flags&(VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT))==(VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT),"coherent mapped input required");
    }
    Result run(const Fixture& fixture,Decoder decoder=LZ4_decompress_safe){
        auto start=Clock::now();auto& s=session;Result r;r.split_decode=true;unsigned before=s.submissions;
        require(fixture.width==s.width&&fixture.height==s.height,"mapped fixture dimensions");
        {
        std::array<Mutable,2> dest;
        for(unsigned eye=0;eye<2;++eye)dest[eye]={static_cast<uint8_t*>(s.o.inputs[eye][0].mapped),size_t(s.o.inputs[eye][0].bytes)};
        Pair pair(blocks,s.width,s.height,{fixture.wire[0],fixture.wire[1]},dest);
        unsigned groups=(s.width+3)/4;
        if(blocks){size_t count=0;for(unsigned eye=0;eye<2;++eye){const auto& index=pair.index(eye);count=std::max(count,index.blocks.size());
                require((1+4*index.blocks.size())*4<=s.o.inputs[eye][1].bytes,"mapped block metadata capacity");}
            groups=unsigned(((s.width+127)/128)*count);
        }else for(unsigned eye=0;eye<2;++eye)require(s.o.inputs[eye][1].bytes>=4,"mapped row metadata capacity");
        require(groups<=s.properties.limits.maxComputeWorkGroupCount[0],"mapped dispatch limit");
        r.preflight_ms=elapsed(start);auto stage=Clock::now();s.poison(1);r.readiness.poison_ms=elapsed(stage);
        // Poison's observed fence also retires earlier reads of these owned
        // inputs. Coherent host writes precede the subsequent queue submission.
        stage=Clock::now();pair.decode(decoder);r.decode_ms=elapsed(stage);require(pair.complete(),"both mapped decodes required before metadata/submission");
        stage=Clock::now();for(unsigned eye=0;eye<2;++eye){auto& input=s.o.inputs[eye];auto* metadata=static_cast<uint32_t*>(input[1].mapped);
            if(blocks){const auto& index=pair.index(eye);metadata[0]=index.blocks.size();
                for(size_t b=0;b<index.blocks.size();++b){const auto& block=index.blocks[b];metadata[1+b*4]=block.row;metadata[2+b*4]=block.height;metadata[3+b*4]=block.mode;metadata[4+b*4]=block.raw_offset;}
                r.metadata_bytes+=(1+index.blocks.size()*4)*4;
            }else{metadata[0]=pair.shape(eye).mode;r.metadata_bytes+=4;}
            *static_cast<uint32_t*>(input[2].mapped)=pair.shape(eye).raw_bytes;r.metadata_bytes+=4;
        }
        r.readiness.stages.input_copy_ms=elapsed(stage);r.readiness.stages.validation_ms=r.preflight_ms+r.decode_ms;
        r.readiness.stages=s.execute_pipeline(1,r.readiness.stages,groups);
        } // Include checked index destruction before the complete outer wall ends.
        // Direct CPU stores still populate all GPU-visible pixel bytes; this is
        // not a claim of zero host/device traffic. Only the intermediate copy is
        // absent, as recorded separately above.
        r.mapped_output_bytes=uint64_t(s.width)*s.height*6;r.readiness.uploaded_bytes=r.mapped_output_bytes;
        r.readiness.submissions=s.submissions-before;r.readiness.total_ms=elapsed(start);
        require(r.readiness.submissions==2,"mapped poison and readiness submissions");return r;
    }
};
}
