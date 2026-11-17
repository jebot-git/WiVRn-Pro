# Android packed-colour GPU import test

`packed444-android-import-test` is an explicit, Android-only hardware test. It is
excluded from normal builds and installations. It feeds a synthetic 512×256
H.264 packed frame to the Qualcomm hardware decoder through an
`AImageReader` PRIVATE output, imports its hardware buffer into Vulkan, and
compares GPU-unpacked 256×256 RGBA pixels against a decoded planar YUV444
reference. CPU readback is used only for the comparison.

```
packed444-android-import-test packed.h264 decoded-reference.yuv444
```

The reference contains three contiguous 256×256 byte planes, Y then Cb then Cr.
Generate it by decoding the exact H.264 sample and reversing packed444_v1:
coded luma left/right become Y/Cb; coded Cb/Cr become even/odd rows of Cr.
Use explicitly matched full-range input and output metadata when creating the
sample. A reference from before lossy encoding cannot isolate GPU unpacking
error from codec quantization error.

The test checks opaque alpha and maximum RGB difference at most one 8-bit code
value from the BT.709 reference. On the rooted Quest Pro, the initial 512×256
PRIVATE AHB (external format 506) produced an exact match with RGB_IDENTITY and
nearest filtering. Larger geometry, padding and other devices require separate
qualification; this test does not establish stereo throughput or latency.
