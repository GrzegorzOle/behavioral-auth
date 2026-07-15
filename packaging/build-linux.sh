#!/usr/bin/env bash
# Build the self-contained one-folder Linux bundle and add the command symlinks.
#
# Produces dist/behavioral-auth/ with:
#   behavioral-authd            the real binary (the daemon)
#   behavioral-auth   -> ...    symlink, CLI (status/report/reset/learn-more)
#   behavioral-report -> ...    symlink
#   behavioral-face   -> ...    symlink
#   config/config.yaml          default config the user can edit in place
#   _internal/                  all bundled dependencies (torch CPU, onnx, cv2, ...)
#
# Run from the repo root with a venv that has pyinstaller installed (make bundle
# does this for you).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYINSTALLER="${PYINSTALLER:-pyinstaller}"

echo ">> Building bundle with PyInstaller"
"$PYINSTALLER" --noconfirm packaging/behavioral-auth.spec

DIST="$ROOT/dist/behavioral-auth"
echo ">> Adding command symlinks in $DIST"
for name in behavioral-auth behavioral-report behavioral-face; do
    ln -sf behavioral-authd "$DIST/$name"
done

echo ">> Done. Bundle at: $DIST"
du -sh "$DIST"
