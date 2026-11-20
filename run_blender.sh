#!/usr/bin/env bash
# Run Blender headless with an ISOLATED user profile so background runs never touch
# the real %APPDATA%/Blender Foundation config, add-ons or extension wheels.
# Usage: ./run_blender.sh <script.py> [-- args...]
B="${BLENDER_EXE:-blender}"   # set BLENDER_EXE if blender is not on PATH
ISO="${BLENDER_ISOLATED_DIR:-$(cd "$(dirname "$0")" && pwd)/.blender_isolated}"
mkdir -p "$ISO"
export BLENDER_USER_RESOURCES="$ISO"
export BLENDER_USER_CONFIG="$ISO/config" BLENDER_USER_SCRIPTS="$ISO/scripts"
export BLENDER_USER_EXTENSIONS="$ISO/extensions" BLENDER_USER_DATAFILES="$ISO/datafiles"
script="$1"; shift
exec "$B" -b --factory-startup --python "$script" "$@"
