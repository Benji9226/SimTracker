"""Text report and a comparison picture (used by tracker.py)."""
from __future__ import annotations

import pandas as pd

from simtracker.analysis.corners import corner_report
from simtracker.analysis.laps import compare_laps, fmt_time, lap_summary
from simtracker.storage.profiles import zones_for


def plot_comparison(ref, other, delta, ref_lap, other_lap, out_path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(5, 1, figsize=(12, 11), sharex=True,
                           gridspec_kw={"height_ratios": [3, 1.2, 1.2, 1.2, 1.5]})
    labels = [("speed", "Speed (km/h)"), ("throttle", "Throttle"),
              ("brake", "Brake"), ("steer", "Steering")]
    for a, (c, title) in zip(ax[:4], labels):
        a.plot(ref["dist"], ref[c], label=f"Lap {ref_lap} (reference)", lw=1.3)
        a.plot(other["dist"], other[c], label=f"Lap {other_lap}", lw=1.3)
        a.set_ylabel(title)
        a.grid(alpha=0.3)
    ax[0].legend(loc="lower right")
    ax[4].plot(ref["dist"], delta, color="crimson", lw=1.3)
    ax[4].axhline(0, color="black", lw=0.6)
    ax[4].set_ylabel(f"Delta (s)\n+ = lap {other_lap} slower")
    ax[4].set_xlabel("Distance (m)")
    ax[4].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def report(df: pd.DataFrame, ref_lap: int | None = None, other_lap: int | None = None,
           out_png: str = "comparison.png", info: dict | None = None) -> None:
    summary = lap_summary(df)
    print("\nLAPS")
    shown = summary.copy()
    shown["lap_time"] = shown["lap_time"].map(fmt_time)
    print(shown.round(1).to_string())

    full = summary[summary["complete"]]
    if ref_lap is None:
        if full.empty:
            print("\nNeed at least one complete, clean lap (no pit visit) to analyse.")
            return
        ref_lap = int(full["lap_time"].idxmin())
    if other_lap is None:
        others = full.drop(index=ref_lap)
        other_lap = int(others["lap_time"].idxmin()) if not others.empty else ref_lap   # one lap: analyse it alone
    single = other_lap == ref_lap

    ref, other, delta = compare_laps(df, ref_lap, other_lap)
    rows = corner_report(ref, other, delta, zones_for(info or {}, ref, float(ref["dist"].iloc[-1])))
    if single:
        print(f"\nSINGLE LAP {ref_lap} (no second clean lap to compare against)")
        print("\nTime spent per corner:")
        for r in rows:
            if r["ref_time_s"] is not None:
                print(f"  {r['name']:<28}{r['ref_time_s']:.3f} s   min speed {r['ref_min_speed']:.0f} km/h")
    else:
        print(f"\nCOMPARING lap {other_lap} against reference lap {ref_lap}")
        print(f"Final delta: {delta.iloc[-1]:+.3f} s")
        print(f"\nTime lost (+) / gained (-) per corner, lap {other_lap} vs {ref_lap}:")
        for r in rows:
            extra = ""
            if r["ref_min_speed"] is not None:
                extra = f"   min speed {r['other_min_speed']:.0f} vs {r['ref_min_speed']:.0f} km/h"
            print(f"  {r['name']:<28}{r['time_change_s']:+.3f} s{extra}")
    plot_comparison(ref, other, delta, ref_lap, other_lap, out_png)
    print(f"\nSaved plot: {out_png}")
