"""Command line: scan, hash, audit, inspect. Never writes to the collection."""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import audit, db, hashpass
from .hashing import HashError, audio_hash, file_sha256
from .paths import ext_of, nfc
from .probe import ProbeError, probe
from .scanner import RootUnavailable, resolve, scan
from .tags import read_tags


def _progress(label):
    def p(done, total):
        print(f"\r{label}: {done}/{total}", end="", file=sys.stderr, flush=True)
        if done == total:
            print(file=sys.stderr)
    return p


def _root_id(con, path):
    if not path:
        return None
    row = con.execute("SELECT id FROM root WHERE path=?", (nfc(os.path.abspath(path)),)).fetchone()
    if not row:
        sys.exit(f"not a scanned root: {path}")
    return row["id"]


def cmd_scan(a):
    con = db.connect(a.db)
    try:
        r = scan(con, a.path, workers=a.workers, progress=_progress("reading"),
                 reprobe_errors=a.reprobe_errors)
    except RootUnavailable as e:
        sys.exit(str(e))
    print(json.dumps({"status": r.status, "abort_reason": r.abort_reason,
                      "seconds": round(r.seconds, 1), **r.counts}, indent=2))
    if r.status != "complete":
        sys.exit(2)
    if a.hash:
        cmd_hash(argparse.Namespace(db=a.db, path=a.path, root=None, workers=a.hash_workers, limit=None))


def cmd_hash(a):
    con = db.connect(a.db)
    target = a.path or a.root
    root_id, sub = None, ""
    if target:
        root, sub = resolve(con, target)
        root_id = _root_id(con, root)
    print(json.dumps(hashpass.run(con, root_id, workers=a.workers, limit=a.limit, subpath=nfc(sub),
                                  progress=_progress("hashing")), indent=2))


def cmd_audit(a):
    con = db.connect(a.db)
    rid = _root_id(con, a.root)
    if a.filter:
        rows = audit.run(con, a.filter, rid)
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            for r in rows[: a.limit]:
                print(" | ".join(str(v) for v in r.values()))
            print(f"-- {len(rows)} {a.filter}", file=sys.stderr)
    else:
        print(json.dumps(audit.summary(con, rid), ensure_ascii=False, indent=2))


def cmd_inspect(a):
    p = a.file
    out = {"path": p, "size": os.path.getsize(p)}
    try:
        out["properties"] = probe(p).as_dict()
    except ProbeError as e:
        out["properties"] = {"error": str(e)}
    t = read_tags(p)
    out["tags"] = {"format": t.tag_format, "values": t.values, "issues": t.issues}
    if a.hash:
        out["file_sha256"] = file_sha256(p)
        try:
            out["audio_hash"], out["audio_hash_method"] = audio_hash(p, ext_of(p))
        except HashError as e:
            out["audio_hash_error"] = str(e)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def _target(con, path):
    root, sub = resolve(con, path)
    return _root_id(con, root), nfc(sub)


def cmd_recognize(a):
    from .recognize import queue, units
    con = db.connect(a.db)
    rid, sub = _target(con, a.path)
    us = units.plan_units(con, rid, sub)
    n_tracks = sum(1 for u in us if u["kind"] == "track")
    print(f"{len(us)} units ({len(us) - n_tracks} albums, {n_tracks} single tracks)", file=sys.stderr)
    if a.plan_only:
        for u in us[: a.limit]:
            print(f"{u['kind']:5} {len(u['file_ids']):3} files  {u['label']}  [{u['folder']}]")
        return
    print(json.dumps(units.enqueue(con, rid, us, requeue=a.requeue)), file=sys.stderr)
    if not os.environ.get("ACOUSTID_API_KEY"):
        print("note: ACOUSTID_API_KEY not set -- matching without fingerprints", file=sys.stderr)
    ids = queue.pending(con, rid, sub)[: a.limit]
    con.close()
    print(json.dumps(queue.run(a.db, ids, progress=_progress("recognizing")), indent=2))


def cmd_results(a):
    from .recognize import results
    con = db.connect(a.db)
    rid, sub = _target(con, a.path)
    for u in results.units(con, rid, sub)[: a.limit]:
        top = u["top"]
        line = (f"{u['status']:7} {u['confidence'] or '':12} {u['label'][:40]:40}  ->  "
                + (f"{top['album'] or ''} / {top['artist']} ({top['year'] or '?'}, {top['country'] or '?'})  "
                   f"d={top['distance']:.2f} fp={top['fingerprint_agrees']}/{u['n_files']}" if top else
                   (u.get("error") or "no candidates")))
        print(line)


def cmd_plan(a):
    from .apply import plan
    con = db.connect(a.db)
    rid, sub = _target(con, a.path)
    try:
        pid = plan.build(con, a.dest, rid, sub)
    except plan.PlanError as e:
        sys.exit(str(e))
    p = plan.load(con, pid)
    print(json.dumps({"plan": pid, "dest": p["dest_root"], **p["summary"]}, indent=2))
    for it in p["items"][: a.limit]:
        flag = "" if it["status"] == "ok" else f"   [{it['status']}: {it['note']}]"
        print(f"  {it['kind']:5} {os.path.relpath(it['src'], os.path.dirname(it['src'])) [:40]:40} -> {it['dest_rel']}{flag}")


def cmd_apply(a):
    from .apply import runner
    con = db.connect(a.db)
    print(json.dumps(runner.apply(con, a.plan, progress=_progress("applying")), indent=2, ensure_ascii=False))


def cmd_resume(a):
    from .apply import runner
    print(json.dumps(runner.resume(db.connect(a.db), a.run), indent=2, ensure_ascii=False))


def cmd_undo(a):
    from .apply import runner
    print(json.dumps(runner.undo(db.connect(a.db), a.run), indent=2, ensure_ascii=False))


def cmd_serve(a):
    from .server import serve
    serve(a.db, a.root, port=a.port, open_browser=not a.no_browser, dest=a.dest)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="music-steward")
    ap.add_argument("--db", default=str(db.DEFAULT_DB), help="app database (default %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="inventory a folder (read-only); a folder inside a known root "
                       "re-scans just that sub-folder")
    s.add_argument("path")
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--hash", action="store_true", help="also hash new/changed audio afterwards")
    s.add_argument("--reprobe-errors", action="store_true",
                   help="re-read files recorded as unreadable even if unchanged")
    s.add_argument("--hash-workers", type=int, default=4)
    s.set_defaults(func=cmd_scan)

    h = sub.add_parser("hash", help="compute missing whole-file and audio hashes (resumable)")
    h.add_argument("path", nargs="?", help="folder to hash (a root or any folder inside one); "
                   "default: everything not yet hashed")
    h.add_argument("--root", help=argparse.SUPPRESS)
    h.add_argument("--workers", type=int, default=4)
    h.add_argument("--limit", type=int)
    h.set_defaults(func=cmd_hash)

    au = sub.add_parser("audit", help="summary, or one filter: " + ", ".join(audit.FILTERS))
    au.add_argument("filter", nargs="?", choices=audit.FILTERS)
    au.add_argument("--root")
    au.add_argument("--json", action="store_true")
    au.add_argument("--limit", type=int, default=200)
    au.set_defaults(func=cmd_audit)

    i = sub.add_parser("inspect", help="show properties/tags/hashes of one file (no database)")
    i.add_argument("file")
    i.add_argument("--hash", action="store_true")
    i.set_defaults(func=cmd_inspect)

    rc = sub.add_parser("recognize", help="queue and match the albums in a folder (resumable)")
    rc.add_argument("path")
    rc.add_argument("--plan-only", action="store_true", help="only show the units that would be matched")
    rc.add_argument("--requeue", action="store_true", help="match again even if already done")
    rc.add_argument("--limit", type=int)
    rc.set_defaults(func=cmd_recognize)

    rs = sub.add_parser("results", help="recognition results for a folder")
    rs.add_argument("path")
    rs.add_argument("--limit", type=int)
    rs.set_defaults(func=cmd_results)

    pl = sub.add_parser("plan", help="build a plan from accepted albums in a folder (changes nothing)")
    pl.add_argument("path")
    pl.add_argument("--dest", required=True, help="destination folder for organized copies")
    pl.add_argument("--limit", type=int, default=40)
    pl.set_defaults(func=cmd_plan)

    apc = sub.add_parser("apply", help="apply a saved plan: copy, tag the copies, verify, publish")
    apc.add_argument("plan", type=int)
    apc.set_defaults(func=cmd_apply)

    rsm = sub.add_parser("resume", help="finish an interrupted run from its journal")
    rsm.add_argument("run", type=int)
    rsm.set_defaults(func=cmd_resume)

    und = sub.add_parser("undo", help="remove the copies a run created (unchanged ones only)")
    und.add_argument("run", type=int)
    und.set_defaults(func=cmd_undo)

    sv = sub.add_parser("serve", help="local web UI (127.0.0.1 only, token-protected)")
    sv.add_argument("--root", action="append", required=True, help="a folder the UI may browse (repeatable)")
    sv.add_argument("--port", type=int, default=0, help="default: a random free port")
    sv.add_argument("--no-browser", action="store_true")
    sv.add_argument("--dest", help="default destination folder for organized copies (outside every root)")
    sv.set_defaults(func=cmd_serve)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
