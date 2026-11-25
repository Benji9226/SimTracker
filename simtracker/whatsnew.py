"""The "What's new" notes: shown once in a window the first time a new version is opened (for example right after an update), and any time from the
link at the bottom of the page.

The notes are part of the program itself, so showing them needs no internet. Add a new entry at the top of NOTES for every release.
Which version the user last saw is remembered in data/settings.json ("seen_version").
"""
from __future__ import annotations

from simtracker import settings
from simtracker.version import VERSION

NOTES = [
    {"version": "1.3.0", "title": "Setup page, in-game overlay and a new look", "items": [
        ("Overlay on top of the game", "Press \"Show overlay\" at the top of the page to get a small window over the game with a live graph of your throttle and brake "
         "and your speed. Yellow dots on the throttle line show traction control working, white dots on the brake line show ABS. The closer the dots, "
         "the harder it hits. Drag it where you want it; right-click it to close it. It shows when the game runs borderless or windowed (not fullscreen)."),
        ("New Setup page", "Shows how often traction control and ABS step in and at which corners, how hot the edges of your tyres run, your pressures and "
         "brake temperatures, and compares two of your setups side by side. It only uses your own laps."),
        ("Turn by turn: on the power", "Under each turn it now also says how much earlier or later you got back on the power compared with the lap you check against."),
        ("A new look", "The whole program, its charts and the overlay have a new dark neon style."),
    ]},
]


def _key(version: str) -> tuple:
    out = []
    for part in str(version).split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def status(everything: bool = False) -> dict:
    """What to show: `show` is True when this version has not been shown yet. After an update, the notes of every version newer than the one last seen are
    included (a user who skipped a version sees both); the first time ever, only the notes of this version."""
    seen = settings.load().get("seen_version")
    if everything:
        notes = NOTES
    elif seen:
        notes = [n for n in NOTES if _key(n["version"]) > _key(seen) and _key(n["version"]) <= _key(VERSION)]
    else:
        notes = [n for n in NOTES if n["version"] == VERSION]
    return {"version": VERSION, "show": bool(notes) and not everything and seen != VERSION, "notes": [
        {"version": n["version"], "title": n["title"], "items": [{"title": t, "text": x} for t, x in n["items"]]} for n in notes]}


def mark_seen() -> None:
    settings.save({"seen_version": VERSION})
