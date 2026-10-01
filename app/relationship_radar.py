#!/usr/bin/env python3
"""
Relationship Radar — scan entity pages for contacts going cold.
Yellow/red thresholds come from config (default 30 / 60 days).
Cross-references hotcache to skip actively-managed deal contacts.
Writes the radar source page and prints a summary.

All user-specific values come from config.json via config_loader.
"""
import re
from datetime import date
from pathlib import Path

from config_loader import (
    entities_dir,
    hotcache_path,
    hotcache_active_threads_heading,
    voice_guide_path,
    tone_profile_path,
    voice_drafts_path,
    relationship_radar_path,
    user_name,
    user_self_entities,
    internal_team_names,
    radar_skip_tags,
    radar_skip_entity_names,
    radar_yellow_days,
    radar_red_days,
    radar_yellow_draft_cap,
)
from claude_cli import run_claude

ENTITIES_DIR = entities_dir()
HOTCACHE_PATH = hotcache_path()
VOICE_GUIDE_PATH = voice_guide_path()
TONE_PROFILE_PATH = tone_profile_path()
OUTPUT_PATH = relationship_radar_path()
VOICE_DRAFTS_LOG = voice_drafts_path()

YELLOW_DAYS = radar_yellow_days()
RED_DAYS = radar_red_days()
YELLOW_DRAFT_CAP = radar_yellow_draft_cap()
ACTIVE_THREADS_HEADING = hotcache_active_threads_heading()

# Names to skip: internal teammates, the user themselves, and configured orgs.
SKIP_NAMES = internal_team_names() | set(user_self_entities()) | radar_skip_entity_names()
SKIP_TAGS = radar_skip_tags()


def extract_last_contact(sources: list, updated: str) -> date | None:
    date_re = re.compile(r"(\d{4}-\d{2}-\d{2})")
    dates = []
    for src in sources:
        m = date_re.search(str(src))
        if m:
            try:
                dates.append(date.fromisoformat(m.group(1)))
            except ValueError:
                pass
    if dates:
        return max(dates)
    if updated:
        try:
            return date.fromisoformat(updated.strip())
        except ValueError:
            pass
    return None


def parse_entity(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not fm_match:
        return None
    fm = fm_match.group(1)

    title_m = re.search(r"^title:\s*(.+)$", fm, re.MULTILINE)
    name = title_m.group(1).strip() if title_m else path.stem

    if name in SKIP_NAMES:
        return None

    tags_m = re.search(r"^tags:\s*(.+)$", fm, re.MULTILINE)
    tags = set(re.findall(r"[\w-]+", tags_m.group(1))) if tags_m else set()
    if tags & SKIP_TAGS:
        return None

    sources_m = re.search(r"^sources:\s*(\[.*?\])", fm, re.DOTALL | re.MULTILINE)
    sources = re.findall(r'"([^"]+)"', sources_m.group(1)) if sources_m else []

    updated_m = re.search(r"^updated:\s*(.+)$", fm, re.MULTILINE)
    updated_str = updated_m.group(1).strip() if updated_m else None

    last_contact = extract_last_contact(sources, updated_str)
    if last_contact is None:
        return None

    days_elapsed = (date.today() - last_contact).days
    if days_elapsed < YELLOW_DAYS:
        return None

    # First substantive paragraph from body (strip wikilinks for cleaner prompts)
    body = text[fm_match.end():].strip()
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip() and not p.strip().startswith("#")]
    context_raw = paragraphs[0][:400] if paragraphs else ""
    context = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", context_raw)

    return {
        "name": name,
        "last_contact": last_contact.isoformat(),
        "days_elapsed": days_elapsed,
        "context": context,
        "most_recent_source": sources[-1] if sources else None,
        "has_context": len(context) >= 50,
    }


def get_hotcache_active_names() -> set:
    if not HOTCACHE_PATH.exists():
        return set()
    text = HOTCACHE_PATH.read_text(encoding="utf-8")
    section_m = re.search(
        rf"##\s+{re.escape(ACTIVE_THREADS_HEADING)}(.+?)(?=\n## |\Z)",
        text,
        re.DOTALL,
    )
    if not section_m:
        return set()
    return set(re.findall(r"\[\[([A-Z][^\]|#\n]+?)\]\]", section_m.group(1)))


def get_anti_prompt() -> str:
    """Universal anti-prompt rules from the tone profile (the /voice-generator output)."""
    if not TONE_PROFILE_PATH.exists():
        return ""
    text = TONE_PROFILE_PATH.read_text(encoding="utf-8")
    m = re.search(r"(## Anti-Prompt.+?)(?=\n---|\Z)", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def get_voice_prompt() -> str:
    base = ""
    if VOICE_GUIDE_PATH.exists():
        text = VOICE_GUIDE_PATH.read_text(encoding="utf-8")
        m = re.search(r"## Paste-Ready Prompt Version\n\n(.+?)(?=\n---|\Z)", text, re.DOTALL)
        base = m.group(1).strip() if m else ""
    anti = get_anti_prompt()
    return f"{base}\n\n{anti}".strip() if anti else base


def generate_draft(contact: dict, voice_prompt: str) -> str:
    src_line = f"Most recent touchpoint: {contact['most_recent_source']}" if contact["most_recent_source"] else ""
    preamble = f"{voice_prompt}\n\n" if voice_prompt else ""
    prompt = f"""{preamble}Write a 2-3 sentence check-in message from {user_name()} to {contact['name']}.
Last contact: {contact['last_contact']} ({contact['days_elapsed']} days ago)
Context: {contact['context']}
{src_line}

Output ONLY the message itself. No preamble, no explanation, no headers. Just 2-3 sentences: casual, direct, not generic "just checking in", reference actual shared context, no em-dashes, no hedging, end with a specific forward-looking question or invitation."""

    try:
        result = run_claude(prompt, timeout=90)
        return result.stdout.strip() if result.returncode == 0 else "[draft failed]"
    except Exception:
        return "[draft failed]"


def render_block(contact: dict, draft: str | None) -> str:
    src_note = f"  \nSource: {contact['most_recent_source']}" if contact["most_recent_source"] else ""
    context_preview = contact["context"][:140] + ("..." if len(contact["context"]) > 140 else "")
    lines = [
        f"### {contact['name']} — {contact['days_elapsed']} days",
        f"Last contact: {contact['last_contact']}  |  {context_preview}{src_note}",
    ]
    if draft:
        lines.append(f"\n> {draft}")
    lines.append("")
    return "\n".join(lines)


def main():
    today = date.today()
    print(f"[relationship-radar] scanning {ENTITIES_DIR}...")

    if not ENTITIES_DIR.exists():
        print(f"[relationship-radar] entities dir not found: {ENTITIES_DIR}")
        print("SUMMARY red=0 yellow=0 in_deal=0")
        return

    hotcache_names = get_hotcache_active_names()
    voice_prompt = get_voice_prompt()

    contacts = []
    for path in sorted(ENTITIES_DIR.glob("*.md")):
        entity = parse_entity(path)
        if entity:
            contacts.append(entity)

    contacts.sort(key=lambda x: x["days_elapsed"], reverse=True)

    red = [c for c in contacts if c["days_elapsed"] >= RED_DAYS and c["name"] not in hotcache_names]
    yellow = [c for c in contacts if YELLOW_DAYS <= c["days_elapsed"] < RED_DAYS and c["name"] not in hotcache_names]
    in_deal = [c for c in contacts if c["name"] in hotcache_names]

    print(f"[relationship-radar] red={len(red)} yellow={len(yellow)} in-deal={len(in_deal)}")

    # Generate drafts: all red contacts with context + first N yellow with context
    draftable_red = [c for c in red if c["has_context"]]
    draftable_yellow = [c for c in yellow if c["has_context"]][:YELLOW_DRAFT_CAP]
    to_draft = draftable_red + draftable_yellow

    drafts: dict[str, str] = {}
    for i, c in enumerate(to_draft):
        print(f"[relationship-radar] drafting {i + 1}/{len(to_draft)}: {c['name']}")
        drafts[c["name"]] = generate_draft(c, voice_prompt)

    # Append drafts to voice calibration log
    if drafts:
        log_lines = []
        if not VOICE_DRAFTS_LOG.exists():
            VOICE_DRAFTS_LOG.parent.mkdir(parents=True, exist_ok=True)
            log_lines.append("# Voice Drafts Log\n\nAI-generated check-in drafts for voice calibration. Appended by relationship_radar.py.\n")
        for name, draft_text in drafts.items():
            if draft_text and draft_text != "[draft failed]":
                log_lines.append(f"## {today.isoformat()} | {name}\n\n> {draft_text}\n\n---\n")
        if log_lines:
            with VOICE_DRAFTS_LOG.open("a", encoding="utf-8") as f:
                f.write("\n".join(log_lines))

    # Render output
    lines = [
        "---",
        "title: Relationship Radar",
        f"updated: {today.isoformat()}",
        "---",
        "",
        f"# Relationship Radar — {today.strftime('%a %b')} {today.day}, {today.year}",
        "",
        f"Flagged {len(red)} red, {len(yellow)} yellow. {len(in_deal)} skipped (active in hotcache).",
        "",
    ]

    if red:
        lines += [f"## Red — {RED_DAYS}+ days (reach out now)", ""]
        for c in red:
            lines.append(render_block(c, drafts.get(c["name"])))

    if yellow:
        lines += [f"## Yellow — {YELLOW_DAYS}-{RED_DAYS - 1} days (check in soon)", ""]
        for c in yellow:
            lines.append(render_block(c, drafts.get(c["name"])))

    if in_deal:
        lines += ["## In Active Deal — skipped (tracked in hotcache)", ""]
        for c in in_deal:
            lines.append(f"- {c['name']} ({c['days_elapsed']} days)")
        lines.append("")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[relationship-radar] written to {OUTPUT_PATH}")
    print(f"SUMMARY red={len(red)} yellow={len(yellow)} in_deal={len(in_deal)}")


if __name__ == "__main__":
    main()
