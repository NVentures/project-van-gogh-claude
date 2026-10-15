"""The finance brief: is each number true in the sense the reader takes it?

A page of financial figures fails quietly. It does not crash, it reports a
fiscal year under a monthly heading, or a zero that means "the report came back
empty", or last Thursday's comparison under a heading that says the week. Every
test here is aimed at one of those, because a crash would have been caught by
anything.

The fixtures are synthetic and shaped like QuickBooks Reports responses: a
Header carrying the period the report actually covers, Columns, and nested Rows
with ColData cells. No real figure from anybody's books appears in this repo.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import finance_brief as fb

APP = Path(__file__).resolve().parent.parent / "app"
TODAY = date(2026, 9, 10)
ISO = "2026-09-10"


# ── Fixture builders ─────────────────────────────────────────────────────────
#
# Shaped from real captured responses on 2026-09-10: the connector returns
# Intuit's reporting shape (reportTitle / reportData.rows[].cells[] with named
# cells, plus a computed `summary` block), NOT the QuickBooks Reports API's
# Header / ColData. Every figure and name below is invented; only the structure
# is real.

REALM = "9130000000000001"


def _row(cells, rid="1", parent="0", group=False):
    return {
        "cells": [{"id": str(i + 1), "name": name, "value": value,
                   "localizedValue": None, "cells": None}
                  for i, (name, value) in enumerate(cells)],
        "depth": 0,
        "metadata": {"id": rid, "parentId": parent, "pageStart": None,
                     "pageEnd": None,
                     **({"type": ["GROUP", "SUMMARY"]} if group else {})},
    }


def balance_sheet(accounts=None, cards=0.0, as_of=ISO, cash_override=None):
    """A balance sheet with a Bank Accounts group and leaves under it."""
    accounts = accounts if accounts is not None else [
        ("Operating Checking", 412908.33), ("Payroll Savings", 85000.00)]
    total = cash_override if cash_override is not None else \
        round(sum(a[1] for a in accounts), 2)
    rows = [
        _row([("ACCOUNT_NAME", "Assets"),
              ("DETAIL_NATURAL_HOME_AMOUNT__TOTAL", total)], "1", "0", True),
        _row([("ACCOUNT_NAME", "Current Assets"),
              ("DETAIL_NATURAL_HOME_AMOUNT__TOTAL", total)], "1.1", "1", True),
        _row([("ACCOUNT_NAME", "Bank Accounts"),
              ("DETAIL_NATURAL_HOME_AMOUNT__TOTAL", total)], "1.1.1", "1.1", True),
    ]
    for i, (name, value) in enumerate(accounts, 1):
        rows.append(_row(
            [("ACCOUNT_NAME", name),
             ("DETAIL_NATURAL_HOME_AMOUNT__TOTAL_WITHOUT_SUBGROUPS", value),
             ("DETAIL_NATURAL_HOME_AMOUNT__TOTAL", value)],
            f"1.1.1.{i}", "1.1.1"))
    return {
        "reportTitle": "Balance Sheet",
        "reportData": {"rows": rows},
        "asOf": as_of,
        "accountingMethod": "Accrual",
        "summary": {"assetBreakdown": {"cash": total},
                    "liabilityBreakdown": {"creditCards": cards}},
        "realmId": REALM,
    }


def aging(buckets=None, as_of=ISO, kind="ar", rows_named=None, summary=True):
    """An aging summary. Receivables spell a bucket "1-30", payables "1 - 30"."""
    buckets = buckets if buckets is not None else {
        "Current": 12500.0, "1-30": 8300.0, "31-60": 4100.0,
        "61-90": 0.0, "91+": 2250.0}
    if kind == "ap":
        buckets = {k.replace("-", " - ") if "-" in k and k != "91+" else k: v
                   for k, v in buckets.items()}
    total = round(sum(buckets.values()), 2)
    overdue = round(total - buckets.get("Current", 0.0), 2)
    who = "Customer" if kind == "ar" else "Vendor"
    named = rows_named if rows_named is not None else [("Acme Foods", total)]
    rows = [_row([(who, name), ("Total", value)] +
                 [(b, v) for b, v in buckets.items()], str(i + 1), "0")
            for i, (name, value) in enumerate(named)]
    report = {
        "reportTitle": "A/R Aging Summary" if kind == "ar" else "A/P Aging Summary",
        "reportData": {"rows": rows},
        "asOf": as_of,
        "realmId": REALM,
    }
    if summary:
        key = "totalReceivables" if kind == "ar" else "totalPayables"
        top = "topOverdueCustomers" if kind == "ar" else "topOverdueVendors"
        report["summary"] = {
            key: total, "totalOverdue": overdue,
            "totalCurrent": buckets.get("Current", 0.0),
            "bucketTotals": buckets,
            top: [{"name": n, "total": v, "overdue": v} for n, v in named],
        }
    return report


def aging_detail(rows=(("Acme Foods", 2250.0), ("Borden Ltd", 4100.0))):
    return {"reportTitle": "A/R Aging Detail", "asOf": ISO, "realmId": REALM,
            "reportData": {"rows": [
                _row([("Customer", name), ("Total", amount)], str(i + 1))
                for i, (name, amount) in enumerate(rows)]}}


def profit_loss(income=184300.00, expenses=151090.00, net=None, period="This month"):
    net = net if net is not None else round(income - expenses, 2)
    return {
        "reportTitle": "Profit and Loss",
        "reportData": {"rows": [
            _row([("ACCOUNT_NAME", "Total Income"),
                  ("DETAIL_NATURAL_HOME_AMOUNT__TOTAL", income)]),
            _row([("ACCOUNT_NAME", "Total Expenses"),
                  ("DETAIL_NATURAL_HOME_AMOUNT__TOTAL", expenses)]),
            _row([("ACCOUNT_NAME", "Net Income"),
                  ("DETAIL_NATURAL_HOME_AMOUNT__TOTAL", net)])]},
        "period": period,
        "accountingMethod": "Accrual",
        "summary": {"totalIncome": income, "totalExpenses": expenses,
                    "netIncome": net},
        "realmId": REALM,
    }


def refused(missing="industry"):
    """The shape a soft refusal takes: HTTP success, no error flag, no data."""
    return {"status": "profile_info_required",
            "message": "Your QuickBooks profile needs additional information.",
            "missingProfileAttributes": [missing], "realmId": REALM}


def company(name="Northwind Solar"):
    """company_info answers in prose and carries no id."""
    return f"Company Name: {name}\nIndustry: 813312"


def raw(**overrides):
    out = {
        "company_info": company(),
        "qbo_accounting_get_balance_sheet": balance_sheet(),
        "qbo_accounting_get_ar_aging_summary": aging(kind="ar"),
        "qbo_accounting_get_ar_aging_detail": aging_detail(),
        "qbo_accounting_get_ap_aging_summary": aging(
            buckets={"Current": 9000.0, "1-30": 1500.0, "31-60": 0.0,
                     "61-90": 0.0, "91+": 0.0}, kind="ap"),
        "profit_loss_quickbooks_account": profit_loss(),
        "profit_loss_quickbooks_account_prior": profit_loss(
            income=201400.00, expenses=163220.00, period="Last month"),
    }
    out.update(overrides)
    return out


def build(**overrides):
    return fb.build(raw(**overrides), today=TODAY, history=[])


# ── P7: the allowlist is read-only by construction ───────────────────────────

def test_every_quickbooks_tool_is_a_read():
    """The same connector deletes invoices, edits payroll and applies for a
    loan. Nothing in this feature may reach one."""
    import re

    allowed = re.compile(
        r"^(company_info|qbo_accounting_get_[a-z_]+|profit_loss_quickbooks_account)$")
    for tool in fb.QUICKBOOKS_TOOLS:
        assert allowed.match(tool), tool


_WRITE_WORDS = ("create", "update", "delete", "submit", "save", "assign",
                "duplicate", "send", "void", "email", "apply", "refund",
                "transfer", "deposit", "upload", "attach", "payment")


def test_no_tool_name_carries_a_write_verb():
    for tool in fb.QUICKBOOKS_TOOLS:
        for word in _WRITE_WORDS:
            assert word not in tool, f"{tool} looks like it writes ({word})"


@pytest.mark.parametrize("planted", [
    "qbo_sales_create_invoice",          # the obvious one
    "qbo_sales_send_invoice_email",      # the one a denylist of verbs misses
    "qbo_payroll_update_employee",
])
def test_the_read_only_scanner_would_catch_a_planted_write_tool(planted):
    """MUTATION CONTROL: proven before the clean pass above is believed."""
    import re

    allowed = re.compile(
        r"^(company_info|qbo_accounting_get_[a-z_]+|profit_loss_quickbooks_account)$")
    caught = (not allowed.match(planted)
              or any(w in planted for w in _WRITE_WORDS))
    assert caught, f"{planted} would have slipped through"


def test_the_tool_list_is_frozen():
    assert isinstance(fb.QUICKBOOKS_TOOLS, tuple)


# ── P11: the arguments are the code's, spelled out ───────────────────────────

def test_the_calls_ask_for_explicit_dates_never_a_macro():
    """A date macro is how a brief ends up reporting the fiscal year."""
    calls = {c["tool"]: c["args"] for c in fb.build_calls(TODAY)}

    assert len(calls) == 7
    assert calls["profit_loss_quickbooks_account"] == {
        "start_date": "2026-09-01", "end_date": ISO, "accounting_method": "Accrual"}
    assert calls["profit_loss_quickbooks_account_prior"] == {
        "start_date": "2026-08-01", "end_date": "2026-08-31",
        "accounting_method": "Accrual"}
    for args in calls.values():
        assert not any("macro" in k.lower() for k in args)


def test_the_prior_month_is_the_month_before_not_thirty_days():
    """On the 10th, "last month" is August, not the 11th of August onward."""
    calls = {c["tool"]: c["args"] for c in fb.build_calls(date(2026, 3, 3))}
    prior = calls["profit_loss_quickbooks_account_prior"]

    assert prior["start_date"] == "2026-02-01"
    assert prior["end_date"] == "2026-02-28"


def test_the_aliased_prior_call_maps_back_to_the_real_tool():
    assert fb.real_tool("profit_loss_quickbooks_account_prior") == \
        "profit_loss_quickbooks_account"
    assert fb.real_tool("company_info") == "company_info"


def test_the_basis_is_passed_and_not_assumed():
    calls = {c["tool"]: c["args"] for c in fb.build_calls(TODAY, basis="Cash")}
    assert calls["qbo_accounting_get_balance_sheet"]["accounting_method"] == "Cash"


# ── P12: the shape the skill and the email read ──────────────────────────────

def test_the_report_carries_every_key_the_renderer_and_skill_need():
    report = build()
    for key in ("company", "as_of", "basis", "currency", "cash", "ar", "ap",
                "pl_mtd", "pl_prior", "deltas", "sections", "values",
                "finance_md", "ar_top_overdue"):
        assert key in report, key
    assert report["company"]["name"] == "Northwind Solar"
    assert report["currency"] == "USD"


# ── P13: the period comes from the response ──────────────────────────────────

def test_a_soft_refusal_is_not_mistaken_for_data():
    """The live shape of a wrong answer: HTTP success, no error flag, and a
    body saying the profile is incomplete. It is non-empty, so nothing falls
    back on its own, and a brief would render an empty month as a real one."""
    report = build(profit_loss_quickbooks_account=refused("industry"))

    assert report["pl_mtd"]["measured"] is False
    assert "industry" in report["pl_mtd"]["reason"]
    assert "fills in by itself" in report["pl_mtd"]["reason"]


def test_the_period_a_ranged_report_covers_is_owned_by_the_request():
    """A ranged report names a phrase ("This month"), not dates, so the guard
    against a fiscal-year answer under a monthly label is the argument check in
    connector_fetch. The dates shown here are the ones that were asked for."""
    report = build()
    assert report["pl_mtd"]["start"] == "2026-09-01"
    assert report["pl_mtd"]["end"] == ISO


def test_a_correct_period_is_accepted():
    """MUTATION CONTROL: the check above is not refusing everything."""
    report = build()
    assert report["pl_mtd"]["measured"] is True
    assert report["pl_mtd"]["net"] == 33210.0


def test_a_stale_balance_sheet_is_refused():
    report = build(qbo_accounting_get_balance_sheet=balance_sheet(
        as_of="2026-08-31"))
    assert report["cash"]["measured"] is False
    assert "2026-08-31" in report["cash"]["reason"]


def test_the_basis_shown_is_the_one_the_report_reported():
    """Asked for accrual, answered on cash: the page says cash."""
    cash_pl = profit_loss()
    cash_pl["accountingMethod"] = "Cash"
    report = fb.build(raw(profit_loss_quickbooks_account=cash_pl),
                      today=TODAY, basis="Accrual", history=[])

    assert report["pl_mtd"]["basis"] == "Cash"
    assert "on a cash basis" in report["finance_md"]


# ── P14: one company, named ──────────────────────────────────────────────────

def test_the_wrong_company_shows_no_numbers_at_all():
    """Someone who runs four companies cannot tell from the figures which
    one answered, so the figures are withheld rather than labelled."""
    report = fb.build(raw(), today=TODAY, history=[],
                      expected_company={"id": REALM, "name": "Northwind Solar"})
    assert report["cash"]["measured"] is True

    other = raw(company_info=company(name="Someone Else Inc"))
    for key in ("qbo_accounting_get_balance_sheet",
                "qbo_accounting_get_ar_aging_summary",
                "qbo_accounting_get_ap_aging_summary"):
        other[key] = dict(other[key], realmId="5550001111")
    wrong = fb.build(other, today=TODAY, history=[],
                     expected_company={"id": REALM, "name": "Northwind Solar"})
    assert wrong["cash"]["measured"] is False
    assert "WRONG-COMPANY" in wrong["cash"]["reason"]
    assert "Someone Else Inc" in wrong["cash"]["reason"]
    assert "Northwind Solar" in wrong["cash"]["reason"]
    assert "412,908" not in wrong["finance_md"]


def test_an_unpinned_company_still_renders():
    """A fresh install has not recorded an id yet, and must still work."""
    report = fb.build(raw(), today=TODAY, history=[], expected_company={})
    assert report["cash"]["measured"] is True


# ── P15: cash is defined, and cards are not netted ───────────────────────────

def test_cash_is_the_bank_accounts_listed_by_name():
    cash = build()["cash"]

    assert cash["total"] == 497908.33
    assert [a["name"] for a in cash["accounts"]] == ["Operating Checking",
                                                     "Payroll Savings"]


def test_a_credit_card_is_shown_separately_and_never_subtracted():
    """Both facts are real; netting them answers a question nobody asked."""
    report = build(qbo_accounting_get_balance_sheet=balance_sheet(cards=18420.55))
    cash = report["cash"]

    assert cash["credit_cards_total"] == 18420.55
    assert cash["total"] == 497908.33, "the card must not be netted off"
    assert "$479,487.78" not in report["finance_md"], "cash minus the card"


def test_every_account_under_bank_accounts_is_counted_and_named():
    """Whatever the books file under Bank Accounts is cash, including the odd
    ones: a real set held a clearing account and an investment account."""
    report = build(qbo_accounting_get_balance_sheet=balance_sheet(
        accounts=[("Business Checking", 1000.00), ("Clearing Account", 250.00)]))

    assert report["cash"]["total"] == 1250.0
    assert [a["name"] for a in report["cash"]["accounts"]] == \
        ["Business Checking", "Clearing Account"]


def test_a_balance_sheet_with_no_bank_accounts_is_not_measured():
    report = build(qbo_accounting_get_balance_sheet=balance_sheet(
        accounts=[], cards=[("Amex", "100.00")]))
    assert report["cash"]["measured"] is False


# ── P19: zero is not a measurement ───────────────────────────────────────────

def test_an_empty_aging_report_is_not_reported_as_nothing_owed():
    """An empty report and an empty ledger both total zero, and only one of
    them is true."""
    empty = aging(buckets={"Current": 0.0, "1-30": 0.0, "31-60": 0.0,
                           "61-90": 0.0, "91+": 0.0}, rows_named=[])
    report = build(qbo_accounting_get_ar_aging_summary=empty)

    assert report["ar"]["measured"] is False
    assert "confirmed zero" in report["ar"]["reason"]
    assert "$0.00 outstanding" not in report["finance_md"]


def test_a_real_balance_is_still_measured():
    """MUTATION CONTROL for the rule above."""
    assert build()["ar"]["measured"] is True


# ── Aging arithmetic ─────────────────────────────────────────────────────────

def test_overdue_is_every_bucket_except_current():
    ar = build()["ar"]

    assert ar["buckets"]["current"] == 12500.0
    assert ar["overdue_total"] == 14650.0, "8300 + 4100 + 0 + 2250"
    assert ar["total"] == 27150.0


def test_the_top_overdue_names_are_sorted_by_size():
    """The summary names its own worst payers, largest first."""
    report = build(qbo_accounting_get_ar_aging_summary=aging(
        rows_named=[("Acme Foods", 2250.0), ("Borden Ltd", 4100.0)]))
    assert [r["name"] for r in report["ar_top_overdue"]] == \
        ["Borden Ltd", "Acme Foods"]


def test_an_unreadable_aging_report_says_so():
    report = build(qbo_accounting_get_ar_aging_summary={
        "reportTitle": "A/R Aging Summary", "asOf": ISO,
        "reportData": {"rows": []}, "realmId": REALM})
    assert report["ar"]["measured"] is False


def test_a_total_that_disagrees_with_its_own_rows_is_refused():
    """The connector computes its own totals. They are reported only after the
    rows underneath are added up and found to agree."""
    lying = aging(rows_named=[("Acme Foods", 27150.0)])
    lying["summary"]["totalReceivables"] = 99999.0
    report = build(qbo_accounting_get_ar_aging_summary=lying)

    assert report["ar"]["measured"] is False
    assert "does not match" in report["ar"]["reason"]


def test_a_cash_total_that_disagrees_with_its_accounts_is_refused():
    report = build(qbo_accounting_get_balance_sheet=balance_sheet(
        accounts=[("Operating Checking", 100.0)], cash_override=99999.0))

    assert report["cash"]["measured"] is False
    assert "does not match" in report["cash"]["reason"]


# ── P18: polarity is a table ─────────────────────────────────────────────────

def test_polarity_disagrees_across_measures_on_purpose():
    assert fb.POLARITY["cash"] == "up"
    assert fb.POLARITY["ar_overdue"] == "down"
    assert fb.POLARITY["ap_overdue"] == "down"


@pytest.mark.parametrize("measure, word, good", [
    ("cash", "up", True), ("cash", "down", False),
    ("ar_overdue", "up", False), ("ar_overdue", "down", True),
    ("net_income", "up", True),
    ("ar_total", "up", None), ("cash", "flat", None),
])
def test_good_news_is_decided_per_measure(measure, word, good):
    assert fb.is_good(measure, word) is good


def test_inverting_a_polarity_row_flips_the_sentence():
    """MUTATION: this is the test that fails if POLARITY stops being read."""
    history = [{"date": "2026-09-03", "kind": "weekly",
                "values": {"ar_overdue": 5000.0}}]
    report = fb.build(raw(), today=TODAY, history=history)
    assert report["deltas"]["ar_overdue"]["good"] is False
    assert "wrong direction" in report["finance_md"]

    original = fb.POLARITY["ar_overdue"]
    try:
        fb.POLARITY["ar_overdue"] = "up"
        flipped = fb.build(raw(), today=TODAY, history=history)
        assert flipped["deltas"]["ar_overdue"]["good"] is True
    finally:
        fb.POLARITY["ar_overdue"] = original


def test_every_money_line_carries_a_direction_or_says_there_is_none():
    """A figure with no direction leaves the reader to guess which way is good."""
    import re

    md = fb.build(raw(), today=TODAY, history=[{
        "date": "2026-09-03", "kind": "weekly",
        "values": {"cash": 400000.0, "ar_overdue": 5000.0,
                   "ap_overdue": 100.0, "net_income": 10000.0}}])["finance_md"]

    directional = ("up", "down", "unchanged", "no comparison yet",
                   "good direction", "wrong direction",
                   # A closed month is context, and saying so IS the
                   # direction: it tells the reader this number is not
                   # the one that moved.
                   "does not move")
    for line in md.splitlines():
        if not re.search(r"\$[\d,]+\.\d{2}", line):
            continue
        if line.startswith("- ") or line.startswith("By age:"):
            continue            # itemised components of a line that has one
        assert any(word in line for word in directional), line


def test_the_direction_scanner_can_fail():
    """MUTATION CONTROL: a line with a figure and no direction is caught."""
    import re

    line = "Cash is $412,908.33 today."
    assert re.search(r"\$[\d,]+\.\d{2}", line)
    assert not any(w in line for w in ("up", "down", "unchanged",
                                       "no comparison yet"))


# ── P21: the baseline is the previous WEEKLY brief ───────────────────────────

def test_a_manual_run_never_becomes_the_comparison_baseline():
    """A Monday brief saying "vs Thursday" answers a different question than
    the heading asks."""
    history = [
        {"date": "2026-09-03", "kind": "weekly", "values": {"cash": 400000.0}},
        {"date": "2026-09-08", "kind": "manual", "values": {"cash": 495000.0}},
    ]
    deltas = fb.build(raw(), today=TODAY, history=history)["deltas"]

    assert deltas["cash"]["prior_date"] == "2026-09-03"
    assert deltas["cash"]["prior"] == 400000.0


def test_with_no_weekly_history_the_brief_says_there_is_no_comparison():
    report = fb.build(raw(), today=TODAY, history=[])
    assert report["deltas"] == {}
    assert "no comparison yet" in report["finance_md"]


def test_a_later_weekly_row_wins():
    history = [
        {"date": "2026-08-27", "kind": "weekly", "values": {"cash": 1.0}},
        {"date": "2026-09-03", "kind": "weekly", "values": {"cash": 400000.0}},
    ]
    deltas = fb.build(raw(), today=TODAY, history=history)["deltas"]
    assert deltas["cash"]["prior_date"] == "2026-09-03"


def test_todays_own_row_is_not_its_own_baseline():
    history = [{"date": ISO, "kind": "weekly", "values": {"cash": 1.0}}]
    assert fb.build(raw(), today=TODAY, history=history)["deltas"] == {}


def test_the_history_keeps_one_row_per_day_and_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(fb.config_loader, "logs_dir", lambda: tmp_path)
    fb.write_history({"date": ISO, "kind": "weekly", "values": {"cash": 1.0}})
    fb.write_history({"date": ISO, "kind": "weekly", "values": {"cash": 2.0}})
    fb.write_history({"date": ISO, "kind": "manual", "values": {"cash": 3.0}})
    rows = fb.read_history()

    assert len(rows) == 2, "the second weekly run replaces the first"
    weekly = [r for r in rows if r["kind"] == "weekly"]
    assert weekly[0]["values"]["cash"] == 2.0


# ── P20: degraded honestly ───────────────────────────────────────────────────

def test_a_missing_section_is_announced_in_the_first_line():
    report = build(qbo_accounting_get_balance_sheet=None)
    first = report["finance_md"].splitlines()[0]

    assert "missing" in first.lower()
    assert "cash" in first.lower()


def test_a_complete_brief_does_not_claim_to_be_missing_anything():
    """MUTATION CONTROL for the line above."""
    assert "missing" not in build()["finance_md"].splitlines()[0].lower()


def test_the_other_sections_survive_one_failed_report():
    report = build(qbo_accounting_get_ar_aging_summary=None)

    assert report["ar"]["measured"] is False
    assert report["cash"]["measured"] is True
    assert "$412,908.33" in report["finance_md"]


# ── P23: voice and dashes ────────────────────────────────────────────────────

def test_no_emdash_or_endash_reaches_the_reader():
    em, en = "\u2014", "\u2013"   # escapes: a dash sweep over this file must not redefine what it hunts
    report = build()
    text = json.dumps(report, default=str)

    assert em not in text and en not in text


def test_the_dash_scanner_can_see_a_planted_one():
    """MUTATION CONTROL: proven before the zero above is trusted."""
    em = "\u2014"
    assert em in f"Cash is up {em} which is good"


_JARGON = ("measured", "bucket", "sidecar", "null", "None", "degraded",
           "tool_result", "connector_fetch", "ColData")


def test_no_data_model_vocabulary_reaches_the_page():
    md = build()["finance_md"]
    for word in _JARGON:
        assert word not in md, f"{word!r} is vocabulary from the data model"


def test_an_unmeasured_section_still_reads_as_english():
    md = build(qbo_accounting_get_balance_sheet=None)["finance_md"]
    assert "Not measured: the balance sheet did not come back." in md
    for word in ("None", "null", "ColData"):
        assert word not in md


# ── Money formatting ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    (412908.33, "$412,908.33"), (0.0, "$0.00"), (-1500.5, "-$1,500.50"),
    (None, "not measured"),
])
def test_money_never_renders_a_missing_number_as_zero(value, expected):
    assert fb.money(value) == expected


@pytest.mark.parametrize("text, expected", [
    ("1,234.56", 1234.56), ("$1,234.56", 1234.56), ("(500.00)", -500.0),
    ("", None), (None, None), ("n/a", None),
])
def test_quickbooks_money_strings_parse(text, expected):
    assert fb._num(text) == expected


def test_the_company_name_is_read_from_prose():
    """company_info answers in prose, not JSON, and names no id."""
    assert fb.read_company(company("Acme Solar"))["name"] == "Acme Solar"


def test_the_company_id_comes_from_the_reports_own_realm():
    """The id that identifies the books travels with the numbers, so it cannot
    drift from them."""
    out = fb.read_company(company(), [{"realmId": "5551234"}])
    assert out["id"] == "5551234"


def test_a_report_wrapped_in_prose_is_still_parsed():
    text = "Here is the report:\n" + json.dumps(balance_sheet()) + "\nHope that helps."
    assert fb._as_json(text)["reportTitle"] == "Balance Sheet"


def test_the_basis_reads_as_english_for_both_values():
    """"on a accrual basis" reads as a typo, and only a cold read finds it."""
    report = build()
    assert "on an accrual basis" in report["finance_md"]

    cash_pl = profit_loss()
    cash_pl["accountingMethod"] = "Cash"
    cash_basis = fb.build(raw(profit_loss_quickbooks_account=cash_pl),
                          today=TODAY, history=[])
    assert "on a cash basis" in cash_basis["finance_md"]


def test_an_unmeasured_reason_does_not_punctuate_itself():
    """The renderer ends the sentence, so a reason that also ends it renders
    "... fills in by itself..", which reads as a typo."""
    assert fb._unmeasured("it did not come back.")["reason"] == \
        "it did not come back"
    report = build(qbo_accounting_get_balance_sheet=None)
    assert ".." not in report["finance_md"]


def test_the_double_period_scanner_can_see_a_planted_one():
    """MUTATION CONTROL."""
    assert ".." in "Not measured: it did not come back.."
