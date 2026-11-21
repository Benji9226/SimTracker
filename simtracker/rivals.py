"""Rival laps: the fastest laps of other drivers, read live from the game while you are in a session.

Le Mans Ultimate has a small web interface on this computer (http://127.0.0.1:6397, settings: "WebUI port"). While a session runs,
`/rest/watch/standings` lists every car with its position, speed, lap time and so on, about 5 times a second. This module listens to
that (nothing is sent anywhere) and, for the cars of YOUR class, cuts the stream into laps. A lap is kept only when

    - it is a full lap the game counted (it has an official lap time, and the game's lap flag said "counts" all the way round),
    - the car did not visit the pits during it, and no yellow flag was out for the car,
    - it is faster than YOUR all-time best for this track and class (nothing else is worth comparing against), and
    - it is one of the 5 fastest such laps (kept per track and class; slower ones are deleted).

Kept laps are stored like laps from a friend (storage/saved_laps.py, `_friends/`), so the laps page can compare to them as it does to any
other lap. What is stored: lap time, speed, racing line and estimated braking. NO name, Steam ID, car ID or team name is read or stored
(a rival is only "Rival #<car number>").

What the game does not give for other cars: throttle, brake and steering. Speed along the lap, and where the car is on the track, are exact enough
to show the racing line and the speed; the BRAKE is estimated from how fast the car slows down (tested on your own laps: the braking point is
typically within about 4 m, 8 times out of 10 within 7 m); throttle is a guess (off/on) and steering is not known (it is stored as 0).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.request

import numpy as np

from simtracker.analysis.laps import grid_points
from simtracker.analysis.edges import _right_normal as right_normal
from simtracker.paths import PENDING_DIR, SAVED_DIR
from simtracker.storage import saved_laps
from simtracker.storage.profiles import slug

BASE = "http://127.0.0.1:6397/rest/watch/"
KEEP_TOP = 5
POLL_S = 0.08
BRAKE_DECEL = 8.0          # m/s2: slowing harder than this for two samples in a row is braking
BRAKE_ONSET_SHIFT_M = 6.0  # the slowing is seen about this much after the pedal is pressed (measured on real laps), so the onset is moved back
MIN_SPEED = 25.0           # m/s: braking from slower than this is not looked for (the estimate is for the big stops)
MAX_GAP_S = 1.5            # a hole in the readings this long makes the lap unusable
PENDING_KEEP = 12          # without a best lap of yours to compare with, this many of the fastest rival laps wait per track and class
PENDING_DAYS = 21          # ... and are forgotten after this many days


def same_class(a: str, b: str) -> bool:
    a, b = (a or "").lower().strip(), (b or "").lower().strip()
    return len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a))


# ------------------------------------------------------------------ what the game says, reduced to what is needed
def normalize(c: dict) -> dict:
    """One car of `standings` as a small dict. Names, IDs and teams are not copied."""
    pos, vel = c.get("carPosition") or {}, c.get("carVelocity") or {}
    return {
        "id": str(c.get("carNumber") or c.get("slotID")), "carClass": c.get("carClass"), "player": bool(c.get("player")),
        "lapDistance": c.get("lapDistance"), "lapsCompleted": c.get("lapsCompleted"), "timeIntoLap": c.get("timeIntoLap"),
        "v": vel.get("velocity"), "pathLateral": c.get("pathLateral"), "trackEdge": c.get("trackEdge"),
        "lastLapTime": c.get("lastLapTime"), "lastSectorTime1": c.get("lastSectorTime1"), "lastSectorTime2": c.get("lastSectorTime2"), "bestLapTime": c.get("bestLapTime"), "countLapFlag": c.get("countLapFlag"),
        "pitting": bool(c.get("pitting")), "pitState": c.get("pitState"), "inGarageStall": bool(c.get("inGarageStall")),
        "underYellow": bool(c.get("underYellow")),
    }


def _bad_sample(s: dict) -> bool:
    return (s.get("countLapFlag") != "COUNT_LAP_AND_TIME" or s.get("pitting") or s.get("inGarageStall")
            or s.get("pitState") in ("ENTERING", "EXITING", "STOPPED") or s.get("underYellow"))


# ------------------------------------------------------------------ a lap from samples to the stored form
def estimate_brake(t: np.ndarray, v: np.ndarray, d: np.ndarray) -> list[tuple[float, float]]:
    """Braking zones (start distance, end distance) from the speed alone."""
    zones = []
    if len(v) < 12:
        return zones
    a = np.diff(v) / np.maximum(np.diff(t), 1e-3)
    j = 1
    while j < len(a) - 3:
        if a[j] < -BRAKE_DECEL and a[j + 1] < -BRAKE_DECEL and a[j - 1] >= -BRAKE_DECEL and v[j] > MIN_SPEED and v[j] - v[min(len(v) - 1, j + 5)] > 8:
            start = d[j] + 0.5 * v[j] * (t[j + 1] - t[j]) - BRAKE_ONSET_SHIFT_M
            k = j + 1
            while k < len(a) and a[k] < -2.0:
                k += 1
            zones.append((start, d[min(k, len(d) - 1)]))
            j = k + 1
            continue
        j += 1
    return zones


def build_grid(samples: list[dict], lap_time: float, ref: dict) -> dict | None:
    """The lap on the same distance grid as your best lap (`ref`: dist, x, y, lateral, edge ...), in the stored form, or None when it does not fit."""
    s = sorted(samples, key=lambda q: q["timeIntoLap"])
    d = np.array([q["lapDistance"] for q in s], float)
    t = np.array([q["timeIntoLap"] for q in s], float)
    v = np.array([q["v"] for q in s], float)
    lat = np.array([q["pathLateral"] for q in s], float)
    edge = np.array([q["trackEdge"] for q in s], float)
    keep = np.r_[True, np.diff(d) > 0]
    d, t, v, lat, edge = d[keep], t[keep], v[keep], lat[keep], edge[keep]
    gdist = np.array(ref["dist"], float)
    length = float(gdist[-1])
    if len(d) < 60 or abs(d[-1] - length) > 0.04 * length or d[0] > 0.04 * length or np.diff(t).max() > MAX_GAP_S:
        return None
    d_ext = np.r_[0.0, d, length]                                      # the first and the last reading are a few metres from the line
    t_ext = np.r_[max(0.0, t[0] - d[0] / max(v[0], 1.0)), t, t[-1] + (length - d[-1]) / max(v[-1], 1.0)]
    time_g = np.interp(gdist, d_ext, t_ext)
    time_g = (time_g - time_g[0]) * (lap_time / (time_g[-1] - time_g[0]))     # starts at 0, ends at the game's own lap time
    speed = np.interp(gdist, d, v) * 3.6
    lat_g, edge_g = np.interp(gdist, d, lat), np.interp(gdist, d, edge)
    rx, ry = right_normal(np.array(ref["x"], float), np.array(ref["y"], float))
    x = np.array(ref["x"], float) + (lat_g - np.array(ref["lateral"], float)) * rx
    y = np.array(ref["y"], float) + (lat_g - np.array(ref["lateral"], float)) * ry
    brake = np.zeros_like(gdist)
    for a, b in estimate_brake(t, v, d):
        brake[(gdist >= a) & (gdist <= b)] = 0.8
    throttle = np.where(np.gradient(speed, gdist) > -0.02, 1.0, 0.0) * (brake == 0)
    return {"dist": gdist, "time": time_g, "speed": speed, "throttle": throttle, "brake": brake, "steer": np.zeros_like(gdist),
            "x": x, "y": y, "lateral": lat_g, "edge": edge_g}


def sector_lines(grid: dict, s1, s2_total) -> list | None:
    """Where on the lap the sector lines are (metres): the distances at which this lap's own clock showed its official sector times.
    The game gives sector 2 as a running total (sector 1 + sector 2)."""
    if not s1 or not s2_total or s1 <= 0 or s2_total <= s1:
        return None
    return [round(float(np.interp(x, grid["time"], grid["dist"])), 1) for x in (s1, s2_total)]


# ------------------------------------------------------------------ your best lap = the bar and the map under the rivals' line
_ref_cache: dict = {}


def find_reference(track_name: str, car_class: str) -> dict | None:
    """Your fastest saved lap (any car of this class) on the track the game calls `track_name` (the layout name): {info, lap_time, grid...}."""
    best = None
    want = slug(track_name or "")
    for path in SAVED_DIR.glob("*/*/best.json") if SAVED_DIR.is_dir() else []:
        try:
            key = (str(path), path.stat().st_mtime)
            if key not in _ref_cache:
                _ref_cache[key] = json.loads(path.read_text(encoding="utf-8"))
            rec = _ref_cache[key]
        except (OSError, ValueError):
            continue
        names = {slug(rec.get("layout") or ""), slug(rec.get("track") or "")}
        if want not in names or not same_class(rec.get("car_class", ""), car_class) or "x" not in rec.get("grid", {}):
            continue
        if best is None or rec["lap_time"] < best["lap_time"]:
            best = rec
    if not best:
        return None
    return {"info": {"track": best["track"], "layout": best["layout"], "car_class": best["car_class"]}, "lap_time": best["lap_time"],
            "length_m": best["length_m"], **best["grid"]}


def make_record(ref: dict, cid: str, samples: list, lap_time: float, s1=None, s2=None) -> dict | None:
    """A stored rival lap (see saved_laps) built from the readings of one lap, or None when it does not fit the track."""
    grid = build_grid(samples, lap_time, ref)
    if grid is None:
        return None
    info = ref["info"]
    rec = {"id": _rival_id(info, lap_time, grid), "kind": "friend", "rival": True, "driver": f"Rival #{cid}", "track": info["track"],
           "layout": info["layout"], "car": "", "car_model": "", "car_class": info.get("car_class", ""), "session": "", "lap": 0,
           "lap_time": round(lap_time, 3), "length_m": round(float(grid["dist"][-1]), 1),
           "date": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
           "grid": {k: [round(float(x), 3) for x in vals] for k, vals in grid.items()}}
    lines = sector_lines(grid, s1, s2)
    if lines:
        rec["sector_m"] = lines
    return rec


# ------------------------------------------------------------------ rival laps heard before you have a best lap on the track
def _pending_path(track: str, cls: str):
    return PENDING_DIR / f"{slug(track)}__{slug(cls)}.json"


def add_pending(track: str, cls: str, cid: str, samples: list, lap_time: float, s1, s2) -> None:
    """Keep the raw readings of a fast lap until your own best lap on this track exists (then `resolve_pending` turns them into rival laps)."""
    path = _pending_path(track, cls)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"track": track, "class": cls, "laps": []}
    data["laps"].append({"id": cid, "time": round(lap_time, 3), "s1": s1, "s2": s2, "at": time.time(),
                         "samples": [[q["lapDistance"], q["timeIntoLap"], q["v"], q["pathLateral"], q["trackEdge"]] for q in samples]})
    seen, laps = set(), []
    for lap in sorted(data["laps"], key=lambda x: x["time"]):         # the fastest few only, each lap once
        if lap["time"] not in seen and time.time() - lap["at"] < PENDING_DAYS * 86400:
            seen.add(lap["time"])
            laps.append(lap)
    data["laps"] = laps[:PENDING_KEEP]
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def resolve_pending() -> int:
    """Turn waiting laps into rival laps wherever you now have a best lap for that track and class. Returns how many rival laps were kept."""
    kept = 0
    for path in list(PENDING_DIR.glob("*.json")) if PENDING_DIR.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
            continue
        old = time.time() - path.stat().st_mtime > PENDING_DAYS * 86400
        ref = find_reference(data.get("track", ""), data.get("class", ""))
        if ref is None:
            if old:
                path.unlink(missing_ok=True)
            continue
        recs = []
        for lap in data.get("laps", []):
            if lap["time"] >= ref["lap_time"] - 0.0005:
                continue
            samples = [{"lapDistance": a, "timeIntoLap": b, "v": c, "pathLateral": d, "trackEdge": e} for a, b, c, d, e in lap["samples"]]
            rec = make_record(ref, lap["id"], samples, lap["time"], lap.get("s1"), lap.get("s2"))
            if rec:
                recs.append(rec)
        before = {r["id"] for r in saved_laps.rival_records(ref["info"])}
        now_kept = {r["id"] for r in prune(ref["info"], ref["lap_time"], recs)}
        kept += len(now_kept - before)
        path.unlink(missing_ok=True)
    return kept


# ------------------------------------------------------------------ keeping the 5 fastest
def _rival_id(info: dict, lap_time: float, grid: dict) -> str:
    mid = len(grid["dist"]) // 2
    return "friend-" + hashlib.sha1(f"rival|{info.get('track')}|{info.get('layout')}|{lap_time}|{round(float(grid['speed'][mid]), 1)}".encode()).hexdigest()[:10]


def prune(info: dict, bar: float | None, add: list[dict] | None = None) -> list[dict]:
    """Keep the KEEP_TOP fastest rival laps that are faster than `bar` (your best), among the stored ones and `add`. Returns what is kept."""
    folder = saved_laps.friends_dir(info)
    have = saved_laps.rival_records(info)
    pool = {r["id"]: r for r in have}
    for r in add or []:
        pool[r["id"]] = r
    keep = sorted((r for r in pool.values() if bar is None or r["lap_time"] < bar - 0.0005), key=lambda r: r["lap_time"])[:KEEP_TOP]
    keep_ids = {r["id"] for r in keep}
    for r in keep:
        if r["id"] not in {h["id"] for h in have}:
            saved_laps.write_record(folder / f"{r['id']}.json", r)
    for h in have:
        if h["id"] not in keep_ids:
            try:
                (folder / f"{h['id']}.json").unlink()
            except OSError:
                pass
    return keep


# ------------------------------------------------------------------ the recorder
class Recorder:
    """Feed it the cars of `standings` again and again (`feed`); it keeps the fast laps of other drivers as described at the top."""

    def __init__(self):
        self.session = None
        self.cars: dict = {}          # car id -> {"lap": n, "samples": [...], "bad": bool}
        self.ref = None
        self.ref_checked = 0.0
        self.my_best_live = None
        self.cls = None
        self.saved = 0

    def reset(self, session):
        self.session, self.cars, self.my_best_live, self.ref, self.ref_checked = session, {}, None, None, 0.0

    def bar(self) -> float | None:
        vals = [x for x in (self.ref["lap_time"] if self.ref else None, self.my_best_live) if x]
        return min(vals) if vals else None

    def feed(self, cars: list[dict], session=None, now: float | None = None) -> int:
        """Returns how many new rival laps were kept by this call."""
        if session != self.session:
            self.reset(session)
        now = now if now is not None else time.time()
        me = next((c for c in cars if c["player"]), None)
        if me:
            self.cls = me.get("carClass") or self.cls
            b = me.get("bestLapTime")
            if b and b > 0:
                self.my_best_live = b if self.my_best_live is None else min(self.my_best_live, b)
        if not self.cls:
            return 0
        if self.ref is None and now - self.ref_checked > 20 and session:
            self.ref_checked = now
            self.ref = find_reference(session[0], self.cls)
            if self.ref:
                self.saved += resolve_pending()
        made = []
        for c in cars:
            if c["player"] or not same_class(c.get("carClass"), self.cls) or c.get("lapDistance") is None:
                continue
            st = self.cars.get(c["id"])
            lap = c.get("lapsCompleted")
            if st is None or lap is None:
                self.cars[c["id"]] = st = {"lap": lap, "samples": [], "bad": True}   # joined mid lap: wait for the next full lap
            if lap != st["lap"]:
                if lap == st["lap"] + 1 and not st["bad"] and (c.get("lastLapTime") or 0) > 20:
                    made.append((c["id"], st["samples"], float(c["lastLapTime"]), c.get("lastSectorTime1"), c.get("lastSectorTime2")))
                st.update(lap=lap, samples=[], bad=bool(c.get("pitting") or c.get("inGarageStall")))
            if st["samples"] and c["timeIntoLap"] - st["samples"][-1]["timeIntoLap"] > MAX_GAP_S:
                st["bad"] = True
            if _bad_sample(c):
                st["bad"] = True
            if not st["samples"] or c["lapDistance"] != st["samples"][-1]["lapDistance"]:
                st["samples"].append(c)
        kept = 0
        bar = self.bar()
        for cid, samples, lap_time, s1, s2 in made:
            if len(samples) < 60 or (bar is not None and lap_time >= bar - 0.0005):
                continue                                                      # not faster than your best (or not a whole lap)
            if self.ref is None:
                if session and session[0]:
                    add_pending(session[0], self.cls, cid, samples, lap_time, s1, s2)     # no best lap of yours yet: wait for it
                continue
            if lap_time < self.ref["lap_time"] * 0.9:                       # not believable next to your best: probably a wrong reading
                continue
            rec = make_record(self.ref, cid, samples, lap_time, s1, s2)
            if rec is None:
                continue
            before = {r["id"] for r in saved_laps.rival_records(self.ref["info"])}
            now_kept = {r["id"] for r in prune(self.ref["info"], bar, [rec])}
            kept += int(rec["id"] in now_kept and rec["id"] not in before)
        self.saved += kept
        return kept


# ------------------------------------------------------------------ talking to the game
def _get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=2) as r:
        return json.loads(r.read())


STATE = {"connected": False, "session": None, "cars": 0, "class": None, "have_best": False, "recorded": 0}


def status() -> dict:
    """What the recorder is doing now, for the dashboard: is the game being listened to, in which session, and what has been kept."""
    waiting = 0
    for p in PENDING_DIR.glob("*.json") if PENDING_DIR.is_dir() else []:
        try:
            waiting += len(json.loads(p.read_text(encoding="utf-8")).get("laps", []))
        except (OSError, ValueError):
            pass
    return {**STATE, "waiting": waiting}


def run_forever() -> None:
    rec = Recorder()
    info_t, session = 0.0, None
    while True:
        try:
            cars = [normalize(c) for c in _get("standings")]
            now = time.time()
            if now - info_t > 3:
                i = _get("sessionInfo")
                session, info_t = (i.get("trackName"), i.get("session")), now
            rec.feed(cars, session, now)
            STATE.update(connected=bool(cars), session=list(session) if session else None, cars=len(cars), have_best=rec.ref is not None, recorded=rec.saved)
            STATE["class"] = rec.cls
            time.sleep(POLL_S)
        except Exception:
            STATE.update(connected=False, cars=0)
            time.sleep(3)           # the game is not running (or not in a session): look again in a moment


def start() -> None:
    threading.Thread(target=run_forever, daemon=True, name="rivals").start()
