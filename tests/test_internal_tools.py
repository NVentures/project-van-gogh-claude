"""The dev-side tools in the internal repo's tools/ dir (punch list P4, P14, P18).

These tools live beside the plugin in the private internal repo, so this
file skips itself in the public mirror, where tools/ does not exist. Every
run here uses a temp internal-repo layout with invented credentials; the
one HTTP layer (preflight) is a stub. The one live preflight run against the
real shared app is a separate owner step, reported by hand.
"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

_INTERNAL = Path(__file__).resolve().parent.parent.parent
_TOOLS = _INTERNAL / "tools"
if not (_TOOLS / "keyfile.py").exists():
    pytest.skip("internal tools/ dir not present (public mirror)", allow_module_level=True)
sys.path.insert(0, str(_TOOLS))

import keyfile  # noqa: E402
import onboard  # noqa: E402
import precall  # noqa: E402
import preflight  # noqa: E402
import scan_publish_tree as scan  # noqa: E402

import companion_key as ck  # noqa: E402
import install_oauth_credentials as ioc  # noqa: E402
from oauth_scopes import MS_SCOPES  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "signin"
CLIENT = "jane-doe"
BUNDLE = {
    "kind": "van-gogh-credentials",
    "google_client_secret": {"installed": {
        "client_id": "123456-fake.apps.googleusercontent.com",
        "client_secret": "GOCSPX-fakeSecretValue1234",
        "project_id": "project-van-gogh-000000",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"]}},
    "ms_client_secret": {"client_id": "f783a7e4-1111-2222-3333-444444444444",
                         "tenant_id": "65d6685a-0000-0000-0000-000000000000"},
    "maps_api_key": "AIzaFakeMapsKeyValue000000",
    "google_consent_screen": {"status": "production", "attested": "2026-09-15",
                              "project": "project-van-gogh-000000"},
}


@pytest.fixture
def root(tmp_path):
    """A temp internal repo: credentials file, one client with a token record
    and details."""
    (tmp_path / "credentials").mkdir()
    (tmp_path / "credentials" / "shared-internal.json").write_text(
        json.dumps(BUNDLE), encoding="utf-8")
    cdir = tmp_path / "clients" / CLIENT
    cdir.mkdir(parents=True)
    (cdir / onboard.TOKEN_FILENAME).write_text(json.dumps({
        "repo": f"NVentures/project-van-gogh-custom-{CLIENT}", "token": "github_pat_fake",
        "created": "2026-10-01", "expires": None, "label": f"van-gogh {CLIENT}"}),
        encoding="utf-8")
    onboard.write_client_details(
        CLIENT, "Jane Doe", "Acme", "America/New_York", "granola",
        ["google:jane@acme.com", "microsoft:jane@corp.com"], "team@example.com",
        root=tmp_path)
    return tmp_path


# ── keyfile v2 is accepted by both client parsers ─────────────────────────────

def test_v2_key_from_the_tool_is_accepted_by_both_parsers(root, tmp_path, monkeypatch):
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path / "state"))
    path, summary, notes = keyfile.generate(CLIENT, root=root)
    assert path == root / "clients" / CLIENT / f"van-gogh-key-{CLIENT}.json"
    assert notes == []
    key = ck.parse_key_file(path)
    assert key["version"] == 2 and key["repo"].endswith(CLIENT)
    assert ioc.validate_bundle(key["credentials"]) == []
    assert ioc.detect_provider(key["credentials"])[0] == "bundle"
    assert [a["email_hint"] for a in key["accounts"]] == ["jane@acme.com", "jane@corp.com"]
    assert key["client"] == {"full_name": "Jane Doe", "first_name": "Jane", "company": "Acme",
                             "timezone": "America/New_York", "notetaker": "granola"}
    assert key["support_email"] == "team@example.com"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "google_consent_screen" not in raw["credentials"]
    assert summary == {"version": 2, "companion": True, "google_credential": True,
                       "microsoft_credential": True, "maps_key": True, "accounts": 2,
                       "client_fields": 5, "support_email": True}
    # And the whole thing installs on a client machine.
    monkeypatch.setattr(ck, "_run_claude", lambda args, token: {"ok": True})
    monkeypatch.setattr(ck, "marketplaces_dir", lambda: tmp_path / "mp")
    monkeypatch.setattr(ck, "working_sign_in", lambda provider: False)
    result = ck.install_all(path)
    assert result["ok"] is True and result["mode"] == "v2"


def test_v1_flag_and_absent_shared_file_both_give_the_old_shape(root):
    path, summary, notes = keyfile.generate(CLIENT, root=root, v1=True)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw) == {"kind", "repo", "token", "issued"}
    assert ck.parse_key_file(path)["version"] == 1
    (root / "credentials" / "shared-internal.json").unlink()
    path, summary, notes = keyfile.generate(CLIENT, root=root)
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {"kind", "repo", "token", "issued"}
    assert any("version 1" in n for n in notes)


def test_missing_client_details_builds_an_empty_client_and_says_so(root):
    (root / "clients" / CLIENT / "client.json").unlink()
    path, summary, notes = keyfile.generate(CLIENT, root=root)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["client"] == {} and raw["accounts"] == [] and raw["support_email"] == ""
    assert any("client.json" in n for n in notes)
    assert summary["accounts"] == 0 and summary["client_fields"] == 0


def test_keyfile_cli_prints_paths_and_counts_never_values(root, monkeypatch, capsys):
    monkeypatch.setattr(keyfile, "_INTERNAL_ROOT", root)
    assert keyfile.main(["--client", CLIENT]) == 0
    out = capsys.readouterr().out
    for value in ("GOCSPX-fakeSecretValue1234", "github_pat_fake", "AIzaFakeMapsKeyValue000000",
                  "123456-fake.apps.googleusercontent.com"):
        assert value not in out
    payload = json.loads(out)
    assert payload["summary"]["accounts"] == 2
    assert payload["path"].endswith(f"van-gogh-key-{CLIENT}.json")


# ── onboard client details ───────────────────────────────────────────────────

def test_client_details_file_has_the_contract_fields(root):
    data = json.loads((root / "clients" / CLIENT / "client.json").read_text(encoding="utf-8"))
    assert set(data) == {"full_name", "first_name", "company", "timezone", "notetaker",
                         "accounts", "support_email"}
    assert data["accounts"] == [{"provider": "google", "email_hint": "jane@acme.com"},
                                {"provider": "microsoft", "email_hint": "jane@corp.com"}]


@pytest.mark.parametrize("spec", ["jane@acme.com", "slack:jane@acme.com", "google:nope"])
def test_bad_account_specs_are_refused(spec):
    with pytest.raises(SystemExit):
        onboard.parse_account(spec)


# ── pre-call checklist ───────────────────────────────────────────────────────

def test_checklist_approval_link_carries_exactly_the_scopes_the_flow_asks_for(root):
    target = precall.generate(CLIENT, root=root,
                              credentials_path=root / "credentials" / "shared-internal.json")
    text = target.read_text(encoding="utf-8")
    link = next(line.strip() for line in text.splitlines()
                if line.startswith("https://login.microsoftonline.com/organizations/v2.0/adminconsent"))
    assert precall.scopes_from_url(link) == set(MS_SCOPES)
    assert set(MS_SCOPES)  # non-empty by construction, asserted anyway
    assert BUNDLE["ms_client_secret"]["client_id"] in link
    assert BUNDLE["google_client_secret"]["installed"]["client_id"] in text
    assert "GOCSPX-fakeSecretValue1234" not in text
    assert precall.CHECK_LINE_MAC in text and precall.CHECK_LINE_WINDOWS in text
    assert "{{" not in text
    assert "jane@acme.com" in text and "Granola" in text and "team@example.com" in text
    assert "short reminder" in text and "Allow" in text
    assert chr(0x2014) not in text and chr(0x2013) not in text


def _checklist(root):
    target = precall.generate(CLIENT, root=root,
                              credentials_path=root / "credentials" / "shared-internal.json")
    return target.read_text(encoding="utf-8")


def _flat(text):
    """Text with line wrapping removed, so a re-wrapped template cannot
    break a wording assertion."""
    return " ".join(text.split())


def _readme_lines():
    return (_INTERNAL / "project-van-gogh" / "README.md").read_text(encoding="utf-8").splitlines()


def test_checklist_carries_the_setup_line_and_it_is_the_readmes(root):
    """The install text tells a stalled client to paste the setup line from
    the checklist again, so the checklist has to hold it, and hold the same
    line the README does."""
    readme = _readme_lines()
    mac = [line for line in readme if line.startswith("claude plugin marketplace add")
           and line.endswith("setup.sh")]
    windows = [line for line in readme if line.startswith("claude plugin marketplace add")
               and "setup.ps1" in line]
    assert len(mac) == 1 and len(windows) == 1
    assert precall.SETUP_LINE_MAC == mac[0]
    assert precall.SETUP_LINE_WINDOWS == windows[0]
    text = _checklist(root)
    lines = text.splitlines()
    assert mac[0] in lines and windows[0] in lines  # each on a line of its own
    heading = next(i for i, line in enumerate(lines) if line.endswith("The setup line"))
    assert lines[heading].startswith("### ")
    section = "\n".join(lines[heading:lines.index(windows[0]) + 1])
    flat = _flat(section)
    assert ("Paste it at the plain Terminal prompt (PowerShell on Windows), before you "
            "start Claude") in flat
    assert "If you see Claude's chat box, close it first" in flat
    assert "again later is safe: it resumes where setup stopped" in flat


def test_checklist_readiness_lines_are_the_readmes_too():
    readme = _readme_lines()
    assert precall.CHECK_LINE_MAC in readme and precall.CHECK_LINE_WINDOWS in readme


def test_checklist_splits_preparation_from_the_install(root):
    """A careful reader told to work through the page in order must not
    install alone: everything numbered before "On the call" is preparation
    and ends with the readiness check; the setup line sits after it."""
    text = _checklist(root)
    before = text.index("## Before the call")
    on_call = text.index("## On the call")
    assert before < text.index(precall.CHECK_LINE_MAC) < on_call < text.index(precall.SETUP_LINE_MAC)
    prep = text[before:on_call]
    assert "setup.sh" not in prep and "setup.ps1" not in prep
    assert "stop here until the" in _flat(prep)
    assert "We do this part together, during the call, not before." in text[on_call:]
    import re
    numbers = [int(n) for n in re.findall(r"^### (\d+)\. ", text, re.M)]
    assert numbers == list(range(1, len(numbers) + 1)) and len(numbers) == 9
    assert "{{" not in text and "<!--" not in text


def test_checklist_explains_how_to_open_a_command_window_at_first_use(root):
    text = _checklist(root)
    first = text.index("Open Terminal (PowerShell on Windows)")
    assert first < text.index("### 2. ")
    explained = _flat(text[first:text.index("### 2. ")])
    assert "press Command" in explained and "type Terminal, press Return" in explained
    assert "press the Windows key, type PowerShell, press Enter" in explained
    assert text.count("press Command") == 1  # explained once, referred back to later
    assert text.count("the way item 1 describes") == 2


def test_checklist_names_the_clients_real_key_file(root):
    text = _checklist(root)
    assert f"`van-gogh-key-{CLIENT}.json`" in text
    assert "<your-name>" not in text
    with pytest.raises(SystemExit, match="key file name"):
        precall.render({"full_name": "Jane Doe"}, BUNDLE)


@pytest.mark.parametrize("provider", ["granola", "grain"])
def test_notetaker_step_is_the_provider_modules_own_key_help(root, provider):
    """One source for where the key lives: the provider module the installer
    itself quotes. The checklist carries that sentence whole."""
    import importlib
    module = importlib.import_module(f"notetaker_{provider}")
    assert precall.notetaker_key_help(provider) == module.KEY_HELP
    path = root / "clients" / CLIENT / "client.json"
    details = json.loads(path.read_text(encoding="utf-8"))
    details["notetaker"] = provider
    path.write_text(json.dumps(details), encoding="utf-8")
    text = _checklist(root)
    assert module.KEY_HELP in text
    assert f"your {module.DISPLAY_NAME} API key" in text


def test_no_notetaker_says_there_is_nothing_to_do(root):
    path = root / "clients" / CLIENT / "client.json"
    details = json.loads(path.read_text(encoding="utf-8"))
    details["notetaker"] = "none"
    path.write_text(json.dumps(details), encoding="utf-8")
    assert "Nothing to do here" in _checklist(root)


def test_permission_wording_is_true_to_what_the_product_does(root):
    text = _checklist(root)
    assert "only when you say so" not in text and "holds" not in text
    assert ("Mail.Send: send mail from your account only to you (your briefings, and one "
            "setup reminder if the install stalls); it sends to anyone else only when "
            "you tell it to") in text
    assert "Calendars.ReadWrite: read your calendar (the permission also allows changes, " \
           "but Van Gogh only reads it)" in text
    listed = [line[2:].split(":", 1)[0] for line in text.splitlines()
              if line.startswith("- ") and line[2:].split(":", 1)[0] in precall.SCOPE_WORDS]
    assert listed == [s.rsplit("/", 1)[-1] for s in MS_SCOPES]
    assert set(precall.SCOPE_WORDS) == {s.rsplit("/", 1)[-1] for s in MS_SCOPES}


def test_nothing_in_the_product_writes_to_a_calendar():
    """The checklist tells an IT admin Van Gogh only reads the calendar. That
    sentence is a claim about the code, so the code is read here."""
    import re
    app = _INTERNAL / "project-van-gogh" / "app"
    writers = []
    scanned = 0
    for path in sorted(app.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        scanned += 1
        if re.search(r"events\(\)\s*\.\s*(insert|patch|update|delete|quickAdd|move)\(", source):
            writers.append(path.name)
        if re.search(r"\.(post|patch|delete)\(\s*f?[\"'][^\"']*(/events|/calendar)", source):
            writers.append(path.name)
    assert scanned > 50 and writers == []


def test_plain_words_for_the_plan_and_the_privacy_sentence(root):
    text = _checklist(root)
    assert "model run" not in text and "Nothing else leaves your machine" not in text
    assert ("because Van Gogh uses Claude every morning to write your briefing, which a "
            "free plan does not allow") in _flat(text)
    assert "Nothing about your mail or meetings is sent to the Van Gogh team." in text


def _with_accounts(root, accounts):
    path = root / "clients" / CLIENT / "client.json"
    details = json.loads(path.read_text(encoding="utf-8"))
    details["accounts"] = accounts
    path.write_text(json.dumps(details), encoding="utf-8")
    return _checklist(root)


MS_HEADING = "Microsoft: ask your IT admin to approve Van Gogh"
GOOGLE_HEADING = "Google: ask your Workspace admin whether Van Gogh needs approving"


def test_a_google_only_client_sees_no_microsoft_admin_section(root):
    text = _with_accounts(root, [{"provider": "google", "email_hint": "jane@acme.com"}])
    assert GOOGLE_HEADING in text and MS_HEADING not in text
    assert "adminconsent" not in text and "Entra" not in text
    assert "### 5. Google: ask" in text and "### 6. Run the readiness check" in text


def test_a_microsoft_only_client_sees_no_google_admin_section(root):
    text = _with_accounts(root, [{"provider": "microsoft", "email_hint": "jane@corp.com"}])
    assert MS_HEADING in text and GOOGLE_HEADING not in text
    assert "admin.google.com" not in text
    assert "### 5. Microsoft" in text and "### 6. Run the readiness check" in text


def test_one_address_under_both_providers_shows_both_sections(root):
    text = _with_accounts(root, [{"provider": "google", "email_hint": "jane@acme.com"},
                                 {"provider": "microsoft", "email_hint": "jane@acme.com"}])
    assert MS_HEADING in text and GOOGLE_HEADING in text
    assert "### 7. Run the readiness check" in text


def test_personal_accounts_need_no_admin_section_and_unknown_accounts_show_both(root):
    text = _with_accounts(root, [{"provider": "google", "email_hint": "jane@gmail.com"},
                                 {"provider": "microsoft", "email_hint": "jane@outlook.com"}])
    assert MS_HEADING not in text and GOOGLE_HEADING not in text
    assert "### 5. Run the readiness check" in text
    text = _with_accounts(root, [])
    assert MS_HEADING in text and GOOGLE_HEADING in text


def test_each_thing_is_said_once(root):
    """Two repeats a cold reader tripped on: 'the install itself' twice in the
    intro, and 'not before' said twice in the second part."""
    flat = _flat(_checklist(root))
    assert flat.lower().count("the install itself") == 1
    assert flat.count("not before") == 1 and "Do not run it beforehand" not in flat
    assert "one pasted line, five questions, your first briefings on screen" in flat


def test_claude_and_terminal_are_named_once_and_kept_fixed(root):
    text = _checklist(root)
    flat = _flat(text)
    assert '"Claude" is the app you chat with' in flat
    assert '"Terminal" (PowerShell on Windows) is the plain window with a prompt' in flat
    assert "you start it by typing `claude` there" in flat
    assert "Claude Code" not in text and "Install it from" not in text
    assert "command window" not in text and "never into Claude" not in text
    assert flat.index('"Claude" is the app you chat with') < flat.index("### 2. ")


def test_item_one_installs_the_claude_command_with_the_official_installer(root):
    """claude.com/download is the desktop app and puts no `claude` command on
    PATH, so a client who followed it could run neither the readiness check
    nor the setup line. Item 1 carries Anthropic's native installer, copied
    from the official setup page named in tools/precall.py."""
    text = _checklist(root)
    flat = _flat(text)
    lines = text.splitlines()
    assert precall.CLAUDE_INSTALL_MAC == "curl -fsSL https://claude.ai/install.sh | bash"
    assert precall.CLAUDE_INSTALL_WINDOWS == "irm https://claude.ai/install.ps1 | iex"
    assert precall.CLAUDE_INSTALL_MAC in lines and precall.CLAUDE_INSTALL_WINDOWS in lines
    assert "claude.com/download" not in text and "claude.com/code" not in text
    item_one = flat[flat.index("### 1. "):flat.index("### 2. ")]
    assert f"{precall.INSTALL_PHRASE}. It needs no administrator password" in item_one
    assert ("Type `claude`, press Enter, and follow the sign-in it offers") in item_one
    assert ("Claude needs a paid plan, because Van Gogh uses Claude every morning to write "
            "your briefing, which a free plan does not allow") in item_one
    assert "open a new one the same way" in item_one  # a new window picks up the command
    assert "The Claude desktop app is not needed" in item_one
    # the install comes before the first time the page has them rely on `claude`
    assert item_one.index(precall.CLAUDE_INSTALL_MAC) < item_one.index("follow the sign-in")
    assert text.index(precall.CLAUDE_INSTALL_MAC) < text.index(precall.CHECK_LINE_MAC)
    source = (_TOOLS / "precall.py").read_text(encoding="utf-8")
    assert "#   https://code.claude.com/docs/en/setup" in source


def test_item_one_covers_a_claude_that_is_installed_but_not_found(root):
    """The official installer does not always put `claude` on the PATH. The
    fix lines are Anthropic's documented ones (code.claude.com/docs/en/
    troubleshoot-install, "Verify your PATH"), pinned here character for
    character and shared with both readiness scripts."""
    assert precall.PATH_FIX_MAC == """echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc"""
    assert precall.PATH_FIX_WINDOWS == (
        "$currentPath = [Environment]::GetEnvironmentVariable('PATH', 'User')",
        "[Environment]::SetEnvironmentVariable('PATH', "
        '"$currentPath;$env:USERPROFILE' "\\.local\\bin" '", ' "'User')")
    text = _checklist(root)
    lines = text.splitlines()
    assert precall.PATH_FIX_MAC in lines
    assert all(line in lines for line in precall.PATH_FIX_WINDOWS)
    flat = _flat(text)
    item_one = flat[flat.index("### 1. "):flat.index("### 2. ")]
    assert ("If Terminal says `command not found` (PowerShell says `claude` is not recognized), "
            "Claude is installed but that window cannot find it yet.") in item_one
    assert 'prints the fix under "Setup notes"' in item_one
    assert "then open a new window and type `claude` again" in item_one
    # after the install line, before the page calls item 1 done
    assert (item_one.index(precall.CLAUDE_INSTALL_MAC) < item_one.index(precall.PATH_FIX_MAC)
            < item_one.index("You are done when"))
    plugin = _INTERNAL / "project-van-gogh"
    sh = (plugin / "check.sh").read_text(encoding="utf-8").replace('\\"', '"').replace("\\$", "$")
    assert precall.PATH_FIX_MAC.rsplit(" ", 1)[0] + " $rc" in sh and '"~/.zshrc"' in sh
    ps1 = (plugin / "check.ps1").read_text(encoding="utf-8").replace("''", "'")
    assert "; ".join(precall.PATH_FIX_WINDOWS) in ps1
    for script in (sh, ps1):
        assert "Claude is installed but this window cannot find it yet." in script


def test_item_one_says_where_a_paid_plan_is_bought(root):
    flat = _flat(_checklist(root))
    assert precall.CLAUDE_PRICING_URL == "https://claude.com/pricing"
    assert "Max is what we recommend. Plans are listed at https://claude.com/pricing." in flat


def test_readiness_item_says_what_the_check_may_ask_for(root):
    """Taken from the scripts' own messages, so the page and the check agree.
    The Mac duration in the script is left out: the page states no times."""
    text = _checklist(root)
    flat = _flat(text)
    plugin = _INTERNAL / "project-van-gogh"
    sh = (plugin / "check.sh").read_text(encoding="utf-8")
    ps1 = (plugin / "check.ps1").read_text(encoding="utf-8")
    for phrase, script in (
            ("type xcode-select --install, press Return and click Install", sh),
            ("git-scm.com/download/win (keep every default), then open a new PowerShell window",
             ps1),
            ("search for 'App execution aliases' and turn off both python entries, then open "
             "a new PowerShell window", ps1)):
        assert phrase in script, phrase
        assert phrase in flat.replace("`", ""), phrase
    assert "If the last line is not READY" in flat
    assert "If the last line is still not READY, send the output to" in flat
    assert "anything other than READY" not in flat
    for marker in ("printf 'ok: ", "printf 'MISSING: ", "printf 'READY", "printf 'NOT READY"):
        assert marker in sh, marker  # the page describes these four line shapes
    assert "starting `ok:`" in flat and "starting `MISSING:`" in flat
    assert "the last line is READY or NOT READY" in flat
    prep = text[text.index("## Before the call"):text.index("## On the call")]
    assert "may ask whether Terminal may access files in your Downloads folder" in _flat(prep)
    assert "Click Allow" in prep
    assert "minutes" not in text


def test_on_the_call_says_how_the_installers_questions_are_answered(root):
    """Confirmed against code.claude.com/docs/en/tools-reference (answer by
    picking an option or typing through the Other row) and the terminal guide
    (no clicking, arrow keys). Nothing here names a key the docs do not."""
    text = _checklist(root)
    on_call = _flat(text[text.index("## On the call"):])
    assert ("You cannot click in that window: move through a list with the arrow keys and "
            "press Enter to choose. To give an answer that is not listed, choose the row "
            "named Other and type it.") in on_call
    assert "shows its own key hints" in on_call


def test_granola_key_help_carries_the_plan_and_the_full_click_path(root):
    import notetaker_granola
    help_text = notetaker_granola.KEY_HELP
    assert help_text == ("Get one from the Granola desktop app (needs a Business or Enterprise "
                         "plan): Settings → Connectors → API keys → Create new key → "
                         "choose the note access scopes → Generate API Key.")
    assert len(help_text) < 200  # still reads as an option description
    assert help_text in _checklist(root)


def test_checklist_and_check_scripts_name_the_same_install_source():
    """One install source, in the same words, on the page and in both
    readiness scripts."""
    plugin = _INTERNAL / "project-van-gogh"
    sh = (plugin / "check.sh").read_text(encoding="utf-8")
    ps1 = (plugin / "check.ps1").read_text(encoding="utf-8")
    template = (_INTERNAL / "templates" / "pre-call-checklist.md").read_text(encoding="utf-8")
    assert "{{INSTALL_PHRASE}}" in template
    for name, script, line in (("check.sh", sh, precall.CLAUDE_INSTALL_MAC),
                               ("check.ps1", ps1, precall.CLAUDE_INSTALL_WINDOWS)):
        assert f"{precall.INSTALL_PHRASE}: " in script, name
        assert line in script, name
        assert "claude.com/code" not in script and "claude.com/download" not in script, name
        assert "Claude Code" not in script, name


def test_admin_steps_use_the_vendors_current_words(root):
    """Checked against Google's Admin help (support.google.com/a/answer/7281227)
    and Microsoft's admin consent endpoint page (learn.microsoft.com, v2-admin-
    consent and grant-admin-consent) on 2026-10-01."""
    import urllib.parse
    text = _checklist(root)
    flat = _flat(text)
    assert ("Security, Access and data control, API controls, clicks Manage App Access, "
            "then Configure new app, searches for that client id, and sets its access to "
            "Trusted") in flat
    assert "Manage third-party app access" not in flat
    link = next(line.strip() for line in text.splitlines()
                if line.startswith("https://login.microsoftonline.com/"))
    parsed = urllib.parse.urlparse(link)
    assert parsed.path == "/organizations/v2.0/adminconsent"  # never /common
    assert set(urllib.parse.parse_qs(parsed.query)) == {"client_id", "scope", "redirect_uri",
                                                        "state"}
    # The Enterprise applications route only works once the app is already in
    # the tenant, which it is not before the first approval.
    assert "Enterprise applications" not in flat
    assert "says it cannot be reached. That is expected: the approval is already saved" in flat


def test_readiness_check_says_who_gets_the_output(root):
    flat = _flat(_checklist(root))
    assert "send the output to team@example.com and it gets sorted before the call" in flat
    assert "send us the output" not in flat
    path = root / "clients" / CLIENT / "client.json"
    details = json.loads(path.read_text(encoding="utf-8"))
    details["support_email"] = ""
    path.write_text(json.dumps(details), encoding="utf-8")
    flat = _flat(_checklist(root))
    assert ("send the output to the person who sent you this page and it gets sorted "
            "before the call") in flat


def test_key_file_section_says_where_to_look(root):
    flat = _flat(_checklist(root))
    assert (f"You will receive one file, named `van-gogh-key-{CLIENT}.json`. It arrives in a "
            "separate message from the person who sent you this page, never attached to "
            "this page. Save it to your Downloads folder") in flat
    assert "by a separate channel" not in flat
    onboard.write_client_details(
        CLIENT, "Jane Doe", "Acme", "America/New_York", "granola",
        ["google:jane@acme.com"], "team@example.com", root=root, key_delivery="by Signal.")
    details = json.loads((root / "clients" / CLIENT / "client.json").read_text(encoding="utf-8"))
    assert details["key_delivery"] == "by Signal."
    flat = _flat(_checklist(root))
    assert (f"named `van-gogh-key-{CLIENT}.json`. We send it to you by Signal. Save it") in flat
    assert "separate message" not in flat


def test_onboard_cli_writes_key_delivery_only_when_given(root, monkeypatch, capsys):
    monkeypatch.setattr(onboard, "_INTERNAL_ROOT", root)
    monkeypatch.setattr(onboard.write_client_details, "__defaults__",
                        ("", root, ""))
    base = ["--client", CLIENT, "--details", "--full-name", "Jane Doe",
            "--account", "google:jane@acme.com"]
    assert onboard.main(base) == 0
    path = root / "clients" / CLIENT / "client.json"
    assert "key_delivery" not in json.loads(path.read_text(encoding="utf-8"))
    assert onboard.main(base + ["--key-delivery", "in a 1Password share link"]) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["key_delivery"] == \
        "in a 1Password share link"
    capsys.readouterr()


def test_admin_sections_speak_as_a_page_that_knows_the_account(root):
    """Shown for a work account on record, the section must not ask 'if your
    account is a company account'; with no account on record it still has to."""
    flat = _flat(_checklist(root))  # jane@acme.com and jane@corp.com: both work accounts
    assert ("Your Google account is a company account. Some companies block new apps until "
            "an admin allows them. Before the call, send your Google Workspace admin the "
            "two lines below and ask whether Van Gogh needs approving") in flat
    assert "If your Google account above is" not in flat
    assert "Your Microsoft account is a work or school account." in flat
    assert "If your Microsoft account above is" not in flat
    text = _checklist(root)
    lines = text.splitlines()
    assert "Product: Van Gogh" in lines
    assert f"OAuth client id: {BUNDLE['google_client_secret']['installed']['client_id']}" in lines
    assert "(work accounts only)" not in text
    assert "minutes" not in text  # no duration claim anywhere on the page
    unknown = _flat(_with_accounts(root, []))
    assert "If you sign in with a company Google account (Google Workspace)" in unknown
    assert "If you sign in with a work or school Microsoft 365 account" in unknown
    assert "Your Google account is a company account" not in unknown


def _stall_section(text):
    return text[text.index("### If the install stalls"):]


def test_stall_paragraph_says_what_the_reminder_is_and_where_help_is(root):
    section = _stall_section(_checklist(root))
    assert "names the next step and where to type it" in _flat(section)
    assert "the one step that is left" not in section
    assert ("an email from your own account to your own address, or a notification on this "
            "computer when no account is signed in yet") in _flat(section)
    assert section.rstrip().endswith("For help, write to team@example.com.")
    assert "the address the reminder names" not in section
    assert "Reply to it" not in section and "reply to" not in section


def test_stall_paragraph_drops_the_help_sentence_when_no_address_is_on_record(root):
    path = root / "clients" / CLIENT / "client.json"
    details = json.loads(path.read_text(encoding="utf-8"))
    details["support_email"] = ""
    path.write_text(json.dumps(details), encoding="utf-8")
    section = _stall_section(_checklist(root))
    assert "names the next step" in section
    assert "write to" not in section and "For help" not in section
    assert "@" not in section and "your Van Gogh contact" not in section
    assert section.rstrip().endswith("is sent to the Van Gogh team.")
    assert precall.stall_help("   ") == ""


def test_checklist_without_details_says_what_to_run_first(root):
    (root / "clients" / CLIENT / "client.json").unlink()
    with pytest.raises(SystemExit, match="--details"):
        precall.generate(CLIENT, root=root,
                         credentials_path=root / "credentials" / "shared-internal.json")


# ── preflight ─────────────────────────────────────────────────────────────────

# The sign-in page a KNOWN multi-tenant app gets at /organizations carries
# "sErrorCode":"50058" in its embedded config (measured live 2026-10-01).
SIGN_IN_PAGE = {"status": 200, "location": "",
                "body": '<html><title>Sign in to your account</title><script>$Config={'
                        '"arrValErrs":["50058"],"sErrorCode":"50058","sErrTxt":""};</script></html>'}
LIVE_SIGN_IN = {"status": 200, "location": "",
                "body": "<html><title>Sign in to your Microsoft account</title></html>"}
ZERO_ID = "00000000-0000-0000-0000-000000000000"


def _missing_app_responses():
    """How /organizations answers for an app Microsoft does not know, one
    response per place the fault can sit."""
    unknown = json.loads((FIXTURES / "ms_organizations_unknown_app.json").read_text(encoding="utf-8"))
    unknown.pop("_source")
    return {
        # captured live: a well-formed id nobody registered, the fault only in $Config
        "config_code_50059": unknown,
        # captured live: a malformed id names itself in the service exception message
        "exception_message_700038": {"status": 200, "location": "", "body":
            '<html><title>Sign in to your account</title><script>$Config={"strServiceExceptionMessage":'
            f'"AADSTS700038: {ZERO_ID} is not a valid application identifier.","iErrorCode":700038}}'
            "</script></html>"},
        "redirect_700016": {"status": 302, "body": "", "location":
            "http://localhost/?error=unauthorized_client&error_description=AADSTS700016%3a+Application+"
            "with+identifier+%27x%27+was+not+found+in+the+directory+%27Contoso%27."},
        "numeric_config_code_700016": {"status": 200, "location": "", "body":
            '<html><title>Sign in to your account</title><script>$Config={"iErrorCode":700016,'
            '"sErrTxt":""};</script></html>'},
        "phrase_only": {"status": 200, "location": "", "body":
            "<html><title>Sign in to your account</title><p>The value is not a valid application "
            "identifier</p></html>"},
    }


def _stub(real_id, missing="exception_message_700038"):
    """A fetch stub shaped like the live responses captured 2026-10-01. Any id
    other than `real_id` (the made-up control included) is a missing app."""
    bogus_consumers = json.loads((FIXTURES / "ms_consumers_bogus_app.json").read_text(encoding="utf-8"))
    missing_response = _missing_app_responses()[missing]

    def fetch(url):
        if "login.live.com" in url:
            return LIVE_SIGN_IN if real_id in url else dict(bogus_consumers)
        if "/consumers/" in url:
            return {"status": 302, "body": "<title>Object moved</title>",
                    "location": "https://login.live.com/oauth20_authorize.srf?" + url.split("?", 1)[1]}
        if real_id in url:
            return SIGN_IN_PAGE
        return dict(missing_response)
    return fetch


def test_preflight_passes_the_real_app_and_fails_a_bogus_id():
    real = BUNDLE["ms_client_secret"]["client_id"]
    fetch = _stub(real)
    good = preflight.run(BUNDLE, fetch_fn=fetch, today=date(2026, 10, 1))
    assert good["ok"] is True
    assert [c["name"] for c in good["checks"]] == [
        "microsoft_consumers", "microsoft_organizations", "google_consent_screen"]
    assert all(c["control_rejected"] is True for c in good["checks"][:2])
    bogus = dict(BUNDLE, ms_client_secret={"client_id": ZERO_ID})
    bad = preflight.run(bogus, fetch_fn=fetch, today=date(2026, 10, 1))
    assert bad["ok"] is False
    by_name = {c["name"]: c for c in bad["checks"]}
    assert by_name["microsoft_consumers"]["ok"] is False
    assert "unauthorized_client: The client does not exist" in by_name["microsoft_consumers"]["detail"]
    assert by_name["microsoft_organizations"]["ok"] is False
    assert "AADSTS700038" in by_name["microsoft_organizations"]["detail"]
    assert by_name["google_consent_screen"]["ok"] is True


@pytest.mark.parametrize("missing, names", [
    ("config_code_50059", "AADSTS50059"),
    ("exception_message_700038", "AADSTS700038"),
    ("redirect_700016", "AADSTS700016"),
    ("numeric_config_code_700016", "AADSTS700016"),
    ("phrase_only", "is not a valid application identifier"),
])
def test_organizations_check_alone_fails_a_missing_app_wherever_the_fault_sits(missing, names):
    """The blind spot: with a made-up id the organizations check used to read
    'accepts work or school accounts', and preflight failed only because of
    the consumers hop. This grades the organizations check ON ITS OWN."""
    real = BUNDLE["ms_client_secret"]["client_id"]
    bogus = dict(BUNDLE, ms_client_secret={"client_id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"})
    checks = preflight.check_microsoft(bogus, fetch_fn=_stub(real, missing))
    org = next(c for c in checks if c["name"] == "microsoft_organizations")
    assert org["ok"] is False and org["fault"] == "not_found"
    assert "app not found" in org["detail"] and names in org["detail"]
    assert "does not accept work or school accounts" in org["detail"]
    assert "accepts work or school accounts:" not in org["detail"].replace("does not accept", "")
    assert "client_id in credentials/shared-internal.json" in org["detail"]
    # and the real app still passes through the same stub
    good = next(c for c in preflight.check_microsoft(BUNDLE, fetch_fn=_stub(real, missing))
                if c["name"] == "microsoft_organizations")
    assert good["ok"] is True and good["control_rejected"] is True


def test_a_probe_that_accepts_a_made_up_id_reports_itself_blind():
    """If Microsoft's pages change so the control id reads as accepted, the
    verdict on the real app proves nothing, and the check says so."""
    clean = {"status": 200, "location": "",
             "body": "<html><title>Sign in to your account</title><script>$Config={}</script></html>"}
    checks = preflight.check_microsoft(BUNDLE, fetch_fn=lambda url: dict(clean))
    assert [c["ok"] for c in checks] == [False, False]
    assert all(c["fault"] == "blind" and c["control_rejected"] is False for c in checks)
    assert all("control" in c["detail"] for c in checks)


def test_an_unrecognised_page_error_code_is_not_waved_through():
    page = {"status": 200, "location": "", "body":
            '<html><title>Sign in to your account</title><script>$Config={"sErrorCode":"90099"};'
            "</script></html>"}
    accepted, detail, fault = preflight.classify(page)
    assert accepted is False and fault == "unexpected" and "AADSTS90099" in detail
    assert preflight.classify(SIGN_IN_PAGE) == (True, "sign-in page served", "")


def test_preflight_names_an_org_only_app_and_a_network_failure():
    fetch = lambda url: ({"status": 0, "body": "", "location": "", "error": "URLError: down"}
                         if "/organizations/" in url else
                         {"status": 200, "location": "", "body":
                          "AADSTS50194: Application is not configured as a multi-tenant application."})
    result = preflight.run(BUNDLE, fetch_fn=fetch, today=date(2026, 10, 1))
    by_name = {c["name"]: c for c in result["checks"]}
    assert "AADSTS50194" in by_name["microsoft_consumers"]["detail"]
    assert "no answer" in by_name["microsoft_organizations"]["detail"]
    assert result["ok"] is False


@pytest.mark.parametrize("block, ok, phrase", [
    ({"status": "production", "attested": "2026-09-15"}, True, "16 days ago"),
    ({"status": "testing", "attested": "2026-09-15"}, False, "not 'production'"),
    ({"status": "unattested", "attested": ""}, False, "not 'production'"),
    ({"status": "production", "attested": "2026-03-01"}, False, "over 180"),
    ({"status": "production", "attested": "2026-11-01"}, False, "future"),
    ({"status": "production", "attested": "yesterday"}, False, "YYYY-MM-DD"),
    ({}, False, "missing"),
])
def test_google_attestation_rules(block, ok, phrase):
    check = preflight.check_google_attestation({"google_consent_screen": block},
                                               today=date(2026, 10, 1))
    assert check["ok"] is ok and phrase in check["detail"]


def test_preflight_cli_exits_nonzero_on_a_fault(tmp_path, monkeypatch, capsys):
    path = tmp_path / "c.json"
    path.write_text(json.dumps(dict(BUNDLE, google_consent_screen={"status": "testing"})),
                    encoding="utf-8")
    monkeypatch.setattr(preflight, "run",
                        lambda bundle, fetch_fn=None, today=None:
                        {"ok": False, "checks": [preflight.check_google_attestation(bundle)]})
    assert preflight.main(["--credentials", str(path)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "GOCSPX" not in json.dumps(out)
    assert preflight.main(["--credentials", str(tmp_path / "missing.json")]) == 1


# ── the publish-tree scanner ─────────────────────────────────────────────────

def test_scanner_hunts_the_secret_values_and_not_the_public_constants():
    values = scan.secret_values(BUNDLE)
    assert set(values) == {"google_client_secret.installed.client_id",
                           "google_client_secret.installed.client_secret",
                           "ms_client_secret.client_id", "ms_client_secret.tenant_id",
                           "maps_api_key"}


def test_scanner_finds_a_planted_value_fails_the_tree_and_names_the_field(tmp_path):
    values = scan.secret_values(BUNDLE)
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "clean.py").write_text("print('hello')\n", encoding="utf-8")
    (tree / "sub" / "leak.md").write_text(
        f"key = {BUNDLE['maps_api_key']}\n", encoding="utf-8")
    result = scan.run(values, tree=tree)
    assert result["status"] == "FAIL" and result["planted_found"] is True
    assert result["files_scanned"] == 2
    assert result["hits"] == [{"file": "sub/leak.md", "field": "maps_api_key"}]
    assert BUNDLE["maps_api_key"] not in json.dumps(result)
    (tree / "sub" / "leak.md").unlink()
    clean = scan.run(values, tree=tree)
    assert clean["status"] == "PASS" and clean["ok"] is True and clean["files_scanned"] == 1


def test_scanner_refuses_to_pass_an_empty_tree(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = scan.run(scan.secret_values(BUNDLE), tree=empty)
    assert result["status"] == "SKIP" and result["ok"] is False


def test_scanner_refuses_when_its_own_plant_is_missed(monkeypatch, tmp_path):
    monkeypatch.setattr(scan, "scan_tree", lambda tree, values: {"files_scanned": 2, "hits": []})
    result = scan.run(scan.secret_values(BUNDLE), tree=tmp_path)
    assert result["status"] == "FAIL" and result["planted_found"] is False


def test_the_real_mirror_tree_carries_no_shared_credential_value():
    """The P18 half: the tree tools/publish.py would push holds none of the
    values in the real credentials file. Skipped when that file is absent."""
    real = _INTERNAL / "credentials" / "shared-internal.json"
    if not real.exists():
        pytest.skip("no credentials/shared-internal.json on this machine")
    import shutil
    if shutil.which("git") is None:
        pytest.skip("git is needed to build the mirror")
    values = scan.secret_values(json.loads(real.read_text(encoding="utf-8")))
    result = scan.run(values, source=_INTERNAL / "project-van-gogh")
    assert result["status"] == "PASS", {k: v for k, v in result.items() if k != "hits"} | {
        "hits": result.get("hits")}
    assert result["files_scanned"] > 100
