// SPDX-License-Identifier: MIT
#pragma once
// Reuse the committed batching comparison and owned-loopback RAII. Only its
// command entry point is omitted: the timed baseline implementation is unchanged.
#define UDP_BATCH_EMBEDDED
#include "../udp_batch/probe.cpp"
#undef UDP_BATCH_EMBEDDED
#include <netinet/udp.h>

namespace offload {
constexpr size_t max_aggregate=max_batch*max_packet;
struct Unsupported:std::runtime_error {using std::runtime_error::runtime_error;};
static void optional_error(const char*what){
    if(errno==ENOPROTOOPT||errno==EOPNOTSUPP||errno==EINVAL)
        throw Unsupported(std::string(what)+": "+std::strerror(errno));
    checked(-1,what);
}
static void enable_gro(Sockets&s){int yes=1;
    if(::setsockopt(s.receiver.value,IPPROTO_UDP,UDP_GRO,&yes,sizeof(yes))<0)optional_error("UDP_GRO unavailable");
}
struct Group {size_t first,count,segment,bytes;};
using Groups=std::vector<std::vector<Group>>;
static Groups plan(const Packets&packets,size_t burst){
    validate_input(packets,burst);Groups windows;
    for(size_t base=0;base<packets.size();base+=burst){
        size_t end=std::min(base+burst,packets.size());std::vector<Group> groups;
        for(size_t at=base;at<end;){Group g{at,1,packets[at].size(),packets[at].size()};
            while(at+g.count<end){size_t next=packets[at+g.count].size();if(next>g.segment)break;
                ++g.count;g.bytes+=next;if(next<g.segment)break;}
            need(g.count<=max_batch&&g.bytes<=max_aggregate,"GSO plan bound");groups.push_back(g);at+=g.count;
        }
        windows.push_back(std::move(groups));
    }
    return windows;
}
struct Parts {size_t segment,count,last;};
// Only UDP_GRO is enabled on these owned sockets. Reject unknown or duplicate
// ancillary records, flags indicating truncation, and impossible segment bounds.
static Parts parse(const msghdr&msg,size_t bytes,size_t remaining,size_t control_capacity){
    need(!(msg.msg_flags&(MSG_TRUNC|MSG_CTRUNC)),"truncated aggregate/control");
    need(bytes>0&&bytes<=max_aggregate&&remaining>0&&remaining<=max_batch,"aggregate bounds");
    need(msg.msg_controllen<=control_capacity,"control extent");size_t segment=bytes;
    if(msg.msg_controllen){
        need(msg.msg_control!=nullptr&&msg.msg_controllen>=CMSG_LEN(sizeof(int))&&
             msg.msg_controllen<=CMSG_SPACE(sizeof(int)),"GRO ancillary extent");
        cmsghdr c{};std::memcpy(&c,msg.msg_control,sizeof(c));
        need(c.cmsg_level==IPPROTO_UDP&&c.cmsg_type==UDP_GRO&&c.cmsg_len==CMSG_LEN(sizeof(int)),"unknown GRO ancillary");
        int size=0;std::memcpy(&size,static_cast<const uint8_t*>(msg.msg_control)+CMSG_ALIGN(sizeof(cmsghdr)),sizeof(size));
        need(size>0&&size<=int(max_packet),"GRO segment bound");segment=size;
    }
    need(segment<=max_packet,"missing GRO segment metadata");
    size_t count=(bytes+segment-1)/segment,last=bytes-(count-1)*segment;
    need(count<=remaining&&last>0&&last<=segment,"GRO record count");
    return {segment,count,last};
}
static void peer(const msghdr&m,const sockaddr_in&p,const Sockets&s){
    need(m.msg_namelen==sizeof(sockaddr_in)&&p.sin_family==AF_INET&&
         p.sin_addr.s_addr==htonl(INADDR_LOOPBACK)&&p.sin_port==s.send_address.sin_port,"unowned UDP peer");
}
struct Result {::Result base;double planning_ms=0;size_t aggregate_receives=0,max_segments=0,gso_groups=0;};

// GSO performs no wait-to-fill: each already available bounded burst is sent
// immediately. It does not pad, merge, rewrite, or truncate application records.
static Result run(Sockets&s,const Packets&packets,size_t burst,bool gro){
    validate_input(packets,burst);Packets received(packets.size(),Packet(max_packet+1));
    std::vector<size_t> lengths(packets.size());
    std::array<iovec,max_batch> vectors{};std::array<mmsghdr,max_batch> messages{};std::array<sockaddr_in,max_batch> peers{};
    std::array<uint8_t,max_aggregate+1> aggregate{};
    alignas(cmsghdr) std::array<uint8_t,CMSG_SPACE(sizeof(int))> control{};
    Result out{};auto&calls=out.base.calls;size_t base=0;
    auto start=Clock::now(),deadline=start+std::chrono::milliseconds(100);
    // Variable-length grouping and its allocations are paid per frame and
    // included, unlike already allocated input/receive storage and socket setup.
    auto windows=plan(packets,burst);auto planned=Clock::now();
    for(const auto&groups:windows){size_t window=std::min(burst,packets.size()-base),got=0;
        for(const auto&g:groups){
            for(size_t i=0;i<g.count;++i){const auto&p=packets[g.first+i];vectors[i]={const_cast<uint8_t*>(p.data()),p.size()};}
            msghdr msg{};msg.msg_iov=vectors.data();msg.msg_iovlen=g.count;
            control.fill(0);msg.msg_control=control.data();msg.msg_controllen=CMSG_SPACE(sizeof(uint16_t));
            auto*c=CMSG_FIRSTHDR(&msg);c->cmsg_level=IPPROTO_UDP;c->cmsg_type=UDP_SEGMENT;c->cmsg_len=CMSG_LEN(sizeof(uint16_t));
            uint16_t segment=uint16_t(g.segment);std::memcpy(CMSG_DATA(c),&segment,sizeof(segment));
            for(;;){need(Clock::now()<deadline,"GSO send deadline");++calls.send_calls;
                auto n=::sendmsg(s.sender.value,&msg,MSG_DONTWAIT);
                if(n<0&&errno==EINTR)continue;
                if(n<0&&(errno==EAGAIN||errno==EWOULDBLOCK)){++calls.would_block;ready(s.sender.value,POLLOUT,deadline,calls);continue;}
                if(n<0)optional_error("UDP_SEGMENT send unavailable");
                need(size_t(n)==g.bytes,"short GSO send");out.base.wire_bytes+=g.bytes;++out.gso_groups;break;
            }
        }
        while(got<window){need(Clock::now()<deadline,"GSO receive deadline");
            if(gro){
                iovec vector{aggregate.data(),aggregate.size()};sockaddr_in address{};msghdr msg{};
                msg.msg_iov=&vector;msg.msg_iovlen=1;msg.msg_name=&address;msg.msg_namelen=sizeof(address);
                control.fill(0);msg.msg_control=control.data();msg.msg_controllen=control.size();++calls.receive_calls;
                auto n=::recvmsg(s.receiver.value,&msg,MSG_DONTWAIT);
                if(n<0&&errno==EINTR)continue;
                if(n<0&&(errno==EAGAIN||errno==EWOULDBLOCK)){++calls.would_block;ready(s.receiver.value,POLLIN,deadline,calls);continue;}
                checked(int(n),"receive GRO owned socket");peer(msg,address,s);
                auto parts=parse(msg,size_t(n),window-got,control.size());
                ++out.aggregate_receives;out.max_segments=std::max(out.max_segments,parts.count);
                // Splitting/copying aggregate bytes is inside the timed interval.
                for(size_t i=0;i<parts.count;++i){size_t len=i+1==parts.count?parts.last:parts.segment;
                    std::memcpy(received[base+got+i].data(),aggregate.data()+i*parts.segment,len);lengths[base+got+i]=len;}
                got+=parts.count;
            }else{
                size_t request=window-got;
                for(size_t i=0;i<request;++i){auto&p=received[base+got+i];vectors[i]={p.data(),p.size()};messages[i]={};peers[i]={};
                    messages[i].msg_hdr.msg_iov=&vectors[i];messages[i].msg_hdr.msg_iovlen=1;messages[i].msg_hdr.msg_name=&peers[i];messages[i].msg_hdr.msg_namelen=sizeof(peers[i]);}
                ++calls.receive_calls;int n=::recvmmsg(s.receiver.value,messages.data(),unsigned(request),MSG_DONTWAIT,nullptr);
                if(n<0&&errno==EINTR)continue;
                if(n<0&&(errno==EAGAIN||errno==EWOULDBLOCK)){++calls.would_block;ready(s.receiver.value,POLLIN,deadline,calls);continue;}
                checked(n,"receive segmented owned datagrams");need(n>0&&size_t(n)<=request,"invalid segmented receive count");
                for(int i=0;i<n;++i){auto&m=messages[i];peer(m.msg_hdr,peers[i],s);
                    need(!(m.msg_hdr.msg_flags&(MSG_TRUNC|MSG_CTRUNC))&&m.msg_len>0&&m.msg_len<=max_packet,"truncated segmented packet");
                    lengths[base+got+size_t(i)]=m.msg_len;}
                got+=size_t(n);
            }
        }
        base+=window;
    }
    auto finish=Clock::now();for(size_t i=0;i<received.size();++i)received[i].resize(lengths[i]);
    need(same_packets(std::move(received),packets),"offload exact packet multiset mismatch");
    out.base.elapsed_ms=std::chrono::duration<double,std::milli>(finish-start).count();
    out.planning_ms=std::chrono::duration<double,std::milli>(planned-start).count();return out;
}
}
