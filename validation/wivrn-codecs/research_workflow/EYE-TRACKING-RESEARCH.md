# Decoupled Quest Pro eye tracking — research declaration

**Declared for research:** treat the rooted Meta Quest Pro eye-tracking path as decoupled into independently usable left-eye and right-eye signals. This enables **two separately controlled foveating regions, one per eye**, and **per-eye eye-movement/gaze prediction**. Region centers, sizes and uncertainty margins may differ between eyes.

Status: **user-declared research assumption; hardware capability not yet verified**. Root access alone establishes neither a particular gaze API nor a sample rate, tracking quality or performance result. The machine-readable declaration is [research-assumptions.json](research-assumptions.json), ID `quest-pro-decoupled-eye-tracking-v1`.

## Signal and prediction contract

- Preserve each eye's sample timestamp and clock domain, gaze origin/direction, coordinate frame, calibration/projection identity, validity and available confidence. Keep measured and predicted gaze distinct; unavailable confidence is unknown, not zero.
- Map each gaze ray through its own eye projection. Independent observation/control does not imply statistically independent movements, and a joint binocular predictor may use correlations while producing distinct per-eye outputs.
- Predict toward the same intended presentation target for both eyes. Compute each prediction horizon from that eye's own timestamp and record target, horizon, algorithm/model version and uncertainty. Cross-device clock arithmetic requires an explicit measured mapping.
- Keep coherent stereo/source/warp identity and the common presentation target. Decoupled gaze does not permit mixed-generation left/right images or independently submitted partial stereo pairs.
- Define bounded-age and uncertainty fallbacks for blinks, invalid/stale samples or tracking loss: widen the affected region or disable foveation according to a recorded policy. Distinguish one-eye fallback from actual bilateral measurements.

## Experimental use

Compare shared-gaze, independent-current-gaze and independent-predicted-gaze arms on identical stereo and gaze traces. Hold codec, transport, offered cadence and declared quality settings constant where possible; explicitly identify the quality tradeoff when they change.

Record actual transmitted bytes, server/headset costs, complete-stereo cadence, gaze/source age with clock basis, per-eye angular prediction error, foveal misses, fallbacks and per-eye/stereo quality. Tests of displayed quality or discomfort require actual relevant observations, not an inference from gaze prediction error.

Use only information available at prediction time; future gaze is a label, never an input. Freeze scene/session splits before training and use participant-separated evaluation for cross-user generalization claims. Label synthetic or replayed gaze separately from captured device data.

Reference the assumption ID and its file hash from the hashed experiment configuration; preserve per-eye gaze metadata in the frozen data index or a hashed sidecar. The existing strict run-manifest fields remain unchanged. A changed foveation/quantization/reconstruction profile is a separate quality arm; it must not be silently labeled exact-Q8 equivalence.

## Evidence and ongoing review

This declaration opens a research capability; it does not select a neural architecture, configure a rooted headset, expose a runtime API or prove a codec/latency gain. Verification needs an observed device API/capture receipt identifying the data path and per-eye timestamp/validity semantics.

During rolling branch review, consider independent foveation and causal per-eye prediction as additional candidate hypotheses. Preserve the existing wire bounds, stereo coherence, quality, source-age and headset latency/cadence requirements. Keep capability assumptions, simulation/replay, host measurements and target-device evidence separate.
