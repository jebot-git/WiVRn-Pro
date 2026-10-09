// SPDX-License-Identifier: MIT
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>
#include <arpa/inet.h>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

using Clock=std::chrono::steady_clock;
using Packet=std::vector<uint8_t>;
using Packets=std::vector<Packet>;
constexpr size_t max_packet=1200,max_packets=512,max_batch=32;
static void need(bool ok,const char* what){if(!ok)throw std::runtime_error(what);}
static void checked(int result,const char* what){if(result<0)throw std::runtime_error(std::string(what)+": "+std::strerror(errno));}
struct Fd {
    int value=-1;
    explicit Fd(int fd):value(fd){checked(fd,"socket");}
    ~Fd(){if(value>=0)::close(value);}
    Fd(const Fd&)=delete;Fd&operator=(const Fd&)=delete;
};
struct Sockets {
    Fd receiver{::socket(AF_INET,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0)};
    Fd sender{::socket(AF_INET,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0)};
    sockaddr_in receive_address{},send_address{};
    int receive_buffer_bytes=0;
    Sockets(){
        receive_address.sin_family=AF_INET;receive_address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
        checked(::bind(receiver.value,reinterpret_cast<sockaddr*>(&receive_address),sizeof(receive_address)),"bind loopback receiver");
        socklen_t size=sizeof(receive_address);checked(::getsockname(receiver.value,reinterpret_cast<sockaddr*>(&receive_address),&size),"receiver address");
        send_address.sin_family=AF_INET;send_address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
        checked(::bind(sender.value,reinterpret_cast<sockaddr*>(&send_address),sizeof(send_address)),"bind loopback sender");
        size=sizeof(send_address);checked(::getsockname(sender.value,reinterpret_cast<sockaddr*>(&send_address),&size),"sender address");
        checked(::connect(sender.value,reinterpret_cast<sockaddr*>(&receive_address),sizeof(receive_address)),"connect owned loopback");
        size=sizeof(receive_buffer_bytes);checked(::getsockopt(receiver.value,SOL_SOCKET,SO_RCVBUF,&receive_buffer_bytes,&size),"receive buffer");
        need(receive_address.sin_addr.s_addr==htonl(INADDR_LOOPBACK)&&send_address.sin_addr.s_addr==htonl(INADDR_LOOPBACK),"non-loopback address");
    }
};
struct Counters {uint32_t send_calls=0,receive_calls=0,poll_calls=0,would_block=0;};
struct Result {double elapsed_ms;Counters calls;size_t wire_bytes;};
static void ready(int fd,short event,Clock::time_point deadline,Counters& counters){
    for(;;){auto now=Clock::now();need(now<deadline,"owned socket deadline expired");
        auto ns=std::chrono::duration_cast<std::chrono::nanoseconds>(deadline-now).count();
        int timeout=int(std::min<int64_t>(5,(ns+999999)/1000000));pollfd p{fd,event,0};++counters.poll_calls;
        int result=::poll(&p,1,timeout);if(result<0&&errno==EINTR)continue;checked(result,"poll owned socket");
        if(result==0)continue;
        need(!(p.revents&(POLLERR|POLLHUP|POLLNVAL)),"poll socket failure");
        if(p.revents&event)return;
    }
}
static Packets generated(size_t count,size_t bytes,uint32_t seed){
    need(count>0&&count<=max_packets&&bytes>=16&&bytes<=max_packet,"generated bounds");
    Packets packets(count,Packet(bytes));std::mt19937 rng(seed);
    for(size_t i=0;i<count;++i){for(auto&b:packets[i])b=uint8_t(rng());
        packets[i][0]='B';packets[i][1]='A';packets[i][2]='T';packets[i][3]='1';
        for(unsigned j=0;j<4;++j){packets[i][4+j]=uint8_t(i>>(8*j));packets[i][8+j]=uint8_t(count>>(8*j));}
    }
    return packets;
}
static void validate_input(const Packets& packets,size_t burst){
    need(!packets.empty()&&packets.size()<=max_packets&&burst>0&&burst<=max_batch,"packet/burst bounds");
    size_t total=0;for(auto&p:packets){need(!p.empty()&&p.size()<=max_packet,"packet size bound");total+=p.size();}
    need(total<=max_packets*max_packet,"pair byte bound");
}
static bool same_packets(Packets actual,Packets expected){std::sort(actual.begin(),actual.end());std::sort(expected.begin(),expected.end());return actual==expected;}

// One thread with at most burst (<=32) outstanding packets. There is no waiting
// to fill a batch: a final partial batch is sent immediately. This intentionally
// measures syscall/loopback work, not independently scheduled endpoints or WiFi.
static Result run(Sockets& sockets,const Packets& packets,size_t burst,bool batched,
                  size_t forced_send_cap=max_batch,size_t forced_receive_cap=max_batch){
    validate_input(packets,burst);Packets received(packets.size(),Packet(max_packet+1));
    need(forced_send_cap>0&&forced_send_cap<=max_batch&&forced_receive_cap>0&&forced_receive_cap<=max_batch,"test syscall caps");
    std::vector<size_t> lengths(packets.size());Counters counters;size_t total_bytes=0;
    std::array<iovec,max_batch> vectors{};std::array<mmsghdr,max_batch> messages{};std::array<sockaddr_in,max_batch> peers{};
    auto start=Clock::now(),deadline=start+std::chrono::milliseconds(100);
    for(size_t base=0;base<packets.size();base+=burst){
        size_t window=std::min(burst,packets.size()-base),sent=0,got=0;
        while(sent<window){
            need(Clock::now()<deadline,"send deadline expired");
            size_t request=batched?window-sent:1;
            for(size_t i=0;i<request;++i){auto&p=packets[base+sent+i];vectors[i]={const_cast<uint8_t*>(p.data()),p.size()};messages[i]={};messages[i].msg_hdr.msg_iov=&vectors[i];messages[i].msg_hdr.msg_iovlen=1;}
            ++counters.send_calls;
            int n;
            if(batched)n=::sendmmsg(sockets.sender.value,messages.data(),unsigned(std::min(request,forced_send_cap)),MSG_DONTWAIT);
            else {auto bytes=::sendmsg(sockets.sender.value,&messages[0].msg_hdr,MSG_DONTWAIT);n=bytes<0?-1:1;if(bytes>=0)messages[0].msg_len=unsigned(bytes);}
            if(n<0&&errno==EINTR)continue;
            if(n<0&&(errno==EAGAIN||errno==EWOULDBLOCK)){++counters.would_block;ready(sockets.sender.value,POLLOUT,deadline,counters);continue;}
            checked(n,"send owned datagrams");need(n>0&&size_t(n)<=request,"invalid send count");
            for(int i=0;i<n;++i){need(messages[i].msg_len==packets[base+sent+size_t(i)].size(),"short UDP send");total_bytes+=messages[i].msg_len;}
            sent+=size_t(n); // Explicit partial-send handling; unsent records remain owned.
        }
        while(got<window){
            need(Clock::now()<deadline,"receive deadline expired");
            size_t request=batched?window-got:1;
            for(size_t i=0;i<request;++i){auto&p=received[base+got+i];vectors[i]={p.data(),p.size()};messages[i]={};peers[i]={};messages[i].msg_hdr.msg_iov=&vectors[i];messages[i].msg_hdr.msg_iovlen=1;messages[i].msg_hdr.msg_name=&peers[i];messages[i].msg_hdr.msg_namelen=sizeof(peers[i]);}
            ++counters.receive_calls;int n;
            if(batched)n=::recvmmsg(sockets.receiver.value,messages.data(),unsigned(std::min(request,forced_receive_cap)),MSG_DONTWAIT,nullptr);
            else {auto bytes=::recvmsg(sockets.receiver.value,&messages[0].msg_hdr,MSG_DONTWAIT);n=bytes<0?-1:1;if(bytes>=0)messages[0].msg_len=unsigned(bytes);}
            if(n<0&&errno==EINTR)continue;
            if(n<0&&(errno==EAGAIN||errno==EWOULDBLOCK)){++counters.would_block;ready(sockets.receiver.value,POLLIN,deadline,counters);continue;}
            checked(n,"receive owned datagrams");need(n>0&&size_t(n)<=request,"invalid receive count");
            for(int i=0;i<n;++i){auto&m=messages[i];auto&p=peers[i];
                need(!(m.msg_hdr.msg_flags&(MSG_TRUNC|MSG_CTRUNC))&&m.msg_len>0&&m.msg_len<=max_packet,"truncated/oversized UDP packet");
                need(m.msg_hdr.msg_namelen==sizeof(sockaddr_in)&&p.sin_family==AF_INET&&p.sin_addr.s_addr==htonl(INADDR_LOOPBACK)&&p.sin_port==sockets.send_address.sin_port,"unowned UDP peer");
                lengths[base+got+size_t(i)]=m.msg_len;
            }
            got+=size_t(n); // Every recvmmsg result may be a partial batch.
        }
    }
    auto finish=Clock::now();
    // Exact multiset verification is deliberately outside the timed interval.
    for(size_t i=0;i<received.size();++i)received[i].resize(lengths[i]);
    need(same_packets(std::move(received),packets),"datagram content/count mismatch");
    return {std::chrono::duration<double,std::milli>(finish-start).count(),counters,total_bytes};
}
template<class Fn>static void rejects(Fn fn){bool bad=false;try{fn();}catch(const std::runtime_error&){bad=true;}need(bad,"expected rejection");}
static int selftest(){
    rejects([&]{validate_input({},1);});rejects([&]{validate_input({Packet(1201)},1);});rejects([&]{validate_input({Packet(1)},33);});rejects([&]{validate_input({{}},1);});
    rejects([&]{validate_input(Packets(max_packets+1,Packet(1)),1);});rejects([&]{validate_input({Packet(1)},0);});
    auto packets=generated(35,1200,7008);Sockets sockets;unsigned trips=0;
    for(size_t burst:{size_t(1),size_t(4),size_t(16),size_t(32)})for(bool batched:{false,true}){auto r=run(sockets,packets,burst,batched);need(r.wire_bytes==35*1200,"wire count");++trips;}
    auto partial=run(sockets,packets,32,true,2,3);need(partial.calls.send_calls==18&&partial.calls.receive_calls==12,"partial-batch retry counters");++trips;
    auto odd=packets;odd.pop_back();need(!same_packets(odd,packets),"missing datagram accepted");odd=packets;odd[0][19]^=1;need(!same_packets(odd,packets),"corruption accepted");
    std::array<uint8_t,1201> b{};iovec v{b.data(),b.size()};msghdr msg{};msg.msg_iov=&v;msg.msg_iovlen=1;
    need(::recvmsg(sockets.receiver.value,&msg,MSG_DONTWAIT)<0&&(errno==EAGAIN||errno==EWOULDBLOCK),"nonblocking drain");
    Counters c;rejects([&]{ready(sockets.receiver.value,POLLIN,Clock::now()+std::chrono::milliseconds(2),c);});
    {Sockets isolated;Fd other{::socket(AF_INET,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0)};
     sockaddr_in foreign{};foreign.sin_family=AF_INET;foreign.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
     checked(::bind(other.value,reinterpret_cast<sockaddr*>(&foreign),sizeof(foreign)),"bind test local source");
     checked(int(::sendto(other.value,packets[0].data(),packets[0].size(),MSG_DONTWAIT,reinterpret_cast<sockaddr*>(&isolated.receive_address),sizeof(isolated.receive_address))),"test foreign source");
     rejects([&]{run(isolated,packets,4,true);});}
    {Sockets isolated;Packet oversized(1400,1);checked(int(::send(isolated.sender.value,oversized.data(),oversized.size(),MSG_DONTWAIT)),"test oversized source");
     rejects([&]{run(isolated,packets,4,true);});}
    std::cout<<"{\"pass\":true,\"loopback_roundtrips\":"<<trips<<",\"partial_final_batch\":true,\"forced_partial_syscalls\":true,\"empty_queue_timeout_checked\":true,\"malformed_input_bounds_checked\":true,\"foreign_peer_and_truncation_rejected\":true}\n";return 0;
}
static void distribution(std::vector<double> values){auto samples=values;std::sort(values.begin(),values.end());std::cout<<"{\"p50\":"<<values[(values.size()-1)/2]<<",\"p95\":"<<values[(95*values.size()+99)/100-1]<<",\"max\":"<<values.back()<<",\"samples\":[";for(size_t i=0;i<samples.size();++i)std::cout<<(i?",":"")<<samples[i];std::cout<<"]}";}
int main(int argc,char**argv){try{
    if(argc==2&&std::string(argv[1])=="self-test")return selftest();
    need(argc==2,"usage: probe self-test | REPS");unsigned reps=std::stoul(argv[1]);need(reps>=5&&reps<=100,"repetition bound");
    struct Job{size_t count,bytes,burst;};std::vector<Job> jobs;
    for(size_t count:{size_t(2),size_t(35),size_t(228)})for(size_t bytes:{size_t(64),size_t(1200)})for(size_t burst:{size_t(1),size_t(4),size_t(16),size_t(32)})jobs.push_back({count,bytes,burst});
    std::shuffle(jobs.begin(),jobs.end(),std::mt19937(9221));Sockets sockets;
    std::cout<<std::setprecision(9)<<"{\"schema\":1,\"pass\":true,\"domain\":\"owned IPv4 UDP loopback only\",\"repetitions\":"<<reps<<",\"warmups\":3,\"receive_buffer_bytes\":"<<sockets.receive_buffer_bytes<<",\"rows\":[";
    unsigned row=0;
    for(auto job:jobs){auto packets=generated(job.count,job.bytes,7008);std::array<std::vector<double>,2> timing;std::array<Counters,2> totals;
        for(unsigned rep=0;rep<reps+3;++rep)for(unsigned j=0;j<2;++j){unsigned which=(rep+j)%2;auto r=run(sockets,packets,job.burst,which);if(rep>=3){timing[which].push_back(r.elapsed_ms);auto&t=totals[which];t.send_calls+=r.calls.send_calls;t.receive_calls+=r.calls.receive_calls;t.poll_calls+=r.calls.poll_calls;t.would_block+=r.calls.would_block;}}
        for(unsigned which=0;which<2;++which){const auto&t=totals[which];std::cout<<(row++?",":"")<<"{\"packet_count\":"<<job.count<<",\"packet_bytes\":"<<job.bytes<<",\"burst\":"<<job.burst<<",\"batched\":"<<(which?"true":"false")<<",\"wire_bytes_per_iteration\":"<<job.count*job.bytes<<",\"exact\":true,\"send_calls\":"<<t.send_calls<<",\"receive_calls\":"<<t.receive_calls<<",\"poll_calls\":"<<t.poll_calls<<",\"would_block\":"<<t.would_block<<",\"whole_loopback_iteration_ms\":";distribution(timing[which]);std::cout<<"}"<<std::flush;}
    }
    std::cout<<"]}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
