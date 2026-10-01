#!/usr/bin/env python3
"""Find out who the user is, while the rest of the install is still running.

Setup asks five questions and learns a name, a role in one line, and one
venture. Every briefing after that grades mail against a business whose
keywords are empty and whose priorities were never stated, so the product
spends its first fortnight surfacing whatever arrived last.

This closes that gap without asking the user to type their own biography. The
moment setup knows a name and a company, it starts a headless session as the
`operator-research` agent, which reads the public web while the user is still
answering questions about notetakers and schedulers. By the finish step the
research is usually done and the user is shown what it found.

Four rules carry the design, and each is a boundary rather than an instruction:

**Nothing reaches the vault without a person.** The agent writes into a
staging folder under logs/ and stops. `apply` is a separate command, run by a
skill, after the user has read a summary and said yes. A research session that
went wrong costs a folder nobody opens.

**The agent cannot reach the user's notes.** Its working directory is its own
staging folder and no `--add-dir` is passed at all, so `wiki/` is not writable
by it whatever the model decides it would like to do. The agent file says the
same thing in words; the flag is what makes it true.

**A fact keeps its source or it stops being a fact.** `_validate` checks every
fact's `source_url` against the declared sources list and demotes the ones
that do not match into inference. A prompt instruction is necessary and never
sufficient, so the demotion is code.

**It fails open, like everything else here.** Every path swallows its own
errors and reports them as state. The worst this may do is nothing, because
the alternative is an install that cannot finish.

CLI:
    operator_research.py start --name "X" --company "Y" --domains a.com,b.com
    operator_research.py status
    operator_research.py wait --timeout 300
    operator_research.py show
    operator_research.py apply --priorities-json '[0,2]'
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_loader                                            # noqa: E402
import run_ledger                                               # noqa: E402
from platform_compat import NO_WINDOW, claude_bin               # noqa: E402

AGENT = "operator-research"
JOB_NAME = "operator-research"

# Reading and thinking over maybe twenty pages. Fifteen minutes is long enough
# for a thorough pass and short enough that a wedged session cannot outlive the
# install that started it.
TIMEOUT_S = 15 * 60

# What `status` reports. A caller branches on these exact strings, so they are
# part of the contract with the skills.
NOT_STARTED = "not_started"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

# Written as escapes, never as the characters themselves: a repo-wide dash
# sweep that rewrote this table would silently redefine what the stripper
# looks for, and it would keep reporting clean while letting both through.
_DASHES = {"\u2014": ", ", "\u2013": "-"}


def _strip_dashes(text: str) -> str:
    """No em-dash or en-dash reaches a file we write.

    The agent is told this in its own voice rules and the model ignores that
    instruction often enough to matter, so every string that lands in the
    vault goes through here first. Same reasoning as everywhere else in this
    product: the prompt is necessary, the code is the guarantee.
    """
    out = str(text or "")
    for bad, good in _DASHES.items():
        out = out.replace(bad, good)
    return out


def _slug(company: str, name: str) -> str:
    """A stable folder name for one research run.

    Company first because a person can change companies and the research is
    about the pair. Falls back to the name alone when there is no company.
    """
    raw = f"{company} {name}".strip() or "operator"
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug[:60] or "operator"


def research_root() -> Path:
    """Where every run stages its work. Machinery, not notes the user keeps."""
    return config_loader.logs_dir() / "operator-research"


def staging_dir(slug: str) -> Path:
    return research_root() / slug


def _status_path(slug: str) -> Path:
    return staging_dir(slug) / "status.json"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_status(slug: str, data: dict) -> None:
    try:
        path = _status_path(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str),
                        encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass  # fails open: a status we cannot save reads as failed, not as a crash


def latest_slug() -> str:
    """The most recently started run, for callers that did not keep the slug.

    Setup starts one and finishes it three steps later in a different tool
    call, so requiring the slug to be carried between them is one more thing
    that can be dropped.
    """
    try:
        dirs = [d for d in research_root().iterdir() if d.is_dir()]
    except OSError:
        return ""
    if not dirs:
        return ""
    def started(d: Path) -> str:
        return str(_read_json(d / "status.json").get("started") or "")
    return sorted(dirs, key=lambda d: (started(d), d.name))[-1].name


def _pid_alive(pid: int) -> bool:
    """Is that process still there?

    Only ever used to turn a stale `running` into `failed`. A status file that
    says running forever, because the session was killed before it could write
    its own ending, is the empty-set pass this product keeps learning about:
    a state nobody set is not evidence of a state.
    """
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


# ── The prompt ────────────────────────────────────────────────────────────────

def prompt_for(name: str, company: str, domains: str = "", role: str = "",
               city: str = "", today: str = "") -> str:
    """What the researcher is told. Facts only, and no instructions it has.

    Deliberately short. Everything about how to work, what may be written and
    what may not is in the agent file, which the CLI loads as the session's
    own definition. Repeating it here would create a second copy that drifts,
    and the copy in the prompt is the one that would win.
    """
    lines = [
        "Research the person below and the company they work at, from public "
        "web sources only.",
        "",
        f"Name: {name}",
        f"Company: {company}",
    ]
    if role:
        lines.append(f"Role, as they described it: {role}")
    if domains:
        lines.append(f"Email domains they use: {domains}")
    if city:
        lines.append(f"Based in: {city}")
    lines += [
        f"Today: {today or datetime.now().date().isoformat()}",
        "",
        "Anchor the identity first, then write proposal.json and dossier.md "
        "in your working directory, in the shape your own definition sets "
        "out. Write nothing anywhere else.",
    ]
    return "\n".join(lines)


def _plugin_root() -> Path:
    """Where this file lives, which is the plugin root.

    Derived from `__file__` rather than read from the `plugin-root` pointer:
    the pointer is per-user state that a half-finished install may not have
    written, and this runs DURING an install. The module doing the spawning
    always knows where it is.
    """
    return Path(__file__).resolve().parent.parent


def _command(prompt: str, claude: str | None = None) -> list:
    """The argv for one research session.

    Every boundary here is a flag rather than a sentence in the prompt.

    - `--agent operator-research` makes the agent file the session's own
      definition, so its rules arrive as the system prompt rather than as
      advice inside a user turn the model may weigh against something else.
    - `--plugin-dir` is what makes that name resolvable at all. Agents under a
      plugin's `agents/` folder are discovered only when the plugin is LOADED,
      and a session spawned from a script loads nothing: without this flag the
      CLI exits 1 with "--agent 'operator-research' not found".
    - The working directory is the STAGING FOLDER and no `--add-dir` is passed
      at all, so `wiki/` is not writable by this session. That is rule 1 of the
      agent file, enforced.
    - `--permission-mode bypassPermissions` because nobody is at a terminal to
      answer a prompt, and a denied call fails the run silently.
    - `--disable-slash-commands` because a research session that invokes a
      briefing skill would re-enter the machinery that started it.
    """
    return [
        claude or claude_bin(), "-p", prompt,
        "--agent", AGENT,
        "--plugin-dir", str(_plugin_root()),
        "--permission-mode", "bypassPermissions",
        "--disable-slash-commands",
    ]


# ── Validation ────────────────────────────────────────────────────────────────

def _fact_list(raw) -> list:
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and (item.get("text") or "").strip():
            out.append({"text": _strip_dashes(item.get("text")),
                        "source_url": str(item.get("source_url") or "").strip()})
    return out


def _validate(proposal: dict) -> dict:
    """Normalise the agent's file, and demote every fact that lost its source.

    The agent is told each fact carries a URL that also appears in `sources`.
    When one does not, the fact is not dropped, it is moved into the inference
    list, where it is labelled unverified and reads as a judgment rather than
    as a finding. Dropping it would throw away real content; keeping it where
    it was would let an unsourced claim wear a citation's clothes.

    Returns the cleaned proposal with an `unsourced` count. Raises ValueError
    when the file is not a proposal at all, which is what a truncated write or
    a crashed session leaves behind.
    """
    if not isinstance(proposal, dict):
        raise ValueError("proposal.json is not an object")
    identity = proposal.get("identity")
    if not isinstance(identity, dict) or not (identity.get("full_name") or "").strip():
        raise ValueError("proposal.json carries no identity")
    confidence = str(identity.get("confidence") or "").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    sources = []
    known = set()
    for src in proposal.get("sources") if isinstance(proposal.get("sources"), list) else []:
        if isinstance(src, dict) and (src.get("url") or "").strip():
            url = str(src["url"]).strip()
            known.add(url)
            sources.append({"url": url,
                            "title": _strip_dashes(src.get("title")),
                            "used_for": _strip_dashes(src.get("used_for"))})

    person = proposal.get("person") if isinstance(proposal.get("person"), dict) else {}
    company = proposal.get("company") if isinstance(proposal.get("company"), dict) else {}
    persona = proposal.get("persona") if isinstance(proposal.get("persona"), dict) else {}
    proposals = proposal.get("proposals") if isinstance(proposal.get("proposals"), dict) else {}

    unsourced = []
    kept = {}
    for key, block in (("person", person), ("company", company)):
        good, bad = [], []
        for fact in _fact_list(block.get("facts")):
            (good if fact["source_url"] and fact["source_url"] in known else bad).append(fact)
        kept[key] = good
        unsourced.extend(bad)

    themes = [_strip_dashes(t) for t in (persona.get("themes") or []) if str(t).strip()]
    # A demoted fact is inference now, so it is stated as one and carries no
    # citation. It reads beside the model's own themes because that is exactly
    # what it has become.
    themes.extend(f"Unverified: {f['text']}" for f in unsourced)

    def _priorities(raw) -> list:
        out = []
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, dict) and (item.get("name") or "").strip():
                out.append({"name": _strip_dashes(item.get("name")),
                            "detail": _strip_dashes(item.get("detail"))})
        return out

    return {
        "identity": {
            "full_name": _strip_dashes(identity.get("full_name")).strip(),
            "company": _strip_dashes(identity.get("company")).strip(),
            "confidence": confidence,
            "anchor_evidence": _fact_list(identity.get("anchor_evidence")),
        },
        "person": {
            "title": _strip_dashes(person.get("title")),
            "summary": _strip_dashes(person.get("summary")),
            "facts": kept["person"],
        },
        "company": {
            "summary": _strip_dashes(company.get("summary")),
            "facts": kept["company"],
            "industry_terms": [_strip_dashes(t) for t in
                               (company.get("industry_terms") or []) if str(t).strip()],
        },
        "persona": {
            "themes": themes,
            "style": _strip_dashes(persona.get("style")),
            "inferred_priorities": _priorities(persona.get("inferred_priorities")),
        },
        "proposals": {
            "role_description": _strip_dashes(proposals.get("role_description")).strip(),
            "keywords": [_strip_dashes(k).strip() for k in
                         (proposals.get("keywords") or []) if str(k).strip()],
            "priorities": _priorities(proposals.get("priorities")),
        },
        "sources": sources,
        "dossier_md": _strip_dashes(proposal.get("dossier_md")),
        "unsourced": len(unsourced),
    }


def read_proposal(slug: str) -> dict:
    """The validated proposal for a run, or {} when there is not one."""
    try:
        return _validate(_read_json(staging_dir(slug) / "proposal.json"))
    except ValueError:
        return {}


# ── start ─────────────────────────────────────────────────────────────────────

def start(name: str, company: str, domains: str = "", role: str = "",
          city: str = "", claude: str | None = None) -> dict:
    """Kick off one research run in the background. Never raises.

    Returns `{ok, slug, staging, why}`. `ok: false` is a normal answer on a
    machine with no `claude` on PATH or no vault yet, and the install skill is
    told to carry on either way: research is the one part of setup that is
    worth nothing if it blocks the rest.
    """
    result = {"ok": False, "slug": "", "staging": "", "why": ""}
    name = (name or "").strip()
    company = (company or "").strip()
    if not name:
        result["why"] = "no name to research"
        return result
    slug = _slug(company, name)
    result["slug"] = slug
    try:
        target = staging_dir(slug)
        target.mkdir(parents=True, exist_ok=True)
        result["staging"] = str(target)
    except (OSError, KeyError, FileNotFoundError) as exc:
        result["why"] = f"no vault to stage in: {type(exc).__name__}"
        return result

    try:
        claude or claude_bin()
    except Exception as exc:                                    # noqa: BLE001
        result["why"] = f"the claude CLI is not on PATH: {type(exc).__name__}"
        _write_status(slug, {"state": FAILED, "slug": slug,
                             "reason": "the claude CLI is not on PATH",
                             "started": run_ledger.now_stamp(),
                             "finished": run_ledger.now_stamp()})
        return result

    inputs = {"name": name, "company": company, "domains": domains,
              "role": role, "city": city}
    # The child is this same file, run again with `_run`. It re-reads its
    # inputs from the status file rather than taking them on a command line,
    # which keeps a person's name out of `ps` output on a shared machine.
    _write_status(slug, {"state": RUNNING, "slug": slug, "inputs": inputs,
                         "started": run_ledger.now_stamp(), "pid": 0})
    try:
        log = (staging_dir(slug) / "run.log").open("a", encoding="utf-8")
    except OSError:
        log = subprocess.DEVNULL
    try:
        cmd = [sys.executable, str(Path(__file__).resolve()), "_run", slug]
        if claude:
            cmd += ["--claude", claude]
        proc = subprocess.Popen(                                # noqa: S603
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=NO_WINDOW,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        _write_status(slug, {"state": FAILED, "slug": slug,
                             "reason": f"could not start: {type(exc).__name__}",
                             "started": run_ledger.now_stamp(),
                             "finished": run_ledger.now_stamp()})
        result["why"] = f"could not start: {type(exc).__name__}"
        return result
    finally:
        if log is not subprocess.DEVNULL:
            try:
                log.close()
            except OSError:
                pass

    status = _read_json(_status_path(slug))
    status["pid"] = proc.pid
    _write_status(slug, status)
    result["ok"] = True
    result["why"] = "researching in the background"
    return result


def _run(slug: str, claude: str | None = None) -> int:
    """The detached child: spawn the session, validate what it wrote, stamp.

    Runs with nobody watching, so it updates the CLI first for the reason the
    whole product does: a CLI a few versions behind is rejected outright for
    current models, and this is an unattended entry point.
    """
    try:
        import claude_update
        claude_update.ensure_current()
    except Exception:                                           # noqa: BLE001
        pass  # fails open: an update we could not run is not a reason to stop

    status = _read_json(_status_path(slug))
    inputs = status.get("inputs") if isinstance(status.get("inputs"), dict) else {}
    started = status.get("started") or run_ledger.now_stamp()
    prompt = prompt_for(inputs.get("name", ""), inputs.get("company", ""),
                        inputs.get("domains", ""), inputs.get("role", ""),
                        inputs.get("city", ""))
    target = staging_dir(slug)

    def finish(state: str, reason: str, rc: int = 0) -> int:
        status.update({"state": state, "reason": reason, "rc": rc,
                       "finished": run_ledger.now_stamp()})
        _write_status(slug, status)
        try:
            run_ledger.record_run(JOB_NAME, started, run_ledger.now_stamp(),
                                  0 if state == DONE else 1,
                                  f"{slug}: {reason}")
        except Exception:                                       # noqa: BLE001
            pass
        return 0 if state == DONE else 1

    try:
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        env["VAN_GOGH_UNATTENDED"] = "1"
        proc = subprocess.run(                                  # noqa: S603
            _command(prompt, claude),
            cwd=str(target),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT_S,
            creationflags=NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return finish(FAILED, f"the research ran past {TIMEOUT_S // 60} minutes", 1)
    except Exception as exc:                                    # noqa: BLE001
        return finish(FAILED, f"could not research: {type(exc).__name__}", 1)

    tail = ((proc.stderr or proc.stdout or "").strip())[-500:]
    if proc.returncode != 0:
        return finish(FAILED, tail or f"the session exited {proc.returncode}",
                      proc.returncode)

    raw = _read_json(target / "proposal.json")
    if not raw:
        return finish(FAILED, "the research wrote nothing to read", 1)
    try:
        clean = _validate(raw)
    except ValueError as exc:
        return finish(FAILED, f"what it wrote was not usable: {exc}", 1)
    try:
        (target / "proposal.json").write_text(
            json.dumps(clean, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    except OSError as exc:
        return finish(FAILED, f"could not save the result: {type(exc).__name__}", 1)

    status["confidence"] = clean["identity"]["confidence"]
    status["unsourced"] = clean["unsourced"]
    return finish(DONE, "researched", 0)


# ── status / wait / show ──────────────────────────────────────────────────────

def status(slug: str = "") -> dict:
    """What state one run is in, with a dead session reported as failed."""
    slug = slug or latest_slug()
    if not slug:
        return {"state": NOT_STARTED, "slug": "", "reason": "nothing has been researched"}
    data = _read_json(_status_path(slug))
    if not data:
        return {"state": NOT_STARTED, "slug": slug, "reason": "nothing has been researched"}
    state = data.get("state") or NOT_STARTED
    if state == RUNNING and not data.get("finished") and not _pid_alive(data.get("pid")):
        state = FAILED
        data["reason"] = "the research session stopped before it finished"
    out = {"state": state, "slug": slug,
           "started": data.get("started", ""),
           "finished": data.get("finished", ""),
           "reason": data.get("reason", ""),
           "confidence": data.get("confidence", ""),
           "staging": str(staging_dir(slug))}
    try:
        started = datetime.fromisoformat(str(data.get("started")))
        end = (datetime.fromisoformat(str(data["finished"]))
               if data.get("finished") else datetime.now())
        out["elapsed_min"] = round((end - started).total_seconds() / 60, 1)
    except (TypeError, ValueError):
        out["elapsed_min"] = 0
    out["proposal_ok"] = bool(read_proposal(slug)) if state == DONE else False
    return out


def wait(slug: str = "", timeout: int = 300, poll: int = 5) -> dict:
    """Block until the run leaves `running`, or until the timeout.

    Used by the on-demand skill, where the user asked for this right now and a
    background run they have to come back for is worse than a wait they can
    watch. The install uses it too, but only after asking.
    """
    slug = slug or latest_slug()
    deadline = time.monotonic() + max(0, int(timeout))
    while True:
        current = status(slug)
        if current.get("state") != RUNNING:
            return current
        if time.monotonic() >= deadline:
            current["timed_out"] = True
            return current
        time.sleep(poll)


def show(slug: str = "") -> dict:
    """The summary a person reads before anything is written.

    Returns the proposal plus a rendered markdown block, so the skill prints
    one thing rather than assembling a page out of ten JSON keys and getting
    the order wrong.
    """
    slug = slug or latest_slug()
    proposal = read_proposal(slug)
    if not proposal:
        return {"ok": False, "slug": slug,
                "why": "there is no finished research to show"}
    ident = proposal["identity"]
    lines = [_identity_line(ident, proposal["person"].get("title")),
             f"Identity confidence: {ident['confidence']}."]
    if ident["anchor_evidence"]:
        lines.append("")
        lines.append("What ties them together:")
        for ev in ident["anchor_evidence"][:3]:
            lines.append(f"- {ev['text']} ({ev['source_url']})")
    if proposal["person"].get("summary"):
        lines += ["", "**Them:** " + proposal["person"]["summary"]]
    if proposal["company"].get("summary"):
        lines += ["", "**The company:** " + proposal["company"]["summary"]]
    if proposal["persona"].get("themes"):
        lines += ["", "**What they keep coming back to:** "
                  + ", ".join(proposal["persona"]["themes"][:5])]
    kw = proposal["proposals"]["keywords"]
    if kw:
        lines += ["", "**Keywords it would add for routing your mail:** " + ", ".join(kw)]
    pri = proposal["proposals"]["priorities"]
    if pri:
        lines += ["", "**Priorities it suggests, pick the ones that are real:**"]
        for i, p in enumerate(pri):
            detail = f" {p['detail']}" if p.get("detail") else ""
            lines.append(f"{i}. {p['name']}.{detail}")
    lines += ["", f"Read from {len(proposal['sources'])} public pages."]
    if proposal.get("unsourced"):
        lines.append(f"{proposal['unsourced']} claims arrived without a working "
                     "source and were moved to unverified.")
    return {"ok": True, "slug": slug, "proposal": proposal,
            "summary_md": _strip_dashes("\n".join(lines))}


# ── apply ─────────────────────────────────────────────────────────────────────

def _identity_line(ident: dict, title: str = "") -> str:
    """One sentence naming who this is, without saying the company twice.

    The title is free text a model wrote, and it frequently carries the
    company inside it ("Founder, SunCast Media and host of the SunCast
    podcast"). Appending "at <company>" to that produced a sentence naming the
    company twice in nine words, which reads as a machine stitching two fields
    together. So the company is appended only when the title does not already
    carry it.
    """
    name = ident.get("full_name") or "This person"
    company = (ident.get("company") or "").strip()
    title = (title or "").strip().rstrip(".")
    parts = f"**{name}**"
    if title:
        parts += f", {title}"
    if company and company.lower() not in title.lower():
        parts += f" at {company}"
    elif not title and not company:
        return parts + ", role and company not confirmed."
    return parts + "."


def _unhedge(text: str) -> str:
    """Drop a leading "Inference." from a line already under an inferred heading.

    The agent labels its own guesses, which is the right instinct, and both
    places this text lands already say so in their heading. Saying it twice in
    two lines is the tell that nobody read the output.
    """
    out = str(text or "").strip()
    for lead in ("Inference:", "Inference.", "Inferred:", "Inferred."):
        if out.lower().startswith(lead.lower()):
            out = out[len(lead):].strip()
            break
    return out


MEMORY_BEGIN = "<!-- van-gogh:operator-profile:begin -->"
ENTITY_BEGIN = "<!-- van-gogh:operator-research:begin -->"
ENTITY_END = "<!-- van-gogh:operator-research:end -->"
MEMORY_END = "<!-- van-gogh:operator-profile:end -->"


def _safe_page_name(name: str) -> str:
    """A filename for a vault page. Letters, digits, spaces, hyphens."""
    cleaned = re.sub(r"[^A-Za-z0-9 \-]", "", _strip_dashes(name)).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "Unnamed"


def _entity_page(name: str, kind: str, proposal: dict, source_title: str,
                 business: str, today: str) -> str:
    block = proposal["person"] if kind == "person" else proposal["company"]
    lines = [
        "---", "type: entity", f"title: {name}", f"created: {today}",
        f"updated: {today}", f'sources: ["[[{source_title}]]"]',
        f"business: {business}", f"tags: [{business}, {kind}]", "---", "",
        f"# {name}", "",
    ]
    if block.get("summary"):
        lines += ["## Overview", "", block["summary"], ""]
    if block.get("facts"):
        lines += ["## Key Facts", ""]
        lines += [f"- {f['text']} ([source]({f['source_url']}))" for f in block["facts"]]
        lines.append("")
    if kind == "person":
        persona = proposal["persona"]
        inferred = list(persona.get("themes") or [])
        if persona.get("style"):
            inferred.append(f"Writes and speaks: {_unhedge(persona['style'])}")
    else:
        inferred = list(proposal["company"].get("industry_terms") or [])
        inferred = [f"Industry vocabulary: {', '.join(inferred)}"] if inferred else []
    if inferred:
        lines += ["## Inferred, unverified", "",
                  "Worked out from the sources below, not stated on any of them.", ""]
        lines += [f"- {t}" for t in inferred]
        lines.append("")
    lines += ["## Appearances in Sources", "", f"- [[{source_title}]]", "",
              "## Related", ""]
    return _strip_dashes("\n".join(lines)) + "\n"


def _entity_section(name: str, kind: str, proposal: dict, source_title: str,
                    business: str, today: str) -> str:
    """The research block, marked, as it appears inside an entity page.

    One definition used by both the create and the update path, so a re-run
    finds exactly what the previous run left and replaces it.
    """
    page = _entity_page(name, kind, proposal, source_title, business, today)
    body = page.split("\n---\n", 1)[-1].split("\n", 2)[-1].strip()
    return f"{ENTITY_BEGIN}\n## Operator research ({today})\n\n{body}\n{ENTITY_END}"


def _source_page(proposal: dict, source_title: str, business: str,
                 today: str) -> str:
    ident = proposal["identity"]
    person_name = ident["full_name"]
    company_name = ident["company"]
    lines = [
        "---", "type: source", f"title: {source_title}", f"created: {today}",
        f"updated: {today}", "sources: []", f"business: {business}",
        f"tags: [{business}, research]", "---", "",
        f"# {source_title}", "",
        "## Summary", "",
        f"Public web research on {person_name}"
        + (f" and {company_name}" if company_name else "")
        + f", run on {today}. Identity confidence: {ident['confidence']}.", "",
    ]
    if proposal.get("dossier_md"):
        lines += [proposal["dossier_md"], ""]
    lines += ["## Key Takeaways", ""]
    takeaways = ([f["text"] for f in proposal["person"]["facts"][:4]]
                 + [f["text"] for f in proposal["company"]["facts"][:4]])
    lines += [f"- {t}" for t in takeaways] if takeaways else ["- Nothing firm enough to keep."]
    lines += ["", "## Entities Mentioned", "", f"- [[{person_name}]]"]
    if company_name:
        lines.append(f"- [[{company_name}]]")
    lines += ["", "## Sources", ""]
    lines += ([f"- [{s['title'] or s['url']}]({s['url']})"
               + (f" {s['used_for']}" if s.get("used_for") else "")
               for s in proposal["sources"]]
              or ["- No public page could be confirmed."])
    lines.append("")
    return _strip_dashes("\n".join(lines)) + "\n"


def _memory_block(proposal: dict, today: str) -> str:
    ident = proposal["identity"]
    lines = [MEMORY_BEGIN, "## Operator profile", "",
             f"_Read off the public web on {today}. "
             "Confidence: " + ident["confidence"] + "._", ""]
    lines.append(_identity_line(ident, proposal["person"].get("title")))
    if proposal["person"].get("summary"):
        lines += ["", proposal["person"]["summary"]]
    if proposal["company"].get("summary"):
        lines += ["", f"**The business.** {proposal['company']['summary']}"]
    if proposal["persona"].get("themes"):
        lines += ["", "**Recurring themes.** "
                  + "; ".join(proposal["persona"]["themes"][:5]) + "."]
    if proposal["persona"].get("style"):
        lines += ["", "**How they write.** "
                  + _unhedge(proposal["persona"]["style"])]
    lines += ["", f"Full record: [[{_research_title(proposal, today)}]]", MEMORY_END]
    return _strip_dashes("\n".join(lines))


def _research_title(proposal: dict, today: str) -> str:
    subject = proposal["identity"]["company"] or proposal["identity"]["full_name"]
    return _safe_page_name(f"Operator research, {subject} ({today})")


def _upsert_memory(text: str, block: str) -> str:
    """Replace the managed block, or insert it above the follow-ups ledger.

    Placed above "## Pending follow-ups" deliberately: that heading is parsed
    by the follow-up radar, and a block landing inside it would be read as
    ledger entries. Re-running replaces in place, so a refresh never stacks a
    second profile on the first.
    """
    if MEMORY_BEGIN in text and MEMORY_END in text:
        head, rest = text.split(MEMORY_BEGIN, 1)
        _, tail = rest.split(MEMORY_END, 1)
        return head + block + tail
    marker = "## Pending follow-ups"
    if marker in text:
        head, tail = text.split(marker, 1)
        return head.rstrip() + "\n\n" + block + "\n\n" + marker + tail
    return text.rstrip() + "\n\n" + block + "\n"


def _pick_business(company: str) -> dict | None:
    """The bucket this research belongs to.

    The company name against display_name and tag first, since that is the
    bucket the user just created for it during setup. Otherwise the first
    bucket that is not `personal`, because a person researching their operator
    profile has exactly one work bucket on a fresh install.
    """
    target = (company or "").strip().lower()
    buckets = config_loader.businesses()
    if target:
        for b in buckets:
            if target in {str(b.get("display_name", "")).lower(),
                          str(b.get("tag", "")).lower()}:
                return b
    for b in buckets:
        if str(b.get("tag", "")).lower() != "personal":
            return b
    return buckets[0] if buckets else None


def _config_path() -> Path:
    return config_loader.van_gogh_root() / "config.json"


def apply(slug: str = "", priorities: list | None = None,
          force: bool = False, today: str = "") -> dict:
    """Copy a finished, human-approved research run into the vault.

    The only path in this module that writes anything a user reads, and it is
    never reached without a person having read the summary first.
    """
    slug = slug or latest_slug()
    out = {"ok": False, "slug": slug, "written": [], "config_changes": {}, "why": ""}
    proposal = read_proposal(slug)
    if not proposal:
        out["why"] = "there is no finished research to file"
        return out
    if proposal["identity"]["confidence"] == "low" and not force:
        out["why"] = ("the research could not confirm this is the right person, "
                      "so nothing was filed")
        return out

    today = today or datetime.now().date().isoformat()
    ident = proposal["identity"]
    person_name = _safe_page_name(ident["full_name"])
    company_name = _safe_page_name(ident["company"]) if ident["company"] else ""
    bucket = _pick_business(ident["company"])
    business = str((bucket or {}).get("tag") or "personal")
    source_title = _research_title(proposal, today)

    try:
        sources_dir = config_loader.sources_dir()
        entities_dir = config_loader.entities_dir()
        sources_dir.mkdir(parents=True, exist_ok=True)
        entities_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, KeyError, FileNotFoundError) as exc:
        out["why"] = f"could not reach the vault: {type(exc).__name__}"
        return out

    # 1. The source page. Overwritten on a re-run for the same day, which is
    #    the only case where the title collides, and it is the same research.
    source_path = sources_dir / f"{source_title}.md"
    source_path.write_text(_source_page(proposal, source_title, business, today),
                           encoding="utf-8")
    out["written"].append(str(source_path))

    # 2. The entity pages. An existing page is appended to, never replaced:
    #    the user may have written on it, and that is theirs.
    for name, kind in ((person_name, "person"), (company_name, "company")):
        if not name:
            continue
        path = entities_dir / f"{name}.md"
        page = _entity_page(name, kind, proposal, source_title, business, today)
        if not path.exists():
            # Marked even on a page we are creating. A create path that wrote
            # an UNMARKED page and an update path that looked for a marker
            # disagreed about the same file, so the second run found no marker
            # and appended a second copy of every section.
            front = page.split("\n---\n", 1)[0] + "\n---\n\n" + f"# {name}\n\n"
            path.write_text(front + _entity_section(
                name, kind, proposal, source_title, business, today) + "\n",
                encoding="utf-8")
            out["written"].append(str(path))
            continue
        # The page exists, so the user may have written on it and that is
        # theirs. The research goes in its own marked section, and a re-run
        # REPLACES that section rather than appending beside it: the first
        # apply writes a whole page with no marker, so a guard that only
        # checked whether the marker was present appended a second copy of
        # everything on the next run. Replace, do not merely skip.
        existing = path.read_text(encoding="utf-8")
        section = _entity_section(name, kind, proposal, source_title,
                                  business, today)
        if ENTITY_BEGIN in existing and ENTITY_END in existing:
            head, rest = existing.split(ENTITY_BEGIN, 1)
            _, tail = rest.split(ENTITY_END, 1)
            updated = head.rstrip() + "\n\n" + section + tail
        else:
            updated = existing.rstrip() + "\n\n" + section + "\n"
        if updated != existing:
            path.write_text(updated, encoding="utf-8")
            out["written"].append(str(path))

    # 3. index.md and log.md, through the same helpers the meeting ingest uses
    #    so a research page files itself exactly like every other page.
    try:
        import meeting_ingest
        index = config_loader.vault("wiki/index.md")
        if index.exists():
            text = index.read_text(encoding="utf-8")
            one_liner = (proposal["person"].get("summary") or "Operator research.")
            one_liner = one_liner.split(".")[0][:120]
            text = meeting_ingest.append_under_heading(
                text, "Sources", f"- [[{source_title}]] : {one_liner} `#{business}`")
            for name in (n for n in (person_name, company_name) if n):
                text = meeting_ingest.append_under_heading(
                    text, "Entities",
                    f"- [[{name}]] : from [[{source_title}]]. `#{business}`")
            index.write_text(text, encoding="utf-8")
            out["written"].append(str(index))
        log = config_loader.vault("wiki/log.md")
        if log.exists():
            text = log.read_text(encoding="utf-8")
            marker = f"## [{today}] operator research | {source_title}"
            if marker not in text:
                names = ", ".join(f"[[{n}]]" for n in (person_name, company_name) if n)
                text = text.rstrip() + (
                    f"\n\n{marker}\n"
                    f"- Source page: [[{source_title}]]\n"
                    f"- Entities: {names or 'none'}\n"
                    f"- Pages read: {len(proposal['sources'])}\n")
                log.write_text(text, encoding="utf-8")
                out["written"].append(str(log))
    except Exception as exc:                                    # noqa: BLE001
        # A vault with no index is a vault that was never scaffolded. The
        # pages themselves are already written and are the thing that matters.
        out["index_note"] = f"could not update the catalog: {type(exc).__name__}"

    # 4. Workspace memory, which is what makes this load in every session.
    try:
        mem = config_loader.vangogh_memory_path()
        mem.parent.mkdir(parents=True, exist_ok=True)
        existing = mem.read_text(encoding="utf-8") if mem.exists() else ""
        mem.write_text(_upsert_memory(existing, _memory_block(proposal, today)),
                       encoding="utf-8")
        out["written"].append(str(mem))
    except OSError as exc:
        out["memory_note"] = f"could not update memory: {type(exc).__name__}"

    # 5. config.json, backed up first. Only three things change, and each one
    #    only in the safe direction: a role description that was empty, more
    #    keywords, and the priorities the user actually picked.
    try:
        path = _config_path()
        config = json.loads(path.read_text(encoding="utf-8"))
        changes = {}
        role = proposal["proposals"]["role_description"]
        if role and not str(config.get("user", {}).get("role_description", "")).strip():
            config.setdefault("user", {})["role_description"] = role
            changes["role_description"] = role
        if bucket is not None:
            for entry in config.get("businesses", []):
                if entry.get("tag") != bucket.get("tag"):
                    continue
                have = {str(k).lower() for k in entry.get("keywords", [])}
                added = [k for k in proposal["proposals"]["keywords"]
                         if k.lower() not in have and not have.add(k.lower())]
                if added:
                    entry.setdefault("keywords", []).extend(added)
                    changes["keywords_added"] = added
                chosen = priorities if priorities is not None else []
                if chosen:
                    names = {str(p.get("name", "")).lower()
                             for p in entry.get("priorities", [])}
                    new = [{"name": p["name"], "detail": p.get("detail", ""),
                            "added": today}
                           for p in chosen if p.get("name")
                           and p["name"].lower() not in names]
                    if new:
                        entry.setdefault("priorities", []).extend(new)
                        changes["priorities_added"] = [p["name"] for p in new]
                changes["business"] = entry.get("display_name") or entry.get("tag")
                break
        if changes:
            backup = path.with_suffix(".json.bak")
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8", newline="\n")
            out["written"].append(str(path))
            out["config_backup"] = str(backup)
        out["config_changes"] = changes
    except (OSError, ValueError, KeyError) as exc:
        out["config_note"] = f"could not update settings: {type(exc).__name__}"

    out["ok"] = True
    return out


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    config_loader.force_utf8_io()
    parser = argparse.ArgumentParser(
        description="Research the person installing Van Gogh, and their company.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start", help="begin a research run in the background")
    p_start.add_argument("--name", required=True)
    p_start.add_argument("--company", default="")
    p_start.add_argument("--domains", default="")
    p_start.add_argument("--role", default="")
    p_start.add_argument("--city", default="")
    p_start.add_argument("--claude", default=None)
    p_start.add_argument("--wait", type=int, default=0,
                         help="block up to N seconds for it to finish")

    p_run = sub.add_parser("_run", help=argparse.SUPPRESS)
    p_run.add_argument("slug")
    p_run.add_argument("--claude", default=None)

    for name in ("status", "show"):
        p = sub.add_parser(name)
        p.add_argument("--slug", default="")

    p_wait = sub.add_parser("wait")
    p_wait.add_argument("--slug", default="")
    p_wait.add_argument("--timeout", type=int, default=300)

    p_apply = sub.add_parser("apply", help="file a finished run into the vault")
    p_apply.add_argument("--slug", default="")
    p_apply.add_argument("--priorities-json", default="",
                         help="the priorities the user picked, as JSON")
    p_apply.add_argument("--force", action="store_true",
                         help="file it even when the identity is unconfirmed")

    args = parser.parse_args(argv)

    if args.cmd == "_run":
        return _run(args.slug, args.claude)
    if args.cmd == "start":
        out = start(args.name, args.company, args.domains, args.role,
                    args.city, args.claude)
        if out["ok"] and args.wait:
            out["final"] = wait(out["slug"], args.wait)
    elif args.cmd == "status":
        out = status(args.slug)
    elif args.cmd == "wait":
        out = wait(args.slug, args.timeout)
    elif args.cmd == "show":
        out = show(args.slug)
    else:
        picked = None
        if args.priorities_json:
            try:
                picked = json.loads(args.priorities_json)
            except ValueError:
                print(json.dumps({"ok": False, "why": "the priorities were not valid JSON"}))
                return 1
            if picked and all(isinstance(p, int) for p in picked):
                proposal = read_proposal(args.slug or latest_slug())
                offered = (proposal.get("proposals", {}).get("priorities") or [])
                picked = [offered[i] for i in picked if 0 <= i < len(offered)]
        out = apply(args.slug, picked, args.force)

    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
