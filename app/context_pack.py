#!/usr/bin/env python3
"""The evidence a draft is written from.

A reply written off a single mail thread can only ever restate that thread.
The vault already knows more: who this counterparty is, what was said on the
last three calls with them, what was promised and never closed, and which
other notes sit one hop away in the graph. This module gathers that into one
pack so the drafter argues from the record instead of from the last message.

Two rules shape it:

* **Every fact carries its source path.** A pack entry the drafter can't
  attribute is a pack entry that can't be checked, and an unattributable claim
  in an email to a counterparty is the expensive kind of wrong.
* **An empty pack is a real answer.** `pack_for` returning empty means the vault
  knows nothing about this person; the caller falls back to thread-only rather
  than letting a model fill the silence. `is_empty()` says so explicitly.

The retrieval functions here were extracted from meeting_prep.py, which still
imports them. They resolve config inside the call rather than at import, so a
test (or a vault switch) can re-point them without reloading the module.
"""

from __future__ import annotations

import re
from pathlib import Path

import brain_graph
from config_loader import (
    entities_dir,
    hotcache_path,
    sources_dir,
    user_first_name,
    user_name_variants,
)

# A note linked from hundreds of others (a business page, a hub index) says
# nothing specific about any one counterparty. Past this degree a neighbour is
# noise, and including it would crowd out the notes that do carry a fact.
HUB_DEGREE = 40

# How many one-hop neighbours reach the pack, most-connected first.
NEIGHBOR_LIMIT = 12

# The vault stamps machine state into HTML comments, in several families:
# `<!-- ai: dir=by_me cb=cb-1234 -->` on action items, plus `synth:`, `ae:`,
# `deal:` and `tasks:` elsewhere. None of it is prose. Stripping is generic
# rather than a list of known prefixes, because enumerating them means the
# next family leaks. That is exactly how `deal:` reached a live pack after
# `ai:` was handled.
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# Ownership is still read from the `ai:` family specifically, since it is the
# only one that carries a `dir=` field.
_AI_MARKER_RE = re.compile(r"<!--\s*ai:\s*(?P<fields>.*?)-->", re.DOTALL)
_DIR_RE = re.compile(r"\bdir=(?P<dir>[a-z_]+)")

# The owner values the marker uses. `by_me` is the user's own promise, which is
# the only class that belongs in a reply as something they still owe.
OWNER_SELF = "by_me"


def _name_tokens(name: str) -> list:
    """Tokens worth matching on. Short tokens collide across unrelated names."""
    return [t for t in (name or "").split() if len(t) > 2]


# ── Entity page ───────────────────────────────────────────────────────────────

def find_entity_page(name):
    entities = entities_dir()
    if not entities.exists():
        return None
    tokens = [t.lower() for t in _name_tokens(name)]
    if not tokens:
        return None

    best, best_score = None, 0
    for f in entities.iterdir():
        if f.suffix != ".md":
            continue
        fname = f.stem.lower()
        score = sum(1 for t in tokens if t in fname)
        if score > best_score:
            best_score, best = score, f

    if best_score >= min(2, len(tokens)):
        return str(best)
    # Fallback: last name only, requiring >4 chars to avoid common-word collisions
    if tokens:
        last = tokens[-1]
        if len(last) > 4:
            for f in entities.iterdir():
                if last in f.stem.lower():
                    return str(f)
    return None


# ── Granola source search ─────────────────────────────────────────────────────

def find_meeting_sources(name, limit=3):
    sources = sources_dir()
    if not sources.exists():
        return []
    tokens = _name_tokens(name)
    if not tokens:
        return []
    # Priority 1: name tokens in filename (meeting IS with this person)
    # Priority 2: all tokens in full text (person mentioned, lower confidence)
    filename_matches, fulltext_matches = [], []
    for f in sources.iterdir():
        if f.suffix != ".md":
            continue
        fname_lower = f.stem.lower()
        if all(t.lower() in fname_lower for t in tokens):
            filename_matches.append(f)
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            if all(t.lower() in text.lower() for t in tokens):
                fulltext_matches.append(f)
        except Exception:
            continue
    filename_matches.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    fulltext_matches.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    combined = filename_matches[:limit]
    remaining = limit - len(combined)
    if remaining > 0:
        combined += fulltext_matches[:remaining]
    return [str(f) for f in combined]


def extract_action_items(source_path):
    """Extract unresolved action items owned by the user from a source page."""
    try:
        text = Path(source_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    variants = [v.lower() for v in user_name_variants()]
    first = user_first_name().lower()

    items = []
    in_section = False
    for line in text.splitlines():
        if re.match(r"^#{1,3}\s+(Action Items|Next Steps|Follow[- ]?ups?)", line, re.IGNORECASE):
            in_section = True
            continue
        if in_section and re.match(r"^#{1,3}\s+", line):
            in_section = False
        if in_section:
            # Unresolved bullet (not [x]) that mentions the user
            line_lower = line.lower()
            owned_by_user = any(v in line_lower for v in variants) or (first and first in line_lower)
            if re.match(r"^\s*-\s+(?!\[x\])", line, re.IGNORECASE) and owned_by_user:
                clean = re.sub(r"^\s*-\s+(\[ \]\s*)?", "", line).strip()
                clean = strip_markers(clean)
                if clean:
                    items.append(clean)
    return items


def strip_markers(text):
    """Remove the vault's HTML-comment bookkeeping from any vault text.

    Every string that enters a pack goes through here. These markers are
    machine state, and a drafter quoting one would put `cb=cb-1234` in front
    of a counterparty. Blank lines left behind by a stripped marker collapse
    too, so a snippet does not arrive full of holes.
    """
    cleaned = _COMMENT_RE.sub("", text or "")
    lines = [ln.rstrip() for ln in cleaned.splitlines()]
    return "\n".join(ln for ln in lines if ln.strip()).strip()


def action_item_owner(line):
    """Who owns this action item, per the vault's `dir=` marker.

    Returns `by_me`, `to_me`, `third_party`, or "" when the line carries no
    marker. A commitment the user did not make is not theirs to apologise for,
    so a drafter must be able to tell them apart.
    """
    match = _AI_MARKER_RE.search(line or "")
    if not match:
        return ""
    found = _DIR_RE.search(match.group("fields"))
    return found.group("dir") if found else ""


# ── Hotcache ──────────────────────────────────────────────────────────────────

def extract_hotcache_snippets(names):
    hotcache = hotcache_path()
    if not hotcache.exists():
        return []
    text = hotcache.read_text(encoding="utf-8", errors="ignore")
    if not names:
        return []
    # Split on ### headers; require ALL tokens of at least one attendee's name
    # to appear in the section (prevents single-token collisions on a bare
    # first name)
    sections = re.split(r"(?=^###\s)", text, flags=re.MULTILINE)
    result = []
    for s in sections:
        if not s.strip():
            continue
        s_lower = s.lower()
        for name in names:
            name_tokens = _name_tokens(name)
            if name_tokens and all(t.lower() in s_lower for t in name_tokens):
                result.append(s.strip())
                break
    return result


# ── Graph neighbourhood ───────────────────────────────────────────────────────

def graph_neighborhood(name, limit=NEIGHBOR_LIMIT, graph=None):
    """Notes one hop from this counterparty's own note in the vault graph.

    Answers "what else does the vault attach to this person": the adjacent
    deal, the org page, the meeting that mentioned them alongside someone else.
    Hub notes are dropped: a page everything links to distinguishes nothing.

    Returns `{"node": <rel path or None>, "neighbors": [{id, label, direction}]}`.
    """
    tokens = _name_tokens(name)
    if not tokens:
        return {"node": None, "neighbors": []}

    g = brain_graph.cached() if graph is None else graph
    hits = brain_graph.search(" ".join(tokens), graph=g)
    if not hits:
        return {"node": None, "neighbors": []}

    node_id = hits[0]
    degree = {n["id"]: n.get("degree", 0) for n in g.get("nodes", [])}
    labels = {n["id"]: n.get("label", "") for n in g.get("nodes", [])}

    linked = brain_graph.neighbors(node_id, graph=g)
    seen, out = set(), []
    for direction in ("outbound", "inbound"):
        for nid in linked.get(direction, []):
            if nid in seen or degree.get(nid, 0) > HUB_DEGREE:
                continue
            seen.add(nid)
            out.append({"id": nid, "label": labels.get(nid, nid), "direction": direction})

    # Most-connected first: a note with several links is a note with a story.
    out.sort(key=lambda n: (-degree.get(n["id"], 0), n["id"]))
    return {"node": node_id, "neighbors": out[:limit]}


def _owners_by_text(source_path):
    """Cleaned action-item text -> its `dir=` owner, for one source file.

    Keyed on the cleaned text because that is what `extract_action_items`
    returns; the marker itself only survives on the raw line.
    """
    try:
        text = Path(source_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}
    owners = {}
    for line in text.splitlines():
        owner = action_item_owner(line)
        if not owner:
            continue
        clean = re.sub(r"^\s*-\s+(\[[ xX]\]\s*)?", "", line).strip()
        clean = strip_markers(clean)
        if clean:
            owners[clean] = owner
    return owners


# ── The pack ──────────────────────────────────────────────────────────────────

def pack_for(counterparty_name, counterparty_email="", meeting_limit=3):
    """Everything the vault knows about one counterparty, each fact sourced.

    Sections are separate rather than flattened so a caller can cite them
    individually, and so `is_empty` can tell "nothing known" from "known but
    quiet". `sources` lists every path the pack drew on, for attribution.
    """
    name = (counterparty_name or "").strip()
    email = (counterparty_email or "").strip()
    pack = {
        "counterparty_name": name,
        "counterparty_email": email,
        "entity_page": None,
        "meetings": [],
        "open_commitments": [],
        "deal_context": [],
        "graph": {"node": None, "neighbors": []},
        "sources": [],
    }
    if not name:
        return pack

    entity = find_entity_page(name)
    if entity:
        pack["entity_page"] = entity
        pack["sources"].append(entity)

    for path in find_meeting_sources(name, limit=meeting_limit):
        items = extract_action_items(path)
        owners = _owners_by_text(path)
        pack["meetings"].append({"path": path, "action_items": items})
        pack["sources"].append(path)
        # Every commitment keeps the meeting it was made on: an open item with
        # no provenance can't be raised in a reply without risking inventing it.
        # Only the user's own promises reach `open_commitments`. Raising
        # someone else's as a thing "we still owe you" is a confident, wrong,
        # and expensive email.
        for item in items:
            # An unmarked item keeps the benefit of the doubt: the marker is a
            # recent convention and older notes predate it, so filtering on its
            # absence would silently drop real commitments. Only an item
            # explicitly owned by someone else is excluded.
            if owners.get(item, OWNER_SELF) == OWNER_SELF:
                pack["open_commitments"].append({"item": item, "source": path})

    snippets = extract_hotcache_snippets([name])
    if snippets:
        hotcache = str(hotcache_path())
        pack["deal_context"] = [{"text": strip_markers(s), "source": hotcache}
                                for s in snippets]
        pack["sources"].append(hotcache)

    pack["graph"] = graph_neighborhood(name)

    return pack


def is_empty(pack) -> bool:
    """True when the vault knows nothing usable about this counterparty.

    The caller's contract: an empty pack must not be handed to a drafter as
    evidence. Draft from the thread alone and say the pack was empty.
    """
    if not pack:
        return True
    return not (
        pack.get("entity_page")
        or pack.get("meetings")
        or pack.get("open_commitments")
        or pack.get("deal_context")
        or pack.get("graph", {}).get("neighbors")
    )


def main(argv=None) -> int:
    """Print one counterparty's pack as JSON, for a skill to read before drafting.

    Exit code is 0 either way: an empty pack is a valid answer, not a failure.
    The caller branches on `is_empty` in the payload, never on the exit code.
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Gather what the vault knows about one counterparty.")
    parser.add_argument("--name", required=True, help="counterparty display name")
    parser.add_argument("--email", default="", help="counterparty email, if known")
    parser.add_argument("--meetings", type=int, default=3,
                        help="how many recent meeting notes to read")
    args = parser.parse_args(argv)

    pack = pack_for(args.name, args.email, meeting_limit=args.meetings)
    pack["is_empty"] = is_empty(pack)
    print(json.dumps(pack, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
