"""kpi_events: the append-only evidence log the weekly scorecard reads.

The test that matters most here is the leak test. Everything else in this file
guards a convenience; `test_no_content_reaches_the_log` guards a promise made
to the user in the email's own footer, so it is written to fail loudly and is
proven by a mutation that puts a subject back.
"""
from __future__ import annotations

import json

import pytest

import kpi_events

# The genuine enabled(), captured before the autouse fixture stubs it, so the
# switch tests can put it back and exercise the real thing.
_real_enabled = kpi_events.enabled


@pytest.fixture(autouse=True)
def logs(tmp_path, monkeypatch):
    """Point the log at a temp dir and turn the feature on for every test."""
    monkeypatch.setattr(kpi_events, "events_path",
                        lambda: tmp_path / "kpi_events.jsonl")
    monkeypatch.setattr(kpi_events, "enabled", lambda: True)
    return tmp_path / "kpi_events.jsonl"


def read_lines(path):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()
            if x.strip()]


# ── Writing ──────────────────────────────────────────────────────────────────

def test_one_event_is_one_line(logs):
    kpi_events.record("snapshot", briefing="week", counts={"open": 3})
    kpi_events.record("closed", keys=["abc"])
    rows = read_lines(logs)
    assert len(rows) == 2
    assert rows[0]["kind"] == "snapshot"
    assert rows[0]["briefing"] == "week"
    assert rows[1]["keys"] == ["abc"]
    assert all(r.get("ts") for r in rows)


def test_an_unwritable_directory_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(kpi_events, "enabled", lambda: True)
    monkeypatch.setattr(kpi_events, "events_path",
                        lambda: tmp_path / "nope" / "deep" / "x.jsonl")

    def boom(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(kpi_events.Path, "mkdir", boom)
    kpi_events.record("snapshot", briefing="week")     # must not raise


def test_an_unserializable_value_never_raises_and_writes_nothing(logs):
    class Hostile:
        def __repr__(self):
            raise RuntimeError("not even repr")

    kpi_events.record("snapshot", briefing="week", weird=Hostile())
    # `default=str` calls repr, which explodes; the guard catches it and the
    # torn line never reaches the file.
    assert read_lines(logs) == []


def test_serialization_failure_leaves_no_partial_line(logs):
    """A bad value must not truncate the file for the reader.

    The write is one call on an already-serialized string, so a value that
    cannot be encoded fails before the handle is opened.
    """
    kpi_events.record("snapshot", briefing="good")

    class Hostile:
        def __repr__(self):
            raise RuntimeError("no")

    kpi_events.record("snapshot", briefing="bad", weird=Hostile())
    rows, skipped = kpi_events.read(logs)
    assert skipped == 0
    assert [r["briefing"] for r in rows] == ["good"]


def test_the_env_var_turns_it_off(tmp_path, monkeypatch):
    path = tmp_path / "kpi_events.jsonl"
    monkeypatch.setattr(kpi_events, "events_path", lambda: path)
    # Undo the fixture's stub: the real enabled() is what is under test here.
    monkeypatch.setattr(kpi_events, "enabled", _real_enabled)
    monkeypatch.setenv("VAN_GOGH_DISABLE_KPI", "1")
    kpi_events.record("snapshot", briefing="week")
    assert not path.exists()


def test_the_config_flag_turns_it_off(tmp_path, monkeypatch):
    path = tmp_path / "kpi_events.jsonl"
    monkeypatch.setattr(kpi_events, "events_path", lambda: path)
    monkeypatch.setattr(kpi_events, "enabled", _real_enabled)
    monkeypatch.delenv("VAN_GOGH_DISABLE_KPI", raising=False)
    import config_loader

    monkeypatch.setattr(config_loader, "kpi_enabled", lambda: False)
    kpi_events.record("snapshot", briefing="week")
    assert not path.exists()


def test_the_switches_can_actually_let_a_write_through(tmp_path, monkeypatch):
    """MUTATION CONTROL for the two switch tests above.

    With both switches ON the same call must write, otherwise those two tests
    would pass against a writer that never writes at all."""
    path = tmp_path / "kpi_events.jsonl"
    monkeypatch.setattr(kpi_events, "events_path", lambda: path)
    monkeypatch.setattr(kpi_events, "enabled", _real_enabled)
    monkeypatch.delenv("VAN_GOGH_DISABLE_KPI", raising=False)
    import config_loader

    monkeypatch.setattr(config_loader, "kpi_enabled", lambda: True)
    kpi_events.record("snapshot", briefing="week")
    assert path.exists()


# ── The leak guard ───────────────────────────────────────────────────────────

# A briefing output shaped exactly like the real thing, carrying strings that
# must never survive into the log.
LEAKY_OUTPUT = {
    "counts": {"open": 2, "late": 1, "front": 2},
    "front_page": [
        {"key": ["kestrel term sheet", "dana@meridian.example"],
         "subject": "Re: Kestrel term sheet", "counterparty_email": "dana@meridian.example",
         "counterparty_name": "Dana Whitfield", "days_late": 4},
        {"key": ["site walk", "marco@cedarpoint.example"],
         "subject": "Site walk", "counterparty_email": "marco@cedarpoint.example",
         "counterparty_name": "Marco Lind", "days_late": 0},
    ],
    "buckets": [
        {"tag": "harbor", "items": [
            {"subject": "Re: Kestrel term sheet",
             "counterparty_email": "dana@meridian.example",
             "counterparty_name": "Dana Whitfield",
             "body_preview": "Confidential: the board agreed to 14 million.",
             "days_late": 4},
            {"subject": "Site walk", "counterparty_email": "marco@cedarpoint.example",
             "counterparty_name": "Marco Lind", "body_preview": "See you Tuesday.",
             "days_late": 0},
        ]},
    ],
}

SECRETS = [
    "Kestrel", "kestrel", "term sheet", "Dana", "Whitfield",
    "dana@meridian.example", "meridian", "Marco", "Lind",
    "marco@cedarpoint.example", "cedarpoint", "Site walk",
    "Confidential", "board agreed", "14 million",
]


def test_no_content_reaches_the_log(logs):
    """Not one identifying string survives into the serialized line.

    Asserted against the RAW text of the file, not a parsed dict: a leak nested
    inside a structure the parser flattens would pass a key-by-key check.
    """
    kpi_events.record_snapshot("morning-coffee", LEAKY_OUTPUT)
    raw = logs.read_text(encoding="utf-8")
    assert raw.strip(), "nothing was written, the test would pass vacuously"
    for secret in SECRETS:
        assert secret not in raw, f"{secret!r} leaked into the events log"


def test_the_leak_test_can_actually_fail(logs):
    """MUTATION CONTROL for the test above.

    Writing a raw subject the way a careless change would must make the same
    assertion red. Without this, the leak test passes just as well over an
    empty file or a writer that records nothing.
    """
    kpi_events.record("snapshot", briefing="morning-coffee",
                      subject="Re: Kestrel term sheet")
    raw = logs.read_text(encoding="utf-8")
    leaked = [s for s in SECRETS if s in raw]
    assert leaked, "the mutation did not leak, so the leak test proves nothing"


def test_the_snapshot_still_carries_the_numbers(logs):
    """Content-free must not mean useless."""
    kpi_events.record_snapshot("morning-coffee", LEAKY_OUTPUT)
    row = read_lines(logs)[0]
    assert row["counts"] == {"open": 2, "late": 1, "front": 2}
    assert len(row["front"]) == 2
    assert len(row["open_keys"]) == 2
    assert row["late_keys"] and len(row["late_keys"]) == 1
    assert {f["late"] for f in row["front"]} == {4, 0}


def test_front_page_and_bucket_keys_agree(logs):
    """The same item reached by two paths must hash the same.

    The front page carries a pre-normalized [subject, email] pair and the
    bucket carries the raw subject. If those produced different keys, an item
    would look like it left the open set the moment it reached the front page.
    """
    kpi_events.record_snapshot("morning-coffee", LEAKY_OUTPUT)
    row = read_lines(logs)[0]
    assert set(f["key"] for f in row["front"]) <= set(row["open_keys"])


# ── The one key function ─────────────────────────────────────────────────────

@pytest.mark.parametrize("subject", [
    "Kestrel term sheet",
    "Re: Kestrel term sheet",
    "RE: Kestrel term sheet",
    "Fwd: Kestrel term sheet",
    "FW: Re: Kestrel term sheet",
    "[EXTERNAL] Kestrel term sheet",
    "Re: [EXTERNAL] Kestrel term sheet",
    "[EXTERNAL] Re: Fwd: Kestrel term sheet",
    "  Kestrel term sheet  ",
    "Kestrel  term   sheet",
    "kestrel TERM sheet",
])
def test_one_thread_keeps_one_key(subject):
    """Every mutation of a real subject line resolves to the same identity."""
    assert kpi_events.item_key(subject, "dana@meridian.example") == \
           kpi_events.item_key("Kestrel term sheet", "dana@meridian.example")


def test_counterparty_case_does_not_fork_a_thread():
    assert kpi_events.item_key("Kestrel", "Dana@Meridian.Example") == \
           kpi_events.item_key("Kestrel", "dana@meridian.example")


def test_different_threads_get_different_keys():
    a = kpi_events.item_key("Kestrel term sheet", "dana@meridian.example")
    b = kpi_events.item_key("Site walk", "dana@meridian.example")
    c = kpi_events.item_key("Kestrel term sheet", "marco@cedarpoint.example")
    assert len({a, b, c}) == 3


def test_a_key_is_short_and_carries_no_source_text():
    k = kpi_events.item_key("Kestrel term sheet", "dana@meridian.example")
    assert len(k) == 12
    assert "kestrel" not in k and "dana" not in k


def test_the_full_value_is_hashed_never_a_prefix():
    """Two ids sharing a long prefix must not collapse onto one key.

    Provider ids share constant prefixes; truncating one before hashing once
    collapsed 23 distinct messages onto a single key.
    """
    prefix = "AAMkAGI2NjhmYmEwLTRiNTYt" * 3
    assert kpi_events.key(prefix + "aaa") != kpi_events.key(prefix + "bbb")


def test_only_one_implementation_of_the_key_exists():
    """No second hash of an item lives under app/.

    The glob is quoted and the scan is proven on a known-present instance
    first: an unquoted `--include=*.py` aborts under zsh and a bare hit count
    would then read as a clean scan that never ran.
    """
    from pathlib import Path

    app = Path(__file__).resolve().parent.parent / "app"
    files = sorted(app.glob("*.py"))
    assert len(files) > 20, "the scan found almost no files, it is not working"

    # Proven on a known-present instance: kpi_events itself must match.
    hits = [f.name for f in files if "hashlib.sha1" in f.read_text(encoding="utf-8")]
    assert "kpi_events.py" in hits, "the scanner cannot even find the real one"

    # Any OTHER module hashing an item identity would be a second key function.
    others = [f.name for f in files
              if f.name != "kpi_events.py"
              and "def item_key" in f.read_text(encoding="utf-8")]
    assert others == [], f"a second item_key implementation exists: {others}"


# ── Reading ──────────────────────────────────────────────────────────────────

def test_a_torn_last_line_is_skipped_and_counted(logs):
    kpi_events.record("snapshot", briefing="week")
    with open(logs, "a", encoding="utf-8") as f:
        f.write('{"kind": "snapshot", "ts": "2026-09-0')   # crash mid-write
    rows, skipped = kpi_events.read(logs)
    assert len(rows) == 1 and skipped == 1


def test_a_missing_file_reads_as_empty(tmp_path):
    rows, skipped = kpi_events.read(tmp_path / "absent.jsonl")
    assert rows == [] and skipped == 0


def test_prune_drops_old_rows_and_keeps_fresh_ones(logs):
    from datetime import datetime, timedelta

    old = (datetime.now() - timedelta(days=200)).isoformat(timespec="seconds")
    new = datetime.now().isoformat(timespec="seconds")
    with open(logs, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": old, "kind": "snapshot", "briefing": "old"}) + "\n")
        f.write(json.dumps({"ts": new, "kind": "snapshot", "briefing": "new"}) + "\n")
    dropped = kpi_events.prune(keep_days=120, path=logs)
    rows, _ = kpi_events.read(logs)
    assert dropped == 1
    assert [r["briefing"] for r in rows] == ["new"]


def test_prune_keeps_a_row_whose_date_cannot_be_read(logs):
    """An unreadable date is not evidence the row is old."""
    with open(logs, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "not-a-date", "kind": "snapshot"}) + "\n")
    kpi_events.prune(keep_days=1, path=logs)
    rows, _ = kpi_events.read(logs)
    assert len(rows) == 1
