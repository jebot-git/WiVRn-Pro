// SPDX-License-Identifier: MIT
#include "offload.hpp"
#include <memory>
static std::string quote(const std::string&s){std::string out="\"";for(char c:s){need(c>=32,"diagnostic control character");if(c=='\\'||c=='"')out+='\\';out+=c;}return out+'"';}
int main(int argc,char**argv){try{
    need(argc==2,"usage: probe REPS");unsigned reps=std::stoul(argv[1]);need(reps>=5&&reps<=100,"repetition bound");
    std::array<std::unique_ptr<Sockets>,3> sockets;std::array<std::string,3> unavailable{};
    for(unsigned i=0;i<3;++i){try{sockets[i]=std::make_unique<Sockets>();if(i==2)offload::enable_gro(*sockets[i]);
        auto packets=generated(3,1200,12);if(i)offload::run(*sockets[i],packets,3,i==2);else run(*sockets[i],packets,3,true);
    }catch(const offload::Unsupported&e){unavailable[i]=e.what();sockets[i].reset();}}
    const char*names[]={"sendmmsg_recvmmsg","gso_recvmmsg","gso_gro_split"};
    struct Job{size_t count,bytes,burst;bool mixed;};std::vector<Job>jobs;
    for(size_t count:{size_t(2),size_t(35),size_t(228)})for(size_t bytes:{size_t(64),size_t(1200)})
        for(size_t burst:{size_t(1),size_t(4),size_t(16),size_t(32)})jobs.push_back({count,bytes,burst,false});
    for(size_t count:{size_t(35),size_t(228)})for(size_t burst:{size_t(1),size_t(4),size_t(16),size_t(32)})jobs.push_back({count,1196,burst,true});
    std::shuffle(jobs.begin(),jobs.end(),std::mt19937(8684));
    std::cout<<std::setprecision(9)<<"{\"schema\":1,\"pass\":true,\"domain\":\"owned IPv4 UDP loopback only\",\"repetitions\":"<<reps<<",\"warmups\":3,\"capabilities\":[";
    for(unsigned i=0;i<3;++i)std::cout<<(i?",":"")<<"{\"profile\":"<<quote(names[i])<<",\"supported\":"<<(sockets[i]?"true":"false")<<",\"unavailable_reason\":"<<quote(unavailable[i])<<",\"receive_buffer_bytes\":"<<(sockets[i]?sockets[i]->receive_buffer_bytes:0)<<"}";
    std::cout<<"],\"rows\":[";unsigned row=0;
    for(auto job:jobs){auto packets=generated(job.count,job.bytes,5109);
        if(job.mixed){packets[0].resize(88);packets[2].resize(88);packets[31].resize(61);packets.back().resize(83);}
        size_t wire=0;for(const auto&p:packets)wire+=p.size();std::array<std::vector<double>,3>timing,planning;
        std::array<Counters,3>counts{};std::array<size_t,3>aggregates{},max_segments{},gso_groups{};
        for(unsigned rep=0;rep<reps+3;++rep)for(unsigned j=0;j<3;++j){unsigned which=(j+rep)%3;if(!sockets[which])continue;
            offload::Result result{};if(which)result=offload::run(*sockets[which],packets,job.burst,which==2);else result.base=run(*sockets[which],packets,job.burst,true);
            need(result.base.wire_bytes==wire,"wire total mismatch");
            if(rep>=3){timing[which].push_back(result.base.elapsed_ms);planning[which].push_back(result.planning_ms);auto&c=counts[which];
                c.send_calls+=result.base.calls.send_calls;c.receive_calls+=result.base.calls.receive_calls;c.poll_calls+=result.base.calls.poll_calls;c.would_block+=result.base.calls.would_block;
                aggregates[which]+=result.aggregate_receives;max_segments[which]=std::max(max_segments[which],result.max_segments);gso_groups[which]+=result.gso_groups;}
        }
        for(unsigned which=0;which<3;++which){if(!sockets[which])continue;auto&c=counts[which];
            std::cout<<(row++?",":"")<<"{\"profile\":"<<quote(names[which])<<",\"packet_count\":"<<job.count<<",\"nominal_packet_bytes\":"<<job.bytes<<",\"mixed_sizes\":"<<(job.mixed?"true":"false")<<",\"burst\":"<<job.burst<<",\"wire_bytes_per_iteration\":"<<wire<<",\"exact\":true,\"send_calls\":"<<c.send_calls<<",\"receive_calls\":"<<c.receive_calls<<",\"poll_calls\":"<<c.poll_calls<<",\"would_block\":"<<c.would_block<<",\"aggregate_receives\":"<<aggregates[which]<<",\"max_segments_per_receive\":"<<max_segments[which]<<",\"gso_groups\":"<<gso_groups[which]<<",\"whole_loopback_including_plan_ms\":";
            distribution(timing[which]);std::cout<<",\"included_group_planning_ms\":";distribution(planning[which]);std::cout<<"}"<<std::flush;
        }
    }
    std::cout<<"]}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
