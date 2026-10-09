// SPDX-License-Identifier: MIT
#include "codec_test/test_mode.h"
#include <cassert>
int main(){
 unsetenv("WIVRN_CODEC_TEST");assert(!wivrn::codec_test::enabled());
 for(const char* mode:{"vulkan-vr444","hevc-carrier","raw-rgba","rrb32-q1","rrb32-vr444"}){
  setenv("WIVRN_CODEC_TEST",mode,1);
#ifdef WIVRN_CODEC_TESTS
  assert(wivrn::codec_test::enabled());
#else
  assert(!wivrn::codec_test::enabled());
#endif
 }
 setenv("WIVRN_CODEC_TEST","rrb32-unknown",1);assert(!wivrn::codec_test::enabled());
}
