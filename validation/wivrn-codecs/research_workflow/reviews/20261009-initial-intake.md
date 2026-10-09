# Initial rolling intake review

Evidence type: read-only source and committed-receipt review. No tests or hardware measurements were reproduced in this review. Initial reference: the comparative pass in the Codex chat "Set up WiVRn-Pro". This note adds newly reviewed branches and does not replace that report with a hardware ranking.

| Branch | Pinned commit | Observed evidence and implication |
|---|---|---|
| rgb-rowfilter-simd | 895e9eff0bb0423f78ce95eae4ba0c1ea1dbaaf8 | Committed host comparison reports identical scalar/SIMD bytes and about 0.072-0.074 ms client savings against scalar Up. Q8 wire growth of 27.88-36.07% and worse combined host work remain. ARM64 is compiled, not runtime-qualified. Keep backend implementation in the experiment identity even when wire bytes match. |
| compositor-target-envelope | 60ee8cb8bc1616464eab3b255dd2dcf83e02f33a | Preserved run05 offline bound remains 26.927659 ms minimum p95 at all 1,900 advancing selections even without the target ceiling. The 14.677762 ms certificate omits 191 deadlines and relaxes ownership/pose constraints. This cannot supply a passing measured latency label for learning. |
| compositor-pacer-capture | bc869a5c170a0cfa1150d160bfe0229bf0dd79ea | Adds a capture contract joining host frame ID and returned display target within host monotonic time. Reported 239 Python tests are source-receipt evidence, not a new capture. Native integration and new acquisition remain outstanding; outer stereo validation is required. |
| vulkan-extensions | 00939ca2c504a525e69c173221e1afb67b5be6bc | Reported Arc execution validates buffer identity/lifetime and retirement, not images, live decoding or presentation. Adreno feature enablement and speedup remain unqualified. Useful correctness groundwork without establishing neural-decoder headroom. |

## Pinned sources

- [SIMD comparison](https://github.com/jebot-git/WiVRn-Pro/blob/895e9eff0bb0423f78ce95eae4ba0c1ea1dbaaf8/prototypes/vr444/experiments/rgb_rowfilter/SIMD.txt)
- [SIMD provenance](https://github.com/jebot-git/WiVRn-Pro/blob/895e9eff0bb0423f78ce95eae4ba0c1ea1dbaaf8/prototypes/vr444/experiments/rgb_rowfilter/simd-results/measurement-provenance.json)
- [Target envelope](https://github.com/jebot-git/WiVRn-Pro/blob/60ee8cb8bc1616464eab3b255dd2dcf83e02f33a/prototypes/vr444/experiments/selection_policy/TARGET-ENVELOPE.txt)
- [Pacer capture](https://github.com/jebot-git/WiVRn-Pro/blob/bc869a5c170a0cfa1150d160bfe0229bf0dd79ea/prototypes/vr444/experiments/pacer_timing/CAPTURE-CONTRACT.txt)
- [Vulkan scope](https://github.com/jebot-git/WiVRn-Pro/blob/00939ca2c504a525e69c173221e1afb67b5be6bc/prototypes/vr444/experiments/vulkan_extensions/README.txt)
- [Vulkan receipt](https://github.com/jebot-git/WiVRn-Pro/blob/00939ca2c504a525e69c173221e1afb67b5be6bc/prototypes/vr444/experiments/vulkan_extensions/qualification-20261009.json)

No reviewed addition establishes a headset latency winner. Keep neural architecture choice open and record timing population, clock domains, source/binary identity, stereo validation and promotion blockers explicitly.
