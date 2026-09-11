#!/usr/bin/env python3
"""Contact capture: promote the people you actually correspond with.

Gmail already collects every address you send to, in a hidden bucket called
"Other contacts". Most people have thousands there and a few dozen in their
real address book, because saving one is a manual click on a menu nobody
opens. The gap is not a data problem, it is a friction problem, so this job
does the clicking: once a week it finds the people the account owner has had a
real two-way exchange with, promotes them into real Contacts, and fills in what
the person's own signature already said.

Three things about this are load-bearing.

**Two-way, not two-hundred.** Promoting everyone you ever emailed turns an
address book into a mailing list: newsletters, no-reply senders, and everyone
who was ever CC'd beside you. The bar here is a real exchange, which means the
owner sent to them AND they sent back. That single filter is what keeps the
result meaningful, and it is why the count this job reports is small compared
to the bucket it reads.

**A copy, then an update.** "Other contacts" is read-only and carries only
names, emails and phone numbers, so there is no way to enrich in place. Every
promotion is therefore two calls: copy the contact into the real address book,
then update it with what we parsed. The People API also requires mutations for
one user to be sequential, so nothing here runs in a thread pool even though
the Gmail reads that feed it could.

**The address book is not a scratch pad.** Writing a contact is easy to do and
tedious to undo, so a run only writes when it is told to. `--dry-run` (the
default) reports what it would promote and touches nothing. This is also why
the first real run is gated on a human looking at that report: a bad filter
does not cost a wasted run, it costs an afternoon of manual cleanup.

Owner-generic by design: it reads whichever Google accounts are configured and
treats each one's own address as "the owner", so the same job serves any
install rather than any one person's mailbox.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_loader                                            # noqa: E402
import run_ledger                                               # noqa: E402
import self_anneal                                              # noqa: E402

JOB_NAME = "contact.capture"
STATE_NAME = "contact_capture_state.json"

# What a promotion copies out of "Other contacts". These are the only three
# fields the API permits in a copyMask, so the list is a fact about the API
# rather than a preference.
COPY_MASK = "names,emailAddresses,phoneNumbers"

# What we read back and write. personFields is required on most People calls,
# and an over-broad mask is a privacy surface as much as a payload cost, so it
# names exactly what this job uses.
READ_MASK = "names,emailAddresses,phoneNumbers,organizations,biographies,metadata"

# Addresses that are structurally incapable of a two-way exchange. Matched on
# the local part so a domain is never condemned by one noreply mailbox.
ROBOT_LOCAL_RE = re.compile(
    r"^(no[-._]?reply|do[-._]?not[-._]?reply|donotreply|notifications?|alerts?"
    r"|mailer[-._]?daemon|postmaster|bounces?|support|help|info|admin|billing"
    r"|invoices?|receipts?|news|newsletter|updates?|marketing|team|hello"
    r"|contact|sales|careers|jobs|security|abuse|automated|auto[-._]?confirm"
    r"|calendar[-._]?notification|via)([-._+].*)?$",
    re.I,
)

# Domains that never carry a person worth saving.
ROBOT_DOMAIN_RE = re.compile(
    r"(^|\.)(bounce|bounces|email|mail|mailer|reply|replies|notifications?|news"
    r"|marketing|sendgrid\.net|mailgun\.org|amazonses\.com|mcsv\.net"
    r"|mailchimpapp\.net|sparkpostmail\.com|salesforce\.com)$",
    re.I,
)

# Free/consumer mail domains: a real person, but the domain says nothing about
# where they work, so it must never become an organization name.
CONSUMER_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
    "mac.com", "proton.me", "protonmail.com", "pm.me", "gmx.com", "mail.com",
    "zoho.com", "fastmail.com", "hey.com", "comcast.net", "verizon.net",
    "sbcglobal.net", "att.net", "cox.net", "charter.net", "bellsouth.net",
})


# ---------------------------------------------------------------- state


def state_path() -> Path:
    return config_loader.logs_dir() / STATE_NAME


def load_state() -> dict:
    """Everything already promoted, so a rerun is cheap and never duplicates.

    Keyed by lowercased email. The address book itself is the real source of
    truth (and is checked too), but that check costs an API call per candidate;
    this file makes the common case free and, more importantly, remembers the
    people we deliberately SKIPPED so a backfill that is interrupted resumes
    instead of re-deciding ten thousand addresses.
    """
    try:
        with open(state_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("promoted", {})
            data.setdefault("skipped", {})
            data.setdefault("last_run", "")
            return data
    except Exception:                                           # noqa: BLE001
        pass
    return {"promoted": {}, "skipped": {}, "last_run": ""}


def save_state(state: dict) -> None:
    """Write the state file. Never raises: bookkeeping must not fail a run."""
    try:
        p = state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        tmp.replace(p)
    except Exception:                                           # noqa: BLE001
        pass


# ---------------------------------------------------------------- filters


def is_robot_address(email: str) -> bool:
    """True for an address that cannot hold up its end of a conversation."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return True
    local, _, domain = email.partition("@")
    if ROBOT_LOCAL_RE.match(local):
        return True
    if ROBOT_DOMAIN_RE.search(domain):
        return True
    # A VERP or plus-tagged bounce address: long opaque local parts that are
    # generated per-send and never belong to a person.
    if len(local) > 40 and any(c.isdigit() for c in local):
        return True
    return False


def org_from_domain(email: str) -> str:
    """A company name inferred from the domain, or "" when it says nothing.

    Only ever a guess, so it is written to the organization field where a
    wrong value is visible and editable, never merged into the person's name.
    """
    _, _, domain = (email or "").lower().partition("@")
    domain = domain.strip()
    if not domain or domain in CONSUMER_DOMAINS:
        return ""
    parts = [p for p in domain.split(".") if p]
    if len(parts) < 2:
        return ""
    # Drop a public suffix of one or two labels (co.uk, com.au, com).
    stem = parts[-3] if len(parts) >= 3 and len(parts[-2]) <= 3 and len(parts[-1]) <= 3 else parts[-2]
    if stem in {"gmail", "googlemail", "email", "mail"}:
        return ""
    return stem.replace("-", " ").title()


# ---------------------------------------------------------------- signatures

# A signature line that looks like a job title. Deliberately conservative: a
# wrong title on a contact is worse than no title, because the reader believes
# it.
# A signature line that looks like a job title. Deliberately conservative: a
# wrong title on a contact is worse than no title, because the reader believes
# it. Built as "optional modifiers, then a role noun", which is why a compound
# like "Chief Financial Officer" or "Senior Development Manager" matches: the
# words between the seniority word and the role are allowed but not required.
_ROLE = (
    r"CEO|CTO|CFO|COO|CIO|CMO|CRO|President|Partner|Founder|Co[- ]?Founder|"
    r"Owner|Director|Manager|Engineer|Developer|Designer|Analyst|Consultant|"
    r"Advisor|Adviser|Counsel|Attorney|Controller|Accountant|Broker|Agent|"
    r"Realtor|Superintendent|Estimator|Coordinator|Specialist|Administrator|"
    r"Representative|Officer|Architect|Scientist|Researcher|Recruiter|"
    r"Supervisor|Foreman|Technician|Planner|Buyer|Underwriter|Originator|"
    r"VP|SVP|EVP|AVP|Principal|Chairman|Chairwoman|Chair|Treasurer|Secretary"
)
_SENIORITY = (
    r"Sr|Jr|Senior|Junior|Lead|Head|Chief|Global|Regional|Managing|Executive|"
    r"Associate|Assistant|Deputy|Vice|Principal|Staff|Group|Interim|General"
)
# Up to three plain words may sit between the seniority word and the role
# ("Chief FINANCIAL Officer", "Senior BUSINESS DEVELOPMENT Manager").
TITLE_RE = re.compile(
    r"^\s*(?P<title>(?:(?:%s)\.?\s+)*(?:[A-Z][A-Za-z&/]*\s+){0,3}(?:%s))"
    r"(?:\s*(?:[,|]|\s+at\s+|\s+of\s+)\s*(?P<org>[^|\n]{2,60}?))?\s*$"
    % (_SENIORITY, _ROLE),
    re.I,
)

PHONE_RE = re.compile(
    r"(?:(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}"
    r"|\+\d{1,3}[\s.-]?\d[\d\s.-]{6,14}\d)"
)

# Where a signature starts. Anything above the first of these is body text.
SIG_START_RE = re.compile(
    r"^\s*(--\s*$|\u2014\s*$|Best|Best regards|Regards|Kind regards|Warm regards|"
    r"Thanks|Thank you|Thanks so much|Cheers|Sincerely|Respectfully|"
    r"All the best|Talk soon|Speak soon|Warmly|Yours truly|Sent from my)",
    re.I,
)

# Lines that are never a person's title or company.
SIG_NOISE_RE = re.compile(
    r"(unsubscribe|confidential|privileged|this email|disclaimer|"
    r"do not disclose|intended recipient|virus|©|\bAll rights reserved\b|"
    r"^\s*https?://|^\s*www\.|@)",
    re.I,
)


def parse_signature(body: str, sender_name: str = "") -> dict:
    """Pull title, phone and company out of the sender's own sign-off.

    Reads only the tail of the message, after a sign-off marker where one
    exists. Everything is optional, and a field is left empty rather than
    guessed: this data is shown to the user as fact.
    """
    out = {"title": "", "phone": "", "org": ""}
    if not body:
        return out

    lines = [ln.rstrip() for ln in body.replace("\r\n", "\n").split("\n")]

    # Find the last sign-off marker; the signature is what follows it. Quoted
    # replies mean a thread can hold several, and the sender's own is last.
    start = None
    for i, ln in enumerate(lines):
        if SIG_START_RE.match(ln) and not ln.strip().startswith(">"):
            start = i
    block = lines[start:start + 12] if start is not None else lines[-12:]
    block = [ln for ln in block
             if ln.strip()
             and not ln.strip().startswith(">")
             and not SIG_NOISE_RE.search(ln)]

    for ln in block:
        if not out["phone"]:
            m = PHONE_RE.search(ln)
            # A bare 4-digit year or a street number should not read as a phone.
            if m and sum(c.isdigit() for c in m.group(0)) >= 10:
                out["phone"] = m.group(0).strip()
        if not out["title"]:
            m = TITLE_RE.match(ln)
            if m:
                # The sender's own name line is not a title, even when it
                # contains a title-shaped word.
                if sender_name and sender_name.lower() in ln.lower():
                    continue
                out["title"] = " ".join(m.group("title").split())
                if m.group("org"):
                    out["org"] = m.group("org").strip(" ,|")
    return out


# ---------------------------------------------------------------- gmail scan


def _hdr(msg: dict, name: str) -> str:
    for h in (msg.get("payload", {}) or {}).get("headers", []) or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "") or ""
    return ""


def _plain_body(payload: dict) -> str:
    """The text/plain part of a message, decoded. Empty when there is none."""
    import base64

    if not payload:
        return ""
    stack = [payload]
    html_fallback = ""
    while stack:
        part = stack.pop(0)
        mime = part.get("mimeType", "")
        data = (part.get("body", {}) or {}).get("data")
        if data and mime == "text/plain":
            try:
                return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
            except Exception:                                   # noqa: BLE001
                pass
        if data and mime == "text/html" and not html_fallback:
            try:
                raw = base64.urlsafe_b64decode(data).decode("utf-8", "replace")
                html_fallback = re.sub(r"<[^>]+>", "\n", raw)
            except Exception:                                   # noqa: BLE001
                pass
        stack.extend(part.get("parts", []) or [])
    return html_fallback


def scan_account(client, owner_email: str, since_days: int,
                 max_messages: int = 4000, log=print) -> dict:
    """Walk the mailbox once and return {email: evidence} for real exchanges.

    One pass over messages, not one query per candidate. The bucket holds
    thousands of addresses and checking each one individually would be
    thousands of API calls; a single date-bounded scan of sent and received
    mail answers the same question and gives us the signature at the same
    time.
    """
    owner = (owner_email or "").strip().lower()
    after = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y/%m/%d")

    sent_to: dict[str, dict] = {}
    heard_from: dict[str, dict] = {}

    for direction, query in (("sent", f"in:sent after:{after}"),
                             ("recv", f"in:anywhere -in:sent -in:chats after:{after}")):
        token, seen = None, 0
        while seen < max_messages:
            resp = (client.gmail.users().messages()
                    .list(userId="me", q=query, pageToken=token,
                          maxResults=min(500, max_messages - seen))
                    .execute())
            ids = [m["id"] for m in resp.get("messages", []) or []]
            if not ids:
                break
            for mid in ids:
                seen += 1
                fmt = "full" if direction == "recv" else "metadata"
                try:
                    msg = (client.gmail.users().messages()
                           .get(userId="me", id=mid, format=fmt,
                                **({"metadataHeaders": ["To", "Cc", "From", "Date"]}
                                   if fmt == "metadata" else {}))
                           .execute())
                except Exception as exc:                        # noqa: BLE001
                    if "insufficient" in str(exc).lower():
                        raise
                    continue

                when = _hdr(msg, "Date")
                try:
                    when_dt = parsedate_to_datetime(when) if when else None
                except Exception:                               # noqa: BLE001
                    when_dt = None

                if direction == "sent":
                    pairs = getaddresses([_hdr(msg, "To"), _hdr(msg, "Cc")])
                    for name, addr in pairs:
                        addr = (addr or "").strip().lower()
                        if not addr or addr == owner or is_robot_address(addr):
                            continue
                        rec = sent_to.setdefault(addr, {"name": "", "last": None, "count": 0})
                        rec["count"] += 1
                        if name and not rec["name"]:
                            rec["name"] = name.strip()
                        if when_dt and (rec["last"] is None or when_dt > rec["last"]):
                            rec["last"] = when_dt
                else:
                    name, addr = (getaddresses([_hdr(msg, "From")]) or [("", "")])[0]
                    addr = (addr or "").strip().lower()
                    if not addr or addr == owner or is_robot_address(addr):
                        continue
                    rec = heard_from.setdefault(addr, {"name": "", "last": None,
                                                       "count": 0, "sig": {},
                                                       "subject": ""})
                    rec["count"] += 1
                    if name and not rec["name"]:
                        rec["name"] = name.strip()
                    if when_dt and (rec["last"] is None or when_dt > rec["last"]):
                        rec["last"] = when_dt
                        rec["subject"] = _hdr(msg, "Subject")
                    if not rec["sig"].get("title") and not rec["sig"].get("phone"):
                        parsed = parse_signature(_plain_body(msg.get("payload", {})),
                                                 rec["name"])
                        if any(parsed.values()):
                            rec["sig"] = parsed
            token = resp.get("nextPageToken")
            if not token:
                break
        log(f"  scanned {seen} {direction} messages")

    # The filter, in one line: they were written to, and they wrote back.
    exchanges = {}
    for addr, sent in sent_to.items():
        got = heard_from.get(addr)
        if not got:
            continue
        exchanges[addr] = {
            "email": addr,
            "name": got["name"] or sent["name"] or "",
            "sent_count": sent["count"],
            "reply_count": got["count"],
            "last_contact": max(d for d in (sent["last"], got["last"]) if d).date().isoformat()
            if (sent["last"] or got["last"]) else "",
            "last_subject": got.get("subject", ""),
            "signature": got.get("sig", {}) or {},
        }
    return exchanges


# ---------------------------------------------------------------- people api


def list_other_contacts(client, log=print) -> dict:
    """Every address in the "Other contacts" bucket, keyed by email.

    This is the bucket Gmail fills automatically and the user never sees. It
    is read-only, which is why promotion is a copy rather than an edit.
    """
    out, token = {}, None
    while True:
        resp = (client.people.otherContacts().list(
            pageSize=1000, pageToken=token,
            readMask="names,emailAddresses,phoneNumbers").execute())
        for person in resp.get("otherContacts", []) or []:
            for ea in person.get("emailAddresses", []) or []:
                addr = (ea.get("value") or "").strip().lower()
                if addr:
                    out.setdefault(addr, person)
        token = resp.get("nextPageToken")
        if not token:
            break
    log(f"  {len(out)} addresses in Other contacts")
    return out


def existing_contact_emails(client, log=print) -> set:
    """Emails already in the real address book, so nobody is added twice."""
    out, token = set(), None
    while True:
        resp = (client.people.people().connections().list(
            resourceName="people/me", pageSize=1000, pageToken=token,
            personFields="emailAddresses").execute())
        for person in resp.get("connections", []) or []:
            for ea in person.get("emailAddresses", []) or []:
                addr = (ea.get("value") or "").strip().lower()
                if addr:
                    out.add(addr)
        token = resp.get("nextPageToken")
        if not token:
            break
    log(f"  {len(out)} already saved as contacts")
    return out


def _split_name(full: str) -> tuple:
    full = " ".join((full or "").split())
    if not full or "@" in full:
        return "", ""
    if "," in full:                       # "Last, First"
        last, _, first = full.partition(",")
        return first.strip(), last.strip()
    bits = full.split()
    return bits[0], " ".join(bits[1:]) if len(bits) > 1 else ""


def build_note(cand: dict, owner_email: str) -> str:
    """The context line written into the contact's notes field.

    Says where this person came from and when they were last heard from, so
    the address book answers "who is this?" a year later.
    """
    bits = [f"Captured by Van Gogh from {owner_email}"]
    if cand.get("last_contact"):
        bits.append(f"last contact {cand['last_contact']}")
    n = cand.get("sent_count", 0) + cand.get("reply_count", 0)
    if n:
        bits.append(f"{n} messages exchanged")
    line = ", ".join(bits) + "."
    subj = (cand.get("last_subject") or "").strip()
    if subj:
        line += f'\nMost recent thread: "{subj[:120]}"'
    return line


def promote(client, cand: dict, other: dict | None, owner_email: str) -> dict:
    """Copy one person into the address book and fill in what we know.

    Two calls, in this order, because "Other contacts" cannot be edited in
    place. When the person is not in that bucket (rare, but a thread can
    outlive the bucket entry) a contact is created outright instead.
    """
    sig = cand.get("signature") or {}
    first, last = _split_name(cand.get("name", ""))
    org = sig.get("org") or org_from_domain(cand["email"])

    if other and other.get("resourceName"):
        created = (client.people.otherContacts()
                   .copyOtherContactToMyContactsGroup(
                       resourceName=other["resourceName"],
                       body={"copyMask": COPY_MASK, "readMask": READ_MASK})
                   .execute())
    else:
        body = {"emailAddresses": [{"value": cand["email"]}]}
        if first or last:
            body["names"] = [{"givenName": first, "familyName": last}]
        created = (client.people.people()
                   .createContact(body=body, personFields=READ_MASK).execute())

    # Now the enrichment the copy could not carry. etag is required on update
    # and comes from the resource we just created, so this cannot clobber a
    # concurrent edit.
    update_body = {"etag": created.get("etag")}
    fields = []
    if org or sig.get("title"):
        update_body["organizations"] = [{
            k: v for k, v in (("name", org), ("title", sig.get("title", ""))) if v
        }]
        fields.append("organizations")
    note = build_note(cand, owner_email)
    if note:
        update_body["biographies"] = [{"value": note, "contentType": "TEXT_PLAIN"}]
        fields.append("biographies")
    if sig.get("phone") and not created.get("phoneNumbers"):
        update_body["phoneNumbers"] = [{"value": sig["phone"]}]
        fields.append("phoneNumbers")
    if not created.get("names") and (first or last):
        update_body["names"] = [{"givenName": first, "familyName": last}]
        fields.append("names")

    if fields:
        created = (client.people.people()
                   .updateContact(resourceName=created["resourceName"],
                                  updatePersonFields=",".join(fields),
                                  personFields=READ_MASK,
                                  body=update_body).execute())
    return created


# ---------------------------------------------------------------- run


SCOPE_HELP = (
    "Contact capture needs two permissions this install has not granted yet "
    "(read your Other contacts, and manage your contacts). Sign in again to "
    "grant them: python app/auth_bootstrap.py"
)


def run(days: int, dry_run: bool, limit: int, account_label: str = "",
        log=print) -> dict:
    """One capture pass over every configured Google account."""
    import data_sources

    if not data_sources.is_tier1():
        raise RuntimeError(
            "Contact capture runs unattended, so it needs the Google sign-in "
            "rather than a chat-session connector. Run /van-gogh:install-van-gogh."
        )

    import google_client as gc

    state = load_state()
    accounts = [a for a in config_loader.google_accounts()
                if not account_label or a["label"] == account_label]
    if not accounts:
        raise RuntimeError("No Google accounts are configured.")

    summary = {"accounts": [], "promoted": 0, "candidates": 0,
               "dry_run": bool(dry_run)}

    for acct in accounts:
        label, owner_email = acct["label"], (acct.get("email") or "")
        log(f"{label} ({owner_email})")
        client = gc.google_client(label)

        try:
            exchanges = scan_account(client, owner_email, days, log=log)
            others = list_other_contacts(client, log=log)
            saved = existing_contact_emails(client, log=log)
        except Exception as exc:                                # noqa: BLE001
            if "insufficient" in str(exc).lower() or "ACCESS_TOKEN_SCOPE" in str(exc):
                raise RuntimeError(f"{SCOPE_HELP} (original error: {exc})") from exc
            raise

        candidates = []
        for addr, cand in sorted(exchanges.items()):
            if addr in saved or addr in state["promoted"]:
                continue
            candidates.append(cand)

        log(f"  {len(candidates)} new two-way contacts to capture")
        summary["candidates"] += len(candidates)

        done, failed = 0, []
        if not dry_run:
            for cand in candidates[:limit] if limit else candidates:
                try:
                    promote(client, cand, others.get(cand["email"]), owner_email)
                    state["promoted"][cand["email"]] = {
                        "at": datetime.now().isoformat(timespec="seconds"),
                        "account": label,
                    }
                    done += 1
                except Exception as exc:                        # noqa: BLE001
                    if "insufficient" in str(exc).lower():
                        raise RuntimeError(f"{SCOPE_HELP} (original error: {exc})") from exc
                    failed.append({"email": cand["email"], "error": str(exc)[:200]})
                if done % 25 == 0 and done:
                    save_state(state)
                    log(f"  ...{done} saved")

        summary["accounts"].append({
            "label": label, "email": owner_email,
            "other_contacts": len(others), "already_saved": len(saved),
            "candidates": len(candidates), "promoted": done,
            "failed": failed[:20],
            "sample": [
                {k: c.get(k) for k in
                 ("email", "name", "last_contact", "sent_count", "reply_count")}
                | {"title": (c.get("signature") or {}).get("title", ""),
                   "org": (c.get("signature") or {}).get("org")
                          or org_from_domain(c["email"])}
                for c in candidates[:25]
            ],
        })
        summary["promoted"] += done

    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    save_state(state)
    return summary


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=365,
                    help="how far back to look for exchanges (default 365)")
    ap.add_argument("--apply", action="store_true",
                    help="actually write contacts; without it nothing is written")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap how many are written in one run (0 = no cap)")
    ap.add_argument("--account", default="",
                    help="only this account label (default: all Google accounts)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    started = run_ledger.now_stamp()
    logs = [] if args.json else None

    def log(msg):
        if logs is None:
            print(msg, flush=True)
        else:
            logs.append(str(msg))

    try:
        summary = run(days=args.days, dry_run=not args.apply,
                      limit=args.limit, account_label=args.account, log=log)
    except Exception as exc:                                    # noqa: BLE001
        detail = str(exc)
        run_ledger.record_run(JOB_NAME, started, run_ledger.now_stamp(), 1, detail)
        try:
            self_anneal.record_failure(JOB_NAME, detail)
        except Exception:                                       # noqa: BLE001
            pass
        if args.json:
            print(json.dumps({"error": detail}, indent=2))
        else:
            print(f"ERROR: {detail}", file=sys.stderr)
        return 1

    run_ledger.record_run(
        JOB_NAME, started, run_ledger.now_stamp(), 0,
        f"{'would promote' if summary['dry_run'] else 'promoted'} "
        f"{summary['candidates'] if summary['dry_run'] else summary['promoted']}")

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        verb = "would capture" if summary["dry_run"] else "captured"
        print(f"\n{verb} {summary['candidates'] if summary['dry_run'] else summary['promoted']} contacts")
        if summary["dry_run"]:
            print("Nothing was written. Re-run with --apply to save them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
