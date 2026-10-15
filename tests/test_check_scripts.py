"""P22: the standalone readiness check, with each item removed in turn.

`check.sh` is run under a fake PATH holding stubs for `claude`, `curl` and
`xcode-select` and nothing else but `grep`, in a fake HOME, so every item it
checks can be taken away one at a time: the named line must appear and READY
must not. The full case prints READY, and prints it through the piped form
too (`cat check.sh | bash`), with a `claude` stub that swallows stdin, which
is what proves the script never lets a child read the pipe it arrives on.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECK_SH = ROOT / "check.sh"
CHECK_PS1 = ROOT / "check.ps1"
HOSTS = ("github.com", "astral.sh", "pypi.org", "accounts.google.com",
         "login.microsoftonline.com")

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="check.sh is the Mac form; check.ps1 runs in the Windows CI step")


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def fake_env(tmp_path: Path, *, claude=True, signed_in=True, devtools=True,
             key="downloads", blocked=(), claude_off_path=False,
             codex=False, codex_signed_in=True) -> dict:
    """A PATH and HOME where exactly the named items exist."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if codex:
        # The real `codex login status` prints this line (captured 2026-10-14).
        line = "Logged in using ChatGPT" if codex_signed_in else "Not logged in"
        _stub(bin_dir / "codex",
              'cat >/dev/null\nif [ "$1 $2" = "login status" ]; then '
              f'printf \'{line}\\n\'; fi\nexit 0\n')
    # grep is the one tool the check needs; cat is what the stdin-eating
    # claude stub needs (a PATH without it would make that stub eat nothing
    # and the piped-form test vacuous).
    for tool in ("grep", "cat"):
        real = shutil.which(tool)
        assert real, f"{tool} is needed to run the check"
        os.symlink(real, bin_dir / tool)
    if claude:
        # Eats stdin on purpose: a check run as `curl | sh` must survive it.
        logged = "true" if signed_in else "false"
        _stub(bin_dir / "claude",
              'cat >/dev/null\nif [ "$1 $2" = "auth status" ]; then '
              f'printf \'{{"loggedIn": {logged}}}\\n\'; fi\nexit 0\n')
    if devtools:
        _stub(bin_dir / "xcode-select", 'printf "/Library/Developer/CommandLineTools\\n"\nexit 0\n')
    blocked_list = " ".join(blocked)
    _stub(bin_dir / "curl",
          'for a in "$@"; do case "$a" in https://*) u="$a";; esac; done\n'
          'h="${u#https://}"; h="${h%%/*}"\n'
          f'for b in {blocked_list}; do [ "$b" = "$h" ] && exit 28; done\n'
          'exit 0\n')
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True, exist_ok=True)
    if claude_off_path:
        # Where Anthropic's installer puts the command; not on the fake PATH.
        (home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
        _stub(home / ".local" / "bin" / "claude", "exit 0\n")
    (home / "Desktop").mkdir(exist_ok=True)
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    state = tmp_path / "state"
    (state / "keyfile").mkdir(parents=True, exist_ok=True)
    where = {"downloads": home / "Downloads" / "van-gogh-key (1).json",
             "desktop": home / "Desktop" / "van-gogh-key.json",
             "cwd": cwd / "van-gogh-key-acme.json",
             "state": state / "keyfile" / "van-gogh-key.json"}
    if key:
        where[key].write_text("{}", encoding="utf-8")
    # SHELL is pinned: when it is absent, bash fills it in from the login
    # shell, which is zsh on a Mac and bash on the macOS CI runner, and the
    # PATH-fix line names a different profile file for each.
    return {"PATH": str(bin_dir), "HOME": str(home), "LANG": "C", "SHELL": "/bin/zsh",
            "VAN_GOGH_STATE_DIR": str(state), "_CWD": str(cwd)}


def run_check(env: dict, shell="bash", piped=False) -> subprocess.CompletedProcess:
    cwd = env.pop("_CWD")
    # The child's PATH holds only the stubs, so the shell is named absolutely.
    shell = shutil.which(shell)
    if piped:
        return subprocess.run([shell], input=CHECK_SH.read_text(encoding="utf-8"),
                              env=env, cwd=cwd, capture_output=True, text=True)
    return subprocess.run([shell, str(CHECK_SH)], env=env, cwd=cwd,
                          capture_output=True, text=True)


@posix_only
@pytest.mark.parametrize("shell", ["bash", "sh"])
def test_everything_present_prints_ready(tmp_path, shell):
    res = run_check(fake_env(tmp_path), shell=shell)
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.rstrip().splitlines()[-1] == "READY"
    assert "MISSING" not in res.stdout


@posix_only
def test_piped_form_survives_a_child_that_reads_stdin(tmp_path):
    res = run_check(fake_env(tmp_path), piped=True)
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.rstrip().splitlines()[-1] == "READY"
    # every host line printed: the script was not swallowed part way
    for host in HOSTS:
        assert f"ok: {host} is reachable" in res.stdout


@posix_only
@pytest.mark.parametrize("removed,expect", [
    (dict(claude=False), "The Claude command is not installed"),
    (dict(signed_in=False), "Claude is installed but not signed in"),
    (dict(devtools=False), "developer tools are not installed"),
    (dict(key=None), "No key file found"),
] + [(dict(blocked=(h,)), f"Cannot reach {h} over HTTPS") for h in HOSTS])
def test_each_item_removed_is_named_and_ready_withheld(tmp_path, removed, expect):
    res = run_check(fake_env(tmp_path, **removed))
    assert res.returncode == 1, res.stdout + res.stderr
    assert expect in res.stdout, res.stdout
    assert "READY" not in res.stdout.replace("NOT READY", "")
    assert res.stdout.count("MISSING:") == 1, res.stdout


# The line Anthropic's documentation gives for a `claude` that is installed
# but not on the PATH (code.claude.com/docs/en/troubleshoot-install, "Verify
# your PATH"): zsh on a Mac, and Bash on a Mac.
PATH_FIX_ZSH = """echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc"""
PATH_FIX_BASH = """echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bash_profile"""


@posix_only
def test_claude_installed_but_off_the_path_gets_the_path_fix_not_the_install_line(tmp_path):
    """Telling someone whose install worked to install again points the wrong
    way. Found in the installer's own folder, the check names the real fault."""
    res = run_check(fake_env(tmp_path, claude=False, claude_off_path=True))
    assert res.returncode == 1, res.stdout + res.stderr
    line = [ln for ln in res.stdout.splitlines() if ln.startswith("MISSING")]
    assert len(line) == 1, res.stdout
    assert line[0] == ("MISSING: Claude is installed but this window cannot find it yet. "
                       "Paste this line into Terminal and press Return: "
                       + PATH_FIX_ZSH + "   Then open a new window.")
    assert "The Claude command is not installed" not in res.stdout
    assert "READY" not in res.stdout.replace("NOT READY", "")


@posix_only
def test_a_bash_user_is_given_the_bash_profile_line(tmp_path):
    env = fake_env(tmp_path, claude=False, claude_off_path=True)
    env["SHELL"] = "/bin/bash"
    res = run_check(env)
    assert PATH_FIX_BASH + "   Then open a new window." in res.stdout
    assert "~/.zshrc" not in res.stdout


@posix_only
def test_claude_not_installed_anywhere_still_gets_the_install_line(tmp_path):
    res = run_check(fake_env(tmp_path, claude=False))
    assert res.returncode == 1
    assert "The Claude command is not installed" in res.stdout
    assert "curl -fsSL https://claude.ai/install.sh | bash" in res.stdout
    assert "cannot find it yet" not in res.stdout


@posix_only
def test_claude_on_the_path_wins_over_the_installer_folder(tmp_path):
    res = run_check(fake_env(tmp_path, claude=True, claude_off_path=True))
    assert res.returncode == 0 and "cannot find it yet" not in res.stdout


def test_windows_check_tells_off_path_from_not_installed():
    """check.ps1 runs only in the Windows CI step (which executes this branch
    with a file planted in the installer's folder); here its text is read."""
    text = CHECK_PS1.read_text(encoding="utf-8")
    assert r'".local\bin\claude.exe"' in text  # the documented install location
    plain = text.replace("''", "'")  # a single-quoted PowerShell string doubles its quotes
    assert ("Claude is installed but this window cannot find it yet. Paste this line into "
            "PowerShell and press Enter: "
            "$currentPath = [Environment]::GetEnvironmentVariable('PATH', 'User'); "
            "[Environment]::SetEnvironmentVariable('PATH', "
            r'"$currentPath;$env:USERPROFILE\.local\bin", ' "'User')"
            "   Then open a new window.") in plain
    off_path = text.index("cannot find it yet")
    not_installed = text.index("The Claude command is not installed")
    assert text.index("Get-Command claude") < off_path < not_installed
    assert "irm https://claude.ai/install.ps1 | iex" in text[not_installed:]
    # the fix must stay literal: in a double-quoted string PowerShell would
    # expand $currentPath and $env:USERPROFILE before printing it
    fix_line = next(ln for ln in text.splitlines() if "cannot find it yet" in ln)
    assert fix_line.strip().startswith("Miss ('")


@posix_only
def test_a_blocked_host_names_the_proxy_in_plain_words(tmp_path):
    res = run_check(fake_env(tmp_path, blocked=("astral.sh",)))
    line = [ln for ln in res.stdout.splitlines() if ln.startswith("MISSING")][0]
    assert "corporate proxy" in line and "astral.sh" in line and "ask IT" in line


@posix_only
@pytest.mark.parametrize("place", ["desktop", "cwd", "state"])
def test_key_file_found_in_every_place_including_the_private_folder(tmp_path, place):
    """P34: Downloads, Desktop, the current folder, and S/keyfile all count."""
    res = run_check(fake_env(tmp_path, key=place))
    assert res.returncode == 0, res.stdout
    assert "ok: key file found" in res.stdout


@posix_only
def test_renamed_variant_counts(tmp_path):
    env = fake_env(tmp_path, key=None)
    (Path(env["HOME"]) / "Downloads" / "van-gogh-key (2).json").write_text("{}")
    res = run_check(env)
    assert "ok: key file found" in res.stdout and res.returncode == 0


@posix_only
def test_shell_syntax():
    for shell in ("sh", "bash"):
        subprocess.run([shell, "-n", str(CHECK_SH)], check=True)


@posix_only
def test_codex_host_is_ready_with_codex_signed_in_and_no_claude(tmp_path):
    """P14: under VAN_GOGH_HOST=codex the check asks Codex, not Claude."""
    env = fake_env(tmp_path, claude=False, codex=True)
    env["VAN_GOGH_HOST"] = "codex"
    res = run_check(env)
    assert "ok: Codex is installed and signed in" in res.stdout
    assert "ok: chatgpt.com is reachable" in res.stdout
    assert "Claude" not in res.stdout
    assert res.stdout.rstrip().endswith("READY") and res.returncode == 0


@posix_only
def test_codex_host_names_a_missing_or_signed_out_codex(tmp_path):
    signed_out = tmp_path / "signed-out"; signed_out.mkdir()
    env = fake_env(signed_out, claude=False, codex=True, codex_signed_in=False)
    env["VAN_GOGH_HOST"] = "codex"
    res = run_check(env)
    assert "MISSING: Codex is installed but not signed in" in res.stdout and res.returncode == 1
    absent = tmp_path / "absent"; absent.mkdir()
    env = fake_env(absent, claude=False, codex=False)
    env["VAN_GOGH_HOST"] = "codex"
    res = run_check(env)
    if Path("/Applications/ChatGPT.app/Contents/Resources/codex").exists():
        # This Mac has the bundled binary, which the check rightly finds; with
        # a fake HOME it is signed out, which is the other named fault.
        assert "MISSING: Codex is installed but not signed in" in res.stdout
    else:
        assert "MISSING: The Codex command is not installed" in res.stdout
        assert "https://chatgpt.com/codex/install.sh | sh" in res.stdout
    assert not any(line.strip() == "READY" for line in res.stdout.splitlines())


@posix_only
def test_the_host_flag_form_and_the_claude_default(tmp_path):
    flagged = tmp_path / "flag"; flagged.mkdir()
    env = fake_env(flagged, claude=False, codex=True)
    cwd = env.pop("_CWD")
    res = subprocess.run([shutil.which("sh"), str(CHECK_SH), "--host", "codex"],
                         env=env, cwd=cwd, capture_output=True, text=True)
    assert "ok: Codex is installed and signed in" in res.stdout
    # With no host named, a Codex stub on PATH changes nothing: Claude is asked.
    both = tmp_path / "both"; both.mkdir()
    env = fake_env(both, claude=True, codex=True)
    res = run_check(env)
    assert "ok: Claude is installed and signed in" in res.stdout and "Codex" not in res.stdout


def test_scripts_are_small_and_self_contained():
    """Served from the public repo root before the plugin exists: no imports
    of anything beside them, under 150 lines each."""
    for script in (CHECK_SH, CHECK_PS1):
        text = script.read_text(encoding="utf-8")
        assert len(text.splitlines()) < 150, script.name
        # The plugin's app/ folder, never the ChatGPT app bundle's path.
        assert "/app/" not in text and "app\\" not in text, script.name
        assert "CLAUDE_PLUGIN_ROOT" not in text, script.name
        for host in HOSTS:
            assert host in text, f"{script.name} does not check {host}"
        assert "van-gogh-key*.json" in text, script.name
        assert "READY" in text, script.name


def test_windows_check_covers_the_store_alias_and_signin():
    text = CHECK_PS1.read_text(encoding="utf-8")
    assert "WindowsApps" in text
    assert "claude auth status --json" in text
    assert "loggedIn" in text
    assert "git --version" in text
    assert "keyfile" in text, "the private folder (P34) must be searched"
    assert "-TimeoutSec 5" in text
    # irm | iex runs in the caller's shell: an exit would close it.
    assert "\nexit" not in text


def test_mac_check_uses_the_documented_signin_probe():
    text = CHECK_SH.read_text(encoding="utf-8")
    assert "claude auth status --json" in text
    assert "xcode-select -p" in text
    assert "--max-time 5" in text
    assert "keyfile" in text, "the private folder (P34) must be searched"


def test_windows_check_has_the_codex_branch():
    """check.ps1 cannot run here; its Codex branch is held to the same
    shape as check.sh's, and the Windows CI job runs it with a stub."""
    text = CHECK_PS1.read_text(encoding="utf-8")
    assert 'param([Alias("Host")][string]$Client = "")' in text
    assert '$env:VAN_GOGH_HOST' in text and '"codex"' in text
    assert "codex login status" in text and 'Logged in' in text
    assert "Codex is installed and signed in" in text
    assert "Codex is installed but not signed in" in text
    assert "The Codex command is not installed" in text and "install.ps1" in text
    assert '$hosts += "chatgpt.com"' in text
    assert "Claude is installed and signed in" in text, "the Claude branch stays"
