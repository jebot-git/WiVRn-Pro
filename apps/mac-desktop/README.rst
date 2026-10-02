Mac Desktop Relay
=================

An independent Linux Qt application and macOS background agent sharing one
virtual 1920x1080 Mac desktop over SSH. No WiVRn, OpenXR, SlimeVR, systemd server
or VR headset is required. WayVR is an optional destination for the same client
window. This project builds independently from this directory.

Linux application
-----------------

Requires Python 3.11+, PySide6, OpenSSH and CMake. Build/install independently::

    cmake -S apps/mac-desktop -B build/mac-desktop \
      -DCMAKE_INSTALL_PREFIX="$HOME/.local/opt/mac-desktop-relay"
    cmake --install build/mac-desktop

Open the installed ``Mac Desktop Relay`` menu entry, enter the Mac hostname and
account short name, and choose **Open desktop**. **Open in WayVR** is optional
and enabled only when ``wayvrctl`` or ``wivrn-pro-wayvrctl`` is available.
To connect directly::

    PREFIX/bin/mac-desktop-relay --connect --host MAC_HOST --user MAC_SHORT_NAME

The application can be copied/built separately from the WiVRn-Pro source tree.
Its only Linux runtime dependencies are Python/PySide6 and OpenSSH. The host
application is not a boot service; start it from KDE's menu when needed.

SSH and settings
----------------

Enable Remote Login for the Mac account, authorize the PC's public key, and
verify the SSH host key before using the client. Unlock encrypted private keys
in the desktop user's SSH agent. The application does not store passwords or
open password prompts.

Settings now belong to ``MacDesktopRelay/Client`` (normally
``~/.config/MacDesktopRelay/Client.conf``). Existing host/account settings from
``WiVRn-Pro/MacDesktop`` are copied once, without overwriting a new target or
modifying the old configuration. WiVRn's dashboard no longer owns a Mac tab.

Mac background agent
--------------------

Requires macOS 14+ and Apple's developer tools to build. Copy the ``macos``
directory to the Mac and run from there::

    bash build.sh
    bash install-agent.sh

The installer creates ``~/Library/LaunchAgents/local.mac-desktop-relay.plist``.
It runs in the logged-in Aqua session with ``--background``, starts at login,
and restarts after abnormal exits. It has a small **MDR** menu-bar menu for
settings/retry/quit; no settings window or Dock icon is opened on startup.
It uses a user login agent because capture/input need the graphical session.
It does not provide access before login or bypass the lock screen.

For first-time setup, open the installed companion or use its MDR menu to grant
Screen & System Audio Recording and Accessibility. Reopen/restart after changing
capture permissions. Closing the settings window keeps the background agent
running; **Quit Mac Desktop Relay** stops it for the current login session.

Agent management::

    launchctl print "gui/$(id -u)/local.mac-desktop-relay"
    launchctl kickstart -k "gui/$(id -u)/local.mac-desktop-relay"
    launchctl bootout "gui/$(id -u)/local.mac-desktop-relay"

To disable future login launches, also remove the matching plist from
``~/Library/LaunchAgents``. Logs are in
``~/Library/Logs/Mac Desktop Relay/agent.log``.

New installations use ``~/Applications/Mac Desktop Relay.app``. Upgrades retain
an existing ``WiVRn-Pro Mac Desktop.app`` path and bundle/signing identifier to
preserve macOS privacy grants where possible. That compatibility identity does
not require WiVRn on either computer. The build is ad-hoc signed for local use;
redistribution still needs signing/notarization, and macOS may require privacy
grants again after rebuilding.

Display, input and transport
----------------------------

The companion creates one extended 1080p display through a dynamically resolved
private CGVirtualDisplay API and captures only that display with ScreenCaptureKit.
Creation failure is reported; it never substitutes a physical display. The
private ABI requires per-release validation (see ``macos/PROVENANCE.txt``).
The existing physical monitor remains the main display.

The first transport is JPEG at up to 15 fps. Mouse motion, three buttons,
double-clicking, scroll and common keyboard keys are supported. Audio,
clipboard, gamepad passthrough, low-latency H.264 and complete non-US/IME keyboard
support are not yet implemented. One receiver may connect at a time.

Click inside the remote desktop to control it. Ctrl+Alt+Esc, losing focus,
closing/disconnecting or missing the 1.5-second input heartbeat releases input.
A blocked write queue or stalled frame stream closes the connection. The Mac's
physical mouse has priority until its buttons are released and it is idle for
1.5 seconds. When used in WayVR, its physical-input capture/priority applies:
select a Linux desktop or press Super+Z to release devices back to Linux.

The Mac listens only on ``127.0.0.1:9758``; SSH carries the stream using system
``nc``. A 256-bit token in ``~/Library/Application Support/Mac Desktop Relay``
(0600) authenticates it. An existing WiVRn-Pro token is copied once; the receiver
also supports the old token path when connecting to an older companion. The
token is never placed in process arguments or logs.

Frames use a 10-byte header: WVRD, version 1, kind (1=JSON format, 2=JPEG,
3=JSON status), and big-endian uint32 length. Payloads are bounded to 4 MiB and
images must decode to 1920x1080. Input uses newline JSON limited to 4096 bytes,
monotonic sequence numbers, and normalized display coordinates.

Validation
----------

Run the standalone test suite::

    ctest --test-dir build/mac-desktop --output-on-failure

It covers profile migration, operating without WayVR, optional WayVR launch
arguments, framing limits, SSH target validation, real JPEG decoding, focus
release, Tab, backpressure and stream loss. Native compilation and actual
capture must also be validated on a Mac. The initial implementation was built
and captured successfully on Apple M4 / macOS 26.6.1 (25G76).

Apple's login-agent documentation:
https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html
