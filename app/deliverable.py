#!/usr/bin/env python3
"""The two halves a ticket needs after it is proposed.

The ledger has always carried four ticket types (`email`, `pptx`, `xlsx`,
`docx`) and a full lifecycle, but only the email type had anything behind it.
This module is the producer end for the file types: a **stager** that plans the
content and a **runner** that builds the artifact, both dispatching on type.

Two rules, both inherited rather than invented here:

* **Nothing is built before a human nods.** The stager writes a plan and stops.
  The runner only ever sees a ticket the user has approved, and it writes to
  disk, never to a counterparty. `SEND` stays the one thing only a person does.
* **A deck is native, never a picture.** Every string in the output is real
  text in a real shape, selectable and editable in PowerPoint. Rendering slides
  to images and embedding them is permanently out: it ships a deliverable the
  recipient cannot edit.

The stager's plan is JSON, which the model writes and this module validates
before it reaches a builder. A model that returns prose around its JSON, or a
plan whose slides are empty, fails here rather than in python-pptx.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from config_loader import van_gogh_root

# The deck's palette and type, taken from DESIGN.md rather than re-chosen here.
# Light theme: an artifact is read on a projector or printed, never in a dark
# room, so the paper kneeboard is the right side of the two.
INK = "1A1F24"
MUTED = "5C6670"
RULE = "C9CFD3"
AMBER = "9C5F0A"
PAPER = "F3F4F1"

FONT_PROSE = "B612"
FONT_MONO = "B612 Mono"

# Slides past this are a document, not a deck. The cap is a real constraint on
# the model rather than a truncation after the fact: a plan that overruns is
# refused so the user sees why, instead of silently losing its last slides.
MAX_SLIDES = 20

# A deck is written once and read by other people, so it is worth the better
# model. Classification stays on Haiku; this does not.
OPUS_MODEL = "opus"

# Deliverables land beside every other piece of vault state.
def deliverables_dir() -> Path:
    return van_gogh_root() / "deliverables"


class PlanInvalid(Exception):
    """The staged plan cannot be built. The message says what is wrong with it."""


# ── Plan extraction and validation ────────────────────────────────────────────

def extract_json(text):
    """Pull the first balanced JSON object out of a model's reply.

    `claude -p` prefixes reasoning prose ahead of its payload often enough that
    a bare `json.loads` on the whole reply is a latent failure, and the CLI
    itself has been seen writing a warning line onto the same stream. Scanning
    for the first balanced object is the only form that survives both.
    """
    if not text:
        raise PlanInvalid("the model returned nothing")
    start = text.find("{")
    while start != -1:
        depth, in_string, escaped = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise PlanInvalid("no JSON object in the model's reply")


def _clean(text):
    """One line of artifact text: no dashes, no stray whitespace.

    DESIGN.md bars em and en dashes from every generated artifact, and a prompt
    instruction is never sufficient on its own. This is the guarantee.
    """
    s = str(text or "")
    s = s.replace("—", ", ").replace("–", "-")
    return re.sub(r"\s+", " ", s).strip()


def validate_plan(plan):
    """Return a normalized deck plan, or raise PlanInvalid saying what is wrong.

    Every failure names the thing to fix. A plan that arrives empty, oversized,
    or with a slide carrying no content is refused here rather than producing a
    deck with blank slides in it, which is the shape that gets sent by mistake.
    """
    if not isinstance(plan, dict):
        raise PlanInvalid("the plan is not an object")

    title = _clean(plan.get("title"))
    if not title:
        raise PlanInvalid("the plan has no title")

    raw_slides = plan.get("slides")
    if not isinstance(raw_slides, list) or not raw_slides:
        raise PlanInvalid("the plan has no slides")
    if len(raw_slides) > MAX_SLIDES:
        raise PlanInvalid(
            f"the plan has {len(raw_slides)} slides, more than the {MAX_SLIDES} a deck holds")

    slides = []
    for i, raw in enumerate(raw_slides, start=1):
        if not isinstance(raw, dict):
            raise PlanInvalid(f"slide {i} is not an object")
        heading = _clean(raw.get("heading"))
        if not heading:
            raise PlanInvalid(f"slide {i} has no heading")
        bullets = [_clean(b) for b in (raw.get("bullets") or []) if _clean(b)]
        if not bullets:
            raise PlanInvalid(f"slide {i} ({heading}) has no bullets")
        slides.append({"heading": heading, "bullets": bullets,
                       "note": _clean(raw.get("note"))})

    return {"title": title, "subtitle": _clean(plan.get("subtitle")), "slides": slides}


# ── The builder ───────────────────────────────────────────────────────────────

def _slug(text):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(text or "")).strip("-").lower()
    return (s or "deliverable")[:60]


def build_pptx(plan, out_path):
    """Write a native .pptx from a validated plan.

    Every string is a real text frame in a real shape: the recipient opens this
    and edits it. Slides are built on the blank layout so nothing inherits a
    template's placeholder prompts, which is how a deck ships to a reader still
    carrying "Describe the pain you relieve".
    """
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Inches, Pt

    plan = validate_plan(plan)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    def paint_ground(slide):
        shape = slide.shapes.add_shape(1, 0, 0, prs.slide_width, prs.slide_height)
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor.from_string(PAPER)
        shape.line.fill.background()
        shape.shadow.inherit = False
        # Drawn first, so it sits behind every later shape on the slide.
        slide.shapes._spTree.remove(shape._element)
        slide.shapes._spTree.insert(2, shape._element)

    def textbox(slide, left, top, width, height, text, size, font,
                color=INK, bold=False, caps=False, spacing=None):
        box = slide.shapes.add_textbox(left, top, width, height)
        frame = box.text_frame
        frame.word_wrap = True
        para = frame.paragraphs[0]
        run = para.add_run()
        run.text = text.upper() if caps else text
        run.font.size = Pt(size)
        run.font.name = font
        run.font.bold = bold
        run.font.color.rgb = RGBColor.from_string(color)
        if spacing is not None:
            # letter-spacing has no python-pptx property; it is a raw attribute.
            run.font._rPr.set("spc", str(int(spacing * 100)))
        return box

    def rule(slide, left, top, width):
        line = slide.shapes.add_shape(1, left, top, width, Emu(9525))
        line.fill.solid()
        line.fill.fore_color.rgb = RGBColor.from_string(RULE)
        line.line.fill.background()
        line.shadow.inherit = False

    margin = Inches(0.9)
    content_w = prs.slide_width - (margin * 2)

    # Cover.
    cover = prs.slides.add_slide(blank)
    paint_ground(cover)
    textbox(cover, margin, Inches(2.6), content_w, Inches(1.4),
            plan["title"], 40, FONT_PROSE, INK, bold=True)
    rule(cover, margin, Inches(4.15), Inches(2.2))
    if plan["subtitle"]:
        textbox(cover, margin, Inches(4.4), content_w, Inches(0.8),
                plan["subtitle"], 15, FONT_PROSE, MUTED)
    # `%-d` is glibc-only and raises on Windows; the day is formatted by hand.
    today = datetime.now()
    stamp = f"{today.strftime('%B')} {today.day}, {today.year}"
    textbox(cover, margin, Inches(6.5), content_w, Inches(0.4),
            stamp, 11, FONT_MONO, MUTED, caps=True, spacing=1.1)

    # Body.
    for index, slide_plan in enumerate(plan["slides"], start=1):
        slide = prs.slides.add_slide(blank)
        paint_ground(slide)
        textbox(slide, margin, Inches(0.62), content_w, Inches(0.3),
                f"{index:02d}", 11, FONT_MONO, AMBER, bold=True, spacing=1.1)
        textbox(slide, margin, Inches(1.0), content_w, Inches(0.9),
                slide_plan["heading"], 28, FONT_PROSE, INK, bold=True)
        rule(slide, margin, Inches(1.95), content_w)

        top = Inches(2.3)
        for bullet in slide_plan["bullets"]:
            box = slide.shapes.add_textbox(margin, top, content_w, Inches(0.5))
            frame = box.text_frame
            frame.word_wrap = True
            para = frame.paragraphs[0]
            marker = para.add_run()
            marker.text = "·  "
            marker.font.size = Pt(15)
            marker.font.name = FONT_MONO
            marker.font.color.rgb = RGBColor.from_string(AMBER)
            run = para.add_run()
            run.text = bullet
            run.font.size = Pt(15)
            run.font.name = FONT_PROSE
            run.font.color.rgb = RGBColor.from_string(INK)
            # Roughly one line per 95 characters at this measure; a long bullet
            # gets the room it needs so the next one does not sit on top of it.
            top += Inches(0.42 + 0.26 * (len(bullet) // 95))

        if slide_plan["note"]:
            slide.notes_slide.notes_text_frame.text = slide_plan["note"]

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out_path))
    return out_path


BUILDERS = {"pptx": build_pptx}


def build(ticket_type, plan, out_path):
    """Build one deliverable. Raises PlanInvalid for a type with no builder."""
    builder = BUILDERS.get(ticket_type)
    if builder is None:
        raise PlanInvalid(f"no builder for a {ticket_type} deliverable yet")
    return builder(plan, out_path)


def output_path(ticket, when=None):
    """Where this ticket's artifact lands, dated and named after the ticket."""
    when = when or datetime.now()
    name = f"{when.strftime('%Y-%m-%d')}-{_slug(ticket.get('title'))}.{ticket.get('type')}"
    return deliverables_dir() / name


# ── The stager ────────────────────────────────────────────────────────────────

_PROMPT = """You are planning a slide deck for {name}, who will present it.

The deck is: {title}

What finished looks like, in their words:
{dod}
{context}{comments}{ratings}
Return ONE JSON object and nothing else. No prose before or after it.

{{"title": "...", "subtitle": "...", "slides": [
  {{"heading": "...", "bullets": ["...", "..."], "note": "..."}}
]}}

Rules, all of them load-bearing:
- Between 3 and 12 slides. Every slide has a heading and at least one bullet.
- A bullet is one sentence a person says out loud, not a fragment and not a
  paragraph. Under 25 words.
- No em-dashes or en-dashes anywhere. Use a comma, a colon, or two sentences.
- Never write a placeholder, a bracketed instruction, or a note to the author.
  Every string ships to the reader exactly as you write it.
- Use only facts given above. If a number or a name is not here, do not invent
  one; write the slide without it.
- `note` is the speaker note for that slide, one or two sentences, optional.
"""


def _context_block(ticket):
    """The ticket's own evidence, rendered for the prompt.

    A staged deck is only as good as what it was given, so this block is where
    the knowledge graph reaches the artifact: a ticket carrying a `context_pack`
    hands the model sourced facts instead of leaving it to guess.
    """
    lines = []
    context = ticket.get("context") or {}
    for key in ("summary", "intent", "suggested_action"):
        value = _clean(context.get(key))
        if value:
            lines.append(f"- {value}")

    pack = ticket.get("context_pack") or {}
    for entry in (pack.get("open_commitments") or [])[:8]:
        item = _clean(entry.get("item"))
        if item:
            lines.append(f"- Still open: {item}")
    for entry in (pack.get("deal_context") or [])[:4]:
        text = _clean(entry.get("text"))
        if text:
            lines.append(f"- From the deal record: {text[:400]}")

    if not lines:
        return ""
    return "\nWhat is known:\n" + "\n".join(lines) + "\n"


def _comments_block(ticket):
    """Revision notes, which outrank everything else in the prompt.

    A comment is the user saying the last attempt was wrong. It goes last and
    is labelled as a correction so the model treats it as one.
    """
    notes = [_clean(c.get("text")) for c in (ticket.get("comments") or [])]
    notes = [n for n in notes if n]
    if not notes:
        return ""
    return ("\nThe user reviewed a previous attempt and asked for these changes. "
            "They take priority:\n" + "\n".join(f"- {n}" for n in notes) + "\n")


def _ratings_block(ratings):
    notes = [_clean(r.get("note")) for r in (ratings or []) if _clean(r.get("note"))]
    if not notes:
        return ""
    return ("\nWhat they said about earlier work:\n"
            + "\n".join(f"- {n}" for n in notes[:5]) + "\n")


def stage_plan(ticket, ratings=None, run=None, name=""):
    """Ask the model for a deck plan and return it validated.

    Raises PlanInvalid when the reply cannot be turned into a deck. The caller
    records that on the ticket rather than retrying blindly: a plan that failed
    validation twice is a prompt problem, not a transient one.
    """
    from config_loader import user_first_name

    if run is None:
        from claude_cli import run_claude as run

    prompt = _PROMPT.format(
        name=name or user_first_name() or "the user",
        title=_clean(ticket.get("title")) or "an untitled deck",
        dod=_clean(ticket.get("dod")) or "Not stated. Use your judgement.",
        context=_context_block(ticket),
        comments=_comments_block(ticket),
        ratings=_ratings_block(ratings),
    )
    result = run(prompt, model=OPUS_MODEL, timeout=180)
    if getattr(result, "returncode", 1) != 0:
        tail = ((result.stderr or "") + (result.stdout or ""))[-300:].strip()
        raise PlanInvalid(f"the model call failed: {tail or 'no output'}")
    return validate_plan(extract_json(result.stdout))



# ── Verification ──────────────────────────────────────────────────────────────

def verify_pptx(plan, path):
    """Reopen the deck and check it against the plan that asked for it.

    A build that reports success having written nothing is the failure worth
    catching, so this reads the file back rather than trusting the builder. It
    also refuses a deck carrying template prompt text: a placeholder that ships
    to a reader is the defect a gate suite never catches on its own.
    """
    from pptx import Presentation

    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        raise PlanInvalid("the build wrote no file")

    prs = Presentation(str(path))
    expected = len(plan["slides"]) + 1          # the cover
    actual = len(prs.slides)
    if actual != expected:
        raise PlanInvalid(f"the deck holds {actual} slides, the plan asked for {expected}")

    words = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                words.append(shape.text_frame.text)
    body = "\n".join(words)
    if not body.strip():
        raise PlanInvalid("the deck holds no text")
    for marker in ("[Blank]", "[Graphic]", "Lorem ipsum", "TODO", "TBD"):
        if marker.lower() in body.lower():
            raise PlanInvalid(f"the deck still carries placeholder text: {marker}")
    if "—" in body or "–" in body:
        raise PlanInvalid("the deck carries a dash that should be a comma")

    size_kb = path.stat().st_size // 1024
    return f"Wrote {actual} slides to {path.name}, {size_kb} KB."


VERIFIERS = {"pptx": verify_pptx}


def verify(ticket_type, plan, path):
    """Check a built deliverable. A type with no verifier is not deliverable."""
    verifier = VERIFIERS.get(ticket_type)
    if verifier is None:
        raise PlanInvalid(f"no verifier for a {ticket_type} deliverable yet")
    return verifier(validate_plan(plan), path)
