// SPDX-License-Identifier: MIT
// Offline canonical RRB1 -> opaque RGBA oracle. No socket/device operations.
#include "codec_test/rrb/rowblocks.hpp"
#include <fstream>
#include <iostream>
int main(int argc,char**argv){
 try{
  if(argc!=3)throw std::runtime_error("usage: rrb-reference INPUT.payload OUTPUT.rgba");
  std::ifstream input(argv[1],std::ios::binary|std::ios::ate);
  if(!input)throw std::runtime_error("input open");auto n=input.tellg();
  if(n<=0||uint64_t(n)>rowblocks::max_wire)throw std::runtime_error("input bound");
  std::vector<uint8_t>wire(static_cast<size_t>(n));input.seekg(0);input.read(reinterpret_cast<char*>(wire.data()),wire.size());if(!input)throw std::runtime_error("input read");
  auto index=rowblocks::inspect(wire);if(index.block_rows!=32)throw std::runtime_error("expected RRB32 profile");
  std::vector<uint32_t>rgba(size_t(index.width)*index.height);rowblocks::Workspace workspace;
  rowblocks::decode_into(wire,rgba,workspace,false);
  std::ofstream output(argv[2],std::ios::binary);output.write(reinterpret_cast<const char*>(rgba.data()),rgba.size()*4);if(!output)throw std::runtime_error("output write");
  std::cout<<"{\"codec\":\"rrb32\",\"width\":"<<index.width<<",\"height\":"<<index.height<<",\"rgba_bytes\":"<<rgba.size()*4<<"}\n";
 }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}
}
