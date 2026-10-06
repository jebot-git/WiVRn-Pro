#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
exec "$(dirname "$0")/../../apps/spatial-subtitles/setup-models.sh" "$@"
