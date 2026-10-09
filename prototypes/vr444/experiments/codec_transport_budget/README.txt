Codec bytes, host CPU and bounded serialized-link budget
=======================================================

Experimental offline component model; no sockets, headset access, native gate
evaluation, new performance measurement, production protocol change or promotion.
This branch joins the measured rowblocks-workers evidence (260a1a22) and the
isolated VXF1 reference transport (726d7c39, cherry-picked as 2c4c907e).

Purpose and inputs
------------------
The worker experiment found faster client decoding with more CPU work and, for
Q8 captures, substantially more bytes. This model asks how those tradeoffs change
under explicitly assumed serialized service rates. It retains every matched
host server/client sample, rather than subtracting unrelated sample percentiles.

Input: 26 profiles, 12 actual captured stereo pairs plus 14 synthetic profiles
whose same eye is deliberately duplicated; 8 codec/worker arms, 31 repetitions.
The source benchmark, measurement receipt and every measured implementation hash
are checked before modeling. Captures themselves are not copied into this branch.
The original x86 host CPU timings remain subject to uncontrolled clocks, desktop
work, affinity and thermal state. They do not estimate headset CPU performance.

The experiment has 8,320 declared rows: rates 50/100/300/600/1000 Mbps; IPv4+UDP
28 bytes or IPv6+UDP 48 bytes per packet; no FEC or XOR stripe width 8; clean or
the first source shard erased. Both encoded eyes must fit the unchanged 256 KiB
VXF pair cap. An oversized profile is ineligible, never truncated or requantized.
All codecs still preserve their respective already-decoded input bytes; exact
Q8 re-encoding does not make the original Q8 input lossless.

The actual VXF packetizer and receiver run against deterministic synthetic bytes
of the measured total encoded stereo length, split evenly between the two eyes.
This exercises framing sizes, ticket expiry, exact complete-eye reconstruction,
CRC and recovery; it does NOT demonstrate that these bytes are valid codec data
or that the formats were integrated. Packet sizes and counts are independent of
this equal split. The receiver retains its original resource/identity bounds.
No CRC or parity is presented as authentication.

Timing model
------------
Both eyes finish encoding before wire service begins. Every packet consumes its
application bytes plus the declared IP/UDP header at the chosen rate, with each
packet duration rounded up to a nanosecond. Dropped packets and parity occurring
after early completion still consume link service. Decode begins only after the
receiver has returned both complete eyes. No encode/send/decode overlap occurs.

For each measured repetition:
  first-record horizon = first actual record receipt to complete assembly
                         + measured host client wall duration
  full horizon = sender-ready to complete assembly
                 + matched measured server and client wall durations

These are component arithmetic with synthetic arrival times, not measured network
latency. Neither horizon is the native first-receive to post-xrEndFrame metric.
No packetization/CRC/recovery CPU time, link/WiFi/MAC overhead, encryption, ACK or
control traffic, propagation, jitter, congestion, pacing, GPU, compositor/runtime
wait, display scanout or optical response is included. A 100 ms receiver ticket
and the check for sustaining a hypothetical 90 Hz offered load are explicit model
parameters, not live settings. There is no sender queue/arrival history model.

The first-record horizon includes the initial manifest. It excludes serialization
before that record arrives. The first-data variant is also retained for inspection
and may be null if a one-shard frame loses its only source and uses parity.
All-wire service includes the final duplicate manifest or parity even when the
pair completed earlier. Sustainable load requires all this traffic to fit.

Results
-------
All 8,320 rows completed the model. 880 rows explicitly reject over-cap synthetic
profiles; 1,860 eligible first-source-loss/no-FEC rows do not assemble; 5,580 rows
assemble exactly before the modeled ticket. These are enumerated cases, not
empirical reliability rates. The clean captured profiles all fit this wire cap.

Below are arithmetic means of the four captured pair p50 full horizons in each
group, milliseconds, with IPv4/UDP and no FEC:

group       Mbps  VR444 serial  RRF-up serial  RRB32 / 4 workers
Q1           50      4.906573      3.794814         3.882532
Q1          100      2.797953      2.591154         2.452372
Q1          300      1.392208      1.788716         1.498934
Q1         1000      0.900195      1.507860         1.165227
Q8 paired-a 100      5.005085      7.247982         6.955855
Q8 paired-b 100      3.826684      5.898724         5.635103

Thus a smaller exact Q1 format can outweigh its added host CPU work at a modeled
slow link, but its advantage reverses as service becomes fast. At 100 Mbps the
modeled Q1 first-record horizons are 2.646597 / 1.721297 / 1.741921 ms, respectively;
excluding preprocessing changes the ordering of two candidates. Those exclusions
must stay explicit when relating component work to the native metric.

All captured Q8 full-horizon alternatives are worse than VR444 at the declared
rates. At 50 Mbps, paired-a RRF/RRB also fail the per-pair hypothetical 90 Hz service
capacity condition in some cases; with width-8 FEC even the VR444 paired-a group
does not have every pair sustainable. No reliability or headset conclusion follows.

Qualification and reproduction
-----------------------------
15 model tests cover independent packet-overhead formulas, 1-shard/partial/full
shard/maximum boundaries, lost-source recovery, ticket expiry, explicit capacity
rejection, IPv6 overhead, first-record timing, matched-sample integrity, malformed
scenario arguments, incomplete group admission and lossless evidence packing.
The unchanged VXF suite is also rerun (24 tests). No extra measured batch is used.

From the repository root:
  python3 -m unittest discover -s prototypes/vr444/experiments/codec_transport_budget -v
  python3 -m unittest discover -s prototypes/vr444/experiments/datagram_xor -v
  python3 prototypes/vr444/experiments/codec_transport_budget/model.py /tmp/codec-budget.json
  python3 prototypes/vr444/experiments/codec_transport_budget/compact.py /tmp/codec-budget.json /tmp/codec-budget-table.json

results/matrix.json contains all rows as a lossless JSON table, with named columns
and interned transfer dictionaries. compact.unpack reconstructs the full readable
model result exactly; this storage convenience is not another wire codec. The
receipt hashes both representations, inputs, source and passing test output.

Next evidence needed: real paired codec/transport interoperability, packet CPU
cost, declared authenticated transport, actual workload and NIC/connection service
behavior, and eventually an explicitly authorized complete headset gate. None is
implicitly satisfied by these arithmetic results.
