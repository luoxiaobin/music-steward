"""The overview: where a collection folder stands, and what is left to do. Read-only."""
import json
import shutil

from music_steward import cli, i18n, report, retire
from music_steward.apply import plan

from .conftest import needs_ffmpeg
from .test_apply import accepted, snapshot  # noqa: F401  (fixture)
from .test_retire import applied  # noqa: F401  (fixture)

import pytest

pytestmark = needs_ffmpeg


def lines(o):
    return {ln["key"]: ln for s in o["sections"] for ln in s["lines"]}


def test_an_accepted_album_not_copied_yet_is_the_next_step(accepted, con):  # noqa: F811
    lib, dest = accepted
    before = snapshot(lib)
    o = report.overview(con, 1)
    got = lines(o)
    assert got["audio"]["text"].startswith("2 audio files (")
    assert (got["albums"]["n"], got["accepted"]["n"], got["undecided"]["n"]) == (1, 1, 0)
    assert got["not_copied"]["n"] == 1 and got["copied"]["n"] == 0
    assert got["not_copied"]["go"] == {"albums": "accept"} and got["garbled_tags"]["go"] == {"files": "garbled_tags"}
    assert [n["key"] for n in o["next"]] == ["not_copied"]
    assert snapshot(lib) == before                                          # read-only


def test_copied_set_aside_and_an_unfinished_copy(applied, con, tmp_path):  # noqa: F811
    lib, dest, run = applied
    got = lines(report.overview(con, 1))
    assert (got["copied"]["n"], got["not_copied"]["n"]) == (1, 0)
    retire.retire(con, run)
    got = lines(report.overview(con, 1))
    assert got["parked"]["text"] == "1 albums' originals set aside in _ORIGINALS (4 files)"
    # a second plan whose run was interrupted before any file was copied
    pid = plan.build(con, str(tmp_path / "second"), 1)
    con.execute("INSERT INTO run(plan_id, run_key, journal, method, started_at) VALUES(?, 'x', ?, 'm', 0)",
                (pid, str(tmp_path / "missing-journal.jsonl")))
    con.commit()
    o = report.overview(con, 1)
    assert lines(o)["copied"]["n"] == 1                                     # the album was copied in the first run


def test_an_interrupted_copy_is_the_first_thing_to_do(accepted, con, tmp_path):  # noqa: F811
    lib, dest = accepted
    pid = plan.build(con, str(dest), 1)
    con.execute("INSERT INTO run(plan_id, run_key, journal, method, started_at) VALUES(?, 'x', ?, 'm', 0)",
                (pid, str(tmp_path / "never-written.jsonl")))
    con.commit()
    o = report.overview(con, 1)
    assert lines(o)["copy_unfinished"]["n"] == 1 and o["next"][0]["key"] == "copy_unfinished"


def test_never_scanned_and_not_connected_still_answer(con, tmp_path):
    con.execute("INSERT INTO root(path, added_at) VALUES(?, 0)", (str(tmp_path / "nowhere"),))
    con.commit()
    o = report.overview(con, 1)
    assert [ln["key"] for ln in o["sections"][0]["lines"]] == ["never_scanned"]
    assert o["next"][0]["key"] == "never_scanned" and all(not s["lines"] for s in o["sections"][1:])


def test_a_not_found_album_is_counted_and_never_counted_as_waiting(accepted, con):  # noqa: F811
    from .test_retire import ALBUM, _recognize_folder
    from music_steward.scanner import scan
    lib, dest = accepted
    con.execute("DELETE FROM decision")
    con.commit()
    shutil.move(str(lib / ALBUM), str(lib.parent / "elsewhere"))
    scan(con, lib)
    _recognize_folder(con)
    got = lines(report.overview(con, 1))
    assert got["not_found"]["n"] == 1 and got["undecided"]["n"] == 0


def test_the_command_line_and_the_api(accepted, con, tmp_path, capsys):  # noqa: F811
    lib, dest = accepted
    db = str(tmp_path / "app.sqlite")
    assert cli.main(["--db", db, "report"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"Music Steward — {lib}") and "1 accepted albums not copied yet" in out and "Next" in out
    assert cli.main(["--db", db, "report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["root"] == str(lib)
    from music_steward.server import App, make_handler
    from http.server import ThreadingHTTPServer
    import threading
    from .test_server import login, req
    app = App(db, [])
    ref = {"port": 0}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, ref))
    ref["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        ck = login(app, ref["port"])
        o = json.loads(req(ref["port"], "/api/report?root_id=1", cookie=ck)[2])
        assert o["root"] == str(lib) and lines(o)["accepted"]["n"] == 1
        assert req(ref["port"], "/api/report?root_id=9", cookie=ck)[0] == 404
    finally:
        httpd.shutdown()


@pytest.mark.parametrize("key", sorted(report.TEXT))
def test_every_overview_sentence_can_be_translated(key):
    assert report.TEXT[key] in {r[0] for r in i18n.TABLE}


def test_the_collection_folder_path_in_the_overview_is_data():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "music_steward/web/app.js").read_text(encoding="utf-8")
    body = js[js.index("async function openOverview("):js.index('document.getElementById("overviewBtn")')]
    assert 'h("p", { class: "mono xsmall", "data-no-translate": "" }, o.root)' in body
