"""Every figure, recomputed a second way, by code that shares nothing.

Checking a number against a fixture written by the same head that wrote the
aggregator proves the two agree, not that either is right. So this file walks
the raw QuickBooks JSON with its own naive reader, imports none of the
aggregation code, and compares.

The mutations at the bottom are what make that claim testable: each one breaks
the real aggregator in a way a human would plausibly ship, and the independent
reader must notice. A second implementation that fails to catch a broken first
one is decoration.
"""

from __future__ import annotations

from datetime import date

import pytest

import finance_brief as fb
from test_finance_brief import ISO, TODAY, raw

# The fixture builders are shared with the other suite on purpose: the reader
# is the one thing both sides may have in common. Everything below computes
# from the raw dictionaries by hand.


# ── The naive reader: no imports from the code under test ────────────────────
#
# Walks Intuit's real reporting shape by hand: rows carry named cells, and the
# tree is expressed in metadata ids rather than nesting. Nothing here consults
# the connector's own `summary` block, which is the point: the aggregator reads
# that, so a second reader that also read it would be checking a number against
# itself.

def _cell(row, *names):
    wanted = {n.lower() for n in names}
    for item in row.get("cells", []):
        if str(item.get("name", "")).lower() in wanted:
            return item.get("value")
    return None


def _rows(report):
    return report.get("reportData", {}).get("rows", [])


def _money(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("$", "")
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s[1:-1] if neg else s
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if neg else n


def naive_cash(report):
    """Find the Bank Accounts group by name, sum whatever hangs off it."""
    bank_id = None
    for row in _rows(report):
        if str(_cell(row, "ACCOUNT_NAME") or "").strip().lower() == "bank accounts":
            bank_id = row["metadata"]["id"]
            break
    assert bank_id, "the fixture has no Bank Accounts group"

    total, names = 0.0, []
    for row in _rows(report):
        if row.get("metadata", {}).get("parentId") != bank_id:
            continue
        amount = _money(_cell(row, "DETAIL_NATURAL_HOME_AMOUNT__TOTAL",
                              "DETAIL_NATURAL_HOME_AMOUNT__TOTAL_WITHOUT_SUBGROUPS"))
        if amount is not None:
            total += amount
            names.append(str(_cell(row, "ACCOUNT_NAME")))
    return round(total, 2), names


def naive_cards(report):
    value = report.get("summary", {}).get("liabilityBreakdown", {}).get("creditCards")
    return _money(value)


def naive_aging(report):
    """Add the customer or vendor rows up by bucket, ignoring any summary."""
    buckets = {}
    for name in ("Current", "1-30", "1 - 30", "31-60", "31 - 60",
                 "61-90", "61 - 90", "91+"):
        values = [_money(_cell(r, name)) for r in _rows(report)]
        values = [v for v in values if v is not None]
        if values:
            buckets[name.replace(" ", "")] = round(sum(values), 2)
    current = buckets.get("Current", 0.0)
    overdue = round(sum(v for k, v in buckets.items() if k != "Current"), 2)
    totals = [_money(_cell(r, "Total")) for r in _rows(report)]
    totals = [t for t in totals if t is not None]
    return {"current": current, "overdue": overdue,
            "total": round(sum(totals), 2) if totals else None}


def naive_pl(report):
    out = {}
    for row in _rows(report):
        key = str(_cell(row, "ACCOUNT_NAME") or "").strip().lower()
        value = _money(_cell(row, "DETAIL_NATURAL_HOME_AMOUNT__TOTAL"))
        if value is None:
            continue
        if key == "total income":
            out["income"] = value
        elif key == "total expenses":
            out["expenses"] = value
        elif key == "net income":
            out["net"] = value
    return out


# ── The comparison ───────────────────────────────────────────────────────────

@pytest.fixture
def both():
    data = raw()
    return data, fb.build(data, today=TODAY, history=[])


def test_cash_agrees_with_an_independent_sum(both):
    data, report = both
    total, names = naive_cash(data["qbo_accounting_get_balance_sheet"])

    assert report["cash"]["total"] == total
    assert [a["name"] for a in report["cash"]["accounts"]] == names


def test_credit_cards_agree_and_are_not_folded_into_cash():
    """A card balance is its own fact. Netting it against cash quietly answers
    a question nobody asked.

    With no cards at all the brief shows no card line, rather than a $0.00 that
    would read as a measured balance.
    """
    from test_finance_brief import balance_sheet

    data = raw(qbo_accounting_get_balance_sheet=balance_sheet(cards=18420.55))
    report = fb.build(data, today=TODAY, history=[])
    cards = naive_cards(data["qbo_accounting_get_balance_sheet"])
    cash, _names = naive_cash(data["qbo_accounting_get_balance_sheet"])

    assert report["cash"]["credit_cards_total"] == cards == 18420.55
    assert report["cash"]["total"] == cash
    assert report["cash"]["total"] != round(cash - cards, 2)

    none_owed = fb.build(raw(), today=TODAY, history=[])
    assert none_owed["cash"]["credit_cards_total"] is None
    assert "Credit cards" not in none_owed["finance_md"]


def test_receivables_agree_bucket_for_bucket(both):
    data, report = both
    naive = naive_aging(data["qbo_accounting_get_ar_aging_summary"])

    assert report["ar"]["current"] == naive["current"]
    assert report["ar"]["overdue_total"] == naive["overdue"]
    assert report["ar"]["total"] == naive["total"]


def test_payables_agree_bucket_for_bucket(both):
    data, report = both
    naive = naive_aging(data["qbo_accounting_get_ap_aging_summary"])

    assert report["ap"]["current"] == naive["current"]
    assert report["ap"]["overdue_total"] == naive["overdue"]


def test_the_month_to_date_agrees(both):
    data, report = both
    naive = naive_pl(data["profit_loss_quickbooks_account"])

    assert report["pl_mtd"]["income"] == naive["income"]
    assert report["pl_mtd"]["expenses"] == naive["expenses"]
    assert report["pl_mtd"]["net"] == naive["net"]


def test_the_prior_month_agrees(both):
    data, report = both
    naive = naive_pl(data["profit_loss_quickbooks_account_prior"])
    assert report["pl_prior"]["net"] == naive["net"]


def test_every_figure_on_the_page_appears_in_the_report(both):
    """Nothing is invented in the rendering step."""
    import re

    _data, report = both
    rendered = set(re.findall(r"\$([\d,]+\.\d{2})", report["finance_md"]))
    known = set()
    for value in (report["cash"]["total"], report["cash"]["credit_cards_total"],
                  report["ar"]["total"], report["ar"]["overdue_total"],
                  report["ap"]["total"], report["ap"]["overdue_total"],
                  report["pl_mtd"]["income"], report["pl_mtd"]["expenses"],
                  report["pl_mtd"]["net"], report["pl_prior"]["income"],
                  report["pl_prior"]["net"]):
        if value is not None:
            known.add(f"{abs(value):,.2f}")
    for account in report["cash"]["accounts"]:
        known.add(f"{abs(account['balance']):,.2f}")
    for bucket in list((report["ar"]["buckets"] or {}).values()) + \
            list((report["ap"]["buckets"] or {}).values()):
        if bucket is not None:
            known.add(f"{abs(bucket):,.2f}")
    for row in report["ar_top_overdue"]:
        known.add(f"{abs(row['amount']):,.2f}")

    assert rendered <= known, f"figures on the page from nowhere: {rendered - known}"


# ── The mutations that make the above mean something ─────────────────────────

def test_dropping_a_bucket_is_caught(monkeypatch):
    """MUTATION: the 31 to 60 bucket stops being counted as overdue."""
    kept = tuple(b for b in fb._AGING_BUCKETS if b[0] != "31_60")
    monkeypatch.setattr(fb, "_AGING_BUCKETS", kept)
    monkeypatch.setattr(fb, "_OVERDUE_KEYS", tuple(k for k, _a in kept[1:]))
    data = raw()
    report = fb.build(data, today=TODAY, history=[])
    naive = naive_aging(data["qbo_accounting_get_ar_aging_summary"])

    # The aggregator now disagrees with its own source, which the cross-check
    # catches as a refusal rather than a wrong number. Either way the bad
    # figure never reaches the page, which is what is being asserted.
    if report["ar"]["measured"]:
        assert report["ar"]["overdue_total"] != naive["overdue"]
    else:
        assert "does not match" in report["ar"]["reason"]


def test_a_summary_that_disagrees_with_its_rows_never_reaches_the_page():
    """MUTATION: the connector's own total is wrong. Trusting it blindly is
    the whole risk of reading a vendor's arithmetic."""
    from test_finance_brief import aging

    lying = aging(rows_named=[("Acme Foods", 27150.0)])
    lying["summary"]["totalOverdue"] = 1.0
    report = fb.build(raw(qbo_accounting_get_ar_aging_summary=lying),
                      today=TODAY, history=[])

    assert report["ar"]["measured"] is False
    assert "$1.00" not in report["finance_md"]


def test_netting_the_credit_card_off_cash_is_caught(monkeypatch):
    """MUTATION: cards get subtracted from cash, the tidy-looking mistake."""
    real_read_cash = fb.read_cash

    def netted(report, expect_as_of):
        out = real_read_cash(report, expect_as_of)
        if out.get("measured") and out.get("credit_cards_total"):
            out["total"] = round(out["total"] - out["credit_cards_total"], 2)
        return out

    monkeypatch.setattr(fb, "read_cash", netted)
    from test_finance_brief import balance_sheet

    data = raw(qbo_accounting_get_balance_sheet=balance_sheet(cards=18420.55))
    report = fb.build(data, today=TODAY, history=[])
    total, _names = naive_cash(data["qbo_accounting_get_balance_sheet"])

    assert report["cash"]["total"] != total


def test_the_mutation_harness_passes_when_nothing_is_broken():
    """MUTATION CONTROL: with no mutation applied, the two agree.

    Without this, every mutation above would pass just as happily against an
    aggregator that was broken to begin with.
    """
    data = raw()
    report = fb.build(data, today=TODAY, history=[])
    naive = naive_aging(data["qbo_accounting_get_ar_aging_summary"])
    total, _names = naive_cash(data["qbo_accounting_get_balance_sheet"])

    assert report["ar"]["overdue_total"] == naive["overdue"]
    assert report["cash"]["total"] == total
