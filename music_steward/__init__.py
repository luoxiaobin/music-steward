"""Music Steward (formerly MP3LibraryTool) — scan, recognize, review, plan and apply changes to a music collection.

Sources are only ever read; changes go to organized copies, from a saved plan, with a journal.
"""
try:
    from importlib.metadata import version as _v
    __version__ = _v("music-steward")      # single source of truth: pyproject.toml
except Exception:  # noqa: BLE001 -- running from a source tree that is not installed
    __version__ = "0.0.0+source"

