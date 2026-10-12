#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Run as the unprivileged builder in Containerfile.arch with /src, /build and /out mounted.
set -euo pipefail

source_dir=$(cd "$(dirname "$0")/../.." && pwd)
build_root=${BUILD_ROOT:-/build}
output_dir=${OUTPUT_DIR:-/out}
build_dir="$build_root/server"
version=${PACKAGE_VERSION:-26.9.2}
release=${PACKAGE_RELEASE:-0.3}
release_tag=${RELEASE_TAG:-v26.9.2-frame.3}
source_rev=${1:-${SOURCE_REV:?Set SOURCE_REV or pass the exact source commit as argument 1}}

mkdir -p "$output_dir"
exec > >(tee "$output_dir/arch-frame-build.log") 2>&1
trap 'result=$?; if (( result != 0 )); then printf "Arch release build failed (exit %s).\n" "$result"; fi' EXIT

cd "$source_dir"
[[ $(id -u) != 0 ]] || { echo 'Run as the unprivileged builder; makepkg cannot run as root.' >&2; exit 1; }
[[ "$source_rev" =~ ^[0-9a-f]{40}$ ]] || { echo 'SOURCE_REV must be a full Git commit SHA.' >&2; exit 1; }
[[ $(git rev-parse HEAD) == "$source_rev" ]] || { echo 'SOURCE_REV does not match the checkout.' >&2; exit 1; }
git diff --exit-code HEAD --

export CARGO_HOME=${CARGO_HOME:-"$build_root/cargo-home"}
export CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-${BUILD_JOBS:-4}}
export QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-offscreen}
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-"$build_root/runtime"}
mkdir -p "$HOME" "$CARGO_HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

bash fork/packaging/build-linux.sh "$build_dir" \
    -DGIT_TAG= -DGIT_DESC="$release_tag" -DGIT_COMMIT="$source_rev" \
    -DWIVRN_USE_PYROWAVE=ON

# The offline suite includes the source-tagged hand packet and cross-architecture wire tests.
dbus-run-session -- bash fork/test-offline.sh

# Exercise the real server history and Monado math, including inactive/source transitions.
# The remaining standalone portability tests already run in test-offline.sh.
monado_source="$build_dir/_deps/monado-src"
monado_headers="$monado_source/src/xrt/include"
monado_auxiliary="$monado_source/src/xrt/auxiliary"
test_dir="$build_root/frame-tests"
mkdir -p "$test_dir"
cxx=${CXX:-c++}
"$cxx" -std=c++23 -Wall -Wextra -Werror -Iserver/driver -I"$monado_headers" \
    fork/tests/controller_roles_test.cpp -o "$test_dir/controller-roles-test"
"$test_dir/controller-roles-test"
sed -e 's/^#cmakedefine01 \([A-Z0-9_]*\)$/#define \1 0/' -e 's/@[A-Z0-9_]*@//g' \
    common/wivrn_config.h.in > "$test_dir/wivrn_config.h"
read -r -a eigen_flags <<< "$(pkg-config --cflags eigen3)"
"$cxx" -std=c++23 -O1 -pthread -ffunction-sections -fdata-sections -Wl,--gc-sections \
    -Iserver -Iserver/driver -Icommon -Iexternal -I"$test_dir" \
    -I"$monado_headers" -I"$monado_auxiliary" "${eigen_flags[@]}" \
    fork/tests/hand_joints_list_test.cpp server/driver/hand_joints_list.cpp \
    server/driver/pose_list.cpp server/driver/clock_offset.cpp server/driver/xrt_cast.cpp \
    "$monado_auxiliary/math/m_space.cpp" "$monado_auxiliary/math/m_base.cpp" \
    common/crypto.cpp common/smp.cpp -lcrypto -o "$test_dir/hand-joints-list-test"
"$test_dir/hand-joints-list-test"

# Keep the production Release dependency caches when running the portability Rust suites.
CARGO_TARGET_DIR="$build_dir/server/shora/cargo" cargo test --workspace --locked --release \
    --manifest-path server/shora/Cargo.toml
CARGO_TARGET_DIR="$build_dir/server/motoc/cargo" cargo test --locked --release \
    --manifest-path server/motoc/upstream/Cargo.toml
CARGO_TARGET_DIR="$build_dir/server/wayvr/cargo" cargo test --locked --release \
    --manifest-path server/wayvr/upstream/Cargo.toml -p wayvr \
    --no-default-features --features openxr,wayland,x11

python3 fork/packaging/package-linux.py arch "$build_dir" "$output_dir" \
    --version "$version" --release "$release" --commit "$source_rev"
package="$output_dir/wivrn-pro-$version-$release-x86_64.pkg.tar.zst"
bsdtar -xOf "$package" .BUILDINFO > "$output_dir/ARCH-BUILDINFO.txt"
cp build/native-tests/wire-host.txt "$output_dir/PROTOCOL.txt"
python3 - "$package" "$output_dir" "$source_rev" "$release_tag" "$version" "$release" <<'PY'
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

package, output, commit, tag, version, release = sys.argv[1:]
output = Path(output)
def archive_member(name):
    return subprocess.check_output(['bsdtar', '-xOf', package, name], text=True)

pkginfo = archive_member('.PKGINFO')
for expected in ['pkgname = wivrn-pro', f'pkgver = {version}-{release}', 'arch = x86_64']:
    assert expected in pkginfo.splitlines(), f'Package metadata mismatch: {expected}'
installed_info = json.loads(archive_member('usr/share/doc/wivrn-pro/build-info.json'))
assert installed_info['commit'] == commit
assert installed_info['version'] == version and installed_info['release'] == release
protocol = (output / 'PROTOCOL.txt').read_text()
match = re.search(r'^Protocol hash: ([0-9a-f]+)$', protocol, re.MULTILINE)
assert match, 'Wire test did not report a protocol hash'
metadata = {
    'commit': commit, 'tag': tag, 'version': version, 'release': release,
    'package': Path(package).name,
    'sha256': hashlib.file_digest(open(package, 'rb'), 'sha256').hexdigest(),
    'protocol_hash': match.group(1),
    'checks': ['offline', 'controller_roles', 'hand_joints_history',
               'shora_release_tests', 'motoc_release_tests', 'wayvr_release_tests',
               'package_staging', 'package_metadata'],
    'installed_validation': 'Run separately in a fresh Arch container.',
    'os_release': Path('/etc/os-release').read_text(),
}
(output / 'arch-build-info.json').write_text(json.dumps(metadata, indent=2) + '\n')
print(json.dumps(metadata, indent=2))
PY
