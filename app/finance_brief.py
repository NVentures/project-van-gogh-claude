#!/usr/bin/env python3
"""The weekly finance brief: cash, who owes you, what you owe, and the month.

Five numbers a person who runs a business wants on a Monday, read straight out
of QuickBooks through the connector they authorized, rendered by code, and
delivered whether or not anyone is at the machine.

The risk in a page of financial figures is not that it crashes. It is that it
is confidently wrong in a way nothing downstream can see, so four rules are
structural rather than careful.

**A number carries the period the report says it covers, never the period it
was asked for.** A profit and loss requested for September and answered for the
fiscal year to date is a correct tool, a correct-looking label and a figure
four times too large. `connector_fetch` refuses the mismatched arguments, and
this module refuses a response whose own header disagrees with the request.

**Zero is not a measurement.** An empty report and a genuinely empty ledger
produce the same 0.00, and only one of them is true. A section with no
underlying rows says it could not measure rather than reporting nothing owed.

**Direction is data.** Cash rising is good, overdue receivables rising is not,
and a shared "up 12 percent" would paint both green. `POLARITY` is a table the
renderer reads, so a new measure has to declare which way is good.

**One company.** The connector is authorized against a single QuickBooks
company, and someone who runs four of them cannot tell from the numbers which
one answered. The realm is pinned in config and checked on every run.

Cash means the bank accounts, listed by name. Credit cards are shown separately
and never netted against them, because a business with 400k in the bank and
90k on the cards has both facts, and subtracting one from the other quietly
answers a question nobody asked.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_loader                                            # noqa: E402
import connector_fetch                                          # noqa: E402
import run_ledger                                               # noqa: E402
from data_sources import load_input                             # noqa: E402

JOB_NAME = "finance.brief"
SERVER = connector_fetch.SERVERS["quickbooks"]

# The only QuickBooks tools this feature may ever touch. A frozen tuple, and a
# test proves every name is a read: the same connector exposes invoice deletes,
# payroll edits and a loan application submit, and the distance between reading
# the books and changing them is one typo in a list.
QUICKBOOKS_TOOLS = (
    "company_info",
    "qbo_accounting_get_balance_sheet",
    "qbo_accounting_get_ar_aging_summary",
    "qbo_accounting_get_ar_aging_detail",
    "qbo_accounting_get_ap_aging_summary",
    "profit_loss_quickbooks_account",
)

# Which way is good, per measure. Three of these five improve by going down,
# so a shared delta colour would call a 40 percent rise in overdue invoices a
# success. Neutral measures get a direction word and no verdict.
POLARITY = {
    "cash": "up",
    "net_income": "up",
    "ar_overdue": "down",
    "ap_overdue": "down",
    "ar_total": "neutral",
    "ap_total": "neutral",
}

# Account types that are cash. Anything else on the balance sheet is not.
CASH_TYPES = ("bank",)
UNDEPOSITED = "undeposited funds"
CARD_TYPES = ("credit card",)

MAX_TOP_OVERDUE = 5


# ── The calls ────────────────────────────────────────────────────────────────

def build_calls(today: date | None = None, basis: str = "Accrual") -> list:
    """The seven reads, with every argument decided here rather than by a model.

    The month to date and the prior full month are asked for as explicit
    dates. A date macro would be shorter and is exactly how a brief ends up
    reporting the fiscal year under a monthly heading.
    """
    today = today or date.today()
    month_start = today.replace(day=1)
    prior_end = month_start - timedelta(days=1)
    prior_start = prior_end.replace(day=1)
    iso = "%Y-%m-%d"
    return [
        {"tool": "company_info", "args": {}},
        {"tool": "qbo_accounting_get_balance_sheet",
         "args": {"as_of_date": today.strftime(iso), "accounting_method": basis}},
        {"tool": "qbo_accounting_get_ar_aging_summary",
         "args": {"as_of_date": today.strftime(iso)}},
        {"tool": "qbo_accounting_get_ar_aging_detail",
         "args": {"as_of_date": today.strftime(iso)}},
        {"tool": "qbo_accounting_get_ap_aging_summary",
         "args": {"as_of_date": today.strftime(iso)}},
        {"tool": "profit_loss_quickbooks_account",
         "args": {"start_date": month_start.strftime(iso),
                  "end_date": today.strftime(iso),
                  "accounting_method": basis}},
        {"tool": "profit_loss_quickbooks_account_prior",
         "args": {"start_date": prior_start.strftime(iso),
                  "end_date": prior_end.strftime(iso),
                  "accounting_method": basis}},
    ]


# The prior-month P&L is the same tool called twice with different dates, and
# `connector_fetch` refuses a tool called twice on purpose. So the second call
# is named apart here and mapped back to the real tool at the edge.
_ALIASES = {"profit_loss_quickbooks_account_prior": "profit_loss_quickbooks_account"}


def real_tool(name: str) -> str:
    return _ALIASES.get(name, name)


# ── Reading a QuickBooks report ──────────────────────────────────────────────

def _as_json(raw):
    """A tool result as a dict, whatever wrapping it arrived in."""
    if isinstance(raw, dict):
        return raw
    text = connector_fetch._as_text(raw)
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
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return {}


# ── The five sections ────────────────────────────────────────────────────────

def _unmeasured(reason: str) -> dict:
    """A section that could not be read, and why, in the reader's words.

    The reason is stored without its full stop so the renderer can end the
    sentence itself. A reason that punctuates itself and a renderer that adds
    one produce "... by itself..", which reads as a typo on the one line a
    reader is already unhappy to see.
    """
    return {"measured": False, "reason": reason.rstrip().rstrip(".")}


def report_period(report: dict) -> dict:
    """What the report says it covers, in its own words.

    This is the guard against a plausible wrong number: the request asked for a
    month, and only the response can say whether a month came back. The
    connector states an `asOf` on a point-in-time report and a `period` on a
    ranged one, and the period is a phrase ("This month") rather than dates,
    which is why a P&L is checked on its phrase and a balance sheet on its date.
    """
    return {
        "as_of": str(report.get("asOf") or "").strip(),
        "period": str(report.get("period") or "").strip(),
        "basis": str(report.get("accountingMethod") or "").strip(),
        "realm": str(report.get("realmId") or "").strip(),
    }


def refusal(report: dict) -> str:
    """A tool that answered without answering, in its own words.

    Not every failure is an error. `profit_loss_quickbooks_account` returns
    HTTP success, no error flag, and a body saying the QuickBooks profile is
    missing an industry. It is non-empty, so nothing downstream falls back, and
    a brief that treated it as data would render an empty month as a real one.
    """
    status = str(report.get("status") or "").strip()
    if status and status not in ("ok", "success"):
        message = str(report.get("message") or "").strip()
        missing = report.get("missingProfileAttributes") or []
        if missing:
            names = ", ".join(str(m) for m in missing)
            return (f"{message} QuickBooks is missing: {names}. Set it in "
                    "QuickBooks and this section fills in by itself.")
        return message or f"the report came back as {status}"
    return ""


def _num(value) -> float | None:
    """A QuickBooks money value as a number, or None when it is not one."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("$", "")
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        out = float(text)
    except ValueError:
        return None
    return -out if negative else out


def iter_rows(report: dict):
    """Every row of a report, flat. The tree lives in metadata, not nesting."""
    return list((report.get("reportData") or {}).get("rows") or [])


def cell(row: dict, *names):
    """The value of the first cell matching any of these names.

    Cells are named rather than positional, and the names differ between
    reports ("1-30" on receivables, "1 - 30" on payables), so every caller
    passes the spellings it will accept instead of trusting a column order.
    """
    wanted = {n.lower() for n in names}
    for item in (row.get("cells") or []):
        if str(item.get("name") or "").lower() in wanted:
            return item.get("value")
    return None


def row_id(row: dict) -> str:
    return str((row.get("metadata") or {}).get("id") or "")


def parent_id(row: dict) -> str:
    return str((row.get("metadata") or {}).get("parentId") or "")


def children_of(report: dict, parent: str) -> list:
    """The leaf rows directly under a group, by the report's own tree."""
    return [r for r in iter_rows(report) if parent_id(r) == parent]


def group_named(report: dict, *labels) -> dict | None:
    """The first group row whose account name matches one of these."""
    wanted = {label.lower() for label in labels}
    for row in iter_rows(report):
        name = str(cell(row, "ACCOUNT_NAME") or "").strip().lower()
        if name in wanted:
            return row
    return None


def _summary(report: dict) -> dict:
    return report.get("summary") or {}


# How far the connector's own arithmetic may differ from the rows underneath
# before the number is treated as untrustworthy. Rounding, not disagreement.
SUMMARY_TOLERANCE = 0.05


def read_cash(report: dict, expect_as_of: str) -> dict:
    """Bank accounts by name, with credit cards kept separate.

    The connector computes `summary.assetBreakdown.cash` itself, and that is
    what is reported, but only after the rows underneath are added up and found
    to agree. A total nobody checked is a total nobody can defend.
    """
    if not report:
        return _unmeasured("the balance sheet did not come back")
    refused = refusal(report)
    if refused:
        return _unmeasured(refused)

    period = report_period(report)
    if expect_as_of and period["as_of"] and period["as_of"][:10] != expect_as_of:
        return _unmeasured(
            f"the balance sheet came back as of {period['as_of'][:10]} rather "
            f"than {expect_as_of}, so it is not today's position")

    bank = group_named(report, "bank accounts", "cash and cash equivalents")
    if bank is None:
        return _unmeasured("the balance sheet carried no bank accounts")

    accounts = []
    for row in children_of(report, row_id(bank)):
        name = str(cell(row, "ACCOUNT_NAME") or "").strip()
        amount = _num(cell(row, "DETAIL_NATURAL_HOME_AMOUNT__TOTAL",
                           "DETAIL_NATURAL_HOME_AMOUNT__TOTAL_WITHOUT_SUBGROUPS"))
        if name and amount is not None:
            accounts.append({"name": name, "balance": round(amount, 2)})

    if not accounts:
        return _unmeasured("the balance sheet listed no individual bank accounts")

    from_rows = round(sum(a["balance"] for a in accounts), 2)
    stated = _num((_summary(report).get("assetBreakdown") or {}).get("cash"))
    if stated is not None and abs(stated - from_rows) > SUMMARY_TOLERANCE:
        return _unmeasured(
            f"the balance sheet's own total for cash ({stated:,.2f}) does not "
            f"match the accounts listed under it ({from_rows:,.2f}), so neither "
            "can be trusted")

    cards = _num((_summary(report).get("liabilityBreakdown") or {}).get("creditCards"))
    return {
        "measured": True, "reason": "",
        "total": from_rows,
        "accounts": accounts,
        "credit_cards_total": round(cards, 2) if cards else None,
        "credit_cards": [],
        "as_of": period["as_of"][:10], "basis": period["basis"],
    }


# The bucket names each report uses, in the spellings actually observed:
# receivables say "1-30" and payables say "1 - 30".
_AGING_BUCKETS = (
    ("current", ("Current",)),
    ("1_30", ("1-30", "1 - 30")),
    ("31_60", ("31-60", "31 - 60")),
    ("61_90", ("61-90", "61 - 90")),
    ("over_90", ("91+", "91 and over", "> 90")),
)

# Derived, never retyped: a hardcoded list of overdue keys beside the table
# above is two definitions that can disagree, and the one that decides whether
# an invoice is late is not the one anybody would think to check.
_OVERDUE_KEYS = tuple(key for key, _aliases in _AGING_BUCKETS[1:])


def read_aging(report: dict, expect_as_of: str, what: str) -> dict:
    """An aging summary: the total, the buckets, and what is actually overdue.

    Same discipline as cash. The connector states its own bucket totals and
    overdue figure, and both are cross-checked against the customer or vendor
    rows before either is shown.
    """
    if not report:
        return _unmeasured(f"the {what} aging report did not come back")
    refused = refusal(report)
    if refused:
        return _unmeasured(refused)

    period = report_period(report)
    if expect_as_of and period["as_of"] and period["as_of"][:10] != expect_as_of:
        return _unmeasured(
            f"the {what} aging came back as of {period['as_of'][:10]} rather "
            f"than {expect_as_of}")

    summary = _summary(report)
    stated_buckets = summary.get("bucketTotals") or {}
    rows = iter_rows(report)

    buckets = {}
    for key, aliases in _AGING_BUCKETS:
        value = None
        for alias in aliases:
            if alias in stated_buckets:
                value = _num(stated_buckets[alias])
                break
        if value is None and rows:
            found = [_num(cell(r, *aliases)) for r in rows]
            found = [f for f in found if f is not None]
            value = round(sum(found), 2) if found else None
        buckets[key] = round(value, 2) if value is not None else None

    total = _num(summary.get("totalReceivables"))
    if total is None:
        total = _num(summary.get("totalPayables"))
    from_rows = [_num(cell(r, "Total")) for r in rows]
    from_rows = [f for f in from_rows if f is not None]
    row_total = round(sum(from_rows), 2) if from_rows else None
    if total is not None and row_total is not None and \
            abs(total - row_total) > SUMMARY_TOLERANCE:
        return _unmeasured(
            f"the {what} report's own total ({total:,.2f}) does not match the "
            f"rows underneath it ({row_total:,.2f}), so neither can be trusted")
    if total is None:
        total = row_total

    overdue = _num(summary.get("totalOverdue"))
    parts = [buckets[k] for k in _OVERDUE_KEYS if buckets.get(k) is not None]
    from_buckets = round(sum(parts), 2) if parts else None
    if overdue is not None and from_buckets is not None and \
            abs(overdue - from_buckets) > SUMMARY_TOLERANCE:
        return _unmeasured(
            f"the {what} report's overdue total ({overdue:,.2f}) does not match "
            f"its own age buckets ({from_buckets:,.2f}), so neither can be trusted")
    if overdue is None:
        overdue = from_buckets

    # An empty report and an empty ledger both total zero, and only one of them
    # is a fact. With no rows behind it, say so rather than report nothing owed.
    if not rows and not total:
        return _unmeasured(
            f"nothing came back on the {what} aging report, so this is not a "
            "confirmed zero")

    return {"measured": True, "reason": "", "total": total,
            "current": buckets.get("current"), "buckets": buckets,
            "overdue_total": overdue, "as_of": period["as_of"][:10]}


def read_overdue_names(report: dict, limit: int = MAX_TOP_OVERDUE) -> list:
    """Who is furthest behind. The report names them itself."""
    if not report:
        return []
    out = []
    for entry in (_summary(report).get("topOverdueCustomers")
                  or _summary(report).get("topOverdueVendors") or []):
        name = str(entry.get("name") or "").strip()
        amount = _num(entry.get("overdue") if entry.get("overdue") is not None
                      else entry.get("total"))
        if name and amount:
            out.append({"name": name, "amount": round(amount, 2)})
    if not out:
        for row in iter_rows(report):
            name = str(cell(row, "Customer", "Vendor") or "").strip()
            amount = _num(cell(row, "Total"))
            if name and amount:
                out.append({"name": name, "amount": round(amount, 2)})
    out.sort(key=lambda r: r["amount"], reverse=True)
    return out[:limit]


def read_pl(report: dict, expect_start: str, expect_end: str, label: str) -> dict:
    """Income, expenses and net for a period the report itself names."""
    if not report:
        return _unmeasured(f"the {label} profit and loss did not come back")
    refused = refusal(report)
    if refused:
        return _unmeasured(refused)

    summary = _summary(report)
    income = _num(summary.get("totalIncome") or summary.get("totalRevenue"))
    expenses = _num(summary.get("totalExpenses") or summary.get("totalExpense"))
    net = _num(summary.get("netIncome") or summary.get("netProfit"))

    if income is None and expenses is None and net is None:
        for row in iter_rows(report):
            name = str(cell(row, "ACCOUNT_NAME") or "").strip().lower()
            value = _num(cell(row, "DETAIL_NATURAL_HOME_AMOUNT__TOTAL"))
            if value is None:
                continue
            if name in ("total income", "total revenue", "income"):
                income = value
            elif name in ("total expenses", "total expense", "expenses"):
                expenses = value
            elif name in ("net income", "net operating income", "profit"):
                net = value

    if income is None and expenses is None and net is None:
        return _unmeasured(f"the {label} profit and loss had no totals to read")
    if net is None and income is not None and expenses is not None:
        net = round(income - expenses, 2)

    period = report_period(report)
    return {"measured": True, "reason": "", "income": income,
            "expenses": expenses, "net": net,
            "start": expect_start, "end": expect_end,
            "basis": period["basis"]}


def read_company(raw_text: str, reports: list | None = None) -> dict:
    """Whose books these are.

    `company_info` answers in prose ("Company Name: Acme, LLC") rather than
    JSON, and carries no id at all. The id that actually identifies the
    company is the `realmId` every report stamps on itself, which is the more
    trustworthy source anyway: it comes from the same response as the numbers,
    so it cannot drift from them.
    """
    text = raw_text if isinstance(raw_text, str) else ""
    name = ""
    for line in text.splitlines():
        if ":" in line and "company name" in line.split(":")[0].lower():
            name = line.split(":", 1)[1].strip()
            break
    realm = ""
    for report in (reports or []):
        realm = str((report or {}).get("realmId") or "").strip()
        if realm:
            break
    return {"name": name, "id": realm, "currency": ""}


# ── Deltas ───────────────────────────────────────────────────────────────────

def direction_word(now, prior) -> str:
    if now is None or prior is None:
        return ""
    if abs(now - prior) < 0.005:
        return "flat"
    return "up" if now > prior else "down"


def is_good(measure: str, word: str):
    """Whether this movement is good news, or None when the measure is neutral."""
    want = POLARITY.get(measure, "neutral")
    if want == "neutral" or word in ("", "flat"):
        return None
    return word == want


def compute_deltas(current: dict, prior_row: dict | None) -> dict:
    """Compare against the previous WEEKLY brief, never an on-demand run.

    A Monday brief that says "vs Thursday" because someone ran the skill on
    Thursday is answering a different question than the one its heading asks.
    """
    if not prior_row:
        return {}
    prior = prior_row.get("values") or {}
    out = {}
    for measure, value in (current.get("values") or {}).items():
        before = prior.get(measure)
        if value is None or before is None:
            continue
        word = direction_word(value, before)
        out[measure] = {"value": value, "prior": before,
                        "prior_date": prior_row.get("date", ""),
                        "direction_word": word,
                        "good": is_good(measure, word)}
    return out


def measured_values(report: dict) -> dict:
    """The comparable numbers, flat, for the history ledger."""
    cash, ar, ap = report.get("cash") or {}, report.get("ar") or {}, report.get("ap") or {}
    pl = report.get("pl_mtd") or {}
    return {
        "cash": cash.get("total") if cash.get("measured") else None,
        "ar_total": ar.get("total") if ar.get("measured") else None,
        "ar_overdue": ar.get("overdue_total") if ar.get("measured") else None,
        "ap_total": ap.get("total") if ap.get("measured") else None,
        "ap_overdue": ap.get("overdue_total") if ap.get("measured") else None,
        "net_income": pl.get("net") if pl.get("measured") else None,
    }


# ── History ──────────────────────────────────────────────────────────────────

def history_path() -> Path:
    return config_loader.logs_dir() / "finance" / "history.json"


def read_history() -> list:
    try:
        with open(history_path(), encoding="utf-8") as f:
            rows = json.load(f)
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def last_weekly(rows: list, before: str = "") -> dict | None:
    weekly = [r for r in rows if r.get("kind") == "weekly"
              and (not before or r.get("date", "") < before)]
    return weekly[-1] if weekly else None


def write_history(row: dict) -> None:
    """One row per day and kind: a second run today replaces the first."""
    rows = [r for r in read_history()
            if not (r.get("date") == row.get("date")
                    and r.get("kind") == row.get("kind"))]
    rows.append(row)
    rows.sort(key=lambda r: (r.get("date", ""), r.get("kind", "")))
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows[-200:], f, indent=2, sort_keys=True)
    tmp.replace(path)


# ── Rendering ────────────────────────────────────────────────────────────────

def money(value, currency: str = "USD") -> str:
    """A figure as a person writes it. None is never rendered as a number."""
    if value is None:
        return "not measured"
    symbol = "$" if currency in ("USD", "", "CAD", "AUD") else ""
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{abs(value):,.2f}"


def _movement(delta: dict | None) -> str:
    """The direction in words, so the reader never has to infer it.

    Every figure that can move says which way it went and whether that is the
    good way, because "up 12 percent" means opposite things for cash and for
    overdue invoices.
    """
    if not delta or not delta.get("direction_word"):
        return "no comparison yet"
    word = delta["direction_word"]
    prior_date = delta.get("prior_date") or ""
    since = f" vs {prior_date}" if prior_date else ""
    if word == "flat":
        return f"unchanged{since}"
    good = delta.get("good")
    if good is True:
        return f"{word}, which is the good direction{since}"
    if good is False:
        return f"{word}, which is the wrong direction{since}"
    return f"{word}{since}"


def render(report: dict) -> str:
    """The whole brief, decided here. The model adds words, never numbers."""
    lines = []
    currency = report.get("currency") or "USD"
    sections = report.get("sections") or {}
    deltas = report.get("deltas") or {}
    missing = [name for name, s in sections.items() if not s.get("measured")]

    company = (report.get("company") or {}).get("name") or "your books"
    if missing:
        lines.append(
            f"Part of this brief is missing: {_and_list(missing)} could not be "
            "read this morning. Everything below is what did come back.")
        lines.append("")
    lines.append(f"{company}, as of {report.get('as_of', '')}. "
                 f"Figures in {currency}.")
    lines.append("")

    cash = report.get("cash") or {}
    lines.append("## Cash")
    if cash.get("measured"):
        lines.append(f"{money(cash['total'], currency)} across "
                     f"{_count_word(len(cash['accounts']))}, "
                     f"{_movement(deltas.get('cash'))}.")
        for account in cash["accounts"]:
            lines.append(f"- {account['name']}: {money(account['balance'], currency)}")
        if cash.get("credit_cards_total") is not None:
            lines.append(f"- Credit cards owed, kept separate: "
                         f"{money(cash['credit_cards_total'], currency)}")
    else:
        lines.append(f"Not measured: {cash.get('reason', 'no reason recorded')}.")
    lines.append("")

    ar = report.get("ar") or {}
    lines.append("## Owed to you")
    if ar.get("measured"):
        lines.append(f"{money(ar.get('total'), currency)} outstanding, of which "
                     f"{money(ar.get('overdue_total'), currency)} is past due, "
                     f"{_movement(deltas.get('ar_overdue'))}.")
        lines.append(_buckets_line(ar.get("buckets") or {}, currency))
        for row in (report.get("ar_top_overdue") or []):
            lines.append(f"- {row['name']}: {money(row['amount'], currency)}")
    else:
        lines.append(f"Not measured: {ar.get('reason', 'no reason recorded')}.")
    lines.append("")

    ap = report.get("ap") or {}
    lines.append("## You owe")
    if ap.get("measured"):
        lines.append(f"{money(ap.get('total'), currency)} outstanding, of which "
                     f"{money(ap.get('overdue_total'), currency)} is past due, "
                     f"{_movement(deltas.get('ap_overdue'))}.")
        lines.append(_buckets_line(ap.get("buckets") or {}, currency))
    else:
        lines.append(f"Not measured: {ap.get('reason', 'no reason recorded')}.")
    lines.append("")

    mtd, prior = report.get("pl_mtd") or {}, report.get("pl_prior") or {}
    lines.append("## The month so far")
    if mtd.get("measured"):
        # "a accrual basis" reads as a typo to anyone who notices it, and the
        # only two values this ever takes are Accrual and Cash.
        word = mtd["basis"].lower() if mtd.get("basis") else ""
        article = "an" if word[:1] in "aeiou" else "a"
        basis = f" on {article} {word} basis" if word else ""
        lines.append(f"{mtd['start']} to {mtd['end']}{basis}: "
                     f"{money(mtd.get('income'), currency)} in, "
                     f"{money(mtd.get('expenses'), currency)} out, "
                     f"{money(mtd.get('net'), currency)} net, "
                     f"{_movement(deltas.get('net_income'))}.")
        if prior.get("measured"):
            # Said as context, not as news: this is a closed month, so it
            # carries no direction of its own and the sentence says so rather
            # than leaving the reader to work out which number is the story.
            lines.append(f"For context, the prior full month, {prior['start']} "
                         f"to {prior['end']}, finished at "
                         f"{money(prior.get('net'), currency)} net on "
                         f"{money(prior.get('income'), currency)} of income. "
                         "That month is closed, so it does not move.")
        else:
            lines.append("The prior month is not shown: "
                         f"{prior.get('reason', 'it did not come back')}.")
    else:
        lines.append(f"Not measured: {mtd.get('reason', 'no reason recorded')}.")

    return "\n".join(lines).strip() + "\n"


def _buckets_line(buckets: dict, currency: str) -> str:
    names = [("current", "not yet due"), ("1_30", "1 to 30 days"),
             ("31_60", "31 to 60 days"), ("61_90", "61 to 90 days"),
             ("over_90", "over 90 days")]
    parts = [f"{label} {money(buckets[key], currency)}"
             for key, label in names if buckets.get(key) is not None]
    return "By age: " + ", ".join(parts) + "." if parts else \
        "The age breakdown did not come back."


def _count_word(n: int) -> str:
    words = {1: "one account", 2: "two accounts", 3: "three accounts",
             4: "four accounts", 5: "five accounts"}
    return words.get(n, f"{n} accounts")


def _and_list(items: list) -> str:
    items = [i.replace("_", " ") for i in items]
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# ── Assembly ─────────────────────────────────────────────────────────────────

def build(raw: dict, today: date | None = None, basis: str = "Accrual",
          kind: str = "manual", expected_company: dict | None = None,
          history: list | None = None) -> dict:
    """Turn raw tool results into the brief. No network, so this is testable."""
    today = today or date.today()
    calls = {c["tool"]: c for c in build_calls(today, basis)}
    iso = today.strftime("%Y-%m-%d")

    reports = [_as_json(raw.get(name)) for name in
               ("qbo_accounting_get_balance_sheet",
                "qbo_accounting_get_ar_aging_summary",
                "qbo_accounting_get_ap_aging_summary")]
    company = read_company(connector_fetch._as_text(raw.get("company_info")),
                           reports)
    currency = company.get("currency") or "USD"

    expected = expected_company or {}
    wrong_company = ""
    if expected.get("id") and company.get("id") and expected["id"] != company["id"]:
        wrong_company = (
            f"WRONG-COMPANY: these books are {company['name'] or 'an unnamed company'} "
            f"(id {company['id']}), not {expected.get('name') or 'the company'} "
            f"(id {expected['id']}) that this brief is set up for. Nothing is "
            "shown, because a number is worthless until you know whose it is.")

    cash = read_cash(_as_json(raw.get("qbo_accounting_get_balance_sheet")), iso)
    ar = read_aging(_as_json(raw.get("qbo_accounting_get_ar_aging_summary")), iso,
                    "receivables")
    ap = read_aging(_as_json(raw.get("qbo_accounting_get_ap_aging_summary")), iso,
                    "payables")
    # The summary report names its own worst payers, so that is where these
    # come from; the detail report is the fallback for a shape that does not.
    top = read_overdue_names(_as_json(raw.get("qbo_accounting_get_ar_aging_summary")))
    if not top:
        top = read_overdue_names(
            _as_json(raw.get("qbo_accounting_get_ar_aging_detail")))
    mtd_args = calls["profit_loss_quickbooks_account"]["args"]
    prior_args = calls["profit_loss_quickbooks_account_prior"]["args"]
    mtd = read_pl(_as_json(raw.get("profit_loss_quickbooks_account")),
                  mtd_args["start_date"], mtd_args["end_date"], "month to date")
    prior = read_pl(_as_json(raw.get("profit_loss_quickbooks_account_prior")),
                    prior_args["start_date"], prior_args["end_date"],
                    "prior month")

    if wrong_company:
        blank = _unmeasured(wrong_company)
        cash = ar = ap = mtd = prior = dict(blank)
        top = []

    report = {
        "company": {"name": company.get("name", ""), "id": company.get("id", "")},
        "as_of": iso, "basis": basis, "currency": currency,
        "cash": cash, "ar": ar, "ap": ap, "ar_top_overdue": top,
        "pl_mtd": mtd, "pl_prior": prior,
    }
    report["sections"] = {
        name: {"measured": bool(report[key].get("measured")),
               "reason": report[key].get("reason", "")}
        for name, key in (("cash", "cash"), ("owed to you", "ar"),
                          ("you owe", "ap"), ("the month", "pl_mtd"))
    }
    report["values"] = measured_values(report)
    report["deltas"] = compute_deltas(report,
                                      last_weekly(history if history is not None
                                                  else read_history(), before=iso))
    report["kind"] = kind
    report["finance_md"] = render(report)
    return report


def output_path() -> Path:
    return config_loader.van_gogh_root() / "finance-brief.md"


def sidecar_path(now: datetime) -> Path:
    return (config_loader.logs_dir() / "finance" /
            f"{now.strftime('%Y-%m-%dT%H%M')}.json")


def fetch_raw(today: date | None = None, basis: str = "Accrual",
              claude: str | None = None) -> dict:
    """Ask the connector for the seven reports."""
    calls = build_calls(today, basis)
    wire = [{"tool": real_tool(c["tool"]), "args": c["args"]} for c in calls]
    # The prior-month P&L is the same tool with different dates, so it is sent
    # as its own fetch: connector_fetch refuses a repeated tool by design, and
    # relaxing that to allow this would reopen the duplicate-call hole.
    first = connector_fetch.fetch(SERVER, wire[:-1], claude=claude)
    if not first["ok"]:
        return first
    second = connector_fetch.fetch(SERVER, wire[-1:], claude=claude)
    data = dict(first["data"])
    if second["ok"]:
        data["profit_loss_quickbooks_account_prior"] = \
            second["data"].get("profit_loss_quickbooks_account")
    merged = dict(first)
    merged["data"] = data
    merged["tool_errors"] = (first.get("tool_errors") or []) + \
        (second.get("tool_errors") or [])
    if not second["ok"]:
        merged["tool_errors"].append(f"prior month: {second['reason']}")
    for key in ("cost_usd", "duration_s"):
        a, b = first["meta"].get(key), second["meta"].get(key)
        if a is not None and b is not None:
            merged["meta"][key] = round(a + b, 4)
    return merged


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="The weekly finance brief.")
    parser.add_argument("--json", action="store_true", help="emit the report JSON")
    parser.add_argument("--input", metavar="PATH",
                        help="pre-fetched raw tool results, for tests and for a "
                             "session that fetched them itself")
    parser.add_argument("--kind", choices=("weekly", "manual"), default="manual",
                        help="weekly runs are the baseline later briefs compare to")
    parser.add_argument("--claude", default=None,
                        help="absolute path to the claude CLI")
    parser.add_argument("--basis", default="Accrual", choices=("Accrual", "Cash"))
    args = parser.parse_args(argv)

    started = run_ledger.now_stamp()
    now = datetime.now()
    today = now.date()
    rc, detail = 0, ""
    fetch_meta: dict = {}

    try:
        if args.input:
            raw, fetch_meta = load_input(args.input), {"status": "input"}
        else:
            out = fetch_raw(today, args.basis, args.claude)
            fetch_meta = dict(out["meta"])
            fetch_meta["status"] = out["status"]
            if not out["ok"]:
                detail = out["reason"]
                print(json.dumps({"status": out["status"], "reason": detail,
                                  "finance_md": "", "sections": {},
                                  "errors": [detail]}, indent=2))
                print(detail, file=sys.stderr)
                rc = 1
                return rc
            raw = out["data"]
            if out.get("tool_errors"):
                fetch_meta["tool_errors"] = out["tool_errors"]

        expected = {"id": config_loader.finance_company_id(),
                    "name": config_loader.finance_company_name()}
        report = build(raw, today=today, basis=args.basis, kind=args.kind,
                       expected_company=expected)
        report["errors"] = list(fetch_meta.get("tool_errors") or [])
        sidecar = sidecar_path(now)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump({"raw": raw, "report": report}, f, indent=2, default=str)
        report["meta"] = {
            "output_path": str(output_path()),
            "sidecar_path": str(sidecar),
            "history_path": str(history_path()),
            "fetch": fetch_meta,
        }
        if any(s["measured"] for s in report["sections"].values()):
            write_history({"date": report["as_of"], "kind": args.kind,
                           "values": report["values"],
                           "company_id": report["company"]["id"]})
        else:
            rc = 1
            detail = next((s["reason"] for s in report["sections"].values()
                           if s["reason"]), "nothing could be measured")
        print(json.dumps(report, indent=2, default=str))
        return rc
    except Exception as exc:                                    # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"[:400]
        print(detail, file=sys.stderr)
        return 1
    finally:
        run_ledger.record_run(JOB_NAME, started, run_ledger.now_stamp(), rc, detail)


if __name__ == "__main__":
    raise SystemExit(main())
