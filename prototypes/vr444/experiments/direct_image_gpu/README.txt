Direct GPU decode to sampled image — isolated offline experiment

Question
Can the current expanded-input coop128dc decoder write the final optimal image,
removing its device-local RGBA buffer and buffer-to-image copy, while retaining
exact RGB444 bytes, opaque alpha, separate eyes and the existing sRGB sampling?
This explores a smaller GPU pipeline cost. It does not address the roughly one
display-period scheduling component in the latest 20.5–26.1 ms p95 live results.
Headset tests are suspended. This executable never opens OpenXR or networking.

Arms
  buffer_copy: existing integer cooperative inverse/constant shortcut, output
    storage buffer, copy into ordinary R8G8B8A8_SRGB optimal image.
  image_unorm: same inverse and shortcut; imageStore normalized code values into
    R8G8B8A8_UNORM storage view, sample through R8G8B8A8_SRGB view.
  image_uint: same inverse and shortcut; exact integer imageStore into
    R8G8B8A8_UINT storage view, sample through R8G8B8A8_SRGB view.
The two direct arms use MUTABLE_FORMAT and per-view usage restrictions (core1.1).
Their storage view only requests STORAGE, their SRGB view only SAMPLED. This
avoids requesting unsupported SRGB storage operations. There is no shader EOTF
or OETF in decode: only the later SRGB view applies EOTF to the stored code bytes.
All three arms retain the same full CPU coefficient validation before dispatch.

By default no optional device extension is enabled. --format-list explicitly
requires/enables advertised VK_KHR_image_format_list and supplies the exact base
and SRGB view formats to both the format-properties query and image creation.
This option may allow driver image-layout optimizations; it is not a guaranteed
speedup. Even a physical device exposing core1.2 must advertise the extension
for this optional path because this harness requests instance API1.1.
Optimal format features, image-format parameters, dimensions, descriptor limits,
workgroup/shared-memory limits and graphics+compute+timestamp queue are checked.
All three arms must initialize and pass for a run to pass. Unsupported capability
is an error/blocked run, never a passing skipped arm. No headset support is inferred
from desktop capability. Android ARM64 is compile checked only.

Build and CPU-only validation (no GPU/device initialization)
  bash prototypes/vr444/experiments/direct_image_gpu/build.sh
  prototypes/vr444/experiments/direct_image_gpu/build/direct-image-host \
    prototypes/vr444/experiments/direct_image_gpu/build/shaders --cpu-only --full
  python3 -m unittest discover -s prototypes/vr444/experiments/direct_image_gpu -v
Build uses the checked-in integration overlay codec.cpp/.hpp as the independent
CPU oracle; it does not depend on or modify the native integration worktree.
The shader derives from constant_fastpath/constant_decode.comp, with unchanged
wire grammar, inverse, quantizers and guarded DC shortcut. Both hosts and Android
use -Wall -Wextra -Werror. All four SPIR-V modules validate for Vulkan1.1.

Host-only correctness (coordinate GPU windows with the root task)
  VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json \
    prototypes/vr444/experiments/direct_image_gpu/build/direct-image-host \
    prototypes/vr444/experiments/direct_image_gpu/build/shaders \
    --device 0 --full --reps 1 > RESULT.json
  python3 prototypes/vr444/experiments/direct_image_gpu/audit.py RESULT.json
Optional: --format-list, --corpus DIRECTORY (recursively reads .payload files).
Corpus allocations are bounded before file reads and by aggregate wire and
expanded-slot budgets. Corpus pairs deliberately duplicate captured single-eye
payloads; generated cases use different eye patterns. Reps must be1..30.
Software devices require explicit --allow-software and are labeled in the audit.
Do not pass a headset path, install or launch the cross-compiled Android binary
while headset testing is suspended.

Coverage and lifetime
84 default generated stereo fixtures: seven shapes from1x1 through256x2,
six patterns (constant, dense integer noise, checker, all-code ramp, text-like
strokes, mixed constant/dense), Q1/Q8 plus per-tile1/8/17/1024. --full adds twelve
576x640 stereo fixtures. The ramp covers every8-bit code in each RGB channel.
CPU-only tests verify wire roundtrip and malformed last-tile rejection for each
eye. They also exercise the actual extension-admission helper with unqueried,
empty and unrelated extension lists (all reject an explicit format-list request),
an advertised matching name (accepted), and disabled optional use (no enablement).
GPU tests reject malformed right-eye data before any submission for either
eye, counted explicitly. Both valid eyes are preflighted before mapped writes.

Each arm/fixture first poisons its image to transparent black, its buffer output
to zero, its float sample output to NaN, and its separate raw readback to0x5a.
Every output texel must become the CPU oracle's exact RGBA bytes, including all
partial-tile edges. Physically separate images/descriptors and distinct generated
eye pixels check eye identity; all sampled pixels must match their own eye.
Persistent sessions are reused through patterns, quantizers and arms to exercise
replacement. A private owned queue is fenced before mapped input/command reuse;
uncertain submit/wait failure poisons the session and teardown drains its owned
device. This is not an asynchronous publication or borrowed-device integration.

Color acceptance and retained rejected run
An initial provisional max1e-3 absolute ideal-EOTF rule failed on Arc A770 code194:
ideal linear0.539479, sampled0.540627. A diagnostic rerun with buffer_copy first
failed on the identical value in the ordinary SRGB baseline. The rejected logs
are retained, rather than relabeled as passing. This demonstrated a limitation
of the provisional FP64-vs-hardware approximation check, not different direct-arm
bytes. Revised acceptance applies equally to all arms:
  1. exact CPU oracle bytes read from actual optimal images;
  2. finite0..1 sampled RGB and exactly1 alpha;
  3. ideal OETF of hardware sampled RGB must round to the original8-bit code
     (strict absolute error<0.5 code value), preserving one EOTF/OETF roundtrip;
  4. every sampled float bit in either direct arm must equal the ordinary SRGB
     baseline's output for that same fixture/eye. Later baseline repetitions
     must also reproduce those bits.
The maximum ideal-linear and encoded-code errors are reported. Hardware SRGB
approximation must not be confused with altered codec bytes or direct-only slack.

Timing boundaries and limits
Warmup runs buffer/unorm/uint once each to establish the ordinary SRGB sample
oracle. Measured arm order rotates by fixture and repetition. Validation and
input-copy durations are reported separately. GPU timestamps cover the two
decode dispatches, any output-buffer copies and sample-ready transitions. Wall
pipeline time covers command preparation/submit/fence after input copies.
Image poisoning, separate raw image readback, sampling and CPU oracle work are
outside that interval, but change cache/queue state. Oracle work has its own
timer. Build/session creation and CPU reference decode are outside all reported
pipeline timing. The audit deliberately makes no performance claim; the first
host window allowed concurrent other-team CPU correctness activity. No tails,
live queue contention, compositor interaction, thermal behavior, subjective
quality, displayed pixels or optical latency have been qualified.

2026-10-09 qualification
Arc A770 passed the 96-fixture full generated suite without optional extensions,
and 134 fixtures with format lists (96 generated plus 38 captured payloads).
After extracting/testing the fail-closed extension helper, the final host binary
passed all 84 smaller fixtures in both extension modes. All three shader arms
passed exact image bytes and bit-exact samples versus their same-device baseline.
Maximum ideal linear error was 0.00114763; maximum encoded-code error was 0.193304.
This does not impose equality between different devices/drivers; each run creates
its own ordinary SRGB reference on the device under test. Final CPU-only fixtures
and extension gates also pass ASan/UBSan (leak detection disabled for ptrace).
Three evidence-audit tests include thirteen corruption/missing-evidence mutations.
Host and Android compile checks and all four spirv-val checks pass. Small committed
qualification receipts hash sources, binaries and local raw evidence. Headset
qualification and performance promotion remain blocked.

Next stage
An isolated performance window should compare sufficient repeated stereo cases,
including sparse captures and dense scenes, with and without format lists. If
direct output is useful on the intended decoder GPU, integrate only after the
live slot owner can supply a mutable image and preserve existing publication,
queue synchronization, color views and handle lifetime. Vulkan extensions such
as host-image-copy/timeline/synchronization2 need separate capability and lifetime
gates; desktop support alone is not a client compatibility argument.

Primary references consulted2026-10-09
https://docs.vulkan.org/refpages/latest/refpages/source/VkImageViewCreateInfo.html
https://docs.vulkan.org/refpages/latest/refpages/source/VkImageViewUsageCreateInfo.html
https://docs.vulkan.org/guide/latest/extensions/VK_KHR_image_format_list.html
https://docs.vulkan.org/guide/latest/storage_image_and_texel_buffers.html
https://docs.vulkan.org/spec/latest/chapters/images.html
https://docs.vulkan.org/features/latest/features/proposals/VK_EXT_host_image_copy.html
