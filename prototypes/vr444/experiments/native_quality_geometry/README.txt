Explicit native quality geometry
================================
This opt-in replaces both test-only576x640 overrides for a narrowly declared
Q1 quality profile. The default, missing and explicit off configuration still
advertise and encode576x640. Non-test builds retain their existing geometry path.
No packet layout, codec grammar, quantizer, image helper or stereo262144-byte
admission cap changes. This does not qualify pixels, central-cone coverage,
physical visibility, latency or a whole quality route.

Controls (the root harness owns backup, readback, restore and provenance):
  client file codec-test-quality-geometry.txt
  server env WIVRN_CODEC_TEST_QUALITY_GEOMETRY
Both use the same canonical token:
  off              576x640, existing diagnostic/latency default
  q1-768x864       quality scale .4 profile
  q1-960x1056      quality scale .5 profile
  q1-1152x1280     quality scale .6 profile
These are exact dimensions, not multiplication by the runtime's recommended size.
The client scale setting alone never activates this native override.

The client reads only a regular, nonsymlink file of at most64 bytes; one final
LF/CRLF is accepted. A missing file is default. Empty, malformed, unreadable,
nonregular or unsupported configuration throws before geometry is advertised.
Larger geometry additionally requires the already parsed submitted-quality
capture configuration to be enabled and codec-test-payload.txt to equal rrb32.
The client token declares Q1 intent; independent server admission proves the
encoder mode. The server requires all of:
  WIVRN_CODEC_TEST=rrb32-q1
  WIVRN_CODEC_TEST_Q=1
  WIVRN_CODEC_TEST_COMPRESSION=none
  WIVRN_CODEC_TEST_QUALITY_CAPTURE=1
  WIVRN_CODEC_SOURCE_TRACE=/absolute/owned/source-trace.jsonl
The advertised headset width/height must exactly match the host token. An off
host rejects a larger client, and a larger host rejects a default client. No
fallback or silent resize is permitted. The two opaque encoder slots use the
selected geometry; the existing unused alpha slot keeps half the eye height.
All profiles are aligned to the existing tile domains and below4MP per eye.

Integration is limited to new common/codec_test/quality_geometry.h and the
existing test-only geometry blocks in client/scenes/stream.cpp and
server/encoder/encoder_settings.cpp. Root regenerates the union overlay manifest
and patch, rebuilds both endpoints, and extends harness/provenance/control lists.
The follow-on effective-geometry audit must check actual WCT envelope dimensions,
not the requested scale, before any central10-degree footprint conclusion.

Fresh qualification:
  python3 qualify.py --native ROOT_OWNED_NATIVE_BUILD_TREE --out /tmp/FRESH_DIR
The helper ASan/UBSan test passes1091 checks for defaults, exact profiles,
invalid tokens, client prerequisites, server proof and peer mismatch. A separate
ASan/UBSan program compiles the actual extracted client file-reader lambda and
passes13 missing/default/newline/size/directory/symlink checks. Actual native
Android stream.cpp and host encoder_settings.cpp each pass ON/OFF syntax checks
with their existing build commands and private source copies. No native tree is
modified. The original attempted source-path override mixed two pragma-once
header trees and caused host duplicate declarations; the final harness keeps
one native header tree and uses the owned new helper only. This was a harness
failure, not a qualified native result, and its raw command output remains in
/tmp/sub15-native-quality-geometry-qualification/partial.json.

results/qualification.json records final commands, exact new source/compiler/
compile-database/binary hashes and unchanged new sources. Existing native
dependency headers are borrowed from the root build tree; this is standalone
admission and translation-unit qualification, not a linked application build.
Root controls final builds, installed-artifact checks and all headset execution.
Read-only codec-team review found no admission/bounds/default-path blocker.
