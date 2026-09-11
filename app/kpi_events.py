#!/usr/bin/env python3
"""The scorecard's evidence: an append-only log of what the product did.

The weekly scorecard email needs history, and until this module existed there
was none. Each briefing overwrote one sidecar, so "how many threads were
waiting on you two weeks ago" had no answer anywhere on the machine.

Three rules shape everything here, and all three are load-bearing.

**Content never leaves the briefing.** A line in this file carries counts,
timestamps and hashed keys. It never carries a subject, a name, an address or
a body. That is not a policy applied at the edges, it is the shape of the
writer: `snapshot_from_output` hashes on the way in, so there is no code path
that could write a subject even by accident. A file the user might sync to
iCloud must be boring to anyone who reads it.

**One key function, imported everywhere.** Item identity is `item_key`, and
nothing else. A second implementation that normalizes `Re:` differently forks
one thread into two, which reads downstream as "an item vanished and a new one
appeared" and corrupts four KPIs at once in the direction of looking busy.
`tests/test_kpi_events.py` greps for a second implementation.

**It fails open, always.** Recording evidence must never cost a briefing. Every
path here swallows its own errors, because a scorecard is worth less than the
briefing it is counting.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

# Reply and forward prefixes, in the languages the product already handles
# (week_review.normalize_subject carries the same five), plus the bracketed
# tags a mail gateway staples on. A thread renames itself every round trip and
# a gateway can add its tag mid-thread, so both have to come off or the same
# thread lands under two keys.
_PREFIX_RE = re.compile(r"^(re|fw|fwd|aw|sv)\s*:\s*", re.IGNORECASE)
_TAG_RE = re.compile(r"^\[[^\]]{1,40}\]\s*")
_WS_RE = re.compile(r"\s+")

# How many rounds of prefix stripping to try. A real thread rarely exceeds
# three; ten is far past anything genuine and bounds a pathological subject.
_MAX_PREFIX_ROUNDS = 10

EVENTS_FILENAME = "kpi_events.jsonl"

# Event kinds. Named here so a typo in a caller is a failed import rather than
# a line nobody ever reads.
SNAPSHOT = "snapshot"
CLOSED = "closed"
PREP = "prep"


def events_path() -> Path:
    import config_loader

    return config_loader.logs_dir() / EVENTS_FILENAME


def enabled() -> bool:
    """False turns the whole module into a no-op.

    Two switches on purpose: the env var is for a test or a one-off run, the
    config flag is the user's own setting. Either one off means off.
    """
    if os.environ.get("VAN_GOGH_DISABLE_KPI", "").strip() == "1":
        return False
    try:
        import config_loader

        return bool(config_loader.kpi_enabled())
    except Exception:                                           # noqa: BLE001
        return False


def normalize_subject(subject: str) -> str:
    """One subject, one spelling, however many times it has been round-tripped.

    Strips gateway tags and reply prefixes in either order and any number of
    times, because `Re: [EXTERNAL] Fwd: Re: Kestrel` is one thread and so is
    `Kestrel`. Collapses whitespace last, so a subject that picked up a line
    break in transit still matches its own earlier self.
    """
    text = (subject or "").strip().lower()
    for _ in range(_MAX_PREFIX_ROUNDS):
        stripped = _TAG_RE.sub("", text)
        stripped = _PREFIX_RE.sub("", stripped)
        stripped = stripped.strip()
        if stripped == text:
            break
        text = stripped
    return _WS_RE.sub(" ", text).strip()


def key(*parts: str) -> str:
    """A stable, content-free id for a thing, from the parts that identify it.

    The full value of every part is hashed. Truncating a provider id before
    hashing is how 23 distinct messages once collapsed onto one key, because
    ids share a long constant prefix; the truncation here is of the DIGEST,
    which has no such structure.
    """
    raw = "|".join((p or "").strip().lower() for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def item_key(subject: str, counterparty_email: str) -> str:
    """THE identity of one open item. Every producer calls this, none copies it."""
    return key(normalize_subject(subject), (counterparty_email or "").strip())


def meeting_key(title: str, day: str) -> str:
    """The identity of one meeting. Title plus day, since ids differ by source."""
    return key(normalize_subject(title), day)


def record(kind: str, **fields) -> None:
    """Append one event. Never raises, whatever goes wrong.

    Written with a single `write` call: a briefing and a tick can land in the
    same second, and two partial writes interleaved is a corrupt line for both.
    """
    try:
        if not enabled():
            return
        record_dict = {"ts": datetime.now().isoformat(timespec="seconds"),
                       "kind": str(kind)}
        record_dict.update(fields)
        # Serialize BEFORE opening the file. A value that cannot be encoded
        # would otherwise raise with the handle open, leaving a truncated line
        # behind for the reader to trip over.
        line = json.dumps(record_dict, default=str) + "\n"
        path = events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:                                           # noqa: BLE001
        pass


def _front_rows(output: dict) -> list:
    rows = []
    for item in (output.get("front_page") or []):
        if not isinstance(item, dict):
            continue
        raw = item.get("key")
        # front_page carries its own key as a [subject, email] pair. Rebuild
        # through item_key rather than trusting the pair's spelling, so the
        # front page and the bucket list can never disagree about identity.
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            k = item_key(str(raw[0]), str(raw[1]))
        else:
            k = item_key(item.get("subject") or "",
                         item.get("counterparty_email") or "")
        rows.append({
            "key": k,
            "late": int(item.get("days_late") or 0),
        })
    return rows


def _open_keys(output: dict) -> list:
    keys = []
    seen = set()
    for bucket in (output.get("buckets") or []):
        if not isinstance(bucket, dict):
            continue
        for entry in (bucket.get("items") or []):
            if not isinstance(entry, dict):
                continue
            k = item_key(entry.get("subject") or "",
                         entry.get("counterparty_email") or "")
            if k in seen:
                continue
            seen.add(k)
            keys.append(k)
    return keys


def _late_keys(output: dict) -> list:
    keys = []
    seen = set()
    for bucket in (output.get("buckets") or []):
        if not isinstance(bucket, dict):
            continue
        for entry in (bucket.get("items") or []):
            if not isinstance(entry, dict):
                continue
            if not int(entry.get("days_late") or 0):
                continue
            k = item_key(entry.get("subject") or "",
                         entry.get("counterparty_email") or "")
            if k in seen:
                continue
            seen.add(k)
            keys.append(k)
    return keys


def snapshot_from_output(briefing: str, output: dict) -> dict:
    """Turn a briefing's JSON output into the content-free row we keep.

    This is the only bridge between briefing data and the events file, which is
    what makes "no content leaks" a property of the code rather than a habit.
    Every string that identifies a person or a thread is hashed here.
    """
    output = output if isinstance(output, dict) else {}
    counts = output.get("counts") if isinstance(output.get("counts"), dict) else {}
    return {
        "briefing": str(briefing),
        "counts": {
            "open": int(counts.get("open") or 0),
            "late": int(counts.get("late") or 0),
            "front": int(counts.get("front") or 0),
        },
        "front": _front_rows(output),
        "open_keys": _open_keys(output),
        "late_keys": _late_keys(output),
    }


def record_snapshot(briefing: str, output: dict) -> None:
    """Record one briefing run. Fails open like everything else here."""
    try:
        record(SNAPSHOT, **snapshot_from_output(briefing, output))
    except Exception:                                           # noqa: BLE001
        pass


def read(path: Path | None = None) -> tuple:
    """Every event, plus how many lines could not be read.

    Returns `(rows, skipped)`. A crash mid-append leaves a torn final line and
    a synced vault can produce a conflicted copy, so an unreadable line is
    counted and stepped over. The count is returned rather than swallowed
    because a report computed over a silent subset is how a plausible wrong
    number is born.
    """
    target = events_path() if path is None else Path(path)
    rows, skipped = [], 0
    try:
        with open(target, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    skipped += 1
                    continue
                if isinstance(row, dict) and row.get("ts"):
                    rows.append(row)
                else:
                    skipped += 1
    except OSError:
        return [], 0
    return rows, skipped


def prune(keep_days: int = 120, path: Path | None = None) -> int:
    """Drop events older than `keep_days`. Returns how many were dropped.

    An append-only file written by four scripts on every run, inside a folder
    the user probably syncs, does not get to grow forever.
    """
    target = events_path() if path is None else Path(path)
    try:
        rows, _ = read(target)
        if not rows:
            return 0
        cutoff = datetime.now().timestamp() - keep_days * 86400
        keep = []
        for row in rows:
            try:
                if datetime.fromisoformat(str(row.get("ts"))).timestamp() >= cutoff:
                    keep.append(row)
            except ValueError:
                keep.append(row)      # unreadable date: keep, never guess
        dropped = len(rows) - len(keep)
        if dropped <= 0:
            return 0
        tmp = target.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for row in keep:
                f.write(json.dumps(row, default=str) + "\n")
        os.replace(tmp, target)
        return dropped
    except Exception:                                           # noqa: BLE001
        return 0
