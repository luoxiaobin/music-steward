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
from .scanner import RootUnavailable, scan
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
        cmd_hash(argparse.Namespace(db=a.db, root=a.path, workers=a.hash_workers, limit=None))


def cmd_hash(a):
    con = db.connect(a.db)
    print(json.dumps(hashpass.run(con, _root_id(con, a.root), workers=a.workers, limit=a.limit,
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


def cmd_serve(a):
    from .server import serve
    serve(a.db, a.root, port=a.port, open_browser=not a.no_browser)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mp3librarytool")
    ap.add_argument("--db", default=str(db.DEFAULT_DB), help="app database (default %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="inventory a folder (read-only)")
    s.add_argument("path")
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--hash", action="store_true", help="also hash new/changed audio afterwards")
    s.add_argument("--reprobe-errors", action="store_true",
                   help="re-read files recorded as unreadable even if unchanged")
    s.add_argument("--hash-workers", type=int, default=4)
    s.set_defaults(func=cmd_scan)

    h = sub.add_parser("hash", help="compute missing whole-file and audio hashes (resumable)")
    h.add_argument("--root")
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

    sv = sub.add_parser("serve", help="local web UI (127.0.0.1 only, token-protected)")
    sv.add_argument("--root", action="append", required=True, help="a folder the UI may browse (repeatable)")
    sv.add_argument("--port", type=int, default=0, help="default: a random free port")
    sv.add_argument("--no-browser", action="store_true")
    sv.set_defaults(func=cmd_serve)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
