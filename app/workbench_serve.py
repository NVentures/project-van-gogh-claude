#!/usr/bin/env python3
"""The Van Gogh Workbench: a local dashboard served from 127.0.0.1.

Run it with `/van-gogh:workbench`, or directly:

    python app/workbench_serve.py            # serve and open a browser
    python app/workbench_serve.py --no-browser --port 8765

Security posture. This process can re-render briefings through an agentic
`claude -p`, so "it's only localhost" is not a sufficient answer:

* the socket binds 127.0.0.1 and nothing else;
* every `/api/*` call must carry a per-boot token (`X-Workbench-Token`, or
  `?t=` for image tags that cannot set headers), compared in constant time;
* the `Host` header must itself be a loopback name, which is what stops a
  hostile page in the user's browser from resolving a domain it controls to
  127.0.0.1 and talking to this server (DNS rebinding);
* static files are served from an exact-name whitelist, so there is no path to
  traverse out of.

The token is written to `~/.config/van-gogh/workbench_session.json` (0600)
together with the port and pid, which is how the launcher skill finds a running
server and how it knows to restart one left over from an older plugin version.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import sys
import threading
import webbrowser
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import brain_graph                                          # noqa: E402
import user_state                                           # noqa: E402
import workbench_data                                       # noqa: E402
import workbench_store as store                             # noqa: E402
from config_loader import (                                 # noqa: E402
    account_labels,
    force_utf8_io,
    repo_root,
    resolved_meta,
    user_first_name,
    van_gogh_root,
    vault,
    workbench_email_mode,
    workbench_logo_fetch,
)
from config_loader import primary_account as config_primary_account  # noqa: E402
from config_loader import workbench_email_mode as config_workbench_email_mode  # noqa: E402
from data_sources import is_tier1                           # noqa: E402


def _version() -> str:
    """The plugin version, read from the manifest so it can never drift."""
    try:
        with open(repo_root() / ".claude-plugin" / "plugin.json", encoding="utf-8") as f:
            return json.load(f).get("version", "unknown")
    except (OSError, ValueError):
        return "unknown"


VERSION = _version()
STATIC_DIR = Path(__file__).resolve().parent / "workbench_static"
# What the launcher opens. The product's front door is the Morning Coffee page,
# not the dashboard shell: the shell stays reachable at `/?t=<token>` for the
# ledger, the graph and the inbox roll-up, but nobody should have to find it.
LANDING = "morning-coffee"


def launch_url(port: int, token: str) -> str:
    """The one URL the launcher opens and the skill hands to the user."""
    return f"http://127.0.0.1:{port}/briefing/{LANDING}?t={token}"

# Exact-name whitelist. Anything not listed here is a 404 by construction.
STATIC_FILES = {
    "style.css": "text/css; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
    "graph.js": "application/javascript; charset=utf-8",
}

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}

_TOKEN = ""
_jobs: dict = {}
_jobs_lock = threading.Lock()


# ── Session file ─────────────────────────────────────────────────────────────

def session_path() -> Path:
    return user_state.state_dir() / "workbench_session.json"


def write_session(port: int, token: str) -> Path:
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"port": port, "token": token, "pid": os.getpid(),
               "version": VERSION, "started_at": datetime.now().isoformat(timespec="seconds")}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass          # Windows ACLs do not map onto POSIX modes; the token is still per-boot
    return path


def clear_session() -> None:
    try:
        session_path().unlink()
    except OSError:
        pass


# ── Jobs (refresh only, for now) ─────────────────────────────────────────────

def _job_new(kind: str, label: str) -> str:
    job_id = secrets.token_hex(6)
    with _jobs_lock:
        _jobs[job_id] = {"id": job_id, "kind": kind, "label": label,
                         "state": "running", "started_at": datetime.now().isoformat(timespec="seconds"),
                         "finished_at": None, "detail": ""}
    return job_id


def _job_note(job_id: str, detail: str) -> None:
    """Record a non-fatal problem on a running job.

    A path that fails open still owes the user a reason: without this a pack
    that could not be read is indistinguishable from a counterparty the vault
    has never heard of.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            job["detail"] = detail


def _job_finish(job_id: str, state: str, detail: str = "") -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            job["state"] = state
            job["detail"] = detail
            job["finished_at"] = datetime.now().isoformat(timespec="seconds")


def _now_pt_et() -> str:
    """The one time format the product uses, both zones, no dash.

    The zones come from the config helpers, as tzinfo objects: fmt_local_time
    calls astimezone, which rejects a zone NAME with a TypeError. Passing
    strings here threw on every stamp this module writes (the tick, the send,
    this) and the failure only showed on a live press, because nothing in the
    suite called it.
    """
    from datetime import timezone

    from config_loader import user_secondary_tz, user_tz
    from platform_compat import fmt_local_time

    return fmt_local_time(datetime.now(timezone.utc), user_tz(),
                          user_secondary_tz())


def _check_off(text: str) -> dict:
    """Close one item in the user's own notes, or say honestly that it did not.

    The page hands over the item's displayed text. That is not the key the
    marker uses, so `resolve_front_item` bridges it, and returns nothing rather
    than guessing: these are files the user edits by hand, and flipping the
    wrong line is worse than reporting a miss.
    """
    import completion_scan

    text = text.strip()
    if not text:
        return {"matched": False, "reason": "no item text"}
    # The rendered line carries its trailing metadata; the week file does not.
    subject = text.split(" \u00b7 ")[0].strip()
    candidate = completion_scan.resolve_front_item(subject)
    if candidate is None:
        return {"matched": False,
                "reason": "not found in your week file, tick it there"}
    result = completion_scan.mark_done([candidate])
    if not (result["marked"] or result["project_tasks"] or result["deals"]):
        return {"matched": False, "reason": "already closed"}
    return {"matched": True, "at": _now_pt_et(), "detail": result}


def _send_draft(key: str) -> dict:
    """Send one draft the briefing already wrote, by its provider id.

    The ledger holds the id, never the body, which is the point: the user may
    have edited the draft in Gmail or Outlook, and this sends what is actually
    there. Never retried, by anyone: a lost response may mean it went, and a
    duplicate is worse than a line telling the reader to check Sent.
    """
    import draft_email
    import send_email as sender

    if config_workbench_email_mode() != "send":
        return {"error": "This install is set to drafts only. "
                         "Change workbench.email_mode to send."}
    entry = (draft_email.load_ledger() or {}).get(key)
    if not entry:
        raise KeyError("no draft recorded for that item")
    account = sender.account_for_label(entry.get("label") or "")
    if not account:
        raise KeyError(f"no account labelled {entry.get('label')!r}")
    sender.send_draft(account, entry.get("id") or "")
    # Record it before returning. A reload that re-offers a send already made is
    # how a duplicate happens, and the ledger is the only thing that remembers.
    at = _now_pt_et()
    try:
        ledger = draft_email.load_ledger() or {}
        if key in ledger:
            ledger[key]["sent_at"] = at
            draft_email.save_ledger(ledger)
    except Exception:                                          # noqa: BLE001
        pass                                                   # the mail went; the note is best effort
    return {"ok": True, "at": at}


def _feedback(briefing: str, text: str) -> dict:
    """Mail the reader's note about this briefing to the reader.

    It goes to their own primary address, not to an author or a service: the
    plugin has no server, this runs on their machine, and a note about their
    own briefing is theirs. It lands in their inbox where they will see it
    next to everything else, which is the whole point of writing it here
    rather than in a file they would have to remember to open.

    This is a send, not a draft, and the drafts-first rule survives it: the
    rule exists so nothing reaches a counterparty without an explicit press
    per message. Here the recipient is the sender, there is no counterparty,
    and the press is explicit.
    """
    import send_email as sender

    text = (text or "").strip()
    if not text:
        return {"error": "nothing to send"}
    if len(text) > 20000:
        return {"error": "too long to send"}
    account = config_primary_account()
    if not account or not account.get("email"):
        return {"error": "no account is configured to send from"}

    title = workbench_data.BRIEFINGS.get(briefing, (briefing,))[0]
    at = _now_pt_et()
    body = (f"{text}\n\n"
            f"----\n"
            f"Sent from the {title} page, {at}.\n"
            f"Van Gogh {VERSION}.\n")
    try:
        sender.send_email(account, account["email"],
                          f"Van Gogh feedback: {title}", body)
    except Exception as exc:                                   # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "at": at, "to": account["email"]}


def _chat(briefing: str, text: str) -> dict:
    """One turn of the page's chat, on the user's own subscription.

    Read and draft only. The deny list is the guard, not the prompt: an
    instruction not to send is necessary and never sufficient.
    """
    import claude_cli

    text = text.strip()
    if not text:
        return {"reply": ""}
    if _wants_a_deck(text):
        return _stage_from_chat(text)

    title = workbench_data.BRIEFINGS.get(briefing, (briefing,))[0]

    # The briefing IS the context. run_claude passes `--tools ""`, so this call
    # is strictly non-agentic: it cannot open a file, search a mailbox or run
    # anything. The old prompt told it the vault and mail were "in reach",
    # which was false, and a model told it can search will narrate searching:
    # it emitted <function_calls> blocks as prose and then wrote "no richer
    # detail surfaced in the immediate vault or mail search" having searched
    # nothing. An answer that invents its own sources is worse than no answer,
    # so the page hands it the text and tells it that is all there is.
    info = workbench_data.briefing(briefing)
    source = ""
    if info.get("exists"):
        try:
            source = Path(info["path"]).read_text(encoding="utf-8")[:24000]
        except Exception:                                      # noqa: BLE001
            source = ""

    prompt = (
        f"You are answering one question for the person whose vault this is, "
        f"about today's {title} briefing or anything in their notes.\n\n"
        f"You can read this vault: Read, Grep and Glob, nothing else. You have "
        f"no shell, no network, no write access and no way to send anything, "
        f"and you cannot reach any file outside this directory. Look things up "
        f"when the question needs it. Never claim to have looked at something "
        f"you did not open.\n"
        f"Answer in at most four sentences, plain words, point first. Say what "
        f"you could not find rather than guessing. No em-dashes.\n\n"
        f"When you have used a file, name it on a final line exactly as:\n"
        f"SOURCES: <vault-relative path>, <another>\n"
        f"Write that line only for files you actually opened. If you answered "
        f"from the briefing below alone, write SOURCES: none.\n\n"
        f"--- TODAY'S BRIEFING ---\n{source}\n--- END ---\n\n"
        f"Question: {text}")

    # The vault is the working directory, which is what confines the file
    # tools: --restricted allows them only there, and no --add-dir widens it.
    try:
        root = vault()
    except Exception:                                          # noqa: BLE001
        root = None
    if root is None:
        return {"reply": "No vault is configured, so there is nothing to read."}

    proc = claude_cli.run_claude_readonly(prompt, root, timeout=CHAT_TIMEOUT_S)
    raw = _clean_reply(proc.stdout or "")
    if proc.returncode != 0 or not raw:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {"reply": tail[-1] if tail else "The assistant did not answer."}
    reply, cited = _split_sources(raw)
    return {"reply": reply, "source": cited or (title if source else "")}


_TOOL_BLOCK_RE = re.compile(
    r"<function_calls>.*?</function_calls>|<invoke\b.*?</invoke>|"
    r"<function_results>.*?</function_results>|<parameter\b.*?</parameter>",
    re.S | re.I)
_STRAY_TAG_RE = re.compile(
    r"^\s*</?(function_calls|invoke|parameter|function_results)\b[^>]*>\s*$",
    re.I | re.M)


def _clean_reply(raw: str) -> str:
    """Strip any tool-call markup the model narrated into its answer.

    The call is non-agentic, so these blocks are always hallucinated text, not
    a real invocation. They reached the page verbatim, which read as the
    assistant showing its work when it had done none. Prompting alone does not
    hold (the same class as the em-dash rule), so the strip is the guarantee
    and the prompt is the encouragement.
    """
    out = _TOOL_BLOCK_RE.sub("", raw or "")
    out = _STRAY_TAG_RE.sub("", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


CHAT_TIMEOUT_S = 240

_SOURCES_RE = re.compile(r"^\s*SOURCES:\s*(.*)$", re.I | re.M)


def _split_sources(raw: str) -> tuple:
    """Separate the answer from the files it says it opened.

    The model names them so the reader can check the answer against the notes
    it came from. "none" means it answered from the briefing alone, which is a
    real answer about this page, not a failure.
    """
    m = _SOURCES_RE.search(raw or "")
    if not m:
        return (raw or "").strip(), ""
    body = (raw[:m.start()] + raw[m.end():]).strip()
    cited = m.group(1).strip().rstrip(".")
    if cited.lower() in ("none", "n/a", "-", ""):
        cited = ""
    return body, cited


# A request for a file, as opposed to a question about one. Deliberately narrow:
# a false positive turns a question into a ticket, which is worse than missing a
# request the reader can simply rephrase.
_DECK_RE = re.compile(
    r"\b(make|build|create|draft|put together|write)\b[^.?!]{0,60}?"
    r"\b(deck|slides|presentation|pptx|powerpoint)\b", re.I)


def _wants_a_deck(text: str) -> bool:
    return bool(_DECK_RE.search(text or ""))


def _stage_from_chat(text: str) -> dict:
    """Turn a chat request for a deck into a staged plan awaiting a nod.

    Staging writes no file. It asks the model for a content plan, validates it,
    and parks it on a ticket in `staged`, which is the same gate the dashboard
    uses: only an explicit approval reaches a builder. The chat moves the
    button to where the reader asked, it does not remove it.
    """
    import deliverable

    ticket = store.create(text.strip()[:120] or "Deck from chat", "pptx",
                          "Asked for from the briefing page.")
    tid = ticket["id"]
    try:
        store.transition(tid, "staging", "planning from chat")
        plan = deliverable.stage_plan(ticket)
        ticket = store.transition(tid, "staged", "planned from chat", staged=plan)
    except Exception as exc:                                   # noqa: BLE001
        store.transition(tid, "failed", f"{type(exc).__name__}: {exc}")
        return {"reply": f"I could not plan that deck: {exc}"}

    # `heading` is the slide's own key; `title` belongs to the deck. Reading
    # the wrong one rendered every line as "Untitled" while the plan was fine.
    slides = plan.get("slides") or []
    lines = [f"{plan.get('title') or 'Deck'}, {len(slides)} slides, "
             f"nothing written yet:"]
    for i, sl in enumerate(slides, 1):
        lines.append(f"{i}. {sl.get('heading') or 'Untitled'}")
    lines.append("")
    lines.append("Press Build and I will write the file.")
    return {"reply": "\n".join(lines), "ticket": tid,
            "build": f"{len(slides)} slides"}


_MIME = {".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def _serve_delivery(self, tid: str) -> None:
    """Hand back the file one ticket delivered, by ticket id.

    The path comes from the ledger, never from the request: a route that took
    a path would serve any file on the machine to anything that could reach
    the port. It must also still be inside the deliverables directory, because
    a ledger written by some future code path is not a promise about where it
    pointed.
    """
    ticket = store.get(tid)
    if not ticket or ticket.get("state") not in ("delivered", "rated"):
        self._send(404, b"nothing delivered for that ticket",
                   "text/plain; charset=utf-8")
        return
    raw = ((ticket.get("delivery") or {}).get("path") or "").strip()
    if not raw:
        self._send(404, b"no file recorded", "text/plain; charset=utf-8")
        return
    import deliverable
    path = Path(raw).resolve()
    root = deliverable.deliverables_dir().resolve()
    if root not in path.parents or not path.is_file():
        self._send(404, b"that file is not in the deliverables folder",
                   "text/plain; charset=utf-8")
        return
    body = path.read_bytes()
    ctype = _MIME.get(path.suffix.lower(), "application/octet-stream")
    self.send_response(200)
    self.send_header("Content-Type", ctype)
    self.send_header("Content-Length", str(len(body)))
    self.send_header("Content-Disposition",
                     f'attachment; filename="{path.name}"')
    self.end_headers()
    self.wfile.write(body)


def _refresh_worker(job_id: str, briefing: str) -> None:
    """Re-render one briefing, then re-derive ticket proposals from it.

    Imported lazily so the module still loads (and the whole suite still runs)
    on a machine with no `claude` CLI present.
    """
    try:
        import digest_send
        with digest_send.digest_lock():
            digest_send.render_briefing(briefing)
        result = store.propose()
        _job_finish(job_id, "done",
                    f"Rewrote {briefing}. {result['added']} proposed, {result['pruned']} cleared.")
    except Exception as exc:                                   # noqa: BLE001
        _job_finish(job_id, "failed", f"{type(exc).__name__}: {exc}")


def _stage_worker(job_id: str, tid: str) -> None:
    """Plan one deliverable's content, then stop and wait for a human.

    Staging never builds a file. It writes a plan onto the ticket and moves it
    to `staged`, which is the state that asks for the nod. A plan the model
    cannot produce lands on `failed` carrying the reason, so the user sees what
    was wrong instead of an empty deck.
    """
    import deliverable
    try:
        ticket = store.get(tid)
        if ticket is None:
            raise KeyError(tid)
        # The vault's own record of this counterparty, so the plan argues from
        # what is known rather than from the ticket title alone. It fails open:
        # a pack that cannot be built costs the deck its evidence, never the
        # whole run. The reason is recorded rather than swallowed, because a
        # silently empty pack looks exactly like a working one.
        name = ticket.get("counterparty_name") or ""
        if name and not ticket.get("context_pack"):
            try:
                import context_pack
                pack = context_pack.pack_for(name, ticket.get("counterparty_email") or "")
                if not context_pack.is_empty(pack):
                    # Written through the store: mutating the dict `get`
                    # returned would leave the pack out of the ledger, so the
                    # next read (and the staged ticket the user sees) has none.
                    ticket = store.attach(tid, context_pack=pack)
            except Exception as exc:                           # noqa: BLE001
                _job_note(job_id, f"no vault record used: {type(exc).__name__}: {exc}")
        plan = deliverable.stage_plan(ticket, ratings=store.recent_ratings())
        store.transition(tid, "staged", "planned, waiting on you", staged=plan)
        _job_finish(job_id, "done",
                    f"Planned {len(plan['slides'])} slides. Waiting on you.")
    except Exception as exc:                                   # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
        try:
            store.transition(tid, "failed", detail)
        except Exception:                                      # noqa: BLE001
            pass
        _job_finish(job_id, "failed", detail)


def _build_worker(job_id: str, tid: str) -> None:
    """Build the approved deliverable, then verify the file it wrote.

    Verification is deliberately not the model's word for it: the file is
    reopened and its slide count checked against the plan. A builder that
    silently wrote nothing is the failure this catches.
    """
    import deliverable
    try:
        ticket = store.get(tid)
        if ticket is None:
            raise KeyError(tid)
        plan = ticket.get("staged")
        if not plan:
            raise deliverable.PlanInvalid("nothing was staged for this ticket")
        store.transition(tid, "running", "building")
        out = deliverable.output_path(ticket)
        deliverable.build(ticket.get("type"), plan, out)

        store.transition(tid, "verifying", "checking the file")
        detail = deliverable.verify(ticket.get("type"), plan, out)

        store.transition(tid, "delivered", detail,
                         delivery={"path": str(out), "at": _now_pt_et(),
                                   "detail": detail})
        _job_finish(job_id, "done", detail)
    except Exception as exc:                                   # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
        try:
            store.transition(tid, "failed", detail)
        except Exception:                                      # noqa: BLE001
            pass
        _job_finish(job_id, "failed", detail)


def jobs_list() -> list:
    with _jobs_lock:
        return sorted(_jobs.values(), key=lambda j: j["started_at"], reverse=True)[:25]


# ── Sender logos ─────────────────────────────────────────────────────────────

_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
_logo_misses: set = set()


def logo_bytes(domain: str) -> bytes | None:
    """A cached 64px favicon for one counterparty domain, or None.

    Fetching discloses the domain to Google's favicon service, which is why it
    sits behind `workbench.logo_fetch` and why a miss is remembered for the
    process lifetime instead of re-asked on every page paint. The domain is
    validated against a hostname pattern before it is ever interpolated into a
    URL or a filename.
    """
    domain = (domain or "").strip().lower()
    if not _DOMAIN_RE.match(domain) or len(domain) > 253:
        return None

    cache = user_state.state_dir() / "logos"
    path = cache / f"{domain}.png"
    try:
        return path.read_bytes()
    except OSError:
        pass
    if domain in _logo_misses or not workbench_logo_fetch():
        return None

    try:
        import requests
        response = requests.get(
            "https://www.google.com/s2/favicons",
            params={"domain": domain, "sz": "64"},
            timeout=5,
        )
        if response.status_code != 200 or not response.content:
            raise ValueError(f"status {response.status_code}")
        cache.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        return response.content
    except Exception:                                          # noqa: BLE001
        _logo_misses.add(domain)
        return None


# ── Request handling ─────────────────────────────────────────────────────────

# ── The live layer ────────────────────────────────────────────────────────────
#
# The published artifact and this page render from the same `render_page`. The
# difference is only what gets appended here: the controls, and a script that
# talks to this server and to nothing else. A page with no token has no
# controls, which is why the artifact cannot act even if someone shares it.

_LIVE_CSS = """
[data-live] li.task{position:relative}
[data-live] li.task input{cursor:pointer}
[data-live] li.task.pending label{opacity:.5}
/* The item is a two-column grid (tick, text). A mark appended to the <li>
   becomes a third grid item one character wide, which wrapped SENT 9:12 AM PT
   one word per line. Put it under the text, in the text's own column. */
[data-live] .act{grid-column:2;display:block;margin-top:4px}
[data-live] .act .state{white-space:nowrap}
.foldall{appearance:none;background:none;border:0;padding:0;cursor:pointer;
  font-family:var(--font-mono);font-size:11px;font-weight:700;
  letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.foldall:hover,.foldall:focus-visible{color:var(--text)}
.stamp{appearance:none;background:transparent;border:1.5px solid var(--amber);
  border-radius:2px;color:var(--amber);font-family:var(--font-mono);font-size:11px;
  font-weight:700;letter-spacing:.18em;text-transform:uppercase;padding:4px 11px;
  cursor:pointer;transition:background 220ms ease-out,color 220ms ease-out}
.stamp:hover,.stamp:focus-visible{background:var(--amber);color:var(--card)}
.stamp[disabled]{opacity:.55;cursor:default}
/* The written file links each draft to its Drafts folder, because a file is
   read anywhere and a link is all a file can offer. Here the same target is a
   button: this page is a control surface, and a bare URL in a list of things
   waiting on you reads as a footnote rather than as the thing to press.

   The button is ink by default. One class served both the travel card and the
   drafts card, so every draft link rendered amber and filled amber on hover,
   which is the send stamp's own treatment: five amber marks on one page, three
   of them meaning nothing. briefing_html.py says of the drafts card that it
   "carries no colour, because amber is reserved for the deadline that cannot
   move", and the shipped page contradicted its own renderer. Amber is opted
   into by `.open.hard`, on the travel line only. */
.open{appearance:none;background:transparent;border:1.5px solid var(--rule);
  border-radius:2px;color:var(--text);font-family:var(--font-mono);font-size:11px;
  font-weight:700;letter-spacing:.18em;text-transform:uppercase;padding:3px 10px;
  cursor:pointer;text-decoration:none;display:inline-block;margin-left:8px;
  transition:background 220ms ease-out,color 220ms ease-out,
    border-color 220ms ease-out}
.open:hover,.open:focus-visible{background:var(--text);color:var(--card);
  border-color:var(--text)}
/* A flight leaves whether or not the briefing was read carefully. */
.open.hard{border-color:var(--amber);color:var(--amber)}
.open.hard:hover,.open.hard:focus-visible{background:var(--amber);
  color:var(--card);border-color:var(--amber)}
.state{font-family:var(--font-mono);font-size:12px;font-weight:700;
  letter-spacing:.06em;text-transform:uppercase}
.state.sent{color:var(--green)}
.state.failed{color:var(--red)}
.state.working{color:var(--cyan)}
.rerun{flex-basis:100%;display:flex;align-items:center;gap:10px;
  font-family:var(--font-mono);font-size:11px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);border-top:1px dotted var(--rule);
  margin-top:8px;padding-top:8px}
.rerun b{color:var(--text);font-weight:700}
.rr-btn{appearance:none;background:transparent;border:1px solid var(--rule);
  border-radius:2px;font:inherit;color:var(--text);cursor:pointer;padding:3px 9px;
  letter-spacing:.1em;margin-left:auto;font-weight:700}
.rr-btn:hover,.rr-btn:focus-visible{background:var(--text);color:var(--card);
  border-color:var(--text)}
.rr-btn[disabled]{opacity:.55;cursor:default}
/* The chat floats: it is the one thing on the page that is not part of the
   briefing, so it does not take a place in the reading order. Collapsed it is
   a tab in the corner; opened it is a panel over the page, and the page keeps
   its scroll position underneath. */
.fb{margin:18px 0 0;padding:0 14px}
.fb-line{display:flex;width:100%;align-items:baseline;gap:10px;appearance:none;
  background:none;border:0;border-top:1px solid var(--rule-soft);padding:12px 0 0;
  cursor:pointer;text-align:left;font-family:var(--font-mono);font-size:11px;
  letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.fb-line:hover,.fb-line:focus-visible{color:var(--text)}
.fb-label{flex:1}
.fb-hint{font-weight:700}
.fb textarea{width:100%;box-sizing:border-box;background:var(--wash);
  border:1px solid var(--rule);border-radius:2px;color:var(--text);
  font:inherit;font-size:14px;line-height:1.5;padding:10px 12px;
  min-height:96px;resize:vertical;margin:10px 0 0}
.fb textarea:focus{outline:2px solid var(--cyan);outline-offset:2px}
.fb-foot{display:flex;align-items:baseline;gap:12px;margin:8px 0 0}
.fb-note{flex:1;font-family:var(--font-mono);font-size:10.5px;line-height:1.5;
  letter-spacing:.04em;color:var(--muted);margin:0}
.fb-send{appearance:none;background:transparent;border:1px solid var(--rule);
  border-radius:2px;font:inherit;font-family:var(--font-mono);font-size:11px;
  font-weight:700;letter-spacing:.14em;text-transform:uppercase;
  color:var(--text);cursor:pointer;padding:5px 12px}
.fb-send:hover,.fb-send:focus-visible{background:var(--text);color:var(--card)}
.fb-send[disabled]{opacity:.55;cursor:default}
/* box-sizing is load-bearing here, not housekeeping: the width is capped
   against the viewport, but a 2px border and 14px of padding on each side sat
   outside that cap, so at 390px the panel rendered 422px wide starting at
   x=-22 and clipped its own first word off the left edge of every page. */
.chat{position:fixed;right:20px;bottom:20px;z-index:40;width:min(420px,calc(100vw - 40px));
  box-sizing:border-box;
  background:var(--card);border:2px solid var(--text);border-radius:2px;
  padding:0 14px 12px;
  max-height:min(70vh,560px);overflow-y:auto;overscroll-behavior:contain}
.chat.shut{padding-bottom:0}
.chat-line{display:flex;width:100%;align-items:baseline;gap:10px;appearance:none;
  background:none;border:0;padding:11px 0;cursor:pointer;text-align:left;
  font-family:var(--font-mono);font-size:11px;font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted)}
.chat-line:hover,.chat-line:focus-visible{color:var(--text)}
.chat-label{flex:1}
.chat-hint{color:var(--muted)}
.turn{display:grid;grid-template-columns:80px minmax(0,1fr);column-gap:12px;
  padding:7px 0;border-bottom:1px solid var(--rule-soft)}
.turn .who{font-family:var(--font-mono);font-size:11px;font-weight:700;
  letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.turn.vg .who{color:var(--cyan)}
.turn p{margin:0;font-size:14px;line-height:1.45;white-space:pre-wrap}
.chat-entry{display:grid;grid-template-columns:80px minmax(0,1fr);column-gap:12px;
  padding:8px 0;align-items:baseline;border-bottom:1px solid var(--rule)}
.chat-entry label{font-family:var(--font-mono);font-size:11px;font-weight:700;
  letter-spacing:.08em;text-transform:uppercase;color:var(--muted);text-align:right}
.chat-entry input{appearance:none;background:transparent;border:0;font:inherit;
  color:var(--text);width:100%;padding:0}
.chat-entry input:focus{outline:none;
  box-shadow:inset 0 -2px 0 0 var(--cyan)}
/* .turn is a two-column grid (who, text). Appended bare, the source line
   became a third grid item in the 80px label column and wrapped four times. */
.turn .dl{color:var(--cyan);text-underline-offset:3px}
.turn .src{grid-column:2;margin:5px 0 0;font-family:var(--font-mono);
  font-size:10.5px;letter-spacing:.04em;color:var(--muted)}
.chat-foot{font-family:var(--font-mono);font-size:10.5px;line-height:1.5;
  letter-spacing:.04em;color:var(--muted);margin:8px 0 0}
:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}
[data-live]{padding-bottom:64px}
@media (max-width:560px){
  .chat{right:10px;bottom:10px;width:calc(100vw - 20px);max-height:64vh}
  .turn,.chat-entry{grid-template-columns:minmax(0,1fr)}
  .chat-entry label{text-align:left}
  .rr-btn{margin-left:0}
}
"""

# Vanilla, no build step, no framework. Everything it does is one POST and one
# rewrite of the row it happened in. There is no toast and no modal anywhere:
# a result the user has to dismiss is a result they can miss.
_LIVE_JS = r"""
<script>
(function(){
  var root = document.querySelector('[data-live]');
  if (!root) return;
  var token = root.dataset.token, briefing = root.dataset.briefing;

  function post(path, body){
    return fetch(path, {
      method: 'POST',
      headers: {'Content-Type':'application/json','X-Workbench-Token':token},
      body: JSON.stringify(body || {})
    }).then(function(r){ return r.json().then(function(j){
      if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
      return j;
    }); });
  }
  function say(el, cls, text){
    el.className = 'state ' + cls;
    el.textContent = text;
  }


  // One row, one status line. The tick used to append its span to the <li>
  // while the send replaced the stamp inside .act, so ticking and then
  // sending the same item left two spans contradicting each other ("OAuth
  // required" beside "not found in your week file"). On a page whose whole
  // authority is saying exactly what happened, a row saying two incompatible
  // things is the worst available failure.
  function rowState(row){
    var found = row.querySelector('.state');
    if (found) return found;
    var s = document.createElement('span');
    s.className = 'state';
    (row.querySelector('.act') || row).appendChild(s);
    return s;
  }

  // The focus card opens folded on a phone (see the max-width:560px block).
  // Pressing anywhere on it opens it, because the whole card is the target and
  // a 10px "More" would not be one.
  var fc = document.querySelector('.card.focus');
  if (fc) fc.addEventListener('click', function(){
    if (window.matchMedia('(max-width:560px)').matches) fc.classList.toggle('open');
  });

  // A tick closes the item in the vault. The box goes back if the write did
  // not land: a checkbox that stays ticked over a failed write is a lie.
  root.addEventListener('change', function(e){
    var box = e.target;
    if (!box.matches('li.task input[type=checkbox]')) return;
    var li = box.closest('li.task');
    // The item text moved from a <span> to the checkbox's <label>. This
    // reads it either way: the payload is the text, and an empty one
    // would silently ask the server to match nothing.
    var body = li.querySelector('label') || li.querySelector('span');
    var text = (body || {}).textContent || '';
    if (!box.checked) { box.checked = true; return; }   // unticking is not a write
    box.disabled = true; li.classList.add('pending');
    var mark = rowState(li);
    say(mark, 'working', 'Closing');
    post('/api/check', {briefing: briefing, text: text.trim()}).then(function(j){
      li.classList.remove('pending');
      if (j.matched) {
        li.classList.add('done');
        say(mark, 'sent', j.at);
      } else {
        box.checked = false; box.disabled = false;
        say(mark, 'failed', j.reason || 'not found in your week file');
      }
    }).catch(function(err){
      li.classList.remove('pending');
      box.checked = false; box.disabled = false;
      say(mark, 'failed', String(err.message || err));
    });
  });

  // One press, one message. The row rewrites itself to what happened.
  root.addEventListener('click', function(e){
    var btn = e.target.closest('.stamp');
    if (!btn) return;
    var wrap = btn.parentNode;
    btn.disabled = true;
    btn.remove();
    var mark = rowState(btn.closest('li.task') || wrap);
    say(mark, 'working', 'Sending');
    post('/api/send', {key: btn.dataset.key}).then(function(j){
      say(mark, 'sent', 'Sent ' + j.at);
    }).catch(function(err){
      say(mark, 'failed', String(err.message || err).replace(/\.\s*$/, '') +
        '. The draft is still in Drafts.');
    });
  });

  // Open all / close all. The label states what the press will do, and flips
  // after, so it is never a checkbox pretending to be a verb.
  root.addEventListener('click', function(e){
    var btn = e.target.closest('.foldall');
    if (!btn) return;
    var card = btn.closest('.card');
    var open = btn.dataset.all === 'open';
    card.querySelectorAll('details').forEach(function(d){ d.open = open; });
    btn.dataset.all = open ? 'close' : 'open';
    btn.textContent = open ? 'Close all' : 'Open all';
  });

  // A fold opened or closed by hand can leave the control lying about what it
  // would do. Re-derive the label from what is actually open.
  root.addEventListener('toggle', function(e){
    if (!e.target.matches('details')) return;
    var card = e.target.closest('.card');
    var btn = card && card.querySelector('.foldall');
    if (!btn) return;
    var all = card.querySelectorAll('details');
    var shut = card.querySelectorAll('details:not([open])').length;
    btn.dataset.all = shut ? 'open' : 'close';
    btn.textContent = shut ? 'Open all' : 'Close all';
  }, true);

  // Rewrite now. The header row carries the job, nothing pops up.
  var rr = document.querySelector('.rr-btn');
  if (rr) rr.addEventListener('click', function(){
    var line = rr.parentNode, label = line.querySelector('b');
    rr.disabled = true; label.textContent = 'Rewriting';
    post('/api/refresh', {briefing: briefing}).then(function(){
      label.textContent = 'Rewriting, this page reloads when it lands';
      var poll = setInterval(function(){
        fetch('/api/jobs', {headers:{'X-Workbench-Token':token}})
          .then(function(r){ return r.json(); })
          .then(function(j){
            var job = (j.jobs || [])[0];
            if (!job || job.state === 'running') return;
            clearInterval(poll);
            if (job.state === 'done') location.reload();
            else { label.textContent = 'Rewrite failed: ' + (job.detail || ''); rr.disabled = false; }
          });
      }, 2000);
    }).catch(function(err){
      label.textContent = 'Rewrite failed: ' + (err.message || err);
      rr.disabled = false;
    });
  });

  // Feedback. Opens from one ruled line at the foot, mails the note to the
  // reader's own address, and reports the result in the line it happened in.
  var fb = document.querySelector('.fb');
  if (fb) fb.addEventListener('click', function(e){
    if (e.target.closest('.fb-line')) {
      if (fb.querySelector('textarea')) return;          // already open
      fb.innerHTML =
        '<div class="fb-line" style="cursor:default"><span class="fb-label">' +
        'Something about this page wrong, missing, or worth changing' +
        '</span></div>' +
        '<textarea placeholder="What would you change?"></textarea>' +
        '<div class="fb-foot"><p class="fb-note"></p>' +
        '<button class="fb-send" type="button">Send to me</button></div>';
      var note = fb.querySelector('.fb-note');
      note.textContent = 'Goes to your own inbox. Nothing leaves this machine '
        + 'except the mail you just wrote.';
      fb.querySelector('textarea').focus();
      return;
    }
    var send = e.target.closest('.fb-send');
    if (!send) return;
    var box = fb.querySelector('textarea'), note = fb.querySelector('.fb-note');
    var text = (box.value || '').trim();
    if (!text) { box.focus(); return; }
    send.disabled = true; box.disabled = true;
    note.textContent = 'Sending';
    post('/api/feedback', {briefing: briefing, text: text}).then(function(j){
      if (j.error) {
        note.textContent = j.error + '. Your note is still in the box.';
        send.disabled = false; box.disabled = false;
        return;
      }
      // The note is gone from the page because it is in the inbox now. Say
      // where it went: "sent" with no destination is not a receipt.
      fb.innerHTML = '<div class="fb-line" style="cursor:default">' +
        '<span class="fb-label">Sent to ' + j.to + ', ' + j.at +
        '</span></div>';
    }).catch(function(err){
      note.textContent = String(err.message || err) +
        '. Your note is still in the box.';
      send.disabled = false; box.disabled = false;
    });
  });

  // The chat. Opens from one ruled line, stays out of the way until asked.
  var _CHAT_TAB =
    '<button class="chat-line" type="button">' +
    '<span class="chat-label">Ask about anything on this page</span>' +
    '<span class="chat-hint">Ask</span></button>';
  var chat = document.querySelector('.chat');
  if (chat) chat.addEventListener('click', function(e){
    var closer = e.target.closest('.chat-shut');
    if (closer) {                       // opened panel back down to a tab
      chat.className = 'chat shut';
      chat.innerHTML = _CHAT_TAB;
      return;
    }
    if (!e.target.closest('.chat-line')) return;
    if (!chat.classList.contains('shut')) return;   // already open
    chat.className = 'chat';
    chat.innerHTML =
      '<button class="chat-line chat-shut" type="button"><span class="chat-label">' +
      'Ask about anything on this page</span><span class="chat-hint">Close</span></button>' +
      '<div class="turns"></div>' +
      '<div class="chat-entry"><label for="ask">You</label>' +
      '<input id="ask" autocomplete="off" placeholder="Type to ask"></div>' +
      '<p class="chat-foot">Runs on your Claude subscription, on this machine. ' +
      'It reads your vault to answer. It cannot change a note, run anything, ' +
      'or send.</p>';
    var input = chat.querySelector('input'), turns = chat.querySelector('.turns');
    input.focus();
    input.addEventListener('keydown', function(ev){
      if (ev.key !== 'Enter' || !input.value.trim()) return;
      var q = input.value.trim();
      input.value = ''; input.disabled = true;
      turns.insertAdjacentHTML('beforeend',
        '<div class="turn you"><span class="who">You</span><p></p></div>' +
        '<div class="turn vg"><span class="who">Van Gogh</span><p class="state working">Thinking</p></div>');
      turns.querySelectorAll('.turn.you p')[turns.querySelectorAll('.turn.you p').length-1].textContent = q;
      var out = turns.querySelectorAll('.turn.vg p');
      out = out[out.length - 1];
      post('/api/chat', {briefing: briefing, text: q}).then(function(j){
        out.className = ''; out.textContent = j.reply;
        // Say what the answer came from, under the answer. The model has no
        // tools, so the briefing is the only source there is, and an answer
        // that does not name its source invites the reader to assume a
        // search happened.
        if (j.source) {
          var src = document.createElement('p');
          src.className = 'src';
          src.textContent = 'Source: ' + j.source;
          out.parentNode.appendChild(src);
        }
        // A staged plan is not a file. The nod is what starts the build, so
        // the button lives here rather than the plan building itself.
        if (j.ticket && j.build) {
          var bar = document.createElement('p');
          bar.className = 'src';
          var go = document.createElement('button');
          go.className = 'stamp'; go.type = 'button';
          go.textContent = 'Build';
          go.addEventListener('click', function(){
            go.disabled = true;
            var note = document.createElement('span');
            note.className = 'state working'; note.textContent = 'Building';
            bar.appendChild(note);
            // Poll the job, not the ticket: there is no GET for one ticket,
            // and the approve call hands back the job id that builds it.
            post('/api/tickets/' + j.ticket + '/approve', {}).then(function(a){
              var jid = a.job_id;
              var poll = setInterval(function(){
                fetch('/api/jobs', {headers:{'X-Workbench-Token':token}})
                  .then(function(r){ return r.json(); })
                  .then(function(d){
                    var job = (d.jobs || []).filter(function(x){
                      return x.id === jid; })[0];
                    if (!job || job.state === 'running') return;
                    clearInterval(poll);
                    if (job.state === 'done') {
                      // The instruction goes when the file exists: "press
                      // Build" above a download link is a stale sentence.
                      out.textContent = out.textContent.replace(
                        /\n*Press Build and I will write the file\.?/, '');
                      bar.innerHTML = '';
                      var a2 = document.createElement('a');
                      a2.href = '/download/' + j.ticket + '?t=' + token;
                      a2.textContent = 'Download the deck';
                      a2.className = 'dl';
                      bar.appendChild(a2);
                    } else {
                      note.className = 'state failed';
                      note.textContent = 'Build failed: ' + (job.detail || '');
                    }
                  });
              }, 2500);
            }).catch(function(err){
              note.className = 'state failed';
              note.textContent = String(err.message || err);
              go.disabled = false;
            });
          });
          bar.appendChild(go);
          out.parentNode.appendChild(bar);
        }
        input.disabled = false; input.focus();
      }).catch(function(err){
        out.className = 'state failed';
        out.textContent = String(err.message || err);
        input.disabled = false; input.focus();
      });
    });
  });
})();
</script>
"""


_FEEDBACK = (
    '<section class="fb"><button class="fb-line" type="button">'
    '<span class="fb-label">Something about this page wrong, missing, '
    'or worth changing</span>'
    '<span class="fb-hint">Tell Van Gogh</span></button></section>')


_CHAT_CLOSED = (
    '<section class="chat shut"><button class="chat-line" type="button">'
    '<span class="chat-label">Ask about anything on this page</span>'
    '<span class="chat-hint">Ask</span></button></section>')


_FOLD_ALL = ('<button class="foldall" type="button" data-all="open">'
             'Open all</button>')


def _fold_control(html: str) -> str:
    """Put an open/close control on the bar of each card that has folds.

    Only where there is something to open: a card with no <details> gets no
    control, because a button that governs nothing is a button that teaches
    the reader to distrust the others.
    """
    out = []
    for chunk in re.split(r'(?=<section class="card)', html):
        if "<details" in chunk and '<div class="cbar">' in chunk:
            chunk = chunk.replace("</h2></div>", "</h2>" + _FOLD_ALL + "</div>", 1)
        out.append(chunk)
    return "".join(out)


def _rerun_line(info: dict) -> str:
    """When the briefing was last written, and the control that rewrites it."""
    when = (info.get("generated_at") or "").replace("T", " ")[:16] or "not yet"
    stale = " This page is older than the newest briefing." if info.get("stale") else ""
    return (f'<div class="rerun"><b>Rewritten</b><span>{escape(when)}'
            f'{escape(stale)}</span>'
            f'<button class="rr-btn" type="button">Rewrite now</button></div>')


# The two cards whose links are controls: travel (a trip with nothing booked)
# and drafts (a reply written and waiting). Both, not the first of the two:
# they appear on the same page, and searching once left whichever came second
# with plain links while the tests only ever planted one card at a time.
_CONTROL_CARD_RE = re.compile(
    r'<section class="card (?:drafts|travel)">.*?</section>', re.S)
_A_RE = re.compile(r'<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)


def _draft_buttons(html: str) -> str:
    """Inside the amber card only, render each link as a button.

    Scoped to that one card on purpose. The same markdown is read in three
    places (this page, the vault file, the digest email) and only this one is
    a control surface, so the link stays a link everywhere else. A page-wide
    rewrite would also catch links inside a draft's own quoted text, which are
    the counterparty's, not a control.

    The button is an anchor, not a <button>: it opens the reply in the
    provider's own Drafts folder, which is a navigation, and an anchor is what
    a browser already knows how to open in a new tab.
    """
    def one_line(li):
        """Move this line's OWN link to its end, and leave the rest alone.

        Per line, not per card. Collecting every link in the card and then
        walking the lines in parallel pairs them by position, so a card whose
        first line has no link (a departure notice, which carries a time and
        nothing to click) handed that line the SECOND line's booking link and
        left the line the link belonged to with a label and nothing behind it.
        Both lines were wrong and neither looked it.
        """
        body = li.group(1)
        found = []

        def strip(a):
            found.append(a.group(1))
            return a.group(2)

        body = _A_RE.sub(strip, body)
        if not found:
            return li.group(0)
        url = found[0]
        # The label names the destination: one opens a reply already written,
        # the other opens a flight search for a trip with nothing booked.
        # Only the flight takes amber: a reply can sit another day without
        # anything being lost, and a departure cannot.
        flights = "/travel/flights" in url
        label = "Find flights" if flights else "Open draft"
        cls = "open hard" if flights else "open"
        return (f'{body}<a class="{cls}" href="{url}" target="_blank" '
                f'rel="noopener noreferrer">{label}</a></li>')

    def one_card(card):
        return re.sub(r"(<li>(?:(?!</li>).)*?)</li>", one_line,
                      card.group(0), flags=re.S)

    return _CONTROL_CARD_RE.sub(one_card, html)


# A row the reader has already closed. The class is written `task done`, so
# this reads the whole attribute rather than a prefix.
_DONE_LI_RE = re.compile(r"<li class=\"[^\"]*\bdone\b")


def _stamps(html: str, briefing: str) -> str:
    """Put a stamp beside every draft this briefing wrote and has not sent.

    Keyed by the draft ledger, so a page only ever offers to send something the
    ledger can actually name. A draft the ledger does not know about gets no
    control at all, rather than a control that would fail on press.
    """
    import draft_email

    ledger = draft_email.load_ledger() or {}
    if not ledger:
        return html
    for key, entry in ledger.items():
        sent_at = entry.get("sent_at")
        if sent_at:
            mark = (f'<span class="act"><span class="state sent">'
                    f'Sent {escape(str(sent_at))}</span></span>')
        else:
            mark = (f'<span class="act"><button class="stamp" type="button" '
                    f'data-key="{escape(key)}">Send</button></span>')
        # Only the unsent case is a live control. The sent case is a statement.
        is_button = not sent_at
        needle = escape(str(entry.get("subject") or "")).strip()
        if not needle:
            continue
        # Land it at the END of the item's own <li>, never mid-sentence: the
        # subject appears inside a line that continues with the counterparty,
        # the date and the tag, and splicing at the match broke the line in two.
        #
        # The class match is a prefix, not the whole attribute: a closed item is
        # `class="task done"`, and matching `task"` exactly failed on it. That
        # miss then fell through to a bare str.replace with no <li> awareness,
        # which spliced a live SEND onto a row that was already ticked and
        # already said the reply had gone, inside a label the `done` rule
        # strikes through. The one irreversible control in the product was
        # offered on work that had shipped, so the fallback is gone: a subject
        # this function cannot place in an open row gets no control at all.
        pattern = re.compile(r"(<li class=\"task[^\"]*\"[^>]*>(?:(?!</li>).)*?"
                             + re.escape(needle) + r"(?:(?!</li>).)*?)</li>", re.S)

        def place(m, mark=mark, is_button=is_button):
            row = m.group(1)
            # A row that already carries an outcome does not get a second one.
            if 'class="state' in row:
                return m.group(0)
            # A closed row still takes the SENT marker: that is a statement
            # about what happened, and the row it happened in is where the
            # system writes it. What a closed row must never take is the live
            # button, the one irreversible control in the product. Offering it
            # on work the reader has already closed invites a duplicate send of
            # a settled position, in a line that simultaneously says the reply
            # has gone.
            if is_button and _DONE_LI_RE.match(row):
                return m.group(0)
            return row + mark + "</li>"

        html = pattern.sub(place, html, count=1)
    return html


class Handler(BaseHTTPRequestHandler):
    server_version = "VanGoghWorkbench"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):        # quieter than the default access log
        if "--verbose" in sys.argv:
            sys.stderr.write(f"{fmt % args}\n")

    # -- plumbing --

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status: int = 200) -> None:
        self._send(status, json.dumps(payload, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return {}
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip().lower()
        return host in LOOPBACK_HOSTS or host == ""

    def _authed(self, query: dict) -> bool:
        supplied = self.headers.get("X-Workbench-Token") or (query.get("t") or [""])[0]
        return hmac.compare_digest(supplied, _TOKEN)

    # -- routing --

    def do_GET(self):                                          # noqa: N802
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)

        if not self._host_ok():
            self._send(403, b"bad host", "text/plain; charset=utf-8")
            return

        if path == "/":
            try:
                html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            except OSError:
                self._send(500, b"index.html missing", "text/plain; charset=utf-8")
                return
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return

        if path.startswith("/static/"):
            name = path[len("/static/"):]
            ctype = STATIC_FILES.get(name)
            if not ctype:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            try:
                data = (STATIC_DIR / name).read_bytes()
            except OSError:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            self._send(200, data, ctype)
            return

        if path.startswith("/download/"):
            # Token-gated like every other route that reveals user data. The
            # first version of this checked nothing, so anything that could
            # reach the port could pull a finished deck.
            if not self._authed(query):
                self._send(401, b"unauthorized", "text/plain; charset=utf-8")
                return
            _serve_delivery(self, path[len("/download/"):])
            return

        if path.startswith("/briefing/"):
            if not self._authed(query):
                self._send(401, b"unauthorized", "text/plain; charset=utf-8")
                return
            try:
                self._serve_briefing(path[len("/briefing/"):], query)
            except Exception as exc:                           # noqa: BLE001
                self._send(500, f"{type(exc).__name__}: {exc}".encode("utf-8"),
                           "text/plain; charset=utf-8")
            return

        if not path.startswith("/api/"):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        if not self._authed(query):
            self._json({"error": "unauthorized"}, 401)
            return

        try:
            self._route_get(path, query)
        except Exception as exc:                               # noqa: BLE001
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _serve_briefing(self, name: str, query: dict) -> None:
        """The local briefing page: the same markdown, with its controls live.

        The published artifact renders from the same function without a token,
        so it has no controls at all. Here the page carries `data-live` and the
        per-boot token, and every control talks to this server and nothing else.
        """
        import briefing_html

        if name not in workbench_data.BRIEFINGS:
            self._send(404, b"no such briefing", "text/plain; charset=utf-8")
            return
        info = workbench_data.briefing(name)
        if not info.get("exists"):
            body = (f"<title>{escape(info['title'])}</title>"
                    f"<style>{briefing_html._TOKENS}{briefing_html._PAGE_CSS}</style>"
                    f'<div class="wrap"><header class="ph"><h1>{escape(info["title"])}</h1>'
                    "</header><section class=\"card\"><p>Nothing was written here yet. "
                    "Run the briefing, or refresh it from the Workbench.</p></section></div>")
            self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")
            return

        md = Path(info["path"]).read_text(encoding="utf-8")
        html = briefing_html.render_page(md, {
            "title": info["title"],
            "briefing_date": (info.get("generated_at") or "")[:10],
            "briefing": name,
        })
        # render_page emits a fragment because the artifact host wraps it in its
        # own skeleton. Here nothing does, so the served document declares its
        # own language rather than leaving a screen reader to guess.
        html = ('<!doctype html><html lang="en"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,'
                'initial-scale=1">' + html + "</html>")
        html = html.replace("</style>", _LIVE_CSS + "</style>", 1)
        html = html.replace(
            '<div class="wrap">',
            f'<div class="wrap" data-live="1" data-briefing="{escape(name)}" '
            f'data-token="{escape(_TOKEN)}">', 1)
        html = html.replace("</header>", _rerun_line(info) + "</header>", 1)
        html = _stamps(html, name)
        html = _draft_buttons(html)
        html = _fold_control(html)
        # The wrap's own closing tag is the LAST one, not the first: replacing
        # the first put the chat inside whichever card closed earliest.
        tail = html.rfind("</div>")
        # The chat floats, so the foot of the page is free: the feedback line
        # sits there, last, where a reader lands when they have finished
        # reading and know what they wanted the page to do differently.
        html = html[:tail] + _FEEDBACK + _CHAT_CLOSED + html[tail:]
        html += _LIVE_JS
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _route_get(self, path: str, query: dict) -> None:
        if path == "/api/meta":
            oauth = is_tier1()
            self._json({
                "version": VERSION,
                "oauth": oauth,
                "first_name": user_first_name() if oauth else "",
                "accounts": account_labels(),
                "van_gogh_root": str(van_gogh_root()),
                "briefings": [{"name": n, "title": t} for n, (t, *_r) in workbench_data.BRIEFINGS.items()],
                "email_mode": workbench_email_mode(),
                "logo_fetch": workbench_logo_fetch(),
                "jobs_enabled": True,
                "staging_enabled": True,
            })
        elif path == "/api/briefings":
            self._json({"briefings": workbench_data.all_briefings()})
        elif path == "/api/tickets":
            self._json({"tickets": store.ordered(), "counts": store.counts()})
        elif path == "/api/jobs":
            self._json({"jobs": jobs_list()})
        elif path == "/api/crm":
            self._json({"groups": workbench_data.crm_groups()})
        elif path == "/api/graph":
            rebuild = (query.get("rebuild") or ["0"])[0] == "1"
            self._json(brain_graph.cached(rebuild=rebuild))
        elif path == "/api/graph/node":
            node_id = (query.get("id") or [""])[0]
            self._json(brain_graph.neighbors(node_id))
        elif path == "/api/graph/search":
            self._json({"hits": brain_graph.search((query.get("q") or [""])[0])})
        elif path == "/api/logo":
            data = logo_bytes((query.get("domain") or [""])[0])
            if data is None:
                self._send(404, b"no logo", "text/plain; charset=utf-8")
            else:
                self._send(200, data, "image/png")
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):                                         # noqa: N802
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)

        if not self._host_ok():
            self._send(403, b"bad host", "text/plain; charset=utf-8")
            return
        if not path.startswith("/api/"):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        if not self._authed(query):
            self._json({"error": "unauthorized"}, 401)
            return

        try:
            self._route_post(path, self._body())
        except store.IllegalTransition as exc:
            self._json({"error": str(exc)}, 409)
        except KeyError as exc:
            self._json({"error": f"no such ticket: {exc}"}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:                               # noqa: BLE001
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _route_post(self, path: str, body: dict) -> None:
        if path == "/api/shutdown":
            self._json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        if path == "/api/refresh":
            briefing = body.get("briefing") or "morning-coffee"
            if briefing not in workbench_data.BRIEFINGS:
                raise ValueError(f"unknown briefing: {briefing}")
            if not is_tier1():
                self._json({"error": "OAuth required. Run /van-gogh:install-van-gogh."}, 412)
                return
            job_id = _job_new("refresh", workbench_data.BRIEFINGS[briefing][0])
            threading.Thread(target=_refresh_worker, args=(job_id, briefing), daemon=True).start()
            self._json({"job_id": job_id})
            return

        if path == "/api/check":
            self._json(_check_off(body.get("text") or ""))
            return

        if path == "/api/send":
            if not is_tier1():
                self._json({"error": "OAuth required. Run /van-gogh:install-van-gogh."}, 412)
                return
            self._json(_send_draft(body.get("key") or ""))
            return

        if path == "/api/feedback":
            self._json(_feedback(body.get("briefing") or "",
                                 body.get("text") or ""))
            return

        if path == "/api/chat":
            self._json(_chat(body.get("briefing") or "", body.get("text") or ""))
            return

        if path == "/api/propose":
            self._json(store.propose())
            return

        if path == "/api/tickets":
            self._json(store.create(body.get("title") or "",
                                    body.get("type") or "email",
                                    body.get("dod") or ""))
            return

        parts = path.strip("/").split("/")          # api tickets <id> <action>
        if len(parts) == 4 and parts[1] == "tickets":
            self._ticket_action(parts[2], parts[3], body)
            return

        self._json({"error": "not found"}, 404)

    def _ticket_action(self, tid: str, action: str, body: dict) -> None:
        if action == "comment":
            self._json(store.comment(tid, body.get("text") or ""))
        elif action == "dod":
            self._json(store.edit_dod(tid, body.get("dod") or ""))
        elif action == "dismiss":
            self._json(store.transition(tid, "dismissed", "dismissed by hand"))
        elif action == "restore":
            self._json(store.transition(tid, "proposed", "restored by hand"))
        elif action == "rate":
            self._json(store.rate(tid, body.get("stars") or 0, body.get("note") or ""))
        elif action == "pin":
            position = body.get("position")
            self._json({"pins": store.pin(tid, None if position is None else int(position))})
        elif action == "stage":
            # Email tickets are drafted by a skill in a chat session, not here:
            # only the file types have a builder behind them.
            ticket = store.get(tid)
            if ticket.get("type") == "email":
                self._json({"error": "an email reply is drafted from the briefing, "
                                     "not staged here"}, 400)
                return
            store.transition(tid, "staging", "planning")
            job_id = _job_new("stage", ticket.get("title") or tid)
            threading.Thread(target=_stage_worker, args=(job_id, tid), daemon=True).start()
            self._json({"job_id": job_id})
        elif action == "approve":
            ticket = store.transition(tid, "approved", "approved by hand")
            # The nod is what starts the build. Nothing before this writes a file.
            if ticket.get("type") != "email" and ticket.get("staged"):
                job_id = _job_new("build", ticket.get("title") or tid)
                threading.Thread(target=_build_worker, args=(job_id, tid), daemon=True).start()
                self._json({"job_id": job_id, "ticket": ticket})
                return
            self._json(ticket)
        else:
            self._json({"error": f"unknown action: {action}"}, 404)


# ── Entry point ──────────────────────────────────────────────────────────────

def build_server(port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main(argv: list | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(description="Serve the Van Gogh Workbench on localhost.")
    parser.add_argument("--port", type=int, default=8765,
                        help="Port to bind. 0 picks a free one. Default 8765.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser.")
    parser.add_argument("--verbose", action="store_true", help="Log every request.")
    args = parser.parse_args(argv)

    global _TOKEN
    _TOKEN = secrets.token_urlsafe(32)

    try:
        server = build_server(args.port)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": f"Could not bind port {args.port}: {exc}"}))
        return 1

    port = server.server_address[1]
    url = launch_url(port, _TOKEN)
    write_session(port, _TOKEN)

    # Seed the ledger from whatever the last briefing left behind, so the
    # Tickets section has something in it the moment the page opens.
    try:
        store.propose()
    except Exception:                                          # noqa: BLE001
        pass

    print(json.dumps({
        "ok": True, "url": url, "port": port, "pid": os.getpid(),
        "version": VERSION, "oauth": is_tier1(),
        "session_file": str(session_path()),
        "meta": {"van_gogh_root": resolved_meta().get("van_gogh_root", "")},
    }, indent=2))
    sys.stdout.flush()

    if not args.no_browser:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        clear_session()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
