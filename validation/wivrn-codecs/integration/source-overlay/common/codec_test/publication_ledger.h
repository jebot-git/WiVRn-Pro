// SPDX-License-Identifier: MIT
#pragma once
#include "selection_trace.h"
#include <memory>
#include <stdexcept>

namespace wivrn::codec_test::publication_ledger {
using Frame = selection_trace::Frame;
using History = std::array<Frame, selection_trace::decoder_count * selection_trace::history_slots>;
inline constexpr std::size_t record_limit = 16384;

inline bool equal(const Frame& a, const Frame& b) {
    return a.present == b.present && a.frame == b.frame && a.warp == b.warp &&
        a.view_display_time == b.view_display_time && a.receive == b.receive &&
        a.ready == b.ready && a.alpha == b.alpha;
}
inline bool valid(const Frame& f) {
    return f.present ? f.frame <= uint64_t(INT64_MAX) && f.view_display_time > 0 &&
        f.receive > 0 && f.ready >= f.receive : equal(f, Frame{});
}
struct Event {
    uint64_t epoch = 0;
    bool reset = false;
    std::size_t decoder = 0;
    Frame added{}, evicted{};
};
static_assert(std::is_trivially_copyable_v<Event>);

// Construct before callback workers start. Call reset(), publish() and mark()
// only while holding the same external frames mutex. Records never change
// after their complete assignment, and the count advances only afterward.
// A mark captured under that mutex synchronizes all marked records with the
// reader. The render thread may then read only that immutable prefix while
// callback threads append later, distinct array elements.
template<std::size_t Capacity = record_limit>
class Ledger {
    static_assert(Capacity > 0 && Capacity <= record_limit);
    std::unique_ptr<std::array<Event, Capacity>> events = std::make_unique<std::array<Event, Capacity>>();
    uint64_t count = 0, epoch = 0, lost = 0;
    bool healthy = true;
    void reject() noexcept {
        healthy = false;
        if (lost < uint64_t(INT64_MAX)) ++lost;
    }
    void append(const Event& event) noexcept {
        if (count == Capacity) { reject(); return; }
        (*events)[count] = event;
        ++count;
    }
public:
    class Mark {
        const Ledger* owner = nullptr;
        uint64_t count = 0, epoch = 0, lost = 0;
        bool healthy = false;
        friend class Ledger;
    public:
        uint64_t records() const noexcept { return count; }
        uint64_t generation() const noexcept { return epoch; }
        uint64_t dropped() const noexcept { return lost; }
        bool valid() const noexcept { return healthy; }
    };
    static constexpr std::size_t capacity = Capacity;
    Ledger() = default;
    Ledger(const Ledger&) = delete;
    Ledger& operator=(const Ledger&) = delete;
    void reset() noexcept {
        if (epoch == uint64_t(INT64_MAX)) { reject(); return; }
        ++epoch;
        append(Event{.epoch = epoch, .reset = true});
    }
    void publish(std::size_t decoder, const Frame& added, const Frame& evicted) noexcept {
        if (!epoch || decoder >= selection_trace::decoder_count || !added.present || !valid(added) || !valid(evicted)) {
            reject(); return;
        }
        append(Event{.epoch = epoch, .decoder = decoder, .added = added, .evicted = evicted});
    }
    Mark mark() const noexcept {
        Mark result;
        result.owner = this; result.count = count; result.epoch = epoch;
        result.lost = lost; result.healthy = healthy;
        return result;
    }
    bool owns(const Mark& mark) const noexcept { return mark.owner == this; }
    const Event& at(const Mark& mark, uint64_t index) const {
        // Do not inspect mutable count/epoch here: this is the unlocked reader.
        if (mark.owner != this || index >= mark.count || index >= Capacity)
            throw std::out_of_range("publication prefix access outside captured mark");
        return (*events)[index];
    }
};

// Render-thread owned. Flush each checkpoint before serializing the matching
// candidate trace record. This exports a prefix without needing a destructor
// or claiming a complete process tail; I/O remains outside frames_mutex.
template<std::size_t Capacity = record_limit>
class Exporter {
    uint64_t written = 0, last_selection = 0;
    bool started = false, healthy = true;
    const Ledger<Capacity>* owner = nullptr;
public:
    bool write(std::ostream& out, const Ledger<Capacity>& ledger,
               const typename Ledger<Capacity>::Mark& mark, uint64_t selection,
               const History& history) {
        if (!healthy || (owner && owner != &ledger) || !ledger.owns(mark) || !selection || selection != last_selection + 1 || mark.records() < written) {
            healthy = false;
            return false;
        }
        owner = &ledger;
        // Any stream exception or failed prefix access leaves export sticky-failed.
        // A partial prefix must never be retried as if nothing was written.
        healthy = false;
        if (!started) {
            out << "{\"kind\":\"header\",\"schema\":1,\"capacity\":" << Capacity
                << ",\"clock\":\"client_xr_ns\",\"coverage\":\"scene_start_through_last_selection_checkpoint\"}\n";
            started = true;
        }
        for (; written < mark.records(); ++written) {
            const auto& event = ledger.at(mark, written);
            out << "{\"kind\":\"" << (event.reset ? "reset" : "publish") << "\",\"sequence\":" << written + 1
                << ",\"epoch\":" << event.epoch;
            if (!event.reset) {
                out << ",\"decoder\":" << event.decoder << ",\"added\":{";
                selection_trace::write_frame(out, event.added);
                out << "},\"evicted\":{";
                selection_trace::write_frame(out, event.evicted);
                out << '}';
            }
            out << "}\n";
        }
        out << "{\"kind\":\"checkpoint\",\"selection_sequence\":" << selection
            << ",\"prefix_records\":" << mark.records() << ",\"epoch\":" << mark.generation()
            << ",\"dropped\":" << mark.dropped() << ",\"valid\":" << (mark.valid() ? "true" : "false")
            << ",\"history\":[";
        for (std::size_t i = 0; i < history.size(); ++i) {
            if (i) out << ',';
            out << "{\"decoder\":" << i / selection_trace::history_slots << ",\"slot\":" << i % selection_trace::history_slots << ',';
            selection_trace::write_frame(out, history[i]); out << '}';
        }
        out << "]}\n";
        out.flush();
        last_selection = selection;
        healthy = bool(out) && mark.valid();
        return healthy;
    }
};
} // namespace wivrn::codec_test::publication_ledger
