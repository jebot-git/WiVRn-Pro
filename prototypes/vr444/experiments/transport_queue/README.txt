Bounded sender queue policies with exact stereo replay (2026-10-09)

This is a deterministic model, not a measured TCP/QUIC/WiFi transport. It executes
the VXF1 packetizer and bounded stereo receiver from ../datagram_xor, including
real CRC checks, immutable frame/warp identity, receiver-issued deadlines and
whole-pair publication. It does not create a socket, run a headset, change the
live pipeline or claim an end-to-end speedup.

Three bounded policies
fifo_two: preserve the current frame and one pending frame; reject newer offers
while both slots are occupied.
latest_pending: preserve the current frame and replace only the pending frame
with the newest offer. This avoids an unbounded FIFO while allowing a full stereo
pair to finish.
eager_preempt: replace the current incomplete frame immediately on every new
offer. A record already being serialized cannot be cancelled, and its receiver
identity is retained. The receiver never publishes a mixture of frames or eyes.

All policies drop unsent work when its absolute deadline has expired. At most two
sender frames are retained, plus one already-in-flight record<=1200 bytes. Each
offered packet list is bounded to287 records of<=1200 bytes, frame IDs must
increase, and there is no backlog of previously offered frames. The actual VXF1
packetizer applies the tighter256KiB source-pair and655,704-byte receiver payload
bounds. Python object overhead is not a measured memory footprint. Test source
generation and output trace storage are harness allocations outside the modeled
sender, not part of a production memory guarantee.

Model inputs
A source offers90 frames per second for90 frames in the matrix. One serializer
services UDP records at a fixed user-specified rate, with28 additional bytes per
record for assumed IPv4+UDP. New-frame offers precede completion events when
timestamps tie. There is no loss, retransmission, ACK, congestion adaptation,
security, WiFi/MAC, CPU processing or propagation-delay model. Synthetic receiver
clock time is the serializer completion time. Sender and receiver share this test
clock; no real clock synchronization is inferred.

The216-case matrix spans32/64/256KiB stereo source sizes,20/50/100/300Mbps fixed
service inputs,12/30/50ms receiver ticket lifetimes, widths0/8 parity, and all three
policies. Every accepted pair is checked byte-exact and before its receiver-issued
deadline, with strictly increasing publication IDs. State caps are checked after
every event. This executes19,440 offered frames across all cases. Eight targeted
tests include impossible deadlines, starvation, replacement/expiry, retained
in-flight identity, valid input bounds and regressing-frame rejection.

Reproduce from repository root
  python3 prototypes/vr444/experiments/transport_queue/test_replay.py
  python3 prototypes/vr444/experiments/transport_queue/replay.py /tmp/queue-matrix.json

Example synthetic results:64KiB pairs,20Mbps service,50ms ticket, no parity,
90 offers at90Hz, with the last frame allowed to drain after offers stop:
                       completed pairs    source-age p95     assembly p95
fifo_two                       19            45.042ms          27.902ms
latest_pending                 37            38.806ms          27.902ms
eager_preempt                   1            27.988ms          27.902ms

Eager preemption completes only the last frame after new offers stop. Publishing
one fast sample would conceal severe starvation. All gates must retain unique
stereo cadence/completeness, source age and drop counts along with latency.
The latest-pending policy allows useful complete frames and lower source age in
this scenario, but37/90 is still not90Hz service or a qualified headset result.

At32KiB/100Mbps/30ms, all three complete90/90 and have identical2.798ms modeled
first-record-to-assembly times. At256KiB/20Mbps/12ms, none can finish a pair before
deadline. Queue policy cannot make an impossible service budget feasible. On this
lossless model, parity adds service cost without a recovery benefit; the separate
FEC matrix covers loss rather than pretending it is free bandwidth.

Metric interpretation
simulated_source_age_ms starts at source offer and includes sender queue time.
simulated_first_record_to_assembly_ms begins at the first delivered record of a
published pair and ends after complete stereo assembly. Neither includes actual
decoder/GPU/upload/compositor/xrEndFrame work. Queue freshness gains may occur
before first receive and therefore do not automatically improve the existing
strict receive-to-submit metric. The displayed distributions are deterministic
model outputs, not observed timings or confidence intervals. Frames that fail to
publish remain explicit in complete-pair counts; empty timing sets stay null.

Integration requirements remain outstanding
A real alternate transport needs measured sustainable service/queue behavior,
negotiated bounds, authenticated control, congestion control, expiry signals,
immutable full stereo identity and a tested choice of queued/in-flight policy.
VXF1's256KiB cap is deliberately retained. High-entropy codec fallback may exceed
it, requiring explicit rejection/fallback or a separately validated profile,
never an unannounced quality reduction or omission from the test corpus.
