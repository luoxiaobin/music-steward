"""A quick check that a build of the app is complete: run by CI on the packaged macOS app (`Music Steward --self-test`)
and by the tests on a normal install. It uses a throwaway database and folder; it reads and writes nothing of the
owner's. Prints one line per check, returns 0 when every required check passed.

What a packaged app most often lacks is something loaded by name at run time: beets' metadata plugins, beets' default
configuration, OpenCC's dictionaries, the web page, and the way a recognition worker is started.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import tempfile
import threading
from http.client import HTTPConnection
from pathlib import Path


def _plugins() -> str:
    for name in ("musicbrainz", "deezer", "discogs", "chroma"):
        importlib.import_module(f"beetsplug.{name}")
    import beets
    if not (Path(beets.__file__).parent / "config_default.yaml").is_file():
        raise RuntimeError("beets' config_default.yaml is missing")
    return "musicbrainz, deezer, discogs, chroma"


def _chinese() -> str:
    from .chinese import to_simplified
    got = to_simplified("鄧麗君")
    if got != "邓丽君":
        raise RuntimeError(f"Traditional → Simplified gave {got!r}")
    return got


def _web() -> str:
    web = Path(__file__).parent / "web"
    need = ["index.html", "app.js", "plan.js", "i18n.js", "dialog.js", "app.css", "icons/icon-512.png"]
    missing = [n for n in need if not (web / n).is_file()]
    if missing:
        raise RuntimeError(f"missing: {', '.join(missing)}")
    return f"{len(need)} files"


def _server(tmp: str) -> str:
    from http.server import ThreadingHTTPServer
    from .server import App, make_handler
    app = App(os.path.join(tmp, "selftest.sqlite"), [])
    ref = {"port": 0}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, ref))
    ref["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        c = HTTPConnection("127.0.0.1", ref["port"], timeout=10)
        c.request("GET", f"/?token={app.token}", headers={"Host": f"127.0.0.1:{ref['port']}"})
        r = c.getresponse()
        r.read()
        cookie = (r.getheader("Set-Cookie") or "").split(";")[0]
        if r.status != 303 or not cookie:
            raise RuntimeError(f"sign-in answered {r.status}")
        c.request("GET", "/", headers={"Host": f"127.0.0.1:{ref['port']}", "Cookie": cookie})
        r = c.getresponse()
        body = r.read().decode(errors="replace")
        if r.status != 200 or "Music Steward" not in body:
            raise RuntimeError(f"the page answered {r.status}")
        c.request("GET", "/api/state", headers={"Host": f"127.0.0.1:{ref['port']}", "Cookie": cookie})
        st = json.loads(c.getresponse().read())
        if st.get("roots") != []:
            raise RuntimeError("a fresh database should have no collection folder")
    finally:
        httpd.shutdown()
        httpd.server_close()
    return "page and API answer"


def _worker() -> str:
    from .environment import worker_cmd
    r = subprocess.run(worker_cmd(["--help"]), capture_output=True, timeout=120)
    if r.returncode != 0 or b"--unit" not in r.stdout:
        raise RuntimeError(f"exit {r.returncode}: {r.stderr.decode(errors='replace')[-300:]}")
    return "starts"


def run() -> int:
    from .environment import ffmpeg_found
    from . import __version__
    checks = [("beets plugins", _plugins), ("Chinese conversion", _chinese), ("web page", _web),
              ("recognition worker", _worker)]
    ok = True
    print(f"Music Steward {__version__} self-test")
    with tempfile.TemporaryDirectory() as tmp:
        checks.append(("local server", lambda: _server(tmp)))
        for name, fn in checks:
            try:
                print(f"  ok    {name}: {fn()}")
            except Exception as e:      # noqa: BLE001 -- every failure is reported, then the exit code says it
                ok = False
                print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"  {'ok  ' if ffmpeg_found() else 'note'}  FFmpeg: {'found' if ffmpeg_found() else 'not found (the app says how to install it)'}")
    import shutil
    fp = shutil.which("fpcalc")
    print(f"  {'ok  ' if fp else 'note'}  fpcalc (Chromaprint): {'found' if fp else 'not found -- fingerprints need it (brew install chromaprint)'}")
    return 0 if ok else 1
