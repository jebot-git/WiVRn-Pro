// SPDX-License-Identifier: MIT
#pragma once
#include "pipeline.hpp"
#include <optional>
namespace rowblocks_image {
struct Profile{std::array<Fixture,5> variants;};
inline std::vector<Profile> paired(const std::filesystem::path& rrf_path,const std::filesystem::path& rrb_path){
    std::vector<Fixture> singles,blocks;rowfilter_gpu::append_corpus(singles,rrf_path,"corpus");rowblocks_gpu::append_corpus(blocks,rrb_path,"rrb");
    std::map<std::string,Fixture> indexed;for(auto& f:blocks)require(indexed.emplace(f.name,std::move(f)).second,"duplicate RRB source");
    std::vector<Profile> result;std::map<std::string,std::array<std::optional<Profile>,2>> captures;
    for(auto& source:singles){Profile p;p.variants[0]=std::move(source);const auto& base=p.variants[0];
        for(unsigned v=1;v<5;++v){auto name="rrb/"+base.name.substr(7,base.name.size()-11)+".r"+std::to_string(8u<<v)+".rrb";
            auto it=indexed.find(name);require(it!=indexed.end(),"missing RRB source variant");auto& f=it->second;
            require(f.width==base.width&&f.height==base.height&&f.expected==base.expected,"RRF/RRB decoded source identity");
            for(const auto& wire:f.wire)require(rowblocks::inspect(wire).block_rows==(8u<<v),"RRB source partition");
            p.variants[v]=std::move(f);p.variants[v].name=base.name;indexed.erase(it);
        }
        if(base.name.starts_with("corpus/synthetic/"))result.push_back(std::move(p));
        else{require(base.name.ends_with("-0.rrf")||base.name.ends_with("-1.rrf"),"capture eye suffix");
            unsigned eye=base.name.ends_with("-1.rrf");auto& slot=captures[base.name.substr(0,base.name.size()-6)][eye];require(!slot,"duplicate eye");slot=std::move(p);}
    }
    require(indexed.empty(),"unmatched RRB corpus entries");require(!captures.empty(),"captured pairs required");
    for(auto& [name,eyes]:captures){require(eyes[0]&&eyes[1],"missing stereo eye");Profile pair;
        for(unsigned v=0;v<5;++v){auto& l=eyes[0]->variants[v];auto& r=eyes[1]->variants[v];require(l.width==r.width&&l.height==r.height,"stereo source shape");
            pair.variants[v]={name,l.width,l.height,{std::move(l.wire[0]),std::move(r.wire[0])},{std::move(l.expected[0]),std::move(r.expected[0])}};}
        result.push_back(std::move(pair));
    }return result;
}
inline std::vector<Profile> qualification(const std::filesystem::path& goldens,const std::filesystem::path& corpus){
    auto sources=rowfilter_gpu::generated(true,true);rowfilter_gpu::append_corpus(sources,goldens,"golden");rowfilter_gpu::append_corpus(sources,corpus,"corpus");std::vector<Profile> result;
    for(auto& source:sources){Profile p;p.variants[0]=std::move(source);
        for(unsigned v=1;v<5;++v){auto& f=p.variants[v];f=p.variants[0];unsigned left=8u<<v,right=left==128?16:left*2;
            for(unsigned eye=0;eye<2;++eye){rowblocks::Workspace w;auto wire=rowblocks::encode(f.expected[eye],f.width,f.height,eye?right:left,rowfilter::Policy::up,w);f.wire[eye].assign(wire.begin(),wire.end());}
        }result.push_back(std::move(p));
    }return result;
}
}
