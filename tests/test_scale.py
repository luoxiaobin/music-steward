"""A big collection must stay responsive: the album list and the Overview cost a fixed number of database queries, not
a few per album (measured on a library like the owner's -- 142,000 files, 18,000 albums, 91,000 candidates -- one
folder's list took 27 s and the whole collection 134 s before; 0.4 s and 1.5 s after)."""
import json

from music_steward.recognize import results


def _library(con, n_units: int, per_unit: int = 3):
    con.execute("INSERT INTO root(path, added_at) VALUES('/lib', 0)")
    cid = 0
    for u in range(1, n_units + 1):
        fids = []
        for t in range(2):
            fids.append(con.execute(
                "INSERT INTO file(root_id, relpath, fs_relpath, kind, ext, size, mtime_ns) VALUES(1,?,?,'audio','.flac',1,1)",
                (f"F{u % 7}/A{u}/{t}.flac", f"F{u % 7}/A{u}/{t}.flac")).lastrowid)
        con.execute("INSERT INTO unit(id, root_id, folder, label, kind, key, file_ids, status) VALUES(?,1,?,?,'album',?,?,'done')",
                    (u, f"F{u % 7}/A{u}", f"A{u}", f"album:{u}", json.dumps(fids)))
        for r in range(per_unit):
            cid += 1
            con.execute("INSERT INTO candidate(id, unit_id, rank, source, album, artist, distance, fingerprint_agrees, "
                        "n_mapped, mediums) VALUES(?,?,?,'MusicBrainz',?,'X',?,0,2,2)",
                        (cid, u, per_unit - 1 - r, f"A{u}", 0.05 + r / 100))     # ranks given in reverse order
            if r == per_unit - 1:                                           # rank 0: the top candidate
                for n, f in enumerate(fids, 1):
                    con.execute("INSERT INTO cand_track(candidate_id, file_id, track_index, medium, medium_index) "
                                "VALUES(?,?,?,2,?)", (cid, f, n, n))
        if u % 5 == 0:
            con.execute("INSERT INTO candidate(id, unit_id, rank, source, album, artist, year) "
                        "VALUES(?, ?, 9, 'Local', 'own', 'me', 1999)", (10_000_000 + u, u))
        if u % 4 == 0:
            con.execute("INSERT INTO decision(unit_id, action, candidate_id, decided_at) VALUES(?, 'skip', NULL, 0)", (u,))
    con.commit()


def test_the_album_list_costs_the_same_few_queries_for_any_number_of_albums(con):
    _library(con, 1900)                                                     # more than one chunk of 900
    queries = []
    con.set_trace_callback(queries.append)
    us = results.units(con, 1)
    con.set_trace_callback(None)
    assert len(us) == 1900
    assert len(queries) < 25, len(queries)                                  # was about 4 per album: 7,600
    by = {u["id"]: u for u in us}
    u = by[20]                                                              # own info and a decision
    assert u["top"]["rank"] == 0 and u["top"]["distance"] == 0.07            # the best-ranked online candidate
    assert u["local"]["album"] == "own" and u["decision"]["action"] == "skip"
    assert u["evidence"]["disc_of_set"] is True and u["evidence"]["disc"] == 2   # the top candidate's disc, batched
    assert by[1]["local"] is None and by[1]["decision"] is None


def test_candidates_are_indexed_by_album(con):
    plans = " ".join(r[3] for r in con.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM candidate WHERE unit_id=? ORDER BY rank", (1,)))
    assert "candidate_unit" in plans, plans


def test_the_window_polls_only_the_job_while_it_runs(tmp_path):
    from http.server import ThreadingHTTPServer
    import threading
    from music_steward.server import App, make_handler
    from .test_server import login, req
    app = App(str(tmp_path / "a.sqlite"), [])
    ref = {"port": 0}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, ref))
    ref["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        ck = login(app, ref["port"])
        app.scan_state = {"running": True, "kind": "recognize", "done": 3, "total": 9}
        assert json.loads(req(ref["port"], "/api/job", cookie=ck)[2]) == {"scan": app.scan_state}
    finally:
        httpd.shutdown()
    from pathlib import Path
    web = Path(__file__).resolve().parents[1] / "music_steward/web"
    js = (web / "app.js").read_text(encoding="utf-8") + (web / "plan.js").read_text(encoding="utf-8")
    for line in js.splitlines():                                            # polling loops use the cheap endpoint
        if "setTimeout(" in line and "api(" in line:
            assert "/api/job" in line, line


def test_the_tree_answers_first_and_late_answers_are_dropped():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "music_steward/web/app.js").read_text(encoding="utf-8")
    click = js[js.index("async function treeNode("):js.index("const li = h(\"li\"")]
    assert click.index("await renderTree()") < click.index("await refreshCenter()")
    center = js[js.index("async function refreshCenter()"):js.index("function renderFiles(")]
    assert center.count("if (stale()) return") >= 2 and "Loading…" in center
    assert "const PAGE = 500" in js and "S.drawMoreUnits(id)" in js
