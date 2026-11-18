"""Matching a lap to the drawing of the track, so a point on the lap can be shown on the map.

The map (data/track_maps) and your lap are two drawings of the same closed track, but the map has no
start line, no direction and its own size. The game's GPS positions give the true shape of the lap, so the
lap is laid over the map: every start point and both directions are tried, the lap is rotated and scaled to
fit (Procrustes), and the combination that fits best wins. That tells us which point of the map each metre of
the lap corresponds to.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

K = 240                      # points used for the matching
MAX_RMS = 0.12               # worst acceptable fit (relative to the track's size); worse = no dot


def lap_xy(df: pd.DataFrame, lap: int, length: float, n: int = 300) -> np.ndarray | None:
    """(n, 2) GPS positions of one lap at equal steps of distance (flat local coordinates, no real-world meaning)."""
    if "lat" not in df.columns:
        return None
    d = df[df["lap"] == lap].dropna(subset=["lat", "lon"]).sort_values("t")
    if len(d) < 50:
        return None
    dist = d["dist"].to_numpy() - d["dist"].iloc[0]
    dist = dist * (length / dist[-1])
    keep = np.r_[True, np.diff(dist) > 0]
    grid = np.linspace(0, length, n)
    lat = np.interp(grid, dist[keep], d["lat"].to_numpy()[keep])
    lon = np.interp(grid, dist[keep], d["lon"].to_numpy()[keep])
    return np.column_stack([(lon - lon.mean()) * np.cos(np.radians(lat.mean())), lat - lat.mean()])


def _resample_closed(pts: np.ndarray, k: int) -> np.ndarray:
    """k points equally spaced along a closed outline."""
    q = np.vstack([pts, pts[:1]])
    s = np.r_[0, np.cumsum(np.hypot(*np.diff(q, axis=0).T))]
    t = np.linspace(0, s[-1], k, endpoint=False)
    return np.column_stack([np.interp(t, s, q[:, 0]), np.interp(t, s, q[:, 1])])


def _by_fraction(pts: np.ndarray, k: int) -> np.ndarray:
    """k points taken at equal steps of the lap (the first row is the start line)."""
    t = np.linspace(0, len(pts) - 1, k, endpoint=False)
    i = np.arange(len(pts))
    return np.column_stack([np.interp(t, i, pts[:, 0]), np.interp(t, i, pts[:, 1])])


def _normalise(p: np.ndarray):
    c = p.mean(axis=0)
    q = p - c
    return q / np.sqrt((q ** 2).sum(axis=1).mean())


def match_to_map(lap: np.ndarray, outline: np.ndarray, n_out: int = 300, refine: bool = True) -> dict | None:
    """Where each point of the lap is on the map.

    `lap` is lap_xy(...); `outline` the closed track line of the map (any number of points, any size).
    Returns {"points": n_out map positions in the map's own coordinates, "rms": how well it fits (0 = perfect),
    "direction": +1/-1, "mirrored": bool} or None."""
    if lap is None or len(outline) < 8:
        return None
    a_raw = _by_fraction(lap, K)
    b_raw = _resample_closed(np.asarray(outline, float), K)
    a, b = _normalise(a_raw), _normalise(b_raw)
    best = None
    for direction in (1, -1):
        bd = b if direction == 1 else b[::-1]
        for shift in range(K):
            bs = np.roll(bd, -shift, axis=0)
            u, _, vt = np.linalg.svd(a.T @ bs)
            r = u @ vt                                           # best rotation (a reflection is allowed)
            rms = float(np.sqrt(np.mean(np.sum((a @ r - bs) ** 2, axis=1))))
            if best is None or rms < best[0]:
                best = (rms, direction, shift, np.linalg.det(r) < 0, r)
    rms, direction, shift, mirrored, rot = best
    matched = np.roll(b_raw if direction == 1 else b_raw[::-1], -shift, axis=0)     # map point for each lap step
    if refine:
        matched = _snap(a @ rot, b_raw, np.asarray(outline, float), direction, shift)
    wrap = np.vstack([matched, matched[:1]])                                        # the lap ends where it starts
    t = np.linspace(0, K, n_out)
    idx = np.arange(K + 1)
    pts = np.column_stack([np.interp(t, idx, wrap[:, 0]), np.interp(t, idx, wrap[:, 1])])
    return {"points": pts, "rms": rms, "direction": direction, "mirrored": bool(mirrored)}


def _snap(aligned: np.ndarray, b_raw: np.ndarray, outline: np.ndarray, direction: int, shift: int) -> np.ndarray:
    """Each lap point (laid over the map) moved onto the nearest part of the outline that is about where it should be.

    A drawing is never perfectly to scale, so stepping along it at the lap's own pace drifts a little; this keeps
    every point on the stretch of outline it really belongs to. Points never go backwards."""
    centre = b_raw.mean(axis=0)
    size = np.sqrt(((b_raw - centre) ** 2).sum(axis=1).mean())
    over = aligned * size + centre                                     # the lap, drawn in the map's own coordinates
    dense_n = 4 * K
    dense = _resample_closed(outline, dense_n)
    dense = np.roll(dense if direction == 1 else dense[::-1], -4 * shift, axis=0)
    window = int(0.05 * dense_n)
    idx, last = [], 0
    for i in range(K):
        expected = i * 4
        lo, hi = max(last, expected - window), min(dense_n - 1, expected + window)
        if hi < lo:
            hi = lo
        seg = dense[lo:hi + 1]
        j = lo + int(np.argmin(((seg - over[i]) ** 2).sum(axis=1)))
        idx.append(j)
        last = j
    return dense[np.array(idx)]
