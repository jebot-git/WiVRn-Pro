// SPDX-License-Identifier: MIT
#include "codec_test/publication_ledger.h"
#include <atomic>
#include <cassert>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <sstream>
#include <thread>

namespace p = wivrn::codec_test::publication_ledger;
using wivrn::codec_test::selection_trace::Frame;

Frame frame(uint64_t id, int64_t ready = 20) {
    return Frame{true, id, id + 10, int64_t(80 + id * 20), 10, ready, false};
}
template<std::size_t N>
void publish(p::Ledger<N>& ledger, p::History& history, std::size_t decoder, Frame added) {
    auto* first = history.data() + decoder * 3;
    const auto evicted = first[0];
    first[0] = first[1]; first[1] = first[2]; first[2] = added;
    ledger.publish(decoder, added, evicted);
}
template<class Function>
void throws(Function&& f) {
    bool rejected = false;
    try { f(); } catch (const std::out_of_range&) { rejected = true; }
    assert(rejected);
}

int main(int argc, char** argv) {
    if (argc == 2 && (std::string(argv[1]) == "--fixture" || std::string(argv[1]) == "--abrupt-fixture")) {
        p::Ledger<> ledger;
        p::History history{};
        ledger.reset();
        for (unsigned eye = 0; eye < 2; ++eye)
            for (unsigned id = 1; id < 3; ++id) publish(ledger, history, eye, frame(id));
        p::Exporter<> exporter;
        assert(exporter.write(std::cout, ledger, ledger.mark(), 1, history));
        if (std::string(argv[1]) == "--abrupt-fixture") std::_Exit(0);
        return 0;
    }
    {
        p::Ledger<8> ledger, other;
        p::History history{};
        assert(ledger.mark().valid() && ledger.mark().records() == 0);
        ledger.reset();
        for (unsigned i = 1; i <= 7; ++i) publish(ledger, history, 0, frame(i));
        const auto before = ledger.mark();
        assert(before.records() == 8 && before.generation() == 1 && before.valid());
        assert(ledger.at(before, 4).evicted.frame == 1);
        throws([&] { (void)ledger.at(before, 8); });
        throws([&] { (void)other.at(before, 0); });
        publish(ledger, history, 0, frame(8));
        ledger.reset();
        assert(!ledger.mark().valid() && ledger.mark().dropped() == 2 && ledger.mark().records() == 8);
        assert(before.valid() && before.dropped() == 0 && ledger.at(before, 7).added.frame == 7);
        p::Exporter<8> exporter;
        std::ostringstream text;
        assert(!exporter.write(text, ledger, ledger.mark(), 1, history));
        assert(text.str().find("\"dropped\":2,\"valid\":false") != std::string::npos);
        assert(!exporter.write(text, ledger, before, 2, history));
    }
    {
        p::Ledger<8> ledger, other;
        p::History history{};
        ledger.reset();
        publish(ledger, history, 0, frame(1));
        auto prefix = ledger.mark();
        ledger.reset();
        history.fill({});
        publish(ledger, history, 0, frame(1)); // Same identity in a new epoch.
        assert(prefix.records() == 2 && prefix.generation() == 1);
        assert(ledger.mark().generation() == 2 && ledger.at(ledger.mark(), 3).epoch == 2);
        p::Exporter<8> exporter;
        std::ostringstream text;
        assert(exporter.write(text, ledger, ledger.mark(), 1, history));
        assert(!exporter.write(text, ledger, prefix, 2, history)); // Prefix cannot regress.
        p::Exporter<8> foreign;
        assert(!foreign.write(text, ledger, other.mark(), 1, history));
        p::Exporter<8> spliced;
        assert(spliced.write(text, ledger, ledger.mark(), 1, history));
        other.reset();
        for (unsigned i = 1; i <= 3; ++i) other.publish(0, frame(i), {});
        assert(!spliced.write(text, other, other.mark(), 2, history));
        p::Exporter<8> skipped;
        assert(!skipped.write(text, ledger, ledger.mark(), 2, history));
        p::Exporter<8> failed_output;
        text.setstate(std::ios::badbit);
        assert(!failed_output.write(text, ledger, ledger.mark(), 1, history));
        text.clear();
        assert(!failed_output.write(text, ledger, ledger.mark(), 2, history));
        p::Exporter<8> throwing;
        std::ostringstream broken;
        broken.exceptions(std::ios::badbit);
        try { broken.setstate(std::ios::badbit); } catch (const std::ios_base::failure&) {}
        bool failed = false;
        try { (void)throwing.write(broken, ledger, ledger.mark(), 1, history); }
        catch (const std::ios_base::failure&) { failed = true; }
        assert(failed);
        broken.clear();
        assert(!throwing.write(broken, ledger, ledger.mark(), 1, history));
    }
    {
        for (unsigned bad = 0; bad < 6; ++bad) {
            p::Ledger<4> ledger;
            if (bad != 0) ledger.reset();
            auto added = frame(1), evicted = Frame{};
            if (bad == 2) added.present = false;
            if (bad == 3) added.ready = added.receive - 1;
            if (bad == 4) evicted.frame = 1;
            if (bad == 5) added.frame = UINT64_MAX;
            ledger.publish(bad == 1 ? 3 : 0, added, evicted);
            assert(!ledger.mark().valid() && ledger.mark().dropped() == 1);
        }
    }
    {
        p::Ledger<> ledger;
        p::History history{};
        std::mutex mutex;
        std::atomic<unsigned> finished = 0;
        std::atomic<bool> start = false;
        uint64_t serial = 0;
        ledger.reset();
        auto worker = [&](unsigned decoder) {
            while (!start.load()) std::this_thread::yield();
            for (unsigned i = 1; i <= 1000; ++i) {
                std::lock_guard lock(mutex);
                publish(ledger, history, decoder, frame(i, 20 + int64_t(++serial)));
            }
            ++finished;
        };
        std::jthread left(worker, 0), right(worker, 1);
        start = true;
        p::Exporter<> exporter;
        std::ostringstream text;
        uint64_t selections = 0;
        do {
            p::Ledger<>::Mark prefix;
            p::History snapshot;
            {
                std::lock_guard lock(mutex);
                prefix = ledger.mark(); snapshot = history;
            }
            assert(exporter.write(text, ledger, prefix, ++selections, snapshot));
            std::this_thread::yield();
        } while (finished.load() != 2);
        left.join(); right.join();
        p::Ledger<>::Mark final;
        { std::lock_guard lock(mutex); final = ledger.mark(); }
        assert(final.records() == 2001 && final.valid());
        assert(exporter.write(text, ledger, final, ++selections, history));
        assert(text.str().find("\"sequence\":2001,") != std::string::npos);
    }
    std::cout << "publication ledger bounds, immutable marks, reset epochs and two-writer prefix export passed\n";
}
