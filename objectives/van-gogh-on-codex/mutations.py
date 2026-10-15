#!/usr/bin/env python3
"""P27: each two-host guard is proved by one mutation, on a copy, never here.

Four mutations, each applied to a fresh copy of the checkout, each followed
by the full suite on that copy; a mutation passes when the set of failing
test FILES equals its own guard and nothing else:

  branch   drop the codex entry of one dispatch row       -> test_host_branches.py
  literal  plant a ["codex", "exec", ...] list in a module -> test_no_host_literals.py
  tool     name a new Claude tool in a copied skill        -> test_tool_name_map.py
  flag     rename a flag in host.CODEX_EXEC_FLAGS          -> test_codex_surface.py
           (run with VAN_GOGH_REQUIRE_CODEX=1)

A baseline run on the unmutated copy must be green first, or the matrix
proves nothing. The one live test in the suite (a model call in
test_priority_judge) is deselected: it is not a guard and it is a sample.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / ".venv" / "bin" / "python")
DESELECT = ["--deselect",
            "tests/test_priority_judge.py::test_live_a_day_name_is_worked_out_from_when_the_message_was_sent"]

MUTATIONS = {
    "branch": ("app/host.py",
               '"other_plugin_roots": {"claude": _other_plugins_claude, "codex": _other_plugins_codex},',
               '"other_plugin_roots": {"claude": _other_plugins_claude},',
               {"test_host_branches.py"}),
    "literal": ("app/kpi_html.py",
                "\nimport host\n",
                "\nimport host\n_PLANTED = [\"codex\", \"exec\", \"x\"]\n",
                {"test_no_host_literals.py"}),
    "tool": ("skills/release-notes/SKILL.md",
             "\n",
             "\nUse the NotebookEdit tool to finish.\n",
             {"test_tool_name_map.py"}),
    "flag": ("app/host.py",
             '"--json", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",',
             '"--json", "--ephemeral", "--ignore-user-config", "--skip-git-check",',
             {"test_codex_surface.py"}),
}


def fresh_copy(tmp: Path) -> Path:
    dst = tmp / "copy"
    # objectives/ stays: two unrelated tests import scripts from earlier objectives.
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", ".venv", ".pytest_cache", "__pycache__"))
    return dst


def run_suite(copy: Path, extra_env=None) -> set:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(extra_env or {})}
    env.pop("VAN_GOGH_HOST", None)
    res = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x" if False else "--continue-on-collection-errors", *DESELECT],
                         cwd=str(copy), env=env, capture_output=True, text=True, timeout=1800)
    failed = set(re.findall(r"^(?:FAILED|ERROR) tests/([^:\s]+)", res.stdout, re.M))
    tail = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else res.stderr[-200:]
    return failed, tail


if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
    print(__doc__ or "no usage text"); sys.exit(0)


def main() -> int:
    only = set(sys.argv[1:])            # e.g. `mutations.py tool` reruns one row
    tmp = Path(tempfile.mkdtemp(prefix="vg-mutations-"))
    bad = 0
    copy = fresh_copy(tmp)
    assert (copy / "app" / "host.py").exists()
    failed, tail = run_suite(copy, {"VAN_GOGH_REQUIRE_CODEX": "1"})
    print(f"baseline: failing files {sorted(failed) or 'none'} | {tail}")
    if failed:
        print("baseline is not green; the matrix proves nothing"); return 1
    for name, (rel, old, new, expect) in MUTATIONS.items():
        if only and name not in only:
            continue
        shutil.rmtree(copy); copy = fresh_copy(tmp)
        path = copy / rel; text = path.read_text(encoding="utf-8")
        if name == "tool":
            path.write_text(text + new, encoding="utf-8")
        else:
            assert text.count(old) == 1, (name, text.count(old))
            path.write_text(text.replace(old, new), encoding="utf-8")
        assert (copy / rel).read_text(encoding="utf-8") != ROOT.joinpath(rel).read_text(encoding="utf-8")
        failed, tail = run_suite(copy, {"VAN_GOGH_REQUIRE_CODEX": "1"})
        ok = failed == expect
        bad += 0 if ok else 1
        print(f"{name}: {'PASS' if ok else 'FAIL'} red files {sorted(failed)} expected {sorted(expect)} | {tail}")
    shutil.rmtree(tmp, ignore_errors=True)
    ran = len(only or MUTATIONS)
    print(f"{ran - bad} of {ran} mutations caught by exactly their own guard")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
