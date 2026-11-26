"""What the app needs from the computer when it is not started from a terminal (the packaged macOS app).

An app opened from the Finder gets a bare environment: PATH is /usr/bin:/bin:/usr/sbin:/sbin (no Homebrew, so no
ffmpeg/ffprobe), and nothing from ~/.zshenv (so no ACOUSTID_API_KEY or DISCOGS_TOKEN). This module

  * adds the usual places of command-line tools to PATH (only those that exist, after what is already there);
  * reads a short, fixed list of variables -- the keys and the MLT_* settings, nothing else -- from the user's login
    shell, once, into this process's environment. They stay where the owner keeps them (~/.zshenv): they are never
    written anywhere, logged or shown (README: "Keys stay in your environment");
  * tells whether FFmpeg can be found, so the window can say how to install it instead of failing later;
  * builds the command that starts a recognition worker, which differs inside the packaged app (no `python -m`).

Nothing here runs unless asked: the packaged app's entry (packaging/macos/app_main.py) calls `prepare()`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

TOOL_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin")      # Homebrew (Apple silicon, Intel), MacPorts
KEYS = ("ACOUSTID_API_KEY", "DISCOGS_TOKEN")
PREFIXES = ("MLT_",)
WORKER_FLAG = "--recognize-worker"


def frozen() -> bool:
    """Running inside the packaged app (PyInstaller sets sys.frozen)."""
    return bool(getattr(sys, "frozen", False))


def bundled_bin() -> str | None:
    """A bin/ folder shipped inside the app, if a build puts tools there."""
    base = getattr(sys, "_MEIPASS", None)
    p = os.path.join(base, "bin") if base else None
    return p if p and os.path.isdir(p) else None


def fix_path(env: dict | None = None) -> list[str]:
    """Append the usual tool folders that exist and are missing (a bundled bin/ goes first). Returns what was added."""
    env = os.environ if env is None else env
    have = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    added = []
    b = bundled_bin()
    if b and b not in have:
        have.insert(0, b)
        added.append(b)
    for d in TOOL_DIRS:
        if d not in have and os.path.isdir(d):
            have.append(d)
            added.append(d)
    env["PATH"] = os.pathsep.join(have)
    return added


def _wanted(name: str) -> bool:
    return name in KEYS or name.startswith(PREFIXES)


def login_env(shell: str | None = None, timeout: float = 5.0) -> dict[str, str]:
    """The wanted variables as the user's login shell sets them. {} when the shell cannot be asked in time."""
    shell = shell or os.environ.get("SHELL") or "/bin/zsh"
    try:
        r = subprocess.run([shell, "-l", "-c", "env -0"], capture_output=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return {}
    out = {}
    for item in r.stdout.split(b"\0"):
        name, sep, value = item.partition(b"=")
        try:
            name_s = name.decode().strip()
        except UnicodeDecodeError:
            continue
        if sep and _wanted(name_s):
            out[name_s] = value.decode(errors="replace")
    return out


def import_login_env(env: dict | None = None, shell: str | None = None) -> list[str]:
    """Copy the wanted variables from the login shell, never overriding one already set. Returns their NAMES only."""
    env = os.environ if env is None else env
    names = []
    for k, v in login_env(shell).items():
        if k not in env:
            env[k] = v
            names.append(k)
    return names


def ffmpeg_found() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def worker_cmd(args: list[str]) -> list[str]:
    """How to start one recognition worker (a fresh process per album, recognize/queue.py)."""
    if frozen():
        return [sys.executable, WORKER_FLAG, *args]
    return [sys.executable, "-I", "-m", "music_steward.recognize.worker", *args]


def prepare() -> dict:
    """Everything the packaged app needs before it opens its window. Names only in the result, never values."""
    return {"path_added": fix_path(), "env_imported": import_login_env(), "ffmpeg": ffmpeg_found()}
