# Anchored passthrough windows over ordinary XR applications

## Finding

Feasible, with compositor/client changes. The running PC XR application does not
need MR support: WiVRn owns the headset OpenXR session and can reveal native
passthrough while continuing to stream the application. Current Monado color
keying alone cannot define an anchored window. The client prototype described at
the end of this document now implements an anchored aperture; it has not been tested in a headset. The server compositor
alternatives below remain design proposals.

This inspection uses WiVRn-Pro's current sources and the configured Monado source
at revision `f037264d23e2472a444a157370647fcd601ed81b` (see `monado-rev`).

## Existing path and its limits

1. WayVR's `try_apply_chroma_key` calls Monado IPC with HSV ranges, curve and
   despill. There is no anchor, pose, size or spatial-mask argument.
2. Monado `comp_multi_system.c` applies those parameters to the visible **base
   session's projection/projection-depth layers**, and changes that session's
   environment blend mode to alpha. It does not key arbitrary overlay quads.
3. WiVRn `server/compositor/layer_squasher.cpp` copies the per-layer key parameters
   into the compute shader. Monado's `layer.comp` keys sampled projection colors
   before source-over composition, including the quad-view inset/context path.
4. `server/compositor/compositor.cpp` sets `view_info.alpha` from the resulting
   environment blend mode and encodes an additional alpha stream.
5. `client/scenes/stream.cpp` enables local passthrough and submits the streamed
   projection with source-alpha blending. `client/shaders/reprojection.glsl`
   reconstructs the transmitted alpha. Camera imagery remains on the headset;
   anchors provide placement, not camera pixels.

A game can therefore already show passthrough wherever its image matches the
chosen key color. That selection follows pixels/content, not a fixed location in
the room. A green overlay is not a ready-made portal: the key is applied to the
base projection before that overlay is composed. A transparent overlay also
cannot erase an opaque layer beneath it. With ordinary source-over blending,
zero-alpha content merely leaves the game visible.

There is a second integration detail: Monado's `find_active_blend_mode` chooses
opaque if **any** visible session is opaque. WayVR requests alpha when passthrough
or keying is enabled, but another opaque overlay can still suppress the alpha
transport. A server portal needs explicit blend-mode arbitration, not just a
shader that produces zero alpha.

## Recommended Monado/WiVRn implementation

Start with one rectangular aperture attached to a persisted anchor UUID. Store
its pose relative to that anchor, width, height and optional feathered border.
Each eye ray intersects the anchor-relative plane; a hit inside the rectangle
produces a mask `M` in [0,1]. Build the mask from compositor-time eye poses/FOVs,
not a fixed screen rectangle or only the left eye. Handle near-plane crossings,
back-face policy, stereo disparity, timewarp, foveation and quad views explicitly.

Two distinct modes can reuse the existing alpha/passthrough transport:

- **Open window:** remove virtual content throughout the aperture, independent
  of app colors. For premultiplied content, multiply both RGB and alpha by
  `1 - M`. This works over an ordinary opaque application.
- **Keyed window:** apply the current chroma-key result only inside the aperture:
  `output = mix(original, keyed, M)`. This preserves non-keyed virtual objects
  inside the window, but only according to their colors, not real-world depth.

For a guaranteed hole through the whole application, apply the open-window mask
once to the **composed base-session image**, including its other layer types.
Keying/masking only its main projection could leave the application's quad HUD
covering the hole. Composite desktop/WayVR overlays afterward if they should
remain usable above the portal. The current flattened layer path does not retain
a convenient base-session group boundary: either carry ownership/group metadata
from Monado or introduce an intermediate base-session composition. A simpler
first prototype may cut the final PC composite, but it would also cut WayVR and
other PC overlays; that is a deliberate UX tradeoff.

Force the alpha transport while a valid portal is active, even if the application
submitted opaque. Preserve app alpha/key settings and restore normal blend-mode
selection when the portal is disabled. Do not mutate the game's textures or
require an injected OpenXR API layer in the game.

Use aligned `pc_stage` anchor poses for a PC implementation and compose once into
the squasher's world/tracking-origin coordinates. Do not assume PC STAGE equals
Monado's internal root; do not apply the bridge's alignment twice. Match the mask
to the same eye/world frame and time used to compose the image.

## Client alternative for better room stability

The Quest client already locates its anchors each render frame at predicted
display time, before limiting published bridge snapshots to 5 Hz. A client-side
aperture can use these fresh `quest_stage` poses directly, avoiding the
PC/encoding/network round trip and compressed alpha edges. Apply the aperture
after decoding/reprojection with coordinates matching the submitted view poses,
and enable passthrough/source-alpha blending even when the server sent opaque
video. Preserve incoming alpha outside the aperture. This still cuts all PC
content inside the window; local WiVRn UI can be submitted above it.

A Quest-specific alternative is an `XR_FB_passthrough` **projected** layer with a
rectangle triangle mesh, composed above the game. That needs projected-purpose
layer creation, triangle-mesh/geometry-instance support and runtime capability
checks. Current WiVRn only creates the reconstruction-purpose FB layer; its HTC
path is planar. Projected passthrough is therefore another implementation option,
not an existing switch or a demonstrated cross-headset solution.

For WiVRn-Pro's Quest-focused use, a client aperture is the strongest follow-up
for visual stability. Use the server path when preserving selected PC overlays
or integrating spatially restricted Monado keying is the priority. Do not require
PC control traffic to update the anchor pose every frame.

## Placement, lifecycle and validation

The existing bridge socket is read-only. A PC GUI may consume it for previews,
but placing/configuring a client portal needs a separate validated command path
in WiVRn's control protocol, or initial placement directly in the headset UI.
A proposed UI is: choose anchor / place rectangle, adjust size and offset, enable,
choose open versus keyed window, disable. Save anchor UUID plus relative pose,
not a stale absolute PC transform. Do not assume the guided capture's marker
poses already coincide with a desired desk/window surface.

One localized 6-DoF anchor is geometrically sufficient; optional multi-anchor
consistency checks can detect reference uncertainty. Three camera-visible anchors
are not a rendering requirement, and runtime localization need not imply that
an anchor is currently in the camera view. Check pose flags, UUID, session,
generation and freshness. On loss of tracking/relocalization, hide the aperture
and show its unavailable state; do not keep displaying a stale positioned hole.
Reacquire before restoring it. Retain explicit full-passthrough access separately.

A useful first validation is an opaque non-MR test app with a moving colored
background and quad HUD, one anchored rectangle, and a WayVR overlay. Verify
both eyes, head movement, recentering, anchor loss/reload, reconnect, disabling,
opaque overlay sessions, edge quality and alpha activation. Then test a real
OpenXR app and an OpenVR app through the runtime bridge. App independence is a
compositor property; those paths still need integration testing.

There is no reliable physical/virtual depth occlusion from color keying or anchor
poses alone. An open window deliberately shows the real world over whatever the
game draws there. Native passthrough availability/permission, anchor localization,
and headset runtime support remain prerequisites.

## Implemented client prototype

The branch now implements **one freely oriented, two-sided rectangular cutout** in the
streaming client. It removes all streamed PC content inside the aperture; local
WiVRn menus/controllers remain separate layers. No Monado key configuration or
application MR support is needed.

1. Localize at least one persisted room marker with Room capture.
2. Open the in-stream WiVRn menu (both thumbsticks), then **Settings → Room
   capture → Anchored passthrough cutout → Design cutout**.
3. Release both triggers/pinches. Put the controllers or tracked hands at opposite
   diagonal corners and pull them apart horizontally and vertically. The upright
   plane follows the horizontal line between your hands: turn the pair around its
   midpoint to yaw the window, or move both hands together to pan it left/right,
   up/down, or nearer/further away. Keep their separation unchanged to preserve
   size while moving. The window starts upright and does not turn with your head.
   Hold **one trigger or one pinch** to grab a corner, then move or rotate that
   controller/hand to pan, pitch, or roll the entire window without changing its
   size. Press the other trigger/pinch while holding to save, or release to resume
   resizing in the tilted plane. Releasing does not snap the window back upright.
   Width and height must each be 10 cm–3 m. A teal outline surrounds the live
   passthrough preview.
4. Hold **both triggers** or **both thumb/index pinches** together for 0.2 seconds
   to confirm. Input loss or a switch between controllers and hands requires
   another release. Closing an XR session or losing its room reference cancels
   an unfinished design. **Cancel cutout design** keeps an earlier saved mask.
5. Use **Enable cutout**, **Redesign cutout**, or **Delete active cutout** in that
   same menu. Deleting the rectangle does not erase its room anchor.

The client saves the anchor UUID, relative pose, dimensions and enabled state in
`spatial-bridge-v1/cutout-v1.txt` using a versioned atomic replacement. It reloads
across client launches and stream reconnects. No absolute PC or STAGE transform
is persisted. Tracking loss hides a saved mask until that same anchor is tracked
again. Room-capture mode takes precedence over cutouts.

The shader intersects per-eye rays with the rectangle using a homogeneous
transform. It handles asymmetric FOVs, stereo disparity and near-plane crossings,
rejects behind-eye intersections, and removes premultiplied RGB and alpha together.
The mask uses the **same video view poses/FOVs submitted to OpenXR** and freshly
localized anchor poses. It is recomputed even for repeated video frames, including
one final redraw when hidden/deleted, to avoid leaving an old aperture onscreen.
The headset's compositor then timewarps the submitted layer. This prototype does
not separately reproject the video to newly predicted eye poses or provide depth
for positional timewarp; near-field stability under rapid translation still needs
headset measurement. Existing alpha is preserved outside the rectangle.

Validation: native geometry/editor tests (persistence, cancellation, rearming,
tracking loss, reference transforms, stereo/asymmetric FOVs and invalid geometry),
GLSL vertex/fragment compilation, host syntax checks of all four modified
streaming translation units against pinned dependency headers, and the repository
offline suite. Android APK/device rendering
and hand/controller interaction still require hardware validation.
