# Alternate protocol research

This parallel workstream explores low-latency host/client transport alongside the active codec research. The user-declared setup is a Quest Pro rooted with Magisk through Singularity, an Intel NUC 12 Extreme i7, and user-confirmed direct 2.5 GbE Ethernet to an ASUS RT-AXE7800 with a 6 GHz network available.

Read the [initial four-agent study](protocol-research/20261009T090350Z.md) and [machine-readable plan](protocol-research-plan.json). The study compares paced authenticated UDP/VXF, QUIC DATAGRAM plus reliable control, and RTP/SRTP with bounded recovery. Reliable/partially reliable transports and USB networking remain separate diagnostic or capability-gated arms. No final protocol or architecture is selected.

The user confirms a 2.5 GbE host/AP link, so a 1 GbE hop is not a blocker for the declared setup. The first physical receipt should archive speed/duplex and AP port-role readback, actual headset 6 GHz association/width, OS/kernel, root modules/tweaks and firmware. The remaining capacity question is measured wireless service, with application admission still separate.

## Rolling research use

- Review changed protocol candidates at exact SHAs using the same complete inventory and ancestry rules as codec research.
- Keep the four lanes independent: transport semantics; Quest/Android capability and topology; Linux/radio scheduling; integration and evidence contract. Delegate parallel investigations when there is actionable new evidence or a concrete experiment to design.
- Retain actual codec bytes, cap/dense regressions, coherent stereo, causal per-eye gaze, fixed quality and the inherited latency/cadence reference. Account separately for host queues, pre-receipt transfer and source/gaze age.
- Compare current TCP over the same 6 GHz route and the historical TCP-over-ADB profile separately. Label localhost, simulation, source review and headset measurements accurately.
- Record protocol details in a hashed versioned sidecar; this plan is not a strict v1 run manifest or a completed measurement. Keep neural architectures replaceable, including declared temporal/progressive dependencies.
- Source research is complete for the initial study; adapter implementation and device trials remain planned. This chat's execution environment is unavailable, and no device settings or production defaults were changed.
- Append new studies and observed receipts without overwriting prior evidence. Use a current-head expected-SHA lease for workflow updates and reconcile conflicts. Notify only for meaningful results, changed conclusions, validated compatibility changes, completion, failures requiring attention or required user action.

The existing checkpoint remains 13:32:40 Europe/Prague (11:32:40 UTC). It does not stop this work or other research, merge branches, deploy, or authorize an automatic protocol/codec selection.
