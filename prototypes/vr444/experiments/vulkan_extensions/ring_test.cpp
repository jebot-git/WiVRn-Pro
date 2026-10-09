// SPDX-License-Identifier: MIT
#include "retirement_ring.hpp"
#include <iostream>
#include <random>
#include <vector>
using retirement::Phase;
void need(bool value,const char* message) { if(!value) throw std::runtime_error(message); }
template<class F> void rejects(F&& f) { bool rejected=false; try { f(); } catch(const std::logic_error&) { rejected=true; } need(rejected,"invalid transition accepted"); }
int main() { try {
    using Ring=retirement::Ring<3>;
    Ring r;
    auto a=*r.reserve(1),b=*r.reserve(2),c=*r.reserve(3);
    need(!r.reserve(4),"bounded capacity"); need(!r.acquire_latest(),"uncompleted pair published");
    rejects([&]{r.observe(4,0);}); need(r.completed_decode()==0,"invalid counter mutated state");
    r.observe(3,0); auto selected=*r.acquire_latest(); need(selected==c,"latest complete pair selection");
    rejects([&]{r.consumer_submit(a);}); // Older completed but unacquired pair was discarded.
    auto retire=r.consumer_submit(c); need(retire==1,"retire sequence");
    rejects([&]{r.consumer_submit(c);});
    auto d=*r.reserve(4),e=*r.reserve(5);
    need(d.slot!=c.slot&&e.slot!=c.slot,"decode-only completion released consumer-owned slot");
    need(!r.reserve(6),"pending consumer did not retain slot");
    r.observe(5,0); need(r.slots()[c.slot].phase==Phase::retiring,"consumer retirement lost");
    r.observe(5,1); auto f=*r.reserve(6); need(f.slot==c.slot,"retired slot not reusable");
    need(f.generation!=c.generation,"generation not advanced");
    rejects([&]{r.consumer_submit(c);}); rejects([&]{r.decode_value(b);});
    rejects([&]{r.observe(4,1);}); rejects([&]{r.observe(5,0);}); rejects([&]{r.reserve(6);});
    r.poison(); rejects([&]{r.reserve(7);}); rejects([&]{r.observe(6,1);}); rejects([&]{r.acquire_latest();});
    for(const auto& s:r.slots()) need(s.phase==Phase::poisoned,"uncertain submission reused");

    // Long deterministic sequences vary producer bursts, consumer retention and
    // completion observations. An independent ledger protects acquired/retiring
    // identities and counts actual slot generations across pressure and reuse.
    unsigned admitted=0,pressure=0,consumed=0,cancelled=0;
    for(unsigned seed=0;seed<100;++seed) {
        Ring ring; std::mt19937 random(seed+104729); uint64_t frame=0;
        std::vector<retirement::Token> held;
        for(unsigned step=0;step<1000;++step) {
            unsigned action=random()%5;
            if(action<=1) {
                auto before=ring.slots(); auto token=ring.reserve(++frame);
                if(token) {
                    ++admitted; need(before[token->slot].phase==Phase::free,"producer overwrote owned slot");
                    for(auto h:held) need(token->slot!=h.slot,"acquired identity reused");
                } else ++pressure;
            } else if(action==2) {
                ring.observe(ring.last_decode(),random()%2?ring.last_retire():ring.completed_retire());
            } else if(action==3) {
                if(auto token=ring.acquire_latest()) held.push_back(*token);
            } else if(!held.empty()) {
                auto token=held.front(); held.erase(held.begin());
                if(random()%4) { ring.consumer_submit(token); ++consumed; }
                else { ring.cancel_unsubmitted_consumer(token); ++cancelled; }
            }
            for(auto h:held) {
                const auto& slot=ring.slots()[h.slot];
                need(slot.phase==Phase::acquired&&slot.frame==h.frame&&slot.generation==h.generation,"held token lifetime broken");
            }
        }
        for(auto token:held) ring.consumer_submit(token);
        ring.observe(ring.last_decode(),ring.last_retire());
    }
    need(admitted&&pressure&&consumed&&cancelled,"scenario coverage");
    std::cout<<"{\"pass\":true,\"sequences\":100,\"steps\":100000,\"admitted\":"<<admitted<<",\"pressure_drops\":"<<pressure<<",\"consumer_submissions\":"<<consumed<<",\"cancelled_before_submit\":"<<cancelled<<",\"headset_used\":false}\n";
    return 0;
} catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; } }
