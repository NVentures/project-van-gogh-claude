"""
Granola → Obsidian Meeting Note Formatter

Reads meeting data (passed as JSON from Granola MCP) and formats it as an
Obsidian-flavored Markdown meeting note. Outputs the formatted note to stdout
or writes directly to the Obsidian vault.

Usage:
  python granola_to_obsidian.py --input meeting.json [--output /path/to/note.md] [--preview]

If --preview is passed, prints the formatted note to stdout without writing.
If --output is passed, writes to that path.
If neither, prints to stdout.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from config_loader import vault, meeting_routes

OBSIDIAN_VAULT = vault()

# Routing is defined per-business in config.json (businesses[].meeting_route).
ROUTING_MAP = meeting_routes()

DEFAULT_ROUTE = ROUTING_MAP.get("personal", "10-Personal/Meetings")


def strip_pii(text: str) -> str:
    """Remove emails and phone numbers from text."""
    # Strip email addresses
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[email removed]', text)
    # Strip phone numbers (various formats)
    text = re.sub(r'(\+?1?[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', '[phone removed]', text)
    return text


def format_attendees_as_wikilinks(attendees: list) -> list:
    """Convert attendee names to Obsidian wikilinks."""
    return [f'  - "[[{name.strip()}]]"' for name in attendees if name.strip()]


def determine_route(meeting_data: dict, route_hint: str = None) -> str:
    """Determine which folder to save the meeting note to."""
    if route_hint and route_hint.lower() in ROUTING_MAP:
        return ROUTING_MAP[route_hint.lower()]
    return DEFAULT_ROUTE


def format_meeting_note(meeting_data: dict, route_hint: str = None) -> tuple:
    """
    Format meeting data into an Obsidian meeting note.

    Returns (formatted_note: str, suggested_path: Path)
    """
    title = meeting_data.get("title", "Untitled Meeting")
    date = meeting_data.get("date", datetime.now().strftime("%Y-%m-%d"))
    attendees = meeting_data.get("attendees", [])
    summary = meeting_data.get("summary", "")
    action_items = meeting_data.get("action_items", [])
    decisions = meeting_data.get("decisions", [])
    transcript = meeting_data.get("transcript", "")
    tags = meeting_data.get("tags", ["type/meeting"])

    # Ensure type/meeting is always present
    if "type/meeting" not in tags:
        tags.insert(0, "type/meeting")

    # Strip PII from all text content
    summary = strip_pii(summary)
    transcript = strip_pii(transcript)
    action_items = [strip_pii(item) for item in action_items]
    decisions = [strip_pii(item) for item in decisions]

    # Build frontmatter
    tags_yaml = "\n".join(f"  - {tag}" for tag in tags)
    attendees_yaml = "\n".join(format_attendees_as_wikilinks(attendees))

    frontmatter = f"""---
title: {title}
date: {date}
tags:
{tags_yaml}
attendees:
{attendees_yaml}
---"""

    # Build body
    parts = [frontmatter, ""]

    # Summary
    if summary:
        parts.append("> [!summary] Meeting Summary")
        for line in summary.strip().split("\n"):
            parts.append(f"> {line}")
        parts.append("")

    # Action items
    parts.append("## Action Items")
    if action_items:
        for item in action_items:
            parts.append(f"- [ ] {item}")
    else:
        parts.append("- [ ] ")
    parts.append("")

    # Key decisions
    parts.append("## Key Decisions")
    if decisions:
        for decision in decisions:
            parts.append(f"- {decision}")
    else:
        parts.append("- ")
    parts.append("")

    # Transcript (collapsed)
    if transcript:
        parts.append("> [!note]- Full Transcript")
        parts.append("> (collapsed by default -- click to expand)")
        for line in transcript.strip().split("\n"):
            parts.append(f"> {line}")
        parts.append("")

    # Related section
    parts.append("## Related")
    parts.append("")

    formatted = "\n".join(parts)

    # Determine save path
    route = determine_route(meeting_data, route_hint)
    folder = OBSIDIAN_VAULT / route
    safe_title = re.sub(r'[/\\:*?"<>|]', '-', title)
    filename = f"{date} {safe_title}.md"
    suggested_path = folder / filename

    return formatted, suggested_path


def main():
    parser = argparse.ArgumentParser(description="Format Granola meeting data as Obsidian note")
    parser.add_argument("--input", "-i", help="Path to JSON file with meeting data")
    parser.add_argument("--output", "-o", help="Output path (overrides auto-routing)")
    parser.add_argument("--route", "-r", help="Route hint (a business tag from config businesses[].tag)")
    parser.add_argument("--preview", action="store_true", help="Print preview without writing")
    args = parser.parse_args()

    # Read meeting data
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            meeting_data = json.load(f)
    else:
        # Read from stdin
        meeting_data = json.load(sys.stdin)

    formatted, suggested_path = format_meeting_note(meeting_data, args.route)

    if args.preview:
        print(f"--- Suggested path: {suggested_path} ---")
        print()
        print(formatted)
        return

    output_path = Path(args.output) if args.output else suggested_path

    # Create directory if needed
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write the note
    output_path.write_text(formatted, encoding="utf-8")
    print(f"Meeting note saved to: {output_path}")


if __name__ == "__main__":
    main()
