// SPDX-License-Identifier: MIT
#pragma once
#include <algorithm>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <utility>
#include <vector>

// Offline exact cardinality frontier for globally nondecreasing source targets.
// This has no native, device, scheduler, ownership or network dependencies.
namespace cardinality_oracle {
struct Frame { int64_t id, target, receipt, ready; };
struct Deadline { int64_t sequence, selection, submit; };
struct Choice { size_t deadline, frame; };
struct Result {
    size_t skips{}, required_count{};
    bool feasible{};
    int32_t maximum_good{-1};
    std::vector<Choice> path;
};
struct Frontier { std::vector<Result> results; size_t stored_states{}, rank_band_width{}, parent_slots{}; };
inline void need(bool condition, const char* reason) { if (!condition) throw std::invalid_argument(reason); }

inline Frontier solve(const std::vector<Frame>& frames, const std::vector<Deadline>& deadlines,
                      size_t maximum_skips, int64_t threshold) {
    const size_t n = frames.size(), d = deadlines.size();
    need(n <= 10000 && d > 0 && d <= 10000, "bounded frame/deadline population required");
    need(maximum_skips < d && maximum_skips <= 64 && threshold > 0, "invalid skip budget/threshold");
    for (size_t i = 0; i < n; ++i) {
        const auto& f = frames[i];
        need(f.id >= 0 && f.target > 0 && f.receipt > 0 && f.receipt <= f.ready, "invalid frame identity/timing");
        if (i) need(frames[i-1].id < f.id && frames[i-1].target <= f.target, "strict frame IDs and globally nondecreasing targets required");
    }
    for (size_t i = 0; i < d; ++i) {
        const auto& t = deadlines[i];
        need(t.sequence > 0 && t.selection > 0 && t.selection <= t.submit, "invalid deadline identity/timing");
        if (i) need(deadlines[i-1].sequence < t.sequence && deadlines[i-1].selection < t.selection
                    && deadlines[i-1].submit < t.submit, "strict deadline identity/time order required");
    }
    Frontier out;
    const size_t minimum_count = d - maximum_skips;
    out.results.resize(maximum_skips + 1);
    for (size_t b = 0; b <= maximum_skips; ++b) {
        out.results[b].skips = b;
        out.results[b].required_count = d - b;
    }
    if (n < minimum_count) return out;
    const size_t width = n - minimum_count + 1, budgets = maximum_skips + 1;
    need(d <= 25000000 / budgets / width, "parent-state budget exceeds 25 million slots");
    out.rank_band_width = width;
    out.parent_slots = d * budgets * width;
    constexpr uint32_t skip_bit = uint32_t(1) << 31;
    constexpr uint32_t missing = std::numeric_limits<uint32_t>::max();
    std::vector<uint32_t> parents(out.parent_slots, missing);
    std::vector<int32_t> prior(budgets*n, -1), current(budgets*n, -1);
    auto parent_slot = [=](size_t stage, size_t skips, size_t rank) {
        const size_t chosen = stage + 1 - skips;
        return (stage * budgets + skips) * width + rank - (chosen - 1);
    };
    for (size_t stage = 0; stage < d; ++stage) {
        std::fill(current.begin(), current.end(), -1);
        for (size_t b = 0; b <= std::min(maximum_skips, stage); ++b) {
            const size_t chosen = stage + 1 - b;
            if (chosen > n) continue;
            const size_t lower = chosen - 1;
            const size_t upper = std::min(n - 1, n - minimum_count + chosen - 1);
            int32_t best_prefix = -1;
            size_t best_rank = 0;
            for (size_t rank = lower; rank <= upper; ++rank) {
                // Prior layer with the same skip count has one fewer choice.
                // Its minimum possible rank is lower-1; the loop builds the
                // strict-rank prefix without admitting current-layer states.
                if (chosen > 1 && rank) {
                    const size_t previous_rank = rank - 1;
                    const auto score = prior[b*n + previous_rank];
                    if (score > best_prefix) { best_prefix = score; best_rank = previous_rank; }
                }
                int32_t score = -1;
                uint32_t link = missing;
                if (b && prior[(b-1)*n + rank] >= 0) {
                    score = prior[(b-1)*n + rank];
                    link = skip_bit | uint32_t(rank + 1);
                }
                const bool first_choice = chosen == 1;
                if (frames[rank].ready <= deadlines[stage].selection && (first_choice || best_prefix >= 0)) {
                    const auto good = int32_t(deadlines[stage].submit - frames[rank].receipt < threshold);
                    const auto candidate = (first_choice ? 0 : best_prefix) + good;
                    if (candidate >= score) {
                        score = candidate;
                        link = first_choice ? 0 : uint32_t(best_rank + 1);
                    }
                }
                if (score >= 0) {
                    current[b*n + rank] = score;
                    parents[parent_slot(stage, b, rank)] = link;
                    ++out.stored_states;
                }
            }
        }
        // Empty histories after skipping all processed deadlines are implicit;
        // chosen==1 above admits their first frame on a later deadline.
        prior.swap(current);
    }
    for (size_t b = 0; b <= maximum_skips; ++b) {
        auto& result = out.results[b];
        size_t ending = 0;
        for (size_t rank = 0; rank < n; ++rank)
            if (prior[b*n + rank] > result.maximum_good) { result.maximum_good = prior[b*n + rank]; ending = rank; }
        if (result.maximum_good < 0) continue;
        result.feasible = true;
        size_t skips = b, rank = ending;
        for (size_t stage_plus_one = d; stage_plus_one > 0; --stage_plus_one) {
            const size_t stage = stage_plus_one - 1;
            if (stage_plus_one == skips) break; // Remaining prefix is all skips.
            const auto link = parents[parent_slot(stage, skips, rank)];
            need(link != missing, "missing certificate parent");
            if (link & skip_bit) {
                need(skips > 0 && (link & ~skip_bit) == rank + 1, "invalid skip parent");
                --skips;
            } else {
                result.path.push_back({stage, rank});
                if (link) rank = link - 1;
                else need(stage == skips, "invalid first-choice parent");
            }
        }
        std::reverse(result.path.begin(), result.path.end());
        need(result.path.size() == result.required_count, "certificate cardinality differs");
        int32_t good = 0;
        for (size_t i = 0; i < result.path.size(); ++i) {
            const auto c = result.path[i];
            need(c.deadline < d && c.frame < n && frames[c.frame].ready <= deadlines[c.deadline].selection,
                 "certificate unavailable frame");
            if (i) {
                const auto before = result.path[i-1];
                need(before.deadline < c.deadline && frames[before.frame].id < frames[c.frame].id
                     && frames[before.frame].target <= frames[c.frame].target, "certificate regresses order");
            }
            good += int32_t(deadlines[c.deadline].submit - frames[c.frame].receipt < threshold);
        }
        need(good == result.maximum_good, "certificate score differs");
    }
    return out;
}
} // namespace cardinality_oracle
