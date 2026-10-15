"""The two HTML surfaces a briefing gets, and the record of where its page lives.

`render_email` is the digest body, unchanged from `digest_html`. `render_page`
is the standalone web page: one permanent private page per briefing, rebuilt
and republished to the same URL on every run, so a link the user bookmarked
keeps showing today's briefing instead of the day it was created.

Both walk the same markdown. The only structural difference is a fold: the
digest cannot open one, so a callout becomes a shaded block there and a closed
<details> here.

Nothing in this module publishes anything. It renders, and it remembers the URL
a caller published to. The publishing itself is the skill's step, and it fails
open: a briefing that could not update its page is still a finished briefing.
"""
from __future__ import annotations

import json
import re
from html import escape

from digest_html import md_to_email_html

# ── The stored URL ───────────────────────────────────────────────────────────

_SIDECAR_NAME = "briefing_pages.json"
BRIEFINGS = ("morning-coffee", "afternoon-tea", "week", "week-retro")


def _sidecar_path():
    from config_loader import logs_dir
    return logs_dir() / _SIDECAR_NAME


def load_pages() -> dict:
    """Every briefing's stored page record. Unreadable means empty, never fatal."""
    try:
        with open(_sidecar_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def page_url(briefing: str) -> str:
    """The one URL this briefing owns, or "" if it has never been published."""
    return str((load_pages().get(briefing) or {}).get("url") or "")


def record_page(briefing: str, url: str, briefing_date: str,
                published: bool = True, reason: str = "") -> dict:
    """Write back what happened to this briefing's page on this run.

    Records the failure too, with its reason: the digest and the terminal both
    print that reason, and a failure nobody recorded is a page that silently
    stops updating.
    """
    if briefing not in BRIEFINGS:
        raise ValueError(f"unknown briefing {briefing!r}")
    pages = load_pages()
    prior = pages.get(briefing) or {}
    entry = {
        "url": url or prior.get("url", ""),
        "briefing_date": briefing_date if published else prior.get("briefing_date", ""),
        "last_attempt": briefing_date,
        "published": bool(published),
        "reason": "" if published else (reason or "the page could not be updated"),
    }
    pages[briefing] = entry
    path = _sidecar_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pages, f, indent=2, sort_keys=True)
    return entry


def link_line(briefing: str) -> str:
    """One sentence naming the page, when the page is current.

    The failure half of this existed from the start and the success half did
    not, so a run that published correctly said nothing at all and the reader
    had no link to follow. Empty when there is no page or the last attempt
    failed, so a caller can print it unconditionally beside `failure_line` and
    exactly one of the two speaks.
    """
    entry = load_pages().get(briefing) or {}
    if not entry.get("published") or not entry.get("url"):
        return ""
    return f"The page is up to date: {entry['url']}"


def page_line(briefing: str) -> str:
    """Whichever of the two lines applies. Never both, never neither wrongly."""
    return failure_line(briefing) or link_line(briefing)


def failure_line(briefing: str) -> str:
    """One sentence for the terminal and the digest when the page is behind.

    Empty when the page is current, so a caller can print it unconditionally.
    """
    entry = load_pages().get(briefing) or {}
    if not entry or entry.get("published"):
        return ""
    reason = entry.get("reason") or "the page could not be updated"
    shown = entry.get("briefing_date") or "an earlier day"
    if not entry.get("url"):
        return f"The web page did not update: {reason}. There is no page yet."
    return (f"The web page did not update: {reason}. "
            f"It still shows {shown}: {entry['url']}")


# ── Rendering ────────────────────────────────────────────────────────────────

def render_email(md: str) -> str:
    """The digest body. Same renderer the digest has always used."""
    return md_to_email_html(md)


_TOKENS = """
:root{
  --font-prose:"B612","Helvetica Neue",Arial,sans-serif;
  --font-mono:"B612 Mono","SF Mono",Menlo,"Cascadia Mono",Consolas,monospace;
  --bg:#F3F4F1; --card:#FFFFFF; --wash:#EEF0EE;
  --text:#1A1F24; --muted:#5C6670;
  --rule:#C9CFD3; --rule-soft:#E1E5E8;
  --amber:#9C5F0A; --cyan:#1F7A99; --red:#B8321F; --green:#2E7A4A;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#101418; --card:#171C22; --wash:#1D2329;
  --text:#E6E9EC; --muted:#8B95A0;
  --rule:#2A323B; --rule-soft:#222930;
  --amber:#E8A33D; --cyan:#5FB8D9; --red:#EA6A57; --green:#58B57A;
}}
:root[data-theme="dark"]{
  --bg:#101418; --card:#171C22; --wash:#1D2329;
  --text:#E6E9EC; --muted:#8B95A0;
  --rule:#2A323B; --rule-soft:#222930;
  --amber:#E8A33D; --cyan:#5FB8D9; --red:#EA6A57; --green:#58B57A;
}
"""

# The page is a stack of cards on a panel. A markdown h2 opens a card and the
# next one closes it, which is why `render_page` splits on h2 rather than
# letting the renderer emit them inline: a card needs a wrapper, and markdown
# has no wrapper. Everything else is the shared renderer's output, restyled.
_PAGE_CSS = """
body{background:var(--bg);color:var(--text);font-family:var(--font-prose);
  font-size:15px;line-height:1.45;margin:0;font-variant-numeric:tabular-nums}
/* 720px is the reading measure and it governs prose. The cards may use more
   when the window has it, because an item line is a row (subject left, filing
   right), not a paragraph, and a row does not get harder to read when wider. */
.wrap{max-width:720px;margin:0 auto;padding:20px 16px 72px}
@media (min-width:900px){
  .wrap{max-width:1000px}
}
.ph{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;
  flex-wrap:wrap;padding:0 0 10px;border-bottom:2px solid var(--text);margin-bottom:12px}
h1{font-size:26px;line-height:1.1;font-weight:700;margin:0}
.title{min-width:0}
.phase{display:block;font-family:var(--font-mono);font-size:11px;font-weight:700;
  letter-spacing:.1em;text-transform:uppercase;color:var(--amber);margin-bottom:4px}
.meta{font-family:var(--font-mono);font-size:11px;line-height:1.5;
  letter-spacing:.1em;text-transform:uppercase;color:var(--muted);
  margin:0;text-align:right}
/* The one recommendation on a page of obligations, and the only thing here
   nobody is asking the reader for. It takes no status colour: amber means
   waiting on you and this is not owed, cyan means waiting on them and nobody
   is holding it. So it is marked by weight and a rule in the ink colour, which
   is what separates a recommendation from an obligation without pretending to
   be one. */
.card.focus{border-left:3px solid var(--text)}
.card.focus>p:first-of-type{font-size:16px;line-height:1.5;font-weight:700}
/* Travel: outlined whole, not ruled down one edge, because this is the one
   card whose deadline the reader cannot move. Amber is already "waiting on
   you" throughout the product, so the outline says what the colour says. */
.card.travel{border:1.5px solid var(--amber)}
.card.travel>p:first-of-type{font-weight:600}
.card.travel li{font-weight:600}
/* The read leads; the counts sentence under it is a caption, not a second
   paragraph of equal weight. */
.lead{padding-top:14px}
.lead>p:first-child{font-size:16.5px;line-height:1.5}
.lead>p:first-child+p{font-family:var(--font-mono);font-size:11.5px;
  line-height:1.6;letter-spacing:.04em;color:var(--muted);
  border-top:1px solid var(--rule-soft);margin-top:12px;padding-top:10px}
/* The rows want the width (at 760px three subjects wrap, at 940 none do) and
   prose wants a measure (the read is 81 characters at 720 and 102 at full
   width, too long a line to track). Capping the paragraph left 265px of dead
   card beside it, which reads as a layout that failed rather than a measure
   that was chosen. So the read sets larger and looser instead: it fills the
   card, and the size buys back the tracking that the length costs.
   This block sits AFTER the base rules on purpose: same specificity, so the
   later one wins, and written above them the size never applied. */
@media (min-width:900px){
  /* The focus card is prose too, and hit the same 62ch wall the read did. */
  .card.focus>p{max-width:none}
  /* 62ch on every p is the real constraint on the read, not the wrap width:
     it is why the paragraph stopped 265px short of the card edge. The measure
     rule is right for body prose and wrong for the one paragraph that IS the
     card, so the lead overrides it and sets larger to keep the line trackable
     at that length. */
  .lead>p{max-width:none}
  .lead>p:first-child{font-size:19px;line-height:1.6}
}
.card{background:var(--card);border:1px solid var(--rule);border-radius:2px;
  margin:0 0 12px;padding:0 14px 12px}
.card:empty{display:none}
.cbar{display:flex;justify-content:space-between;align-items:baseline;gap:10px;
  font-family:var(--font-mono);font-size:11px;font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;padding:9px 0 7px;border-bottom:1px solid var(--rule);
  margin:0 0 8px}
h2{font:inherit;margin:0;flex:1}
h3{font-family:var(--font-mono);font-size:11px;font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);margin:12px 0 4px}
p{margin:6px 0;max-width:62ch}
.lead{font-size:15px;line-height:1.45;margin:0 0 6px}
.stale{font-family:var(--font-mono);font-size:11px;line-height:1.45;
  letter-spacing:.06em;background:var(--card);color:var(--red);
  border:1px solid var(--red);border-radius:2px;padding:9px 12px;margin:0 0 12px}
hr{border:none;border-top:1px solid var(--rule-soft);margin:12px 0}
a{color:var(--cyan)}
strong{font-weight:700}
code{font-family:var(--font-mono);font-size:12.5px;background:var(--wash);
  padding:1px 5px;border-radius:2px}
details{border:0;border-bottom:1px solid var(--rule-soft);margin:0;padding:0}
/* The summary keeps list-item display. Any other value drops the native
   toggle in WebKit: the fold takes focus and opens on Enter but ignores a
   click. The flex row lives on an inner span instead. */
details>summary{font-family:var(--font-mono);font-size:12px;font-weight:700;
  letter-spacing:.06em;text-transform:uppercase;cursor:pointer;padding:8px 0;
  display:list-item;list-style:none}
details>summary::-webkit-details-marker{display:none}
details>summary>.sumrow{display:flex;gap:10px;align-items:center}
details>summary>.sumrow::before{content:"";width:0;height:0;flex:none;
  border-left:5px solid var(--muted);border-top:4px solid transparent;
  border-bottom:4px solid transparent;transition:transform 140ms ease-out}
details[open]>summary>.sumrow::before{transform:rotate(90deg)}
details .foldbody{padding:0 0 10px;font-size:14px}
ul,ol{margin:6px 0;padding-left:1.15em}
li{margin:0 0 3px}
/* A checkbox is the bullet. Rendering both puts a dot beside every box. */
ul.tasks{list-style:none;padding-left:0}
li.task{display:grid;grid-template-columns:16px minmax(0,1fr);column-gap:10px;
  align-items:baseline;padding:2px 0;border-bottom:1px solid var(--rule-soft)}
li.task:last-child{border-bottom:0}
/* The box is drawn at 16px but its label carries the target: the reader ticks
   items off fifteen times a session, and a 12px box is half the 24px minimum. */
li.task input{margin:0;accent-color:var(--green);width:16px;height:16px;
  position:relative;top:3px;flex:none}
li.task label{display:block;padding:6px 0;min-height:24px}
/* What the item says, then what files it. Same characters, two ranks: at one
   weight a 124-character line is a wall and the subject has to be hunted for. */
li.task label .what{display:block}
li.task label .filed{display:block;margin-top:2px;font-family:var(--font-mono);
  font-size:11px;letter-spacing:.03em;color:var(--muted)}
/* Lateness is what the page was ranked on, so it does not set at the weight of
   a filing tag. --red is the system's colour for late.

   Scoped to the filing half of a front-page task, this rule could only ever
   fire on the two briefings that emit that shape. The Week and Week Retro write
   plain bullets, so "2d overdue" and "81d since contact" rendered in ink on the
   two pages that exist to report decay. The mark is now the selector, wherever
   it lands, and inside `.filed` it keeps the same treatment it always had. */
.late{color:var(--red);font-weight:700}
@media (min-width:900px){
  /* Wide enough for both on one line: the subject takes the reading column and
     the filing sets to the right, so the eye lands on the subject every time.

     Scoped with :has(), because the two ranks only exist when the renderer
     actually split them. The Week writes its items as one sentence with no
     filing tail, and an unscoped grid treated that label's own children as
     columns: the `[DEAL]` tag took column one, the bolded deal name was thrown
     to the right margin, and the sentence wrapped to a second row beginning
     with an orphaned colon. The eye landed on the tag instead of the work. */
  li.task label:has(.filed){display:grid;grid-template-columns:minmax(0,1fr) auto;
    column-gap:18px;align-items:baseline}
  li.task label .filed{margin-top:0;text-align:right}
}
li.task.done label{color:var(--muted);text-decoration:line-through;
  text-decoration-color:var(--rule)}
.tablewrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;margin:6px 0 10px;
  font-family:var(--font-mono);font-size:12.5px}
th{text-align:left;font-size:10.5px;font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);padding:5px 8px 5px 0;
  border-bottom:1px solid var(--text)}
td{padding:6px 8px 6px 0;border-bottom:1px solid var(--rule-soft);
  vertical-align:top;text-align:left}
@media (max-width:560px){
  /* On a phone the first checkbox sat at y=1039 in an 844px viewport: a screen
     and a quarter of prose at 7am before anything could be pressed. The focus
     card is a recommendation, not an obligation, so it is the one card that can
     afford to open folded to its own first line. Nothing is hidden: the reader
     presses it and gets the whole thing. */
  .card.focus>p:not(:first-of-type){display:none}
  .card.focus.open>p{display:block}
  .card.focus>p:first-of-type::after{content:" More";font-family:var(--font-mono);
    font-size:10.5px;font-weight:700;letter-spacing:.1em;color:var(--muted)}
  .card.focus.open>p:first-of-type::after{content:" Less"}
  .card.focus{cursor:pointer}

  .wrap{padding:12px 10px 56px}
  .ph{flex-direction:column;align-items:flex-start}
  .meta{text-align:left}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""


PHASES = {
    "morning-coffee": "Departure checklist",
    "afternoon-tea": "Shutdown checklist",
    "week": "Flight plan",
    "week-retro": "Debrief",
}

_H2 = re.compile(r"^##\s+(.+?)\s*$", re.M)


_FOCUS_RE = re.compile(r"\b(suggested focus task|one big rock)\b", re.I)
# The travel card sits directly under the focus task, outlined in amber. It is
# the one block on the page carrying a deadline the reader cannot renegotiate:
# a flight leaves whether or not the briefing was read carefully. Everything
# else on the page can slip a day; this cannot, so it is the one block that
# gets an outline rather than a rule down one edge.
_TRAVEL_RE = re.compile(r"^\s*travel\b", re.I)
# Drafts is marked but not outlined. The class exists so the Workbench can find
# the card and turn each waiting reply into a button; it carries no colour,
# because amber is reserved for the deadline that cannot move.
_DRAFTS_RE = re.compile(r"^\s*drafts\b", re.I)


def _cards(body_html: str) -> str:
    """Wrap each section in a card, with its heading as the card's bar.

    Markdown has no wrapper element, so a heading has to open one and the next
    heading has to close it. Anything before the first heading (the opening
    sentence) gets its own card, because a paragraph loose on the panel reads
    as a mistake.

    Both h2 and h3 open a card. The front-page renderer writes `### DO TODAY`
    and `### THE REST` while the skill writes `## Drafts` and `## Your day` into
    the same file, so keying only on h2 put a real briefing's whole front page
    inside one card and its section bars nowhere.
    """
    parts = re.split(r"(<h[23]>.*?</h[23]>)", body_html, flags=re.S)
    out, open_card = [], False
    for part in parts:
        m = re.fullmatch(r"<h([23])>(.*?)</h\1>", part, re.S)
        if m:
            if open_card:
                out.append("</section>")
            # The focus card is the one recommendation on a page of
            # obligations, so it is marked and styled apart. Matched on the
            # heading text because the skill writes it, not the renderer.
            title = m.group(2)
            plain = re.sub("<[^>]+>", "", title)
            focus = "focus" if _FOCUS_RE.search(plain) else ""
            if not focus and _TRAVEL_RE.search(plain):
                focus = "travel"
            elif not focus and _DRAFTS_RE.search(plain):
                focus = "drafts"
            out.append(f'<section class="card {focus}"><div class="cbar">'
                       f"<h2>{title}</h2></div>")
            open_card = True
        elif part.strip():
            if not open_card:
                # The card before the first heading holds the read and, under
                # it, the counts sentence the code writes. Set at one weight
                # they read as one four-sentence block; the read is prose and
                # the counts are a caption, so they are typeset that way.
                out.append('<section class="card lead">')
                open_card = True
            out.append(part)
    if open_card:
        out.append("</section>")
    return "".join(out)


def render_page(md: str, meta: dict | None = None) -> str:
    """The standalone page. A stack of cards; folds are real, closed <details>.

    `meta` carries `title`, `briefing_date`, `newest_date`, and `briefing`:
    when the page is older than the newest briefing on disk it says so at the
    top, because a bookmarked link that quietly shows last Tuesday is worse
    than no link. `briefing` picks the phase label over the title.
    """
    from workbench_data import render_markdown
    meta = meta or {}
    title = str(meta.get("title") or "Briefing")
    bdate = str(meta.get("briefing_date") or "")
    newest = str(meta.get("newest_date") or "")
    phase = PHASES.get(str(meta.get("briefing") or ""), "")

    # The body usually opens with its own "# Morning Coffee". Printing the
    # meta title above it showed the name twice, one line apart.
    lines = md.splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if line.strip().lower() == f"# {title}".lower():
            md = "\n".join(lines[i + 1:])
        break

    # render_markdown splits the callouts itself and threads one checkbox
    # counter through the whole document. Splitting here and calling it once
    # per chunk restarted that counter at every fold, so `ck0` appeared four
    # times: duplicate ids, and a <label for> that points at the first one
    # silently stops labelling the rest.
    body = _cards(render_markdown(md))

    banner = ""
    if newest and bdate and newest > bdate:
        banner = (f'<div class="stale">This page shows {escape(bdate)}. '
                  f"A newer briefing ({escape(newest)}) has been written since, "
                  "and this page did not update.</div>")

    phase_line = f'<span class="phase">{escape(phase)}</span>' if phase else ""
    meta_line = f'<p class="meta">{escape(bdate)}</p>' if bdate else ""
    return (f"<title>{escape(title)}</title>\n<style>{_TOKENS}{_PAGE_CSS}</style>\n"
            f'<div class="wrap">\n<header class="ph"><div class="title">{phase_line}'
            f"<h1>{escape(title)}</h1></div>"
            f"{meta_line}</header>{banner}\n{body}\n</div>\n")


# ── Redaction ────────────────────────────────────────────────────────────────
#
# The page is private by default, but "private" is a setting and a leaked token
# is forever. Everything that looks like a credential comes out of the body
# before anything is published.

_REDACTIONS = [
    (re.compile(r"\b(ya29|1//)[A-Za-z0-9._\-]{10,}"), "[redacted token]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "[redacted key]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{16,}"),
     "[redacted key]"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"), "[redacted key]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted key]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
     "[redacted token]"),
    (re.compile(r"\b[A-Za-z0-9_]*(?:api[_\-]?key|secret|refresh[_\-]?token|"
                r"client[_\-]?secret|password)[A-Za-z0-9_]*\s*[:=]\s*"
                r"[\"']?[A-Za-z0-9/+._\-]{8,}[\"']?", re.IGNORECASE),
     "[redacted credential]"),
    # Two backslashes in a raw string, not four. Four is what a regex needs to
    # match two literal backslashes, and a Windows path has one, so the branch
    # existed and could never fire: C:\Users\... shipped verbatim onto a page.
    (re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\Users\\)[^\s\"'<>|]+"),
     "[local path]"),
]


def redact(text: str) -> str:
    """Strip anything credential-shaped, and local filesystem paths, from a body."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text
