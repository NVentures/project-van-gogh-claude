#!/usr/bin/env python3
"""schedule_plan.py: everything that can run by itself, as one table a person
reads, and the one place a change to that table is checked and written.

Setup used to ask one yes or no about four briefings and never showed the
rest. A person could finish an install without learning that a prep email, a
relationship radar or a tone refresh existed, and changing a time afterwards
meant a model editing config.json by hand. This module is both halves:

  table(cfg)             one row per `job_registry` row, read from a config
                         dict, so setup can show what a yes WOULD schedule
                         before anything is written
  setup_defaults(cfg)    the config a fresh install proposes
  apply_changes(cfg, c)  a list of changes in a loose shape (a model wrote
                         them from a person's words), each one checked here:
                         the job has to exist, the time has to be a time, the
                         day has to be a day. What cannot be placed is
                         refused by name and nothing else is touched
  write(cfg)             back up config.json, write it, resync the scheduler

Everything above `write` is a pure function of the dict it is handed. The
model never decides whether a change is valid; it only says what the person
asked for.

CLI (one JSON object on stdout):

  show                   the table as it stands on this computer
  change [--dry-run]     takes {"changes": [...]} from --changes or stdin;
                         --dry-run shows what would change and writes nothing
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import job_registry  # noqa: E402
from platform_compat import NO_WINDOW, fmt_hour_minute  # noqa: E402

APP_DIR = Path(__file__).resolve().parent

WEEK = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
WEEKDAYS = WEEK[:5]
BRIEFINGS = ("morning-coffee", "afternoon-tea", "week", "week-retro")
PREP_MODES = ("each", "daily")

# What a fresh install proposes as on. Chosen by the product owner on
# 2026-10-12: the four briefings, the two filing jobs and the watcher (which
# arrive with any schedule at all), one morning email of the day's meeting
# preps, the relationship radar, both voice jobs, and the Workbench at login.
SETUP_ON = ("digest-morning-coffee", "digest-afternoon-tea", "digest-week",
            "digest-week-retro", "prep-email", "relationship-radar",
            "voice-calibration", "voice-generator", "workbench")
SETUP_PREP_MODE = "daily"

# The name a person reads and one line on what the job is for. Every
# `job_registry` row has an entry; a test fails when one is added without it.
NAMES = {
    "digest-morning-coffee": ("Morning Coffee", "your morning briefing, by email"),
    "digest-afternoon-tea": ("Afternoon Tea", "your end of day briefing, by email"),
    "digest-week": ("Week", "the plan for your week, by email"),
    "digest-week-retro": ("Week Retro", "how the week went, by email"),
    "meeting-ingest": ("Meeting notes filing", "files your recorded meetings in the vault"),
    "ingest-workspace": ("Project notes sync", "copies your project notes into the vault"),
    "done-judge": ("To-do check-off", "closes the to-dos your mail and meetings show are done"),
    "job-watch": ("Job check", "restarts any of these that failed"),
    "prep-email": ("Meeting prep", "who you are meeting, by email; or one before each call"),
    "relationship-radar": ("Relationship radar", "who is going cold"),
    "voice-calibration": ("Writing review", "learns from the mail you sent this week"),
    "voice-generator": ("Tone profile refresh", "rebuilds the tone your drafts are written in"),
    "workbench": ("Workbench", "your briefings as a page on this computer"),
    "finance-email": ("Finance brief", "the week's numbers, by email"),
    "note": ("Watcher between briefings", "mails you when something urgent lands"),
    "contact-capture": ("Contact saving", "adds new people to your address book"),
    "calendar-stub-check": ("Unrecorded meeting check", "finds meetings nobody took notes on"),
    "five-fifteen": ("Client reports", "drafts your weekly client reports, never sends"),
}

# Jobs with no switch and no clock of their own: they come with the schedule.
FIXED = ("meeting-ingest", "ingest-workspace", "job-watch")
# Everything that comes with a schedule. The check-off rides along like the
# fixed three (nothing chosen, nothing runs), and unlike them it keeps a
# switch of its own: `auto_close.enabled`, on unless someone turned it off.
WITH_SCHEDULE = FIXED + ("done-judge",)
# One day a week, stored as `day`, not `days`.
ONE_DAY = {"finance-email": "finance", "contact-capture": "contact_capture"}
CADENCE_SKILLS = ("calendar-stub-check", "relationship-radar", "five-fifteen",
                  "voice-calibration", "voice-generator")

# Other words a person uses for a job. Checked before the names above.
ALIASES = {
    "coffee": "digest-morning-coffee", "morning briefing": "digest-morning-coffee",
    "tea": "digest-afternoon-tea", "afternoon briefing": "digest-afternoon-tea",
    "weekly briefing": "digest-week", "week briefing": "digest-week",
    "retro": "digest-week-retro",
    "prep": "prep-email", "meeting prep": "prep-email", "prep email": "prep-email",
    "radar": "relationship-radar",
    "finance": "finance-email", "finance brief": "finance-email",
    "notes": "note", "watcher": "note",
    "contacts": "contact-capture", "contact capture": "contact-capture",
    "stub check": "calendar-stub-check",
    "client reports": "five-fifteen", "5:15": "five-fifteen", "515": "five-fifteen",
    "voice calibration": "voice-calibration", "tone profile": "voice-generator",
    "voice generator": "voice-generator",
    "meeting ingest": "meeting-ingest", "meeting filing": "meeting-ingest",
    "project sync": "ingest-workspace",
    "check-off": "done-judge", "check off": "done-judge", "checkoff": "done-judge",
    "done judge": "done-judge", "auto close": "done-judge",
}


class PlanError(ValueError):
    """A plan that cannot be read or written; the message is for the person."""


# ── reading a config dict ────────────────────────────────────────────────────

def _template() -> dict:
    return json.loads((APP_DIR.parent / "config.template.json").read_text(encoding="utf-8"))


def _get(cfg: dict, *path, default=None):
    cur = cfg
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _section(cfg: dict, *path) -> dict:
    cur = cfg
    for key in path:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    return cur


def _clock(hhmm: str) -> str:
    hour, minute = (int(p) for p in str(hhmm).split(":", 1))
    return fmt_hour_minute(datetime(2000, 1, 1, hour, minute))


def _days_words(days) -> str:
    days = [d for d in WEEK if d in {str(x).lower() for x in (days or [])}]
    if not days or len(days) == 7:
        return "every day"
    if days == list(WEEKDAYS):
        return "weekdays"
    if days == ["saturday", "sunday"]:
        return "weekends"
    return " and ".join(f"{d.capitalize()}s" for d in days)


def _spec(cfg: dict, key: str) -> dict:
    """What config says about one job: on, days, time, and what may change."""
    tpl = _TEMPLATE_CACHE.setdefault("t", _template())
    if key in FIXED:
        return {"on": True, "can": ()}
    if key.startswith("digest-"):
        name = key[len("digest-"):]
        mine = _get(cfg, "digest", "briefings", name, default={}) or {}
        base = _get(tpl, "digest", "briefings", name, default={}) or {}
        return {"on": bool(_get(cfg, "digest", "enabled", default=False))
                and bool(mine.get("enabled", False)),
                "days": list(mine.get("days") or base.get("days") or []),
                "time": str(mine.get("time") or base.get("time") or "00:00"),
                "can": ("on", "time", "days")}
    if key in ONE_DAY:
        block = ONE_DAY[key]
        mine = _get(cfg, block, default={}) or {}
        base = _get(tpl, block, default={}) or {}
        return {"on": bool(mine.get("enabled", False)),
                "days": [str(mine.get("day") or base.get("day") or "monday").lower()],
                "time": str(mine.get("time") or base.get("time") or "00:00"),
                "can": ("on", "time", "day")}
    if key in CADENCE_SKILLS:
        mine = _get(cfg, "cadence", key, default={}) or {}
        base = _get(tpl, "cadence", key, default={}) or {}
        return {"on": mine.get("enabled") is True,
                "days": list(mine.get("days") or base.get("days") or []),
                "time": str(mine.get("time") or base.get("time") or "00:00"),
                "can": ("on", "time", "days")}
    if key == "prep-email":
        mine = _get(cfg, "meeting_prep", default={}) or {}
        base = _get(tpl, "meeting_prep", default={}) or {}
        mode = str(mine.get("mode") or base.get("mode") or "each").lower()
        return {"on": bool(mine.get("enabled", False)),
                "mode": mode if mode in PREP_MODES else "each",
                "lead_minutes": int(mine.get("lead_minutes") or base.get("lead_minutes") or 60),
                "time": str(mine.get("daily_time") or base.get("daily_time") or "06:15"),
                "can": ("on", "mode", "time", "lead_minutes")}
    if key == "note":
        mine = _get(cfg, "notes", default={}) or {}
        base = _get(tpl, "notes", default={}) or {}
        return {"on": bool(mine.get("enabled", False)),
                "from": str(mine.get("from") or base.get("from") or "07:30"),
                "until": str(mine.get("until") or base.get("until") or "18:00"),
                "can": ("on",)}
    if key == "workbench":
        return {"on": bool(_get(cfg, "workbench", "autostart", default=False)),
                "can": ("on",)}
    if key == "done-judge":
        # On unless a literal false says otherwise, as config_loader reads it.
        return {"on": _get(cfg, "auto_close", "enabled", default=True) is not False,
                "can": ("on",)}
    raise PlanError(f"no plan entry for the job {key!r}")


_TEMPLATE_CACHE: dict = {}


def _when(key: str, spec: dict, job) -> str:
    if key == "job-watch" or (key in FIXED and job.cadence.kind == "interval"):
        n = job.cadence.interval_min
        return "every hour" if n == 60 else f"every {n} minutes"
    if key in FIXED or key == "done-judge":
        return f"every day at {_clock(f'{job.cadence.hour:02d}:{job.cadence.minute:02d}')}"
    if key == "prep-email":
        if spec["mode"] == "daily":
            return f"one email each morning at {_clock(spec['time'])}"
        return f"{spec['lead_minutes']} minutes before each call"
    if key == "note":
        return (f"every {job.cadence.interval_min} minutes, "
                f"{_clock(spec['from'])} to {_clock(spec['until'])}")
    if key == "workbench":
        return "when you log in"
    return f"{_days_words(spec['days'])} at {_clock(spec['time'])}"


def table(cfg: dict) -> list:
    """One row per scheduled job, in the order a morning runs.

    `on` for a fixed row means "comes with the schedule": it is on whenever
    anything else is, and `any_on` below says whether anything is.
    """
    rows = []
    for job in job_registry.jobs():
        if job.key not in NAMES:
            raise PlanError(f"no plan entry for the job {job.key!r}")
        spec = _spec(cfg, job.key)
        name, what = NAMES[job.key]
        rows.append({"key": job.key, "name": name, "what": what,
                     "when": _when(job.key, spec, job), "on": spec["on"],
                     "fixed": job.key in WITH_SCHEDULE, "can": list(spec["can"]),
                     **({"mode": spec["mode"]} if "mode" in spec else {})})
    return rows


def any_on(rows: list) -> bool:
    return any(r["on"] for r in rows if not r["fixed"])


def render(rows: list, changed: tuple = ()) -> str:
    """The table as plain lines. Fixed rows sit under ON only when something
    else is on: with nothing chosen, nothing runs."""
    live = any_on(rows)
    on = [r for r in rows if r["on"] and (live or not r["fixed"])]
    off = [r for r in rows if r not in on]
    width = max(len(r["name"]) for r in rows) + 2
    wide = max(len(r["when"]) for r in rows) + 2

    def line(r):
        mark = "  (changed)" if r["key"] in changed else ""
        return f"  {r['name']:<{width}}{r['when']:<{wide}}{r['what']}{mark}"

    out = ["What runs by itself on this computer, by its own clock."]
    out += ["", f"ON ({len(on)})"] + [line(r) for r in on]
    out += ["", f"OFF ({len(off)})"] + [line(r) for r in off]
    return "\n".join(out)


def installed(rows: list) -> str:
    """What was just scheduled, said at the moment it was: the ON rows of
    the table the client kept, so nobody has to ask what got installed."""
    on = [r for r in rows if r["on"]] if any_on(rows) else []
    width = max((len(r["name"]) for r in on), default=0) + 2
    wide = max((len(r["when"]) for r in on), default=0) + 2
    head = (f"Scheduled on this computer now: {len(on)} "
            f"job{'' if len(on) == 1 else 's'}, each by this computer's clock.")
    return "\n".join([head, ""] + [f"  {r['name']:<{width}}{r['when']:<{wide}}{r['what']}"
                                    for r in on])


# ── the config a fresh install proposes ──────────────────────────────────────

def _set_on(cfg: dict, key: str, on: bool) -> None:
    if key.startswith("digest-"):
        _section(cfg, "digest", "briefings", key[len("digest-"):])["enabled"] = on
    elif key in ONE_DAY:
        _section(cfg, ONE_DAY[key])["enabled"] = on
    elif key in CADENCE_SKILLS:
        _section(cfg, "cadence", key)["enabled"] = on
    elif key == "prep-email":
        _section(cfg, "meeting_prep")["enabled"] = on
    elif key == "note":
        _section(cfg, "notes")["enabled"] = on
    elif key == "workbench":
        _section(cfg, "workbench")["autostart"] = on
    elif key == "done-judge":
        _section(cfg, "auto_close")["enabled"] = on


def setup_defaults(cfg: dict) -> dict:
    """A copy of `cfg` with the fresh-install proposal switched on.

    Times are left as the config has them (the template's on a new vault).
    """
    out = copy.deepcopy(cfg)
    for job in job_registry.jobs():
        if job.key in WITH_SCHEDULE:
            continue
        _set_on(out, job.key, job.key in SETUP_ON)
    _section(out, "digest")["enabled"] = True
    _section(out, "meeting_prep")["mode"] = SETUP_PREP_MODE
    return out


def nothing(cfg: dict) -> dict:
    """A copy of `cfg` with every job a person can switch turned off."""
    out = copy.deepcopy(cfg)
    for job in job_registry.jobs():
        if job.key not in WITH_SCHEDULE:
            _set_on(out, job.key, False)
    _section(out, "digest")["enabled"] = False
    return out


# ── reading a change written from a person's words ───────────────────────────

_TIME_RE = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?\s*([ap])?\.?m?\.?$", re.I)
_DAY_WORDS = {"weekdays": WEEKDAYS, "weekday": WEEKDAYS, "every weekday": WEEKDAYS,
              "daily": WEEK, "every day": WEEK, "everyday": WEEK, "all": WEEK,
              "weekends": ("saturday", "sunday"), "weekend": ("saturday", "sunday")}


def parse_time(value) -> str:
    """`HH:MM` on a 24 hour clock, or raise. Reads 6:30, 06:30, 6:30 pm, 6pm,
    noon. A bare hour with no am or pm is read as written on a 24 hour clock."""
    text = str(value if value is not None else "").strip().lower()
    if text in ("noon", "midday"):
        return "12:00"
    if text == "midnight":
        return "00:00"
    m = _TIME_RE.match(text.replace(" ", ""))
    if not m:
        raise PlanError(f'"{value}" is not a time I can read')
    hour, minute, half = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if half:
        if not 1 <= hour <= 12:
            raise PlanError(f'"{value}" is not a time I can read')
        hour = hour % 12 + (12 if half == "p" else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise PlanError(f'"{value}" is not a time I can read')
    return f"{hour:02d}:{minute:02d}"


def parse_days(value) -> list:
    """Weekday names in week order, or raise."""
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _DAY_WORDS:
            return list(_DAY_WORDS[text])
        value = [p for p in re.split(r"[,/&]|\band\b|\s+", text) if p]
    if not isinstance(value, (list, tuple)) or not value:
        raise PlanError(f'"{value}" is not a day I can read')
    found = set()
    for item in value:
        word = str(item).strip().lower().rstrip("s") if str(item).strip().lower() not in WEEK \
            else str(item).strip().lower()
        if word in _DAY_WORDS:
            found |= set(_DAY_WORDS[word])
            continue
        match = [d for d in WEEK if d == word or (len(word) >= 3 and d.startswith(word))]
        if len(match) != 1:
            raise PlanError(f'"{item}" is not a day I can read')
        found.add(match[0])
    return [d for d in WEEK if d in found]


def resolve_job(text) -> str:
    """The job key a person's word for it means, or raise."""
    word = re.sub(r"\s+", " ", str(text or "").strip().lower()).strip(" .")
    word = re.sub(r"^(?:the|my|your) ", "", word)
    if not word:
        raise PlanError("a change did not say which job it is for")
    keys = [j.key for j in job_registry.jobs()]
    if word in keys:
        return word
    if f"digest-{word}" in keys:
        return f"digest-{word}"
    dashed = word.replace(" ", "-")
    if dashed in keys:
        return dashed
    if f"digest-{dashed}" in keys:
        return f"digest-{dashed}"
    if word in ALIASES:
        return ALIASES[word]
    names = {NAMES[k][0].lower(): k for k in keys}
    if word in names:
        return names[word]
    # What they said may wrap the name in other words ("the morning coffee
    # briefing"). The reverse is not allowed: a single common word such as
    # "briefing" sits inside one job's name and means none of them.
    known = {**names, **ALIASES}
    inside = [n for n in known if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", word)]
    if inside:
        longest = max(len(n) for n in inside)
        found = {known[n] for n in inside if len(n) == longest}
        if len(found) == 1:
            return found.pop()
    raise PlanError(f'I could not tell which job "{text}" is')


def _bool(value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("on", "true", "yes", "enable", "enabled"):
        return True
    if text in ("off", "false", "no", "disable", "disabled"):
        return False
    raise PlanError(f'"{value}" is neither on nor off')


def _mode(value) -> str:
    text = str(value or "").strip().lower()
    if text in ("daily", "morning", "one", "one email", "digest"):
        return "daily"
    if text in ("each", "before", "per call", "before each call", "every call"):
        return "each"
    raise PlanError(f'"{value}" is not one of the two ways meeting prep arrives')


def apply_changes(cfg: dict, changes) -> tuple:
    """(a new config, what was done, what was refused, the keys that changed).

    A change is `{"job": ..., "on": ..., "time": ..., "days": ..., "mode": ...,
    "lead_minutes": ...}` with any subset of the fields after `job`. A change
    that cannot be placed is refused whole and named; the others still apply.
    """
    out = copy.deepcopy(cfg)
    done, refused, touched = [], [], []
    if not isinstance(changes, (list, tuple)):
        return out, done, ["That was not a list of changes."], touched
    for change in changes:
        if not isinstance(change, dict):
            refused.append("One change was not in a form I can read.")
            continue
        try:
            key = resolve_job(change.get("job"))
            trial = copy.deepcopy(out)
            said = _apply_one(trial, key, change)
        except PlanError as exc:
            refused.append(str(exc)[:1].upper() + str(exc)[1:] + ".")
            continue
        if said:
            out = trial
            done.extend(said)
            touched.append(key)
    return out, done, refused, touched


def _apply_one(cfg: dict, key: str, change: dict) -> list:
    name = NAMES[key][0]
    fields = {k: v for k, v in change.items() if k != "job" and v is not None}
    if "day" in fields and "days" not in fields:
        fields["days"] = fields.pop("day")
    if not fields:
        raise PlanError(f"the change for {name} did not say what to change")
    if key in FIXED:
        raise PlanError(f"{name} comes with the schedule and has no setting of its own")
    spec = _spec(cfg, key)
    unknown = [f for f in fields if f not in ("on", "time", "days", "mode", "lead_minutes")]
    if unknown:
        raise PlanError(f'{name} has no setting called "{unknown[0]}"')
    said = []
    if key == "prep-email":
        block = _section(cfg, "meeting_prep")
        if "days" in fields:
            raise PlanError(f"{name} follows your calendar and has no days to set")
        if "mode" in fields:
            block["mode"] = _mode(fields["mode"])
        if "lead_minutes" in fields:
            try:
                lead = int(fields["lead_minutes"])
            except (TypeError, ValueError):
                raise PlanError(f'"{fields["lead_minutes"]}" is not a number of minutes')
            if not 5 <= lead <= 24 * 60:
                raise PlanError(f"{lead} minutes ahead is outside what {name} can do")
            block["lead_minutes"] = lead
            block["mode"] = "each"
        if "time" in fields:
            block["daily_time"] = parse_time(fields["time"])
            block["mode"] = "daily"
        if "on" in fields:
            block["enabled"] = _bool(fields["on"])
        elif not spec["on"]:
            block["enabled"] = True
    elif "time" in fields and "time" not in spec["can"]:
        raise PlanError(f"{name} has no time to set, it can only be on or off")
    elif "days" in fields and not {"days", "day"} & set(spec["can"]):
        raise PlanError(f"{name} has no days to set, it can only be on or off")
    elif "mode" in fields or "lead_minutes" in fields:
        raise PlanError(f"{name} arrives one way only")
    else:
        time = parse_time(fields["time"]) if "time" in fields else None
        days = parse_days(fields["days"]) if "days" in fields else None
        on = _bool(fields["on"]) if "on" in fields else None
        if key.startswith("digest-"):
            mine = key[len("digest-"):]
            block = _section(cfg, "digest", "briefings", mine)
            if on is None and not spec["on"]:
                on = True
            if on and not _get(cfg, "digest", "enabled", default=False):
                # The master switch is off, so the other three briefings may
                # still read "enabled" underneath it. Turning one on must not
                # turn on the rest.
                for other in BRIEFINGS:
                    if other != mine:
                        _section(cfg, "digest", "briefings", other)["enabled"] = False
                _section(cfg, "digest")["enabled"] = True
            if time:
                block["time"] = time
            if days:
                block["days"] = days
            if on is not None:
                block["enabled"] = on
        elif key in ONE_DAY:
            block = _section(cfg, ONE_DAY[key])
            if days and len(days) != 1:
                raise PlanError(f"{name} runs one day a week, so it needs exactly one day")
            if time:
                block["time"] = time
            if days:
                block["day"] = days[0]
            block["enabled"] = on if on is not None else True
        elif key in CADENCE_SKILLS:
            block = _section(cfg, "cadence", key)
            if time:
                block["time"] = time
            if days:
                block["days"] = days
            block["enabled"] = on if on is not None else True
        else:
            if on is None:
                raise PlanError(f"{name} can only be on or off")
            _set_on(cfg, key, on)
    after = _spec(cfg, key)
    job = job_registry.by_key(key)
    if after["on"]:
        said.append(f"{name}: on, {_when(key, after, job)}.")
    else:
        said.append(f"{name}: off.")
    return said


# ── writing ──────────────────────────────────────────────────────────────────

def _config_path() -> Path:
    import config_loader
    return config_loader.van_gogh_root() / "config.json"


def read_config() -> dict:
    return json.loads(_config_path().read_text(encoding="utf-8"))


SCHEDULER_SKIPPED = "left alone (the state folder is overridden)"


def scheduler_allowed() -> bool:
    """Whether this run may touch the computer's real scheduler. One rule,
    kept in `setup_state` beside the check that reads the same scheduler."""
    import setup_state
    return setup_state.scheduler_allowed()


def write(cfg: dict, sync: bool = True) -> dict:
    """Back up config.json, write the new one, and resync the scheduler.

    Returns `{"written": True, "scheduler": "ok" | <why not>}`. The config is
    written first and stays written when the scheduler cannot be reached: the
    watcher's next tick and the next install both read it.
    """
    path = _config_path()
    text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    json.loads(text)
    if path.exists():
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    if not sync:
        return {"written": True, "scheduler": "not asked"}
    if not scheduler_allowed():
        return {"written": True, "scheduler": SCHEDULER_SKIPPED}
    try:
        r = subprocess.run([sys.executable, str(APP_DIR / "scheduler_setup.py"), "install"],
                           capture_output=True, text=True, timeout=300,
                           creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"written": True, "scheduler": f"could not be reached ({type(exc).__name__})"}
    if r.returncode != 0 or "ERROR" in (r.stdout or ""):
        return {"written": True, "scheduler": "reported a problem; the jobs may not match yet"}
    return {"written": True, "scheduler": "ok"}


# ── CLI ──────────────────────────────────────────────────────────────────────

def _emit(obj: dict) -> int:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    return 0 if obj.get("ok") else 1


def cmd_show(_args) -> int:
    try:
        rows = table(read_config())
    except (OSError, ValueError) as exc:
        return _emit({"ok": False, "error": f"the settings could not be read ({type(exc).__name__})"})
    return _emit({"ok": True, "rows": rows, "table": render(rows)})


def cmd_change(args) -> int:
    try:
        asked = json.loads(args.changes if args.changes else (sys.stdin.read() or "{}"))
    except ValueError:
        return _emit({"ok": False, "error": "the changes were not readable"})
    try:
        cfg = read_config()
    except (OSError, ValueError) as exc:
        return _emit({"ok": False, "error": f"the settings could not be read ({type(exc).__name__})"})
    asked = asked if isinstance(asked, dict) else {"changes": asked}
    new, done, refused, touched = apply_changes(cfg, asked.get("changes"))
    rows = table(new)
    out = {"ok": bool(done), "done": done, "refused": refused,
           "table": render(rows, tuple(touched)), "written": False}
    if done and not args.dry_run:
        out.update(write(new))
    return _emit(out)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    change = sub.add_parser("change")
    change.add_argument("--dry-run", action="store_true")
    change.add_argument("--changes", default="",
                        help="the changes as one JSON object (there is nothing "
                             "secret in a schedule, so the command line is fine)")
    args = parser.parse_args(argv)
    return {"show": cmd_show, "change": cmd_change}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
