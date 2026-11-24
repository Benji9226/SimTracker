"""Setup report: what the car did in each run, measured from your own telemetry, and how two setups compare.

Everything here comes from the player's own laps (other drivers' laps carry none of these channels). One run is one telemetry file; a file
holds the setup it was driven with, so a run can be compared with any other run of the same car on the same track.

What is measured (all of it only on clean laps, see `_clean_laps`):
  * traction control (TC): seconds it was active per lap, and the 100 m stretches of track where it came on in most laps
  * ABS: share of braking time it was active, and the stretches where it came on in most laps
  * tyres: how much hotter the inner edge is than the outer edge (camber), mean pressure
  * brakes: mean and peak disc temperature

The inner edge: the telemetry calls the tyre edges "left" and "right" as seen from the driver, so on the left tyres the inner edge is the
right one and on the right tyres it is the left one. This was checked on real data: with more negative camber in the setup, the inner
edge ran hotter on all four tyres.

Nothing personal is read: only the car, track, setup and the numbers above. The driver name and Steam ID are never touched.
"""
from __future__ import annotations

import hashlib
import shutil

import numpy as np

from simtracker.setup import model as setup_model
from simtracker.telemetry.loader import open_db

BIN_M = 100.0            # track positions are grouped in stretches of this many metres
MOVING = 25.0            # m/s: below this the tyres are not working (pit lane, slow corners)
RECURS = 0.6             # a stretch counts as "most laps" when the effect shows in this share of the clean laps
BRAKING = 5.0            # brake pedal in percent: above this the driver is braking
MIN_LAPS = 3             # fewer clean laps than this say too little

_META = ("TrackName", "TrackLayout", "CarName", "CarClass", "SessionType", "RecordingTime", "CarSetup")
_cache: dict = {}


def _arr(con, table: str, col: str = "value") -> np.ndarray:
    return np.asarray(con.execute(f'SELECT "{col}" FROM "{table}"').fetchnumpy()[col], dtype=float)


def _cols(con, table: str) -> np.ndarray:
    """A table with four values per sample (one per wheel: front left, front right, rear left, rear right)."""
    d = con.execute(f'SELECT value1, value2, value3, value4 FROM "{table}"').fetchnumpy()
    return np.column_stack([np.asarray(d[f"value{i}"], dtype=float) for i in (1, 2, 3, 4)])


def _rising(on: np.ndarray) -> np.ndarray:
    """Indices where a True stretch starts."""
    return np.flatnonzero(np.diff(np.r_[0, on.astype(np.int8)]) == 1)


def _clean_laps(starts: list[float], dist10: np.ndarray) -> list[tuple[float, float, float]]:
    """(start s, end s, length m) of the laps that look like ordinary full laps: a similar length and time as most laps. Out laps, in laps and
    laps with a spin or a pit visit are left out."""
    laps = []
    for a, b in zip(starts[:-1], starts[1:]):
        if not 20 < b - a < 600:
            continue
        seg = dist10[int(round(a * 10)):int(round(b * 10))]
        if len(seg) > 10:
            laps.append((a, b, float(seg.max())))
    if not laps:
        return []
    med_len, med_t = np.median([l[2] for l in laps]), np.median([l[1] - l[0] for l in laps])
    return [l for l in laps if abs(l[2] / med_len - 1) < 0.03 and abs((l[1] - l[0]) / med_t - 1) < 0.08]


def _compute(path) -> dict | None:
    con, tmp = open_db(str(path))
    try:
        meta = dict(con.execute("SELECT key, value FROM metadata WHERE key IN (%s)" % ",".join("'%s'" % k for k in _META)).fetchall())
        events = con.execute('SELECT ts FROM "Lap" ORDER BY ts').fetchall()
        out = {"id": path.name, "recorded": meta.get("RecordingTime", ""), "track": meta.get("TrackName", ""), "layout": meta.get("TrackLayout", ""),
               "car": meta.get("CarName", ""), "car_class": meta.get("CarClass", ""), "session_type": meta.get("SessionType", ""),
               "setup": setup_model.from_telemetry(meta.get("CarSetup", "")), "n_laps": 0}
        if len(events) < 3:
            return out
        t0 = events[0][0]                              # the game's event times start at the first lap marker; the samples start at 0
        starts = [e[0] - t0 for e in events]
        dist10 = _arr(con, "Lap Dist")
        laps = _clean_laps(starts, dist10)
        out["n_laps"] = len(laps)
        if not laps:
            return out
        times = [l[1] - l[0] for l in laps]
        out["lap_times"] = [round(t, 3) for t in times]
        out["best"], out["median"] = round(min(times), 3), round(float(np.median(times)), 3)
        out["length"] = round(float(np.median([l[2] for l in laps])))

        speed, tc = _arr(con, "Ground Speed"), _arr(con, "TC") > 0.5                     # 100 Hz
        brake = _arr(con, "Brake Pos")                                                     # 50 Hz
        t100, t50 = np.arange(len(speed)) / 100.0, np.arange(len(brake)) / 50.0
        dist100 = np.interp(t100, np.arange(len(dist10)) / 10.0, dist10)
        dist50 = np.interp(t50, np.arange(len(dist10)) / 10.0, dist10)

        absev = con.execute('SELECT ts, value FROM "ABS" ORDER BY ts').fetchall()
        abs50 = np.zeros(len(brake), dtype=bool)
        cur, last = False, 0
        for ts, val in absev:
            i = min(max(int(round((ts - t0) * 50)), 0), len(brake))
            abs50[last:i] = cur
            cur, last = bool(val), max(i, last)
        abs50[last:] = cur
        braking = brake > BRAKING

        mask100 = np.zeros(len(speed), dtype=bool)
        mask50 = np.zeros(len(brake), dtype=bool)
        tc_time, tc_bins, abs_bins = [], {}, {}
        abs_time = brake_time = 0.0
        for a, b, _ in laps:
            s100, s50 = slice(int(a * 100), int(b * 100)), slice(int(a * 50), int(b * 50))
            mask100[s100] = True
            mask50[s50] = True
            tc_time.append(float(tc[s100].sum()) / 100.0)
            for k in {int(d // BIN_M) for d in dist100[s100][_rising(tc[s100])]}:
                tc_bins[k] = tc_bins.get(k, 0) + 1
            on = abs50[s50] & braking[s50]
            abs_time += float(on.sum()) / 50.0
            brake_time += float(braking[s50].sum()) / 50.0
            for k in {int(d // BIN_M) for d in dist50[s50][_rising(on)]}:
                abs_bins[k] = abs_bins.get(k, 0) + 1
        out["tc_s_per_lap"] = round(float(np.median(tc_time)), 2)
        out["tc_bins"], out["abs_bins"] = tc_bins, abs_bins
        out["abs_pct"] = round(100.0 * abs_time / brake_time, 1) if brake_time > 0 else None

        move = mask100 & (speed > MOVING)
        left, centre, right = (_cols(con, t)[:len(speed)][move] for t in ("TyresTempLeft", "TyresTempCentre", "TyresTempRight"))
        inner = np.where(np.array([True, False, True, False]), right, left)             # left tyres: the right edge is the inner one
        outer = np.where(np.array([True, False, True, False]), left, right)
        out["tyre_inner_minus_outer"] = [round(float(x), 1) for x in (inner - outer).mean(axis=0)]
        out["tyre_temp"] = [round(float(x), 1) for x in ((left + centre + right) / 3).mean(axis=0)]
        p10 = _cols(con, "TyresPressure")
        m10 = np.zeros(len(p10), dtype=bool)
        for a, b, _ in laps:
            m10[int(a * 10):int(b * 10)] = True
        out["pressure"] = [round(float(x), 1) for x in p10[m10].mean(axis=0)]
        bt = _cols(con, "Brakes Temp")[:len(brake)][mask50]
        out["brake_temp_mean"] = [round(float(x)) for x in bt.mean(axis=0)]
        out["brake_temp_peak"] = [round(float(x)) for x in np.percentile(bt, 99, axis=0)]
        return out
    finally:
        con.close()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def run_metrics(path) -> dict | None:
    """The measurements of one telemetry file (cached until the file changes). None when it can't be read."""
    st = path.stat()
    key = (path.name, st.st_mtime, st.st_size)
    if key in _cache:
        return _cache[key]
    try:
        out = _compute(path)
    except Exception:
        out = None
    for k in [k for k in _cache if k[0] == path.name]:
        del _cache[k]
    _cache[key] = out
    return out


# ---- settings ---------------------------------------------------------------------------------------------------------------

_IGNORED = ("VM_FUEL", "VM_VIRTUAL_ENERGY", "VM_NUM_PITSTOPS", "VM_PITSTOP", "WM_COMPOUND", "VM_FRONT_TIRE_COMPOUND", "VM_REAR_TIRE_COMPOUND",
            "gearGraph", "symmetric")
_NAMES = {
    "VM_TRACTIONCONTROLMAP": "Traction control (TC) map", "VM_TRACTIONCONTROLPOWERCUTMAP": "TC power cut", "VM_TRACTIONCONTROLSLIPANGLEMAP": "TC slip angle",
    "VM_ANTILOCKBRAKESYSTEMMAP": "ABS map", "VM_BRAKE_BALANCE": "Brake bias", "VM_BRAKE_PRESSURE": "Brake pressure",
    "VM_BRAKE_DUCTS": "Front brake ducts", "VM_BRAKE_DUCTS_REAR": "Rear brake ducts", "VM_DIFF_PRELOAD": "Diff preload", "VM_DIFF_POWER": "Diff power",
    "VM_DIFF_COAST": "Diff coast", "VM_FRONT_WING": "Front wing", "VM_REAR_WING": "Rear wing", "VM_FRONT_ANTISWAY": "Front anti-roll bar",
    "VM_REAR_ANTISWAY": "Rear anti-roll bar", "VM_FRONT_TOEIN": "Front toe", "VM_REAR_TOEIN": "Rear toe", "VM_ENGINE_BRAKEMAP": "Engine braking",
    "VM_STEER_LOCK": "Steering lock", "WM_CAMBER": "Camber", "WM_PRESSURE": "Tyre pressure", "WM_RIDEHEIGHT": "Ride height", "WM_SPRING": "Spring",
    "WM_FASTBUMP": "Fast bump damper", "WM_SLOWBUMP": "Slow bump damper", "WM_FASTREBOUND": "Fast rebound damper",
    "WM_SLOWREBOUND": "Slow rebound damper", "WM_PACKERS": "Packers", "WM_TENDERSPRING": "Third spring", "WM_TENDERSPRINGTRAVEL": "Third spring travel",
    "WM_BRAKEDISC": "Brake disc", "WM_BRAKEPAD": "Brake pad",
}
_WHEELS = ("FL", "FR", "RL", "RR")


def _pretty(key: str) -> str:
    return _NAMES.get(key) or key.replace("VM_", "").replace("WM_", "").replace("_", " ").title()


def setup_id(setup: dict) -> str:
    """Two runs with the same id were driven with the same setup (fuel and tyre wear are not part of it)."""
    items = sorted((k, v["text"]) for k, v in setup.items() if not k.startswith(_IGNORED))
    return hashlib.sha1(repr(items).encode()).hexdigest()[:8]


def changes(a: dict, b: dict) -> list[dict]:
    """What is different in setup b compared with setup a: [{"name", "where", "from", "to"}], wheel settings merged where the same change was
    made on several wheels."""
    rows, wheel = [], {}
    for key in sorted(set(a) | set(b)):
        if key.startswith(_IGNORED):
            continue
        ta, tb = (a.get(key) or {}).get("text"), (b.get(key) or {}).get("text")
        if ta == tb:
            continue
        if key.startswith("WM_") and "-W_" in key:
            base, w = key.split("-W_")
            wheel.setdefault(base, {})[w] = (ta, tb)
        else:
            rows.append({"name": _pretty(key), "where": "", "from": ta, "to": tb})
    for base, per in wheel.items():
        groups: dict = {}
        for w, change in per.items():
            groups.setdefault(change, []).append(w)
        for (ta, tb), ws in groups.items():
            ws = [w for w in _WHEELS if w in ws]
            where = "all four wheels" if len(ws) == 4 else "front" if ws == ["FL", "FR"] else "rear" if ws == ["RL", "RR"] else ", ".join(ws)
            rows.append({"name": _pretty(base), "where": where, "from": ta, "to": tb})
    return rows


def setting(run: dict, key: str) -> str | None:
    return ((run.get("setup") or {}).get(key) or {}).get("text")


# ---- reading the numbers -----------------------------------------------------------------------------------------------------

def spots(bins: dict, n_laps: int, turns: list[dict] | None, length: float | None, top: int = 4) -> list[dict]:
    """The stretches of track where something happened in most laps, neighbouring 100 m stretches merged, with the turn they are in."""
    if not n_laps:
        return []
    hot = sorted(k for k, v in bins.items() if v / n_laps >= RECURS)
    merged: list[list[int]] = []
    for k in hot:
        if merged and k - merged[-1][-1] <= 1:
            merged[-1].append(k)
        else:
            merged.append([k])
    out = []
    for grp in merged:
        share = max(bins[k] for k in grp) / n_laps
        frm, to = grp[0] * BIN_M, (grp[-1] + 1) * BIN_M
        turn = ""
        if turns and length:
            mid = (frm + to) / 2 / length
            for t in turns:
                if t["start_frac"] - 0.015 <= mid <= t["end_frac"] + 0.015:
                    turn = t["name"]
                    break
        out.append({"from_m": int(frm), "to_m": int(to), "share": round(share, 2), "turn": turn})
    return sorted(out, key=lambda s: -s["share"])[:top]


def _where(s: dict) -> str:
    return (f"{s['turn']} (" if s["turn"] else "") + f"{s['from_m']}–{s['to_m']} m" + (")" if s["turn"] else "")


def findings(run: dict, turns: list[dict] | None) -> list[dict]:
    """Plain-language findings for one run: [{"topic", "kind": "try" | "info", "title", "text", "evidence"}]. A "try" has a suggested change with a
    clear direction. These are rules of thumb on measurements, not facts about what the car needs: every suggestion says what to compare."""
    out = []
    n, length = run.get("n_laps", 0), run.get("length")
    if n < MIN_LAPS:
        return out
    tc_level, abs_level = setting(run, "VM_TRACTIONCONTROLMAP"), setting(run, "VM_ANTILOCKBRAKESYSTEMMAP")

    tcs = spots(run.get("tc_bins", {}), n, turns, length)
    sec = run.get("tc_s_per_lap", 0)
    if tcs and sec >= 1.0:
        out.append({"topic": "Traction control", "kind": "try", "title": f"TC is active about {sec:.1f} s every lap, at the same places",
                    "text": "It comes on at the same spots in most laps, so it is the car and the TC setting, not a one-off. If you want TC to do less, lower the "
                            "TC map one step (and keep the others as they are), then compare TC time and lap time over at least 8 laps. Less TC can also "
                            "mean more wheelspin, so it only helps if you can control the throttle at those spots.",
                    "evidence": [f"{_where(s)}: on in {round(s['share'] * 100)}% of laps" for s in tcs] + ([f"TC map now: {tc_level}"] if tc_level else [])})
    elif sec < 0.5:
        out.append({"topic": "Traction control", "kind": "info", "title": "TC hardly interferes",
                    "text": f"It was active only {sec:.1f} s a lap. If the car feels slow out of corners it is not TC that holds you back.",
                    "evidence": [f"TC map now: {tc_level}"] if tc_level else []})

    abs_spots = spots(run.get("abs_bins", {}), n, turns, length)
    pct = run.get("abs_pct")
    if pct is not None:
        ev = [f"{_where(s)}: on in {round(s['share'] * 100)}% of laps" for s in abs_spots] + ([f"ABS map now: {abs_level}"] if abs_level else [])
        if pct >= 75 and abs_spots:
            out.append({"topic": "ABS", "kind": "try", "title": f"ABS is active for {pct:.0f}% of your braking",
                        "text": "ABS is working for almost all of your braking, at the same zones every lap. That can be right (it lets you brake late and "
                                "hard), but it also means you are always at the limit. If you would rather feel the limit yourself, lower the ABS map one step "
                                "and compare braking distance and lap time over at least 8 laps.", "evidence": ev})
        else:
            out.append({"topic": "ABS", "kind": "info", "title": f"ABS is active for {pct:.0f}% of your braking",
                        "text": "This is how much of the time your brake pedal is down that ABS was holding the wheels back. Use it to compare runs; "
                                "I don't have a 'right' value for this car.", "evidence": ev})

    spread = run.get("tyre_inner_minus_outer")
    if spread:
        names = ("front left", "front right", "rear left", "rear right")
        hot = [f"{names[i]} (+{s:.0f}°C)" for i, s in enumerate(spread) if s > 12]
        cold = [f"{names[i]} ({s:+.0f}°C)" for i, s in enumerate(spread) if s < 2]
        ev = [f"{names[i]}: inner edge {s:+.1f}°C compared with the outer edge" for i, s in enumerate(spread)]
        if hot:
            out.append({"topic": "Camber", "kind": "try", "title": "The inner edge of some tyres runs much hotter than the outer edge",
                        "text": "A common rule of thumb is that the inner edge should be roughly 5–10°C hotter than the outer edge. A bigger difference "
                                "suggests too much negative camber on " + ", ".join(hot) + ". Try one step less negative camber on those tyres and compare "
                                "the difference and the lap times. Corners you drive more in one direction heat the tyres on that side more, so judge the two sides "
                                "of the car separately.", "evidence": ev + [f"Camber now: {setting(run, 'WM_CAMBER-W_FL')} front, {setting(run, 'WM_CAMBER-W_RL')} rear"]})
        elif cold:
            out.append({"topic": "Camber", "kind": "try", "title": "The edges of some tyres run at the same temperature",
                        "text": "The inner edge is not hotter than the outer edge on " + ", ".join(cold) + ". A little more negative camber would load the "
                                "inner edge more in corners. Try one step and compare the temperature difference and the lap times.",
                        "evidence": ev})
        else:
            out.append({"topic": "Camber", "kind": "info", "title": "Tyre edge temperatures look balanced",
                        "text": "The inner edge is about 2–12°C hotter than the outer edge on every tyre, which is a normal range.", "evidence": ev})
    return out


def compare(a: dict, b: dict) -> dict:
    """Run b against run a: the setting changes and what moved."""
    ch = changes(a.get("setup") or {}, b.get("setup") or {})
    fr = lambda v: None if not v else round((v[0] + v[1]) / 2, 1)
    rr = lambda v: None if not v else round((v[2] + v[3]) / 2, 1)
    rows = []

    def row(label, va, vb, unit="", dec=2):
        if va is None or vb is None:
            return
        rows.append({"label": label, "a": round(va, dec), "b": round(vb, dec), "diff": round(vb - va, dec), "unit": unit})

    row("Typical lap time", a.get("median"), b.get("median"), " s", 3)
    row("Best lap time", a.get("best"), b.get("best"), " s", 3)
    row("TC active per lap", a.get("tc_s_per_lap"), b.get("tc_s_per_lap"), " s")
    row("ABS active while braking", a.get("abs_pct"), b.get("abs_pct"), " %", 1)
    for name, k in (("Tyre pressure", "pressure"), ("Tyre inner minus outer edge", "tyre_inner_minus_outer"), ("Brake temperature peak", "brake_temp_peak")):
        for side, f in (("front", fr), ("rear", rr)):
            row(f"{name}, {side}", f(a.get(k)), f(b.get(k)), {"pressure": " kPa"}.get(k, " °C"), 1)
    note = None
    if len(ch) > 4:
        note = (f"{len(ch)} settings changed between these runs. With that many changes at once you can't tell which one did what. "
                "Change one thing at a time and compare after at least 8 clean laps.")
    return {"changes": ch, "rows": rows, "note": note}
