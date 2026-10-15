"""User extensions (app/extensions.py): briefing sections and external
notetaker providers loaded from ~/.config/van-gogh/extensions/.

The whole surface is fail-open: a broken extension costs its own section (one
line in errors), never the briefing — the digest scheduler runs unattended.
"""

import copy
import shutil
import sys
import textwrap

import pytest

import config_loader as cl
import extensions
import notetaker
import user_state


@pytest.fixture()
def ext_dir():
    """A clean extensions dir inside the suite's temp state dir."""
    root = user_state.extensions_dir()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)
    # Extension modules are cached in sys.modules by alias; a test that
    # rewrites a same-named file must not inherit the previous import.
    for alias in [k for k in sys.modules if k.startswith("van_gogh_ext_")]:
        del sys.modules[alias]


@pytest.fixture(autouse=True)
def _restore_config():
    saved = copy.deepcopy(cl._config)
    yield
    cl._config = saved


def _write(root, name, body):
    (root / name).write_text(textwrap.dedent(body), encoding="utf-8")


# ── Briefing sections ─────────────────────────────────────────────────────────

def test_no_extensions_dir_is_quiet():
    root = user_state.extensions_dir()
    if root.exists():
        shutil.rmtree(root)
    errors = []
    assert extensions.briefing_sections("morning-coffee", errors) == []
    assert errors == []


def test_section_happy_path(ext_dir):
    _write(ext_dir, "briefing_section_alpha.py", """
        TITLE = "Alpha Watch"
        def collect(ctx):
            assert ctx["briefing"] == "morning-coffee"
            assert ctx["vault"]
            assert "accounts" in ctx["meta"]
            return "hello **world**"
    """)
    errors = []
    sections = extensions.briefing_sections("morning-coffee", errors)
    assert sections == [{"name": "alpha", "title": "Alpha Watch",
                         "md": "hello **world**"}]
    assert errors == []


def test_briefings_filter(ext_dir):
    _write(ext_dir, "briefing_section_weekly.py", """
        TITLE = "Weekly Only"
        BRIEFINGS = {"week"}
        def collect(ctx):
            return "weekly content"
    """)
    errors = []
    assert extensions.briefing_sections("morning-coffee", errors) == []
    assert [s["name"] for s in extensions.briefing_sections("week", errors)] == ["weekly"]
    assert errors == []


def test_broken_extension_degrades_not_crashes(ext_dir):
    _write(ext_dir, "briefing_section_bad.py", """
        def collect(ctx):
            raise RuntimeError("boom")
    """)
    _write(ext_dir, "briefing_section_broken_import.py", """
        import does_not_exist_anywhere
    """)
    _write(ext_dir, "briefing_section_good.py", """
        TITLE = "Still Here"
        def collect(ctx):
            return "fine"
    """)
    errors = []
    sections = extensions.briefing_sections("week", errors)
    assert [s["name"] for s in sections] == ["good"]
    assert len(errors) == 2
    assert any("'bad'" in e and "boom" in e for e in errors)
    assert any("'broken_import'" in e for e in errors)


def test_failed_import_is_retried_next_run(ext_dir):
    """A module that failed to import is evicted, so fixing the file works."""
    path = "briefing_section_flaky.py"
    _write(ext_dir, path, "import does_not_exist_anywhere\n")
    errors = []
    assert extensions.briefing_sections("week", errors) == []
    assert len(errors) == 1
    _write(ext_dir, path, "TITLE = 'Fixed'\n"
                          "def collect(ctx):\n    return 'ok'\n")
    errors = []
    assert [s["md"] for s in extensions.briefing_sections("week", errors)] == ["ok"]
    assert errors == []


def test_empty_and_none_sections_are_omitted(ext_dir):
    _write(ext_dir, "briefing_section_quiet.py", """
        def collect(ctx):
            return "   "
    """)
    _write(ext_dir, "briefing_section_none.py", """
        def collect(ctx):
            return None
    """)
    errors = []
    assert extensions.briefing_sections("week", errors) == []
    assert errors == []


def test_non_string_return_is_an_error(ext_dir):
    _write(ext_dir, "briefing_section_dicty.py", """
        def collect(ctx):
            return {"not": "markdown"}
    """)
    errors = []
    assert extensions.briefing_sections("week", errors) == []
    assert len(errors) == 1 and "'dicty'" in errors[0]


def test_runaway_section_is_clamped(ext_dir):
    _write(ext_dir, "briefing_section_huge.py", """
        def collect(ctx):
            return "x" * 50000
    """)
    errors = []
    [section] = extensions.briefing_sections("week", errors)
    assert len(section["md"]) < 50000
    assert section["md"].endswith("*(section truncated)*")


def test_missing_title_falls_back_to_the_name(ext_dir):
    _write(ext_dir, "briefing_section_deal_flow.py", """
        def collect(ctx):
            return "content"
    """)
    [section] = extensions.briefing_sections("week", [])
    assert section["title"] == "Deal Flow"


def test_attach_always_sets_the_key(ext_dir):
    _write(ext_dir, "briefing_section_ok.py", """
        TITLE = "Ok"
        def collect(ctx):
            return "body"
    """)
    output = {"errors": []}
    extensions.attach(output, "afternoon-tea")
    assert output["extra_sections"] == [{"name": "ok", "title": "Ok", "md": "body"}]
    assert output["errors"] == []


# ── External notetaker providers ──────────────────────────────────────────────

_FAKE_PROVIDER = """
    NAME = "fakeprov"
    DISPLAY_NAME = "Fake Provider"
    ENV_KEY = "FAKEPROV_API_KEY"
    KEY_HELP = "Get a key from the Fake dashboard."

    def api_key():
        return "test-key"

    def iter_stubs(since=None, limit=None):
        return iter([])

    def fetch_note(note_id, include_transcript=False):
        return {}

    def latest_id():
        return None
"""


def test_external_notetaker_joins_the_registry(ext_dir):
    _write(ext_dir, "notetaker_fakeprov.py", _FAKE_PROVIDER)
    assert "fakeprov" in notetaker.provider_names()
    mod = notetaker.provider("fakeprov")
    assert mod.DISPLAY_NAME == "Fake Provider"
    assert mod.api_key() == "test-key"


def test_external_provider_can_be_configured_without_a_problem(ext_dir):
    _write(ext_dir, "notetaker_fakeprov.py", _FAKE_PROVIDER)
    cl._config.setdefault("notetaker", {})["provider"] = "fakeprov"
    assert notetaker.config_problem() == ""
    assert notetaker.active_name() == "fakeprov"


def test_typo_is_still_a_config_problem(ext_dir):
    cl._config.setdefault("notetaker", {})["provider"] = "granolla"
    assert "granolla" in notetaker.config_problem()
    assert notetaker.active_name() == notetaker.DEFAULT_PROVIDER


def test_keyed_external_provider_wins_inference(ext_dir, monkeypatch):
    """No config, only the extension has a key → it becomes active."""
    # A dev machine may have real notetaker keys in its environment; inference
    # needs exactly one keyed provider, so clear them for this test.
    monkeypatch.delenv("GRANOLA_API_KEY", raising=False)
    monkeypatch.delenv("GRAIN_API_KEY", raising=False)
    _write(ext_dir, "notetaker_fakeprov.py", _FAKE_PROVIDER)
    cl._config.setdefault("notetaker", {})["provider"] = ""
    assert notetaker.active_name() == "fakeprov"


def test_builtin_name_wins_a_collision(ext_dir):
    _write(ext_dir, "notetaker_granola.py", _FAKE_PROVIDER)
    mod = notetaker.provider("granola")
    assert mod.DISPLAY_NAME != "Fake Provider"


def test_no_extensions_means_builtin_registry_only():
    root = user_state.extensions_dir()
    if root.exists():
        shutil.rmtree(root)
    assert notetaker.provider_names() == sorted(notetaker._REGISTRY)


def test_broken_configured_external_notetaker_is_loud_not_silent(ext_dir):
    """The multi-confirmed review finding: a configured extension provider
    whose file will not import must surface as a config problem, never as a
    briefing that reports zero meetings like a quiet week."""
    _write(ext_dir, "notetaker_busted.py", "import does_not_exist_anywhere\n")
    cl._config.setdefault("notetaker", {})["provider"] = "busted"
    assert notetaker.active_name() == "busted"
    problem = notetaker.config_problem()
    assert problem and "busted" in problem
    # And the diagnostic probe renders the reason instead of crashing.
    result = notetaker.check()
    assert result["ok"] is False


def test_sys_exit_in_extension_cannot_kill_the_run(ext_dir):
    _write(ext_dir, "briefing_section_quitter.py", """
        import sys
        def collect(ctx):
            sys.exit(0)
    """)
    _write(ext_dir, "notetaker_exiter.py", """
        import sys
        sys.exit(3)
    """)
    errors = []
    assert extensions.briefing_sections("week", errors) == []
    assert any("'quitter'" in e for e in errors)
    # Key inference imports extension providers; sys.exit must not escape.
    cl._config.setdefault("notetaker", {})["provider"] = ""
    notetaker.active_name()
    notetaker.available()


def test_extension_prints_never_reach_stdout(ext_dir, capsys):
    """Briefing stdout is a JSON channel; extension noise goes to stderr."""
    _write(ext_dir, "briefing_section_noisy.py", """
        print("IMPORT NOISE")
        def collect(ctx):
            print("COLLECT NOISE")
            return "content"
    """)
    [section] = extensions.briefing_sections("week", [])
    assert section["md"] == "content"
    captured = capsys.readouterr()
    assert "NOISE" not in captured.out
    assert "IMPORT NOISE" in captured.err and "COLLECT NOISE" in captured.err


def test_briefings_declared_as_string_still_works(ext_dir):
    _write(ext_dir, "briefing_section_stringy.py", """
        BRIEFINGS = "week"
        def collect(ctx):
            return "body"
    """)
    errors = []
    assert [s["md"] for s in extensions.briefing_sections("week", errors)] == ["body"]
    assert extensions.briefing_sections("morning-coffee", errors) == []
    assert errors == []


def test_unknown_briefing_name_in_briefings_is_reported(ext_dir):
    _write(ext_dir, "briefing_section_typoed.py", """
        BRIEFINGS = {"morning_coffee"}
        def collect(ctx):
            return "never seen"
    """)
    errors = []
    assert extensions.briefing_sections("morning-coffee", errors) == []
    assert any("morning_coffee" in e and "'typoed'" in e for e in errors)


def test_attach_survives_a_scan_level_failure(monkeypatch):
    monkeypatch.setattr(extensions, "briefing_sections",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("scan boom")))
    output = {}
    extensions.attach(output, "week")
    assert output["extra_sections"] == []
    assert any(e.startswith("Extensions:") for e in output["errors"])


def test_extension_cannot_poison_shared_ctx(ext_dir):
    _write(ext_dir, "briefing_section_amutator.py", """
        def collect(ctx):
            ctx["meta"].clear()
            return "mutated"
    """)
    _write(ext_dir, "briefing_section_breader.py", """
        def collect(ctx):
            return "accounts" if "accounts" in ctx["meta"] else ""
    """)
    sections = extensions.briefing_sections("week", [])
    assert [s["name"] for s in sections] == ["amutator", "breader"]


def test_uppercase_provider_filename_is_normalized(ext_dir):
    _write(ext_dir, "notetaker_Fancy.py", _FAKE_PROVIDER)
    assert "fancy" in notetaker.provider_names()
    assert notetaker.provider("fancy").DISPLAY_NAME == "Fake Provider"


def test_available_survives_a_provider_missing_attributes(ext_dir):
    _write(ext_dir, "notetaker_bare.py", "def api_key():\n    return None\n")
    rows = notetaker.available()
    assert all(r["name"] != "bare" for r in rows)
    assert {"granola", "grain"} <= {r["name"] for r in rows}
