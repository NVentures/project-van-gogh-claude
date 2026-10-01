"""app/pick_vault.py — native folder picker for the vault path.

Every native dialog is mocked: no real dialog opens and the suite stays
headless/CI-safe. Tests drive each OS branch by monkeypatching sys.platform,
shutil.which, the environment, and subprocess.run.
"""
import subprocess

import pick_vault as p


def _proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# --- macOS -----------------------------------------------------------------

def test_macos_picked(monkeypatch):
    monkeypatch.setattr(p.sys, "platform", "darwin")
    monkeypatch.setattr(p.shutil, "which", lambda name: "/usr/bin/osascript" if name == "osascript" else None)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return _proc(stdout="/Users/you/Obsidian Vault/\n")

    monkeypatch.setattr(p.subprocess, "run", fake_run)
    assert p.pick_folder() == "/Users/you/Obsidian Vault/"
    assert calls[0][0] == "/usr/bin/osascript"
    assert "shell" not in str(calls)  # arg list, never shell=True


def test_macos_no_osascript_is_no_picker(monkeypatch):
    monkeypatch.setattr(p.sys, "platform", "darwin")
    monkeypatch.setattr(p.shutil, "which", lambda name: None)
    assert p.pick_folder() is p._NO_PICKER


def test_macos_cancel(monkeypatch):
    monkeypatch.setattr(p.sys, "platform", "darwin")
    monkeypatch.setattr(p.shutil, "which", lambda name: "/usr/bin/osascript" if name == "osascript" else None)
    monkeypatch.setattr(p.subprocess, "run", lambda *a, **k: _proc(returncode=1, stderr="User canceled."))
    assert p.pick_folder() is p._CANCELLED


def test_macos_no_windowserver_is_no_picker(monkeypatch):
    monkeypatch.setattr(p.sys, "platform", "darwin")
    monkeypatch.setattr(p.shutil, "which", lambda name: "/usr/bin/osascript" if name == "osascript" else None)
    monkeypatch.setattr(p.subprocess, "run", lambda *a, **k: _proc(returncode=1, stderr="execution error"))
    assert p.pick_folder() is p._NO_PICKER


# --- Windows ---------------------------------------------------------------

def test_windows_picked(monkeypatch):
    monkeypatch.setattr(p.sys, "platform", "win32")
    monkeypatch.setattr(p.shutil, "which", lambda name: "C:\\Windows\\powershell.exe" if name == "powershell" else None)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return _proc(stdout="C:\\Users\\you\\Vault\r\n")

    monkeypatch.setattr(p.subprocess, "run", fake_run)
    assert p.pick_folder() == "C:\\Users\\you\\Vault"
    assert calls[0][0] == "C:\\Windows\\powershell.exe"


def test_windows_no_powershell_is_no_picker(monkeypatch):
    monkeypatch.setattr(p.sys, "platform", "win32")
    monkeypatch.setattr(p.shutil, "which", lambda name: None)
    assert p.pick_folder() is p._NO_PICKER


def test_windows_cancel_empty_output(monkeypatch):
    monkeypatch.setattr(p.sys, "platform", "win32")
    monkeypatch.setattr(p.shutil, "which", lambda name: "C:\\Windows\\powershell.exe" if name == "powershell" else None)
    monkeypatch.setattr(p.subprocess, "run", lambda *a, **k: _proc(stdout=""))
    assert p.pick_folder() is p._CANCELLED


# --- Linux -----------------------------------------------------------------

def test_linux_no_display_is_no_picker(monkeypatch):
    monkeypatch.setattr(p.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert p.pick_folder() is p._NO_PICKER


def test_linux_no_picker_binary(monkeypatch):
    monkeypatch.setattr(p.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(p.shutil, "which", lambda name: None)
    assert p.pick_folder() is p._NO_PICKER


def test_linux_zenity_picked(monkeypatch):
    monkeypatch.setattr(p.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(p.shutil, "which", lambda name: "/usr/bin/zenity" if name == "zenity" else None)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return _proc(stdout="/home/you/vault\n")

    monkeypatch.setattr(p.subprocess, "run", fake_run)
    assert p.pick_folder() == "/home/you/vault"
    assert calls[0][:3] == ["/usr/bin/zenity", "--file-selection", "--directory"]


def test_linux_zenity_cancel(monkeypatch):
    monkeypatch.setattr(p.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(p.shutil, "which", lambda name: "/usr/bin/zenity" if name == "zenity" else None)
    monkeypatch.setattr(p.subprocess, "run", lambda *a, **k: _proc(returncode=1))
    assert p.pick_folder() is p._CANCELLED


# --- main() marker blocks --------------------------------------------------

def test_main_emits_pick_block(monkeypatch, capsys):
    monkeypatch.setattr(p, "pick_folder", lambda *a, **k: "/some/vault")
    assert p.main([]) == 0
    out = capsys.readouterr().out
    assert "===VANGOGH_VAULT_PICK===" in out
    assert "path=/some/vault" in out
    assert "===END===" in out


def test_main_emits_cancelled_block(monkeypatch, capsys):
    monkeypatch.setattr(p, "pick_folder", lambda *a, **k: p._CANCELLED)
    p.main([])
    assert "===VANGOGH_VAULT_CANCELLED===" in capsys.readouterr().out


def test_main_emits_no_picker_block(monkeypatch, capsys):
    monkeypatch.setattr(p, "pick_folder", lambda *a, **k: p._NO_PICKER)
    p.main([])
    assert "===VANGOGH_VAULT_NO_PICKER===" in capsys.readouterr().out
