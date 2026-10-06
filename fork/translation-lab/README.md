# WiVRn translation lab

Godot 4.7 test room authored and inspected through Godot AI MCP. Use the Vulkan
Mobile renderer: the installed OpenGL driver failed Godot's multiview shaders.
The room is a native OpenXR application, not a desktop video shown in VR.

## Run

Start WiVRn Pro, connect the matching Quest client, then run `./launch.sh` from
this folder. Or open `project.godot` in Godot and play the main scene. The local
Godot AI 4.0.3 addon is installed in `addons/godot_ai` but is excluded from Git;
install that addon in a fresh checkout before opening the project. It supplies
the editor MCP connection and runtime debugger helper.

The launcher selects the local WiVRn Pro OpenXR runtime and routes audio to
`wivrn.sink`. The scene also supports a desktop preview with `--xr-mode off`.

## Test content and controls

Three capsules are at (-1, 0, -1.2), (1, 0, -1.2), and (0, 0, -2.5) metres.
Their maximum pairwise separation is 2 metres. Enter a 1.1-metre horizontal
radius to start the full recording. Leave beyond 1.45 metres to re-arm the
trigger. Playback continues to its end when leaving and is spatially attenuated.
Overlapping playback is possible for testing voice distinction.

| Capsule | Recording length |
| --- | --- |
| English | 12.44 seconds |
| Japanese | 10.50 seconds |
| Korean | 13.14 seconds |

The recordings are randomly selected, unmodified Google FLEURS test samples
from the Fluid Inference mirror, under CC BY 4.0. Exact URLs, hashes and reference
transcripts are in `assets/audio/sources.json`; attribution is in the same folder.

Four panes contain short Japanese, Russian, Chinese and Korean phrases. Their
positions and contrasting foreground/background colours are randomized at start.
Jupiteroid is used for English labels; a Noto CJK font subset covers pane text.
Both font licences are included alongside the font files.

- Left stick: move. Right stick: 30-degree snap turn.
- Right grip within 40 cm of the pen: pick up; grip again: put it on the stand.
- Right trigger while holding the pen: write in open space.
- Left X: clear ink. Right A: randomize pane colours. Right B: face the speakers
  and return to the starting point.
- Ink particles and interpolated beads stay fixed in the world for 45 seconds,
  with a 12,000-bead bound. Stationary input does not accumulate duplicate beads.
- Desktop preview: WASD, right-drag to look, Tab to pick up pen, left-drag to
  write, C to clear, R to recolour, and 1/2/3 to approach a speaker.

Enable speech or scene translation independently in the WiVRn unified GUI's
Subtitles tab. Supply an OpenAI API key there and apply. No key is bundled in
this project. This scene does not enable capture or API usage by itself.

## Checks performed

Godot MCP confirmed an active OpenXR session, three speakers, four panes, all
sample lengths above ten seconds, and maximum speaker spacing of two metres.
Runtime movement triggered each recording. Programmatic pen strokes produced
223 retained beads and clearing removed them. A Quest Pro ADB screenshot showed
the scene after switching to Vulkan. API-backed translation quality and manual
controller handwriting still require live testing.
