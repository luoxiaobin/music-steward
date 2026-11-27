"""The trial set (docs/TRIAL_E2E.md): about 20 albums covering the hard cases, picked from the database only, and the
commands that work on exactly those albums."""
import json

import pytest

from music_steward import cli, trial
from music_steward.apply import plan

from .conftest import needs_ffmpeg
from .test_apply import accepted  # noqa: F401  (fixture)


def _album(con, folder, n, *, codec="flac", channels=2, artist="Band", issues=None, label=None, kind="album",
           disc_dirs=False, artists=None):
    ids = []
    for i in range(n):
        sub = f"/CD{1 + i % 2}" if disc_dirs else ""
        tags = {"title": f"T{i}", "artist": (artists[i % len(artists)] if artists else artist), "album": label or folder}
        ids.append(con.execute(
            "INSERT INTO file(root_id, relpath, fs_relpath, kind, ext, size, mtime_ns, probe_status, codec, channels, "
            "tags_json, tag_issues_json) VALUES(1,?,?,'audio','.x',1,1,'ok',?,?,?,?)",
            (f"{folder}{sub}/{i:02d}.x", f"{folder}{sub}/{i:02d}.x", codec, channels, json.dumps(tags, ensure_ascii=False),
             json.dumps(issues) if issues else None)).lastrowid)
    return con.execute("INSERT INTO unit(root_id, folder, label, kind, key, file_ids, status) VALUES(1,?,?,?,?,?,'queued')",
                       (folder, label or folder.rsplit("/", 1)[-1], kind, f"{kind}:{folder}", json.dumps(ids))).lastrowid


@pytest.fixture
def collection(con):
    con.execute("INSERT INTO root(path, added_at) VALUES('/music', 0)")
    u = {
        "clean": _album(con, "Western/Phil Collins - Hits", 8, artist="Phil Collins"),
        "clean_big": _album(con, "Western/Huge Box", 40, artist="Somebody"),
        "garbled": _album(con, "mp3/Garbled", 6, issues=[{"field": "artist"}]),
        "comp": _album(con, "Mp3(20200426)/[世纪歌典VOL.17.民歌篇.1].专辑.(MP3)", 12, artist="群星"),
        "ape": _album(con, "Ape.Flac/邓丽君 - 精选", 1, codec="ape", kind="cue"),
        "flacimg": _album(con, "CD上翻录/image", 1, codec="flac", kind="cue"),
        "discs": _album(con, "music_cd/Two Discs", 8, disc_dirs=True),
        "surround": _album(con, "5.1声道.flac/一水隔天涯", 6, channels=6, artist="梦之旅合唱组合"),
        "duet": _album(con, "QQ.flac/对唱", 6, artists=["张也", "张也 & 阎维文"]),
        "scratch": _album(con, "_mlt_scratch/x", 8, artist="Test"),
    }
    for i in range(4):
        fid = con.execute("INSERT INTO file(root_id, relpath, fs_relpath, kind, ext, size, mtime_ns, probe_status, codec, "
                          "channels, tags_json) VALUES(1,?,?,'audio','.mp3',1,1,'ok','mp3',2,'{}')",
                          (f"刘晓-单曲/{i}.mp3", f"刘晓-单曲/{i}.mp3")).lastrowid
        con.execute("INSERT INTO unit(root_id, folder, label, kind, key, file_ids, status) VALUES(1,'刘晓-单曲',?,'track',?,?,'queued')",
                    (f"song{i}", f"track:{fid}", json.dumps([fid])))
    con.commit()
    return u


def test_the_pick_covers_every_hard_case_once(collection, con):
    u = collection
    chosen = trial.pick(con, 1)
    by = {c["category"]: c for c in chosen}
    assert set(by) == {c for c, _, _ in trial.CATEGORIES}                   # one of each kind here
    assert by["well_known"]["unit_ids"] == [u["clean"]]                      # small and clean, not the 40-file box
    assert by["garbled"]["unit_ids"] == [u["garbled"]] and by["compilation"]["unit_ids"] == [u["comp"]]
    assert by["cue_ape"]["unit_ids"] == [u["ape"]] and by["cue_flac"]["unit_ids"] == [u["flacimg"]]
    assert by["multi_disc"]["unit_ids"] == [u["discs"]] and by["surround"]["unit_ids"] == [u["surround"]]
    assert by["duet"]["unit_ids"] == [u["duet"]]
    assert by["singles"]["folder"] == "刘晓-单曲" and len(by["singles"]["unit_ids"]) == 4
    assert all(u["scratch"] not in c["unit_ids"] for c in chosen)            # the experiment's leftovers never


def test_fill_order_covers_every_category():
    assert sorted(trial.FILL_ORDER) == sorted(c for c, _, _ in trial.CATEGORIES)


def test_decided_albums_are_not_picked_and_nothing_on_disk_is_read(collection, con, monkeypatch):
    con.execute("INSERT INTO decision(unit_id, action, decided_at) VALUES(?, 'skip', 0)", (collection["clean"],))
    con.commit()
    import builtins
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda *a, **k: (_ for _ in ()).throw(AssertionError(f"opened {a[0]}"))
                        if str(a[0]).startswith("/music") else real_open(*a, **k))
    chosen = trial.pick(con, 1)
    assert all(collection["clean"] not in c["unit_ids"] for c in chosen)


def test_saved_in_the_app_folder_and_shown(collection, con, tmp_path, monkeypatch):
    monkeypatch.setattr(trial, "path", lambda: tmp_path / "trial.json")
    assert cli.main(["--db", con.execute("PRAGMA database_list").fetchone()[2], "trial", "pick"]) == 0
    t = trial.load()
    assert t["root_id"] == 1 and len(t["albums"]) == 9
    with pytest.raises(SystemExit, match="exists already"):
        cli.main(["--db", con.execute("PRAGMA database_list").fetchone()[2], "trial", "pick"])
    rows = trial.status(con)
    assert {r["state"] for r in rows} == {"waiting: queued"}


@needs_ffmpeg
def test_a_trial_plan_holds_only_the_trial_albums(accepted, con, tmp_path):  # noqa: F811
    lib, dest = accepted
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    with pytest.raises(plan.PlanError, match="no albums"):
        plan.build(con, str(dest), 1, unit_ids=[])
    assert plan.load(con, plan.build(con, str(dest), 1, unit_ids=[uid + 1]))["items"] == []     # not in the set
    p = plan.load(con, plan.build(con, str(dest) + "2", 1, unit_ids=[uid]))
    assert p["items"] and {i["unit_id"] for i in p["items"]} == {uid}


def test_the_album_list_marks_the_trial_set(collection, con, tmp_path, monkeypatch):
    from http.server import ThreadingHTTPServer
    import threading
    from music_steward.server import App, make_handler
    from .test_server import login, req
    monkeypatch.setattr(trial, "path", lambda: tmp_path / "trial.json")
    trial.save(1, [{"category": "garbled", "unit_ids": [collection["garbled"]], "folder": "mp3/Garbled",
                    "label": "Garbled", "n": 6}])
    app = App(con.execute("PRAGMA database_list").fetchone()[2], [])
    ref = {"port": 0}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, ref))
    ref["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        ck = login(app, ref["port"])
        us = json.loads(req(ref["port"], "/api/units?root_id=1", cookie=ck)[2])
        assert [u["id"] for u in us if u["trial"]] == [collection["garbled"]]
    finally:
        httpd.shutdown()
