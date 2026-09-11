#!/usr/bin/env python3
"""
Notetaker → Second Brain Wiki: end-to-end ingest

Fetches a meeting summary (NOT the transcript) from whichever notetaker is
active — Granola, Grain, ... — and writes a schema-compliant source page into
the wiki, applying jargon corrections, extracting action items, wikilinking
known entities, and updating index.md / log.md.

Which service the note comes from is app/notetaker.py's business: this file
reads the canonical note shape and never talks to a vendor API directly.

The transcript is intentionally NOT fetched. It's huge and almost always
noise. The source page stores `meeting_id:` and `meeting_source:` in
frontmatter so the transcript can be retrieved on demand later via
meeting_fetch.py.

USAGE (<tag> is one of your configured businesses[].tag):
    # Preview what would be written (no business tag required)
    python app/meeting_ingest.py --meeting latest --dry-run

    # Write to disk (business tag required)
    python app/meeting_ingest.py --meeting latest --apply --business <tag>

    # List all recent uningested meetings
    python app/meeting_ingest.py --meeting uningested --dry-run

    # Ingest all recent uningested meetings with a single business tag
    python app/meeting_ingest.py --meeting uningested --apply --business <tag>

    # Control how many recent meetings to scan (default: 20)
    python app/meeting_ingest.py --meeting uningested --dry-run --limit 50

    # For a sales call instead of an internal meeting
    python app/meeting_ingest.py --meeting <id> --apply --business <tag> --type sales-call

    # Read from a specific notetaker instead of the configured one
    python app/meeting_ingest.py --meeting latest --dry-run --source grain

    # Auto-create stub entity pages for new attendees (default: off — too aggressive)
    python app/meeting_ingest.py --meeting <id> --apply --business <tag> --stub-attendees

    # Force overwrite of an existing source page
    python app/meeting_ingest.py --meeting <id> --apply --business <tag> --force

EXIT CODES (in addition to meeting_fetch.py's):
    10  --business required for --apply (single-meeting mode)
    11  Invalid --business value
    12  Invalid --type value
    13  Source page already exists; pass --force to overwrite
    14  Unknown --source (no such notetaker provider)
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import notetaker
import user_state
from notetaker import NotetakerError

# ── Config ────────────────────────────────────────────────────────────────────
from config_loader import (
    vault as _vault,
    business_tags,
    force_utf8_io,
    hotcache_action_items_heading,
    user_first_name,
    user_name_variants,
    user_self_entities,
)
WIKI = _vault()
SOURCES = WIKI / "wiki" / "sources"
ENTITIES = WIKI / "wiki" / "entities"
CONCEPTS = WIKI / "wiki" / "concepts"
PROJECTS = WIKI / "wiki" / "projects"
INDEX = WIKI / "wiki" / "index.md"
LOG = WIKI / "wiki" / "log.md"
WIKI_CLAUDE_MD = WIKI / "CLAUDE.md"
HOTCACHE = WIKI / "wiki" / "hotcache.md"

_HOTCACHE_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "call", "meeting",
    "sync", "update", "catch", "catchup", "check", "intro", "discussion",
    "recap", "review", "follow", "internal", "weekly", "daily",
    user_first_name().lower(),
}
OBSIDIAN_WEEKLY_DIR = WIKI / "wiki" / "weekly"

# Stage-change keyword detection for hotcache deal comments. Each keyword maps
# to a pipeline stage; _STAGE_RANK orders them so a meeting only ever advances a
# deal forward (the no-downgrade guard) — mentioning an earlier stage never
# walks a deal back. Word boundaries keep "deadline" from tripping "dead".
_STAGE_KEYWORDS = [
    (r"\bloi\b", "loi"),
    (r"\bexclusivity\b", "exclusivity"),
    (r"\bsigned\b", "closing"),
    (r"\bclosed\b", "closed"),
    (r"\bpassed\b", "passed"),
    (r"\bdead\b", "dead"),
]
_STAGE_RANK = {
    "outreach": 1, "loi": 2, "exclusivity": 3,
    "closing": 4, "closed": 5, "passed": 6, "dead": 6,
}


def detect_stage_change(text):
    """Highest-ranked pipeline stage implied by keywords in `text`, or None."""
    if not text:
        return None
    low = text.lower()
    best = None
    for pat, stage in _STAGE_KEYWORDS:
        if re.search(pat, low) and _STAGE_RANK.get(stage, 0) > _STAGE_RANK.get(best, 0):
            best = stage
    return best

# Named exit codes (values are stable; callers may branch on them).
# 2-8 mirror meeting_fetch.py's so a skill can branch on either script's exit.
EXIT_NO_API_KEY = 2
EXIT_REQUEST_FAILED = 3
EXIT_UNAUTHORIZED = 4
EXIT_NOT_FOUND = 5
EXIT_RATE_LIMITED = 6
EXIT_HTTP_ERROR = 7
EXIT_NO_NOTES = 8
EXIT_MISSING_BUSINESS = 10
EXIT_BAD_BUSINESS = 11
EXIT_BAD_TYPE = 12
EXIT_PAGE_EXISTS = 13
EXIT_UNKNOWN_SOURCE = 14

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
user_state.load_env()

# Which provider this run reads from. Set once from --source in main(); None
# means "whatever config says". Module-level because the ingest pipeline is a
# long call chain and threading a provider argument through every frame would
# buy nothing — one run only ever talks to one notetaker.
SOURCE = None

# Share-link footers, per notetaker. Granola writes the link into the summary
# body and exposes no field for it; Grain returns one on the recording, which
# run_ingest prefers. Keep the fallback regex for both so a summary that carries
# a link still yields one when the field is absent.
SHARE_URL_RE = re.compile(
    r"\[(https://(?:notes\.granola\.ai/t|grain\.com/(?:share|recordings))/[A-Za-z0-9_-]+)\]"
)

VALID_BUSINESS = business_tags()
VALID_TYPE = {"meeting", "sales-call"}


# ── Notetaker ─────────────────────────────────────────────────────────────────
def _provider():
    try:
        return notetaker.provider(SOURCE)
    except NotetakerError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        sys.exit(EXIT_UNKNOWN_SOURCE)


def _die(err):
    """Map a NotetakerError onto this script's stable exit codes."""
    text = str(err)
    if "is not set" in text:
        sys.stderr.write(f"ERROR: {text}\nSet it in ~/.config/van-gogh/.env\n")
        sys.exit(EXIT_NO_API_KEY)
    for marker, code in (("401", EXIT_UNAUTHORIZED), ("403", EXIT_UNAUTHORIZED),
                         ("404", EXIT_NOT_FOUND), ("429", EXIT_RATE_LIMITED)):
        if marker in text:
            sys.stderr.write(f"ERROR: {text}\n")
            sys.exit(code)
    sys.stderr.write(f"ERROR: {text}\n")
    sys.exit(EXIT_REQUEST_FAILED if "HTTP error" in text else EXIT_HTTP_ERROR)


def fetch_meeting(note_id):
    """Fetch the note WITHOUT its transcript. We only want the summary."""
    mod = _provider()
    try:
        if note_id == "latest":
            note_id = mod.latest_id()
            if not note_id:
                sys.stderr.write(f"ERROR: no recent meetings found in {mod.DISPLAY_NAME}\n")
                sys.exit(EXIT_NO_NOTES)
        return mod.fetch_note(note_id)
    except NotetakerError as e:
        _die(e)


# Pages written before the notetaker layer landed carry `granola_id:`; pages
# written since carry `meeting_id:`. Both are read, forever — a vault full of
# already-filed meetings must never look uningested and get filed twice.
MEETING_ID_RE = re.compile(r"^(?:meeting_id|granola_id):\s*(\S+)", re.MULTILINE)


def get_ingested_meeting_ids():
    """Every meeting id already present in wiki/sources/ frontmatter."""
    ids = set()
    if not SOURCES.exists():
        return ids
    for f in SOURCES.glob("*.md"):
        try:
            m = MEETING_ID_RE.search(f.read_text(encoding="utf-8"))
            if m:
                ids.add(m.group(1).strip())
        except Exception:
            pass
    return ids


def fetch_uningested_stubs(limit=20):
    """Meeting stubs (id, title, created_at) not yet filed in wiki/sources/."""
    ingested = get_ingested_meeting_ids()
    try:
        stubs = list(_provider().iter_stubs(limit=limit))
    except NotetakerError as e:
        _die(e)
    return [n for n in stubs if n.get("id") not in ingested]


# PII stripping and date/attendee resolution are shared with every other
# notetaker consumer; see app/notetaker.py.
strip_pii = notetaker.strip_pii


# ── Jargon table ──────────────────────────────────────────────────────────────
def parse_jargon_table():
    """Read wiki/CLAUDE.md and extract the Jargon & Alias Table as {wrong: correct}."""
    if not WIKI_CLAUDE_MD.exists():
        return {}
    text = WIKI_CLAUDE_MD.read_text(encoding="utf-8")
    section_match = re.search(
        r"##\s*Jargon\s*&\s*Alias\s*Table.*?(?=\n##\s|\Z)",
        text,
        re.DOTALL,
    )
    if not section_match:
        return {}
    section = section_match.group(0)
    table = {}
    for line in section.splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        wrong, correct = cells[0], cells[1]
        if wrong.lower().startswith("wrong"):
            continue
        if wrong and correct and wrong != correct:
            table[wrong] = correct
    return table


def apply_jargon(text, table):
    """Word-boundary search/replace for jargon corrections."""
    if not isinstance(text, str):
        return text
    for wrong, correct in table.items():
        pattern = r"\b" + re.escape(wrong) + r"\b"
        text = re.sub(pattern, correct, text)
    return text


# ── Summary parsing ───────────────────────────────────────────────────────────
# Hosts a meeting share link may point at, per notetaker. A link is written
# into vault markdown, so the allowlist is the trust boundary, not the provider.
SHARE_URL_HOSTS = {"notes.granola.ai", "grain.com", "www.grain.com", "api.grain.com"}


def safe_share_url(url):
    """A provider-supplied share link, or None if it is not one we will write.

    Requires https and an allowlisted host, and rejects anything carrying
    whitespace, a newline, or a ")" — the last one because the value is
    interpolated into `[Transcript]({url})` and a bare paren ends the link and
    turns the tail into page content.
    """
    if not url or not isinstance(url, str):
        return None
    if any(c in url for c in ') \t\r\n<>"'):
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.netloc.lower() not in SHARE_URL_HOSTS:
        return None
    return url


def extract_share_url(summary_md):
    if not summary_md:
        return None
    m = SHARE_URL_RE.search(summary_md)
    return m.group(1) if m else None


ACTION_HEADINGS_RE = re.compile(
    r"\n#{2,3}\s*(?:Action Items|Next Steps|Follow[- ]?ups?|To[- ]?Dos?|TODOs?)\s*\n(.*?)(?=\n#{2,3}\s|\Z)",
    re.DOTALL | re.IGNORECASE,
)


def extract_action_items(summary_md):
    """Pull the action-items / next-steps section out of the summary.
    Notetakers use different headings for different meeting styles — match them
    all. Providers whose API returns action items as a separate field (Grain)
    fold them under an "## Action Items" heading so this sees one shape.
    Returns (list_of_items, summary_with_section_removed)."""
    if not summary_md:
        return [], summary_md
    m = ACTION_HEADINGS_RE.search(summary_md)
    if not m:
        return [], summary_md
    section = m.group(1)
    cleaned = (summary_md[: m.start()] + summary_md[m.end():]).rstrip() + "\n"

    # Granola format is often:
    #   -   Roman
    #       -   Draft responses for Q1 and Q2 (...)
    #       -   ... another item ...
    #   -   Ryan
    #       -   Revise Q3 response (...)
    #
    # We flatten "Roman → Draft X" into "**Roman** — Draft X".
    items = []
    current_assignee = None
    verb_hints = (
        " to ", " will ", "draft ", "send ", "update ", "call ", "circle ",
        "revise ", "follow up", "review ", "schedule ", "prepare ", "share ",
        "submit ", "ping ", "email ",
    )
    for raw_line in section.splitlines():
        if not raw_line.strip().startswith("-"):
            continue
        leading = len(raw_line) - len(raw_line.lstrip())
        content = raw_line.strip().lstrip("-").strip()
        if not content:
            continue
        if leading == 0:
            # Top-level: assignee header OR a flat action item
            if len(content) < 40 and not any(v in content.lower() for v in verb_hints):
                current_assignee = content
            else:
                items.append(content)
                current_assignee = None
        else:
            if current_assignee:
                items.append(f"**{current_assignee}** — {content}")
            else:
                items.append(content)
    return items, cleaned


def strip_share_url_footer(summary_md):
    """Remove the 'Chat with meeting transcript:' footer Granola appends to summaries."""
    if not summary_md:
        return summary_md
    pattern = re.compile(
        r"\n*-{3,}\s*\n*\s*Chat with meeting transcript:.*?(?=\n\n|\Z)",
        re.DOTALL,
    )
    return pattern.sub("", summary_md).rstrip() + "\n"


# ── Entity detection ──────────────────────────────────────────────────────────
def _parse_aliases_from_frontmatter(text):
    """Parse a YAML `aliases:` list from a markdown file's frontmatter.
    Handles both inline `aliases: [a, b]` and block-list formats.
    Returns a list of alias strings (canonical name not included)."""
    fm = re.match(r"---\s*\n(.*?)\n---", text, re.DOTALL)
    if not fm:
        return []
    body = fm.group(1)
    inline = re.search(r"^aliases:\s*\[(.*?)\]", body, re.MULTILINE)
    if inline:
        return [s.strip().strip('"').strip("'") for s in inline.group(1).split(",") if s.strip()]
    block = re.search(r"^aliases:\s*\n((?:\s+-\s*.*\n?)+)", body, re.MULTILINE)
    if block:
        return [
            re.sub(r'^[\s\-"\']+|["\'\s]+$', "", line)
            for line in block.group(1).splitlines()
            if line.strip().startswith("-")
        ]
    return []


def list_existing_pages():
    """Return two dicts:
       canonical: {canonical_name: kind} for all entities/concepts/projects
       alias_map: {alias: canonical_name} for any frontmatter aliases (canonical name itself NOT included)
    """
    canonical = {}
    alias_map = {}
    for d, kind in [(ENTITIES, "entity"), (CONCEPTS, "concept"), (PROJECTS, "project")]:
        if not d.exists():
            continue
        for f in d.glob("*.md"):
            name = f.stem
            canonical[name] = kind
            try:
                aliases = _parse_aliases_from_frontmatter(f.read_text(encoding="utf-8"))
            except Exception:
                aliases = []
            for alias in aliases:
                if alias and alias != name:
                    alias_map[alias] = name
    return canonical, alias_map


def detect_entities_in_text(text, canonical, alias_map):
    """Find which existing pages appear in the text (by canonical name OR alias).
    Returns set of (canonical_name, kind)."""
    if not text:
        return set()
    found = set()
    for name, kind in canonical.items():
        pattern = r"\b" + re.escape(name) + r"\b"
        if re.search(pattern, text):
            found.add((name, kind))
    for alias, canonical_name in alias_map.items():
        if canonical_name not in canonical:
            continue
        pattern = r"\b" + re.escape(alias) + r"\b"
        if re.search(pattern, text):
            found.add((canonical_name, canonical[canonical_name]))
    return found


def wikilink_entities_in_text(text, canonical, alias_map):
    """Wrap canonical names and aliases with [[wikilinks]]. Aliases use [[Canonical|alias]] form.
    Longest names first to prevent partial overlaps."""
    if not text:
        return text
    # Build a single ordered list of (search_string, wikilink_form), longest first
    replacements = []
    for name in canonical:
        replacements.append((name, f"[[{name}]]"))
    for alias, canonical_name in alias_map.items():
        if canonical_name in canonical:
            replacements.append((alias, f"[[{canonical_name}|{alias}]]"))
    replacements.sort(key=lambda r: len(r[0]), reverse=True)
    for search, wikilink in replacements:
        # Don't double-wrap if already inside [[ ]]
        pattern = re.compile(r"(?<!\[\[)(?<!\[)\b" + re.escape(search) + r"\b(?!\]\])(?!\|)")
        text = pattern.sub(wikilink, text)
    return text


def detect_attendee_wikilinks(attendees_raw, canonical, alias_map):
    linked = []
    new_candidates = []
    for name in attendees_raw:
        name = name.strip()
        if not name:
            continue
        if name in canonical:
            linked.append(f"[[{name}]]")
        elif name in alias_map:
            linked.append(f"[[{alias_map[name]}|{name}]]")
        else:
            linked.append(name)
            new_candidates.append(name)
    return linked, new_candidates


# High-confidence signals in the meeting title → sales-call
SALES_CALL_TITLE_SIGNALS = [
    "demo", "sales call", "discovery call", "intro call", "introductory call",
    "pitch", "product walkthrough", "product demo", "onboarding call",
    "customer call", "client call",
]

# Broader signals checked only in the summary body (require more specific phrasing)
SALES_CALL_BODY_SIGNALS = [
    "sales call", "discovery call", "introductory call", "intro call",
    "product demo", "product walkthrough", "onboarding call",
    "we demoed", "i demoed", "ran a demo", "gave a demo",
]


def infer_meeting_type(title, summary_md):
    """Return 'sales-call' if title or summary contains reliable sales-call signals.

    Title signals are checked first (high confidence — e.g. "Demo with X").
    Body signals use stricter phrases to avoid false positives from words like
    'prospect' appearing in M&A or BD meeting notes.
    """
    title_lower = title.lower()
    for signal in SALES_CALL_TITLE_SIGNALS:
        if signal in title_lower:
            return "sales-call"
    body_lower = (summary_md or "").lower()
    for signal in SALES_CALL_BODY_SIGNALS:
        if signal in body_lower:
            return "sales-call"
    return "meeting"


def _business_tag_from_entity(name, kind):
    """Return the business tag from a single entity page, or None."""
    d = {"entity": ENTITIES, "concept": CONCEPTS, "project": PROJECTS}[kind]
    path = d / f"{name}.md"
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        m = re.search(r"^business:\s*(\S+)", text, re.MULTILINE)
        return m.group(1).strip() if m else None
    except Exception:
        return None


def infer_business_tag(detected_entities, title_entities=None):
    """Read frontmatter `business:` from detected entities and pick the most common.

    Strategy:
    1. If entities detected directly in the meeting title produce a unanimous or
       majority vote among themselves, use that result (title = primary context signal).
    2. Otherwise fall back to a weighted full vote where title-detected entities
       each count 3x versus summary-body entities.

    This ensures e.g. "<User> / <Counterparty> Sync" votes by the counterparty's
    business even when other entities are mentioned throughout the summary body.
    """
    if not detected_entities:
        return None

    title_names = {name for name, _ in (title_entities or set())}

    # Pass 1: vote only from title entities (excluding the user themselves — host is noise)
    SELF_ENTITIES = user_self_entities()
    title_counts = {}
    for name, kind in (title_entities or set()):
        if name in SELF_ENTITIES:
            continue
        tag = _business_tag_from_entity(name, kind)
        if tag:
            title_counts[tag] = title_counts.get(tag, 0) + 1
    if title_counts:
        return max(title_counts, key=title_counts.get)

    # Pass 2: fall back to weighted full vote
    counts = {}
    for name, kind in detected_entities:
        if name in SELF_ENTITIES:
            continue
        tag = _business_tag_from_entity(name, kind)
        if not tag:
            continue
        weight = 3 if name in title_names else 1
        counts[tag] = counts.get(tag, 0) + weight
    if not counts:
        return None
    return max(counts, key=counts.get)


# ── Note shaping ──────────────────────────────────────────────────────────────
meeting_date_from_note = notetaker.note_date
attendee_names = notetaker.attendee_names


# ── Source page builder ───────────────────────────────────────────────────────
def safe_filename(title):
    return re.sub(r'[/\\:*?"<>|\n\r]', "-", title).strip()


def build_source_page(
    meeting_id,
    meeting_source,
    source_title,
    date,
    attendees_formatted,
    attendees_list,
    share_url,
    business,
    meeting_type,
    summary_body,
    action_items,
    detected_entities,
    today,
):
    attendees_yaml = "[" + ", ".join(f'"{a}"' for a in attendees_list) + "]"
    fm = (
        f"---\n"
        f"type: source\n"
        f"title: {source_title}\n"
        f"meeting_id: {meeting_id}\n"
        f"meeting_source: {meeting_source}\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        f"sources: []\n"
        f"attendees: {attendees_yaml}\n"
        f"business: {business}\n"
        f"tags: [{business}, {meeting_type}]\n"
        f"---"
    )
    parts = [fm, "", f"# {source_title}"]
    parts.append(f"- **Date:** {date}")
    parts.append(f"- **Type:** {meeting_type}")
    parts.append(f"- **Attendees:** {attendees_formatted}")
    if share_url:
        parts.append(f"- **Meeting link:** [Transcript]({share_url})")
    parts.append("")

    parts.append("## Summary")
    parts.append("")
    parts.append(summary_body.strip())
    parts.append("")

    parts.append("## Action Items")
    parts.append("")
    if action_items:
        for item in action_items:
            parts.append(f"- [ ] {item}")
    else:
        parts.append("- [ ] ")
    parts.append("")

    parts.append("## Entities Mentioned")
    parts.append("")
    if detected_entities:
        for name, kind in sorted(detected_entities):
            parts.append(f"- [[{name}]]")
    else:
        parts.append("%% No existing entities detected. %%")
    parts.append("")

    parts.append("## Notes")
    parts.append("")
    parts.append("%% Add ad-hoc observations, contradictions, or open questions here. %%")
    parts.append("")

    return "\n".join(parts)


def build_entity_stub(name, business, source_title, today):
    return (
        f"---\n"
        f"type: entity\n"
        f"title: {name}\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        f"sources: [\"[[{source_title}]]\"]\n"
        f"business: {business}\n"
        f"tags: [{business}, stub]\n"
        f"---\n\n"
        f"# {name}\n"
        f"First seen in [[{source_title}]]. Stub — promote with detail when this entity reappears.\n"
    )


# ── index.md / log.md updates ─────────────────────────────────────────────────
def append_under_heading(text, heading_name, new_line):
    """Append a new line under a `## {heading_name}` section. Idempotent.

    Matches headings that contain heading_name as a substring, so it works with
    plain headings (## Sources) and span-wrapped ones
    (## <span style="...">● Sources</span>).
    """
    if new_line in text:
        return text
    # Match any ## line containing heading_name, followed by its content block
    escaped = re.escape(heading_name)
    pattern = re.compile(
        r"(^##[^\n]*" + escaped + r"[^\n]*\n.*?)(\n##\s)",
        re.MULTILINE | re.DOTALL,
    )
    new_text, count = pattern.subn(
        lambda m: m.group(1).rstrip() + "\n" + new_line + "\n" + m.group(2),
        text,
        count=1,
    )
    if count == 0:
        # Heading is the last section in the file
        pattern2 = re.compile(
            r"(^##[^\n]*" + escaped + r"[^\n]*\n.*)\Z",
            re.MULTILINE | re.DOTALL,
        )
        new_text = pattern2.sub(
            lambda m: m.group(1).rstrip() + "\n" + new_line + "\n",
            text,
        )
    return new_text


def lint_new_index_entry(title, summary, business):
    """Quality check the new index entry. Returns list of issues, or [] if clean."""
    full_line = f"- [[{title}]] — {summary} `#{business}`"
    desc = (summary or "").strip().rstrip(".")
    issues = []
    if len(full_line) < 80:
        issues.append(f"line too short ({len(full_line)} chars)")
    if len(desc) < 30:
        issues.append(f'description too thin ({len(desc)} chars: "{desc[:60]}")')
    if re.match(r'^[\*_`#]', desc):
        issues.append("description starts with markdown formatting (likely ingest truncation bug)")
    return issues


def update_index(source_title, summary_one_liner, business, new_entity_stubs, today):
    if not INDEX.exists():
        return
    text = INDEX.read_text(encoding="utf-8")

    source_line = f"- [[{source_title}]] — {summary_one_liner} `#{business}`"
    text = append_under_heading(text, "Sources", source_line)

    for name in new_entity_stubs:
        line = f"- [[{name}]] — Stub from [[{source_title}]]. `#{business}`"
        text = append_under_heading(text, "Entities", line)

    text = re.sub(
        r"^updated: \d{4}-\d{2}-\d{2}",
        f"updated: {today}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    INDEX.write_text(text, encoding="utf-8")


def _parse_sources_list(fm_body):
    """Extract all source refs from a frontmatter body, regardless of YAML format.

    Handles three formats:
      inline:     sources: ["[[A]]", "[[B]]"]
      block-list: sources:\n  - "[[A]]"\n  - "[[B]]"
      absent:     (returns empty list)

    Returns a list of ref strings like ['"[[A]]"', '"[[B]]"'].
    """
    # Collect all "[[...]]" tokens that appear anywhere after a `sources:` key.
    # First narrow to the sources block to avoid picking up wikilinks elsewhere.
    block = re.search(
        r"^sources\s*:(.*?)(?=\n\S|\Z)", fm_body, re.MULTILINE | re.DOTALL
    )
    if not block:
        return []
    return re.findall(r'"(\[\[[^\]]+\]\])"', block.group(0))


def _replace_sources_in_frontmatter(fm_body, sources_list):
    """Remove all `sources:` entries (handles duplicates / block-list / inline)
    and write a single canonical inline `sources: [...]` line.

    Insertion order: after `updated:` if present, else after `created:`, else at end.
    """
    # Build the replacement line
    if sources_list:
        refs = ", ".join(f'"{r}"' for r in sources_list)
        new_line = f"sources: [{refs}]"
    else:
        new_line = "sources: []"

    # Strip ALL existing sources: blocks (inline or block-list, including duplicates)
    # A block-list block ends at the next non-indented line.
    cleaned = re.sub(
        r"^sources\s*:.*?(?=\n\S|\Z)", "", fm_body, flags=re.MULTILINE | re.DOTALL
    )
    # Collapse any resulting blank lines left behind
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).rstrip("\r\n")

    # Insert after `updated:`, or `created:`, or at the very end
    for anchor in (r"^updated: .*", r"^created: .*"):
        m = re.search(anchor, cleaned, re.MULTILINE)
        if m:
            insert_at = m.end()
            return cleaned[:insert_at] + "\n" + new_line + cleaned[insert_at:]

    return cleaned + "\n" + new_line


def update_entity_appearances(detected_entities, source_title, date, today):
    """For each detected entity/concept/project, append a source appearance entry
    and add the source to the 'sources:' frontmatter list."""
    dir_map = {"entity": ENTITIES, "concept": CONCEPTS, "project": PROJECTS}
    updated = []
    for name, kind in sorted(detected_entities):
        path = dir_map[kind] / f"{safe_filename(name)}.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")

        # ── Frontmatter: add source ref (idempotent, YAML-format-agnostic) ──
        source_ref = f"[[{source_title}]]"
        fm_match = re.match(r"(---\s*\n)(.*?)(\n---)", text, re.DOTALL)
        if fm_match:
            fm_body = fm_match.group(2)
            existing = _parse_sources_list(fm_body)
            if source_ref not in existing:
                existing.append(source_ref)
            fm_body = _replace_sources_in_frontmatter(fm_body, existing)
            # Bump updated date
            fm_body = re.sub(
                r"^updated: \d{4}-\d{2}-\d{2}",
                f"updated: {today}",
                fm_body,
                flags=re.MULTILINE,
            )
            text = fm_match.group(1) + fm_body + fm_match.group(3) + text[fm_match.end():]

        # ── Body: append appearance bullet (idempotent) ──
        # Check for any existing bullet mentioning this source, not just the exact format,
        # so manually-written descriptive entries don't get duplicated.
        appearance_line = f"- [[{source_title}]] — {date}"
        already_present = re.search(
            r"^- \[\[" + re.escape(source_title) + r"\]\]",
            text,
            re.MULTILINE,
        )
        if not already_present:
            placeholder = "<!-- populated by meeting_ingest.py -->"
            if placeholder in text:
                text = text.replace(placeholder, placeholder + "\n" + appearance_line)
            elif "## Appearances in Sources" in text:
                text = re.sub(
                    r"(## Appearances in Sources\s*\n)",
                    r"\1" + appearance_line + "\n",
                    text,
                )
            else:
                text = text.rstrip() + "\n\n## Appearances in Sources\n\n" + appearance_line + "\n"

        path.write_text(text, encoding="utf-8")
        updated.append(name)
    return updated


def update_entity_coattendance(linked_attendees, source_title):
    """Append co-attendance entries to entity pages for each meeting attendee."""
    attendee_parsed = []
    for a in linked_attendees:
        m = re.match(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', a)
        if m:
            attendee_parsed.append((m.group(1), a))

    if len(attendee_parsed) < 2:
        return []

    updated = []
    for name, _ in attendee_parsed:
        path = ENTITIES / f"{safe_filename(name)}.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")

        # Idempotent: skip if this source is already in the co-attended section
        co_section = re.search(r'## Co-attended with(.+?)(?=\n## |\Z)', text, re.DOTALL)
        if co_section and f"[[{source_title}]]" in co_section.group(1):
            continue

        others = [display for a_name, display in attendee_parsed if a_name != name]
        entry = f"- [[{source_title}]] — {', '.join(others)}"

        if "## Co-attended with" in text:
            text = re.sub(
                r"(## Co-attended with\s*\n)",
                r"\1" + entry + "\n",
                text,
                count=1,
            )
        else:
            text = text.rstrip() + "\n\n## Co-attended with\n\n" + entry + "\n"

        path.write_text(text, encoding="utf-8")
        updated.append(name)
    return updated


def append_to_log(source_title, meeting_id, meeting_source, business, new_entity_stubs,
                  action_items_count, today):
    if not LOG.exists():
        return
    text = LOG.read_text(encoding="utf-8")
    marker = f"## [{today}] ingest | {source_title}"
    if marker in text:
        return  # idempotent

    stub_str = (
        ", ".join(f"[[{n}]]" for n in new_entity_stubs)
        if new_entity_stubs
        else "none"
    )
    entry = (
        f"\n\n{marker}\n\n"
        f"- **Source**: {meeting_source} meeting `{meeting_id}` via `meeting_ingest.py`\n"
        f"- **Business**: `#{business}`\n"
        f"- **Created**: source page + {len(new_entity_stubs)} entity stub(s)\n"
        f"- **Action items**: {action_items_count}\n"
        f"- **Stubs**: {stub_str}\n"
    )
    text = text.rstrip() + entry
    text = re.sub(
        r"^updated: \d{4}-\d{2}-\d{2}",
        f"updated: {today}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    LOG.write_text(text, encoding="utf-8")


def promote_user_action_items(action_items, source_title):
    """Append user-assigned action items to hotcache.md under the configured heading."""
    if not HOTCACHE.exists() or not action_items:
        return 0
    variants = sorted(user_name_variants(), key=len, reverse=True)
    bold_patterns = tuple(f"**{v}**" for v in variants)
    strip_re = re.compile(
        r"^\*\*(?:" + "|".join(re.escape(v) for v in variants) + r")\*\*\s*—\s*"
    )
    heading = hotcache_action_items_heading()
    promoted = []
    for item in action_items:
        if any(item.startswith(p) for p in bold_patterns):
            task = strip_re.sub("", item).strip()
            if task:
                promoted.append(f"- [ ] {task} (from [[{source_title}]])")
    if not promoted:
        return 0
    text = HOTCACHE.read_text(encoding="utf-8")
    for line in promoted:
        text = append_under_heading(text, heading, line)
    HOTCACHE.write_text(text, encoding="utf-8")
    return len(promoted)


def update_hotcache_last_contact(meeting_title, names, meeting_date, summary_text=None):
    """Update last_contact= in the best-matching hotcache thread's <!-- deal: --> comment.

    When summary_text contains a stage-change keyword that ranks higher than the
    thread's current stage, also advance stage= (never downgrades). Returns None
    if no thread matched, else (thread_name, stage_change) where stage_change is
    None or {"thread": name, "from": old, "to": new}."""
    if not HOTCACHE.exists():
        return None

    tokens = set(
        t.lower() for t in re.split(r"\W+", meeting_title)
        if len(t) > 3 and t.lower() not in _HOTCACHE_STOPWORDS
    )
    for name in (names or []):
        for t in re.split(r"\W+", name):
            if len(t) > 3 and t.lower() not in _HOTCACHE_STOPWORDS:
                tokens.add(t.lower())
    if not tokens:
        return None

    text = HOTCACHE.read_text(encoding="utf-8")
    parts = re.split(r"(\n### [^\n]+)", text)

    best_score, best_idx = 0, -1
    for i in range(1, len(parts), 2):
        score = sum(1 for t in tokens if t in parts[i].lower())
        if score > best_score:
            best_score, best_idx = score, i

    if best_score < 1:
        return None

    thread_name = parts[best_idx].strip().lstrip("#").strip()
    date_str = meeting_date if isinstance(meeting_date, str) else meeting_date.strftime("%Y-%m-%d")
    new_stage = detect_stage_change(summary_text)
    stage_change = {}

    def _replace(m):
        meta = re.sub(r"last_contact=\d{4}-\d{2}-\d{2}", f"last_contact={date_str}", m.group(2))
        if new_stage:
            cur_m = re.search(r"stage=([\w-]+)", meta)
            cur = cur_m.group(1) if cur_m else None
            # No-downgrade guard: only advance to a higher-ranked stage.
            if cur_m and _STAGE_RANK.get(new_stage, 0) > _STAGE_RANK.get(cur, 0):
                meta = re.sub(r"stage=[\w-]+", f"stage={new_stage}", meta, count=1)
                stage_change["thread"] = thread_name
                stage_change["from"] = cur
                stage_change["to"] = new_stage
        return m.group(1) + meta + m.group(3)

    new_content, n = re.subn(
        r"(<!--\s*deal:\s*)(.+?)(\s*-->)", _replace, parts[best_idx + 1], count=1
    )
    if n == 0:
        return None

    parts[best_idx + 1] = new_content
    HOTCACHE.write_text("".join(parts), encoding="utf-8")
    return thread_name, (stage_change or None)


def first_line_of_summary(summary_md, max_len=200):
    """Pull a one-line description from the start of the summary for index.md.
    Skips section headings and grabs the first real bullet/sentence."""
    if not summary_md:
        return "(no summary)"
    for raw in summary_md.splitlines():
        if raw.lstrip().startswith("#"):
            continue  # skip section headings
        line = raw.strip()
        line = re.sub(r"^[-*]\s*", "", line)
        # Strip wikilink brackets for the index one-liner so it stays compact
        line = re.sub(r"\[\[[^|\]]*\|([^\]]+)\]\]", r"\1", line)  # [[Canon|Alias]] → Alias
        line = re.sub(r"\[\[([^\]]+)\]\]", r"\1", line)
        # Skip indented sub-bullets that may continue from headings
        if line:
            return line[:max_len].rstrip() + ("…" if len(line) > max_len else "")
    return "(no summary)"


# ── Meeting ↔ deal thread cross-reference ────────────────────────────────────

def find_obsidian_week_file():
    """Return path to the most recent Obsidian weekly file within the last 7 days."""
    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=7)
    best = None
    for p in OBSIDIAN_WEEKLY_DIR.glob("week-*.md"):
        m = re.match(r"week-(\d{4}-\d{2}-\d{2})\.md", p.name)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date >= cutoff and (best is None or file_date > best[0]):
            best = (file_date, p)
    return best[1] if best else None


WEEK_DEAL_RE = re.compile(
    r"^- \[(.)\] \[(?:[^\]]+)\] \"(.+?)\" — (.+?) \(([^)]+@[^)]+)\)"
)


def match_deal_threads(attendee_emails, meeting_title):
    """Find open deal threads in the current week file that match attendee emails
    or meeting title name tokens. Returns list of dicts."""
    week_file = find_obsidian_week_file()
    if not week_file:
        return []

    attendee_set = {e.lower().strip() for e in attendee_emails if e}
    title_tokens = {t.lower() for t in re.split(r"[\s/,|]+", meeting_title) if len(t) > 2}

    matches = []
    current_section = None
    for line in week_file.read_text(encoding="utf-8").splitlines():
        if re.match(r"^## Deals — Waiting", line):
            current_section = "waiting"
        elif re.match(r"^## Deals — Cold Urgent", line):
            current_section = "cold_urgent"
        elif re.match(r"^## Deals — Cold Monitor", line):
            current_section = "cold_monitor"
        elif re.match(r"^## ", line):
            current_section = None

        if current_section is None:
            continue

        m = WEEK_DEAL_RE.match(line)
        if not m:
            continue
        checked_char, subject, name, email = m.groups()
        if checked_char == "x":
            continue

        if email.lower().strip() in attendee_set:
            matches.append({
                "subject": subject, "name": name, "email": email,
                "section": current_section, "match_type": "email",
            })
            continue

        name_tokens = {t.lower() for t in name.split() if len(t) > 2}
        if title_tokens & name_tokens:
            matches.append({
                "subject": subject, "name": name, "email": email,
                "section": current_section, "match_type": "name",
            })

    return matches


def mark_threads_met(matches, date):
    """Append <!-- met:YYYY-MM-DD --> to matching deal lines in the Obsidian week file.
    Idempotent: skips lines already tagged."""
    week_file = find_obsidian_week_file()
    if not week_file:
        return 0
    tagged = {(dm["subject"], dm["email"]) for dm in matches}
    lines = week_file.read_text(encoding="utf-8").splitlines(keepends=True)
    changed = 0
    for i, line in enumerate(lines):
        m = WEEK_DEAL_RE.match(line)
        if not m:
            continue
        _, subject, _, email = m.groups()
        if (subject, email) not in tagged:
            continue
        if "<!-- met:" in line:
            continue
        lines[i] = line.rstrip("\r\n") + f" <!-- met:{date} -->\n"
        changed += 1
    if changed:
        week_file.write_text("".join(lines), encoding="utf-8")
    return changed


# ── Batch pipeline (uningested mode) ─────────────────────────────────────────
def run_batch(args):
    """Handle --meeting uningested: list or ingest all uningested recent meetings."""
    limit = getattr(args, "limit", 20)
    stubs = fetch_uningested_stubs(limit)

    if not stubs:
        print(f"All meetings in the last {limit} are already ingested.")
        return

    if args.dry_run:
        print(f"=== Uningested meetings (scanning last {limit}) ===")
        print(f"Found {len(stubs)} not yet in wiki/sources/:\n")
        for s in stubs:
            date = s.get("created_at", "")[:10]
            print(f"  {s['id']}  {date}  {s.get('title', 'Untitled')}")
        print()
        print("To ingest all with one business tag:")
        print(f"  meeting_ingest.py --meeting uningested --apply --business <tag>")
        print()
        print("To ingest individually (recommended for mixed-business backlog):")
        for s in stubs:
            print(f"  meeting_ingest.py --meeting {s['id']} --apply --business <tag>")
        return

    # ── AUTO mode: per-meeting inferred tags, no human input ──
    if getattr(args, "auto", False):
        print(f"[auto] Ingesting {len(stubs)} uningested meeting(s) with inferred tags...\n")
        ok, skipped = [], []
        for stub in stubs:
            note_id = stub["id"]
            title = stub.get("title", "Untitled")
            print(f"── {title} ({note_id})")
            args.meeting = note_id
            args.business = None  # run_ingest will use inferred_business
            try:
                run_ingest(args)
            except SystemExit as e:
                code = e.code
                if code == 13:
                    print(f"  · already exists, skipping")
                    skipped.append(title)
                else:
                    print(f"  ✗ failed ({code}) — skipping")
                    skipped.append(title)
                continue
            ok.append(title)
            print()
        print(f"\n[auto] Done. {len(ok)} ingested, {len(skipped)} skipped.")
        if skipped:
            print("Skipped:")
            for t in skipped:
                print(f"  - {t}")
        return

    # ── APPLY batch ──
    if not args.business:
        sys.stderr.write(
            "ERROR: --business required for --apply. "
            "Use --dry-run first to see the list, then ingest individually if tags differ.\n"
        )
        sys.exit(EXIT_MISSING_BUSINESS)

    print(f"Ingesting {len(stubs)} uningested meeting(s) with --business {args.business}...\n")
    ok = []
    skipped = []
    for stub in stubs:
        note_id = stub["id"]
        title = stub.get("title", "Untitled")
        print(f"── {title} ({note_id})")
        # Delegate to single-note pipeline by swapping args.meeting
        args.meeting = note_id
        try:
            run_ingest(args)
        except SystemExit as e:
            code = e.code
            if code == 13:
                print(f"  · already exists, skipping (use --force to overwrite)")
                skipped.append(title)
            else:
                print(f"  ✗ failed with exit code {code} — skipping")
                skipped.append(title)
            continue
        ok.append(title)
        print()

    print(f"\nDone. {len(ok)} ingested, {len(skipped)} skipped.")
    if skipped:
        print("Skipped:")
        for t in skipped:
            print(f"  - {t}")


# ── Main pipeline ─────────────────────────────────────────────────────────────
def run_ingest(args):
    note = fetch_meeting(args.meeting)
    meeting_id = note.get("id")
    meeting_source = note.get("source") or SOURCE or notetaker.active_name()
    raw_title = note.get("title") or "Untitled Meeting"
    date = meeting_date_from_note(note)
    today = datetime.now().strftime("%Y-%m-%d")

    jargon = parse_jargon_table()
    title = apply_jargon(raw_title, jargon)

    summary_md = note.get("summary_markdown") or note.get("summary_text") or ""
    summary_md = strip_pii(summary_md)
    summary_md = apply_jargon(summary_md, jargon)

    # Wikilink BEFORE extracting action items, so assignees inside the action
    # items section get the same wikilink treatment as the summary body.
    canonical, alias_map = list_existing_pages()
    summary_md_linked = wikilink_entities_in_text(summary_md, canonical, alias_map)

    # Grain returns a share link as a field; Granola only writes one into the
    # summary footer. Prefer the field — but validate it first. The footer path
    # was always host-anchored by SHARE_URL_RE; taking a provider field on trust
    # would have been a downgrade, and this link is written straight into vault
    # markdown as [Transcript](...), where a stray ")" closes the link early and
    # spills the remainder into the page as content this repo parses.
    share_url = safe_share_url(note.get("share_url")) or extract_share_url(summary_md_linked)
    action_items, summary_no_actions = extract_action_items(summary_md_linked)
    summary_body_linked = strip_share_url_footer(summary_no_actions)

    # Detect against the original (un-wikilinked) summary + title so we don't double-count.
    # Including the title ensures attendees named in the meeting title (e.g. "<User> / <Counterparty> Sync")
    # are picked up for business-tag inference even if they aren't mentioned in the summary body.
    detected_for_page = detect_entities_in_text(f"{title} {summary_md}", canonical, alias_map)

    raw_attendees = attendee_names(note)
    linked_attendees, new_attendee_candidates = detect_attendee_wikilinks(
        raw_attendees, canonical, alias_map
    )
    attendees_formatted = (
        ", ".join(linked_attendees) if linked_attendees else "(none returned by API)"
    )

    title_entities = detect_entities_in_text(title, canonical, alias_map)
    inferred_business = infer_business_tag(detected_for_page, title_entities=title_entities)
    business = args.business or inferred_business
    inferred_type = infer_meeting_type(title, summary_md)
    # Use explicit --type if provided; otherwise fall back to inferred type
    meeting_type = args.type if args.type != "meeting" else inferred_type

    # The "wiki title" must equal the file's stem so wikilinks resolve.
    # Slashes and other unsafe filename chars get rewritten consistently
    # everywhere they appear (frontmatter, H1, wikilinks in index/log).
    source_title = safe_filename(f"{title} - {date}")
    source_path = SOURCES / f"{source_title}.md"

    # If a file with the same title+date already exists for a DIFFERENT meeting,
    # append a short ID suffix to avoid clobbering it.
    if source_path.exists() and not args.force:
        existing_text = source_path.read_text(encoding="utf-8")
        existing_id_match = MEETING_ID_RE.search(existing_text)
        existing_id = existing_id_match.group(1).strip() if existing_id_match else None
        if existing_id and existing_id != meeting_id:
            # Different meeting, same filename — disambiguate with a short suffix
            short_suffix = meeting_id[-6:]
            source_title = safe_filename(f"{title} - {date} ({short_suffix})")
            source_path = SOURCES / f"{source_title}.md"

    # ── DRY RUN ──
    if args.dry_run:
        print("=== Meeting Ingest: Dry Run ===")
        print(f"Meeting:      {title}")
        print(f"Date:         {date}")
        print(f"Source:       {meeting_source}")
        print(f"Meeting ID:   {meeting_id}")
        print(f"Meeting URL:  {share_url or '(not detected)'}")
        print(f"Attendees:    {', '.join(raw_attendees) or '(none returned by API)'}")
        print(f"Action items: {len(action_items)}")
        print(f"Jargon table: {len(jargon)} entries loaded")
        print()
        if detected_for_page:
            print("Existing pages detected in summary:")
            for name, kind in sorted(detected_for_page):
                print(f"  - [[{name}]] ({kind})")
        else:
            print("No existing entities detected in summary.")
        print()
        if new_attendee_candidates:
            print("Attendees with no entity page yet:")
            for name in new_attendee_candidates:
                print(f"  - {name}")
            print()
        print(f"Inferred business tag: {inferred_business or '(none)'}")
        print(f"Will use:              {business or '(REQUIRED — pass --business)'}")
        print(f"Inferred type:         {inferred_type}")
        print(f"Meeting type:          {meeting_type}")
        print()
        print(f"Would write: {source_path}")
        if source_path.exists() and not args.force:
            print("  ⚠ ALREADY EXISTS — use --force to overwrite")
        if args.stub_attendees and new_attendee_candidates:
            print(
                f"Would create stubs: {', '.join(new_attendee_candidates)} "
                f"(--stub-attendees)"
            )
        elif new_attendee_candidates:
            print(
                "(Skipping attendee stubs — pass --stub-attendees if you want them.)"
            )
        print()
        if not business:
            print("⚠ Pass --business <tag> with --apply to write.")
        else:
            print(f"To apply: rerun with --apply --business {business}")
        return

    # ── APPLY ──
    if not business:
        if getattr(args, "auto", False):
            business = "personal"
            print(f"  [auto] No business tag inferred — defaulting to 'personal'")
        else:
            sys.stderr.write("ERROR: --business required for --apply (or use --dry-run)\n")
            sys.exit(EXIT_MISSING_BUSINESS)
    if business not in VALID_BUSINESS:
        sys.stderr.write(
            f"ERROR: --business must be one of: {sorted(VALID_BUSINESS)}\n"
        )
        sys.exit(EXIT_BAD_BUSINESS)
    if meeting_type not in VALID_TYPE:
        sys.stderr.write(f"ERROR: --type must be one of: {sorted(VALID_TYPE)}\n")
        sys.exit(EXIT_BAD_TYPE)

    if source_path.exists() and not args.force:
        sys.stderr.write(
            f"ERROR: {source_path} already exists. Use --force to overwrite.\n"
        )
        sys.exit(EXIT_PAGE_EXISTS)

    page = build_source_page(
        meeting_id=meeting_id,
        meeting_source=meeting_source,
        source_title=source_title,
        date=date,
        attendees_formatted=attendees_formatted,
        attendees_list=linked_attendees,
        share_url=share_url,
        business=business,
        meeting_type=meeting_type,
        summary_body=summary_body_linked,
        action_items=action_items,
        detected_entities=detected_for_page,
        today=today,
    )

    SOURCES.mkdir(parents=True, exist_ok=True)
    source_path.write_text(page, encoding="utf-8")
    print(f"✓ Wrote {source_path}")

    if not args.dry_run:
        promoted = promote_user_action_items(action_items, source_title)
        if promoted:
            print(f"✓ Promoted {promoted} action item(s) to hotcache.md")
        updated = update_hotcache_last_contact(title, attendee_names(note), date, summary_text=summary_md)
        if updated:
            updated_thread, stage_change = updated
            print(f"✓ Updated hotcache last_contact: {updated_thread}")
            if stage_change:
                print(f"✓ Advanced hotcache stage: {stage_change['thread']} ({stage_change['from']} → {stage_change['to']})")

    updated_entities = update_entity_appearances(detected_for_page, source_title, date, today)
    if updated_entities:
        print(f"✓ Updated appearances in: {', '.join(updated_entities)}")

    co_updated = update_entity_coattendance(linked_attendees, source_title)
    if co_updated:
        print(f"✓ Updated co-attendance in: {', '.join(co_updated)}")

    # Cross-reference against open deal threads
    raw_attendee_emails = [
        (a or {}).get("email", "") for a in (note.get("attendees") or [])
    ]
    deal_matches = match_deal_threads(raw_attendee_emails, title)
    if deal_matches:
        print()
        print(f"⚠ Deal thread matches ({len(deal_matches)}):")
        for dm in deal_matches:
            flag = "(email)" if dm["match_type"] == "email" else "(name)"
            print(f"  [{dm['section']}] \"{dm['subject']}\" — {dm['name']} {flag}")
        if args.link_deals:
            marked = mark_threads_met(deal_matches, date)
            wf = find_obsidian_week_file()
            print(f"  ✓ Marked {marked} thread(s) as met in {wf.name if wf else 'week file'}")
        else:
            print("  Run with --link-deals to mark these threads as met in the week file.")

    new_stubs = new_attendee_candidates if args.stub_attendees else []
    for name in new_stubs:
        stub_path = ENTITIES / f"{safe_filename(name)}.md"
        if stub_path.exists():
            print(f"  · stub exists, skipping: {name}")
            continue
        stub_path.write_text(build_entity_stub(name, business, source_title, today), encoding="utf-8")
        print(f"✓ Created stub: {stub_path}")

    # Also update appearances for freshly created stubs
    if new_stubs:
        stub_entities = {
            (name, "entity")
            for name in new_stubs
            if (ENTITIES / f"{safe_filename(name)}.md").exists()
        }
        stub_updated = update_entity_appearances(stub_entities, source_title, date, today)
        if stub_updated:
            print(f"✓ Stub appearances updated: {', '.join(stub_updated)}")

    one_liner = first_line_of_summary(summary_body_linked)
    update_index(source_title, one_liner, business, new_stubs, today)
    print("✓ Updated wiki/index.md")

    lint_issues = lint_new_index_entry(source_title, one_liner, business)
    if lint_issues:
        print("⚠ Index entry quality issues:")
        for issue in lint_issues:
            print(f"    - {issue}")
        print("    Consider editing the entry in wiki/index.md.")

    append_to_log(source_title, meeting_id, meeting_source, business, new_stubs,
                  len(action_items), today)
    print("✓ Updated wiki/log.md")

    print()
    print("Done. meeting_id stored in frontmatter.")
    print(
        f"Pull transcript later with:\n"
        f"  meeting_fetch.py --meeting {meeting_id} "
        f"--source {meeting_source} --fields transcript"
    )


def run_backfill():
    """Retroactively populate 'Appearances in Sources' and sources: frontmatter
    for all entities/concepts/projects by scanning every existing source page."""
    canonical, alias_map = list_existing_pages()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    source_files = sorted(SOURCES.glob("*.md"))
    print(f"=== Backfill: scanning {len(source_files)} source pages ===\n")

    total_updated = {}
    for sf in source_files:
        text = sf.read_text(encoding="utf-8")
        # Extract date from frontmatter or fall back to file stem
        date_match = re.search(r"^- \*\*Date:\*\*\s*(\d{4}-\d{2}-\d{2})", text, re.MULTILINE)
        date = date_match.group(1) if date_match else "unknown"
        source_title = sf.stem
        detected = detect_entities_in_text(text, canonical, alias_map)
        if not detected:
            continue
        updated = update_entity_appearances(detected, source_title, date, today)
        if updated:
            print(f"  [{source_title}] -> {', '.join(updated)}")
            for name in updated:
                total_updated[name] = total_updated.get(name, 0) + 1

    print(f"\nDone. Updated {len(total_updated)} unique pages across {len(source_files)} sources.")
    if total_updated:
        print("Pages updated (appearance count):")
        for name, count in sorted(total_updated.items()):
            print(f"  {name}: {count} appearance(s) added")


def main():
    force_utf8_io()
    parser = argparse.ArgumentParser(
        description="End-to-end notetaker → Second Brain wiki ingest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--meeting",
        help="Meeting ID, 'latest' for the most recent, or 'uningested' for batch mode",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Retroactively populate Appearances in Sources for all entities from existing sources",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="How many recent meetings to scan in uningested mode (default: 30)",
    )
    parser.add_argument(
        "--source",
        metavar="NAME",
        help=(
            "Read from this notetaker instead of the configured one. "
            f"Registered: {', '.join(notetaker.provider_names())}"
        ),
    )
    parser.add_argument(
        "--business",
        help=f"Business tag, one of: {sorted(VALID_BUSINESS)}",
    )
    parser.add_argument(
        "--type",
        default="meeting",
        choices=sorted(VALID_TYPE),
        help="Source type (default: meeting)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--apply", action="store_true", help="Write to disk")
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Unattended batch mode: ingest all uningested with inferred tags, "
             "default 'personal' if no tag inferred. Skips Dev Log and attendee stubs.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing source page")
    parser.add_argument(
        "--stub-attendees",
        action="store_true",
        help="Auto-create entity stubs for new attendees (default: off)",
    )
    parser.add_argument(
        "--link-deals",
        action="store_true",
        help="When deal thread matches are found, append <!-- met:DATE --> to those lines in the week file",
    )
    args = parser.parse_args()

    if args.source:
        global SOURCE
        SOURCE = args.source
        _provider()  # fail fast on an unknown --source, before any vault work

    if args.backfill:
        run_backfill()
        return

    # --auto implies --meeting uningested --apply (unattended batch)
    if args.auto:
        args.meeting = "uningested"
        args.apply = True
        args.dry_run = False
        run_batch(args)
        return

    if not args.meeting:
        parser.error("Pass --meeting <id|latest|uningested>, --auto, or --backfill")

    if not args.dry_run and not args.apply:
        parser.error("Pass --dry-run or --apply")

    if args.meeting == "uningested":
        run_batch(args)
    else:
        run_ingest(args)


if __name__ == "__main__":
    main()