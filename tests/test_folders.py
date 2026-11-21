"""The folder check: form, existence, use -- in plain words, before anything is saved or written."""
import json
import os

import pytest

from music_steward import folders, settings

from .conftest import needs_ffmpeg
from .test_server import login, req, server  # noqa: F401  (fixture + helpers)

SHARES = [("192.0.2.10", "music", "/Volumes/music"), ("nas.local", "music", "/Volumes/music")]   # documentation addresses


def check(con, text, purpose="destination"):
    return folders.check_folder(con, text, purpose)


@pytest.mark.parametrize("text", [
    "//192.0.2.10/music/Ape.Flac", r"\\192.0.2.10\music\Ape.Flac", "smb://192.0.2.10/music/Ape.Flac",
    "smb://someone@192.0.2.10/music/Ape.Flac", "SMB://NAS.local/Music/Ape.Flac", '  "//192.0.2.10/music/Ape.Flac"  '])
def test_a_network_address_is_recognised_and_its_mounted_folder_offered(con, monkeypatch, text):
    monkeypatch.setattr(folders, "_mounted_shares", lambda: SHARES)
    r = check(con, text)
    assert (r["level"], r["code"], r["ok"]) == ("error", "network_address_mounted", False)
    assert r["suggestion"] == "/Volumes/music/Ape.Flac"
    assert "someone" not in json.dumps(r)                                   # a user name in the address is never echoed


def test_a_network_address_that_is_not_connected_says_how_to_connect(con, monkeypatch):
    monkeypatch.setattr(folders, "_mounted_shares", lambda: [])
    r = check(con, "//192.0.2.10/music/Ape.Flac")
    assert (r["code"], r["suggestion"], r["ok"]) == ("network_address", None, False)
    assert "Connect to Server" in r["message"]


def test_a_local_path_with_two_leading_slashes_is_not_taken_for_a_network_address(con, tmp_path):
    r = check(con, "/" + str(tmp_path), "any")
    assert (r["code"], r["ok"]) == ("ok", True)


def test_a_web_address_is_not_a_folder(con):
    assert check(con, "https://example.com/music")["code"] == "web_address"


def test_the_path_must_be_a_full_path(con, tmp_path, monkeypatch):
    assert check(con, "music/Ape.Flac")["code"] == "not_absolute"
    monkeypatch.setenv("HOME", str(tmp_path))
    r = check(con, "~/Organized")
    assert (r["code"], r["resolved"]) == ("will_be_created", str(tmp_path.resolve() / "Organized"))


def test_an_empty_entry_is_neutral(con):
    r = check(con, "   ")
    assert (r["level"], r["ok"]) == ("empty", True)


def test_an_existing_folder_is_ok_and_reports_free_space(con, tmp_path):
    r = check(con, str(tmp_path))
    assert (r["code"], r["exists"], r["is_dir"], r["ok"]) == ("ok", True, True, True) and r["free"] > 0


def test_a_file_is_not_a_folder(con, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    assert check(con, str(f))["code"] == "is_file"


def test_a_folder_that_does_not_exist_yet_can_be_created_only_if_its_parent_exists(con, tmp_path):
    r = check(con, str(tmp_path / "new"))
    assert (r["code"], r["level"], r["exists"], r["ok"]) == ("will_be_created", "warn", False, True)
    r = check(con, str(tmp_path / "typo" / "deeper" / "new"))
    assert (r["code"], r["ok"], r["detail"]) == ("parent_missing", False, str(tmp_path))


def test_a_drive_that_is_not_connected_is_named(con, tmp_path, monkeypatch):
    vols = tmp_path / "Volumes"
    (vols / "Connected").mkdir(parents=True)
    monkeypatch.setattr(folders, "VOLUMES", str(vols))
    r = check(con, str(vols / "Backup" / "Music"))
    assert (r["code"], r["detail"], r["ok"]) == ("not_mounted", "Backup", False)
    assert check(con, str(vols / "Connected" / "Music"))["code"] == "will_be_created"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_a_folder_the_app_may_not_write_to(con, tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        assert check(con, str(ro))["code"] == "not_writable"
        assert check(con, str(ro / "new"))["code"] == "not_writable"
        assert check(con, str(ro), "any")["code"] == "ok"                      # only reading is needed there
    finally:
        ro.chmod(0o755)


def test_a_place_for_files_may_not_be_inside_the_collection(con, tmp_path):
    lib = tmp_path / "lib"
    (lib / "Organized").mkdir(parents=True)
    con.execute("INSERT INTO root(path, added_at) VALUES(?, 0)", (str(lib),))
    con.commit()
    for p in (lib / "Organized", lib, tmp_path):
        r = check(con, str(p))
        assert (r["code"], r["detail"], r["ok"]) == ("overlaps", str(lib), False)
    assert check(con, str(lib / "Organized"), "any")["code"] == "ok"
    assert check(con, str(tmp_path / "elsewhere"))["code"] == "will_be_created"


def test_settings_refuse_a_bad_destination_and_store_nothing(con, tmp_path, monkeypatch):
    monkeypatch.setattr(folders, "_mounted_shares", lambda: SHARES)
    for bad in ("//192.0.2.10/music/Ape.Flac", "relative/path", "https://example.com/x", str(tmp_path / "nope" / "deeper" / "x")):
        with pytest.raises(settings.SettingsError):
            settings.save(con, {"dest": bad})
        assert settings.get(con, "dest") == ""
    with pytest.raises(settings.SettingsError, match="/Volumes/music/Ape.Flac"):
        settings.save(con, {"dest": "smb://192.0.2.10/music/Ape.Flac"})        # the message offers the real path
    assert settings.save(con, {"dest": str(tmp_path)})["dest"]["value"] == str(tmp_path)
    assert settings.save(con, {"dest": str(tmp_path / "new")})["dest"]["value"] == str(tmp_path / "new")
    assert settings.save(con, {"dest": ""})["dest"]["value"] == ""             # empty = no default, always allowed


@needs_ffmpeg
def test_the_server_answers_the_live_check_and_the_plan_refuses_a_network_address(server, library, tmp_path, monkeypatch):  # noqa: F811
    app, port = server
    ck = login(app, port)
    r = json.loads(req(port, "/api/check_folder?purpose=destination&path=" + str(tmp_path / "out").replace("/", "%2F"), cookie=ck)[2])
    assert (r["code"], r["ok"]) == ("will_be_created", True)
    r = json.loads(req(port, "/api/check_folder?path=smb%3A%2F%2F192.0.2.10%2Fmusic%2Fx", cookie=ck)[2])
    assert r["code"] in ("network_address", "network_address_mounted") and r["ok"] is False
    r = json.loads(req(port, "/api/plan", cookie=ck, method="POST", headers={"X-MLT": "1"},
                       body=json.dumps({"root": str(library), "path": "", "dest": "//192.0.2.10/music/out"}))[2])
    assert "network address" in r["error"]


@pytest.mark.parametrize("wrap", ["file://{}", '"{}"', "  {}  "])
def test_what_is_saved_is_the_path_that_was_checked(con, tmp_path, wrap):
    out = tmp_path / "out"
    out.mkdir()
    saved = settings.save(con, {"dest": wrap.format(out)})["dest"]["value"]
    assert saved == str(out)                                                # not "<cwd>/file:/..." or a quoted path
