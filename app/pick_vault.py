"""
pick_vault.py — open a native OS folder picker so the user can select their
vault folder during /install-van-gogh instead of typing the path.

Cross-platform, no extra dependency: shells out to the dialog tool already
present per OS (osascript on macOS, PowerShell's FolderBrowserDialog on Windows,
zenity/kdialog on Linux). Never raises on cancel or a missing GUI — it maps every
outcome to a machine-readable marker block so the installer can branch on stdout,
mirroring the ===VANGOGH_AUTH=== convention in auth_bootstrap.py.

    ===VANGOGH_VAULT_PICK===        picked a folder; a `path=` line follows
    ===VANGOGH_VAULT_CANCELLED===   the user closed the dialog
    ===VANGOGH_VAULT_NO_PICKER===   no GUI / no picker binary; fall back to typed input

Run via the installer:
    python app/pick_vault.py
"""

import os
import shutil
import subprocess
import sys

from platform_compat import NO_WINDOW

DEFAULT_PROMPT = "Select your vault folder"

# Sentinels distinguishing a cancel from an unavailable-picker outcome.
_CANCELLED = object()
_NO_PICKER = object()


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    # NO_WINDOW hides the console host of e.g. the PowerShell picker child;
    # the folder-picker dialog itself is a GUI window and still shows.
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=NO_WINDOW,
    )


def _pick_macos(prompt: str):
    osascript = shutil.which("osascript")
    if not osascript:
        return _NO_PICKER
    script = (
        f'POSIX path of (choose folder with prompt "{prompt}")'
    )
    try:
        proc = _run([osascript, "-e", script])
    except (OSError, FileNotFoundError):
        return _NO_PICKER
    if proc.returncode != 0:
        # osascript exits 1 when the user cancels the dialog.
        if "User canceled" in (proc.stderr or ""):
            return _CANCELLED
        # No WindowServer (e.g. plain SSH session) or other environment failure.
        return _NO_PICKER
    path = (proc.stdout or "").strip()
    return path or _CANCELLED


def _pick_windows(prompt: str):
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
        f"$d.Description = '{prompt}'; "
        "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
        "{ Write-Output $d.SelectedPath }"
    )
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return _NO_PICKER
    try:
        proc = _run([powershell, "-NoProfile", "-Command", script])
    except (OSError, FileNotFoundError):
        return _NO_PICKER
    if proc.returncode != 0:
        return _NO_PICKER
    path = (proc.stdout or "").strip()
    # Empty output means the user clicked Cancel.
    return path or _CANCELLED


def _pick_linux(prompt: str):
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return _NO_PICKER

    zenity = shutil.which("zenity")
    if zenity:
        proc = _run([zenity, "--file-selection", "--directory", "--title", prompt])
        if proc.returncode == 0:
            path = (proc.stdout or "").strip()
            return path or _CANCELLED
        # zenity exits 1 on cancel, 255 on error.
        return _CANCELLED if proc.returncode == 1 else _NO_PICKER

    kdialog = shutil.which("kdialog")
    if kdialog:
        proc = _run([kdialog, "--getexistingdirectory", os.path.expanduser("~"), "--title", prompt])
        if proc.returncode == 0:
            path = (proc.stdout or "").strip()
            return path or _CANCELLED
        return _CANCELLED if proc.returncode == 1 else _NO_PICKER

    return _NO_PICKER


def pick_folder(prompt: str = DEFAULT_PROMPT):
    """Open a native folder picker. Returns the chosen absolute path string,
    or the _CANCELLED / _NO_PICKER sentinel."""
    if sys.platform == "darwin":
        return _pick_macos(prompt)
    if sys.platform == "win32":
        return _pick_windows(prompt)
    return _pick_linux(prompt)


def main(argv: list[str] | None = None) -> int:
    result = pick_folder()
    if result is _NO_PICKER:
        print("===VANGOGH_VAULT_NO_PICKER===")
        print("===END===")
    elif result is _CANCELLED:
        print("===VANGOGH_VAULT_CANCELLED===")
        print("===END===")
    else:
        print("===VANGOGH_VAULT_PICK===")
        print(f"path={result}")
        print("===END===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
