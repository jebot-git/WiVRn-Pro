Android microphone capture and software filtering

The default audio backend always requests AAudio UNPROCESSED capture, mono
signed 16-bit PCM, with LOW_LATENCY performance. Filter selection never changes
the Android input preset. Server demand still stops/starts capture when PC readers
disappear/return; historical keep-alive is not enabled.

Reason: Quest Pro Android 14 downgraded VOICE_COMMUNICATION to normal input flags
and Qualcomm HAL usecase 23 (audio-record). A system_server recorder attributed
to com.oculus.nux.ota also uses 23. After a VRChat world-load reader gap that
recorder took ownership, and WiVRn's restart produced repeated duplicate-usecase
errors and zero PCM despite a successful AAudio start. UNPROCESSED received FAST
input/usecase 31 (low-latency-record), allowing both captures to coexist.
Requested LOW_LATENCY is not a guarantee; negotiated performance/sharing are logged.

User setting (existing JSON key mic_unprocessed_audio is retained)
  Unprocessed audio ON: copy raw capture PCM without applying a software filter.
  Unprocessed audio OFF: apply SpeexDSP 1.2.1 software noise suppression, -18 dB.
No software acoustic echo canceller is claimed or enabled: that requires a
properly synchronized playback reference and delay handling. AGC and VAD gating
are disabled. This is not an acoustic equivalent of Android voice processing.
The UI states this difference. Existing setting values and app data are retained.
Changing the setting takes effect when the streaming audio object is recreated.

Implementation
cmake/MicrophoneDSP.cmake pins the official SpeexDSP tarball by SHA-256 and builds
its portable floating-point preprocessing subset. Its BSD notice is included in
the Android package under licenses/SpeexDSP.txt. The server and wire protocol
have no new dependency or data-format change.

The AAudio callback only copies samples into a preallocated bounded SPSC queue
and notifies a worker. The worker performs filtering and encrypted packet sends.
10 ms mono frames are assembled from arbitrary callback sizes in filtered mode.
Raw mode forwards available chunks without the filter/frame-assembly delay.
Speex's overlap processing adds approximately one frame of algorithmic delay;
actual end-to-end latency and subjective voice quality require device testing.
Packets retain the first sample's capture timestamp, adjusted when splitting.

Queue capacity is seven chunks, each at most 10 ms. The worker drops chunks older
than 30 ms rather than accumulating a growing delay. Queue overflow is counted.
Stop/resume changes a capture epoch; queued old audio and partial filter frames
are discarded and filter adaptation is reset. Discontinuities from dropped chunks
also reset assembly/adaptation. An already in-flight network send may complete.
Send errors are surfaced to the AAudio callback to stop capture; the worker stays
alive for a later explicit capture start. Supported negotiated mono rates are
8-192 kHz in multiples of 100 Hz. The target Quest Pro path uses 48 kHz.

The negotiated input rate/channel count is checked before forwarding. AAudio
start/stop result, state before/after, queue drops, capture profile and filtering
mode are logged outside the real-time callback. No PCM is logged or stored.
Both raw and software-filtered paths use UNPROCESSED hardware capture; there is
no fallback to the conflicting VOICE_COMMUNICATION preset.

Tests
  cmake -S fork/tests/microphone -B build/microphone-tests -G Ninja
  cmake --build build/microphone-tests --parallel 2
  ctest --test-dir build/microphone-tests --output-on-failure

The first configure fetches the hash-pinned dependency. For a cached/offline build
pass -DFETCHCONTENT_SOURCE_DIR_SPEEXDSP=/path/to/speexdsp-1.2.1. Android uses the
normal FetchContent cache. GitHub Actions runs the standalone DSP/lifecycle tests.
Tests cover exact raw PCM forwarding, arbitrary callback sizes, partial-frame
discard on pause, bounded-queue overflow, stale epochs, stationary-noise reduction,
foreground tone retention, a second negotiated rate, and sender-failure recovery.
Clang ASan+UBSan and TSan runs are supported via standard CMake flags.

Device acceptance (required before claiming the microphone regression is fixed)
1. Back up the current APK. Build a same-package/same-certificate local test APK
   with the same versionCode for reversible replacement; do not uninstall.
2. Set Unprocessed audio OFF, reconnect/restart the headset client. Check logs:
   preset=UNPROCESSED, software_denoise=true, negotiated LOW_LATENCY, and HAL
   low-latency-record. Confirm actual speech reaches VRChat.
3. With only VRChat reading and no diagnostic probe, change worlds three times.
   Verify its reader actually disappears and the source suspends during at least
   one transition. Inspect for duplicate-usecase failures on WiVRn's input.
4. Quit/relaunch VRChat with a reader-free idle gap. Then test actual OpenXR
   STOPPING/READY sleep/wake transitions separately. Mark video failures separately.
5. Compare voice quality, noise reduction, echo and latency against raw mode.
   Do not treat a synthetic tone test as proof of intelligibility or echo control.
6. Preserve failure evidence before recovery. Restore the backed-up APK using
   adb install -r if needed; app data and the host installation remain intact.
