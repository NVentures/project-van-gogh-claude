#!/usr/bin/env python3
"""Ask any connected system about a person, with no code written for it.

`connector_fetch.fetch` reads a connector exactly: the caller names the tools
and the arguments, and the reply is parsed by code written for that one
service. That is right for the books, where a wrong date range is a wrong
number, and it means every new service needs its own module.

A CRM does not need that. Before a call the question is always the same, "what
does this system hold about this person", and the answer is read by a person,
not summed. So this is the general path: the user names a connector once
(`config.json connectors[]`), says which of its tools may be used, and any
capability can ask it about someone.

The trade is stated plainly, because it is the whole design.

**What stays in code.** Which tools exist for the session (the same whitelist
flags `connector_fetch` uses, so a tool not on the list does not exist for the
call), that every listed tool is named as a read (checked when the connector
is added and again at every use), how many calls count, and whether a result
is about this person at all.

**What the model decides.** The arguments. Every CRM spells "search contacts"
differently, and that knowledge is exactly what there is no code for.

**So a finding is context, never a fact to act on.** It is returned with the
tool and arguments that produced it and shown to the reader as a quotation from
that system. Nothing here may be used to write to the vault, pick a recipient
or change a record.

**A result that does not name the person is dropped.** A search that matched
nothing often returns a list of other people, and attaching someone else's deal
to an attendee is worse than saying the system had nothing. The address, or the
first and last name together, must appear in the text, and not merely because
the tool repeated the question: its restatement of the query is removed first.
A system that restates a query in some other shape can still get past that,
which is one more reason a finding is shown as a quotation and trusted as
nothing more.

Usage:
    python app/connector_lookup.py --list
    python app/connector_lookup.py --add crm --server claude_ai_HubSpot \\
        --tools search_contacts,get_contact --about "contacts and deals"
    python app/connector_lookup.py --test crm --name "Ada Lovelace" --email ada@example.com
    python app/connector_lookup.py --remove crm
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import claude_update                                            # noqa: E402
import config_loader                                            # noqa: E402
import connector_fetch as cf                                    # noqa: E402
from claude_cli import HAIKU_MODEL                              # noqa: E402

# Tool calls that count toward one person. Find the record, read it, read what
# hangs off it: four is that with one to spare. Results past it are ignored.
MAX_CALLS = 4

# One lookup, including the model's turns and a slow connector handshake.
LOOKUP_TIMEOUT_S = 150

# Everything a single prep run may spend on lookups, and how many people it
# will ask about. A day with twenty attendees still has to mail a prep.
BUDGET_S = 420
PEOPLE_CAP = 8

# Characters of one result that reach the brief, cut around the first mention
# of the person so a long listing shows the part that is about them.
RESULT_CHARS = 3000
LEAD_CHARS = 600

CACHE_DAYS = 2

# Verdicts beyond connector_fetch's. `nothing` is an answer: the system was
# asked and holds nothing naming this person. `skipped` never asked.
NOTHING = "nothing"
SKIPPED = "skipped"

# The default use. A connector is offered to every capability that asks for
# people unless its entry says otherwise.
PEOPLE = "people"

_NAME_OK = re.compile(r"[^A-Za-z0-9 .,'\-]")
_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9\-]{0,39}$")
_TOOL_OK = re.compile(r"^[A-Za-z0-9_\-]{1,120}$")

# Addresses whose domain says nothing about a company.
_FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com",
              "live.com", "yahoo.com", "icloud.com", "me.com", "aol.com",
              "proton.me", "protonmail.com", "msn.com"}


# ── What may be used ─────────────────────────────────────────────────────────

def server_key(server: str) -> str:
    """A server name in the form its tools are prefixed with.

    Accepts the display name (`claude.ai HubSpot`), the prefix
    (`mcp__claude_ai_HubSpot`) or the bare key, so a person can paste whichever
    one the listing showed them.
    """
    server = (server or "").strip()
    if server.startswith("mcp__"):
        server = server[len("mcp__"):]
    server = server.split("__")[0] if "__" in server else server
    return cf._prefix_for(server)[len("mcp__"):]


def tool_key(tool: str, server: str = "") -> str:
    """A tool's own name, with any `mcp__<server>__` prefix removed."""
    tool = (tool or "").strip()
    if tool.startswith("mcp__") and "__" in tool[len("mcp__"):]:
        tool = tool.split("__", 2)[2]
    return tool


# A tool may be used only when its name SAYS it reads. `connector_fetch` sorts
# a listing by the opposite test (no word that changes things), which is fine
# for a list a person reads and not for a session nobody watches: on the first
# real connector tried here it passed `rebuild_corpus`, because "rebuild" was
# not on anyone's list of verbs. Naming what is allowed cannot miss that way.
_READ_WORDS = {"get", "list", "search", "read", "query", "fetch", "find",
               "lookup", "retrieve", "show", "describe", "view", "count"}
_CHANGE_WORDS = {"create", "update", "delete", "submit", "save", "assign",
                 "duplicate", "send", "void", "apply", "upload", "attach",
                 "add", "remove", "set", "write", "invite", "archive",
                 "rebuild", "build", "run", "execute", "merge", "sync",
                 "import", "trigger", "start", "stop", "cancel", "close",
                 "move", "rename", "edit", "patch", "put", "insert", "upsert",
                 "clear", "reset", "enable", "disable", "approve", "reject",
                 "mark", "log", "record", "book", "schedule", "publish",
                 "share", "convert", "generate", "prime", "reprime"}


def reads_only(tool: str) -> bool:
    """Whether a tool's name has a word that reads and none that changes.

    Whole words only, so `get_assignments` reads and `assign_owner` does not.
    It over-refuses: `company_info` and `timeline` both read and are both
    turned away for having no reading word in them. That is the right way to
    be wrong here, and the cost is a tool the user cannot pick.
    """
    words = {w for w in re.split(r"[^a-z0-9]+", tool_key(tool).lower()) if w}
    return bool(words & _READ_WORDS) and not (words & _CHANGE_WORDS)


def split_tools(tools: list) -> tuple:
    """`(usable, refused)`, by `reads_only`. Checked when a connector is added
    and again at every use, so a hand-edited entry gets no further than a
    saved one."""
    usable, refused = [], []
    for tool in tools or []:
        name = tool_key(str(tool))
        if not name or not _TOOL_OK.match(name) or name in usable or name in refused:
            continue
        (usable if reads_only(name) else refused).append(name)
    return usable, refused


def configured(use: str = PEOPLE) -> list:
    """Connectors set up for this use, each with only its usable tools."""
    out = []
    for entry in config_loader.connectors():
        if use not in entry["use_for"]:
            continue
        usable, _ = split_tools(entry["tools"])
        if usable:
            out.append({**entry, "tools": usable})
    return out


# ── The question ─────────────────────────────────────────────────────────────

def clean_person(person: dict) -> dict:
    """A person as far as they may be named in a prompt.

    The name comes from a calendar invite, which anyone can send. It is cut to
    the characters a name is made of so an invite cannot carry instructions
    into the session that reads the CRM.
    """
    raw = str(person.get("name") or "")
    # An invite with no display name carries the address in its place.
    name = "" if "@" in raw else " ".join(_NAME_OK.sub(" ", raw).split())[:80]
    email = str(person.get("email") or "").strip().lower()
    if not re.match(r"^[a-z0-9._%+'\-]+@[a-z0-9.\-]+\.[a-z]{2,}$", email):
        email = ""
    domain = email.split("@")[1] if email else ""
    return {"name": name, "email": email,
            "domain": "" if domain in _FREE_MAIL else domain}


def build_prompt(connector: dict, person: dict) -> str:
    lines = [
        "You are reading one system on behalf of its owner, who is preparing "
        "for a meeting. You may call only the tools you have, and every one "
        "of them reads.",
        "",
        "Find what this system holds about this person:",
    ]
    if person["name"]:
        lines.append(f"  Name: {person['name']}")
    if person["email"]:
        lines.append(f"  Email: {person['email']}")
    if person["domain"]:
        lines.append(f"  Company domain: {person['domain']}")
    if connector.get("about"):
        lines += ["", f"What this system is: {connector['about'][:200]}"]
    lines += [
        "",
        "Look for their own record first, by the email address when there is "
        "one and otherwise by the full name. Then read what the system keeps "
        "attached to that record: their company, any open deal or "
        "opportunity, and the most recent activity or note.",
        "",
        f"At most {MAX_CALLS} tool calls in total. Never repeat a call. Search "
        "for this person only, and never list or export everything.",
        "When you have finished, reply with the single word DONE.",
        "If the tools are not available to you, reply with the single word "
        "UNAVAILABLE and stop. Never invent a result.",
    ]
    return "\n".join(lines)


def _strings(value) -> list:
    """Every string inside a tool call's arguments, longest first."""
    found = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for inner in value.values():
            found += _strings(inner)
    elif isinstance(value, list):
        for inner in value:
            found += _strings(inner)
    return sorted({f for f in found if len(f.strip()) > 2}, key=len, reverse=True)


# Where a tool repeats what it was asked. In a structured reply that is a
# field named for the question; in a written one it is a line that talks about
# the search rather than giving a record.
_ECHO_KEYS = {"query", "q", "search", "search_term", "searchterm", "term",
              "terms", "filter", "filters", "input", "request", "params",
              "parameters", "arguments", "args", "criteria", "keyword",
              "keywords"}
_ECHO_CUE = re.compile(
    r"\b(match(?:ing|es|ed)?|results?|quer(?:y|ies)|search(?:ed|ing)?|found|"
    r"showing|filter(?:ed)?)\b", re.IGNORECASE)


def _drop_echo_keys(value):
    if isinstance(value, dict):
        return {k: _drop_echo_keys(v) for k, v in value.items()
                if str(k).lower() not in _ECHO_KEYS}
    if isinstance(value, list):
        return [_drop_echo_keys(v) for v in value]
    return value


def without_echo(text: str, args) -> str:
    """A result with the tool's repetition of its own question taken out.

    A search tool repeats its query in its answer ("Found 20 results matching
    ..."), so a search for someone the system has never heard of comes back
    naming them. Measured on the first live lookup: an invented person was
    "found" three times over on that header alone.

    Only the repetition goes, never every occurrence of the argument: a
    search by address that returns the record with that address is the
    commonest true answer there is. So a structured reply loses the fields
    named for the question, and a written one loses the lines that both
    repeat an argument and talk about the search.
    """
    text = text or ""
    asked = [" ".join(a.lower().split()) for a in _strings(args)]
    if not asked:
        return text
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, (dict, list)):
        return json.dumps(_drop_echo_keys(data))
    kept = []
    for line in text.splitlines():
        low = " ".join(line.lower().split())
        if _ECHO_CUE.search(line) and any(a in low for a in asked):
            continue
        kept.append(line)
    return "\n".join(kept)


def names_person(text: str, person: dict) -> bool:
    """Whether a result is about this person, by their address or full name."""
    low = (text or "").lower()
    if person["email"] and person["email"] in low:
        return True
    parts = [p for p in re.findall(r"[a-z0-9']+", person["name"].lower())
             if len(p) > 1]
    if len(parts) < 2:
        return False                    # a first name alone names nobody
    return parts[0] in low and parts[-1] in low


def _excerpt(text: str, person: dict) -> str:
    """The part of a result that is about the person, at a readable size."""
    text = text or ""
    if len(text) <= RESULT_CHARS:
        return text
    low = text.lower()
    marks = [low.find(m) for m in (person["email"],
                                   person["name"].lower().split(" ")[-1])
             if m and low.find(m) >= 0]
    start = max(0, (min(marks) if marks else 0) - LEAD_CHARS)
    cut = text[start:start + RESULT_CHARS]
    return ("..." if start else "") + cut + ("..." if start + RESULT_CHARS < len(text) else "")


def _verdict(parsed: dict, connector: dict, person: dict) -> dict:
    """What happened, decided from the event stream and nothing else."""
    server = connector["server"]
    for result in parsed["results"]:
        if result["is_error"] and cf._text_says_needs_auth(cf._as_text(result["content"])):
            return {"status": cf.NEEDS_AUTH, "reason":
                    f"{connector['name']} needs signing in again, in Claude's "
                    "connector settings"}
    if not parsed["saw_init"]:
        return {"status": cf.ERROR, "reason": "the session did not start"}
    status = cf._server_status(parsed, server)
    if not status:
        return {"status": cf.ABSENT, "reason":
                f"{connector['name']} is not connected on this machine"}
    if status == "needs-auth":
        return {"status": cf.NEEDS_AUTH, "reason":
                f"{connector['name']} needs signing in, in Claude's connector "
                "settings"}
    if status != "connected" and not parsed["uses"]:
        return {"status": cf.ERROR, "retryable": True, "reason":
                f"{connector['name']} was still connecting"}

    allowed = {f"mcp__{server}__{tool}".lower() for tool in connector["tools"]}
    by_id = {r["id"]: r for r in parsed["results"]}
    findings, answered, failed = [], 0, 0
    for use in [u for u in parsed["uses"] if u["name"].lower() in allowed][:MAX_CALLS]:
        result = by_id.get(use["id"])
        if result is None:
            continue
        if result["is_error"]:
            failed += 1
            continue
        answered += 1
        text = cf._as_text(result["content"])
        if not names_person(without_echo(text, use["input"]), person):
            continue
        findings.append({"tool": tool_key(use["name"]), "args": use["input"],
                         "text": _excerpt(text, person)})
    if findings:
        return {"status": cf.OK, "findings": findings, "calls": answered}
    if answered:
        return {"status": NOTHING, "calls": answered, "reason":
                f"{connector['name']} holds nothing under this person's name "
                "or address"}
    if failed:
        return {"status": cf.ERROR, "reason":
                f"{connector['name']} refused every question it was asked"}
    return {"status": cf.ERROR, "reason":
            f"{connector['name']} was not asked anything: the session "
            "returned without looking"}


def lookup(connector: dict, person: dict, claude: str | None = None,
           model: str = HAIKU_MODEL, timeout_s: int = LOOKUP_TIMEOUT_S,
           wait_s: int = cf.RECONNECT_WAIT_S, attempts: int = 2) -> dict:
    """Ask one connector about one person. Never raises.

    Returns `name`, `server`, `status` (ok, nothing, needs-auth, absent, error),
    `reason` when not ok, and `findings`: `[{"tool", "args", "text"}]`.
    """
    person = clean_person(person)
    base = {"name": connector["name"], "server": connector["server"],
            "findings": [], "reason": "", "calls": 0}
    usable, _ = split_tools(connector.get("tools"))
    if not usable:
        return {**base, "status": cf.ERROR,
                "reason": f"{connector['name']} has no tool whose name says it only reads"}
    if not person["email"] and len(person["name"].split()) < 2:
        return {**base, "status": SKIPPED,
                "reason": "no address and no full name to ask about"}
    connector = {**connector, "tools": usable}
    prompt = build_prompt(connector, person)
    verdict: dict = {}
    for attempt in range(1, attempts + 1):
        try:
            proc = cf._spawn(connector["server"], tuple(usable), prompt, model,
                             timeout_s)
        except subprocess.TimeoutExpired:
            return {**base, "status": cf.ERROR,
                    "reason": f"{connector['name']} did not answer in time"}
        except OSError as exc:
            return {**base, "status": cf.ERROR,
                    "reason": f"the lookup could not start ({type(exc).__name__})"}
        parsed = cf._parse_stream(proc.stdout or "")
        # Persisted pointers are followed before cleanup removes their files.
        for item in parsed.get("results") or []:
            item["content"] = cf._as_text(item["content"])
        verdict = _verdict(parsed, connector, person)
        cf._delete_transcript(parsed.get("session_id") or "")
        if not verdict.get("retryable"):
            break
        if attempt < attempts:
            time.sleep(wait_s)
    verdict.pop("retryable", None)
    return {**base, **verdict}


# ── Many people, once a day ──────────────────────────────────────────────────

def person_key(person: dict) -> str:
    person = clean_person(person)
    return person["email"] or person["name"].lower()


def _cache_dir() -> Path:
    return config_loader.logs_dir() / "connector_lookup"


def _cache_path(day: str) -> Path:
    return _cache_dir() / f"{day}.json"


def _read_cache(day: str) -> dict:
    try:
        data = json.loads(_cache_path(day).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_cache(day: str, rows: dict) -> None:
    try:
        folder = _cache_dir()
        folder.mkdir(parents=True, exist_ok=True)
        _cache_path(day).write_text(json.dumps(rows, indent=1), encoding="utf-8")
        keep = {(datetime.fromisoformat(day) - timedelta(days=n)).date().isoformat()
                for n in range(CACHE_DAYS)}
        for old in folder.glob("*.json"):
            if old.stem not in keep:
                old.unlink()
    except (OSError, ValueError):
        pass                            # a cache never fails a lookup


def _cache_id(connector: dict, key: str) -> str:
    raw = f"{connector['server']}|{','.join(sorted(connector['tools']))}|{key}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def lookup_people(people: list, use: str = PEOPLE, claude: str | None = None,
                  now: datetime | None = None, budget_s: int = BUDGET_S,
                  cap: int = PEOPLE_CAP) -> dict:
    """Ask every configured connector about each person. Never raises.

    Returns `{person_key: [result, ...]}`, one result per connector. An answer
    (found or nothing) is kept for the day, so the prep email and the briefing
    that follows it pay for one lookup between them; a failure is not kept, so
    a connector that comes back is asked again.
    """
    try:
        connectors = configured(use)
    except Exception:                                           # noqa: BLE001
        return {}
    if not connectors:
        return {}
    try:
        claude_update.ensure_current(claude)
    except Exception:                                           # noqa: BLE001
        pass
    day = (now or datetime.now()).date().isoformat()
    cache = _read_cache(day)
    started = time.time()
    out: dict = {}
    seen: list = []
    for person in people or []:
        key = person_key(person)
        if not key or key in seen:
            continue
        seen.append(key)
        rows = []
        for connector in connectors:
            base = {"name": connector["name"], "server": connector["server"],
                    "findings": [], "calls": 0}
            cid = _cache_id(connector, key)
            if cid in cache:
                rows.append(cache[cid])
                continue
            if len(seen) > cap:
                rows.append({**base, "status": SKIPPED, "reason":
                             f"only the first {cap} people are looked up"})
                continue
            if time.time() - started > budget_s:
                rows.append({**base, "status": SKIPPED,
                             "reason": "the lookups ran out of time"})
                continue
            try:
                row = lookup(connector, person, claude=claude)
            except Exception as exc:                            # noqa: BLE001
                row = {**base, "status": cf.ERROR, "reason":
                       f"the lookup failed ({type(exc).__name__})"}
            if row["status"] in (cf.OK, NOTHING):
                cache[cid] = row
                _write_cache(day, cache)
            rows.append(row)
        out[key] = rows
    return out


# ── Setting one up ───────────────────────────────────────────────────────────

def _config_path() -> Path:
    return config_loader.van_gogh_root() / "config.json"


def _write_config(mutate) -> dict:
    """Apply one change to config.json, backing the old file up first."""
    path = _config_path()
    config = json.loads(path.read_text(encoding="utf-8"))
    entries = [e for e in config.get("connectors") or [] if isinstance(e, dict)]
    entries = mutate(entries)
    config["connectors"] = entries
    path.with_suffix(".json.bak").write_text(
        path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return config


def add(name: str, server: str, tools: list, about: str = "",
        use_for: list | None = None, claude: str | None = None,
        offered: list | None = None) -> dict:
    """Save a connector, after checking it against the connector itself.

    Refuses a tool the connector does not offer and a tool whose name does not
    read. `offered` is the connector's own tool list; left out, it is asked.
    """
    name = (name or "").strip().lower()
    if not _SLUG_OK.match(name):
        return {"ok": False, "why": "a connector needs a short name made of "
                                    "lowercase letters, digits and hyphens"}
    key = server_key(server)
    if not key:
        return {"ok": False, "why": "no connector was named"}
    if offered is None:
        listing = cf.tools_of(key, claude=claude)
        if not listing.get("ok"):
            return {"ok": False, "why": listing.get("reason") or
                    "the connector did not list its tools"}
        offered = listing["tools"]
    offered_keys = {tool_key(t).lower() for t in offered}
    wanted = [tool_key(t) for t in tools or [] if tool_key(t)]
    missing = [t for t in wanted if t.lower() not in offered_keys]
    if missing:
        return {"ok": False, "why": "this connector does not offer: "
                                    + ", ".join(missing)}
    usable, refused = split_tools(wanted)
    if refused:
        return {"ok": False, "refused": refused, "why":
                "these tools look like they change things, and only tools "
                "that read may be used: " + ", ".join(refused)}
    if not usable:
        return {"ok": False, "why": "no tool was chosen"}
    entry = {"name": name, "server": key, "tools": usable,
             "about": " ".join((about or "").split())[:200],
             "use_for": [u for u in (use_for or [PEOPLE]) if u]}
    try:
        _write_config(lambda rows: [r for r in rows if r.get("name") != name] + [entry])
    except (OSError, ValueError) as exc:
        return {"ok": False, "why": f"the settings could not be saved "
                                    f"({type(exc).__name__})"}
    return {"ok": True, "connector": entry}


def remove(name: str) -> dict:
    name = (name or "").strip().lower()
    found = []

    def mutate(rows):
        found.extend(r for r in rows if r.get("name") == name)
        return [r for r in rows if r.get("name") != name]

    try:
        _write_config(mutate)
    except (OSError, ValueError) as exc:
        return {"ok": False, "why": f"the settings could not be saved "
                                    f"({type(exc).__name__})"}
    return {"ok": bool(found), "why": "" if found else
            f"no connector is saved under the name {name}"}


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Connectors any capability can ask.")
    parser.add_argument("--list", action="store_true",
                        help="the connectors saved in settings")
    parser.add_argument("--add", metavar="NAME")
    parser.add_argument("--server")
    parser.add_argument("--tools", default="", help="comma separated tool names")
    parser.add_argument("--about", default="")
    parser.add_argument("--use", default=PEOPLE, help="comma separated uses")
    parser.add_argument("--remove", metavar="NAME")
    parser.add_argument("--test", metavar="NAME",
                        help="ask a saved connector about one person, for real")
    parser.add_argument("--name", default="")
    parser.add_argument("--email", default="")
    parser.add_argument("--claude", default=None)
    args = parser.parse_args(argv)

    if args.add:
        out = add(args.add, args.server or "",
                  [t for t in args.tools.split(",") if t.strip()],
                  about=args.about,
                  use_for=[u.strip() for u in args.use.split(",") if u.strip()],
                  claude=args.claude)
    elif args.remove:
        out = remove(args.remove)
    elif args.test:
        match = [c for c in config_loader.connectors() if c["name"] == args.test.lower()]
        if not match:
            out = {"ok": False, "why": f"no connector is saved under the name {args.test}"}
        else:
            row = lookup(match[0], {"name": args.name, "email": args.email},
                         claude=args.claude)
            out = {"ok": row["status"] in (cf.OK, NOTHING), **row}
    else:
        rows = []
        for entry in config_loader.connectors():
            usable, refused = split_tools(entry["tools"])
            rows.append({**entry, "tools": usable, "refused": refused})
        out = {"ok": True, "connectors": rows}
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
