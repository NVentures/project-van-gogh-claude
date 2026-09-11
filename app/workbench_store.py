#!/usr/bin/env python3
"""Write side of the Workbench: the ticket ledger.

Tickets live in `{vault}/van-gogh/workbench/tickets.json`, ratings append to
`ratings.jsonl` beside it. The server process is the only writer, so one
module-level lock plus an atomic replace is the whole concurrency story.

Two rules shape everything here:

* **Code owns rank and lifecycle, the model owns content.** `score()` is a pure
  function of the ticket's own fields, so the same inbox always ranks the same
  way and a user's pin always wins. Nothing about ordering is ever asked of a
  model.
* **A refresh may never destroy user intent.** `propose()` re-derives tickets
  from the latest sidecar and merges them onto what is already stored: state,
  comments, ratings and pins survive. Only rows still sitting untouched in
  `proposed` are pruned when they stop appearing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from config_loader import deal_critical_domains, van_gogh_root
from workbench_data import attention_items

_LOCK = threading.RLock()

TICKET_TYPES = ("email", "pptx", "xlsx", "docx")

# state -> states it may move to
TRANSITIONS = {
    "proposed":  {"staging", "dismissed", "approved"},
    # Dismissal stays reachable while a draft is being written: the user
    # changing their mind must never depend on a background job finishing.
    "staging":   {"staged", "failed", "dismissed"},
    "staged":    {"staging", "approved", "dismissed"},
    "approved":  {"running", "delivered", "failed", "staging"},
    "running":   {"verifying", "delivered", "failed"},
    "verifying": {"delivered", "running", "failed"},
    "delivered": {"rated"},
    "rated":     set(),
    "failed":    {"approved", "staging", "dismissed"},
    "dismissed": {"proposed"},
}

# Terminal-ish states a proposal refresh must never overwrite or prune.
USER_TOUCHED = {"staging", "staged", "approved", "running", "verifying",
                "delivered", "rated", "dismissed", "failed"}

_URGENCY_WEIGHT = {"high": 30, "medium": 15, "low": 5}
_SOURCE_WEIGHT = {"cold_urgent": 10, "manual": 8, "inbox_pending": 6,
                  "waiting_on_user": 4, "cold_monitor": 2}

_WS_RE = re.compile(r"\s+")
_RE_PREFIX_RE = re.compile(r"^(?:re|fw|fwd)\s*:\s*", re.IGNORECASE)


class IllegalTransition(Exception):
    """Raised when a caller asks for a lifecycle move the ledger forbids."""


# ── Paths ────────────────────────────────────────────────────────────────────

def workbench_dir() -> Path:
    return van_gogh_root() / "workbench"


def tickets_path() -> Path:
    return workbench_dir() / "tickets.json"


def ratings_path() -> Path:
    return workbench_dir() / "ratings.jsonl"


def briefs_dir() -> Path:
    return workbench_dir() / "briefs"


# ── Persistence ──────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read() -> dict:
    try:
        with open(tickets_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"tickets": {}, "pins": []}
    if not isinstance(data, dict):
        return {"tickets": {}, "pins": []}
    data.setdefault("tickets", {})
    data.setdefault("pins", [])
    return data


def _write(data: dict) -> None:
    """Atomic replace so a crash mid-write can never truncate the ledger."""
    path = tickets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


# ── Identity and rank ────────────────────────────────────────────────────────

def normalize_subject(subject: str) -> str:
    """Strip reply/forward prefixes so a thread keeps one id as it goes back and forth."""
    text = (subject or "").strip()
    while True:
        stripped = _RE_PREFIX_RE.sub("", text)
        if stripped == text:
            break
        text = stripped
    return _WS_RE.sub(" ", text).strip().lower()


def ticket_id(source_type: str, counterparty_email: str, subject: str) -> str:
    raw = f"{source_type}|{(counterparty_email or '').lower()}|{normalize_subject(subject)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def score(ticket: dict, critical_domains: set | None = None) -> int:
    """Deterministic priority. Pure: same ticket, same number, every time."""
    critical = critical_domains
    if critical is None:
        critical = {d.lower() for d in deal_critical_domains()}
    context = ticket.get("context") or {}
    domain = (ticket.get("counterparty_email") or "").rpartition("@")[2]

    total = _URGENCY_WEIGHT.get((context.get("urgency") or "low").lower(), 5)
    total += min(int(context.get("age_days") or 0), 14) * 2
    total += 20 if domain and domain in critical else 0
    total += _SOURCE_WEIGHT.get(ticket.get("source") or "", 0)
    if ticket.get("state") == "failed":
        total += 12          # a failure the user has not resolved outranks fresh noise
    return total


def ordered(data: dict | None = None) -> list:
    """Pins first in pin order, then everything else by score descending.

    Dismissed tickets never appear. Ties break on id so the list is stable
    across refreshes and two tickets never swap places for no reason.
    """
    data = _read() if data is None else data
    critical = {d.lower() for d in deal_critical_domains()}
    rows = [t for t in data["tickets"].values() if t.get("state") != "dismissed"]
    for row in rows:
        row["score"] = score(row, critical)

    pins = [p for p in data.get("pins", [])]
    pin_rank = {tid: i for i, tid in enumerate(pins)}
    rows.sort(key=lambda t: (
        pin_rank.get(t["id"], len(pins)),
        -t["score"],
        t["id"],
    ))
    return rows


# ── Ticket construction ──────────────────────────────────────────────────────

def _dod_for(item: dict) -> str:
    """The definition of done a ticket carries into staging, in past tense.

    Written as the finished state rather than an instruction, matching the
    interface grammar in DESIGN.md. The user can edit it before approving.
    """
    who = item.get("counterparty_name") or item.get("counterparty_email") or "them"
    action = (item.get("suggested_action") or "").strip().rstrip(".")
    if action:
        return f"Replied to {who}: {action}."
    return f"Replied to {who} on \"{item.get('subject') or 'the open thread'}\"."


def ticket_from_item(item: dict) -> dict:
    tid = ticket_id(item["source"], item["counterparty_email"], item["subject"])
    who = item.get("counterparty_name") or item.get("counterparty_email") or "Unknown"
    return {
        "id": tid,
        "type": "email",
        "state": "proposed",
        "title": f"{who}: {item.get('subject') or '(no subject)'}",
        "source": item["source"],
        "counterparty_name": item.get("counterparty_name") or "",
        "counterparty_email": item.get("counterparty_email") or "",
        "account": item.get("account") or "",
        "context": {
            "subject": item.get("subject") or "",
            "body_preview": item.get("body_preview") or "",
            "summary": item.get("summary") or "",
            "intent": item.get("intent") or "",
            "urgency": item.get("urgency") or "low",
            "suggested_action": item.get("suggested_action") or "",
            "age_days": item.get("age_days") or 0,
        },
        "dod": _dod_for(item),
        "comments": [],
        "staged": None,
        "delivery": None,
        "rating": None,
        "created_at": _now(),
        "history": [{"at": _now(), "to": "proposed", "note": "proposed from briefing"}],
    }


def manual_ticket(title: str, ttype: str, dod: str = "") -> dict:
    if ttype not in TICKET_TYPES:
        raise ValueError(f"unknown ticket type: {ttype}")
    tid = ticket_id("manual", "", title)
    return {
        "id": tid,
        "type": ttype,
        "state": "proposed",
        "title": title.strip() or "Untitled",
        "source": "manual",
        "counterparty_name": "",
        "counterparty_email": "",
        "account": "",
        "context": {"urgency": "medium", "age_days": 0, "summary": "", "intent": "",
                    "subject": title.strip(), "body_preview": "", "suggested_action": ""},
        "dod": dod.strip(),
        "comments": [],
        "staged": None,
        "delivery": None,
        "rating": None,
        "created_at": _now(),
        "history": [{"at": _now(), "to": "proposed", "note": "created by hand"}],
    }


# ── Mutations ────────────────────────────────────────────────────────────────

def propose(items: list | None = None) -> dict:
    """Re-derive proposals from the briefing and merge onto the stored ledger.

    Returns `{"added": n, "pruned": n, "kept": n}`. Never touches a ticket the
    user has acted on: its state, comments, rating, dod edits and pin all
    survive, and only its read-only `context` is refreshed so age and urgency
    stay current.
    """
    rows = attention_items() if items is None else items
    with _LOCK:
        data = _read()
        stored = data["tickets"]
        fresh = {}
        for item in rows:
            ticket = ticket_from_item(item)
            fresh[ticket["id"]] = ticket

        added = kept = 0
        for tid, ticket in fresh.items():
            existing = stored.get(tid)
            if existing is None:
                stored[tid] = ticket
                added += 1
                continue
            existing["context"] = ticket["context"]
            existing["account"] = ticket["account"] or existing.get("account", "")
            if existing.get("state") == "proposed":
                existing["title"] = ticket["title"]
                if not existing.get("dod_edited"):
                    existing["dod"] = ticket["dod"]
            kept += 1

        pruned = 0
        for tid in list(stored):
            row = stored[tid]
            if tid in fresh or row.get("source") == "manual":
                continue
            if row.get("state") in USER_TOUCHED:
                continue
            del stored[tid]
            pruned += 1

        data["pins"] = [p for p in data.get("pins", []) if p in stored]
        _write(data)
        return {"added": added, "pruned": pruned, "kept": kept}


def get(ticket_id_: str) -> dict | None:
    return _read()["tickets"].get(ticket_id_)


def create(title: str, ttype: str = "email", dod: str = "") -> dict:
    with _LOCK:
        data = _read()
        ticket = manual_ticket(title, ttype, dod)
        # A hand-made ticket with a title that already exists is the same ask.
        if ticket["id"] in data["tickets"]:
            return data["tickets"][ticket["id"]]
        data["tickets"][ticket["id"]] = ticket
        _write(data)
        return ticket


def transition(ticket_id_: str, to_state: str, note: str = "", **fields) -> dict:
    """Move one ticket. Raises rather than silently accepting a bad move."""
    with _LOCK:
        data = _read()
        ticket = data["tickets"].get(ticket_id_)
        if ticket is None:
            raise KeyError(ticket_id_)
        current = ticket.get("state", "proposed")
        if to_state not in TRANSITIONS.get(current, set()):
            raise IllegalTransition(f"{ticket_id_}: {current} -> {to_state}")
        ticket["state"] = to_state
        ticket.update(fields)
        ticket.setdefault("history", []).append(
            {"at": _now(), "to": to_state, "note": note})
        _write(data)
        return ticket


def attach(ticket_id_: str, **fields) -> dict:
    """Write fields onto a ticket without moving it.

    Evidence gathered while a ticket is being staged belongs on the ticket, but
    attaching it is not a lifecycle event: `transition` would have to allow a
    state to re-enter itself, which would weaken the table for every caller.
    """
    with _LOCK:
        data = _read()
        ticket = data["tickets"].get(ticket_id_)
        if ticket is None:
            raise KeyError(ticket_id_)
        ticket.update(fields)
        _write(data)
        return ticket


def comment(ticket_id_: str, text: str) -> dict:
    """Append a revision note and send the ticket back for re-staging.

    A comment never approves anything. Whatever was staged is now stale, so a
    staged ticket drops to `staging` and has to earn a fresh confirmation.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty comment")
    with _LOCK:
        data = _read()
        ticket = data["tickets"].get(ticket_id_)
        if ticket is None:
            raise KeyError(ticket_id_)
        ticket.setdefault("comments", []).append({"at": _now(), "text": text})
        if ticket.get("state") in ("staged", "failed", "proposed"):
            ticket["state"] = "staging"
            ticket.setdefault("history", []).append(
                {"at": _now(), "to": "staging", "note": "comment added, re-staging"})
        _write(data)
        return ticket


def edit_dod(ticket_id_: str, dod: str) -> dict:
    with _LOCK:
        data = _read()
        ticket = data["tickets"].get(ticket_id_)
        if ticket is None:
            raise KeyError(ticket_id_)
        ticket["dod"] = (dod or "").strip()
        ticket["dod_edited"] = True
        _write(data)
        return ticket


def pin(ticket_id_: str, position: int | None = None) -> list:
    """Pin a ticket to the top, or unpin it when position is None."""
    with _LOCK:
        data = _read()
        if ticket_id_ not in data["tickets"]:
            raise KeyError(ticket_id_)
        pins = [p for p in data.get("pins", []) if p != ticket_id_]
        if position is not None:
            pins.insert(max(0, min(int(position), len(pins))), ticket_id_)
        data["pins"] = pins
        _write(data)
        return pins


def rate(ticket_id_: str, stars: int, note: str = "") -> dict:
    """Record a rating on delivered work and append it to the ratings log."""
    stars = int(stars)
    if not 1 <= stars <= 5:
        raise ValueError("stars must be 1 to 5")
    with _LOCK:
        data = _read()
        ticket = data["tickets"].get(ticket_id_)
        if ticket is None:
            raise KeyError(ticket_id_)
        rating = {"stars": stars, "note": (note or "").strip(), "at": _now()}
        ticket["rating"] = rating
        if ticket.get("state") == "delivered":
            ticket["state"] = "rated"
            ticket.setdefault("history", []).append(
                {"at": _now(), "to": "rated", "note": f"{stars} of 5"})
        _write(data)

        path = ratings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ticket_id": ticket_id_, "type": ticket.get("type"),
                "title": ticket.get("title"), "dod": ticket.get("dod"),
                **rating,
            }, default=str) + "\n")
        return ticket


def recent_ratings(limit: int = 10) -> list:
    """The last N ratings, newest first. Feeds the staging prompt so the model
    learns from what the user actually liked instead of being told in the abstract."""
    try:
        with open(ratings_path(), encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        return []
    return rows[-limit:][::-1]


def counts() -> dict:
    """Per-state counts for the section bar."""
    out: dict = {}
    for ticket in _read()["tickets"].values():
        state = ticket.get("state", "proposed")
        out[state] = out.get(state, 0) + 1
    return out
