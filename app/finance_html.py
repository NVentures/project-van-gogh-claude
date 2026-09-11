#!/usr/bin/env python3
"""The finance brief as an email: five figures a person reads on a phone.

Nested tables with explicit widths and every style inline, for the same reason
`kpi_html` is: Gmail strips a `<style>` block, Outlook renders through Word and
ignores `max-width` on a div, and neither has ever supported flexbox.

Two rules go beyond the layout, and both come from what a wrong financial page
costs rather than from taste.

**Amber cannot appear here.** In this product amber means work waiting on the
reader. A brief is a record of a position, so there is nothing on the page for
it to mean, and `PALETTE` is a closed set that excludes it.

**A colour never says anything the words have not already said.** Green on a
rising number is meaningless until the reader knows whether rising is good, and
it is good for cash and bad for overdue invoices. The movement is stated in
words first and the colour only agrees, so the page survives greyscale, a
colour-blind reader, and a client that strips styles entirely.
"""

from __future__ import annotations

from html import escape

# The only colours allowed. Amber is deliberately absent: see the docstring.
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

FONT = "'B612','Helvetica Neue',Helvetica,Arial,sans-serif"
MONO = "'B612 Mono','SF Mono',Menlo,Consolas,'Courier New',monospace"

PAGE_WIDTH = 640


def _tone(good) -> str:
    """The colour a movement is drawn in. Only ever agrees with the words."""
    if good is True:
        return PALETTE["green"]
    if good is False:
        return PALETTE["red"]
    return PALETTE["muted"]


def _movement_words(delta: dict | None) -> str:
    """The direction in words. This is what the colour is allowed to echo."""
    if not delta or not delta.get("direction_word"):
        return "no comparison yet"
    word = delta["direction_word"]
    since = f" since {delta['prior_date']}" if delta.get("prior_date") else ""
    if word == "flat":
        return f"unchanged{since}"
    good = delta.get("good")
    if good is True:
        return f"{word}{since}, the good way"
    if good is False:
        return f"{word}{since}, the wrong way"
    return f"{word}{since}"


def _money(value, currency: str = "USD") -> str:
    if value is None:
        return "not measured"
    symbol = "$" if currency in ("USD", "", "CAD", "AUD") else ""
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{abs(value):,.2f}"


def _card(title: str, figure: str, movement: str, good, detail: str = "") -> str:
    """One figure, its movement in words, and the small print beneath."""
    colour = _tone(good)
    rows = [
        f'<tr><td style="padding:0 0 6px 0;font-family:{FONT};font-size:12px;'
        f'letter-spacing:0.08em;text-transform:uppercase;'
        f'color:{PALETTE["muted"]}">{escape(title)}</td></tr>',
        f'<tr><td style="padding:0;font-family:{MONO};font-size:26px;'
        f'line-height:1.2;color:{PALETTE["text"]}">{escape(figure)}</td></tr>',
        f'<tr><td style="padding:4px 0 0 0;font-family:{FONT};font-size:13px;'
        f'color:{colour}">{escape(movement)}</td></tr>',
    ]
    if detail:
        rows.append(
            f'<tr><td style="padding:8px 0 0 0;font-family:{FONT};'
            f'font-size:12px;line-height:1.5;color:{PALETTE["muted"]}">'
            f'{detail}</td></tr>')
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="100%" style="width:100%;border-collapse:collapse;'
        f'background:{PALETTE["card"]};border:1px solid {PALETTE["rule_soft"]}">'
        f'<tr><td style="padding:18px 20px">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="100%" style="width:100%;border-collapse:collapse">'
        + "".join(rows) +
        f'</table></td></tr></table>'
    )


def _unmeasured_card(title: str, reason: str) -> str:
    """A section that could not be read says so, and never shows a zero."""
    return _card(title, "not measured", reason or "no reason recorded", None)


def _list(items: list) -> str:
    if not items:
        return ""
    rows = "".join(
        f'<tr><td style="padding:2px 0;font-family:{FONT};font-size:12px;'
        f'color:{PALETTE["muted"]}">{escape(text)}</td></tr>' for text in items)
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'border="0" width="100%" style="width:100%;'
            f'border-collapse:collapse">{rows}</table>')


def render(report: dict, meta: dict | None = None) -> str:
    """The whole email. Every figure comes from `report`, none is computed."""
    meta = meta or {}
    currency = report.get("currency") or "USD"
    deltas = report.get("deltas") or {}
    company = (report.get("company") or {}).get("name") or "Your books"
    missing = [name for name, s in (report.get("sections") or {}).items()
               if not s.get("measured")]

    blocks = []

    if missing:
        names = ", ".join(n.replace("_", " ") for n in missing)
        blocks.append(
            f'<tr><td style="padding:0 0 16px 0;font-family:{FONT};'
            f'font-size:13px;line-height:1.6;color:{PALETTE["red"]}">'
            f'Part of this brief is missing: {escape(names)} could not be read. '
            f'Everything below is what did come back.</td></tr>')

    cash = report.get("cash") or {}
    if cash.get("measured"):
        detail = _list(
            [f"{a['name']}: {_money(a['balance'], currency)}"
             for a in cash.get("accounts") or []] +
            ([f"Credit cards owed, kept separate: "
              f"{_money(cash['credit_cards_total'], currency)}"]
             if cash.get("credit_cards_total") is not None else []))
        card = _card("Cash", _money(cash.get("total"), currency),
                     _movement_words(deltas.get("cash")),
                     (deltas.get("cash") or {}).get("good"), detail)
    else:
        card = _unmeasured_card("Cash", cash.get("reason", ""))
    blocks.append(f'<tr><td style="padding:0 0 12px 0">{card}</td></tr>')

    ar = report.get("ar") or {}
    if ar.get("measured"):
        names = [f"{r['name']}: {_money(r['amount'], currency)}"
                 for r in (report.get("ar_top_overdue") or [])]
        detail = _list(
            [f"{_money(ar.get('total'), currency)} outstanding in total"] + names)
        card = _card("Past due to you", _money(ar.get("overdue_total"), currency),
                     _movement_words(deltas.get("ar_overdue")),
                     (deltas.get("ar_overdue") or {}).get("good"), detail)
    else:
        card = _unmeasured_card("Past due to you", ar.get("reason", ""))
    blocks.append(f'<tr><td style="padding:0 0 12px 0">{card}</td></tr>')

    ap = report.get("ap") or {}
    if ap.get("measured"):
        detail = _list([f"{_money(ap.get('total'), currency)} outstanding in total"])
        card = _card("Past due from you", _money(ap.get("overdue_total"), currency),
                     _movement_words(deltas.get("ap_overdue")),
                     (deltas.get("ap_overdue") or {}).get("good"), detail)
    else:
        card = _unmeasured_card("Past due from you", ap.get("reason", ""))
    blocks.append(f'<tr><td style="padding:0 0 12px 0">{card}</td></tr>')

    mtd = report.get("pl_mtd") or {}
    prior = report.get("pl_prior") or {}
    if mtd.get("measured"):
        lines = [f"{_money(mtd.get('income'), currency)} in, "
                 f"{_money(mtd.get('expenses'), currency)} out, "
                 f"{mtd.get('start', '')} to {mtd.get('end', '')}"]
        if mtd.get("basis"):
            lines.append(f"On a {mtd['basis'].lower()} basis")
        if prior.get("measured"):
            lines.append(f"For context, the prior full month finished at "
                         f"{_money(prior.get('net'), currency)} net. "
                         "That month is closed, so it does not move.")
        card = _card("Net, month to date", _money(mtd.get("net"), currency),
                     _movement_words(deltas.get("net_income")),
                     (deltas.get("net_income") or {}).get("good"), _list(lines))
    else:
        card = _unmeasured_card("Net, month to date", mtd.get("reason", ""))
    blocks.append(f'<tr><td style="padding:0 0 12px 0">{card}</td></tr>')

    footer = (f'Read from QuickBooks on {escape(str(report.get("as_of", "")))}. '
              f'Figures in {escape(currency)}. Nothing was changed in your books: '
              f'this only reads them.')
    if meta.get("version"):
        footer += f' Van Gogh {escape(str(meta["version"]))}.'

    return (
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(company)}</title></head>'
        f'<body style="margin:0;padding:0;background:{PALETTE["bg"]}">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="100%" style="width:100%;border-collapse:collapse;'
        f'background:{PALETTE["bg"]}"><tr><td align="center" '
        f'style="padding:24px 12px">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="{PAGE_WIDTH}" style="width:{PAGE_WIDTH}px;max-width:100%;'
        f'border-collapse:collapse">'
        f'<tr><td style="padding:0 0 4px 0;font-family:{FONT};font-size:18px;'
        f'color:{PALETTE["text"]}">{escape(company)}</td></tr>'
        f'<tr><td style="padding:0 0 18px 0;font-family:{FONT};font-size:13px;'
        f'color:{PALETTE["muted"]}">Where the money stands, '
        f'{escape(str(report.get("as_of", "")))}</td></tr>'
        + "".join(blocks) +
        f'<tr><td style="padding:14px 0 0 0;border-top:1px solid '
        f'{PALETTE["rule_soft"]};font-family:{FONT};font-size:11px;'
        f'line-height:1.6;color:{PALETTE["muted"]}">{footer}</td></tr>'
        f'</table></td></tr></table></body></html>'
    )


def render_text(report: dict) -> str:
    """The plain-text part. The markdown already reads as prose, so it is it."""
    return report.get("finance_md") or ""
