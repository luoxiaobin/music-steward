// Music Steward UI — read-only inventory browser (M1/M2 groundwork).
// All DOM is built with textContent; nothing from the collection is ever parsed as HTML.
"use strict";

const S = { rootId: null, path: "", filter: null, view: "folders", files: [], selected: null,
            expanded: new Set([""]), summary: null, scanTimer: null };

const FILTER_LABELS = {
  garbled_tags: "Text decoding issues", missing_metadata: "Missing metadata",
  unreadable: "Unreadable", wrong_extension: "Wrong extension", estimated_values: "Estimated duration / bitrate",
  exact_duplicates: "Exact duplicates", same_audio: "Same audio, different tags",
  cue_sheets: "Cue sheets", orphan_lrc: "Lyrics without a track",
  dirs_named_like_file: "Folders named like files", unlistable_dirs: "Folders not readable",
  removed: "Removed since last scan",
};
// filters that select audio files (others are informational lists)
const FILE_FILTERS = new Set(["garbled_tags", "missing_metadata", "unreadable", "wrong_extension", "estimated_values",
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
  const chip = document.getElementById("rootChip");
  const pickable = st.roots.filter(r => r.root_id);
  if (pickable.length > 1) {          // more than one collection folder: a switcher (ADR 0004 §14); one at a time
    const sel = h("select", { id: "rootPick", "aria-label": "Collection folder", "data-no-translate": "",
      onchange: e => { S.rootId = Number(e.target.value); S.path = ""; S.expanded = new Set([""]); S.filter = null; refreshAll(); } },
      pickable.map(r => h("option", { value: r.root_id, selected: r.root_id === S.rootId ? true : null },
        r.path + (r.available ? "" : "  ⚠"))));
    chip.classList.add("pick");
    chip.replaceChildren(h("span", {}, "Collection folder:"), sel, root && !root.available ? h("span", {}, "(not connected)") : "");
  } else (chip.classList.remove("pick"), chip.textContent =
    root ? `Collection folder: ${root.path}${root.available ? "" : " (not connected)"} · sources read-only` : "No collection folder yet");
  renderConn(root, st);
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
  const busy = !!(sc && sc.running) || !S.root || !S.root.available;
  for (const b of [btn, document.getElementById("rescanFolderBtn"), document.getElementById("hashFolderBtn"),
                   document.getElementById("recognizeBtn")]) b.disabled = busy;
  if (sc && sc.running) {
    const where = sc.path ? `“${sc.path}”` : "whole root";
    el.textContent = sc.kind === "recognize_unit" ? "Searching again…"
      : sc.kind === "recognize"
      ? (sc.total ? `Recognizing ${where}: ${sc.done} / ${sc.total} albums & tracks` : `Recognizing ${where}: planning…`)
      : sc.kind === "hash"
      ? (sc.total ? `Hashing ${where}: ${sc.done} / ${sc.total} files` : `Hashing ${where}: starting…`)
      : (sc.total ? `Scanning ${where}: ${sc.done} / ${sc.total} new or changed files read` : `Scanning ${where}: listing folders…`);
    if (sc.kind === "recognize") el.append(" ", h("button", { type: "button", class: "btn", style: "min-height:24px;padding:0 8px",
      onclick: async e => { e.target.disabled = true; e.target.textContent = "Stopping after this album…";
                            const r = await post2("/api/stop", {}); if (r.error) tell(r.error); } }, "Stop"));
    clearTimeout(S.scanTimer);
    // poll the job alone: the full state recomputes the inventory, seconds on a big collection, every 1.5 s
    S.scanTimer = setTimeout(async () => { const st = await api("/api/job"); renderScan(st.scan);
                                           if (!st.scan.running) refreshAll(); }, 1500);
  } else if (sc && sc.result) {
    // "Scan of “X”", "Recognition of “X” stopped" -- but "Recognized “X”", not "Recognized of “X”" (owner, 0.4.0)
    const r = sc.result, where = sc.path ? ` of “${sc.path}”` : "", named = sc.path ? ` “${sc.path}”` : "";
    // name the job that stopped: "Scan stopped" after a stopped recognition was misleading (seen on the owner's Mac)
    const JOB = { hash: "Hash", recognize: "Recognition", recognize_unit: "Search again", scan: "Scan" };
    el.textContent = r.status !== "complete" ? `${JOB[sc.kind] || "Scan"}${where} ${r.status}: ${r.abort_reason || ""}`
      : sc.kind === "recognize_unit" ? `Searched again: ${r.done} done, ${r.errors} errors in ${r.seconds}s`
      : sc.kind === "recognize" ? `Recognized${named}: ${r.done} done, ${r.errors} errors in ${r.seconds}s`
      : sc.kind === "hash" ? `Hashed${named}: ${r.hashed} files (${fmtSize(r.bytes)}) in ${r.seconds}s, ${r.errors} errors`
      : `Scan${where}: ${r.n_new} new, ${r.n_changed} changed, ${r.n_unchanged} unchanged, ${r.n_removed} removed`;
  } else el.textContent = "";
}
async function startJob(kind, path) {
  if (!S.root) return;
  const r = await api(`/api/${kind}`, { method: "POST", headers: { "Content-Type": "application/json", "X-MLT": "1" },
                                        body: JSON.stringify({ root: S.root.path, path: path || "" }) });
  if (r.error) { document.getElementById("scanNote").textContent = r.error; return; }
  const st = await api("/api/job");
  renderScan(st.scan);
}
const startScan = () => startJob("scan", "");

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
                                                   S.path = path; S.filter = null; clearListFilter(); renderFilters();
                                                   // the tree answers the click at once; the list follows when it is loaded
                                                   await renderTree(); await refreshCenter(); } },
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
  // each click starts a new load; an answer that arrives after a newer click is dropped, so a slow folder can never
  // overwrite the one you moved on to (and the window keeps reacting while it loads)
  const mine = S.centerSeq = (S.centerSeq || 0) + 1;
  const stale = () => mine !== S.centerSeq;
  document.getElementById("tableNote").replaceChildren(h("span", { class: "muted" }, "Loading…"));
  document.querySelectorAll(".selbar").forEach(el => el.remove());   // album selection bar: Albums view only
  const d = await api(`/api/files?root_id=${S.rootId}&path=${encodeURIComponent(S.path)}` +
                      (S.filter ? `&filter=${S.filter}` : "") + (S.listQ ? `&q=${encodeURIComponent(S.listQ)}` : ""));
  if (stale()) return;
  S.files = d.files;
  const fs = await api(`/api/folders?root_id=${S.rootId}&path=${encodeURIComponent(S.path)}`);
  if (stale()) return;
  document.getElementById("folderStats").textContent =
    `${fs.audio.toLocaleString()} audio · ${fs.audio ? Math.round(100 * fs.hashed / fs.audio) : 0}% hashed`;
  document.getElementById("hashFolderBtn").textContent = fs.hashed >= fs.audio && fs.audio ? "Hashed ✓" : "Hash folder";
  const where = S.path || (S.root && S.root.path) || "";
  const fl = S.filter ? ` · ${FILTER_LABELS[S.filter]}` : "";
  document.getElementById("tableNote").textContent =
    d.total > d.shown ? `showing first ${d.shown} of ${d.total.toLocaleString()} — pick a sub-folder to narrow` : "";
  if (S.view === "albums") { if (S.filter || !(await renderUnits(where, stale))) { if (!stale()) renderAlbums(where, fl); } }
  else renderFiles(d.total, where, fl);
}
function renderFiles(total, where, fl) {
  document.getElementById("tableTitle").textContent = `Files · ${total.toLocaleString()} in ${where}${fl}`;
  const t = document.getElementById("table");
  const body = h("tbody");
  for (const f of S.files) {
    const tr = h("tr", { tabindex: "0", "data-file": f.id, "aria-selected": String(S.selected && S.selected.id === f.id),
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
  applyColumns("files");
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
const CONF = {
  ready: ["b-ready", "Ready"], likely: ["b-rel", "Likely"], needs_review: ["b-review", "Needs review"],
  weak: ["b-unid", "Weak: title agrees"], no_match: ["b-unid", "No real match"],
  queued: ["b-rel", "Queued"], running: ["b-rel", "Recognizing…"], error: ["b-conflict", "Error"],
};
function unitBadge(u) {
  if (u.presence === "retired") return h("span", { class: "badge b-ready" }, "Originals set aside");
  if (u.presence === "not_found") return h("span", { class: "badge b-conflict" }, "Not found");
  if (u.decision) return u.decision.action === "accept"
    ? h("span", { class: "badge b-ready" }, u.decision.release_key === "local" || u.decision.source === "Local" ? "Accepted · own info"
        : u.decision.source ? `Accepted · ${u.decision.source}` : "Accepted")
                                                          : h("span", { class: "badge b-unid" }, "Skipped");
  if (u.evidence && u.evidence.different_album)      // its top match is another album: Own info is the way
    return h("span", { class: "badge b-conflict" }, "Different album");
  const [cls, txt] = CONF[u.confidence] || ["b-unid", u.confidence];
  return h("span", { class: `badge ${cls}` }, txt);
}
async function renderUnits(where, stale = () => false) {
  const us = await api(`/api/units?root_id=${S.rootId}&path=${encodeURIComponent(S.path)}` +
                       (S.listQ ? `&q=${encodeURIComponent(S.listQ)}` : ""));
  if (stale()) return true;                 // you have moved on: this folder's list is no longer wanted
  if (!us.length && S.listQ) {
    document.getElementById("tableTitle").textContent = `Albums · none match “${S.listQ}” in ${where}`;
    document.getElementById("table").replaceChildren();
    return true;
  }
  if (!us.length) return false;
  const counts = {};
  for (const u of us) { const k = u.decision ? u.decision.action : u.confidence; counts[k] = (counts[k] || 0) + 1; }
  document.getElementById("tableTitle").textContent = S.listQ ? `Albums · ${us.length} matching “${S.listQ}” in ${where}`
                                                                : `Albums · ${us.length} recognized units in ${where}`;
  const note = document.getElementById("tableNote");
  note.replaceChildren(Object.entries(counts).map(([k, n]) => `${n} ${k.replace("_", " ")}`).join(" · "));
  const nLikely = us.filter(u => !u.decision && u.confidence === "likely" && u.presence === "here").length;
  const nReady = us.filter(u => !u.decision && u.confidence === "ready" && u.presence === "here").length;
  if (nLikely + nReady) note.append(" ", h("button", { type: "button", class: "btn", style: "min-height:28px;padding:0 10px;margin-left:6px",
    onclick: async () => {
      if (!await ask(`Accept the top candidate for ${nReady} Ready and ${nLikely} Likely albums in this folder? ` +
                   "Nothing is copied until you apply a plan; each decision can be undone.")) return;
      const r = await post2("/api/bulk_accept", { root: S.root.path, path: S.path, confidences: ["ready", "likely"],
                                                   only_ids: S.listQ ? us.map(u => u.id) : null });
      if (r.error) tell(r.error);
      await refreshCenter();
    } }, `Accept all Ready + Likely (${nReady + nLikely})`));
  // ---- folder-wide actions on undecided albums ----
  const und = us.filter(u => !u.decision && u.status === "done" && u.presence === "here");   // as the server counts
  const nNoLocal = und.filter(u => ["no_match", "weak"].includes(u.confidence) && !u.local).length;
  const nLocal = und.filter(u => u.local).length;
  const nSkip = und.filter(u => ["no_match", "weak"].includes(u.confidence)).length;
  const bulkBtn = (label, n, question, body) => n ? h("button", { type: "button", class: "btn",
    style: "min-height:28px;padding:0 10px;margin-left:6px", onclick: async () => {
      if (question && !await ask(question)) return;
      const r = await post2("/api/bulk", { root: S.root.path, path: S.path, ...body,
                                           only_ids: S.listQ ? us.map(u => u.id) : null });
      if (r.error) tell(r.error);
      else if (r.n_failed) tell(`${r.done} done; ${r.n_failed} could not be read:\n` + r.failed.join("\n"));
      await refreshCenter();
    } }, `${label} (${n})`) : null;
  note.append(...[
    bulkBtn("Add own info to No match + Weak", nNoLocal, null, { action: "local" }),
    bulkBtn("Accept own info", nLocal,
      `Accept the album's own info (cue sheet / tags / names) for ${nLocal} albums? Check the "Own info" lines in the table first. ` +
      "Nothing is copied until you apply a plan; each decision can be undone, and fields can still be edited.", { action: "accept_local" }),
    bulkBtn("Skip No match + Weak", nSkip,
      `Skip ${nSkip} undecided No match / Weak albums in this folder? They stay where they are and are left out of plans. Each skip can be undone.`,
      { action: "skip", confidences: ["no_match", "weak"] })].filter(Boolean));
  // ---- albums whose files are gone: the owner may remove their records (never automatic) ----
  const gone = us.filter(u => u.presence === "not_found");
  if (gone.length) note.append(" ", h("button", { type: "button", class: "btn", style: "min-height:28px;padding:0 10px;margin-left:6px",
    onclick: () => removeNotFound(gone.map(u => u.id)) }, `Remove not-found albums (${gone.length})…`));
  // ---- selection: filter, tick, act on exactly the ticked albums ----
  S.unitSel = new Set([...(S.unitSel || [])].filter(id => us.some(u => u.id === id)));
  const statusOf = u => u.decision ? u.decision.action : u.confidence;
  const FILTERS = [["all", "All"], ["undecided", "Undecided"], ["likely", "Likely"], ["needs_review", "Needs review"],
                   ["weak", "Weak"], ["no_match", "No match"], ["accept", "Accepted"], ["skip", "Skipped"],
                   // the groups the Overview counts (and links to)
                   ["ready_likely", "Ready + Likely"], ["no_match_weak", "No match + Weak"], ["not_found", "Not found"],
                   // the thin slice of docs/TRIAL_E2E.md (music-steward trial pick)
                   ...(us.some(u => u.trial) ? [["trial", "Trial set"]] : [])];
  const f = S.unitFilter || "all";
  const undecided = u => !u.decision && u.presence === "here" && u.status === "done";
  // one definition for both the list and the count beside each filter's name
  const matches = (u, v) => v === "all" || (v === "undecided" ? !u.decision
    : v === "not_found" ? u.presence === "not_found"
    : v === "trial" ? !!u.trial
    : v === "ready_likely" ? undecided(u) && ["ready", "likely"].includes(u.confidence)
    : v === "no_match_weak" ? undecided(u) && ["no_match", "weak"].includes(u.confidence)
    : statusOf(u) === v);
  const match = u => matches(u, f);
  const shown = us.filter(u => match(u) &&
                               (!S.titleOnly || (u.evidence && u.evidence.title_agrees)));
  S.shownIds = shown.map(u => u.id);
  S.unitsById = Object.fromEntries(us.map(u => [u.id, u]));
  const titleDiffers = u => !!(u.top && u.evidence && !u.evidence.title_agrees);
  const selBar = h("div", { class: "selbar" });
  const drawBar = () => {
    const n = S.unitSel.size;
    const act = (label, action, question) => h("button", { type: "button", class: "btn", disabled: !n, onclick: async () => {
      if (question && !await ask(question.replace("{n}", n))) return;
      const r = await post2("/api/bulk", { root: S.root.path, path: S.path, action, unit_ids: [...S.unitSel] });
      if (r.error) return tell(r.error);
      if (r.n_failed) tell(`${r.done} done; ${r.n_failed} not done:\n` + r.failed.join("\n"));
      S.unitSel.clear();
      await refreshCenter();
    } }, label);
    selBar.replaceChildren(
      h("label", { class: "small" }, "Show ", h("select", { onchange: e => { S.unitFilter = e.target.value; S.unitSel.clear(); refreshCenter(); } },
        FILTERS.map(([v, l]) => h("option", { value: v, selected: v === f ? true : null },
          `${l} (${us.filter(u => matches(u, v)).length})`)))),
      h("label", { class: "small" }, h("input", { type: "checkbox", checked: S.titleOnly ? true : null,
        onchange: e => { S.titleOnly = e.target.checked; S.unitSel.clear(); refreshCenter(); } }), " only where the album title agrees"),
      h("span", { class: "small strong" }, `${n} selected`),
      h("button", { type: "button", class: "btn", disabled: !n, onclick: async () => {
        const ids = [...S.unitSel], bad = ids.filter(id => titleDiffers(S.unitsById[id] || {}));
        let use = ids;
        if (bad.length) {
          const ex = S.unitsById[bad[0]];
          const pick = await _dialog(`${bad.length} of the ${ids.length} selected albums have a DIFFERENT album title on ` +
            `their top match — usually a different album by the same artist ` +
            `(e.g. “${ex.label}” → “${ex.top.album}”).\n\nAccept only the ${ids.length - bad.length} whose title agrees?`,
            [["Cancel", null], [`Accept all ${ids.length}`, "all"], [`Accept ${ids.length - bad.length} that agree`, "agree", true]]);
          if (!pick) return;
          if (pick === "agree") use = ids.filter(id => !bad.includes(id));
        } else if (!await ask(`Accept the best online candidate for ${ids.length} albums?`)) return;
        if (!use.length) return;
        const r = await post2("/api/bulk", { root: S.root.path, path: S.path, action: "accept_top", unit_ids: use });
        if (r.error) return tell(r.error);
        if (r.n_failed) tell(`${r.done} done; ${r.n_failed} not done:\n` + r.failed.join("\n"));
        S.unitSel.clear();
        await refreshCenter();
      } }, "Accept top match"),
      act("Use own info", "own_info", "Organize {n} albums from their own info (cue sheet / tags / names)?"),
      act("Skip", "skip", "Skip {n} albums? They are left out of plans."),
      act("Clear decision", "clear", null));
  };
  drawBar();
  const boxes = [];
  const all = h("input", { type: "checkbox", "aria-label": "Select all shown",
    checked: shown.length && shown.every(u => S.unitSel.has(u.id)) ? true : null,
    onchange: e => { for (const u of shown) e.target.checked ? S.unitSel.add(u.id) : S.unitSel.delete(u.id);
                     boxes.forEach(b => { b.checked = e.target.checked; }); drawBar(); } });
  const body = h("tbody");
  // pages of rows: a folder of thousands of albums must not freeze the window while thousands of rows are built
  const PAGE = 500;
  let drawn = 0;
  const more = h("tr", { class: "morerow" }, h("td", { colspan: "5" }));
  const drawMore = () => {
    for (const u of shown.slice(drawn, drawn + PAGE)) body.insertBefore(unitRow(u), more);
    drawn = Math.min(shown.length, drawn + PAGE);
    more.hidden = drawn >= shown.length;
    more.firstChild.replaceChildren(h("button", { type: "button", class: "btn", onclick: drawMore },
      `Show ${Math.min(PAGE, shown.length - drawn)} more`), h("span", { class: "muted small" }, `  ${drawn} of ${shown.length} shown`));
  };
  S.drawMoreUnits = id => { while (drawn < shown.length && !body.querySelector(`tr[data-unit="${id}"]`)) drawMore(); };
  function unitRow(u) {
    const t = u.top;
    const box = h("input", { type: "checkbox", "aria-label": `Select ${u.label}`, checked: S.unitSel.has(u.id) ? true : null,
      onclick: e => e.stopPropagation(),
      onchange: e => { e.target.checked ? S.unitSel.add(u.id) : S.unitSel.delete(u.id); drawBar(); } });
    boxes.push(box);
    const tr = h("tr", { tabindex: "0", "data-unit": u.id, "aria-selected": String(S.selectedUnit === u.id),
                         onclick: () => selectUnit(u.id, tr), onkeydown: e => { if (e.key === "Enter") selectUnit(u.id, tr); } },
      h("td", { class: "selcol", onclick: e => e.stopPropagation() }, box),
      h("td", {}, h("div", { class: "strong", "data-no-translate": "" }, u.label), h("div", { class: "sub mono" }, (u.folder || ".") + "/"),
        u.decision && u.decision.stale_reason ? h("div", { class: "warn small strong" }, "Audio changed · decide again") : null),
      h("td", {}, u.kind === "track" ? "track" : u.kind === "cue" ? `${u.n_tracks || "?"} (cue)` : u.n_files),
      h("td", {}, t ? [h("div", {}, `${t.album || t.artist} · ${yearText(t)}${t.country ? " · " + t.country : ""}`,
                         u.evidence && u.evidence.different_album ? h("span", { class: "err small strong" }, "  different album")
                           : u.evidence && u.evidence.title_agrees ? h("span", { class: "ok small" }, "  title ✓")
                           : titleDiffers(u) ? h("span", { class: "warn small strong" }, "  title differs") : null),
                       h("div", { class: "sub" }, `${t.artist} · distance ${t.distance.toFixed(2)} · fingerprints ${t.fingerprint_agrees}/${u.n_tracks || u.n_files}` +
                         (u.evidence && u.evidence.disc_of_set ? ` · disc ${u.evidence.disc} of ${t.mediums}` : ""))]
                    : h("span", { class: "muted" }, u.status === "error" ? "error" : u.status === "done" ? "no candidates" : "—"),
                       u.local ? h("div", { class: "sub" }, h("strong", {}, "Own info: "),
                         `${u.local.album || "?"} · ${u.local.artist || "Unknown Artist"}${u.local.year ? " · " + u.local.year : ""}`) : null),
      h("td", {}, unitBadge(u)));
    return tr;
  }
  body.append(more);
  drawMore();
  note.after(selBar);
  selBar.append(h("span", { class: "muted xsmall" }, "Keys: ↓/↑ move · A accept · O own info · S skip · U undo · X tick"));
  document.querySelectorAll(".selbar").forEach(el => { if (el !== selBar) el.remove(); });
  document.getElementById("table").replaceChildren(
    h("thead", {}, h("tr", {}, h("th", { scope: "col", class: "selcol" }, all),
      ...["Album / folder", "Tracks", "Best candidate", "Status"].map(x => h("th", { scope: "col" }, x)))), body);
  applyColumns("albums");
  return true;
}
function nextUnitId(id, step = 1) {
  const ids = S.shownIds || [];
  const i = ids.indexOf(id);
  if (i < 0) return ids[0] ?? null;
  return ids[Math.min(ids.length - 1, Math.max(0, i + step))] ?? null;
}
async function gotoUnit(id) {
  if (id == null) return;
  if (S.drawMoreUnits) S.drawMoreUnits(id);          // moving with ↓ past the rows drawn so far draws the next page
  const tr = document.querySelector(`#table tbody tr[data-unit="${id}"]`);
  if (tr) tr.scrollIntoView({ block: "nearest" });
  await selectUnit(id, tr);
}
document.addEventListener("keydown", e => {
  if (S.view !== "albums" || e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target;
  if (t && (t.closest("input, textarea, select, dialog") || t.isContentEditable)) return;
  if (document.querySelector("dialog[open]")) return;
  const k = e.key.toLowerCase(), A = S.detailActions || {};
  const cur = S.selectedUnit;
  if (k === "arrowdown" || k === "j") { e.preventDefault(); gotoUnit(cur == null ? (S.shownIds || [])[0] : nextUnitId(cur, 1)); }
  else if (k === "arrowup" || k === "k") { e.preventDefault(); gotoUnit(nextUnitId(cur, -1)); }
  else if (cur == null) return;
  else if (k === "a") A.accept && A.accept();
  else if (k === "o") A.own && A.own();
  else if (k === "s") A.skip && A.skip();
  else if (k === "u") A.undo && A.undo();
  else if (k === "x") {
    const box = document.querySelector(`#table tbody tr[data-unit="${cur}"] input[type=checkbox]`);
    if (box) { box.checked = !box.checked; box.dispatchEvent(new Event("change")); }
  }
});
async function selectUnit(id, tr) {
  if (tr) markSelected(tr);
  S.selectedUnit = id;
  if (S.allCands !== id) S.allCands = null;
  const d = await api(`/api/unit/${id}`);
  const sel = d.decision && d.decision.candidate_id || (d.candidates[0] && d.candidates[0].id);
  S.unitChoice = sel;
  renderUnitDetail(d);
}
const effYear = c => (c.original_year && (!c.year || c.original_year < c.year)) ? c.original_year : c.year;
const yearText = c => effYear(c) && effYear(c) !== c.year ? `${effYear(c)} (this release ${c.year})` : (c.year || "?");
const online = cands => cands.filter(c => c.source !== "Local");
function renderUnitDetail(d) {
  const cands = d.candidates;
  const chosen = cands.find(c => c.id === S.unitChoice) || cands[0];
  const files = Object.fromEntries(d.files.map(f => [f.id, f]));
  const list = h("fieldset", { style: "border:0;margin:0;padding:0" }, h("legend", { class: "strong", style: "margin-bottom:8px" },
    cands.length ? `Candidate releases (${cands.length})` : "No candidates"));
  const nT = d.n_tracks || d.n_files;
  const onl = cands.filter(c => c.source !== "Local");
  const nOnl = S.allCands === d.id ? onl.length : Math.min(3, onl.length);
  const shown = onl.slice(0, nOnl).concat(onl.slice(nOnl).filter(c => c.id === chosen?.id))
                   .concat(cands.filter(c => c.source === "Local"));
  const ORIGIN = o => Object.entries(o || {}).filter(([, v]) => v).map(([k, v]) => `${k} from ${v}`).join(" · ");
  for (const c of shown) {
    const fpShare = nT ? c.fingerprint_agrees / nT : 0;
    if (c.source === "Local") {
      list.append(h("label", { class: "cand" + (c.id === chosen?.id ? " sel" : "") },
        h("input", { type: "radio", name: "cand", checked: c.id === chosen?.id,
                     onchange: () => { S.unitChoice = c.id; renderUnitDetail(d); } }),
        h("span", { style: "flex:1" },
          h("span", { class: "strong" }, `${c.album || c.tracks[0]?.title || "?"} · ${c.year || "?"}`),
          h("br"), h("span", { class: "ev" }, `${c.artist || "Unknown Artist"}`), h("br"),
          h("span", { class: "ev" }, "Your album's own info (not an online release): ", ORIGIN(c.origin)))));
      continue;
    }
    list.append(h("label", { class: "cand" + (c.id === chosen?.id ? " sel" : "") },
      h("input", { type: "radio", name: "cand", checked: c.id === chosen?.id,
                   onchange: () => { S.unitChoice = c.id; renderUnitDetail(d); } }),
      h("span", { style: "flex:1" },
        h("span", { class: "strong" }, `${c.album || c.tracks[0]?.title || "?"} · ${yearText(c)}${c.country ? " · " + c.country : ""}` +
          (c.media ? ` · ${c.mediums > 1 ? c.mediums + "×" : ""}${c.media}` : "")),
        h("br"), h("span", { class: "ev" }, `${c.artist} · ${c.label || ""}`), h("br"),
        h("span", { class: "ev" }, `distance ${c.distance.toFixed(2)} · `,
          h("span", { class: fpShare >= 0.6 ? "ok" : "warn" }, `fingerprints agree ${c.fingerprint_agrees}/${nT}`),
          c.evidence && c.evidence.disc_of_set ? ` · your files are disc ${c.evidence.disc} of ${c.mediums}` :
          c.extra_tracks.length ? ` · ${c.extra_tracks.length} release tracks missing locally` : "",
          c.extra_items.length ? ` · ${c.extra_items.length} local files not on this release` : "",
          ` · ${c.source}`))));
  }
  const parts = [
    h("div", {}, h("div", { style: "display:flex;gap:10px;align-items:center;flex-wrap:wrap" }, h("h2", { "data-no-translate": "" }, d.label), unitBadge(d)),
      h("div", { class: "mono muted", style: "margin-top:6px" }, d.kind === "cue"
        ? `${d.folder || "."}/ · one image file with ${d.n_tracks || "?"} tracks (cue sheet)`
        : `${d.folder || "."}/ · ${d.n_files} file${d.n_files > 1 ? "s" : ""} · ${d.kind}`),
      d.status === "error" ? h("div", { class: "banner" }, h("strong", {}, "Recognition failed. "), (d.error || "").split("\n")[0]) : null,
      d.notice ? h("div", { class: "banner" }, h("strong", {}, "Matched again. "), d.notice) : null,
      d.decision && d.decision.stale_reason ? h("div", { class: "banner" }, h("strong", {}, "Audio changed since you decided. "),
        "This album is left out of plans until you decide again (Accept, Own info or Skip).") : null,
      d.acoustid_used === 0 ? h("div", { class: "banner" }, "Matched without fingerprints (no AcoustID key).") : null,
      d.repaired ? h("div", { class: "banner" }, h("strong", {}, "Text decoding repaired before matching. "),
        `${d.repaired} of ${d.n_files} file${d.n_files > 1 ? "s" : ""} had garbled GBK/Big5 tags; the repaired text was used to search. ` +
        "The files themselves are unchanged.") : null),
    list];
  if (chosen && chosen.tracks.length) {
    const player = h("audio", { id: "unitPlayer", controls: true, preload: "none", style: "width:100%" });
    const nowPlaying = h("div", { class: "muted small", id: "nowPlaying" }, "Press ▶ to listen to a track.");
    // the desktop window (WebKit) played nothing for a FLAC file and said nothing (owner, 2026-11-28): when a file sent
    // as it is fails, try once more with the converted MP3 preview, and always say what happened
    let current = null;
    const failed = (why, src) => {
      if (!current || src !== current.active) return;      // a late error from an earlier try or another track
      if (current.direct && current.fallback) {
        const c = current;
        current = { ...c, direct: false, active: c.fallback };
        nowPlaying.textContent = `Converting ${c.label} for playback (first time takes a few seconds)…`;
        player.src = c.fallback;
        player.play().then(() => { nowPlaying.textContent = `Playing ${c.label}`; }).catch(e => {
          if (e && e.name !== "AbortError") failed(e.name, c.fallback); });
        return;
      }
      nowPlaying.textContent = `This track could not be played (${why || "preview failed"}).`;
    };
    player.addEventListener("error", () => failed(player.error ? `media error ${player.error.code}` : "",
      current && player.currentSrc.endsWith(current.active) ? current.active : null));
    const srcFor = t => {
      if (d.kind === "cue") {
        const sl = (d.cue_slices || {})[String(t.cue_track)];
        return sl ? `/api/preview/${d.audio_file_id}?start=${sl[0]}&dur=${sl[1] || ""}` : null;
      }
      const f = files[t.file_id];
      if (!f) return null;
      const ext = (f.relpath.split(".").pop() || "").toLowerCase();
      return ["mp3", "m4a", "aac", "flac", "wav", "ogg", "opus"].includes(ext) ? `/api/audio/${f.id}` : `/api/preview/${f.id}`;
    };
    const play = (t, label) => {
      const src = srcFor(t);
      if (!src) return;
      const direct = src.startsWith("/api/audio/");
      current = { label, direct, active: src, fallback: direct ? src.replace("/api/audio/", "/api/preview/") : null };
      nowPlaying.textContent = `Preparing ${label}…`;
      player.src = src;
      const mine = current;
      player.play().then(() => { nowPlaying.textContent = `Playing ${label}`; }).catch(e => {
        if (e && e.name === "AbortError") return;           // another track was chosen meanwhile
        failed(e && e.name, src);
      });
      // a player that neither plays nor reports an error (seen in the desktop window) gets the MP3 after 8 s
      setTimeout(() => { if (current === mine && player.readyState < 3 && player.currentTime === 0) failed("no answer", src); }, 8000);
    };
    const rows = chosen.tracks.map(t => h("tr", {},
      h("td", { class: "playcol" }, srcFor(t) ? h("button", { type: "button", class: "playbtn", "aria-label": "Play",
        onclick: () => play(t, t.label || `${t.medium_index ?? t.track_index}. ${t.title}`) }, "▶") : null),
      h("td", { class: "mono" }, t.label || (files[t.file_id]?.relpath || "").split("/").pop()),
      h("td", {}, `${chosen.mediums > 1 ? t.medium + "-" : ""}${t.medium_index ?? t.track_index}. ${t.title}`),
      chosen.source === "Local" ? h("td", { class: "muted" }, "own info")
        : h("td", { class: t.track_distance > 0.3 ? "warn" : "ok" }, t.track_distance > 0.3 ? `check (${t.track_distance.toFixed(2)})` : "✓")));
    parts.push(h("div", {}, h("h3", {}, "Track mapping"), h("table", { class: "kv" },
      h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Source file"), h("th", {}, "Release track"), h("th", {}, "Check"))), h("tbody", {}, rows))));
    parts[parts.length - 1].insertBefore(h("div", { class: "playerbox" }, player, nowPlaying), parts[parts.length - 1].children[1]);
  }
  // ---- Edit fields (only for the accepted release) ----
  const accepted = d.decision && d.decision.action === "accept" && d.decision.candidate_id === chosen?.id;
  const ov = accepted && d.decision.overrides_json ? JSON.parse(d.decision.overrides_json) : {};
  if (accepted && chosen) {
    // the placeholders are the release's own values (titles, artists): data, never translated
    const inp = (id, val, ph, w) => h("input", { type: "text", id, value: val ?? "", placeholder: ph ?? "", "data-no-translate": "",
                                                 style: `font:14px var(--font);min-height:32px;padding:0 8px;border:1px solid var(--btn-line);border-radius:6px;width:${w || "100%"}` });
    // per-track artist: empty keeps the release's track artist (or the album artist), shown greyed
    const titles = d.kind === "cue"
      ? chosen.tracks.map(t => h("tr", {},
          h("td", { class: "mono" }, `Track ${String(t.cue_track).padStart(2, "0")}`),
          h("td", {}, inp(`ct-${t.cue_track}`, ov.cue_titles?.[String(t.cue_track)] ?? "", t.title)),
          h("td", {}, inp(`ca-${t.cue_track}`, ov.cue_artists?.[String(t.cue_track)] ?? "", t.artist || chosen.artist))))
      : chosen.tracks.map(t => h("tr", {},
          h("td", { class: "mono" }, (files[t.file_id]?.relpath || "").split("/").pop()),
          h("td", {}, inp(`t-${t.file_id}`, ov.titles?.[String(t.file_id)] ?? "", t.title)),
          h("td", {}, inp(`a-${t.file_id}`, ov.artists?.[String(t.file_id)] ?? "", t.artist || chosen.artist))));
    const media = [...new Set(chosen.tracks.map(t => t.medium).filter(Boolean))];
    parts.push(h("details", { open: Object.keys(ov).length ? true : null },
      h("summary", { class: "strong", style: "cursor:pointer" }, "Edit fields" + (Object.keys(ov).length ? " · edited" : "")),
      h("p", { class: "muted xsmall" }, "Leave a box empty to keep the release's value (shown greyed). Used for the copies' tags and names."),
      h("table", { class: "kv" }, h("tbody", {},
        h("tr", {}, h("td", { class: "muted" }, h("label", { for: "e-album" }, "Album")), h("td", {}, inp("e-album", ov.album, chosen.album))),
        h("tr", {}, h("td", { class: "muted" }, h("label", { for: "e-aa" }, "Album artist")), h("td", {}, inp("e-aa", ov.albumartist, chosen.artist))),
        h("tr", {}, h("td", { class: "muted" }, h("label", { for: "e-year" }, "Year")), h("td", {}, inp("e-year", ov.year, effYear(chosen), "8em"))),
        h("tr", {}, h("td", { class: "muted" }, h("label", { for: "e-disc" }, "Disc")),
          h("td", {}, inp("e-disc", ov.disc, media.length === 1 ? media[0] : "", "5em"), " of ",
            inp("e-discs", ov.discs, chosen.mediums > 1 ? chosen.mediums : "", "5em"),
            h("div", { class: "muted xsmall" }, "Discs of one set: give them the same album, album artist and year, and each its own disc number. They are put in one album folder."))))),
      h("table", { class: "kv" }, h("thead", {}, h("tr", {}, h("th", {}, "File"), h("th", {}, "Title"), h("th", {}, "Artist"))), h("tbody", {}, titles)),
      h("div", { style: "display:flex;gap:8px;align-items:center;margin-top:8px" },
        h("button", { type: "button", class: "btn", onclick: async () => {
          const t = {}, ct = {}, a = {}, ca = {};
          const val = id => document.getElementById(id).value;
          for (const tr of chosen.tracks) {
            if (d.kind === "cue") {
              if (val(`ct-${tr.cue_track}`).trim()) ct[tr.cue_track] = val(`ct-${tr.cue_track}`);
              if (val(`ca-${tr.cue_track}`).trim()) ca[tr.cue_track] = val(`ca-${tr.cue_track}`);
            } else {
              if (val(`t-${tr.file_id}`).trim()) t[tr.file_id] = val(`t-${tr.file_id}`);
              if (val(`a-${tr.file_id}`).trim()) a[tr.file_id] = val(`a-${tr.file_id}`);
            }
          }
          const r = await post2("/api/edit", { unit_id: d.id, overrides: { album: document.getElementById("e-album").value,
            albumartist: document.getElementById("e-aa").value, year: document.getElementById("e-year").value,
            disc: document.getElementById("e-disc").value, discs: document.getElementById("e-discs").value,
            titles: t, cue_titles: ct, artists: a, cue_artists: ca } });
          document.getElementById("editMsg").textContent = r.error || "Saved.";
          if (!r.error) selectUnit(d.id);
        } }, "Save edits"),
        h("span", { id: "editMsg", class: "muted small" }))));
  }
  // ---- Use the album's own info (cue sheet / tags / folder name) ----
  const hasLocal = cands.some(c => c.source === "Local");
  parts.push(h("details", { open: !d.decision && !hasLocal && ["no_match", "weak"].includes(d.confidence) ? true : null },
    h("summary", { class: "strong", style: "cursor:pointer" }, "Use the album's own info" + (hasLocal ? " · added" : "")),
    h("p", { class: "muted xsmall" }, d.kind === "cue"
      ? "Not in any online catalogue? Organize it from its cue sheet: album, artist, year and every track title. You can correct them after accepting."
      : "Not in any online catalogue? Organize it from its tags (repaired if garbled), file names and folder name. You can correct them after accepting."),
    h("div", { style: "display:flex;gap:8px;align-items:center" },
      h("button", { type: "button", class: "btn", onclick: async () => {
        const r = await post2("/api/local", { unit_id: d.id });
        if (r.error) { document.getElementById("localMsg").textContent = r.error; return; }
        const nd = await api(`/api/unit/${d.id}`);
        S.unitChoice = r.candidate_id;
        renderUnitDetail(nd);
      } }, hasLocal ? "Read it again" : "Use own info"),
      h("span", { id: "localMsg", class: "muted small" }))));
  // ---- Search again ----
  const sj = d.search_json ? JSON.parse(d.search_json) : null;
  const isTrack = d.kind === "track";
  const topc = cands[0] || {};
  const firstFileTags = d.files[0]?.tags || {};
  const sInp = (id, val, ph) => h("input", { type: "text", id, value: val ?? "", placeholder: ph,
    style: "font:14px var(--font);min-height:32px;padding:0 8px;border:1px solid var(--btn-line);border-radius:6px;width:100%" });
  parts.push(h("details", {},
    h("summary", { class: "strong", style: "cursor:pointer" }, "Search again" + (sj ? ` · last: ${sj.link || [sj.artist, sj.album || sj.title].join(" – ")}` : "")),
    h("p", { class: "muted xsmall" }, `Match this ${isTrack ? "track" : "album"} again with your own terms, or paste a MusicBrainz ${isTrack ? "recording" : "release"} link.`),
    h("table", { class: "kv" }, h("tbody", {},
      h("tr", {}, h("td", { class: "muted" }, h("label", { for: "s-artist" }, "Artist")), h("td", {}, sInp("s-artist", sj?.artist ?? (topc.artist || firstFileTags.artist), "artist"))),
      h("tr", {}, h("td", { class: "muted" }, h("label", { for: "s-name" }, isTrack ? "Title" : "Album")),
                  h("td", {}, sInp("s-name", isTrack ? (sj?.title ?? firstFileTags.title) : (sj?.album ?? firstFileTags.album), isTrack ? "title" : "album"))),
      h("tr", {}, h("td", { class: "muted" }, h("label", { for: "s-link" }, "or link")), h("td", {}, sInp("s-link", sj?.link, "https://musicbrainz.org/release/…"))))),
    h("div", { style: "display:flex;gap:8px;align-items:center;margin-top:8px" },
      h("button", { type: "button", class: "btn", onclick: async () => {
        const r = await post2("/api/search", { unit_id: d.id, artist: document.getElementById("s-artist").value,
          name: document.getElementById("s-name").value, link: document.getElementById("s-link").value });
        document.getElementById("searchMsg").textContent = r.error || "Searching… (this album will refresh when done)";
        if (!r.error) { const st = await api("/api/job"); renderScan(st.scan); waitThenReload(d.id); }
      } }, "Search"),
      h("span", { id: "searchMsg", class: "muted small" }))));
  const decide = async (action, advance = action !== "clear") => {
    if (action === "accept" && !chosen) return;
    const next = advance ? nextUnitId(d.id) : d.id;
    if (action === "own_info") {
      const r = await post2("/api/bulk", { root: S.root.path, path: S.path, action: "own_info", unit_ids: [d.id] });
      if (r.error || r.n_failed) return tell(r.error || r.failed.join("\n"));
    } else {
      const send = extra => api("/api/decision", { method: "POST", headers: { "Content-Type": "application/json", "X-MLT": "1" },
        body: JSON.stringify({ unit_id: d.id, action, candidate_id: action === "accept" ? chosen.id : null, ...extra }) });
      let r = await send({});
      if (r.confirm === "different_album") {        // the trial's lesson: a wrong release is never one key press away
        if (!await ask("This looks like a different album, not yours: its title and artist (or volume number) " +
                       "do not match your folder and tags.\n\nThe copies would be named and tagged as that album. " +
                       "Own info is usually right here.\n\nAccept it anyway?", "Accept anyway")) return;
        r = await send({ confirm_different: true });
      }
      if (r.error) return tell(r.error);
    }
    await refreshCenter();
    await gotoUnit(next ?? d.id);
  };
  S.detailActions = { accept: () => decide("accept"), own: () => decide("own_info"), skip: () => decide("skip"),
                      undo: () => decide("clear", false) };
  const acceptedNow = d.decision?.action === "accept" && d.decision.candidate_id === chosen?.id;
  const otherAlbum = !!(chosen && chosen.source !== "Local" && chosen.evidence && chosen.evidence.different_album);
  const bar = h("div", { class: "detailbar" },
    h("button", { type: "button", class: otherAlbum ? "btn" : "btn btn-primary", disabled: !chosen, onclick: S.detailActions.accept },
      acceptedNow ? "Accepted ✓" : chosen?.source === "Local" ? "Accept own info" : otherAlbum ? "Accept anyway…" : "Accept release",
      h("span", { class: "kbd" }, "A")),
    h("button", { type: "button", class: otherAlbum ? "btn btn-primary" : "btn", disabled: d.status !== "done", onclick: S.detailActions.own },
      otherAlbum ? "Use own info" : "Own info", h("span", { class: "kbd" }, "O")),
    h("button", { type: "button", class: "btn", onclick: S.detailActions.skip }, "Skip", h("span", { class: "kbd" }, "S")),
    d.decision ? h("button", { type: "button", class: "btn", onclick: S.detailActions.undo }, "Undo", h("span", { class: "kbd" }, "U")) : null);
  const differs = chosen && chosen.source !== "Local" && chosen.evidence && !chosen.evidence.title_agrees;
  const ev = chosen && chosen.evidence || {};
  const data = t => h("span", { class: "strong", "data-no-translate": "" }, t || "?");
  if (otherAlbum) parts.splice(1, 0, h("div", { class: "banner errbanner" },
    h("p", {}, h("strong", {}, "This looks like a different album. "),
      "Accepting it would name and tag your copies as that album. Use own info, or pick another candidate below."),
    h("ul", { class: "small" },
      h("li", {}, h("span", {}, "This release:"), " ", data(chosen.album), " — ", data(chosen.artist)),
      h("li", {}, h("span", {}, "Your album:"), " ", h("span", { class: "strong", "data-no-translate": "" }, d.label), " ", h("span", { class: "mono xsmall", "data-no-translate": "" }, d.folder)),
      ev.artist === "differs" ? h("li", {}, "The artist is not the one in your folder name or tags.") : null,
      ev.numbers_differ ? h("li", {}, "The volume or disc number is different.") : null,
      !ev.title_agrees && !ev.numbers_differ ? h("li", {}, "The title is different.") : null)));
  else if (differs) parts.splice(1, 0, h("div", { class: "banner warnbanner" }, h("strong", {}, "The album title differs. "),
    h("span", {}, "Your album"), " ", h("span", { class: "strong", "data-no-translate": "" }, d.label), " ", h("span", {}, "vs this release"), " ", data(chosen.album), ". ",
    h("span", {}, "Often a different album by the same artist — compare the track mapping below, pick another candidate, or use Own info."),
    ev.artist === "differs" ? h("div", { class: "small" }, "The artist is not the one in your folder name or tags either.") : null));
  else if (chosen && chosen.source !== "Local" && ev.artist === "differs") parts.splice(1, 0, h("div", { class: "banner warnbanner" },
    h("strong", {}, "The artist differs. "), h("span", {}, "This release is by"), " ", data(chosen.artist),
    (ev.our_artists || []).length ? [" — ", h("span", {}, "your files say"), " ", data(ev.our_artists.join(", "))] : null, ". ",
    h("span", {}, "Check the track mapping below before you accept it.")));
  if (onl.length > nOnl) list.append(h("button", { type: "button", class: "linkbtn",
    onclick: () => { S.allCands = d.id; renderUnitDetail(d); } }, `Show ${onl.length - nOnl} more candidates`));
  parts.unshift(bar);
  if (d.presence === "not_found") parts.unshift(h("div", { class: "banner warnbanner" },
    h("strong", {}, "Not found. "),
    "None of this album's files are in its folder any more. Its decision, matches and edits are kept, and this " +
    "warning clears by itself if the files come back. Leaving it changes nothing.", " ",
    h("span", { class: "mono", "data-no-translate": "" }, d.folder), " ",
    h("button", { type: "button", class: "btn", style: "min-height:28px;padding:0 10px",
      onclick: () => removeNotFound([d.id]) }, "Remove…")));
  if ((d.history || []).length) parts.push(h("details", {}, h("summary", { class: "strong", style: "cursor:pointer" }, "History"),
    historyList(d.history, false)));
  parts.push(h("p", { class: "muted xsmall" }, "Decisions are saved immediately. Nothing is copied or changed until you apply a plan."));
  document.getElementById("details").replaceChildren(...parts.filter(Boolean));
}
const post2 = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json", "X-MLT": "1" },
                                        body: JSON.stringify(body) });
function waitThenReload(unitId) {
  const tick = async () => {
    const st = await api("/api/job");
    if (st.scan && st.scan.running) return setTimeout(tick, 1500);
    await refreshCenter();
    const tr = [...document.querySelectorAll("#table tbody tr")].find(r => r.getAttribute("aria-selected") === "true");
    selectUnit(unitId, tr);
  };
  setTimeout(tick, 1500);
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
  applyColumns("albumgroups");
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
    : h("p", { class: "muted small" }, d.hash_error ? "Hash error: " + d.hash_error : "Not hashed yet (run `music-steward hash` or use Hash folder)."),
    d.same_audio.length ? h("p", { class: "small" }, h("span", { class: "badge b-rel" }, "Same audio"), " also at: ",
                            d.same_audio.join(", ")) : null));
  if (d.side_files.length) parts.push(h("div", {}, h("h3", {}, "Side files in this folder"),
    h("ul", { style: "margin:0;padding-left:18px" }, d.side_files.map(s =>
      h("li", { class: "small mono" }, s.relpath.split("/").pop(), s.matches_track ? h("span", { class: "muted" }, "  (goes with this track)") : null)))));
  parts.push(h("div", {}, h("h3", {}, "Preview"),
    d.playable ? h("audio", { controls: true, preload: "none", src: `/api/audio/${d.id}` })
      : d.probe_status === "ok" ? [h("audio", { controls: true, preload: "none", src: `/api/preview/${d.id}` }),
          h("p", { class: "muted xsmall" }, `${(d.codec || d.ext).toUpperCase()} is converted to a preview when you press play (first time takes a few seconds).`)]
      : h("p", { class: "muted small" }, "Preview unavailable: this file could not be read.")));
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
      h("code", {}, `music-steward audit ${k}`), " for the full list."));
}
async function refreshAll() { await loadState(); await renderTree(); await refreshCenter(); }

document.querySelectorAll("[data-view]").forEach(b => b.addEventListener("click", async () => {
  S.view = b.dataset.view;
  document.querySelectorAll("[data-view]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
  await refreshCenter();
}));
document.getElementById("scanBtn").addEventListener("click", startScan);
document.getElementById("rescanFolderBtn").addEventListener("click", () => startJob("scan", S.path));
document.getElementById("hashFolderBtn").addEventListener("click", () => startJob("hash", S.path));
document.getElementById("recognizeBtn").addEventListener("click", async () => {
  const where = S.path || "the whole collection";
  if (!await ask(`Recognize every album in “${where}”?\n\nAlbums already matched are kept; new ones are matched at about ` +
               "10 seconds each, so a big folder can take hours. You can stop it at any time.")) return;
  startJob("recognize", S.path);
});
(async () => {
  try { const st = await api("/api/settings"); await setLanguage(st.settings.language.value); } catch (e) { /* English */ }
  await refreshAll();
})().catch(e => { document.getElementById("tableTitle").textContent = "Error: " + e.message; });


// ---------------------------------------------------------------- overview (music_steward/report.py)
// Where this collection folder stands, and what is left to do. Read-only; each line leads to where you act on it.
async function openOverview() {
  if (!S.rootId) return tell("No collection folder yet. Add one in Settings → Collection folders.");
  const o = await api(`/api/report?root_id=${S.rootId}`);
  const close = () => { dlg.close(); dlg.remove(); };
  const go = async g => {
    close();
    if (g.plans) return document.getElementById("planBtn").click();
    S.path = ""; S.expanded = new Set([""]);
    if (g.files) { await setView("folders"); return setFilter(g.files); }
    S.filter = null; S.unitFilter = g.albums; await setView("albums"); await refreshAll();
  };
  const line = ln => h("li", {}, h("span", {}, ln.text), ln.go ? [" ", h("button", { type: "button", class: "linkbtn",
    onclick: () => go(ln.go) }, ln.go.plans ? "Plans →" : "Show →")] : null);
  const dlg = h("dialog", { class: "appdialog settings" },
    h("h2", { class: "h2" }, "Overview"),
    h("p", { class: "mono xsmall", "data-no-translate": "" }, o.root),
    o.next.length ? h("section", {}, h("h3", {}, "Next"), h("ul", { class: "overview next" }, o.next.map(line))) : null,
    ...o.sections.filter(s => s.lines.length).map(s => h("section", {}, h("h3", {}, s.title), h("ul", { class: "overview" }, s.lines.map(line)))),
    h("p", { class: "muted xsmall" }, "Read-only: nothing is changed by looking. The command line has the same: music-steward report"),
    h("div", { class: "btnrow" }, h("button", { type: "button", class: "btn btn-primary", onclick: close }, "Close")));
  dlg.addEventListener("cancel", e => { e.preventDefault(); close(); });
  document.body.append(dlg);
  dlg.showModal();
}
document.getElementById("overviewBtn").addEventListener("click", openOverview);

// ---------------------------------------------------------------- removing not-found albums (notfound.py)
// The confirmation names exactly what goes; the server checks each album again and removes only those still not found.
async function removeNotFound(ids) {
  const n = ids.length;
  if (!await ask((n === 1 ? "Remove this not-found album?" : `Remove ${n} not-found albums?`) + "\n\n" +
      "This removes the app's record of it: the album, its decision, its matches and your edits. No file is touched, " +
      "in the collection folder or in the organized copies, and the History keeps its name. If the files come back " +
      "later, a scan finds them as a new album.", "Remove")) return;
  const r = await post2("/api/remove_not_found", { root_id: S.rootId, unit_ids: ids, confirm: true });
  if (r.error) return tell(r.error);
  if (r.kept.length) await tell(`Removed: ${r.removed}. Kept: ${r.kept.length}, because their files are in the collection folder again or they were set aside.`);
  S.selectedUnit = null;
  document.getElementById("details").replaceChildren(h("p", { class: "muted" }, "Select a file or album to see its details."));
  await refreshAll();
}

// ---------------------------------------------------------------- history (music_steward/history.py)
// What changed originals, copies or a collection folder's path, newest first. The message is interface text; album
// names and paths are data.
function historyList(lines, withAlbums) {
  if (!lines.length) return h("p", { class: "muted small" }, "Nothing has been copied, set aside or changed yet.");
  return h("ul", { class: "history" }, lines.map(e => h("li", {},
    h("span", { class: "muted xsmall" }, new Date(e.at * 1000).toLocaleString()), " ",
    h("span", { class: "small" }, e.message),
    e.old_path ? h("div", { class: "mono xsmall", "data-no-translate": "" }, `${e.old_path} → ${e.new_path}`) : null,
    e.dest ? h("div", { class: "mono xsmall", "data-no-translate": "" }, e.dest) : null,
    withAlbums && e.albums.length ? h("div", { class: "xsmall muted" },
      h("span", { "data-no-translate": "" }, e.albums.join(" · ")),
      e.n_albums > e.albums.length ? ` +${e.n_albums - e.albums.length}` : "") : null)));
}
async function openHistory() {
  const lines = await api("/api/history");
  const dlg = h("dialog", { class: "appdialog settings" },
    h("h2", { class: "h2" }, "History"),
    h("p", { class: "muted xsmall" }, "Every copy run, undo, and change to your originals or to a collection folder's path. " +
      "Kept on this Mac; nothing here can be changed."),
    h("div", { style: "max-height:60vh;overflow:auto" }, historyList(lines, true)),
    h("div", { class: "btnrow" }, h("button", { type: "button", class: "btn btn-primary", onclick: () => { dlg.close(); dlg.remove(); } }, "Close")));
  dlg.addEventListener("cancel", e => { e.preventDefault(); dlg.close(); dlg.remove(); });
  document.body.append(dlg);
  dlg.showModal();
}
document.getElementById("historyBtn").addEventListener("click", openHistory);

// ---------------------------------------------------------------- collection folders (ADR 0004)
// A collection folder whose drive or share is not mounted: say so, offer the share's new place when the
// same share is mounted elsewhere, Change path…, and Check again. Nothing about the folder is lost.
function renderConn(root, st) {
  const box = document.getElementById("connNote");
  if (st && st.ffmpeg === false) {            // the packaged app, started from the Finder, cannot find FFmpeg
    box.hidden = false;
    box.replaceChildren(h("p", { class: "small" }, h("strong", {}, "FFmpeg is not installed. "),
      "Music Steward needs it to read and copy audio. Install it with Homebrew (brew install ffmpeg), then quit and " +
      "reopen Music Steward."));
    return;
  }
  if (!root) {                                  // first launch: no collection folder yet
    box.hidden = false;
    box.replaceChildren(h("p", { class: "small" }, h("strong", {}, "Welcome. "),
      "Start by adding the folder that holds your music, for example your NAS share under /Volumes. It is only read: " +
      "nothing in it is changed."),
      h("div", { class: "btnrow" }, h("button", { type: "button", class: "btn btn-primary", onclick: () => addCollectionDialog() },
        "Add a collection folder…")));
    return;
  }
  if (root.available || !root.root_id) { box.hidden = true; box.replaceChildren(); return; }
  box.hidden = false;
  box.replaceChildren(...[
    h("p", { class: "small" }, "This collection folder is not connected. If the share is now mounted somewhere else, use " +
      "Change path… below. Otherwise connect it (Finder → Go → Connect to Server) and press Check again."),
    h("p", { class: "mono small", "data-no-translate": "" }, root.path),
    root.suggestion ? h("p", { class: "small" }, "The same share is now at this path. Change path to it?", " ",
      h("span", { class: "mono", "data-no-translate": "" }, root.suggestion)) : null,
    h("div", { class: "btnrow" },
      h("button", { type: "button", class: "btn", onclick: () => refreshAll() }, "Check again"),
      h("button", { type: "button", class: "btn" + (root.suggestion ? " btn-primary" : ""),
        onclick: () => changePathDialog(root.root_id, root.path, root.suggestion) }, "Change path…"))].filter(Boolean));
}

async function changePathDialog(rootId, oldPath, suggested) {
  const input = h("input", { type: "text", id: "cpPath", value: suggested || "", placeholder: "/Volumes/music" });
  const out = h("div", { class: "small", style: "margin-top:8px;min-height:18px" });
  const msg = h("p", { class: "err" });
  let checked = null, seq = 0;
  const go = h("button", { type: "button", class: "btn btn-primary", disabled: true, onclick: async () => {
    go.disabled = true;
    const r = await post2("/api/collection_path", { root_id: rootId, path: input.value });
    if (r.error) { msg.textContent = r.error; return; }
    close();
    S.rootId = rootId;
    await refreshAll();
    if (await ask(r.done.message, "Scan now")) startScan();
  } }, "Change path");
  const fchk = folderCheck(input, "any", () => {});
  const check = async () => {
    const mine = ++seq;
    checked = null; go.disabled = true; msg.textContent = "";
    if (!input.value.trim()) { out.replaceChildren(); return; }
    out.replaceChildren(h("span", { class: "muted" }, "Comparing files…"));
    let r;
    try { r = await api(`/api/collection_check?root_id=${rootId}&path=${encodeURIComponent(input.value)}`); }
    catch (e) { r = { error: String(e) }; }
    if (mine !== seq) return;
    if (r.error) { out.replaceChildren(h("span", { class: "err" }, r.error)); return; }
    checked = r;
    const res = r.result;
    out.replaceChildren(...[
      res ? h("div", {}, h("span", { class: res.code.startsWith("accepted") ? "ok" : "err", style: "font-weight:600" }, res.code.startsWith("accepted") ? "✓ " : "✗ "),
              h("span", {}, res.message),
              res.detail ? h("span", { class: "mono", "data-no-translate": "" }, " " + res.detail) : null) : null,
      ...r.blockers.map(b => h("div", { class: "err" }, "✗ ", h("span", {}, b.message),
        b.detail ? h("span", { class: "mono", "data-no-translate": "" }, " " + b.detail) : null))].filter(Boolean));
    go.disabled = !r.ok;
  };
  let timer = null;
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(check, 500); });
  const dlg = h("dialog", { class: "appdialog settings" },
    h("h2", { class: "h2" }, "Change path…"),
    h("p", { class: "small" }, "Use this when the same collection is now at another path, for example a share mounted under a new name. " +
      "Music Steward compares up to 200 known files with the new folder; nothing is read or written there."),
    h("div", { class: "small" }, "Current path: ", h("span", { class: "mono", "data-no-translate": "" }, oldPath)),
    h("h3", {}, h("label", { for: "cpPath" }, "New path")),
    h("div", { style: "display:flex;gap:8px" }, input, chooseButton(input)),
    fchk.el, out, msg,
    h("div", { class: "btnrow" },
      h("button", { type: "button", class: "btn", onclick: () => close() }, "Cancel"), go));
  const close = () => { dlg.close(); dlg.remove(); };
  dlg.addEventListener("cancel", e => { e.preventDefault(); close(); });
  document.body.append(dlg);
  dlg.showModal();
  if (input.value) check();
}

function collectionList(d, onChange) {
  const hide = async (c, hidden) => {
    if (hidden && !await ask("Hide this collection folder? It is no longer shown or scanned. Its albums, decisions, plans " +
                             "and history are kept, and Show brings everything back.", "Hide")) return;
    const r = await post2("/api/collection_hidden", { root_id: c.id, hidden });
    if (r.error) return tell(r.error);
    onChange();
    if (hidden && S.rootId === c.id) S.rootId = null;
    await refreshAll();
    openSettings();
  };
  return h("div", { class: "collist" },
    (d.collections || []).map(c => h("div", { class: "colrow" + (c.hidden ? " muted" : "") },
      h("span", { class: "mono small", "data-no-translate": "" }, c.path),
      h("span", { class: "small " + (c.hidden ? "muted" : c.connected ? "ok" : "warn") },
        c.hidden ? "Hidden" : c.connected ? "Connected" : "Not connected"),
      c.hidden ? null : h("button", { type: "button", class: "btn", onclick: () => { onChange(); changePathDialog(c.id, c.path, c.suggestion); } },
        "Change path…"),
      h("button", { type: "button", class: "btn", onclick: () => hide(c, !c.hidden) }, c.hidden ? "Show" : "Hide"))),
    h("div", {}, h("button", { type: "button", class: "btn", onclick: () => { onChange(); addCollectionDialog(); } }, "Add…")));
}

// Add a collection folder (ADR 0004 §3, 4): registers only -- nothing is read until you scan it.
async function addCollectionDialog() {
  const input = h("input", { type: "text", id: "addPath", placeholder: "/Volumes/another-share" });
  const out = h("div", { class: "small", style: "margin-top:8px;min-height:18px" });
  const msg = h("p", { class: "err" });
  let seq = 0, timer = null, inside = null, checked = null;
  const close = () => { dlg.close(); dlg.remove(); };
  const go = h("button", { type: "button", class: "btn btn-primary", disabled: true, onclick: async () => {
    go.disabled = true;
    const r = await post2("/api/collection_add", { path: input.value });
    if (r.error) { msg.textContent = r.error; return; }
    close();
    S.rootId = r.root_id; S.path = ""; S.expanded = new Set([""]);
    await refreshAll();
    if (r.done.code === "added" && await ask(r.done.message, "Scan now")) startScan();
    else if (r.done.code === "shown") tell(r.done.message);
  } }, "Add");
  const open = h("button", { type: "button", class: "btn", hidden: true, onclick: async () => {
    close();
    S.rootId = inside.root_id; S.path = inside.sub; S.expanded = new Set(["", ...inside.sub.split("/").map((_, i, a) => a.slice(0, i + 1).join("/"))]);
    await refreshAll();
  } }, "Open it");
  const check = async () => {
    // only when the text changed: leaving the box to click Add fires "change", and disabling Add then would eat the click
    if (input.value === checked) return;
    checked = input.value;
    const mine = ++seq;
    go.disabled = true; open.hidden = true; msg.textContent = ""; inside = null;
    if (!input.value.trim()) { out.replaceChildren(); return; }
    let r;
    try { r = await api(`/api/collection_add_check?path=${encodeURIComponent(input.value)}`); }
    catch (e) { r = { ok: false, result: { message: String(e) } }; }
    if (mine !== seq) return;
    const res = r.result;
    out.replaceChildren(...(res ? [h("span", { class: r.ok ? "ok" : "err", style: "font-weight:600" }, r.ok ? "✓ " : "✗ "),
      h("span", {}, res.message), res.detail ? h("span", { class: "mono", "data-no-translate": "" }, " " + res.detail) : null] : [])
      .filter(Boolean));
    go.disabled = !r.ok;
    if (r.inside && !r.inside.hidden) { inside = r.inside; open.hidden = false; }
  };
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(check, 400); });
  input.addEventListener("change", check);
  const dlg = h("dialog", { class: "appdialog settings" },
    h("h2", { class: "h2" }, "Add a collection folder"),
    h("p", { class: "small" }, "Another folder of music to look after, for example a second share or a USB drive. Adding it " +
      "only registers it: nothing is read until you scan it, and Retire originals is off for it."),
    h("h3", {}, h("label", { for: "addPath" }, "Folder")),
    h("div", { style: "display:flex;gap:8px" }, input, chooseButton(input)),
    out, msg,
    h("div", { class: "btnrow" }, h("button", { type: "button", class: "btn", onclick: close }, "Cancel"), open, go));
  dlg.addEventListener("cancel", e => { e.preventDefault(); close(); });
  document.body.append(dlg);
  dlg.showModal();
}

// ---------------------------------------------------------------- settings
async function openSettings() {
  const d = await api("/api/settings");
  const v = n => d.settings[n].value, env = n => d.settings[n].overridden_by;
  const radios = (name, opts) => h("div", { class: "radios" }, opts.map(([val, label, hint]) =>
    h("label", {}, h("input", { type: "radio", name, value: val, checked: v(name) === val ? true : null, disabled: env(name) ? true : null }),
      " ", label, hint ? h("span", { class: "muted xsmall" }, "  " + hint) : null)));
  const envNote = n => env(n) ? h("div", { class: "muted xsmall" }, `Set by ${env(n)} in the environment; change it there.`) : null;
  const srcs = v("sources").split(",");
  const chk = (id, label, on, disabled, hint) => h("label", {}, h("input", { type: "checkbox", id, checked: on ? true : null,
    disabled: disabled ? true : null }), " ", label, hint ? h("span", { class: "muted xsmall" }, "  " + hint) : null);
  const dest = h("input", { type: "text", id: "setDest", value: v("dest") || "", placeholder: "/Users/you/Music/Organized  (outside your collection)" });
  const msg = h("p", { class: "err" });
  const destCheck = folderCheck(dest, "destination", s => { const b = document.getElementById("settingsSave"); if (b) b.disabled = s.level === "error"; });
  const dlg = h("dialog", { class: "appdialog settings" },
    h("h2", { class: "h2" }, "Settings"),
    h("section", {}, h("h3", {}, h("label", { for: "setLang" }, "Language")),
      h("select", { id: "setLang", "data-no-translate": "", disabled: env("language") ? true : null },
        [["auto", "Automatic (this computer's language)"], ["en", "English"], ["zh-hans", "简体中文"], ["zh-hant", "繁體中文"],
         ["fr-ca", "Français (Canada)"], ["es-us", "Español (EE. UU.)"]].map(([val, label]) =>
          h("option", { value: val, selected: v("language") === val ? true : null }, val === "auto" ? tr(label) : label))),
      envNote("language")),
    h("section", {}, h("h3", {}, "Collection folders"),
      h("div", { class: "muted xsmall" }, "The folders Music Steward scans. They are only read. If one is now at another path, " +
        "for example a share mounted under a new name, choose Change path…: your albums and decisions stay."),
      collectionList(d, () => { dlg.close(); dlg.remove(); })),
    h("section", {}, h("h3", {}, "Names in the organized copies"),
      radios("script", [["simplified", "Simplified Chinese", "one folder per artist (鄧麗君 → 邓丽君)"],
                        ["traditional", "Traditional Chinese"], ["keep", "As each release has it"]]), envNote("script")),
    h("section", {}, h("h3", {}, "Year in folder names and tags"),
      radios("year", [["original", "Original release year", "a 2018 reissue of a 1986 album shows 1986"],
                      ["release", "Year of the matched release"]]), envNote("year")),
    h("section", {}, h("h3", {}, "Cue sheets"),
      radios("cue_encoding", [["utf-8-sig", "UTF-8", "readable by foobar2000 on Mac and Windows and most players"],
                              ["original", "Keep each cue's original encoding", "for old Chinese Windows players"]]), envNote("cue_encoding"),
      chk("setAlbumCue", "Write a cue sheet for albums of separate track files", v("album_cue") === "on", env("album_cue")), envNote("album_cue")),
    h("section", {}, h("h3", {}, "Albums of one file with a cue sheet"),
      radios("cue_albums", [["image", "Keep one file + cue sheet", "as they are; needs a cue-aware player (foobar2000, VOX…)"],
                            ["tracks", "One file per track", "lossless FLAC, for music servers and phone players; MP3 images stay one file"]]),
      envNote("cue_albums")),
    h("section", {}, h("h3", {}, "Retiring originals"),
      h("div", { class: "muted xsmall" }, "After an organized copy has been verified, Music Steward can set the original files aside " +
        "(moved into a folder called _ORIGINALS in the same collection folder, never deleted) so you can restore them, archive them or delete them yourself. " +
        "Off unless you allow it for a collection folder."),
      ...(d.retire_roots || []).map(r => chk("setRetire" + r.id, ["Allow for ", h("span", { class: "mono", "data-no-translate": "" }, r.path)], r.allowed, false)),
      h("div", { style: "margin-top:8px" }, h("label", { for: "setCooloff", class: "small" }, "Days after setting originals aside before deleting them needs an extra confirmation "),
        h("select", { id: "setCooloff", "data-no-translate": "" }, ["0", "1", "3", "7", "14", "30"].map(n =>
          h("option", { value: n, selected: v("retire_cooloff_days") === n ? true : null }, n))))),
    h("section", {}, h("h3", {}, "Online catalogues"),
      chk("srcMb", "MusicBrainz", true, true, "always"),
      chk("srcDeezer", "Deezer", srcs.includes("deezer"), env("sources")),
      chk("srcDiscogs", "Discogs", srcs.includes("discogs") && d.status.discogs, env("sources") || !d.status.discogs,
          d.status.discogs ? "" : "needs DISCOGS_TOKEN in the environment"),
      h("div", { class: "muted xsmall" }, `Fingerprints (AcoustID): ${d.status.acoustid ? "key found" : "no key — matching without fingerprints"}. ` +
        "Keys stay in your environment (~/.zshenv); the app never stores them."), envNote("sources")),
    h("section", {}, h("h3", {}, h("label", { for: "setDest" }, "Default destination folder")),
      h("div", { style: "display:flex;gap:8px" }, dest, chooseButton(dest)),
      h("div", { class: "muted xsmall" }, "Where plans copy to unless you choose another folder. Never inside your collection."),
      destCheck.el,
      h("div", { class: "muted xsmall", style: "margin-top:6px" }, "This is where the organized copies are written, not the folder to scan. The folders Music Steward scans are your collection folders: ",
        ...(d.retire_roots || []).map(r => h("span", { class: "mono", "data-no-translate": "" }, r.path + "  ")),
        "To add another, use Add… under Collection folders above.")),
    msg,
    h("div", { class: "muted xsmall", "data-no-translate": "" }, `Music Steward ${d.version}`),
    h("div", { class: "btnrow" },
      h("button", { type: "button", class: "btn", onclick: () => { dlg.close(); dlg.remove(); } }, "Cancel"),
      h("button", { type: "button", class: "btn btn-primary", id: "settingsSave", onclick: async () => {
        const pick = n => dlg.querySelector(`input[name=${n}]:checked`)?.value;
        const body = { script: pick("script"), year: pick("year"), cue_encoding: pick("cue_encoding"), cue_albums: pick("cue_albums"),
          album_cue: dlg.querySelector("#setAlbumCue").checked ? "on" : "off",
          sources: ["musicbrainz", dlg.querySelector("#srcDeezer").checked && "deezer",
                    dlg.querySelector("#srcDiscogs").checked && "discogs"].filter(Boolean).join(","),
          dest: dest.value, language: dlg.querySelector("#setLang").value,
          retire_cooloff_days: dlg.querySelector("#setCooloff").value };
        const retire_roots = Object.fromEntries((d.retire_roots || []).map(r => [r.id, dlg.querySelector("#setRetire" + r.id).checked]));
        for (const n of Object.keys(body)) if (env(n === "sources" ? "sources" : n)) delete body[n];
        const r = await post2("/api/settings", { settings: body, retire_roots });
        if (r.error) { msg.textContent = r.error; return; }
        dlg.close(); dlg.remove();
        if (body.language && body.language !== v("language")) location.reload();   // switch the whole interface
      } }, "Save")));
  document.body.append(dlg);
  dlg.showModal();
}
document.getElementById("settingsBtn").addEventListener("click", openSettings);


// ---------------------------------------------------------------- desktop window (pywebview)
// Inside the desktop window, folder fields get a native "Choose…" button.
const inDesktop = () => !!(window.pywebview && window.pywebview.api && window.pywebview.api.choose_folder);
// A live check under a folder field (music_steward/folders.py): is it written correctly, does it exist,
// can it be used? Shows the answer in words, offers the real path when a network address is typed, and
// tells `onChange` the result ({level: "empty" | "ok" | "warn" | "error"}) so a button can wait for it.
function folderCheck(input, purpose, onChange) {
  const el = h("div", { class: "small foldercheck", style: "margin-top:4px;min-height:18px" });
  let timer = null, seq = 0, state = { level: "empty", ok: true };
  const run = async () => {
    const mine = ++seq, text = input.value;
    if (!text.trim()) { state = { level: "empty", ok: true }; el.replaceChildren(); if (onChange) onChange(state); return; }
    el.replaceChildren(h("span", { class: "muted" }, "Checking the folder…"));
    let r;
    try { r = await api(`/api/check_folder?purpose=${purpose}&path=${encodeURIComponent(text)}`); }
    catch (e) { r = { level: "error", ok: false, message: String(e) }; }
    if (mine !== seq) return;                                           // the field changed meanwhile
    state = r;
    const cls = r.level === "error" ? "err" : r.level === "warn" ? "warn" : "ok", mark = r.level === "error" ? "✗ " : r.level === "warn" ? "! " : "✓ ";
    el.replaceChildren(...[
      h("span", { class: cls, style: "font-weight:600" }, mark), h("span", { class: cls === "err" ? "err" : "" }, r.message),
      r.detail ? h("span", { class: "mono", "data-no-translate": "" }, " " + r.detail) : null,
      r.suggestion ? h("div", {}, h("span", { class: "mono", "data-no-translate": "" }, r.suggestion), " ",
        h("button", { type: "button", class: "btn", style: "min-height:26px;padding:0 8px",
          onclick: () => { input.value = r.suggestion; input.dispatchEvent(new Event("input")); } }, "Use this path")) : null,
      r.free != null && r.ok ? h("div", { class: "muted xsmall" }, `${fmtSize(r.free)} free`) : null].filter(Boolean));
    if (onChange) onChange(state);
  };
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 400); });
  input.addEventListener("change", run);
  setTimeout(run, 0);
  return { el, state: () => state, recheck: run };
}

function chooseButton(input) {
  const b = h("button", { type: "button", class: "btn", hidden: inDesktop() ? null : true, onclick: async () => {
    const p = await window.pywebview.api.choose_folder(input.value || "");
    if (p) { input.value = p; input.dispatchEvent(new Event("input")); }
  } }, "Choose…");
  window.addEventListener("pywebviewready", () => { b.hidden = false; });
  return b;
}


// ---------------------------------------------------------------- panel sizes (remembered per viewer)
const LAYOUT_KEY = "mlt.layout";
function layoutEl() { return document.getElementById("reviewPage"); }
function setWidths(w) {
  const el = layoutEl(), max = Math.max(320, window.innerWidth * 0.45);
  if (w.left) el.style.setProperty("--w-left", Math.round(Math.min(max, Math.max(170, w.left))) + "px");
  if (w.right) el.style.setProperty("--w-right", Math.round(Math.min(max, Math.max(300, w.right))) + "px");
}
function saveWidths() {
  const cs = getComputedStyle(layoutEl());
  try { localStorage.setItem(LAYOUT_KEY, JSON.stringify({ left: parseInt(cs.getPropertyValue("--w-left")),
                                                          right: parseInt(cs.getPropertyValue("--w-right")) })); } catch (e) { /* storage off */ }
}
function autoSize(side) {
  // left: fit the longest folder name (within limits); right: about 30% of the window
  if (side !== "right") {
    const tree = document.getElementById("tree");
    setWidths({ left: Math.min(380, (tree ? tree.scrollWidth : 220) + 40) });
  }
  if (side !== "left") setWidths({ right: Math.min(600, Math.max(380, window.innerWidth * 0.3)) });
  saveWidths();
}
(function initResizers() {
  try { const w = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "null"); if (w) setWidths(w); } catch (e) { /* none */ }
  for (const r of document.querySelectorAll(".resizer")) {
    const side = r.dataset.side;
    r.addEventListener("dblclick", () => autoSize(side));
    r.addEventListener("pointerdown", e => {
      e.preventDefault();
      const cs = getComputedStyle(layoutEl()), x0 = e.clientX;
      const w0 = parseInt(cs.getPropertyValue(side === "left" ? "--w-left" : "--w-right"));
      r.setPointerCapture(e.pointerId); r.classList.add("dragging"); document.body.classList.add("resizing");
      const move = ev => setWidths(side === "left" ? { left: w0 + (ev.clientX - x0) } : { right: w0 - (ev.clientX - x0) });
      const up = () => { r.removeEventListener("pointermove", move); r.classList.remove("dragging");
                         document.body.classList.remove("resizing"); saveWidths(); };
      r.addEventListener("pointermove", move);
      r.addEventListener("pointerup", up, { once: true });
    });
  }
})();

// ---------------------------------------------------------------- show / hide table columns (remembered)
const PINNED = new Set(["", "Title / file", "Album / folder"]);   // the name column always shows
function applyColumns(kind) {
  const t = document.getElementById("table");
  t.dataset.kind = kind;
  const heads = [...t.querySelectorAll("thead th")].map(th => th.textContent.trim());
  let hidden = [];
  try { hidden = JSON.parse(localStorage.getItem("mlt.cols." + kind) || "[]"); } catch (e) { /* none */ }
  let css = "";
  heads.forEach((name, i) => { if (hidden.includes(name) && !PINNED.has(name)) css += `#table[data-kind="${kind}"] tr > :nth-child(${i + 1}){display:none}`; });
  let st = document.getElementById("colStyle");
  if (!st) { st = document.createElement("style"); st.id = "colStyle"; document.head.append(st); }
  st.textContent = css;
  const pop = document.getElementById("colMenuPop");
  if (!pop) return;
  pop.replaceChildren(h("div", { class: "muted xsmall", style: "margin-bottom:4px" }, "Show columns"),
    ...heads.filter(n => n && !PINNED.has(n) && !/^\d+$/.test(n)).map(name => h("label", { class: "small" },
      h("input", { type: "checkbox", checked: hidden.includes(name) ? null : true, onchange: e => {
        hidden = e.target.checked ? hidden.filter(x => x !== name) : [...new Set([...hidden, name])];
        try { localStorage.setItem("mlt.cols." + kind, JSON.stringify(hidden)); } catch (err) { /* storage off */ }
        applyColumns(kind);
      } }), " ", name)));
}
document.addEventListener("click", e => {   // close the Columns menu when clicking elsewhere
  const m = document.getElementById("colMenu");
  if (m && m.open && !m.contains(e.target)) m.open = false;
});


// ---------------------------------------------------------------- search (albums, folders, tracks; pinyin)
const SR = { timer: null, items: [], idx: -1, seq: 0 };
async function goFolder(path) {
  const parts = path ? path.split("/") : [];
  for (let i = 0; i <= parts.length; i++) S.expanded.add(parts.slice(0, i).join("/"));
  S.path = path; S.filter = null; clearListFilter();
  await refreshCenter(); renderFilters(); await renderTree();
  document.querySelector('#tree button[aria-current="true"]')?.scrollIntoView({ block: "center" });
}
async function setView(v) {
  if (S.view === v) return;
  document.querySelector(`[data-view="${v}"]`)?.click();
  await new Promise(r => setTimeout(r, 50));
}
async function openResult(r) {
  closeSearch();
  if (r.kind === "folder") { await goFolder(r.folder); return; }
  if (r.kind === "album") {
    await setView("albums"); await goFolder(r.folder);
    await gotoUnit(r.ref); return;
  }
  await setView("folders"); await goFolder(r.folder);   // track
  const tr = document.querySelector(`#table tbody tr[data-file="${r.ref}"]`);
  if (tr) { tr.scrollIntoView({ block: "center" }); tr.click(); }
}
function closeSearch() { document.getElementById("searchPop").hidden = true; SR.idx = -1; }
function drawSearch(res) {
  const pop = document.getElementById("searchPop");
  SR.items = []; SR.idx = -1;
  const group = (key, title, icon) => {
    const list = res[key] || [], n = (res.counts || {})[key] || 0;
    if (!list.length) return null;
    return h("div", { class: "sgroup" }, h("div", { class: "shead" }, `${title} · ${n.toLocaleString()}` + (n > list.length ? ` (showing ${list.length})` : "")),
      list.map(r => { const i = SR.items.push(r) - 1;
        return h("button", { type: "button", class: "sitem", "data-i": i, onclick: () => openResult(r),
                              onmouseenter: () => { SR.idx = i; markSearch(); } },
          h("span", { class: "sicon" }, icon),
          h("span", { class: "stext" }, h("span", { class: "strong", "data-no-translate": "" }, r.label),
            h("span", { class: "muted small" }, r.kind === "folder" ? `  ${r.n} tracks · ${r.folder}` : (r.sub ? `  ${r.sub}` : ""),
              r.kind !== "folder" ? h("span", { class: "mono xsmall" }, `  ${r.folder}/`) : null))); }));
  };
  const parts = [group("albums", "Albums", "💿"), group("folders", "Folders", "📁"), group("tracks", "Tracks", "♪")].filter(Boolean);
  pop.replaceChildren(...(parts.length ? parts : [h("div", { class: "muted small", style: "padding:10px" }, `Nothing found for “${res.q}”.`)]));
  pop.hidden = false;
}
function markSearch() {
  document.querySelectorAll("#searchPop .sitem").forEach(b => b.classList.toggle("on", Number(b.dataset.i) === SR.idx));
  document.querySelector("#searchPop .sitem.on")?.scrollIntoView({ block: "nearest" });
}
(function initSearch() {
  const box = document.getElementById("searchBox");
  box.addEventListener("input", () => {
    clearTimeout(SR.timer);
    const q = box.value.trim();
    if (!q) return closeSearch();
    SR.timer = setTimeout(async () => {
      const seq = ++SR.seq;
      const pop = document.getElementById("searchPop");
      if (pop.hidden) { pop.replaceChildren(h("div", { class: "muted small", style: "padding:10px" }, "Searching…")); pop.hidden = false; }
      const res = await api(`/api/search?q=${encodeURIComponent(q)}&root_id=${S.rootId || ""}`);
      if (seq === SR.seq) drawSearch(res);
    }, 220);
  });
  box.addEventListener("keydown", e => {
    if (e.key === "Escape") { box.value = ""; closeSearch(); box.blur(); return; }
    if (!SR.items.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); SR.idx = Math.min(SR.items.length - 1, SR.idx + 1); markSearch(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); SR.idx = Math.max(0, SR.idx - 1); markSearch(); }
    else if (e.key === "Enter") { e.preventDefault(); openResult(SR.items[Math.max(0, SR.idx)]); }
  });
  box.addEventListener("focus", () => { if (box.value.trim() && SR.items.length) document.getElementById("searchPop").hidden = false; });
  document.addEventListener("click", e => { if (!e.target.closest(".searchwrap")) closeSearch(); });
  document.addEventListener("keydown", e => {   // "/" or ⌘K / Ctrl+K: jump to search
    const t = e.target;
    if (t && t.closest && t.closest("input, textarea, select, dialog")) return;
    if (e.key === "/" || ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k")) { e.preventDefault(); box.focus(); box.select(); }
  });
})();


// ---------------------------------------------------------------- "Filter this list" (current folder only)
function clearListFilter() { S.listQ = ""; const el = document.getElementById("listFilter"); if (el) el.value = ""; }
(function initListFilter() {
  const el = document.getElementById("listFilter");
  let t = null;
  el.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { S.listQ = el.value.trim(); refreshCenter(); }, 250); });
  el.addEventListener("keydown", e => { if (e.key === "Escape") { clearListFilter(); refreshCenter(); el.blur(); } });
})();
