# WiVRn Pro configuration

Server configuration merges `<prefix>/share/wivrn-pro/config.json`,
`/etc/wivrn-pro/config.json` and `$XDG_CONFIG_HOME/wivrn-pro/config.json`
(default `~/.config/wivrn-pro/config.json`), in that order. Missing user fork files are imported once from the existing WiVRn and native
Shora profiles; existing fork files take precedence. See the [carryover rules](../fork/SHORA.md#configuration-carryover).
All values are optional.

## Encoding

`encoder` accepts `vulkan`, `vaapi`, `nvenc`, or `qsv` (requires `WIVRN_USE_QSV=ON`).
When omitted, Arc retains Vulkan Video → VAAPI → QSV preference. NVIDIA prefers
NVENC and AMD prefers VAAPI, then falls back to the other compiled hardware paths.
Explicit selections have no fallback to another backend. Every backend uses the
selected supported GPU. QSV still requires Intel Arc. Unknown/removed encoders and AV1/raw codecs fail.

`codec` accepts `h264` or `h265` (HEVC). Automatic codec choice follows the
Quest Pro decoder's supported preference order. `bit-depth` defaults to 8;
HEVC may use 10 bits when supported by both peers and the selected encoder.

```json
{
  "encoder": { "encoder": "qsv", "codec": "h265" },
  "bit-depth": 8
}
```

The encoder object applies to all eye/alpha streams. A three-element list may
specify each stream independently. `device` for VAAPI/QSV, if set, must resolve
to the same DRM render node as the selected GPU; cross-GPU offload is rejected.
The compositor chooses the first supported Arc, AMD or NVIDIA device. The optional
`XRT_COMPOSITOR_FORCE_GPU_INDEX` override also rejects unsupported vendors and non-Arc Intel devices.

Advanced `options` in VAAPI/QSV objects pass string values to FFmpeg. QSV defaults
to async depth 1, no B frames/lookahead, forced IDR, a veryfast preset and low-delay
rate control. Overrides can affect latency and compatibility. Runtime capability
errors are reported instead of choosing a software encoder.

## Tracking bridge and xrizer

`"tracking-mode": "slimevr"` is the default. `"motoc"` selects Lighthouse and
turns off the SlimeVR proxy, acquisition and SolarXR output for that worker.
Select the mode in the GUI and restart WiVRn; the active mode cannot change
mid-session. Motoc settings are saved under `[motoc]` in the Shora profile.
See [Motoc and experimental anchor continuation](../fork/PORTABILITY.md).

In the **Motoc / Lighthouse** tab, **Use Lighthouse controllers when available**
enables Index and other Lighthouse controllers alongside the WiVRn headset.
It defaults on (`"motoc-lighthouse-controllers": true`); each hand without a
detected Lighthouse controller keeps its headset controller. Turn it off to use
headset controllers with Lighthouse body trackers. Save **WiVRn settings**, then
restart WiVRn; power on paired controllers before startup. This option only
affects Motoc bridge mode and uses the same playspace calibration as trackers.

`"xrizer-auto-detect": true` with no `openvr-compat-path` searches installed
xrizer directories under `/usr/local/lib64`, `/usr/local/lib`, `/usr/lib64`,
`/usr/lib`, common Debian multiarch directories and `/opt`. A candidate must
contain `bin/linux64/vrclient.so`. An explicit path or `null` retains precedence.
The GUI exposes default discovery, system xrizer, manual path and disabled modes.

## CBR and variable-traffic CQP presets

The streaming GUI loads the existing Arc profiles for QSV, NVENC or VAAPI.
Select the target backend beside the preset list. CBR profiles retain their
headset bitrate instructions; the host preset does not change the headset slider.
CQP uses `"options": {"rate_control": "cqp", "qp": "24"}` for AVC/HEVC, with
integer QP 1–51. This is constant quantization with variable, **uncapped** traffic,
not average-bitrate VBR. The headset bitrate slider does not cap CQP. NVENC maps
it to CONSTQP; VAAPI to CQP; QSV uses QSCALE. QSV private options are stripped
when applying presets to NVENC/VAAPI. Unsupported capability errors are reported;
CQP is not silently converted to CBR. Vulkan CQP is rejected. Defaults remain CBR.

## `application`
Default value: unset

An application to start when connection with the headset is established, can be a string or an array of strings if parameters need to be provided.

### Example
```json
{
	"application": ["steam", "steam://launch/275850/VR"]
}
```
Launch No Man's Sky in VR mode on Steam when connection with headset is established.

## WayVR worker

WayVR runs from the bundled upstream source build, starting with the WiVRn runtime
and stopping when that runtime ends. The supervisor restarts crashes with a capped
backoff; normal Quit stays stopped until the next session or **Restart WayVR** in
the Qt dashboard. WayVR's own Restart returns to this supervisor. A worker failure
does not restart streaming or Shora. A briefly disconnected headset can leave the
runtime alive, so WayVR follows the runtime lifetime rather than every Wi-Fi event.

```json
{
  "wayvr": {
    "enabled": true,
    "dashboard": true,
    "args": ["--show"]
  }
}
```

Both booleans default to true. `dashboard` opens the unified Qt dashboard in a
WayVR-owned 1440×1000 virtual Wayland display without changing saved autostart
settings. `--no-autostart` also suppresses that automatic dashboard launch.
Allowed arguments are `--show`, `--headless`, `--no-autostart`, `--no-audio`,
`--openxr` and `--wait`; OpenXR and waiting are always enforced by the supervisor.
Unsupported arguments fail with a dashboard status message. Existing unrelated
WiVRn application autostart entries still launch alongside WayVR.

Direct `application` entries naming WayVR, WlxOverlay-S or their recognized
AppImage filenames are replaced at launch by the bundled worker. Their supported
arguments carry over unless `wayvr.args` is explicitly set. The saved entry and
original files are not rewritten, and an explicitly disabled worker does not
launch the old AppImage. Shell-wrapper commands are not interpreted; remove a
WayVR launch from such a wrapper if it would also start a duplicate overlay.
An already running external WayVR is left alone; the bundled worker exits instead
of using `--replace` or `--multi`.

WayVR continues using `$XDG_CONFIG_HOME/wayvr` (default `~/.config/wayvr`), including
its `conf.d` settings, window sets, screen-capture tokens, layouts and assets.
No profile conversion or copy is necessary. Changes made inside WayVR use its
normal shared configuration files. Desktop portals may require screen access to
be authorized again for the new executable.

Save settings in the Qt dashboard, then use **Restart WayVR / apply saved overlay
settings**, or reconnect for the next runtime session. Status reports the worker
process lifecycle; it does not certify XR/desktop capture readiness. Diagnostics
are in the service journal and `$XDG_RUNTIME_DIR/wivrn-pro/wayvr.log`.
Restarting WayVR closes its virtual displays and apps, including a dashboard
opened inside it; the new worker reopens the dashboard if enabled.

## `tcp-only`
Default value: `false`

Only use TCP for communications with the client, this may have increased latency.
If `false` or unset, WiVRn will use both TCP and UDP.

### Example
```json
{
	"tcp-only": true
}
```

## `publish-service`
Default value: `avahi`

How to publish the service over the network, `avahi` or null.

If set to null, service will not be published and address has to be entered manually on the headset.

## `openvr-compat-path`
Default value: unset

Provides the path to the directory of an OpenVR compatibility tool (such as OpenComposite).

If unset, WiVRn will autodetect the path of such a tool as usual (see [the SteamVR guide](./steamvr.md)).

If set to an null, WiVRn will not manage the OpenVR configuration.

## `hid-forwarding`
Default value: `false`

Only available when the `uinput` kernel module is loaded and the user has write access.

Mirrors input devices forwarded from the headset (keyboard, mouse, gamepad) to `uinput`
devices, for applications that do not read them through OpenXR. Which devices are forwarded is
chosen on the headset.

A forwarded gamepad is also exposed as a native OpenXR gamepad at `/user/gamepad`, which needs
no permission and is always available. Only the digested controller state is forwarded
(buttons, two sticks, analog triggers and a d-pad), so device specific features such as gyro,
adaptive triggers or the touchpad are not available.

## `debug-gui`
Default value: `false`

Only available when built with `WIVRN_FEATURE_DEBUG_GUI`.

Enables the Monado debug gui.


## `port`
Default value: `9757`

Change the TCP/UDP port used for the connection.

## `hostname`
Default value: unset

If set, overrides the name displayed in the server list.

### Shora batteries in WayVR

The built-in wrist panel and dashboard Home tab read the existing Shora status
hook directly from the supervised backend. No `shora-wayvr` helper, Python panel
or separate service is needed. The wrist shows up to six trackers; Home shows up
to sixteen, with overflow counts. Batteries at 20% or below are highlighted.
Inactive trackers show `offline`; absent or invalid readings show `unknown`.
Extension trackers do not inherit another sensor's battery value.

The worker receives `WIVRN_SHORA_CONTROL_SOCKET` from its supervisor. A separate
reader polls every 500 ms, checks the socket peer UID and bounds responses. The
render thread reads a cache; failures clear it and readings older than three
seconds display `disconnected / stale`. Existing custom wrist XML can override
the built-in layout; add a label with `_source="shora_batteries"` to such layouts
to opt into the same feed. User WayVR profiles are preserved.

### Hand input in WayVR

Enable hand tracking in the Quest and WiVRn client settings, then put the
controllers aside. WayVR requests `XR_EXT_hand_tracking` joints through WiVRn;
no separate KWin process or additional Quest helper is required. Controller input
returns when the runtime switches back to the controller interaction profile.

- The wrist panel follows the configured hand's wrist (left by default), on the
  back of the hand. Turn it toward you and point at it with the other hand.
- Point using the runtime's hand aim pose, or a shoulder-to-index ray when no
  aim pose is available.
- Pinch thumb–index to click. Hold it while moving to drag inside an application.
- Pinch thumb–middle for the middle mouse button.
- On the offhand (the watch hand, left by default), pinch thumb, index and middle
  together to space-drag. Move that wrist while holding the chord, then separate
  either fingertip from the thumb to release. Existing WayVR space-drag axis lock,
  multiplier, world-offset and gravity settings apply.
- Point at a movable window, close your hand into a grip, then move it in 3D.
  Open the hand to drop it. The grip moves that window rather than its whole group.
- Pinch thumb–ring to right-click. Open the hand briefly between gestures.
- Touch the wrist watch or virtual keyboard with an extended index fingertip.
  Approach from the front, touch the surface, then withdraw to release. Use the
  opposite hand for the watch. Touch takes priority over that hand's distant ray;
  after leaving the panel, release any pinch before clicking with the ray again.
- The mouse cursor follows the hand ray's projected surface hit without requiring
  a movement modifier or pausing after a click.

The gesture design comes from the local KWin prototype: pinch distances are
normalized to hand width, with 0.28 press / 0.42 release thresholds, a 45 ms press
debounce and 100 ms neutral rearming. Rays use 35 ms smoothing. Lowering the palm
more than 50 cm below the headset, or bringing it within 12 cm of the headset's
forward plane, disables pointing while retaining a valid wrist attachment.
A held grip continues below the pointing zone and follows the wrist independently
of the runtime aim pose. Index plus two other curled fingers held for 80 ms start
it; opening two fingers for 100 ms releases it. Brief finger occlusion is tolerated
for up to 120 ms while the wrist is still tracked. Wrist loss, focus loss and
input-source changes release clicks and grabs. Valid wrists remain attached when
finger joints temporarily become unavailable.
WiVRn expires hand samples after 150 ms without fresh production timestamps.

Existing action files are preserved; missing hand aim bindings are filled in
memory. Saved attachment offsets and scale remain applied relative to the wrist
attachment frame. Hand gestures replace native pinch bindings while in hand mode,
preventing duplicate clicks; existing controller mappings remain in use otherwise.
The wrist orientation follows the
[OpenXR hand-joint convention](https://registry.khronos.org/OpenXR/specs/1.1/html/xrspec.html#convention-of-hand-joints).

Direct touch uses the same surface projection as ray input, including window
rotation, scale and curvature. It arms when the fingertip is at least 14 mm in
front of the surface, presses within 4 mm after 25 ms, and releases after pulling
back 14 mm. Leaving the target, losing fingertip tracking or penetrating more
than 20 mm releases the contact. Each hand has independent touch state; appearing
inside a panel cannot press it. The watch's hand attachment includes the corrected
90° roll while retaining saved controller-relative offsets.

The offhand space-drag chord debounces for 80 ms and releases after either pinch
opens for 60 ms. Offhand single-pinches wait 120 ms to allow assembling the chord
without a premature click; the other hand retains its 45 ms mouse-pinch debounce.
Release to neutral between gestures. Space drag uses wrist displacement even when
the aim pose disappears, suppresses that hand's touch/cursor interactions while
held, and releases on tracking loss. A closed fist still selects window grip.

### PyroWave desktop bitrate

The desktop streaming panel exposes **PyroWave bitrate** in Mbit/s. The
experimental preset loads 400 Mbit/s; edit this value before saving. Zero
(**Use headset setting**) removes the desktop override. The JSON key
`pyrowave-bitrate` accepts 1,000,000–1,000,000,000 bits/s and controls the
aggregate target for both eyes and alpha. It applies only to PyroWave, persists
across connections, and takes priority over subsequent headset bitrate changes.
Saving updates an active PyroWave session without restarting VRChat. Actual
traffic can vary with content; this target is not a network throughput guarantee.

### Experimental PyroWave foveation

**PyroWave foveation (reconnect)** in the desktop streaming panel overrides the
headset stream scale for this codec. Save and reconnect to change dimensions.
`pyrowave-foveation` accepts an integer from 0 to 80: 0 disables spatial
foveation, while 30 requests 70% of each render axis before 64-pixel alignment.
**Use headset setting** removes the override. Other codecs are unchanged.

For Quest Pro's raw OpenXR recommendation of 1440×1584 per eye, 30% encodes
1024×1152, compared with padded 1472×1600 at 0%. This cuts encoded eye pixels
by about 50%, not necessarily network traffic at a fixed bitrate target.
The compositor preserves 1:1 detail around the fovea and reduces peripheral
sampling. The existing GPU defoveator restores the original render dimensions.
Full chroma is retained at encoded sample positions; peripheral spatial detail
is reduced. The existing eye-gaze path moves the center when eye tracking is
enabled and the headset manual foveation-center override is off; otherwise the
existing fixed center is used. The preset defaults to 0%, pending headset QA.
