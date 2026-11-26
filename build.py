#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build a folder anyone can unzip and run on Windows, without Python:

  dist/CapCut导出器/
    CapCut导出器.exe   the window
    render.exe         the exporter it runs
    ffmpeg/            ffmpeg.exe + ffprobe.exe
    使用说明.txt
  dist/CapCut导出器.zip

Usage (PyInstaller must be importable by this Python):
  python build.py [--ffmpeg <folder with ffmpeg.exe and ffprobe.exe>]
"""
import argparse, os, shutil, subprocess, sys, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "CapCut导出器"

ap = argparse.ArgumentParser()
ap.add_argument("--ffmpeg", default=os.path.join(HERE, "ffmpeg"),
                help="folder with ffmpeg.exe and ffprobe.exe (default: ./ffmpeg)")
args = ap.parse_args()
for tool in ("ffmpeg.exe", "ffprobe.exe"):
    if not os.path.isfile(os.path.join(args.ffmpeg, tool)):
        sys.exit(f"missing {tool} in {args.ffmpeg}")

build, dist = os.path.join(HERE, "build"), os.path.join(HERE, "dist")
shutil.rmtree(build, ignore_errors=True)
shutil.rmtree(dist, ignore_errors=True)


def pyinstaller(*options):
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--workpath", build, "--specpath", build, "--distpath", dist, *options],
                   check=True, cwd=HERE)


pyinstaller("--onedir", "--windowed", "--name", NAME, os.path.join(HERE, "gui.py"))
pyinstaller("--onefile", "--console", "--name", "render", os.path.join(HERE, "render.py"))

app = os.path.join(dist, NAME)
shutil.move(os.path.join(dist, "render.exe"), os.path.join(app, "render.exe"))
os.makedirs(os.path.join(app, "ffmpeg"))
for tool in ("ffmpeg.exe", "ffprobe.exe"):
    shutil.copy2(os.path.join(args.ffmpeg, tool), os.path.join(app, "ffmpeg", tool))
shutil.copy2(os.path.join(HERE, "使用说明.txt"), os.path.join(app, "使用说明.txt"))

archive = os.path.join(dist, NAME + ".zip")
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, _dirs, files in os.walk(app):
        for file in files:
            full = os.path.join(root, file)
            zf.write(full, os.path.relpath(full, dist))
shutil.rmtree(build, ignore_errors=True)
print(f"\nfolder: {app}\nzip:    {archive} ({os.path.getsize(archive) / 2**20:.0f} MiB)")
