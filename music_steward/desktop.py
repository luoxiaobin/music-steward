"""Music Steward in its own desktop window (ADR 0001): the same local server and web UI, shown in
a native window by pywebview (system WebKit on macOS, WebView2 on Windows).

One process: the HTTP server runs in a thread on 127.0.0.1 with a random port; the window opens
it already signed in (one-time token). The page can ask for a native folder picker through
`window.pywebview.api.choose_folder()`. Closing the window while a job runs asks first; a stopped
job is safe and can be resumed.
"""
from __future__ import annotations

import os
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

ICON = Path(__file__).parent / "web/icons/icon-512.png"     # made by tools/make_icons.py


def _dock_icon() -> None:
    """macOS: show the app's icon in the Dock and the app switcher (a plain Python process would
    show the Python rocket). Best effort -- the window works without it."""
    if sys.platform != "darwin" or not ICON.exists():
        return
    try:
        from AppKit import NSApplication, NSImage     # pyobjc, installed with pywebview on macOS
        image = NSImage.alloc().initWithContentsOfFile_(str(ICON))
        if image is not None:
            NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception:       # noqa: BLE001 -- an icon is never worth failing the window for
        pass


class Api:
    """Exposed to the page as window.pywebview.api.* (only what the UI needs)."""

    def __init__(self):
        self.window = None

    def choose_folder(self, start: str = "") -> str | None:
        import webview
        start = start if start and os.path.isdir(start) else os.path.expanduser("~")
        r = self.window.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
        return r[0] if r else None


def _to_front(window) -> None:
    """Bring the window forward when the app is started again (a second launch only asks, then exits)."""
    try:
        window.restore()
        window.show()
    except Exception:       # noqa: BLE001 -- best effort
        pass
    if sys.platform == "darwin":
        try:
            from AppKit import NSApplication
            from PyObjCTools import AppHelper
            AppHelper.callAfter(NSApplication.sharedApplication().activateIgnoringOtherApps_, True)
        except Exception:   # noqa: BLE001
            pass


def run(db_path: str, roots: list[str], dest: str | None = None, debug: bool = False) -> None:
    from . import single
    me = single.acquire(db_path)
    if me is None:          # one window per database: two would both start scans and copies (owner, 2026-11-28)
        shown = single.ask_to_show(db_path)
        print("Music Steward is already open" + ("; its window was brought to the front." if shown else
              " (a window or `music-steward serve`)."), flush=True)
        return
    try:
        _run(db_path, roots, dest, debug, me)
    finally:
        me.close()


def _run(db_path: str, roots: list[str], dest: str | None, debug: bool, me) -> None:
    import webview
    from .server import App, make_handler

    app = App(db_path, roots, dest)
    port_ref = {"port": 0}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, port_ref))
    port_ref["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port_ref['port']}/?token={app.token}"
    print(f"Music Steward window: server on 127.0.0.1:{port_ref['port']}", flush=True)

    api = Api()
    window = webview.create_window("Music Steward", url, js_api=api, width=1440, height=900,
                                   min_size=(960, 600), text_select=True)
    api.window = window
    me.listen(lambda: _to_front(window))

    def closing():
        job = app.scan_state
        if job.get("running"):
            ok = window.create_confirmation_dialog(
                "Quit Music Steward?",
                f"A {job.get('kind')} is still running. Quitting stops it now; nothing half-written is "
                "kept, and you can resume it next time.")
            if not ok:
                return False
            app._stop.set()
        return True

    window.events.closing += closing
    try:
        # not private: the page remembers per-viewer conveniences (panel sizes, hidden columns)
        from .db import APP_DIR
        _dock_icon()
        webview.start(debug=debug, private_mode=False, storage_path=str(APP_DIR / "webview"))
    finally:
        httpd.shutdown()
        httpd.server_close()
