"""P8: the no-sudo scanner proves itself on a planted instance before its 0
on the real tree is believed, refuses to pass on 0 files, and ignores the
prose that forbids the very thing it hunts."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCANNER = ROOT / "tools" / "scan_no_sudo.py"

spec = importlib.util.spec_from_file_location("scan_no_sudo", SCANNER)
scan_no_sudo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan_no_sudo)


def _copy_tree(tmp_path: Path) -> Path:
    root = tmp_path / "plugin"
    (root / "app").mkdir(parents=True)
    for name in ("setup.sh", "setup.ps1", "check.sh", "check.ps1"):
        shutil.copy(ROOT / name, root / name)
    return root


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCANNER), "--root", str(root)],
                          capture_output=True, text=True)


def test_planted_sudo_in_a_reached_app_script_is_found_first(tmp_path):
    root = _copy_tree(tmp_path)
    (root / "app" / "setup_flow.py").write_text(
        'import subprocess\n'
        'subprocess.run(["sudo", "chown", "-R", "me", "/usr/local"])\n',
        encoding="utf-8")
    res = _run(root)
    assert res.returncode == 1, res.stdout
    assert "HIT sudo: app/setup_flow.py:2" in res.stdout.replace("\\", "/")
    assert "scanned 5 file(s)" in res.stdout


def test_planted_instances_in_each_entry_and_transitive_import(tmp_path):
    root = _copy_tree(tmp_path)
    (root / "setup.ps1").write_text(
        (root / "setup.ps1").read_text(encoding="utf-8")
        + "\nStart-Process powershell -Verb RunAs -ArgumentList 'x'\n", encoding="utf-8")
    (root / "check.sh").write_text(
        (root / "check.sh").read_text(encoding="utf-8") + "\nbrew install python@3.12\n",
        encoding="utf-8")
    (root / "app" / "setup_flow.py").write_text("import helper\n", encoding="utf-8")
    (root / "app" / "helper.py").write_text(
        'import os\nos.system("winget install Python.Python.3.12")\n'
        'os.system("choco install git")\n'
        'os.system("osascript -e \'do shell script \\"x\\" with administrator privileges\'")\n',
        encoding="utf-8")
    hits, count = scan_no_sudo.scan(root)
    labels = sorted(label for _, _, label, _ in hits)
    assert labels == ["RunAs", "admin dialog", "brew", "choco", "winget"]
    assert count == 6
    files = {rel.replace("\\", "/") for rel, _, _, _ in hits}
    assert files == {"setup.ps1", "check.sh", "app/helper.py"}


def test_plants_in_dynamically_reached_modules_are_found(tmp_path):
    """The evaluator planted `sudo` in keep_awake.py and the first walk missed
    it: setup_flow reaches it through `_try_import("keep_awake")`, and
    warm_start.py through `_run_script("warm_start.py", [...])`, neither of
    which is a static import or an `app/x.py` path. A copy of the REAL app/
    tree is scanned here with plants in keep_awake.py and week_review.py
    (reached via warm_start.py), and both must be found."""
    root = _copy_tree(tmp_path)
    shutil.rmtree(root / "app")
    shutil.copytree(ROOT / "app", root / "app",
                    ignore=shutil.ignore_patterns("__pycache__"))
    assert (root / "app" / "setup_flow.py").is_file(), "the orchestrator must exist for this walk"
    for name, plant in (("keep_awake.py", 'os.system("sudo pmset -a disablesleep 1")\n'),
                        ("week_review.py", 'os.system("brew install jq")\n')):
        target = root / "app" / name
        target.write_text(target.read_text(encoding="utf-8") + "\nimport os\n" + plant,
                          encoding="utf-8")
    hits, count = scan_no_sudo.scan(root)
    found = {(rel.replace("\\", "/"), label) for rel, _, label, _ in hits}
    assert ("app/keep_awake.py", "sudo") in found, hits
    assert ("app/week_review.py", "brew") in found, hits
    assert count > 60, count
    reached = {p.name for p in scan_no_sudo.reachable(root)}
    for name in ("keep_awake.py", "setup_nudge.py", "warm_start.py",
                 "meeting_ingest.py", "week_review.py"):
        assert name in reached, name


def test_the_walk_follows_dynamic_imports_and_subprocess_script_names(tmp_path):
    """Independent of what the real setup_flow does today: the three idioms
    it uses (`_try_import("x")`, `_run_script("x.py", [...])`, a script path
    inside an argv list) each reach their module, transitively."""
    root = _copy_tree(tmp_path)
    app = root / "app"
    (app / "setup_flow.py").write_text(
        'import importlib, subprocess, sys\n'
        'def _try_import(name): return importlib.import_module(name)\n'
        'def _run_script(script, argv): return subprocess.run([sys.executable, script] + argv)\n'
        '_try_import("dyn_mod")\n'
        '_run_script("worker.py", ["scan"])\n'
        'subprocess.Popen([sys.executable, "vault/scaffold.py", "x"])\n',
        encoding="utf-8")
    (app / "dyn_mod.py").write_text('import os\nos.system("sudo true")\n', encoding="utf-8")
    (app / "worker.py").write_text('def f():\n    import deeper\n', encoding="utf-8")
    (app / "deeper.py").write_text('import os\nos.system("choco install x")\n', encoding="utf-8")
    (root / "vault").mkdir()
    (root / "vault" / "scaffold.py").write_text('import os\nos.system("winget install y")\n',
                                                encoding="utf-8")
    hits, count = scan_no_sudo.scan(root)
    assert sorted(label for _, _, label, _ in hits) == ["choco", "sudo", "winget"]
    assert count == 9


def test_prose_that_forbids_the_thing_is_not_a_hit(tmp_path):
    root = _copy_tree(tmp_path)
    (root / "app" / "setup_flow.py").write_text(
        '"""Never use sudo here; brew install is banned; no winget, no choco.\n'
        'Start-Process -Verb RunAs is banned too."""\n'
        '# a comment saying sudo is banned\n'
        'x = 1  # trailing comment: sudo\n',
        encoding="utf-8")
    hits, count = scan_no_sudo.scan(root)
    assert hits == [] and count == 5


def test_real_tree_is_clean_and_scanned_more_than_zero(tmp_path=None):
    res = _run(ROOT)
    assert res.returncode == 0, res.stdout
    count = int(res.stdout.split("scanned ")[1].split(" ")[0])
    assert count >= 4, res.stdout
    assert "PASS" in res.stdout


def test_zero_files_is_skip_never_pass(tmp_path):
    empty = tmp_path / "nothing"
    empty.mkdir()
    res = _run(empty)
    assert res.returncode == 2
    assert "SKIP" in res.stdout and "PASS" not in res.stdout
    assert "scanned 0 file(s)" in res.stdout
