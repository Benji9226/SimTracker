"""Check for a newer version of SimTracker and, on request, install it.

What goes over the internet: one request to GitHub's public release page for this project
(api.github.com/repos/<owner>/<name>/releases/latest), once at start-up and then every few hours, and, only when
the user presses "Update application", one download of the new SimTracker.exe from that release. Nothing about the
user or their laps is sent. If there is no internet, or the release page is not reachable, nothing is shown.

Installing (the double-click program only): the new exe is downloaded next to the old one and checked (it is an
exe, a plausible size, and matches the checksum GitHub lists for it). Then a tiny helper script waits for this
program to close, swaps the files and starts the new version. The user's data folder is never touched.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from simtracker.paths import FROZEN
from simtracker.version import ASSET_NAME, UPDATE_REPO, VERSION

API_URL = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
ALLOWED_HOSTS = ("github.com", "githubusercontent.com")        # downloads may only come from here
CHECK_EVERY = 6 * 3600
MIN_BYTES, MAX_BYTES = 10_000_000, 400_000_000
_UA = {"User-Agent": "SimTracker-update-check", "Accept": "application/vnd.github+json"}

_state: dict = {"checked": False, "available": False, "latest": None, "notes": "", "url": None, "sha256": None, "size": None}
_lock = threading.Lock()


def _version_tuple(text: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})", (text or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def _host_ok(url: str) -> bool:
    u = urlparse(url)
    return u.scheme == "https" and any(u.hostname == h or (u.hostname or "").endswith("." + h) for h in ALLOWED_HOSTS)


def check() -> dict:
    """Ask GitHub for the latest release and remember the answer. Never raises."""
    try:
        req = urllib.request.Request(API_URL, headers=_UA)
        with urllib.request.urlopen(req, timeout=6) as r:
            rel = json.loads(r.read(2_000_000))
        latest, current = _version_tuple(rel.get("tag_name", "")), _version_tuple(VERSION)
        asset = next((a for a in rel.get("assets", []) if a.get("name") == ASSET_NAME), None)
        newer = bool(latest and current and latest > current and asset and _host_ok(asset.get("browser_download_url", "")))
        digest = str(asset.get("digest") or "") if asset else ""
        with _lock:
            _state.update(checked=True, available=newer, latest=str(rel.get("tag_name", "")).lstrip("v") if newer else None,
                          notes=str(rel.get("body") or "")[:600] if newer else "", url=asset["browser_download_url"] if newer else None,
                          sha256=digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None,
                          size=asset.get("size") if newer else None)
    except Exception:
        with _lock:
            _state["checked"] = True            # offline, private repository, rate limited...: just say nothing
    return status()


def status() -> dict:
    with _lock:
        return {"current": VERSION, "available": _state["available"], "latest": _state["latest"], "notes": _state["notes"],
                "can_install": FROZEN}


def start_background() -> None:
    def loop():
        while True:
            check()
            time.sleep(CHECK_EVERY)
    threading.Thread(target=loop, daemon=True).start()


def leftovers_cleanup() -> None:
    """Remove a half-downloaded update left behind by an earlier attempt."""
    if FROZEN:
        try:
            (Path(sys.executable).resolve().parent / "SimTracker.update.exe").unlink()
        except OSError:
            pass


def _download(url: str, dest: Path, expected_sha: str | None) -> None:
    class SameSite(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not _host_ok(newurl):
                raise urllib.error.URLError("redirected somewhere that is not GitHub")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(SameSite)
    h, total = hashlib.sha256(), 0
    try:
        with opener.open(urllib.request.Request(url, headers=_UA), timeout=30) as r, dest.open("wb") as out:
            first = True
            while chunk := r.read(1 << 20):
                if first and chunk[:2] != b"MZ":
                    raise ValueError("the downloaded file is not a program")
                first = False
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("the downloaded file is too big")
                h.update(chunk)
                out.write(chunk)
        if total < MIN_BYTES:
            raise ValueError("the downloaded file is too small")
        if expected_sha and h.hexdigest().lower() != expected_sha:
            raise ValueError("the downloaded file does not match its checksum")
    except Exception:
        dest.unlink(missing_ok=True)
        raise


def _swap_and_restart(new_exe: Path, old_exe: Path, pid: int) -> None:
    """Start a helper that waits until this program has closed, replaces the exe and starts it again."""
    script = Path(tempfile.gettempdir()) / "simtracker_update.bat"
    script.write_text("\r\n".join([
        "@echo off",
        "echo Updating SimTracker, one moment...",
        "set /a tries=0",
        ":swap",
        "set /a tries+=1",
        f'move /y "{new_exe}" "{old_exe}" >nul 2>&1',
        "if not errorlevel 1 goto done",
        "if %tries% GEQ 60 goto failed",
        "ping -n 2 127.0.0.1 >nul",
        "goto swap",
        ":done",
        f'start "" "{old_exe}" --after-update',
        "goto end",
        ":failed",
        f'echo SimTracker could not be updated. Please download the new SimTracker.exe by hand and put it over the old one.',
        "pause",
        ":end",
        '(goto) 2>nul & del "%~f0"',
    ]) + "\r\n", encoding="ascii")
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)          # a small window that says what is going on (and why, if it fails)
    subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=flags, close_fds=True, cwd=str(old_exe.parent))


def install(exit_after=lambda: os._exit(0)) -> None:
    """Download the new version and restart into it. Returns only on failure (raises); on success this program exits."""
    if not FROZEN:
        raise ValueError("Updating only works in the double-click program (SimTracker.exe).")
    with _lock:
        url, sha = _state["url"], _state["sha256"]
        ok = _state["available"]
    if not ok or not url:
        raise ValueError("There is no newer version to install.")
    old_exe = Path(sys.executable).resolve()
    new_exe = old_exe.with_name("SimTracker.update.exe")
    try:
        _download(url, new_exe, sha)
    except Exception as e:
        raise ValueError(f"Could not download the update: {e}") from None
    _swap_and_restart(new_exe, old_exe, os.getpid())
    threading.Timer(1.0, exit_after).start()
