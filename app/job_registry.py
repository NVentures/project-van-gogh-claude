#!/usr/bin/env python3
"""One table of everything this product schedules.

The twelve scheduled jobs used to be defined in three separate places, and the
six hand-written ones were each repeated across four functions: install,
uninstall, status and the roster the watcher grades from. Nothing held them
together, so they drifted, and every drift was silent:

* `job_states` never reported the prep poller on macOS, nor the scorecard,
  finance brief or prep poller on Windows. A job missing there is not merely
  unlisted -- `job_watch.evaluate` grades an absent label `not_scheduled` and
  never kicks it, so the watcher was blind to four of the twelve jobs.
* The Note tick was installed by one function and graded by none.
* A feature could be built, scheduled and shipped without any install question
  ever offering it, which is how a finished install emailed nobody.

So this module is the single table, and everything else derives from it. A row
here is installable, removable, reportable on both platforms, gradeable, and
offered to the user, because `tests/test_job_registry_guard.py` walks the table
and asserts each of those for every row. Adding a job is adding a row; the
guard test is what stops a row being half-added.

**A leaf module on purpose.** It imports neither `scheduler_setup` nor
`job_watch` -- both import it -- so the lazy-import cycle those two already
avoid stays impossible, and a test can read the table without touching
launchd. `config_loader` is imported lazily inside the functions that need it
for the same reason.

**`jobs()` is a function, not a module constant.** Several rows read config for
their cadence, and a module-level table would read config at import time, which
breaks `tests/test_script_imports.py` and would freeze a user's schedule at
whatever the config said when the process started.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# launchd StartCalendarInterval weekday numbers (0 = Sunday). Windows spells
# the same days in words; `Cadence.windows_days` does that translation.
LAUNCHD_WEEKDAY = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3,
                   "thursday": 4, "friday": 5, "saturday": 6}

# Monday-zero indexes, which is what `job_watch` grades slots against and what
# `datetime.weekday()` returns. Deliberately a second mapping rather than
# arithmetic on the one above: the two conventions disagree by design, and
# converting between them in-line is how an off-by-one ends up in a schedule.
WATCH_WEEKDAY = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                 "friday": 4, "saturday": 5, "sunday": 6}


@dataclass(frozen=True)
class Cadence:
    """When a job runs.

    Two kinds, because the OS schedulers offer two and they are not
    interchangeable:

    * `clock` -- at an hour and minute, on `days` (empty meaning every day).
      launchd gets a `StartCalendarInterval`, Windows a `-Daily`/`-Weekly`
      trigger.
    * `interval` -- every `interval_min` minutes, forever. launchd gets a
      `StartInterval`; Windows has no interval trigger at all, so it gets a
      daily trigger at `anchor` carrying an indefinite repetition.

    `anchor` exists because the two Windows pollers do not share one: the
    watcher and the prep poller anchor at 12:05am and the Note tick at
    12:10am. Nothing depends on the difference, which is exactly why it is
    carried as data rather than normalized away -- normalizing it would
    rewrite a scheduled task on every Windows machine for no stated reason.
    """

    kind: str = "clock"
    days: tuple = ()
    hour: int = 0
    minute: int = 0
    interval_min: int = 0
    anchor: str = "12:05am"
    run_at_load: bool = False
    keep_alive: bool = False

    @property
    def windows_days(self) -> str:
        """`-DaysOfWeek` as Task Scheduler spells it: `Monday,Friday`."""
        return ",".join(d.capitalize() for d in self.days)

    @property
    def watch_days(self) -> set:
        """The days this is due, Monday-zero, for slot grading.

        An empty `days` means daily, which is every index rather than none:
        a job due every day that reported "due on no day" would be graded
        as never owing anything, and so never noticed when it stopped.
        """
        if not self.days:
            return set(WATCH_WEEKDAY.values())
        return {WATCH_WEEKDAY[d] for d in self.days if d in WATCH_WEEKDAY}

    def describe(self) -> str:
        """The cadence in the words the install and sync output already use."""
        if self.kind == "interval":
            if not self.interval_min:
                # Not a timer at all: it starts at login and stays up.
                return "at login" + (", restarted if it stops"
                                     if self.keep_alive else "")
            return f"every {self.interval_min} min"
        when = f"{self.hour:02d}:{self.minute:02d}"
        if not self.days:
            return f"daily {when}"
        return f"{'/'.join(d[:3] for d in self.days)} {when}"


@dataclass(frozen=True)
class Job:
    """One scheduled thing, in full.

    Everything any caller needs to install it, remove it, report on it, grade
    it or ask the user about it. The fields divide into four groups.

    **Identity.** `key` is the stable name every other surface uses, pinned to
    what `roster()` and `job_states()` already called these jobs so nothing
    downstream shifted when the table landed. `mac_label` and `win_task` are
    the OS names; `state_name` is what `job_states` reports as `kind`.

    **What to run.** `script`, `args`, and `needs_claude` -- which decides
    whether the resolved CLI path is embedded. launchd hands a job a PATH too
    sparse to find the binary at fire time, so any job whose work reaches a
    model must carry it, and any job that does not must not: a `--claude` on a
    script that never declared the argument is a crash at 4:30 AM.

    **When.** `cadence`, or `cadence_from_config` for the rows a user can
    retime. `depends_on` names the jobs that must have run today before this
    one is worth running (see `run_ledger.wait_for_dependencies`).

    **Whether, and how it is graded and described.** `enabled` is the config
    predicate; a row whose predicate is false is removed rather than skipped,
    because a user who turns a feature off and still gets its email has been
    ignored. `graded=False` exempts a row from the watcher's roster, which
    exactly one row may take. `skill_phrase` is the words the skills use for
    this when asking a person about it, which the guard test matches on.
    """

    key: str
    state_name: str
    mac_label: str
    win_task: str
    script: str
    title: str
    kind: str = "clock"
    args: tuple = ()
    needs_claude: bool = False
    log_stem: str = ""
    cadence: Cadence = field(default_factory=Cadence)
    description: str = ""
    ledger_job: str = ""
    success_verb: str = "registered"
    skill_phrase: str = ""
    depends_on: tuple = ()
    enabled: object = None
    off_note: str = ""
    cadence_from_config: object = None
    args_from_config: object = None
    deliverable: object = None
    graded: bool = True

    @property
    def log_name(self) -> str:
        return f"{self.log_stem or self.key}.log"

    @property
    def err_name(self) -> str:
        return f"{self.log_stem or self.key}.err"

    def is_enabled(self) -> bool:
        """Whether the user has this on. A row with no predicate is always on.

        Never raises: a config that cannot be read must not stop the whole
        install, and an unreadable predicate reads as off, which removes the
        job rather than installing one the user may not have asked for.
        """
        if self.enabled is None:
            return True
        try:
            return bool(self.enabled())
        except Exception:                                        # noqa: BLE001
            return False

    def resolved_cadence(self) -> Cadence:
        """This job's cadence, reading config when the user can retime it.

        Raises whatever the config accessor raises. Callers turn that into
        "job removed until the config is fixed" rather than a crashed install,
        which is what the digest sync path already did for its own cadences
        and what every row now inherits.
        """
        if self.cadence_from_config is None:
            return self.cadence
        return self.cadence_from_config()

    def resolved_args(self) -> tuple:
        """Static args, plus any the config supplies (the capture window)."""
        if self.args_from_config is None:
            return tuple(self.args)
        return tuple(self.args) + tuple(self.args_from_config())


# ── Cadence readers for the rows a user can retime ───────────────────────────

def _weekly_from(day_fn: str, time_fn: str) -> Cadence:
    import config_loader
    hour, minute = (int(p) for p in getattr(config_loader, time_fn)().split(":", 1))
    day = getattr(config_loader, day_fn)()
    if day not in LAUNCHD_WEEKDAY:
        raise ValueError(f"invalid day: {day!r}")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"invalid time: {hour}:{minute}")
    return Cadence(kind="clock", days=(day,), hour=hour, minute=minute)


def _cfg_flag(name: str):
    """A config boolean accessor, read at call time rather than import time."""
    def read() -> bool:
        import config_loader
        return bool(getattr(config_loader, name)())
    return read


def _finance_cadence() -> Cadence:
    return _weekly_from("finance_day", "finance_time")


def _capture_cadence() -> Cadence:
    return _weekly_from("contact_capture_day", "contact_capture_time")


def _capture_args() -> tuple:
    import config_loader
    return ("--days", str(config_loader.contact_capture_window_days()))


def _digest_cadence(briefing: str):
    """That briefing's configured days and time, validated.

    The validation is the digest sync path's, kept word for word: its error
    strings are what `/van-gogh:update-digest-preferences` prints, and a user
    reading "invalid digest days" is reading this function.
    """
    def read() -> Cadence:
        import config_loader
        spec = config_loader.digest_briefings().get(briefing, {}) or {}
        days = [str(d).strip().lower() for d in spec.get("days", [])]
        bad = [d for d in days if d not in LAUNCHD_WEEKDAY]
        if not days or bad:
            raise ValueError(f"invalid digest days: {spec.get('days')!r}")
        try:
            hour_s, minute_s = str(spec.get("time", "")).split(":")
            hour, minute = int(hour_s), int(minute_s)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except ValueError:
            raise ValueError(
                f"invalid digest time (want HH:MM): {spec.get('time')!r}")
        return Cadence(kind="clock", days=tuple(days), hour=hour, minute=minute)
    return read


def _digest_enabled(briefing: str):
    """A briefing runs only if digests are on AND that briefing is opted in."""
    def read() -> bool:
        import config_loader
        if not config_loader.digest_enabled():
            return False
        spec = config_loader.digest_briefings().get(briefing, {}) or {}
        return bool(spec.get("enabled", False))
    return read


def _digest_deliverable(briefing: str):
    def read():
        import digest_send
        return digest_send.briefing_md_path(briefing)
    return read


def _briefing_title(briefing: str) -> str:
    """What this briefing is called when a person is being told about it.

    Lowercase on purpose: these land mid-sentence in the watcher's recovery
    line, and a capital there is the tell that a name was pasted into prose.
    """
    try:
        import digest_send
        name = digest_send.BRIEFING_NAMES.get(briefing, "")
    except Exception:                                            # noqa: BLE001
        name = ""
    return f"your {name} briefing" if name else f"your {briefing} briefing"


def _skill_cadence(skill: str):
    """A scheduled capability's days and time, from the `cadence` block."""
    def read() -> Cadence:
        import config_loader
        spec = config_loader.skill_schedule(skill)
        hour, minute = (int(p) for p in spec["time"].split(":", 1))
        days = tuple(spec["days"])
        # Every day of the week is "daily" to both schedulers, and saying so
        # keeps the plist to one trigger instead of seven.
        if set(days) == set(LAUNCHD_WEEKDAY):
            days = ()
        return Cadence(kind="clock", days=days, hour=hour, minute=minute)
    return read


def _skill_enabled(skill: str):
    def read() -> bool:
        import config_loader
        return bool(config_loader.skill_schedule(skill)["enabled"])
    return read


def _config_path(name: str):
    """A deliverable that is one file whose location is a config accessor."""
    def read():
        import config_loader
        return getattr(config_loader, name)()
    return read


def _module_deliverable(module: str, attr: str = "latest_path"):
    def read():
        import importlib
        return getattr(importlib.import_module(module), attr)()
    return read


# ── The table ────────────────────────────────────────────────────────────────

SKILL_TITLES = {
    "meeting-ingest": "the filing of your meeting notes",
    "ingest-workspace": "the sync of your project notes",
}

# The two background skills, at 4:30 and 4:50 AM. They used to share 4:30,
# which meant two headless renders against the same vault at the same instant;
# twenty minutes apart costs nothing and removes the collision.
SKILL_CADENCE = {
    "meeting-ingest": Cadence(kind="clock", hour=4, minute=30),
    "ingest-workspace": Cadence(kind="clock", hour=4, minute=50),
}

# Capabilities a person used to have to remember to run. Each is one skill
# run headlessly by `skill_run.py` on its own schedule, off until asked for.
# `phrase` is what the install question calls it, `title` is how the watcher
# names it mid-sentence, and `deliverable` is the file a good run leaves
# changed, where there is exactly one. The stub check writes nothing on a day
# with nothing to stub, and the 5:15 writes one file per client, so those two
# are graded on their run rows alone.
SCHEDULED_SKILLS = {
    "calendar-stub-check": {
        "title": "the check for meetings nobody recorded",
        "phrase": "checking for meetings nobody recorded",
        "deliverable": None},
    "relationship-radar": {
        "title": "your relationship radar",
        "phrase": "a weekly look at who is going cold",
        "deliverable": "relationship_radar_path"},
    "five-fifteen": {
        "title": "your weekly client reports",
        "phrase": "drafting your weekly client reports",
        "deliverable": None},
    "voice-calibration": {
        "title": "the weekly read of how you write",
        "phrase": "learning from the mail you sent this week",
        "deliverable": "voice_snapshot_path"},
    "voice-generator": {
        "title": "the refresh of your tone profile",
        "phrase": "refreshing your tone profile",
        "deliverable": "tone_profile_path"},
}

# Everything that files the vault. The briefings wait on these on a catch-up
# morning, so a laptop opened at 8:30 renders against a filed vault rather
# than yesterday's.
INGEST = ("meeting-ingest", "ingest-workspace")


# What each briefing is called in the install question, in the user's words
# rather than a config key. The guard test asserts each of these appears in a
# SKILL.md, which is what makes "a feature nobody is ever asked about"
# impossible to reintroduce.
_BRIEFING_PHRASES = {
    "morning-coffee": "Morning Coffee",
    "afternoon-tea": "Afternoon Tea",
    "week": "Week",
    "week-retro": "Week Retro",
}

# What each briefing must have filed before it is worth rendering. launchd
# coalesces every missed slot and fires them at once on wake with no order, so
# on a machine opened at 8:30 on a Monday the ingests, Week and Morning Coffee
# all fire together and Coffee can read a vault nothing has filed yet.
_BRIEFING_DEPENDS = {
    "morning-coffee": INGEST + ("digest-week",),
    "afternoon-tea": INGEST,
    "week": INGEST,
    "week-retro": INGEST,
}


def jobs() -> list:
    """Every schedulable job, in install order. Reads config; never raises.

    Order matters only for output legibility: the ingests first, then the
    watcher, then the briefings, then the opt-in extras, which is the order a
    person would describe their morning in.
    """
    rows: list = []

    for skill in ("meeting-ingest", "ingest-workspace"):
        rows.append(Job(
            key=skill,
            state_name="skill",
            mac_label=f"com.monet.{skill}",
            win_task=f"VanGogh-{skill}",
            script="skill_run.py",
            args=(skill,),
            needs_claude=True,
            title=SKILL_TITLES[skill],
            kind="skill",
            cadence=SKILL_CADENCE[skill],
            description=f"Project Van Gogh: daily /van-gogh:{skill}",
            ledger_job=f"scheduled.{skill}",
            skill_phrase=("filing your meeting notes" if skill == "meeting-ingest"
                          else "syncing your project notes"),
        ))

    rows.append(Job(
        key="job-watch",
        state_name="watch",
        mac_label="com.monet.job-watch",
        win_task="VanGogh-job-watch",
        script="job_watch.py",
        title="the check that restarts a job that failed",
        kind="watch",
        log_stem="job-watch",
        cadence=Cadence(kind="interval", interval_min=30, anchor="12:05am",
                        run_at_load=True),
        description=("Project Van Gogh: re-runs scheduled jobs that failed "
                     "or never fired"),
        # No skill_phrase: the watcher is not a feature anyone opts into. It
        # arrives with the scheduler and is described in 8a rather than
        # offered as a choice, because a scheduler with nothing watching it is
        # not a lesser option, it is a worse one.
        # The one row exempt from grading. It must not grade and kick itself,
        # and its own liveness already reaches the user through the briefing
        # footer's stale-tick branch, which is why there is no second process
        # watching this one.
        graded=False,
    ))

    for briefing in ("morning-coffee", "afternoon-tea", "week", "week-retro"):
        rows.append(Job(
            key=f"digest-{briefing}",
            state_name="digest",
            mac_label=f"com.monet.digest-{briefing}",
            win_task=f"VanGogh-digest-{briefing}",
            script="digest_send.py",
            args=(briefing,),
            needs_claude=True,
            title=_briefing_title(briefing),
            kind="digest",
            log_stem=f"digest-{briefing}",
            cadence_from_config=_digest_cadence(briefing),
            description=f"Project Van Gogh: emailed /van-gogh:{briefing} digest",
            ledger_job=f"digest.{briefing}",
            success_verb="scheduled",
            skill_phrase=_BRIEFING_PHRASES[briefing],
            enabled=_digest_enabled(briefing),
            off_note=f"the {briefing} briefing is off",
            deliverable=_digest_deliverable(briefing),
            depends_on=_BRIEFING_DEPENDS[briefing],
        ))

    rows.append(Job(
        key="finance-email",
        state_name="finance",
        mac_label="com.monet.finance-email",
        win_task="VanGogh-finance-email",
        script="finance_send.py",
        needs_claude=True,
        title="your weekly finance brief",
        kind="finance",
        log_stem="finance-email",
        cadence_from_config=_finance_cadence,
        description="Project Van Gogh: emails a weekly finance brief",
        ledger_job="finance.email",
        skill_phrase="the weekly finance brief",
        enabled=_cfg_flag("finance_enabled"),
        off_note="the weekly finance brief is off",
        deliverable=_module_deliverable("finance_send"),
        # Nothing: QuickBooks is not in the vault, so a catch-up morning does
        # not make this brief any better for waiting.
        depends_on=(),
    ))

    rows.append(Job(
        key="contact-capture",
        state_name="capture",
        mac_label="com.monet.contact-capture",
        win_task="VanGogh-contact-capture",
        script="contact_capture.py",
        args=("--apply",),
        args_from_config=_capture_args,
        title="saving new contacts to your address book",
        kind="capture",
        log_stem="contact-capture",
        cadence_from_config=_capture_cadence,
        description=("Project Van Gogh: saves people you correspond with "
                     "into Contacts"),
        ledger_job="contact.capture",
        skill_phrase="saving new contacts to your address book",
        enabled=_cfg_flag("contact_capture_enabled"),
        off_note="contact capture is off",
        # No vault deliverable: what it produces lands in the user's Google
        # address book, which this machine cannot read back cheaply. The run
        # row is the evidence, which is why the script records one on every
        # exit path including the ones that wrote nothing.
        deliverable=None,
    ))

    rows.append(Job(
        key="prep-email",
        state_name="poller",
        mac_label="com.monet.prep-email",
        win_task="VanGogh-prep-email",
        script="prep_send.py",
        needs_claude=True,
        title="the prep email before your calls",
        kind="poller",
        log_stem="prep-email",
        # Fifteen, not thirty: a prep promises to arrive about an hour before
        # a call, and a 30 minute tick turns that into a 30 minute spread.
        cadence=Cadence(kind="interval", interval_min=15, anchor="12:05am",
                        run_at_load=True),
        description="Project Van Gogh: emails a meeting prep before each call",
        ledger_job="prep.tick",
        skill_phrase="a meeting prep emailed to you before each call",
        enabled=_cfg_flag("prep_email_enabled"),
        off_note="meeting prep email is off",
    ))

    rows.append(Job(
        key="note",
        state_name="note",
        mac_label="com.monet.note",
        win_task="VanGogh-note",
        script="note_send.py",
        needs_claude=True,
        title="the watcher between briefings",
        kind="poller",
        log_stem="note",
        cadence=Cadence(kind="interval", interval_min=15, anchor="12:10am",
                        run_at_load=True),
        description="Project Van Gogh: the watcher between briefings",
        ledger_job="note.tick",
        skill_phrase="the watcher between briefings",
        enabled=_cfg_flag("notes_enabled"),
        off_note="notes are off",
    ))

    for skill, spec in SCHEDULED_SKILLS.items():
        rows.append(Job(
            key=skill,
            state_name="skill",
            mac_label=f"com.monet.{skill}",
            win_task=f"VanGogh-{skill}",
            script="skill_run.py",
            args=(skill,),
            needs_claude=True,
            title=spec["title"],
            kind="skill",
            cadence_from_config=_skill_cadence(skill),
            description=f"Project Van Gogh: scheduled /van-gogh:{skill}",
            ledger_job=f"scheduled.{skill}",
            skill_phrase=spec["phrase"],
            enabled=_skill_enabled(skill),
            off_note=f"{spec['title']} is not scheduled",
            deliverable=(_config_path(spec["deliverable"])
                         if spec["deliverable"] else None),
            # No `depends_on`: only a briefing's sender waits on the filing
            # jobs, and declaring a dependency nothing honours would be a
            # claim the code does not keep.
        ))

    rows.append(Job(
        key="workbench",
        state_name="workbench",
        mac_label="com.monet.workbench",
        win_task="VanGogh-workbench",
        script="workbench_serve.py",
        args=("--no-browser",),
        title="the Workbench",
        kind="server",
        log_stem="workbench",
        # The first long-running job in the product; every other one is
        # fire-and-exit. RunAtLoad starts it at login and KeepAlive restarts it
        # if it dies, which is what "always there" means for a server. It is
        # graded by liveness rather than by a slot or a deliverable, so it
        # carries no ledger row and stays out of the watcher's roster.
        cadence=Cadence(kind="interval", interval_min=0, run_at_load=True,
                        keep_alive=True),
        description="Project Van Gogh: the Workbench, running at login",
        skill_phrase="the Workbench",
        enabled=_cfg_flag("workbench_autostart"),
        off_note="the Workbench does not start on its own",
        graded=False,
    ))

    return rows


def by_key(key: str):
    """One row by key, or None."""
    for job in jobs():
        if job.key == key:
            return job
    return None


