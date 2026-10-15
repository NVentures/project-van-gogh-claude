#!/usr/bin/env python3
"""P21: the real morning_coffee.py, run as a subprocess, over five scenarios.

This runs the SHIPPED entry point, not a reimplementation and not a direct
call into travel.build_notices. Every scenario spawns `python app/morning_coffee.py`
and reads the JSON it prints on stdout, so the integration path is what gets
graded: travel_enabled() gating, the calendar handed to travel.collect, the
"travel" key seeded in the output dict, and the error plumbing.

The email scan and the calendar fetch are replaced by a stub week_review on
sys.path ahead of app/, so no network and no OAuth are needed. Everything
downstream of that, including all of travel.py, is the real code.

The control is what makes the matrix mean anything. With app/travel.py moved
aside, morning_coffee must still run and still emit a "travel" key, and every
scenario that expects a notice must fail. A control that prints a hardcoded
line proves nothing, so this one runs the same scenarios the same way.

Usage:
    .venv/bin/python objectives/travel-logistics/live_check.py
    .venv/bin/python objectives/travel-logistics/live_check.py --control
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent.parent
PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")

# 6:05 AM Eastern is 3:05 AM Pacific: the case the whole module exists for.
DEPART = datetime(2026, 11, 12, 6, 5, tzinfo=ET)
SENTINEL_ADDRESS = "1 Sentinel Way, Nowhere"
SENTINEL_KEY = "sentinel-api-key-value"

# A stub week_review, shadowing the real one so no network is touched. It
# returns the calendar shape the real fetchers now emit, location included.
STUB = '''
import json, os, sys

def main():
    pass

def run(*a, **k):
    return {}
'''

RUNNER = '''
import json, os, sys, types

# Shadow the heavy collectors before morning_coffee imports them.
stub = types.ModuleType("week_review")
stub.main = lambda: None
sys.modules["week_review"] = stub

import morning_coffee as mc

# The calendar the briefing would have fetched, in the real emitted shape.
CAL = json.loads(os.environ["VG_TEST_CALENDAR"])

mc.run_week_review = lambda *a, **k: {
    "calendar": CAL, "waiting_on_user": [], "cold_urgent": [],
    "cold_monitor": [], "recent_replies": [], "drafts_done": [],
    "filtered_pending": [], "status_change_alerts": [],
}
mc.fetch_meetings = lambda *a, **k: []
mc.sync_hotcache_from_week = lambda *a, **k: []
mc.find_current_week_file = lambda *a, **k: os.environ["VG_TEST_WEEKFILE"]
mc.parse_week_file = lambda p: ([], [], [], [])
mc.load_previously_completed = lambda: set()
mc.parse_hotcache_alerts = lambda d: []
mc.update_workspace_calendar = lambda *a, **k: None
mc._build_front_page_section = lambda out: None
mc.extensions.attach = lambda *a, **k: None
mc.workbench_data.write_sidecar = lambda *a, **k: None

# Freeze "today" so a scenario is a date, not a wall clock.
import datetime as _dt
_REAL = _dt.datetime
class _Frozen(_REAL):
    @classmethod
    def now(cls, tz=None):
        base = _REAL.fromisoformat(os.environ["VG_TEST_NOW"])
        return base if tz is None else base.astimezone(tz)
_dt.datetime = _Frozen
mc.datetime = _Frozen

sys.argv = ["morning_coffee.py"]
mc.main()
'''


def event(location="SFO -> EWR, Terminal 3", title="UA 523 to Newark"):
    """A calendar event shaped exactly as app/week_review.py emits one."""
    local = DEPART.astimezone(PT)
    return {
        "source": "Google",
        "title": title,
        "day": local.strftime("%a %b") + f" {local.day}",
        "time": "6:05 AM",
        "location": location,
        "_sort": DEPART.isoformat(),
    }


def _build_fixture_vault(vault: Path, state: Path):
    """A minimal but real vault: the pointer, config.json, and the dirs the
    briefing touches. Nothing here is personal and nothing is read from the
    developer's own machine."""
    for rel in ("wiki/weekly", "wiki/sources", "wiki/entities", "wiki/projects",
                "van-gogh/logs"):
        (vault / rel).mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    (state / "vault-pointer").write_text(str(vault), encoding="utf-8")
    (vault / "wiki" / "hotcache.md").write_text(
        "# Hotcache\n\n## Action Items\n\n## Active Threads\n", encoding="utf-8")
    config = {
        "user": {"full_name": "Test Traveller", "first_name": "Test",
                 "name_variants": ["Test Traveller"], "self_entities": ["Test"],
                 "role_description": "", "timezone": "America/Los_Angeles",
                 "secondary_timezone": ""},
        "accounts": [{"provider": "google", "email": "t@example.com",
                      "label": "Gmail", "is_primary": True, "sent_folder_id": ""}],
        "obsidian": {"vault_path": str(vault),
                     "hotcache_relpath": "wiki/hotcache.md",
                     "sources_relpath": "wiki/sources",
                     "weekly_relpath": "wiki/weekly",
                     "entities_relpath": "wiki/entities",
                     "hotcache_action_items_heading": "Action Items",
                     "hotcache_active_threads_heading": "Active Threads"},
        "businesses": [{"tag": "personal", "display_name": "Personal",
                        "project_page": "wiki/projects/Personal.md",
                        "meeting_route": "10-Personal/Meetings",
                        "keywords": [], "priorities": []}],
        "clients": [],
        # No "travel" block on purpose: the feature must run on its defaults,
        # which is the state every existing install is in.
    }
    (vault / "van-gogh").mkdir(parents=True, exist_ok=True)
    (vault / "van-gogh" / "config.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8")


def run_briefing(days_before, calendar, workdir):
    """Spawn the real morning_coffee.py and return its parsed output."""
    now = (DEPART.astimezone(PT).replace(hour=5, minute=0)
           - timedelta(days=days_before))
    runner = workdir / "runner.py"
    runner.write_text(RUNNER, encoding="utf-8")
    weekfile = workdir / "week-2026-11-09.md"
    weekfile.write_text("# Week\n", encoding="utf-8")

    # A real vault, because config_loader resolves the pointer at import time
    # and morning_coffee reads accessors at module load. Built once per run.
    vault = workdir / "vault"
    state = workdir / "state"
    if not (state / "vault-pointer").exists():
        _build_fixture_vault(vault, state)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "app")
    env["VAN_GOGH_STATE_DIR"] = str(state)
    env["VG_TEST_CALENDAR"] = json.dumps(calendar)
    env["VG_TEST_NOW"] = now.isoformat()
    env["VG_TEST_WEEKFILE"] = str(weekfile)
    env["VAN_GOGH_HOME_ADDRESS"] = SENTINEL_ADDRESS
    env["GOOGLE_MAPS_API_KEY"] = SENTINEL_KEY
    (workdir / "state").mkdir(exist_ok=True)

    proc = subprocess.run(
        [sys.executable, str(runner)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(ROOT),
        timeout=120,
    )
    if proc.returncode != 0:
        return None, proc.stderr[-600:]
    try:
        start = proc.stdout.index("{")
        return json.loads(proc.stdout[start:]), ""
    except (ValueError, json.JSONDecodeError) as e:
        return None, f"unparseable output: {e}\n{proc.stdout[-400:]}"


def main():
    control = "--control" in sys.argv
    workdir = Path(tempfile.mkdtemp(prefix="vg-live-check-"))
    stashed = None
    try:
        if control:
            # The real control: remove the feature and run the SAME scenarios.
            stashed = workdir / "travel.py.stashed"
            shutil.move(str(ROOT / "app" / "travel.py"), str(stashed))
            for cached in (ROOT / "app" / "__pycache__").glob("travel*"):
                cached.unlink()

        results = []
        # Two calendars on purpose. The flights link only fires for a trip
        # with no booking, and "UA 523" IS a booking reference, so the
        # unbooked fixture must not carry a flight number.
        cal = [event()]
        cal_unbooked = [event(title="Trip to Newark")]

        # (a) no trip at all: no notices, and morning_coffee still works.
        out, err = run_briefing(0, [], workdir)
        a_ok = out is not None and out.get("travel") == []
        results.append(("a. no trip, no travel notices, briefing still runs",
                        a_ok, err or f"travel={out.get('travel') if out else 'no output'}"))

        # (b) five days out: a flights link.
        out_b, err_b = run_briefing(5, cal_unbooked, workdir)
        kinds_b = [n["kind"] for n in (out_b or {}).get("travel", [])]
        b_ok = kinds_b == ["flights"]
        results.append(("b. 5 days out, flights link only", b_ok,
                        err_b or f"kinds={kinds_b}"))

        # (c) two days out: the forecast. Needs the network, so a failure here
        # is reported honestly rather than stubbed into a pass.
        out_c, err_c = run_briefing(2, cal, workdir)
        kinds_c = [n["kind"] for n in (out_c or {}).get("travel", [])]
        c_ok = kinds_c == ["weather"]
        c_detail = err_c or f"kinds={kinds_c}"
        if not c_ok and kinds_c == []:
            c_detail += " (weather API unreachable from here)"
        results.append(("c. 2 days out, weather only", c_ok, c_detail))

        # (d) departure day: the full notice. No Maps key is valid here, so
        # the notice must degrade honestly and NOT invent a leave-by.
        out_d, err_d = run_briefing(0, cal, workdir)
        notices_d = (out_d or {}).get("travel", [])
        kinds_d = [n["kind"] for n in notices_d]
        d_ok = kinds_d == ["departure"]
        leave = notices_d[0].get("leave_by") if notices_d else None
        # The key is a sentinel, so Routes refuses it: leave_by must be None,
        # never a guess.
        d_ok = d_ok and leave is None and "usual time" in notices_d[0]["text"]
        results.append(("d. departure day, degrades honestly on a bad key",
                        d_ok, err_d or f"kinds={kinds_d} leave_by={leave!r}"))

        # (e) the address and key must not be anywhere in the emitted JSON.
        blob = json.dumps([out_b, out_c, out_d])
        e_ok = SENTINEL_ADDRESS not in blob and SENTINEL_KEY not in blob
        results.append(("e. no home address or API key in the briefing output",
                        e_ok, "clean" if e_ok else "LEAKED"))

        header = ("P21 LIVE END TO END: app/travel.py ABSENT (control)"
                  if control else
                  "P21 LIVE END TO END: real morning_coffee.py subprocess")
        print("=" * 72)
        print(header)
        print("=" * 72)
        for name, ok, detail in results:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
            print(f"         {detail}")
        passed = sum(1 for _, ok, _ in results if ok)
        print("-" * 72)
        print(f"{passed} of {len(results)} scenarios pass")

        if control:
            # b, c and d must all have failed; a and e may legitimately pass,
            # because the briefing is supposed to survive without the feature.
            gradeable = [ok for name, ok, _ in results
                         if name[0] in ("b", "c", "d")]
            if not any(gradeable):
                print("CONTROL SATISFIED: every notice scenario fails "
                      "when the feature is removed.")
                return 0
            print("CONTROL INVALID: a notice scenario passed with no travel.py.")
            return 2
        return 0 if passed == len(results) else 1
    finally:
        if stashed and stashed.exists():
            shutil.move(str(stashed), str(ROOT / "app" / "travel.py"))
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
