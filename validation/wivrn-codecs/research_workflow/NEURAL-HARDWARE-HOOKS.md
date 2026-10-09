# Manual neural hardware hooks

This is a **manual acquisition interface**, not a native decoder or a hardware qualification runner. It does not discover devices, train a model, invoke ADB, change root/router settings, install software, select a codec, or run newly discovered branches. There is no bundled live adapter. The example is deliberately unbound; missing pins/artifacts/adapter produce a blocked run rather than fabricated data.

The independent sidecars below do not change manifest.py's strict v1 schema, CONTRACT.md, or run_pair.py. Keep offline real-bitstream receipts separate. If observations are later transferred into a v1 manifest, review their exact population and evidence type first. Hash the plan, capture descriptor, validation receipt and supporting artifacts into that manifest's provenance.

## Commands and explicit trust

Run on a POSIX host with Python 3:

    python3 neural_hooks.py plan neural-live-plan.example.json
    python3 neural_hooks.py run /ABS/plan.json --output /ABS/NEW_CAPTURE_DIRECTORY
    python3 neural_hooks.py import /ABS/plan.json /ABS/EXISTING_CAPTURE_DIRECTORY --output /ABS/NEW_VALIDATION.json

plan performs structural validation only; it does not read input artifacts or launch anything. run binds input bytes, invokes the exact adapter_argv, waits for its synchronous completion, and validates its output. import performs the same artifact/event checks without launching anything. The output directory/file must not exist. Exit 0 means capture_validated; 2 means blocked/failed collection; 1 means setup/CLI failure. Planned validation returns 0 even while reporting unresolved bindings.

adapter_argv is a JSON string array. Include standalone {plan} and {output} tokens; those are the only substitutions. No shell is used. The executable is trusted code: this is not a sandbox, and a manually configured command can itself control hardware. There is no implicit authorization for a bundled ADB/root/network command. The adapter receives the original absolute plan path and a new output directory. Resolve relative input paths against the plan's parent. Process groups are terminated after completion/timeout; each file written by the subprocess has the declared file-size limit. Raw stdout/stderr are retained. Background daemon work is unsupported. Command wall time is **never** reported as latency.

Input plan/artifact hashes are rechecked after acquisition. Artifact paths must stay within the capture directory and cannot be symlinks; files must be regular. JSON rejects duplicate keys and nonfinite numbers. Artifact size, total size, row count and command timeout are bounded. Each JSON/JSONL artifact parsed by the hook additionally has an 8 MiB limit. Failure preserves existing raw output; a failed validation never supplies partial summary metrics.

## Plan: wivrn-neural-live-plan-v1

Use neural-live-plan.example.json. Bind candidate, baseline and harness to full 40-character commits; ref is descriptive only. The harness pin must identify the code actually executed. Config and frozen dataset index require paths and SHA-256 of exact bytes; learned candidates additionally require the exact model. Null pins/paths/hashes are permitted for planning and block collection. Hashes of built binaries, backend/runtime versions and runtime telemetry belong in the runtime artifact; external executables referenced only by their reported hash are not independently reverified.

quality.mode is lossless, q8_exact, or new_lossy, with an explicit reference. The quality artifact must retain the actual checks, errors, metrics and sample population. The hook binds those bytes but does not interpret image quality or establish Q8/display equivalence.

route preserves profile_id, protocol, path and measurement_layer, plus any endpoint/framing fields. Copy it exactly into the capture. Native TCP, native TCP through ADB forwarding, localhost transport, ADB file-copy/decode, real UDP and simulated datagrams are separate routes. An ADB network route can carry genuine native headset events; an ADB file-only decoder cannot supply xrEndFrame evidence. route equality prevents accidental relabeling within one plan, but does not prove the physical route or qualify comparisons between runs. Record actual interfaces, endpoints, negotiated rates and framing in runtime/transport artifacts. Controlled secured UDP/VXF, QUIC DATAGRAM with reliable control, and RTP/SRTP with bounded repair are separate experimental route/configuration profiles; bind their security, congestion, pacing, framing and repair parameters explicitly. Keep transport diagnostics separate from native presentation evidence. The hook does not automatically select or enable any protocol.

setup_declared holds the user's NUC/ASUS/Quest/root setup assumption. It is never promoted to observed capability. Native captures need a separately supplied runtime record. The example records the user-confirmed direct 2.5 GbE connection and available 6 GHz network as declarations. It does not claim a locally measured port/rate, current headset association/channel width or gaze API.

gaze.arm describes acquisition evidence: none, simulated, replayed, device_shared, or device_independent. For non-none arms, gaze.mode independently names shared_gaze, independent_current_gaze, or independent_predicted_gaze. Rooted Quest Pro independent-eye access remains a user-declared research assumption. A device label or two copied values do not prove bilateral API access.

## Adapter output: wivrn-neural-device-capture-v1

Write capture.json after completing/flushing artifact files. Its required fields are:

- schema, run_id, plan_sha256: exact original plan bytes; identity and route copied exactly from plan.
- evidence: native_headset or synthetic_fixture, exactly matching plan; input_sha256 maps config/dataset/model roles to plan digests.
- status: completed, blocked or failed. A blocked/failed descriptor requires reason; no metrics are extracted. Absent required capabilities also yields blocked.
- For completed capture: runtime object (native requires device_role: headset), capabilities containing stereo_receipt_events and post_successful_xr_end_frame_events, client_clock with id, unit: ns and kind: monotonic or xr_time. Synthetic fixtures use kind: synthetic and cannot fill headset timing.
- receipt_scope: earliest_application_receipt_any_eye. Instrument the earliest application receipt of any byte/fragment of the identified stereo pair, preserving earlier arrivals across both eyes and retries. If current telemetry only begins after reassembly/decode, it does not implement this boundary.
- window: client-clock id, start_ns and end_ns, half-open [start,end). coverage: render_tail_complete boolean and notes explaining censored attempts, unknown tails, acquisition gaps, warmup and offered-population boundaries.
- offered_pairs and artifacts. Each artifacts key is a relative path; its value has role, bytes and SHA-256. Index every consumed artifact. Do not include capture.json itself.

Exactly one indexed artifact is required for each role offered, pairs, native_events, runtime and quality. Non-none gaze requires gaze and gaze_geometry as well. Additional raw artifacts may use source_events, arrival_events, deadline_events, queue_events, transport_trace, clock_mapping, gaze_calibration, decoder_outputs, build_receipt or other documented roles. Preserve original native logs alongside normalized events, including failed calls and terminal tails. Their hashes bind supplied evidence; no signature or independent hardware authenticity is established.

Offered, pair and native-event artifacts are JSONL:

1. **offered** has one row per offered pair: nonempty pair_id, source_id, warp_id and target_id. IDs are scoped by run_id. target_id here is the immutable offered/encoded target identity. Record every offered pair in the frozen ledger, including dense/over-cap, rejected, stale, fallback and unfinished-tail inputs. The hook cannot recover offers omitted by the adapter from both ledger and descriptor.
2. **pairs** has exactly the same identities, once each; disposition is candidate, fallback or rejected, with reason. These disjoint dispositions describe the delivery path; lack of submission is separately retained and must not be inferred as successful delivery. candidate_artifact points to the actual complete candidate stereo envelope with role candidate_bitstream, and candidate_envelope_bytes must equal that file's checked length. over_cap must match the plan's cap and require fallback/rejected disposition. If serialization was never measured, all three fields are null, and the unmeasured count remains explicit. Do not combine raw per-eye files and claim a production envelope without actual framing.
3. **native_events** has unique event_id, complete immutable offered identity, clock and integer timestamp_ns. kind: receipt records the earliest application-arrival observations. kind: xr_end_frame_return records every observed call result, with post_call: true, success boolean and xr_result string. success must equal whether xr_result is XR_SUCCESS. On success, supply presentation_target_id for the actual returned/submitted target and eyes.left/eyes.right, each repeating the offered identity plus the same presentation_target_id. The actual presentation target may differ on a repeated/retargeted render attempt. Both eyes must be coherent. A rejected pair cannot successfully submit.

Use distinct source IDs for known source generations; re-encoding does not create new source identity. A source ID is provenance, not proof of identical pixels, simulation/input epoch or acquisition age. Preserve original source timestamps, clock mapping and uncertainty as separate raw artifacts. Likewise archive actual transport bytes, packet framing, retransmits and fallback streams at the declared layer; the hook does not manufacture wire bytes from envelope sizes.

candidate_serialized_bytes is the sum of **generated candidate envelopes before admission/fallback**, including rejected/unused over-cap candidates. It excludes fallback envelopes and transport framing. If any candidate envelope is unmeasured, the aggregate is null.

## Gaze decision artifact

For each observed successfully submitted pair—including previously presented pairs repeated during the window—provide a gaze JSONL row with pair_id, immutable target_id, arm, mode, decision (tracked or fallback), fallback_policy, decision_ns and decision_clock equal to the client clock. presentation_target_ns and presentation_target_clock describe the common prediction/presentation target for the stereo decision; preserve actual rendered target identity in native events.

Each left/right record requires:

- valid and stale booleans; confidence numeric in [0,1] or explicit null.
- sample_clock, acquisition_ns and pose_expression_ns, with explicit null where unavailable. A returned OpenXR pose-expression time may be in the future and is not acquisition time.
- available_clock and available_ns: when that eye's data became available to the decision. Same-client-clock availability must be no later than decision_ns. Other clocks require available_in_client_ns, availability_uncertainty_ns, and availability_mapping_artifact pointing to an indexed clock_mapping artifact. The mapped upper bound must be no later than the decision.
- horizon_ns and uncertainty_ns, nonnegative or explicit null; calibration_artifact references a hashed artifact or is explicitly null. Do not replace unknown acquisition/calibration/uncertainty with zero.

An invalid/stale eye requires a recorded fallback decision. The hashed gaze_geometry artifact must preserve per-eye projection, foveating-region parameters, coordinate transforms, calibration provenance and the actual fallback policy. The importer checks its hash, not its geometry or the rendered effect. It does not prove sample acquisition time, bilateral access, predictive accuracy, clock-calibration correctness or perceptual quality. Retain causal sample/decision traces and prediction/quality controls for later review.

## What the validation receipt means

capture-validation.json uses wivrn-neural-capture-validation-v1 and always says independent_hardware_verification: false. It verifies supplied artifact integrity, declared identities, complete declared populations and basic event consistency; a self-asserted native JSON record cannot authenticate a hardware experiment.

The only latency summary is nearest-rank p95 over first successful coherent pair submissions whose **earliest observed success** falls in the measurement window, measured from that pair's earliest recorded application receipt in the same client clock. A previously successful pair repeated in-window does not become a new sample. Repeat counts, all offered pairs, rejected/fallback/over-cap populations, absent submissions, explicit tail coverage and the sample IDs remain visible. Native evidence may populate headset_receive_to_submit_p95_ms; synthetic_fixture keeps that field null and only supplies diagnostic_receive_to_submit_p95_ms. A native field is conditional on supplied logs, not independent verification.

The endpoint is post successful xrEndFrame return, not GPU completion, visible scanout or optical response. Capture validation does not establish the 576x640/eye, 90 Hz, p95 <15 ms, >=88 unique/s gate. It does not verify repeated-run policy, offer cadence, geometry, complete telemetry coverage or native render load. Server queue/encode, source age and wire bytes remain null in this summary, with raw hooks preserved. Independent stage p95s must not be added.

## Synthetic checks

    python3 -m unittest discover -s validation/wivrn-codecs/research_workflow -p 'test_neural_hooks.py'

Tests create explicit synthetic fixtures and local Python adapter stubs; they neither discover hardware nor supply device evidence. The authoring session had no shell/execution environment, so these tests were not run there. CI must report its own actual result. No live trial is implied by committing this harness.
