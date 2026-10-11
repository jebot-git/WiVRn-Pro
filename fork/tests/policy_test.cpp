// SPDX-License-Identifier: GPL-3.0-or-later
#include "fork_policy.h"
using namespace wivrn::fork_policy;
static_assert(intel_arc(0x8086, 0x56a0));  // A770
static_assert(intel_arc(0x8086, 0xe20b));  // B580
static_assert(!intel_arc(0x8086, 0x4680)); // UHD
static_assert(!intel_arc(0x8086, 0x5695)); // Xe MAX
static_assert(!intel_arc(0x8086, 0x56c0)); // Flex
static_assert(!intel_arc(0x10de, 0x56a0));
static_assert(!intel_arc(0x1002, 0x56a0));
static_assert(!intel_arc(0x8086, 0xffff));
static_assert(quest_pro("seacliff", "Oculus"));
static_assert(quest_pro("seacliff", "Meta"));
static_assert(!quest_pro("eureka", "Oculus"));
static_assert(!quest_pro("hollywood", "Oculus"));
static_assert(!quest_pro("seacliff", "Pico"));
static_assert(encoder("qsv") && encoder("vaapi") && encoder("vulkan") && encoder(""));
static_assert(encoder("nvenc") && !encoder("x264") && !encoder("raw"));
static_assert(codec("h264") && codec("h265") && !codec("av1") && !codec("raw"));
static_assert(wifi_interface("wlan0") && !wifi_interface("lo") && !wifi_interface("usb0") && !wifi_interface("eth0"));
static_assert(hardware_gpu(0x10de, 0x2684) && hardware_gpu(0x1002, 0x744c));
static_assert(!hardware_gpu(0x8086, 0x4680) && !hardware_gpu(0x1234, 0x1111));
int main() {}
