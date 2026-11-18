"""The track catalog (data/track_catalog.json): official turn counts for the circuits in Le Mans Ultimate."""
from __future__ import annotations

import json
import unicodedata

from simtracker.paths import CATALOG_FILE

LENGTH_TOLERANCE = 0.04      # your lap must be within 4% of the catalog length for the entry to apply


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(text.replace("-", " ").split())


def _load() -> list[dict]:
    try:
        return json.loads(CATALOG_FILE.read_text(encoding="utf-8")).get("tracks", [])
    except (OSError, ValueError):
        return []


def find(track: str, length_m: float | None) -> dict | None:
    """The catalog entry for a circuit, or None.

    Matches on the circuit name (the game's name for it, or any alias) AND the lap length, so a shorter
    layout of the same circuit does not pick up the full circuit's turn count. When more than one entry
    fits, the one whose length is closest to yours wins."""
    name = _norm(track)
    if not name:
        return None
    best, best_gap = None, None
    for entry in _load():
        words = [_norm(entry["name"])] + [_norm(a) for a in entry.get("aliases", [])]
        if not any(w and (w in name or name in w) for w in words):
            continue
        gap = abs(length_m - entry["length_m"]) / entry["length_m"] if length_m else 0.0
        if gap > LENGTH_TOLERANCE:
            continue
        if best is None or gap < best_gap:
            best, best_gap = entry, gap
    return best
