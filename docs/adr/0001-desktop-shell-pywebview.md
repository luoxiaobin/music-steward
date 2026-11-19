# ADR 0001: Desktop shell — pywebview, not Electron

* **Status:** Accepted
* **Date:** 2026-11-18
* **Deciders:** Kevin Luo

## Context

Music Steward (formerly MP3LibraryTool) is a single-user app. Its engine is Python: the inventory scanner, beets (in-process, for matching), mutagen (tags), and the apply journal. The UI is a local web app served by that same Python process (`music-steward serve`), opened in a browser tab, written in plain HTML/CSS/JS.

A browser tab is not ideal for a single-user desktop tool: no dock icon, no native folder picker (we rely on configured roots), and a one-time token to protect the localhost server. It must run on the Mac mini and on Windows.

We considered Electron.

## Options

| | Browser tab (today) | pywebview | Electron | Tauri |
|---|---|---|---|---|
| Native window, dock icon | no | yes | yes | yes |
| Native folder picker | no | yes | yes | yes |
| Languages | Python + JS | Python + JS | Node + Python sidecar + JS | Rust + Python sidecar + JS |
| Footprint | none | small (system WebKit / WebView2) | ~150 MB+ Chromium | small |
| Reuses current UI | — | unchanged | mostly | mostly |
| Extra toolchain | none | none | Node, electron-builder | Rust, cargo |

## Decision

1. Keep the architecture: **one Python process** owns the service layer; the UI is web code (HTML/CSS/JS) that talks to it.
2. Wrap it in a native window with **pywebview** (BSD-licensed) when review is usable — **at the end of M2**. pywebview gives a dock/taskbar window and a native folder dialog on macOS and Windows, using the system web engine.
3. **Do not use Electron.** It would still need our Python engine as a sidecar process, adds a second runtime and ~150 MB, and complicates packaging and signing on two OSes, without solving any limit of the web UI (e.g. Chromium cannot play APE either).
4. Keep the browser mode (`serve`) working alongside the window (e.g. to check progress from another machine).

## Consequences

* UI work continues in the browser for fast iteration; nothing written now is wasted.
* The desktop window is ~1 day of work: launch `serve` in-process, open a pywebview window on it, expose the folder picker through pywebview's JS API.
* In the window, the session token can be injected directly instead of via the launch URL; the Host-header check, CSP and POST guard stay.
* Browser constraints remain (APE preview needs transcoding later, whatever the shell).

## Revisit when

* the app is to be distributed to non-technical users with installers and auto-update, or
* the UI grows into something that needs a heavy JS framework and its tooling.

Then prefer **Tauri** over Electron (smaller), still with the Python engine as a sidecar.
