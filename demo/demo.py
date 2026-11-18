"""Run the analyzer on synthetic data to check the pipeline works.

    python demo/demo.py
"""
import numpy as np
import pandas as pd

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simtracker.analysis.report import report


def fake_session(laps: int = 5, hz: int = 100, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    track_len = 5000.0
    rows, t0 = [], 0.0
    for lap in range(1, laps + 1):
        pace = 1.0 + rng.normal(0, 0.01)          # per-lap pace factor
        base_time = 90.0 * pace
        n = int(base_time * hz)
        t = t0 + np.arange(n) / hz
        frac = np.linspace(0, 1, n)
        dist = frac * track_len
        speed = 180 + 90 * np.sin(2 * np.pi * 3 * frac) / pace
        throttle = np.clip(0.5 + 0.5 * np.sin(2 * np.pi * 3 * frac + 0.5), 0, 1)
        brake = np.clip(-np.sin(2 * np.pi * 3 * frac + 0.5), 0, 1) * 0.8
        steer = 0.4 * np.sin(2 * np.pi * 6 * frac)
        rows.append(pd.DataFrame({"t": t, "lap": lap, "dist": dist, "speed": speed,
                                  "throttle": throttle, "brake": brake, "steer": steer}))
        t0 = t[-1] + 1 / hz
    return pd.concat(rows, ignore_index=True)


if __name__ == "__main__":
    report(fake_session(), out_png=str(Path(__file__).resolve().parent / "demo_comparison.png"))
