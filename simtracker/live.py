"""Live numbers from the game: the speed, throttle, brake and gear of the player's car, read from the game's own shared memory.

Le Mans Ultimate publishes a read-only block of memory called "LMU_Data" for tools like this (Studio 397 ships the description of it with the
game, in Support\\SharedMemoryInterface). It needs the game setting "Enable external plugins" switched on (Settings.JSON, on by default), and
nothing has to be installed or changed in the game's folder. This module only ever READS from it: it never writes, never takes the game's
lock, and never touches other drivers' data (the block also holds the whole field, which is not read).

Where things are (the game's structures are packed to 4 bytes, so the sizes simply add up; the total, 324,820 bytes, matches the block):
    general data 332 bytes + paths 1,300 + scoring 126,832  ->  the telemetry part starts at byte 128,464:
        1 byte: number of cars, 1 byte: which slot is the player's car, 1 byte: the player has a car, 1 byte padding, then 104 slots of 1,888 bytes.
    inside a slot: elapsed time (double) at 12, velocity (3 doubles, m/s) at 184, gear (int) at 352,
        raw throttle (double, 0..1) at 388, raw brake at 396, raw steering (-1..1) at 404.
"""
from __future__ import annotations

import ctypes
import math
import struct
import sys

_NAME = "LMU_Data"
_TELEMETRY = 332 + 1300 + 126832          # where the telemetry part begins
_SLOT, _SLOTS, _TOTAL = 1888, 104, 324820
_READ = 440                               # bytes read from the player's slot: everything up to the raw steering value


class Live:
    """Opens the game's memory block when it exists, and reads the player's car from it. `read()` returns None while the game is not publishing."""

    def __init__(self):
        self._handle = None
        self._ptr = None
        self._k = None
        if sys.platform == "win32":
            k = ctypes.windll.kernel32
            k.OpenFileMappingW.restype = ctypes.c_void_p
            k.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
            k.MapViewOfFile.restype = ctypes.c_void_p
            k.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
            k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
            k.CloseHandle.argtypes = [ctypes.c_void_p]
            self._k = k

    def _open(self) -> bool:
        if self._ptr or not self._k:
            return bool(self._ptr)
        handle = self._k.OpenFileMappingW(0x0004, 0, _NAME)           # FILE_MAP_READ: read only
        if not handle:
            return False
        ptr = self._k.MapViewOfFile(handle, 0x0004, 0, 0, _TOTAL)
        if not ptr:
            self._k.CloseHandle(handle)
            return False
        self._handle, self._ptr = handle, ptr
        return True

    def close(self) -> None:
        if self._ptr:
            self._k.UnmapViewOfFile(self._ptr)
            self._ptr = None
        if self._handle:
            self._k.CloseHandle(self._handle)
            self._handle = None

    def read(self) -> dict | None:
        """{"t": game time (s), "speed": km/h, "throttle": 0..1, "brake": 0..1, "gear": int} or None."""
        if not self._open():
            return None
        try:
            has_car = ctypes.string_at(self._ptr + _TELEMETRY + 2, 1)[0]
            idx = ctypes.string_at(self._ptr + _TELEMETRY + 1, 1)[0]
            if not has_car or idx >= _SLOTS:
                return None
            raw = ctypes.string_at(self._ptr + _TELEMETRY + 4 + idx * _SLOT, _READ)
        except (OSError, ValueError):
            return None
        t = struct.unpack_from("<d", raw, 12)[0]
        vx, vy, vz = struct.unpack_from("<3d", raw, 184)
        gear = struct.unpack_from("<i", raw, 352)[0]
        throttle, brake = struct.unpack_from("<2d", raw, 388)
        if not all(math.isfinite(x) for x in (t, vx, vy, vz, throttle, brake)):
            return None
        return {"t": t, "speed": math.sqrt(vx * vx + vy * vy + vz * vz) * 3.6, "gear": gear,
                "throttle": min(max(throttle, 0.0), 1.0), "brake": min(max(brake, 0.0), 1.0)}
