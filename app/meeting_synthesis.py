#!/usr/bin/env python3
"""meeting_synthesis.py: read a meeting transcript, write the eight sections.

The notetaker's own summary is a lossy artifact of somebody else's model: it
decides what mattered, drops the hedge around a commitment, and never reports
who was uneasy about what. This module reads the TRANSCRIPT instead and answers
eight questions the vault actually needs (SECTIONS below).

The transcript is never stored. It is fetched, fed to the model, and dropped;
the source page keeps the synthesis. It stays re-fetchable from the notetaker
by `meeting_id`, so nothing is lost that a second API call cannot restore, and
raw speech (which carries far more incidental PII than a summary) never lands
on disk.

Failure is always degradation, never a crash: `synthesize()` returns None when
the transcript is missing, the CLI fails, or the reply will not parse, and the
caller falls back to the provider's summary. Model-side failures are retried
once, and `LAST_FAILURE` says why the last call fell back. A meeting filed from the vendor
summary is worse than one filed from its transcript; a meeting not filed at all
is worse than both.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import claude_cli
import config_loader
import notetaker

# Synthesis is a judgment task, not a classification one: the last two sections
# ask the model to read tone and subtext, which Haiku does poorly. Sonnet is the
# floor for that. claude_cli.HAIKU_MODEL stays what it is for classification.
SYNTHESIS_MODEL = "claude-sonnet-5"

# A long meeting is a long call, not a runaway: 90 minutes of speech is ~18k
# tokens and must not be truncated into a synthesis that silently omits the last
# half hour. The cap exists only to stop a pathological note (a recorder left
# running overnight) from costing a fortune, and truncation is REPORTED rather
# than silent, so a caller can tell a partial synthesis from a whole one.
MAX_TRANSCRIPT_CHARS = 400_000

# A short transcript is a failed recording, not a short meeting. Below this the
# model is guessing from nothing, which is how a confident, wrong synthesis gets
# written into the vault, so the caller falls back to the provider summary.
MIN_TRANSCRIPT_CHARS = 400

# The model's reply is not deterministic: the same transcript parses on most
# calls and comes back unusable on the odd one. A single failed call used to
# drop the meeting to the vendor summary with no reason given, which read as a
# broken feature. One retry covers the model-side failures; a missing or short
# transcript is never retried, because asking again cannot change it.
SYNTHESIS_ATTEMPTS = 2

# Why the last synthesize() call returned None, in plain words, or "" when it
# succeeded. The caller prints it, so a fallback always carries its cause.
LAST_FAILURE = ""

# The eight sections, in page order. `key` is the JSON field; `heading` is the
# markdown heading; `empty` is what the section says when it genuinely has
# nothing, which is never the same sentence as an error.
SECTIONS = [
    ("discussed", "What Was Discussed",
     "%% Nothing substantive discussed. %%"),
    ("decisions", "Decisions Made",
     "%% No decisions recorded. %%"),
    ("changes", "Changes From Previous Plans",
     "%% No changes from previous plans. %%"),
    ("my_commitments", "What You Owe",
     "%% No commitments made by you. %%"),
    ("their_commitments", "What Others Owe You",
     "%% Nothing owed to you. %%"),
    ("next_steps", "Deadlines, Owners and Next Steps",
     "%% No deadlines or next steps named. %%"),
    ("open_questions", "Open Questions",
     "%% No open questions. %%"),
    ("subtext", "Tension, Disagreement and Subtext",
     "%% Nothing notable. %%"),
]

# The two sections that become vault checkboxes rather than prose. Everything
# else is bullets the reader skims.
LIST_KEYS = {"my_commitments", "their_commitments", "next_steps"}


def _extract_json_object(raw):
    """First balanced JSON object in the reply, or None.

    The model prefixes prose despite being told not to, and a fence-strip that
    only handles an opening ``` misses the trailing paragraph. This scans for a
    balanced object from BOTH ends rather than trusting the payload to start at
    byte 0. Returns None when there is nothing parseable: a degraded run, not a
    crash.
    """
    depth = 0
    start = None
    in_str = False
    esc = False
    for i, ch in enumerate(raw or ""):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    # strict=False accepts a raw newline or tab inside a
                    # string, which the model writes into long prose fields.
                    return json.loads(raw[start:i + 1], strict=False)
                except ValueError:
                    return None
    return None


# Some notetakers do not say who spoke, they say which audio channel the words
# came in on: the user's own microphone, or the far side of the call. Handed
# to the model unexplained, the two labels are a coin toss, and the toss
# decides which side of the table every commitment lands on. A call where the
# user owed three things was filed as the counterparty owing all three.
_CHANNEL_LABEL_RE = re.compile(r"(?m)^\[(?:microphone|speaker)\] ")


def channel_legend(transcript, user_name):
    """What the channel labels mean, or "" when the transcript names speakers."""
    if not _CHANNEL_LABEL_RE.search(transcript or ""):
        return ""
    return (
        "\nHow to read the labels: this recording does not name who spoke, it "
        "names the audio channel.\n"
        f"- [microphone] is {user_name}'s own microphone. On a remote call every "
        f"[microphone] line is {user_name} speaking. Only when the meeting was "
        "held in one room can it also carry the people sitting with them.\n"
        "- [speaker] is the far side of the call: everyone else, not told "
        "apart. Work out who is talking from what is said.\n"
        f"Never give a [microphone] line to someone on the far side, and never "
        f"give a [speaker] line to {user_name}. A promise made on [microphone] "
        f"is {user_name}'s own.\n"
    )


def build_prompt(transcript, title, date, attendees, user_name, truncated=False):
    """The synthesis prompt.

    Written in plain language because the output is read by a person. The
    model is told WHO the user is by name: "what am I responsible for" is
    unanswerable without knowing which speaker is the reader, and getting that
    backwards puts a counterparty's promise on the user's own to-do list, which
    is the expensive kind of wrong.
    """
    who = ", ".join(attendees) if attendees else "(not recorded)"
    note = ""
    if truncated:
        note = ("\nNOTE: this transcript was too long to include whole and has "
                "been cut. Say so in `discussed` rather than implying the "
                "meeting ended where the text does.\n")
    note += channel_legend(transcript, user_name)
    return f"""You are reading the transcript of a meeting so that {user_name} can
file what actually happened. You are writing for {user_name} to read later,
when the details have gone.

Meeting: {title}
Date: {date}
Attendees: {who}
The reader is: {user_name}
{note}
Answer these eight questions from the transcript. Return ONE JSON object, no
prose before or after it, with exactly these keys:

  "discussed":          string. What was actually talked about, in a short
                        paragraph or a few sentences. Substance, not an agenda.
  "decisions":          array of strings. Decisions that were actually MADE.
                        A decision is settled; something still being weighed is
                        an open question, not a decision.
  "changes":            array of strings. Anything that changed from what was
                        previously planned, assumed or agreed. Include what it
                        was before and what it is now. This is the section
                        people most often miss.
  "my_commitments":     array of strings. What {user_name} personally committed
                        to. Only {user_name}'s own commitments. Include who it
                        is owed to and any date, e.g. "Send the term sheet to
                        Jane Smith by Friday".
  "their_commitments":  array of strings. What OTHER people committed to doing,
                        for {user_name} or for the group. Name the person.
  "next_steps":         array of strings. Deadlines, owners and next steps, each
                        naming the owner and the date where one was given.
  "open_questions":     array of strings. Questions raised and left unanswered,
                        and decisions deferred.
  "subtext":            array of strings. Disagreement, tension, hesitation,
                        reluctance or anything left unsaid that {user_name}
                        should notice. A hedge ("I guess we could"), a topic
                        nobody picked up, someone going quiet, two people
                        wanting different things without saying so. Be concrete
                        and quote the words where you can. Do NOT invent
                        conflict: if the meeting was genuinely straightforward,
                        return an empty array.

Rules:
- Every array may be empty. An empty array is a real answer. Never pad a
  section to look thorough.
- Never state something the transcript does not support. If you are inferring,
  say so in the line itself ("sounds like", "may be").
- Use the speakers' own words where quoting helps.
- Do not use em dashes or en dashes anywhere in your output.
- Return only the JSON object.

TRANSCRIPT:
{transcript}
"""


def _as_list(value):
    """A JSON field that should be a list, whatever the model returned.

    A model told "array of strings" sometimes returns one string. Coercing here
    means one bad field costs its own shape, never the whole synthesis.
    """
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _as_text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " ".join(str(v).strip() for v in value if str(v).strip())
    return ""


def synthesize(note, user_name=None, model=SYNTHESIS_MODEL, timeout=600):
    """Transcript → the eight sections, or None to fall back to the summary.

    Returns a dict keyed by SECTIONS keys plus "truncated", or None. None is
    the honest answer for every failure: no transcript, a transcript too short
    to be a real recording, a CLI failure, or a reply that will not parse.
    """
    global LAST_FAILURE
    LAST_FAILURE = ""
    transcript = notetaker.flatten_transcript(note.get("transcript"))
    if not transcript:
        LAST_FAILURE = "the notetaker returned no transcript"
        return None
    if len(transcript) < MIN_TRANSCRIPT_CHARS:
        LAST_FAILURE = (f"the transcript is {len(transcript)} characters, "
                        "too short to be a real recording")
        return None

    truncated = len(transcript) > MAX_TRANSCRIPT_CHARS
    if truncated:
        transcript = transcript[:MAX_TRANSCRIPT_CHARS]

    # The transcript is PII-stripped before it reaches the model, on the same
    # terms the canonical shape already applies to summaries.
    transcript = notetaker.strip_pii(transcript)

    if not user_name:
        try:
            user_name = config_loader.user_name()
        except Exception:
            user_name = "the reader"

    prompt = build_prompt(
        transcript,
        note.get("title") or "Untitled Meeting",
        notetaker.note_date(note) or "(unknown)",
        notetaker.attendee_names(note),
        user_name or "the reader",
        truncated=truncated,
    )

    for attempt in range(1, SYNTHESIS_ATTEMPTS + 1):
        out = _one_attempt(prompt, model, timeout, truncated)
        if out is not None:
            LAST_FAILURE = ""
            return out
    LAST_FAILURE += f" (tried {SYNTHESIS_ATTEMPTS} times)"
    return None


def _one_attempt(prompt, model, timeout, truncated):
    """One model call. The eight sections, or None with LAST_FAILURE set."""
    global LAST_FAILURE
    try:
        result = claude_cli.run_claude(prompt, model=model, timeout=timeout)
    except Exception as e:
        # The exception TYPE only: its text can quote the prompt.
        LAST_FAILURE = f"the model call failed ({type(e).__name__})"
        return None
    if result.returncode != 0:
        LAST_FAILURE = f"the model call exited {result.returncode}"
        return None

    parsed = _extract_json_object(result.stdout or "")
    if not isinstance(parsed, dict):
        LAST_FAILURE = "the model's reply was not readable JSON"
        return None

    out = {"truncated": truncated}
    for key, _heading, _empty in SECTIONS:
        out[key] = _as_list(parsed.get(key)) if key != "discussed" \
            else _as_text(parsed.get(key))

    # A synthesis whose every section is empty is a failed read wearing a
    # valid shape, not a quiet meeting: fall back rather than file a page of
    # "nothing recorded" over a transcript that plainly held something.
    if not out["discussed"] and not any(out[k] for k, _h, _e in SECTIONS
                                        if k != "discussed"):
        LAST_FAILURE = "the model's reply had every section empty"
        return None
    return out


def render_markdown(synth):
    """The eight sections as vault markdown.

    Commitments and next steps render as `- [ ]` checkboxes because the vault's
    action-item pipeline reads those; everything else renders as bullets.
    """
    parts = []
    if synth.get("truncated"):
        parts.append("%% Transcript was too long to read whole; "
                     "this synthesis covers the earlier part of the call. %%")
        parts.append("")
    for key, heading, empty in SECTIONS:
        parts.append(f"## {heading}")
        parts.append("")
        value = synth.get(key)
        if key == "discussed":
            parts.append(value.strip() if value else empty)
        elif value:
            marker = "- [ ] " if key in LIST_KEYS else "- "
            parts.extend(f"{marker}{item}" for item in value)
        else:
            parts.append(empty)
        parts.append("")
    return "\n".join(parts)


def action_items_from(synth):
    """The user's own commitments, for the vault's action-item pipeline.

    Only `my_commitments`. Raising a counterparty's promise as something the
    user owes is the expensive kind of wrong, and `their_commitments` lives on
    the page for reading, never as one of the reader's own tasks.
    """
    return list(synth.get("my_commitments") or [])
