#!/bin/bash
# Build "Music Steward.app" into dist/, then run its self-test. From the repository root, on macOS:
#   packaging/macos/build.sh
# With CODESIGN_IDENTITY set (a "Developer ID Application: …" certificate in the keychain) the app is signed
# with the hardened runtime; packaging/macos/notarize.sh then notarizes and staples it.
set -euo pipefail
cd "$(dirname "$0")/../.."
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller packaging/macos/MusicSteward.spec
APP="dist/Music Steward.app"
"$APP/Contents/MacOS/Music Steward" --self-test
if [ -n "${CODESIGN_IDENTITY:-}" ]; then
  codesign --verify --deep --strict --verbose=2 "$APP"
fi
VERSION=$(python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
ditto -c -k --keepParent "$APP" "dist/Music-Steward-$VERSION-macOS.zip"
echo "built: $APP and dist/Music-Steward-$VERSION-macOS.zip"
