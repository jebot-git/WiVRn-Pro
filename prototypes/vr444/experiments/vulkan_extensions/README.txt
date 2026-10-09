Vulkan extension and retirement experiments — 2026-10-09

This branch is an offline stage after direct_image_gpu. No headset operation,
OpenXR session, network connection, live decoder change or latency claim occurs.
It separates extension advertisement, feature query, device enablement, exact
image usage support, and resource retirement. These are different conditions.

Implemented paths
capabilities.cpp queries physical devices only: timeline semaphore and
synchronization2 feature bits; host-image-copy feature/properties and the exact
optimal R8G8B8A8_SRGB HOST_TRANSFER|SAMPLED image query; image-format-list name;
timeline pending-value limit. It creates no logical device. Unqueried bits are
null. This experiment uses advertised EXT/KHR routes for optional operations;
it does not infer a working core-only entry-point route from a version number.

planner.py makes fail-closed decisions from this inventory. It also imports one
complete Available Vulkan device extensions section from an existing captured
log. Historical names never become feature-query or feature-enablement claims.
No queried support, wrong image usage, absent limits, unavailable features, or
inconsistent enablement cannot silently become a passing configuration.

retirement_ring.hpp implements a bounded stereo ownership prototype with three
slots, immutable frame/generation tokens and separate decode/consumer counters.
Whole input pairs are dropped under capacity pressure. Only complete decode pairs
become ready; the single consumer acquires the newest ready pair and releases
unacquired older pairs. An acquired pair stays owned until its consumer submits
and its separate retirement value completes. Decode completion cannot release a
consumer-owned pair. Stale tokens, counter regressions, counters beyond submitted
work, duplicate consumer submission and uncertain submission state fail closed.
An acquired pair may be cancelled only before any consumer submission. Poisoning
never makes uncertain resources reusable. The model has one recording owner and
one consumer submission per acquired pair; it is not a general shared_ptr model.

timeline_probe.cpp exercises that model using actual Vulkan queue submissions.
For each accepted pair, two device-local 4 KiB buffers receive independent eye
signatures derived from one immutable frame ID. A consumer submission waits on
the decode timeline, copies both eyes to a slot-owned coherent readback, emits
TRANSFER_WRITE -> HOST_READ visibility, and signals a separate retirement
timeline. Every word is checked before that generation is released for reuse.
Command buffers are persistent per slot and reset only after ownership permits.

Both vkQueueSubmit + VkTimelineSemaphoreSubmitInfo and opt-in vkQueueSubmit2KHR
are implemented. Submit2 signals at ALL_COMMANDS, retaining the legacy signal
scope; it is not assumed to accelerate the work. Counter polling reads consumer
then decode, confirms already observed values with a zero-time vkWaitSemaphores,
and then inspects host memory/releases slots. This avoids treating a pair of
independently sampled counters as an atomic snapshot. When progress is required,
a bounded 1 ms host wait targets only an already submitted next counter value.
No future-signal wait is enqueued. The private queue is externally serialized by
its sole CPU owner. Timeout/error leaves resources owned until device teardown.
All uncertain submissions poison the ring; the owned private device is drained
before destruction. Nothing waits on or destroys a borrowed application device.

Build and portable tests
  bash prototypes/vr444/experiments/vulkan_extensions/build.sh
  ASAN_OPTIONS=detect_leaks=0 \
    prototypes/vr444/experiments/vulkan_extensions/build/ring-test
  python3 -m unittest discover -s prototypes/vr444/experiments/vulkan_extensions -v
Both Vulkan programs compile on host and Android API29 ARM64 with warnings as
errors. Android binaries are not installed or executed while tests are suspended.
The ring test uses ASan/UBSan, explicit lifecycle failures, and 100,000 deterministic
producer/consumer scheduling operations. Leak detection is disabled for ptrace.

Host-only commands (no performance sampling)
  VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json \
    prototypes/vr444/experiments/vulkan_extensions/build/capabilities-host \
    > host-capabilities.json
  python3 prototypes/vr444/experiments/vulkan_extensions/planner.py host-capabilities.json
  VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json \
    prototypes/vr444/experiments/vulkan_extensions/build/timeline_probe-host
Repeat the last command with --sync2 for the second submit path. --device N selects
an enumerated host device. Hardware is required by the retirement probe; there
is no automatic software fallback. It offers 512 pairs in bursts and requires
actual pressure drops, multiple slot generations, and exact consumed-pair
accounting. The offer/drop numbers are intentionally artificial load, not live
frame-rate, useful throughput, thermal or latency evidence.

Observed decisions
Arc A770 advertises and queries timeline/synchronization2 support. Both actual
retirement paths passed exact frame/eye identity under bounded slot reuse. Five
planner tests cover ten negative gate cases plus incomplete/ambiguous historical
logs. The portable model passed 100,000 operations including 14,279 admissions,
26,059 pressure drops, 5,786 consumer submissions and 1,840 pre-submit cancellations.

Host image copy is advertised and the queried SRGB optimal usage succeeds on both
Arc A770 and UHD770. However, optimalDeviceAccess, identicalMemoryLayout and
identicalMemoryTypeRequirements all report false for that exact image query.
This does not prove host copying is slower; it means equivalence to ordinary
GPU image access is not promised. A full upload plus repeated sampling experiment
would be required before choosing it. It is lower priority than direct shader
output and palette expansion for the current client.

The existing 05-closest-traced/wivrn-logcat.txt capture from the latest headset
session advertises VK_KHR_image_format_list, VK_KHR_timeline_semaphore and
VK_KHR_synchronization2 for Adreno650 driver512.819.0. It does not list
VK_EXT_host_image_copy. The importer retains all feature and image-format fields
as unknown. No current headset query, enablement or performance is inferred.

Integration requirements and limits
This is a buffer identity/lifetime prototype, not async live video publication.
The live slot owner must retain every outstanding renderer reference and actual
consumer GPU completion, serialize access to its shared VkQueue, preserve image
layouts and acquire/release ownership, and map complete stereo IDs to one slot
generation. OpenXR submission does not by itself mean GPU consumption retired.
More slots can increase stale-work latency; bounded pressure/drop policy must be
retained and measured with compositor scheduling. The latest-complete policy here
is only a standalone owner test, not approval to replace the live selector.
Headset feature queries and worn controlled tests remain suspended and unpassed.

Primary references consulted2026-10-09
https://docs.vulkan.org/samples/latest/samples/extensions/timeline_semaphore/README.html
https://docs.vulkan.org/guide/latest/extensions/VK_KHR_synchronization2.html
https://docs.vulkan.org/spec/latest/chapters/synchronization.html
https://docs.vulkan.org/refpages/latest/refpages/source/VkPhysicalDeviceHostImageCopyFeatures.html
https://docs.vulkan.org/refpages/latest/refpages/source/VK_EXT_host_image_copy.html
https://docs.vulkan.org/features/latest/features/proposals/VK_EXT_host_image_copy.html
