// SPDX-License-Identifier: MIT
#pragma once
#include "wire.h"
#include <algorithm>
#include <array>
#include <condition_variable>
#include <map>
#include <mutex>
#include <optional>
#include <utility>

namespace wivrn::codec_test {
// One generation owns one coordinator. Workers pull their own jobs; this class
// never stores callbacks or decoder pointers. Payload memory is bounded by two
// active eyes plus at most two pending frame IDs (four pending eye payloads).
template<class Job> class StereoAdmission {
public:
    struct Stats {
        uint64_t offered_eyes=0, admitted_pairs=0, completed_pairs=0, failed_pairs=0;
        uint64_t dropped_eyes=0, stale_eyes=0, duplicate_eyes=0, mismatched_pairs=0;
        uint64_t abandoned_pairs=0, pending_dropped_pairs=0, orphan_dropped_eyes=0;
        size_t pending_frames=0;
        bool active=false, closed=false;
    };
private:
    struct Entry { Header header; Job job; };
    using Pair=std::array<std::optional<Entry>,2>;
    mutable std::mutex mutex;
    std::condition_variable cv;
    std::map<uint64_t,Pair> pending;
    Pair active;
    std::optional<uint64_t> active_frame, retired_through;
    std::array<bool,2> taken{}, done{};
    bool active_failed=false, closed=false;
    Stats stats;
    static size_t count(const Pair& pair) { return size_t(bool(pair[0]))+size_t(bool(pair[1])); }
    static bool compatible(const Header& a,const Header& b) {
        return a.kind==b.kind && a.width==b.width && a.height==b.height && a.frame==b.frame && a.warp==b.warp;
    }
    void discard_pending(const Pair& pair) {
        auto eyes=count(pair);stats.dropped_eyes+=eyes;
        if(eyes==2)++stats.pending_dropped_pairs;else stats.orphan_dropped_eyes+=eyes;
    }
    void retire(uint64_t frame) {
        if(!retired_through || frame>*retired_through)retired_through=frame;
        for(auto it=pending.begin();it!=pending.end()&&it->first<=frame;){
            discard_pending(it->second);it=pending.erase(it);
        }
    }
    void admit() {
        if(closed || active_frame)return;
        auto chosen=pending.end();
        for(auto it=pending.rbegin();it!=pending.rend();++it)
            if(it->second[0]&&it->second[1]){chosen=std::prev(it.base());break;}
        if(chosen==pending.end())return;
        active_frame=chosen->first;active=std::move(chosen->second);pending.erase(chosen);
        retire(*active_frame);taken={};done={};active_failed=false;++stats.admitted_pairs;
        cv.notify_all();
    }
    void bound_pending() {
        // Retain the newest complete pair even if newer orphan frames arrive.
        // Otherwise a consistently early eye can evict every decodable pair.
        while(pending.size()>2){
            auto protected_pair=pending.end();
            for(auto it=pending.rbegin();it!=pending.rend();++it)
                if(it->second[0]&&it->second[1]){protected_pair=std::prev(it.base());break;}
            auto victim=pending.begin();if(victim==protected_pair)++victim;
            discard_pending(victim->second);pending.erase(victim);
        }
    }
public:
    bool submit(Header header,Job job) {
        if(header.eye>1)throw std::invalid_argument("stereo admission eye must be 0 or 1");
        std::lock_guard lock(mutex);++stats.offered_eyes;
        if(closed){++stats.dropped_eyes;return false;}
        if(retired_through && header.frame<=*retired_through){
            ++stats.dropped_eyes;
            if(active_frame && header.frame==*active_frame)++stats.duplicate_eyes;else ++stats.stale_eyes;
            return false;
        }
        auto& pair=pending[header.frame];
        if(pair[header.eye]){++stats.dropped_eyes;++stats.duplicate_eyes;return false;}
        if(pair[1-header.eye] && !compatible(header,pair[1-header.eye]->header)){
            ++stats.mismatched_pairs;++stats.dropped_eyes;
            // Retire both halves and older pending data; a late duplicate of a
            // corrupt identity must never reconstruct that rejected frame ID.
            retire(header.frame);return false;
        }
        pair[header.eye].emplace(Entry{header,std::move(job)});
        admit();bound_pending();return true;
    }
    std::optional<Job> wait_take(unsigned eye) {
        if(eye>1)throw std::invalid_argument("stereo admission eye must be 0 or 1");
        std::unique_lock lock(mutex);
        cv.wait(lock,[&]{return closed || (active_frame && !taken[eye]);});
        if(closed)return {};
        taken[eye]=true;
        auto result=std::move(active[eye]->job);active[eye].reset();
        return result;
    }
    void finish(unsigned eye,uint64_t frame,bool success) {
        std::lock_guard lock(mutex);
        if(eye>1 || !active_frame || *active_frame!=frame || !taken[eye] || done[eye])
            throw std::logic_error("invalid stereo completion");
        done[eye]=true;active_failed|=!success;
        if(done[0]&&done[1]){
            if(active_failed)++stats.failed_pairs;else ++stats.completed_pairs;
            active_frame.reset();admit();
        }
        cv.notify_all();
    }
    void stop() {
        std::lock_guard lock(mutex);
        if(closed)return;
        closed=true;
        for(const auto& [frame,pair]:pending)discard_pending(pair);
        pending.clear();
        if(active_frame){++stats.abandoned_pairs;stats.dropped_eyes+=count(active);active={};}
        cv.notify_all();
    }
    Stats snapshot() const {
        std::lock_guard lock(mutex);auto copy=stats;
        copy.pending_frames=pending.size();copy.active=bool(active_frame);copy.closed=closed;return copy;
    }
};
}
