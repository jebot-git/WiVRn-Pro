Conditional native foveation allocation prediction
================================================

The existing 1152x1280 profile is the next bounded coverage candidate. Under
stated fixed-gaze inputs, the actual extracted native allocator predicts full
one-to-one allocation across the conservative central 10-degree cone. This is
not a headset, pixel-equality, wire-admission or visible-quality pass.

Observed input: three completed stereo captures from the 960x1056 Q1 chroma-grid
run, manifest 229c1c1d5a90dae9456ed9e494a6a771ded423cdf847cc2416ae5eae8275ddb3.
The six exact metadata files and captured provenance are digest-checked before
and after analysis. Typed stereo identities must agree, and the extracted source
must equal the actual captured native helper digest. All
have 1800x1980 reconstructed images and source-bound actual FOVs. Captured960
allocation has ratio1 center 502x534, 67,008 protected encoded cells per eye,
and 2,698 of those cells span more than one source pixel.

The predictor extracts defoveate(), solve_foveation(), angles_to_center(),
convergence_angle() and fill_param_2d() verbatim from the committed native
foveation.cpp. The standalone C++ caller supplies declared angles and sizes;
it does not instantiate a native compositor or infer actual hidden tracking.
Nominal inputs are zero gaze/no manual override, 1 m convergence, eye_x of
-0.033/+0.033m, and the constructor's 10-degree downward default. These inputs
reproduce every actual 960 X/Y vector exactly. They are not uniquely recovered
measurements; source rect/flip and tracked inputs require the new mapping capture.

With those same nominal inputs, 1152x1280 produces ratio1 center 687x736 and all
69,612 protected encoded cells per eye have unit source spans. The rectangle is
x 803..1490,y 568..1304 for the left eye; x 310..997,y 568..1304 for the right,
using canonical source axes and half-open bounds. Nine finite combinations of
half-IPD 31/33/35 mm and downward angle 9/10/11 degrees also produce zero protected
reduced cells. These cases are sensitivity samples, not a continuous bound.

The mask includes any encoded pixel footprint intersecting the 10-degree cone,
using the existing research_quality.protected_mask helper and actual captured
FOV. It proves only the conditional allocation property. RGB code roundtrip,
original application mapping, submitted output and visible assessment are
separate requirements. In particular, LINEAR reconstruction and host sRGB
conversion can affect values even with an allocation that covers the cone.
The unchanged 262,144-byte complete-stereo envelope cap still requires actual
admission; no bitrate/latency/thermal claim follows from these dimensions.

Qualification: 108 eye/capture/profile cases, 216 axis invocations per compiler,
432 total. GCC -O2 -ffast-math and Clang ASan/UBSan -O1 produced identical
partitions for this finite population. Five invalid shape/angle admissions per
binary fail before construction (10 negatives). Source, generated code,
compiler, binary and input hashes are recorded. Inputs and binaries remain
unchanged afterward. Eight Python adversarial tests additionally reject malformed stereo identity,
missing eyes, changed native source, unsafe paths and symlink parents. A read-only
compositor peer review found the mask/conditional scope sound; its identity and
source-binding hardenings are included. No GPU, OpenXR or device calls occurred. Results are in
results/quality960-to1152.json; build artifacts are local under
/tmp/sub15-quality-coverage-final5.

Earlier harness attempts retained under /tmp/sub15-quality-coverage-final and
-final2 did not qualify: strict empty-stderr rejected Clang's expected native
fast-math/isnan warning, then GCC sanitizer linking found its ASan library
unavailable. The final run uses the actual finite native function extraction
with GCC fast-math and Clang's separately installed sanitizer runtime. No
algorithm was changed to suppress a failing correctness result.

Reproduce (choose a new output directory):
python3 prototypes/vr444/experiments/quality_coverage_prediction/predict.py \
 --run <immutable native-quality-960/rrb4-q1-chroma-grid> \
 --manifest-sha256 229c1c1d5a90dae9456ed9e494a6a771ded423cdf847cc2416ae5eae8275ddb3 \
 --out /tmp/sub15-quality-coverage-recheck

This analysis requires the original capture files locally. No source pixel
images are committed. The next native run can use the already implemented
q1-1152x1280 profile; it must retain the cap and independently observe geometry,
actual source mapping, all byte stages, and the user's visible assessment.
