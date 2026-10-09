// SPDX-License-Identifier: MIT
#include "codec_test/layer_filter.h"
#include <cassert>
int main() {
    using namespace wivrn::codec_test;
    for (uint64_t flags : {0, 4, 8}) {
        assert(submitted_filter_flags(flags, true, true) == flags);
        assert(submitted_filter_flags(flags, false, true) == 0);
        assert(submitted_filter_flags(flags, true, false) == 0);
    }
    layer_filter_telemetry state;
    assert(state.observe({8,8,true,true}));
    assert(!state.observe({8,8,true,true}));
    assert(state.observe({4,4,true,true}));
    assert(state.observe({0,0,true,true}));
    assert(state.observe({8,0,false,true}));
    assert(state.observe({8,8,true,true}));
    state.reset();
    assert(state.generation == 1);
    assert(state.observe({8,8,true,true}));
    assert(!state.observe({8,8,true,true}));
}
