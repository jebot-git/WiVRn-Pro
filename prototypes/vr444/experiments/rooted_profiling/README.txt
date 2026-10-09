Standalone Vulkan clock probe (research 0.10.0)

Build from workspace root:
  bash prototypes/vr444/experiments/rooted_profiling/build.sh OUT_DIRECTORY
  OUT_DIRECTORY/probe-host > host.json

probe-android targets arm64 Android API 29 using NDK 29.0.14206865 by default.
The parent test runner can copy it to a new /data/local/tmp scratch directory,
chmod 755 it, run it with no arguments, capture stdout, and remove that scratch
directory afterward. ADB and device mutation are deliberately outside this probe.
Root is unnecessary. No headset wearing or eye-tracking permission is required.

The probe requests a Vulkan 1.1 instance, enumerates every physical device, and
records timestampPeriod (nanoseconds/device tick), timestampComputeAndGraphics,
queue-family flags/counts/timestampValidBits, and KHR/EXT extension availability.
It prefers KHR when advertised. EXT and KHR timestamp types and commands are
specification aliases. Advertised entry points are loaded through the instance
and logical-device dispatch appropriate to each command. Enumeration retries
VK_INCOMPLETE at most eight times. Devices below Vulkan 1.1 are reported as
unsupported instead of attempting an unenabled properties2 dependency.

If both DEVICE and CLOCK_MONOTONIC are available, it creates a minimal VkDevice
with one queue and the selected timestamp extension. No optional feature bits
are enabled; this extension has no feature structure. It collects 32 consecutive
calibration calls without sleeping, submitting work, or waiting for GPU work.
The driver call itself is synchronous; this is not a hard nonblocking guarantee.
Each pair includes raw device ticks, raw CLOCK_MONOTONIC nanoseconds and the
driver's strictly-positive maxDeviation in nanoseconds. uint64 values are decimal
JSON strings so JavaScript readers cannot silently lose precision. Device values
must be scaled by timestampPeriod before dimensional comparisons. The probe
does not calculate offsets, host-GPU elapsed durations, or presentation latency.

Unsupported extension/domain combinations return a status and empty sample list.
Initialization/enumeration failure produces a valid JSON error and process exit 1.
Per-device calibration errors produce calibration.status=error while permitting
other devices to be inspected; readers must inspect that status, not only exit 0.
No queues are fetched/submitted, and no GPU resources or OpenXR objects are made.
Logical devices are destroyed after the query, including on exceptions.

Interpretation
This checks timestamp observability, not codec timing, prediction accuracy,
motion-to-photon latency, or root performance. maxDeviation bounds uncertainty
of one calibration, not a measured pipeline delay. Clocks can drift; any future
integration must recalibrate, preserve queue-valid-bit wrapping semantics, and
carry uncertainty. Consecutive idle calls are not representative of loaded VR.
The standalone VkDevice is separate from the WiVRn application's VkDevice.
CLOCK_MONOTONIC is local to each machine; comparing PC to headset requires a
separate network clock relation and its uncertainty. Root traces can help align
local scheduling events but do not themselves supply cross-device or optical time.

Primary references, accessed 2026-10-08
https://docs.vulkan.org/refpages/latest/refpages/source/VK_KHR_calibrated_timestamps.html
  Device-extension dependency is Vulkan 1.1 or KHR_get_physical_device_properties2.
https://docs.vulkan.org/refpages/latest/refpages/source/VkTimeDomainKHR.html
  Device ticks, POSIX monotonic nanoseconds, and KHR/EXT alias definitions.
https://docs.vulkan.org/refpages/latest/refpages/source/vkGetCalibratedTimestampsKHR.html
  Unique queried domains, maximum deviation units, and recalibration requirements.
https://docs.vulkan.org/features/latest/features/proposals/VK_EXT_calibrated_timestamps.html
  Design motivation, monotonicity and drift limitations.
