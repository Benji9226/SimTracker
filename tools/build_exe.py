"""Build the double-click program:   python tools/build_exe.py      ->   dist/SimTracker.exe

Needs:  pip install pyinstaller
If tools/install_dir.txt holds a folder, the new exe is also copied there (the copy you actually run).
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
built = ROOT / "dist" / "SimTracker.exe"
print("\nDone:", built)

# Also put the new exe where you run it from: write that folder in tools/install_dir.txt (one line, not shared via git).
target_file = ROOT / "tools" / "install_dir.txt"
if target_file.is_file():
    target = Path(target_file.read_text(encoding="utf-8").strip().strip('"'))
    if target.is_dir():
        try:
            shutil.copy2(built, target / "SimTracker.exe")
            print("Copied to", target / "SimTracker.exe", "(your data folder there is untouched)")
        except PermissionError:
            sys.exit(f"Built, but {target / 'SimTracker.exe'} is in use. Close SimTracker (the black window) and run this again, "
                     f"or copy {built} over it by hand.")
    else:
        print("tools/install_dir.txt points at a folder that does not exist:", target)
