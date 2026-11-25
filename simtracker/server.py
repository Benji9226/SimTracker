"""Local web dashboard for LMU laps.

    python dashboard.py            then open http://localhost:8765 (opens automatically)

Lists your sessions (newest first, refreshes itself), shows the laps, and lets you
pick a reference lap and a comparison lap to plot speed/throttle/brake/steering/delta.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

from simtracker.analysis.consistency import turn_stats
from simtracker.analysis.corners import corner_report
from simtracker.analysis import setup_report
from simtracker.analysis.edges import track_edges
from simtracker.analysis.layout import MAX_RMS, lap_xy, match_to_map, outline_from_lap
from simtracker.analysis.laps import compare_laps, compare_to_grid, fmt_time, grid_points, lap_on_grid, lap_summary
from simtracker import catalog, settings, updater
from simtracker.version import VERSION
from simtracker.paths import DATA_DIR, FROZEN, MAPS_DIR, WEB_DIR
from simtracker.storage import profiles, saved_laps, surface
from simtracker.storage.profiles import track_key
from simtracker import rivals, whatsnew
from simtracker.setup import model as setup_model
from simtracker.telemetry import cleanup
from simtracker.telemetry.finder import TelemetryNotFound, ask_for_folder, find_telemetry_dir
from simtracker.telemetry.info import car_model, read_info
from simtracker.telemetry.loader import load_lmu_files, open_db
from simtracker.telemetry.sessions import facts, group_for, groups

PORT = 8765
FOLDER: Path = None          # the Telemetry folder, found when the dashboard starts (see main)
_cache: dict[tuple, object] = {}
_lock = threading.Lock()


def _files(name: str) -> list[Path]:
    """Files of the session `name` (a session can be several runs). Names only, no paths from the client."""
    return [FOLDER / f for f in group_for(FOLDER, name)["files"]]


def _links(name: str) -> list[bool]:
    return group_for(FOLDER, name)["links"]


def _df(name: str):
    files = _files(name)
    key = tuple((p.name, p.stat().st_mtime) for p in files)
    with _lock:
        if key not in _cache:
            _cache.clear()                # keep only one session in memory
            _cache[key] = load_lmu_files([str(p) for p in files], _links(name))
        return _cache[key]


def _clean(a) -> list:
    return [None if not np.isfinite(x) else round(float(x), 4) for x in np.asarray(a, float)]


_last_cleanup: dict | None = None
CLEANUP_LOG = DATA_DIR / "cleanup_log.txt"


def run_cleanup() -> dict:
    """Delete telemetry session files older than the number of days in data/settings.json (see telemetry/cleanup.py).

    Before a session is deleted its laps are loaded once through api_laps, which checks its best clean lap
    against your saved personal best, so a record is never lost just because you never opened that session."""
    global _last_cleanup
    days = int(settings.load().get("telemetry_cleanup_days") or 0)
    result = cleanup.run(FOLDER, days, before_delete=lambda g: api_laps(g["id"])) if days > 0 else {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "days": 0, "deleted": [], "kept": [], "freed_bytes": 0}
    _last_cleanup = result
    lines = [f"{result['at']}  deleted {d['file']} ({d['bytes'] / 1048576:.1f} MB)" for d in result["deleted"]]
    lines += [f"{result['at']}  kept {k['session']}: {k['reason']}" for k in result["kept"]]
    if lines:
        with CLEANUP_LOG.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return result


def backfill_surface(limit: int = 60) -> int:
    """Teach the track surface (storage/surface.py) from the sessions already in the Telemetry folder, newest first, once at
    start-up. Sessions that cannot be read right now (the game has the file open) are skipped; laps already used are not
    counted again. Returns how many laps were added."""
    added = 0
    for g in groups(FOLDER)[:limit]:
        try:
            df = load_lmu_files([str(FOLDER / f) for f in g["files"]], g["links"])
            info = read_info(str(FOLDER / g["files"][-1]))["info"]
            added += surface.update(info, g["id"], df, lap_summary(df))
        except Exception:
            continue
        time.sleep(0.5)                           # be gentle: this runs next to the game
    return added


def backfill_best_setups() -> int:
    """Once at start-up: give personal bests saved before the setup book the setup of their session, while that session's telemetry is
    still there. A best whose session is already deleted cannot be filled in (its setup is gone). Returns how many were filled in."""
    done = 0
    try:
        by_id = {g["id"]: g for g in groups(FOLDER)}
        for path, meta in saved_laps.best_files_without_setup():
            g = by_id.get(meta.get("session") or "")
            if not g:
                continue
            setup = _lap_setup([FOLDER / f for f in g["files"]], float(meta["lap_time"]))
            if setup and saved_laps.add_setup(path, setup):
                done += 1
    except Exception:
        pass
    return done


def _cleanup_loop() -> None:
    while True:                                  # once at start-up, then every day while the dashboard runs
        try:
            run_cleanup()
        except Exception as e:                   # never let clean-up take the dashboard down
            print("telemetry clean-up failed:", e)
        time.sleep(24 * 3600)


def api_status() -> dict:
    days = int(settings.load().get("telemetry_cleanup_days") or 0)
    now = cleanup.folder_summary(FOLDER)
    last = _last_cleanup
    return {"version": VERSION, "cleanup_days": days, "folder_files": now["files"], "folder_mb": round(now["bytes"] / 1048576, 1),
            "last": None if not last else {"at": last["at"], "deleted": len(last["deleted"]),
                                           "freed_mb": round(last["freed_bytes"] / 1048576, 1), "kept": len(last["kept"])}}


def _map_dot(df, summary, clean, m) -> dict | None:
    """Where each metre of the lap is on the map (so the page can show a dot), or None when that can't be told
    (no GPS in the file, a map drawn as a filled band, or a poor fit)."""
    shapes = m.get("shapes") or []
    if len(shapes) != 1 or shapes[0]["kind"] != "stroke" or len(shapes[0]["rings"]) != 1:
        return None
    lap = int(clean["lap_time"].idxmin())
    length = float(summary.loc[lap, "length_m"])
    found = match_to_map(lap_xy(df, lap, length), np.array(shapes[0]["rings"][0], float))
    if not found or found["rms"] > MAX_RMS:
        return None
    return {"length": round(length, 1), "rms": round(found["rms"], 4),
            "points": [[round(float(x), 4), round(float(y), 4)] for x, y in found["points"]]}


def _gps_map(df, summary, clean, info) -> dict | None:
    """A drawing made from the lap's own GPS positions, for a layout that has no drawing in data/track_maps
    (for example the short Sebring School Circuit). The dot then follows the lap exactly."""
    lap = int(clean["lap_time"].idxmin())
    length = float(summary.loc[lap, "length_m"])
    xy = lap_xy(df, lap, length, n=400)
    if xy is None:
        return None
    pts = [[round(float(x), 4), round(float(y), 4)] for x, y in outline_from_lap(xy)]
    return {"slug": "gps", "title": info.get("layout") or info.get("track") or "Track", "author": "the game's own GPS positions",
            "shapes": [{"kind": "stroke", "rings": [pts]}], "dot": {"length": round(length, 1), "rms": 0.0, "points": pts}}


def api_trackmap(name: str) -> dict:
    """The drawing of this session's track layout (from data/track_maps), or {"map": None}.

    The circuit comes from the session's track name and lap length, via the catalog (so a short layout
    of a circuit does not get the full circuit's drawing)."""
    info = _info(name)["info"]
    s = lap_summary(_df(name))
    clean = s[s["complete"]]
    if clean.empty:
        return {"map": None}
    entry = catalog.find(info.get("track", ""), float(clean["length_m"].median()))
    slug = entry.get("map") if entry else None
    m = None
    if slug and re.fullmatch(r"[a-z0-9]+", slug):
        try:
            m = json.loads((MAPS_DIR / f"{slug}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            m = None
    if m is None:                                  # no drawing for this layout: draw it from the lap itself
        return {"map": _gps_map(_df(name), s, clean, info)}
    m["dot"] = _map_dot(df=_df(name), summary=s, clean=clean, m=m)
    if m["dot"] is None:                           # the lap does not fit this drawing: draw the layout from the lap itself
        m = _gps_map(_df(name), s, clean, info) or m
    return {"map": m}


def _session_bytes(g: dict) -> int:
    total = 0
    for fn in g["files"]:
        try:
            total += (FOLDER / fn).stat().st_size
        except OSError:
            pass
    return total


_RECORDED = re.compile(r"(\d{4})-(\d\d)-(\d\d)T(\d\d)_(\d\d)_(\d\d)Z")


def _label(g: dict) -> str:
    """A readable name for a session: "Tue 18 Nov 20:28 · Le Mans · Practice · McLaren 720S LMGT3 Evo · 2 runs".

    Built from what the file itself says (track, session type, car, when it started, in your own time zone).
    Falls back to the file name for a file that can't be read."""
    f = g.get("facts")
    if not f:
        return g["label"]
    track, _layout, car, kind = f["who"]
    parts = []
    try:
        first = facts(FOLDER / g["files"][0]) or f              # when the session STARTED (its first run)
    except OSError:
        first = f
    m = _RECORDED.search(first["recorded"] or "") or _RECORDED.search(g["files"][0])
    if m:
        started = datetime(*map(int, m.groups()), tzinfo=timezone.utc).astimezone()
        parts.append(f"{started:%a} {started.day} {started:%b} {started:%H:%M}")
    if track:
        parts.append(catalog.short_name(track))
    if kind:
        parts.append(kind)
    model = car_model(str(FOLDER / g["files"][-1]), car or "") if car else ""
    if model or car:
        parts.append(model or car)
    if g["runs"] > 1:
        parts.append(f"{g['runs']} runs")
    return " · ".join(parts)


def api_sessions(everything: bool = False) -> list[dict]:
    """Sessions, newest first. The picker shows the newest 50; the "Manage sessions" window asks for all of them."""
    gs = groups(FOLDER)
    return [{"name": g["id"], "label": _label(g), "mtime": g["mtime"], "runs": g["runs"], "bytes": _session_bytes(g)}
            for g in (gs if everything else gs[:50])]


def api_delete_sessions(names: list[str]) -> dict:
    """Delete whole sessions (every run of each) from the game's Telemetry folder, on the user's request.

    Same safeguards as the automatic clean-up: only *.duckdb files inside the Telemetry folder, names come from
    the list of sessions (never a path from the page), your personal best is checked first so it is never lost,
    and a file the game has open is left alone and reported."""
    deleted, failed, freed = 0, [], 0
    wanted = [str(n) for n in names][:1000]
    by_id = {g["id"]: g for g in groups(FOLDER)}
    for name in wanted:
        g = by_id.get(Path(name).name)
        if not g:
            failed.append({"name": name, "reason": "not found (already deleted?)"})
            continue
        try:
            api_laps(g["id"])                      # keeps your personal best up to date before the data goes away
        except Exception:
            pass                                   # unreadable file: nothing worth keeping in it
        with _lock:
            _cache.clear()
            _info_cache.clear()
        problem = None
        for fn in g["files"]:
            path = FOLDER / Path(fn).name
            if path.suffix != ".duckdb" or path.parent != FOLDER:
                continue
            try:
                size = path.stat().st_size
                path.unlink()
                wal = path.with_name(path.name + ".wal")
                if wal.is_file():
                    wal.unlink()
                freed += size
            except FileNotFoundError:
                pass
            except OSError as e:
                problem = e.strerror or str(e)
        if problem:
            failed.append({"name": _label(g), "reason": "could not be deleted: " + problem + " (is the game still using it?)"})
        else:
            deleted += 1
    return {"deleted": deleted, "failed": failed, "freed_mb": round(freed / 1048576, 1)}


_info_cache: dict[tuple[str, float], dict] = {}


def _info(name: str) -> dict:
    p = _files(name)[-1]                  # car, setup and weather of the latest run
    key = (p.name, p.stat().st_mtime)
    with _lock:
        if key not in _info_cache:
            _info_cache.clear()
            _info_cache[key] = read_info(str(p))
        d = _info_cache[key]
        if not d["info"]["car_model"]:   # results file may be written after the session starts
            d["info"]["car_model"] = car_model(str(p), d["info"]["car"])
        return d


def _zones_for(name: str):
    """Function giving the turns of this session's track (from its saved profile) for any lap grid."""
    info = _info(name)["info"]
    return lambda ref, length: profiles.zones_for(info, ref, length)


def _lap_grid(df, summary, lap: int):
    length = float(summary.loc[lap, "length_m"])
    return lap_on_grid(df, lap, length, grid_points(length))


def _auto_best(name: str, df, summary) -> dict:
    """Keep your personal best for this track and car up to date (see saved_laps). Only laps that count."""
    info = _info(name)["info"]
    clean = summary[summary["complete"]]
    if not info.get("track") or not (info.get("car_model") or info.get("car")):
        return {"updated": False}
    here = group_for(FOLDER, name)["id"]
    best = next((r for r in saved_laps.list_saved(info) if r["kind"] == "best"), None)
    if best and best["session"] == here and best["lap"] not in clean.index:
        saved_laps.discard_best(info)                       # saved from a lap that does not count (e.g. track limits)
        best = None
    if clean.empty:
        return {"updated": False}
    lap = int(clean["lap_time"].idxmin())
    lap_time = float(clean.loc[lap, "lap_time"])
    files = _files(name)
    if best and best["session"] == here and best["lap"] == lap:
        if abs(best["lap_time"] - lap_time) > 0.0005 or not best.get("has_gear") or not best.get("has_line") or not best.get("has_setup"):
            saved_laps.refresh_best(info, here, lap, lap_time, _lap_grid(df, summary, lap), _lap_setup(files, lap_time))   # same lap: exact time, gear data, setup
        return {"updated": False, "lap_time": lap_time}
    if best and lap_time >= best["lap_time"] - 0.0005:       # nothing faster: skip the resampling
        return {"updated": False, "lap_time": best["lap_time"]}
    result = saved_laps.update_best(info, here, lap, lap_time, _lap_grid(df, summary, lap), _lap_setup(files, lap_time))
    if result.get("updated"):
        try:
            rivals.prune(info, lap_time)                    # rivals only count while they are faster than your best
        except Exception:
            pass
    return result


def _pb_and_rivals(name: str, df, summary) -> dict:
    pb = _auto_best(name, df, summary)
    try:
        rivals.resolve_pending()                # rival laps that waited for your best lap on this track
    except Exception:
        pass
    return pb


def api_laps(name: str) -> dict:
    df = _df(name)
    s = lap_summary(df)
    try:
        surface.update(_info(name)["info"], group_for(FOLDER, name)["id"], df, s)     # what this session's laps show of the track
    except Exception:
        pass                                                                          # never let this stop the laps from loading
    laps = [{"lap": int(i), "time": r.lap_time, "time_str": fmt_time(r.lap_time),
             "top_speed": round(r.top_speed, 1), "pit": bool(r.pit), "complete": bool(r.complete),
             "valid": bool(r.valid), "reason": r.reason, "comparable": bool(r.length_ok)}
            for i, r in s.iterrows()]
    full = sorted((l for l in laps if l["complete"]), key=lambda l: l["time"])
    return {"laps": laps,
            "ref": full[0]["lap"] if full else None,
            "other": full[1]["lap"] if len(full) > 1 else None,
            "pb": _pb_and_rivals(name, df, s)}


def api_turns(name: str, include_saved: bool = True) -> dict:
    """Consistency per turn. Saved laps from other sessions count as extra passes; this session's own
    saved laps are skipped, because those laps are already part of the session."""
    saved = []
    if include_saved:
        info, here = _info(name)["info"], group_for(FOLDER, name)["id"]
        for meta in saved_laps.list_saved(info):
            if meta["kind"] in ("best", "pinned") and meta.get("session") != here:
                loaded = saved_laps.load_grid(info, meta["id"])
                if loaded:
                    saved.append(loaded)
    r = turn_stats(_df(name), _zones_for(name), saved)
    if not r:
        return {"error": "Need at least one complete, clean lap (no pit visit)."}
    r["track_check"] = profiles.track_check(_info(name)["info"])        # (the profile exists once turn_stats has run)
    return r


_TRACE = ("speed", "throttle", "brake", "steer", "gear", "time")      # what the charts draw (and, with time, what the track view plays back)
_LINE = ("x", "y", "lateral", "edge")                           # the racing line (metres), for the track view


def _line(a) -> list:
    return [None if not np.isfinite(v) else round(float(v), 2) for v in np.asarray(a, float)]


def _align(grid: pd.DataFrame, dist: np.ndarray, ref_len: float) -> pd.DataFrame:
    """A saved lap resampled onto the distance points of another lap (the laps may differ a little in measured length)."""
    own_len = float(grid["dist"].iloc[-1])
    at = np.asarray(dist, float) * (own_len / ref_len)
    out = {"dist": np.asarray(dist, float)}
    for c in grid.columns:
        if c != "dist":
            out[c] = np.interp(at, grid["dist"].to_numpy(float), grid[c].to_numpy(float))
    return pd.DataFrame(out)


def _sectors(info: dict, ref_df, other, saved_ids: list) -> list | None:
    """Time in each of the three sectors for both laps, using the sector lines found from the rival laps recorded on this track."""
    lines = []
    for rec in saved_laps.rival_records(info):
        if rec.get("sector_m") and rec.get("length_m"):
            lines.append([x / rec["length_m"] for x in rec["sector_m"]])
    if not lines:
        return None
    frac = np.median(np.array(lines), axis=0)
    ref_len = float(ref_df["dist"].iloc[-1])
    cuts = [float(f) * ref_len for f in frac]
    out = []
    for n, (a, b_) in enumerate([(0.0, cuts[0]), (cuts[0], cuts[1]), (cuts[1], ref_len)], 1):
        def span(g):
            t = lambda x: float(np.interp(x, g["dist"].to_numpy(float), g["time"].to_numpy(float)))
            return t(b_) - t(a)
        r, o = span(ref_df), span(other)
        out.append({"n": n, "ref": round(r, 3), "other": round(o, 3), "diff": round(o - r, 3)})
    return out


def api_compare(name: str, ref: str, other: str) -> dict:
    """Compare the lap `other` with `ref`. Each is a lap number of this session or "saved:<id>" (your best, a lap you saved, a friend's or a rival's)."""
    df = _df(name)
    info = _info(name)["info"]
    summary = lap_summary(df)            # any full-length lap can be compared, also out laps and laps that did not count
    for item in (ref, other):
        if not str(item).startswith("saved:"):
            lap = int(item)
            if lap not in summary.index or not bool(summary.loc[lap, "length_ok"]):
                raise ValueError(f"Lap {lap} is not a full lap, so it can't be compared.")

    def saved_grid(item: str):
        loaded = saved_laps.load_grid(info, item[len("saved:"):])
        if not loaded:
            raise ValueError("That saved lap no longer exists.")
        return loaded

    saved, single = None, False
    ref_saved, other_saved = ref.startswith("saved:"), other.startswith("saved:")
    if not ref_saved and not other_saved:
        ref_df, other_df, delta = compare_laps(df, int(ref), int(other))
        single = int(ref) == int(other)       # one lap on its own, nothing to compare against
    elif ref_saved and not other_saved:
        saved, grid = saved_grid(ref)
        ref_df, other_df, delta = compare_to_grid(df, int(other), grid)
    else:
        if ref_saved:
            saved, ref_df = saved_grid(ref)
        else:
            length = float(summary.loc[int(ref), "length_m"])
            ref_df = lap_on_grid(df, int(ref), length, grid_points(length))
        _, other_grid = saved_grid(other)
        other_df = _align(other_grid, ref_df["dist"].to_numpy(float), float(ref_df["dist"].iloc[-1]))
        delta = other_df["time"] - ref_df["time"]
    zones = _zones_for(name)(ref_df, float(ref_df["dist"].iloc[-1]))
    return {
        "dist": _clean(ref_df["dist"]),
        "delta": _clean(delta),
        "ref": {**{c: _clean(ref_df[c]) for c in _TRACE if c in ref_df.columns}, **{c: _line(ref_df[c]) for c in _LINE if c in ref_df.columns}},
        "other": {**{c: _clean(other_df[c]) for c in _TRACE if c in other_df.columns}, **{c: _line(other_df[c]) for c in _LINE if c in other_df.columns}},
        "single": single,
        "saved": saved,
        "final_delta": round(float(delta.iloc[-1]), 3),
        "corners": corner_report(ref_df, other_df, delta, zones),
        "sectors": None if single else _sectors(info, ref_df, other_df, [ref, other]),
    }


def api_setup(name: str, a: str | None = None, b: str | None = None) -> dict:
    """Setup report for the car and track of session `name`: every run (telemetry file) of that car on that track, what each showed,
    findings for the run on screen, and a comparison of two runs. Only your own laps are used (see analysis/setup_report.py)."""
    info = _info(name)["info"]
    last = _files(name)[-1]
    f0 = facts(last)
    if not f0:
        raise ValueError("This session can't be read yet.")
    who = f0["who"][:3]
    mine = {fn for fn in group_for(FOLDER, name)["files"]}
    runs, skipped = [], 0
    for g in groups(FOLDER):
        for fn in g["files"]:
            path = FOLDER / fn
            fa = facts(path)
            if not fa or fa["who"][:3] != who:
                continue
            m = setup_report.run_metrics(path)
            if m and m["n_laps"] >= setup_report.MIN_LAPS:
                runs.append(m)
            else:
                skipped += 1
    runs.sort(key=lambda r: r["recorded"])
    if not runs:
        raise ValueError("Need at least %d clean laps in one run of this car on this track." % setup_report.MIN_LAPS)
    ids = {}
    for r in runs:                                   # runs with the same setup get the same letter
        ids.setdefault(setup_report.setup_id(r["setup"]), chr(65 + len(ids) % 26))
    by_id = {r["id"]: r for r in runs}
    shown = by_id.get(b) or next((r for r in reversed(runs) if r["id"] in mine), runs[-1])
    earlier = [r for r in runs if r["recorded"] < shown["recorded"] and setup_report.setup_id(r["setup"]) != setup_report.setup_id(shown["setup"])]
    base = by_id.get(a) or (earlier[-1] if earlier else None)
    prof = profiles.load(info)
    turns = (prof or {}).get("turns")
    rows = [{"id": r["id"], "recorded": r["recorded"], "setup": ids[setup_report.setup_id(r["setup"])], "n_laps": r["n_laps"], "best": r["best"],
             "median": r["median"], "tc_s_per_lap": r["tc_s_per_lap"], "abs_pct": r["abs_pct"], "session": r["session_type"],
             "tc": setup_report.setting(r, "VM_TRACTIONCONTROLMAP"), "abs": setup_report.setting(r, "VM_ANTILOCKBRAKESYSTEMMAP")} for r in runs]
    detail = {k: shown.get(k) for k in ("tyre_inner_minus_outer", "tyre_temp", "pressure", "brake_temp_mean", "brake_temp_peak")}
    return {"car": who[2], "track": who[0], "layout": who[1], "runs": rows, "skipped": skipped, "shown": shown["id"],
            "base": base["id"] if base else None, "findings": setup_report.findings(shown, turns), "detail": detail,
            "tc_spots": setup_report.spots(shown["tc_bins"], shown["n_laps"], turns, shown.get("length")),
            "compare": setup_report.compare(base, shown) if base else None}


def api_edges(name: str) -> dict:
    """The two edges of the track in metres, from all laps of the session (see analysis/edges.py)."""
    df, s, info = _df(name), lap_summary(_df(name)), _info(name)["info"]
    try:
        surface.update(info, group_for(FOLDER, name)["id"], df, s)
        e = surface.edges(info)                      # everything learned about this track so far, over all sessions
    except Exception:
        e = None
    return {"edges": e or track_edges(df, s)}


def api_saved(name: str) -> dict:
    return {"items": saved_laps.list_saved(_info(name)["info"])}


def api_save_lap(name: str, lap: int) -> dict:
    df = _df(name)
    s = lap_summary(df)
    if lap not in s.index or not bool(s.loc[lap, "complete"]):
        raise ValueError("Only clean laps can be saved (a full lap without a pit visit).")
    saved = saved_laps.pin(_info(name)["info"], group_for(FOLDER, name)["id"], lap, float(s.loc[lap, "lap_time"]), _lap_grid(df, s, lap),
                           _lap_setup(_files(name), float(s.loc[lap, "lap_time"])))
    return {"saved": saved, **api_saved(name)}


def _file_name(parts: list[str]) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]+", "", " - ".join(p for p in parts if p)).strip()[:120] or "SimTracker lap"


def api_export(name: str, lap: str | None, saved: str | None, driver: str) -> tuple[bytes, str]:
    """A lap as a small file to send to a friend: (file content, file name). Either a lap of this session
    (only laps that count) or one of your own saved laps."""
    info = _info(name)["info"]
    if saved:
        data = saved_laps.export_saved(info, saved, driver)
        if not data:
            raise ValueError("That saved lap could not be found.")
    else:
        df = _df(name)
        s = lap_summary(df)
        n = int(lap)
        if n not in s.index or not bool(s.loc[n, "complete"]):
            raise ValueError("Only laps that count can be shared.")
        data = saved_laps.export_grid(info, float(s.loc[n, "lap_time"]), _lap_grid(df, s, n), driver)
    label = f"{fmt_time(data['lap_time'])}".replace(":", "-")
    fname = _file_name(["SimTracker", catalog.short_name(data["track"]), label, data["driver"]]) + ".simlap"
    return json.dumps(data, separators=(",", ":")).encode("utf-8"), fname


def api_import(name: str, data) -> dict:
    """Keep a lap file from a friend (see saved_laps.import_lap) and say whether it belongs to the track on screen."""
    meta = saved_laps.import_lap(data)
    here = _info(name)["info"]
    same = track_key(here) == track_key(meta)
    return {"imported": meta, "same_track": same, "items": api_saved(name)["items"]}


def api_delete_saved(name: str, rec_id: str) -> dict:
    if not saved_laps.delete(_info(name)["info"], rec_id):
        raise ValueError("Could not delete that lap. (Only laps you saved yourself can be deleted.)")
    return api_saved(name)


def api_rename_turn(name: str, number: int, new_name: str) -> dict:
    if not profiles.rename_turn(_info(name)["info"], number, new_name):
        raise ValueError("No such turn.")
    return {"ok": True}


def _file_setup(path: Path) -> dict:
    """The setup a telemetry file was driven with."""
    con, tmp = open_db(str(path))
    try:
        row = con.execute("SELECT value FROM metadata WHERE key = 'CarSetup'").fetchone()
    finally:
        con.close()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    return setup_model.from_telemetry(row[0] if row else "")


def _lap_setup(files: list[Path], lap_time: float) -> dict:
    """The setup of the run that contains a lap with this time. A session can hold several runs with different setups, so with
    more than one file the lap times of each run are compared; if none matches, the latest run's setup is used."""
    try:
        if len(files) == 1:
            return _file_setup(files[0])
        for p in reversed(files):
            r = _run_info(p)
            if r and any(abs(t - lap_time) < 0.002 for t in r["laps"]):
                return r["setup"]
        return _file_setup(files[-1])
    except Exception:
        return {}


_run_cache: dict = {}
_RECORDED_RE = re.compile(r"(\d{4})-(\d\d)-(\d\d)T(\d\d)_(\d\d)_(\d\d)Z")


def _recorded_utc(f0: dict, name: str):
    m = _RECORDED_RE.search((f0 or {}).get("recorded") or "") or _RECORDED_RE.search(name)
    return datetime(*map(int, m.groups()), tzinfo=timezone.utc) if m else None


def _run_info(p: Path) -> dict | None:
    """One telemetry file as the setup test needs it: when it was recorded, its setup, and its clean lap times."""
    st = p.stat()
    key = (p.name, st.st_mtime, st.st_size)
    if key in _run_cache:
        return _run_cache[key]
    out = None
    try:
        f0 = facts(p)
        con, tmp = open_db(str(p))
        try:
            row = con.execute("SELECT value FROM metadata WHERE key = 'CarSetup'").fetchone()
        finally:
            con.close()
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
        sm = lap_summary(load_lmu_files([str(p)]))
        laps = [float(x) for x in sm[sm["complete"]]["lap_time"]]
        clean_len = sm[sm["complete"]]["length_m"]
        out = {"name": p.name, "recorded": _recorded_utc(f0, p.name), "who": (f0 or {}).get("who"),
               "setup": setup_model.from_telemetry(row[0] if row else ""), "laps": laps, "length": float(clean_len.median()) if len(clean_len) else None}
    except Exception:
        out = None
    for k in [k for k in _run_cache if k[0] == p.name]:
        del _run_cache[k]
    _run_cache[key] = out
    return out


_overlay = {"proc": None}


def api_overlay(action: str | None = None) -> dict:
    """Start or stop the overlay window (a separate process, see overlay.py), or just say whether it is running."""
    proc = _overlay["proc"]
    running = proc is not None and proc.poll() is None
    if action == "start" and not running:
        cmd = [sys.executable, "--overlay"] if FROZEN else [sys.executable, str(Path(__file__).resolve().parent.parent / "dashboard.py"), "--overlay"]
        cmd += ["--parent", str(os.getpid())]                      # the overlay closes itself when this program closes
        _overlay["proc"] = subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        running = True
    elif action == "stop" and running:
        proc.terminate()
        running = False
    return {"running": running}


atexit.register(lambda: api_overlay("stop"))


STATIC = {   # URL -> (file in web/, content type)
    "/": ("laps.html", "text/html; charset=utf-8"),
    "/turns": ("turns.html", "text/html; charset=utf-8"),
    "/setup": ("setup.html", "text/html; charset=utf-8"),
    "/common.js": ("common.js", "application/javascript"),
    "/common.css": ("common.css", "text/css"),
    "/plotly.min.js": ("plotly.min.js", "application/javascript"),
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, status: int = 200, headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")   # always serve the latest page code
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in STATIC:
                fname, ctype = STATIC[u.path]
                return self._send((WEB_DIR / fname).read_bytes(), ctype)
            if u.path == "/api/export":
                body, fname = api_export(q["file"], q.get("lap"), q.get("saved"), q.get("name", ""))
                return self._send(body, "application/octet-stream", 200, {"Content-Disposition": f'attachment; filename="{fname}"'})
            if u.path == "/api/sessions":
                data = api_sessions(q.get("all") == "1")
            elif u.path == "/api/trackmap":
                data = api_trackmap(q["file"])
            elif u.path == "/api/status":
                data = api_status()
            elif u.path == "/api/update":
                data = updater.status()
            elif u.path == "/api/info":
                data = _info(q["file"])
            elif u.path == "/api/turns":
                data = api_turns(q["file"], q.get("saved", "1") != "0")
            elif u.path == "/api/laps":
                data = api_laps(q["file"])
            elif u.path == "/api/compare":
                data = api_compare(q["file"], q["ref"], q["other"])
            elif u.path == "/api/edges":
                data = api_edges(q["file"])
            elif u.path == "/api/saved":
                data = api_saved(q["file"])
            elif u.path == "/api/rivals":
                data = rivals.status()
            elif u.path == "/api/overlay":
                data = api_overlay()
            elif u.path == "/api/whatsnew":
                data = whatsnew.status(q.get("all") == "1")
            elif u.path == "/api/setup":
                data = api_setup(q["file"], q.get("a"), q.get("b"))
            else:
                return self._send(b"not found", "text/plain", 404)
            self._send(json.dumps(data).encode(), "application/json")
        except Exception as e:
            self._send(json.dumps({"error": str(e)}).encode(), "application/json", 500)

    def do_POST(self):
        """Changes: save/delete a lap, rename a turn. Only accepted from this dashboard's own pages."""
        try:
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"):
                return self._send(b'{"error": "forbidden"}', "application/json", 403)
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._send(b'{"error": "expected JSON"}', "application/json", 415)
            size = int(self.headers.get("Content-Length") or 0)
            path = urlparse(self.path).path
            if size > (3_000_000 if path == "/api/shared/import" else 100_000):
                return self._send(b'{"error": "too large"}', "application/json", 413)
            body = json.loads(self.rfile.read(size) or b"{}")
            if path == "/api/saved/save":
                data = api_save_lap(body["file"], int(body["lap"]))
            elif path == "/api/saved/delete":
                data = api_delete_saved(body["file"], str(body["id"]))
            elif path == "/api/update/install":
                updater.install()                          # downloads, then this program closes and starts again
                data = {"ok": True}
            elif path == "/api/shared/import":
                data = api_import(body["file"], body.get("lap"))
            elif path == "/api/sessions/delete":
                data = api_delete_sessions(list(body.get("names") or []))
            elif path == "/api/overlay":
                data = api_overlay(str(body.get("action", "")))
            elif path == "/api/whatsnew/seen":
                whatsnew.mark_seen()
                data = {"ok": True}
            elif path == "/api/turns/rename":
                data = api_rename_turn(body["file"], int(body["number"]), str(body.get("name", "")))
            else:
                return self._send(b"not found", "text/plain", 404)
            self._send(json.dumps(data).encode(), "application/json")
        except Exception as e:
            self._send(json.dumps({"error": str(e)}).encode(), "application/json", 500)

    def log_message(self, *a):            # keep the console quiet
        pass


def _already_running() -> bool:
    """True when a SimTracker dashboard already answers on our port (double-clicked twice)."""
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=1):
            return True
    except OSError:
        return False


def _stop_with(message: str) -> None:
    print("\n" + message)
    if FROZEN:                                   # the double-click program: keep the window open so the text can be read
        try:
            input("\nPress Enter to close this window.")
        except EOFError:
            pass
    sys.exit(1)


def main():
    global FOLDER
    url = f"http://localhost:{PORT}"
    if _already_running():
        print(f"SimTracker is already running, opening {url}")
        webbrowser.open(url)
        return
    try:
        FOLDER = find_telemetry_dir()
    except TelemetryNotFound:
        FOLDER = ask_for_folder()
        if FOLDER is None:
            _stop_with("No Telemetry folder chosen.\n"
                       "Start SimTracker again and pick the folder called Telemetry, inside the game's UserData folder\n"
                       "(or set the LMU_TELEMETRY_DIR environment variable).")
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        _stop_with(f"Could not start on port {PORT}: {e}\nSomething else is using that port.")
    print("SimTracker is running. Keep this window open while you use it; close it to stop.")
    print(f"Reading sessions from {FOLDER}\nDashboard: {url}  (Ctrl+C to stop)")
    if "--after-update" not in sys.argv:           # after an update the page that is already open reloads itself
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    updater.leftovers_cleanup()
    updater.start_background()
    threading.Thread(target=_cleanup_loop, daemon=True).start()
    threading.Thread(target=backfill_surface, daemon=True).start()
    threading.Thread(target=backfill_best_setups, daemon=True).start()
    rivals.start()                                  # listens to the game for the fastest laps of other drivers (see rivals.py)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
