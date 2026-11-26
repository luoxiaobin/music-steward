# The macOS app

`Music Steward.app` is the same program as `music-steward app`, packaged with its own Python so it opens from the
Finder or the Dock. It is built by CI (`.github/workflows/macos-app.yml`) on Apple silicon, checked by its own
self-test, and kept as a download on the run's page for 30 days; on a version tag it is added to the release.

## What the app still needs from the Mac

* **FFmpeg** (required) and **Chromaprint** (for audio fingerprints): `brew install ffmpeg chromaprint`. The app looks
  in Homebrew's folders even when started from the Finder; if FFmpeg is missing, the window says so.
* **Keys** stay where they are, in `~/.zshenv` ([CONFIGURATION.md](CONFIGURATION.md)). When started from the Finder the
  app asks your login shell for exactly these: `ACOUSTID_API_KEY`, `DISCOGS_TOKEN` and the `MLT_*` settings. It keeps
  them in memory only; it never writes, logs or shows them.
* Its data is the same as the command line's: `~/.music-steward/`. The app and `music-steward app` can be used in turn
  (not at the same time).

The first time, with no collection folder yet, the window offers **Add a collection folder…**.

## Unsigned builds (now)

Without a Developer ID the app is unsigned. macOS then refuses a double-click the first time: **right-click the app →
Open → Open**, once. To get one: the *macOS app* workflow's latest run → **Artifacts** → *Music-Steward-macOS*; unzip
and move `Music Steward.app` to Applications.

## Signed and notarized builds (needs the owner's Apple Developer account)

Once these five repository secrets exist (GitHub → Settings → Secrets and variables → Actions), every build is
signed with the hardened runtime, notarized by Apple and stapled, and opens with a double-click:

| Secret | What it is | Where it comes from |
|---|---|---|
| `MACOS_CERT_P12` | the *Developer ID Application* certificate and its private key, as base64 | Xcode → Settings → Accounts → Manage Certificates → **+** → Developer ID Application; then Keychain Access → export it as `.p12`; then `base64 -i cert.p12 \| pbcopy` |
| `MACOS_CERT_PASSWORD` | the password chosen when exporting the `.p12` | — |
| `APPLE_ID` | the Apple ID of the developer account | — |
| `APPLE_TEAM_ID` | the 10-character team ID | developer.apple.com → Membership |
| `APPLE_APP_PASSWORD` | an app-specific password for notarization | account.apple.com → Sign-In and Security → App-Specific Passwords |

Delete the exported `.p12` file afterwards; never commit it (`.gitignore` refuses `*.p12`). Without the three Apple
secrets the build is signed but not notarized.

## Building locally (macOS)

```bash
pip install '.[desktop]' pyinstaller==6.22.3
packaging/macos/build.sh
```

`CODESIGN_IDENTITY="Developer ID Application: …"` signs it; `packaging/macos/notarize.sh` (with `APPLE_ID`,
`APPLE_TEAM_ID`, `APPLE_APP_PASSWORD` in the environment) notarizes it. Any install can check itself with
`music-steward self-test`.

## Limits

* Apple silicon only (the CI machine). An Intel or universal build needs a universal Python; not done yet.
* FFmpeg and Chromaprint are not bundled: Homebrew keeps them up to date, and bundling them would make every app
  update carry their licences and security fixes.
* The bundle identifier is `app.musicsteward.MusicSteward` (in `packaging/macos/MusicSteward.spec`); change it before
  the first signed release if you prefer another.
