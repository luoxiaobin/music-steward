# PyInstaller spec for the macOS app: `pyinstaller packaging/macos/MusicSteward.spec` from the repository root
# (packaging/macos/build.sh does that, then checks the result). Unsigned unless CODESIGN_IDENTITY is set.
import os
import tomllib

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
VERSION = tomllib.load(open(os.path.join(ROOT, "pyproject.toml"), "rb"))["project"]["version"]
IDENTITY = os.environ.get("CODESIGN_IDENTITY") or None

# loaded by name at run time, so PyInstaller cannot see them by itself
hidden = (collect_submodules("music_steward") + collect_submodules("beetsplug") + collect_submodules("beets")
          + collect_submodules("discogs_client") + ["acoustid", "webview.platforms.cocoa"])
datas = (collect_data_files("music_steward") + collect_data_files("beets") + collect_data_files("beetsplug")
         + collect_data_files("opencc") + collect_data_files("confuse") + collect_data_files("mediafile")
         # package metadata: music_steward.__version__ and beets' own version checks read it at run time
         + copy_metadata("music-steward") + copy_metadata("beets"))

a = Analysis([os.path.join(SPECPATH, "app_main.py")], pathex=[ROOT], datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Music Steward", console=False,
          codesign_identity=IDENTITY, entitlements_file=os.path.join(SPECPATH, "entitlements.plist"))
coll = COLLECT(exe, a.binaries, a.datas, name="Music Steward")
app = BUNDLE(coll, name="Music Steward.app", icon=os.path.join(ROOT, "assets", "icon", "MusicSteward.icns"),
             bundle_identifier="app.musicsteward.MusicSteward", version=VERSION,
             info_plist={"CFBundleName": "Music Steward", "CFBundleDisplayName": "Music Steward",
                         "CFBundleShortVersionString": VERSION, "CFBundleVersion": VERSION,
                         "LSMinimumSystemVersion": "12.0", "NSHighResolutionCapable": True,
                         "LSApplicationCategoryType": "public.app-category.music",
                         "NSHumanReadableCopyright": "GPL-3.0-or-later"})
