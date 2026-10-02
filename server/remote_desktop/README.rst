Mac desktop relay (prototype)
=============================

The Linux PC remains the WiVRn/OpenXR/WayVR host. A native macOS companion
creates one extended 1920x1080 display and captures only that display through
ScreenCaptureKit. The Linux Qt receiver appears as an ordinary WayVR window.
Focus the remote desktop by clicking it; Ctrl+Alt+Esc or focus loss releases
remote input. Linux desktop windows remain available alongside it.

This initial implementation transports JPEG at up to 15 fps over SSH. It has
mouse motion, three buttons, wheel scrolling and common keyboard keys. It does
not yet have audio, clipboard, gamepad passthrough, low-latency H.264 or
background login registration.
Clicking a WayVR-managed window now captures physical mouse/keyboard input,
including the WiVRn headset passthrough devices. Selecting a Linux desktop
releases capture; Super+Z also releases it. Clicking the Mac window again
reacquires capture. Held keys/buttons defer capture until release. Closing the
selected window and the existing worker watchdog also release device capture.
Non-US keyboard layouts and IME text entry need further work and testing.

Build and run on the Mac
------------------------

Install Apple's Command Line Tools and copy the ``macos`` directory to the Mac.
In that directory::

    ./build.sh
    mkdir -p "$HOME/Applications"
    ditto "build/WiVRn-Pro Mac Desktop.app" "$HOME/Applications/WiVRn-Pro Mac Desktop.app"
    open "$HOME/Applications/WiVRn-Pro Mac Desktop.app"

Grant Screen & System Audio Recording and Accessibility to the companion.
Quit and reopen after granting screen capture permission. The companion must
run in the logged-in graphical session. Closing it removes the virtual display.
Start/retry reports errors instead of falling back to a physical monitor.
The built app is ad-hoc signed for local testing, not signed/notarized for
redistribution. Rebuilding can require renewed privacy permission.

Virtual display creation uses the private CGVirtualDisplay ABI, dynamically
resolved and guarded against exceptions. It must be tested per macOS release;
see ``macos/PROVENANCE.txt`` for the API declaration reference. Capture and
input use Apple's public ScreenCaptureKit and CoreGraphics APIs.

Connection
----------

Enable Remote Login for the Mac account and authorize the Linux PC's public
SSH key. Establish and verify the SSH host key before opening the receiver.
An encrypted private key must be unlocked in the Linux user's SSH agent.
No password is stored or requested by the receiver.

The installed dashboard's **Mac desktop** tab saves the host and account short
name separately from existing WiVRn and Shora configurations. Its launch button
starts the receiver in WayVR's compositor. To run as a regular local window::

    PREFIX/bin/wivrn-pro-mac-desktop --host MAC_HOST --user MAC_SHORT_NAME

To launch inside WayVR directly::

    PREFIX/bin/wivrn-pro-wayvrctl process-launch --name 'Mac desktop' \
      PREFIX/bin/wivrn-pro-mac-desktop 1920x1080 floating \
      PREFIX/share/icons/hicolor/128x128/apps/wivrn-pro-menu.png -- \
      '--host MAC_HOST --user MAC_SHORT_NAME'

The companion listens on Mac loopback only (127.0.0.1:9758). SSH carries the
stream via the system ``nc`` tool. A generated 256-bit token in the Mac user's
``~/Library/Application Support/WiVRn-Pro/relay.token`` (0600) authenticates the
loopback connection. The token is not included in arguments or logs. The GUI
never starts an SSH password prompt; authentication failures remain visible.

Input safety and protocol
-------------------------

Mac-to-Linux frames contain a 10-byte header: WVRD magic, version byte (1), kind
byte (1=JSON format, 2=JPEG, 3=JSON status), and big-endian uint32 payload length.
Payloads are bounded to 4 MiB and decoded images must be exactly 1920x1080.
The receiver drains its process in 64 KiB chunks and drops superseded images.
Linux-to-Mac input is bounded newline JSON (4096 bytes), with monotonic sequence
numbers after token authentication. Coordinates are normalized within the
virtual display and mapped to its current desktop bounds.

Focus loss, closing/disconnecting, a stalled stream or a missing 1.5-second
heartbeat releases remote keys and buttons. A blocked write queue terminates
the connection. The Mac's physical pointer suppresses remote input for 1.5 s
after activity, and held physical buttons retain priority. Existing local
Linux mouse priority remains implemented by WayVR.

Tests
-----

On Linux with PySide6 installed::

    python3 -m unittest discover -s server/remote_desktop -p 'test_*.py' -v

Tests exercise fragmented/coalesced framing, malformed headers, size limits,
SSH argument validation, real JPEG decoding, focus/timeout release, Tab
forwarding and blocked output. Native compilation and virtual-display/input
validation must additionally run on a Mac; Linux tests cannot verify the
private macOS display API or privacy permissions.

Validated platform
------------------

Initial native build and real 1920x1080 capture verified on Apple M4 / macOS
26.6.1 (25G76). System Profiler confirmed an extended virtual display with
mirroring off, leaving the physical display as the main display. The Qt
receiver was launched in the Linux host's existing WayVR compositor.
