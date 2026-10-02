# WiVRn Pro configuration

Server configuration merges `<prefix>/share/wivrn-pro/config.json`,
`/etc/wivrn-pro/config.json` and `$XDG_CONFIG_HOME/wivrn-pro/config.json`
(default `~/.config/wivrn-pro/config.json`), in that order. Missing user fork files are imported once from the existing WiVRn and native
Shora profiles; existing fork files take precedence. See the [carryover rules](../fork/SHORA.md#configuration-carryover).
All values are optional.

## Encoding

`encoder` accepts `vulkan`, `vaapi`, or `qsv` (requires `WIVRN_USE_QSV=ON`).
When omitted, the server probes Vulkan Video, then VAAPI, then QSV if built.
Explicit selections have no fallback to another backend. Every backend uses the
selected Intel Arc GPU. Unknown/removed encoders and AV1/raw codecs fail.

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
to the same DRM render node as the selected Arc; cross-GPU offload is rejected.
The compositor chooses the first explicitly supported Arc. The optional
`XRT_COMPOSITOR_FORCE_GPU_INDEX` override also rejects non-Arc devices.

Advanced `options` in VAAPI/QSV objects pass string values to FFmpeg. QSV defaults
to async depth 1, no B frames/lookahead, forced IDR, a veryfast preset and low-delay
rate control. Overrides can affect latency and compatibility. Runtime capability
errors are reported instead of choosing a software encoder.

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
