Dynamic foveated VR444 quantization: offline viability experiment0.9.0

This controller produces bounded per-tile quantizers for the existing VR44 v2
format. It does not alter WiVRn, the installed headset application, a GPU shader,
or transport. No headset is needed to reproduce it. Outcomes are in
validation/results/foveation-research-0.9.0/codec/benchmark.json and REPORT.txt.

controller.py
Each eye supplies its own geometry identity, projected horizontal/vertical gaze
angles, and tile footprints. Rectilinear synthetic rays are
normalize(tan(horizontal),tan(vertical),1). The controller computes exact maximum
ray dot product to each tangent-plane rectangle using corner and edge stationary
points. A spherical cap encloses the current/predicted/previous-saccade corridor;
subtracting its radius from point-to-tile angle gives a conservative lower bound.
This avoids the off-axis underprotection caused by Euclidean projected angles or
incorrectly interpreting vertical projection angle as yaw/elevation pitch.

All tiles intersecting the core+guard+uncertainty region use Q1. Quantizers rise
outward with a smoothstep in log2(Q), rounded toward better quality, to Q32/Q128.
Whole16 x16 tiles are protected because the transform spans the tile. Edge tiles
are clipped to image bounds. Q1 preserves RGB exactly relative to encoder input;
it cannot restore detail already removed by rendering/warping. Alpha is opaque.

Defaults are experimental settings:5 degreecore,2 degreeguard,0.75 degreebase
uncertainty,90 degree/seconduncertainty growth,18 degreetransition,50 msmaximum age.
Prediction uses finite differences in projected coordinates, capped to20 ms and
5 coordinate degrees; the150/900 degree-per-second saccade/velocity knobs are
heuristic projected-coordinate rates. They are not physical angular-velocity
measurements or calibrated perceptual/Quest limits. The spherical protection
guarantee applies to the explicitly defined ROI, not unknown future true gaze.

Timestamps share one clock. sample_ns means a KNOWN physical acquisition time
or synthetic ground truth. XrEyeGazeSampleTimeEXT is an expressed pose time and
cannot be substituted as raw acquisition time; runtime-predicted data is not
supported by this prototype contract. A real integration needs separate poll,
expressed, requested-target, clock-error and uncertainty metadata and must avoid
predicting a runtime-predicted pose twice. Missing, invalid, future or >50 msold
input falls back to whole-frame Q1.65/80 ms scenarios demonstrate that fallback.
The existing measured headset-receive-to-submit p95 alone is roughly53–62 ms;
the fresh10/20/35 ms cases are counterfactual optimization scenarios, not the
current complete source-gaze-to-display path. Full-path age is longer still.

Captured host .rgba files are pristine PRE-ENCODE inputs, but already have
WiVRn's spatial foveation. Their inverse map is unavailable to this experiment.
They use explicitly labeled normalized PIXEL-SPACE proxy coordinates. Their
byte/CPU/encoder-input-error results are valid, but their ROI is not an angular
fovea. Synthetic text/edges/noise use known separate-eye asymmetric rectilinear
FOVs. Those eye projections/gaze values are artificial; per-eye IDs do not derive
real vergence/depth. No warp_rgb() encoded-byte interpolation is used.

probe.cpp and run.py
The existing unmodified CPU codec encodes, packs, unpacks and decodes each case.
Wire byte counts include the header and Q-map records. Uniform maps serialize
with a global Q, avoiding5760 bytes of unnecessary stereo Q-map overhead. Every
Q1 tile is checked for exact RGB. Matched fresh-current-gaze masks provide common
foveal/peripheral error regions for ALL policies; they are not each policy's
different expanded region. Null PSNR plus zero error means exact reconstruction.

LZ4 uses the existing WPC1 frame wrapper, including24 byteheader, block records,
CRC and raw escape. Every wrapped payload roundtrips exactly. Input payloads are
small enough to avoid the old wrapper's16 MiB nesting-cap caveat. Compression
sizes, not compression runtime, are measured here. Sums are actual independent
left+right bytes, not one eye multiplied by two.100 Mbps/90 Hz fit flags allow
138888.89 bytes per pair of MEDIA PAYLOAD ONLY; they omit transport, Wi-Fi, FEC,
retries, CPU/GPU and presentation scheduling. Over-budget frames are reported,
never quietly degraded in the lossless ROI. No base/patch protocol is implemented.

Timing: one initial encode/roundtrip plus five timed CPU RGB-transform+pack
operations per case; median and max reported. Allocation is included. File I/O,
Q-map/controller work, decode checks and LZ4 are excluded. Stereo timing is the
sum of each eye's median, not a concurrent GPU measurement. Serial host process,
clang++-O2, no pinning/thermal stabilization; descriptive results only. CPU
transform+pack itself generally misses an11.11 ms90 Hzstereo budget, so this is
not a realtime implementation or evidence of improved motion-to-photon latency.

Reproduction from workspace root:
  EXP=prototypes/vr444/experiments/dynamic_foveation
  clang++ -std=c++20 -O2 -Wall -Wextra -Werror -Wno-missing-field-initializers -I prototypes/vr444 "$EXP/probe.cpp" prototypes/vr444/codec.cpp -o /tmp/wivrn-dynamic-foveation-probe
  python3 -m unittest discover -s "$EXP" -p test_controller.py -v
  python3 "$EXP/run.py" --workspace . --out /tmp/foveation-reproduction --probe /tmp/wivrn-dynamic-foveation-probe
  clang++ -std=c++20 -O1 -g -Wall -Wextra -Werror -Wno-missing-field-initializers -fsanitize=address,undefined -fno-omit-frame-pointer -I prototypes/vr444 "$EXP/probe.cpp" prototypes/vr444/codec.cpp -o /tmp/wivrn-dynamic-foveation-sanitized
  python3 "$EXP/check_probe.py" /tmp/wivrn-dynamic-foveation-sanitized

Tests cover bounds, monotonic/quantized quality, increasing age, stale/invalid/
future fallback, moving gaze/saccade corridors, independent eye geometry, partial
tiles, off-axis ±40/70 degreeprojections, a coordinate-convention regression,
1000 random maps, and18150 independent sampled dot checks. ASan/UBSan covers20 codec
roundtrips on1 x1/17 x19/63 x65/128 x96 and12 invalid map rejections. The gaze research
agent independently sampled another30250 rays; its receipt is sibling gaze/.

Implementation priorities supported by this experiment
1. Preserve per-frame spatial mapping and stereo identity; inject a late Q mask
   only against correctly mapped source footprints. Never relabel old warped
   image pixels with a newly moved gaze center.
2. Compare complete payload AFTER LZ4; quantization patterns can destroy repeated
   codewords. Synthetic saccade text is smaller AND higher quality as uniform Q1
   than the proposed foveated profile. Do not assume compounded savings.
3. Do not enable Q128 periphery by default: large errors remain even with exact
   foveal RGB. Calibrate visible eccentricity/content/motion tolerances first.
4. Keep a deadline-aware over-budget policy explicit. All-lossless stale fallback
   can increase network/GPU load just when latency is already bad. A future safe
   response might drop an obsolete stereo pair or use a separately negotiated
   base+patch mode; this prototype only reports the condition.
5. Port a proven controller/Q-map path into GPU encoding only after source gaze
   timing/geometry and real transport/codec-stage deadlines are instrumented.

Separate policy timing: run measure_maps.py; map-timing.json reports five
samples per policy/eye/domain with cached footprint geometry. The measured
Python angular reference costs roughly 8 ms per stereo pair and is additional
to encode+pack; it is not the proposed production realtime implementation.
