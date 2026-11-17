# Experimental packed full-colour transport

`packed444_v1` carries every original 8-bit Y, Cb and Cr sample through a standard H.264 or HEVC 4:2:0 video stream. It avoids spatial chroma subsampling before encoding; the video codec remains lossy. This is an experimental alternative to PyroWave, not a native 4:4:4 decoder mode.

## Enabling

Build both server and Android client from the same revision. The wire protocol has changed, so existing installed clients and servers cannot be mixed with these builds. Android capability advertisement is currently restricted to Quest Pro (`seacliff`), where the hardware-buffer import and GPU unpack path has been tested.

The FLTK dashboard exposes **Packed full colour (experimental; restart)** in the streaming settings. The preset `packed444-hevc-experimental` selects HEVC, 8-bit output and `packed444: true`; choose an available hardware encoder backend before applying it. Ordinary presets clear this flag. Alternatively, add the following root keys to the server configuration:

```json
{
  "packed444": true,
  "bit-depth": 8,
  "encoder": [{"encoder": "qsv", "codec": "h265"}]
}
```

The setting takes effect for a new WiVRn runtime session. Save the configuration and restart WiVRn through the usual dashboard workflow between tests; reconnecting to an existing compositor keeps its current layout. It defaults to off. Use H.264 or HEVC for every stream, including alpha; PyroWave and AV1 are rejected with this layout. Start with a reduced stream resolution, 72 Hz and approximately 200 Mbps while measuring performance. No preset is activated by building the code.

## Layout and ownership

For a logical image W × H, the coded NV12 image is 2W × H:

- The left half of coded luma stores original Y. The right half stores original Cb.
- The coded Cb plane stores original Cr from even rows; coded Cr stores Cr from odd rows.
- Alpha uses the same layout at logical W × H/2, containing the two half-resolution eye alpha images, with neutral carrier chroma.

Both logical dimensions must be even. Server dimensions are aligned to 64; logical dimensions above 4096 are rejected by the foveation path. The doubled coded width must also fit the selected encoder and decoder. The stream description retains logical dimensions and explicitly identifies the layout.

Packing runs in the existing server foveation pass. It requires supported full compute subgroups; unsupported devices reject packed mode. The Android decoder uses ordinary 8-bit hardware video decoding into AHardwareBuffer, imports it with RGB-identity/nearest sampling, and reconstructs full-resolution BT.709 RGBA in Vulkan. It performs no CPU pixel copies. Foreign ownership is released and the unpack fence completes before the media buffer is recycled or the RGBA frame is published. Output images and their samplers remain alive while the renderer retains them.

## Validation and remaining work

The actual server shader has GPU regression coverage for both eyes, alpha, flipped and foveated mappings, plus standard 4:2:0 and PyroWave output. Geometry and capability/description serialization are covered by `fork/tests/wire_test.cpp`. `common/packed444/tests/README.md` describes the standalone Android hardware decode/import/unpack test.

A Quest Pro H.264 hardware decode → AHardwareBuffer → Vulkan unpack probe matched a CPU reference of the decoded carrier exactly. Host shader tests pass on Arc A770 and UHD 770. A live Quest Pro Codec Lab smoke test also passed using QSV HEVC, packed stereo colour streams at coded 1920×1024 (logical 960×1024), a coded 1920×512 alpha stream, and a 200 Mbps target. A headset-side stereo screenshot confirmed the reconstructed scene in both eyes. The headset ran at 90 Hz despite a requested 72 Hz preference; this does not qualify 72 Hz selection. These results do not qualify sustained thermal behavior, larger or padded decoder surfaces, quality at other bitrates, other encoder backends, or end-to-end latency. The doubled coded image can increase decoder load and bitrate requirements. Complete those measurements before promoting this mode or making it a default.

Run the host GPU regression explicitly from the repository root (requires Vulkan development files and `glslangValidator`):

```sh
cmake -S fork/tests/packed444-gpu -B build/packed444-gpu -DCMAKE_BUILD_TYPE=Release
cmake --build build/packed444-gpu --parallel 4
glslangValidator -V --target-env vulkan1.1 -Os -S comp server/compositor/shaders/foveation.comp -o build/packed444-gpu/foveation.spv
build/packed444-gpu/packed444-foveation-gpu-test build/packed444-gpu/foveation.spv
```

This test uses synthetic images and CPU readback for verification; it does not touch a running server or headset.
