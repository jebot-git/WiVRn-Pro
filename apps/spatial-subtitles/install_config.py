# SPDX-License-Identifier: GPL-3.0-or-later
import json, os, shlex, shutil, subprocess
from pathlib import Path

home = Path.home()
prefix = Path(os.environ["SPATIAL_SUBTITLES_PREFIX"])
data = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
app = data / "spatial-subtitles"
python = app / "venv/bin/python"
modules = prefix / "share/spatial-subtitles"


def write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


# Explicit layer only. Ordinary OpenXR applications are unaffected.
layer = prefix / "share/openxr/explicit.d"
write(
    layer / "spatial-subtitles.json",
    json.dumps(
        {
            "file_format_version": "1.0.0",
            "api_layer": {
                "name": "XR_APILAYER_SPATIAL_subtitles",
                "library_path": str(prefix / "lib/libXR_APILAYER_SPATIAL_subtitles.so"),
                "api_version": "1.0",
                "implementation_version": "1",
                "description": "Spatial Subtitles opt-in scene capture",
            },
        },
        indent=2,
    ),
)
write(
    prefix / "bin/spatial-subtitles-capture",
    f"""#!/bin/sh
export XR_API_LAYER_PATH={shlex.quote(str(layer))}${{XR_API_LAYER_PATH:+:$XR_API_LAYER_PATH}}
export XR_ENABLE_API_LAYERS=XR_APILAYER_SPATIAL_subtitles${{XR_ENABLE_API_LAYERS:+:$XR_ENABLE_API_LAYERS}}
exec "$@"
""",
    0o755,
)
write(
    prefix / "bin/spatial-subtitles-settings",
    f'#!/bin/sh\nexec /usr/bin/python3 {shlex.quote(str(modules / "gui.py"))} "$@"\n',
    0o755,
)
write(
    prefix / "bin/spatial-subtitles",
    f"""#!/bin/sh
systemctl --user start spatial-subtitles.target
exec {shlex.quote(str(prefix / "bin/spatial-subtitles-settings"))}
""",
    0o755,
)
units = config / "systemd/user"
write(
    units / "spatial-subtitles.target",
    """[Unit]
Description=Spatial Subtitles local OpenXR overlay
Wants=spatial-subtitles-backend.service spatial-subtitles-overlay.service
""",
)
common = """Restart=on-failure
RestartSec=5
NoNewPrivileges=true
RestrictAddressFamilies=AF_UNIX AF_NETLINK
Environment=HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
"""
write(
    units / "spatial-subtitles-backend.service",
    f"""[Unit]
Description=Spatial Subtitles recognition and translation
PartOf=spatial-subtitles.target
[Service]
Type=exec
ExecStart={python} {modules / "backend.py"}
{common.replace("AF_UNIX AF_NETLINK", "AF_UNIX AF_NETLINK AF_INET AF_INET6")}TimeoutStopSec=20
""",
)
write(
    units / "spatial-subtitles-overlay.service",
    f"""[Unit]
Description=Spatial Subtitles OpenXR overlay
PartOf=spatial-subtitles.target
[Service]
Type=exec
ExecStart={prefix / "bin/spatial-subtitles-overlay"}
{common}TimeoutStopSec=5
""",
)
write(
    data / "applications/spatial-subtitles.desktop",
    f"""[Desktop Entry]
Type=Application
Name=Spatial Subtitles
GenericName=VR Translator
Comment=Speech subtitles and scene text translation in VR
Exec={prefix / "bin/spatial-subtitles"}
Icon={modules / "spatial-subtitles.png"}
StartupWMClass=spatial-subtitles
Keywords=translator;translation;subtitles;OCR;DeepL;OpenXR;
Categories=Utility;Accessibility;
Terminal=false
""",
)
print("Installed independent app, services, and opt-in capture launcher:", prefix)

fonts = data / "fonts/spatial-subtitles"
fonts.mkdir(parents=True, exist_ok=True)
for font in (prefix / "share/fonts/spatial-subtitles").glob("*"):
    if font.is_file():
        shutil.copy2(font, fonts / font.name)
subprocess.run(["fc-cache", str(fonts)], check=True)
if shutil.which("update-desktop-database"):
    subprocess.run(["update-desktop-database", str(data / "applications")], check=True)
