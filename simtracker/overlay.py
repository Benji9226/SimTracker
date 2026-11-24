"""The overlay: a small window that stays on top of the game and draws your throttle and brake for the last few seconds, with your speed in km/h.

It is a separate window, nothing is put inside the game: it reads the live numbers from the game's own shared memory (see live.py) and
draws them. It only shows over the game when Le Mans Ultimate runs in borderless or windowed mode (not exclusive fullscreen, not VR).

The graph runs a tenth of a second behind the game. That small delay is used to draw it smoothly: the line is resampled evenly between the
readings and lightly smoothed, so it moves without stepping.

Drag it with the left mouse button to move it (the position is remembered); right-click for the menu. It never takes the keyboard focus
from the game.
"""
from __future__ import annotations

import ctypes
import sys
import time
from collections import deque

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageTk

from simtracker import settings
from simtracker.live import Live

W, H = 470, 113
S = 2                            # the picture is drawn at twice the size and scaled down, which makes the lines smooth
HISTORY_S = 12.0                 # seconds of driving shown
DELAY_S = 0.10                   # the graph runs this far behind the game, so it can be drawn smoothly
STEP_S = 1 / 60                  # one point of the drawn line
PAD, TOP = 12, 38
BG, GRID = (4, 12, 20), (14, 52, 72)
CYAN, ORANGE, TEXT, DIM = (0, 229, 255), (255, 90, 54), (211, 244, 255), (106, 147, 168)
STALE_S = 1.0                    # no new game time for this long: the game is paused, in a menu, or closed

_KERNEL = np.array([1, 4, 6, 4, 1], dtype=float) / 16.0


def _font(size: float):
    for name in ("bahnschrift.ttf", "segoeuib.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, int(size * S))
        except OSError:
            continue
    return ImageFont.load_default()


def _alive(pid: int) -> bool:
    """Is the program with this process number still running?"""
    if sys.platform != "win32":
        return True
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    handle = k.OpenProcess(0x00100000, 0, pid)                    # SYNCHRONIZE
    if not handle:
        return False
    try:
        return k.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x102     # WAIT_TIMEOUT: still running
    finally:
        k.CloseHandle(ctypes.c_void_p(handle))


class Overlay:
    def __init__(self, read=None, seconds: float | None = None):
        import tkinter as tk
        if sys.platform == "win32":
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)          # sharp drawing, and positions in real pixels
            except Exception:
                pass
        self.live = None if read else Live()
        self.read = read or self.live.read
        self.deadline = time.time() + seconds if seconds else None
        self.parent = int(sys.argv[sys.argv.index("--parent") + 1]) if "--parent" in sys.argv else None
        self.samples: deque = deque()               # (game time, throttle, brake)
        self.last_t, self.last_new = None, 0.0
        self.latest: dict | None = None
        self.connected = False
        self.f_big, self.f_mid, self.f_small = _font(26), _font(12), _font(10.5)
        root = self.root = tk.Tk()
        root.title("SimTracker overlay")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.94)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        pos = settings.load().get("overlay") or {}
        x = min(max(int(pos.get("x", 24)), 0), max(sw - W, 0))
        y = min(max(int(pos.get("y", sh - H - 90)), 0), max(sh - H, 0))
        root.geometry(f"{W}x{H}+{x}+{y}")
        self.canvas = tk.Canvas(root, width=W, height=H, bg="#040c14", highlightthickness=0, cursor="fleur")
        self.canvas.pack()
        self.image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.photo = None
        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="Close overlay", command=self.close)
        self.drag = (0, 0)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
        root.update_idletasks()
        self._no_focus()
        self._tick()
        self._keep_on_top()

    # -- window behaviour --
    def _no_focus(self) -> None:
        """Clicking the overlay must not take the keyboard from the game, and it should not show up in Alt+Tab."""
        if sys.platform != "win32":
            return
        try:
            u = ctypes.windll.user32
            hwnd = u.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            style = u.GetWindowLongW(hwnd, -20)
            u.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x00000080)          # WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        except Exception:
            pass

    def _keep_on_top(self) -> None:
        self.root.attributes("-topmost", True)
        self.root.lift()
        if self.parent and not _alive(self.parent):               # SimTracker was closed: the overlay goes with it
            return self.close()
        self.root.after(2000, self._keep_on_top)

    def _press(self, e) -> None:
        self.drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _move(self, e) -> None:
        self.root.geometry(f"+{e.x_root - self.drag[0]}+{e.y_root - self.drag[1]}")

    def _release(self, _e) -> None:
        settings.save({"overlay": {"x": self.root.winfo_x(), "y": self.root.winfo_y()}})

    def close(self) -> None:
        if self.live:
            self.live.close()
        self.root.destroy()

    # -- data --
    def _tick(self) -> None:
        now = time.time()
        if self.deadline and now > self.deadline:
            return self.close()
        d = self.read()
        self.connected = d is not None
        if d and d["t"] != self.last_t:
            if self.samples and d["t"] < self.samples[-1][0]:
                self.samples.clear()                # the game time went backwards: a new session started
            self.last_t, self.last_new = d["t"], now
            self.samples.append((d["t"], d["throttle"], d["brake"]))
            while self.samples and self.samples[0][0] < d["t"] - HISTORY_S - 2:
                self.samples.popleft()
        self.latest = d if d and now - self.last_new < STALE_S else None
        self._draw(now)
        self.root.after(14, self._tick)

    def _curves(self, now: float):
        """Throttle and brake on an even time grid, ending DELAY_S behind the newest reading, lightly smoothed. Returns (times, throttle, brake)."""
        s = np.array(self.samples, dtype=float)
        newest = s[-1, 0]
        head = min(self.last_t + (now - self.last_new), newest + 0.05) - DELAY_S          # the game clock, estimated between readings
        times = head - HISTORY_S + np.arange(int(HISTORY_S / STEP_S) + 1) * STEP_S
        pad = 2 * STEP_S
        grid = np.r_[times[0] - pad, times, times[-1] + pad]
        out = []
        for col in (1, 2):
            y = np.interp(grid, s[:, 0], s[:, col])
            out.append(np.clip(np.convolve(np.pad(y, 2, mode="edge"), _KERNEL, "valid")[1:-1], 0.0, 1.0))
        return times, out[0], out[1]

    # -- drawing --
    def _draw(self, now: float) -> None:
        img = Image.new("RGBA", (W * S, H * S), BG + (255,))
        d = ImageDraw.Draw(img)
        gx0, gx1, gy0, gy1 = PAD * S, (W - PAD) * S, TOP * S, (H - 10) * S
        span = gx1 - gx0
        live = self.latest is not None and len(self.samples) > 3
        times, glow = None, None
        if live:
            times, thr, brk = self._curves(now)
        for frac in (0.0, 0.5, 1.0):
            y = gy1 - frac * (gy1 - gy0)
            d.line([(gx0, y), (gx1, y)], fill=GRID + (255,), width=1 if frac == 0.5 else S)
        if live:                                                    # vertical marks every two seconds, scrolling with the graph
            first = np.ceil(times[0] / 2.0) * 2.0
            for tt in np.arange(first, times[-1], 2.0):
                x = gx0 + (tt - times[0]) / HISTORY_S * span
                d.line([(x, gy0), (x, gy1)], fill=(10, 36, 52, 255), width=1)

        if live:
            xs = gx0 + (times - times[0]) / HISTORY_S * span
            pts = {}
            for key, v in (("thr", thr), ("brk", brk)):
                pts[key] = list(zip(xs.tolist(), (gy1 - v * (gy1 - gy0)).tolist()))
            fill = Image.new("RGBA", img.size, (0, 0, 0, 0))
            fd = ImageDraw.Draw(fill)
            fd.polygon(pts["thr"] + [(gx1, gy1), (gx0, gy1)], fill=CYAN + (38,))
            fd.polygon(pts["brk"] + [(gx1, gy1), (gx0, gy1)], fill=ORANGE + (58,))
            img = Image.alpha_composite(img, fill)
            d = ImageDraw.Draw(img)
            glow = Image.new("RGB", (W, H), (0, 0, 0))                 # the glow is made at the final size (cheaper), and added at the end
            gd = ImageDraw.Draw(glow)
            gd.line([(x / S, y / S) for x, y in pts["thr"]], fill=CYAN, width=5)
            gd.line([(x / S, y / S) for x, y in pts["brk"]], fill=ORANGE, width=5)
            glow = glow.filter(ImageFilter.GaussianBlur(3)).point(lambda v: int(v * 0.85))
            d.line(pts["thr"], fill=CYAN, width=2 * S, joint="curve")
            d.line(pts["brk"], fill=(255, 112, 80), width=2 * S, joint="curve")
            x, y = pts["thr"][-1]                                    # a bright dot where the line is being drawn
            d.ellipse([x - 3 * S, y - 3 * S, x + 3 * S, y + 3 * S], fill=(220, 252, 255))
            x, y = pts["brk"][-1]
            d.ellipse([x - 3 * S, y - 3 * S, x + 3 * S, y + 3 * S], fill=(255, 190, 170))
        else:
            msg = "WAITING FOR LE MANS ULTIMATE" if not self.connected else "NOT DRIVING"
            d.text((W * S / 2, (gy0 + gy1) / 2), msg, fill=DIM, font=self.f_mid, anchor="mm")

        # header: pedal names, speed and gear
        d.rectangle([PAD * S, 12 * S, PAD * S + 7 * S, 19 * S], fill=CYAN)
        d.text((PAD * S + 12 * S, 15.5 * S), "THROTTLE", fill=TEXT, font=self.f_small, anchor="lm")
        d.rectangle([(PAD + 84) * S, 12 * S, (PAD + 91) * S, 19 * S], fill=ORANGE)
        d.text(((PAD + 96) * S, 15.5 * S), "BRAKE", fill=TEXT, font=self.f_small, anchor="lm")
        if self.latest:
            speed, gear = self.latest["speed"], self.latest["gear"]
            d.text(((W - PAD - 38) * S, 22 * S), f"{speed:.0f}", fill=(235, 252, 255), font=self.f_big, anchor="rm")
            d.text(((W - PAD - 34) * S, 26 * S), "KM/H", fill=CYAN, font=self.f_small, anchor="lm")
            g = "R" if gear < 0 else "N" if gear == 0 else str(gear)
            bx = (W - PAD - 160) * S
            d.polygon([(bx + 6 * S, 8 * S), (bx + 30 * S, 8 * S), (bx + 30 * S, 28 * S), (bx, 28 * S), (bx, 14 * S)], outline=CYAN, width=S)
            d.text((bx + 16 * S, 18.5 * S), g, fill=(235, 252, 255), font=self.f_mid, anchor="mm")

        # frame: thin border and bright corner brackets
        d.rectangle([0, 0, W * S - 1, H * S - 1], outline=(10, 70, 92, 255), width=S)
        b = 14 * S
        for (cx, cy, sx, sy) in ((0, 0, 1, 1), (W * S - 1, 0, -1, 1), (0, H * S - 1, 1, -1), (W * S - 1, H * S - 1, -1, -1)):
            d.line([(cx, cy + sy * b), (cx, cy), (cx + sx * b, cy)], fill=CYAN, width=2 * S)
        d.line([(30 * S, 0), (W * S - 30 * S, 0)], fill=(0, 140, 170, 255), width=S)

        out = img.convert("RGB").reduce(S)
        if glow is not None:
            out = ImageChops.add(out, glow)
        self.photo = ImageTk.PhotoImage(out)
        self.canvas.itemconfig(self.image_item, image=self.photo)


def run(seconds: float | None = None) -> None:
    Overlay(seconds=seconds).root.mainloop()
