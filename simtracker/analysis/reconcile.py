"""Bring the turns found on a lap closer to the official turn count of the track.

The computer cannot tell some corners apart: a double-apex corner (Pouhon at Spa) looks like one long
turn. When the catalog (simtracker/catalog.py) gives a confident official count and fewer turns were
found, this splits the turn that most looks like two. Only clear cases are split: both halves must be real
bends (not a flat-out kink), the wheel must let go between them, and the turn must be much longer than a
typical one. Without clear evidence nothing is split and the count is simply left short.

It never joins turns: when more are found than the official count, they are left as they are, because
guessing which ones belong together went wrong in testing. It also never adds more than MAX_CHANGE turns,
so a track whose count is far off (for instance a different layout) is left alone.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MAX_CHANGE = 3              # never add more than this many turns
MIN_SPLIT_POINTS = 16       # a turn shorter than ~90 m is not split
PEAK_GAP_POINTS = 7         # two steering peaks must be at least ~40 m apart to be two corners
STRONG_PEAK = 0.35          # both peaks must be at least this fraction of the steering range (not a flat-out kink)
MIN_SCORE = 0.6             # evidence needed before a turn is split


def _apex(speed: np.ndarray, mag: np.ndarray, a: int, b: int) -> int:
    """Slowest point of a stretch; if that is at its very edge, where the wheel is turned most instead."""
    i = a + int(np.argmin(speed[a:b + 1]))
    if not (a + 0.1 * (b - a) <= i <= b - 0.1 * (b - a)):
        i = a + int(np.argmax(mag[a:b + 1]))
    return i


def split_candidates(corners: list[dict], mag: np.ndarray, scale: float) -> list[tuple]:
    """(score, index of turn, cut point) for turns that look like two corners, best evidence last in sort order.

    score = how fully the wheel is released between the two steering peaks (0..1)
            x how much longer the turn is than the typical one."""
    typical = float(np.median([c["end"] - c["start"] for c in corners])) or 1.0
    out = []
    for k, c in enumerate(corners):
        s, e = c["start"], c["end"]
        if e - s < MIN_SPLIT_POINTS:
            continue
        seg = mag[s:e + 1]
        peaks = [i for i in range(1, len(seg) - 1)
                 if seg[i] >= seg[i - 1] and seg[i] > seg[i + 1] and seg[i] >= STRONG_PEAK * scale]
        if len(peaks) < 2:
            continue
        p1 = max(peaks, key=lambda i: seg[i])
        far = [i for i in peaks if abs(i - p1) >= PEAK_GAP_POINTS]
        if not far:
            continue
        p2 = max(far, key=lambda i: seg[i])
        lo, hi = sorted((p1, p2))
        valley = lo + int(np.argmin(seg[lo:hi + 1]))
        release = 1.0 - seg[valley] / min(seg[p1], seg[p2])
        out.append((release * (e - s) / typical, k, s + valley))
    return out


def reconcile(ref: pd.DataFrame, corners: list[dict], expected: int, scale: float) -> list[dict]:
    """`corners` with turns split so there are `expected` of them, as far as the evidence allows.

    `ref` is the lap on its distance grid and `scale` its steering range (the one detect_corners uses)."""
    missing = expected - len(corners)
    if not corners or missing <= 0 or missing > MAX_CHANGE:
        return corners
    corners = [dict(c) for c in corners]
    mag = np.abs(np.convolve(ref["steer"].to_numpy(), np.ones(5) / 5, mode="same"))
    speed = ref["speed"].to_numpy()
    while len(corners) < expected:
        found = [f for f in split_candidates(corners, mag, scale) if f[0] >= MIN_SCORE]
        if not found:
            break
        _, k, cut = max(found)
        c = corners[k]
        corners[k:k + 1] = [{**c, "end": cut, "apex": _apex(speed, mag, c["start"], cut)},
                            {**c, "start": cut, "apex": _apex(speed, mag, cut, c["end"])}]
    for n, c in enumerate(corners, 1):
        c["number"] = n
    return corners
