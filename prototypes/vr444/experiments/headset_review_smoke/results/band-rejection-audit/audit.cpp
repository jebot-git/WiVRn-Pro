#include "encoder.hpp"
#include "codec.hpp"
#include <iostream>
using namespace rowbands;
int main(){
 auto pixels=vr444::scene(35,65,1), other=vr444::scene(35,65,0);
 std::vector<uint32_t> qmap(15);for(size_t i=0;i<qmap.size();++i)qmap[i]=i%2?1:8;
 auto l=vr444::pack(vr444::encode(pixels,35,65,8,qmap));std::reverse(qmap.begin(),qmap.end());
 auto r=vr444::pack(vr444::encode(other,35,65,8,qmap));
 std::cout<<"seed_body_bytes="<<l.size()-24<<" right_wire_bytes="<<r.size()<<" fixed_rejections="<<900+l.size()-24+r.size()<<'\n';
 for(unsigned workers:{1u,2u,4u}){Pool pool(workers);
  for(unsigned trial=0;trial<20;++trial){Workspace published;auto out=transcode_pair(l,r,16,rowfilter::Policy::up,published,&pool);(void)out;
   size_t active=0,aliases=0;std::cout<<"workers="<<workers<<" trial="<<trial<<" scratch_sizes=";
   for(auto&s:published.scratch){std::cout<<'['<<s.rgb.size()<<','<<s.encoder.rgb.size()<<','<<s.encoder.filtered.size()<<','<<s.encoder.compressed.size()<<','<<s.encoder.wire.size()<<']';
    if(!s.rgb.empty()){++active;++aliases;for(auto*b:{&s.encoder.rgb,&s.encoder.filtered,&s.encoder.compressed,&s.encoder.wire})if(!b->empty())++aliases;}}
   std::cout<<" active="<<active<<" alias_rejections="<<aliases<<" inferred_full_test_rejections="<<900+l.size()-24+r.size()+aliases<<'\n';
  }
 }
}
