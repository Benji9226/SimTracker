"""Build the double-click program:   python tools/build_exe.py      ->   dist/SimTracker.exe

Needs:  pip install pyinstaller
Only the program's built-in files are packed (pages, track catalog, track drawings, cars.json).
Nothing personal goes in: not your config.json, saved laps or track profiles.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEP = ";" if sys.platform == "win32" else ":"

add = [
    (ROOT / "web", "web"),
    (ROOT / "data" / "track_catalog.json", "data"),
    (ROOT / "data" / "cars.json", "data"),
    (ROOT / "data" / "track_maps", "data/track_maps"),
]
cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--console",
       "--name", "SimTracker", "--distpath", str(ROOT / "dist"),
       "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
       "--collect-all", "duckdb", "--exclude-module", "matplotlib", "--exclude-module", "scipy"]
for src, dest in add:
    cmd += ["--add-data", f"{src}{SEP}{dest}"]
cmd.append(str(ROOT / "dashboard.py"))

print(" ".join(cmd))
subprocess.run(cmd, check=True)
shutil.rmtree(ROOT / "build", ignore_errors=True)
print("\nDone:", ROOT / "dist" / "SimTracker.exe")
