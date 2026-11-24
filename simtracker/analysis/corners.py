"""Finding the turns on a lap, and measuring each one."""
from __future__ import annotations

import numpy as np
import pandas as pd


def steering_scale(ref: pd.DataFrame) -> float:
    """How big a steering input is on this lap (the 95th percentile of the wheel angle). Everything in
    turn detection is relative to this, so it works for any car's steering ratio."""
    st = np.convolve(ref["steer"].to_numpy(), np.ones(3) / 3, mode="same")
    return float(np.percentile(np.abs(st), 95))


def _detect_by_speed(ref: pd.DataFrame, min_drop: float = 30.0, smooth: int = 5) -> list[dict]:
    """Find corners on the reference lap from its speed trace.

    A corner is a speed minimum that the car climbs at least `min_drop` km/h away from on
    both sides, so a chicane (two close minima with no real straight between) counts as
    one turn. Each corner owns the stretch from the fastest point before it to the fastest
    point after it (braking, apex and exit), so every metre of the lap belongs to a turn.
    """
    v = np.convolve(ref["speed"].to_numpy(), np.ones(smooth) / smooth, mode="same")
    n = len(v)
    minima = [i for i in range(1, n - 1) if v[i] <= v[i - 1] and v[i] < v[i + 1]]

    def rise(i: int, step: int) -> float:
        peak, j = v[i], i + step
        while 0 <= j < n and v[j] >= v[i]:
            peak = max(peak, v[j])
            j += step
        return peak - v[i]

    apexes = [i for i in minima if min(rise(i, -1), rise(i, 1)) >= min_drop]
    # Of apexes sharing the same two-sided climb (flat-bottomed dips), keep the slowest.
    cleaned = []
    for i in apexes:
        if cleaned and v[cleaned[-1]:i + 1].max() - max(v[cleaned[-1]], v[i]) < min_drop:
            if v[i] < v[cleaned[-1]]:
                cleaned[-1] = i
        else:
            cleaned.append(i)
    apexes = cleaned

    bounds = [0]
    for a, b in zip(apexes[:-1], apexes[1:]):
        bounds.append(a + int(np.argmax(v[a:b + 1])))
    bounds.append(n - 1)
    corners = []
    for k, a in enumerate(apexes):
        lo, hi = bounds[k], bounds[k + 1]
        # Zone = from where braking starts (speed leaves the top 10% of the drop) to where the
        # exit is nearly back up to speed. The rest of the lap counts as "straights".
        before, after = v[lo], v[hi]
        s_idx = a - int(np.argmax(v[lo:a + 1][::-1] >= before - 0.1 * (before - v[a])))
        e_idx = a + int(np.argmax(v[a:hi + 1] >= after - 0.1 * (after - v[a])))
        corners.append({"number": k + 1, "apex": a, "start": s_idx, "end": e_idx})
    return corners


def detect_corners(ref: pd.DataFrame, thr_frac: float = 0.15, peak_frac: float = 0.22,
                   merge_gap_m: float = 120.0, strong_frac: float = 0.6, split_gap_m: float = 60.0) -> list[dict]:
    """Find the turns on the reference lap from the steering trace.

    A turn is a stretch where the wheel is held to one side. Left-right chicanes therefore
    become separate turns, and a long gentle bend (like Curva Grande) is one turn even though
    the car never slows for it. Stretches in the same direction less than `merge_gap_m` apart
    (steering dipping briefly mid-corner) are joined. Thresholds are relative to the car's
    own steering range. Each turn's zone runs from the braking point (if any) to where the
    throttle is back to full; neighbouring zones meet in the middle. If the steering channel
    gives nothing usable, falls back to detecting corners from speed dips.
    """
    x = ref["dist"].to_numpy()
    sp = ref["speed"].to_numpy()
    brake, thr = ref["brake"].to_numpy(), ref["throttle"].to_numpy()
    st = np.convolve(ref["steer"].to_numpy(), np.ones(3) / 3, mode="same")
    n, scale = len(st), steering_scale(ref)      # (the 99th percentile jumped around with tiny changes in sampling)
    if scale <= 0:
        return _detect_by_speed(ref)
    sign = np.where(st > thr_frac * scale, 1, np.where(st < -thr_frac * scale, -1, 0))

    segs, i = [], 0
    while i < n:
        if sign[i]:
            j = i
            while j + 1 < n and sign[j + 1] == sign[i]:
                j += 1
            segs.append([i, j, int(sign[i])])
            i = j + 1
        else:
            i += 1
    def strong(a: int, b: int) -> bool:        # a real bend, not a gentle wobble
        return np.abs(st[a:b + 1]).max() >= strong_frac * scale

    merged = []
    for a, b, g in segs:
        if merged and merged[-1][2] == g and x[a] - x[merged[-1][1]] <= merge_gap_m:
            gap = x[a] - x[merged[-1][1]]
            if gap >= split_gap_m and strong(*merged[-1][:2]) and strong(a, b):
                merged.append([a, b, g])       # two strong bends with a clear straight between: two turns
            else:
                merged[-1][1] = b              # the wheel just eased for a moment: one turn
        else:
            merged.append([a, b, g])
    groups = [g for g in merged if np.abs(st[g[0]:g[1] + 1]).max() >= peak_frac * scale]
    if len(groups) < 2:
        return _detect_by_speed(ref)

    zones = []
    for k, (a, b, g) in enumerate(groups):
        prev_end = groups[k - 1][1] if k else 0
        next_start = groups[k + 1][0] if k + 1 < len(groups) else n - 1
        apex = a + int(np.argmin(sp[a:b + 1]))
        if not (a + 0.1 * (b - a) <= apex <= b - 0.1 * (b - a)):
            # The slowest point is at the edge of the turn (still accelerating out of the last one, or a
            # flat-out bend): the middle of the turn is where the wheel is turned the most instead.
            apex = a + int(np.argmax(np.abs(st[a:b + 1])))
        lo = max(prev_end, a - 60)                           # look up to ~350 m back for braking
        braking = np.where(brake[lo:a + 1] > 0.1)[0]
        start = lo + int(braking[0]) if len(braking) else max(prev_end, a - 3)
        full = np.where(thr[b:next_start + 1] > 0.9)[0]
        end = b + int(full[0]) if len(full) else min(b + 5, n - 1)
        zones.append({"apex": apex, "start": start, "end": end, "direction": "R" if g > 0 else "L"})
    for z0, z1 in zip(zones[:-1], zones[1:]):                # neighbours meet in the middle
        if z0["end"] > z1["start"]:
            z0["end"] = z1["start"] = (z0["end"] + z1["start"]) // 2
    return [{"number": k + 1, **z} for k, z in enumerate(zones)]


def _brake_point(lap: pd.DataFrame, a: int, b: int, thresh: float = 0.2):
    idx = np.where(lap["brake"].to_numpy()[a:b + 1] > thresh)[0]
    return float(lap["dist"].iloc[a + idx[0]]) if len(idx) else None


def _power_point(lap: pd.DataFrame, a: int, b: int, rise: float = 2.0):
    """Where the car starts speeding up again after the slowest point of the turn (a..b): the first place its speed is `rise` km/h above
    that slowest point and still rising. Taken from speed alone, so it can be read the same way for your laps and for rival laps."""
    sp = lap["speed"].to_numpy(float)[a:b + 1]
    if len(sp) < 4:
        return None
    m = int(np.argmin(sp))
    for i in range(m + 1, len(sp) - 1):
        if sp[i] >= sp[m] + rise and sp[i + 1] >= sp[i]:
            return float(lap["dist"].iloc[a + i])
    return None


def corner_report(ref: pd.DataFrame, other: pd.DataFrame, delta: pd.Series, zones: list[dict]) -> list[dict]:
    """Per-turn time change (+ = other lost time), minimum speeds and braking point.

    `zones` are the turns on this grid: dicts with number, name, start, apex and end (grid indices),
    normally from the track profile (simtracker.storage.profiles.zones_for)."""
    rows = []
    for c in zones:
        a, e, s = c["apex"], c["end"], c["start"]
        rb, ob = _brake_point(ref, s, a), _brake_point(other, s, a)
        rp, op = _power_point(ref, s, e), _power_point(other, s, e)
        rows.append({
            "name": c["name"],
            "number": c["number"],
            "apex_m": round(float(ref["dist"].iloc[a])),
            "from_m": round(float(ref["dist"].iloc[s])),
            "to_m": round(float(ref["dist"].iloc[e])),
            "time_change_s": round(float(delta.iloc[e] - delta.iloc[s]), 3),
            "ref_time_s": round(float(ref["time"].iloc[e] - ref["time"].iloc[s]), 3),
            "ref_min_speed": round(float(ref["speed"].iloc[s:e + 1].min()), 1),
            "other_min_speed": round(float(other["speed"].iloc[s:e + 1].min()), 1),
            "brake_diff_m": round(ob - rb, 1) if rb is not None and ob is not None else None,
            "power_diff_m": round(op - rp, 1) if rp is not None and op is not None else None,
        })
    zoned = sum(r["time_change_s"] for r in rows)
    rows.append({"name": "Straights / between corners", "number": None, "apex_m": None,
                 "from_m": None, "to_m": None, "ref_time_s": None,
                 "time_change_s": round(float(delta.iloc[-1] - zoned), 3),
                 "ref_min_speed": None, "other_min_speed": None, "brake_diff_m": None, "power_diff_m": None})
    return rows
