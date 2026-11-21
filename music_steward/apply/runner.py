"""Apply a saved plan (copy -> tag the copy -> verify -> publish), resume, undo."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from .. import db as dbmod
from . import protocol
from .plan import load


def journal_dir() -> Path:
    return Path(dbmod.DEFAULT_DB).parent / "journal"


def apply(con: sqlite3.Connection, plan_id: int, progress=None, journal_root: Path | None = None, stop=None) -> dict:
    p = load(con, plan_id)
    dest = Path(p["dest_root"])
    dest.mkdir(parents=True, exist_ok=True)
    method = protocol.choose_method(dest)
    if method == "none":
        return {"error": f"{dest} supports no safe (no-overwrite) publish method"}
    run_key = uuid.uuid4().hex[:8]
    jpath = (journal_root or journal_dir()) / f"run-{run_key}.jsonl"
    j = protocol.Journal(jpath)
    rid = con.execute("INSERT INTO run(plan_id, run_key, journal, method, started_at) VALUES(?,?,?,?,?)",
                      (plan_id, run_key, str(jpath), method, time.time())).lastrowid
    con.commit()
    _journal_plan(j, run_key, p, dest, method)
    protocol.run_all(j, progress=progress, stop=stop)
    return finish(con, rid)


def _journal_plan(j: protocol.Journal, run_key: str, p: dict, dest: Path, method: str) -> int:
    """Journal an intent for every item of the plan, then mark the run as fully planned.

    The run row exists before this loop (so an interrupted run is visible and resumable); a crash
    inside the loop used to leave a journal with only some of the plan's files, which Resume then
    finished and reported as complete -- the rest silently missing. Idempotent: Resume calls it
    again for a run that is not marked planned and it adds only the missing intents."""
    have = set(j.ops())
    n = 0
    for it in p["items"]:
        if it["status"] != "ok":
            continue
        n += 1
        op = f"i{it['seq']}"
        if op not in have:
            protocol.plan_op(j, run_key, op, Path(it["src"]), dest / it["dest_rel"], it["kind"],
                             it["codec"], it["tags"], method, it["src_size"], it["src_mtime_ns"])
    j.write({"op": "_run", "state": "planned", "planned": n})
    return n


def finish(con, run_id) -> dict:
    r = con.execute("SELECT * FROM run WHERE id=?", (run_id,)).fetchone()
    jr = protocol.Journal(Path(r["journal"]))
    ops, j_meta = jr.ops(), jr.meta()
    states: dict[str, int] = {}
    for o in ops.values():
        states[o["history"][-1]] = states.get(o["history"][-1], 0) + 1
    planned = j_meta.get("planned")
    summary = {"run": r["id"], "method": r["method"], "journal": r["journal"], "states": states,
               "planned": planned,
               # every file of the plan has a journal entry; False = interrupted while planning (Resume)
               "complete_plan": planned is not None and planned == len(ops),
               "failed": [{"src": o["src"], "reason": o.get("reason")} for o in ops.values()
                          if o["history"][-1] in ("failed", "conflict", "aborted_source_changed",
                                                  "aborted_audio_changed")]}
    con.execute("UPDATE run SET finished_at=?, summary_json=? WHERE id=?", (time.time(), json.dumps(summary), run_id))
    con.commit()
    return summary


def resume(con, run_id, progress=None, stop=None) -> dict:
    r = con.execute("SELECT * FROM run WHERE id=?", (run_id,)).fetchone()
    j = protocol.Journal(Path(r["journal"]))
    if "planned" not in j.meta():           # interrupted while being planned: journal the rest first
        p = load(con, r["plan_id"])
        _journal_plan(j, r["run_key"], p, Path(p["dest_root"]), r["method"])
    decisions = protocol.recover(j)
    protocol.run_all(j, progress=progress, stop=stop)
    return {"recovered": decisions, **finish(con, run_id)}


def undo(con, run_id) -> dict:
    r = con.execute("SELECT * FROM run WHERE id=?", (run_id,)).fetchone()
    out = protocol.undo(protocol.Journal(Path(r["journal"])))
    con.execute("UPDATE run SET undone_at=? WHERE id=?", (time.time(), run_id))
    con.commit()
    actions: dict[str, int] = {}
    for o in out:
        actions[o["action"]] = actions.get(o["action"], 0) + 1
    return {"run": run_id, "actions": actions, "kept": [o for o in out if o["action"].startswith("kept")]}
