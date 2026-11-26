#!/bin/bash
# Notarize and staple a signed build (after build.sh with CODESIGN_IDENTITY). Needs an app-specific password:
#   APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD   (never written to a file or printed; see docs/PACKAGING.md)
set -euo pipefail
cd "$(dirname "$0")/../.."
APP="dist/Music Steward.app"
VERSION=$(python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
ZIP="dist/Music-Steward-$VERSION-macOS.zip"
xcrun notarytool submit "$ZIP" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" --password "$APPLE_APP_PASSWORD" --wait
xcrun stapler staple "$APP"
rm -f "$ZIP" && ditto -c -k --keepParent "$APP" "$ZIP"      # the zip again, now with the ticket stapled
spctl --assess --type execute --verbose=2 "$APP"
echo "notarized: $ZIP"
