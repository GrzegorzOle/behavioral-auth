#!/usr/bin/env bash
# Wrap the PyInstaller one-folder bundle into a single-file AppImage.
#
# Prerequisites:
#   - the bundle exists (dist/behavioral-auth/); this script runs build-linux.sh
#     if it does not.
#   - appimagetool on PATH, or its path in the APPIMAGETOOL env var. Grab it from
#     https://github.com/AppImage/AppImageKit/releases (appimagetool-x86_64.AppImage).
#
# Output: dist/behavioral-auth-x86_64.AppImage
#
# Running the result: it is invoked as `./behavioral-auth-x86_64.AppImage <command>`
# where <command> is authd | auth | report | face (see AppRun). The type-2 runtime
# needs FUSE 2 (libfuse.so.2) on the target; distros that ship only FUSE 3 (recent
# Fedora) must install fuse-libs, or run with APPIMAGE_EXTRACT_AND_RUN=1.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BUNDLE="$ROOT/dist/behavioral-auth"
APPDIR="$ROOT/dist/behavioral-auth.AppDir"
OUT="$ROOT/dist/behavioral-auth-x86_64.AppImage"
APPIMAGETOOL="${APPIMAGETOOL:-appimagetool}"

if [ ! -d "$BUNDLE" ]; then
    echo ">> Bundle missing — building it first"
    PYINSTALLER="${PYINSTALLER:-pyinstaller}" bash "$ROOT/packaging/build-linux.sh"
fi

echo ">> Assembling AppDir at $APPDIR"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
# The bundle (binary + symlinks + _internal + config) becomes usr/bin/*.
cp -a "$BUNDLE"/. "$APPDIR/usr/bin/"

install -m 0755 "$ROOT/packaging/AppRun"                 "$APPDIR/AppRun"
install -m 0644 "$ROOT/packaging/behavioral-auth.desktop" "$APPDIR/behavioral-auth.desktop"
install -m 0644 "$ROOT/packaging/behavioral-auth.png"     "$APPDIR/behavioral-auth.png"
cp "$ROOT/packaging/behavioral-auth.png" "$APPDIR/.DirIcon"

echo ">> Building AppImage with appimagetool"
# ARCH is required for reproducible naming; EXTRACT_AND_RUN avoids needing FUSE
# for appimagetool itself (e.g. in CI / sandboxes).
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL" "$APPDIR" "$OUT"

echo ">> Done: $OUT"
ls -lh "$OUT"
