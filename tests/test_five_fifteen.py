"""Unit tests for five_fifteen.py pure helpers (no network).

conftest puts app/ on sys.path and primes config_loader, so importing the
script is safe with no config.json present.
"""
import json
from datetime import date

import five_fifteen as ff


def test_mdy_no_leading_zeros():
    assert ff.mdy(date(2026, 5, 9)) == "5/9/2026"
    assert ff.mdy(date(2026, 11, 29)) == "11/29/2026"


def test_most_recent_friday():
    # Friday returns itself.
    assert ff.most_recent_friday(date(2026, 5, 29)) == date(2026, 5, 29)
    # Saturday rolls back to the day before.
    assert ff.most_recent_friday(date(2026, 5, 30)) == date(2026, 5, 29)
    # Sunday rolls back to the prior Friday.
    assert ff.most_recent_friday(date(2026, 5, 31)) == date(2026, 5, 29)
    # Mid-week (Wed) rolls back to the prior Friday.
    assert ff.most_recent_friday(date(2026, 5, 27)) == date(2026, 5, 22)


def test_resolve_week_explicit_ending():
    w = ff.resolve_week("2026-05-29")
    assert w["ending"] == "2026-05-29"
    assert w["start"] == "2026-05-23"  # 7-day window, Sat..Fri
    assert w["label"] == "2026-W22"
    assert w["ending_display"] == "5/29/2026"
    assert w["range_display"] == "5/23/2026 to 5/29/2026"


def test_find_prior_report_picks_latest(tmp_path):
    (tmp_path / "2026-05-15.md").write_text("older", encoding="utf-8")
    (tmp_path / "2026-05-22.md").write_text("PRIOR WEEK BODY", encoding="utf-8")
    # The current week's file may already exist — it must be excluded.
    (tmp_path / "2026-05-29.md").write_text("current draft", encoding="utf-8")
    path, text = ff.find_prior_report(tmp_path, "2026-05-29.md")
    assert path.endswith("2026-05-22.md")
    assert text == "PRIOR WEEK BODY"


def test_find_prior_report_empty(tmp_path):
    assert ff.find_prior_report(tmp_path, "2026-05-29.md") == (None, "")
    assert ff.find_prior_report(tmp_path / "missing", "x.md") == (None, "")


# ── fetch_client_meetings keyword filter ──────────────────────────────────────

def _fake_meetings():
    return [
        {"title": "Acme sync", "summary_text": "shipped the widget", "attendees": ["a@acme.com"], "date": "2026-05-27"},
        {"title": "Other call", "summary_text": "", "attendees": [], "date": "2026-05-28"},
    ]


def test_fetch_client_meetings_keyword_filter(monkeypatch):
    import notetaker
    monkeypatch.setattr(notetaker, "configured", lambda: True)
    monkeypatch.setattr(notetaker, "fetch_meetings", lambda *a, **k: [dict(m) for m in _fake_meetings()])
    matched, all_titles, err = ff.fetch_client_meetings("2026-05-23", "2026-05-29", ["acme"])
    assert err is None
    assert len(all_titles) == 2
    assert [m["title"] for m in matched] == ["Acme sync"]


def test_fetch_client_meetings_empty_keywords_matches_nothing(monkeypatch):
    # A client with no keywords must match ZERO meetings (never everything) and
    # surface an error — the acute cross-client-leak guard.
    import notetaker
    monkeypatch.setattr(notetaker, "configured", lambda: True)
    monkeypatch.setattr(notetaker, "fetch_meetings", lambda *a, **k: [dict(m) for m in _fake_meetings()])
    matched, all_titles, err = ff.fetch_client_meetings("2026-05-23", "2026-05-29", [])
    assert matched == []
    assert err and "keywords" in err
    assert len(all_titles) == 2  # window titles still surfaced for the skill


def test_fetch_client_meetings_no_api_key(monkeypatch):
    import notetaker
    monkeypatch.setattr(notetaker, "configured", lambda: False)
    monkeypatch.setattr(notetaker, "env_key", lambda: "GRANOLA_API_KEY")
    matched, all_titles, err = ff.fetch_client_meetings("2026-05-23", "2026-05-29", ["acme"])
    assert matched == [] and all_titles == [] and err == "no GRANOLA_API_KEY configured"


# ── find_prior_report only trusts date-named, earlier reports ──────────────────

def test_find_prior_report_ignores_non_date_files(tmp_path):
    (tmp_path / "2026-05-22.md").write_text("PRIOR", encoding="utf-8")
    (tmp_path / "template.md").write_text("NOT A REPORT", encoding="utf-8")  # sorts after digits
    (tmp_path / "notes.md").write_text("NOT A REPORT", encoding="utf-8")
    path, text = ff.find_prior_report(tmp_path, "2026-05-29.md")
    assert path.endswith("2026-05-22.md")
    assert text == "PRIOR"


def test_find_prior_report_excludes_future_reports(tmp_path):
    # A --week-ending in the past must not carry forward from a later report.
    (tmp_path / "2026-05-15.md").write_text("EARLIER", encoding="utf-8")
    (tmp_path / "2026-06-05.md").write_text("LATER", encoding="utf-8")
    path, text = ff.find_prior_report(tmp_path, "2026-05-22.md")
    assert path.endswith("2026-05-15.md")
    assert text == "EARLIER"


# ── main error path + resolved_meta clients shape ─────────────────────────────

def test_unknown_client_tag_exits_1(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["five_fifteen.py", "--client", "nope"])
    with __import__("pytest").raises(SystemExit) as e:
        ff.main()
    assert e.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert "unknown client tag" in out["error"]


def test_resolved_meta_clients_shape():
    import config_loader
    saved = config_loader._config
    try:
        config_loader._config = {
            **saved,
            "clients": [{"tag": "t", "display_name": "T", "from_account": "Outlook",
                         "report_dir": "wiki/clients/T/5-15"}],
        }
        c = config_loader.resolved_meta()["clients"][0]
        assert c["tag"] == "t"
        assert c["contract"] == {}
        assert c["from_platform"] == "outlook"   # Outlook account -> outlook
        assert c["from_email"] == "user@company.com"
        assert c["report_dir"].endswith("5-15")
        # Unknown from_account resolves to empty, never raises.
        config_loader._config = {**saved, "clients": [{"tag": "u", "from_account": "Nope"}]}
        c2 = config_loader.resolved_meta()["clients"][0]
        assert c2["from_email"] == "" and c2["from_platform"] == ""
    finally:
        config_loader._config = saved
