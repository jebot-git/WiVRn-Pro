#include "codec_test/wire.h"
#include <cassert>
#include <functional>
using namespace wivrn::codec_test;
void rejected(const std::function<void()>& f) { bool threw=false;try{f();}catch(const std::exception&){threw=true;}assert(threw); }
int main(){
 const std::vector<uint8_t> body{0,1,2,255};
 for(auto kind:{Kind::hevc_carrier,Kind::vr444,Kind::raw_rgba}) {
  auto p=pack({kind,576,640,1,0xfedcba9876543210ull,0xf123456789abcdefull},body);
  auto h=unpack(p);assert(h.kind==kind&&h.frame==0xfedcba9876543210ull&&h.warp==0xf123456789abcdefull);
  for(size_t i=0;i<p.size();++i)rejected([&]{unpack(std::span<const uint8_t>(p).first(i));});
  auto corrupt=p;corrupt.back()^=1;rejected([&]{unpack(corrupt);});
  corrupt=p;corrupt[44]=1;rejected([&]{unpack(corrupt);});
  corrupt=p;corrupt.push_back(0);rejected([&]{unpack(corrupt);});
 }
 rejected([&]{pack({Kind::raw_rgba,576,640,2,0,0},body);});
 rejected([&]{pack({Kind::raw_rgba,4096,4096,0,0,0},body);});
 rejected([&]{pack({Kind(4),576,640,0,0,0},body);});
 rejected([&]{pack({Kind::raw_rgba,576,640,0,0,0},{});});
 const std::vector<uint8_t> standard{'1','2','3','4','5','6','7','8','9'};
 assert(crc(standard)==0xcbf43926);
}
