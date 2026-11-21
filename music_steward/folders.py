"""Is this a folder Music Steward can use? One answer, in plain words, for every place a folder is typed.

The check says (1) whether what was typed has the right FORM -- a full path on this Mac, not a network
address like //192.168.50.29/music/Ape.Flac or smb://nas/music, which no program can open directly: macOS
shows a connected share as a folder under /Volumes -- (2) whether the folder EXISTS (and if not, whether it
could be created, or whether the drive is simply not connected), (3) whether the app may read and write
there, and (4) for a place where files are written, that it is not inside the collection.

Messages are fixed sentences (so the interface can translate them); the changing part -- a folder name, a
path -- comes separately in `detail`. Nothing here writes anything.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

VOLUMES = "/Volumes"            # where macOS mounts drives and shares (tests point it elsewhere)

MESSAGES = {
    "empty": "",
    "ok": "The folder exists and can be used.",
    "will_be_created": "This folder does not exist yet. It will be created when it is first needed.",
    "network_address": "That is a network address, which this app cannot open directly. Connect to the share first "
                       "(Finder → Go → Connect to Server), then use the folder as it appears under /Volumes.",
    "network_address_mounted": "That is a network address. The same share is mounted on this Mac; use this path instead:",
    "web_address": "That is a web address, not a folder on this Mac.",
    "not_absolute": "Use the full path, starting with / (or ~ for your home folder).",
    "not_mounted": "The drive or share is not connected to this Mac:",
    "parent_missing": "This folder does not exist, and neither does the folder that should contain it. "
                      "Check the spelling. The closest existing folder is:",
    "is_file": "That is a file, not a folder.",
    "overlaps": "This folder is inside, or contains, your collection folder. Choose a folder outside it. Collection folder:",
    "not_readable": "Music Steward is not allowed to read this folder.",
    "not_writable": "Music Steward is not allowed to write to this folder (or to the folder it would be created in).",
}
LEVEL = {"empty": "empty", "ok": "ok", "will_be_created": "warn", "network_address_mounted": "error"}   # others: error

_URL = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*)://(?:[^/@]*@)?([^/]+)/*(.*)$")
_UNC = re.compile(r"^(?:\\\\|//)(?:[^/\\@]*@)?([^/\\]+)[/\\]+([^/\\]+)[/\\]*(.*)$")
_MOUNT = re.compile(r"^//(?:[^@/ ]+@)?([^/ ]+)/(.+?) on (/.+?) (?:\(|type )")
_NET_SCHEMES = {"smb", "cifs", "afp", "nfs", "ftp", "sftp", "webdav", "dav"}


def _mounted_shares() -> list[tuple[str, str, str]]:
    """[(host, share, mount point)] of the network shares mounted on this Mac (user names left out)."""
    try:
        out = subprocess.run(["mount"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        m = _MOUNT.match(line)
        if m:
            found.append((m.group(1).lower(), unquote(m.group(2)).lower(), m.group(3)))
    return found


def _result(code: str, detail: str | None = None, **extra) -> dict:
    level = LEVEL.get(code, "error")
    return {"ok": level in ("ok", "warn", "empty"), "level": level, "code": code, "message": MESSAGES[code],
            "detail": detail, "resolved": None, "exists": False, "is_dir": False, "free": None,
            "suggestion": None, **extra}


def _network_address(text: str) -> tuple[str, str, str] | None:
    """(host, share, rest) when `text` is a network address, else None. '//Users/me' is a local path."""
    m = _URL.match(text)
    if m and m.group(1).lower() in _NET_SCHEMES:
        host, tail = m.group(2), m.group(3)
        share, _, rest = tail.partition("/")
        return host, unquote(share), unquote(rest)
    m = _UNC.match(text)
    if m and not os.path.isdir("/" + m.group(1)):                 # //Users/... is a perfectly good local path
        return m.group(1), m.group(2), m.group(3).replace("\\", "/")
    return None


def _nearest_existing(p: Path) -> Path:
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def check_folder(con, text: str, purpose: str = "destination") -> dict:
    """Check a typed folder. purpose: 'destination' / 'archive' (files will be written there, outside the
    collection) or 'any' (just a folder to read)."""
    text = (text or "").strip().strip("\"'").strip()
    if not text:
        return _result("empty")
    m = _URL.match(text)
    if m and m.group(1).lower() not in _NET_SCHEMES and m.group(1).lower() != "file":
        return _result("web_address")
    net = _network_address(text)
    if net:
        host, share, rest = net
        for h, s, mountpoint in _mounted_shares():
            if h == host.lower() and s == share.lower():
                return _result("network_address_mounted", suggestion=str(Path(mountpoint) / rest) if rest else mountpoint)
        return _result("network_address")
    if text.lower().startswith("file://"):
        text = unquote(text[7:])
    path = os.path.expanduser(text)
    if not os.path.isabs(path):
        return _result("not_absolute")
    p = Path(os.path.abspath(path))
    resolved = str(Path(os.path.realpath(p)))
    base = {"resolved": resolved, "exists": p.exists(), "is_dir": p.is_dir()}
    vol = Path(VOLUMES)
    if vol in p.parents and not (vol / p.relative_to(vol).parts[0]).exists():
        return {**_result("not_mounted", p.relative_to(vol).parts[0]), **base}
    if p.exists() and not p.is_dir():
        return {**_result("is_file"), **base}
    writes = purpose in ("destination", "archive")
    if writes and con is not None:
        for (root,) in con.execute("SELECT path FROM root"):
            rr = os.path.realpath(root)
            if resolved == rr or resolved.startswith(rr.rstrip("/") + "/") or rr.startswith(resolved.rstrip("/") + "/"):
                return {**_result("overlaps", root), **base}
    anchor = _nearest_existing(p)
    free = None
    try:
        free = shutil.disk_usage(anchor).free
    except OSError:
        pass
    if p.exists():
        if not os.access(p, os.R_OK | os.X_OK):
            return {**_result("not_readable"), **base, "free": free}
        if writes and not os.access(p, os.W_OK):
            return {**_result("not_writable"), **base, "free": free}
        return {**_result("ok"), **base, "free": free}
    if anchor == p.parent:                                          # only the last folder is missing: it can be made
        if writes and not os.access(anchor, os.W_OK | os.X_OK):
            return {**_result("not_writable"), **base, "free": free}
        return {**_result("will_be_created"), **base, "free": free}
    return {**_result("parent_missing", str(anchor)), **base, "free": free}


def explain(result: dict) -> str:
    """The message and its detail as one line (for a plain error text)."""
    return result["message"] + (f" {result['detail']}" if result.get("detail") else "") + \
        (f" {result['suggestion']}" if result.get("suggestion") else "")
