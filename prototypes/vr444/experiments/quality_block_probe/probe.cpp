// Offline exact RRB1 block profile size probe; no device or transport operation.
#include "codec_test/rrb/rowblocks.hpp"
#include <fstream>
#include <iostream>
#include <string>
#include <filesystem>
std::vector<uint32_t> read(const char*p,size_t count){std::ifstream in(p,std::ios::binary|std::ios::ate);if(!in||in.tellg()!=std::streamoff(count*4))throw std::runtime_error("source shape");std::vector<uint32_t> v(count);in.seekg(0);in.read((char*)v.data(),count*4);if(!in)throw std::runtime_error("source read");return v;}
int main(int argc,char**argv){try{
 if(argc!=6)throw std::runtime_error("probe LEFT.rgba RIGHT.rgba WIDTH HEIGHT OUTDIR");
 auto w=std::stoul(argv[3]),h=std::stoul(argv[4]);if(w>4096||h>4096||w*h>4194304)throw std::runtime_error("shape cap");
 std::array<std::vector<uint32_t>,2> src{read(argv[1],w*h),read(argv[2],w*h)};
 std::filesystem::path out(argv[5]);if(!std::filesystem::is_directory(out)||!std::filesystem::is_empty(out))throw std::runtime_error("fresh output directory");
 for(unsigned rows:{16,32,64,128}){size_t pair=96;std::cout<<"{\"block_rows\":"<<rows<<",\"eyes\":[";
  for(unsigned eye=0;eye<2;++eye){rowblocks::Workspace enc,dec;auto wire=rowblocks::encode(src[eye],w,h,rows,rowblocks::Policy::adaptive,enc);
   std::vector<uint32_t> reconstructed(w*h);auto index=rowblocks::decode_into(wire,reconstructed,dec,false);if(src[eye]!=reconstructed)throw std::runtime_error("exact reconstruction");
   unsigned modes[3]={};for(auto&b:index.blocks)++modes[b.mode];pair+=wire.size();
   auto file=out/("rrb"+std::to_string(rows)+"-"+std::to_string(eye)+".payload");std::ofstream f(file,std::ios::binary);f.write((const char*)wire.data(),wire.size());if(!f)throw std::runtime_error("output write");
   if(eye)std::cout<<",";std::cout<<"{\"eye\":"<<eye<<",\"payload_bytes\":"<<wire.size()<<",\"blocks\":"<<index.blocks.size()<<",\"modes_raw_plain_up\":["<<modes[0]<<","<<modes[1]<<","<<modes[2]<<"],\"scalar_exact\":true}";
  }
  std::cout<<"],\"stereo_envelope_bytes\":"<<pair<<",\"within_existing_262144_cap\":"<<(pair<=262144?"true":"false")<<"}\n";
 }
 }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
