"""Group LMU telemetry files that belong to the same game session.

The game starts a new .duckdb every time you leave the garage, even inside one practice
session. Two files are the same session when they are for the same track, car and session
type AND the later one carries on where the earlier one stopped: its first lap number is the
earlier one's last lap (or the next), and the odometer (Total Dist) continues. Restarting the
game or a new session resets both, so those stay separate.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from simtracker.telemetry.loader import open_db


_cache: dict[tuple, dict | None] = {}


def facts(path: Path) -> dict | None:
    """Small fingerprint of one file (cached until the file changes). None if unreadable."""
    st = path.stat()
    key = (path.name, st.st_mtime, st.st_size)
    if key in _cache:
        return _cache[key]
    out = None
    try:
        con, tmp = open_db(str(path))
        try:
            meta = dict(con.execute("SELECT key, value FROM metadata").fetchall())
            laps = [r[0] for r in con.execute('SELECT value FROM "Lap" ORDER BY ts').fetchall()]
            d0, d1 = con.execute('SELECT min(value), max(value) FROM "Total Dist"').fetchone()
            t0, t1 = con.execute('SELECT min(value), max(value) FROM "GPS Time"').fetchone()
            out = {
                "who": (meta.get("TrackName"), meta.get("TrackLayout"), meta.get("CarName"), meta.get("SessionType")),
                "recorded": meta.get("RecordingTime", path.name),
                "first_lap": int(laps[0]) if laps else None, "last_lap": int(laps[-1]) if laps else None,
                "dist0": d0, "dist1": d1, "t0": t0, "t1": t1,
            }
        finally:
            con.close()
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        out = None
    for k in [k for k in _cache if k[0] == path.name]:
        del _cache[k]
    _cache[key] = out
    return out


def _continues(a: dict, b: dict) -> bool:
    """Does file b carry on from file a?"""
    if a["who"] != b["who"] or None in (a["last_lap"], b["first_lap"]):
        return False
    return (b["first_lap"] in (a["last_lap"], a["last_lap"] + 1)
            and abs(b["dist0"] - a["dist1"]) < 200
            and b["t0"] >= a["t0"])


def groups(folder: Path) -> list[dict]:
    """Sessions, newest first: {"id", "files" (oldest first), "label", "mtime", "runs"}."""
    items = []
    for p in folder.glob("*.duckdb"):
        try:
            items.append((p, facts(p), p.stat().st_mtime))
        except OSError:
            continue
    items.sort(key=lambda x: (x[1]["recorded"] if x[1] else x[0].name))
    out: list[dict] = []
    for p, f, mtime in items:
        target = None
        if f:
            for g in reversed(out):                       # most recent group first
                if g["facts"] and _continues(g["facts"], f):
                    target = g
                    break
        if target is None:
            out.append({"id": p.name, "files": [p.name], "facts": f, "mtime": mtime})
        else:
            target["files"].append(p.name)
            target["facts"] = f
            target["mtime"] = max(target["mtime"], mtime)
    for g in out:
        g["runs"] = len(g["files"])
        base = g["id"].replace(".duckdb", "").replace("_", " ")
        g["label"] = base + (f"  ({g['runs']} runs)" if g["runs"] > 1 else "")
    out.sort(key=lambda g: g["mtime"], reverse=True)
    return out


def group_for(folder: Path, name: str) -> dict:
    """The group whose id (first file name) or any member file is `name`."""
    base = Path(name).name
    for g in groups(folder):
        if base == g["id"] or base in g["files"]:
            return g
    raise FileNotFoundError(name)
