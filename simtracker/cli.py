"""One-command LMU lap analysis. No file path needed.

    python tracker.py                 analyse the newest session automatically
    python tracker.py --watch         keep running; re-analyse whenever a session is saved/updated
    python tracker.py --list          show recent sessions
    python tracker.py --file X.duckdb analyse a specific file
    python tracker.py --laps 3 5      compare lap 5 against reference lap 3

The Telemetry folder is found automatically (Steam libraries on every drive) and
remembered in config.json. Set LMU_TELEMETRY_DIR to override.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from simtracker.analysis.report import report
from simtracker.paths import OUTPUT_DIR
from simtracker.telemetry.finder import find_telemetry_dir, sessions
from simtracker.telemetry.info import read_info
from simtracker.telemetry.loader import load_lmu_files
from simtracker.telemetry.sessions import groups


def analyse(path: Path, laps=None, files=None) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"\n=== {path.name} ===")
    df = load_lmu_files([str(f) for f in (files or [path])])
    ref, other = (laps or (None, None))
    report(df, ref_lap=ref, other_lap=other, out_png=str(OUTPUT_DIR / "comparison.png"),
           info=read_info(str((files or [path])[-1]))["info"])


def watch(folder: Path, laps=None, settle: float = 5.0) -> None:
    print(f"Watching {folder}  (Ctrl+C to stop)")
    seen: dict[Path, float] = {}
    first = True
    while True:
        gs = groups(folder)
        if gs:
            g = gs[0]
            newest = folder / g["id"]
            m = g["mtime"]
            if first:
                seen[newest] = m          # don't re-run old sessions on startup
                first = False
                print(f"Latest existing session: {g['label']} (will analyse when it changes)")
            elif seen.get(newest) != m and time.time() - m >= settle:
                seen[newest] = m
                try:
                    analyse(newest, laps, [folder / f for f in g["files"]])
                except Exception as e:     # file mid-write, too few laps, etc.
                    print(f"Skipped {newest.name}: {e}")
        time.sleep(2)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--file")
    ap.add_argument("--laps", nargs=2, type=int, metavar=("REF", "OTHER"))
    a = ap.parse_args()

    if a.file:
        return analyse(Path(a.file), a.laps)
    folder = find_telemetry_dir()
    files = sessions(folder)
    if a.list:
        for g in groups(folder)[:15]:
            print(time.strftime("%Y-%m-%d %H:%M", time.localtime(g["mtime"])), g["label"])
        return
    if a.watch:
        return watch(folder, a.laps)
    if not files:
        sys.exit(f"No .duckdb sessions in {folder}")
    g = groups(folder)[0]
    analyse(folder / g["id"], a.laps, [folder / f for f in g["files"]])
