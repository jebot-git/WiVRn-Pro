Bounded newest-complete selection experiment

Test-only source lives in common/codec_test/selection_policy.h in the isolated
WiVRn codec source. Missing codec-test-selection-policy.txt defaults to
closest-target. Its only accepted tokens are closest-target and bounded-newest.
The default retains legacy candidate eligibility, stable target-distance tie
ordering, first-frame-ID resource lookup and unconditional previous fallback.
Malformed duplicate IDs with different warps consequently preserve the old
default behavior and fail diagnostic coherence checks instead of being hidden.

bounded-newest is opt-in and experimental. It requires coherent exact stereo
frame/warp/target/alpha identities; alpha requirement still comes from oldest
left history slot. It chooses the greatest eligible encoded frame ID. Targets
and IDs may not regress from the closest-target result or previous coherent
selection. Same ID with different warp, target or alpha cannot replace either.
The future horizon is min(runtime reported predicted period, positive change in
consecutive runtime predicted targets). Missing/invalid period or prior target
yields zero, which permits no advancement beyond the closest identity and no
future target. Bounds include the exact endpoint. Ties retain first left slot.
If history has no complete or eligible candidate, retain only a coherent previous
pair with its own alpha mode. No deadline, scene-generation or optical claim is
inferred from predicted targets or encoded IDs.

The existing frames mutex owns snapshot/selection, resources stay referenced
through the previous render fence, and the existing OpenXR compositor submission
is unchanged. This addresses observed selection retention before adding a custom
compositor or shader. Trace schema 2 records fixed history/previous/closest/selected
snapshots, actual chosen history slots, eligible left slots and runtime bound
inputs. Logging remains after successful xrEndFrame and diagnostic-only.

Build and run locally from repository root:
  c++ -std=c++20 -Wall -Wextra -Werror -pedantic \
    -I.worktrees/wivrn-codec-tests/common \
    prototypes/vr444/experiments/selection_policy/test_selection_policy.cpp \
    -o /tmp/vr444-selection-policy-test
  /tmp/vr444-selection-policy-test
  /tmp/vr444-selection-policy-test --fixtures > /tmp/selection-policy-fixtures.jsonl

Fixture records are synthetic policy cases, including deliberately malformed
metadata and boundary clocks. They are intended for independent cross-language
decision replay, not for passing whole-run clock/provenance qualification.
