"""P33 (the mechanism half): the inhibitor is bound to a pid and dies with it.

On macOS `start(pid)` spawns `caffeinate -i -w <pid>`; killing that pid must
end the caffeinate child within 2 s with nobody calling `stop()`. The bell is
checked through a recorded spawn so the suite stays silent.
"""

import subprocess
import sys
import time

import pytest

import keep_awake

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="caffeinate is macOS")


def _alive(pid: int) -> bool:
    """Running, as the OS sees it. A zombie (exited, not yet reaped by this
    process) is not running: it inhibits nothing."""
    res = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                         capture_output=True, text=True)
    return res.returncode == 0 and not res.stdout.strip().startswith("Z")


@mac_only
def test_killing_the_watched_pid_ends_caffeinate_within_two_seconds():
    watched = subprocess.Popen(["sleep", "60"])
    try:
        info = keep_awake.start(watched.pid)
        assert info["ok"] and info["mechanism"] == "caffeinate"
        child = info["child_pid"]
        args = subprocess.run(["ps", "-o", "args=", "-p", str(child)],
                              capture_output=True, text=True).stdout
        assert f"caffeinate -i -w {watched.pid}" in args, args
        watched.kill()
        watched.wait()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and _alive(child):
            time.sleep(0.05)
        assert not _alive(child), "caffeinate outlived the pid it was bound to"
    finally:
        if watched.poll() is None:
            watched.kill()
        keep_awake.stop()


@mac_only
def test_stop_ends_the_inhibitor_and_is_idempotent():
    info = keep_awake.start()
    child = info["child_pid"]
    assert _alive(child)
    keep_awake.stop()
    assert not _alive(child)
    keep_awake.stop()
    assert keep_awake._child is None


@mac_only
def test_start_twice_reuses_the_live_child():
    first = keep_awake.start()
    try:
        assert keep_awake.start()["child_pid"] == first["child_pid"]
    finally:
        keep_awake.stop()


def test_bell_writes_the_terminal_bell_and_plays_the_mac_sound(monkeypatch, capsys):
    spawned = []

    class _Fake:
        def __init__(self, argv, **kw):
            spawned.append(argv)
    monkeypatch.setattr(keep_awake.subprocess, "Popen", _Fake)
    keep_awake.bell()
    assert capsys.readouterr().out == "\a"
    if sys.platform == "darwin":
        assert spawned == [["afplay", "/System/Library/Sounds/Glass.aiff"]]
    else:
        assert spawned == []


def test_bell_fails_open_when_the_player_is_missing(monkeypatch):
    def _boom(*a, **kw):
        raise OSError("no afplay")
    monkeypatch.setattr(keep_awake.subprocess, "Popen", _boom)
    keep_awake.bell()


def test_start_fails_open_when_caffeinate_is_missing(monkeypatch):
    def _boom(*a, **kw):
        raise OSError("no caffeinate")
    monkeypatch.setattr(keep_awake.subprocess, "Popen", _boom)
    monkeypatch.setattr(keep_awake, "_child", None)
    monkeypatch.setattr(keep_awake.sys, "platform", "darwin")
    assert keep_awake.start(1) == {"ok": False, "mechanism": "caffeinate", "child_pid": None}
    keep_awake.stop()


def test_windows_flags_are_the_documented_values():
    assert keep_awake.ES_CONTINUOUS == 0x80000000
    assert keep_awake.ES_SYSTEM_REQUIRED == 0x00000001
    if sys.platform == "win32":
        info = keep_awake.start()
        assert info["mechanism"] == "SetThreadExecutionState" and info["ok"]
        keep_awake.stop()


# ── P33: the long-lived holder the install uses (setup_flow _keepawake) ──────
#
# `next` is a short-lived process, so an inhibitor bound to its pid ends a
# second after it exits. The orchestrator therefore starts one detached holder
# per install that runs `keep_awake.start(os.getpid())` on ITSELF and lives
# while the checklist is open. Killing the holder must end caffeinate within
# 2 s, exactly as killing any watched pid does above.

def _holder_state(tmp_path, monkeypatch):
    import json
    state = tmp_path / "state"
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(state))
    monkeypatch.setenv("VAN_GOGH_SETUP_LOGS", str(tmp_path / "logs"))
    import setup_state as ss
    ss.save(ss.new("r-hold"))
    return state


@mac_only
def test_killing_the_install_holder_ends_caffeinate_within_two_seconds(tmp_path, monkeypatch):
    import os
    import setup_flow
    _holder_state(tmp_path, monkeypatch)
    holder = subprocess.Popen([sys.executable, str(setup_flow.APP_DIR / "setup_flow.py"), "_keepawake"],
                              env=dict(os.environ), stdout=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5.0
        found = ""
        while time.monotonic() < deadline and not found:
            found = subprocess.run(["pgrep", "-f", f"caffeinate -i -w {holder.pid}"],
                                   capture_output=True, text=True).stdout.strip()
            time.sleep(0.1)
        assert found, "the holder never started caffeinate on its own pid"
        caff = int(found.split()[0])
        holder.kill()
        holder.wait()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and _alive(caff):
            time.sleep(0.05)
        assert not _alive(caff), "caffeinate outlived the holder"
    finally:
        if holder.poll() is None:
            holder.kill()


def test_the_holder_on_windows_keeps_the_execution_state_in_its_own_process(tmp_path, monkeypatch):
    """Same shape with the ctypes call stubbed: the holder's body calls
    SetThreadExecutionState with the continuous flags on entry and resets it
    on exit, in the holder process, never in `next`."""
    import os
    import types
    import setup_flow
    import setup_state as ss
    _holder_state(tmp_path, monkeypatch)
    calls = []
    kernel32 = types.SimpleNamespace(SetThreadExecutionState=lambda flags: calls.append(flags) or 1)
    monkeypatch.setattr(sys, "platform", "win32")
    import ctypes
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=kernel32), raising=False)
    ticks = {"n": 0}

    def sleep(_):
        ticks["n"] += 1
        if ticks["n"] == 2:
            ss.remove()

    why = setup_flow.keepawake_loop(max_seconds=60, poll=1, clock=lambda: ticks["n"], sleep=sleep)
    assert why == "checklist gone"
    assert calls == [keep_awake.ES_CONTINUOUS | keep_awake.ES_SYSTEM_REQUIRED, keep_awake.ES_CONTINUOUS]
