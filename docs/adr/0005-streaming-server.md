# ADR 0005: Streaming the organized library to a phone

* **Status:** Proposed (2026-11-22). Settled in conversation with the owner; awaits the phone trial
  ([PHONE_TRIAL.md](../PHONE_TRIAL.md)) before anything is built.
* **Date:** 2026-11-22
* **Deciders:** Kevin Luo

Terms are those of [GLOSSARY.md](../../GLOSSARY.md). "Subsonic" includes OpenSubsonic. Background and sources:
[PHONE_APP.md](../PHONE_APP.md).

## Context

The owner's goal: the organized library, cleaned and fixed by Music Steward, should be **streamable to a phone**,
at home and away from home, from a Mac (later Windows), with no extra work after each copy run.

Facts that shape the decision:

* Phone players that speak the Subsonic protocol are plentiful (48 on iOS; Amperfy is free, open source, 4.5 stars).
  A generic player is not worth building; the scarce thing is a clean library.
* Music Steward is **not** a Subsonic server. Navidrome is, and a throwaway Navidrome already passes our test
  (`tests/test_subsonic.py`): every album once, tracks in order, planned titles, artists, years, durations.
* The desktop app deliberately binds `127.0.0.1` only. A phone cannot use it, and that rule stays.
* A home address (`192.168.x.x`) is unreachable from mobile data; something must bridge it.
* Navidrome is GPL-3.0, as is Music Steward, so the licences are compatible; the question is maintenance and
  user experience, not law (to be re-checked with the actual distribution method before bundling).

## Decision

1. **Use a managed Navidrome as the streaming server.** Music Steward sets up, starts, stops and monitors it; it does
   not write its own Subsonic server now. An own server (a façade over the plan database, about fifteen endpoints) is
   built **only if** the trial and real use show something only we can provide (verified badges, pinyin search,
   review on the phone) that Navidrome cannot.
2. **It serves only the organized copies** (the destination folder). Never a collection folder, never `_ORIGINALS`.
   Read-only: Navidrome keeps its own data in `~/.music-steward/navidrome` (accounts, play counts, cover cache).
3. **Separate process, separate port, separate accounts.** The streaming server never shares a port, token or password
   with the review screen; the review screen stays on `127.0.0.1`.
4. **Off until the owner switches it on**, from a **Listen on your phone** screen. It listens on the home network only
   when on. It is never opened to the internet by Music Steward.
5. **Always on, not tied to the window.** Once switched on it runs as a login item (Mac) or service (Windows), and
   `music-steward serve` runs it headless on an always-on machine. The Mac or PC must stay awake while serving.
6. **Rescan after every finished Apply, Resume, Undo, Retire and Restore** (Subsonic `startScan`), so the phone
   shows what the copy folder holds.
7. **Away from home through a private network (Tailscale, or the owner's own VPN), not a forwarded port.** First as a
   written guide; setting it up for the user is a later, separate decision.
8. **Screen content:** status (running, address, last scan, number of albums), a QR code and the address for the phone,
   the account (strong password required), a suggested free app, and a test ("reachable from this network").
9. **Mac first.** The design is the same on Windows; service setup follows.
10. **Cellular data:** the guide recommends a lower-bitrate stream on mobile data and offline downloads on Wi-Fi.

## Considered and rejected

* **Our own Subsonic server first.** A product of its own (authentication, a network-facing port, transcoding,
  compatibility with dozens of clients). Deferred until a gap is proven.
* **Opening the review screen to the network.** Breaks the privacy rule in AGENTS.md.
* **Port forwarding / a public address.** Exposes the owner's music server to the internet.
* **A browser app (PWA) as the player.** Needs HTTPS and iOS background audio is unreliable ([PHONE_APP.md](../PHONE_APP.md));
  only worth a spike if existing apps fall short.

## Open until the trial

* Does Amperfy (and one other client) show our library correctly: Chinese sorting and search, cue albums, lyrics?
* Does remote listening over Tailscale work with them, and how much mobile data does it use?
* Is anything we could show (verified, needs review) missed enough to justify an own player or server?

## Consequences

* New work items, in order: the trial; the *Listen* screen (status, QR code, account) with start/stop and rescan after
  Apply; login item / `serve`; the remote-access guide; Windows.
* Glossary: **Streaming server**, **Listen**, **Remote listening**.
* AGENTS.md: the streaming server never serves anything but organized copies; the review screen's binding is unchanged.
