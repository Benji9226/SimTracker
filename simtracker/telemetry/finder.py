"""Find the Le Mans Ultimate Telemetry folder and list its files.

Looked at, in this order: the LMU_TELEMETRY_DIR setting, the folder remembered from last time, every Steam
library (read from Steam's own list, so any drive and any folder name works), then the usual folders on every drive.
If nothing is found the user is asked to pick the folder once (see ask_for_folder) and it is remembered.
"""
from __future__ import annotations

import json
import os
import re
import string
from pathlib import Path

from simtracker.paths import DATA_DIR

CONFIG = DATA_DIR / "config.json"        # the folder found last time is remembered here
REL = Path("steamapps/common/Le Mans Ultimate/UserData/Telemetry")


class TelemetryNotFound(Exception):
    pass


def _steam_roots() -> list[Path]:
    """Steam's install folder and every library folder it knows about."""
    roots: list[Path] = []
    try:
        import winreg
        for hive, key, value in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                 (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                                 (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.append(Path(winreg.QueryValueEx(k, value)[0]))
            except OSError:
                pass
    except ImportError:                  # not Windows
        pass
    for base in list(roots):
        try:
            text = (base / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            roots.append(Path(m.group(1).replace("\\\\", "\\")))
    return roots


def _candidates():
    env = os.environ.get("LMU_TELEMETRY_DIR")
    if env:
        yield Path(env)
    if CONFIG.exists():
        try:
            yield Path(json.loads(CONFIG.read_text())["telemetry_dir"])
        except (OSError, ValueError, KeyError):
            pass
    for r in _steam_roots():
        yield r / REL
    for d in string.ascii_uppercase:
        for sub in ("", "Steam", "SteamLibrary", "Program Files (x86)/Steam", "Program Files/Steam",
                    "Games/Steam", "Games/SteamLibrary"):
            yield Path(f"{d}:/") / sub / REL


def remember(folder: Path) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps({"telemetry_dir": str(folder)}, indent=2))
    except OSError:
        pass


def find_telemetry_dir() -> Path:
    for c in _candidates():
        if c.is_dir():
            try:
                saved = json.loads(CONFIG.read_text()).get("telemetry_dir") if CONFIG.exists() else None
            except (OSError, ValueError):
                saved = None
            if saved != str(c):
                remember(c)
            return c
    raise TelemetryNotFound("Could not find the Le Mans Ultimate Telemetry folder.")


def ask_for_folder() -> Path | None:
    """Show a "choose folder" window. Returns the folder, remembered for next time, or None if cancelled."""
    try:
        import tkinter
        from tkinter import filedialog, messagebox
    except ImportError:
        return None
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        messagebox.showinfo(
            "SimTracker",
            "SimTracker could not find your Le Mans Ultimate telemetry folder by itself.\n\n"
            "Please pick it once. It is the folder called Telemetry inside\n"
            "...\\Le Mans Ultimate\\UserData\\ (the folder that holds the .duckdb files).\n\n"
            "Play one lap in the game first if you cannot find it.", parent=root)
        chosen = filedialog.askdirectory(title="Pick the Le Mans Ultimate Telemetry folder", parent=root)
    finally:
        root.destroy()
    if not chosen:
        return None
    folder = Path(chosen)
    remember(folder)
    return folder


def sessions(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.duckdb"), key=lambda p: p.stat().st_mtime, reverse=True)
