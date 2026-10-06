# Reused components

## Stay Aligned

`src/stay_aligned.rs` adapts SlimeVR's rest detector, relaxed pose classification,
paired leg angles, weighted body heading, and neighbor/centering error approach
from [SlimeVR-Server](https://github.com/SlimeVR/SlimeVR-Server), inspected at
`83941fd38e91cc91ca6b360deab5c2ae986dd1b6` (`server/core/src/main/java/dev/slimevr/tracking/processor/stayaligned/`).
The Rust adaptation uses synchronous per-frame updates, per-sensor timestamps,
native control/Qt setup, and the body segments implemented by this server.
It is not a complete Kotlin port. SlimeVR is Copyright (c) 2020 Eiren Rain and
SlimeVR Contributors, MIT OR Apache-2.0. This adaptation uses the MIT option;
the full notice is retained in [licenses/SlimeVR-MIT.txt](licenses/SlimeVR-MIT.txt).

## HaritoraX, SlimeTora, and the Shora TUI

`src/haritorax/` reuses Shora's Rust port of
[JovannMC/haritorax-interpreter](https://github.com/JovannMC/haritorax-interpreter)
and its [SlimeTora](https://github.com/OCSYT/SlimeTora)-compatible tracker identity
generation. The reused Shora revision is
`8e11b57b2bc2c3e752188cc71853341580377568`. `src/ui/` and file logging adapt Shora's
ratatui frontend and logging approach for the native server.

The integration covers GX6/GX2 serial acquisition, HaritoraX 2/Wireless IMU
interpretation, stable IDs, battery and button reports. It adds managed worker
lifecycle, direct registry input, shared status and commands, and a new Qt frontend.
It does not embed the complete SlimeTora application or JavaScript package.

- SlimeTora: Copyright (c) 2024 BracketProto & JovannMC,
  [MIT notice](licenses/SlimeTora-MIT.txt).
- haritorax-interpreter: Copyright (c) 2024 JovannMC,
  [MIT notice](licenses/haritorax-interpreter-MIT.txt).
- Shora: MIT OR Apache-2.0; its MIT notice matches [LICENSE-MIT](LICENSE-MIT).

The optional Qt frontend uses separately installed PySide6 in the RPM and source
installations. DEB and AppImage builds bundle Python and PySide6 Essentials;
their notices and shared-library notices are included in the bundle's `licenses/`
directory. See [portable packaging](packaging/linux/README.md) for versions,
upstream sources and library replacement. Qt/PySide6 retain their own upstream
licensing; they are not relicensed by this project.

## oscavmgr face/eye integration

`src/face/` adapts the face/eye pipeline from the adjacent Shora repository,
commit `8e11b57b2bc2c3e752188cc71853341580377568`. Shora's port is based on
[galister/oscavmgr](https://github.com/galister/oscavmgr):

- Unified and combined expression model, Meta face-weight mapping.
- Babble / EyeTrackVR address mappings.
- OSC avatar parameter mapping and bundle output.
- OpenXR headless face/eye source.

The server-specific configuration, worker lifecycle, socket handling, OSCQuery
integration, fixed UniFT output, and VRChat avatar JSON input-route importer were
adapted or added here. This is an embedded subset, not the
complete oscavmgr application. Autopilot, Gogo Loco, external storage, ALVR,
and non-Meta OpenXR face providers are not included.

oscavmgr is Copyright (c) 2023 galister, MIT licensed. Its full license is retained
in [licenses/oscavmgr-MIT.txt](licenses/oscavmgr-MIT.txt). Shora is distributed under
MIT OR Apache-2.0; its MIT notice matches this project's [LICENSE-MIT](LICENSE-MIT).

## HaritoraX sensor controls

The GX settings layout in `src/haritorax/settings.rs` follows
`getSettingsHexValue` / `setTrackerSettings` and the sensor mode definitions from
haritorax-interpreter commit `abd9bc89fb4100351aed42d1e703ddc2bebc4029`
(`src/HaritoraX.ts`, `src/types.ts`), cross-checked against SlimeTora commit
`5fee86ead0cda39275ede72a2fa7108a00f7e354` (`src/static/js/settings.ts`). The MIT
notices above apply. This adaptation uses read-modify-write plus fresh echo
verification, preserves unrelated fields, and exposes sensor modes only.
