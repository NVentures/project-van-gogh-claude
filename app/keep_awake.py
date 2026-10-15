#!/usr/bin/env python3
"""keep_awake.py: stop the machine sleeping while an install waits, and ring.

`start(pid)` keeps the system awake for as long as the process `pid` lives.
On macOS it spawns `caffeinate -i -w <pid>`, which exits by itself when that
process does, so a killed orchestrator never leaves the inhibitor behind. On
Windows it calls `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`
through ctypes in the calling process (the flag belongs to the calling thread,
so the long-running `_job` children of the orchestrator call it themselves).
`stop()` undoes either. `bell()` writes `\\a` to the terminal and, on macOS,
plays the default notification sound; every path here fails open.
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from platform_compat import NO_WINDOW  # noqa: E402

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
MAC_SOUND = "/System/Library/Sounds/Glass.aiff"

_child: subprocess.Popen | None = None


def start(pid: int | None = None) -> dict:
    """Keep the machine awake while `pid` (default: this process) lives.

    Returns `{"ok", "mechanism", "child_pid"}`; `ok` is False where nothing
    is available (Linux, or caffeinate missing), never an exception.
    """
    global _child
    pid = int(pid or os.getpid())
    if sys.platform == "darwin":
        if _child is not None and _child.poll() is None:
            return {"ok": True, "mechanism": "caffeinate", "child_pid": _child.pid}
        try:
            _child = subprocess.Popen(
                ["caffeinate", "-i", "-w", str(pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=NO_WINDOW,
            )
        except OSError:
            return {"ok": False, "mechanism": "caffeinate", "child_pid": None}
        return {"ok": True, "mechanism": "caffeinate", "child_pid": _child.pid}
    if sys.platform == "win32":
        try:
            import ctypes
            flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
            previous = ctypes.windll.kernel32.SetThreadExecutionState(flags)
        except (AttributeError, OSError):
            return {"ok": False, "mechanism": "SetThreadExecutionState", "child_pid": None}
        return {"ok": bool(previous), "mechanism": "SetThreadExecutionState", "child_pid": None}
    return {"ok": False, "mechanism": None, "child_pid": None}


def stop() -> None:
    """End the inhibitor started by `start()`. Safe to call twice."""
    global _child
    if sys.platform == "darwin":
        if _child is not None and _child.poll() is None:
            try:
                _child.terminate()
                _child.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        _child = None
        return
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except (AttributeError, OSError):
            pass


def bell() -> None:
    """Ring: `\\a` to the terminal, plus the macOS notification sound."""
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except (OSError, ValueError):
        pass
    if sys.platform == "darwin":
        try:
            subprocess.Popen(
                ["afplay", MAC_SOUND],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=NO_WINDOW,
            )
        except OSError:
            pass
