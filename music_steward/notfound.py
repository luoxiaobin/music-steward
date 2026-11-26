"""Removing a not-found album (docs/LIFECYCLES.md): the owner's choice, never the app's.

An album whose files are not at its collection folder's location raises the *not found* warning and keeps its decision,
matches and edits (ADR 0004 rule 7). The owner may leave it (the default: nothing changes, and it clears by itself if
the files return) or remove it. Removing deletes the app's RECORD of the album -- the album, its decision, its matches
and the owner's edits -- and nothing else: no file anywhere is touched, plans, runs and parked originals keep their own
records, and the History keeps the album's name. If the files come back later, a scan finds them as a new album.

Every album is checked again at the moment of removal: still not found, not set aside (parked), its collection folder
connected (a share that dropped would make every album look gone -- though a scan never marks files gone then, this
is checked again here), and no job running.
"""
from __future__ import annotations

import sqlite3

from . import collection, history
from .recognize.units import gone_units

MESSAGES = {
    "not_connected": collection.MESSAGES["not_connected"],
    "job_running": "Wait until the running job has finished:",
    "here": "its files are in the collection folder again",
    "retired": "its originals are set aside, not missing",
    "unknown": "no such album in this collection folder",
}


class RemoveError(ValueError):
    pass


def removable(con: sqlite3.Connection, root_id: int, folder: str = "") -> list[dict]:
    """The not-found albums of a collection folder (or of a folder inside it). Changes nothing."""
    gone = gone_units(con, root_id)
    out = []
    for u in con.execute("SELECT u.id, u.label, u.folder, d.action FROM unit u LEFT JOIN decision d ON d.unit_id=u.id "
                         "WHERE u.root_id=? ORDER BY u.folder, u.label", (root_id,)):
        if gone.get(u["id"]) != "not_found":
            continue
        if folder and not (u["folder"] == folder or u["folder"].startswith(folder.rstrip("/") + "/")):
            continue
        out.append({"id": u["id"], "label": u["label"], "folder": u["folder"], "decision": u["action"]})
    return out


def remove(con: sqlite3.Connection, root_id: int, unit_ids: list[int], job: dict | None = None) -> dict:
    """Remove the records of these not-found albums, each checked again now. {removed, kept: [{id, label, reason}]}."""
    root = con.execute("SELECT path FROM root WHERE id=?", (root_id,)).fetchone()
    if root is None:
        raise RemoveError(collection.MESSAGES["unknown"])
    if job and job.get("running"):
        raise RemoveError(f"{MESSAGES['job_running']} {job.get('kind')}")
    if not collection.connected(root["path"]):
        raise RemoveError(MESSAGES["not_connected"])
    gone = gone_units(con, root_id)
    ids, labels, kept = [], {}, []
    for uid in dict.fromkeys(int(u) for u in unit_ids):
        u = con.execute("SELECT id, label FROM unit WHERE id=? AND root_id=?", (uid, root_id)).fetchone()
        if u is None:
            kept.append({"id": uid, "label": None, "reason": MESSAGES["unknown"]})
        elif gone.get(uid) != "not_found":
            kept.append({"id": uid, "label": u["label"], "reason": MESSAGES[gone.get(uid, "here")]})
        else:
            ids.append(uid)
            labels[uid] = u["label"]
    if ids:
        marks = ",".join("?" * len(ids))
        # candidates (and their track mappings) and the decision go with the unit (ON DELETE CASCADE); the
        # decision is deleted explicitly too, so nothing depends on the foreign-key setting of a connection
        # the album's History lines stay, with its name, but can never become those of a later album that SQLite
        # gives the same id (the files coming back, say)
        history.detach_units(con, ids)
        con.execute(f"DELETE FROM decision WHERE unit_id IN ({marks})", ids)
        con.execute(f"DELETE FROM candidate WHERE unit_id IN ({marks})", ids)
        con.execute(f"DELETE FROM unit WHERE id IN ({marks})", ids)
        con.commit()
        history.record(con, "albums_removed", root_id, [-u for u in ids], detail={"n": len(ids)},
                       labels={-u: n for u, n in labels.items()})
    return {"removed": len(ids), "kept": kept}
