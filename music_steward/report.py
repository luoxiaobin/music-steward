"""Where a collection folder stands: one read-only overview of the whole journey (scan → recognize → review → copy →
originals), with what is left to do. It reads the app database and the copy runs' journals; it never opens a file in
the collection folder or in the organized copy.

Each line is {key, n, text, go}: `text` is a fixed sentence with the number in it (the interface translates it), `go`
says where the owner acts on it ({"files": filter} | {"albums": filter} | {"plans": True}), or None.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import audit
from .apply import protocol

OK_STATES = ("committed", "already_existed")         # a copied file that is verified (as retire.py counts it)

TEXT = {
    # inventory
    "audio": "{n} audio files ({size})",
    "hashed": "{n} audio files not hashed yet (duplicate checks wait for them)",
    "unreadable": "{n} unreadable audio files",
    "garbled_tags": "{n} files with text decoding issues in their tags",
    "missing_metadata": "{n} files missing a title, artist or album tag",
    "wrong_extension": "{n} files whose extension does not match their content",
    "exact_duplicates": "{n} groups of exact duplicate files",
    "same_audio": "{n} groups with the same audio but different tags",
    "never_scanned": "This collection folder has not been scanned yet",
    # recognition and review
    "albums": "{n} albums and single tracks found",
    "not_recognized": "{n} not recognized yet",
    "rec_errors": "{n} could not be recognized (error)",
    "undecided": "{n} waiting for your decision",
    "ready_likely": "{n} of them Ready or Likely (can be accepted together)",
    "needs_review": "{n} of them need a closer look (Needs review)",
    "no_match": "{n} of them have no real match (No match or Weak)",
    "accepted": "{n} accepted",
    "skipped": "{n} skipped",
    "flagged": "{n} accepted, but their audio changed since: decide again",
    "not_found": "{n} not found (their files are gone)",
    # copies
    "copied": "{n} accepted albums copied and verified",
    "not_copied": "{n} accepted albums not copied yet",
    "copy_failed": "{n} albums with files that failed to copy (Retry failed)",
    "copy_unfinished": "{n} albums in a copy that was stopped or interrupted (Resume)",
    # originals
    "parked": "{n} albums' originals set aside in _ORIGINALS ({m} files)",
    "archived": "{n} batches of set-aside originals archived and verified",
    "deleted": "{n} set-aside original files deleted",
    "retire_off": "Retire originals is off for this collection folder",
}


# the same facts under "Next", where they stand alone ("213 of them have no real match" needs the line above it)
NEXT_TEXT = {
    "ready_likely": "{n} albums waiting for you are Ready or Likely (accept them together)",
    "needs_review": "{n} albums waiting for you need a closer look (Needs review)",
    "no_match": "{n} albums waiting for you have no real match (No match or Weak)",
}


def _size(b: int) -> str:
    for unit, f in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6)):
        if b >= f:
            return f"{b / f:.1f} {unit}"
    return f"{round(b / 1e3)} KB"


def _line(key: str, n: int | None = None, go: dict | None = None, **extra) -> dict:
    t = TEXT[key]
    if n is not None:
        t = t.replace("{n}", f"{n:,}")
    for k, v in extra.items():
        t = t.replace("{" + k + "}", f"{v:,}" if isinstance(v, int) else str(v))
    return {"key": key, "n": n, "text": t, "go": go}


def _copy_state(con: sqlite3.Connection, root_id: int) -> dict[int, str]:
    """{unit id: copied | failed | unfinished} from every copy run of this folder that was not undone. An album is
    copied when, in some run, every one of its planned files is committed (or already there, verified)."""
    best: dict[int, str] = {}
    rank = {"unfinished": 1, "failed": 2, "copied": 3}
    for run in con.execute("SELECT run.id, run.plan_id, run.journal FROM run JOIN plan p ON p.id=run.plan_id "
                           "WHERE p.root_id=? AND run.undone_at IS NULL", (root_id,)).fetchall():
        try:
            ops = protocol.Journal(Path(run["journal"])).ops()
        except OSError:
            continue
        per_unit: dict[int, list[str]] = {}
        for it in con.execute("SELECT seq, unit_id FROM plan_item WHERE plan_id=? AND status='ok' AND unit_id IS NOT NULL",
                              (run["plan_id"],)):
            o = ops.get(f"i{it['seq']}")
            per_unit.setdefault(it["unit_id"], []).append(o["history"][-1] if o else "intent")
        for uid, states in per_unit.items():
            s = ("copied" if all(x in OK_STATES for x in states) else
                 "unfinished" if any(x not in protocol.TERMINAL for x in states) else "failed")
            if rank[s] > rank.get(best.get(uid), 0):
                best[uid] = s
    return best


def overview(con: sqlite3.Connection, root_id: int, inventory: dict | None = None) -> dict:
    """{root, sections: [{title, lines}], next: [line]}. `inventory`: audit.summary, if the caller has it cached."""
    from . import settings
    from .recognize import results
    root = con.execute("SELECT path FROM root WHERE id=?", (root_id,)).fetchone()
    scanned = con.execute("SELECT 1 FROM scan WHERE root_id=? AND status='complete' LIMIT 1", (root_id,)).fetchone()
    inv = inventory or audit.summary(con, root_id)
    f = inv.get("filters", {})
    lines_inv = []
    if not scanned:
        lines_inv.append(_line("never_scanned"))
    else:
        lines_inv.append(_line("audio", inv.get("audio") or 0, None, size=_size(inv.get("bytes") or 0)))
        unhashed = (inv.get("audio") or 0) - (inv.get("hashed") or 0)
        if unhashed:
            lines_inv.append(_line("hashed", unhashed))
        for k in ("unreadable", "garbled_tags", "missing_metadata", "wrong_extension", "exact_duplicates", "same_audio"):
            if f.get(k):
                lines_inv.append(_line(k, f[k], {"files": k}))

    us = results.units(con, root_id)
    copy = _copy_state(con, root_id)
    here = [u for u in us if u["presence"] == "here"]
    done = [u for u in here if u["status"] == "done"]
    und = [u for u in done if not u["decision"]]
    acc = [u for u in here if u["decision"] and u["decision"]["action"] == "accept"]
    flagged = [u for u in acc if u["decision"].get("stale_reason")]
    lines_rec = [_line("albums", len(us), {"albums": "all"})] if us else []
    c = lambda pred, xs: sum(1 for x in xs if pred(x))      # noqa: E731
    for key, n, go in (
            ("not_recognized", c(lambda u: u["status"] in ("queued", "running"), here), None),
            ("rec_errors", c(lambda u: u["status"] == "error", here), None),
            ("undecided", len(und), {"albums": "undecided"}),
            ("ready_likely", c(lambda u: u["confidence"] in ("ready", "likely"), und), {"albums": "ready_likely"}),
            ("needs_review", c(lambda u: u["confidence"] == "needs_review", und), {"albums": "needs_review"}),
            ("no_match", c(lambda u: u["confidence"] in ("no_match", "weak"), und), {"albums": "no_match_weak"}),
            ("accepted", len(acc), {"albums": "accept"}),
            ("skipped", c(lambda u: u["decision"] and u["decision"]["action"] == "skip", here), {"albums": "skip"}),
            ("flagged", len(flagged), {"albums": "accept"}),
            ("not_found", c(lambda u: u["presence"] == "not_found", us), {"albums": "not_found"})):
        if n or key in ("undecided", "accepted"):
            if us:
                lines_rec.append(_line(key, n, go))

    ok_acc = [u for u in acc if not u["decision"].get("stale_reason")]
    n_copied = c(lambda u: copy.get(u["id"]) == "copied", ok_acc)
    n_failed = c(lambda u: copy.get(u["id"]) == "failed", ok_acc)
    n_unfin = c(lambda u: copy.get(u["id"]) == "unfinished", ok_acc)
    n_not = c(lambda u: u["id"] not in copy, ok_acc)
    lines_copy = [_line(k, n, {"plans": True} if k != "not_copied" else {"albums": "accept"})
                  for k, n in (("copied", n_copied), ("not_copied", n_not), ("copy_failed", n_failed),
                               ("copy_unfinished", n_unfin)) if n or k in ("copied", "not_copied")] if acc else []

    parked = con.execute("SELECT count(DISTINCT rf.unit_id), count(*) FROM retired_file rf JOIN retire_batch b "
                         "ON b.id=rf.batch_id WHERE b.root_id=? AND rf.state='moved'", (root_id,)).fetchone()
    deleted = con.execute("SELECT count(*) FROM retired_file rf JOIN retire_batch b ON b.id=rf.batch_id "
                          "WHERE b.root_id=? AND rf.state='deleted'", (root_id,)).fetchone()[0]
    archived = sum(1 for (a,) in con.execute("SELECT archive_json FROM retire_batch WHERE root_id=?", (root_id,))
                   if a and json.loads(a).get("verified"))
    lines_orig = []
    if parked[1]:
        lines_orig.append(_line("parked", parked[0], {"plans": True}, m=parked[1]))
    if archived:
        lines_orig.append(_line("archived", archived, {"plans": True}))
    if deleted:
        lines_orig.append(_line("deleted", deleted, {"plans": True}))
    if not settings.retire_allowed(con, root_id) and (parked[1] or n_copied):
        lines_orig.append(_line("retire_off"))

    # what to do next, in the order of the journey
    nxt = []
    by_key = {ln["key"]: ln for sec in (lines_inv, lines_rec, lines_copy) for ln in sec}
    for key in ("never_scanned", "rec_errors", "copy_unfinished", "copy_failed", "flagged", "ready_likely",
                "needs_review", "no_match", "not_copied"):
        if key in by_key and (by_key[key]["n"] is None or by_key[key]["n"]):
            ln = dict(by_key[key])
            if key in NEXT_TEXT:
                ln["text"] = NEXT_TEXT[key].replace("{n}", f"{ln['n']:,}")
            nxt.append(ln)
    if scanned and not us:
        nxt.append({"key": "recognize", "n": None, "text": "Recognize the collection folder's albums (Recognize folder)",
                    "go": None})
    return {"root_id": root_id, "root": root["path"] if root else None,
            "sections": [{"title": "Files", "lines": lines_inv}, {"title": "Albums", "lines": lines_rec},
                         {"title": "Organized copies", "lines": lines_copy}, {"title": "Originals", "lines": lines_orig}],
            "next": nxt}


def as_text(o: dict) -> str:
    out = [f"Music Steward — {o['root']}", ""]
    for s in o["sections"]:
        if not s["lines"]:
            continue
        out.append(s["title"])
        out += [f"  {ln['text']}" for ln in s["lines"]]
        out.append("")
    if o["next"]:
        out.append("Next")
        out += [f"  • {ln['text']}" for ln in o["next"]]
    return "\n".join(out).rstrip() + "\n"
