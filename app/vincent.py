#!/usr/bin/env python3
"""Vincent: what the chief of staff knows before he says a word.

Vincent is the one agent the user talks to (`agents/vincent.md`). He knows
every skill and agent on the machine, whether each scheduled job ran, and what
the user asked him to remember, and he hands work to whichever of them fits.
This module is the part of that which is code, because none of it may be left
to a model's recollection:

- `catalog()`   every skill and agent, from every place one can live, read
                off disk on each call. Nothing is cached and nothing is
                hand-kept, so a skill added a minute ago is in the next answer.
- `schedule()`  one row per `job_registry` row: on or off, what the OS
                scheduler holds, the last run, today's grade from
                `job_watch.evaluate` and the watcher's own sentence about it.
                It reads. It never re-runs, never writes the watcher's state.
- `memory()`    the workspace memory page, its follow-ups ledger, and Claude
                Code's memory for this vault and this plugin, with anything
                credential-shaped replaced.
- `remember()`  the one write: a dated line inside a block this module owns
                in the workspace memory page.
- `situation()` all of the above plus the line Vincent opens with.
- `answer()`    one headless turn for a surface with no terminal (the
                Workbench chat today, a chat bridge later).

Three things are decided here and never by the prompt, for the reason
`claude_cli` gives at length (an instruction is necessary and never
sufficient):

- **Who a request went to.** The agent file requires a line `Handing to: ...`
  before any hand-off. `parse_handoff` reads it, so routing is a fact code
  can check and act on, not a sentence to interpret.
- **What may run with nobody at a terminal.** A headless turn has Read, Grep
  and Glob and nothing else. When it names a skill, code runs that skill, and
  only if it is in `HEADLESS_RUNNABLE`. Everything else is answered with
  where to run it.
- **What needs a yes first.** `NEEDS_CONFIRM` marks the skills that change an
  account, the settings or the install. They are flagged in every catalog row
  and are never in `HEADLESS_RUNNABLE`.

Nothing here can send mail. There is no call to the send path in this file,
and the headless turn has no tool that could reach one.

    vincent.py --situation            everything, as JSON
    vincent.py --situation --page 1   the same, as text in pages a session reads
    vincent.py --catalog | --schedule
    vincent.py --remember "a fact"
    vincent.py --handoff /van-gogh:week   check a name, print the line to write
    vincent.py --ask "a message" --channel workbench:morning-coffee
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import config_loader
import host
import job_registry
import job_watch
import memory_sync
import run_ledger
import schedule_plan
import scheduler_setup
import setup_state
from platform_compat import fmt_hour_minute

AGENT = "vincent"

# Skills that change an account, the settings or the install itself. Vincent
# names the hand-off and asks before running any of them, and code never runs
# one from a surface with no terminal. Matched on the skill's own name
# whatever its source, so a personal copy of one is held to the same rule.
NEEDS_CONFIRM = frozenset({
    "add-account", "remove-account", "update-settings",
    "update-digest-preferences", "install-van-gogh", "uninstall-van-gogh",
    "migrate-from-legacy-van-gogh", "update-van-gogh", "add-connector",
})

# The only skills code will run from a headless turn: each one reads, renders
# and at most leaves a draft. Filing, syncing, repairs and anything in
# NEEDS_CONFIRM stay with a session a person is sitting in.
HEADLESS_RUNNABLE = ("morning-coffee", "afternoon-tea", "week", "week-retro",
                     "meeting-prep", "note", "suggest", "follow-up-radar",
                     "relationship-radar")

# One headless turn, and one skill run from it. Both are ceilings: a chat that
# never answers is worse than one that says it ran out of time.
VINCENT_TIMEOUT_S = 240
SKILL_RUN_TIMEOUT_S = 15 * 60
# The longest reply a chat surface is handed, skill output included.
REPLY_CAP = 6000

# A scheduled job that is not named after its skill. Every other job key is
# the skill's name, or `digest-` in front of it. The watcher has no skill.
# `tests/test_vincent.py` fails when a new job maps to nothing, so a job
# cannot be added without deciding which skill a person would ask for.
JOB_SKILL = {"finance-email": "finance-brief", "prep-email": "meeting-prep"}
JOBS_WITHOUT_A_SKILL = frozenset({"job-watch"})

# Ledger names for the two rows that declare none because nothing grades them.
_LEDGER_FOR = {"job-watch": "watch.tick"}

MEMORY_BEGIN = "<!-- van-gogh:vincent-memory:begin -->"
MEMORY_END = "<!-- van-gogh:vincent-memory:end -->"
MEMORY_HEADING = "## Remembered for Vincent"
FACT_CAP = 500

# How much of each memory source one situation carries.
_MEMORY_TEXT_CAP = 40_000
_PROJECT_FILE_CAP = 8_000
_PROJECT_FILES_MAX = 40

# A page a session reads through its shell tool, which cuts long output.
PAGE_BUDGET = 20_000

_US_ZONE = {"PST": "PT", "PDT": "PT", "MST": "MT", "MDT": "MT",
            "CST": "CT", "CDT": "CT", "EST": "ET", "EDT": "ET",
            "AKST": "AKT", "AKDT": "AKT", "HST": "HT"}


def plugin_root() -> Path:
    """Where this file lives, which is the plugin root (see `repair`)."""
    return Path(__file__).resolve().parent.parent


# ── Frontmatter ──────────────────────────────────────────────────────────────

_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*):(?:[ \t]+(.*))?$")


def frontmatter(text: str) -> dict:
    """The top-level string fields of a skill or agent file's frontmatter.

    Hand-parsed because the product ships no YAML library, and written for the
    shapes these files really use: a plain value, a quoted value, a value
    continued on indented lines, and a block (`>`, `>-`, `|`). Lists and
    nested maps are skipped. A file with no frontmatter answers {}.
    """
    if text.startswith(chr(0xFEFF)):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}
    body = lines[1:end]
    out = {}
    i = 0
    while i < len(body):
        m = _KEY_RE.match(body[i])
        i += 1
        if not m:
            continue
        key, value = m.group(1), (m.group(2) or "").strip()
        block = []
        while i < len(body) and (not body[i].strip() or body[i][:1] in " \t"):
            block.append(body[i])
            i += 1
        if value[:1] in (">", "|") and len(value) <= 2:
            out[key] = _block_scalar(block, folded=value[0] == ">")
        elif value[:1] in ('"', "'"):
            out[key] = _unquote(" ".join([value] + [b.strip() for b in block
                                                    if b.strip()]))
        elif value:
            # A plain value may run on over indented lines; a comment may end it.
            joined = " ".join([value] + [b.strip() for b in block if b.strip()])
            out[key] = re.sub(r"\s+#.*$", "", joined).strip()
        # An empty value followed by indented lines is a list or a map: skipped.
    return out


def _block_scalar(lines: list, folded: bool) -> str:
    indents = [len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()]
    cut = min(indents) if indents else 0
    stripped = [ln[cut:].rstrip() if ln.strip() else "" for ln in lines]
    if not folded:
        return "\n".join(stripped).strip()
    out, para = [], []
    for ln in stripped:
        if ln:
            para.append(ln.strip())
        elif para:
            out.append(" ".join(para))
            para = []
    if para:
        out.append(" ".join(para))
    return "\n".join(out).strip()


def _unquote(value: str) -> str:
    quote = value[0]
    end = value.rfind(quote)
    inner = value[1:end] if end > 0 else value[1:]
    if quote == "'":
        return inner.replace("''", "'")
    return (inner.replace('\\"', '"').replace("\\n", "\n")
            .replace("\\\\", "\\"))


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


# ── The catalog ──────────────────────────────────────────────────────────────

def _skill_rows(root: Path, source: str, prefix: str) -> list:
    """One row per `<root>/skills/<name>/SKILL.md`."""
    rows = []
    try:
        dirs = sorted(p for p in (root / "skills").iterdir() if p.is_dir())
    except OSError:
        return rows
    for d in dirs:
        text = _read(d / "SKILL.md")
        if not text:
            continue
        fm = frontmatter(text)
        rows.append({"kind": "skill", "name": d.name,
                     "invoke": f"/{prefix}{d.name}", "source": source,
                     "description": fm.get("description", "")})
    return rows


def _command_rows(root: Path, source: str, prefix: str) -> list:
    """One row per `<root>/commands/<name>.md`, the older form of a skill."""
    rows = []
    try:
        files = sorted(p for p in (root / "commands").iterdir()
                       if p.is_file() and p.suffix == ".md")
    except OSError:
        return rows
    for f in files:
        fm = frontmatter(_read(f))
        rows.append({"kind": "skill", "name": f.stem,
                     "invoke": f"/{prefix}{f.stem}", "source": source,
                     "description": fm.get("description", "")})
    return rows


def _agent_rows(root: Path, source: str, prefix: str) -> list:
    """One row per `<root>/agents/<name>.md`."""
    rows = []
    try:
        files = sorted(p for p in (root / "agents").iterdir()
                       if p.is_file() and p.suffix == ".md")
    except OSError:
        return rows
    for f in files:
        fm = frontmatter(_read(f))
        if not fm:
            continue
        name = fm.get("name") or f.stem
        rows.append({"kind": "agent", "name": name, "invoke": f"{prefix}{name}",
                     "source": source,
                     "description": fm.get("description", "")})
    return rows


def _own_plugin_name() -> str:
    try:
        data = json.loads(_read(plugin_root() / ".claude-plugin" / "plugin.json"))
        return str(data.get("name") or "van-gogh")
    except (ValueError, TypeError):
        return "van-gogh"


def _installed_plugin_rows(own: str) -> list:
    """Skills and agents of every other plugin installed for this user.

    A plugin recorded as installed whose folder is gone is reported as
    missing. Reporting it as a plugin with no skills would be a confident
    wrong answer to "can anything here do X".
    """
    rows = []
    for name, key, root in host.other_plugin_roots(own):
        if str(root) in ("", ".") or not root.is_dir():
            rows.append({"kind": "plugin", "name": name, "invoke": "",
                         "source": key, "description": "",
                         "missing": True})
            continue
        rows += _skill_rows(root, key, f"{name}:")
        rows += _command_rows(root, key, f"{name}:")
        rows += _agent_rows(root, key, f"{name}:")
    return rows


def skill_cadence() -> dict:
    """{skill name: "daily 04:30, on"} for every skill a scheduled job runs."""
    out = {}
    try:
        jobs = job_registry.jobs()
    except Exception:                                            # noqa: BLE001
        return out
    for job in jobs:
        skill = skill_of_job(job.key)
        if not skill:
            continue
        try:
            words = job.resolved_cadence().describe()
            on = job.is_enabled()
        except Exception:                                        # noqa: BLE001
            continue
        out[skill] = f"{words}, {'on' if on else 'off'}"
    return out


def skill_of_job(key: str) -> str:
    """The skill a person would ask for to get what this job produces."""
    if key in JOBS_WITHOUT_A_SKILL:
        return ""
    if key in JOB_SKILL:
        return JOB_SKILL[key]
    return key[len("digest-"):] if key.startswith("digest-") else key


def catalog() -> list:
    """Every skill and agent this machine has, read off disk now.

    Order is the order Vincent should prefer them in: Van Gogh's own skills
    and agents, the user's own agents, other installed plugins, then the
    user's own skills. Two skills with the same name from different places
    are both kept; their `invoke` forms differ, which is what tells them
    apart.
    """
    own = _own_plugin_name()
    root = plugin_root()
    home = host.personal_skills_dir().parent
    rows = (_skill_rows(root, own, f"{own}:")
            + _agent_rows(root, own, "")
            + _agent_rows(home, "personal", "")
            + _installed_plugin_rows(own)
            + _skill_rows(home, "personal", "")
            + _command_rows(home, "personal", ""))
    cadence = skill_cadence()
    for row in rows:
        row["needs_confirm"] = (row["kind"] == "skill"
                                and row["name"] in NEEDS_CONFIRM)
        if row["kind"] == "skill" and row["source"] == own:
            row["cadence"] = cadence.get(row["name"], "")
    return rows


# ── The schedule ─────────────────────────────────────────────────────────────

def _zone(dt: datetime) -> str:
    name = dt.strftime("%Z")
    key = getattr(dt.tzinfo, "key", "") or ""
    if key.startswith(("America/", "US/", "Pacific/Honolulu")):
        return _US_ZONE.get(name, name)
    return name


def when(stamp, now: datetime | None = None) -> str:
    """`Sat Oct 10, 4:40 AM PT / 7:40 AM ET` from a ledger stamp, or "".

    The ledger writes the machine's local time with no zone on it. It is read
    as local, then shown in the zones the user set, both when there are two.
    Written by code so a time is never a model's arithmetic.
    """
    if isinstance(stamp, datetime):
        dt = stamp
    else:
        try:
            dt = datetime.fromisoformat(str(stamp))
        except (TypeError, ValueError):
            return ""
    try:
        aware = dt.astimezone()
        first = aware.astimezone(config_loader.user_tz())
        text = (f"{first.strftime('%a %b')} {first.day}, "
                f"{fmt_hour_minute(first)} {_zone(first)}")
        second_tz = config_loader.user_secondary_tz()
        if second_tz is not None:
            second = aware.astimezone(second_tz)
            text += f" / {fmt_hour_minute(second)} {_zone(second)}"
        return text
    except Exception:                                            # noqa: BLE001
        return ""


def _ledger() -> tuple:
    """(every run row, "") or (None, why the ledger could not be read).

    `run_ledger.read_runs` answers an unreadable file with an empty list,
    which is right for a grader that must never stop and wrong here: "no job
    has ever run" and "the record could not be opened" are different answers
    and only one of them is safe to say out loud. A file that does not exist
    yet is a new install, not a fault.
    """
    path = run_ledger.runs_path()
    try:
        path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], ""
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"the record of runs could not be read ({type(exc).__name__})"
    return run_ledger.read_runs(path), ""


def _first_line(text, cap: int = 200) -> str:
    """The first non-empty line, with anything credential-shaped replaced."""
    for line in str(text or "").splitlines():
        line = line.strip()
        if line:
            return memory_sync.redact_secrets(line[:cap])[0]
    return ""


def _what_happened(row: dict) -> str:
    """A predicate for a job that did not deliver, in the reader's words."""
    status = row["status"]
    if status == "failed":
        detail = _first_line(row.get("detail"))
        at = f" at {row['last_run_when']}" if row.get("last_run_when") else ""
        return f"failed{at}" + (f". What it recorded: {detail}" if detail else "")
    if status == "missing":
        return "did not run when it was due"
    if status == "no_deliverable":
        return "ran, but the page it writes is not from today"
    return ""


def _sentence(row: dict) -> str:
    """One row that needs attention, as a sentence that says whose move it is."""
    title = row["title"]
    lead = f"{title[:1].upper()}{title[1:]}"
    what = _what_happened(row)
    hold = row.get("hold", "")
    if what and hold:
        return f"{lead} {what}. It {hold}"
    if hold:
        return f"{lead} {hold}"
    if what:
        return (f"{lead} {what}. The check that restarts failed jobs runs it "
                "again within half an hour. Nothing for you to do yet.")
    return ""


def schedule(now: datetime | None = None, runs: list | None = None,
             job_rows: list | None = None) -> dict:
    """One row per scheduled job, and whether the record could be read.

    Reads only. The hold sentence comes from `job_watch.decide`, the same
    function the watcher's own tick uses, run against a COPY of the watcher's
    state so nothing it notes along the way is ever saved. No job is started
    from here: starting one is `job_watch.kick`, which this never calls.

    `runs` and `job_rows` are injectable for the same reason `evaluate`'s are:
    a planted ledger and a planted scheduler, with no clock and no launchd.
    """
    now = now or datetime.now()
    out = {"read_ok": True, "error": "", "rows": [], "needs_attention": []}
    try:
        if runs is None:
            runs, error = _ledger()
            if runs is None:
                return {"read_ok": False, "error": error, "rows": [],
                        "needs_attention": None}
        if job_rows is None:
            try:
                job_rows = scheduler_setup.job_states(sys.platform)
            except Exception:                                    # noqa: BLE001
                job_rows = []
        held_by = {r.get("name", ""): r.get("state", "") for r in job_rows}

        since = (now - timedelta(days=job_watch.LOOKBACK_DAYS)).date()
        recent = [r for r in runs
                  if str(r.get("started", ""))[:10] >= since.isoformat()]
        state = copy.deepcopy(job_watch._read_state())
        graded = {r["job"]: r for r in job_watch.evaluate(
            now=now, state=state, runs=recent, job_rows=job_rows)}
        job_watch._PROBE_CACHE.clear()
        declined = job_watch._declined()

        last = {}
        for r in runs:
            last[r.get("job", "")] = r

        for job in job_registry.jobs():
            try:
                on = job.is_enabled()
                cadence = job.resolved_cadence().describe()
            except Exception:                                    # noqa: BLE001
                on, cadence = False, ""
            run = last.get(job.ledger_job or _LEDGER_FOR.get(job.key, ""), {})
            started = str(run.get("started", "") or "")
            row = {"key": job.key, "title": job.title, "cadence": cadence,
                   "on": on, "scheduler": held_by.get(job.key, ""),
                   "skill": skill_of_job(job.key),
                   "last_run": started, "last_run_when": when(started),
                   "last_rc": run.get("rc") if run else None,
                   "status": "off" if not on else "not graded",
                   "detail": "", "hold": "", "needs_reader": False,
                   "watcher_restarts_it": False}
            g = graded.get(job.key)
            if g is not None:
                row["status"] = g["status"]
                row["detail"] = _first_line(g.get("detail"))
                can, why = job_watch.decide(g, state, now, declined)
                row["hold"] = why
                row["watcher_restarts_it"] = bool(can)
                row["needs_reader"] = bool(why) and job_watch._needs_the_reader(why)
            elif job.key == "job-watch" and on:
                row.update(_watcher_row(run, now))
            out["rows"].append(row)

        for row in out["rows"]:
            if row["status"] in job_watch.ACTIONABLE or row["needs_reader"]:
                out["needs_attention"].append(
                    {"key": row["key"], "title": row["title"],
                     "status": row["status"], "sentence": _sentence(row)})
    except Exception as exc:                                     # noqa: BLE001
        return {"read_ok": False,
                "error": f"the schedule could not be read ({type(exc).__name__})",
                "rows": [], "needs_attention": None}
    return out


def _watcher_row(run: dict, now: datetime) -> dict:
    """The watcher grades everything else, so nothing grades it. This does.

    Its only evidence is its own last tick. Past `STALE_TICK_H` it has stopped,
    and that is the one fault nothing else on the machine would ever report.
    """
    try:
        last_tick = datetime.fromisoformat(str(run.get("started", "")))
    except (TypeError, ValueError):
        last_tick = None
    if last_tick and (now - last_tick) <= timedelta(hours=job_watch.STALE_TICK_H):
        return {"status": "ok"}
    return {"status": "stopped", "needs_reader": True,
            "hold": ("has itself stopped, so if a job fails now, nothing "
                     f"restarts it. Running {host.cmd('install-van-gogh')} again "
                     "sets it back up.")}


# ── What is set up, and what is not ──────────────────────────────────────────

_BOX_WORDS = {setup_state.DONE: "done", setup_state.OPEN: "open",
              setup_state.PENDING: "in progress", setup_state.SKIPPED: "skipped by choice",
              setup_state.NOT_APPLICABLE: "not needed here"}
SETUP_LINE = ("paste the setup line from your checklist in Terminal "
              "(PowerShell on Windows)")


def _box_rows(data: dict) -> list:
    action = {b[0]: b[3] for b in setup_state.BOXES}
    return [{"key": b["key"], "title": b["title"], "status": b["status"],
             "words": _BOX_WORDS.get(b["status"], b["status"]),
             "needs": b.get("needs", ""), "detail": str(b.get("detail") or ""),
             "action": action.get(b["key"], "")} for b in data["boxes"]]


def _setup_state() -> dict:
    """The nine setup steps as they stand, read from disk and never saved.

    A checklist on disk means setup is not finished, and it is the truth: the
    product reopens a step there by itself when a sign-in stops working or a
    job disappears. With no checklist, the completion stamp says setup
    finished and what was skipped. With neither, nothing was ever set up.

    The checklist is reported as it is stored and is not re-checked here. A
    re-check from this side can only use the quick local tests, and those
    know less than what wrote the file: a sign-in the provider rejected an
    hour ago still has its token on disk, so a local look would tick it done
    and hide the one thing the person has to act on.
    """
    try:
        data = setup_state.load()
    except Exception as exc:                                     # noqa: BLE001
        return {"state": "unknown", "boxes": [], "open": [], "next": "",
                "error": f"the setup record could not be read ({type(exc).__name__})"}
    if data is not None:
        rows = _box_rows(data)
        open_rows = [r for r in rows if r["status"] not in setup_state.RESOLVED]
        if open_rows:
            first = open_rows[0]
            return {"state": "incomplete", "boxes": rows,
                    "open": [r["title"] for r in open_rows],
                    "next": first["action"],
                    "before_claude": first["key"] in ("plugin", "python"),
                    "error": ""}
        return {"state": "finishing", "boxes": rows, "open": [], "next": "", "error": ""}
    stamp = setup_state.read_stamp()
    if stamp is not None:
        data = setup_state.new(stamp.get("run_id", ""), sys.platform,
                               stamp.get("plugin_version", ""))
        for b in data["boxes"]:
            status = (stamp.get("boxes") or {}).get(b["key"], setup_state.DONE)
            b["status"] = status if status in setup_state.STATUSES else setup_state.DONE
        return {"state": "complete", "boxes": _box_rows(data), "open": [], "next": "",
                "finished": str(stamp.get("finished") or stamp.get("when") or ""),
                "error": ""}
    if _vault_config() is not None:
        # A working vault with no record either way is an install from before
        # setup kept one. It is in use, so it is not "never started"; the
        # product writes its record the next time any command runs.
        return {"state": "complete", "boxes": [], "open": [], "next": "",
                "unrecorded": True, "error": ""}
    return {"state": "not_started", "boxes": [], "open": [], "next": "", "error": ""}


def _vault_config() -> dict | None:
    """The vault's config, or None before setup has made a vault."""
    try:
        cfg = config_loader.cfg()
    except Exception:                                            # noqa: BLE001
        return None
    return cfg if isinstance(cfg, dict) and cfg else None


def install() -> dict:
    """What is set up on this computer and what is not. Reads only.

    Three things a person means by "installed": whether setup itself
    finished, which accounts and services are connected, and which of the
    jobs that can run by themselves are switched on. Each is read on its own
    and fails on its own, so a machine with no vault yet still gets an answer
    about setup.
    """
    out = {"setup": _setup_state(), "has_vault": False, "accounts": [],
           "notetaker": "", "runs_by_itself": [], "switched_off": [],
           "daily_suggestions": False, "connectors": []}
    cfg = _vault_config()
    if cfg is None:
        return out
    out["has_vault"] = True
    try:
        out["accounts"] = [{"label": str(a.get("label") or ""),
                            "email": str(a.get("email") or ""),
                            "provider": str(a.get("provider") or a.get("type") or "")}
                           for a in config_loader.accounts()]
    except Exception:                                            # noqa: BLE001
        pass
    try:
        import notetaker
        out["notetaker"] = notetaker.safe_display_name() if notetaker.configured() else ""
    except Exception:                                            # noqa: BLE001
        pass
    try:
        rows = schedule_plan.table(cfg)
        live = schedule_plan.any_on(rows)
        for r in rows:
            on = r["on"] and (live or not r["fixed"])
            (out["runs_by_itself"] if on else out["switched_off"]).append(
                {"job": r["key"], "name": r["name"], "when": r["when"], "what": r["what"]})
    except Exception:                                            # noqa: BLE001
        pass
    try:
        out["daily_suggestions"] = bool(config_loader.advisor_enabled())
        out["connectors"] = [str(c.get("name") or "") for c in config_loader.connectors()]
    except Exception:                                            # noqa: BLE001
        pass
    return out


def _setup_sentence(inst: dict) -> str:
    """The sentence that leads the opening while setup is not finished."""
    setup = inst["setup"]
    if setup["state"] == "not_started":
        return ("Van Gogh is not set up on this computer yet. I can take you "
                "through setup now.")
    if setup["state"] != "incomplete":
        return ""
    n, total = len(setup["open"]), len(setup_state.KEYS)
    steps = "step is" if n == 1 else "steps are"
    if setup.get("before_claude"):
        return (f"Setup is not finished: {n} of {total} {steps} open. The next one "
                f"happens outside {host.host_word()}: {SETUP_LINE}.")
    return (f"Setup is not finished: {n} of {total} {steps} open. Next: "
            f"{setup['next']}. I can take you through it now.")


def setup_rules() -> str:
    """How to run setup, with every path filled in.

    The rules are the install skill's own page, so there is one copy of them.
    That skill cannot be started by a model and its commands name the plugin
    folder through a variable an agent session does not have, so the page is
    printed here with the folder written out.
    """
    root = plugin_root()
    text = _read(root / "skills" / "install-van-gogh" / "SKILL.md")
    if not text:
        return ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        text = text[end + 4:] if end != -1 else text
    # The page's Codex first-chat paragraph is about a chat whose plugin
    # variable is not set yet; these rules write every path out, so it is
    # dropped here.
    start = text.find("<!-- codex-first-chat -->")
    stop = text.find("<!-- /codex-first-chat -->")
    if start != -1 and stop > start:
        text = text[:start] + text[stop + len("<!-- /codex-first-chat -->\n"):]
    posix = root.as_posix()
    text = text.replace("${CLAUDE_PLUGIN_ROOT}", posix)
    text = text.replace("$env:CLAUDE_PLUGIN_ROOT", str(root))
    text = text.replace("](references/", f"]({posix}/skills/install-van-gogh/references/")
    head = ("SETUP RULES, printed by code. You run setup yourself by following "
            "this page exactly. Write no hand-off line for it. When the script "
            "prints `done`, print its closing text as given, then say in one "
            "line that you are here if they want something now.\n")
    return head + text.lstrip("\n")


def opening(sched: dict, inst: dict | None = None) -> str:
    """The first thing Vincent says: what did not run, or that everything did.

    Built here because it carries counts and names, and a count is code's to
    state. What needs the reader comes first, exactly as the briefing footer
    orders it.
    """
    lead = _setup_sentence(inst) if inst else ""
    if lead and not inst.get("has_vault"):
        # No vault yet means no job has ever been scheduled: there is nothing
        # else to report, and "the record could not be read" would be noise.
        return lead
    if lead and not _jobs_were_scheduled(inst):
        # Setup has not reached its schedule step, so no job was ever due.
        # "It did not run" about a job nobody has switched on is a false alarm
        # in the first sentence a new person reads.
        return lead
    rest = _jobs_sentence(sched)
    return f"{lead} {rest}".strip()


def _jobs_were_scheduled(inst: dict) -> bool:
    for b in inst["setup"]["boxes"]:
        if b["key"] == "jobs":
            return b["status"] in setup_state.RESOLVED
    return True


def _jobs_sentence(sched: dict) -> str:
    if not sched.get("read_ok"):
        return (f"I cannot tell you whether your scheduled jobs ran: "
                f"{sched.get('error') or 'the record could not be read'}.")
    rows = sched["rows"]
    attention = sched.get("needs_attention") or []
    if attention:
        keyed = {r["key"]: r for r in rows}
        ordered = sorted(attention,
                         key=lambda a: not keyed.get(a["key"], {}).get("needs_reader"))
        return " ".join(a["sentence"] for a in ordered if a["sentence"])
    on = [r for r in rows if r["on"] and r["status"] != "not graded"]
    if not on:
        return "No scheduled job is switched on, so nothing was due."
    waiting = [r for r in on if r["status"] in ("pending", "running")]
    done = len(on) - len(waiting)
    line = (f"Everything scheduled has run: {done} of the {len(on)} jobs that "
            "are switched on are up to date")
    if waiting:
        line += f", and {len(waiting)} {'is' if len(waiting) == 1 else 'are'} running or just due"
    return line + "."


# ── Memory ───────────────────────────────────────────────────────────────────

def _redacted(text: str) -> tuple:
    return memory_sync.redact_secrets(text or "")


def remembered(text: str) -> list:
    """The dated lines inside this module's block of the memory page."""
    if MEMORY_BEGIN not in text or MEMORY_END not in text:
        return []
    block = text.split(MEMORY_BEGIN, 1)[1].split(MEMORY_END, 1)[0]
    return [ln.strip()[2:].strip() for ln in block.splitlines()
            if ln.strip().startswith("- ")]


def memory(today=None) -> dict:
    """Everything Vincent may know from before this conversation.

    Three sources, each failing on its own: the workspace memory page, the
    follow-ups ledger inside it (through the radar's own parser, so there is
    one definition of overdue), and Claude Code's memory folders for this
    vault and this plugin. That last read does not wait for the mirror switch
    (`memory_sync.candidate_memory_dirs`): nothing is copied anywhere.
    """
    today = today or datetime.now().date()
    out = {"path": "", "text": "", "remembered": [], "follow_ups": [],
           "project_memory": [], "redactions": 0, "errors": []}
    try:
        path = config_loader.vangogh_memory_path()
        out["path"] = str(path)
        raw = path.read_text(encoding="utf-8") if path.exists() else ""
        out["remembered"] = [_redacted(r)[0] for r in remembered(raw)]
        text, n = _redacted(raw[:_MEMORY_TEXT_CAP])
        out["text"], out["redactions"] = text, n
    except Exception as exc:                                     # noqa: BLE001
        raw = ""
        out["errors"].append(f"workspace memory: {type(exc).__name__}")
    try:
        import follow_up_radar
        ledger_path = config_loader.follow_ups_memory_path()
        ledger = raw if str(ledger_path) == out["path"] else _read(ledger_path)
        items = follow_up_radar.parse_follow_ups(
            ledger, today, config_loader.follow_ups_horizon_days())
        out["follow_ups"] = [
            {"title": _redacted(str(it.get("title", "")))[0],
             "status": it.get("status", ""),
             "due": str(it.get("due") or it.get("earliest_trigger") or "")}
            for it in items if not it.get("done")]
    except Exception as exc:                                     # noqa: BLE001
        out["errors"].append(f"follow-ups: {type(exc).__name__}")
    try:
        for folder in memory_sync.candidate_memory_dirs():
            files = []
            names = sorted(p for p in folder.iterdir()
                           if p.is_file() and p.suffix == ".md")
            names.sort(key=lambda p: p.name != "MEMORY.md")
            for p in names[:_PROJECT_FILES_MAX]:
                text, n = _redacted(_read(p)[:_PROJECT_FILE_CAP])
                out["redactions"] += n
                files.append({"name": p.name, "text": text})
            out["project_memory"].append({"dir": str(folder), "files": files})
    except Exception as exc:                                     # noqa: BLE001
        out["errors"].append(f"chat memory: {type(exc).__name__}")
    return out


def _normal(fact: str) -> str:
    return re.sub(r"\s+", " ", fact).strip().rstrip(".").casefold()


def remember(fact: str, today=None) -> dict:
    """Write one dated line into this module's block of the memory page.

    The same fact twice leaves one line, whatever its case or spacing. A
    credential-shaped string is replaced before it is written, because this
    page is loaded into every session opened in the vault. Dashes used as
    punctuation become commas, as everywhere else in the product. Only the
    block between the two markers is ever touched.
    """
    today = today or datetime.now().date()
    fact = re.sub(r"\s+", " ", str(fact or "")).strip()
    fact = fact.replace(chr(0x2014), ",").replace(chr(0x2013), "-")
    fact, redactions = _redacted(fact)
    fact = fact[:FACT_CAP].strip()
    if not fact:
        return {"ok": False, "error": "there was nothing to remember"}
    path = config_loader.vangogh_memory_path()
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        known = {_normal(re.sub(r"^\d{4}-\d{2}-\d{2}:\s*", "", r))
                 for r in remembered(text)}
        if _normal(fact) in known:
            return {"ok": True, "added": False, "fact": fact, "path": str(path),
                    "redactions": redactions}
        line = f"- {today.isoformat()}: {fact}"
        if MEMORY_BEGIN in text and MEMORY_END in text:
            head, rest = text.split(MEMORY_BEGIN, 1)
            block, tail = rest.split(MEMORY_END, 1)
            new = (head + MEMORY_BEGIN + block.rstrip() + "\n" + line
                   + "\n" + MEMORY_END + tail)
        else:
            block = f"{MEMORY_BEGIN}\n{MEMORY_HEADING}\n\n{line}\n{MEMORY_END}"
            # Above the follow-ups ledger, never inside it: that heading is
            # parsed by the radar, which would read these lines as entries.
            marker = "## Pending follow-ups"
            if marker in text:
                head, tail = text.split(marker, 1)
                new = head.rstrip() + "\n\n" + block + "\n\n" + marker + tail
            else:
                new = (text.rstrip() + "\n\n" if text.strip() else "") + block + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
    except Exception as exc:                                     # noqa: BLE001
        return {"ok": False, "error": f"could not write the memory page "
                                      f"({type(exc).__name__})"}
    return {"ok": True, "added": True, "fact": fact, "path": str(path),
            "redactions": redactions}


# ── The situation ────────────────────────────────────────────────────────────

def _waiting_on_reader(now: datetime) -> dict:
    """Suggestions awaiting a yes and work that stopped moving. Fails open."""
    out = {"suggestions": [], "stalled": []}
    try:
        import advisor
        out["suggestions"] = [str(t.get("title", "")) for t in advisor.waiting()]
    except Exception:                                            # noqa: BLE001
        pass
    try:
        import task_stall
        out["stalled"] = [str(r.get("title") or r.get("text") or "")
                          for r in task_stall.stalled(now)][:10]
    except Exception:                                            # noqa: BLE001
        pass
    return out


def situation(now: datetime | None = None) -> dict:
    """Everything Vincent knows, gathered by code with no model in the path.

    Four blocks: the catalog, the schedule, memory and what needs attention.
    `needs_attention` is a list when the schedule was read (empty on a quiet
    day) and null when it could not be, with `schedule_error` naming why, so
    "nothing is wrong" is never said about a record nobody could open.
    """
    now = now or datetime.now()
    sched = schedule(now)
    inst = install()
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "generated_when": when(now) or now.strftime("%Y-%m-%d %H:%M"),
        "opening": opening(sched, inst),
        "install": inst,
        "needs_attention": sched["needs_attention"],
        "schedule_read_ok": sched["read_ok"],
        "schedule_error": sched["error"],
        "schedule": sched["rows"],
        "waiting_on_you": _waiting_on_reader(now),
        "memory": memory(now.date()),
        "catalog": catalog(),
    }


# ── The same thing as pages of text ──────────────────────────────────────────

def _one_line(text: str, cap: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= cap else text[:cap - 3].rstrip() + "..."


def _catalog_line(row: dict, cap: int) -> str:
    if row.get("missing"):
        return (f"- plugin {row['name']} ({row['source']}): recorded as "
                "installed but its folder is missing")
    tags = [row["kind"]]
    if row.get("needs_confirm"):
        tags.append("ASK FIRST")
    if row.get("cadence"):
        tags.append(f"scheduled {row['cadence']}")
    return (f"- {row['invoke']} [{', '.join(tags)}]: "
            f"{_one_line(row['description'], cap)}")


def _schedule_line(row: dict) -> str:
    parts = [f"{'on' if row['on'] else 'off'}", row["cadence"]]
    if row["scheduler"]:
        parts.append(f"scheduler: {row['scheduler']}")
    if row["on"]:
        parts.append(f"today: {row['status']}")
    if row["last_run"]:
        parts.append(f"last ran {row['last_run_when']} ({row['last_run']})")
    else:
        parts.append("no run on record")
    line = f"- {row['title']} [{row['key']}]: " + "; ".join(p for p in parts if p)
    if row["hold"]:
        line += f". It {row['hold']}"
    return line


def _commands_line() -> str:
    """The plugin folder, spelled out, for every later command.

    The agent file finds the folder by reading a pointer file inside the
    command, which a permission check treats as a command it cannot see
    into and asks about. Once the folder is known it can be written out, and
    a command with nothing hidden in it runs without a question.
    """
    return ("COMMANDS ON THIS COMPUTER: the plugin folder is "
            f"{plugin_root().as_posix()} . From here on, write that folder out "
            "in every command in place of the part that reads it from the "
            "plugin-root file. A command written that way is not asked about.")


SETUP_MESSAGE = "Set up Van Gogh on this computer."


def setup_brief() -> str:
    """What the setup line hands Vincent before the first message.

    The situation and the setup rules, gathered here so the session that
    opens does not spend its first two turns fetching them, and so nothing
    it runs first needs a permission question on a machine that has never
    allowed one.
    """
    sit = situation()
    state = sit["install"]["setup"]["state"]
    parts = []
    if host.is_codex():
        # Codex has no --agent: the agent file is the first thing in the
        # brief, so the session opens with Vincent's own rules.
        parts += ["--- WHO YOU ARE (agents/vincent.md) ---",
                  host.agent_prompt("vincent"),
                  "--- END OF WHO YOU ARE ---", ""]
    parts += ["--- THE SITUATION, GATHERED BY CODE JUST NOW ---",
              pages(sit)[0],
              "--- END OF THE SITUATION ---", ""]
    if state in ("incomplete", "not_started", "finishing") and \
            not sit["install"]["setup"].get("before_claude"):
        parts += [
            "This session was opened by the setup line the person pasted, so "
            "they have already asked to be set up. Do not run the situation "
            "command and do not print the setup rules again: both are here. "
            "Say in one short sentence that you are Vincent and that you are "
            "starting setup, then start it.", "",
            setup_rules()]
    else:
        parts += [
            "This session was opened by the setup line the person pasted. "
            "Do not run the situation command: it is above. Open as your "
            "rules say, with what SETUP reports, and ask what they need."]
    return "\n".join(parts) + "\n"


def _install_lines(inst: dict | None) -> list:
    """SETUP and WHAT IS SET UP, as the lines page 1 carries."""
    if not inst:
        return ["SETUP: unknown"]
    setup = inst["setup"]
    state = setup["state"]
    total = len(setup_state.KEYS)
    if state == "incomplete":
        out = [f"SETUP: not finished ({len(setup['open'])} of {total} steps open)"]
    elif state == "complete":
        out = ["SETUP: finished" + (", before setup kept a record of its steps"
                                    if setup.get("unrecorded") else "")]
    elif state == "finishing":
        out = ["SETUP: every step is resolved and the finish has not been written yet"]
    elif state == "not_started":
        out = ["SETUP: never started on this computer"]
    else:
        out = [f"SETUP: unknown. {setup.get('error', '')}"]
    for b in setup["boxes"]:
        detail = f" ({_one_line(b['detail'], 80)})" if b["detail"] and \
            b["status"] != setup_state.DONE else ""
        out.append(f"- [{b['words']}] {b['title']}{detail}")
    if state in ("incomplete", "not_started", "finishing"):
        if setup.get("before_claude"):
            out.append(f"The next step happens outside {host.host_word()}: {SETUP_LINE}. "
                       "You cannot run it from here.")
        else:
            out.append("You run setup yourself. The person asking to set up, or "
                       "saying yes to your offer, is the go-ahead. Print the "
                       "rules with `vincent.py --setup-rules` and follow them. "
                       "No hand-off line.")
    out.append("")
    if not inst["has_vault"]:
        out.append("WHAT IS SET UP: nothing yet. There is no vault on this computer.")
        return out
    out.append("WHAT IS SET UP")
    accounts = inst["accounts"]
    out.append(f"- accounts ({len(accounts)}): " + (", ".join(
        f"{a['label'] or a['provider']} {a['email']}".strip() for a in accounts) or "none"))
    out.append(f"- notetaker: {inst['notetaker'] or 'none connected'}")
    out.append(f"- daily suggestions: {'on' if inst['daily_suggestions'] else 'off'}")
    out.append("- connectors: " + (", ".join(inst["connectors"]) or "none"))
    on, off = inst["runs_by_itself"], inst["switched_off"]
    out.append(f"- runs by itself ({len(on)}):")
    out += [f"  - {r['name']} [{r['job']}]: {r['when']}" for r in on] or ["  - nothing"]
    out.append(f"- available and switched off ({len(off)}):")
    out += [f"  - {r['name']} [{r['job']}]: {r['what']}; would run {r['when']}"
            for r in off] or ["  - nothing"]
    return out


def pages(sit: dict) -> list:
    """The situation as text, cut into pages a session can read in one go.

    Page 1 always holds what a first reply needs: the opening, what needs
    attention, the schedule, the follow-ups, what was remembered, and Van
    Gogh's own skills and agents. The rest of memory and every other skill on
    the machine follow. A session's shell tool cuts long output, which is why
    this is paged at all: a catalog that arrives truncated is a catalog with
    skills silently missing from it.
    """
    own = _own_plugin_name()
    cat = sit["catalog"]
    mine = [r for r in cat if r["source"] == own]
    others = [r for r in cat if r["source"] != own]
    mem = sit["memory"]
    attention = sit["needs_attention"]

    first = [f"OPENING\n{sit['opening']}", ""]
    first += _install_lines(sit.get("install")) + [""]
    first += [_commands_line(), ""]
    inst = sit.get("install") or {}
    has_vault = inst.get("has_vault", True)
    unscheduled = bool(inst) and inst["setup"]["state"] == "incomplete" \
        and not _jobs_were_scheduled(inst)
    if not has_vault:
        first += ["NEEDS ATTENTION: setup. Nothing else can run until it is done.", ""]
    elif unscheduled:
        # Before setup's schedule step nothing was ever due. A row that reads
        # "did not run" here is about a job nobody has switched on.
        first += ["NEEDS ATTENTION: setup. Nothing has been scheduled yet, so no "
                  "job was due and none has failed.", ""]
    elif attention is None:
        first += [f"NEEDS ATTENTION: unknown. {sit['schedule_error']}", ""]
    elif attention:
        first += [f"NEEDS ATTENTION ({len(attention)})"]
        first += [f"- {a['sentence']}" for a in attention] + [""]
    else:
        first += ["NEEDS ATTENTION: nothing", ""]
    if unscheduled:
        first += ["SCHEDULE: nothing is scheduled yet. Setup's schedule step "
                  "shows every job and asks.", ""]
    else:
        first += [f"SCHEDULE ({len(sit['schedule'])} jobs)"]
        first += [_schedule_line(r) for r in sit["schedule"]] + [""]
    waiting = sit["waiting_on_you"]
    if waiting["suggestions"] or waiting["stalled"]:
        first += ["WAITING ON THE READER"]
        first += [f"- suggestion awaiting a yes or a no: {_one_line(t, 140)}"
                  for t in waiting["suggestions"]]
        first += [f"- stopped moving: {_one_line(t, 140)}"
                  for t in waiting["stalled"]]
        first += [""]
    open_items = mem["follow_ups"]
    first += [f"FOLLOW-UPS ({len(open_items)} open)"]
    first += [f"- [{f['status']}] {_one_line(f['title'], 120)}"
              + (f", due {f['due']}" if f["due"] else "")
              for f in open_items[:20]] or ["- none"]
    first += ["", f"REMEMBERED FOR YOU ({len(mem['remembered'])})"]
    first += [f"- {_one_line(r, 300)}" for r in mem["remembered"]] or ["- nothing yet"]
    first += ["", f"VAN GOGH'S OWN SKILLS AND AGENTS ({len(mine)})"]
    first += [_catalog_line(r, 260) for r in mine]

    rest = ["WORKSPACE MEMORY, IN FULL", mem["text"] or "(empty)", ""]
    for folder in mem["project_memory"]:
        rest += [f"CHAT MEMORY FROM {folder['dir']}"]
        for f in folder["files"]:
            rest += [f"--- {f['name']} ---", f["text"], ""]
    rest += [f"EVERY OTHER SKILL AND AGENT ON THIS COMPUTER ({len(others)})"]
    rest += [_catalog_line(r, 400) for r in others]

    out = ["\n".join(first)]
    current, size = [], 0
    for chunk in rest:
        for line in str(chunk).splitlines() or [""]:
            line = line[:2000]
            if size + len(line) + 1 > PAGE_BUDGET and current:
                out.append("\n".join(current))
                current, size = [], 0
            current.append(line)
            size += len(line) + 1
    if current:
        out.append("\n".join(current))

    total = len(out)
    stamped = []
    for i, body in enumerate(out, start=1):
        head = (f"VINCENT SITUATION, gathered by code at {sit['generated_when']}. "
                f"Page {i} of {total}.")
        foot = ("End of the situation." if i == total else
                f"More on page {i + 1} of {total}: the rest of memory and "
                "every other skill on this computer. Read every page before "
                "saying that nothing here can do what was asked.")
        stamped.append(f"{head}\n\n{body}\n\n{foot}")
    return stamped


# ── Hand-offs ────────────────────────────────────────────────────────────────

_HANDOFF_RE = re.compile(r"(?mi)^[ \t>*_`-]*Handing to:[ \t]*(.+?)[ \t]*$")
_SKILL_TARGET_RE = re.compile(r"^/(?:([A-Za-z0-9][\w-]*):)?([A-Za-z0-9][\w-]*)$")
_AGENT_TARGET_RE = re.compile(r"^(?:[A-Za-z0-9][\w-]*:)?[A-Za-z0-9][\w-]*$")


def parse_handoff(text: str):
    """Who a reply handed the work to, or None when it did not say.

    Exactly one `Handing to:` line counts. None, or two that name different
    things, is no hand-off: code acting on an ambiguous line is how the wrong
    skill gets run, and a missed hand-off costs only a second ask. Returns
    `{"kind": "skill" | "agent" | "nobody", "target", "plugin", "skill"}`.
    """
    targets = []
    for raw in _HANDOFF_RE.findall(text or ""):
        target = raw.strip().strip("`*_ ").rstrip(".").strip("`*_ ")
        if target and target not in targets:
            targets.append(target)
    if len(targets) != 1:
        return None
    target = targets[0]
    if target.lower() == "nobody":
        return {"kind": "nobody", "target": "nobody", "plugin": "", "skill": ""}
    m = _SKILL_TARGET_RE.match(target)
    if m:
        return {"kind": "skill", "target": target, "plugin": m.group(1) or "",
                "skill": m.group(2)}
    if _AGENT_TARGET_RE.match(target):
        return {"kind": "agent", "target": target, "plugin": "", "skill": ""}
    return None


def handoff_line(target: str) -> dict:
    """Check a hand-off against the catalog and give back the line to write.

    A session with real tools runs this before it starts a skill or an agent.
    It exists because the rule "write the line first" was, on its first live
    run, skipped exactly when the work really started: a model about to call
    a tool goes straight to the tool. A command is something it does call, so
    the line is produced here, checked against what is really installed, and
    comes back with whether this one waits for a yes.
    """
    target = (target or "").strip()
    own = _own_plugin_name()
    if target.lower() == "nobody":
        # The whole of the rest of the catalog travels with this line, one
        # short row each. A session that reads the later pages in one batch
        # gets its output cut, then searches what is left for its own words,
        # and a skill described in other words is missed: that is how a
        # planted skill went unrouted once in six real runs. "Nobody" is the
        # one answer that must not be given on a partial read, so the list is
        # put in front of the model at the moment it is about to give it.
        others = [r for r in catalog() if r["source"] != own and r["invoke"]]
        listing = "\n".join(f"- {r['invoke']}: {_one_line(r['description'], 110)}"
                            for r in others)
        return {"ok": True, "line": "Handing to: nobody", "needs_yes": False,
                "say": "Before you use this line to say that nothing here does "
                       "what was asked, read every row below. They are every "
                       "skill and agent on this computer that is not Van "
                       "Gogh's own, described in their own words, which may "
                       "not be the person's words. If one of them does the "
                       "work, run this command again with its name and use "
                       "that line instead. If you are only asking the person "
                       f"a question, use the line as it is.\n\n{listing}"}
    wanted = target[len(own) + 1:] if target.startswith(f"{own}:") else target
    row = next((r for r in catalog() if r["invoke"] and r["invoke"] in (target, wanted)), None)
    if row is None:
        return {"ok": False, "line": "", "needs_yes": False,
                "say": f"Nothing here is called {target}. Use a name exactly as "
                       "the situation lists it, or hand to nobody."}
    line = f"Handing to: {row['invoke']}"
    if row.get("needs_confirm"):
        return {"ok": True, "line": line, "needs_yes": True,
                "say": "This one waits for a yes. Write the line above, ask one "
                       "plain question as your last sentence, and stop. Start "
                       "nothing until the person says yes."}
    return {"ok": True, "line": line, "needs_yes": False,
            "say": "Begin your reply to the person with the line above, exactly "
                   "as printed and on a line of its own, then do the work. The "
                   "line stays at the top of your reply whatever the skill's "
                   "own instructions say about what to print."}


def is_asking(reply: str) -> bool:
    """Whether a reply ends by asking the reader something.

    The agent file has Vincent write the hand-off line and then ask, for work
    that waits for a yes. The line alone cannot tell a proposal from a
    go-ahead, so the closing question is what code reads: a reply whose last
    sentence is a question starts nothing. It only ever narrows. A reply that
    ends with a question it did not need costs the reader one more "yes".
    """
    lines = [ln.strip() for ln in (reply or "").splitlines() if ln.strip()
             and not ln.strip().upper().startswith("SOURCES:")]
    return bool(lines) and lines[-1].rstrip("*_`\"') ").endswith("?")


def runs_headless(handoff) -> bool:
    """Whether code may run this hand-off with nobody at a terminal."""
    return bool(handoff and handoff["kind"] == "skill"
                and handoff["plugin"] == _own_plugin_name()
                and handoff["skill"] in HEADLESS_RUNNABLE
                and handoff["skill"] not in NEEDS_CONFIRM)


# ── One headless turn ────────────────────────────────────────────────────────

_TERMINAL_LINE = host.terminal_hint("claude")


def _terminal_line() -> str:
    return host.terminal_hint()


def _sessions_path() -> Path:
    import user_state
    return user_state.state_dir() / "vincent-sessions.json"


def _load_sessions() -> dict:
    try:
        data = json.loads(_sessions_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, UnicodeDecodeError):
        return {}


def _save_session(channel: str, day: str, session: str) -> None:
    """Remember today's conversation for this channel. Fails open."""
    try:
        data = _load_sessions()
        data[channel] = {"day": day, "id": session}
        path = _sessions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass


def session_for(channel: str, day: str) -> tuple:
    """(session id, whether it continues one). One conversation per channel
    per day, so "re-run the first one" means something, and yesterday's
    conversation is not dragged into today."""
    rec = _load_sessions().get(channel)
    if isinstance(rec, dict) and rec.get("day") == day and rec.get("id"):
        return str(rec["id"]), True
    return str(uuid.uuid4()), False


def _turn_prompt(sit: dict, text: str, channel: str) -> str:
    where = channel.split(":", 1)
    page = ""
    if where[0] == "workbench" and len(where) == 2 and where[1]:
        page = (f" The reader is on the {where[1]} page of the Workbench. "
                f"If they ask about it, its text is in van-gogh/{where[1]}.md.")
    return (
        f"A message for you from {channel}, with nobody at a terminal.{page}\n\n"
        "In this turn you can read the vault (Read, Grep, Glob) and nothing "
        "else: no shell, no skills, no agents, no writing. Do not say you "
        "ran, started or remembered anything. When the message asks for work "
        "a skill or an agent does, write your `Handing to:` line as your "
        "rules say; code reads it and runs the skill when it is one that can "
        "run without a person, and tells the reader where to run it when it "
        "is not. Answer in at most six sentences. If you opened a vault file "
        "to answer, name it on a last line exactly as `SOURCES: <path inside "
        "the vault>, <another>`.\n\n"
        "--- THE SITUATION, GATHERED BY CODE JUST NOW ---\n"
        + "\n\n".join(pages(sit))
        + "\n--- END OF THE SITUATION ---\n\n"
        f"The message: {text}")


def _run_handed_off(skill: str) -> dict:
    """Run one skill for a headless turn and say honestly how it went.

    A run that failed is reported as one that did not finish, with the line it
    left, and never as a result. The line is the exception's last line with
    credential shapes replaced, never the whole text.
    """
    import skill_run
    title = host.cmd(skill)
    started = run_ledger.now_stamp()
    rc, detail, out = 0, "", ""
    try:
        out = skill_run.run_skill(skill, capture_stdout=True,
                                  timeout_s=SKILL_RUN_TIMEOUT_S) or ""
    except subprocess.TimeoutExpired:
        rc, detail = 1, (f"it was stopped after "
                         f"{SKILL_RUN_TIMEOUT_S // 60} minutes")
    except Exception as exc:                                     # noqa: BLE001
        lines = [ln.strip() for ln in str(exc).splitlines() if ln.strip()]
        rc = 1
        detail = _first_line(lines[-1] if lines else type(exc).__name__, 300)
    try:
        run_ledger.record_run(f"vincent.{skill}", started,
                              run_ledger.now_stamp(), rc, detail)
    except Exception:                                            # noqa: BLE001
        pass
    if rc:
        return {"ok": False, "text": f"{title} did not finish. What it "
                                     f"reported: {detail}"}
    if not out.strip():
        return {"ok": False, "text": f"{title} ran and printed nothing, so "
                                     "there is no result to show."}
    return {"ok": True, "text": out.strip()}


def _fit(reply: str, result: str) -> str:
    """The reply and a skill's output together, never past REPLY_CAP."""
    reply = reply.strip()
    if not result:
        if len(reply) <= REPLY_CAP:
            return reply
        note = "\n\n(Shortened. Ask for the part you need.)"
        return reply[:REPLY_CAP - len(note)].rstrip() + note
    reply = reply[:REPLY_CAP // 3].rstrip()
    whole = f"{reply}\n\n{result}"
    if len(whole) <= REPLY_CAP:
        return whole
    note = (f"\n\n(Shortened: the full result was {len(result):,} characters. "
            "The page the skill writes in your vault has all of it.)")
    room = REPLY_CAP - len(reply) - 2 - len(note)
    return f"{reply}\n\n{result[:max(room, 0)].rstrip()}{note}"


def answer(text: str, channel: str, now: datetime | None = None,
           day: str | None = None) -> dict:
    """One turn with Vincent from a surface that has no terminal.

    The turn itself can only read: it is `claude_cli.run_claude_readonly` with
    the agent file as its rules. What it may cause is decided afterwards, in
    code, from its `Handing to:` line (`runs_headless`). Returns `{reply,
    handoff, ran, session}`; never raises.
    """
    import claude_cli

    text = (text or "").strip()
    out = {"reply": "", "handoff": None, "ran": "", "session": ""}
    if not text:
        return out
    now = now or datetime.now()
    day = day or now.date().isoformat()
    try:
        root = config_loader.vault()
    except Exception:                                            # noqa: BLE001
        out["reply"] = "No vault is configured, so there is nothing to read."
        return out

    sit = situation(now)
    prompt = _turn_prompt(sit, text, channel)
    model = config_loader.workbench_chat_model() or None
    session, resumed = session_for(channel, day)

    def spawn(sid, resume):
        return claude_cli.run_claude_readonly(
            prompt, root, model=model, timeout=VINCENT_TIMEOUT_S,
            agent=AGENT, plugin_dir=plugin_root(),
            session_id=None if resume else sid, resume=sid if resume else None)

    try:
        proc = spawn(session, resumed)
        if resumed and (proc.returncode != 0 or not (proc.stdout or "").strip()):
            # Yesterday's file can be gone, or the CLI may no longer know the
            # id. A fresh conversation loses the thread and still answers.
            session, resumed = str(uuid.uuid4()), False
            proc = spawn(session, False)
    except subprocess.TimeoutExpired:
        out["reply"] = (f"That took longer than {VINCENT_TIMEOUT_S // 60} "
                        "minutes and was stopped. Ask again, or ask for less "
                        "at once.")
        return out
    except Exception as exc:                                     # noqa: BLE001
        out["reply"] = f"Vincent could not be reached ({type(exc).__name__})."
        return out

    reply = (proc.stdout or "").strip()
    if proc.returncode != 0 or not reply:
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = _first_line(lines[-1], 300) if lines else ""
        out["reply"] = ("Vincent did not answer."
                        + (f" What came back: {tail}" if tail else ""))
        return out

    # Codex names its own thread; Claude keeps the id it was given.
    session = host.thread_id(proc) or session
    _save_session(channel, day, session)
    out["session"] = session
    handoff = parse_handoff(reply)
    out["handoff"] = handoff
    result = ""
    if handoff and handoff["kind"] != "nobody":
        if runs_headless(handoff) and is_asking(reply):
            result = "Nothing has started. Say yes and it will."
        elif runs_headless(handoff):
            ran = _run_handed_off(handoff["skill"])
            out["ran"] = handoff["skill"] if ran["ok"] else ""
            result = ran["text"]
        elif not (handoff["kind"] == "skill"
                  and handoff["skill"] in NEEDS_CONFIRM):
            result = _terminal_line()
        else:
            result = ("Nothing was changed. " + _terminal_line())
    out["reply"] = _fit(reply, result)
    return out


# ── Command line ─────────────────────────────────────────────────────────────

def _json(value) -> None:
    sys.stdout.write(json.dumps(value, indent=2, default=str) + "\n")


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="What Vincent knows: skills, schedule, memory.")
    ap.add_argument("--situation", action="store_true",
                    help="everything, as JSON (or as text with --page)")
    ap.add_argument("--page", type=int, default=0,
                    help="with --situation: print that page of the text form")
    ap.add_argument("--catalog", action="store_true")
    ap.add_argument("--schedule", action="store_true")
    ap.add_argument("--install", action="store_true",
                    help="what is set up on this computer and what is not, as JSON")
    ap.add_argument("--setup-rules", action="store_true",
                    help="print how to run setup, with every path filled in")
    ap.add_argument("--setup-brief", action="store_true",
                    help="the situation and the setup rules, for the setup line")
    ap.add_argument("--remember", default=None, metavar="FACT")
    ap.add_argument("--handoff", default=None, metavar="NAME",
                    help="check a skill or agent name and print the line to write")
    ap.add_argument("--ask", default=None, metavar="MESSAGE")
    ap.add_argument("--channel", default="terminal")
    ap.add_argument("--day", default=None,
                    help="with --ask: which day's conversation (YYYY-MM-DD)")
    args = ap.parse_args(argv)

    if args.remember is not None:
        result = remember(args.remember)
        _json(result)
        return 0 if result.get("ok") else 1
    if args.handoff is not None:
        result = handoff_line(args.handoff)
        if result["line"]:
            sys.stdout.write(result["line"] + "\n\n")
        sys.stdout.write(result["say"] + "\n")
        return 0 if result["ok"] else 1
    if args.ask is not None:
        _json(answer(args.ask, args.channel, day=args.day))
        return 0
    if args.setup_brief:
        sys.stdout.write(setup_brief())
        return 0
    if args.setup_rules:
        rules = setup_rules()
        sys.stdout.write(rules or "The setup rules could not be read.\n")
        return 0 if rules else 1
    if args.install:
        _json(install())
        return 0
    if args.catalog:
        _json(catalog())
        return 0
    if args.schedule:
        _json(schedule())
        return 0
    if args.situation:
        sit = situation()
        if args.page:
            all_pages = pages(sit)
            if not 1 <= args.page <= len(all_pages):
                sys.stderr.write(f"there are {len(all_pages)} pages\n")
                return 1
            sys.stdout.write(all_pages[args.page - 1] + "\n")
        else:
            _json(sit)
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
