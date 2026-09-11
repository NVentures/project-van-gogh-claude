"""Guards for the Claude Code plugin packaging.

The repo ships as the `van-gogh` plugin (`.claude-plugin/plugin.json`) and is
its own marketplace (`.claude-plugin/marketplace.json`). Installed plugins run
from Claude's plugin cache, not the user's cwd, so every skill command must
anchor on CLAUDE_PLUGIN_ROOT, never on `git rev-parse --show-toplevel`, and
cross-skill references must use the namespaced `/van-gogh:<skill>` form.
"""
import json
import subprocess
import sys
import re
import tomllib

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from platform_compat import NO_WINDOW  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_DIR = _REPO_ROOT / "skills"


def _plugin_manifest():
    return json.loads(
        (_REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )


def test_plugin_version_matches_pyproject():
    pyproject = tomllib.loads(
        (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert _plugin_manifest()["version"] == pyproject["project"]["version"]


def test_marketplace_lists_the_plugin():
    marketplace = json.loads(
        (_REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    names = [p["name"] for p in marketplace["plugins"]]
    assert _plugin_manifest()["name"] in names


def test_skills_live_at_plugin_root():
    # The plugin loader only discovers skills/ at the plugin root. A
    # .claude/skills/ directory may exist for local dev tooling a contributor
    # installed (it is gitignored), but nothing there may be TRACKED: a shipped
    # skill in that path is invisible to the loader and would silently not run.
    assert _SKILLS_DIR.is_dir()
    tracked = subprocess.run(
        ["git", "ls-files", ".claude/skills"],
        cwd=_REPO_ROOT, capture_output=True, text=True, check=False,
        creationflags=NO_WINDOW,
    ).stdout.split()
    assert not tracked, f"skills tracked outside the plugin root: {tracked[:5]}"
    assert (_SKILLS_DIR / "morning-coffee" / "SKILL.md").is_file()


def test_no_rev_parse_toplevel_in_skill_commands():
    # `git rev-parse --show-toplevel` resolves to the *user's* repo, not the
    # plugin cache. `git ... rev-parse --is-inside-work-tree` (vault guard) is
    # fine; the cwd-independent anchor is CLAUDE_PLUGIN_ROOT.
    offenders = [
        str(path.relative_to(_REPO_ROOT))
        for path in _SKILLS_DIR.rglob("*.md")
        if "rev-parse --show-toplevel" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_no_uv_invocations_in_skills():
    # Dependencies install into the stable per-user venv via plain `python -m venv`
    # + pip (requirements.txt); skills invoke the venv interpreter directly.
    offenders = [
        f"{path.relative_to(_REPO_ROOT)}:{i}"
        for path in list(_SKILLS_DIR.rglob("*.md"))
        + list((_REPO_ROOT / "scripts").glob("*.ps1"))
        for i, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if re.search(r"\buv (run|sync|--project)\b", line)
    ]
    assert offenders == []


def test_no_plugin_cache_venv_in_skills():
    # The interpreter is the stable per-user venv (~/.config/van-gogh/venv,
    # see skills/_shared/python-runtime.md) — a plugin update re-clones the
    # cache, so a venv inside it would break every skill and scheduled job.
    offenders = [
        f"{path.relative_to(_REPO_ROOT)}:{i}"
        for path in list(_SKILLS_DIR.rglob("*.md"))
        + list((_REPO_ROOT / "scripts").glob("*.ps1"))
        for i, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if "CLAUDE_PLUGIN_ROOT}/.venv" in line or "CLAUDE_PLUGIN_ROOT\\.venv" in line
    ]
    assert offenders == []


def test_every_skill_md_has_powershell_forms():
    # CLAUDE.md mandates every SKILL.md works on Windows: any skill showing
    # bash commands must show the PowerShell forms beside them, or link a
    # references/*.md in the same skill dir that carries them (progressive
    # disclosure, e.g. install-van-gogh's references/windows-commands.md).
    def has_powershell(path):
        text = path.read_text(encoding="utf-8")
        if "```bash" not in text:
            return True
        if "```powershell" in text:
            return True
        return any(
            "```powershell" in ref.read_text(encoding="utf-8")
            for ref in (path.parent / "references").glob("*.md")
            if f"references/{ref.name}" in text
        )

    offenders = [
        str(path.relative_to(_REPO_ROOT))
        for path in _SKILLS_DIR.glob("*/SKILL.md")
        if not has_powershell(path)
    ]
    assert offenders == []


def test_every_skill_md_has_frontmatter():
    # The plugin loader reads name/description from YAML frontmatter; a skill
    # without it (install-van-gogh, historically) risks not registering.
    offenders = [
        str(path.relative_to(_REPO_ROOT))
        for path in _SKILLS_DIR.glob("*/SKILL.md")
        if not path.read_text(encoding="utf-8").startswith("---\n")
    ]
    assert offenders == []


def test_cross_skill_references_are_namespaced():
    skills = sorted(
        (p.name for p in _SKILLS_DIR.iterdir() if (p / "SKILL.md").is_file()),
        key=len,
        reverse=True,
    )
    bare_ref = re.compile(
        r"(?<![\w/.:-])/(" + "|".join(map(re.escape, skills)) + r")(?!\.md\b)(?![\w-])"
    )
    offenders = []
    for path in _SKILLS_DIR.rglob("*.md"):
        for i, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if bare_ref.search(line):
                offenders.append(f"{path.relative_to(_REPO_ROOT)}:{i}: {line.strip()}")
    assert offenders == []


def test_workbench_static_files_exist_and_carry_no_dashes():
    """The Workbench ships its own HTML/CSS/JS, which are output like any other
    artifact: no em-dash or en-dash anywhere, per DESIGN.md and CLAUDE.md."""
    static = _REPO_ROOT / "app" / "workbench_static"
    names = ["index.html", "style.css", "app.js", "graph.js"]
    missing = [n for n in names if not (static / n).is_file()]
    assert missing == [], f"missing Workbench assets: {missing}"

    offenders = []
    for name in names:
        for i, line in enumerate((static / name).read_text(encoding="utf-8").splitlines(), 1):
            if "\u2014" in line or "\u2013" in line:
                offenders.append(f"app/workbench_static/{name}:{i}")
    assert offenders == [], "dash characters in shipped web assets:\n" + "\n".join(offenders)


def _frontmatter(path: Path) -> str:
    """The YAML block between the first two `---` fences."""
    return path.read_text(encoding="utf-8").split("---", 2)[1]


def test_every_skill_frontmatter_is_valid_yaml():
    """A bare `: ` inside an unquoted description is invalid YAML.

    Silent, because the harness's own loader is lenient and still lists the
    skill, so it looks installed and working while any yaml-based tooling
    trips. Three skills shipped broken this way: afternoon-tea and week-retro
    on "a focused daily retro: done today", ingest-workspace on a quote that
    closed mid-description. The fix is a folded block scalar (`description: >-`).
    """
    try:
        import yaml
    except ModuleNotFoundError:                                 # pragma: no cover
        yaml = None
    broken = []
    for path in sorted(_SKILLS_DIR.glob("*/SKILL.md")):
        fm = _frontmatter(path)
        if yaml is not None:
            try:
                meta = yaml.safe_load(fm)
            except Exception as exc:                            # noqa: BLE001
                broken.append(f"{path.parent.name}: {str(exc).splitlines()[0]}")
                continue
            if not isinstance(meta, dict) or not meta.get("name"):
                broken.append(f"{path.parent.name}: no name key")
            continue
        # No PyYAML (CI without it): catch the one shape that actually breaks,
        # a top-level scalar value carrying an unquoted ": ". A skipped guard
        # is not a guard, so this runs either way.
        for num, line in enumerate(fm.splitlines(), 1):
            if line[:1].isspace() or ": " not in line:
                continue
            value = line.split(": ", 1)[1]
            if ": " in value and not value.startswith((">", "|", '"', "'")):
                broken.append(f"{path.parent.name}: bare ': ' in {line.split(':', 1)[0]}")
    assert not broken, "invalid skill frontmatter:\n  " + "\n  ".join(broken)


def test_no_dashes_inside_skill_output_templates():
    """A dash in a fenced block is a dash the model is told to emit.

    Prose about a skill is read and never printed, so it is out of scope here.
    A fenced block is a render template: it reaches the terminal, the vault
    file, the local page and the artifact verbatim. 25 of them shipped this
    way across the four briefings.
    """
    em, en = chr(0x2014), chr(0x2013)
    hits = []
    for path in sorted(_SKILLS_DIR.glob("*/SKILL.md")):
        in_fence = False
        for num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence and (em in line or en in line):
                hits.append(f"{path.parent.name}/SKILL.md:{num}: {line.strip()[:70]}")
    assert not hits, "dashes inside output templates:\n  " + "\n  ".join(hits)


def test_the_written_file_uses_the_file_render_not_the_terminal_one():
    """The terminal key pads columns with whitespace; markdown collapses it.

    A skill that names `front_page_md` where it describes the written file
    turns the whole front page into one run-on paragraph on three surfaces at
    once (the vault file, the local page served from it, and the artifact).
    That already shipped to a real published page. Each briefing that writes a
    front page must name the `_file_md` key in its file-shape section.
    """
    for name in ("morning-coffee", "afternoon-tea", "week-retro", "week"):
        text = (_SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        shape = text.split("Shape of the file", 1)
        assert len(shape) == 2, f"{name}: no file-shape section"
        # Whitespace-normalized: the warning legitimately wraps across a
        # newline in a hard-wrapped file, and a raw substring check missed it.
        block = re.sub(r"\s+", " ", shape[1][:900])
        assert "front_page_file_md" in block, f"{name}: file shape omits the _file_md key"
        assert "never `front_page_md`" in block, f"{name}: file shape does not warn off the terminal key"


def test_changelog_has_an_entry_for_the_current_version():
    """The changelog IS the release notes the user sees after an update
    (plugin_update.whats_new). A version bump without an entry ships an update
    that renders as nothing, so the two move in lockstep like the manifest and
    pyproject do."""
    changelog = (_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    version = _plugin_manifest()["version"]
    assert f"## {version} " in changelog or f"## {version}\n" in changelog, (
        f"CHANGELOG.md has no entry for {version}; write one in plain language "
        "(see the rules at the top of the file) in the same commit as the bump."
    )
