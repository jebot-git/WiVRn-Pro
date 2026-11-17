# WiVRn Codec Lab

A standalone, procedural OpenXR scene for comparing standard AVC/HEVC, packed444 and PyroWave through WiVRn. The same scene also runs as a native Wayland/Vulkan desktop preview. It uses Godot 4 with the Mobile renderer; no plugins, downloaded models, editor import step or network service is needed.

```sh
# From the WiVRn-Pro repository. Use an existing, ready WiVRn session.
apps/codec-lab/run.sh --xr --take=hevc-baseline

# Desktop preview without opening an OpenXR session.
apps/codec-lab/run.sh --desktop
```

The launcher respects `XR_RUNTIME_JSON`; otherwise it chooses the installed WiVRn-Pro OpenXR manifest. `GODOT=/path/to/godot` selects another engine binary. Wayland is the default; `CODEC_LAB_DISPLAY_DRIVER=x11` explicitly selects X11. It does not change WiVRn settings, install an APK, restart a service or close another VR application. Close the current OpenXR application yourself before launching the XR scene.

## Test stations

| Station | Content | Inspect |
| --- | --- | --- |
| 1: Chroma and type | Nearest-filtered 1/2/4/8-source-texel stripes with approximately matched BT.709 luma; small white/red/cyan bitmap text and Unicode | Chroma loss, colour fringes, text readability, ringing |
| 2: Motion | Scrolling fine stripes, a rotating 32-spoke wheel and a moving checkerboard | Smearing, edge breakup, crawling detail and frame skips |
| 3: Gradients | GPU-generated floating-point white, near-black, RGB and narrow mid-grey ramps | Banding, black clipping, colour changes |
| Room and depth | 480 static seeded rods, 24 moving coloured fins, near moving targets and markers at 1/2/4 metres | Peripheral detail, stereo consistency, pose response and motion stability |

Source texels are **not** encoded/display pixels: their projected size depends on resolution, distance and foveation. Fine patterns intentionally alias at some viewing distances. Disabling AA in the scene removes one source of blur, but it does not disable runtime or codec filtering. The rods and geometry use inexpensive unshaded materials to keep scene rendering light.

All animation repeats every 12 seconds with seed `444420`. `--fixed-time=3` starts paused at the same phase. `--take=...` is only a manual visible label (up to 32 characters); it does not select or detect the codec. In XR, the head remains normally tracked and the scene never moves the viewer automatically.

## Controls

| Input | Action |
| --- | --- |
| Space / either controller trigger | Pause or resume |
| R / Touch B or Y / simple-controller menu | Restart the loop at zero and play |
| 0 / 1 / 2 / 3 | Desktop wide view / chroma / motion / gradients |
| Hold right mouse + move | Desktop look; WASD moves while held |
| Escape | Release mouse capture |
| Ctrl+W | Close the scene |

`--view=0`, `1`, `2` or `3` chooses an initial desktop view. The marker on the floor indicates the room origin. For VR comparisons, use a repeatable seated/standing position, then inspect targets with natural head movement. Keyboard controls remain available in XR.

## Repeatable comparison

1. Keep render/stream resolution, refresh rate, foveation, eye-tracked foveation and network conditions the same for each take. Record the actual encoder settings and bitrate separately. Different codecs do not necessarily need the same bitrate for the same quality; compare both equal-bitrate and quality-matched runs.
2. Compare the paused charts at a fixed phase, for example `--fixed-time=3`. In the headset, inspect small text and the matched-luma stripes from the same pose. Keep gaze consistent if eye-tracked foveation is enabled.
3. Restart the loop and inspect several complete cycles. Use WiVRn's real timing/traffic statistics to assess decode time, dropped frames and bandwidth. The scene's animation clock and source screenshots cannot measure motion-to-photon latency.
4. Change the codec using WiVRn's normal configuration workflow and repeat. Packed444 requires the matching experimental client/server builds described in [PACKED444.md](../../fork/PACKED444.md). This scene also works with existing installed baseline codecs.

A source-only screenshot and sidecar metadata can be captured without installing anything:

```sh
apps/codec-lab/run.sh --desktop --take=source-reference --fixed-time=3 --view=1 \
  --capture=/absolute/path/chroma-source.png
```

The app saves a PNG and `.png.json` then exits. This is the **host-rendered source**, not a captured decoded headset frame. It cannot establish codec fidelity. A client capture or optical recording is needed for that comparison.

## Bitmap assets

Labels use direct bitmap sprites with nearest sampling. The atlas contains 1,307 genuine GNU Unifont 17.0.04 glyphs, covering the included Latin/Czech, Greek, Cyrillic and Japanese samples. Unknown glyphs in a custom take label become `?`; no vector font fallback is used. The bundled bitmap subset is under GPLv2-or-later with the GNU Font Embedding Exception; see [COPYING.unifont](assets/COPYING.unifont).

To regenerate the font subset and chroma chart, supply the original Unifont BDF and Python Pillow:

```sh
python3 apps/codec-lab/tools/make_assets.py /path/to/unifont-17.0.04.bdf
```

The source BDF used locally came from DomeD's pinned `precipice-appearance-source/assets/fonts` collection. All other geometry and patterns are generated by the scene; there are no external art assets.

## Hardware smoke test

The scene was launched through the matching development WiVRn server/client on Quest Pro with packed QSV HEVC and a 200 Mbps target. Both eyes were visible in a headset-side screenshot. Actual display/video refresh was 90 Hz, despite the initial 72 Hz preference. This is a functional smoke test, not a latency or sustained-throughput qualification.
