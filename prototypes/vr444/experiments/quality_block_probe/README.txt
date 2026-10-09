Archived unfinished block-size investigation

The source is an unexecuted offline RRB16/32/64/128 size/equality probe begun in
response to the saved1152x1280 RGB-ramp stereo cap rejection (408,093 vs262,144
bytes). Before any probe was executed, the user replaced the WiVRn-cap task with
standalone codec research with no inherited cap. This investigation is stopped.

An initial link against libwivrn-test-codec.a failed due to unresolved Vulkan
symbols pulled from that archive. No probe binary ran, no alternative compression
result exists, and no native profile or cap was changed. This commit retains the
source and branch only to preserve provenance of the abandoned approach. It is
not qualified, measured, production-ready or recommended for integration.
