RPT1 GPU output-memory alternatives — offline correctness branch

Three arms use identical canonical encode/scan/compact shader modules from
palette_gpu_encode. They preserve every packet byte, pixel and format choice.
This branch changes how the compact body reaches CPU header/CRC assembly:

full_copy: device-local compact output, one submission/fence per eye, copy the
full2*(772*tiles+8)-byte stereo capacity to coherent host readback.
mapped_coherent: GPU writes into coherent host-visible compact output, one
submission/fence per eye, compute-write-to-host-read barrier, then CPU assembly
directly reads that allocation. It performs no vkCmdCopyBuffer readback.
compact_copy: first submit copies the4-byte bounded body length, host waits and
validates it, then a second submission/fence copies only the rounded body. Total
copy bytes per eye are4+round_up(body_bytes,4). The buffer stays owned throughout.
This reduces copied bytes at the cost of an additional CPU dependency and fence.

Zero buffer-copy bytes does not mean zero bus traffic. The mapped arm still has
GPU writes, visibility/cache costs and CPU reads of host-visible memory. All arms
currently clear the entire compact capacity before atomic OR, including when
the actual packet is tiny; mapped output may therefore still incur full-capacity
traffic. Input RGBA upload, intermediate poison and host readback poison also
remain in this prototype. No renderer-resident source or reduced allocation is
claimed, and the mapped arm retains an unused readback allocation for parity.

Safety and exactness
The original complete-pair source validation precedes writes/submission. Each
eye run repeats its shape/alpha guard. Only the private device/queue and resources
are touched; every frame/eye finishes before command-buffer and storage reuse.
Compact length admission uses the existing7..772 bytes per tile bounds before
rounded sizes are used. First and second copies remain within the allocated
capacity. Compute/transfer barriers and host-visible coherent memory pair with
the observed fences. Query0 is written during encoding and query1 at final output
completion; query state is retained across the compact arm's two submissions.
The timestamp span therefore includes the compact arm's CPU-dependent gap. It is
explicitly not labeled pure GPU execution time.

Host input/readback memory requires visible+coherent flags. Mapped compact output
requires the same; full/compact copy output requires device-local memory. Actual
flags are reported per arm. CPU/unknown devices and insufficient queried limits
are rejected. No optional extension is enabled, and Android remains compile-only.
The shader hashes equal the previously qualified encoder's hashes. Four new
independent tie fixtures verify constant-vs-raw, palette-equals-raw, runs-equal-
palette and runs-equal-raw decisions in every arm. Malformed right-eye admission
must leave submission counters unchanged. Every valid output equals canonical
CPU RPT bytes and decodes exactly to input pixels, including maximum-size data.

Reproduce
  bash prototypes/vr444/experiments/palette_output_memory/build.sh
  prototypes/vr444/experiments/palette_output_memory/build/palette-output-host \
    prototypes/vr444/experiments/palette_output_memory/build/shaders \
    prototypes/vr444/experiments/rgb_palette/fixtures \
    --extra-goldens prototypes/vr444/experiments/palette_gpu/fixtures --cpu-only --full --max-size
  VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json \
    prototypes/vr444/experiments/palette_output_memory/build/palette-output-host \
    prototypes/vr444/experiments/palette_output_memory/build/shaders \
    prototypes/vr444/experiments/rgb_palette/fixtures \
    --extra-goldens prototypes/vr444/experiments/palette_gpu/fixtures \
    --full --max-size --corpus EXPORTED_RPT_DIRECTORY > RESULT.json
  python3 prototypes/vr444/experiments/palette_output_memory/audit.py RESULT.json
  python3 -m unittest discover -s prototypes/vr444/experiments/palette_output_memory -v
All three arms must be present for every repetition. The audit independently
checks exact byte-copy counts, two versus four stereo submissions, per-arm memory
flags, four tie cases and nonfinite timing rejection. The benchmark interpretation
remains blocked even when every correctness gate passes.

Observed qualification,2026-10-09
Arc A770 passed190 full fixtures in all three arms (570 measured stereo arm
invocations plus570 warmups); UHD770 passed134 default fixtures in all arms.
The190 full cases include the earlier186-case encoder suite and four new tie
vectors. The original encoder executable retains full_copy as its default and
independently passed its186-case suite after common-fixture extraction. The shared
encoder's original qualification receipt is historical evidence bound to commit
145310492b351e1d17e9cdfbb8ac34129e097096, not to the modified current source.
Current evidence and hashes are in this directory's qualification-20261009.json.
No captured image bytes are committed, and no headset was accessed.
CPU190 fixtures passed ASan/UBSan with leak detection disabled under ptrace.
Both host/Android warnings-as-errors builds, three spirv-val shader modules and
four audit tests passed. Original seven source/ten capability admission checks
also pass with the extended shared encoder.

Limits and interpretation
Every arm still uploads CPU RGBA and assembles/CRCs the output on CPU. Input
validation, upload, assembly/CRC and submission/wait timing are separate fields.
Submission/wait overlaps device timestamp span and must not be added to it.
The host readback poison is outside the component submission timer but alters
cache state; GPU intermediate poison/clear is inside it. Arm order rotates by
fixture and repetition after one warmup. Concurrent team correctness work is
allowed, so these runs establish correctness and costs counted, not speedups.
Later controlled whole-prototype measurements must include both copy and CPU
dependency costs; a reduced byte count alone is not a latency result.

Observed receipt-to-publication and publication-to-selection age accumulation in
the preserved headset traces does not identify a removable scheduler cost.
Earlier decode/arrival could change which frame is eligible at a deadline, causing
discrete effects larger than an isolated stage delta. Conversely, faster host GPU
encoding alone cannot establish sub-15ms headset submission or optical freshness.
There is no network, compositor scheduling, native overlay or wearer validation
in this branch, and the separate transport caps/fallback requirements still apply.

Primary synchronization reference
  https://docs.vulkan.org/guide/latest/synchronization_examples.html
Host writes before submission, shader/transfer visibility, and host readback
completion are distinct requirements. An independent compositor-team static
review found no blocker in the predecessor encoder's atomic byte compaction or
full-copy synchronization; the new memory choices have their own qualification.
