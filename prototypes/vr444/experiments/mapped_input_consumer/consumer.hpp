// SPDX-License-Identifier: MIT
#pragma once
#include "../mapped_input_decode/engine.hpp"
namespace mapped_consumer {
using namespace mapped_input;
struct Consumer {double wall_ms{},gpu_ms{};unsigned submissions{};};
using Snapshot=std::array<std::vector<float>,2>;
inline Snapshot snapshot(Session& s){
    Snapshot result;
    for(unsigned eye=0;eye<2;++eye){const auto* values=static_cast<const float*>(s.o.linear[eye].mapped);
        result[eye].assign(values,values+s.o.linear[eye].bytes/sizeof(float));}
    return result;
}
inline void require_same(const Snapshot& actual,const Snapshot& expected){
    for(unsigned eye=0;eye<2;++eye)
        require(actual[eye].size()==expected[eye].size()&&!std::memcmp(actual[eye].data(),expected[eye].data(),actual[eye].size()*sizeof(float)),"first consumer differs from verified stereo reference");
}
inline Consumer consume(Engine& engine,bool omit_right=false){
    auto& s=engine.session();const unsigned mode=engine.mode();auto start=Clock::now();const unsigned before=s.submissions;
    for(const auto& image:s.o.images[mode])require(image.layout==VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,"first consumer requires ready image");
    // Readiness completed its fence/query read before this query pool or command
    // buffer is reused. Both descriptor sets sample the actual SRGB image views.
    s.begin();vkCmdResetQueryPool(s.command,s.o.queries,0,2);
    vkCmdWriteTimestamp(s.command,VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,s.o.queries,0);
    for(unsigned eye=0;eye<(omit_right?1u:2u);++eye){s.bind(modes,s.sample_sets[mode][eye]);vkCmdDispatch(s.command,(s.width*s.height+63)/64,1,1);}
    s.memory_barrier(VK_ACCESS_SHADER_WRITE_BIT,VK_ACCESS_HOST_READ_BIT|VK_ACCESS_SHADER_WRITE_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT|VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);
    vkCmdWriteTimestamp(s.command,VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,s.o.queries,1);s.submit();
    uint64_t stamps[2]{};check(vkGetQueryPoolResults(s.o.device,s.o.queries,0,2,sizeof(stamps),stamps,8,VK_QUERY_RESULT_64_BIT));
    const uint64_t mask=s.timestamp_bits==64?UINT64_MAX:(uint64_t(1)<<s.timestamp_bits)-1;
    Consumer result{elapsed(start),double((stamps[1]-stamps[0])&mask)*s.properties.limits.timestampPeriod/1e6,s.submissions-before};
    require(result.submissions==1,"one first-consumer submission required");return result;
}
inline void omission_control(Engine& engine,const Fixture& fixture){
    engine.run(fixture);auto& s=engine.session();
    // This private output is retired by readiness's observed fence. Poison the
    // actual consumer destinations so an omitted eye cannot reuse valid pixels.
    for(auto& output:s.o.linear)std::memset(output.mapped,0xff,output.bytes);
    consume(engine,true);auto missing=snapshot(s);
    s.verify(engine.mode(),fixture.expected);auto expected=snapshot(s);
    require(missing[0]==expected[0],"omission control changed the present left eye");
    bool rejected=false;try{require_same(missing,expected);}catch(const std::exception&){rejected=true;}
    require(rejected,"omitted right consumer was accepted");
}
}
