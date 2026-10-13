# Native portability packages

Build each Linux package on its target distribution; do not convert Fedora
binaries to DEB or Arch archives. The package includes the server, OpenXR runtime,
Qt dashboard, integrated Shora bridge, WayVR, Motoc and the encoder profiles present in the source tree.
GPU drivers, SteamVR's Lighthouse driver and xrizer remain external installations.
The packages conflict with stock WiVRn packages that share runtime files.

The provided container recipes target Debian 13 (trixie) and current Arch Linux.
The RPM is built on Nobara 44, with Fedora 44 library ABI requirements calculated
by RPM. Older distributions are not supported by these binaries. All Linux
artifacts are x86_64. Hardware streaming requires testing on the intended GPU.

## Build

Install the dependencies in `docs/building.md`, including Python PySide6 with
QtDBus, and Rust 1.95 or newer for the locked WayVR dependency set. Container
recipes provide these dependencies without modifying the host.

```sh
fork/packaging/build-linux.sh build/native -DGIT_COMMIT="$(git rev-parse HEAD)"
python3 fork/packaging/package-linux.py deb build/native build/artifacts \
  --release 1+deb13 --commit "$(git rev-parse HEAD)"
```

Use `rpm` with `--release 1.portability.fc44` on Nobara/Fedora 44 and configure
`-DCMAKE_INSTALL_LIBDIR=lib64`. The GCC build helper keeps
`-Wmaybe-uninitialized` as a warning because nlohmann-json's headers trigger it
in optimized builds with GCC 14 and 16. Other warnings remain errors. Use `arch` with `--release 1` on
Arch, as an unprivileged user (makepkg rejects root). Each format stages its own
installation and leaves running services untouched. CPack derives RPM and DEB
shared-library requirements from the actual binaries. Arch uses makepkg with the
runtime dependencies declared in the generated PKGBUILD.

For example, an isolated Debian build with Podman (no host installation):

```sh
mkdir -p build/package-debian build/artifacts
podman build -t localhost/wivrn-pro-build:debian13 \
  -f fork/packaging/Containerfile.debian fork/packaging
podman run --rm --security-opt label=disable \
  -v "$PWD:/src:ro" -v "$PWD/build/package-debian:/build:rw" \
  -v "$PWD/build/artifacts:/out:rw" -e CARGO_HOME=/build/cargo-home \
  -e SOURCE_REV="$(git rev-parse HEAD)" localhost/wivrn-pro-build:debian13 \
  bash -c '/src/fork/packaging/build-linux.sh /build/server -DGIT_COMMIT="$SOURCE_REV" &&
    python3 /src/fork/packaging/package-linux.py deb /build/server /out \
      --release 1+deb13 --commit "$SOURCE_REV"'
```

For Arch use `Containerfile.arch`, a separate build directory, the `arch`
packaging format and release `1`. Add `--userns=keep-id` to the container command
so makepkg runs without root. The recipes download pinned project dependencies;
distribution packages come from the configured repositories at build time.

To reuse a source cache, pass `-DFETCHCONTENT_SOURCE_DIR_MONADO=/path/to/monado`
pointing to exactly `monado-rev` plus all `patches/monado/*.patch`, in sorted order.
Set the three `WIVRN_{SHORA,WAYVR,MOTOC}_OFFLINE=ON` flags only with populated
Cargo caches. Build directories and Cargo caches must be separate for each OS.

## Install and start

Use the package matching your distribution, with `dnf install ./...rpm`,
`apt install ./...deb`, or `pacman -U ./...pkg.tar.zst`. Installation does not
enable services or change the selected OpenXR runtime. Launch **WiVRn Pro** from
the application menu; choose the tracking mode and encoder in the GUI. Optional
user service activation is described in `docs/building.md`.

The client package ID is `org.meumeu.wivrn.spatial`, for Quest Pro, ARM64.
Locally signed APKs are sideload builds, not Meta Store releases. A development
signature cannot update an installation signed with a different release key.
Uninstalling that installation deletes its local application data, so keep it
until you are ready to switch builds. No headset installation is part of packaging.

Use `--allow-partial-encoders` to package a deliberate encoder subset (for example
an Intel Arc experimental build without NVENC). The package description and
`build-info.json` record the enabled encoders; the default requires all standard
backends. PyroWave is included only when its build option is enabled.
