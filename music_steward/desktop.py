"""Music Steward in its own desktop window (ADR 0001): the same local server and web UI, shown in
a native window by pywebview (system WebKit on macOS, WebView2 on Windows).

One process: the HTTP server runs in a thread on 127.0.0.1 with a random port; the window opens
it already signed in (one-time token). The page can ask for a native folder picker through
`window.pywebview.api.choose_folder()`. Closing the window while a job runs asks first; a stopped
job is safe and can be resumed.
"""
from __future__ import annotations

import os
import threading
from http.server import ThreadingHTTPServer


class Api:
    """Exposed to the page as window.pywebview.api.* (only what the UI needs)."""

    def __init__(self):
        self.window = None

    def choose_folder(self, start: str = "") -> str | None:
        import webview
        start = start if start and os.path.isdir(start) else os.path.expanduser("~")
        r = self.window.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
        return r[0] if r else None


def run(db_path: str, roots: list[str], dest: str | None = None, debug: bool = False) -> None:
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
        webview.start(debug=debug, private_mode=False, storage_path=str(APP_DIR / "webview"))
    finally:
        httpd.shutdown()
        httpd.server_close()
