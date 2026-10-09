Native staged RRB32 image observation
====================================
The first source-bound native staged-GPU run produced 2,087 complete decoded
stereo pairs. All 48 saved decoder-image readbacks match the corresponding saved
codec reference exactly; none match the original Q8 pre-codec source exactly.
Twenty-six captured eyes have a frame/warp identity that was later presented.
These are image-production and telemetry findings, not final submitted-image,
central-cone, original-application or displayed-pixel qualification.

results/native-gpu.json binds the completed rrb-gpu-publication run manifest and
the d6be1ee9 native build receipt. audit.py checks the actual helper, decoder,
shader and wire/codec sources against build and launch provenance, and the built
APK against its recorded installed identity. Actual installation verification
and full native trace validators remain the root campaign's separate work. The
auditor rechecks each used raw file after parsing its originally hashed bytes.
It checks both decoder populations, frame/warp/generation identity, matching host
encode events, joint256KiB envelopes, zero alpha-stream rows, every diagnostic
readback, WCT4 header/CRC/shape and opaque output bytes. Independent scalar
re-decoding of those payloads is a separate codec-team audit; saved reference
equality alone is not asserted to be that independent scalar check.

All-session diagnostic medians in milliseconds, left / right:
  private staging prepare:   0.237291 / 0.232136
  submit + producer fence:   0.555313 / 0.537656
  complete decoder wall:     3.095730 / 3.070620
  waiting before decode:     0.417865 / 0.026823
The shared complete-pair residual CPU value is2.075520ms median and2.539270ms
nearest-rank p95, counted once per complete pair. The source logs it identically
on both eye rows; adding them would double-count the shared work. Prepare is a
whole preparation operation, not a pure memcpy timer. Submit/fence includes
queue lock, submission and observed producer completion, not just shader time.
decode_upload excludes the pre-decode queue wait and the subsequent event flush
and native pair-publication call; it includes startup and optional image capture.
The report separates captured and uncaptured populations for inspection. Stage
quantiles must not be added; no within-run CPU/GPU comparative claim is made.

Every native GPU row explicitly says gpu_timing_valid=false and decode_gpu_ms=0.
Zero is unavailable telemetry here, not instantaneous GPU execution. The root's
separate strict presentation/source/publication/pacer validators passed this
traced acquisition, but its warm complete-stereo p95 was28.654430ms at89.416582Hz,
so it does not establish the sub15ms route. Root warm presentation metrics are
retained verbatim under root_warm_presentation, not requalified by this script.

Reproduce the read-only audit:
  python3 audit.py --run PATH/rrb-gpu-publication --manifest-sha256 SHA \
    --build-receipt PATH/build-receipt.json --build-receipt-sha256 SHA \
    --out /tmp/FRESH_NATIVE_IMAGE_REPORT.json
  python3 -m unittest -v test_audit
No builds, networking, ADB or GPU commands are issued. The four regression tests
cover unavailable GPU-timing claims, duplicate/incorrect identities, captured
stride, envelope CRC/size/shape, immutable snapshots, path traversal and finite
statistics. This observer is intentionally restricted to the recorded Q8 RRB32
staged route; Q1 quality captures use their separate source/submission auditors.
