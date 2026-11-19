"""Delete old telemetry session files to save disk space.

The game keeps writing a new .duckdb file for every session and never removes them. This deletes the ones
that have not been written to for `days` days, with these safeguards:

  * only *.duckdb files directly inside the Telemetry folder are ever touched (never config.json or anything else);
  * the newest session is always kept, however old, so you are never left with nothing;
  * a session whose runs are not ALL old is kept whole;
  * `before_delete(session)` runs first (the dashboard uses it to check the session's best lap against your
    saved personal best), and if it fails the session is kept;
  * a file that cannot be deleted (for example the game has it open) is left alone and reported.

Your saved laps (data/saved_laps) live elsewhere and are never touched. They carry the setup of the lap with them (the setup book),
so the best lap and its setup outlive the telemetry that is deleted here.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from simtracker.telemetry.sessions import groups


def plan(folder: Path, days: int, now: float | None = None) -> list[dict]:
    """Sessions that are old enough to delete: all their files older than `days`; never the newest session."""
    cutoff = (now if now is not None else time.time()) - days * 86400
    return [g for g in groups(folder)[1:] if g["mtime"] < cutoff]


def run(folder: Path, days: int, before_delete=None, dry_run: bool = False, now: float | None = None) -> dict:
    """Delete (or, with dry_run, only list) the old sessions. Returns what happened."""
    result = {"at": datetime.now().isoformat(timespec="seconds"), "days": days, "dry_run": dry_run,
              "deleted": [], "kept": [], "freed_bytes": 0}
    if days <= 0:
        return result
    for g in plan(folder, days, now):
        files = [folder / f for f in g["files"]]
        if before_delete is not None and not dry_run:
            try:
                before_delete(g)
            except Exception as e:                       # could not read/check it: keep it
                result["kept"].append({"session": g["id"], "reason": f"could not be checked first ({e})"})
                continue
        for path in files:
            size = path.stat().st_size if path.is_file() else 0
            if dry_run:
                result["deleted"].append({"file": path.name, "bytes": size})
                result["freed_bytes"] += size
                continue
            try:
                path.unlink()
                for extra in (path.with_name(path.name + ".wal"),):        # DuckDB's leftover write-ahead log
                    if extra.is_file():
                        extra.unlink()
            except OSError as e:                         # in use (e.g. the game has it open): leave it
                result["kept"].append({"session": path.name, "reason": f"could not be deleted ({e.strerror or e})"})
                continue
            result["deleted"].append({"file": path.name, "bytes": size})
            result["freed_bytes"] += size
    return result


def folder_summary(folder: Path) -> dict:
    """How much room the session files take (for the page footer)."""
    files = list(folder.glob("*.duckdb"))
    return {"files": len(files), "bytes": sum(f.stat().st_size for f in files)}
