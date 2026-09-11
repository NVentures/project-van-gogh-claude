#!/usr/bin/env python3
"""The scorecard email: six cards, an honest opening, and one thing to try.

Email is a hostile rendering target and the rules come from that, not from
taste. Gmail strips a `<style>` block, Outlook renders through Word and ignores
`max-width` on a div, and neither has ever supported flexbox. So this is nested
tables with explicit widths and every style inline, the way `digest_html`
already does it, with the Kneeboard palette from DESIGN.md on top.

Two rules are load-bearing beyond the layout.

**Amber cannot appear here.** In this product amber means work waiting on the
reader and nothing else. A scorecard is a record of what already happened, so
there is nothing on this page for it to mean. `PALETTE` is a closed set that
excludes it, and every colour written into the HTML comes from that set, which
is what stops a plausible near-amber creeping in later.

**A number never appears without its direction.** "31" and even "31, down from
54" leave the reader to work out whether down is good. Every card states the
movement in words, and the colour only agrees with what the words already say,
so the page survives greyscale and colour blindness intact.
"""

from __future__ import annotations

from html import escape

# The only colours allowed in this email. Amber is deliberately absent: see the
# module docstring. A test asserts the rendered HTML uses nothing outside this.
PALETTE = {
    "bg": "#F3F4F1",
    "card": "#FFFFFF",
    "wash": "#EEF0EE",
    "text": "#1A1F24",
    "muted": "#5C6670",
    "rule": "#C9CFD3",
    "rule_soft": "#E1E5E8",
    "green": "#2E7A4A",
    "red": "#B8321F",
    "cyan": "#1F7A99",
}

# B612 is the product's face but no email client will load a web font, and a
# font file cannot be embedded in a way Outlook respects. The stacks name it
# first for anyone who has it and fall back to faces that exist everywhere.
FONT = "'B612','Helvetica Neue',Helvetica,Arial,sans-serif"
MONO = "'B612 Mono','SF Mono',Menlo,Consolas,'Courier New',monospace"

CARD_WIDTH = 268          # two cards plus the gutter inside a 640px table
BAR_WIDTH = 150           # the comparison bar's track, in px


def _tone(direction: str) -> str:
    """The colour a movement is drawn in. Only ever agrees with the words."""
    if direction == "better":
        return PALETTE["green"]
    if direction == "worse":
        return PALETTE["red"]
    return PALETTE["muted"]


def _bar(fraction: float, colour: str) -> str:
    """One comparison bar as a table, because a div with a width is not
    reliable in Outlook. Clamped so a value above its comparator cannot draw
    past the track and read as a wider number than it is."""
    fraction = max(0.0, min(1.0, fraction))
    filled = max(2, int(round(BAR_WIDTH * fraction)))
    rest = max(0, BAR_WIDTH - filled)
    cells = (f'<td width="{filled}" style="width:{filled}px;height:6px;'
             f'background:{colour};font-size:0;line-height:0">&nbsp;</td>')
    if rest:
        cells += (f'<td width="{rest}" style="width:{rest}px;height:6px;'
                  f'background:{PALETTE["rule_soft"]};font-size:0;'
                  f'line-height:0">&nbsp;</td>')
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'border="0" width="{BAR_WIDTH}" style="width:{BAR_WIDTH}px;'
            f'border-collapse:collapse"><tr>{cells}</tr></table>')


def _bar_row(label: str, value_text: str, fraction: float, colour: str) -> str:
    """One labelled bar. The VALUE is deliberately not repeated here.

    "2 / down from 5" at the top of a card and "latest week 2 / week before 5"
    at the bottom is the same pair of numbers twice in six lines. The bars
    carry the shape of the change; the movement line carries the numbers.
    """
    del value_text                      # kept in the signature for callers
    return (
        f'<tr><td style="padding:2px 0;font-family:{MONO};font-size:11px;'
        f'color:{PALETTE["muted"]};white-space:nowrap" width="82">'
        f'{escape(label)}</td>'
        f'<td style="padding:2px 6px">{_bar(fraction, colour)}</td>'
        f'</tr>'
    )


def _comparison(kpi: dict) -> str:
    """Two bars, this week over last, scaled to whichever is larger.

    Scaling to the larger of the pair rather than to a target means the two
    bars are comparable with each other, which is the only comparison the card
    is making. A missing prior draws one bar, never a fake baseline.
    """
    try:
        value = float(kpi.get("value") or 0)
    except (TypeError, ValueError):
        return ""
    prior_raw = kpi.get("prior")
    colour = _tone(kpi.get("direction") or "none")
    if prior_raw is None:
        # No comparator, so no bars. One full-width bar labelled "latest"
        # compares a number with itself and reads as a broken chart.
        return ""
    try:
        prior = float(prior_raw)
    except (TypeError, ValueError):
        return ""
    # A chart of 0 against 1 is decoration: the movement line above already
    # said "down from 1" and two nearly empty tracks add nothing a reader can
    # use. Bars earn their space only once the numbers are big enough to have
    # a shape.
    if max(abs(value), abs(prior)) < 3:
        return ""
    top = max(abs(value), abs(prior), 1e-9)
    rows = _bar_row("latest week", kpi.get("value_text") or "",
                    abs(value) / top, colour)
    rows += _bar_row("week before", kpi.get("prior_text") or "",
                     abs(prior) / top, PALETTE["rule"])
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'border="0" width="100%">{rows}</table>')


def _card(kpi: dict) -> str:
    """One KPI card. An unmeasured KPI gets the same frame and says why."""
    label = escape(kpi.get("name") or "")
    inner = [
        f'<div style="font-family:{MONO};font-size:11px;letter-spacing:0.1em;'
        f'text-transform:uppercase;color:{PALETTE["muted"]};'
        f'padding-bottom:8px">{label}</div>'
    ]

    if not kpi.get("measured"):
        # The reason IS the message. A "Not enough yet." headline above a
        # reason that also begins "not enough" is one fact twice.
        reason = kpi.get("reason") or "not measured this time"
        inner.append(
            f'<div style="font-family:{FONT};font-size:15px;'
            f'color:{PALETTE["muted"]};line-height:1.45">'
            f'{escape(reason[:1].upper() + reason[1:])}</div>')
    else:
        colour = _tone(kpi.get("direction") or "none")
        note = kpi.get("unit_note") or ""
        note_html = (f'<span style="font-size:13px;color:{PALETTE["muted"]};'
                     f'padding-left:6px">{escape(note)}</span>' if note else "")
        inner.append(
            f'<div style="font-family:{MONO};font-size:38px;line-height:1;'
            f'color:{PALETTE["text"]};padding-bottom:6px">'
            f'{escape(kpi.get("value_text") or "")}{note_html}</div>')

        # The movement, in words before colour. A reader in greyscale loses
        # nothing here, which is the rule DESIGN.md sets for every status.
        # The movement phrase either ENDS in a preposition and takes the prior
        # value ("down from" + "5"), or is already a complete clause and must
        # not have a number stapled to it. Appending unconditionally produced
        # "holding above three 3.5", which is not a sentence.
        words = kpi.get("direction_text") or ""
        prior_text = kpi.get("prior_text") or ""
        if words.endswith(" from"):
            movement = f"{words} {prior_text}".strip() if prior_text else words
        elif words:
            movement = words
        else:
            movement = "no comparison yet"
        # No judgement here: the verdict line below states whether the target
        # was met, and saying "the way you want" above "Met." is one fact
        # twice. It also let a move TOWARD a target read as the wrong way.
        good = ""
        inner.append(
            f'<div style="font-family:{FONT};font-size:13px;color:{colour};'
            f'padding-bottom:2px">{escape(movement)}{escape(good)}</div>')

        if kpi.get("detail"):
            inner.append(
                f'<div style="font-family:{FONT};font-size:13px;'
                f'color:{PALETTE["text"]};padding-bottom:2px">'
                f'{escape(kpi["detail"])}</div>')

        # The count this was measured over, but ONLY when it says something the
        # headline does not. "2" above "2 open now" is the same fact twice, and
        # a cold reader counted three renderings of one number on every card.
        n_text = kpi.get("n_text") or ""
        value_text = kpi.get("value_text") or ""
        if n_text and not n_text.startswith(value_text + " "):
            inner.append(
                f'<div style="font-family:{MONO};font-size:11px;'
                f'color:{PALETTE["muted"]};padding-bottom:8px">'
                f'{escape(n_text)}</div>')

        comparison = _comparison(kpi)
        if comparison:
            inner.append(comparison)

        # The verdict against the target, said out loud. Without it a card can
        # show "down from 3.5, the wrong way" above a target the number
        # actually meets, and a reader who spots that stops believing the
        # other five cards. Movement and verdict are two different questions
        # and the card now answers both.
        met = kpi.get("met")
        if met is True:
            verdict, verdict_colour = "Met.", PALETTE["green"]
        elif met is False:
            verdict, verdict_colour = "Not yet.", PALETTE["muted"]
        else:
            verdict, verdict_colour = "", PALETTE["muted"]
        target_line = (f'<span style="color:{verdict_colour}">{verdict}</span> '
                       if verdict else "")
        inner.append(
            f'<div style="font-family:{FONT};font-size:12px;'
            f'color:{PALETTE["muted"]};padding-top:8px">{target_line}'
            f'Target: {escape(kpi.get("target_text") or "")}</div>')

        if kpi.get("measured_as"):
            inner.append(
                f'<div style="font-family:{FONT};font-size:12px;'
                f'color:{PALETTE["muted"]};padding-top:4px;font-style:italic">'
                f'Counted as: {escape(kpi["measured_as"])}</div>')

    body = "".join(inner)
    return (
        f'<td width="{CARD_WIDTH}" valign="top" '
        f'style="width:{CARD_WIDTH}px;padding:0 0 16px 0">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="100%" style="background:{PALETTE["card"]};'
        f'border:1px solid {PALETTE["rule"]};border-radius:2px">'
        f'<tr><td style="padding:16px">{body}</td></tr></table></td>'
    )


def _card_rows(kpis: list) -> str:
    """Cards two to a row. Each row is its own table so a narrow client that
    drops to one column still gets whole cards rather than a torn grid."""
    rows = []
    for i in range(0, len(kpis), 2):
        pair = kpis[i:i + 2]
        cells = _card(pair[0])
        if len(pair) == 2:
            cells += (f'<td width="20" style="width:20px;font-size:0">'
                      f'&nbsp;</td>{_card(pair[1])}')
        else:
            cells += f'<td width="{CARD_WIDTH + 20}">&nbsp;</td>'
        rows.append(
            f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'border="0" width="100%"><tr>{cells}</tr></table>')
    return "".join(rows)


def _spotlight(spot: dict) -> str:
    if not spot:
        return ""
    if spot.get("all_used"):
        return (
            f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'border="0" width="100%" style="background:{PALETTE["wash"]};'
            f'border:1px solid {PALETTE["rule"]};border-radius:2px">'
            f'<tr><td style="padding:16px;font-family:{FONT};font-size:14px;'
            f'color:{PALETTE["text"]}">'
            f'{escape(spot.get("note") or "")}</td></tr></table>')

    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" '
        f'border="0" width="100%" style="background:{PALETTE["wash"]};'
        f'border:1px solid {PALETTE["rule"]};border-radius:2px">'
        f'<tr><td style="padding:18px">'
        f'<div style="font-family:{MONO};font-size:11px;letter-spacing:0.1em;'
        f'text-transform:uppercase;color:{PALETTE["muted"]};'
        f'padding-bottom:10px">One thing you have not tried yet</div>'
        f'<div style="font-family:{FONT};font-size:18px;font-weight:700;'
        f'color:{PALETTE["text"]};padding-bottom:6px">'
        f'{escape(spot.get("title") or "")}</div>'
        f'<div style="font-family:{FONT};font-size:14px;line-height:1.5;'
        f'color:{PALETTE["text"]};padding-bottom:6px">'
        f'{escape(spot.get("what") or "")}</div>'
        f'<div style="font-family:{FONT};font-size:14px;line-height:1.5;'
        f'color:{PALETTE["muted"]};padding-bottom:12px">'
        f'{escape(spot.get("when") or "")}</div>'
        f'<div style="font-family:{MONO};font-size:13px;color:{PALETTE["cyan"]};'
        f'background:{PALETTE["card"]};border:1px solid {PALETTE["rule"]};'
        f'border-radius:2px;padding:8px 10px;display:inline-block">'
        f'{escape(spot.get("command") or "")}</div>'
        f'</td></tr></table>')


def _pretty_date(iso: str) -> str:
    """`2026-09-05` as `Sep 5`. Falls back to the raw string rather than
    raising: a date this cannot parse is still worth printing."""
    try:
        from datetime import date

        d = date.fromisoformat(str(iso))
        return f"{d.strftime('%b')} {d.day}"
    except (TypeError, ValueError):
        return str(iso)


def render(report: dict, meta: dict | None = None) -> str:
    """The whole email as one HTML string. Pure: no I/O, no config read."""
    meta = meta or {}
    kpis = report.get("kpis") or []
    window = (f"{_pretty_date(report.get('window_start'))} to "
              f"{_pretty_date(report.get('window_end'))}")

    opening = "".join(
        f'<div style="font-family:{FONT};font-size:15px;line-height:1.5;'
        f'color:{PALETTE["text"]};padding-bottom:4px">{escape(line)}</div>'
        for line in (report.get("opening") or []))

    notes = [f"Counted over {window}, from your own briefings."]
    if report.get("malformed_events"):
        notes.append(f"{report['malformed_events']} unreadable lines in the "
                     f"log were skipped.")

    notes_html = "".join(
        f'<div style="font-family:{FONT};font-size:12px;line-height:1.5;'
        f'color:{PALETTE["muted"]}">{escape(n)}</div>' for n in notes)

    version = meta.get("version") or ""
    footer_bits = ["No message text, no names and no addresses leave your "
                   "machine, only these counts."]
    footer_bits.append("To stop these emails, run /van-gogh:update-settings "
                       "and turn the scorecard off.")
    if version:
        footer_bits.append(f"Van Gogh {version}.")
    footer = "".join(
        f'<div style="font-family:{FONT};font-size:12px;line-height:1.5;'
        f'color:{PALETTE["muted"]}">{escape(b)}</div>' for b in footer_bits)

    return (
        f'<body style="margin:0;padding:0;background:{PALETTE["bg"]}">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="100%" style="background:{PALETTE["bg"]}"><tr>'
        f'<td align="center" style="padding:24px 12px">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="640" style="width:640px;max-width:640px">'

        f'<tr><td style="padding-bottom:18px">'
        f'<div style="font-family:{MONO};font-size:11px;letter-spacing:0.14em;'
        f'text-transform:uppercase;color:{PALETTE["muted"]};'
        f'padding-bottom:10px">Van Gogh</div>'
        f'<div style="font-family:{FONT};font-size:26px;font-weight:700;'
        f'color:{PALETTE["text"]};padding-bottom:10px">Your fortnight, '
        f'{escape(window)}</div>'
        f'{opening}</td></tr>'

        f'<tr><td>{_card_rows(kpis)}</td></tr>'

        f'<tr><td style="padding:4px 0 18px 0">{notes_html}</td></tr>'
        f'<tr><td style="padding-bottom:20px">'
        f'{_spotlight(report.get("spotlight") or {})}</td></tr>'

        f'<tr><td style="border-top:1px solid {PALETTE["rule"]};'
        f'padding-top:14px">{footer}</td></tr>'

        f'</table></td></tr></table></body>'
    )


def render_text(report: dict) -> str:
    """The plain-text alternative. Same facts, same order, no decoration.

    Not a courtesy: a multipart message without a text part is a spam signal,
    and some clients show it in a preview line.
    """
    lines = [f"Your fortnight, {_pretty_date(report.get('window_start'))} to "
             f"{_pretty_date(report.get('window_end'))}", ""]
    lines.extend(report.get("opening") or [])
    lines.append("")
    for kpi in (report.get("kpis") or []):
        if not kpi.get("measured"):
            lines.append(f"{kpi.get('name')}: not enough yet, "
                         f"{kpi.get('reason')}")
            continue
        movement = kpi.get("direction_text") or ""
        prior = kpi.get("prior_text") or ""
        tail = f" ({movement} {prior})" if movement and prior else ""
        lines.append(f"{kpi.get('name')}: {kpi.get('value_text')}{tail}, "
                     f"{kpi.get('n_text')}")
        if kpi.get("detail"):
            lines.append(f"    {kpi['detail']}")
        lines.append(f"    Target: {kpi.get('target_text')}")
    spot = report.get("spotlight") or {}
    if spot and not spot.get("all_used"):
        lines += ["", "One thing you have not tried yet",
                  f"{spot.get('title')}: {spot.get('what')}",
                  f"{spot.get('command')}"]
    elif spot.get("all_used"):
        lines += ["", str(spot.get("note") or "")]
    lines += ["", "Counted on your machine from your own briefings.",
              "To stop these emails, run /van-gogh:update-settings and turn "
              "the scorecard off."]
    return "\n".join(lines)
