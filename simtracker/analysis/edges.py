"""The track surface, worked out from the laps you drove.

The game records, at every moment, how far right (+) or left (-) of the track's centre line the car is (`lateral`)
and where the nearest track edge is on that same scale (`edge`). The centre line itself is therefore
    car position - lateral x (the direction to the right of travel),
the same for every lap, and the edges lie at fixed lateral distances from it. A lap that ran on the left of the
track shows the left edge there, a lap on the right shows the right edge.

Every finished lap is turned into a small set of observations (`observe_lap`). Observations from any number of laps,
sessions and days are just added up (`Accumulator`, kept per track by storage/surface.py), and `solve` turns the
sums into the two edges. The more laps, the more of each edge has been seen and the more accurate it gets.
Stretches where one edge was never seen are filled from the nearest places where it was (an edge's distance from the
centre line changes slowly); where only one edge was ever seen, the other is put the usual width of the nearby
stretch away.
"""
from __future__ import annotations

import numpy as np

from simtracker.analysis.laps import grid_points, lap_on_grid

MAX_FILL_POINTS = 60           # about 350 m: the longest stretch of one edge that is filled in from its neighbours
FIELDS = ("cx", "cy", "left", "right")
COUNT_CAP = 200                # a point is the average of at most about this many laps: enough for a clear picture, and it stays bounded


def _fill(a: np.ndarray, max_gap: int) -> np.ndarray:
    """Linear fill of runs of NaN no longer than max_gap between two known values."""
    a = a.copy()
    ok = np.where(np.isfinite(a))[0]
    for lo, hi in zip(ok[:-1], ok[1:]):
        if 1 < hi - lo <= max_gap + 1:
            a[lo:hi + 1] = np.linspace(a[lo], a[hi], hi - lo + 1)
    return a


def _smooth(a: np.ndarray, k: int = 5) -> np.ndarray:
    return np.convolve(np.r_[[a[0]] * k, a, [a[-1]] * k], np.ones(k) / k, "same")[k:-k]


OUTLIER_WINDOW = 9             # points (about 50 m): a reading that differs from the middle of its neighbours by more than OUTLIER_M is thrown away
OUTLIER_M = 0.8
EDGE_SIGMA = 2.5               # points (about 15 m): how much the edges are smoothed along the track
CENTRE_SIGMA = 1.0
MIN_WIDTH_M = 4.0


def _circular(a: np.ndarray, r: int) -> np.ndarray:
    """The array with its last r values in front and its first r values behind (a lap is a loop: the start line is not an end)."""
    return np.r_[a[-r:], a, a[:r]]


def _smooth_loop(a: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian smoothing along a loop. Unknown values (NaN) stay unknown and do not pull their neighbours."""
    if sigma <= 0 or len(a) < 8:
        return a
    r = int(3 * sigma) + 1
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    ok = np.isfinite(a)
    num = np.convolve(_circular(np.where(ok, a, 0.0), r), k, "valid")
    den = np.convolve(_circular(ok.astype(float), r), k, "valid")
    return np.where(ok & (den > 0), num / np.maximum(den, 1e-9), np.nan)


def _drop_outliers(a: np.ndarray, window: int = OUTLIER_WINDOW, tol: float = OUTLIER_M) -> np.ndarray:
    """NaN where a reading is far from the middle (median) of the readings around it: pit lane entrances, kerbs, a wall seen once."""
    r = window // 2
    ok = np.isfinite(a)
    if ok.sum() < window:
        return a
    padded = _circular(a, r)
    med = np.array([np.nanmedian(padded[i:i + window]) if np.isfinite(padded[i:i + window]).any() else np.nan for i in range(len(a))])
    return np.where(ok & (np.abs(a - med) > tol), np.nan, a)


def _right_normal(x: np.ndarray, y: np.ndarray):
    """Unit vector to the right of the direction of travel (x east, y north) along a path."""
    tx, ty = np.gradient(_smooth(x)), np.gradient(_smooth(y))
    norm = np.hypot(tx, ty)
    norm[norm == 0] = 1.0
    return ty / norm, -tx / norm


def observe_lap(g) -> dict | None:
    """One lap (on the distance grid, see laps.lap_on_grid) as observations per grid point:
    cx, cy = the centre line under the car; left / right = lateral position of that edge (NaN where not on that side)."""
    if not all(c in g.columns for c in ("x", "y", "lateral", "edge")):
        return None
    x, y, lat, edge = (g[c].to_numpy(float) for c in ("x", "y", "lateral", "edge"))
    rx, ry = _right_normal(x, y)
    left = np.where(edge < 0, edge, np.nan)
    right = np.where(edge > 0, edge, np.nan)
    return {"cx": x - lat * rx, "cy": y - lat * ry, "left": left, "right": right}


class Accumulator:
    """Running sums of observations for one track; `n` grid points, one lap length."""

    def __init__(self, n: int, length: float):
        self.n, self.length = n, length
        self.sum = {f: np.zeros(n) for f in FIELDS}
        self.cnt = {f: np.zeros(n) for f in FIELDS}
        self.laps = 0

    def add(self, obs: dict) -> None:
        for f in FIELDS:
            v = np.asarray(obs[f], float)
            ok = np.isfinite(v)
            self.sum[f][ok] += v[ok]
            self.cnt[f][ok] += 1
            over = self.cnt[f] > COUNT_CAP                    # keep the average, forget the oldest weight
            self.sum[f][over] *= COUNT_CAP / self.cnt[f][over]
            self.cnt[f][over] = COUNT_CAP
        self.laps += 1

    def to_json(self) -> dict:
        return {"n": self.n, "length": self.length, "laps": self.laps,
                "sum": {f: [round(float(v), 3) for v in self.sum[f]] for f in FIELDS},
                "cnt": {f: [int(v) for v in self.cnt[f]] for f in FIELDS}}

    @classmethod
    def from_json(cls, d: dict) -> "Accumulator":
        a = cls(int(d["n"]), float(d["length"]))
        a.laps = int(d.get("laps", 0))
        for f in FIELDS:
            a.sum[f] = np.array(d["sum"][f], float)
            a.cnt[f] = np.array(d["cnt"][f], float)
        return a


def solve(acc: Accumulator, clean: bool = True) -> dict | None:
    """{"length", "laps_used", "left": [[x, y] or None ...], "right": [...]} in metres (x east, y north), or None.

    With `clean` (the default) the edges are tidied before they are drawn: readings that do not fit their neighbours are dropped, and the
    edges are smoothed along the track, so straights are straight and the outline has no spikes. (`clean=False` gives the raw averages.)"""
    n = acc.n
    if acc.laps == 0 or not (acc.cnt["cx"] > 0).any():
        return None
    mean = {f: np.where(acc.cnt[f] > 0, acc.sum[f] / np.maximum(acc.cnt[f], 1), np.nan) for f in FIELDS}
    cx, cy = _fill(mean["cx"], MAX_FILL_POINTS), _fill(mean["cy"], MAX_FILL_POINTS)
    if not (np.isfinite(cx).all() and np.isfinite(cy).all()):
        return None
    if clean:
        cx, cy = _smooth_loop(cx, CENTRE_SIGMA), _smooth_loop(cy, CENTRE_SIGMA)
    rx, ry = _right_normal(cx, cy)
    left, right = mean["left"], mean["right"]
    if clean:
        left, right = _drop_outliers(left), _drop_outliers(right)
    left, right = _fill(left, MAX_FILL_POINTS), _fill(right, MAX_FILL_POINTS)
    if clean:
        left, right = _smooth_loop(left, EDGE_SIGMA), _smooth_loop(right, EDGE_SIGMA)
    both = np.isfinite(left) & np.isfinite(right)
    if both.any():                                          # where only one edge is known, the other is the usual width away
        idx = np.arange(n)
        width = np.interp(idx, idx[both], (right - left)[both])
        if clean:
            width = np.maximum(_smooth_loop(width, 2 * EDGE_SIGMA), MIN_WIDTH_M)
        left = np.where(np.isfinite(left), left, right - width)
        right = np.where(np.isfinite(right), right, left + width)
        if clean:                                           # never narrower than a road
            narrow = np.isfinite(left) & np.isfinite(right) & (right - left < MIN_WIDTH_M)
            mid = (left + right) / 2
            left, right = np.where(narrow, mid - MIN_WIDTH_M / 2, left), np.where(narrow, mid + MIN_WIDTH_M / 2, right)

    def side(lat: np.ndarray):
        px, py = cx + lat * rx, cy + lat * ry
        return [None if not np.isfinite(lat[i]) else [round(float(px[i]), 2), round(float(py[i]), 2)] for i in range(n)]

    return {"length": round(acc.length, 1), "laps_used": acc.laps, "left": side(left), "right": side(right)}


def eligible_laps(summary) -> list[int]:
    """Laps that start at the line and run the whole way round (not out/in laps: those start or end somewhere else, so
    the same distance would be a different place on the track). Laps the game did not count are fine."""
    return [int(l) for l in summary.index if bool(summary.loc[l, "length_ok"]) and not bool(summary.loc[l, "pit"])]


def track_edges(df, summary) -> dict | None:
    """The edges from the laps of one session alone (used when no stored track data is available)."""
    laps = eligible_laps(summary)
    if not laps:
        return None
    length = float(summary.loc[laps, "length_m"].median())
    acc = Accumulator(grid_points(length), length)
    for lap in laps:
        obs = observe_lap(lap_on_grid(df, lap, length, acc.n))
        if obs:
            acc.add(obs)
    return solve(acc)
