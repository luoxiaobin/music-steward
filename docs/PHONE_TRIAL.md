# Phone trial — Navidrome on the Mac, Amperfy on the iPhone

A hands-on trial to learn what an existing, well-rated Subsonic client already does with our organized library, and
what is missing. Background: [PHONE_APP.md](PHONE_APP.md). Time: about 30 minutes to set up, then listening.

Nothing here changes your music: Navidrome only **reads** the folder you point it at and keeps its own data
(accounts, play counts, cover cache) in a separate folder.

## Part 1 — Navidrome on the Mac

Navidrome is already installed here (`navidrome 0.64.2`, from Homebrew). On another Mac: `brew install navidrome`.

**1. Start it** in Terminal. This serves the organized copies that Music Steward made, on your home network, and keeps
the Mac awake while it runs (stop it with `Ctrl-C`):

```bash
mkdir -p ~/.music-steward/navidrome
```

```bash
ND_MUSICFOLDER="/Users/kevinluo/Projects/MP3LibraryTool/m0/scratch/organized" ND_DATAFOLDER="$HOME/.music-steward/navidrome" ND_ADDRESS=0.0.0.0 ND_PORT=4533 ND_ENABLEINSIGHTSCOLLECTOR=false caffeinate -i navidrome
```

The folder in `ND_MUSICFOLDER` is the destination folder of your earlier runs (337 files, 40 GB). Change it to your real
destination folder when you have one. macOS may ask "Allow navidrome to accept incoming network connections?" — choose
**Allow**, or the phone cannot reach it.

**2. Create the account.** On the Mac open <http://localhost:4533>. The first visit asks you to create an admin user:
pick a name and a **strong password** (the server is open to anyone on your Wi-Fi who has it).

**3. Wait for the scan** (under a minute for this library). The web page should show about 130 albums (133 when I tried it).

**4. Find the address your phone will use.** Mac and iPhone must be on the **same Wi-Fi**. This Mac's address was
`192.168.50.134` when I looked, so the server address is `http://192.168.50.134:4533`; it can change after a restart,
so `http://Kevins-Mac-mini.local:4533` is steadier. Test it first in Safari on the iPhone: you should see the same
Navidrome page.

Do **not** put this server on the internet. Remote listening is a separate step (see PHONE_APP.md, the HTTPS spike).

## Part 2 — Amperfy on the iPhone

1. App Store → search **Amperfy Music** → Get (free, open source, 4.5 stars in the US store).
2. Open it → add a server: choose **Subsonic** (not Ampache), then
   * Server URL: `http://192.168.50.134:4533` (or the `.local` name)
   * User name and password: the ones you just created
3. Allow it to access the local network when iOS asks. Tap Connect; the library appears.
4. Settings → **Streaming**: leave transcoding **off** for MP3/FLAC; if an APE track will not play, choose a streaming
   format of MP3 or Opus for it (Navidrome converts on the fly). Note which tracks needed that.

Optional comparison, same server: **play:Sub** ($4.99) and **Substreamer** (free). Use the same checklist for each and
note the differences.

## Part 3 — What to check

Use a few albums of each kind: Chinese (Simplified), Chinese (Traditional) if you have any, a classical album, an album
that was an APE image + cue (now split into tracks), a multi-disc set, a compilation. Mark each line ✅ fine · ⚠️ works
but annoying · ❌ broken, and write one line on what you saw.

**A. The library as the phone shows it** (this is Music Steward's work being judged)

| # | Check |
|---|---|
| 1 | Every album appears **once**, with all tracks, in the right order |
| 2 | Titles, artists and years are the ones you accepted in Music Steward (Chinese characters correct, no garbled text) |
| 3 | The split cue albums show as normal albums of tracks |
| 4 | Multi-disc sets sit in one album with disc 1 / disc 2 |
| 5 | Covers appear for most albums; which are missing? |
| 6 | Compilations ("Various") and "Unknown Artist" — are they usable or a mess? |
| 7 | **Sort order of Chinese artists and albums** — by pinyin, by stroke, or random? Is it findable? |
| 8 | **Search**: type a Chinese title; type pinyin or initials (e.g. "zjl"); type Traditional for a Simplified title |
| 9 | Synced lyrics show for tracks that have a `.lrc` file (if the app supports them) |
| 10 | Genre / year / "recently added" browsing — is anything useful, or empty? |

**B. Listening**

| # | Check |
|---|---|
| 11 | Play an album start to finish with the **screen locked**; lock-screen next / previous / pause work |
| 12 | **Gapless**: a live or classical album with no pause between tracks |
| 13 | Switching apps for 10 minutes, then coming back: still playing, position kept |
| 14 | A phone call or Siri interrupts, then music resumes |
| 15 | Bluetooth / AirPods: controls work and the title shows |
| 16 | Streaming over Wi-Fi: start time of a FLAC track (seconds), seeking inside a track |
| 17 | Walk out of Wi-Fi range (or turn Wi-Fi off): what happens, how clear is the message |

**C. Offline and everyday use**

| # | Check |
|---|---|
| 18 | Download one album for offline; switch on airplane mode; it plays |
| 19 | Queue handling: add next, add to end, reorder, the queue survives closing the app |
| 20 | Playlists and favourites (stars) created on the phone show in Navidrome's web page too |
| 21 | CarPlay (if you have a car to try) |

**D. First-run experience** (what a new user of Music Steward would go through)

| # | Check |
|---|---|
| 22 | How many steps from "I installed the app" to music playing? Where did you hesitate? |
| 23 | Were any words confusing (server, Subsonic, transcoding)? |
| 24 | If you had to explain it to a family member, which step would lose them? |

## Part 4 — Away from home (mobile data)

Navidrome's home address does not work on mobile data. The safe way is a private network, not a forwarded port:

1. Install the free **Tailscale** app on the Mac and on the iPhone and sign in to the same account on both.
2. In Tailscale note the Mac's name (for example `kevins-mac-mini`) or its `100.x.x.x` address.
3. In Amperfy add a second server (or change the address) to `http://<that name or address>:4533`.
4. Turn **Wi-Fi off** on the phone and repeat checks 11, 16 and 17.
5. In Amperfy's settings look for separate streaming formats for Wi-Fi and cellular; for cellular choose MP3 or Opus
   at 128–192 kbps. Then note: how long a track takes to start, and what the cellular data counter shows after one album.

| # | Check |
|---|---|
| 25 | Play and seek over mobile data with the lock screen on |
| 26 | Seconds to start a FLAC track on cellular, with and without conversion |
| 27 | Mobile data used by one album (Settings → Cellular) |
| 28 | The Mac sleeps or Navidrome stops: what does the phone show? |
| 29 | Download an album on Wi-Fi, then play it on cellular without using data |

Keep the Mac awake (`caffeinate` is in the start command) and never forward a port on your router for this.

## What to send back

A copy of this checklist with ✅ / ⚠️ / ❌ and one line each, plus: the **three things you liked most**, the **three
that annoyed you most**, and **anything you wished the app showed that it could not** (for example "is this album
verified?", "which albums still need review?"). That list is the real input for deciding whether Music Steward needs
its own player, and what it should do differently.

## Stop everything

`Ctrl-C` in the Terminal window stops Navidrome. To remove its data: delete the folder `~/.music-steward/navidrome`
(your music is not in it).
