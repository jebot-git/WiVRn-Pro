// SPDX-License-Identifier: MIT
#pragma once
#include <array>
#include <cstdint>
#include <limits>
#include <optional>
#include <stdexcept>

namespace retirement {
enum class Phase { free, decoding, ready, acquired, retiring, poisoned };
struct Token { unsigned slot; uint64_t generation, frame; bool operator==(const Token&) const = default; };
struct Slot { Phase phase=Phase::free; uint64_t generation=0,frame=0,decode=0,retire=0; };

// Single command-recording owner and one consumer submission per acquired pair.
// Counter observations must come from this ring's two distinct semaphores.
// No Vulkan operation occurs here, and callers must poison after uncertain submit.
template<unsigned Count=3> class Ring {
    static_assert(Count>=2&&Count<=16);
    std::array<Slot,Count> state{};
    uint64_t generation=0,last_frame=0,decode_submitted=0,retire_submitted=0;
    uint64_t decode_observed=0,retire_observed=0;
    bool broken=false;
    static void need(bool value,const char* message) { if(!value) throw std::logic_error(message); }
    static uint64_t next(uint64_t value) { need(value<std::numeric_limits<uint64_t>::max(),"counter exhausted"); return value+1; }
    Slot& get(Token token,Phase expected) {
        need(!broken,"ring poisoned"); need(token.slot<Count,"slot index");
        auto& s=state[token.slot];
        need(s.generation==token.generation&&s.frame==token.frame,"stale or wrong-frame token");
        need(s.phase==expected,"invalid ownership phase"); return s;
    }
public:
    const std::array<Slot,Count>& slots() const { return state; }
    uint64_t last_decode() const { return decode_submitted; }
    uint64_t last_retire() const { return retire_submitted; }
    uint64_t completed_decode() const { return decode_observed; }
    uint64_t completed_retire() const { return retire_observed; }
    bool poisoned() const { return broken; }
    void observe(uint64_t decode,uint64_t retire) {
        need(!broken,"ring poisoned");
        // Validate both domains before changing anything. Decode completion alone
        // cannot release a slot read by a submitted consumer.
        need(decode>=decode_observed&&decode<=decode_submitted,"invalid decode counter");
        need(retire>=retire_observed&&retire<=retire_submitted,"invalid retirement counter");
        for(const auto& s:state) if(s.phase==Phase::retiring&&s.retire<=retire)
            need(s.decode<=decode,"consumer completed before decode dependency");
        decode_observed=decode; retire_observed=retire;
        for(auto& s:state) {
            if(s.phase==Phase::decoding&&s.decode<=decode) s.phase=Phase::ready;
            if(s.phase==Phase::retiring&&s.retire<=retire) s.phase=Phase::free;
        }
    }
    std::optional<Token> reserve(uint64_t frame) {
        need(!broken,"ring poisoned"); need(frame>last_frame,"nonmonotonic frame");
        for(unsigned i=0;i<Count;++i) if(state[i].phase==Phase::free) {
            // Overflow checks happen before any state changes.
            auto new_generation=next(generation),new_decode=next(decode_submitted);
            generation=new_generation; decode_submitted=new_decode; last_frame=frame;
            state[i]={Phase::decoding,generation,frame,decode_submitted,0};
            return Token{i,generation,frame};
        }
        return {}; // Caller drops this incoming complete pair without queue growth.
    }
    uint64_t decode_value(Token token) { return get(token,Phase::decoding).decode; }
    std::optional<Token> acquire_latest() {
        need(!broken,"ring poisoned"); std::optional<unsigned> selected;
        for(unsigned i=0;i<Count;++i) if(state[i].phase==Phase::ready&&(!selected||state[i].frame>state[*selected].frame)) selected=i;
        if(!selected) return {};
        // These discarded pairs have completed decode and were never acquired by
        // a consumer, so only their own private decode resources are retired.
        for(unsigned i=0;i<Count;++i) if(i!=*selected&&state[i].phase==Phase::ready) state[i].phase=Phase::free;
        auto& s=state[*selected]; s.phase=Phase::acquired;
        return Token{*selected,s.generation,s.frame};
    }
    uint64_t ready_value(Token token) { return get(token,Phase::acquired).decode; }
    uint64_t consumer_submit(Token token) {
        auto& s=get(token,Phase::acquired); auto value=next(retire_submitted);
        retire_submitted=value; s.retire=value; s.phase=Phase::retiring; return value;
    }
    void cancel_unsubmitted_consumer(Token token) { get(token,Phase::acquired).phase=Phase::free; }
    void poison() noexcept { broken=true; for(auto& s:state) s.phase=Phase::poisoned; }
};
}
