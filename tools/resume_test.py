"""Resumability test at scale: recognize a sample of a big folder, interrupt it, resume, check.

    python tools/resume_test.py pick  --folder 音乐Ape --every 5   # queue every 5th album
    python tools/resume_test.py run                               # match the chosen ones still pending
    python tools/resume_test.py snap NAME                         # remember each unit's state
    python tools/resume_test.py report [--since NAME]             # counts; what was redone since NAME

`run` uses the same queue as `music-steward recognize` and the web app, in its own process
group, so `kill -INT -- -PID` is a real Ctrl-C (the worker gets it too) and `kill -KILL -- -PID`
is a crash / power loss / force-quit of the app. State lives next to the app DB
(resume_test.json); nothing here writes to the collection.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from music_steward import db  # noqa: E402
from music_steward.recognize import queue, units  # noqa: E402

DB = os.environ.get("MLT_DB") or str(db.DEFAULT_DB)
STATE = Path(DB).with_name("resume_test.json")
ROOT = "/Volumes/music"


def load() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(st: dict):
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1))


def rows(con, ids):
    return {r["id"]: dict(r) for r in con.execute(
        f"SELECT id, status, started_at, finished_at, error FROM unit WHERE id IN ({','.join('?' * len(ids))})", ids)}


def cmd_pick(a):
    con = db.connect(DB)
    rid = con.execute("SELECT id FROM root WHERE path=?", (ROOT,)).fetchone()["id"]
    us = units.plan_units(con, rid, a.folder)[:: a.every]
    print(json.dumps(units.enqueue(con, rid, us)))          # no scope: nothing else is touched
    ids = [con.execute("SELECT id FROM unit WHERE root_id=? AND key=?", (rid, u["key"])).fetchone()["id"] for u in us]
    save({"ids": ids, "folder": a.folder, "every": a.every, "snaps": {}})
    print(f"{len(ids)} units chosen")


def cmd_run(a):
    os.setsid() if os.getpid() != os.getpgrp() else None    # own process group (see doc)
    ids = set(load()["ids"])
    con = db.connect(DB)
    todo = [i for i in queue.pending(con) if i in ids]
    con.close()
    print(f"pid {os.getpid()} pgid {os.getpgrp()}: {len(todo)} pending", flush=True)

    def progress(i, n):
        if i % 10 == 0 or i == n:
            print(f"{time.strftime('%H:%M:%S')} {i}/{n}", flush=True)
    print(json.dumps(queue.run(DB, todo, progress=progress)), flush=True)


def cmd_snap(a):
    st = load()
    con = db.connect(DB)
    st["snaps"][a.name] = {"at": time.time(), "units": rows(con, st["ids"])}
    save(st)
    print(f"snapshot {a.name}: {len(st['ids'])} units")


def cmd_report(a):
    st = load()
    con = db.connect(DB)
    now = rows(con, st["ids"])
    by = {}
    for r in now.values():
        by[r["status"]] = by.get(r["status"], 0) + 1
    print(f"{len(now)} units: {by}")
    errs = [r for r in now.values() if r["status"] == "error"]
    for r in errs[:10]:
        print("  error", r["id"], (r["error"] or "").splitlines()[0][:160])
    if a.since:
        snap = st["snaps"][a.since]["units"]
        redone = [i for i, r in now.items() if snap[str(i)]["status"] == "done"
                  and r["finished_at"] != snap[str(i)]["finished_at"]]
        resumed = [i for i, r in now.items() if snap[str(i)]["status"] in ("queued", "running")
                   and r["status"] == "done"]
        was_running = [i for i in now if snap[str(i)]["status"] == "running"]
        print(f"since {a.since}: redone {len(redone)} (must be 0); finished since {len(resumed)}; "
              f"left 'running' at the snapshot {was_running} -> now {[now[i]['status'] for i in was_running]}")
        no_cands = [i for i in was_running if now[i]["status"] == "done" and not con.execute(
            "SELECT 1 FROM candidate WHERE unit_id=?", (i,)).fetchone()]
        print(f"  interrupted units that finished with no candidates at all: {no_cands}")
    stuck = [i for i, r in now.items() if r["status"] in ("queued", "running")]
    print(f"still queued/running: {len(stuck)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("pick")
    p.add_argument("--folder", required=True)
    p.add_argument("--every", type=int, default=5)
    p.set_defaults(f=cmd_pick)
    sp.add_parser("run").set_defaults(f=cmd_run)
    s = sp.add_parser("snap")
    s.add_argument("name")
    s.set_defaults(f=cmd_snap)
    r = sp.add_parser("report")
    r.add_argument("--since")
    r.set_defaults(f=cmd_report)
    a = ap.parse_args()
    a.f(a)
