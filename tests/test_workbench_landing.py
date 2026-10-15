"""The Workbench opens on the Morning Coffee page.

The launcher's URL is the product's front door for the user and for every
client, so it is pinned here from the outside: `main()` is driven with the
server and the browser stubbed, and the URL the browser is handed is what is
asserted, not a helper's return value.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import workbench_serve  # noqa: E402


class _FakeServer:
    server_address = ("127.0.0.1", 8765)

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        pass


class _Now:
    """threading.Timer stand-in that fires on start, so the test sees the open."""

    def __init__(self, _delay, fn, args=()):
        self.fn, self.args = fn, args

    def start(self):
        self.fn(*self.args)


def _drive(monkeypatch, capsys, argv):
    opened = []
    monkeypatch.setattr(workbench_serve, "build_server", lambda port: _FakeServer())
    monkeypatch.setattr(workbench_serve, "write_session", lambda port, token: None)
    monkeypatch.setattr(workbench_serve, "clear_session", lambda: None)
    monkeypatch.setattr(workbench_serve, "session_path", lambda: Path("/dev/null"))
    monkeypatch.setattr(workbench_serve, "is_tier1", lambda: True)
    monkeypatch.setattr(workbench_serve, "resolved_meta", lambda: {})
    monkeypatch.setattr(workbench_serve.store, "propose", lambda: None)
    monkeypatch.setattr(workbench_serve.threading, "Timer", _Now)
    monkeypatch.setattr(workbench_serve.webbrowser, "open", lambda u: opened.append(u))
    assert workbench_serve.main(argv) == 0
    out = capsys.readouterr().out
    return json.loads(out[out.index("{"):]), opened


def test_the_launcher_opens_the_morning_coffee_page(monkeypatch, capsys):
    payload, opened = _drive(monkeypatch, capsys, ["--port", "8765"])
    assert opened == [payload["url"]], "the browser gets the same URL the skill reports"
    assert payload["url"].startswith("http://127.0.0.1:8765/briefing/morning-coffee?t=")
    token = payload["url"].split("?t=", 1)[1]
    assert len(token) >= 40, "the per-boot token rides on the URL"


def test_the_shell_is_not_what_opens(monkeypatch, capsys):
    payload, _ = _drive(monkeypatch, capsys, ["--port", "8765"])
    assert "/?t=" not in payload["url"], "the dashboard shell is reachable, never opened"


def test_no_browser_still_reports_the_briefing_url(monkeypatch, capsys):
    payload, opened = _drive(monkeypatch, capsys, ["--port", "8765", "--no-browser"])
    assert opened == []
    assert "/briefing/morning-coffee?t=" in payload["url"]


def test_the_landing_briefing_is_one_the_server_serves():
    assert workbench_serve.LANDING in workbench_serve.workbench_data.BRIEFINGS
