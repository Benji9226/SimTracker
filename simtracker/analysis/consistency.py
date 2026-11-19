"""Consistency across all your clean laps: the ideal lap and where you vary most."""
from __future__ import annotations

import numpy as np
import pandas as pd

from simtracker.analysis.laps import grid_points, lap_on_grid, lap_summary


FASTEST_SHARE = 0.50      # the share of your clean laps (fastest first) that the turn page uses
MIN_LAPS = 3
MAX_SLOWER = 1.20         # a lap slower than this times the best lap is never used


def _insight(rows: list[dict], times: list[float]) -> dict | None:
    """What was different in your fastest pass through a turn, compared with your other passes.

    This does not assume any technique is right or wrong (easing off the throttle is correct in some
    turns and a mistake in others). It only reports the biggest difference, in your own laps, that goes
    with your fastest pass. Each measurement has a size below which a difference is ignored as noise.
    Returns None with fewer than two passes; "similar" when nothing differs clearly.
    value > 0 means the fastest pass had more of it (for brake_dist: it braked earlier).
    """
    if len(rows) < 2:
        return None
    b = times.index(min(times))
    best, others = rows[b], [r for i, r in enumerate(rows) if i != b]
    found = []
    for key, noise in (("min_speed", 3.0), ("exit_speed", 3.0), ("brake_dist", 8.0), ("coast", 0.15)):
        vals = [o[key] for o in others if o[key] is not None]
        if best[key] is None or not vals:
            continue
        if key == "exit_speed" and best["throttle_delay"] is None:
            continue                      # first half of a chicane: the zone ends mid-corner, speed there says little
        d = best[key] - float(np.mean(vals))
        found.append((abs(d) / noise, key, d))
    found.sort(reverse=True)
    if not found or found[0][0] < 1:
        return {"kind": "similar", "value": 0}
    _, key, d = found[0]
    return {"kind": key, "value": round(d, 2 if key == "coast" else 1)}


def _on_ref_grid(ref: pd.DataFrame, lap: pd.DataFrame) -> pd.DataFrame:
    """A saved lap resampled onto the distance grid of `ref`, so both can be cut at the same turns."""
    x = ref["dist"].to_numpy() * (float(lap["dist"].iloc[-1]) / float(ref["dist"].iloc[-1]))
    out = {"dist": ref["dist"].to_numpy()}
    for c in ("time", "speed", "throttle", "brake", "steer"):
        out[c] = np.interp(x, lap["dist"].to_numpy(), lap[c].to_numpy())
    return pd.DataFrame(out)


def turn_stats(df: pd.DataFrame, zones_for, saved: list | None = None) -> dict | None:
    """Per-turn consistency across your clean laps, and the "ideal lap" built from your best turns.

    `zones_for(ref_grid, length)` gives the turn zones (normally from the track profile); every lap is
    cut at the same zones, so each turn gets one time per lap. Besides time it measures coasting (no
    throttle and no brake), minimum and exit speed, braking point, and how long after the apex you wait
    before reaching full throttle. The lap time not spent in a turn is reported as "Straights".
    Ideal lap = the best time of every part, added together.

    `saved` is an optional list of (metadata, lap on its grid) for laps saved on other days
    (simtracker.storage.saved_laps). They count as extra passes through every turn.
    """
    summary = lap_summary(df)
    clean = summary[summary["complete"]]
    if clean.empty:
        return None
    best_lap = int(clean["lap_time"].idxmin())              # your fastest clean lap in this session
    length = summary.loc[best_lap, "length_m"]
    n = grid_points(length)
    grids = {int(l): lap_on_grid(df, int(l), length, n) for l in clean.index}
    ref = grids[best_lap]
    corners = zones_for(ref, length)
    if not corners:
        return None

    keys = sorted(grids)                                    # this session's laps, then saved laps
    source = {l: {"kind": "session", "lap": l, "lap_time": float(clean.loc[l, "lap_time"])} for l in keys}
    saved_used = []
    for meta, g in saved or []:
        if abs(float(g["dist"].iloc[-1]) - float(length)) / float(length) > 0.03:
            continue                                        # a different layout: not comparable
        key = "saved:" + meta["id"]
        grids[key] = _on_ref_grid(ref, g)
        keys.append(key)
        source[key] = {"kind": "saved", "saved_kind": meta["kind"], "lap": meta["lap"],
                       "lap_time": meta["lap_time"], "date": meta.get("date", "")}
        saved_used.append({"id": meta["id"], **{k: source[key][k] for k in ("saved_kind", "lap", "lap_time", "date")}})

    # Only your fastest laps count: the quickest 50% (at least 3 laps) and never one slower than 120% of the best.
    # A slow lap (a spin, traffic, the formation lap) would otherwise give nonsense best times and an impossible ideal lap.
    by_time = sorted(keys, key=lambda k: source[k]["lap_time"])
    quick = [k for k in by_time if source[k]["lap_time"] <= MAX_SLOWER * source[by_time[0]]["lap_time"]]
    keep = set(quick[:max(MIN_LAPS, int(np.ceil(FASTEST_SHARE * len(quick))))])
    keep.add(best_lap)                                      # this session's own best lap is always the reference
    keys = [k for k in keys if k in keep]
    saved_used = [s for s in saved_used if "saved:" + s["id"] in keep]
    grids = {k: grids[k] for k in keys}

    per_lap = {}                      # lap -> per-turn measurements + straights time
    for lap, g in grids.items():
        t = g["time"].to_numpy()
        thr, brk, spd = g["throttle"].to_numpy(), g["brake"].to_numpy(), g["speed"].to_numpy()
        dist = g["dist"].to_numpy()
        dt = np.diff(t, prepend=t[0])
        rows = []
        for c in corners:
            s, e, a = c["start"], c["end"], c["apex"]
            coast = float(dt[s + 1:e + 1][(thr[s + 1:e + 1] < 0.05) & (brk[s + 1:e + 1] < 0.05)].sum())
            full = np.where(thr[a:e + 1] > 0.9)[0]
            braking = np.where(brk[s:a + 1] > 0.2)[0]
            rows.append({"time": float(t[e] - t[s]), "min_speed": float(spd[s:e + 1].min()), "coast": coast,
                         "exit_speed": float(spd[e]),
                         "brake_dist": float(dist[a] - dist[s + braking[0]]) if len(braking) else None,   # metres before the apex
                         "throttle_delay": float(t[a + full[0]] - t[a]) if len(full) else None})
        per_lap[lap] = {"turns": rows, "straights": float(t[-1] - sum(r["time"] for r in rows))}

    def avg(xs):
        xs = [x for x in xs if x is not None]
        return float(np.mean(xs)) if xs else None

    turns = []
    for k, c in enumerate(corners):
        times = [per_lap[l]["turns"][k]["time"] for l in keys]
        best = min(times)
        turns.append({
            "name": c["name"], "number": c["number"],
            "best": round(best, 3), "best_from": source[keys[times.index(best)]],
            "avg": round(float(np.mean(times)), 3), "spread": round(max(times) - best, 3),
            "in_best_lap": round(per_lap[best_lap]["turns"][k]["time"], 3),
            "lost_in_best_lap": round(per_lap[best_lap]["turns"][k]["time"] - best, 3),
            "avg_loss": round(float(np.mean(times)) - best, 3),
            "coast": round(avg([per_lap[l]["turns"][k]["coast"] for l in keys]), 3),
            "throttle_delay": (None if avg([per_lap[l]["turns"][k]["throttle_delay"] for l in keys]) is None
                               else round(avg([per_lap[l]["turns"][k]["throttle_delay"] for l in keys]), 3)),
            "min_speed": round(avg([per_lap[l]["turns"][k]["min_speed"] for l in keys]), 1),
            "insight": _insight([per_lap[l]["turns"][k] for l in keys], times),
        })
    st = [per_lap[l]["straights"] for l in keys]
    straights = {"name": "Straights / between turns", "number": None, "best": round(min(st), 3),
                 "best_from": source[keys[st.index(min(st))]], "avg": round(float(np.mean(st)), 3),
                 "spread": round(max(st) - min(st), 3),
                 "in_best_lap": round(per_lap[best_lap]["straights"], 3),
                 "lost_in_best_lap": round(per_lap[best_lap]["straights"] - min(st), 3),
                 "avg_loss": round(float(np.mean(st)) - min(st), 3), "coast": None,
                 "throttle_delay": None, "min_speed": None, "insight": None}
    ideal = sum(t["best"] for t in turns) + straights["best"]
    best_key = min(keys, key=lambda k: float(grids[k]["time"].iloc[-1]))     # fastest lap of all, saved ones included
    best_total, best_time = float(grids[best_key]["time"].iloc[-1]), source[best_key]["lap_time"]
    ranked = sorted(turns, key=lambda t: -t["avg_loss"])
    for t in turns:
        t["focus"] = t["number"] in [r["number"] for r in ranked[:3]] and t["avg_loss"] > 0.02
    return {"laps": [k for k in keys if isinstance(k, int)], "n_laps": len(keys), "n_session": len(keys) - len(saved_used),
            "saved_used": saved_used, "best_lap": best_lap, "best_from": source[best_key], "best_time": float(best_time),
            "ideal_time": round(float(ideal) - (best_total - float(best_time)), 3),
            "potential": round(best_total - ideal, 3), "turns": turns + [straights]}
