// SPDX-License-Identifier: MIT
#include "../direct_image_gpu/host_copy.hpp"
#include <iostream>
using namespace direct_image::host_copy;
int main(){try {
    Evidence yes;yes.queried=yes.dependencies=yes.feature=yes.format=yes.image_usage=true;
    yes.sources={VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,VK_IMAGE_LAYOUT_GENERAL};yes.destinations={VK_IMAGE_LAYOUT_GENERAL,VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL};
    gate(yes);unsigned rejected=0;
    auto deny=[&](Evidence e){try{gate(e);}catch(const Unsupported&){++rejected;return;}throw std::runtime_error("unsupported gate accepted");};
    deny({});
    for(auto flag:{&Evidence::queried,&Evidence::dependencies,&Evidence::feature,&Evidence::format,&Evidence::image_usage}){auto e=yes;e.*flag=false;deny(e);}
    for(unsigned index=0;index<2;++index){auto e=yes;e.sources.erase(e.sources.begin()+index);deny(e);e=yes;e.destinations.erase(e.destinations.begin()+index);deny(e);}
    auto unrelated=yes;unrelated.sources={VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL};deny(unrelated);unrelated=yes;unrelated.destinations={VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL};deny(unrelated);
    // Performance hints are evidence, never substitutes for feature support or
    // reasons to silently skip an otherwise correct path.
    yes.optimal_device_access=yes.identical_memory_layout=yes.identical_memory_type_requirements=false;gate(yes);
    std::cout<<"{\"pass\":true,\"negative_capability_gates\":"<<rejected<<",\"performance_hints_not_support_gates\":true}\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
