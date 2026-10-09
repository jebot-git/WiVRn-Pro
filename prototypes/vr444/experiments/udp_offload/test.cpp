// SPDX-License-Identifier: MIT
#include "offload.hpp"
int main(){try{
    using namespace offload;size_t rejection_count=0;
    auto reject=[&](auto fn){rejects(fn);++rejection_count;};
    alignas(cmsghdr) std::array<uint8_t,2*CMSG_SPACE(sizeof(int))> control{};
    msghdr msg{};msg.msg_control=control.data();msg.msg_controllen=CMSG_SPACE(sizeof(int));
    auto set=[&](int segment){msg.msg_flags=0;msg.msg_controllen=CMSG_SPACE(sizeof(int));auto*c=CMSG_FIRSTHDR(&msg);c->cmsg_level=IPPROTO_UDP;c->cmsg_type=UDP_GRO;c->cmsg_len=CMSG_LEN(sizeof(int));std::memcpy(CMSG_DATA(c),&segment,sizeof(segment));};
    set(1200);auto p=parse(msg,2417,3,control.size());need(p.count==3&&p.segment==1200&&p.last==17,"tail parsing");
    for(int bad:{-1,0,1201}){set(bad);reject([&]{parse(msg,1200,2,control.size());});}
    set(1200);reject([&]{parse(msg,2417,2,control.size());});reject([&]{parse(msg,0,2,control.size());});
    reject([&]{parse(msg,max_aggregate+1,max_batch,control.size());});
    for(int flag:{MSG_TRUNC,MSG_CTRUNC}){set(1200);msg.msg_flags=flag;reject([&]{parse(msg,1200,2,control.size());});}
    set(1200);CMSG_FIRSTHDR(&msg)->cmsg_level=SOL_SOCKET;reject([&]{parse(msg,1200,2,control.size());});
    set(1200);CMSG_FIRSTHDR(&msg)->cmsg_type=UDP_SEGMENT;reject([&]{parse(msg,1200,2,control.size());});
    set(1200);CMSG_FIRSTHDR(&msg)->cmsg_len=CMSG_LEN(sizeof(int))-1;reject([&]{parse(msg,1200,2,control.size());});
    set(1200);msg.msg_controllen=control.size();reject([&]{parse(msg,1200,2,control.size());});
    set(1200);reject([&]{parse(msg,1200,2,1);});
    msg.msg_controllen=0;need(parse(msg,1196,1,control.size()).count==1,"ordinary packet parse");reject([&]{parse(msg,1201,2,control.size());});
    reject([&]{plan({},1);});reject([&]{plan({Packet(1201)},1);});reject([&]{plan({Packet(1)},33);});
    auto mixed=generated(35,1196,103);mixed[0].resize(84);mixed[2].resize(84);mixed[31].resize(17);mixed.back().resize(83);
    auto groups=plan(mixed,32);need(groups[0].size()==3&&groups[0][1].count==2&&groups[0][2].count==29&&groups[1].size()==1,"mixed group boundaries");
    unsigned trips=0;std::array<bool,2> supported{true,true};
    for(unsigned which=0;which<2;++which){
        try{Sockets sockets;if(which)enable_gro(sockets);
            for(size_t count:{size_t(1),size_t(2),size_t(31),size_t(32),size_t(33),size_t(35),size_t(228)})
                for(size_t bytes:{size_t(17),size_t(64),size_t(1196),size_t(1200)})
                    for(size_t burst:{size_t(1),size_t(4),size_t(16),size_t(32)}){
                        auto packets=generated(count,bytes,5109);auto result=offload::run(sockets,packets,burst,which);
                        need(result.base.wire_bytes==count*bytes,"offload wire total");++trips;}
            for(size_t burst:{size_t(1),size_t(4),size_t(16),size_t(32)}){offload::run(sockets,mixed,burst,which);++trips;}
        }catch(const Unsupported&){supported[which]=false;}
    }
    // Source/size failure containment uses new owned loopback sockets per test.
    if(supported[1]){
        {Sockets sockets;enable_gro(sockets);Packet large(max_aggregate+2,1);
         checked(int(::send(sockets.sender.value,large.data(),large.size(),MSG_DONTWAIT)),"owned oversize injection");
         reject([&]{offload::run(sockets,generated(1,64,8),1,true);});}
        {Sockets sockets;enable_gro(sockets);Fd other{::socket(AF_INET,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0)};
         sockaddr_in source{};source.sin_family=AF_INET;source.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
         checked(::bind(other.value,reinterpret_cast<sockaddr*>(&source),sizeof(source)),"bind owned test source");Packet data(64,1);
         checked(int(::sendto(other.value,data.data(),data.size(),MSG_DONTWAIT,reinterpret_cast<sockaddr*>(&sockets.receive_address),sizeof(sockets.receive_address))),"owned alternate source injection");
         reject([&]{offload::run(sockets,generated(1,64,8),1,true);});}
    }
    std::cout<<"{\"pass\":true,\"loopback_roundtrips\":"<<trips<<",\"rejections\":"<<rejection_count<<",\"gso_supported\":"<<(supported[0]?"true":"false")<<",\"gro_supported\":"<<(supported[1]?"true":"false")<<",\"maximum_outstanding_records\":32,\"partial_final_segments\":true,\"mixed_packet_sizes\":true}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
