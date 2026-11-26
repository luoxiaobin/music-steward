"""Entry point of the packaged macOS app (built by PyInstaller from MusicSteward.spec).

The same binary is started three ways:
  * from the Finder / Dock: prepare the environment (tools on PATH, the keys from the login shell -- see
    music_steward/environment.py) and open the window, with the collection folders the app already knows;
  * by the app itself, once per album being recognized: `Music Steward --recognize-worker --db … --unit …`;
  * by CI after a build: `Music Steward --self-test`.
"""
import sys


def main() -> int:
    from music_steward import environment
    args = sys.argv[1:]
    if args[:1] == [environment.WORKER_FLAG]:
        from music_steward.recognize import worker
        return worker.main(args[1:])
    if args[:1] == ["--self-test"]:
        environment.fix_path()
        from music_steward import selftest
        return selftest.run()
    environment.prepare()
    from music_steward import db
    from music_steward.desktop import run
    run(str(db.DEFAULT_DB), [])
    return 0


if __name__ == "__main__":
    sys.exit(main())
