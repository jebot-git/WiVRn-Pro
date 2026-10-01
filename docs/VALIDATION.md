# Validation — 2026-10-01

The standalone checkout builds the PC OpenXR helper and Quest APK without a
WiVRn source checkout or the avatar application. OpenXR Android SDK artifacts
are cached under ignored quest/.deps for this development checkout.

Checks run after extraction:

- Python server suite: 13 tests passed.
- Native anchor-manager lifecycle: 44 checks passed.
- Installed WiVRn 26.9: two concurrent Python receivers shared 27 snapshots with
  valid headset and both controller poses. Restarting the helper preserved
  receiver connections.
- Stock WiVRn's D-Bus owner was unchanged; the test did not restart its server.

Additional multi-client and systemd checks:

- Python server suite: 15 tests passed, including 32 simultaneous subscribers
  receiving identical broadcasts, rejection at capacity, disconnect/slot reuse,
  and Quest provider fan-out/disconnection with three receivers kept connected.
- `systemd-analyze --user verify` accepted the generated user unit. Installed and
  enabled `spatial-bridge.service` alongside the existing `wivrn.service`, retaining
  this checkout's `.state/catalog.json` and spatial cache.
- Eight concurrent TCP subscribers to the actual service received 60 identical
  live tracking broadcasts with valid headset poses. The service owned exactly
  one shared OpenXR helper. Initial snapshots have individual timestamps and were
  excluded from broadcast equality comparisons.
- Restarted only `spatial-bridge.service`: its old helper exited, endpoint
  credentials and bridge session changed, and three new subscribers received live
  tracking. WiVRn's D-Bus owner stayed unchanged and its XR session stayed running.
- Service left enabled and running. Summary evidence:
  `evidence/spatial_bridge_service.json` (ignored by Git).
- Dependency audit: the PC server and native helper require no game engine;
  the Quest build links OpenXR, Android, EGL and GLESv3 directly.

After removing the engine-specific examples, the Python-only `live_smoke.py`
passed with three receivers sharing 11 snapshots. The late-joining receiver
received 11 valid headset samples; the original two stayed connected across the
temporary helper restart. Headset and both controller poses were valid, and
WiVRn's D-Bus owner remained unchanged. All 15 broker tests also passed.

Guided capture implementation (APK 0.2):

- Native anchor-manager suite: 69 checks passed, including frozen capture poses,
  safe retry after save failure, preservation of saved UUIDs, and late completion
  after an asynchronous create timeout.
- Guided capture suite: 707 checks passed. It simulates four scan directions,
  twelve separated markers, save/localization gates, tracking loss, motion,
  focus loss, retry/skip, pending-operation timeouts, reload drift rejection,
  full rechecks, saved-marker reuse and resuming partial captures from a different
  initial heading.
- Python broker suite: 16 tests passed, including twelve-marker progress delivery,
  archival and live-pose invalidation on pause.
- Native Android APK built successfully with the existing signing key. The HUD
  was rasterized and visually inspected; in-headset orientation/readability and
  controller bindings still require a wearer check.
- APK 0.2 installed on the connected Quest Pro with app data preserved. The PC
  service was restarted to carry `quest.capture`; WiVRn's D-Bus owner was unchanged.
  The capture activity was not launched during deployment.
- No twelve-marker hardware reliability claim is made. Run `quest_smoke.py` while
  a wearer follows the prompts; the default target is twelve verified markers.
  The smoke test permits five minutes for the guided flow and does not require
  scene permission/geometry to pass anchor verification.
- Background-service and 2D-panel feasibility were researched, not implemented.
  They can host transport/status UI but do not establish simultaneous live anchor
  localization with WiVRn. See the README for sources and the supported cache handoff.

The Quest spatial-data path was hardware-tested before extraction with three
saved/reloaded anchors, 36 scene entities and a native stage rectangle. The user
confirmed the diagnostic crosses stayed fixed during head movement. Two readers
shared 474 snapshots during a 15-second wireless ADB observation with the USB
cable physically unplugged. A non-VR PC receiver also consumed Quest data while WiVRn was
not connected to the headset.

The new checkout's APK was rebuilt and installed with the existing signing key.
Its subsequent live Quest check encountered foreground WiVRn, which pauses the
source as documented; that was not counted as a passing spatial acquisition test.
No continuous background localization is claimed. Controller alignment was added
and tested separately as described below.

Controller calibration was hardware-tested on 2026-10-01 with the Quest Pro's
stationary controllers, after the user confirmed their placement. APK 0.3 adds
grip-pose telemetry and a mode that pauses guided marker capture. It was built,
signed and installed without clearing app data. The PC helper was rebuilt and
the spatial bridge restarted to expose grip-space/profile metadata.

- Captured 81 WiVRn samples, 37 native Quest samples and 81 return WiVRn samples,
  each covering at least four seconds. Both paths reported the Oculus Touch
  interaction profile, with all four pose validity/tracking flags set.
- Controller separation: 1.02276 m. Maximum within-window position deviation:
  0.347 mm. Maximum orientation deviation: 0.046 degrees.
- Left-controller fit, right-controller held-out check: 0.137 mm positional
  residual and 0.00699 degrees angular residual. PC round-trip differences:
  0.121 mm left, 0.350 mm right.
- Measured Quest-to-PC transform: yaw -97.88188 degrees, translation
  (-0.342382, 0.000057, 0.610710) m, unit scale. This is a transform between the
  measured source sessions, not a permanent room calibration.
- Local evidence: `evidence/controller-calibration-01.json`, including raw samples
  and comparison with the earlier read-only runtime-offset probe. WiVRn was
  restored to the foreground after measurement.
- Seven initial solver tests and sixteen broker tests passed. Solver cases cover
  a known nonidentity transform, quaternion sign flips, held-out-controller
  disagreement, round-trip motion, jitter, inferred poses, session changes,
  mismatched profiles, repeated timestamps and nonfinite positions.

The residuals establish cross-app consistency at two stationary locations, not
absolute tracking accuracy or room-wide anchor stability. That initial diagnostic
report was not applied automatically.

The integrated method adds broker-side recomputation/activation through a separate
local credential, live PC-space pose views, and matching-session cached pose
views. Cached poses never gain live-valid flags. Alignment invalidation covers
provider/Quest sessions, origin generations, WiVRn owner changes and PC tracking
loss. Sparse Quest lifecycle packets retain generation only within the same
session. Scene-only captures are cached; APK 0.4 reloads existing guided markers
during calibration without creating or saving replacements.

Validation: 35 Python tests passed, covering fit/hold-out checks, authenticated
activation and clearing, multi-client delivery/late join, transform direction,
scene and boundary poses, original geometry preservation, cache provenance,
expired/malformed reports, sticky invalidation and snapshot size limits. Both
native suites passed (69 anchor-manager checks, 707 guided-capture checks).
The Android APK built and signed successfully.

The integrated hardware activation passed on 2026-10-01 after two rejected
attempts (inactive controllers, then a return-pose mismatch of 2.36 cm / 2.13
degrees). The bridge did not activate either rejected attempt. After controller
tracking settled, the successful run captured 90 PC-before, 38 Quest and 81
PC-after samples, with controllers 0.526 m apart. Held-out right-controller error
was 0.156 mm / 0.0696 degrees; PC round-trip position differences were 0.396 mm
left and 0.977 mm right. The broker accepted the independently recomputed result.
WiVRn was restored, and the same-session cache contained 36 scene entities.
Evidence: `evidence/controller-calibration-integrated-03.json`; rejected attempts
remain in the preceding `-01` and `-02` reports. No guided markers were present
in this capture, so hardware marker-view verification remains separate from the
passing synthetic marker transformation tests.

Three simultaneous PC receivers subsequently received 45 identical broadcasts
with the active calibration and 36 transformed cached scene poses. Each pose
was checked against the published transform and its original saved pose. Cached
output remained `live: false`, the paused Quest live view was unavailable, and
snapshots were 31,978 bytes. Evidence: `evidence/controller-alignment-fanout.json`.
Connection-specific initial snapshots are excluded from byte-for-byte broadcast
comparison because their send timestamps differ.

The subsequent user-guided marker capture on 2026-10-01 completed all four scan
directions and all twelve marker slots, with no skips. All twelve app-owned
anchors were saved, reloaded and verified in the native Quest stage. Two PC
receivers observed eleven shared complete/verified snapshots, and the persistent
cache contained all twelve UUIDs. The live payload included 33 scene entities
alongside the markers within the provider size budget. Evidence:
`evidence/guided-marker-capture-run.json`. This validates one complete twelve-marker
run; it does not establish a reliability maximum or long-term drift bound.

Recalibration after this capture exposed short WiVRn transport disconnects during
the Quest app handoff. Two attempts were correctly rejected when the monitor
restarted the PC helper and changed its provider identity. The monitor now retains
the helper for a ten-second reconnect grace period, immediately invalidating poses
and alignment and suppressing helper samples while transport is down. Owner
changes, helper exit and longer disconnects still restart it. All 36 Python tests
passed, including a new short-pause test proving that stale poses are withheld.

The subsequent calibration was accepted with controllers 0.747 m apart. The
held-out right-controller residual was 0.455 mm / 0.0483 degrees. Calibration mode
reloaded all twelve captured UUIDs in its new Quest source session. Two PC clients
received thirty identical broadcasts containing all twelve correctly transformed
cached markers and 33 cached scene poses, with `live: false`. Snapshots were
35,235 bytes, and the paused Quest live view remained unavailable. WiVRn was
restored and alignment was valid at the final check. Evidence:
`evidence/controller-calibration-after-markers-04.json` and
`evidence/marker-capture-recalibration-validation.json`.

Regression checks:

```sh
python3 -m unittest test_controller_calibration test_alignment_bridge test_bridge
```

Run native lifecycle checks from the repository root:

```sh
g++ -std=c++23 -Wall -Wextra -Werror -Wno-missing-field-initializers \
  -Iquest/native tests/anchor_manager_test.cpp quest/native/anchor_manager.cpp \
  -o /tmp/spatial-native-test
/tmp/spatial-native-test
g++ -std=c++23 -Wall -Wextra -Werror -Wno-missing-field-initializers \
  -Iquest/native tests/guided_capture_test.cpp -o /tmp/spatial-capture-test
/tmp/spatial-capture-test
```

Generated evidence is ignored by Git because it may contain room geometry,
anchor identities or device addresses. Hardware tests write local reports under
`evidence/`; this summary contains no credentials or marker identities.
