# Phone app — competitive analysis and prototype proposal

* **Status:** Proposal (2026-11-22). **Decided since** (owner, 2026-11-22; see [ADR 0005](adr/0005-streaming-server.md)):
  use **Navidrome** as the streaming server instead of writing one; **iPhone first**; **try existing apps first**
  ([PHONE_TRIAL.md](PHONE_TRIAL.md)) before any own player. §6 below is partly answered by this.
* **Question:** Can we get a phone player quickly, as a browser-based app, that speaks the Subsonic API and plays the
  library Music Steward has scanned, cleaned and organized — and what should it do to be worth using?

Terms are those of [GLOSSARY.md](../GLOSSARY.md). "Subsonic" includes OpenSubsonic.

## 1. What the research found

**The phone-player space is crowded.** Navidrome's own [iOS client list](https://www.navidrome.org/apps/?platform=ios)
shows **48 iOS apps** that speak Subsonic, many free and several open source (Amperfy, Substreamer, Flo, Primuse,
Minidisc, Vibrdrome and others). A generic "Subsonic player" is not a gap; building one is a lot of work for a
feature that exists many times over. The scarce thing is a **clean library**, which is what Music Steward produces.

**Features that nearly every serious client has** (counted from that list; paid and free alike):

| Feature | How common | Notes |
|---|---|---|
| Offline downloads / smart caching | almost all | the reason people pay |
| CarPlay | about half | native only; impossible in a browser |
| Gapless / crossfade | about half | gapless is a quality signal for albums |
| Synced (LRC) lyrics | about half | matters for our users: we already write `.lrc` files |
| ReplayGain, EQ | about a third | audiophile tier |
| Scrobbling (Last.fm, ListenBrainz) | common | |
| Multiple servers, local/remote address switching | common among paid | a home NAS has two addresses |
| Siri / Shortcuts, widgets, Apple Watch | a few | native only |

**What a browser app cannot do on iPhone** (from the sources below; items marked ◇ are from memory and to be checked
in the first spike):

* **HTTPS is required** for the pieces that make it an app (service worker, offline, install). A plain
  `http://192.168.x.x` address is **not** a secure context ([MDN](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts));
  `localhost` is exempt, a LAN address is not. An `https://` page also cannot call an `http://` server (mixed content).
  So the server needs a real certificate — Tailscale's HTTPS certificates, or a reverse proxy such as Caddy with a
  domain ◇. This is the biggest practical hurdle for a *home-network* PWA.
* **Background audio is fragile in home-screen web apps.** WebKit has open bugs where audio stops when a standalone web
  app leaves the foreground or the screen locks
  ([198277](https://bugs.webkit.org/show_bug.cgi?id=198277)), and where a paused session cannot be resumed
  ([243258](https://lists.webkit.org/pipermail/webkit-unassigned/2022-July/1049527.html)). Developers report working
  around it: re-registering Media Session handlers on every `playing` event, and a track that ends in the background
  closing the audio session so the next track stays paused ([write-up](https://dev.to/amsyeung/getting-my-iphone-music-app-to-keep-playing-on-the-lock-screen-91f)).
  I found no confirmation that this is fixed on iOS 26. **This decides whether a PWA can be a daily player at all.**
* **Offline storage is best-effort.** WebKit gives a home-screen web app the same quota as Safari (up to 60% of disk per
  origin) but the data can be evicted under storage pressure or after disuse; a site can ask for persistence and
  home-screen apps are favoured ([WebKit](https://webkit.org/blog/14403/updates-to-storage-policy/)). Fine for a
  queue-ahead cache, risky as "my downloaded albums".
* **No CarPlay, Siri, widgets, Watch, AirPlay-from-lock-screen polish.** Audio formats are better than expected: Safari
  plays MP3, AAC, FLAC and ALAC; **not APE** (the server must transcode, as Navidrome does).

Existing browser clients to learn from: Navidrome's own web UI (responsive, Media Session), Airdrome (a PWA for
Subsonic servers with gapless and a persistent queue), Chora (PWA + Android), Feishin (desktop-first), Mist. I could not
verify the current feature sets; look at them hands-on before copying anything.

## 2. What this means

1. **Do not build "another Subsonic player".** Build only what makes *our* library better to listen to, and let any
   Subsonic client work with it (already true: tested with Navidrome, phone verified on 2026-11-21).
2. **A browser prototype is a good tool for the question "what should the experience be?"** and a poor tool for "can this
   replace a native player?". Its fate depends on one test: lock-screen background playback of a multi-track album on a
   real iPhone, from an installed web app, over HTTPS. Run that test first.
3. **The server question is separate and smaller than it looks.** Music Steward is not a Subsonic server; Navidrome is,
   and it works today. The desktop app deliberately binds `127.0.0.1` only (AGENTS.md privacy rule), so the phone app
   **cannot** be served by it without changing that security model. A phone client should be a separate small static web
   app that talks to *a Subsonic server* — Navidrome now; a Music Steward Subsonic façade later, only if "one install,
   no Navidrome" becomes important. Since the plan already holds every title, artist, year and duration, such a façade
   would be modest (ping, getMusicFolders, getArtists/getArtist/getAlbum, getAlbumList2, getSong, stream, getCoverArt,
   search3, getLyrics, scrobble) ◇ — but it is a product of its own, with auth and a LAN-facing port.

## 3. Where a Music Steward phone app could genuinely be better

Generic playback is a commodity. These are not:

| Idea | Why it is ours |
|---|---|
| **Chinese-first browsing and search**: pinyin and initials, Simplified ⇄ Traditional folding, 简/繁 display preference | our users' libraries; most clients search by raw string |
| **Cue-sheet and split-album awareness** (an APE image shown as its tracks, with the cue's titles) | we already split and verify them |
| **Review on the phone**: accept / skip / fix an album's match from the couch | listed in the feature sheet as "phone-friendly review"; this is the one thing no player has |
| **Library health on the phone**: "12 albums need attention" | comes from the collection health report |
| **Trust cues**: "organized copy, verified" badges; original vs organized | unique to the pipeline |
| **Lyrics first-class**: LRC from our files, large synced view | our files carry them |

## 4. Options

| | Option | Effort | Gets us | Risk |
|---|---|---|---|---|
| A | **No player**: a "Listen on your phone" guide and a short list of recommended clients (free ones first), verified against our output | days | users listening now; zero maintenance | none; no differentiation |
| B | **Browser prototype** ("Steward Player", PWA, Subsonic client) for the experience and for review-on-phone | 2–3 weeks to a usable prototype | learning about UX; fast feedback from the first trial users | iOS background audio and HTTPS (see spikes) |
| C | **Native iPhone app** (SwiftUI), or fork an open-source client (Amperfy is GPL-3.0) | months | the real thing: CarPlay, offline, lock screen | store terms and licences (the feature sheet already flags GPL vs App Store); big maintenance |

**Recommendation: A now, B as a gated prototype, C only if B proves the experience and the platform limits hurt.**
A costs days and helps real use immediately. B is the quickest honest answer to "what should it feel like".

## 5. Prototype plan with stop criteria

**Spike 1 (two days) — can iOS do the basics?** A static page over HTTPS, one `<audio>` element, Media Session, a real
Navidrome album (MP3 and FLAC), installed to the Home Screen, tested on a real iPhone: lock the screen, play a whole album,
use the lock-screen next/previous, background for ten minutes, take a phone call. *Pass:* the album plays through without
touching the phone. *Stop:* it does not; then B is only a design tool and we do not invest in it as a player.

**Spike 2 (two days) — HTTPS at home.** Make `https://` work for a phone on the home network (Tailscale or a reverse
proxy), write the steps a non-expert can follow. *Pass:* a non-technical user could do it in ten minutes. *Stop:* it
needs a domain name and DNS knowledge; then B targets Tailscale users only, or waits for a Music Steward Subsonic façade.

**Prototype (two weeks), only if both pass.** Scope, in order: sign in to a server and remember it (with a second
address for away from home); browse artists, albums, years; Chinese-aware search; album page with cover and tracks
(cue albums as tracks); queue, shuffle, repeat; Media Session; synced lyrics; a queue-ahead cache. Out of scope: offline
libraries, EQ, scrobbling, podcasts, radio.

**Review-on-phone** is a separate, later piece (it needs the desktop app reachable from the phone — the security model
change in §2.3 — so it is a decision of its own).

## 6. Decisions for the owner

1. **Who is the phone app for?** You and the first trial users on iPhone only, or Android too (a PWA works better on
   Android; native Android clients are plentiful)?
2. **Is "runs alone" really "no Navidrome"?** If yes, the Subsonic façade in Music Steward becomes part of the plan, with
   the LAN-binding and authentication design that implies. If "a Subsonic server somewhere" is enough, we skip it.
3. **Go for Option A now?** (Recommended; a small guide plus verified client list.)
4. **Is Spike 1 worth two days now,** before more lifecycle or collection-folder work? (Recommended: yes — it is the
   cheapest way to find out whether a browser player can exist at all.)
5. **Name and look:** reuse the music-folder icon for a player, or the "subtle variant" the plan already mentions.

## Sources

* Navidrome, [Apps & Players: iOS](https://www.navidrome.org/apps/?platform=ios) (the 48-client list)
* MDN, [Secure contexts](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts)
* WebKit, [Updates to storage policy](https://webkit.org/blog/14403/updates-to-storage-policy/)
* WebKit bugs [198277](https://bugs.webkit.org/show_bug.cgi?id=198277) and
  [243258](https://lists.webkit.org/pipermail/webkit-unassigned/2022-July/1049527.html); Apple Developer Forums,
  [iOS audio lockscreen problem in PWA](https://developer.apple.com/forums/thread/762582)
* Developer write-ups: [keeping audio alive on the lock screen](https://dev.to/amsyeung/getting-my-iphone-music-app-to-keep-playing-on-the-lock-screen-91f),
  [Media Session and iOS web apps](https://dbushell.com/2023/03/20/ios-pwa-media-session-api/) (could not be fetched;
  cited from search results only)
