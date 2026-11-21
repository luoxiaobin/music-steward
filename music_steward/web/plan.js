// Plan screen (mockup "Review plan"). Uses h(), api(), fmtSize(), S from app.js.
"use strict";

const P = { planId: null, poll: null };

function showPage(name) {
  document.getElementById("reviewPage").hidden = name !== "review";
  document.getElementById("planPage").hidden = name !== "plan";
}
async function openPlans(planId) {
  showPage("plan");
  if (planId) P.planId = planId;
  if (P.planId) return renderPlan(P.planId);
  return renderPlanBuilder();
}
const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json", "X-MLT": "1" },
                                        body: JSON.stringify(body) });

async function renderPlanBuilder(msg) {
  const st = await api("/api/state");
  let saved = "";
  try { saved = localStorage.getItem("mlt.dest") || ""; } catch (e) { /* storage may be blocked */ }
  const input = h("input", { type: "text", id: "destInput", value: st.dest || saved, "aria-label": "Destination folder",
                             placeholder: "/Users/you/Music/Organized  (outside your collection)" });
  const plans = await api("/api/plans");
  const scope = S.path || "(whole root)";
  const page = document.getElementById("planPage");
  page.replaceChildren(...[
    h("div", { class: "plan-head" },
      h("a", { href: "#", onclick: e => { e.preventDefault(); showPage("review"); } }, "← Back to review"),
      h("h1", {}, "New plan")),
    h("div", { class: "card" },
      h("h2", { class: "h2" }, "Collect accepted albums"),
      h("p", { class: "muted small" }, `Scope: ${scope}. Every album you accepted in this folder becomes part of the plan. ` +
        "Building a plan changes nothing on disk."),
      h("div", { class: "field" }, h("label", { for: "destInput", class: "strong" }, "Destination"), input, chooseButton(input),
        h("button", { type: "button", class: "btn btn-primary", onclick: async () => {
          try { localStorage.setItem("mlt.dest", input.value); } catch (e) { /* ignore */ }
          const r = await post("/api/plan", { root: S.root.path, path: S.path, dest: input.value });
          if (r.error) return renderPlanBuilder(r.error);
          P.planId = r.plan_id; renderPlan(r.plan_id);
        } }, "Build plan")),
      msg ? h("p", { class: "err" }, msg) : null),
    plans.length ? h("div", { class: "card" }, h("h2", { class: "h2" }, "Recent plans"),
      h("table", {}, h("tbody", {}, plans.map(p => {
        const s = JSON.parse(p.summary_json || "{}");
        return h("tr", { onclick: () => { P.planId = p.id; renderPlan(p.id); } },
          h("td", {}, `Plan ${p.id}`), h("td", { class: "muted" }, new Date(p.created_at * 1000).toLocaleString()),
          h("td", { class: "mono" }, p.dest_root), h("td", {}, `${s.ok} to copy · ${s.blocked} blocked`));
      })))) : null
  ].filter(Boolean));
}

function tagDiff(it) {
  const t = it.tags || {}, cur = it.current_tags || {};
  const keys = ["title", "artist", "album", "albumartist", "track", "disc", "date"];
  return keys.filter(k => t[k] !== null && t[k] !== undefined && String(t[k]) !== String(cur[k] ?? ""))
             .map(k => ({ field: k, current: cur[k], proposed: t[k] }));
}

async function renderPlan(id) {
  const p = await api(`/api/plan/${id}`);
  const st = await api("/api/state");
  const s = p.summary, items = p.items;
  const ok = items.filter(i => i.status === "ok"), blocked = items.filter(i => !["ok", "duplicate_source"].includes(i.status));
  const dups = items.filter(i => i.status === "duplicate_source");
  const audio = ok.filter(i => i.kind === "audio" || i.kind === "split"),
        side = items.filter(i => i.kind !== "audio" && i.kind !== "split");
  const sideLabel = i => i.kind === "lrc" ? "Follows its track" : i.kind === "cue" ? "Rewritten: file name + titles" : i.kind === "cue_new" ? "New: album cue sheet" : "Linked";
  const lastRun = p.runs[p.runs.length - 1];
  const applied = lastRun && !lastRun.undone_at;
  const job = st.scan || {};
  const busy = job.running;
  const badge = applied ? h("span", { class: "badge b-ready" }, "Applied") : lastRun ? h("span", { class: "badge b-unid" }, "Undone")
                        : h("span", { class: "badge b-unid" }, "Draft");
  const applyBtn = h("button", { type: "button", class: "btn btn-primary", disabled: busy || !ok.length || applied,
    onclick: async () => { await post("/api/apply", { id }); pollPlan(id); } },
    `Apply · copy ${ok.length} files` + (blocked.length ? ` (${blocked.length} blocked are skipped)` : "") +
    (dups.length ? ` · ${s.duplicate_albums_skipped || ""} duplicate album(s) copied once` : ""));
  const rows = (list, cols) => h("table", {}, h("thead", {}, h("tr", {}, cols.map(c => h("th", {}, c[0])))),
    h("tbody", {}, list.map(i => h("tr", {}, cols.map(c => c[1](i))))));
  const diffs = audio.flatMap(i => tagDiff(i).map(d => ({ ...d, file: i.src_rel })));
  const page = document.getElementById("planPage");
  page.replaceChildren(...[
    h("div", { class: "plan-head" },
      h("a", { href: "#", onclick: e => { e.preventDefault(); showPage("review"); refreshCenter(); } }, "← Back to review"),
      h("h1", {}, `Review plan · ${id}`), badge,
      h("div", { style: "margin-left:auto;display:flex;gap:8px;flex-wrap:wrap" },
        h("button", { type: "button", class: "btn", onclick: () => { P.planId = null; renderPlanBuilder(); } }, "New plan"),
        applyBtn)),
    busy ? progressCard(job, ok) : null,
    !busy && job.result && ["apply", "resume", "retry"].includes(job.kind) && job.result.status === "stopped"
      ? h("div", { class: "card banner" }, h("strong", {}, "Stopped. "), job.result.abort_reason) : null,
    doneForYou(s, dups),
    (s.left_out || []).length ? h("div", { class: "card block" }, h("h2", { class: "h2" }, "Left out · audio changed since you decided ",
        h("span", { class: "badge b-conflict" }, s.left_out.length)),
      rows(s.left_out, [["Album", x => h("td", { "data-no-translate": "" }, x.label)],
                        ["Folder", x => h("td", { class: "mono", "data-no-translate": "" }, (x.folder || ".") + "/")]]),
      h("p", { class: "muted small" }, "Decide these albums again in review, then build a new plan.")) : null,
    previewTree(ok.concat(items.filter(i => i.kind === "cue_new" && i.status === "ok" && !ok.includes(i)))),
    h("div", { class: "cards" },
      h("div", { class: "card" }, h("div", { class: "lbl" }, "Source root"), h("div", { class: "mono", style: "margin-top:6px" }, S.root.path),
        h("div", { class: "muted small" }, "Read-only · never modified")),
      h("div", { class: "card" }, h("div", { class: "lbl" }, "Destination root"), h("div", { class: "mono", style: "margin-top:6px;word-break:break-all" }, p.dest_root),
        h("div", { class: "muted small" }, "New files only · nothing overwritten")),
      h("div", { class: "card" }, h("div", { class: "lbl" }, "Files"), h("div", { class: "big" }, `${ok.length} to copy`),
        h("div", { class: "muted small" }, `${s.albums} albums · ${blocked.length} blocked`)),
      h("div", { class: "card" }, h("div", { class: "lbl" }, "Space needed"), h("div", { class: "big" }, `≈ ${fmtSize(s.bytes)}`),
        h("div", { class: "muted small" }, p.dest_free != null ? `${fmtSize(p.dest_free)} free on destination` : ""))),
    blocked.length ? h("div", { class: "card block" }, h("h2", { class: "h2" }, "Blocked · skipped by Apply ",
        h("span", { class: "badge b-conflict" }, blocked.length)),
      rows(blocked, [["Source", i => h("td", { class: "mono" }, i.src_rel)], ["Destination", i => h("td", { class: "mono" }, i.dest_rel)],
                     ["Why", i => h("td", { class: "muted" }, i.note)]])) : null,
    p.runs.length ? h("div", { class: "card" }, h("h2", { class: "h2" }, "Runs"),
      rows(p.runs, [["Run", r => h("td", {}, `#${r.id} · ${r.method}`)],
                    ["Started", r => h("td", { class: "muted" }, new Date(r.started_at * 1000).toLocaleString())],
                    ["Result", r => h("td", {}, Object.entries(r.summary.states || {}).map(([k, n]) => `${n} ${k}`).join(" · ") +
                                         (r.undone_at ? " · undone" : ""))],
                    ["", r => h("td", {}, r.undone_at ? "" : [
                      h("button", { type: "button", class: "btn", disabled: busy, onclick: async () => {
                        if (!await ask("Remove the copies this run created? Files you changed since are kept.")) return;
                        await post("/api/undo", { id: r.id }); pollPlan(id); } }, "Undo run"), " ",
                      h("button", { type: "button", class: "btn", disabled: busy, onclick: async () => {
                        await post("/api/resume", { id: r.id }); pollPlan(id); } }, "Resume"),
                      ...(retirable(r) ? [" ", h("button", { type: "button", class: "btn", disabled: busy || !p.retire_allowed,
                        title: p.retire_allowed ? "Set the originals of the albums that were copied aside (nothing is deleted)"
                                                : "Allow it first: Settings → Originals",
                        onclick: () => retireDialog(r.id, id) }, "Retire originals…")] : []),
                      ...((r.summary.states || {}).failed ? [" ", h("button", { type: "button", class: "btn primary", disabled: busy,
                        title: "Try the failed files again, e.g. after reconnecting the drive", onclick: async () => {
                          await post("/api/retry", { id: r.id }); pollPlan(id); } }, `Retry failed (${r.summary.states.failed})`)] : [])])]])) : null,
    failedList(p.runs, rows),
    originalsCard(p, id, busy, rows),
    retireResult(job, busy),
    h("div", { class: "card" }, h("h2", { class: "h2" }, "Tag changes ",
        h("span", { class: "muted small", style: "font-weight:400" }, `${diffs.length} changes in ${audio.length} files · written to the copies only`)),
      h("div", { style: "overflow-x:auto" }, rows(diffs.slice(0, 200), [
        ["File", d => h("td", { class: "mono" }, d.file.split("/").pop())], ["Field", d => h("td", {}, d.field)],
        ["Current", d => h("td", { class: "mono" }, d.current ?? h("span", { class: "muted" }, "empty"))],
        ["Proposed", d => h("td", { class: "proposed" }, String(d.proposed))]])),
      diffs.length > 200 ? h("p", { class: "muted small" }, `+ ${diffs.length - 200} more`) : null,
      h("p", { class: "muted small" }, "All other tags, artwork, lyrics and ReplayGain are kept as they are.")),
    h("div", { class: "card" }, h("h2", { class: "h2" }, "Path changes ",
        h("span", { class: "muted small", style: "font-weight:400" }, `${audio.length} files · Album artist / Album (Year) / NN - Title`)),
      h("div", { style: "overflow-x:auto" }, rows(audio.slice(0, 200), [
        ["Source", i => h("td", { class: "mono" }, i.src_rel)], ["Destination", i => h("td", { class: "mono" }, i.dest_rel)]]))),
    side.length ? h("div", { class: "card" }, h("h2", { class: "h2" }, "Side files"),
      rows(side, [["File", i => h("td", { class: "mono" }, i.src_rel)],
                  ["Goes to", i => h("td", { class: "mono" }, i.dest_rel)],
                  ["", i => h("td", {}, i.status === "ok" ? h("span", { class: "badge b-ready" }, sideLabel(i))
                                                           : h("span", { class: "badge b-review" }, "Blocked"))]])) : null,
    h("div", { class: "card muted small" }, "Frozen in this plan: source sizes and modification times, tag values, destination paths. " +
      "A source that changes before Apply is skipped and reported. Copies are verified (audio unchanged) before they appear under their final name.")
  ].filter(Boolean));
}

// ---- apply progress: bytes done (items run in plan order), ETA, Stop
function failedList(runs, rows) {
  // what went wrong, per file, for runs not undone (the reasons are the journal's own words)
  const rowsOut = [];
  for (const r of runs) {
    if (r.undone_at) continue;
    for (const f of (r.summary.failed || [])) rowsOut.push({ run: r.id, src: f.src, reason: f.reason });
  }
  if (!rowsOut.length) return null;
  return h("div", { class: "card block" }, h("h2", { class: "h2" }, "Not copied ", h("span", { class: "badge b-conflict" }, rowsOut.length)),
    rows(rowsOut.slice(0, 200), [["Run", x => h("td", {}, `#${x.run}`)], ["Source", x => h("td", { class: "mono" }, x.src.split("/").slice(-2).join("/"))],
                                 ["Why", x => h("td", { class: "muted" }, x.reason || "")]]),
    h("p", { class: "muted small" }, "Failed files can be tried again with Retry failed. Conflicts and changed sources need a new plan."));
}

// ---- originals (ADR 0003): set aside after the copy is verified; Restore; what to do with them
const retirable = r => !r.undone_at && r.finished_at && ((r.summary.states || {}).committed || (r.summary.states || {}).already_existed);

async function retireDialog(runId, planId) {
  const dlg = h("dialog", { class: "appdialog settings" }, h("h2", { class: "h2" }, "Retire originals"),
    h("p", { class: "muted" }, "Checking the organized copies…"));
  document.body.append(dlg);
  dlg.showModal();
  const close = () => { dlg.close(); dlg.remove(); };
  let d;
  try { d = await api(`/api/retire_plan?run=${runId}`); } catch (e) { d = { error: String(e) }; }
  if (d.error) {
    dlg.replaceChildren(h("h2", { class: "h2" }, "Retire originals"), h("p", { class: "err" }, d.error),
      h("div", { class: "btnrow" }, h("button", { type: "button", class: "btn btn-primary", onclick: close }, "OK")));
    return;
  }
  const cant = d.albums.filter(a => !a.eligible), flagged = d.albums.filter(a => a.eligible && a.flags.length);
  const tick = h("input", { type: "checkbox", id: "retireAck" });
  const go = h("button", { type: "button", class: "btn btn-primary", disabled: true, onclick: async () => {
    const r = await post("/api/retire", { id: runId, ack: true });
    close();
    if (r.error) return tell(r.error);
    pollPlan(planId);
  } }, `Set aside albums: ${d.albums_ok} (${fmtSize(d.bytes_ok)})`);
  tick.addEventListener("change", () => { go.disabled = !tick.checked || !d.albums_ok; });
  dlg.replaceChildren(...[
    h("h2", { class: "h2" }, "Retire originals"),
    h("p", {}, h("strong", {}, `Albums ${d.albums_ok} · files ${d.files_ok} · ${fmtSize(d.bytes_ok)}`),
      " can be set aside: their organized copies exist and their audio is identical to the originals."),
    h("p", { class: "small" }, "The original files are moved (not copied, not deleted) into a folder named ",
      h("code", {}, "_ORIGINALS"), " inside your collection folder, on the same drive. It takes seconds. " +
      "The folder has a README explaining what it is. You can restore them from here at any time."),
    cant.length ? h("details", {}, h("summary", {}, h("strong", {}, "Albums that stay where they are "),
      h("span", { class: "badge b-conflict" }, cant.length)),
      h("ul", { class: "small" }, cant.slice(0, 100).map(a => reasonLi(a.label, a.reason)),
        cant.length > 100 ? h("li", { class: "muted" }, `+ ${cant.length - 100} more`) : null)) : null,
    flagged.length ? h("details", {}, h("summary", {}, h("strong", {}, "Worth a look "), h("span", { class: "badge b-unid" }, flagged.length)),
      h("ul", { class: "small" }, flagged.slice(0, 100).map(a => reasonLi(a.label, ...a.flags)))) : null,
    h("div", { class: "banner" }, h("strong", {}, "One thing to know. "),
      "Afterwards the organized copy is the only finished copy; the originals are only set aside. " +
      "Music Steward does not make backups. Keep your own backup of the organized copy."),
    h("label", { class: "small" }, tick, " I know this, and I have (or will make) a backup of the organized copy."),
    h("div", { class: "btnrow" }, h("button", { type: "button", class: "btn", onclick: close }, "Cancel"), go)
  ].filter(Boolean));            // a null child would be written into the page as the word "null"
  dlg.querySelector("#retireAck").focus();
}

// one list item: the album, then each reason in its own element (a fixed sentence the page can translate)
const reasonLi = (label, ...reasons) => h("li", {}, h("strong", { "data-no-translate": "" }, label), " — ",
  ...reasons.flatMap((x, i) => [i ? "; " : null, h("span", {}, x)].filter(Boolean)));

// ---- Archive and Delete of the parked originals (ADR 0003)
async function archiveDialog(b, planId) {
  const input = h("input", { type: "text", id: "archTarget", "data-no-translate": "", placeholder: "/Volumes/Backup  (outside your collection)",
                             value: (b.archive && b.archive.target) || "" });
  const info = h("div", { class: "small", style: "margin-top:8px" });
  const go = h("button", { type: "button", class: "btn btn-primary", disabled: true }, "Copy and verify");
  const dlg = h("dialog", { class: "appdialog settings" });
  const close = () => { dlg.close(); dlg.remove(); };
  let timer = null;
  const check = async () => {
    go.disabled = true;
    const target = input.value.trim();
    if (!target) { info.replaceChildren(); return; }
    info.replaceChildren(h("span", { class: "muted" }, "Checking the place…"));
    const d = await api(`/api/archive_plan?batch=${b.id}&target=${encodeURIComponent(target)}`);
    if (target !== input.value.trim()) return;                         // typed on meanwhile
    if (d.error) { info.replaceChildren(h("span", { class: "err" }, d.error)); return; }
    go.disabled = false;
    info.replaceChildren(...[
      h("div", {}, h("strong", {}, `Files ${d.files} · ${fmtSize(d.bytes)}`), `  ·  ${fmtSize(d.free)} free there`),
      h("div", { class: "muted xsmall" }, "They are copied into ", h("span", { class: "mono", "data-no-translate": "" }, d.dest)),
      d.same_drive ? h("div", { class: "banner" }, h("strong", {}, "Same drive. "),
        "This is the same drive as your collection: if the drive fails, the archive is lost with it.") : null].filter(Boolean));
  };
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(check, 500); });
  go.addEventListener("click", async () => {
    const r = await post("/api/archive", { id: b.id, target: input.value.trim() });
    close();
    if (r.error) return tell(r.error);
    pollPlan(planId);
  });
  dlg.replaceChildren(h("h2", { class: "h2" }, "Archive the originals"),
    h("p", { class: "small" }, "Copies these original files to a place you choose — an external drive or another share — and checks every file, " +
      "so you have a backup. The originals stay where they are."),
    h("div", { style: "display:flex;gap:8px" }, input, chooseButton(input)), info,
    h("div", { class: "btnrow" }, h("button", { type: "button", class: "btn", onclick: close }, "Cancel"), go));
  document.body.append(dlg);
  dlg.showModal();
  if (input.value) check();
  input.focus();
}

async function deleteDialog(b, planId) {
  const dlg = h("dialog", { class: "appdialog settings" }, h("h2", { class: "h2" }, "Delete the originals"),
    h("p", { class: "muted" }, "Checking the organized copies again…"));
  document.body.append(dlg);
  dlg.showModal();
  const close = () => { dlg.close(); dlg.remove(); };
  let d;
  try { d = await api(`/api/delete_plan?batch=${b.id}`); } catch (e) { d = { error: String(e) }; }
  if (d.error) {
    dlg.replaceChildren(h("h2", { class: "h2" }, "Delete the originals"), h("p", { class: "err" }, d.error),
      h("div", { class: "btnrow" }, h("button", { type: "button", class: "btn btn-primary", onclick: close }, "OK")));
    return;
  }
  const kept = d.albums.filter(a => !a.eligible), L = d.lost || {};
  const typed = h("input", { type: "text", id: "delTyped", autocomplete: "off", "data-no-translate": "", placeholder: "DELETE" });
  const ackBackup = h("input", { type: "checkbox", id: "delAckBackup" }), ackPlayed = h("input", { type: "checkbox", id: "delAckPlayed" });
  const go = h("button", { type: "button", class: "btn", disabled: true, style: "background:var(--conflict);border-color:var(--conflict);color:#fff",
    onclick: async () => {
      const r = await post("/api/delete", { id: b.id, typed: typed.value, ack_no_backup: ackBackup.checked, ack_checked: ackPlayed.checked });
      close();
      if (r.error) return tell(r.error);
      pollPlan(planId);
    } }, `Delete forever: files ${d.files_ok} (${fmtSize(d.bytes_ok)})`);
  const ready = () => { go.disabled = !(d.files_ok && typed.value === "DELETE" && (!d.needs_no_backup_ack || ackBackup.checked) && (!d.young || ackPlayed.checked)); };
  for (const el of [typed, ackBackup, ackPlayed]) { el.addEventListener("input", ready); el.addEventListener("change", ready); }
  const lostItems = [
    L.tags_files ? `Original tag values in files: ${L.tags_files} (including garbled text that was repaired)` : null,
    L.split_cues ? `Original cue sheets of albums split into tracks: ${L.split_cues}` : null,
    L.rewritten_cues ? `Original cue sheets that were rewritten: ${L.rewritten_cues}` : null,
    L.split_lyrics ? `Original lyrics files that were split per track: ${L.split_lyrics}` : null].filter(Boolean);
  dlg.replaceChildren(...[
    h("h2", { class: "h2" }, "Delete the originals"),
    h("p", {}, h("strong", {}, `Albums ${d.albums_ok} · files ${d.files_ok} · ${fmtSize(d.bytes_ok)}`),
      " would be deleted from the originals folder, freeing that space."),
    h("div", { class: "callout callout-red" }, h("strong", {}, "This cannot be undone. There is no Trash.")),
    d.has_archive
      ? h("div", { class: "callout callout-green" }, h("strong", {}, "A verified archive exists: "), "a checked copy of these originals is at ",
          h("span", { class: "mono", "data-no-translate": "" }, d.archive.dest))
      : h("div", { class: "callout callout-red" }, h("strong", {}, "No archive. "),
          "After this, the organized copy is your only copy. Music Steward does not make backups."),
    lostItems.length ? h("div", { class: "small", style: "margin-top:10px" }, h("strong", {}, "Not carried over to the organized copies, and lost for good:"),
      h("ul", {}, lostItems.map(t => h("li", {}, t)))) : null,
    kept.length ? h("details", {}, h("summary", {}, h("strong", {}, "Albums that stay where they are "),
      h("span", { class: "badge b-conflict" }, kept.length)),
      h("ul", { class: "small" }, kept.slice(0, 100).map(a => reasonLi(a.label, a.reason)))) : null,
    d.young ? h("label", { class: "small", style: "display:block;margin-top:10px" }, ackPlayed,
      " I have played the organized copies and checked them.") : null,
    d.needs_no_backup_ack ? h("label", { class: "small", style: "display:block;margin-top:6px" }, ackBackup,
      " I understand I have no backup of the organized copies.") : null,
    h("div", { style: "margin-top:12px" }, h("label", { for: "delTyped", class: "strong small" }, "Type the word DELETE to confirm"), typed),
    h("div", { class: "btnrow" }, h("button", { type: "button", class: "btn", onclick: close }, "Cancel"), go)
  ].filter(Boolean));
  typed.focus();
}

function originalsCard(p, id, busy, rows) {
  if (!(p.originals || []).length) return null;
  const state = b => b.restored_at ? "Restored to the collection" : b.deleted && !b.moved ? "Originals deleted"
    : b.status === "running" ? "Interrupted — Finish setting aside to continue"
    : b.status === "stopped" ? "Stopped — Finish setting aside to continue" : "Set aside (can be restored)";
  return h("div", { class: "card" }, h("h2", { class: "h2" }, "Originals set aside"),
    h("p", { class: "muted small" }, "These original files were set aside, unchanged, in the folder shown; each folder has a README. " +
      "What you can do with them: restore them; archive them (a checked copy on a backup drive); or delete them to save space " +
      "— only after you have played the organized copies and have a backup of them. Deleting cannot be undone."),
    rows(p.originals, [["Set aside on", b => h("td", {}, new Date(b.created_at * 1000).toLocaleString())],
      ["Albums", b => h("td", {}, String((b.summary || {}).albums ?? 0))],
      ["Files · size", b => h("td", {}, `${b.moved} · ${fmtSize((b.summary || {}).bytes || 0)}`)],
      ["Originals folder", b => h("td", { class: "mono", style: "word-break:break-all", "data-no-translate": "" }, b.folder)],
      ["Status", b => h("td", {}, h("div", {}, state(b)),
        b.archive && b.archive.verified ? h("div", { class: "ok small" }, h("strong", {}, "Archived and verified"), " ",
          h("span", { class: "mono", "data-no-translate": "" }, b.archive.dest)) : null,
        b.archive && b.archive.exists && !b.archive.verified ? h("div", { class: "warn small" }, "Archive not finished or not verified") : null)],
      ["", b => h("td", {}, [
        ["running", "stopped"].includes(b.status) && !b.restored_at ? h("button", { type: "button", class: "btn", disabled: busy,
          onclick: async () => { await post("/api/retire_resume", { id: b.id }); pollPlan(id); } }, "Finish setting aside") : null, " ",
        b.moved && !b.restored_at ? h("button", { type: "button", class: "btn", disabled: busy, onclick: async () => {
          if (!await ask(`Move ${b.moved} files back to where they were? Anything that has appeared at an original place since is left alone and reported.`, "Restore originals")) return;
          await post("/api/restore", { id: b.id }); pollPlan(id); } }, "Restore originals") : null, " ",
        b.moved && !b.restored_at ? h("button", { type: "button", class: "btn", disabled: busy, onclick: () => archiveDialog(b, id) }, "Archive originals…") : null, " ",
        b.moved && !b.restored_at ? h("button", { type: "button", class: "btn", disabled: busy, onclick: () => deleteDialog(b, id) }, "Delete originals…") : null])]]));
}

function retireResult(job, busy) {
  const r = job.result;
  if (busy || !r || !["retire", "retire_resume", "restore", "archive", "delete"].includes(job.kind)) return null;
  if (r.status === "error") return h("div", { class: "card banner" }, h("strong", {}, "Not done. "), r.abort_reason);
  if (job.kind === "archive") {
    return h("div", { class: "card banner" }, h("strong", {}, r.verified ? "Archived and verified. " : r.status === "stopped" ? "Stopped. " : "Not complete. "),
      `Files copied and checked: ${r.files} of ${r.planned} in `, h("span", { class: "mono", "data-no-translate": "" }, r.dest),
      (r.problems || []).length ? h("ul", { class: "small" }, r.problems.slice(0, 10).map(x => h("li", {}, h("span", { class: "mono", "data-no-translate": "" }, x.path.split("/").slice(-2).join("/")), " — ", x.reason))) : null);
  }
  if (job.kind === "delete") {
    const kept = r.kept || [];
    return h("div", { class: "card banner" }, h("strong", {}, r.status === "stopped" ? "Stopped. " : "Done. "), `Files deleted: ${r.deleted} (${fmtSize(r.bytes)})`,
      kept.length ? h("details", {}, h("summary", {}, h("strong", {}, "Albums that stay where they are "), h("span", { class: "badge b-conflict" }, kept.length)),
        h("ul", { class: "small" }, kept.slice(0, 50).map(a => reasonLi(a.label, a.reason)))) : null);
  }
  if (job.kind === "restore") {
    return h("div", { class: "card banner" }, h("strong", {}, `Restored files: ${r.restored}. `),
      (r.blocked || []).length ? h("div", {}, "Left where they are (something is at the original place now): ",
        h("span", { class: "mono", "data-no-translate": "" }, r.blocked.slice(0, 5).map(x => x.relpath).join(", "))) : null,
      (r.missing || []).length ? h("div", {}, `No longer in the originals folder: ${r.missing.length}`) : null);
  }
  const sk = r.skipped || [];
  return h("div", { class: "card banner" }, h("strong", {}, r.status === "stopped" ? "Stopped. " : "Done. "),
    r.batch ? [`Albums set aside: ${r.albums} (${fmtSize(r.bytes)}) in `, h("span", { class: "mono", "data-no-translate": "" }, r.folder)] : "Nothing was set aside.",
    sk.length ? h("details", {}, h("summary", {}, h("strong", {}, "Albums that stay where they are "),
      h("span", { class: "badge b-conflict" }, sk.length)),
      h("ul", { class: "small" }, sk.slice(0, 50).map(a => reasonLi(a.label, a.reason)))) : null);
}

function progressCard(job, ok) {
  const doneN = job.done || 0, total = job.total || ok.length;
  const bytes = ok.reduce((a, i) => a + (i.src_size || 0), 0);
  const doneB = job.kind === "apply" ? ok.slice(0, doneN).reduce((a, i) => a + (i.src_size || 0), 0) : 0;
  const frac = job.kind === "apply" && bytes ? doneB / bytes : (total ? doneN / total : 0);
  const el = (Date.now() / 1000) - (job.started || Date.now() / 1000);
  const eta = frac > 0.02 ? Math.max(0, el / frac - el) : null;
  const mins = x => x < 90 ? `${Math.round(x)} s` : `${Math.round(x / 60)} min`;
  return h("div", { class: "card" },
    h("div", { style: "display:flex;gap:12px;align-items:center;flex-wrap:wrap" },
      h("strong", {}, {apply: "Copying", resume: "Resuming", retry: "Retrying", retire: "Setting originals aside",
                       retire_resume: "Finishing setting originals aside", restore: "Restoring originals",
                       archive: "Archiving the originals", delete: "Deleting the originals"}[job.kind] || "Undoing"),
      h("span", { class: "muted small" }, `${doneN} / ${total} ${["retire", "retire_resume"].includes(job.kind) ? "albums" : "files"}` +
        (job.kind === "apply" && bytes ? ` · ${fmtSize(doneB)} of ${fmtSize(bytes)}` : "") +
        (el > 3 && doneB ? ` · ${fmtSize(doneB / el)}/s` : "") + (eta != null ? ` · about ${mins(eta)} left` : "")),
      ["apply", "resume", "retry", "retire", "retire_resume", "archive", "delete"].includes(job.kind) ? h("button", { type: "button", class: "btn", style: "margin-left:auto",
        onclick: async e => { e.target.disabled = true; e.target.textContent = "Stopping after this file…";
                              const r = await post("/api/stop", {}); if (r.error) tell(r.error); } }, "Stop") : null),
    h("div", { class: "pbar" }, h("div", { style: `width:${Math.round(frac * 100)}%` })),
    h("p", { class: "muted xsmall" }, "Stopping is safe: finished files stay, nothing half-written is left, and Resume continues."));
}

// ---- what the plan decided on your behalf (your naming rules), with the albums concerned
function doneForYou(s, dups) {
  const notes = s.notes || [];
  const groups = [
    ["artist_merged", "Artist names unified", n => `${n.label}: “${n.from}” → “${n.to}” (the album's own name)`],
    ["script", "Written in Simplified Chinese", n => `${n.from} → ${n.to}`],
    ["original_year", "Original release year used", n => `${n.label}: ${n.from} → ${n.to}`],
    ["kept_image", "Kept as one file (lossy audio is not cut)", n => `${n.label} (${n.from})`]];
  const dupUnits = [...new Map(dups.map(i => [i.unit_id, i])).values()];
  const rowsOf = (list, fmt) => h("ul", { class: "small" }, list.slice(0, 100).map(x => h("li", {}, fmt(x))),
    list.length > 100 ? h("li", { class: "muted" }, `+ ${list.length - 100} more`) : null);
  const parts = groups.map(([t, title, fmt]) => {
    const list = notes.filter(n => n.type === t);
    return list.length ? h("details", {}, h("summary", {}, h("strong", {}, `${title} `), h("span", { class: "badge b-unid" }, list.length)),
      rowsOf(list, fmt)) : null;
  }).filter(Boolean);
  if (dupUnits.length) parts.push(h("details", {}, h("summary", {}, h("strong", {}, "Duplicate albums copied once "),
    h("span", { class: "badge b-unid" }, dupUnits.length)), rowsOf(dupUnits, i => `${i.src_rel.split("/").slice(-2, -1)[0]} — ${i.note}`)));
  if (s.split_tracks) parts.push(h("div", { class: "small" }, h("strong", {}, "Cue-sheet albums split into tracks "),
    h("span", { class: "badge b-unid" }, s.split_tracks), h("span", { class: "muted" }, "  lossless FLAC files, the images unchanged")));
  if (s.album_cues) parts.push(h("div", { class: "small" }, h("strong", {}, "Album cue sheets written "),
    h("span", { class: "badge b-unid" }, s.album_cues), h("span", { class: "muted" }, "  for albums of separate track files")));
  return parts.length ? h("div", { class: "card" }, h("h2", { class: "h2" }, "Done for you ",
    h("span", { class: "muted small", style: "font-weight:400" }, "your naming rules, applied in this plan")), ...parts) : null;
}

// ---- the organized result as a folder tree: Artist / Album / files
function previewTree(list) {
  const tree = new Map();
  for (const i of list) {
    const parts = i.dest_rel.split("/"), file = parts.pop(), album = parts.pop() || "", artist = parts.join("/") || "";
    if (!tree.has(artist)) tree.set(artist, new Map());
    const a = tree.get(artist);
    if (!a.has(album)) a.set(album, []);
    a.get(album).push(file);
  }
  const artists = [...tree.keys()].sort((x, y) => x.localeCompare(y, "zh"));
  const nAlb = artists.reduce((n, a) => n + tree.get(a).size, 0);
  return h("div", { class: "card" }, h("h2", { class: "h2" }, "Result preview ",
      h("span", { class: "muted small", style: "font-weight:400" }, `${artists.length} artists · ${nAlb} albums — how the destination will look`)),
    h("div", { class: "tree" }, artists.map(a => h("details", {},
      h("summary", {}, h("span", { class: "strong" }, `📁 ${a}`), h("span", { class: "muted small" }, `  ${tree.get(a).size} album${tree.get(a).size > 1 ? "s" : ""}`)),
      [...tree.get(a).keys()].sort().map(al => h("details", { class: "tree-album" },
        h("summary", {}, `📁 ${al}`, h("span", { class: "muted small" }, `  ${tree.get(a).get(al).length} files`)),
        h("ul", { class: "mono small" }, tree.get(a).get(al).sort().map(f => h("li", {}, f)))))))));
}

function pollPlan(id) {
  clearTimeout(P.poll);
  const tick = async () => {
    const st = await api("/api/state");
    await renderPlan(id);
    if (st.scan && st.scan.running) P.poll = setTimeout(tick, 1000);
  };
  P.poll = setTimeout(tick, 300);
}

document.getElementById("planBtn").addEventListener("click", () => openPlans());
