#!/usr/bin/env python3
"""Build a standalone Windows MLBDaily.exe with ffmpeg bundled.

Produces a windowed (double-click) app under dist/MLBDaily/ and zips it to
dist/MLBDaily-v<version>-win64.zip, with ffmpeg/ffprobe and the license files
included so non-technical users need nothing else installed.

Usage:
  pip install pyinstaller
  python build_exe.py                       # downloads ffmpeg if needed
  python build_exe.py --ffmpeg-dir ./ffmpeg --dist ./dist --work ./build

The bundled ffmpeg is a slim **LGPL** shared build (we only remux with -c copy,
so no encoders are needed). Windows only.
"""
import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

# Slim LGPL shared build: tiny ffmpeg.exe/ffprobe.exe + shared codec DLLs.
FFMPEG_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
              "ffmpeg-master-latest-win64-lgpl-shared.zip")


def get_version():
    """Read __version__ from MLBDaily.py without importing it."""
    ns = {}
    with open(os.path.join(HERE, "MLBDaily.py"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("__version__"):
                exec(line, ns)
                break
    return ns.get("__version__", "0.0.0")


def ensure_ffmpeg(ffmpeg_dir):
    """Make sure ffmpeg_dir holds ffmpeg.exe + ffprobe.exe (+ shared DLLs).
    Downloads and extracts the LGPL shared build if they're missing."""
    have = all(os.path.isfile(os.path.join(ffmpeg_dir, x))
               for x in ("ffmpeg.exe", "ffprobe.exe"))
    if have:
        print(f"ffmpeg already present in {ffmpeg_dir}")
        return
    os.makedirs(ffmpeg_dir, exist_ok=True)
    tmp = os.path.join(ffmpeg_dir, "_ffmpeg.zip")
    print(f"Downloading ffmpeg (LGPL shared build)\n  {FFMPEG_URL}")
    urllib.request.urlretrieve(FFMPEG_URL, tmp)
    print("Extracting bin/ + license...")
    with zipfile.ZipFile(tmp) as z:
        for name in z.namelist():
            if name.endswith("/"):
                continue
            base = os.path.basename(name)
            if "/bin/" in name:
                with open(os.path.join(ffmpeg_dir, base), "wb") as out:
                    out.write(z.read(name))
            elif base == "LICENSE.txt":
                with open(os.path.join(ffmpeg_dir, "ffmpeg-LICENSE.txt"), "wb") as out:
                    out.write(z.read(name))
    os.remove(tmp)
    print(f"ffmpeg ready in {ffmpeg_dir}")


def build(ffmpeg_dir, dist_dir, work_dir):
    version = get_version()
    # Bundle every ffmpeg file at the app root so resolve_tool() finds the exes
    # and ffmpeg can load its sibling DLLs.
    binaries = []
    for fn in sorted(os.listdir(ffmpeg_dir)):
        if fn.lower().endswith((".exe", ".dll")):
            src = os.path.join(ffmpeg_dir, fn)
            binaries += ["--add-binary", f"{src}{os.pathsep}."]

    args = [
        "--noconfirm", "--clean", "--windowed",
        "--name", "MLBDaily",
        "--distpath", dist_dir,
        "--workpath", work_dir,
        "--specpath", work_dir,
        *binaries,
        os.path.join(HERE, "MLBDaily.py"),
    ]
    print("Running PyInstaller...")
    import PyInstaller.__main__ as pyi
    pyi.run(args)

    appdir = os.path.join(dist_dir, "MLBDaily")
    # Drop license files next to the exe.
    shutil.copy(os.path.join(HERE, "LICENSE"), os.path.join(appdir, "LICENSE.txt"))
    ff_lic = os.path.join(ffmpeg_dir, "ffmpeg-LICENSE.txt")
    if os.path.isfile(ff_lic):
        shutil.copy(ff_lic, os.path.join(appdir, "ffmpeg-LICENSE.txt"))
    with open(os.path.join(appdir, "READ-ME-FIRST.txt"), "w", encoding="utf-8") as f:
        f.write(
            "MLBDaily\n"
            "========\n\n"
            "Double-click MLBDaily.exe to start. No install needed; ffmpeg is\n"
            "bundled. Videos download to a 'mlb_videos' folder by default.\n\n"
            "ffmpeg is included under the LGPL (see ffmpeg-LICENSE.txt).\n"
            "MLBDaily itself is MIT licensed (see LICENSE.txt).\n"
            "Project: https://github.com/SlimJimPoisson/MLBDaily\n"
        )

    zip_path = os.path.join(dist_dir, f"MLBDaily-v{version}-win64.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    print(f"Zipping -> {zip_path}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(appdir):
            for fn in files:
                full = os.path.join(root, fn)
                z.write(full, os.path.relpath(full, dist_dir))  # keep top MLBDaily/ dir
    mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"Done: {zip_path}  ({mb:.1f} MB)")
    return zip_path


def main():
    if os.name != "nt":
        print("This build targets Windows (.exe). Run it on Windows.", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser(description="Build MLBDaily.exe with bundled ffmpeg.")
    ap.add_argument("--ffmpeg-dir", default=os.path.join(HERE, "ffmpeg"))
    ap.add_argument("--dist", default=os.path.join(HERE, "dist"))
    ap.add_argument("--work", default=os.path.join(HERE, "build"))
    a = ap.parse_args()
    ensure_ffmpeg(a.ffmpeg_dir)
    build(a.ffmpeg_dir, a.dist, a.work)
    return 0


if __name__ == "__main__":
    sys.exit(main())
