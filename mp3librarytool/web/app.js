// MP3LibraryTool UI — read-only inventory browser (M1/M2 groundwork).
// All DOM is built with textContent; nothing from the collection is ever parsed as HTML.
"use strict";

const S = { rootId: null, path: "", filter: null, view: "folders", files: [], selected: null,
            expanded: new Set([""]), summary: null, scanTimer: null };

const FILTER_LABELS = {
  garbled_tags: "Text decoding issues", missing_metadata: "Missing metadata",
  unreadable: "Unreadable", estimated_values: "Estimated duration / bitrate",
  exact_duplicates: "Exact duplicates", same_audio: "Same audio, different tags",
  cue_sheets: "Cue sheets", orphan_lrc: "Lyrics without a track",
  dirs_named_like_file: "Folders named like files", unlistable_dirs: "Folders not readable",
  removed: "Removed since last scan",
};
// filters that select audio files (others are informational lists)
const FILE_FILTERS = new Set(["garbled_tags", "missing_metadata", "unreadable", "estimated_values",
                              "exact_duplicates", "same_audio"]);

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false)
    el.append(k instanceof Node ? k : String(k));
  return el;
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}
const fmtSize = b => b == null ? "" : b > 1e9 ? (b / 1e9).toFixed(1) + " GB" : b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.round(b / 1e3) + " KB";
const fmtDur = s => s == null ? "" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
function fmtAudio(f) {
  const c = (f.codec || f.ext || "").toUpperCase().replace("PCM_S16LE", "WAV");
  if (f.unreadable) return "unreadable";
  const lossless = ["FLAC", "APE", "ALAC", "WAV"].includes(c) || (f.codec || "").startsWith("pcm");
  if (lossless) return `${c} · ${f.bits ? f.bits + "-bit / " : ""}${f.sample_rate ? f.sample_rate / 1000 + " kHz" : ""}`;
  const br = f.bit_rate ? Math.round(f.bit_rate / 1000) + " kbps" : "";
  return `${c} · ${br}${f.bit_rate_estimated ? " (estimated)" : ""}`;
}
function status(f) {
  if (f.unreadable) return h("span", { class: "badge b-conflict" }, "Unreadable");
  if (f.garbled) return h("span", { class: "badge b-review" }, "Text decoding issue");
  if (!f.title || !f.artist || !f.album) return h("span", { class: "badge b-unid" }, "Missing metadata");
  return h("span", { class: "badge b-ready" }, "Tags OK");
}
const shown = (f, k) => f.fixed && f.fixed[k] ? f.fixed[k] : f[k];

// ---------------------------------------------------------------- state
async function loadState() {
  const st = await api("/api/state" + (S.rootId ? `?root_id=${S.rootId}` : ""));
  S.rootId = st.root_id;
  S.summary = st.summary;
  const root = st.roots.find(r => r.root_id === S.rootId) || st.roots[0];
  S.root = root;
  document.getElementById("rootChip").textContent =
    root ? `Root: ${root.path}${root.available ? "" : " (not available)"} · sources read-only` : "No root configured";
  renderInventory(st);
  renderFilters();
  renderScan(st.scan);
}
function renderInventory(st) {
  const s = S.summary;
  const inv = document.getElementById("invCount"), note = document.getElementById("invNote");
  if (!s) { inv.textContent = ""; note.textContent = "Not scanned yet. Click Scan."; return; }
  inv.textContent = `${(s.audio || 0).toLocaleString()} audio files`;
  const pct = s.audio ? Math.round(100 * (s.hashed || 0) / s.audio) : 0;
  document.getElementById("hashBar").style.width = pct + "%";
  const last = S.root && S.root.last_scan;
  note.textContent = `${fmtSize(s.bytes)} · ${pct}% hashed` +
    (last ? ` · scanned ${new Date(last.started_at * 1000).toLocaleString()}` : "");
}
function renderFilters() {
  const box = document.getElementById("filters");
  box.replaceChildren();
  const counts = (S.summary && S.summary.filters) || {};
  const all = h("button", { type: "button", "aria-pressed": String(!S.filter), onclick: () => setFilter(null) },
                "All audio", h("span", { class: "n" }, (S.summary && S.summary.audio) || 0));
  box.append(all);
  for (const [k, label] of Object.entries(FILTER_LABELS)) {
    const n = counts[k] ?? 0;
    const unit = k === "exact_duplicates" || k === "same_audio" ? (n === 1 ? " group" : " groups") : "";
    box.append(h("button", { type: "button", "aria-pressed": String(S.filter === k), onclick: () => setFilter(k) },
                 label, h("span", { class: "n" }, n + unit)));
  }
  box.append(h("div", { class: "later xsmall", style: "padding:6px 8px" },
               "Needs review · Unidentified · Conflicts · Ready arrive with recognition (M2)."));
}
function renderScan(sc) {
  const el = document.getElementById("scanNote"), btn = document.getElementById("scanBtn");
  btn.disabled = !!(sc && sc.running) || !S.root || !S.root.available;
  if (sc && sc.running) {
    el.textContent = sc.total ? `Scanning: ${sc.done} / ${sc.total} new or changed files read` : "Scanning: listing folders…";
    clearTimeout(S.scanTimer);
    S.scanTimer = setTimeout(async () => { const st = await api("/api/state"); renderScan(st.scan);
                                           if (!st.scan.running) refreshAll(); }, 1500);
  } else if (sc && sc.result) {
    const r = sc.result;
    el.textContent = r.status === "complete"
      ? `Last scan: ${r.n_new} new, ${r.n_changed} changed, ${r.n_unchanged} unchanged, ${r.n_removed} removed`
      : `Scan ${r.status}: ${r.abort_reason || ""}`;
  } else el.textContent = "";
}
async function startScan() {
  if (!S.root) return;
  await api("/api/scan", { method: "POST", headers: { "Content-Type": "application/json", "X-MLT": "1" },
                           body: JSON.stringify({ root: S.root.path }) });
  const st = await api("/api/state");
  renderScan(st.scan);
}

// ---------------------------------------------------------------- tree
async function renderTree() {
  const box = document.getElementById("tree");
  if (!S.rootId) { box.textContent = "—"; return; }
  box.replaceChildren(await treeNode("", S.root.path.split("/").pop() || S.root.path, null));
}
async function treeNode(path, name, count) {
  const open = S.expanded.has(path);
  const btn = h("button", { type: "button", "aria-current": String(S.path === path), "aria-expanded": String(open),
                            onclick: async () => { if (S.path === path) { open ? S.expanded.delete(path) : S.expanded.add(path); }
                                                   else { S.expanded.add(path); }
                                                   S.path = path; S.filter = null; await refreshCenter(); renderFilters(); await renderTree(); } },
                h("span", { class: "tw", "aria-hidden": "true" }, open ? "▾" : "▸"), name,
                count !== null ? h("span", { class: "cnt" }, count) : null);
  const li = h("li", {}, btn);
  if (open) {
    const d = await api(`/api/folders?root_id=${S.rootId}&path=${encodeURIComponent(path)}`);
    if (d.folders.length) {
      const ul = h("ul");
      for (const f of d.folders) ul.append(await treeNode(f.path, f.name, f.audio));
      li.append(ul);
    }
  }
  return path === "" ? h("ul", {}, li) : li;
}

// ---------------------------------------------------------------- table
async function refreshCenter() {
  if (!S.rootId) return;
  const d = await api(`/api/files?root_id=${S.rootId}&path=${encodeURIComponent(S.path)}` +
                      (S.filter ? `&filter=${S.filter}` : ""));
  S.files = d.files;
  const where = S.path || (S.root && S.root.path) || "";
  const fl = S.filter ? ` · ${FILTER_LABELS[S.filter]}` : "";
  document.getElementById("tableNote").textContent =
    d.total > d.shown ? `showing first ${d.shown} of ${d.total.toLocaleString()} — pick a sub-folder to narrow` : "";
  if (S.view === "albums") renderAlbums(where, fl); else renderFiles(d.total, where, fl);
}
function renderFiles(total, where, fl) {
  document.getElementById("tableTitle").textContent = `Files · ${total.toLocaleString()} in ${where}${fl}`;
  const t = document.getElementById("table");
  const body = h("tbody");
  for (const f of S.files) {
    const tr = h("tr", { tabindex: "0", "aria-selected": String(S.selected && S.selected.id === f.id),
                         onclick: () => selectFile(f, tr), onkeydown: e => { if (e.key === "Enter") selectFile(f, tr); } },
      h("td", {}, h("div", { class: "strong" }, shown(f, "title") || f.relpath.split("/").pop()),
                  h("div", { class: "sub mono" }, f.relpath)),
      h("td", {}, shown(f, "artist") || h("span", { class: "muted" }, "—"),
                  f.fixed.artist ? h("div", { class: "sub" }, `tag reads ${f.artist}`) : null),
      h("td", {}, shown(f, "album") || h("span", { class: "muted" }, "—")),
      h("td", {}, f.track || ""),
      h("td", {}, fmtAudio(f)),
      h("td", {}, status(f)));
    body.append(tr);
  }
  t.replaceChildren(h("thead", {}, h("tr", {}, ["Title / file", "Artist", "Album", "Track", "Audio", "Status"]
                                      .map(x => h("th", { scope: "col" }, x)))), body);
}
function albumGroups() {
  const g = new Map();
  for (const f of S.files) {
    const artist = shown(f, "albumartist") || shown(f, "artist") || "";
    const album = shown(f, "album") || "";
    const key = album ? `${artist}\u0000${album}` : `\u0001${f.relpath.split("/").slice(0, -1).join("/")}`;
    if (!g.has(key)) g.set(key, { album, artist, folder: f.relpath.split("/").slice(0, -1).join("/"), files: [] });
    g.get(key).files.push(f);
  }
  return [...g.values()];
}
function renderAlbums(where, fl) {
  const groups = albumGroups();
  document.getElementById("tableTitle").textContent = `Albums · ${groups.length} groups, ${S.files.length} files in ${where}${fl}`;
  const body = h("tbody");
  for (const a of groups) {
    const codecs = [...new Set(a.files.map(f => (f.codec || f.ext).toUpperCase()))].join(", ");
    const garbled = a.files.some(f => f.garbled), unread = a.files.some(f => f.unreadable);
    const artists = new Set(a.files.map(f => shown(f, "artist")));
    const badge = unread ? h("span", { class: "badge b-conflict" }, "Unreadable file")
      : garbled ? h("span", { class: "badge b-review" }, "Text decoding issue")
      : !a.album ? h("span", { class: "badge b-unid" }, "No album tag")
      : h("span", { class: "badge b-ready" }, "Tags OK");
    const tr = h("tr", { tabindex: "0", onclick: () => selectAlbum(a, tr), onkeydown: e => { if (e.key === "Enter") selectAlbum(a, tr); } },
      h("td", {}, h("div", { class: "strong" }, a.album || "Loose tracks"), h("div", { class: "sub mono" }, (a.folder || ".") + "/")),
      h("td", {}, artists.size > 2 && !a.artist ? "Various Artists" : (a.artist || h("span", { class: "muted" }, "—"))),
      h("td", {}, a.files.length), h("td", {}, codecs), h("td", {}, badge));
    body.append(tr);
  }
  document.getElementById("table").replaceChildren(
    h("thead", {}, h("tr", {}, ["Album / folder", "Artist", "Tracks", "Audio", "Status"].map(x => h("th", { scope: "col" }, x)))), body);
}
function markSelected(tr) {
  for (const r of document.querySelectorAll("#table tbody tr")) r.setAttribute("aria-selected", "false");
  tr.setAttribute("aria-selected", "true");
}

// ---------------------------------------------------------------- details
async function selectFile(f, tr) {
  markSelected(tr);
  S.selected = f;
  const d = await api(`/api/file/${f.id}`);
  const t = d.tags || {};
  const fixes = Object.fromEntries((d.issues || []).filter(i => i.proposal).map(i => [i.field, i]));
  const title = (fixes.title && fixes.title.proposal) || t.title || d.relpath.split("/").pop();
  const artist = (fixes.artist && fixes.artist.proposal) || t.artist;
  const parts = [
    h("div", {},
      h("div", { style: "display:flex;gap:10px;align-items:center;flex-wrap:wrap" },
        h("h2", {}, artist ? `${title} — ${artist}` : title), status(f)),
      h("div", { class: "mono muted", style: "margin-top:6px;word-break:break-all" }, d.relpath),
      d.issues && d.issues.length ? h("div", { class: "banner" },
        h("strong", {}, "Text decoding issue. "),
        "Some tags were stored as legacy Chinese bytes (GBK/Big5) in fields marked Latin-1. " +
        "Below are the proposed readings. Nothing is written to the file; repairs apply only to organized copies later.") : null),
  ];
  // tags: current vs proposed
  const rows = ["title", "artist", "album", "albumartist", "track", "disc", "date", "genre"]
    .filter(k => t[k] || fixes[k]).map(k => h("tr", {},
      h("td", {}, k), h("td", { class: fixes[k] ? "mono" : "" }, t[k] || h("span", { class: "muted" }, "empty")),
      h("td", { class: fixes[k] ? "proposed" : "muted" }, fixes[k] ? `${fixes[k].proposal}` : "unchanged")));
  parts.push(h("div", {}, h("h3", {}, "Tags (" + (d.tag_format || "none") + ")"),
    rows.length ? h("table", { class: "kv" }, h("thead", {}, h("tr", {}, h("th", {}, "Field"), h("th", {}, "Current"), h("th", {}, "Proposed"))),
                    h("tbody", {}, rows))
                : h("p", { class: "muted small" }, "No tags found in this file.")));
  if (Object.keys(fixes).length) {
    const alt = Object.values(fixes).map(i => h("li", { class: "small" }, `${i.field}: ` +
      Object.entries(i.readings || {}).map(([enc, s]) => `${enc} → ${s}`).join(" · ")));
    parts.push(h("div", {}, h("h3", {}, "Other readings"), h("ul", { style: "margin:0;padding-left:18px" }, alt)));
  }
  // properties
  const P = [["Container", d.container], ["Codec", d.codec],
             ["Duration", fmtDur(d.duration) + (d.duration_estimated ? " (estimated)" : "")],
             ["Bitrate", d.bit_rate ? Math.round(d.bit_rate / 1000) + " kbps" + (d.bit_rate_estimated ? " (estimated: no Xing header)" : "") : ""],
             ["Sample rate", d.sample_rate ? d.sample_rate + " Hz" : ""], ["Bits", d.bits_per_sample], ["Channels", d.channels],
             ["Size", fmtSize(d.size)], ["Probe", d.probe_status === "error" ? "error: " + d.probe_error : d.probe_status]];
  parts.push(h("div", {}, h("h3", {}, "Technical properties"),
    h("table", { class: "kv" }, h("tbody", {}, P.filter(([, v]) => v !== null && v !== undefined && v !== "")
      .map(([k, v]) => h("tr", {}, h("td", { class: "muted" }, k), h("td", {}, String(v))))))));
  parts.push(h("div", {}, h("h3", {}, "Hashes"),
    d.file_sha256 ? h("table", { class: "kv" }, h("tbody", {},
      h("tr", {}, h("td", { class: "muted" }, "File"), h("td", { class: "mono" }, d.file_sha256.slice(0, 16) + "…")),
      h("tr", {}, h("td", { class: "muted" }, `Audio (${d.audio_hash_method})`), h("td", { class: "mono" }, d.audio_hash.slice(0, 16) + "…"))))
    : h("p", { class: "muted small" }, d.hash_error ? "Hash error: " + d.hash_error : "Not hashed yet (run `mp3librarytool hash`)."),
    d.same_audio.length ? h("p", { class: "small" }, h("span", { class: "badge b-rel" }, "Same audio"), " also at: ",
                            d.same_audio.join(", ")) : null));
  if (d.side_files.length) parts.push(h("div", {}, h("h3", {}, "Side files in this folder"),
    h("ul", { style: "margin:0;padding-left:18px" }, d.side_files.map(s =>
      h("li", { class: "small mono" }, s.relpath.split("/").pop(), s.matches_track ? h("span", { class: "muted" }, "  (goes with this track)") : null)))));
  parts.push(h("div", {}, h("h3", {}, "Preview"),
    d.playable ? h("audio", { controls: true, preload: "none", src: `/api/audio/${d.id}` })
               : h("p", { class: "muted small" }, `Preview unavailable for ${(d.codec || d.ext).toUpperCase()} in the browser.`)));
  document.getElementById("details").replaceChildren(...parts.filter(Boolean));
}
function selectAlbum(a, tr) {
  markSelected(tr);
  const garbled = a.files.filter(f => f.garbled).length;
  const rows = a.files.map(f => h("tr", { onclick: () => selectFile(f, tr) },
    h("td", { class: "mono" }, f.relpath.split("/").pop()),
    h("td", {}, shown(f, "title") || h("span", { class: "muted" }, "—")),
    h("td", {}, f.track || "")));
  document.getElementById("details").replaceChildren(
    h("div", {}, h("h2", {}, (a.album || "Loose tracks") + (a.artist ? ` — ${a.artist}` : "")),
      h("div", { class: "mono muted", style: "margin-top:6px" }, `${a.folder || "."}/ · ${a.files.length} files`),
      garbled ? h("div", { class: "banner" }, h("strong", {}, "Text decoding issue. "),
                  `${garbled} of ${a.files.length} files have legacy-encoded tags; names shown use the proposed readings.`) : null),
    h("div", {}, h("h3", {}, "Tracks"), h("table", { class: "kv" },
      h("thead", {}, h("tr", {}, h("th", {}, "File"), h("th", {}, "Title"), h("th", {}, "#"))), h("tbody", {}, rows))),
    h("p", { class: "muted small" }, "Candidate releases, track mapping and destination preview arrive with recognition (M2)."));
}

// ---------------------------------------------------------------- wiring
async function setFilter(k) {
  S.filter = k;
  renderFilters();
  if (k && !FILE_FILTERS.has(k)) return showInfoList(k);
  await refreshCenter();
}
async function showInfoList(k) {
  // informational filters (cue sheets, folders...) are listed in the details pane
  const d = await api(`/api/state?root_id=${S.rootId}`);
  document.getElementById("details").replaceChildren(h("h2", {}, FILTER_LABELS[k]),
    h("p", { class: "muted small" }, `${(d.summary.filters[k] || 0).toLocaleString()} items. Run ` ,
      h("code", {}, `mp3librarytool audit ${k}`), " for the full list."));
}
async function refreshAll() { await loadState(); await renderTree(); await refreshCenter(); }

document.querySelectorAll("[data-view]").forEach(b => b.addEventListener("click", async () => {
  S.view = b.dataset.view;
  document.querySelectorAll("[data-view]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
  await refreshCenter();
}));
document.getElementById("scanBtn").addEventListener("click", startScan);
refreshAll().catch(e => { document.getElementById("tableTitle").textContent = "Error: " + e.message; });
