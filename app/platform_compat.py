"""Cross-platform helpers shared across Project Van Gogh scripts.

Centralizes the few places where macOS/Linux and Windows diverge so no script
has to branch on ``sys.platform`` inline. See CLAUDE.md "## Cross-platform".
"""

from __future__ import annotations

import shutil
import subprocess

# On Windows, a console-subsystem child spawned from a window-less parent
# (Claude Code's hidden tool shell, Task Scheduler, a GUI process) allocates
# its own conhost window, which flashes in the foreground for every spawn —
# every `claude` classification call, pip sync, or powershell helper. Passing
# CREATE_NO_WINDOW suppresses the window; output still flows through the
# pipes every call site already uses. 0 is a no-op on macOS/Linux, where the
# constant doesn't exist. Every subprocess call in app/ must pass
# `creationflags=NO_WINDOW` (enforced by tests/test_no_console_window.py).
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def fmt_hour_minute(dt) -> str:
    """Format a datetime as ``"9:05 AM"`` with no leading zero on the hour.

    ``%-I`` (strip the leading zero) is POSIX-only; Windows ``strftime`` needs
    ``%#I`` and raises/mis-renders on ``%-I``. Formatting with the portable,
    zero-padded ``%I`` and stripping the leading zero in Python avoids the
    platform split entirely (e.g. ``"08:05 AM"`` -> ``"8:05 AM"``).
    """
    return dt.strftime("%I:%M %p").lstrip("0")


def fmt_local_time(dt_utc, primary_tz, secondary_tz=None) -> str:
    """Format a UTC datetime in the user's timezone(s).

    Renders ``"9:00 AM PDT"`` against ``primary_tz``. When ``secondary_tz`` is
    provided (the optional dual-zone display, e.g. PT / ET), renders both
    side by side:
    ``"9:00 AM PDT / 12:00 PM EDT"``. The zone label comes from ``strftime``'s
    ``%Z`` so any configured IANA zone produces the right abbreviation, instead
    of a hardcoded ``PT``/``ET``.
    """
    primary = dt_utc.astimezone(primary_tz)
    out = f"{fmt_hour_minute(primary)} {primary.strftime('%Z')}"
    if secondary_tz is not None:
        secondary = dt_utc.astimezone(secondary_tz)
        out += f" / {fmt_hour_minute(secondary)} {secondary.strftime('%Z')}"
    return out


def claude_bin() -> str:
    """Resolve the ``claude`` CLI on ``PATH``, raising a clear error if absent.

    Uses :func:`shutil.which`, which honors ``PATHEXT`` on Windows (so it finds
    ``claude.cmd`` / ``claude.exe``) and the executable bit on POSIX.
    """
    path = shutil.which("claude")
    if not path:
        raise FileNotFoundError(
            "The 'claude' CLI was not found on PATH. Install Claude Code and "
            "make sure `claude` is on your PATH before running this script."
        )
    return path
