// SPDX-License-Identifier: MIT
#pragma once
#include "../rowfilter_image_timing/pipeline.hpp"
namespace host_image_timing {
using namespace rowfilter_image;
struct Consumer {double wall_ms{},gpu_ms{};unsigned submissions{};};
inline Consumer sample(ImagePath& path,bool omit_right=false) {
    auto& s=path.session;auto start=Clock::now();auto before=s.submissions;
    for(const auto& image:s.o.images[path.mode])require(image.layout==VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,"consumer image readiness");
    s.begin();vkCmdResetQueryPool(s.command,s.o.queries,0,2);
    vkCmdWriteTimestamp(s.command,VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,s.o.queries,0);
    for(unsigned eye=0;eye<(omit_right?1u:2u);++eye){s.bind(modes,s.sample_sets[path.mode][eye]);vkCmdDispatch(s.command,(s.width*s.height+63)/64,1,1);}
    // Read the completed first consumer output on the host before the separate
    // oracle can overwrite it. Also retain shader write visibility for reuse.
    s.memory_barrier(VK_ACCESS_SHADER_WRITE_BIT,VK_ACCESS_HOST_READ_BIT|VK_ACCESS_SHADER_WRITE_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT|VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);
    vkCmdWriteTimestamp(s.command,VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,s.o.queries,1);s.submit();
    uint64_t stamps[2]{};check(vkGetQueryPoolResults(s.o.device,s.o.queries,0,2,sizeof(stamps),stamps,8,VK_QUERY_RESULT_64_BIT));
    uint64_t mask=s.timestamp_bits==64?UINT64_MAX:(uint64_t(1)<<s.timestamp_bits)-1;
    Consumer result{elapsed(start),double((stamps[1]-stamps[0])&mask)*s.properties.limits.timestampPeriod/1e6,s.submissions-before};
    require(result.submissions==1,"consumer submission count");return result;
}
} // namespace host_image_timing
