// SPDX-License-Identifier: MIT
#include "../udp_offload/offload.hpp"
// A correctness-only stdin/stdout adapter around the owned-loopback experiment.
// It cannot select an external address/port and emits no latency measurement.
static void read_bytes(void*dst,size_t n){std::cin.read(static_cast<char*>(dst),std::streamsize(n));need(bool(std::cin),"truncated relay input");}
static uint32_t get32(){std::array<uint8_t,4>b{};read_bytes(b.data(),b.size());return uint32_t(b[0])|uint32_t(b[1])<<8|uint32_t(b[2])<<16|uint32_t(b[3])<<24;}
static void put32(uint32_t n){std::array<uint8_t,4>b{};for(unsigned i=0;i<4;++i)b[i]=uint8_t(n>>(8*i));std::cout.write(reinterpret_cast<char*>(b.data()),b.size());}
int main(int argc,char**argv){try{
    need(argc==2,"relay requires mode 0,1,2");std::string arg=argv[1];need(arg=="0"||arg=="1"||arg=="2","unknown relay mode");unsigned mode=unsigned(arg[0]-'0');
    std::array<char,4>magic{};read_bytes(magic.data(),4);need(magic==std::array<char,4>{'U','P','X','1'},"relay magic");
    uint32_t count=get32();need(count>0&&count<=512,"relay packet count");Packets input;input.reserve(count);
    for(unsigned i=0;i<count;++i){uint32_t n=get32();need(n>0&&n<=1200,"relay packet length");Packet p(n);read_bytes(p.data(),p.size());input.push_back(std::move(p));}
    need(std::cin.peek()==std::char_traits<char>::eof(),"relay trailing input");
    Sockets sockets;Packets received;Counters calls{};size_t max_segments=1;
    if(mode==0)calls=run(sockets,input,32,true,max_batch,max_batch,&received).calls;
    else{if(mode==2)offload::enable_gro(sockets);auto r=offload::run(sockets,input,32,mode==2,&received);calls=r.base.calls;max_segments=r.max_segments;}
    need(received.size()==input.size(),"relay complete output");
    std::cout.write("UPY1",4);put32(uint32_t(received.size()));put32(calls.send_calls);put32(calls.receive_calls);put32(uint32_t(max_segments));
    for(const auto&p:received){put32(uint32_t(p.size()));std::cout.write(reinterpret_cast<const char*>(p.data()),std::streamsize(p.size()));}
    need(bool(std::cout),"relay output error");
}catch(const offload::Unsupported&e){std::cerr<<e.what()<<'\n';return 77;}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
