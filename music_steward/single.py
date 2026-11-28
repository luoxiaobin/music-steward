"""One Music Steward window (or `serve`) per database.

The owner started the Mac app twice and had two windows on one database: both could start a scan or a copy at the same
time. The first instance holds an exclusive lock on `<database>.lock` for as long as it runs. The operating system
drops the lock when the process ends, so a crash never leaves a stale lock behind. A second instance asks the first,
through a private local socket next to the database (only this user can open it, and the only message is "show"), to
bring its window to the front, then exits.

The command-line commands (scan, recognize, plan, ...) are not affected.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
from pathlib import Path
from typing import Callable

SHOW = b"show\n"


class Instance:
    def __init__(self, fh, sock_path: Path | None):
        self._fh = fh
        self._sock_path = sock_path
        self._server: socket.socket | None = None

    def listen(self, on_show: Callable[[], None]) -> None:
        """Call `on_show` when another instance is started (best effort: no socket, no focus)."""
        if self._sock_path is None or not hasattr(socket, "AF_UNIX"):
            return
        try:
            self._sock_path.unlink(missing_ok=True)          # left by a crash: the lock is ours, so it is stale
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            old = os.umask(0o177)                            # created 0600: only this user can connect
            try:
                s.bind(str(self._sock_path))
            finally:
                os.umask(old)
            s.listen(2)
        except OSError:
            return
        self._server = s

        def loop():
            while True:
                try:
                    c, _ = s.accept()
                except OSError:
                    return                                   # closed
                with c:
                    c.settimeout(2)
                    try:
                        if c.recv(16) == SHOW:
                            on_show()
                    except Exception:                        # noqa: BLE001 -- a focus request never stops the app
                        pass
        threading.Thread(target=loop, daemon=True).start()

    def close(self) -> None:
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            if self._sock_path is not None:
                self._sock_path.unlink(missing_ok=True)
        try:
            self._fh.close()                                 # releases the lock
        except OSError:
            pass


def _paths(db_path: str) -> tuple[Path, Path]:
    db = Path(db_path).expanduser().resolve()
    sock = db.with_name(db.name + ".sock")
    if len(os.fsencode(str(sock))) > 100:                  # macOS allows 104 bytes for a socket's path
        import hashlib
        import tempfile
        tag = hashlib.sha256(os.fsencode(str(db))).hexdigest()[:16]
        sock = Path(tempfile.gettempdir()) / f"music-steward-{os.getuid() if hasattr(os, 'getuid') else 0}-{tag}.sock"
    return db.with_name(db.name + ".lock"), sock


def acquire(db_path: str) -> Instance | None:
    """The lock for this database, or None when another window or `serve` already holds it."""
    lock, sock = _paths(db_path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return Instance(fh, sock)


def ask_to_show(db_path: str) -> bool:
    """Ask the running instance to bring its window to the front. True if it got the message."""
    _, sock = _paths(db_path)
    if not hasattr(socket, "AF_UNIX"):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(str(sock))
            s.sendall(SHOW)
        return True
    except OSError:
        return False
