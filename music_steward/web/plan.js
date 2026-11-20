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
      h("div", { class: "field" }, h("label", { for: "destInput", class: "strong" }, "Destination"), input,
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
  const audio = ok.filter(i => i.kind === "audio"), side = items.filter(i => i.kind !== "audio");
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
    busy ? h("p", { class: "muted" }, job.total ? `${job.kind}: ${job.done} / ${job.total}…` : `${job.kind}…`) : null,
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
                        await post("/api/resume", { id: r.id }); pollPlan(id); } }, "Resume")])]])) : null,
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
